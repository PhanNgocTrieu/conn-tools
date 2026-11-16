// I2C access for device-level tests (MFi Authentication Coprocessor, ...).
//
// The agent deliberately knows nothing about Apple's register map (NDA):
// tests are sequences of generic steps (read / write / poll / variable-length
// read) supplied by a private config on the orchestrator side.
#pragma once

#include <cstdint>
#include <memory>
#include <stdexcept>
#include <string>
#include <vector>

namespace ctt {

// The device did not acknowledge (absent, asleep or busy). Retryable.
struct I2cNack : std::runtime_error {
    using std::runtime_error::runtime_error;
};

class I2cBus {
public:
    virtual ~I2cBus() = default;
    // Write `wlen` bytes, then (if rlen > 0) read `rlen` bytes.
    // combined=true: one transaction with a repeated START; false: two
    // transactions separated by STOP (what many auth coprocessors require).
    virtual void transfer(uint8_t addr, const uint8_t* w, size_t wlen, uint8_t* r, size_t rlen,
                          bool combined) = 0;
    virtual std::string describe() const = 0;
};

// "N" or "/dev/i2c-N"
std::unique_ptr<I2cBus> open_linux_i2c(const std::string& spec);

// Simulated device for tests:
//   "fake[,nack=P][,busy_ms=N][,seed=S][,rXX=HEX]..."
//   nack     probability that any transaction is NACKed (0..1)
//   busy_ms  device NACKs for N ms after every write (models processing)
//   rXX=HEX  initial content of register 0xXX (e.g. r00=05, r30=0400)
// Registers are byte arrays; reading register R returns its bytes (zero padded).
std::unique_ptr<I2cBus> open_fake_i2c(const std::string& spec);

std::unique_ptr<I2cBus> open_i2c(const std::string& spec);  // dispatches on "fake"

std::vector<uint8_t> parse_hex(const std::string& s);  // "0a0B" / "0x0a0b" / "0a:0b"; throws
std::string to_hex(const uint8_t* p, size_t n);

int run_i2c_command(int argc, char** argv);      // `ctt-agent i2c ...`
int run_storage_command(int argc, char** argv);  // `ctt-agent storage ...`
int run_pipe_command(int argc, char** argv);     // `ctt-agent pipe ...` (raw link relay)

}  // namespace ctt
