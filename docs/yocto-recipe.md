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
| **A. Recipe trong `meta-project`** (khuyến nghị) | Thêm thư mục `meta-project/recipes-project/ctt-agent/`, source chép kèm | Máy build công ty không truy cập được GitHub; mọi recipe nằm trong một layer, không phải sửa `bblayers.conf` |
| B. Thêm layer `meta-ctt` | `bitbake-layers add-layer …/connectivity-tools/yocto/meta-ctt` | Chỉ dùng được khi máy build clone được repo CTT (recipe này tải bằng `git://`) |

Phần dưới làm theo **cách A**. Cách B dùng đúng các file trong [yocto/meta-ctt](../yocto/meta-ctt), chỉ khác ở bước thêm layer ([yocto/meta-ctt/README.md](../yocto/meta-ctt/README.md)).

`conf/layer.conf` của `meta-project` phải match được `recipes-*/*/*.bb` (thường là có sẵn). Kiểm tra bằng:

```bash
grep BBFILES layers/meta-project/conf/layer.conf
```

## 2. Tạo recipe (source nằm cạnh recipe, không tải từ git)

Máy build ở công ty **không clone được repo GitHub cá nhân**, nên recipe không dùng `git://`. Source của `ctt-agent` được chép thẳng vào layer, giống cách `bt-daemon` và `usb-daemon` đang làm (`SRC_URI = "file://…"`). Bitbake không cần mạng và không cần `SRCREV`.

Cấu trúc:

```
meta-project/recipes-project/ctt-agent/
├── ctt-agent.bb
└── files/
    ├── ctt-agent-bt.service          # server RFCOMM channel 22 (systemd)
    └── ctt-agent/                    # source C++ (~340 KB, 51 file)
        ├── CMakeLists.txt
        ├── SOURCE-COMMIT             # commit connectivity-tools đã dùng để chép
        ├── agent/{CMakeLists.txt, include/, src/}
        └── scripts/dut-usb-gadget.sh # cài thành /usr/bin/ctt-usb-gadget
```

**Tạo thư mục này** ở máy có repo connectivity-tools (máy cá nhân), rồi đem thư mục `ctt-agent/` lên công ty:

```bash
cd connectivity-tools
scripts/export-recipe.sh                       # mặc định ghi vào recipes-project/ctt-agent/
# hoặc ghi thẳng vào layer:  scripts/export-recipe.sh /path/to/layers/meta-project/recipes-project/ctt-agent
```

Script chỉ chép file, không build:
- chép source vào `files/ctt-agent/` (xóa bản cũ trước) và chép file service;
- ghi commit vào `SOURCE-COMMIT` (thêm `-dirty` nếu lúc chép có sửa đổi chưa commit);
- lần đầu (thư mục chưa có `.bb`): chép recipe mẫu [yocto/recipe-local-src/ctt-agent.bb](../yocto/recipe-local-src/ctt-agent.bb). Đã có `.bb` thì không ghi đè, chỉ cập nhật dòng `PV` theo version trong `CMakeLists.txt`.

`ctt-agent.bb` (bản mẫu trong repo: `yocto/recipe-local-src/ctt-agent.bb`):

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
# Source kept in this layer next to the recipe (files/ctt-agent/), like bt-daemon:
# no git fetch, no network, no SRCREV. The source commit is in files/ctt-agent/SOURCE-COMMIT.
# Refresh it from the connectivity-tools tree with:  scripts/export-recipe.sh <this folder>
FILESEXTRAPATHS:prepend := "${THISDIR}/files:"
SRC_URI = " \
    file://ctt-agent \
    file://ctt-agent-bt.service \
"
PV = "0.1.0"   # = version in CMakeLists.txt; export-recipe.sh keeps it equal
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
SYSTEMD_SERVICE:${PN} = "ctt-agent-bt.service"
SYSTEMD_AUTO_ENABLE:${PN} ??= "disable"

# =============================================================
# Install
# =============================================================
# cmake installs /usr/bin/ctt-agent and /usr/bin/ctt-usb-gadget.
do_install:append() {
    install -d ${D}${systemd_system_unitdir}
    install -m 0644 ${WORKDIR}/ctt-agent-bt.service ${D}${systemd_system_unitdir}/

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
"
# RFCOMM / i2c-dev may be built as modules.
RRECOMMENDS:${PN} = "kernel-module-rfcomm kernel-module-i2c-dev"
```

Ghi chú:
- **Cập nhật agent:** chạy lại `scripts/export-recipe.sh`, rồi commit thư mục `files/ctt-agent/` vào git của `meta-project`. Nếu muốn package có version mới thì tăng `PR` hoặc `PV`.
- Recipe không đụng tới daemon vendor nào. `ctt-agent` chỉ mở socket RFCOMM của kernel trên channel 22, còn bt-daemon vẫn giữ adapter và pairing như bình thường. Service chạy sau `bt-daemon.service`.
- Recipe chỉ cần những gì đã có sẵn trong toolchain: CMake ≥ 3.16, C++17 và pthread. Không cần thư viện ngoài nào (bluez, libusb…).

## 3. Biết image đang chứa bản agent nào

Không có `SRCREV` để ghim. Phiên bản chính là nội dung `files/ctt-agent/` đã commit trong `meta-project`. Muốn biết nó lấy từ commit nào của connectivity-tools:

```bash
cat layers/meta-project/recipes-project/ctt-agent/files/ctt-agent/SOURCE-COMMIT
```

Trên board, `ctt-agent version` in ra số version (`0.1.0`).

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

**Đang sửa code CTT và muốn build lại ngay mà không chép lại source** (`externalsrc`, dùng ở máy có repo). Thêm vào `build/conf/local.conf`:

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
journalctl -f -u ctt-agent-bt            # trace mỗi giây trong lúc test: đã nhận / đã gửi bao nhiêu
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
| `Unable to find file file://ctt-agent` | Thiếu thư mục `files/ctt-agent/`. Chạy `scripts/export-recipe.sh`, hoặc kiểm tra thư mục đã được commit vào `meta-project` chưa |
| `Fetcher failure … github.com` | Đang dùng recipe `git://` (cách B). Máy công ty phải dùng cách A |
| `Nothing PROVIDES 'ctt-agent'` | Recipe không nằm trong `BBFILES` của `meta-project`. Kiểm tra `layer.conf` và đường dẫn `recipes-*/*/*.bb` |
| `QA Issue: … installed-vs-shipped` | Có file được cài nhưng không nằm trong `FILES:${PN}`. Thêm đường dẫn vào `FILES:${PN}` |
| CMake báo phiên bản quá thấp | Cần CMake ≥ 3.16, scarthgap đã có sẵn |
| Trên board: `RFCOMM socket: … (kernel built without CONFIG_BT_RFCOMM?)` | Kernel thiếu `CONFIG_BT_RFCOMM` (bước 5), hoặc module `rfcomm` chưa nạp (`modprobe rfcomm`) |
| Trên board: `rfcomm listen …/22: Address already in use (channel used by another profile/app)` | Đang có một `ctt-agent server` khác chạy (service đã bật). Dừng bằng `systemctl stop ctt-agent-bt`, hoặc dùng luôn service đó |
