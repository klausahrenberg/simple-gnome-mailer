"""SQLite-Cache mit FTS5-Volltextindex."""
import re
import sqlite3
import threading
import time
import zlib

from .paths import DB_PATH

SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS accounts(
  id INTEGER PRIMARY KEY,
  name TEXT, email TEXT NOT NULL,
  kind TEXT NOT NULL,                -- imap | google | microsoft
  imap_host TEXT, imap_port INTEGER DEFAULT 993,
  smtp_host TEXT, smtp_port INTEGER DEFAULT 587,
  username TEXT, color INTEGER DEFAULT 0,
  goa_id TEXT, provider TEXT
);
CREATE TABLE IF NOT EXISTS folders(
  account_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
  name TEXT NOT NULL, role TEXT NOT NULL DEFAULT 'other',
  delim TEXT, uidvalidity INTEGER, title TEXT,
  PRIMARY KEY(account_id, name)
);
CREATE TABLE IF NOT EXISTS messages(
  id INTEGER PRIMARY KEY,
  account_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
  folder TEXT NOT NULL, uid INTEGER NOT NULL,
  message_id TEXT, subject TEXT, from_name TEXT, from_addr TEXT, to_addrs TEXT,
  date INTEGER, snippet TEXT, body_text TEXT, body_html TEXT,
  seen INTEGER DEFAULT 0, flagged INTEGER DEFAULT 0, has_att INTEGER DEFAULT 0, partial INTEGER DEFAULT 0, att_names TEXT, remote_id TEXT,
  UNIQUE(account_id, folder, uid)
);
CREATE INDEX IF NOT EXISTS idx_msg_date ON messages(date DESC);
CREATE INDEX IF NOT EXISTS idx_msg_folder ON messages(account_id, folder, date DESC);
CREATE TABLE IF NOT EXISTS raw(
  message_pk INTEGER PRIMARY KEY REFERENCES messages(id) ON DELETE CASCADE,
  data BLOB
);
CREATE VIRTUAL TABLE IF NOT EXISTS msg_fts USING fts5(
  subject, sender, recipients, body,
  content='messages', content_rowid='id',
  tokenize='unicode61 remove_diacritics 2'
);
CREATE TRIGGER IF NOT EXISTS msg_ai AFTER INSERT ON messages BEGIN
  INSERT INTO msg_fts(rowid, subject, sender, recipients, body)
  VALUES (new.id, new.subject, new.from_name || ' ' || new.from_addr, new.to_addrs, new.body_text);
END;
CREATE TRIGGER IF NOT EXISTS msg_ad AFTER DELETE ON messages BEGIN
  INSERT INTO msg_fts(msg_fts, rowid, subject, sender, recipients, body)
  VALUES ('delete', old.id, old.subject, old.from_name || ' ' || old.from_addr, old.to_addrs, old.body_text);
END;
"""

_local = threading.local()


def _backfill_att_names(c):
    from . import mailparse
    for r in c.execute("SELECT m.id, r.data FROM messages m JOIN raw r ON r.message_pk=m.id WHERE m.has_att=1").fetchall():
        try:
            names = mailparse.att_names(zlib.decompress(r["data"]))
        except Exception:
            continue
        c.execute("UPDATE messages SET att_names=? WHERE id=?", (names, r["id"]))
    c.commit()


def conn():
    c = getattr(_local, "c", None)
    if c is None:
        c = sqlite3.connect(DB_PATH, timeout=30)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys=ON")
        c.executescript(SCHEMA)
        cols = {r[1] for r in c.execute("PRAGMA table_info(accounts)")}
        for col in ('goa_id', 'provider'):
            if col not in cols:
                c.execute(f'ALTER TABLE accounts ADD COLUMN {col} TEXT')
        mcols = {r[1] for r in c.execute('PRAGMA table_info(messages)')}
        if 'remote_id' not in mcols:
            c.execute('ALTER TABLE messages ADD COLUMN remote_id TEXT')
        if 'title' not in {r[1] for r in c.execute('PRAGMA table_info(folders)')}:
            c.execute('ALTER TABLE folders ADD COLUMN title TEXT')
        if 'partial' not in mcols:
            c.execute('ALTER TABLE messages ADD COLUMN partial INTEGER DEFAULT 0')
        if 'att_names' not in mcols:
            c.execute('ALTER TABLE messages ADD COLUMN att_names TEXT')
            _backfill_att_names(c)
        c.create_function('ulower', 1, lambda v: v.lower() if isinstance(v, str) else v, deterministic=True)
        _local.c = c
    return c


# ---- Konten -----------------------------------------------------------

def accounts():
    return conn().execute("SELECT * FROM accounts ORDER BY id").fetchall()


def account(acc_id):
    return conn().execute("SELECT * FROM accounts WHERE id=?", (acc_id,)).fetchone()


def add_account(**kw):
    c = conn()
    cols = ", ".join(kw)
    cur = c.execute(f"INSERT INTO accounts({cols}) VALUES ({', '.join('?' * len(kw))})", tuple(kw.values()))
    c.commit()
    return cur.lastrowid


def remove_account(acc_id):
    c = conn()
    c.execute("DELETE FROM messages WHERE account_id=?", (acc_id,))
    c.execute("DELETE FROM accounts WHERE id=?", (acc_id,))
    c.commit()


# ---- Ordner -----------------------------------------------------------

def folders(acc_id):
    return conn().execute("SELECT * FROM folders WHERE account_id=?", (acc_id,)).fetchall()


def folder_by_role(acc_id, role):
    r = conn().execute("SELECT name FROM folders WHERE account_id=? AND role=?", (acc_id, role)).fetchone()
    return r["name"] if r else None


# ---- Nachrichten ------------------------------------------------------

def insert_message(m, raw_bytes):
    c = conn()
    cols = ", ".join(m)
    cur = c.execute(
        f"INSERT OR IGNORE INTO messages({cols}) VALUES ({', '.join('?' * len(m))})", tuple(m.values())
    )
    if cur.rowcount:
        c.execute("INSERT INTO raw(message_pk, data) VALUES (?,?)", (cur.lastrowid, zlib.compress(raw_bytes, 6)))
        return cur.lastrowid
    return None


def raw_message(pk):
    r = conn().execute("SELECT data FROM raw WHERE message_pk=?", (pk,)).fetchone()
    return zlib.decompress(r["data"]) if r else None


def message(pk):
    return conn().execute("SELECT * FROM messages WHERE id=?", (pk,)).fetchone()


_recent = {}   # pk -> Zeitpunkt der letzten lokalen Flag-Änderung


def is_recent(pk, seconds=90):
    """True, wenn die Nachricht gerade lokal geändert wurde (Server hat die Änderung evtl. noch nicht)."""
    return time.time() - _recent.get(pk, 0) < seconds


def set_flags(pk, seen=None, flagged=None):
    _recent[pk] = time.time()
    c = conn()
    if seen is not None:
        c.execute("UPDATE messages SET seen=? WHERE id=?", (int(seen), pk))
    if flagged is not None:
        c.execute("UPDATE messages SET flagged=? WHERE id=?", (int(flagged), pk))
    c.commit()


def delete_message(pk):
    c = conn()
    c.execute("DELETE FROM messages WHERE id=?", (pk,))
    c.commit()


# ---- Abfragen ---------------------------------------------------------

_TOKEN = re.compile(r"[^\W_]+", re.UNICODE)


def fts_query(text):
    """Nutzereingabe -> sichere FTS5-Abfrage mit Präfixsuche (UND-verknüpft)."""
    toks = _TOKEN.findall(text)
    return " ".join(f'"{t}"*' for t in toks)


OPERATORS = {"von": "from", "from": "from", "an": "to", "to": "to", "betreff": "subject", "subject": "subject",
             "file": "file", "datei": "file", "anhang": "file", "attachment": "file", "ist": "is", "is": "is"}
_OP = re.compile(r'(?<!\S)(\w+):(?:"([^"]*)"|(\S+))', re.UNICODE)


def parse_search(text):
    """'von:"heike ahrenberg" file:pdf rechnung' -> ([('from','heike ahrenberg'),('file','pdf')], 'rechnung')"""
    ops = []

    def take(m):
        key = OPERATORS.get(m.group(1).lower())
        val = (m.group(2) if m.group(2) is not None else m.group(3)).strip()
        if not key:
            return m.group(0)
        if val:
            ops.append((key, val))
        return " "

    free = _OP.sub(take, text or "")
    return ops, free.strip()


def query(view, search="", limit=300, offset=0):
    """view: dict(kind=unified|folder|unread|flagged, account_id, folder)."""
    where, args = [], []
    kind = view.get("kind", "unified")
    if kind == "unified":
        # gemeinsamer Posteingang: INBOX-Rolle aller Konten
        where.append("(m.account_id, m.folder) IN (SELECT account_id, name FROM folders WHERE role='inbox')")
    elif kind == "folder":
        where.append("m.account_id=? AND m.folder=?")
        args += [view["account_id"], view["folder"]]
    elif kind == "sent":
        # gemeinsamer Gesendet-Ordner: Sent-Rolle aller Konten
        where.append("(m.account_id, m.folder) IN (SELECT account_id, name FROM folders WHERE role='sent')")
    elif kind == "unread":
        where.append("m.seen=0 AND (m.account_id, m.folder) IN "
                     "(SELECT account_id, name FROM folders WHERE role NOT IN ('trash','junk','sent','drafts'))")
    elif kind == "flagged":
        where.append("m.flagged=1")
    if kind in ("unified", "sent", "unread", "flagged") and view.get("account_id"):
        where.append("m.account_id=?")
        args.append(view["account_id"])
    join = ""
    ops, free = parse_search(search)
    for key, val in ops:
        words = val.lower().split()
        if key == "from":
            fld, args_ = "ulower(m.from_name || ' ' || m.from_addr)", words
        elif key == "to":
            fld, args_ = "ulower(m.to_addrs)", words
        elif key == "subject":
            fld, args_ = "ulower(m.subject)", words
        elif key == "file":
            fld, args_ = "m.att_names", [w.lstrip(".*") for w in words]
            where.append("m.has_att=1")
        elif key == "is":
            if val.lower() in ("ungelesen", "unread"):
                where.append("m.seen=0")
            elif val.lower() in ("gelesen", "read"):
                where.append("m.seen=1")
            elif val.lower() in ("markiert", "flagged", "starred"):
                where.append("m.flagged=1")
            continue
        else:
            continue
        for w in args_:
            where.append(f"{fld} LIKE ? ESCAPE '\\'")
            args.append("%" + w.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%")
    q = fts_query(free)
    if q:
        join = "JOIN msg_fts ON msg_fts.rowid = m.id"
        where.append("msg_fts MATCH ?")
        args.append(q)
    sql = (
        "SELECT m.id, m.account_id, m.folder, m.uid, m.subject, m.from_name, m.from_addr, m.date, "
        "m.snippet, m.seen, m.flagged, m.has_att, m.to_addrs FROM messages m "
        f"{join} WHERE {' AND '.join(where) or '1'} ORDER BY m.date DESC LIMIT ? OFFSET ?"
    )
    return conn().execute(sql, (*args, limit, offset)).fetchall()


def unread_counts():
    """{(account_id, folder): n}"""
    rows = conn().execute("SELECT account_id, folder, COUNT(*) n FROM messages WHERE seen=0 GROUP BY 1,2")
    return {(r["account_id"], r["folder"]): r["n"] for r in rows}


def replace_raw(pk, raw_bytes, fields):
    """Ersetzt eine nur teilweise geladene Nachricht durch die vollständige."""
    c = conn()
    c.execute("UPDATE raw SET data=? WHERE message_pk=?", (zlib.compress(raw_bytes, 6), pk))
    c.execute("UPDATE messages SET has_att=?, att_names=?, partial=0 WHERE id=?", (fields["has_att"], fields["att_names"], pk))
    c.commit()
