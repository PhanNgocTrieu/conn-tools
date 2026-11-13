# Cài đặt môi trường WSL2 cho CTT

## 1. Windows (PowerShell)

```powershell
winget install usbipd            # chia sẻ USB vào WSL (BT dongle, WiFi dongle, cổng USB của DUT)
wsl --update
```

Tuỳ chọn: chỉ cần khi **DUT phải kết nối ngược vào host** (ví dụ chạy `--server-side host`). Thêm vào `%USERPROFILE%\.wslconfig`:

```ini
[wsl2]
networkingMode=mirrored
```

## 2. WSL

```bash
scripts/dev/setup-wsl.sh             # kiểm tra, không thay đổi gì
scripts/dev/setup-wsl.sh --install   # cài package, bật systemd, load module, bật bluetooth.service
```

Nếu script vừa bật systemd thì chạy `wsl --shutdown` trong PowerShell rồi mở lại WSL.

## 3. Gắn thiết bị USB vào WSL

```powershell
.\scripts\dev\usbipd-attach.ps1                                  # liệt kê thiết bị
.\scripts\dev\usbipd-attach.ps1 -HardwareId 0bda:8771 -AutoAttach   # lần đầu cần chạy trong PowerShell Admin
```

Kiểm tra trong WSL: `lsusb`, `hciconfig -a` (BT), `iw dev` (WiFi).

Dongle khuyến nghị (driver đã có sẵn trong kernel WSL 6.18):

| Loại | Chipset | Module |
|---|---|---|
| BT | Realtek RTL8761B/BU, Intel AX200/AX210 (bản USB) | `btusb` (cần `linux-firmware`) |
| WiFi | MediaTek MT7612U, MT7921AU, MT7601U | `mt76x2u`, `mt7921u`, `mt7601u` |

## 4. Giới hạn của WSL cần nhớ

- USB đi qua USB/IP (mạng ảo), nên throughput USB bị giới hạn khoảng 30–40 MB/s và jitter cao hơn. Số đo USB để sign-off phải lấy trên máy Linux native.
- WSL không truy cập được BT và WiFi tích hợp của laptop. Phải dùng dongle USB.
- Ở chế độ NAT (mặc định), host kết nối ra DUT bình thường. Chỉ chiều DUT → WSL là bị chặn.

## 5. Kết nối tới DUT (board)

```bash
ssh-copy-id root@<dut-ip>          # hoặc thêm public key vào /home/root/.ssh/authorized_keys
scripts/ctt dut-info --dut root@<dut-ip>
```

`dut-info` liệt kê kernel, card mạng, BT (hciconfig, module rfcomm), cổng USB (UDC/role), các bus I2C và những tool có sẵn trong image. Gửi output này lại để chốt các câu hỏi còn mở trong PLAN.
