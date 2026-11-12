"""Tab 3: raw two-way terminal (PC pane and board pane)."""

from __future__ import annotations

import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk

from ctt.btapp.gui.context import AppContext
from ctt.btapp.service import TerminalSession

PC_TX, PC_RX, BOARD_TX = "#1d4ed8", "#15803d", "#b45309"
EOL = {"(none)": "", "\\n": "\n", "\\r\\n": "\r\n"}


class TerminalPane(ttk.LabelFrame):
    """Message log + input line of one side."""

    def __init__(self, master, title: str, send_text: str, on_send):
        super().__init__(master, text=title, padding=6)
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)
        self.box = tk.Text(self, state="disabled", wrap="char", font=("Consolas", 10))
        self.box.grid(row=0, column=0, columnspan=2, sticky="nsew")
        self.var = tk.StringVar()
        e = ttk.Entry(self, textvariable=self.var)
        e.grid(row=1, column=0, sticky="ew", pady=(6, 0))
        e.bind("<Return>", lambda _e: on_send())
        ttk.Button(self, text=send_text, command=on_send).grid(row=1, column=1, padx=(6, 0), pady=(6, 0))
        self.box.tag_configure("pc_tx", foreground=PC_TX)
        self.box.tag_configure("pc_rx", foreground=PC_RX)
        self.box.tag_configure("bd_tx", foreground=BOARD_TX)
        self.box.tag_configure("meta", foreground="#6b7280")

    def data(self, label: str, data: bytes, tag: str, as_hex: bool) -> None:
        if as_hex:
            shown = data.hex(" ")
        else:
            shown = data.decode("utf-8", errors="replace").replace("\r", "\\r").replace("\n", "\\n")
        self._insert(f"{time.strftime('%H:%M:%S')} {label} [{len(data)} B] ", "meta")
        self._insert(shown + "\n", tag)

    def meta(self, msg: str) -> None:
        self._insert(f"{time.strftime('%H:%M:%S')} {msg}\n", "meta")

    def _insert(self, text: str, tag: str) -> None:
        self.box.configure(state="normal")
        self.box.insert("end", text, tag)
        self.box.see("end")
        self.box.configure(state="disabled")


class TerminalTab(ttk.Frame):
    title = "  3. Terminal  "

    def __init__(self, master, ctx: AppContext):
        super().__init__(master, padding=12)
        self.ctx = ctx
        self.session: TerminalSession | None = None
        self.v_fmt = tk.StringVar(value="text")
        self.v_eol = tk.StringVar(value="\\n")
        self.columnconfigure(0, weight=1)
        self.columnconfigure(1, weight=1)
        self.rowconfigure(1, weight=1)
        self._toolbar().grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        self.pc = TerminalPane(self, "PC", "Send PC → board", self.on_pc_send)
        self.pc.grid(row=1, column=0, sticky="nsew", padx=(0, 6))
        self.bd = TerminalPane(self, "Board board (over SSH)", "Board sends → PC", self.on_board_send)
        self.bd.grid(row=1, column=1, sticky="nsew", padx=(6, 0))
        ttk.Label(self, text="With SSH the board runs `ctt-agent pipe`: you see what the board receives and can type what "
                             "the board sends. Without SSH the PC connects to the channel entered on the Connect tab "
                             "(any RFCOMM server on the board) and only the PC pane is used.",
                  style="Cap.TLabel", wraplength=1000).grid(row=3, column=0, columnspan=2, sticky="w",
                                                            pady=(6, 0))

    def apply_to(self, settings) -> None:
        """No persistent fields."""

    def _toolbar(self):
        bar = ttk.Frame(self)
        self.btn = ttk.Button(bar, text="Connect", style="Accent.TButton", command=self.on_toggle)
        self.btn.pack(side="left")
        ttk.Label(bar, text="  Format:").pack(side="left")
        ttk.Radiobutton(bar, text="Text", value="text", variable=self.v_fmt).pack(side="left")
        ttk.Radiobutton(bar, text="Hex", value="hex", variable=self.v_fmt).pack(side="left")
        ttk.Label(bar, text="  Line end:").pack(side="left")
        ttk.Combobox(bar, textvariable=self.v_eol, values=list(EOL), width=7,
                     state="readonly").pack(side="left")
        self.info = ttk.Label(bar, text="Not connected", style="Cap.TLabel")
        self.info.pack(side="right")
        return bar

    @property
    def as_hex(self) -> bool:
        return self.v_fmt.get() == "hex"

    def _payload(self, text: str) -> bytes:
        if self.as_hex:
            return bytes.fromhex(text.replace(" ", ""))
        return (text + EOL[self.v_eol.get()]).encode("utf-8")

    # ---- connect / disconnect --------------------------------------------------------

    def on_toggle(self):
        if self.session:
            return self.close("Disconnected")
        self.ctx.sync_settings()
        self.btn.configure(state="disabled")
        self.ctx.set_status("Opening terminal…")
        ui = self.ctx.dispatcher.ui
        self.ctx.dispatcher.background(
            lambda: self.ctx.service.open_terminal(
                on_pc_rx=lambda d: ui(self.pc.data, "board → PC", d, "pc_rx", self.as_hex),
                on_closed=lambda why: ui(self._remote_closed, why),
                on_board_event=lambda ev: ui(self._on_board_event, ev)),
            self._opened, self._open_failed)

    def _opened(self, session: TerminalSession):
        self.session = session
        t = session.target
        self.btn.configure(text="Disconnect", state="normal")
        self.info.configure(text=f"Connected {t.address} channel {t.channel}")
        self.pc.meta(f"— connected to {t.address} / channel {t.channel}")
        if not session.has_board_side:
            self.bd.meta("— (no SSH: the board side is not visible)")
        self.ctx.set_status("Terminal connected")

    def _open_failed(self, e):
        self.btn.configure(state="normal")
        self.ctx.fail(e)

    def _remote_closed(self, why: str):
        if self.session:
            self.close(why)

    def close(self, why: str):
        session, self.session = self.session, None
        if session:
            threading.Thread(target=session.close, daemon=True).start()
        self.btn.configure(text="Connect", state="normal")
        self.info.configure(text="Not connected")
        self.pc.meta(f"— {why}")
        self.ctx.set_status(why)

    def _on_board_event(self, ev):
        e = ev.get("event")
        if e == "rx":
            self.bd.data("board rx", ev.get("data", b""), "pc_tx", self.as_hex)
        elif e == "connected":
            self.bd.meta(f"— PC connected: {ev.get('peer')}")
        elif e == "listening":
            self.bd.meta(f"— board listening on channel {ev.get('channel')}")
        elif e in ("closed", "error"):
            self.bd.meta(f"— {ev.get('reason') or ev.get('message')}")

    # ---- send ------------------------------------------------------------------------------

    def _read(self, pane: TerminalPane) -> bytes | None:
        try:
            return self._payload(pane.var.get())
        except ValueError:
            messagebox.showerror("Hex", "Invalid hex string")
            return None

    def on_pc_send(self):
        if not self.session:
            return self.ctx.log("Terminal is not connected", "err")
        data = self._read(self.pc)
        if data is None:
            return
        try:
            self.session.send_from_pc(data)
        except OSError as e:
            return self.close(f"Send failed: {e}")
        self.pc.data("PC → board", data, "pc_tx", self.as_hex)
        self.pc.var.set("")

    def on_board_send(self):
        if not self.session or not self.session.has_board_side:
            return self.ctx.log("Board send needs SSH to the board", "err")
        data = self._read(self.bd)
        if data is None:
            return
        session = self.session

        def sent(_r):
            self.bd.data("board tx", data, "bd_tx", self.as_hex)
            self.bd.var.set("")
        self.ctx.dispatcher.background(lambda: session.send_from_board(data), sent)
