"""GNOME Online Accounts: E-Mail-Konten und Zugangsdaten (OAuth-Token/Passwort) vom Desktop übernehmen."""
import gi

try:
    gi.require_version("Goa", "1.0")
    from gi.repository import Goa
except (ValueError, ImportError):
    Goa = None

import threading
import time

from . import db
from .i18n import _

_lock = threading.Lock()   # GOA/D-Bus-Zugriffe aus Worker-Threads serialisieren
_cache = {}               # goa_id -> (art, token, gültig_bis)


def available():
    return Goa is not None


def _mail_objects(client):
    for o in client.get_accounts():
        a, m = o.get_account(), o.get_mail()
        if m and not a.props.mail_disabled and (m.props.imap_supported or a.props.provider_type == "ms_graph"):
            yield o


def import_accounts(client=None):
    """Gleicht die lokale Kontenliste mit GOA ab. True, wenn sich etwas geändert hat."""
    if not Goa:
        return False
    client = client or Goa.Client.new_sync(None)
    c = db.conn()
    have = {r["goa_id"]: r for r in c.execute("SELECT * FROM accounts WHERE kind='goa'")}
    seen, changed = set(), False
    for o in _mail_objects(client):
        a, m = o.get_account(), o.get_mail()
        gid = a.props.id
        seen.add(gid)
        vals = dict(
            email=m.props.email_address or a.props.identity, name=m.props.name or a.props.presentation_identity,
            provider=a.props.provider_type, username=m.props.imap_user_name or a.props.identity,
            imap_host=m.props.imap_host, imap_port=993 if m.props.imap_use_ssl else 143,
            smtp_host=m.props.smtp_host, smtp_port=465 if m.props.smtp_use_ssl else 587,
        )
        if gid in have:
            if any(have[gid][k] != v for k, v in vals.items()):
                c.execute(f"UPDATE accounts SET {', '.join(k + '=?' for k in vals)} WHERE goa_id=?", (*vals.values(), gid))
                changed = True
        else:
            db.add_account(kind="goa", goa_id=gid, **vals)
            changed = True
    for gid in set(have) - seen:
        db.remove_account(have[gid]["id"])
        changed = True
    c.commit()
    return changed


def invalidate(goa_id):
    """Verwirft zwischengespeicherte Token (z. B. nach HTTP 401)."""
    with _lock:
        for k in [k for k in _cache if k[0] == goa_id]:
            del _cache[k]


def _lookup(client, goa_id):
    obj = client.lookup_by_id(goa_id)
    if obj is None:
        raise RuntimeError(_("Online account no longer exists"))
    return obj


def credentials(goa_id, kind="imap"):
    """-> ("oauth", access_token) oder ("password", passwort). Blockierend; für Worker-Threads."""
    with _lock:
        hit = _cache.get((goa_id, kind))
        if hit and hit[2] > time.time() + 60:
            return hit[0], hit[1]
        obj = _lookup(Goa.Client.new_sync(None), goa_id)
        obj.get_account().call_ensure_credentials_sync(None)
        if (o := obj.get_oauth2_based()):
            tok, exp = o.call_get_access_token_sync(None)
            res = ("oauth", tok, time.time() + (exp or 300))
        elif (p := obj.get_password_based()):
            res = ("password", p.call_get_password_sync(f"{kind}-password", None), time.time() + 300)
        else:
            raise RuntimeError(_("No credentials available for the online account"))
        _cache[(goa_id, kind)] = res
        return res[0], res[1]


def smtp_credentials(goa_id):
    with _lock:
        m = _lookup(Goa.Client.new_sync(None), goa_id).get_mail()
        user = m.props.smtp_user_name or m.props.imap_user_name
    how, secret = credentials(goa_id, "smtp" if not _is_oauth(goa_id) else "imap")
    return user, how, secret


def _is_oauth(goa_id):
    with _lock:
        return _lookup(Goa.Client.new_sync(None), goa_id).get_oauth2_based() is not None
