# Connectivity Test Tool (CTT) — Kế hoạch triển khai

> Phiên bản: v0.4 · 2026-11-10 — Phase 0–7 (Yocto) đã code xong; macOS host chưa làm (xem mục 5)
> Phạm vi: test USB / WiFi / Bluetooth / MFi trên DUT **Vendor board chạy Linux build bằng Yocto**.
> Host: Windows → **WSL2 (Ubuntu 24.04)**, sau này là Linux native và macOS. Giao diện: **CLI**.

---

## 1. Quyết định kiến trúc

| Hạng mục | Quyết định | Lý do |
|---|---|---|
| Ngôn ngữ data plane | **C++17** | Một codebase build cho 2 nơi: host x86_64 (WSL) và DUT aarch64 (Yocto SDK). Đủ nhanh để đo WiFi ở mức Gbps. |
| Ngôn ngữ control plane | **Python 3.10+** | CLI, điều phối, deploy lên DUT qua SSH, test matrix, report. Team đã quen. |
| Mô hình | **Kiểu iperf**: binary `ctt-agent` (C++) chạy cả ở host lẫn DUT, vai trò server hoặc client | Không phải viết 2 phần mềm khác nhau. Có thể test DUT↔DUT hoặc host↔DUT. |
| Giao thức | **CTP**: một framing chung cho mọi transport | Mọi test mode và metrics dùng chung cho tất cả transport. |
| MFi | Test **Auth Coprocessor qua I2C** chạy ngay trên DUT (`/dev/i2c-N`) | Không cần iPhone ở giai đoạn này. |
| Đóng gói trên DUT | Layer Yocto `meta-ctt` (recipe `ctt-agent`, thêm `iperf3`, `bluez5-tools`, `i2c-tools`) | Cài vào image chuẩn, tái lập được. |

```
 ┌──────────────── HOST (WSL2) ────────────────┐            ┌──────────── DUT board (Yocto) ───────────┐
 │ ctt (Python CLI)                            │  SSH       │ ctt-agent (C++, aarch64)                    │
 │  ├─ deploy / start / stop agent ────────────┼───────────▶│  ├─ transports: tcp, udp, rfcomm, l2cap,    │
 │  ├─ profiles YAML, test matrix              │ (Ethernet/ │  │   usb (gadget/host), i2c (MFi CP)        │
 │  ├─ gom JSON kết quả 2 phía                 │  WiFi/UART)│  └─ modes: sink / source / echo / bidir     │
 │  └─ report: JSON, CSV, HTML, JUnit          │            └─────────────────────────────────────────────┘
 │ ctt-agent (C++, x86_64) ◀═══ data plane ════╪══ RFCOMM / L2CAP / TCP / UDP / USB bulk ══════════════════╡
 │   BT: USB dongle + BlueZ (usbipd)           │
 │   USB: usbipd-win                           │
 │   WiFi: mirrored networking / USB dongle    │
 └─────────────────────────────────────────────┘
```

**Tách control plane khỏi data plane:** lệnh, deploy và thu log đi qua SSH. Data plane chỉ mang traffic đang được đo, nên traffic điều khiển không làm sai số đo.

---

## 2. CTP — Connectivity Test Protocol v1

Đặc tả đầy đủ: [ctp-spec.md](ctp-spec.md). Tóm tắt:
- Header 32 byte, little-endian: `magic "CTP1" | ver | type | flags | seq | payload_len | ts_ns | crc32 | reserved`
- Message: START, START_ACK, DATA, STOP, STATS, ECHO_REQ, ECHO_RSP, ERROR
- Payload là pattern PRBS dựng sẵn và CRC-32 tính sẵn, nên phía gửi không tốn CPU cho việc này. Bên nhận có thể tắt verify bằng `--no-verify`.

**Test mode:** `tx`, `rx`, `bidir`, `echo` (RTT), `integrity`, `soak` (nhiều giờ, có reconnect).

**Metrics:** goodput (Mbps), PPS, bytes, loss/reorder/CRC error, RTT p50/p95/p99/max, jitter. Bên nhận lấy mẫu mỗi 1 giây (time-series). Kèm CPU% và RSS của agent, cùng thông tin link (RSSI, MTU, PHY, tốc độ USB) khi có.

---

## 3. Phạm vi từng interface

### 3.1 Bluetooth (ưu tiên số 1, vì đã có code RFCOMM)
- **Host:** USB BT dongle attach vào WSL (`usbipd attach --wsl`), BlueZ chạy bằng systemd.
- **Pairing tự động:** Python gọi BlueZ D-Bus (`dbus-next`). Gồm scan, pair, trust, connect, remove. Agent tự xử lý passkey/confirm. Có stress test pair/unpair N lần.
- **RFCOMM:** DUT là server (channel cấu hình được, đăng ký SDP record), host là client. Đo tx/rx/bidir/echo.
- **L2CAP:** `SOCK_SEQPACKET`, BR/EDR (PSM lẻ ≥ 0x1001) và LE CoC.
- **Socket tuning:** `SO_SNDBUF`/`SO_RCVBUF`, `BT_SECURITY`, kích thước write (bội số của RFCOMM MTU), đọc MTU qua `getsockopt`.
- **Phase sau:** BLE GATT throughput (Write Without Response / Notify), PHY 2M, DLE.

### 3.2 WiFi
- TCP/UDP qua `ctt-agent`. Thêm chế độ tương thích iperf3 để đối chiếu.
- Host dùng `networkingMode=mirrored` để host và DUT cùng subnet qua WiFi của Windows.
- Thu thông tin link trên DUT: `iw dev wlan0 link` (RSSI, bitrate, kênh).
- Phase sau: test STA/SoftAP, reconnect stress, scan time.

### 3.3 USB (cần chốt vai trò của DUT, xem mục 6)
- **DUT ở device mode (OTG):** DUT dựng configfs gadget, gồm FunctionFS với bulk vendor interface (do `ctt-agent` phục vụ) và ECM/NCM (để test TCP qua USB). Host dùng libusb.
- **DUT ở host mode:** test thiết bị cắm vào DUT, gồm throughput mass storage (`fio`/`dd` với O_DIRECT), enumerate time, hot-plug stress.
- Lưu ý: usbipd trên WSL giới hạn khoảng 30–40 MB/s. Số đo USB "thật" phải chạy trên Linux native.

### 3.4 MFi (Auth Coprocessor qua I2C)
Chạy hoàn toàn trên DUT qua `/dev/i2c-N` (ioctl `I2C_RDWR`):
- **Probe:** tìm chip ở địa chỉ I2C cấu hình được. **Retry khi gặp NACK**, vì chip auth thường NACK khi đang ngủ hoặc bận.
- **Đọc thông tin nhận dạng:** version, device ID, error register.
- **Đọc certificate:** kiểm tra độ dài và tính toàn vẹn qua nhiều lần đọc.
- **Challenge/response:** đo thời gian xử lý và tỉ lệ thành công.
- **Stress:** đọc N lần, thống kê lỗi I2C, latency, ở nhiều tốc độ bus (100/400 kHz).

⚠️ Register map lấy từ spec Apple MFi (NDA). Để trong file cấu hình **private** (`mfi_cp.private.yaml`), không commit vào repo public.

---

## 4. Cấu trúc repo

```
connectivity-tools/
├── CMakeLists.txt
├── agent/                     # C++17 — build cho cả host và DUT
│   ├── include/ctt/           # ctp.hpp, transport.hpp, metrics.hpp
│   ├── src/ctp/               # codec, crc, prbs
│   ├── src/transports/        # tcp.cpp udp.cpp rfcomm.cpp l2cap.cpp usb_ffs.cpp usb_libusb.cpp i2c.cpp
│   ├── src/modes/             # tx rx bidir echo integrity
│   ├── src/main.cpp           # ctt-agent CLI, output JSON lines
│   └── tests/                 # GoogleTest
├── ctt/                       # Python package (CLI)
│   ├── cli.py                 # `ctt discover|pair|run|report|deploy`
│   ├── dut.py                 # SSH (paramiko): deploy, start/stop agent, lấy log
│   ├── bt.py                  # BlueZ D-Bus: scan/pair/trust
│   ├── runner.py              # chạy profile, đồng bộ 2 phía
│   └── report/                # json/csv/html (plotly)/junit
├── profiles/                  # bt_rfcomm_throughput.yaml, wifi_udp_sweep.yaml, mfi_cp_stress.yaml
├── yocto/meta-ctt/            # recipes-connectivity/ctt-agent/ctt-agent_git.bb
├── scripts/                   # setup-wsl.sh, usbipd-attach.ps1, build-dut.sh
└── docs/                      # PLAN.md, ctp-spec.md, wsl-setup.md
```

Ví dụ sử dụng:
```bash
ctt deploy --dut root@192.168.1.50                         # copy ctt-agent aarch64 lên DUT
ctt pair bt 00:11:22:33:44:55
ctt run bt-rfcomm --dut 192.168.1.50 --bdaddr 00:11:22:33:44:55 --channel 3 --mode bidir --duration 60
ctt run --profile profiles/wifi_udp_sweep.yaml --report out/
ctt run mfi-cp --dut 192.168.1.50 --bus 2 --iterations 1000
```

---

## 5. Lộ trình

| Phase | Nội dung | Kết quả cuối phase | Ước lượng |
|---|---|---|---|
| **0. Nền tảng** ✅ (còn chờ: build aarch64 bằng SDK, SSH vào DUT thật) | Repo, CMake + toolchain file cho Yocto SDK, Python package. Script `setup-wsl.sh` (systemd, bluez, usbip, udev, kiểm tra module). Spec CTP. Xác nhận SSH vào DUT | Build ra `ctt-agent` cho x86_64 và aarch64. SSH vào DUT được | 1 tuần |
| **1. Core + TCP loopback** ✅ | CTP codec, metrics, các mode, transport TCP. `ctt run` điều phối 2 phía. Report JSON/CSV | Chạy TCP host↔DUT và có report đầy đủ | 2 tuần |
| **2. Bluetooth RFCOMM/L2CAP** ✅ (chờ test với dongle + DUT) | Transport RFCOMM + L2CAP (BR/EDR, LE CoC) dùng socket kernel, không cần libbluetooth. Channel tự cấp. Pairing tự động qua `bluetoothctl` (host + DUT). Pair-stress. Tuning buffer/MTU/security. (SDP: để phase sau) | Đo được throughput RFCOMM, có pair stress | 2–3 tuần |
| **3. MFi I2C** ✅ (chờ register map + board) | Transport I2C, test probe/version/cert/challenge/stress | Report MFi CP | 1–2 tuần |
| **4. WiFi** ✅ | UDP (rate-limited, đo loss/jitter), so sánh với iperf3, thu thông tin link | Đo được throughput WiFi | 1–2 tuần |
| **5. USB** ✅ (gadget chờ test trên board) | Theo vai trò DUT: FunctionFS gadget + libusb, hoặc mass storage | Đo được throughput USB | 2–3 tuần |
| **6. Orchestration** ✅ | Test matrix (sweep size/MTU/buffer), soak test, HTML report, so sánh baseline, JUnit | Chạy suite tự động qua đêm | 2 tuần |
| **7. Yocto + đa nền tảng** ✅ Yocto / ⏳ macOS | Layer `meta-ctt` trong image. Port host sang Linux native, rồi macOS (BT host qua IOBluetooth) | Bản phát hành | 2–3 tuần |

---

## 6. Câu hỏi còn mở

1. **Truy cập DUT:** DUT có Ethernet/WiFi để SSH vào không, hay chỉ có UART debug? Nếu chỉ có UART thì control plane cần một backend serial (pyserial).
2. **Yocto SDK:** đã có SDK (`populate_sdk`) cho board chưa? Trong image có `libbluetooth`/`bluez5` không?
3. **Stack BT trên DUT:** kernel BlueZ (`AF_BLUETOOTH`) cho tất cả, hay có stack userspace riêng cho profile của xe (HFP/A2DP)? Channel RFCOMM nào đang bị profile khác chiếm?
4. **USB:** port USB của DUT ở vai trò host, device (OTG), hay cả hai? Mục tiêu là test gì (CarPlay/AA, mass storage, kết nối PC)?
5. **MFi:** chip Auth CP phiên bản nào (2.0C / 3.0)? Nằm trên bus I2C số mấy, địa chỉ bao nhiêu?
6. **Chỉ tiêu pass/fail:** throughput tối thiểu của từng interface (ví dụ RFCOMM ≥ 1.5 Mbps)?
