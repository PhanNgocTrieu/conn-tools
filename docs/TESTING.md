# Hướng dẫn test — Connectivity Test Tool (CTT)

> DUT: **Vendor board**, Linux build bằng Yocto. Host: Windows + WSL2 (Ubuntu 24.04).
> Áp dụng cho CTT v0.1 (Phase 0–4). Test USB sẽ được bổ sung khi tool hỗ trợ.

Tài liệu gồm 3 cấp test, chạy theo thứ tự:

| Cấp | Mục đích | Cần phần cứng? |
|---|---|---|
| **A. Tự kiểm tra tool** | Chắc chắn tool chạy đúng trước khi đo thiết bị | Không |
| **B. Bring-up DUT** | Host kết nối được tới DUT, agent chạy được trên DUT | DUT + mạng |
| **C. Test tính năng** | Đo WiFi / Bluetooth / MFi (sau này thêm USB) | DUT + dongle |

Mỗi test case có mã (`WIFI-03`, `BT-05`, ...) để ghi vào báo cáo hoặc bug tracker.

---

## 0. Chuẩn bị bàn test (làm một lần)

### 0.1 Sơ đồ

```
 Windows PC                                         board DUT
 ┌───────────────────────────────┐                  ┌─────────────────────────┐
 │ WSL2: ctt + ctt-agent         │── Ethernet/WiFi ─│ sshd, ctt-agent         │
 │   USB BT dongle  (usbipd) ))) │~~~~ Bluetooth ~~~│ BT module               │
 │   (WiFi: qua card của Windows │~~~~~ WiFi ~~~~~~~│ WiFi module             │
 │    hoặc USB WiFi dongle)      │                  │                         │
 └───────────────────────────────┘                  └─────────────────────────┘
```

- **Đường điều khiển (SSH)** nên đi qua Ethernet. Như vậy khi test WiFi, traffic điều khiển không chiếm băng thông của link đang đo.
- Đặt DUT và anten cách dongle khoảng **1 m**, ít vật cản. Ghi lại vị trí trong báo cáo, vì kết quả phụ thuộc nhiều vào môi trường RF.

### 0.2 Host (WSL)

```bash
cd ~/connectivity-tools
scripts/setup-wsl.sh --install      # cần sudo; nếu script vừa bật systemd: chạy `wsl --shutdown` rồi mở lại WSL
scripts/setup-wsl.sh                # chạy lại: không còn dòng [fail]
scripts/build-host.sh               # build + chạy toàn bộ test của tool
```

Gắn BT dongle vào WSL (PowerShell trên Windows, lần đầu chạy bằng quyền Admin):

```powershell
.\scripts\usbipd-attach.ps1                                   # xem BUSID / VID:PID
.\scripts\usbipd-attach.ps1 -HardwareId <vid:pid> -AutoAttach
```

### 0.3 Build agent cho board

```bash
# Ưu tiên: dùng Yocto SDK của image board (bitbake <image> -c populate_sdk)
scripts/build-dut.sh --sdk /opt/<board-sdk>/environment-setup-aarch64-...
# Nếu chưa có SDK: build tĩnh bằng cross gcc của Ubuntu
scripts/build-dut.sh --generic --static
```

### 0.4 Biến môi trường dùng trong tài liệu

```bash
export CTT_DUT=root@<ip-ethernet-của-DUT>    # khỏi phải gõ --dut ở mỗi lệnh
alias ctt="python3 -m ctt"
```

---

## A. Tự kiểm tra tool (không cần DUT)

| ID | Mục đích | Lệnh | Pass khi |
|---|---|---|---|
| **SYS-01** | Toàn bộ unit test và integration test | `cd build/host && ctest --output-on-failure` | `100% tests passed` |
| **SYS-02** | Các mode tx/rx/bidir/echo qua loopback | `ctt selftest` | `4/4 passed`, LOST = 0, CRC = 0 |
| **SYS-03** | Đường đi dạng gói (giống L2CAP) | `ctt run --dut local -t unix -o type=seqpacket -o mtu=672 -m bidir -b 990 -d 5s` | PASS, lost = 0, crc_err = 0 |

> Nếu cấp A fail thì **không** chạy tiếp, vì số đo phía sau sẽ không đáng tin.

---

## B. Bring-up DUT

| ID | Mục đích | Lệnh | Pass khi |
|---|---|---|---|
| **DUT-01** | SSH không cần mật khẩu | `ssh-copy-id $CTT_DUT` rồi `ssh -o BatchMode=yes $CTT_DUT true` | exit code 0 |
| **DUT-02** | Copy agent và chạy được trên DUT | `ctt deploy` | in ra `ctt-agent 0.1.0 (CTP v1, aarch64)` |
| **DUT-03** | Kiểm kê stack connectivity | `ctt dut-info > dut-info.txt` | Có card mạng WiFi; có `hci0` và module `rfcomm`/`bluetooth`; liệt kê được `/dev/i2c-*` |
| **DUT-04** | Đường TCP qua Ethernet (baseline của chính tool) | `ctt run -m bidir -d 10s` | PASS. Throughput gần tốc độ link Ethernet |

**Lưu file `dut-info.txt` cùng báo cáo.** File này ghi lại phiên bản kernel, image và tool có trên DUT tại thời điểm test.

---

## C1. WiFi (TCP / UDP)

**Điều kiện:**
- DUT đã kết nối cùng AP với PC, hoặc PC kết nối vào SoftAP của DUT.
- Lấy IP WiFi của DUT: `ctt dut-info` (mục network).
- Dùng `--dut root@<ip-wifi>` cho các test dưới đây, hoặc giữ SSH qua Ethernet và chỉ trỏ data sang WiFi bằng `-e <ip-wifi>:5201`.

```bash
WIFI=<ip-wifi-của-DUT>
```

| ID | Mục đích | Lệnh | Pass khi |
|---|---|---|---|
| **WIFI-01** | Throughput host → DUT | `ctt run -e $WIFI:5201 -m tx -d 30s --expect up_mbps_min=<X>` | up ≥ X, lost = 0, crc = 0 |
| **WIFI-02** | Throughput DUT → host | `ctt run -e $WIFI:5201 -m rx -d 30s --expect down_mbps_min=<X>` | down ≥ X |
| **WIFI-03** | Full-duplex | `ctt run -e $WIFI:5201 -m bidir -d 30s` | Cả 2 chiều > 0, không lỗi. Ghi lại tổng throughput |
| **WIFI-04** | Độ trễ khứ hồi | `ctt run -e $WIFI:5201 -m echo -d 20s --expect rtt_p99_us_max=<Y>` | p99 ≤ Y |
| **WIFI-05** | Ảnh hưởng của kích thước ghi | `ctt profile profiles/tcp_smoke.yaml` (test `block-sweep`, có thể thêm `-e`) | Không lỗi. Ghi đường cong throughput theo block |
| **WIFI-06** | Soak (độ ổn định lâu dài) | `ctt run -e $WIFI:5201 -m bidir -d 1h -i 10s --name wifi-soak` | Không ERROR. Throughput trong `intervals.csv` không tụt về 0 |
| **WIFI-07** | Bộ test chuẩn TCP | `ctt profile profiles/wifi_tcp_throughput.yaml` (sửa ngưỡng `expect` trước) | Mọi test PASS |
| **WIFI-08** | UDP: mất gói và jitter ở tải cố định | `ctt run -t udp -e $WIFI:5201 -m tx -r 50M -d 30s --wifi --expect loss_pct_max=1` | loss ≤ 1%. Ghi lại jitter |
| **WIFI-09** | UDP: tìm mức tải bắt đầu mất gói | `ctt profile profiles/wifi_udp.yaml --only load` | Mức tải cao nhất mà loss vẫn ≤ ngưỡng (đọc trong `summary.csv`) |
| **WIFI-10** | Luồng kiểu VoIP (gói nhỏ, tốc độ thấp) | `ctt profile profiles/wifi_udp.yaml --only voip` | loss ≤ 0.5%, jitter ≤ 3 ms |
| **WIFI-11** | RTT qua UDP, có đếm request bị mất | `ctt run -t udp -e $WIFI:5201 -m echo --echo-interval 20 -d 30s --expect echo_loss_pct_max=1` | echo loss ≤ 1% |

**Thông tin link:** thêm `--wifi` (hoặc `wifi_iface: auto` trong profile). Tool sẽ đọc `iw dev <if> link` trên DUT trước và sau mỗi test, rồi đưa signal (dBm), tx/rx bitrate và tần số vào report (`wifi_*` trong summary). Có thể đặt điều kiện `--expect wifi_signal_dbm_min=-65` để loại các lần đo khi sóng yếu.

**UDP khác TCP ở chỗ nào:** UDP không retransmit, nên `lost` / `loss_pct` là tỉ lệ mất gói thật trên sóng, và `jitter_us` là jitter theo RFC 3550. Client mặc định giới hạn 10 Mbit/s, nên luôn đặt `-r`. Trong `result.json`, mục `link.udp_rcvbuf_errors` khác 0 nghĩa là gói bị rơi ngay trong buffer của máy nhận (do CPU hoặc buffer), không phải trên sóng.

**Ghi kèm vào báo cáo:**
- Kết quả `iw dev wlan0 link` trên DUT (đã tự động có khi dùng `--wifi`).
- Băng tần 2.4/5 GHz, kênh, và chuẩn của AP.

> Lưu ý về WSL: traffic đi qua card WiFi hoặc Ethernet của Windows. Nếu PC nối với AP bằng Ethernet, số đo phản ánh đúng link WiFi của DUT. Nếu PC cũng dùng WiFi, cả hai đầu cùng chia sẻ sóng, nên throughput sẽ thấp hơn.

---

## C2. Bluetooth

**Điều kiện:**
- `ctt bt list` thấy controller của dongle.
- `ctt bt list --side dut` thấy controller của DUT.

### C2.1 Controller và pairing

| ID | Mục đích | Lệnh | Pass khi |
|---|---|---|---|
| **BT-01** | Controller có mặt ở hai phía | `ctt bt list` và `ctt bt list --side dut` | Mỗi bên có ít nhất 1 controller. Ghi lại địa chỉ |
| **BT-02** | DUT được phát hiện khi scan | `ctt bt prepare --side dut` rồi `ctt bt scan -t 15` | Địa chỉ của DUT có trong danh sách |
| **BT-03** | Pair host ↔ DUT | `ctt bt pair --dut-accept` | `OK pair ...`. Ghi lại thời gian pair |
| **BT-04** | Thông tin sau khi pair | `ctt bt info <DUT-addr>` | `Paired: yes`, `Trusted: yes`. Có UUID Serial Port (nếu DUT quảng bá SPP) |
| **BT-05** | Độ ổn định pairing | `ctt bt pair-stress --dut-accept -n 50 --connect --min-success-rate 0.98` | Tỉ lệ thành công ≥ 98% (hoặc theo yêu cầu sản phẩm) |
| **BT-06** | Xoá pairing | `ctt bt unpair <DUT-addr>` rồi `ctt bt info <DUT-addr>` | `removed`, sau đó `not known` |

> `--dut-accept` chạy agent BlueZ trên DUT để tự đồng ý pairing. Nếu phần mềm BT của sản phẩm tự quản lý pairing (có UI xác nhận), **bỏ cờ này** và xác nhận trên DUT.
> Khi chạy BT-05, tool tự trả lời các prompt pairing ở phía host. Số và loại prompt được in ra để đối chiếu với phương thức pairing mong đợi (Just Works hay Numeric Comparison).

### C2.2 Throughput RFCOMM / L2CAP

Agent chạy làm server trên DUT. Channel RFCOMM do kernel của DUT tự cấp, nên không đụng HFP/PBAP. Tool tự đọc địa chỉ và channel đó.

| ID | Mục đích | Lệnh | Pass khi |
|---|---|---|---|
| **BT-10** | RFCOMM host → DUT | `ctt run -t rfcomm -m tx -d 30s --expect up_mbps_min=<X>` | up ≥ X, lost = 0, crc = 0 |
| **BT-11** | RFCOMM DUT → host | `ctt run -t rfcomm -m rx -d 30s --expect down_mbps_min=<X>` | down ≥ X |
| **BT-12** | RFCOMM full-duplex | `ctt run -t rfcomm -m bidir -d 30s` | Không lỗi. Ghi lại tổng throughput |
| **BT-13** | Độ trễ RFCOMM | `ctt run -t rfcomm -m echo -b 64 --echo-interval 20 -d 10s` | p99 ≤ yêu cầu |
| **BT-14** | Kích thước ghi và mức security | `ctt profile profiles/bt_rfcomm_throughput.yaml --only block --only security` | Không lỗi. So sánh throughput giữa các mức `low` / `medium` / `high` |
| **BT-15** | L2CAP BR/EDR | `ctt run -t l2cap -o type=bredr -o imtu=1691 -m tx -d 30s` | PASS. Thường cao hơn RFCOMM một chút |
| **BT-16** | Soak RFCOMM | `ctt run -t rfcomm -m bidir -d 1h -i 10s --timeout 30s --name bt-soak` | Không ERROR (không rớt link) |
| **BT-17** | Bộ test chuẩn | `ctt profile profiles/bt_rfcomm_throughput.yaml` (sửa ngưỡng trước) | Mọi test PASS |

**Mốc tham khảo:**
- BR/EDR EDR 3 Mbps: RFCOMM goodput thực tế khoảng **2.0–2.2 Mbps**.
- EDR 2 Mbps: khoảng 1.4 Mbps.
- Dưới khoảng 1 Mbps là đáng ngờ. Kiểm tra theo thứ tự: block size, mức security, nhiễu WiFi 2.4 GHz, rồi overhead USB/IP (đo lại bằng dongle trên máy Linux native).

### C2.3 Coexistence BT + WiFi

| ID | Mục đích | Cách làm | Pass khi |
|---|---|---|---|
| **COEX-01** | BT và WiFi 2.4 GHz chạy đồng thời | Mở 2 terminal cùng lúc: (1) `ctt run -e $WIFI:5202 --port 5202 -m bidir -d 60s --name coex-wifi`, (2) `ctt run -t rfcomm -m bidir -d 60s --name coex-bt` | Cả hai PASS. So với khi chạy riêng lẻ (WIFI-03, BT-12), throughput giảm không quá mức yêu cầu |

---

## C3. MFi (Auth Coprocessor qua I2C)

Tool không chứa register map của Apple (thuộc NDA). Bạn mô tả các bước đọc/ghi trong một **file config private**, sau đó tool chạy các bước đó trên DUT qua `ctt-agent i2c`. Tool tự retry khi chip NACK (chip ngủ hoặc bận), đo thời gian từng bước, và kiểm tra dữ liệu đọc ra có giống nhau giữa các lần lặp không.

**Chuẩn bị (một lần):**

```bash
cp configs/mfi_cp.example.yaml configs/mfi_cp.private.yaml    # git sẽ bỏ qua file *.private.yaml
# Sửa: bus (số N của /dev/i2c-N trên board), address (7-bit, theo schematic),
#      các thanh ghi / độ dài / giá trị mong đợi theo Apple MFi spec của chip (2.0C / 3.0)
ctt mfi configs/mfi_cp.example.yaml --dut local     # chạy thử với bus giả lập: phải 5/5 PASS
```

Tìm bus và địa chỉ: xem mục `i2c` trong `ctt dut-info`. Có thể chạy thêm `ssh $CTT_DUT i2cdetect -y -r <N>`, nhưng chip có thể không hiện ra vì nó NACK khi đang ngủ.

| ID | Mục đích | Lệnh | Pass khi |
|---|---|---|---|
| **MFI-01** | Chip trả lời trên bus | `ctt mfi configs/mfi_cp.private.yaml --only MFI-01` | 100% probe OK (retries được phép) |
| **MFI-02** | Version / device ID / protocol | `--only MFI-02` | Giá trị đọc được đúng bằng `EXPECT` |
| **MFI-03** | Certificate | `--only MFI-03` | Độ dài đúng; mọi lần đọc cho kết quả giống nhau (`stable`) |
| **MFI-04** | Challenge / response | `--only MFI-04` | Mọi lần thành công; thời gian bước `done` ≤ ngưỡng |
| **MFI-05** | Stress | `--only MFI-05` (hoặc cả file) | `success_rate` = 1.0 |
| **MFI-ALL** | Toàn bộ | `ctt mfi configs/mfi_cp.private.yaml` | 5/5 PASS |

- `--repeat-scale 0.1` để chạy nhanh khi đang chỉnh config. Dùng `-q` để ẩn bảng chi tiết của từng bước.
- Cột `retries` cao là bình thường với Auth CP, vì chip NACK khi đang ngủ hoặc đang xử lý. Chỉ đáng lo khi bước bị **fail**, tức là đã hết số lần retry (tăng `retries` / `retry_delay_ms` trong config).
- Test ở 100 kHz và 400 kHz: tốc độ bus do device tree của board quyết định. Muốn test cả hai thì đổi DT, rồi chạy lại MFI-ALL và so sánh.
- Chạy tay trên DUT: `ctt-agent i2c --help`.

## C4. USB — ⏳ Phase 5

Phạm vi test phụ thuộc vào vai trò cổng USB của board (host, device/OTG hay cả hai). Kiểm tra mục `usb` trong output của `ctt dut-info`.

---

## D. Đọc kết quả

Mỗi lần chạy tạo một thư mục `out/<thời-gian>_<tên>/`:

```
report.html                       mở bằng trình duyệt: bảng tổng hợp, biểu đồ throughput theo thời gian, chi tiết check
junit.xml                         cho CI (Jenkins / GitLab / GitHub Actions)
summary.csv / summary.json        tổng hợp mọi test: throughput, lost, crc, rtt, verdict
NN_<test>/result.json             đầy đủ: spec, kết quả hai phía, link info, lỗi
NN_<test>/intervals.csv           throughput theo từng interval (vẽ biểu đồ, xem tụt/đứt)
```

| Cột / trường | Ý nghĩa |
|---|---|
| `up_mbps` | host → DUT, **đo ở DUT** (bên nhận), là số dùng để kết luận |
| `down_mbps` | DUT → host, đo ở host |
| `*_sender_mbps` | lượng byte bên gửi đẩy vào socket. Luôn ≥ số bên nhận, chỉ dùng để tham khảo |
| `lost`, `crc_errors` | Với TCP/RFCOMM/L2CAP thì phải bằng 0. Khác 0 nghĩa là có bug (driver, firmware, stack). Với UDP, `lost` là mất gói thật |
| `loss_pct`, `jitter_us` | Chỉ có với UDP: tỉ lệ mất gói của chiều tệ nhất, jitter RFC 3550 |
| `wifi_signal_dbm`, `wifi_tx/rx_bitrate_mbps` | Có khi dùng `--wifi`: link WiFi của DUT lúc bắt đầu test |
| `rtt_*_us` | Độ trễ khứ hồi ở tầng ứng dụng |
| `link` (trong result.json) | WiFi/TCP: retransmits, cwnd. BT: security, ACL MTU, bộ đếm byte của HCI |

**Verdict:**
- `PASS`: đạt mọi tiêu chí `expect`.
- `FAIL`: test chạy xong nhưng không đạt ngưỡng.
- `ERROR`: test không chạy được (kết nối, pairing, agent...). Xem trường `error`.

Exit code của `ctt` bằng 0 khi mọi test PASS, nên dùng trực tiếp được trong CI.

**So sánh với baseline (regression):** giữ lại thư mục của một lần chạy "chuẩn" (ví dụ image release trước). Với mỗi image mới, chạy cùng profile rồi so sánh:

```bash
ctt compare out/<baseline-run> out/<new-run> --tolerance 10
```

Lệnh này báo các metric xấu đi quá 10% (throughput giảm; RTT, loss hoặc jitter tăng), các counter đáng lẽ bằng 0 (`lost`, `crc_errors`) mà tăng, và các test từ PASS chuyển sang không PASS. Exit code là 1 nếu có regression.

Tạo lại `report.html` / `junit.xml` cho một thư mục cũ: `ctt report out/<run>`.

---

## E. Báo lỗi

Khi một test FAIL hoặc ERROR, đính kèm:

1. Thư mục `out/...` của lần chạy đó.
2. `dut-info.txt` (DUT-03).
3. Log kernel trên DUT: `ssh $CTT_DUT dmesg | tail -200`.
4. Nếu là lỗi Bluetooth: chạy lại kèm bản ghi HCI.
   ```bash
   sudo btmon -w bt-host.snoop &                          # phía host
   ssh $CTT_DUT 'btmon -w /tmp/bt-dut.snoop' &            # phía DUT (nếu image có btmon)
   ```
   File `.snoop` mở được bằng Wireshark.
5. Nếu là lỗi WiFi: `iw dev wlan0 link` và `iw dev wlan0 station dump` trên DUT, chụp ngay khi lỗi xảy ra.

---

## F. Mẫu báo cáo nhanh

```
Ngày / người test   :
Image DUT           : (uname -a, /etc/os-release từ dut-info)
ctt version         : (ctt --version, git rev-parse --short HEAD)
Bàn test            : khoảng cách, AP/kênh, dongle BT (VID:PID, chipset)
Kết quả             : đính kèm summary.csv
Ghi chú             :
```

Xử lý sự cố Bluetooth chi tiết: xem [bluetooth.md](bluetooth.md#xử-lý-sự-cố). Cài môi trường: xem [wsl-setup.md](wsl-setup.md).
