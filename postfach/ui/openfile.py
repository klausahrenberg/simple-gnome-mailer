"""Anhänge sofort mit der Standard-App öffnen (Empfangen und Verfassen)."""
import os
import re

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gio, GLib, Gtk

from ..i18n import _

DANGEROUS = {"desktop", "sh", "bash", "exe", "bat", "cmd", "com", "scr", "msi", "jar", "appimage", "run", "ps1", "vbs", "js"}


def write_cache(name, data, folder):
    """Schreibt die Datei vollständig und atomar in den Cache. -> (sicherer Name, Pfad)"""
    safe = re.sub(r"[^\w.\- ]", "_", os.path.basename(name)).strip(". ") or "anhang"
    d = os.path.join(GLib.get_user_cache_dir(), "postfach", "attachments", str(folder))
    os.makedirs(d, mode=0o700, exist_ok=True)
    path = os.path.join(d, safe)
    tmp = path + ".part"
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    return safe, path


def open_data(parent, name, data, folder, toast, ask=False):
    """Öffnet die Datei mit der Standard-App (ohne Dialog); ask=True oder unbekannter Typ: „Öffnen mit …“."""
    safe, path = write_cache(name, data, folder)
    if safe.rsplit(".", 1)[-1].lower() in DANGEROUS:         # ausführbare Typen nie direkt starten
        toast(_("Executable files are not opened directly – please save them"))
        return
    gfile = Gio.File.new_for_path(path)
    if not ask:
        app = Gio.AppInfo.get_default_for_type(Gio.content_type_guess(path, None)[0], False)
        if app:
            try:
                app.launch([gfile], None)
                return
            except GLib.Error:
                pass
    launcher = Gtk.FileLauncher.new(gfile)
    launcher.set_always_ask(True)

    def done(l, res):
        try:
            l.launch_finish(res)
        except GLib.Error as e:
            if not e.matches(Gtk.dialog_error_quark(), Gtk.DialogError.DISMISSED):
                toast(_("Opening failed: {error}").format(error=e.message))

    launcher.launch(parent, None, done)
