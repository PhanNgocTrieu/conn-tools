"""Runs blocking work off the Tk thread and marshals results back onto it."""

from __future__ import annotations

import queue
import threading
import tkinter as tk
import traceback
from typing import Callable


class UiDispatcher:
    def __init__(self, root: tk.Tk, on_error: Callable[[Exception], None], log_error: Callable[[str], None]):
        self.root = root
        self._q: queue.Queue = queue.Queue()
        self._on_error = on_error
        self._log_error = log_error
        root.after(100, self._drain)

    def background(self, fn: Callable, done: Callable | None = None, fail: Callable | None = None) -> None:
        """fn() in a worker thread; done(result) or fail(exc) on the UI thread."""
        def work():
            try:
                r = fn()
            except Exception as e:  # noqa: BLE001
                self._q.put((fail or self._on_error, (e,)))
                return
            if done:
                self._q.put((done, (r,)))
        threading.Thread(target=work, daemon=True).start()

    def ui(self, fn: Callable, *args) -> None:
        """Schedules fn(*args) on the UI thread; callable from any thread."""
        self._q.put((fn, args))

    def _drain(self) -> None:
        try:
            while True:
                fn, args = self._q.get_nowait()
                try:
                    fn(*args)
                except Exception:  # noqa: BLE001
                    self._log_error(traceback.format_exc())
        except queue.Empty:
            pass
        self.root.after(50, self._drain)
