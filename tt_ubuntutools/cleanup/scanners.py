"""Tìm dữ liệu có thể dọn và phân loại theo MỨC AN TOÀN.

Nguyên tắc (giống phần mềm trong thư mục nhà): chỉ nhận đúng mẫu, còn lại khoá. Mỗi loại có bộ nhận diện riêng
(DETECTORS) — bước lập kế hoạch và bước xoá gọi LẠI đúng bộ nhận diện đó ngay trước khi xoá.

Mức an toàn:
  safe          xanh lá — công cụ / hệ thống tự tạo lại, bạn không phải làm gì (chọn sẵn)
  review        vàng    — tạo lại được nhưng bạn phải tự làm, hoặc lần dùng sau chậm hơn
  irreversible  đỏ      — xoá là mất hẳn (Thùng rác)
  locked        xám     — công cụ không cho xoá, kèm lý do
"""

from __future__ import annotations

import datetime as dt
import glob
import json
import os
import re
import shutil
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from ..utils import mount_points, mounts_under, run

HOME = os.path.expanduser("~")

SAFE, REVIEW, IRREVERSIBLE, LOCKED = "safe", "review", "irreversible", "locked"
LEVELS = {
    SAFE: ("An toàn", "lvl-safe", "Công cụ / hệ thống tự tạo lại khi cần — bạn không phải làm gì."),
    REVIEW: ("Cân nhắc", "lvl-review", "Tạo lại được nhưng bạn phải tự làm (vd. npm install), hoặc lần dùng sau "
                                        "sẽ chậm hơn."),
    IRREVERSIBLE: ("Không khôi phục được", "lvl-irreversible", "Xoá là mất hẳn — hãy xem kỹ trước."),
    LOCKED: ("Bị khoá", "lvl-locked", "Công cụ không cho xoá; lý do ghi ở từng mục."),
}
GROUPS = [
    ("cache", "Bộ nhớ đệm công cụ", "folder-download-symbolic"),
    ("system", "Hệ thống (cần mật khẩu quản trị)", "system-run-symbolic"),
    ("browser", "Trình duyệt & VS Code", "web-browser-symbolic"),
    ("project", "Dự án & Thùng rác", "folder-symbolic"),
]

# Dự án: thư mục tạo lại được
NODE_LOCKFILES = ("package-lock.json", "npm-shrinkwrap.json", "yarn.lock", "pnpm-lock.yaml", "bun.lockb", "bun.lock")
PY_MANIFESTS = ("pyproject.toml", "setup.py", "setup.cfg", "Pipfile", "requirements.txt")
VENV_NAMES = (".venv", "venv", "env")
VENV_LAYOUT = {"bin", "lib", "lib64", "include", "share", "etc", "Scripts", "Lib", "Include", "pyvenv.cfg",
               ".gitignore", "CACHEDIR.TAG", "pip-selfcheck.json", "pip.conf", ".lock"}
BUILD_DIRS = (".next", ".nuxt", ".turbo", ".parcel-cache")
PY_CACHE_TAGGED = (".pytest_cache", ".mypy_cache", ".ruff_cache")
SCAN_SKIP_TOP = {"snap", "Trash"}          # thư mục cấp 1 trong HOME không quét
SCAN_BUDGET_SECONDS = 40.0
SCAN_BUDGET_DIRS = 400_000
SIZE_BUDGET_ENTRIES = 3_000_000
SIZE_BUDGET_SECONDS = 25.0


@dataclass
class CleanupItem:
    id: str
    group: str
    kind: str                     # khoá trong DETECTORS (dùng để kiểm tra lại trước khi xoá)
    title: str
    level: str
    note: str                     # ghi chú hậu quả: điều gì xảy ra sau khi xoá
    paths: list[str] = field(default_factory=list)   # đường dẫn sẽ xoá (method 'delete') / để đo dung lượng
    method: str = "delete"        # delete | cmd | root
    argv: list[list[str]] = field(default_factory=list)  # lệnh (cmd) / hành động helper (root)
    size: int | None = None
    size_exact: bool = True
    size_mark: str = ""           # "" chính xác · "≥" chưa đo hết · "≤" có liên kết cứng (có thể giải phóng ít hơn) · "≈" ước tính
    detail: str = ""
    reason: str = ""              # lý do khoá (level == locked)
    modified: dt.datetime | None = None
    env: dict | None = None

    @property
    def default_selected(self) -> bool:
        return self.level == SAFE

    @property
    def needs_root(self) -> bool:
        return self.method == "root"

    @property
    def level_label(self) -> str:
        return LEVELS[self.level][0]


# ---------------------------------------------------------------- tiện ích
def _tilde(p: str) -> str:
    return "~" + p[len(HOME):] if p == HOME or p.startswith(HOME + "/") else p


def measure(paths: list[str], budget_entries: int = SIZE_BUDGET_ENTRIES,
            budget_seconds: float = SIZE_BUDGET_SECONDS) -> tuple[int, bool]:
    """(tổng dung lượng thật trên đĩa, có chính xác không). Không đi theo liên kết, không sang ổ khác.
    Xem measure_marked() để biết thêm có liên kết cứng hay không."""
    total, exact, _hard = measure_marked(paths, budget_entries, budget_seconds)
    return total, exact


def measure_marked(paths: list[str], budget_entries: int = SIZE_BUDGET_ENTRIES,
                   budget_seconds: float = SIZE_BUDGET_SECONDS) -> tuple[int, bool, bool]:
    """(dung lượng, đo hết chưa, có file liên kết cứng không). Mỗi inode chỉ đếm một lần; file có liên kết cứng
    (venv của uv, node_modules của pnpm dùng chung với kho) có thể KHÔNG được giải phóng khi xoá."""
    total, count, exact, hard = 0, 0, True, False
    seen: set[tuple[int, int]] = set()
    deadline = time.monotonic() + budget_seconds
    for path in paths:
        try:
            st = os.lstat(path)
        except OSError:
            continue
        if not os.path.isdir(path) or os.path.islink(path):
            total += st.st_blocks * 512
            continue
        dev = st.st_dev
        stack = [path]
        while stack:
            cur = stack.pop()
            try:
                with os.scandir(cur) as it:
                    for e in it:
                        count += 1
                        if count > budget_entries or (count % 5000 == 0 and time.monotonic() > deadline):
                            return total, False, hard
                        try:
                            est = e.stat(follow_symlinks=False)
                        except OSError:
                            continue
                        if e.is_dir(follow_symlinks=False):
                            if est.st_dev == dev:
                                stack.append(e.path)
                        else:
                            if est.st_nlink > 1:
                                hard = True
                                key = (est.st_dev, est.st_ino)
                                if key in seen:
                                    continue
                                seen.add(key)
                            total += est.st_blocks * 512
            except OSError:
                exact = False
    return total, exact, hard


def _is_real_dir(p: str) -> bool:
    return os.path.isdir(p) and not os.path.islink(p)


def basic_dir_ok(path: str, home: str | None = None) -> str:
    """Kiểm tra chung trước khi xoá một thư mục trong thư mục nhà ('' = được)."""
    home = home or HOME
    norm = os.path.normpath(path)
    if norm != path or not path.startswith(home + "/"):
        return "nằm ngoài thư mục nhà"
    if os.path.realpath(os.path.dirname(path)) != os.path.dirname(path):
        return "nằm trong thư mục là liên kết tượng trưng"
    if not _is_real_dir(path):
        return "không còn là thư mục thật"
    try:
        if os.lstat(path).st_dev != os.stat(home).st_dev:
            return "nằm trên ổ đĩa khác"
    except OSError:
        return "không đọc được"
    if mounts_under(path, mount_points()):
        return "có ổ đĩa gắn bên trong"
    if not os.access(os.path.dirname(path), os.W_OK):
        return "không có quyền ghi"
    return ""


def _hidden_inside(path: str, home: str, allow_last: tuple[str, ...] = ()) -> bool:
    """Đường dẫn có đi qua thư mục ẩn (ruột ứng dụng: ~/.vscode, ~/.config, ~/.local/share...) không."""
    rel = os.path.relpath(path, home).split("/")
    for i, part in enumerate(rel):
        if part.startswith(".") and not (i == len(rel) - 1 and part in allow_last):
            return True
    return False


def _proc_names() -> set[str]:
    """Tên chương trình đang chạy của người dùng hiện tại (để biết trình duyệt có đang mở không)."""
    names = set()
    uid = os.getuid()
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        try:
            if os.stat(f"/proc/{pid}").st_uid != uid:
                continue
            names.add(os.path.basename(os.readlink(f"/proc/{pid}/exe").removesuffix(" (deleted)")))
        except OSError:
            continue
    return names


def _newest_mtime(project: str, skip: set[str]) -> dt.datetime | None:
    best = 0.0
    try:
        with os.scandir(project) as it:
            for e in it:
                if e.name in skip:
                    continue
                try:
                    best = max(best, e.stat(follow_symlinks=False).st_mtime)
                except OSError:
                    continue
    except OSError:
        return None
    return dt.datetime.fromtimestamp(best) if best else None


# ---------------------------------------------------------------- bộ nhận diện (dùng lại khi xoá)
def is_node_modules(path: str) -> str:
    """'' nếu path đúng là node_modules của một dự án cài lại được; ngược lại lý do."""
    parent = os.path.dirname(path)
    if os.path.basename(path) != "node_modules" or not _is_real_dir(path):
        return "không phải thư mục node_modules"
    if not os.path.isfile(os.path.join(parent, "package.json")):
        return "thư mục cha không có package.json"
    if not any(os.path.isfile(os.path.join(parent, f)) for f in NODE_LOCKFILES):
        return ("không có lockfile (package-lock.json / yarn.lock / pnpm-lock.yaml) cùng thư mục — không chắc cài "
                "lại đúng (nếu là gói con của monorepo, hãy dọn ở thư mục gốc)")
    if _has_vcs(path, depth=3):
        return "node_modules chứa kho mã (.git) — có thể là bản fork / thay đổi của bạn chưa đẩy lên; không xoá"
    return ""


def is_project_venv(path: str) -> str:
    parent = os.path.dirname(path)
    if os.path.basename(path) not in VENV_NAMES or not _is_real_dir(path):
        return "không phải môi trường ảo"
    if not os.path.isfile(os.path.join(path, "pyvenv.cfg")):
        return "không có pyvenv.cfg"
    try:
        extra = sorted(n for n in os.listdir(path) if n not in VENV_LAYOUT)
    except OSError:
        return "không đọc được nội dung môi trường ảo"
    if extra:
        return ("môi trường ảo chứa mục không chuẩn (" + ", ".join(extra[:5])
                + ") — có thể là file của bạn hoặc mã nguồn cài kiểu pip -e; không xoá")
    if _has_vcs(path):
        return "môi trường ảo chứa kho mã (.git) — có thể có thay đổi chưa đẩy lên; không xoá"
    if not (any(os.path.isfile(os.path.join(parent, f)) for f in PY_MANIFESTS)
            or glob.glob(os.path.join(glob.escape(parent), "requirements*.txt"))):
        return ("dự án không có pyproject.toml / requirements.txt — không biết cách cài lại. Nếu muốn dọn, hãy lưu "
                "danh sách gói trước: .venv/bin/pip freeze > requirements.txt, rồi quét lại")
    return ""


def _has_vcs(root: str, depth: int = 6, budget: int = 200_000) -> bool:
    """Có thư mục .git / .hg / .svn ở đâu đó bên trong không (fail-closed khi vượt ngân sách)."""
    stack, seen = [(root, 0)], 0
    while stack:
        cur, d = stack.pop()
        try:
            with os.scandir(cur) as it:
                for e in it:
                    seen += 1
                    if seen > budget:
                        return True
                    if e.name in (".git", ".hg", ".svn"):
                        return True
                    if d + 1 < depth and e.is_dir(follow_symlinks=False):
                        stack.append((e.path, d + 1))
        except OSError:
            continue
    return False


def is_build_dir(path: str) -> str:
    parent, name = os.path.dirname(path), os.path.basename(path)
    if name not in BUILD_DIRS or not _is_real_dir(path):
        return "không phải thư mục build"
    pkg = os.path.join(parent, "package.json")
    if not os.path.isfile(pkg):
        return "thư mục cha không có package.json"
    if name == ".next":
        has_cfg = bool(glob.glob(os.path.join(glob.escape(parent), "next.config.*")))
        try:
            with open(pkg, encoding="utf-8", errors="replace") as fh:
                has_dep = '"next"' in fh.read(200_000)
        except OSError:
            has_dep = False
        if not (has_cfg or has_dep):
            return "không phải dự án Next.js"
    return ""


def is_py_cache(path: str) -> str:
    name = os.path.basename(path)
    if not _is_real_dir(path):
        return "không phải thư mục"
    if name == "__pycache__":
        try:
            with os.scandir(path) as it:
                for e in it:
                    if not (e.is_file(follow_symlinks=False) and e.name.endswith((".pyc", ".pyo"))):
                        return "__pycache__ chứa file lạ"
        except OSError:
            return "không đọc được"
        return ""
    if name in PY_CACHE_TAGGED:
        return "" if os.path.isfile(os.path.join(path, "CACHEDIR.TAG")) or \
            os.path.isfile(os.path.join(path, "README.md")) else "thiếu dấu hiệu thư mục cache"
    return "không phải thư mục cache Python"


def is_tool_cache(path: str) -> str:
    known = {os.path.join(HOME, p) for p in (".cache/pip", ".cache/uv", ".cache/pnpm", ".cache/yarn", ".npm/_cacache")}
    return "" if path in known and _is_real_dir(path) else "không phải thư mục bộ nhớ đệm đã biết"


def is_browser_cache(path: str) -> str:
    rel = os.path.relpath(path, os.path.join(HOME, ".cache"))
    parts = rel.split("/")
    ok = (not rel.startswith("..") and len(parts) >= 3 and parts[-1] in ("Cache", "Code Cache", "GPUCache")
          and "/".join(parts[:-2]) in BROWSERS)
    return "" if ok and _is_real_dir(path) else "không phải thư mục cache của trình duyệt"


def is_vscode_obsolete(path: str) -> str:
    base, name = os.path.dirname(path), os.path.basename(path)
    if base not in [os.path.join(HOME, d, "extensions") for d in VSCODE_DIRS] or not _is_real_dir(path):
        return "không phải thư mục tiện ích VS Code"
    obsolete, used = _vscode_state(base)
    if name not in obsolete:
        return "VS Code không đánh dấu tiện ích này là lỗi thời"
    if name in used:
        return "tiện ích vẫn đang được VS Code dùng"
    return ""


def is_trash_entry(path: str) -> str:
    trash = os.path.join(HOME, ".local/share/Trash")
    if os.path.dirname(path) not in (os.path.join(trash, d) for d in ("files", "info", "expunged")):
        return "không nằm trong Thùng rác"
    return ""


DETECTORS = {
    "node_modules": is_node_modules, "venv": is_project_venv, "build": is_build_dir, "pycache": is_py_cache,
    "tool_cache": is_tool_cache, "browser_cache": is_browser_cache, "vscode_obsolete": is_vscode_obsolete,
    "trash": is_trash_entry,
}


def git_tracked_inside(path: str) -> str:
    """Lý do nếu git của dự án đang THEO DÕI file nằm trong path (mã nguồn của bạn, không phải thứ tạo lại được).
    Fail-closed: có kho git mà không hỏi được git -> coi như có."""
    d = os.path.dirname(path)
    top = ""
    while d.startswith(HOME + "/") or d == HOME:
        if os.path.lexists(os.path.join(d, ".git")):
            top = d
            break
        if d == HOME:
            break
        d = os.path.dirname(d)
    if not top:
        return ""
    git = shutil.which("git")
    if not git:
        return "dự án dùng git nhưng không có lệnh git để kiểm tra file được theo dõi"
    try:
        # -c ... ghi đè cấu hình trong .git/config của repo: repo tải về không được chạy lệnh qua fsmonitor / hooks
        res = subprocess.run([git, "-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null",
                              "-c", "core.untrackedCache=false", "-C", top, "ls-files", "-z", "--",
                              os.path.relpath(path, top)],
                             capture_output=True, timeout=15, stdin=subprocess.DEVNULL,
                             env={"PATH": "/usr/bin:/bin", "LC_ALL": "C.UTF-8", "HOME": HOME,
                                  "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null",
                                  "GIT_LITERAL_PATHSPECS": "1"})
    except (OSError, subprocess.TimeoutExpired):
        return "không hỏi được git về các file được theo dõi"
    if res.returncode != 0:
        return "không hỏi được git về các file được theo dõi"
    if res.stdout.strip(b"\0"):
        return "git của dự án đang theo dõi file bên trong (mã nguồn của bạn) — không xoá"
    return ""


def recheck(kind: str, path: str) -> str:
    """Kiểm tra lại NGAY TRƯỚC KHI XOÁ: bộ nhận diện của loại + kiểm tra chung ('' = được xoá)."""
    det = DETECTORS.get(kind)
    if det is None:
        return "loại không được hỗ trợ"
    if kind == "trash":
        if os.path.realpath(os.path.dirname(path)) != os.path.dirname(path):
            return "Thùng rác nằm trong liên kết tượng trưng"
        if os.path.isdir(path) and not os.path.islink(path) and mounts_under(path, mount_points()):
            return "có ổ đĩa gắn bên trong"
        return det(path)
    why = det(path) or basic_dir_ok(path)
    if why:
        return why
    if kind in ("node_modules", "venv", "build", "pycache") and _hidden_inside(
            path, HOME, allow_last=VENV_NAMES + BUILD_DIRS + PY_CACHE_TAGGED + ("node_modules", "__pycache__")):
        return "nằm trong thư mục riêng của ứng dụng"
    if kind in ("node_modules", "venv", "build"):
        return git_tracked_inside(path)
    return ""


# ---------------------------------------------------------------- 1. bộ nhớ đệm công cụ
def _tool_path(name: str) -> str:
    for d in (os.path.join(HOME, ".local/bin"), "/usr/local/bin", "/usr/bin", "/bin"):
        p = os.path.join(d, name)
        if os.path.isfile(p) and os.access(p, os.X_OK):
            return p
    return ""


def scan_caches() -> list[CleanupItem]:
    items = []
    pip_dir = os.path.join(HOME, ".cache/pip")
    if _is_real_dir(pip_dir):
        has_pip = run(["/usr/bin/python3", "-m", "pip", "--version"], timeout=30).returncode == 0
        items.append(CleanupItem(
            id="cache:pip", group="cache", kind="tool_cache", title="Bộ nhớ đệm pip", level=SAFE,
            note="Lần cài gói Python sau sẽ tải lại.", paths=[pip_dir],
            method="cmd" if has_pip else "delete",
            argv=[["/usr/bin/python3", "-m", "pip", "cache", "purge"]] if has_pip else [],
            detail=_tilde(pip_dir)))
    uv_dir = os.path.join(HOME, ".cache/uv")
    if _is_real_dir(uv_dir):
        uv = _tool_path("uv")
        items.append(CleanupItem(
            id="cache:uv", group="cache", kind="tool_cache", title="Bộ nhớ đệm uv", level=SAFE,
            note="Lần cài gói Python bằng uv sau sẽ tải lại.", paths=[uv_dir],
            method="cmd" if uv else "delete", argv=[[uv, "cache", "clean"]] if uv else [],
            detail=_tilde(uv_dir)))
    for rel, title in ((".cache/pnpm", "Bộ nhớ đệm pnpm"), (".cache/yarn", "Bộ nhớ đệm yarn"),
                       (".npm/_cacache", "Bộ nhớ đệm npm")):
        p = os.path.join(HOME, rel)
        if _is_real_dir(p):
            items.append(CleanupItem(
                id="cache:" + rel, group="cache", kind="tool_cache", title=title, level=SAFE,
                note="Lần cài gói Node.js sau sẽ tải lại.", paths=[p], detail=_tilde(p)))
    store = os.path.join(HOME, ".local/share/pnpm/store")
    pnpm = _tool_path("pnpm")
    if _is_real_dir(store) and pnpm:
        whole, _ = measure([store])
        # Không biết trước giải phóng được bao nhiêu -> không cộng vào tổng (size = 0, ghi rõ trong chi tiết).
        items.append(CleanupItem(
            id="cache:pnpm-store", group="cache", kind="cmd_only", title="Kho gói pnpm (gói không dự án nào dùng)",
            level=SAFE, note="Chỉ xoá gói không còn dự án nào dùng (pnpm store prune) — không ảnh hưởng dự án.",
            paths=[], method="cmd", argv=[[pnpm, "store", "prune"]], size=0, size_exact=False, size_mark="?",
            detail=f"{_tilde(store)} · cả kho {whole // 1024 ** 2} MB, phần giải phóng chưa biết trước"))
    return items


# ---------------------------------------------------------------- 2. hệ thống
SNAP_NAME_RE = re.compile(r"[a-z0-9](?:[a-z0-9\-]{0,38}[a-z0-9])?")


def parse_snap_current(text: str) -> dict[str, str]:
    """{tên: bản đang dùng} từ output `snap list` (không --all)."""
    out = {}
    for line in text.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 3 and parts[2].isdigit():
            out[parts[0]] = parts[2]
    return out


def parse_snap_list_all(text: str, current: dict[str, str] | None = None) -> list[tuple[str, str]]:
    """[(tên, bản)] các bản snap CŨ: đánh dấu 'disabled' và KHÔNG phải bản đang dùng (snap bị `snap disable` thì
    bản đang dùng cũng có chữ disabled). Bỏ qua tên lạ (vd. bản cài song song foo_bar)."""
    out = []
    for line in text.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 4 and parts[2].isdigit() and "disabled" in parts[-1].split(","):
            name, rev = parts[0], parts[2]
            if not SNAP_NAME_RE.fullmatch(name):
                continue
            if current is not None and (name not in current or current[name] == rev):
                continue
            out.append((name, rev))
    return out


def parse_journal_usage(text: str) -> int | None:
    m = re.search(r"take up ([\d.]+)([KMGT])", text)
    if not m:
        return None
    return int(float(m.group(1)) * {"K": 1024, "M": 1024 ** 2, "G": 1024 ** 3, "T": 1024 ** 4}[m.group(2)])


JOURNAL_KEEP = "100M"


def scan_system() -> list[CleanupItem]:
    items = []
    debs = glob.glob("/var/cache/apt/archives/*.deb") + glob.glob("/var/cache/apt/archives/partial/*")
    if debs:
        size, _ = measure(debs)
        items.append(CleanupItem(
            id="system:apt", group="system", kind="root", title=f"Gói .deb apt đã tải ({len(debs)} file)",
            level=SAFE, note="apt tải lại nếu cần cài lại; không ảnh hưởng phần mềm đang dùng.",
            method="root", argv=[["apt-clean"]], size=size, detail="/var/cache/apt/archives"))
    if shutil.which("journalctl"):
        res = run(["journalctl", "--disk-usage"], timeout=30)
        used = parse_journal_usage(res.stdout + res.stderr)
        if used and used > 150 * 1024 ** 2:
            items.append(CleanupItem(
                id="system:journal", group="system", kind="root", title="Nhật ký hệ thống (journal) cũ",
                level=SAFE, note=f"Mất nhật ký cũ, giữ lại {JOURNAL_KEEP}B gần nhất để chẩn đoán lỗi.",
                method="root", argv=[["journal-vacuum", "--size", JOURNAL_KEEP]],
                size=used - 100 * 1024 ** 2, size_exact=False, size_mark="≈",
                detail=f"đang dùng {used // 1024 ** 2} MB"))
    if os.path.exists("/usr/bin/snap"):
        res = run(["/usr/bin/snap", "list", "--all"], timeout=60)
        cur = run(["/usr/bin/snap", "list"], timeout=60)
        revs = (parse_snap_list_all(res.stdout, parse_snap_current(cur.stdout))
                if res.returncode == 0 and cur.returncode == 0 else [])
        if revs:
            files = [f"/var/lib/snapd/snaps/{n}_{r}.snap" for n, r in revs]
            size, _ = measure([f for f in files if os.path.exists(f)])
            items.append(CleanupItem(
                id="system:snap", group="system", kind="root",
                title=f"Bản snap cũ không dùng ({len(revs)} bản)", level=SAFE,
                note="Chỉ mất khả năng quay về bản trước của các snap này; bản đang dùng giữ nguyên.",
                method="root", argv=[["snap-remove-revision", n, "--revision", r] for n, r in revs],
                size=size, detail=", ".join(f"{n} ({r})" for n, r in revs[:8]) + (" …" if len(revs) > 8 else "")))
    return items


# ---------------------------------------------------------------- 3. trình duyệt & VS Code
BROWSERS = {"google-chrome": ("Google Chrome", ("chrome",)),
            "chromium": ("Chromium", ("chromium", "chromium-browser", "chrome")),
            "microsoft-edge": ("Microsoft Edge", ("msedge",)),
            "BraveSoftware/Brave-Browser": ("Brave", ("brave",))}
VSCODE_DIRS = (".vscode", ".vscode-oss", ".vscode-insiders")


def _vscode_state(ext_dir: str) -> tuple[set[str], set[str]]:
    obsolete, used = set(), set()
    try:
        with open(os.path.join(ext_dir, ".obsolete"), encoding="utf-8") as fh:
            data = json.load(fh)
        obsolete = {k for k, v in data.items() if v is True and isinstance(k, str)} if isinstance(data, dict) else set()
    except (OSError, ValueError):
        pass
    try:
        with open(os.path.join(ext_dir, "extensions.json"), encoding="utf-8") as fh:
            for e in json.load(fh):
                if not isinstance(e, dict):
                    continue
                loc = e.get("location") or {}
                p = loc.get("path") or loc.get("fsPath") or ""
                if p:
                    used.add(os.path.basename(p.rstrip("/")))
                if e.get("relativeLocation"):
                    used.add(e["relativeLocation"])
    except (OSError, ValueError, TypeError):
        # Không đọc được danh sách đang dùng -> coi như MỌI tiện ích đang dùng (không xoá gì).
        try:
            used = set(os.listdir(ext_dir))
        except OSError:
            used = obsolete
    return obsolete, used


def scan_browser() -> list[CleanupItem]:
    items = []
    running = _proc_names()
    for sub, (title, procs) in BROWSERS.items():
        base = os.path.join(HOME, ".cache", sub)
        if not _is_real_dir(base):
            continue
        paths = sorted(p for p in glob.glob(os.path.join(glob.escape(base), "*", "*"))
                       if os.path.basename(p) in ("Cache", "Code Cache", "GPUCache") and _is_real_dir(p))
        if not paths:
            continue
        is_running = any(n in procs for n in running)  # tên CHÍNH XÁC (không nhầm chrome_crashpad_handler của Electron)
        items.append(CleanupItem(
            id="browser:" + sub, group="browser", kind="browser_cache", title=f"Bộ nhớ đệm {title}",
            level=LOCKED if is_running else REVIEW,
            note="Trang web tải chậm hơn lần đầu; không mất đăng nhập, mật khẩu hay lịch sử.",
            reason=f"{title} đang mở — hãy đóng trình duyệt rồi quét lại." if is_running else "",
            paths=paths, detail=_tilde(base)))
    for d in VSCODE_DIRS:
        ext_dir = os.path.join(HOME, d, "extensions")
        if not _is_real_dir(ext_dir):
            continue
        obsolete, used = _vscode_state(ext_dir)
        paths = sorted(os.path.join(ext_dir, n) for n in obsolete - used if _is_real_dir(os.path.join(ext_dir, n)))
        if paths:
            items.append(CleanupItem(
                id="browser:vscode:" + d, group="browser", kind="vscode_obsolete",
                title=f"Bản cũ của tiện ích VS Code ({len(paths)})", level=SAFE,
                note="VS Code đã thay chúng bằng bản mới và tự đánh dấu là lỗi thời.",
                paths=paths, detail=", ".join(os.path.basename(p) for p in paths[:4]) + (" …" if len(paths) > 4 else "")))
    return items


# ---------------------------------------------------------------- 4. dự án & Thùng rác
def _protected_roots() -> dict[str, str]:
    """Thư mục của chương trình đã cài trong thư mục nhà (GomGo, app giải nén vào ~/Programs...) và nội dung các
    lối tắt / lệnh trong ~/.local/bin — node_modules, .venv, .next nằm trong đó là RUỘT chương trình, không dọn.
    Nếu không xác định được (lỗi), trả về '__error__' để mọi mục dự án bị khoá (fail-closed)."""
    out: dict[str, str] = {}
    try:
        from ..collectors.home_apps import find_home_apps, user_desktop_entries
        for a in find_home_apps(home=HOME):
            for r in {a.root, os.path.dirname(a.target) if a.target else ""}:
                if r and os.path.isdir(r) and r.startswith(HOME + "/"):
                    out[r] = ("thuộc chương trình đã cài ("
                              + (a.entries[0].title if a.entries else os.path.basename(r)) + ")")
        texts = [e.exec for e in user_desktop_entries(HOME)]
    except Exception as exc:
        return {"__error__": f"không kiểm tra được chương trình đã cài ({type(exc).__name__})"}
    for d in (os.path.join(HOME, ".config/systemd/user"), os.path.join(HOME, ".local/share/systemd/user"),
              os.path.join(HOME, ".config/autostart")):
        for f in glob.glob(os.path.join(glob.escape(d), "**", "*"), recursive=True):
            try:
                if os.path.isfile(f) and os.path.getsize(f) < 256 * 1024:
                    with open(f, "rb") as fh:
                        texts.append(fh.read().decode("utf-8", "replace"))
            except OSError:
                continue
    try:
        texts.append(run(["crontab", "-l"], timeout=10).stdout)
    except Exception:
        pass
    for b in (os.path.join(HOME, ".local/bin"), os.path.join(HOME, "bin")):
        try:
            for n in os.listdir(b):
                p = os.path.join(b, n)
                if os.path.islink(p):
                    texts.append(os.path.realpath(p))
                    try:
                        texts.append(os.path.join(os.path.dirname(p), os.readlink(p)))
                    except OSError:
                        pass
                elif os.path.isfile(p) and os.path.getsize(p) < 64 * 1024:
                    with open(p, "rb") as fh:
                        texts.append(fh.read().decode("utf-8", "replace"))
        except OSError:
            continue
    out["__texts__"] = "\n".join(texts)
    return out


def _protected_reason(path: str, protected: dict[str, str]) -> str:
    if "__error__" in protected:
        return protected["__error__"]
    proj = os.path.dirname(path)
    for root, why in protected.items():
        if root.startswith("__"):
            continue
        if path.startswith(root + "/") or path == root or proj == root:
            return why
    texts = protected.get("__texts__", "")
    for cand in (path, proj):
        if cand + "/" in texts or cand + '"' in texts or texts.endswith(cand) or (cand + "\n") in texts \
                or _tilde(cand) + "/" in texts:
            return "đang được lối tắt / lệnh trong ~/.local/bin dùng"
    return ""


def walk_projects(home: str | None = None, budget_seconds: float = SCAN_BUDGET_SECONDS,
                  budget_dirs: int = SCAN_BUDGET_DIRS) -> tuple[dict[str, list[str]], bool]:
    """Duyệt thư mục nhà (không vào thư mục ẩn, liên kết, ổ khác). Trả về ({loại: [đường dẫn]}, quét hết?)."""
    home = home or HOME
    found: dict[str, list[str]] = {"node_modules": [], "venv": [], "build": [], "pycache": []}
    try:
        home_dev = os.stat(home).st_dev
    except OSError:
        return found, False
    deadline = time.monotonic() + budget_seconds
    count = 0
    stack = [home]
    while stack:
        cur = stack.pop()
        count += 1
        if count > budget_dirs or time.monotonic() > deadline:
            return found, False
        try:
            entries = list(os.scandir(cur))
        except OSError:
            continue
        for e in entries:
            try:
                if not e.is_dir(follow_symlinks=False):
                    continue
                if e.stat(follow_symlinks=False).st_dev != home_dev:
                    continue
            except OSError:
                continue
            name = e.name
            if cur == home and name in SCAN_SKIP_TOP:
                continue
            if name == "node_modules":
                found["node_modules"].append(e.path)
                continue
            if name in VENV_NAMES and os.path.isfile(os.path.join(e.path, "pyvenv.cfg")):
                found["venv"].append(e.path)
                continue
            if name in BUILD_DIRS:
                found["build"].append(e.path)
                continue
            if name == "__pycache__" or name in PY_CACHE_TAGGED:
                found["pycache"].append(e.path)
                continue
            if name.startswith("."):
                continue  # thư mục ẩn = ruột ứng dụng / cấu hình: không quét
            stack.append(e.path)
    return found, True


_RUNTIME_EXE = re.compile(r"node|nodejs|bun|deno|python[0-9.]*|pypy[0-9.]*|uvicorn|gunicorn|npm|npx|pnpm|yarn")


def _user_procs() -> list[tuple[str, str, str]]:
    out = []
    uid = os.getuid()
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        try:
            if os.stat(f"/proc/{pid}").st_uid != uid:
                continue
            exe = os.readlink(f"/proc/{pid}/exe").removesuffix(" (deleted)")  # chương trình vừa được nâng cấp
            out.append((pid, os.path.basename(exe), os.readlink(f"/proc/{pid}/cwd").removesuffix(" (deleted)")))
        except OSError:
            continue
    return out


def project_in_use(paths: list[str]) -> str:
    """Lý do nếu có chương trình đang dùng các thư mục này / đang chạy trong dự án của chúng (kiểm tra lại lúc
    lập kế hoạch dọn — dev server có thể vừa được bật sau khi quét)."""
    from ..removal.planner import running_pids
    pids = running_pids(paths)
    if not pids:
        proj = [os.path.dirname(p) for p in paths]
        pids = [pid for pid, exe, cwd in _user_procs()
                if _RUNTIME_EXE.fullmatch(exe) and any(cwd == d or cwd.startswith(d + "/") for d in proj)]
    return f"đang được chương trình chạy dùng (PID {', '.join(pids[:4])}) — hãy dừng nó trước" if pids else ""


def _project_of(path: str) -> str:
    return os.path.dirname(path)


def scan_projects() -> list[CleanupItem]:
    items: list[CleanupItem] = []
    found, complete = walk_projects()
    protected = _protected_roots() if any(found[k] for k in ("node_modules", "venv", "build")) else {}
    def lock_running(paths: list[str]) -> str:
        # cả runtime (node, python, bun…) đang chạy với thư mục làm việc trong dự án: dev server, next start…
        return project_in_use(paths)

    for kind in ("node_modules", "build"):
        for p in sorted(found[kind]):
            why = recheck(kind, p) or _protected_reason(p, protected) or lock_running([p])
            proj = _project_of(p)
            name = os.path.basename(p)
            if kind == "node_modules":
                title = f"node_modules · {os.path.basename(proj)}"
                note = "Phải chạy lại npm install (hoặc yarn / pnpm install, vài phút, cần mạng) trước khi làm tiếp dự án."
            else:
                title = f"{name} · {os.path.basename(proj)}"
                note = ("Thư mục build; lần build / chạy dev sau sẽ lâu hơn. Nếu đang chạy bản production "
                        "(next start), phải build lại trước khi chạy.")
            items.append(CleanupItem(
                id=f"project:{p}", group="project", kind=kind, title=title,
                level=LOCKED if why else REVIEW, note=note, reason=why, paths=[p],
                modified=_newest_mtime(proj, {name}), detail=_tilde(proj)))
    for p in sorted(found["venv"]):
        why = recheck("venv", p) or _protected_reason(p, protected) or lock_running([p])
        proj = _project_of(p)
        name = os.path.basename(p)
        if os.path.isfile(os.path.join(proj, "uv.lock")):
            how = "chạy uv sync"
        elif os.path.isfile(os.path.join(proj, "poetry.lock")):
            how = "chạy poetry install"
        elif glob.glob(os.path.join(glob.escape(proj), "requirements*.txt")):
            req = os.path.basename(sorted(glob.glob(os.path.join(glob.escape(proj), "requirements*.txt")))[0])
            how = f"chạy python3 -m venv {name} rồi {name}/bin/pip install -r {req}"
        else:
            how = f"chạy python3 -m venv {name} rồi {name}/bin/pip install -e ."
        items.append(CleanupItem(
            id=f"project:{p}", group="project", kind="venv",
            title=f"{name} · {os.path.basename(proj)}", level=LOCKED if why else REVIEW,
            note=f"Phải tạo lại môi trường ảo ({how}) — các gói cài tay thêm sẽ mất.",
            reason=why, paths=[p], modified=_newest_mtime(proj, {os.path.basename(p)}), detail=_tilde(proj)))
    # Cache Python: gom theo thư mục cấp cao nhất chứa chúng (theo từng dự án)
    by_proj: dict[str, list[str]] = {}
    for p in found["pycache"]:
        if recheck("pycache", p):
            continue
        rel = os.path.relpath(p, HOME).split("/")
        key = os.path.join(HOME, *rel[:2]) if len(rel) > 2 else os.path.dirname(p)
        by_proj.setdefault(key, []).append(p)
    for proj, paths in sorted(by_proj.items()):
        items.append(CleanupItem(
            id=f"project:pycache:{proj}", group="project", kind="pycache",
            title=f"Cache Python · {os.path.basename(proj)} ({len(paths)} thư mục)", level=SAFE,
            note="__pycache__ / .pytest_cache…: Python và công cụ test tự tạo lại khi chạy.",
            paths=sorted(paths), detail=_tilde(proj)))
    trash = os.path.join(HOME, ".local/share/Trash")
    if _is_real_dir(trash):
        entries = []
        for d in ("files", "info", "expunged"):
            dd = os.path.join(trash, d)
            if _is_real_dir(dd):
                entries += [os.path.join(dd, n) for n in os.listdir(dd)]
        n_files = len([e for e in entries if "/files/" in e])
        if entries:
            items.append(CleanupItem(
                id="project:trash", group="project", kind="trash", title=f"Thùng rác ({n_files} mục)",
                level=IRREVERSIBLE, note="Mọi thứ trong Thùng rác sẽ mất vĩnh viễn — hãy mở Thùng rác xem trước.",
                paths=sorted(entries), detail=_tilde(trash)))
    if not complete:
        items.append(CleanupItem(
            id="project:incomplete", group="project", kind="none", title="Chưa quét hết thư mục nhà",
            level=LOCKED, note="", reason="Thư mục nhà quá lớn để quét trong thời gian cho phép; một số dự án có "
                                          "thể chưa được liệt kê.", paths=[]))
    return items


# ---------------------------------------------------------------- tổng hợp
SCANNERS = {"cache": scan_caches, "system": scan_system, "browser": scan_browser, "project": scan_projects}


def scan_all(progress=None) -> tuple[list[CleanupItem], dict[str, str]]:
    """Quét song song 4 nhóm rồi đo dung lượng. progress(nhóm) được gọi khi mỗi nhóm xong."""
    items: list[CleanupItem] = []
    errors: dict[str, str] = {}

    def _run(name):
        try:
            return name, SCANNERS[name](), None
        except Exception as exc:  # một nhóm lỗi không làm hỏng nhóm khác
            return name, [], f"{type(exc).__name__}: {exc}"

    with ThreadPoolExecutor(max_workers=4) as pool:
        for name, got, err in pool.map(_run, SCANNERS):
            items.extend(got)
            if err:
                errors[name] = err
            if progress:
                progress(name)

    def _size(it: CleanupItem):
        if it.size is None and it.paths:
            it.size, it.size_exact, hard = measure_marked(it.paths)
            it.size_mark = "≥" if not it.size_exact else ("≤" if hard else "")

    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(_size, items))
    return items, errors
