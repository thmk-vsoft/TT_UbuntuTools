"""Lịch sử các lần gỡ: JSON Lines trong ~/.local/share/tt-ubuntutools/history.jsonl."""

from __future__ import annotations

import datetime as dt
import json
import os

from .models import RemovalPlan, RemovalResult
from .utils import display

LOG_TAIL_LINES = 80


def history_path() -> str:
    base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    return os.path.join(base, "tt-ubuntutools", "history.jsonl")


def _clean(v):
    if isinstance(v, str):
        return display(v)
    if isinstance(v, list):
        return [_clean(x) for x in v]
    return v


def make_record(plan: RemovalPlan, result: RemovalResult, when: dt.datetime | None = None) -> dict:
    item = plan.item
    rec = {
        "time": (when or dt.datetime.now()).isoformat(timespec="seconds"),
        "name": item.title,
        "id": item.id,
        "source": item.source.code,
        "source_label": item.source.label,
        "version": item.version,
        "purge": plan.options.purge,
        "autoremove": plan.options.autoremove,
        "affected": [a.name for a in plan.affected],
        "freed_bytes": (plan.freed_bytes or 0) if result.success else 0,
        "trashed_bytes": (plan.trashed_bytes or 0) if result.success else 0,
        "partial": result.partial,
        "completed": result.completed,
        "success": result.success,
        "verified": result.verified,
        "exit_code": result.exit_code,
        "message": result.message,
        "log_tail": "\n".join(result.log.splitlines()[-LOG_TAIL_LINES:])[-16384:],
    }
    return {k: _clean(v) for k, v in rec.items()}


def append(record: dict, path: str | None = None) -> None:
    path = path or history_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def load(path: str | None = None) -> list[dict]:
    """Bản ghi mới nhất trước. Bỏ qua dòng hỏng."""
    path = path or history_path()
    out = []
    try:
        with open(path, "rb") as fh:
            for raw in fh:
                try:
                    rec = json.loads(raw.decode("utf-8", "replace").strip() or "null")
                except ValueError:
                    continue
                if isinstance(rec, dict):
                    out.append(rec)
    except OSError:
        return []
    out.reverse()
    return out


def clear(path: str | None = None) -> None:
    path = path or history_path()
    try:
        os.remove(path)
    except FileNotFoundError:
        pass
