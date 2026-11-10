#include "fd_stream.hpp"

#include <poll.h>
#include <sys/socket.h>
#include <sys/time.h>
#include <unistd.h>

#include <cerrno>
#include <cstring>
#include <stdexcept>

namespace ctt {

void throw_errno(const std::string& what) {
    int e = errno;
    if (e == EAGAIN || e == EWOULDBLOCK) throw TimeoutError(what + ": timed out");
    throw std::runtime_error(what + ": " + std::strerror(e));
}

FdStream::~FdStream() {
    if (fd_ >= 0) ::close(fd_);
}

ssize_t FdStream::read_some(uint8_t* buf, size_t len) {
    for (;;) {
        ssize_t r = ::read(fd_, buf, len);
        if (r >= 0) return r;
        if (errno == EINTR) continue;
        throw_errno("read");
    }
}

ssize_t FdStream::writev_some(const struct iovec* iov, int iovcnt) {
    for (;;) {
        ssize_t r = ::writev(fd_, iov, iovcnt);
        if (r >= 0) return r;
        if (errno == EINTR) continue;
        throw_errno("write");
    }
}

void FdStream::shutdown_write() { ::shutdown(fd_, SHUT_WR); }

void apply_socket_options(int fd, const TransportOptions& o) {
    if (o.sndbuf > 0) setsockopt(fd, SOL_SOCKET, SO_SNDBUF, &o.sndbuf, sizeof(o.sndbuf));
    if (o.rcvbuf > 0) setsockopt(fd, SOL_SOCKET, SO_RCVBUF, &o.rcvbuf, sizeof(o.rcvbuf));
    if (o.timeout_ms > 0) {
        timeval tv{};
        tv.tv_sec = o.timeout_ms / 1000;
        tv.tv_usec = (o.timeout_ms % 1000) * 1000;
        setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof(tv));
        setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &tv, sizeof(tv));
    }
}

bool wait_readable(int fd, int timeout_ms) {
    pollfd p{};
    p.fd = fd;
    p.events = POLLIN;
    for (;;) {
        int r = ::poll(&p, 1, timeout_ms);
        if (r > 0) return true;
        if (r == 0) return false;
        if (errno != EINTR) throw_errno("poll");
    }
}

}  // namespace ctt
