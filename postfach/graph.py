"""Microsoft Graph als Mail-Backend für Microsoft-365-Konten aus GNOME-Online-Konten.

GOA liefert für diese Konten ein Graph-Token (kein IMAP-Token); Nachrichten werden als MIME geladen
und wie bei IMAP im lokalen Cache gespeichert."""
import base64
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from . import config, db, goa, mailparse

API = "https://graph.microsoft.com/v1.0"
ROLE_ALIAS = {"inbox": "inbox", "sent": "sentitems", "drafts": "drafts", "trash": "deleteditems",
              "junk": "junkemail", "archive": "archive"}
SELECT = "id,isRead,flag,receivedDateTime"


def is_graph(acc):
    return acc["kind"] == "goa" and (acc["provider"] == "ms_graph" or not acc["imap_host"])


class GraphError(Exception):
    pass


def _request(acc, method, url, body=None, headers=None, raw=False, _retry=True):
    _how, token = goa.credentials(acc["goa_id"])
    if not url.startswith("http"):
        url = API + url
    data = body if isinstance(body, (bytes, type(None))) else json.dumps(body).encode()
    h = {"Authorization": "Bearer " + token, **(headers or {})}
    if isinstance(body, (dict, list)):
        h["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            payload = r.read()
    except urllib.error.HTTPError as e:
        if e.code == 401 and _retry:
            goa.invalidate(acc["goa_id"])
            return _request(acc, method, url, body, headers, raw, _retry=False)
        if e.code == 429:
            time.sleep(int(e.headers.get("Retry-After", "5")))
            return _request(acc, method, url, body, headers, raw, _retry=_retry)
        raise GraphError(f"Microsoft Graph {e.code}: {e.read()[:200].decode(errors='replace')}") from e
    if raw:
        return payload
    return json.loads(payload) if payload else {}


# ---- Ordner -----------------------------------------------------------

def refresh_folders(acc):
    c = db.conn()
    found, known = [], set()
    for role, alias in ROLE_ALIAS.items():
        try:
            f = _request(acc, "GET", f"/me/mailFolders/{alias}?$select=id,displayName")
        except GraphError:
            continue                      # z. B. „archive“ existiert nicht in jedem Postfach
        known.add(f["id"])
        found.append((f["id"], role, f["displayName"]))
    url = "/me/mailFolders?$top=100&$select=id,displayName"
    while url:
        page = _request(acc, "GET", url)
        for f in page.get("value", []):
            if f["id"] not in known:
                found.append((f["id"], "other", f["displayName"]))
        url = page.get("@odata.nextLink")
    for fid, role, title in found:
        c.execute("INSERT INTO folders(account_id,name,role,title) VALUES (?,?,?,?) "
                  "ON CONFLICT(account_id,name) DO UPDATE SET role=excluded.role, title=excluded.title",
                  (acc["id"], fid, role, title))
    ids = [f[0] for f in found]
    c.execute(f"DELETE FROM folders WHERE account_id=? AND name NOT IN ({','.join('?' * len(ids))})", (acc["id"], *ids))
    c.commit()


# ---- Nachrichten ------------------------------------------------------

def _list_remote(acc, folder, depth):
    out, url = [], (f"/me/mailFolders/{folder}/messages?$select={SELECT}&$orderby=receivedDateTime desc"
                    f"&$top={min(depth, 100)}")
    url = url.replace(" ", "%20")
    while url and len(out) < depth:
        page = _request(acc, "GET", url)
        out += page.get("value", [])
        url = page.get("@odata.nextLink")
    return out[:depth]


def _flagged(m):
    return int((m.get("flag") or {}).get("flagStatus") == "flagged")


def sync_folder(acc, folder, depth, on_batch=None, on_new=None):
    c = db.conn()
    remote = _list_remote(acc, folder, depth)
    rmap = {m["id"]: m for m in remote}
    local = {r["remote_id"]: r for r in c.execute(
        "SELECT id, remote_id, seen, flagged, date FROM messages WHERE account_id=? AND folder=? AND remote_id IS NOT NULL",
        (acc["id"], folder))}
    oldest = min((m["receivedDateTime"] for m in remote), default="")
    complete = len(remote) < depth
    for rid, row in local.items():                       # auf dem Server gelöscht
        if rid not in rmap and (complete or _iso_to_ts(oldest) <= (row["date"] or 0)):
            c.execute("DELETE FROM messages WHERE id=?", (row["id"],))
    for rid, m in rmap.items():                          # geänderte Flags
        row = local.get(rid)
        if row and not db.is_recent(row["id"]) and (row["seen"] != int(m.get("isRead", False)) or row["flagged"] != _flagged(m)):
            c.execute("UPDATE messages SET seen=?, flagged=? WHERE id=?",
                      (int(m.get("isRead", False)), _flagged(m), row["id"]))
    new = [m for m in remote if m["id"] not in local]
    first_sync = not local
    next_uid = (c.execute("SELECT COALESCE(MAX(uid),0) FROM messages WHERE account_id=? AND folder=?",
                          (acc["id"], folder)).fetchone()[0]) + 1
    added, fresh = 0, []

    def fetch(m):
        return m, _request(acc, "GET", f"/me/messages/{m['id']}/$value", raw=True)

    with ThreadPoolExecutor(4) as pool:
        for i in range(0, len(new), 12):
            for m, raw in pool.map(lambda m: _safe(fetch, m), new[i:i + 12]):
                if raw is None:
                    continue
                try:
                    p = mailparse.parse(raw)
                except Exception:
                    continue
                seen = int(m.get("isRead", False))
                p.update(account_id=acc["id"], folder=folder, uid=next_uid, remote_id=m["id"],
                         seen=seen, flagged=_flagged(m))
                next_uid += 1
                pk = db.insert_message(p, raw)
                if pk:
                    added += 1
                    if on_new and not first_sync and not seen:
                        fresh.append({"pk": pk, "account_id": acc["id"], "folder": folder, "uid": p["uid"],
                                      "from_name": p["from_name"], "from_addr": p["from_addr"], "subject": p["subject"]})
            c.commit()
            if on_batch:
                on_batch(added)
    c.commit()
    if fresh and on_new:
        on_new(fresh)
    return added


def _safe(fn, m):
    try:
        return fn(m)
    except Exception:
        return m, None


def _iso_to_ts(iso):
    try:
        return int(time.mktime(time.strptime(iso.rstrip("Z")[:19], "%Y-%m-%dT%H:%M:%S"))) - time.timezone if iso else 0
    except ValueError:
        return 0


def sync_account(acc, progress=None, on_batch=None, only_folder=None, on_new=None):
    refresh_folders(acc)
    depth = int(config.get("sync_depth"))
    order = {"inbox": 0, "sent": 1, "drafts": 2, "archive": 3, "trash": 4, "junk": 5, "other": 6}
    total = 0
    for f in sorted(db.folders(acc["id"]), key=lambda f: order.get(f["role"], 9)):
        if only_folder is not None:
            if f["name"] != only_folder:
                continue
        elif f["role"] == "other":
            continue
        if progress:
            progress(f"{acc['email']}: {f['title'] or f['role']}")
        d = depth if f["role"] in ("inbox", "sent", "archive") else min(depth, 100)
        total += sync_folder(acc, f["name"], d, on_batch, on_new if f["role"] == "inbox" else None)
    return total


# ---- Aktionen ---------------------------------------------------------

def _remote_ids(pks):
    for pk in pks:
        m = db.message(pk)
        if m and m["remote_id"]:
            yield db.account(m["account_id"]), m


def set_flag(pks, flag, on):
    for acc, m in _remote_ids(pks):
        body = ({"isRead": bool(on)} if flag == "\\Seen"
                else {"flag": {"flagStatus": "flagged" if on else "notFlagged"}})
        _request(acc, "PATCH", f"/me/messages/{m['remote_id']}", body)


def delete(pks):
    """In „Gelöschte Elemente“ verschieben; liegt die Nachricht schon dort, endgültig löschen."""
    for acc, m in _remote_ids(pks):
        trash = db.folder_by_role(acc["id"], "trash")
        if trash and trash != m["folder"]:
            _request(acc, "POST", f"/me/messages/{m['remote_id']}/move", {"destinationId": "deleteditems"})
        else:
            _request(acc, "DELETE", f"/me/messages/{m['remote_id']}")


def archive(pks):
    for acc, m in _remote_ids(pks):
        if db.folder_by_role(acc["id"], "archive") not in (None, m["folder"]):
            _request(acc, "POST", f"/me/messages/{m['remote_id']}/move", {"destinationId": "archive"})


def spam(pks):
    for acc, m in _remote_ids(pks):
        if db.folder_by_role(acc["id"], "junk") not in (None, m["folder"]):
            _request(acc, "POST", f"/me/messages/{m['remote_id']}/move", {"destinationId": "junkemail"})


def not_spam(pks):
    for acc, m in _remote_ids(pks):
        if db.folder_by_role(acc["id"], "inbox") not in (None, m["folder"]):
            _request(acc, "POST", f"/me/messages/{m['remote_id']}/move", {"destinationId": "inbox"})


def save_draft_mime(acc, mime_bytes):
    """Entwurf aus MIME im Ordner „Entwürfe“ anlegen."""
    _request(acc, "POST", "/me/messages", base64.b64encode(mime_bytes), {"Content-Type": "text/plain"})


def send_mime(acc, mime_bytes):
    """Sendet eine fertige MIME-Nachricht; Graph legt die Kopie selbst unter „Gesendete Elemente“ ab."""
    _request(acc, "POST", "/me/sendMail", base64.b64encode(mime_bytes), {"Content-Type": "text/plain"})
