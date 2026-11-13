# Đưa `ctt-agent` vào image Yocto của board

Mục tiêu: build `ctt-agent` cùng image của board, để agent có sẵn ở `/usr/bin/ctt-agent`, kèm các service systemd (tắt sẵn). Khi agent đã nằm trong image, test qua UART không cần chép file bằng USB stick (xem [bt-client-test.md](bt-client-test.md)).

Áp dụng cho mọi BSP Yocto có systemd. Trong tài liệu này:

| Ký hiệu | Nghĩa | Ví dụ |
|---|---|---|
| `<layer>` | layer của dự án, nơi đặt recipe | `meta-myproduct` |
| `<image>` | image cần thêm agent | `core-image-base`, `my-product-image` |
| `<build>` | thư mục build (đã `source oe-init-build-env`) | `build/` |

Agent chỉ dùng các interface của kernel (socket Bluetooth, configfs, i2c-dev) và D-Bus của BlueZ cho BLE GATT. Không cần thư viện ngoài nào: chỉ cần CMake ≥ 3.16, một compiler C++17 và pthread.

---

## 1. Chọn cách đặt recipe

| Cách | Làm gì | Khi nào nên dùng |
|---|---|---|
| **A. Source đặt cạnh recipe** (khuyến nghị) | `scripts/export-recipe.sh <layer>/recipes-connectivity/ctt-agent` | Máy build không truy cập được repo này (mạng nội bộ, không có tài khoản). Recipe dùng `file://`, không cần mạng |
| B. Thêm layer `meta-ctt` | `bitbake-layers add-layer …/connectivity-tools/yocto/meta-ctt` | Máy build clone được repo. Recipe dùng `git://` và ghim `SRCREV` |

`conf/layer.conf` của `<layer>` phải match được `recipes-*/*/*.bb` (thường đã có sẵn):

```bash
grep BBFILES <layer>/conf/layer.conf
```

## 2. Cách A: xuất recipe kèm source

Chạy ở máy có repo connectivity-tools, rồi mang thư mục kết quả vào `<layer>`:

```bash
cd connectivity-tools
scripts/export-recipe.sh <layer>/recipes-connectivity/ctt-agent    # hoặc không đối số: out/ctt-agent-recipe
```

Kết quả:

```
<layer>/recipes-connectivity/ctt-agent/
├── ctt-agent.bb                      # lần đầu chép từ yocto/recipe-local-src/ctt-agent.bb
└── files/
    ├── ctt-agent-bt.service          # server RFCOMM channel 22 (tắt sẵn)
    ├── ctt-agent-ble.service         # server BLE GATT (tắt sẵn)
    └── ctt-agent/                    # source C++ (~350 KB)
        ├── CMakeLists.txt
        ├── SOURCE-COMMIT             # commit connectivity-tools đã dùng
        ├── agent/{CMakeLists.txt, include/, src/}
        └── scripts/dut-usb-gadget.sh # cài thành /usr/bin/ctt-usb-gadget
```

Script chỉ chép file, không build:
- chép source vào `files/ctt-agent/` (xóa bản cũ trước) và chép các file service;
- ghi commit vào `SOURCE-COMMIT` (thêm `-dirty` nếu lúc chép có sửa đổi chưa commit);
- thư mục chưa có `.bb` thì chép recipe mẫu [yocto/recipe-local-src/ctt-agent.bb](../yocto/recipe-local-src/ctt-agent.bb). Đã có `.bb` thì không ghi đè, chỉ cập nhật dòng `PV` theo version trong `CMakeLists.txt`.

Recipe mẫu:

```bitbake
SUMMARY = "CTT agent - connectivity test endpoint (TCP/UDP/RFCOMM/L2CAP/BLE GATT/USB/I2C)"
LICENSE = "CLOSED"

FILESEXTRAPATHS:prepend := "${THISDIR}/files:"
SRC_URI = " \
    file://ctt-agent \
    file://ctt-agent-bt.service \
    file://ctt-agent-ble.service \
"
PV = "0.3.0"   # = version in CMakeLists.txt; export-recipe.sh keeps it equal
S = "${WORKDIR}/ctt-agent"

inherit cmake systemd features_check
REQUIRED_DISTRO_FEATURES = "systemd"
EXTRA_OECMAKE = "-DCTT_BUILD_TESTS=OFF"

SYSTEMD_SERVICE:${PN} = "ctt-agent-bt.service ctt-agent-ble.service"
SYSTEMD_AUTO_ENABLE:${PN} ??= "disable"
# … do_install:append (cài 2 service), FILES:${PN}, RRECOMMENDS: kernel-module-rfcomm, i2c-dev
```

**Cập nhật agent:** chạy lại `scripts/export-recipe.sh <cùng thư mục>`, rồi commit thư mục đó vào git của `<layer>`.

**Biết image chứa bản nào:** xem `files/ctt-agent/SOURCE-COMMIT` trong layer. Trên board, `ctt-agent version` in số version.

## 3. Cách B: layer `meta-ctt` (git)

```bash
bitbake-layers add-layer /path/to/connectivity-tools/yocto/meta-ctt
```

Ghim commit để build lặp lại được (trong `local.conf` hoặc file `srcrev` của dự án):

```bitbake
SRCREV_pn-ctt-agent = "<commit đã push của connectivity-tools>"
# repo nội bộ / mirror:
CTT_GIT_URI = "git://git.example.com/connectivity-tools.git;protocol=ssh;branch=main"
```

Chi tiết: [yocto/meta-ctt/README.md](../yocto/meta-ctt/README.md).

## 4. Thêm vào image

Không đưa tool test vào image production. Chỉ bật khi build image test:

**Chỉ máy của bạn**, trong `<build>/conf/local.conf`:

```bitbake
IMAGE_INSTALL:append = " ctt-agent"
# Tự chạy server khi boot (test qua UART không cần gõ lệnh): bật cả ctt-agent-bt (RFCOMM 22)
# và ctt-agent-ble (BLE GATT)
SYSTEMD_AUTO_ENABLE:pn-ctt-agent = "enable"
```

**Cả team dùng chung một image test**: trong `<image>.bb` (hoặc một bbappend), bật bằng biến:

```bitbake
IMAGE_INSTALL:append = "${@' ctt-agent' if d.getVar('CTT_TEST_IMAGE') == '1' else ''}"
```

Rồi trong `local.conf`: `CTT_TEST_IMAGE = "1"`.

Nên có thêm trong image test: `bluez5` (bluetoothd, `bluetoothctl`; GATT server của agent cần bluetoothd), `bluez5-noinst-tools` (`btmon`, `l2ping`), `iw` (trạng thái WiFi trong `ctt board status`).

Nếu một daemon của sản phẩm bật adapter Bluetooth, hãy cho service của agent chạy sau nó bằng một drop-in, ví dụ:
`files/ctt-agent-bt.service.d/after.conf` với nội dung `[Unit]` / `After=<daemon>.service`.

## 5. Kiểm tra kernel

| Option | Dùng cho |
|---|---|
| `CONFIG_BT_RFCOMM` (y hoặc m) | Bluetooth Classic RFCOMM: **bắt buộc** cho test Classic |
| `CONFIG_BT_BREDR`, `CONFIG_BT` | L2CAP BR/EDR |
| `CONFIG_BT_LE` | BLE (GATT qua bluetoothd, L2CAP LE CoC) |
| `CONFIG_I2C_CHARDEV` | Test I2C (ví dụ MFi coprocessor) |
| `CONFIG_USB_CONFIGFS`, `CONFIG_USB_CONFIGFS_F_FS`, `_NCM` | Test USB gadget |

Trên board:

```sh
zcat /proc/config.gz | grep -E 'BT_RFCOMM|BT_BREDR|BT_LE|I2C_CHARDEV|USB_CONFIGFS'
```

Thiếu option thì thêm một fragment (ví dụ `ctt.cfg`) vào bbappend của kernel trong `<layer>`:

```bitbake
SRC_URI:append = " file://ctt.cfg"
```

## 6. Build và kiểm tra

```bash
bitbake ctt-agent                               # chỉ build agent, nhanh, để sửa lỗi recipe
oe-pkgdata-util list-pkg-files ctt-agent        # /usr/bin/ctt-agent, ctt-usb-gadget, 2 file .service
bitbake <image>
```

Không cần flash lại cả image để thử: chép thẳng binary vừa build lên board.

```bash
ls tmp/work/*/ctt-agent/*/image/usr/bin/ctt-agent
scp tmp/work/*/ctt-agent/*/image/usr/bin/ctt-agent root@<board-ip>:/tmp/   # có mạng
```

Không có mạng thì chép bằng USB stick ([bt-client-test.md](bt-client-test.md) bước 1).

Đang sửa code CTT và muốn build lại ngay (`externalsrc`, ở máy có repo):

```bitbake
INHERIT += "externalsrc"
EXTERNALSRC:pn-ctt-agent = "/path/to/connectivity-tools"
EXTERNALSRC_BUILD:pn-ctt-agent = "${WORKDIR}/build"
```

Hoặc dùng `devtool modify ctt-agent`.

## 7. Kiểm tra trên board (qua UART)

```sh
ctt-agent version                         # ctt-agent X.Y.Z (CTP v1, aarch64)
systemctl status ctt-agent-bt ctt-agent-ble
journalctl -f -u ctt-agent-bt             # trace mỗi giây trong lúc test: đã nhận / đã gửi bao nhiêu
```

Từ PC:

```
ctt-agent-console.exe run --addr <địa chỉ BT của board> --channel 22 -m tx -d 10
ctt-agent-console.exe suite --addr <địa chỉ BT của board> --link ble-gatt
```

## 8. Lỗi thường gặp

| Lỗi | Nguyên nhân / cách xử lý |
|---|---|
| `Unable to find file file://ctt-agent` | Thiếu `files/ctt-agent/`: chạy `scripts/export-recipe.sh`, hoặc thư mục chưa được commit vào `<layer>` |
| `Fetcher failure … ` (cách B) | Máy build không clone được repo: dùng cách A, hoặc đặt `CTT_GIT_URI` về mirror nội bộ |
| `Nothing PROVIDES 'ctt-agent'` | Recipe không nằm trong `BBFILES` của `<layer>` (`layer.conf`, đường dẫn `recipes-*/*/*.bb`) |
| `QA Issue: … installed-vs-shipped` | File được cài nhưng thiếu trong `FILES:${PN}` |
| Trên board: `RFCOMM socket: … (kernel built without CONFIG_BT_RFCOMM?)` | Thiếu `CONFIG_BT_RFCOMM` (bước 5) hoặc chưa nạp module (`modprobe rfcomm`) |
| Trên board: `rfcomm listen …/22: Address already in use …` | Đang có một `ctt-agent server` khác (service đã bật). `systemctl stop ctt-agent-bt`, hoặc dùng luôn service đó |
| Trên board: `GATT: cannot register the CTT service with BlueZ …` | bluetoothd chưa chạy hoặc adapter chưa bật ([bt-ble.md](bt-ble.md)) |
