// Pure USB descriptor helpers (no I/O), unit-testable without hardware.
#pragma once

#include <cstdint>
#include <string>
#include <vector>

namespace ctt::usb {

// Default IDs used by agent/scripts/dut-usb-gadget.sh (Linux Foundation,
// "Multifunction Composite Gadget") - fine for lab use, not for products.
constexpr uint16_t kDefaultVid = 0x1d6b;
constexpr uint16_t kDefaultPid = 0x0104;

// FunctionFS v2 descriptor blob: one vendor-class interface with bulk IN
// (ep1, 0x81) and bulk OUT (ep2, 0x02) for full/high/super speed.
std::vector<uint8_t> ffs_descriptors();
// FunctionFS strings blob (en-US, one string: the interface name).
std::vector<uint8_t> ffs_strings(const std::string& interface_name);

struct BulkPair {
    int interface = -1;
    uint8_t ep_in = 0, ep_out = 0;
    uint16_t mps_in = 0, mps_out = 0;
};

// Parses the raw descriptors read from /dev/bus/usb/BBB/DDD (device + config
// descriptors) and returns the bulk IN/OUT pair of `interface` (alt 0), or of
// the first vendor-specific interface having both when interface < 0.
bool find_bulk_pair(const std::vector<uint8_t>& raw, int interface, BulkPair& out);

struct DeviceRef {
    std::string sysname;  // e.g. "1-2"
    int busnum = 0, devnum = 0;
    std::string devnode() const;  // /dev/bus/usb/001/005
};

// Devices with the given VID:PID under `sysfs_root` (default /sys/bus/usb/devices).
std::vector<DeviceRef> find_devices(uint16_t vid, uint16_t pid,
                                    const std::string& sysfs_root = "/sys/bus/usb/devices");

// "1d6b:0104", "1d6b:0104/1", "/dev/bus/usb/001/005", "/dev/bus/usb/001/005/1"
struct HostEndpoint {
    std::string devnode;   // set for /dev/bus/usb form
    uint16_t vid = 0, pid = 0;
    int interface = -1;
};
HostEndpoint parse_host_endpoint(const std::string& ep);  // throws

const char* speed_name(int usbdevfs_speed);

}  // namespace ctt::usb
