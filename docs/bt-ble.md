# Bluetooth Low Energy (BLE): thiết kế và cách chạy

CTT đo BLE bằng **cùng giao thức CTP** và cùng các mode như Bluetooth Classic: `tx` (PC → board), `rx` (board → PC), `bidir` (hai chiều) và `echo` (RTT). Nhờ vậy trace mỗi giây, suite, báo cáo JSON/CSV và level `measure` / `baseline` / `target` dùng y hệt cho mọi đường truyền. Thứ duy nhất thay đổi là **link** (đường truyền).

## 1. Ba link, nền tảng nào chạy được gì

| Link | Là gì | Board (`ctt-agent`) | PC Windows | PC Linux |
|---|---|---|---|---|
| `rfcomm` | Bluetooth Classic (BR/EDR), RFCOMM | `server -t rfcomm -e /22` | ✔ socket RFCOMM của Windows | ✔ socket BlueZ |
| `ble-gatt` | BLE, GATT: PC ghi (write without response), board gửi notification | `server -t gatt` | ✔ qua `bleak` → WinRT | ✔ qua `bleak` → BlueZ |
| `ble-coc` | BLE, L2CAP Credit-Based Channel (LE CoC) | `server -t l2cap -o type=le` | ✗ Windows không có API cho app | ✔ qua `ctt-agent pipe --binary` |

**Nguyên tắc: mỗi nền tảng chạy cái nó hỗ trợ.** App tự dò khả năng của PC:
- Console: `ctt-agent-console caps` hoặc menu **11**. Cột `no` có ghi lý do.
- GUI: ở tab **1. Connect**, link nào PC không hỗ trợ thì bị làm mờ, kèm lý do bên dưới.

```
link           what                           detail
rfcomm    yes  Bluetooth Classic RFCOMM       OK
ble-gatt  yes  BLE GATT (write / notify)      OK (bleak)
ble-coc   no   BLE L2CAP CoC                  win32: no L2CAP LE CoC API for applications (Linux only)
```

Nên dùng link nào:
- **`ble-gatt`**: đây là cách điện thoại và PC thật sự dùng BLE. Số đo phản ánh trải nghiệm thực tế (giới hạn bởi connection interval, số gói mỗi connection event, ATT MTU, data length extension).
- **`ble-coc`**: đo năng lực thô của BLE trên chip, ít overhead hơn GATT. Cần một PC Linux thật có dongle BLE (WSL không dùng được), hoặc một board thứ hai.

## 2. Kiến trúc

```
PC                                                      Board (Linux, BlueZ)
CtpClient (tx/rx/bidir/echo, trace, suite)              ctt-agent server  (cùng code session với RFCOMM)
   │ socket                                                │ Stream
   ├─ rfcomm   : socket RFCOMM ──────── RFCOMM ─────────── ├─ rfcomm transport
   ├─ ble-gatt : SocketBridge ⇄ bleak ── GATT ── bluetoothd ⇄ D-Bus ⇄ gatt transport (AcquireWrite / AcquireNotify fd)
   └─ ble-coc  : SocketBridge ⇄ ctt-agent pipe --binary ── L2CAP LE CoC ── l2cap transport (type=le)
```

- **Phía PC** ([ctt/protocol/links.py](../ctt/protocol/links.py)): `SocketBridge` biến một đường truyền theo gói thành một socket thường, nên `CtpClient` không phải sửa gì.
  - GATT: mỗi lần ghi tối đa ATT MTU − 3 byte.
  - LE CoC: Python không mở được socket L2CAP LE (không đặt được loại địa chỉ LE), nên app gọi `ctt-agent` của chính PC ở chế độ `pipe --binary`.
- **Phía board, GATT** ([agent/src/transports/gatt.cpp](../agent/src/transports/gatt.cpp)):
  - Agent tự nói chuyện D-Bus với `bluetoothd` ([agent/src/dbus.cpp](../agent/src/dbus.cpp)). Không dùng libdbus, nên agent vẫn là một file static.
  - Agent đăng ký **một GATT service riêng của CTT** và một advertisement. GATT service của các daemon khác trên board vẫn giữ nguyên, agent không đụng tới.
  - Đường dữ liệu dùng `AcquireWrite` / `AcquireNotify`: BlueZ trao cho agent một socket cho mỗi chiều. Dữ liệu không đi qua D-Bus, nên không làm chậm phép đo.

GATT service của CTT:

| | UUID | Thuộc tính | Dùng cho |
|---|---|---|---|
| Service | `a0c70001-8f5e-4d3a-9c2b-1f0e5d4c3b2a` | primary, có trong advertisement | PC tìm board bằng `ble-scan` |
| rx | `a0c70002-…` | write-without-response, write | PC → board |
| tx | `a0c70003-…` | notify | board → PC |
| info | `a0c70004-…` | read | `ctt-agent <version>` |

Thứ tự phía PC: kết nối → bật notify trên `tx` → ghi CTP vào `rx`. Agent chỉ bắt đầu phiên đo khi đã có đủ cả hai chiều.

## 3. Chạy

### Board (UART, không SSH)

```sh
/tmp/ctt-agent server -t gatt -i 1                 # BLE GATT (Windows hoặc Linux PC)
/tmp/ctt-agent server -t l2cap -o type=le -i 1     # BLE L2CAP CoC (Linux PC), PSM 0x0080
/tmp/ctt-agent pipe --text -t gatt                 # terminal dữ liệu thô qua BLE GATT
```

Hoặc dùng service có sẵn trong image: `systemctl start ctt-agent-ble`, rồi xem trace bằng `journalctl -f -u ctt-agent-ble`.

Lệnh `server -t gatt` in ra, ví dụ:
```
ctt-agent server listening on gatt hci0 service a0c70001-8f5e-4d3a-9c2b-1f0e5d4c3b2a
```
Khi có lỗi:
- `GATT: cannot register the CTT service with BlueZ: …`: `bluetoothd` chưa chạy, hoặc adapter chưa bật (`hciconfig hci0 up`).
- Thêm `-J` để xem `advertising` / `advertising_error`. Nếu controller hết slot quảng bá (daemon khác đang dùng) thì `advertising_error` có nội dung. PC vẫn kết nối được bằng địa chỉ, chỉ là `ble-scan` không thấy board.

### PC

| | Console | GUI |
|---|---|---|
| Chọn link | `--link ble-gatt` / `--link ble-coc`, hoặc menu **1** → `Link` | Tab **1. Connect** → **Link** |
| Tìm board | `ctt-agent-console ble-scan` (chỉ thiết bị có service CTT; `--all` để thấy tất cả), hoặc menu **4** khi link là BLE | **Scan** (khi link là BLE) |
| Đo | `run --addr <ADDR> --link ble-gatt -m tx -d 30 -b 244` | Tab **2. Throughput** như Classic |
| Cả bộ | `suite --addr <ADDR> --link ble-gatt` → BLE-TP-01…06 | – |
| Terminal | menu **9** khi link là BLE | Tab **3. Terminal** |
| Có SSH tới board | `--board root@<ip> --link ble-gatt`: app tự chạy `server -t gatt` trên board và in trace `[board] …` | như Classic |

`<ADDR>` là địa chỉ public của adapter board (`hciconfig -a` → `BD Address`). Khi chạy `server -t gatt -J`, agent cũng in địa chỉ này trong trường `bdaddr`.

**Pairing:** GATT service và LE CoC của CTT không yêu cầu mã hóa, nên **không cần pair** để đo. Nếu board bắt buộc bảo mật cho LE, thì pair như với Classic.

## 4. Bộ test BLE (BLE-TP-01…06)

Chạy với `suite --link ble-gatt` hoặc `--link ble-coc`. Cả hai link dùng chung bộ này:

| Case | Mode | Block | Thời gian | Ghi chú |
|---|---|---|---|---|
| BLE-TP-01 | `tx` PC → board | 244 | 30 s | 244 = một gói LE khi có data length extension (251 − 4 L2CAP − 3 ATT) |
| BLE-TP-02 | `rx` board → PC | 244 | 30 s | notification |
| BLE-TP-03 | `bidir` | 244 | 30 s | |
| BLE-TP-04 | `echo` | 20 | 10 s | RTT ≈ 1–2 connection interval |
| BLE-TP-05 | `tx` | 990 | 30 s | một frame CTP chia thành nhiều gói BLE |
| BLE-TP-06 | `rx` | 990 | 30 s | |

**Chưa có ngưỡng Mbit/s.** Level mặc định là `measure`: chỉ ghi số đo, kèm kiểm tra toàn vẹn dữ liệu (`OK` / `DATA ERR`). Khi đã có số thật, ghi ngưỡng vào `baseline` / `target` bằng `suite --dump --link ble-gatt > ble.json` → sửa → `suite --file ble.json`.

Tham khảo trần lý thuyết (goodput GATT, LE 1M PHY): khoảng 0.2–0.7 Mbit/s tùy connection interval và số gói mỗi event; LE 2M PHY lên tới khoảng 1.4 Mbit/s. Windows thường chọn connection interval 7.5–30 ms.

## 5. Kiểm tra đã làm (không cần radio)

[tests/test_ble_links.py](../tests/test_ble_links.py) dựng một **dbus-daemon riêng** và một BlueZ giả ([tests/fakes/fake_bluez.py](../tests/fakes/fake_bluez.py)), rồi chạy `ctt-agent server -t gatt` thật trên đó. Test kiểm tra:
- **Đăng ký:** agent đăng ký được service và advertisement (`LocalName`, UUID), và đọc được characteristic `info`.
- **Phiên đo:** BlueZ giả gọi `AcquireNotify` / `AcquireWrite` giống BlueZ thật (có truyền fd qua D-Bus). Cả 4 mode chạy qua đó với MTU 247, mỗi notification ≤ MTU − 3. Board nhận đúng số byte PC gửi, không mất, không lỗi CRC.
- **Đường LE CoC:** `ctt-agent pipe --binary` + `SocketBridge` chạy được, test qua TCP.

**Chưa thử với radio thật.** Lần chạy đầu trên board cần xác nhận:
1. BlueZ trên board chấp nhận `RegisterApplication` khi các daemon Bluetooth của sản phẩm đang chạy.
2. Advertisement đăng ký được, hoặc PC kết nối được bằng địa chỉ.
3. Windows đọc được MTU đã thương lượng.
