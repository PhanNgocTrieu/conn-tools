"""PyInstaller entry point for ctt-agent.exe (console: menu + sub-commands)."""
import sys

from ctt.btapp.__main__ import console_main

if __name__ == "__main__":
    sys.exit(console_main())
