# ctt-bt: app test throughput Bluetooth

App desktop (Windows `.exe` hoặc chạy trong WSL) để test đường Bluetooth giữa PC và board board:

1. **Kết nối**: tìm board, pair, kiểm tra board qua SSH, deploy `ctt-agent`.
2. **Throughput**: đo PC → board, board → PC, hai chiều, echo/RTT. Có biểu đồ Mbit/s theo thời gian thực, kiểm tra mất gói và CRC.
3. **Terminal**: PC gửi chữ/hex sang board và thấy board nhận được gì. Gõ ở khung "Board" thì board gửi ngược về PC.

```
 PC (Windows hoặc WSL)                                        board
 ┌──────────────────────────┐                                 ┌──────────────────────────────┐
 │ ctt-bt                   │── SSH (Ethernet) ──────────────▶│ ctt-agent server / pipe      │
 │  ├ pairing (WinRT/BlueZ) │                                 │ bt-daemon (xác nhận   │
 │  └ RFCOMM socket ────────│~~~~ Bluetooth (TP-Link UB) ~~~~▶│   pairing qua D-Bus)          │
 └──────────────────────────┘                                 └──────────────────────────────┘
```

SSH dùng để chạy agent trên board và xác nhận pairing ở phía board. Không có SSH thì bật `ctt-agent-bt.service` trên board (channel 22, xem `yocto/meta-ctt`), và trong app tick "Không dùng SSH".

## Chạy trên Windows

1. Cắm dongle TP-Link. Dongle phải thuộc về Windows: nếu trước đó đã gắn vào WSL bằng usbipd thì chạy `usbipd detach --busid <id>`.
2. Mở `ctt-bt.exe`. Lấy file này từ GitHub Actions (artifact `ctt-bt-windows`) hoặc từ trang Releases khi có tag `v*`. Bản `.exe` có sẵn agent aarch64 bên trong.
3. Lần đầu dùng SSH: cần có SSH key mà board chấp nhận (`ssh root@<board>` chạy được trong PowerShell mà không phải gõ mật khẩu).

Tự build: cài Python 3.12 cho Windows, rồi chạy `.\scripts\build-btapp.ps1 -Agent <đường dẫn ctt-agent aarch64>`.

## Chạy trên WSL

```bash
sudo apt install python3-tk bluez          # hoặc scripts/setup-wsl.sh --install
# PowerShell (Admin): gắn dongle vào WSL
#   .\scripts\usbipd-attach.ps1 -HardwareId <vid:pid> -AutoAttach
python3 -m ctt.btapp                       # cửa sổ (WSLg)
```

Dòng lệnh (không cần cửa sổ):

```bash
python3 -m ctt.btapp scan
python3 -m ctt.btapp pair <board-addr> --board root@<board-ip>      # board tự xác nhận qua SSH
python3 -m ctt.btapp run --board root@<board-ip> -m bidir -d 20     # đo hai chiều 20 s
python3 -m ctt.btapp run --addr <board-addr> --channel 22 -m tx     # không SSH (service trên board)
python3 -m ctt.btapp term --board root@<board-ip>                   # Enter: PC gửi; "!abc": board gửi
```

## Quy trình test

| Bước | Trong app | Kết quả mong đợi |
|---|---|---|
| 1 | Kết nối → nhập SSH → **Kiểm tra board** | Hiện hostname, agent, địa chỉ BT, trạng thái bt-daemon và số bond |
| 2 | Nếu agent "CHƯA CÓ" → **Deploy agent** | `Deploy OK: ctt-agent 0.1.0 (… aarch64)` |
| 3 | **Scan** → chọn board → **Pair** (tick "Board tự chấp nhận") | Nhật ký: `Pair …: Pairing successful`. Mã numeric comparison được ghi lại |
| 4 | Throughput → chọn chiều → **Bắt đầu đo** | Biểu đồ chạy; kết quả `mất 0 · CRC 0` → PASS |
| 5 | Terminal → **Kết nối** → gõ ở khung PC và khung Board | Khung Board hiện "board nhận …"; khung PC hiện "board → PC …" |
| 6 | **Lưu kết quả** | File CSV và JSON để đính kèm báo cáo |

## Cách đo

- Dữ liệu dùng giao thức CTP (xem [ctp-spec.md](ctp-spec.md)), giống hệt `ctt run -t rfcomm`. Throughput lấy ở **bên nhận**: PC → board do board đo, board → PC do PC đo. Con số trên biểu đồ của chiều PC → board là lượng dữ liệu PC đẩy vào socket.
- Block mặc định là 990 byte, vừa một gói RFCOMM. Muốn đo throughput tối đa của link thì tăng block, ví dụ 4096.
- Echo đo RTT ứng dụng: mỗi lần gửi một gói và chờ board trả lại.

## Xử lý sự cố

| Hiện tượng | Nguyên nhân / cách xử lý |
|---|---|
| `RFCOMM connect …: [WinError 10060/10064]` | Chưa pair, board không trong tầm, hoặc channel sai. Dùng "auto" khi có SSH |
| `bad CTP magic` | Channel đang nối không phải `ctt-agent server` (ví dụ `bt_throughput_server`). Dùng tab Terminal |
| Pair lỗi `CapacityReached` / thất bại liên tục | bt-daemon đã giữ đủ 5 bond: chạy `ctt fw bt-unpair <addr>` |
| WSL: "no Bluetooth controller" | Dongle chưa được gắn vào WSL (usbipd), hoặc kernel WSL thiếu `btusb` |
| Windows: không thấy board khi scan | Board chưa discoverable: Pair với tick "Board tự chấp nhận" sẽ bật discoverable qua SSH |
