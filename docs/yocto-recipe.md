# Đưa `ctt-agent` vào image Yocto của dự án (meta-project)

Mục tiêu: build `ctt-agent` cùng image của board, để agent có sẵn ở `/usr/bin/ctt-agent`. Có agent sẵn trong image thì làm được Client Test (board khách chỉ có UART, xem [bt-client-test.md](bt-client-test.md)) mà không phải chép file bằng USB hay UART.

Tài liệu viết theo cấu trúc BSP hiện tại:

| | Giá trị trong dự án |
|---|---|
| Yocto | **scarthgap** (poky có `bluez5_5.72`, `systemd_255`, `gcc_13.4`) |
| Layer của dự án | `layers/meta-project` (các recipe vendor nằm ở `recipes-project/`, `recipes-3rdparty/`, `recipes-drv/`…) |
| Image connectivity | `meta-project/recipes-core/images/project-image.bb` |
| Mẫu cấu hình build | `meta-project/conf/local_conf/board-sub/conf/{local.conf, bblayers.conf, srcrev.inc}` (và `board-main`) |
| Kernel | `meta-project/recipes-kernel/linux/linux-vendor_%.bbappend` kèm các fragment `.cfg` (`PROJECT_Bluetooth.cfg`…) |

> Nếu image chạy connectivity không phải `project-image` / `board-sub`, thay tên tương ứng ở các bước dưới.

---

## 1. Chọn cách đặt recipe

| Cách | Làm gì | Khi nào nên dùng |
|---|---|---|
| **A. Recipe trong `meta-project`** (khuyến nghị) | Thêm thư mục `meta-project/recipes-test/ctt-agent/` | Dự án giữ mọi recipe trong một layer, không phải sửa `bblayers.conf` |
| B. Thêm layer `meta-ctt` | `bitbake-layers add-layer …/connectivity-tools/yocto/meta-ctt` | Muốn giữ tool tách khỏi layer sản phẩm, cập nhật bằng cách pull repo CTT |

Phần dưới làm theo **cách A**. Cách B dùng đúng các file trong [yocto/meta-ctt](../yocto/meta-ctt), chỉ khác ở bước thêm layer ([yocto/meta-ctt/README.md](../yocto/meta-ctt/README.md)).

`conf/layer.conf` của `meta-project` phải match được `recipes-*/*/*.bb` (thường là có sẵn). Kiểm tra bằng:

```bash
grep BBFILES layers/meta-project/conf/layer.conf
```

## 2. Tạo recipe

Cấu trúc:

```
meta-project/recipes-test/ctt-agent/
├── ctt-agent_git.bb
└── files/
    └── ctt-agent-bt.service      # chép từ connectivity-tools/yocto/meta-ctt/recipes-connectivity/ctt-agent/files/
```

`ctt-agent_git.bb` (viết theo kiểu các recipe vendor như `usb-daemon.bb`):

```bitbake
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
# Repository of the tool. The commit is pinned in srcrev.inc (SRCREV_pn-ctt-agent).
CTT_GIT_URI ??= "git://github.com/PhanNgocTrieu/conn-tools.git;protocol=https;branch=main"
SRC_URI = " \
    ${CTT_GIT_URI} \
    file://ctt-agent-bt.service \
"
SRCREV ??= "${AUTOREV}"
PV = "0.1+git"
S = "${WORKDIR}/git"

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
# Installed but disabled: enable it only in test images (see step 4).
SYSTEMD_SERVICE:${PN} = "ctt-agent-bt.service"
SYSTEMD_AUTO_ENABLE:${PN} ??= "disable"

# =============================================================
# Install
# =============================================================
# cmake installs /usr/bin/ctt-agent and /usr/bin/ctt-usb-gadget.
do_install:append() {
    install -d ${D}${systemd_system_unitdir}
    install -m 0644 ${WORKDIR}/ctt-agent-bt.service ${D}${systemd_system_unitdir}/
}

# =============================================================
# Packaging
# =============================================================
FILES:${PN} += " \
    ${bindir}/ctt-agent \
    ${bindir}/ctt-usb-gadget \
    ${systemd_system_unitdir}/ctt-agent-bt.service \
"
# RFCOMM / i2c-dev may be built as modules.
RRECOMMENDS:${PN} = "kernel-module-rfcomm kernel-module-i2c-dev"
```

Ghi chú:
- `PV = "0.1+git"`: từ nanbield/scarthgap, fetcher tự thêm mã commit vào phiên bản, nên không cần `${SRCPV}` nữa.
- Nếu máy build **không ra được Internet / GitHub**: đổi `CTT_GIT_URI` sang server git nội bộ (mirror của repo), hoặc dùng `externalsrc` (bước 6).
- Recipe không đụng tới daemon vendor nào. `ctt-agent` chỉ mở socket RFCOMM của kernel trên channel 22; bt-daemon vẫn giữ adapter và pairing như bình thường.

## 3. Ghim phiên bản (SRCREV)

Image release phải build lại được y hệt, nên không dùng `AUTOREV`. Thêm vào `meta-project/conf/local_conf/board-sub/conf/srcrev.inc` (và `board-main` nếu cần):

```bitbake
SRCREV_pn-ctt-agent = "<commit của connectivity-tools>"
```

Lấy commit bằng `git -C connectivity-tools rev-parse HEAD` (commit đã push lên repo).

## 4. Thêm vào image

Không đưa tool test vào image production. Chỉ bật khi build image test, chọn một trong hai cách:

**a) Chỉ máy của bạn**: sửa `build/conf/local.conf` (file sinh ra khi build):

```bitbake
IMAGE_INSTALL:append = " ctt-agent"
# Tự chạy server RFCOMM channel 22 khi boot (cho Client Test, không cần gõ lệnh trên UART):
SYSTEMD_AUTO_ENABLE:pn-ctt-agent = "enable"
```

**b) Cả team dùng chung một "test image"**: sửa `project-image.bb`, cho phép bật bằng một biến:

```bitbake
# ctt-agent only when CTT_TEST_IMAGE = "1" (local.conf)
IMAGE_INSTALL:append = "${@' ctt-agent' if d.getVar('CTT_TEST_IMAGE') == '1' else ''}"
```

Sau đó trong `local.conf` của người cần build: `CTT_TEST_IMAGE = "1"`.

Muốn có thêm `bluetoothctl` / `btmon` (để làm baseline `l2ping`, `btmon` trong [bt-test-plan.md](bt-test-plan.md)): kiểm tra `bluez5_%.bbappend` của `meta-project` (ở `recipes-3rdparty/bluez5/`) có cài các tool đó không. Không có thì thêm `bluez5-noinst-tools` hoặc `bluez5-client` vào `IMAGE_INSTALL` của image test.

## 5. Kiểm tra kernel

Agent cần các option sau. Chúng nằm trong các fragment `.cfg` của `linux-vendor_%.bbappend`, kiểm tra ở `PROJECT_Bluetooth.cfg`:

| Option | Dùng cho |
|---|---|
| `CONFIG_BT_RFCOMM=y` (hoặc `=m`) | Test Bluetooth RFCOMM: **bắt buộc** |
| `CONFIG_BT_BREDR=y`, `CONFIG_BT` | L2CAP BR/EDR |
| `CONFIG_I2C_CHARDEV` | MFi (I2C) |
| `CONFIG_USB_CONFIGFS`, `CONFIG_USB_CONFIGFS_F_FS`, `_NCM` | Test USB gadget (đã có cho usb-daemon) |

Kiểm tra trên board đang chạy:

```sh
zcat /proc/config.gz | grep -E 'BT_RFCOMM|BT_BREDR|I2C_CHARDEV'
```

Hoặc trong build:

```bash
bitbake -e virtual/kernel | grep ^B=
```

rồi xem file `.config` trong thư mục `B` đó.

Thiếu option nào thì thêm dòng tương ứng vào `PROJECT_Bluetooth.cfg`, hoặc tạo fragment mới (ví dụ `ctt.cfg`) rồi khai báo trong `linux-vendor_%.bbappend`:

```bitbake
SRC_URI:append = " file://ctt.cfg"
```

## 6. Build và kiểm tra

Dùng script build của BSP như bình thường (`build.sh`, `script/build_image.sh`). Hoặc trong môi trường build đã `source`:

```bash
bitbake ctt-agent                               # chỉ build agent, nhanh, để sửa lỗi recipe
oe-pkgdata-util list-pkg-files ctt-agent        # phải thấy /usr/bin/ctt-agent, ctt-usb-gadget, ctt-agent-bt.service
bitbake project-image          # build image test
```

Không cần flash lại cả image để thử: chép thẳng binary vừa build lên board.

```bash
ls tmp/work/*/ctt-agent/*/image/usr/bin/ctt-agent
scp tmp/work/*/ctt-agent/*/image/usr/bin/ctt-agent root@<board-ip>:/tmp/   # có mạng
```

Không có mạng thì chép bằng USB stick, xem [bt-client-test.md](bt-client-test.md) bước 1.

**Sửa code CTT và build lại ngay, không qua git** (`externalsrc`). Thêm vào `build/conf/local.conf`:

```bitbake
INHERIT += "externalsrc"
EXTERNALSRC:pn-ctt-agent = "/path/to/connectivity-tools"
EXTERNALSRC_BUILD:pn-ctt-agent = "${WORKDIR}/build"
```

Hoặc dùng `devtool modify ctt-agent` (BSP có `script/devtool.sh`).

## 7. Kiểm tra trên board (qua UART)

```sh
ctt-agent version                         # ctt-agent 0.1.0 (CTP v1, aarch64)
systemctl status ctt-agent-bt             # active (running) nếu đã bật ở bước 4
journalctl -u ctt-agent-bt -n 20          # log của server RFCOMM channel 22
```

Từ PC (Client PC):

```
ctt-agent-console.exe run --addr <địa chỉ BT của board> --channel 22 -m tx -d 10
ctt-agent-console.exe suite --addr <địa chỉ BT của board> --channel 22 --level target
```

Đã bật service thì có thể bỏ qua bước 1 và bước 6 của [bt-client-test.md](bt-client-test.md) (đưa agent lên và chạy `ctt-agent server` bằng tay).

## 8. Lỗi thường gặp

| Lỗi | Nguyên nhân / cách xử lý |
|---|---|
| `Fetcher failure … github.com` | Máy build không ra được GitHub. Đổi `CTT_GIT_URI` sang mirror nội bộ, hoặc dùng `externalsrc` |
| `AUTOREV` lỗi khi `BB_NO_NETWORK = "1"` | Phải ghim `SRCREV_pn-ctt-agent` trong `srcrev.inc` |
| `Nothing PROVIDES 'ctt-agent'` | Recipe không nằm trong `BBFILES` của `meta-project`. Kiểm tra `layer.conf` và đường dẫn `recipes-*/*/*.bb` |
| `QA Issue: … installed-vs-shipped` | Có file được cài nhưng không nằm trong `FILES:${PN}`. Thêm đường dẫn vào `FILES:${PN}` |
| CMake báo phiên bản quá thấp | Cần CMake ≥ 3.16, scarthgap đã có sẵn |
| Trên board: `RFCOMM socket: … (kernel built without CONFIG_BT_RFCOMM?)` | Kernel thiếu `CONFIG_BT_RFCOMM` (bước 5), hoặc module `rfcomm` chưa nạp (`modprobe rfcomm`) |
| Trên board: `rfcomm listen …/22: Address already in use (channel used by another profile/app)` | Đang có một `ctt-agent server` khác chạy (service đã bật). Dừng bằng `systemctl stop ctt-agent-bt`, hoặc dùng luôn service đó |
