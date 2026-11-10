// Stream over a POSIX socket file descriptor. Base for TCP, and later RFCOMM,
// L2CAP and other socket-based transports.
#pragma once

#include <string>

#include "ctt/transport.hpp"

namespace ctt {

[[noreturn]] void throw_errno(const std::string& what);

// Waits for the fd to become readable. Returns false on timeout.
bool wait_readable(int fd, int timeout_ms);

class FdStream : public Stream {
public:
    FdStream(int fd, std::string peer) : fd_(fd), peer_(std::move(peer)) {}
    ~FdStream() override;
    FdStream(const FdStream&) = delete;
    FdStream& operator=(const FdStream&) = delete;

    ssize_t read_some(uint8_t* buf, size_t len) override;
    ssize_t writev_some(const struct iovec* iov, int iovcnt) override;
    void shutdown_write() override;
    std::string peer() const override { return peer_; }
    bool wait_readable(int timeout_ms) override { return ctt::wait_readable(fd_, timeout_ms); }

    int fd() const { return fd_; }

protected:
    int fd_;
    std::string peer_;
};

// SO_SNDBUF / SO_RCVBUF / SO_RCVTIMEO / SO_SNDTIMEO
void apply_socket_options(int fd, const TransportOptions& o);


}  // namespace ctt
