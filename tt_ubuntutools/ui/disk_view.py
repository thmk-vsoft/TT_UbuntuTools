"""Khu vực "Dung lượng" — cùng bố cục 3 cột: lối tắt bên trái · bảng thư mục ở giữa · chi tiết bên phải.

Tính khi mở từng thư mục: danh sách hiện ngay, dung lượng thư mục con được tính dần ở luồng nền (nhớ trong phiên,
huỷ khi rời thư mục). Xoá = chuyển vào Thùng rác, chỉ trong thư mục nhà, theo diskusage.trash.can_trash."""

from __future__ import annotations

import os
import threading
from concurrent.futures import ThreadPoolExecutor

from gi.repository import Adw, Gio, GLib, GObject, Gtk, Pango

from .. import history
from ..cleanup import scanners as sc
from ..diskusage.hints import Hint, hint_for
from ..diskusage.scanner import DiskUsageCache, Entry, dedup_total, disk_usage, list_dir, measure_dir
from ..diskusage.trash import can_trash, xdg_dirs, xdg_user_dirs
from ..models import human_size
from ..utils import display, mount_points
from .cleanup_dialog import level_badge
from .common import make_spinner, make_toast

HOME = os.path.realpath(os.path.expanduser("~"))


def _tilde(p: str) -> str:
    return "~" + p[len(HOME):] if p == HOME or p.startswith(HOME + "/") else p


def _pct(p: float) -> str:
    return (f"{p:.1f}%" if p < 10 else f"{p:.0f}%").replace(".", ",")


def _count(n: int | None) -> str:
    if n is None:
        return "…"
    return f"{n / 1000:.0f}k" if n >= 10000 else (f"{n / 1000:.1f}k".replace(".", ",") if n >= 1000 else str(n))


def _num(n: int | None) -> str:
    return "…" if n is None else f"{n:,}".replace(",", ".")


class DiskObject(GObject.Object):
    __gtype_name__ = "TTDiskObject"
    rev = GObject.Property(type=int, default=0)  # tăng khi dung lượng / tỉ lệ đổi -> dòng tự cập nhật

    def __init__(self, entry: Entry, hint: Hint | None):
        super().__init__()
        self.entry = entry
        self.hint = hint
        self.percent = 0.0

    def touch(self):
        self.rev += 1

    # Khoá sắp xếp (đọc một lần cho mỗi dòng khi sắp xếp — so sánh chạy trong GTK, không gọi Python n·log n lần)
    @GObject.Property(type=GObject.TYPE_INT64, flags=GObject.ParamFlags.READABLE)
    def sizekey(self):
        e = self.entry
        return -1 if e.size is None else e.size

    @GObject.Property(type=str, flags=GObject.ParamFlags.READABLE)
    def namekey(self):
        return self.entry.name.casefold()

    @GObject.Property(type=GObject.TYPE_INT64, flags=GObject.ParamFlags.READABLE)
    def fileskey(self):
        e = self.entry
        return e.files if e.files is not None else (0 if not e.is_dir else -1)

    @GObject.Property(type=float, flags=GObject.ParamFlags.READABLE)
    def mtimekey(self):
        return self.entry.mtime.timestamp() if self.entry.mtime else 0.0


class DiskDetails(Gtk.Stack):
    __gsignals__ = {
        "open-requested": (GObject.SignalFlags.RUN_FIRST, None, (object,)),
        "trash-requested": (GObject.SignalFlags.RUN_FIRST, None, (object,)),
        "link-requested": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
    }

    def __init__(self):
        super().__init__(transition_type=Gtk.StackTransitionType.CROSSFADE)
        self.set_size_request(340, -1)
        self.obj: DiskObject | None = None
        empty = Adw.StatusPage(icon_name="drive-harddisk-symbolic", title="Chọn một mục",
                               description="Chọn một dòng để xem dung lượng, tỉ lệ và các thao tác. Nhấn đúp để "
                                           "vào bên trong thư mục.")
        empty.add_css_class("compact")
        self.add_named(empty, "empty")
        sc_ = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        clamp = Adw.Clamp(maximum_size=520, margin_start=16, margin_end=16, margin_top=18, margin_bottom=18)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        clamp.set_child(box)
        sc_.set_child(clamp)
        self.add_named(sc_, "details")
        self.icon = Gtk.Image(pixel_size=56, halign=Gtk.Align.CENTER)
        self.title = Gtk.Label(wrap=True, justify=Gtk.Justification.CENTER, wrap_mode=Pango.WrapMode.WORD_CHAR)
        self.title.add_css_class("title-2")
        self.size = Gtk.Label(halign=Gtk.Align.CENTER)
        self.size.add_css_class("title-3")
        self.pct = Gtk.Label(halign=Gtk.Align.CENTER)
        self.pct.add_css_class("dim-label")
        for w in (self.icon, self.title, self.size, self.pct):
            box.append(w)
        self.hint_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.hint_badge = Gtk.Box()
        self.hint_label = Gtk.Label(wrap=True, xalign=0, wrap_mode=Pango.WrapMode.WORD_CHAR)
        self.hint_box.append(self.hint_badge)
        self.hint_box.append(self.hint_label)
        box.append(self.hint_box)
        btns = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, column_spacing=8, row_spacing=8,
                           max_children_per_line=2, homogeneous=False, halign=Gtk.Align.CENTER)
        self.open_btn = Gtk.Button(label="Mở thư mục")
        self.open_btn.add_css_class("pill")
        self.open_btn.connect("clicked", lambda *_: self.obj and self.emit("open-requested", self.obj))
        self.link_btn = Gtk.Button(label="Mở ở tab Dọn dẹp")
        self.link_btn.add_css_class("pill")
        self.link_btn.add_css_class("suggested-action")
        self.link_btn.connect("clicked", lambda *_: self.obj and self.obj.hint and
                              self.emit("link-requested", self.obj.hint.link))
        self.trash_btn = Gtk.Button(label="Chuyển vào Thùng rác…")
        self.trash_btn.add_css_class("pill")
        self.trash_btn.add_css_class("destructive-action")
        self.trash_btn.connect("clicked", lambda *_: self.obj and self.emit("trash-requested", self.obj))
        for b in (self.open_btn, self.link_btn, self.trash_btn):
            btns.append(b)
        box.append(btns)
        self.blocked = Gtk.Label(wrap=True, xalign=0.5, justify=Gtk.Justification.CENTER)
        self.blocked.add_css_class("dim-label")
        self.blocked.add_css_class("caption")
        box.append(self.blocked)
        self.props = Adw.PreferencesGroup()
        box.append(self.props)
        more = Gtk.Box(spacing=8, halign=Gtk.Align.CENTER)
        files_btn = Gtk.Button(label="Mở trong Files")
        files_btn.add_css_class("flat")
        files_btn.connect("clicked", lambda *_: self._launch(self._target(False)))
        parent_btn = Gtk.Button(label="Mở thư mục chứa")
        parent_btn.add_css_class("flat")
        parent_btn.connect("clicked", lambda *_: self._launch(self._target(True)))
        more.append(files_btn)
        more.append(parent_btn)
        box.append(more)
        self._rows: list[Gtk.Widget] = []

    def _target(self, parent: bool) -> str:
        if not self.obj:
            return ""
        e = self.obj.entry
        return os.path.dirname(e.path) if parent or not e.is_dir else e.path

    @staticmethod
    def _launch(path: str):
        if not path:
            return
        try:
            Gio.AppInfo.launch_default_for_uri(Gio.File.new_for_path(path).get_uri(), None)
        except GLib.Error:
            pass

    def _row(self, title, value):
        r = Adw.ActionRow(title=title, subtitle=GLib.markup_escape_text(display(value) or "—"),
                          subtitle_selectable=True, subtitle_lines=0)
        r.add_css_class("property")
        self.props.add(r)
        self._rows.append(r)

    def show_obj(self, obj: DiskObject | None, trash_reason: str = ""):
        self.obj = obj
        if obj is None:
            self.set_visible_child_name("empty")
            return
        e = obj.entry
        for r in self._rows:
            self.props.remove(r)
        self._rows.clear()
        self.icon.set_from_icon_name("folder-symbolic" if e.is_dir else
                                     ("emblem-symbolic-link" if e.is_link else "text-x-generic-symbolic"))
        self.title.set_label(e.name)
        if e.other_device:
            self.size.set_label("ổ khác")
        else:
            self.size.set_label(human_size(e.size) if e.size is not None else "đang tính…")
        self.pct.set_label(f"{_pct(obj.percent)} thư mục đang xem" if e.size is not None else "")
        child = self.hint_badge.get_first_child()
        if child:
            self.hint_badge.remove(child)
        for css in ("safe-box", "warning-box", "blocked-box", "locked-box"):
            self.hint_box.remove_css_class(css)
        if obj.hint:
            self.hint_badge.append(level_badge(obj.hint.level))
            self.hint_label.set_label(obj.hint.note)
            self.hint_box.add_css_class({sc.SAFE: "safe-box", sc.REVIEW: "warning-box",
                                         sc.IRREVERSIBLE: "blocked-box"}.get(obj.hint.level, "locked-box"))
            self.hint_box.set_visible(True)
        else:
            self.hint_box.set_visible(False)
        self.open_btn.set_visible(e.is_dir and e.readable and not e.other_device)
        self.link_btn.set_visible(bool(obj.hint and obj.hint.link.startswith("cleanup")))
        self.trash_btn.set_sensitive(not trash_reason)
        self.blocked.set_label(("Không chuyển vào Thùng rác được: " + trash_reason) if trash_reason else "")
        self.blocked.set_visible(bool(trash_reason))
        self._row("Đường dẫn", e.path)
        kind = "Thư mục" if e.is_dir else ("Liên kết tượng trưng" if e.is_link else "File")
        if e.hidden:
            kind += " ẩn"
        self._row("Loại", kind)
        if e.is_dir and not e.other_device:
            self._row("Bên trong", f"{_num(e.files)} file · {_num(e.dirs)} thư mục")
        if e.errors:
            self._row("Không đọc được", f"{e.errors} mục (cần quyền khác) — không tính vào dung lượng")
        if e.mtime:
            self._row("Ngày sửa", e.mtime.strftime("%d/%m/%Y %H:%M"))
        self.set_visible_child_name("details")


class DiskView(Gtk.Box):
    """Khu vực Dung lượng. Phát 'link-requested' ("cleanup:<nhóm>" | "software")."""

    __gsignals__ = {
        "link-requested": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        "toast": (GObject.SignalFlags.RUN_FIRST, None, (object,)),
    }

    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.cache = DiskUsageCache()
        self.pool = ThreadPoolExecutor(max_workers=3, thread_name_prefix="tt-du")
        self.cancel = threading.Event()
        self.path = ""
        self.objects: list[DiskObject] = []
        self.pending = 0
        self.total_dirs = 0
        self.show_hidden = True
        self.query = ""
        self.installed_roots: dict[str, str] = {}
        self._gen = 0
        self._resort_id = 0
        self._listing = False
        self._programmatic = False
        self._hidden = self._unread = 0
        self._own_blocks = 0
        self.roots_state = "loading"  # "ok" | "loading" | lý do lỗi
        self._error = ""
        self._auto_top = True
        self.split = Adw.NavigationSplitView(min_sidebar_width=220, max_sidebar_width=280, vexpand=True)
        self.append(self.split)
        self._build_sidebar()
        self._build_content()
        self._roots_token = 0
        self.reload_installed_roots()

    # ------------------------------------------------------------ thanh bên
    def _build_sidebar(self):
        tv = Adw.ToolbarView()
        hb = Adw.HeaderBar(show_end_title_buttons=False, show_start_title_buttons=False)
        hb.set_title_widget(Adw.WindowTitle(title="Vị trí", subtitle="xem dung lượng"))
        tv.add_top_bar(hb)
        self.places = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.places.add_css_class("navigation-sidebar")
        self.place_rows: dict[str, Gtk.ListBoxRow] = {}
        places = [(HOME, "Thư mục nhà", "user-home-symbolic")]
        names = {"Desktop": ("Màn hình nền", "user-desktop-symbolic"), "Documents": ("Tài liệu", "folder-documents-symbolic"),
                 "Downloads": ("Tải về", "folder-download-symbolic")}
        xdg = xdg_dirs(HOME)
        for key, (label, icon) in names.items():
            raw = self._xdg_path(key) or os.path.join(HOME, key)
            cand = os.path.realpath(raw)  # open_path dùng đường dẫn thật -> hàng lối tắt phải khớp
            rel_ok = (os.path.relpath(raw, HOME) in xdg | {key} or os.path.relpath(cand, HOME) in xdg | {key}
                      or raw in xdg_user_dirs(HOME).values())
            if os.path.isdir(cand) and rel_ok:
                places.append((cand, label, icon))
        places.append(("/", "Toàn bộ ổ đĩa (/)", "drive-harddisk-symbolic"))
        for path, label, icon in places:
            row = Gtk.ListBoxRow()
            row.path = path
            box = Gtk.Box(spacing=10, margin_top=6, margin_bottom=6, margin_start=6, margin_end=6)
            box.append(Gtk.Image.new_from_icon_name(icon))
            v = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            v.append(Gtk.Label(label=label, xalign=0))
            sub = Gtk.Label(label=_tilde(path), xalign=0, ellipsize=Pango.EllipsizeMode.MIDDLE)
            sub.add_css_class("caption")
            sub.add_css_class("dim-label")
            v.append(sub)
            box.append(v)
            row.set_child(box)
            self.places.append(row)
            self.place_rows[path] = row
        self.places.connect("row-activated", lambda _lb, row: (self.open_path(row.path), self.split.set_show_content(True)))
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.append(Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER, child=self.places))
        pick = Gtk.Button(label="Chọn thư mục…", margin_start=12, margin_end=12, margin_bottom=8)
        pick.connect("clicked", self._pick_folder)
        box.append(pick)
        disk = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, margin_start=12, margin_end=12,
                       margin_bottom=12)
        t = Gtk.Label(label="Ổ đĩa chứa thư mục nhà", xalign=0)
        t.add_css_class("caption-heading")
        self.disk_bar = Gtk.LevelBar(min_value=0, max_value=1)
        self.disk_bar.remove_offset_value("low")
        self.disk_bar.remove_offset_value("high")
        self.disk_bar.remove_offset_value("full")
        self.disk_label = Gtk.Label(xalign=0, wrap=True)
        self.disk_label.add_css_class("caption")
        self.disk_label.add_css_class("dim-label")
        for w in (t, self.disk_bar, self.disk_label):
            disk.append(w)
        box.append(disk)
        tv.set_content(box)
        self.split.set_sidebar(Adw.NavigationPage(title="Vị trí", child=tv))
        self.update_disk()

    @staticmethod
    def _xdg_path(key: str) -> str:
        name = {"Desktop": "DESKTOP", "Documents": "DOCUMENTS", "Downloads": "DOWNLOAD"}[key]
        return xdg_user_dirs(HOME).get(name, "")

    def update_disk(self) -> str:
        du = disk_usage(HOME)
        if not du:
            self.disk_label.set_label("Không đọc được dung lượng ổ đĩa")
            return ""
        total, used, free = du
        self.disk_bar.set_value(used / total if total else 0)
        self.disk_label.set_label(f"Đã dùng {human_size(used)} / {human_size(total)} · còn trống {human_size(free)}")
        return f"còn {human_size(free)}"

    def _pick_folder(self, *_):
        dlg = Gtk.FileDialog(title="Chọn thư mục để xem dung lượng", modal=True)
        dlg.set_initial_folder(Gio.File.new_for_path(self.path or HOME))

        def done(d, res):
            try:
                f = d.select_folder_finish(res)
            except GLib.Error:
                return
            if f and f.get_path():
                self.open_path(f.get_path())
                self.split.set_show_content(True)

        dlg.select_folder(self.get_root(), None, done)

    def _load_installed_roots(self, token: int = 0):
        roots: dict[str, str] = {}
        state = "ok"
        try:
            from ..collectors.home_apps import find_home_apps
            for a in find_home_apps(home=HOME):
                # cả trạng thái "ambiguous" (nhiều lối tắt dùng chung thư mục) — tab Gỡ cài đặt còn từ chối gỡ
                if a.state in ("ok", "standalone", "ambiguous") and a.root and os.path.isdir(a.root):
                    roots[a.root] = a.entries[0].title if a.entries else os.path.basename(a.root)
        except Exception as exc:  # không biết chương trình nào đã cài -> khoá việc xoá (fail-closed)
            state = f"không kiểm tra được chương trình đã cài ({type(exc).__name__})"
        GLib.idle_add(self._set_installed_roots, roots, state, token)

    def _set_installed_roots(self, roots, state="ok", token=0):
        if token != self._roots_token:  # đã có lần đọc mới hơn — bỏ kết quả cũ
            return False
        self.installed_roots = roots
        self.roots_state = state
        for o in self.objects:
            o.hint = hint_for(o.entry.path, o.entry.is_dir, roots, HOME)
            o.touch()
        if self.details.obj is not None:
            self._show_details(self.details.obj)
        return False

    # ------------------------------------------------------------ nội dung
    def _build_content(self):
        tv = Adw.ToolbarView()
        hb = Adw.HeaderBar(show_end_title_buttons=False, show_start_title_buttons=False)
        self.up_btn = Gtk.Button(icon_name="go-up-symbolic", tooltip_text="Lên thư mục cha (Backspace)")
        self.up_btn.connect("clicked", lambda *_: self.go_up())
        hb.pack_start(self.up_btn)
        self.search = Gtk.SearchEntry(placeholder_text="Lọc theo tên trong thư mục này…", hexpand=True)
        self.search.connect("search-changed", self._on_search)
        hb.set_title_widget(Adw.Clamp(maximum_size=380, child=self.search))
        self.refresh_btn = Gtk.Button(icon_name="view-refresh-symbolic", tooltip_text="Quét lại thư mục này")
        self.refresh_btn.connect("clicked", lambda *_: self.rescan())
        hb.pack_end(self.refresh_btn)
        self.hidden_btn = Gtk.ToggleButton(icon_name="view-reveal-symbolic", active=True,
                                           tooltip_text="Hiện mục ẩn (bắt đầu bằng dấu chấm)")
        self.hidden_btn.connect("toggled", self._on_hidden)
        hb.pack_end(self.hidden_btn)
        tv.add_top_bar(hb)

        # Thanh đường dẫn dạng nút
        crumb_sc = Gtk.ScrolledWindow(vscrollbar_policy=Gtk.PolicyType.NEVER, hscrollbar_policy=Gtk.PolicyType.EXTERNAL,
                                      propagate_natural_height=True)
        self.crumbs = Gtk.Box(spacing=2, margin_start=10, margin_end=10, margin_top=4)
        self.crumbs.add_css_class("crumb-bar")
        crumb_sc.set_child(self.crumbs)
        tv.add_top_bar(crumb_sc)
        self.crumb_sc = crumb_sc
        # Tóm tắt thư mục đang xem
        sumbox = Gtk.Box(spacing=10, margin_start=12, margin_end=12, margin_top=4, margin_bottom=6)
        sumbox.add_css_class("summary-bar")
        self.summary = Gtk.Label(xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.END)
        self.summary.add_css_class("dim-label")
        self.spinner = make_spinner(16)
        self.spinner.set_visible(False)
        self.share_bar = Gtk.LevelBar(min_value=0, max_value=1, valign=Gtk.Align.CENTER, width_request=120,
                                      tooltip_text="Thư mục này chiếm bao nhiêu % ổ đĩa")
        for off in ("low", "high", "full"):
            self.share_bar.remove_offset_value(off)
        self.share_label = Gtk.Label()
        self.share_label.add_css_class("caption")
        self.share_label.add_css_class("dim-label")
        for w in (self.spinner, self.summary, self.share_bar, self.share_label):
            sumbox.append(w)
        tv.add_top_bar(sumbox)

        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        self.store = Gio.ListStore(item_type=DiskObject)
        self.filter = Gtk.CustomFilter.new(lambda o: self._matches(o))
        self.filter_model = Gtk.FilterListModel(model=self.store, filter=self.filter)
        self.sort_model = Gtk.SortListModel(model=self.filter_model, incremental=True)
        self.selection = Gtk.SingleSelection(model=self.sort_model, autoselect=False, can_unselect=True)
        self.selection.connect("notify::selected-item", self._on_selected)
        self.view = Gtk.ColumnView(model=self.selection, show_row_separators=False)
        self.view.add_css_class("data-table")
        self.view.connect("activate", lambda _v, pos: self._activate(self.sort_model.get_item(pos)))
        self.sort_model.set_sorter(self.view.get_sorter())
        self._add_col("Tên", self._setup_name, self._bind_name, self._unbind, "namekey", expand=True, fixed=260)
        self.size_col = self._add_col("Tỉ lệ", self._setup_bar, self._bind_bar, self._unbind, "sizekey", fixed=170)
        self._add_col("Dung lượng", self._setup_right, self._bind_size, self._unbind, "sizekey", fixed=105)
        self._add_col("Số file", self._setup_right, self._bind_files, self._unbind, "fileskey", fixed=80)
        self._add_col("Ngày sửa", self._setup_label, self._bind_date, self._unbind, "mtimekey", fixed=100)
        self.view.sort_by_column(self.size_col, Gtk.SortType.DESCENDING)
        key = Gtk.EventControllerKey()
        key.connect("key-pressed", self._on_key)
        self.view.add_controller(key)
        list_sc = Gtk.ScrolledWindow(hexpand=True, vexpand=True, child=self.view)
        # Khi đang tính dần, dòng sắp xếp lại liên tục: giữ ở đầu bảng cho tới khi người dùng tự cuộn / chọn.
        self._auto_top = True
        scroll = Gtk.EventControllerScroll(flags=Gtk.EventControllerScrollFlags.VERTICAL)
        scroll.connect("scroll", lambda *_: setattr(self, "_auto_top", False))
        list_sc.add_controller(scroll)

        self.details = DiskDetails()
        self.details.connect("open-requested", lambda _d, o: self._activate(o))
        self.details.connect("trash-requested", lambda _d, o: self._confirm_trash(o))
        self.details.connect("link-requested", lambda _d, link: self.emit("link-requested", link))
        self.details_split = Adw.OverlaySplitView(
            content=list_sc, sidebar=self.details, sidebar_position=Gtk.PackType.END,
            min_sidebar_width=330, max_sidebar_width=420, sidebar_width_fraction=0.3,
            show_sidebar=True, enable_show_gesture=False, enable_hide_gesture=True)
        self.stack.add_named(self.details_split, "list")
        self.empty = Adw.StatusPage(icon_name="folder-symbolic", title="Thư mục trống")
        self.stack.add_named(self.empty, "empty")
        loading = Adw.StatusPage(title="Đang đọc thư mục…")
        sp = make_spinner(32)
        sp.set_halign(Gtk.Align.CENTER)
        loading.set_child(sp)
        self.stack.add_named(loading, "loading")
        tv.set_content(self.stack)
        self.split.set_content(Adw.NavigationPage(title="Dung lượng", child=tv))

    def _add_col(self, title, setup, bind, unbind, key, expand=False, fixed=None):
        f = Gtk.SignalListItemFactory()
        f.connect("setup", setup)
        f.connect("bind", bind)
        f.connect("unbind", unbind)
        col = Gtk.ColumnViewColumn(title=title, factory=f, resizable=True, expand=expand)
        if fixed:
            col.set_fixed_width(fixed)
        expr = Gtk.PropertyExpression.new(DiskObject, None, key)
        col.set_sorter(Gtk.StringSorter(expression=expr, ignore_case=True) if key == "namekey"
                       else Gtk.NumericSorter(expression=expr))
        self.view.append_column(col)
        return col

    # Mỗi ô nghe notify::rev của đối tượng để tự cập nhật khi dung lượng / tỉ lệ thay đổi.
    @staticmethod
    def _watch(li, fn):
        obj = li.get_item()
        fn(obj)
        li._hid = (obj, obj.connect("notify::rev", lambda o, _p: fn(o)))

    @staticmethod
    def _unbind(_f, li):
        hid = getattr(li, "_hid", None)
        if hid:
            hid[0].disconnect(hid[1])
            li._hid = None

    @staticmethod
    def _setup_name(_f, li):
        box = Gtk.Box(spacing=10, margin_top=3, margin_bottom=3)
        img = Gtk.Image(pixel_size=22)
        t = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.MIDDLE, hexpand=True)
        badge_box = Gtk.Box()
        box.append(img)
        box.append(t)
        box.append(badge_box)
        li.set_child(box)
        li._w = (img, t, badge_box)

    def _bind_name(self, _f, li):
        img, t, badge_box = li._w

        def upd(o: DiskObject):
            e = o.entry
            img.set_from_icon_name("folder-symbolic" if e.is_dir else
                                   ("emblem-symbolic-link" if e.is_link else "text-x-generic-symbolic"))
            t.set_label(e.name)
            t.set_tooltip_text(e.path)
            if e.hidden:
                t.add_css_class("dim-label")
            else:
                t.remove_css_class("dim-label")
            child = badge_box.get_first_child()
            if child:
                badge_box.remove(child)
            if o.hint:
                b = level_badge(o.hint.level)
                b.set_tooltip_text(o.hint.note)
                badge_box.append(b)
        self._watch(li, upd)

    @staticmethod
    def _setup_bar(_f, li):
        box = Gtk.Box(spacing=8)
        bar = Gtk.ProgressBar(valign=Gtk.Align.CENTER, hexpand=True, width_request=40)
        bar.add_css_class("du-bar")
        lbl = Gtk.Label(width_chars=6, xalign=1)
        lbl.add_css_class("numeric")
        lbl.add_css_class("caption")
        box.append(bar)
        box.append(lbl)
        li.set_child(box)
        li._w = (bar, lbl)

    def _bind_bar(self, _f, li):
        bar, lbl = li._w

        def upd(o: DiskObject):
            e = o.entry
            if e.other_device:
                bar.set_fraction(0)
                lbl.set_label("ổ khác")
            elif e.size is None:
                bar.pulse()
                lbl.set_label("…")
            else:
                bar.set_fraction(min(1.0, o.percent / 100))
                lbl.set_label(_pct(o.percent))
        self._watch(li, upd)

    @staticmethod
    def _setup_right(_f, li):
        lbl = Gtk.Label(xalign=1)
        lbl.add_css_class("numeric")
        li.set_child(lbl)

    @staticmethod
    def _setup_label(_f, li):
        li.set_child(Gtk.Label(xalign=0))

    def _bind_size(self, _f, li):
        lbl = li.get_child()

        def upd(o):
            e = o.entry
            lbl.set_label("—" if e.other_device else (human_size(e.size) if e.size is not None else "đang tính…"))
        self._watch(li, upd)

    def _bind_files(self, _f, li):
        lbl = li.get_child()
        self._watch(li, lambda o: lbl.set_label(_count(o.entry.files) if o.entry.is_dir and not o.entry.other_device
                                                  else "—"))

    def _bind_date(self, _f, li):
        lbl = li.get_child()
        self._watch(li, lambda o: lbl.set_label(o.entry.mtime.strftime("%d/%m/%Y") if o.entry.mtime else "—"))

    # ------------------------------------------------------------ duyệt
    def open_path(self, path: str, select: str = ""):
        """Mở thư mục: đọc danh sách ở luồng nền (không làm đứng giao diện), rồi tính dần dung lượng thư mục con.
        select: đường dẫn mục cần chọn sẵn sau khi đọc xong (vd. khi đi lên thư mục cha)."""
        path = os.path.realpath(path)  # đường dẫn thật: khớp đúng điểm gắn và khoá bộ nhớ đệm
        self.cancel.set()                       # huỷ việc đọc / tính của thư mục trước
        if self._resort_id:
            GLib.source_remove(self._resort_id)
            self._resort_id = 0
        self.cancel = threading.Event()
        self._gen += 1
        gen = self._gen
        self.path = path
        self.split.set_show_content(True)  # cửa sổ hẹp: chuyển sang trang bảng
        self.search.set_text("")
        self.query = ""
        self._auto_top = True
        self._build_crumbs()
        self.up_btn.set_sensitive(path != "/")
        for p, row in self.place_rows.items():
            if p == path:
                self.places.select_row(row)
                break
        else:
            self.places.unselect_all()
        self.details.show_obj(None)
        if self.details_split.get_collapsed():
            self.details_split.set_show_sidebar(False)
        # Bỏ ngay danh sách cũ: không còn chọn / xoá nhầm mục của thư mục trước trong lúc đang đọc.
        self.objects = []
        self._listing = True
        self.store.remove_all()
        self.pending = self.total_dirs = 0
        self.spinner.set_visible(True)
        self.summary.set_label("Đang đọc thư mục…")
        # Chỉ hiện trang "Đang đọc" nếu đọc lâu (tránh nháy màn hình với thư mục nhỏ)
        GLib.timeout_add(250, lambda: (gen == self._gen and self._listing and
                                       self.stack.set_visible_child_name("loading"), False)[1])
        roots = dict(self.installed_roots)
        cancel = self.cancel

        def work():
            own = 0
            try:
                try:
                    own = os.lstat(path).st_blocks * 512  # khối của chính thư mục (như du -s)
                except OSError:
                    pass
                mounts = set(mount_points())
                entries, err = list_dir(path, mounts)
                objs = []
                for e in entries:
                    if cancel.is_set():
                        return
                    if e.is_dir and not e.complete:
                        m = self.cache.get(e.path)
                        if m is not None:
                            self._apply(e, m)
                    objs.append((e, hint_for(e.path, e.is_dir, roots, HOME)))
            except Exception as exc:  # không bao giờ để giao diện kẹt ở "đang đọc"
                entries, objs, err = [], [], f"{type(exc).__name__}: {exc}"
                mounts = set()
            GLib.idle_add(self._on_listed, gen, objs, err, select, mounts, roots, own)

        self.pool.submit(work)

    @staticmethod
    def _apply(e: Entry, m):
        e.size, e.files, e.dirs, e.errors, e.links, e.complete = m.size, m.files, m.dirs, m.errors, m.links, True

    def _on_listed(self, gen, objs, err, select, mounts, roots=None, own=0):
        if gen != self._gen:
            return False
        self._listing = False
        self._own_blocks = own
        if roots is not None and roots != self.installed_roots:  # danh sách chương trình đã cài vừa về
            objs = [(e, hint_for(e.path, e.is_dir, self.installed_roots, HOME)) for e, _h in objs]
        self.objects = [DiskObject(e, h) for e, h in objs]
        self._hidden = sum(1 for o in self.objects if o.entry.hidden)
        todo = [o for o in self.objects if o.entry.is_dir and not o.entry.complete]
        self.total_dirs = len(todo)
        self.pending = len(todo)
        self._error = err
        self._recompute(notify=False)  # tỉ lệ tính trước khi đưa vào bảng: dòng mới đọc thẳng giá trị đúng
        self.store.splice(0, self.store.get_n_items(), self._presorted(self.objects))
        self._refresh_view()
        if select:
            for i in range(self.sort_model.get_n_items()):
                if self.sort_model.get_item(i).entry.path == select:
                    self._programmatic = True
                    try:
                        self.selection.set_selected(i)
                    finally:
                        self._programmatic = False
                    self._auto_top = False
                    self.view.scroll_to(i, None, Gtk.ListScrollFlags.NONE, None)
                    break
        self._scroll_top()
        cancel = self.cancel
        for o in todo:
            self.pool.submit(self._measure_job, o, cancel, gen, mounts)
        return False

    def _presorted(self, objs: list[DiskObject]) -> list[DiskObject]:
        """Sắp sẵn theo cột đang chọn: GTK dùng timsort nên dữ liệu đã có thứ tự được sắp gần như tức thì
        (thư mục 30.000 mục không làm đứng giao diện)."""
        sorter = self.view.get_sorter()
        col = sorter.get_primary_sort_column() if sorter else None
        if col is None:
            return objs
        key = {"Tên": lambda o: o.entry.name.casefold(), "Tỉ lệ": lambda o: o.sizekey,
               "Dung lượng": lambda o: o.sizekey, "Số file": lambda o: o.fileskey,
               "Ngày sửa": lambda o: o.mtimekey}.get(col.get_title())
        if key is None:
            return objs
        desc = sorter.get_primary_sort_order() == Gtk.SortType.DESCENDING
        return sorted(objs, key=key, reverse=desc)

    def _measure_job(self, obj: DiskObject, cancel: threading.Event, gen: int, mounts=None):
        if cancel.is_set():
            return
        try:
            m = measure_dir(obj.entry.path, cancel, mounts=mounts)
        except Exception:  # luôn báo xong để dòng không kẹt ở "đang tính"
            from ..diskusage.scanner import Measure
            m = Measure(errors=1)
        if m.cancelled or cancel.is_set():
            return
        self.cache.put(obj.entry.path, m)
        GLib.idle_add(self._on_measured, obj, m, gen)

    def _on_measured(self, obj: DiskObject, m, gen: int):
        if gen != self._gen:
            return False
        self._apply(obj.entry, m)
        self.pending -= 1
        obj.touch()
        if not self._resort_id:  # tính lại tỉ lệ, tổng và sắp xếp theo đợt (tránh nhảy dòng / tốn CPU liên tục)
            self._resort_id = GLib.timeout_add(400 if self.pending > 0 else 50, self._resort)
        if self.details.obj is obj:
            self._show_details(obj)
        return False

    def _resort(self):
        self._resort_id = 0
        if self._listing:
            return False
        self._recompute()
        if self.details.obj is not None:  # tỉ lệ của mục đang xem chi tiết có thể vừa đổi
            self._show_details(self.details.obj)
        sorter = self.view.get_sorter()
        if sorter:
            sorter.changed(Gtk.SorterChange.DIFFERENT)
        self._scroll_top()
        return False

    def _scroll_top(self):
        if self._auto_top and self.sort_model.get_n_items():
            self.view.scroll_to(0, None, Gtk.ListScrollFlags.NONE, None)

    def _total(self) -> int:
        rows = [(o.entry.size, o.entry.links) for o in self.objects if not o.entry.other_device]
        return dedup_total([(self._own_blocks, None)] + rows)

    def _recompute(self, notify: bool = True):
        """Tính lại tỉ lệ; chỉ báo cập nhật cho dòng có tỉ lệ thay đổi đáng kể."""
        total = self._total()
        for o in self.objects:
            new = min(100.0, (o.entry.size or 0) * 100.0 / total) if total else 0.0
            if notify and abs(new - o.percent) > 0.05:
                o.percent = new
                o.touch()
            else:
                o.percent = new
        self._unread = sum(1 for o in self.objects if o.entry.errors)
        self._update_summary(total)

    def _update_summary(self, total: int):
        txt = f"Thư mục này: {human_size(total)} · {len(self.objects)} mục"
        if self._hidden:
            txt += f" ({self._hidden} ẩn)"
        if self._unread:
            txt += f" · {self._unread} mục có phần không đọc được"
        if self.pending > 0:
            txt += f" · đang tính {self.total_dirs - self.pending}/{self.total_dirs} thư mục…"
        if self._error:
            txt = f"Không đọc được thư mục: {self._error}"
        self.summary.set_label(txt)
        self.spinner.set_visible(self.pending > 0)
        du = disk_usage(self.path or "/")
        if du and du[0]:
            share = total / du[0]
            self.share_bar.set_value(min(1.0, share))
            self.share_label.set_label(f"{_pct(share * 100)} ổ đĩa")

    def _build_crumbs(self):
        child = self.crumbs.get_first_child()
        while child:
            nxt = child.get_next_sibling()
            self.crumbs.remove(child)
            child = nxt
        if self.path == HOME or self.path.startswith(HOME + "/"):
            parts = [("~", HOME)]
            cur = HOME
            for p in os.path.relpath(self.path, HOME).split("/"):
                if p in (".", ""):
                    continue
                cur = os.path.join(cur, p)
                parts.append((p, cur))
        else:
            parts = [("/", "/")]
            cur = "/"
            for p in self.path.strip("/").split("/"):
                if not p:
                    continue
                cur = os.path.join(cur, p)
                parts.append((p, cur))
        for i, (label, target) in enumerate(parts):
            if i:
                sep = Gtk.Label(label="›")
                sep.add_css_class("dim-label")
                self.crumbs.append(sep)
            b = Gtk.Button(label=label)
            b.add_css_class("flat")
            if i == len(parts) - 1:
                b.add_css_class("heading")
            b.connect("clicked", lambda _b, t=target: self.open_path(t))
            self.crumbs.append(b)

    def go_up(self):
        if self.path and self.path != "/":
            self.open_path(os.path.dirname(self.path), select=self.path)

    def rescan(self):
        if not self.path:
            self.open_path(HOME)
            return
        self.cache.invalidate(self.path)
        self.reload_installed_roots()
        self.open_path(self.path)

    def _activate(self, obj):
        if obj is None:
            return
        e = obj.entry
        if e.is_dir and not e.is_link and e.readable and not e.other_device:
            self.open_path(e.path)

    def _on_key(self, _c, keyval, _code, state):
        from gi.repository import Gdk
        if keyval == Gdk.KEY_BackSpace or (keyval == Gdk.KEY_Left and state & Gdk.ModifierType.ALT_MASK):
            self.go_up()
            return True
        if keyval == Gdk.KEY_Delete:
            obj = self.selection.get_selected_item()
            if obj is not None:
                self._confirm_trash(obj)
            return True
        return False

    def _on_hidden(self, btn):
        self.show_hidden = btn.get_active()
        self._refresh_view()

    def _on_search(self, entry):
        self.query = entry.get_text().strip().casefold()
        self._refresh_view()

    def _matches(self, o: DiskObject) -> bool:
        if not self.show_hidden and o.entry.hidden:
            return False
        return not self.query or self.query in o.entry.name.casefold()

    def _refresh_view(self):
        self.filter.changed(Gtk.FilterChange.DIFFERENT)
        if self._listing:  # đang đọc thư mục mới: giữ trang "Đang đọc", không hiện kết quả cũ
            return
        if self._error and not self.objects:
            self.empty.set_icon_name("dialog-error-symbolic")
            self.empty.set_title("Không đọc được thư mục")
            self.empty.set_description(self._error)
        elif self.objects:
            self.empty.set_icon_name("edit-find-symbolic")
            self.empty.set_title("Không có mục phù hợp")
            self.empty.set_description("Thử đổi từ khoá hoặc bật “Hiện mục ẩn”.")
        else:
            self.empty.set_icon_name("folder-symbolic")
            self.empty.set_title("Thư mục trống")
            self.empty.set_description("")
        self.stack.set_visible_child_name("list" if self.filter_model.get_n_items() else "empty")

    def _on_selected(self, *_):
        obj = self.selection.get_selected_item()
        self._show_details(obj)
        if obj is not None:
            self._auto_top = False
        # Chọn sẵn khi đi lên thư mục cha: không bật khung chi tiết che bảng ở cửa sổ hẹp.
        if obj is not None and not self._programmatic and self.details_split.get_collapsed():
            self.details_split.set_show_sidebar(True)

    def _trash_reason(self, obj: DiskObject) -> str:
        locked = bool(obj.hint and obj.hint.level == sc.LOCKED)
        reason = can_trash(obj.entry.path, HOME, locked_hint=locked, extra_protected=list(self.installed_roots))
        if not reason and self.roots_state != "ok":
            reason = ("đang kiểm tra chương trình đã cài, thử lại sau giây lát" if self.roots_state == "loading"
                      else self.roots_state)
        return reason

    def _show_details(self, obj):
        self.details.show_obj(obj, self._trash_reason(obj) if obj is not None else "")

    # ------------------------------------------------------------ chuyển vào Thùng rác
    def _confirm_trash(self, obj: DiskObject):
        e = obj.entry
        reason = self._trash_reason(obj)
        if reason:
            self.emit("toast", make_toast("Không chuyển vào Thùng rác được: " + reason, 5))
            return
        from ..removal.planner import running_pids
        pids = running_pids([e.path]) if e.is_dir else []
        body = (f"{_tilde(e.path)}\n\nDung lượng: {human_size(e.size) if e.size is not None else 'đang tính'}"
                + (f" · {_num(e.files)} file" if e.is_dir and e.files is not None else ""))
        if obj.hint:
            body += f"\n\nGợi ý: {obj.hint.note}"
        if pids:
            body += f"\n\n⚠ Đang có chương trình chạy từ đây (PID {', '.join(pids[:5])}) — nên đóng nó trước."
        body += "\n\nMục này vào Thùng rác — khôi phục được cho tới khi bạn dọn Thùng rác."
        dlg = Adw.AlertDialog(heading=f"Chuyển “{e.name}” vào Thùng rác?", body=body)
        dlg.add_response("cancel", "Huỷ")
        dlg.add_response("trash", "Chuyển vào Thùng rác")
        dlg.set_response_appearance("trash", Adw.ResponseAppearance.DESTRUCTIVE)
        dlg.set_default_response("cancel")
        dlg.set_close_response("cancel")
        dlg.connect("response", lambda _d, r: r == "trash" and self._do_trash(obj))
        dlg.present(self.get_root())

    def _do_trash(self, obj: DiskObject):
        e = obj.entry
        reason = self._trash_reason(obj)  # kiểm tra LẠI ngay trước khi chuyển
        if reason:
            self.emit("toast", make_toast("Không chuyển vào Thùng rác được: " + reason, 5))
            return
        size, files = e.size, e.files
        gfile = Gio.File.new_for_path(e.path)

        def done(src, res, _d=None):
            ok, msg = True, ""
            try:
                src.trash_finish(res)
            except GLib.Error as exc:
                ok, msg = False, exc.message
            self._record(e.path, size, files, ok, msg)
            if not ok:
                self.emit("toast", make_toast("Không chuyển được: " + msg, 6))
                return
            self.cache.invalidate(e.path)
            t = make_toast(f"Đã chuyển “{e.name}” vào Thùng rác", 8)
            t.set_button_label("Hoàn tác")
            t.connect("button-clicked", lambda *_: self._undo(e.path))
            self.emit("toast", t)
            self.open_path(self.path)

        gfile.trash_async(GLib.PRIORITY_DEFAULT, None, done, None)

    def _undo(self, orig: str):
        def work():
            msg = ""
            try:
                trash = Gio.File.new_for_uri("trash:///")
                best, best_date = None, ""
                en = trash.enumerate_children("standard::name,trash::orig-path,trash::deletion-date",
                                              Gio.FileQueryInfoFlags.NONE, None)
                for info in en:
                    if info.get_attribute_byte_string("trash::orig-path") == orig:
                        date = info.get_attribute_string("trash::deletion-date") or ""
                        if date >= best_date:
                            best, best_date = info, date
                if best is None:
                    msg = "không tìm thấy trong Thùng rác"
                elif os.path.lexists(orig):
                    msg = "đã có mục cùng tên ở vị trí cũ"
                else:
                    trash.get_child(best.get_name()).move(Gio.File.new_for_path(orig), Gio.FileCopyFlags.NOFOLLOW_SYMLINKS,
                                                          None, None, None)
            except GLib.Error as exc:
                msg = exc.message
            GLib.idle_add(self._undone, orig, msg)

        threading.Thread(target=work, daemon=True).start()

    def _undone(self, orig, msg):
        self.emit("toast", make_toast(("Không hoàn tác được: " + msg) if msg else f"Đã khôi phục {_tilde(orig)}", 5))
        try:
            history.append(history.make_disk_record(orig, None, None, not msg, msg, restored=True))
        except Exception:
            pass
        self.cache.invalidate(orig)
        self.open_path(self.path)
        return False

    @staticmethod
    def _record(path, size, files, ok, msg):
        try:
            history.append(history.make_disk_record(path, size, files, ok, msg))
        except Exception:
            pass

    def ensure_opened(self):
        if not self.path:
            self.open_path(HOME)

    def free_text(self) -> str:
        return self.update_disk()

    def reload_installed_roots(self):
        """Đọc lại danh sách chương trình đã cài (có thể vừa cài / gỡ); trong lúc đọc, việc xoá bị khoá."""
        self.roots_state = "loading"
        self._roots_token += 1
        threading.Thread(target=self._load_installed_roots, args=(self._roots_token,), daemon=True).start()

    def invalidate_all(self):
        """Dung lượng đã đổi (vừa gỡ phần mềm / dọn dẹp): bỏ mọi kết quả cũ và tính lại thư mục đang xem."""
        self.reload_installed_roots()
        self.cancel.set()
        self.cache.clear()
        self.update_disk()
        if self.path:
            self.open_path(self.path)

    def shutdown(self):
        """Dừng mọi việc đo khi đóng cửa sổ / thoát (để tiến trình không chạy ngầm tiếp)."""
        self.cancel.set()
        self.pool.shutdown(wait=False, cancel_futures=True)
