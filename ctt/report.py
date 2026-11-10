"""Result files and console output."""

from __future__ import annotations

import csv
import json
import re
import sys
import time
from pathlib import Path

from ctt.runner import TestResult


def _fmt(v, digits=2) -> str:
    if v is None:
        return "-"
    if isinstance(v, float):
        return f"{v:.{digits}f}"
    return str(v)


def _slug(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.=-]+", "_", s)[:80]


class ReportWriter:
    def __init__(self, out_root: str | Path, run_name: str):
        stamp = time.strftime("%Y%m%d-%H%M%S")
        self.dir = Path(out_root) / f"{stamp}_{_slug(run_name)}"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.results: list[TestResult] = []
        self.rows: list[dict] = []

    def add(self, r: TestResult) -> Path:
        self.results.append(r)
        row = {"name": r.spec.name, "transport": r.spec.transport, "mode": r.spec.mode,
               "block": r.result.get("block") if r.result else r.spec.block,
               "duration_s": r.spec.duration, "verdict": r.verdict, **r.metrics,
               "error": r.error or ""}
        return self.add_record(r.spec.name, row, r.to_dict(), r.intervals)

    def add_record(self, name: str, row: dict, data: dict, intervals: list | None = None) -> Path:
        """Generic entry: `row` goes to summary.csv/json, `data` to result.json."""
        self.rows.append(row)
        tdir = self.dir / f"{len(self.rows):02d}_{_slug(name)}"
        tdir.mkdir(exist_ok=True)
        (tdir / "result.json").write_text(json.dumps(data, indent=2))
        if intervals:
            cols = ["side", "dir", "role", "t_start", "t_end", "mbps", "bytes", "frames", "lost",
                    "crc_errors", "samples", "avg_us", "max_us"]
            with open(tdir / "intervals.csv", "w", newline="") as f:
                w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
                w.writeheader()
                w.writerows(intervals)
        return tdir

    def finish(self, meta: dict) -> None:
        rows = self.rows
        cols: list[str] = []
        for row in rows:
            cols += [k for k in row if k not in cols]
        with open(self.dir / "summary.csv", "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=cols)
            w.writeheader()
            w.writerows(rows)
        summary = {
            **meta,
            "passed": sum(r["verdict"] == "PASS" for r in rows),
            "failed": sum(r["verdict"] == "FAIL" for r in rows),
            "errors": sum(r["verdict"] == "ERROR" for r in rows),
            "tests": rows,
        }
        (self.dir / "summary.json").write_text(json.dumps(summary, indent=2))
        from ctt.html_report import write_reports
        write_reports(self.dir)


# ---- console ------------------------------------------------------------------

_COLOR = sys.stdout.isatty()


def color(text: str, code: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _COLOR else text


VERDICT_COLOR = {"PASS": "32", "FAIL": "31", "ERROR": "33"}


def print_live_event(side: str, ev: dict, quiet: bool = False) -> None:
    kind = ev.get("event")
    if quiet or side != "client":
        return
    if kind == "connected":
        print(f"  connected: {ev.get('transport')} {ev.get('peer')}")
    elif kind == "interval":
        if ev.get("dir") == "rtt":
            print(f"  [{ev['t_start']:6.2f}-{ev['t_end']:6.2f}s] rtt avg {ev['avg_us']:8.1f} us"
                  f"  max {ev['max_us']:8.1f} us  n={ev['samples']}")
        else:
            arrow = "client->server" if ev["dir"] == "up" else "server->client"
            print(f"  [{ev['t_start']:6.2f}-{ev['t_end']:6.2f}s] {ev['dir']:<4} {arrow} "
                  f"{ev['mbps']:10.3f} Mbps ({ev['role']} side)")


def print_result(r: TestResult) -> None:
    v = color(r.verdict, VERDICT_COLOR[r.verdict])
    print(f"  => {v}  ({r.elapsed_s:.1f}s)")
    m = r.metrics
    if "up_mbps" in m:
        print(f"     up   (client->server): {_fmt(m['up_mbps'], 3):>10} Mbps  "
              f"lost={m.get('up_lost')} crc_err={m.get('up_crc_errors')}")
    if "down_mbps" in m:
        print(f"     down (server->client): {_fmt(m['down_mbps'], 3):>10} Mbps  "
              f"lost={m.get('down_lost')} crc_err={m.get('down_crc_errors')}")
    if "loss_pct" in m:
        print(f"     udp: loss {_fmt(m['loss_pct'], 3)}%  jitter {_fmt(m.get('jitter_us'), 1)} us")
    if "wifi_signal_dbm" in m:
        print(f"     wifi: signal {m['wifi_signal_dbm']} dBm  tx {_fmt(m.get('wifi_tx_bitrate_mbps'), 1)}"
              f" / rx {_fmt(m.get('wifi_rx_bitrate_mbps'), 1)} Mbit/s")
    if "rtt_avg_us" in m:
        print(f"     rtt: avg {_fmt(m['rtt_avg_us'], 1)} us  p99 {_fmt(m.get('rtt_p99_us'), 1)} us"
              f"  max {_fmt(m.get('rtt_max_us'), 1)} us  (n={m.get('rtt_count')})")
    for c in r.checks:
        if not c["ok"]:
            print(f"     check {c['check']}: {c['metric']}={_fmt(c['value'])} "
                  f"not {c['op']} {c['limit']}")
    if r.error:
        print(f"     error: {r.error}")


def print_summary(results: list[TestResult], out_dir: Path | None) -> None:
    print()
    header = f"{'TEST':<34} {'MODE':<6} {'UP Mbps':>11} {'DOWN Mbps':>11} {'RTT avg us':>11}" \
             f" {'LOST':>5} {'CRC':>4}  VERDICT"
    print(header)
    print("-" * len(header))
    for r in results:
        m = r.metrics
        print(f"{r.spec.name[:34]:<34} {r.spec.mode:<6} {_fmt(m.get('up_mbps'), 3):>11} "
              f"{_fmt(m.get('down_mbps'), 3):>11} {_fmt(m.get('rtt_avg_us'), 1):>11} "
              f"{_fmt(m.get('lost')):>5} {_fmt(m.get('crc_errors')):>4}  "
              f"{color(r.verdict, VERDICT_COLOR[r.verdict])}")
    passed = sum(r.verdict == "PASS" for r in results)
    print(f"\n{passed}/{len(results)} passed")
    if out_dir:
        print(f"report: {out_dir}  (report.html, junit.xml)")
