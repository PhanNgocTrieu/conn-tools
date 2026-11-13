"""Links other than a plain socket, presented to CtpClient as a connected socket.

CtpClient works on any stream socket. BLE links are not sockets on every platform:
  ble-gatt  write-without-response + notifications through `bleak` (Windows WinRT, Linux BlueZ, macOS)
  ble-coc   L2CAP LE credit-based channel through `ctt-agent pipe --binary` (Linux only: Python cannot
            open an LE L2CAP socket, Windows has no API for it)
SocketBridge turns such a link into one end of a socketpair; a pump thread moves bytes between the
other end and the link.

Which link works where: `capabilities()`.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import socket
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from ctt.paths import HOST_AGENT

# CTT GATT service on the board (agent/src/transports/gatt.cpp)
GATT_SERVICE = "a0c70001-8f5e-4d3a-9c2b-1f0e5d4c3b2a"
GATT_RX = "a0c70002-8f5e-4d3a-9c2b-1f0e5d4c3b2a"     # PC -> board, write without response
GATT_TX = "a0c70003-8f5e-4d3a-9c2b-1f0e5d4c3b2a"     # board -> PC, notify
GATT_INFO = "a0c70004-8f5e-4d3a-9c2b-1f0e5d4c3b2a"   # read: "ctt-agent <version>"
LE_COC_PSM = 0x0080                                  # default LE PSM of `ctt-agent server -t l2cap -o type=le`

LINKS = ("rfcomm", "ble-gatt", "ble-coc")
LINK_LABELS = {"rfcomm": "Bluetooth Classic RFCOMM", "ble-gatt": "BLE GATT (write / notify)",
               "ble-coc": "BLE L2CAP CoC"}
# what the board runs for each link: ctt.bluetooth.board.agent_link_args()


class LinkError(RuntimeError):
    pass


@dataclass
class Capability:
    link: str
    ok: bool
    why: str


def _bleak_ok() -> tuple[bool, str]:
    try:
        import bleak  # noqa: F401
    except Exception as e:  # noqa: BLE001 - any import problem means "not available"
        return False, f"Python package bleak missing ({e.__class__.__name__}): pip install bleak"
    return True, "bleak"


def host_agent() -> Path | None:
    """ctt-agent for this PC (Linux): CTT_HOST_AGENT, build/host, next to the app, or on PATH."""
    cands = [os.environ.get("CTT_HOST_AGENT", ""),
             str(HOST_AGENT),
             str(Path(sys.argv[0]).resolve().parent / "ctt-agent"),
             shutil.which("ctt-agent") or ""]
    for c in cands:
        if c and Path(c).is_file() and os.access(c, os.X_OK):
            return Path(c)
    return None


def capabilities() -> list[Capability]:
    """What this PC can run. Each platform runs what it supports, the rest is reported with the reason."""
    from ctt.protocol.ctp import bluetooth_supported
    out = []
    rf = bluetooth_supported()
    out.append(Capability("rfcomm", rf, "OK" if rf else "this Python has no RFCOMM sockets"))
    ok, why = _bleak_ok()
    out.append(Capability("ble-gatt", ok, "OK (bleak)" if ok else why))
    if not sys.platform.startswith("linux"):
        out.append(Capability("ble-coc", False, f"{sys.platform}: no L2CAP LE CoC API for applications "
                                                "(Linux only)"))
    else:
        agent = host_agent()
        out.append(Capability("ble-coc", agent is not None,
                              f"OK ({agent})" if agent else "needs ctt-agent for this PC "
                              "(scripts/build-host.sh, or ctt-agent-x86_64 from the release, set CTT_HOST_AGENT)"))
    return out


def supported(link: str) -> Capability:
    for c in capabilities():
        if c.link == link:
            return c
    raise LinkError(f"unknown link {link!r}, expected one of {', '.join(LINKS)}")


# ---- bridge ------------------------------------------------------------------------------------

class SocketBridge:
    """socketpair: `app` goes to CtpClient, `link` side is pumped by the owner.

    `send_packet(data)` is called from the pump thread with chunks of at most `chunk` bytes;
    the owner calls `feed(data)` for every packet received from the link.
    """

    def __init__(self, send_packet: Callable[[bytes], None], chunk: int, name: str = "link"):
        self.app, self._side = socket.socketpair()
        self._send = send_packet
        self.chunk = max(1, chunk)
        self._closed = threading.Event()
        self.error: BaseException | None = None
        self._th = threading.Thread(target=self._pump, name=f"{name}-tx", daemon=True)
        self._th.start()

    def _pump(self) -> None:
        try:
            while not self._closed.is_set():
                data = self._side.recv(self.chunk)
                if not data:
                    break
                self._send(data)
        except OSError:
            pass
        except BaseException as e:  # noqa: BLE001 - reported to CtpClient as a closed link
            self.error = e
            self.link_closed()

    def feed(self, data: bytes) -> None:
        try:
            self._side.sendall(data)
        except OSError:
            pass

    def link_closed(self) -> None:
        """The link went away: CtpClient sees EOF."""
        try:
            self._side.shutdown(socket.SHUT_WR)
        except OSError:
            pass

    def close(self) -> None:
        self._closed.set()
        for s in (self._side, self.app):
            try:
                s.close()
            except OSError:
                pass


class LinkSocket:
    """What open_link() returns: `.sock` for CtpClient plus close() that also ends the link."""

    def __init__(self, sock: socket.socket, closer: Callable[[], None], info: dict):
        self.sock = sock
        self._closer = closer
        self.info = info

    def close(self) -> None:
        try:
            self._closer()
        finally:
            try:
                self.sock.close()
            except OSError:
                pass


# ---- BLE GATT (bleak) --------------------------------------------------------------------------

class _Loop:
    """One asyncio loop in a background thread for all bleak calls."""
    _lock = threading.Lock()
    _loop: asyncio.AbstractEventLoop | None = None

    @classmethod
    def get(cls) -> asyncio.AbstractEventLoop:
        with cls._lock:
            if cls._loop is None:
                cls._loop = asyncio.new_event_loop()
                threading.Thread(target=cls._loop.run_forever, name="ble-loop", daemon=True).start()
            return cls._loop

    @classmethod
    def run(cls, coro, timeout: float | None = None):
        """Runs a bleak coroutine; bleak's own exceptions come back as LinkError with a plain message."""
        try:
            return asyncio.run_coroutine_threadsafe(coro, cls.get()).result(timeout)
        except LinkError:
            raise
        except TimeoutError as e:
            raise LinkError("BLE: no answer in time (board out of range or not connectable?)") from e
        except Exception as e:  # noqa: BLE001 - BleakError & co. are not RuntimeError
            if e.__class__.__module__.startswith("bleak"):
                msg = e.args[0] if e.args and isinstance(e.args[0], str) else str(e)
                raise LinkError(f"BLE: {msg}") from e
            raise


def ble_scan(seconds: float = 8, only_ctt: bool = True) -> list[tuple[str, str, int | None]]:
    """[(address, name, rssi)] of BLE devices; only_ctt: those advertising the CTT service."""
    from bleak import BleakScanner

    async def scan():
        found = await BleakScanner.discover(timeout=seconds, return_adv=True)
        out = []
        for addr, (dev, adv) in found.items():
            uuids = [u.lower() for u in (adv.service_uuids or [])]
            if only_ctt and GATT_SERVICE not in uuids:
                continue
            out.append((dev.address, adv.local_name or dev.name or "", adv.rssi))
        return sorted(out, key=lambda d: -(d[2] or -999))
    return _Loop.run(scan(), timeout=seconds + 15)


def open_gatt(address: str, timeout: float = 20) -> LinkSocket:
    """Connect to the CTT GATT service of the board; returns a socket for CtpClient."""
    if not _bleak_ok()[0]:
        raise LinkError(_bleak_ok()[1])
    from bleak import BleakClient

    holder: dict = {}
    client = BleakClient(address, timeout=timeout,
                         disconnected_callback=lambda _c: holder["bridge"].link_closed() if "bridge" in holder
                         else None)

    async def connect():
        await client.connect()
        svc = client.services.get_service(GATT_SERVICE)
        if svc is None:
            await client.disconnect()
            raise LinkError(f"{address} has no CTT GATT service: start `ctt-agent server -t gatt` on the board")
        acquire = getattr(getattr(client, "_backend", None), "_acquire_mtu", None)
        if acquire:   # BlueZ: bleak reports 23 until the negotiated MTU is read
            try:
                await acquire()
            except Exception:  # noqa: BLE001 - keep the default
                pass
        mtu = client.mtu_size or 23
        info = ""
        try:
            info = bytes(await client.read_gatt_char(GATT_INFO)).decode(errors="replace")
        except Exception:  # noqa: BLE001 - informational only
            pass
        await client.start_notify(GATT_TX, lambda _c, data: holder["bridge"].feed(bytes(data)))
        return mtu, info

    # the bridge must exist before notifications can arrive
    def send(data: bytes) -> None:
        _Loop.run(client.write_gatt_char(GATT_RX, data, response=False), timeout=timeout)

    bridge = SocketBridge(send, 20, "gatt")
    holder["bridge"] = bridge
    try:
        mtu, info = _Loop.run(connect(), timeout=timeout + 10)
    except BaseException:
        bridge.close()
        raise
    bridge.chunk = max(20, mtu - 3)

    def closer():
        try:
            _Loop.run(client.disconnect(), timeout=10)
        except Exception:  # noqa: BLE001
            pass
        bridge.close()
    return LinkSocket(bridge.app, closer, {"link": "ble-gatt", "att_mtu": mtu, "payload": mtu - 3,
                                           "board": info})


# ---- BLE L2CAP CoC (Linux, through the host agent) --------------------------------------------

def open_le_coc(address: str, psm: int = LE_COC_PSM, timeout: float = 20) -> LinkSocket:
    """Linux: `ctt-agent pipe --binary` connects the LE CoC channel; stdin/stdout carry the bytes."""
    cap = supported("ble-coc")
    if not cap.ok:
        raise LinkError("BLE L2CAP CoC: " + cap.why)
    agent = host_agent()
    proc = subprocess.Popen([str(agent), "pipe", "--binary", "-t", "l2cap", "-o", "type=le",
                             "--connect", f"{address}/{psm}"],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0)
    import json
    status = proc.stderr.readline().decode(errors="replace")   # {"event":"connected"} or an error
    try:
        ev = json.loads(status)
    except ValueError:
        ev = {"event": "error", "message": status.strip() or f"agent exited ({proc.poll()})"}
    if ev.get("event") != "connected":
        proc.kill()
        raise LinkError("BLE L2CAP CoC connect failed: " + str(ev.get("message") or ev.get("reason") or ev))

    def send(data: bytes) -> None:
        proc.stdin.write(data)

    bridge = SocketBridge(send, 4096, "le-coc")

    def reader():
        while True:
            try:
                data = os.read(proc.stdout.fileno(), 65536)
            except OSError:
                data = b""
            if not data:
                break
            bridge.feed(data)
        bridge.link_closed()
    threading.Thread(target=reader, name="le-coc-rx", daemon=True).start()

    def closer():
        try:
            proc.stdin.close()
        except OSError:
            pass
        try:
            proc.wait(3)
        except subprocess.TimeoutExpired:
            proc.kill()
        bridge.close()
    return LinkSocket(bridge.app, closer, {"link": "ble-coc", "psm": psm, **{k: v for k, v in ev.items()
                                                                          if k != "event"}})
