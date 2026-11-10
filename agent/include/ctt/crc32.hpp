#pragma once

#include <cstddef>
#include <cstdint>

namespace ctt {

// CRC-32 (IEEE 802.3, reflected, poly 0xEDB88320), slice-by-8.
// crc32("123456789") == 0xCBF43926.
uint32_t crc32_update(uint32_t crc, const uint8_t* data, size_t len);

inline uint32_t crc32(const uint8_t* data, size_t len) {
    return crc32_update(0, data, len);
}

}  // namespace ctt
