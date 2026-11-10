#pragma once

#include <cstdint>
#include <cstdio>
#include <string>
#include <string_view>

#include "ctt/ctp.hpp"
#include "ctt/transport.hpp"

namespace ctt {

enum class Role { None, Server, Client, Version, ListTransports, Help };

struct AgentOptions {
    Role role = Role::None;
    std::string transport = "tcp";
    std::string endpoint;
    bool json = false;
    double interval_s = 1.0;
    TransportOptions topts;

    // server
    bool once = false;
    double accept_timeout_s = 0;  // 0 = wait forever

    // client
    ctp::Mode mode = ctp::Mode::Tx;
    uint32_t block = 0;  // 0 = transport default
    double duration_s = 10.0;
    uint64_t rate_bps = 0;
    bool verify = true;
    uint32_t echo_count = 0;
    uint32_t echo_interval_us = 10000;
};

bool parse_args(int argc, char** argv, AgentOptions& o, std::string& err);
void print_usage(FILE* f);

// "64K" -> 65536 (binary units: K/M/G = 1024^n)
bool parse_size(std::string_view s, uint64_t& out);
// "2M" -> 2000000 bits/s (decimal units: k/M/G = 1000^n)
bool parse_rate(std::string_view s, uint64_t& out);
// "10", "10s", "500ms", "2m" -> seconds
bool parse_seconds(std::string_view s, double& out);

}  // namespace ctt
