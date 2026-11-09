"""Snap: lấy dữ liệu qua REST API của snapd (không cần quyền root)."""

from __future__ import annotations

import datetime as dt
import os
import re

from ..desktop_index import DesktopIndex, best_title
from ..models import SoftwareItem, Source
from ..removal.protection import snap_reason
from ..utils import snap_content_providers, snapd_get
from .base import Collector

_FRACTION_RE = re.compile(r"(\.\d{6})\d+")


def parse_snap_date(s: str | None) -> dt.datetime | None:
    """'2026-11-06T14:03:07.364026353+07:00' -> datetime (giờ địa phương, không tz)."""
    if not s:
        return None
    s = _FRACTION_RE.sub(r"\1", s).replace("Z", "+00:00")
    try:
        d = dt.datetime.fromisoformat(s)
    except ValueError:
        return None
    if d.tzinfo:
        d = d.astimezone().replace(tzinfo=None)
    return d


def content_providers(connections: dict) -> dict[str, set[str]]:
    return snap_content_providers(connections)


def _meta_icon(name: str) -> str:
    for ext in ("png", "svg"):
        p = f"/snap/{name}/current/meta/gui/icon.{ext}"
        if os.path.isfile(p):
            return p
    return ""


class SnapCollector(Collector):
    name = "snap"
    label = "Snap"

    def available(self) -> bool:
        return os.path.exists("/run/snapd.socket")

    def collect(self, index: DesktopIndex) -> list[SoftwareItem]:
        snaps = snapd_get("/v2/snaps") or []
        try:
            providers = content_providers(snapd_get("/v2/connections?select=all") or {})
        except Exception:
            providers = {}
        base_users: dict[str, set[str]] = {}
        for s in snaps:
            if s.get("base"):
                base_users.setdefault(s["base"], set()).add(s["name"])

        items = []
        for s in snaps:
            name = s["name"]
            entries = index.by_snap.get(name, [])
            entry = entries[0] if entries else None
            stype = s.get("type", "app")
            consumers = sorted(providers.get(name, set()))
            users = sorted(base_users.get(name, set()))
            publisher = (s.get("publisher") or {}).get("display-name") or s.get("developer", "")
            channel = s.get("tracking-channel") or s.get("channel") or ""
            origin = "Snap Store"
            if publisher:
                origin += f" — {publisher}"
            if channel:
                origin += f" ({channel})"
            if s.get("devmode") or s.get("jailmode") or s.get("confinement") == "classic":
                origin += f" [{s.get('confinement')}]"
            items.append(SoftwareItem(
                source=Source.SNAP,
                id=name,
                name=name,
                display_name=best_title(entries, f"{name}_{name}.desktop", s.get("title") or ""),
                version=f"{s.get('version', '')} (rev {s.get('revision', '?')})",
                size=s.get("installed-size"),
                install_date=parse_snap_date(s.get("install-date")),
                origin=origin,
                summary=s.get("summary") or "",
                description=s.get("description") or "",
                icon=(entry.icon if entry else "") or _meta_icon(name),
                is_app=bool(entries),
                manual=stype == "app",
                path=f"/snap/{name}",
                protected_reason=snap_reason(name, stype, consumers, users),
                extra={
                    "snap_type": stype,
                    "content_provider": consumers,
                    "base_users": users,
                    "base": s.get("base", ""),
                    "desktop_files": [e.path for e in entries],
                },
            ))
        return items
