"""Interactive console menu (ctt-agent-console.exe without arguments).

Made for a PC reached over SSH (Client Test setup), where no window can be
opened. Uses the same BtTestService and the same saved settings as the GUI.
"""

from __future__ import annotations

import sys
import threading
from typing import Callable

from ctt import __version__
from ctt.btapp.console.terminal import ConsoleTerminal
from ctt.btapp.formatting import interval_line, result_lines
from ctt.btapp.service import BtTestService, ThroughputParams
from ctt.btapp.settings import AppSettings, SettingsStore
from ctt.protocol.ctp import MODE_LABELS

MODES = ("tx", "rx", "bidir", "echo")


def ask(prompt: str, default: str = "") -> str:
    shown = f" [{default}]" if default != "" else ""
    value = input(f"{prompt}{shown}: ").strip()
    return value or default


def ask_yes(prompt: str, default: bool = True) -> bool:
    v = ask(f"{prompt} (y/n)", "y" if default else "n").lower()
    return v.startswith("y")


class ConsoleMenu:
    def __init__(self, store: SettingsStore | None = None):
        self.store = store or SettingsStore()
        self.settings: AppSettings = self.store.load()
        self.svc = BtTestService(self.settings)
        self.items: list[tuple[str, str, Callable[[], None]]] = [
            ("1", "Cài đặt (SSH board / không SSH / địa chỉ BT / channel)", self.configure),
            ("2", "Kiểm tra board (SSH)", self.check_board),
            ("3", "Deploy agent lên board (SSH)", self.deploy),
            ("4", "Scan Bluetooth", self.scan),
            ("5", "Thiết bị đã pair", self.paired),
            ("6", "Pair với board", self.pair),
            ("7", "Bỏ pair", self.unpair),
            ("8", "Đo throughput / latency", self.throughput),
            ("9", "Terminal: gửi / nhận dữ liệu", self.terminal),
            ("0", "Thoát", self.quit),
        ]
        self._running = True

    # ---- loop ------------------------------------------------------------------------

    def header(self) -> str:
        s = self.settings
        link = f"SSH {s.board}" if s.uses_ssh else "không SSH (agent chạy sẵn trên board)"
        return (f"\n=== CTT Bluetooth console {__version__} ===\n"
                f"Board: {link} | BT: {s.address() or '?'} | channel: {s.channel}")

    def run(self) -> int:
        ok, msg = self.svc.pairing_status()
        print(f"Bluetooth trên PC: {self.svc.pairing.name}: {'OK' if ok else msg}")
        try:
            while self._running:
                print(self.header())
                for key, label, _ in self.items:
                    print(f"  {key}) {label}")
                choice = input("Chọn: ").strip()
                action = next((fn for key, _, fn in self.items if key == choice), None)
                if not action:
                    print("Lựa chọn không hợp lệ")
                    continue
                self._safe(action)
        except (EOFError, KeyboardInterrupt):
            print()
        finally:
            self.store.save(self.settings)
            self.svc.close()
        return 0

    @staticmethod
    def _safe(action: Callable[[], None]) -> None:
        try:
            action()
        except KeyboardInterrupt:
            print("\n(dừng)")
        except (RuntimeError, OSError, ValueError) as e:
            print(f"Lỗi: {e}")

    # ---- actions -------------------------------------------------------------------------

    def configure(self) -> None:
        s = self.settings
        s.no_ssh = not ask_yes("Có SSH tới board?", s.uses_ssh)
        if s.uses_ssh:
            s.board = ask("SSH board (user@ip)", s.board)
        s.bt_addr = ask("Địa chỉ BT của board (trống = lấy qua SSH)", s.bt_addr).upper()
        s.channel = ask("RFCOMM channel (auto = hỏi board qua SSH; không SSH mặc định 22)", s.channel)
        self.store.save(s)
        print("Đã lưu.")

    def check_board(self) -> None:
        info = self.svc.check_board()
        fw = info.get("fw", {})
        print(f"  host       : {info['hostname'] or '?'} ({info['arch']})")
        print(f"  agent      : {info['agent'] or 'CHƯA CÓ -> chọn 3 để deploy'}")
        print(f"  BT address : {info.get('bt_address') or '?'}")
        print(f"  bt-manager : {fw.get('services', {}).get('bt-daemon', '-')}")
        if isinstance(fw.get("bt", {}).get("paired"), int):
            print(f"  bond       : {fw['bt']['paired']}/5")
        self.store.save(self.settings)

    def deploy(self) -> None:
        print("Deploy OK: " + self.svc.deploy_agent())

    def _devices(self, devs) -> None:
        for i, d in enumerate(devs, 1):
            print(f"  {i:2d}. {d.address}  {'[paired] ' if d.paired else ''}{d.name or '(không tên)'}")
        print(f"  ({len(devs)} thiết bị)")
        if devs and ask_yes("Chọn một thiết bị làm board?", False):
            idx = int(ask("Số thứ tự", "1")) - 1
            self.settings.bt_addr = devs[idx].address
            self.store.save(self.settings)

    def scan(self) -> None:
        print("Đang scan (10–15 s)...")
        self._devices(self.svc.scan(10))

    def paired(self) -> None:
        self._devices(self.svc.paired())

    def pair(self) -> None:
        addr = ask("Địa chỉ BT của board", self.settings.address())
        accept = self.settings.uses_ssh and ask_yes("Board tự chấp nhận pairing qua SSH?", True)
        ok, msg = self.svc.pair(addr, accept, on_pin=lambda p: print(f"  Mã xác nhận: {p}"),
                                on_log=lambda m: print(f"  {m}"))
        print(("OK  " if ok else "FAIL ") + msg)
        if ok:
            self.settings.bt_addr = addr.upper()
            self.store.save(self.settings)

    def unpair(self) -> None:
        addr = ask("Địa chỉ cần bỏ pair", self.settings.address())
        print(self.svc.unpair(addr)[1])

    def throughput(self) -> None:
        for i, m in enumerate(MODES, 1):
            print(f"  {i}) {MODE_LABELS[m]}")
        mode = MODES[int(ask("Chiều đo", "1")) - 1]
        params = ThroughputParams(
            mode=mode,
            duration_s=float(ask("Thời gian (s)", self.settings.duration)),
            block=int(ask("Block (byte)", self.settings.block)),
            rate_bps=int(float(ask("Giới hạn Mbit/s (0 = tối đa)", "0")) * 1e6),
            verify=ask_yes("Kiểm tra CRC?", True))
        self.settings.duration, self.settings.block = str(params.duration_s), str(params.block)
        self.store.save(self.settings)
        print("Ctrl+C để dừng sớm (kết quả vẫn được thu về).")
        outcome: dict = {}

        def work():
            try:
                outcome["result"] = self.svc.run_throughput(
                    params, on_interval=lambda iv: print(interval_line(iv)),
                    on_board_event=self._board_event,
                    on_connect=lambda t: print(f"Kết nối RFCOMM {t.address} channel {t.channel}..."))
            except Exception as e:  # noqa: BLE001 - shown below
                outcome["error"] = e

        worker = threading.Thread(target=work, daemon=True)
        worker.start()
        while worker.is_alive():
            try:
                worker.join(0.2)
            except KeyboardInterrupt:
                print("\nĐang dừng...")
                self.svc.stop_throughput()
        if "error" in outcome:
            raise outcome["error"]
        print("\n".join(result_lines(outcome["result"])))

    @staticmethod
    def _board_event(ev: dict) -> None:
        if ev.get("event") == "session_end" and not ev.get("ok"):
            print(f"  Board: phiên lỗi: {ev.get('message')}")
        elif ev.get("event") in ("error", "warning"):
            print(f"  Board {ev['event']}: {ev.get('message')}")

    def terminal(self) -> None:
        ConsoleTerminal(self.svc).run()

    def quit(self) -> None:
        self._running = False


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    return ConsoleMenu().run()
