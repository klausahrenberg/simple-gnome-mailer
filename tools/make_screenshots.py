"""Erzeugt die Store-Screenshots (data/screenshots) mit erfundenen Demo-Daten – ohne Konten, Netz oder echte Mails.
Aufruf:  tools/make_screenshots.py   (legt eine temporäre Demo-Datenbank an)"""
import os
import subprocess
import sys
import tempfile

ROOT = __file__.rsplit("/tools/", 1)[0]
OUT = os.path.join(ROOT, "data", "screenshots")
tmp = tempfile.mkdtemp()
env = dict(os.environ, POSTFACH_DEMO="1", XDG_DATA_HOME=tmp, XDG_CONFIG_HOME=tmp, XDG_CACHE_HOME=tmp + "/cache")
subprocess.run([sys.executable, os.path.join(ROOT, "tools", "seed_demo.py")], env=env, check=True, timeout=60)
os.makedirs(OUT, exist_ok=True)
shot = os.path.join(ROOT, "tools", "screenshot.py")
for name, args in (("01-zwei-spalten-hell", ["1280", "800", "select=2", "light"]),
                   ("02-zwei-spalten-dunkel", ["1280", "800", "select=1", "dark"]),
                   ("03-ordnerbaum", ["1280", "800", "select=2", "drawer", "light"]),
                   ("04-verfassen", ["820", "620", "composeshot", "light"])):
    subprocess.run([sys.executable, shot, os.path.join(OUT, name + ".png"), *args], env=env, check=True, timeout=60)
