#include "usb_desc.hpp"

#include <dirent.h>

#include <cstdio>
#include <cstdlib>
#include <fstream>
#include <stdexcept>

#include "ctt/byteorder.hpp"

namespace ctt::usb {
namespace {

// <linux/usb/functionfs.h>
constexpr uint32_t kDescMagicV2 = 3;
constexpr uint32_t kStringsMagic = 2;
constexpr uint32_t kHasFs = 1, kHasHs = 2, kHasSs = 4;

void put16(std::vector<uint8_t>& v, uint16_t x) {
    v.push_back(uint8_t(x));
    v.push_back(uint8_t(x >> 8));
}
void put32(std::vector<uint8_t>& v, uint32_t x) {
    for (int i = 0; i < 4; ++i) v.push_back(uint8_t(x >> (8 * i)));
}

void interface_desc(std::vector<uint8_t>& v) {
    // bLength, INTERFACE, bInterfaceNumber, bAlternateSetting, bNumEndpoints,
    // class vendor-specific, subclass, protocol, iInterface (string 1)
    v.insert(v.end(), {9, 4, 0, 0, 2, 0xFF, 0, 0, 1});
}

void endpoint_desc(std::vector<uint8_t>& v, uint8_t addr, uint16_t mps) {
    v.insert(v.end(), {7, 5, addr, 0x02 /* bulk */});
    put16(v, mps);
    v.push_back(0);  // bInterval
}

void ss_companion(std::vector<uint8_t>& v) {
    // bLength, SS_ENDPOINT_COMP, bMaxBurst (n+1 packets), bmAttributes, wBytesPerInterval
    v.insert(v.end(), {6, 0x30, 15, 0});
    put16(v, 0);
}

}  // namespace

std::vector<uint8_t> ffs_descriptors() {
    std::vector<uint8_t> fs, hs, ss;
    interface_desc(fs);
    endpoint_desc(fs, 0x81, 64);
    endpoint_desc(fs, 0x02, 64);
    interface_desc(hs);
    endpoint_desc(hs, 0x81, 512);
    endpoint_desc(hs, 0x02, 512);
    interface_desc(ss);
    endpoint_desc(ss, 0x81, 1024);
    ss_companion(ss);
    endpoint_desc(ss, 0x02, 1024);
    ss_companion(ss);

    std::vector<uint8_t> v;
    put32(v, kDescMagicV2);
    put32(v, 0);  // length, patched below
    put32(v, kHasFs | kHasHs | kHasSs);
    put32(v, 3);  // fs_count: interface + 2 endpoints
    put32(v, 3);  // hs_count
    put32(v, 5);  // ss_count: interface + 2 x (endpoint + companion)
    v.insert(v.end(), fs.begin(), fs.end());
    v.insert(v.end(), hs.begin(), hs.end());
    v.insert(v.end(), ss.begin(), ss.end());
    put_le32(v.data() + 4, uint32_t(v.size()));
    return v;
}

std::vector<uint8_t> ffs_strings(const std::string& name) {
    std::vector<uint8_t> v;
    put32(v, kStringsMagic);
    put32(v, 0);
    put32(v, 1);  // str_count
    put32(v, 1);  // lang_count
    put16(v, 0x0409);
    v.insert(v.end(), name.begin(), name.end());
    v.push_back(0);
    put_le32(v.data() + 4, uint32_t(v.size()));
    return v;
}

bool find_bulk_pair(const std::vector<uint8_t>& raw, int interface, BulkPair& out) {
    size_t i = 0;
    int cur_if = -1, cur_alt = -1, cur_class = -1;
    BulkPair cand;
    auto done = [&]() {
        return cand.interface >= 0 && cand.ep_in && cand.ep_out;
    };
    while (i + 2 <= raw.size()) {
        uint8_t len = raw[i], type = raw[i + 1];
        if (len < 2 || i + len > raw.size()) break;
        if (type == 2 && i > 0 && done()) break;  // next configuration: stop
        if (type == 4 && len >= 9) {              // interface
            if (done()) break;
            cur_if = raw[i + 2];
            cur_alt = raw[i + 3];
            cur_class = raw[i + 5];
            cand = BulkPair{};
            bool want = cur_alt == 0 && (interface >= 0 ? cur_if == interface : cur_class == 0xFF);
            if (want) cand.interface = cur_if;
        } else if (type == 5 && len >= 7 && cand.interface >= 0) {  // endpoint
            uint8_t addr = raw[i + 2], attr = raw[i + 3];
            uint16_t mps = uint16_t(get_le16(&raw[i + 4]) & 0x7FF);
            if ((attr & 3) == 2) {
                if ((addr & 0x80) && !cand.ep_in) {
                    cand.ep_in = addr;
                    cand.mps_in = mps;
                } else if (!(addr & 0x80) && !cand.ep_out) {
                    cand.ep_out = addr;
                    cand.mps_out = mps;
                }
            }
        }
        i += len;
    }
    if (!done()) return false;
    out = cand;
    return true;
}

std::string DeviceRef::devnode() const {
    char b[32];
    std::snprintf(b, sizeof(b), "/dev/bus/usb/%03d/%03d", busnum, devnum);
    return b;
}

namespace {
std::string read_attr(const std::string& path) {
    std::ifstream f(path);
    std::string s;
    std::getline(f, s);
    return s;
}
}  // namespace

std::vector<DeviceRef> find_devices(uint16_t vid, uint16_t pid, const std::string& root) {
    std::vector<DeviceRef> out;
    DIR* d = opendir(root.c_str());
    if (!d) return out;
    while (dirent* e = readdir(d)) {
        std::string name = e->d_name;
        if (name[0] == '.' || name.find(':') != std::string::npos) continue;  // skip interfaces
        std::string base = root + "/" + name + "/";
        std::string v = read_attr(base + "idVendor"), p = read_attr(base + "idProduct");
        if (v.empty() || p.empty()) continue;
        if (std::stoul(v, nullptr, 16) != vid || std::stoul(p, nullptr, 16) != pid) continue;
        DeviceRef r;
        r.sysname = name;
        r.busnum = std::atoi(read_attr(base + "busnum").c_str());
        r.devnum = std::atoi(read_attr(base + "devnum").c_str());
        out.push_back(r);
    }
    closedir(d);
    return out;
}

HostEndpoint parse_host_endpoint(const std::string& ep) {
    HostEndpoint h;
    std::string rest = ep;
    if (ep.rfind("/dev/bus/usb/", 0) == 0) {
        // /dev/bus/usb/BBB/DDD[/IF]
        int bus = 0, dev = 0, ifn = -1;
        int n = std::sscanf(ep.c_str(), "/dev/bus/usb/%d/%d/%d", &bus, &dev, &ifn);
        if (n < 2) throw std::runtime_error("bad usb endpoint: " + ep);
        char b[32];
        std::snprintf(b, sizeof(b), "/dev/bus/usb/%03d/%03d", bus, dev);
        h.devnode = b;
        h.interface = n == 3 ? ifn : -1;
        return h;
    }
    unsigned vid = 0, pid = 0;
    int ifn = -1;
    char extra = 0;
    int n = std::sscanf(ep.c_str(), "%x:%x/%d%c", &vid, &pid, &ifn, &extra);
    if (n < 2 || n == 4 || vid > 0xFFFF || pid > 0xFFFF)
        throw std::runtime_error("usb endpoint must be VID:PID[/IF] or /dev/bus/usb/BBB/DDD[/IF]: " + ep);
    h.vid = uint16_t(vid);
    h.pid = uint16_t(pid);
    h.interface = n >= 3 ? ifn : -1;
    return h;
}

const char* speed_name(int s) {
    switch (s) {
        case 1: return "low (1.5M)";
        case 2: return "full (12M)";
        case 3: return "high (480M)";
        case 4: return "wireless";
        case 5: return "super (5G)";
        case 6: return "super+ (10G+)";
    }
    return "unknown";
}

}  // namespace ctt::usb
