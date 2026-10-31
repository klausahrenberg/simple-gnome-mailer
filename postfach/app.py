import os
import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk

from . import APP_ID, APP_NAME, config
from .tray import Tray
from .i18n import _
from .ui.window import MainWindow

AUTOSTART = os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"),
                         "autostart", APP_ID + ".desktop")


def set_autostart(on):
    """Legt den Autostart-Eintrag an/entfernt ihn (Start im Hintergrund)."""
    if not on:
        try:
            os.remove(AUTOSTART)
        except OSError:
            pass
        return
    import shutil
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    # installiertes Paket: Befehl „postfach“; sonst run.sh im Projektordner
    cmd = shutil.which("postfach") or os.path.join(root, "run.sh")
    os.makedirs(os.path.dirname(AUTOSTART), exist_ok=True)
    with open(AUTOSTART, "w", encoding="utf-8") as f:
        f.write(f"[Desktop Entry]\nType=Application\nName={APP_NAME}\nComment={_('Fetch mail in the background')}\n"
                f"Exec={cmd} --background\nIcon={APP_ID}\n"
                "X-GNOME-Autostart-enabled=true\nX-GNOME-Autostart-Delay=5\n")


class App(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
        self.add_main_option("background", 0, GLib.OptionFlags.NONE, GLib.OptionArg.NONE,
                             _("Start in the background without a window"), None)
        self.connect("handle-local-options", self._local_options)
        self.start_hidden = False
        self.held = False
        self.tray = None

    def _local_options(self, _app, opts):
        self.start_hidden = opts.contains("background")
        return -1

    def do_startup(self):
        Adw.Application.do_startup(self)
        Gtk.IconTheme.get_for_display(Gdk.Display.get_default()).add_search_path(
            os.path.join(os.path.dirname(__file__), "icons"))
        Gtk.Window.set_default_icon_name(APP_ID)
        a = Gio.SimpleAction.new("open-message", GLib.VariantType.new("i"))
        a.connect("activate", lambda _a, v: self._win().show_message_pk(v.get_int32()))
        self.add_action(a)
        for name, cb, accel in (("show", lambda *_: self._win().present(), None),
                                ("compose", lambda *_: self._compose(), None),
                                ("quit-app", lambda *_: self.quit_app(), "<Control>q")):
            act = Gio.SimpleAction.new(name, None)
            act.connect("activate", cb)
            self.add_action(act)
            if accel:
                self.set_accels_for_action(f"app.{name}", [accel])
        if config.get("autostart") and not os.environ.get("POSTFACH_DEMO"):
            set_autostart(True)       # Eintrag bei jedem Start prüfen/erneuern (fehlt er, wird er wiederhergestellt)
        self.tray = Tray(lambda: self._win().present(), self._compose, self.quit_app)
        if not os.environ.get("POSTFACH_DEMO"):
            self.tray.start()

    def _win(self):
        return self.props.active_window or MainWindow(self)

    def _compose(self):
        w = self._win()
        w.present()
        w.activate_action("win.compose", None)

    def keep_running(self):
        """Hält die App am Leben, auch wenn das Fenster geschlossen ist."""
        if not self.held:
            self.hold()
            self.held = True

    def restart(self):
        """Beendet die App und startet sie danach neu (z. B. nach einem Sprachwechsel)."""
        import shutil
        import subprocess
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        cmd = shutil.which("postfach") or os.path.join(root, "run.sh")
        subprocess.Popen(["sh", "-c", f'sleep 2; exec "{cmd}"'], start_new_session=True,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.quit_app()

    def quit_app(self):
        win = self.props.active_window
        if win:
            win.shutdown()
        self.quit()

    def do_activate(self):
        first = self.props.active_window is None
        win = self._win()
        if first and self.start_hidden and config.get("run_in_background"):
            self.keep_running()      # Autostart: Mails abrufen, Fenster erst auf Anforderung zeigen
            self.start_hidden = False
            return
        win.present()


def main():
    return App().run(sys.argv)
