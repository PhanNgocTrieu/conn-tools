// L2CAP connection-oriented channels (SOCK_SEQPACKET).
//
// Endpoint:  connect  "AA:BB:CC:DD:EE:FF/<psm>"     listen "/<psm>" ("" = default PSM)
// Options:   -o type=bredr|le|le-random   (default bredr; le = LE Credit Based CoC)
//            -o imtu=<bytes>               (our receive MTU)
//            -o security=..., -o adapter=...
// Default PSM: 0x1001 (BR/EDR dynamic range), 0x0080 (LE dynamic range).
#include <endian.h>
#include <sys/socket.h>
#include <unistd.h>

#include <cstdio>

#include <cerrno>
#include <cstring>
#include <stdexcept>

#include "bt_common.hpp"
#include "ctt/json.hpp"
#include "packet_stream.hpp"

namespace ctt {
namespace {

using namespace bt;

struct L2Config {
    uint8_t type = kAddrBredr;
    uint16_t imtu = 0;
    int security = -1;
};

L2Config parse_config(const TransportOptions& o) {
    L2Config c;
    std::string t = opt(o, "type");
    if (t.empty() || t == "bredr") c.type = kAddrBredr;
    else if (t == "le" || t == "le-public") c.type = kAddrLePublic;
    else if (t == "le-random") c.type = kAddrLeRandom;
    else throw std::runtime_error("l2cap type must be bredr|le|le-random, got: " + t);
    std::string m = opt(o, "imtu");
    if (!m.empty()) {
        uint32_t v = parse_number(m, "imtu");
        if (v < 23 || v > 65535) throw std::runtime_error("imtu must be 23..65535");
        c.imtu = uint16_t(v);
    }
    std::string s = opt(o, "security");
    if (!s.empty()) {
        c.security = parse_security(s);
        if (c.security < 0) throw std::runtime_error("invalid security level: " + s);
    }
    return c;
}

bool is_le(uint8_t type) { return type != kAddrBredr; }
uint16_t default_psm(uint8_t type) { return is_le(type) ? 0x0080 : 0x1001; }

int open_socket(const L2Config& c, const TransportOptions& o) {
    int fd = ::socket(kAfBluetooth, SOCK_SEQPACKET | SOCK_CLOEXEC, kProtoL2cap);
    if (fd < 0) throw std::runtime_error(std::string("L2CAP socket: ") + std::strerror(errno));
    try {
        if (c.security >= 0) set_security(fd, c.security);
        if (c.imtu) {
            if (is_le(c.type)) {
                uint16_t m = c.imtu;
                if (setsockopt(fd, kSolBluetooth, kBtRcvMtu, &m, sizeof(m)) < 0)
                    throw std::runtime_error(std::string("BT_RCVMTU: ") + std::strerror(errno));
            } else {
                L2capOptionsOpt lo{};
                socklen_t len = sizeof(lo);
                if (getsockopt(fd, kSolL2cap, kL2capOptions, &lo, &len) == 0) {
                    lo.imtu = c.imtu;
                    setsockopt(fd, kSolL2cap, kL2capOptions, &lo, sizeof(lo));
                }
            }
        }
        if (o.sndbuf > 0) setsockopt(fd, SOL_SOCKET, SO_SNDBUF, &o.sndbuf, sizeof(o.sndbuf));
        if (o.rcvbuf > 0) setsockopt(fd, SOL_SOCKET, SO_RCVBUF, &o.rcvbuf, sizeof(o.rcvbuf));
    } catch (...) {
        ::close(fd);
        throw;
    }
    return fd;
}

void query_mtu(int fd, size_t& omtu, size_t& imtu) {
    uint16_t s = 0, r = 0;
    socklen_t len = sizeof(s);
    if (getsockopt(fd, kSolBluetooth, kBtSndMtu, &s, &len) == 0) {  // LE CoC
        len = sizeof(r);
        if (getsockopt(fd, kSolBluetooth, kBtRcvMtu, &r, &len) == 0 && s && r) {
            omtu = s;
            imtu = r;
            return;
        }
    }
    L2capOptionsOpt lo{};
    len = sizeof(lo);
    if (getsockopt(fd, kSolL2cap, kL2capOptions, &lo, &len) == 0 && lo.omtu && lo.imtu) {
        omtu = lo.omtu;
        imtu = lo.imtu;
        return;
    }
    omtu = imtu = 672;  // L2CAP default MTU
}

class L2capStream : public PacketStream {
public:
    L2capStream(int fd, std::string peer, size_t omtu, size_t imtu, Bdaddr local, uint8_t type)
        : PacketStream(fd, std::move(peer), omtu, imtu), local_(local), type_(type) {}

    void link_info(Json& j) const override {
        j.add("l2cap_type", is_le(type_) ? "le" : "bredr")
            .add("l2cap_omtu", omtu())
            .add("l2cap_imtu", imtu())
            .add("packets_tx", packets_tx())
            .add("packets_rx", packets_rx());
        int sec = get_security(fd_);
        if (sec >= 0) j.add("bt_security", security_name(sec));
        uint32_t phy = 0;
        socklen_t len = sizeof(phy);
        if (getsockopt(fd_, kSolBluetooth, kBtPhy, &phy, &len) == 0) j.add("bt_phy_mask", phy);
        adapter_json(j, local_);
    }

private:
    Bdaddr local_;
    uint8_t type_;
};

std::string peer_name(const SockaddrL2& a) {
    return to_string(a.l2_bdaddr) + "/0x" + [&] {
        char b[8];
        std::snprintf(b, sizeof(b), "%04x", unsigned(le16toh(a.l2_psm)));
        return std::string(b);
    }();
}

class L2capListener : public Listener {
public:
    L2capListener(int fd, Bdaddr local, uint16_t psm, L2Config c, TransportOptions o)
        : fd_(fd), local_(local), shown_(local), psm_(psm), cfg_(c), opts_(std::move(o)) {
        AdapterInfo a;
        if (local_.is_any() && default_adapter(a)) shown_ = a.addr;
    }
    ~L2capListener() override { ::close(fd_); }

    std::unique_ptr<Stream> accept(int timeout_ms) override {
        if (!wait_readable(fd_, timeout_ms)) return nullptr;
        SockaddrL2 ra{};
        socklen_t len = sizeof(ra);
        int c = ::accept4(fd_, reinterpret_cast<sockaddr*>(&ra), &len, SOCK_CLOEXEC);
        if (c < 0) throw_errno("l2cap accept");
        apply_socket_options(c, opts_);
        size_t omtu, imtu;
        query_mtu(c, omtu, imtu);
        ra.l2_psm = htole16(psm_);
        return std::make_unique<L2capStream>(c, peer_name(ra), omtu, imtu, local_, cfg_.type);
    }

    std::string local() const override {
        char b[16];
        std::snprintf(b, sizeof(b), "/0x%04x", psm_);
        return to_string(shown_) + b;
    }

    void describe(Json& j) const override {
        j.add("bdaddr", to_string(shown_)).add("psm", psm_).add("l2cap_type",
                                                                 is_le(cfg_.type) ? "le" : "bredr");
    }

private:
    int fd_;
    Bdaddr local_, shown_;
    uint16_t psm_;
    L2Config cfg_;
    TransportOptions opts_;
};

class L2capTransport : public Transport {
public:
    const char* name() const override { return "l2cap"; }
    const char* description() const override {
        return "Bluetooth L2CAP CoC (BR/EDR or LE). Endpoint BDADDR/PSM; -o type=bredr|le, -o imtu=";
    }
    std::string default_listen_endpoint() const override { return ""; }
    uint32_t default_block() const override { return 4096; }

    std::unique_ptr<Listener> listen(const std::string& ep, const TransportOptions& o) override {
        L2Config c = parse_config(o);
        std::string addr_s, psm_s;
        split_endpoint(ep, addr_s, psm_s);
        uint32_t psm = psm_s.empty() || psm_s == "0" ? default_psm(c.type)
                                                     : parse_number(psm_s, "PSM");
        if (psm > 0xFFFF) throw std::runtime_error("PSM out of range");
        TransportOptions oo = o;
        if (!addr_s.empty()) oo.extra["adapter"] = addr_s;
        Bdaddr local = local_bdaddr(oo);

        int fd = open_socket(c, o);
        SockaddrL2 la{};
        la.l2_family = kAfBluetooth;
        la.l2_psm = htole16(uint16_t(psm));
        la.l2_bdaddr = local;
        la.l2_bdaddr_type = is_le(c.type) ? kAddrLePublic : kAddrBredr;
        if (::bind(fd, reinterpret_cast<sockaddr*>(&la), sizeof(la)) < 0 || ::listen(fd, 1) < 0) {
            int e = errno;
            ::close(fd);
            throw std::runtime_error("l2cap listen psm " + std::to_string(psm) + ": " +
                                     std::strerror(e));
        }
        return std::make_unique<L2capListener>(fd, local, uint16_t(psm), c, o);
    }

    std::unique_ptr<Stream> connect(const std::string& ep, const TransportOptions& o) override {
        L2Config c = parse_config(o);
        std::string addr_s, psm_s;
        split_endpoint(ep, addr_s, psm_s);
        SockaddrL2 ra{};
        ra.l2_family = kAfBluetooth;
        if (!parse_bdaddr(addr_s, ra.l2_bdaddr))
            throw std::runtime_error("l2cap endpoint must be BDADDR/PSM, got: " + ep);
        uint32_t psm = psm_s.empty() ? default_psm(c.type) : parse_number(psm_s, "PSM");
        ra.l2_psm = htole16(uint16_t(psm));
        ra.l2_bdaddr_type = c.type;

        Bdaddr local = local_bdaddr(o);
        int fd = open_socket(c, o);
        apply_socket_options(fd, o);
        if (!local.is_any() || is_le(c.type)) {
            SockaddrL2 la{};
            la.l2_family = kAfBluetooth;
            la.l2_bdaddr = local;
            la.l2_bdaddr_type = is_le(c.type) ? kAddrLePublic : kAddrBredr;
            if (::bind(fd, reinterpret_cast<sockaddr*>(&la), sizeof(la)) < 0) {
                int e = errno;
                ::close(fd);
                throw std::runtime_error(std::string("l2cap bind: ") + std::strerror(e));
            }
        }
        if (::connect(fd, reinterpret_cast<sockaddr*>(&ra), sizeof(ra)) < 0) {
            int e = errno;
            ::close(fd);
            std::string h = (e == EHOSTUNREACH || e == ENODEV)
                                ? " (no usable Bluetooth adapter?)"
                                : (e == ECONNREFUSED ? " (PSM not listening on the peer)" : "");
            throw std::runtime_error("l2cap connect " + ep + ": " + std::strerror(e) + h);
        }
        size_t omtu, imtu;
        query_mtu(fd, omtu, imtu);
        return std::make_unique<L2capStream>(fd, peer_name(ra), omtu, imtu, local, c.type);
    }
};

}  // namespace

std::unique_ptr<Transport> make_l2cap_transport() { return std::make_unique<L2capTransport>(); }

}  // namespace ctt
