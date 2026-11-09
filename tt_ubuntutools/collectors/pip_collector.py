"""Gói Python cài bằng pip (toàn cục /usr/local hoặc ~/.local) và pipx."""

from __future__ import annotations

import datetime as dt
import glob
import importlib.metadata as md
import json
import os
import re

from ..desktop_index import DesktopIndex
from ..models import SoftwareItem, Source
from ..utils import dir_size
from .base import Collector

HOME = os.path.expanduser("~")


def site_dirs() -> list[tuple[str, str]]:
    """[(thư mục, phạm vi)] — phạm vi 'system' (cần root) hoặc 'user'."""
    out = []
    for d in sorted(glob.glob("/usr/local/lib/python3*/dist-packages")):
        out.append((d, "system"))
    for d in sorted(glob.glob(os.path.join(HOME, ".local/lib/python3*/site-packages"))):
        out.append((d, "user"))
    return out


def _dist_size(dist) -> int | None:
    try:
        files = dist.files or []
    except Exception:
        return None
    total = 0
    for f in files:
        try:
            total += os.path.getsize(dist.locate_file(f))
        except OSError:
            pass
    return total or None


def _dist_date(dist) -> dt.datetime | None:
    path = getattr(dist, "_path", None)
    try:
        return dt.datetime.fromtimestamp(os.stat(path).st_mtime) if path else None
    except OSError:
        return None


class PipCollector(Collector):
    name = "pip"
    label = "pip / pipx"

    def collect(self, index: DesktopIndex) -> list[SoftwareItem]:
        items = []
        for d, scope in site_dirs():
            m = re.fullmatch(r"python3\.(\d+)", os.path.basename(os.path.dirname(d)))
            pyver = m.group(1) if m else ""
            if not pyver:
                orphan = f"Không xác định được phiên bản Python của {d} — công cụ không tự gỡ."
            elif not os.path.isfile(f"/usr/bin/python3.{pyver}"):
                orphan = (f"Python 3.{pyver} không còn trên máy — đây là site-packages mồ côi (thường sau khi "
                          "nâng cấp Ubuntu); pip của phiên bản hiện tại không gỡ được.")
            else:
                orphan = ""
            for dist in md.distributions(path=[d]):
                name = dist.metadata.get("Name") or ""
                if not name:
                    continue
                items.append(SoftwareItem(
                    source=Source.PIP,
                    id=f"{scope}:{d}:{name}",
                    name=name,
                    version=dist.version or "",
                    size=_dist_size(dist),
                    install_date=_dist_date(dist),
                    origin=f"pip — {'toàn hệ thống' if scope == 'system' else 'người dùng'} ({d})",
                    summary=dist.metadata.get("Summary") or "",
                    icon="text-x-python",
                    manual=True,
                    path=d,
                    protected_reason=orphan,
                    extra={"scope": scope, "kind": "pip", "pyver": pyver},
                ))
        venvs = os.path.join(HOME, ".local/share/pipx/venvs")
        if os.path.isdir(venvs):
            for name in sorted(os.listdir(venvs)):
                vdir = os.path.join(venvs, name)
                version = ""
                try:
                    with open(os.path.join(vdir, "pipx_metadata.json"), encoding="utf-8") as fh:
                        meta = json.load(fh)
                    version = meta.get("main_package", {}).get("package_version", "")
                except (OSError, ValueError):
                    pass
                try:
                    date = dt.datetime.fromtimestamp(os.stat(vdir).st_mtime)
                except OSError:
                    date = None
                items.append(SoftwareItem(
                    source=Source.PIP,
                    id=f"pipx:{name}",
                    name=name,
                    version=version,
                    size=dir_size(vdir),
                    install_date=date,
                    origin="pipx (người dùng)",
                    summary="Ứng dụng Python cài bằng pipx",
                    icon="text-x-python",
                    manual=True,
                    path=vdir,
                    extra={"scope": "user", "kind": "pipx"},
                ))
        return items
