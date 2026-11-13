# App test Bluetooth: `ctt-agent` (GUI) và `ctt-agent-console` (console)

Hai app dùng chung một lõi nghiệp vụ và một file cấu hình, chỉ khác cách hiển thị. Giao diện của app bằng **tiếng Anh**; tài liệu này ghi tên nút và menu đúng như trên app.

| | `ctt-agent.exe` | `ctt-agent-console.exe` |
|---|---|---|
| Giao diện | Cửa sổ 3 tab, có biểu đồ | Menu số trong console, cùng các lệnh con |
| Dùng khi | Ngồi trực tiếp trước PC | Vào PC qua SSH, hoặc cần chạy bằng script |
| Trong WSL / Linux | `python3 -m ctt.btapp` | `python3 -m ctt.btapp console` |

Cấu hình (SSH board, có dùng SSH hay không, địa chỉ BT, channel, block, thời gian) được lưu ở `%APPDATA%\ctt-bt\config.json` (Linux: `~/.config/ctt-bt/config.json`). Hai app đọc chung file này. Thư mục vẫn tên `ctt-bt` (tên cũ của app) để giữ cấu hình đã lưu.

## 1. Hai cách nối PC với board

| Chế độ | Khi nào | App làm gì trên board |
|---|---|---|
| **Có SSH** (mặc định) | PC vào được board qua LAN (setup *Local Test*) | Tự kiểm tra board, deploy agent, chạy server RFCOMM, tự chấp nhận pairing, chạy `ctt-agent pipe` cho terminal hai chiều |
| **Không SSH** | Board chỉ có UART (setup *Client Test*) | Không làm gì trên board. Người test tự chạy agent trên board qua UART (hoặc agent chạy sẵn bằng service, xem [yocto-recipe.md](yocto-recipe.md)); app nối vào channel cố định (mặc định 22) |

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
bluetoothctl                 # một phiên tương tác, để mở trong suốt lúc pair
power on
agent NoInputNoOutput        # hoặc KeyboardDisplay nếu muốn thấy/so mã 6 số
default-agent
pairable on
discoverable on
# khi PC pair: "[agent] Confirm passkey 123456 (yes/no):" -> gõ yes
```

Nếu Bluetooth của board do một daemon riêng của sản phẩm quản lý (nó là agent mặc định của BlueZ), hãy dùng công cụ của daemon đó. Có thể khai báo các lệnh ấy trong board profile (`bluetooth.pairing`, `commands`) để app tự chạy khi có SSH, hoặc để gọi bằng `ctt board cmd <tên>`. Xem [configs/board.example.yaml](../configs/board.example.yaml).

## 2. GUI: `ctt-agent.exe`

### Tab `1. Connect`
| Vùng | Nút / ô | Tác dụng |
|---|---|---|
| Board (SSH) | **SSH**, **Check board** | Hiện hostname, agent, địa chỉ BT, các service trong board profile, số bond, tên board profile |
| | **Deploy agent** | Copy agent aarch64 đóng gói sẵn trong `.exe` lên `/tmp/ctt-agent` |
| | **No SSH (agent already runs as a service, channel 22)** | Chuyển sang chế độ không SSH |
| PC Bluetooth (USB dongle) | **Scan**, **Paired devices** | Tìm thiết bị bằng dongle của PC |
| | **Pair**, **Unpair** | Pair hoặc bỏ pair với địa chỉ đang chọn. Tick **Board accepts the pairing (over SSH)** thì board tự xác nhận. Mã numeric comparison hiện trong **Log** (`Passkey (numeric comparison): …`) |
| Target | **Board BT address**, **RFCOMM channel** | `auto` = hỏi board qua SSH. Không SSH: nhập số channel (22) |

### Tab `2. Throughput`
- **Direction**: `PC → board` (`tx`), `board → PC` (`rx`), `both ways` (`bidir`), `echo / RTT` (`echo`).
- **Parameters**:
  - **Duration (s)**;
  - **Block (byte)**: 990 ≈ một gói RFCOMM;
  - **Rate limit Mbit/s**: để trống là không giới hạn;
  - **Min Mbit/s (PASS)**: ngưỡng Mbit/s tối thiểu cho mỗi chiều đo được; để trống là không đặt ngưỡng;
  - **Verify CRC of every block**.
- **Start / Stop**: biểu đồ và Log cập nhật mỗi 1 s (đã gửi / đã nhận bao nhiêu). Bấm Stop thì vẫn nhận được kết quả.
- **Result**: Mbit/s từng chiều, RTT, `lost` và `CRC errors`.
  - **PASS** khi không mất gói, không lỗi CRC, đúng thứ tự, và đạt **Min Mbit/s** (nếu có nhập).
  - Khi FAIL, Log ghi rõ điều kiện không đạt, ví dụ `FAILED: PC->board 0.320 >= 1 Mbit/s`.
- **Save results (CSV + JSON)…**: lưu mọi lần đo trong phiên. **Clear history**: xoá lịch sử.

Bộ test case có ngưỡng Mbit/s (BT-TP-01…06) chạy bằng console (`suite`, menu 10), xem mục 3.

### Tab `3. Terminal`
- **Connect**: có SSH thì app chạy `ctt-agent pipe` trên board rồi nối RFCOMM tới đó. Không SSH thì nối vào channel đã nhập.
- **Khung PC**: gõ rồi Enter (hoặc **Send PC → board**). Dòng `PC → board` là đã gửi, `board → PC` là đã nhận.
- **Khung Board (over SSH)** (chỉ khi có SSH):
  - `board rx` là các byte board nhận được;
  - gõ vào khung này rồi bấm **Board sends → PC** để board gửi về PC (dòng `board tx`).
- **Format**: `Text` hoặc `Hex`. **Line end**: `(none)` / `\n` / `\r\n`.

## 3. Console: `ctt-agent-console.exe` <a id="console"></a>

Chạy không có tham số sẽ mở menu:

```
PC Bluetooth: Windows (WinRT): OK

=== CTT Bluetooth console 0.1.0 ===
Link: rfcomm | Board: SSH root@192.168.1.10 | BT: AA:BB:CC:DD:EE:01 | channel: auto
  1) Settings (board SSH / no SSH / BT address / channel)
  2) Check board (SSH)
  3) Deploy agent to the board (SSH)
  4) Scan Bluetooth
  5) Paired devices
  6) Pair with the board
  7) Unpair
  8) Throughput / latency test
  9) Terminal: send / receive data
  10) Test suite with Mbit/s expectations (BT-TP-01..06)
  0) Exit
Choice:
```

- Mỗi câu hỏi có giá trị mặc định trong `[...]`; nhấn Enter để giữ.
- **1** (Settings): kiểm tra giá trị nhập và hỏi lại nếu sai. SSH phải có dạng `user@ip`, địa chỉ có dạng `AA:BB:CC:DD:EE:FF`, channel là `auto` hoặc 1–30.
- **4 / 5** (Scan, Paired devices) cho phép chọn ngay một thiết bị làm board (`Use one of them as the board?`).
- **8** (đo): Ctrl+C dừng sớm, kết quả vẫn được in ra.
- **9** (terminal):
  - gõ dòng rồi Enter: PC gửi sang board;
  - `!abc`: board gửi `abc` về PC (cần SSH);
  - `/hex 48 65`: gửi byte dạng hex;
  - `/quit`: thoát.

  Dữ liệu nhận hiện dạng `[PC rx] b'...'` (PC nhận) và `[board rx] b'...'` (board nhận, cần SSH).
- **10** (test suite): chọn level `baseline` / `target`, chọn case (trống = tất cả). Kết quả lưu ở `ctt-results/suite-<level>-<thời điểm>.json` và `.csv`. Ngưỡng của từng case: [bt-test-cases.md](bt-test-cases.md#level-chỉ-đo-hoặc-có-ngưỡng-pass).

### Lệnh con (dùng trong script / CI)

```bat
ctt-agent-console scan
ctt-agent-console paired
ctt-agent-console pair AA:BB:CC:DD:EE:01 --board root@192.168.1.10
ctt-agent-console run --board root@192.168.1.10 -m bidir -d 20          :: tự deploy agent nếu thiếu
ctt-agent-console run --addr AA:BB:CC:DD:EE:01 --channel 22 -m tx -d 30   :: không SSH
ctt-agent-console run ... -m tx --min-up 1.0                               :: FAIL nếu PC->board < 1.0 Mbit/s
ctt-agent-console run ... -m echo -b 64 --max-rtt-p99 100                  :: FAIL nếu RTT p99 > 100 ms
ctt-agent-console run ... --json > result.json                              :: kết quả JSON (có "checks", "verdict")
ctt-agent-console suite --list -l target                                    :: xem các case và ngưỡng
ctt-agent-console suite --addr AA:BB:CC:DD:EE:01 --channel 22               :: cả bộ BT-TP-01..06, chỉ đo (level measure)
ctt-agent-console suite --board root@192.168.1.10 -l baseline             :: chạy cả bộ, chấm theo ngưỡng baseline
ctt-agent-console suite --addr AA:BB:CC:DD:EE:01 --channel 22 -l target --only BT-TP-01,BT-TP-02
ctt-agent-console suite --dump > my-suite.json                              :: sửa ngưỡng rồi chạy với --file my-suite.json
ctt-agent-console suite --addr AA:BB:CC:DD:EE:01 --channel 22 -q           :: không in trace mỗi giây
ctt-agent-console caps                                                      :: link nào PC này chạy được (RFCOMM / BLE GATT / BLE CoC)
ctt-agent-console ble-scan                                                  :: tìm board đang chạy ctt-agent server -t gatt
ctt-agent-console suite --addr AA:BB:CC:DD:EE:01 --link ble-gatt            :: bộ BLE-TP-01..06 qua BLE GATT
ctt-agent-console run --addr AA:BB:CC:DD:EE:01 --link ble-gatt -m rx -d 30 -b 244
ctt-agent-console term --board root@192.168.1.10
ctt-agent-console unpair AA:BB:CC:DD:EE:01
```

Trong lúc chạy, `run` và `suite` in mỗi giây một dòng: tốc độ và tổng đã gửi / đã nhận của PC, kèm dòng `[board] …` của agent trên board (khi có SSH). Cách đọc: [bt-client-test.md](bt-client-test.md#theo-dõi-trong-lúc-chạy-trace-mỗi-giây).

**Link** (`--link`, menu 1, hoặc mục **Link** ở tab Connect): `rfcomm` (mặc định), `ble-gatt`, `ble-coc`. Mỗi PC chỉ cho chọn link nó hỗ trợ; xem [bt-ble.md](bt-ble.md).

**Level của `suite`**: mặc định `measure` (chỉ đo; cột kết quả `OK` / `DATA ERR`), hoặc `baseline` / `target` (PASS/FAIL theo ngưỡng). `run` trả exit code 0 khi PASS, 1 khi FAIL. `suite` ở level `measure` trả 0 khi mọi case chạy được; ở `baseline` / `target` trả 0 khi mọi case PASS. `ctt-agent.exe` nhận đúng các lệnh con này (ví dụ `ctt-agent.exe run ...`). Trong WSL dùng `python3 -m ctt.btapp <lệnh>`.

## 4. Cài đặt

**Windows**: không cần cài gì; chép 2 file `.exe` là chạy. Muốn SSH tới board thì cần SSH key mà board chấp nhận: lệnh `ssh root@<board>` phải chạy được trong PowerShell mà không hỏi mật khẩu.

**Vào PC Windows qua SSH** (setup *Client Test*): bật OpenSSH Server trên PC đó, SSH vào rồi chạy `ctt-agent-console.exe`. Đừng chạy `ctt-agent.exe` qua SSH, vì cửa sổ sẽ không hiện ra. Console luôn in UTF-8, nên đọc được đúng chữ qua SSH.

**WSL / Linux**: `sudo apt install python3-tk bluez`. Gắn dongle vào WSL bằng `scripts/usbipd-attach.ps1`. Nếu sau đó `hciconfig -a` vẫn trống, kiểm tra `lsusb` có thấy dongle không, module `btusb` đã nạp chưa và đã có firmware (`linux-firmware`) chưa; xem [wsl-setup.md](wsl-setup.md).

## 5. Cách đo

- Dữ liệu dùng giao thức CTP ([ctp-spec.md](ctp-spec.md)), giống `ctt run -t rfcomm`.
- Throughput lấy ở **bên nhận**: chiều PC → board do board đo, chiều board → PC do PC đo. Đường biểu đồ của chiều PC → board là lượng dữ liệu PC đẩy vào socket.
- Echo đo RTT ứng dụng: gửi một gói, chờ board trả lại, rồi mới gửi gói tiếp.

## 6. Xử lý sự cố

| Hiện tượng | Nguyên nhân / cách xử lý |
|---|---|
| `RFCOMM connect …: [WinError 10060/10064]` | Chưa pair, board ngoài tầm, hoặc sai channel. Có SSH thì để channel `auto` |
| `bad CTP magic` | Channel đó không phải `ctt-agent server` (ví dụ `bt_throughput_server`, hoặc board đang chạy `pipe`). Dùng Terminal |
| Pair lỗi vì board đã đủ số bond | Xóa bond cũ: `ctt board unpair <addr> --dut root@<board-ip>` hoặc trên board `bluetoothctl remove <addr>` (giới hạn đặt trong board profile: `bluetooth.max_bonds`) |
| Scan không thấy board | Board chưa discoverable: Pair có tick "Board accepts the pairing", hoặc bật discoverable trên board qua UART |
| WSL: "no Bluetooth controller" | Dongle chưa gắn vào WSL (usbipd), `btusb` chưa nạp, hoặc thiếu firmware của dongle |
| `ctt-agent is not on the board` | Tab Connect → **Deploy agent** (console: menu 3), hoặc dùng lệnh `run` / `suite` (tự deploy) |
| `FAIL` dù `lost 0 · CRC 0` | Không đạt ngưỡng Mbit/s hoặc RTT. Dòng `[FAIL] …` (console) hoặc `FAILED: …` (Log) cho biết ngưỡng nào |
