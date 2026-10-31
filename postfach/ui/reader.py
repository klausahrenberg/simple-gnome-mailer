"""Lesebereich: Kopfdaten, Anhänge, Inhalt (WebKit, ohne JavaScript, Remote-Bilder blockiert)."""
import os
import re
import time
import urllib.parse

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("WebKit", "6.0")
from gi.repository import Adw, Gio, GLib, Gtk, Pango, WebKit

from .. import config, contacts, db, logos, mailparse
from . import openfile
from .invitecard import InviteCard
from .. import invite
from ..i18n import _
from ..mailparse import display_subject


def _fmt_size(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


class Reader(Gtk.Box):
    def __init__(self, window):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.win = window
        self.pk = None
        self.domain = ""
        self.raw = None
        self.row = None

        # Kopf
        head = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        for s in ("top", "bottom"):
            getattr(head, f"set_margin_{s}")(14)
        head.set_margin_start(18)
        head.set_margin_end(18)
        self.subject = Gtk.Label(xalign=0, wrap=True, selectable=True)
        self.subject.add_css_class("title-2")
        head.append(self.subject)
        who = Gtk.Box(spacing=12)
        self.avatar = Adw.Avatar(size=44, show_initials=True)
        who.append(self.avatar)
        txt = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)
        self.sender = Gtk.Label(xalign=0, selectable=True, ellipsize=Pango.EllipsizeMode.END)
        self.sender.add_css_class("heading")
        self.to = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END, selectable=True)
        self.to.add_css_class("dim-label")
        self.to.add_css_class("caption")
        txt.append(self.sender)
        txt.append(self.to)
        who.append(txt)
        self.date = Gtk.Label(xalign=1, valign=Gtk.Align.START)
        self.date.add_css_class("dim-label")
        self.date.add_css_class("caption")
        # rechts: Datum und darunter Antworten / Allen antworten / Weiterleiten (nur Symbole)
        right = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, halign=Gtk.Align.END, valign=Gtk.Align.START)
        right.append(self.date)
        acts = Gtk.Box(spacing=0, halign=Gtk.Align.END)
        for icon, act, tip in (("mail-reply-sender-symbolic", "reply", _("Reply (Ctrl+R)")),
                               ("mail-reply-all-symbolic", "reply-all", _("Reply all (Ctrl+Shift+R)")),
                               ("mail-forward-symbolic", "forward", _("Forward (Ctrl+L)"))):
            b = Gtk.Button(icon_name=icon, action_name=f"win.{act}", tooltip_text=tip)
            b.add_css_class("flat")
            acts.append(b)
        right.append(acts)
        who.append(right)
        head.append(who)
        self.append(head)

        self.invite_card = InviteCard(window)
        self.append(self.invite_card)

        self.atts = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, max_children_per_line=6,
                                column_spacing=6, row_spacing=6)
        self.atts.set_margin_start(18)
        self.atts.set_margin_end(18)
        self.atts.set_margin_bottom(10)
        self.atts.set_visible(False)
        self.append(self.atts)

        # Hinweisleiste: Laden (einmalig) oder „Immer von <Domain> laden“ (Dropdown am Knopf)
        bar = Gtk.Box(spacing=12, halign=Gtk.Align.FILL)
        bar.add_css_class("remote-bar")
        lbl = Gtk.Label(label=_("External content was blocked"), hexpand=True, xalign=0.5)
        lbl.add_css_class("heading")
        bar.append(lbl)
        self.load_btn = Adw.SplitButton(label=_("Load"))
        self.load_btn.connect("clicked", self._load_remote)
        pop = Gtk.Popover()
        self.always_btn = Gtk.Button(label=_("Always load"))
        self.always_btn.add_css_class("flat")
        self.always_btn.connect("clicked", self._always_load)
        pop.set_child(self.always_btn)
        self.load_btn.set_popover(pop)
        bar.append(self.load_btn)
        self.banner = Gtk.Revealer(child=bar, reveal_child=False)
        self.append(self.banner)
        self.append(Gtk.Separator())

        settings = WebKit.Settings(enable_javascript=False, enable_developer_extras=False,
                                   enable_page_cache=False, enable_media=False, enable_webgl=False,
                                   allow_file_access_from_file_urls=False)
        self.web = WebKit.WebView(settings=settings, vexpand=True, hexpand=True)
        self.web.connect("decide-policy", self._policy)
        self.web.connect("create", lambda *_: None)
        self.append(self.web)

    # -- Anzeige ---------------------------------------------------------
    def show_message(self, pk):
        row = db.message(pk)
        if not row:
            return
        self.pk, self.row = pk, row
        self.raw = db.raw_message(pk)
        name = row["from_name"] or row["from_addr"]
        self.subject.set_text(display_subject(row["subject"]))
        self.avatar.set_text(name)
        self.avatar.set_custom_image(contacts.texture(row["from_addr"]) or logos.texture(name, row["from_addr"]))
        self.sender.set_text(f"{name} <{row['from_addr']}>" if row["from_name"] and row["from_name"] != row["from_addr"] else name)
        self.to.set_text(_("To: {recipients}").format(recipients=row["to_addrs"] or _("hidden recipients (BCC)")))
        self.date.set_text(time.strftime(_("%Y-%m-%d %H:%M"), time.localtime(row["date"] or 0)))
        self._attachments()
        inv = None
        if row["has_cal"] and self.raw:
            try:
                inv = invite.find(self.raw)
            except Exception:
                inv = None
        self.invite_card.show_invite(inv, row)
        self.domain = self._domain(row["from_addr"])
        self.always_btn.set_label(_("Always load from {domain}").format(domain=self.domain) if self.domain else _("Always load"))
        self.always_btn.set_sensitive(bool(self.domain))
        self._render(config.get("load_remote_images") or self.trusted(self.domain))

    def _render(self, remote):
        doc, has_remote = mailparse.render_html(self.row, self.raw, allow_remote=remote)
        self.banner.set_reveal_child(has_remote and not remote)
        self.web.load_html(doc, "about:blank")

    @staticmethod
    def _domain(addr):
        return (addr or "").rsplit("@", 1)[-1].strip().lower() if "@" in (addr or "") else ""

    @staticmethod
    def trusted(domain):
        """True, wenn die Absender-Domain (oder eine übergeordnete) zum automatischen Laden freigegeben ist."""
        return bool(domain) and any(domain == d or domain.endswith("." + d) for d in config.get("remote_domains"))

    def _always_load(self, *_args):
        self.load_btn.get_popover().popdown()
        if self.domain and self.domain not in config.get("remote_domains"):
            config.set("remote_domains", sorted(config.get("remote_domains") + [self.domain]))
            self.win.toast(_("Content from {domain} will now load automatically").format(domain=self.domain))
        self._render(True)

    def _load_remote(self, *_):
        self._render(True)

    def _attachments(self):
        while (c := self.atts.get_first_child()):
            self.atts.remove(c)
        items = mailparse.attachments(self.raw) if self.raw and self.row["has_att"] else []
        for idx, fname, ctype, size in items:
            box = Gtk.Box()
            box.add_css_class("linked")
            op = Gtk.Button(tooltip_text=_("Open"))
            inner = Gtk.Box(spacing=6)
            inner.append(Gtk.Image.new_from_icon_name("mail-attachment-symbolic"))
            inner.append(Gtk.Label(label=f"{fname} ({_fmt_size(size)})", ellipsize=Pango.EllipsizeMode.MIDDLE,
                                   max_width_chars=36))
            op.set_child(inner)
            op.connect("clicked", self._open_attachment, idx, fname)
            self._context_menu(op, idx, fname)
            sv = Gtk.Button(icon_name="document-save-symbolic", tooltip_text=_("Save as …"))
            sv.connect("clicked", self._save_attachment, idx, fname)
            box.append(op)
            box.append(sv)
            self.atts.append(box)
        self.atts.set_visible(bool(items))

    def _context_menu(self, button, idx, fname):
        """Rechtsklick: „Öffnen mit …“ und „Speichern unter …“."""
        pop = Gtk.Popover()
        pop.set_parent(button)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        for label, cb in ((_("Open with …"), lambda *_: self._open_attachment(None, idx, fname, ask=True)),
                          (_("Save as …"), lambda *_: self._save_attachment(None, idx, fname))):
            b = Gtk.Button(label=label)
            b.add_css_class("flat")
            b.connect("clicked", lambda _b, cb=cb: (pop.popdown(), cb()))
            box.append(b)
        pop.set_child(box)
        click = Gtk.GestureClick(button=3)
        click.connect("pressed", lambda g, n, x, y: pop.popup())
        button.add_controller(click)
        button.connect("destroy", lambda *_: pop.unparent())

    def _need_full(self, fname, action):
        """Bei nur teilweise geladener Nachricht erst vollständig nachladen (sonst wären Anhänge abgeschnitten)."""
        if not (self.row and self.row["partial"]):
            return False
        pk = self.pk
        self.win.toast(_("Loading “{name}” completely …").format(name=fname))

        def again():
            if self.pk != pk:
                return
            for idx, name, _t, _s in mailparse.attachments(self.raw):
                if name == fname:
                    action(idx)
                    return
            self.win.toast(_("Attachment not found"))

        self.win.load_full(pk, again)
        return True

    def _open_attachment(self, _b, idx, fname, ask=False):
        if self._need_full(fname, lambda i: self._open_attachment(None, i, fname, ask)):
            return
        openfile.open_data(self.win, fname, mailparse.attachment_bytes(self.raw, idx), self.pk, self.win.toast, ask)

    def _save_attachment(self, _b, idx, fname):
        if self._need_full(fname, lambda i: self._save_attachment(None, i, fname)):
            return
        dlg = Gtk.FileDialog(initial_name=fname)

        def done(d, res):
            try:
                f = d.save_finish(res)
            except GLib.Error:
                return
            f.replace_contents(mailparse.attachment_bytes(self.raw, idx), None, False, Gio.FileCreateFlags.NONE, None)
            self.win.toast(_("Saved: {name}").format(name=fname))

        dlg.save(self.win, None, done)

    def _policy(self, web, decision, dtype):
        if dtype not in (WebKit.PolicyDecisionType.NAVIGATION_ACTION, WebKit.PolicyDecisionType.NEW_WINDOW_ACTION):
            return False
        nav = decision.get_navigation_action()
        if nav.get_navigation_type() != WebKit.NavigationType.LINK_CLICKED \
                and dtype == WebKit.PolicyDecisionType.NAVIGATION_ACTION:
            return False   # load_html des eigenen Inhalts
        decision.ignore()
        if nav.get_navigation_type() == WebKit.NavigationType.LINK_CLICKED:
            self._open_uri(nav.get_request().get_uri() or "")
        return True

    def _open_uri(self, uri):
        if uri.lower().startswith("mailto:"):
            parts = urllib.parse.urlsplit(uri)
            q = urllib.parse.parse_qs(parts.query)
            self.win.compose_to(urllib.parse.unquote(parts.path), q.get("subject", [""])[0], q.get("body", [""])[0])
        elif uri.lower().startswith(("http://", "https://", "tel:")):
            Gio.AppInfo.launch_default_for_uri(uri, None)
