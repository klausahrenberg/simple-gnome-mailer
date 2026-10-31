"""Import von Nachrichten aus einem Thunderbird-Profil (mbox- und Maildir-Ordner).

Importierte Nachrichten werden – wie archivierte – nur lokal gespeichert (`archived=1`) und erscheinen im
gemeinsamen Posteingang bzw. Gesendet-Ordner sowie in der Suche. Bereits vorhandene Nachrichten
(gleiche Message-ID) werden übersprungen."""
import email
import email.policy
import os
import re

from . import db, mailparse

PROFILE_ROOTS = ("~/.thunderbird", "~/.var/app/org.mozilla.Thunderbird/.thunderbird",
                 "~/snap/thunderbird/common/.thunderbird", "~/.config/thunderbird")
INBOX_NAMES = {"inbox", "posteingang"}
SENT_NAMES = {"sent", "gesendet", "gesendete elemente", "gesendete objekte", "sent items", "sent mail", "sent messages",
              "gesendete nachrichten"}
# Ordner, die nie importiert werden (auf jeder Ebene): Entwürfe, Papierkorb, Spam, Vorlagen …
SKIP_NAMES = {"drafts", "entwürfe", "entwuerfe", "entwurf", "trash", "papierkorb", "junk", "spam", "junk-e-mail", "templates",
              "vorlagen", "outbox", "unsent messages", "bin", "deleted", "deleted items", "gelöschte elemente",
              "deleted messages", "spam-verdacht", "unwanted", "bulk mail", "bulk", "all mail", "alle nachrichten",
              "important", "wichtig", "starred", "markiert"}
GMAIL_PARENTS = {"[gmail]", "[google mail]"}
SEP = re.compile(rb"^From \S* *\w{3} \w{3} [ \d]\d \d\d:\d\d")
MOZ_STATUS = re.compile(rb"^X-Mozilla-Status:\s*([0-9a-fA-F]+)", re.M)


def find_profiles():
    """Thunderbird-Profilordner dieses Benutzers (über profiles.ini, sonst nach Inhalt)."""
    out = []
    for root in PROFILE_ROOTS:
        root = os.path.expanduser(root)
        ini = os.path.join(root, "profiles.ini")
        if os.path.exists(ini):
            cur, sections = None, []
            for line in open(ini, encoding="utf-8", errors="replace"):
                line = line.strip()
                if line.startswith("["):
                    cur = {}
                    sections.append(cur)
                elif "=" in line and cur is not None:
                    k, v = line.split("=", 1)
                    cur[k] = v
            for s in sections:
                if "Path" in s:
                    p = os.path.join(root, s["Path"]) if s.get("IsRelative", "1") == "1" else s["Path"]
                    if os.path.isdir(p) and p not in out:
                        out.append(p)
        if os.path.isdir(root):
            for d in sorted(os.listdir(root)):
                p = os.path.join(root, d)
                if os.path.isdir(p) and (os.path.isdir(os.path.join(p, "ImapMail")) or os.path.isdir(os.path.join(p, "Mail"))) \
                        and p not in out:
                    out.append(p)
    return out


def _servers(profile):
    """{Ordnername unter ImapMail/Mail: (Benutzername, Host)} aus prefs.js."""
    prefs = {}
    try:
        with open(os.path.join(profile, "prefs.js"), encoding="utf-8", errors="replace") as f:
            for m in re.finditer(r'user_pref\("mail\.server\.(server\d+)\.([\w-]+)",\s*"([^"]*)"\)', f.read()):
                prefs.setdefault(m.group(1), {})[m.group(2)] = m.group(3)
    except OSError:
        return {}
    out = {}
    for p in prefs.values():
        d = p.get("directory-rel") or p.get("directory") or ""
        if d:
            out[os.path.basename(d.rstrip("/"))] = (p.get("userName", ""), p.get("hostname", ""))
    return out


def _is_mbox(path):
    try:
        if os.path.getsize(path) == 0:
            return False
        with open(path, "rb") as f:
            return f.read(5) == b"From "
    except OSError:
        return False


def _norm(name):
    """Ordnername ohne Thunderbird-Zähler („Sent-1“) und Sync-Konflikt-Zusatz („Sent.sync-conflict-…“)."""
    return re.sub(r"-\d+$", "", name.lower().split(".sync-conflict")[0])


def _role(parts):
    leaf = _norm(parts[-1])
    if leaf in SKIP_NAMES:
        return None
    top = len(parts) == 1 or parts[0].lower() in GMAIL_PARENTS or (len(parts) == 2 and parts[0].lower() == "inbox")
    if top:
        if leaf in INBOX_NAMES:
            return "inbox"
        if leaf in SENT_NAMES:
            return "sent"
    return "other"


def is_junk(raw):
    """Spam und Kaputtes, das nicht importiert wird: vom Server/Thunderbird als Spam markiert
    oder ohne Datumszeile (typisch für verstümmelte Spam-Nachrichten)."""
    msg = email.message_from_bytes(raw[:65536], policy=email.policy.default)
    if str(msg.get("X-Spam-Flag") or "").strip().lower() == "yes" or \
            str(msg.get("X-Spam-Status") or "").strip().lower().startswith("yes"):
        return True
    keys = str(msg.get("X-Mozilla-Keys") or "").lower().replace("nonjunk", "")
    if "junk" in keys:
        return True
    return msg.get("Date") is None


def _size(path, kind):
    if kind == "mbox":
        return os.path.getsize(path)
    return sum(e.stat().st_size for sub in ("cur", "new") if os.path.isdir(os.path.join(path, sub))
               for e in os.scandir(os.path.join(path, sub)) if e.is_file())


def scan(profile):
    """Ordner des Profils -> [dict(path, kind, parts, label, role, size, server)]; ausgelassen werden
    Entwürfe, Papierkorb, Spam, Vorlagen u. Ä. Erst Posteingang/Gesendet, dann die übrigen Ordner."""
    found = []

    def walk(d, parts, server):
        try:
            entries = sorted(os.scandir(d), key=lambda e: e.name.lower())
        except OSError:
            return
        for e in entries:
            name = e.name
            if name.endswith(".sbd") and e.is_dir():
                continue
            kind = None
            if e.is_file() and not name.endswith((".msf", ".dat", ".json", ".html", ".tmp")) and _is_mbox(e.path):
                kind = "mbox"
            elif e.is_dir() and os.path.isdir(os.path.join(e.path, "cur")):
                kind = "maildir"
            if kind:
                p = parts + [name]
                role = _role(p)
                if role:
                    found.append({"path": e.path, "kind": kind, "parts": p, "label": " / ".join(p), "role": role,
                                  "size": _size(e.path, kind), "server": server})
            sub = os.path.join(d, name + ".sbd")
            if os.path.isdir(sub):
                walk(sub, parts + [name], server)

    for top in ("ImapMail", "Mail"):
        base = os.path.join(profile, top)
        if os.path.isdir(base):
            for srv in sorted(os.scandir(base), key=lambda e: e.name.lower()):
                if srv.is_dir():
                    walk(srv.path, [], srv.name)
    srv_info = _servers(profile)
    for f in found:
        f["user"], f["host"] = srv_info.get(f["server"], ("", f["server"]))
    for f in found:
        f["mode"] = f["role"] if f["role"] in ("inbox", "sent") else "auto"
    order = {"inbox": 0, "sent": 1, "other": 2}
    found.sort(key=lambda f: (order[f["role"]], f["server"].lower(), f["label"].lower()))
    return found


# ---- Lesen ------------------------------------------------------------

def iter_mbox(path):
    """Liefert (rohe Nachricht, X-Mozilla-Status) je Nachricht einer mbox-Datei."""
    buf = []

    def done():
        raw = b"".join(buf)
        while raw.endswith(b"\n\n") or raw.endswith(b"\r\n\r\n"):
            raw = raw[:-1] if raw.endswith(b"\n\n") else raw[:-2]
        return raw

    with open(path, "rb") as f:
        prev_blank = True
        for line in f:
            if prev_blank and line.startswith(b"From ") and SEP.match(line):
                if buf:
                    yield done()
                buf = []
            else:
                buf.append(line[1:] if line.startswith(b">From ") else line)
            prev_blank = line in (b"\n", b"\r\n")
        if buf:
            yield done()


def iter_maildir(path):
    for sub in ("cur", "new"):
        d = os.path.join(path, sub)
        if not os.path.isdir(d):
            continue
        for e in sorted(os.scandir(d), key=lambda e: e.name):
            if e.is_file():
                try:
                    yield open(e.path, "rb").read()
                except OSError:
                    continue


def _flags(raw):
    """(gelesen, markiert, gelöscht) aus X-Mozilla-Status; ohne Angabe gilt die Nachricht als gelesen."""
    m = MOZ_STATUS.search(raw[:8192])
    if not m:
        return True, False, False
    v = int(m.group(1), 16)
    return bool(v & 1), bool(v & 4), bool(v & 8)


def identities(profile):
    """E-Mail-Adressen der Thunderbird-Identitäten (prefs.js) – für die Erkennung eigener Absender."""
    out = set()
    try:
        with open(os.path.join(profile, "prefs.js"), encoding="utf-8", errors="replace") as f:
            for m in re.finditer(r'user_pref\("mail\.identity\.id\d+\.useremail",\s*"([^"]+)"\)', f.read()):
                out.add(m.group(1).lower())
    except OSError:
        pass
    return out


def _find_account(accounts, addr):
    addr = (addr or "").lower()
    return next((a for a in accounts if addr and addr in ((a["email"] or "").lower(), (a["username"] or "").lower())), None)


def _find_by_server(accounts, user, host):
    """Konto zum Thunderbird-Server: Benutzername = Adresse/Anmeldename, oder Host + Benutzername ohne Domain."""
    user, host = (user or "").lower(), (host or "").lower()
    acc = _find_account(accounts, user)
    if acc or not user:
        return acc
    return next((a for a in accounts if host and host == (a["imap_host"] or "").lower()
                 and user in ((a["email"] or "").lower().split("@")[0], (a["username"] or "").lower().split("@")[0])), None)


def reassign_local():
    """Lokal archivierte Mails im „Lokalen Archiv“ den passenden Konten zuordnen (Empfänger- bzw. Absenderadresse).
    Gibt die Zahl der umgehängten Nachrichten zurück."""
    c = db.conn()
    loc = db.local_account()
    accounts = db.mail_accounts()
    moved = 0
    for role in ("inbox", "sent"):
        folder = db.folder_by_role(loc["id"], role)
        for r in c.execute("SELECT id, from_addr, to_addrs FROM messages WHERE account_id=? AND folder=? AND archived=1",
                           (loc["id"], folder)).fetchall():
            if role == "sent":
                acc = _find_account(accounts, r["from_addr"])
            else:
                acc = next((a for a in accounts if a["email"] and a["email"].lower() in (r["to_addrs"] or "").lower()), None)
            target = db.folder_by_role(acc["id"], role) if acc else None
            if target:
                c.execute("UPDATE messages SET account_id=?, folder=? WHERE id=?", (acc["id"], target, r["id"]))
                moved += 1
    c.commit()
    return moved


def _target(role, m, src, accounts, cache):
    """-> (account_id, Ordnername). Gesendetes: Konto des Absenders; Empfangenes: Konto des Thunderbird-Servers
    bzw. des Empfängers; sonst das lokale Archiv."""
    if role == "sent":
        acc = _find_account(accounts, m["from_addr"])
    else:
        acc = _find_by_server(accounts, src.get("user"), src.get("host")) or next(
            (a for a in accounts if a["email"] and a["email"].lower() in (m["to_addrs"] or "").lower()), None)
    key = (acc["id"] if acc else None, role)
    if key not in cache:
        if acc and db.folder_by_role(acc["id"], role):
            cache[key] = (acc["id"], db.folder_by_role(acc["id"], role))
        else:
            loc = db.local_account()
            cache[key] = (loc["id"], db.folder_by_role(loc["id"], role))
    return cache[key]


def run_import(sources, progress=None, cancel=None, own=()):
    """Importiert die gewählten Ordner. Jede Quelle hat `mode`: "inbox", "sent" oder "auto" (je Mail nach Absender:
    eigene Adresse -> Gesendet, sonst Posteingang). progress(bytes_done, bytes_total); cancel: threading.Event;
    own: weitere eigene Adressen (Thunderbird-Identitäten).
    -> dict(imported, duplicates, skipped)"""
    accounts = db.mail_accounts()
    mine = {x.lower() for x in own}
    for a in accounts:
        mine |= {(a["email"] or "").lower(), (a["username"] or "").lower()}
    mine.discard("")
    total = sum(s["size"] for s in sources) or 1
    stats = {"imported": 0, "duplicates": 0, "skipped": 0}
    done = n = 0
    c = db.conn()
    cache = {}
    for src in sources:
        it = iter_mbox(src["path"]) if src["kind"] == "mbox" else iter_maildir(src["path"])
        for raw in it:
            if cancel is not None and cancel.is_set():
                c.commit()
                return stats
            done += len(raw)
            n += 1
            seen, flagged, deleted = _flags(raw)
            if deleted or not raw.strip() or is_junk(raw):
                stats["skipped"] += 1
                continue
            try:
                m = mailparse.parse(raw)
                role = src.get("mode", src["role"])
                if role not in ("inbox", "sent"):
                    role = "sent" if (m["from_addr"] or "").lower() in mine else "inbox"
                if db.find_duplicate(role, m["message_id"], m["date"], m["subject"], m["from_addr"]):
                    stats["duplicates"] += 1
                    continue
                acc_id, folder = _target(role, m, src, accounts, cache)
                m.update(account_id=acc_id, folder=folder, uid=db.next_local_uid(), seen=int(seen),
                         flagged=int(flagged), archived=1)
                if db.insert_message(m, raw):
                    stats["imported"] += 1
            except Exception:
                stats["skipped"] += 1
                continue
            if n % 100 == 0:
                c.commit()
                if progress:
                    progress(done, total)
        c.commit()
        if progress:
            progress(done, total)
    reassign_local()
    return stats
