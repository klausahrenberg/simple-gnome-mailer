"""Mehrsprachigkeit (gettext). Quelltexte sind englisch; Übersetzungen liegen in po/*.po.

Sprache: Einstellung „language“ (auto | en | de). „auto“ folgt der Desktop-Sprache (Deutsch, sonst Englisch).
Die Wahl gilt ab dem nächsten Start."""
import gettext
import json
import os
import subprocess

from .paths import CACHE_LOCALE, CONFIG_PATH

DOMAIN = "postfach"
SUPPORTED = ("en", "de")
_PKG_LOCALE = os.path.join(os.path.dirname(__file__), "locale")


def preference():
    """Gespeicherte Wahl: „auto“, „en“ oder „de“ (liest die Konfiguration direkt, bevor GTK startet)."""
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            v = json.load(f).get("language", "auto")
    except (OSError, ValueError):
        v = "auto"
    return v if v in SUPPORTED else "auto"


def desktop_language():
    """Sprache des Desktops: „de“ bei deutscher Locale, sonst „en“ (Standard)."""
    for var in ("LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG"):
        val = os.environ.get(var)
        if val:
            for part in val.split(":"):
                part = part.strip().lower()
                if part.startswith("de"):
                    return "de"
                if part and not part.startswith(("c", "posix")):
                    return "en"
    try:
        from gi.repository import GLib
        for name in GLib.get_language_names():
            if name.lower().startswith("de"):
                return "de"
            if name.lower().startswith("en"):
                break
    except Exception:
        pass
    return "en"


def active_language():
    pref = preference()
    return pref if pref != "auto" else desktop_language()


def _mo_dir(lang):
    """Verzeichnis mit der kompilierten Übersetzung; fehlt die .mo, wird sie aus po/<lang>.po erzeugt (Entwicklung)."""
    po = os.path.join(os.path.dirname(os.path.dirname(__file__)), "po", lang + ".po")
    for base in (_PKG_LOCALE, CACHE_LOCALE, "/usr/share/locale"):
        mo = os.path.join(base, lang, "LC_MESSAGES", DOMAIN + ".mo")
        if os.path.exists(mo) and not (os.path.exists(po) and os.path.getmtime(po) > os.path.getmtime(mo)):
            return base                      # vorhanden und aktuell (Entwicklung: .po neuer → neu kompilieren)
    if os.path.exists(po):
        out = os.path.join(CACHE_LOCALE, lang, "LC_MESSAGES")
        os.makedirs(out, exist_ok=True)
        try:
            subprocess.run(["msgfmt", "-o", os.path.join(out, DOMAIN + ".mo"), po], check=True, capture_output=True)
            return CACHE_LOCALE
        except (OSError, subprocess.CalledProcessError):
            pass
    return None


LANG = active_language()
if preference() != "auto":                 # auch GTK/libadwaita-Texte (Dialoge) in der gewählten Sprache
    os.environ["LANGUAGE"] = LANG
_catalog = None
if LANG != "en":
    d = _mo_dir(LANG)
    if d:
        _catalog = gettext.translation(DOMAIN, d, languages=[LANG], fallback=True)


def _(msg):
    return _catalog.gettext(msg) if _catalog else msg


def ngettext(singular, plural, n):
    return _catalog.ngettext(singular, plural, n) if _catalog else (singular if n == 1 else plural)


def N_(msg):
    """Markiert Text zur Übersetzung, ohne ihn jetzt zu übersetzen (z. B. für Konstanten)."""
    return msg
