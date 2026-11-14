#!/usr/bin/env bash
# Đóng gói TT_DesktopTool thành file .deb:  bash packaging/build-deb.sh
set -euo pipefail
umask 022

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VERSION="$(python3 -I -B -c "import sys; sys.path.insert(0, '$ROOT'); import tt_ubuntutools as t; print(t.VERSION)")"
PKG="tt-desktoptool"
OUT="$ROOT/dist"
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT

echo "==> Dựng cây thư mục cho $PKG $VERSION"
LIB="$STAGE/usr/lib/$PKG"
DOC="$STAGE/usr/share/doc/$PKG"
install -d "$LIB" "$STAGE/usr/bin" "$STAGE/DEBIAN" "$DOC" \
  "$STAGE/usr/share/applications" \
  "$STAGE/usr/share/polkit-1/actions"
ICON_SIZES="16 24 32 48 64 128 256 512"

# Mã nguồn Python + tài nguyên (không kèm __pycache__)
(cd "$ROOT" && find tt_ubuntutools -type f \( -name '*.py' -o -name '*.png' -o -name '*.svg' \) \
    -not -path '*/__pycache__/*' -print0 | while IFS= read -r -d '' f; do
  install -D -m 0644 "$f" "$LIB/$f"
done)

install -m 0755 "$ROOT/helper/tt-desktoptool-helper" "$LIB/tt-desktoptool-helper"

cat > "$STAGE/usr/bin/$PKG" <<'EOF'
#!/bin/sh
exec /usr/bin/python3 -I -B -c 'import sys; sys.path.insert(0, "/usr/lib/tt-desktoptool"); from tt_ubuntutools.app import main; sys.exit(main(["tt-desktoptool"] + sys.argv[1:]))' "$@"
EOF
chmod 0755 "$STAGE/usr/bin/$PKG"

install -m 0644 "$ROOT/data/vn.tt.DesktopTool.desktop" "$STAGE/usr/share/applications/"
# Biểu tượng có dấu TT (assets/icon-tt/, tạo bằng add_tt_badge.py từ data/vn.tt.DesktopTool.svg)
for s in $ICON_SIZES; do
  install -D -m 0644 "$ROOT/assets/icon-tt/vn.tt.DesktopTool-$s.png" \
    "$STAGE/usr/share/icons/hicolor/${s}x${s}/apps/vn.tt.DesktopTool.png"
done
install -m 0644 "$ROOT/data/vn.tt.DesktopTool.policy" "$STAGE/usr/share/polkit-1/actions/"

install -m 0644 "$ROOT/LICENSE" "$DOC/copyright"
# Giấy phép và miễn trừ trách nhiệm đi cùng chương trình (hộp Giới thiệu mở các tệp này)
install -m 0644 "$ROOT/LICENSE" "$LIB/LICENSE.txt"
install -m 0644 "$ROOT/MIEN-TRU-TRACH-NHIEM.txt" "$LIB/MIEN-TRU-TRACH-NHIEM.txt"
cat > "$STAGE/changelog.tmp" <<EOF
$PKG ($VERSION) unstable; urgency=medium

  * Đổi tên thành TT_DesktopTool (gói tt-desktoptool, thay thế tt-ubuntutools;
    lịch sử cũ được chuyển sang tự động).
  * Biểu tượng mới có dấu TT; màn hình chào lần đầu; hộp Giới thiệu thêm dòng
    giấy phép và miễn trừ trách nhiệm.
  * Bỏ 3 mục chuyển khu vực khỏi menu (đã có thẻ ở đầu cửa sổ).
  * Bảo mật: không để cấu hình trong repo git chạy lệnh khi quét Dọn dẹp; helper
    không ghi nhật ký qua liên kết tượng trưng; lịch sử chỉ chủ tài khoản đọc
    được; npm không tự kết nối Internet để kiểm tra bản mới.

 -- THMK V-Soft <ttvn92i2026@gmail.com>  $(LC_ALL=C date -R)

$PKG (1.3.1) unstable; urgency=medium

  * Sửa mô tả gói bị lỗi font (dấu "?") trong App Center: PackageKit đọc mô tả
    ở locale C nên mô tả gói nay viết bằng tiếng Anh (giao diện vẫn tiếng Việt).
  * Cập nhật trang chủ sang kho thmk-vsoft/TT_UbuntuTools_Ubuntu.

 -- THMK V-Soft <ttvn92i2026@gmail.com>  Sat, 14 Nov 2026 08:06:00 +0700

$PKG (1.3.0) unstable; urgency=medium

  * Tab mới "Dung lượng" (Ctrl+3): duyệt thư mục như trình quản lý file, xem
    dung lượng và tỉ lệ % của từng thư mục / file (kể cả mục ẩn), tính dần khi
    mở từng thư mục. Nhãn gợi ý theo 4 mức an toàn, nút "Mở ở tab Dọn dẹp".
  * Chuyển mục lớn vào Thùng rác (chỉ trong thư mục nhà, chặn thư mục quan
    trọng, có Hoàn tác), ghi vào lịch sử.
  * Phím tắt Ctrl+1 / Ctrl+2 / Ctrl+3 cho ba khu vực.

 -- THMK V-Soft <ttvn92i2026@gmail.com>  Sat, 14 Nov 2026 07:53:00 +0700

$PKG (1.2.1) unstable; urgency=medium

  * Bố cục lại: hai khu vực ngang hàng "Gỡ cài đặt" / "Dọn dẹp" ở đầu cửa sổ,
    cùng kiểu 3 cột (nhóm · bảng · chi tiết).
  * Bỏ dải nhắc dọn dẹp; bảng Dọn dẹp gọn lại (cột "Ngày sửa"), không còn
    tiêu đề cột bị che.

 -- THMK V-Soft <ttvn92i2026@gmail.com>  Tue, 10 Nov 2026 08:10:00 +0700

$PKG (1.2.0) unstable; urgency=medium

  * Mục Dọn dẹp (Ctrl+K): bộ nhớ đệm công cụ, gói .deb đã tải, bản snap cũ,
    nhật ký journal, bộ nhớ đệm trình duyệt, tiện ích VS Code lỗi thời,
    node_modules / .venv / .next / __pycache__ của dự án và Thùng rác — phân
    loại theo mức an toàn bằng màu và ghi chú; chỉ chọn sẵn mục An toàn.
  * Helper thêm apt-clean, journal-vacuum, snap-remove-revision (chỉ gỡ bản
    snap đang tắt).

 -- THMK V-Soft <ttvn92i2026@gmail.com>  Tue, 10 Nov 2026 08:00:00 +0700

$PKG (1.1.0) unstable; urgency=medium

  * Nhận ra phần mềm tự cài trong thư mục nhà (~/Programs, ~/Applications,
    ~/.local/share..., lệnh trong ~/.local/bin, lối tắt hỏng) — vd. bản GomGo
    thứ hai còn sót sau khi gỡ gói .deb. Chỉ nhận những gì khớp đúng mẫu của
    một bản cài; dự án / dữ liệu / runtime dùng chung chỉ được liệt kê.
  * Mặc định chỉ gỡ lối tắt, lệnh khởi chạy, biểu tượng; thư mục chương trình
    chỉ vào Thùng rác khi người dùng tự bật tuỳ chọn và xem nội dung.
  * Chuyển vào Thùng rác chạy nền (không đứng giao diện); cảnh báo chương
    trình đang chạy; lịch sử ghi rõ tuỳ chọn và phần được giữ lại.

 -- THMK V-Soft <ttvn92i2026@gmail.com>  Tue, 10 Nov 2026 06:35:04 +0700

$PKG (1.0.0) unstable; urgency=medium

  * Bản phát hành 1.0: thống kê phần mềm theo phương thức cài (APT, PPA /
    kho bên thứ ba, file .deb, Snap, Flatpak, AppImage, pip, npm, cài thủ
    công) và gỡ từng phần mềm với xem trước, purge / autoremove, luật bảo vệ
    hệ thống và lịch sử gỡ.
  * Gỡ gói APT chạy dưới khoá dpkg của helper suốt từ lúc kiểm tra tới lúc
    gỡ xong; chặn khi dpkg bị ngắt giữa chừng hoặc gói đang bị apt-mark hold.
  * Đóng góp dự án (Buy me a coffee): nút riêng trên thanh tiêu đề, hộp thoại có mã VietQR.

 -- THMK V-Soft <ttvn92i2026@gmail.com>  Mon, 09 Nov 2026 15:40:00 +0700
EOF
gzip -9n -c "$STAGE/changelog.tmp" > "$DOC/changelog.Debian.gz"
rm "$STAGE/changelog.tmp"
chmod 0644 "$DOC/changelog.Debian.gz"

# Gỡ gói: dọn __pycache__ nếu có ai đó đã chạy mã bằng quyền root.
cat > "$STAGE/DEBIAN/prerm" <<'EOF'
#!/bin/sh
set -e
if [ -d /usr/lib/tt-desktoptool ]; then
  find /usr/lib/tt-desktoptool -depth -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
fi
exit 0
EOF
chmod 0755 "$STAGE/DEBIAN/prerm"

# Purge gói: xoá nhật ký của helper.
cat > "$STAGE/DEBIAN/postrm" <<'EOF'
#!/bin/sh
set -e
if [ "$1" = "purge" ]; then
  rm -rf /var/log/tt-desktoptool /var/log/tt-ubuntutools
fi
exit 0
EOF
chmod 0755 "$STAGE/DEBIAN/postrm"

# Cài / nâng cấp: gói cũ tt-ubuntutools (trước 1.4.0) chỉ bị gỡ chứ không purge -> dọn nhật ký cũ của nó.
cat > "$STAGE/DEBIAN/postinst" <<'EOF'
#!/bin/sh
set -e
if [ "$1" = "configure" ] && [ -d /var/log/tt-ubuntutools ] && [ ! -L /var/log/tt-ubuntutools ]; then
  rm -rf /var/log/tt-ubuntutools
fi
exit 0
EOF
chmod 0755 "$STAGE/DEBIAN/postinst"

# Quyền chuẩn: thư mục 755, file 644 (trừ file thực thi)
chmod 0755 "$STAGE"
find "$STAGE" -type d -exec chmod 0755 {} +
find "$STAGE" -type f ! -perm -u+x -exec chmod 0644 {} +

(cd "$STAGE" && find . -type f ! -path './DEBIAN/*' -printf '%P\0' | sort -z | xargs -0 md5sum) \
  > "$STAGE/DEBIAN/md5sums"
chmod 0644 "$STAGE/DEBIAN/md5sums"

INSTALLED_SIZE="$(du -sk --exclude=DEBIAN "$STAGE" | cut -f1)"
cat > "$STAGE/DEBIAN/control" <<EOF
Package: $PKG
Version: $VERSION
Section: admin
Priority: optional
Architecture: all
Installed-Size: $INSTALLED_SIZE
Depends: python3 (>= 3.10), python3-gi, gir1.2-gtk-4.0 (>= 4.12), gir1.2-adw-1 (>= 1.5), python3-apt, pkexec | policykit-1
Suggests: snapd, flatpak, python3-pip
Provides: tt-ubuntutools
Replaces: tt-ubuntutools
Conflicts: tt-ubuntutools
Maintainer: THMK V-Soft <ttvn92i2026@gmail.com>
Homepage: https://github.com/thmk-vsoft/TT_DesktopTool_Ubuntu
Description: List, uninstall software and clean up disk space on Ubuntu
 TT_DesktopTool lists installed software by install method (APT, PPA and
 third-party repositories, local .deb files, Snap, Flatpak, AppImage, pip,
 npm, manual installs in /opt and self-installed apps in the home folder) and
 uninstalls one item at a time with a preview, system protection rules and a
 removal history.
 .
 The Cleanup section finds caches, downloaded .deb packages, old snap
 revisions, node_modules, .venv and more, grouped by safety level. The Disk
 usage section browses folders like a file manager, shows which folders and
 files take the most space and safely moves large items to the Trash.
 .
 The user interface is in Vietnamese.
EOF

# PackageKit / App Center chạy ở locale C và đổi mọi ký tự ngoài ASCII trong mô tả gói thành "?"
# -> trường control phải là ASCII (giao diện trong app vẫn tiếng Việt).
if LC_ALL=C grep -n '[^ -~]' "$STAGE/DEBIAN/control"; then
  echo "LỖI: DEBIAN/control có ký tự ngoài ASCII (App Center sẽ hiện thành '?')" >&2
  exit 1
fi

mkdir -p "$OUT"
# Bản cũ chuyển vào dist/archive/<phiên-bản>/ (không xoá)
for old in "$OUT"/${PKG}_*_all.deb* "$OUT"/${PKG}-*-linux-x64.deb*; do
  [ -e "$old" ] || continue
  case "$(basename "$old")" in "${PKG}-${VERSION}-linux-x64.deb"*) continue ;; esac
  v="$(basename "$old" | sed -E "s/^${PKG}[_-]([0-9][0-9.]*[0-9])[_-].*/\1/")"
  mkdir -p "$OUT/archive/$v" && mv "$old" "$OUT/archive/$v/"
done
DEB="$OUT/${PKG}-${VERSION}-linux-x64.deb"
dpkg-deb --build --root-owner-group "$STAGE" "$DEB" >/dev/null
# Tên tệp phải khớp phiên bản bên trong gói
[ "$(dpkg-deb -f "$DEB" Version)" = "$VERSION" ] || { echo "LỖI: phiên bản trong gói khác tên tệp" >&2; exit 1; }
(cd "$OUT" && sha256sum -b "$(basename "$DEB")" > SHA256SUMS.txt)
echo "==> Đã tạo $DEB"
cat "$OUT/SHA256SUMS.txt"
