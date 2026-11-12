"""Where commands run: on this machine, or on the DUT over SSH."""

from __future__ import annotations

import os
import shlex
import subprocess
import tempfile
from pathlib import Path


# Windows GUI app: do not flash a console window for every ssh/scp call.
NO_WINDOW = {"creationflags": 0x08000000} if os.name == "nt" else {}


def _popen(argv: list[str], interactive: bool) -> subprocess.Popen:
    if interactive:
        return subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, bufsize=0, **NO_WINDOW)
    return subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            stdin=subprocess.DEVNULL, text=True, bufsize=1, **NO_WINDOW)


class Executor:
    name = "?"

    def popen(self, argv: list[str], interactive: bool = False) -> subprocess.Popen:
        """interactive=True: stdin is a pipe and stdout is raw bytes, unbuffered
        (for driving tools like bluetoothctl)."""
        raise NotImplementedError

    def run(self, argv: list[str] | str, timeout: float = 30) -> subprocess.CompletedProcess:
        raise NotImplementedError

    @property
    def address(self) -> str:
        """Network address of this machine as seen by the peer."""
        raise NotImplementedError


class LocalExecutor(Executor):
    name = "local"

    def popen(self, argv, interactive=False):
        return _popen(argv, interactive)

    def run(self, argv, timeout=30):
        return subprocess.run(argv, shell=isinstance(argv, str), capture_output=True, text=True,
                              timeout=timeout)

    @property
    def address(self):
        return "127.0.0.1"


class SshExecutor(Executor):
    """Runs commands on the DUT with the system `ssh` client.

    Uses connection multiplexing so repeated commands (deploy, start server,
    collect logs) do not pay the handshake each time. Works with OpenSSH and
    dropbear on the DUT (scp uses the legacy protocol, -O, because dropbear
    images usually have no sftp-server).
    """

    def __init__(self, target: str, port: int | None = None, extra_opts: list[str] | None = None):
        self.target = target
        self.name = target
        control = Path(tempfile.gettempdir()) / "ctt-ssh-%C"
        self.opts = [
            "-o", "BatchMode=yes",
            "-o", "ConnectTimeout=5",
            "-o", "ServerAliveInterval=5",
        ]
        if os.name != "nt":   # Windows OpenSSH has no connection multiplexing
            self.opts += [
                "-o", "ControlMaster=auto",
                "-o", f"ControlPath={control}",
                "-o", "ControlPersist=120",
            ]
        if port:
            self.opts += ["-p", str(port)]
        for o in extra_opts or []:
            self.opts += ["-o", o]
        self._port = port

    @property
    def address(self):
        host = self.target.split("@", 1)[-1]
        return host.strip("[]")

    def _ssh(self, command: str) -> list[str]:
        return ["ssh", *self.opts, "-T", self.target, command]

    def popen(self, argv, interactive=False):
        # `exec` so that the remote shell is replaced by the agent process.
        return _popen(self._ssh("exec " + shlex.join(argv)), interactive)

    def run(self, argv, timeout=30):
        cmd = argv if isinstance(argv, str) else shlex.join(argv)
        return subprocess.run(self._ssh(cmd), capture_output=True, text=True, timeout=timeout,
                              stdin=subprocess.DEVNULL, **NO_WINDOW)

    def put(self, local: str, remote: str, timeout: float = 120) -> None:
        opts = [o if o != "-p" else "-P" for o in self.opts]  # scp uses -P for the port
        cmd = ["scp", "-O", "-q", *opts, local, f"{self.target}:{remote}"]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                           stdin=subprocess.DEVNULL, **NO_WINDOW)
        if r.returncode != 0:
            raise RuntimeError(f"scp failed: {r.stderr.strip()}")


def make_executor(dut: str, ssh_port: int | None = None,
                  ssh_opts: list[str] | None = None) -> Executor:
    if dut in ("", "local", "localhost"):
        return LocalExecutor()
    return SshExecutor(dut, ssh_port, ssh_opts)
