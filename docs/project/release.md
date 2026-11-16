# Phát hành phiên bản (GitHub Releases)

GitHub Actions ([.github/workflows/btapp.yml](../../.github/workflows/btapp.yml)) build và đưa các file chạy được lên trang **Releases** của repo. Không cần build tay.

## File trong mỗi release

| File | Chạy trên | Ghi chú |
|---|---|---|
| `ctt-agent-aarch.tar.gz` | Mọi board aarch64 | `ctt-agent-aarch/ctt-agent`: static, đã strip. Chép lên board thành `/tmp/ctt-agent` rồi `chmod +x` |
| `ctt-agent-linux.tar.gz` | Linux / WSL x86_64 | `ctt-agent-linux/ctt-agent`: static |
| `ctt-agent-window.zip` | Windows | `ctt-agent-window/ctt-agent.exe` (console: menu và lệnh con, chạy được qua SSH) và `ctt-agent-UI.exe` (app cửa sổ: pair, throughput có biểu đồ, terminal) |
| `SHA256SUMS` | | Checksum của 3 gói trên |
| Source code (zip / tar.gz) | | GitHub tự thêm vào mỗi release: toàn bộ source tại commit của tag |

Chỉ có một chương trình tên `ctt-agent`; mỗi nền tảng một gói, giải nén ra thư mục cùng tên với gói (giống hệt `build/` khi tự build bằng `scripts/build.sh`).

## Hai loại release

| Loại | Tạo khi | Tên | Dùng cho |
|---|---|---|---|
| **Rolling** | Mỗi lần push lên `main` | `latest-main` (pre-release, bị thay mỗi lần push) | Thử bản mới nhất |
| **Chính thức** | Push tag `vX.Y.Z` | `vX.Y.Z` (giữ mãi) | Đem đi test, ghi vào báo cáo, giao cho người khác |

## Tạo release `vX.Y.Z`

Version nằm ở 3 chỗ: `linux-app/CMakeLists.txt` (agent), `pyproject.toml` và `window-app/src/ctt/__init__.py` (app Windows). Script [scripts/version.sh](../../scripts/version.sh) đổi cả ba cùng lúc. CI **từ chối** tạo release nếu tag không khớp version trong source.

```bash
git checkout main && git pull

# 1. Đổi version (ví dụ 0.2.0)
scripts/version.sh                 # xem version hiện tại
scripts/version.sh set 0.2.0

# 2. Build và test trên máy (khuyến nghị)
scripts/build.sh --clean      # build/ctt-agent-linux (+ test), build/ctt-agent-aarch, build/ctt-agent-window

# 3. Commit, tag, push
git commit -am "Release 0.2.0"
git tag -a v0.2.0 -m "CTT 0.2.0"
git push origin main
git push origin v0.2.0
```

Push tag sẽ chạy workflow. Theo dõi ở tab **Actions** (khoảng 10 phút). Khi xong, release `v0.2.0` có đủ các file ở bảng trên. Release notes gồm bảng file, cộng danh sách commit và PR từ tag trước (GitHub tự sinh).

Quy tắc đánh số (`MAJOR.MINOR.PATCH`):
- `PATCH` (0.2.**1**): sửa lỗi, không đổi cách dùng.
- `MINOR` (0.**3**.0): thêm tính năng, ví dụ test case hoặc lệnh mới.
- `MAJOR`: đổi giao thức CTP. Lúc đó agent board và app PC cũ không dùng chung được nữa.

### Làm lại một release bị lỗi

```bash
git push origin :refs/tags/v0.2.0  # xóa tag trên GitHub
git tag -d v0.2.0                  # xóa tag ở máy
# xóa release v0.2.0 trên trang Releases (nút Delete), sửa lỗi, commit, rồi tag + push lại
```

Nếu CI báo `version mismatch: tag says …` thì chạy `scripts/version.sh set …`, commit, rồi tag lại như trên.

## Tải về và kiểm tra

Trang Releases → chọn bản → mục **Assets**. Hoặc dùng link cố định:

```
https://github.com/PhanNgocTrieu/conn-tools/releases/download/v0.2.0/ctt-agent-aarch.tar.gz
https://github.com/PhanNgocTrieu/conn-tools/releases/latest/download/ctt-agent-window.zip        # bản chính thức mới nhất
https://github.com/PhanNgocTrieu/conn-tools/releases/download/latest-main/ctt-agent-linux.tar.gz  # bản mới nhất của main
```

Kiểm tra file không bị hỏng (để các gói và SHA256SUMS trong cùng thư mục):

```bash
sha256sum -c SHA256SUMS            # Linux / WSL: mỗi file in "OK"
```
```powershell
Get-FileHash .\ctt-agent-window.zip -Algorithm SHA256   # Windows: so với dòng tương ứng trong SHA256SUMS
```

Kiểm tra version:

```sh
/tmp/ctt-agent version             # board:   ctt-agent 0.2.0 (CTP v1, aarch64)
ctt-agent.exe --version    # Windows: ctt-agent 0.2.0
```

`ctt-agent-UI.exe` hiện version ở tiêu đề cửa sổ (`CTT Bluetooth Test 0.2.0`).

## Đưa bản release vào Yocto (máy build công ty)

Máy build công ty không lấy được gì từ GitHub, nên recipe dùng source chép sẵn. Ở máy có repo:

```bash
git checkout v0.2.0
scripts/export-recipe.sh <layer>/recipes-connectivity/ctt-agent   # PV = "0.2.0", SOURCE-COMMIT = commit của tag
```

Rồi commit thư mục đó vào git của layer. Chi tiết: [yocto-recipe.md](yocto-recipe.md).
