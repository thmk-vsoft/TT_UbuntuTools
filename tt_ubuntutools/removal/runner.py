"""Thực thi kế hoạch gỡ bất đồng bộ trong vòng lặp GLib, stream log từng dòng."""

from __future__ import annotations

import os
import time
from typing import Callable

from gi.repository import Gio, GLib

from ..models import RemovalPlan, RemovalResult, Step
from ..utils import clean_env, display
from .helperpath import (DEV_FLAG, HELPER_MARKER, HELPER_MESSAGES, JOB_DONE, SOURCE_HELPER,
                         HelperUnavailable, helper_path)

def step_argv(step: Step) -> list[str]:
    if step.root:
        helper = helper_path()
        dev = [DEV_FLAG] if helper == SOURCE_HELPER else []
        return ["pkexec", helper, *dev, *step.argv]
    return list(step.argv)


class RemovalRunner:
    def __init__(self, plan: RemovalPlan, on_line: Callable[[str], None],
                 on_done: Callable[[RemovalResult], None]):
        self.plan = plan
        self.on_line = on_line
        self.on_done = on_done
        self._log: list[str] = []
        self._steps = self.merge_root_steps(plan.steps)
        self._completed: list[str] = []
        self._cancellable = Gio.Cancellable()
        self._stream = None
        self._buf = b""
        self._proc = None
        self._saw_marker = False
        self._no_agent = False
        self._last_output = 0.0
        self._watchdog = 0
        self._lock_hint_shown = False
        self.running = False

    @staticmethod
    def merge_root_steps(steps: list[Step]) -> list[Step]:
        """Gộp các bước root liên tiếp thành một lần gọi helper (chỉ hỏi mật khẩu một lần)."""
        out: list[Step] = []
        for s in steps:
            if s.root and out and out[-1].root:
                prev = out[-1]
                out[-1] = Step("cmd", prev.argv + ["::"] + s.argv, root=True,
                               label=f"{prev.label}; {s.label}")
            else:
                out.append(Step(s.kind, list(s.argv), s.root, s.label, s.env))
        return out

    # ---- công khai -------------------------------------------------------
    def start(self):
        self.running = True
        if not self._steps:
            self._finish(False, 1, "Không có bước nào để thực hiện.")
            return
        self._next(0)

    # ---- nội bộ ----------------------------------------------------------
    MAX_LINE = 4096  # một dòng output khổng lồ không được làm đơ giao diện / phình lịch sử

    def _emit(self, line: str):
        line = display(line)
        if len(line) > self.MAX_LINE:
            line = line[:self.MAX_LINE] + " …"
        self._log.append(line)
        self.on_line(line)

    def _step_done(self, step: Step):
        if step.kind == "trash":
            self._completed.extend(step.argv)
        else:
            self._completed.append(step.label or step.display())

    def _next(self, idx: int):
        if idx >= len(self._steps):
            self._finish(True, 0, "Gỡ cài đặt thành công.")
            return
        step = self._steps[idx]
        self._emit(f"▶ Bước {idx + 1}/{len(self._steps)}: {step.label or step.display()}")
        if step.kind == "trash":
            for p in step.argv:
                try:
                    Gio.File.new_for_path(p).trash(None)
                    self._emit(f"  Đã chuyển vào Thùng rác: {p}")
                    self._completed.append(p)
                except GLib.Error as exc:
                    self._emit(f"  LỖI khi chuyển {p} vào Thùng rác: {exc.message}")
                    self._finish(False, 1, f"Không chuyển được {display(p)} vào Thùng rác.")
                    return
            GLib.idle_add(lambda: (self._next(idx + 1), False)[1])
            return
        try:
            argv = step_argv(step)
        except HelperUnavailable as exc:
            self._finish(False, 127, str(exc))
            return
        self._emit("$ " + " ".join(argv))
        self._saw_marker = False
        self._no_agent = False
        self._jobs_done: list[str] = []
        self._nonce = ""
        self._cmd_started = False  # helper đã bắt đầu chạy lệnh thay đổi hệ thống ("$ ..." sau marker)
        self._helper_error = ""  # lý do cụ thể helper đưa ra (dòng "LỖI AN TOÀN:" / "LỖI:")
        try:
            launcher = Gio.SubprocessLauncher.new(
                Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_MERGE
            )
            launcher.set_stdin_file_path("/dev/null")  # không bao giờ hỏi mật khẩu trên terminal ẩn
            launcher.set_cwd("/")  # tránh import nhầm module trong thư mục hiện tại (vd. ./pip/)
            env = clean_env()
            for k in ("DISPLAY", "WAYLAND_DISPLAY", "XAUTHORITY", "DBUS_SESSION_BUS_ADDRESS",
                      "XDG_RUNTIME_DIR", "HOME", "USER"):
                if k in os.environ:
                    env[k] = os.environ[k]
            if step.env and not step.root:
                env.update(step.env)
            launcher.set_environ([f"{k}={v}" for k, v in env.items()])
            proc = launcher.spawnv(argv)
        except GLib.Error as exc:
            self._emit(f"LỖI: {exc.message}")
            self._finish(False, 127, f"Không chạy được lệnh: {exc.message}")
            return
        self._proc = proc
        self._stream = proc.get_stdout_pipe()
        self._buf = b""
        self._last_output = time.monotonic()
        self._read_next(idx, step)

    def _lock_watch(self):
        if self._proc is None:
            self._watchdog = 0
            return False
        if self._saw_marker and not self._lock_hint_shown and time.monotonic() - self._last_output > 8:
            self._lock_hint_shown = True
            self._emit("… Đang chờ (có thể apt/dpkg đang bận ở chương trình khác — tối đa 2 phút).")
        return True

    def _read_next(self, idx: int, step: Step):
        # Đọc khối byte thô (EOF = khối rỗng) rồi tự tách dòng; không bao giờ dừng đọc trước EOF,
        # vì đóng pipe sớm có thể làm apt/dpkg chết giữa chừng.
        self._stream.read_bytes_async(65536, GLib.PRIORITY_DEFAULT, None, self._on_read, (idx, step))

    def _on_read(self, src, res, data):
        try:
            chunk = src.read_bytes_finish(res).get_data() or b""
        except GLib.Error as exc:
            self._emit(f"(lỗi đọc output: {exc.message})")
            chunk = b""
        if not chunk:
            if self._buf:
                self._safe_handle(self._buf)
                self._buf = b""
            try:
                src.close(None)
            except GLib.Error:
                pass
            self._proc.wait_async(None, self._on_exit, data)
            return
        self._last_output = time.monotonic()
        self._buf += chunk
        *lines, self._buf = self._buf.split(b"\n")
        for raw in lines:
            self._safe_handle(raw)
        self._read_next(*data)

    def _safe_handle(self, raw: bytes):
        try:
            self._handle_line(raw)
        except Exception as exc:  # không bao giờ ngừng đọc vì lỗi hiển thị
            self._log.append(f"(lỗi hiển thị dòng log: {exc})")

    def _handle_line(self, raw: bytes):
        # Thanh tiến trình dùng \r để vẽ đè: chỉ giữ đoạn cuối cùng có nội dung.
        parts = [p for p in raw.decode("utf-8", "replace").split("\r") if p.strip()]
        text = parts[-1] if parts else ""
        stripped = text.strip()
        if not self._saw_marker and stripped.startswith(HELPER_MARKER):
            # Dòng đầu tiên của helper, in TRƯỚC mọi output lệnh con: mang theo mã nonce bí mật.
            self._saw_marker = True
            self._nonce = stripped[len(HELPER_MARKER):].strip()
            return
        nonce = getattr(self, "_nonce", "")
        if nonce and stripped.startswith(f"{JOB_DONE} {nonce} "):
            parts = stripped.split(" ", 3)
            self._jobs_done.append(parts[3] if len(parts) > 3 else stripped)
            return
        if "No authentication agent found" in text:
            self._no_agent = True
        if self._saw_marker:  # chỉ helper (bước root) in marker
            if stripped.startswith("$ "):
                self._cmd_started = True
            elif stripped.startswith(("LỖI AN TOÀN:", "LỖI:")):
                self._helper_error = stripped.split(":", 1)[1].strip()
        self._emit(text)

    def _on_exit(self, p, res, data):
        idx, step = data
        try:
            p.wait_finish(res)
        except GLib.Error:
            pass
        if self._watchdog:
            GLib.source_remove(self._watchdog)
            self._watchdog = 0
        self._proc = None
        code = p.get_exit_status() if p.get_if_exited() else -1
        if code != 0:
            if step.root and self._jobs_done:
                self._completed.extend(self._jobs_done)  # các hành động helper đã làm xong trước khi lỗi
            self._finish(False, code, self._failure_message(step, code))
            return
        self._step_done(step)
        self._next(idx + 1)

    def _failure_message(self, step: Step, code: int) -> str:
        if step.root and not self._saw_marker:
            if self._no_agent:
                return ("Không có polkit agent để hỏi mật khẩu quản trị. Hãy chạy trong phiên desktop "
                        "có polkit agent (GNOME, KDE, Xfce...) hoặc cài một polkit agent.")
            if code == 126:
                return "Bạn đã huỷ hộp thoại xác thực — chưa gỡ gì."
            if code == 127:
                return "Không được cấp quyền quản trị (xác thực thất bại hoặc bị từ chối) — chưa gỡ gì."
            return f"Không khởi chạy được helper (mã {code}) — chưa gỡ gì."
        reason = getattr(self, "_helper_error", "")
        if step.root and code == 5 and reason:
            return f"Kiểm tra an toàn không đạt — chưa gỡ gì: {reason}"
        if step.root and code == 6 and getattr(self, "_cmd_started", False):
            msg = "Lệnh gỡ bị lỗi giữa chừng — hệ thống có thể đã thay đổi một phần (xem nhật ký)."
            if any(a == "apt-remove" for a in step.argv):
                msg += " Nếu apt/dpkg báo lỗi, hãy chạy 'sudo dpkg --configure -a' trong terminal để hoàn tất."
            return msg
        if step.root and code == 6 and reason:
            return f"Có lỗi trước khi thực hiện — chưa gỡ gì: {reason}"
        if step.root and code in HELPER_MESSAGES:
            return HELPER_MESSAGES[code]
        return f"Lệnh kết thúc với mã lỗi {code}."

    def _finish(self, ok: bool, code: int, message: str):
        self.running = False
        partial = (not ok) and bool(self._completed)
        if partial:
            message += " Một phần đã được thực hiện trước đó: " + ", ".join(self._completed)
        self._emit(("✔ " if ok else "✘ ") + message)
        removed = [a.name for a in self.plan.affected] if ok else list(self._completed)
        self.on_done(RemovalResult(success=ok, exit_code=code, log="\n".join(self._log),
                                   message=message, removed=removed, partial=partial,
                                   completed=list(self._completed)))
