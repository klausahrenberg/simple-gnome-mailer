# Postfach: nächtliches Datenbank-Backup

Die Datenbank (`mail.db`) ist mehrere GB groß und ändert sich ständig. Eine Dateisynchronisation wie Syncthing eignet sich
dafür nicht (hohe Last, mögliche Inkonsistenz durch die WAL-Datei). Dieses Backup erzeugt stattdessen jede Nacht einen
**konsistenten Schnappschuss** (`sqlite3 .backup`, funktioniert bei laufender App) und kann ihn zusätzlich per **restic**
verschlüsselt und mit Versionen auf einen Server sichern – dabei werden nur geänderte Teile übertragen.

    ./install.sh            # installiert Skript, Dienst und Timer (startet nichts)
    ./install.sh --enable   # zusätzlich den Timer aktivieren (täglich ca. 03:30, verpasste Läufe werden nachgeholt)
    ./install.sh --remove   # wieder entfernen (Backups bleiben)

Einstellungen: `~/.config/postfach/backup.conf` (Vorlage: `postfach-backup.conf.example`).

- Schnappschuss: `<Datenordner>/syncthing-backup/mail.db`, bei der Standardeinstellung `~/Dokumente/Postfach/syncthing-backup/mail.db`
  (wird einmal am Tag ersetzt und liegt damit im Syncthing-Ordner „Dokumente“). In dessen `.stignore` stehen nur die Live-Dateien
  (`/Postfach/mail.db`, `…-wal`, `…-shm`); der Unterordner `syncthing-backup` bleibt synchronisiert. Die Arbeitskopie entsteht
  vorher in `~/.cache/postfach/`, Syncthing sieht die fertige Datei erst nach dem Umbenennen.
- Sofort ausführen: `systemctl --user start postfach-backup.service`, Ausgabe: `journalctl --user -u postfach-backup`.
- restic (optional): `sudo pacman -S restic`, dann `RESTIC_REPOSITORY` und `RESTIC_PASSWORD_FILE` in der Konfiguration setzen.
- Wiederherstellen: App beenden, `mail.db` (und `config.json`) aus dem Backup in den Datenordner kopieren, `mail.db-wal` und
  `mail.db-shm` dort löschen, App starten. Mit restic: `restic restore latest --tag postfach --target <Ordner>`.
