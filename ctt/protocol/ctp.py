"""Pure-Python CTP v1 client (see docs/ctp-spec.md).

Talks to `ctt-agent server` over any connected stream socket: RFCOMM on
Windows (Winsock AF_BTH) or Linux (BlueZ), or TCP in tests. Throughput on a
Bluetooth link is a few Mbit/s, far below what Python can frame.
"""

from __future__ import annotations

import os
import select
import socket
import statistics
import struct
import threading
import time
import zlib
from dataclasses import dataclass, field
from typing import Callable

MAGIC = 0x31505443
VERSION = 1
HEADER = struct.Struct("<IBBHIIQII")       # 32 bytes
START = struct.Struct("<B3xIIIQII")        # 32 bytes
FLAG_CRC = 1
START_VERIFY = 1
MAX_PAYLOAD = 1 << 20

T_START, T_START_ACK, T_DATA, T_STOP, T_STATS, T_ECHO_REQ, T_ECHO_RSP, T_ERROR = range(1, 9)
MODES = {"tx": 1, "rx": 2, "bidir": 3, "echo": 4}
MODE_LABELS = {"tx": "PC → board", "rx": "board → PC", "bidir": "hai chiều", "echo": "echo / RTT"}


class CtpError(RuntimeError):
    pass


def _now_ns() -> int:
    return time.monotonic_ns()


@dataclass
class Interval:
    """Live progress, emitted every `interval_s` while a test runs."""
    t: float                      # seconds since start
    up_mbps: float | None = None   # PC -> board (as sent by the PC)
    down_mbps: float | None = None  # board -> PC (as received by the PC)
    rtt_ms: float | None = None    # echo: average RTT of the interval
    lost: int = 0
    crc_errors: int = 0


@dataclass
class Result:
    mode: str
    block: int
    duration_s: float
    up_mbps: float | None = None          # goodput measured by the board (receiver)
    down_mbps: float | None = None        # goodput measured by the PC (receiver)
    up_sent_mbps: float | None = None     # what the PC pushed into the socket
    up_bytes: int = 0
    down_bytes: int = 0
    lost: int = 0
    out_of_order: int = 0
    crc_errors: int = 0
    rtt_ms: dict = field(default_factory=dict)   # min/avg/p50/p95/p99/max
    echoed: int = 0
    board_stats: dict = field(default_factory=dict)
    error: str = ""

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items()}


class _Pattern:
    """Pre-generated payload windows with their CRCs (same idea as the agent)."""

    def __init__(self, block: int):
        self.block = block
        self.period = max(1, min(256, (4 << 20) // max(block, 1)))
        self.buf = os.urandom(block + self.period)
        self.crcs = [zlib.crc32(self.buf[i:i + block]) for i in range(self.period)]

    def get(self, seq: int) -> tuple[memoryview, int]:
        i = seq % self.period
        return memoryview(self.buf)[i:i + self.block], self.crcs[i]


class _Reader:
    """Splits the byte stream into CTP frames."""

    def __init__(self, sock: socket.socket):
        self.sock = sock
        self.buf = bytearray()

    def _fill(self, n: int, deadline: float) -> bool:
        """Reads until the buffer holds n bytes; False when the deadline passes first.
        Never changes the socket timeout, so a sender thread is not affected."""
        while len(self.buf) < n:
            left = deadline - time.monotonic()
            if left <= 0:
                return False
            r, _, _ = select.select([self.sock], [], [], left)
            if not r:
                return False
            chunk = self.sock.recv(65536)
            if not chunk:
                raise CtpError("board closed the connection")
            self.buf += chunk
        return True

    def next(self, timeout: float = 15.0) -> tuple[tuple, bytes] | None:
        """Next frame, or None if none completed within `timeout` (partial data is kept)."""
        deadline = time.monotonic() + timeout
        if not self._fill(HEADER.size, deadline):
            return None
        h = HEADER.unpack_from(self.buf)
        if h[0] != MAGIC:
            raise CtpError("bad CTP magic: the peer is not a ctt-agent server (use Terminal for raw data)")
        if h[5] > MAX_PAYLOAD:
            raise CtpError("CTP payload too large")
        total = HEADER.size + h[5]
        if not self._fill(total, deadline):
            return None
        payload = bytes(self.buf[HEADER.size:total])
        del self.buf[:total]
        return h, payload

    def expect(self, timeout: float) -> tuple[tuple, bytes]:
        f = self.next(timeout)
        if f is None:
            raise CtpError(f"no answer from the board within {timeout:g} s")
        return f


def _frame(mtype: int, seq: int = 0, payload: bytes | memoryview = b"", crc: int | None = None,
           ts: int | None = None) -> list:
    flags = FLAG_CRC if crc is not None else 0
    hdr = HEADER.pack(MAGIC, VERSION, mtype, flags, seq, len(payload),
                      _now_ns() if ts is None else ts, crc or 0, 0)
    return [hdr, payload] if len(payload) else [hdr]


def _send(sock: socket.socket, parts: list, lock: threading.Lock | None = None) -> None:
    data = b"".join(bytes(p) for p in parts)
    if lock:
        with lock:
            sock.sendall(data)
    else:
        sock.sendall(data)


def _decode_stats(p: bytes) -> dict:
    if len(p) < 88:
        return {}
    flags = struct.unpack_from("<I", p)[0]
    names = ["rx_frames", "rx_bytes", "rx_lost", "rx_out_of_order", "rx_crc_errors",
             "rx_duration_ns", "tx_frames", "tx_bytes", "tx_duration_ns", "echoed"]
    vals = struct.unpack_from("<10Q", p, 8)
    d = dict(zip(names, vals))
    d["rx_jitter_ns"] = struct.unpack_from("<Q", p, 88)[0] if len(p) >= 96 else 0
    d["has_rx"], d["has_tx"] = bool(flags & 1), bool(flags & 2)
    return d


def _mbps(nbytes: int, ns: int) -> float | None:
    return nbytes * 8 / (ns / 1e9) / 1e6 if ns > 0 else None


def _pct(sorted_v: list[float], p: float) -> float:
    k = min(len(sorted_v) - 1, max(0, round(p / 100 * (len(sorted_v) - 1))))
    return sorted_v[k]


class CtpClient:
    def __init__(self, sock: socket.socket, timeout: float = 15.0):
        self.sock = sock
        self.timeout = timeout
        self._stop = threading.Event()

    def stop(self) -> None:
        """Ends the running test early (the result is still collected)."""
        self._stop.set()

    def run(self, mode: str = "tx", duration_s: float = 10, block: int = 990, rate_bps: int = 0,
            verify: bool = True, echo_interval_ms: float = 10, interval_s: float = 0.5,
            on_interval: Callable[[Interval], None] | None = None) -> Result:
        if mode not in MODES:
            raise ValueError(f"mode must be one of {list(MODES)}")
        sock = self.sock
        sock.settimeout(self.timeout)
        rd = _Reader(sock)
        res = Result(mode, block, duration_s)
        params = START.pack(MODES[mode], block, int(duration_s * 1000),
                            START_VERIFY if verify else 0, rate_bps,
                            int(echo_interval_ms * 1000), 0)
        _send(sock, _frame(T_START, payload=params))
        h, p = rd.expect(self.timeout)
        if h[2] == T_ERROR:
            raise CtpError("board refused: " + p.decode(errors="replace"))
        if h[2] != T_START_ACK:
            raise CtpError(f"expected START_ACK, got type {h[2]}")
        t0 = _now_ns()

        if mode == "echo":
            self._echo(rd, res, t0, duration_s, block, echo_interval_ms, interval_s, on_interval)
        else:
            self._data(rd, res, t0, mode, duration_s, block, rate_bps, verify, interval_s, on_interval)

        stats = {}
        while True:
            h, p = rd.expect(self.timeout)
            if h[2] == T_STATS:
                stats = _decode_stats(p)
                break
            if h[2] == T_ERROR:
                raise CtpError("board error: " + p.decode(errors="replace"))
            if h[2] in (T_DATA, T_STOP):
                continue
        res.board_stats = stats
        if stats.get("has_rx"):
            res.up_mbps = _mbps(stats["rx_bytes"], stats["rx_duration_ns"])
            res.up_bytes = stats["rx_bytes"]
            res.lost += stats["rx_lost"]
            res.out_of_order += stats["rx_out_of_order"]
            res.crc_errors += stats["rx_crc_errors"]
        res.echoed = stats.get("echoed", 0)
        return res

    # ---- data modes ----------------------------------------------------------

    def _data(self, rd, res, t0, mode, duration_s, block, rate_bps, verify, interval_s, on_interval):
        end = t0 + int(duration_s * 1e9)
        lock = threading.Lock()
        tx = {"bytes": 0, "frames": 0, "end": t0}
        rx = {"bytes": 0, "frames": 0, "next_seq": 0, "lost": 0, "ooo": 0, "crc": 0, "last": t0}
        errors: list[BaseException] = []

        def sender():
            pat = _Pattern(block)
            seq = 0
            try:
                while not self._stop.is_set():
                    now = _now_ns()
                    if now >= end:
                        break
                    if rate_bps:
                        due = t0 + int(tx["bytes"] * 8e9 / rate_bps)
                        if now < due:
                            time.sleep((min(due, end) - now) / 1e9)
                            continue
                    payload, crc = pat.get(seq)
                    _send(self.sock, _frame(T_DATA, seq, payload, crc if verify else None, now), lock)
                    seq += 1
                    tx["frames"] += 1
                    tx["bytes"] += block
                tx["end"] = _now_ns()
                _send(self.sock, _frame(T_STOP, seq), lock)
            except BaseException as e:  # noqa: BLE001 - reported by the caller
                errors.append(e)

        th = None
        if mode in ("tx", "bidir"):
            th = threading.Thread(target=sender, daemon=True)
            th.start()

        stop_seen = mode == "tx"   # tx: nothing comes back until STATS
        last_t, last_tx, last_rx = t0, 0, 0
        try:
            while True:
                now = _now_ns()
                if on_interval and now - last_t >= interval_s * 1e9:
                    dt = (now - last_t) / 1e9
                    on_interval(Interval(
                        t=(now - t0) / 1e9,
                        up_mbps=(tx["bytes"] - last_tx) * 8 / dt / 1e6 if th else None,
                        down_mbps=(rx["bytes"] - last_rx) * 8 / dt / 1e6 if mode != "tx" else None,
                        lost=rx["lost"], crc_errors=rx["crc"]))
                    last_t, last_tx, last_rx = now, tx["bytes"], rx["bytes"]
                if errors:
                    raise errors[0]
                if stop_seen and (th is None or not th.is_alive()):
                    break
                if stop_seen:          # tx: wait for the sender only
                    time.sleep(0.05)
                    continue
                f = rd.next(0.2)
                if f is None:
                    if _now_ns() > end + int(self.timeout * 1e9):
                        raise CtpError("board stopped sending (timeout)")
                    continue
                h, p = f
                if h[2] == T_DATA:
                    t = _now_ns()
                    seq = h[4]
                    if seq == rx["next_seq"]:
                        rx["next_seq"] += 1
                    elif seq > rx["next_seq"]:
                        rx["lost"] += seq - rx["next_seq"]
                        rx["next_seq"] = seq + 1
                    else:
                        rx["ooo"] += 1
                        rx["lost"] = max(0, rx["lost"] - 1)
                    if verify and (h[3] & FLAG_CRC) and zlib.crc32(p) != h[7]:
                        rx["crc"] += 1
                    rx["frames"] += 1
                    rx["bytes"] += len(p)
                    rx["last"] = t
                elif h[2] == T_STOP:
                    stop_seen = True
                elif h[2] == T_ERROR:
                    raise CtpError("board error: " + p.decode(errors="replace"))
        except BaseException:
            self._stop.set()        # make the sender give up too
            raise
        finally:
            if th:
                th.join(timeout=self.timeout)

        if th:
            res.up_sent_mbps = _mbps(tx["bytes"], tx["end"] - t0)
        if mode != "tx":
            res.down_bytes = rx["bytes"]
            res.down_mbps = _mbps(rx["bytes"], rx["last"] - t0)
            res.lost += rx["lost"]
            res.out_of_order += rx["ooo"]
            res.crc_errors += rx["crc"]

    # ---- echo ----------------------------------------------------------------

    def _echo(self, rd, res, t0, duration_s, block, interval_ms, interval_s, on_interval):
        end = t0 + int(duration_s * 1e9)
        payload = os.urandom(block)
        rtts: list[float] = []
        window: list[float] = []
        last_t = t0
        seq = 0
        while not self._stop.is_set() and _now_ns() < end:
            sent = _now_ns()
            _send(self.sock, _frame(T_ECHO_REQ, seq, payload, ts=sent))
            while True:
                h, p = rd.expect(self.timeout)
                if h[2] == T_ECHO_RSP and h[4] == seq:
                    break
                if h[2] == T_ERROR:
                    raise CtpError("board error: " + p.decode(errors="replace"))
            rtt = (_now_ns() - h[6]) / 1e6
            rtts.append(rtt)
            window.append(rtt)
            seq += 1
            now = _now_ns()
            if on_interval and now - last_t >= interval_s * 1e9:
                on_interval(Interval(t=(now - t0) / 1e9, rtt_ms=sum(window) / len(window)))
                window.clear()
                last_t = now
            if interval_ms > 0:
                time.sleep(interval_ms / 1000)
        _send(self.sock, _frame(T_STOP, seq))
        if rtts:
            s = sorted(rtts)
            res.rtt_ms = {"count": len(s), "min": s[0], "avg": statistics.fmean(s),
                          "p50": _pct(s, 50), "p95": _pct(s, 95), "p99": _pct(s, 99), "max": s[-1]}


# ---- links -----------------------------------------------------------------------

def bluetooth_supported() -> bool:
    return hasattr(socket, "AF_BLUETOOTH") and hasattr(socket, "BTPROTO_RFCOMM")


def open_rfcomm(addr: str, channel: int, timeout: float = 15) -> socket.socket:
    """Connected RFCOMM socket to addr/channel (Windows or Linux)."""
    if not bluetooth_supported():
        raise CtpError("this Python has no Bluetooth socket support (AF_BLUETOOTH/BTPROTO_RFCOMM)")
    s = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_STREAM, socket.BTPROTO_RFCOMM)
    s.settimeout(timeout)
    try:
        s.connect((addr, int(channel)))
    except OSError as e:
        s.close()
        raise CtpError(f"RFCOMM connect {addr} channel {channel}: {e}. Board paired? "
                       "Server listening on that channel?") from e
    return s
