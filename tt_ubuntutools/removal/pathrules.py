"""Luật xoá đường dẫn bằng quyền root — dùng chung cho planner (kiểm tra trước) và helper (kiểm tra lại khi chạy).

Chỉ phụ thuộc thư viện chuẩn để helper (chạy root, python3 -I) import an toàn.
"""

from __future__ import annotations

import glob
import os
import re
import stat
import subprocess

DIR_OK_PARENTS = {"/opt"}  # được xoá cả thư mục con trực tiếp
FILE_ONLY_PARENTS = {"/usr/local/bin", "/usr/share/applications", "/usr/local/share/applications"}
GLOB_CHARS = set("*?[\\")
_ENV = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LC_ALL": "C.UTF-8", "LANG": "C.UTF-8"}


# Thư mục runtime/dữ liệu của dịch vụ thường nằm trong /opt nhưng không phải "phần mềm cài tay".
RUNTIME_DIRS = {"containerd", "cni", "docker", "lxd", "nvidia", "microsoft", "puppetlabs", "aws", "zabbix"}
SYSTEMD_UNIT_DIRS = ["/etc/systemd/system", "/usr/lib/systemd/system", "/lib/systemd/system",
                     "/usr/local/lib/systemd/system", "/run/systemd/system"]


class PathRuleError(Exception):
    pass


def systemd_units_using(path: str, unit_dirs=None) -> list[str]:
    """Các unit systemd có nhắc tới path (dịch vụ đang dùng chương trình này)."""
    found = []
    for d in (SYSTEMD_UNIT_DIRS if unit_dirs is None else unit_dirs):
        for f in glob.glob(os.path.join(d, "*")) + glob.glob(os.path.join(d, "*.d", "*.conf")):
            try:
                if os.path.isdir(f) or os.path.getsize(f) > 200_000:
                    continue
                with open(f, encoding="utf-8", errors="replace") as fh:
                    text = fh.read()
            except OSError:
                continue
            for m in re.finditer(re.escape(path) + r"(?=[/\s\"';:]|$)", text):
                found.append(os.path.basename(f))
                break
    return sorted(set(found))




def dpkg_owner_strict(path: str) -> str | None:
    """Gói dpkg sở hữu path; None nếu chắc chắn không thuộc gói nào. Lỗi khác -> ném PathRuleError (fail-closed)."""
    try:
        res = subprocess.run(["dpkg-query", "-S", "--", path], capture_output=True, text=True,
                             encoding="utf-8", errors="replace", env=_ENV, timeout=60,
                             stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PathRuleError(f"Không kiểm tra được chủ sở hữu của {path}: {exc}")
    if res.returncode == 0 and res.stdout.strip():
        for line in res.stdout.splitlines():
            if ": " in line and not line.startswith("diversion by"):
                return line.split(": ", 1)[0].split(",")[0].strip()
        return "?"
    if res.returncode == 1 and "no path found matching pattern" in res.stderr:
        return None
    raise PathRuleError(f"dpkg-query lỗi khi kiểm tra {path}: {res.stderr.strip() or res.returncode}")


def _mount_points() -> list[str]:
    pts = []
    try:
        with open("/proc/self/mountinfo", encoding="utf-8", errors="surrogateescape") as fh:
            for line in fh:
                parts = line.split()
                if len(parts) > 4:
                    raw, out, i = parts[4], [], 0
                    while i < len(raw):
                        if raw[i] == "\\" and i + 3 < len(raw) and raw[i + 1 : i + 4].isdigit():
                            out.append(chr(int(raw[i + 1 : i + 4], 8)))
                            i += 4
                        else:
                            out.append(raw[i])
                            i += 1
                    pts.append("".join(out))
    except OSError:
        pass
    return pts


def validate_rm_path(path: str, owner_fn=dpkg_owner_strict, mount_points=None, unit_dirs=None) -> str:
    """Trả về đường dẫn chuẩn hoá nếu được phép xoá, ngược lại ném PathRuleError."""
    if not isinstance(path, str) or not path.startswith("/") or "\x00" in path:
        raise PathRuleError("Đường dẫn phải là tuyệt đối.")
    if any(c in GLOB_CHARS for c in path):
        raise PathRuleError("Đường dẫn chứa ký tự đặc biệt (* ? [ \\) — không xử lý.")
    norm = os.path.normpath(path)
    if norm != path or "/../" in path or path.endswith(("/..", "/.")):
        raise PathRuleError("Đường dẫn không được chứa '..', '.' hoặc '/' thừa.")
    parent, name = os.path.dirname(norm), os.path.basename(norm)
    if not name or name in (".", ".."):
        raise PathRuleError("Tên không hợp lệ.")
    if parent in DIR_OK_PARENTS:
        pass
    elif parent in FILE_ONLY_PARENTS:
        if parent.endswith("/applications") and not name.endswith(".desktop"):
            raise PathRuleError("Chỉ được xoá file .desktop trong thư mục này.")
    else:
        raise PathRuleError(f"Không được phép xoá trong {parent}.")
    # Thư mục cha phải là thư mục thật (không qua symlink).
    if os.path.realpath(parent) != parent:
        raise PathRuleError(f"{parent} là liên kết tượng trưng — không xoá trong đó.")
    try:
        st = os.lstat(norm)
        pst = os.lstat(parent)
    except FileNotFoundError:
        raise PathRuleError("Đường dẫn không tồn tại.")
    except OSError as exc:
        raise PathRuleError(f"Không đọc được {norm}: {exc}")
    if parent in FILE_ONLY_PARENTS and stat.S_ISDIR(st.st_mode):
        raise PathRuleError("Chỉ được xoá file trong thư mục này.")
    # Không bao giờ xoá xuyên qua điểm gắn (ổ mạng, ổ dữ liệu, bind mount).
    if st.st_dev != pst.st_dev:
        raise PathRuleError(f"{norm} nằm trên ổ/phân vùng khác — không xoá.")
    pts = _mount_points() if mount_points is None else mount_points
    inside = [m for m in pts if m == norm or m.startswith(norm + "/")]
    if inside:
        raise PathRuleError(f"{norm} chứa điểm gắn ổ đĩa ({inside[0]}) — không xoá.")
    if parent == "/opt" and name.lower() in RUNTIME_DIRS:
        raise PathRuleError(f"{norm} là thư mục runtime/dữ liệu của dịch vụ hệ thống — không xoá.")
    units = systemd_units_using(norm, unit_dirs)
    if units:
        raise PathRuleError(f"{norm} đang được dịch vụ systemd dùng ({', '.join(units[:4])}) — hãy dừng/gỡ dịch vụ trước.")
    owner = owner_fn(norm)
    if owner:
        raise PathRuleError(f"{norm} thuộc gói {owner} — hãy gỡ bằng APT.")
    return norm


def safe_remove(path: str) -> int:
    """Xoá path (đã validate). Thư mục: quét trước, từ chối nếu gặp thiết bị khác; rồi xoá bằng dir_fd
    (không bao giờ đi theo symlink). Trả về số mục đã xoá."""
    parent, name = os.path.dirname(path), os.path.basename(path)
    pfd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        st = os.stat(name, dir_fd=pfd, follow_symlinks=False)
        if not stat.S_ISDIR(st.st_mode):
            os.unlink(name, dir_fd=pfd)
            return 1
        top_dev = st.st_dev
        # Lượt 1: kiểm tra toàn bộ cây nằm cùng thiết bị.
        for _dp, _dn, _fn, dfd in os.fwalk(path, topdown=True, follow_symlinks=False):
            if os.fstat(dfd).st_dev != top_dev:
                raise PathRuleError(f"Phát hiện ổ đĩa khác bên trong {path} — huỷ, chưa xoá gì.")
        # Lượt 2: xoá từ dưới lên.
        count = 0
        for _dp, dirnames, filenames, dfd in os.fwalk(path, topdown=False, follow_symlinks=False):
            if os.fstat(dfd).st_dev != top_dev:
                raise PathRuleError(f"Phát hiện ổ đĩa khác bên trong {path} — dừng.")
            for fn in filenames:
                os.unlink(fn, dir_fd=dfd)
                count += 1
            for dn in dirnames:
                dst = os.stat(dn, dir_fd=dfd, follow_symlinks=False)
                if stat.S_ISLNK(dst.st_mode):
                    os.unlink(dn, dir_fd=dfd)
                else:
                    os.rmdir(dn, dir_fd=dfd)
                count += 1
        os.rmdir(name, dir_fd=pfd)
        return count + 1
    finally:
        os.close(pfd)
