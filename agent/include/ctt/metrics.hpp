#pragma once

#include <cstdint>
#include <string>
#include <vector>

#include "ctt/json.hpp"

namespace ctt {

inline double mbps(uint64_t bytes, uint64_t ns) {
    return ns ? double(bytes) * 8.0 * 1000.0 / double(ns) : 0.0;
}

// Receiver-side counters. The receiver is the source of truth for throughput.
struct RxCounters {
    uint64_t frames = 0;
    uint64_t bytes = 0;  // payload bytes (goodput)
    uint64_t lost = 0;
    uint64_t out_of_order = 0;
    uint64_t crc_errors = 0;
    uint64_t first_ns = 0;
    uint64_t last_ns = 0;
    uint32_t next_seq = 0;
    bool started = false;

    // Marks the start of the measurement window (e.g. when START_ACK is
    // exchanged). If never called, the first frame starts the window.
    void begin(uint64_t now);
    void on_frame(uint32_t seq, uint32_t len, uint64_t now, bool crc_ok);
    uint64_t duration_ns() const { return started ? last_ns - first_ns : 0; }
    Json to_json() const;
};

// Sender-side counters ("bytes handed to the transport").
struct TxCounters {
    uint64_t frames = 0;
    uint64_t bytes = 0;
    uint64_t start_ns = 0;
    uint64_t end_ns = 0;

    uint64_t duration_ns() const { return end_ns > start_ns ? end_ns - start_ns : 0; }
    Json to_json() const;
};

Json rx_json(uint64_t frames, uint64_t bytes, uint64_t lost, uint64_t ooo, uint64_t crc,
             uint64_t duration_ns);
Json tx_json(uint64_t frames, uint64_t bytes, uint64_t duration_ns);

class RttStats {
public:
    void add(uint64_t rtt_ns);
    size_t count() const { return samples_us_.size(); }
    Json to_json() const;
    std::string to_text() const;

private:
    std::vector<double> samples_us_;
    double jitter_sum_ = 0;
};

// Emits periodic "interval" events from a sender or receiver loop.
// dir:  "up" (client->server) or "down" (server->client)
// role: "rx" or "tx" (who is measuring)
class IntervalReporter {
public:
    IntervalReporter(const char* dir, const char* role, double interval_s, uint64_t t0_ns);

    void tick(uint64_t now, uint64_t bytes, uint64_t frames, uint64_t lost = 0,
              uint64_t crc_errors = 0) {
        if (interval_ns_ && now >= next_ns_) emit(now, bytes, frames, lost, crc_errors);
    }
    // Emits the final partial interval, if any.
    void finish(uint64_t now, uint64_t bytes, uint64_t frames, uint64_t lost = 0,
                uint64_t crc_errors = 0);

private:
    void emit(uint64_t now, uint64_t bytes, uint64_t frames, uint64_t lost, uint64_t crc);

    const char* dir_;
    const char* role_;
    uint64_t interval_ns_;
    uint64_t t0_;
    uint64_t next_ns_;
    uint64_t last_ns_;
    uint64_t last_bytes_ = 0, last_frames_ = 0, last_lost_ = 0, last_crc_ = 0;
};

}  // namespace ctt
