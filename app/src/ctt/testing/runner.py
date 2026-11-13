"""Runs one test: starts the server side, runs the client side, collects results."""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field, replace
from typing import Callable

from ctt.core.agent import AgentProcess, client_args, server_args
from ctt.core.executor import Executor, LocalExecutor
from ctt.core.units import parse_rate, parse_seconds, parse_size

MODES = ("tx", "rx", "bidir", "echo")

# Per transport: default port, server bind endpoint and client target.
# Templates may use {port}, {addr} (network address of the server machine) and
# any field of the server's "listening" event, e.g. {bdaddr}, {channel}, {psm}.
TRANSPORT_DEFAULTS = {
    "tcp": {"port": 5201, "listen": "0.0.0.0:{port}", "target": "{addr}:{port}"},
    # one frame per datagram; client defaults to 10 Mbit/s unless `rate` is set
    "udp": {"port": 5201, "listen": "0.0.0.0:{port}", "target": "{addr}:{port}"},
    # port = RFCOMM channel; 0 lets the kernel pick a free one on the server
    "rfcomm": {"port": 0, "listen": "/{port}", "target": "{bdaddr}/{channel}"},
    # port = PSM; 0 -> agent default (0x1001 BR/EDR, 0x0080 LE)
    "l2cap": {"port": 0, "listen": "/{port}", "target": "{bdaddr}/{psm}"},
    "unix": {"port": 0, "listen": "@ctt-{port}", "target": "{endpoint}", "local_only": True},
    # server = DUT gadget (FunctionFS, dut-usb-gadget.sh), client = host (usbdevfs)
    "usb": {"port": 0, "listen": "/dev/ffs-ctt", "target": "1d6b:0104", "opts": {"udc": "auto"},
            "server_side": "dut"},
}
BT_TRANSPORTS = ("rfcomm", "l2cap")
ZERO_BDADDR = "00:00:00:00:00:00"


@dataclass
class TestSpec:
    name: str = "test"
    transport: str = "tcp"
    mode: str = "tx"
    duration: float = 10.0
    block: int | None = None
    rate: int | None = None          # bits/s
    verify: bool = True
    count: int | None = None         # echo
    echo_interval_ms: float | None = None
    interval: float = 1.0
    nodelay: bool = False
    sndbuf: int | None = None
    rcvbuf: int | None = None
    timeout: float | None = None     # agent idle I/O timeout
    port: int | None = None          # tcp port / RFCOMM channel / L2CAP PSM
    bdaddr: str | None = None        # BT address of the server side (if not auto-detected)
    endpoint: str | None = None      # client target; default derived from transport
    listen: str | None = None        # server bind; default derived from transport
    server_side: str = "dut"         # "dut" or "host"
    transport_opts: dict = field(default_factory=dict)
    expect: dict = field(default_factory=dict)
    # WiFi link info from the DUT before/after the test: "auto" (first
    # connected wireless interface), an interface name, or None (off)
    wifi_iface: str | None = None

    @classmethod
    def from_dict(cls, d: dict) -> "TestSpec":
        d = dict(d)
        known = set(cls.__dataclass_fields__)
        unknown = set(d) - known
        if unknown:
            raise ValueError(f"unknown test field(s): {', '.join(sorted(unknown))}")
        if "duration" in d:
            d["duration"] = parse_seconds(d["duration"])
        for k in ("interval", "timeout"):
            if d.get(k) is not None:
                d[k] = parse_seconds(d[k])
        if d.get("block") is not None:
            d["block"] = parse_size(d["block"])
        if d.get("rate") is not None:
            d["rate"] = parse_rate(d["rate"])
        for k in ("sndbuf", "rcvbuf"):
            if d.get(k) is not None:
                d[k] = parse_size(d[k])
        spec = cls(**d)
        spec.validate()
        return spec

    def validate(self):
        if self.mode not in MODES:
            raise ValueError(f"{self.name}: mode must be one of {MODES}")
        if self.server_side not in ("dut", "host"):
            raise ValueError(f"{self.name}: server_side must be 'dut' or 'host'")
        need = TRANSPORT_DEFAULTS.get(self.transport, {}).get("server_side")
        if need and self.server_side != need:
            raise ValueError(f"{self.name}: transport '{self.transport}' needs server_side={need}")
        unknown = set(self.expect) - set(EXPECT_KEYS)
        if unknown:
            raise ValueError(f"{self.name}: unknown expect key(s): {', '.join(sorted(unknown))}")


@dataclass
class TestResult:
    spec: TestSpec
    verdict: str = "ERROR"             # PASS / FAIL / ERROR
    result: dict | None = None         # client "result" event
    metrics: dict = field(default_factory=dict)
    checks: list = field(default_factory=list)
    intervals: list = field(default_factory=list)   # client + server interval events
    server_events: list = field(default_factory=list)
    wifi: dict = field(default_factory=dict)          # {"before": {...}, "after": {...}}
    error: str | None = None
    target: str | None = None
    started_at: float = 0.0
    elapsed_s: float = 0.0

    def to_dict(self) -> dict:
        d = asdict(self)
        d["spec"] = asdict(self.spec)
        return d


# ---- metrics & pass/fail ----------------------------------------------------

def summarize(result: dict) -> dict:
    """Flattens a client result into the key metrics used for verdicts/reports."""
    m: dict = {}
    for d in ("up", "down"):
        part = result.get(d)
        if not part:
            continue
        rx = part.get("receiver") or {}
        tx = part.get("sender") or {}
        m[f"{d}_mbps"] = rx.get("mbps")
        m[f"{d}_sender_mbps"] = tx.get("mbps")
        m[f"{d}_bytes"] = rx.get("bytes")
        m[f"{d}_lost"] = rx.get("lost", 0)
        m[f"{d}_crc_errors"] = rx.get("crc_errors", 0)
        if result.get("lossy"):
            m[f"{d}_loss_pct"] = rx.get("loss_pct")
            m[f"{d}_jitter_us"] = rx.get("jitter_us")
    m["lost"] = m.get("up_lost", 0) + m.get("down_lost", 0)
    m["crc_errors"] = m.get("up_crc_errors", 0) + m.get("down_crc_errors", 0)
    if result.get("lossy"):
        m["loss_pct"] = max((m.get(f"{d}_loss_pct") or 0) for d in ("up", "down"))
        m["jitter_us"] = max((m.get(f"{d}_jitter_us") or 0) for d in ("up", "down"))
    echo = result.get("echo")
    if echo and result.get("lossy"):
        m["echo_loss_pct"] = echo.get("loss_pct")
    rtt = result.get("rtt")
    if rtt:
        for k in ("count", "min_us", "avg_us", "p50_us", "p95_us", "p99_us", "max_us",
                  "jitter_us"):
            if k in rtt:
                m[f"rtt_{k}"] = rtt[k]
    link = result.get("link") or {}
    if "tcp_total_retrans" in link:
        m["tcp_retrans"] = link["tcp_total_retrans"]
    return m


# expect key -> (metric, comparison)
EXPECT_KEYS = {
    "up_mbps_min": ("up_mbps", ">="),
    "down_mbps_min": ("down_mbps", ">="),
    "lost_max": ("lost", "<="),
    "crc_errors_max": ("crc_errors", "<="),
    "rtt_avg_us_max": ("rtt_avg_us", "<="),
    "rtt_p99_us_max": ("rtt_p99_us", "<="),
    "rtt_max_us_max": ("rtt_max_us", "<="),
    "loss_pct_max": ("loss_pct", "<="),           # UDP: worst direction
    "jitter_us_max": ("jitter_us", "<="),         # UDP: worst direction
    "echo_loss_pct_max": ("echo_loss_pct", "<="),
    "wifi_signal_dbm_min": ("wifi_signal_dbm", ">="),
}

# Applied to every test unless overridden: data must arrive intact.
DEFAULT_EXPECT = {"crc_errors_max": 0}


def evaluate(metrics: dict, expect: dict) -> list[dict]:
    checks = []
    for key, limit in {**DEFAULT_EXPECT, **expect}.items():
        metric, op = EXPECT_KEYS[key]
        value = metrics.get(metric)
        if value is None:
            ok = False
        else:
            ok = value >= float(limit) if op == ">=" else value <= float(limit)
        checks.append({"check": key, "metric": metric, "op": op, "limit": limit,
                       "value": value, "ok": ok})
    return checks


# ---- runner -------------------------------------------------------------------

class Runner:
    def __init__(self, dut: Executor, host: Executor, dut_agent: str, host_agent: str,
                 on_event: Callable[[str, dict], None] | None = None):
        self.dut = dut
        self.host = host
        self.dut_agent = dut_agent
        self.host_agent = host_agent
        self.on_event = on_event or (lambda side, ev: None)

    def _sides(self, spec: TestSpec):
        if spec.server_side == "dut":
            return (self.dut, self.dut_agent), (self.host, self.host_agent)
        return (self.host, self.host_agent), (self.dut, self.dut_agent)

    def _listen(self, spec: TestSpec) -> str | None:
        if spec.listen is not None:
            return spec.listen
        d = TRANSPORT_DEFAULTS.get(spec.transport)
        if not d:
            return None  # agent default
        port = spec.port if spec.port is not None else d["port"]
        return d["listen"].format(port=port)

    def _target(self, spec: TestSpec, server_exec: Executor, listening: dict) -> str:
        if spec.endpoint:
            return spec.endpoint
        d = TRANSPORT_DEFAULTS.get(spec.transport)
        if not d:
            raise ValueError(f"{spec.name}: transport '{spec.transport}' needs 'endpoint'")
        if d.get("local_only") and not isinstance(self.dut, LocalExecutor):
            raise ValueError(f"{spec.name}: transport '{spec.transport}' only works with --dut local")
        if spec.transport in ("tcp", "udp") and spec.server_side == "host" and \
                not isinstance(self.dut, LocalExecutor):
            # 127.0.0.1 would point the DUT at itself.
            raise ValueError(
                f"{spec.name}: server runs on the host, so the DUT needs the host's "
                "address - set 'endpoint' (e.g. 192.168.1.10:5201)")
        fields = {**listening, "addr": server_exec.address,
                  "port": spec.port if spec.port is not None else d["port"]}
        if spec.transport in BT_TRANSPORTS:
            if spec.bdaddr:
                fields["bdaddr"] = spec.bdaddr
            if fields.get("bdaddr", ZERO_BDADDR) == ZERO_BDADDR:
                raise ValueError(
                    f"{spec.name}: the server's Bluetooth address is unknown (no adapter "
                    f"found by the agent on the {spec.server_side}) - set 'bdaddr'")
        try:
            return d["target"].format(**fields)
        except KeyError as e:
            raise ValueError(f"{spec.name}: cannot build target, server did not report {e}") \
                from None

    def _wifi(self, spec: TestSpec) -> dict:
        from ctt.interfaces import wifi
        iface = None if spec.wifi_iface == "auto" else spec.wifi_iface
        return wifi.pick(wifi.collect(self.dut, iface), iface)

    def run(self, spec: TestSpec) -> TestResult:
        d = TRANSPORT_DEFAULTS.get(spec.transport, {})
        if d.get("opts"):
            spec = replace(spec, transport_opts={**d["opts"], **spec.transport_opts})
        res = TestResult(spec=spec, started_at=time.time())
        t_start = time.monotonic()
        server = client = None
        try:
            (s_exec, s_agent), (c_exec, c_agent) = self._sides(spec)
            server = AgentProcess(
                s_exec.popen(server_args(s_agent, spec, self._listen(spec), accept_timeout=30)),
                f"server@{s_exec.name}")
            if spec.wifi_iface:
                res.wifi["before"] = self._wifi(spec)
            ev = server.wait_for("listening", timeout=15)
            self.on_event("server", ev)
            target = self._target(spec, s_exec, ev)
            res.target = target

            client = AgentProcess(c_exec.popen(client_args(c_agent, spec, target)),
                                  f"client@{c_exec.name}")
            budget = spec.duration + 30 if not spec.count else 600
            for ev in client.iter_events(timeout=budget):
                kind = ev.get("event")
                if kind == "interval":
                    res.intervals.append({"side": "client", **ev})
                elif kind == "result":
                    res.result = ev
                elif kind == "error":
                    res.error = ev.get("message")
                self.on_event("client", ev)
            rc = client.wait(timeout=10)
            if res.result is None:
                raise RuntimeError(res.error or f"client failed (rc={rc}): "
                                   f"{client.stderr_text()}")

            server.wait(timeout=10)
            for ev in server.all_events:
                if ev.get("event") == "interval":
                    res.intervals.append({"side": "server", **ev})
                else:
                    res.server_events.append(ev)

            if spec.wifi_iface:
                res.wifi["after"] = self._wifi(spec)
            res.metrics = summarize(res.result)
            link = res.wifi.get("before") or {}
            for k in ("signal_dbm", "tx_bitrate_mbps", "rx_bitrate_mbps", "freq_mhz"):
                if link.get(k) is not None:
                    res.metrics[f"wifi_{k}"] = link[k]
            res.checks = evaluate(res.metrics, spec.expect)
            res.verdict = "PASS" if all(c["ok"] for c in res.checks) else "FAIL"
        except Exception as e:  # noqa: BLE001 - reported per test
            res.verdict = "ERROR"
            res.error = res.error or str(e)
            if server and server.stderr_lines and server.stderr_text() not in res.error:
                res.error += f" | server stderr: {server.stderr_text()}"
        finally:
            for p in (client, server):
                if p:
                    p.close()
            res.elapsed_s = time.monotonic() - t_start
        return res
