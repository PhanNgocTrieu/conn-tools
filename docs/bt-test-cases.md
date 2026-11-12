# Test case Bluetooth: từ kết nối đến gửi dữ liệu

Mỗi test case có: mục tiêu, điều kiện trước, các bước trên **GUI** (`ctt-bt.exe`) và **console** (`ctt-agent-console.exe`, số trong ngoặc là mục menu), phần làm thêm cho **Client Test (CT)**, và kết quả mong đợi. Chiến lược và tiêu chí: [bt-test-plan.md](bt-test-plan.md). Cách dùng app: [bt-app.md](bt-app.md).

Quy ước:
- **LT** = Local Test: có SSH tới board, board dùng USB dongle.
- **CT** = Client Test: board chỉ có UART, dùng chip BT trên board, người test SSH vào Client PC.
- `<BOARD>` = SSH của board (ví dụ `root@192.168.1.10`). `<ADDR>` = địa chỉ BT của board. `<CH>` = RFCOMM channel (CT: 22).
- Ghi kết quả của từng TC vào bảng ở cuối file.

---

## A. Chuẩn bị

### TC-01: Bluetooth trên PC sẵn sàng
| | |
|---|---|
| Mục tiêu | App thấy được adapter Bluetooth của PC |
| Trước | Dongle đã cắm, do Windows quản lý |
| GUI | Mở `ctt-bt.exe`, xem dòng đầu trong **Nhật ký** và nhãn bên phải tab Kết nối |
| Console | Chạy `ctt-agent-console.exe`, xem dòng đầu tiên |
| Mong đợi | `Bluetooth trên PC: Windows (WinRT): OK`, không có cảnh báo "không có socket RFCOMM" |

### TC-02: Kiểm tra board (LT)
| | |
|---|---|
| Mục tiêu | PC vào được board qua SSH; biết tình trạng agent, BT, bt-daemon |
| GUI | Kết nối → nhập `<BOARD>` → **Kiểm tra board** |
| Console | (1) Cài đặt: có SSH = `y`, nhập `<BOARD>` → (2) Kiểm tra board |
| CT | Thay bằng UART: `uname -a; ctt-agent version; hciconfig -a; systemctl is-active bt-daemon` |
| Mong đợi | Hiện hostname, `aarch64`, địa chỉ BT. Nếu agent "CHƯA CÓ" thì làm TC-03. Bond < 5 |

### TC-03: Deploy agent (LT)
| | |
|---|---|
| GUI | **Deploy agent** |
| Console | (3) Deploy agent |
| CT | Agent phải có sẵn trong image (`ctt-agent-bt.service`), hoặc copy bằng USB stick vào `/usr/bin/ctt-agent` |
| Mong đợi | `Deploy OK: ctt-agent 0.1.0 (CTP v1, aarch64)` |

---

## B. Pairing

### TC-04: PC tìm thấy board
| | |
|---|---|
| Trước | LT: tick "Board tự chấp nhận" chưa cần. Nếu board không discoverable: CT bật bằng `bt-daemon-cli.sh set-discoverable true` |
| GUI | **Scan**, chờ 10–15 s |
| Console | (4) Scan Bluetooth → chọn board (`y`, số thứ tự) |
| Mong đợi | Board có trong danh sách với đúng `<ADDR>`. Chọn board thì ô địa chỉ được điền |

### TC-05: Pair PC ↔ board
| | |
|---|---|
| Trước | Board chưa pair với PC (nếu đã pair thì làm TC-18 trước) |
| GUI | Chọn board → tick **Board tự chấp nhận pairing** → **Pair** |
| Console | (6) Pair với board → Enter (địa chỉ mặc định) → `y` (board tự chấp nhận) |
| CT | Trước khi pair, chạy trên UART: `bt-daemon-cli.sh set-pairable true`. Khi PC đang pair, chạy `bt-daemon-cli.sh confirm-pairing`. Ở console chọn `n` cho câu "board tự chấp nhận" (không có SSH thì câu này không hiện) |
| Mong đợi | `Pair <ADDR>: Pairing successful`. Mã xác nhận 6 số (nếu có) **trùng** với mã board in ra. Board xuất hiện trong "Thiết bị đã pair" |

### TC-18: Bỏ pair rồi pair lại
| | |
|---|---|
| GUI | **Bỏ pair** → xác nhận → làm lại TC-05 |
| Console | (7) Bỏ pair → (6) Pair |
| Mong đợi | `removed`, sau đó pair lại thành công. Nhắc lại 5 lần đều OK |

---

## C. Kết nối và gửi dữ liệu thô (Terminal)

### TC-06: Mở kết nối RFCOMM
| | |
|---|---|
| Trước | TC-05 đã OK |
| GUI | Tab Terminal → **Kết nối** |
| Console | (9) Terminal |
| CT | Trên UART chạy `ctt-agent pipe -t rfcomm -e /22`. Trong app: Cài đặt → không SSH, `<ADDR>`, channel `22` |
| Mong đợi | `Đã kết nối <ADDR> channel <CH>`. LT: khung Board hiện `PC đã kết nối`. CT: UART in ra `{"event":"connected",...}` |

### TC-07: PC gửi chữ sang board
| | |
|---|---|
| GUI | Khung PC gõ `hello board` → Enter |
| Console | Gõ `hello board` → Enter |
| CT | Xem UART: dòng `{"event":"rx",...,"hex":"68656c6c6f20626f6172640a"}`. Đó là chuỗi `hello board\n` viết dạng hex |
| Mong đợi | LT: khung Board hiện `board nhận [12 B] hello board\n`. Số byte và nội dung đúng tuyệt đối |

### TC-08: Board gửi chữ về PC
| | |
|---|---|
| GUI | Khung Board gõ `hi PC` → **Board gửi → PC** |
| Console | Gõ `!hi PC` → Enter |
| CT | Trên UART gõ chuỗi hex của nội dung rồi Enter: `6869205043` (`hi PC`) |
| Mong đợi | Khung PC hiện `board → PC [6 B] hi PC\n` (CT: `[5 B] hi PC`, vì gõ hex thì không có `\n`) |

### TC-09: Dữ liệu nhị phân (hex)
| | |
|---|---|
| GUI | Dạng = **Hex** → khung PC gõ `00 ff 55 aa 0d 0a` → Enter |
| Console | `/hex 00 ff 55 aa 0d 0a` |
| Mong đợi | Board nhận đúng 6 byte `00 ff 55 aa 0d 0a`, không bị đổi hay bỏ byte nào (kể cả `0d 0a`) |

---

## D. Throughput và latency

> Trước mỗi lần đo: đóng Terminal (TC-06) vì nó đang giữ kết nối. CT: dừng `ctt-agent pipe` trên UART và chạy `ctt-agent server -t rfcomm -e /22 -i 1` (hoặc `systemctl start ctt-agent-bt`).

### TC-10: PC → board (`tx`)
| | |
|---|---|
| GUI | Tab Throughput → **PC → board**, 30 s, block 990, CRC bật → **Bắt đầu đo** |
| Console | (8) → `1` → `30` → `990` → `0` → `y` |
| Script | `ctt-agent-console run --board <BOARD> -m tx -d 30` (CT: `--addr <ADDR> --channel 22`) |
| Mong đợi | Biểu đồ/dòng log tăng đều; kết quả `mất 0 · CRC 0` → **PASS**. Ghi lại Mbit/s |

### TC-11: board → PC (`rx`)
Như TC-10, chọn **board → PC** (console: `2`, script: `-m rx`). Mong đợi PASS, ghi Mbit/s.

### TC-12: Hai chiều cùng lúc (`bidir`)
Như TC-10, chọn **hai chiều** (console: `3`, `-m bidir`). Mong đợi PASS, ghi cả hai con số. Tổng hai chiều thường xấp xỉ số đo một chiều (do link dùng chung).

### TC-13: Latency (`echo`)
| | |
|---|---|
| Thiết lập | **echo / RTT**, 10 s, block `64` |
| Console | (8) → `4` → `10` → `64` |
| Mong đợi | In ra RTT min/avg/p95/p99/max và số gói. Ghi lại p99 |

### TC-14: Ảnh hưởng của block size
Chạy TC-10 và TC-11 với block 990 / 2048 / 4096 / 8192. **Mỗi ô 3 lần × 30 s**, ghi min/avg/max. Mong đợi: mọi lần đều PASS. Ghi lại xu hướng (xem plan, Giai đoạn 1).

---

## E. Ổn định và báo cáo

### TC-15: Dừng giữa chừng
| | |
|---|---|
| GUI | Bắt đầu đo 60 s → sau ~10 s bấm **Dừng** |
| Console | (8) đo 60 s → Ctrl+C |
| Mong đợi | Vẫn nhận được kết quả (Mbit/s của khoảng đã chạy), không treo, lần đo kế tiếp chạy bình thường |

### TC-16: Chạy dài
`bidir` 600 s (`ctt-agent-console run ... -m bidir -d 600 --json > long.json`). Mong đợi: PASS, không rớt kết nối, throughput cuối không thấp hơn 20% so với đầu.

### TC-17: Mất và khôi phục kết nối
| | |
|---|---|
| Bước | Đang đo 60 s → tắt BT của board (`hciconfig hci0 down`, hoặc mang ra xa) → bật lại → đo lần nữa |
| Mong đợi | Lần đo đang chạy kết thúc với lỗi rõ ràng (không treo app). Lần đo sau PASS mà không phải khởi động lại app hay pair lại |

### TC-19: Lưu kết quả
| | |
|---|---|
| GUI | Throughput → **Lưu kết quả (CSV + JSON)…** |
| Console | Dùng `run ... --json > ket-qua.json` cho từng lần đo |
| Mong đợi | File CSV có một dòng cho mỗi lần đo. JSON có số liệu do board đếm: `detail.board_stats` (GUI) hoặc `board_stats` (console `--json`) |

---

## Bảng ghi kết quả

| TC | Setup (LT/CT) | App (GUI/console) | Ngày | Build board | Kết quả (PASS/FAIL) | Số đo / ghi chú |
|---|---|---|---|---|---|---|
| TC-01 | | | | | | |
| TC-02 | | | | | | |
| TC-03 | | | | | | |
| TC-04 | | | | | | |
| TC-05 | | | | | | mã xác nhận: |
| TC-06 | | | | | | |
| TC-07 | | | | | | |
| TC-08 | | | | | | |
| TC-09 | | | | | | |
| TC-10 | | | | | | Mbit/s: |
| TC-11 | | | | | | Mbit/s: |
| TC-12 | | | | | | up / down: |
| TC-13 | | | | | | RTT p99 ms: |
| TC-14 | | | | | | bảng block size: |
| TC-15 | | | | | | |
| TC-16 | | | | | | |
| TC-17 | | | | | | |
| TC-18 | | | | | | |
| TC-19 | | | | | | |
