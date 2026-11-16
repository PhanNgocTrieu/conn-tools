// RFCOMM (Bluetooth Classic, SPP-style serial stream).
//
// Endpoint:  connect  "AA:BB:CC:DD:EE:FF/<channel>"   (channel 1..30)
//            listen   "/<channel>", "hci0/<channel>", "AA:BB:../<channel>", or ""
//                     channel 0 / omitted = let the kernel pick a free channel
// Options:   -o security=low|medium|high|fips   (BT_SECURITY, default: kernel default)
//            -o adapter=hci0|AA:BB:..           (local adapter to use)
#include <sys/socket.h>
#include <unistd.h>

#include <cerrno>
#include <cstring>
#include <stdexcept>

#include "bt_common.hpp"
#include "ctt/json.hpp"
#include "fd_stream.hpp"

namespace ctt {
namespace {

using namespace bt;

std::string hint(int err) {
    if (err == EHOSTUNREACH || err == ENODEV)
        return " (no usable Bluetooth adapter? check `hciconfig -a` / usbipd attach)";
    if (err == ECONNREFUSED) return " (nothing listening on that channel on the peer)";
    if (err == EHOSTDOWN || err == ETIMEDOUT) return " (peer not reachable: powered/in range/connectable?)";
    if (err == EACCES || err == EPERM) return " (authentication / pairing failed or rejected)";
    return "";
}

int open_socket(const TransportOptions& o) {
    int fd = ::socket(kAfBluetooth, SOCK_STREAM | SOCK_CLOEXEC, kProtoRfcomm);
    if (fd < 0)
        throw std::runtime_error(std::string("RFCOMM socket: ") + std::strerror(errno) +
                                 " (kernel built without CONFIG_BT_RFCOMM?)");
    std::string sec = opt(o, "security");
    if (!sec.empty()) {
        int level = parse_security(sec);
        if (level < 0) {
            ::close(fd);
            throw std::runtime_error("invalid security level: " + sec);
        }
        set_security(fd, level);
    }
    return fd;
}

class RfcommStream : public FdStream {
public:
    RfcommStream(int fd, std::string peer, Bdaddr local) : FdStream(fd, std::move(peer)), local_(local) {}

    void link_info(Json& j) const override {
        int sec = get_security(fd_);
        if (sec >= 0) j.add("bt_security", security_name(sec));
        RfcommConnInfoOpt ci{};
        socklen_t len = sizeof(ci);
        if (getsockopt(fd_, kSolRfcomm, kRfcommConnInfo, &ci, &len) == 0)
            j.add("hci_handle", ci.hci_handle);
        int v = 0;
        len = sizeof(v);
        if (getsockopt(fd_, SOL_SOCKET, SO_SNDBUF, &v, &len) == 0) j.add("so_sndbuf", v);
        len = sizeof(v);
        if (getsockopt(fd_, SOL_SOCKET, SO_RCVBUF, &v, &len) == 0) j.add("so_rcvbuf", v);
        adapter_json(j, local_);
    }

private:
    Bdaddr local_;
};

class RfcommListener : public Listener {
public:
    RfcommListener(int fd, Bdaddr local, uint8_t channel, TransportOptions o)
        : fd_(fd), local_(local), channel_(channel), opts_(std::move(o)) {
        // Report the adapter's real address when bound to BDADDR_ANY, so the
        // client knows where to connect.
        if (local_.is_any()) {
            AdapterInfo a;
            if (default_adapter(a)) shown_ = a.addr;
        } else {
            shown_ = local_;
        }
    }
    ~RfcommListener() override { ::close(fd_); }

    std::unique_ptr<Stream> accept(int timeout_ms) override {
        if (!wait_readable(fd_, timeout_ms)) return nullptr;
        SockaddrRc ra{};
        socklen_t len = sizeof(ra);
        int c = ::accept4(fd_, reinterpret_cast<sockaddr*>(&ra), &len, SOCK_CLOEXEC);
        if (c < 0) throw_errno("rfcomm accept");
        apply_socket_options(c, opts_);
        return std::make_unique<RfcommStream>(
            c, to_string(ra.rc_bdaddr) + "/" + std::to_string(ra.rc_channel), local_);
    }

    std::string local() const override { return to_string(shown_) + "/" + std::to_string(channel_); }

    void describe(Json& j) const override {
        j.add("bdaddr", to_string(shown_)).add("channel", channel_);
    }

private:
    int fd_;
    Bdaddr local_;
    Bdaddr shown_;
    uint8_t channel_;
    TransportOptions opts_;
};

class RfcommTransport : public Transport {
public:
    const char* name() const override { return "rfcomm"; }
    const char* description() const override {
        return "Bluetooth RFCOMM stream. Endpoint BDADDR/CHANNEL; -o security=, -o adapter=";
    }
    std::string default_listen_endpoint() const override { return "/0"; }
    uint32_t default_block() const override { return 4096; }

    std::unique_ptr<Listener> listen(const std::string& ep, const TransportOptions& o) override {
        std::string addr_s, ch_s;
        split_endpoint(ep, addr_s, ch_s);
        uint32_t ch = ch_s.empty() ? 0 : parse_number(ch_s, "RFCOMM channel");
        if (ch > 30) throw std::runtime_error("RFCOMM channel must be 0..30");

        TransportOptions oo = o;
        if (!addr_s.empty()) oo.extra["adapter"] = addr_s;
        Bdaddr local = local_bdaddr(oo);

        int fd = open_socket(o);
        if (o.rcvbuf > 0) setsockopt(fd, SOL_SOCKET, SO_RCVBUF, &o.rcvbuf, sizeof(o.rcvbuf));
        if (o.sndbuf > 0) setsockopt(fd, SOL_SOCKET, SO_SNDBUF, &o.sndbuf, sizeof(o.sndbuf));
        SockaddrRc la{};
        la.rc_family = kAfBluetooth;
        la.rc_bdaddr = local;
        la.rc_channel = uint8_t(ch);
        if (::bind(fd, reinterpret_cast<sockaddr*>(&la), sizeof(la)) < 0 || ::listen(fd, 1) < 0) {
            int e = errno;
            ::close(fd);
            throw std::runtime_error("rfcomm listen " + to_string(local) + "/" + std::to_string(ch) +
                                     ": " + std::strerror(e) +
                                     (e == EADDRINUSE ? " (channel used by another profile/app)" : ""));
        }
        // Channel 0 -> the kernel assigned a free one at listen(); read it back.
        SockaddrRc bound{};
        socklen_t len = sizeof(bound);
        if (getsockname(fd, reinterpret_cast<sockaddr*>(&bound), &len) == 0) ch = bound.rc_channel;
        return std::make_unique<RfcommListener>(fd, local, uint8_t(ch), o);
    }

    std::unique_ptr<Stream> connect(const std::string& ep, const TransportOptions& o) override {
        std::string addr_s, ch_s;
        split_endpoint(ep, addr_s, ch_s);
        SockaddrRc ra{};
        ra.rc_family = kAfBluetooth;
        if (!parse_bdaddr(addr_s, ra.rc_bdaddr))
            throw std::runtime_error("rfcomm endpoint must be BDADDR/CHANNEL, got: " + ep);
        uint32_t ch = ch_s.empty() ? 1 : parse_number(ch_s, "RFCOMM channel");
        if (ch < 1 || ch > 30) throw std::runtime_error("RFCOMM channel must be 1..30");
        ra.rc_channel = uint8_t(ch);

        Bdaddr local = local_bdaddr(o);
        int fd = open_socket(o);
        apply_socket_options(fd, o);  // SO_SNDTIMEO also bounds connect()
        if (!local.is_any()) {
            SockaddrRc la{};
            la.rc_family = kAfBluetooth;
            la.rc_bdaddr = local;
            if (::bind(fd, reinterpret_cast<sockaddr*>(&la), sizeof(la)) < 0) {
                int e = errno;
                ::close(fd);
                throw std::runtime_error(std::string("rfcomm bind adapter: ") + std::strerror(e));
            }
        }
        if (::connect(fd, reinterpret_cast<sockaddr*>(&ra), sizeof(ra)) < 0) {
            int e = errno;
            ::close(fd);
            throw std::runtime_error("rfcomm connect " + ep + ": " + std::strerror(e) + hint(e));
        }
        return std::make_unique<RfcommStream>(fd, to_string(ra.rc_bdaddr) + "/" + std::to_string(ch),
                                              local);
    }
};

}  // namespace

std::unique_ptr<Transport> make_rfcomm_transport() { return std::make_unique<RfcommTransport>(); }

}  // namespace ctt
