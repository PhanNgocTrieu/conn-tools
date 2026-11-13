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

Hoặc không build: tải `ctt-agent-aarch64` và `ctt-agent-console.exe` (thêm `ctt-agent.exe` nếu dùng GUI) từ GitHub Releases, xem [release.md](release.md).

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
systemctl is-active bluetooth                 # bluetoothd
bluetoothctl devices Paired                   # các bond đang có; xóa bớt: bluetoothctl remove <addr>
```

- Nếu Bluetooth của board do một daemon riêng của sản phẩm quản lý (nó là agent mặc định), hãy dùng công cụ của daemon đó để bật pairable/discoverable và xác nhận pairing, thay cho `bluetoothctl` ở bước 4.
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
| # | UART (board, trong `bluetoothctl`) | SSH (Client PC) |
|---|---|---|
| 1 | `power on`, `agent NoInputNoOutput`, `default-agent` | |
| 2 | `pairable on`, `discoverable on` | |
| 3 | | `ctt-agent-console.exe scan` → thấy `<ADDR>` |
| 4 | | `ctt-agent-console.exe pair <ADDR>` |
| 5 | Khi hiện `Confirm passkey … (yes/no)`: gõ `yes` | |
| 6 | | `ctt-agent-console.exe paired` → có `<ADDR>` |

**Xong khi** PC báo `Pairing successful`, và mã 6 số (nếu có) trùng với mã board in ra.

## Bước 5: Gửi/nhận dữ liệu thô (TC-06 → TC-09)

Mục đích là nhìn tận mắt dữ liệu đi đúng cả hai chiều.

**UART:**
```sh
/tmp/ctt-agent pipe --text -t rfcomm -e /22
```
`--text` cho người đọc: dòng gõ trên UART được gửi đi dạng chữ, `/hex 00 ff` gửi byte thô, và dữ liệu nhận được in cả chữ lẫn hex kèm tổng byte.

**Client PC** (SSH): chạy `ctt-agent-console.exe`, vào menu **1** (Settings), trả lời `SSH to the board?` = `n`, nhập `<ADDR>` và channel `22`, rồi vào menu **9** (Terminal: send / receive data).

| Thử | Làm | Mong đợi |
|---|---|---|
| Kết nối | Mở menu 9 | Console: `connected <ADDR> channel 22.` UART: `-- connected: …` |
| PC → board | Gõ `hello board` rồi Enter | Console: `[PC tx 12 B, total 12 B]`. UART: `board rx 12 B (total rx 12 B): "hello board\n"   hex 68 65 6c …` |
| Board → PC | Trên UART gõ `hi PC` rồi Enter | UART: `board tx 6 B (total tx 6 B): "hi PC\n"`. Console: `[PC rx 6 B, total 6 B] b'hi PC\n'` |
| Nhị phân | `/hex 00 ff 55 aa 0d 0a` | UART: `board rx 6 B (…): "\x00\xffU\xaa\r\n"   hex 00 ff 55 aa 0d 0a`, đúng 6 byte |

Bản đầy đủ (kể cả với GUI `ctt-agent.exe`): [bt-test-cases.md](bt-test-cases.md) mục 2.

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

### Theo dõi trong lúc chạy (trace mỗi giây)

Cả PC và board đều in **mỗi giây một dòng**: tốc độ của giây vừa qua, cộng **tổng đã gửi / đã nhận từ đầu** (byte và số frame). So hai bên là biết dữ liệu có tới đủ hay không. Ví dụ PC → board (`tx`), 3 s, giới hạn 0.4 Mbit/s:

PC (console):
```
     1.0s  PC->board  0.403 Mbit/s  sent 50.5 KB (51 fr)
     2.0s  PC->board  0.403 Mbit/s  sent 101.0 KB (102 fr)
     3.0s  PC->board  0.395 Mbit/s  sent 150.5 KB (152 fr)
PC -> board 0.403 Mbit/s   board -> PC - Mbit/s   lost 0  crc 0
data: PC sent 150.5 KB, board received 150.5 KB
```

Board (UART: chạy `server` bằng tay ở trên, hoặc `journalctl -f -u ctt-agent-bt` nếu dùng service):
```
session from <địa chỉ BT của PC>
  mode=tx block=990 duration=3.0s rate=400000 verify=on
  direction: client -> server (up): this side RECEIVES and counts the data
[up   rx]   0.00-  1.00 s      0.408 Mbps  +52 fr   51.5 KB | received 52 fr   51.5 KB  lost=0 crc_err=0
[up   rx]   1.00-  2.00 s      0.400 Mbps  +50 fr   49.5 KB | received 102 fr  101.0 KB  lost=0 crc_err=0
[up   rx]   2.00-  3.00 s      0.396 Mbps  +50 fr   49.5 KB | received 152 fr  150.5 KB  lost=0 crc_err=0
session done: received 150.5 KB (152 fr) in 3.0 s = 0.403 Mbps, lost=0 crc_err=0
```

| Mode | PC in | Board in |
|---|---|---|
| `tx` (PC → board) | `PC->board … sent X` | `[up   rx] … received X  lost= crc_err=` |
| `rx` (board → PC) | `board->PC … received X  lost crc` | `[down tx] … sent X` |
| `bidir` | cả hai, cách nhau bằng `\|` | xen kẽ `[up   rx]` (nhận) và `[down tx]` (gửi) |
| `echo` | `echo  rtt avg … replies N` | `[echo srv] … replied N` |

- Board ngừng in dòng mới trong khi PC vẫn tăng `sent`: dữ liệu bị nghẽn hoặc mất trên đường Bluetooth.
- `lost` / `crc_err` khác 0: hỏng dữ liệu, case FAIL dù tốc độ đạt.
- Có SSH thì không cần nhìn UART: console và tab Throughput in luôn các dòng `[board] … received …` lấy từ agent trên board.
- `suite -q` tắt trace, chỉ in kết quả từng case.

Mọi case cũng phải có mất gói 0, lỗi CRC 0. Giải thích ngưỡng và level `baseline`: [bt-test-cases.md](bt-test-cases.md#level-chỉ-đo-hoặc-có-ngưỡng-pass).

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
| Pair lỗi vì đủ số bond | Trên board: `bluetoothctl devices Paired`, rồi `bluetoothctl remove <addr>` |
| `RFCOMM connect … [WinError 10060/10064]` | Chưa pair, sai channel, hoặc chưa chạy `ctt-agent server` / `pipe` trên board |
| `bad CTP magic` khi `run` | Board đang chạy `pipe` chứ không phải `server` |
| SSH vào Client PC chạy `.exe` không ra gì | Phải dùng `ctt-agent-console.exe`. `ctt-agent.exe` là app cửa sổ, chạy qua SSH sẽ không hiện |
