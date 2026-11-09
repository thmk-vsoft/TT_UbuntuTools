"""Luật bảo vệ: chặn gỡ các gói mà hệ thống / desktop cần để khởi động, đăng nhập và hoạt động.

Được dùng ở cả giao diện (xem trước) và helper root (kiểm tra lại ngay trước khi gỡ),
nên chỉ phụ thuộc thư viện chuẩn (python3-apt là tuỳ chọn).
"""

from __future__ import annotations

import fnmatch
import functools
import glob
import os
import re
import shutil
import subprocess

from .. import PACKAGE_NAME

def _default_python_minor() -> str:
    """'3.14' từ /usr/bin/python3 -> python3.14 (để chỉ bảo vệ đúng Python mặc định của hệ thống)."""
    m = re.search(r"python(3\.\d+)$", os.path.realpath("/usr/bin/python3"))
    return m.group(1) if m else ""


_PY = _default_python_minor()
_PY_PATTERNS = ([f"python{_PY}", f"python{_PY}-minimal", f"libpython{_PY}-minimal", f"libpython{_PY}-stdlib"]
                if _PY else [])

# Gói phát triển / tài liệu không bao giờ cần để hệ thống chạy -> không áp dụng mẫu tên cho chúng.
_NON_RUNTIME_SUFFIXES = ("-dev", "-doc", "-dbg", "-dbgsym", "-examples", "-tests", "-installer")

# (nhóm lý do, danh sách mẫu) — hỗ trợ wildcard kiểu shell. Cố ý viết cụ thể để không chặn nhầm
# gói người dùng (plugin VPN, python3.x-venv, libssl-dev...).
CORE_GROUPS: list[tuple[str, list[str]]] = [
    ("là thành phần lõi của hệ thống", [
        "systemd", "systemd-sysv", "systemd-timesyncd", "systemd-oomd", "systemd-boot*", "systemd-hwe-hwdb",
        "systemd-cryptsetup", "libsystemd*", "init", "init-system-helpers", "udev", "libudev1",
        "dbus", "dbus-bin", "dbus-daemon", "dbus-system-bus-common", "dbus-session-bus-common",
        "dbus-user-session", "dbus-broker", "libdbus-1-3", "polkitd", "pkexec", "policykit-1",
        "libpolkit-*", "apt", "apt-utils", "libapt-pkg*", "dpkg", "debconf", "sudo", "sudo-rs", "login",
        "passwd", "adduser", "libpam0g", "libpam-modules*", "libpam-runtime", "libpam-systemd",
        "libc6", "libc-bin", "coreutils", "rust-coreutils", "coreutils-from-*", "bash", "dash", "base-files",
        "base-passwd", "util-linux", "mount", "procps", "kmod", "linux-base", "ca-certificates", "openssl",
        "libssl3*", "python3", "python3-minimal", "libpython3-stdlib", *_PY_PATTERNS, "python3-gi",
        "python3-apt", "python3-cairo", "gir1.2-glib-2.0", "gir1.2-gtk-4.0", "gir1.2-adw-1", "libgtk-4-1",
        "libadwaita-1-0", "libgirepository-*",
    ]),
    ("là nền tảng snap / bảo mật AppArmor (gỡ sẽ làm hỏng mọi ứng dụng snap, purge xoá toàn bộ dữ liệu snap)", [
        "snapd", "snap-confine", "apparmor", "libapparmor1",
    ]),
    ("cần để khởi động máy (bootloader / initramfs / ổ đĩa)", [
        "grub-common", "grub2-common", "grub-efi*", "grub-pc*", "grub-gfxpayload-lists", "shim", "shim-signed",
        "efibootmgr", "mokutil", "os-prober", "dracut", "dracut-*", "zfs-dracut", "clevis-dracut",
        "clevis-initramfs", "initramfs-tools*", "tiny-initramfs*", "busybox-initramfs", "klibc-utils",
        "libklibc", "cryptsetup*", "libcryptsetup*", "lvm2", "dmsetup", "libdevmapper*", "mdadm",
        "multipath-tools*", "open-iscsi", "zfsutils-linux", "zfs-initramfs", "zfs-zed", "libzfs*",
        "btrfs-progs", "e2fsprogs", "dosfstools", "fdisk", "secureboot-db",
    ]),
    ("là firmware / vi mã cần cho phần cứng", [
        "linux-firmware*", "firmware-*", "intel-microcode", "amd64-microcode", "iucode-tool",
        "wireless-regdb", "alsa-ucm-conf", "alsa-topology-conf",
    ]),
    ("là thành phần mạng", [
        "network-manager", "network-manager-gnome", "plasma-nm", "netplan.io", "libnetplan*",
        "netplan-generator", "wpasupplicant", "systemd-resolved", "systemd-networkd", "iproute2",
        "ifupdown", "isc-dhcp-client",
    ]),
    ("là thành phần desktop / màn hình đăng nhập", [
        "*ubuntu-desktop", "*ubuntu-desktop-minimal", "*ubuntu-desktop-raspi", "ubuntu-mate-desktop",
        "ubuntu-budgie-desktop*", "ubuntu-unity-desktop", "ubuntucinnamon-desktop*", "ubuntukylin-desktop*",
        "ubuntu-minimal", "ubuntu-standard", "ubuntu-server*",
        "ubuntu-session", "ubuntu-system-adjustments", "xubuntu-core", "ubuntu-mate-core",
        "ubuntustudio-desktop*", "edubuntu-desktop*", "kubuntu-desktop*", "kubuntu-settings*",
        "lubuntu-desktop*", "lubuntu-default-settings",
        "gnome-shell", "gnome-shell-common", "gnome-session*", "gdm3", "mutter", "mutter-common*",
        "libmutter*", "gnome-control-center", "gnome-settings-daemon*", "nautilus", "xdg-desktop-portal*",
        "sddm", "sddm-theme-*", "lightdm", "lightdm-*-greeter", "slick-greeter", "unity-greeter",
        "plasma-workspace", "plasma-desktop", "plasma-session*", "kwin-wayland", "kwin-x11", "kwin-common",
        "polkit-kde-agent-1", "xfce4-session", "xfwm4", "xfdesktop4", "xfce4-panel",
        "lxqt-session", "lxqt-policykit", "openbox", "mate-session-manager", "marco", "mate-polkit",
        "cinnamon", "cinnamon-session", "cinnamon-core", "budgie-desktop", "policykit-1-gnome",
        "xfce-polkit", "lxpolkit", "xorg", "xserver-xorg", "xserver-xorg-core*", "xwayland",
        "pipewire", "pipewire-bin", "pipewire-pulse", "pipewire-alsa", "pipewire-audio", "libpipewire*",
        "wireplumber", "pulseaudio",
    ]),
]

# Kernel meta (không có số phiên bản) — gỡ thì máy ngừng nhận bản vá kernel.
KERNEL_META_RE = re.compile(
    r"^linux-(?:(?:image|headers|modules|signed|tools|cloud-tools|image-unsigned)-)?"
    r"(?:generic|virtual|oem|lowlatency|raspi|aws|azure|gcp|gke|gkeop|kvm|ibm|oracle|nvidia|hwe|realtime|"
    r"intel|iotg|starfive|riscv|fips|xilinx|nezha|allwinner|laptop|mtk|apple|bluefield)(?:[.+-][a-z0-9.+-]*)?$"
)
_VERSIONED_KERNEL_RE = re.compile(r"-\d+\.\d+\.\d+-\d+")

# Purge (apt --purge / snap --purge) phần mềm máy chủ có dữ liệu có thể xoá vĩnh viễn dữ liệu.
# Dùng regex neo hai đầu để không bắt nhầm gói client (mysql-client, redis-tools, postgresql-16-pgvector...).
DATA_SERVER_RES = [re.compile(r) for r in [
    r"snapd", r"postgresql(-\d+)?", r"postgresql-common", r"mariadb-server(-\d+(\.\d+)*)?",
    r"mysql-server(-\d+(\.\d+)*)?", r"percona-server-server(-\d+(\.\d+)*)?", r"slapd", r"redis(-server)?",
    r"valkey(-server)?", r"mongodb(-server|-org-server)?", r"docker(\.io|-ce)?", r"containerd(\.io)?", r"podman",
    r"influxdb2?", r"elasticsearch", r"opensearch", r"rabbitmq-server", r"couchdb", r"clickhouse-server",
    r"gitlab-(ce|ee)", r"gitea", r"forgejo", r"jenkins", r"grafana(-enterprise)?", r"etcd(-server)?",
    r"nextcloud(-server)?", r"minio", r"lxd(-installer)?", r"incus", r"microk8s", r"microceph", r"k8s",
    r"charmed-[a-z0-9-]+", r"multipass", r"libvirt-daemon-system", r"virtualbox(-\d+(\.\d+)*)?", r"bind9",
    r"dovecot-core", r"postfix", r"samba", r"nfs-kernel-server", r"mosquitto", r"prometheus", r"loki",
    r"vault", r"consul", r"keycloak", r"zabbix-server-[a-z]+",
]]

# Gói cài file vào các vị trí này là móc khởi động / initramfs: gỡ có thể làm máy không khởi động được.
_BOOT_HOOK_PREFIXES = ("/usr/lib/dracut/modules.d/", "/usr/share/initramfs-tools/hooks/",
                       "/usr/share/initramfs-tools/scripts/", "/etc/initramfs-tools/",
                       "/usr/lib/kernel/install.d/", "/etc/kernel/postinst.d/", "/etc/kernel/install.d/")
_FIRMWARE_PREFIXES = ("/lib/firmware/", "/usr/lib/firmware/")

PROTECTED_SNAP_TYPES = {"base", "os", "core", "snapd", "kernel", "gadget"}
CORE_SNAPS = {"snapd", "snapd-desktop-integration", "prompting-client"}
SEEDED_DESKTOP_SNAPS = {"snap-store", "firmware-updater", "desktop-security-center", "gnome-system-monitor"}

_ENV = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LC_ALL": "C.UTF-8", "LANG": "C.UTF-8"}


def _match(name: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatchcase(name, p) for p in patterns)


def _match_runtime(name: str, patterns: list[str]) -> bool:
    """Như _match nhưng không áp cho gói -dev/-doc/-dbg (trừ khi mẫu ghi đúng tên)."""
    if name.endswith(_NON_RUNTIME_SUFFIXES):
        return name in patterns
    return _match(name, patterns)


def running_kernel_release() -> str:
    return os.uname().release


def is_running_kernel_pkg(name: str, release: str | None = None) -> bool:
    rel = release or running_kernel_release()
    return name.startswith("linux-") and rel in name


def is_kernel_meta(name: str) -> bool:
    if name.endswith(("-tools-common", "-cloud-tools-common", "-doc", "-source")):
        return False
    return bool(KERNEL_META_RE.match(name)) and not _VERSIONED_KERNEL_RE.search(name)


@functools.lru_cache(maxsize=4)
def _critical_file_index(info_dir: str) -> tuple[frozenset, frozenset]:
    """Quét tất cả file .list của dpkg MỘT lần: (gói có firmware, gói có móc khởi động)."""
    fw, hooks = set(), set()
    hook_dirs = {p.rstrip("/") for p in _BOOT_HOOK_PREFIXES}
    try:
        names = os.listdir(info_dir)
    except OSError:
        return frozenset(), frozenset()
    for fn in names:
        if not fn.endswith(".list"):
            continue
        pkg = fn[:-5].split(":")[0]
        try:
            with open(os.path.join(info_dir, fn), encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    if line.startswith(_FIRMWARE_PREFIXES):
                        fw.add(pkg)
                    elif line.startswith(_BOOT_HOOK_PREFIXES) and line.rstrip("\n").rstrip("/") not in hook_dirs:
                        hooks.add(pkg)
        except OSError:
            continue
    return frozenset(fw), frozenset(hooks)


def ships_firmware(name: str, info_dir: str = "/var/lib/dpkg/info") -> bool:
    """Gói có cài file vào /lib/firmware hoặc /usr/lib/firmware không."""
    return name.split(":")[0] in _critical_file_index(info_dir)[0]


def ships_boot_hooks(name: str, info_dir: str = "/var/lib/dpkg/info") -> bool:
    """Gói có cài móc initramfs / dracut / kernel postinst không."""
    return name.split(":")[0] in _critical_file_index(info_dir)[1]


def _owner(path: str) -> str | None:
    try:
        res = subprocess.run(["dpkg-query", "-S", "--", path], capture_output=True, text=True,
                             errors="replace", env=_ENV, timeout=30, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if res.returncode == 0:
        for line in res.stdout.splitlines():
            if ": " in line and not line.startswith("diversion by"):
                return line.split(": ", 1)[0].split(",")[0].split(":")[0].strip()
    return None


def _session_uid() -> int:
    for var in ("PKEXEC_UID", "SUDO_UID"):
        if os.environ.get(var, "").isdigit():
            return int(os.environ[var])
    return os.getuid()


@functools.lru_cache(maxsize=1)
def active_session_packages() -> dict[str, str]:
    """Gói đang phục vụ phiên làm việc hiện tại trên máy NÀY (mọi bản Ubuntu/flavour):
    trình quản lý đăng nhập, các phiên desktop đã cài, polkit agent đang chạy, SSH khi đang kết nối từ xa."""
    found: dict[str, str] = {}

    def add(path: str | None, why: str):
        if path:
            pkg = _owner(os.path.realpath(path)) or _owner(path)
            if pkg:
                found.setdefault(pkg, why)

    try:
        with open("/etc/X11/default-display-manager", encoding="utf-8") as fh:
            add(fh.read().strip(), "trình quản lý đăng nhập (display manager) đang dùng")
    except OSError:
        pass
    for d in ("/usr/share/wayland-sessions", "/usr/share/xsessions"):
        for f in glob.glob(os.path.join(d, "*.desktop")):
            add(f, "cung cấp phiên đăng nhập desktop")
            try:
                with open(f, encoding="utf-8", errors="replace") as fh:
                    for line in fh:
                        if line.startswith("Exec="):
                            words = [w for w in line[5:].strip().split() if "=" not in w and w != "env"]
                            if words:
                                add(shutil.which(words[0]) or words[0], "chương trình khởi động phiên desktop")
                            break
            except OSError:
                pass
    uid = _session_uid()
    for proc in glob.glob("/proc/[0-9]*"):
        try:
            if os.stat(proc).st_uid != uid:
                continue
            exe = os.readlink(os.path.join(proc, "exe"))
        except OSError:
            continue
        exe = exe.removesuffix(" (deleted)")
        base = os.path.basename(exe)
        if "polkit" in base or "authentication-agent" in base or base in ("lxpolkit", "mate-polkit"):
            add(exe, "polkit agent (hộp thoại nhập mật khẩu) đang chạy")
    for pkg, why in remote_session_packages().items():
        found.setdefault(pkg, why)
    return found


def remote_session_packages() -> dict[str, str]:
    """Gói phục vụ phiên điều khiển từ xa ĐANG mở (đọc từ systemd-logind, dùng được cả khi chạy root)."""
    found: dict[str, str] = {}
    services: set[str] = set()
    try:
        res = subprocess.run(["loginctl", "list-sessions", "--no-legend"], capture_output=True, text=True,
                             errors="replace", env=_ENV, timeout=10, stdin=subprocess.DEVNULL)
        for line in res.stdout.splitlines():
            sid = line.split()[0] if line.split() else ""
            if not sid:
                continue
            info = subprocess.run(["loginctl", "show-session", sid, "-p", "Remote", "-p", "Service"],
                                  capture_output=True, text=True, errors="replace", env=_ENV, timeout=10,
                                  stdin=subprocess.DEVNULL).stdout
            props = dict(x.split("=", 1) for x in info.splitlines() if "=" in x)
            if props.get("Remote") == "yes":
                services.add(props.get("Service", ""))
    except (OSError, subprocess.TimeoutExpired):
        pass
    if os.environ.get("SSH_CONNECTION") or os.environ.get("SSH_CLIENT"):
        services.add("sshd")
    # Dịch vụ điều khiển từ xa đang bật (kể cả GNOME Desktop Sharing — phiên không đánh dấu Remote=yes).
    units = {"ssh.service": "sshd", "ssh.socket": "sshd", "sshd.service": "sshd",
             "gnome-remote-desktop.service": "remote-desktop", "xrdp.service": "rdp"}
    try:
        res = subprocess.run(["systemctl", "is-active", *units], capture_output=True, text=True,
                             errors="replace", env=_ENV, timeout=10, stdin=subprocess.DEVNULL)
        for unit, state in zip(units, res.stdout.split()):
            if state.strip() == "active":
                services.add(units[unit])
    except (OSError, subprocess.TimeoutExpired):
        pass
    try:
        res = subprocess.run(["systemctl", "--user", "--machine", f"{_session_uid()}@", "is-active",
                              "gnome-remote-desktop.service"], capture_output=True, text=True, errors="replace",
                             env=_ENV, timeout=10, stdin=subprocess.DEVNULL)
        if res.stdout.strip() == "active":
            services.add("remote-desktop")
    except (OSError, subprocess.TimeoutExpired):
        pass
    for svc in services:
        if "ssh" in svc:
            for pkg in ("openssh-server", "openssh-sftp-server", "ssh"):
                found.setdefault(pkg, "dịch vụ SSH đang bật / có người đăng nhập qua SSH — gỡ có thể khoá bạn khỏi máy")
        if "remote-desktop" in svc or "rdp" in svc or "vnc" in svc:
            for pkg in ("gnome-remote-desktop", "xrdp", "xorgxrdp", "krdp", "freerdp3-shadow-x11"):
                found.setdefault(pkg, "dịch vụ điều khiển từ xa (RDP/VNC) đang bật")
    return found


def clear_caches() -> None:
    """Gọi khi làm mới danh sách / lập kế hoạch mới để phản ánh trạng thái hiện tại."""
    active_session_packages.cache_clear()
    _critical_file_index.cache_clear()


def apt_reason(name: str, essential: bool = False, priority: str = "",
               dynamic: dict[str, str] | None = None) -> str:
    """Lý do không được gỡ một gói apt ('' nếu được phép)."""
    base = name.split(":")[0]
    if base == PACKAGE_NAME:
        return "Đây là chính công cụ TT_UbuntuTools."
    if essential:
        return f"Gói {base} được đánh dấu Essential — bắt buộc cho hệ thống."
    if priority in ("required", "important"):
        return f"Gói {base} có mức ưu tiên '{priority}' — cần thiết cho hệ thống."
    if is_running_kernel_pkg(base):
        return f"Gói {base} thuộc kernel đang chạy."
    if is_kernel_meta(base):
        return f"Gói {base} là gói kernel chính — gỡ đi máy sẽ ngừng nhận bản vá kernel."
    for why, patterns in CORE_GROUPS:
        if _match_runtime(base, patterns):
            return f"Gói {base} {why}."
    if dynamic and base in dynamic:
        return f"Gói {base}: {dynamic[base]}."
    return ""


def purge_reason(name: str) -> str:
    base = name.split(":")[0]
    if any(r.fullmatch(base) for r in DATA_SERVER_RES):
        return (f"Gói {base} là phần mềm máy chủ có dữ liệu — purge có thể xoá vĩnh viễn dữ liệu. "
                "Hãy tắt tuỳ chọn purge (chỉ gỡ chương trình, giữ dữ liệu).")
    return ""


def check_apt_removal(names: list[str], info: dict[str, tuple[bool, str]], *, purge: bool = False,
                      initramfs_providers: list[str] | None = None, firmware_check: bool = True,
                      dynamic: dict[str, str] | None = None) -> list[str]:
    """Kiểm tra TOÀN BỘ gói sẽ bị gỡ. info: {tên: (essential, priority)}.

    initramfs_providers: các gói đang cài cung cấp linux-initramfs-tool (nếu biết)."""
    reasons = []
    bases = {n.split(":")[0] for n in names}
    for n in names:
        essential, priority = info.get(n, info.get(n.split(":")[0], (False, "")))
        r = apt_reason(n, essential, priority, dynamic)
        if not r and firmware_check and ships_firmware(n):
            r = f"Gói {n.split(':')[0]} cài firmware cho phần cứng (/lib/firmware)."
        if r:
            reasons.append(r)
        if purge:
            pr = purge_reason(n)
            if pr:
                reasons.append(pr)
    if initramfs_providers and set(initramfs_providers) <= bases:
        reasons.append("Thao tác này gỡ hết công cụ tạo initramfs (" + ", ".join(sorted(initramfs_providers))
                       + ") — kernel mới sẽ không khởi động được.")
    return list(dict.fromkeys(reasons))


def boot_hook_warnings(names: list[str]) -> list[str]:
    """Cảnh báo (không chặn) cho gói có móc initramfs/kernel: thường không làm hỏng khởi động
    (vd. ntfs-3g, brltty), nhưng nên biết trước."""
    hooks = sorted({n.split(":")[0] for n in names if ships_boot_hooks(n)})
    if not hooks:
        return []
    return ["Các gói sau có móc chạy khi tạo initramfs / cài kernel: " + ", ".join(hooks)
            + ". Chỉ gỡ nếu chắc chắn máy không cần chúng để khởi động (vd. ổ gốc NTFS, chữ nổi khi mở khoá ổ)."]


def installed_initramfs_providers(cache) -> list[str]:
    try:
        return sorted({p.name for p in cache.get_providing_packages("linux-initramfs-tool")
                       if p.is_installed})
    except Exception:
        return []


def snap_reason(name: str, snap_type: str = "", consumers: list[str] | None = None,
                base_users: list[str] | None = None) -> str:
    if snap_type in PROTECTED_SNAP_TYPES:
        users = f" (đang dùng bởi: {', '.join(sorted(base_users))})" if base_users else ""
        return f"Snap {name} là snap nền loại '{snap_type}'{users}."
    if name in CORE_SNAPS:
        return f"Snap {name} là thành phần tích hợp của Ubuntu Desktop."
    if base_users:
        return f"Snap {name} đang được dùng làm base cho: {', '.join(sorted(base_users))}."
    if consumers:
        return f"Snap {name} cung cấp thư viện dùng chung cho: {', '.join(sorted(consumers))}."
    return ""
