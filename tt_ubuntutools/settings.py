"""Cài đặt nhỏ của người dùng: JSON trong ~/.config/tt-desktoptool/settings.json (hỏng / thiếu -> mặc định)."""

from __future__ import annotations

import json
import os


def settings_path() -> str:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(base, "tt-desktoptool", "settings.json")


def load(path: str | None = None) -> dict:
    try:
        with open(path or settings_path(), encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def get(key: str, default=None, path: str | None = None):
    return load(path).get(key, default)


def set_value(key: str, value, path: str | None = None) -> bool:
    """Ghi một giá trị (ghi tạm rồi đổi tên — không làm hỏng tệp nếu bị ngắt giữa chừng). False nếu không ghi được."""
    path = path or settings_path()
    data = load(path)
    data[key] = value
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=1)
        os.replace(tmp, path)
        return True
    except OSError:
        return False
