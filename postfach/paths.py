import os

def _xdg(var, default):
    return os.path.join(os.environ.get(var) or os.path.expanduser(default), "postfach")

DATA_DIR = _xdg("XDG_DATA_HOME", "~/.local/share")
CONFIG_DIR = _xdg("XDG_CONFIG_HOME", "~/.config")
for _d in (DATA_DIR, CONFIG_DIR):
    os.makedirs(_d, mode=0o700, exist_ok=True)

CACHE_LOCALE = os.path.join(os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache"), "postfach", "locale")
DB_PATH = os.path.join(DATA_DIR, "mail.db")
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")
SECRETS_FALLBACK = os.path.join(CONFIG_DIR, "secrets.json")
