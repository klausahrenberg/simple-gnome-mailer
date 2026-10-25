"""Kontaktfotos aus GNOME-Kontakten (Evolution Data Server; enthält Fotos verknüpfter Online-Konten)."""
import threading
import time

import gi

try:
    gi.require_version("EDataServer", "1.2")
    gi.require_version("EBookContacts", "1.2")
    gi.require_version("EBook", "1.2")
    from gi.repository import EBook, EBookContacts, EDataServer
except (ValueError, ImportError):
    EDataServer = None

gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, GLib

_photos = {}     # e-mail (klein) -> Pfad oder bytes
_people = {}     # e-mail (klein) -> Anzeigename (für die Adressvorschläge)
_textures = {}   # e-mail -> Gdk.Texture | None
_lock = threading.Lock()
_loaded_at = 0.0


def available():
    return EDataServer is not None


def load():
    """Liest alle Kontakte mit E-Mail und Foto. Blockierend → Worker-Thread. True, wenn sich etwas geändert hat."""
    global _loaded_at
    if not EDataServer:
        return False
    found, people = {}, {}
    try:
        reg = EDataServer.SourceRegistry.new_sync(None)
        query = EBookContacts.BookQuery.field_exists(EBookContacts.ContactField.EMAIL).to_string()
        for src in reg.list_sources(EDataServer.SOURCE_EXTENSION_ADDRESS_BOOK):
            try:
                ok, contacts = EBook.BookClient.connect_sync(src, 30, None).get_contacts_sync(query, None)
            except GLib.Error:
                continue
            for c in contacts or []:
                name = (c.get_property("full-name") or "").strip()
                mails = [(attr.get_value() or "").strip().lower() for attr in c.get_attributes()
                         if attr.get_name() == "EMAIL"]
                for mail in filter(None, mails):
                    people.setdefault(mail, name)
                photo = c.get_property("photo")
                if not photo:
                    continue
                if photo.type == EBookContacts.ContactPhotoType.URI:
                    uri = photo.get_uri() or ""
                    data = GLib.filename_from_uri(uri)[0] if uri.startswith("file://") else None
                else:
                    data = bytes(photo.get_inlined() or b"") or None
                if data:
                    for mail in filter(None, mails):
                        found.setdefault(mail, data)
    except Exception:
        return False
    with _lock:
        changed = found != _photos or people != _people
        _people.clear()
        _people.update(people)
        _photos.clear()
        _photos.update(found)
        _textures.clear()
    _loaded_at = time.time()
    return changed


def stale(max_age=600):
    return time.time() - _loaded_at > max_age


def texture(email):
    """Gdk.Texture für die Adresse oder None (nur im Hauptthread aufrufen)."""
    key = (email or "").strip().lower()
    if key in _textures:
        return _textures[key]
    with _lock:
        data = _photos.get(key)
    tex = None
    if data:
        try:
            tex = Gdk.Texture.new_from_filename(data) if isinstance(data, str) \
                else Gdk.Texture.new_from_bytes(GLib.Bytes.new(data))
        except GLib.Error:
            tex = None
    _textures[key] = tex
    return tex


def people():
    """[(Name, Adresse)] aller Kontakte mit E-Mail-Adresse."""
    with _lock:
        return [(n, a) for a, n in _people.items()]
