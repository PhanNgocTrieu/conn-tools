#pragma once

#include <cstddef>
#include <cstdint>
#include <vector>

namespace ctt {

// Pre-generated pseudo-random payload. Frame `seq` uses the window starting at
// offset (seq % period), so consecutive frames differ while the sender never
// has to generate or copy data per frame. CRCs for every window are
// pre-computed, so CRC costs the sender nothing at run time.
class PatternSource {
public:
    PatternSource(size_t block, bool with_crc);

    const uint8_t* payload(uint32_t seq) const { return buf_.data() + (seq % period_); }
    uint32_t crc(uint32_t seq) const { return crcs_.empty() ? 0 : crcs_[seq % period_]; }
    size_t block() const { return block_; }
    size_t period() const { return period_; }

private:
    size_t block_;
    size_t period_;
    std::vector<uint8_t> buf_;
    std::vector<uint32_t> crcs_;
};

}  // namespace ctt
