#pragma once

#include <atomic>

#include "ctt/framing.hpp"
#include "ctt/metrics.hpp"
#include "ctt/options.hpp"

namespace ctt {

int run_server(const AgentOptions& o);
int run_client(const AgentOptions& o);

// ---- shared building blocks (both roles) ----------------------------------

// Lossy (datagram) transports: control frames can be lost too.
constexpr int kCtlRetryMs = 300;  // wait before re-sending START / STOP
constexpr int kCtlRetries = 10;
constexpr int kStopCopies = 3;    // STOP is sent this many times

// Idle time after which a lossy receiver assumes the peer has finished.
int lossy_idle_ms(const TransportOptions& o);

// Sends STOP (several copies on lossy transports).
void send_stop(Stream& s, uint32_t seq);

// Sends DATA frames for sp.duration_ms (respecting sp.rate_bps), then STOP.
void send_data(Stream& s, const ctp::StartParams& sp, uint64_t t0, TxCounters& tx,
               IntervalReporter& rep, const std::atomic<bool>* abort);

// Receives DATA frames until STOP. Returns true when STOP was seen, false when
// a lossy receiver gave up after `idle_ms` without traffic (STOP lost).
// `reack`: server side - answer duplicate STARTs (lost START_ACK) on it.
bool receive_data(FrameReader& rd, bool verify, RxCounters& rx, IntervalReporter& rep,
                  bool lossy, int idle_ms, Stream* reack);

}  // namespace ctt
