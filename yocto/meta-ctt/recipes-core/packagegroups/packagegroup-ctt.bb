SUMMARY = "Connectivity Test Tool - agent and the tools ctt uses on the DUT"
LICENSE = "MIT"
inherit packagegroup

# bluez5: bluetoothctl / btmon for `ctt bt` (--side dut, --dut-accept)
# iw: WiFi link info for `--wifi`;  i2c-tools: bus inspection for MFi bring-up
RDEPENDS:${PN} = " \
    ctt-agent \
    bluez5 \
    iw \
    i2c-tools \
"
# Optional: iperf3 (meta-oe) for cross-checking numbers
RRECOMMENDS:${PN} = "iperf3"
