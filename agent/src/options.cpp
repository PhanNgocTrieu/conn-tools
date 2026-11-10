#include "ctt/options.hpp"

#include <cctype>
#include <cmath>
#include <cstdlib>
#include <string>

namespace ctt {
namespace {

bool parse_number_suffix(std::string_view s, double& num, std::string& suffix) {
    if (s.empty()) return false;
    std::string str(s);
    char* end = nullptr;
    num = std::strtod(str.c_str(), &end);
    if (end == str.c_str() || !std::isfinite(num) || num < 0) return false;
    suffix = end;
    for (auto& c : suffix) c = char(std::tolower(static_cast<unsigned char>(c)));
    return true;
}

}  // namespace

bool parse_size(std::string_view s, uint64_t& out) {
    double n;
    std::string suf;
    if (!parse_number_suffix(s, n, suf)) return false;
    double mul;
    if (suf.empty() || suf == "b") mul = 1;
    else if (suf == "k" || suf == "kb" || suf == "kib") mul = 1024.0;
    else if (suf == "m" || suf == "mb" || suf == "mib") mul = 1024.0 * 1024;
    else if (suf == "g" || suf == "gb" || suf == "gib") mul = 1024.0 * 1024 * 1024;
    else return false;
    out = uint64_t(n * mul);
    return true;
}

bool parse_rate(std::string_view s, uint64_t& out) {
    double n;
    std::string suf;
    if (!parse_number_suffix(s, n, suf)) return false;
    if (suf.size() > 3 && suf.compare(suf.size() - 3, 3, "bps") == 0) suf.resize(suf.size() - 3);
    double mul;
    if (suf.empty()) mul = 1;
    else if (suf == "k") mul = 1e3;
    else if (suf == "m") mul = 1e6;
    else if (suf == "g") mul = 1e9;
    else return false;
    out = uint64_t(n * mul);
    return true;
}

bool parse_seconds(std::string_view s, double& out) {
    double n;
    std::string suf;
    if (!parse_number_suffix(s, n, suf)) return false;
    if (suf.empty() || suf == "s") out = n;
    else if (suf == "ms") out = n / 1000.0;
    else if (suf == "us") out = n / 1e6;
    else if (suf == "m" || suf == "min") out = n * 60.0;
    else if (suf == "h") out = n * 3600.0;
    else return false;
    return true;
}

void print_usage(FILE* f) {
    std::fputs(
        "ctt-agent - Connectivity Test Tool agent (CTP v1)\n"
        "\n"
        "Usage:\n"
        "  ctt-agent server [options]                 wait for a test session\n"
        "  ctt-agent client -e ENDPOINT [options]     run a test against a server\n"
        "  ctt-agent i2c --bus N --addr 0xNN --step ...  I2C device test (MFi CP; see i2c --help)\n"
        "  ctt-agent storage --path FILE [--size 256M]   block-device throughput (USB storage)\n"
        "  ctt-agent list-transports | version | help\n"
        "\n"
        "Common options:\n"
        "  -t, --transport NAME    transport (default: tcp)\n"
        "  -e, --endpoint EP       server: bind address, client: target (tcp: host:port)\n"
        "  -i, --interval SEC      periodic report interval, 0 = off (default 1)\n"
        "  -J, --json              JSON-lines output (for the ctt orchestrator)\n"
        "  -o, --opt KEY=VALUE     transport specific option (repeatable)\n"
        "      --sndbuf SIZE       socket send buffer (e.g. 256K)\n"
        "      --rcvbuf SIZE       socket receive buffer\n"
        "      --timeout SEC       idle I/O timeout (default 10, 0 = none)\n"
        "\n"
        "Server options:\n"
        "      --once              exit after one session\n"
        "      --accept-timeout SEC  give up if no client connects in time\n"
        "\n"
        "Client options:\n"
        "  -m, --mode MODE         tx (client->server), rx (server->client), bidir, echo\n"
        "  -d, --duration SEC      test duration (default 10)\n"
        "  -b, --block SIZE        payload bytes per frame (default: per transport;\n"
        "                          echo: 64)\n"
        "  -r, --rate RATE         limit sender to RATE bits/s (e.g. 2M), 0 = unlimited\n"
        "      --no-verify         do not CRC-check payloads\n"
        "  -n, --count N           echo: number of round trips (default: run for duration)\n"
        "      --echo-interval MS  echo: gap between requests (default 10, 0 = back-to-back)\n"
        "      --nodelay           disable Nagle (always on for echo)\n",
        f);
}

bool parse_args(int argc, char** argv, AgentOptions& o, std::string& err) {
    if (argc < 2) {
        err = "missing command";
        return false;
    }
    std::string_view cmd = argv[1];
    if (cmd == "server") o.role = Role::Server;
    else if (cmd == "client") o.role = Role::Client;
    else if (cmd == "version" || cmd == "--version" || cmd == "-V") o.role = Role::Version;
    else if (cmd == "list-transports") o.role = Role::ListTransports;
    else if (cmd == "help" || cmd == "--help" || cmd == "-h") o.role = Role::Help;
    else {
        err = "unknown command: " + std::string(cmd);
        return false;
    }

    for (int i = 2; i < argc; ++i) {
        std::string arg = argv[i];
        std::string val;
        bool has_inline = false;
        if (arg.rfind("--", 0) == 0) {
            auto eq = arg.find('=');
            if (eq != std::string::npos) {
                val = arg.substr(eq + 1);
                arg.resize(eq);
                has_inline = true;
            }
        }
        auto need = [&](std::string& out) -> bool {
            if (has_inline) {
                out = val;
                return true;
            }
            if (i + 1 >= argc) {
                err = "option " + arg + " needs a value";
                return false;
            }
            out = argv[++i];
            return true;
        };
        auto bad = [&](const std::string& v) {
            err = "invalid value for " + arg + ": " + v;
            return false;
        };

        std::string v;
        uint64_t u = 0;
        double d = 0;
        if (arg == "-t" || arg == "--transport") {
            if (!need(o.transport)) return false;
        } else if (arg == "-e" || arg == "--endpoint") {
            if (!need(o.endpoint)) return false;
        } else if (arg == "-i" || arg == "--interval") {
            if (!need(v)) return false;
            if (!parse_seconds(v, o.interval_s)) return bad(v);
        } else if (arg == "-J" || arg == "--json") {
            o.json = true;
        } else if (arg == "-o" || arg == "--opt") {
            if (!need(v)) return false;
            auto eq = v.find('=');
            if (eq == std::string::npos) return bad(v);
            o.topts.extra[v.substr(0, eq)] = v.substr(eq + 1);
        } else if (arg == "--sndbuf" || arg == "--rcvbuf") {
            if (!need(v)) return false;
            if (!parse_size(v, u) || u > (1u << 30)) return bad(v);
            (arg == "--sndbuf" ? o.topts.sndbuf : o.topts.rcvbuf) = int(u);
        } else if (arg == "--timeout") {
            if (!need(v)) return false;
            if (!parse_seconds(v, d)) return bad(v);
            o.topts.timeout_ms = int(d * 1000);
        } else if (arg == "--once") {
            o.once = true;
        } else if (arg == "--accept-timeout") {
            if (!need(v)) return false;
            if (!parse_seconds(v, o.accept_timeout_s)) return bad(v);
        } else if (arg == "-m" || arg == "--mode") {
            if (!need(v)) return false;
            if (!ctp::parse_mode(v, o.mode)) return bad(v);
        } else if (arg == "-d" || arg == "--duration") {
            if (!need(v)) return false;
            if (!parse_seconds(v, o.duration_s) || o.duration_s > 4e6) return bad(v);
        } else if (arg == "-b" || arg == "--block") {
            if (!need(v)) return false;
            if (!parse_size(v, u) || u == 0 || u > ctp::kMaxPayload) return bad(v);
            o.block = uint32_t(u);
        } else if (arg == "-r" || arg == "--rate") {
            if (!need(v)) return false;
            if (!parse_rate(v, o.rate_bps)) return bad(v);
        } else if (arg == "--no-verify") {
            o.verify = false;
        } else if (arg == "-n" || arg == "--count") {
            if (!need(v)) return false;
            if (!parse_size(v, u) || u > 0xFFFFFFFFu) return bad(v);
            o.echo_count = uint32_t(u);
        } else if (arg == "--echo-interval") {
            if (!need(v)) return false;
            if (!parse_seconds(v + (v.find_first_not_of("0123456789.") == std::string::npos
                                        ? "ms"
                                        : ""),
                               d))
                return bad(v);
            o.echo_interval_us = uint32_t(d * 1e6);
        } else if (arg == "--nodelay") {
            o.topts.nodelay = true;
        } else {
            err = "unknown option: " + arg;
            return false;
        }
    }

    if (o.role == Role::Client && o.endpoint.empty()) {
        err = "client needs --endpoint";
        return false;
    }
    return true;
}

}  // namespace ctt
