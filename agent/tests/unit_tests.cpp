// Self-contained unit tests (no external framework, so they also build with a
// bare Yocto SDK).
#include <cstdio>
#include <cstring>
#include <functional>
#include <string>
#include <vector>

#include "ctt/crc32.hpp"
#include "ctt/ctp.hpp"
#include "ctt/framing.hpp"
#include "ctt/metrics.hpp"
#include "ctt/options.hpp"
#include "ctt/pattern.hpp"

#include <sys/socket.h>
#include <unistd.h>

#include <cerrno>
#include <thread>

#include "bt_common.hpp"
#include "ctt/i2c.hpp"
#include "packet_stream.hpp"

using namespace ctt;

static int g_failed = 0;
static int g_checks = 0;
#define CHECK(cond)                                                              \
    do {                                                                         \
        ++g_checks;                                                              \
        if (!(cond)) {                                                           \
            ++g_failed;                                                          \
            std::fprintf(stderr, "  FAIL %s:%d: %s\n", __FILE__, __LINE__, #cond); \
        }                                                                        \
    } while (0)

// In-memory stream that hands out data in tiny, irregular chunks to exercise
// frame reassembly.
class MemStream : public Stream {
public:
    std::vector<uint8_t> in;
    std::vector<uint8_t> written;
    size_t pos = 0;
    size_t chunk = 1;

    ssize_t read_some(uint8_t* buf, size_t len) override {
        if (pos >= in.size()) return 0;
        size_t n = std::min({len, in.size() - pos, chunk});
        chunk = chunk % 7 + 1;
        std::memcpy(buf, in.data() + pos, n);
        pos += n;
        return ssize_t(n);
    }
    ssize_t writev_some(const struct iovec* iov, int cnt) override {
        // Accept at most 5 bytes per call to exercise partial writes.
        size_t budget = 5, total = 0;
        for (int i = 0; i < cnt && budget; ++i) {
            size_t n = std::min(budget, iov[i].iov_len);
            auto* b = static_cast<const uint8_t*>(iov[i].iov_base);
            written.insert(written.end(), b, b + n);
            budget -= n;
            total += n;
        }
        return ssize_t(total);
    }
    void shutdown_write() override {}
    std::string peer() const override { return "mem"; }
};

static void test_crc32() {
    const char* s = "123456789";
    CHECK(crc32(reinterpret_cast<const uint8_t*>(s), 9) == 0xCBF43926u);
    CHECK(crc32(nullptr, 0) == 0);
    // Incremental == one-shot, across the slice-by-8 boundary.
    std::vector<uint8_t> d(1000);
    for (size_t i = 0; i < d.size(); ++i) d[i] = uint8_t(i * 31 + 7);
    uint32_t whole = crc32(d.data(), d.size());
    uint32_t part = crc32_update(crc32_update(0, d.data(), 13), d.data() + 13, d.size() - 13);
    CHECK(whole == part);
}

static void test_header_roundtrip() {
    ctp::Header h;
    h.type = ctp::MsgType::EchoRsp;
    h.flags = ctp::kFlagCrc;
    h.seq = 0xDEADBEEF;
    h.payload_len = 1234;
    h.ts_ns = 0x0123456789ABCDEFull;
    h.crc32 = 0xCAFEBABE;
    uint8_t buf[ctp::kHeaderSize];
    ctp::encode_header(h, buf);
    CHECK(std::memcmp(buf, "CTP1", 4) == 0);
    ctp::Header d;
    CHECK(ctp::decode_header(buf, d) == ctp::DecodeStatus::Ok);
    CHECK(d.type == h.type && d.flags == h.flags && d.seq == h.seq);
    CHECK(d.payload_len == h.payload_len && d.ts_ns == h.ts_ns && d.crc32 == h.crc32);

    buf[0] = 'X';
    CHECK(ctp::decode_header(buf, d) == ctp::DecodeStatus::BadMagic);
    ctp::encode_header(h, buf);
    buf[4] = 9;
    CHECK(ctp::decode_header(buf, d) == ctp::DecodeStatus::BadVersion);
    h.payload_len = ctp::kMaxPayload + 1;
    ctp::encode_header(h, buf);
    CHECK(ctp::decode_header(buf, d) == ctp::DecodeStatus::TooLarge);
}

static void test_start_stats_roundtrip() {
    ctp::StartParams p;
    p.mode = ctp::Mode::Bidir;
    p.block_size = 990;
    p.duration_ms = 60000;
    p.flags = ctp::kStartVerify;
    p.rate_bps = 3000000000ull;
    p.echo_interval_us = 500;
    p.echo_count = 77;
    uint8_t b[ctp::kStartSize];
    ctp::encode_start(p, b);
    ctp::StartParams q;
    CHECK(ctp::decode_start(b, sizeof(b), q));
    CHECK(q.mode == p.mode && q.block_size == p.block_size && q.duration_ms == p.duration_ms);
    CHECK(q.flags == p.flags && q.rate_bps == p.rate_bps);
    CHECK(q.echo_interval_us == p.echo_interval_us && q.echo_count == p.echo_count);
    CHECK(!ctp::decode_start(b, sizeof(b) - 1, q));
    b[0] = 0;
    CHECK(!ctp::decode_start(b, sizeof(b), q));

    ctp::StatsPayload s;
    s.has_rx = true;
    s.rx_bytes = 1ull << 40;
    s.rx_lost = 3;
    s.tx_duration_ns = 42;
    s.echoed = 9;
    uint8_t sb[ctp::kStatsSize];
    ctp::encode_stats(s, sb);
    ctp::StatsPayload t;
    CHECK(ctp::decode_stats(sb, sizeof(sb), t));
    CHECK(t.has_rx && !t.has_tx && t.rx_bytes == s.rx_bytes && t.rx_lost == 3);
    CHECK(t.tx_duration_ns == 42 && t.echoed == 9);
}

static void test_framing() {
    // Write frames through a stream that accepts 5 bytes per writev...
    MemStream w;
    PatternSource pat(300, true);
    for (uint32_t seq = 0; seq < 20; ++seq) {
        ctp::Header h;
        h.type = ctp::MsgType::Data;
        h.flags = ctp::kFlagCrc;
        h.seq = seq;
        h.crc32 = pat.crc(seq);
        send_frame(w, h, pat.payload(seq), seq == 7 ? 0 : 300);
    }
    send_control(w, ctp::MsgType::Stop, 20);

    // ...then read them back through a stream that returns 1..7 byte chunks.
    MemStream r;
    r.in = w.written;
    FrameReader rd(r, 64);  // small buffer forces growth + compaction
    ctp::Header h;
    const uint8_t* p = nullptr;
    for (uint32_t seq = 0; seq < 20; ++seq) {
        CHECK(rd.next(h, p));
        CHECK(h.type == ctp::MsgType::Data && h.seq == seq);
        if (seq == 7) {
            CHECK(h.payload_len == 0);
        } else {
            CHECK(h.payload_len == 300);
            CHECK(crc32(p, h.payload_len) == h.crc32);
            CHECK(std::memcmp(p, pat.payload(seq), 300) == 0);
        }
    }
    CHECK(rd.next(h, p) && h.type == ctp::MsgType::Stop && h.seq == 20);
    CHECK(!rd.next(h, p));  // clean EOF

    // Truncated stream must throw, not return garbage.
    MemStream t;
    t.in.assign(w.written.begin(), w.written.begin() + 40);
    FrameReader rt(t);
    bool threw = false;
    try {
        rt.next(h, p);
    } catch (const std::exception&) {
        threw = true;
    }
    CHECK(threw);
}

static void test_rx_counters() {
    RxCounters rx;
    rx.begin(1000);
    for (uint32_t s : {0u, 1u, 2u, 5u, 6u, 4u, 7u}) rx.on_frame(s, 100, 2000 + s, true);
    CHECK(rx.frames == 7);
    CHECK(rx.lost == 1);  // 3 lost; 4 arrived late
    CHECK(rx.out_of_order == 1);
    CHECK(rx.bytes == 700);
    rx.on_frame(8, 100, 3000, false);
    CHECK(rx.crc_errors == 1);
    CHECK(rx.duration_ns() == 2000);
    CHECK(mbps(125000, 1000000000ull) == 1.0);
}

static void test_parsers() {
    uint64_t u = 0;
    double d = 0;
    CHECK(parse_size("64K", u) && u == 65536);
    CHECK(parse_size("1m", u) && u == 1048576);
    CHECK(parse_size("990", u) && u == 990);
    CHECK(!parse_size("12X", u));
    CHECK(parse_rate("2M", u) && u == 2000000);
    CHECK(parse_rate("1.5Mbps", u) && u == 1500000);
    CHECK(parse_seconds("500ms", d) && d == 0.5);
    CHECK(parse_seconds("2m", d) && d == 120.0);
    CHECK(parse_seconds("10", d) && d == 10.0);

    const char* argv[] = {"ctt-agent", "client", "-e", "1.2.3.4:5201", "-m", "bidir",
                          "--block=4K", "-d", "3", "--no-verify", "-o", "channel=3"};
    AgentOptions o;
    std::string err;
    CHECK(parse_args(12, const_cast<char**>(argv), o, err));
    CHECK(o.role == Role::Client && o.mode == ctp::Mode::Bidir && o.block == 4096);
    CHECK(o.duration_s == 3.0 && !o.verify && o.topts.extra["channel"] == "3");

    const char* bad[] = {"ctt-agent", "client", "-m", "tx"};
    AgentOptions o2;
    CHECK(!parse_args(4, const_cast<char**>(bad), o2, err));  // missing endpoint
}


static void test_bdaddr() {
    bt::Bdaddr a;
    CHECK(a.is_any());
    CHECK(bt::parse_bdaddr("AA:BB:CC:DD:EE:0f", a));
    CHECK(a.b[0] == 0x0F && a.b[5] == 0xAA);  // stored reversed, like bdaddr_t
    CHECK(bt::to_string(a) == "AA:BB:CC:DD:EE:0F");
    CHECK(!a.is_any());
    CHECK(!bt::parse_bdaddr("AA:BB:CC:DD:EE", a));
    CHECK(!bt::parse_bdaddr("AA-BB-CC-DD-EE-FF", a));
    CHECK(!bt::parse_bdaddr("AA:BB:CC:DD:EE:GG", a));

    std::string addr, num;
    bt::split_endpoint("AA:BB:CC:DD:EE:FF/3", addr, num);
    CHECK(addr == "AA:BB:CC:DD:EE:FF" && num == "3");
    bt::split_endpoint("/0x1001", addr, num);
    CHECK(addr.empty() && bt::parse_number(num, "psm") == 0x1001);
    bt::split_endpoint("hci0", addr, num);
    CHECK(addr == "hci0" && num.empty());
    bool threw = false;
    try { bt::parse_number("12x", "n"); } catch (const std::exception&) { threw = true; }
    CHECK(threw);

    CHECK(bt::parse_security("medium") == 2 && bt::parse_security("bogus") == -1);
    CHECK(std::string(bt::security_name(3)) == "high");
}

// PacketStream over a real AF_UNIX SOCK_SEQPACKET pair: every write must be
// split into <= MTU packets and reassembled without loss.
static void test_packet_stream() {
    int sv[2];
    CHECK(socketpair(AF_UNIX, SOCK_SEQPACKET, 0, sv) == 0);
    PacketStream tx(sv[0], "a", 48, 48);
    PacketStream rx(sv[1], "b", 48, 48);

    PatternSource pat(500, true);
    std::thread writer([&] {
        for (uint32_t seq = 0; seq < 50; ++seq) {
            ctp::Header h;
            h.type = ctp::MsgType::Data;
            h.flags = ctp::kFlagCrc;
            h.seq = seq;
            h.crc32 = pat.crc(seq);
            send_frame(tx, h, pat.payload(seq), 500);
        }
        send_control(tx, ctp::MsgType::Stop, 50);
        tx.shutdown_write();
    });
    FrameReader rd(rx, 100);  // smaller than a frame, larger than MTU
    ctp::Header h;
    const uint8_t* p = nullptr;
    int ok_frames = 0;
    while (rd.next(h, p)) {
        if (h.type == ctp::MsgType::Stop) break;
        if (h.payload_len == 500 && crc32(p, 500) == h.crc32) ++ok_frames;
    }
    writer.join();
    CHECK(ok_frames == 50);
    CHECK(tx.packets_tx() == 50 * ((32 + 500 + 47) / 48) + 1);  // ceil(532/48) per frame + STOP
    CHECK(rx.packets_rx() == tx.packets_tx());

    // Tiny reads (bounce-buffer path) still see every byte in order.
    uint8_t msg[100];
    for (int i = 0; i < 100; ++i) msg[i] = uint8_t(i);
    int sv2[2];
    CHECK(socketpair(AF_UNIX, SOCK_SEQPACKET, 0, sv2) == 0);
    PacketStream a(sv2[0], "a", 64, 64), b(sv2[1], "b", 64, 64);
    iovec iov{msg, sizeof(msg)};
    write_all(a, &iov, 1);
    uint8_t got[100];
    size_t n = 0;
    while (n < 100) {
        ssize_t r = b.read_some(got + n, 7);
        CHECK(r > 0);
        if (r <= 0) break;
        n += size_t(r);
    }
    CHECK(std::memcmp(got, msg, 100) == 0);
    CHECK(a.packets_tx() == 2);

    // A packet larger than imtu must be an error, never silently truncated.
    int sv3[2];
    CHECK(socketpair(AF_UNIX, SOCK_SEQPACKET, 0, sv3) == 0);
    PacketStream big(sv3[0], "a", 200, 200), small(sv3[1], "b", 64, 64);
    iovec iov2{msg, 100};
    write_all(big, &iov2, 1);
    bool threw = false;
    try {
        uint8_t buf[256];
        small.read_some(buf, 10);  // < imtu -> bounce buffer of imtu (64) < packet (100)
    } catch (const std::exception&) {
        threw = true;
    }
    CHECK(threw);
}

// Real kernel RFCOMM socket: listen on channel 0 must yield a kernel-assigned
// channel. Skipped when the kernel has no AF_BLUETOOTH support.
static void test_rfcomm_listen() {
    int probe = ::socket(bt::kAfBluetooth, SOCK_STREAM, bt::kProtoRfcomm);
    if (probe < 0) {
        std::printf("  (skipped: no AF_BLUETOOTH/RFCOMM in this kernel: %s)\n", std::strerror(errno));
        return;
    }
    ::close(probe);
    Transport* t = find_transport("rfcomm");
    CHECK(t != nullptr);
    TransportOptions o;
    auto l1 = t->listen("/0", o);
    auto l2 = t->listen("", o);
    Json j1, j2;
    l1->describe(j1);
    l2->describe(j2);
    CHECK(j1.str().find("\"channel\":0") == std::string::npos);
    CHECK(j1.str() != j2.str());  // two listeners -> two different channels
    CHECK(l1->accept(10) == nullptr);  // times out cleanly

    bool threw = false;
    try {
        t->listen("/31", o);
    } catch (const std::exception&) {
        threw = true;
    }
    CHECK(threw);
    threw = false;
    try {
        t->connect("not-an-address/1", o);
    } catch (const std::exception&) {
        threw = true;
    }
    CHECK(threw);
}

static void test_i2c_fake() {
    CHECK(to_hex(parse_hex("0x0A:0b ff").data(), 3) == "0a0bff");
    bool threw = false;
    try { parse_hex("abc"); } catch (const std::exception&) { threw = true; }
    CHECK(threw);

    auto bus = open_i2c("fake,busy_ms=50,r00=05,r01=0102");
    uint8_t reg = 0, b[4] = {0};
    bus->transfer(0x10, &reg, 1, b, 2, false);
    CHECK(b[0] == 0x05 && b[1] == 0x00);  // zero padded
    reg = 1;
    bus->transfer(0x10, &reg, 1, b, 2, false);
    CHECK(b[0] == 0x01 && b[1] == 0x02);
    threw = false;
    try { bus->transfer(0x11, &reg, 1, b, 1, false); } catch (const I2cNack&) { threw = true; }
    CHECK(threw);  // wrong address
    const uint8_t w[] = {0x02, 0xAA};
    bus->transfer(0x10, w, 2, nullptr, 0, false);
    threw = false;
    try { bus->transfer(0x10, &reg, 1, b, 1, false); } catch (const I2cNack&) { threw = true; }
    CHECK(threw);  // busy after write
}

int main() {
    const std::pair<const char*, std::function<void()>> tests[] = {
        {"crc32", test_crc32},
        {"header_roundtrip", test_header_roundtrip},
        {"start_stats_roundtrip", test_start_stats_roundtrip},
        {"framing", test_framing},
        {"rx_counters", test_rx_counters},
        {"parsers", test_parsers},
        {"bdaddr", test_bdaddr},
        {"packet_stream", test_packet_stream},
        {"rfcomm_listen", test_rfcomm_listen},
        {"i2c_fake", test_i2c_fake},
    };
    for (const auto& [name, fn] : tests) {
        int before = g_failed;
        fn();
        std::printf("%-24s %s\n", name, g_failed == before ? "ok" : "FAILED");
    }
    std::printf("%d checks, %d failed\n", g_checks, g_failed);
    return g_failed ? 1 : 0;
}
