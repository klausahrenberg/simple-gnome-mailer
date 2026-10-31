"""Klappbare Baumansicht der Postfächer (Hamburger-Menü)."""
import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gio, GObject, Gtk, Pango

from .. import config


class NavNode(GObject.Object):
    count = GObject.Property(type=int, default=0)     # ungelesen
    total = GObject.Property(type=int, default=0)     # alle Nachrichten

    def __init__(self, key, label, icon=None, view=None, count=0, color=None, children=None, bold=False, total=0):
        super().__init__()
        self.key, self.label, self.icon, self.view, self.color, self.bold = key, label, icon, view, color, bold
        self.count = count
        self.total = total
        self.kids = children or []


def _flatten(nodes, depth=0):
    for n in nodes:
        yield n, depth
        yield from _flatten(n.kids, depth + 1)


class NavTree:
    def __init__(self, on_view):
        self.on_view = on_view
        self.current = {"kind": "unified"}
        self._guard = False
        self._sig = None
        self._nodes = []
        saved = config.get("nav_expanded")
        self._expanded = set(saved) if saved is not None else None   # None: Konten zunächst aufgeklappt

        f = Gtk.SignalListItemFactory()
        f.connect("setup", self._setup)
        f.connect("bind", self._bind)
        f.connect("unbind", self._unbind)
        self.view = Gtk.ListView(factory=f, single_click_activate=False)
        self.view.add_css_class("navigation-sidebar")
        self.sel = None

    # ------------------------------------------------------------ Zeilen
    def _setup(self, _f, li):
        exp = Gtk.TreeExpander()
        box = Gtk.Box(spacing=10, margin_top=6, margin_bottom=6, margin_end=6)
        box.icon = Gtk.Image()
        box.dot = Gtk.Box(valign=Gtk.Align.CENTER, halign=Gtk.Align.CENTER)
        box.dot.add_css_class("acc-dot")
        box.label = Gtk.Label(xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.END)
        box.count = Gtk.Label()          # ungelesen: hervorgehoben
        box.count.add_css_class("caption-heading")
        box.count.add_css_class("accent")
        box.total = Gtk.Label()          # alle Nachrichten: gedämpft
        box.total.add_css_class("dim-label")
        box.total.add_css_class("caption")
        for w in (box.icon, box.dot, box.label, box.count, box.total):
            box.append(w)
        exp.set_child(box)
        li.set_child(exp)

    def _bind(self, _f, li):
        row, exp = li.get_item(), li.get_child()
        node, box = row.get_item(), exp.get_child()
        exp.set_list_row(row)
        box.label.set_text(node.label)
        (box.label.add_css_class if node.bold else box.label.remove_css_class)("heading")
        box.icon.set_visible(bool(node.icon) and node.color is None)
        if node.icon:
            box.icon.set_from_icon_name(node.icon)
        for c in [c for c in box.dot.get_css_classes() if c.startswith("acc-color-")]:
            box.dot.remove_css_class(c)
        box.dot.set_visible(node.color is not None)
        if node.color is not None:
            box.dot.add_css_class(f"acc-color-{node.color}")

        def upd(*_):
            box.count.set_text(str(node.count) if node.count else "")
            box.total.set_text(str(node.total) if node.total else "")

        upd()
        box._node, box._cid = node, (node.connect("notify::count", upd), node.connect("notify::total", upd))
        exp._rid = row.connect("notify::expanded", lambda r, _p, key=node.key: self._on_expanded(key, r.get_expanded()))

    def _unbind(self, _f, li):
        exp = li.get_child()
        box = exp.get_child()
        if getattr(box, "_node", None):
            for cid in box._cid:
                box._node.disconnect(cid)
            box._node = None
        row = li.get_item()
        if row is not None and getattr(exp, "_rid", None):
            row.disconnect(exp._rid)
            exp._rid = None

    def _on_expanded(self, key, value):
        if self._guard or self._expanded is None:
            return
        (self._expanded.add if value else self._expanded.discard)(key)
        config.set("nav_expanded", sorted(self._expanded))

    # ------------------------------------------------------------ Aufbau
    def set_nodes(self, nodes):
        """Struktur ersetzen; bei gleicher Struktur nur die Zähler aktualisieren."""
        flat = list(_flatten(nodes))
        sig = tuple((n.key, n.label, d, n.color) for n, d in flat)
        if sig == self._sig:
            old = {n.key: n for n, _ in _flatten(self._nodes)}
            for n, _ in flat:
                if n.key in old:
                    if old[n.key].count != n.count:
                        old[n.key].count = n.count
                    if old[n.key].total != n.total:
                        old[n.key].total = n.total
            return
        self._sig, self._nodes = sig, nodes
        if self._expanded is None:
            self._expanded = {n.key for n in nodes if n.kids}      # Standard: Konten offen
        root = Gio.ListStore.new(NavNode)
        for n in nodes:
            root.append(n)

        def children(node):
            if not node.kids:
                return None
            st = Gio.ListStore.new(NavNode)
            for k in node.kids:
                st.append(k)
            return st

        tree = Gtk.TreeListModel.new(root, False, False, children)
        self._guard = True
        try:
            i = 0
            while i < tree.get_n_items():          # gespeicherten Zustand wiederherstellen
                r = tree.get_row(i)
                if r.get_item().key in self._expanded:
                    r.set_expanded(True)
                i += 1
        finally:
            self._guard = False
        self.sel = Gtk.SingleSelection(model=tree, autoselect=False, can_unselect=True)
        self.sel.connect("selection-changed", self._changed)
        self.view.set_model(self.sel)
        self.select_view(self.current)

    def _changed(self, sel, *_):
        if self._guard:
            return
        row = sel.get_selected_item()
        if row is None:
            return
        node = row.get_item()
        if node.view:
            self.current = dict(node.view)
            self.on_view(node.view)
        else:                                       # Konto-/Verzeichniszeile: auf-/zuklappen
            row.set_expanded(not row.get_expanded())
            self.select_view(self.current)

    @staticmethod
    def _same(a, b):
        return all(a.get(k) == b.get(k) for k in ("kind", "account_id", "folder"))

    def find_view(self, view):
        for n, _ in _flatten(self._nodes):
            if n.view and self._same(n.view, view):
                return n.view
        return None

    def select_view(self, view):
        self.current = dict(view)
        if not self.sel:
            return
        self._guard = True
        try:
            model = self.sel.get_model()
            for i in range(model.get_n_items()):
                n = model.get_row(i).get_item()
                if n.view and self._same(n.view, view):
                    self.sel.set_selected(i)
                    return
            self.sel.set_selected(Gtk.INVALID_LIST_POSITION)
        finally:
            self._guard = False
