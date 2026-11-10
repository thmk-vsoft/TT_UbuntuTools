"""Lập kế hoạch dọn dẹp từ các mục người dùng đã chọn — kiểm tra lại từng mục ngay lúc này."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from ..models import AffectedEntry, RemovalOptions, RemovalPlan, SoftwareItem, Source, Step
from ..removal.helperpath import HelperUnavailable, helper_path
from .scanners import IRREVERSIBLE, LEVELS, LOCKED, REVIEW, SAFE, CleanupItem, recheck


@dataclass
class CleanupPlan:
    plan: RemovalPlan
    items: list[CleanupItem] = field(default_factory=list)          # mục sẽ dọn
    dropped: list[tuple[CleanupItem, str]] = field(default_factory=list)  # mục bị loại khi kiểm tra lại
    root_argv: dict[str, list[list[str]]] = field(default_factory=dict)  # id mục -> việc root thật sự sẽ chạy
    by_level: dict[str, tuple[int, int]] = field(default_factory=dict)  # mức -> (số mục, dung lượng)

    @property
    def total(self) -> int:
        return sum(it.size or 0 for it in self.items)


def _pseudo_item() -> SoftwareItem:
    return SoftwareItem(Source.MANUAL, "cleanup", "cleanup", display_name="Dọn dẹp")


def _fresh_root_argv(it: CleanupItem) -> list[list[str]]:
    """Bản snap cũ: hỏi lại snapd ngay lúc này, bỏ bản đã biến mất / đã thành bản đang dùng — để một bản cũ
    không còn hợp lệ không làm helper từ chối cả lần dọn (apt, journal)."""
    if it.id != "system:snap":
        return [list(a) for a in it.argv]
    import subprocess

    from ..utils import run
    from .scanners import parse_snap_current, parse_snap_list_all
    try:
        res = run(["/usr/bin/snap", "list", "--all"], timeout=60)
        cur = run(["/usr/bin/snap", "list"], timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return []  # snapd bận / không có: bỏ mục, không chặn cả lần dọn
    if res.returncode != 0 or cur.returncode != 0:
        return []
    valid = set(parse_snap_list_all(res.stdout, parse_snap_current(cur.stdout)))
    return [list(a) for a in it.argv if len(a) == 4 and (a[1], a[3]) in valid]


def build_cleanup_plan(selected: list[CleanupItem]) -> CleanupPlan:
    """Thứ tự bước: lệnh dọn của công cụ -> xoá vĩnh viễn -> việc cần quyền quản trị (một lần hỏi mật khẩu, để
    cuối: nếu bạn huỷ hộp thoại mật khẩu, các mục của bạn vẫn đã được dọn)."""
    plan = RemovalPlan(item=_pseudo_item(), options=RemovalOptions())
    cp = CleanupPlan(plan=plan)
    root_steps, cmd_steps, del_steps = [], [], []
    for it in selected:
        if it.level == LOCKED:
            cp.dropped.append((it, it.reason or "bị khoá"))
            continue
        if it.method == "root":
            argv = _fresh_root_argv(it)
            if not argv:
                cp.dropped.append((it, "không còn gì để dọn (đã thay đổi từ lúc quét)"))
                continue
            cp.root_argv[it.id] = argv
            for a in argv:
                root_steps.append(Step("cmd", list(a), root=True, label=it.title, tag=it.id))
        elif it.method == "cmd":
            bad = [a[0] for a in it.argv if not (os.path.isfile(a[0]) and os.access(a[0], os.X_OK))]
            if not it.argv or bad:
                cp.dropped.append((it, f"không tìm thấy lệnh {bad[0] if bad else ''}".strip()))
                continue
            for a in it.argv:
                cmd_steps.append(Step("cmd", list(a), label=it.title, env=it.env, tag=it.id))
        elif it.method == "delete":
            ok_paths, why_first = [], ""
            for p in it.paths:
                if not os.path.lexists(p):
                    continue
                why = recheck(it.kind, p)
                if why:
                    why_first = why_first or why
                    continue
                ok_paths.append(p)
            if not ok_paths:
                cp.dropped.append((it, why_first or "không còn gì để xoá"))
                continue
            if len(ok_paths) < len([p for p in it.paths if os.path.lexists(p)]):
                plan.warnings.append(f"{it.title}: một số đường dẫn bị bỏ qua khi kiểm tra lại ({why_first}).")
            if it.kind in ("node_modules", "venv", "build"):
                from .scanners import project_in_use
                busy = project_in_use(ok_paths)
                if busy:
                    cp.dropped.append((it, busy))
                    continue
            del_steps.append(Step("delete", ok_paths, label=it.title, check=it.kind, tag=it.id))
        else:
            cp.dropped.append((it, "cách dọn không được hỗ trợ"))
            continue
        cp.items.append(it)
        plan.affected.append(AffectedEntry(it.title, f"{LEVELS[it.level][0]} · {it.note}", it.size))
    plan.steps = cmd_steps + del_steps + root_steps
    if root_steps:
        try:
            helper_path()
        except HelperUnavailable as exc:
            plan.error = str(exc)
    for lvl in (SAFE, REVIEW, IRREVERSIBLE):
        its = [i for i in cp.items if i.level == lvl]
        if its:
            cp.by_level[lvl] = (len(its), sum(i.size or 0 for i in its))
    if del_steps:
        plan.warnings.append("Các thư mục được XOÁ VĨNH VIỄN (không vào Thùng rác) để giải phóng chỗ ngay.")
    if any(i.level == IRREVERSIBLE for i in cp.items):
        plan.warnings.append("Có mục KHÔNG KHÔI PHỤC ĐƯỢC (Thùng rác) — những gì trong đó sẽ mất hẳn.")
    plan.freed_bytes = cp.total or None
    if not plan.steps and not plan.error:
        plan.error = "Không có mục nào để dọn."
    return cp
