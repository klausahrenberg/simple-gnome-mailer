"""Persistente Einstellungen (JSON) – kein GSettings-Schema nötig."""
import json
import os
import threading

from .paths import CONFIG_PATH

DEFAULTS = {
    "language": "auto",        # auto = Desktop-Sprache (Deutsch, sonst Englisch) | en | de
    "preview_lines": 2,        # 2 oder 3 Zeilen Vorschau in der Nachrichtenliste
    "sync_interval_min": 3,
    "sync_depth": 300,         # neueste N Nachrichten je Ordner
    "load_remote_images": False,
    "load_logos": True,        # Logos von Organisations-Absendern (eigene Webseite der Domain) laden
    "logo_service": False,     # zusätzlich DuckDuckGo-Icon-Dienst nutzen (gibt Absender-Domains an Dritte weiter)
    "compose_html": True,      # neue Nachrichten formatiert (HTML) schreiben; False = Nur-Text
    "reply_follow_original": True,   # Antworten/Weiterleiten im Format der Originalmail
    "shrink_prompt": True,     # beim Hinzufügen großer Anhänge das Verkleinern von Bildern vorschlagen
    "shrink_image_mb": 2,      # Einzelbild ab dieser Größe
    "shrink_total_mb": 5,      # Anhänge insgesamt ab dieser Größe
    "image_target_px": 1600,   # zuletzt gewählte Zielgröße (längste Seite)
    "remote_domains": [],      # Absender-Domains, deren externe Inhalte immer geladen werden
    "run_in_background": True,  # Fenster schließen = im Hintergrund weiterlaufen
    "autostart": False,        # beim Anmelden im Hintergrund starten
    "nav_expanded": None,      # aufgeklappte Knoten der Ordner-Baumansicht
    "list_fraction": 0.36,     # Breite der Nachrichtenliste relativ zum Fenster
    "window_size": [1280, 800],
    "last_view": {"kind": "unified"},
}

_lock = threading.Lock()
_data = None


def _load():
    global _data
    if _data is None:
        try:
            with open(CONFIG_PATH, encoding="utf-8") as f:
                _data = json.load(f)
        except (OSError, ValueError):
            _data = {}
    return _data


def get(key):
    with _lock:
        d = _load()
        return d.get(key, DEFAULTS[key])


def set(key, value):
    with _lock:
        d = _load()
        d[key] = value
        tmp = CONFIG_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, indent=2)
        os.replace(tmp, CONFIG_PATH)
