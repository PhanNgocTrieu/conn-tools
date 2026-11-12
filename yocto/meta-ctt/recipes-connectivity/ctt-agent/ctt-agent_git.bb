SUMMARY = "CTT agent - connectivity test endpoint (TCP/UDP/RFCOMM/L2CAP/USB/I2C)"
DESCRIPTION = "Server/client for the Connectivity Test Tool: throughput, latency, \
loss and integrity over WiFi, Bluetooth and USB, plus I2C (MFi auth coprocessor) \
and storage tests. Uses only kernel interfaces (no libbluetooth / libusb)."
HOMEPAGE = "https://github.com/PhanNgocTrieu/conn-tools"
LICENSE = "CLOSED"

# Point CTT_GIT_URI at your repository (local.conf or a bbappend), e.g.
#   CTT_GIT_URI = "git://git.mycompany.com/connectivity-tools.git;protocol=ssh;branch=main"
#   SRCREV_pn-ctt-agent = "<commit>"   (pin it for release images)
# During development you can build from a working copy instead:
#   INHERIT += "externalsrc"
#   EXTERNALSRC_pn-ctt-agent = "/path/to/connectivity-tools"
CTT_GIT_URI ??= "git://github.com/PhanNgocTrieu/conn-tools.git;protocol=https;branch=main"
SRC_URI = "${CTT_GIT_URI} file://ctt-agent-bt.service"
SRCREV ??= "${AUTOREV}"
PV = "0.1+git${SRCPV}"
S = "${WORKDIR}/git"

inherit cmake systemd

# ctt-agent-bt.service: RFCOMM server on channel 22 for ctt-agent.exe / ctt-agent-console.exe when the
# PC has no SSH access to the board. Installed but disabled by default; enable it
# in a test image with  SYSTEMD_AUTO_ENABLE:${PN} = "enable"  (bbappend/local.conf).
SYSTEMD_SERVICE:${PN} = "ctt-agent-bt.service"
SYSTEMD_AUTO_ENABLE:${PN} ??= "disable"

do_install:append() {
    install -d ${D}${systemd_system_unitdir}
    install -m 0644 ${WORKDIR}/ctt-agent-bt.service ${D}${systemd_system_unitdir}/
}

EXTRA_OECMAKE = "-DCTT_BUILD_TESTS=OFF"

# ctt-agent:         /usr/bin/ctt-agent
# ctt-usb-gadget:    /usr/bin/ctt-usb-gadget  (configfs/FunctionFS gadget helper)
FILES:${PN} += "${bindir}/ctt-agent ${bindir}/ctt-usb-gadget ${systemd_system_unitdir}/ctt-agent-bt.service"
RRECOMMENDS:${PN} = "kernel-module-rfcomm kernel-module-i2c-dev"
