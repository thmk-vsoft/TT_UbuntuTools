"""Hộp thoại chạy dọn dẹp: xác nhận (gom theo mức an toàn) -> chạy -> kết quả.
Việc quét và chọn mục nằm ở khu vực Dọn dẹp của cửa sổ chính (ui/cleanup_view.py)."""

from __future__ import annotations

import os
import threading

from gi.repository import Adw, Gio, GLib, Gtk, Pango

from .. import history
from ..cleanup.plan import CleanupPlan, build_cleanup_plan
from ..cleanup.scanners import IRREVERSIBLE, LEVELS, LOCKED, REVIEW, SAFE, CleanupItem
from ..models import RemovalResult, human_size
from ..removal.runner import RemovalRunner
from ..utils import display
from .common import make_spinner


def _clear(box: Gtk.Box):
    child = box.get_first_child()
    while child:
        nxt = child.get_next_sibling()
        box.remove(child)
        child = nxt


def level_badge(level: str) -> Gtk.Label:
    label, css, _ = LEVELS[level]
    lbl = Gtk.Label(label=label, valign=Gtk.Align.CENTER)
    lbl.add_css_class("badge")
    lbl.add_css_class(css)
    return lbl


def size_text(it: CleanupItem) -> str:
    if it.size is None or it.size_mark == "?":
        return "chưa biết"
    mark = it.size_mark or ("≥" if not it.size_exact else "")
    return (mark + " " if mark else "") + human_size(it.size)


def total_text(items: list[CleanupItem]) -> str:
    total = sum(i.size or 0 for i in items)
    marks = {i.size_mark or ("≥" if not i.size_exact else "") for i in items} - {""}
    return ("khoảng " if marks else "") + human_size(total)


def _free_space() -> dict[int, int]:
    """Dung lượng trống theo thiết bị (thư mục nhà và /)."""
    out = {}
    for p in (os.path.expanduser("~"), "/"):
        try:
            st = os.statvfs(p)
            out[os.stat(p).st_dev] = st.f_bavail * st.f_frsize
        except OSError:
            continue
    return out


class CleanupRunDialog(Adw.Dialog):
    def __init__(self, selected: list[CleanupItem], on_finished=None):
        super().__init__(title="Dọn dẹp", content_width=680, content_height=720)
        self.on_finished = on_finished
        self.cplan: CleanupPlan | None = None
        self.irrev_check = None
        self.toolbar = Adw.ToolbarView()
        self.toolbar.add_top_bar(Adw.HeaderBar())
        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.SLIDE_LEFT_RIGHT, vexpand=True)
        self.toolbar.set_content(self.stack)
        self.set_child(self.toolbar)
        wait = Adw.StatusPage(title="Đang kiểm tra lại các mục đã chọn…")
        sp = make_spinner(32)
        sp.set_halign(Gtk.Align.CENTER)
        wait.set_child(sp)
        self.stack.add_named(wait, "wait")
        self._build_confirm_page()
        self._build_running_page()
        self._build_result_page()
        self._start_plan(list(selected))

    def _on_closed(self, *_):
        # Huỷ ở bước xác nhận mà danh sách đã cũ -> nhờ cửa sổ chính quét lại
        if getattr(self, "_needs_rescan", False) and not getattr(self, "runner", None) and self.on_finished:
            self.on_finished(None)

    def _start_plan(self, sel: list[CleanupItem]):
        def work():
            try:
                cp = build_cleanup_plan(sel)
            except Exception as exc:  # không để hộp thoại kẹt im lặng
                GLib.idle_add(self._plan_failed, f"{type(exc).__name__}: {exc}")
                return
            GLib.idle_add(self._show_confirm, cp)

        threading.Thread(target=work, daemon=True).start()

    def _plan_failed(self, msg: str):
        _clear(self.confirm_box)
        w = Gtk.Label(label="Không lập được kế hoạch dọn: " + msg, xalign=0, wrap=True)
        box = Gtk.Box()
        box.add_css_class("blocked-box")
        box.append(w)
        self.confirm_box.append(box)
        self.run_btn.set_sensitive(False)
        self.stack.set_visible_child_name("confirm")
        return False

    # ======================= Xác nhận =======================
    def _build_confirm_page(self):
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        sc = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        clamp = Adw.Clamp(maximum_size=660, margin_start=16, margin_end=16, margin_top=10, margin_bottom=10)
        self.confirm_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        clamp.set_child(self.confirm_box)
        sc.set_child(clamp)
        outer.append(sc)
        bar = Gtk.Box(spacing=10, halign=Gtk.Align.END, margin_start=16, margin_end=16, margin_top=8,
                      margin_bottom=14)
        back = Gtk.Button(label="Huỷ")
        back.connect("clicked", lambda *_: self.close())
        self.connect("closed", self._on_closed)
        bar.append(back)
        self.run_btn = Gtk.Button(label="Dọn ngay")
        self.run_btn.add_css_class("destructive-action")
        self.run_btn.connect("clicked", self._on_run)
        bar.append(self.run_btn)
        outer.append(bar)
        self.stack.add_named(outer, "confirm")

    def _show_confirm(self, cp: CleanupPlan):
        self.cplan = cp
        self._needs_rescan = bool(cp.dropped)  # có mục đã thay đổi từ lúc quét -> huỷ thì quét lại
        _clear(self.confirm_box)
        t = Gtk.Label(label="Xác nhận dọn dẹp", xalign=0)
        t.add_css_class("title-3")
        self.confirm_box.append(t)
        if not cp.items:
            st = Adw.StatusPage(icon_name="emblem-ok-symbolic", title="Không còn gì để dọn",
                                description="Các mục đã chọn đã thay đổi từ lúc quét (lý do ở bên dưới). Đóng hộp "
                                            "thoại này để quét lại.")
            st.add_css_class("compact")
            self.confirm_box.append(st)
            self._needs_rescan = True
        summary = Adw.PreferencesGroup(title="Tóm tắt theo mức an toàn")
        for lvl in (SAFE, REVIEW, IRREVERSIBLE):
            if lvl in cp.by_level:
                n, size = cp.by_level[lvl]
                r = Adw.ActionRow(title=f"{n} mục", subtitle=LEVELS[lvl][2])
                r.add_prefix(level_badge(lvl))
                its = [i for i in cp.items if i.level == lvl]
                r.add_suffix(Gtk.Label(label=total_text(its)))
                r.add_css_class("lvl-edge-" + lvl)
                summary.add(r)
        if cp.by_level:
            self.confirm_box.append(summary)
        errs = [cp.plan.error] if cp.plan.error and cp.items else []
        for w in cp.plan.warnings + errs:
            lbl = Gtk.Label(label=w, xalign=0, wrap=True)
            box = Gtk.Box()
            box.add_css_class("warning-box" if w != cp.plan.error else "blocked-box")
            box.append(lbl)
            self.confirm_box.append(box)
        if cp.plan.needs_root:
            n = Gtk.Label(label="Một số mục cần quyền quản trị — hệ thống sẽ hỏi mật khẩu một lần.", xalign=0,
                          wrap=True)
            n.add_css_class("dim-label")
            self.confirm_box.append(n)
        lst = Adw.PreferencesGroup(title="Sẽ dọn")
        for it in cp.items:
            r = Adw.ActionRow(title=GLib.markup_escape_text(display(it.title)),
                              subtitle=GLib.markup_escape_text(display(it.note)), subtitle_lines=0)
            r.add_prefix(level_badge(it.level))
            r.add_suffix(Gtk.Label(label=size_text(it)))
            r.add_css_class("lvl-edge-" + it.level)
            lst.add(r)
        if cp.items:
            self.confirm_box.append(lst)
        if cp.dropped:
            dg = Adw.PreferencesGroup(title="Bỏ qua khi kiểm tra lại")
            for it, why in cp.dropped:
                r = Adw.ActionRow(title=GLib.markup_escape_text(display(it.title)),
                                  subtitle=GLib.markup_escape_text(display(why)), subtitle_lines=0)
                skip = Gtk.Label(label="Bỏ qua", valign=Gtk.Align.CENTER)
                skip.add_css_class("badge")
                skip.add_css_class("lvl-locked")
                r.add_prefix(skip)
                dg.add(r)
            self.confirm_box.append(dg)
        self.irrev_check = None
        if any(i.level == IRREVERSIBLE for i in cp.items):
            self.irrev_check = Gtk.CheckButton(label="Tôi hiểu: các mục màu đỏ (Thùng rác) sẽ bị xoá vĩnh viễn, "
                                                     "không khôi phục được.")
            self.irrev_check.add_css_class("error")
            self.irrev_check.connect("toggled", lambda *_: self._update_run_btn())
            self.confirm_box.append(self.irrev_check)
        self._update_run_btn()
        self.stack.set_visible_child_name("confirm")
        return False

    def _update_run_btn(self):
        cp = self.cplan
        ok = bool(cp and cp.plan.steps and not cp.plan.error)
        if self.irrev_check is not None and not self.irrev_check.get_active():
            ok = False
        self.run_btn.set_sensitive(ok)

    # ======================= Chạy =======================
    def _build_running_page(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin_start=18, margin_end=18,
                      margin_top=12, margin_bottom=18)
        top = Gtk.Box(spacing=12)
        top.append(make_spinner(16))
        lbl = Gtk.Label(label="Đang dọn dẹp…", xalign=0)
        lbl.add_css_class("title-4")
        top.append(lbl)
        box.append(top)
        hint = Gtk.Label(label="Nếu được hỏi, hãy nhập mật khẩu quản trị trong hộp thoại hệ thống.", xalign=0,
                         wrap=True)
        hint.add_css_class("dim-label")
        box.append(hint)
        self.log_buffer = Gtk.TextBuffer()
        self.log_view = Gtk.TextView(buffer=self.log_buffer, editable=False, monospace=True,
                                     wrap_mode=Gtk.WrapMode.WORD_CHAR, cursor_visible=False)
        self.log_view.add_css_class("log-view")
        scr = Gtk.ScrolledWindow(vexpand=True, child=self.log_view)
        scr.add_css_class("card")
        box.append(scr)
        self.stack.add_named(box, "running")

    def _append_log(self, line: str):
        end = self.log_buffer.get_end_iter()
        self.log_buffer.insert(end, display(line) + "\n")
        mark = self.log_buffer.create_mark(None, self.log_buffer.get_end_iter(), False)
        self.log_view.scroll_to_mark(mark, 0, False, 0, 1)
        self.log_buffer.delete_mark(mark)

    def _on_run(self, *_):
        cp = self.cplan
        if not cp or cp.plan.error or not cp.plan.steps:
            return
        self.set_can_close(False)
        self._app = Gtk.Application.get_default()
        if self._app is not None and hasattr(self._app, "begin_busy"):
            self._app.begin_busy()
        self._free_before = _free_space()
        self.stack.set_visible_child_name("running")
        self.runner = RemovalRunner(cp.plan, self._append_log, self._on_done, success_message="Dọn dẹp xong.",
                                    keep_going=True)
        self.runner.start()

    # ======================= Kết quả =======================
    def _build_result_page(self):
        self.result_status = Adw.StatusPage(vexpand=True)
        self.result_status.add_css_class("compact")
        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.result_details = Adw.PreferencesGroup()
        inner.append(self.result_details)
        exp = Gtk.Expander(label="Nhật ký chi tiết")
        self.result_log = Gtk.Label(xalign=0, wrap=True, selectable=True, wrap_mode=Pango.WrapMode.WORD_CHAR)
        self.result_log.add_css_class("log-view")
        sc = Gtk.ScrolledWindow(min_content_height=160, max_content_height=260, propagate_natural_height=True,
                                child=self.result_log)
        sc.add_css_class("card")
        exp.set_child(sc)
        self.result_log_exp = exp
        inner.append(exp)
        close = Gtk.Button(label="Đóng", halign=Gtk.Align.CENTER)
        close.add_css_class("pill")
        close.add_css_class("suggested-action")
        close.connect("clicked", lambda *_: self.close())
        inner.append(close)
        self.result_status.set_child(inner)
        self.stack.add_named(Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER,
                                                child=self.result_status), "result")

    def _on_done(self, result: RemovalResult):
        freed = 0
        try:
            after = _free_space()
            freed = sum(max(0, after.get(dev, 0) - before) for dev, before in self._free_before.items())
            self._show_result(result, freed)
        finally:
            self.set_can_close(True)
            app = getattr(self, "_app", None)
            if app is not None and hasattr(app, "end_busy"):
                app.end_busy()
            try:
                history.append(history.make_cleanup_record(self.cplan, result, freed, self.runner.skipped,
                                                           done_ids=self.done_ids(result)))
            except Exception as exc:
                self._append_log(f"Không ghi được lịch sử: {exc}")
            if self.on_finished:
                self.on_finished(result)

    def done_ids(self, result: RemovalResult) -> list[str]:
        """id các mục THẬT SỰ đã dọn — chỉ khi có bằng chứng: đường dẫn đã xoá, bước đã chạy xong, hoặc (bước root
        gộp chung bị lỗi giữa chừng) mọi việc của mục đều có dòng TT-JOB-DONE."""
        failed = set(self.runner.error_tags)
        finished = set(self.runner.done_tags)
        completed = set(result.completed)
        out = []
        for it in self.cplan.items:
            if it.method == "delete":
                if any(p in completed for p in it.paths):
                    out.append(it.id)
                continue
            if it.id in finished and it.id not in failed:
                out.append(it.id)
                continue
            if it.method == "root":
                argv = self.cplan.root_argv.get(it.id, [])
                expect = [f"snap {a[1]} bản {a[3]}" if a[0] == "snap-remove-revision" else a[0] for a in argv]
                if expect and all(e in completed for e in expect):
                    out.append(it.id)
        return out

    def done_titles(self, result: RemovalResult) -> list[str]:
        ids = set(self.done_ids(result))
        return [i.title for i in self.cplan.items if i.id in ids]

    def _show_result(self, result: RemovalResult, freed: int):
        ok = result.success
        self.result_status.set_icon_name("emblem-ok-symbolic" if ok else "dialog-error-symbolic")
        self.result_status.set_title("Đã dọn dẹp xong" if ok else
                                     ("Mới dọn được một phần" if result.partial else "Dọn dẹp không thành công"))
        self.result_status.set_description(GLib.markup_escape_text(display(result.message)))

        def row(title, value):
            r = Adw.ActionRow(title=title, subtitle=GLib.markup_escape_text(display(value) or "—"),
                              subtitle_selectable=True, subtitle_lines=0)
            r.add_css_class("property")
            self.result_details.add(r)

        row("Dung lượng trống tăng thêm", human_size(freed) + " (đo trực tiếp trên ổ đĩa)")
        done_titles = self.done_titles(result)
        row("Đã dọn", ", ".join(done_titles[:30]) or "—")
        if self.runner.errors:
            row("Lỗi", "; ".join(f"{st}: {e}" for st, e in self.runner.errors[:6]))
        if self.runner.skipped:
            row("Bỏ qua khi kiểm tra lại", "; ".join(f"{p} ({why})" for p, why in self.runner.skipped[:10]))
        if not ok:
            row("Mã lỗi", str(result.exit_code))
        self.result_log.set_label(display(result.log))
        self.result_log_exp.set_expanded(not ok)
        self.stack.set_visible_child_name("result")
