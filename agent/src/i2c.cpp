#include "ctt/i2c.hpp"

#include <fcntl.h>
#include <linux/i2c-dev.h>
#include <linux/i2c.h>
#include <sys/ioctl.h>
#include <unistd.h>

#include <cctype>
#include <cerrno>
#include <cstring>
#include <map>
#include <random>

#include "ctt/clock.hpp"

namespace ctt {

std::vector<uint8_t> parse_hex(const std::string& in) {
    std::string s;
    for (size_t i = 0; i < in.size(); ++i) {
        char c = in[i];
        if (c == '0' && i + 1 < in.size() && (in[i + 1] == 'x' || in[i + 1] == 'X')) {
            ++i;
            continue;
        }
        if (c == ':' || c == ' ' || c == '_') continue;
        if (!std::isxdigit(static_cast<unsigned char>(c)))
            throw std::runtime_error("invalid hex: " + in);
        s += c;
    }
    if (s.size() % 2) throw std::runtime_error("odd number of hex digits: " + in);
    std::vector<uint8_t> out(s.size() / 2);
    for (size_t i = 0; i < out.size(); ++i) out[i] = uint8_t(std::stoul(s.substr(2 * i, 2), nullptr, 16));
    return out;
}

std::string to_hex(const uint8_t* p, size_t n) {
    static const char* d = "0123456789abcdef";
    std::string s(2 * n, '0');
    for (size_t i = 0; i < n; ++i) {
        s[2 * i] = d[p[i] >> 4];
        s[2 * i + 1] = d[p[i] & 15];
    }
    return s;
}

// ---- Linux /dev/i2c-N ------------------------------------------------------

namespace {

class LinuxI2c : public I2cBus {
public:
    explicit LinuxI2c(std::string path) : path_(std::move(path)) {
        fd_ = ::open(path_.c_str(), O_RDWR | O_CLOEXEC);
        if (fd_ < 0)
            throw std::runtime_error("open " + path_ + ": " + std::strerror(errno) +
                                     (errno == ENOENT ? " (i2c-dev module loaded? bus number?)" : ""));
        unsigned long funcs = 0;
        if (::ioctl(fd_, I2C_FUNCS, &funcs) == 0 && !(funcs & I2C_FUNC_I2C))
            throw std::runtime_error(path_ + ": adapter does not support plain I2C transfers");
    }
    ~LinuxI2c() override { ::close(fd_); }

    void transfer(uint8_t addr, const uint8_t* w, size_t wlen, uint8_t* r, size_t rlen,
                  bool combined) override {
        i2c_msg msgs[2];
        int n = 0;
        if (wlen || !rlen) {
            msgs[n++] = i2c_msg{addr, 0, uint16_t(wlen), const_cast<uint8_t*>(w)};
        }
        if (rlen) {
            i2c_msg rd{addr, I2C_M_RD, uint16_t(rlen), r};
            if (combined || n == 0) {
                msgs[n++] = rd;
            } else {
                xfer(msgs, n);  // write, STOP
                n = 0;
                msgs[n++] = rd;  // separate read transaction
            }
        }
        xfer(msgs, n);
    }

    std::string describe() const override { return path_; }

private:
    void xfer(i2c_msg* msgs, int n) {
        i2c_rdwr_ioctl_data data{msgs, uint32_t(n)};
        for (;;) {
            if (::ioctl(fd_, I2C_RDWR, &data) >= 0) return;
            int e = errno;
            if (e == EINTR) continue;
            // Drivers report a missing ACK differently.
            if (e == ENXIO || e == EREMOTEIO || e == EIO || e == EAGAIN || e == ETIMEDOUT)
                throw I2cNack(std::string("NACK/no response: ") + std::strerror(e));
            throw std::runtime_error(std::string("I2C_RDWR: ") + std::strerror(e));
        }
    }

    std::string path_;
    int fd_ = -1;
};

// ---- simulated device ------------------------------------------------------

class FakeI2c : public I2cBus {
public:
    explicit FakeI2c(const std::string& spec) : spec_(spec) {
        size_t pos = 0;
        while (pos != std::string::npos) {
            size_t next = spec.find(',', pos);
            std::string kv = spec.substr(pos, next == std::string::npos ? next : next - pos);
            pos = next == std::string::npos ? next : next + 1;
            if (kv == "fake" || kv.empty()) continue;
            auto eq = kv.find('=');
            if (eq == std::string::npos) throw std::runtime_error("fake i2c option needs '=': " + kv);
            std::string k = kv.substr(0, eq), v = kv.substr(eq + 1);
            if (k == "nack") nack_ = std::stod(v);
            else if (k == "busy_ms") busy_ns_ = uint64_t(std::stod(v) * 1e6);
            else if (k == "seed") rng_.seed(uint32_t(std::stoul(v)));
            else if (k == "addr") addr_ = uint8_t(std::stoul(v, nullptr, 0));
            else if (k.size() == 3 && k[0] == 'r') regs_[uint8_t(std::stoul(k.substr(1), nullptr, 16))] = parse_hex(v);
            else throw std::runtime_error("unknown fake i2c option: " + k);
        }
    }

    void transfer(uint8_t addr, const uint8_t* w, size_t wlen, uint8_t* r, size_t rlen,
                  bool /*combined*/) override {
        if (addr != addr_) throw I2cNack("NACK (no device at this address)");
        uint64_t now = now_ns();
        if (now < busy_until_) throw I2cNack("NACK (busy)");
        if (nack_ > 0 && std::uniform_real_distribution<double>(0, 1)(rng_) < nack_)
            throw I2cNack("NACK (simulated)");
        if (wlen >= 1) {
            ptr_ = w[0];
            if (wlen > 1) {
                regs_[ptr_].assign(w + 1, w + wlen);
                if (busy_ns_) busy_until_ = now + busy_ns_;
            }
        }
        if (rlen) {
            const auto& v = regs_[ptr_];
            for (size_t i = 0; i < rlen; ++i) r[i] = i < v.size() ? v[i] : 0;
        }
    }

    std::string describe() const override {
        return "fake(" + std::to_string(regs_.size()) + " regs, nack=" + std::to_string(nack_).substr(0, 4) +
               ", busy_ms=" + std::to_string(busy_ns_ / 1000000) + ")";
    }

private:
    std::string spec_;
    std::map<uint8_t, std::vector<uint8_t>> regs_;
    uint8_t addr_ = 0x10;
    uint8_t ptr_ = 0;
    double nack_ = 0;
    uint64_t busy_ns_ = 0;
    uint64_t busy_until_ = 0;
    std::mt19937 rng_{12345};
};

}  // namespace

std::unique_ptr<I2cBus> open_linux_i2c(const std::string& spec) {
    std::string path = spec.rfind("/dev/", 0) == 0 ? spec : "/dev/i2c-" + spec;
    return std::make_unique<LinuxI2c>(path);
}

std::unique_ptr<I2cBus> open_fake_i2c(const std::string& spec) { return std::make_unique<FakeI2c>(spec); }

std::unique_ptr<I2cBus> open_i2c(const std::string& spec) {
    if (spec.rfind("fake", 0) == 0) return open_fake_i2c(spec);
    return open_linux_i2c(spec);
}

}  // namespace ctt
