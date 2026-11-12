"""Text formatting of throughput results, shared by the console front-ends."""

from __future__ import annotations

from ctt.protocol.ctp import Interval, Result


def mbps(v: float | None, digits: int = 3) -> str:
    return "-" if v is None else f"{v:.{digits}f}"


def human_bytes(n: int) -> str:
    """512 B, 51.5 KB, 1.23 MB (1 KB = 1000 B, same as the board agent)."""
    if n < 1000:
        return f"{n} B"
    if n < 1_000_000:
        return f"{n / 1e3:.1f} KB"
    if n < 1_000_000_000:
        return f"{n / 1e6:.2f} MB"
    return f"{n / 1e9:.2f} GB"


def interval_line(iv: Interval) -> str:
    """One live line of the PC side: rate of the last interval + totals so far.

       12.0s  PC->board  0.412 Mbit/s  sent 205.9 KB (208 fr) | board->PC  0.560 Mbit/s  received 300.1 KB (303 fr)  lost 0  crc 0
    """
    if iv.rtt_ms is not None:
        return f"  {iv.t:6.1f}s  echo  rtt avg {iv.rtt_ms:7.2f} ms  replies {iv.echo_count}"
    parts = []
    if iv.up_mbps is not None:
        parts.append(f"PC->board {iv.up_mbps:6.3f} Mbit/s  sent {human_bytes(iv.up_bytes)} ({iv.up_frames} fr)")
    if iv.down_mbps is not None:
        parts.append(f"board->PC {iv.down_mbps:6.3f} Mbit/s  received {human_bytes(iv.down_bytes)} "
                     f"({iv.down_frames} fr)  lost {iv.lost}  crc {iv.crc_errors}")
    return f"  {iv.t:6.1f}s  " + " | ".join(parts)


def board_event_line(ev: dict) -> str | None:
    """Live trace of the board agent (SSH mode), e.g.
         [board] 3.0s  received 205.9 KB (208 fr)  0.412 Mbit/s  lost 0  crc 0
    None for events that are not progress or errors."""
    kind = ev.get("event")
    if kind == "interval":
        rx = ev.get("role") == "rx"
        what = "received" if rx else "sent"
        line = (f"  [board] {ev.get('t_end', 0):5.1f}s  {what} {human_bytes(int(ev.get('total_bytes', 0)))} "
                f"({ev.get('total_frames', 0)} fr)  {ev.get('mbps', 0):.3f} Mbit/s")
        if rx:
            line += f"  lost {ev.get('total_lost', 0)}  crc {ev.get('total_crc_errors', 0)}"
        return line
    if kind == "echo_progress":
        return f"  [board] {ev.get('t_end', 0):5.1f}s  echo replied {ev.get('total_replies', 0)}"
    if kind == "session_params" and ev.get("direction"):
        return f"  [board] {ev['direction']}"
    if kind == "session_end":
        if not ev.get("ok"):
            return f"  [board] session failed: {ev.get('message')}"
        parts = []
        if "rx" in ev:
            r = ev["rx"]
            parts.append(f"received {human_bytes(int(r.get('bytes', 0)))} ({r.get('frames', 0)} fr) "
                         f"lost {r.get('lost', 0)} crc {r.get('crc_errors', 0)}")
        if "tx" in ev:
            parts.append(f"sent {human_bytes(int(ev['tx'].get('bytes', 0)))} ({ev['tx'].get('frames', 0)} fr)")
        if ev.get("echoed"):
            parts.append(f"replied {ev['echoed']} echo requests")
        return "  [board] session done: " + "; ".join(parts)
    if kind in ("error", "warning"):
        return f"  [board] {kind}: {ev.get('message')}"
    return None


def passed(r: Result) -> bool:
    return r.lost == 0 and r.crc_errors == 0


def result_lines(r: Result) -> list[str]:
    lines = [f"PC -> board {mbps(r.up_mbps)} Mbit/s   board -> PC {mbps(r.down_mbps)} Mbit/s   "
             f"lost {r.lost}  crc {r.crc_errors}"]
    data = []
    if r.up_mbps is not None or r.up_sent_bytes:
        data.append(f"PC sent {human_bytes(r.up_sent_bytes)}, board received {human_bytes(r.up_bytes)}")
    if r.down_mbps is not None:
        sent = r.board_stats.get("tx_bytes")
        data.append((f"board sent {human_bytes(int(sent))}, " if sent is not None else "")
                    + f"PC received {human_bytes(r.down_bytes)}")
    if data:
        lines.append("data: " + "   ".join(data))
    if r.rtt_ms:
        lines.append("RTT ms: " + "  ".join(f"{k} {v:.2f}" for k, v in r.rtt_ms.items() if k != "count")
                     + f"  ({r.rtt_ms['count']})")
    lines.append("PASS" if passed(r) else "FAIL")
    return lines
