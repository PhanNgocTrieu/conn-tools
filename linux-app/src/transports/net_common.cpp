#include "net_common.hpp"

#include <stdexcept>

namespace ctt::net {

void split_host_port(const std::string& ep, std::string& host, std::string& port) {
    if (!ep.empty() && ep[0] == '[') {
        auto close = ep.find(']');
        if (close == std::string::npos) throw std::runtime_error("bad endpoint: " + ep);
        host = ep.substr(1, close - 1);
        port = (close + 1 < ep.size() && ep[close + 1] == ':') ? ep.substr(close + 2) : "";
    } else {
        auto colon = ep.rfind(':');
        if (colon == std::string::npos) {
            host = ep;
            port.clear();
        } else {
            host = ep.substr(0, colon);
            port = ep.substr(colon + 1);
        }
    }
    if (port.empty()) port = kDefaultPort;
}

std::string addr_to_string(const sockaddr* sa) {
    char host[NI_MAXHOST], serv[NI_MAXSERV];
    socklen_t len = sa->sa_family == AF_INET6 ? sizeof(sockaddr_in6) : sizeof(sockaddr_in);
    if (getnameinfo(sa, len, host, sizeof(host), serv, sizeof(serv),
                    NI_NUMERICHOST | NI_NUMERICSERV) != 0)
        return "?";
    if (sa->sa_family == AF_INET6) return std::string("[") + host + "]:" + serv;
    return std::string(host) + ":" + serv;
}

void resolve(const std::string& ep, bool passive, int socktype, AddrInfo& out) {
    std::string host, port;
    split_host_port(ep, host, port);
    addrinfo hints{};
    hints.ai_family = AF_UNSPEC;
    hints.ai_socktype = socktype;
    // Numeric addresses first: resolved without NSS, so a statically linked
    // agent works on any DUT image. Names fall back to the full resolver.
    hints.ai_flags = (passive ? AI_PASSIVE : 0) | AI_NUMERICHOST | AI_NUMERICSERV;
    const char* h = host.empty() ? nullptr : host.c_str();
    int rc = getaddrinfo(h, port.c_str(), &hints, &out.ai);
    if (rc == EAI_NONAME && h) {
        hints.ai_flags = passive ? AI_PASSIVE : 0;
        rc = getaddrinfo(h, port.c_str(), &hints, &out.ai);
    }
    if (rc != 0) throw std::runtime_error("resolve " + ep + ": " + gai_strerror(rc));
}

}  // namespace ctt::net
