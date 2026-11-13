#!/bin/sh
# USB gadget for CTT on the board (run as root on the board, or via
# `ctt usb gadget up|down|status --dut root@<ip>`).
#
#   dut-usb-gadget.sh up [--ncm] [--stop-service UNIT]... [--role-file FILE] [--keep-services]
#                                  create configfs gadget + FunctionFS (bulk) [+ NCM network]
#   dut-usb-gadget.sh bind         bind to the first UDC (ctt-agent can do this itself: -o udc=auto)
#   dut-usb-gadget.sh down         unbind, remove everything, give the port back
#   dut-usb-gadget.sh status
#
# Dual-role ports: when no device controller (UDC) exists, `up` switches the port to
# device mode through its role file (--role-file, else auto: */dwc2_drdmode or
# /sys/class/usb_role/*/role) and waits for the UDC. If a daemon of the product owns the
# port, name it with --stop-service (ctt passes usb.gadget_stop_services of the board
# profile): `up` stops it, `down` switches the port back to host and restarts it.
# --keep-services refuses instead of stopping them.
#
# The FunctionFS function is served by `ctt-agent server -t usb` (endpoints
# ep1 IN / ep2 OUT), which writes the USB descriptors; the gadget can only be
# bound to the UDC after that, hence `-o udc=auto` on the agent.
# NCM (optional) gives a usb0 network interface for `-t tcp` / `-t udp` tests.
set -eu

G=/sys/kernel/config/usb_gadget/ctt
FFS_DIR=/dev/ffs-ctt
VID=0x1d6b      # Linux Foundation - lab use only
PID=0x0104      # Multifunction Composite Gadget

STATE=/run/ctt-usb-gadget       # what `up` changed, undone by `down`
STOP_SERVICES=""                # --stop-service UNIT (repeatable)
ROLE_FILE=""                    # --role-file FILE

die() { echo "error: $*" >&2; exit 1; }
udcs() { ls /sys/class/udc 2>/dev/null; }
abort() { give_back_port; die "$@"; }

# Role file of the dual-role port: --role-file, else the first one sysfs offers.
role_file() {
    f="$ROLE_FILE"
    [ -n "$f" ] && [ -f "$f" ] || f=$(ls /sys/bus/platform/devices/*/dwc2_drdmode 2>/dev/null | head -n1)
    [ -n "$f" ] || f=$(ls /sys/class/usb_role/*/role 2>/dev/null | head -n1)
    echo "$f"
}

svc_active() { command -v systemctl >/dev/null 2>&1 && systemctl is-active -q "$1"; }

take_port() {
    mkdir -p "$STATE"
    for svc in $STOP_SERVICES; do
        svc_active "$svc" || continue
        [ "$KEEP" = 1 ] && die "$svc is running and owns the USB port (drop --keep-services to stop it)"
        echo "stopping $svc (restarted by 'down')"
        systemctl stop "$svc" || abort "cannot stop $svc"
        echo "$svc" >> "$STATE/stopped-services"
    done
    [ -n "$(udcs)" ] && return 0
    rf=$(role_file)
    [ -n "$rf" ] || abort "no USB device controller and no role file: port is host-only"
    echo "switching $rf: $(cat "$rf" 2>/dev/null) -> device"
    echo "$rf" > "$STATE/role-file"
    echo device > "$rf" || abort "cannot write $rf"
    i=0
    while [ -z "$(udcs)" ] && [ $i -lt 50 ]; do sleep 0.1 2>/dev/null || sleep 1; i=$((i + 1)); done
    [ -n "$(udcs)" ] || abort "port switched to device but no UDC appeared in /sys/class/udc"
    echo "UDC: $(udcs | tr '\n' ' ')"
}

give_back_port() {
    if [ -s "$STATE/role-file" ]; then
        rf=$(cat "$STATE/role-file")
        echo host > "$rf" 2>/dev/null && echo "$rf -> host" || echo "warning: cannot restore $rf" >&2
    fi
    if [ -s "$STATE/stopped-services" ]; then
        for svc in $(cat "$STATE/stopped-services"); do
            systemctl start "$svc" && echo "$svc restarted" || echo "warning: cannot restart $svc" >&2
        done
    fi
    rm -rf "$STATE"
}

up() {
    [ "$(id -u)" = 0 ] || die "must run as root"
    grep -q configfs /proc/filesystems || modprobe configfs 2>/dev/null || true
    mountpoint -q /sys/kernel/config 2>/dev/null || mount -t configfs none /sys/kernel/config
    modprobe libcomposite 2>/dev/null || true
    [ -d /sys/kernel/config/usb_gadget ] || die "no usb_gadget in configfs (CONFIG_USB_CONFIGFS)"
    [ -d "$G" ] && { echo "gadget already exists ($G)"; return 0; }
    take_port

    mkdir -p "$G"
    echo $VID > "$G/idVendor"
    echo $PID > "$G/idProduct"
    echo 0x0200 > "$G/bcdUSB"
    mkdir -p "$G/strings/0x409"
    echo "CTT" > "$G/strings/0x409/manufacturer"
    echo "CTT test gadget" > "$G/strings/0x409/product"
    cat /etc/machine-id 2>/dev/null | cut -c1-16 > "$G/strings/0x409/serialnumber" || echo 0001 > "$G/strings/0x409/serialnumber"
    mkdir -p "$G/configs/c.1/strings/0x409"
    echo "CTT" > "$G/configs/c.1/strings/0x409/configuration"
    echo 250 > "$G/configs/c.1/MaxPower"

    mkdir -p "$G/functions/ffs.ctt" || die "FunctionFS not available (CONFIG_USB_CONFIGFS_F_FS)"
    ln -s "$G/functions/ffs.ctt" "$G/configs/c.1/"
    if [ "${1:-}" = "--ncm" ]; then
        mkdir -p "$G/functions/ncm.usb0" && ln -s "$G/functions/ncm.usb0" "$G/configs/c.1/" \
            || echo "warning: NCM not available" >&2
    fi
    mkdir -p "$FFS_DIR"
    mountpoint -q "$FFS_DIR" || mount -t functionfs ctt "$FFS_DIR"
    echo "gadget ready: FunctionFS at $FFS_DIR (not bound yet)"
    echo "next: ctt-agent server -t usb -e $FFS_DIR -o udc=auto"
}

bind() {
    udc=$(ls /sys/class/udc | head -n1)
    [ -n "$udc" ] || die "no UDC"
    echo "$udc" > "$G/UDC"
    echo "bound to $udc"
}

down() {
    [ -d "$G" ] || { echo "no gadget"; give_back_port; return 0; }
    [ -s "$G/UDC" ] && echo "" > "$G/UDC" 2>/dev/null || true
    for l in "$G"/configs/c.1/*.*; do [ -L "$l" ] && rm -f "$l"; done
    mountpoint -q "$FFS_DIR" 2>/dev/null && umount "$FFS_DIR" || true
    rmdir "$G"/configs/c.1/strings/0x409 "$G"/configs/c.1 2>/dev/null || true
    for f in "$G"/functions/*; do [ -d "$f" ] && rmdir "$f" 2>/dev/null || true; done
    rmdir "$G"/strings/0x409 "$G" 2>/dev/null || true
    echo "gadget removed"
    give_back_port
}

status() {
    echo "UDC controllers: $(ls /sys/class/udc 2>/dev/null | tr '\n' ' ')"
    rf=$(role_file); [ -n "$rf" ] && echo "role: $rf = $(cat "$rf" 2>/dev/null)"
    for svc in $STOP_SERVICES; do
        command -v systemctl >/dev/null 2>&1 && echo "$svc: $(systemctl is-active "$svc" 2>/dev/null)"
    done
    [ -d "$STATE" ] && echo "changed by ctt: $(cat "$STATE"/* 2>/dev/null | tr '\n' ' ')"
    if [ -d "$G" ]; then
        echo "gadget: $G  UDC=$(cat "$G/UDC" 2>/dev/null)"
        echo "functions: $(ls "$G/configs/c.1" 2>/dev/null | grep '\.' | tr '\n' ' ')"
        mountpoint -q "$FFS_DIR" 2>/dev/null && echo "FunctionFS: $FFS_DIR mounted ($(ls $FFS_DIR | tr '\n' ' '))"
    else
        echo "gadget: not created"
    fi
}

KEEP=0
ACTION=${1:-status}; [ $# -gt 0 ] && shift
NCM=""
while [ $# -gt 0 ]; do
    case "$1" in
        --keep-services|--keep-fw) KEEP=1 ;;
        --ncm) NCM=--ncm ;;
        --stop-service) shift; STOP_SERVICES="$STOP_SERVICES ${1:-}" ;;
        --role-file) shift; ROLE_FILE="${1:-}" ;;
    esac
    shift
done

case "$ACTION" in
    up) up "$NCM" ;;
    bind) bind ;;
    down) down ;;
    status) status ;;
    *) sed -n '2,22p' "$0"; exit 64 ;;
esac
