"""Argument groups and helpers shared by several `ctt` commands."""

from __future__ import annotations

import argparse
import os
import sys

from ctt.core.agent import DEFAULT_DUT_AGENT, REPO_ROOT, find_host_agent
from ctt.core.executor import Executor, LocalExecutor, make_executor
from ctt.reporting.console import color
from ctt.testing.runner import MODES


def add_dut_args(p: argparse.ArgumentParser) -> None:
    g = p.add_argument_group("DUT connection")
    g.add_argument("--dut", default=os.environ.get("CTT_DUT", "local"),
                   help="[user@]host of the DUT reached by SSH, or 'local' to run both sides "
                        "on this machine (default: $CTT_DUT or local)")
    g.add_argument("--ssh-port", type=int, default=None)
    g.add_argument("--ssh-opt", action="append", default=[],
                   help="extra ssh -o option, repeatable (e.g. StrictHostKeyChecking=no)")
    g.add_argument("--dut-agent", default=None,
                   help=f"ctt-agent path on the DUT (default {DEFAULT_DUT_AGENT})")
    g.add_argument("--host-agent", default=None, help="ctt-agent path on this machine")
    g.add_argument("--board-profile", default=None,
                   help="board profile YAML/JSON (default: $CTT_BOARD_PROFILE, board.yaml, "
                        "configs/board.yaml, configs/board.private.yaml; none = plain BlueZ)")


def add_test_args(p: argparse.ArgumentParser) -> None:
    g = p.add_argument_group("test")
    g.add_argument("-t", "--transport", default="tcp")
    g.add_argument("-m", "--mode", choices=MODES, default="tx",
                   help="tx: host->DUT, rx: DUT->host, bidir, echo (latency) - when the "
                        "server runs on the DUT")
    g.add_argument("-d", "--duration", default="10s")
    g.add_argument("-b", "--block", default=None, help="payload per frame, e.g. 990, 64K")
    g.add_argument("-r", "--rate", default=None, help="sender rate limit, e.g. 2M (bits/s)")
    g.add_argument("--no-verify", action="store_true", help="disable payload CRC checks")
    g.add_argument("-n", "--count", type=int, default=None, help="echo: number of round trips")
    g.add_argument("--echo-interval", type=float, default=None, dest="echo_interval_ms",
                   help="echo: ms between requests (0 = back-to-back)")
    g.add_argument("-i", "--interval", default="1s", help="report interval")
    g.add_argument("--port", type=lambda v: int(v, 0), default=None,
                   help="tcp port (5201) / RFCOMM channel (0 = auto) / L2CAP PSM")
    g.add_argument("--bdaddr", default=None,
                   help="BT address of the server side, if the agent cannot detect it")
    g.add_argument("-e", "--endpoint", default=None, help="explicit client target")
    g.add_argument("--listen", default=None, help="explicit server bind endpoint")
    g.add_argument("--server-side", choices=("dut", "host"), default="dut",
                   help="which side runs the agent server (default dut)")
    g.add_argument("--sndbuf", default=None)
    g.add_argument("--rcvbuf", default=None)
    g.add_argument("--nodelay", action="store_true")
    g.add_argument("--wifi", nargs="?", const="auto", default=None, dest="wifi_iface",
                   metavar="IFACE", help="record the DUT's WiFi link (signal, bitrate) "
                                         "before/after; IFACE default: auto")
    g.add_argument("--timeout", default=None,
                   help="agent idle I/O timeout, e.g. 30s (default 10s)")
    g.add_argument("-o", "--opt", action="append", default=[], metavar="KEY=VALUE",
                   help="transport option, repeatable")
    g.add_argument("--expect", action="append", default=[], metavar="KEY=VALUE",
                   help="pass criterion, e.g. up_mbps_min=1.5, lost_max=0")
    g.add_argument("--name", default=None)


def add_out_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--out", default=str(REPO_ROOT / "out"), help="report directory root")
    p.add_argument("-q", "--quiet", action="store_true", help="no per-interval output")


def parse_kv(items: list[str], what: str) -> dict:
    out = {}
    for it in items:
        if "=" not in it:
            raise SystemExit(f"ctt: {what} must be KEY=VALUE, got {it!r}")
        k, v = it.split("=", 1)
        out[k] = v
    return out


def dut_executor(args) -> Executor:
    return make_executor(args.dut, args.ssh_port, args.ssh_opt)


def dut_agent_path(args, dut: Executor) -> str:
    """Agent on the DUT side: the host build when 'DUT' is this machine."""
    if isinstance(dut, LocalExecutor):
        return args.dut_agent or args.host_agent or find_host_agent()
    return args.dut_agent or DEFAULT_DUT_AGENT


def warn(message: str) -> None:
    print(color(f"warning: {message}", "33"), file=sys.stderr)


def board_profile(args):
    """The board profile for this command (--board-profile, $CTT_BOARD_PROFILE or found by name)."""
    from ctt.platform.board import load_profile
    return load_profile(getattr(args, "board_profile", None))
