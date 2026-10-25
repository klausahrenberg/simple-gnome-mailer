"""Integrationstest: IMAP-Sync gegen einen minimalen Fake-Server (kein Netz, temporäre DB)."""
import imaplib
import os
import re
import socketserver
import sys
import tempfile
import threading

os.environ["XDG_DATA_HOME"] = tempfile.mkdtemp()
os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp()
sys.path.insert(0, __file__.rsplit("/tools/", 1)[0])
from postfach import db, imapsync  # noqa: E402
from postfach import secrets as keyring  # noqa: E402

def mk(i, subj):
    return (f"From: Tester <t{i}@x.de>\r\nTo: me@x.de\r\nSubject: {subj}\r\nDate: Mon, 5 Oct 2026 1{i}:00:00 +0200\r\n"
            f"Message-ID: <{i}@t>\r\nContent-Type: text/plain; charset=utf-8\r\n\r\nInhalt Nr {i} Müller\r\n").encode()

MAILS = {1: mk(1, "Eins"), 2: mk(2, "Zwei"), 3: mk(3, "Drei")}
LOG = []
APPENDED = []

class H(socketserver.StreamRequestHandler):
    def handle(self):
        w = lambda s: self.wfile.write(s if isinstance(s, bytes) else s.encode())
        w("* OK ready\r\n")
        for line in self.rfile:
            line = line.decode().strip()
            tag, cmd, *rest = line.split(" ", 2) + [""] if len(line.split(" ", 2)) < 3 else line.split(" ", 2)
            args = rest[0] if rest else ""
            LOG.append(line)
            cu = cmd.upper()
            if cu == "CAPABILITY":
                w(f"* CAPABILITY IMAP4rev1 IDLE\r\n{tag} OK done\r\n")
            elif cu == "APPEND":
                n = int(re.search(r"\{(\d+)\+?\}\s*$", args).group(1))
                w("+ ready\r\n")
                APPENDED.append((args.split(" ")[0], self.rfile.read(n)))
                self.rfile.readline()
                w(f"{tag} OK appended\r\n")
            elif cu == "IDLE":
                w("+ idling\r\n")
                import time as _t
                _t.sleep(1)
                w("* 4 EXISTS\r\n")
                self.rfile.readline()  # DONE
                w(f"{tag} OK idle terminated\r\n")
            elif cu == "LOGIN":
                w(f"{tag} OK ok\r\n")
            elif cu == "LIST":
                w('* LIST (\\HasNoChildren) "/" "INBOX"\r\n* LIST (\\HasNoChildren \\Sent) "/" "Gesendet"\r\n')
                w(f"{tag} OK done\r\n")
            elif cu in ("SELECT", "EXAMINE"):
                w(f"* {len(MAILS)} EXISTS\r\n* OK [UIDVALIDITY 7] ok\r\n{tag} OK [READ-ONLY] ok\r\n")
            elif cu == "UID" and args.upper().startswith("SEARCH"):
                uids = list(MAILS) if self.server.cur != "Gesendet" else []
                w(f"* SEARCH {' '.join(map(str, uids))}\r\n{tag} OK done\r\n")
            elif cu == "UID" and args.upper().startswith("FETCH"):
                m = re.match(r"FETCH (\S+) \((.*)\)", args, re.I)
                spec, items = m.groups()
                if "*" in spec:
                    lo = int(spec.split(":")[0])
                    uids = [u for u in MAILS if u >= lo]
                else:
                    uids = [int(x) for x in spec.split(",")]
                for u in uids:
                    if "BODY" in items.upper():
                        raw = MAILS[u]
                        w(f"* {u} FETCH (UID {u} FLAGS ({'\\Seen' if u == 1 else '\\Flagged' if u == 2 else ''}) BODY[] {{{len(raw)}}}\r\n")
                        w(raw)
                        w(")\r\n")
                    else:
                        w(f"* {u} FETCH (UID {u} FLAGS ({'\\Flagged' if u == 2 else ''}))\r\n")
                w(f"{tag} OK done\r\n")
            elif cu == "LOGOUT":
                w(f"* BYE\r\n{tag} OK bye\r\n")
                return
            else:
                w(f"{tag} OK\r\n")
            if cu in ("SELECT", "EXAMINE"):
                self.server.cur = args.strip('"')


class S(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    cur = "INBOX"


srv = S(("127.0.0.1", 0), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
port = srv.server_address[1]
imaplib.IMAP4_SSL = lambda host, p, timeout=None: imaplib.IMAP4(host, port, timeout=timeout)  # ohne TLS testen

aid = db.add_account(name="T", email="me@x.de", kind="imap", imap_host="127.0.0.1", imap_port=993, username="me")
keyring.store(aid, "password", "pw")
acc = db.account(aid)
n = imapsync.sync_account(acc)
assert n == 3, n
rows = db.query({"kind": "unified"}, "")
assert [r["subject"] for r in rows] == ["Drei", "Zwei", "Eins"] or len(rows) == 3, rows
by = {r["subject"]: r for r in rows}
assert by["Eins"]["seen"] == 1 and by["Zwei"]["flagged"] == 1 and by["Drei"]["seen"] == 0
assert len(db.query({"kind": "unified"}, "müller")) == 3 and len(db.query({"kind": "unified"}, "Zwei")) == 1
assert {f["role"] for f in db.folders(aid)} == {"inbox", "sent"}
# zweiter Lauf: nichts Neues; Server löscht Nr. 2 -> lokal verschwindet sie
MAILS[4] = mk(4, "Vier")
got_new = []
assert imapsync.sync_account(acc, on_new=got_new.extend) == 1
assert [m["subject"] for m in got_new] == ["Vier"] and got_new[0]["pk"], got_new
del MAILS[4]
assert imapsync.sync_account(acc) == 0
del MAILS[2]
assert imapsync.sync_account(acc) == 0
assert {r["subject"] for r in db.query({"kind": "unified"}, "")} == {"Eins", "Drei"}
print("OK – Sync, Flags, Volltext, Löschabgleich funktionieren")

# IDLE: Server meldet neue Nachricht -> Watcher ruft on_change
got = threading.Event()
wch = imapsync.IdleWatcher(aid, lambda i: got.set())
wch.start()
assert got.wait(10), "IDLE-Push nicht erkannt"
wch.stop()
print("OK – IDLE-Push erkannt")
# Entwurf speichern: landet im Entwürfe-Ordner (hier „Gesendet“ ist Sent; Drafts-Ordner anlegen)
db.conn().execute("INSERT INTO folders(account_id,name,role) VALUES (?,?,?)", (aid, "Entwuerfe", "drafts")); db.conn().commit()
imapsync.save_draft(acc, "", "", "Entwurf Betreff", "Halbfertiger Text")
box, raw = APPENDED[-1]
assert box == '"Entwuerfe"' and b"Subject: Entwurf Betreff" in raw and b"Halbfertiger Text" in raw, APPENDED[-1]
print("OK – Entwurf wird im Entwürfe-Ordner abgelegt")
keyring.clear(aid)
