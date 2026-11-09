"""Gói dpkg/APT: phân biệt kho Ubuntu, PPA / kho bên thứ ba, và file .deb cài tay."""

from __future__ import annotations

import datetime as dt
import glob
import gzip
import zlib
import os
import re
from dataclasses import dataclass

from ..desktop_index import DesktopIndex, best_title
from ..models import SoftwareItem, Source
from ..removal.protection import active_session_packages, apt_reason, ships_firmware
from ..utils import APT_LOCK
from .base import Collector

DPKG_INFO = "/var/lib/dpkg/info"


@dataclass(frozen=True)
class OriginInfo:
    origin: str
    label: str
    site: str
    archive: str


def classify_origins(origins: list[OriginInfo]) -> tuple[Source, str] | None:
    """Phân loại từ danh sách origin của một phiên bản.

    Trả về None nếu phiên bản chỉ có origin 'now' (không thuộc kho nào)."""
    real = [o for o in origins if o.archive != "now" and (o.origin or o.site)]
    if not real:
        return None
    for o in real:
        if o.origin == "Ubuntu" or o.site.endswith("ubuntu.com"):
            return Source.APT, f"Ubuntu ({o.archive})" if o.archive else "Ubuntu"
    o = real[0]
    if o.origin.startswith("LP-PPA-"):
        ppa = o.origin[len("LP-PPA-"):]
        return Source.THIRD_PARTY, f"PPA: {o.label or ppa} ({ppa})" if o.label else f"PPA: {ppa}"
    name = o.label or o.origin or o.site
    return Source.THIRD_PARTY, f"{name} ({o.site})" if o.site and o.site not in name else name


DPKG_LOG_RE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) install (\S+?)(?::\S+)? ")


def parse_dpkg_log_lines(lines, result: dict[str, dt.datetime] | None = None) -> dict[str, dt.datetime]:
    """Lấy thời điểm cài sớm nhất của mỗi gói từ các dòng dpkg.log."""
    result = {} if result is None else result
    for line in lines:
        m = DPKG_LOG_RE.match(line)
        if not m:
            continue
        try:
            when = dt.datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
        pkg = m.group(2)
        if pkg not in result or when < result[pkg]:
            result[pkg] = when
    return result


def read_dpkg_logs(pattern: str = "/var/log/dpkg.log*") -> dict[str, dt.datetime]:
    result: dict[str, dt.datetime] = {}
    for path in glob.glob(pattern):
        try:
            opener = gzip.open if path.endswith(".gz") else open
            with opener(path, "rt", encoding="utf-8", errors="replace") as fh:
                parse_dpkg_log_lines(fh, result)
        except (OSError, EOFError, zlib.error, ValueError):
            continue  # file log hỏng/cắt cụt: giữ phần đã đọc được
    return result


def list_file_mtime(pkg: str, arch: str) -> dt.datetime | None:
    for cand in (f"{pkg}.list", f"{pkg}:{arch}.list"):
        p = os.path.join(DPKG_INFO, cand)
        try:
            return dt.datetime.fromtimestamp(os.stat(p).st_mtime)
        except OSError:
            continue
    return None


def _origins_of(version) -> list[OriginInfo]:
    return [OriginInfo(o.origin or "", o.label or "", o.site or "", o.archive or "") for o in version.origins]


def _reason(name: str, essential: bool, priority: str, dynamic: dict) -> str:
    r = apt_reason(name, essential, priority, dynamic)
    if r:
        return r
    if ships_firmware(name):
        return f"Gói {name} cài firmware cho phần cứng (/lib/firmware)."
    return ""


class AptCollector(Collector):
    name = "apt"
    label = "APT / dpkg"

    def available(self) -> bool:
        try:
            import apt  # noqa: F401
        except ImportError:
            return False
        return os.path.isdir(DPKG_INFO)

    def collect(self, index: DesktopIndex) -> list[SoftwareItem]:
        import apt

        log_dates = read_dpkg_logs()
        with APT_LOCK:
            return self._collect(apt.Cache(), index, log_dates)

    def _collect(self, cache, index: DesktopIndex, log_dates) -> list[SoftwareItem]:
        items: list[SoftwareItem] = []
        dynamic = active_session_packages()
        for pkg in cache:
            if not pkg.is_installed:
                continue
            ver = pkg.installed
            classified = classify_origins(_origins_of(ver))
            note = ""
            if classified is None:
                # Phiên bản đang cài không có trong kho; nếu gói vẫn tồn tại trong kho
                # (khác phiên bản) thì vẫn tính theo kho đó.
                for other in pkg.versions:
                    c = classify_origins(_origins_of(other))
                    if c:
                        classified = c
                        note = " — phiên bản đang cài khác bản trong kho"
                        break
            if classified is None:
                source, origin = Source.DEB, "Cài từ file .deb (không thuộc kho nào)"
            else:
                source, origin = classified
                origin += note

            entries = index.by_package.get(pkg.name, [])
            entry = entries[0] if entries else None
            arch = pkg.architecture()
            essential = bool(pkg.essential)
            priority = ver.priority or ""
            item = SoftwareItem(
                source=source,
                id=pkg.name,
                name=pkg.name,
                display_name=best_title(entries, f"{pkg.name}.desktop"),
                version=ver.version,
                size=ver.installed_size or None,
                install_date=log_dates.get(pkg.name) or list_file_mtime(pkg.name, arch),
                origin=origin,
                summary=(entry.comment_vi or entry.comment) if entry and (entry.comment_vi or entry.comment) else (ver.summary or ""),
                description=ver.description or "",
                icon=entry.icon if entry else "",
                is_app=bool(entries),
                manual=not pkg.is_auto_installed,
                protected_reason=_reason(pkg.name, essential, priority, dynamic),
                extra={
                    "arch": arch,
                    "section": ver.section or "",
                    "priority": priority,
                    "essential": essential,
                    "desktop_files": [e.path for e in entries],
                    "date_from_log": pkg.name in log_dates,
                },
            )
            items.append(item)
        return items
