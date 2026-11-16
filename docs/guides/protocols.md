# Đo throughput theo giao thức: USB và WiFi

Từ version 3.0, CTT không chỉ đo "tốc độ tối đa của đường truyền". Nó còn **mô phỏng tải và nhịp gửi** của từng giao thức thật chạy trên USB và WiFi (CarPlay, Android Auto, audio, video, VoIP…). Mục đích là trả lời câu hỏi: *board có gánh được một phiên CarPlay / một cuộc gọi / một luồng camera mà không giật, không rè hay không?*

> **CTT không cài đặt các giao thức đó.** Nó không có iAP2, xác thực Apple MFi, AirPlay, AOA hay Android Auto. CTT chỉ gửi **đúng lượng dữ liệu, đúng nhịp, đúng chiều** như giao thức thật, trên **cùng đường truyền vật lý** (USB bulk, USB-NCM, WiFi TCP/UDP). Vì vậy kết quả cho biết đường truyền và ngăn xếp của board có đủ sức hay không; nó không kiểm tra tính tương thích của giao thức.

## 1. Mô hình traffic

| Mô hình | Tùy chọn | Dùng cho |
|---|---|---|
| Liên tục, tối đa | (mặc định) | Dung lượng đường truyền: copy file, MSC/MTP |
| Liên tục, giới hạn tốc độ | `rate: 10M` | Luồng đều: serial (CDC-ACM), tải UDP cố định |
| **Burst** | `burst: 20K` + `burst_rate: 60` | Media: mỗi chu kỳ gửi liền một khối, ví dụ một khung hình video hoặc một chu kỳ audio |
| Echo | `mode: echo` | Độ trễ khứ hồi: lệnh điều khiển, input, HID |

**Burst:** cứ mỗi `1 / burst_rate` giây, bên gửi gửi liền `burst` byte (làm tròn lên thành số block nguyên). Bên nhận đo:

| Chỉ số | Ý nghĩa | Chỉ tiêu (`expect`) |
|---|---|---|
| `on time %` | Tỉ lệ burst hoàn tất đúng nhịp | `burst_on_time_pct_min` |
| `late` | Số burst đến muộn hơn **1,5 chu kỳ** so với burst trước: người dùng thấy **giật hình** / nghe **rè, mất tiếng** | `burst_late_max` |
| `max gap` | Khoảng trống dài nhất giữa hai burst | `burst_max_gap_ms_max` |
| `jitter` | Độ lệch trung bình so với chu kỳ | `burst_jitter_ms_max` |

Ngoài ra, mọi phép đo vẫn có Mbit/s, mất gói, CRC và (với UDP) loss %, jitter.

**Chạy song song:** một mục `parallel:` trong profile chạy nhiều luồng **cùng lúc**, ví dụ một phiên CarPlay gồm video, audio, mic, touch và điều khiển. Mỗi luồng TCP/UDP tự nhận một cổng riêng (5201, 5202…). USB bulk chỉ có một pipe dữ liệu nên không chạy song song được; dùng USB-NCM (IP qua USB) nếu cần nhiều luồng cùng lúc.

Với TCP, cả hai đầu tự tắt Nagle (`TCP_NODELAY`) cho phiên burst, giống các ứng dụng media thật. Nếu không tắt, block cuối của mỗi khung bị giữ lại khoảng 40 ms.

## 2. Chạy

```bash
export CTT_DUT=root@<board-ip>
scripts/ctt profile config/profiles/<usb|wifi>/<giao-thức>.yaml --list     # xem các luồng
scripts/ctt profile config/profiles/usb/carplay-wired.yaml --target-addr <ip-usb0-của-board>
scripts/ctt run -t tcp -m tx --burst 20K --burst-rate 60 -d 30s            # một luồng lẻ
```

- `--target-addr`: IP dùng cho dữ liệu khi nó khác IP SSH, ví dụ `usb0` (NCM) hoặc `wlan0`. Các luồng vẫn giữ cổng riêng.
- `-d 10s`: đổi thời lượng mọi luồng (để chạy thử nhanh).
- Kết quả: bảng tổng kết có cột `ON TIME %`. Báo cáo HTML/JUnit nằm ở `build/reports/`.

**Quy ước vai trò** trong mọi profile: **board = CTT server**, **PC = CTT client**. `tx` = PC → board, `rx` = board → PC. Mỗi file ghi rõ board đóng vai gì (head unit, thiết bị USB…).

## 3. USB

Chuẩn bị: `scripts/ctt usb gadget up --dut root@<board>` (thêm `--ncm` cho các profile chạy IP qua USB), xem [usb.md](usb.md).

| Giao thức | Profile | Đường truyền trong CTT | Luồng (mặc định) |
|---|---|---|---|
| **CarPlay có dây** | [usb/carplay-wired.yaml](../../config/profiles/usb/carplay-wired.yaml) | USB-NCM (TCP + UDP), chạy song song | Video màn hình H.264 ~10 Mbit/s @60 fps (tx); audio 48 kHz stereo 10 ms (UDP, tx); mic 16 kHz 20 ms (UDP, rx); touch 120 Hz (rx); điều khiển iAP2 (echo, p99 ≤ 20 ms); headroom |
| **Android Auto có dây** | [usb/androidauto-wired.yaml](../../config/profiles/usb/androidauto-wired.yaml) | USB bulk (một pipe như AOA), lần lượt | Video 1080p30 ~8 Mbit/s, 60 fps ~12 Mbit/s; audio media 48 kHz; dẫn đường 16 kHz; mic; input/sensor 100 Hz; điều khiển; dung lượng |
| **USB Audio (UAC 1/2)** | [usb/uac-audio.yaml](../../config/profiles/usb/uac-audio.yaml) | USB bulk, chu kỳ 1 ms | 48 kHz/16-bit/2 kênh = 192 B/ms (phát, thu); 96 kHz/24-bit = 576 B/ms; 8 kênh 32-bit = 1536 B/ms; thoại 16 kHz hai chiều. Chỉ tiêu: đúng hạn ≥ 99,9 %, khoảng trống ≤ 3 ms |
| **USB Video (UVC)** | [usb/uvc-video.yaml](../../config/profiles/usb/uvc-video.yaml) | USB bulk, mỗi khung hình một burst (rx) | H.264 1080p30; MJPEG 720p30 / 1080p30; YUY2 480p30 (147 Mbit/s, vừa High-Speed); YUY2 720p30 (442 Mbit/s, cần SuperSpeed) |
| **Mass Storage / MTP** | [usb/usb-classes.yaml](../../config/profiles/usb/usb-classes.yaml) | USB bulk, khối 512 KB | Ghi / đọc tối đa. Thiết bị nhớ cắm **vào** board: `ctt usb storage` |
| **CDC-ACM (serial)** | usb/usb-classes.yaml | USB bulk, giới hạn tốc độ | 115200 và 921600 baud, hai chiều, không được mất byte |
| **HID** | usb/usb-classes.yaml | USB bulk / echo | Report 64 B mỗi 1 ms; độ trễ report (p99 ≤ 3 ms) |
| **CDC-NCM / ECM (mạng qua USB)** | [usb/ncm-network.yaml](../../config/profiles/usb/ncm-network.yaml) | TCP/UDP trên `usb0` | TCP lên/xuống/hai chiều, 4 luồng song song, UDP 200 Mbit/s, độ trễ |
| Bulk thô | [usb/bulk-raw.yaml](../../config/profiles/usb/bulk-raw.yaml) | USB bulk | Dung lượng, các cỡ block |

USB thật dùng **isochronous** cho audio/video. CTT không có transport isochronous: nó gửi cùng số byte, cùng nhịp qua bulk. Kết quả cho biết đường USB và CPU của board có giữ được nhịp không, nhưng không đo cơ chế isochronous.

## 4. WiFi

Chuẩn bị: PC và board chung mạng WiFi (thường PC nối vào AP của board, 5 GHz). Thêm `wifi_iface: auto` (đã có sẵn trong các profile) để ghi signal / bitrate vào mỗi kết quả.

| Giao thức | Profile | Luồng (mặc định) |
|---|---|---|
| **CarPlay không dây** | [wifi/carplay-wireless.yaml](../../config/profiles/wifi/carplay-wireless.yaml) | Song song: video ~12 Mbit/s @60 fps (TCP); audio 48 kHz (UDP); mic (UDP); touch 120 Hz; điều khiển (echo, p99 ≤ 30 ms); headroom |
| **Android Auto không dây** | [wifi/androidauto-wireless.yaml](../../config/profiles/wifi/androidauto-wireless.yaml) | Song song trên TCP: video ~8 Mbit/s @30 fps; audio; mic; input/sensor; điều khiển |
| **Miracast / Wi-Fi Display** | [wifi/miracast.yaml](../../config/profiles/wifi/miracast.yaml) | RTP/UDP MPEG-TS (datagram 1316 B) 1080p30 ~15 Mbit/s + UIBC + RTSP song song; 4K30 ~40 Mbit/s |
| **VoIP / gọi video** | [wifi/voip-call.yaml](../../config/profiles/wifi/voip-call.yaml) | G.711 160 B / 20 ms hai chiều; Opus băng rộng; video call 720p ~1,5 Mbit/s mỗi chiều; độ trễ. Chỉ tiêu: mất ≤ 1 %, jitter ≤ 30 ms |
| **Streaming audio/video** | [wifi/media-streaming.yaml](../../config/profiles/wifi/media-streaming.yaml) | HLS/DASH: mỗi segment 4 s là một burst (HD 5 Mbit/s, 4K 25 Mbit/s), nhạc 320 kbit/s. Segment đến muộn nghĩa là bị rebuffer |
| **Truyền file / OTA** | [wifi/file-transfer.yaml](../../config/profiles/wifi/file-transfer.yaml) | TCP lên / xuống / hai chiều tối đa, 4 kết nối song song |
| **Tương tác thời gian thực** | [wifi/interactive-latency.yaml](../../config/profiles/wifi/interactive-latency.yaml) | UDP 60 Hz (game, điều khiển từ xa), request/response TCP, telemetry 10 Hz |
| TCP / UDP cơ bản | [wifi/tcp-baseline.yaml](../../config/profiles/wifi/tcp-baseline.yaml), [wifi/udp-load.yaml](../../config/profiles/wifi/udp-load.yaml) | Dung lượng TCP; quét tải UDP 10–200 Mbit/s; luồng kiểu VoIP |

Bluetooth: [bluetooth/rfcomm-throughput.yaml](../../config/profiles/bluetooth/rfcomm-throughput.yaml) và app Bluetooth ([bt-app.md](bt-app.md)).

## 5. Con số mặc định đến từ đâu

Các bitrate, cỡ khung và chu kỳ trong profile là **giá trị kỹ thuật điển hình**, tính từ định dạng media:

| Định dạng | Công thức |
|---|---|
| Audio PCM | byte mỗi chu kỳ = tần số lấy mẫu × số kênh × byte/mẫu × chu kỳ. Ví dụ 48000 × 2 × 2 × 10 ms = 1920 B |
| Video nén (H.264) | byte mỗi khung = bitrate / fps. Ví dụ 10 Mbit/s / 60 = ~20 KB |
| Video thô (YUY2) | rộng × cao × 2 B. Ví dụ 640 × 480 × 2 = 600 KB mỗi khung |
| Serial | byte/s = baud / 10 |

Đây **không phải** số trích từ đặc tả của Apple, Google hay USB-IF. Hãy chỉnh theo sản phẩm (độ phân giải màn hình, codec, định dạng audio) và đặt ngưỡng `expect` theo yêu cầu sản phẩm. Cách làm: sửa trực tiếp file YAML, hoặc chép sang `config/profiles/<tên>.private.yaml`; file `.private.yaml` không bị commit.

## 6. Đọc kết quả

```
session/screen-video   tx   10.122 Mbps   ON TIME 100   LOST 0   PASS
     up   bursts: 600  on time 100%  late 0  max gap 17.1 ms  jitter 0.04 ms
```

- **Mbit/s xấp xỉ bitrate của mô hình** là bình thường: mô hình burst gửi đúng lượng cần, không gửi tối đa. Muốn biết còn bao nhiêu dư địa, xem phép đo `headroom` / `capacity`.
- **`on time` < 100 % hoặc `max gap` lớn** nghĩa là có lúc đường truyền hoặc board không theo kịp. Hãy so với chu kỳ: video 60 fps có chu kỳ 16,7 ms, nên khoảng trống 100 ms tương đương mất khoảng 6 khung.
- **UDP `loss` > 0** nghĩa là gói bị mất trên WiFi/USB hoặc do bộ đệm socket. Thử `sndbuf` / `rcvbuf` lớn hơn và so với signal WiFi trong báo cáo.
