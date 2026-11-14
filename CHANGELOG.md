# Nhật ký thay đổi — TT_DesktopTool

Trước bản 1.4.0, phần mềm có tên **TT_UbuntuTools** (gói `tt-ubuntutools`).

## 1.4.0 — 2026-11-14

### Mới
- Đổi tên thành **TT_DesktopTool** (gói `tt-desktoptool`, lệnh `tt-desktoptool`). Gói mới thay thế `tt-ubuntutools`:
  cài bản mới bằng `apt` sẽ tự gỡ bản cũ; lịch sử gỡ / dọn / xoá cũ được chuyển sang tự động. Muốn xoá hẳn mục gói cũ
  còn sót (trạng thái "rc"): `sudo apt purge tt-ubuntutools`.
- Biểu tượng mới có dấu TT.
- Màn hình chào khi mở lần đầu: giới thiệu ba khu vực, có ô "Không hiện lại".
- Hộp Giới thiệu: dòng **Giấy phép** (mở tệp giấy phép) và khung **Miễn trừ trách nhiệm**; gói cài kèm `LICENSE.txt` và
  `MIEN-TRU-TRACH-NHIEM.txt`.

### Cải tiến
- Bỏ ba mục Gỡ cài đặt / Dọn dẹp / Dung lượng khỏi menu (đã có thẻ ở đầu cửa sổ; phím tắt vẫn dùng được).
- Tệp lịch sử chỉ chủ tài khoản đọc được.
- npm không còn tự kết nối Internet để kiểm tra bản mới khi phần mềm gọi nó.

### Sửa lỗi
- Bảo mật: khi quét Dọn dẹp, cấu hình đặt sẵn trong một repo git (vd. `core.fsmonitor` trong dự án tải về) không còn
  chạy được lệnh.
- Bảo mật: helper quản trị không ghi nhật ký vào thư mục log bị thay bằng liên kết tượng trưng.

## 1.3.1 — 2026-11-14

### Sửa lỗi
- Mô tả gói bị lỗi font (chữ có dấu hiện thành `?`) trong App Center: PackageKit đọc mô tả ở locale C, nên mô tả gói
  nay viết bằng tiếng Anh; giao diện vẫn tiếng Việt.

## 1.3.0 — 2026-11-14

### Mới
- Khu vực **Dung lượng** (Ctrl+3): duyệt thư mục như trình quản lý file, xem dung lượng và tỉ lệ % của từng thư mục /
  file (kể cả mục ẩn), tính dần khi mở từng thư mục; nhãn gợi ý theo 4 mức an toàn, nút "Mở ở tab Dọn dẹp".
- Chuyển mục lớn vào Thùng rác (chỉ trong thư mục nhà, chặn thư mục quan trọng, có Hoàn tác), ghi vào lịch sử.
- Phím tắt Ctrl+1 / Ctrl+2 / Ctrl+3 cho ba khu vực.

## 1.2.1 — 2026-11-10

### Cải tiến
- Hai khu vực ngang hàng "Gỡ cài đặt" / "Dọn dẹp" ở đầu cửa sổ, cùng bố cục 3 cột (nhóm · bảng · chi tiết).
- Bỏ dải nhắc dọn dẹp; bảng Dọn dẹp gọn lại, không còn tiêu đề cột bị che.

## 1.2.0 — 2026-11-10

### Mới
- Khu vực **Dọn dẹp** (Ctrl+K): bộ nhớ đệm công cụ, gói .deb đã tải, bản snap cũ, nhật ký journal, bộ nhớ đệm trình
  duyệt, tiện ích VS Code lỗi thời, node_modules / .venv / .next / __pycache__ của dự án và Thùng rác — phân loại theo
  mức an toàn bằng màu và ghi chú.

## 1.1.0 — 2026-11-10

### Mới
- Nhận ra phần mềm tự cài trong thư mục nhà (~/Programs, ~/Applications, ~/.local/share…, lệnh trong ~/.local/bin,
  lối tắt hỏng); chỉ nhận những gì khớp đúng mẫu của một bản cài.

### Cải tiến
- Mặc định chỉ gỡ lối tắt, lệnh khởi chạy, biểu tượng; thư mục chương trình chỉ vào Thùng rác khi người dùng tự bật.

## 1.0.0 — 2026-11-09

### Mới
- Thống kê phần mềm theo phương thức cài (APT, PPA / kho bên thứ ba, file .deb, Snap, Flatpak, AppImage, pip, npm, cài
  thủ công) và gỡ từng phần mềm với xem trước, purge / autoremove, luật bảo vệ hệ thống và lịch sử gỡ.
- Đóng góp dự án (Buy me a coffee): nút riêng trên thanh tiêu đề, hộp thoại có mã VietQR.

### Sửa lỗi
- Gỡ gói APT chạy dưới khoá dpkg của helper từ lúc kiểm tra tới lúc gỡ xong (không còn bị treo).
