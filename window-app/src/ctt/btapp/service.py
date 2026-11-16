"""Application service of the Bluetooth test app.

All use cases (board check/deploy, discovery, pairing, throughput, terminal)
live here, so the desktop window and the console front-end only present
results; neither talks to SSH, sockets or the pairing backend directly.
"""

from __future__ import annotations

import socket
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from ctt.bluetooth import pairing
from ctt.bluetooth.board import LE_PSM, Board, BoardPipe, bundled_agent
from ctt.btapp.settings import AppSettings
from ctt.protocol import links
from ctt.protocol.ctp import CtpClient, Interval, Result, open_rfcomm

EventFn = Callable[[dict], None]


class ServiceError(RuntimeError):
    pass


@dataclass
class Target:
    """Where the PC connects: board address + RFCOMM channel / LE PSM (+ the board pipe, if any)."""
    address: str
    channel: int
    pipe: BoardPipe | None = None
    link: str = "rfcomm"

    def describe(self) -> str:
        if self.link == "ble-gatt":
            return f"{self.address} BLE GATT"
        if self.link == "ble-coc":
            return f"{self.address} BLE L2CAP CoC PSM 0x{self.channel:04x}"
        return f"{self.address} channel {self.channel}"


def open_link(target: Target) -> tuple[socket.socket, Callable[[], None], dict]:
    """Connected socket for CtpClient on the target's link, plus its closer and link details."""
    if target.link == "rfcomm":
        s = open_rfcomm(target.address, target.channel)
        return s, s.close, {"link": "rfcomm", "channel": target.channel}
    cap = links.supported(target.link)
    if not cap.ok:
        raise ServiceError(f"{links.LINK_LABELS[target.link]} is not available on this PC: {cap.why}")
    if target.link == "ble-gatt":
        ls = links.open_gatt(target.address)
    else:
        ls = links.open_le_coc(target.address, target.channel or LE_PSM)
    return ls.sock, ls.close, ls.info


@dataclass
class ThroughputParams:
    mode: str = "tx"
    duration_s: float = 10
    block: int = 990
    rate_bps: int = 0
    verify: bool = True


class TerminalSession:
    """Raw two-way link: PC end is a socket on the chosen link, board end (optional) a BoardPipe."""

    def __init__(self, sock: socket.socket, target: Target,
                 on_pc_rx: Callable[[bytes], None], on_closed: Callable[[str], None],
                 closer: Callable[[], None] | None = None):
        self.sock = sock
        self._closer = closer or sock.close
        self.target = target
        self._on_pc_rx = on_pc_rx
        self._on_closed = on_closed
        self._closed = False
        threading.Thread(target=self._reader, daemon=True).start()

    @property
    def has_board_side(self) -> bool:
        return self.target.pipe is not None

    def _reader(self) -> None:
        try:
            while True:
                data = self.sock.recv(4096)
                if not data:
                    break
                self._on_pc_rx(data)
        except OSError:
            pass
        if not self._closed:
            self._on_closed("board closed the connection")

    def send_from_pc(self, data: bytes) -> None:
        self.sock.sendall(data)

    def send_from_board(self, data: bytes) -> None:
        if not self.target.pipe:
            raise ServiceError("board send needs SSH to the board")
        self.target.pipe.send(data)

    def close(self) -> None:
        self._closed = True
        try:
            self._closer()
        except OSError:
            pass
        if self.target.pipe:
            self.target.pipe.close()


class BtTestService:
    def __init__(self, settings: AppSettings, pairing_backend: pairing.PairingBackend | None = None):
        self.settings = settings
        self.pairing = pairing_backend or pairing.backend()
        self._board: Board | None = None
        self._client: CtpClient | None = None

    # ---- board (SSH) ------------------------------------------------------------

    def board(self) -> Board:
        target = self.settings.board.strip()
        if not target:
            raise ServiceError("enter the board SSH, e.g. root@192.168.1.10")
        if not self._board or self._board.target != target:
            if self._board:
                self._board.close()
            self._board = Board(target)
        return self._board

    def check_board(self) -> dict:
        info = self.board().check()
        if info.get("bt_address") and not self.settings.address():
            self.settings.bt_addr = info["bt_address"]
        return info

    def deploy_agent(self, local: Path | None = None) -> str:
        return self.board().deploy(local or bundled_agent())

    def _board_with_agent(self, auto_deploy: bool) -> Board:
        board = self.board()
        if not board.find_agent():
            if not auto_deploy:
                raise ServiceError("ctt-agent is not on the board: Connect tab -> Deploy agent (console: menu 3)")
            board.deploy()
        return board

    # ---- discovery / pairing -------------------------------------------------------

    def pairing_status(self) -> tuple[bool, str]:
        return self.pairing.available()

    def scan(self, seconds: float = 10) -> list[pairing.Device]:
        return self.pairing.scan(seconds)

    def paired(self) -> list[pairing.Device]:
        return self.pairing.paired()

    def pair(self, address: str, accept_on_board: bool, on_pin: Callable[[str], None] | None = None,
             on_log: Callable[[str], None] | None = None) -> tuple[bool, str]:
        """PC pairs with the board; over SSH the board accepts the request itself."""
        acceptor = None
        if accept_on_board and self.settings.uses_ssh:
            acceptor = self.board().acceptor()
            acceptor.__enter__()
            if on_log:
                on_log(f"Board: pairable + discoverable ({type(acceptor).__name__})")
        try:
            return self.pairing.pair(address.upper(), on_pin=on_pin)
        finally:
            if acceptor:
                acceptor.close()

    def unpair(self, address: str) -> tuple[bool, str]:
        return self.pairing.unpair(address.upper())

    # ---- targets -----------------------------------------------------------------------

    def server_target(self, on_board_event: EventFn | None = None, auto_deploy: bool = False) -> Target:
        """Board RFCOMM server for throughput (started over SSH when SSH is used)."""
        addr = self.settings.address()
        if not self.settings.uses_ssh:
            if not addr:
                raise ServiceError("enter the board Bluetooth address (Connect tab / console menu 1)")
            return Target(addr, self.settings.server_channel(), link=self.settings.link)
        board = self._board_with_agent(auto_deploy)
        b_addr, ch = board.start_server(self.settings.server_channel(), on_event=on_board_event,
                                        link=self.settings.link)
        if not (addr or b_addr):
            raise ServiceError("board Bluetooth address unknown: enter it (Connect tab / console menu 1)")
        return Target(addr or b_addr, ch, link=self.settings.link)

    def pipe_target(self, on_board_event: EventFn | None = None, auto_deploy: bool = False) -> Target:
        """Board raw pipe for the terminal (SSH), or a fixed server channel (no SSH)."""
        addr = self.settings.address()
        if not self.settings.uses_ssh:
            if not addr:
                raise ServiceError("enter the board Bluetooth address")
            return Target(addr, self.settings.server_channel(), link=self.settings.link)
        board = self._board_with_agent(auto_deploy)
        board.stop_server()   # the server may hold the channel / the GATT service
        pipe = board.start_pipe(self.settings.server_channel(), on_event=on_board_event, link=self.settings.link)
        b_addr, ch = pipe.endpoint
        return Target(addr or b_addr, ch, pipe, link=self.settings.link)

    # ---- use cases -----------------------------------------------------------------------

    def run_throughput(self, params: ThroughputParams, on_interval: Callable[[Interval], None] | None = None,
                       on_board_event: EventFn | None = None, on_connect: Callable[[Target], None] | None = None,
                       auto_deploy: bool = False) -> Result:
        target = self.server_target(on_board_event, auto_deploy)
        if on_connect:
            on_connect(target)
        sock, closer, _info = open_link(target)
        try:
            self._client = CtpClient(sock)
            return self._client.run(params.mode, params.duration_s, params.block, params.rate_bps,
                                    params.verify, interval_s=1.0, on_interval=on_interval)
        finally:
            self._client = None
            closer()

    def stop_throughput(self) -> bool:
        if self._client:
            self._client.stop()
            return True
        return False

    def open_terminal(self, on_pc_rx: Callable[[bytes], None], on_closed: Callable[[str], None],
                      on_board_event: EventFn | None = None, auto_deploy: bool = False) -> TerminalSession:
        target = self.pipe_target(on_board_event, auto_deploy)
        try:
            sock, closer, _info = open_link(target)
        except Exception:
            if target.pipe:
                target.pipe.close()
            raise
        return TerminalSession(sock, target, on_pc_rx, on_closed, closer)

    def close(self) -> None:
        self.stop_throughput()
        if self._board:
            self._board.close()
