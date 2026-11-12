"""Line-based raw terminal on the console (both directions)."""

from __future__ import annotations

from ctt.btapp.service import BtTestService

HELP = ("Gõ dòng rồi Enter: PC gửi sang board.  !text: board gửi về PC (cần SSH).  "
        "/hex 48 65: gửi byte dạng hex.  /quit hoặc Ctrl+D: thoát.")


class ConsoleTerminal:
    def __init__(self, service: BtTestService, auto_deploy: bool = False):
        self.svc = service
        self.auto_deploy = auto_deploy

    @staticmethod
    def _show(prefix: str, data: bytes) -> None:
        print(f"\r[{prefix}] {data!r}\n> ", end="", flush=True)

    def _board_event(self, ev: dict) -> None:
        if ev.get("event") == "rx":
            self._show("board nhận", ev.get("data", b""))

    def run(self) -> int:
        session = self.svc.open_terminal(
            on_pc_rx=lambda d: self._show("PC nhận", d),
            on_closed=lambda why: print(f"\r[link closed] {why}"),
            on_board_event=self._board_event, auto_deploy=self.auto_deploy)
        t = session.target
        print(f"connected {t.address} channel {t.channel}. {HELP}")
        try:
            while True:
                line = input("> ")
                if line.strip() == "/quit":
                    break
                if line.startswith("/hex "):
                    try:
                        session.send_from_pc(bytes.fromhex(line[5:].replace(" ", "")))
                    except ValueError:
                        print("chuỗi hex không hợp lệ")
                elif line.startswith("!"):
                    if not session.has_board_side:
                        print("board send needs SSH to the board")
                        continue
                    session.send_from_board((line[1:] + "\n").encode())
                else:
                    session.send_from_pc((line + "\n").encode())
        except (EOFError, KeyboardInterrupt):
            pass
        finally:
            session.close()
        return 0
