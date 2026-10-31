"""Test: Datenordner-Umzug, Thunderbird-Import (mbox/Maildir), lokales Archiv (IMAP-Stub), Sync lässt Archiv unberührt."""
import os
import sys
import tempfile
import time

root = tempfile.mkdtemp()
os.environ["XDG_CONFIG_HOME"] = root + "/cfg"
os.environ["XDG_DATA_HOME"] = root + "/old"
os.environ["XDG_CONFIG_HOME"] = root + "/cfg"
os.makedirs(root + "/old/postfach")
os.makedirs(root + "/cfg/postfach")
open(root + "/old/postfach/mail.db", "wb").write(b"")        # Platzhalter: wird beim Start zum Ziel verschoben
sys.path.insert(0, __file__.rsplit("/tools/", 1)[0])
os.environ["HOME"] = root                                      # Dokumente-Ordner = <root>/Documents
from postfach import paths  # noqa: E402

assert paths.DATA_DIR == root + "/Documents/Postfach", paths.DATA_DIR
assert os.path.exists(paths.DB_PATH) and not os.path.exists(root + "/old/postfach/mail.db"), "Umzug"
os.remove(paths.DB_PATH)

from postfach import archive, db, thunderbird  # noqa: E402


def mail(i, subj, date="Mon, 5 Oct 2020 10:00:00 +0200", frm="a@x.de", to="me@x.de", body="Hallo"):
    return (f"From: Absender <{frm}>\r\nTo: {to}\r\nSubject: {subj}\r\nDate: {date}\r\nMessage-ID: <{i}@t>\r\n"
            f"Content-Type: text/plain; charset=utf-8\r\n\r\n{body} Müller\r\n").encode()


# ---- Thunderbird-Profil
prof = root + "/tb/abc.default"
os.makedirs(prof + "/ImapMail/imap.x.de/INBOX.sbd")
os.makedirs(prof + "/Mail/Local Folders/Archiv2019.sbd")
os.makedirs(prof + "/ImapMail/imap.x.de/Entwürfe.sbd", exist_ok=True)


def mbox(path, msgs, status="0001"):
    with open(path, "wb") as f:
        for m in msgs:
            f.write(b"From - Mon Oct  5 10:00:00 2020\nX-Mozilla-Status: " + status.encode() + b"\n" + m.replace(b"\r\n", b"\n") + b"\n")


mbox(prof + "/ImapMail/imap.x.de/INBOX", [mail(1, "Eins"), mail(2, "Zwei", body="Zeile\nFrom hier\n>From dort")])
mbox(prof + "/ImapMail/imap.x.de/Gesendet", [mail(3, "Gesendet eins", frm="me@x.de", to="a@x.de")])
mbox(prof + "/ImapMail/imap.x.de/Papierkorb", [mail(9, "Weg")])
mbox(prof + "/Mail/Local Folders/Projekt", [mail(4, "Projekt"), mail(8, "Projekt gesendet", frm="me@x.de", to="a@x.de")])
mbox(prof + "/Mail/Local Folders/Weg", [mail(5, "Gelöscht")], status="0009")   # als gelöscht markiert
os.makedirs(prof + "/Mail/Local Folders/Md/cur")
open(prof + "/Mail/Local Folders/Md/cur/1:2,S", "wb").write(mail(6, "Maildir"))
open(prof + "/Mail/Local Folders/Posteingang", "wb").write(b"From - Mon Oct  5 10:00:00 2020\n" + mail(7, "Lokal").replace(b"\r\n", b"\n"))
open(prof + "/prefs.js", "w").write('user_pref("mail.server.server1.directory-rel", "[ProfD]ImapMail/imap.x.de");\n'
                                    'user_pref("mail.server.server1.userName", "me@x.de");\n'
                                    'user_pref("mail.server.server1.hostname", "imap.x.de");\n')
with open(prof + "/ImapMail/imap.x.de/Projekt.msf", "w") as f:
    f.write("kein mbox")

acc = db.add_account(name="Ich", email="me@x.de", kind="imap", imap_host="imap.x.de", username="me@x.de")
c = db.conn()
c.execute("INSERT INTO folders(account_id,name,role) VALUES (?,?,?)", (acc, "INBOX", "inbox"))
c.execute("INSERT INTO folders(account_id,name,role) VALUES (?,?,?)", (acc, "Gesendet", "sent"))
c.commit()

srcs = thunderbird.scan(prof)
labels = {(s["role"], s["label"]) for s in srcs}
assert ("inbox", "INBOX") in labels and ("sent", "Gesendet") in labels and ("inbox", "Posteingang") in labels, labels
assert not any("Papierkorb" in s["label"] or "Entw" in s["label"] for s in srcs), labels
assert any(s["label"] == "Md" and s["kind"] == "maildir" for s in srcs), labels

st = thunderbird.run_import(srcs)
print(st)
assert st == {"imported": 7, "duplicates": 0, "skipped": 1}, st
n = lambda role: len(db.query({"kind": role}))
assert n("unified") == 5, n("unified")          # Eins, Zwei (Konto me@x.de), Lokal, Projekt, Maildir (lokales Archiv)
assert n("sent") == 2                            # Gesendet eins + gemischter Ordner nach Absender
assert any(r["subject"] == "Projekt gesendet" for r in db.query({"kind": "sent"}))
assert any(r["subject"] == "Lokal" for r in db.query({"kind": "unified"}))
assert db.query({"kind": "unified"}, "Müller") and db.query({"kind": "sent"}, "gesendet")
two = [r for r in db.query({"kind": "unified"}) if r["subject"] == "Zwei"][0]
assert b"From hier" in db.raw_message(two["id"]) and b"\nFrom dort" in db.raw_message(two["id"])
assert two["account_id"] == acc                  # dem passenden Konto zugeordnet
again = thunderbird.run_import(srcs)
assert again["imported"] == 0, again             # erneuter Import: nur Duplikate

# ---- Archiv mit IMAP-Stub
class Stub:
    capabilities = ("UIDPLUS",)

    def __init__(self, mails):
        self.mails, self.deleted, self.copied = mails, [], []

    def select(self, name, readonly=False):
        self.cur = name
        return "OK", [b"1"]

    def uid(self, cmd, *a):
        if cmd == "SEARCH":
            assert a[1] in ("BEFORE", "ALL")
            hit = a[1] == "BEFORE" and self.cur == '"INBOX"'      # ALL (Sync) liefert nichts: Server ist leer
            return "OK", [" ".join(map(str, self.mails) if hit else []).encode()]
        if cmd == "FETCH":
            out = []
            for u in map(int, a[0].split(",")):
                out.append((f"{u} (UID {u} FLAGS (\\Seen) BODY[] {{1}}".encode(), self.mails[u]))
                out.append(b")")
            return "OK", out
        if cmd == "STORE":
            self.deleted += a[0].split(",")
        if cmd == "COPY":
            self.copied.append(a)
        return "OK", [b""]

    def response(self, name):
        return name, [b"7"]

    def expunge(self):
        return "OK", []

    def logout(self):
        pass


import postfach.imapsync as imapsync  # noqa: E402
old = {101: mail(101, "Alt eins", "Mon, 5 Oct 2020 10:00:00 +0200"), 102: mail(1, "Eins", "Mon, 5 Oct 2020 10:00:00 +0200"),
       103: mail(103, "Alt drei")}
# 102 hat dieselbe Message-ID wie der Thunderbird-Import -> Duplikat, trotzdem vom Server löschen
stub = Stub(old)
imapsync.connect = lambda a: stub
acc_row = db.account(acc)
done = archive._imap_account(acc_row, time.time() - 86400 * 365, None)
assert done == 3 and sorted(stub.deleted) == ["101", "102", "103"], (done, stub.deleted)
subs = sorted(r["subject"] for r in db.query({"kind": "unified"}))
assert subs.count("Eins") == 1 and "Alt eins" in subs and "Alt drei" in subs, subs
assert all(r["uid"] < 0 for r in db.conn().execute("SELECT uid FROM messages WHERE archived=1"))

# Sync-Abgleich (Server meldet nichts) darf Archiviertes nicht löschen
imapsync.connect = lambda a: type("S", (Stub,), {})({})
im = Stub({})
before = db.conn().execute("SELECT COUNT(*) FROM messages").fetchone()[0]
imapsync.sync_folder(im, acc_row, "INBOX", 50)
after = db.conn().execute("SELECT COUNT(*) FROM messages").fetchone()[0]
assert before == after, (before, after)

# vorhandene synchronisierte Zeile wird umgewandelt statt dupliziert
raw = mail(500, "Synchron", "Mon, 5 Oct 2020 10:00:00 +0200")
m = __import__("postfach.mailparse", fromlist=["x"]).parse(raw)
m.update(account_id=acc, folder="INBOX", uid=500, seen=0, flagged=1)
pk = db.insert_message(m, raw)
db.conn().commit()
archive.store(acc, "INBOX", "inbox", raw, True, True, existing=pk)
r = db.message(pk)
assert r["archived"] == 1 and r["uid"] < 0
# Remote-Aktionen ignorieren Archiviertes
assert imapsync._split([pk]) == ([], [])
# Kontoentfernung behält Archiviertes im lokalen Archiv
db.remove_account(acc)
assert db.message(pk) and db.message(pk)["account_id"] == db.local_account()["id"]
assert archive.cutoff_ts(24, __import__("datetime").datetime(2026, 10, 27)) == __import__("datetime").datetime(2024, 10, 27).timestamp()
print("OK")
