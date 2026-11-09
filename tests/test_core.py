"""Unit test cho các parser, luật bảo vệ, helper và lịch sử.  Chạy: python3 -m unittest discover tests"""

import datetime as dt
import gzip
import importlib.machinery
import importlib.util
import os
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
HELPER_PATH = os.path.join(ROOT, "helper", "tt-ubuntutools-helper")


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
        for n in ["gnome-shell", "systemd-resolved", "ubuntu-desktop-minimal", "tt-ubuntutools",
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
        target = "/opt/" + sorted(os.listdir("/opt"))[0]
        never = lambda p: None  # noqa: E731
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


if __name__ == "__main__":
    unittest.main()
