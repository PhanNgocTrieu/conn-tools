# Test Bluetooth trên board khách hàng (Client Test)

Hướng dẫn từng bước để test Bluetooth trên **board khách hàng**:
- board dùng **chip Bluetooth trên board**;
- chỉ vào board được qua **UART**;
- PC có dongle là **Client PC**, người test SSH vào máy đó từ Local PC.

Sơ đồ và chiến lược chung: [bt-test-plan.md](bt-test-plan.md). Test case đầy đủ: [bt-test-cases.md](bt-test-cases.md). Cách dùng app: [bt-app.md](bt-app.md).

Quy ước trong tài liệu này:
- `<ADDR>` = địa chỉ Bluetooth của board (lấy ở bước 2);
- `<client-pc>` = Client PC;
- channel RFCOMM cố định là **22**.

> **Nên làm trước bước 5–6 một lần ở Local Test** (EVB có SSH). Nếu có lỗi thì biết ngay là lỗi tool hay lỗi board khách, đỡ mất công debug qua UART.

---

## Bước 0: Build trên máy của bạn (WSL)

```bash
scripts/build-dut.sh --generic --static --strip    # -> build/dut/agent/ctt-agent (aarch64, static, ~2.3 MB)
scripts/build-windows.sh console                   # -> dist/ctt-agent-console.exe (lần đầu tự cài Python)
```

**Xong khi** có đủ 2 file trên.

## Bước 1: Đưa `ctt-agent` lên board (không có mạng)

Chọn một cách:

| Cách | Làm thế nào |
|---|---|
| **USB stick** (nhanh nhất) | Chép `ctt-agent` vào USB, cắm vào board, rồi trên UART chạy lệnh bên dưới bảng |
| **Qua UART** | Dùng `rz` nếu image có `lrzsz`. Nếu không: `gzip -9` rồi `base64` file trên PC, dán vào UART, trên board giải bằng `base64 -d \| gunzip > /tmp/ctt-agent`. File nén khoảng 860 KB, nên ở 115200 baud mất vài phút |
| **Image có `meta-ctt`** | Agent đã nằm ở `/usr/bin/ctt-agent`, có `ctt-agent-bt.service`. Cần nhờ bên build image |

Lệnh UART cho cách USB stick:

```sh
mount /dev/sda1 /mnt && cp /mnt/ctt-agent /tmp/ && chmod +x /tmp/ctt-agent
```

**Xong khi** `/tmp/ctt-agent version` in ra `aarch64`.

## Bước 2: Kiểm tra Bluetooth trên board (UART)

```sh
dmesg -n 1                                    # giảm log kernel cho UART đỡ rối
hciconfig -a                                  # có hci0, trạng thái UP; ghi lại "BD Address" = <ADDR>
systemctl is-active bt-daemon
which bt-daemon-cli.sh                # script này nằm trong thư mục test/ của vendor, image khách có thể KHÔNG có
bt-daemon-cli.sh get-paired-devices   # tối đa 5 bond; đủ 5 thì: bt-daemon-cli.sh remove <addr>
```

- Nếu image **không có** `bt-daemon-cli.sh`: pair bằng `bluetoothctl` (`agent NoInputNoOutput`, `default-agent`, `pairable on`, `discoverable on`), hoặc gọi D-Bus của bt-daemon trực tiếp.
- **Phải chốt được cách pair trước khi sang bước 4.**

**Xong khi** có `hci0` UP, biết `<ADDR>`, biết cách bật pairable/discoverable.

## Bước 3: Chuẩn bị Client PC

1. Bật OpenSSH Server trên Client PC (Settings → Optional features → OpenSSH Server).
2. Chép `ctt-agent-console.exe` sang Client PC.
3. Cắm dongle Bluetooth vào Client PC. Dongle phải do Windows quản lý.
4. Từ Local PC:
   ```
   ssh <user>@<client-pc> ctt-agent-console.exe --version
   ```

**Xong khi** lệnh trên in ra `ctt-agent-console 0.1.0`. Đây là TC-01.

## Bước 4: Pair (TC-04, TC-05)

| # | UART (board) | SSH (Client PC) |
|---|---|---|
| 1 | `bt-daemon-cli.sh set-pairable true` | |
| 2 | `bt-daemon-cli.sh set-discoverable true` | |
| 3 | | `ctt-agent-console.exe scan` → thấy `<ADDR>` |
| 4 | | `ctt-agent-console.exe pair <ADDR>` |
| 5 | `bt-daemon-cli.sh confirm-pairing`, chạy ngay khi PC đang pair | |
| 6 | | `ctt-agent-console.exe paired` → có `<ADDR>` |

**Xong khi** PC báo `Pairing successful`, và mã 6 số (nếu có) trùng với mã board in ra.

## Bước 5: Gửi/nhận dữ liệu thô (TC-06 → TC-09)

Mục đích là nhìn tận mắt dữ liệu đi đúng cả hai chiều.

**UART:**
```sh
/tmp/ctt-agent pipe -t rfcomm -e /22
```

**Client PC** (SSH): chạy `ctt-agent-console.exe`, vào menu **1** (Settings), trả lời `SSH to the board?` = `n`, nhập `<ADDR>` và channel `22`, rồi vào menu **9** (Terminal: send / receive data).

| Thử | Làm | Mong đợi |
|---|---|---|
| Kết nối | Mở menu 9 | Console: `connected <ADDR> channel 22.` UART: `{"event":"connected",...}` |
| PC → board | Gõ `hello board` rồi Enter | UART: `{"event":"rx",...,"hex":"68656c6c6f20626f6172640a"}` |
| Board → PC | Trên UART gõ `6869205043` rồi Enter | Console: `[PC rx] b'hi PC'` |
| Nhị phân | `/hex 00 ff 55 aa 0d 0a` | UART: `"hex":"00ff55aa0d0a"`, đúng 6 byte |

Thoát bằng `/quit` (console) và Ctrl+C (UART).

## Bước 6: Đo throughput / latency có ngưỡng Mbit/s (TC-10 → TC-14)

**UART:** dừng `pipe` nếu đang chạy, rồi:
```sh
/tmp/ctt-agent server -t rfcomm -e /22 -i 1      # hoặc: systemctl start ctt-agent-bt
```

**Client PC** (SSH): chạy cả bộ BT-TP-01…06 với ngưỡng **target** (mục tiêu sản phẩm), mất khoảng 3 phút:
```
ctt-agent-console.exe suite --addr <ADDR> --channel 22 -l target
```

| Case | Đo | Ngưỡng `target` |
|---|---|---|
| BT-TP-01 | PC → board, block 990, 30 s | ≥ 1.0 Mbit/s |
| BT-TP-02 | board → PC, block 990, 30 s | ≥ 1.0 Mbit/s |
| BT-TP-03 | hai chiều, block 990, 30 s | mỗi chiều ≥ 0.5 Mbit/s |
| BT-TP-04 | echo, block 64, 10 s | RTT p99 ≤ 100 ms |
| BT-TP-05 | PC → board, block 4096, 30 s | ≥ 1.0 Mbit/s |
| BT-TP-06 | board → PC, block 4096, 30 s | ≥ 1.0 Mbit/s |

Mọi case cũng phải có mất gói 0, lỗi CRC 0. Giải thích ngưỡng và level `baseline`: [bt-test-cases.md](bt-test-cases.md#d-throughput-và-latency-có-ngưỡng-mbits).

Chạy riêng từng case:
```
ctt-agent-console.exe suite --addr <ADDR> --channel 22 -l target --only BT-TP-01
ctt-agent-console.exe run   --addr <ADDR> --channel 22 -m tx -d 30 --min-up 1.0 --json > tx.json
```

**Xong khi** bảng tổng kết in `6/6 PASS` (exit code 0). Báo cáo nằm ở `ctt-results/suite-target-*.json` và `.csv` trên Client PC; chép về bằng `scp <user>@<client-pc>:ctt-results/* .`. Ghi số vào bảng ở cuối [bt-test-cases.md](bt-test-cases.md).

Nếu case nào FAIL vì không đạt ngưỡng (ví dụ `PC->board 0.420 >= 1 Mbit/s`):
- ghi lại con số đo được;
- chạy `btmon` trên board trong lúc đo để xem loại gói ACL (2-DH5/3-DH5 hay DH1) và sniff mode;
- so với level `baseline` (`-l baseline`) để biết board khách kém hơn hay tương đương setup Local Test.

Muốn đo kỹ hơn thì chạy tiếp:
- TC-14: thêm block 2048 / 8192 (`run -b …`), mỗi ô 3 lần × 30 s;
- TC-15: dừng giữa chừng;
- TC-16: `bidir` 600 s;
- TC-17: mất và khôi phục kết nối;
- TC-18: bỏ pair rồi pair lại.

---

## Cần ghi lại

- Image của board khách, commit của tool, driver dongle trên Client PC.
- Khoảng cách, vật cản, WiFi 2.4 GHz xung quanh.
- Các file `*.json`, log UART, và `btmon` trên board nếu có.

## Xử lý sự cố

| Hiện tượng | Nguyên nhân / cách xử lý |
|---|---|
| `ctt-agent: not found` / `Exec format error` | Chép sai file, hoặc chưa `chmod +x`. Phải là bản `build/dut` (aarch64), không phải `build/host` |
| `RFCOMM socket: … (kernel built without CONFIG_BT_RFCOMM?)` | Kernel không có RFCOMM: xem [yocto-recipe.md](yocto-recipe.md) bước 5 |
| `rfcomm listen …/22: Address already in use` | Channel 22 đang bận (có thể `ctt-agent-bt.service` đang chạy). `systemctl stop ctt-agent-bt`, hoặc thử `-e /23` và dùng `--channel 23` ở PC |
| Scan không thấy board | Chưa `set-discoverable true`, hoặc `hci0` đang DOWN |
| Pair lỗi `CapacityReached` | Đủ 5 bond: `bt-daemon-cli.sh remove <addr>` |
| `RFCOMM connect … [WinError 10060/10064]` | Chưa pair, sai channel, hoặc chưa chạy `ctt-agent server` / `pipe` trên board |
| `bad CTP magic` khi `run` | Board đang chạy `pipe` chứ không phải `server` |
| SSH vào Client PC chạy `.exe` không ra gì | Phải dùng `ctt-agent-console.exe`. `ctt-bt.exe` là app cửa sổ, chạy qua SSH sẽ không hiện |
