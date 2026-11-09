"""Bảng danh sách phần mềm (Gtk.ColumnView) với lọc + sắp xếp."""

from __future__ import annotations

from gi.repository import Gio, GObject, Gtk, Pango

from ..models import SoftwareItem, format_date, human_size
from .common import ItemObject, badge, set_badge, set_item_icon


def _cmp(a, b) -> int:
    if a == b:
        return 0
    return -1 if a < b else 1


def _key_name(it: SoftwareItem):
    return it.title.casefold()


def _key_size(it: SoftwareItem):
    return it.size if it.size is not None else -1


def _key_date(it: SoftwareItem):
    return it.install_date.timestamp() if it.install_date else 0


class SoftwareList(Gtk.ScrolledWindow):
    __gsignals__ = {
        "item-selected": (GObject.SignalFlags.RUN_FIRST, None, (object,)),
        "item-activated": (GObject.SignalFlags.RUN_FIRST, None, (object,)),
    }

    def __init__(self):
        super().__init__(hexpand=True, vexpand=True)
        self.store = Gio.ListStore(item_type=ItemObject)
        self.filter = Gtk.CustomFilter.new(self._filter_func)
        self.filter_model = Gtk.FilterListModel(model=self.store, filter=self.filter)
        self.sort_model = Gtk.SortListModel(model=self.filter_model)
        self.selection = Gtk.SingleSelection(model=self.sort_model, autoselect=False, can_unselect=True)
        self.selection.connect("notify::selected-item", self._on_selected)

        self.view = Gtk.ColumnView(model=self.selection, show_row_separators=False,
                                   reorderable=False)
        self.view.add_css_class("data-table")
        self.view.connect("activate", self._on_activate)
        self.sort_model.set_sorter(self.view.get_sorter())
        self.set_child(self.view)

        # Trạng thái lọc
        self.source_code: str = "all"
        self.apps_only: bool = True
        self.query: str = ""

        self.name_col = self._add_column("Tên", self._setup_name, self._bind_name, _key_name, expand=True,
                                         fixed=250)
        self._add_column("Phương thức", self._setup_badge, self._bind_badge,
                         lambda it: it.source.label, fixed=165)
        self._add_column("Phiên bản", self._setup_label, self._bind_text(lambda it: it.version or "—"),
                         lambda it: it.version, fixed=130)
        self._add_column("Dung lượng", self._setup_label_right,
                         self._bind_text(lambda it: human_size(it.size)), _key_size, fixed=95)
        self._add_column("Ngày cài", self._setup_label, self._bind_text(lambda it: format_date(it.install_date)),
                         _key_date, fixed=95)
        self._add_column("Nguồn kho", self._setup_label, self._bind_text(lambda it: it.origin or "—", tooltip=True),
                         lambda it: it.origin.casefold(), fixed=200)
        self.view.sort_by_column(self.name_col, Gtk.SortType.ASCENDING)

    # ---- cột ---------------------------------------------------------------
    def _add_column(self, title, setup, bind, key, expand=False, fixed=None):
        factory = Gtk.SignalListItemFactory()
        factory.connect("setup", setup)
        factory.connect("bind", bind)
        col = Gtk.ColumnViewColumn(title=title, factory=factory, resizable=True, expand=expand)
        if fixed:
            col.set_fixed_width(fixed)
        col.set_sorter(Gtk.CustomSorter.new(
            lambda a, b, _d: _cmp(key(a.item), key(b.item)), None))
        self.view.append_column(col)
        return col

    @staticmethod
    def _setup_name(_f, li):
        box = Gtk.Box(spacing=10, margin_top=3, margin_bottom=3)
        img = Gtk.Image()
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER)
        title_box = Gtk.Box(spacing=6)
        title = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END)
        title.add_css_class("heading")
        lock = Gtk.Image.new_from_icon_name("changes-prevent-symbolic")
        lock.add_css_class("dim-label")
        lock.set_tooltip_text("Được bảo vệ — không thể gỡ")
        title_box.append(title)
        title_box.append(lock)
        sub = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END)
        sub.add_css_class("dim-label")
        sub.add_css_class("caption")
        vbox.append(title_box)
        vbox.append(sub)
        box.append(img)
        box.append(vbox)
        li.set_child(box)
        li._w = (img, title, lock, sub)

    @staticmethod
    def _bind_name(_f, li):
        it: SoftwareItem = li.get_item().item
        img, title, lock, sub = li._w
        set_item_icon(img, it, 32)
        title.set_label(it.title)
        lock.set_visible(bool(it.protected_reason))
        text = it.summary or ""
        if it.title != it.name:
            text = f"{it.name} — {text}" if text else it.name
        sub.set_label(text)
        sub.set_tooltip_text(text or None)

    @staticmethod
    def _setup_badge(_f, li):
        from ..models import Source

        lbl = badge(Source.APT)
        li.set_child(lbl)

    @staticmethod
    def _bind_badge(_f, li):
        set_badge(li.get_child(), li.get_item().item.source)

    @staticmethod
    def _setup_label(_f, li):
        li.set_child(Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END))

    @staticmethod
    def _setup_label_right(_f, li):
        lbl = Gtk.Label(xalign=1, ellipsize=Pango.EllipsizeMode.END)
        lbl.add_css_class("numeric")
        li.set_child(lbl)

    @staticmethod
    def _bind_text(fn, tooltip=False):
        def bind(_f, li):
            text = fn(li.get_item().item)
            lbl = li.get_child()
            lbl.set_label(text)
            if tooltip:
                lbl.set_tooltip_text(text)
        return bind

    # ---- dữ liệu & lọc -------------------------------------------------------
    def set_items(self, items: list[SoftwareItem]):
        selected = self.selected_item()
        self.store.splice(0, self.store.get_n_items(), [ItemObject(i) for i in items])
        if selected:
            self.select_key(selected.key)

    def matches(self, it: SoftwareItem, ignore_source: bool = False) -> bool:
        if not ignore_source and self.source_code != "all" and it.source.code != self.source_code:
            return False
        if self.apps_only and not it.visible_as_app:
            return False
        if self.query:
            hay = f"{it.title} {it.name} {it.summary} {it.origin}".casefold()
            if not all(tok in hay for tok in self.query.split()):
                return False
        return True

    def _filter_func(self, obj) -> bool:
        return self.matches(obj.item)

    def refilter(self):
        self.filter.changed(Gtk.FilterChange.DIFFERENT)

    def visible_items(self) -> list[SoftwareItem]:
        return [self.filter_model.get_item(i).item for i in range(self.filter_model.get_n_items())]

    def all_items(self) -> list[SoftwareItem]:
        return [self.store.get_item(i).item for i in range(self.store.get_n_items())]

    # ---- chọn ---------------------------------------------------------------
    def selected_item(self) -> SoftwareItem | None:
        obj = self.selection.get_selected_item()
        return obj.item if obj else None

    def select_key(self, key: str) -> bool:
        for i in range(self.sort_model.get_n_items()):
            if self.sort_model.get_item(i).item.key == key:
                self.selection.set_selected(i)
                return True
        self.selection.set_selected(Gtk.INVALID_LIST_POSITION)
        return False

    def _on_selected(self, *_):
        self.emit("item-selected", self.selected_item())

    def _on_activate(self, _view, pos):
        obj = self.sort_model.get_item(pos)
        if obj:
            self.emit("item-activated", obj.item)
