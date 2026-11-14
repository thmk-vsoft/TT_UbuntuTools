"""Vị trí helper quyền quản trị (không phụ thuộc GTK)."""

from __future__ import annotations

import os

INSTALLED_HELPER = "/usr/lib/tt-desktoptool/tt-desktoptool-helper"
SOURCE_HELPER = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "..", "helper", "tt-desktoptool-helper")
)
HELPER_MARKER = "TT-HELPER-START"

HELPER_MESSAGES = {
    2: "Helper từ chối tham số (lỗi nội bộ của công cụ).",
    3: "Helper không chạy được với quyền root.",
    5: "Kiểm tra an toàn trước khi gỡ không đạt — chưa gỡ gì (xem nhật ký).",
    6: "Có lỗi trong lúc thực hiện (xem nhật ký).",
}
JOB_DONE = "TT-JOB-DONE"
DEV_FLAG = "--dev-allow-user-code"


class HelperUnavailable(Exception):
    pass


def _root_owned_chain(path: str) -> bool:
    """File và mọi thư mục cha thuộc root, không cho nhóm/người khác ghi."""
    p = path
    while True:
        try:
            st = os.stat(p)
        except OSError:
            return False
        if st.st_uid != 0 or st.st_mode & 0o022:
            return False
        if p == "/":
            return True
        p = os.path.dirname(p)


def helper_path() -> str:
    """Helper đã cài (thuộc root). Chạy từ mã nguồn chỉ dùng helper trong repo khi đặt TT_DEV_HELPER=1."""
    if os.path.exists(INSTALLED_HELPER):
        if _root_owned_chain(INSTALLED_HELPER):
            return INSTALLED_HELPER
        raise HelperUnavailable(f"{INSTALLED_HELPER} không thuộc root hoặc bị người khác ghi được — từ chối dùng.")
    if os.environ.get("TT_DEV_HELPER") == "1" and os.path.exists(SOURCE_HELPER):
        return SOURCE_HELPER
    raise HelperUnavailable("Chưa cài gói tt-desktoptool nên không có helper quyền quản trị. Hãy cài file .deb "
                            "(hoặc khi phát triển: chạy với TT_DEV_HELPER=1).")


