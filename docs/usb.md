# USB testing (Phase 5)

Cách test phụ thuộc vào vai trò của cổng USB trên board. Xem bằng `ctt dut-info` (mục `usb`: "UDC" và `usb_role`) hoặc `ctt usb gadget status --dut ...`:

| `/sys/class/udc` trên DUT | Board có thể là | Test |
|---|---|---|
| có ít nhất một UDC | **device** (gadget) | Bulk throughput DUT ↔ PC (mục 1) và NCM/TCP (mục 2) |
| trống | chỉ **host** | Throughput USB storage (mục 3), danh sách và tốc độ thiết bị |

Cổng OTG có thể đổi vai trò qua `/sys/class/usb_role/*/role` (`host` / `device`), tuỳ BSP.

**Cổng dual-role có daemon của sản phẩm giữ:** khai báo trong board profile `usb.gadget_stop_services` (daemon cần dừng khi chạy gadget) và `usb.role_file` (file sysfs đổi role, ví dụ `…/dwc2_drdmode` của controller dwc2). `ctt usb gadget up` dừng daemon, chuyển cổng sang device; `down` trả lại như cũ. `usb-daemon` giữ cổng ở chế độ host, và UDC **chỉ xuất hiện khi role là `device`**, nên `/sys/class/udc` trống là bình thường. `ctt usb gadget up` tự làm các bước: dừng `usb-daemon`, ghi `device`, chờ UDC, dựng gadget CTT. `ctt usb gadget down` gỡ gadget, ghi lại `host` và start lại `usb-daemon`. Những gì đã thay đổi được lưu ở `/run/ctt-usb-gadget`. Dùng `--keep-fw` nếu muốn lệnh báo lỗi thay vì dừng `usb-daemon`.

## 1. Bulk throughput: board làm gadget

```
PC (WSL)  ctt-agent client -t usb  ── usbdevfs ── cáp USB ── UDC ── FunctionFS ── ctt-agent server -t usb  (DUT)
```

```bash
ctt usb gadget up --dut root@<ip>            # configfs gadget 1d6b:0104 + FunctionFS tại /dev/ffs-ctt
# Cắm cáp USB từ cổng device của DUT vào PC. Trên Windows (PowerShell):
#   .\scripts\usbipd-attach.ps1 -HardwareId 1d6b:0104 -AutoAttach
ctt usb list --side host                      # trong WSL phải thấy 1d6b:0104
ctt run -t usb -m bidir -d 20s --dut root@<ip>
ctt profile profiles/usb_bulk.yaml --dut root@<ip>
ctt usb gadget down --dut root@<ip>
```

Cơ chế:
- Agent trên DUT ghi descriptor (một interface vendor-specific, gồm bulk IN 0x81 và bulk OUT 0x02, cho FS/HS/SS) vào FunctionFS, sau đó tự bind UDC (`-o udc=auto`).
- Agent trên PC dùng usbdevfs (không cần libusb). Nó tìm thiết bị theo VID:PID trong sysfs, claim interface, rồi truyền bulk theo khối 256 KiB.
- `link` trong `result.json` ghi lại `usb_speed` (high = 480M, super = 5G) và max packet size.

Quyền truy cập: `scripts/setup-wsl.sh --install` cài udev rule cho `1d6b:0104`. Nếu thiếu rule này, client cần quyền root.

> **WSL:** gói USB đi qua USB/IP (mạng ảo), nên throughput bị giới hạn khoảng 30–40 MB/s. Số sign-off phải đo bằng PC Linux native, cắm trực tiếp.

## 2. Mạng qua USB (NCM)

```bash
ctt usb gadget up --ncm --dut root@<ip>       # thêm interface usb0 trên DUT
# Gán IP cho usb0 trên DUT và cho card mạng tương ứng phía PC, sau đó:
ctt run -t tcp -e <ip-usb0-của-DUT>:5201 -m bidir -d 20s --dut root@<ip>
ctt run -t udp -e <ip-usb0-của-DUT>:5201 -r 200M -m tx -d 20s --dut root@<ip>
```

## 3. USB storage: board làm host

```bash
ctt usb list --dut root@<ip>                  # thiết bị cắm vào DUT và tốc độ đàm phán (480M / 5000M)
ctt usb storage --dut root@<ip> --path /run/media/sda1/ctt.bin --size 512M \
    --expect write_MBps_min=20 --expect read_MBps_min=30
```

- Đo ghi rồi đọc tuần tự bằng `O_DIRECT` (không qua page cache). Nếu filesystem không hỗ trợ `O_DIRECT`, tool chuyển sang buffered + fsync và ghi chú `[buffered]`.
- Mỗi block 1 MiB mang số thứ tự và CRC, nên lúc đọc lại tool phát hiện được dữ liệu hỏng (`verify_errors`).
- `--size` nên lớn hơn cache của thiết bị (thường ≥ 512M) để có số đo bền vững.
- Đường dẫn phải nằm trên phân vùng của thiết bị USB. File test sẽ bị xoá sau khi chạy.

## Đã kiểm chứng / chưa kiểm chứng

| Thành phần | Trạng thái |
|---|---|
| Descriptor FunctionFS (layout byte), parse config descriptor, tìm thiết bị qua sysfs | Unit test |
| `ctt-agent storage` (O_DIRECT, verify, phát hiện dữ liệu hỏng) | Test trên file thật |
| I/O FunctionFS / usbdevfs, `dut-usb-gadget.sh` trên board | **Chưa chạy trên phần cứng.** Kernel WSL không có gadget/dummy_hcd. Cần chạy lần đầu trên board thật |
