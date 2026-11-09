"""Khung chi tiết phần mềm đang chọn + nút Gỡ cài đặt."""

from __future__ import annotations

from gi.repository import Adw, GLib, GObject, Gtk, Pango

from ..models import SoftwareItem, Source, human_size
from ..utils import display
from .common import badge, set_badge, set_item_icon


class DetailsPane(Gtk.Stack):
    __gsignals__ = {
        "remove-requested": (GObject.SignalFlags.RUN_FIRST, None, (object,)),
    }

    def __init__(self):
        super().__init__(transition_type=Gtk.StackTransitionType.CROSSFADE)
        self.set_size_request(340, -1)
        self.item: SoftwareItem | None = None

        empty = Adw.StatusPage(icon_name="view-list-symbolic", title="Chọn một phần mềm",
                               description="Chọn một dòng trong danh sách để xem chi tiết và gỡ cài đặt.")
        empty.add_css_class("compact")
        self.add_named(empty, "empty")

        scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        clamp = Adw.Clamp(maximum_size=520, margin_start=16, margin_end=16, margin_top=18, margin_bottom=18)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        clamp.set_child(box)
        scroller.set_child(clamp)
        self.add_named(scroller, "details")

        # Đầu trang
        self.icon = Gtk.Image(halign=Gtk.Align.CENTER)
        self.title = Gtk.Label(wrap=True, justify=Gtk.Justification.CENTER)
        self.title.add_css_class("title-2")
        self.subtitle = Gtk.Label(wrap=True, justify=Gtk.Justification.CENTER, selectable=True)
        self.subtitle.add_css_class("dim-label")
        self.badge = badge(Source.APT)
        self.badge.set_halign(Gtk.Align.CENTER)
        for w in (self.icon, self.title, self.subtitle, self.badge):
            box.append(w)

        # Hộp cảnh báo bảo vệ
        self.protected_box = Gtk.Box(spacing=10)
        self.protected_box.add_css_class("protected-box")
        self.protected_box.append(Gtk.Image.new_from_icon_name("changes-prevent-symbolic"))
        self.protected_label = Gtk.Label(wrap=True, xalign=0, hexpand=True)
        self.protected_box.append(self.protected_label)
        box.append(self.protected_box)

        # Nút gỡ
        self.remove_btn = Gtk.Button(label="Gỡ cài đặt…", halign=Gtk.Align.CENTER)
        self.remove_btn.add_css_class("destructive-action")
        self.remove_btn.add_css_class("pill")
        self.remove_btn.connect("clicked", lambda *_: self.item and self.emit("remove-requested", self.item))
        box.append(self.remove_btn)

        # Thuộc tính
        self.props_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.props_list.add_css_class("boxed-list")
        box.append(self.props_list)

        # Mô tả
        self.desc_title = Gtk.Label(label="Mô tả", xalign=0)
        self.desc_title.add_css_class("heading")
        self.desc = Gtk.Label(wrap=True, xalign=0, selectable=True, wrap_mode=Pango.WrapMode.WORD_CHAR)
        box.append(self.desc_title)
        box.append(self.desc)

    def _row(self, title: str, value: str):
        row = Adw.ActionRow(title=title, subtitle=GLib.markup_escape_text(display(value) or "—"),
                            subtitle_selectable=True)
        row.add_css_class("property")
        self.props_list.append(row)

    def show_item(self, item: SoftwareItem | None):
        self.item = item
        if item is None:
            self.set_visible_child_name("empty")
            return
        self.set_visible_child_name("details")
        set_item_icon(self.icon, item, 72)
        self.title.set_label(item.title)
        self.subtitle.set_label(item.name if item.name != item.title else "")
        self.subtitle.set_visible(item.name != item.title)
        set_badge(self.badge, item.source)

        self.protected_box.set_visible(bool(item.protected_reason))
        self.protected_label.set_label("Được bảo vệ: " + item.protected_reason if item.protected_reason else "")
        self.remove_btn.set_sensitive(not item.protected_reason)
        self.remove_btn.set_tooltip_text(item.protected_reason or "Xem trước và gỡ phần mềm này")

        self.props_list.remove_all()
        self._row("Phương thức cài", item.source.label)
        self._row("Phiên bản", item.version)
        self._row("Dung lượng", human_size(item.size) if item.size is not None
                  else "Không rõ (không đọc được hoặc quá lớn để đếm nhanh)" if item.source == Source.MANUAL
                  else "—")
        date_txt = item.install_date.strftime("%d/%m/%Y %H:%M") if item.install_date else ""
        if item.source in (Source.APT, Source.THIRD_PARTY, Source.DEB) and item.install_date \
                and not item.extra.get("date_from_log"):
            date_txt += " (ước tính — lần cập nhật gần nhất)"
        if item.source in (Source.APPIMAGE, Source.MANUAL, Source.PIP, Source.NPM) and item.install_date:
            date_txt += " (theo thời gian sửa file)"
        self._row("Ngày cài", date_txt)
        self._row("Nguồn kho", item.origin)
        if item.source in (Source.APT, Source.THIRD_PARTY, Source.DEB):
            self._row("Kiểu cài", "Người dùng tự cài" if item.manual else "Tự động (phụ thuộc của gói khác)")
            self._row("Section / Priority",
                      f"{item.extra.get('section', '')} / {item.extra.get('priority', '')}")
        if item.source == Source.SNAP:
            self._row("Loại snap", item.extra.get("snap_type", ""))
            if item.extra.get("base"):
                self._row("Base", item.extra["base"])
        if item.path:
            self._row("Đường dẫn", item.path)
        desks = item.extra.get("desktop_files") or []
        if desks:
            self._row("Lối tắt menu", "\n".join(desks))

        desc = item.description or item.summary
        self.desc.set_label(desc)
        self.desc.set_visible(bool(desc))
        self.desc_title.set_visible(bool(desc))
