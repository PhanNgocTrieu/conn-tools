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

void emit(const Json& j) {
    std::lock_guard<std::mutex> lk(g_out);
    std::fputs(j.str().c_str(), stdout);
    std::fputc('\n', stdout);
    std::fflush(stdout);
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
        "                      [--accept-timeout SEC]\n"
        "Relays a raw link: stdin hex lines -> link, link data -> stdout JSON lines.\n"
        "Default: -t rfcomm, listen on a kernel-assigned channel.\n",
        f);
}

void closed(const std::string& reason) {
    Json j;
    j.add("event", "closed").add("reason", reason);
    emit(j);
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
            emit(jl);
            s = lis->accept(accept_timeout > 0 ? int(accept_timeout * 1000) : -1);
            if (!s) {
                closed("accept timeout");
                return 2;
            }
        }
        Json jc;
        jc.add("event", "connected").add("peer", s->peer());
        emit(jc);

        const uint64_t t0 = now_ns();
        Stream* sp = s.get();
        std::thread reader([sp, t0] {
            uint8_t buf[4096];
            try {
                for (;;) {
                    ssize_t n = sp->read_some(buf, sizeof(buf));
                    if (n <= 0) break;
                    Json j;
                    j.add("event", "rx")
                        .add("t", double(now_ns() - t0) / 1e9)
                        .add("len", int64_t(n))
                        .add("hex", to_hex(buf, size_t(n)));
                    emit(j);
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
        while (std::getline(std::cin, line)) {
            if (line.empty()) continue;
            if (!from_hex(line, data)) {
                Json j;
                j.add("event", "error").add("message", "stdin line is not hex: " + line);
                emit(j);
                continue;
            }
            struct iovec iov = {data.data(), data.size()};
            write_all(*sp, &iov, 1);
            Json j;
            j.add("event", "tx").add("len", int64_t(data.size()));
            emit(j);
        }
        s->shutdown_write();
        closed("stdin closed");
        return 0;
    } catch (const std::exception& e) {
        Json j;
        j.add("event", "error").add("message", e.what());
        emit(j);
        return 1;
    }
}

}  // namespace ctt
