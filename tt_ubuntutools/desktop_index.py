"""Đọc các file .desktop để biết phần mềm nào là "ứng dụng" (có lối tắt menu), biểu tượng và tên hiển thị."""

from __future__ import annotations

import glob
import os
import shlex
from dataclasses import dataclass

from .utils import dpkg_owners

HOME = os.path.expanduser("~")

SYSTEM_DIRS = ["/usr/share/applications", "/usr/local/share/applications"]
SNAP_DIR = "/var/lib/snapd/desktop/applications"
FLATPAK_DIRS = [
    "/var/lib/flatpak/exports/share/applications",
    os.path.join(HOME, ".local/share/flatpak/exports/share/applications"),
]
USER_DIR = os.path.join(HOME, ".local/share/applications")


@dataclass
class DesktopEntry:
    path: str
    name: str = ""
    name_vi: str = ""
    comment: str = ""
    comment_vi: str = ""
    icon: str = ""
    exec: str = ""
    workdir: str = ""  # khoá Path= (thư mục làm việc)
    no_display: bool = False

    @property
    def title(self) -> str:
        return self.name_vi or self.name

    @property
    def exec_target(self) -> str:
        """Đường dẫn chương trình trong Exec= (đã bỏ env/biến)."""
        try:
            parts = shlex.split(self.exec)
        except ValueError:
            parts = self.exec.split()
        if parts and parts[0] == "env":
            parts = [p for p in parts[1:] if "=" not in p]
        return parts[0] if parts else ""


def parse_desktop_file(path: str) -> DesktopEntry | None:
    entry = DesktopEntry(path=path)
    in_main = False
    entry_type = ""
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for raw in fh:
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith("["):
                    in_main = line == "[Desktop Entry]"
                    continue
                if not in_main or "=" not in line:
                    continue
                key, val = line.split("=", 1)
                key, val = key.strip(), val.strip()
                if key == "Name":
                    entry.name = val
                elif key == "Name[vi]":
                    entry.name_vi = val
                elif key == "Comment":
                    entry.comment = val
                elif key == "Comment[vi]":
                    entry.comment_vi = val
                elif key == "Icon":
                    entry.icon = val
                elif key == "Exec":
                    entry.exec = val
                elif key == "Path":
                    entry.workdir = val
                elif key == "Type":
                    entry_type = val
                elif key in ("NoDisplay", "Hidden") and val.lower() == "true":
                    entry.no_display = True
    except OSError:
        return None
    if entry_type and entry_type != "Application":
        return None
    return entry


def best_title(entries: list[DesktopEntry], preferred_basename: str = "", fallback: str = "") -> str:
    """Tên hiển thị cho phần mềm có một hoặc nhiều lối tắt.

    Nhiều lối tắt (vd. LibreOffice Writer/Calc/...) -> phần đầu chung của các tên."""
    if not entries:
        return fallback
    for e in entries:
        if preferred_basename and os.path.basename(e.path) == preferred_basename:
            return e.title
    if len(entries) == 1:
        return entries[0].title
    words = [e.title.split() for e in entries if e.title]
    common = []
    for group in zip(*words):
        if all(w == group[0] for w in group):
            common.append(group[0])
        else:
            break
    return " ".join(common) or fallback or entries[0].title


class DesktopIndex:
    def __init__(self):
        self.by_package: dict[str, list[DesktopEntry]] = {}
        self.by_snap: dict[str, list[DesktopEntry]] = {}
        self.by_flatpak: dict[str, DesktopEntry] = {}
        self.user_entries: list[DesktopEntry] = []
        self.system_entries: list[DesktopEntry] = []

    @classmethod
    def build(cls) -> "DesktopIndex":
        idx = cls()
        # Hệ thống: map về gói dpkg
        sys_files = []
        for d in SYSTEM_DIRS:
            sys_files.extend(sorted(glob.glob(os.path.join(d, "*.desktop"))))
        owners = dpkg_owners(sys_files)
        for f in sys_files:
            e = parse_desktop_file(f)
            if not e or e.no_display:
                continue
            idx.system_entries.append(e)
            pkg = owners.get(f)
            if pkg:
                idx.by_package.setdefault(pkg, []).append(e)
        # Snap: <snap>_<app>.desktop
        for f in sorted(glob.glob(os.path.join(SNAP_DIR, "*.desktop"))):
            e = parse_desktop_file(f)
            if not e or e.no_display:
                continue
            snap = os.path.basename(f).split("_", 1)[0]
            idx.by_snap.setdefault(snap, []).append(e)
        # Flatpak: <app-id>.desktop
        for d in FLATPAK_DIRS:
            for f in glob.glob(os.path.join(d, "*.desktop")):
                e = parse_desktop_file(f)
                if e and not e.no_display:
                    idx.by_flatpak[os.path.basename(f)[: -len(".desktop")]] = e
        # Người dùng
        for f in sorted(glob.glob(os.path.join(USER_DIR, "*.desktop"))):
            e = parse_desktop_file(f)
            if e:
                idx.user_entries.append(e)
        return idx

    def entries_for_path(self, path: str) -> list[DesktopEntry]:
        """Các lối tắt (người dùng + hệ thống không thuộc gói) có Exec trỏ vào path (file hoặc thư mục)."""
        path = path.rstrip("/")
        out = []
        for e in self.user_entries + self.system_entries:
            target = e.exec_target
            if target == path or target.startswith(path + "/"):
                out.append(e)
        return out
