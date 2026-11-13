# CTT — Connectivity Test Tool

[![CI](https://github.com/PhanNgocTrieu/conn-tools/actions/workflows/btapp.yml/badge.svg?branch=main)](https://github.com/PhanNgocTrieu/conn-tools/actions/workflows/btapp.yml)
[![Release](https://img.shields.io/github/v/release/PhanNgocTrieu/conn-tools?sort=semver)](https://github.com/PhanNgocTrieu/conn-tools/releases)
![Board](https://img.shields.io/badge/board-Linux%20aarch64%20%7C%20x86__64-informational)
![PC](https://img.shields.io/badge/PC-Windows%20%7C%20Linux-informational)

CTT measures **throughput, latency and data integrity** of connectivity links between an embedded **Linux board** and a **PC**. The links are Bluetooth Classic, Bluetooth Low Energy, WiFi, Ethernet, USB and I2C.

Every test checks the sequence number and CRC of each block, and prints a live trace on both ends once per second. Results are written in machine-readable formats (JSON, CSV, JUnit, HTML).

> The detailed guides in [`docs/`](docs/README.md) are written in Vietnamese.

---

## Contents

- [Features](#features)
- [Architecture](#architecture)
- [Supported platforms](#supported-platforms)
- [Installation](#installation)
- [Quick start](#quick-start)
- [Results and reports](#results-and-reports)
- [Board profile](#board-profile)
- [Documentation](#documentation)
- [Development](#development)
- [Status and known limitations](#status-and-known-limitations)

---

## Features

- **One test protocol for every link.**
  - [CTP](docs/project/ctp-spec.md) runs over RFCOMM, L2CAP (BR/EDR and LE CoC), BLE GATT, TCP, UDP, USB bulk (FunctionFS) and AF_UNIX.
  - The same four modes work on every link: `tx`, `rx`, `bidir` and `echo`.
- **Integrity is always checked.**
  - Every block carries a sequence number and a CRC32.
  - Results always report lost blocks, CRC errors and out-of-order blocks, next to Mbit/s and RTT (min / avg / p50 / p95 / p99 / max).
- **Live monitoring.**
  - Board and PC both print, once per second, the rate and the bytes/frames sent and received so far.
  - At the end, a `data:` line compares the totals of both ends.
- **Test suites.**
  - Bluetooth Classic: BT-TP-01…06. BLE: BLE-TP-01…06.
  - Level `measure` (default) only records the numbers. Levels `baseline` and `target` add PASS/FAIL on Mbit/s and RTT limits.
  - Limits can be overridden from a JSON file.
- **Windows apps.**
  - `ctt-agent.exe` is a desktop app. `ctt-agent-console.exe` has a menu and scriptable sub-commands, and also works over SSH.
  - Both detect which links the PC supports.
- **Dependency-free agent.**
  - `ctt-agent` is a single static C++17 binary that only uses kernel interfaces.
  - For BLE GATT it talks to bluetoothd through its own built-in D-Bus client.
- **Board-agnostic.** Product-specific behaviour is declared in a [board profile](#board-profile), not in the code.
- **CI and Yocto ready.**
  - Exit codes, JUnit reports and baseline comparison for CI.
  - For Yocto: a recipe that fetches with git, a recipe that ships its source in your layer, and systemd services.

## Architecture

```
        PC (Windows / Linux)                                    Board (Linux)
 ┌──────────────────────────────────┐                ┌──────────────────────────────┐
 │ ctt-agent.exe / -console.exe     │                │ ctt-agent (C++17, static)    │
 │   CtpClient: tx/rx/bidir/echo    │    RFCOMM      │   server : CTP measurements  │
 │   live trace, suites, reports    │◄── BLE GATT ──►│   pipe   : raw data terminal │
 │ ctt (CLI): WiFi/BT/USB/I2C,      │    L2CAP LE    │   i2c, storage               │
 │   HTML/JUnit, baseline compare   │    TCP / UDP   │ systemd: ctt-agent-bt / -ble │
 └───────────────┬──────────────────┘    USB bulk    └──────────────┬───────────────┘
                 │                                                  │
                 └────────── SSH (optional): deploy, start the ─────┘
                             server, board trace, pairing
```

- **With SSH to the board,** the apps deploy the agent, start the server and show the board's trace next to their own.
- **Without SSH** (for example a board with only a serial console), the agent runs by hand or as a systemd service. The PC only needs Bluetooth.

## Supported platforms

| Link | Board (`ctt-agent`) | Windows PC | Linux PC |
|---|:-:|:-:|:-:|
| Bluetooth Classic RFCOMM | ✔ | ✔ | ✔ |
| BLE GATT (write / notify) | ✔ (`server -t gatt`) | ✔ (bleak / WinRT) | ✔ (bleak / BlueZ) |
| BLE L2CAP CoC | ✔ | ✗ (no application API) | ✔ |
| L2CAP BR/EDR | ✔ | – | ✔ (`ctt` CLI) |
| TCP / UDP (WiFi, Ethernet, USB-NCM) | ✔ | – | ✔ (`ctt` CLI) |
| USB bulk (FunctionFS gadget / host) | ✔ | – | ✔ (`ctt` CLI) |
| I2C (e.g. an authentication coprocessor) | ✔ | – | ✔ (`ctt` CLI) |

**Board requirements:**
- Linux with systemd and BlueZ (bluetoothd);
- configfs USB gadget, nl80211 WiFi;
- the kernel options listed in [docs/project/yocto-recipe.md](docs/project/yocto-recipe.md#5-kiểm-tra-kernel).

**PC requirements:** Windows 10/11 or Linux, with a Bluetooth adapter managed by the operating system.

## Installation

### Prebuilt binaries (recommended)

Download from [Releases](https://github.com/PhanNgocTrieu/conn-tools/releases): `vX.Y.Z` for official releases, or `latest-main` for the latest commit on `main`.

| File | For |
|---|---|
| `ctt-agent-aarch64` | Linux aarch64 board: copy to `/tmp/ctt-agent` and `chmod +x` |
| `ctt-agent-x86_64` | Linux x86_64 PC or board |
| `ctt-agent.exe` | Windows desktop app |
| `ctt-agent-console.exe` | Windows console app (also over SSH) |
| `SHA256SUMS` | Verify the download: `sha256sum -c SHA256SUMS` |

### Agent in the board image

A Yocto recipe and systemd services (disabled by default) are provided, see [docs/project/yocto-recipe.md](docs/project/yocto-recipe.md). If your build machine cannot reach this repository, `scripts/export-recipe.sh` exports a recipe that carries its own source.

### From source

```bash
scripts/dev/setup-wsl.sh --install   # Ubuntu / WSL packages (first time)
scripts/build-all.sh               # every release file into output/v<version>/, with a build report
```

See [Development](#development) for details.

## Quick start

### 1. Desktop app on Windows (board reachable over SSH)

1. Open `ctt-agent.exe`. On **1. Connect**: enter the board's SSH target → **Check board** → **Deploy agent** if needed → **Scan** → **Pair**.
2. **2. Throughput**: choose a direction → **▶ Start**. Leave *Min Mbit/s* empty to only measure.
3. **3. Terminal**: exchange raw data in both directions.

### 2. Console app, board with a serial console only

```sh
# on the board
/tmp/ctt-agent server -t rfcomm -e /22 -i 1        # or: systemctl start ctt-agent-bt
```
```bat
:: on the PC (also over SSH)
ctt-agent-console.exe caps                                      :: links this PC supports
ctt-agent-console.exe suite --addr <BOARD_BT_ADDR> --channel 22 :: BT-TP-01..06, level measure
ctt-agent-console.exe suite --addr <BOARD_BT_ADDR> --link ble-gatt
ctt-agent-console.exe run   --addr <BOARD_BT_ADDR> --channel 22 -m bidir -d 30 --json > result.json
```

Run it without arguments to get the numbered menu. All commands: [docs/guides/bt-app.md](docs/guides/bt-app.md).

### 3. `ctt` CLI (Linux / WSL): WiFi, Bluetooth, USB, I2C

```bash
export CTT_DUT=root@<board-ip>
scripts/ctt deploy                                   # copy the agent to the board
scripts/ctt board status                             # board state (according to the board profile)
scripts/ctt run -t tcp -m bidir -d 30s               # a single test
scripts/ctt profile config/profiles/wifi_tcp_throughput.yaml
scripts/ctt selftest                                 # self-check over TCP loopback, no board needed
```

## Results and reports

- **Live trace on both ends.** Board side, while the PC sends:
  ```
  [up   rx]   2.00-  3.00 s      0.396 Mbps  +50 fr   49.5 KB | received 152 fr  150.5 KB  lost=0 crc_err=0
  ```
  PC side:
  ```
       3.0s  PC->board  0.395 Mbit/s  sent 150.5 KB (152 fr)
  data: PC sent 150.5 KB, board received 150.5 KB
  ```
- **Suite levels:**
  - `measure`: each case is `OK`, `DATA ERR` or `ERROR`. The summary table shows average Mbit/s, the per-second min–max range, and RTT avg / p99.
  - `baseline` / `target`: `PASS` / `FAIL` against limits.
- **Output files:**
  - apps: `ctt-results/suite-<level>-<time>.json` and `.csv`;
  - CLI: `output/reports/<time>_<name>/` with `report.html`, `junit.xml` and CSV/JSON. `ctt compare` checks a run against a baseline run.
- **Exit codes:**
  - `0`: success;
  - `1`: failed checks;
  - `2`: usage or connection error.
  - With level `measure`, `suite` returns `0` when every case ran.

## Board profile

CTT works out of the box on a board with the standard Linux stack. Whatever a product does differently goes into a YAML file, so the code stays generic. Typical content:
- its own Bluetooth / USB / WiFi daemons;
- how the board accepts a pairing;
- a bond limit;
- a dual-role USB port owned by a daemon;
- extra operational commands.

```yaml
name: my-board
services: [bluetooth, my-bt-daemon]
bluetooth:
  max_bonds: 5
  pairing: {setup: [my-bt-cli pairable on], confirm: my-bt-cli confirm-loop}
usb:
  gadget_stop_services: [my-usb-daemon]
commands:
  wifi-ap: my-wifi-cli mode ap
```

- **Lookup order:**
  1. `--board-profile FILE` or `CTT_BOARD_PROFILE`;
  2. `./board.yaml`;
  3. `config/boards/board.yaml`;
  4. `config/boards/board.private.yaml`;
  5. `%APPDATA%\ctt-bt\board.yaml`.
- **Full template:** [config/boards/board.example.yaml](config/boards/board.example.yaml).
- **Note:** `*.private.yaml` files are never committed. Keep project-specific information there.

## Documentation

| Topic | Document |
|---|---|
| Bluetooth apps (desktop, console, sub-commands) | [docs/guides/bt-app.md](docs/guides/bt-app.md) |
| BLE: design, GATT service, usage | [docs/guides/bt-ble.md](docs/guides/bt-ble.md) |
| Bluetooth test plan | [docs/guides/bt-test-plan.md](docs/guides/bt-test-plan.md) |
| Bluetooth test cases (serial console + console / desktop app) | [docs/guides/bt-test-cases.md](docs/guides/bt-test-cases.md) |
| Step by step on a board with a serial console only | [docs/guides/bt-client-test.md](docs/guides/bt-client-test.md) |
| WiFi, USB, I2C with the `ctt` CLI | [docs/guides/TESTING.md](docs/guides/TESTING.md), [docs/guides/usb.md](docs/guides/usb.md), [docs/guides/bluetooth.md](docs/guides/bluetooth.md) |
| CTP protocol | [docs/project/ctp-spec.md](docs/project/ctp-spec.md) |
| Yocto: recipe, services, kernel | [docs/project/yocto-recipe.md](docs/project/yocto-recipe.md) |
| Releasing | [docs/project/release.md](docs/project/release.md) |
| WSL environment | [docs/project/wsl-setup.md](docs/project/wsl-setup.md) |

## Development

### Build

| Command | Result |
|---|---|
| `scripts/build-all.sh` | All release files into `output/v<version>/` (`linux-aarch64/`, `linux-x86_64/`, `windows/`), with `SHA256SUMS` and `BUILD-REPORT.txt`. Options: `--no-test`, `--no-windows` |
| `scripts/build-host.sh` | Agent for this PC (`build/host`), then all tests (C++ and Python) |
| `scripts/build-dut.sh --generic --static --strip` | aarch64 board agent (`build/dut`); `--sdk <env>` to use a Yocto SDK |
| `scripts/build-windows.sh [all\|gui\|console]` | Both `.exe` from WSL into `output/v<version>/windows/`; installs Python 3.12 per user (winget) on first use |
| `.\scripts\build-btapp.ps1` | Same, from PowerShell |

From a checkout, run the tools without installing anything: `scripts/ctt …` (CLI) and `scripts/ctt-app …` (Bluetooth app on Linux / WSL).

### Tests

```bash
scripts/build-host.sh                        # CTest: C++ unit tests, loopback, all Python tests
python3 -m unittest discover -s app/tests        # Python only
```

The tests run without hardware:
- Bluetooth: a fake `bluetoothctl`;
- BLE GATT: the real `ctt-agent` against a fake BlueZ on a private dbus-daemon;
- other links: TCP and AF_UNIX.

Set `CTT_BOARD_PROFILE=none` to keep local board profiles out of the tests.

### CI and releases

[GitHub Actions](.github/workflows/btapp.yml) builds the host and board agents and both `.exe` files, runs the tests on Linux and Windows, and smoke-tests the executables.
- **A push to `main`** refreshes the `latest-main` pre-release.
- **A `vX.Y.Z` tag** publishes a release. CI checks that the tag matches the version in the sources (`scripts/version.sh check`).

Full procedure: [docs/project/release.md](docs/project/release.md).

```bash
scripts/version.sh set X.Y.Z && git commit -am "Release X.Y.Z" && git tag -a vX.Y.Z -m "CTT X.Y.Z"
git push origin main vX.Y.Z
```

### Repository layout

Each top-level folder answers one question:

```
agent/          What runs on the board?   C++17 agent with its own CMake project
  include/ src/   CTP, transports (tcp, udp, rfcomm, l2cap, gatt, usb, unix), server / client / pipe,
                  minimal D-Bus client, i2c, storage
  tests/          unit tests, loopback test
  cmake/          cross toolchains
  scripts/        USB gadget helper (installed on the board as ctt-usb-gadget)
app/            What runs on the PC?      Python package (src layout)
  src/ctt/        protocol/ (CTP client, BLE links), btapp/ (desktop + console apps), bluetooth/,
                  platform/ (board profile), interfaces/, testing/, reporting/, cli/, paths.py
  tests/          tests and fakes/ (bluetoothctl, BlueZ on D-Bus, agent, ssh)
  pyproject.toml  requirements.txt
packaging/      How is it shipped?
  yocto/          meta-ctt layer (git) and recipe-local-src (source kept in your layer)
  systemd/        ctt-agent-bt.service, ctt-agent-ble.service (used by both recipes)
  windows/        PyInstaller entry points of ctt-agent.exe / ctt-agent-console.exe
config/         What do users adjust?
  boards/         board profiles (board.example.yaml; *.private.yaml stay local)
  profiles/       YAML test profiles for `ctt profile`
  i2c/            I2C test configuration example
scripts/        What is automated?        build-*, version.sh, export-recipe.sh, ctt / ctt-app launchers
  dev/            development environment (setup-wsl.sh, usbipd-attach.ps1)
docs/           project/ (design, release, Yocto, environment), guides/ (test guides)
output/         (not committed) v<version>/ release files, reports/ test reports
build/          (not committed) CMake build trees, logs, PyInstaller work files
```

## Status and known limitations

| Area | Status |
|---|---|
| TCP / UDP | Stable |
| Bluetooth RFCOMM, pairing, Windows apps | Run on hardware: Windows PC with a board over SSH, and with a board that has a serial console only |
| BLE GATT / L2CAP CoC | Verified against a fake BlueZ on a real D-Bus; **not yet run on a real radio** |
| USB bulk gadget / storage | Storage tested; gadget not yet run on hardware |
| I2C | Tested on a simulated bus; needs the device's real register map |
| Yocto | aarch64 agent built in CI; recipe not yet built inside a specific BSP |

- **Windows does not offer L2CAP LE CoC to applications.** The `ble-coc` link runs from Linux PCs only.
- **WSL does not share the Bluetooth adapter with Windows.** The `.exe` apps use the adapter managed by Windows. The `ctt` CLI inside WSL needs the dongle attached to WSL with usbipd.
