// Linux Bluetooth socket ABI, defined here so the agent needs no libbluetooth
// (BlueZ userspace library) at build time or on the DUT - only the kernel
// AF_BLUETOOTH sockets. Values/layouts mirror <linux/bluetooth*.h> /
// BlueZ lib/{bluetooth,rfcomm,l2cap,hci}.h.
#pragma once

#include <sys/socket.h>

#include <cstddef>
#include <cstdint>
#include <string>
#include <string_view>

#include "ctt/transport.hpp"

namespace ctt::bt {

constexpr int kAfBluetooth = 31;
constexpr int kProtoL2cap = 0;
constexpr int kProtoHci = 1;
constexpr int kProtoRfcomm = 3;

constexpr int kSolBluetooth = 274;
constexpr int kSolL2cap = 6;
constexpr int kSolRfcomm = 18;

constexpr int kBtSecurity = 4;  // SOL_BLUETOOTH
constexpr int kBtSndMtu = 12;
constexpr int kBtRcvMtu = 13;
constexpr int kBtPhy = 14;

constexpr int kL2capOptions = 0x01;   // SOL_L2CAP
constexpr int kRfcommConnInfo = 0x02;  // SOL_RFCOMM

constexpr uint8_t kAddrBredr = 0;
constexpr uint8_t kAddrLePublic = 1;
constexpr uint8_t kAddrLeRandom = 2;

// bdaddr_t: 6 bytes, stored little-endian (reverse of the printed form).
struct Bdaddr {
    uint8_t b[6] = {0, 0, 0, 0, 0, 0};
    bool is_any() const;
};
static_assert(sizeof(Bdaddr) == 6 && alignof(Bdaddr) == 1);

// struct sockaddr_rc. NOT packed: the kernel's rfcomm connect() requires
// addrlen >= sizeof(struct sockaddr_rc) == 10; what matters is channel @ 8.
struct SockaddrRc {
    sa_family_t rc_family;
    Bdaddr rc_bdaddr;
    uint8_t rc_channel;
};
static_assert(sizeof(SockaddrRc) == 10);
static_assert(offsetof(SockaddrRc, rc_bdaddr) == 2);
static_assert(offsetof(SockaddrRc, rc_channel) == 8);

// struct sockaddr_l2 (psm / cid are little-endian on the wire)
struct SockaddrL2 {
    sa_family_t l2_family;
    uint16_t l2_psm;
    Bdaddr l2_bdaddr;
    uint16_t l2_cid;
    uint8_t l2_bdaddr_type;
};
static_assert(sizeof(SockaddrL2) == 14);
static_assert(offsetof(SockaddrL2, l2_bdaddr) == 4);
static_assert(offsetof(SockaddrL2, l2_cid) == 10);
static_assert(offsetof(SockaddrL2, l2_bdaddr_type) == 12);

struct BtSecurityOpt {
    uint8_t level;
    uint8_t key_size;
};

struct L2capOptionsOpt {
    uint16_t omtu;
    uint16_t imtu;
    uint16_t flush_to;
    uint8_t mode;
    uint8_t fcs;
    uint8_t max_tx;
    uint16_t txwin_size;
};

struct RfcommConnInfoOpt {
    uint16_t hci_handle;
    uint8_t dev_class[3];
};

// ---- helpers ----------------------------------------------------------------

bool parse_bdaddr(std::string_view s, Bdaddr& out);
std::string to_string(const Bdaddr& a);

// "low|medium|high|fips|sdp" -> BT_SECURITY level, -1 if invalid.
int parse_security(std::string_view s);
const char* security_name(int level);
void set_security(int fd, int level);  // throws
int get_security(int fd);              // -1 if unavailable

struct AdapterInfo {
    int dev_id = -1;
    std::string name;
    Bdaddr addr;
    bool up = false;
    uint16_t acl_mtu = 0;
    uint16_t acl_pkts = 0;
    uint32_t byte_rx = 0;
    uint32_t byte_tx = 0;
};
bool adapter_info(int dev_id, AdapterInfo& out);
// First adapter that is up (else first present). False if none.
bool default_adapter(AdapterInfo& out);
// "hci1" or "AA:BB:CC:DD:EE:FF". False if not found.
bool find_adapter(std::string_view spec, AdapterInfo& out);

// Source address for bind(): -o adapter=hciN|BDADDR, else BDADDR_ANY.
Bdaddr local_bdaddr(const TransportOptions& o);

// Splits "AA:BB:CC:DD:EE:FF/3" (or "/3", "hci0/3") at the last '/'.
void split_endpoint(const std::string& ep, std::string& addr, std::string& num);
uint32_t parse_number(const std::string& s, const char* what);  // dec or 0x-hex, throws

// Reads -o key=value, empty if absent.
std::string opt(const TransportOptions& o, const char* key);

// Adds adapter fields (name, address, acl_mtu, HCI byte counters).
void adapter_json(Json& j, const Bdaddr& local);

}  // namespace ctt::bt
