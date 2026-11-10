"""Running ctt-agent processes and consuming their JSON-lines event stream."""

from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import threading
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DUT_AGENT = "/tmp/ctt-agent"


def find_host_agent() -> str:
    candidates = [os.environ.get("CTT_AGENT"),
                  str(REPO_ROOT / "build" / "host" / "agent" / "ctt-agent"),
                  shutil.which("ctt-agent")]
    for c in candidates:
        if c and Path(c).is_file() and os.access(c, os.X_OK):
            return c
    raise FileNotFoundError(
        "ctt-agent for the host not found. Build it with scripts/build-host.sh "
        "or set CTT_AGENT=/path/to/ctt-agent")


class AgentProcess:
    """Wraps a running agent (local or remote) and parses its stdout events."""

    def __init__(self, proc: subprocess.Popen, label: str):
        self.proc = proc
        self.label = label
        self.events: "queue.Queue[dict | None]" = queue.Queue()
        self.all_events: list[dict] = []
        self.raw_lines: list[str] = []
        self.stderr_lines: list[str] = []
        self._t_out = threading.Thread(target=self._read_stdout, daemon=True)
        self._t_err = threading.Thread(target=self._read_stderr, daemon=True)
        self._t_out.start()
        self._t_err.start()

    def _read_stdout(self):
        for line in self.proc.stdout:
            line = line.rstrip("\n")
            self.raw_lines.append(line)
            if line.startswith("{"):
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                self.all_events.append(ev)
                self.events.put(ev)
        self.events.put(None)  # EOF marker

    def _read_stderr(self):
        for line in self.proc.stderr:
            self.stderr_lines.append(line.rstrip("\n"))

    def iter_events(self, timeout: float):
        """Yields events until EOF; raises TimeoutError if `timeout` passes."""
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"{self.label}: no completion within {timeout:.0f}s")
            try:
                ev = self.events.get(timeout=min(remaining, 0.5))
            except queue.Empty:
                continue
            if ev is None:
                return
            yield ev

    def wait_for(self, event: str, timeout: float) -> dict:
        for ev in self.iter_events(timeout):
            if ev.get("event") == event:
                return ev
            if ev.get("event") == "error":
                raise RuntimeError(f"{self.label}: {ev.get('message')}")
        raise RuntimeError(f"{self.label} exited before '{event}': {self.stderr_text()}")

    def wait(self, timeout: float) -> int:
        try:
            rc = self.proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self.kill()
            rc = self.proc.wait(timeout=5)
        self._t_out.join(timeout=2)
        self._t_err.join(timeout=2)
        return rc

    def kill(self):
        if self.proc.poll() is None:
            self.proc.kill()

    def close(self):
        """Kills the process if still running and releases its pipes."""
        self.kill()
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        self._t_out.join(timeout=2)
        self._t_err.join(timeout=2)
        for f in (self.proc.stdout, self.proc.stderr):
            if f:
                f.close()

    def stderr_text(self) -> str:
        return "\n".join(self.stderr_lines[-20:]).strip() or "(no stderr)"


def agent_common_args(spec) -> list[str]:
    args = ["--json", "-t", spec.transport, "-i", str(spec.interval)]
    if spec.sndbuf:
        args += ["--sndbuf", str(spec.sndbuf)]
    if spec.rcvbuf:
        args += ["--rcvbuf", str(spec.rcvbuf)]
    if spec.timeout is not None:
        args += ["--timeout", str(spec.timeout)]
    for k, v in (spec.transport_opts or {}).items():
        args += ["-o", f"{k}={v}"]
    return args


def server_args(agent: str, spec, listen: str | None, accept_timeout: float) -> list[str]:
    args = [agent, "server", "--once", "--accept-timeout", str(accept_timeout),
            *agent_common_args(spec)]
    if listen:
        args += ["-e", listen]
    return args


def client_args(agent: str, spec, endpoint: str) -> list[str]:
    args = [agent, "client", *agent_common_args(spec), "-e", endpoint, "-m", spec.mode,
            "-d", str(spec.duration)]
    if spec.block:
        args += ["-b", str(spec.block)]
    if spec.rate:
        args += ["-r", str(spec.rate)]
    if not spec.verify:
        args.append("--no-verify")
    if spec.count:
        args += ["-n", str(spec.count)]
    if spec.echo_interval_ms is not None:
        args += ["--echo-interval", f"{spec.echo_interval_ms}ms"]
    if spec.nodelay:
        args.append("--nodelay")
    return args
