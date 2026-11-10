#pragma once

#include <atomic>

#include "ctt/framing.hpp"
#include "ctt/metrics.hpp"
#include "ctt/options.hpp"

namespace ctt {

int run_server(const AgentOptions& o);
int run_client(const AgentOptions& o);

// Shared building blocks (used by both roles).

// Sends DATA frames for sp.duration_ms (respecting sp.rate_bps), then STOP.
void send_data(Stream& s, const ctp::StartParams& sp, uint64_t t0, TxCounters& tx,
               IntervalReporter& rep, const std::atomic<bool>* abort);

// Receives DATA frames until STOP. Throws on EOF / unexpected frames.
void receive_data(FrameReader& rd, bool verify, RxCounters& rx, IntervalReporter& rep);

}  // namespace ctt
