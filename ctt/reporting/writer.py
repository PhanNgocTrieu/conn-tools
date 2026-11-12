"""Run directory writer: summary.csv/json, per-test result.json, intervals.csv, reports."""

from __future__ import annotations

import csv
import json
import re
import time
from pathlib import Path

from ctt.testing.runner import TestResult


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
        from ctt.reporting.html import write_reports
        write_reports(self.dir)
