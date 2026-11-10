"""Phần mềm cài thủ công: thư mục trong /opt và file trong /usr/local/bin không thuộc gói dpkg nào,
cùng phần mềm tự cài vào thư mục nhà (xem home_apps.py)."""

from __future__ import annotations

import datetime as dt
import os

from ..desktop_index import DesktopIndex, best_title
from ..models import SoftwareItem, Source
from ..utils import dir_size, dpkg_owners, mount_points, mounts_under
from .appimage_collector import find_appimages, is_appimage
from .home_apps import HOME, HomeApp, find_home_apps
from .base import Collector

OPT = "/opt"
LOCAL_BIN = "/usr/local/bin"


def is_generated_entry_point(path: str) -> bool:
    """Script do pip/npm sinh ra (đã được hiển thị ở nguồn pip/npm)."""
    try:
        real = os.path.realpath(path)
        if "/node_modules/" in real:
            return True
        with open(path, "rb") as fh:
            head = fh.read(600)
    except OSError:
        return False
    if not head.startswith(b"#!"):
        return False
    first = head.split(b"\n", 1)[0]
    return b"python" in first and b"sys.argv[0] = re.sub(" in head


class ManualCollector(Collector):
    name = "manual"
    label = "Cài thủ công"

    def collect(self, index: DesktopIndex) -> list[SoftwareItem]:
        from ..removal.pathrules import RUNTIME_DIRS, systemd_units_using

        opt_is_link = os.path.islink(OPT)
        points = mount_points()
        candidates: list[tuple[str, str]] = []  # (path, kind)
        if os.path.isdir(OPT):
            opt_dev = os.stat(OPT).st_dev   # lỗi đọc /opt -> báo lỗi nguồn (không âm thầm trả rỗng)
            names = sorted(os.listdir(OPT))
            for name in names:
                if name.startswith("."):
                    continue
                p = os.path.join(OPT, name)
                if mounts_under(p, points):
                    continue  # điểm gắn ổ đĩa / ổ mạng: kiểm tra TRƯỚC khi stat để không treo vì NFS chết
                try:
                    if os.lstat(p).st_dev != opt_dev:
                        continue
                except OSError:
                    continue
                if os.path.isfile(p) and is_appimage(p):
                    continue  # đã có ở nguồn AppImage
                candidates.append((p, "opt"))
        if os.path.isdir(LOCAL_BIN):
            names = sorted(os.listdir(LOCAL_BIN))
            for name in names:
                p = os.path.join(LOCAL_BIN, name)
                if os.path.isdir(p) or is_generated_entry_point(p) or is_appimage(p):
                    continue
                candidates.append((p, "local-bin"))
        owners = dpkg_owners([p for p, _ in candidates])
        items = []
        for path, kind in candidates:
            if path in owners:
                continue
            try:
                st = os.lstat(path)
            except OSError:
                continue
            entries = index.entries_for_path(path)
            entry = entries[0] if entries else None
            name = os.path.basename(path)
            readable = os.access(path, os.R_OK | (os.X_OK if os.path.isdir(path) else 0))
            reason = ""
            if opt_is_link and kind == "opt":
                reason = "/opt là liên kết tượng trưng — công cụ không xoá trong đó để đảm bảo an toàn."
            elif kind == "opt" and name.lower() in RUNTIME_DIRS:
                reason = (f"{path} là thư mục runtime/dữ liệu của dịch vụ hệ thống (containerd, Docker, CNI...). "
                          "Hãy gỡ dịch vụ tương ứng bằng APT.")
            else:
                units = systemd_units_using(path)
                if units:
                    reason = f"{path} đang được dịch vụ systemd dùng: {', '.join(units[:4])}."
            items.append(SoftwareItem(
                source=Source.MANUAL,
                id=path,
                name=name,
                display_name=entry.title if entry else "",
                size=dir_size(path),
                install_date=dt.datetime.fromtimestamp(st.st_mtime),
                origin=f"Thư mục {os.path.dirname(path)} (không thuộc gói nào)",
                summary=(entry.comment_vi or entry.comment) if entry and (entry.comment_vi or entry.comment)
                else ("Thư mục chương trình" if kind == "opt" else "Chương trình/script"),
                icon=entry.icon if entry else ("folder" if os.path.isdir(path) else "application-x-executable"),
                is_app=bool(entries),
                manual=True,
                path=path,
                protected_reason=reason,
                extra={"kind": kind, "desktop_files": [e.path for e in entries],
                       "readable": readable},
            ))
        items.extend(home_items(index, points))
        return items


def pip_owned_bin_files(home: str = HOME) -> set[str]:
    """File trong ~/.local/bin do `pip install --user` cài (theo RECORD) — đã có ở nguồn pip, không liệt kê lại."""
    import glob
    from importlib import metadata

    owned: set[str] = set()
    bins = {os.path.join(home, ".local/bin"), os.path.realpath(os.path.join(home, ".local/bin"))}
    for site in glob.glob(os.path.join(glob.escape(home), ".local/lib/python3*/site-packages")):
        try:
            dists = list(metadata.distributions(path=[site]))
        except Exception:
            continue
        for dist in dists:
            try:
                files = dist.files or []
            except Exception:
                continue
            for f in files:
                try:
                    loc = os.path.normpath(str(dist.locate_file(f)))
                except Exception:
                    continue
                if os.path.dirname(loc) in bins:
                    owned.add(loc)
                    owned.add(os.path.realpath(loc))
    return owned


def _tilde(path: str) -> str:
    for h in (os.path.realpath(HOME), HOME):
        if path == h or path.startswith(h + "/"):
            return "~" + path[len(h):]
    return path


def home_items(index: DesktopIndex, points=None) -> list[SoftwareItem]:
    """Phần mềm tự cài vào thư mục nhà (GomGo, Telegram tải về, uv...)."""
    other_icons = {e.icon for e in index.system_entries if e.icon}
    other_icons |= {e.icon for es in index.by_snap.values() for e in es if e.icon}
    other_icons |= {e.icon for e in index.by_flatpak.values() if e.icon}
    sys_names = {os.path.basename(e.path) for e in index.system_entries}
    sys_names |= {os.path.basename(e.path) for es in index.by_snap.values() for e in es}
    sys_names |= {os.path.basename(e.path) for e in index.by_flatpak.values()}
    known = {os.path.realpath(p) for p in find_appimages()}
    apps = find_home_apps(other_icons=other_icons, known_appimages=known, generated=is_generated_entry_point,
                          owned=pip_owned_bin_files(), system_desktop_names=sys_names)
    points = mount_points() if points is None else points
    return [_home_item(a, points) for a in apps]


_SUMMARY = {
    "ok": "Chương trình tự cài trong thư mục nhà",
    "standalone": "Chương trình đứng riêng trong thư mục nhà",
    "broken": "Lối tắt hỏng — chương trình không còn trên máy",
    "unmanaged": "Lối tắt tới chương trình trong thư mục riêng của bạn",
    "ambiguous": "Chương trình trong thư mục nhà (không tự gỡ được)",
}


def _home_item(app: HomeApp, points) -> SoftwareItem:
    main = app.root or app.target
    entry = app.entries[0] if app.entries else None
    reason = app.reason
    size = None
    date = None
    if app.state in ("ok", "standalone"):
        if mounts_under(main, points):
            reason = f"{main} có ổ đĩa / ổ mạng gắn bên trong — công cụ không xoá để đảm bảo an toàn."
        elif os.path.islink(main):
            reason = f"{main} là liên kết tượng trưng — công cụ không xoá để đảm bảo an toàn."
        else:
            size = dir_size(main) if os.path.isdir(main) else None
            if size is None and os.path.isfile(main):
                size = os.path.getsize(main)
    # Ngày cài: lúc tạo lối tắt (sớm nhất); nếu không có lối tắt thì lúc file/thư mục được đặt vào máy (ctime) —
    # mtime của file trong gói nén thường là ngày nhà phát hành đóng gói (vd. Android Studio: 2010).
    stamps = []
    for p in app.desktop_files:
        try:
            stamps.append(os.lstat(p).st_mtime)
        except OSError:
            pass
    if not stamps:
        try:
            st = os.lstat(main)
            stamps.append(min(st.st_ctime, st.st_mtime) if app.state == "standalone" else st.st_ctime)
        except OSError:
            pass
    if stamps:
        date = dt.datetime.fromtimestamp(min(stamps))
    comment = (entry.comment_vi or entry.comment) if entry else ""
    summary = _SUMMARY[app.state]
    if app.state == "broken":
        summary += f" ({_tilde(app.target)})"
    elif comment:
        summary = comment
    # Mục "lối tắt hỏng" có thể trùng đích với mục khác: dùng chính lối tắt / liên kết hỏng làm id.
    item_id = main if app.state != "broken" else ((app.launchers or app.desktop_files or [main])[0])
    return SoftwareItem(
        source=Source.MANUAL,
        id=item_id,
        name=os.path.basename(main),
        display_name=best_title(app.entries) if app.entries else "",
        size=size,
        install_date=date,
        origin=f"Thư mục nhà: {_tilde(os.path.dirname(main))}",
        summary=summary,
        icon=(entry.icon if entry and entry.icon else ("folder" if os.path.isdir(main) else "application-x-executable")),
        is_app=any(not e.no_display for e in app.entries),
        manual=True,
        path=main,
        protected_reason=reason,
        extra={"kind": "home", "home_state": app.state, "root": app.root, "target": app.target,
               "trash_paths": app.trash_paths(), "desktop_files": app.desktop_files,
               "launchers": app.launchers, "icons": app.icons, "folder_links": app.folder_links},
    )
