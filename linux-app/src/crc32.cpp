#include "ctt/crc32.hpp"

#include "ctt/byteorder.hpp"

namespace ctt {
namespace {

struct Tables {
    uint32_t t[8][256];
    Tables() {
        for (uint32_t i = 0; i < 256; ++i) {
            uint32_t c = i;
            for (int k = 0; k < 8; ++k) c = (c & 1) ? (c >> 1) ^ 0xEDB88320u : (c >> 1);
            t[0][i] = c;
        }
        for (uint32_t i = 0; i < 256; ++i)
            for (int s = 1; s < 8; ++s) t[s][i] = (t[s - 1][i] >> 8) ^ t[0][t[s - 1][i] & 0xFF];
    }
};

const Tables& tables() {
    static const Tables tb;
    return tb;
}

}  // namespace

uint32_t crc32_update(uint32_t crc, const uint8_t* p, size_t len) {
    const auto& T = tables().t;
    crc = ~crc;
    while (len >= 8) {
        uint32_t one = get_le32(p) ^ crc;
        uint32_t two = get_le32(p + 4);
        crc = T[7][one & 0xFF] ^ T[6][(one >> 8) & 0xFF] ^ T[5][(one >> 16) & 0xFF] ^
              T[4][one >> 24] ^ T[3][two & 0xFF] ^ T[2][(two >> 8) & 0xFF] ^
              T[1][(two >> 16) & 0xFF] ^ T[0][two >> 24];
        p += 8;
        len -= 8;
    }
    while (len--) crc = (crc >> 8) ^ T[0][(crc ^ *p++) & 0xFF];
    return ~crc;
}

}  // namespace ctt
