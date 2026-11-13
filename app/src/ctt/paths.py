"""Where things live in the repository (one place, so the layout can change safely).

    agent/        C++ agent (runs on the board / a Linux PC): include, src, tests, cmake, scripts
    app/          this Python package (src/ctt), its tests and pyproject.toml
    packaging/    yocto/ (layer + recipe with local source), systemd/, windows/ (PyInstaller)
    config/       boards/ (board profiles), profiles/ (test profiles), i2c/ (examples)
    scripts/      build / release automation, dev/ (environment setup)
    build/        CMake build trees (not committed)
    output/       release files per version and test reports (not committed)
"""

from __future__ import annotations

from pathlib import Path

PACKAGE = Path(__file__).resolve().parent          # app/src/ctt
REPO_ROOT = PACKAGE.parents[2]
AGENT_SRC = REPO_ROOT / "agent"
CONFIG = REPO_ROOT / "config"
CONFIGS = CONFIG / "boards"                        # board profiles (board.yaml, board.private.yaml)
PROFILES = CONFIG / "profiles"
BUILD = REPO_ROOT / "build"
OUTPUT = REPO_ROOT / "output"
REPORTS = OUTPUT / "reports"

HOST_AGENT = BUILD / "host" / "agent" / "ctt-agent"     # scripts/build-host.sh
BOARD_AGENT = BUILD / "dut" / "agent" / "ctt-agent"     # scripts/build-dut.sh
GADGET_SCRIPT = AGENT_SRC / "scripts" / "dut-usb-gadget.sh"
