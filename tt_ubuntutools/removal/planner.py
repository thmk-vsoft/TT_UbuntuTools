"""Lập kế hoạch gỡ: mô phỏng, liệt kê phần bị ảnh hưởng, kiểm tra luật bảo vệ (không cần root).

Mọi kiểm tra ở đây được helper root làm LẠI ngay trước khi thực hiện."""

from __future__ import annotations

import glob
import os
import re

from ..models import AffectedEntry, RemovalOptions, RemovalPlan, SoftwareItem, Source, Step
from ..utils import APT_LOCK, clean_env, display, human_users, run, which
from . import pathrules
from .pathrules import systemd_units_using  # noqa: F401  (dùng lại ở collector)
from .helperpath import HelperUnavailable, helper_path
from .protection import (SEEDED_DESKTOP_SNAPS, active_session_packages, check_apt_removal,
                         installed_initramfs_providers, purge_reason, boot_hook_warnings, clear_caches)

HOME = os.path.expanduser("~")

SIM_RE = re.compile(r"^(Remv|Purg|Inst) (\S+)(?: \[([^\]]*)\])?")


def parse_apt_simulation(text: str) -> list[tuple[str, str, str]]:
    """[(hành_động, gói, phiên_bản)] từ output `apt-get -s remove` (Remv/Purg/Inst)."""
    out = []
    for line in text.splitlines():
        m = SIM_RE.match(line.strip())
        if m:
            out.append((m.group(1), m.group(2).split(":")[0], m.group(3) or ""))
    return out


def apt_simulation_command(name: str, opts: RemovalOptions) -> list[str]:
    cmd = ["apt-get", "-s", "remove"]
    if opts.purge:
        cmd.append("--purge")
    if opts.autoremove:
        cmd.append("--autoremove")
    cmd.append(name)
    return cmd


def _multi_user_warning(plan: RemovalPlan):
    users = human_users()
    if len(users) > 1:
        plan.warnings.append("Máy có nhiều tài khoản (" + ", ".join(users[:6])
                             + ") — phần mềm cài cho toàn hệ thống sẽ bị gỡ với tất cả mọi người.")


def _plan_apt(plan: RemovalPlan, item: SoftwareItem, opts: RemovalOptions):
    plan.supports_purge = plan.supports_autoremove = True
    res = run(apt_simulation_command(item.name, opts), timeout=180)
    if res.returncode != 0:
        plan.error = "Mô phỏng gỡ thất bại:\n" + (res.stderr or res.stdout).strip()[-800:]
        return
    actions = parse_apt_simulation(res.stdout)
    installs = [n for a, n, _ in actions if a == "Inst"]
    actions = [x for x in actions if x[0] != "Inst"]
    if installs:
        plan.blocked_reasons.append(
            "Khi gỡ gói này, apt muốn CÀI THÊM gói thay thế (" + ", ".join(installs) + "). Công cụ chỉ gỡ, "
            "không tự cài gì nên đã dừng lại. Nếu chắc chắn, hãy gỡ bằng terminal (sudo apt remove …) để tự "
            "xem apt sẽ làm gì.")
    if not actions:
        plan.error = "apt không tìm thấy gói nào để gỡ (có thể gói đã bị gỡ)."
        return
    if "essential packages will be removed" in res.stdout.lower():
        plan.blocked_reasons.append("apt cảnh báo: thao tác này sẽ gỡ gói Essential của hệ thống.")
    info: dict[str, tuple[bool, str]] = {}
    sizes: dict[str, int | None] = {}
    providers: list[str] = []
    try:
        import apt

        with APT_LOCK:
            cache = apt.Cache()
            for _, name, _v in actions:
                if name in cache and cache[name].is_installed:
                    p = cache[name]
                    info[name] = (bool(p.essential), p.installed.priority or "")
                    sizes[name] = p.installed.installed_size or None
            providers = installed_initramfs_providers(cache)
    except ImportError:
        pass
    names = [n for _, n, _ in actions]
    plan.blocked_reasons.extend(check_apt_removal(
        names, info, purge=opts.purge, initramfs_providers=providers, dynamic=active_session_packages()))
    total = 0
    for act, name, ver in actions:
        detail = "gỡ + xoá cấu hình" if act == "Purg" else "gỡ"
        if name != item.name:
            detail += " (phụ thuộc / tự động)"
        if ver:
            detail += f" — {ver}"
        plan.affected.append(AffectedEntry(name, detail, sizes.get(name)))
        total += sizes.get(name) or 0
    plan.freed_bytes = total or None
    plan.warnings.extend(boot_hook_warnings(names))
    if len(actions) > 1:
        plan.warnings.append(f"Sẽ gỡ tổng cộng {len(actions)} gói, kiểm tra kỹ danh sách bên dưới.")
    if opts.autoremove:
        plan.warnings.append("autoremove dọn MỌI gói tự động không còn dùng trên toàn hệ thống, "
                             "không chỉ của phần mềm này.")
    if opts.purge:
        plan.warnings.append("purge xoá luôn file cấu hình hệ thống của các gói trên (không khôi phục được).")
    _multi_user_warning(plan)
    args = ["apt-remove", item.name]
    if opts.purge:
        args.append("--purge")
    if opts.autoremove:
        args.append("--autoremove")
    # Helper chỉ gỡ khi tập gói lúc chạy KHỚP ĐÚNG danh sách người dùng đã xem ở đây.
    args += ["--expect", ",".join(sorted(set(names)))]
    plan.steps.append(Step("cmd", args, root=True, label="Gỡ bằng apt-get"))


def _plan_snap(plan: RemovalPlan, item: SoftwareItem, opts: RemovalOptions):
    plan.supports_purge = True
    plan.purge_label = "Xoá vĩnh viễn dữ liệu của snap, không lưu snapshot (--purge)"
    plan.affected.append(AffectedEntry(item.name, f"snap — {item.version}", item.size))
    plan.freed_bytes = item.size
    if opts.purge:
        pr = purge_reason(item.name)
        if pr:
            plan.blocked_reasons.append(pr)
    if item.name in SEEDED_DESKTOP_SNAPS:
        plan.warnings.append(f"{item.title} là ứng dụng mặc định của Ubuntu Desktop — chỉ gỡ nếu bạn chắc chắn "
                             "không cần (có thể cài lại bằng snap install).")
    base = item.extra.get("base")
    if base:
        plan.warnings.append(f"Snap nền '{base}' vẫn được giữ lại.")
    if not opts.purge:
        plan.warnings.append("Không chọn --purge: snapd sẽ lưu snapshot dữ liệu trong 31 ngày.")
    else:
        plan.warnings.append("--purge: dữ liệu của snap này bị xoá vĩnh viễn, không khôi phục được.")
    _multi_user_warning(plan)
    args = ["snap-remove", item.name]
    if opts.purge:
        args.append("--purge")
    plan.steps.append(Step("cmd", args, root=True, label="Gỡ snap"))


def _plan_flatpak(plan: RemovalPlan, item: SoftwareItem, opts: RemovalOptions):
    plan.supports_purge = plan.supports_autoremove = True
    plan.purge_label = "Xoá vĩnh viễn dữ liệu ứng dụng (--delete-data)"
    plan.autoremove_label = "Gỡ các runtime không còn dùng (--unused)"
    flatpak = which("flatpak")
    if not flatpak:
        plan.error = "Không tìm thấy lệnh flatpak."
        return
    inst = item.extra.get("installation", "system")
    scope = "--user" if inst == "user" else "--system"
    branch = item.extra.get("branch", "")
    ref = f"{item.name}//{branch}" if branch else item.name
    plan.affected.append(AffectedEntry(item.name, f"flatpak ({inst})", item.size))
    plan.freed_bytes = item.size
    # --noninteractive chặn hộp thoại xác thực của flatpak -> chỉ dùng cho bản cài theo người dùng.
    cmd = [flatpak, "uninstall", "-y", scope] + (["--noninteractive"] if inst == "user" else [])
    if opts.purge:
        cmd.append("--delete-data")
        plan.warnings.append("--delete-data: dữ liệu ứng dụng bị xoá vĩnh viễn.")
    cmd.append(ref)
    plan.steps.append(Step("cmd", cmd, label="Gỡ flatpak"))
    if inst != "user":
        _multi_user_warning(plan)
        plan.warnings.append("Flatpak cài cho toàn hệ thống: flatpak sẽ tự hỏi mật khẩu quản trị theo quy tắc riêng "
                             "của nó (tài khoản không phải quản trị sẽ không gỡ được).")
    if opts.autoremove:
        plan.steps.append(Step("cmd", [flatpak, "uninstall", "-y", scope, "--unused"]
                               + (["--noninteractive"] if inst == "user" else []),
                               label="Dọn runtime không dùng"))


def _split_desktop_files(item: SoftwareItem) -> tuple[list[str], list[str]]:
    user, system = [], []
    for f in item.extra.get("desktop_files", []):
        (user if f.startswith(HOME + "/") else system).append(f)
    return user, system


def _same_fs_as_home(path: str) -> bool:
    try:
        return os.stat(os.path.dirname(path)).st_dev == os.stat(HOME).st_dev
    except OSError:
        return False


def _add_root_rm(plan: RemovalPlan, path: str) -> bool:
    """Thêm bước xoá bằng root sau khi kiểm tra TRƯỚC bằng đúng luật của helper."""
    try:
        pathrules.validate_rm_path(path)
    except pathrules.PathRuleError as exc:
        plan.error = f"Không thể xoá {path}: {exc}"
        return False
    plan.steps.append(Step("cmd", ["rm-path", path], root=True, label=f"Xoá {path}"))
    return True


def _finalize_file_plan(plan: RemovalPlan, root_paths: list[str], trash_paths: list[str], size: int | None,
                        main_path: str = ""):
    """Bước root chạy TRƯỚC (một lần xác thực); chỉ khi thành công mới chuyển file người dùng vào Thùng rác."""
    for p in root_paths:
        if not _add_root_rm(plan, p):
            plan.steps.clear()
            return
    if trash_paths:
        plan.steps.append(Step("trash", trash_paths, label="Chuyển vào Thùng rác"))
        not_home = [p for p in trash_paths if not _same_fs_as_home(p)]
        if not_home:
            plan.warnings.append("Một số file nằm trên ổ khác thư mục nhà — Thùng rác có thể không hỗ trợ; "
                                 "nếu vậy bước chuyển vào Thùng rác sẽ báo lỗi (các bước xoá trước đó vẫn đã "
                                 "được thực hiện).")
    if root_paths:
        plan.warnings.append("Các mục sau sẽ bị xoá VĨNH VIỄN bằng quyền quản trị (không vào Thùng rác): "
                             + ", ".join(root_paths))
    # Dung lượng chính thuộc về file/thư mục chương trình (item.path); lối tắt .desktop không đáng kể.
    if main_path in root_paths:
        plan.freed_bytes = size
    elif main_path in trash_paths:
        plan.trashed_bytes = size


def _plan_appimage(plan: RemovalPlan, item: SoftwareItem, opts: RemovalOptions):
    path = item.path
    plan.affected.append(AffectedEntry(path, "file AppImage", item.size))
    user_desk, sys_desk = _split_desktop_files(item)
    for f in user_desk + sys_desk:
        plan.affected.append(AffectedEntry(f, "lối tắt menu"))
    candidates = [path] + user_desk
    trash = [p for p in candidates if p.startswith(HOME + "/") and os.access(os.path.dirname(p), os.W_OK)]
    root_paths = [p for p in candidates + sys_desk if p not in trash]
    _finalize_file_plan(plan, root_paths, trash, item.size, path)
    if plan.trashed_bytes:
        plan.warnings.append("File được chuyển vào Thùng rác (có thể khôi phục). Dung lượng chỉ được giải "
                             "phóng khi bạn dọn Thùng rác.")


def _plan_manual(plan: RemovalPlan, item: SoftwareItem, opts: RemovalOptions):
    path = item.path
    units = systemd_units_using(path)
    if units:
        plan.blocked_reasons.append(f"{path} đang được dịch vụ systemd dùng ({', '.join(units[:5])}). "
                                    "Hãy dừng/gỡ dịch vụ đó trước.")
    plan.affected.append(AffectedEntry(path, "thư mục" if os.path.isdir(path) else "file", item.size))
    user_desk, sys_desk = _split_desktop_files(item)
    for f in user_desk + sys_desk:
        plan.affected.append(AffectedEntry(f, "lối tắt menu"))
    if path.startswith(HOME + "/") and os.access(os.path.dirname(path), os.W_OK):
        root_paths, trash = sys_desk, [path] + user_desk
    else:
        root_paths, trash = [path] + sys_desk, user_desk
    _finalize_file_plan(plan, root_paths, trash, item.size, path)
    plan.warnings.append("Phần mềm cài tay có thể còn file cấu hình/dịch vụ ở nơi khác; công cụ không tự dọn được.")
    if root_paths:
        _multi_user_warning(plan)


def _plan_pip(plan: RemovalPlan, item: SoftwareItem, opts: RemovalOptions):
    plan.affected.append(AffectedEntry(item.name, item.origin, item.size))
    plan.freed_bytes = item.size
    kind = item.extra.get("kind")
    if kind == "pipx":
        pipx = which("pipx") or (os.path.join(HOME, ".local/bin/pipx")
                                 if os.access(os.path.join(HOME, ".local/bin/pipx"), os.X_OK) else None)
        if not pipx:
            plan.error = "Không tìm thấy lệnh pipx để gỡ."
            return
        plan.steps.append(Step("cmd", [pipx, "uninstall", item.name], label="Gỡ bằng pipx"))
        return
    pyver = item.extra.get("pyver", "")
    py = f"/usr/bin/python3.{pyver}" if pyver else ""
    if not py or not os.path.isfile(py):
        plan.error = (f"Python 3.{pyver} không còn trên máy — thư mục {item.path} là site-packages mồ côi, "
                      "pip không gỡ được.")
        return
    pyflags = ["-E", "-P"] if int(pyver or 0) >= 11 else ["-E"]
    if run([py, *pyflags, "-m", "pip", "--version"], timeout=60, env=clean_env()).returncode != 0:
        plan.error = f"{py} chưa có pip. Hãy cài gói python3-pip rồi thử lại."
        return
    if item.extra.get("scope") == "system":
        plan.steps.append(Step("cmd", ["pip-remove", item.name, "--site", item.path], root=True,
                               label="Gỡ bằng pip"))
        _multi_user_warning(plan)
    else:
        # Đúng trình thông dịch của site-packages này; -E bỏ qua biến PYTHON* (venv/conda...).
        import importlib.metadata as md

        norm = re.sub(r"[-_.]+", "-", item.name).lower()
        if not any(re.sub(r"[-_.]+", "-", d.metadata.get("Name") or "").lower() == norm
                   for d in md.distributions(path=[item.path])):
            plan.error = f"Không còn thấy gói {item.name} trong {item.path}."
            return
        # -P: không nạp module từ thư mục hiện tại; -E: bỏ biến PYTHON* (venv/conda...)
        plan.steps.append(Step("cmd", [py, *pyflags, "-m", "pip", "uninstall", "-y", "--break-system-packages",
                                       item.name], label="Gỡ bằng pip"))
    plan.warnings.append("pip không gỡ các thư viện phụ thuộc được cài kèm.")


def _plan_npm(plan: RemovalPlan, item: SoftwareItem, opts: RemovalOptions):
    plan.affected.append(AffectedEntry(item.name, item.origin, item.size))
    plan.freed_bytes = item.size
    prefix = item.extra.get("prefix", "")
    npm = item.extra.get("npm", "")
    if not prefix or not npm:
        plan.error = "Không xác định được thư mục npm toàn cục."
        return
    if item.extra.get("needs_root"):
        plan.steps.append(Step("cmd", ["npm-remove", item.name, "--prefix", prefix], root=True,
                               label="Gỡ bằng npm"))
        _multi_user_warning(plan)
    else:
        node, npm_real = item.extra.get("node", ""), item.extra.get("npm_real", "")
        if not node or not npm_real:
            plan.error = "Không tìm thấy node/npm để gỡ."
            return
        plan.steps.append(Step("cmd", [node, npm_real, "--prefix", prefix, "uninstall", "-g", item.name],
                               label="Gỡ bằng npm",
                               env={"PATH": os.path.dirname(node) + ":/usr/local/bin:/usr/bin:/bin"}))


_PLANNERS = {
    Source.APT: _plan_apt, Source.THIRD_PARTY: _plan_apt, Source.DEB: _plan_apt,
    Source.SNAP: _plan_snap, Source.FLATPAK: _plan_flatpak, Source.APPIMAGE: _plan_appimage,
    Source.MANUAL: _plan_manual, Source.PIP: _plan_pip, Source.NPM: _plan_npm,
}


def make_plan(item: SoftwareItem, opts: RemovalOptions | None = None) -> RemovalPlan:
    opts = opts or RemovalOptions()
    clear_caches()  # luôn dùng trạng thái hiện tại của máy
    plan = RemovalPlan(item=item, options=opts)
    if item.protected_reason:
        plan.blocked_reasons.append(item.protected_reason)
    try:
        _PLANNERS[item.source](plan, item, opts)
    except Exception as exc:
        plan.error = f"Không lập được kế hoạch gỡ: {type(exc).__name__}: {exc}"
    if plan.needs_root and not plan.error:
        try:
            helper_path()
        except HelperUnavailable as exc:
            plan.error = str(exc)
    plan.blocked_reasons = list(dict.fromkeys(display(r) for r in plan.blocked_reasons))
    plan.warnings = [display(w) for w in plan.warnings]
    plan.error = display(plan.error)
    return plan
