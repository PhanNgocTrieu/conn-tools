# Bluetooth testing (Phase 2)

## Sơ đồ

```
 WSL host                                          DUT (board, Yocto)
 ┌──────────────────────────────┐                  ┌──────────────────────────────┐
 │ ctt (Python)                 │ ── SSH ────────▶ │ ctt-agent server -t rfcomm   │
 │   bt: bluetoothctl (BlueZ)   │                  │   kernel RFCOMM / L2CAP      │
 │ ctt-agent client -t rfcomm   │ ═══ BT radio ══▶ │   (hoặc --dut-accept:        │
 │   USB BT dongle (usbipd)     │                  │    bluetoothctl agent)       │
 └──────────────────────────────┘                  └──────────────────────────────┘
```

Agent chỉ dùng **socket kernel** (`AF_BLUETOOTH`). Không cần `libbluetooth` khi build hay trên DUT, chỉ cần kernel có `CONFIG_BT_RFCOMM` / `CONFIG_BT` (L2CAP).

## Quy trình

```bash
# 0. Một lần: setup WSL + gắn dongle (xem wsl-setup.md)
scripts/setup-wsl.sh --install
#    PowerShell: .\scripts\usbipd-attach.ps1 -HardwareId <vid:pid> -AutoAttach
python3 -m ctt bt list                              # thấy controller của dongle

# 1. Đưa agent lên DUT và kiểm tra stack BT của DUT
python3 -m ctt deploy   --dut root@<dut-ip>
python3 -m ctt dut-info --dut root@<dut-ip>         # mục bluetooth / bt-kernel
python3 -m ctt bt list  --side dut --dut root@<dut-ip>

# 2. Pair host <-> DUT
python3 -m ctt bt pair --dut root@<dut-ip> --dut-accept
#   - không truyền địa chỉ: tự lấy địa chỉ controller của DUT qua SSH
#   - --dut-accept: chạy agent tự đồng ý trên DUT (khi DUT dùng BlueZ bluetoothd).
#     Bỏ cờ này nếu ứng dụng BT của sản phẩm tự xử lý việc pairing.

# 3. Throughput
python3 -m ctt run -t rfcomm -m bidir -d 30s --dut root@<dut-ip>
python3 -m ctt profile profiles/bt_rfcomm_throughput.yaml --dut root@<dut-ip>

# 4. Độ ổn định của pairing
python3 -m ctt bt pair-stress --dut root@<dut-ip> --dut-accept -n 50 --connect
```

## Transport

| Transport | Endpoint | Tuỳ chọn `-o` | Ghi chú |
|---|---|---|---|
| `rfcomm` | `BDADDR/CHANNEL` | `security=low\|medium\|high\|fips`, `adapter=hci0\|BDADDR` | Server với channel `0` (mặc định) để kernel tự cấp channel trống. `ctt` đọc channel đó từ sự kiện `listening` |
| `l2cap` | `BDADDR/PSM` | `type=bredr\|le\|le-random`, `imtu=`, `security=`, `adapter=` | `SOCK_SEQPACKET`. Agent tự cắt gói theo MTU. PSM mặc định là 0x1001 (BR/EDR) hoặc 0x0080 (LE) |
| `unix` | `@name` hoặc path | `type=seqpacket`, `mtu=` | Không cần radio, dùng để test/CI đường đi của gói (code giống L2CAP) |

Chạy tay, không qua Python:

```bash
# DUT
ctt-agent server -t rfcomm                    # in ra: listening on rfcomm <DUT-addr>/<channel>
# host
ctt-agent client -t rfcomm -e <DUT-addr>/<channel> -m bidir -d 30
```

## Đối chiếu với code RFCOMM hiện có của bạn

| Code của bạn | `ctt-agent` (agent/src/transports/rfcomm.cpp) |
|---|---|
| `struct ns_sockaddr_rc` tự định nghĩa | `SockaddrRc` với `static_assert(sizeof == 10)` và `offsetof(rc_channel) == 8`. **Không được pack**, vì `connect()` của kernel yêu cầu `addrlen >= sizeof(struct sockaddr_rc)` = 10 |
| channel cố định 1 | cấu hình được, hoặc dùng 0 (kernel tự chọn) rồi đọc lại bằng `getsockname()` |
| `SO_REUSEADDR` | bỏ, vì không có tác dụng với RFCOMM |
| không đặt security | `-o security=...` → `setsockopt(SOL_BLUETOOTH, BT_SECURITY)` |
| — | `SO_SNDBUF`/`SO_RCVBUF` (`--sndbuf/--rcvbuf`), timeout I/O, link info (hci handle, security, HCI byte counters) |

## Đọc kết quả

- `up`/`down` Mbps là **goodput đo ở bên nhận** (chỉ tính payload, đã trừ header CTP 32 B/frame).
- `link` trong `result.json` gồm `bt_security`, `hci_handle`, `acl_mtu`, `hci_byte_rx/tx` (bộ đếm của adapter). Với L2CAP còn có `l2cap_omtu/imtu`, `packets_tx/rx` và `bt_phy_mask`.
- Trần thực tế để so sánh: BR/EDR 3 Mbps EDR cho RFCOMM goodput khoảng 2.0–2.2 Mbps. Nếu xa mức này thì nên kiểm tra theo thứ tự: block size → security level → USB/IP overhead (thử dongle trên máy Linux native) → coexistence với WiFi 2.4 GHz.

## Xử lý sự cố

| Triệu chứng | Nguyên nhân thường gặp |
|---|---|
| `the server's Bluetooth address is unknown` | Agent trên DUT không thấy adapter qua HCI ioctl. Kiểm tra `hciconfig -a`, hoặc truyền `--bdaddr` |
| `No route to host` | Phía client không có adapter đang chạy (dongle chưa attach, `power off`) |
| `Connection refused` | Channel/PSM không có ai listen, hoặc server chưa sẵn sàng |
| `Permission denied` / `EACCES` | Pairing hoặc authentication thất bại so với `security=` yêu cầu. Pair lại |
| `channel used by another profile/app` | Channel đang bị HFP/SPP của sản phẩm chiếm. Dùng channel 0 |
| `bt pair` bị treo ở `Confirm passkey` | DUT không có agent: dùng `--dut-accept`, hoặc xác nhận trên UI của DUT |

## Chưa làm (các phase sau)

- Đăng ký SDP record cho agent. Hiện tại không cần, vì client Linux kết nối thẳng vào channel. Chỉ cần SDP khi peer là điện thoại.
- BLE GATT throughput (Write Without Response / Notify), PHY 2M, DLE.
- `raw` mode để đo với một server không nói CTP (ví dụ app SPP có sẵn của sản phẩm).
