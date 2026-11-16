"""PyInstaller entry point for ctt-agent-UI.exe (desktop window)."""
import sys

from ctt.btapp.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
