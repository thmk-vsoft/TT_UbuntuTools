"""Adw.Application của TT_DesktopTool."""

from __future__ import annotations

import os
import sys

MIN_ADW = (1, 5)


def _check_environment() -> str:
    """Trả về thông báo lỗi dễ hiểu nếu không chạy được, '' nếu ổn."""
    if os.geteuid() == 0:
        return ("Không chạy TT_DesktopTool bằng quyền root/sudo. Hãy mở bằng tài khoản thường — "
                "công cụ sẽ tự hỏi mật khẩu quản trị khi cần gỡ phần mềm.")
    try:
        import gi

        gi.require_version("Gtk", "4.0")
        gi.require_version("Adw", "1")
        from gi.repository import Adw  # noqa: F401
    except (ImportError, ValueError) as exc:
        return ("Thiếu thư viện giao diện GTK4 / libadwaita. Hãy cài: "
                "sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 python3-apt\n"
                f"(chi tiết: {exc})")
    ver = (Adw.get_major_version(), Adw.get_minor_version())
    if ver < MIN_ADW:
        return (f"Cần libadwaita ≥ {MIN_ADW[0]}.{MIN_ADW[1]} (Ubuntu 24.04 trở lên); máy đang có "
                f"{ver[0]}.{ver[1]}.")
    return ""


_ERR = _check_environment()
if not _ERR:
    from gi.repository import Adw, Gio, Gtk  # noqa: E402

    from . import APP_ID  # noqa: E402

    class TTApplication(Adw.Application):
        def __init__(self):
            super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
            self.window = None
            self.busy = 0  # số thao tác gỡ đang chạy
            self._inhibit_cookie = 0
            for name, cb in (("about", self._on_about), ("history", self._on_history), ("cleanup", self._on_cleanup),
                             ("donate", self._on_donate), ("quit", self._on_quit)):
                act = Gio.SimpleAction.new(name, None)
                act.connect("activate", cb)
                self.add_action(act)
            self.set_accels_for_action("app.quit", ["<Control>q"])
            self.set_accels_for_action("app.history", ["<Control>h"])
            self.set_accels_for_action("app.cleanup", ["<Control>k"])

        def do_startup(self):
            Adw.Application.do_startup(self)
            from .ui.common import load_css
            from .ui.donate import register_assets

            load_css()
            register_assets()
            # Ctrl+C / SIGTERM / đóng terminal (SIGHUP) đi qua cùng đường thoát có kiểm tra "đang gỡ".
            try:
                from gi.repository import GLib

                for sig in (1, 2, 15):  # SIGHUP, SIGINT, SIGTERM
                    GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, sig, lambda *_: (self._on_quit(), True)[1])
            except Exception:
                pass

        def do_activate(self):
            from .ui.window import MainWindow


            if self.window is None:
                self.window = MainWindow(self)
            self.window.present()

        # ---- trạng thái bận: chặn thoát / đăng xuất / ngủ khi đang gỡ -------------
        def begin_busy(self):
            self.busy += 1
            if self.busy == 1:
                try:
                    self._inhibit_cookie = self.inhibit(
                        self.window, Gtk.ApplicationInhibitFlags.LOGOUT | Gtk.ApplicationInhibitFlags.SUSPEND,
                        "Đang gỡ cài đặt / dọn dẹp")
                except Exception:
                    self._inhibit_cookie = 0

        def end_busy(self):
            self.busy = max(0, self.busy - 1)
            if self.busy == 0 and self._inhibit_cookie:
                self.uninhibit(self._inhibit_cookie)
                self._inhibit_cookie = 0

        def _on_quit(self, *_):
            if self.busy:
                if self.window:
                    from .ui.common import make_toast

                    self.window.toasts.add_toast(make_toast("Đang gỡ cài đặt / dọn dẹp — chưa thể thoát.", 4))
                return
            self.quit()

        def _on_about(self, *_):
            from .ui.donate import AboutDialog

            AboutDialog(self.window).present(self.window)

        def _on_donate(self, *_):
            from .ui.donate import DonateDialog

            DonateDialog().present(self.window)

        def _on_cleanup(self, *_):
            if self.window is not None:
                self.window.open_cleanup()

        def _on_history(self, *_):
            from .ui.history_dialog import HistoryDialog

            HistoryDialog().present(self.window)


def _show_startup_error(msg: str) -> None:
    """Mở từ menu ứng dụng thì không có terminal: hiện thông báo bằng zenity/notify-send nếu có."""
    import shutil
    import subprocess

    for cmd in (["zenity", "--error", "--no-markup", "--title=TT_DesktopTool", f"--text={msg}"],
                ["notify-send", "TT_DesktopTool", msg]):
        if shutil.which(cmd[0]):
            try:
                if subprocess.run(cmd, timeout=600, check=False).returncode == 0:
                    return
            except (OSError, subprocess.TimeoutExpired):
                continue


def main(argv=None) -> int:
    if _ERR:
        print("TT_DesktopTool: " + _ERR, file=sys.stderr)
        if os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"):
            _show_startup_error(_ERR)
        return 1
    app = TTApplication()
    return app.run(argv if argv is not None else sys.argv)
