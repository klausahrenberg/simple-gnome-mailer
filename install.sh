#!/bin/sh
# Postfach für den aktuellen Benutzer einrichten (Ordner kopieren, einmal ausführen):
#   ./install.sh              prüft/installiert fehlende Pakete, legt Starter, Symbole und den Befehl „postfach“ an
#   ./install.sh --yes        fehlende Pakete ohne Rückfrage installieren (sudo)
#   ./install.sh --check      nur prüfen, nichts ändern
#   ./install.sh --no-deps    Paketprüfung überspringen
#   ./install.sh --uninstall  Starter, Symbole und Befehl wieder entfernen (Daten und Einstellungen bleiben)
# Der Ordner muss danach an seinem Platz bleiben: Starter und Befehl zeigen auf run.sh in diesem Ordner.
set -e
here="$(cd "$(dirname "$0")" && pwd)"
data="${XDG_DATA_HOME:-$HOME/.local/share}"
apps="$data/applications"
icons="$data/icons/hicolor/scalable/apps"
bindir="$HOME/.local/bin"

mode=install; yes=0; deps=1
for a in "$@"; do
  case "$a" in
    --uninstall) mode=uninstall ;;
    --check) mode=check ;;
    --yes|-y) yes=1 ;;
    --no-deps) deps=0 ;;
    -h|--help) sed -n '2,8p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "Unbekannte Option: $a" >&2; exit 2 ;;
  esac
done

if [ "$mode" = uninstall ]; then
  rm -f "$apps/eu.ahrenberg.mail.desktop" "$icons"/eu.ahrenberg.mail*.svg "$bindir/postfach"
  gtk-update-icon-cache -q -t "$data/icons/hicolor" 2>/dev/null || true
  update-desktop-database -q "$apps" 2>/dev/null || true
  echo "Postfach wurde entfernt (Nachrichten und Einstellungen bleiben erhalten)."
  exit 0
fi

# ---- Abhängigkeiten ----------------------------------------------------
py="$(command -v python3 || true)"
[ -n "$py" ] || { echo "python3 fehlt – bitte zuerst Python 3 installieren." >&2; exit 1; }
"$py" -c 'import sys; sys.exit(sys.version_info < (3, 10))' || { echo "Python 3.10 oder neuer wird benötigt." >&2; exit 1; }

# ok <Python-Ausdruck>: 0, wenn der Ausdruck fehlerfrei läuft
ok() { "$py" -c "$1" >/dev/null 2>&1; }
gi() { ok "import gi; gi.require_version('$1', '$2'); from gi.repository import $1"; }

missing=""      # Pflicht
optional=""     # optional
need() { missing="$missing $1"; }
want() { optional="$optional $1"; }

ok "import gi" || need gi
gi Gtk 4.0 || need gtk4
gi Adw 1 || need adw
gi WebKit 6.0 || need webkit
gi Secret 1 || need secret
command -v msgfmt >/dev/null 2>&1 || want gettext
gi Goa 1.0 || want goa
gi EDataServer 1.2 || want eds

# Paketnamen je Distribution
. /etc/os-release 2>/dev/null || true
family="${ID:-} ${ID_LIKE:-}"
case " $family " in
  *" arch "*|*" manjaro "*) mgr="pacman"; inst="sudo pacman -S --needed" ;;
  *" debian "*|*" ubuntu "*) mgr="apt"; inst="sudo apt install" ;;
  *" fedora "*|*" rhel "*) mgr="dnf"; inst="sudo dnf install" ;;
  *" suse "*|*" opensuse "*) mgr="zypper"; inst="sudo zypper install" ;;
  *) mgr=""; inst="" ;;
esac
pkg() {   # Schlüssel -> Paketname der erkannten Distribution
  case "$mgr:$1" in
    pacman:gi) echo python-gobject;;   pacman:gtk4) echo gtk4;;   pacman:adw) echo libadwaita;;
    pacman:webkit) echo webkitgtk-6.0;;   pacman:secret) echo libsecret;;   pacman:gettext) echo gettext;;
    pacman:goa) echo gnome-online-accounts;;   pacman:eds) echo evolution-data-server;;
    apt:gi) echo python3-gi;;   apt:gtk4) echo gir1.2-gtk-4.0;;   apt:adw) echo gir1.2-adw-1;;
    apt:webkit) echo gir1.2-webkit-6.0;;   apt:secret) echo gir1.2-secret-1;;   apt:gettext) echo gettext;;
    apt:goa) echo gir1.2-goa-1.0;;   apt:eds) echo "gir1.2-edataserver-1.2 gir1.2-ebook-1.2 gir1.2-ebookcontacts-1.2";;
    dnf:gi) echo python3-gobject;;   dnf:gtk4) echo gtk4;;   dnf:adw) echo libadwaita;;
    dnf:webkit) echo webkitgtk6.0;;   dnf:secret) echo libsecret;;   dnf:gettext) echo gettext;;
    dnf:goa) echo gnome-online-accounts;;   dnf:eds) echo evolution-data-server;;
    zypper:gi) echo python3-gobject;;   zypper:gtk4) echo typelib-1_0-Gtk-4_0;;   zypper:adw) echo typelib-1_0-Adw-1;;
    zypper:webkit) echo typelib-1_0-WebKit-6_0;;   zypper:secret) echo typelib-1_0-Secret-1;;
    zypper:gettext) echo gettext-runtime;;   zypper:goa) echo typelib-1_0-Goa-1_0;;   zypper:eds) echo evolution-data-server;;
  esac
}
names() { for k in $1; do pkg "$k"; done | tr '\n' ' '; }
label() {
  case "$1" in
    gi) echo "PyGObject";; gtk4) echo "GTK 4";; adw) echo "libadwaita";; webkit) echo "WebKitGTK 6";;
    secret) echo "libsecret (Schlüsselbund)";; gettext) echo "gettext (Übersetzungen)";;
    goa) echo "GNOME-Online-Konten (Google/Microsoft)";; eds) echo "Evolution Data Server (Kontaktfotos)";;
  esac
}

if [ "$deps" = 1 ]; then
  echo "Prüfe Abhängigkeiten …"
  if [ -z "$missing$optional" ]; then
    echo "  alles vorhanden."
  else
    for k in $missing; do echo "  FEHLT (nötig):    $(label "$k")"; done
    for k in $optional; do echo "  fehlt (optional): $(label "$k")"; done
  fi
  if [ -n "$missing$optional" ]; then
    if [ -z "$mgr" ]; then
      echo "Distribution nicht erkannt – bitte die oben genannten Pakete selbst installieren."
    elif [ "$mode" = check ]; then
      echo "Installieren mit: $inst $(names "$missing$optional")"
    else
      want_pkgs="$(names "$missing$optional")"
      answer=n
      if [ "$yes" = 1 ]; then answer=y
      elif [ -t 0 ]; then printf "Fehlende Pakete jetzt installieren (%s %s)? [J/n] " "$inst" "$want_pkgs"; read -r answer || answer=n
        case "$answer" in ""|j|J|y|Y) answer=y ;; *) answer=n ;; esac
      fi
      if [ "$answer" = y ]; then
        # shellcheck disable=SC2086
        $inst $want_pkgs || echo "Die Paketinstallation ist fehlgeschlagen – bitte manuell nachholen."
      else
        echo "Übersprungen. Manuell: $inst $want_pkgs"
      fi
    fi
    # erneut prüfen: Pflichtpakete müssen jetzt da sein
    still=""
    ok "import gi" || still=1
    gi Gtk 4.0 || still=1
    gi Adw 1 || still=1
    gi WebKit 6.0 || still=1
    gi Secret 1 || still=1
    if [ -n "$still" ] && [ "$mode" != check ]; then
      echo "Pflichtpakete fehlen noch – Postfach startet erst, wenn sie installiert sind. Starter wird trotzdem angelegt."
    fi
  fi
fi
[ "$mode" = check ] && exit 0

# ---- Übersetzungen -----------------------------------------------------
if command -v msgfmt >/dev/null 2>&1; then
  "$here/tools/build_locale.sh" >/dev/null || echo "Hinweis: Übersetzungen konnten nicht kompiliert werden (App läuft auf Englisch)."
else
  echo "Hinweis: gettext (msgfmt) fehlt – die App läuft auf Englisch."
fi

# ---- Starter, Symbole, Befehl ------------------------------------------
# frühere Kennungen aufräumen
for old in de.ahrenberg.Postfach de.ahrenberg.mail; do
  rm -f "$apps/$old.desktop" "$icons"/$old*.svg
done
mkdir -p "$apps" "$icons" "$bindir"
cp "$here"/postfach/icons/hicolor/scalable/apps/*.svg "$icons/"
chmod +x "$here/run.sh"
sed "s|^Exec=.*|Exec=\"$here/run.sh\"|" "$here/data/eu.ahrenberg.mail.desktop" > "$apps/eu.ahrenberg.mail.desktop"
printf '#!/bin/sh\nexec "%s/run.sh" "$@"\n' "$here" > "$bindir/postfach"
chmod +x "$bindir/postfach"
gtk-update-icon-cache -q -t "$data/icons/hicolor" 2>/dev/null || true
update-desktop-database -q "$apps" 2>/dev/null || true

echo "Postfach ist eingerichtet: im Anwendungsmenü unter „Postfach“ oder mit dem Befehl »postfach«."
case ":$PATH:" in *":$bindir:"*) ;; *) echo "Hinweis: $bindir liegt nicht in PATH (nach erneuter Anmeldung meist der Fall)." ;; esac
echo "Symbol im Benachrichtigungsbereich: unter GNOME wird die Erweiterung „AppIndicator and KStatusNotifierItem Support“ benötigt."
