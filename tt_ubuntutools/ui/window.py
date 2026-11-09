"""Cửa sổ chính."""

from __future__ import annotations

import threading

from gi.repository import Adw, Gio, GLib, Gtk, Pango

from .. import APP_NAME
from ..collectors import CollectResult, collect_all
from ..models import RemovalResult, SoftwareItem, Source, human_size
from .common import SOURCE_ICONS, ScopeSwitcher, make_spinner, make_toast
from .donate import donate_button
from .details_pane import DetailsPane
from .remove_dialog import RemoveDialog
from .software_list import SoftwareList


class SidebarRow(Gtk.ListBoxRow):
    def __init__(self, code: str, label: str):
        super().__init__()
        self.code = code
        box = Gtk.Box(spacing=12, margin_top=8, margin_bottom=8, margin_start=6, margin_end=6)
        box.append(Gtk.Image.new_from_icon_name(SOURCE_ICONS.get(code, "package-x-generic-symbolic")))
        vb = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)
        title = Gtk.Label(label=label, xalign=0)
        self.sub = Gtk.Label(xalign=0)
        self.sub.add_css_class("dim-label")
        self.sub.add_css_class("sidebar-count")
        vb.append(title)
        vb.append(self.sub)
        box.append(vb)
        self.count = Gtk.Label()
        self.count.add_css_class("dim-label")
        box.append(self.count)
        self.set_child(box)

    def update(self, n: int, size: int):
        self.count.set_label(str(n))
        self.sub.set_label(human_size(size) if size else "")
        self.sub.set_visible(bool(size))


class MainWindow(Adw.ApplicationWindow):
    def __init__(self, app: Adw.Application):
        super().__init__(application=app, title=APP_NAME, default_width=1320, default_height=820)
        self.set_size_request(380, 400)
        self.result: CollectResult | None = None
        self._loading = False
        self._reload_pending = False
        self.connect("close-request", self._on_close_request)

        self.toasts = Adw.ToastOverlay()
        self.split = Adw.NavigationSplitView(min_sidebar_width=230, max_sidebar_width=290)
        self.toasts.set_child(self.split)
        self.set_content(self.toasts)

        self._build_sidebar()
        self._build_content()

        # Cửa sổ hẹp: thu khung chi tiết thành lớp phủ; rất hẹp: thu cả sidebar nguồn.
        bp2 = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 1150sp"))
        bp2.add_setter(self.details_split, "collapsed", True)
        bp2.add_setter(self.details_split, "show-sidebar", False)
        self.add_breakpoint(bp2)
        self.add_breakpoint(self._donate_compact)
        bp = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 760sp"))
        bp.add_setter(self.split, "collapsed", True)
        bp.add_setter(self.details_split, "collapsed", True)
        bp.add_setter(self.details_split, "show-sidebar", False)
        self.add_breakpoint(bp)
        self._setup_actions()
        self.reload()

    # =================== Sidebar ===================
    def _build_sidebar(self):
        tv = Adw.ToolbarView()
        header = Adw.HeaderBar()
        header.set_title_widget(Adw.WindowTitle(title=APP_NAME, subtitle="Phần mềm đã cài"))
        tv.add_top_bar(header)
        self.sidebar_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.sidebar_list.add_css_class("navigation-sidebar")
        self.sidebar_rows: dict[str, SidebarRow] = {}
        all_row = SidebarRow("all", "Tất cả")
        self.sidebar_rows["all"] = all_row
        self.sidebar_list.append(all_row)
        for s in Source:
            row = SidebarRow(s.code, s.label)
            self.sidebar_rows[s.code] = row
            self.sidebar_list.append(row)
        self.sidebar_list.select_row(all_row)
        self.sidebar_list.connect("row-selected", self._on_source_selected)
        self.sidebar_list.connect("row-activated", lambda *_: self.split.set_show_content(True))

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        sc = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER, child=self.sidebar_list)
        box.append(sc)
        self.unavailable_label = Gtk.Label(wrap=True, xalign=0, margin_start=14, margin_end=14,
                                           margin_bottom=12, margin_top=6)
        self.unavailable_label.add_css_class("dim-label")
        self.unavailable_label.add_css_class("caption")
        box.append(self.unavailable_label)
        tv.set_content(box)
        self.split.set_sidebar(Adw.NavigationPage(title="Nguồn cài đặt", child=tv))

    # =================== Nội dung ===================
    def _build_content(self):
        tv = Adw.ToolbarView()
        header = Adw.HeaderBar()

        self.scope_group = ScopeSwitcher([
            ("apps", "Ứng dụng", "Chỉ hiện ứng dụng có lối tắt menu hoặc do bạn tự cài"),
            ("all", "Tất cả gói", "Hiện toàn bộ, kể cả thư viện và gói hệ thống"),
        ], "apps")
        self.scope_group.connect("changed", self._on_scope_changed)
        header.pack_start(self.scope_group)

        self.search = Gtk.SearchEntry(placeholder_text="Tìm theo tên, mô tả, nguồn…", hexpand=True)
        self.search.connect("search-changed", self._on_search)
        clamp = Adw.Clamp(maximum_size=420, child=self.search)
        header.set_title_widget(clamp)

        menu = Gio.Menu()
        menu.append("Lịch sử gỡ cài đặt", "app.history")
        menu.append("Làm mới", "win.refresh")
        menu.append("Giới thiệu", "app.about")
        menu_btn = Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu, tooltip_text="Menu")
        header.pack_end(menu_btn)
        # Nút "Đóng góp dự án" (DonateKit) — nhóm bên phải, trước Giới thiệu; cửa sổ hẹp chỉ hiện trái tim.
        self._donate_compact = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 1000sp"))
        header.pack_end(donate_button(self._donate_compact))
        self.refresh_btn = Gtk.Button(icon_name="view-refresh-symbolic", tooltip_text="Làm mới (F5)",
                                      action_name="win.refresh")
        header.pack_end(self.refresh_btn)
        tv.add_top_bar(header)

        self.summary = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END)
        self.summary.add_css_class("summary-bar")
        self.summary.add_css_class("dim-label")
        tv.add_top_bar(self.summary)

        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        loading = Adw.StatusPage(title="Đang thống kê phần mềm…",
                                 description="Đang đọc dữ liệu từ apt, snap, flatpak, AppImage, pip, npm và /opt")
        sp = make_spinner(48)
        sp.set_halign(Gtk.Align.CENTER)
        loading.set_child(sp)
        self.stack.add_named(loading, "loading")

        self.list = SoftwareList()
        self.list.connect("item-selected", self._on_item_selected)
        self.list.connect("item-activated", lambda _l, it: self.open_remove(it))
        self.details = DetailsPane()
        self.details.connect("remove-requested", lambda _d, it: self.open_remove(it))
        self.details_split = Adw.OverlaySplitView(
            content=self.list, sidebar=self.details, sidebar_position=Gtk.PackType.END,
            min_sidebar_width=330, max_sidebar_width=420, sidebar_width_fraction=0.3,
            show_sidebar=True, enable_show_gesture=False, enable_hide_gesture=True)
        self.list_page = self.details_split
        self.stack.add_named(self.list_page, "list")

        self.empty_page = Adw.StatusPage(icon_name="edit-find-symbolic", title="Không có kết quả",
                                         description="Thử đổi từ khoá hoặc chọn “Tất cả gói”.")
        self.stack.add_named(self.empty_page, "empty")
        tv.set_content(self.stack)
        self.content_page = Adw.NavigationPage(title="Danh sách", child=tv)
        self.split.set_content(self.content_page)

    def _setup_actions(self):
        refresh = Gio.SimpleAction.new("refresh", None)
        refresh.connect("activate", lambda *_: self.reload())
        self.add_action(refresh)
        focus = Gio.SimpleAction.new("search", None)
        focus.connect("activate", lambda *_: self.search.grab_focus())
        self.add_action(focus)
        app = self.get_application()
        app.set_accels_for_action("win.refresh", ["F5", "<Control>r"])
        app.set_accels_for_action("win.search", ["<Control>f"])

    # =================== Dữ liệu ===================
    def reload(self, keep_key: str | None = None):
        if self._loading:
            self._reload_pending = True  # làm mới lại ngay khi lần tải hiện tại xong
            return
        self._loading = True
        self.refresh_btn.set_sensitive(False)
        if self.result is None:
            self.stack.set_visible_child_name("loading")

        def work():
            try:
                res = collect_all()
            except Exception as exc:  # luôn trả kết quả để giao diện không kẹt ở màn hình tải
                res = CollectResult(errors={"Thống kê": f"{type(exc).__name__}: {exc}"})
            GLib.idle_add(self._on_loaded, res, keep_key)

        threading.Thread(target=work, daemon=True).start()

    def _on_loaded(self, res: CollectResult, keep_key: str | None):
        self._loading = False
        self.refresh_btn.set_sensitive(True)
        self.result = res
        self.list.set_items(res.items)
        if keep_key:
            self.list.select_key(keep_key)
        self.details.show_item(self.list.selected_item())
        for label, err in res.errors.items():
            self.toasts.add_toast(make_toast(f"Lỗi khi đọc {label}: {err}", 6))
        if res.unavailable:
            self.unavailable_label.set_label("Không có trên máy: " + ", ".join(res.unavailable))
        self.unavailable_label.set_visible(bool(res.unavailable))
        self._update_view()
        if self._reload_pending:
            self._reload_pending = False
            self.reload()
        return False

    def _update_view(self):
        items = self.list.all_items()
        # Đếm theo nguồn (tôn trọng bộ lọc Ứng dụng/Tất cả + tìm kiếm)
        counts: dict[str, list[int]] = {k: [0, 0] for k in self.sidebar_rows}
        for it in items:
            if not self.list.matches(it, ignore_source=True):
                continue
            for k in ("all", it.source.code):
                counts[k][0] += 1
                counts[k][1] += it.size or 0
        present = {it.source.code for it in items}
        for code, row in self.sidebar_rows.items():
            row.update(*counts[code])
            row.set_visible(code == "all" or code in present)
        selected = self.sidebar_list.get_selected_row()
        if selected is not None and not selected.get_visible():
            self.sidebar_list.select_row(self.sidebar_rows["all"])

        visible = self.list.visible_items()
        total = sum(i.size or 0 for i in visible)
        protected = sum(1 for i in visible if i.protected_reason)
        label = f"Hiển thị {len(visible)} / {len(items)} mục · tổng {human_size(total)}"
        if protected:
            label += f" · {protected} mục được bảo vệ 🔒"
        self.summary.set_label(label)
        if self.result is not None:
            if not items and self.result.errors:
                self.empty_page.set_title("Không đọc được danh sách phần mềm")
                self.empty_page.set_description("Xem thông báo lỗi ở cuối màn hình rồi bấm Làm mới (F5).")
            else:
                self.empty_page.set_title("Không có kết quả")
                self.empty_page.set_description("Thử đổi từ khoá hoặc chọn “Tất cả gói”.")
            self.stack.set_visible_child_name("list" if visible else "empty")

    # =================== Sự kiện ===================
    def _on_source_selected(self, _lb, row):
        if row is None:
            return
        self.list.source_code = row.code
        self.list.refilter()
        self._update_view()

    def _on_scope_changed(self, *_):
        self.list.apps_only = self.scope_group.get_active_name() == "apps"
        self.list.refilter()
        self._update_view()

    def _on_search(self, entry):
        self.list.query = entry.get_text().strip().casefold()
        self.list.refilter()
        self._update_view()

    def _on_item_selected(self, _list, item: SoftwareItem | None):
        self.details.show_item(item)
        if item is not None and self.details_split.get_collapsed():
            self.details_split.set_show_sidebar(True)

    def open_remove(self, item: SoftwareItem | None):
        if item is None:
            return
        if item.protected_reason:
            self.toasts.add_toast(make_toast(f"Không thể gỡ: {item.protected_reason}", 5))
            return
        dlg = RemoveDialog(item, self._on_removed)
        dlg.present(self)

    def _on_removed(self, result: RemovalResult):
        if result.success:
            self.toasts.add_toast(make_toast("Đã gỡ xong — đang làm mới danh sách", 4))
        else:
            self.toasts.add_toast(make_toast("Gỡ không thành công: " + result.message, 6))
        self.reload()

    def _on_close_request(self, *_):
        """Không cho đóng cửa sổ khi đang gỡ — đóng giữa chừng có thể làm hỏng dpkg."""
        app = self.get_application()
        if app is not None and getattr(app, "busy", 0):
            self.toasts.add_toast(make_toast("Đang gỡ cài đặt — vui lòng đợi xong rồi hãy đóng.", 4))
            return True
        return False
