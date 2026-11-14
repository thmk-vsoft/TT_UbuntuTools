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
                         installed_initramfs_providers, purge_reason, boot_hook_warnings, clear_caches,
                         DPKG_INTERRUPTED_MSG, HELD_MARK, dpkg_interrupted, held_reason)

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
    if HELD_MARK in res.stdout.lower():
        plan.blocked_reasons.append(held_reason(item.name))
    if dpkg_interrupted():
        plan.blocked_reasons.append(DPKG_INTERRUPTED_MSG)
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


def running_pids(paths: list[str]) -> list[str]:
    """PID của tiến trình (của người dùng hiện tại) đang chạy từ / làm việc trong các đường dẫn này."""
    paths = [p.rstrip("/") for p in paths if p]
    uid = os.getuid()
    out = []

    def inside(t: str) -> bool:
        return any(t == p or t.startswith(p + "/") for p in paths)

    try:
        pids = [d for d in os.listdir("/proc") if d.isdigit()]
    except OSError:
        return []
    for pid in pids:
        if pid == str(os.getpid()):
            continue
        try:
            if os.stat(f"/proc/{pid}").st_uid != uid:
                continue
            targets = []
            for link in ("exe", "cwd"):
                try:
                    targets.append(os.readlink(f"/proc/{pid}/{link}"))
                except OSError:
                    pass
            with open(f"/proc/{pid}/cmdline", "rb") as fh:
                argv0 = fh.read(4096).split(b"\0", 1)[0].decode("utf-8", "replace")
            targets.append(argv0)
        except OSError:
            continue
        if any(t and inside(t) for t in targets):
            out.append(pid)
    return out


def _home_path_ok(path: str, state: str, root: str, home: str | None = None, target: str = "") -> str:
    """'' nếu được chuyển `path` vào Thùng rác; nếu không, trả về lý do. Kiểm tra lại từ đầu (không tin collector)."""
    from ..collectors import home_apps as ha

    home = os.path.realpath(home or HOME)
    norm = os.path.normpath(path)
    rel = ha._rel(norm, home)
    if norm != path or rel is None:
        return f"{path} nằm ngoài thư mục nhà"
    if os.path.realpath(os.path.dirname(norm)) != os.path.dirname(norm):
        return f"{path} nằm trong thư mục là liên kết tượng trưng"
    parent = os.path.dirname(rel)
    if norm == root:
        if state == "ok":
            if not ha.is_install_root(norm, home):
                return f"{path} không còn là thư mục chương trình hợp lệ"
            proj = ha.looks_like_project(norm, target)
            if proj:
                return f"{path}: {proj} — có thể là dự án mã nguồn / dữ liệu của bạn"
            return ""
        if state == "standalone":
            return "" if ha.in_bin_dir(norm, home) or ha.is_install_root(norm, home) else \
                f"{path} không nằm trong ~/.local/bin hoặc ~/bin"
        return f"{path}: trạng thái {state} không được gỡ"
    if parent in ha.shortcut_dirs(home):
        return "" if norm.endswith(".desktop") else f"{path} không phải file lối tắt .desktop"
    if os.path.dirname(norm) in ha.bin_dirs(home):
        return ""
    if ha.icon_path_ok(norm, home):
        return ""
    return f"{path} không thuộc loại file công cụ được phép gỡ"


_HOME_KIND_LABEL = {"root_dir": "thư mục chương trình", "root_file": "file chương trình", "bin": "lệnh / script đi kèm",
                    "shortcut_menu": "lối tắt menu", "shortcut_desktop": "lối tắt ngoài màn hình",
                    "autostart": "tự chạy khi đăng nhập", "icon": "biểu tượng"}


def _home_label(path: str, root: str) -> str:
    from ..collectors import home_apps as ha

    home = os.path.realpath(HOME)
    if path == root:
        return _HOME_KIND_LABEL["root_dir" if os.path.isdir(path) and not os.path.islink(path) else "root_file"]
    parent = os.path.dirname(ha._rel(path, home) or "")
    if os.path.dirname(path) in ha.bin_dirs(home):
        return _HOME_KIND_LABEL["bin"]
    if parent == ".local/share/applications":
        return _HOME_KIND_LABEL["shortcut_menu"]
    if parent == ".config/autostart":
        return _HOME_KIND_LABEL["autostart"]
    if parent in ha.desktop_dirs(home):
        return _HOME_KIND_LABEL["shortcut_desktop"]
    return _HOME_KIND_LABEL["icon"]


def _folder_preview(root: str, limit: int = 12) -> str:
    try:
        names = sorted(os.listdir(root), key=str.lower)
    except OSError:
        return "(không đọc được nội dung)"
    shown = [n + ("/" if os.path.isdir(os.path.join(root, n)) and not os.path.islink(os.path.join(root, n)) else "")
             for n in names[:limit]]
    more = f" … (tổng {len(names)} mục)" if len(names) > limit else ""
    return ", ".join(shown) + more if shown else "(trống)"


def _under_path(path: str, root: str) -> bool:
    return bool(root) and (path == root or path.startswith(root.rstrip("/") + "/"))


def _plan_home(plan: RemovalPlan, item: SoftwareItem, opts: RemovalOptions):
    """Phần mềm tự cài trong thư mục nhà: chỉ chuyển vào Thùng rác, không cần quyền quản trị.

    Lối tắt, script khởi chạy, icon (chắc chắn thuộc chương trình) được gỡ ngay. THƯ MỤC chương trình chỉ được
    chuyển vào Thùng rác khi người dùng tự bật tuỳ chọn (plan.purge), sau khi xem danh sách nội dung của nó —
    không bộ luật tự động nào chắc chắn 100% một thư mục chỉ chứa chương trình."""
    from ..models import human_size
    from ..utils import mount_points, mounts_under

    home = os.path.realpath(HOME)
    state = item.extra.get("home_state", "")
    if state not in ("ok", "standalone", "broken"):
        return  # unmanaged / ambiguous: protected_reason đã chặn
    root = item.extra.get("root", "") if state != "broken" else ""
    if root and not os.path.lexists(root):
        plan.error = f"{root} không còn trên máy (có thể đã bị gỡ). Hãy làm mới danh sách."
        return
    folder = root if state == "ok" and os.path.isdir(root) and not os.path.islink(root) else ""
    include_folder = bool(folder) and opts.purge
    if folder:
        plan.supports_purge = True
        size = human_size(item.size) if item.size is not None else "không rõ dung lượng"
        plan.purge_label = f"Chuyển cả thư mục chương trình vào Thùng rác ({size})"
    if include_folder:
        try:
            st = os.stat(folder)
        except OSError as exc:
            plan.error = f"Không đọc được {folder}: {exc.strerror}"
            return
        if st.st_uid != os.getuid() or not os.access(folder, os.W_OK | os.X_OK):
            plan.blocked_reasons.append(
                f"{folder} thuộc người dùng khác hoặc chỉ đọc (có thể đã từng chạy bằng sudo) — không chuyển vào "
                f"Thùng rác được. Hãy sửa quyền (vd. sudo chown -R $USER: '{folder}') rồi thử lại.")
            return
        units = systemd_units_using(folder, unit_dirs=[os.path.join(home, ".config/systemd/user"),
                                                       os.path.join(home, ".local/share/systemd/user")])
        if units:
            plan.blocked_reasons.append(f"{folder} đang được dịch vụ systemd của bạn dùng ({', '.join(units[:4])}). "
                                        "Hãy dừng / gỡ dịch vụ đó trước.")
            return
    points = mount_points()
    trash = []
    extra_links = item.extra.get("folder_links", []) if include_folder else []
    for p in list(item.extra.get("trash_paths", [])) + extra_links:
        if folder and p == folder and not include_folder:
            continue
        if p in extra_links and (not os.path.islink(p) or not _under_path(os.path.realpath(p), folder)):
            continue  # liên kết đã đổi đích: để nguyên
        if not os.path.lexists(p):
            continue  # đã biến mất từ lúc quét
        why = _home_path_ok(p, state, root, home, item.extra.get("target", ""))
        if why:
            plan.error = "Không thể gỡ an toàn: " + why + ". Hãy làm mới danh sách."
            return
        if os.path.isdir(p) and not os.path.islink(p) and mounts_under(p, points):
            plan.blocked_reasons.append(f"{p} có ổ đĩa / ổ mạng gắn bên trong — không xoá.")
            return
        if not os.access(os.path.dirname(p), os.W_OK):
            plan.error = f"Không có quyền ghi vào {os.path.dirname(p)}."
            return
        trash.append(p)
    if folder and include_folder:
        plan.warnings.append(f"Thư mục {folder} sẽ vào Thùng rác cùng MỌI THỨ bên trong: {_folder_preview(folder)}. "
                             "Hãy chắc chắn trong đó không có file của bạn (dự án, dữ liệu, bản lưu game...).")
    elif folder:
        plan.kept_paths.append(folder)
        plan.warnings.append(f"Thư mục chương trình {folder} được GIỮ LẠI. Bật «Chuyển cả thư mục chương trình vào "
                             f"Thùng rác» ở mục Tuỳ chọn nếu muốn gỡ cả nó — nội dung: {_folder_preview(folder)}.")
    if not trash:
        if folder and not include_folder:
            plan.blocked_reasons.append("Chỉ còn thư mục chương trình — bật «Chuyển cả thư mục chương trình vào "
                                        "Thùng rác» ở mục Tuỳ chọn để gỡ nó.")
        else:
            plan.error = "Không còn gì để gỡ (có thể đã bị gỡ). Hãy làm mới danh sách."
        return
    for p in trash:
        plan.affected.append(AffectedEntry(p, _home_label(p, root), item.size if p == root else None))
    main = root if (root and root in trash) else ""
    _finalize_file_plan(plan, [], trash, item.size if main else None, main)
    pids = running_pids([root] + item.extra.get("launchers", [])) if root else []
    if pids:
        plan.warnings.append(f"Chương trình đang chạy (PID {', '.join(pids[:6])}) — hãy đóng nó trước khi gỡ.")
    plan.warnings.append("Mọi mục được chuyển vào Thùng rác (có thể khôi phục). Dung lượng chỉ được giải phóng khi "
                         "bạn dọn Thùng rác.")
    if state != "broken":
        plan.warnings.append("Dữ liệu và cấu hình của chương trình ở ~/.config, ~/.local/share, thư mục Tải về... "
                             + ("(ngoài thư mục chương trình ở trên) " if include_folder else "") + "KHÔNG bị xoá.")


def _plan_manual(plan: RemovalPlan, item: SoftwareItem, opts: RemovalOptions):
    if item.extra.get("kind") == "home":
        _plan_home(plan, item, opts)
        return
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
                               env={"PATH": os.path.dirname(node) + ":/usr/local/bin:/usr/bin:/bin",
                                    "NPM_CONFIG_UPDATE_NOTIFIER": "false"}))


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
