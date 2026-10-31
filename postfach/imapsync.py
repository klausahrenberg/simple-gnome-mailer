"""IMAP-Synchronisation, Flag-Aktionen und SMTP-Versand (laufen in Worker-Threads)."""
import base64
import email.message
import email.utils
import imaplib
import re
import select
import smtplib
import socket
import threading
import time

from . import config, db, goa, graph, mailparse
from .i18n import _
from . import secrets as keyring

SKIP_FLAGS = {"\\all", "\\flagged", "\\important", "\\noselect", "\\nonexistent"}
ROLE_FLAGS = {"\\sent": "sent", "\\drafts": "drafts", "\\trash": "trash", "\\junk": "junk", "\\archive": "archive"}
ROLE_NAMES = {
    "sent": ("sent", "gesendet", "gesendete elemente", "gesendete objekte", "sent items"),
    "drafts": ("drafts", "entwürfe", "entwurf", "entwuerfe"),
    "trash": ("trash", "papierkorb", "gelöschte elemente", "deleted items"),
    "junk": ("junk", "spam", "junk-e-mail", "junk email"),
    "archive": ("archive", "archiv"),
}


def xoauth2(user, token):
    """SASL-XOAUTH2-Antwort (Token stammt aus den GNOME-Online-Konten)."""
    return f"user={user}\x01auth=Bearer {token}\x01\x01".encode()


# ---- Modified UTF-7 (RFC 3501) ---------------------------------------

def utf7_decode(s):
    def dec(m):
        if m.group(1) == "":
            return "&"
        b = m.group(1).replace(",", "/")
        b += "=" * (-len(b) % 4)
        return base64.b64decode(b).decode("utf-16-be")
    return re.sub(r"&([^-]*)-", dec, s)


def utf7_encode(s):
    out, buf = [], ""

    def flush():
        nonlocal buf
        if buf:
            out.append("&" + base64.b64encode(buf.encode("utf-16-be")).decode().rstrip("=").replace("/", ",") + "-")
            buf = ""

    for ch in s:
        if 0x20 <= ord(ch) <= 0x7E:
            flush()
            out.append("&-" if ch == "&" else ch)
        else:
            buf += ch
    flush()
    return "".join(out)


def folder_title(role, name, delim, title=None):
    titles = {"inbox": _("Inbox"), "sent": _("Sent"), "drafts": _("Drafts"), "trash": _("Trash"),
              "junk": _("Spam"), "archive": _("Archive")}
    if role in titles:
        return titles[role]
    if title:
        return title
    n = utf7_decode(name)
    return n.split(delim)[-1] if delim else n


def _q(name):
    return '"' + name.replace("\\", "\\\\").replace('"', '\\"') + '"'


# ---- Verbindung -------------------------------------------------------

def connect(acc, password=None):
    host, port = acc["imap_host"], acc["imap_port"] or 993
    im = imaplib.IMAP4_SSL(host, port, timeout=60) if port == 993 else imaplib.IMAP4(host, port, timeout=60)
    if port != 993:
        im.starttls()
    if acc["kind"] == "goa":
        how, secret = goa.credentials(acc["goa_id"])
        user = acc["username"] or acc["email"]
        if how == "oauth":
            im.authenticate("XOAUTH2", lambda _: xoauth2(user, secret))
        else:
            im.login(user, secret)
    elif acc["kind"] in ("google", "microsoft"):
        raise imaplib.IMAP4.error(_("Direct Google/Microsoft sign-in is no longer supported – "
                                    "please add the account via GNOME Online Accounts"))
    else:
        pw = password if password is not None else keyring.lookup(acc["id"], "password")
        if pw is None:
            raise imaplib.IMAP4.error(_("No password saved – please enter it again under Settings → Accounts"))
        im.login(acc["username"] or acc["email"], pw)
    return im


# ---- Ordner -----------------------------------------------------------

_LIST = re.compile(rb'\((?P<flags>[^)]*)\) (?P<delim>"(?:[^"\\]|\\.)*"|NIL) (?P<name>.+)$')


def refresh_folders(im, acc):
    typ, data = im.list()
    c = db.conn()
    found = []
    for line in data or []:
        if not isinstance(line, bytes):
            continue
        m = _LIST.match(line)
        if not m:
            continue
        flags = {f.lower() for f in m.group("flags").decode().split()}
        if flags & SKIP_FLAGS:
            continue
        delim = m.group("delim").decode().strip('"')
        delim = None if delim == "NIL" else delim
        name = m.group("name").decode()
        if name.startswith('"'):
            name = re.sub(r"\\(.)", r"\1", name[1:-1])
        low = utf7_decode(name).lower()
        leaf = low.split(delim)[-1] if delim else low
        if leaf in ("all mail", "alle nachrichten", "important", "wichtig", "starred", "markiert"):
            continue
        role = "inbox" if low == "inbox" else next((r for f, r in ROLE_FLAGS.items() if f in flags), "other")
        found.append([name, role, delim, leaf])
    taken = {r for _, r, _, _ in found if r != "other"}
    out = []
    for name, role, delim, leaf in found:   # Namensheuristik nur für noch freie Rollen
        if role == "other":
            for r, names in ROLE_NAMES.items():
                if leaf in names and r not in taken:
                    role = r
                    taken.add(r)
        out.append((name, role, delim))
    found = out
    for name, role, delim in found:
        c.execute("INSERT INTO folders(account_id,name,role,delim) VALUES (?,?,?,?) "
                  "ON CONFLICT(account_id,name) DO UPDATE SET role=excluded.role, delim=excluded.delim",
                  (acc["id"], name, role, delim))
    names = [f[0] for f in found]
    if names:
        c.execute(f"DELETE FROM folders WHERE account_id=? AND name NOT IN ({','.join('?' * len(names))})",
                  (acc["id"], *names))
    c.commit()


# ---- Nachrichten ------------------------------------------------------

def _fetch_items(data):
    """imaplib-FETCH-Antwort -> [(meta_bytes, literal_bytes_or_None)]"""
    items = []
    for part in data:
        if isinstance(part, tuple):
            items.append([part[0], part[1]])
        elif isinstance(part, bytes) and items:
            items[-1][0] += b" " + part
        elif isinstance(part, bytes):
            items.append([part, None])
    return items


def _parse_meta(meta):
    uid = re.search(rb"UID (\d+)", meta)
    flags = re.search(rb"FLAGS \(([^)]*)\)", meta)
    fl = flags.group(1).lower().split() if flags else []
    return (int(uid.group(1)) if uid else None, b"\\seen" in fl, b"\\flagged" in fl)


def sync_folder(im, acc, folder, depth, on_batch=None, on_new=None):
    c = db.conn()
    typ, _ = im.select(_q(folder), readonly=True)
    if typ != "OK":
        return 0
    uv = im.response("UIDVALIDITY")[1]
    uidvalidity = int(uv[0]) if uv and uv[0] else None
    row = c.execute("SELECT uidvalidity FROM folders WHERE account_id=? AND name=?", (acc["id"], folder)).fetchone()
    if row and row["uidvalidity"] and uidvalidity and row["uidvalidity"] != uidvalidity:
        c.execute("DELETE FROM messages WHERE account_id=? AND folder=? AND archived=0", (acc["id"], folder))
    c.execute("UPDATE folders SET uidvalidity=? WHERE account_id=? AND name=?", (uidvalidity, acc["id"], folder))
    typ, data = im.uid("SEARCH", None, "ALL")
    remote = {int(x) for x in (data[0] or b"").split()}
    local = {r["uid"]: r["id"] for r in
             c.execute("SELECT uid, id FROM messages WHERE account_id=? AND folder=? AND archived=0", (acc["id"], folder))}
    for uid in set(local) - remote:
        c.execute("DELETE FROM messages WHERE id=?", (local[uid],))
    new = sorted(remote - set(local), reverse=True)
    if not local:
        new = new[:depth]
    else:  # nur Nachrichten neuer als die lokal bekannten + Lücken im Fenster
        floor = min(local) if local else 0
        new = [u for u in new if u >= floor or len(local) < depth][:depth]
    added = 0
    fresh = []   # neu eingetroffene, ungelesene Nachrichten (nicht beim allerersten Abruf)
    BIG = 150_000   # größere Mails nur als Kopf + Textanfang laden (Anhänge machen den Sync sonst quälend langsam)
    for i in range(0, len(new), 30):
        batch = new[i:i + 30]
        typ, data = im.uid("FETCH", ",".join(map(str, batch)), "(RFC822.SIZE)")
        sizes = {}
        for line in data or []:
            line = line if isinstance(line, bytes) else line[0]
            u, sz = re.search(rb"UID (\d+)", line), re.search(rb"RFC822\.SIZE (\d+)", line)
            if u and sz:
                sizes[int(u.group(1))] = int(sz.group(1))
        small = [u for u in batch if sizes.get(u, 0) <= BIG]
        big = [u for u in batch if sizes.get(u, 0) > BIG]
        got = []   # (uid, seen, flagged, raw, truncated)
        for j in range(0, len(small), 15):
            part = small[j:j + 15]
            typ, data = im.uid("FETCH", ",".join(map(str, part)), "(FLAGS BODY.PEEK[])")
            if typ == "OK":
                for meta, raw in _fetch_items(data):
                    uid, seen, flagged = _parse_meta(meta)
                    if raw is not None and uid is not None:
                        got.append((uid, seen, flagged, raw, False))
        for u in big:
            typ, data = im.uid("FETCH", str(u), "(FLAGS BODY.PEEK[HEADER] BODY.PEEK[TEXT]<0.60000>)")
            if typ != "OK":
                continue
            items = _fetch_items(data)
            head = next((raw for m_, raw in items if raw is not None and b"HEADER" in m_.upper()), None)
            text = next((raw for m_, raw in items if raw is not None and b"TEXT" in m_.upper()), b"")
            if head is not None:
                meta = b" ".join(m_ for m_, _ in items)
                uid, seen, flagged = _parse_meta(meta)
                got.append((u, seen, flagged, head + text, True))
        for uid, seen, flagged, raw, trunc in got:
            try:
                m = mailparse.parse(raw)
            except Exception:
                continue
            if trunc:
                m["has_att"] = 1
                m["partial"] = 1
            m.update(account_id=acc["id"], folder=folder, uid=uid, seen=int(seen), flagged=int(flagged))
            if db.insert_message(m, raw):
                added += 1
                if on_new and local and not seen:
                    fresh.append({"account_id": acc["id"], "folder": folder, "uid": uid, "from_name": m["from_name"],
                                  "from_addr": m["from_addr"], "subject": m["subject"]})
        c.commit()
        if on_batch and got:
            on_batch(len(got))
    # Flags bestehender Nachrichten abgleichen
    if local:
        typ, data = im.uid("FETCH", f"{min(local)}:*", "(FLAGS)")
        if typ == "OK":
            for line in data:
                if not line:
                    continue
                line = line if isinstance(line, bytes) else line[0]
                uid, seen, flagged = _parse_meta(line)
                if uid in local and not db.is_recent(local[uid]):
                    c.execute("UPDATE messages SET seen=?, flagged=? WHERE id=?", (int(seen), int(flagged), local[uid]))
    c.commit()
    if fresh and on_new:
        for f in fresh:
            f["pk"] = c.execute("SELECT id FROM messages WHERE account_id=? AND folder=? AND uid=?",
                               (acc["id"], folder, f["uid"])).fetchone()[0]
        on_new(fresh)
    return added


def sync_account(acc, progress=None, on_batch=None, only_folder=None, on_new=None):
    """Synchronisiert die Rollen-Ordner (INBOX, Gesendet, …) eines Kontos – oder genau einen Ordner.
    Übrige Ordner werden erst beim Öffnen geladen. Gibt die Zahl neuer Nachrichten zurück."""
    if graph.is_graph(acc):
        return graph.sync_account(acc, progress, on_batch, only_folder, on_new)
    im = connect(acc)
    total = 0
    try:
        refresh_folders(im, acc)
        depth = int(config.get("sync_depth"))
        order = {"inbox": 0, "sent": 1, "drafts": 2, "archive": 3, "trash": 4, "junk": 5, "other": 6}
        for f in sorted(db.folders(acc["id"]), key=lambda f: order.get(f["role"], 9)):
            if only_folder is not None:
                if f["name"] != only_folder:
                    continue
            elif f["role"] == "other":
                continue
            if progress:
                progress(f"{acc['email']}: {folder_title(f['role'], f['name'], f['delim'])}")
            d = depth if f["role"] in ("inbox", "sent", "archive") else min(depth, 100)
            total += sync_folder(im, acc, f["name"], d, on_batch, on_new if f["role"] == "inbox" else None)
    finally:
        try:
            im.logout()
        except Exception:
            pass
    return total


# ---- Aktionen ---------------------------------------------------------

def _with_msg(pk, fn):
    m = db.message(pk)
    if not m or m["archived"]:
        return
    acc = db.account(m["account_id"])
    im = connect(acc)
    try:
        im.select(_q(m["folder"]))
        fn(im, acc, m)
    finally:
        try:
            im.logout()
        except Exception:
            pass


def fetch_full(pk):
    """Lädt eine nur teilweise synchronisierte (große) Nachricht vollständig nach."""
    if graph.is_graph(db.account(db.message(pk)["account_id"])):
        return

    def go(im, acc, m):
        typ, data = im.uid("FETCH", str(m["uid"]), "(BODY.PEEK[])")
        for meta, raw in _fetch_items(data):
            if raw:
                db.replace_raw(pk, raw, mailparse.parse(raw))
                return
    _with_msg(pk, go)


def _with_msgs(pks, fn):
    """Führt fn(im, acc, folder, uids) je (Konto, Ordner) mit einer einzigen Verbindung pro Konto aus."""
    groups = {}
    for pk in pks:
        m = db.message(pk)
        if m and not m["archived"]:
            groups.setdefault(m["account_id"], {}).setdefault(m["folder"], []).append(m["uid"])
    for acc_id, folders in groups.items():
        acc = db.account(acc_id)
        im = connect(acc)
        try:
            for folder, uids in folders.items():
                im.select(_q(folder))
                fn(im, acc, folder, ",".join(map(str, sorted(uids))))
        finally:
            try:
                im.logout()
            except Exception:
                pass


def _split(pks):
    """-> (Graph-Nachrichten, IMAP-Nachrichten)"""
    pks = [pks] if isinstance(pks, int) else list(pks)
    pks = [pk for pk in pks if (m := db.message(pk)) and not m["archived"]]   # lokal archivierte: nur lokal
    g = [pk for pk in pks if graph.is_graph(db.account(db.message(pk)["account_id"]))]
    return g, [pk for pk in pks if pk not in g]


def remote_set_flag(pks, flag, on):
    g, i = _split(pks)
    if g:
        graph.set_flag(g, flag, on)
    if i:
        _with_msgs(i, lambda im, acc, folder, uids: im.uid("STORE", uids, "+FLAGS" if on else "-FLAGS", f"({flag})"))


def _move_or_delete(role):
    def go(im, acc, folder, uids):
        target = db.folder_by_role(acc["id"], role) if role else None
        if role and (not target or target == folder):
            if role in ("archive", "junk", "inbox"):
                return                   # kein Zielordner / schon dort
        elif target:
            im.uid("COPY", uids, _q(target))
        im.uid("STORE", uids, "+FLAGS", "(\\Deleted)")
        im.expunge()
    return go


def remote_delete(pks):
    """Verschiebt in den Papierkorb bzw. löscht endgültig, wenn schon dort (oder kein Papierkorb existiert)."""
    g, i = _split(pks)
    if g:
        graph.delete(g)
    if i:
        _with_msgs(i, _move_or_delete("trash"))


def remote_mark_spam(pks):
    """Verschiebt in den Spam-Ordner des jeweiligen Kontos."""
    g, i = _split(pks)
    if g:
        graph.spam(g)
    if i:
        _with_msgs(i, _move_or_delete("junk"))


def remote_not_spam(pks):
    """„Kein Spam“: zurück in den Posteingang des jeweiligen Kontos."""
    g, i = _split(pks)
    if g:
        graph.not_spam(g)
    if i:
        _with_msgs(i, _move_or_delete("inbox"))


def remote_move_archive(pks):
    g, i = _split(pks)
    if g:
        graph.archive(g)
    if i:
        _with_msgs(i, _move_or_delete("archive"))


def build_message(acc, to, cc, subject, body, in_reply_to=None, references=None, html=None, attachments=None, bcc=None, ics=None):
    """Fertige MIME-Nachricht (Text, optional HTML mit eingebetteten Bildern, Anhänge)."""
    msg = email.message.EmailMessage()
    msg["From"] = email.utils.formataddr((acc["name"] or "", acc["email"]))
    if to:
        msg["To"] = to
    if cc:
        msg["Cc"] = cc
    if bcc:
        msg["Bcc"] = bcc          # smtplib sendet an Bcc-Empfänger und entfernt die Zeile aus der übertragenen Mail
    msg["Subject"] = subject
    msg["Date"] = email.utils.formatdate(localtime=True)
    msg["Message-ID"] = email.utils.make_msgid(domain=acc["email"].split("@")[-1])
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
        msg["References"] = ((references or "") + " " + in_reply_to).strip()
    msg.set_content(body)
    if html:                                    # multipart/alternative: Text + HTML (Originalformat HTML)
        import base64 as _b64
        import re as _re
        images = []

        def cid_for(m):
            cid = email.utils.make_msgid(domain="postfach.local")
            images.append((cid, m.group(1), _b64.b64decode(m.group(2))))
            return f'src="cid:{cid[1:-1]}"'

        html = _re.sub(r'src=["\']data:(image/[\w.+-]+);base64,([A-Za-z0-9+/=\s]+)["\']', cid_for, html)
        msg.add_alternative("<!doctype html><html><body style=\"font-family:sans-serif;font-size:14px\">"
                            + html + "</body></html>", subtype="html")
        for cid, ctype, data in images:
            msg.get_body(("html",)).add_related(data, *ctype.split("/", 1), cid=cid)
    if ics:                                     # Terminantwort: text/calendar; method=REPLY
        msg.add_alternative(ics[1], subtype="calendar", params={"method": ics[0]})
    for fname, ctype, data in attachments or []:
        maintype, _, subtype = (ctype or "application/octet-stream").partition("/")
        msg.add_attachment(data, maintype=maintype, subtype=subtype or "octet-stream", filename=fname)
    return msg


def save_draft(acc, *args, **kw):
    """Legt die Nachricht im Entwürfe-Ordner des Kontos ab."""
    msg = build_message(acc, *args, **kw)
    if graph.is_graph(acc):
        graph.save_draft_mime(acc, msg.as_bytes())
        return
    drafts = db.folder_by_role(acc["id"], "drafts")
    if not drafts:
        raise RuntimeError(_("The account has no drafts folder"))
    im = connect(acc)
    try:
        typ, _resp = im.append(_q(drafts), "\\Draft \\Seen", imaplib.Time2Internaldate(time.time()), msg.as_bytes())
        if typ != "OK":
            raise RuntimeError(_("The server did not accept the draft"))
    finally:
        try:
            im.logout()
        except Exception:
            pass


def send_mail(acc, to, cc, subject, body, in_reply_to=None, references=None, html=None, attachments=None, bcc=None, ics=None):
    msg = build_message(acc, to, cc, subject, body, in_reply_to, references, html, attachments, bcc, ics)
    if graph.is_graph(acc):
        graph.send_mime(acc, msg.as_bytes())
        return
    host, port = acc["smtp_host"], acc["smtp_port"] or 587
    s = smtplib.SMTP_SSL(host, port, timeout=60) if port == 465 else smtplib.SMTP(host, port, timeout=60)
    try:
        s.ehlo()
        if port != 465:
            s.starttls()
            s.ehlo()
        if acc["kind"] == "goa":
            user, how, secret = goa.smtp_credentials(acc["goa_id"])
            if how == "oauth":
                s.auth("XOAUTH2", lambda challenge=None: xoauth2(user, secret).decode(), initial_response_ok=True)
            else:
                s.login(user, secret or "")
        elif acc["kind"] in ("google", "microsoft"):
            raise RuntimeError(_("Direct Google/Microsoft sign-in is no longer supported – "
                                 "please add the account via GNOME Online Accounts"))
        else:
            s.login(acc["username"] or acc["email"], keyring.lookup(acc["id"], "password") or "")
        s.send_message(msg)
    finally:
        try:
            s.quit()
        except Exception:
            pass
    if acc["kind"] == "imap" or (acc["kind"] == "goa" and acc["provider"] not in ("google", "ms_graph", "windows_live", "exchange")):  # Google/Microsoft legen Gesendetes selbst ab
        sent = db.folder_by_role(acc["id"], "sent")
        if sent:
            im = connect(acc)
            try:
                im.append(_q(sent), "\\Seen", imaplib.Time2Internaldate(time.time()), msg.as_bytes())
            finally:
                im.logout()


# ---- IDLE (Push) -------------------------------------------------------

IDLE_RENEW = 25 * 60  # Server trennen untätige IDLE-Sitzungen nach ~30 min


class IdleWatcher(threading.Thread):
    """Hält INBOX eines Kontos per IMAP IDLE offen und meldet Änderungen."""

    def __init__(self, acc_id, on_change):
        super().__init__(daemon=True, name=f"idle-{acc_id}")
        self.acc_id, self.on_change = acc_id, on_change
        self.stop_flag = threading.Event()

    def stop(self):
        self.stop_flag.set()

    def run(self):
        delay = 15
        while not self.stop_flag.is_set():
            acc = db.account(self.acc_id)
            if not acc or acc["kind"] == "local":
                return
            if graph.is_graph(acc):
                return                      # Graph: kein IDLE, Abruf im Intervall
            try:
                if not self._idle_session(acc):
                    return  # Server ohne IDLE: Polling genügt
                delay = 15
            except Exception:
                self.stop_flag.wait(delay)
                delay = min(delay * 2, 600)

    def _idle_session(self, acc):
        im = connect(acc)
        try:
            if "IDLE" not in getattr(im, "capabilities", ()):
                return False
            im.select("INBOX", readonly=True)
            while not self.stop_flag.is_set():
                tag = im._new_tag()
                im.send(tag + b" IDLE\r\n")
                if not im.readline().startswith(b"+"):
                    raise imaplib.IMAP4.error("IDLE not accepted")
                end = time.time() + IDLE_RENEW
                changed = False
                while time.time() < end and not self.stop_flag.is_set() and not changed:
                    if select.select([im.sock], [], [], 5)[0]:
                        line = im.readline()
                        if not line:
                            raise imaplib.IMAP4.abort("connection closed")
                        changed = bool(re.search(rb"\* \d+ (EXISTS|EXPUNGE|FETCH)", line))
                im.send(b"DONE\r\n")
                while True:  # bis zur getaggten Antwort lesen
                    line = im.readline()
                    if not line:
                        raise imaplib.IMAP4.abort("connection closed")
                    if re.search(rb"\* \d+ (EXISTS|EXPUNGE|FETCH)", line):
                        changed = True
                    if line.startswith(tag):
                        break
                if changed:
                    self.on_change(acc["id"])
            return True
        finally:
            try:
                im.logout()
            except Exception:
                pass


# ---- Hintergrund-Manager ---------------------------------------------

class SyncManager:
    def __init__(self, on_done, on_status, on_new=None):
        self.on_done, self.on_status, self.on_new = on_done, on_status, on_new
        self._wake = threading.Event()
        self._stop = False
        self._only = set()
        self._all = False
        self._idle = {}
        self._folder_reqs = []
        self.online = True             # False: letzter Verbindungsversuch scheiterte (stiller Wiederholungstest)
        self._offline_tries = 0
        self._ops = False
        self._archive_due = 0          # Zeitpunkt des nächsten automatischen Archivlaufs
        self._archive_now = False
        self._lock = threading.Lock()
        self.thread = threading.Thread(target=self._run, daemon=True, name="sync")

    def start(self):
        self.thread.start()
        self.refresh_idle()

    def stop(self):
        self._stop = True
        for w in self._idle.values():
            w.stop()
        self._wake.set()

    def request(self, account_id=None):
        with self._lock:
            if account_id is None:
                self._all = True
            else:
                self._only.add(account_id)
        self._wake.set()

    def request_folder(self, account_id, folder):
        with self._lock:
            if (account_id, folder) not in self._folder_reqs:
                self._folder_reqs.append((account_id, folder))
        self._wake.set()

    def request_ops(self):
        """Neue lokale Aktionen (löschen, Spam, Flags …) zum Server übertragen – ohne vollen Abgleich."""
        self._ops = True
        self._wake.set()

    def _probe(self):
        """Ist irgendein Konto erreichbar? (nur TCP-Verbindung, kein Login)"""
        for acc in db.mail_accounts():
            host = acc["imap_host"] or "graph.microsoft.com"
            port = (acc["imap_port"] or 993) if acc["imap_host"] else 443
            try:
                socket.create_connection((host, port), timeout=5).close()
                return True
            except OSError:
                continue
        return not db.mail_accounts()

    def _go_offline(self):
        if self.online:
            self.online = False
            self.on_status("offline", "")
        self._offline_tries += 1

    def _go_online(self):
        self.online, self._offline_tries = True, 0
        self.on_status("online", "")

    def _process_ops(self):
        """-> True, wenn online weitergearbeitet werden kann."""
        from . import ops
        if not db.pending_count():
            return True
        offline, error = ops.process()
        if offline:
            self._go_offline()
            return False
        if error:
            self.on_done(None, 0, error)
        self.on_done(None, 1, None)                # Liste neu aufbauen
        return True

    def request_archive(self):
        """Archivlauf sofort anstoßen (Einstellungen: „Jetzt archivieren“)."""
        self._archive_now = True
        self._wake.set()

    def _maybe_archive(self):
        manual, self._archive_now = self._archive_now, False
        if not manual and (not config.get("archive_enabled") or time.time() < self._archive_due):
            return
        self._archive_due = time.time() + 6 * 3600
        from . import archive
        self.on_status("sync", _("Archiving old messages …"))
        n, errors = archive.run(lambda k: self.on_status("sync", _("Archiving old messages … {n}").format(n=k)))
        if n:
            self.on_done(None, n, None)
        for e in errors:
            self.on_done(None, 0, e)

    def refresh_idle(self):
        """Startet/stoppt IDLE-Watcher passend zu den vorhandenen Konten."""
        ids = {a["id"] for a in db.mail_accounts()}
        for i in list(self._idle):
            if i not in ids or not self._idle[i].is_alive():
                self._idle.pop(i).stop()
        for i in ids - set(self._idle):
            w = IdleWatcher(i, self.request)
            self._idle[i] = w
            w.start()

    def _run(self):
        first = True
        while not self._stop:
            if not first:
                wait = min(30 * 2 ** self._offline_tries, 300) if not self.online else max(1, int(config.get("sync_interval_min"))) * 60
                self._wake.wait(wait)
            first = False
            self._wake.clear()
            if self._stop:
                break
            with self._lock:
                only = set(self._only)
                ops_only = self._ops and not (self._all or only or self._folder_reqs)
                full = self._all or bool(only) or not self._folder_reqs
                reqs, self._folder_reqs = self._folder_reqs, []
                self._only, self._all, self._ops = set(), False, False
            if not self.online and not self._probe():      # weiterhin offline: still erneut versuchen
                self._go_offline()
                continue
            try:
                if not self._process_ops():
                    continue
            except Exception as e:
                self.on_done(None, 0, str(e))
            if not self.online:
                self._go_online()
            if ops_only:
                self.on_status("idle", "")
                continue
            self.refresh_idle()
            jobs = [(db.account(i), f) for i, f in reqs]
            if full:
                jobs += [(a, None) for a in db.mail_accounts() if not only or a["id"] in only]
            from . import ops
            for acc, folder in jobs:
                if acc is None or acc["kind"] == "local":
                    continue
                try:
                    self.on_status("sync", _("Syncing {account} …").format(account=acc["email"]))
                    sync_account(acc, lambda t: t and self.on_status("sync", t),
                                 on_batch=lambda k, i=acc["id"]: self.on_done(i, k, None), only_folder=folder, on_new=self.on_new)
                    self.on_done(acc["id"], 0, None)
                except Exception as e:  # Netz/Anmeldung usw. dürfen die Schleife nicht beenden
                    if ops.is_network_error(e):         # offline: einmal melden, danach still prüfen
                        self._go_offline()
                        break
                    self.on_done(acc["id"], 0, f"{acc['email']}: {e}")
            if not self.online:
                continue
            try:
                self._maybe_archive()
            except Exception as e:
                if not ops.is_network_error(e):
                    self.on_done(None, 0, str(e))
            self.on_status("idle", "")
