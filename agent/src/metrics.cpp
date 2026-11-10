#include "ctt/metrics.hpp"

#include <algorithm>
#include <cmath>

#include "ctt/output.hpp"

namespace ctt {

void RxCounters::begin(uint64_t now) {
    started = true;
    first_ns = last_ns = now;
}

void RxCounters::on_frame(uint32_t seq, uint32_t len, uint64_t now, bool crc_ok,
                          uint64_t sender_ts) {
    if (!started) begin(now);
    if (sender_ts) {
        int64_t transit = int64_t(now) - int64_t(sender_ts);
        if (have_transit) {
            double d = double(transit - last_transit);
            jitter_ns += (std::fabs(d) - jitter_ns) / 16.0;
        }
        last_transit = transit;
        have_transit = true;
    }
    // Senders always start at seq 0, so a gap before the first frame is loss too.
    if (seq == next_seq) {
        next_seq = seq + 1;
    } else if (int32_t(seq - next_seq) > 0) {
        lost += seq - next_seq;
        next_seq = seq + 1;
    } else {
        // Late frame that was previously counted as lost.
        ++out_of_order;
        if (lost) --lost;
    }
    ++frames;
    bytes += len;
    last_ns = now;
    if (!crc_ok) ++crc_errors;
}

Json rx_json(uint64_t frames, uint64_t bytes, uint64_t lost, uint64_t ooo, uint64_t crc,
             uint64_t duration_ns, double jitter_ns) {
    Json j;
    j.add("frames", frames)
        .add("bytes", bytes)
        .add("lost", lost)
        .add("out_of_order", ooo)
        .add("crc_errors", crc)
        .add("duration_s", double(duration_ns) / 1e9)
        .add("mbps", mbps(bytes, duration_ns))
        .add("loss_pct", frames + lost ? 100.0 * double(lost) / double(frames + lost) : 0.0)
        .add("jitter_us", jitter_ns / 1000.0);
    return j;
}

Json tx_json(uint64_t frames, uint64_t bytes, uint64_t duration_ns) {
    Json j;
    j.add("frames", frames)
        .add("bytes", bytes)
        .add("duration_s", double(duration_ns) / 1e9)
        .add("mbps", mbps(bytes, duration_ns));
    return j;
}

Json RxCounters::to_json() const {
    return rx_json(frames, bytes, lost, out_of_order, crc_errors, duration_ns(), jitter_ns);
}

Json TxCounters::to_json() const { return tx_json(frames, bytes, duration_ns()); }

void RttStats::add(uint64_t rtt_ns) {
    double us = double(rtt_ns) / 1000.0;
    if (!samples_us_.empty()) jitter_sum_ += std::fabs(us - samples_us_.back());
    samples_us_.push_back(us);
}

namespace {
double percentile(std::vector<double>& sorted, double p) {
    if (sorted.empty()) return 0;
    double idx = p / 100.0 * double(sorted.size() - 1);
    size_t lo = size_t(std::floor(idx));
    size_t hi = std::min(lo + 1, sorted.size() - 1);
    double frac = idx - double(lo);
    return sorted[lo] * (1 - frac) + sorted[hi] * frac;
}
}  // namespace

Json RttStats::to_json() const {
    Json j;
    j.add("count", samples_us_.size());
    if (samples_us_.empty()) return j;
    std::vector<double> s = samples_us_;
    std::sort(s.begin(), s.end());
    double sum = 0;
    for (double v : s) sum += v;
    j.add("min_us", s.front())
        .add("avg_us", sum / double(s.size()))
        .add("p50_us", percentile(s, 50))
        .add("p95_us", percentile(s, 95))
        .add("p99_us", percentile(s, 99))
        .add("max_us", s.back())
        .add("jitter_us", s.size() > 1 ? jitter_sum_ / double(s.size() - 1) : 0.0);
    return j;
}

std::string RttStats::to_text() const {
    if (samples_us_.empty()) return "rtt: no samples";
    std::vector<double> s = samples_us_;
    std::sort(s.begin(), s.end());
    double sum = 0;
    for (double v : s) sum += v;
    return out::strprintf(
        "rtt (us): samples=%zu min=%.1f avg=%.1f p50=%.1f p95=%.1f p99=%.1f max=%.1f "
        "jitter=%.1f",
        s.size(), s.front(), sum / double(s.size()), percentile(s, 50), percentile(s, 95),
        percentile(s, 99), s.back(), s.size() > 1 ? jitter_sum_ / double(s.size() - 1) : 0.0);
}

IntervalReporter::IntervalReporter(const char* dir, const char* role, double interval_s,
                                   uint64_t t0_ns)
    : dir_(dir),
      role_(role),
      interval_ns_(interval_s > 0 ? uint64_t(interval_s * 1e9) : 0),
      t0_(t0_ns),
      next_ns_(t0_ns + interval_ns_),
      last_ns_(t0_ns) {}

void IntervalReporter::emit(uint64_t now, uint64_t bytes, uint64_t frames, uint64_t lost,
                            uint64_t crc) {
    uint64_t dt = now - last_ns_;
    uint64_t db = bytes - last_bytes_;
    double t_start = double(last_ns_ - t0_) / 1e9;
    double t_end = double(now - t0_) / 1e9;
    double rate = mbps(db, dt);
    Json j;
    j.add("event", "interval")
        .add("dir", dir_)
        .add("role", role_)
        .add("t_start", t_start)
        .add("t_end", t_end)
        .add("bytes", db)
        .add("frames", frames - last_frames_)
        .add("lost", lost - last_lost_)
        .add("crc_errors", crc - last_crc_)
        .add("mbps", rate);
    std::string text = out::strprintf("[%-4s %s] %6.2f-%6.2f s  %10.3f Mbps  frames=%llu", dir_,
                                      role_, t_start, t_end, rate,
                                      (unsigned long long)(frames - last_frames_));
    if (role_[0] == 'r')
        text += out::strprintf("  lost=%llu crc_err=%llu", (unsigned long long)(lost - last_lost_),
                               (unsigned long long)(crc - last_crc_));
    out::emit(j, text);
    last_ns_ = now;
    last_bytes_ = bytes;
    last_frames_ = frames;
    last_lost_ = lost;
    last_crc_ = crc;
    while (next_ns_ <= now) next_ns_ += interval_ns_;
}

void IntervalReporter::finish(uint64_t now, uint64_t bytes, uint64_t frames, uint64_t lost,
                              uint64_t crc) {
    if (!interval_ns_) return;
    // Skip tiny tail intervals (< 10% of the period): their rate is noise.
    if (bytes != last_bytes_ && now - last_ns_ >= interval_ns_ / 10)
        emit(now, bytes, frames, lost, crc);
}

}  // namespace ctt
