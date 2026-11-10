#include <exception>
#include <stdexcept>
#include <string>
#include <thread>

#include "ctt/clock.hpp"
#include "ctt/output.hpp"
#include "ctt/pattern.hpp"
#include "ctt/session.hpp"

namespace ctt {
namespace {

ctp::StatsPayload read_stats(FrameReader& rd) {
    ctp::Header h;
    const uint8_t* p = nullptr;
    for (;;) {
        if (!rd.next(h, p)) throw std::runtime_error("server closed before sending STATS");
        if (h.type == ctp::MsgType::Stats) {
            ctp::StatsPayload st;
            if (!ctp::decode_stats(p, h.payload_len, st)) throw std::runtime_error("bad STATS");
            return st;
        }
        if (h.type == ctp::MsgType::Error)
            throw std::runtime_error("server error: " +
                                     std::string(reinterpret_cast<const char*>(p), h.payload_len));
        throw std::runtime_error(std::string("expected STATS, got ") + ctp::to_string(h.type));
    }
}

void run_echo(Stream& s, FrameReader& rd, const ctp::StartParams& sp, const AgentOptions& o,
              uint64_t t0, RttStats& rtt) {
    PatternSource pat(sp.block_size, false);
    const uint64_t end = t0 + uint64_t(sp.duration_ms) * 1000000ull;
    const uint64_t gap = uint64_t(sp.echo_interval_us) * 1000ull;
    const uint64_t report_ns = o.interval_s > 0 ? uint64_t(o.interval_s * 1e9) : 0;
    uint64_t next_send = t0, next_report = t0 + report_ns, last_report = t0;
    uint64_t win_n = 0, win_max = 0;
    double win_sum = 0;

    ctp::Header req;
    req.type = ctp::MsgType::EchoReq;
    ctp::Header h;
    const uint8_t* p = nullptr;
    for (uint32_t seq = 0;; ++seq) {
        uint64_t now = now_ns();
        if (sp.echo_count ? seq >= sp.echo_count : now >= end) break;
        if (now < next_send) {
            sleep_ns(next_send - now);
            now = now_ns();
        }
        req.seq = seq;
        req.ts_ns = now;
        send_frame(s, req, pat.payload(seq), sp.block_size);
        if (!rd.next(h, p)) throw std::runtime_error("server closed during echo");
        uint64_t back = now_ns();
        if (h.type != ctp::MsgType::EchoRsp || h.seq != seq)
            throw std::runtime_error("echo response mismatch (seq " + std::to_string(seq) + ")");
        uint64_t r = back - h.ts_ns;
        rtt.add(r);
        ++win_n;
        win_sum += double(r);
        if (r > win_max) win_max = r;
        if (report_ns && back >= next_report) {
            Json j;
            j.add("event", "interval")
                .add("dir", "rtt")
                .add("role", "echo")
                .add("t_start", double(last_report - t0) / 1e9)
                .add("t_end", double(back - t0) / 1e9)
                .add("samples", win_n)
                .add("avg_us", win_sum / double(win_n) / 1000.0)
                .add("max_us", double(win_max) / 1000.0);
            out::emit(j, out::strprintf("[rtt echo] %6.2f-%6.2f s  samples=%llu avg=%.1f us max=%.1f us",
                                        double(last_report - t0) / 1e9, double(back - t0) / 1e9,
                                        (unsigned long long)win_n,
                                        win_sum / double(win_n) / 1000.0, double(win_max) / 1000.0));
            win_n = 0;
            win_sum = 0;
            win_max = 0;
            last_report = back;
            while (next_report <= back) next_report += report_ns;
        }
        next_send += gap;
        if (next_send < back) next_send = back;  // never burst to catch up
    }
    send_control(s, ctp::MsgType::Stop);
}

}  // namespace

int run_client(const AgentOptions& o) {
    Transport* t = find_transport(o.transport);
    if (!t) throw std::runtime_error("unknown transport: " + o.transport);

    TransportOptions topts = o.topts;
    if (o.mode == ctp::Mode::Echo) topts.nodelay = true;

    ctp::StartParams sp;
    sp.mode = o.mode;
    sp.block_size = o.block ? o.block : (o.mode == ctp::Mode::Echo ? 64 : t->default_block());
    sp.duration_ms = uint32_t(o.duration_s * 1000.0);
    sp.flags = o.verify ? ctp::kStartVerify : 0;
    sp.rate_bps = o.rate_bps;
    sp.echo_interval_us = o.echo_interval_us;
    sp.echo_count = o.echo_count;

    auto s = t->connect(o.endpoint, topts);
    Json jc;
    jc.add("event", "connected").add("transport", t->name()).add("peer", s->peer());
    out::emit(jc, out::strprintf("connected to %s %s  mode=%s block=%u duration=%.1fs", t->name(),
                                 s->peer().c_str(), ctp::to_string(sp.mode), sp.block_size,
                                 sp.duration_ms / 1000.0));

    uint8_t sbuf[ctp::kStartSize];
    ctp::encode_start(sp, sbuf);
    send_control(*s, ctp::MsgType::Start, 0, sbuf, sizeof(sbuf));

    FrameReader rd(*s);
    ctp::Header h;
    const uint8_t* p = nullptr;
    if (!rd.next(h, p)) throw std::runtime_error("server closed before START_ACK");
    if (h.type == ctp::MsgType::Error)
        throw std::runtime_error("server rejected test: " +
                                 std::string(reinterpret_cast<const char*>(p), h.payload_len));
    if (h.type != ctp::MsgType::StartAck)
        throw std::runtime_error(std::string("expected START_ACK, got ") + ctp::to_string(h.type));

    const uint64_t t0 = now_ns();
    RxCounters rx;
    TxCounters tx;
    RttStats rtt;
    IntervalReporter rep_tx("up", "tx", o.interval_s, t0);
    IntervalReporter rep_rx("down", "rx", o.interval_s, t0);
    ctp::StatsPayload remote;

    switch (sp.mode) {
        case ctp::Mode::Tx:
            send_data(*s, sp, t0, tx, rep_tx, nullptr);
            break;
        case ctp::Mode::Rx:
            rx.begin(t0);
            receive_data(rd, o.verify, rx, rep_rx);
            break;
        case ctp::Mode::Bidir: {
            std::atomic<bool> abort{false};
            std::exception_ptr tx_err;
            std::thread th([&] {
                try {
                    send_data(*s, sp, t0, tx, rep_tx, &abort);
                } catch (...) {
                    tx_err = std::current_exception();
                }
            });
            try {
                rx.begin(t0);
                receive_data(rd, o.verify, rx, rep_rx);
            } catch (...) {
                abort = true;
                th.join();
                throw;
            }
            th.join();
            if (tx_err) std::rethrow_exception(tx_err);
            break;
        }
        case ctp::Mode::Echo:
            run_echo(*s, rd, sp, o, t0, rtt);
            break;
    }
    remote = read_stats(rd);

    // ---- result ----
    Json res;
    res.add("event", "result")
        .add("ok", true)
        .add("transport", t->name())
        .add("endpoint", o.endpoint)
        .add("peer", s->peer())
        .add("mode", ctp::to_string(sp.mode))
        .add("block", sp.block_size)
        .add("duration_s", sp.duration_ms / 1000.0)
        .add("rate_bps", sp.rate_bps)
        .add("verify", o.verify);

    std::string text = out::strprintf(
        "\n=== CTT result: %s %s  mode=%s block=%u duration=%.1fs verify=%s ===\n", t->name(),
        s->peer().c_str(), ctp::to_string(sp.mode), sp.block_size, sp.duration_ms / 1000.0,
        o.verify ? "on" : "off");
    auto dir_line = [](const char* label, double rx_mbps, double tx_mbps, uint64_t lost,
                       uint64_t crc, uint64_t bytes) {
        return out::strprintf(
            "  %-26s %10.3f Mbps (receiver)  %10.3f Mbps (sender)  bytes=%llu lost=%llu "
            "crc_err=%llu\n",
            label, rx_mbps, tx_mbps, (unsigned long long)bytes, (unsigned long long)lost,
            (unsigned long long)crc);
    };

    if (sp.mode == ctp::Mode::Tx || sp.mode == ctp::Mode::Bidir) {
        Json up;
        up.add("sender", tx.to_json());
        if (remote.has_rx)
            up.add("receiver", rx_json(remote.rx_frames, remote.rx_bytes, remote.rx_lost,
                                       remote.rx_out_of_order, remote.rx_crc_errors,
                                       remote.rx_duration_ns));
        res.add("up", up);
        text += dir_line("up   (client -> server)", mbps(remote.rx_bytes, remote.rx_duration_ns),
                         mbps(tx.bytes, tx.duration_ns()), remote.rx_lost, remote.rx_crc_errors,
                         remote.rx_bytes);
    }
    if (sp.mode == ctp::Mode::Rx || sp.mode == ctp::Mode::Bidir) {
        Json down;
        if (remote.has_tx)
            down.add("sender", tx_json(remote.tx_frames, remote.tx_bytes, remote.tx_duration_ns));
        down.add("receiver", rx.to_json());
        res.add("down", down);
        text += dir_line("down (server -> client)", mbps(rx.bytes, rx.duration_ns()),
                         mbps(remote.tx_bytes, remote.tx_duration_ns), rx.lost, rx.crc_errors,
                         rx.bytes);
    }
    if (sp.mode == ctp::Mode::Echo) {
        res.add("rtt", rtt.to_json());
        res.add("echoed_by_server", remote.echoed);
        text += "  " + rtt.to_text() + "\n";
    }
    Json link;
    s->link_info(link);
    res.add("link", link);
    out::emit(res, text);
    return 0;
}

}  // namespace ctt
