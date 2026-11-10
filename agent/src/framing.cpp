#include "ctt/framing.hpp"

#include <cstring>
#include <stdexcept>
#include <string>

namespace ctt {

FrameReader::FrameReader(Stream& s, size_t bufsize) : s_(s), buf_(bufsize) {}

bool FrameReader::ensure(size_t n) {
    while (end_ - start_ < n) {
        if (buf_.size() - start_ < n) {
            // Not enough room after start_: move the partial frame to the front.
            std::memmove(buf_.data(), buf_.data() + start_, end_ - start_);
            end_ -= start_;
            start_ = 0;
            if (buf_.size() < n) buf_.resize(n);
        }
        ssize_t r = s_.read_some(buf_.data() + end_, buf_.size() - end_);
        if (r == 0) {
            if (end_ == start_) return false;
            throw std::runtime_error("connection closed in the middle of a frame");
        }
        end_ += size_t(r);
    }
    return true;
}

bool FrameReader::next(ctp::Header& h, const uint8_t*& payload) {
    if (start_ == end_) start_ = end_ = 0;
    if (!ensure(ctp::kHeaderSize)) return false;
    auto st = ctp::decode_header(buf_.data() + start_, h);
    if (st != ctp::DecodeStatus::Ok)
        throw std::runtime_error(std::string("CTP decode error: ") + ctp::to_string(st));
    size_t total = ctp::kHeaderSize + h.payload_len;
    if (!ensure(total)) throw std::runtime_error("connection closed in the middle of a frame");
    payload = buf_.data() + start_ + ctp::kHeaderSize;
    start_ += total;
    return true;
}

void send_frame(Stream& s, ctp::Header h, const uint8_t* payload, size_t len) {
    uint8_t hdr[ctp::kHeaderSize];
    h.payload_len = uint32_t(len);
    ctp::encode_header(h, hdr);
    struct iovec iov[2];
    iov[0].iov_base = hdr;
    iov[0].iov_len = sizeof(hdr);
    iov[1].iov_base = const_cast<uint8_t*>(payload);
    iov[1].iov_len = len;
    write_all(s, iov, len ? 2 : 1);
}

void write_all(Stream& s, struct iovec* iov, int cnt) {
    while (cnt > 0) {
        ssize_t w = s.writev_some(iov, cnt);
        if (w <= 0) throw std::runtime_error("write returned 0");
        size_t left = size_t(w);
        while (cnt > 0 && left >= iov->iov_len) {
            left -= iov->iov_len;
            ++iov;
            --cnt;
        }
        if (cnt > 0) {
            iov->iov_base = static_cast<char*>(iov->iov_base) + left;
            iov->iov_len -= left;
        }
    }
}

}  // namespace ctt
