"""Khu vực "Dọn dẹp" — cùng bố cục với khu vực Gỡ cài đặt: nhóm bên trái · bảng ở giữa · chi tiết bên phải."""

from __future__ import annotations

import os
import threading

from gi.repository import Adw, Gio, GLib, GObject, Gtk, Pango

from ..cleanup.scanners import GROUPS, IRREVERSIBLE, LEVELS, LOCKED, REVIEW, SAFE, CleanupItem, scan_all
from ..models import human_size
from ..utils import display
from .cleanup_dialog import level_badge, size_text, total_text
from .common import make_spinner

LEVEL_ORDER = {SAFE: 0, REVIEW: 1, IRREVERSIBLE: 2, LOCKED: 3}
LEVEL_ICON = {SAFE: "emblem-ok-symbolic", REVIEW: "dialog-warning-symbolic",
              IRREVERSIBLE: "dialog-error-symbolic", LOCKED: "changes-prevent-symbolic"}


def _cmp(a, b) -> int:
    return 0 if a == b else (-1 if a < b else 1)


class CleanupObject(GObject.Object):
    __gtype_name__ = "TTCleanupObject"
    checked = GObject.Property(type=bool, default=False)

    def __init__(self, item: CleanupItem, checked: bool | None = None):
        super().__init__()
        self.item = item
        self.checked = item.default_selected if checked is None else (checked and item.level != LOCKED)


class GroupRow(Gtk.ListBoxRow):
    def __init__(self, code: str, label: str, icon: str):
        super().__init__()
        self.code = code
        box = Gtk.Box(spacing=10, margin_top=6, margin_bottom=6, margin_start=6, margin_end=6)
        box.append(Gtk.Image.new_from_icon_name(icon))
        v = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)
        t = Gtk.Label(label=label, xalign=0, ellipsize=Pango.EllipsizeMode.END)
        self.sub = Gtk.Label(xalign=0)
        self.sub.add_css_class("dim-label")
        self.sub.add_css_class("caption")
        v.append(t)
        v.append(self.sub)
        box.append(v)
        self.count = Gtk.Label()
        self.count.add_css_class("dim-label")
        self.count.add_css_class("sidebar-count")
        box.append(self.count)
        self.set_child(box)

    def update(self, items: list[CleanupItem]):
        safe = [i for i in items if i.level == SAFE and i.size]
        other = [i for i in items if i.level in (REVIEW, IRREVERSIBLE) and i.size]
        self.count.set_label(str(len(items)) if items else "")
        parts = []
        if safe:
            parts.append("An toàn " + total_text(safe))
        if other:
            parts.append("Cần xem " + total_text(other))
        self.sub.set_label(" · ".join(parts) if parts else ("chỉ có mục bị khoá" if items else "—"))


class CleanupDetails(Gtk.Stack):
    __gsignals__ = {
        "toggle-requested": (GObject.SignalFlags.RUN_FIRST, None, (object,)),
        "clean-requested": (GObject.SignalFlags.RUN_FIRST, None, (object,)),
    }

    def __init__(self):
        super().__init__(transition_type=Gtk.StackTransitionType.CROSSFADE)
        self.set_size_request(340, -1)
        self.obj: CleanupObject | None = None
        empty = Adw.StatusPage(icon_name="edit-clear-all-symbolic", title="Chọn một mục",
                               description="Chọn một dòng để xem nó là gì, xoá đi thì sao, và dọn riêng mục đó.")
        empty.add_css_class("compact")
        self.add_named(empty, "empty")
        sc = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        clamp = Adw.Clamp(maximum_size=520, margin_start=16, margin_end=16, margin_top=18, margin_bottom=18)
        self.box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        clamp.set_child(self.box)
        sc.set_child(clamp)
        self.add_named(sc, "details")
        self.icon = Gtk.Image(pixel_size=56, halign=Gtk.Align.CENTER)
        self.title = Gtk.Label(wrap=True, justify=Gtk.Justification.CENTER)
        self.title.add_css_class("title-2")
        self.badge_box = Gtk.Box(halign=Gtk.Align.CENTER)
        self.size = Gtk.Label(halign=Gtk.Align.CENTER)
        self.size.add_css_class("title-3")
        for w in (self.icon, self.title, self.badge_box, self.size):
            self.box.append(w)
        self.note_box = Gtk.Box(spacing=10)
        self.note_icon = Gtk.Image(valign=Gtk.Align.START)
        self.note = Gtk.Label(wrap=True, xalign=0, hexpand=True, wrap_mode=Pango.WrapMode.WORD_CHAR)
        self.note_box.append(self.note_icon)
        self.note_box.append(self.note)
        self.box.append(self.note_box)
        btns = Gtk.Box(spacing=10, halign=Gtk.Align.CENTER)
        self.toggle_btn = Gtk.Button()
        self.toggle_btn.add_css_class("pill")
        self.toggle_btn.connect("clicked", lambda *_: self.obj and self.emit("toggle-requested", self.obj))
        self.clean_btn = Gtk.Button(label="Dọn mục này…")
        self.clean_btn.add_css_class("pill")
        self.clean_btn.add_css_class("destructive-action")
        self.clean_btn.connect("clicked", lambda *_: self.obj and self.emit("clean-requested", self.obj))
        btns.append(self.toggle_btn)
        btns.append(self.clean_btn)
        self.box.append(btns)
        self.props = Adw.PreferencesGroup()
        self.box.append(self.props)
        self.open_btn = Gtk.Button(label="Mở thư mục", halign=Gtk.Align.CENTER)
        self.open_btn.add_css_class("flat")
        self.open_btn.connect("clicked", self._open_folder)
        self.box.append(self.open_btn)
        self._rows: list[Gtk.Widget] = []

    def _row(self, title: str, value: str):
        r = Adw.ActionRow(title=title, subtitle=GLib.markup_escape_text(display(value) or "—"),
                          subtitle_selectable=True, subtitle_lines=0)
        r.add_css_class("property")
        self.props.add(r)
        self._rows.append(r)

    def show_obj(self, obj: CleanupObject | None):
        self.obj = obj
        if obj is None:
            self.set_visible_child_name("empty")
            return
        it = obj.item
        for r in self._rows:
            self.props.remove(r)
        self._rows.clear()
        icon = dict((g, ic) for g, _t, ic in GROUPS).get(it.group, "edit-clear-all-symbolic")
        self.icon.set_from_icon_name("user-trash-full-symbolic" if it.kind == "trash" else icon)
        self.title.set_label(it.title)
        child = self.badge_box.get_first_child()
        if child:
            self.badge_box.remove(child)
        self.badge_box.append(level_badge(it.level))
        self.size.set_label(size_text(it))
        self.note_icon.set_from_icon_name(LEVEL_ICON[it.level])
        for css in ("warning-box", "blocked-box", "locked-box", "safe-box"):
            self.note_box.remove_css_class(css)
        if it.level == LOCKED:
            self.note.set_label("Bị khoá: " + it.reason)
            self.note_box.add_css_class("locked-box")
        else:
            self.note.set_label(f"{LEVELS[it.level][0]}: {it.note}")
            self.note_box.add_css_class({SAFE: "safe-box", REVIEW: "warning-box",
                                         IRREVERSIBLE: "blocked-box"}[it.level])
        locked = it.level == LOCKED
        self.toggle_btn.set_visible(not locked)
        self.clean_btn.set_visible(not locked)
        self.toggle_btn.set_label("Bỏ chọn" if obj.checked else "Chọn để dọn")
        self._row("Nhóm", dict((g, t) for g, t, _i in GROUPS).get(it.group, it.group))
        if it.detail:
            self._row("Vị trí / chi tiết", it.detail)
        if it.modified:
            self._row("Sửa lần cuối", it.modified.strftime("%d/%m/%Y %H:%M"))
        how = {"delete": "Xoá vĩnh viễn (không vào Thùng rác)", "cmd": "Lệnh dọn của chính công cụ: "
               + "; ".join(" ".join(a) for a in it.argv), "root": "Cần mật khẩu quản trị (helper của công cụ)"}
        if not locked:
            self._row("Cách dọn", how.get(it.method, it.method))
        if it.paths and it.kind != "trash":
            self._row(f"Đường dẫn ({len(it.paths)})", "\n".join(it.paths[:12]) + ("\n…" if len(it.paths) > 12 else ""))
        target = self._folder()
        self.open_btn.set_visible(bool(target))
        self.open_btn.set_label("Mở Thùng rác" if it.kind == "trash" else "Mở thư mục")
        self.set_visible_child_name("details")

    def _folder(self) -> str:
        if not self.obj:
            return ""
        it = self.obj.item
        if it.kind == "trash":
            return "trash:///"
        cand = it.paths[0] if it.paths else ""
        if it.kind in ("node_modules", "venv", "build"):
            cand = os.path.dirname(cand)
        return cand if cand and os.path.isdir(cand) else ""

    def _open_folder(self, *_):
        target = self._folder()
        if not target:
            return
        uri = target if target.startswith("trash:") else Gio.File.new_for_path(target).get_uri()
        try:
            Gio.AppInfo.launch_default_for_uri(uri, None)
        except GLib.Error:
            pass


class CleanupView(Gtk.Box):
    """Khu vực Dọn dẹp. Phát 'summary-changed' (an toàn, cân nhắc: số byte) và 'run-requested' (list mục)."""

    __gsignals__ = {
        "summary-changed": (GObject.SignalFlags.RUN_FIRST, None, (object,)),
        "run-requested": (GObject.SignalFlags.RUN_FIRST, None, (object,)),
    }

    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.items: list[CleanupItem] = []
        self.errors: dict[str, str] = {}
        self.scanning = False
        self.scanned = False
        self.group = "all"
        self.query = ""
        self.split = Adw.NavigationSplitView(min_sidebar_width=230, max_sidebar_width=290, vexpand=True)
        self.append(self.split)
        self._build_sidebar()
        self._build_content()

    # ------------------------------------------------------------ thanh bên
    def _build_sidebar(self):
        tv = Adw.ToolbarView()
        hb = Adw.HeaderBar(show_end_title_buttons=False, show_start_title_buttons=False)
        hb.set_title_widget(Adw.WindowTitle(title="Nhóm dữ liệu", subtitle="có thể dọn"))
        tv.add_top_bar(hb)
        self.groups = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.groups.add_css_class("navigation-sidebar")
        self.group_rows: dict[str, GroupRow] = {"all": GroupRow("all", "Tất cả", "view-grid-symbolic")}
        self.groups.append(self.group_rows["all"])
        for g, t, icon in GROUPS:
            self.group_rows[g] = GroupRow(g, t, icon)
            self.groups.append(self.group_rows[g])
        self.groups.select_row(self.group_rows["all"])
        self.groups.connect("row-selected", self._on_group)
        self.groups.connect("row-activated", lambda *_: self.split.set_show_content(True))
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.append(Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER, child=self.groups))
        legend = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_start=12, margin_end=12,
                         margin_bottom=12, margin_top=6)
        lt = Gtk.Label(label="Mức an toàn", xalign=0)
        lt.add_css_class("caption-heading")
        legend.append(lt)
        for lvl in (SAFE, REVIEW, IRREVERSIBLE, LOCKED):
            row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            b = level_badge(lvl)
            b.set_halign(Gtk.Align.START)
            row.append(b)
            d = Gtk.Label(label=LEVELS[lvl][2], xalign=0, wrap=True)
            d.add_css_class("caption")
            d.add_css_class("dim-label")
            row.append(d)
            legend.append(row)
        box.append(legend)
        tv.set_content(box)
        self.split.set_sidebar(Adw.NavigationPage(title="Nhóm dữ liệu", child=tv))

    # ------------------------------------------------------------ nội dung
    def _build_content(self):
        tv = Adw.ToolbarView()
        hb = Adw.HeaderBar(show_end_title_buttons=False, show_start_title_buttons=False)
        self.search = Gtk.SearchEntry(placeholder_text="Tìm mục, dự án, đường dẫn…", hexpand=True)
        self.search.connect("search-changed", self._on_search)
        hb.set_title_widget(Adw.Clamp(maximum_size=420, child=self.search))
        self.rescan_btn = Gtk.Button(icon_name="view-refresh-symbolic", tooltip_text="Quét lại")
        self.rescan_btn.connect("clicked", lambda *_: self.scan())
        hb.pack_end(self.rescan_btn)
        tv.add_top_bar(hb)

        # Dải tóm tắt theo mức (màu + chữ)
        if hasattr(Adw, "WrapBox"):  # libadwaita ≥ 1.7: tự xuống dòng khi hẹp
            self.summary_box = Adw.WrapBox(child_spacing=8, line_spacing=4, margin_start=12, margin_end=12,
                                           margin_top=6, margin_bottom=6)
            self.summary_box.add_css_class("summary-bar")
            tv.add_top_bar(self.summary_box)
        else:
            self.summary_box = Gtk.Box(spacing=8, margin_start=12, margin_end=12, margin_top=6, margin_bottom=6)
            self.summary_box.add_css_class("summary-bar")
            tv.add_top_bar(Gtk.ScrolledWindow(child=self.summary_box, vscrollbar_policy=Gtk.PolicyType.NEVER,
                                              hscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
                                              propagate_natural_height=True))
        # Đang quét lại: báo rõ, khoá thao tác dọn
        self.scan_banner = Adw.Banner(title="Đang quét lại — danh sách sẽ cập nhật sau ít giây…", revealed=False)
        tv.add_top_bar(self.scan_banner)

        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        loading = Adw.StatusPage(title="Đang tìm dữ liệu có thể dọn…",
                                 description="Bộ nhớ đệm, bản snap cũ, gói .deb đã tải, node_modules, .venv… "
                                             "Chỉ đọc, chưa xoá gì.")
        sp = make_spinner(48)
        sp.set_halign(Gtk.Align.CENTER)
        loading.set_child(sp)
        self.stack.add_named(loading, "loading")

        self.store = Gio.ListStore(item_type=CleanupObject)
        self.filter = Gtk.CustomFilter.new(lambda o: self._matches(o.item))
        self.filter_model = Gtk.FilterListModel(model=self.store, filter=self.filter)
        self.sort_model = Gtk.SortListModel(model=self.filter_model)
        self.selection = Gtk.SingleSelection(model=self.sort_model, autoselect=False, can_unselect=True)
        self.selection.connect("notify::selected-item", self._on_selected)
        self.view = Gtk.ColumnView(model=self.selection, show_row_separators=False)
        self.view.add_css_class("data-table")
        self.view.connect("activate", lambda _v, pos: self._toggle(self.sort_model.get_item(pos)))
        self.sort_model.set_sorter(self.view.get_sorter())
        # Tổng bề rộng các cột cố định ~400 px + cột Tên co giãn: vừa chỗ kể cả khi khung chi tiết đang mở.
        # Vị trí / đường dẫn xem ở khung chi tiết bên phải (và khi rê chuột lên tên).
        self._add_col("Chọn", self._setup_check, self._bind_check, None, fixed=56, unbind=self._unbind_check)
        name_col = self._add_col("Tên", self._setup_name, self._bind_name,
                                 lambda it: it.title.casefold(), expand=True, fixed=240)
        self.level_col = self._add_col("Mức an toàn", self._setup_level, self._bind_level,
                                       lambda it: (LEVEL_ORDER[it.level], -(it.size or 0)), fixed=160)
        self._add_col("Dung lượng", self._setup_right, self._bind(lambda it: size_text(it)),
                      lambda it: it.size or 0, fixed=105)
        self._add_col("Ngày sửa", self._setup_label,
                      self._bind(lambda it: it.modified.strftime("%d/%m/%Y") if it.modified else "—"),
                      lambda it: it.modified.timestamp() if it.modified else 0, fixed=100)
        self.view.sort_by_column(self.level_col, Gtk.SortType.ASCENDING)
        _ = name_col
        list_sc = Gtk.ScrolledWindow(hexpand=True, vexpand=True, child=self.view)

        self.details = CleanupDetails()
        self.details.connect("toggle-requested", lambda _d, o: self._toggle(o))
        self.details.connect("clean-requested", lambda _d, o: self.emit("run-requested", [o.item]))
        self.details_split = Adw.OverlaySplitView(
            content=list_sc, sidebar=self.details, sidebar_position=Gtk.PackType.END,
            min_sidebar_width=330, max_sidebar_width=420, sidebar_width_fraction=0.3,
            show_sidebar=True, enable_show_gesture=False, enable_hide_gesture=True)
        self.stack.add_named(self.details_split, "list")
        self.empty = Adw.StatusPage(icon_name="emblem-ok-symbolic", title="Không có gì để dọn",
                                    description="Máy đang gọn gàng — hoặc thử đổi từ khoá / nhóm.")
        self.stack.add_named(self.empty, "empty")
        tv.set_content(self.stack)

        # Thanh hành động cuối
        bar = Gtk.Box(spacing=10, margin_start=12, margin_end=12, margin_top=8, margin_bottom=10)
        self.sel_label = Gtk.Label(xalign=0, hexpand=True)
        self.sel_label.add_css_class("heading")
        bar.append(self.sel_label)
        self.sel_label.set_ellipsize(Pango.EllipsizeMode.END)
        self.pick_safe = Gtk.Button(label="Chọn mọi mục An toàn", tooltip_text="Thêm mọi mục An toàn vào lựa chọn")
        self.pick_safe.connect("clicked", lambda *_: self._pick_safe())
        self.pick_none = Gtk.Button(label="Bỏ chọn tất cả")
        self.pick_none.connect("clicked", lambda *_: self._pick_none())
        # Bản thu gọn cho cửa sổ hẹp (chỉ biểu tượng)
        self.pick_safe_icon = Gtk.Button(icon_name="object-select-symbolic", visible=False,
                                         tooltip_text="Chọn mọi mục An toàn")
        self.pick_safe_icon.connect("clicked", lambda *_: self._pick_safe())
        self.pick_none_icon = Gtk.Button(icon_name="edit-clear-symbolic", visible=False, tooltip_text="Bỏ chọn tất cả")
        self.pick_none_icon.connect("clicked", lambda *_: self._pick_none())
        self.go_btn = Gtk.Button(label="Dọn các mục đã chọn…")
        self.go_btn.add_css_class("suggested-action")
        self.go_btn.add_css_class("pill")
        self.go_btn.connect("clicked", self._on_go)
        for w in (self.pick_safe, self.pick_none, self.pick_safe_icon, self.pick_none_icon, self.go_btn):
            bar.append(w)
        tv.add_bottom_bar(bar)
        self.split.set_content(Adw.NavigationPage(title="Dọn dẹp", child=tv))

    # ------------------------------------------------------------ cột
    def _add_col(self, title, setup, bind, key, expand=False, fixed=None, unbind=None):
        f = Gtk.SignalListItemFactory()
        f.connect("setup", setup)
        f.connect("bind", bind)
        if unbind:
            f.connect("unbind", unbind)
        col = Gtk.ColumnViewColumn(title=title, factory=f, resizable=bool(title), expand=expand)
        if fixed:
            col.set_fixed_width(fixed)
        if key:
            col.set_sorter(Gtk.CustomSorter.new(lambda a, b, _d: _cmp(key(a.item), key(b.item)), None))
        self.view.append_column(col)
        return col

    def _setup_check(self, _f, li):
        c = Gtk.CheckButton(halign=Gtk.Align.CENTER)
        c.connect("toggled", self._on_check_toggled, li)
        li.set_child(c)

    def _bind_check(self, _f, li):
        obj = li.get_item()
        c = li.get_child()

        def sync(*_):
            li._binding = True
            c.set_active(obj.checked)
            li._binding = False

        sync()
        li._hid = (obj, obj.connect("notify::checked", sync))  # nút chọn luôn khớp trạng thái thật
        c.set_sensitive(obj.item.level != LOCKED)
        c.set_tooltip_text(obj.item.reason if obj.item.level == LOCKED else "Chọn để dọn")
        c.update_property([Gtk.AccessibleProperty.LABEL], [f"Chọn để dọn: {obj.item.title}"])

    @staticmethod
    def _unbind_check(_f, li):
        hid = getattr(li, "_hid", None)
        if hid:
            hid[0].disconnect(hid[1])
            li._hid = None

    def _on_check_toggled(self, c, li):
        if getattr(li, "_binding", False):
            return
        obj = li.get_item()
        if obj is None or obj.item.level == LOCKED:
            return
        obj.checked = c.get_active()
        self._update_selection()
        if self.details.obj is obj:
            self.details.show_obj(obj)

    @staticmethod
    def _setup_name(_f, li):
        box = Gtk.Box(spacing=10, margin_top=4, margin_bottom=4)
        edge = Gtk.Box(width_request=4)
        img = Gtk.Image(pixel_size=24)
        v = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER)
        t = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END)
        t.add_css_class("heading")
        s = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END)
        s.add_css_class("dim-label")
        s.add_css_class("caption")
        v.append(t)
        v.append(s)
        box.append(edge)
        box.append(img)
        box.append(v)
        li.set_child(box)
        li._w = (edge, img, t, s)

    @staticmethod
    def _bind_name(_f, li):
        it: CleanupItem = li.get_item().item
        edge, img, t, s = li._w
        for lvl in LEVELS:
            edge.remove_css_class("lvl-bar-" + lvl)
        edge.add_css_class("lvl-bar-" + it.level)
        icon = dict((g, ic) for g, _t, ic in GROUPS).get(it.group, "edit-clear-all-symbolic")
        img.set_from_icon_name("user-trash-full-symbolic" if it.kind == "trash" else icon)
        t.set_label(it.title)
        t.set_tooltip_text(it.title + (f"\n{it.detail}" if it.detail else ""))
        text = ("Bị khoá: " + it.reason) if it.level == LOCKED else it.note
        s.set_label(text)
        s.set_tooltip_text(text)

    @staticmethod
    def _setup_level(_f, li):
        li.set_child(Gtk.Box(valign=Gtk.Align.CENTER))

    @staticmethod
    def _bind_level(_f, li):
        box = li.get_child()
        child = box.get_first_child()
        if child:
            box.remove(child)
        box.append(level_badge(li.get_item().item.level))

    @staticmethod
    def _setup_label(_f, li):
        li.set_child(Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END))

    @staticmethod
    def _setup_right(_f, li):
        lbl = Gtk.Label(xalign=1)
        lbl.add_css_class("numeric")
        li.set_child(lbl)

    @staticmethod
    def _bind(fn, tooltip=False):
        def bind(_f, li):
            text = fn(li.get_item().item)
            li.get_child().set_label(text)
            if tooltip:
                li.get_child().set_tooltip_text(text)
        return bind

    # ------------------------------------------------------------ dữ liệu
    def scan(self):
        if self.scanning:
            self._rescan_pending = True  # ví dụ vừa dọn xong khi lần quét ngầm đang chạy
            return
        self.scanning = True
        self._rescan_pending = False
        self.rescan_btn.set_sensitive(False)
        self.go_btn.set_sensitive(False)
        self.details.clean_btn.set_sensitive(False)
        self.scan_banner.set_revealed(self.scanned)
        if not self.scanned:
            self.stack.set_visible_child_name("loading")

        def work():
            try:
                items, errors = scan_all()
            except Exception as exc:
                items, errors = [], {"tất cả": f"{type(exc).__name__}: {exc}"}
            GLib.idle_add(self._on_scanned, items, errors)

        threading.Thread(target=work, daemon=True).start()

    def _on_scanned(self, items, errors):
        self.scanning = False
        first = not self.scanned
        self.scanned = True
        self.rescan_btn.set_sensitive(True)
        self.scan_banner.set_revealed(False)
        self.details.clean_btn.set_sensitive(True)
        prev = {} if first else {o.item.id: o.checked for o in self._objects()}
        self.errors = errors
        self.notices = [i for i in items if i.kind == "none"]
        self.items = [i for i in items if i.kind != "none"]
        self.store.splice(0, self.store.get_n_items(),
                          [CleanupObject(i, prev.get(i.id)) for i in self.items])
        for code, row in self.group_rows.items():
            its = [i for i in self.items if code == "all" or i.group == code]
            row.update(its)
            row.set_visible(code == "all" or bool(its))
        sel_row = self.groups.get_selected_row()
        if sel_row is not None and not sel_row.get_visible():
            self.groups.select_row(self.group_rows["all"])  # nhóm đang xem vừa trống
        self._refresh_view()
        self._update_summary()
        self._update_selection()
        self.details.show_obj(None)
        if self.details_split.get_collapsed():
            self.details_split.set_show_sidebar(False)  # không để khung chi tiết trống đè lên danh sách
        safe = sum(i.size or 0 for i in self.items if i.level == SAFE)
        review = sum(i.size or 0 for i in self.items if i.level in (REVIEW, IRREVERSIBLE))
        self.emit("summary-changed", (safe, review, bool(self.errors)))
        if getattr(self, "_rescan_pending", False):
            self.scan()
        return False

    def _update_summary(self):
        child = self.summary_box.get_first_child()
        while child:
            nxt = child.get_next_sibling()
            self.summary_box.remove(child)
            child = nxt
        if self.errors:
            names = dict((g, t) for g, t, _ in GROUPS)
            w = Gtk.Label(label="Không quét được: " + ", ".join(names.get(g, g) for g in self.errors), xalign=0)
            w.add_css_class("error")
            self.summary_box.append(w)
        for n in getattr(self, "notices", []):
            w = Gtk.Label(label="⚠ " + n.title, tooltip_text=n.reason, xalign=0, margin_end=10)
            w.add_css_class("warning")
            self.summary_box.append(w)
        for lvl in (SAFE, REVIEW, IRREVERSIBLE, LOCKED):
            its = [i for i in self.items if i.level == lvl]
            if not its:
                continue
            pair = Gtk.Box(spacing=6, margin_end=10)
            pair.append(level_badge(lvl))
            lbl = Gtk.Label(label=f"{len(its)} mục" if lvl == LOCKED else total_text(its))
            lbl.add_css_class("dim-label")
            pair.append(lbl)
            self.summary_box.append(pair)

    def _matches(self, it: CleanupItem) -> bool:
        if self.group != "all" and it.group != self.group:
            return False
        if self.query:
            hay = f"{it.title} {it.note} {it.detail} {it.reason} {' '.join(it.paths[:3])}".casefold()
            if not all(t in hay for t in self.query.split()):
                return False
        return True

    def _refresh_view(self):
        self.filter.changed(Gtk.FilterChange.DIFFERENT)
        has = self.filter_model.get_n_items() > 0
        if not has and self.errors and not self.items:
            self.empty.set_icon_name("dialog-error-symbolic")
            self.empty.set_title("Không quét được dữ liệu có thể dọn")
            self.empty.set_description("; ".join(self.errors.values())[:300] + " — bấm Quét lại (F5) để thử lại.")
        elif not has and (self.query or self.group != "all"):
            self.empty.set_icon_name("edit-find-symbolic")
            self.empty.set_title("Không có mục phù hợp")
            self.empty.set_description("Thử đổi từ khoá hoặc chọn nhóm “Tất cả”.")
        else:
            self.empty.set_icon_name("emblem-ok-symbolic")
            self.empty.set_title("Không có gì để dọn")
            self.empty.set_description("Máy đang gọn gàng.")
        if self.scanned:
            self.stack.set_visible_child_name("list" if has else "empty")

    def _on_group(self, _lb, row):
        if row is None:
            return
        self.group = row.code
        self._refresh_view()
        self._update_selection()

    def select_group(self, code: str):
        """Chọn nhóm ở thanh bên (dùng khi bấm "Mở ở tab Dọn dẹp" từ tab Dung lượng)."""
        row = self.group_rows.get(code) or self.group_rows["all"]
        if not row.get_visible():
            row = self.group_rows["all"]
        self.groups.select_row(row)
        self.split.set_show_content(True)

    def _on_search(self, entry):
        self.query = entry.get_text().strip().casefold()
        self._refresh_view()
        self._update_selection()

    def _on_selected(self, *_):
        obj = self.selection.get_selected_item()
        self.details.show_obj(obj)
        if obj is not None and self.details_split.get_collapsed():
            self.details_split.set_show_sidebar(True)

    # ------------------------------------------------------------ chọn & dọn
    def _objects(self) -> list[CleanupObject]:
        return [self.store.get_item(i) for i in range(self.store.get_n_items())]

    def _toggle(self, obj):
        if obj is None or obj.item.level == LOCKED:
            return
        obj.checked = not obj.checked
        self._update_selection()
        if self.details.obj is obj:
            self.details.show_obj(obj)

    def _pick_safe(self):
        """THÊM mọi mục An toàn vào lựa chọn (không bỏ chọn mục khác bạn đã tích)."""
        for o in self._objects():
            if o.item.level == SAFE:
                o.checked = True
        self._after_bulk()

    def _pick_none(self):
        for o in self._objects():
            o.checked = False
        self._after_bulk()

    def _after_bulk(self):
        self._update_selection()
        if self.details.obj:
            self.details.show_obj(self.details.obj)

    def selected_items(self) -> list[CleanupItem]:
        return [o.item for o in self._objects() if o.checked and o.item.level != LOCKED]

    def _update_selection(self):
        sel = self.selected_items()
        hidden = [i for i in sel if not self._matches(i)]
        txt = f"Đã chọn {len(sel)} mục · {total_text(sel)}" if sel else "Chưa chọn mục nào"
        if hidden:
            txt += f" (gồm {len(hidden)} mục ở nhóm khác)"
        self.sel_label.set_label(txt)
        self.sel_label.set_tooltip_text(txt)
        self.go_btn.set_sensitive(bool(sel) and not self.scanning)

    def _on_go(self, *_):
        sel = self.selected_items()
        if sel:
            self.emit("run-requested", sel)

    def safe_total(self) -> int:
        return sum(i.size or 0 for i in self.items if i.level == SAFE)
