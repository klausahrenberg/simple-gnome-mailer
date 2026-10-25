"""Aktualisiert po/postfach.pot (aus dem Quelltext) und erzeugt po/de.po aus den vorhandenen Übersetzungen.
Aufruf:  tools/make_po.py   (benötigt xgettext). Neue/fehlende Übersetzungen werden gemeldet."""
import ast
import glob
import json
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
POT, DE, PAIRS = "po/postfach.pot", "po/de.po", "po/pairs_de.json"
files = sorted(glob.glob("postfach/**/*.py", recursive=True))
subprocess.run(["xgettext", "--from-code=UTF-8", "--language=Python", "-k_", "-kngettext:1,2", "-kN_", "--add-comments=TRANSLATORS",
                "--package-name=postfach", "--msgid-bugs-address=", "--no-wrap", "-o", POT, *files], check=True)


def unq(lines):
    return "".join(ast.literal_eval(l) for l in lines)


def q(s):
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'


entries, cur, key = [], None, None
for line in open(POT, encoding="utf-8").read().split("\n"):
    if line.startswith("#:"):
        if cur is None or cur.get("done"):
            cur = {"refs": [], "msgid": [], "plural": [], "done": False}
            entries.append(cur)
        cur["refs"].append(line[3:])
    elif line.startswith("msgid_plural"):
        key = "plural"; cur["plural"].append(line[len("msgid_plural "):])
    elif line.startswith("msgid "):
        if cur is None or cur.get("done"):
            cur = {"refs": [], "msgid": [], "plural": [], "done": False}
            entries.append(cur)
        key = "msgid"; cur["msgid"].append(line[6:])
    elif line.startswith("msgstr"):
        key = None
        cur["done"] = True
    elif line.startswith('"') and key:
        cur[key].append(line)
entries = [e for e in entries if unq(e["msgid"])]
pairs = json.load(open(PAIRS, encoding="utf-8"))
used, missing = set(), []
out = ['# German translation for Postfach.\nmsgid ""\nmsgstr ""\n"Project-Id-Version: postfach\\n"\n"Last-Translator: Klaus Ahrenberg\\n"\n"Language-Team: German\\n"\n"Language: de\\n"\n'
       '"MIME-Version: 1.0\\n"\n"Content-Type: text/plain; charset=UTF-8\\n"\n"Content-Transfer-Encoding: 8bit\\n"\n'
       '"Plural-Forms: nplurals=2; plural=(n != 1);\\n"\n']
for e in entries:
    mid, plural = unq(e["msgid"]), unq(e["plural"]) if e["plural"] else None
    de = pairs.get(mid)
    if de is None:
        missing.append(mid)
    used.add(mid)
    block = [f"#: {r}" for r in e["refs"][:3]]
    if re.search(r"\{\w*\}", mid):
        block.append("#, python-brace-format")
    block.append(f"msgid {q(mid)}")
    if plural is not None:
        de2 = pairs.get(plural)
        if de2 is None:
            missing.append(plural)
        used.add(plural)
        block += [f"msgid_plural {q(plural)}", f"msgstr[0] {q(de or '')}", f"msgstr[1] {q(de2 or '')}"]
    else:
        block.append(f"msgstr {q(de or '')}")
    out.append("\n".join(block) + "\n")
open(DE, "w", encoding="utf-8").write("\n".join(out))
unused = sorted(set(pairs) - used)
print(f"{len(entries)} Einträge; fehlende Übersetzungen: {len(missing)}; ungenutzte Paare: {len(unused)}")
for m in missing:
    print("  FEHLT:", repr(m))
for m in unused:
    print("  UNGENUTZT:", repr(m))
sys.exit(1 if missing else 0)
