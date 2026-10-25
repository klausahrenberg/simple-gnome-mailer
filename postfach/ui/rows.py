"""Zeile der Nachrichtenliste."""
import time
from email.utils import getaddresses

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GObject, Gtk, Pango

from .. import config, contacts, logos
from ..i18n import _
from ..mailparse import display_subject


class MessageItem(GObject.Object):
    def __init__(self, row, account_label="", color=None):
        super().__init__()
        self.d = dict(row)
        self.account_label = account_label
        self.color = color
        self.show_to = False

    on_star = None   # wird vom Fenster gesetzt: Klick auf den Stern in der Zeile

    @GObject.Signal
    def changed(self):
        """Daten des Eintrags wurden geändert – gebundene Zeile zeichnet sich neu."""

    @property
    def pk(self):
        return self.d["id"]


def format_date(ts):
    if not ts:
        return ""
    t = time.localtime(ts)
    now = time.localtime()
    if t[:3] == now[:3]:
        return time.strftime("%H:%M", t)
    if now.tm_year == t.tm_year:
        return time.strftime(_("%m-%d"), t)
    return time.strftime(_("%Y-%m-%d"), t)


def _label(css=None, ellipsize=True, xalign=0.0):
    l = Gtk.Label(xalign=xalign)
    if ellipsize:
        l.set_ellipsize(Pango.EllipsizeMode.END)
    for c in css or ():
        l.add_css_class(c)
    return l


class MessageRow(Gtk.Box):
    def __init__(self):
        super().__init__(spacing=12)
        for side in ("top", "bottom"):
            getattr(self, f"set_margin_{side}")(9)
        self.set_margin_start(12)
        self.set_margin_end(0)
        self.avatar = Adw.Avatar(size=40, show_initials=True)
        self.avatar.set_valign(Gtk.Align.START)
        self.append(self.avatar)

        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, hexpand=True, margin_end=2)
        self.append(col)
        self.bar = Gtk.Box(valign=Gtk.Align.CENTER, height_request=26, width_request=3, margin_end=4)     # Kontofarbe am rechten Rand
        self.bar.add_css_class("acc-bar")
        self.append(self.bar)

        top = Gtk.Box(spacing=6)
        self.dot = Gtk.Box(valign=Gtk.Align.CENTER)
        self.dot.add_css_class("unread-dot")
        top.append(self.dot)
        self.sender = _label()
        self.sender.set_hexpand(True)
        self.clip = Gtk.Image.new_from_icon_name("mail-attachment-symbolic")
        self.clip.add_css_class("dim-label")
        self.star = Gtk.Button(icon_name="starred-symbolic", tooltip_text=_("Remove flag"), valign=Gtk.Align.CENTER)
        self.star.add_css_class("flat")
        self.star.add_css_class("circular")
        self.star.add_css_class("warning")
        self.star.add_css_class("row-star")
        self.star.connect("clicked", lambda *_: self._item and self._item.on_star and self._item.on_star(self._item))
        self._item, self._hid = None, None
        self.date = _label(["caption", "dim-label", "numeric"], ellipsize=False, xalign=1.0)
        for w in (self.sender, self.clip, self.star, self.date):
            top.append(w)
        col.append(top)

        sub = Gtk.Box(spacing=6)
        self.subject = _label()
        self.subject.set_hexpand(True)
        self.acc = _label(["caption", "dim-label"], xalign=1.0)
        self.acc.set_max_width_chars(24)
        sub.append(self.subject)
        sub.append(self.acc)
        col.append(sub)

        self.preview = Gtk.Label(xalign=0.0, yalign=0.0, wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR,
                                 ellipsize=Pango.EllipsizeMode.END, lines=2, hexpand=True)
        self.preview.add_css_class("dim-label")
        col.append(self.preview)

    def bind(self, item):
        self.unbind()
        self._item = item
        self._hid = item.connect("changed", lambda *_: self._refresh(item))
        self._refresh(item)

    def unbind(self):
        if self._item is not None and self._hid is not None:
            self._item.disconnect(self._hid)
        self._item, self._hid = None, None

    def _refresh(self, item):
        d = item.d
        name, addr = d["from_name"] or d["from_addr"] or "?", d["from_addr"]
        if item.show_to and d.get("to_addrs"):
            rcpt = getaddresses([d["to_addrs"]])
            if rcpt:
                name, addr = rcpt[0][0] or rcpt[0][1] or name, rcpt[0][1]
                if len(rcpt) > 1:
                    name += f" +{len(rcpt) - 1}"
        self.avatar.set_text(name)
        self.avatar.set_custom_image(contacts.texture(addr) or logos.texture(name, addr))
        self.sender.set_text(name)
        self.subject.set_text(display_subject(d["subject"]))
        self.date.set_text(format_date(d["date"]))
        self.preview.set_text(d["snippet"] or "")
        self.preview.set_lines(int(config.get("preview_lines")))
        self.clip.set_visible(bool(d["has_att"]))
        self.star.set_visible(bool(d["flagged"]))
        self.acc.set_text(item.account_label)
        self.acc.set_visible(bool(item.account_label))
        for c in [c for c in self.bar.get_css_classes() if c.startswith("acc-color-")]:
            self.bar.remove_css_class(c)
        self.bar.set_visible(item.color is not None)
        if item.color is not None:
            self.bar.add_css_class(f"acc-color-{item.color}")
        unread = not d["seen"]
        self.dot.set_visible(unread)
        for w in (self.sender, self.subject):
            (w.add_css_class if unread else w.remove_css_class)("heading")
        # ungelesen: Uhrzeit in Akzentfarbe und fett, sonst gedämpft
        (self.date.remove_css_class if unread else self.date.add_css_class)("dim-label")
        (self.date.add_css_class if unread else self.date.remove_css_class)("unread-time")
