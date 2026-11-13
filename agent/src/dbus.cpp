#include "dbus.hpp"

#include <poll.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <unistd.h>

#include <cerrno>
#include <chrono>
#include <cstdlib>
#include <cstring>
#include <stdexcept>

namespace ctt::dbus {

// ---- values -------------------------------------------------------------------------------

const Val* Val::get(const std::string& k) const {
    for (const auto& e : v) {
        if (e.t != '{' || e.v.size() != 2 || e.v[0].s != k) continue;
        const Val* x = &e.v[1];
        return x->t == 'v' && !x->v.empty() ? &x->v[0] : x;
    }
    return nullptr;
}

Val byte(uint8_t x) { Val v; v.t = 'y'; v.u = x; return v; }
Val boolean(bool x) { Val v; v.t = 'b'; v.u = x; return v; }
Val u16(uint16_t x) { Val v; v.t = 'q'; v.u = x; return v; }
Val i16(int16_t x) { Val v; v.t = 'n'; v.u = uint16_t(x); return v; }
Val u32(uint32_t x) { Val v; v.t = 'u'; v.u = x; return v; }
Val str(const std::string& x) { Val v; v.t = 's'; v.s = x; return v; }
Val path(const std::string& x) { Val v; v.t = 'o'; v.s = x; return v; }
Val fd_index(uint32_t idx) { Val v; v.t = 'h'; v.u = idx; return v; }

Val variant(const Val& inner) {
    Val v;
    v.t = 'v';
    v.sig = signature_of(inner);
    v.v.push_back(inner);
    return v;
}

Val array(const std::string& elem_sig, std::vector<Val> items) {
    Val v;
    v.t = 'a';
    v.sig = elem_sig;
    v.v = std::move(items);
    return v;
}

Val bytes(const uint8_t* p, size_t n) {
    Val v = array("y");
    v.v.reserve(n);
    for (size_t i = 0; i < n; ++i) v.v.push_back(byte(p[i]));
    return v;
}

Val strings(const std::vector<std::string>& xs) {
    Val v = array("s");
    for (const auto& x : xs) v.v.push_back(str(x));
    return v;
}

Val entry(Val k, Val val) {
    Val v;
    v.t = '{';
    v.v.push_back(std::move(k));
    v.v.push_back(std::move(val));
    return v;
}

Val sv(const std::string& k, const Val& val) { return entry(str(k), variant(val)); }

Val structure(std::vector<Val> items) {
    Val v;
    v.t = '(';
    v.v = std::move(items);
    return v;
}

std::string signature_of(const Val& v) {
    switch (v.t) {
        case 'a': return "a" + v.sig;
        case '(': {
            std::string s = "(";
            for (const auto& x : v.v) s += signature_of(x);
            return s + ")";
        }
        case '{': return "{" + signature_of(v.v.at(0)) + signature_of(v.v.at(1)) + "}";
        default: return std::string(1, v.t);
    }
}

// ---- marshalling ----------------------------------------------------------------------------

namespace {

size_t align_of(char t) {
    switch (t) {
        case 'y': case 'g': case 'v': return 1;
        case 'n': case 'q': return 2;
        case 'b': case 'i': case 'u': case 's': case 'o': case 'a': case 'h': return 4;
        case 'x': case 't': case 'd': case '(': case '{': return 8;
        default: throw std::runtime_error(std::string("dbus: unsupported type ") + t);
    }
}

// Length of the first complete type in `sig` starting at `i`.
size_t type_len(const std::string& sig, size_t i) {
    if (i >= sig.size()) throw std::runtime_error("dbus: bad signature " + sig);
    char c = sig[i];
    if (c == 'a') return 1 + type_len(sig, i + 1);
    if (c == '(' || c == '{') {
        char close = c == '(' ? ')' : '}';
        size_t j = i + 1;
        while (j < sig.size() && sig[j] != close) j += type_len(sig, j);
        if (j >= sig.size()) throw std::runtime_error("dbus: bad signature " + sig);
        return j - i + 1;
    }
    return 1;
}

struct Writer {
    std::vector<uint8_t> b;
    void pad(size_t a) {
        while (b.size() % a) b.push_back(0);
    }
    void raw(const void* p, size_t n) {
        auto* c = static_cast<const uint8_t*>(p);
        b.insert(b.end(), c, c + n);
    }
    void u8(uint8_t x) { b.push_back(x); }
    void u16(uint16_t x) { pad(2); raw(&x, 2); }
    void u32(uint32_t x) { pad(4); raw(&x, 4); }
    void u64(uint64_t x) { pad(8); raw(&x, 8); }
    void string(const std::string& s) {
        u32(uint32_t(s.size()));
        raw(s.data(), s.size());
        u8(0);
    }
    void sig(const std::string& s) {
        u8(uint8_t(s.size()));
        raw(s.data(), s.size());
        u8(0);
    }

    void value(const Val& v, const std::string& type) {
        char t = type.at(0);
        switch (t) {
            case 'y': u8(uint8_t(v.u)); break;
            case 'b': case 'i': case 'u': case 'h': u32(uint32_t(v.u)); break;
            case 'n': case 'q': u16(uint16_t(v.u)); break;
            case 'x': case 't': case 'd': u64(v.u); break;
            case 's': case 'o': string(v.s); break;
            case 'g': sig(v.s); break;
            case 'v': {
                sig(v.sig);
                value(v.v.at(0), v.sig);
                break;
            }
            case 'a': {
                std::string elem = type.substr(1, type_len(type, 1));
                u32(0);
                size_t len_at = b.size() - 4;
                pad(align_of(elem[0]));
                size_t start = b.size();
                for (const auto& x : v.v) value(x, elem);
                uint32_t len = uint32_t(b.size() - start);
                std::memcpy(&b[len_at], &len, 4);
                break;
            }
            case '(': case '{': {
                pad(8);
                size_t i = 1, k = 0;
                while (type[i] != ')' && type[i] != '}') {
                    size_t n = type_len(type, i);
                    value(v.v.at(k++), type.substr(i, n));
                    i += n;
                }
                break;
            }
            default: throw std::runtime_error(std::string("dbus: cannot write type ") + t);
        }
    }
};

struct Reader {
    const uint8_t* p;
    size_t n;
    size_t i = 0;
    void need(size_t k) {
        if (i + k > n) throw std::runtime_error("dbus: truncated message");
    }
    void align(size_t a) {
        while (i % a) {
            need(1);
            ++i;
        }
    }
    uint8_t u8() { need(1); return p[i++]; }
    uint16_t u16() { align(2); need(2); uint16_t x; std::memcpy(&x, p + i, 2); i += 2; return x; }
    uint32_t u32() { align(4); need(4); uint32_t x; std::memcpy(&x, p + i, 4); i += 4; return x; }
    uint64_t u64() { align(8); need(8); uint64_t x; std::memcpy(&x, p + i, 8); i += 8; return x; }
    std::string string() {
        uint32_t len = u32();
        need(size_t(len) + 1);
        std::string s(reinterpret_cast<const char*>(p + i), len);
        i += len + 1;
        return s;
    }
    std::string sig() {
        uint8_t len = u8();
        need(size_t(len) + 1);
        std::string s(reinterpret_cast<const char*>(p + i), len);
        i += len + 1;
        return s;
    }

    Val value(const std::string& type) {
        Val v;
        v.t = type.at(0);
        switch (v.t) {
            case 'y': v.u = u8(); break;
            case 'b': case 'i': case 'u': case 'h': v.u = u32(); break;
            case 'n': case 'q': v.u = u16(); break;
            case 'x': case 't': case 'd': v.u = u64(); break;
            case 's': case 'o': v.s = string(); break;
            case 'g': v.s = sig(); break;
            case 'v': {
                v.sig = sig();
                v.v.push_back(value(v.sig));
                break;
            }
            case 'a': {
                std::string elem = type.substr(1, type_len(type, 1));
                v.sig = elem;
                uint32_t len = u32();
                align(align_of(elem[0]));
                size_t end = i + len;
                need(len);
                while (i < end) v.v.push_back(value(elem));
                break;
            }
            case '(': case '{': {
                align(8);
                size_t k = 1;
                while (type[k] != ')' && type[k] != '}') {
                    size_t len = type_len(type, k);
                    v.v.push_back(value(type.substr(k, len)));
                    k += len;
                }
                break;
            }
            default: throw std::runtime_error(std::string("dbus: cannot read type ") + v.t);
        }
        return v;
    }
};

std::vector<uint8_t> encode(const Message& m) {
    std::string body_sig;
    for (const auto& v : m.body) body_sig += signature_of(v);
    Writer body;
    for (const auto& v : m.body) body.value(v, signature_of(v));

    Writer w;
    w.u8('l');
    w.u8(m.type);
    w.u8(m.flags);
    w.u8(1);
    w.u32(uint32_t(body.b.size()));
    w.u32(m.serial);
    std::vector<Val> fields;
    auto field = [&](uint8_t code, const Val& v) { fields.push_back(structure({byte(code), variant(v)})); };
    if (!m.path.empty()) field(1, path(m.path));
    if (!m.interface.empty()) field(2, str(m.interface));
    if (!m.member.empty()) field(3, str(m.member));
    if (!m.error_name.empty()) field(4, str(m.error_name));
    if (m.reply_serial) field(5, u32(m.reply_serial));
    if (!m.destination.empty()) field(6, str(m.destination));
    if (!body_sig.empty()) {
        Val g;
        g.t = 'g';
        g.s = body_sig;
        field(8, g);
    }
    if (!m.fds.empty()) field(9, u32(uint32_t(m.fds.size())));
    w.value(array("(yv)", fields), "a(yv)");
    w.pad(8);
    w.raw(body.b.data(), body.b.size());
    return w.b;
}

std::string hex(const std::string& s) {
    static const char* d = "0123456789abcdef";
    std::string out;
    for (unsigned char c : s) {
        out += d[c >> 4];
        out += d[c & 15];
    }
    return out;
}

void write_line(int fd, const std::string& s) {
    size_t off = 0;
    while (off < s.size()) {
        ssize_t r = ::send(fd, s.data() + off, s.size() - off, MSG_NOSIGNAL);
        if (r < 0) {
            if (errno == EINTR) continue;
            throw std::runtime_error(std::string("dbus auth: ") + std::strerror(errno));
        }
        off += size_t(r);
    }
}

std::string read_line(int fd) {
    std::string s;
    char c;
    while (s.size() < 4096) {
        ssize_t r = ::recv(fd, &c, 1, 0);
        if (r < 0 && errno == EINTR) continue;
        if (r <= 0) throw std::runtime_error("dbus auth: bus closed the connection");
        s += c;
        if (s.size() >= 2 && s.compare(s.size() - 2, 2, "\r\n") == 0) return s.substr(0, s.size() - 2);
    }
    throw std::runtime_error("dbus auth: line too long");
}

std::string socket_path() {
    // DBUS_SYSTEM_BUS_ADDRESS=unix:path=/run/dbus/system_bus_socket (tests use their own bus)
    const char* a = std::getenv("DBUS_SYSTEM_BUS_ADDRESS");
    std::string addr = a ? a : "unix:path=/run/dbus/system_bus_socket";
    auto k = addr.find("path=");
    if (addr.rfind("unix:", 0) != 0 || k == std::string::npos)
        throw std::runtime_error("dbus: only unix:path= bus addresses are supported: " + addr);
    std::string p = addr.substr(k + 5);
    auto comma = p.find(',');
    return comma == std::string::npos ? p : p.substr(0, comma);
}

}  // namespace

// ---- connection -----------------------------------------------------------------------------

Connection::Connection() {
    std::string sp = socket_path();
    fd_ = ::socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0);
    if (fd_ < 0) throw std::runtime_error(std::string("dbus socket: ") + std::strerror(errno));
    sockaddr_un sa{};
    sa.sun_family = AF_UNIX;
    std::strncpy(sa.sun_path, sp.c_str(), sizeof(sa.sun_path) - 1);
    if (::connect(fd_, reinterpret_cast<sockaddr*>(&sa), sizeof(sa)) < 0) {
        int e = errno;
        ::close(fd_);
        fd_ = -1;
        throw std::runtime_error("dbus connect " + sp + ": " + std::strerror(e) +
                                 " (is dbus / bluetoothd running?)");
    }
    char nul = 0;
    write_line(fd_, std::string(&nul, 1));
    write_line(fd_, "AUTH EXTERNAL " + hex(std::to_string(::getuid())) + "\r\n");
    std::string r = read_line(fd_);
    if (r.rfind("OK", 0) != 0) throw std::runtime_error("dbus auth rejected: " + r);
    write_line(fd_, "NEGOTIATE_UNIX_FD\r\n");
    r = read_line(fd_);
    if (r != "AGREE_UNIX_FD") throw std::runtime_error("dbus: bus does not pass unix fds: " + r);
    write_line(fd_, "BEGIN\r\n");
    Message hello = call("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus", "Hello");
    if (!hello.body.empty()) unique_ = hello.body[0].s;
}

Connection::~Connection() {
    if (fd_ >= 0) ::close(fd_);
    for (int f : in_fds_) ::close(f);
}

void Connection::send(Message& m) {
    m.serial = next_serial_++;
    std::vector<uint8_t> data = encode(m);
    size_t off = 0;
    bool fds_sent = m.fds.empty();
    while (off < data.size()) {
        iovec iov{data.data() + off, data.size() - off};
        msghdr msg{};
        msg.msg_iov = &iov;
        msg.msg_iovlen = 1;
        std::vector<char> ctrl;
        if (!fds_sent) {
            ctrl.assign(CMSG_SPACE(sizeof(int) * m.fds.size()), 0);
            msg.msg_control = ctrl.data();
            msg.msg_controllen = ctrl.size();
            cmsghdr* c = CMSG_FIRSTHDR(&msg);
            c->cmsg_level = SOL_SOCKET;
            c->cmsg_type = SCM_RIGHTS;
            c->cmsg_len = CMSG_LEN(sizeof(int) * m.fds.size());
            std::memcpy(CMSG_DATA(c), m.fds.data(), sizeof(int) * m.fds.size());
        }
        ssize_t r = ::sendmsg(fd_, &msg, MSG_NOSIGNAL);
        if (r < 0) {
            if (errno == EINTR) continue;
            throw std::runtime_error(std::string("dbus send: ") + std::strerror(errno));
        }
        fds_sent = true;
        off += size_t(r);
    }
}

bool Connection::fill(int timeout_ms) {
    pollfd p{fd_, POLLIN, 0};
    int r = ::poll(&p, 1, timeout_ms);
    if (r < 0) {
        if (errno == EINTR) return true;
        throw std::runtime_error(std::string("dbus poll: ") + std::strerror(errno));
    }
    if (r == 0) return true;  // timeout, nothing new
    uint8_t buf[16384];
    char ctrl[CMSG_SPACE(sizeof(int) * 16)];
    iovec iov{buf, sizeof(buf)};
    msghdr msg{};
    msg.msg_iov = &iov;
    msg.msg_iovlen = 1;
    msg.msg_control = ctrl;
    msg.msg_controllen = sizeof(ctrl);
    ssize_t n = ::recvmsg(fd_, &msg, MSG_CMSG_CLOEXEC);
    if (n < 0) {
        if (errno == EINTR || errno == EAGAIN) return true;
        throw std::runtime_error(std::string("dbus recv: ") + std::strerror(errno));
    }
    if (n == 0) return false;
    for (cmsghdr* c = CMSG_FIRSTHDR(&msg); c; c = CMSG_NXTHDR(&msg, c)) {
        if (c->cmsg_level != SOL_SOCKET || c->cmsg_type != SCM_RIGHTS) continue;
        size_t k = (c->cmsg_len - CMSG_LEN(0)) / sizeof(int);
        const int* fds = reinterpret_cast<const int*>(CMSG_DATA(c));
        for (size_t i = 0; i < k; ++i) in_fds_.push_back(fds[i]);
    }
    in_.insert(in_.end(), buf, buf + n);
    return true;
}

bool Connection::read_message(Message& out, int timeout_ms) {
    auto deadline = std::chrono::steady_clock::now() + std::chrono::milliseconds(timeout_ms);
    for (;;) {
        if (in_.size() >= 16) {
            if (in_[0] != 'l') throw std::runtime_error("dbus: big-endian messages are not supported");
            uint32_t body_len, fields_len;
            std::memcpy(&body_len, &in_[4], 4);
            std::memcpy(&fields_len, &in_[12], 4);
            size_t hdr = 16 + fields_len;
            size_t total = ((hdr + 7) & ~size_t(7)) + body_len;
            if (in_.size() >= total) {
                Reader r{in_.data(), total};
                r.i = 1;
                out = Message{};
                out.type = r.u8();
                out.flags = r.u8();
                r.u8();
                r.u32();
                out.serial = r.u32();
                uint32_t nfds = 0;
                Val fields = r.value("a(yv)");
                for (const auto& f : fields.v) {
                    const Val& x = f.v.at(1).v.at(0);
                    switch (f.v.at(0).u) {
                        case 1: out.path = x.s; break;
                        case 2: out.interface = x.s; break;
                        case 3: out.member = x.s; break;
                        case 4: out.error_name = x.s; break;
                        case 5: out.reply_serial = uint32_t(x.u); break;
                        case 6: out.destination = x.s; break;
                        case 7: out.sender = x.s; break;
                        case 8: out.signature = x.s; break;
                        case 9: nfds = uint32_t(x.u); break;
                    }
                }
                r.align(8);
                for (size_t k = 0; k < out.signature.size();) {
                    size_t len = type_len(out.signature, k);
                    out.body.push_back(r.value(out.signature.substr(k, len)));
                    k += len;
                }
                for (uint32_t k = 0; k < nfds && !in_fds_.empty(); ++k) {
                    out.fds.push_back(in_fds_.front());
                    in_fds_.pop_front();
                }
                in_.erase(in_.begin(), in_.begin() + long(total));
                return true;
            }
        }
        int left = int(std::chrono::duration_cast<std::chrono::milliseconds>(
                           deadline - std::chrono::steady_clock::now())
                           .count());
        if (left <= 0) return false;
        if (!fill(left)) throw std::runtime_error("dbus: the bus closed the connection");
    }
}

Message Connection::call(const std::string& dest, const std::string& p, const std::string& iface,
                         const std::string& member, std::vector<Val> args, int timeout_ms) {
    Message m;
    m.type = 1;
    m.destination = dest;
    m.path = p;
    m.interface = iface;
    m.member = member;
    m.body = std::move(args);
    send(m);
    const uint32_t serial = m.serial;
    auto deadline = std::chrono::steady_clock::now() + std::chrono::milliseconds(timeout_ms);
    for (;;) {
        int left = int(std::chrono::duration_cast<std::chrono::milliseconds>(
                           deadline - std::chrono::steady_clock::now())
                           .count());
        Message in;
        if (left <= 0 || !read_message(in, left))
            throw std::runtime_error("dbus: no answer to " + iface + "." + member);
        if ((in.type == 2 || in.type == 3) && in.reply_serial == serial) {
            if (in.type == 3) {
                for (int f : in.fds) ::close(f);
                std::string text = !in.body.empty() && in.body[0].t == 's' ? in.body[0].s : "";
                throw std::runtime_error(iface + "." + member + ": " + in.error_name +
                                         (text.empty() ? "" : " (" + text + ")"));
            }
            return in;
        }
        // BlueZ calls back into us (GetManagedObjects) before answering RegisterApplication:
        // serve those calls right away, keep anything else for process().
        if (in.type == 1 && on_call_) on_call_(in);
        else pending_.push_back(std::move(in));
    }
}

void Connection::reply(const Message& to, std::vector<Val> args, std::vector<int> fds) {
    if (to.flags & 1) return;  // NO_REPLY_EXPECTED
    Message m;
    m.type = 2;
    m.reply_serial = to.serial;
    m.destination = to.sender;
    m.body = std::move(args);
    m.fds = std::move(fds);
    send(m);
}

void Connection::error(const Message& to, const std::string& name, const std::string& text) {
    if (to.flags & 1) return;
    Message m;
    m.type = 3;
    m.reply_serial = to.serial;
    m.destination = to.sender;
    m.error_name = name;
    m.body.push_back(str(text));
    send(m);
}

void Connection::signal(const std::string& p, const std::string& iface, const std::string& member,
                        std::vector<Val> args) {
    Message m;
    m.type = 4;
    m.path = p;
    m.interface = iface;
    m.member = member;
    m.body = std::move(args);
    send(m);
}

bool Connection::process(int timeout_ms) {
    while (!pending_.empty()) {
        Message m = std::move(pending_.front());
        pending_.pop_front();
        if (m.type == 1 && on_call_) on_call_(m);
        else
            for (int f : m.fds) ::close(f);
    }
    Message m;
    try {
        if (!read_message(m, timeout_ms)) return true;
    } catch (const std::runtime_error& e) {
        if (std::string(e.what()).find("closed") != std::string::npos) return false;
        throw;
    }
    if (m.type == 1 && on_call_) on_call_(m);
    else
        for (int f : m.fds) ::close(f);
    return true;
}

}  // namespace ctt::dbus
