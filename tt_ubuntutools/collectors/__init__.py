"""Chạy tất cả bộ thu thập song song."""

from __future__ import annotations

import traceback
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from ..desktop_index import DesktopIndex
from ..models import SoftwareItem
from .appimage_collector import AppImageCollector
from .apt_collector import AptCollector
from .base import Collector
from .flatpak_collector import FlatpakCollector
from .manual_collector import ManualCollector
from .npm_collector import NpmCollector
from .pip_collector import PipCollector
from .snap_collector import SnapCollector


def all_collectors() -> list[Collector]:
    return [AptCollector(), SnapCollector(), FlatpakCollector(), AppImageCollector(),
            PipCollector(), NpmCollector(), ManualCollector()]


@dataclass
class CollectResult:
    items: list[SoftwareItem] = field(default_factory=list)
    errors: dict[str, str] = field(default_factory=dict)      # nhãn nguồn -> lỗi
    unavailable: list[str] = field(default_factory=list)      # nguồn không có trên máy


def collector_label(name: str) -> str:
    for c in all_collectors():
        if c.name == name:
            return c.label
    return name


def collect_all(only: set[str] | None = None) -> CollectResult:
    """Thu thập toàn bộ (hoặc chỉ các collector có name trong `only`)."""
    result = CollectResult()
    from ..removal.protection import clear_caches

    clear_caches()
    try:
        index = DesktopIndex.build()
    except Exception as exc:  # không để lỗi lối tắt làm treo cả màn hình
        traceback.print_exc()
        index = DesktopIndex()
        result.errors["Lối tắt menu (.desktop)"] = f"{type(exc).__name__}: {exc}"
    collectors = [c for c in all_collectors() if only is None or c.name in only]
    runnable = []
    for c in collectors:
        try:
            ok = c.available()
        except Exception:
            ok = False
        if ok:
            runnable.append(c)
        else:
            result.unavailable.append(c.label)

    def _run(c: Collector):
        try:
            return c, c.collect(index), None
        except Exception as exc:  # một nguồn lỗi không làm hỏng nguồn khác
            traceback.print_exc()
            return c, [], f"{type(exc).__name__}: {exc}"

    with ThreadPoolExecutor(max_workers=len(runnable) or 1) as pool:
        for c, items, err in pool.map(_run, runnable):
            result.items.extend(items)
            if err:
                result.errors[c.label] = err
    return result
