"""Flatpak: ứng dụng và runtime (chỉ chạy khi máy có lệnh flatpak)."""

from __future__ import annotations

import datetime as dt
import os
import re

from ..desktop_index import DesktopIndex
from ..models import SoftwareItem, Source
from ..utils import run, which
from .base import Collector

COLUMNS = "application,name,version,branch,arch,size,origin,installation,runtime"
_UNITS = {"b": 1, "kb": 1000, "mb": 1000**2, "gb": 1000**3, "tb": 1000**4,
          "kib": 1024, "mib": 1024**2, "gib": 1024**3, "tib": 1024**4, "bytes": 1}


def parse_human_size(s: str) -> int | None:
    m = re.match(r"^\s*([\d.,]+)\s*([A-Za-z]+)?\s*$", s or "")
    if not m:
        return None
    num = float(m.group(1).replace(",", "."))
    unit = (m.group(2) or "b").lower()
    return int(num * _UNITS.get(unit, 1))


def parse_flatpak_list(text: str) -> list[dict]:
    keys = COLUMNS.split(",")
    rows = []
    for line in text.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        parts += [""] * (len(keys) - len(parts))
        rows.append(dict(zip(keys, (p.strip() for p in parts))))
    return rows


def _deploy_date(app_id: str, installation: str, kind: str) -> dt.datetime | None:
    base = (os.path.expanduser("~/.local/share/flatpak") if installation == "user"
            else "/var/lib/flatpak")
    p = os.path.join(base, kind, app_id)
    try:
        return dt.datetime.fromtimestamp(os.stat(p).st_mtime)
    except OSError:
        return None


_SHARED_EXT_HINTS = (".GL.", ".GL32.", ".VAAPI.", ".Codecs", "codecs", ".openh264", "Gtk3theme",
                     ".Locale", ".ffmpeg", ".Compat.")


def _shared_extension(app_id: str) -> bool:
    """Phần mở rộng runtime dùng chung (GL, codec, theme...) — ứng dụng nạp gián tiếp nên khó biết ai dùng."""
    return app_id.startswith("org.freedesktop.Platform.") or any(h in app_id for h in _SHARED_EXT_HINTS)


class FlatpakCollector(Collector):
    name = "flatpak"
    label = "Flatpak"

    def available(self) -> bool:
        return which("flatpak") is not None

    def collect(self, index: DesktopIndex) -> list[SoftwareItem]:
        outputs = []
        for kind in ("--app", "--runtime"):
            res = run(["flatpak", "list", kind, f"--columns={COLUMNS}"])
            if res.returncode != 0:  # báo lỗi để bước kiểm tra lại không nhầm thành "đã gỡ"
                raise RuntimeError("flatpak list lỗi: " + (res.stderr or "").strip()[-200:])
            outputs.append(parse_flatpak_list(res.stdout))
        apps, runtimes = outputs
        used: dict[tuple[str, str], set[str]] = {}
        for a in apps:
            parts = a.get("runtime", "").split("/")  # id/arch/branch
            if parts and parts[0]:
                key = (parts[0], parts[2] if len(parts) > 2 else "")
                used.setdefault(key, set()).add(a["application"])

        items = []
        for row, is_runtime in [(a, False) for a in apps] + [(r, True) for r in runtimes]:
            app_id = row["application"]
            inst = row.get("installation") or "system"
            entry = index.by_flatpak.get(app_id)
            branch = row.get("branch", "")
            users = sorted(used.get((app_id, branch), set())) if is_runtime else []
            if is_runtime and not users:
                # Extension (vd. org.freedesktop.Platform.GL.default, org.gnome.Platform.Locale):
                # dùng chung với runtime cha cùng nhánh.
                parent = app_id
                while "." in parent and not users:
                    parent = parent.rsplit(".", 1)[0]
                    users = sorted(used.get((parent, branch), set()))
            if is_runtime and not users and apps and _shared_extension(app_id):
                users = ["các ứng dụng flatpak (phần mở rộng dùng chung: đồ hoạ / codec / giao diện)"]
            items.append(SoftwareItem(
                source=Source.FLATPAK,
                id=f"{app_id}//{row.get('branch', '')}//{inst}",
                name=app_id,
                display_name=(entry.title if entry else "") or row.get("name", ""),
                version=row.get("version") or row.get("branch", ""),
                size=parse_human_size(row.get("size", "")),
                install_date=_deploy_date(app_id, inst, "runtime" if is_runtime else "app"),
                origin=f"{row.get('origin', '')} ({'người dùng' if inst == 'user' else 'hệ thống'})",
                summary=(entry.comment_vi or entry.comment) if entry else ("Runtime" if is_runtime else ""),
                icon=entry.icon if entry else app_id,
                is_app=not is_runtime,
                manual=not is_runtime,
                protected_reason=(f"Runtime đang được dùng bởi: {', '.join(users)}." if users else ""),
                extra={"installation": inst, "branch": row.get("branch", ""),
                       "arch": row.get("arch", ""), "runtime": is_runtime},
            ))
        return items
