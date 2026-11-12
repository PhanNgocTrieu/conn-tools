# Test case Bluetooth: từ kết nối đến gửi dữ liệu

Mỗi test case có:
- mục tiêu và điều kiện trước;
- các bước trên **GUI** (`ctt-bt.exe`) và trên **console** (`ctt-agent-console.exe`, số trong ngoặc là mục menu);
- phần làm thêm cho **Client Test (CT)**;
- kết quả mong đợi.

Chiến lược và tiêu chí: [bt-test-plan.md](bt-test-plan.md). Cách dùng app: [bt-app.md](bt-app.md). Board khách hàng, từng bước: [bt-client-test.md](bt-client-test.md).

Giao diện app bằng tiếng Anh. Tên nút, menu và chuỗi kết quả trong tài liệu này ghi **đúng như app hiển thị**.

Quy ước:
- **LT** = Local Test: có SSH tới board, board dùng USB dongle.
- **CT** = Client Test: board chỉ có UART, dùng chip BT trên board, người test SSH vào Client PC.
- `<BOARD>` = SSH của board (ví dụ `root@192.168.1.10`). `<ADDR>` = địa chỉ BT của board. `<CH>` = RFCOMM channel (CT: 22).
- `<LINK>` = `--board <BOARD>` (LT) hoặc `--addr <ADDR> --channel 22` (CT).
- Ghi kết quả của từng TC vào bảng ở cuối file.

---

## A. Chuẩn bị

### TC-01: Bluetooth trên PC sẵn sàng
| | |
|---|---|
| Mục tiêu | App thấy được adapter Bluetooth của PC |
| Trước | Dongle đã cắm, do Windows quản lý |
| GUI | Mở `ctt-bt.exe`, xem dòng đầu trong **Log** và nhãn bên phải vùng "PC Bluetooth" của tab Connect |
| Console | Chạy `ctt-agent-console.exe`, xem dòng đầu tiên |
| Mong đợi | `PC Bluetooth: Windows (WinRT): OK`, không có cảnh báo `this Python has no RFCOMM sockets!` |

### TC-02: Kiểm tra board (LT)
| | |
|---|---|
| Mục tiêu | PC vào được board qua SSH; biết tình trạng agent, BT, bt-daemon |
| GUI | Connect → nhập `<BOARD>` → **Check board** |
| Console | (1) Settings: `SSH to the board?` = `y`, nhập `<BOARD>` → (2) Check board |
| CT | Thay bằng UART: `uname -a; ctt-agent version; hciconfig -a; systemctl is-active bt-daemon` |
| Mong đợi | Hiện hostname, `aarch64`, địa chỉ BT. Nếu agent là `MISSING` thì làm TC-03. Bond < 5 |

### TC-03: Deploy agent (LT)
| | |
|---|---|
| GUI | **Deploy agent** |
| Console | (3) Deploy agent to the board |
| CT | Agent phải có sẵn trong image ([yocto-recipe.md](yocto-recipe.md)), hoặc chép bằng USB stick vào `/tmp/ctt-agent` |
| Mong đợi | `Deploy OK: ctt-agent 0.1.0 (CTP v1, aarch64)` |

---

## B. Pairing

### TC-04: PC tìm thấy board
| | |
|---|---|
| Trước | Board discoverable. CT: bật bằng `bt-daemon-cli.sh set-discoverable true` |
| GUI | **Scan**, chờ 10–15 s |
| Console | (4) Scan Bluetooth → `Use one of them as the board?` = `y` → số thứ tự |
| Mong đợi | Board có trong danh sách với đúng `<ADDR>`. Chọn board thì ô địa chỉ được điền |

### TC-05: Pair PC ↔ board
| | |
|---|---|
| Trước | Board chưa pair với PC (nếu đã pair thì làm TC-18 trước) |
| GUI | Chọn board → tick **Board accepts the pairing (over SSH)** → **Pair** |
| Console | (6) Pair with the board → Enter (địa chỉ mặc định) → `Board accepts the pairing itself over SSH?` = `y` |
| CT | Trước khi pair, chạy trên UART: `bt-daemon-cli.sh set-pairable true`. Khi PC đang pair, chạy `bt-daemon-cli.sh confirm-pairing`. Không có SSH thì console không hỏi câu "accepts the pairing" |
| Mong đợi | GUI: `Pair <ADDR>: Pairing successful`. Console: `OK  Pairing successful`. Mã 6 số (`Passkey: …`, nếu có) **trùng** với mã board in ra. Board xuất hiện trong **Paired devices** |

### TC-18: Bỏ pair rồi pair lại
| | |
|---|---|
| GUI | **Unpair** → Yes → làm lại TC-05 |
| Console | (7) Unpair → (6) Pair with the board |
| Mong đợi | `removed`, sau đó pair lại thành công. Nhắc lại 5 lần đều OK |

---

## C. Kết nối và gửi dữ liệu thô (Terminal)

### TC-06: Mở kết nối RFCOMM
| | |
|---|---|
| Trước | TC-05 đã OK |
| GUI | Tab Terminal → **Connect** |
| Console | (9) Terminal: send / receive data |
| CT | Trên UART chạy `ctt-agent pipe -t rfcomm -e /22`. Trong app: (1) Settings → `SSH to the board?` = `n`, `<ADDR>`, channel `22` |
| Mong đợi | GUI: `Connected <ADDR> channel <CH>`; LT: khung Board hiện `— PC connected: …`. Console: `connected <ADDR> channel <CH>.` CT: UART in ra `{"event":"connected",...}` |

### TC-07: PC gửi chữ sang board
| | |
|---|---|
| GUI | Khung PC gõ `hello board` → Enter |
| Console | Gõ `hello board` → Enter |
| CT | Xem UART: dòng `{"event":"rx",...,"hex":"68656c6c6f20626f6172640a"}`. Đó là chuỗi `hello board\n` viết dạng hex |
| Mong đợi | LT, GUI: khung Board hiện `board rx [12 B] hello board\n`. LT, console: `[board rx] b'hello board\n'`. Số byte và nội dung đúng tuyệt đối |

### TC-08: Board gửi chữ về PC
| | |
|---|---|
| GUI | Khung Board gõ `hi PC` → **Board sends → PC** |
| Console | Gõ `!hi PC` → Enter |
| CT | Trên UART gõ chuỗi hex của nội dung rồi Enter: `6869205043` (`hi PC`) |
| Mong đợi | GUI: khung PC hiện `board → PC [6 B] hi PC\n`. Console: `[PC rx] b'hi PC\n'`. CT: `[PC rx] b'hi PC'` (gõ hex thì không có `\n`) |

### TC-09: Dữ liệu nhị phân (hex)
| | |
|---|---|
| GUI | Format = **Hex** → khung PC gõ `00 ff 55 aa 0d 0a` → Enter |
| Console | `/hex 00 ff 55 aa 0d 0a` |
| Mong đợi | Board nhận đúng 6 byte `00 ff 55 aa 0d 0a`, không bị đổi hay bỏ byte nào (kể cả `0d 0a`). CT: UART `"hex":"00ff55aa0d0a"` |

---

## D. Throughput và latency (có ngưỡng Mbit/s)

> **Trước mỗi lần đo:** đóng Terminal (TC-06) vì nó đang giữ kết nối. CT: dừng `ctt-agent pipe` trên UART và chạy `ctt-agent server -t rfcomm -e /22 -i 1` (hoặc `systemctl start ctt-agent-bt`).

### Ngưỡng PASS

Mọi lần đo **luôn** phải có `lost 0`, `CRC errors 0` và đúng thứ tự. Ngoài ra phải đạt ngưỡng của level đang chạy:

| Level | Ý nghĩa | Dùng khi |
|---|---|---|
| `baseline` | Mức sàn để phát hiện regression của **setup hiện tại** (PC Windows + USB dongle, Local Test). Bằng khoảng 75% số thấp nhất đã đo trong `report.md` (PC → board 0.32–0.44, board → PC 0.51–0.61 Mbit/s) | Mỗi build tool / firmware mới (Giai đoạn 1, 4) |
| `target` | **Mục tiêu sản phẩm** trên board khách (chip trên board, Client Test) | Chấp nhận sản phẩm (Giai đoạn 3) |

| Case | TC | Chiều | Block | Thời gian | `baseline` | `target` |
|---|---|---|---|---|---|---|
| BT-TP-01 | TC-10 | PC → board (`tx`) | 990 | 30 s | PC→board **≥ 0.25 Mbit/s** | **≥ 1.0 Mbit/s** |
| BT-TP-02 | TC-11 | board → PC (`rx`) | 990 | 30 s | board→PC **≥ 0.40 Mbit/s** | **≥ 1.0 Mbit/s** |
| BT-TP-03 | TC-12 | hai chiều (`bidir`) | 990 | 30 s | PC→board **≥ 0.15** và board→PC **≥ 0.25 Mbit/s** | mỗi chiều **≥ 0.5 Mbit/s** |
| BT-TP-04 | TC-13 | echo / RTT | 64 | 10 s | RTT p99 **≤ 250 ms** | RTT p99 **≤ 100 ms** |
| BT-TP-05 | TC-14 | PC → board (`tx`) | 4096 | 30 s | **≥ 0.25 Mbit/s** | **≥ 1.0 Mbit/s** |
| BT-TP-06 | TC-14 | board → PC (`rx`) | 4096 | 30 s | **≥ 0.40 Mbit/s** | **≥ 1.0 Mbit/s** |

Tham khảo trần lý thuyết của RFCOMM (goodput):

| Gói ACL | Trần |
|---|---|
| 3-DH5 (EDR 3 Mbps) | khoảng 2.1 Mbit/s |
| 2-DH5 (EDR 2 Mbps) | khoảng 1.4 Mbit/s |
| DH5 (basic rate) | khoảng 0.7 Mbit/s |

Mức `target` 1.0 Mbit/s cần link chạy EDR. Nếu đo ra dưới 0.7 Mbit/s thì kiểm tra bằng `btmon` xem link có đang dùng gói DH1/DM1 hoặc đang ở sniff mode không (xem plan, Giai đoạn 1).

Hai ngưỡng RTT (250 ms, 100 ms) **chưa có số đo thật**, nên chốt lại sau lần chạy đầu ở Giai đoạn 1. Muốn đổi ngưỡng:
1. `ctt-agent-console suite --dump > my-suite.json`;
2. sửa số trong file;
3. chạy `suite --file my-suite.json`.

### Chạy cả bộ một lần

```
ctt-agent-console suite <LINK> -l baseline          (LT, mỗi build)
ctt-agent-console suite <LINK> -l target            (CT, chấp nhận sản phẩm)
```

Hoặc dùng menu **(10) Test suite with Mbit/s expectations** → level → Enter (tất cả case).

Mong đợi: bảng tổng kết có dòng `6/6 PASS`, exit code 0. Báo cáo nằm ở `ctt-results/suite-<level>-<thời điểm>.json` và `.csv`. Case FAIL ghi rõ ngưỡng không đạt, ví dụ `PC->board 0.320 >= 1 Mbit/s`.

Ví dụ màn hình:

```
case      mode   block  PC->board  board->PC   RTT p99  verdict  failed checks
BT-TP-01  tx       990      0.412          -         -  PASS
BT-TP-02  rx       990          -      0.563         -  PASS
...
6/6 PASS
report: ctt-results/suite-baseline-20261112-153000.json (+ .csv)
```

### TC-10: PC → board (`tx`), BT-TP-01
| | |
|---|---|
| GUI | Tab Throughput → **PC → board**, Duration 30, Block 990, **Min Mbit/s (PASS)** = `0.25` (LT) / `1.0` (CT), CRC bật → **Start** |
| Console | (8) → `1` → `30` → `990` → `0` → `y`, rồi so số `PC -> board` với ngưỡng. Hoặc (10) → level → `BT-TP-01` |
| Script | `ctt-agent-console run <LINK> -m tx -d 30 --min-up 0.25` (CT: `--min-up 1.0`) |
| Mong đợi | `lost 0 · CRC errors 0`, PC→board **≥ 0.25 Mbit/s** (baseline) / **≥ 1.0 Mbit/s** (target) → **PASS**. Ghi lại Mbit/s |

### TC-11: board → PC (`rx`), BT-TP-02
| | |
|---|---|
| GUI | Như TC-10, chọn **board → PC**, Min Mbit/s = `0.40` (LT) / `1.0` (CT) |
| Script | `ctt-agent-console run <LINK> -m rx -d 30 --min-down 0.40` (CT: `--min-down 1.0`) |
| Mong đợi | board→PC **≥ 0.40 Mbit/s** (baseline) / **≥ 1.0 Mbit/s** (target), không mất gói → **PASS** |

### TC-12: Hai chiều cùng lúc (`bidir`), BT-TP-03
| | |
|---|---|
| GUI | Như TC-10, chọn **both ways**. Ô Min Mbit/s áp cho cả hai chiều: nhập `0.15` (LT) / `0.5` (CT), rồi tự so chiều board→PC với 0.25 (LT) |
| Script | `ctt-agent-console run <LINK> -m bidir -d 30 --min-up 0.15 --min-down 0.25` (CT: `--min-up 0.5 --min-down 0.5`) |
| Mong đợi | Đạt cả hai ngưỡng → **PASS**. Tổng hai chiều thường xấp xỉ số đo một chiều (do link dùng chung). Ghi cả hai con số |

### TC-13: Latency (`echo`), BT-TP-04
| | |
|---|---|
| GUI | **echo / RTT**, Duration 10, Block `64` → **Start**. So RTT p99 trong Log với ngưỡng |
| Console | (8) → `4` → `10` → `64` |
| Script | `ctt-agent-console run <LINK> -m echo -d 10 -b 64 --max-rtt-p99 250` (CT: `--max-rtt-p99 100`) |
| Mong đợi | RTT p99 **≤ 250 ms** (baseline) / **≤ 100 ms** (target) → **PASS**. Ghi lại min/avg/p99/max |

### TC-14: Ảnh hưởng của block size, BT-TP-05 / 06
| | |
|---|---|
| Bước | Chạy TC-10 và TC-11 với block 990 / 2048 / 4096 / 8192. **Mỗi ô 3 lần × 30 s**, ghi min/avg/max. Ngưỡng giữ nguyên như TC-10 / TC-11 |
| Script | `ctt-agent-console suite <LINK> -l baseline --only BT-TP-05,BT-TP-06` (block 4096). Block khác dùng `run ... -b 2048 --min-up …` |
| Mong đợi | Mọi lần đều PASS. Theo `report.md`, trên Windows block gần như không ảnh hưởng (chênh dưới 0.1 Mbit/s). Nếu block lớn **nhanh hơn rõ rệt** thì ghi lại, vì điều đó thay đổi kết luận về nút cổ chai |

---

## E. Ổn định và báo cáo

### TC-15: Dừng giữa chừng
| | |
|---|---|
| GUI | **Start** đo 60 s → sau ~10 s bấm **Stop** |
| Console | (8) đo 60 s → Ctrl+C |
| Mong đợi | Vẫn nhận được kết quả (Mbit/s của khoảng đã chạy), không treo, lần đo kế tiếp chạy bình thường |

### TC-16: Chạy dài
| | |
|---|---|
| Script | `ctt-agent-console run <LINK> -m bidir -d 600 --min-up 0.15 --min-down 0.25 --json > long.json` (CT: ngưỡng target) |
| Mong đợi | PASS. Không rớt kết nối. Throughput của 60 s cuối không thấp hơn 20% so với 60 s đầu (xem các dòng interval) |

### TC-17: Mất và khôi phục kết nối
| | |
|---|---|
| Bước | Đang đo 60 s → tắt BT của board (`hciconfig hci0 down`, hoặc mang ra xa) → bật lại → đo lần nữa |
| Mong đợi | Lần đo đang chạy kết thúc với lỗi rõ ràng (không treo app). Lần đo sau PASS (đạt ngưỡng TC-10) mà không phải khởi động lại app hay pair lại |

### TC-19: Lưu kết quả
| | |
|---|---|
| GUI | Throughput → **Save results (CSV + JSON)…** |
| Console | `run ... --json > ket-qua.json` cho từng lần đo, hoặc `suite` (tự lưu JSON + CSV vào `ctt-results/`) |
| Mong đợi | File CSV có một dòng cho mỗi lần đo. JSON có số liệu do board đếm (`detail.board_stats` của GUI, `board_stats` của `run --json`, `cases[].result.board_stats` của `suite`) và có `verdict` / `checks` |

---

## Bảng ghi kết quả

| TC | Setup (LT/CT) | App (GUI/console) | Ngày | Build board | Level | Kết quả (PASS/FAIL) | Số đo / ghi chú |
|---|---|---|---|---|---|---|---|
| TC-01 | | | | | – | | |
| TC-02 | | | | | – | | |
| TC-03 | | | | | – | | |
| TC-04 | | | | | – | | |
| TC-05 | | | | | – | | mã xác nhận: |
| TC-06 | | | | | – | | |
| TC-07 | | | | | – | | |
| TC-08 | | | | | – | | |
| TC-09 | | | | | – | | |
| TC-10 / BT-TP-01 | | | | | | | PC→board Mbit/s (ngưỡng 0.25 / 1.0): |
| TC-11 / BT-TP-02 | | | | | | | board→PC Mbit/s (ngưỡng 0.40 / 1.0): |
| TC-12 / BT-TP-03 | | | | | | | up / down (ngưỡng 0.15+0.25 / 0.5+0.5): |
| TC-13 / BT-TP-04 | | | | | | | RTT p99 ms (ngưỡng 250 / 100): |
| TC-14 / BT-TP-05, 06 | | | | | | | bảng block size: |
| TC-15 | | | | | – | | |
| TC-16 | | | | | | | đầu / cuối Mbit/s: |
| TC-17 | | | | | – | | |
| TC-18 | | | | | – | | |
| TC-19 | | | | | – | | |
