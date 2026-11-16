"""What every tab needs from the window: service, settings, dispatcher, log, status."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from ctt.btapp.gui.dispatcher import UiDispatcher
from ctt.btapp.service import BtTestService
from ctt.btapp.settings import AppSettings


@dataclass
class AppContext:
    service: BtTestService
    settings: AppSettings
    dispatcher: UiDispatcher
    log: Callable[..., None]           # log(msg, tag="")
    set_status: Callable[[str], None]
    sync_settings: Callable[[], None]  # copy every tab's fields into `settings` and save

    def fail(self, e: Exception) -> None:
        self.log(f"Error: {e}", "err")
        self.set_status(f"Error: {e}")
