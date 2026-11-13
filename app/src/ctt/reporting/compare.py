"""Compare a run against a baseline run (regression check).

Tests are matched by name. A metric regresses when it moves in the bad
direction by more than `tolerance_pct` percent; counters that should be zero
(lost, crc_errors) regress on any increase from zero.
"""

from __future__ import annotations

import json
from pathlib import Path

HIGHER_IS_BETTER = ("up_mbps", "down_mbps", "success_rate", "wifi_signal_dbm")
LOWER_IS_BETTER = ("rtt_avg_us", "rtt_p99_us", "loss_pct", "jitter_us", "echo_loss_pct", "max_ms",
                   "lost", "crc_errors")
ZERO_EXPECTED = ("lost", "crc_errors")
# loss_pct below this (absolute %) is noise, not a regression
ABS_FLOOR = {"loss_pct": 0.1, "echo_loss_pct": 0.5}


def _rows(run_dir: Path) -> dict[str, dict]:
    p = Path(run_dir) / "summary.json"
    if not p.exists():
        raise FileNotFoundError(f"{p} not found (not a ctt run directory?)")
    return {r["name"]: r for r in json.loads(p.read_text()).get("tests", [])}


def compare(base_dir: str | Path, new_dir: str | Path, tolerance_pct: float = 10.0) -> list[dict]:
    base, new = _rows(Path(base_dir)), _rows(Path(new_dir))
    out = []
    for name, nr in new.items():
        br = base.get(name)
        if br is None:
            out.append({"test": name, "metric": "-", "status": "new"})
            continue
        if br.get("verdict") == "PASS" and nr.get("verdict") != "PASS":
            out.append({"test": name, "metric": "verdict", "base": br.get("verdict"),
                        "new": nr.get("verdict"), "status": "regression"})
        for metric in HIGHER_IS_BETTER + LOWER_IS_BETTER:
            b, n = br.get(metric), nr.get(metric)
            if not isinstance(b, (int, float)) or not isinstance(n, (int, float)):
                continue
            higher = metric in HIGHER_IS_BETTER
            worse = (n < b) if higher else (n > b)
            if b == 0:
                change = None
                regress = worse and (metric in ZERO_EXPECTED or n > ABS_FLOOR.get(metric, 0))
            else:
                change = (n - b) / abs(b) * 100.0
                delta_ok = abs(n - b) <= ABS_FLOOR.get(metric, 0)
                regress = worse and abs(change) > tolerance_pct and not delta_ok
            improved = not worse and n != b and (change is None or abs(change) > tolerance_pct)
            out.append({"test": name, "metric": metric, "base": b, "new": n,
                        "change_pct": change,
                        "status": "regression" if regress else ("improved" if improved else "ok")})
    for name in base.keys() - new.keys():
        out.append({"test": name, "metric": "-", "status": "missing"})
    return out


def format_table(rows: list[dict]) -> str:
    lines = [f"{'TEST':<36} {'METRIC':<14} {'BASE':>12} {'NEW':>12} {'CHANGE':>9}  STATUS"]
    for r in rows:
        ch = r.get("change_pct")
        chs = "-" if ch is None else f"{ch:+.1f}%"

        def f(v):
            return "-" if v is None else (f"{v:.3f}" if isinstance(v, float) else str(v))

        lines.append(f"{r['test'][:36]:<36} {r['metric']:<14} {f(r.get('base')):>12} "
                     f"{f(r.get('new')):>12} {chs:>9}  {r['status']}")
    return "\n".join(lines)
