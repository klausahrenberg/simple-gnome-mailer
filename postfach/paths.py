import json
import os
import shutil


def _xdg(var, default):
    return os.path.join(os.environ.get(var) or os.path.expanduser(default), "postfach")


CONFIG_DIR = _xdg("XDG_CONFIG_HOME", "~/.config")
os.makedirs(CONFIG_DIR, mode=0o700, exist_ok=True)
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")
SECRETS_FALLBACK = os.path.join(CONFIG_DIR, "secrets.json")
CACHE_LOCALE = os.path.join(os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache"), "postfach", "locale")

OLD_DATA_DIR = _xdg("XDG_DATA_HOME", "~/.local/share")   # Speicherort bis Version 0.1
DB_FILES = ("mail.db", "mail.db-wal", "mail.db-shm")


def documents_dir():
    """Dokumente-Ordner des Benutzers (XDG_DOCUMENTS_DIR), sonst ~/Documents."""
    try:
        with open(os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"),
                               "user-dirs.dirs"), encoding="utf-8") as f:
            for line in f:
                if line.startswith("XDG_DOCUMENTS_DIR="):
                    p = line.split("=", 1)[1].strip().strip('"').replace("$HOME", os.path.expanduser("~"))
                    if p and os.path.abspath(p) != os.path.expanduser("~"):
                        return p
    except OSError:
        pass
    return os.path.expanduser("~/Documents")


DEFAULT_DATA_DIR = os.path.join(documents_dir(), "Postfach")


def _read_cfg():
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _write_cfg(cfg):
    tmp = CONFIG_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)
    os.replace(tmp, CONFIG_PATH)


def _move_db(src, dst):
    """Verschiebt die Datenbankdateien (nur vor dem ersten Öffnen aufrufen). True bei Erfolg."""
    if not os.path.exists(os.path.join(src, "mail.db")) or os.path.exists(os.path.join(dst, "mail.db")):
        return False
    try:
        os.makedirs(dst, mode=0o700, exist_ok=True)
        for f in DB_FILES:                       # Hauptdatei zuletzt entfernen, falls etwas schiefgeht
            if os.path.exists(os.path.join(src, f)):
                shutil.copy2(os.path.join(src, f), os.path.join(dst, f))
        for f in DB_FILES:
            if os.path.exists(os.path.join(src, f)):
                os.remove(os.path.join(src, f))
        return True
    except OSError:
        for f in DB_FILES:                       # unvollständige Kopie wieder entfernen, Quelle bleibt maßgeblich
            try:
                os.remove(os.path.join(dst, f))
            except OSError:
                pass
        return False


def _resolve():
    """Datenordner bestimmen; bestehende Datenbank bei neuem Ordner mitnehmen (Standard: Dokumente/Postfach)."""
    env = os.environ.get("POSTFACH_DATA_DIR")
    if env:
        return env
    cfg = _read_cfg()
    target = cfg.get("data_dir") or DEFAULT_DATA_DIR
    source = cfg.get("data_dir_from") or OLD_DATA_DIR
    if os.path.abspath(source) != os.path.abspath(target) and _move_db(source, target):
        pass
    elif os.path.abspath(source) != os.path.abspath(target) and not os.path.exists(os.path.join(target, "mail.db")) \
            and os.path.exists(os.path.join(source, "mail.db")):
        return source                            # Umzug nicht möglich (z. B. keine Schreibrechte): vorerst alter Ort
    if "data_dir_from" in cfg:
        cfg.pop("data_dir_from")
        _write_cfg(cfg)
    return target


DATA_DIR = _resolve()
os.makedirs(DATA_DIR, mode=0o700, exist_ok=True)
DB_PATH = os.path.join(DATA_DIR, "mail.db")

