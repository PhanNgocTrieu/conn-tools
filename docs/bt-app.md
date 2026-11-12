# App test Bluetooth: `ctt-bt` (GUI) và `ctt-agent-console` (console)

Hai app dùng chung một lõi nghiệp vụ và một file cấu hình, chỉ khác cách hiển thị:

| | `ctt-bt.exe` | `ctt-agent-console.exe` |
|---|---|---|
| Giao diện | Cửa sổ 3 tab, có biểu đồ | Menu số trong console, cùng các lệnh con |
| Dùng khi | Ngồi trực tiếp trước PC | Vào PC qua SSH, hoặc cần chạy bằng script |
| Trong WSL / Linux | `python3 -m ctt.btapp` | `python3 -m ctt.btapp console` |

Cấu hình (SSH board, có dùng SSH hay không, địa chỉ BT, channel, block, thời gian) được lưu ở `%APPDATA%\ctt-bt\config.json` (Linux: `~/.config/ctt-bt/config.json`). Hai app đọc chung file này.

## 1. Hai cách nối PC với board

| Chế độ | Khi nào | App làm gì trên board |
|---|---|---|
| **Có SSH** (mặc định) | PC vào được board qua LAN (setup *Local Test*) | Tự kiểm tra board, deploy agent, chạy server RFCOMM, tự chấp nhận pairing, chạy `ctt-agent pipe` cho terminal hai chiều |
| **Không SSH** | Board chỉ có UART (setup *Client Test*) | Không làm gì trên board. Người test tự chạy agent trên board qua UART; app nối vào channel cố định (mặc định 22) |

Chi tiết về hai setup xem [bt-test-plan.md](bt-test-plan.md).

### Chuẩn bị board khi không có SSH

Chạy trên console UART của board:

```sh
# agent đã có trong image (yocto/meta-ctt) thì dùng service:
systemctl start ctt-agent-bt          # = ctt-agent server -t rfcomm -e /22
# hoặc chạy tay (thấy được log từng phiên đo):
ctt-agent server -t rfcomm -e /22 -i 1
# terminal hai chiều bằng tay: board gửi bằng cách gõ chuỗi HEX rồi Enter, dữ liệu nhận hiện thành JSON
ctt-agent pipe -t rfcomm -e /22
```

Chấp nhận pairing trên board:

```sh
# board có bt-daemon (image vendor):
bt-daemon-cli.sh set-pairable true
bt-daemon-cli.sh set-discoverable true
bt-daemon-cli.sh confirm-pairing        # chạy khi PC đang pair (numeric comparison)
# board chỉ có BlueZ:
bluetoothctl    # rồi gõ: agent NoInputNoOutput / default-agent / pairable on / discoverable on
```

## 2. GUI: `ctt-bt.exe`

### Tab 1: Kết nối
| Vùng | Nút / ô | Tác dụng |
|---|---|---|
| Board (SSH) | **SSH**, **Kiểm tra board** | Hiện hostname, agent, địa chỉ BT, trạng thái bt-daemon, số bond (tối đa 5) |
| | **Deploy agent** | Copy agent aarch64 đóng gói sẵn trong `.exe` lên `/tmp/ctt-agent` |
| | **Không dùng SSH** | Chuyển sang chế độ không SSH (channel mặc định 22) |
| Bluetooth trên PC | **Scan**, **Thiết bị đã pair** | Tìm thiết bị bằng dongle của PC |
| | **Pair**, **Bỏ pair** | Pair hoặc bỏ pair với địa chỉ đang chọn. Tick "Board tự chấp nhận" thì board xác nhận qua SSH. Mã numeric comparison được ghi vào nhật ký |
| Đích kết nối | **Địa chỉ BT**, **RFCOMM channel** | `auto` = hỏi board qua SSH. Không SSH: nhập số channel (22) |

### Tab 2: Throughput
- **Chiều đo**: PC → board (`tx`), board → PC (`rx`), hai chiều (`bidir`), echo/RTT (`echo`).
- **Thông số**: thời gian (giây), block (byte/gói, 990 ≈ một gói RFCOMM), giới hạn Mbit/s, kiểm tra CRC.
- **Bắt đầu đo / Dừng**: biểu đồ cập nhật mỗi 0.5 s. Bấm Dừng thì vẫn nhận được kết quả.
- **Kết quả**: Mbit/s từng chiều, RTT, số gói mất và lỗi CRC. PASS khi không mất gói và không lỗi CRC.
- **Lưu kết quả**: CSV và JSON của mọi lần đo trong phiên.

### Tab 3: Terminal
- **Kết nối**: có SSH thì app chạy `ctt-agent pipe` trên board rồi nối RFCOMM tới đó. Không SSH thì nối vào channel đã nhập.
- **Khung PC**: gõ rồi Enter để PC gửi. Bên dưới hiện `PC → board` (đã gửi) và `board → PC` (đã nhận).
- **Khung Board** (chỉ khi có SSH): hiện `board nhận` (đúng các byte PC gửi tới), gõ vào đây để board gửi về PC.
- **Dạng**: chữ hoặc hex. **Kết thúc dòng**: không / `\n` / `\r\n`.

## 3. Console: `ctt-agent-console.exe` <a id="console"></a>

Chạy không có tham số sẽ mở menu:

```
=== CTT Bluetooth console 0.1.0 ===
Board: SSH root@192.168.1.10 | BT: AA:BB:CC:DD:EE:01 | channel: auto
  1) Cài đặt (SSH board / không SSH / địa chỉ BT / channel)
  2) Kiểm tra board (SSH)
  3) Deploy agent lên board (SSH)
  4) Scan Bluetooth
  5) Thiết bị đã pair
  6) Pair với board
  7) Bỏ pair
  8) Đo throughput / latency
  9) Terminal: gửi / nhận dữ liệu
  0) Thoát
```

- Mỗi câu hỏi có giá trị mặc định trong `[...]`; nhấn Enter để giữ.
- **4 / 5** (Scan, Thiết bị đã pair) cho phép chọn ngay một thiết bị làm board.
- **8** (đo): Ctrl+C dừng sớm, kết quả vẫn được in ra.
- **9** (terminal):
  - gõ dòng rồi Enter: PC gửi sang board;
  - `!abc`: board gửi `abc` về PC (cần SSH);
  - `/hex 48 65`: gửi byte dạng hex;
  - `/quit`: thoát.

### Lệnh con (dùng trong script / CI)

```bat
ctt-agent-console scan
ctt-agent-console paired
ctt-agent-console pair AA:BB:CC:DD:EE:01 --board root@192.168.1.10
ctt-agent-console run --board root@192.168.1.10 -m bidir -d 20          :: tự deploy agent nếu thiếu
ctt-agent-console run --addr AA:BB:CC:DD:EE:01 --channel 22 -m tx -d 30   :: không SSH
ctt-agent-console run ... --json > result.json                              :: kết quả JSON
ctt-agent-console term --board root@192.168.1.10
ctt-agent-console unpair AA:BB:CC:DD:EE:01
```

`run` trả exit code 0 khi PASS và 1 khi FAIL. `ctt-bt.exe` nhận đúng các lệnh con này (ví dụ `ctt-bt run ...`). Trong WSL dùng `python3 -m ctt.btapp <lệnh>`.

## 4. Cài đặt

**Windows**: không cần cài gì; chép 2 file `.exe` là chạy. Muốn SSH tới board thì cần SSH key mà board chấp nhận: lệnh `ssh root@<board>` phải chạy được trong PowerShell mà không hỏi mật khẩu.

**Vào PC Windows qua SSH** (setup *Client Test*): bật OpenSSH Server trên PC đó, SSH vào rồi chạy `ctt-agent-console.exe`. Đừng chạy `ctt-bt.exe` qua SSH, vì cửa sổ sẽ không hiện ra.

**WSL / Linux**: `sudo apt install python3-tk bluez`. Gắn dongle vào WSL bằng `scripts/usbipd-attach.ps1`. Nếu sau đó `hciconfig -a` vẫn trống, kiểm tra `lsusb` có thấy dongle không, module `btusb` đã nạp chưa và đã có firmware (`linux-firmware`) chưa; xem [wsl-setup.md](wsl-setup.md).

## 5. Cách đo

- Dữ liệu dùng giao thức CTP ([ctp-spec.md](ctp-spec.md)), giống `ctt run -t rfcomm`.
- Throughput lấy ở **bên nhận**: chiều PC → board do board đo, chiều board → PC do PC đo. Đường biểu đồ của chiều PC → board là lượng dữ liệu PC đẩy vào socket.
- Echo đo RTT ứng dụng: gửi một gói, chờ board trả lại, rồi mới gửi gói tiếp.

## 6. Xử lý sự cố

| Hiện tượng | Nguyên nhân / cách xử lý |
|---|---|
| `RFCOMM connect …: [WinError 10060/10064]` | Chưa pair, board ngoài tầm, hoặc sai channel. Có SSH thì để channel `auto` |
| `bad CTP magic` | Channel đó không phải `ctt-agent server` (ví dụ `bt_throughput_server`). Dùng Terminal |
| Pair lỗi `CapacityReached` | bt-daemon đã đủ 5 bond: `ctt fw bt-unpair <addr>` hoặc `bt-daemon-cli.sh remove <addr>` |
| Scan không thấy board | Board chưa discoverable: Pair có tick "Board tự chấp nhận", hoặc bật discoverable trên board qua UART |
| WSL: "no Bluetooth controller" | Dongle chưa gắn vào WSL (usbipd), `btusb` chưa nạp, hoặc thiếu firmware của dongle |
| `board chưa có ctt-agent` | Kết nối → Deploy agent (menu 3), hoặc dùng lệnh `run` (tự deploy) |
