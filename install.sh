#!/bin/sh
# Installiert Starter und Icons für den aktuellen Benutzer (~/.local); Entfernen: ./install.sh --uninstall
set -e
here="$(cd "$(dirname "$0")" && pwd)"
apps="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
icons="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor/scalable/apps"
# frühere Kennungen (de.ahrenberg.Postfach, de.ahrenberg.mail) aufräumen
for old in de.ahrenberg.Postfach de.ahrenberg.mail; do
  rm -f "$apps/$old.desktop" "$icons"/$old*.svg
done
if [ "$1" = "--uninstall" ]; then
  rm -f "$apps/eu.ahrenberg.mail.desktop" "$icons"/eu.ahrenberg.mail*.svg
else
  mkdir -p "$apps" "$icons"
  cp "$here"/postfach/icons/hicolor/scalable/apps/*.svg "$icons/"
  sed "s|^Exec=.*|Exec=$here/run.sh|" "$here/data/eu.ahrenberg.mail.desktop" > "$apps/eu.ahrenberg.mail.desktop"
fi
gtk-update-icon-cache -q -t "${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor" 2>/dev/null || true
update-desktop-database -q "$apps" 2>/dev/null || true
