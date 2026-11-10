#include "ctt/ctp.hpp"

#include <cstring>

#include "ctt/byteorder.hpp"

namespace ctt::ctp {

// Header layout (32 bytes, little-endian):
//  0 magic u32 | 4 version u8 | 5 type u8 | 6 flags u16 | 8 seq u32
// 12 payload_len u32 | 16 ts_ns u64 | 24 crc32 u32 | 28 reserved u32
void encode_header(const Header& h, uint8_t* o) {
    put_le32(o + 0, kMagic);
    o[4] = h.version;
    o[5] = uint8_t(h.type);
    put_le16(o + 6, h.flags);
    put_le32(o + 8, h.seq);
    put_le32(o + 12, h.payload_len);
    put_le64(o + 16, h.ts_ns);
    put_le32(o + 24, h.crc32);
    put_le32(o + 28, h.reserved);
}

DecodeStatus decode_header(const uint8_t* in, Header& h) {
    if (get_le32(in) != kMagic) return DecodeStatus::BadMagic;
    h.version = in[4];
    if (h.version != kVersion) return DecodeStatus::BadVersion;
    h.type = MsgType(in[5]);
    h.flags = get_le16(in + 6);
    h.seq = get_le32(in + 8);
    h.payload_len = get_le32(in + 12);
    h.ts_ns = get_le64(in + 16);
    h.crc32 = get_le32(in + 24);
    h.reserved = get_le32(in + 28);
    if (h.payload_len > kMaxPayload) return DecodeStatus::TooLarge;
    return DecodeStatus::Ok;
}

const char* to_string(MsgType t) {
    switch (t) {
        case MsgType::Start: return "START";
        case MsgType::StartAck: return "START_ACK";
        case MsgType::Data: return "DATA";
        case MsgType::Stop: return "STOP";
        case MsgType::Stats: return "STATS";
        case MsgType::EchoReq: return "ECHO_REQ";
        case MsgType::EchoRsp: return "ECHO_RSP";
        case MsgType::Error: return "ERROR";
    }
    return "UNKNOWN";
}

const char* to_string(DecodeStatus s) {
    switch (s) {
        case DecodeStatus::Ok: return "ok";
        case DecodeStatus::BadMagic: return "bad magic (stream desynchronised or not a CTP peer)";
        case DecodeStatus::BadVersion: return "unsupported CTP version";
        case DecodeStatus::TooLarge: return "payload length too large";
    }
    return "?";
}

const char* to_string(Mode m) {
    switch (m) {
        case Mode::Tx: return "tx";
        case Mode::Rx: return "rx";
        case Mode::Bidir: return "bidir";
        case Mode::Echo: return "echo";
    }
    return "?";
}

bool parse_mode(std::string_view s, Mode& out) {
    if (s == "tx" || s == "up") out = Mode::Tx;
    else if (s == "rx" || s == "down") out = Mode::Rx;
    else if (s == "bidir" || s == "both") out = Mode::Bidir;
    else if (s == "echo" || s == "rtt") out = Mode::Echo;
    else return false;
    return true;
}

// START layout (32 bytes):
//  0 mode u8 | 1..3 reserved | 4 block u32 | 8 duration_ms u32 | 12 flags u32
// 16 rate_bps u64 | 24 echo_interval_us u32 | 28 echo_count u32
void encode_start(const StartParams& p, uint8_t* o) {
    std::memset(o, 0, kStartSize);
    o[0] = uint8_t(p.mode);
    put_le32(o + 4, p.block_size);
    put_le32(o + 8, p.duration_ms);
    put_le32(o + 12, p.flags);
    put_le64(o + 16, p.rate_bps);
    put_le32(o + 24, p.echo_interval_us);
    put_le32(o + 28, p.echo_count);
}

bool decode_start(const uint8_t* in, size_t len, StartParams& p) {
    if (len < kStartSize) return false;
    if (in[0] < uint8_t(Mode::Tx) || in[0] > uint8_t(Mode::Echo)) return false;
    p.mode = Mode(in[0]);
    p.block_size = get_le32(in + 4);
    p.duration_ms = get_le32(in + 8);
    p.flags = get_le32(in + 12);
    p.rate_bps = get_le64(in + 16);
    p.echo_interval_us = get_le32(in + 24);
    p.echo_count = get_le32(in + 28);
    return p.block_size > 0 && p.block_size <= kMaxPayload;
}

// STATS layout (88 bytes): 0 flags u32 (bit0 rx, bit1 tx) | 4 reserved u32 |
// 8.. ten u64 counters in declaration order.
void encode_stats(const StatsPayload& s, uint8_t* o) {
    std::memset(o, 0, kStatsSize);
    put_le32(o, (s.has_rx ? 1u : 0u) | (s.has_tx ? 2u : 0u));
    const uint64_t v[10] = {s.rx_frames,      s.rx_bytes, s.rx_lost,  s.rx_out_of_order,
                            s.rx_crc_errors,  s.rx_duration_ns,       s.tx_frames,
                            s.tx_bytes,       s.tx_duration_ns,       s.echoed};
    for (int i = 0; i < 10; ++i) put_le64(o + 8 + 8 * i, v[i]);
}

bool decode_stats(const uint8_t* in, size_t len, StatsPayload& s) {
    if (len < kStatsSize) return false;
    uint32_t f = get_le32(in);
    s.has_rx = f & 1;
    s.has_tx = f & 2;
    uint64_t* v[10] = {&s.rx_frames,     &s.rx_bytes,       &s.rx_lost,   &s.rx_out_of_order,
                       &s.rx_crc_errors, &s.rx_duration_ns, &s.tx_frames, &s.tx_bytes,
                       &s.tx_duration_ns, &s.echoed};
    for (int i = 0; i < 10; ++i) *v[i] = get_le64(in + 8 + 8 * i);
    return true;
}

}  // namespace ctt::ctp
