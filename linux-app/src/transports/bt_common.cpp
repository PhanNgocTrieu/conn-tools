#include "bt_common.hpp"

#include <sys/ioctl.h>
#include <unistd.h>

#include <cerrno>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <stdexcept>

#include "ctt/json.hpp"

namespace ctt::bt {
namespace {

// <bluetooth/hci.h>
struct HciDevStats {
    uint32_t err_rx, err_tx, cmd_tx, evt_rx, acl_tx, acl_rx, sco_tx, sco_rx, byte_rx, byte_tx;
};
struct HciDevInfo {
    uint16_t dev_id;
    char name[8];
    Bdaddr bdaddr;
    uint32_t flags;
    uint8_t type;
    uint8_t features[8];
    uint32_t pkt_type;
    uint32_t link_policy;
    uint32_t link_mode;
    uint16_t acl_mtu;
    uint16_t acl_pkts;
    uint16_t sco_mtu;
    uint16_t sco_pkts;
    HciDevStats stat;
};
static_assert(offsetof(HciDevInfo, flags) == 16);
static_assert(offsetof(HciDevInfo, acl_mtu) == 44);
static_assert(sizeof(HciDevInfo) == 92);

constexpr unsigned long kHciGetDevInfo = _IOR('H', 211, int);
constexpr uint32_t kHciUp = 1u << 0;
constexpr int kMaxAdapters = 16;

}  // namespace

bool Bdaddr::is_any() const {
    for (uint8_t v : b)
        if (v) return false;
    return true;
}

bool parse_bdaddr(std::string_view s, Bdaddr& out) {
    if (s.size() != 17) return false;
    for (int i = 0; i < 6; ++i) {
        if (i < 5 && s[size_t(i * 3 + 2)] != ':') return false;
        char hex[3] = {s[size_t(i * 3)], s[size_t(i * 3 + 1)], 0};
        char* end = nullptr;
        long v = std::strtol(hex, &end, 16);
        if (end != hex + 2) return false;
        out.b[5 - i] = uint8_t(v);
    }
    return true;
}

std::string to_string(const Bdaddr& a) {
    char buf[18];
    std::snprintf(buf, sizeof(buf), "%02X:%02X:%02X:%02X:%02X:%02X", a.b[5], a.b[4], a.b[3],
                  a.b[2], a.b[1], a.b[0]);
    return buf;
}

int parse_security(std::string_view s) {
    if (s == "sdp") return 0;
    if (s == "low") return 1;
    if (s == "medium") return 2;
    if (s == "high") return 3;
    if (s == "fips") return 4;
    return -1;
}

const char* security_name(int level) {
    switch (level) {
        case 0: return "sdp";
        case 1: return "low";
        case 2: return "medium";
        case 3: return "high";
        case 4: return "fips";
    }
    return "?";
}

void set_security(int fd, int level) {
    BtSecurityOpt s{uint8_t(level), 0};
    if (setsockopt(fd, kSolBluetooth, kBtSecurity, &s, sizeof(s)) < 0)
        throw std::runtime_error(std::string("BT_SECURITY: ") + std::strerror(errno));
}

int get_security(int fd) {
    BtSecurityOpt s{};
    socklen_t len = sizeof(s);
    if (getsockopt(fd, kSolBluetooth, kBtSecurity, &s, &len) < 0) return -1;
    return s.level;
}

bool adapter_info(int dev_id, AdapterInfo& out) {
    int fd = ::socket(kAfBluetooth, SOCK_RAW | SOCK_CLOEXEC, kProtoHci);
    if (fd < 0) return false;
    HciDevInfo di{};
    di.dev_id = uint16_t(dev_id);
    bool ok = ::ioctl(fd, kHciGetDevInfo, &di) == 0;
    ::close(fd);
    if (!ok) return false;
    out.dev_id = dev_id;
    out.name.assign(di.name, strnlen(di.name, sizeof(di.name)));
    out.addr = di.bdaddr;
    out.up = di.flags & kHciUp;
    out.acl_mtu = di.acl_mtu;
    out.acl_pkts = di.acl_pkts;
    out.byte_rx = di.stat.byte_rx;
    out.byte_tx = di.stat.byte_tx;
    return true;
}

bool default_adapter(AdapterInfo& out) {
    bool found = false;
    for (int i = 0; i < kMaxAdapters; ++i) {
        AdapterInfo a;
        if (!adapter_info(i, a)) continue;
        if (a.up) {
            out = a;
            return true;
        }
        if (!found) {
            out = a;
            found = true;
        }
    }
    return found;
}

bool find_adapter(std::string_view spec, AdapterInfo& out) {
    Bdaddr want;
    bool by_addr = parse_bdaddr(spec, want);
    for (int i = 0; i < kMaxAdapters; ++i) {
        AdapterInfo a;
        if (!adapter_info(i, a)) continue;
        if (by_addr ? std::memcmp(a.addr.b, want.b, 6) == 0 : a.name == spec) {
            out = a;
            return true;
        }
    }
    return false;
}

Bdaddr local_bdaddr(const TransportOptions& o) {
    std::string spec = opt(o, "adapter");
    Bdaddr a;
    if (spec.empty()) return a;  // BDADDR_ANY
    if (parse_bdaddr(spec, a)) return a;
    AdapterInfo info;
    if (!find_adapter(spec, info)) throw std::runtime_error("adapter not found: " + spec);
    return info.addr;
}

void split_endpoint(const std::string& ep, std::string& addr, std::string& num) {
    auto slash = ep.rfind('/');
    if (slash == std::string::npos) {
        addr = ep;
        num.clear();
    } else {
        addr = ep.substr(0, slash);
        num = ep.substr(slash + 1);
    }
}

uint32_t parse_number(const std::string& s, const char* what) {
    char* end = nullptr;
    errno = 0;
    unsigned long v = std::strtoul(s.c_str(), &end, 0);
    if (s.empty() || *end || errno) throw std::runtime_error(std::string("invalid ") + what + ": " + s);
    return uint32_t(v);
}

std::string opt(const TransportOptions& o, const char* key) {
    auto it = o.extra.find(key);
    return it == o.extra.end() ? std::string() : it->second;
}

void adapter_json(Json& j, const Bdaddr& local) {
    AdapterInfo a;
    bool ok = local.is_any() ? default_adapter(a) : find_adapter(to_string(local), a);
    if (!ok) return;
    j.add("adapter", a.name)
        .add("adapter_addr", to_string(a.addr))
        .add("acl_mtu", a.acl_mtu)
        .add("acl_pkts", a.acl_pkts)
        .add("hci_byte_rx", a.byte_rx)
        .add("hci_byte_tx", a.byte_tx);
}

}  // namespace ctt::bt
