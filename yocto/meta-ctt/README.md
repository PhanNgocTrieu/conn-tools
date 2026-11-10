# meta-ctt

Yocto layer that puts `ctt-agent` (and the tools `ctt` uses) into the board image.

```bash
# in the BSP build directory
bitbake-layers add-layer /path/to/connectivity-tools/yocto/meta-ctt
```

`conf/local.conf`:

```
CTT_GIT_URI = "git://<your-git-server>/connectivity-tools.git;protocol=ssh;branch=main"
SRCREV_pn-ctt-agent = "<commit>"          # pin for release images
IMAGE_INSTALL:append = " packagegroup-ctt"
```

Using a local working copy while developing:

```
INHERIT += "externalsrc"
EXTERNALSRC_pn-ctt-agent = "/path/to/connectivity-tools"
```

Build only the agent: `bitbake ctt-agent`. The agent is then at `/usr/bin/ctt-agent` on the DUT;
run `ctt` with `--dut-agent /usr/bin/ctt-agent` (instead of `ctt deploy`).

Notes:
- Recipe syntax uses the `:` override separator (Yocto ≥ 3.4 honister). For older BSPs
  (dunfell), replace `FILES:${PN}` → `FILES_${PN}` and `RDEPENDS:${PN}` → `RDEPENDS_${PN}`.
- `iperf3` comes from meta-oe. It is only recommended, so the image still builds without meta-oe.
- Kernel options the tests rely on: `CONFIG_BT_RFCOMM`, `CONFIG_BT` (L2CAP), `CONFIG_I2C_CHARDEV`,
  and for USB gadget tests `CONFIG_USB_CONFIGFS` + `CONFIG_USB_CONFIGFS_F_FS` (+ `_NCM`).
