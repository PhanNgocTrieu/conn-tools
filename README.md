# CTT — Connectivity Test Tool

Tool đo throughput, latency và độ toàn vẹn dữ liệu cho USB / WiFi / Bluetooth / MFi trên thiết bị embedded (DUT: Vendor board, Yocto Linux).

- `ctt-agent` (C++17): một binary duy nhất, chạy cả trên host (x86_64, WSL) và DUT (aarch64). Hoạt động theo mô hình server/client giống iperf và dùng giao thức chung [CTP](docs/ctp-spec.md).
- `ctt` (Python): CLI điều phối. Deploy agent lên DUT qua SSH, chạy hai phía, áp tiêu chí pass/fail và xuất report JSON/CSV.

Kế hoạch tổng thể: [docs/PLAN.md](docs/PLAN.md) · USB: [docs/usb.md](docs/usb.md) · **Hướng dẫn test: [docs/TESTING.md](docs/TESTING.md)** · Cài môi trường: [docs/wsl-setup.md](docs/wsl-setup.md) · Bluetooth: [docs/bluetooth.md](docs/bluetooth.md)

## Bắt đầu nhanh

```bash
scripts/setup-wsl.sh --install          # lần đầu
scripts/build-host.sh                   # build và chạy unit test + loopback test
python3 -m ctt selftest                 # tất cả các mode qua TCP loopback
python3 -m ctt ui --dut root@<dut-ip>   # giao diện web: mở http://localhost:8765 trên Windows
```

Giao diện web chỉ gọi lại các lệnh `ctt` và hiện đúng dòng lệnh tương ứng, nên chạy bằng giao diện hay CLI đều cho cùng kết quả. Server chỉ lắng nghe trên `127.0.0.1` và không có đăng nhập.

## App Bluetooth (ctt-bt)

App desktop để test throughput Bluetooth PC ↔ board: pair, đo các chiều kèm biểu đồ, và terminal hai chiều. Chạy được dưới dạng `.exe` trên Windows (build bằng GitHub Actions) hoặc `python3 -m ctt.btapp` trong WSL. Hướng dẫn: [docs/btapp.md](docs/btapp.md).

## Firmware vendor trên DUT

Image sản phẩm chạy sẵn `usb-daemon`, `bt-daemon` và `wifi-daemon`. Các daemon này đang giữ cổng USB, agent pairing BlueZ và mode WiFi. CTT làm việc với chúng như sau:

```bash
python3 -m ctt fw status                 # trạng thái daemon, WiFi mode/SSID, số bond BT, role cổng USB
python3 -m ctt fw wifi-mode ap|sta|p2p   # wifi-daemon SetMode
python3 -m ctt bt pair --dut-accept bm   # DUT xác nhận pairing qua bt-daemon (auto chọn sẵn)
python3 -m ctt fw bt-unpair <addr>       # xoá bond (bt-daemon giữ tối đa 5)
python3 -m ctt usb gadget up / down      # dừng usb-daemon + dwc2_drdmode=device / trả lại như cũ
```

Trước mỗi lần chạy `run`/`profile`, CTT cảnh báo khi test sắp giành tài nguyên với một daemon (tắt bằng `CTT_NO_FW_CHECK=1`). `--wifi` cũng chạy được khi DUT là AP: thông số lấy từ `iw station dump` của client.

Chạy với DUT:

```bash
scripts/build-dut.sh --sdk /opt/<board-sdk>/environment-setup-aarch64-...   # hoặc --generic --static
python3 -m ctt deploy   --dut root@192.168.1.50
python3 -m ctt dut-info --dut root@192.168.1.50
python3 -m ctt run      --dut root@192.168.1.50 -m bidir -d 30s
python3 -m ctt profile profiles/wifi_tcp_throughput.yaml --dut root@192.168.1.50
```

Đặt `export CTT_DUT=root@192.168.1.50` để khỏi phải gõ `--dut` mỗi lần.

Bluetooth (chi tiết ở [docs/bluetooth.md](docs/bluetooth.md)):

```bash
python3 -m ctt bt pair --dut-accept                         # host pair với DUT (lấy địa chỉ DUT qua SSH)
python3 -m ctt run -t rfcomm -m bidir -d 30s                # RFCOMM throughput
python3 -m ctt profile profiles/bt_rfcomm_throughput.yaml
python3 -m ctt bt pair-stress --dut-accept -n 50 --connect  # độ ổn định pairing
```

## Transports

| `-t` | Dùng cho | Endpoint |
|---|---|---|
| `tcp` | WiFi, Ethernet, USB-NCM | `host:port` |
| `udp` | WiFi/Ethernet: mất gói thật, jitter (luôn đặt `-r`) | `host:port` |
| `rfcomm` | Bluetooth Classic (SPP) | `BDADDR/CHANNEL` (server: channel 0 = tự cấp) |
| `l2cap` | BT L2CAP CoC, BR/EDR hoặc LE (`-o type=le`) | `BDADDR/PSM` |
| `usb` | USB bulk: server = DUT gadget (FunctionFS), client = PC (usbdevfs) | `VID:PID` (mặc định 1d6b:0104) |
| `unix` | test/CI không cần radio (`-o type=seqpacket -o mtu=` giả lập L2CAP) | `@name` |

## Các mode

Mặc định server chạy trên DUT, nên:

| Mode | Hướng | Đo gì |
|---|---|---|
| `tx` | host → DUT | throughput upstream, đo ở DUT |
| `rx` | DUT → host | throughput downstream, đo ở host |
| `bidir` | cả hai chiều cùng lúc | throughput full-duplex |
| `echo` | ping-pong | RTT min/avg/p50/p95/p99/max, jitter |

Mọi mode đều kiểm tra `seq` (mất gói, sai thứ tự) và CRC-32 của từng frame (tắt bằng `--no-verify`).

## Tiêu chí pass/fail

Dùng `--expect KEY=VALUE` trên CLI hoặc khối `expect:` trong profile:
`up_mbps_min`, `down_mbps_min`, `lost_max`, `crc_errors_max` (mặc định 0), `rtt_avg_us_max`, `rtt_p99_us_max`, `rtt_max_us_max`.

`ctt` trả exit code 0 khi mọi test PASS, nên dùng trực tiếp được trong CI. Dùng `ctt compare BASE NEW` để phát hiện regression so với một lần chạy chuẩn.

## Report

Mỗi lần chạy tạo thư mục `out/<timestamp>_<tên>/` gồm:
- `report.html`: bảng tổng hợp và biểu đồ, một file duy nhất mở được offline
- `junit.xml`: cho CI
- `summary.csv`, `summary.json`
- mỗi test một thư mục con chứa `result.json` (đủ cả hai phía) và `intervals.csv` (time-series theo từng interval)

## Đưa agent vào image Yocto

Layer [yocto/meta-ctt](yocto/meta-ctt/README.md) gồm recipe `ctt-agent` và `packagegroup-ctt` (agent, bluez5, iw, i2c-tools). Khi agent đã nằm sẵn trong image, chạy `ctt ... --dut-agent /usr/bin/ctt-agent` và không cần `ctt deploy`.

Khi chưa có SDK, dùng `scripts/build-dut.sh --generic --static --strip` để tạo một binary aarch64 tĩnh khoảng 2.3 MB. Binary này chạy được trên mọi image glibc của board. Nên dùng IP dạng số cho endpoint, vì phân giải tên miền trong binary tĩnh cần thư viện NSS của image.

## Dùng agent trực tiếp (không qua Python)

```bash
ctt-agent server                                   # trên DUT
ctt-agent client -e <dut-ip>:5201 -m bidir -d 10   # trên host
ctt-agent help
```

## Cấu trúc thư mục

```
agent/      C++ agent: CTP, transports, test modes, metrics, tests
ctt/        Python orchestrator (CLI, SSH, runner, profile, report, fw, web UI)
profiles/   YAML test profiles
configs/    MFi config mẫu (bản thật: *.private.yaml, không commit)
scripts/    setup-wsl.sh, build-host.sh, build-dut.sh, usbipd-attach.ps1
cmake/      toolchain files
yocto/      meta-ctt layer (recipe ctt-agent, packagegroup-ctt)
docs/       PLAN, TESTING (test cases), CTP spec, WSL setup, bluetooth
tests/      Python tests (python3 -m unittest discover -s tests), fakes/ (bluetoothctl giả lập)
```

## Trạng thái

| Interface | Trạng thái |
|---|---|
| TCP (WiFi/Ethernet/USB-NCM) | ✅ Phase 1 |
| Bluetooth RFCOMM / L2CAP + pairing (`ctt bt`) | ✅ Phase 2 (đã test không cần radio; chờ test với dongle + DUT) |
| MFi Auth CP (I2C) — `ctt mfi` | ✅ Phase 3 (đã test với bus giả lập; chờ register map + board) |
| WiFi UDP (loss/jitter) + link info (`--wifi`) | ✅ Phase 4 |
| Yocto layer `meta-ctt` + bản aarch64 tĩnh | ✅ Phase 7 (đã build aarch64; recipe chờ chạy trong BSP) |
| USB bulk (gadget/usbdevfs) + USB storage (`ctt usb`) | ✅ Phase 5 (unit test + storage đã test; gadget chờ board) |
