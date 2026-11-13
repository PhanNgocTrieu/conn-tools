"""Console output of test runs (live intervals, per-test result, summary table)."""

from __future__ import annotations

import sys
from pathlib import Path

from ctt.testing.runner import TestResult


def _fmt(v, digits=2) -> str:
    if v is None:
        return "-"
    if isinstance(v, float):
        return f"{v:.{digits}f}"
    return str(v)


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
