"""Hộp thoại lịch sử các lần gỡ."""

from __future__ import annotations

import datetime as dt

from gi.repository import Adw, GLib, Gtk, Pango

from .. import history
from ..models import human_size


def _fmt_time(s: str) -> str:
    try:
        return dt.datetime.fromisoformat(s).strftime("%d/%m/%Y %H:%M:%S")
    except (TypeError, ValueError):
        return s or "?"


class HistoryDialog(Adw.Dialog):
    def __init__(self):
        super().__init__(title="Lịch sử gỡ cài đặt & dọn dẹp", content_width=640, content_height=620)
        tv = Adw.ToolbarView()
        header = Adw.HeaderBar()
        self.clear_btn = Gtk.Button(icon_name="user-trash-symbolic", tooltip_text="Xoá lịch sử")
        self.clear_btn.connect("clicked", self._on_clear)
        header.pack_start(self.clear_btn)
        tv.add_top_bar(header)
        self.stack = Gtk.Stack()
        tv.set_content(self.stack)
        self.set_child(tv)

        empty = Adw.StatusPage(icon_name="document-open-recent-symbolic", title="Chưa có lịch sử",
                               description="Các lần gỡ phần mềm sẽ được ghi lại ở đây.")
        self.stack.add_named(empty, "empty")

        sc = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        self.page = Adw.PreferencesPage()
        sc.set_child(self.page)
        self.stack.add_named(sc, "list")
        self.group: Adw.PreferencesGroup | None = None
        self._load()

    def _load(self):
        try:
            self._load_inner()
        except Exception as exc:  # lịch sử hỏng không được làm hỏng hộp thoại (nút Xoá vẫn dùng được)
            if self.group:
                self.page.remove(self.group)
            self.group = Adw.PreferencesGroup(title="Không đọc được lịch sử",
                                              description=GLib.markup_escape_text(
                                                  f"{type(exc).__name__}: {exc}. Bạn có thể xoá lịch sử."))
            self.page.add(self.group)
            self.clear_btn.set_sensitive(True)
            self.stack.set_visible_child_name("list")

    def _load_inner(self):
        records = history.load()
        if self.group:
            self.page.remove(self.group)
        total_freed = sum(int(r.get("freed_bytes") or 0) for r in records
                          if r.get("success") or (r.get("source") == "cleanup" and r.get("partial")))
        ok = sum(1 for r in records if r.get("success"))
        self.group = Adw.PreferencesGroup(
            title=f"{len(records)} lần gỡ / dọn dẹp",
            description=f"{ok} thành công, {len(records) - ok} thất bại · đã giải phóng {human_size(total_freed)}")
        self.page.add(self.group)
        for r in records:
            success = r.get("success")
            exp = Adw.ExpanderRow(
                title=GLib.markup_escape_text(f"{r.get('name', '?')} — {r.get('source_label', '')}"),
                subtitle=GLib.markup_escape_text(
                    f"{_fmt_time(r.get('time'))} · "
                    + ("Thành công" if success else ("Một phần" if r.get("partial") else "Thất bại"))
                    + (f" · giải phóng {human_size(r.get('freed_bytes'))}"
                       if (success or r.get("partial")) and r.get("freed_bytes") else "")),
            )
            icon = Gtk.Image.new_from_icon_name("emblem-ok-symbolic" if success else "dialog-error-symbolic")
            icon.add_css_class("success" if success else "error")
            exp.add_prefix(icon)
            opts = [str(x) for x in r.get("option_labels") or [] if isinstance(x, str)]
            if not opts and "option_labels" not in r:  # bản ghi cũ
                if r.get("purge"):
                    opts.append("purge")
                if r.get("autoremove"):
                    opts.append("autoremove")
            if r.get("kept"):
                opts.append("giữ lại: " + ", ".join(str(x) for x in r.get("kept") if isinstance(x, str)))
            detail = (
                f"Phiên bản: {r.get('version') or '—'}\n"
                f"Tuỳ chọn: {', '.join(opts) or 'không'}\n"
                f"Bị gỡ: {', '.join(r.get('affected') or []) or '—'}\n"
                f"Kết quả: {r.get('message', '')} (mã {r.get('exit_code')})\n"
                + ({True: "Kiểm tra lại: lối tắt và lệnh khởi chạy đã được gỡ (thư mục chương trình vẫn còn)\n",
                    False: "Kiểm tra lại: VẪN còn trên máy\n"} if r.get("kept") else
                   {True: "Kiểm tra lại: đã không còn trên máy\n", False: "Kiểm tra lại: VẪN còn trên máy\n"})
                .get(r.get("verified"), "")
                + "\n" + (r.get("log_tail") or "")
            )
            lbl = Gtk.Label(label=detail, xalign=0, wrap=True, selectable=True,
                            wrap_mode=Pango.WrapMode.WORD_CHAR, margin_start=12, margin_end=12,
                            margin_top=8, margin_bottom=8)
            lbl.add_css_class("log-view")
            exp.add_row(lbl)
            self.group.add(exp)
        self.clear_btn.set_sensitive(bool(records))
        self.stack.set_visible_child_name("list" if records else "empty")

    def _on_clear(self, *_):
        dlg = Adw.AlertDialog(heading="Xoá toàn bộ lịch sử?",
                              body="Thao tác này chỉ xoá nhật ký, không ảnh hưởng phần mềm đã cài.")
        dlg.add_response("cancel", "Huỷ")
        dlg.add_response("clear", "Xoá")
        dlg.set_response_appearance("clear", Adw.ResponseAppearance.DESTRUCTIVE)

        def on_resp(_d, resp):
            if resp == "clear":
                history.clear()
                self._load()

        dlg.connect("response", on_resp)
        dlg.present(self)
