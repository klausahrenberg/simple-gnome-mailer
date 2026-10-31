"""Konto hinzufügen + Einstellungen."""
import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk

from .. import APP_NAME, colors, config, db, goa, imapsync
from .. import secrets as keyring
from ..i18n import _, ngettext

PRESETS = {  # Domain -> (imap, smtp)
    "gmail.com": ("imap.gmail.com", "smtp.gmail.com"), "googlemail.com": ("imap.gmail.com", "smtp.gmail.com"),
    "gmx.de": ("imap.gmx.net", "mail.gmx.net"), "gmx.net": ("imap.gmx.net", "mail.gmx.net"),
    "web.de": ("imap.web.de", "smtp.web.de"), "t-online.de": ("secureimap.t-online.de", "securesmtp.t-online.de"),
    "yahoo.com": ("imap.mail.yahoo.com", "smtp.mail.yahoo.com"), "icloud.com": ("imap.mail.me.com", "smtp.mail.me.com"),
    "posteo.de": ("posteo.de", "posteo.de"), "mailbox.org": ("imap.mailbox.org", "smtp.mailbox.org"),
}


def _row_btn(title, subtitle, icon, cb):
    r = Adw.ActionRow(title=title, subtitle=subtitle, activatable=True)
    r.add_prefix(Gtk.Image.new_from_icon_name(icon))
    r.add_suffix(Gtk.Image.new_from_icon_name("go-next-symbolic"))
    r.connect("activated", lambda *_: cb())
    return r


def ask_password(parent, acc, on_done=None, error=None):
    """Passwort eines IMAP-Kontos (neu) eingeben: wird gegen den Server geprüft und im Schlüsselbund gespeichert."""
    d = Adw.AlertDialog(heading=_("Password for {email}").format(email=acc["email"]),
                        body=error or _("The saved password is missing or has changed. It is stored securely in the GNOME keyring."))
    pw = Adw.PasswordEntryRow(title=_("Password"))
    lb = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
    lb.add_css_class("boxed-list")
    lb.append(pw)
    d.set_extra_child(lb)
    d.add_response("cancel", _("Cancel"))
    d.add_response("ok", _("Save"))
    d.set_response_appearance("ok", Adw.ResponseAppearance.SUGGESTED)
    d.set_default_response("ok")
    d.set_close_response("cancel")
    pw.connect("entry-activated", lambda *_: (d.emit("response", "ok"), d.force_close()))

    def resp(_d, r):
        secret = pw.get_text()
        if r != "ok" or not secret:
            return

        def work():
            try:
                im = imapsync.connect(acc, password=secret)
                im.logout()
                GLib.idle_add(ok)
            except Exception as e:
                msg = _("Sign-in failed: {error}").format(error=str(e)[:200])
                GLib.idle_add(lambda: (ask_password(parent, acc, on_done, msg), False)[1])

        def ok():
            keyring.store(acc["id"], "password", secret)
            win = parent if hasattr(parent, "toast") else getattr(parent, "win", None)
            if win:
                win.toast(_("Password for {email} saved").format(email=acc["email"]))
                win.sync.request(acc["id"])
            if on_done:
                on_done()
            return False

        threading.Thread(target=work, daemon=True).start()

    d.connect("response", resp)
    d.present(parent)


class AddAccountDialog(Adw.Dialog):
    def __init__(self, on_added):
        super().__init__(title=_("Add account"), content_width=460, content_height=560)
        self.on_added = on_added
        self.nav = Adw.NavigationView()
        self.set_child(self.nav)
        self.nav.add(self._choose_page())

    # -- Seite 1 ----------------------------------------------------------
    def _choose_page(self):
        page = Adw.NavigationPage(title=_("Add account"), tag="choose")
        tv = Adw.ToolbarView()
        tv.add_top_bar(Adw.HeaderBar())
        grp = Adw.PreferencesGroup(title=_("Choose a provider"), description=_("Set up Google and Microsoft accounts in "
                                   "GNOME Online Accounts; they then appear here automatically."))
        if goa.available():
            grp.add(_row_btn(_("GNOME Online Accounts"), _("Manage the accounts of the desktop settings (recommended)"),
                             "system-users-symbolic", self._open_goa))
        grp.add(_row_btn(_("Other IMAP account"), _("With password"), "mail-send-symbolic",
                         lambda: self.nav.push(self._imap_page())))
        clamp = Adw.Clamp(child=grp, margin_top=12, margin_start=12, margin_end=12, margin_bottom=12)
        self.status = Gtk.Label(wrap=True, margin_start=18, margin_end=18)
        self.status.add_css_class("error")
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.append(clamp)
        box.append(self.status)
        tv.set_content(box)
        page.set_child(tv)
        return page

    def _open_goa(self):
        Gio.AppInfo.create_from_commandline("gnome-control-center online-accounts", None,
                                            Gio.AppInfoCreateFlags.NONE).launch([], None)
        self.status.remove_css_class("error")
        self.status.set_text(_("Enable “Mail” for an account there – it will appear here automatically."))

    # -- IMAP-Formular ----------------------------------------------------
    def _imap_page(self, gmail_hint=False):
        page = Adw.NavigationPage(title=_("IMAP account"), tag="imap")
        tv = Adw.ToolbarView()
        tv.add_top_bar(Adw.HeaderBar())
        grp = Adw.PreferencesGroup()
        if gmail_hint:
            hint = Adw.ActionRow(title=_("Create an app password"),
                                 subtitle=_("Google account → Security → enable 2-step verification, then create an "
                                            "app password and enter it below instead of your normal password."),
                                 subtitle_lines=0, activatable=True)
            hint.add_suffix(Gtk.Image.new_from_icon_name("adw-external-link-symbolic"))
            hint.connect("activated", lambda *_: Gio.AppInfo.launch_default_for_uri(
                "https://myaccount.google.com/apppasswords", None))
            grp.add(hint)
        self.f_name = Adw.EntryRow(title=_("Name"))
        self.f_mail = Adw.EntryRow(title=_("Email address"))
        self.f_pw = Adw.PasswordEntryRow(title=_("Password"))
        self.f_user = Adw.EntryRow(title=_("Username (if different)"))
        self.f_imap = Adw.EntryRow(title=_("IMAP server"))
        self.f_iport = Adw.EntryRow(title=_("IMAP port"), text="993")
        self.f_smtp = Adw.EntryRow(title=_("SMTP server"))
        self.f_sport = Adw.EntryRow(title=_("SMTP port (587 STARTTLS / 465 SSL)"), text="587")
        for r in (self.f_name, self.f_mail, self.f_pw, self.f_user, self.f_imap, self.f_iport, self.f_smtp, self.f_sport):
            grp.add(r)
        self.f_mail.connect("changed", self._guess)
        self.err = Gtk.Label(wrap=True, margin_top=6)
        self.err.add_css_class("error")
        self.go = Gtk.Button(label=_("Connect and add"), halign=Gtk.Align.CENTER, margin_top=12)
        self.go.add_css_class("suggested-action")
        self.go.add_css_class("pill")
        self.go.connect("clicked", self._imap_add)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, margin_start=12, margin_end=12, margin_bottom=12)
        box.append(grp)
        box.append(self.err)
        box.append(self.go)
        sc = Gtk.ScrolledWindow(child=Adw.Clamp(child=box))
        tv.set_content(sc)
        page.set_child(tv)
        return page

    def _guess(self, *_):
        mail = self.f_mail.get_text()
        if "@" not in mail:
            return
        dom = mail.split("@")[1].strip().lower()
        if not dom:
            return
        imap, smtp = PRESETS.get(dom, (f"imap.{dom}", f"smtp.{dom}"))
        self.f_imap.set_text(imap)
        self.f_smtp.set_text(smtp)

    def _imap_add(self, *_args):
        try:
            data = dict(name=self.f_name.get_text() or self.f_mail.get_text().split("@")[0],
                        email=self.f_mail.get_text().strip(), kind="imap",
                        username=self.f_user.get_text().strip() or self.f_mail.get_text().strip(),
                        imap_host=self.f_imap.get_text().strip(), imap_port=int(self.f_iport.get_text()),
                        smtp_host=self.f_smtp.get_text().strip(), smtp_port=int(self.f_sport.get_text()))
        except ValueError:
            self.err.set_text(_("Ports must be numbers."))
            return
        pw = self.f_pw.get_text()
        if not data["email"] or not data["imap_host"] or not pw:
            self.err.set_text(_("Email address, server and password are required."))
            return
        self.err.set_text("")
        self.go.set_sensitive(False)
        self.go.set_label(_("Connecting …"))

        def work():
            try:
                im = imapsync.connect(data, password=pw)
                im.logout()
                GLib.idle_add(self._imap_ok, data, pw)
            except Exception as e:
                GLib.idle_add(self._imap_fail, str(e))

        threading.Thread(target=work, daemon=True).start()

    def _imap_fail(self, msg):
        self.go.set_sensitive(True)
        self.go.set_label(_("Connect and add"))
        self.err.set_text(_("Connection failed: {error}").format(error=msg[:300]))

    def _imap_ok(self, data, pw):
        acc_id = db.add_account(**data)
        keyring.store(acc_id, "password", pw)
        self.on_added(acc_id)
        self.close()


class SettingsDialog(Adw.PreferencesDialog):
    def __init__(self, win):
        super().__init__(title=_("Settings"))
        self.win = win
        self.connect("closed", lambda *_: win.reload_list())
        self._build_accounts()
        self._build_view()
        self._build_sync()
        from .storage import build_pages
        build_pages(self)

    def _build_accounts(self):
        self.page_acc = Adw.PreferencesPage(title=_("Accounts"), icon_name="avatar-default-symbolic")
        self.add(self.page_acc)
        self._fill_accounts()

    def _fill_accounts(self):
        if getattr(self, "grp_acc", None):
            self.page_acc.remove(self.grp_acc)
        self.grp_acc = Adw.PreferencesGroup(title=_("Accounts"))
        for i, a in enumerate(db.accounts()):
            missing = a["kind"] == "imap" and keyring.lookup(a["id"], "password") is None
            r = Adw.ActionRow(title=_("Local archive") if a["kind"] == "local" else a["email"],
                              subtitle={"goa": _("GNOME Online Account"), "local": _("Stored on this device only")}.get(a["kind"], "IMAP")
                              + (" – " + _("password missing") if missing else ""))
            if missing:
                r.add_css_class("error")
            r.add_suffix(self._color_button(a, i))
            if a["kind"] == "imap":
                k = Gtk.Button(icon_name="dialog-password-symbolic", valign=Gtk.Align.CENTER, tooltip_text=_("Change password …"))
                k.add_css_class("flat")
                k.connect("clicked", lambda _b, a=a: ask_password(self, a, self._fill_accounts))
                r.add_suffix(k)
            if a["kind"] not in ("goa", "local"):
                b = Gtk.Button(icon_name="user-trash-symbolic", valign=Gtk.Align.CENTER)
                b.add_css_class("flat")
                b.set_tooltip_text(_("Remove account"))
                b.connect("clicked", self._remove, a["id"], a["email"])
                r.add_suffix(b)
            self.grp_acc.add(r)
        add = Adw.ButtonRow(title=_("Add account …"))
        add.add_css_class("suggested-action")
        add.connect("activated", lambda *_: AddAccountDialog(self._added).present(self))
        self.grp_acc.add(add)
        self.page_acc.add(self.grp_acc)

    def _color_button(self, acc, ordinal):
        """Farbwahl: kleiner Kreis in der Kontofarbe, öffnet eine Palette."""
        current = colors.index_for(acc, ordinal)
        mb = Gtk.MenuButton(valign=Gtk.Align.CENTER, tooltip_text=_("Account color"))
        mb.add_css_class("flat")
        dot = Gtk.Box(width_request=16, height_request=16, valign=Gtk.Align.CENTER, halign=Gtk.Align.CENTER)
        dot.add_css_class("acc-dot")
        dot.add_css_class(f"acc-color-{current}")
        mb.set_child(dot)
        pop = Gtk.Popover()
        grid = Gtk.FlowBox(max_children_per_line=4, min_children_per_line=4, selection_mode=Gtk.SelectionMode.NONE,
                           row_spacing=6, column_spacing=6, margin_top=8, margin_bottom=8, margin_start=8, margin_end=8)
        for idx, (name, _hex) in enumerate(colors.PALETTE):
            sw = Gtk.Button(tooltip_text=name)
            sw.add_css_class("swatch")
            sw.add_css_class(f"acc-color-{idx}")
            if idx == current:
                sw.set_child(Gtk.Image.new_from_icon_name("object-select-symbolic"))
            sw.connect("clicked", lambda _b, i=idx: self._set_color(acc["id"], i, pop))
            grid.append(sw)
        pop.set_child(grid)
        mb.set_popover(pop)
        return mb

    def _set_color(self, acc_id, idx, pop):
        c = db.conn()
        c.execute("UPDATE accounts SET color=? WHERE id=?", (idx + 1, acc_id))
        c.commit()
        pop.popdown()
        self._fill_accounts()
        self.win.refresh_colors()

    def _added(self, acc_id):
        self._fill_accounts()
        self.win.accounts_changed(acc_id)

    def _remove(self, _b, acc_id, mail):
        d = Adw.AlertDialog(heading=_("Remove account?"), body=_("{mail} and the locally stored messages "
                            "will be removed from this device, except messages archived locally, which move to the local archive. "
                            "Everything on the server stays untouched.").format(mail=mail))
        d.add_response("cancel", _("Cancel"))
        d.add_response("remove", _("Remove"))
        d.set_response_appearance("remove", Adw.ResponseAppearance.DESTRUCTIVE)

        def resp(_d, r):
            if r == "remove":
                db.remove_account(acc_id)
                keyring.clear(acc_id)
                self._fill_accounts()
                self.win.accounts_changed(None)

        d.connect("response", resp)
        d.present(self)

    def _build_view(self):
        page = Adw.PreferencesPage(title=_("View"), icon_name="view-list-symbolic")
        gl = Adw.PreferencesGroup(title=_("Language"))
        langs = ["auto", "en", "de"]
        lang = Adw.ComboRow(title=_("Language"), subtitle=_("Takes effect after a restart"),
                            model=Gtk.StringList.new([_("System default"), "English", "Deutsch"]))
        lang.set_selected(langs.index(config.get("language")) if config.get("language") in langs else 0)

        def lang_changed(c, _p):
            config.set("language", langs[c.get_selected()])
            self.win.toast_restart(_("Restart {app} to apply the language").format(app=APP_NAME))

        lang.connect("notify::selected", lang_changed)
        gl.add(lang)
        page.add(gl)
        g = Adw.PreferencesGroup(title=_("Message list"))
        combo = Adw.ComboRow(title=_("Preview lines"), subtitle=_("Lines of the message text per entry"),
                             model=Gtk.StringList.new([_("2 lines"), _("3 lines")]))
        combo.set_selected(0 if int(config.get("preview_lines")) == 2 else 1)
        combo.connect("notify::selected", lambda c, _p: (config.set("preview_lines", 2 + c.get_selected()),
                                                          self.win.reload_list()))
        g.add(combo)
        width = Adw.SpinRow.new_with_range(20, 70, 1)
        width.set_title(_("Width of the message list (%)"))
        width.set_subtitle(_("Can also be changed by dragging the divider between the columns"))
        width.set_value(round(float(config.get("list_fraction")) * 100))
        width.connect("notify::value", lambda r, _p: self.win.set_list_fraction(r.get_value() / 100))
        self._width_row = width
        g.add(width)
        self.win.split.connect("notify::sidebar-width-fraction", self._sync_width_row)
        g3 = Adw.PreferencesGroup(title=_("Writing messages"))
        fmt = Adw.ComboRow(title=_("Default format"), subtitle=_("Format for new messages"),
                           model=Gtk.StringList.new([_("Formatted (HTML)"), _("Plain text")]))
        fmt.set_selected(0 if config.get("compose_html") else 1)
        fmt.connect("notify::selected", lambda c, _p: config.set("compose_html", c.get_selected() == 0))
        g3.add(fmt)
        follow = Adw.SwitchRow(title=_("Reply in the format of the original message"),
                               subtitle=_("Answer HTML mails as HTML and text mails as text; otherwise the default format applies"))
        follow.set_active(bool(config.get("reply_follow_original")))
        follow.connect("notify::active", lambda s, _p: config.set("reply_follow_original", s.get_active()))
        g3.add(follow)
        sp = Adw.SwitchRow(title=_("Suggest reducing large images"), subtitle=_("When adding attachments if images or the message get too large"))
        sp.set_active(bool(config.get("shrink_prompt")))
        sp.connect("notify::active", lambda r, _p: config.set("shrink_prompt", r.get_active()))
        g3.add(sp)
        for key, title in (("shrink_image_mb", _("Single image from (MB)")), ("shrink_total_mb", _("All attachments from (MB)"))):
            row = Adw.SpinRow.new_with_range(1, 50, 1)
            row.set_title(title)
            row.set_value(int(config.get(key)))
            row.connect("notify::value", lambda r, _p, k=key: config.set(k, int(r.get_value())))
            g3.add(row)
        g2 = Adw.PreferencesGroup(title=_("Reading pane"))
        sw = Adw.SwitchRow(title=_("Load external images automatically"), subtitle=_("Can tell senders that you opened the message"))
        sw.set_active(bool(config.get("load_remote_images")))
        sw.connect("notify::active", lambda s, _p: config.set("load_remote_images", s.get_active()))
        g2.add(sw)
        lg = Adw.SwitchRow(title=_("Load sender logos"),
                           subtitle=_("For company and service senders (noreply@, info@ …) the logo is fetched from the sender domain's website"))
        lg.set_active(bool(config.get("load_logos")))
        lg.connect("notify::active", lambda s, _p: (config.set("load_logos", s.get_active()), self.win._touch_all()))
        g2.add(lg)
        ls = Adw.SwitchRow(title=_("Also use an icon service"),
                           subtitle=_("For domains without a logo of their own, {app} asks DuckDuckGo – DuckDuckGo then learns the sender domains").format(app=APP_NAME))
        ls.set_active(bool(config.get("logo_service")))
        ls.connect("notify::active", lambda s, _p: config.set("logo_service", s.get_active()))
        g2.add(ls)
        page.add(g)
        page.add(g3)
        page.add(g2)
        # Liste der immer geladenen Domains: eigenes Fenster (hält das Einstellungsfenster kompakt)
        self.trust_row = Adw.ActionRow(title=_("Always loaded content"), activatable=True)
        self.trust_row.add_suffix(Gtk.Image.new_from_icon_name("go-next-symbolic"))
        self.trust_row.connect("activated", lambda *_: TrustedDomainsDialog(self._update_trust_row).present(self))
        g2.add(self.trust_row)
        self._update_trust_row()
        self.add(page)

    def _update_trust_row(self):
        n = len(config.get("remote_domains"))
        self.trust_row.set_subtitle(_("No sender domain approved") if not n else
                                    ngettext("{n} sender domain – content loads automatically",
                                             "{n} sender domains – content loads automatically", n).format(n=n))

    def _sync_width_row(self, *_):
        v = round(self.win.split.get_sidebar_width_fraction() * 100)
        if int(self._width_row.get_value()) != v:
            self._width_row.set_value(v)

    def _build_sync(self):
        page = Adw.PreferencesPage(title=_("Fetching"), icon_name="emblem-synchronizing-symbolic")
        g = Adw.PreferencesGroup(title=_("Synchronization"))
        a = Adw.SpinRow.new_with_range(1, 60, 1)
        a.set_title(_("Fetch interval (minutes)"))
        a.set_value(int(config.get("sync_interval_min")))
        a.connect("notify::value", lambda r, _p: config.set("sync_interval_min", int(r.get_value())))
        b = Adw.SpinRow.new_with_range(50, 5000, 50)
        b.set_title(_("Messages per folder"))
        b.set_subtitle(_("The newest N messages are stored locally and searched"))
        b.set_value(int(config.get("sync_depth")))
        b.connect("notify::value", lambda r, _p: config.set("sync_depth", int(r.get_value())))
        g.add(a)
        g.add(b)
        page.add(g)
        g2 = Adw.PreferencesGroup(title=_("Background"))
        bg = Adw.SwitchRow(title=_("Keep running in the background"),
                           subtitle=_("Keep fetching mail when the window is closed; reopen via notification or status bar"))
        bg.set_active(bool(config.get("run_in_background")))
        bg.connect("notify::active", lambda r, _p: config.set("run_in_background", r.get_active()))
        auto = Adw.SwitchRow(title=_("Start at login"), subtitle=_("Starts {app} in the background without a window").format(app=APP_NAME))
        auto.set_active(bool(config.get("autostart")))

        def toggled(r, _p):
            from ..app import set_autostart
            config.set("autostart", r.get_active())
            set_autostart(r.get_active())

        auto.connect("notify::active", toggled)
        g2.add(bg)
        g2.add(auto)
        page.add(g2)
        self.add(page)


class TrustedDomainsDialog(Adw.Dialog):
    """Absender-Domains, deren externe Inhalte immer geladen werden – einzeln widerrufbar."""

    def __init__(self, on_change):
        super().__init__(title=_("Always loaded content"), content_width=440, content_height=480)
        self.on_change = on_change
        tv = Adw.ToolbarView()
        tv.add_top_bar(Adw.HeaderBar())
        self.page = Adw.PreferencesPage()
        tv.set_content(self.page)
        self.set_child(tv)
        self.group = None
        self._fill()

    def _fill(self):
        if self.group:
            self.page.remove(self.group)
        self.group = Adw.PreferencesGroup(description=_("External content (images etc.) from these sender domains is "
                                                        "loaded automatically. Subdomains are included."))
        domains = config.get("remote_domains")
        for d in domains:
            r = Adw.ActionRow(title=d)
            b = Gtk.Button(icon_name="user-trash-symbolic", valign=Gtk.Align.CENTER, tooltip_text=_("Stop loading automatically"))
            b.add_css_class("flat")
            b.connect("clicked", lambda _b, d=d: self._remove(d))
            r.add_suffix(b)
            self.group.add(r)
        if not domains:
            self.group.add(Adw.ActionRow(title=_("None"), subtitle=_("Add one via “Load” → “Always load from …” in a message")))
        self.page.add(self.group)

    def _remove(self, domain):
        config.set("remote_domains", [x for x in config.get("remote_domains") if x != domain])
        self._fill()
        self.on_change()
