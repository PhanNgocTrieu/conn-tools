# CTT: Connectivity Test Tool

Bộ công cụ đo **throughput, latency và độ toàn vẹn dữ liệu** của Bluetooth, WiFi, USB và MFi trên board **Vendor board** (Linux Yocto).

## Thành phần

| Thành phần | Chạy ở đâu | Dùng để làm gì |
|---|---|---|
| `ctt-bt.exe` | PC Windows (hoặc `python3 -m ctt.btapp` trong WSL) | App **cửa sổ** test Bluetooth: pair, đo throughput có biểu đồ, terminal gửi/nhận dữ liệu |
| `ctt-agent-console.exe` | PC Windows, kể cả khi vào máy đó qua SSH | Giống `ctt-bt` nhưng **chỉ dùng console**: có menu và các lệnh con |
| `ctt-agent` | Board (aarch64) và PC Linux | Đầu đo (C++), dùng giao thức chung [CTP](docs/ctp-spec.md) |
| `ctt` | WSL / Linux | CLI test mọi giao tiếp (WiFi, BT, USB, MFi), xuất report HTML/JUnit |

## Bắt đầu từ đâu?

| Bạn muốn… | Đọc |
|---|---|
| Test Bluetooth PC ↔ board bằng app (GUI hoặc console) | [docs/bt-app.md](docs/bt-app.md) |
| Biết nên test Bluetooth theo cách nào với setup hiện tại | [docs/bt-test-plan.md](docs/bt-test-plan.md) |
| Chạy từng test case Bluetooth, từ kết nối đến gửi dữ liệu | [docs/bt-test-cases.md](docs/bt-test-cases.md) |
| Test WiFi / USB / MFi bằng CLI `ctt` | [docs/TESTING.md](docs/TESTING.md) |
| Cài môi trường WSL | [docs/wsl-setup.md](docs/wsl-setup.md) |

## Test Bluetooth nhanh (Windows)

1. Tải `ctt-bt.exe` và `ctt-agent-console.exe` từ GitHub Actions (artifact `ctt-bt-windows`) hoặc từ trang Releases.
2. Cắm USB Bluetooth dongle vào PC. Dongle phải do Windows quản lý, không gắn vào WSL.
3. Mở `ctt-bt.exe`, rồi ở tab **Kết nối**:
   - nhập SSH của board → **Kiểm tra board**;
   - nếu báo chưa có agent → **Deploy agent**;
   - **Scan** → chọn board → **Pair**.
4. Tab **Throughput**: chọn chiều đo → **Bắt đầu đo**. Kết quả PASS khi `mất 0 · CRC 0`.
5. Tab **Terminal**: **Kết nối**, gõ ở khung PC (PC gửi sang board) và khung Board (board gửi về PC).

Không có cửa sổ (đang SSH vào PC): chạy `ctt-agent-console.exe` rồi làm theo menu, xem [docs/bt-app.md](docs/bt-app.md#console).

## Môi trường phát triển (WSL / Linux)

```bash
scripts/setup-wsl.sh --install     # cài gói (lần đầu, cần sudo)
scripts/build-host.sh              # build ctt-agent cho PC và chạy toàn bộ test
python3 -m ctt selftest            # tự kiểm tra tool qua TCP loopback, không cần board
scripts/build-dut.sh --generic --static --strip   # build ctt-agent cho board (aarch64)
```

### Build file `.exe` trên máy (không cần push)

Script tự cài những gì còn thiếu trên Windows:
- **Python 3.12**: cài bằng `winget`, chỉ cho user hiện tại, không cần quyền admin. Lệnh `python` có sẵn trong WindowsApps chỉ là lối tắt tới Store nên không tính. Không muốn tự cài: thêm `--no-python-install` (WSL) hoặc `-NoPythonInstall` (PowerShell).
- **PyInstaller và `winrt`**: cài vào venv riêng `.venv-win`.

Kết quả nằm ở `dist/`.

| Muốn build | Từ WSL | Từ PowerShell (ở thư mục repo) |
|---|---|---|
| Chỉ `ctt-agent-console.exe` | `scripts/build-windows.sh console` | `.\scripts\build-console.ps1` |
| Chỉ `ctt-bt.exe` | `scripts/build-windows.sh gui` | `.\scripts\build-btapp.ps1 -Only gui` |
| Cả hai | `scripts/build-windows.sh` | `.\scripts\build-btapp.ps1` |

- Muốn app deploy được agent lên board thì phải nhúng agent aarch64 vào: build agent trước bằng `scripts/build-dut.sh --generic --static --strip`.
  - Bản WSL tự lấy `build/dut/agent/ctt-agent`.
  - Bản PowerShell: thêm `-Agent <đường dẫn>`.
- Bản WSL copy mã nguồn sang `%LOCALAPPDATA%\ctt-build` rồi build ở đó, vì PyInstaller chạy không ổn định trên đường dẫn `\\wsl.localhost`.
- GitHub Actions cũng build cả 3 file ([.github/workflows/btapp.yml](.github/workflows/btapp.yml)).

### Chạy app console `ctt-agent-console`

| Chạy từ | Lệnh |
|---|---|
| PowerShell / cmd (ở thư mục repo) | `.\dist\ctt-agent-console.exe` |
| WSL, gọi bản `.exe` của Windows (dùng dongle do Windows quản lý) | `dist/ctt-agent-console.exe` |
| Vào Client PC qua SSH (setup *Client Test*) | `ssh <user>@<client-pc>` rồi `ctt-agent-console.exe` |
| WSL / Linux, không cần `.exe` (dùng BlueZ, dongle phải gắn vào WSL) | `python3 -m ctt.btapp console` |

Chạy không có tham số sẽ mở **menu số** (1–9, 0 để thoát). Lệnh con dùng trong script:

```bat
ctt-agent-console.exe --version
ctt-agent-console.exe scan                                                  :: tìm thiết bị BT
ctt-agent-console.exe paired                                                :: thiết bị đã pair
ctt-agent-console.exe pair <ADDR> --board root@<board-ip>                   :: pair, board tự chấp nhận qua SSH
ctt-agent-console.exe run --board root@<board-ip> -m bidir -d 20            :: đo throughput (có SSH)
ctt-agent-console.exe run --addr <ADDR> --channel 22 -m tx -d 30 --json     :: đo khi không SSH (Client Test)
ctt-agent-console.exe term --board root@<board-ip>                          :: terminal gửi/nhận dữ liệu
ctt-agent-console.exe unpair <ADDR>
```

`run` trả exit code 0 khi PASS và 1 khi FAIL. Trong WSL, thay `ctt-agent-console.exe` bằng `python3 -m ctt.btapp`.

App cửa sổ: `.\dist\ctt-bt.exe` (Windows) hoặc `python3 -m ctt.btapp` (WSL, cần `python3-tk`).

Chi tiết từng mục menu: [docs/bt-app.md](docs/bt-app.md#console).

## CLI `ctt` (WiFi / BT / USB / MFi)

```bash
export CTT_DUT=root@<board-ip>
python3 -m ctt deploy                         # copy agent lên board
python3 -m ctt dut-info                       # kiểm kê phần cứng + daemon vendor trên board
python3 -m ctt run -t tcp -m bidir -d 30s     # một test
python3 -m ctt profile profiles/wifi_tcp_throughput.yaml
python3 -m ctt fw status                      # usb-daemon / bt-daemon / wifi-daemon
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
  firmware/             daemon vendor trên board (usb/bt/wifi-daemon)
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
| Bluetooth RFCOMM / L2CAP, pairing (`ctt bt`, `ctt-bt`, `ctt-agent-console`) | ✅ đã test trên board với setup Local Test; Client Test chưa chạy |
| MFi Auth CP (I2C) | ✅ đã test với bus giả lập; chờ register map |
| USB bulk gadget / storage | ✅ storage đã test; gadget chờ board |
| Yocto layer `meta-ctt` | ✅ build aarch64; recipe chờ chạy trong BSP |
