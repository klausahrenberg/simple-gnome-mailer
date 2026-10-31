"""Verfassen-Fenster: Nur-Text oder formatiert (HTML) – je nach Format der beantworteten Nachricht."""
import base64
import html as htmllib
import os
import re
import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("WebKit", "6.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Adw, Gdk, GdkPixbuf, Gio, GLib, GObject, Gtk, WebKit

from email.utils import getaddresses

from .. import addressbook, config, contacts, db, imageshrink, imapsync, logos, mailparse
from . import openfile
from ..i18n import _, ngettext

CSP = "default-src 'none'; img-src data:; style-src 'unsafe-inline'"   # Skripte der zitierten Mail bleiben blockiert
EDITOR_CSS = ("body { font: 14px/1.5 sans-serif; margin: 12px 16px; background: #fff; color: #1c1c1c; "
              "color-scheme: light; } blockquote { margin: 0 0 0 .8ex; border-left: 2px solid #c0c0c0; "
              "padding-left: 1ex; } img { max-width: 100%; }")
MAX_ATTACH = 25 * 1024 * 1024
INLINE_MAX_BYTES = 600_000     # Bilder bis zu dieser Größe (ggf. als JPEG verkleinert) kommen in den Text, größere werden Anhänge
INLINE_MAX_PX = 1200


def _size(n):
    if n < 1024:
        return f"{n} B"
    return f"{n / 1024:.0f} KB" if n < 1024 * 1024 else f"{n / 1024 / 1024:.1f} MB"


def make_avatar(name, addr, size):
    """Adw.Avatar mit Kontaktfoto, Firmenlogo oder Initialen – wie in der Nachrichtenliste."""
    av = Adw.Avatar(size=size, show_initials=True, text=name or addr or "?")
    av.set_custom_image(contacts.texture(addr) or logos.texture(name, addr))
    return av


class AddressCompleter:
    """Vorschläge unter einem Adressfeld (An/Cc/Bcc): Kontakte und bisherige Empfänger; mehrere Adressen per Komma."""

    def __init__(self, row):
        self.row, self.items, self._busy = row, [], False
        self.pop = Gtk.Popover(autohide=False, has_arrow=False, position=Gtk.PositionType.BOTTOM, halign=Gtk.Align.START)
        self.pop.set_parent(row)
        self.pop.set_can_focus(False)
        self.list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE, can_focus=False)
        self.list.add_css_class("navigation-sidebar")
        self.list.connect("row-activated", lambda _l, r: self._choose(r.idx))
        self.pop.set_child(self.list)
        self.avatar_box = Gtk.Box(valign=Gtk.Align.CENTER, visible=False)
        row.add_prefix(self.avatar_box)
        row.connect("changed", self._changed)
        row.connect("changed", lambda *_: self.refresh_avatar())
        row.connect("destroy", lambda *_: self.pop.unparent())
        keys = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self._key)
        row.add_controller(keys)
        focus = Gtk.EventControllerFocus()
        focus.connect("leave", lambda *_: GLib.timeout_add(150, lambda: (self.pop.popdown(), False)[1]))
        row.add_controller(focus)

    def refresh_avatar(self):
        """Symbol (Foto/Logo/Initialen) des ersten Empfängers im Feld; keine Zeilenhöhe zusätzlich."""
        while (c := self.avatar_box.get_first_child()):
            self.avatar_box.remove(c)
        first = next(((n, a) for n, a in getaddresses([self.row.get_text()]) if "@" in a), None)
        self.avatar_box.set_visible(bool(first))
        if first:
            self.avatar_box.append(make_avatar(first[0], first[1], 28))

    def _token(self):
        text, pos = self.row.get_text(), self.row.get_position()
        pos = len(text) if pos < 0 else pos
        start = max(text.rfind(",", 0, pos), text.rfind(";", 0, pos)) + 1
        return start, pos, text[start:pos].strip()

    def _changed(self, *_):
        # die Cursorposition steht erst nach dem „changed“-Signal fest → im Leerlauf auswerten
        if not self._busy and not getattr(self, "_pending", False):
            self._pending = True
            GLib.idle_add(self._update)

    def _update(self):
        self._pending = False
        _s, _e, tok = self._token()
        self.items = addressbook.suggest(tok) if len(tok) >= 1 and "<" not in tok else []
        while (c := self.list.get_first_child()):
            self.list.remove(c)
        for i, (name, addr) in enumerate(self.items):
            r = Gtk.ListBoxRow()
            r.idx = i
            h = Gtk.Box(spacing=10, margin_top=4, margin_bottom=4, margin_start=8, margin_end=8)
            h.append(make_avatar(name, addr, 28))
            b = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER)
            if name:
                b.append(Gtk.Label(label=name, xalign=0))
                sub = Gtk.Label(label=addr, xalign=0)
                sub.add_css_class("dim-label")
                sub.add_css_class("caption")
                b.append(sub)
            else:
                b.append(Gtk.Label(label=addr, xalign=0))
            h.append(b)
            r.set_child(h)
            self.list.append(r)
        if self.items:
            self.pop.set_size_request(max(280, self.row.get_width() - 24), -1)
            self.list.select_row(self.list.get_row_at_index(0))
            self.pop.popup()
        else:
            self.pop.popdown()
        return False

    def _choose(self, idx):
        name, addr = self.items[idx]
        start, end, _tok = self._token()
        text = self.row.get_text()
        new = text[:start].rstrip() + (" " if text[:start].strip() else "") + addressbook.format_entry(name, addr) + ", "
        self._busy = True
        self.row.set_text(new + text[end:].lstrip())
        self.row.set_position(len(new))
        self._busy = False
        self.pop.popdown()

    def _key(self, _c, keyval, _code, _state):
        if not self.pop.get_visible() or not self.items:
            return False
        cur = self.list.get_selected_row()
        i = cur.idx if cur else 0
        if keyval in (Gdk.KEY_Down, Gdk.KEY_Up):
            i = (i + (1 if keyval == Gdk.KEY_Down else -1)) % len(self.items)
            self.list.select_row(self.list.get_row_at_index(i))
            return True
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter, Gdk.KEY_Tab):
            self._choose(i)
            return True
        if keyval == Gdk.KEY_Escape:
            self.pop.popdown()
            return True
        return False


class ComposeWindow(Adw.Window):
    """Verfassen: formatiert (HTML, Standard) oder Nur-Text; Anhänge per Knopf, Ziehen und Einfügen (Strg+V)."""

    def __init__(self, parent, account_id=None, to="", cc="", bcc="", subject="", body="", body_html=None,
                 in_reply_to=None, references=None, attachments=None):
        super().__init__(transient_for=parent, default_width=760, default_height=700, title=_("New message"))
        self.parent_win = parent
        self.in_reply_to, self.references = in_reply_to, references
        self.accs = list(db.mail_accounts())
        self.attachments = list(attachments or [])
        self.web = None
        self.html_mode = body_html is not None
        self._pasted = 0

        tv = Adw.ToolbarView()
        header = Adw.HeaderBar()
        self.send_btn = Gtk.Button(label="Senden")
        self.send_btn.add_css_class("suggested-action")
        self.send_btn.connect("clicked", self._send)
        header.pack_end(self.send_btn)
        self.spinner = Gtk.Spinner()
        header.pack_end(self.spinner)
        self.attach_btn = Gtk.Button(icon_name="mail-attachment-symbolic",
                                     tooltip_text=_("Add attachment … (also by drag and drop or Ctrl+V)"))
        self.attach_btn.connect("clicked", self._add_attachment)
        # Cc/Bcc sind zunächst ausgeblendet und per Schalter einblendbar (vorbelegte Felder sind sichtbar)
        self.cc_btn = Gtk.ToggleButton(label="Cc", active=bool(cc), tooltip_text=_("Show/hide the Cc field"))
        self.bcc_btn = Gtk.ToggleButton(label="Bcc", active=bool(bcc), tooltip_text=_("Show/hide the Bcc field"))
        if not self.html_mode:          # Nur-Text: Büroklammer, Cc, Bcc oben links (spart Platz); formatiert: in der Werkzeugleiste
            header.pack_start(self.attach_btn)
            header.pack_start(self.cc_btn)
            header.pack_start(self.bcc_btn)
        tv.add_top_bar(header)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        fields = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        fields.add_css_class("boxed-list")
        for side in ("top", "start", "end"):
            getattr(fields, f"set_margin_{side}")(12)
        fields.set_margin_bottom(6)
        self.from_row = Adw.ComboRow(title=_("From"), model=Gtk.StringList.new([a["email"] for a in self.accs]))
        for setter in (self.from_row.set_factory, self.from_row.set_list_factory):   # Adressen vollständig anzeigen
            f = Gtk.SignalListItemFactory()
            f.connect("setup", lambda _f, li: li.set_child(Gtk.Label(xalign=0, margin_start=6, margin_end=6)))
            f.connect("bind", lambda _f, li: li.get_child().set_text(li.get_item().get_string()))
            setter(f)
        if account_id:
            for i, a in enumerate(self.accs):
                if a["id"] == account_id:
                    self.from_row.set_selected(i)
        self.to_row = Adw.EntryRow(title=_("To"), text=to)
        self.cc_row = Adw.EntryRow(title="Cc", text=cc)
        self.bcc_row = Adw.EntryRow(title="Bcc", text=bcc)
        self.sub_row = Adw.EntryRow(title=_("Subject"), text=subject)
        for r in (self.from_row, self.to_row, self.cc_row, self.bcc_row, self.sub_row):
            fields.append(r)
        self.completers = [AddressCompleter(r) for r in (self.to_row, self.cc_row, self.bcc_row)]
        for btn, row in ((self.cc_btn, self.cc_row), (self.bcc_btn, self.bcc_row)):
            btn.bind_property("active", row, "visible", GObject.BindingFlags.SYNC_CREATE)
            btn.connect("toggled", lambda b, r=row: b.get_active() and r.grab_focus())
        box.append(fields)

        self.chips = Adw.WrapBox(child_spacing=6, line_spacing=6, margin_start=12, margin_end=12, margin_bottom=6)
        box.append(self.chips)
        self._fill_chips()

        self.fmt_bar = self._format_bar() if self.html_mode else Gtk.Box()
        box.append(self.fmt_bar)

        self.text = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR, top_margin=10, bottom_margin=10,
                                 left_margin=16, right_margin=16, vexpand=True)
        self.text.get_buffer().set_text(body)
        self.text.get_buffer().place_cursor(self.text.get_buffer().get_start_iter())
        sc = Gtk.ScrolledWindow(vexpand=True)
        sc.set_child(self.text)
        self.stack = Gtk.Stack(vexpand=True)
        self.stack.add_named(sc, "text")
        box.append(self.stack)
        if self.html_mode:
            self._ensure_web(body_html)
        self._show_mode()
        self._init = (to, cc, bcc, subject, body, len(self.attachments))     # Ausgangszustand für „bereits begonnen?“
        self._force = self._asking_close = False
        self.connect("close-request", self._on_close)
        self._logo_cb = lambda: [c.refresh_avatar() for c in self.completers]   # nachgeladene Logos nachzeichnen
        logos.on_ready(self._logo_cb)
        self.connect("destroy", lambda *_: logos.off_ready(self._logo_cb))
        for c in self.completers:
            c.refresh_avatar()

        # Dateien ablegen (Ziehen aus der Dateiverwaltung) – vor dem Editor abfangen
        drop = Gtk.DropTarget.new(Gdk.FileList, Gdk.DragAction.COPY)
        drop.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        drop.connect("drop", self._on_drop)
        box.add_controller(drop)
        # Strg+V: Bilder / Dateien aus der Zwischenablage
        keys = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self._on_key)
        self.add_controller(keys)

        self.toasts = Adw.ToastOverlay(child=box)
        tv.set_content(self.toasts)
        self.set_content(tv)
        (self.to_row if not to else self._editor()).grab_focus()

    # ---------------------------------------------------------- Format / Editor
    def _editor(self):
        return self.web if self.html_mode and self.web else self.text

    def _doc(self, inner):
        return (f'<!doctype html><html><head><meta charset="utf-8">'
                f'<meta http-equiv="Content-Security-Policy" content="{CSP}"><style>{EDITOR_CSS}</style></head>'
                f"<body>{inner}</body></html>")

    def _ensure_web(self, inner_html):
        """WebKit-Editor beim ersten Bedarf anlegen (Skripte der zitierten Mail bleiben per CSP blockiert)."""
        if self.web is None:
            settings = WebKit.Settings(enable_javascript=True, enable_media=False, enable_webgl=False,
                                       enable_developer_extras=False, allow_file_access_from_file_urls=False)
            self.web = WebKit.WebView(settings=settings, vexpand=True, hexpand=True, editable=True)
            self.web.connect("decide-policy", self._no_navigation)
            self._html0 = None

            def loaded(web, ev):                 # Ausgangsinhalt (normalisiert) merken
                if ev == WebKit.LoadEvent.FINISHED and self._html0 is None:
                    web.evaluate_javascript("document.body.innerHTML", -1, None, None, None,
                                            lambda w, r: setattr(self, "_html0", w.evaluate_javascript_finish(r).to_string()))

            self.web.connect("load-changed", loaded)
            self.stack.add_named(self.web, "html")
        self.web.load_html(self._doc(inner_html), "about:blank")

    def _show_mode(self):
        self.stack.set_visible_child_name("html" if self.html_mode else "text")
        self.fmt_bar.set_visible(self.html_mode)

    def _format_bar(self):
        bar = Gtk.Box(spacing=2, margin_start=12, margin_end=12, margin_bottom=4)
        bar.add_css_class("toolbar")
        for icon, tip, cmd in (("format-text-bold-symbolic", _("Bold"), "Bold"),
                               ("format-text-italic-symbolic", _("Italic"), "Italic"),
                               ("format-text-underline-symbolic", _("Underline"), "Underline"),
                               ("view-list-bullet-symbolic", _("Bulleted list"), "InsertUnorderedList"),
                               ("view-list-ordered-symbolic", _("Numbered list"), "InsertOrderedList")):
            b = Gtk.Button(icon_name=icon, tooltip_text=tip)
            b.add_css_class("flat")
            b.connect("clicked", lambda _b, c=cmd: (self.web.execute_editing_command(c), self.web.grab_focus()))
            bar.append(b)
        link = Gtk.Button(icon_name="insert-link-symbolic", tooltip_text=_("Insert link …"))
        link.add_css_class("flat")
        link.connect("clicked", self._insert_link)
        bar.append(link)
        self.attach_btn.add_css_class("flat")
        bar.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL, margin_top=4, margin_bottom=4, margin_start=4, margin_end=4))
        bar.append(self.attach_btn)
        for b in (self.cc_btn, self.bcc_btn):
            b.add_css_class("flat")
            bar.append(b)
        return bar

    def _insert_link(self, *_args):
        d = Adw.AlertDialog(heading=_("Insert link"), body=_("Link the selected text to this address:"))
        entry = Gtk.Entry(placeholder_text="https://…", activates_default=True)
        d.set_extra_child(entry)
        d.add_response("cancel", _("Cancel"))
        d.add_response("ok", _("Insert"))
        d.set_response_appearance("ok", Adw.ResponseAppearance.SUGGESTED)
        d.set_default_response("ok")

        def resp(_d, r):
            url = entry.get_text().strip()
            if r == "ok" and url.lower().startswith(("http://", "https://", "mailto:")):
                self.web.execute_editing_command_with_argument("CreateLink", url)
            self.web.grab_focus()

        d.connect("response", resp)
        d.present(self)

    @staticmethod
    def _no_navigation(web, decision, dtype):
        if dtype in (WebKit.PolicyDecisionType.NAVIGATION_ACTION, WebKit.PolicyDecisionType.NEW_WINDOW_ACTION):
            nav = decision.get_navigation_action()
            if nav.get_navigation_type() != WebKit.NavigationType.OTHER or dtype == WebKit.PolicyDecisionType.NEW_WINDOW_ACTION:
                decision.ignore()                 # Links im Editor nicht folgen
                return True
        return False

    # ---------------------------------------------------------- Anhänge
    def _fill_chips(self):
        """Anhänge als Schaltflächen: Klick öffnet die Datei mit der Standard-App, nur das Kreuz entfernt sie."""
        while (c := self.chips.get_first_child()):
            self.chips.remove(c)
        for i, (name, ctype, data) in enumerate(self.attachments):
            chip = Gtk.Box()
            chip.add_css_class("linked")
            op = Gtk.Button(tooltip_text=_("Open (right-click: Open with …)"))
            inner = Gtk.Box(spacing=6)
            inner.append(Gtk.Image.new_from_icon_name("mail-attachment-symbolic"))
            inner.append(Gtk.Label(label=f"{name} ({_size(len(data))})", max_width_chars=32, ellipsize=3))
            op.set_child(inner)
            op.connect("clicked", lambda _b, i=i: self._open_attachment(i))
            pop = Gtk.Popover()
            pop.set_parent(op)
            ask = Gtk.Button(label=_("Open with …"))
            ask.add_css_class("flat")
            ask.connect("clicked", lambda _b, i=i, pop=pop: (pop.popdown(), self._open_attachment(i, ask=True)))
            pop.set_child(ask)
            click = Gtk.GestureClick(button=3)
            click.connect("pressed", lambda g, n, x, y, pop=pop: pop.popup())
            op.add_controller(click)
            op.connect("destroy", lambda *_, pop=pop: pop.unparent())
            rm = Gtk.Button(icon_name="window-close-symbolic", tooltip_text=_("Remove attachment"))
            rm.connect("clicked", lambda _b, i=i: (self.attachments.pop(i), self._fill_chips()))
            chip.append(op)
            chip.append(rm)
            self.chips.append(chip)
        self.chips.set_visible(bool(self.attachments))

    def _open_attachment(self, i, ask=False):
        if i >= len(self.attachments):
            return
        name, _ctype, data = self.attachments[i]
        try:
            openfile.open_data(self, name, data, f"compose-{id(self)}", lambda t: self.toasts.add_toast(Adw.Toast(title=t)), ask)
        except OSError as e:
            self.toasts.add_toast(Adw.Toast(title=_("Opening failed: {error}").format(error=e)))

    def _attach_bytes(self, name, ctype, data):
        total = sum(len(a[2]) for a in self.attachments) + len(data)
        if total > MAX_ATTACH:
            self.toasts.add_toast(Adw.Toast(title=_("“{name}” was not added – attachments would exceed 25 MB").format(name=name)))
            return False
        self.attachments.append((name, ctype or "application/octet-stream", bytes(data)))
        self._fill_chips()
        GLib.idle_add(self._check_size)          # nach dem letzten Anhang einer Gruppe einmal prüfen
        return True

    def _attach_file(self, gfile):
        try:
            ok, data, _etag = gfile.load_contents(None)
        except GLib.Error as e:
            self.toasts.add_toast(Adw.Toast(title=_("Reading failed: {error}").format(error=e.message)))
            return
        name = os.path.basename(gfile.get_path() or gfile.get_basename() or "attachment")
        self._attach_bytes(name, Gio.content_type_guess(name, data)[0], data)

    def _add_attachment(self, *_args):
        dlg = Gtk.FileDialog(title=_("Add attachment"))

        def done(d, res):
            try:
                files = d.open_multiple_finish(res)
            except GLib.Error:
                return
            for f in files:
                self._attach_file(f)

        dlg.open_multiple(self, None, done)

    def _on_drop(self, _target, value, _x, _y):
        files = value.get_files() if hasattr(value, "get_files") else []
        for f in files:
            self._attach_file(f)
        return bool(files)

    # ---------------------------------------------------------- Verkleinern vorschlagen
    def _check_size(self):
        """Große Einzelbilder oder zu große Gesamtgröße: Verkleinern anbieten (Grenzen in den Einstellungen)."""
        if getattr(self, "_asking", False) or not config.get("shrink_prompt"):
            return False
        mb = 1024 * 1024
        img_limit, total_limit = config.get("shrink_image_mb") * mb, config.get("shrink_total_mb") * mb
        declined = getattr(self, "_declined", set())
        todo = [(i, a) for i, a in enumerate(self.attachments)
                if imageshrink.is_shrinkable(a[1]) and len(a[2]) > 300_000 and (a[0], len(a[2])) not in declined]
        if not todo:
            return False
        total = sum(len(a[2]) for a in self.attachments)
        big = [a for _i, a in todo if len(a[2]) > img_limit]
        if not big and total <= total_limit:
            return False
        self._asking = True
        if big:
            why = ngettext("“{name}” is {size} – images over {limit} MB should be reduced before sending.",
                           "“{name}” ({size}) and {more} more images are large – images over {limit} MB should be reduced before sending.",
                           len(big)).format(name=big[0][0], size=_size(len(big[0][2])), more=len(big) - 1, limit=config.get("shrink_image_mb"))
        else:
            why = _("The attachments total {size} (limit {limit} MB). Many mail servers reject large messages.").format(
                size=_size(total), limit=config.get("shrink_total_mb"))
        d = Adw.AlertDialog(heading=_("Reduce image size?"), body=why + "\n" + _("The originals on your computer stay unchanged."))
        combo = Adw.ComboRow(title=_("Target size"), model=Gtk.StringList.new([p[0] for p in imageshrink.PRESETS]))
        last = config.get("image_target_px")
        combo.set_selected(next((i for i, p in enumerate(imageshrink.PRESETS) if p[1] == last), 0))
        lb = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        lb.add_css_class("boxed-list")
        lb.append(combo)
        d.set_extra_child(lb)
        d.add_response("keep", _("Send originals"))
        d.add_response("shrink", _("Reduce"))
        d.set_response_appearance("shrink", Adw.ResponseAppearance.SUGGESTED)
        d.set_default_response("shrink")
        d.set_close_response("keep")

        def resp(_d, r):
            self._asking = False
            if r == "shrink":
                px = imageshrink.PRESETS[combo.get_selected()][1]
                config.set("image_target_px", px)
                self._shrink_attachments([a for _i, a in todo], px)
            else:
                self._declined = declined | {(a[0], len(a[2])) for _i, a in todo}

        d.connect("response", resp)
        d.present(self)
        return False

    def _shrink_attachments(self, items, px):
        before = sum(len(a[2]) for a in self.attachments)
        self.spinner.start()
        self.toasts.add_toast(Adw.Toast(title=_("Reducing images …"), timeout=2))

        def work():
            done = {}
            for name, ctype, data in items:
                r = imageshrink.shrink(data, name, px)
                if r:
                    done[(name, len(data))] = r
            GLib.idle_add(finish, done)

        def finish(done):
            self.spinner.stop()
            new = []
            for name, ctype, data in self.attachments:
                r = done.get((name, len(data)))
                new.append((r[2], r[1], r[0]) if r else (name, ctype, data))
            self.attachments = new
            self._fill_chips()
            after = sum(len(a[2]) for a in self.attachments)
            msg = (_("Attachments reduced from {before} to {after}").format(before=_size(before), after=_size(after))
                   if after < before else _("No reduction possible"))
            if after > config.get("shrink_total_mb") * 1024 * 1024:
                msg += " " + _("– still over {limit} MB (the other attachments are not images)").format(limit=config.get("shrink_total_mb"))
            self.toasts.add_toast(Adw.Toast(title=msg, timeout=6))
            return False

        threading.Thread(target=work, daemon=True).start()

    # ---------------------------------------------------------- Zwischenablage (Strg+V)
    def _on_key(self, _c, keyval, _code, state):
        paste = (keyval in (Gdk.KEY_v, Gdk.KEY_V) and state & Gdk.ModifierType.CONTROL_MASK) or \
                (keyval == Gdk.KEY_Insert and state & Gdk.ModifierType.SHIFT_MASK)
        if not paste or isinstance(self.get_focus(), Gtk.Editable) and not isinstance(self.get_focus(), Gtk.TextView):
            return False             # Einfügen in Adressfelder/Betreff bleibt normaler Text
        cb = self.get_clipboard()
        fmts = cb.get_formats()
        if fmts.contain_gtype(Gdk.FileList):              # kopierte Dateien → Anhänge (jedes Format)
            def got_files(c, res):
                try:
                    for f in c.read_value_finish(res).get_files():
                        self._attach_file(f)
                except GLib.Error:
                    pass
            cb.read_value_async(Gdk.FileList, GLib.PRIORITY_DEFAULT, None, got_files)
            return True
        has_text = fmts.contain_mime_type("text/plain;charset=utf-8") or fmts.contain_mime_type("text/plain")
        is_image = fmts.contain_gtype(Gdk.Texture) or any(g.is_a(Gdk.Texture.__gtype__) for g in fmts.get_gtypes()) \
            or any(m.startswith("image/") for m in fmts.get_mime_types() or [])
        if is_image and not has_text:                     # reines Bild (z. B. Screenshot)
            cb.read_texture_async(None, self._got_texture)
            return True
        return False                 # normaler Text/HTML: Standardverhalten

    @staticmethod
    def _shrink(png):
        """PNG (Screenshots sind als PNG oft groß) als JPEG neu kodieren; mit Transparenz auf weißem Grund. -> (Bytes, MIME) oder None."""
        loader = GdkPixbuf.PixbufLoader()
        try:
            loader.write(png)
            loader.close()
        except GLib.Error:
            return None
        pb = loader.get_pixbuf()
        if pb is None:
            return None
        if pb.get_has_alpha():                       # JPEG kennt keine Transparenz: auf weißen Grund legen
            w, h = pb.get_width(), pb.get_height()
            bg = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, w, h)
            bg.fill(0xFFFFFFFF)
            pb.composite(bg, 0, 0, w, h, 0, 0, 1, 1, GdkPixbuf.InterpType.BILINEAR, 255)
            pb = bg
        for quality in ("88", "75"):
            ok, data = pb.save_to_bufferv("jpeg", ["quality"], [quality])
            if ok and len(data) <= INLINE_MAX_BYTES:
                return bytes(data), "image/jpeg"
        return None

    def _got_texture(self, cb, res):
        try:
            tex = cb.read_texture_finish(res)
        except GLib.Error:
            return
        png = tex.save_to_png_bytes().get_data()
        if self.html_mode and self.web and max(tex.get_width(), tex.get_height()) <= INLINE_MAX_PX:
            img = (png, "image/png") if len(png) <= INLINE_MAX_BYTES else self._shrink(png)
            if img:                                       # passt in den Text (ggf. als JPEG verkleinert)
                uri = f"data:{img[1]};base64," + base64.b64encode(img[0]).decode()
                self.web.execute_editing_command_with_argument("InsertImage", uri)
                return
        self._pasted += 1                                 # zu groß (oder Nur-Text): Original als Anhang
        self._attach_bytes(_("Image-{n}.png").format(n=self._pasted), "image/png", png)
        self.toasts.add_toast(Adw.Toast(title=_("Image added as an attachment")))

    # ---------------------------------------------------------- Inhalt / Senden / Entwurf
    def _get_content(self, cb):
        """Inhalt holen (HTML asynchron aus dem Editor): cb(text, html|None) oder cb(None, fehler)."""
        if self.html_mode:
            def got(web, res):
                try:
                    inner = web.evaluate_javascript_finish(res).to_string()
                except GLib.Error as e:
                    cb(None, _("Editor content could not be read: {error}").format(error=e.message))
                    return
                cb(mailparse.html_to_text(inner), inner)

            self.web.evaluate_javascript("document.body.innerHTML", -1, None, None, None, got)
        else:
            buf = self.text.get_buffer()
            cb(buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False), None)

    def _args(self, text, html):
        acc = self.accs[self.from_row.get_selected()]
        clean = lambda row: row.get_text().replace(";", ",").strip(" ,")      # Komma nach der letzten Adresse entfernen
        return (acc, clean(self.to_row), clean(self.cc_row), self.sub_row.get_text(), text,
                self.in_reply_to, self.references, html, list(self.attachments), clean(self.bcc_row))

    def _send(self, *_args):
        if not self.accs:
            return
        if not self.to_row.get_text().strip(" ,;"):
            self.toasts.add_toast(Adw.Toast(title=_("Please enter a recipient")))
            return
        self.send_btn.set_sensitive(False)
        self.spinner.start()

        def have(text, html):
            if text is None:
                self._sent(html)
                return
            self._run(imapsync.send_mail, self._args(text, html), self._sent)

        self._get_content(have)

    def _run(self, fn, args, done):
        def work():
            try:
                fn(*args)
                GLib.idle_add(done, None)
            except Exception as e:
                err = str(e)
                GLib.idle_add(done, err)

        threading.Thread(target=work, daemon=True).start()

    def _sent(self, err):
        self.spinner.stop()
        if err:
            self.send_btn.set_sensitive(True)
            self.toasts.add_toast(Adw.Toast(title=_("Sending failed: {error}").format(error=err), timeout=8))
        else:
            self.parent_win.toast(_("Message sent"))
            self.parent_win.sync.request()
            self._force = True
            GLib.idle_add(lambda: self.close() and False)
        return False

    # ---------------------------------------------------------- Schließen: verwerfen oder Entwurf
    def _on_close(self, *_):
        if self._force:
            return False
        if not self._asking_close:
            self._asking_close = True
            self._is_dirty(self._after_dirty)
        return True                                   # erst nach Rückfrage schließen

    def _is_dirty(self, cb):
        """Wurde schon etwas geschrieben (Felder, Text, Anhänge)? cb(bool)"""
        to, cc, bcc, subject, body, n_att = self._init
        fields = (self.to_row.get_text(), self.cc_row.get_text(), self.bcc_row.get_text(), self.sub_row.get_text())
        if fields != (to, cc, bcc, subject) or len(self.attachments) != n_att:
            cb(True)
            return
        if self.html_mode:
            self.web.evaluate_javascript("document.body.innerHTML", -1, None, None, None,
                                         lambda w, r: cb(w.evaluate_javascript_finish(r).to_string() != self._html0))
        else:
            buf = self.text.get_buffer()
            cb(buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False) != body)

    def _after_dirty(self, dirty):
        if not dirty:
            self._force = True
            self.close()
            return
        d = Adw.AlertDialog(heading=_("Message not sent"), body=_("You have already started writing. "
                            "Discard the message or save it as a draft?"))
        d.add_response("cancel", _("Keep writing"))
        d.add_response("discard", _("Discard"))
        d.add_response("save", _("Save as draft"))
        d.set_response_appearance("discard", Adw.ResponseAppearance.DESTRUCTIVE)
        d.set_response_appearance("save", Adw.ResponseAppearance.SUGGESTED)
        d.set_default_response("save")
        d.set_close_response("cancel")

        def resp(_d, r):
            self._asking_close = False
            _d.force_close()                         # Dialog sicher schließen, bevor das Fenster geschlossen wird
            if r == "discard":
                self._force = True
                GLib.idle_add(lambda: self.close() and False)      # erst nach dem Schließen des Dialogs
            elif r == "save":
                self._save_draft()

        d.connect("response", resp)
        d.present(self)

    def _save_draft(self):
        if not self.accs:
            return
        self.spinner.start()

        def have(text, html):
            if text is None:
                self.spinner.stop()
                self.toasts.add_toast(Adw.Toast(title=html))
                return
            self._run(imapsync.save_draft, self._args(text, html), self._draft_saved)

        self._get_content(have)

    def _draft_saved(self, err):
        self.spinner.stop()
        if err:
            self.toasts.add_toast(Adw.Toast(title=_("Draft not saved: {error}").format(error=err), timeout=8))
        else:
            self.parent_win.toast(_("Draft saved"))
            acc = self.accs[self.from_row.get_selected()]
            folder = db.folder_by_role(acc["id"], "drafts")
            if folder:
                self.parent_win.sync.request_folder(acc["id"], folder)
            self._force = True
            GLib.idle_add(lambda: self.close() and False)
        return False
