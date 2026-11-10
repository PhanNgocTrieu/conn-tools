#!/bin/sh
# USB gadget for CTT on the DUT (run as root on the board, or via
# `ctt usb gadget up|down|status --dut root@<ip>`).
#
#   dut-usb-gadget.sh up [--ncm]   create configfs gadget + FunctionFS (bulk) [+ NCM network]
#   dut-usb-gadget.sh bind         bind to the first UDC (ctt-agent can do this itself: -o udc=auto)
#   dut-usb-gadget.sh down         unbind and remove everything
#   dut-usb-gadget.sh status
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

die() { echo "error: $*" >&2; exit 1; }

up() {
    [ "$(id -u)" = 0 ] || die "must run as root"
    grep -q configfs /proc/filesystems || modprobe configfs 2>/dev/null || true
    mountpoint -q /sys/kernel/config 2>/dev/null || mount -t configfs none /sys/kernel/config
    modprobe libcomposite 2>/dev/null || true
    [ -d /sys/kernel/config/usb_gadget ] || die "no usb_gadget in configfs (CONFIG_USB_CONFIGFS)"
    ls /sys/class/udc >/dev/null 2>&1 && [ -n "$(ls /sys/class/udc)" ] || \
        die "no USB device controller in /sys/class/udc: port is host-only or OTG not in device mode"
    [ -d "$G" ] && { echo "gadget already exists ($G)"; return 0; }

    mkdir -p "$G"
    echo $VID > "$G/idVendor"
    echo $PID > "$G/idProduct"
    echo 0x0200 > "$G/bcdUSB"
    mkdir -p "$G/strings/0x409"
    echo "CTT" > "$G/strings/0x409/manufacturer"
    echo "CTT board test gadget" > "$G/strings/0x409/product"
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
    [ -d "$G" ] || { echo "no gadget"; return 0; }
    [ -s "$G/UDC" ] && echo "" > "$G/UDC" 2>/dev/null || true
    for l in "$G"/configs/c.1/*.*; do [ -L "$l" ] && rm -f "$l"; done
    mountpoint -q "$FFS_DIR" 2>/dev/null && umount "$FFS_DIR" || true
    rmdir "$G"/configs/c.1/strings/0x409 "$G"/configs/c.1 2>/dev/null || true
    for f in "$G"/functions/*; do [ -d "$f" ] && rmdir "$f" 2>/dev/null || true; done
    rmdir "$G"/strings/0x409 "$G" 2>/dev/null || true
    echo "gadget removed"
}

status() {
    echo "UDC controllers: $(ls /sys/class/udc 2>/dev/null | tr '\n' ' ')"
    for r in /sys/class/usb_role/*/role; do [ -f "$r" ] && echo "$r: $(cat "$r")"; done
    if [ -d "$G" ]; then
        echo "gadget: $G  UDC=$(cat "$G/UDC" 2>/dev/null)"
        echo "functions: $(ls "$G/configs/c.1" 2>/dev/null | grep '\.' | tr '\n' ' ')"
        mountpoint -q "$FFS_DIR" 2>/dev/null && echo "FunctionFS: $FFS_DIR mounted ($(ls $FFS_DIR | tr '\n' ' '))"
    else
        echo "gadget: not created"
    fi
}

case "${1:-status}" in
    up) shift; up "${1:-}" ;;
    bind) bind ;;
    down) down ;;
    status) status ;;
    *) sed -n '2,13p' "$0"; exit 64 ;;
esac
