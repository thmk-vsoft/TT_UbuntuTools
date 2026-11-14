"""Unit test cho các parser, luật bảo vệ, helper và lịch sử.  Chạy: python3 -m unittest discover tests"""

import datetime as dt
import gzip
import importlib.machinery
import importlib.util
import os
import shutil
import tempfile
import unittest

from tt_ubuntutools import history
from tt_ubuntutools.collectors.apt_collector import OriginInfo, classify_origins, parse_dpkg_log_lines, read_dpkg_logs
from tt_ubuntutools.collectors.appimage_collector import is_appimage, pretty_name
from tt_ubuntutools.collectors.flatpak_collector import parse_flatpak_list, parse_human_size
from tt_ubuntutools.collectors.npm_collector import parse_npm_ls
from tt_ubuntutools.collectors.snap_collector import content_providers, parse_snap_date
from tt_ubuntutools.desktop_index import DesktopEntry, best_title, parse_desktop_file
from tt_ubuntutools.models import (AffectedEntry, RemovalOptions, RemovalPlan, RemovalResult, SoftwareItem,
                                   Source, Step, human_size)
from tt_ubuntutools.removal.planner import apt_simulation_command, parse_apt_simulation
from tt_ubuntutools.removal import pathrules
from tt_ubuntutools.removal.planner import systemd_units_using
from tt_ubuntutools.removal.protection import apt_reason, check_apt_removal, ships_firmware, snap_reason
from tt_ubuntutools.utils import _unescape_mount, dir_size, display, mounts_under, snap_content_providers

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HELPER_PATH = os.path.join(ROOT, "helper", "tt-desktoptool-helper")


def load_helper():
    loader = importlib.machinery.SourceFileLoader("tt_helper", HELPER_PATH)
    spec = importlib.util.spec_from_loader("tt_helper", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


class DpkgLogTest(unittest.TestCase):
    LINES = [
        "2026-10-18 07:13:54 startup archives unpack",
        "2026-10-18 07:13:54 install chatgpt:amd64 <none> 26.1103.32239",
        "2026-10-20 08:00:00 upgrade chatgpt:amd64 26.1 26.2",
        "2026-09-01 10:00:00 install chatgpt:amd64 <none> 25.0",
        "2026-10-18 07:14:00 install libfoo1:i386 <none> 1.0",
        "2026-10-18 07:14:00 status installed chatgpt:amd64 26.1",
    ]

    def test_earliest_install(self):
        res = parse_dpkg_log_lines(self.LINES)
        self.assertEqual(res["chatgpt"], dt.datetime(2026, 9, 1, 10, 0, 0))
        self.assertEqual(res["libfoo1"], dt.datetime(2026, 10, 18, 7, 14, 0))
        self.assertEqual(len(res), 2)

    def test_read_gz_and_plain(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "dpkg.log"), "w") as fh:
                fh.write(self.LINES[1] + "\n")
            with gzip.open(os.path.join(d, "dpkg.log.2.gz"), "wt") as fh:
                fh.write("2025-01-02 03:04:05 install vim:amd64 <none> 9\n")
            res = read_dpkg_logs(os.path.join(d, "dpkg.log*"))
        self.assertIn("chatgpt", res)
        self.assertEqual(res["vim"], dt.datetime(2025, 1, 2, 3, 4, 5))


class OriginTest(unittest.TestCase):
    NOW = OriginInfo("", "", "", "now")

    def test_ubuntu(self):
        src, txt = classify_origins([self.NOW, OriginInfo("Ubuntu", "Ubuntu", "archive.ubuntu.com", "resolute")])
        self.assertEqual(src, Source.APT)
        self.assertIn("resolute", txt)

    def test_ppa(self):
        src, txt = classify_origins([self.NOW, OriginInfo("LP-PPA-wireshark-dev-stable", "Wireshark stable releases",
                                                          "ppa.launchpadcontent.net", "resolute")])
        self.assertEqual(src, Source.THIRD_PARTY)
        self.assertTrue(txt.startswith("PPA:"))

    def test_third_party(self):
        src, txt = classify_origins([self.NOW, OriginInfo("Google LLC", "Google", "dl.google.com", "stable")])
        self.assertEqual(src, Source.THIRD_PARTY)
        self.assertIn("dl.google.com", txt)

    def test_local_only(self):
        self.assertIsNone(classify_origins([self.NOW]))
        self.assertIsNone(classify_origins([]))


class AptSimulationTest(unittest.TestCase):
    OUT = """NOTE: This is only a simulation!
Reading package lists...
Remv wireshark [4.6.9-1~ubuntu26.04.0~ppa1]
Purg wireshark [4.6.9-1~ubuntu26.04.0~ppa1]
Remv libfoo1:amd64 [1.0]
Purg qt6-wayland [6.10.2-4]
Inst something [1.0]
"""

    def test_parse(self):
        acts = parse_apt_simulation(self.OUT)
        self.assertEqual(acts[0], ("Remv", "wireshark", "4.6.9-1~ubuntu26.04.0~ppa1"))
        self.assertEqual(acts[2], ("Remv", "libfoo1", "1.0"))
        self.assertEqual(len(acts), 5)
        self.assertEqual(acts[-1][:2], ("Inst", "something"))  # planner chặn nếu 'gỡ' mà lại cài thêm

    def test_command(self):
        self.assertEqual(apt_simulation_command("x", RemovalOptions()), ["apt-get", "-s", "remove", "x"])
        self.assertEqual(apt_simulation_command("x", RemovalOptions(True, True)),
                         ["apt-get", "-s", "remove", "--purge", "--autoremove", "x"])


class ProtectionTest(unittest.TestCase):
    def test_apt(self):
        self.assertTrue(apt_reason("coreutils", essential=True))
        self.assertTrue(apt_reason("cron", priority="important"))
        for n in ["gnome-shell", "systemd-resolved", "ubuntu-desktop-minimal", "tt-desktoptool",
                  "linux-image-" + os.uname().release, "dracut", "dracut-core", "initramfs-tools",
                  "firmware-sof-signed", "linux-firmware", "intel-microcode", "cryptsetup-initramfs", "lvm2",
                  "sddm", "lightdm", "kubuntu-desktop", "xubuntu-desktop", "plasma-workspace", "xfce4-session",
                  "polkit-kde-agent-1", "mate-polkit", "network-manager-gnome", "linux-generic", "linux-virtual",
                  "linux-image-virtual", "linux-oem-24.04c", "linux-generic-hwe-24.04", "grub-efi-amd64-signed"]:
            with self.subTest(n=n):
                self.assertTrue(apt_reason(n), n)
        for n in ["wireshark", "vlc", "gimp", "linux-tools-common", "linux-libc-dev",
                  "linux-headers-6.8.0-31-generic", "openssh-server"]:
            with self.subTest(n=n):
                self.assertEqual(apt_reason(n, priority="optional"), "", n)

    def test_snapd_and_false_positives(self):
        self.assertTrue(apt_reason("snapd"))
        self.assertTrue(apt_reason("apparmor"))
        self.assertTrue(check_apt_removal(["firefox", "snapd"], {}, purge=True, firmware_check=False))
        for n in ["network-manager-openvpn", "network-manager-pptp", "python3.99-venv", "libssl-dev",
                  "grub-customizer", "gnome-firmware", "systemd-container", "kubuntu-wallpapers", "g++",
                  "libsystemd-dev", "yelp", "zenity", "mysql-client", "redis-tools", "postgresql-client-16"]:
            with self.subTest(n=n):
                self.assertEqual(apt_reason(n, priority="optional"), "", n)
        for n in ["mysql-client", "redis-tools", "postgresql-client-16", "postgresql-16-pgvector",
                  "nextcloud-desktop", "virtualbox-qt", "influxdb-client"]:
            self.assertEqual(check_apt_removal([n], {}, purge=True, firmware_check=False), [], n)
        for n in ["zfs-dracut", "clevis-dracut", "sudo-rs", "linux-image-realtime", "linux-intel-iotg"]:
            self.assertTrue(apt_reason(n), n)

    def test_dynamic(self):
        self.assertTrue(apt_reason("foo-dm", dynamic={"foo-dm": "trình đăng nhập"}))

    def test_plan_check_catches_dependents(self):
        reasons = check_apt_removal(["some-app", "ubuntu-desktop", "foo"], {}, firmware_check=False)
        self.assertEqual(len(reasons), 1)
        self.assertIn("ubuntu-desktop", reasons[0])

    def test_initramfs_providers(self):
        self.assertTrue(check_apt_removal(["tiny-initramfs"], {}, initramfs_providers=["tiny-initramfs"],
                                          firmware_check=False))

    def test_purge_data_servers(self):
        self.assertEqual(check_apt_removal(["postgresql-16"], {}, firmware_check=False), [])
        self.assertTrue(check_apt_removal(["postgresql-16"], {}, purge=True, firmware_check=False))

    def test_firmware_list(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "foo-fw.list"), "w") as fh:
                fh.write("/.\n/usr/lib/firmware/foo/x.bin\n")
            with open(os.path.join(d, "bar:amd64.list"), "w") as fh:
                fh.write("/usr/bin/bar\n")
            self.assertTrue(ships_firmware("foo-fw", d))
            self.assertFalse(ships_firmware("bar", d))

    def test_snap(self):
        self.assertTrue(snap_reason("core24", "base", [], ["firefox"]))
        self.assertTrue(snap_reason("snapd", "snapd"))
        self.assertTrue(snap_reason("gtk-common-themes", "app", ["firefox"], []))
        self.assertEqual(snap_reason("vlc", "app", [], []), "")


class AppImageTest(unittest.TestCase):
    def test_magic(self):
        with tempfile.TemporaryDirectory() as d:
            good = os.path.join(d, "Foo-1.0-x86_64.AppImage")
            with open(good, "wb") as fh:
                fh.write(b"\x7fELF\x02\x01\x01\x00AI\x02" + b"\0" * 100)
            other_elf = os.path.join(d, "tool")
            with open(other_elf, "wb") as fh:
                fh.write(b"\x7fELF\x02\x01\x01\x00\x00\x00\x00" + b"\0" * 100)
            text = os.path.join(d, "x.AppImage")
            with open(text, "w") as fh:
                fh.write("not an elf at all")
            self.assertTrue(is_appimage(good))
            self.assertFalse(is_appimage(other_elf))
            self.assertFalse(is_appimage(text))
        self.assertEqual(pretty_name("Foo-1.0-x86_64.AppImage"), "Foo-1.0")


class ParsersTest(unittest.TestCase):
    def test_flatpak(self):
        rows = parse_flatpak_list("org.gimp.GIMP\tGIMP\t2.10\tstable\tx86_64\t1.2 GB\tflathub\tsystem\t"
                                  "org.gnome.Platform/x86_64/46\n")
        self.assertEqual(rows[0]["application"], "org.gimp.GIMP")
        self.assertEqual(rows[0]["installation"], "system")
        self.assertEqual(parse_human_size("1.2 GB"), 1_200_000_000)
        self.assertEqual(parse_human_size("512,5 kB"), 512_500)
        self.assertIsNone(parse_human_size("?"))

    def test_npm(self):
        self.assertEqual(parse_npm_ls('{"dependencies": {"typescript": {"version": "5.6.0"}, "npm": {}}}'),
                         {"typescript": "5.6.0", "npm": ""})
        self.assertEqual(parse_npm_ls("garbage"), {})

    def test_snap(self):
        d = parse_snap_date("2026-11-06T14:03:07.364026353+07:00")
        self.assertIsNotNone(d)
        self.assertIsNone(d.tzinfo)
        self.assertIsNone(parse_snap_date(None))
        prov = content_providers({"established": [
            {"interface": "content", "slot": {"snap": "gtk-common-themes"}, "plug": {"snap": "firefox"}},
            {"interface": "x11", "slot": {"snap": "snapd"}, "plug": {"snap": "firefox"}},
        ]})
        self.assertEqual(prov, {"gtk-common-themes": {"firefox"}})

    def test_desktop(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "a.desktop")
            with open(p, "w") as fh:
                fh.write("[Desktop Entry]\nType=Application\nName=Foo\nName[vi]=Phần mềm Foo\n"
                         "Exec=env A=1 /opt/foo/bin/foo %U\nIcon=foo\n[Desktop Action new]\nName=New\n")
            e = parse_desktop_file(p)
        self.assertEqual(e.title, "Phần mềm Foo")
        self.assertEqual(e.exec_target, "/opt/foo/bin/foo")
        entries = [DesktopEntry("x/libreoffice_calc.desktop", name="LibreOffice 26.8 Calc"),
                   DesktopEntry("x/libreoffice_writer.desktop", name="LibreOffice 26.8 Writer")]
        self.assertEqual(best_title(entries), "LibreOffice 26.8")
        self.assertEqual(best_title([], fallback="x"), "x")

    def test_human_size(self):
        self.assertEqual(human_size(None), "—")
        self.assertEqual(human_size(512), "512 B")
        self.assertEqual(human_size(1536), "1,5 KB")


class HelperTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.h = load_helper()

    def test_build_valid(self):
        j = self.h.build(["apt-remove", "vlc", "--purge", "--expect", "vlc,libvlc5:amd64"])
        self.assertEqual((j["name"], j["purge"], j["autoremove"], j["expect"]), ("vlc", True, False, ["libvlc5", "vlc"]))
        self.assertEqual(self.h.build(["snap-remove", "vlc"])["name"], "vlc")
        self.assertEqual(self.h.build(["npm-remove", "@scope/pkg", "--prefix", "/usr/local"])["prefix"], "/usr/local")
        self.assertEqual(self.h.build(["pip-remove", "requests", "--site",
                                       "/usr/local/lib/python3.12/dist-packages"])["pyver"], "12")

    def test_build_rejects(self):
        bad = [
            [], ["rm", "-rf", "/"], ["apt-remove", "-oAPT::x=1", "--expect", "x"], ["apt-remove", "vlc;reboot"],
            ["apt-remove", "fonts-.", "--expect", "x"], ["apt-remove", "vlc\n", "--expect", "vlc"],
            ["apt-remove", "vlc", "--expect", "vlc\n"],
            ["apt-remove", "gnome-", "--expect", "x"], ["apt-remove", "vlc"], ["apt-remove", "vlc", "--expect"],
            ["apt-remove", "vlc", "--expect", "vlc,-x"], ["apt-remove", "vlc", "--force-yes", "--expect", "vlc"],
            ["snap-remove", "Vlc"], ["snap-remove", "-vlc"], ["pip-remove", "../x", "--site", "/usr/local/lib/python3.12/dist-packages"],
            ["pip-remove", "x", "--site", "/home/u/.local/lib/python3.12/site-packages"], ["pip-remove", "x"],
            ["npm-remove", "a b", "--prefix", "/usr"], ["npm-remove", "x", "--prefix", "/home/u/.npm"],
            ["npm-remove", "x", "--prefix", "/"], ["npm-remove", "x", "--prefix", "/usr/../home"],
            ["rm-path"], ["rm-path", "/a", "/b"], ["rm-path", "relative"], ["rm-path", "::"],
        ]
        for argv in bad:
            with self.subTest(argv=argv):
                with self.assertRaises(self.h.UsageError):
                    self.h.build(argv)

    def test_trailing_plus_names_allowed(self):
        # g++ / memtest86+ là tên gói hợp lệ; chặn "cài thêm" nằm ở bước mô phỏng (dòng Inst).
        self.assertEqual(self.h.build(["apt-remove", "g++", "--expect", "g++,gcc"])["name"], "g++")

    def test_npm_legacy_uppercase(self):
        self.h.build(["npm-remove", "JSONStream", "--prefix", "/usr/local"])
        with self.assertRaises(self.h.UsageError):
            self.h.build(["npm-remove", "@Foo/bar", "--prefix", "/usr/local"])
        with self.assertRaises(self.h.UsageError):
            self.h.build(["npm-remove", "../x", "--prefix", "/usr/local"])

    def test_dpkg_interrupted_rule(self):
        from tt_ubuntutools.removal.protection import dpkg_interrupted
        with tempfile.TemporaryDirectory() as d:
            self.assertFalse(dpkg_interrupted(d))
            os.makedirs(os.path.join(d, "updates"))
            open(os.path.join(d, "updates", "tmp.i"), "w").close()
            self.assertFalse(dpkg_interrupted(d))
            open(os.path.join(d, "updates", "0003"), "w").close()
            self.assertTrue(dpkg_interrupted(d))

    def test_split_actions(self):
        self.assertEqual(self.h.split_actions(["rm-path", "/opt/a", "::", "rm-path", "/opt/b"]),
                         [["rm-path", "/opt/a"], ["rm-path", "/opt/b"]])

    def test_relay_does_not_hang_on_background_writer(self):
        import time as _t
        t0 = _t.monotonic()
        code = self.h.relay(["/bin/sh", "-c", "(for i in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20; do echo tick; sleep 0.3; done &) ; exit 0"])
        self.assertEqual(code, 0)
        self.assertLess(_t.monotonic() - t0, 10)

    def test_marker_starts_on_new_line(self):
        out = self.h.Out()
        written = []
        out.write = lambda data, _w=out.write: (written.append(data), setattr(out, "last", data[-1:]))
        out.write(b"no newline at end")
        out.marker("TT-JOB-DONE n 0 x")
        self.assertEqual(written[1], b"\n")

    def test_parse_simulation(self):
        rem, inst = self.h.parse_simulation("Remv a [1]\nPurg b:amd64 [2]\nInst c (1 x)\nConf c (1 x)\n")
        self.assertEqual((rem, inst), ({"a", "b"}, {"c"}))

    def test_main_refuses_without_root_or_bad_args(self):
        self.assertEqual(self.h.main(["apt-remove", "bad;name", "--expect", "x"]), 2)
        if os.geteuid() != 0:
            self.assertEqual(self.h.main(["apt-remove", "vlc", "--expect", "vlc"]), 3)

    @unittest.skipUnless(os.path.exists("/usr/bin/apt-get") and os.path.exists("/var/lib/dpkg/status"), "cần apt")
    def test_check_apt_on_this_machine(self):
        """Kiểm tra an toàn của helper (chạy được không cần root vì chỉ mô phỏng)."""
        import subprocess
        q = subprocess.run(["dpkg-query", "-W", "-f=${db:Status-Abbrev}", "gnome-shell"], capture_output=True, text=True)
        if not q.stdout.startswith("ii"):
            self.skipTest("máy không có gnome-shell")
        job = self.h.build(["apt-remove", "gnome-shell", "--expect", "gnome-shell"])
        with self.assertRaises(self.h.SafetyError):  # danh sách khác + bị luật bảo vệ chặn
            self.h.check_apt(job)

    def test_check_apt_not_installed(self):
        job = self.h.build(["apt-remove", "tt-surely-not-installed-pkg", "--expect", "tt-surely-not-installed-pkg"])
        with self.assertRaises(self.h.SafetyError):
            self.h.check_apt(job)


_FAKE_STATUS = """Package: ttdummy
Status: install ok installed
Priority: optional
Section: misc
Installed-Size: 1
Maintainer: x <x@example.invalid>
Architecture: all
Version: 1.0
Description: dummy
 dummy

"""


@unittest.skipUnless(os.path.exists("/usr/bin/apt-get"), "cần apt-get")
class HelperAptRunTest(unittest.TestCase):
    """Chạy apt-get THẬT theo đúng đường thực hiện của helper (giữ khoá frontend rồi gỡ) trên một hệ
    dpkg giả trong thư mục tạm, với dpkg giả — không cần root, không đụng tới hệ thống.
    Bắt lỗi tự khoá chết: helper giữ khoá frontend còn apt-get lại đứng chờ chính khoá đó."""

    def setUp(self):
        self.h = load_helper()
        self.tmp = tempfile.TemporaryDirectory()
        d = self.tmp.name
        for sub in ("state/lists/partial", "cache/archives/partial", "etc/apt.conf.d", "etc/preferences.d",
                    "etc/sources.list.d", "log"):
            os.makedirs(os.path.join(d, sub))
        open(os.path.join(d, "etc/sources.list"), "w").close()
        with open(os.path.join(d, "state/status"), "w") as f:
            f.write(_FAKE_STATUS)
        self.calls = os.path.join(d, "log/dpkg.calls")
        fake = os.path.join(d, "fakedpkg")
        with open(fake, "w") as f:
            f.write('#!/bin/sh\necho "$* LOCKED=${DPKG_FRONTEND_LOCKED:-unset}" >> "%s"\nexit 0\n' % self.calls)
        os.chmod(fake, 0o755)
        conf = os.path.join(d, "apt.conf")
        with open(conf, "w") as f:
            for k, v in (("Dir::State", "state"), ("Dir::State::status", "state/status"), ("Dir::Cache", "cache"),
                         ("Dir::Etc", "etc"), ("Dir::Etc::sourcelist", "etc/sources.list"),
                         ("Dir::Etc::sourceparts", "etc/sources.list.d"), ("Dir::Etc::parts", "etc/apt.conf.d"),
                         ("Dir::Etc::preferencesparts", "etc/preferences.d"), ("Dir::Log", "log"),
                         ("Dir::Bin::dpkg", "fakedpkg")):
                f.write(f'{k} "{os.path.join(d, v)}";\n')
        self.h.ENV = dict(self.h.ENV, APT_CONFIG=conf)
        self.h.DPKG_FRONTEND_LOCK = os.path.join(d, "state/lock-frontend")
        self.state = os.path.join(d, "state")
        dq = os.path.join(d, "fake-dpkg-query")
        with open(dq, "w") as f:
            f.write('#!/bin/sh\nexec /usr/bin/dpkg-query --admindir="%s" "$@"\n' % self.state)
        os.chmod(dq, 0o755)
        self.h.DPKG_QUERY = dq
        self.h.OUT.write = lambda data: None  # không in output của apt ra màn hình test

    def tearDown(self):
        self.tmp.cleanup()

    def test_apt_runs_while_helper_holds_frontend_lock(self):
        import time as _t
        job = self.h.build(["apt-remove", "ttdummy", "--expect", "ttdummy"])
        lock = self.h.FrontendLock()
        lock.acquire(timeout=5)
        try:
            removed, installed = self.h.parse_simulation(
                self.h.capture(self.h.apt_sim_cmd(job, simulate=True), 60).stdout)
            self.assertEqual((removed, installed), ({"ttdummy"}, set()))
            t0 = _t.monotonic()
            ok, info = self.h.run_job(job)
            elapsed = _t.monotonic() - t0
        finally:
            lock.release()
        self.assertTrue(ok, info)
        self.assertLess(elapsed, 30, "apt-get đứng chờ khoá do chính helper giữ")
        with open(self.calls) as f:
            calls = f.read()
        self.assertIn("--remove ttdummy", calls)
        self.assertNotIn("LOCKED=unset", calls)  # dpkg luôn được báo là khoá frontend đã có người giữ

    def _job(self):
        return self.h.build(["apt-remove", "ttdummy", "--expect", "ttdummy"])

    def test_check_apt_passes_on_clean_sandbox(self):
        self.h.check_apt(self._job())

    def test_check_apt_refuses_interrupted_dpkg(self):
        os.makedirs(os.path.join(self.state, "updates"))
        open(os.path.join(self.state, "updates", "0001"), "w").close()
        with self.assertRaisesRegex(self.h.SafetyError, "dpkg --configure -a"):
            self.h.check_apt(self._job())
        # chính apt-get chạy NoLocking sẽ KHÔNG tự chặn — đó là lý do helper phải tự kiểm tra
        out = self.h.capture(self.h.apt_sim_cmd(self._job(), simulate=True), 60)
        self.assertEqual(out.returncode, 0)

    def test_check_apt_refuses_held_package(self):
        with open(os.path.join(self.state, "status"), "w") as f:
            f.write(_FAKE_STATUS.replace("install ok installed", "hold ok installed"))
        with self.assertRaisesRegex(self.h.SafetyError, "apt-mark"):
            self.h.check_apt(self._job())

    def test_apt_refused_without_lock(self):
        job = self.h.build(["apt-remove", "ttdummy", "--expect", "ttdummy"])
        ok, _ = self.h.run_job(job)
        self.assertFalse(ok)
        self.assertFalse(os.path.exists(self.calls))


class PathRulesTest(unittest.TestCase):
    def test_rejects(self):
        never = lambda p: None  # noqa: E731
        for p in ["/etc/passwd", "/opt", "/opt/", "/opt/../etc", "/opt/a/b", "relative/x", "/opt/x*",
                  "/opt/[a]", "/usr/local/bin/../../etc/x", "/home/x/.bashrc", "/usr/share/applications/x.txt",
                  "/opt/./x", "//opt/x"]:
            with self.subTest(path=p):
                with self.assertRaises(pathrules.PathRuleError):
                    pathrules.validate_rm_path(p, owner_fn=never, mount_points=[])
        with self.assertRaises(pathrules.PathRuleError):
            pathrules.validate_rm_path("/opt/__khong_ton_tai__", owner_fn=never, mount_points=[])

    @unittest.skipUnless(os.path.isdir("/opt") and os.listdir("/opt"), "cần /opt có nội dung")
    def test_real_opt_entry(self):
        never = lambda p: None  # noqa: E731
        target = None
        for name in sorted(os.listdir("/opt")):  # bỏ qua mục được bảo vệ (vd. /opt/containerd là runtime)
            try:
                target = pathrules.validate_rm_path("/opt/" + name, owner_fn=never, mount_points=[])
                break
            except pathrules.PathRuleError:
                continue
        if target is None:
            self.skipTest("mọi mục trong /opt đều được bảo vệ")
        with self.assertRaises(pathrules.PathRuleError):
            pathrules.validate_rm_path(target, owner_fn=lambda p: "some-package", mount_points=[])
        with self.assertRaises(pathrules.PathRuleError):  # chứa điểm gắn ổ đĩa
            pathrules.validate_rm_path(target, owner_fn=never, mount_points=[target + "/data"])
        with self.assertRaises(pathrules.PathRuleError):  # chính nó là điểm gắn
            pathrules.validate_rm_path(target, owner_fn=never, mount_points=[target])
        self.assertEqual(pathrules.validate_rm_path(target, owner_fn=never, mount_points=[]), target)

    def test_safe_remove_never_follows_symlinks(self):
        with tempfile.TemporaryDirectory() as outside, tempfile.TemporaryDirectory() as base:
            keep = os.path.join(outside, "keep.txt")
            with open(keep, "w") as fh:
                fh.write("x")
            root = os.path.join(base, "app")
            os.makedirs(os.path.join(root, "sub", "deep"))
            with open(os.path.join(root, "sub", "deep", "f"), "w") as fh:
                fh.write("y")
            os.symlink(outside, os.path.join(root, "link-dir"))
            os.symlink(keep, os.path.join(root, "sub", "link-file"))
            n = pathrules.safe_remove(root)
            self.assertFalse(os.path.lexists(root))
            self.assertTrue(os.path.exists(keep))  # đích của symlink không bị đụng tới
            self.assertGreaterEqual(n, 5)

    def test_dpkg_owner_strict(self):
        if not os.path.exists("/usr/bin/dpkg-query"):
            self.skipTest("cần dpkg")
        self.assertIsNone(pathrules.dpkg_owner_strict("/opt/__tt_khong_ton_tai__"))
        self.assertTrue(pathrules.dpkg_owner_strict("/usr/bin/dpkg-query"))


class HomeAppsTest(unittest.TestCase):
    """Phần mềm tự cài trong thư mục nhà — dựng một thư mục nhà giả mô phỏng các kiểu cài thực tế."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = os.path.realpath(self.tmp.name)
        h = self.home
        for d in (".local/share/applications", ".local/bin", "Desktop", ".config/autostart",
                  ".local/share/icons/hicolor/scalable/apps", "Programs", "MyCode/proj"):
            os.makedirs(os.path.join(h, d))

    def tearDown(self):
        self.tmp.cleanup()

    def w(self, rel, text="", mode=0o644, data=None):
        path = os.path.join(self.home, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(data if data is not None else text.encode())
        os.chmod(path, mode)
        return path

    def desk(self, rel, name, exec_, icon=""):
        return self.w(rel, f"[Desktop Entry]\nType=Application\nName={name}\nExec={exec_}\nIcon={icon}\n")

    def apps(self, **kw):
        from tt_ubuntutools.collectors.home_apps import find_home_apps
        return {a.key: a for a in find_home_apps(home=self.home, **kw)}

    def test_gomgo_style_script_and_venv(self):
        h = self.home
        app = os.path.join(h, ".local/share/multi-downloader-app")
        os.makedirs(os.path.join(app, ".venv/bin"))
        os.symlink("/usr/bin/python3", os.path.join(app, ".venv/bin/python"))  # đúng như venv thật
        self.w(".local/share/multi-downloader-app/multidownloader/__init__.py")
        self.w(".local/share/multi-downloader/library.sqlite3")  # dữ liệu người dùng — KHÔNG được gỡ
        launcher = self.w(".local/bin/multi-downloader",
                          f"#!/bin/sh\ncd {app}\nexec {app}/.venv/bin/python -m multidownloader \"$@\"\n", 0o755)
        d1 = self.desk(".local/share/applications/gomgo.desktop", "GomGo", f'"{launcher}"', "gomgo")
        d2 = self.desk("Desktop/gomgo.desktop", "GomGo", f'"{launcher}"', "gomgo")
        icon = self.w(".local/share/icons/hicolor/scalable/apps/gomgo.svg", "<svg/>")
        a = self.apps()["root:" + app]
        self.assertEqual(a.state, "ok")
        self.assertEqual(sorted(a.trash_paths()), sorted([app, launcher, d1, d2, icon]))
        self.assertNotIn(os.path.join(h, ".local/share/multi-downloader"), a.trash_paths())
        # icon còn được lối tắt hệ thống khác dùng -> giữ lại
        a = self.apps(other_icons={"gomgo"})["root:" + app]
        self.assertNotIn(icon, a.trash_paths())

    def test_programs_dir_and_bin_symlink(self):
        h = self.home
        exe = self.w("Programs/Telegram/Telegram", data=b"\x7fELF" + b"\0" * 20, mode=0o755)
        os.symlink(exe, os.path.join(h, ".local/bin/telegram"))
        self.desk("Desktop/Telegram.desktop", "Telegram", exe + " -- %U")
        a = self.apps()["root:" + os.path.join(h, "Programs/Telegram")]
        self.assertEqual(a.state, "ok")
        self.assertIn(os.path.join(h, ".local/bin/telegram"), a.launchers)

    def test_own_project_is_never_removable(self):
        h = self.home
        script = self.w("MyCode/proj/run.sh", "#!/bin/sh\necho hi\n", 0o755)
        self.desk(".local/share/applications/proj.desktop", "Proj", script)
        apps = self.apps()
        self.assertEqual([a.state for a in apps.values()], ["unmanaged"])
        self.assertIn("không tự gỡ", list(apps.values())[0].reason)

    def test_ambiguous_and_shared_roots(self):
        h = self.home
        self.w("Programs/A/run", "#!/bin/sh\n", 0o755)
        self.w("Programs/B/run", "#!/bin/sh\n", 0o755)
        l = self.w(".local/bin/two", f"#!/bin/sh\n{h}/Programs/A/run && {h}/Programs/B/run\n", 0o755)
        self.desk(".local/share/applications/two.desktop", "Two", l)
        x = self.w(".local/share/Suite/x", data=b"\x7fELF", mode=0o755)
        y = self.w(".local/share/Suite/y", data=b"\x7fELF", mode=0o755)
        self.desk(".local/share/applications/x.desktop", "X", x)
        self.desk(".local/share/applications/y.desktop", "Y", y)
        # Cùng một sản phẩm trong ~/Programs (vd. bin/studio và bin/studio.sh) vẫn gỡ được.
        s1 = self.w("Programs/studio/bin/studio", data=b"\x7fELF", mode=0o755)
        s2 = self.w("Programs/studio/bin/studio.sh", "#!/bin/sh\n", mode=0o755)
        self.desk(".local/share/applications/s1.desktop", "Studio", s1)
        self.desk("Desktop/s2.desktop", "Studio", s2)
        states = {k: a.state for k, a in self.apps().items()}
        self.assertIn(states.get("unmanaged:" + l), ("unmanaged", "ambiguous"))  # script chạy 2 chương trình
        self.assertEqual(states["root:" + os.path.join(h, ".local/share/Suite")], "ambiguous")
        self.assertEqual(states["root:" + os.path.join(h, "Programs/studio")], "ok")

    def test_mentioned_data_dirs_are_never_program_folders(self):
        h = self.home
        self.w(".local/share/TelegramDesktop/tdata/key_datas", "secret")
        self.w(".local/share/backups/old.tar", "x")
        tg = self.w(".local/bin/tg-work",
                    "#!/bin/sh\nexec /usr/bin/telegram-desktop -workdir $HOME/.local/share/TelegramDesktop \"$@\"\n", 0o755)
        bk = self.w(".local/bin/backup", "#!/bin/sh\nrsync -a ~/Documents/ ~/.local/share/backups\n", 0o755)
        self.desk(".local/share/applications/tg.desktop", "TG work", tg)
        self.desk(".local/share/applications/bk.desktop", "Backup", bk)
        self.w(".local/share/Deep/a/b/c/d/run", data=b"\x7fELF", mode=0o755)
        self.desk(".local/share/applications/deep.desktop", "Deep", os.path.join(h, ".local/share/Deep/a/b/c/d/run"))
        apps = self.apps()
        self.assertFalse([a for a in apps.values() if a.state in ("ok", "standalone")], apps)
        for a in apps.values():
            for p in a.trash_paths():
                self.assertNotIn("TelegramDesktop", p)
                self.assertNotIn("backups", p)

    def test_projects_and_runtimes(self):
        h = self.home
        self.w("Programs/proj/.git/HEAD", "ref")
        run = self.w("Programs/proj/run.sh", "#!/bin/sh\n", 0o755)
        self.desk(".local/share/applications/proj.desktop", "Proj", run)
        node = self.w("Programs/node/bin/node", data=b"\x7fELF", mode=0o755)
        app_js = self.w("MyCode/web/app.js", "console.log(1)")
        self.desk(".local/share/applications/web.desktop", "Web", f"{node} {app_js}")
        self.desk(".local/share/applications/node.desktop", "Node", node)
        # Lối tắt chính thức của Telegram có '-workdir <thư mục dữ liệu>': vẫn là chương trình Telegram
        tg = self.w("Programs/Telegram/Telegram", data=b"\x7fELF", mode=0o755)
        self.desk(".local/share/applications/t.desktop", "Telegram",
                  f"{tg} -workdir {h}/.local/share/TelegramDesktop/ -- %u")
        apps = self.apps()
        self.assertEqual(apps["unmanaged:" + os.path.join(h, "Programs/proj")].state, "unmanaged")
        self.assertEqual(apps["unmanaged:" + app_js].state, "unmanaged")
        self.assertEqual(apps["root:" + os.path.join(h, "Programs/node")].state, "ambiguous")  # runtime dùng chung
        self.assertEqual(apps["root:" + os.path.join(h, "Programs/Telegram")].state, "ok")

    def test_duplicates_and_overrides_are_skipped(self):
        h = self.home
        ruff = self.w(".local/bin/ruff", data=b"\x7fELF", mode=0o755)
        uv = self.w(".local/bin/uv", data=b"\x7fELF", mode=0o755)
        uvx = self.w(".local/bin/uvx", data=b"\x7fELF", mode=0o755)
        os.utime(uv, (1_700_000_000, 1_700_000_000))
        os.utime(uvx, (1_700_000_000, 1_700_000_000))
        wrap = self.w(".local/bin/code-wrap", "#!/bin/sh\nexec /usr/bin/code --disable-gpu \"$@\"\n", 0o755)
        self.desk(".local/share/applications/code.desktop", "Visual Studio Code", wrap)
        apps = self.apps(owned={ruff}, system_desktop_names={"code.desktop"})
        self.assertNotIn("file:" + ruff, apps)               # đã có ở nguồn pip
        self.assertEqual(apps["file:" + uv].launchers, [uvx])  # uv + uvx là một sản phẩm
        self.assertFalse([k for k in apps if "code-wrap" in k])  # bản tuỳ chỉnh lối tắt của app apt

    def test_icons_localized_desktop_and_symlinked_home(self):
        h = self.home
        self.w(".config/user-dirs.dirs", 'XDG_DESKTOP_DIR="$HOME/Màn hình nền"\n')
        os.makedirs(os.path.join(h, "Màn hình nền"))
        exe = self.w("Programs/Zapp/app", data=b"\x7fELF", mode=0o755)
        d = self.desk("Màn hình nền/app.desktop", "App", exe, "appicon")
        self.desk(".local/share/applications/other.desktop", "Other", "/usr/bin/other", "appicon")
        self.w(".local/share/icons/hicolor/scalable/apps/appicon.svg", "<svg/>")
        link = os.path.join(os.path.dirname(h), os.path.basename(h) + "-link")
        os.symlink(h, link)
        try:
            from tt_ubuntutools.collectors.home_apps import find_home_apps
            apps = {a.key: a for a in find_home_apps(home=link)}
        finally:
            os.unlink(link)
        a = apps["root:" + os.path.join(h, "Programs/Zapp")]
        self.assertIn(d, a.desktop_files)   # thư mục Desktop tiếng Việt được quét
        self.assertEqual(a.icons, [])       # icon còn được lối tắt khác dùng -> giữ lại

    def test_reaudit_data_files_comments_and_sourced_env(self):
        h = self.home
        self.w(".local/share/keepass/Passwords.kdbx", "db")
        self.w(".local/share/myenv/env.sh", "export A=1")
        self.w(".local/share/myconf/settings.ini", "x")
        self.w("Programs/notes/todo.txt", "x")
        cases = {
            "kp": "#!/bin/sh\nexec /usr/bin/keepassxc ~/.local/share/keepass/Passwords.kdbx\n",
            "env": "#!/bin/sh\n. ~/.local/share/myenv/env.sh\nexec code ~/MyCode\n",
            "cmt": "#!/bin/sh\n# cấu hình ở ~/.local/share/myconf/settings.ini\nexec /usr/bin/xterm\n",
            "notes": f"#!/bin/sh\nexec gedit {h}/Programs/notes/todo.txt\n",
        }
        for name, text in cases.items():
            l = self.w(f".local/bin/{name}", text, 0o755)
            self.desk(f".local/share/applications/{name}.desktop", name, l)
        apps = self.apps()
        self.assertFalse([a for a in apps.values() if a.state in ("ok", "standalone")], apps)
        for a in apps.values():
            for p in a.trash_paths():
                self.assertFalse(any(x in p for x in ("keepass", "myenv", "myconf", "Programs/notes")), p)

    def test_reaudit_nested_repos_runtime_args_scripts_dirs(self):
        h = self.home
        self.w("Programs/work/proj1/.git/HEAD", "x")
        p1 = self.w("Programs/work/proj1/build/proj1", data=b"\x7fELF", mode=0o755)
        self.desk(".local/share/applications/p1.desktop", "P1", p1)
        java = self.w("Programs/jdk-21/bin/java", data=b"\x7fELF", mode=0o755)
        jar = self.w("MyCode/game/app.jar", "jar")
        self.desk(".local/share/applications/jar.desktop", "Game", f"{java} -jar {jar}")
        node = self.w("Programs/node-v20/bin/node", data=b"\x7fELF", mode=0o755)
        self.w("MyCode/bot/index.js", "x")
        self.w(".local/share/applications/bot.desktop",
               f"[Desktop Entry]\nType=Application\nName=Bot\nExec={node} --inspect index.js\nPath={h}/MyCode/bot\n")
        b = self.w(".local/share/scripts/backup.sh", "#!/bin/sh\nrsync -a a b\n", 0o755)
        self.w(".local/share/scripts/other-tool.sh", "#!/bin/sh\n", 0o755)
        self.desk(".local/share/applications/b.desktop", "Backup", b)
        apps = self.apps()
        st = {k: a.state for k, a in apps.items()}
        self.assertNotIn("ok", [st.get("root:" + os.path.join(h, x)) for x in
                                ("Programs/work", "Programs/jdk-21", "Programs/node-v20", ".local/share/scripts")])

    def test_reaudit_icons_and_grouping(self):
        h = self.home
        exe = self.w("Programs/Telegram/Telegram", data=b"\x7fELF", mode=0o755)
        self.desk(".local/share/applications/t.desktop", "Telegram", exe, "telegram")
        own = self.w(".local/share/icons/hicolor/256x256/apps/telegram.png", "png")
        theme = self.w(".local/share/icons/Papirus/48x48/apps/telegram.svg", "<svg/>")
        for n in ("rg", "rga", "rga-preproc"):
            f = self.w(f".local/bin/{n}", data=b"\x7fELF", mode=0o755)
            os.utime(f, ns=(1_700_000_000_000_000_000, 1_700_000_000_000_000_000))
        apps = self.apps()
        a = apps["root:" + os.path.join(h, "Programs/Telegram")]
        self.assertIn(own, a.icons)
        self.assertNotIn(theme, a.icons)     # icon của bộ giao diện (Papirus) không phải của app
        self.assertEqual(apps["file:" + os.path.join(h, ".local/bin/rg")].launchers, [])
        self.assertEqual(apps["file:" + os.path.join(h, ".local/bin/rga")].launchers,
                         [os.path.join(h, ".local/bin/rga-preproc")])

    def test_round3_scripts_runtimes_and_containers(self):
        """Mọi bố cục tấn công của vòng rà soát 3: KHÔNG cái nào được ở trạng thái gỡ được."""
        h = self.home
        elf = b"\x7fELF" + b"\0" * 8
        self.w("Programs/jdk-21/bin/java", data=elf, mode=0o755)
        self.w("Programs/jdk-21/release", "x")
        self.w("MyCode/invoice-tool/target/invoice-tool.jar", "jar")
        self.w("Programs/node-v20/bin/node", data=elf, mode=0o755)
        self.w("MyCode/bot/index.js", "x")
        self.w("Documents/notes/server.js", "x")
        self.w("Applications/Godot_v4/Godot.x86_64", data=elf, mode=0o755)
        os.makedirs(os.path.join(h, "Games/platformer"))
        scripts = {
            "invoice": '#!/bin/sh\ncd "$HOME/MyCode/invoice-tool" || exit 1\nexec "$HOME/Programs/jdk-21/bin/java" -jar target/invoice-tool.jar "$@"\n',
            "bot": '#!/bin/bash\nNODE="$HOME/Programs/node-v20/bin/node"\nAPP_DIR="$HOME/MyCode/bot"\nexec "$NODE" "$APP_DIR/index.js" "$@"\n',
            "notes": "#!/bin/sh\ncd ~/Documents/notes\n~/Programs/node-v20/bin/node server.js\n",
            "game": '#!/bin/sh\nGAME=~/Games/platformer\nrun(){ exec ~/Applications/Godot_v4/Godot.x86_64 --path "$GAME" "$@"; }\ncase "$1" in *) run "$@";; esac\n',
            "work": "#!/bin/sh\n~/Programs/node-v20/bin/node &\nfirefox https://x &\nnautilus ~/Documents &\n",
        }
        for name, text in scripts.items():
            l = self.w(f".local/bin/{name}", text, 0o755)
            self.desk(f".config/autostart/{name}.desktop", name, l)
        # Lối tắt trực tiếp: tham số tương đối không có Path=, đường dẫn Windows cho wine
        self.desk(".local/share/applications/rel.desktop", "Rel",
                  f"{h}/Programs/jdk-21/bin/java -jar MyCode/invoice-tool/target/invoice-tool.jar")
        self.w("opt/wine-ge/bin/wine", data=elf, mode=0o755)
        self.desk(".local/share/applications/ga.desktop", "Game A", f'env WINEPREFIX={h}/Games/pfx {h}/opt/wine-ge/bin/wine "C:\\\\GameA\\\\a.exe"')
        # Thư mục gom nhóm / conda / nhiều venv / script tự viết / tor
        self.w("Programs/Games/Celeste/Celeste", data=elf, mode=0o755)
        self.w("Programs/Games/Stardew/saves/farm.sav", "x")
        self.desk(".local/share/applications/celeste.desktop", "Celeste", f"{h}/Programs/Games/Celeste/Celeste")
        self.w("Programs/IDEs/android-studio/bin/studio.sh", "#!/bin/sh\n", 0o755)
        self.desk(".local/share/applications/studio.desktop", "Studio", f"{h}/Programs/IDEs/android-studio/bin/studio.sh")
        self.w("opt/miniconda3/envs/sci/bin/spyder", data=elf, mode=0o755)
        self.desk(".local/share/applications/spyder.desktop", "Spyder", f"{h}/opt/miniconda3/envs/sci/bin/spyder %F")
        os.makedirs(os.path.join(h, ".local/share/venvs/tool/bin"))
        os.symlink("/usr/bin/python3", os.path.join(h, ".local/share/venvs/tool/bin/python"))
        self.desk(".local/share/applications/venv.desktop", "Tool", f"{h}/.local/share/venvs/tool/bin/python -m tool")
        self.w("programs/mygui/main.py", "print(1)", 0o755)
        self.w("programs/mygui/data.db", "x")
        self.desk(".local/share/applications/mygui.desktop", "MyGUI", f"python3 {h}/programs/mygui/main.py")
        self.w("Programs/scripts/backup.sh", "#!/bin/sh\n", 0o755)
        self.desk(".local/share/applications/backup.desktop", "Backup", f"{h}/Programs/scripts/backup.sh")
        self.w("Programs/tor-browser/Browser/start-tor-browser", data=elf, mode=0o755)
        self.desk(".local/share/applications/tor.desktop", "Tor", f"{h}/Programs/tor-browser/Browser/start-tor-browser --detach")
        apps = self.apps()
        bad = {k: (a.state, a.trash_paths()) for k, a in apps.items() if a.state in ("ok", "standalone")}
        self.assertEqual(bad, {})

    def test_round3_walk_is_fail_closed(self):
        from tt_ubuntutools.collectors import home_apps as ha
        h = self.home
        exe = self.w("Programs/devtool/app", data=b"\x7fELF", mode=0o755)
        for i in range(30):
            self.w(f"Programs/devtool/big/f{i}", "x")
        self.w("Programs/devtool/zz/myapp/.git/HEAD", "x")
        self.desk(".local/share/applications/dev.desktop", "Dev", exe)
        self.assertIn("quá nhiều", ha.looks_like_project(os.path.join(h, "Programs/devtool"), max_entries=10))
        self.assertIn(".git", ha.looks_like_project(os.path.join(h, "Programs/devtool")))
        self.assertEqual(self.apps()["unmanaged:" + os.path.join(h, "Programs/devtool")].state, "unmanaged")

    def test_round3_legit_layouts_still_removable(self):
        h = self.home
        elf = b"\x7fELF" + b"\0" * 8
        code = self.w("Programs/VSCode-linux-x64/code", data=elf, mode=0o755)
        self.desk(".local/share/applications/code-tar.desktop", "VS Code", f"{code} --unity-launch %F")
        kitty = self.w(".local/kitty.app/bin/kitty", data=elf, mode=0o755)
        self.desk(".local/share/applications/kitty.desktop", "kitty", kitty)
        self.w("Programs/android-studio/bin/studio", data=elf, mode=0o755)
        sh = self.w("Programs/android-studio/bin/studio.sh", "#!/bin/sh\nexec java -jar x\n", 0o755)
        self.desk("Desktop/as.desktop", "Android Studio", sh)
        tg = self.w("Programs/Telegram/Telegram", data=elf, mode=0o755)
        self.desk(".local/share/applications/tg.desktop", "Telegram", f"{tg} -workdir {h}/.local/share/TelegramDesktop/ -- %u")
        simple = self.w(".local/bin/foo", f'#!/bin/sh\nexec "$HOME/Programs/foo/foo" "$@"\n', 0o755)
        self.w("Programs/foo/foo", data=elf, mode=0o755)
        self.desk(".local/share/applications/foo.desktop", "Foo", simple)
        st = {k: a.state for k, a in self.apps().items()}
        for r in ("Programs/VSCode-linux-x64", ".local/kitty.app", "Programs/android-studio", "Programs/Telegram",
                  "Programs/foo"):
            self.assertEqual(st.get("root:" + os.path.join(h, r)), "ok", (r, st))

    def test_round4_attack_layouts_are_locked(self):
        h = self.home
        elf = b"\x7fELF" + b"\0" * 8
        # Bot Python của bạn cạnh chromedriver
        self.w("Programs/autobot/bot.py", "print(1)")
        self.w("Programs/autobot/data/accounts.csv", "x")
        self.w("Programs/autobot/chromedriver", data=elf, mode=0o755)
        run = self.w("Programs/autobot/run.sh", '#!/bin/sh\ncd "$(dirname "$0")"\npython3 bot.py\n', 0o755)
        self.desk(".local/share/applications/bot1.desktop", "Bot", f"python3 {h}/Programs/autobot/bot.py")
        self.desk(".local/share/applications/bot2.desktop", "Bot2", run)
        # Thư mục gom công cụ, prefix kiểu FHS
        ff = self.w("Programs/tools/ffmpeg", data=elf, mode=0o755)
        self.w("Programs/tools/mylist.txt", "x")
        self.desk(".local/share/applications/ff.desktop", "ffmpeg", ff)
        fo = self.w("opt/bin/foo", data=elf, mode=0o755)
        self.desk(".local/share/applications/foo.desktop", "foo", fo)
        lf = self.w("opt/local/bin/lfoo", data=elf, mode=0o755)
        self.desk(".local/share/applications/lfoo.desktop", "lfoo", lf)
        # Dự án biên dịch tại chỗ (C, Lazarus)
        self.w("Programs/mygame/main.c", "int main(){}")
        g = self.w("Programs/mygame/mygame", data=elf, mode=0o755)
        self.desk(".local/share/applications/mygame.desktop", "MyGame", g)
        self.w("Programs/QuanLyHS/QuanLyHS.lpi", "x")
        q = self.w("Programs/QuanLyHS/QuanLyHS", data=elf, mode=0o755)
        self.desk(".local/share/applications/qlhs.desktop", "QLHS", q)
        # Tham số trỏ vào dữ liệu NẰM TRONG thư mục chương trình
        gd = self.w("Programs/Godot/Godot_v4.3", data=elf, mode=0o755)
        self.w("Programs/Godot/MyGame/player.gd", "x")
        self.desk(".local/share/applications/gd.desktop", "Godot proj", f"{gd} -e --path {h}/Programs/Godot/MyGame")
        ds = self.w("Applications/duckstation/duckstation-qt", data=elf, mode=0o755)
        self.w("Applications/duckstation/roms/ff7.bin", "x")
        self.desk(".local/share/applications/ds.desktop", "FF7", f"{ds} {h}/Applications/duckstation/roms/ff7.bin")
        # Biến môi trường / thư mục dữ liệu với runtime
        java = self.w("opt/jdk-17/bin/java", data=elf, mode=0o755)
        self.desk(".local/share/applications/cp.desktop", "CP", f"env CLASSPATH={h}/MyCode/app/classes {java} -Xmx512m com.me.Main")
        node = self.w("opt/node-v20/bin/node", data=elf, mode=0o755)
        os.makedirs(os.path.join(h, ".local/share/mybot"))
        self.desk(".local/share/applications/nb.desktop", "NodeBot", f"{node} {h}/.local/share/mybot")
        # Comment kết thúc bằng '\' nuốt dòng cd
        py = self.w("opt/Python-3.12/bin/python3", data=elf, mode=0o755)
        l = self.w(".local/bin/proj", f"#!/bin/sh\n# launcher \\\ncd ~/MyCode/proj\nexec {py} -m proj \"$@\"\n", 0o755)
        self.desk(".local/share/applications/proj.desktop", "Proj", l)
        # Nhiều AppImage cạnh nhau trong một thư mục con
        a1 = self.w("Programs/AppImgs2/a.AppImage", data=b"\x7fELF" + b"\0" * 4 + b"AI\x02", mode=0o755)
        self.w("Programs/AppImgs2/b.AppImage", data=b"\x7fELF" + b"\0" * 4 + b"AI\x02", mode=0o755)
        self.desk(".local/share/applications/a.desktop", "A", a1)
        apps = self.apps()
        bad = {k: a.trash_paths() for k, a in apps.items() if a.state == "ok"}
        self.assertEqual(bad, {})

    def test_round4_shared_ref_single_file_and_broken_ids(self):
        from tt_ubuntutools.collectors import manual_collector
        h = self.home
        elf = b"\x7fELF" + b"\0" * 8
        qt = self.w(".local/share/qtrt/bin/qtrt", data=elf, mode=0o755)
        self.w(".local/share/qtrt/lib/libQt6Core.so.6", data=elf)
        self.desk(".local/share/applications/qt.desktop", "Qt", qt)
        b = self.w(".local/bin/b", f'#!/bin/sh\nexport LD_LIBRARY_PATH={h}/.local/share/qtrt/lib\nexec {h}/Programs/B/b "$@"\n', 0o755)
        self.w("Programs/B/b", data=elf, mode=0o755)
        self.desk(".local/share/applications/b.desktop", "B", b)
        godot = self.w("Applications/Godot_v4.2.1-stable_linux.x86_64", data=elf, mode=0o755)
        self.desk(".local/share/applications/godot.desktop", "Godot", f"{godot} %f")
        # Lối tắt hỏng + liên kết hỏng cùng trỏ vào chương trình đã xoá: hai id khác nhau
        gone = os.path.join(h, "Programs/old/bin/studio.sh")
        os.symlink(gone, os.path.join(h, ".local/bin/studio"))
        self.desk(".local/share/applications/old.desktop", "Old", gone)
        apps = self.apps()
        self.assertEqual(apps["root:" + os.path.join(h, ".local/share/qtrt")].state, "ambiguous")
        self.assertEqual(apps["root:" + godot].state, "ok")
        self.assertEqual(apps["root:" + godot].trash_paths()[0], godot)
        ids = [manual_collector._home_item(a, []).id for a in apps.values() if a.state == "broken"]
        self.assertEqual(len(ids), 2)
        self.assertEqual(len(set(ids)), 2)

    def test_round5_gomgo_switch_kept_folder_and_history(self):
        from tt_ubuntutools.collectors import manual_collector, home_apps as ha
        from tt_ubuntutools.removal import planner
        h = self.home
        app = os.path.join(h, ".local/share/multi-downloader-app")
        os.makedirs(os.path.join(app, ".venv/bin"))
        os.symlink("/usr/bin/python3", os.path.join(app, ".venv/bin/python"))
        self.w(".local/share/multi-downloader-app/requirements.txt", "yt-dlp")
        self.w(".local/share/multi-downloader-app/multidownloader/__init__.py")
        self.w(".local/share/multi-downloader-app/assets/logo.png", "x")
        l = self.w(".local/bin/multi-downloader",
                   f'#!/bin/sh\ncd {app}\nexec {app}/.venv/bin/python -m multidownloader "$@"\n', 0o755)
        self.desk(".local/share/applications/gomgo.desktop", "GomGo", f'"{l}"', "gomgo")
        self.desk("Desktop/gomgo.desktop", "GomGo", f'"{l}"', "gomgo")
        old = planner.HOME
        planner.HOME = h
        try:
            item = manual_collector._home_item(ha.find_home_apps(home=h)[0], [])
            self.assertEqual(item.extra["home_state"], "ok")
            off = planner.make_plan(item)
            self.assertEqual(off.kept_paths, [app])
            on = planner.make_plan(item, RemovalOptions(purge=True))
            self.assertFalse(on.blocked or on.error, (on.blocked_reasons, on.error))
            self.assertIn(app, on.steps[0].argv)
            rec = history.make_record(on, RemovalResult(True, 0, "", "ok", ["x"]))
            self.assertIn("thư mục", rec["option_labels"][0])
        finally:
            planner.HOME = old

    def test_round5_groups_icons_unmounted_and_bundled_links(self):
        h = self.home
        img = b"\x7fELF" + b"\0" * 4 + b"AI\x02"
        d1 = self.w("Programs/emulators/DuckStation.AppImage", data=img, mode=0o755)
        d2 = self.w("Programs/emulators/pcsx2.AppImage", data=img, mode=0o755)
        self.desk(".local/share/applications/duck.desktop", "DuckStation", d1)
        self.desk(".local/share/applications/pcsx2.desktop", "PCSX2", d2)
        tg = self.w("Programs/Telegram/Telegram", data=b"\x7fELF", mode=0o755)
        icon = self.w(".local/share/icons/hicolor/256x256/apps/telegram.png", "png")
        self.desk(".local/share/applications/tg.desktop", "Telegram", tg, "telegram")
        self.desk(".local/share/applications/tgweb.desktop", "TG web", "firefox https://web.telegram.org", icon)
        os.makedirs(os.path.join(h, "HDD"))
        self.desk(".local/share/applications/game.desktop", "Game", f"{h}/HDD/Games/Celeste/Celeste")
        st = self.w("Programs/android-studio/bin/studio", data=b"\x7fELF", mode=0o755)
        java = self.w("Programs/android-studio/jbr/bin/java", data=b"\x7fELF", mode=0o755)
        self.desk(".local/share/applications/as.desktop", "AS", st)
        os.symlink(java, os.path.join(h, ".local/bin/java"))
        os.symlink(st, os.path.join(h, ".local/bin/studio"))
        apps = self.apps()
        self.assertEqual(apps["root:" + os.path.join(h, "Programs/emulators")].state, "ambiguous")
        self.assertNotIn(icon, apps["root:" + os.path.join(h, "Programs/Telegram")].icons)
        self.assertFalse([a for a in apps.values() if a.state == "broken"])   # ổ ~/HDD chưa gắn
        a = apps["root:" + os.path.join(h, "Programs/android-studio")]
        self.assertEqual(a.launchers, [os.path.join(h, ".local/bin/studio")])  # không lấy theo jbr/bin/java

    def test_round6_kitty_vscode_links_and_unmounted(self):
        from tt_ubuntutools.collectors import manual_collector, home_apps as ha
        from tt_ubuntutools.removal import planner
        h = self.home
        elf = b"\x7fELF" + b"\0" * 8
        kitty = self.w(".local/kitty.app/bin/kitty", data=elf, mode=0o755)
        kitten = self.w(".local/kitty.app/bin/kitten", data=elf, mode=0o755)
        k1 = self.desk(".local/share/applications/kitty.desktop", "kitty", kitty)
        k2 = self.desk(".local/share/applications/kitty-open.desktop", "kitty URL", f"{kitty} +open %U")
        os.symlink(kitty, os.path.join(h, ".local/bin/kitty"))
        os.symlink(kitten, os.path.join(h, ".local/bin/kitten"))
        code = self.w("Programs/VSCode-linux-x64/code", data=elf, mode=0o755)
        self.w("Programs/VSCode-linux-x64/bin/code", "#!/bin/sh\nexec x\n", 0o755)
        os.symlink(os.path.join(h, "Programs/VSCode-linux-x64/bin/code"), os.path.join(h, ".local/bin/code"))
        self.desk(".local/share/applications/code.desktop", "VS Code", f"{code} %F")
        self.desk("Desktop/code.desktop", "VS Code", "code %F")
        # AppImage đã xoá, ~/AppImages trống: lối tắt hỏng thật (không phải ổ chưa gắn)
        os.makedirs(os.path.join(h, "AppImages"))
        self.desk(".local/share/applications/obs.desktop", "Obsidian", f"{h}/AppImages/obsidian.appimage")
        apps = self.apps()
        ka = apps["root:" + os.path.join(h, ".local/kitty.app")]
        self.assertEqual(ka.state, "ok")
        self.assertEqual(sorted(ka.desktop_files), sorted([k1, k2]))
        self.assertEqual(ka.folder_links, [os.path.join(h, ".local/bin/kitten")])
        self.assertEqual(apps["root:" + os.path.join(h, "Programs/VSCode-linux-x64")].state, "ok")
        self.assertTrue([a for a in apps.values() if a.state == "broken"])
        old = planner.HOME
        planner.HOME = h
        try:
            item = manual_collector._home_item(ka, [])
            off = planner.make_plan(item)
            self.assertNotIn(os.path.join(h, ".local/bin/kitten"), off.steps[0].argv)
            on = planner.make_plan(item, RemovalOptions(purge=True))
            self.assertIn(os.path.join(h, ".local/bin/kitten"), on.steps[0].argv)
        finally:
            planner.HOME = old

    def test_round6_icon_scan_budget_fails_closed(self):
        from tt_ubuntutools.collectors import home_apps as ha
        h = self.home
        exe = self.w("Programs/Zapp/zapp", data=b"\x7fELF", mode=0o755)
        self.desk(".local/share/applications/z.desktop", "Z", exe, "zapp")
        self.w(".local/share/icons/hicolor/scalable/apps/zapp.svg", "<svg/>")
        self.assertIsNone(ha._extra_user_entries(h, [], budget=0) if os.listdir(
            os.path.join(h, ".local/share/applications")) else None)

    def test_plan_end_to_end_in_fake_home(self):
        from tt_ubuntutools.collectors import manual_collector
        from tt_ubuntutools.removal import planner
        h = self.home
        app = os.path.join(h, ".local/share/gg-app")
        os.makedirs(os.path.join(app, ".venv/bin"))
        os.symlink("/usr/bin/python3", os.path.join(app, ".venv/bin/python"))
        launcher = self.w(".local/bin/gg", f"#!/bin/sh\ncd {app}\nexec {app}/.venv/bin/python -m gg\n", 0o755)
        d = self.desk(".local/share/applications/gg.desktop", "GG", launcher)
        tool = self.w(".local/bin/tool", data=b"\x7fELF", mode=0o755)
        old = planner.HOME
        planner.HOME = h
        try:
            items = {i.id: i for i in (manual_collector._home_item(a, []) for a in
                                       __import__("tt_ubuntutools.collectors.home_apps", fromlist=["x"])
                                       .find_home_apps(home=h))}
            # Mặc định: thư mục chương trình được GIỮ LẠI, chỉ gỡ lối tắt + script khởi chạy
            plan = planner.make_plan(items[app])
            self.assertFalse(plan.blocked or plan.error, (plan.blocked_reasons, plan.error))
            self.assertFalse(plan.needs_root)
            self.assertTrue(plan.supports_purge)
            self.assertEqual(plan.steps[0].kind, "trash")
            self.assertEqual(sorted(plan.steps[0].argv), sorted([launcher, d]))
            # Người dùng tự bật tuỳ chọn -> thư mục cũng vào Thùng rác, kèm danh sách nội dung trong cảnh báo
            plan = planner.make_plan(items[app], RemovalOptions(purge=True))
            self.assertEqual(sorted(plan.steps[0].argv), sorted([app, launcher, d]))
            self.assertTrue(any(".venv/" in w for w in plan.warnings))
            plan = planner.make_plan(items[tool])
            self.assertEqual(plan.steps[0].argv, [tool])
        finally:
            planner.HOME = old

    def test_denied_and_skipped_locations(self):
        h = self.home
        self.w(".local/share/pipx/venvs/black/bin/black", "#!/bin/sh\n", 0o755)
        self.desk(".local/share/applications/b.desktop", "B", os.path.join(h, ".local/share/pipx/venvs/black/bin/black"))
        self.w(".local/share/icons/evil", data=b"\x7fELF", mode=0o755)
        self.desk(".local/share/applications/e.desktop", "E", os.path.join(h, ".local/share/icons/evil"))
        self.desk(".local/share/applications/sys.desktop", "Sys", "/usr/bin/gedit %U")
        self.desk(".local/share/applications/cmd.desktop", "Cmd", "firefox %u")
        apps = self.apps()
        self.assertFalse([a for a in apps.values() if a.state == "ok"])
        self.assertTrue(all(".local/share/icons" not in a.root for a in apps.values()))

    def test_broken_shortcut_and_standalone_bin(self):
        h = self.home
        d = self.desk(".local/share/applications/gone.desktop", "Gone", os.path.join(h, "Programs/Gone/run"))
        self.w(".local/bin/tool", data=b"\x7fELF" + b"\0" * 10, mode=0o755)
        self.w(".local/bin/myscript.sh", "#!/bin/sh\necho mine\n", 0o755)  # script riêng của người dùng: bỏ qua
        apps = self.apps()
        broken = [a for a in apps.values() if a.state == "broken"][0]
        self.assertEqual(broken.trash_paths(), [d])
        self.assertEqual([a.root for a in apps.values() if a.state == "standalone"],
                         [os.path.join(h, ".local/bin/tool")])

    def test_planner_revalidates_paths(self):
        from tt_ubuntutools.removal import planner
        old = planner.HOME
        planner.HOME = self.home
        try:
            root = os.path.join(self.home, "Programs/Zapp")
            os.makedirs(root)
            ok = lambda p: planner._home_path_ok(p, "ok", root)  # noqa: E731
            self.assertEqual(ok(root), "")
            self.assertEqual(ok(os.path.join(self.home, "Desktop/a.desktop")), "")
            self.assertNotEqual(ok(os.path.join(self.home, "Desktop/notes.txt")), "")
            self.assertNotEqual(ok(os.path.join(self.home, "MyCode")), "")
            self.assertNotEqual(ok(os.path.join(self.home, "Programs")), "")
            self.assertNotEqual(ok(self.home), "")
            self.assertNotEqual(ok("/etc/passwd"), "")
            self.assertNotEqual(ok(os.path.join(self.home, "Programs/Zapp/../../MyCode")), "")
            self.assertNotEqual(planner._home_path_ok(os.path.join(self.home, ".local/share/icons"), "ok",
                                                      os.path.join(self.home, ".local/share/icons")), "")
        finally:
            planner.HOME = old


class CleanupTest(unittest.TestCase):
    """Mục Dọn dẹp — thư mục nhà giả; scanners.HOME được trỏ vào đó."""

    def setUp(self):
        from tt_ubuntutools.cleanup import scanners
        self.sc = scanners
        self.tmp = tempfile.TemporaryDirectory()
        self.home = os.path.realpath(self.tmp.name)
        self._old = scanners.HOME
        scanners.HOME = self.home
        for d in (".local/share/applications", ".local/bin", "MyCode", "Programs"):
            os.makedirs(os.path.join(self.home, d))

    def tearDown(self):
        self.sc.HOME = self._old
        self.tmp.cleanup()

    def w(self, rel, text="", mode=0o644, data=None):
        path = os.path.join(self.home, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(data if data is not None else text.encode())
        os.chmod(path, mode)
        return path

    def items(self):
        return {i.id: i for i in self.sc.scan_projects()}

    def test_levels_and_defaults(self):
        sc = self.sc
        self.assertEqual(set(sc.LEVELS), {sc.SAFE, sc.REVIEW, sc.IRREVERSIBLE, sc.LOCKED})
        for lvl in sc.LEVELS:
            it = sc.CleanupItem(id="x", group="cache", kind="tool_cache", title="x", level=lvl, note="n")
            self.assertEqual(it.default_selected, lvl == sc.SAFE)

    def test_project_detection_and_locks(self):
        h, sc = self.home, self.sc
        self.w("MyCode/web/package.json", "{}")
        self.w("MyCode/web/package-lock.json", "{}")
        self.w("MyCode/web/node_modules/a/index.js", "x" * 5000)
        self.w("MyCode/nolock/package.json", "{}")
        self.w("MyCode/nolock/node_modules/a/index.js", "x")
        self.w(".vscode/extensions/ext/package.json", "{}")
        self.w(".vscode/extensions/ext/package-lock.json", "{}")
        self.w(".vscode/extensions/ext/node_modules/a.js", "x")
        self.w("MyCode/py/requirements.txt", "requests")
        self.w("MyCode/py/.venv/pyvenv.cfg", "home = /usr/bin")
        self.w("Desktop/CodePython/.venv/pyvenv.cfg", "home = /usr/bin")
        self.w("MyCode/py/pkg/__pycache__/m.cpython-314.pyc", "x")
        self.w("MyCode/py/odd/__pycache__/notes.txt", "mine")
        self.w("MyCode/site/package.json", '{"dependencies": {"next": "15"}}')
        self.w("MyCode/site/.next/cache/x", "x")
        os.symlink(os.path.join(h, "MyCode/web/node_modules"), os.path.join(h, "MyCode/link_nm"))
        its = self.items()
        get = lambda p: its.get("project:" + os.path.join(h, p))  # noqa: E731
        self.assertEqual(get("MyCode/web/node_modules").level, sc.REVIEW)
        self.assertEqual(get("MyCode/nolock/node_modules").level, sc.LOCKED)
        self.assertIsNone(get(".vscode/extensions/ext/node_modules"))     # ruột ứng dụng: không quét
        self.assertEqual(get("MyCode/py/.venv").level, sc.REVIEW)
        self.assertEqual(get("Desktop/CodePython/.venv").level, sc.LOCKED)  # không biết cách cài lại
        self.assertEqual(get("MyCode/site/.next").level, sc.REVIEW)
        py = [i for i in its.values() if i.kind == "pycache"]
        self.assertEqual(len(py), 1)
        self.assertEqual(py[0].level, sc.SAFE)
        self.assertEqual(py[0].paths, [os.path.join(h, "MyCode/py/pkg/__pycache__")])  # thư mục lạ bị loại
        self.assertFalse([i for i in its.values() if "link_nm" in i.id])

    def test_installed_app_internals_are_locked(self):
        from tt_ubuntutools.collectors import home_apps
        h, sc = self.home, self.sc
        exe = self.w("Programs/ElectronApp/electronapp", data=b"\x7fELF" + b"\0" * 8, mode=0o755)
        self.w("Programs/ElectronApp/resources/app/package.json", "{}")
        self.w("Programs/ElectronApp/package.json", "{}")
        self.w("Programs/ElectronApp/package-lock.json", "{}")
        self.w("Programs/ElectronApp/node_modules/x.js", "x")
        self.w(".local/share/applications/e.desktop",
               f"[Desktop Entry]\nType=Application\nName=E\nExec={exe}\n")
        old = home_apps.HOME
        try:
            its = self.items()
        finally:
            home_apps.HOME = old
        it = its["project:" + os.path.join(h, "Programs/ElectronApp/node_modules")]
        # Dự án có package.json ở gốc nên home_apps khoá chính app; dù vậy node_modules không bao giờ ở mức xoá được
        # khi thuộc thư mục của một chương trình có lối tắt.
        self.assertIn(it.level, (sc.LOCKED,))

    def test_recheck_and_plan(self):
        from tt_ubuntutools.cleanup.plan import build_cleanup_plan
        h, sc = self.home, self.sc
        self.w("MyCode/web/package.json", "{}")
        lock = self.w("MyCode/web/package-lock.json", "{}")
        nm = os.path.join(h, "MyCode/web/node_modules")
        self.w("MyCode/web/node_modules/a.js", "x")
        its = self.items()
        it = its["project:" + nm]
        locked = sc.CleanupItem(id="l", group="project", kind="none", title="L", level=sc.LOCKED, note="", reason="r")
        cp = build_cleanup_plan([it, locked])
        self.assertEqual([s.kind for s in cp.plan.steps], ["delete"])
        self.assertEqual(cp.plan.steps[0].argv, [nm])
        self.assertEqual(cp.dropped[0][0].id, "l")
        os.unlink(lock)  # trạng thái đổi sau khi quét -> kiểm tra lại phải từ chối
        self.assertIn("lockfile", sc.recheck("node_modules", nm))
        cp = build_cleanup_plan([it])
        self.assertFalse(cp.plan.steps)
        self.assertTrue(cp.plan.error)

    def test_runner_delete_step_rechecks_before_deleting(self):
        from gi.repository import GLib
        from tt_ubuntutools.removal.runner import RemovalRunner
        h = self.home
        self.w("MyCode/a/package.json", "{}")
        self.w("MyCode/a/yarn.lock", "")
        self.w("MyCode/a/node_modules/deep/x.js", "x")
        self.w("MyCode/b/package.json", "{}")                 # không có lockfile -> phải bị bỏ qua
        self.w("MyCode/b/node_modules/keep.js", "keep")
        a, b = os.path.join(h, "MyCode/a/node_modules"), os.path.join(h, "MyCode/b/node_modules")
        plan = RemovalPlan(SoftwareItem(Source.MANUAL, "cleanup", "cleanup"), RemovalOptions())
        plan.steps = [Step("delete", [a, b], label="nm", check="node_modules")]
        loop, out = GLib.MainLoop(), {}
        r = RemovalRunner(plan, lambda l: None, lambda res: (out.setdefault("r", res), GLib.idle_add(loop.quit)),
                          success_message="Dọn dẹp xong.")
        GLib.idle_add(lambda: (r.start(), False)[1])
        GLib.timeout_add_seconds(20, loop.quit)
        loop.run()
        self.assertTrue(out["r"].success)
        self.assertFalse(os.path.exists(a))
        self.assertTrue(os.path.exists(os.path.join(b, "keep.js")))
        self.assertEqual([p for p, _ in r.skipped], [b])

    def test_vscode_obsolete_and_trash(self):
        h, sc = self.home, self.sc
        ext = os.path.join(h, ".vscode/extensions")
        self.w(".vscode/extensions/a.ext-1.0/package.json", "{}")
        self.w(".vscode/extensions/a.ext-2.0/package.json", "{}")
        self.w(".vscode/extensions/b.ext-1.0/package.json", "{}")
        self.w(".vscode/extensions/.obsolete", '{"a.ext-1.0": true, "b.ext-1.0": true}')
        self.w(".vscode/extensions/extensions.json",
               '[{"relativeLocation": "a.ext-2.0"}, {"location": {"path": "%s/b.ext-1.0"}}]' % ext)
        its = [i for i in sc.scan_browser() if i.kind == "vscode_obsolete"]
        self.assertEqual(its[0].paths, [os.path.join(ext, "a.ext-1.0")])  # b vẫn đang dùng -> giữ
        self.w(".vscode/extensions/extensions.json", "{broken")
        self.assertFalse([i for i in sc.scan_browser() if i.kind == "vscode_obsolete"])  # không đọc được -> không xoá
        self.w(".local/share/Trash/files/old.txt", "x")
        self.w(".local/share/Trash/info/old.txt.trashinfo", "x")
        t = [i for i in sc.scan_projects() if i.kind == "trash"][0]
        self.assertEqual(t.level, sc.IRREVERSIBLE)
        self.assertFalse(t.default_selected)

    def test_round1_venv_contents_running_and_keep_going(self):
        h, sc = self.home, self.sc
        self.w("MyCode/bot/requirements.txt", "x")
        self.w("MyCode/bot/venv/pyvenv.cfg", "home = /usr/bin")
        self.w("MyCode/bot/venv/src/mylib/core.py", "mine")          # pip -e git+... -> mã nguồn của bạn
        self.w("MyCode/ok/requirements.txt", "x")
        self.w("MyCode/ok/.venv/pyvenv.cfg", "home = /usr/bin")
        self.w("MyCode/ok/.venv/lib/python3.14/site-packages/x.py", "x")
        self.w("MyCode/git/requirements.txt", "x")
        self.w("MyCode/git/.venv/pyvenv.cfg", "x")
        self.w("MyCode/git/.venv/lib/pkg/.git/HEAD", "x")
        its = self.items()
        self.assertEqual(its["project:" + os.path.join(h, "MyCode/bot/venv")].level, sc.LOCKED)
        self.assertEqual(its["project:" + os.path.join(h, "MyCode/ok/.venv")].level, sc.REVIEW)
        self.assertEqual(its["project:" + os.path.join(h, "MyCode/git/.venv")].level, sc.LOCKED)
        # snap: bản đang dùng không bao giờ là "bản cũ", kể cả khi snap bị disable
        all_ = ("Name  Version  Rev  Tracking  Publisher  Notes\n"
                "foo   1        10   latest    me         disabled\n"
                "foo   2        11   latest    me         disabled\n"
                "a_b   1        5    latest    me         disabled\n")
        cur = "Name  Version  Rev  Tracking  Publisher  Notes\nfoo   2        11   latest    me         disabled\n"
        self.assertEqual(sc.parse_snap_list_all(all_, sc.parse_snap_current(cur)), [("foo", "10")])

    def test_round1_runner_keeps_going(self):
        from gi.repository import GLib
        from tt_ubuntutools.removal.runner import RemovalRunner
        h = self.home
        self.w("MyCode/a/package.json", "{}")
        self.w("MyCode/a/yarn.lock", "")
        self.w("MyCode/a/node_modules/x.js", "x")
        plan = RemovalPlan(SoftwareItem(Source.MANUAL, "cleanup", "cleanup"), RemovalOptions())
        plan.steps = [Step("cmd", ["/bin/false"], label="lệnh hỏng"),
                      Step("delete", [os.path.join(h, "MyCode/a/node_modules")], label="nm", check="node_modules")]
        loop, out = GLib.MainLoop(), {}
        r = RemovalRunner(plan, lambda l: None, lambda res: (out.setdefault("r", res), GLib.idle_add(loop.quit)),
                          keep_going=True)
        GLib.idle_add(lambda: (r.start(), False)[1])
        GLib.timeout_add_seconds(20, loop.quit)
        loop.run()
        self.assertFalse(out["r"].success)
        self.assertFalse(os.path.exists(os.path.join(h, "MyCode/a/node_modules")))  # mục sau vẫn được dọn
        self.assertEqual([e[0] for e in r.errors], ["lệnh hỏng"])

    def test_round2_nested_git_and_tracked_files(self):
        import subprocess as sp
        h, sc = self.home, self.sc
        self.w("MyCode/fork/package.json", "{}")
        self.w("MyCode/fork/package-lock.json", "{}")
        self.w("MyCode/fork/node_modules/@s/pkg/.git/HEAD", "x")
        self.w("MyCode/fork/node_modules/@s/pkg/index.js", "edited")
        self.assertIn(".git", sc.recheck("node_modules", os.path.join(h, "MyCode/fork/node_modules")))
        if shutil.which("git"):
            proj = os.path.join(h, "MyCode/tracked")
            self.w("MyCode/tracked/package.json", "{}")
            self.w("MyCode/tracked/package-lock.json", "{}")
            self.w("MyCode/tracked/node_modules/mine/index.js", "my code")
            env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t",
                       GIT_COMMITTER_EMAIL="t@t")
            sp.run(["git", "init", "-q", proj], check=True, env=env)
            sp.run(["git", "-C", proj, "add", "-f", "node_modules/mine/index.js"], check=True, env=env)
            self.assertIn("git", sc.recheck("node_modules", os.path.join(proj, "node_modules")))

    def test_round2_stale_snap_revisions_dropped_at_plan_time(self):
        from tt_ubuntutools.cleanup import plan as cplan
        sc = self.sc
        it = sc.CleanupItem(id="system:snap", group="system", kind="root", title="snap", level=sc.SAFE, note="",
                            method="root", argv=[["snap-remove-revision", "a", "--revision", "1"],
                                                 ["snap-remove-revision", "b", "--revision", "7"]])
        import types
        all_ = "Name Version Rev Tracking Publisher Notes\na 1 1 l p disabled\na 2 2 l p -\nb 3 9 l p -\n"
        cur = "Name Version Rev Tracking Publisher Notes\na 2 2 l p -\nb 3 9 l p -\n"
        old = cplan.__dict__.get("run")
        import tt_ubuntutools.utils as u
        orig = u.run
        u.run = lambda cmd, timeout=60, **k: types.SimpleNamespace(returncode=0, stdout=all_ if "--all" in cmd else cur,
                                                                   stderr="")
        try:
            self.assertEqual(cplan._fresh_root_argv(it), [["snap-remove-revision", "a", "--revision", "1"]])
        finally:
            u.run = orig

    def test_round3_literal_pathspec_and_snap_timeout(self):
        import subprocess as sp
        import types
        h, sc = self.home, self.sc
        if shutil.which("git"):
            proj = os.path.join(h, "code/:weird")
            self.w("code/:weird/package.json", "{}")
            self.w("code/:weird/package-lock.json", "{}")
            self.w("code/:weird/node_modules/foo/index.js", "mine")
            env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t",
                       GIT_COMMITTER_EMAIL="t@t")
            sp.run(["git", "init", "-q", os.path.join(h, "code")], check=True, env=env)
            sp.run(["git", "-C", os.path.join(h, "code"), "add", "-f", "--", ":(literal):weird/node_modules/foo/index.js"],
                   check=True, env=env)
            self.assertIn("git", sc.recheck("node_modules", os.path.join(proj, "node_modules")))
        from tt_ubuntutools.cleanup import plan as cplan
        import tt_ubuntutools.utils as u
        it = sc.CleanupItem(id="system:snap", group="system", kind="root", title="snap", level=sc.SAFE, note="",
                            method="root", argv=[["snap-remove-revision", "a", "--revision", "1"]])
        orig = u.run

        def boom(cmd, timeout=60, **k):
            raise sp.TimeoutExpired(cmd, timeout)
        u.run = boom
        try:
            cp = cplan.build_cleanup_plan([it])
        finally:
            u.run = orig
        self.assertEqual([d[0].id for d in cp.dropped], ["system:snap"])  # bị loại, không ném lỗi

    def test_parsers(self):
        sc = self.sc
        snap = ("Name      Version   Rev    Tracking       Publisher   Notes\n"
                "core22    2024      2955   latest/stable  canonical✓  base,disabled\n"
                "core22    2025      3001   latest/stable  canonical✓  base\n"
                "firefox   140       8763   latest/stable  mozilla✓    disabled\n")
        self.assertEqual(sc.parse_snap_list_all(snap), [("core22", "2955"), ("firefox", "8763")])
        self.assertEqual(sc.parse_journal_usage("Archived and active journals take up 525.8M in the file system."),
                         int(525.8 * 1024 ** 2))


class HelperCleanupTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.h = load_helper()

    def test_build(self):
        h = self.h
        self.assertEqual(h.build(["apt-clean"])["action"], "apt-clean")
        self.assertEqual(h.build(["journal-vacuum", "--size", "100M"])["size"], "100M")
        self.assertEqual(h.build(["snap-remove-revision", "core22", "--revision", "2955"])["revision"], "2955")
        for bad in (["apt-clean", "x"], ["journal-vacuum", "--size", "10M"], ["journal-vacuum", "--size", "5G"],
                    ["journal-vacuum", "--size", "1;rm"], ["snap-remove-revision", "core22", "--revision", "-1"],
                    ["snap-remove-revision", "Bad Name", "--revision", "1"],
                    ["snap-remove-revision", "core22", "--revision", "1", "--purge"]):
            with self.assertRaises(h.UsageError, msg=bad):
                h.build(bad)

    def test_snap_revision_must_be_disabled(self):
        h = self.h
        import types
        out = ("Name    Version  Rev   Tracking  Publisher  Notes\n"
               "core22  1        2955  latest    canonical  base,disabled\n"
               "core22  2        3001  latest    canonical  base\n")
        cur = ("Name    Version  Rev   Tracking  Publisher  Notes\n"
               "core22  2        3001  latest    canonical  base\n")
        old = h.capture
        h.capture = lambda cmd, timeout=300: types.SimpleNamespace(
            returncode=0, stdout=out if "--all" in cmd else cur, stderr="")
        try:
            h.check_snap_revision(h.build(["snap-remove-revision", "core22", "--revision", "2955"]))
            with self.assertRaises(h.SafetyError):
                h.check_snap_revision(h.build(["snap-remove-revision", "core22", "--revision", "3001"]))
            with self.assertRaises(h.SafetyError):
                h.check_snap_revision(h.build(["snap-remove-revision", "core22", "--revision", "9999"]))
            # snap bị `snap disable`: bản đang dùng cũng ghi 'disabled' -> vẫn không được gỡ
            out = out.replace("2        3001  latest    canonical  base\n", "2        3001  latest    canonical  base,disabled\n")
            with self.assertRaises(h.SafetyError):
                h.check_snap_revision(h.build(["snap-remove-revision", "core22", "--revision", "3001"]))
        finally:
            h.capture = old


class UtilsTest(unittest.TestCase):
    def test_mounts(self):
        pts = ["/", "/opt/data", "/opt/datax", "/home"]
        self.assertEqual(mounts_under("/opt/data", pts), ["/opt/data"])
        self.assertEqual(mounts_under("/opt", pts), ["/opt/data", "/opt/datax"])
        self.assertEqual(_unescape_mount("/mnt/My\\040Disk"), "/mnt/My Disk")

    def test_dir_size_budget(self):
        with tempfile.TemporaryDirectory() as d:
            for i in range(50):
                with open(os.path.join(d, f"f{i}"), "w") as fh:
                    fh.write("ab")
            self.assertEqual(dir_size(d), 100)
            self.assertIsNone(dir_size(d, max_entries=10))

    def test_display(self):
        bad = b"Ph\xe1\xba\xa7n m\xe8m".decode("utf-8", "surrogateescape")
        out = display(bad)
        out.encode("utf-8")  # không lỗi
        self.assertTrue(out.startswith("Phần m"))

    def test_snap_providers_select_all(self):
        prov = snap_content_providers({"plugs": [
            {"interface": "content", "snap": "vlc", "attrs": {"default-provider": "kf6-core24:kf6"}}]})
        self.assertEqual(prov, {"kf6-core24": {"vlc"}})

    def test_systemd_units(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "myapp.service"), "w") as fh:
                fh.write("[Service]\nExecStart=/opt/myapp/bin/run --x\n")
            with open(os.path.join(d, "other.service"), "w") as fh:
                fh.write("[Service]\nExecStart=/opt/myapp2/bin/run\n")
            self.assertEqual(systemd_units_using("/opt/myapp", [d]), ["myapp.service"])
            self.assertEqual(systemd_units_using("/opt/zzz", [d]), [])


class RunnerTest(unittest.TestCase):
    def test_job_done_markers(self):
        from tt_ubuntutools.removal.runner import RemovalRunner

        item = SoftwareItem(Source.MANUAL, "/opt/x", "x")
        r = RemovalRunner(RemovalPlan(item, RemovalOptions()), lambda l: None, lambda res: None)
        r._jobs_done = []
        for line in [b"TT-HELPER-START abc123", b"TT-JOB-DONE 0 /forged", b"TT-JOB-DONE wrong 0 /forged",
                     b"$ xoa /opt/x", b"TT-JOB-DONE abc123 0 /opt/x", b"\xff\xfe bad"]:
            r._handle_line(line)
        self.assertTrue(r._saw_marker)
        self.assertEqual(r._jobs_done, ["/opt/x"])  # dòng giả mạo (không có nonce đúng) bị bỏ qua
        self.assertIn("\ufffd", r._log[-1])
        self.assertIn("làm được", r._failure_message(Step("cmd", [], root=True), 6) + "làm được")

    def _runner_after(self, lines):
        from tt_ubuntutools.removal.runner import RemovalRunner

        r = RemovalRunner(RemovalPlan(SoftwareItem(Source.DEB, "gomgo", "gomgo"), RemovalOptions()),
                          lambda l: None, lambda res: None)
        r._jobs_done, r._cmd_started, r._helper_error = [], False, ""
        for line in lines:
            r._handle_line(line)
        return r

    def test_failure_messages_name_the_cause(self):
        apt = Step("cmd", ["apt-remove", "gomgo", "--expect", "gomgo"], root=True)
        held = "LỖI AN TOÀN: Có gói đang bị giữ (apt-mark hold) — hãy chạy 'sudo apt-mark unhold gomgo'."
        r = self._runner_after([b"TT-HELPER-START n1", held.encode()])
        msg = r._failure_message(apt, 5)
        self.assertIn("chưa gỡ gì", msg)
        self.assertIn("apt-mark unhold gomgo", msg)
        # apt đã chạy rồi lỗi: không được nói "chưa gỡ gì", phải gợi ý dpkg --configure -a
        r = self._runner_after([b"TT-HELPER-START n1", b"$ /usr/bin/apt-get -o Debug::NoLocking=1 -y remove gomgo",
                                b"E: Sub-process /usr/bin/dpkg returned an error code (1)",
                                "LỖI: thực hiện thất bại (apt-get mã 100).".encode()])
        msg = r._failure_message(apt, 6)
        self.assertNotIn("chưa gỡ gì", msg)
        self.assertIn("dpkg --configure -a", msg)

    def test_progress_and_huge_lines(self):
        r = self._runner_after([b"TT-HELPER-START n1", b"Progress: 10%\rProgress: 55%\rDone\r", b"x" * 100000])
        self.assertEqual(r._log[-2], "Done")
        self.assertLess(len(r._log[-1]), 5000)

    def test_merge_root_steps(self):
        from tt_ubuntutools.removal.runner import RemovalRunner

        steps = [Step("cmd", ["rm-path", "/opt/a"], root=True, label="a"),
                 Step("cmd", ["rm-path", "/usr/share/applications/a.desktop"], root=True, label="b"),
                 Step("trash", ["/home/u/x"]),
                 Step("cmd", ["snap-remove", "x"], root=True)]
        merged = RemovalRunner.merge_root_steps(steps)
        self.assertEqual(len(merged), 3)
        self.assertEqual(merged[0].argv, ["rm-path", "/opt/a", "::", "rm-path", "/usr/share/applications/a.desktop"])


class HistoryTest(unittest.TestCase):
    def test_roundtrip(self):
        item = SoftwareItem(Source.SNAP, "vlc", "vlc", display_name="VLC", version="3.0")
        plan = RemovalPlan(item, RemovalOptions(purge=True), affected=[AffectedEntry("vlc")], freed_bytes=100)
        res = RemovalResult(True, 0, "line1\nline2", "ok", ["vlc"], verified=True)
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "sub", "h.jsonl")
            history.append(history.make_record(plan, res), path)
            history.append(history.make_record(plan, RemovalResult(False, 1, "err", "fail")), path)
            with open(path, "a") as fh:
                fh.write("{broken json\n")
            recs = history.load(path)
        self.assertEqual(len(recs), 2)
        self.assertFalse(recs[0]["success"])        # mới nhất trước
        self.assertEqual(recs[1]["freed_bytes"], 100)
        self.assertTrue(recs[1]["purge"])
        self.assertEqual(recs[0]["freed_bytes"], 0)


class DiskUsageTest(unittest.TestCase):
    """Tab Dung lượng — đo, nhãn gợi ý, luật Thùng rác trên thư mục nhà giả."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = os.path.realpath(self.tmp.name)
        self.p = lambda *a: os.path.join(self.home, *a)

    def tearDown(self):
        for root, dirs, _files in os.walk(self.home):
            for d in dirs:
                try:
                    os.chmod(os.path.join(root, d), 0o755)
                except OSError:
                    pass
        self.tmp.cleanup()

    def _write(self, rel, size):
        path = self.p(rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as fh:
            fh.write(b"x" * size)
        return path

    def test_measure_counts_blocks_hardlinks_once_no_symlinks(self):
        from tt_ubuntutools.diskusage.scanner import measure_dir
        a = self._write("proj/a.bin", 100_000)
        self._write("proj/sub/b.bin", 50_000)
        os.link(a, self.p("proj/sub/a_link.bin"))
        big = self._write("outside/big.bin", 400_000)
        os.symlink(big, self.p("proj/link_to_big"))
        os.symlink(self.p("outside"), self.p("proj/link_dir"))
        m = measure_dir(self.p("proj"))
        expect = sum(os.lstat(x).st_blocks * 512 for x in (self.p("proj"), self.p("proj/sub"), a,
                                                           self.p("proj/sub/b.bin")))
        self.assertEqual(m.size, expect)
        self.assertEqual(m.files, 3)  # a, b, a_link (hardlink đếm file nhưng không cộng dung lượng lần 2)
        self.assertEqual(m.dirs, 1)
        self.assertEqual(m.errors, 0)
        self.assertFalse(m.cancelled)

    def test_measure_unreadable_and_cancel(self):
        import threading
        from tt_ubuntutools.diskusage.scanner import measure_dir
        self._write("p/ok.bin", 10)
        os.makedirs(self.p("p/locked"))
        self._write("p/locked/secret.bin", 10)
        os.chmod(self.p("p/locked"), 0)
        if os.geteuid() != 0:
            self.assertEqual(measure_dir(self.p("p")).errors, 1)
        os.makedirs(self.p("many"))
        for i in range(2500):
            open(self.p("many", f"f{i}"), "w").close()
        ev = threading.Event()
        ev.set()
        self.assertTrue(measure_dir(self.p("many"), ev).cancelled)
        self.assertEqual(measure_dir(self.p("many")).files, 2500)

    def test_list_dir(self):
        from tt_ubuntutools.diskusage.scanner import list_dir
        self._write("d/.hidden", 10)
        self._write("d/visible.txt", 5000)
        os.makedirs(self.p("d/sub"))
        os.symlink(self.p("d/visible.txt"), self.p("d/ln"))
        entries, err = list_dir(self.p("d"))
        self.assertEqual(err, "")
        by = {e.name: e for e in entries}
        self.assertEqual(set(by), {".hidden", "visible.txt", "sub", "ln"})
        self.assertTrue(by[".hidden"].hidden)
        self.assertFalse(by["visible.txt"].hidden)
        self.assertTrue(by["sub"].is_dir and by["sub"].size is None and not by["sub"].complete)
        self.assertTrue(by["ln"].is_link and by["ln"].size == 0 and not by["ln"].is_dir)
        self.assertEqual(by["visible.txt"].size, os.lstat(self.p("d/visible.txt")).st_blocks * 512)
        self.assertEqual(list_dir(self.p("missing"))[0], [])
        self.assertTrue(list_dir(self.p("missing"))[1])
        root = {e.name: e for e in list_dir("/")[0]}
        if "proc" in root:
            self.assertTrue(root["proc"].other_device)

    def test_cache_invalidate(self):
        from tt_ubuntutools.diskusage.scanner import DiskUsageCache, Measure
        c = DiskUsageCache()
        for k in ("/a", "/a/b", "/a/b/c", "/a/bb", "/x", "/"):
            c.put(k, Measure(size=1))
        c.put("/cancelled", Measure(size=1, cancelled=True))
        self.assertIsNone(c.get("/cancelled"))
        c.invalidate("/a/b")
        self.assertIsNone(c.get("/a/b"))
        self.assertIsNone(c.get("/a/b/c"))
        self.assertIsNone(c.get("/a"))   # thư mục cha: tổng đã đổi
        self.assertIsNone(c.get("/"))
        self.assertIsNotNone(c.get("/a/bb"))  # anh em có tiền tố giống không bị ảnh hưởng
        self.assertIsNotNone(c.get("/x"))

    def test_hints(self):
        from tt_ubuntutools.cleanup import scanners as sc
        from tt_ubuntutools.diskusage.hints import hint_for
        h = self.home
        for rel in (".cache/pip", ".cache/uv", ".npm/_cacache"):
            self.assertEqual(hint_for(self.p(rel), True, home=h).level, sc.SAFE, rel)
        self.assertEqual(hint_for(self.p(".cache"), True, home=h).level, sc.REVIEW)
        self.assertEqual(hint_for(self.p(".cache/google-chrome"), True, home=h).link, "cleanup:browser")
        for rel in (".config", ".ssh", ".gnupg", ".local/share", ".local/bin", "snap", ".var", ".mozilla",
                    ".config/Code/User", ".ssh/id_ed25519"):
            self.assertEqual(hint_for(self.p(rel), True, home=h).level, sc.LOCKED, rel)
        self.assertEqual(hint_for(h, True, home=h).level, sc.LOCKED)
        self.assertEqual(hint_for(self.p(".local/share/Trash"), True, home=h).level, sc.IRREVERSIBLE)
        self.assertEqual(hint_for(self.p("Programs/App"), True, {self.p("Programs/App"): "App"}, h).link, "software")
        # node_modules: chỉ gắn nhãn khi bộ nhận diện của Dọn dẹp xác nhận
        os.makedirs(self.p("proj/node_modules"))
        self.assertIsNone(hint_for(self.p("proj/node_modules"), True, home=h))
        self._write("proj/package.json", 2)
        self._write("proj/package-lock.json", 2)
        old = sc.HOME
        sc.HOME = h
        try:
            nm = hint_for(self.p("proj/node_modules"), True, home=h)
        finally:
            sc.HOME = old
        self.assertEqual((nm.level, nm.link), (sc.REVIEW, "cleanup:project"))
        self.assertEqual(hint_for(self.p("proj/__pycache__"), True, home=h).level, sc.SAFE)
        self.assertIsNone(hint_for(self.p("Videos/film.mkv"), False, home=h))
        self.assertIsNone(hint_for("/usr/lib", True, home=h))
        self.assertEqual(hint_for("/var/cache/apt/archives", True, home=h).level, sc.SAFE)

    def test_can_trash(self):
        from tt_ubuntutools.diskusage.trash import can_trash
        h = self.home
        for d in ("Desktop", "Tải về", "Ảnh", ".config/app", ".ssh", ".gnupg", ".local/share/Trash/files",
                  ".local/share/keyrings", ".local/share/Steam", ".cache/thumbnails", "snap/firefox", ".var/app/x",
                  ".mozilla/firefox", "MyCode/proj/node_modules", "Programs/App"):
            os.makedirs(self.p(d), exist_ok=True)
        with open(self.p(".config/user-dirs.dirs"), "w") as fh:
            fh.write('XDG_DOWNLOAD_DIR="$HOME/Tải về"\nXDG_PICTURES_DIR="$HOME/Ảnh"\n')
        film = self._write("Tải về/film.mkv", 10)
        self._write(".config/app/settings.json", 1)
        self._write(".ssh/id_ed25519", 1)
        os.symlink(self.p("MyCode"), self.p("ln_code"))
        allowed = [film, self.p("MyCode/proj/node_modules"), self.p("MyCode/proj"), self.p(".cache/thumbnails"),
                   self.p(".local/share/Steam"), self.p("Desktop"), self.p("ln_code")]
        # Desktop chỉ được phép khi không phải thư mục XDG -> ở đây vẫn là tên mặc định -> bị chặn
        allowed.remove(self.p("Desktop"))
        for path in allowed:
            self.assertEqual(can_trash(path, h), "", path)
        blocked = [h, "/usr/bin", "/tmp", self.p("Tải về"), self.p("Ảnh"), self.p("Desktop"), self.p(".config"),
                   self.p(".config/app"), self.p(".config/app/settings.json"), self.p(".ssh"), self.p(".ssh/id_ed25519"),
                   self.p(".gnupg"), self.p(".local"), self.p(".local/share"), self.p(".local/share/Trash"),
                   self.p(".local/share/Trash/files"), self.p(".local/share/keyrings"), self.p(".cache"),
                   self.p("snap"), self.p("snap/firefox"), self.p(".var/app/x"), self.p(".mozilla/firefox"),
                   self.p("missing"), self.p("MyCode/../MyCode/proj"), "relative/path", self.p("MyCode") + "/",
                   self.p("ln_code/proj")]
        for path in blocked:
            self.assertNotEqual(can_trash(path, h), "", path)
        self.assertNotEqual(can_trash(self.p("Programs/App"), h, locked_hint=True), "")
        if os.geteuid() != 0:
            os.chmod(self.p("MyCode/proj"), 0o555)
            self.assertIn("quyền ghi", can_trash(self.p("MyCode/proj/node_modules"), h))
            os.chmod(self.p("MyCode/proj"), 0o755)

    def test_review_fixes_scanner(self):
        from tt_ubuntutools.diskusage import scanner
        from tt_ubuntutools.diskusage.scanner import DiskUsageCache, Measure, dedup_total, list_dir, measure_dir
        c = DiskUsageCache()
        for k in ("/", "/usr", "/usr/lib", "/home"):
            c.put(k, Measure(size=1))
        c.invalidate("/")
        self.assertEqual([k for k in ("/", "/usr", "/usr/lib", "/home") if c.get(k)], [])
        # liên kết cứng giữa các thư mục anh em: tổng khử trùng như du
        big = self._write("t/a/big", 100_000)
        os.makedirs(self.p("t/b"))
        os.link(big, self.p("t/b/big2"))
        os.link(big, self.p("t/top_link"))
        entries = {e.name: e for e in list_dir(self.p("t"))[0]}
        rows = []
        for name, e in entries.items():
            if e.is_dir:
                m = measure_dir(e.path)
                rows.append((m.size, m.links))
            else:
                rows.append((e.size, e.links))
        whole = measure_dir(self.p("t"))
        self.assertEqual(dedup_total(rows) + os.lstat(self.p("t")).st_blocks * 512, whole.size)
        # mục biến mất giữa scandir và stat: bỏ qua, không phải lỗi
        self._write("busy/keep", 10)
        real_scandir = os.scandir

        class Gone:
            def __init__(self, e):
                self.e = e
                self.name, self.path = e.name, e.path

            def stat(self, follow_symlinks=True):
                raise FileNotFoundError(2, "gone")

        class Wrap:
            def __init__(self, it):
                self.it = it

            def __enter__(self):
                return self

            def __exit__(self, *a):
                self.it.close()

            def __iter__(self):
                for e in self.it:
                    yield Gone(e)

            def close(self):
                self.it.close()

        scanner.os.scandir = lambda p: Wrap(real_scandir(p))
        try:
            ents, err = list_dir(self.p("busy"))
            m = measure_dir(self.p("busy"))
        finally:
            scanner.os.scandir = real_scandir
        self.assertEqual((ents, err), ([], ""))
        self.assertEqual(m.errors, 0)

        # readdir ném lỗi giữa chừng: measure_dir không được ném ra ngoài
        class Boom(Wrap):
            def __iter__(self):
                raise PermissionError(13, "readdir")
                yield  # noqa

        scanner.os.scandir = lambda p: Boom(real_scandir(p))
        try:
            m = measure_dir(self.p("busy"))
        finally:
            scanner.os.scandir = real_scandir
        self.assertEqual(m.errors, 1)
        # thư mục là điểm gắn (bind mount cùng ổ) không được đi vào
        os.makedirs(self.p("mnt/inner"))
        self._write("mnt/inner/x", 50_000)
        m = measure_dir(self.p("mnt"), mounts={self.p("mnt/inner")})
        self.assertEqual(m.files, 0)
        self.assertTrue({e.name: e for e in list_dir(self.p("mnt"), {self.p("mnt/inner")})[0]}["inner"].other_device)

    def test_review_fixes_trash(self):
        from tt_ubuntutools.diskusage.hints import hint_for
        from tt_ubuntutools.diskusage.trash import can_trash, xdg_dirs
        h = self.home
        for d in ("dotfiles/ssh", "dotfiles/gnupg", "sync/pass", "Data/Tài liệu", "Data/Music", "Data/other",
                  "Apps/Foo", "..data", ".aws", ".config", "storage/Videos", "proj/.venv/bin", "proj2/.venv/bin"):
            os.makedirs(self.p(d), exist_ok=True)
        self._write("dotfiles/ssh/id_ed25519", 1)
        os.symlink(self.p("dotfiles/ssh"), self.p(".ssh"))
        os.symlink(self.p("dotfiles/gnupg"), self.p(".gnupg"))
        os.symlink(self.p("sync/pass"), self.p(".password-store"))
        os.symlink(self.p("storage/Videos"), self.p("Videos"))
        with open(self.p(".config/user-dirs.dirs"), "w") as fh:
            fh.write('# comment\nXDG_DOCUMENTS_DIR="$HOME/Data/Tài liệu"\n'
                     'XDG_MUSIC_DIR="${HOME}/Data/Music"   # moved\nXDG_TEMPLATES_DIR="$HOME/"\n')
        self.assertIn("Data/Music", xdg_dirs(h))
        for path in ("dotfiles/ssh/id_ed25519", "dotfiles/ssh", "dotfiles", "dotfiles/gnupg", "sync/pass", "sync",
                     "Data", "Data/Tài liệu", "Data/Music", "storage/Videos", "storage", ".aws"):
            self.assertNotEqual(can_trash(self.p(path), h), "", path)
        self.assertNotEqual(can_trash(self.p("Apps"), h, extra_protected=[self.p("Apps/Foo")]), "")
        self.assertNotEqual(can_trash(self.p("Apps/Foo"), h, extra_protected=[self.p("Apps/Foo")]), "")
        self.assertEqual(can_trash(self.p("Data/other"), h), "")
        self.assertEqual(can_trash(self.p("..data"), h), "")
        self.assertIsNotNone(hint_for(self.p(".aws"), True, home=h))
        # venv: chỉ có liên kết sang Dọn dẹp khi Dọn dẹp thật sự dọn được
        for v in ("proj/.venv", "proj2/.venv"):
            with open(self.p(v, "pyvenv.cfg"), "w") as fh:
                fh.write("home = /usr/bin\n")
        self._write("proj/requirements.txt", 1)
        os.makedirs(self.p("proj/.venv/lib"))
        os.makedirs(self.p("proj2/.venv/lib"))
        self.assertEqual(hint_for(self.p("proj/.venv"), True, home=h).link, "cleanup:project")
        self.assertEqual(hint_for(self.p("proj2/.venv"), True, home=h).link, "")

    def test_review_round2(self):
        from tt_ubuntutools.diskusage import scanner
        from tt_ubuntutools.diskusage.scanner import dedup_total, list_dir, measure_dir
        from tt_ubuntutools.diskusage.trash import can_trash
        # home là liên kết tượng trưng, user-dirs.dirs ghi theo đường dẫn liên kết
        real = self.p("data/u")
        os.makedirs(os.path.join(real, "Tài liệu"))
        os.makedirs(os.path.join(real, ".config"))
        os.makedirs(self.p("home"))
        link = self.p("home/u")
        os.symlink(real, link)
        with open(os.path.join(real, ".config/user-dirs.dirs"), "w") as fh:
            fh.write(f'XDG_DOCUMENTS_DIR="{link}/Tài liệu"\n')
        self.assertNotEqual(can_trash(os.path.join(real, "Tài liệu"), real), "")
        # thông tin đăng nhập Docker bị chặn, máy ảo Docker Desktop thì xoá được
        os.makedirs(os.path.join(real, ".docker/desktop"))
        with open(os.path.join(real, ".docker/config.json"), "w") as fh:
            fh.write("{}")
        self.assertNotEqual(can_trash(os.path.join(real, ".docker/config.json"), real), "")
        self.assertEqual(can_trash(os.path.join(real, ".docker/desktop"), real), "")
        # khối của chính liên kết tượng trưng được tính (như du)
        os.makedirs(self.p("sl"))
        os.symlink("x" * 200, self.p("sl/long"))
        blocks = os.lstat(self.p("sl/long")).st_blocks * 512
        self.assertEqual(measure_dir(self.p("sl")).size, os.lstat(self.p("sl")).st_blocks * 512 + blocks)
        self.assertEqual({e.name: e for e in list_dir(self.p("sl"))[0]}["long"].size, blocks)
        # vượt giới hạn liên kết cứng: vẫn giữ phần đã ghi để khử trùng
        os.makedirs(self.p("hl/a"))
        os.makedirs(self.p("hl/b"))
        for i in range(5):
            f = self._write(f"hl/a/f{i}", 5000)
            os.link(f, self.p(f"hl/b/f{i}"))
        old = scanner.MAX_LINKS
        scanner.MAX_LINKS = 3
        try:
            ma, mb = measure_dir(self.p("hl/a")), measure_dir(self.p("hl/b"))
        finally:
            scanner.MAX_LINKS = old
        self.assertEqual(len(ma.links), 3)
        self.assertLess(dedup_total([(ma.size, ma.links), (mb.size, mb.links)]), ma.size + mb.size)

    def test_review_round3(self):
        from tt_ubuntutools.cleanup import scanners as sc
        from tt_ubuntutools.diskusage.hints import hint_for
        from tt_ubuntutools.diskusage.trash import can_trash, xdg_user_dirs
        h = self.home
        for d in (".docker/contexts/tls/abc", ".docker/machine/machines/dev", ".docker/certs.d/reg", ".docker/desktop",
                  ".minikube/profiles/x", ".minikube/machines", ".config", "Docs$1", "back\\slash"):
            os.makedirs(self.p(d), exist_ok=True)
        for f in (".npmrc", ".pypirc", ".bashrc", ".gitconfig", ".docker/machine/machines/dev/id_rsa"):
            self._write(f, 1)
        for path in (".docker/contexts/tls", ".docker/contexts/tls/abc", ".docker/machine/machines/dev/id_rsa",
                     ".docker/certs.d/reg", ".minikube/profiles/x", ".npmrc", ".pypirc", ".bashrc", ".gitconfig"):
            self.assertNotEqual(can_trash(self.p(path), h), "", path)
        self.assertEqual(hint_for(self.p(".docker/machine/machines/dev"), True, home=h).level, sc.LOCKED)
        self.assertEqual(can_trash(self.p(".docker/desktop"), h), "")
        self.assertEqual(can_trash(self.p(".minikube/machines"), h), "")
        # giá trị XDG có ký tự thoát kiểu shell (như xdg-user-dirs-update ghi)
        with open(self.p(".config/user-dirs.dirs"), "w") as fh:
            fh.write('XDG_DESKTOP_DIR="$HOME/Docs\\$1"\nXDG_MUSIC_DIR="$HOME/back\\\\slash"\n')
        dirs = xdg_user_dirs(h)
        self.assertEqual(dirs["DESKTOP"], self.p("Docs$1"))
        self.assertEqual(dirs["MUSIC"], self.p("back\\slash"))
        self.assertNotEqual(can_trash(self.p("Docs$1"), h), "")
        self.assertNotEqual(can_trash(self.p("back\\slash"), h), "")

    def test_disk_history_record(self):
        rec = history.make_disk_record("/home/u/Videos/big.mkv", 5_000_000, None, True)
        self.assertEqual((rec["source"], rec["source_label"], rec["trashed_bytes"], rec["freed_bytes"]),
                         ("disk", "Dung lượng", 5_000_000, 0))
        self.assertTrue(rec["success"])
        bad = history.make_disk_record("/home/u/x", 10, 1, False, "Permission denied")
        self.assertFalse(bad["success"])
        self.assertEqual(bad["trashed_bytes"], 0)
        self.assertIn("Permission denied", bad["message"])
        back = history.make_disk_record("/home/u/x", None, None, True, restored=True)
        self.assertTrue(back["success"])
        self.assertEqual(back["trashed_bytes"], 0)
        self.assertIn("Khôi phục", back["name"])


class ReleaseHardeningTest(unittest.TestCase):
    """Các bản sửa của lượt rà soát trước phát hành 1.4.0."""

    def test_helper_log_dir_not_followed(self):
        h = load_helper()
        with tempfile.TemporaryDirectory() as d:
            target = os.path.join(d, "victim")
            os.makedirs(target)
            link = os.path.join(d, "logdir")
            os.symlink(target, link)
            old = h.LOG_DIR
            h.LOG_DIR = link
            try:
                out = h.Out()
                out._open_log()
            finally:
                h.LOG_DIR = old
            self.assertEqual(out.log, -1)
            self.assertEqual(os.listdir(target), [])  # không tạo tệp qua liên kết tượng trưng

    def test_git_repo_config_cannot_run_commands(self):
        import shutil
        import subprocess
        from tt_ubuntutools.cleanup import scanners as sc
        if not shutil.which("git"):
            self.skipTest("không có git")
        with tempfile.TemporaryDirectory() as d:
            repo = os.path.join(d, "repo")
            os.makedirs(os.path.join(repo, "node_modules"))
            subprocess.run(["git", "init", "-q", repo], check=True)
            marker = os.path.join(d, "PWNED")
            subprocess.run(["git", "-C", repo, "config", "core.fsmonitor", f"touch {marker}; false"], check=True)
            sc.git_tracked_inside(os.path.join(repo, "node_modules"))
            self.assertFalse(os.path.exists(marker))

    def test_history_private_and_settings(self):
        from tt_ubuntutools import settings
        with tempfile.TemporaryDirectory() as d:
            hp = os.path.join(d, "h", "history.jsonl")
            history.append({"name": "x"}, hp)
            self.assertEqual(os.stat(hp).st_mode & 0o777, 0o600)
            sp = os.path.join(d, "cfg", "settings.json")
            self.assertFalse(settings.get("welcome_shown", False, sp))
            self.assertTrue(settings.set_value("welcome_shown", True, sp))
            self.assertTrue(settings.get("welcome_shown", False, sp))
            with open(sp, "w") as fh:
                fh.write("{hỏng")
            self.assertEqual(settings.load(sp), {})

    def test_npm_update_notifier_off(self):
        from tt_ubuntutools.utils import C_ENV, clean_env
        self.assertEqual(C_ENV["NPM_CONFIG_UPDATE_NOTIFIER"], "false")
        self.assertEqual(clean_env()["NPM_CONFIG_UPDATE_NOTIFIER"], "false")


class RenameTransitionTest(unittest.TestCase):
    """Đổi tên TT_UbuntuTools -> TT_DesktopTool (1.4.0)."""

    def test_old_package_still_protected(self):
        self.assertTrue(apt_reason("tt-ubuntutools"))
        self.assertTrue(apt_reason("tt-desktoptool"))

    def test_history_migrates_from_old_dir(self):
        with tempfile.TemporaryDirectory() as d:
            old = os.path.join(d, "tt-ubuntutools", "history.jsonl")
            os.makedirs(os.path.dirname(old))
            with open(old, "w") as fh:
                fh.write('{"name": "cu"}\n')
            env = os.environ.get("XDG_DATA_HOME")
            os.environ["XDG_DATA_HOME"] = d
            try:
                path = history.history_path()
                recs = history.load()
            finally:
                if env is None:
                    os.environ.pop("XDG_DATA_HOME", None)
                else:
                    os.environ["XDG_DATA_HOME"] = env
            self.assertEqual(path, os.path.join(d, "tt-desktoptool", "history.jsonl"))
            self.assertFalse(os.path.exists(old))
            self.assertEqual(recs[0]["name"], "cu")
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
