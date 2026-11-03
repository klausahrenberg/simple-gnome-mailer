"""Test: gespeicherter Entwurf -> Verfassen-Argumente (Empfänger, Bcc, HTML ohne Hülle, Anhänge) und endgültiges Löschen."""
import os
import sys
import tempfile

os.environ["POSTFACH_DATA_DIR"] = os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp()
sys.path.insert(0, __file__.rsplit("/tools/", 1)[0])
from postfach import composer, db, imapsync, mailparse, ops  # noqa: E402

acc_id = db.add_account(name="Ich", email="me@x.de", kind="imap", imap_host="imap.x.de", username="me@x.de")
c = db.conn()
c.execute("INSERT INTO folders(account_id,name,role) VALUES (?,?,?)", (acc_id, "Drafts", "drafts"))
c.commit()
acc = db.account(acc_id)
msg = imapsync.build_message(acc, 'A <a@x.de>, "Boschi, Robert" <r@x.de>', "c@x.de", "Entwurf Test", "Hallo Text",
                             html="<p>Hallo <b>HTML</b></p>", attachments=[("a.txt", "text/plain", b"anhang")], bcc="b@x.de",
                             in_reply_to="<orig@x>", references="<r0@x>")
raw = msg.as_bytes()
m = mailparse.parse(raw)
m.update(account_id=acc_id, folder="Drafts", uid=1, seen=1, flagged=0)
pk = db.insert_message(m, raw)
c.commit()
kw = composer.build_draft(db.message(pk), raw)
from email.utils import getaddresses  # noqa: E402
assert [a for _n, a in getaddresses([kw["to"]])] == ["a@x.de", "r@x.de"], kw["to"]
assert kw["cc"] == "c@x.de" and kw["bcc"] == "b@x.de" and kw["subject"] == "Entwurf Test"
assert "<p>Hallo <b>HTML</b></p>" in kw["body_html"] and "<html" not in kw["body_html"].lower() and "<body" not in kw["body_html"].lower(), kw["body_html"]
assert [a[0] for a in kw["attachments"]] == ["a.txt"] and kw["attachments"][0][2] == b"anhang"
assert kw["in_reply_to"] == "<orig@x>" and kw["references"] and kw["account_id"] == acc_id
# Nur-Text-Entwurf
raw2 = imapsync.build_message(acc, "z@x.de", "", "Text", "Nur Text\nZeile 2").as_bytes()
m2 = mailparse.parse(raw2)
m2.update(account_id=acc_id, folder="Drafts", uid=2, seen=1, flagged=0)
pk2 = db.insert_message(m2, raw2)
kw2 = composer.build_draft(db.message(pk2), raw2)
assert kw2["body_html"] is None and "Nur Text" in kw2["body_text"]
# Entwurf ersetzen: sofort ausgeblendet, endgültig (ohne Papierkorb) gelöscht
db.queue_op("purge", pk)
assert len(db.query({"kind": "drafts"})) == 1
CALLS = []
imapsync.remote_purge = lambda p: CALLS.append(list(p))
off, err = ops.process()
assert not off and CALLS == [[pk]] and db.message(pk) is None
print("OK – Entwürfe weiterbearbeiten")
