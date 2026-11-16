#include "ctt/pattern.hpp"

#include <algorithm>

#include "ctt/crc32.hpp"

namespace ctt {

PatternSource::PatternSource(size_t block, bool with_crc) : block_(block) {
    // Up to 256 distinct windows, but bound the CRC pre-computation to ~4 MiB.
    period_ = std::clamp<size_t>((4u << 20) / std::max<size_t>(block, 1), 1, 256);
    buf_.resize(block + period_);
    uint32_t x = 0x2545F491u;  // xorshift32
    for (auto& b : buf_) {
        x ^= x << 13;
        x ^= x >> 17;
        x ^= x << 5;
        b = uint8_t(x);
    }
    if (with_crc) {
        crcs_.resize(period_);
        for (size_t i = 0; i < period_; ++i) crcs_[i] = crc32(buf_.data() + i, block_);
    }
}

}  // namespace ctt
