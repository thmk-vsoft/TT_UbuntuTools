"""Lịch sử các lần gỡ: JSON Lines trong ~/.local/share/tt-desktoptool/history.jsonl."""

from __future__ import annotations

import datetime as dt
import json
import os

from .models import RemovalPlan, RemovalResult
from .utils import display

LOG_TAIL_LINES = 80


def history_path() -> str:
    base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    path = os.path.join(base, "tt-desktoptool", "history.jsonl")
    old = os.path.join(base, "tt-ubuntutools", "history.jsonl")  # tên cũ trước 1.4.0
    if not os.path.lexists(path) and os.path.isfile(old) and not os.path.islink(old):
        try:
            os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
            os.replace(old, path)
            os.chmod(path, 0o600)
        except OSError:
            return old
    return path


def _clean(v):
    if isinstance(v, str):
        return display(v)
    if isinstance(v, list):
        return [_clean(x) for x in v]
    return v


def make_cleanup_record(cplan, result: RemovalResult, freed: int, skipped=(), when: dt.datetime | None = None,
                        done_ids: list[str] | None = None) -> dict:
    """Bản ghi lịch sử cho một lần dọn dẹp (cplan: cleanup.plan.CleanupPlan); done_ids = id các mục THẬT SỰ đã dọn."""
    items = list(getattr(cplan, "items", []) or [])
    if done_ids is not None:
        ids = set(done_ids)
        items = [i for i in items if i.id in ids]
    rec = {
        "time": (when or dt.datetime.now()).isoformat(timespec="seconds"),
        "name": f"Dọn dẹp {len(items)} mục",
        "id": "cleanup",
        "source": "cleanup",
        "source_label": "Dọn dẹp",
        "version": "",
        "purge": False,
        "autoremove": False,
        "option_labels": sorted({f"{i.level_label}" for i in items}),
        "kept": [],
        "affected": [f"{i.title} [{i.level_label}]" for i in items],
        "freed_bytes": int(freed or 0),
        "trashed_bytes": 0,
        "partial": result.partial,
        "completed": [f"{len(result.completed)} bước / đường dẫn"],
        "skipped": [f"{p} ({why})" for p, why in list(skipped)[:20]],
        "success": result.success,
        "verified": None,
        "exit_code": result.exit_code,
        "message": result.message,
        "log_tail": "\n".join(result.log.splitlines()[-LOG_TAIL_LINES:])[-16384:],
    }
    return {k: _clean(v) for k, v in rec.items()}


def make_disk_record(path: str, size: int | None, files: int | None, ok: bool, message: str = "",
                     when: dt.datetime | None = None, restored: bool = False) -> dict:
    """Bản ghi cho một lần "Chuyển vào Thùng rác" (hoặc Hoàn tác — restored=True) từ tab Dung lượng."""
    name = os.path.basename(path.rstrip("/")) or path
    if restored:
        rec = {
            "time": (when or dt.datetime.now()).isoformat(timespec="seconds"),
            "name": f"Khôi phục từ Thùng rác: {name}",
            "id": "disk", "source": "disk", "source_label": "Dung lượng", "version": "",
            "purge": False, "autoremove": False, "option_labels": ["Hoàn tác"], "kept": [],
            "affected": [path], "freed_bytes": 0, "trashed_bytes": 0, "partial": False,
            "completed": [path] if ok else [], "success": bool(ok), "verified": None,
            "exit_code": 0 if ok else 1,
            "message": "Đã khôi phục về vị trí cũ" if ok else f"Không khôi phục được: {message}",
            "log_tail": "",
        }
        return {k: _clean(v) for k, v in rec.items()}
    rec = {
        "time": (when or dt.datetime.now()).isoformat(timespec="seconds"),
        "name": f"Chuyển vào Thùng rác: {name}",
        "id": "disk",
        "source": "disk",
        "source_label": "Dung lượng",
        "version": "",
        "purge": False,
        "autoremove": False,
        "option_labels": ["Thùng rác"],
        "kept": [],
        "affected": [path],
        "freed_bytes": 0,
        "trashed_bytes": int(size or 0) if ok else 0,
        "partial": False,
        "completed": [path] if ok else [],
        "success": bool(ok),
        "verified": None,
        "exit_code": 0 if ok else 1,
        "message": ("Đã chuyển vào Thùng rác"
                    + (f" ({files} file)" if files is not None else "")) if ok else f"Không chuyển được: {message}",
        "log_tail": "",
    }
    return {k: _clean(v) for k, v in rec.items()}


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
        "option_labels": ([plan.purge_label] if plan.options.purge and plan.supports_purge else [])
                         + ([plan.autoremove_label] if plan.options.autoremove and plan.supports_autoremove else []),
        "kept": list(plan.kept_paths),
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
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)  # chỉ chủ tài khoản đọc
    try:
        os.fchmod(fd, 0o600)
    except OSError:
        pass
    with os.fdopen(fd, "a", encoding="utf-8") as fh:
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
