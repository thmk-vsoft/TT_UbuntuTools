"""Tiện ích giao diện dùng chung."""

from __future__ import annotations

import os

from gi.repository import Gdk, GLib, GObject, Gtk

from ..models import SoftwareItem, Source

FALLBACK_ICONS = {
    Source.APT: "package-x-generic",
    Source.THIRD_PARTY: "package-x-generic",
    Source.DEB: "package-x-generic",
    Source.SNAP: "package-x-generic",
    Source.FLATPAK: "package-x-generic",
    Source.APPIMAGE: "application-x-executable",
    Source.PIP: "text-x-python",
    Source.NPM: "text-x-script",
    Source.MANUAL: "folder",
}

SOURCE_ICONS = {
    "all": "view-grid-symbolic",
    Source.APT.code: "system-software-install-symbolic",
    Source.THIRD_PARTY.code: "network-server-symbolic",
    Source.DEB.code: "document-save-symbolic",
    Source.SNAP.code: "package-x-generic-symbolic",
    Source.FLATPAK.code: "package-x-generic-symbolic",
    Source.APPIMAGE.code: "application-x-executable-symbolic",
    Source.PIP.code: "text-x-generic-symbolic",
    Source.NPM.code: "text-x-generic-symbolic",
    Source.MANUAL.code: "folder-symbolic",
}

_texture_cache: dict[str, Gdk.Texture | None] = {}


class ItemObject(GObject.Object):
    """Bọc SoftwareItem để đưa vào Gio.ListStore."""

    __gtype_name__ = "TTItemObject"

    def __init__(self, item: SoftwareItem):
        super().__init__()
        self.item = item


def _icon_theme() -> Gtk.IconTheme:
    return Gtk.IconTheme.get_for_display(Gdk.Display.get_default())


def set_item_icon(image: Gtk.Image, item: SoftwareItem, size: int = 32) -> None:
    image.set_pixel_size(size)
    icon = item.icon or ""
    if icon.startswith("/"):
        tex = _texture_cache.get(icon, False)
        if tex is False:
            tex = None
            if os.path.isfile(icon):
                try:
                    tex = Gdk.Texture.new_from_filename(icon)
                except Exception:
                    tex = None
            _texture_cache[icon] = tex
        if tex is not None:
            image.set_from_paintable(tex)
            return
        icon = ""
    if icon:
        theme = _icon_theme()
        for cand in (icon, os.path.splitext(icon)[0]):
            if theme.has_icon(cand):
                image.set_from_icon_name(cand)
                return
    image.set_from_icon_name(FALLBACK_ICONS.get(item.source, "application-x-executable"))


def badge(source: Source) -> Gtk.Label:
    lbl = Gtk.Label(label=source.label)
    lbl.add_css_class("badge")
    lbl.add_css_class(source.css_class)
    lbl.set_halign(Gtk.Align.START)
    lbl.set_valign(Gtk.Align.CENTER)
    return lbl


def set_badge(lbl: Gtk.Label, source: Source) -> None:
    for s in Source:
        lbl.remove_css_class(s.css_class)
    lbl.set_label(source.label)
    lbl.add_css_class(source.css_class)


CSS = """
.badge {
  border-radius: 999px;
  padding: 1px 9px;
  font-size: 0.82em;
  font-weight: 600;
}
.src-apt        { background: alpha(#e95420, 0.16); color: mix(#e95420, @window_fg_color, 0.25); }
.src-thirdparty { background: alpha(#3584e4, 0.16); color: mix(#3584e4, @window_fg_color, 0.25); }
.src-deb        { background: alpha(#c64600, 0.16); color: mix(#c64600, @window_fg_color, 0.30); }
.src-snap       { background: alpha(#26a269, 0.16); color: mix(#26a269, @window_fg_color, 0.30); }
.src-flatpak    { background: alpha(#1c71d8, 0.16); color: mix(#1c71d8, @window_fg_color, 0.30); }
.src-appimage   { background: alpha(#9141ac, 0.16); color: mix(#9141ac, @window_fg_color, 0.25); }
.src-pip        { background: alpha(#e5a50a, 0.18); color: mix(#a17200, @window_fg_color, 0.30); }
.src-npm        { background: alpha(#c01c28, 0.16); color: mix(#c01c28, @window_fg_color, 0.30); }
.src-manual     { background: alpha(#77767b, 0.18); color: mix(#77767b, @window_fg_color, 0.40); }

.summary-bar { padding: 6px 12px; }
.protected-box {
  background: alpha(@warning_bg_color, 0.18);
  border-radius: 12px;
  padding: 10px 12px;
}
.blocked-box {
  background: alpha(@error_bg_color, 0.15);
  border-radius: 12px;
  padding: 10px 12px;
}
.warning-box {
  background: alpha(@warning_bg_color, 0.15);
  border-radius: 12px;
  padding: 10px 12px;
}
.log-view { font-family: monospace; font-size: 0.9em; padding: 8px; }
.sidebar-count { font-size: 0.85em; }
columnview > listview > row { padding-top: 2px; padding-bottom: 2px; }
"""


def load_css() -> None:
    provider = Gtk.CssProvider()
    provider.load_from_string(CSS)
    Gtk.StyleContext.add_provider_for_display(
        Gdk.Display.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
    )


# ---------------------------------------------------------------- tương thích libadwaita 1.5+

def make_spinner(size: int = 16) -> Gtk.Widget:
    """Adw.Spinner (libadwaita ≥ 1.6) hoặc Gtk.Spinner trên bản cũ (Ubuntu 24.04)."""
    from gi.repository import Adw

    if hasattr(Adw, "Spinner"):
        sp = Adw.Spinner()
    else:
        sp = Gtk.Spinner(spinning=True)
    sp.set_size_request(size, size)
    return sp


def make_toast(title: str, timeout: int = 4):
    """Toast hiển thị chữ thuần (không phân tích markup Pango)."""
    from gi.repository import Adw

    from ..utils import display

    t = Adw.Toast(title=display(title), timeout=timeout)
    try:
        t.set_use_markup(False)
    except AttributeError:
        t.set_title(GLib.markup_escape_text(display(title)))
    return t


class ScopeSwitcher(Gtk.Box):
    """Chọn 'Ứng dụng' / 'Tất cả gói'. Dùng Adw.ToggleGroup (≥ 1.7) hoặc hai Gtk.ToggleButton liên kết."""

    __gsignals__ = {"changed": (GObject.SignalFlags.RUN_FIRST, None, (str,))}

    def __init__(self, options: list[tuple[str, str, str]], active: str):
        super().__init__()
        from gi.repository import Adw

        self._active = active
        if hasattr(Adw, "ToggleGroup"):
            grp = Adw.ToggleGroup()
            for name, label, tip in options:
                grp.add(Adw.Toggle(name=name, label=label, tooltip=tip))
            grp.set_active_name(active)
            grp.connect("notify::active-name", lambda g, _p: self._set(g.get_active_name()))
            self.append(grp)
        else:
            self.add_css_class("linked")
            first = None
            for name, label, tip in options:
                b = Gtk.ToggleButton(label=label, tooltip_text=tip)
                if first is None:
                    first = b
                else:
                    b.set_group(first)
                b.set_active(name == active)
                b.connect("toggled", lambda btn, n=name: btn.get_active() and self._set(n))
                self.append(b)

    def _set(self, name: str):
        if name and name != self._active:
            self._active = name
            self.emit("changed", name)

    def get_active_name(self) -> str:
        return self._active
