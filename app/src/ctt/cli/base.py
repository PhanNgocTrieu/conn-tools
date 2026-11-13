"""Command pattern for the `ctt` command line.

Each sub-command is one class that declares its arguments (`configure`) and
executes (`run`). New commands are added by registering a class in
`ctt.cli.app.COMMANDS`; no central if/elif dispatch has to change.
"""

from __future__ import annotations

import argparse
from abc import ABC, abstractmethod


class Command(ABC):
    name: str = ""
    help: str = ""

    def add_to(self, subparsers) -> argparse.ArgumentParser:
        parser = subparsers.add_parser(self.name, allow_abbrev=False, help=self.help)
        self.configure(parser)
        parser.set_defaults(handler=self)
        return parser

    def configure(self, parser: argparse.ArgumentParser) -> None:
        """Declares the command's arguments."""

    @abstractmethod
    def run(self, args: argparse.Namespace) -> int:
        """Executes the command; returns the process exit code."""


class CommandGroup(Command):
    """A command made of sub-commands (`ctt bt pair`, `ctt usb gadget`, ...)."""

    dest: str = ""

    @abstractmethod
    def subcommands(self) -> list[Command]:
        ...

    def configure(self, parser):
        sub = parser.add_subparsers(dest=self.dest, required=True)
        for cmd in self.subcommands():
            cmd.add_to(sub)

    def run(self, args):  # pragma: no cover - argparse always selects a sub-command
        raise SystemExit(f"ctt {self.name}: a sub-command is required")
