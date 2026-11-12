"""Main window: styles, tabs, status line, log panel, shutdown."""

from __future__ import annotations

import threading
import tkinter as tk
from tkinter import ttk

from ctt import __version__
from ctt.btapp.gui.connect_tab import ConnectTab
from ctt.btapp.gui.context import AppContext
from ctt.btapp.gui.dispatcher import UiDispatcher
from ctt.btapp.gui.terminal_tab import TerminalTab
from ctt.btapp.gui.throughput_tab import ThroughputTab
from ctt.btapp.gui.widgets import LogPanel
from ctt.btapp.service import BtTestService
from ctt.btapp.settings import SettingsStore
from ctt.protocol.ctp import bluetooth_supported


class MainWindow:
    TABS = (ConnectTab, ThroughputTab, TerminalTab)

    def __init__(self, root: tk.Tk, store: SettingsStore | None = None):
        self.root = root
        root.title(f"CTT Bluetooth Test {__version__}")
        root.geometry("1100x760")
        root.minsize(900, 620)
        self.store = store or SettingsStore()
        self.settings = self.store.load()
        self.service = BtTestService(self.settings)
        self._style()

        top = ttk.Frame(root, padding=(10, 8))
        top.pack(fill="x")
        ttk.Label(top, text="CTT Bluetooth Test", font=("Segoe UI", 13, "bold")).pack(side="left")
        self.status = ttk.Label(top, text="Ready", style="Cap.TLabel")
        self.status.pack(side="right")

        paned = ttk.PanedWindow(root, orient="vertical")
        paned.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        notebook = ttk.Notebook(paned)
        paned.add(notebook, weight=4)
        self.logpanel = LogPanel(paned)
        paned.add(self.logpanel, weight=1)

        dispatcher = UiDispatcher(root, on_error=self._fail, log_error=lambda t: self.log(t, "err"))
        self.ctx = AppContext(self.service, self.settings, dispatcher, self.log, self.set_status,
                              self.sync_settings)
        self.tabs = []
        for cls in self.TABS:
            tab = cls(notebook, self.ctx)
            notebook.add(tab, text=tab.title)
            self.tabs.append(tab)

        root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.log(f"PC Bluetooth: {self.service.pairing.name}"
                 f"{'' if bluetooth_supported() else ' (this Python has no RFCOMM sockets!)'}")
        dispatcher.background(self.service.pairing_status, self.tabs[0].show_backend_status)

    @staticmethod
    def _style():
        s = ttk.Style()
        if "vista" in s.theme_names():
            s.theme_use("vista")
        elif "clam" in s.theme_names():
            s.theme_use("clam")
        s.configure("Big.TLabel", font=("Segoe UI", 20, "bold"))
        s.configure("Cap.TLabel", foreground="#5d6778")
        s.configure("Accent.TButton", font=("Segoe UI", 10, "bold"))

    def log(self, msg: str, tag: str = "") -> None:
        self.logpanel.write(msg, tag)

    def set_status(self, msg: str) -> None:
        self.status.configure(text=msg)

    def _fail(self, e: Exception) -> None:
        self.ctx.fail(e)

    def sync_settings(self) -> None:
        for tab in self.tabs:
            tab.apply_to(self.settings)
        self.store.save(self.settings)

    def on_close(self) -> None:
        self.sync_settings()
        self.service.stop_throughput()
        terminal = next(t for t in self.tabs if isinstance(t, TerminalTab))
        if terminal.session:
            terminal.close("app closed")
        threading.Thread(target=self.service.close, daemon=True).start()
        self.root.after(300, self.root.destroy)
