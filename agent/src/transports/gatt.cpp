// BLE GATT (Bluetooth Low Energy), server side only: the board is a GATT peripheral.
//
// The agent registers its own GATT service with BlueZ over D-Bus (next to whatever
// another daemon of the product exports) and an LE advertisement carrying the service UUID.
// A PC (Windows WinRT / Linux BlueZ, e.g. through `bleak`) connects and runs CTP on it:
//   rx  (PC -> board)   write-without-response   BlueZ hands over the data with AcquireWrite
//   tx  (board -> PC)   notify                    BlueZ hands over a socket with AcquireNotify
//   info                read                      "ctt-agent <version>"
// The PC must enable notifications on tx before it writes the first CTP frame.
//
// Endpoint:  listen "" or "hci0"   (adapter)
// Options:   -o adapter=hci0   -o name=CTT-<host> (advertised local name)   -o adv=0 (no advertisement)
#include <sys/socket.h>
#include <unistd.h>

#include <algorithm>
#include <atomic>
#include <cerrno>
#include <chrono>
#include <condition_variable>
#include <cstring>
#include <memory>
#include <mutex>
#include <stdexcept>
#include <string>
#include <thread>

#include "../dbus.hpp"
#include "ctt/json.hpp"
#include "ctt/version.hpp"
#include "packet_stream.hpp"

namespace ctt {

// Public UUIDs of the CTT GATT service (also in ctt/protocol/ble.py on the PC).
const char* const kGattService = "a0c70001-8f5e-4d3a-9c2b-1f0e5d4c3b2a";
const char* const kGattRx = "a0c70002-8f5e-4d3a-9c2b-1f0e5d4c3b2a";
const char* const kGattTx = "a0c70003-8f5e-4d3a-9c2b-1f0e5d4c3b2a";
const char* const kGattInfo = "a0c70004-8f5e-4d3a-9c2b-1f0e5d4c3b2a";

namespace {

namespace d = dbus;

const std::string kRoot = "/ctt/gatt";
const std::string kSvc = kRoot + "/service0";
const std::string kRxPath = kSvc + "/rx";
const std::string kTxPath = kSvc + "/tx";
const std::string kInfoPath = kSvc + "/info";
const std::string kAdvPath = kRoot + "/adv";
const char* const kBluez = "org.bluez";
const char* const kProps = "org.freedesktop.DBus.Properties";

// "/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF" -> "AA:BB:CC:DD:EE:FF"
std::string device_address(const std::string& p) {
    auto k = p.rfind("dev_");
    if (k == std::string::npos) return p.empty() ? "?" : p;
    std::string a = p.substr(k + 4);
    for (char& c : a)
        if (c == '_') c = ':';
    return a;
}

std::string option(const TransportOptions& o, const std::string& k, const std::string& def) {
    auto it = o.extra.find(k);
    return it == o.extra.end() ? def : it->second;
}

class GattStream : public Stream {
public:
    GattStream(int rfd, int wfd, size_t mtu, std::string peer)
        : rd_(rfd, peer, mtu, 517), wr_(wfd, peer, mtu > 3 ? mtu - 3 : 20, 517), mtu_(mtu),
          peer_(std::move(peer)) {}

    ssize_t read_some(uint8_t* buf, size_t len) override { return rd_.read_some(buf, len); }
    ssize_t writev_some(const struct iovec* iov, int iovcnt) override { return wr_.writev_some(iov, iovcnt); }
    void shutdown_write() override { wr_.shutdown_write(); }
    std::string peer() const override { return peer_; }
    bool wait_readable(int timeout_ms) override { return rd_.wait_readable(timeout_ms); }
    void link_info(Json& j) const override {
        j.add("att_mtu", uint64_t(mtu_)).add("notify_payload", uint64_t(wr_.omtu()));
    }

private:
    PacketStream rd_;
    PacketStream wr_;
    size_t mtu_;
    std::string peer_;
};

class GattListener : public Listener {
public:
    GattListener(const std::string& adapter, const TransportOptions& o) : adapter_(adapter) {
        name_ = option(o, "name", "");
        if (name_.empty()) {
            char host[64] = "board";
            ::gethostname(host, sizeof(host) - 1);
            name_ = std::string("CTT-") + host;
            if (name_.size() > 20) name_.resize(20);  // keep the advertisement small
        }
        bus_.set_call_handler([this](const d::Message& m) { on_call(m); });
        const std::string apath = "/org/bluez/" + adapter_;
        try {
            bus_.call(kBluez, apath, "org.bluez.GattManager1", "RegisterApplication",
                      {d::path(kRoot), d::array("{sv}")});
        } catch (const std::exception& e) {
            throw std::runtime_error(std::string("GATT: cannot register the CTT service with BlueZ: ") +
                                     e.what() + " (bluetoothd running? adapter " + adapter_ + " powered?)");
        }
        try {  // adapter address = the board's LE public address the PC connects to
            d::Message r = bus_.call(kBluez, apath, kProps, "Get",
                                     {d::str("org.bluez.Adapter1"), d::str("Address")}, 3000);
            if (!r.body.empty() && !r.body[0].v.empty()) bdaddr_ = r.body[0].v[0].s;
        } catch (...) {
        }
        if (option(o, "adv", "1") != "0") {
            try {
                bus_.call(kBluez, apath, "org.bluez.LEAdvertisingManager1", "RegisterAdvertisement",
                          {d::path(kAdvPath), d::array("{sv}")});
                advertising_ = true;
            } catch (const std::exception& e) {
                adv_error_ = e.what();  // e.g. all advertising slots in use: PC may still connect by address
            }
        }
        thread_ = std::thread([this] {
            try {
                while (!stop_ && bus_.process(200)) {
                }
            } catch (const std::exception& e) {
                std::lock_guard<std::mutex> lk(mu_);
                bus_error_ = e.what();
            }
            std::lock_guard<std::mutex> lk(mu_);
            bus_closed_ = true;
            cv_.notify_all();
        });
    }

    ~GattListener() override {
        stop_ = true;
        if (thread_.joinable()) thread_.join();
        const std::string apath = "/org/bluez/" + adapter_;
        try {
            if (advertising_)
                bus_.call(kBluez, apath, "org.bluez.LEAdvertisingManager1", "UnregisterAdvertisement",
                          {d::path(kAdvPath)}, 2000);
            bus_.call(kBluez, apath, "org.bluez.GattManager1", "UnregisterApplication", {d::path(kRoot)}, 2000);
        } catch (...) {
        }
        reset_fds();
    }

    std::unique_ptr<Stream> accept(int timeout_ms) override {
        std::unique_lock<std::mutex> lk(mu_);
        auto ready = [this] { return (rfd_ >= 0 && wfd_ >= 0) || bus_closed_; };
        if (timeout_ms < 0) cv_.wait(lk, ready);
        else if (!cv_.wait_for(lk, std::chrono::milliseconds(timeout_ms), ready)) return nullptr;
        if (rfd_ < 0 || wfd_ < 0)
            throw std::runtime_error("GATT: D-Bus connection lost" + (bus_error_.empty() ? "" : ": " + bus_error_));
        auto s = std::make_unique<GattStream>(rfd_, wfd_, mtu_, peer_);
        rfd_ = wfd_ = -1;  // owned by the stream now; the next connection acquires new ones
        mtu_ = 0;
        peer_ = "?";
        return s;
    }

    std::string local() const override { return adapter_ + " service " + kGattService; }

    void describe(Json& j) const override {
        j.add("service_uuid", kGattService)
            .add("rx_uuid", kGattRx)
            .add("tx_uuid", kGattTx)
            .add("adapter", adapter_)
            .add("local_name", name_)
            .add("advertising", advertising_);
        if (!bdaddr_.empty()) j.add("bdaddr", bdaddr_);
        if (!adv_error_.empty()) j.add("advertising_error", adv_error_);
    }

private:
    void reset_fds() {
        if (rfd_ >= 0) ::close(rfd_);
        if (wfd_ >= 0) ::close(wfd_);
        if (feed_fd_ >= 0) ::close(feed_fd_);
        rfd_ = wfd_ = feed_fd_ = -1;
    }

    d::Val props(const std::string& p, const std::string& iface) {
        std::vector<d::Val> v;
        auto add = [&](const char* k, const d::Val& x) { v.push_back(d::sv(k, x)); };
        if (p == kSvc && iface == "org.bluez.GattService1") {
            add("UUID", d::str(kGattService));
            add("Primary", d::boolean(true));
        } else if (iface == "org.bluez.GattCharacteristic1" && (p == kRxPath || p == kTxPath || p == kInfoPath)) {
            add("Service", d::path(kSvc));
            if (p == kRxPath) {
                add("UUID", d::str(kGattRx));
                add("Flags", d::strings({"write-without-response", "write"}));
                add("WriteAcquired", d::boolean(false));
            } else if (p == kTxPath) {
                add("UUID", d::str(kGattTx));
                add("Flags", d::strings({"notify"}));
                add("NotifyAcquired", d::boolean(false));
            } else {
                add("UUID", d::str(kGattInfo));
                add("Flags", d::strings({"read"}));
            }
        } else if (p == kAdvPath && iface == "org.bluez.LEAdvertisement1") {
            add("Type", d::str("peripheral"));
            add("ServiceUUIDs", d::strings({kGattService}));
            add("LocalName", d::str(name_));
        }
        return d::array("{sv}", std::move(v));
    }

    // Only for tools (busctl, dbus-python); BlueZ itself reads GetManagedObjects.
    static std::string introspect(const std::string& p) {
        std::string x = "<!DOCTYPE node PUBLIC \"-//freedesktop//DTD D-BUS Object Introspection 1.0//EN\" "
                        "\"http://www.freedesktop.org/standards/dbus/1.0/introspect.dtd\">\n<node>"
                        "<interface name=\"org.freedesktop.DBus.Introspectable\"><method name=\"Introspect\">"
                        "<arg type=\"s\" direction=\"out\"/></method></interface>"
                        "<interface name=\"org.freedesktop.DBus.Properties\"><method name=\"GetAll\">"
                        "<arg type=\"s\" direction=\"in\"/><arg type=\"a{sv}\" direction=\"out\"/></method>"
                        "</interface>";
        if (p == kRoot)
            x += "<interface name=\"org.freedesktop.DBus.ObjectManager\"><method name=\"GetManagedObjects\">"
                 "<arg type=\"a{oa{sa{sv}}}\" direction=\"out\"/></method></interface>"
                 "<node name=\"service0\"/><node name=\"adv\"/>";
        if (p == kSvc) x += "<node name=\"rx\"/><node name=\"tx\"/><node name=\"info\"/>";
        if (p == kRxPath || p == kTxPath || p == kInfoPath)
            x += "<interface name=\"org.bluez.GattCharacteristic1\">"
                 "<method name=\"ReadValue\"><arg type=\"a{sv}\" direction=\"in\"/>"
                 "<arg type=\"ay\" direction=\"out\"/></method>"
                 "<method name=\"WriteValue\"><arg type=\"ay\" direction=\"in\"/>"
                 "<arg type=\"a{sv}\" direction=\"in\"/></method>"
                 "<method name=\"AcquireWrite\"><arg type=\"a{sv}\" direction=\"in\"/>"
                 "<arg type=\"h\" direction=\"out\"/><arg type=\"q\" direction=\"out\"/></method>"
                 "<method name=\"AcquireNotify\"><arg type=\"a{sv}\" direction=\"in\"/>"
                 "<arg type=\"h\" direction=\"out\"/><arg type=\"q\" direction=\"out\"/></method>"
                 "<method name=\"StartNotify\"/><method name=\"StopNotify\"/></interface>";
        return x + "</node>";
    }

    d::Val managed_objects() {
        std::vector<d::Val> objs;
        auto obj = [&](const std::string& p, const char* iface) {
            objs.push_back(d::entry(d::path(p), d::array("{sa{sv}}", {d::entry(d::str(iface), props(p, iface))})));
        };
        obj(kSvc, "org.bluez.GattService1");
        obj(kRxPath, "org.bluez.GattCharacteristic1");
        obj(kTxPath, "org.bluez.GattCharacteristic1");
        obj(kInfoPath, "org.bluez.GattCharacteristic1");
        return d::array("{oa{sa{sv}}}", std::move(objs));
    }

    // One end goes to BlueZ, the other one stays here. SEQPACKET keeps ATT value boundaries.
    int pair_for_bluez(int& keep) {
        int sv[2];
        if (::socketpair(AF_UNIX, SOCK_SEQPACKET | SOCK_CLOEXEC, 0, sv) < 0)
            throw std::runtime_error(std::string("socketpair: ") + std::strerror(errno));
        keep = sv[0];
        return sv[1];
    }

    void acquired(const d::Message& m, bool write_side) {
        const d::Val* opts = m.body.empty() ? nullptr : &m.body[0];
        uint16_t mtu = 23;
        if (opts)
            if (const d::Val* x = opts->get("mtu")) mtu = uint16_t(x->u);
        std::string dev;
        if (opts)
            if (const d::Val* x = opts->get("device")) dev = x->s;
        int keep = -1, give = pair_for_bluez(keep);
        {
            std::lock_guard<std::mutex> lk(mu_);
            int& slot = write_side ? rfd_ : wfd_;
            if (slot >= 0) ::close(slot);  // a stale fd from a connection that never started
            slot = keep;
            mtu_ = mtu_ ? std::min<size_t>(mtu_, mtu) : mtu;
            if (!dev.empty()) peer_ = device_address(dev);
            if (rfd_ >= 0 && wfd_ >= 0) cv_.notify_all();
        }
        bus_.reply(m, {d::fd_index(0), d::u16(mtu)}, {give});
        ::close(give);
    }

    void on_call(const d::Message& m) {
        try {
            if (m.interface == "org.freedesktop.DBus.Introspectable" && m.member == "Introspect") {
                bus_.reply(m, {d::str(introspect(m.path))});
            } else if (m.interface == "org.freedesktop.DBus.ObjectManager" && m.member == "GetManagedObjects") {
                bus_.reply(m, {managed_objects()});
            } else if (m.interface == kProps && m.member == "GetAll" && !m.body.empty()) {
                bus_.reply(m, {props(m.path, m.body[0].s)});
            } else if (m.interface == kProps && m.member == "Get" && m.body.size() >= 2) {
                d::Val all = props(m.path, m.body[0].s);
                const d::Val* x = all.get(m.body[1].s);
                if (x) bus_.reply(m, {d::variant(*x)});
                else bus_.error(m, "org.freedesktop.DBus.Error.InvalidArgs", "no such property");
            } else if (m.member == "AcquireWrite" && m.path == kRxPath) {
                acquired(m, true);
            } else if (m.member == "AcquireNotify" && m.path == kTxPath) {
                acquired(m, false);
            } else if (m.member == "WriteValue" && m.path == kRxPath && !m.body.empty()) {
                // Fallback when BlueZ does not use AcquireWrite: feed the value into our read socket.
                std::lock_guard<std::mutex> lk(mu_);
                if (feed_fd_ < 0) {
                    if (rfd_ >= 0) ::close(rfd_);
                    int keep = -1;
                    feed_fd_ = pair_for_bluez(keep);
                    rfd_ = keep;
                    if (const d::Val* x = m.body.size() > 1 ? m.body[1].get("device") : nullptr)
                        peer_ = device_address(x->s);
                    if (rfd_ >= 0 && wfd_ >= 0) cv_.notify_all();
                }
                std::string data;
                for (const auto& b : m.body[0].v) data += char(b.u);
                ::send(feed_fd_, data.data(), data.size(), MSG_NOSIGNAL);
                bus_.reply(m);
            } else if (m.member == "ReadValue" && m.path == kInfoPath) {
                std::string info = std::string("ctt-agent ") + CTT_VERSION;
                bus_.reply(m, {d::bytes(reinterpret_cast<const uint8_t*>(info.data()), info.size())});
            } else if (m.member == "StartNotify" || m.member == "StopNotify" || m.member == "Release" ||
                       m.member == "Confirm") {
                bus_.reply(m);
            } else {
                bus_.error(m, "org.freedesktop.DBus.Error.UnknownMethod", m.interface + "." + m.member);
            }
        } catch (const std::exception& e) {
            bus_.error(m, "org.bluez.Error.Failed", e.what());
        }
        for (int f : m.fds) ::close(f);
    }

    std::string adapter_;
    std::string name_;
    d::Connection bus_;
    std::thread thread_;
    std::atomic<bool> stop_{false};
    std::mutex mu_;
    std::condition_variable cv_;
    int rfd_ = -1, wfd_ = -1, feed_fd_ = -1;
    size_t mtu_ = 0;
    std::string peer_ = "?";
    bool advertising_ = false;
    bool bus_closed_ = false;
    std::string adv_error_, bus_error_, bdaddr_;
};

class GattTransport : public Transport {
public:
    const char* name() const override { return "gatt"; }
    const char* description() const override {
        return "BLE GATT peripheral via BlueZ D-Bus (server only). Endpoint: adapter (hci0); "
               "-o name=<adv name> -o adv=0";
    }
    std::string default_listen_endpoint() const override { return "hci0"; }
    uint32_t default_block() const override { return 244; }

    std::unique_ptr<Listener> listen(const std::string& endpoint, const TransportOptions& o) override {
        std::string adapter = option(o, "adapter", "");
        if (adapter.empty()) adapter = endpoint.empty() || endpoint == "/" ? "hci0" : endpoint;
        if (!adapter.empty() && adapter[0] == '/') adapter = adapter.substr(1);
        return std::make_unique<GattListener>(adapter, o);
    }

    std::unique_ptr<Stream> connect(const std::string&, const TransportOptions&) override {
        throw std::runtime_error("gatt: the agent is the GATT server only; run the client on the PC "
                                 "(ctt-agent-console --link ble-gatt)");
    }
};

}  // namespace

std::unique_ptr<Transport> make_gatt_transport() { return std::make_unique<GattTransport>(); }

}  // namespace ctt
