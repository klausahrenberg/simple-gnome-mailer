"""Karte für Termineinladungen im Lesebereich: Daten anzeigen, zu-/absagen, in den Kalender übernehmen."""
import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk

from .. import db, imapsync, invite
from ..i18n import _, ngettext
from . import openfile


def _row(grid, r, label, text, css=None):
    l = Gtk.Label(label=label, xalign=1, yalign=0)
    l.add_css_class("dim-label")
    v = Gtk.Label(label=text, xalign=0, yalign=0, wrap=True, selectable=True, hexpand=True)
    if css:
        v.add_css_class(css)
    grid.attach(l, 0, r, 1, 1)
    grid.attach(v, 1, r, 1, 1)
    return v


class InviteCard(Gtk.Box):
    def __init__(self, win):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=10, margin_start=18, margin_end=18, margin_bottom=12)
        self.win = win
        self.add_css_class("card")
        self.set_visible(False)
        self.inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, margin_top=14, margin_bottom=14,
                             margin_start=16, margin_end=16)
        self.append(self.inner)
        self.inv = None
        self.pk = None
        self.buttons = {}

    # ------------------------------------------------------------ Anzeige
    def show_invite(self, inv, row):
        while (c := self.inner.get_first_child()):
            self.inner.remove(c)
        self.buttons = {}
        self.inv, self.pk, self.row = inv, row["id"], row
        if inv is None:
            self.set_visible(False)
            return
        cancelled = inv.method == "CANCEL" or inv.status == "CANCELLED"

        head = Gtk.Box(spacing=10)
        head.append(Gtk.Image.new_from_icon_name("x-office-calendar-symbolic"))
        title = Gtk.Label(label=inv.summary or _("(no title)"), xalign=0, wrap=True, hexpand=True, selectable=True)
        title.add_css_class("title-4")
        if cancelled:
            title.add_css_class("dim-label")
        head.append(title)
        self.inner.append(head)

        if cancelled:
            note = Gtk.Label(label=_("This event was cancelled."), xalign=0)
            note.add_css_class("error")
            self.inner.append(note)
        elif inv.method == "REPLY":
            who = inv.attendees[0] if inv.attendees else None
            if who:
                text = {"ACCEPTED": _("{name} accepted the invitation."), "DECLINED": _("{name} declined the invitation."),
                        "TENTATIVE": _("{name} may attend.")}.get(who.partstat, _("{name} replied to the invitation."))
                note = Gtk.Label(label=text.format(name=who.display), xalign=0, wrap=True)
                self.inner.append(note)
        elif inv.sequence > 0 and inv.method == "REQUEST":
            pass

        grid = Gtk.Grid(column_spacing=12, row_spacing=4)
        r = 0
        _row(grid, r, _("When"), invite.when_text(inv)); r += 1
        if inv.rrule:
            _row(grid, r, "", invite.repeat_text(inv), "dim-label"); r += 1
        if inv.location:
            _row(grid, r, _("Where"), inv.location); r += 1
        if inv.organizer.email or inv.organizer.name:
            o = inv.organizer
            _row(grid, r, _("Organizer"), f"{o.name} <{o.email}>" if o.name and o.email else o.display); r += 1
        others = [a for a in inv.attendees if a.email.lower() != inv.organizer.email.lower()]
        if others:
            names = ", ".join(a.display for a in others[:4])
            if len(others) > 4:
                names += " " + _("+{n} more").format(n=len(others) - 4)
            _row(grid, r, _("Attendees"), names); r += 1
        self.inner.append(grid)

        actions = Gtk.Box(spacing=8)
        accounts = [a["email"] for a in db.mail_accounts()]
        me_organizer = inv.organizer.email.lower() in {a.lower() for a in accounts}
        if inv.method == "REQUEST" and not cancelled and not me_organizer and inv.organizer.email:
            current = db.invite_response(self.pk)
            for key, label in (("accept", _("Accept")), ("tentative", _("Maybe")), ("decline", _("Decline"))):
                b = Adw.SplitButton(label=label)       # Klick = sofort antworten; Pfeil = „Mit Nachricht …“
                if current == key:
                    b.add_css_class("suggested-action")
                b.connect("clicked", lambda _b, k=key: self._respond(k))
                pop = Gtk.Popover()
                item = Gtk.Button(label=_("With a message …"))
                item.add_css_class("flat")
                item.connect("clicked", lambda _b, k=key, p=pop: (p.popdown(), self._ask_with_message(k)))
                pop.set_child(item)
                b.set_popover(pop)
                actions.append(b)
                self.buttons[key] = b
        if not cancelled:
            cal = Gtk.Button(label=_("Add to calendar"))
            cal.add_css_class("flat")
            cal.connect("clicked", self._add_to_calendar)
            actions.append(Gtk.Box(hexpand=True))
            actions.append(cal)
        self.inner.append(actions)
        if self.buttons and db.invite_response(self.pk):
            sent = Gtk.Label(label={"accept": _("You accepted this invitation."), "tentative": _("You answered “maybe”."),
                                    "decline": _("You declined this invitation.")}[db.invite_response(self.pk)], xalign=0)
            sent.add_css_class("dim-label")
            self.inner.append(sent)
        self.set_visible(True)

    # ------------------------------------------------------------ Aktionen
    def _add_to_calendar(self, *_args, tentative=False):
        inv = self.inv
        name = (inv.summary or "event")[:60] + ".ics"
        try:
            openfile.open_data(self.win, name, invite.importable(inv, tentative).encode("utf-8"), f"invite-{self.pk}", self.win.toast)
        except OSError as e:
            self.win.toast(_("Opening failed: {error}").format(error=e))

    def _ask_with_message(self, action):
        """Dialog: Antwort (Zusage/Vielleicht/Absage) mit einer Nachricht an den Organisator."""
        headings = {"accept": _("Accept with a message"), "tentative": _("“Maybe” with a message"),
                    "decline": _("Decline with a message")}
        d = Adw.AlertDialog(heading=headings[action],
                            body=_("The message is sent to {name} together with your response.").format(name=self.inv.organizer.display))
        text = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR, top_margin=8, bottom_margin=8, left_margin=10, right_margin=10)
        d.set_extra_child(Gtk.ScrolledWindow(child=text, min_content_height=110, has_frame=True))
        d.add_response("cancel", _("Cancel"))
        d.add_response("send", _("Send"))
        d.set_response_appearance("send", Adw.ResponseAppearance.SUGGESTED)
        d.set_default_response("send")
        d.set_close_response("cancel")

        def resp(_d, r):
            if r == "send":
                buf = text.get_buffer()
                self._respond(action, buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False))

        d.connect("response", resp)
        d.present(self.win)
        text.grab_focus()

    def _respond(self, action, comment=""):
        inv, row, pk = self.inv, self.row, self.pk
        acc = db.account(row["account_id"])
        mine = invite.my_attendee(inv, [a["email"] for a in db.mail_accounts()])
        my_email = mine.email if mine else acc["email"]
        text = invite.build_reply(inv, action, my_email, acc["name"] or "", comment)
        args = (acc, inv.organizer.email, "", invite.reply_subject(inv, action), invite.reply_body(inv, action, comment))
        for b in self.buttons.values():
            b.set_sensitive(False)

        def work():
            try:
                imapsync.send_mail(*args, ics=("REPLY", text))
                GLib.idle_add(done, None)
            except Exception as e:
                err = str(e)
                GLib.idle_add(done, err)

        def done(err):
            if err:
                for b in self.buttons.values():
                    b.set_sensitive(True)
                self.win.toast(_("Sending failed: {error}").format(error=err))
            else:
                db.set_invite_response(pk, action)
                self.win.toast(_("Response sent"))
                self.win.sync.request()
                if self.pk == pk:
                    self.show_invite(inv, row)
                    if action in ("accept", "tentative"):      # Zusage/Vielleicht: gleich den Kalendereintrag anlegen
                        self._add_to_calendar(tentative=action == "tentative")
            return False

        threading.Thread(target=work, daemon=True).start()
