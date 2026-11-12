"""Line-based raw terminal on the console (both directions)."""

from __future__ import annotations

from ctt.btapp.service import BtTestService

HELP = ("Type a line + Enter: PC sends it to the board.  !text: board sends text to the PC (needs SSH).  "
        "/hex 48 65: send raw bytes.  /quit or Ctrl+D: exit.")


class ConsoleTerminal:
    def __init__(self, service: BtTestService, auto_deploy: bool = False):
        self.svc = service
        self.auto_deploy = auto_deploy
        self.totals = {"PC rx": 0, "PC tx": 0, "board rx": 0}

    def _show(self, prefix: str, data: bytes) -> None:
        """[PC rx 6 B, total 6 B] b'hi PC\n'  - byte count and running total per direction."""
        self.totals[prefix] = self.totals.get(prefix, 0) + len(data)
        print(f"\r[{prefix} {len(data)} B, total {self.totals[prefix]} B] {data!r}\n> ", end="", flush=True)

    def _send(self, session, data: bytes) -> None:
        session.send_from_pc(data)
        self.totals["PC tx"] += len(data)
        print(f"[PC tx {len(data)} B, total {self.totals['PC tx']} B]")

    def _board_event(self, ev: dict) -> None:
        if ev.get("event") == "rx":
            self._show("board rx", ev.get("data", b""))

    def run(self) -> int:
        session = self.svc.open_terminal(
            on_pc_rx=lambda d: self._show("PC rx", d),
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
                        self._send(session, bytes.fromhex(line[5:].replace(" ", "")))
                    except ValueError:
                        print("invalid hex string")
                elif line.startswith("!"):
                    if not session.has_board_side:
                        print("board send needs SSH to the board")
                        continue
                    session.send_from_board((line[1:] + "\n").encode())
                else:
                    self._send(session, (line + "\n").encode())
        except (EOFError, KeyboardInterrupt):
            pass
        finally:
            session.close()
        return 0
