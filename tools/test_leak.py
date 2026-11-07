"""Test: Listenzeilen und -einträge werden nach dem Neuaufbau der Liste wieder freigegeben (früher blieben je Neuaufbau
~400 Zeilen / ~14 MB im Speicher: Signalfunktion Zeile -> Stern -> Funktion -> Zeile)."""
import gc
import os
import sys
import tempfile
import weakref

os.environ["POSTFACH_DATA_DIR"] = os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp()
sys.path.insert(0, __file__.rsplit("/tools/", 1)[0])
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk

Adw.init()
from postfach.ui.rows import MessageItem, MessageRow  # noqa: E402

row = {"id": 1, "account_id": 1, "folder": "INBOX", "uid": 1, "subject": "S", "from_name": "N", "from_addr": "n@x.de", "date": 1,
       "snippet": "x", "seen": 0, "flagged": 0, "answered": 0, "has_att": 0, "has_cal": 0, "to_addrs": "", "archived": 0}
# Die Schaltflächen müssen funktionieren (Stern, Rückgängig): die Zeile wird – wie in der Liste – nur von C gehalten
calls = []
live = MessageRow()
it = MessageItem(row)
it.on_star = lambda i: calls.append("star")
live.bind(it)
live.star.emit("clicked")
ph = MessageItem(row)
ph.placeholder, ph.undo_pks, ph.on_undo = True, [1], lambda i: calls.append("undo")
live.bind(ph)
live.undo_box.get_last_child().get_prev_sibling().get_next_sibling().emit("clicked") if False else None
btn = live.undo_box.get_first_child()
while btn is not None and not isinstance(btn, Gtk.Button):
    btn = btn.get_next_sibling()
btn.emit("clicked")
assert calls == ["star", "undo"], calls
dead = []
for _ in range(50):
    r = MessageRow()
    item = MessageItem(row)
    r.bind(item)
    r.unbind()
    dead.append((weakref.ref(r), weakref.ref(item)))
    del r, item
gc.collect()
alive = sum(1 for a, b in dead if a() is not None or b() is not None)
assert alive == 0, f"{alive} von 50 Zeilen/Einträgen nicht freigegeben (Speicherleck)"
print("OK – Listenzeilen werden freigegeben")
