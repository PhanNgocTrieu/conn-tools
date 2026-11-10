#include <algorithm>
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

constexpr uint64_t kDefaultDatagramRate = 10000000;  // 10 Mbit/s, like iperf's UDP default idea

[[noreturn]] void throw_peer_error(const uint8_t* p, uint32_t len) {
    throw std::runtime_error("server error: " + std::string(reinterpret_cast<const char*>(p), len));
}

// START -> START_ACK. On lossy links START is repeated, and DATA / ECHO_RSP
// arriving first counts as an implicit ACK (the frame is kept for the test).
void handshake(Stream& s, FrameReader& rd, const ctp::StartParams& sp) {
    uint8_t sbuf[ctp::kStartSize];
    ctp::encode_start(sp, sbuf);
    const bool lossy = s.lossy();
    ctp::Header h;
    const uint8_t* p = nullptr;
    for (int attempt = 0;; ++attempt) {
        send_control(s, ctp::MsgType::Start, 0, sbuf, sizeof(sbuf));
        if (!lossy) {
            if (!rd.next(h, p)) throw std::runtime_error("server closed before START_ACK");
        } else {
            auto st = rd.next_for(h, p, kCtlRetryMs);
            if (st == FrameReader::Status::Timeout) {
                if (attempt + 1 >= kCtlRetries)
                    throw std::runtime_error("no START_ACK from server (is it listening? firewall?)");
                continue;
            }
            if (st == FrameReader::Status::Eof) throw std::runtime_error("server closed before START_ACK");
        }
        if (h.type == ctp::MsgType::StartAck) return;
        if (h.type == ctp::MsgType::Error) throw_peer_error(p, h.payload_len);
        if (lossy && (h.type == ctp::MsgType::Data || h.type == ctp::MsgType::EchoRsp)) {
            rd.push_back();
            return;
        }
        if (!lossy)
            throw std::runtime_error(std::string("expected START_ACK, got ") + ctp::to_string(h.type));
    }
}

// Reads the server's STATS. On lossy links, re-sends STOP until it arrives.
ctp::StatsPayload fetch_stats(Stream& s, FrameReader& rd) {
    const bool lossy = s.lossy();
    ctp::Header h;
    const uint8_t* p = nullptr;
    int timeouts = 0;
    for (;;) {
        if (!lossy) {
            if (!rd.next(h, p)) throw std::runtime_error("server closed before sending STATS");
        } else {
            auto st = rd.next_for(h, p, kCtlRetryMs);
            if (st == FrameReader::Status::Timeout) {
                if (++timeouts >= kCtlRetries)
                    throw std::runtime_error("server STATS not received (control frames lost)");
                send_control(s, ctp::MsgType::Stop);  // "please send STATS again"
                continue;
            }
            if (st == FrameReader::Status::Eof) throw std::runtime_error("server closed before sending STATS");
        }
        if (h.type == ctp::MsgType::Stats) {
            ctp::StatsPayload stp;
            if (!ctp::decode_stats(p, h.payload_len, stp)) throw std::runtime_error("bad STATS");
            return stp;
        }
        if (h.type == ctp::MsgType::Error) throw_peer_error(p, h.payload_len);
        if (!lossy)
            throw std::runtime_error(std::string("expected STATS, got ") + ctp::to_string(h.type));
        // lossy: late DATA / duplicate STOP / late ECHO_RSP - ignore
    }
}

struct EchoStats {
    uint64_t sent = 0, lost = 0, late = 0;
};

void run_echo(Stream& s, FrameReader& rd, const ctp::StartParams& sp, const AgentOptions& o,
              uint64_t t0, RttStats& rtt, EchoStats& es) {
    const bool lossy = s.lossy();
    PatternSource pat(sp.block_size, false);
    const uint64_t end = t0 + uint64_t(sp.duration_ms) * 1000000ull;
    const uint64_t gap = uint64_t(sp.echo_interval_us) * 1000ull;
    // A reply later than this counts as lost (lossy links only).
    const int wait_ms = int(std::clamp<uint64_t>(gap / 1000000ull * 4, 200, 2000));
    const uint64_t report_ns = o.interval_s > 0 ? uint64_t(o.interval_s * 1e9) : 0;
    uint64_t next_send = t0, next_report = t0 + report_ns, last_report = t0;
    uint64_t win_n = 0, win_max = 0, win_lost = 0;
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
        ++es.sent;

        bool got = false;
        uint64_t deadline = now + uint64_t(wait_ms) * 1000000ull;
        while (!got) {
            if (!lossy) {
                if (!rd.next(h, p)) throw std::runtime_error("server closed during echo");
            } else {
                uint64_t t = now_ns();
                if (t >= deadline) break;
                auto st = rd.next_for(h, p, int((deadline - t) / 1000000ull) + 1);
                if (st == FrameReader::Status::Timeout) break;
                if (st == FrameReader::Status::Eof) throw std::runtime_error("server closed during echo");
            }
            if (h.type == ctp::MsgType::EchoRsp && h.seq == seq) {
                got = true;
            } else if (lossy && h.type == ctp::MsgType::EchoRsp && int32_t(h.seq - seq) < 0) {
                ++es.late;  // reply to an earlier request we already gave up on
            } else if (lossy && h.type == ctp::MsgType::StartAck) {
                // duplicate ACK of a repeated START
            } else if (h.type == ctp::MsgType::Error) {
                throw_peer_error(p, h.payload_len);
            } else {
                throw std::runtime_error("echo response mismatch (seq " + std::to_string(seq) + ")");
            }
        }
        uint64_t back = now_ns();
        if (got) {
            uint64_t r = back - h.ts_ns;
            rtt.add(r);
            ++win_n;
            win_sum += double(r);
            win_max = std::max(win_max, r);
        } else {
            ++es.lost;
            ++win_lost;
        }
        if (report_ns && back >= next_report) {
            double avg = win_n ? win_sum / double(win_n) / 1000.0 : 0.0;
            Json j;
            j.add("event", "interval")
                .add("dir", "rtt")
                .add("role", "echo")
                .add("t_start", double(last_report - t0) / 1e9)
                .add("t_end", double(back - t0) / 1e9)
                .add("samples", win_n)
                .add("lost", win_lost)
                .add("avg_us", avg)
                .add("max_us", double(win_max) / 1000.0);
            out::emit(j, out::strprintf("[rtt echo] %6.2f-%6.2f s  samples=%llu avg=%.1f us max=%.1f us%s",
                                        double(last_report - t0) / 1e9, double(back - t0) / 1e9,
                                        (unsigned long long)win_n, avg, double(win_max) / 1000.0,
                                        win_lost ? out::strprintf(" lost=%llu",
                                                                  (unsigned long long)win_lost).c_str()
                                                 : ""));
            win_n = win_lost = win_max = 0;
            win_sum = 0;
            last_report = back;
            while (next_report <= back) next_report += report_ns;
        }
        next_send += gap;
        if (next_send < back) next_send = back;  // never burst to catch up
    }
    send_stop(s, 0);
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
    const bool lossy = s->lossy();
    if (lossy) {
        if (sp.block_size + ctp::kHeaderSize > 65507)
            throw std::runtime_error("block too large for a datagram (max 65475)");
        if (!sp.rate_bps && o.mode != ctp::Mode::Echo) sp.rate_bps = kDefaultDatagramRate;
    }
    Json jc;
    jc.add("event", "connected").add("transport", t->name()).add("peer", s->peer());
    if (lossy) jc.add("rate_bps", sp.rate_bps);
    out::emit(jc, out::strprintf("connected to %s %s  mode=%s block=%u duration=%.1fs%s", t->name(),
                                 s->peer().c_str(), ctp::to_string(sp.mode), sp.block_size,
                                 sp.duration_ms / 1000.0,
                                 lossy && sp.rate_bps
                                     ? out::strprintf(" rate=%.1f Mbit/s", sp.rate_bps / 1e6).c_str()
                                     : ""));

    FrameReader rd(*s);
    handshake(*s, rd, sp);

    const uint64_t t0 = now_ns();
    const int idle_ms = lossy_idle_ms(topts);
    RxCounters rx;
    TxCounters tx;
    RttStats rtt;
    EchoStats es;
    IntervalReporter rep_tx("up", "tx", o.interval_s, t0);
    IntervalReporter rep_rx("down", "rx", o.interval_s, t0);
    bool stop_lost = false;

    switch (sp.mode) {
        case ctp::Mode::Tx:
            send_data(*s, sp, t0, tx, rep_tx, nullptr);
            break;
        case ctp::Mode::Rx:
            rx.begin(t0);
            stop_lost = !receive_data(rd, o.verify, rx, rep_rx, lossy, idle_ms, nullptr);
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
                stop_lost = !receive_data(rd, o.verify, rx, rep_rx, lossy, idle_ms, nullptr);
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
            run_echo(*s, rd, sp, o, t0, rtt, es);
            break;
    }
    ctp::StatsPayload remote = fetch_stats(*s, rd);

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
        .add("verify", o.verify)
        .add("lossy", lossy);
    if (stop_lost) res.add("warning", "STOP from server lost; receiver ended after idle timeout");

    std::string text = out::strprintf(
        "\n=== CTT result: %s %s  mode=%s block=%u duration=%.1fs verify=%s ===\n", t->name(),
        s->peer().c_str(), ctp::to_string(sp.mode), sp.block_size, sp.duration_ms / 1000.0,
        o.verify ? "on" : "off");
    auto dir_line = [lossy](const char* label, double rx_mbps, double tx_mbps, uint64_t frames,
                            uint64_t lost, uint64_t crc, uint64_t bytes, double jitter_ns) {
        std::string l = out::strprintf(
            "  %-26s %10.3f Mbps (receiver)  %10.3f Mbps (sender)  bytes=%llu lost=%llu crc_err=%llu",
            label, rx_mbps, tx_mbps, (unsigned long long)bytes, (unsigned long long)lost,
            (unsigned long long)crc);
        if (lossy)
            l += out::strprintf(" loss=%.3f%% jitter=%.1fus",
                                frames + lost ? 100.0 * double(lost) / double(frames + lost) : 0.0,
                                jitter_ns / 1000.0);
        return l + "\n";
    };

    if (sp.mode == ctp::Mode::Tx || sp.mode == ctp::Mode::Bidir) {
        Json up;
        up.add("sender", tx.to_json());
        if (remote.has_rx)
            up.add("receiver", rx_json(remote.rx_frames, remote.rx_bytes, remote.rx_lost,
                                       remote.rx_out_of_order, remote.rx_crc_errors,
                                       remote.rx_duration_ns, double(remote.rx_jitter_ns)));
        res.add("up", up);
        text += dir_line("up   (client -> server)", mbps(remote.rx_bytes, remote.rx_duration_ns),
                         mbps(tx.bytes, tx.duration_ns()), remote.rx_frames, remote.rx_lost,
                         remote.rx_crc_errors, remote.rx_bytes, double(remote.rx_jitter_ns));
    }
    if (sp.mode == ctp::Mode::Rx || sp.mode == ctp::Mode::Bidir) {
        Json down;
        if (remote.has_tx)
            down.add("sender", tx_json(remote.tx_frames, remote.tx_bytes, remote.tx_duration_ns));
        down.add("receiver", rx.to_json());
        res.add("down", down);
        text += dir_line("down (server -> client)", mbps(rx.bytes, rx.duration_ns()),
                         mbps(remote.tx_bytes, remote.tx_duration_ns), rx.frames, rx.lost,
                         rx.crc_errors, rx.bytes, rx.jitter_ns);
    }
    if (sp.mode == ctp::Mode::Echo) {
        Json r = rtt.to_json();
        res.add("rtt", r);
        res.add("echo", Json()
                            .add("sent", es.sent)
                            .add("lost", es.lost)
                            .add("late", es.late)
                            .add("loss_pct", es.sent ? 100.0 * double(es.lost) / double(es.sent) : 0.0));
        res.add("echoed_by_server", remote.echoed);
        text += "  " + rtt.to_text() + "\n";
        if (es.lost || es.late)
            text += out::strprintf("  echo lost=%llu late=%llu of %llu\n", (unsigned long long)es.lost,
                                   (unsigned long long)es.late, (unsigned long long)es.sent);
    }
    Json link;
    s->link_info(link);
    res.add("link", link);
    out::emit(res, text);
    return 0;
}

}  // namespace ctt
