"""Settings shared by the GUI and the console app, persisted as JSON."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, fields
from pathlib import Path

from ctt.bluetooth.board import SERVICE_CHANNEL

AUTO = "auto"


def default_config_path() -> Path:
    base = os.environ.get("APPDATA") or os.path.join(Path.home(), ".config")
    return Path(base) / "ctt-bt" / "config.json"


@dataclass
class AppSettings:
    board: str = "root@192.168.1.10"   # SSH target of the board
    no_ssh: bool = False                 # agent already runs on the board (service / UART)
    bt_addr: str = ""                    # board Bluetooth address
    channel: str = AUTO                  # RFCOMM channel, "auto" = ask the board over SSH
    block: str = "990"
    duration: str = "10"

    @property
    def uses_ssh(self) -> bool:
        return not self.no_ssh

    def address(self) -> str:
        return self.bt_addr.strip().upper()

    def channel_or(self, default: int) -> int:
        """The configured channel, or `default` when it is 'auto' / empty."""
        ch = str(self.channel).strip().lower()
        return default if ch in ("", AUTO) else int(ch)

    def server_channel(self) -> int:
        """Channel to listen on (SSH: 0 = kernel picks) or to connect to (no SSH)."""
        return self.channel_or(0 if self.uses_ssh else SERVICE_CHANNEL)


class SettingsStore:
    def __init__(self, path: Path | None = None):
        self.path = path or default_config_path()

    def load(self) -> AppSettings:
        try:
            raw = json.loads(self.path.read_text())
        except (OSError, ValueError):
            return AppSettings()
        known = {f.name for f in fields(AppSettings)}
        return AppSettings(**{k: v for k, v in raw.items() if k in known})

    def save(self, settings: AppSettings) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(asdict(settings), indent=2))
        except OSError:
            pass
