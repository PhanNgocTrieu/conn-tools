"""Where things live in the repository (one place, so the layout can change safely).

    linux-app/    C++ ctt-agent (Linux x86_64 + aarch64): src, include, tests, cmake, scripts
    window-app/   this Python package (src/ctt): Windows ctt-agent.exe / ctt-agent-UI.exe, and the
                  `ctt` automation CLI (scripts/ctt) used on Linux
    config/       boards/ (board profiles), profiles/ (test profiles), i2c/ (examples)   - shared
    packaging/    yocto/ (board image recipes), systemd/
    scripts/      build.sh (everything), version.sh, export-recipe.sh, dev/
    build/        generated only: ctt-agent-linux/, ctt-agent-aarch/, ctt-agent-window/, reports/,
                  .work/ (intermediate files)
"""

from __future__ import annotations

from pathlib import Path

PACKAGE = Path(__file__).resolve().parent          # window-app/src/ctt
REPO_ROOT = PACKAGE.parents[2]
AGENT_SRC = REPO_ROOT / "linux-app"
CONFIG = REPO_ROOT / "config"
CONFIGS = CONFIG / "boards"                        # board profiles (board.yaml, board.private.yaml)
PROFILES = CONFIG / "profiles"
BUILD = REPO_ROOT / "build"
REPORTS = BUILD / "reports"

HOST_AGENT = BUILD / "ctt-agent-linux" / "ctt-agent"    # scripts/build.sh linux
BOARD_AGENT = BUILD / "ctt-agent-aarch" / "ctt-agent"   # scripts/build.sh aarch
GADGET_SCRIPT = AGENT_SRC / "scripts" / "dut-usb-gadget.sh"
