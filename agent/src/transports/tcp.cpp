// TCP transport. Endpoint syntax: "host:port", "[v6addr]:port", ":port" (any).
#include <arpa/inet.h>
#include <netdb.h>
#include <netinet/in.h>
#include <netinet/tcp.h>
#include <sys/socket.h>
#include <unistd.h>

#include <cstring>
#include <stdexcept>

#include "ctt/json.hpp"
#include "fd_stream.hpp"
#include "net_common.hpp"

namespace ctt {
namespace {

using namespace net;

class TcpStream : public FdStream {
public:
    using FdStream::FdStream;

    void link_info(Json& j) const override {
        tcp_info ti{};
        socklen_t len = sizeof(ti);
        if (getsockopt(fd_, IPPROTO_TCP, TCP_INFO, &ti, &len) == 0) {
            j.add("tcp_rtt_us", ti.tcpi_rtt)
                .add("tcp_rttvar_us", ti.tcpi_rttvar)
                .add("tcp_snd_cwnd", ti.tcpi_snd_cwnd)
                .add("tcp_snd_mss", ti.tcpi_snd_mss)
                .add("tcp_total_retrans", ti.tcpi_total_retrans);
        }
        int v = 0;
        len = sizeof(v);
        if (getsockopt(fd_, SOL_SOCKET, SO_SNDBUF, &v, &len) == 0) j.add("so_sndbuf", v);
        len = sizeof(v);
        if (getsockopt(fd_, SOL_SOCKET, SO_RCVBUF, &v, &len) == 0) j.add("so_rcvbuf", v);
    }
};

void apply_tcp_options(int fd, const TransportOptions& o) {
    apply_socket_options(fd, o);
    if (o.nodelay) {
        int one = 1;
        setsockopt(fd, IPPROTO_TCP, TCP_NODELAY, &one, sizeof(one));
    }
}

class TcpListener : public Listener {
public:
    TcpListener(int fd, std::string local, TransportOptions o)
        : fd_(fd), local_(std::move(local)), opts_(std::move(o)) {}
    ~TcpListener() override { ::close(fd_); }

    std::unique_ptr<Stream> accept(int timeout_ms) override {
        if (!wait_readable(fd_, timeout_ms)) return nullptr;
        sockaddr_storage ss{};
        socklen_t len = sizeof(ss);
        int c = ::accept4(fd_, reinterpret_cast<sockaddr*>(&ss), &len, SOCK_CLOEXEC);
        if (c < 0) throw_errno("accept");
        apply_tcp_options(c, opts_);
        return std::make_unique<TcpStream>(c, addr_to_string(reinterpret_cast<sockaddr*>(&ss)));
    }

    std::string local() const override { return local_; }

private:
    int fd_;
    std::string local_;
    TransportOptions opts_;
};

class TcpTransport : public Transport {
public:
    const char* name() const override { return "tcp"; }
    const char* description() const override {
        return "TCP socket (WiFi / Ethernet / USB-NCM). Endpoint host:port";
    }
    std::string default_listen_endpoint() const override { return std::string(":") + kDefaultPort; }
    uint32_t default_block() const override { return 128 * 1024; }

    std::unique_ptr<Listener> listen(const std::string& ep, const TransportOptions& o) override {
        AddrInfo res;
        resolve(ep.empty() ? default_listen_endpoint() : ep, true, SOCK_STREAM, res);
        std::string last_err = "no address";
        for (addrinfo* a = res.ai; a; a = a->ai_next) {
            int fd = ::socket(a->ai_family, a->ai_socktype | SOCK_CLOEXEC, a->ai_protocol);
            if (fd < 0) continue;
            int one = 1;
            setsockopt(fd, SOL_SOCKET, SO_REUSEADDR, &one, sizeof(one));
            // Buffers must be set before listen() so window scaling is negotiated.
            if (o.sndbuf > 0) setsockopt(fd, SOL_SOCKET, SO_SNDBUF, &o.sndbuf, sizeof(o.sndbuf));
            if (o.rcvbuf > 0) setsockopt(fd, SOL_SOCKET, SO_RCVBUF, &o.rcvbuf, sizeof(o.rcvbuf));
            if (::bind(fd, a->ai_addr, a->ai_addrlen) == 0 && ::listen(fd, 4) == 0) {
                sockaddr_storage ss{};
                socklen_t len = sizeof(ss);
                getsockname(fd, reinterpret_cast<sockaddr*>(&ss), &len);
                return std::make_unique<TcpListener>(
                    fd, addr_to_string(reinterpret_cast<sockaddr*>(&ss)), o);
            }
            last_err = std::strerror(errno);
            ::close(fd);
        }
        throw std::runtime_error("tcp listen " + ep + ": " + last_err);
    }

    std::unique_ptr<Stream> connect(const std::string& ep, const TransportOptions& o) override {
        AddrInfo res;
        resolve(ep, false, SOCK_STREAM, res);
        std::string last_err = "no address";
        for (addrinfo* a = res.ai; a; a = a->ai_next) {
            int fd = ::socket(a->ai_family, a->ai_socktype | SOCK_CLOEXEC, a->ai_protocol);
            if (fd < 0) continue;
            apply_tcp_options(fd, o);  // also bounds connect() time via SO_SNDTIMEO
            if (::connect(fd, a->ai_addr, a->ai_addrlen) == 0)
                return std::make_unique<TcpStream>(fd, addr_to_string(a->ai_addr));
            last_err = std::strerror(errno);
            ::close(fd);
        }
        throw std::runtime_error("tcp connect " + ep + ": " + last_err);
    }
};

}  // namespace

std::unique_ptr<Transport> make_tcp_transport() { return std::make_unique<TcpTransport>(); }

}  // namespace ctt
