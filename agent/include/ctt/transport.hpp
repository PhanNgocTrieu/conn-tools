// Transport abstraction. Every transport (TCP, RFCOMM, L2CAP, USB, ...) only
// has to provide a reliable byte/packet stream; CTP framing, test modes and
// metrics are shared on top of it.
#pragma once

#include <sys/types.h>
#include <sys/uio.h>

#include <map>
#include <memory>
#include <stdexcept>
#include <string>
#include <string_view>
#include <vector>

namespace ctt {

class Json;

struct TransportOptions {
    int sndbuf = 0;          // bytes, 0 = OS default
    int rcvbuf = 0;          // bytes, 0 = OS default
    bool nodelay = false;    // disable Nagle / coalescing where applicable
    int timeout_ms = 10000;  // idle read/write timeout, 0 = none
    std::map<std::string, std::string> extra;  // transport specific (-o key=value)
};

struct TimeoutError : std::runtime_error {
    using std::runtime_error::runtime_error;
};

class Stream {
public:
    virtual ~Stream() = default;
    // Returns >0 bytes read, 0 on orderly EOF. Throws on error/timeout.
    virtual ssize_t read_some(uint8_t* buf, size_t len) = 0;
    // Returns >0 bytes written. Throws on error/timeout.
    virtual ssize_t writev_some(const struct iovec* iov, int iovcnt) = 0;
    virtual void shutdown_write() = 0;
    virtual std::string peer() const = 0;
    // Transport specific link information (RTT, MTU, RSSI, ...).
    virtual void link_info(Json& /*out*/) const {}
};

class Listener {
public:
    virtual ~Listener() = default;
    // timeout_ms < 0: wait forever. Returns nullptr on timeout.
    virtual std::unique_ptr<Stream> accept(int timeout_ms) = 0;
    virtual std::string local() const = 0;
    // Extra fields for the "listening" event (e.g. bdaddr + RFCOMM channel),
    // so the orchestrator can build the client's target endpoint.
    virtual void describe(Json& /*out*/) const {}
};

class Transport {
public:
    virtual ~Transport() = default;
    virtual const char* name() const = 0;
    virtual const char* description() const = 0;
    virtual std::string default_listen_endpoint() const = 0;
    virtual uint32_t default_block() const = 0;
    virtual std::unique_ptr<Listener> listen(const std::string& endpoint,
                                             const TransportOptions& opts) = 0;
    virtual std::unique_ptr<Stream> connect(const std::string& endpoint,
                                            const TransportOptions& opts) = 0;
};

Transport* find_transport(std::string_view name);
std::vector<Transport*> all_transports();

// Writes every byte of the iovec array (modifies iov in place).
void write_all(Stream& s, struct iovec* iov, int iovcnt);

}  // namespace ctt
