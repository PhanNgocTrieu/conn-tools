// Shared helpers for IP transports (TCP, UDP).
#pragma once

#include <netdb.h>
#include <sys/socket.h>

#include <string>

namespace ctt::net {

constexpr const char* kDefaultPort = "5201";

// "host:port", "[v6]:port", ":port", "host" (default port)
void split_host_port(const std::string& ep, std::string& host, std::string& port);
std::string addr_to_string(const sockaddr* sa);

struct AddrInfo {
    addrinfo* ai = nullptr;
    AddrInfo() = default;
    AddrInfo(const AddrInfo&) = delete;
    AddrInfo& operator=(const AddrInfo&) = delete;
    ~AddrInfo() {
        if (ai) freeaddrinfo(ai);
    }
};

void resolve(const std::string& ep, bool passive, int socktype, AddrInfo& out);

}  // namespace ctt::net
