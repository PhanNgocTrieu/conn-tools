#include <algorithm>
#include <stdexcept>
#include <string>
#include <thread>

#include "ctt/clock.hpp"
#include "ctt/crc32.hpp"
#include "ctt/pattern.hpp"
#include "ctt/session.hpp"

namespace ctt {

void sleep_ns(uint64_t ns) { std::this_thread::sleep_for(std::chrono::nanoseconds(ns)); }

int lossy_idle_ms(const TransportOptions& o) {
    // Bounded by the idle I/O timeout, but never so long that a lost STOP
    // stalls the test noticeably.
    int t = o.timeout_ms > 0 ? o.timeout_ms : 10000;
    return std::min(t, 2000);
}

void send_stop(Stream& s, uint32_t seq) {
    int copies = s.lossy() ? kStopCopies : 1;
    for (int i = 0; i < copies; ++i) {
        if (i) sleep_ns(2000000);
        send_control(s, ctp::MsgType::Stop, seq);
    }
}

void send_data(Stream& s, const ctp::StartParams& sp, uint64_t t0, TxCounters& tx,
               IntervalReporter& rep, const std::atomic<bool>* abort) {
    const bool verify = sp.flags & ctp::kStartVerify;
    PatternSource pat(sp.block_size, verify);
    const uint64_t end = t0 + uint64_t(sp.duration_ms) * 1000000ull;

    ctp::Header h;
    h.type = ctp::MsgType::Data;
    h.flags = verify ? ctp::kFlagCrc : 0;
    uint32_t seq = 0;
    tx.start_ns = t0;

    for (;;) {
        uint64_t now = now_ns();
        if (now >= end || (abort && abort->load(std::memory_order_relaxed))) break;
        if (sp.rate_bps) {
            uint64_t due = t0 + uint64_t(double(tx.bytes) * 8e9 / double(sp.rate_bps));
            if (now < due) {
                sleep_ns(std::min(due, end) - now);
                continue;
            }
        }
        h.seq = seq;
        h.ts_ns = now;
        h.crc32 = pat.crc(seq);
        send_frame(s, h, pat.payload(seq), sp.block_size);
        ++seq;
        ++tx.frames;
        tx.bytes += sp.block_size;
        rep.tick(now_ns(), tx.bytes, tx.frames);
    }
    tx.end_ns = now_ns();
    rep.finish(tx.end_ns, tx.bytes, tx.frames);
    send_stop(s, seq);
}

bool receive_data(FrameReader& rd, bool verify, RxCounters& rx, IntervalReporter& rep,
                  bool lossy, int idle_ms, Stream* reack) {
    ctp::Header h;
    const uint8_t* p = nullptr;
    for (;;) {
        if (lossy) {
            auto st = rd.next_for(h, p, idle_ms);
            if (st == FrameReader::Status::Timeout) {
                rep.finish(now_ns(), rx.bytes, rx.frames, rx.lost, rx.crc_errors);
                return false;  // peer finished, STOP lost (or everything lost)
            }
            if (st == FrameReader::Status::Eof)
                throw std::runtime_error("peer closed the connection during the test");
        } else if (!rd.next(h, p)) {
            throw std::runtime_error("peer closed the connection during the test");
        }
        uint64_t now = now_ns();
        switch (h.type) {
            case ctp::MsgType::Data: {
                bool ok = !(verify && (h.flags & ctp::kFlagCrc)) || crc32(p, h.payload_len) == h.crc32;
                rx.on_frame(h.seq, h.payload_len, now, ok, h.ts_ns);
                rep.tick(now, rx.bytes, rx.frames, rx.lost, rx.crc_errors);
                break;
            }
            case ctp::MsgType::Stop:
                rep.finish(now, rx.bytes, rx.frames, rx.lost, rx.crc_errors);
                return true;
            case ctp::MsgType::Error:
                throw std::runtime_error("peer error: " +
                                         std::string(reinterpret_cast<const char*>(p), h.payload_len));
            default:
                if (lossy) {
                    // Duplicate START: our START_ACK was lost - repeat it.
                    if (h.type == ctp::MsgType::Start && reack)
                        send_control(*reack, ctp::MsgType::StartAck);
                    break;  // stale control frames are harmless on datagram links
                }
                throw std::runtime_error(std::string("unexpected ") + ctp::to_string(h.type) +
                                         " frame during data phase");
        }
    }
}

}  // namespace ctt
