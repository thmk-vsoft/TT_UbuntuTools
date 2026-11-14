"""Màn hình chào lần đầu: giới thiệu ba khu vực, cách bắt đầu, dòng miễn trừ ngắn, nút Đóng góp dự án."""

from __future__ import annotations

from gi.repository import Adw, Gtk

from .. import APP_NAME, settings
from .donate import DonateDialog, heart_image, T as DT

KEY = "welcome_shown"

SECTIONS = [
    ("system-software-install-symbolic", "Gỡ cài đặt", "Ctrl+1",
     "Xem mọi phần mềm đã cài theo cách cài (APT, Snap, Flatpak, AppImage, pip, npm…) và gỡ từng cái, có xem trước."),
    ("edit-clear-all-symbolic", "Dọn dẹp", "Ctrl+2",
     "Tìm bộ nhớ đệm, gói đã tải, node_modules, .venv… phân loại theo mức an toàn bằng màu sắc và ghi chú."),
    ("drive-harddisk-symbolic", "Dung lượng", "Ctrl+3",
     "Xem thư mục / file nào chiếm nhiều chỗ và chuyển mục lớn trong thư mục nhà vào Thùng rác."),
]

TEXT = {
    "title": "Chào mừng bạn đến với {0}",
    "start_hint": "Bắt đầu: chọn một khu vực ở đầu cửa sổ.",
    "disclaimer": "Phần mềm được cung cấp “nguyên trạng” — hãy đọc kỹ trang xem trước trước khi gỡ, dọn hoặc xoá.",
    "dont_show": "Không hiện lại",
    "start": "Bắt đầu",
}


def should_show() -> bool:
    return not settings.get(KEY, False)


class WelcomeDialog(Adw.Dialog):
    def __init__(self, parent: Gtk.Widget):
        super().__init__(title=APP_NAME, content_width=520)
        self._parent = parent
        tv = Adw.ToolbarView()
        tv.add_top_bar(Adw.HeaderBar())
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14, margin_start=24, margin_end=24,
                      margin_bottom=20)
        title = Gtk.Label(label=TEXT["title"].format(APP_NAME), wrap=True, justify=Gtk.Justification.CENTER)
        title.add_css_class("title-2")
        box.append(title)
        for icon, name, key, desc in SECTIONS:
            row = Gtk.Box(spacing=14)
            img = Gtk.Image.new_from_icon_name(icon)
            img.set_pixel_size(28)
            img.set_valign(Gtk.Align.START)
            row.append(img)
            v = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            head = Gtk.Label(xalign=0, label=f"{name}  ({key})")
            head.add_css_class("heading")
            v.append(head)
            v.append(Gtk.Label(label=desc, xalign=0, wrap=True))
            row.append(v)
            box.append(row)
        box.append(Gtk.Label(label=TEXT["start_hint"], xalign=0, wrap=True))
        disc = Gtk.Label(label=TEXT["disclaimer"], xalign=0, wrap=True)
        disc.add_css_class("dim-label")
        disc.add_css_class("caption")
        box.append(disc)

        foot = Gtk.Box(spacing=10, margin_top=4)
        self.dont_show = Gtk.CheckButton(label=TEXT["dont_show"], active=True, hexpand=True)
        foot.append(self.dont_show)
        donate_box = Gtk.Box(spacing=7)
        donate_box.append(heart_image(16))
        donate_box.append(Gtk.Label(label=DT["donate"]))
        donate = Gtk.Button(child=donate_box, tooltip_text=DT["tip"])
        donate.connect("clicked", lambda *_: DonateDialog().present(self._parent))
        foot.append(donate)
        start = Gtk.Button(label=TEXT["start"])
        start.add_css_class("suggested-action")
        start.connect("clicked", lambda *_: self.close())
        foot.append(start)
        box.append(foot)

        tv.set_content(box)
        self.set_child(tv)
        self.set_default_widget(start)
        self.set_focus(start)
        self.connect("closed", self._on_closed)

    def _on_closed(self, *_):
        if self.dont_show.get_active():
            settings.set_value(KEY, True)
