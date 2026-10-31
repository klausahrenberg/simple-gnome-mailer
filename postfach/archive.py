"""Lokales Archiv: alte Mails (Posteingang und Gesendet) nur noch lokal behalten und auf dem Server löschen.

Eine Nachricht wird erst vom Server entfernt, nachdem sie vollständig geladen und in der lokalen Datenbank
gespeichert (committed) wurde. Archivierte Nachrichten bleiben in ihrem Konto und Ordner (gemeinsamer Posteingang /
Gesendet, Suche), tragen aber eine negative UID und `archived=1`; der Abgleich mit dem Server lässt sie unberührt."""
import datetime
import time
import urllib.parse
import zlib

from . import config, db, graph, mailparse

ROLES = ("inbox", "sent")
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def cutoff_ts(months, now=None):
    """Zeitpunkt, vor dem Mails als „alt“ gelten (Kalendermonate zurück)."""
    now = now or datetime.datetime.now()
    y, m = divmod(now.year * 12 + now.month - 1 - int(months), 12)
    day = min(now.day, 28)
    return datetime.datetime(y, m + 1, day).timestamp()


def store(acc_id, folder, role, raw, seen, flagged, existing=None, remote_id=None):
    """Legt `raw` als lokal archivierte Nachricht ab (oder wandelt die vorhandene Zeile `existing` um).
    Gibt die pk zurück; None, wenn die Nachricht schon archiviert vorliegt (Duplikat)."""
    m = mailparse.parse(raw)
    if existing:
        c = db.conn()
        c.execute("UPDATE raw SET data=? WHERE message_pk=?", (zlib.compress(raw, 6), existing))
        c.execute("UPDATE messages SET archived=1, partial=0, uid=?, remote_id=NULL, has_att=?, att_names=?, has_cal=? WHERE id=?",
                  (db.next_local_uid(), m["has_att"], m["att_names"], m["has_cal"], existing))
        c.commit()
        return existing
    dup = db.find_duplicate(role, m["message_id"], m["date"], m["subject"], m["from_addr"], archived_only=True)
    if dup:
        return None
    m.update(account_id=acc_id, folder=folder, uid=db.next_local_uid(), seen=int(seen), flagged=int(flagged), archived=1)
    pk = db.insert_message(m, raw)
    db.conn().commit()
    return pk


# ---- IMAP -------------------------------------------------------------

def _imap_date(ts):
    d = datetime.datetime.fromtimestamp(ts)
    return f"{d.day}-{MONTHS[d.month - 1]}-{d.year}"


def _imap_delete(im, acc, folder, uids):
    """Löscht die UIDs auf dem Server. Gmail entfernt beim Löschen nur das Label – dort wandert die Mail in den Papierkorb."""
    from .imapsync import _q
    ids = ",".join(map(str, uids))
    if acc["provider"] == "google":
        trash = db.folder_by_role(acc["id"], "trash")
        if trash and trash != folder:
            im.uid("COPY", ids, _q(trash))
    im.uid("STORE", ids, "+FLAGS", "(\\Deleted)")
    if "UIDPLUS" in getattr(im, "capabilities", ()):
        im.uid("EXPUNGE", ids)
    else:
        im.expunge()


def _imap_account(acc, cutoff, progress):
    from .imapsync import _fetch_items, _parse_meta, _q, connect
    done = 0
    im = connect(acc)
    try:
        for f in db.folders(acc["id"]):
            if f["role"] not in ROLES:
                continue
            typ, _x = im.select(_q(f["name"]))
            if typ != "OK":
                continue
            typ, data = im.uid("SEARCH", None, "BEFORE", _imap_date(cutoff))
            uids = sorted(int(x) for x in (data[0] or b"").split()) if typ == "OK" else []
            local = {r["uid"]: r["id"] for r in db.conn().execute(
                "SELECT uid, id FROM messages WHERE account_id=? AND folder=? AND archived=0", (acc["id"], f["name"]))}
            for i in range(0, len(uids), 10):
                batch = uids[i:i + 10]
                typ, data = im.uid("FETCH", ",".join(map(str, batch)), "(FLAGS BODY.PEEK[])")
                if typ != "OK":
                    continue
                safe = []
                for meta, raw in _fetch_items(data):
                    uid, seen, flagged = _parse_meta(meta)
                    if not raw or uid is None:
                        continue
                    try:
                        store(acc["id"], f["name"], f["role"], raw, seen, flagged, existing=local.get(uid))
                    except Exception:
                        continue          # nicht speicherbar -> auf dem Server lassen
                    safe.append(uid)
                if safe:
                    _imap_delete(im, acc, f["name"], safe)
                    done += len(safe)
                    if progress:
                        progress(done)
    finally:
        try:
            im.logout()
        except Exception:
            pass
    return done


# ---- Microsoft Graph --------------------------------------------------

def _graph_account(acc, cutoff, progress):
    done = 0
    iso = datetime.datetime.fromtimestamp(cutoff, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    for f in db.folders(acc["id"]):
        if f["role"] not in ROLES:
            continue
        flt = urllib.parse.quote(f"receivedDateTime lt {iso}")
        url = (f"/me/mailFolders/{f['name']}/messages?$select={graph.SELECT}&$filter={flt}"
               f"&$orderby=receivedDateTime%20desc&$top=100")
        old = []
        while url:
            page = graph._request(acc, "GET", url)
            old += page.get("value", [])
            url = page.get("@odata.nextLink")
        local = {r["remote_id"]: r["id"] for r in db.conn().execute(
            "SELECT remote_id, id FROM messages WHERE account_id=? AND folder=? AND archived=0 AND remote_id IS NOT NULL",
            (acc["id"], f["name"]))}
        for m in old:
            try:
                raw = graph._request(acc, "GET", f"/me/messages/{m['id']}/$value", raw=True)
                store(acc["id"], f["name"], f["role"], raw, m.get("isRead", False), graph._flagged(m),
                      existing=local.get(m["id"]))
                graph._request(acc, "POST", f"/me/messages/{m['id']}/permanentDelete")
            except Exception:
                continue
            done += 1
            if progress:
                progress(done)
    return done


# ---- Einstieg ---------------------------------------------------------

def run(progress=None, months=None):
    """Archiviert alle Konten; gibt (Anzahl, Fehlerliste) zurück."""
    months = months or int(config.get("archive_months"))
    cutoff = cutoff_ts(months)
    total, errors = 0, []
    base = [0]
    for acc in db.mail_accounts():
        try:
            def prog(n, b=base):
                if progress:
                    progress(b[0] + n)
            n = (_graph_account if graph.is_graph(acc) else _imap_account)(acc, cutoff, prog)
            base[0] += n
            total += n
        except Exception as e:
            errors.append(f"{acc['email']}: {e}")
    return total, errors
