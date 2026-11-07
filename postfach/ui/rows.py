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
        self.placeholder = False          # True: Platzhalter „gelöscht – Rückgängig“ statt einer Nachricht
        self.collapsing = False           # True: Platzhalter fährt gerade ein (danach wird der Eintrag entfernt)
        self.undo_pks, self.undo_text, self.on_undo = (), "", None

    on_delete = None   # wird vom Fenster gesetzt: Papierkorb-Knopf beim Überfahren der Zeile
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
        # Aufbau: untereinander zwei Einklapper – Nachricht und „gelöscht / Rückgängig“-Platzhalter. Beim Löschen, Zurückholen
        # und Entfernen fahren sie weich ein bzw. aus (die Zeile verändert ihre Höhe animiert statt zu springen).
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.set_margin_start(12)
        self.set_margin_end(0)
        self.inner = Gtk.Box(spacing=12, margin_top=9, margin_bottom=9)
        self.avatar = Adw.Avatar(size=40, show_initials=True)
        self.avatar.set_valign(Gtk.Align.START)
        self.inner.append(self.avatar)

        col = self.col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, hexpand=True, margin_end=2)
        self.inner.append(col)
        self.bar = Gtk.Box(valign=Gtk.Align.CENTER, height_request=26, width_request=3, margin_end=4)     # Kontofarbe am rechten Rand
        self.bar.add_css_class("acc-bar")
        self.inner.append(self.bar)

        top = Gtk.Box(spacing=6, height_request=22)      # feste Höhe: Stern/Schnellaktionen (Hover) verändern die Zeilenhöhe nicht
        self.dot = Gtk.Box(valign=Gtk.Align.CENTER)
        self.dot.add_css_class("unread-dot")
        top.append(self.dot)
        self.sender = _label()
        self.sender.set_hexpand(True)
        self.clip = Gtk.Image.new_from_icon_name("mail-attachment-symbolic")
        self.clip.add_css_class("dim-label")
        self.replied = Gtk.Image.new_from_icon_name("mail-replied-symbolic")
        self.replied.add_css_class("dim-label")
        self.replied.set_tooltip_text(_("You replied to this message"))
        self.cal = Gtk.Image.new_from_icon_name("x-office-calendar-symbolic")
        self.cal.add_css_class("dim-label")
        self.cal.set_tooltip_text(_("Calendar invitation"))
        self.star = Gtk.Button(icon_name="starred-symbolic", tooltip_text=_("Remove flag"), valign=Gtk.Align.CENTER)
        self.star.add_css_class("flat")
        self.star.add_css_class("circular")
        self.star.add_css_class("warning")
        self.star.add_css_class("row-star")
        # Die Signalfunktionen halten die Zeile (Zeile -> Stern -> Funktion -> Zeile): ein Kreis über C-Objekte, den Python
        # nicht auflöst. Deshalb sind sie nur zwischen bind und unbind verbunden – sonst blieben bei jedem Neuaufbau der Liste
        # ~400 Zeilen (~14 MB) im Speicher. (Schwache Referenzen genügen nicht: der Python-Wrapper der Zeile wird schon
        # vorher freigegeben und die Funktion fände die Zeile nicht mehr.)
        self._handlers = []              # (Widget, Handler-ID): nur zwischen bind und unbind verbunden
        self._item, self._hid = None, None
        self.date = _label(["caption", "dim-label", "numeric"], ellipsize=False, xalign=1.0)
        # beim Überfahren mit der Maus: Stern und Löschen anstelle des Datums
        self.hover = Gtk.Box(spacing=2, valign=Gtk.Align.CENTER, visible=False)
        self.hstar = Gtk.Button(icon_name="non-starred-symbolic", tooltip_text=_("Flag"), valign=Gtk.Align.CENTER)
        self.hdel = Gtk.Button(icon_name="user-trash-symbolic", tooltip_text=_("Delete"), valign=Gtk.Align.CENTER)
        for b in (self.hstar, self.hdel):
            b.add_css_class("flat")
            b.add_css_class("circular")
            b.add_css_class("row-star")          # kompakt wie der Stern: die Zeile wird beim Überfahren nicht höher
            self.hover.append(b)
        self._hover = False
        self._motion = Gtk.EventControllerMotion()
        self.add_controller(self._motion)
        for w in (self.sender, self.replied, self.cal, self.clip, self.star, self.date, self.hover):
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

        # Platzhalter nach dem Löschen: „Nachricht gelöscht“ und Rückgängig-Schaltfläche an der Stelle der Nachricht
        self.undo_box = Gtk.Box(spacing=12, hexpand=True, valign=Gtk.Align.CENTER, margin_top=8, margin_bottom=8)
        icon = Gtk.Image.new_from_icon_name("user-trash-symbolic")
        icon.add_css_class("dim-label")
        self.undo_label = Gtk.Label(xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.END)
        self.undo_label.add_css_class("dim-label")
        undo_btn = Gtk.Button(label=_("Undo"), valign=Gtk.Align.CENTER)
        undo_btn.add_css_class("suggested-action")
        self.undo_btn = undo_btn
        for w in (icon, self.undo_label, undo_btn):
            self.undo_box.append(w)
        self.undo_box.set_margin_end(12)
        self.content = Gtk.Revealer(child=self.inner, reveal_child=True, transition_type=Gtk.RevealerTransitionType.SLIDE_UP)
        self.undo_rev = Gtk.Revealer(child=self.undo_box, reveal_child=False, transition_type=Gtk.RevealerTransitionType.SLIDE_DOWN)
        self.append(self.content)
        self.append(self.undo_rev)
        self._state = None               # (Platzhalter?, wird eingeklappt?) – Änderungen werden animiert, das erste Binden nicht

        self.preview = Gtk.Label(xalign=0.0, yalign=0.0, wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR,
                                 ellipsize=Pango.EllipsizeMode.END, lines=2, hexpand=True)
        self.preview.add_css_class("dim-label")
        col.append(self.preview)

    def _star_clicked(self, *_a):
        if self._item and self._item.on_star:
            self._item.on_star(self._item)

    def _delete_clicked(self, *_a):
        if self._item and self._item.on_delete:
            self._item.on_delete(self._item)

    def _enter(self, *_a):
        self._set_hover(True)

    def _leave(self, *_a):
        self._set_hover(False)

    def _set_hover(self, on):
        """Schnellaktionen (Stern, Löschen) beim Überfahren der Zeile statt des Datums."""
        self._hover = bool(on and self._item and not self._item.placeholder)
        self.hover.set_visible(self._hover)
        if self._item and not self._item.placeholder:
            flagged = bool(self._item.d["flagged"])
            self.hstar.set_icon_name("starred-symbolic" if flagged else "non-starred-symbolic")
            (self.hstar.add_css_class if flagged else self.hstar.remove_css_class)("warning")
            self.hstar.set_tooltip_text(_("Remove flag") if flagged else _("Flag"))
            self.date.set_visible(not self._hover)
            self.star.set_visible(flagged and not self._hover)

    def _undo_clicked(self, *_a):
        if self._item and self._item.on_undo:
            self._item.on_undo(self._item)

    def bind(self, item):
        self.unbind()
        self._item = item
        self._handlers = [(self.star, self.star.connect("clicked", self._star_clicked)),
                          (self.hstar, self.hstar.connect("clicked", self._star_clicked)),
                          (self.hdel, self.hdel.connect("clicked", self._delete_clicked)),
                          (self.undo_btn, self.undo_btn.connect("clicked", self._undo_clicked)),
                          (self._motion, self._motion.connect("enter", self._enter)),
                          (self._motion, self._motion.connect("leave", self._leave))]
        self._hid = item.connect("changed", lambda *_a: self._refresh(item))
        self._refresh(item)

    def unbind(self):
        if self._item is not None and self._hid is not None:
            self._item.disconnect(self._hid)
        for w, hid in self._handlers:        # Kreis Zeile -> Schaltfläche -> Funktion -> Zeile auflösen (sonst Speicherleck)
            w.disconnect(hid)
        self._handlers = []
        self._item, self._hid = None, None
        self._state = None
        self._hover = False
        self.hover.set_visible(False)
        self.date.set_visible(True)

    def _refresh(self, item):
        d = item.d
        state = (item.placeholder, item.collapsing)
        ms = 240 if self._state is not None and self._state != state else 0       # nur Zustandswechsel animieren
        self._state = state
        for rev in (self.content, self.undo_rev):
            rev.set_transition_duration(ms)
        self.content.set_reveal_child(not item.placeholder)
        self.undo_rev.set_reveal_child(item.placeholder and not item.collapsing)
        if item.placeholder:
            self.undo_label.set_text(item.undo_text)
            return
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
        self.replied.set_visible(bool(d["answered"]))
        self.cal.set_visible(bool(d["has_cal"]))
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
        if self._hover:
            self._set_hover(True)
