// `ctt-agent pipe` - interactive raw data link for the CTT Bluetooth app.
//
// Listens (default) or connects on any transport and relays bytes between the
// link and the controlling process, line by line, so both directions can be
// shown and driven from the PC over SSH:
//
//   stdin  : one hex string per line ("48656c6c6f0a")  -> sent on the link
//   stdout : JSON lines
//            {"event":"listening","bdaddr":"..","channel":N}
//            {"event":"connected","peer":".."}
//            {"event":"rx","t":1.234,"len":5,"hex":"48656c6c6f"}   data from the peer
//            {"event":"tx","len":5}                                 written to the link
//            {"event":"closed","reason":".."}
//
// Usage: ctt-agent pipe [-t rfcomm] [-e /CHANNEL] [-o KEY=VALUE] [--connect EP]
//                       [--accept-timeout SEC]
#include <cctype>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <iostream>
#include <memory>
#include <mutex>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>

#include "ctt/clock.hpp"
#include "ctt/json.hpp"
#include "ctt/session.hpp"
#include "ctt/transport.hpp"

namespace ctt {
namespace {

std::mutex g_out;
bool g_text = false;  // --text: human-readable lines for a person on the UART

void emit(const Json& j, const std::string& text = "") {
    std::lock_guard<std::mutex> lk(g_out);
    if (g_text) {
        if (text.empty()) return;
        std::fputs(text.c_str(), stdout);
    } else {
        std::fputs(j.str().c_str(), stdout);
    }
    std::fputc('\n', stdout);
    std::fflush(stdout);
}

// "hello\n" -> hello\n (printable ASCII as is, the rest escaped)
std::string printable(const uint8_t* p, size_t n) {
    std::string s;
    for (size_t i = 0; i < n; ++i) {
        uint8_t c = p[i];
        if (c == '\n') s += "\\n";
        else if (c == '\r') s += "\\r";
        else if (c == '\t') s += "\\t";
        else if (c >= 0x20 && c < 0x7f) s += char(c);
        else {
            char b[5];
            std::snprintf(b, sizeof(b), "\\x%02x", c);
            s += b;
        }
    }
    return s;
}

std::string spaced_hex(const uint8_t* p, size_t n) {
    std::string s;
    char b[4];
    for (size_t i = 0; i < n; ++i) {
        std::snprintf(b, sizeof(b), i ? " %02x" : "%02x", p[i]);
        s += b;
    }
    return s;
}

std::string to_hex(const uint8_t* p, size_t n) {
    static const char* d = "0123456789abcdef";
    std::string s(n * 2, '0');
    for (size_t i = 0; i < n; ++i) {
        s[2 * i] = d[p[i] >> 4];
        s[2 * i + 1] = d[p[i] & 15];
    }
    return s;
}

bool from_hex(const std::string& in, std::vector<uint8_t>& out) {
    std::string s;
    for (char c : in)
        if (!std::isspace(static_cast<unsigned char>(c))) s += c;
    if (s.size() % 2) return false;
    out.clear();
    for (size_t i = 0; i < s.size(); i += 2) {
        char b[3] = {s[i], s[i + 1], 0};
        char* end = nullptr;
        long v = std::strtol(b, &end, 16);
        if (*end) return false;
        out.push_back(uint8_t(v));
    }
    return true;
}

void usage(FILE* f) {
    std::fputs(
        "usage: ctt-agent pipe [-t TRANSPORT] [-e ENDPOINT] [-o KEY=VALUE]... [--connect EP]\n"
        "                      [--accept-timeout SEC] [--text]\n"
        "Relays a raw link: stdin hex lines -> link, link data -> stdout JSON lines.\n"
        "Default: -t rfcomm, listen on a kernel-assigned channel.\n"
        "--text: for a person on the console. A typed line is sent as text + \"\\n\";\n"
        "        \"/hex 00 ff 0d\" sends raw bytes. Received data is printed as text and hex,\n"
        "        with running totals of bytes received / sent.\n",
        f);
}

void closed(const std::string& reason) {
    Json j;
    j.add("event", "closed").add("reason", reason);
    emit(j, "-- link closed: " + reason);
}

}  // namespace

int run_pipe_command(int argc, char** argv) {
    std::string transport = "rfcomm", endpoint, connect_ep;
    TransportOptions topts;
    topts.timeout_ms = 0;  // interactive: never time out on idle
    double accept_timeout = 0;
    for (int i = 2; i < argc; ++i) {
        std::string a = argv[i];
        auto val = [&](const char* what) -> std::string {
            if (i + 1 >= argc) throw std::runtime_error(std::string(what) + " needs a value");
            return argv[++i];
        };
        if (a == "-t" || a == "--transport") transport = val("-t");
        else if (a == "-e" || a == "--endpoint") endpoint = val("-e");
        else if (a == "--connect") connect_ep = val("--connect");
        else if (a == "--accept-timeout") accept_timeout = std::atof(val("--accept-timeout").c_str());
        else if (a == "--text") g_text = true;
        else if (a == "-o" || a == "--opt") {
            std::string kv = val("-o");
            auto eq = kv.find('=');
            if (eq == std::string::npos) throw std::runtime_error("-o needs KEY=VALUE");
            topts.extra[kv.substr(0, eq)] = kv.substr(eq + 1);
        } else if (a == "-h" || a == "--help") {
            usage(stdout);
            return 0;
        } else {
            usage(stderr);
            return 64;
        }
    }

    try {
        Transport* t = find_transport(transport);
        if (!t) throw std::runtime_error("unknown transport: " + transport);
        std::unique_ptr<Stream> s;
        if (!connect_ep.empty()) {
            s = t->connect(connect_ep, topts);
        } else {
            auto lis = t->listen(endpoint.empty() ? t->default_listen_endpoint() : endpoint, topts);
            Json jl;
            jl.add("event", "listening").add("transport", t->name()).add("endpoint", lis->local());
            lis->describe(jl);
            emit(jl, "-- listening on " + std::string(t->name()) + " " + lis->local() +
                         (g_text ? "  (type a line + Enter to send it, /hex 00 ff to send bytes, Ctrl+C to quit)"
                                 : ""));
            s = lis->accept(accept_timeout > 0 ? int(accept_timeout * 1000) : -1);
            if (!s) {
                closed("accept timeout");
                return 2;
            }
        }
        Json jc;
        jc.add("event", "connected").add("peer", s->peer());
        emit(jc, "-- connected: " + s->peer());

        const uint64_t t0 = now_ns();
        Stream* sp = s.get();
        std::thread reader([sp, t0] {
            uint8_t buf[4096];
            uint64_t total = 0;
            try {
                for (;;) {
                    ssize_t n = sp->read_some(buf, sizeof(buf));
                    if (n <= 0) break;
                    total += uint64_t(n);
                    const double t = double(now_ns() - t0) / 1e9;
                    Json j;
                    j.add("event", "rx")
                        .add("t", t)
                        .add("len", int64_t(n))
                        .add("total", total)
                        .add("hex", to_hex(buf, size_t(n)));
                    char head[96];
                    std::snprintf(head, sizeof(head), "[%7.2fs] board rx %zd B (total rx %llu B): ", t, n,
                                  (unsigned long long)total);
                    emit(j, head + ("\"" + printable(buf, size_t(n)) + "\"   hex " +
                                    spaced_hex(buf, size_t(n))));
                }
                closed("peer closed the link");
            } catch (const std::exception& e) {
                closed(e.what());
            }
            std::fflush(stdout);
            std::_Exit(0);  // the link is gone: stop waiting on stdin too
        });
        reader.detach();

        std::string line;
        std::vector<uint8_t> data;
        uint64_t total_tx = 0;
        while (std::getline(std::cin, line)) {
            if (!line.empty() && line.back() == '\r') line.pop_back();  // CRLF from a serial console
            if (line.empty()) continue;
            bool ok = true;
            if (!g_text) ok = from_hex(line, data);
            else if (line.rfind("/hex ", 0) == 0) ok = from_hex(line.substr(5), data);
            else data.assign(line.begin(), line.end()), data.push_back('\n');
            if (!ok || data.empty()) {
                Json j;
                j.add("event", "error").add("message", "stdin line is not hex: " + line);
                emit(j, "-- not a hex string: " + line);
                continue;
            }
            struct iovec iov = {data.data(), data.size()};
            write_all(*sp, &iov, 1);
            total_tx += data.size();
            Json j;
            j.add("event", "tx").add("len", int64_t(data.size())).add("total", total_tx);
            char head[96];
            std::snprintf(head, sizeof(head), "[%7.2fs] board tx %zu B (total tx %llu B): ",
                          double(now_ns() - t0) / 1e9, data.size(), (unsigned long long)total_tx);
            emit(j, head + ("\"" + printable(data.data(), data.size()) + "\""));
        }
        s->shutdown_write();
        closed("stdin closed");
        return 0;
    } catch (const std::exception& e) {
        Json j;
        j.add("event", "error").add("message", e.what());
        emit(j, std::string("-- error: ") + e.what());
        return 1;
    }
}

}  // namespace ctt
