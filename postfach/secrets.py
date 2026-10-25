"""Passwörter/Token im GNOME-Schlüsselbund (libsecret); Fallback: 0600-Datei."""
import json
import os

import gi

gi.require_version("Secret", "1")
from gi.repository import Secret

from .paths import SECRETS_FALLBACK

# Schema-Name = App-Kennung (frühere Einträge unter „de.ahrenberg.Postfach“ werden nicht mehr gelesen;
# Passwörter müssen einmal neu eingegeben werden).
SCHEMA = Secret.Schema.new(
    "eu.ahrenberg.mail", Secret.SchemaFlags.NONE,
    {"account": Secret.SchemaAttributeType.STRING, "kind": Secret.SchemaAttributeType.STRING},
)


def _fb_read():
    try:
        with open(SECRETS_FALLBACK, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _fb_write(d):
    fd = os.open(SECRETS_FALLBACK, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(d, f)


def store(account_id, kind, value):
    try:
        Secret.password_store_sync(SCHEMA, {"account": str(account_id), "kind": kind},
                                   Secret.COLLECTION_DEFAULT, f"Postfach {kind} ({account_id})", value, None)
        return
    except Exception:
        d = _fb_read()
        d[f"{account_id}:{kind}"] = value
        _fb_write(d)


def lookup(account_id, kind):
    try:
        v = Secret.password_lookup_sync(SCHEMA, {"account": str(account_id), "kind": kind}, None)
        if v:
            return v
    except Exception:
        pass
    return _fb_read().get(f"{account_id}:{kind}")


def clear(account_id):
    for kind in ("password",):
        try:
            Secret.password_clear_sync(SCHEMA, {"account": str(account_id), "kind": kind}, None)
        except Exception:
            pass
    d = _fb_read()
    for kind in ("password",):
        d.pop(f"{account_id}:{kind}", None)
    _fb_write(d)
