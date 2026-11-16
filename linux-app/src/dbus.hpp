// Minimal D-Bus client over the system bus socket: just enough to export a
// BlueZ GATT application (method calls in and out, properties, unix fds).
// No libdbus / sd-bus, so the agent stays a static binary without dependencies.
#pragma once

#include <cstdint>
#include <deque>
#include <functional>
#include <map>
#include <string>
#include <vector>

namespace ctt::dbus {

// A D-Bus value. `t` is the type code of the value ('s', 'u', 'a', '(', 'v', ...).
//   integers / bool / fd index -> u (signed values are stored as two's complement)
//   s / o / g                  -> s
//   a / ( / {                  -> v (elements); for 'a', sig = element signature
//   v                          -> v[0] = inner value, sig = inner signature
struct Val {
    char t = 0;
    uint64_t u = 0;
    std::string s;
    std::string sig;
    std::vector<Val> v;

    // dict lookup for a{sv} / a{s..}: returns the value of key `k` (unwrapped variant), or nullptr
    const Val* get(const std::string& k) const;
};

Val byte(uint8_t x);
Val boolean(bool x);
Val u16(uint16_t x);
Val i16(int16_t x);
Val u32(uint32_t x);
Val str(const std::string& x);
Val path(const std::string& x);
Val fd_index(uint32_t idx);
Val variant(const Val& inner);   // signature derived from the inner value
Val array(const std::string& elem_sig, std::vector<Val> items = {});
Val bytes(const uint8_t* p, size_t n);   // ay
Val strings(const std::vector<std::string>& xs);  // as
Val entry(Val k, Val v);   // dict entry {kv}
Val sv(const std::string& k, const Val& v);  // {sv} entry, value wrapped in a variant
Val structure(std::vector<Val> items);

// Signature of a single value (e.g. "a{sv}").
std::string signature_of(const Val& v);

struct Message {
    uint8_t type = 0;  // 1 call, 2 return, 3 error, 4 signal
    uint8_t flags = 0;
    uint32_t serial = 0;
    uint32_t reply_serial = 0;
    std::string path, interface, member, error_name, destination, sender, signature;
    std::vector<Val> body;
    std::vector<int> fds;  // received fds (owned by the receiver) / fds to send
};

class Connection {
public:
    using Handler = std::function<void(const Message&)>;

    // Connects to the system bus (DBUS_SYSTEM_BUS_ADDRESS or /run/dbus/system_bus_socket).
    Connection();
    ~Connection();
    Connection(const Connection&) = delete;
    Connection& operator=(const Connection&) = delete;

    const std::string& unique_name() const { return unique_; }
    int fd() const { return fd_; }

    // Blocking call; throws std::runtime_error on a D-Bus error reply or timeout.
    Message call(const std::string& dest, const std::string& path, const std::string& iface,
                 const std::string& member, std::vector<Val> args = {}, int timeout_ms = 10000);

    void reply(const Message& to, std::vector<Val> args = {}, std::vector<int> fds = {});
    void error(const Message& to, const std::string& name, const std::string& text);
    void signal(const std::string& path, const std::string& iface, const std::string& member,
                std::vector<Val> args);

    // Incoming method calls go to `on_call`; return false from process() on disconnect.
    void set_call_handler(Handler h) { on_call_ = std::move(h); }
    // Reads and dispatches whatever is available within timeout_ms. False when the bus closed.
    bool process(int timeout_ms);

private:
    void send(Message& m);
    bool read_message(Message& out, int timeout_ms);
    bool fill(int timeout_ms);

    int fd_ = -1;
    uint32_t next_serial_ = 1;
    std::string unique_;
    std::vector<uint8_t> in_;
    std::deque<int> in_fds_;
    std::deque<Message> pending_;  // messages read while waiting for a reply
    Handler on_call_;
};

}  // namespace ctt::dbus
