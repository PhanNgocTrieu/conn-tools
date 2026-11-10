// Byte-stream view of a SOCK_SEQPACKET socket (L2CAP, AF_UNIX seqpacket).
//  - writes are split into packets of at most `omtu` bytes
//  - reads never truncate a packet: if the caller's buffer is smaller than
//    `imtu`, the packet is received into a bounce buffer and handed out in
//    pieces. A packet larger than imtu is reported as an error, not dropped.
#pragma once

#include <vector>

#include "fd_stream.hpp"

namespace ctt {

class PacketStream : public FdStream {
public:
    PacketStream(int fd, std::string peer, size_t omtu, size_t imtu);

    ssize_t read_some(uint8_t* buf, size_t len) override;
    ssize_t writev_some(const struct iovec* iov, int iovcnt) override;

    size_t omtu() const { return omtu_; }
    size_t imtu() const { return imtu_; }
    uint64_t packets_tx() const { return packets_tx_; }
    uint64_t packets_rx() const { return packets_rx_; }

private:
    ssize_t recv_packet(uint8_t* buf, size_t len);

    size_t omtu_;
    size_t imtu_;
    std::vector<uint8_t> pend_;
    size_t pend_off_ = 0;
    size_t pend_len_ = 0;
    uint64_t packets_tx_ = 0;
    uint64_t packets_rx_ = 0;
};

}  // namespace ctt
