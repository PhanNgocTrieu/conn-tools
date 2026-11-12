"""Bluetooth control through BlueZ's `bluetoothctl`.

Driving bluetoothctl (instead of D-Bus bindings) works the same on the WSL
host and on the DUT over SSH, and needs nothing beyond BlueZ itself. An agent
is registered in the same session, and pairing prompts (passkey confirmation,
service authorization, PIN/passkey entry) are answered automatically.
"""

from __future__ import annotations

import os
import re
import statistics
import threading
import time
from dataclasses import asdict, dataclass, field

from ctt.core.executor import Executor

ADDR = r"[0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5}"
_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b[()][A-Z0-9]|[\x01\x02\r]")
_PROMPT = re.compile(r"\[[^\]\n]*\][#>] ?")

CAPABILITIES = ("NoInputNoOutput", "DisplayOnly", "DisplayYesNo", "KeyboardOnly",
                "KeyboardDisplay")


class BtError(RuntimeError):
    pass


def _addr_re(addr: str) -> str:
    return re.escape(addr.upper())


class BluetoothCtl:
    """One interactive bluetoothctl session on `executor` (host or DUT)."""

    def __init__(self, executor: Executor, label: str = "", command: list[str] | None = None,
                 pin: str = "0000", passkey: str | None = None, log=None):
        self.label = label or executor.name
        self.pin = pin
        self.passkey = passkey
        self.log = log  # callable(str) for raw transcript, optional
        self.proc = executor.popen(command or ["bluetoothctl"], interactive=True)
        self._buf = ""        # cleaned output
        self._pos = 0         # consumed by expect()
        self._resp_pos = 0    # scanned by auto-responders
        self._cond = threading.Condition()
        self._eof = False
        self.prompts: list[str] = []   # agent prompts seen (for reports)
        self._responders = [
            (re.compile(r"Confirm passkey (\d+) \(yes/no\)"), lambda m: "yes"),
            (re.compile(r"Authorize service [^\n]*\(yes/no\)"), lambda m: "yes"),
            (re.compile(r"Request authorization[^\n]*\(yes/no\)"), lambda m: "yes"),
            (re.compile(r"Accept pairing \(yes/no\)"), lambda m: "yes"),
            (re.compile(r"Enter PIN code:"), lambda m: self.pin),
            (re.compile(r"Enter passkey \(number in 0-999999\):"),
             lambda m: self.passkey if self.passkey is not None else "000000"),
        ]
        self._reader = threading.Thread(target=self._read, daemon=True)
        self._reader.start()

    # ---- low level --------------------------------------------------------

    def _read(self):
        fd = self.proc.stdout.fileno()
        while True:
            try:
                chunk = os.read(fd, 4096)
            except OSError:
                chunk = b""
            if not chunk:
                break
            text = _ANSI.sub("", chunk.decode("utf-8", "replace"))
            if self.log:
                self.log(text)
            with self._cond:
                self._buf += text
                self._auto_respond()
                self._cond.notify_all()
        with self._cond:
            self._eof = True
            self._cond.notify_all()

    def _auto_respond(self):
        # Called with the lock held. Answers each prompt exactly once.
        while True:
            best = None
            for rx, fn in self._responders:
                m = rx.search(self._buf, self._resp_pos)
                if m and (best is None or m.start() < best[0].start()):
                    best = (m, fn)
            if not best:
                return
            m, fn = best
            self._resp_pos = m.end()
            self.prompts.append(m.group(0))
            self.send(fn(m))

    def send(self, line: str):
        try:
            self.proc.stdin.write((line + "\n").encode())
            self.proc.stdin.flush()
        except (BrokenPipeError, OSError) as e:
            raise BtError(f"{self.label}: bluetoothctl is not running ({e})") from e

    def expect(self, patterns: list[str], timeout: float) -> tuple[int, re.Match]:
        """Waits for the first of `patterns` in output not yet consumed."""
        rx = [re.compile(p) for p in patterns]
        deadline = time.monotonic() + timeout
        with self._cond:
            while True:
                best = None
                for i, r in enumerate(rx):
                    m = r.search(self._buf, self._pos)
                    if m and (best is None or m.start() < best[1].start()):
                        best = (i, m)
                if best:
                    self._pos = best[1].end()
                    return best
                remaining = deadline - time.monotonic()
                if self._eof:
                    raise BtError(f"{self.label}: bluetoothctl exited "
                                  f"(output: {self._buf[-300:].strip()!r})")
                if remaining <= 0:
                    raise BtError(f"{self.label}: timeout waiting for {patterns} "
                                  f"(last output: {self._buf[-300:].strip()!r})")
                self._cond.wait(min(remaining, 0.2))

    def query(self, command: str, timeout: float = 10) -> str:
        """Runs a command and returns its output, delimited by a `version` sentinel."""
        with self._cond:
            start = self._pos
        self.send(command)
        self.send("version")
        _, m = self.expect([r"Version \d+\.\d+"], timeout)
        with self._cond:
            # Drop interactive prompts ("[bluetooth]# ", "[board]# ") that may
            # precede output on the same line.
            return _PROMPT.sub("", self._buf[start:m.start()])

    def close(self):
        if self.proc.poll() is None:
            try:
                self.send("quit")
                self.proc.wait(timeout=3)
            except Exception:  # noqa: BLE001
                pass
        if self.proc.poll() is None:
            self.proc.kill()
            self.proc.wait(timeout=3)
        self._reader.join(timeout=2)
        for f in (self.proc.stdin, self.proc.stdout):
            try:
                f.close()
            except OSError:
                pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # ---- operations -------------------------------------------------------

    def wait_ready(self, timeout: float = 10):
        self.send("version")
        self.expect([r"Version \d+\.\d+"], timeout)

    def register_agent(self, capability: str = "NoInputNoOutput", timeout: float = 10):
        if capability not in CAPABILITIES:
            raise BtError(f"capability must be one of {CAPABILITIES}")
        # bluetoothctl registers a KeyboardDisplay agent at start-up; "agent X"
        # would then only say "already registered" and keep the old capability.
        self.send("agent off")
        self.expect([r"Agent unregistered", r"No agent is registered"], timeout)
        self.send(f"agent {capability}")
        self.expect([r"Agent registered"], timeout)
        self.send("default-agent")
        self.expect([r"Default agent request successful"], timeout)

    def controllers(self) -> list[dict]:
        out = self.query("list")
        return [{"address": m.group(1).upper(), "name": m.group(2).strip(),
                 "default": bool(m.group(3))}
                for m in re.finditer(rf"Controller ({ADDR}) (.*?)( \[default\])?$", out, re.M)]

    def set(self, what: str, on: bool = True, timeout: float = 10):
        """power / pairable / discoverable / connectable."""
        state = "on" if on else "off"
        self.send(f"{what} {state}")
        i, m = self.expect([rf"Changing {what} {state} succeeded", rf"Failed to set {what}[^\n]*",
                            r"No default controller available"], timeout)
        if i != 0:
            raise BtError(f"{self.label}: {m.group(0)}")

    def scan(self, seconds: float, until_addr: str | None = None) -> dict[str, str]:
        """Discovers devices for `seconds` (or until `until_addr` shows up)."""
        self.send("scan on")
        if until_addr:
            try:
                self.expect([rf"Device {_addr_re(until_addr)}\b"], seconds)
            except BtError:
                pass
        else:
            time.sleep(seconds)
        self.send("scan off")
        return self.devices()

    def devices(self) -> dict[str, str]:
        out = self.query("devices")
        return {m.group(1).upper(): m.group(2).strip()
                for m in re.finditer(rf"^Device ({ADDR}) ?(.*)$", out, re.M)}

    def info(self, addr: str) -> dict | None:
        out = self.query(f"info {addr}")
        if re.search(r"not available", out):
            return None
        props = {}
        for m in re.finditer(r"^\s+([A-Za-z]+): (.*)$", out, re.M):
            key, val = m.group(1), m.group(2).strip()
            if key == "UUID":
                props.setdefault("UUIDs", []).append(val)
            else:
                props[key] = val
        return props

    def pair(self, addr: str, timeout: float = 40) -> tuple[bool, str]:
        self.send(f"pair {addr}")
        i, m = self.expect([r"Pairing successful", r"Failed to pair: ?([^\n]*)",
                            rf"Device {_addr_re(addr)} not available"], timeout)
        if i == 0:
            return True, "Pairing successful"
        if i == 1 and "AlreadyExists" in m.group(1):
            return True, "already paired"
        return False, m.group(0).strip()

    def trust(self, addr: str, timeout: float = 10) -> None:
        self.send(f"trust {addr}")
        i, m = self.expect([r"trust succeeded", r"Failed to set trusted[^\n]*",
                            rf"Device {_addr_re(addr)} not available"], timeout)
        if i != 0:
            raise BtError(f"{self.label}: {m.group(0)}")

    def remove(self, addr: str, timeout: float = 10) -> bool:
        """True if removed, False if the device was not known."""
        self.send(f"remove {addr}")
        i, m = self.expect([r"Device has been removed", rf"Device {_addr_re(addr)} not available",
                            r"Failed to remove device[^\n]*"], timeout)
        if i == 2:
            raise BtError(f"{self.label}: {m.group(0)}")
        return i == 0

    def connect(self, addr: str, timeout: float = 30) -> tuple[bool, str]:
        self.send(f"connect {addr}")
        i, m = self.expect([r"Connection successful", r"Failed to connect: ?([^\n]*)",
                            rf"Device {_addr_re(addr)} not available"], timeout)
        return i == 0, m.group(0).strip()

    def disconnect(self, addr: str, timeout: float = 15) -> None:
        self.send(f"disconnect {addr}")
        self.expect([r"Successful disconnected", r"Failed to disconnect[^\n]*",
                     r"not available", r"Missing device address"], timeout)


# ---- higher level flows ---------------------------------------------------------

def prepare_acceptor(ctl: BluetoothCtl, capability: str = "NoInputNoOutput",
                     discoverable: bool = True) -> None:
    """Puts a side (usually the DUT) into a state where it accepts pairing."""
    ctl.wait_ready()
    ctl.register_agent(capability)
    ctl.set("power", True)
    ctl.set("pairable", True)
    if discoverable:
        ctl.set("discoverable", True)


def default_controller(ctl: BluetoothCtl) -> str:
    ctrls = ctl.controllers()
    if not ctrls:
        raise BtError(f"{ctl.label}: no Bluetooth controller")
    for c in ctrls:
        if c["default"]:
            return c["address"]
    return ctrls[0]["address"]


@dataclass
class PairAttempt:
    index: int
    ok: bool
    message: str
    discover_s: float = 0.0
    pair_s: float = 0.0
    connect_ok: bool | None = None


@dataclass
class PairStressResult:
    peer: str
    attempts: list[PairAttempt] = field(default_factory=list)

    @property
    def success(self) -> int:
        return sum(a.ok for a in self.attempts)

    def summary(self) -> dict:
        times = [a.pair_s for a in self.attempts if a.ok]
        s = {"peer": self.peer, "attempts": len(self.attempts), "success": self.success,
             "success_rate": self.success / len(self.attempts) if self.attempts else 0.0}
        if times:
            s.update(pair_s_min=min(times), pair_s_avg=statistics.mean(times),
                     pair_s_max=max(times))
        return s

    def to_dict(self) -> dict:
        return {"summary": self.summary(), "attempts": [asdict(a) for a in self.attempts]}


def pair_once(ctl: BluetoothCtl, addr: str, scan_timeout: float, pair_timeout: float,
              trust: bool = True) -> PairAttempt:
    t0 = time.monotonic()
    if ctl.info(addr) is None:
        ctl.scan(scan_timeout, until_addr=addr)
        if ctl.info(addr) is None:
            return PairAttempt(0, False, f"{addr} not found by scan within {scan_timeout:g}s",
                               discover_s=time.monotonic() - t0)
    t1 = time.monotonic()
    ok, msg = ctl.pair(addr, pair_timeout)
    t2 = time.monotonic()
    if ok and trust:
        ctl.trust(addr)
    return PairAttempt(0, ok, msg, discover_s=t1 - t0, pair_s=t2 - t1)


def pair_stress(ctl: BluetoothCtl, addr: str, count: int, scan_timeout: float = 20,
                pair_timeout: float = 40, connect: bool = False, settle_s: float = 1.0,
                on_attempt=None) -> PairStressResult:
    """remove -> discover -> pair (-> connect) `count` times."""
    res = PairStressResult(peer=addr)
    for i in range(1, count + 1):
        try:
            ctl.remove(addr)
            time.sleep(settle_s)
            a = pair_once(ctl, addr, scan_timeout, pair_timeout)
            if a.ok and connect:
                a.connect_ok, _ = ctl.connect(addr)
        except BtError as e:
            a = PairAttempt(0, False, str(e))
        a.index = i
        res.attempts.append(a)
        if on_attempt:
            on_attempt(a)
    return res
