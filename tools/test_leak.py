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
