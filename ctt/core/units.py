"""Parsing of human friendly sizes / rates / durations (same rules as ctt-agent)."""

from __future__ import annotations

import re

_NUM = re.compile(r"^\s*([0-9]*\.?[0-9]+)\s*([a-zA-Z]*)\s*$")


def _split(value) -> tuple[float, str]:
    if isinstance(value, (int, float)):
        return float(value), ""
    m = _NUM.match(str(value))
    if not m:
        raise ValueError(f"invalid number: {value!r}")
    return float(m.group(1)), m.group(2).lower()


def parse_size(value) -> int:
    """'64K' -> 65536 (binary units)."""
    n, suf = _split(value)
    mul = {"": 1, "b": 1, "k": 1024, "kb": 1024, "kib": 1024, "m": 1024**2, "mb": 1024**2,
           "mib": 1024**2, "g": 1024**3, "gb": 1024**3, "gib": 1024**3}.get(suf)
    if mul is None:
        raise ValueError(f"invalid size: {value!r}")
    return int(n * mul)


def parse_rate(value) -> int:
    """'2M' / '2Mbps' -> 2_000_000 bits/s (decimal units)."""
    n, suf = _split(value)
    if suf.endswith("bps"):
        suf = suf[:-3]
    mul = {"": 1, "k": 1e3, "m": 1e6, "g": 1e9}.get(suf)
    if mul is None:
        raise ValueError(f"invalid rate: {value!r}")
    return int(n * mul)


def parse_seconds(value) -> float:
    """'10' / '10s' / '500ms' / '2m' -> seconds."""
    n, suf = _split(value)
    mul = {"": 1, "s": 1, "ms": 1e-3, "us": 1e-6, "m": 60, "min": 60, "h": 3600}.get(suf)
    if mul is None:
        raise ValueError(f"invalid duration: {value!r}")
    return n * mul


def fmt_size(n: int) -> str:
    for unit, mul in (("M", 1024**2), ("K", 1024)):
        if n >= mul and n % mul == 0:
            return f"{n // mul}{unit}"
    return str(n)
