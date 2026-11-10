// UDP transport: one CTP frame per datagram. Loss, reordering and jitter are
// real measurements here (the session layer retries lost control frames).
//
// Endpoint: "host:port" (connect), ":port" / "host:port" (listen), default 5201.
// Notes:
//  - default block 1440 B -> 1472 B datagrams = no IP fragmentation on a
//    1500-byte MTU (WiFi / Ethernet). Larger blocks are allowed (fragmented).
//  - a client without --rate is limited to 10 Mbit/s (set --rate for more).
//  - -o drop=P drops each outgoing datagram (data AND control) with
//    probability P: loss injection for testing / emulating a bad link.
#include <netinet/in.h>
#include <sys/socket.h>
#include <unistd.h>

#include <cerrno>
#include <cstdio>
#include <cstring>
#include <fstream>
#include <random>
#include <sstream>
#include <stdexcept>

#include "ctt/clock.hpp"
#include "ctt/json.hpp"
#include "net_common.hpp"
#include "packet_stream.hpp"

namespace ctt {
namespace {

using namespace net;

constexpr size_t kMaxDatagram = 65507;
constexpr int kDefaultRcvbuf = 4 << 20;  // absorb bursts; capped by net.core.rmem_max

// Kernel-wide UDP receive-buffer overflow counter (/proc/net/snmp), to tell
// "lost on the air" from "dropped by our own socket".
long long udp_rcvbuf_errors() {
    std::ifstream f("/proc/net/snmp");
    std::string hdr, val;
    while (std::getline(f, hdr) && std::getline(f, val)) {
        if (hdr.rfind("Udp:", 0) != 0) continue;
        std::istringstream h(hdr), v(val);
        std::string name, num;
        while (h >> name && v >> num)
            if (name == "RcvbufErrors") return std::stoll(num);
    }
    return -1;
}

class UdpStream : public PacketStream {
public:
    UdpStream(int fd, std::string peer, double drop)
        : PacketStream(fd, std::move(peer), kMaxDatagram, kMaxDatagram),
          rcvbuf_errors_start_(udp_rcvbuf_errors()), drop_(drop),
          rng_(uint32_t(now_ns())) {}

    bool lossy() const override { return true; }

    ssize_t writev_some(const struct iovec* iov, int iovcnt) override {
        size_t total = 0;
        for (int i = 0; i < iovcnt; ++i) total += iov[i].iov_len;
        if (drop_ > 0 && std::uniform_real_distribution<double>(0, 1)(rng_) < drop_) {
            ++injected_drops_;
            return ssize_t(total);
        }
        try {
            return PacketStream::writev_some(iov, iovcnt);
        } catch (const std::runtime_error&) {
            // ICMP port unreachable (peer not listening yet / already gone) or
            // a full local queue: the datagram is lost, which UDP allows.
            if (errno == ECONNREFUSED || errno == ENOBUFS || errno == EAGAIN) {
                ++dropped_tx_;
                return ssize_t(total);
            }
            throw;
        }
    }

    // Pending ICMP errors make poll() report readable; consume them here so a
    // following read never blocks.
    bool wait_readable(int timeout_ms) override {
        uint64_t deadline = now_ns() + uint64_t(timeout_ms < 0 ? 0 : timeout_ms) * 1000000ull;
        for (;;) {
            int left = timeout_ms < 0 ? -1 : int((deadline > now_ns() ? deadline - now_ns() : 0) / 1000000ull);
            if (!PacketStream::wait_readable(left)) return false;
            uint8_t b;
            ssize_t r = ::recv(fd_, &b, 1, MSG_PEEK | MSG_DONTWAIT);
            if (r >= 0) return true;
            if (errno != ECONNREFUSED && errno != EAGAIN && errno != EINTR) return true;  // let read throw
            if (timeout_ms >= 0 && now_ns() >= deadline) return false;
        }
    }

    void link_info(Json& j) const override {
        int v = 0;
        socklen_t len = sizeof(v);
        if (getsockopt(fd_, SOL_SOCKET, SO_SNDBUF, &v, &len) == 0) j.add("so_sndbuf", v);
        len = sizeof(v);
        if (getsockopt(fd_, SOL_SOCKET, SO_RCVBUF, &v, &len) == 0) j.add("so_rcvbuf", v);
        j.add("datagrams_tx", packets_tx()).add("datagrams_rx", packets_rx()).add("tx_dropped_local", dropped_tx_);
        if (drop_ > 0) j.add("injected_drop_prob", drop_).add("injected_drops", injected_drops_);
        long long now = udp_rcvbuf_errors();
        if (now >= 0 && rcvbuf_errors_start_ >= 0)
            j.add("udp_rcvbuf_errors", now - rcvbuf_errors_start_);  // host-wide, during the test
    }

private:
    uint64_t dropped_tx_ = 0;
    long long rcvbuf_errors_start_;
    double drop_;
    uint64_t injected_drops_ = 0;
    std::mt19937 rng_;
};

double drop_prob(const TransportOptions& o) {
    auto it = o.extra.find("drop");
    if (it == o.extra.end()) return 0;
    double d = std::stod(it->second);
    if (d < 0 || d >= 1) throw std::runtime_error("udp drop must be in [0, 1)");
    return d;
}

int make_socket(int family, const TransportOptions& o) {
    int fd = ::socket(family, SOCK_DGRAM | SOCK_CLOEXEC, 0);
    if (fd < 0) throw std::runtime_error(std::string("udp socket: ") + std::strerror(errno));
    TransportOptions oo = o;
    if (oo.rcvbuf <= 0) oo.rcvbuf = kDefaultRcvbuf;
    oo.timeout_ms = o.timeout_ms;
    apply_socket_options(fd, oo);
    return fd;
}

class UdpListener : public Listener {
public:
    UdpListener(std::string ep, TransportOptions o) : ep_(std::move(ep)), opts_(std::move(o)) { open(); }
    ~UdpListener() override {
        if (fd_ >= 0) ::close(fd_);
    }

    // The first datagram from a peer "accepts" it: the socket is connected to
    // that peer and handed to the session (the datagram stays queued).
    std::unique_ptr<Stream> accept(int timeout_ms) override {
        if (!wait_readable(fd_, timeout_ms)) return nullptr;
        sockaddr_storage ss{};
        socklen_t len = sizeof(ss);
        uint8_t b;
        if (::recvfrom(fd_, &b, 1, MSG_PEEK, reinterpret_cast<sockaddr*>(&ss), &len) < 0)
            throw_errno("udp recvfrom");
        if (::connect(fd_, reinterpret_cast<sockaddr*>(&ss), len) < 0) throw_errno("udp connect");
        int fd = fd_;
        fd_ = -1;
        open();  // fresh unconnected socket for the next session
        return std::make_unique<UdpStream>(fd, addr_to_string(reinterpret_cast<sockaddr*>(&ss)),
                                           drop_prob(opts_));
    }

    std::string local() const override { return local_; }

private:
    void open() {
        AddrInfo res;
        resolve(ep_, true, SOCK_DGRAM, res);
        std::string err = "no address";
        for (addrinfo* a = res.ai; a; a = a->ai_next) {
            int fd = make_socket(a->ai_family, opts_);
            int one = 1;
            setsockopt(fd, SOL_SOCKET, SO_REUSEADDR, &one, sizeof(one));
            setsockopt(fd, SOL_SOCKET, SO_REUSEPORT, &one, sizeof(one));
            if (::bind(fd, a->ai_addr, a->ai_addrlen) == 0) {
                sockaddr_storage ss{};
                socklen_t len = sizeof(ss);
                getsockname(fd, reinterpret_cast<sockaddr*>(&ss), &len);
                local_ = addr_to_string(reinterpret_cast<sockaddr*>(&ss));
                fd_ = fd;
                return;
            }
            err = std::strerror(errno);
            ::close(fd);
        }
        throw std::runtime_error("udp listen " + ep_ + ": " + err);
    }

    std::string ep_;
    TransportOptions opts_;
    int fd_ = -1;
    std::string local_;
};

class UdpTransport : public Transport {
public:
    const char* name() const override { return "udp"; }
    const char* description() const override {
        return "UDP datagrams (WiFi/Ethernet loss, jitter). Endpoint host:port; client default 10 Mbit/s";
    }
    std::string default_listen_endpoint() const override { return std::string(":") + kDefaultPort; }
    uint32_t default_block() const override { return 1440; }

    std::unique_ptr<Listener> listen(const std::string& ep, const TransportOptions& o) override {
        return std::make_unique<UdpListener>(ep.empty() ? default_listen_endpoint() : ep, o);
    }

    std::unique_ptr<Stream> connect(const std::string& ep, const TransportOptions& o) override {
        AddrInfo res;
        resolve(ep, false, SOCK_DGRAM, res);
        std::string err = "no address";
        for (addrinfo* a = res.ai; a; a = a->ai_next) {
            int fd = make_socket(a->ai_family, o);
            if (::connect(fd, a->ai_addr, a->ai_addrlen) == 0)
                return std::make_unique<UdpStream>(fd, addr_to_string(a->ai_addr), drop_prob(o));
            err = std::strerror(errno);
            ::close(fd);
        }
        throw std::runtime_error("udp connect " + ep + ": " + err);
    }
};

}  // namespace

std::unique_ptr<Transport> make_udp_transport() { return std::make_unique<UdpTransport>(); }

}  // namespace ctt
