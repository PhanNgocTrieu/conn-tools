# CTP — Connectivity Test Protocol v1

CTP là giao thức chung giữa hai `ctt-agent`, chạy trên **bất kỳ transport dạng stream tin cậy** nào (TCP, RFCOMM, L2CAP, USB bulk, ...). Mọi chế độ test và metrics đều dùng chung, transport chỉ có nhiệm vụ chuyển byte.

Mã nguồn tham chiếu: [agent/include/ctt/ctp.hpp](../agent/include/ctt/ctp.hpp), [agent/src/ctp.cpp](../agent/src/ctp.cpp).

## 1. Frame

Mọi message đều gồm một header 32 byte theo sau là payload. Tất cả các trường dùng **little-endian**.

| Offset | Size | Field | Ghi chú |
|---:|---:|---|---|
| 0 | 4 | `magic` | `0x31505443`, trên dây là chuỗi ASCII `"CTP1"` |
| 4 | 1 | `version` | `1` |
| 5 | 1 | `type` | xem mục 2 |
| 6 | 2 | `flags` | bit0 `CRC`: trường `crc32` hợp lệ |
| 8 | 4 | `seq` | DATA: bắt đầu từ 0, tăng 1 mỗi frame. ECHO: id của request |
| 12 | 4 | `payload_len` | ≤ 1 MiB |
| 16 | 8 | `ts_ns` | monotonic ns **của bên gửi** (chỉ so sánh được trên cùng máy) |
| 24 | 4 | `crc32` | CRC-32/IEEE của payload (khi có flag CRC) |
| 28 | 4 | `reserved` | 0 |

Bên nhận tách frame theo `payload_len`. Nếu `magic` sai thì stream đã mất đồng bộ, khi đó session bị huỷ với lỗi.

## 2. Message types

| Type | Tên | Hướng | Payload |
|---:|---|---|---|
| 1 | `START` | client → server | `StartParams` (32 B) |
| 2 | `START_ACK` | server → client | — |
| 3 | `DATA` | bên gửi → bên nhận | pattern PRBS |
| 4 | `STOP` | bên gửi → bên nhận | — (`seq` = số frame đã gửi) |
| 5 | `STATS` | server → client | `StatsPayload` (88 B) |
| 6 | `ECHO_REQ` | client → server | dữ liệu bất kỳ |
| 7 | `ECHO_RSP` | server → client | copy nguyên `ECHO_REQ`, gồm cả `seq` và `ts_ns` |
| 8 | `ERROR` | bất kỳ | chuỗi UTF-8 |

### StartParams (32 B)

| Offset | Size | Field |
|---:|---:|---|
| 0 | 1 | `mode`: 1 tx, 2 rx, 3 bidir, 4 echo (đặt tên theo góc nhìn **client**) |
| 1 | 3 | reserved |
| 4 | 4 | `block_size`: số byte payload mỗi DATA frame |
| 8 | 4 | `duration_ms` |
| 12 | 4 | `flags`: bit0 `VERIFY` (bên gửi tính CRC, bên nhận kiểm tra) |
| 16 | 8 | `rate_bps`: giới hạn tốc độ bên gửi, 0 = không giới hạn |
| 24 | 4 | `echo_interval_us` |
| 28 | 4 | `echo_count`: 0 = chạy theo thời gian |

### StatsPayload (88 B)

`flags u32` (bit0 có rx, bit1 có tx), `reserved u32`, sau đó là 10 trường `u64`:
`rx_frames, rx_bytes, rx_lost, rx_out_of_order, rx_crc_errors, rx_duration_ns, tx_frames, tx_bytes, tx_duration_ns, echoed`.

## 3. Luồng session

```
client                                   server
  | START(mode, block, duration, ...) ---> |
  | <--------------------------- START_ACK |   t0 ở cả 2 phía
  |                                        |
  | tx:    DATA... STOP ------------------> |   server đếm (bên nhận)
  | rx:    <-------------------- DATA... STOP
  | bidir: cả hai chiều chạy song song, mỗi bên tự gửi STOP
  | echo:  ECHO_REQ -> <- ECHO_RSP (lặp lại), sau đó STOP
  |                                        |
  | <------------------------------- STATS |   góc nhìn của server
  |   server: shutdown(WR), client đóng kết nối
```

## 4. Đo đạc

- **Throughput lấy ở bên nhận**: `payload_bytes × 8 / (t_last_frame − t0)`. Số đo ở bên gửi chỉ là lượng byte đã đưa vào transport (còn nằm trong buffer), nên luôn cao hơn thực tế. Report hiển thị cả hai số.
- **Loss / out-of-order**: dựa trên `seq`. Với transport tin cậy (TCP, RFCOMM), mọi giá trị khác 0 đều là bug.
- **Integrity**: kiểm tra CRC-32 trên mỗi frame. Bên gửi dùng buffer pattern tạo sẵn và CRC tính sẵn, nên phía gửi không tốn chi phí lúc chạy. Dùng `--no-verify` để tắt CRC khi CPU của DUT là nút cổ chai.
- **RTT**: `now − ts_ns` của `ECHO_RSP`, đo trên đồng hồ của client nên không cần đồng bộ clock giữa hai máy.
- **Overhead**: header 32 B/frame. Ví dụ block 990 B (RFCOMM) thì overhead khoảng 3.1%, block 128 KiB (TCP) thì khoảng 0.02%. Throughput báo cáo là **goodput** (chỉ tính payload).

## 5. Mở rộng

- Thêm trường mới vào header thì phải tăng `version`. Trường `reserved` được dành cho flag mở rộng tương thích ngược.
- Transport kiểu datagram (UDP, BLE GATT) gửi mỗi frame trong một datagram. Khi đó `lost` và `out_of_order` là số đo thật, không còn là dấu hiệu bug.
