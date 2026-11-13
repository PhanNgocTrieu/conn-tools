# CTT: Connectivity Test Tool

Bộ công cụ đo **throughput, latency và độ toàn vẹn dữ liệu** của Bluetooth, WiFi, USB và MFi trên **board Linux** (Yocto, Buildroot, Debian…; aarch64 hoặc x86_64), với PC Windows hoặc Linux.

## Thành phần

| Thành phần | Chạy ở đâu | Dùng để làm gì |
|---|---|---|
| `ctt-agent.exe` | PC Windows (hoặc `python3 -m ctt.btapp` trong WSL) | App **cửa sổ** test Bluetooth: pair, đo throughput có biểu đồ, terminal gửi/nhận dữ liệu. Giao diện tiếng Anh |
| `ctt-agent-console.exe` | PC Windows, kể cả khi vào máy đó qua SSH | Giống `ctt-agent.exe` nhưng **chỉ dùng console**: có menu và các lệnh con |
| `ctt-agent` | Board (aarch64) và PC Linux | Đầu đo (C++), dùng giao thức chung [CTP](docs/ctp-spec.md). Trên release có tên `ctt-agent-aarch64` (board) và `ctt-agent-x86_64` (Linux / WSL) |
| `ctt` | WSL / Linux | CLI test mọi giao tiếp (WiFi, BT, USB, MFi), xuất report HTML/JUnit |

## Chạy trên board nào?

Mọi board Linux có ngăn xếp chuẩn: systemd, BlueZ (bluetoothd), USB gadget qua configfs, WiFi nl80211. Agent trên board là một file static (`ctt-agent-aarch64`, hoặc tự build cho kiến trúc khác) và không cần thư viện nào.

Phần riêng của từng board/sản phẩm khai báo trong một **board profile** (YAML), không nằm trong code. Ví dụ: daemon Bluetooth/USB/WiFi riêng, cách board chấp nhận pairing, giới hạn số bond, lệnh trạng thái riêng.
- Mẫu: [configs/board.example.yaml](configs/board.example.yaml).
- Bản của dự án đặt ở `configs/board.private.yaml`, file này đã được gitignore. Có thể chỉ định file khác bằng `--board-profile FILE` hoặc biến `CTT_BOARD_PROFILE`.
- Không có profile thì CTT dùng BlueZ mặc định.
- Xem profile đang dùng: `python3 -m ctt board status --dut root@<board-ip>`.

## Bắt đầu từ đâu?

| Bạn muốn… | Đọc |
|---|---|
| Test Bluetooth PC ↔ board bằng app (GUI hoặc console) | [docs/bt-app.md](docs/bt-app.md) |
| Test **BLE** (GATT trên Windows / Linux, L2CAP CoC trên Linux) | [docs/bt-ble.md](docs/bt-ble.md) |
| Biết nên test Bluetooth theo cách nào với setup hiện tại | [docs/bt-test-plan.md](docs/bt-test-plan.md) |
| Chạy từng test case Bluetooth, từ kết nối đến gửi dữ liệu | [docs/bt-test-cases.md](docs/bt-test-cases.md) |
| Test Bluetooth trên **board khách hàng** (chỉ có UART), từng bước | [docs/bt-client-test.md](docs/bt-client-test.md) |
| Đưa `ctt-agent` vào image Yocto của board | [docs/yocto-recipe.md](docs/yocto-recipe.md) |
| Tải bản build sẵn, tạo release `vX.Y.Z` | [docs/release.md](docs/release.md) |
| Test WiFi / USB / MFi bằng CLI `ctt` | [docs/TESTING.md](docs/TESTING.md) |
| Cài môi trường WSL | [docs/wsl-setup.md](docs/wsl-setup.md) |

## Test Bluetooth nhanh (Windows)

1. Tải `ctt-agent.exe` và `ctt-agent-console.exe`:
   - bản chính thức: trang **Releases** → `vX.Y.Z`;
   - bản mới nhất của `main`: pre-release **`latest-main`** (GitHub Actions build lại mỗi lần push lên `main`);
   - mỗi release có `ctt-agent.exe`, `ctt-agent-console.exe`, `ctt-agent-aarch64` (board), `ctt-agent-x86_64` (Linux / WSL), `SHA256SUMS` và source. Xem [docs/release.md](docs/release.md);
   - hoặc tự build trên máy: `scripts/build-all.sh`.
2. Cắm USB Bluetooth dongle vào PC. Dongle phải do Windows quản lý, không gắn vào WSL.
3. Mở `ctt-agent.exe` (giao diện tiếng Anh), rồi ở tab **1. Connect**:
   - nhập SSH của board → **Check board**;
   - nếu agent là `MISSING` → **Deploy agent**;
   - **Scan** → chọn board → **Pair**.
4. Tab **2. Throughput**: chọn **Direction**, nhập **Min Mbit/s (PASS)** nếu cần → **Start**. Kết quả PASS khi `lost 0 · CRC errors 0` và đạt Min Mbit/s.
5. Tab **3. Terminal**: **Connect**, gõ ở khung PC (PC gửi sang board) và khung Board (board gửi về PC).
6. Bộ test case có ngưỡng Mbit/s (BT-TP-01…06): `ctt-agent-console.exe suite --board root@<board-ip> -l baseline`. Board khách chỉ có UART: làm theo [docs/bt-test-cases.md](docs/bt-test-cases.md) (board UART + console qua SSH, hoặc board UART + GUI).

Không có cửa sổ (đang SSH vào PC): chạy `ctt-agent-console.exe` rồi làm theo menu, xem [docs/bt-app.md](docs/bt-app.md#console).

## Môi trường phát triển (WSL / Linux)

```bash
scripts/setup-wsl.sh --install     # cài gói (lần đầu, cần sudo)
scripts/build-host.sh              # build ctt-agent cho PC và chạy toàn bộ test
python3 -m ctt selftest            # tự kiểm tra tool qua TCP loopback, không cần board
scripts/build-dut.sh --generic --static --strip   # build ctt-agent cho board (aarch64)
```

### Build tất cả bằng một lệnh

```bash
scripts/build-all.sh               # agent WSL (+ test), agent board, ctt-agent-console.exe, ctt-agent.exe
scripts/build-all.sh --no-test     # bỏ qua test của agent WSL cho nhanh
scripts/build-all.sh --no-windows  # không build .exe
```

Build xong, script in ra **báo cáo**, đồng thời lưu vào `dist/BUILD-REPORT.txt`. Báo cáo gồm:
- trạng thái OK / FAIL của từng bước, kèm log ở `build/logs/<bước>.log`;
- đường dẫn từng file kết quả (cả đường dẫn Windows), dung lượng, thời gian;
- lệnh để chạy từng file.

Mỗi file được gắn nhãn:
- `[new]`: vừa build xong;
- `[up to date]`: build lại nhưng không có gì thay đổi;
- `[OLD FILE …]`: bước build file đó vừa lỗi, file đang thấy là bản cũ.

Một bước lỗi không làm dừng các bước khác. Exit code là 0 chỉ khi mọi bước OK.

| File | Nằm ở |
|---|---|
| agent cho WSL/Linux | `build/host/agent/ctt-agent` |
| agent cho board | `build/dut/agent/ctt-agent` |
| app console | `dist/ctt-agent-console.exe` |
| app cửa sổ | `dist/ctt-agent.exe` |

### Build file `.exe` trên máy (không cần push)

Script tự cài những gì còn thiếu trên Windows:
- **Python 3.12**: cài bằng `winget`, chỉ cho user hiện tại, không cần quyền admin. Lệnh `python` có sẵn trong WindowsApps chỉ là lối tắt tới Store nên không tính. Không muốn tự cài: thêm `--no-python-install` (WSL) hoặc `-NoPythonInstall` (PowerShell).
- **PyInstaller và `winrt`**: cài vào venv riêng `.venv-win`.

Kết quả nằm ở `dist/`.

| Muốn build | Từ WSL | Từ PowerShell (ở thư mục repo) |
|---|---|---|
| Chỉ `ctt-agent-console.exe` | `scripts/build-windows.sh console` | `.\scripts\build-console.ps1` |
| Chỉ `ctt-agent.exe` | `scripts/build-windows.sh gui` | `.\scripts\build-btapp.ps1 -Only gui` |
| Cả hai | `scripts/build-windows.sh` | `.\scripts\build-btapp.ps1` |

- Muốn app deploy được agent lên board thì phải nhúng agent aarch64 vào: build agent trước bằng `scripts/build-dut.sh --generic --static --strip`.
  - Bản WSL tự lấy `build/dut/agent/ctt-agent`.
  - Bản PowerShell: thêm `-Agent <đường dẫn>`.
- Bản WSL copy mã nguồn sang `%LOCALAPPDATA%\ctt-build` rồi build ở đó, vì PyInstaller chạy không ổn định trên đường dẫn `\\wsl.localhost`.
- GitHub Actions cũng build cả 3 file ([.github/workflows/btapp.yml](.github/workflows/btapp.yml)):
  - mỗi lần chạy có artifact `ctt-agent-windows`, `ctt-agent-console-windows`, `ctt-agent-aarch64` và `ctt-agent-x86_64`;
  - push lên `main` cập nhật release `latest-main`;
  - tag `vX.Y.Z` tạo release chính thức, xem [docs/release.md](docs/release.md).

### Chạy app console `ctt-agent-console`

| Chạy từ | Lệnh |
|---|---|
| PowerShell / cmd (ở thư mục repo) | `.\dist\ctt-agent-console.exe` |
| WSL, gọi bản `.exe` của Windows (dùng dongle do Windows quản lý) | `dist/ctt-agent-console.exe` |
| Vào Client PC qua SSH (setup *Client Test*) | `ssh <user>@<client-pc>` rồi `ctt-agent-console.exe` |
| WSL / Linux, không cần `.exe` (dùng BlueZ, dongle phải gắn vào WSL) | `python3 -m ctt.btapp console` |

Chạy không có tham số sẽ mở **menu số** (1–10, 0 để thoát; giao diện tiếng Anh). Lệnh con dùng trong script:

```bat
ctt-agent-console.exe --version
ctt-agent-console.exe scan                                                  :: tìm thiết bị BT
ctt-agent-console.exe paired                                                :: thiết bị đã pair
ctt-agent-console.exe pair <ADDR> --board root@<board-ip>                   :: pair, board tự chấp nhận qua SSH
ctt-agent-console.exe run --board root@<board-ip> -m bidir -d 20            :: đo throughput (có SSH)
ctt-agent-console.exe run --addr <ADDR> --channel 22 -m tx -d 30 --json     :: đo khi không SSH (Client Test)
ctt-agent-console.exe run --board root@<board-ip> -m tx --min-up 1.0        :: FAIL nếu dưới 1.0 Mbit/s
ctt-agent-console.exe suite --board root@<board-ip> -l baseline             :: bộ BT-TP-01..06 có ngưỡng Mbit/s
ctt-agent-console.exe suite --addr <ADDR> --channel 22 -l target            :: như trên, board khách (không SSH)
ctt-agent-console.exe term --board root@<board-ip>                          :: terminal gửi/nhận dữ liệu
ctt-agent-console.exe unpair <ADDR>
```

`run` và `suite` trả exit code 0 khi PASS và 1 khi FAIL. `suite` lưu báo cáo JSON + CSV vào `ctt-results/`. Trong WSL, thay `ctt-agent-console.exe` bằng `python3 -m ctt.btapp`.

App cửa sổ: `.\dist\ctt-agent.exe` (Windows) hoặc `python3 -m ctt.btapp` (WSL, cần `python3-tk`).

Chi tiết từng mục menu: [docs/bt-app.md](docs/bt-app.md#console).

## CLI `ctt` (WiFi / BT / USB / MFi)

```bash
export CTT_DUT=root@<board-ip>
python3 -m ctt deploy                         # copy agent lên board
python3 -m ctt dut-info                       # kiểm kê phần cứng + trạng thái board
python3 -m ctt run -t tcp -m bidir -d 30s     # một test
python3 -m ctt profile profiles/wifi_tcp_throughput.yaml
python3 -m ctt board status                   # service trong board profile, BlueZ, USB role, WiFi
```

| `-t` | Giao tiếp | `-m` | Hướng (server chạy trên board) |
|---|---|---|---|
| `tcp`, `udp` | WiFi, Ethernet, USB-NCM | `tx` | PC → board |
| `rfcomm`, `l2cap` | Bluetooth | `rx` | board → PC |
| `usb` | USB bulk (gadget) | `bidir` | cả hai chiều |
| `unix` | test không cần phần cứng | `echo` | RTT |

Mọi test đều kiểm tra số thứ tự gói và CRC. Exit code 0 khi mọi test PASS, nên dùng thẳng được trong CI. Report nằm ở `out/<thời điểm>_<tên>/` (`report.html`, `junit.xml`, CSV/JSON). Chi tiết: [docs/TESTING.md](docs/TESTING.md).

## Cấu trúc mã nguồn

```
agent/                  ctt-agent (C++17): CTP, transports, server/client, pipe, i2c, storage
ctt/                    Python
  core/                 executor (local/SSH), chạy agent, đơn vị
  protocol/ctp.py       client CTP thuần Python (dùng cho app Windows)
  bluetooth/            bluetoothctl, pairing (WinRT/BlueZ), điều khiển board qua SSH
  platform/             board profile: phần riêng của từng board (configs/board.example.yaml)
  interfaces/           wifi, usb, mfi
  testing/              runner, profile YAML
  reporting/            ghi kết quả, in console, HTML/JUnit, so sánh baseline
  cli/                  CLI `ctt` (mỗi lệnh là một class trong cli/commands/)
  btapp/                app Bluetooth
    service.py          nghiệp vụ dùng chung cho GUI và console
    settings.py         cấu hình (lưu %APPDATA%\ctt-bt\config.json)
    gui/                cửa sổ Tkinter (mỗi tab một class)
    console/            menu console + lệnh con
packaging/              entry point PyInstaller cho 2 file .exe
profiles/  configs/     profile YAML, cấu hình MFi mẫu
scripts/                setup, build host/board/app, gadget USB, usbipd
yocto/meta-ctt/         recipe ctt-agent (+ service RFCOMM channel 22)
docs/                   tài liệu
tests/                  unit/integration test (python3 -m unittest discover -s tests)
```

## Trạng thái

| Phần | Trạng thái |
|---|---|
| TCP / UDP (WiFi, Ethernet, USB-NCM) | ✅ |
| Bluetooth RFCOMM / L2CAP, pairing (`ctt bt`, `ctt-agent`, `ctt-agent-console`) | ✅ đã test trên board với setup Local Test; Client Test chưa chạy |
| MFi Auth CP (I2C) | ✅ đã test với bus giả lập; chờ register map |
| USB bulk gadget / storage | ✅ storage đã test; gadget chờ board |
| Yocto layer `meta-ctt` | ✅ build aarch64; recipe chờ chạy trong BSP |
