// AF_UNIX transport - for development and CI without radio hardware.
//
// Endpoint:  socket path, or "@name" for the Linux abstract namespace.
// Options:   -o type=stream|seqpacket   (default stream)
//            -o mtu=<bytes>             seqpacket packet size (default 672 = L2CAP
//                                       default MTU), exercises the same
//                                       segmentation path as L2CAP.
#include <sys/socket.h>
#include <sys/un.h>
#include <unistd.h>

#include <cerrno>
#include <cstring>
#include <stdexcept>

#include "bt_common.hpp"
#include "ctt/json.hpp"
#include "packet_stream.hpp"

namespace ctt {
namespace {

struct UnixConfig {
    bool seqpacket = false;
    size_t mtu = 672;
};

UnixConfig parse_config(const TransportOptions& o) {
    UnixConfig c;
    std::string t = bt::opt(o, "type");
    if (t == "seqpacket") c.seqpacket = true;
    else if (!t.empty() && t != "stream") throw std::runtime_error("unix type must be stream|seqpacket");
    std::string m = bt::opt(o, "mtu");
    if (!m.empty()) {
        c.mtu = bt::parse_number(m, "mtu");
        if (c.mtu < 33 || c.mtu > 65536) throw std::runtime_error("mtu must be 33..65536");
    }
    return c;
}

socklen_t make_addr(const std::string& path, sockaddr_un& a) {
    std::memset(&a, 0, sizeof(a));
    a.sun_family = AF_UNIX;
    if (path.empty() || path.size() >= sizeof(a.sun_path))
        throw std::runtime_error("bad unix socket path: '" + path + "'");
    std::memcpy(a.sun_path, path.data(), path.size());
    if (path[0] == '@') a.sun_path[0] = '\0';  // abstract namespace
    return socklen_t(offsetof(sockaddr_un, sun_path) + path.size());
}

std::unique_ptr<Stream> wrap(int fd, const std::string& peer, const UnixConfig& c) {
    if (c.seqpacket) return std::make_unique<PacketStream>(fd, peer, c.mtu, c.mtu);
    return std::make_unique<FdStream>(fd, peer);
}

class UnixListener : public Listener {
public:
    UnixListener(int fd, std::string path, UnixConfig c, TransportOptions o)
        : fd_(fd), path_(std::move(path)), cfg_(c), opts_(std::move(o)) {}
    ~UnixListener() override {
        ::close(fd_);
        if (!path_.empty() && path_[0] != '@') ::unlink(path_.c_str());
    }

    std::unique_ptr<Stream> accept(int timeout_ms) override {
        if (!wait_readable(fd_, timeout_ms)) return nullptr;
        int c = ::accept4(fd_, nullptr, nullptr, SOCK_CLOEXEC);
        if (c < 0) throw_errno("unix accept");
        apply_socket_options(c, opts_);
        return wrap(c, path_, cfg_);
    }

    std::string local() const override { return path_; }
    void describe(Json& j) const override {
        j.add("unix_type", cfg_.seqpacket ? "seqpacket" : "stream");
        if (cfg_.seqpacket) j.add("mtu", cfg_.mtu);
    }

private:
    int fd_;
    std::string path_;
    UnixConfig cfg_;
    TransportOptions opts_;
};

class UnixTransport : public Transport {
public:
    const char* name() const override { return "unix"; }
    const char* description() const override {
        return "AF_UNIX socket for tests without radio. Endpoint path or @abstract; "
               "-o type=seqpacket -o mtu= emulates L2CAP packets";
    }
    std::string default_listen_endpoint() const override { return "@ctt-agent"; }
    uint32_t default_block() const override { return 64 * 1024; }

    std::unique_ptr<Listener> listen(const std::string& ep, const TransportOptions& o) override {
        UnixConfig c = parse_config(o);
        std::string path = ep.empty() ? default_listen_endpoint() : ep;
        sockaddr_un a;
        socklen_t len = make_addr(path, a);
        int fd = ::socket(AF_UNIX, (c.seqpacket ? SOCK_SEQPACKET : SOCK_STREAM) | SOCK_CLOEXEC, 0);
        if (fd < 0) throw_errno("unix socket");
        if (path[0] != '@') ::unlink(path.c_str());
        if (::bind(fd, reinterpret_cast<sockaddr*>(&a), len) < 0 || ::listen(fd, 1) < 0) {
            int e = errno;
            ::close(fd);
            throw std::runtime_error("unix listen " + path + ": " + std::strerror(e));
        }
        return std::make_unique<UnixListener>(fd, path, c, o);
    }

    std::unique_ptr<Stream> connect(const std::string& ep, const TransportOptions& o) override {
        UnixConfig c = parse_config(o);
        sockaddr_un a;
        socklen_t len = make_addr(ep, a);
        int fd = ::socket(AF_UNIX, (c.seqpacket ? SOCK_SEQPACKET : SOCK_STREAM) | SOCK_CLOEXEC, 0);
        if (fd < 0) throw_errno("unix socket");
        apply_socket_options(fd, o);
        if (::connect(fd, reinterpret_cast<sockaddr*>(&a), len) < 0) {
            int e = errno;
            ::close(fd);
            throw std::runtime_error("unix connect " + ep + ": " + std::strerror(e));
        }
        return wrap(fd, ep, c);
    }
};

}  // namespace

std::unique_ptr<Transport> make_unix_transport() { return std::make_unique<UnixTransport>(); }

}  // namespace ctt
