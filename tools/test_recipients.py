"""Test: Empfänger-Bubbles (Eingabe, Einfügen, Bearbeiten, Entfernen) – braucht eine Anzeige (GTK)."""
import os
import sys
import tempfile

os.environ["POSTFACH_DATA_DIR"] = os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp()
sys.path.insert(0, __file__.rsplit("/tools/", 1)[0])
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gtk

Adw.init()
from postfach.ui.recipients import RecipientRow  # noqa: E402

changes = []
r = RecipientRow("An")
r.connect("changed", lambda *_: changes.append(1))
r.text.set_text("heike@x.de, lisa@y.de, par")           # Komma übernimmt fertige Adressen, Rest bleibt im Feld
assert r.entries == [("", "heike@x.de"), ("", "lisa@y.de")] and r.text.get_text() == "par", (r.entries, r.text.get_text())
assert r.get_text() == "heike@x.de, lisa@y.de, par"
r.text.set_text("")
r.text.set_text('Anna Beispiel <anna@x.de>; "Müller, Hans" <hans@x.de>;')   # Einfügen mit Semikolon und Komma im Namen
assert [a for _n, a in r.entries] == ["heike@x.de", "lisa@y.de", "anna@x.de", "hans@x.de"], r.entries
assert r.entries[3][0] == "Müller, Hans" and r.text.get_text() == ""
r.text.set_text("neu@x.de")                              # ohne Trennzeichen: bleibt Text, bis committet wird
assert len(r.entries) == 4
r.commit()
assert len(r.entries) == 5 and r.text.get_text() == ""
r.add_address("Dup", "HEIKE@x.de")                         # Duplikat wird nicht doppelt aufgenommen
assert len(r.entries) == 5
assert r._on_key(None, Gdk.KEY_BackSpace, 0, 0) and len(r.entries) == 4 and r.text.get_text() == "neu@x.de"   # Rückschritt: Bubble zum Bearbeiten
r._remove(0)
assert [a for _n, a in r.entries][0] == "lisa@y.de" and len(r.entries) == 3
r.text.set_text("kein-adresse")
r.commit()                                                 # ohne @ keine Bubble, Text bleibt erhalten
assert len(r.entries) == 3 and r.text.get_text() == "kein-adresse"
r.set_text("A <a@x.de>, b@x.de")
assert r.get_text() == "A <a@x.de>, b@x.de" and len(r.entries) == 2
assert changes
# Antworten: Namen mit Komma bleiben ein Empfänger (früher entstand „Boschi“ + „Robert“)
from email.utils import getaddresses  # noqa: E402
from postfach import composer  # noqa: E402
line = ", ".join([composer._fmt("Boschi, Robert", "robert@x.de"), composer._fmt("Elizabeth.Trapp", "e@x.de"), composer._fmt("Anna", "a@x.de")])
assert getaddresses([line]) == [("Boschi, Robert", "robert@x.de"), ("Elizabeth.Trapp", "e@x.de"), ("Anna", "a@x.de")], line
r2 = RecipientRow("Cc", line)
assert [a for _n, a in r2.entries] == ["robert@x.de", "e@x.de", "a@x.de"] and r2.text.get_text() == ""
# Ziehen: Reihenfolge ändern und zwischen Zeilen verschieben (Kern der Ablegen-Logik)
a, b = RecipientRow("An", "a1@x.de, a2@x.de, a3@x.de"), RecipientRow("Cc", "c1@x.de")
a.receive(a, 0, 3)                                         # a1 ans Ende
assert [x for _n, x in a.entries] == ["a2@x.de", "a3@x.de", "a1@x.de"]
a.receive(a, 2, 0)                                         # a1 wieder nach vorn
assert [x for _n, x in a.entries] == ["a1@x.de", "a2@x.de", "a3@x.de"]
b.receive(a, 1, 0)                                         # a2 nach Cc, vor c1
assert [x for _n, x in a.entries] == ["a1@x.de", "a3@x.de"] and [x for _n, x in b.entries] == ["a2@x.de", "c1@x.de"]
a.add_address("", "c1@x.de")
b.receive(a, 2, 5)                                         # schon vorhanden: nur aus der Quelle entfernen
assert [x for _n, x in b.entries] == ["a2@x.de", "c1@x.de"] and len(a.entries) == 2
print("OK – Empfänger-Bubbles")
