"""Hộp thoại gỡ: xem trước -> xác nhận -> tiến trình -> kết quả."""

from __future__ import annotations

import threading
from typing import Callable

from gi.repository import Adw, GLib, Gtk, Pango

from .. import history
from ..collectors import collect_all, collector_label
from ..models import RemovalOptions, RemovalPlan, RemovalResult, SoftwareItem, Source, human_size
from ..removal.planner import make_plan
from ..removal.runner import RemovalRunner
from ..utils import display
from .common import make_spinner, set_item_icon

COLLECTOR_FOR_SOURCE = {
    Source.APT: "apt", Source.THIRD_PARTY: "apt", Source.DEB: "apt", Source.SNAP: "snap",
    Source.FLATPAK: "flatpak", Source.APPIMAGE: "appimage", Source.PIP: "pip",
    Source.NPM: "npm", Source.MANUAL: "manual",
}


def _clear(box: Gtk.Box):
    child = box.get_first_child()
    while child:
        nxt = child.get_next_sibling()
        box.remove(child)
        child = nxt


def _notice(css: str, icon: str, text: str) -> Gtk.Box:
    b = Gtk.Box(spacing=10)
    b.add_css_class(css)
    img = Gtk.Image.new_from_icon_name(icon)
    img.set_valign(Gtk.Align.START)
    b.append(img)
    lbl = Gtk.Label(label=text, wrap=True, xalign=0, hexpand=True, wrap_mode=Pango.WrapMode.WORD_CHAR)
    b.append(lbl)
    return b


class RemoveDialog(Adw.Dialog):
    def __init__(self, item: SoftwareItem, on_finished: Callable[[RemovalResult], None]):
        super().__init__(title=f"Gỡ {item.title}", content_width=620, content_height=680)
        self.item = item
        self.on_finished = on_finished
        self.plan: RemovalPlan | None = None
        self.options = RemovalOptions()
        self._plan_seq = 0
        self.result: RemovalResult | None = None

        self.toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar()
        self.toolbar.add_top_bar(header)
        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.SLIDE_LEFT_RIGHT, vexpand=True)
        self.toolbar.set_content(self.stack)
        self.set_child(self.toolbar)

        self._build_plan_page()
        self._build_running_page()
        self._build_result_page()
        self._refresh_plan()

    # =================== Trang xem trước ===================
    def _build_plan_page(self):
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        scroller = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        clamp = Adw.Clamp(maximum_size=560, margin_start=18, margin_end=18, margin_top=12, margin_bottom=12)
        self.plan_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        clamp.set_child(self.plan_box)
        scroller.set_child(clamp)
        outer.append(scroller)

        # Đầu
        head = Gtk.Box(spacing=12)
        icon = Gtk.Image()
        set_item_icon(icon, self.item, 48)
        head.append(icon)
        vb = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER)
        t = Gtk.Label(label=f"Gỡ “{self.item.title}”?", xalign=0, wrap=True)
        t.add_css_class("title-3")
        s = Gtk.Label(label=f"{self.item.source.label} · {self.item.version or self.item.name}",
                      xalign=0, wrap=True)
        s.add_css_class("dim-label")
        vb.append(t)
        vb.append(s)
        head.append(vb)
        self.plan_box.append(head)

        # Tuỳ chọn
        self.opts_group = Adw.PreferencesGroup(title="Tuỳ chọn")
        self.purge_row = Adw.SwitchRow(title="Xoá cả file cấu hình (purge)")
        self.auto_row = Adw.SwitchRow(title="Dọn các gói phụ thuộc không còn dùng (autoremove)")
        self.purge_row.connect("notify::active", self._on_option_changed)
        self.auto_row.connect("notify::active", self._on_option_changed)
        self.opts_group.add(self.purge_row)
        self.opts_group.add(self.auto_row)
        self.opts_group.set_visible(False)
        self.plan_box.append(self.opts_group)

        # Nội dung động (thông báo, danh sách, lệnh)
        self.dynamic = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.plan_box.append(self.dynamic)

        # Nút
        btns = Gtk.Box(spacing=12, halign=Gtk.Align.END, margin_start=18, margin_end=18,
                       margin_top=10, margin_bottom=18)
        cancel = Gtk.Button(label="Huỷ")
        cancel.connect("clicked", lambda *_: self.close())
        self.confirm_btn = Gtk.Button(label="Gỡ cài đặt", sensitive=False)
        self.confirm_btn.add_css_class("destructive-action")
        self.confirm_btn.connect("clicked", self._on_confirm)
        btns.append(cancel)
        btns.append(self.confirm_btn)
        outer.append(btns)
        self.stack.add_named(outer, "plan")

    def _on_option_changed(self, *_):
        self.options = RemovalOptions(purge=self.purge_row.get_active(),
                                      autoremove=self.auto_row.get_active())
        self._refresh_plan()

    def _refresh_plan(self):
        self._plan_seq += 1
        seq = self._plan_seq
        self.confirm_btn.set_sensitive(False)
        _clear(self.dynamic)
        spin_box = Gtk.Box(spacing=10, halign=Gtk.Align.CENTER, margin_top=24)
        spin_box.append(make_spinner(16))
        spin_box.append(Gtk.Label(label="Đang mô phỏng thao tác gỡ…"))
        self.dynamic.append(spin_box)
        item, opts = self.item, self.options

        def work():
            plan = make_plan(item, opts)
            GLib.idle_add(self._show_plan, plan, seq)

        threading.Thread(target=work, daemon=True).start()

    def _show_plan(self, plan: RemovalPlan, seq: int):
        if seq != self._plan_seq:
            return False  # đã có yêu cầu mới hơn
        self.plan = plan
        _clear(self.dynamic)
        self.opts_group.set_visible(plan.supports_purge or plan.supports_autoremove)
        self.purge_row.set_visible(plan.supports_purge)
        self.purge_row.set_title(plan.purge_label)
        self.auto_row.set_visible(plan.supports_autoremove)
        self.auto_row.set_title(plan.autoremove_label)

        if plan.error:
            self.dynamic.append(_notice("blocked-box", "dialog-error-symbolic", plan.error))
        for r in plan.blocked_reasons:
            self.dynamic.append(_notice("blocked-box", "changes-prevent-symbolic", "Bị chặn: " + r))
        if plan.blocked_reasons:
            self.dynamic.append(_notice(
                "blocked-box", "dialog-information-symbolic",
                "Không thể gỡ vì thao tác này sẽ ảnh hưởng tới thành phần quan trọng của hệ thống."))
        for w in plan.warnings:
            self.dynamic.append(_notice("warning-box", "dialog-warning-symbolic", w))

        if plan.affected:
            if plan.freed_bytes:
                desc = f"Giải phóng khoảng {human_size(plan.freed_bytes)}"
            elif plan.trashed_bytes:
                desc = (f"Chuyển {human_size(plan.trashed_bytes)} vào Thùng rác — chỉ giải phóng khi bạn "
                        "dọn Thùng rác")
            else:
                desc = None
            group = Adw.PreferencesGroup(title=f"Sẽ bị gỡ ({len(plan.affected)})",
                                         description=GLib.markup_escape_text(desc) if desc else None)
            for a in plan.affected:
                row = Adw.ActionRow(title=GLib.markup_escape_text(display(a.name)),
                                    subtitle=GLib.markup_escape_text(display(a.detail)))
                if a.name == self.item.name or a.name == self.item.path:
                    row.add_prefix(Gtk.Image.new_from_icon_name("user-trash-symbolic"))
                if a.size:
                    lbl = Gtk.Label(label=human_size(a.size))
                    lbl.add_css_class("dim-label")
                    row.add_suffix(lbl)
                group.add(row)
            self.dynamic.append(group)

        if plan.steps:
            exp = Gtk.Expander(label="Lệnh sẽ thực hiện")
            txt = "\n".join(display(s.display()) for s in plan.steps)
            if plan.needs_root:
                txt += "\n\n(Cần quyền quản trị — hệ thống sẽ hỏi mật khẩu.)"
            lbl = Gtk.Label(label=txt, xalign=0, wrap=True, selectable=True,
                            wrap_mode=Pango.WrapMode.WORD_CHAR)
            lbl.add_css_class("log-view")
            exp.set_child(lbl)
            self.dynamic.append(exp)

        self.confirm_btn.set_sensitive(not plan.blocked and bool(plan.steps))
        return False

    # =================== Trang đang chạy ===================
    def _build_running_page(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin_start=18, margin_end=18,
                      margin_top=12, margin_bottom=18)
        top = Gtk.Box(spacing=12)
        top.append(make_spinner(16))
        self.running_label = Gtk.Label(label=f"Đang gỡ {self.item.title}…", xalign=0)
        self.running_label.add_css_class("title-4")
        top.append(self.running_label)
        box.append(top)
        hint = Gtk.Label(label="Nếu được hỏi, hãy nhập mật khẩu quản trị trong hộp thoại hệ thống.",
                         xalign=0, wrap=True)
        hint.add_css_class("dim-label")
        box.append(hint)
        self.log_buffer = Gtk.TextBuffer()
        self.log_view = Gtk.TextView(buffer=self.log_buffer, editable=False, monospace=True,
                                     wrap_mode=Gtk.WrapMode.WORD_CHAR, cursor_visible=False)
        self.log_view.add_css_class("log-view")
        self.log_scroller = Gtk.ScrolledWindow(vexpand=True, child=self.log_view)
        self.log_scroller.add_css_class("card")
        box.append(self.log_scroller)
        self.stack.add_named(box, "running")

    def _append_log(self, line: str):
        end = self.log_buffer.get_end_iter()
        self.log_buffer.insert(end, display(line) + "\n")
        mark = self.log_buffer.create_mark(None, self.log_buffer.get_end_iter(), False)
        self.log_view.scroll_to_mark(mark, 0, False, 0, 1)
        self.log_buffer.delete_mark(mark)

    def _on_confirm(self, *_):
        if not self.plan or self.plan.blocked:
            return
        self.set_can_close(False)
        self._app = Gtk.Application.get_default()
        if self._app is not None and hasattr(self._app, "begin_busy"):
            self._app.begin_busy()
        self.stack.set_visible_child_name("running")
        self.runner = RemovalRunner(self.plan, self._append_log, self._on_runner_done)
        self.runner.start()

    def _on_runner_done(self, result: RemovalResult):
        self.result = result
        if not result.success:
            self._finish(result)
            return
        self._append_log("Đang kiểm tra lại danh sách phần mềm…")
        source_name = COLLECTOR_FOR_SOURCE[self.item.source]
        key = self.item.key

        label = collector_label(source_name)

        def verify():
            try:
                res = collect_all(only={source_name})
                if label in res.errors or label in res.unavailable:
                    result.verified = None  # không quét lại được -> KHÔNG được báo là đã kiểm tra
                else:
                    result.verified = not any(i.key == key for i in res.items)
            except Exception:
                result.verified = None
            GLib.idle_add(self._finish, result)

        threading.Thread(target=verify, daemon=True).start()

    # =================== Trang kết quả ===================
    def _build_result_page(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.result_status = Adw.StatusPage(vexpand=True)
        self.result_status.add_css_class("compact")
        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.result_details = Adw.PreferencesGroup()
        inner.append(self.result_details)
        self.result_log_exp = Gtk.Expander(label="Nhật ký chi tiết")
        self.result_log = Gtk.Label(xalign=0, wrap=True, selectable=True, wrap_mode=Pango.WrapMode.WORD_CHAR)
        self.result_log.add_css_class("log-view")
        sc = Gtk.ScrolledWindow(min_content_height=160, max_content_height=260, propagate_natural_height=True,
                                child=self.result_log)
        sc.add_css_class("card")
        self.result_log_exp.set_child(sc)
        inner.append(self.result_log_exp)
        close = Gtk.Button(label="Đóng", halign=Gtk.Align.CENTER)
        close.add_css_class("pill")
        close.add_css_class("suggested-action")
        close.connect("clicked", lambda *_: self.close())
        inner.append(close)
        self.result_status.set_child(inner)
        sc2 = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER, child=self.result_status)
        box.append(sc2)
        self.stack.add_named(box, "result")

    def _finish(self, result: RemovalResult):
        try:
            self._show_result(result)
        finally:
            self.set_can_close(True)
            app = getattr(self, "_app", None)
            if app is not None and hasattr(app, "end_busy"):
                app.end_busy()
            try:
                history.append(history.make_record(self.plan, result))
            except Exception as exc:
                self._append_log(f"Không ghi được lịch sử: {exc}")
            self.on_finished(result)
        return False

    def _show_result(self, result: RemovalResult):
        ok = result.success and result.verified is not False
        if result.success and result.verified is False:
            result.success = False
            result.message = "Lệnh chạy xong nhưng phần mềm vẫn còn trên máy."
        self.result_status.set_icon_name("emblem-ok-symbolic" if ok else "dialog-error-symbolic")
        self.result_status.set_title("Đã gỡ thành công" if ok else
                                     ("Mới gỡ được một phần" if result.partial else "Gỡ không thành công"))
        self.result_status.set_description(GLib.markup_escape_text(display(result.message)))

        def row(title, value):
            r = Adw.ActionRow(title=title, subtitle=GLib.markup_escape_text(display(value) or "—"),
                              subtitle_selectable=True)
            r.add_css_class("property")
            self.result_details.add(r)

        row("Phần mềm", f"{self.item.title} ({self.item.source.label})")
        if ok:
            row("Đã gỡ", ", ".join(result.removed))
            if self.plan.freed_bytes:
                row("Dung lượng giải phóng", human_size(self.plan.freed_bytes))
            elif self.plan.trashed_bytes:
                row("Đã chuyển vào Thùng rác", human_size(self.plan.trashed_bytes) + " (dọn Thùng rác để giải phóng)")
            row("Kiểm tra lại", {True: "Phần mềm không còn trên máy ✔",
                                 None: "Không kiểm tra lại được — hãy bấm Làm mới để xem"}[result.verified])
        else:
            if result.partial and result.completed:
                row("Đã thực hiện được", ", ".join(result.completed))
            row("Mã lỗi", str(result.exit_code))
        self.result_log.set_label(display(result.log))
        self.result_log_exp.set_expanded(not ok)
        self.stack.set_visible_child_name("result")
