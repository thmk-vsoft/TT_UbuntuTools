"""Nhãn gợi ý cho thư mục quen thuộc — dùng chung 4 mức an toàn của mục Dọn dẹp."""

from __future__ import annotations

import functools
import os
from dataclasses import dataclass

from ..cleanup import scanners as sc


@dataclass
class Hint:
    level: str          # sc.SAFE / REVIEW / IRREVERSIBLE / LOCKED
    note: str
    link: str = ""      # "cleanup:<nhóm>" -> nút "Mở ở tab Dọn dẹp"; "software" -> tab Gỡ cài đặt


TOOL_CACHES = {".cache/pip": "pip", ".cache/uv": "uv", ".cache/pnpm": "pnpm", ".cache/yarn": "yarn",
               ".npm/_cacache": "npm", ".npm": "npm"}
BROWSER_CACHES = {".cache/google-chrome": "Google Chrome", ".cache/chromium": "Chromium",
                  ".cache/microsoft-edge": "Microsoft Edge", ".cache/BraveSoftware": "Brave"}
LOCKED_EXACT = {
    ".config": "Cấu hình của mọi ứng dụng — xoá sẽ mất thiết lập, tài khoản đăng nhập… Không xoá ở đây.",
    ".ssh": "Khoá SSH của bạn — không xoá.",
    ".gnupg": "Khoá GPG của bạn — không xoá.",
    ".pki": "Chứng chỉ của trình duyệt / hệ thống — không xoá.",
    ".local": "Dữ liệu và chương trình của bạn (~/.local) — không xoá cả thư mục.",
    ".local/share": "Dữ liệu của mọi ứng dụng — không xoá cả thư mục; xem từng mục bên trong.",
    ".local/bin": "Lệnh bạn đã cài cho riêng mình — gỡ từng chương trình ở tab Gỡ cài đặt.",
    ".local/state": "Trạng thái của ứng dụng — không xoá.",
    ".local/share/keyrings": "Kho mật khẩu của hệ thống — không xoá.",
    ".local/share/applications": "Lối tắt menu của bạn — gỡ từng chương trình ở tab Gỡ cài đặt.",
    ".local/share/flatpak": "Ứng dụng Flatpak cài cho riêng bạn — gỡ ở tab Gỡ cài đặt.",
    "snap": "Dữ liệu của các ứng dụng Snap — gỡ ứng dụng ở tab Gỡ cài đặt.",
    ".var": "Dữ liệu của các ứng dụng Flatpak — gỡ ứng dụng ở tab Gỡ cài đặt.",
    ".mozilla": "Hồ sơ Firefox / Thunderbird (dấu trang, mật khẩu…) — không xoá.",
    ".thunderbird": "Thư điện tử Thunderbird — không xoá.",
    ".password-store": "Kho mật khẩu pass — không xoá.",
    ".aws": "Khoá truy cập AWS — không xoá.",
    ".kube": "Cấu hình và khoá truy cập Kubernetes — không xoá.",
    ".azure": "Thông tin đăng nhập Azure — không xoá.",
    ".docker": "Cấu hình và đăng nhập Docker — không xoá cả thư mục.",
    ".netrc": "Mật khẩu đăng nhập máy chủ (netrc) — không xoá.",
    ".git-credentials": "Mật khẩu / token Git đã lưu — không xoá.",
    ".electrum": "Ví tiền mã hoá Electrum — xoá có thể mất tiền vĩnh viễn.",
    ".bitcoin": "Ví / dữ liệu Bitcoin — xoá có thể mất tiền vĩnh viễn.",
    ".ethereum": "Ví / dữ liệu Ethereum — xoá có thể mất tiền vĩnh viễn.",
    ".local/share/evolution": "Thư điện tử Evolution trên máy — không xoá.",
    ".local/share/gnome-shell": "Tiện ích mở rộng và dữ liệu của GNOME Shell — không xoá cả thư mục.",
    ".local/share/gnome-shell/extensions": "Tiện ích mở rộng GNOME — gỡ từng cái trong ứng dụng Extensions.",
    ".docker/config.json": "Thông tin đăng nhập kho Docker — không xoá.",
    ".docker/contexts": "Cấu hình kết nối Docker — không xoá.",
    ".docker/machine": "Máy Docker và khoá của chúng — không xoá.",
    ".docker/certs.d": "Chứng chỉ kho Docker — không xoá.",
    ".docker/ca.pem": "Chứng chỉ Docker — không xoá.",
    ".docker/cert.pem": "Chứng chỉ Docker — không xoá.",
    ".docker/key.pem": "Khoá Docker — không xoá.",
    ".npmrc": "Cấu hình và token đăng nhập npm — không xoá.",
    ".pypirc": "Mật khẩu / token PyPI — không xoá.",
    ".vault-token": "Token Vault — không xoá.",
    ".gitconfig": "Cấu hình Git của bạn (tên, email…) — không xoá.",
    ".bashrc": "Cấu hình terminal (bash) — không xoá.",
    ".profile": "Cấu hình đăng nhập — không xoá.",
    ".bash_profile": "Cấu hình terminal (bash) — không xoá.",
    ".zshrc": "Cấu hình terminal (zsh) — không xoá.",
    ".gem/credentials": "Khoá đăng nhập RubyGems — không xoá.",
    ".cargo/credentials": "Token crates.io — không xoá.",
    ".cargo/credentials.toml": "Token crates.io — không xoá.",
    ".m2/settings.xml": "Cấu hình và mật khẩu Maven — không xoá.",
    ".terraform.d": "Thông tin đăng nhập Terraform — không xoá.",
    ".minikube/profiles": "Cấu hình và khoá các cụm minikube — không xoá.",
    ".minikube/certs": "Chứng chỉ minikube — không xoá.",
}


@functools.lru_cache(maxsize=8)
def _realhome(home: str) -> str:
    return os.path.realpath(home)


def _rel(path: str, home: str) -> str | None:
    rel = os.path.relpath(path, home)
    return None if (rel == ".." or rel.startswith("../")) else ("" if rel == "." else rel)


def hint_for(path: str, is_dir: bool, installed_roots: dict[str, str] | None = None,
             home: str | None = None) -> Hint | None:
    """Nhãn cho path (None nếu không quen). installed_roots: {thư mục chương trình đã cài: tên}."""
    home = _realhome(home or sc.HOME)
    if path != home and not path.startswith(home + "/") and not path.startswith(("/var/cache/apt", "/var/log",
                                                                                   "/var/lib/snapd")):
        return None  # nhanh: ngoài thư mục nhà và không phải thư mục hệ thống đã biết
    rel = _rel(path, home)
    name = os.path.basename(path)
    if path in ("/var/cache/apt/archives", "/var/cache/apt"):
        return Hint(sc.SAFE, "Gói .deb apt đã tải — dọn an toàn ở tab Dọn dẹp (cần mật khẩu).", "cleanup:system")
    if path in ("/var/log/journal", "/var/log"):
        return Hint(sc.REVIEW, "Nhật ký hệ thống — thu gọn journal ở tab Dọn dẹp, không xoá tay.", "cleanup:system")
    if path in ("/var/lib/snapd/snaps", "/var/lib/snapd"):
        return Hint(sc.REVIEW, "Gồm cả bản snap đang dùng — chỉ dọn bản cũ ở tab Dọn dẹp.", "cleanup:system")
    if rel is None:
        return None
    if rel == "":
        return Hint(sc.LOCKED, "Thư mục nhà của bạn.")
    for root, title in (installed_roots or {}).items():
        if path == root:
            return Hint(sc.LOCKED, f"Thư mục của chương trình đã cài ({title}) — gỡ ở tab Gỡ cài đặt.", "software")
    if rel in LOCKED_EXACT:
        link = "software" if "Gỡ cài đặt" in LOCKED_EXACT[rel] else ""
        return Hint(sc.LOCKED, LOCKED_EXACT[rel], link)
    if rel.startswith((".config/", ".ssh/", ".gnupg/", ".local/share/keyrings/", ".password-store/", ".aws/",
                       ".kube/", ".azure/", ".electrum/", ".bitcoin/", ".ethereum/", ".local/share/evolution/",
                       ".docker/contexts/", ".docker/machine/", ".docker/certs.d/", ".terraform.d/",
                       ".minikube/profiles/", ".minikube/certs/")):
        return Hint(sc.LOCKED, "Nằm trong thư mục cấu hình / khoá bảo mật — không xoá ở đây.")
    if rel == ".local/share/Trash" or rel.startswith(".local/share/Trash/"):
        return Hint(sc.IRREVERSIBLE, "Thùng rác — dọn ở tab Dọn dẹp (xoá là mất hẳn).", "cleanup:project")
    if rel == ".cache":
        return Hint(sc.REVIEW, "Bộ nhớ đệm của mọi ứng dụng — dọn từng phần an toàn ở tab Dọn dẹp.", "cleanup:cache")
    if rel in TOOL_CACHES:
        return Hint(sc.SAFE, f"Bộ nhớ đệm {TOOL_CACHES[rel]} — dọn an toàn ở tab Dọn dẹp.", "cleanup:cache")
    if rel in BROWSER_CACHES:
        return Hint(sc.REVIEW, f"Bộ nhớ đệm {BROWSER_CACHES[rel]} — dọn ở tab Dọn dẹp khi trình duyệt đóng.",
                    "cleanup:browser")
    if is_dir and name == "node_modules" and not sc.is_node_modules(path):
        return Hint(sc.REVIEW, "node_modules của dự án — tạo lại bằng npm install; dọn ở tab Dọn dẹp.",
                    "cleanup:project")
    if is_dir and name in sc.VENV_NAMES and os.path.isfile(os.path.join(path, "pyvenv.cfg")):
        why = sc.is_project_venv(path)
        if not why:
            return Hint(sc.REVIEW, "Môi trường ảo Python — tạo lại được từ requirements.txt / pyproject.toml; "
                                   "dọn ở tab Dọn dẹp.", "cleanup:project")
        return Hint(sc.REVIEW, f"Môi trường ảo Python — Dọn dẹp không tự dọn vì {why}.")
    if is_dir and name in sc.BUILD_DIRS and not sc.is_build_dir(path):
        return Hint(sc.REVIEW, "Thư mục build — tạo lại khi build / chạy dev.", "cleanup:project")
    if is_dir and name == "__pycache__":
        return Hint(sc.SAFE, "Cache Python — tự tạo lại khi chạy.", "cleanup:project")
    return None
