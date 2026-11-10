// CTP - Connectivity Test Protocol v1. See docs/ctp-spec.md.
#pragma once

#include <cstddef>
#include <cstdint>
#include <string_view>

namespace ctt::ctp {

constexpr uint32_t kMagic = 0x31505443;  // bytes "CTP1" on the wire
constexpr uint8_t kVersion = 1;
constexpr size_t kHeaderSize = 32;
constexpr uint32_t kMaxPayload = 1u << 20;  // 1 MiB

enum class MsgType : uint8_t {
    Start = 1,
    StartAck = 2,
    Data = 3,
    Stop = 4,
    Stats = 5,
    EchoReq = 6,
    EchoRsp = 7,
    Error = 8,
};

// Header flags
constexpr uint16_t kFlagCrc = 0x0001;  // crc32 field covers the payload

struct Header {
    uint8_t version = kVersion;
    MsgType type = MsgType::Data;
    uint16_t flags = 0;
    uint32_t seq = 0;
    uint32_t payload_len = 0;
    uint64_t ts_ns = 0;
    uint32_t crc32 = 0;
    uint32_t reserved = 0;
};

enum class DecodeStatus { Ok, BadMagic, BadVersion, TooLarge };

void encode_header(const Header& h, uint8_t* out /* kHeaderSize */);
DecodeStatus decode_header(const uint8_t* in /* kHeaderSize */, Header& out);
const char* to_string(MsgType t);
const char* to_string(DecodeStatus s);

// Test mode, always named from the CLIENT's point of view:
//   Tx    client -> server   ("upstream")
//   Rx    server -> client   ("downstream")
//   Bidir both at once
//   Echo  request/response round trips (latency)
enum class Mode : uint8_t { Tx = 1, Rx = 2, Bidir = 3, Echo = 4 };
const char* to_string(Mode m);
bool parse_mode(std::string_view s, Mode& out);

// START payload flags
constexpr uint32_t kStartVerify = 0x0001;  // sender fills CRC, receiver checks it

struct StartParams {
    Mode mode = Mode::Tx;
    uint32_t block_size = 0;
    uint32_t duration_ms = 0;
    uint32_t flags = 0;
    uint64_t rate_bps = 0;  // 0 = unlimited
    uint32_t echo_interval_us = 0;
    uint32_t echo_count = 0;  // 0 = run for duration
};
constexpr size_t kStartSize = 32;
void encode_start(const StartParams& p, uint8_t* out /* kStartSize */);
bool decode_start(const uint8_t* in, size_t len, StartParams& out);

// STATS payload: the server's own view of the session, sent at the end.
struct StatsPayload {
    bool has_rx = false;
    bool has_tx = false;
    uint64_t rx_frames = 0;
    uint64_t rx_bytes = 0;
    uint64_t rx_lost = 0;
    uint64_t rx_out_of_order = 0;
    uint64_t rx_crc_errors = 0;
    uint64_t rx_duration_ns = 0;
    uint64_t tx_frames = 0;
    uint64_t tx_bytes = 0;
    uint64_t tx_duration_ns = 0;
    uint64_t echoed = 0;
};
constexpr size_t kStatsSize = 88;
void encode_stats(const StatsPayload& s, uint8_t* out /* kStatsSize */);
bool decode_stats(const uint8_t* in, size_t len, StatsPayload& out);

}  // namespace ctt::ctp
