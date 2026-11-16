// `ctt-agent i2c` - run a sequence of I2C steps N times and report per-step
// statistics (latency, NACK retries, failures, value stability).
//
// Step syntax:  [NAME=]KIND[:args]
//   probe                               1-byte read, checks the device ACKs
//   r:REG:LEN[:EXPECT_HEX]              read LEN bytes from register REG
//   w:REG:HEX                           write bytes to register REG
//   p:REG:MASK:VALUE[:TIMEOUT_MS]       poll REG until (byte & MASK) == VALUE
//   d:MS                                delay
//   rv:LENREG:LENSIZE:DATAREG[:CHUNK[:INC]]
//        variable-length read: big-endian length from LENREG (LENSIZE bytes),
//        then read that many bytes from DATAREG in CHUNK-byte pieces
//        (INC=1: register advances by one per chunk, INC=0: same register).
#include <algorithm>
#include <cstdlib>
#include <cstdio>
#include <cstring>
#include <string>
#include <vector>

#include "ctt/clock.hpp"
#include "ctt/crc32.hpp"
#include "ctt/i2c.hpp"
#include "ctt/json.hpp"
#include "ctt/options.hpp"
#include "ctt/output.hpp"

namespace ctt {
namespace {

enum class Kind { Probe, Read, Write, Poll, Delay, VarRead };

struct Step {
    std::string name;
    std::string text;
    Kind kind = Kind::Probe;
    uint8_t reg = 0;
    size_t len = 0;
    std::vector<uint8_t> data;    // write payload
    std::vector<uint8_t> expect;  // read expectation
    bool has_expect = false;
    uint8_t mask = 0, value = 0;
    double timeout_ms = 1000;
    double delay_ms = 0;
    uint8_t len_reg = 0;
    size_t len_size = 2;
    size_t chunk = 128;
    bool inc = true;

    // stats
    uint64_t runs = 0, ok = 0, failed = 0, retries = 0, mismatches = 0, polls = 0, timed = 0;
    uint64_t lat_min = UINT64_MAX, lat_max = 0, lat_sum = 0;
    std::vector<uint8_t> first;
    bool have_first = false;
    bool stable = true;
    std::string last_error;
};

struct Options {
    std::string bus;
    uint8_t addr = 0x10;
    int retries = 10;
    double retry_delay_ms = 1.0;
    uint64_t repeat = 1;
    bool combined = false;
    bool json = false;
    bool stable_check = true;
    size_t max_hex = 64;
    std::vector<Step> steps;
};

uint32_t num(const std::string& s, const char* what) {
    try {
        size_t idx = 0;
        unsigned long v = std::stoul(s, &idx, 0);
        if (idx != s.size()) throw 0;
        return uint32_t(v);
    } catch (...) {
        throw std::runtime_error(std::string("invalid ") + what + ": '" + s + "'");
    }
}

std::vector<std::string> split(const std::string& s, char sep) {
    std::vector<std::string> v;
    size_t pos = 0;
    for (;;) {
        size_t n = s.find(sep, pos);
        v.push_back(s.substr(pos, n == std::string::npos ? n : n - pos));
        if (n == std::string::npos) return v;
        pos = n + 1;
    }
}

Step parse_step(const std::string& text) {
    Step st;
    st.text = text;
    std::string body = text;
    auto eq = text.find('=');
    if (eq != std::string::npos && text.find(':') > eq) {
        st.name = text.substr(0, eq);
        body = text.substr(eq + 1);
    }
    auto a = split(body, ':');
    const std::string& k = a[0];
    auto need = [&](size_t lo, size_t hi) {
        if (a.size() < lo || a.size() > hi) throw std::runtime_error("bad step syntax: " + text);
    };
    if (k == "probe") {
        need(1, 1);
        st.kind = Kind::Probe;
    } else if (k == "r") {
        need(3, 4);
        st.kind = Kind::Read;
        st.reg = uint8_t(num(a[1], "register"));
        st.len = num(a[2], "length");
        if (st.len == 0 || st.len > 4096) throw std::runtime_error("read length 1..4096: " + text);
        if (a.size() == 4) {
            st.expect = parse_hex(a[3]);
            st.has_expect = true;
        }
    } else if (k == "w") {
        need(3, 3);
        st.kind = Kind::Write;
        st.reg = uint8_t(num(a[1], "register"));
        st.data = parse_hex(a[2]);
    } else if (k == "p") {
        need(4, 5);
        st.kind = Kind::Poll;
        st.reg = uint8_t(num(a[1], "register"));
        st.mask = uint8_t(num(a[2], "mask"));
        st.value = uint8_t(num(a[3], "value"));
        if (a.size() == 5) st.timeout_ms = num(a[4], "timeout");
    } else if (k == "d") {
        need(2, 2);
        st.kind = Kind::Delay;
        st.delay_ms = std::stod(a[1]);
    } else if (k == "rv") {
        need(4, 6);
        st.kind = Kind::VarRead;
        st.len_reg = uint8_t(num(a[1], "length register"));
        st.len_size = num(a[2], "length size");
        if (st.len_size < 1 || st.len_size > 4) throw std::runtime_error("length size 1..4: " + text);
        st.reg = uint8_t(num(a[3], "data register"));
        if (a.size() >= 5) st.chunk = num(a[4], "chunk");
        if (a.size() == 6) st.inc = num(a[5], "inc") != 0;
        if (st.chunk == 0 || st.chunk > 4096) throw std::runtime_error("chunk 1..4096: " + text);
    } else {
        throw std::runtime_error("unknown step kind '" + k + "' in: " + text);
    }
    if (st.name.empty()) st.name = body;
    return st;
}

// One transfer with NACK retries; returns number of retries used.
uint64_t xfer(I2cBus& bus, const Options& o, const uint8_t* w, size_t wlen, uint8_t* r,
              size_t rlen) {
    for (int attempt = 0;; ++attempt) {
        try {
            bus.transfer(o.addr, w, wlen, r, rlen, o.combined);
            return uint64_t(attempt);
        } catch (const I2cNack&) {
            if (attempt >= o.retries) throw;
            sleep_ns(uint64_t(o.retry_delay_ms * 1e6));
        }
    }
}

// Executes one step; returns the bytes read (if any). Throws on failure.
std::vector<uint8_t> exec(I2cBus& bus, const Options& o, Step& st) {
    std::vector<uint8_t> out;
    switch (st.kind) {
        case Kind::Probe:
            out.resize(1);
            st.retries += xfer(bus, o, nullptr, 0, out.data(), 1);
            out.clear();
            break;
        case Kind::Read:
            out.resize(st.len);
            st.retries += xfer(bus, o, &st.reg, 1, out.data(), st.len);
            break;
        case Kind::Write: {
            std::vector<uint8_t> w{st.reg};
            w.insert(w.end(), st.data.begin(), st.data.end());
            st.retries += xfer(bus, o, w.data(), w.size(), nullptr, 0);
            break;
        }
        case Kind::Poll: {
            uint64_t deadline = now_ns() + uint64_t(st.timeout_ms * 1e6);
            uint8_t b = 0;
            for (;;) {
                ++st.polls;
                try {
                    bus.transfer(o.addr, &st.reg, 1, &b, 1, o.combined);
                    if ((b & st.mask) == st.value) break;
                } catch (const I2cNack&) {
                    // busy device: keep polling
                }
                if (now_ns() >= deadline)
                    throw std::runtime_error(out::strprintf(
                        "poll timeout after %.0f ms (last value 0x%02x)", st.timeout_ms, b));
                sleep_ns(uint64_t(o.retry_delay_ms * 1e6));
            }
            out.assign(1, b);
            break;
        }
        case Kind::Delay:
            sleep_ns(uint64_t(st.delay_ms * 1e6));
            break;
        case Kind::VarRead: {
            uint8_t lb[4] = {0, 0, 0, 0};
            st.retries += xfer(bus, o, &st.len_reg, 1, lb, st.len_size);
            size_t total = 0;
            for (size_t i = 0; i < st.len_size; ++i) total = (total << 8) | lb[i];
            if (total > 65536) throw std::runtime_error("variable length too large: " + std::to_string(total));
            out.resize(total);
            uint8_t reg = st.reg;
            for (size_t off = 0; off < total; off += st.chunk) {
                size_t n = std::min(st.chunk, total - off);
                st.retries += xfer(bus, o, &reg, 1, out.data() + off, n);
                if (st.inc) ++reg;
            }
            break;
        }
    }
    return out;
}

const char* kind_name(Kind k) {
    switch (k) {
        case Kind::Probe: return "probe";
        case Kind::Read: return "read";
        case Kind::Write: return "write";
        case Kind::Poll: return "poll";
        case Kind::Delay: return "delay";
        case Kind::VarRead: return "read_var";
    }
    return "?";
}

void usage(FILE* f) {
    std::fputs(
        "Usage: ctt-agent i2c --bus N|/dev/i2c-N|fake,... --addr 0xNN --step STEP [--step ...]\n"
        "  --repeat N          run the step sequence N times (default 1)\n"
        "  --retries N         retries per transfer on NACK (default 10)\n"
        "  --retry-delay MS    delay between retries / polls (default 1)\n"
        "  --combined          repeated-START write+read (default: STOP between)\n"
        "  --no-stable-check   do not require reads to return identical data each run\n"
        "  --max-hex N         bytes of read data to print (default 64)\n"
        "  -J, --json          JSON-lines output\n"
        "Steps: probe | r:REG:LEN[:EXPECT] | w:REG:HEX | p:REG:MASK:VALUE[:TIMEOUT_MS]\n"
        "       | d:MS | rv:LENREG:LENSIZE:DATAREG[:CHUNK[:INC]]   (optional NAME= prefix)\n",
        f);
}

Options parse(int argc, char** argv) {
    Options o;
    for (int i = 2; i < argc; ++i) {
        std::string a = argv[i], v;
        auto eq = a.find('=');
        bool inl = a.rfind("--", 0) == 0 && eq != std::string::npos;
        if (inl) {
            v = a.substr(eq + 1);
            a.resize(eq);
        }
        auto val = [&]() -> std::string {
            if (inl) return v;
            if (i + 1 >= argc) throw std::runtime_error(a + " needs a value");
            return argv[++i];
        };
        if (a == "--bus") o.bus = val();
        else if (a == "--addr") o.addr = uint8_t(num(val(), "address"));
        else if (a == "--retries") o.retries = int(num(val(), "retries"));
        else if (a == "--retry-delay") o.retry_delay_ms = std::stod(val());
        else if (a == "--repeat") o.repeat = num(val(), "repeat");
        else if (a == "--combined") o.combined = true;
        else if (a == "--no-stable-check") o.stable_check = false;
        else if (a == "--max-hex") o.max_hex = num(val(), "max-hex");
        else if (a == "--step") o.steps.push_back(parse_step(val()));
        else if (a == "-J" || a == "--json") o.json = true;
        else if (a == "-h" || a == "--help") {
            usage(stdout);
            std::exit(0);
        } else throw std::runtime_error("unknown option: " + a);
    }
    if (o.bus.empty()) throw std::runtime_error("--bus is required");
    if (o.steps.empty()) throw std::runtime_error("at least one --step is required");
    if (o.repeat == 0) throw std::runtime_error("--repeat must be >= 1");
    return o;
}

}  // namespace

int run_i2c_command(int argc, char** argv) {
    Options o;
    try {
        o = parse(argc, argv);
    } catch (const std::exception& e) {
        std::fprintf(stderr, "ctt-agent i2c: %s\n\n", e.what());
        usage(stderr);
        return 64;
    }
    out::set_format(o.json ? out::Format::Json : out::Format::Text);

    std::unique_ptr<I2cBus> bus;
    try {
        bus = open_i2c(o.bus);
    } catch (const std::exception& e) {
        Json j;
        j.add("event", "error").add("message", e.what());
        out::emit(j, std::string("error: ") + e.what());
        return 1;
    }

    uint64_t t0 = now_ns(), iter_ok = 0;
    for (uint64_t it = 0; it < o.repeat; ++it) {
        bool ok = true;
        for (auto& st : o.steps) {
            if (!ok) break;  // a failed step aborts the rest of this iteration
            ++st.runs;
            uint64_t s = now_ns();
            try {
                auto v = exec(*bus, o, st);
                uint64_t lat = now_ns() - s;
                st.lat_min = std::min(st.lat_min, lat);
                st.lat_max = std::max(st.lat_max, lat);
                st.lat_sum += lat;
                ++st.timed;
                bool good = true;
                if (st.has_expect && (v.size() < st.expect.size() ||
                                      !std::equal(st.expect.begin(), st.expect.end(), v.begin()))) {
                    ++st.mismatches;
                    st.last_error = "expected " + to_hex(st.expect.data(), st.expect.size()) +
                                    ", got " + to_hex(v.data(), v.size());
                    good = false;
                }
                if (!st.have_first) {
                    st.first = v;
                    st.have_first = true;
                } else if (o.stable_check && st.kind != Kind::Poll && v != st.first) {
                    st.stable = false;
                    st.last_error = "value differs from first run";
                    good = false;
                }
                if (good) ++st.ok;
                else {
                    ++st.failed;
                    ok = false;
                }
            } catch (const std::exception& e) {
                ++st.failed;
                st.last_error = e.what();
                ok = false;
            }
        }
        if (ok) ++iter_ok;
    }
    double dur = double(now_ns() - t0) / 1e9;

    std::string arr = "[";
    std::string text = out::strprintf("I2C %s addr 0x%02x  iterations %llu/%llu ok  (%.2fs)\n",
                                      bus->describe().c_str(), o.addr,
                                      (unsigned long long)iter_ok, (unsigned long long)o.repeat, dur);
    text += out::strprintf("  %-22s %-8s %6s %6s %6s %7s %9s %9s %9s  %s\n", "step", "kind", "runs",
                           "ok", "fail", "retries", "min_us", "avg_us", "max_us", "value");
    bool all_ok = iter_ok == o.repeat;
    for (size_t i = 0; i < o.steps.size(); ++i) {
        auto& st = o.steps[i];
        double avg = st.timed ? double(st.lat_sum) / double(st.timed) / 1e3 : 0;
        double mn = st.lat_min == UINT64_MAX ? 0 : double(st.lat_min) / 1e3;
        size_t shown = std::min(st.first.size(), o.max_hex);
        std::string hex = to_hex(st.first.data(), shown) + (shown < st.first.size() ? "..." : "");
        Json j;
        j.add("name", st.name)
            .add("kind", kind_name(st.kind))
            .add("step", st.text)
            .add("runs", st.runs)
            .add("ok", st.ok)
            .add("failed", st.failed)
            .add("retries", st.retries)
            .add("mismatches", st.mismatches)
            .add("min_us", mn)
            .add("avg_us", avg)
            .add("max_us", double(st.lat_max) / 1e3)
            .add("stable", st.stable);
        if (st.kind == Kind::Poll) j.add("polls", st.polls);
        if (st.have_first && !st.first.empty()) {
            j.add("value_len", st.first.size())
                .add("value_hex", hex)
                .add("value_crc32", out::strprintf("%08x", crc32(st.first.data(), st.first.size())));
        }
        if (!st.last_error.empty()) j.add("error", st.last_error);
        arr += (i ? "," : "") + j.str();
        text += out::strprintf("  %-22s %-8s %6llu %6llu %6llu %7llu %9.1f %9.1f %9.1f  %s%s\n",
                               st.name.substr(0, 22).c_str(), kind_name(st.kind),
                               (unsigned long long)st.runs, (unsigned long long)st.ok,
                               (unsigned long long)st.failed, (unsigned long long)st.retries, mn, avg,
                               double(st.lat_max) / 1e3,
                               st.first.size() > 0 ? (std::to_string(st.first.size()) + "B ").c_str() : "",
                               hex.c_str());
        if (!st.last_error.empty()) text += "      ! " + st.last_error + "\n";
    }
    arr += "]";
    Json res;
    res.add("event", "i2c_result")
        .add("ok", all_ok)
        .add("bus", bus->describe())
        .add("addr", out::strprintf("0x%02x", o.addr))
        .add("iterations", o.repeat)
        .add("iterations_ok", iter_ok)
        .add("duration_s", dur)
        .add_raw("steps", arr);
    text += all_ok ? "RESULT: OK" : "RESULT: FAILED";
    out::emit(res, text);
    return all_ok ? 0 : 1;
}

}  // namespace ctt
