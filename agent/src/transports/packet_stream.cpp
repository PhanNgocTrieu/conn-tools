#include "packet_stream.hpp"

#include <sys/socket.h>

#include <algorithm>
#include <cerrno>
#include <cstring>
#include <stdexcept>
#include <string>

namespace ctt {

PacketStream::PacketStream(int fd, std::string peer, size_t omtu, size_t imtu)
    : FdStream(fd, std::move(peer)), omtu_(std::max<size_t>(omtu, 1)),
      imtu_(std::max<size_t>(imtu, 1)), pend_(imtu_) {}

ssize_t PacketStream::recv_packet(uint8_t* buf, size_t len) {
    iovec iov{buf, len};
    msghdr msg{};
    msg.msg_iov = &iov;
    msg.msg_iovlen = 1;
    for (;;) {
        ssize_t r = ::recvmsg(fd_, &msg, 0);
        if (r >= 0) {
            if (msg.msg_flags & MSG_TRUNC)
                throw std::runtime_error("received packet larger than imtu (" +
                                         std::to_string(imtu_) + ")");
            if (r > 0) ++packets_rx_;
            return r;
        }
        if (errno == EINTR) continue;
        throw_errno("recv");
    }
}

ssize_t PacketStream::read_some(uint8_t* buf, size_t len) {
    if (pend_off_ == pend_len_) {
        if (len >= imtu_) return recv_packet(buf, len);  // zero-copy path
        ssize_t r = recv_packet(pend_.data(), pend_.size());
        if (r <= 0) return r;
        pend_off_ = 0;
        pend_len_ = size_t(r);
    }
    size_t n = std::min(len, pend_len_ - pend_off_);
    std::memcpy(buf, pend_.data() + pend_off_, n);
    pend_off_ += n;
    return ssize_t(n);
}

ssize_t PacketStream::writev_some(const struct iovec* iov, int iovcnt) {
    // Take at most omtu bytes from the iovec array -> exactly one packet.
    iovec part[8];
    int n = 0;
    size_t total = 0;
    for (int i = 0; i < iovcnt && n < 8 && total < omtu_; ++i) {
        if (iov[i].iov_len == 0) continue;
        size_t take = std::min(iov[i].iov_len, omtu_ - total);
        part[n].iov_base = iov[i].iov_base;
        part[n].iov_len = take;
        total += take;
        ++n;
    }
    if (n == 0) return 0;
    msghdr msg{};
    msg.msg_iov = part;
    msg.msg_iovlen = size_t(n);
    for (;;) {
        ssize_t r = ::sendmsg(fd_, &msg, MSG_NOSIGNAL);
        if (r >= 0) {
            ++packets_tx_;
            return r;
        }
        if (errno == EINTR) continue;
        throw_errno("send");
    }
}

}  // namespace ctt
