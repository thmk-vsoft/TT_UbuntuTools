"""Luật "Chuyển vào Thùng rác" của tab Dung lượng: chỉ trong thư mục nhà, chặn thư mục quan trọng.

can_trash() được gọi khi hiển thị nút VÀ lại ngay trước khi chuyển (không tin kết quả cũ).
Mục được bảo vệ được so theo cả tên (tương đối với thư mục nhà) lẫn đường dẫn thật (khi ~/.ssh, ~/Videos…
là liên kết tượng trưng tới thư mục khác); thư mục CHA của một mục được bảo vệ cũng không được chuyển."""

from __future__ import annotations

import os
import re

from ..cleanup import scanners as sc
from ..utils import mount_points, mounts_under

XDG_DEFAULTS = ("Desktop", "Documents", "Downloads", "Pictures", "Music", "Videos", "Templates", "Public")
# Chính các thư mục này không được xoá (nội dung bên trong một số thư mục thì được — xem INSIDE_BLOCKED).
PROTECTED_EXACT = {
    ".config", ".ssh", ".gnupg", ".pki", ".local", ".local/share", ".local/bin", ".local/lib", ".local/state",
    ".local/share/applications", ".local/share/icons", ".local/share/fonts", ".local/share/keyrings",
    ".local/share/flatpak", ".local/share/gnome-shell", ".local/share/Trash", ".cache", "snap", ".var",
    ".mozilla", ".thunderbird", ".password-store", ".dbus", ".gvfs",
    # khoá / thông tin đăng nhập / ví / thư điện tử cục bộ
    ".aws", ".kube", ".docker", ".azure", ".netrc", ".git-credentials", ".electrum", ".bitcoin", ".ethereum",
    ".local/share/evolution", ".local/share/gnome-shell/extensions",
    # thông tin đăng nhập của Docker (thư mục máy ảo .docker/desktop lớn thì vẫn được xoá)
    ".docker/config.json", ".docker/ca.pem", ".docker/cert.pem", ".docker/key.pem", ".docker/contexts",
    ".docker/machine", ".docker/certs.d",
    # file đăng nhập / token / cấu hình shell & git
    ".npmrc", ".pypirc", ".vault-token", ".gitconfig", ".bashrc", ".profile", ".bash_profile", ".zshrc",
    ".gem/credentials", ".cargo/credentials", ".cargo/credentials.toml", ".m2/settings.xml", ".terraform.d",
    ".minikube/profiles", ".minikube/certs", ".minikube/ca.key", ".minikube/proxy-client-ca.key",
}
# Mọi thứ BÊN TRONG các thư mục này cũng không được xoá ở đây.
INSIDE_BLOCKED = (".config", ".ssh", ".gnupg", ".pki", ".password-store", ".local/share/keyrings",
                  ".local/share/Trash", ".local/share/flatpak", ".mozilla", ".thunderbird", "snap", ".var",
                  ".aws", ".kube", ".azure", ".electrum", ".bitcoin", ".ethereum", ".local/share/evolution",
                  ".docker/contexts", ".docker/machine", ".docker/certs.d", ".terraform.d", ".minikube/profiles",
                  ".minikube/certs")

_XDG_LINE = re.compile(r'^\s*(?:export\s+)?XDG_([A-Z]+)_DIR\s*=\s*(?:"((?:[^"\\]|\\.)*)"|([^\s#"]+))\s*(?:#.*)?$')


def outside(rel: str) -> bool:
    """relpath trỏ ra ngoài (không nhầm tên bắt đầu bằng '..' như '..data')."""
    return rel == ".." or rel.startswith("../")


def _real(p: str) -> str:
    try:
        return os.path.realpath(p)
    except (OSError, ValueError):
        return p


def xdg_user_dirs(home: str) -> dict[str, str]:
    """{TÊN: đường dẫn tuyệt đối} đọc từ ~/.config/user-dirs.dirs (kể cả tên đã dịch / đã dời chỗ).
    Bỏ giá trị bằng chính thư mục nhà (nghĩa là "không đặt")."""
    out: dict[str, str] = {}
    try:
        with open(os.path.join(home, ".config/user-dirs.dirs"), encoding="utf-8", errors="replace") as fh:
            for line in fh:
                m = _XDG_LINE.match(line)
                if not m:
                    continue
                val = m.group(2) if m.group(2) is not None else m.group(3)
                # Chỉ thay $HOME ở đầu (không bị thoát); rồi bỏ ký tự thoát kiểu shell (\$ \\ \` \")
                for pre in ("${HOME}", "$HOME"):
                    if val.startswith(pre):
                        val = home + val[len(pre):]
                        break
                val = re.sub(r"\\(.)", r"\1", val)
                if not val.startswith("/"):
                    continue
                val = os.path.normpath(val)
                if val != os.path.normpath(home):
                    out[m.group(1)] = val
    except OSError:
        pass
    return out


def xdg_dirs(home: str) -> set[str]:
    """Tên các thư mục người dùng (Desktop, Tải về, Tài liệu…, kể cả tên đã dịch) — tương đối với home thật.
    Giá trị ghi theo đường dẫn home là liên kết tượng trưng (/home/u -> /data/u) cũng được nhận ra."""
    out = set(XDG_DEFAULTS)
    real_home = _real(home)
    for val in xdg_user_dirs(home).values():
        for v in (val, _real(val)):
            rel = os.path.relpath(v, real_home)
            if not outside(rel) and rel != ".":
                out.add(rel)
    return out


def protected_paths(home: str, extra: tuple[str, ...] | list[str] = ()) -> tuple[set[str], set[str]]:
    """(mục không được chuyển — kèm mọi thư mục cha của chúng, thư mục mà mọi thứ bên trong bị chặn).
    Mỗi mục có cả dạng tên trong home lẫn dạng đường dẫn thật."""
    exact: set[str] = set()
    inside: set[str] = set()
    for rel in PROTECTED_EXACT | xdg_dirs(home):
        p = os.path.join(home, rel)
        exact.update((p, _real(p)))
    for val in xdg_user_dirs(home).values():  # thư mục người dùng đặt ở bất kỳ đâu
        exact.update((val, _real(val)))
    for p in extra:
        exact.update((os.path.normpath(p), _real(p)))
    for rel in INSIDE_BLOCKED:
        p = os.path.join(home, rel)
        inside.update((p, _real(p)))
    return exact, inside


def can_trash(path: str, home: str | None = None, locked_hint: bool = False,
              extra_protected: tuple[str, ...] | list[str] = ()) -> str:
    """'' nếu được chuyển `path` vào Thùng rác; ngược lại là lý do (tiếng Việt).
    extra_protected: thư mục cũng phải giữ (vd. thư mục của chương trình đã cài) — kể cả thư mục cha của chúng."""
    home = os.path.realpath(home or sc.HOME)
    norm = os.path.normpath(path)
    if norm != path or not path.startswith("/"):
        return "đường dẫn không hợp lệ"
    rel = os.path.relpath(norm, home)
    if rel == ".":
        return "đây là thư mục nhà của bạn"
    if outside(rel):
        return "nằm ngoài thư mục nhà — tab này chỉ xem, không xoá"
    parent = os.path.dirname(norm)
    if os.path.realpath(parent) != parent:
        return "nằm trong thư mục là liên kết tượng trưng"
    if not os.path.lexists(norm):
        return "không còn tồn tại"
    exact, inside = protected_paths(home, extra_protected)
    if norm in exact:
        if os.path.isdir(norm) and not os.path.islink(norm):
            return ("thư mục quan trọng của hệ thống / người dùng — không xoá cả thư mục "
                    "(xoá từng mục bên trong nếu được)")
        return "file cấu hình / đăng nhập quan trọng — không xoá"
    if any(p.startswith(norm + "/") for p in exact):
        return "bên trong có thư mục quan trọng (thư mục người dùng, cấu hình, khoá, chương trình đã cài…)"
    if any(norm.startswith(p + "/") for p in inside):
        return "nằm trong thư mục cấu hình / dữ liệu bảo mật — không xoá ở đây"
    if locked_hint:
        return "mục được bảo vệ (xem nhãn gợi ý)"
    is_dir = os.path.isdir(norm) and not os.path.islink(norm)
    if is_dir:
        if mounts_under(norm, mount_points()):
            return "có ổ đĩa gắn bên trong"
        try:
            if os.lstat(norm).st_dev != os.stat(home).st_dev:
                return "nằm trên ổ đĩa khác — Thùng rác không hỗ trợ"
        except OSError:
            return "không đọc được"
    if not os.access(parent, os.W_OK):
        return "không có quyền ghi vào thư mục chứa"
    return ""
