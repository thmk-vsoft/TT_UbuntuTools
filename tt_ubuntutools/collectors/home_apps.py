"""Phần mềm tự cài vào thư mục nhà (không qua apt/snap/flatpak/pip/npm).

Ví dụ thực tế:
  * GomGo: lối tắt ~/.local/share/applications/gomgo.desktop -> script ~/.local/bin/multi-downloader
    (cd ~/.local/share/multi-downloader-app; exec ~/.local/share/multi-downloader-app/.venv/bin/python -m ...)
  * Telegram / Android Studio: giải nén vào ~/Programs/<tên>/, lối tắt trỏ vào file chạy bên trong.
  * uv / pnpm / cloudflared: một file chạy đứng riêng trong ~/.local/bin.

NGUYÊN TẮC: CHỈ CHẤP NHẬN NHỮNG GÌ KHỚP ĐÚNG MẪU, CÒN LẠI KHOÁ (liệt kê kèm lý do, không gỡ). Mọi thứ gỡ đều
vào Thùng rác. Một chương trình "ok" (gỡ được) phải thoả TẤT CẢ:
  1. Thư mục chương trình = mục con trực tiếp của một thư mục cài đặt quen thuộc (INSTALL_BASES).
  2. File chạy nằm đúng vị trí của một bản cài: <thư mục>/<file>, <thư mục>/bin/<file> hoặc
     <thư mục>/{.venv,venv}/bin/python* — nên thư mục gom nhóm (~/Programs/Games/Celeste/...) hay conda
     (~/opt/miniconda3/envs/x/bin/...) không bao giờ khớp.
  3. Có bằng chứng là bản cài: file chạy là ELF / AppImage / python của venv, hoặc thư mục có chương trình ELF.
  4. Lối tắt / script khởi chạy chỉ nhắc tới đường dẫn trong chính thư mục đó (hoặc thư mục dữ liệu như
     ~/.local/share/TelegramDesktop). Tham số là file của bạn (java -jar ~/MyCode/x.jar), đường dẫn tương
     đối, biến, nhiều lệnh, if/case/& ... -> khoá.
  5. Không có dấu hiệu dự án mã nguồn (.git ở mọi cấp <= 5, file dự án) — dò không hết thì khoá.
  6. Thư mục không được lối tắt nào khác dùng tới.
"""

from __future__ import annotations

import glob
import os
import re
import shlex
from dataclasses import dataclass, field

from ..desktop_index import DesktopEntry, parse_desktop_file

HOME = os.path.expanduser("~")

# Thư mục (tương đối với HOME) mà mỗi mục con trực tiếp thường là MỘT chương trình đã cài.
INSTALL_BASES = ("Programs", "programs", "Applications", "opt", ".opt", ".local/opt", ".local/share",
                 ".local/lib")
SHALLOW_BASES = (".local/share", ".local/lib")  # thư mục dữ liệu chung: luật chặt hơn
BIN_DIRS = (".local/bin", "bin")
APP_DIRS = (".local/share/applications", ".config/autostart")
DATA_DIRS = (".local/share", ".config", ".cache", ".var")  # thư mục dữ liệu (không bị gỡ) được phép nhắc tới

# Thư mục dữ liệu / hạ tầng của hệ thống hoặc công cụ khác — không bao giờ là "thư mục chương trình".
DENY_NAMES = {
    "applications", "icons", "pixmaps", "fonts", "themes", "sounds", "backgrounds", "mime", "Trash",
    "flatpak", "snap", "pipx", "uv", "pnpm", "node_modules", "containers", "keyrings", "gnome-shell",
    "nautilus", "gvfs-metadata", "dbus-1", "evolution", "tracker", "tracker3", "kio", "kservices5",
    "pki", "icc", "ibus-table", "yelp", "recently-used.xbel", "user-session-migration", "systemd",
    "gnome-settings-daemon", "desktop-directories", "xorg", "zeitgeist", "webkitgtk", "tt-desktoptool", "tt-ubuntutools",
    "virtualenv", "virtualenvs", "venv", "venvs", "envs", "pyenv", "nvm", "rustup", "cargo", "go", "gem",
    "Steam", "JetBrains", "AppImages", "applications-merged", "wine", "lutris", "bottles", "Google",
    "Mozilla", "mozilla", "conda", "mamba", "micromamba",
}
DENY_PREFIXES = ("python", "gnome-", "org.gnome.", "org.freedesktop.", ".", "anaconda", "miniconda", "miniforge",
                 "mambaforge")
# Đường dẫn do công cụ khác quản lý (đã có nguồn riêng hoặc tự gỡ bằng công cụ đó).
SKIP_PREFIXES = (".local/share/flatpak", ".local/share/pipx", ".local/share/uv", ".local/share/pnpm",
                 ".local/share/JetBrains", ".local/lib/node_modules", "snap", ".cargo", ".rustup", ".nvm",
                 ".pyenv", ".volta", ".local/share/Steam", ".steam", ".var/app", ".wine", ".npm-global",
                 "go/bin", ".deno", ".bun")
VCS_MARKERS = (".git", ".hg", ".svn", ".bzr")
PROJECT_MARKERS = VCS_MARKERS + ("package.json", "pyproject.toml", "setup.py", "setup.cfg", "Cargo.toml", "go.mod",
                                 "Makefile", "makefile", "GNUmakefile", "CMakeLists.txt", "pom.xml", "build.gradle",
                                 "build.gradle.kts", "settings.gradle", "meson.build", "configure.ac", "Dockerfile",
                                 "project.godot", "Pipfile", "environment.yml", "composer.json", "Gemfile",
                                 ".idea", ".vscode", "node_modules", "requirements.txt")
PROJECT_GLOBS = ("*.lpi", "*.lpr", "*.pro", "*.sln", "*.csproj", "*.ipynb", "*.xcodeproj", "*.code-workspace")
# Mã nguồn nằm ở cấp trên cùng: bản cài thật hầu như không bao giờ có.
SOURCE_EXT = (".c", ".cc", ".cpp", ".h", ".hpp", ".pas", ".py", ".rs", ".go", ".java", ".kt", ".cs", ".gd", ".js",
              ".mjs", ".ts", ".rb", ".pl", ".php", ".lua", ".ipynb", ".r", ".jl", ".swift", ".m", ".vb")
WALK_DEPTH = 5
WALK_BUDGET = 50_000
SCRIPT_MAX = 16 * 1024
_SHELLS = ("sh", "bash", "dash", "zsh", "python", "python3", "env")
_FIELD_CODE = re.compile(r"%[a-zA-Z%]")
_BARE_VALUE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.+-]*")
_ASSIGN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=(.*)", re.S)


@dataclass
class HomeApp:
    key: str
    state: str                    # ok | standalone | broken | unmanaged | ambiguous
    target: str = ""              # chương trình mà lối tắt chạy
    root: str = ""                # thư mục / file chương trình sẽ bị gỡ
    launchers: list[str] = field(default_factory=list)   # script / liên kết / file đi kèm trong ~/.local/bin
    entries: list[DesktopEntry] = field(default_factory=list)
    icons: list[str] = field(default_factory=list)
    reason: str = ""
    folder_links: list[str] = field(default_factory=list)  # ~/.local/bin/x -> <root>/... (chỉ gỡ cùng thư mục)

    @property
    def desktop_files(self) -> list[str]:
        return sorted({e.path for e in self.entries})

    def trash_paths(self) -> list[str]:
        """Mọi thứ sẽ chuyển vào Thùng rác (thư mục chương trình đầu tiên; bỏ mục nằm bên trong mục khác)."""
        paths = ([self.root] if self.root else []) + self.launchers + self.desktop_files + self.icons
        out: list[str] = []
        for p in dict.fromkeys(paths):
            if not any(p != q and p.startswith(q.rstrip("/") + "/") for q in paths):
                out.append(p)
        return out


# ---------------------------------------------------------------- thư mục
def real_home(home: str = HOME) -> str:
    return os.path.realpath(home)


def _rel(path: str, home: str) -> str | None:
    rel = os.path.relpath(path, home)
    return None if rel == "." or rel.startswith("..") else rel


def _under(path: str, root: str) -> bool:
    return path == root or path.startswith(root.rstrip("/") + "/")


def _keep_name(path: str) -> str:
    """Đưa thư mục cha về đường dẫn thật nhưng GIỮ tên file (không đi theo liên kết cuối, vd. venv python)."""
    return os.path.join(os.path.realpath(os.path.dirname(path)), os.path.basename(path))


def desktop_dirs(home: str = HOME) -> list[str]:
    """Thư mục Desktop (kể cả tên đã dịch, vd. 'Màn hình nền', đọc từ ~/.config/user-dirs.dirs)."""
    dirs = ["Desktop"]
    try:
        with open(os.path.join(home, ".config/user-dirs.dirs"), encoding="utf-8", errors="replace") as fh:
            for line in fh:
                m = re.match(r'\s*XDG_DESKTOP_DIR\s*=\s*"(.*)"\s*$', line)
                if m:
                    val = m.group(1).replace("$HOME", home)
                    rel = _rel(os.path.normpath(val), home)
                    if rel and "/" not in rel and rel not in dirs and not rel.startswith("."):
                        dirs.append(rel)
    except OSError:
        pass
    return dirs


def shortcut_dirs(home: str = HOME) -> list[str]:
    return list(APP_DIRS) + desktop_dirs(home)


def bin_dirs(home: str = HOME) -> list[str]:
    """~/.local/bin, ~/bin — bỏ qua nếu là liên kết tượng trưng (vd. ~/bin -> ~/MyCode/tools)."""
    out = []
    for b in BIN_DIRS:
        d = os.path.join(home, b)
        if os.path.isdir(d) and not os.path.islink(d) and os.path.realpath(d) == d:
            out.append(d)
    return out


def in_bin_dir(path: str, home: str = HOME) -> bool:
    return os.path.dirname(path) in bin_dirs(home)


def _skipped(path: str, home: str) -> bool:
    rel = _rel(path, home)
    return rel is None or any(rel == p or rel.startswith(p + "/") for p in SKIP_PREFIXES)


GENERIC_NAMES = {"bin", "sbin", "lib", "lib32", "lib64", "libexec", "share", "include", "local", "usr", "etc", "var",
                 "tools", "tool", "scripts", "script", "utils", "util", "apps", "app", "programs", "software",
                 "games", "ides", "dev", "devel", "projects", "project", "src", "code", "work", "workspace",
                 "downloads", "appimages", "portable", "misc", "other", "test", "tmp", "temp", "backup", "backups"}


def _denied(name: str) -> bool:
    """Tên không bao giờ là MỘT chương trình (thư mục hạ tầng, tên chung kiểu ~/opt/bin, ~/Programs/tools...)."""
    return name in DENY_NAMES or name.startswith(DENY_PREFIXES) or name.lower() in GENERIC_NAMES


def is_elf(path: str) -> bool:
    try:
        with open(path, "rb") as fh:
            return fh.read(4) == b"\x7fELF"
    except OSError:
        return False


def is_appimage_file(path: str) -> bool:
    from .appimage_collector import is_appimage
    return os.path.isfile(path) and is_appimage(path)


def app_root(path: str, home: str = HOME) -> str | None:
    """Mục con trực tiếp của một INSTALL_BASES chứa `path` (hoặc ~/.local/<x>.app), None nếu không có.
    Chỉ là ỨNG VIÊN — find_home_apps còn kiểm tra vị trí file chạy, bằng chứng cài đặt, dự án..."""
    rel = _rel(path, home)
    if rel is None:
        return None
    parts = rel.split("/")
    if len(parts) >= 3 and parts[0] == ".local" and parts[1].endswith(".app") and not _denied(parts[1][:-4]):
        return os.path.join(home, ".local", parts[1])  # kitty.app, zed.app
    for base in sorted(INSTALL_BASES, key=lambda b: -b.count("/")):
        bparts = base.split("/")
        if parts[:len(bparts)] != bparts or len(parts) <= len(bparts):
            continue
        name = parts[len(bparts)]
        if _denied(name):
            return None
        root = os.path.join(home, base, name)
        if len(parts) == len(bparts) + 1:
            # File nằm thẳng trong thư mục cài: AppImage, hoặc chương trình ELF một file trong ~/Applications,
            # ~/Programs (vd. Godot_v4.2.x86_64). Không bao giờ là liên kết.
            if os.path.islink(root) or not os.path.isfile(root):
                return None
            if is_appimage_file(root) or (base in ("Applications", "Programs", "programs") and is_elf(root)):
                return root
            return None
        return root
    return None


def is_install_root(path: str, home: str = HOME) -> bool:
    """path có đúng là một "thư mục / file chương trình" theo app_root không (dùng để kiểm tra lại trước khi gỡ)."""
    if os.path.isdir(path) and not os.path.islink(path):
        return app_root(os.path.join(path, "_"), home) == path
    return os.path.isfile(path) and app_root(path, home) == path


def layout_ok(exe: str, root: str) -> bool:
    """File chạy nằm đúng vị trí của một bản cài trong root."""
    if exe == root:
        return is_appimage_file(root) or is_elf(root)
    if not exe.startswith(root + "/"):
        return False
    parts = exe[len(root) + 1:].split("/")
    return (len(parts) == 1 or (len(parts) == 2 and parts[0] == "bin")
            or (len(parts) == 3 and parts[0] in (".venv", "venv") and parts[1] == "bin"
                and parts[2].startswith("python")))


def _has_elf(root: str, depth: int = 3, budget: int = 5000) -> bool:
    stack, seen = [(root, 0)], 0
    while stack:
        cur, d = stack.pop()
        try:
            with os.scandir(cur) as it:
                for e in it:
                    seen += 1
                    if seen > budget:
                        return False
                    if e.is_file(follow_symlinks=False) and is_elf(e.path):
                        return True
                    if d + 1 < depth and e.is_dir(follow_symlinks=False):
                        stack.append((e.path, d + 1))
        except OSError:
            continue
    return False


def install_evidence(exe: str, root: str) -> bool:
    """Bằng chứng đây là bản cài (không phải script / dự án tự viết)."""
    if is_elf(exe) or is_appimage_file(exe):
        return True
    parts = exe[len(root) + 1:].split("/") if exe.startswith(root + "/") else []
    if len(parts) == 3 and parts[0] in (".venv", "venv") and os.path.islink(exe):
        return True  # python của môi trường ảo riêng (kiểu GomGo)
    # Script khởi chạy của nhà phát hành (vd. android-studio/bin/studio.sh): chỉ khi nằm trong bin/ và thư mục có
    # chương trình đã biên dịch. Script / mã nguồn nằm thẳng trong thư mục (bot.py, run.sh) KHÔNG tính.
    return len(parts) == 2 and parts[0] == "bin" and os.path.isdir(root) and _has_elf(root)


def looks_like_project(root: str, target: str = "", max_depth: int = WALK_DEPTH,
                       max_entries: int = WALK_BUDGET) -> str:
    """Lý do nếu thư mục có thể là dự án mã nguồn / dữ liệu của người dùng ('' nếu sạch). FAIL-CLOSED: dò không
    hết (quá nhiều file, không đọc được) cũng trả về lý do."""
    if not os.path.isdir(root) or os.path.islink(root):
        return ""
    dirs = [root]
    if target and target.startswith(root + "/"):
        d = os.path.dirname(target)
        while d.startswith(root + "/"):
            dirs.append(d)
            d = os.path.dirname(d)
    venv_app = bool(target) and len(target[len(root) + 1:].split("/")) == 3 and \
        target[len(root) + 1:].split("/")[0] in (".venv", "venv")
    for d in dirs:
        for m in PROJECT_MARKERS:
            if venv_app and m == "requirements.txt":
                continue  # ứng dụng Python triển khai kèm venv riêng (kiểu GomGo) thường có requirements.txt
            if os.path.lexists(os.path.join(d, m)):
                return f"có {os.path.join(d, m)}"
        for g in PROJECT_GLOBS:
            hit = glob.glob(os.path.join(glob.escape(d), g))
            if hit:
                return f"có {hit[0]}"
        if not venv_app:
            try:
                names = os.listdir(d)
            except OSError:
                return f"không đọc được {d}"
            src = [n for n in names if n.lower().endswith(SOURCE_EXT) and os.path.isfile(os.path.join(d, n))]
            if src:
                return f"có mã nguồn ({os.path.join(d, src[0])})"
    seen = 0
    queue = [(root, 0)]  # duyệt theo chiều rộng: thư mục lớn không che được .git ở cấp nông
    while queue:
        cur, depth = queue.pop(0)
        try:
            with os.scandir(cur) as it:
                entries = list(it)
        except PermissionError:
            return f"không đọc được {cur}"
        except OSError:
            continue
        for e in entries:
            seen += 1
            if seen > max_entries:
                return "quá nhiều file để kiểm tra chắc chắn"
            if e.name in VCS_MARKERS:
                return f"có {e.path}"
            if depth + 1 < max_depth and e.is_dir(follow_symlinks=False):
                queue.append((e.path, depth + 1))
    return ""


# ---------------------------------------------------------------- phân tích lối tắt / script
def exec_parts(entry: DesktopEntry) -> list[str]:
    try:
        return shlex.split(entry.exec)
    except ValueError:
        return []


def _expand(p: str, home: str) -> str:
    for pre in ("~/", "$HOME/", "${HOME}/", "\"$HOME\"/"):
        if p.startswith(pre):
            return os.path.join(home, p[len(pre):])
    if p in ("~", "$HOME", "${HOME}"):
        return home
    return p


@dataclass
class ExecInfo:
    target: str = ""              # '' = lệnh hệ thống / không rõ
    args: list[str] = field(default_factory=list)
    via: str = ""                 # trình thông dịch đã bỏ ra (sh, python3...) nếu có
    env: list[str] = field(default_factory=list)    # giá trị các biến môi trường gán trong Exec=
    bad_env: bool = False


def resolve_exec(entry: DesktopEntry, home: str = HOME) -> ExecInfo:
    """Chương trình trong Exec=, tham số, trình thông dịch và biến môi trường.
    Hiểu cả 'env A=b x', 'sh /đường/dẫn/script', 'python3 /đường/dẫn/x.py'."""
    info = ExecInfo()
    parts = exec_parts(entry)
    while parts:
        if parts[0] in ("env", "/usr/bin/env", "/bin/env"):
            parts = parts[1:]
            if parts and parts[0].startswith("-"):
                info.bad_env = True  # env -i / -C / -S ...: không hiểu chắc chắn
                return info
            continue
        m = _ASSIGN.fullmatch(parts[0])
        if m and not parts[0].startswith("/"):
            info.env.append(m.group(1))
            parts = parts[1:]
            continue
        break
    if not parts:
        return info
    first, rest = parts[0], parts[1:]
    if os.path.basename(first) in _SHELLS and rest and not rest[0].startswith("-"):
        info.via = os.path.basename(first)
        first, rest = rest[0], rest[1:]
    info.args = rest
    first = _expand(first, home)
    if not first.startswith("/"):
        for d in bin_dirs(home):  # lệnh không có đường dẫn: chỉ quan tâm nếu nó nằm trong ~/.local/bin, ~/bin
            cand = os.path.join(d, first)
            if os.path.lexists(cand):
                info.target = cand
                return info
        return info
    info.target = os.path.normpath(first)
    return info


def check_args(args: list[str], home: str, allow_shell_args: bool = False) -> tuple[bool, list[str]]:
    """Tham số của lệnh có nằm trong mẫu an toàn không. Trả về (ok, các đường dẫn tuyệt đối được nhắc tới).
    Cho phép: mã %U/%F..., '--', tuỳ chọn (-x, --x=giá_trị), giá trị đơn giản sau tuỳ chọn (-m pkg, --type x),
    đường dẫn tuyệt đối (kiểm tra sau); trong script thêm "$@" / "$*".
    Không cho phép: tham số vị trí là chữ / đường dẫn tương đối (index.js, MyCode/x.jar), đường dẫn Windows,
    biến ($APP/x)..."""
    refs: list[str] = []
    prev_opt = False
    for i, a in enumerate(args):
        if i == 0 and re.fullmatch(r"\+[a-z][a-z0-9-]*", a):
            continue  # lệnh con kiểu kitty (+open, +kitten): không trỏ tới đường dẫn nào
        if _FIELD_CODE.fullmatch(a) or a == "--":
            prev_opt = False
            continue
        if allow_shell_args and a in ("$@", "$*"):
            prev_opt = False
            continue
        if a.startswith("-"):
            if "=" in a:
                val = _expand(a.split("=", 1)[1], home)
                if "$" in val or "\\" in val:
                    return False, refs
                if val.startswith("/"):
                    refs.append(os.path.normpath(val))
                elif "/" in val:
                    return False, refs
                prev_opt = False
            elif "$" in a:
                return False, refs
            else:
                prev_opt = True
            continue
        val = _expand(a, home)
        if "$" in val or "\\" in val or "`" in val:
            return False, refs
        if val.startswith("/"):
            refs.append(os.path.normpath(val))
        elif not (prev_opt and _BARE_VALUE.fullmatch(val)
                  and not re.search(r"\.(jar|js|mjs|py|sh|rb|pl|php|lua|exe|bat|html?|json)$", val, re.I)):
            return False, refs  # tham số vị trí / tương đối: lối tắt chạy THỨ KHÁC bằng chương trình này
        prev_opt = False
    return True, refs


_FORBIDDEN_TOKENS = {"if", "then", "else", "elif", "fi", "case", "esac", "for", "while", "until", "do", "done",
                     "function", "{", "}", "(", ")", "&&", ";", "|", "&", "source", ".", "eval", "trap", "read"}


@dataclass
class Launcher:
    ok: bool
    reason: str = ""
    command: str = ""             # chương trình được chạy (đường dẫn tuyệt đối)
    args: list[str] = field(default_factory=list)
    refs: list[str] = field(default_factory=list)   # mọi đường dẫn khác được nhắc tới (cd, export, tham số)


def parse_launcher(path: str, home: str = HOME) -> Launcher:
    """Hiểu script khởi chạy CHỈ KHI nó thuộc dạng đơn giản: shebang sh/bash, các dòng 'set -e', 'cd <thư mục>'
    (có thể kèm '|| exit N'), 'export TÊN=giá_trị', và ĐÚNG MỘT lệnh '[exec] [TÊN=giá_trị ...] /đường/dẫn [tham số]'.
    Mọi thứ khác (biến, nhiều lệnh, &, |, if, case, hàm, source...) -> không hiểu được -> không tự gỡ."""
    try:
        with open(path, "rb") as fh:
            data = fh.read(SCRIPT_MAX + 1)
    except OSError as exc:
        return Launcher(False, f"không đọc được script ({exc.strerror})")
    if len(data) > SCRIPT_MAX or b"\0" in data:
        return Launcher(False, "script quá lớn")
    text = data.decode("utf-8", "replace").replace("\r\n", "\n")
    if "\\\n" in text:
        return Launcher(False, "script có dòng nối (\\) — không hiểu chắc chắn")
    lines = text.splitlines()
    if not lines or not re.fullmatch(r"#!\s*(/usr)?/bin/(env\s+)?(ba|da)?sh\s*", lines[0]):
        return Launcher(False, "không phải script shell đơn giản")
    res = Launcher(True)
    for raw in lines[1:]:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        line = re.split(r"\s#", line, maxsplit=1)[0]
        if any(c in line for c in ("`", "$(", ">", "<", ";", "|&")) or re.search(r"(?<!\|)\|(?!\|)", line) \
                or re.search(r"&(?!&)", line.replace("&&", "")):
            return Launcher(False, "script có lệnh phức tạp (chuyển hướng, ống, chạy nền...)")
        try:
            toks = shlex.split(line)
        except ValueError:
            return Launcher(False, "script có cú pháp lạ")
        if not toks:
            continue
        if toks[0] == "set" and all(t[:1] in "-+" for t in toks[1:]):
            continue
        if toks[0] == "cd":
            if len(toks) == 2 or (len(toks) in (4, 5) and toks[2] == "||" and toks[3] == "exit"
                                  and (len(toks) == 4 or toks[4].isdigit())):
                d = _expand(toks[1], home)
                if not d.startswith("/") or "$" in d:
                    return Launcher(False, "script cd tới thư mục không xác định")
                res.refs.append(os.path.normpath(d))
                continue
            return Launcher(False, "script có lệnh cd phức tạp")
        if any(t in _FORBIDDEN_TOKENS for t in toks) or any(t.endswith(";") for t in toks):
            return Launcher(False, "script có nhiều lệnh / cấu trúc điều khiển")
        rest = toks[1:] if toks[0] == "export" else toks
        if toks[0] == "export" or all(_ASSIGN.fullmatch(t) for t in toks):
            for t in rest:
                m = _ASSIGN.fullmatch(t)
                if not m:
                    return Launcher(False, "script có lệnh export lạ")
                val = _expand(m.group(1), home)
                if "$" in val:
                    return Launcher(False, "script dùng biến")
                if val.startswith("/"):
                    res.refs.append(os.path.normpath(val))
            continue
        if res.command:
            return Launcher(False, "script chạy nhiều chương trình")
        if toks[0] in ("exec", "nohup"):
            toks = toks[1:]
        while toks and _ASSIGN.fullmatch(toks[0]):
            val = _expand(_ASSIGN.fullmatch(toks[0]).group(1), home)
            if "$" in val:
                return Launcher(False, "script dùng biến")
            if val.startswith("/"):
                res.refs.append(os.path.normpath(val))
            toks = toks[1:]
        if not toks:
            return Launcher(False, "script có lệnh rỗng")
        cmd = _expand(toks[0], home)
        if not cmd.startswith("/") or "$" in cmd:
            return Launcher(False, "script chạy chương trình không có đường dẫn rõ ràng")
        ok, refs = check_args(toks[1:], home, allow_shell_args=True)
        if not ok:
            return Launcher(False, "script truyền tham số không rõ ràng (file của bạn, đường dẫn tương đối, biến...)")
        res.command = os.path.normpath(cmd)
        res.args = toks[1:]
        res.refs += refs
    if not res.command:
        return Launcher(False, "script không chạy chương trình nào")
    return res


ICON_EXT = (".png", ".svg", ".svgz", ".xpm")


def icon_path_ok(path: str, home: str = HOME) -> bool:
    """File icon của chính ứng dụng (bộ hicolor hoặc thư mục icon/pixmaps phẳng) — dùng chung với planner."""
    rel = _rel(path, home)
    return bool(rel) and path.endswith(ICON_EXT) and not os.path.islink(path) and bool(
        re.fullmatch(r"\.local/share/icons/hicolor/[^/]+/apps/[^/]+", rel)
        or re.fullmatch(r"\.local/share/(icons|pixmaps)/[^/]+", rel))


def find_icons(name: str, home: str = HOME) -> list[str]:
    if not name:
        return []
    if name.startswith("/"):
        return [name] if icon_path_ok(name, home) and os.path.isfile(name) else []
    if "/" in name or not re.fullmatch(r"[\w.+-]+", name):
        return []
    h, n = glob.escape(home), glob.escape(name)
    found = []
    for ext in ("png", "svg", "svgz", "xpm"):
        found += glob.glob(os.path.join(h, ".local/share/icons", "hicolor", "*", "apps", f"{n}.{ext}"))
        found += glob.glob(os.path.join(h, ".local/share/icons", f"{n}.{ext}"))
        found += glob.glob(os.path.join(h, ".local/share/pixmaps", f"{n}.{ext}"))
    return sorted(f for f in set(found) if os.path.isfile(f) and icon_path_ok(f, home))


# ---------------------------------------------------------------- phát hiện
def _extra_user_entries(home: str, known: list[DesktopEntry], depth: int = 4,
                        budget: int = 5000) -> list[DesktopEntry] | None:
    """Lối tắt người dùng KHÔNG được xét để gỡ (file .desktop là liên kết, thư mục con của
    ~/.local/share/applications như wine/Programs/...) — chỉ để biết icon nào còn được dùng.
    None nếu không duyệt hết (khi đó không xoá icon nào)."""
    seen = {e.path for e in known}
    files = []
    for d in shortcut_dirs(home):
        files += glob.glob(os.path.join(glob.escape(home), d, "*.desktop"))
    base = os.path.join(home, ".local/share/applications")
    count = 0
    if os.path.isdir(base) and not os.path.islink(base):
        for cur, dirs, names in os.walk(base, followlinks=False):
            if cur.count("/") - base.count("/") >= depth:
                dirs[:] = []
            count += len(names) + len(dirs)
            if count > budget:
                return None
            if cur != base:
                files += [os.path.join(cur, n) for n in names if n.endswith(".desktop")]
    out = []
    for f in files:
        if f in seen or not os.path.isfile(f):
            continue
        e = parse_desktop_file(f)
        if e:
            out.append(e)
    return out


def user_desktop_entries(home: str = HOME) -> list[DesktopEntry]:
    out = []
    for d in shortcut_dirs(home):
        for f in sorted(glob.glob(os.path.join(glob.escape(home), d, "*.desktop"))):
            if os.path.islink(f) or not os.path.isfile(f):
                continue
            e = parse_desktop_file(f)
            if e and e.exec:
                out.append(e)
    return out


def _fstab_targets() -> set[str]:
    out = set()
    try:
        with open("/etc/fstab", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                parts = line.split()
                if len(parts) >= 2 and not parts[0].startswith("#"):
                    out.add(os.path.normpath(parts[1].replace("\\040", " ")))
    except OSError:
        pass
    return out


def maybe_unmounted(path: str, home: str) -> str:
    """Thư mục cha gần nhất đang tồn tại của một đường dẫn không còn, nếu nó có vẻ là chỗ gắn ổ đĩa chưa gắn
    (có trong /etc/fstab, hoặc là thư mục rỗng trong thư mục nhà) — khi đó KHÔNG coi lối tắt là hỏng."""
    fstab = _fstab_targets()
    d = os.path.dirname(path)
    while d and d != home and not os.path.lexists(d):
        if d in fstab:
            return d
        d = os.path.dirname(d)
    if not d or d == home or _rel(d, home) is None:
        return ""
    if os.path.ismount(d):
        return ""  # ổ đang gắn mà chương trình không còn: lối tắt hỏng thật
    if d in fstab:
        return d
    # Thư mục rỗng ngay trong thư mục nhà (vd. ~/HDD) — nhưng không phải ~/Programs, ~/opt... đã trống vì
    # người dùng tự xoá chương trình cuối cùng.
    rel = _rel(d, home) or ""
    common = {b.split("/")[0] for b in INSTALL_BASES} | {"AppImages", "Downloads", "Tải về", "Games", "Desktop",
                                                         "Documents", "bin", ".local"}
    if "/" not in rel and rel not in common:
        try:
            if os.path.isdir(d) and not os.listdir(d):
                return d
        except OSError:
            return d
    return ""


def _unmanaged_reason(where: str, why: str = "") -> str:
    why = f" ({why})" if why else ""
    return (f"Lối tắt dùng tới {where}{why} — công cụ không chắc đây là một chương trình đã cài (có thể là mã "
            "nguồn, dữ liệu hay runtime dùng chung) nên không tự gỡ. Nếu muốn, hãy tự xoá.")


_RUNTIMES = re.compile(r"java|node|nodejs|electron[0-9]*|python[0-9.]*|pypy[0-9.]*|dotnet|mono|ruby|perl|php|"
                       r"deno|bun|wine[0-9]*|wine64|lua[0-9.]*|Rscript|julia|godot.*|love|qemu.*", re.I)


def is_runtime(exe: str) -> bool:
    return bool(_RUNTIMES.fullmatch(os.path.basename(exe)))


def _refs_ok(refs: list[str], root: str, home: str, exe: str) -> str:
    """'' nếu mọi đường dẫn được nhắc tới đều an toàn; ngược lại trả về đường dẫn vi phạm. An toàn là:
    * ngoài thư mục nhà, hoặc chính thư mục nhà (không bao giờ bị gỡ);
    * chính thư mục chương trình (vd. 'cd <root>' trong script khởi chạy);
    * THƯ MỤC dữ liệu (~/.local/share/x, ~/.config/x, ~/.cache/x — có thể chưa tồn tại), chỉ khi chương trình
      không phải runtime (java, node, python...). Với runtime, thư mục đó có thể chính là chương trình của bạn.
    Mọi FILE hay đường dẫn khác bên trong thư mục chương trình (vd. --path ~/Programs/Godot/MyGame, roms/ff7.bin)
    đều bị coi là dữ liệu của bạn -> khoá."""
    for r in refs:
        rr = _keep_name(r) if os.path.lexists(r) else os.path.normpath(r)
        rel = _rel(rr, home)
        if rel is None:
            continue
        if root and rr == root:
            continue
        if not is_runtime(exe) and not os.path.isfile(rr) and not os.path.islink(rr) \
                and any(rel.startswith(d + "/") for d in DATA_DIRS) and not (root and _under(rr, root)):
            continue
        return rr
    return ""


def find_home_apps(home: str = HOME, entries: list[DesktopEntry] | None = None,
                   other_icons: set[str] | None = None, known_appimages: set[str] | None = None,
                   generated=None, owned: set[str] | None = None,
                   system_desktop_names: set[str] | None = None) -> list[HomeApp]:
    """entries: lối tắt người dùng (mặc định đọc từ ~/.local/share/applications, Desktop, autostart).
    other_icons: icon mà lối tắt hệ thống/snap/flatpak dùng (không xoá). known_appimages: AppImage đã có ở nguồn
    AppImage. generated(path): script do pip/npm sinh ra. owned: file do pip quản lý (RECORD).
    system_desktop_names: tên file .desktop của hệ thống/snap/flatpak (lối tắt người dùng cùng tên = bản tuỳ chỉnh)."""
    home = real_home(home)
    entries = user_desktop_entries(home) if entries is None else entries
    other_icons = set(other_icons or ())
    known_appimages = set(known_appimages or ())
    owned = set(owned or ())
    sys_names = set(system_desktop_names or ())
    generated = generated or (lambda p: False)
    apps: dict[str, HomeApp] = {}
    root_users: dict[str, set[str]] = {}       # root -> các app (key) có dùng tới nó
    root_targets: dict[str, set[str]] = {}

    def get(key: str, **kw) -> HomeApp:
        if key not in apps:
            apps[key] = HomeApp(key=key, **kw)
        return apps[key]

    def lock(key: str, state: str, target: str, e: DesktopEntry, reason: str, touched=()):
        app = get(key, state=state, target=target)
        app.entries.append(e)
        app.reason = app.reason or reason
        for r in touched:
            if r:
                root_users.setdefault(r, set()).add(key)

    for e in entries:
        if os.path.basename(e.path) in sys_names:
            continue  # bản tuỳ chỉnh của lối tắt apt/snap/flatpak — phần mềm đã có ở nguồn đó
        info = resolve_exec(e, home)
        if info.bad_env:
            continue
        target, args = info.target, info.args
        if not target:
            continue  # lệnh hệ thống
        target = _keep_name(os.path.normpath(target))
        if _rel(os.path.realpath(target) if os.path.lexists(target) else target, home) is None:
            continue  # chương trình ngoài thư mục nhà — đã có ở nguồn khác
        if not os.path.lexists(target):
            mnt = maybe_unmounted(target, home)
            if mnt:
                lock("unmanaged:" + target, "unmanaged", target, e,
                     f"Chương trình {target} không có mặt — có thể vì ổ đĩa / thư mục {mnt} chưa được gắn. Công cụ "
                     "không gỡ lối tắt này.")
                continue
            lock("broken:" + target, "broken", target, e, "")
            continue
        real = os.path.realpath(target)
        if _skipped(real, home) or _skipped(target, home) or real in known_appimages or generated(target) \
                or real in owned or target in owned:
            continue
        refs: list[str] = []
        workdir = os.path.normpath(_expand(e.workdir, home)) if e.workdir else ""
        if workdir:
            refs.append(workdir)
        ok = True
        for val in info.env:  # vd. env CLASSPATH=~/MyCode/app/classes java ...
            v = _expand(val, home)
            if "$" in v or "`" in v:
                ok = False
            for piece in v.split(":"):
                piece = _expand(piece, home)
                if piece.startswith("/"):
                    refs.append(os.path.normpath(piece))
        args_ok, arg_refs = check_args(args, home)
        ok = ok and args_ok
        refs += arg_refs
        launchers: list[str] = []
        exe = real
        if info.via and info.via not in ("sh", "bash", "dash"):
            # python3 ~/x/main.py ...: lối tắt chạy SCRIPT bằng trình thông dịch hệ thống -> script của bạn.
            lock("unmanaged:" + real, "unmanaged", real, e,
                 _unmanaged_reason(real, f"lối tắt chạy file này bằng {info.via}"), [app_root(real, home)])
            continue
        if in_bin_dir(target, home) and not os.path.islink(target) and not is_elf(target) \
                and not is_appimage_file(target):
            # Script khởi chạy trong ~/.local/bin (kiểu GomGo): chỉ chấp nhận dạng đơn giản.
            if args and any(not _FIELD_CODE.fullmatch(a) for a in args):
                ok = False
            ln = parse_launcher(target, home)
            if not ok or not ln.ok:
                lock("unmanaged:" + target, "unmanaged", target, e,
                     f"{target}: {ln.reason or 'lối tắt truyền tham số không rõ ràng'} — công cụ không tự gỡ script "
                     "khởi chạy mà nó không hiểu chắc chắn.")
                continue
            exe = _keep_name(ln.command)
            refs += ln.refs
            launchers = [target]
            if _rel(os.path.realpath(exe), home) is None and _rel(exe, home) is None:
                lock("unmanaged:" + target, "unmanaged", target, e,
                     f"{target} là script tuỳ chỉnh chạy chương trình hệ thống ({exe}) — phần mềm đó đã có ở nguồn "
                     "khác. Công cụ không gỡ script của bạn.")
                continue
        elif not ok:
            lock("unmanaged:" + real, "unmanaged", real, e,
                 _unmanaged_reason(real, "lối tắt chạy kèm file / tham số / biến khác, có thể là chương trình của bạn "
                                         "chạy bằng chương trình này"), [app_root(real, home)])
            continue
        elif in_bin_dir(target, home) or in_bin_dir(real, home):
            if in_bin_dir(real, home) and (is_elf(real) or is_appimage_file(real)):
                if refs and _refs_ok(refs, "", home, real):
                    lock("unmanaged:" + real, "unmanaged", real, e, _unmanaged_reason(_refs_ok(refs, "", home, real)))
                    continue
                app = get("file:" + real, state="standalone", target=real, root=real)
                app.entries.append(e)
                continue
            if os.path.islink(target):
                launchers = [target]  # ~/.local/bin/x -> ~/Programs/x/x
        root = app_root(exe, home)
        # Đường dẫn được nhắc tới mà thuộc thư mục chương trình KHÁC -> thư mục đó đang được lối tắt này dùng.
        for r in refs:
            other = app_root(_keep_name(r) if os.path.lexists(r) else r, home)
            if other and other != root:
                root_users.setdefault(other, set()).add("ref:" + e.path)
        if not root:
            lock("unmanaged:" + exe, "unmanaged", exe, e, _unmanaged_reason(exe))
            continue
        bad = _refs_ok(refs, root, home, exe)
        if bad:
            lock("unmanaged:" + bad, "unmanaged", exe, e, _unmanaged_reason(bad), [root])
            continue
        if not layout_ok(exe, root):
            lock("ambiguous:" + exe, "ambiguous", exe, e,
                 f"File chạy {exe} không nằm ở vị trí của một bản cài thông thường trong {root} (có thể là thư mục "
                 "gom nhiều chương trình, conda, dự án...) — công cụ không tự gỡ.", [root])
            continue
        if not install_evidence(exe, root):
            lock("unmanaged:" + root, "unmanaged", exe, e,
                 f"{root} không có dấu hiệu là một bản cài (file chạy là script / mã nguồn) — có thể là dự án của "
                 "bạn. Công cụ không tự gỡ.", [root])
            continue
        proj = looks_like_project(root, exe)
        if proj:
            lock("unmanaged:" + root, "unmanaged", exe, e,
                 f"{root} {proj} — có thể là dự án mã nguồn / dữ liệu của bạn. Công cụ không tự gỡ.", [root])
            continue
        key = "root:" + root
        app = get(key, state="ok", target=exe, root=root)
        app.launchers = sorted(set(app.launchers) | set(launchers))
        app.entries.append(e)
        root_targets.setdefault(root, set()).add(exe)
        root_users.setdefault(root, set()).add(key)

    for root, targets in root_targets.items():
        app = apps["root:" + root]
        rel = _rel(root, home) or ""
        top = {t for t in targets if os.path.dirname(t) == root}
        names = {os.path.basename(t) for t in targets}
        if len(names) > 1 and top:
            app.state = "ambiguous"
            app.reason = (f"{root} chứa nhiều chương trình khác nhau ("
                          + ", ".join(sorted(names)[:3])
                          + ") — có thể là thư mục gom nhiều chương trình; công cụ không tự gỡ.")
        if rel.startswith(SHALLOW_BASES):
            # ~/.local/share, ~/.local/lib chứa dữ liệu của đủ loại chương trình: thêm điều kiện chặt hơn.
            others_top = []
            try:
                with os.scandir(root) as it:
                    for de in it:
                        if de.is_file(follow_symlinks=False) and os.access(de.path, os.X_OK) \
                                and de.path not in targets:
                            others_top.append(de.name)
            except OSError:
                others_top.append("?")
            if len(targets) > 1 or others_top:
                app.state = "ambiguous"
                app.reason = (f"{root} nằm trong thư mục dữ liệu chung và chứa nhiều chương trình / script khác nhau "
                              "— công cụ không tự gỡ.")
        if app.state == "ok" and os.path.isdir(root):
            try:
                with os.scandir(root) as it:
                    imgs = [de.name for de in it if de.is_file(follow_symlinks=False) and de.path not in targets
                            and is_appimage_file(de.path)]
            except OSError:
                imgs = ["?"]
            if imgs:
                app.state = "ambiguous"
                app.reason = (f"{root} chứa cả chương trình khác ({', '.join(imgs[:3])}) — công cụ không tự gỡ.")
        if app.state == "ok" and any(os.path.realpath(os.path.dirname(x.path)) != os.path.dirname(x.path)
                                     for x in app.entries):
            app.state = "ambiguous"
            app.reason = "Lối tắt nằm trong thư mục là liên kết tượng trưng — công cụ không tự gỡ để đảm bảo an toàn."
        users = root_users.get(root, set()) - {"root:" + root}
        if users and app.state == "ok":
            names = set()
            for k in users:
                if k.startswith("ref:"):
                    names.add(os.path.basename(k[4:]))
                else:
                    names |= {os.path.basename(x.path) for x in apps[k].entries}
            app.state = "ambiguous"
            app.reason = (f"Thư mục {root} còn được lối tắt khác dùng (" + ", ".join(sorted(names))
                          + ") — gỡ nó sẽ làm hỏng lối tắt đó.")

    # Chương trình đứng riêng trong ~/.local/bin, ~/bin (vd. uv + uvx, pnpm) và liên kết vào app đã biết.
    root_apps = {a.root: a for a in apps.values() if a.state == "ok" and a.root}
    claimed = {p for a in apps.values() for p in a.launchers + [a.root, a.target] if p}
    standalone: dict[str, list[tuple[str, int]]] = {}
    for d in bin_dirs(home):
        try:
            names = sorted(os.listdir(d))
        except OSError:
            continue
        for n in names:
            p = os.path.join(d, n)
            if p in claimed or p in owned:
                continue
            if os.path.islink(p):
                real = os.path.realpath(p)
                if _rel(real, home) is None or _skipped(real, home):
                    continue
                if not os.path.exists(real):
                    if maybe_unmounted(real, home):
                        continue  # ổ đĩa chưa gắn: không đụng tới
                    app = get("broken:" + p, state="broken", target=real)
                    app.launchers = [p]  # liên kết hỏng trỏ vào chương trình đã bị xoá
                    continue
                r = app_root(real, home)
                if r and r in root_apps:
                    a = root_apps[r]
                    own = {os.path.realpath(a.target)} | {os.path.realpath(x) for x in a.launchers}
                    if real in own:
                        a.launchers = sorted(set(a.launchers) | {p})
                    else:
                        # lệnh khác đi kèm (kitten, code, jbr/bin/java): chỉ gỡ khi thư mục cũng được gỡ
                        a.folder_links = sorted(set(a.folder_links) | {p})
                continue
            if not os.path.isfile(p) or os.path.realpath(p) in known_appimages or generated(p) or not is_elf(p):
                continue
            try:
                mtime = os.stat(p).st_mtime_ns
            except OSError:
                continue
            standalone.setdefault(d, []).append((p, mtime))
    for d, files in standalone.items():
        # Gộp các file cùng một sản phẩm: cùng thời điểm cài và tên nối tiếp (uv + uvx, rga + rga-preproc).
        files.sort(key=lambda x: (len(os.path.basename(x[0])), x[0]))
        used: set[str] = set()
        for p, mt in files:
            if p in used:
                continue
            base = os.path.basename(p)

            def sibling(q: str) -> bool:
                rest = os.path.basename(q)[len(base):]
                return os.path.basename(q).startswith(base) and (rest == "x" or rest[:1] in ("-", "_"))

            group = [q for q, m in files if q not in used and q != p and m == mt and len(base) >= 2 and sibling(q)]
            used |= {p, *group}
            app = get("file:" + p, state="standalone", target=p, root=p)
            app.launchers = sorted(group)

    # Icon: chỉ xoá FILE icon không còn lối tắt nào khác dùng (so theo file thật, không theo chuỗi Icon=).
    def icon_files(icon: str) -> set[str]:
        if not icon:
            return set()
        if icon.startswith("/"):
            return {_keep_name(os.path.normpath(icon))}
        base = icon[:-len(os.path.splitext(icon)[1])] if icon.endswith(ICON_EXT) else icon
        return set(find_icons(base, home))

    def norm_name(icon: str) -> str:
        return icon[:-len(os.path.splitext(icon)[1])] if icon.endswith(ICON_EXT) and "/" not in icon else icon

    extra = _extra_user_entries(home, entries)
    every = list(entries) + (extra or [])
    used_by: dict[str, set[str]] = {}
    for e in every:
        for f in icon_files(e.icon):
            used_by.setdefault(f, set()).add(e.path)
    other_names = {norm_name(i) for i in other_icons}
    for app in apps.values():
        if app.state not in ("ok", "standalone", "broken") or extra is None:
            continue
        mine = {e.path for e in app.entries}
        icons = set()
        for e in app.entries:
            if norm_name(e.icon) in other_names:
                continue
            for f in icon_files(e.icon):
                if used_by.get(f, set()) - mine or (app.root and f.startswith(app.root + "/")):
                    continue
                if os.path.isfile(f) and icon_path_ok(f, home):
                    icons.add(f)
        app.icons = sorted(icons)
    return sorted(apps.values(), key=lambda a: a.key)
