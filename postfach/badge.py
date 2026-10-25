"""Zähler am Programmsymbol: Unity-LauncherEntry-Schnittstelle (Dash to Dock, Ubuntu Dock, Plank u. a.)."""
from gi.repository import Gio, GLib

from . import APP_ID

_last = None


def set_count(n):
    global _last
    if n == _last:
        return
    _last = n
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        props = GLib.Variant("a{sv}", {"count": GLib.Variant("x", int(n)), "count-visible": GLib.Variant("b", n > 0)})
        bus.emit_signal(None, "/com/canonical/unity/launcherentry/1", "com.canonical.Unity.LauncherEntry", "Update",
                        GLib.Variant.new_tuple(GLib.Variant("s", f"application://{APP_ID}.desktop"), props))
    except GLib.Error:
        pass
