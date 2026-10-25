"""Startet die App und speichert nach einer Verzögerung einen PNG-Screenshot des Fensters.
Aufruf: screenshot.py OUT.png [WIDTH HEIGHT] [select=N] [dark]"""
import sys

sys.path.insert(0, __file__.rsplit("/tools/", 1)[0])
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk, Graphene

from postfach.app import App

out = sys.argv[1]
w, h = (int(sys.argv[2]), int(sys.argv[3])) if len(sys.argv) > 3 else (1280, 800)
opts = sys.argv[4:]
app = App()
from gi.repository import Gio
app.set_flags(Gio.ApplicationFlags.NON_UNIQUE)


target = []   # Verfassen-Fenster (Option composeshot) statt Hauptfenster aufnehmen


def shoot():
    win = target[0] if target else app.props.active_window
    p = Gtk.WidgetPaintable.new(win)
    snap = Gtk.Snapshot()
    p.snapshot(snap, win.get_width(), win.get_height())
    node = snap.to_node()
    tex = win.get_native().get_renderer().render_texture(node, Graphene.Rect().init(0, 0, win.get_width(), win.get_height()))
    tex.save_to_png(out)
    print("saved", out)
    app.quit()
    return False


def fill_compose():
    c = target[0]
    c.set_default_size(w, h)
    c.to_row.set_text("lena@example.net")
    c.sub_row.set_text("Wochenend-Wanderung am Samstag")
    c.cc_btn.set_active(False)
    c.web.grab_focus()
    GLib.timeout_add(700, lambda: (c.web.execute_editing_command_with_argument(
        "InsertText", "Hallo Lena, ich bin dabei! Treffen wir uns um 9 Uhr am Parkplatz?"), False)[1])
    return False


def prep():
    win = app.props.active_window
    win.set_default_size(w, h)
    for o in opts:
        if o.startswith("select="):
            win._select_pos(int(o[7:]))
        if o.startswith('multi='):
            for i in range(int(o[6:])): win.sel.select_item(i, False)
        if o == "drawer":
            win.drawer.set_show_sidebar(True)
        if o == "light":
            Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.FORCE_LIGHT)
        if o == "dark":
            Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.FORCE_DARK)
        if o == "composeshot":
            from postfach.ui.compose import ComposeWindow
            orig = ComposeWindow.present
            ComposeWindow.present = lambda self: (target.append(self), orig(self))
            win.activate_action("win.compose", None)
            GLib.timeout_add(1800, fill_compose)
        if o in ("settings", "add-account", "compose", "reply"):
            win.activate_action(f"win.{o}", None)
        if o.startswith("search="):
            win.search.set_text(o[7:])
    GLib.timeout_add(5000 if "composeshot" in opts else 2200, shoot)
    return False


app.connect("activate", lambda a: GLib.timeout_add(1500, prep))
app.run([])
