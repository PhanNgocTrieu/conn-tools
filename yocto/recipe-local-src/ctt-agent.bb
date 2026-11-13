# =============================================================
# Metadata
# =============================================================
SUMMARY = "CTT agent - connectivity test endpoint (TCP/UDP/RFCOMM/L2CAP/USB/I2C)"
DESCRIPTION = "Server/client of the Connectivity Test Tool: throughput, latency, loss and \
integrity over WiFi, Bluetooth and USB. Uses only kernel interfaces (no libbluetooth / libusb)."
LICENSE = "CLOSED"

# =============================================================
# Source
# =============================================================
# Source kept in this layer next to the recipe (files/ctt-agent/): no git fetch, no network,
# no SRCREV. The source commit is in files/ctt-agent/SOURCE-COMMIT.
# Refresh it from the connectivity-tools tree with:  scripts/export-recipe.sh <this folder>
FILESEXTRAPATHS:prepend := "${THISDIR}/files:"
SRC_URI = " \
    file://ctt-agent \
    file://ctt-agent-bt.service \
    file://ctt-agent-ble.service \
"
PV = "0.3.0"   # = version in CMakeLists.txt; export-recipe.sh keeps it equal
S = "${WORKDIR}/ctt-agent"

# =============================================================
# Build
# =============================================================
inherit cmake systemd features_check
REQUIRED_DISTRO_FEATURES = "systemd"

# Unit tests need the host toolchain; the image only needs the binary.
EXTRA_OECMAKE = "-DCTT_BUILD_TESTS=OFF"

# =============================================================
# Service
# =============================================================
# RFCOMM server on channel 22 for ctt-bt / ctt-agent-console when the PC has no SSH to the board.
# Installed but disabled: enable it only in test images, e.g. in local.conf
#   SYSTEMD_AUTO_ENABLE:pn-ctt-agent = "enable"
SYSTEMD_SERVICE:${PN} = "ctt-agent-bt.service ctt-agent-ble.service"
SYSTEMD_AUTO_ENABLE:${PN} ??= "disable"

# =============================================================
# Install
# =============================================================
# cmake installs /usr/bin/ctt-agent and /usr/bin/ctt-usb-gadget.
do_install:append() {
    install -d ${D}${systemd_system_unitdir}
    install -m 0644 ${WORKDIR}/ctt-agent-bt.service ${D}${systemd_system_unitdir}/
    install -m 0644 ${WORKDIR}/ctt-agent-ble.service ${D}${systemd_system_unitdir}/

    if [ ! -x ${D}${bindir}/ctt-agent ]; then
        bbfatal "ctt-agent was not installed: ${D}${bindir}/ctt-agent"
    fi
}

# =============================================================
# Packaging
# =============================================================
FILES:${PN} += " \
    ${bindir}/ctt-agent \
    ${bindir}/ctt-usb-gadget \
    ${systemd_system_unitdir}/ctt-agent-bt.service \
    ${systemd_system_unitdir}/ctt-agent-ble.service \
"
# RFCOMM / i2c-dev may be built as modules.
RRECOMMENDS:${PN} = "kernel-module-rfcomm kernel-module-i2c-dev"
