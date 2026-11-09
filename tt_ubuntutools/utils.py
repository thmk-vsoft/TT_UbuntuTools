"""Tiện ích chạy lệnh và truy vấn hệ thống (không phụ thuộc GTK, helper root cũng dùng được)."""

from __future__ import annotations

import http.client
import json
import os
import shutil
import socket
import subprocess
import threading
import time

C_ENV = {**os.environ, "LC_ALL": "C.UTF-8", "LANG": "C.UTF-8"}

# python-apt dùng trạng thái toàn cục — chỉ một luồng mở apt.Cache tại một thời điểm.
APT_LOCK = threading.Lock()

# Biến môi trường có thể khiến pip/npm/python chạy nhầm môi trường (venv, conda...).
UNSAFE_ENV_PREFIXES = ("VIRTUAL_ENV", "CONDA_", "PYTHON", "PIP_", "NODE_", "NPM_CONFIG_", "npm_config_",
                       "PYENV", "LD_PRELOAD", "LD_LIBRARY_PATH")


def clean_env(extra: dict | None = None) -> dict:
    env = {k: v for k, v in os.environ.items() if not k.startswith(UNSAFE_ENV_PREFIXES)}
    env["PATH"] = "/usr/local/bin:/usr/bin:/bin"
    env["LC_ALL"] = env["LANG"] = "C.UTF-8"
    if extra:
        env.update(extra)
    return env


def which(cmd: str) -> str | None:
    return shutil.which(cmd)


def run(cmd: list[str], timeout: float = 60, check: bool = False,
        env: dict | None = None) -> subprocess.CompletedProcess:
    """Chạy lệnh với locale C để kết quả dễ parse; không bao giờ lỗi vì byte không phải UTF-8."""
    return subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=timeout, env=env or C_ENV, check=check, stdin=subprocess.DEVNULL,
    )


def display(s) -> str:
    """Chuỗi an toàn để đưa vào GTK / JSON: thay byte lỗi (surrogateescape) bằng ký tự thay thế."""
    if s is None:
        return ""
    s = str(s)
    try:
        s.encode("utf-8")
        return s
    except UnicodeEncodeError:
        return s.encode("utf-8", "surrogateescape").decode("utf-8", "replace")


def dpkg_owners(paths: list[str]) -> dict[str, str]:
    """Trả về {đường_dẫn: gói} cho các đường dẫn thuộc về một gói dpkg (gọi gộp)."""
    paths = [p for p in paths if not any(c in p for c in "*?[\\")]
    if not paths or not which("dpkg-query"):
        return {}
    owners: dict[str, str] = {}
    for i in range(0, len(paths), 200):
        chunk = paths[i : i + 200]
        try:
            res = run(["dpkg-query", "-S", "--", *chunk], timeout=120)
        except (subprocess.TimeoutExpired, OSError):
            continue
        for line in res.stdout.splitlines():
            if ": " not in line or line.startswith("diversion by"):
                continue
            pkgs, path = line.split(": ", 1)
            path = path.strip()
            pkg = pkgs.split(",")[0].strip().split(":")[0]
            if path in chunk and path not in owners:
                owners[path] = pkg
    return owners


# ---------------------------------------------------------------- mount points

def _unescape_mount(s: str) -> str:
    out, i = [], 0
    while i < len(s):
        if s[i] == "\\" and i + 3 < len(s) and s[i + 1 : i + 4].isdigit():
            out.append(chr(int(s[i + 1 : i + 4], 8)))
            i += 4
        else:
            out.append(s[i])
            i += 1
    return "".join(out)


def mount_points(mountinfo: str = "/proc/self/mountinfo") -> list[str]:
    pts = []
    try:
        with open(mountinfo, encoding="utf-8", errors="surrogateescape") as fh:
            for line in fh:
                parts = line.split()
                if len(parts) > 4:
                    pts.append(_unescape_mount(parts[4]))
    except OSError:
        pass
    return pts


def mounts_under(path: str, points: list[str] | None = None) -> list[str]:
    """Các điểm gắn (mount) bằng path hoặc nằm bên trong path."""
    path = path.rstrip("/") or "/"
    pts = mount_points() if points is None else points
    return [m for m in pts if m == path or m.startswith(path + "/")]


# ---------------------------------------------------------------- dung lượng

def dir_size(path: str, max_entries: int = 200_000, max_seconds: float = 5.0) -> int | None:
    """Tổng dung lượng thư mục, không đi qua mount khác, có giới hạn thời gian/số mục.

    None nếu không truy cập được hoặc vượt giới hạn (hiển thị 'không rõ')."""
    try:
        st = os.lstat(path)
    except OSError:
        return None
    if not os.path.isdir(path) or os.path.islink(path):
        return st.st_size
    root_dev = st.st_dev
    total, count, readable = 0, 0, False
    deadline = time.monotonic() + max_seconds
    stack = [path]
    while stack:
        cur = stack.pop()
        try:
            with os.scandir(cur) as it:
                readable = True
                for e in it:
                    count += 1
                    if count > max_entries or (count % 2000 == 0 and time.monotonic() > deadline):
                        return None
                    try:
                        est = e.stat(follow_symlinks=False)
                    except OSError:
                        continue
                    if e.is_dir(follow_symlinks=False):
                        if est.st_dev == root_dev:
                            stack.append(e.path)
                    else:
                        total += est.st_size
        except OSError:
            pass
    return total if readable else None


# ---------------------------------------------------------------- snapd

class _SnapdConnection(http.client.HTTPConnection):
    def __init__(self, socket_path: str = "/run/snapd.socket", timeout: float = 15):
        super().__init__("localhost", timeout=timeout)
        self._socket_path = socket_path

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(self._socket_path)


def snapd_get(path: str):
    """GET tới REST API của snapd. Trả về trường 'result' hoặc ném lỗi."""
    if not os.path.exists("/run/snapd.socket"):
        raise FileNotFoundError("snapd không chạy")
    conn = _SnapdConnection()
    try:
        conn.request("GET", path)
        data = json.loads(conn.getresponse().read())
    finally:
        conn.close()
    if data.get("type") == "error":
        raise RuntimeError(data.get("result", {}).get("message", "lỗi snapd"))
    return data.get("result")


def snap_content_providers(connections: dict) -> dict[str, set[str]]:
    """{snap cung cấp content: {snap cần nó}} — cả kết nối đã thiết lập lẫn plug khai báo default-provider."""
    out: dict[str, set[str]] = {}
    for c in connections.get("established", []) or []:
        if c.get("interface") != "content":
            continue
        slot = (c.get("slot") or {}).get("snap")
        plug = (c.get("plug") or {}).get("snap")
        if slot and plug and slot != plug:
            out.setdefault(slot, set()).add(plug)
    for p in connections.get("plugs", []) or []:
        if p.get("interface") != "content":
            continue
        provider = ((p.get("attrs") or {}).get("default-provider") or "").split(":")[0]
        consumer = p.get("snap")
        if provider and consumer and provider != consumer:
            out.setdefault(provider, set()).add(consumer)
    return out


def human_users() -> list[str]:
    """Tài khoản người dùng thật trên máy (uid 1000–59999, có shell đăng nhập)."""
    users = []
    try:
        with open("/etc/passwd", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                parts = line.rstrip("\n").split(":")
                if len(parts) < 7:
                    continue
                try:
                    uid = int(parts[2])
                except ValueError:
                    continue
                if 1000 <= uid < 60000 and not parts[6].endswith(("nologin", "false")):
                    users.append(parts[0])
    except OSError:
        pass
    return users
