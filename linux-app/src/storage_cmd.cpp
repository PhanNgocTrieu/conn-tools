// `ctt-agent storage` - sequential write/read throughput of a file on a block
// device (USB mass storage on the DUT acting as USB host, SD card, eMMC...).
//
// O_DIRECT bypasses the page cache so the numbers are the device's, not RAM's.
// When O_DIRECT is unsupported (tmpfs, some FUSE), it falls back to buffered
// I/O + fsync + POSIX_FADV_DONTNEED and reports "direct": false.
// Every block carries its index + CRC so the read-back is verified.
#include <fcntl.h>
#include <sys/stat.h>
#include <unistd.h>

#include <cerrno>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <memory>
#include <string>

#include "ctt/byteorder.hpp"
#include "ctt/clock.hpp"
#include "ctt/crc32.hpp"
#include "ctt/json.hpp"
#include "ctt/options.hpp"
#include "ctt/output.hpp"

namespace ctt {
namespace {

struct Opts {
    std::string path;
    uint64_t size = 64ull << 20;
    uint64_t block = 1 << 20;
    bool direct = true;
    bool keep = false;
    bool json = false;
    bool do_write = true, do_read = true;
};

void usage(FILE* f) {
    std::fputs(
        "Usage: ctt-agent storage --path FILE [options]\n"
        "  --size SIZE      bytes to write/read (default 64M)\n"
        "  --block SIZE     I/O size (default 1M, multiple of 4K)\n"
        "  --no-direct      buffered I/O (page cache) instead of O_DIRECT\n"
        "  --read-only      only read (FILE must exist, e.g. from --keep)\n"
        "  --write-only     only write\n"
        "  --keep           keep FILE afterwards (default: delete)\n"
        "  -J, --json       JSON output\n",
        f);
}

struct AlignedFree {
    void operator()(void* p) const { std::free(p); }
};

void fill_block(uint8_t* b, uint64_t block, uint64_t idx) {
    uint32_t x = uint32_t(idx * 2654435761u) | 1u;
    for (uint64_t i = 16; i < block; i += 4) {
        x ^= x << 13;
        x ^= x >> 17;
        x ^= x << 5;
        put_le32(b + i, x);
    }
    put_le64(b, idx);
    put_le32(b + 8, crc32(b + 16, block - 16));
    put_le32(b + 12, 0x43545453);  // "STTC"
}

bool check_block(const uint8_t* b, uint64_t block, uint64_t idx) {
    return get_le64(b) == idx && get_le32(b + 12) == 0x43545453 &&
           get_le32(b + 8) == crc32(b + 16, block - 16);
}

int open_file(const std::string& path, int flags, bool& direct) {
    if (direct) {
        int fd = ::open(path.c_str(), flags | O_DIRECT | O_CLOEXEC, 0644);
        if (fd >= 0) return fd;
        if (errno != EINVAL) throw std::runtime_error("open " + path + ": " + std::strerror(errno));
        direct = false;  // filesystem without O_DIRECT
    }
    int fd = ::open(path.c_str(), flags | O_CLOEXEC, 0644);
    if (fd < 0) throw std::runtime_error("open " + path + ": " + std::strerror(errno));
    return fd;
}

}  // namespace

int run_storage_command(int argc, char** argv) {
    Opts o;
    try {
        for (int i = 2; i < argc; ++i) {
            std::string a = argv[i];
            auto val = [&]() -> std::string {
                if (i + 1 >= argc) throw std::runtime_error(a + " needs a value");
                return argv[++i];
            };
            if (a == "--path") o.path = val();
            else if (a == "--size") {
                if (!parse_size(val(), o.size) || o.size == 0) throw std::runtime_error("bad --size");
            } else if (a == "--block") {
                if (!parse_size(val(), o.block) || o.block < 4096 || o.block % 4096)
                    throw std::runtime_error("--block must be a multiple of 4K");
            } else if (a == "--no-direct") o.direct = false;
            else if (a == "--keep") o.keep = true;
            else if (a == "--read-only") o.do_write = false;
            else if (a == "--write-only") o.do_read = false;
            else if (a == "-J" || a == "--json") o.json = true;
            else if (a == "-h" || a == "--help") {
                usage(stdout);
                return 0;
            } else throw std::runtime_error("unknown option: " + a);
        }
        if (o.path.empty()) throw std::runtime_error("--path is required");
    } catch (const std::exception& e) {
        std::fprintf(stderr, "ctt-agent storage: %s\n\n", e.what());
        usage(stderr);
        return 64;
    }
    out::set_format(o.json ? out::Format::Json : out::Format::Text);
    const uint64_t blocks = (o.size + o.block - 1) / o.block;
    const uint64_t total = blocks * o.block;

    void* raw = nullptr;
    if (posix_memalign(&raw, 4096, o.block) != 0) {
        std::fprintf(stderr, "out of memory\n");
        return 1;
    }
    std::unique_ptr<uint8_t, AlignedFree> buf(static_cast<uint8_t*>(raw));

    Json res;
    res.add("event", "storage_result").add("path", o.path).add("bytes", total).add("block", o.block);
    std::string text = out::strprintf("storage %s  %llu MiB in %llu KiB blocks\n", o.path.c_str(),
                                      (unsigned long long)(total >> 20), (unsigned long long)(o.block >> 10));
    bool direct_w = o.direct, direct_r = o.direct;
    uint64_t bad = 0;
    try {
        if (o.do_write) {
            int fd = open_file(o.path, O_WRONLY | O_CREAT | O_TRUNC, direct_w);
            uint64_t t0 = now_ns();
            for (uint64_t i = 0; i < blocks; ++i) {
                fill_block(buf.get(), o.block, i);
                ssize_t w = ::write(fd, buf.get(), o.block);
                if (w != ssize_t(o.block)) {
                    int e = errno;
                    ::close(fd);
                    throw std::runtime_error(w < 0 ? std::string("write: ") + std::strerror(e)
                                                   : "short write (disk full?)");
                }
            }
            if (::fsync(fd) < 0) throw std::runtime_error(std::string("fsync: ") + std::strerror(errno));
            uint64_t dt = now_ns() - t0;
            posix_fadvise(fd, 0, 0, POSIX_FADV_DONTNEED);  // drop cached pages before reading
            ::close(fd);
            double mbs = double(total) / (double(dt) / 1e9) / 1e6;
            res.add("write_MBps", mbs).add("write_mbps", mbs * 8).add("write_s", double(dt) / 1e9)
                .add("write_direct", direct_w);
            text += out::strprintf("  write  %9.2f MB/s  (%8.2f Mbit/s)  %.2fs  %s\n", mbs, mbs * 8,
                                   double(dt) / 1e9, direct_w ? "O_DIRECT" : "buffered+fsync");
        }
        if (o.do_read) {
            int fd = open_file(o.path, O_RDONLY, direct_r);
            if (!direct_r) posix_fadvise(fd, 0, 0, POSIX_FADV_DONTNEED);
            uint64_t t0 = now_ns();
            for (uint64_t i = 0; i < blocks; ++i) {
                ssize_t r = ::read(fd, buf.get(), o.block);
                if (r != ssize_t(o.block)) {
                    int e = errno;
                    ::close(fd);
                    throw std::runtime_error(r < 0 ? std::string("read: ") + std::strerror(e)
                                                   : "short read (file smaller than --size?)");
                }
                if (!check_block(buf.get(), o.block, i)) ++bad;
            }
            uint64_t dt = now_ns() - t0;
            ::close(fd);
            double mbs = double(total) / (double(dt) / 1e9) / 1e6;
            res.add("read_MBps", mbs).add("read_mbps", mbs * 8).add("read_s", double(dt) / 1e9)
                .add("read_direct", direct_r).add("verify_errors", bad);
            text += out::strprintf("  read   %9.2f MB/s  (%8.2f Mbit/s)  %.2fs  %s  verify_errors=%llu\n",
                                   mbs, mbs * 8, double(dt) / 1e9,
                                   direct_r ? "O_DIRECT" : "buffered (may be cached!)",
                                   (unsigned long long)bad);
        }
    } catch (const std::exception& e) {
        if (!o.keep && o.do_write) ::unlink(o.path.c_str());
        Json j;
        j.add("event", "error").add("message", e.what());
        out::emit(j, std::string("error: ") + e.what());
        return 1;
    }
    if (!o.keep && o.do_write) ::unlink(o.path.c_str());
    res.add("ok", bad == 0);
    text += bad ? "RESULT: DATA CORRUPTION" : "RESULT: OK";
    out::emit(res, text);
    return bad ? 1 : 0;
}

}  // namespace ctt
