"""Discovery and pairing on the PC side.

Windows : WinRT (Windows.Devices.Enumeration / Windows.Devices.Bluetooth) through
          the `winrt-*` packages; the stack of the TP-Link dongle is the Windows one.
Linux   : BlueZ through bluetoothctl (WSL with the dongle attached by usbipd).
"""

from __future__ import annotations

import asyncio
import re
import sys
from dataclasses import dataclass
from typing import Callable

ADDR_RE = re.compile(r"([0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5})")


@dataclass
class Device:
    address: str
    name: str = ""
    paired: bool = False


def backend() -> "PairingBackend":
    if sys.platform == "win32":
        return WindowsPairing()
    return BluezPairing()


class PairingBackend:
    name = "?"

    def available(self) -> tuple[bool, str]:
        return True, ""

    def scan(self, seconds: float = 10) -> list[Device]:
        raise NotImplementedError

    def paired(self) -> list[Device]:
        raise NotImplementedError

    def pair(self, address: str, on_pin: Callable[[str], None] | None = None,
             timeout: float = 40) -> tuple[bool, str]:
        raise NotImplementedError

    def unpair(self, address: str) -> tuple[bool, str]:
        raise NotImplementedError


# ---- Windows -----------------------------------------------------------------------

def _addr_to_int(addr: str) -> int:
    return int(addr.replace(":", ""), 16)


def _int_to_addr(v: int) -> str:
    h = f"{v:012X}"
    return ":".join(h[i:i + 2] for i in range(0, 12, 2))


class WindowsPairing(PairingBackend):
    name = "Windows (WinRT)"

    def available(self):
        try:
            import winrt.windows.devices.bluetooth  # noqa: F401
            import winrt.windows.devices.enumeration  # noqa: F401
        except ImportError as e:
            return False, f"WinRT modules missing ({e}); pip install -r requirements-btapp.txt"
        return True, ""

    @staticmethod
    def _run(coro):
        return asyncio.run(coro)

    @staticmethod
    async def _find_all(aqs: str):
        from winrt.windows.devices.enumeration import DeviceInformation
        try:
            return await DeviceInformation.find_all_async(aqs)
        except TypeError:   # projections that name overloads explicitly
            return await DeviceInformation.find_all_async_aqs_filter(aqs)

    async def _list(self, paired: bool) -> list[Device]:
        from winrt.windows.devices.bluetooth import BluetoothDevice
        items = await self._find_all(BluetoothDevice.get_device_selector_from_pairing_state(paired))
        out = []
        for di in items:
            m = ADDR_RE.findall(di.id or "")
            addr = m[-1].upper() if m else ""
            if not addr:
                try:
                    dev = await BluetoothDevice.from_id_async(di.id)
                    addr = _int_to_addr(dev.bluetooth_address)
                except Exception:  # noqa: BLE001
                    continue
            out.append(Device(addr, di.name or "", paired))
        return out

    def scan(self, seconds: float = 10) -> list[Device]:
        # Windows runs the inquiry itself while enumerating unpaired endpoints.
        return self._run(self._list(False))

    def paired(self) -> list[Device]:
        return self._run(self._list(True))

    async def _device_info(self, address: str):
        from winrt.windows.devices.bluetooth import BluetoothDevice
        dev = await BluetoothDevice.from_bluetooth_address_async(_addr_to_int(address))
        if dev is None:
            raise RuntimeError(f"{address} not found: is the board powered, in range and discoverable?")
        return dev.device_information

    async def _pair(self, address: str, on_pin, timeout: float):
        from winrt.windows.devices.enumeration import DevicePairingKinds as K
        from winrt.windows.devices.enumeration import DevicePairingResultStatus as S
        di = await self._device_info(address)
        if di.pairing.is_paired:
            return True, "already paired"
        custom = di.pairing.custom

        def on_request(_sender, args):
            kind = args.pairing_kind
            if kind == K.CONFIRM_PIN_MATCH or kind == K.DISPLAY_PIN:
                if on_pin:
                    on_pin(args.pin)
                args.accept()
            elif kind == K.PROVIDE_PIN:
                args.accept("0000")
            else:
                args.accept()

        token = custom.add_pairing_requested(on_request)
        try:
            kinds = K.CONFIRM_ONLY | K.CONFIRM_PIN_MATCH | K.DISPLAY_PIN | K.PROVIDE_PIN
            res = await asyncio.wait_for(custom.pair_async(kinds), timeout)
        finally:
            custom.remove_pairing_requested(token)
        ok = res.status in (S.PAIRED, S.ALREADY_PAIRED)
        return ok, "Pairing successful" if ok else f"pairing failed: {res.status.name}"

    def pair(self, address, on_pin=None, timeout=40):
        return self._run(self._pair(address, on_pin, timeout))

    async def _unpair(self, address):
        from winrt.windows.devices.enumeration import DeviceUnpairingResultStatus as U
        di = await self._device_info(address)
        res = await di.pairing.unpair_async()
        ok = res.status in (U.UNPAIRED, U.ALREADY_UNPAIRED)
        return ok, "removed" if ok else f"unpair failed: {res.status.name}"

    def unpair(self, address):
        return self._run(self._unpair(address))


# ---- Linux / WSL (BlueZ) ----------------------------------------------------------------

class BluezPairing(PairingBackend):
    name = "BlueZ (bluetoothctl)"

    def __init__(self):
        from ctt.executor import LocalExecutor
        self.ex = LocalExecutor()

    def _ctl(self, **kw):
        import os
        import shlex
        from ctt.bt import BluetoothCtl
        cmd = os.environ.get("CTT_BLUETOOTHCTL")   # tests: fake bluetoothctl
        ctl = BluetoothCtl(self.ex, label="pc", command=shlex.split(cmd) if cmd else None, **kw)
        ctl.wait_ready()
        return ctl

    def available(self):
        import os
        import shutil
        if not os.environ.get("CTT_BLUETOOTHCTL") and not shutil.which("bluetoothctl"):
            return False, "bluetoothctl not found (apt install bluez)"
        try:
            with self._ctl() as ctl:
                if not ctl.controllers():
                    return False, ("no Bluetooth controller: attach the TP-Link dongle to WSL "
                                   "(scripts/usbipd-attach.ps1)")
        except Exception as e:  # noqa: BLE001
            return False, str(e)
        return True, ""

    def scan(self, seconds=10):
        with self._ctl() as ctl:
            ctl.set("power", True)
            found = ctl.scan(seconds)
            paired = set(self._paired_addrs(ctl))
        return [Device(a, n, a in paired) for a, n in sorted(found.items())]

    @staticmethod
    def _paired_addrs(ctl) -> list[str]:
        out = ctl.query("devices Paired")
        if "Invalid command" in out or "Device" not in out:
            out = ctl.query("paired-devices")
        return [m.group(1).upper() for m in re.finditer(r"^Device ([0-9A-Fa-f:]{17})", out, re.M)]

    def paired(self):
        with self._ctl() as ctl:
            names = ctl.devices()
            return [Device(a, names.get(a, ""), True) for a in self._paired_addrs(ctl)]

    def pair(self, address, on_pin=None, timeout=40):
        from ctt.bt import pair_once
        with self._ctl() as ctl:
            ctl.register_agent("DisplayYesNo")
            ctl.set("power", True)
            a = pair_once(ctl, address.upper(), scan_timeout=20, pair_timeout=timeout, trust=True)
            for p in ctl.prompts:
                m = re.search(r"(\d{6})", p)
                if m and on_pin:
                    on_pin(m.group(1))
            return a.ok, a.message

    def unpair(self, address):
        with self._ctl() as ctl:
            ok = ctl.remove(address.upper())
        return ok, "removed" if ok else "not paired / unknown"
