#!/bin/sh
# Richtet das nächtliche Postfach-Backup für den aktuellen Benutzer ein.
#   ./install.sh            Dateien installieren (nichts wird gestartet)
#   ./install.sh --enable   zusätzlich den Timer aktivieren
#   ./install.sh --remove   alles wieder entfernen (Backups bleiben erhalten)
set -e
here="$(cd "$(dirname "$0")" && pwd)"
bin="$HOME/.local/bin"
units="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
conf="${XDG_CONFIG_HOME:-$HOME/.config}/postfach"
case "${1:-}" in
  --remove)
    systemctl --user disable --now postfach-backup.timer 2>/dev/null || true
    rm -f "$bin/postfach-backup" "$units/postfach-backup.service" "$units/postfach-backup.timer"
    systemctl --user daemon-reload
    echo "Backup-Timer entfernt."; exit 0 ;;
esac
mkdir -p "$bin" "$units" "$conf"
install -m 755 "$here/postfach-backup" "$bin/postfach-backup"
install -m 644 "$here/postfach-backup.service" "$here/postfach-backup.timer" "$units/"
[ -f "$conf/backup.conf" ] || install -m 644 "$here/postfach-backup.conf.example" "$conf/backup.conf"
systemctl --user daemon-reload
if [ "${1:-}" = "--enable" ]; then
  systemctl --user enable --now postfach-backup.timer
  echo "Timer aktiv. Nächster Lauf:"; systemctl --user list-timers postfach-backup.timer --no-pager | head -3
else
  echo "Installiert. Aktivieren mit:  systemctl --user enable --now postfach-backup.timer"
  echo "Sofort einmal ausführen:      systemctl --user start postfach-backup.service  (Ausgabe: journalctl --user -u postfach-backup)"
fi
echo "Einstellungen: $conf/backup.conf"
