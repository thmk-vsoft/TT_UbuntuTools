"""Liệt kê thư mục và đo dung lượng (tính khi mở từng thư mục, nhớ kết quả trong phiên).

Cách đo: dung lượng thật trên đĩa (st_blocks * 512), mỗi inode có liên kết cứng chỉ đếm một lần trong một lần đo,
không đi theo liên kết tượng trưng, không sang ổ khác hay điểm gắn (kể cả bind mount cùng ổ, /proc, /sys, /run,
/snap/*...). Mục biến mất giữa chừng (thư mục bận như ~/.cache) được bỏ qua, không tính là lỗi.
"""

from __future__ import annotations

import datetime as dt
import errno
import os
import stat
import threading
from dataclasses import dataclass, field

from ..utils import mount_points

MAX_LINKS = 200_000  # giới hạn số inode liên kết cứng ghi nhớ cho mỗi thư mục (để khử trùng ở tổng; vượt thì tổng
                     # của thư mục cha chỉ có thể hơi dư với phần vượt)


@dataclass
class Entry:
    path: str
    name: str
    is_dir: bool
    is_link: bool = False
    hidden: bool = False
    other_device: bool = False   # điểm gắn / hệ thống ảo — không tính vào dung lượng
    readable: bool = True
    mtime: dt.datetime | None = None
    size: int | None = None      # None = chưa tính xong
    files: int | None = None     # số file bên trong (thư mục)
    dirs: int | None = None
    errors: int = 0              # số mục không đọc được bên trong
    complete: bool = False       # đã tính xong (kể cả khi có mục không đọc được)
    links: dict = field(default_factory=dict)  # {(dev, ino): bytes} các file có liên kết cứng (nlink > 1)


@dataclass
class Measure:
    size: int = 0
    files: int = 0
    dirs: int = 0
    errors: int = 0
    cancelled: bool = False
    links: dict = field(default_factory=dict)   # {(dev, ino): bytes} — để khử trùng khi cộng nhiều thư mục


def _mtime(st) -> dt.datetime | None:
    try:
        return dt.datetime.fromtimestamp(st.st_mtime)
    except (OverflowError, OSError, ValueError):
        return None


def _gone(exc: OSError) -> bool:
    return exc.errno in (errno.ENOENT, errno.ENOTDIR)


def list_dir(path: str, mounts: set[str] | None = None) -> tuple[list[Entry], str]:
    """Các mục trực tiếp trong thư mục (kể cả mục ẩn). Trả về (danh sách, lỗi '' nếu đọc được)."""
    try:
        parent_dev = os.stat(path).st_dev
    except OSError as exc:
        return [], exc.strerror or str(exc)
    mounts = set(mount_points()) if mounts is None else mounts
    out: list[Entry] = []
    try:
        with os.scandir(path) as it:
            for e in it:
                try:
                    st = e.stat(follow_symlinks=False)
                except OSError as exc:
                    if _gone(exc):
                        continue
                    out.append(Entry(e.path, e.name, False, hidden=e.name.startswith("."), readable=False,
                                     size=0, complete=True, errors=1))
                    continue
                is_link = stat.S_ISLNK(st.st_mode)
                is_dir = stat.S_ISDIR(st.st_mode)
                ent = Entry(e.path, e.name, is_dir, is_link=is_link, hidden=e.name.startswith("."),
                            mtime=_mtime(st))
                if is_dir and (st.st_dev != parent_dev or e.path in mounts):
                    ent.other_device = True
                    ent.size, ent.files, ent.dirs, ent.complete = 0, 0, 0, True
                elif not is_dir:
                    ent.size = st.st_blocks * 512  # liên kết tượng trưng: chỉ khối của chính nó (như du)
                    if not is_link and st.st_nlink > 1:
                        ent.links = {(st.st_dev, st.st_ino): ent.size}
                    ent.complete = True
                elif not os.access(e.path, os.R_OK | os.X_OK):
                    ent.readable = False
                    ent.size, ent.files, ent.dirs, ent.errors, ent.complete = 0, 0, 0, 1, True
                out.append(ent)
    except OSError as exc:
        return out, exc.strerror or str(exc)
    return out, ""


def measure_dir(path: str, cancel: threading.Event | None = None, dev: int | None = None,
                mounts: set[str] | None = None) -> Measure:
    """Tổng dung lượng thư mục (đệ quy). Huỷ được giữa chừng qua `cancel`. Không bao giờ ném lỗi OSError."""
    m = Measure()
    try:
        st = os.lstat(path)
    except OSError as exc:
        if not _gone(exc):
            m.errors += 1
        return m
    if not stat.S_ISDIR(st.st_mode):
        m.size = st.st_blocks * 512
        m.files = 1
        return m
    mounts = set(mount_points()) if mounts is None else mounts
    root_dev = st.st_dev if dev is None else dev
    m.size += st.st_blocks * 512  # chính thư mục (như du)
    visited = {(st.st_dev, st.st_ino)}
    seen: set = set()
    stack = [path]
    n = 0
    while stack:
        cur = stack.pop()
        try:
            it = os.scandir(cur)
        except OSError as exc:
            if not _gone(exc):
                m.errors += 1
            continue
        try:
            with it:
                for e in it:  # readdir có thể ném lỗi (vd. /proc/<pid>/map_files) — xem except bên dưới
                    n += 1
                    if cancel is not None and n % 2000 == 0 and cancel.is_set():
                        m.cancelled = True
                        return m
                    try:
                        est = e.stat(follow_symlinks=False)
                    except OSError as exc:
                        if not _gone(exc):
                            m.errors += 1
                        continue
                    if stat.S_ISDIR(est.st_mode):
                        key = (est.st_dev, est.st_ino)
                        if est.st_dev == root_dev and e.path not in mounts and key not in visited:
                            visited.add(key)
                            m.dirs += 1
                            m.size += est.st_blocks * 512
                            stack.append(e.path)
                        continue
                    if stat.S_ISLNK(est.st_mode):
                        m.size += est.st_blocks * 512  # khối của chính liên kết (không đi theo), như du
                        continue
                    m.files += 1
                    if est.st_nlink > 1:
                        key = (est.st_dev, est.st_ino)
                        if key in seen:
                            continue
                        seen.add(key)
                        if len(m.links) < MAX_LINKS:
                            m.links[key] = est.st_blocks * 512
                    m.size += est.st_blocks * 512
        except OSError as exc:
            if not _gone(exc):
                m.errors += 1
    return m


def dedup_total(sizes_and_links) -> int:
    """Cộng dung lượng nhiều mục, mỗi inode liên kết cứng chỉ tính một lần.
    sizes_and_links: [(size, {(dev, ino): bytes})]."""
    total = 0
    seen: set = set()
    for size, links in sizes_and_links:
        total += size or 0
        if links:
            for key, b in links.items():
                if key in seen:
                    total -= b
                else:
                    seen.add(key)
    return max(total, 0)


class DiskUsageCache:
    """Nhớ dung lượng đã tính của các thư mục trong phiên. An toàn khi dùng từ nhiều luồng."""

    def __init__(self):
        self._lock = threading.Lock()
        self._data: dict[str, Measure] = {}

    def get(self, path: str) -> Measure | None:
        with self._lock:
            return self._data.get(path)

    def put(self, path: str, m: Measure):
        if m.cancelled:
            return
        with self._lock:
            self._data[path] = m

    def invalidate(self, path: str, with_children: bool = True):
        """Bỏ kết quả của path, mọi thư mục cha (tổng đã đổi) và (tuỳ chọn) mọi thư mục con."""
        path = os.path.normpath(path) if path else "/"
        prefix = path if path.endswith("/") else path + "/"
        with self._lock:
            for k in list(self._data):
                if k == path or (with_children and k.startswith(prefix)):
                    del self._data[k]
            p = os.path.dirname(path)
            while True:
                self._data.pop(p, None)
                if p in ("/", ""):
                    break
                p = os.path.dirname(p)

    def clear(self):
        with self._lock:
            self._data.clear()


def disk_usage(path: str) -> tuple[int, int, int] | None:
    """(tổng, đã dùng, còn trống) của ổ chứa path, theo statvfs."""
    try:
        st = os.statvfs(path)
    except OSError:
        return None
    total = st.f_blocks * st.f_frsize
    free = st.f_bavail * st.f_frsize
    used = total - st.f_bfree * st.f_frsize
    return total, used, free
