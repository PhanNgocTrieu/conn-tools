// USB bulk transport.
//
// listen()  = DEVICE side (the DUT as a USB gadget) via FunctionFS:
//   endpoint: FunctionFS mount directory (default /dev/ffs-ctt), created by
//             agent/scripts/dut-usb-gadget.sh (ctt-usb-gadget on the board)
//   -o gadget=/sys/kernel/config/usb_gadget/ctt -o udc=auto|<udc name>
//             bind the gadget to the UDC once descriptors are written
// connect() = HOST side via usbdevfs (no libusb needed):
//   endpoint: VID:PID[/IF] (default 1d6b:0104) or /dev/bus/usb/BBB/DDD[/IF]
//
// Both sides speak plain CTP over one bulk IN + one bulk OUT endpoint.
#include <dirent.h>
#include <fcntl.h>
#include <linux/usb/functionfs.h>
#include <linux/usbdevice_fs.h>
#include <poll.h>
#include <sys/ioctl.h>
#include <unistd.h>

#include <algorithm>
#include <cerrno>
#include <cstring>
#include <fstream>
#include <memory>
#include <stdexcept>
#include <vector>

#include "bt_common.hpp"  // opt()
#include "ctt/json.hpp"
#include "fd_stream.hpp"
#include "usb_desc.hpp"

namespace ctt {
namespace {

using bt::opt;

std::string errstr(const std::string& what) { return what + ": " + std::strerror(errno); }

void write_all_fd(int fd, const std::vector<uint8_t>& v, const char* what) {
    ssize_t w = ::write(fd, v.data(), v.size());
    if (w != ssize_t(v.size())) throw std::runtime_error(errstr(what));
}

// ---- device side (FunctionFS) -------------------------------------------------

struct FfsEndpoints {
    int ep0 = -1, ep_in = -1, ep_out = -1;  // ep1 = IN (to host), ep2 = OUT (from host)
    ~FfsEndpoints() {
        for (int fd : {ep_in, ep_out, ep0})
            if (fd >= 0) ::close(fd);
    }
};

class FfsStream : public Stream {
public:
    explicit FfsStream(std::shared_ptr<FfsEndpoints> e) : e_(std::move(e)) {}

    ssize_t read_some(uint8_t* buf, size_t len) override {
        for (;;) {
            ssize_t r = ::read(e_->ep_out, buf, len);
            if (r >= 0) return r;
            if (errno == EINTR) continue;
            if (errno == ESHUTDOWN) return 0;  // host disabled the function: EOF
            throw std::runtime_error(errstr("ffs read"));
        }
    }
    ssize_t writev_some(const struct iovec* iov, int cnt) override {
        // One USB transfer per frame: gather header + payload.
        size_t total = 0;
        for (int i = 0; i < cnt; ++i) total += iov[i].iov_len;
        buf_.resize(total);
        size_t off = 0;
        for (int i = 0; i < cnt; ++i) {
            std::memcpy(buf_.data() + off, iov[i].iov_base, iov[i].iov_len);
            off += iov[i].iov_len;
        }
        for (;;) {
            ssize_t w = ::write(e_->ep_in, buf_.data(), total);
            if (w >= 0) return w;
            if (errno == EINTR) continue;
            throw std::runtime_error(errstr("ffs write"));
        }
    }
    void shutdown_write() override {}
    std::string peer() const override { return "usb-host"; }
    void link_info(Json& j) const override { j.add("usb_role", "device (FunctionFS)"); }

private:
    std::shared_ptr<FfsEndpoints> e_;
    std::vector<uint8_t> buf_;
};

class FfsListener : public Listener {
public:
    FfsListener(std::string dir, const TransportOptions& o) : dir_(std::move(dir)) {
        e_ = std::make_shared<FfsEndpoints>();
        e_->ep0 = ::open((dir_ + "/ep0").c_str(), O_RDWR | O_CLOEXEC);
        if (e_->ep0 < 0)
            throw std::runtime_error(errstr("open " + dir_ + "/ep0") +
                                     " (FunctionFS mounted? run ctt-usb-gadget up / ctt usb gadget up)");
        write_all_fd(e_->ep0, usb::ffs_descriptors(), "write FunctionFS descriptors");
        write_all_fd(e_->ep0, usb::ffs_strings("CTT bulk"), "write FunctionFS strings");

        gadget_ = opt(o, "gadget");
        std::string udc = opt(o, "udc");
        if (!udc.empty()) {
            if (gadget_.empty()) gadget_ = "/sys/kernel/config/usb_gadget/ctt";
            if (udc == "auto") udc = first_udc();
            std::ofstream f(gadget_ + "/UDC");
            f << udc << "\n";
            f.close();
            if (!f) throw std::runtime_error("cannot bind UDC '" + udc + "' via " + gadget_ + "/UDC");
            bound_udc_ = udc;
        }
    }
    ~FfsListener() override {
        if (!bound_udc_.empty()) {
            std::ofstream f(gadget_ + "/UDC");
            f << "\n";
        }
    }

    std::unique_ptr<Stream> accept(int timeout_ms) override {
        if (enabled_) return std::make_unique<FfsStream>(e_);
        // Wait for the host to configure us (FUNCTIONFS_ENABLE on ep0).
        uint64_t waited = 0;
        while (!enabled_) {
            pollfd p{e_->ep0, POLLIN, 0};
            int step = timeout_ms < 0 ? 1000 : std::min(1000, timeout_ms - int(waited));
            if (timeout_ms >= 0 && step <= 0) return nullptr;
            int r = ::poll(&p, 1, step);
            if (r < 0 && errno != EINTR) throw std::runtime_error(errstr("poll ep0"));
            waited += uint64_t(step);
            if (r <= 0) continue;
            usb_functionfs_event ev[4];
            ssize_t n = ::read(e_->ep0, ev, sizeof(ev));
            if (n < 0) {
                if (errno == EINTR || errno == EAGAIN) continue;
                throw std::runtime_error(errstr("read ep0"));
            }
            for (size_t i = 0; i < size_t(n) / sizeof(ev[0]); ++i) {
                if (ev[i].type == FUNCTIONFS_ENABLE) enabled_ = true;
                else if (ev[i].type == FUNCTIONFS_SETUP) stall_setup();
            }
        }
        if (e_->ep_in < 0) {
            e_->ep_in = ::open((dir_ + "/ep1").c_str(), O_RDWR | O_CLOEXEC);
            e_->ep_out = ::open((dir_ + "/ep2").c_str(), O_RDWR | O_CLOEXEC);
            if (e_->ep_in < 0 || e_->ep_out < 0) throw std::runtime_error(errstr("open ep1/ep2"));
        }
        return std::make_unique<FfsStream>(e_);
    }

    std::string local() const override { return dir_; }
    void describe(Json& j) const override {
        if (!bound_udc_.empty()) j.add("udc", bound_udc_);
        char b[16];
        std::snprintf(b, sizeof(b), "%04x:%04x", usb::kDefaultVid, usb::kDefaultPid);
        j.add("usb_id_hint", b);
    }

private:
    // We define no control requests: answer any SETUP with a stall.
    void stall_setup() {
        uint8_t dummy;
        if (::read(e_->ep0, &dummy, 0) < 0) { /* reading 0 bytes stalls an IN request */ }
    }

    static std::string first_udc() {
        DIR* d = opendir("/sys/class/udc");
        if (!d) throw std::runtime_error("no /sys/class/udc: kernel without USB device controller");
        std::string name;
        while (dirent* e = readdir(d))
            if (e->d_name[0] != '.') {
                name = e->d_name;
                break;
            }
        closedir(d);
        if (name.empty()) throw std::runtime_error("no USB device controller (UDC) found");
        return name;
    }

    std::string dir_;
    std::shared_ptr<FfsEndpoints> e_;
    bool enabled_ = false;
    std::string gadget_, bound_udc_;
};

// ---- host side (usbdevfs) ---------------------------------------------------------

class UsbHostStream : public Stream {
public:
    UsbHostStream(int fd, std::string name, usb::BulkPair bp, unsigned timeout_ms)
        : fd_(fd), name_(std::move(name)), bp_(bp), timeout_ms_(timeout_ms ? timeout_ms : 10000),
          bounce_(64 * 1024) {}
    ~UsbHostStream() override {
        unsigned ifn = unsigned(bp_.interface);
        ::ioctl(fd_, USBDEVFS_RELEASEINTERFACE, &ifn);
        ::close(fd_);
    }

    ssize_t read_some(uint8_t* buf, size_t len) override {
        if (pend_off_ < pend_len_) {
            size_t n = std::min(len, pend_len_ - pend_off_);
            std::memcpy(buf, bounce_.data() + pend_off_, n);
            pend_off_ += n;
            return ssize_t(n);
        }
        size_t mps = bp_.mps_in ? bp_.mps_in : 512;
        size_t want = std::min<size_t>(len, kMaxXfer) / mps * mps;  // whole packets only
        if (want >= 16 * 1024) return bulk(bp_.ep_in, buf, want);
        ssize_t r = bulk(bp_.ep_in, bounce_.data(), bounce_.size() / mps * mps);
        pend_off_ = 0;
        pend_len_ = size_t(r);
        return read_some(buf, len);
    }

    ssize_t writev_some(const struct iovec* iov, int cnt) override {
        size_t total = 0;
        for (int i = 0; i < cnt; ++i) total += iov[i].iov_len;
        total = std::min(total, kMaxXfer);
        out_.resize(total);
        size_t off = 0;
        for (int i = 0; i < cnt && off < total; ++i) {
            size_t n = std::min(iov[i].iov_len, total - off);
            std::memcpy(out_.data() + off, iov[i].iov_base, n);
            off += n;
        }
        return bulk(bp_.ep_out, out_.data(), total);
    }

    void shutdown_write() override {}
    std::string peer() const override { return name_; }

    void link_info(Json& j) const override {
        int sp = ::ioctl(fd_, USBDEVFS_GET_SPEED, nullptr);
        j.add("usb_role", "host (usbdevfs)")
            .add("usb_device", name_)
            .add("usb_interface", bp_.interface)
            .add("usb_mps_in", bp_.mps_in)
            .add("usb_mps_out", bp_.mps_out);
        if (sp >= 0) j.add("usb_speed", usb::speed_name(sp));
    }

private:
    static constexpr size_t kMaxXfer = 256 * 1024;

    ssize_t bulk(uint8_t ep, uint8_t* data, size_t len) {
        usbdevfs_bulktransfer bt{};
        bt.ep = ep;
        bt.len = unsigned(len);
        bt.timeout = timeout_ms_;
        bt.data = data;
        for (;;) {
            int r = ::ioctl(fd_, USBDEVFS_BULK, &bt);
            if (r >= 0) return r;
            if (errno == EINTR) continue;
            if (errno == ETIMEDOUT) throw TimeoutError("usb bulk transfer timed out");
            if (errno == ENODEV) throw std::runtime_error("usb device disconnected");
            throw std::runtime_error(errstr("usb bulk ep 0x" + std::to_string(ep)));
        }
    }

    int fd_;
    std::string name_;
    usb::BulkPair bp_;
    unsigned timeout_ms_;
    std::vector<uint8_t> bounce_, out_;
    size_t pend_off_ = 0, pend_len_ = 0;
};

std::unique_ptr<Stream> open_host(const std::string& ep, const TransportOptions& o) {
    usb::HostEndpoint h = usb::parse_host_endpoint(ep.empty() ? "1d6b:0104" : ep);
    std::string node = h.devnode;
    if (node.empty()) {
        auto devs = usb::find_devices(h.vid, h.pid);
        if (devs.empty())
            throw std::runtime_error("usb device " + ep +
                                     " not found (attached to WSL with usbipd? gadget bound on the DUT?)");
        if (devs.size() > 1) throw std::runtime_error("several devices match " + ep + ": use /dev/bus/usb/BBB/DDD");
        node = devs[0].devnode();
    }
    int fd = ::open(node.c_str(), O_RDWR | O_CLOEXEC);
    if (fd < 0)
        throw std::runtime_error(errstr("open " + node) +
                                 (errno == EACCES ? " (add a udev rule or run as root; see docs/usb.md)" : ""));
    std::vector<uint8_t> raw(4096);
    ssize_t n = ::read(fd, raw.data(), raw.size());
    raw.resize(n > 0 ? size_t(n) : 0);
    usb::BulkPair bp;
    if (!usb::find_bulk_pair(raw, h.interface, bp)) {
        ::close(fd);
        throw std::runtime_error("no bulk IN/OUT interface on " + node);
    }
    usbdevfs_ioctl cmd{bp.interface, USBDEVFS_DISCONNECT, nullptr};
    ::ioctl(fd, USBDEVFS_IOCTL, &cmd);  // detach a kernel driver if any (ENODATA if none)
    unsigned ifn = unsigned(bp.interface);
    if (::ioctl(fd, USBDEVFS_CLAIMINTERFACE, &ifn) < 0) {
        std::string e = errstr("claim interface " + std::to_string(ifn));
        ::close(fd);
        throw std::runtime_error(e);
    }
    return std::make_unique<UsbHostStream>(fd, node + "/" + std::to_string(bp.interface), bp,
                                           unsigned(std::max(o.timeout_ms, 0)));
}

class UsbTransport : public Transport {
public:
    const char* name() const override { return "usb"; }
    const char* description() const override {
        return "USB bulk. Server = gadget (FunctionFS dir), client = host (VID:PID or /dev/bus/usb/..)";
    }
    std::string default_listen_endpoint() const override { return "/dev/ffs-ctt"; }
    uint32_t default_block() const override { return 64 * 1024; }

    std::unique_ptr<Listener> listen(const std::string& ep, const TransportOptions& o) override {
        return std::make_unique<FfsListener>(ep.empty() ? default_listen_endpoint() : ep, o);
    }
    std::unique_ptr<Stream> connect(const std::string& ep, const TransportOptions& o) override {
        return open_host(ep, o);
    }
};

}  // namespace

std::unique_ptr<Transport> make_usb_transport() { return std::make_unique<UsbTransport>(); }

}  // namespace ctt
