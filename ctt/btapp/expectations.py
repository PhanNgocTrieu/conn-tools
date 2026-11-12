"""Pass/fail expectations for Bluetooth throughput results, and the built-in test suite.

A result passes when the data arrived intact (no loss, no CRC error, in order) AND it
meets the numeric expectations of the case (Mbit/s minimums, RTT maximum).

The suite has two levels of expectations:
  baseline  regression floor of the current Local Test setup (Windows PC + USB dongles),
            about 75% of the lowest values measured there (report.md, block size table).
  target    product goal for the Client Test setup (on-board chip, BR/EDR). RFCOMM goodput
            ceilings for reference: 3-DH5 ~2.1, 2-DH5 ~1.4, DH5 (basic rate) ~0.7 Mbit/s.
Edit them per project with `ctt-agent-console suite --dump > my-suite.json`, change the
numbers, and run `ctt-agent-console suite --file my-suite.json`.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

from ctt.protocol.ctp import Interval, Result

LEVELS = ("baseline", "target")

# key -> (label, unit, comparison)
EXPECT_KEYS = {
    "up_mbps_min": ("PC->board", "Mbit/s", ">="),
    "down_mbps_min": ("board->PC", "Mbit/s", ">="),
    "rtt_p99_ms_max": ("RTT p99", "ms", "<="),
    "rtt_avg_ms_max": ("RTT avg", "ms", "<="),
}


@dataclass
class Check:
    name: str
    value: float | None
    op: str
    limit: float
    unit: str = ""
    ok: bool = False

    def text(self) -> str:
        v = "-" if self.value is None else f"{self.value:.3f}"
        return f"{self.name} {v} {self.op} {self.limit:g} {self.unit}".strip()


def _metric(r: Result, key: str) -> float | None:
    if key == "up_mbps_min":
        return r.up_mbps
    if key == "down_mbps_min":
        return r.down_mbps
    if key == "rtt_p99_ms_max":
        return r.rtt_ms.get("p99") if r.rtt_ms else None
    if key == "rtt_avg_ms_max":
        return r.rtt_ms.get("avg") if r.rtt_ms else None
    raise KeyError(key)


def evaluate(r: Result, expect: dict | None = None) -> list[Check]:
    """Integrity checks (always) + the numeric expectations in `expect`."""
    checks = [Check("lost", r.lost, "<=", 0, "blocks", r.lost == 0),
              Check("CRC errors", r.crc_errors, "<=", 0, "", r.crc_errors == 0),
              Check("out of order", r.out_of_order, "<=", 0, "", r.out_of_order == 0)]
    for key, limit in (expect or {}).items():
        if limit is None:
            continue
        if key not in EXPECT_KEYS:
            raise ValueError(f"unknown expectation {key!r} (known: {', '.join(EXPECT_KEYS)})")
        label, unit, op = EXPECT_KEYS[key]
        value = _metric(r, key)
        ok = value is not None and (value >= limit if op == ">=" else value <= limit)
        checks.append(Check(label, value, op, float(limit), unit, ok))
    return checks


def all_ok(checks: list[Check]) -> bool:
    return all(c.ok for c in checks)


# ---- suite -------------------------------------------------------------------------------

@dataclass
class SuiteCase:
    id: str
    title: str
    mode: str
    duration_s: float = 30
    block: int = 990
    expect: dict = field(default_factory=dict)   # level -> {expect key: limit}

    def expect_for(self, level: str) -> dict:
        return self.expect.get(level, {})


@dataclass
class Suite:
    name: str
    cases: list[SuiteCase]
    notes: str = ""

    def select(self, only: list[str] | None) -> list[SuiteCase]:
        if not only:
            return list(self.cases)
        wanted = {o.strip().upper() for o in only}
        unknown = wanted - {c.id.upper() for c in self.cases}
        if unknown:
            raise ValueError(f"unknown case id(s): {', '.join(sorted(unknown))}")
        return [c for c in self.cases if c.id.upper() in wanted]

    def to_json(self) -> str:
        return json.dumps({"name": self.name, "notes": self.notes,
                           "cases": [asdict(c) for c in self.cases]}, indent=2)


def _case(id_, title, mode, block, duration, baseline, target) -> SuiteCase:
    return SuiteCase(id_, title, mode, duration, block, {"baseline": baseline, "target": target})


DEFAULT_SUITE = Suite(
    name="bt-rfcomm-throughput",
    notes=("baseline = regression floor of the Local Test setup (Windows + USB dongles); "
           "target = product goal on the customer board. Integrity (lost 0, CRC 0, in order) "
           "is always required."),
    cases=[
        _case("BT-TP-01", "PC -> board, block 990", "tx", 990, 30,
              {"up_mbps_min": 0.25}, {"up_mbps_min": 1.0}),
        _case("BT-TP-02", "board -> PC, block 990", "rx", 990, 30,
              {"down_mbps_min": 0.40}, {"down_mbps_min": 1.0}),
        _case("BT-TP-03", "both ways at once, block 990", "bidir", 990, 30,
              {"up_mbps_min": 0.15, "down_mbps_min": 0.25}, {"up_mbps_min": 0.5, "down_mbps_min": 0.5}),
        _case("BT-TP-04", "latency (echo), block 64", "echo", 64, 10,
              {"rtt_p99_ms_max": 250}, {"rtt_p99_ms_max": 100}),
        _case("BT-TP-05", "PC -> board, block 4096", "tx", 4096, 30,
              {"up_mbps_min": 0.25}, {"up_mbps_min": 1.0}),
        _case("BT-TP-06", "board -> PC, block 4096", "rx", 4096, 30,
              {"down_mbps_min": 0.40}, {"down_mbps_min": 1.0}),
    ])


def load_suite(path: str | Path | None) -> Suite:
    if not path:
        return DEFAULT_SUITE
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    cases = [SuiteCase(**c) for c in data["cases"]]
    for c in cases:
        for level_expect in c.expect.values():
            evaluate(Result(c.mode, c.block, c.duration_s), level_expect)   # validates the keys
    return Suite(data.get("name", Path(path).stem), cases, data.get("notes", ""))


@dataclass
class CaseOutcome:
    case: SuiteCase
    level: str
    result: Result | None
    checks: list[Check]
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error and all_ok(self.checks)

    def to_dict(self) -> dict:
        return {"id": self.case.id, "title": self.case.title, "mode": self.case.mode,
                "block": self.case.block, "duration_s": self.case.duration_s, "level": self.level,
                "verdict": "PASS" if self.ok else "FAIL", "error": self.error,
                "checks": [asdict(c) for c in self.checks],
                "result": self.result.to_dict() if self.result else None}


class SuiteRunner:
    """Runs the cases one after another through BtTestService.run_throughput."""

    def __init__(self, service, pause_s: float = 2.0):
        self.service = service
        self.pause_s = pause_s
        self._stopped = False

    def stop(self) -> None:
        self._stopped = True
        self.service.stop_throughput()

    def run(self, cases: list[SuiteCase], level: str,
            on_case: Callable[[int, SuiteCase], None] | None = None,
            on_interval: Callable[[Interval], None] | None = None,
            on_outcome: Callable[[CaseOutcome], None] | None = None,
            auto_deploy: bool = False) -> list[CaseOutcome]:
        from ctt.btapp.service import ThroughputParams
        if level not in LEVELS:
            raise ValueError(f"level must be one of {', '.join(LEVELS)}")
        outcomes = []
        for i, case in enumerate(cases, 1):
            if self._stopped:
                break
            if on_case:
                on_case(i, case)
            params = ThroughputParams(case.mode, case.duration_s, case.block, 0, True)
            try:
                r = self.service.run_throughput(params, on_interval=on_interval, auto_deploy=auto_deploy)
                out = CaseOutcome(case, level, r, evaluate(r, case.expect_for(level)))
            except (RuntimeError, OSError) as e:
                out = CaseOutcome(case, level, None, [], str(e))
            outcomes.append(out)
            if on_outcome:
                on_outcome(out)
            if i < len(cases) and not self._stopped:
                time.sleep(self.pause_s)   # let the RFCOMM link and the board server settle
        return outcomes


def summary_lines(outcomes: list[CaseOutcome]) -> list[str]:
    lines = [f"{'case':<9} {'mode':<6} {'block':>5}  {'PC->board':>9}  {'board->PC':>9}  "
             f"{'RTT p99':>8}  verdict  failed checks"]
    for o in outcomes:
        r = o.result
        up = "-" if not r or r.up_mbps is None else f"{r.up_mbps:.3f}"
        down = "-" if not r or r.down_mbps is None else f"{r.down_mbps:.3f}"
        p99 = "-" if not r or not r.rtt_ms else f"{r.rtt_ms['p99']:.1f}"
        why = o.error or "; ".join(c.text() for c in o.checks if not c.ok)
        lines.append(f"{o.case.id:<9} {o.case.mode:<6} {o.case.block:>5}  {up:>9}  {down:>9}  "
                     f"{p99:>8}  {'PASS' if o.ok else 'FAIL':<7}  {why}".rstrip())
    n_ok = sum(o.ok for o in outcomes)
    lines.append(f"{n_ok}/{len(outcomes)} PASS" + ("" if outcomes else " (nothing run)"))
    return lines


def write_report(outcomes: list[CaseOutcome], suite: Suite, level: str, out_dir: str | Path) -> Path:
    """suite-<level>-<time>.json (+ .csv); returns the JSON path."""
    import csv
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = out_dir / time.strftime(f"suite-{level}-%Y%m%d-%H%M%S")
    data = {"suite": suite.name, "level": level, "time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "passed": sum(o.ok for o in outcomes), "total": len(outcomes),
            "cases": [o.to_dict() for o in outcomes]}
    stem.with_suffix(".json").write_text(json.dumps(data, indent=2), encoding="utf-8")
    with open(stem.with_suffix(".csv"), "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["id", "title", "mode", "block", "duration_s", "level", "up_mbps", "down_mbps",
                    "rtt_p99_ms", "lost", "crc_errors", "verdict", "failed_checks"])
        for o in outcomes:
            r = o.result
            w.writerow([o.case.id, o.case.title, o.case.mode, o.case.block, o.case.duration_s, level,
                        r.up_mbps if r else "", r.down_mbps if r else "",
                        r.rtt_ms.get("p99") if r and r.rtt_ms else "", r.lost if r else "",
                        r.crc_errors if r else "", "PASS" if o.ok else "FAIL",
                        o.error or "; ".join(c.text() for c in o.checks if not c.ok)])
    return stem.with_suffix(".json")
