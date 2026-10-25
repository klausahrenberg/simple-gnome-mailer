"""Adressvorschläge: GNOME-Kontakte und Empfänger bisher gesendeter Nachrichten."""
import time
from collections import Counter
from email.utils import formataddr, getaddresses

from . import contacts, db

_sent = {"at": 0.0, "people": {}}      # addr -> (Name, Häufigkeit, letzter Zeitpunkt)


def _sent_recipients():
    if time.time() - _sent["at"] < 60:
        return _sent["people"]
    cnt, names, last = Counter(), {}, {}
    rows = db.conn().execute(
        "SELECT m.to_addrs, m.date FROM messages m JOIN folders f ON f.account_id=m.account_id AND f.name=m.folder "
        "WHERE f.role='sent' AND m.to_addrs != '' ORDER BY m.date DESC LIMIT 4000").fetchall()
    for r in rows:
        for name, addr in getaddresses([r["to_addrs"]]):
            a = addr.lower()
            if not a or "@" not in a:
                continue
            cnt[a] += 1
            last[a] = max(last.get(a, 0), r["date"] or 0)
            if name and a not in names:
                names[a] = name
    _sent["people"] = {a: (names.get(a, ""), cnt[a], last[a]) for a in cnt}
    _sent["at"] = time.time()
    return _sent["people"]


def suggest(token, limit=6):
    """[(Name, Adresse)] passend zur Eingabe: Wortanfang im Namen oder Teilstring der Adresse; häufig/zuletzt Genutzte zuerst."""
    q = token.strip().lower()
    if not q:
        return []
    sent = _sent_recipients()
    found = {}                                             # addr -> [Name, Punkte]
    for name, addr in contacts.people():
        found[addr.lower()] = [name, 1.0]
    for addr, (name, n, ts) in sent.items():
        entry = found.setdefault(addr, [name, 0.0])
        entry[0] = entry[0] or name
        entry[1] += 2.0 + min(n, 20) / 10 + (1.0 if time.time() - ts < 90 * 86400 else 0)   # schon angeschrieben: bevorzugt
    out = []
    for addr, (name, score) in found.items():
        words = [w for w in name.lower().replace(",", " ").split()]
        local = addr.split("@")[0]
        if any(w.startswith(q) for w in words) or name.lower().startswith(q):
            rank = 3
        elif local.startswith(q) or addr.startswith(q):
            rank = 2
        elif q in addr or q in name.lower():
            rank = 1
        else:
            continue
        out.append((rank, score, name, addr))
    out.sort(key=lambda t: (-t[0], -t[1], t[2].lower(), t[3]))
    return [(n, a) for _r, _s, n, a in out[:limit]]


def format_entry(name, addr):
    return formataddr((name, addr)) if name else addr
