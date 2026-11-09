"""Phần mềm cài thủ công: thư mục trong /opt và file trong /usr/local/bin không thuộc gói dpkg nào."""

from __future__ import annotations

import datetime as dt
import os

from ..desktop_index import DesktopIndex
from ..models import SoftwareItem, Source
from ..utils import dir_size, dpkg_owners, mount_points, mounts_under
from .appimage_collector import is_appimage
from .base import Collector

OPT = "/opt"
LOCAL_BIN = "/usr/local/bin"


def is_generated_entry_point(path: str) -> bool:
    """Script do pip/npm sinh ra (đã được hiển thị ở nguồn pip/npm)."""
    try:
        real = os.path.realpath(path)
        if "/node_modules/" in real:
            return True
        with open(path, "rb") as fh:
            head = fh.read(600)
    except OSError:
        return False
    if not head.startswith(b"#!"):
        return False
    first = head.split(b"\n", 1)[0]
    return b"python" in first and b"sys.argv[0] = re.sub(" in head


class ManualCollector(Collector):
    name = "manual"
    label = "Cài thủ công"

    def collect(self, index: DesktopIndex) -> list[SoftwareItem]:
        from ..removal.pathrules import RUNTIME_DIRS, systemd_units_using

        opt_is_link = os.path.islink(OPT)
        points = mount_points()
        candidates: list[tuple[str, str]] = []  # (path, kind)
        if os.path.isdir(OPT):
            opt_dev = os.stat(OPT).st_dev   # lỗi đọc /opt -> báo lỗi nguồn (không âm thầm trả rỗng)
            names = sorted(os.listdir(OPT))
            for name in names:
                if name.startswith("."):
                    continue
                p = os.path.join(OPT, name)
                if mounts_under(p, points):
                    continue  # điểm gắn ổ đĩa / ổ mạng: kiểm tra TRƯỚC khi stat để không treo vì NFS chết
                try:
                    if os.lstat(p).st_dev != opt_dev:
                        continue
                except OSError:
                    continue
                if os.path.isfile(p) and is_appimage(p):
                    continue  # đã có ở nguồn AppImage
                candidates.append((p, "opt"))
        if os.path.isdir(LOCAL_BIN):
            names = sorted(os.listdir(LOCAL_BIN))
            for name in names:
                p = os.path.join(LOCAL_BIN, name)
                if os.path.isdir(p) or is_generated_entry_point(p) or is_appimage(p):
                    continue
                candidates.append((p, "local-bin"))
        owners = dpkg_owners([p for p, _ in candidates])
        items = []
        for path, kind in candidates:
            if path in owners:
                continue
            try:
                st = os.lstat(path)
            except OSError:
                continue
            entries = index.entries_for_path(path)
            entry = entries[0] if entries else None
            name = os.path.basename(path)
            readable = os.access(path, os.R_OK | (os.X_OK if os.path.isdir(path) else 0))
            reason = ""
            if opt_is_link and kind == "opt":
                reason = "/opt là liên kết tượng trưng — công cụ không xoá trong đó để đảm bảo an toàn."
            elif kind == "opt" and name.lower() in RUNTIME_DIRS:
                reason = (f"{path} là thư mục runtime/dữ liệu của dịch vụ hệ thống (containerd, Docker, CNI...). "
                          "Hãy gỡ dịch vụ tương ứng bằng APT.")
            else:
                units = systemd_units_using(path)
                if units:
                    reason = f"{path} đang được dịch vụ systemd dùng: {', '.join(units[:4])}."
            items.append(SoftwareItem(
                source=Source.MANUAL,
                id=path,
                name=name,
                display_name=entry.title if entry else "",
                size=dir_size(path),
                install_date=dt.datetime.fromtimestamp(st.st_mtime),
                origin=f"Thư mục {os.path.dirname(path)} (không thuộc gói nào)",
                summary=(entry.comment_vi or entry.comment) if entry and (entry.comment_vi or entry.comment)
                else ("Thư mục chương trình" if kind == "opt" else "Chương trình/script"),
                icon=entry.icon if entry else ("folder" if os.path.isdir(path) else "application-x-executable"),
                is_app=bool(entries),
                manual=True,
                path=path,
                protected_reason=reason,
                extra={"kind": kind, "desktop_files": [e.path for e in entries],
                       "readable": readable},
            ))
        return items
