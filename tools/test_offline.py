"""Test: Offline-Betrieb – lokale Wirkung sofort, Serveraktionen in der Warteschlange, stille Wiederholung."""
import os
import sys
import tempfile
import threading
import time

os.environ["POSTFACH_DATA_DIR"] = os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp()
sys.path.insert(0, __file__.rsplit("/tools/", 1)[0])
from postfach import db, imapsync, mailparse, ops  # noqa: E402

acc = db.add_account(name="Ich", email="me@x.de", kind="imap", imap_host="imap.x.de", username="me@x.de")
c = db.conn()
c.execute("INSERT INTO folders(account_id,name,role) VALUES (?,?,?)", (acc, "INBOX", "inbox"))
c.commit()
pks = []
for i in range(1, 5):
    raw = f"From: a@x.de\r\nTo: me@x.de\r\nSubject: S{i}\r\nDate: Mon, 5 Oct 2026 1{i}:00:00 +0200\r\nMessage-ID: <{i}@t>\r\n\r\nText\r\n".encode()
    m = mailparse.parse(raw)
    m.update(account_id=acc, folder="INBOX", uid=i, seen=0, flagged=0)
    pks.append(db.insert_message(m, raw))
c.commit()
inbox = lambda: [r["subject"] for r in db.query({"kind": "unified"})]
assert len(inbox()) == 4 and db.unread_counts()[(acc, "INBOX")] == 4

# Löschen: Nachricht verschwindet sofort aus Liste und Zählern
db.queue_op("delete", pks[0])
assert "S1" not in inbox() and len(inbox()) == 3 and db.unread_counts()[(acc, "INBOX")] == 3
assert db.message(pks[0]) is not None and db.pending_count() == 1

# offline: Aktion bleibt, Nachricht bleibt ausgeblendet
CALLS = []
def fake_delete(pks_):
    CALLS.append(("delete", list(pks_)))
    if FAIL:
        raise FAIL[0]
imapsync.remote_delete = fake_delete
imapsync.remote_mark_spam = lambda p: CALLS.append(("spam", list(p)))
imapsync.remote_set_flag = lambda p, f, on: CALLS.append(("flag", list(p), f, on))
FAIL = [OSError("Network is unreachable")]
off, err = ops.process()
assert off and err is None and db.pending_count() == 1 and "S1" not in inbox()
# wieder online: Aktion wird ausgeführt, Zeile endgültig gelöscht
FAIL.clear()
off, err = ops.process()
assert not off and db.pending_count() == 0 and db.message(pks[0]) is None, (off, err)
# Flags: lokal sofort, bleiben vor dem Server-Abgleich geschützt, spätere Änderung ersetzt frühere
db.set_flags(pks[1], seen=True)
db.queue_op("flag", pks[1], "\\Seen:1")
db.queue_op("flag", pks[1], "\\Seen:0")
db.queue_op("flag", pks[1], "\\Flagged:1")
assert db.pending_count() == 2 and db.is_recent(pks[1], seconds=0)
CALLS.clear()
ops.process()
assert ("flag", [pks[1]], "\\Seen", False) in CALLS and ("flag", [pks[1]], "\\Flagged", True) in CALLS and db.pending_count() == 0
# Spam
db.queue_op("spam", pks[2])
ops.process()
assert db.message(pks[2]) is None and ("spam", [pks[2]]) in CALLS
# dauerhafter Serverfehler: nach 3 Versuchen wieder anzeigen
FAIL.append(RuntimeError("Mailbox locked"))
db.queue_op("delete", pks[3])
assert "S4" not in inbox()
for _ in range(3):
    off, err = ops.process()
assert not off and err and "S4" in inbox() and db.pending_count() == 0, (err, inbox())
FAIL.clear()

# ---- SyncManager: einmal offline melden, danach still prüfen, wieder online
statuses, dones = [], []
state = {"net": False, "syncs": 0}
def fake_sync(acc_, *a, **kw):
    if not state["net"]:
        raise OSError("Network is unreachable")
    state["syncs"] += 1
    return 0
imapsync.sync_account = fake_sync
mgr = imapsync.SyncManager(lambda *a: dones.append(a), lambda s, t: statuses.append(s))
mgr._probe = lambda: state["net"]
mgr.thread.start()
def wait(cond, t=5):
    end = time.time() + t
    while time.time() < end and not cond():
        time.sleep(0.05)
    assert cond(), (statuses, dones)
wait(lambda: "offline" in statuses)
for _ in range(3):                               # weitere Versuche: keine weiteren Meldungen, keine Fehler
    mgr.request()
    time.sleep(0.3)
assert statuses.count("offline") == 1 and not [d for d in dones if d[2]], (statuses, dones)
db.queue_op("delete", db.insert_message(dict(mailparse.parse(b"From: z@x.de\r\nSubject: Z\r\nMessage-ID: <z@t>\r\n\r\nx"), account_id=acc, folder="INBOX", uid=99, seen=0, flagged=0), b"x"))
mgr.request_ops()
time.sleep(0.3)
assert db.pending_count() == 1
state["net"] = True
imapsync.remote_delete = fake_delete
mgr.request()
wait(lambda: "online" in statuses and state["syncs"] >= 1)
assert db.pending_count() == 0, "Warteschlange nach Wiederverbindung abgearbeitet"
mgr.stop()
print("OK")
