"""Kiểu dữ liệu dùng chung."""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field
from enum import Enum


class Source(Enum):
    """Phương thức cài đặt. value = (mã, nhãn tiếng Việt, lớp CSS badge)."""

    APT = ("apt", "APT (kho Ubuntu)", "src-apt")
    THIRD_PARTY = ("third_party", "PPA / Kho bên thứ ba", "src-thirdparty")
    DEB = ("deb", "File .deb", "src-deb")
    SNAP = ("snap", "Snap", "src-snap")
    FLATPAK = ("flatpak", "Flatpak", "src-flatpak")
    APPIMAGE = ("appimage", "AppImage", "src-appimage")
    PIP = ("pip", "pip / pipx", "src-pip")
    NPM = ("npm", "npm", "src-npm")
    MANUAL = ("manual", "Cài thủ công", "src-manual")

    @property
    def code(self) -> str:
        return self.value[0]

    @property
    def label(self) -> str:
        return self.value[1]

    @property
    def css_class(self) -> str:
        return self.value[2]

    @classmethod
    def from_code(cls, code: str) -> "Source":
        for s in cls:
            if s.code == code:
                return s
        raise ValueError(code)


@dataclass
class SoftwareItem:
    source: Source
    id: str                      # khoá duy nhất trong nguồn: tên gói / tên snap / đường dẫn ...
    name: str                    # tên kỹ thuật (tên gói, tên snap, tên file)
    display_name: str = ""
    version: str = ""
    size: int | None = None      # byte
    install_date: _dt.datetime | None = None
    origin: str = ""             # nguồn kho chi tiết
    summary: str = ""
    description: str = ""
    icon: str = ""               # tên icon theme hoặc đường dẫn file
    is_app: bool = False         # có lối tắt .desktop hiển thị
    manual: bool = False         # người dùng tự cài (không phải phụ thuộc tự động)
    path: str = ""               # đường dẫn (AppImage, thủ công)
    protected_reason: str = ""   # khác rỗng => không cho gỡ
    extra: dict = field(default_factory=dict)

    def __post_init__(self):
        # Trường chỉ để hiển thị: làm sạch byte không phải UTF-8 (tên file Windows...).
        # id và path giữ nguyên để thao tác với hệ thống file.
        from .utils import display

        for f in ("name", "display_name", "version", "origin", "summary", "description", "icon",
                  "protected_reason"):
            setattr(self, f, display(getattr(self, f)))

    @property
    def key(self) -> str:
        return f"{self.source.code}:{self.id}"

    @property
    def title(self) -> str:
        return self.display_name or self.name

    @property
    def visible_as_app(self) -> bool:
        """Có hiện ở chế độ 'Chỉ ứng dụng' không."""
        if self.source in (Source.APT,):
            return self.is_app or self.manual
        if self.source == Source.SNAP:
            return self.is_app or (
                self.extra.get("snap_type") == "app" and not self.extra.get("content_provider")
            )
        return True


@dataclass
class RemovalOptions:
    purge: bool = False
    autoremove: bool = False


@dataclass
class AffectedEntry:
    name: str
    detail: str = ""
    size: int | None = None


@dataclass
class Step:
    """Một bước thực thi khi gỡ.

    kind = 'cmd'   : chạy argv (nếu root=True thì argv là tham số cho helper qua pkexec)
    kind = 'trash' : chuyển các đường dẫn trong argv vào Thùng rác (không cần root)
    kind = 'delete': XOÁ VĨNH VIỄN các đường dẫn trong argv (không cần root); trước khi xoá từng đường dẫn,
                     kiểm tra lại bằng bộ nhận diện `check` của mục dọn dẹp (cleanup.scanners.recheck)
    """

    kind: str
    argv: list[str]
    root: bool = False
    label: str = ""
    env: dict | None = None  # biến môi trường bổ sung (chỉ cho bước không cần root)
    check: str = ""          # loại mục dọn dẹp (bước 'delete')
    tag: str = ""            # id mục dọn dẹp mà bước này thuộc về (báo cáo kết quả theo mục)

    def display(self) -> str:
        if self.kind == "trash":
            return "Chuyển vào Thùng rác: " + ", ".join(self.argv)
        if self.kind == "delete":
            return "Xoá vĩnh viễn: " + ", ".join(self.argv[:6]) + (f" … (+{len(self.argv) - 6})" if len(self.argv) > 6 else "")
        prefix = "pkexec tt-desktoptool-helper " if self.root else ""
        return prefix + " ".join(self.argv)


@dataclass
class RemovalPlan:
    item: SoftwareItem
    options: RemovalOptions
    affected: list[AffectedEntry] = field(default_factory=list)
    freed_bytes: int | None = None
    trashed_bytes: int | None = None       # chuyển vào Thùng rác — chưa thực sự giải phóng
    blocked_reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    steps: list[Step] = field(default_factory=list)
    supports_purge: bool = False
    supports_autoremove: bool = False
    purge_label: str = "Xoá cả file cấu hình (purge)"
    autoremove_label: str = "Dọn các gói phụ thuộc không còn dùng (autoremove)"
    error: str = ""
    kept_paths: list[str] = field(default_factory=list)   # phần được GIỮ LẠI có chủ đích (vd. thư mục chương trình)

    @property
    def blocked(self) -> bool:
        return bool(self.blocked_reasons) or bool(self.error)

    @property
    def needs_root(self) -> bool:
        return any(s.root for s in self.steps)


@dataclass
class RemovalResult:
    success: bool
    exit_code: int
    log: str
    message: str = ""
    removed: list[str] = field(default_factory=list)
    verified: bool | None = None
    partial: bool = False                   # đã làm được một phần trước khi lỗi
    completed: list[str] = field(default_factory=list)


def human_size(n: int | None) -> str:
    if n is None:
        return "—"
    size = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            if unit == "B":
                return f"{int(size)} B"
            return f"{size:.1f} {unit}".replace(".", ",")
        size /= 1024
    return f"{n} B"


def format_date(d: _dt.datetime | None) -> str:
    if d is None:
        return "—"
    return d.strftime("%d/%m/%Y")
