#pragma once

#include <cstdint>
#include <vector>

#include "ctt/ctp.hpp"
#include "ctt/transport.hpp"

namespace ctt {

// Reassembles CTP frames from a byte stream. Reads in large chunks so that
// small frames (e.g. RFCOMM-sized) do not cost one syscall each.
class FrameReader {
public:
    explicit FrameReader(Stream& s, size_t bufsize = 256 * 1024);

    // Returns false on clean EOF at a frame boundary. Throws on protocol error,
    // EOF in the middle of a frame, I/O error or timeout. `payload` stays valid
    // until the next call.
    bool next(ctp::Header& h, const uint8_t*& payload);

    // Like next(), but gives up after timeout_ms without a complete frame.
    enum class Status { Frame, Eof, Timeout };
    Status next_for(ctp::Header& h, const uint8_t*& payload, int timeout_ms);

    // Makes the frame just returned by next()/next_for() be returned again.
    void push_back() { start_ = last_start_; }

private:
    bool ensure(size_t n);
    bool have_frame() const;
    size_t last_start_ = 0;

    Stream& s_;
    std::vector<uint8_t> buf_;
    size_t start_ = 0;
    size_t end_ = 0;
};

// Sends header + payload with a single writev (no payload copy).
// h.payload_len is set from len.
void send_frame(Stream& s, ctp::Header h, const uint8_t* payload, size_t len);

inline void send_control(Stream& s, ctp::MsgType type, uint32_t seq = 0,
                         const uint8_t* payload = nullptr, size_t len = 0) {
    ctp::Header h;
    h.type = type;
    h.seq = seq;
    send_frame(s, h, payload, len);
}

}  // namespace ctt
