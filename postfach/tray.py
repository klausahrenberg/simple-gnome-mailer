"""Statusleisten-Symbol (StatusNotifierItem + dbusmenu) – sichtbar mit der AppIndicator-Erweiterung von GNOME."""
import os

import cairo

from gi.repository import Gio, GLib

from . import APP_ID, APP_NAME
from .i18n import _, ngettext

SNI_XML = """<node><interface name="org.kde.StatusNotifierItem">
<property name="Category" type="s" access="read"/><property name="Id" type="s" access="read"/>
<property name="Title" type="s" access="read"/><property name="Status" type="s" access="read"/>
<property name="WindowId" type="u" access="read"/><property name="IconName" type="s" access="read"/>
<property name="IconThemePath" type="s" access="read"/><property name="IconPixmap" type="a(iiay)" access="read"/><property name="OverlayIconName" type="s" access="read"/>
<property name="AttentionIconName" type="s" access="read"/><property name="ItemIsMenu" type="b" access="read"/>
<property name="Menu" type="o" access="read"/><property name="ToolTip" type="(sa(iiay)ss)" access="read"/>
<method name="Activate"><arg type="i" direction="in"/><arg type="i" direction="in"/></method>
<method name="SecondaryActivate"><arg type="i" direction="in"/><arg type="i" direction="in"/></method>
<method name="ContextMenu"><arg type="i" direction="in"/><arg type="i" direction="in"/></method>
<method name="Scroll"><arg type="i" direction="in"/><arg type="s" direction="in"/></method>
<signal name="NewIcon"/><signal name="NewAttentionIcon"/><signal name="NewToolTip"/>
<signal name="NewStatus"><arg type="s"/></signal>
</interface></node>"""

MENU_XML = """<node><interface name="com.canonical.dbusmenu">
<property name="Version" type="u" access="read"/><property name="TextDirection" type="s" access="read"/>
<property name="Status" type="s" access="read"/><property name="IconThemePath" type="as" access="read"/>
<method name="GetLayout"><arg type="i" direction="in"/><arg type="i" direction="in"/><arg type="as" direction="in"/>
<arg type="u" direction="out"/><arg type="(ia{sv}av)" direction="out"/></method>
<method name="GetGroupProperties"><arg type="ai" direction="in"/><arg type="as" direction="in"/>
<arg type="a(ia{sv})" direction="out"/></method>
<method name="GetProperty"><arg type="i" direction="in"/><arg type="s" direction="in"/><arg type="v" direction="out"/></method>
<method name="Event"><arg type="i" direction="in"/><arg type="s" direction="in"/><arg type="v" direction="in"/><arg type="u" direction="in"/></method>
<method name="EventGroup"><arg type="a(isvu)" direction="in"/><arg type="ai" direction="out"/></method>
<method name="AboutToShow"><arg type="i" direction="in"/><arg type="b" direction="out"/></method>
<method name="AboutToShowGroup"><arg type="ai" direction="in"/><arg type="ai" direction="out"/><arg type="ai" direction="out"/></method>
<signal name="LayoutUpdated"><arg type="u"/><arg type="i"/></signal>
<signal name="ItemsPropertiesUpdated"><arg type="a(ia{sv})"/><arg type="a(ia{sv})"/></signal>
</interface></node>"""

SIZE = 64
_pixmaps = {}


PLANE = [(1.5, 7.2), (14.5, 1.6), (11.6, 14.0), (8.2, 10.6), (6.4, 13.2), (6.2, 9.6)]
OUTLINE = (0.93, 0.93, 0.93)    # monochrom, passend zur dunklen GNOME-Leiste
SIGNAL = (0.34, 0.89, 0.54)     # dezentes Grün (GNOME-Palette) bei neuen Nachrichten


def pixmap(n):
    """Papierflieger als ARGB32 (Netzwerk-Byte-Reihenfolge): Umriss ohne, gefüllt mit Signalfarbe mit ungelesenen Mails."""
    key = n > 0
    if key in _pixmaps:
        return _pixmaps[key]
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, SIZE, SIZE)
    ctx = cairo.Context(surf)
    ctx.scale(SIZE / 16, SIZE / 16)
    ctx.set_line_join(cairo.LINE_JOIN_ROUND)
    ctx.set_line_cap(cairo.LINE_CAP_ROUND)
    ctx.move_to(*PLANE[0])
    for pt in PLANE[1:]:
        ctx.line_to(*pt)
    ctx.close_path()
    if key:
        ctx.set_source_rgb(*SIGNAL)
        ctx.fill_preserve()
        ctx.set_line_width(0.6)
        ctx.stroke()
        ctx.set_source_rgba(0.1, 0.45, 0.25, 0.55)     # Faltkante
    else:
        ctx.set_source_rgb(*OUTLINE)
        ctx.set_line_width(1.1)
        ctx.stroke()
        ctx.set_source_rgb(*OUTLINE)
    ctx.set_line_width(0.9 if not key else 0.7)
    ctx.move_to(14.5, 1.6)
    ctx.line_to(6.2, 9.6)
    ctx.stroke()
    surf.flush()
    raw, stride = bytes(surf.get_data()), surf.get_stride()
    out = bytearray()
    for y in range(SIZE):
        for x in range(SIZE):
            o = y * stride + x * 4
            b, g, r, a = raw[o:o + 4]
            if a:
                r, g, b = min(255, r * 255 // a), min(255, g * 255 // a), min(255, b * 255 // a)
            out += bytes((a, r, g, b))
    _pixmaps[key] = [(SIZE, SIZE, bytes(out))]
    return _pixmaps[key]


SNI_PATH, MENU_PATH = "/StatusNotifierItem", "/StatusNotifierMenu"
ICONS = os.path.join(os.path.dirname(__file__), "icons")


class Tray:
    def __init__(self, on_open, on_compose, on_quit):
        self.on_open, self.on_compose, self.on_quit = on_open, on_compose, on_quit
        self.unread = 0
        self.conn = None
        self.items = [(1, _("Open {app}").format(app=APP_NAME), on_open), (2, _("New message"), on_compose),
                      (3, None, None), (4, _("Quit"), on_quit)]

    # ------------------------------------------------------------ Start
    def start(self):
        try:
            self.conn = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            self.name = f"org.kde.StatusNotifierItem-{os.getpid()}-1"
            Gio.bus_own_name_on_connection(self.conn, self.name, Gio.BusNameOwnerFlags.NONE, None, None)
            sni = Gio.DBusNodeInfo.new_for_xml(SNI_XML).interfaces[0]
            menu = Gio.DBusNodeInfo.new_for_xml(MENU_XML).interfaces[0]
            self.conn.register_object(SNI_PATH, sni, self._sni_call, self._sni_prop, None)
            self.conn.register_object(MENU_PATH, menu, self._menu_call, self._menu_prop, None)
            Gio.bus_watch_name_on_connection(self.conn, "org.kde.StatusNotifierWatcher", Gio.BusNameWatcherFlags.NONE,
                                             self._register, None)
        except GLib.Error:
            self.conn = None

    def _register(self, conn, name, owner):
        conn.call(name, "/StatusNotifierWatcher", "org.kde.StatusNotifierWatcher", "RegisterStatusNotifierItem",
                  GLib.Variant("(s)", (self.name,)), None, Gio.DBusCallFlags.NONE, -1, None, lambda *a: None)

    # ------------------------------------------------------------ Zustand
    def set_unread(self, n):
        if n == self.unread or not self.conn:
            self.unread = n
            return
        self.unread = n
        for sig, args in (("NewStatus", GLib.Variant("(s)", (self._status(),))), ("NewIcon", None),
                          ("NewAttentionIcon", None), ("NewToolTip", None)):
            self.conn.emit_signal(None, SNI_PATH, "org.kde.StatusNotifierItem", sig, args)

    def _status(self):
        return "Active"

    # ------------------------------------------------------------ SNI
    def _sni_prop(self, conn, sender, path, iface, prop):
        n = self.unread
        tip = (ngettext("{n} unread message", "{n} unread messages", n).format(n=n) if n else _("No unread messages"))
        table = {
            "Category": GLib.Variant("s", "Communications"), "Id": GLib.Variant("s", APP_ID),
            "Title": GLib.Variant("s", APP_NAME), "Status": GLib.Variant("s", self._status()),
            "WindowId": GLib.Variant("u", 0),
            "IconName": GLib.Variant("s", ""), "IconPixmap": GLib.Variant("a(iiay)", pixmap(n)),
            "IconThemePath": GLib.Variant("s", ICONS), "OverlayIconName": GLib.Variant("s", ""),
            "AttentionIconName": GLib.Variant("s", ""), "ItemIsMenu": GLib.Variant("b", False),
            "Menu": GLib.Variant("o", MENU_PATH),
            "ToolTip": GLib.Variant("(sa(iiay)ss)", (APP_ID, [], APP_NAME, tip)),
        }
        return table.get(prop)

    def _sni_call(self, conn, sender, path, iface, method, params, inv):
        if method in ("Activate", "SecondaryActivate"):
            self.on_open()
        inv.return_value(None)

    # ------------------------------------------------------------ Menü
    def _menu_prop(self, conn, sender, path, iface, prop):
        return {"Version": GLib.Variant("u", 3), "TextDirection": GLib.Variant("s", "ltr"),
                "Status": GLib.Variant("s", "normal"), "IconThemePath": GLib.Variant("as", [])}.get(prop)

    def _props(self, item):
        _id, label, _cb = item
        if label is None:
            return {"type": GLib.Variant("s", "separator")}
        return {"label": GLib.Variant("s", label), "enabled": GLib.Variant("b", True), "visible": GLib.Variant("b", True)}

    def _menu_call(self, conn, sender, path, iface, method, params, inv):
        if method == "GetLayout":
            kids = [GLib.Variant("(ia{sv}av)", (it[0], self._props(it), [])) for it in self.items]
            root = (0, {"children-display": GLib.Variant("s", "submenu")}, kids)
            inv.return_value(GLib.Variant("(u(ia{sv}av))", (1, root)))
        elif method == "GetGroupProperties":
            inv.return_value(GLib.Variant("(a(ia{sv}))", ([(it[0], self._props(it)) for it in self.items],)))
        elif method == "GetProperty":
            inv.return_value(GLib.Variant("(v)", (GLib.Variant("s", ""),)))
        elif method == "Event":
            item_id, event = params.unpack()[:2]
            if event == "clicked":
                for it in self.items:
                    if it[0] == item_id and it[2]:
                        GLib.idle_add(lambda cb=it[2]: cb() and False)
            inv.return_value(None)
        elif method == "EventGroup":
            inv.return_value(GLib.Variant("(ai)", ([],)))
        elif method == "AboutToShow":
            inv.return_value(GLib.Variant("(b)", (False,)))
        elif method == "AboutToShowGroup":
            inv.return_value(GLib.Variant("(aiai)", ([], [])))
        else:
            inv.return_value(None)
