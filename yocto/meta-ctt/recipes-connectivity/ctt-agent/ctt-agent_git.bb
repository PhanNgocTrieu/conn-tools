SUMMARY = "CTT agent - connectivity test endpoint (TCP/UDP/RFCOMM/L2CAP/USB/I2C)"
DESCRIPTION = "Server/client for the Connectivity Test Tool: throughput, latency, \
loss and integrity over WiFi, Bluetooth and USB, plus I2C (MFi auth coprocessor) \
and storage tests. Uses only kernel interfaces (no libbluetooth / libusb)."
HOMEPAGE = "https://example.invalid/connectivity-tools"
LICENSE = "CLOSED"

# Point CTT_GIT_URI at your repository (local.conf or a bbappend), e.g.
#   CTT_GIT_URI = "git://git.mycompany.com/connectivity-tools.git;protocol=ssh;branch=main"
#   SRCREV_pn-ctt-agent = "<commit>"   (pin it for release images)
# During development you can build from a working copy instead:
#   INHERIT += "externalsrc"
#   EXTERNALSRC_pn-ctt-agent = "/path/to/connectivity-tools"
CTT_GIT_URI ??= "git://example.invalid/connectivity-tools.git;protocol=https;branch=main"
SRC_URI = "${CTT_GIT_URI}"
SRCREV ??= "${AUTOREV}"
PV = "0.1+git${SRCPV}"
S = "${WORKDIR}/git"

inherit cmake

EXTRA_OECMAKE = "-DCTT_BUILD_TESTS=OFF"

# ctt-agent:         /usr/bin/ctt-agent
# ctt-usb-gadget:    /usr/bin/ctt-usb-gadget  (configfs/FunctionFS gadget helper)
FILES:${PN} += "${bindir}/ctt-agent ${bindir}/ctt-usb-gadget"
RRECOMMENDS:${PN} = "kernel-module-rfcomm kernel-module-i2c-dev"
