# Test case Bluetooth: board (UART) ↔ PC client (console qua SSH / GUI)

Tài liệu này dùng cho **3 thành phần** test cùng nhau:

| Thành phần | Chạy ở đâu | Là gì |
|---|---|---|
| **Board terminal** | UART của board | `ctt-agent` (bản `ctt-agent-aarch64` trên release). Chạy `pipe --text` (gửi/nhận dữ liệu thô) hoặc `server` (đo throughput) |
| **PC remote terminal** | Client PC, SSH vào từ máy của bạn | `ctt-agent-console.exe`: menu và lệnh con, chạy được qua SSH |
| **PC GUI** | Client PC, ngồi trực tiếp hoặc Remote Desktop | `ctt-agent.exe`: app cửa sổ (tab Connect / Throughput / Terminal) |

Ghép thành **2 tổ hợp**. Mỗi test case ghi bước và kết quả mong đợi cho cả hai:

| Tổ hợp | Board | PC |
|---|---|---|
| **A** | Board terminal (UART) | PC remote terminal: `ctt-agent-console.exe` qua SSH |
| **B** | Board terminal (UART) | PC GUI: `ctt-agent.exe` |

PC **không SSH vào board**. Mọi thứ phía board đều làm bằng tay trên UART, và mọi số liệu phía board đọc trên UART.

Liên quan: chiến lược [bt-test-plan.md](bt-test-plan.md), cách dùng app [bt-app.md](bt-app.md), các bước chi tiết trên board khách [bt-client-test.md](bt-client-test.md), đưa agent vào image [yocto-recipe.md](yocto-recipe.md).

Quy ước:
- `<ADDR>` = địa chỉ Bluetooth của board. Channel RFCOMM luôn là **22**.
- `$AGENT` = đường dẫn agent trên board: `/tmp/ctt-agent` nếu chép tay, `/usr/bin/ctt-agent` nếu agent có sẵn trong image.
- Chuỗi trong `code` là **đúng chữ app in ra**. Dấu `…` là phần thay đổi theo từng lần chạy.
- Ghi kết quả vào bảng ở cuối file, mỗi tổ hợp một cột.

---

## 0. Chuẩn bị (làm một lần)

### P-01: Lấy đúng phiên bản

| | |
|---|---|
| Làm | Tải từ GitHub Releases (bản `vX.Y.Z` hoặc `latest-main`): `ctt-agent-aarch64` cho board, `ctt-agent.exe` và `ctt-agent-console.exe` cho Client PC. Kiểm tra bằng `sha256sum -c SHA256SUMS` (xem [release.md](release.md)) |
| Board (UART) | Chép `ctt-agent-aarch64` vào `/tmp/ctt-agent` (USB stick, xem [bt-client-test.md](bt-client-test.md) bước 1), rồi `chmod +x /tmp/ctt-agent && /tmp/ctt-agent version` |
| PC | `ctt-agent-console.exe --version`. Với GUI thì xem tiêu đề cửa sổ |
| Mong đợi | Board: `ctt-agent X.Y.Z (CTP v1, aarch64)`. PC: `ctt-agent-console X.Y.Z`; tiêu đề cửa sổ `CTT Bluetooth Test X.Y.Z`. **Ba số version phải giống nhau** |

### P-02: Bluetooth trên board

| | |
|---|---|
| Board (UART) | `dmesg -n 1; hciconfig -a; systemctl is-active bt-daemon; bt-daemon-cli.sh get-paired-devices` |
| Mong đợi | `hci0` ở trạng thái `UP RUNNING`. Ghi lại `BD Address` (= `<ADDR>`). bt-daemon `active`. Số bond < 5 (đủ 5 thì `bt-daemon-cli.sh remove <addr>`) |

### TC-01: Bluetooth trên PC và cấu hình "No SSH"

| | |
|---|---|
| A (console) | `ctt-agent-console.exe` → (1) Settings → `SSH to the board?` = `n` → `Board BT address` = `<ADDR>` (hoặc để trống rồi chọn ở TC-04) → `RFCOMM channel on the board` = `22` |
| B (GUI) | Tab **1. Connect** → tick **No SSH (agent already runs as a service, channel 22)** → ô **Board BT address** = `<ADDR>`, **RFCOMM channel** = `22` |
| Mong đợi | Dòng đầu (console) / dòng đầu Log (GUI): `PC Bluetooth: Windows (WinRT): OK`. Không có `this Python has no RFCOMM sockets!`. Console: dòng `Board:` của menu hiện `<ADDR>` và channel 22 |

---

## 1. Pairing

### TC-04: PC tìm thấy board

| | |
|---|---|
| Board (UART) | `bt-daemon-cli.sh set-pairable true; bt-daemon-cli.sh set-discoverable true` |
| A (console) | (4) Scan Bluetooth → chờ ~10 s → `Use one of them as the board?` = `y` → số thứ tự của board. Hoặc lệnh: `ctt-agent-console.exe scan` |
| B (GUI) | **Scan** → chờ 10–15 s → chọn board trong danh sách |
| Mong đợi | Board có trong danh sách với đúng `<ADDR>`. Chọn board thì địa chỉ được điền vào Settings / ô **Board BT address** |

### TC-05: Pair PC ↔ board

| | |
|---|---|
| Trước | Board chưa pair với PC (đã pair thì làm TC-18 trước) |
| A (console) | (6) Pair with the board → Enter (giữ địa chỉ). Không có SSH thì console không hỏi `Board accepts the pairing…` |
| B (GUI) | Chọn board → **Pair**. Bỏ tick **Board accepts the pairing (over SSH)** (không có SSH) |
| Board (UART) | Ngay khi PC đang pair: `bt-daemon-cli.sh confirm-pairing` |
| Mong đợi | Console: `OK  Pairing successful`. GUI: Log `Pair <ADDR>: Pairing successful`. Có mã 6 số (`Passkey: …`) thì mã phải **trùng** với mã board in ra. (5) Paired devices / **Paired devices** có `<ADDR>` |

### TC-18: Bỏ pair rồi pair lại (5 lần)

| | |
|---|---|
| A / B | (7) Unpair → (6) Pair / **Unpair** → Yes → **Pair**. Board xác nhận như TC-05 |
| Mong đợi | `removed`, rồi pair lại thành công. Lặp 5 lần đều OK. Board không bị đầy 5 bond (`get-paired-devices`) |

---

## 2. Terminal: gửi / nhận dữ liệu thô

Board chạy `pipe --text`: mỗi dòng gõ trên UART được gửi sang PC, và mọi dữ liệu nhận được in ra cả dạng chữ lẫn hex, kèm **tổng byte đã nhận / đã gửi**. PC hiện tổng theo từng chiều.

**Board (UART), chạy trước mọi TC ở mục này** (dừng `ctt-agent-bt` nếu đang chạy, vì nó giữ channel 22):
```sh
systemctl stop ctt-agent-bt 2>/dev/null
$AGENT pipe --text -t rfcomm -e /22
```
Board in ra: `-- listening on rfcomm <ADDR>/22  (type a line + Enter to send it, /hex 00 ff to send bytes, Ctrl+C to quit)`

### TC-06: Mở kết nối RFCOMM

| | |
|---|---|
| A (console) | (9) Terminal: send / receive data |
| B (GUI) | Tab **3. Terminal** → **Connect** |
| Mong đợi | Board: `-- connected: <địa chỉ BT của PC>…`. Console: `connected <ADDR> channel 22. Type a line + Enter: …`. GUI: nhãn `Connected <ADDR> channel 22`, khung PC `— connected to <ADDR> / channel 22`, khung Board `— (no SSH: the board side is not visible)` |

### TC-07: PC gửi chữ sang board

| | |
|---|---|
| A (console) | Gõ `hello board` → Enter |
| B (GUI) | Format **Text**, Line end `\n` → khung PC gõ `hello board` → **Send PC → board** |
| Mong đợi | PC: console `[PC tx 12 B, total 12 B]`; GUI khung PC `PC → board [12 B, total 12 B] hello board\n`. Board: `[   …s] board rx 12 B (total rx 12 B): "hello board\n"   hex 68 65 6c 6c 6f 20 62 6f 61 72 64 0a`. **Số byte hai bên bằng nhau**, nội dung đúng từng ký tự |

### TC-08: Board gửi chữ về PC

| | |
|---|---|
| Board (UART) | Gõ `hi PC` → Enter |
| Mong đợi | Board: `[   …s] board tx 6 B (total tx 6 B): "hi PC\n"`. Console: `[PC rx 6 B, total 6 B] b'hi PC\n'`. GUI khung PC: `board → PC [6 B, total 6 B] hi PC\n` |

### TC-09: Dữ liệu nhị phân (hex), hai chiều

| | |
|---|---|
| A (console) | `/hex 00 ff 55 aa 0d 0a` |
| B (GUI) | Format **Hex** → khung PC gõ `00 ff 55 aa 0d 0a` → **Send PC → board** |
| Board (UART) | `/hex 01 02 fe 0d 0a` |
| Mong đợi | Board nhận: `board rx 6 B (total rx 18 B): "\x00\xffU\xaa\r\n"   hex 00 ff 55 aa 0d 0a`, tức đúng 6 byte, không mất hay đổi byte nào (kể cả `0d 0a`). PC nhận: `[PC rx 5 B, total 11 B] b'\x01\x02\xfe\r\n'` (console) / `board → PC [5 B, total 11 B] 01 02 fe 0d 0a` (GUI, Format Hex) |

### TC-09b: Đếm đủ khi gửi nhiều

| | |
|---|---|
| Làm | PC gửi 20 dòng bất kỳ, board gửi 20 dòng bất kỳ (gõ nhanh liên tục) |
| Mong đợi | `total rx` trên board = `total` của `PC tx` (console) / `PC → board` (GUI) ở dòng cuối. `total` của `PC rx` / `board → PC` = `total tx` của board. Không dòng nào bị dính hay mất |

Kết thúc mục này: console `/quit`, GUI **Disconnect**. Board in `-- link closed: peer closed the link`. Nếu board vẫn chờ thì Ctrl+C.

---

## 3. Throughput và latency, có trace mỗi giây

**Board (UART), chạy trước mọi TC ở mục này** (dừng `pipe` trước, vì hai lệnh không dùng chung channel 22 được):
```sh
$AGENT server -t rfcomm -e /22 -i 1
```
Hoặc dùng service: `systemctl start ctt-agent-bt`, rồi `journalctl -f -u ctt-agent-bt` để xem trace.

### Đọc trace

Trong lúc đo, **mỗi giây** cả hai bên in một dòng: tốc độ của giây vừa qua, cộng **tổng đã gửi / đã nhận từ đầu** (byte và frame). Cuối lần đo, PC in dòng `data:` để so tổng hai bên.

| Mode | Board (UART) | PC: console và Log của GUI |
|---|---|---|
| `tx` PC → board | `[up   rx] … Mbps  +N fr  … KB \| received N fr  X KB  lost=0 crc_err=0` | `…s  PC->board  0.403 Mbit/s  sent X KB (N fr)` |
| `rx` board → PC | `[down tx] … \| sent N fr  X KB` | `…s  board->PC  0.400 Mbit/s  received X KB (N fr)  lost 0  crc 0` |
| `bidir` | xen kẽ dòng `[up   rx]` (nhận) và `[down tx]` (gửi) | hai chiều trên một dòng, cách nhau bởi `\|` |
| `echo` | `[echo srv] … +N replies \| replied M (… KB)` | `…s  echo  rtt avg … ms  replies M` |

Mỗi lần đo, board in đầu tiên `session from …`, rồi `mode=… block=… duration=…`, rồi `direction: …` (board đang nhận hay gửi). Cuối cùng board in `session done: received X (N fr) in T s = R Mbps, lost=0 crc_err=0` và/hoặc `sent X (N fr) …`.

GUI: tab **2. Throughput** có biểu đồ, nhãn kết quả (`sent … · received … · lost 0 · CRC errors 0`) và một dòng Log mỗi giây.

### Ngưỡng PASS

Mọi lần đo **luôn** phải có `lost 0`, `CRC errors 0` và đúng thứ tự. Ngoài ra phải đạt ngưỡng của level đang chạy:

| Level | Ý nghĩa | Dùng khi |
|---|---|---|
| `baseline` | Mức sàn để bắt regression của setup Local Test (PC Windows + USB dongle). Bằng khoảng 75% số thấp nhất đã đo trong `report.md` (PC → board 0.32–0.44, board → PC 0.51–0.61 Mbit/s) | Mỗi build tool / firmware mới |
| `target` | **Mục tiêu sản phẩm** trên board khách (chip trên board) | Chấp nhận sản phẩm |

| Case | TC | Chiều | Block | Thời gian | `baseline` | `target` |
|---|---|---|---|---|---|---|
| BT-TP-01 | TC-10 | PC → board (`tx`) | 990 | 30 s | **≥ 0.25 Mbit/s** | **≥ 1.0 Mbit/s** |
| BT-TP-02 | TC-11 | board → PC (`rx`) | 990 | 30 s | **≥ 0.40 Mbit/s** | **≥ 1.0 Mbit/s** |
| BT-TP-03 | TC-12 | hai chiều (`bidir`) | 990 | 30 s | PC→board **≥ 0.15**, board→PC **≥ 0.25** | mỗi chiều **≥ 0.5** |
| BT-TP-04 | TC-13 | echo / RTT | 64 | 10 s | RTT p99 **≤ 250 ms** | RTT p99 **≤ 100 ms** |
| BT-TP-05 | TC-14 | PC → board (`tx`) | 4096 | 30 s | **≥ 0.25 Mbit/s** | **≥ 1.0 Mbit/s** |
| BT-TP-06 | TC-14 | board → PC (`rx`) | 4096 | 30 s | **≥ 0.40 Mbit/s** | **≥ 1.0 Mbit/s** |

Trần lý thuyết của RFCOMM (goodput): 3-DH5 khoảng 2.1, 2-DH5 khoảng 1.4, DH5 khoảng 0.7 Mbit/s. Mức `target` 1.0 Mbit/s cần link chạy EDR. Đo dưới 0.7 Mbit/s thì chạy `btmon` trên board để xem loại gói ACL và sniff mode. Hai ngưỡng RTT chưa có số đo thật, sẽ chốt sau lần chạy đầu. Muốn đổi ngưỡng: `suite --dump > my.json`, sửa số, rồi `suite --file my.json`.

### TC-10: PC → board (`tx`), BT-TP-01

| | |
|---|---|
| A (console) | (8) Throughput / latency test → Direction `1` → Duration `30` → Block `990` → Rate `0` → CRC `y`. Hoặc: `ctt-agent-console.exe run --addr <ADDR> --channel 22 -m tx -d 30 --min-up 1.0` |
| B (GUI) | Tab **2. Throughput** → **PC → board**, Duration `30`, Block `990`, **Min Mbit/s (PASS)** = `1.0` (target) / `0.25` (baseline), tick **Verify CRC of every block** → **▶ Start** |
| Theo dõi | Mỗi giây `sent` trên PC tăng, và `received` trên board tăng theo. Board ngừng in mà PC vẫn tăng `sent` nghĩa là dữ liệu bị nghẽn hoặc mất trên đường |
| Mong đợi | PC: `data: PC sent X, board received X` (hai số **bằng nhau**), `lost 0  crc 0`, PC→board ≥ ngưỡng → `PASS`. Board: `session done: received X (N fr) … lost=0 crc_err=0`, với X giống PC. Ghi lại Mbit/s |

### TC-11: board → PC (`rx`), BT-TP-02

| | |
|---|---|
| A (console) | (8) → Direction `2`, 30 s, 990. Hoặc: `run --addr <ADDR> --channel 22 -m rx -d 30 --min-down 1.0` |
| B (GUI) | **board → PC**, Min Mbit/s `1.0` / `0.40` → **▶ Start** |
| Mong đợi | Board: `[down tx] … sent …` mỗi giây, cuối cùng `session done: sent X (N fr) …`. PC: `received X` tăng mỗi giây, `data: board sent X, PC received X` (bằng nhau), board→PC ≥ ngưỡng → `PASS` |

### TC-12: Hai chiều cùng lúc (`bidir`), BT-TP-03

| | |
|---|---|
| A (console) | (8) → Direction `3`. Hoặc: `run --addr <ADDR> --channel 22 -m bidir -d 30 --min-up 0.5 --min-down 0.5` |
| B (GUI) | **both ways**. Ô Min Mbit/s áp cho cả hai chiều: `0.5` (target). Với baseline, nhập `0.15` rồi tự so chiều board→PC với 0.25 |
| Mong đợi | Board in xen kẽ `[up   rx]` và `[down tx]`. `session done: received X …; sent Y …`. PC: `data: PC sent X, board received X   board sent Y, PC received Y`. Đạt ngưỡng cả hai chiều → `PASS`. Ghi cả hai số |

### TC-13: Latency (`echo`), BT-TP-04

| | |
|---|---|
| A (console) | (8) → Direction `4` → Duration `10` → Block `64`. Hoặc: `run --addr <ADDR> --channel 22 -m echo -d 10 -b 64 --max-rtt-p99 100` |
| B (GUI) | **echo / RTT**, Duration `10`, Block `64` → **▶ Start**. Đọc RTT p99 trong Log |
| Mong đợi | Board: `[echo srv] … replied M` mỗi giây, cuối cùng `session done: replied to M echo requests`. PC: `replies M` tăng dần, M cuối **bằng** số của board. RTT p99 ≤ ngưỡng → `PASS`. Ghi lại min/avg/p99/max |

### TC-14: Block lớn, BT-TP-05 / 06

| | |
|---|---|
| A (console) | `ctt-agent-console.exe suite --addr <ADDR> --channel 22 -l target --only BT-TP-05,BT-TP-06`. Block khác: `run … -b 2048 --min-up …` |
| B (GUI) | Như TC-10 / TC-11, Block `4096` (rồi `2048`, `8192`) |
| Mong đợi | Mọi lần PASS. Trên Windows block gần như không ảnh hưởng (chênh dưới 0.1 Mbit/s, theo `report.md`). Block lớn **nhanh hơn rõ rệt** thì ghi lại, vì điều đó thay đổi kết luận về nút cổ chai |

### TC-20: Cả bộ BT-TP-01…06 một lần (chỉ tổ hợp A)

| | |
|---|---|
| A (console) | `ctt-agent-console.exe suite --addr <ADDR> --channel 22 -l target` (khoảng 3 phút). Hoặc (10) Test suite with Mbit/s expectations → level → Enter |
| Theo dõi | Trước mỗi case in `[i/6] BT-TP-0x …`, rồi một dòng trace mỗi giây. Board in một `session from …` cho mỗi case. `-q` tắt trace |
| Mong đợi | Bảng tổng kết kết thúc bằng `6/6 PASS`, exit code 0. Báo cáo ở `ctt-results/suite-target-<thời điểm>.json` và `.csv`. Case FAIL ghi rõ ngưỡng không đạt, ví dụ `PC->board 0.420 >= 1 Mbit/s` |

GUI không có nút chạy cả bộ. Ở tổ hợp B, chạy TC-10 → TC-14 lần lượt.

---

## 4. Ổn định và lưu kết quả

### TC-15: Dừng giữa chừng

| | |
|---|---|
| A / B | Đo `tx` 60 s → sau ~10 s nhấn Ctrl+C (console) / **■ Stop** (GUI) |
| Mong đợi | Vẫn có kết quả của khoảng đã chạy. Board in `session done: …` với số byte bằng `data:` trên PC. Không treo; lần đo kế tiếp chạy bình thường mà không phải khởi động lại `server` trên board |

### TC-16: Chạy dài

| | |
|---|---|
| A | `run --addr <ADDR> --channel 22 -m bidir -d 600 --min-up 0.5 --min-down 0.5 --json > long.json` |
| B | **both ways**, Duration `600` |
| Mong đợi | PASS, không rớt kết nối. Mbit/s ở các dòng trace cuối không thấp hơn quá 20% so với các dòng đầu. Tổng hai bên bằng nhau |

### TC-17: Mất và khôi phục kết nối

| | |
|---|---|
| Làm | Đang đo 60 s → tắt Bluetooth của board (`hciconfig hci0 down` từ một shell khác trên board, ví dụ chạy `server` trong nền bằng `&`), hoặc mang PC ra xa → bật lại `hciconfig hci0 up` → đo lần nữa |
| Mong đợi | Lần đo đang chạy kết thúc bằng lỗi rõ ràng: PC `board stopped sending (timeout)` hoặc lỗi kết nối, board `session failed: …`. App không treo. Lần đo sau PASS mà không phải pair lại hay khởi động lại app |

### TC-19: Lưu kết quả

| | |
|---|---|
| A | `run … --json > ket-qua.json` cho từng lần đo, hoặc `suite` (tự lưu JSON + CSV vào `ctt-results/`). Chép về: `scp <user>@<client-pc>:ctt-results/* .` |
| B | Tab Throughput → **Save results (CSV + JSON)…** |
| Board | Lưu log UART (hoặc `journalctl -u ctt-agent-bt > ctt.log` nếu dùng service) |
| Mong đợi | CSV có một dòng cho mỗi lần đo. JSON có số board đếm (`board_stats`), `up_sent_bytes` / `up_bytes` / `down_bytes`, `verdict` và `checks` |

---

## Bảng ghi kết quả

Build: board `ctt-agent ……`, PC `ctt-agent-console ……` / `ctt-agent.exe ……`, ngày ……, người test ……

| TC | A: UART + console (SSH) | B: UART + GUI | Số đo / ghi chú |
|---|---|---|---|
| P-01 version | | | board / console / GUI: |
| P-02 BT board | | | `<ADDR>`: |
| TC-01 BT PC, No SSH | | | |
| TC-04 Scan | | | |
| TC-05 Pair | | | mã xác nhận: |
| TC-18 Unpair / pair ×5 | | | |
| TC-06 Kết nối terminal | | | |
| TC-07 PC → board text | | | byte PC / board: |
| TC-08 board → PC text | | | |
| TC-09 hex hai chiều | | | |
| TC-09b đếm đủ 20 + 20 dòng | | | total hai bên: |
| TC-10 / BT-TP-01 tx | | | Mbit/s, byte PC / board: |
| TC-11 / BT-TP-02 rx | | | Mbit/s: |
| TC-12 / BT-TP-03 bidir | | | up / down: |
| TC-13 / BT-TP-04 echo | | | RTT p99 ms, replies: |
| TC-14 / BT-TP-05, 06 | | | 2048 / 4096 / 8192: |
| TC-20 suite (A) | | – | x/6 PASS: |
| TC-15 Stop | | | |
| TC-16 600 s | | | đầu / cuối Mbit/s: |
| TC-17 mất kết nối | | | |
| TC-19 lưu kết quả | | | |

Trường hợp **có SSH tới board** (setup Local Test), app tự chạy agent trên board và in luôn trace của board (dòng `[board] …`). Xem [bt-app.md](bt-app.md).
