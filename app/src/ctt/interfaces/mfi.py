"""MFi Authentication Coprocessor tests over I2C.

The register map of Apple's auth coprocessor is covered by the MFi NDA, so it
is NOT part of this repository. Tests are described in a config file (keep the
real one as *.private.yaml, which git ignores) as sequences of generic
`ctt-agent i2c` steps; see config/i2c/mfi_cp.example.yaml.

    bus: 2                 # /dev/i2c-2 on the DUT (or "fake,..." to dry-run)
    address: 0x10
    retries: 20            # NACK retries per transfer (CP NACKs while asleep/busy)
    retry_delay_ms: 1
    tests:
      - id: MFI-02
        name: identity
        repeat: 10
        steps: ["version=r:0x00:1:05"]
        expect: {success_rate_min: 1.0, step_max_ms: {version: 5}}
"""

from __future__ import annotations

import json
import shlex
import time
from dataclasses import dataclass, field
from pathlib import Path

from ctt.core.executor import Executor

EXPECT_KEYS = ("success_rate_min", "retries_max", "max_ms", "step_max_ms", "step_avg_ms")


@dataclass
class MfiTest:
    id: str
    name: str
    steps: list[str]
    repeat: int = 1
    expect: dict = field(default_factory=dict)


@dataclass
class MfiConfig:
    bus: str
    address: int
    retries: int = 10
    retry_delay_ms: float = 1.0
    combined: bool = False
    tests: list[MfiTest] = field(default_factory=list)


def _int(v) -> int:
    return v if isinstance(v, int) else int(str(v), 0)


def parse_config(doc: dict) -> MfiConfig:
    for k in ("bus", "address", "tests"):
        if k not in doc:
            raise ValueError(f"MFi config: '{k}' is required")
    cfg = MfiConfig(bus=str(doc["bus"]), address=_int(doc["address"]),
                    retries=int(doc.get("retries", 10)),
                    retry_delay_ms=float(doc.get("retry_delay_ms", 1.0)),
                    combined=bool(doc.get("combined", False)))
    for i, t in enumerate(doc["tests"]):
        steps = t.get("steps")
        if not steps or not isinstance(steps, list):
            raise ValueError(f"MFi test #{i + 1}: 'steps' must be a non-empty list")
        unknown = set(t.get("expect") or {}) - set(EXPECT_KEYS)
        if unknown:
            raise ValueError(f"MFi test #{i + 1}: unknown expect key(s) {sorted(unknown)}")
        cfg.tests.append(MfiTest(id=str(t.get("id", f"MFI-{i + 1:02d}")),
                                 name=str(t.get("name", f"test{i + 1}")),
                                 steps=[str(s) for s in steps], repeat=int(t.get("repeat", 1)),
                                 expect=dict(t.get("expect") or {})))
    return cfg


def load_config(path: str | Path) -> MfiConfig:
    import yaml
    with open(path, encoding="utf-8") as f:
        return parse_config(yaml.safe_load(f) or {})


def agent_argv(agent: str, cfg: MfiConfig, test: MfiTest, repeat_scale: float = 1.0) -> list[str]:
    argv = [agent, "i2c", "--json", "--bus", cfg.bus, "--addr", hex(cfg.address),
            "--retries", str(cfg.retries), "--retry-delay", str(cfg.retry_delay_ms),
            "--repeat", str(max(1, int(test.repeat * repeat_scale)))]
    if cfg.combined:
        argv.append("--combined")
    for s in test.steps:
        argv += ["--step", s]
    return argv


def metrics(result: dict) -> dict:
    it = result.get("iterations", 0)
    steps = result.get("steps", [])
    m = {"iterations": it, "iterations_ok": result.get("iterations_ok", 0),
         "success_rate": (result.get("iterations_ok", 0) / it) if it else 0.0,
         "retries": sum(s.get("retries", 0) for s in steps),
         "max_ms": max((s.get("max_us", 0) for s in steps), default=0) / 1000.0}
    for s in steps:
        m[f"{s['name']}_avg_ms"] = s.get("avg_us", 0) / 1000.0
        m[f"{s['name']}_max_ms"] = s.get("max_us", 0) / 1000.0
    return m


def evaluate(result: dict, m: dict, expect: dict) -> list[dict]:
    checks = []

    def add(check, value, op, limit):
        ok = value is not None and (value >= limit if op == ">=" else value <= limit)
        checks.append({"check": check, "value": value, "op": op, "limit": limit, "ok": ok})

    add("success_rate_min", m["success_rate"], ">=", float(expect.get("success_rate_min", 1.0)))
    if "retries_max" in expect:
        add("retries_max", m["retries"], "<=", float(expect["retries_max"]))
    if "max_ms" in expect:
        add("max_ms", m["max_ms"], "<=", float(expect["max_ms"]))
    for key, suffix in (("step_max_ms", "max_ms"), ("step_avg_ms", "avg_ms")):
        for step, limit in (expect.get(key) or {}).items():
            add(f"{key}.{step}", m.get(f"{step}_{suffix}"), "<=", float(limit))
    return checks


@dataclass
class MfiResult:
    test: MfiTest
    verdict: str = "ERROR"
    result: dict | None = None
    metrics: dict = field(default_factory=dict)
    checks: list = field(default_factory=list)
    error: str | None = None
    elapsed_s: float = 0.0

    def row(self) -> dict:
        return {"name": f"{self.test.id} {self.test.name}", "transport": "i2c", "mode": "mfi",
                "verdict": self.verdict, **self.metrics, "error": self.error or ""}

    def to_dict(self) -> dict:
        return {"test": self.test.__dict__, "verdict": self.verdict, "result": self.result,
                "metrics": self.metrics, "checks": self.checks, "error": self.error,
                "elapsed_s": self.elapsed_s}


def run_test(ex: Executor, agent: str, cfg: MfiConfig, test: MfiTest,
             repeat_scale: float = 1.0, timeout: float = 600) -> MfiResult:
    res = MfiResult(test=test)
    t0 = time.monotonic()
    try:
        r = ex.run(agent_argv(agent, cfg, test, repeat_scale), timeout=timeout)
        for line in r.stdout.splitlines():
            if line.startswith("{"):
                ev = json.loads(line)
                if ev.get("event") == "i2c_result":
                    res.result = ev
                elif ev.get("event") == "error":
                    res.error = ev.get("message")
        if res.result is None:
            raise RuntimeError(res.error or (r.stderr.strip() or f"agent exited {r.returncode}"))
        res.metrics = metrics(res.result)
        res.checks = evaluate(res.result, res.metrics, test.expect)
        res.verdict = "PASS" if all(c["ok"] for c in res.checks) else "FAIL"
        failing = [f"{s['name']}: {s['error']}" for s in res.result.get("steps", []) if s.get("error")]
        if failing:
            res.error = "; ".join(failing)
    except Exception as e:  # noqa: BLE001
        res.verdict = "ERROR"
        res.error = res.error or str(e)
    res.elapsed_s = time.monotonic() - t0
    return res


def format_steps(result: dict) -> str:
    lines = [f"     {'step':<20} {'ok/runs':>11} {'retries':>8} {'avg ms':>8} {'max ms':>8}  value"]
    for s in result.get("steps", []):
        val = ""
        if s.get("value_len"):
            hexv = s.get("value_hex", "")
            val = f"{s['value_len']}B {hexv[:32]}{'...' if len(hexv) > 32 else ''}"
        lines.append(f"     {s['name'][:20]:<20} {s['ok']:>5}/{s['runs']:<5} {s['retries']:>8} "
                     f"{s['avg_us'] / 1000:>8.3f} {s['max_us'] / 1000:>8.3f}  {val}")
    return "\n".join(lines)


def describe(cfg: MfiConfig) -> str:
    return f"bus {shlex.quote(cfg.bus)[:40]} addr {hex(cfg.address)}"
