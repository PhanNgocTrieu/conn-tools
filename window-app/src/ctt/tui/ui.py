"""Small text-UI toolkit for the console app: banner, numbered menus, validated prompts."""

from __future__ import annotations

import ipaddress
import re
import shutil
import sys
from dataclasses import dataclass
from typing import Callable

from ctt.reporting.console import color

WIDTH = 64


class Back(Exception):
    """Leave the current menu (0 / Ctrl+C at a prompt)."""


def _input(prompt: str) -> str:
    try:
        return input(prompt)
    except EOFError:
        raise SystemExit(0) from None
    except KeyboardInterrupt:
        print()
        raise Back from None


def rule(char: str = "-") -> str:
    return char * min(WIDTH, shutil.get_terminal_size((WIDTH, 20)).columns)


def banner(title: str, subtitle: str = "") -> None:
    print()
    print(color(rule("="), "36"))
    print(color(f"  {title}", "1;36"))
    if subtitle:
        print(color(f"  {subtitle}", "36"))
    print(color(rule("="), "36"))


def header(title: str, status: list[str]) -> None:
    print()
    print(color(f"[ {title} ]", "1;33") + " " + color(rule("-")[len(title) + 5:], "33"))
    for line in status:
        print(f"  {line}")


@dataclass
class Item:
    label: str
    action: Callable[[], object]
    hint: str = ""


NO_PAUSE = object()   # returned by menu(): going back from a sub-menu needs no [Enter]


def menu(title: str, items: list[Item], status: Callable[[], list[str]] = lambda: [],
         exit_label: str = "Back") -> object:
    """Shows the menu until 0 is chosen; each action runs, then the menu is shown again."""
    while True:
        header(title, status())
        for i, it in enumerate(items, 1):
            hint = color(f"  - {it.hint}", "2") if it.hint else ""
            print(f"  {color(f'{i:>2}', '1')}) {it.label}{hint}")
        print(f"  {color(' 0', '1')}) {exit_label}")
        try:
            ans = _input(color("\n  select > ", "1")).strip().lower()
        except Back:
            return NO_PAUSE
        if ans in ("0", "q", "b", "back", "exit"):
            return NO_PAUSE
        if not (ans.isdigit() and 1 <= int(ans) <= len(items)):
            print(color(f"  enter a number 0-{len(items)}", "31"))
            continue
        try:
            result = items[int(ans) - 1].action()
        except Back:
            print(color("  cancelled", "2"))
            continue
        if result is not NO_PAUSE:
            pause()


def choose(title: str, options: list[str], default: int | None = None) -> int:
    """One-off choice; returns the 0-based index. 0 / Ctrl+C raise Back."""
    print(f"\n  {color(title, '1')}")
    for i, o in enumerate(options, 1):
        mark = color(" (default)", "2") if default == i - 1 else ""
        print(f"   {i}) {o}{mark}")
    print("   0) cancel")
    while True:
        ans = _input("  > ").strip()
        if ans == "" and default is not None:
            return default
        if ans == "0":
            raise Back
        if ans.isdigit() and 1 <= int(ans) <= len(options):
            return int(ans) - 1
        print(color(f"  enter 1-{len(options)} or 0", "31"))


def ask(question: str, default: str = "", check: Callable[[str], str | None] | None = None) -> str:
    """Asks until `check` returns None (it returns an error text otherwise)."""
    shown = color(f" [{default}]", "2") if default else ""
    while True:
        value = _input(f"  {question}{shown}: ").strip() or default
        error = check(value) if check else None
        if not error:
            return value
        print(color(f"  -> {error}", "31"))


def ask_yes(question: str, default: bool = True) -> bool:
    v = ask(f"{question} (y/n)", "y" if default else "n",
            lambda v: None if v.lower() in ("y", "yes", "n", "no") else "answer y or n")
    return v.lower().startswith("y")


def pause() -> None:
    try:
        _input(color("\n  [Enter] continue", "2"))
    except Back:
        pass


HOST_RE = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?$")
DURATION_RE = re.compile(r"^\d+(\.\d+)?(ms|s|m)?$")


def check_ip(value: str) -> str | None:
    """IPv4 / IPv6 address or a host name."""
    if not value:
        return "an address is required"
    try:
        ipaddress.ip_address(value)
        return None
    except ValueError:
        pass
    if HOST_RE.match(value) and not re.match(r"^[\d.]+$", value):
        return None
    return f"'{value}' is not an IP address (e.g. 192.168.1.10)"


def check_board(value: str) -> str | None:
    """'local', an IP, or user@IP."""
    if value == "local":
        return None
    user, _, host = value.rpartition("@")
    if "@" in value and not user:
        return "expected user@ip, e.g. root@192.168.1.10"
    return check_ip(host)


def board_target(value: str) -> str:
    """192.168.1.10 -> root@192.168.1.10 (boards log in as root)."""
    return value if value == "local" or "@" in value else f"root@{value}"


def check_duration(value: str) -> str | None:
    return None if not value or DURATION_RE.match(value) else "e.g. 10s, 2m, 500ms"


def ok(msg: str) -> None:
    print(f"  {color('OK', '32')}  {msg}")


def warn(msg: str) -> None:
    print(f"  {color('!!', '33')}  {msg}")


def info(msg: str) -> None:
    print(f"  {color('..', '36')}  {msg}")


def use_utf8_output() -> None:
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(errors="replace")
            except (ValueError, OSError):
                pass
