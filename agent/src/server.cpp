#include <algorithm>
#include <exception>
#include <stdexcept>
#include <string>
#include <thread>

#include "ctt/clock.hpp"
#include "ctt/output.hpp"
#include "ctt/session.hpp"

namespace ctt {
namespace {

void send_error(Stream& s, const std::string& msg) {
    try {
        send_control(s, ctp::MsgType::Error, 0, reinterpret_cast<const uint8_t*>(msg.data()),
                     msg.size());
    } catch (...) {
    }
}

// First frame of a session must be START. On datagram links stale frames from
// an earlier session may arrive first; skip them.
ctp::StartParams read_start(Stream& s, FrameReader& rd, bool lossy) {
    ctp::Header h;
    const uint8_t* p = nullptr;
    for (int skipped = 0;; ++skipped) {
        if (lossy) {
            auto st = rd.next_for(h, p, 5000);
            if (st == FrameReader::Status::Timeout) throw std::runtime_error("no START received");
            if (st == FrameReader::Status::Eof) throw std::runtime_error("peer closed before START");
        } else if (!rd.next(h, p)) {
            throw std::runtime_error("peer closed before START");
        }
        if (h.type == ctp::MsgType::Start) break;
        if (!lossy || skipped > 10000) {
            send_error(s, "expected START");
            throw std::runtime_error("expected START, got " + std::string(ctp::to_string(h.type)));
        }
    }
    ctp::StartParams sp;
    if (!decode_start(p, h.payload_len, sp)) {
        send_error(s, "invalid START parameters");
        throw std::runtime_error("invalid START parameters");
    }
    if (lossy && sp.block_size + ctp::kHeaderSize > 65507) {
        send_error(s, "block too large for a datagram");
        throw std::runtime_error("block too large for a datagram");
    }
    return sp;
}

void send_stats(Stream& s, const ctp::StatsPayload& st) {
    uint8_t buf[ctp::kStatsSize];
    ctp::encode_stats(st, buf);
    send_control(s, ctp::MsgType::Stats, 0, buf, sizeof(buf));
}

ctp::StatsPayload serve_session(Stream& s, const AgentOptions& o) {
    const bool lossy = s.lossy();
    const int idle_ms = lossy_idle_ms(o.topts);
    FrameReader rd(s);
    ctp::StartParams sp = read_start(s, rd, lossy);
    const bool verify = sp.flags & ctp::kStartVerify;

    Json jp;
    jp.add("event", "session_params")
        .add("mode", ctp::to_string(sp.mode))
        .add("block", sp.block_size)
        .add("duration_s", sp.duration_ms / 1000.0)
        .add("rate_bps", sp.rate_bps)
        .add("verify", verify)
        .add("lossy", lossy);
    out::emit(jp, out::strprintf("  mode=%s block=%u duration=%.1fs rate=%llu verify=%s%s",
                                 ctp::to_string(sp.mode), sp.block_size, sp.duration_ms / 1000.0,
                                 (unsigned long long)sp.rate_bps, verify ? "on" : "off",
                                 lossy ? " (datagram)" : ""));

    send_control(s, ctp::MsgType::StartAck);
    const uint64_t t0 = now_ns();

    ctp::StatsPayload st;
    RxCounters rx;
    TxCounters tx;
    IntervalReporter rep_rx("up", "rx", o.interval_s, t0);
    IntervalReporter rep_tx("down", "tx", o.interval_s, t0);
    bool stop_lost = false;

    switch (sp.mode) {
        case ctp::Mode::Tx:
            rx.begin(t0);
            stop_lost = !receive_data(rd, verify, rx, rep_rx, lossy, idle_ms, &s);
            st.has_rx = true;
            break;
        case ctp::Mode::Rx:
            send_data(s, sp, t0, tx, rep_tx, nullptr);
            st.has_tx = true;
            break;
        case ctp::Mode::Bidir: {
            std::atomic<bool> abort{false};
            std::exception_ptr tx_err;
            std::thread th([&] {
                try {
                    send_data(s, sp, t0, tx, rep_tx, &abort);
                } catch (...) {
                    tx_err = std::current_exception();
                }
            });
            try {
                rx.begin(t0);
                stop_lost = !receive_data(rd, verify, rx, rep_rx, lossy, idle_ms, &s);
            } catch (...) {
                abort = true;
                th.join();
                throw;
            }
            th.join();
            if (tx_err) std::rethrow_exception(tx_err);
            st.has_rx = st.has_tx = true;
            break;
        }
        case ctp::Mode::Echo: {
            ctp::Header h;
            const uint8_t* p = nullptr;
            for (;;) {
                if (lossy) {
                    auto r = rd.next_for(h, p, idle_ms);
                    if (r == FrameReader::Status::Timeout) {
                        stop_lost = true;
                        break;
                    }
                    if (r == FrameReader::Status::Eof) throw std::runtime_error("peer closed during echo");
                } else if (!rd.next(h, p)) {
                    throw std::runtime_error("peer closed during echo");
                }
                if (h.type == ctp::MsgType::Stop) break;
                if (h.type == ctp::MsgType::EchoReq) {
                    ctp::Header r = h;
                    r.type = ctp::MsgType::EchoRsp;
                    send_frame(s, r, p, h.payload_len);
                    ++st.echoed;
                } else if (lossy) {
                    if (h.type == ctp::MsgType::Start) send_control(s, ctp::MsgType::StartAck);
                } else {
                    throw std::runtime_error(std::string("unexpected ") + ctp::to_string(h.type));
                }
            }
            break;
        }
    }

    if (st.has_rx) {
        st.rx_frames = rx.frames;
        st.rx_bytes = rx.bytes;
        st.rx_lost = rx.lost;
        st.rx_out_of_order = rx.out_of_order;
        st.rx_crc_errors = rx.crc_errors;
        st.rx_duration_ns = rx.duration_ns();
        st.rx_jitter_ns = uint64_t(rx.jitter_ns);
    }
    if (st.has_tx) {
        st.tx_frames = tx.frames;
        st.tx_bytes = tx.bytes;
        st.tx_duration_ns = tx.duration_ns();
    }
    send_stats(s, st);
    if (stop_lost) {
        Json j;
        j.add("event", "warning").add("message", "STOP not received, ended after idle timeout");
        out::emit(j, "  warning: STOP not received, ended after idle timeout");
    }

    if (lossy) {
        // Linger: if STATS was lost the client re-sends STOP; answer it again.
        ctp::Header h;
        const uint8_t* p = nullptr;
        uint64_t deadline = now_ns() + 1500ull * 1000000ull;
        while (now_ns() < deadline) {
            int left = int((deadline - now_ns()) / 1000000ull);
            if (rd.next_for(h, p, std::max(left, 1)) != FrameReader::Status::Frame) break;
            if (h.type == ctp::MsgType::Stop) send_stats(s, st);
        }
    } else {
        // Graceful close: let the client read STATS and close first.
        s.shutdown_write();
        try {
            uint8_t drain[256];
            while (s.read_some(drain, sizeof(drain)) > 0) {
            }
        } catch (...) {
        }
    }
    return st;
}

}  // namespace

int run_server(const AgentOptions& o) {
    Transport* t = find_transport(o.transport);
    if (!t) throw std::runtime_error("unknown transport: " + o.transport);
    auto lis = t->listen(o.endpoint.empty() ? t->default_listen_endpoint() : o.endpoint, o.topts);

    Json jl;
    jl.add("event", "listening").add("transport", t->name()).add("endpoint", lis->local());
    lis->describe(jl);
    out::emit(jl, out::strprintf("ctt-agent server listening on %s %s", t->name(),
                                 lis->local().c_str()));

    int rc = 0;
    for (;;) {
        int to = o.accept_timeout_s > 0 ? int(o.accept_timeout_s * 1000) : -1;
        auto s = lis->accept(to);
        if (!s) {
            Json j;
            j.add("event", "error").add("message", "accept timeout");
            out::emit(j, "accept timeout: no client connected");
            return 2;
        }
        Json js;
        js.add("event", "session_start").add("peer", s->peer());
        out::emit(js, "session from " + s->peer());
        try {
            auto st = serve_session(*s, o);
            Json je;
            je.add("event", "session_end").add("ok", true);
            if (st.has_rx)
                je.add("rx", rx_json(st.rx_frames, st.rx_bytes, st.rx_lost, st.rx_out_of_order,
                                     st.rx_crc_errors, st.rx_duration_ns, double(st.rx_jitter_ns)));
            if (st.has_tx) je.add("tx", tx_json(st.tx_frames, st.tx_bytes, st.tx_duration_ns));
            if (st.echoed) je.add("echoed", st.echoed);
            std::string text = "session done:";
            if (st.has_rx)
                text += out::strprintf(" rx %.3f Mbps (lost=%llu crc_err=%llu)",
                                       mbps(st.rx_bytes, st.rx_duration_ns),
                                       (unsigned long long)st.rx_lost,
                                       (unsigned long long)st.rx_crc_errors);
            if (st.has_tx)
                text += out::strprintf(" tx %.3f Mbps", mbps(st.tx_bytes, st.tx_duration_ns));
            if (st.echoed) text += out::strprintf(" echoed=%llu", (unsigned long long)st.echoed);
            out::emit(je, text);
            rc = 0;
        } catch (const std::exception& e) {
            Json je;
            je.add("event", "session_end").add("ok", false).add("message", e.what());
            out::emit(je, std::string("session failed: ") + e.what());
            rc = 1;
        }
        if (o.once) break;
    }
    return rc;
}

}  // namespace ctt
