"""The board side, driven over SSH: agent deploy, test servers, raw pipe, and accepting the
PC's pairing request (with the board profile's commands when it has some, else a bluetoothctl
agent). Works with any Linux board; board specifics live in the board profile
(ctt/platform/board.py)."""

from __future__ import annotations

import json
import subprocess
import sys
import threading
from pathlib import Path
from typing import Callable

from ctt.core.executor import NO_WINDOW, SshExecutor

REMOTE_AGENT = "/tmp/ctt-agent"
INSTALLED_AGENT = "/usr/bin/ctt-agent"
SERVICE_CHANNEL = 22   # channel of ctt-agent-bt.service (yocto/meta-ctt), used without SSH
LE_PSM = 0x0080        # default LE PSM of `ctt-agent ... -t l2cap -o type=le`

# transport arguments of the agent per link; {n} = RFCOMM channel / LE PSM (0 = default)
_AGENT_LINK = {"rfcomm": "-t rfcomm -e /{n}", "ble-gatt": "-t gatt",
               "ble-coc": "-t l2cap -o type=le -e /{n}"}


def agent_link_args(link: str, n: int = 0) -> str:
    if link not in _AGENT_LINK:
        raise ValueError(f"unknown link {link!r}")
    if link == "ble-coc" and not n:
        n = LE_PSM
    return _AGENT_LINK[link].format(n=n)


def endpoint_of(ev: dict, link: str) -> tuple[str, int] | None:
    """(board address, channel / PSM) from the agent's "listening" event; None when incomplete."""
    addr = str(ev.get("bdaddr", "")).upper()
    if link == "rfcomm":
        return (addr, int(ev["channel"])) if "channel" in ev else None
    if link == "ble-coc":
        return (addr, int(ev["psm"])) if "psm" in ev else None
    return (addr, 0) if "service_uuid" in ev else None


def bundled_agent() -> Path | None:
    """aarch64 agent shipped with the app (PyInstaller bundle) or built in the repo."""
    roots = []
    if getattr(sys, "_MEIPASS", None):
        roots.append(Path(sys._MEIPASS) / "agent")
    repo = Path(__file__).resolve().parents[2]
    roots += [repo / "build" / "dut" / "agent", repo / "dist" / "agent"]
    for r in roots:
        p = r / "ctt-agent"
        if p.is_file():
            return p
    return None


class Board:
    def __init__(self, target: str, port: int | None = None, profile=None):
        # accept-new: first connection to a fresh board must not need a manual ssh.
        self.ex = SshExecutor(target, port, ["StrictHostKeyChecking=accept-new"])
        self.target = target
        self.agent = REMOTE_AGENT
        from ctt.platform.board import load_profile
        self.profile = profile or load_profile()
        self._server: subprocess.Popen | None = None
        self._server_link = ""
        self.server_endpoint: tuple[str, int] | None = None

    # ---- inventory -------------------------------------------------------------

    def find_agent(self) -> str | None:
        """Path of a working agent on the board (installed one preferred)."""
        for p in (INSTALLED_AGENT, REMOTE_AGENT):
            r = self.ex.run(f"[ -x {p} ] && {p} version", timeout=15)
            if r.returncode == 0 and "ctt-agent" in r.stdout:
                self.agent = p
                return r.stdout.strip()
        return None

    def check(self) -> dict:
        """SSH reachability, agent version, BT address and the state of the board's stack."""
        from ctt.platform import board as plat
        r = self.ex.run("uname -m; cat /etc/hostname 2>/dev/null", timeout=15)
        if r.returncode != 0:
            raise RuntimeError(f"SSH to {self.target} failed: {(r.stderr or r.stdout).strip()} "
                               "(network? ssh key? try `ssh " + self.target + "` in a terminal)")
        arch, _, host = r.stdout.strip().partition("\n")
        info = {"arch": arch, "hostname": host.strip(), "agent": self.find_agent()}
        st = plat.status(self.ex, self.profile)
        info["fw"] = st
        info["bt_address"] = st.get("bt", {}).get("Address") or self._hci_address()
        info["profile"] = self.profile.describe()
        return info

    def _hci_address(self) -> str | None:
        r = self.ex.run("cat /sys/class/bluetooth/hci0/address 2>/dev/null || "
                        "hciconfig hci0 2>/dev/null | sed -n 's/.*BD Address: \\([0-9A-F:]*\\).*/\\1/p'",
                        timeout=10)
        a = r.stdout.strip().upper()
        return a or None

    def deploy(self, local: Path | None = None) -> str:
        local = local or bundled_agent()
        if not local:
            raise RuntimeError("no aarch64 ctt-agent bundled with the app "
                               "(build it: scripts/build-dut.sh --generic --static --strip)")
        self.ex.put(str(local), REMOTE_AGENT + ".new")
        r = self.ex.run(f"chmod +x {REMOTE_AGENT}.new && mv -f {REMOTE_AGENT}.new {REMOTE_AGENT} "
                        f"&& {REMOTE_AGENT} version", timeout=30)
        if r.returncode != 0:
            raise RuntimeError("agent does not run on the board: " + (r.stderr or r.stdout).strip())
        self.agent = REMOTE_AGENT
        return r.stdout.strip()

    # ---- throughput server -------------------------------------------------------

    def start_server(self, channel: int = 0, on_event: Callable[[dict], None] | None = None,
                     timeout: float = 20, link: str = "rfcomm") -> tuple[str, int]:
        """Starts `ctt-agent server` for `link` (rfcomm / ble-gatt / ble-coc), kept running for many
        sessions. Returns (board bdaddr, channel or PSM; 0 for GATT)."""
        if (self._server and self._server.poll() is None and self.server_endpoint
                and self._server_link == link):
            return self.server_endpoint
        self.stop_server()
        cmd = f"exec {self.agent} server {agent_link_args(link, channel)} -J -i 1 --timeout 30"
        self._server = self._spawn(cmd)
        ready = threading.Event()
        holder: dict = {}

        def pump():
            assert self._server and self._server.stdout
            for line in self._server.stdout:
                ev = _parse(line)
                if ev.get("event") == "listening" and not ready.is_set():
                    holder.update(ev)
                    ready.set()
                if on_event:
                    on_event(ev)
            holder.setdefault("error", "board server exited")
            ready.set()

        threading.Thread(target=pump, daemon=True).start()
        ep = endpoint_of(holder, link) if ready.wait(timeout) else None
        if not ep:
            err = holder.get("message") or holder.get("error") or "no answer"
            self.stop_server()
            raise RuntimeError(f"cannot start the {link} server on the board: {err}")
        self._server_link = link
        self.server_endpoint = ep
        return ep

    def stop_server(self) -> None:
        if self._server:
            _terminate(self._server)
            self._server = None
        self.server_endpoint = None
        self._server_link = ""
        self._kill_remote("server -t")

    # ---- raw pipe ----------------------------------------------------------------------

    def start_pipe(self, channel: int = 0, on_event: Callable[[dict], None] | None = None,
                   timeout: float = 20, link: str = "rfcomm") -> "BoardPipe":
        cmd = f"exec {self.agent} pipe {agent_link_args(link, channel)}"
        proc = self._spawn(cmd, stdin=True)
        return BoardPipe(self, proc, on_event, timeout, link)

    # ---- pairing acceptance ------------------------------------------------------------

    def acceptor(self):
        """Context manager: board pairable + discoverable and confirming requests
        (the board profile's pairing commands, else an own bluetoothctl agent)."""
        from ctt.platform.board import ProfileAcceptor
        if self.profile.pairing.custom:
            return ProfileAcceptor(self.ex, self.profile.pairing)
        return _CtlAcceptor(self.ex)

    # ---- helpers ------------------------------------------------------------------------

    def _spawn(self, remote_cmd: str, stdin: bool = False) -> subprocess.Popen:
        return subprocess.Popen(self.ex._ssh(remote_cmd), text=True, bufsize=1,
                                stdin=subprocess.PIPE if stdin else subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, **NO_WINDOW)

    def _kill_remote(self, pattern: str) -> None:
        try:
            self.ex.run(f"pkill -f 'ctt-agent {pattern}' 2>/dev/null; true", timeout=10)
        except Exception:  # noqa: BLE001 - best effort
            pass

    def close(self) -> None:
        self.stop_server()


class BoardPipe:
    """`ctt-agent pipe` on the board: what the board receives, and data it sends."""

    def __init__(self, board: Board, proc: subprocess.Popen, on_event, timeout: float, link: str = "rfcomm"):
        self.board = board
        self.proc = proc
        self.link = link
        self.on_event = on_event
        self.endpoint: tuple[str, int] | None = None
        self._ready = threading.Event()
        self._err = ""
        threading.Thread(target=self._pump, daemon=True).start()
        if not self._ready.wait(timeout) or not self.endpoint:
            self.close()
            raise RuntimeError("cannot start the pipe on the board: " + (self._err or "no answer"))

    def _pump(self):
        assert self.proc.stdout
        for line in self.proc.stdout:
            ev = _parse(line)
            if ev.get("event") == "listening":
                self.endpoint = endpoint_of(ev, self.link)
                self._ready.set()
            elif ev.get("event") == "error":
                self._err = ev.get("message", "")
            if ev.get("event") == "rx" and "hex" in ev:
                ev["data"] = bytes.fromhex(ev["hex"])
            if self.on_event:
                self.on_event(ev)
        self._ready.set()

    def send(self, data: bytes) -> None:
        """Board -> PC: the board writes `data` on its end of the link."""
        if not self.proc.stdin or self.proc.poll() is not None:
            raise RuntimeError("board pipe is not running")
        self.proc.stdin.write(data.hex() + "\n")
        self.proc.stdin.flush()

    def close(self) -> None:
        try:
            if self.proc.stdin:
                self.proc.stdin.close()   # agent sees EOF and closes the link
        except OSError:
            pass
        _terminate(self.proc)
        self.board._kill_remote("pipe -t")


class _CtlAcceptor:
    """Board without pairing commands in its profile: a bluetoothctl agent accepting everything."""

    def __init__(self, ex):
        self.ex = ex
        self.ctl = None
        self.confirmed = 0

    def __enter__(self):
        from ctt.bluetooth.bluetoothctl import BluetoothCtl, prepare_acceptor
        self.ctl = BluetoothCtl(self.ex, label="board")
        prepare_acceptor(self.ctl, "NoInputNoOutput")
        return self

    def close(self):
        if self.ctl:
            self.confirmed = len(self.ctl.prompts)
            self.ctl.close()
            self.ctl = None

    def __exit__(self, *exc):
        self.close()


def _parse(line: str) -> dict:
    line = line.strip()
    if line.startswith("{"):
        try:
            return json.loads(line)
        except ValueError:
            pass
    return {"event": "log", "message": line}


def _terminate(p: subprocess.Popen) -> None:
    if p.poll() is None:
        p.terminate()
        try:
            p.wait(timeout=3)
        except subprocess.TimeoutExpired:
            p.kill()
