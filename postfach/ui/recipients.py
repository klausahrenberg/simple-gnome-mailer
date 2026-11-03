"""Empfängerfelder (An/Cc/Bcc) mit „Bubbles“: Adressen werden nach der Eingabe zu Blasen mit Kontaktsymbol, Name und
Adresse; das Feld bleibt editierbar (weitere Adressen tippen, Blasen anklicken zum Bearbeiten, Rückschritt/Kreuz entfernt)."""
import re
from email.utils import formataddr, getaddresses

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GLib, GObject, Gtk

from .. import addressbook, contacts, logos

CSS = """
.recipient-chip { background: alpha(currentColor, .12); border-radius: 20px; padding: 2px 4px 2px 3px; }
.recipient-chip:hover { background: alpha(currentColor, .2); }
.recipient-chip button.recipient-x { min-width: 18px; min-height: 18px; padding: 0; margin: 0; }
.recipient-text { background: transparent; box-shadow: none; outline: none; border: none; }
"""
_css_done = False
CHIP_H = 38   # Höhe aller Bubbles (mit und ohne Namen)
LINE = 49     # Zeilenhöhe wie bei den Adw.EntryRow (Betreff); bei vielen Empfängern ein Vielfaches davon


def _ensure_css():
    global _css_done
    if not _css_done and Gdk.Display.get_default():
        p = Gtk.CssProvider()
        p.load_from_string(CSS)
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), p, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        _css_done = True


def make_avatar(name, addr, size):
    """Adw.Avatar mit Kontaktfoto, Firmenlogo oder Initialen – wie in der Nachrichtenliste."""
    av = Adw.Avatar(size=size, show_initials=True, text=name or addr or "?")
    av.set_custom_image(contacts.texture(addr) or logos.texture(name, addr))
    return av


def parse_addresses(text):
    """[(Name, Adresse)] aus einer Adressliste; tolerant gegenüber Endkommas und Semikolons."""
    text = (text or "").replace(";", ",").strip(" ,")
    try:
        pairs = getaddresses([text], strict=False)
    except TypeError:                       # ältere Python-Versionen kennen `strict` nicht
        pairs = getaddresses([text])
    return [(n, a) for n, a in pairs if a]


def make_bubble(name, addr, size=28):
    """Bubble: Kontaktsymbol, Name und darunter kleiner die Adresse (ohne Namen nur die Adresse). -> (Bubble, Textspalte)"""
    name = contacts.name_for(addr) or name          # bekannter Kontakt: dessen Name hat Vorrang
    chip = Gtk.Box(spacing=6, valign=Gtk.Align.CENTER, height_request=CHIP_H)   # alle Bubbles gleich hoch
    chip.add_css_class("recipient-chip")
    chip.append(make_avatar(name, addr, size))
    col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER)
    if name and name != addr:
        col.append(Gtk.Label(label=name, xalign=0, max_width_chars=26, ellipsize=3))
        sub = Gtk.Label(label=addr, xalign=0, max_width_chars=30, ellipsize=3)
        sub.add_css_class("caption")
        sub.add_css_class("dim-label")
        col.append(sub)
    else:
        col.append(Gtk.Label(label=addr, xalign=0, max_width_chars=32, ellipsize=3))
    chip.append(col)
    chip.set_tooltip_text(f"{name} <{addr}>" if name and name != addr else addr)
    return chip, col


_drag = {"row": None, "i": None}      # laufender Ziehvorgang (nur innerhalb des Fensters)


class RecipientDrag(GObject.Object):
    """Inhalt des Ziehvorgangs: eigener Typ (kein Text), damit das Eingabefeld die Bubble nicht als Text einfügt."""

    def __init__(self, row, index):
        super().__init__()
        self.row, self.index = row, index


class RecipientRow(Gtk.ListBoxRow):
    """Zeile mit Bezeichnung, Bubbles und Eingabefeld. Schnittstelle wie ein Textfeld: get_text/set_text/grab_focus/changed."""
    __gsignals__ = {"changed": (GObject.SignalFlags.RUN_FIRST, None, ())}

    def __init__(self, title, text=""):
        super().__init__(activatable=False, selectable=False)
        _ensure_css()
        self.entries = []          # [(Name, Adresse)]
        self._busy = False
        box = Gtk.Box(spacing=10, margin_start=12, margin_end=12)
        lab = Gtk.Label(label=title, xalign=0, valign=Gtk.Align.START, width_request=34, height_request=LINE)
        lab.add_css_class("dim-label")
        lab.add_css_class("caption")
        box.append(lab)
        self.flow = Adw.WrapBox(child_spacing=6, line_spacing=0, hexpand=True, valign=Gtk.Align.START)
        self.text = Gtk.Text(width_chars=12, max_width_chars=12, height_request=LINE)
        self.text.add_css_class("recipient-text")
        self.flow.append(self.text)
        # zunächst nur eine Zeile (der Rest ist verdeckt, „+N“ zeigt wie viele); beim Bearbeiten oder per Knopf ganz
        self.expanded, self.overflow = False, 0
        self.scroll = Gtk.ScrolledWindow(child=self.flow, hexpand=True, vscrollbar_policy=Gtk.PolicyType.EXTERNAL,
                                         hscrollbar_policy=Gtk.PolicyType.NEVER, propagate_natural_height=False,
                                         min_content_height=LINE)
        block = Gtk.EventControllerScroll(flags=Gtk.EventControllerScrollFlags.VERTICAL)
        block.connect("scroll", lambda *a: True)
        self.scroll.add_controller(block)
        box.append(self.scroll)
        self.more = Gtk.Button(valign=Gtk.Align.START, margin_top=(LINE - 28) // 2, visible=False,
                               focusable=False, focus_on_click=False)
        self.more.add_css_class("flat")
        self.more.add_css_class("circular")
        self.more.connect("clicked", lambda *_: self._toggle())
        box.append(self.more)
        self.flow.connect("notify::width", lambda *_: GLib.idle_add(self._measure))
        self.scroll.connect("notify::width", lambda *_: GLib.idle_add(self._measure))
        self.set_child(box)
        self.on_drag = None                       # Hook(bool): Ziehen beginnt/endet (blendet Cc/Bcc zum Ablegen ein)
        drop = Gtk.DropTarget.new(RecipientDrag, Gdk.DragAction.MOVE)      # Bubbles hierher ziehen (zwischen An/Cc/Bcc, Reihenfolge)
        drop.connect("enter", self._drop_enter)
        drop.connect("leave", lambda *_: GLib.idle_add(self._left_drag))
        drop.connect("drop", self._on_drop)
        self.add_controller(drop)
        self.text.connect("changed", self._on_text)
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._on_key)
        self.text.add_controller(keys)
        focus = Gtk.EventControllerFocus()
        focus.connect("enter", lambda *_: self._set_expanded(True))
        focus.connect("leave", lambda *_: GLib.idle_add(self._left))
        self.text.add_controller(focus)
        click = Gtk.GestureClick()              # Klick in die freie Fläche der Zeile: ins Eingabefeld
        click.connect("pressed", lambda *_: self.text.grab_focus())
        box.add_controller(click)
        if text:
            self.set_text(text)

    # ------------------------------------------------------------ Schnittstelle
    def get_text(self):
        parts = [addressbook.format_entry(n, a) for n, a in self.entries]
        pending = self.text.get_text().strip(" ,;")
        if pending:
            parts.append(pending)
        return ", ".join(parts)

    def set_text(self, text):
        self.entries = []
        self._busy = True
        self.text.set_text("")
        self._busy = False
        self._add_text(text, final=True)
        self._render()
        self.emit("changed")

    def grab_focus(self):
        return self.text.grab_focus()

    def commit(self):
        """Angefangene Eingabe als Adresse übernehmen (beim Verlassen des Feldes, vor dem Senden)."""
        if self.text.get_text().strip(" ,;") and self._add_text(self.text.get_text(), final=True, from_entry=True):
            self.emit("changed")

    def add_address(self, name, addr):
        if addr.lower() not in {a.lower() for _n, a in self.entries}:
            self.entries.append((name, addr))
        self._busy = True
        self.text.set_text("")
        self._busy = False
        self._render()
        self.emit("changed")

    def refresh(self):
        """Bubbles neu zeichnen (z. B. nachdem Logos geladen wurden)."""
        self._render()

    # ------------------------------------------------------------ Auf-/Zuklappen
    def _left(self):
        self.commit()
        if not self.text.has_focus():
            self._set_expanded(False)
        return False

    def _toggle(self):
        if self.expanded:
            self._set_expanded(False)
        else:
            self._set_expanded(True)
            self.text.grab_focus()

    def _set_expanded(self, on):
        if on != self.expanded:
            self.expanded = on
            self._apply()
            if not on:
                GLib.timeout_add(100, self._measure)

    def _apply(self):
        self.scroll.set_propagate_natural_height(self.expanded)
        self.more.set_visible(bool(self.overflow))
        if self.expanded:
            self.more.set_child(Gtk.Image.new_from_icon_name("pan-up-symbolic"))
        elif self.overflow:
            self.more.set_child(Gtk.Label(label=f"+{self.overflow}"))
        self.scroll.queue_resize()

    def _measure(self):
        """Wie viele Bubbles liegen außerhalb der ersten Zeile?"""
        if not self.flow.get_width():
            return False
        hidden = 0
        child = self.flow.get_first_child()
        while child is not None:
            if child is not self.text:
                ok, rect = child.compute_bounds(self.flow)
                if ok and rect.get_y() >= LINE - 1:
                    hidden += 1
            child = child.get_next_sibling()
        self.overflow = hidden
        self._apply()
        return False

    # ------------------------------------------------------------ Eingabe
    def _add_text(self, text, final, from_entry=False):
        """Adressen aus `text` übernehmen. Ohne final bleibt der letzte (noch ungetrennte) Teil im Feld stehen.
        -> True, wenn etwas geändert wurde."""
        ends = text.rstrip()[-1:] in (",", ";")
        pairs = [p for p in getaddresses([text.replace(";", ",").strip(" ,")]) if p != ("", "")]   # Endkomma verwirrt den strengen Parser
        keep = ""
        if not (final or ends) and pairs:
            n, a = pairs.pop()
            keep = formataddr((n, a)) if n else a
        have = {a.lower() for _n, a in self.entries}
        bad = []
        changed = False
        for n, a in pairs:
            if "@" in a and a.lower() not in have:
                self.entries.append((n, a))
                have.add(a.lower())
                changed = True
            elif "@" not in a and (n or a):
                bad.append(n or a)
        left = keep or " ".join(bad)
        if from_entry or changed or left != text:
            self._busy = True
            self.text.set_text(left)
            self.text.set_position(-1)
            self._busy = False
        if changed:
            self._render()
        return changed

    def _on_text(self, _t):
        if self._busy:
            return
        t = self.text.get_text()
        if "," in t or ";" in t:
            self._add_text(t, final=False, from_entry=True)
        self.emit("changed")

    def _on_key(self, _c, keyval, _code, _state):
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter) and self.text.get_text().strip():
            self.commit()
            return True
        if keyval == Gdk.KEY_Tab and self.text.get_text().strip():
            self.commit()
            return True
        if keyval == Gdk.KEY_BackSpace and not self.text.get_text() and self.entries:
            self._edit(len(self.entries) - 1)       # letzte Bubble zurück ins Feld: bearbeiten oder weiter löschen
            return True
        return False

    def _edit(self, i):
        n, a = self.entries.pop(i)
        self.commit()
        self._busy = True
        self.text.set_text(addressbook.format_entry(n, a))
        self._busy = False
        self.text.grab_focus()
        self.text.set_position(-1)
        self._render()
        self.emit("changed")

    def _remove(self, i):
        self.entries.pop(i)
        self._render()
        self.emit("changed")

    # ------------------------------------------------------------ Ziehen und Ablegen
    def _drop_enter(self, _t, _x, _y):
        self._set_expanded(True)                  # alle Bubbles sichtbar, damit überall abgelegt werden kann
        return Gdk.DragAction.MOVE

    def _left_drag(self):
        if not self.text.has_focus() and _drag["row"] is not None:
            self._set_expanded(False)
        return False

    def _index_at(self, x, y):
        """Einfügeposition in der Bubble-Reihenfolge für einen Punkt (Koordinaten der Zeile)."""
        i = 0
        child = self.flow.get_first_child()
        while child is not None:
            if child is not self.text:
                ok, r = child.compute_bounds(self)
                if ok and (y < r.get_y() or (y < r.get_y() + r.get_height() and x < r.get_x() + r.get_width() / 2)):
                    return i
                i += 1
            child = child.get_next_sibling()
        return len(self.entries)

    def _on_drop(self, _t, value, x, y):
        if not isinstance(value, RecipientDrag) or value.row is None:
            return False
        self.receive(value.row, value.index, self._index_at(x, y))
        return True

    def receive(self, src, i, index):
        """Bubble `i` der Zeile `src` an Position `index` in dieser Zeile einsetzen (verschiebt zwischen An/Cc/Bcc)."""
        entry = src.entries[i]
        if src is self:
            self.entries.pop(i)
            self.entries.insert(index - 1 if index > i else index, entry)
        else:
            src.entries.pop(i)
            if entry[1].lower() not in {a.lower() for _n, a in self.entries}:
                self.entries.insert(min(index, len(self.entries)), entry)
            src._render()
            src.emit("changed")
        self._render()
        self.emit("changed")

    def _drag_prepare(self, source, _x, _y, i):
        _drag["row"], _drag["i"] = self, i
        return Gdk.ContentProvider.new_for_value(RecipientDrag(self, i))

    def _drag_begin(self, source, _drag_obj, chip):
        source.set_icon(Gtk.WidgetPaintable.new(chip), 12, 12)
        if self.on_drag:
            self.on_drag(True)

    def _drag_end(self, *_):
        _drag["row"] = _drag["i"] = None
        if self.on_drag:
            self.on_drag(False)

    # ------------------------------------------------------------ Darstellung
    def _render(self):
        child = self.flow.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            if child is not self.text:
                self.flow.remove(child)
            child = nxt
        last = None
        for i, (name, addr) in enumerate(self.entries):
            self.flow.insert_child_after(self._bubble(i, name, addr), last)
            last = self.flow.get_first_child() if last is None else last.get_next_sibling()
        for ms in (0, 150, 500):                          # nach dem Layout erneut zählen (Breite steht erst dann fest)
            GLib.timeout_add(ms, self._measure)

    def _bubble(self, i, name, addr):
        slot = Gtk.Box(height_request=LINE)              # Zeile fester Höhe; die Bubble sitzt mittig darin
        chip, col = make_bubble(name, addr)
        slot.append(chip)
        x = Gtk.Button(icon_name="window-close-symbolic", valign=Gtk.Align.CENTER, tooltip_text=None)
        x.add_css_class("flat")
        x.add_css_class("circular")
        x.add_css_class("recipient-x")
        x.connect("clicked", lambda _b, i=i: self._remove(i))
        chip.append(x)
        click = Gtk.GestureClick()           # Klick (ohne Ziehen): Bubble zum Bearbeiten zurück ins Feld
        click.connect("released", lambda g, n, px, py, i=i: self._edit(i))
        col.add_controller(click)
        src = Gtk.DragSource(actions=Gdk.DragAction.MOVE)
        src.connect("prepare", self._drag_prepare, i)
        src.connect("drag-begin", self._drag_begin, chip)
        src.connect("drag-end", self._drag_end)
        chip.add_controller(src)
        return slot


class BubbleStrip(Gtk.Box):
    """Adressen als Bubbles für den Nachrichtenkopf: zunächst eine Zeile, ein Klick klappt alle auf (und wieder zu)."""
    SLOT = 40

    def __init__(self, label=None):
        super().__init__(spacing=10)
        _ensure_css()
        self.pairs, self.expanded, self.overflow = [], False, 0
        if label:
            lab = Gtk.Label(label=label, xalign=0, valign=Gtk.Align.START, width_request=34, height_request=self.SLOT)
            lab.add_css_class("dim-label")
            lab.add_css_class("caption")
            self.append(lab)
        self.flow = Adw.WrapBox(child_spacing=6, line_spacing=2, valign=Gtk.Align.START, hexpand=True)
        self.scroll = Gtk.ScrolledWindow(child=self.flow, hexpand=True, vscrollbar_policy=Gtk.PolicyType.EXTERNAL,
                                         hscrollbar_policy=Gtk.PolicyType.NEVER, propagate_natural_height=False,
                                         min_content_height=self.SLOT)
        block = Gtk.EventControllerScroll(flags=Gtk.EventControllerScrollFlags.VERTICAL)
        block.connect("scroll", lambda *a: True)         # Mausrad verschiebt die verdeckten Zeilen nicht
        self.scroll.add_controller(block)
        self.append(self.scroll)
        self.more = Gtk.Button(valign=Gtk.Align.START, margin_top=(self.SLOT - 28) // 2, visible=False)
        self.more.add_css_class("flat")
        self.more.add_css_class("circular")
        self.more.connect("clicked", lambda *_: self.toggle())
        self.append(self.more)
        click = Gtk.GestureClick()                        # Klick in den Kopfbereich klappt auf/zu
        click.connect("released", lambda *_: self.toggle())
        self.add_controller(click)
        self.flow.connect("notify::width", lambda *_: GLib.idle_add(self._measure))
        self.scroll.connect("notify::width", lambda *_: GLib.idle_add(self._measure))

    def set_addresses(self, pairs, empty_text=None):
        self.pairs, self.expanded, self.overflow = list(pairs), False, 0
        while (c := self.flow.get_first_child()):
            self.flow.remove(c)
        for name, addr in self.pairs:
            slot = Gtk.Box(height_request=self.SLOT)
            slot.append(make_bubble(name, addr, 26)[0])
            self.flow.append(slot)
        if not self.pairs and empty_text:
            lab = Gtk.Label(label=empty_text, xalign=0, height_request=self.SLOT)
            lab.add_css_class("dim-label")
            self.flow.append(lab)
        self._apply()
        for ms in (0, 150, 500):                          # nach dem Layout erneut zählen (Breite steht erst dann fest)
            GLib.timeout_add(ms, self._measure)

    def toggle(self):
        if self.overflow or self.expanded:
            self.expanded = not self.expanded
            self._apply()

    def _apply(self):
        self.scroll.set_propagate_natural_height(self.expanded)
        self.more.set_visible(bool(self.overflow) or self.expanded)
        if self.expanded:
            self.more.set_child(Gtk.Image.new_from_icon_name("pan-up-symbolic"))
        elif self.overflow:
            self.more.set_child(Gtk.Label(label=f"+{self.overflow}"))
        self.queue_resize()

    def _measure(self):
        """Wie viele Bubbles liegen außerhalb der ersten Zeile?"""
        if self.expanded or not self.flow.get_mapped() and not self.flow.get_width():
            return False
        hidden = 0
        child = self.flow.get_first_child()
        while child is not None:
            ok, rect = child.compute_bounds(self.flow)
            if ok and rect.get_y() >= self.SLOT - 1:
                hidden += 1
            child = child.get_next_sibling()
        if hidden != self.overflow:
            self.overflow = hidden
            self._apply()
        return False


class AddressCompleter:
    """Vorschläge unter einem Empfängerfeld: Kontakte und bisherige Empfänger; Auswahl wird sofort zur Bubble."""

    def __init__(self, row):
        self.row, self.items = row, []
        self.pop = Gtk.Popover(autohide=False, has_arrow=False, position=Gtk.PositionType.BOTTOM, halign=Gtk.Align.START)
        self.pop.set_parent(row)
        self.pop.set_can_focus(False)
        self.list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE, can_focus=False)
        self.list.add_css_class("navigation-sidebar")
        self.list.connect("row-activated", lambda _l, r: self._choose(r.idx))
        self.pop.set_child(self.list)
        row.text.connect("changed", self._changed)
        row.connect("destroy", lambda *_: self.pop.unparent())
        keys = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self._key)
        row.text.add_controller(keys)
        focus = Gtk.EventControllerFocus()
        focus.connect("leave", lambda *_: GLib.timeout_add(150, lambda: (self.pop.popdown(), False)[1]))
        row.text.add_controller(focus)

    def _changed(self, *_):
        if not getattr(self, "_pending", False):
            self._pending = True
            GLib.idle_add(self._update)

    def _update(self):
        self._pending = False
        tok = self.row.text.get_text().strip()
        self.items = addressbook.suggest(tok) if tok and "<" not in tok and "," not in tok else []
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
        self.pop.popdown()
        self.row.add_address(name, addr)
        self.row.text.grab_focus()

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
