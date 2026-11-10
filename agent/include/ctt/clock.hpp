#pragma once

#include <cstdint>
#include <ctime>

namespace ctt {

// Monotonic nanoseconds. Only comparable within one machine (used for
// durations and for RTT, where the timestamp is echoed back to its origin).
inline uint64_t now_ns() {
    timespec ts{};
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return uint64_t(ts.tv_sec) * 1000000000ull + uint64_t(ts.tv_nsec);
}

void sleep_ns(uint64_t ns);

}  // namespace ctt
