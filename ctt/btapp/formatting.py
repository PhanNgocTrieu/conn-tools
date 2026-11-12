"""Text formatting of throughput results, shared by the console front-ends."""

from __future__ import annotations

from ctt.protocol.ctp import Interval, Result


def mbps(v: float | None, digits: int = 3) -> str:
    return "-" if v is None else f"{v:.{digits}f}"


def interval_line(iv: Interval) -> str:
    parts = [f"{iv.t:6.1f}s"]
    if iv.up_mbps is not None:
        parts.append(f"up {iv.up_mbps:7.3f}")
    if iv.down_mbps is not None:
        parts.append(f"down {iv.down_mbps:7.3f}")
    if iv.rtt_ms is not None:
        parts.append(f"rtt {iv.rtt_ms:7.2f} ms")
    return "  " + "  ".join(parts) + ("  Mbit/s" if iv.rtt_ms is None else "")


def passed(r: Result) -> bool:
    return r.lost == 0 and r.crc_errors == 0


def result_lines(r: Result) -> list[str]:
    lines = [f"PC -> board {mbps(r.up_mbps)} Mbit/s   board -> PC {mbps(r.down_mbps)} Mbit/s   "
             f"lost {r.lost}  crc {r.crc_errors}"]
    if r.rtt_ms:
        lines.append("RTT ms: " + "  ".join(f"{k} {v:.2f}" for k, v in r.rtt_ms.items() if k != "count")
                     + f"  ({r.rtt_ms['count']})")
    lines.append("PASS" if passed(r) else "FAIL")
    return lines
