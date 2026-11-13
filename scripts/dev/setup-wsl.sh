#!/usr/bin/env bash
# Prepare / verify a WSL2 (Ubuntu 24.04) host for CTT.
#
#   scripts/dev/setup-wsl.sh            # check only (no changes, no sudo)
#   scripts/dev/setup-wsl.sh --install  # install packages + configure (uses sudo)
set -uo pipefail

MODE=check
[[ "${1:-}" == "--install" ]] && MODE=install

PACKAGES=(
    build-essential cmake ninja-build pkg-config file git
    python3 python3-yaml python3-venv python3-tk   # python3-tk: ctt-agent desktop app
    bluez bluez-tools libbluetooth-dev      # BT host side (Phase 2)
    libusb-1.0-0-dev usbutils               # USB host side (Phase 5)
    iw wireless-tools iperf3                # WiFi (Phase 4)
    i2c-tools                               # MFi CP debugging
    g++-aarch64-linux-gnu                   # generic DUT cross build (fallback for Yocto SDK)
    openssh-client
)
MODULES=(bluetooth btusb rfcomm vhci_hcd usbip_core cfg80211 mac80211)

ok=0; warn=0; bad=0
pass() { printf '  \033[32m[ok]\033[0m   %s\n' "$*"; ((ok++)); }
note() { printf '  \033[33m[warn]\033[0m %s\n' "$*"; ((warn++)); }
fail() { printf '  \033[31m[fail]\033[0m %s\n' "$*"; ((bad++)); }
hdr()  { printf '\n== %s ==\n' "$*"; }

hdr "WSL"
if grep -qi microsoft /proc/version; then
    pass "running under WSL ($(uname -r))"
else
    note "not WSL - fine for native Linux, WSL-specific checks will be skipped"
fi
if [[ "$(cat /proc/1/comm 2>/dev/null)" == "systemd" ]]; then
    pass "systemd is PID 1 (needed for bluetoothd)"
else
    fail "systemd not running: set [boot] systemd=true in /etc/wsl.conf, then 'wsl --shutdown'"
    if [[ $MODE == install ]]; then
        if ! grep -q '^systemd=true' /etc/wsl.conf 2>/dev/null; then
            printf '[boot]\nsystemd=true\n' | sudo tee -a /etc/wsl.conf >/dev/null
            echo "         -> wrote /etc/wsl.conf; run 'wsl --shutdown' from Windows and reopen"
        fi
    fi
fi

WINPROFILE=""
if command -v cmd.exe >/dev/null 2>&1; then
    WINPROFILE=$(cmd.exe /c 'echo %USERPROFILE%' 2>/dev/null | tr -d '\r')
    [[ -n "$WINPROFILE" ]] && WINPROFILE=$(wslpath "$WINPROFILE" 2>/dev/null)
fi
if [[ -n "$WINPROFILE" && -f "$WINPROFILE/.wslconfig" ]] &&
   grep -qi '^\s*networkingMode\s*=\s*mirrored' "$WINPROFILE/.wslconfig"; then
    pass "networkingMode=mirrored (DUT can reach the host directly)"
else
    note "WSL networking is NAT: OK while the server runs on the DUT (default). For the host as" \
         "server, add 'networkingMode=mirrored' under [wsl2] in %USERPROFILE%\\.wslconfig"
fi

if command -v usbipd.exe >/dev/null 2>&1; then
    pass "usbipd-win found on Windows ($(usbipd.exe --version 2>/dev/null | tr -d '\r'))"
else
    note "usbipd-win not found (needed for USB devices and BT/WiFi dongles):" \
         "winget install usbipd   (in Windows PowerShell)"
fi

hdr "Kernel modules"
for m in "${MODULES[@]}"; do
    if [[ -d /sys/module/$m ]]; then
        pass "$m loaded"
    elif modprobe -n "$m" >/dev/null 2>&1; then
        if [[ $MODE == install ]] && sudo modprobe "$m" 2>/dev/null; then
            pass "$m loaded now"
        else
            note "$m available but not loaded (sudo modprobe $m)"
        fi
    else
        fail "$m not available in this kernel"
    fi
done
if [[ $MODE == install ]]; then
    printf '%s\n' "${MODULES[@]}" | sudo tee /etc/modules-load.d/ctt.conf >/dev/null
fi

hdr "Packages"
missing=()
for p in "${PACKAGES[@]}"; do
    dpkg -s "$p" >/dev/null 2>&1 || missing+=("$p")
done
if [[ ${#missing[@]} -eq 0 ]]; then
    pass "all packages installed"
elif [[ $MODE == install ]]; then
    sudo apt-get update && sudo apt-get install -y "${missing[@]}" && pass "installed: ${missing[*]}" \
        || fail "apt-get install failed"
else
    note "missing: ${missing[*]}"
fi

hdr "Bluetooth"
if systemctl is-active --quiet bluetooth 2>/dev/null; then
    pass "bluetooth.service active"
elif [[ $MODE == install ]] && systemctl list-unit-files bluetooth.service >/dev/null 2>&1; then
    sudo systemctl enable --now bluetooth && pass "bluetooth.service started"
else
    note "bluetooth.service not active (needs systemd + bluez)"
fi
if ls /sys/class/bluetooth/hci* >/dev/null 2>&1; then
    pass "adapter(s): $(ls /sys/class/bluetooth | tr '\n' ' ')"
else
    note "no BT adapter: plug a USB BT dongle and run scripts/dev/usbipd-attach.ps1 on Windows"
fi
if id -nG | grep -qw bluetooth; then
    pass "user in 'bluetooth' group"
elif [[ $MODE == install ]] && getent group bluetooth >/dev/null; then
    sudo usermod -aG bluetooth "$USER" && note "added $USER to 'bluetooth' (re-login needed)"
fi

hdr "USB (usbdevfs access for the CTT gadget 1d6b:0104)"
RULE=/etc/udev/rules.d/99-ctt-usb.rules
if [[ -f $RULE ]]; then
    pass "udev rule $RULE"
elif [[ $MODE == install ]]; then
    echo 'SUBSYSTEM=="usb", ATTR{idVendor}=="1d6b", ATTR{idProduct}=="0104", MODE="0666"' | sudo tee $RULE >/dev/null
    sudo udevadm control --reload-rules 2>/dev/null || true
    pass "installed udev rule $RULE"
else
    note "no udev rule for the CTT gadget: 'ctt-agent client -t usb' would need root"
fi

hdr "Python"
python3 -c 'import yaml' 2>/dev/null && pass "PyYAML available" || fail "PyYAML missing (apt install python3-yaml)"

hdr "Result"
echo "  ok=$ok warn=$warn fail=$bad"
[[ $MODE == check && ( $warn -gt 0 || $bad -gt 0 ) ]] && echo "  run with --install to fix what can be fixed automatically"
exit $(( bad > 0 ? 1 : 0 ))
