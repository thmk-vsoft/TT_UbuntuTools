"""AppImage: quét các thư mục hay chứa file AppImage."""

from __future__ import annotations

import datetime as dt
import os

from ..desktop_index import DesktopIndex
from ..models import SoftwareItem, Source
from .base import Collector

HOME = os.path.expanduser("~")
SCAN_DIRS = [
    (os.path.join(HOME, "Applications"), 2),
    (os.path.join(HOME, "AppImages"), 2),
    (os.path.join(HOME, "Downloads"), 1),
    (os.path.join(HOME, "Desktop"), 1),
    (os.path.join(HOME, ".local/bin"), 1),
    (os.path.join(HOME, "bin"), 1),
    ("/opt", 2),
]


def is_appimage(path: str) -> bool:
    """File ELF có magic AppImage ('AI' + kiểu 1/2 tại offset 8) hoặc đuôi .AppImage."""
    try:
        with open(path, "rb") as fh:
            head = fh.read(11)
    except OSError:
        return False
    if len(head) < 11 or head[:4] != b"\x7fELF":
        return False
    if head[8:10] == b"AI" and head[10] in (1, 2):
        return True
    return path.lower().endswith(".appimage")


def find_appimages(dirs=SCAN_DIRS) -> list[str]:
    found = []
    seen = set()
    for root, depth in dirs:
        if not os.path.isdir(root):
            continue
        stack = [(root, 0)]
        while stack:
            cur, d = stack.pop()
            try:
                with os.scandir(cur) as it:
                    for e in it:
                        try:
                            if e.is_dir(follow_symlinks=False):
                                if d + 1 < depth and not e.name.startswith(".") and \
                                        e.stat(follow_symlinks=False).st_dev == os.stat(cur).st_dev:
                                    stack.append((e.path, d + 1))
                            elif e.is_file(follow_symlinks=False):
                                # Chỉ đọc header khi tên hợp lý hoặc file có quyền thực thi.
                                if (e.name.lower().endswith(".appimage") or os.access(e.path, os.X_OK)) \
                                        and is_appimage(e.path):
                                    real = os.path.realpath(e.path)
                                    if real not in seen:
                                        seen.add(real)
                                        found.append(e.path)
                        except OSError:
                            continue
            except OSError:
                continue
    return found


def pretty_name(filename: str) -> str:
    base = filename
    if base.lower().endswith(".appimage"):
        base = base[: -len(".appimage")]
    # Bỏ đuôi kiến trúc/phiên bản phổ biến: Foo-1.2.3-x86_64
    for arch in ("-x86_64", "_x86_64", "-amd64", "_amd64", ".x86_64"):
        if base.endswith(arch):
            base = base[: -len(arch)]
    return base


class AppImageCollector(Collector):
    name = "appimage"
    label = "AppImage"

    def collect(self, index: DesktopIndex) -> list[SoftwareItem]:
        items = []
        for path in find_appimages():
            try:
                st = os.stat(path)
            except OSError:
                continue
            entries = index.entries_for_path(path)
            entry = entries[0] if entries else None
            fname = os.path.basename(path)
            reason = ""
            if not path.startswith(HOME + "/") and os.path.dirname(path) != "/opt":
                reason = (f"File nằm trong thư mục {os.path.dirname(path)} — hãy gỡ cả thư mục đó ở mục "
                          "“Cài thủ công”.")
            items.append(SoftwareItem(
                source=Source.APPIMAGE,
                id=path,
                name=fname,
                display_name=(entry.title if entry else "") or pretty_name(fname),
                version="",
                size=st.st_size,
                install_date=dt.datetime.fromtimestamp(st.st_mtime),
                origin=os.path.dirname(path),
                summary=(entry.comment_vi or entry.comment) if entry else "File AppImage",
                icon=entry.icon if entry else "application-x-executable",
                is_app=True,
                manual=True,
                path=path,
                protected_reason=reason,
                extra={"desktop_files": [e.path for e in entries],
                       "writable": os.access(os.path.dirname(path), os.W_OK)},
            ))
        return items
