"""Einstellungen „Speicher“: Datenordner, lokales Archiv (alte Mails nur noch lokal) und Thunderbird-Import."""
import os
import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk

from .. import config, db, paths, thunderbird
from ..i18n import _, ngettext


def _mb(n):
    return f"{n / 1_000_000:.1f} MB" if n < 1_000_000_000 else f"{n / 1_000_000_000:.2f} GB"


def build_pages(dlg):
    win = dlg.win
    page = Adw.PreferencesPage(title=_("Storage"), icon_name="drive-harddisk-symbolic", name="storage")

    # ---- Datenordner
    g = Adw.PreferencesGroup(title=_("Data location"),
                             description=_("The database (SQLite) with all stored messages and the local archive. "
                                           "A new folder takes effect after a restart; the database is moved then."))
    try:
        size = sum(os.path.getsize(os.path.join(paths.DATA_DIR, f)) for f in paths.DB_FILES
                   if os.path.exists(os.path.join(paths.DATA_DIR, f)))
    except OSError:
        size = 0
    row = Adw.ActionRow(title=paths.DATA_DIR, subtitle=_mb(size), title_selectable=True)
    row.add_prefix(Gtk.Image.new_from_icon_name("folder-symbolic"))
    pending = config.get("data_dir_from")
    if pending and config.get("data_dir"):
        row.set_subtitle(_("Moves to {path} after the restart").format(path=config.get("data_dir")))
    b = Gtk.Button(label=_("Change …"), valign=Gtk.Align.CENTER)

    def chosen(d, res):
        try:
            f = d.select_folder_finish(res)
        except GLib.Error:
            return
        new = f.get_path()
        if not new or not os.access(new, os.W_OK):
            win.toast(_("The folder is not writable"))
            return
        if os.path.abspath(new) == os.path.abspath(paths.DATA_DIR):
            config.set("data_dir", paths.DATA_DIR)
            config.set("data_dir_from", None)
            row.set_subtitle(_mb(size))
            return
        config.set("data_dir_from", config.get("data_dir_from") or paths.DATA_DIR)
        config.set("data_dir", new)
        row.set_subtitle(_("Moves to {path} after the restart").format(path=new))
        win.toast_restart(_("The database moves to the new folder after the restart"))

    def pick(_b):
        d = Gtk.FileDialog(title=_("Choose data folder"))
        d.set_initial_folder(Gio.File.new_for_path(os.path.dirname(paths.DATA_DIR) or "/"))
        d.select_folder(win, None, chosen)

    b.connect("clicked", pick)
    row.add_suffix(b)
    g.add(row)
    page.add(g)

    # ---- Lokales Archiv
    n_local = db.conn().execute("SELECT COUNT(*) FROM messages WHERE archived=1").fetchone()[0]
    g2 = Adw.PreferencesGroup(title=_("Local archive"),
                              description=_("Older messages (inbox and sent) are kept only on this device and deleted on "
                                            "the server. They stay in the unified inbox and sent folder and in the search."))
    sw = Adw.SwitchRow(title=_("Keep old messages only locally"),
                       subtitle=_("Deletes them on the server after they are saved here"))
    sw.set_active(bool(config.get("archive_enabled")))
    spin = Adw.SpinRow.new_with_range(1, 240, 1)
    spin.set_title(_("Older than (months)"))
    spin.set_value(int(config.get("archive_months")))
    spin.set_sensitive(sw.get_active())
    now = Adw.ButtonRow(title=_("Archive now"))
    now.set_sensitive(sw.get_active())
    guard = {"busy": False}

    def switched(r, _p):
        if guard["busy"]:
            return
        if not r.get_active():
            config.set("archive_enabled", False)
            spin.set_sensitive(False)
            now.set_sensitive(False)
            return
        d = Adw.AlertDialog(heading=_("Delete old messages on the server?"),
                            body=_("Messages in the inbox and sent folders older than {n} months are saved on this device "
                                   "and then deleted on the server. Only this copy remains, so back up the data folder "
                                   "regularly.").format(n=int(spin.get_value())))
        d.add_response("cancel", _("Cancel"))
        d.add_response("ok", _("Enable"))
        d.set_response_appearance("ok", Adw.ResponseAppearance.DESTRUCTIVE)

        def resp(_d, answer):
            if answer == "ok":
                config.set("archive_enabled", True)
                spin.set_sensitive(True)
                now.set_sensitive(True)
                win.sync.request_archive()
            else:
                guard["busy"] = True
                r.set_active(False)
                guard["busy"] = False

        d.connect("response", resp)
        d.present(dlg)

    sw.connect("notify::active", switched)
    spin.connect("notify::value", lambda r, _p: config.set("archive_months", int(r.get_value())))
    now.connect("activated", lambda *_a: (win.sync.request_archive(), win.toast(_("Archiving started"))))
    g2.add(sw)
    g2.add(spin)
    g2.add(now)
    info = Adw.ActionRow(title=ngettext("{n} message stored only locally", "{n} messages stored only locally", n_local)
                         .format(n=n_local))
    info.add_prefix(Gtk.Image.new_from_icon_name("emblem-documents-symbolic"))
    g2.add(info)
    page.add(g2)

    # ---- Import
    g3 = Adw.PreferencesGroup(title=_("Import"))
    r = Adw.ActionRow(title=_("Import from Thunderbird …"),
                      subtitle=_("Reads messages from a Thunderbird profile; duplicates are skipped"), activatable=True)
    r.add_prefix(Gtk.Image.new_from_icon_name("document-open-symbolic"))
    r.add_suffix(Gtk.Image.new_from_icon_name("go-next-symbolic"))
    r.connect("activated", lambda *_a: ThunderbirdDialog(win).present(dlg))
    g3.add(r)
    page.add(g3)
    dlg.add(page)


class ThunderbirdDialog(Adw.Dialog):
    def __init__(self, win):
        super().__init__(title=_("Import from Thunderbird"), content_width=560, content_height=640)
        self.win = win
        self.profiles = thunderbird.find_profiles()
        self.sources, self.checks = [], []
        self.cancel = threading.Event()
        tv = Adw.ToolbarView()
        tv.add_top_bar(Adw.HeaderBar())
        self.box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin_top=12, margin_bottom=12,
                           margin_start=16, margin_end=16)
        pg = Adw.PreferencesGroup()
        self.combo = Adw.ComboRow(title=_("Profile"))
        self.combo.set_model(Gtk.StringList.new([os.path.basename(p) for p in self.profiles] or [_("No profile found")]))
        self.combo.connect("notify::selected", lambda *_a: self._fill())
        other = Gtk.Button(label=_("Other folder …"), valign=Gtk.Align.CENTER)
        other.connect("clicked", self._pick)
        self.combo.add_suffix(other)
        pg.add(self.combo)
        self.box.append(pg)
        self.groups = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        sc = Gtk.ScrolledWindow(child=self.groups, vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        self.box.append(sc)
        self.progress = Gtk.ProgressBar(visible=False)
        self.status = Gtk.Label(wrap=True, xalign=0, visible=False)
        self.box.append(self.progress)
        self.box.append(self.status)
        self.go = Gtk.Button(label=_("Import"), halign=Gtk.Align.CENTER)
        self.go.add_css_class("suggested-action")
        self.go.add_css_class("pill")
        self.go.connect("clicked", self._start)
        self.box.append(self.go)
        tv.set_content(self.box)
        self.set_child(tv)
        self._fill()

    def _pick(self, _b):
        d = Gtk.FileDialog(title=_("Choose Thunderbird profile folder"))

        def done(dd, res):
            try:
                p = dd.select_folder_finish(res).get_path()
            except GLib.Error:
                return
            if p and p not in self.profiles:
                self.profiles.append(p)
                self.combo.set_model(Gtk.StringList.new([os.path.basename(x) for x in self.profiles]))
            self.combo.set_selected(self.profiles.index(p))
            self._fill()

        d.select_folder(self.win, None, done)

    def _fill(self):
        while (c := self.groups.get_first_child()):
            self.groups.remove(c)
        self.sources, self.checks = [], []
        if not self.profiles:
            self.status.set_text(_("No Thunderbird profile found. Choose the profile folder manually."))
            self.status.set_visible(True)
            self.go.set_sensitive(False)
            return
        self.status.set_visible(False)
        self.sources = thunderbird.scan(self.profiles[self.combo.get_selected()])
        self.go.set_sensitive(bool(self.sources))
        if not self.sources:
            self.status.set_text(_("No mail folders found in this profile."))
            self.status.set_visible(True)
            return
        titles = {"inbox": _("Inbox"), "sent": _("Sent"), "other": _("Other folders")}
        for role in ("inbox", "sent", "other"):
            items = [s for s in self.sources if s["role"] == role]
            if not items:
                continue
            g = Adw.PreferencesGroup(title=titles[role])
            for s in items:
                chk = Gtk.CheckButton(active=role != "other", valign=Gtk.Align.CENTER)
                mode = Gtk.DropDown.new_from_strings([_("Inbox"), _("Sent"), _("By sender")])
                mode.set_valign(Gtk.Align.CENTER)
                mode.set_selected({"inbox": 0, "sent": 1}.get(s["mode"], 2))
                who = _("Local folders") if s["server"] == "Local Folders" else (s["user"] or s["host"])
                row = Adw.ActionRow(title=s["label"] if role == "other" else (who or s["label"]),
                                    subtitle=(f"{who} · " if role == "other" and who else
                                              f"{s['label']} · " if s["label"].lower() not in ("inbox", "posteingang") else "")
                                    + _mb(s["size"]),
                                    activatable_widget=chk)
                row.add_prefix(chk)
                row.add_suffix(mode)
                g.add(row)
                self.checks.append((chk, s, mode))
            self.groups.append(g)

    def _start(self, _b):
        chosen = []
        for chk, s, mode in self.checks:
            if chk.get_active():
                chosen.append(dict(s, mode=("inbox", "sent", "auto")[mode.get_selected()]))
        if not chosen:
            return
        self.go.set_sensitive(False)
        self.combo.set_sensitive(False)
        self.progress.set_visible(True)
        self.status.set_visible(True)
        self.status.set_text(_("Importing …"))
        self.set_can_close(False)

        own = thunderbird.identities(self.profiles[self.combo.get_selected()])

        def work():
            try:
                stats = thunderbird.run_import(
                    chosen, lambda d, t: GLib.idle_add(self.progress.set_fraction, min(1.0, d / t)), self.cancel,
                    own)
                err = None
            except Exception as e:
                stats, err = None, str(e)
            GLib.idle_add(self._finished, stats, err)

        threading.Thread(target=work, daemon=True, name="tb-import").start()

    def _finished(self, stats, err):
        self.set_can_close(True)
        self.progress.set_visible(False)
        if err:
            self.status.set_text(_("Import failed: {error}").format(error=err))
        else:
            self.status.set_text(_("Imported {n} messages, {d} duplicates skipped").format(
                n=stats["imported"], d=stats["duplicates"]))
            self.win._rebuild_drawer()
            self.win.reload_list()
        self.go.set_label(_("Close"))
        self.go.set_sensitive(True)
        self.go.disconnect_by_func(self._start)
        self.go.connect("clicked", lambda *_a: self.close())
        return False
