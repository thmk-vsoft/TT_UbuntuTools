"""Phần "Đóng góp dự án" (Buy me a coffee) và hộp thoại Giới thiệu — theo bộ mẫu DonateKit của THMK V-Soft."""

from __future__ import annotations

import os
import urllib.parse

from gi.repository import Adw, Gdk, GLib, Gtk

from .. import APP_ID, APP_NAME, VERSION

ASSETS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets")
QR_PATH = os.path.join(ASSETS, "donate-qr.png")
ICONS_DIR = os.path.join(ASSETS, "icons")
QR_RATIO = 1006 / 886  # cao / rộng của donate-qr.png
COPYRIGHT_YEAR = 2026  # khớp với LICENSE

INFO = {
    "owner": "THMK V-Soft",
    "email": "ttvn92i2026@gmail.com",
    "bank": "VIB",
    "account": "099428268",
}

T = {
    "donate": "Đóng góp dự án",
    "tip": "Ủng hộ dự án nếu bạn thấy phần mềm hữu ích",
    "title": "Đóng góp dự án",
    "heading": "Buy me a coffee ☕",
    "text": "Ủng hộ dự án nếu bạn thấy phần mềm hữu ích.",
    "scan": "Quét mã QR bằng ứng dụng ngân hàng hoặc ví điện tử có hỗ trợ VietQR / Napas 247.",
    "account": "Ngân hàng {bank} · Số tài khoản {account}",
    "copy_tip": "Sao chép số tài khoản",
    "copy": "Sao chép",
    "copied": "Đã sao chép",
    "thanks": "Cảm ơn bạn đã ủng hộ!",
    "close": "Đóng",
    "enlarge": "Bấm để phóng to mã QR",
    "license": "Thông tin bản quyền",
    "copyright": "Bản quyền phần mềm: ",
    "contact": "Liên hệ: ",
    "copy_mail": "Sao chép địa chỉ email",
    "rights": "© {year} {owner}. Bảo lưu mọi quyền.",
}

DONATE_CSS = """
.dk-heart { color: #DB2777; }
.dk-dark .dk-heart, .dk-dark.dk-heart { color: #F472B6; }
.dk-heading { color: #DB2777; font-size: 1.6em; font-weight: 800; }
.dk-dark .dk-heading { color: #F472B6; }
.dk-qr-card { background: #FFFFFF; border-radius: 12px; padding: 8px; }
.dk-box { border-radius: 12px; padding: 14px 16px; border: 1px solid alpha(@borders, 1.0);
          background: alpha(@card_bg_color, 0.8); }
.dk-thanks { font-style: italic; }
button.dk-primary { background: #0969DA; color: #FFFFFF; }
.dk-dark button.dk-primary { background: #1F6FEB; }
"""


def register_assets() -> None:
    """Thêm thư mục icon riêng (trái tim) và CSS của phần Đóng góp."""
    display = Gdk.Display.get_default()
    Gtk.IconTheme.get_for_display(display).add_search_path(ICONS_DIR)
    provider = Gtk.CssProvider()
    provider.load_from_string(DONATE_CSS)
    Gtk.StyleContext.add_provider_for_display(display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)


def _track_dark(widget: Gtk.Widget) -> None:
    """Đổi màu hồng theo giao diện Sáng / Tối (#DB2777 / #F472B6)."""
    sm = Adw.StyleManager.get_default()

    def apply(*_):
        if sm.get_dark():
            widget.add_css_class("dk-dark")
        else:
            widget.remove_css_class("dk-dark")

    apply()
    handler = sm.connect("notify::dark", apply)
    widget.connect("destroy", lambda *_: sm.disconnect(handler))


def heart_image(size: int = 16) -> Gtk.Image:
    img = Gtk.Image.new_from_icon_name("tt-heart-symbolic")
    img.set_pixel_size(size)
    img.add_css_class("dk-heart")
    return img


def _copy_text(widget: Gtk.Widget, text: str) -> None:
    widget.get_clipboard().set(text)


def _copy_button(text_to_copy: str, tooltip: str) -> Gtk.Button:
    btn = Gtk.Button(label=T["copy"], tooltip_text=tooltip, valign=Gtk.Align.CENTER)

    def on_click(b):
        _copy_text(b, text_to_copy)
        b.set_label(T["copied"])
        GLib.timeout_add(1800, lambda: (b.set_label(T["copy"]), False)[1])

    btn.connect("clicked", on_click)
    return btn


def _qr_picture(width: int) -> Gtk.Widget:
    pic = Gtk.Picture.new_for_filename(QR_PATH)
    pic.set_content_fit(Gtk.ContentFit.CONTAIN)
    pic.set_can_shrink(True)
    pic.set_size_request(width, int(width * QR_RATIO))
    card = Gtk.Box(halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
    card.add_css_class("dk-qr-card")  # luôn nền trắng để quét được ở giao diện Tối
    card.append(pic)
    return card


def donate_button(compact_breakpoint: Adw.Breakpoint | None = None) -> Gtk.Button:
    """Nút 'Đóng góp dự án' cho thanh tiêu đề: trái tim hồng + chữ."""
    box = Gtk.Box(spacing=7)
    box.append(heart_image(16))
    label = Gtk.Label(label=T["donate"])
    box.append(label)
    btn = Gtk.Button(child=box, tooltip_text=T["tip"], action_name="app.donate")
    _track_dark(btn)
    if compact_breakpoint is not None:
        compact_breakpoint.add_setter(label, "visible", False)
    return btn


class DonateDialog(Adw.Dialog):
    """Hộp thoại 'Đóng góp dự án' (rộng ~460 px). Esc hoặc Đóng để đóng."""

    def __init__(self):
        super().__init__(title=T["title"], content_width=460)
        _track_dark(self)
        tv = Adw.ToolbarView()
        tv.add_top_bar(Adw.HeaderBar())
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin_start=24, margin_end=24,
                      margin_bottom=20)
        heading = Gtk.Label(label=T["heading"])
        heading.add_css_class("dk-heading")
        box.append(heading)
        box.append(Gtk.Label(label=T["text"], wrap=True, justify=Gtk.Justification.CENTER))
        box.append(_qr_picture(300))
        scan = Gtk.Label(label=T["scan"], wrap=True, justify=Gtk.Justification.CENTER)
        scan.add_css_class("dim-label")
        scan.add_css_class("caption")
        box.append(scan)

        acc = Gtk.Box(spacing=10, halign=Gtk.Align.CENTER)
        acc_label = Gtk.Label(label=T["account"].format(**INFO), selectable=True, wrap=True)
        acc_label.add_css_class("heading")
        acc.append(acc_label)
        acc.append(_copy_button(INFO["account"], T["copy_tip"]))
        box.append(acc)

        foot = Gtk.Box(spacing=12, margin_top=6)
        thanks = Gtk.Label(label=T["thanks"], hexpand=True, xalign=0)
        thanks.add_css_class("dim-label")
        thanks.add_css_class("dk-thanks")
        foot.append(thanks)
        close = Gtk.Button(label=T["close"])
        close.add_css_class("dk-primary")
        close.connect("clicked", lambda *_: self.close())
        foot.append(close)
        box.append(foot)

        tv.set_content(box)
        self.set_child(tv)
        self.set_default_widget(close)
        self.set_focus(close)


class AboutDialog(Adw.Dialog):
    """Hộp thoại Giới thiệu: thông tin phần mềm + Thông tin bản quyền + Buy me a coffee."""

    def __init__(self, parent: Gtk.Widget):
        super().__init__(title="Giới thiệu", content_width=560)
        self._parent = parent
        _track_dark(self)
        tv = Adw.ToolbarView()
        tv.add_top_bar(Adw.HeaderBar())
        sc = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, propagate_natural_height=True,
                                max_content_height=720)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin_start=24, margin_end=24,
                      margin_bottom=20)
        sc.set_child(box)

        icon = Gtk.Image.new_from_icon_name(APP_ID)
        icon.set_pixel_size(96)
        if not Gtk.IconTheme.get_for_display(Gdk.Display.get_default()).has_icon(APP_ID):
            icon.set_from_icon_name("system-software-install")
        box.append(icon)
        name = Gtk.Label(label=APP_NAME)
        name.add_css_class("title-1")
        box.append(name)
        ver = Gtk.Label(label=f"Phiên bản {VERSION}")
        ver.add_css_class("dim-label")
        box.append(ver)
        desc = Gtk.Label(wrap=True, justify=Gtk.Justification.CENTER, label=(
            "Thống kê phần mềm đã cài trên Ubuntu theo phương thức cài (APT, file .deb, PPA, Snap, "
            "Flatpak, AppImage, pip, npm, cài thủ công) và gỡ phần mềm không cần thiết."))
        box.append(desc)

        box.append(self._license_block())
        box.append(self._coffee_block())

        rights = Gtk.Label(label=T["rights"].format(year=COPYRIGHT_YEAR, owner=INFO["owner"]), xalign=0)
        rights.add_css_class("dim-label")
        rights.add_css_class("caption")
        box.append(rights)

        tv.set_content(sc)
        self.set_child(tv)

    def _license_block(self) -> Gtk.Widget:
        b = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        b.add_css_class("dk-box")
        title = Gtk.Label(label=T["license"], xalign=0)
        title.add_css_class("heading")
        b.append(title)

        row1 = Gtk.Box(spacing=10)
        row1.append(Gtk.Image.new_from_icon_name("emblem-documents-symbolic"))
        lbl = Gtk.Label(xalign=0, hexpand=True, use_markup=True,
                        label=f"{GLib.markup_escape_text(T['copyright'])}<b>{GLib.markup_escape_text(INFO['owner'])}</b>")
        row1.append(lbl)
        b.append(row1)

        row2 = Gtk.Box(spacing=10)
        row2.append(Gtk.Image.new_from_icon_name("mail-unread-symbolic"))
        subject = urllib.parse.quote(f"{APP_NAME} {VERSION}")
        mail = Gtk.Label(xalign=0, hexpand=True, use_markup=True, label=(
            f"{GLib.markup_escape_text(T['contact'])}"
            f"<a href=\"mailto:{INFO['email']}?subject={subject}\">{INFO['email']}</a>"))
        row2.append(mail)
        row2.append(_copy_button(INFO["email"], T["copy_mail"]))
        b.append(row2)
        return b

    def _coffee_block(self) -> Gtk.Widget:
        b = Gtk.Box(spacing=16)
        b.add_css_class("dk-box")
        qr_btn = Gtk.Button(child=_qr_picture(92), tooltip_text=T["enlarge"], valign=Gtk.Align.CENTER)
        qr_btn.add_css_class("flat")
        qr_btn.connect("clicked", self._open_donate)
        b.append(qr_btn)
        v = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, valign=Gtk.Align.CENTER)
        h = Gtk.Label(label=T["heading"], xalign=0)
        h.add_css_class("dk-heading")
        v.append(h)
        v.append(Gtk.Label(label=T["text"], xalign=0, wrap=True))
        btn_box = Gtk.Box(spacing=7)
        btn_box.append(heart_image(16))
        btn_box.append(Gtk.Label(label=T["donate"]))
        btn = Gtk.Button(child=btn_box, halign=Gtk.Align.START, tooltip_text=T["tip"])
        btn.connect("clicked", self._open_donate)
        v.append(btn)
        b.append(v)
        return b

    def _open_donate(self, *_):
        DonateDialog().present(self._parent)
