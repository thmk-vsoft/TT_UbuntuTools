# TT_DesktopTool

Công cụ đồ hoạ (GTK4 / libadwaita) dành cho Ubuntu, gồm ba khu vực: **Gỡ cài đặt** (thống kê phần mềm đã cài theo phương thức cài và gỡ phần mềm không cần thiết một cách an toàn), **Dọn dẹp** (dữ liệu thừa, phân loại theo mức an toàn) và **Dung lượng** (thư mục / file nào chiếm nhiều chỗ).

> Trước bản 1.4.0, phần mềm có tên **TT_UbuntuTools** (gói `tt-ubuntutools`). Gói mới `tt-desktoptool` thay thế gói cũ; lịch sử cũ được chuyển sang tự động.

- Bản quyền phần mềm: **THMK V-Soft** · Liên hệ: **ttvn92i2026@gmail.com**
- **Buy me a coffee ☕**: ủng hộ dự án nếu bạn thấy phần mềm hữu ích — nút **Đóng góp dự án** trong phần mềm.

## Tính năng
- Nguồn: APT (kho Ubuntu) · PPA / kho bên thứ ba · file `.deb` cài tay · Snap · Flatpak · AppImage · pip/pipx · npm toàn cục · cài thủ công (`/opt`, `/usr/local/bin`) · **phần mềm tự cài trong thư mục nhà** (`~/Programs`, `~/Applications`, `~/opt`, `~/.local/share`… kèm lối tắt và lệnh trong `~/.local/bin`). Nguồn nào máy không có thì tự bỏ qua.
- Cột: tên + biểu tượng + mô tả, phương thức, phiên bản, dung lượng, ngày cài, nguồn kho. Sắp xếp theo cột, tìm kiếm, lọc theo nguồn.
- Chế độ **Ứng dụng** (mặc định: có lối tắt menu hoặc do bạn tự cài) / **Tất cả gói**.
- Gỡ **từng** phần mềm: xem trước toàn bộ gói bị gỡ theo, tuỳ chọn purge / autoremove, báo kết quả và kiểm tra lại, lưu **lịch sử** (`~/.local/share/tt-desktoptool/history.jsonl`, chỉ chủ tài khoản đọc được).

- **Dọn dẹp** (Ctrl+K): tìm dữ liệu tạo lại được hoặc không còn dùng và cho bạn chọn xoá, mỗi mục có **nhãn màu theo mức an toàn** và ghi chú điều gì xảy ra sau khi xoá:
  - 🟢 **An toàn** (chọn sẵn): bộ nhớ đệm pip / uv / npm / yarn / pnpm, `__pycache__`, gói `.deb` apt đã tải, bản snap cũ đang tắt, nhật ký journal cũ (giữ 100 MB), bản tiện ích VS Code mà VS Code đã đánh dấu lỗi thời.
  - 🟡 **Cân nhắc**: `node_modules`, `.venv`, `.next`… của dự án (kèm ngày sửa gần nhất và lệnh cài lại), bộ nhớ đệm trình duyệt.
  - 🔴 **Không khôi phục được**: Thùng rác (phải tích thêm ô xác nhận).
  - ⚪ **Bị khoá** (kèm lý do): thư mục bên trong ứng dụng (`~/.vscode`, `~/.config`…), `node_modules` không có lockfile, `.venv` không có `requirements.txt` / `pyproject.toml`, môi trường của chương trình đã cài, trình duyệt đang mở, thư mục đang được chương trình chạy dùng.
  Thư mục dự án được xoá vĩnh viễn để giải phóng chỗ ngay; mỗi đường dẫn được kiểm tra lại ngay trước khi xoá. Việc cần quyền quản trị (snap cũ, apt, journal) đi qua helper và chỉ hỏi mật khẩu một lần.

- **Dung lượng** (Ctrl+3): xem thư mục / file nào chiếm nhiều chỗ, giống trình quản lý file:
  - Lối tắt Thư mục nhà · Màn hình nền · Tài liệu · Tải về · Toàn bộ ổ đĩa (`/`) · Chọn thư mục…; thanh dung lượng ổ đĩa.
  - Bảng: tên, **tỉ lệ %** (so với thư mục đang xem, có thanh màu), dung lượng, số file, ngày sửa; hiện cả **mục ẩn** (bật/tắt được); nhấn đúp để vào trong, Backspace / Alt+← để lên.
  - Dung lượng được tính dần khi mở từng thư mục (dung lượng thật trên đĩa như `du`, không đi theo liên kết tượng trưng, không sang ổ khác), nhớ trong phiên; ⟳ để quét lại.
  - Nhãn gợi ý theo 4 mức an toàn của mục Dọn dẹp, kèm nút **Mở ở tab Dọn dẹp**.
  - **Chuyển vào Thùng rác** (có Hoàn tác): chỉ trong thư mục nhà; không cho xoá chính thư mục nhà, các thư mục người dùng (Tài liệu, Tải về…), `.config`, `.ssh`, `.gnupg`, `.local`, `.cache`, `snap`, `.var`, hồ sơ trình duyệt… và mọi thứ bên trong thư mục cấu hình / khoá bảo mật. Ngoài thư mục nhà chỉ để xem.

## An toàn
- **Luật bảo vệ** chặn gỡ: gói Essential / required / important; kernel đang chạy và gói kernel chính; bootloader, công cụ initramfs (dracut, initramfs-tools), công cụ ổ đĩa (cryptsetup, lvm2, mdadm, zfs, btrfs); firmware; mạng; desktop và màn hình đăng nhập của GNOME, KDE, Xfce, LXQt, MATE, Cinnamon, Budgie; trình đăng nhập và các phiên desktop đã cài trên chính máy bạn; polkit agent đang chạy; SSH / RDP / chia sẻ màn hình GNOME khi dịch vụ đó đang bật hoặc đang có phiên điều khiển từ xa; nền tảng snap (snapd, apparmor); snap nền / snap dùng chung; thư mục runtime của dịch vụ và chương trình đang được dịch vụ systemd dùng.
- Purge các phần mềm máy chủ có dữ liệu (PostgreSQL, MySQL/MariaDB, Docker…) bị chặn để tránh mất dữ liệu.
- Thao tác cần quyền quản trị chạy qua `pkexec` + helper chỉ nhận một số hành động cố định. Helper **kiểm tra lại** mọi thứ bằng quyền root ngay trước khi gỡ: apt chỉ chạy khi danh sách gói bị gỡ **khớp đúng** danh sách bạn đã xem trước và không cài thêm gói nào; không bao giờ xoá xuyên qua ổ đĩa/ổ mạng được gắn vào.
- Mỗi lần gỡ đều hỏi mật khẩu (không nhớ quyền giữa các lần); hộp thoại mật khẩu ghi rõ lệnh sẽ chạy. Cần một polkit agent (có sẵn trong GNOME, KDE, Xfce…). Riêng flatpak cài cho toàn hệ thống do flatpak tự hỏi quyền theo quy tắc riêng.
- Helper giữ khoá của dpkg từ lúc kiểm tra tới lúc gỡ xong, nên chương trình cập nhật tự động không chen vào làm sai kết quả xem trước.
- Trong lúc đang gỡ, ứng dụng không cho thoát và xin hệ thống hoãn đăng xuất / ngủ; nếu cửa sổ vẫn bị đóng bất thường, helper tiếp tục chạy apt/dpkg tới cùng và ghi nhật ký vào `/var/log/tt-desktoptool/`.
- File trong thư mục người dùng (AppImage…) được chuyển vào **Thùng rác**; mục trong `/opt` bị xoá vĩnh viễn (có cảnh báo trước).
- Phần mềm tự cài trong thư mục nhà: chỉ nhận ra những gì khớp **đúng mẫu** của một bản cài (file chạy ở `<thư mục>/`, `<thư mục>/bin/` hoặc `.venv/bin/`, là chương trình đã biên dịch / AppImage, không có `.git` hay mã nguồn, script khởi chạy dạng đơn giản…). Lối tắt trỏ vào dự án, dữ liệu hay runtime dùng chung của bạn chỉ được **liệt kê kèm lý do**, không bao giờ bị gỡ. Mặc định chỉ gỡ lối tắt, lệnh khởi chạy và biểu tượng; **thư mục chương trình chỉ vào Thùng rác khi bạn tự bật tuỳ chọn** sau khi xem danh sách nội dung của nó.

> Công cụ gỡ phần mềm khỏi hệ thống. Hãy luôn đọc kỹ danh sách ở bước xem trước trước khi bấm **Gỡ cài đặt**.

## Yêu cầu
Ubuntu **24.04 LTS trở lên** (libadwaita ≥ 1.5, GTK ≥ 4.12), Python 3.10+. Đã thử trên Ubuntu 26.04 GNOME.

## Cài đặt
Tải file `tt-desktoptool-<phiên bản>-linux-x64.deb` và `SHA256SUMS` ở trang phát hành, kiểm tra mã SHA-256 rồi cài trong Terminal:
```bash
sha256sum -c SHA256SUMS --ignore-missing
```
```bash
sudo apt install ./tt-desktoptool-1.4.0-linux-x64.deb
```
Máy đã có bản cũ (kể cả `tt-ubuntutools`) thì cũng nâng cấp bằng lệnh trên — App Center chỉ báo "Installed" chứ không nâng cấp. Sau khi nâng cấp từ `tt-ubuntutools`, có thể xoá hẳn mục cũ còn sót trong danh sách gói:
```bash
sudo apt purge tt-ubuntutools
```
Sau khi cài, mở **TT_DesktopTool** từ menu ứng dụng hoặc chạy `tt-desktoptool`. Không chạy bằng `sudo`. Lần đầu mở có màn hình chào giới thiệu ba khu vực.

## Tự đóng gói từ mã nguồn
```bash
bash packaging/build-deb.sh
```

## Dành cho nhà phát triển
Chạy thử giao diện từ mã nguồn (thao tác cần quyền quản trị chỉ hoạt động khi đã cài file .deb; thao tác không cần root như chuyển AppImage vào Thùng rác vẫn chạy):
```bash
python3 -m tt_ubuntutools
```
Để thử gỡ bằng helper ngay trong thư mục mã nguồn (chỉ khi phát triển — helper sẽ chạy mã trong thư mục của bạn bằng quyền root, đừng dùng trên máy dùng chung):
```bash
TT_DEV_HELPER=1 python3 -m tt_ubuntutools
```
Chạy kiểm thử:
```bash
python3 -m unittest discover -s tests -t .
```

## Cấu trúc
- `tt_ubuntutools/collectors/` — mỗi nguồn cài đặt một bộ thu thập
- `tt_ubuntutools/removal/` — lập kế hoạch gỡ, luật bảo vệ, luật đường dẫn, chạy gỡ
- `tt_ubuntutools/cleanup/` — quét và lập kế hoạch dọn dẹp
- `tt_ubuntutools/diskusage/` — đo dung lượng, nhãn gợi ý, luật chuyển vào Thùng rác của tab Dung lượng
- `tt_ubuntutools/ui/` — giao diện (kèm phần Đóng góp dự án theo DonateKit)
- `tt_ubuntutools/` — mã nguồn (tên module giữ từ tên cũ)
- `helper/tt-desktoptool-helper` — helper chạy bằng quyền root qua pkexec
- `data/` — file `.desktop`, biểu tượng gốc (SVG), polkit policy
- `assets/icon-tt/` — biểu tượng có dấu TT ở các cỡ (tạo từ `data/vn.tt.DesktopTool.svg`)

## Giấy phép và miễn trừ trách nhiệm
- Giấy phép: **Độc quyền** — © 2026 THMK V-Soft. Bảo lưu mọi quyền. Xem [LICENSE](LICENSE).
- Phần mềm được cung cấp "nguyên trạng" — xem [MIEN-TRU-TRACH-NHIEM.txt](MIEN-TRU-TRACH-NHIEM.txt).
- Nhật ký thay đổi: [CHANGELOG.md](CHANGELOG.md).

## Nguồn tài nguyên
- Biểu tượng phần mềm: tự vẽ (THMK V-Soft); dấu TT và mã VietQR thuộc bộ kit THMK V-Soft.
- Biểu tượng trái tim của nút Đóng góp dự án: theo mẫu DonateKit (hình "heart" của bộ Lucide, giấy phép ISC).
