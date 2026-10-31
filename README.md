# Postfach – a lean mail client for GNOME

A simple, modern mail client for GNOME (Python · GTK4 · libadwaita): two-column view, unified inbox across accounts,
full-text search with filters, IMAP plus Google/Microsoft 365 via GNOME Online Accounts, HTML and plain-text composing,
notifications, background operation. English by default, German included (`po/`). Licence: GPL-3.0-or-later.

*Run:* `./run.sh` · *Install (Arch/Manjaro):* see below · *Translations:* see "Sprachen". The rest of this README is in German.

---

# Postfach

Schlanker, moderner Mail-Client für GNOME (Python · GTK4 · libadwaita · WebKitGTK).

* Zwei-Spalten-Ansicht: links Nachrichtenliste (2 oder 3 Vorschauzeilen, einstellbar), rechts Lesebereich
* Hamburger-Menü links oben → Konten, Ordner, „Gemeinsamer Posteingang“, Ungelesen, Markiert, Konto hinzufügen, Einstellungen
* Mehrere Konten, gemeinsamer Posteingang über alle Konten
* IMAP (Passwort); Google und Microsoft 365 über die GNOME-Online-Konten (OAuth2/Graph)
* Volltextsuche (SQLite FTS5) über Betreff, Absender, Empfänger und Nachrichtentext, auch Präfixsuche
* Termineinladungen (iCalendar): Anzeige in der Nachricht, Zu-/Absagen (Antwort an den Organisator), „Zum Kalender hinzufügen“
* Senden per SMTP, Antworten, Weiterleiten, Löschen, Archivieren, Stern, Ungelesen
* Passwörter im GNOME-Schlüsselbund (libsecret); HTML-Mails ohne JavaScript, externe Bilder blockiert

## Installation (Arch/Manjaro)

    cd packaging/arch && makepkg -f
    sudo pacman -U postfach-0.1.0-1-any.pkg.tar.zst

Das Paket installiert den Befehl `postfach`, den Starter `eu.ahrenberg.mail`, die Icons und die AppStream-Metadaten
(Anzeige in GNOME Software unter „Installiert“). Optionale Pakete: `gnome-online-accounts`, `evolution-data-server`,
`gnome-shell-extension-appindicator`. Vorher ggf. die Benutzer-Installation entfernen: `./install.sh --uninstall`.

## Start aus dem Projektordner

    ./run.sh

Benötigt: `python-gobject`, `gtk4`, `libadwaita`, `webkitgtk-6.0`, `libsecret` (Python ≥ 3.10).
Daten: Datenbank `mail.db` (SQLite, FTS5) in `~/Dokumente/Postfach/` (einstellbar unter Einstellungen → Speicher), Einstellungen: `~/.config/postfach/config.json`.
Alte Mails können nur noch lokal gehalten werden (Posteingang/Gesendet, Standard 24 Monate; Löschung auf dem Server); Import aus Thunderbird (mbox/Maildir) unter Einstellungen → Speicher.

## Google / Microsoft

Google- und Microsoft-365-Konten werden über die GNOME-Online-Konten (Systemeinstellungen) eingerichtet und erscheinen dann
automatisch in Postfach; eigene App-Registrierungen sind nicht nötig. Alle anderen Anbieter: „Anderes IMAP-Konto“.

## Sprachen

Postfach ist mehrsprachig (Quelltexte englisch, Übersetzung in `po/`). Standard ist Englisch; bei deutschem Desktop
erscheint die App auf Deutsch. Einstellbar unter Einstellungen → Ansicht → Sprache (Systemstandard / English / Deutsch),
gilt nach einem Neustart.

Neue Sprache: `po/postfach.pot` kopieren (`msginit -l fr -i po/postfach.pot -o po/fr.po`), übersetzen, `tools/build_locale.sh`.
Texte im Quelltext ändern → `tools/make_po.py` (aktualisiert `po/postfach.pot` und `po/de.po`), `tools/check_i18n.py` prüft die `_()`-Aufrufe.

## Entwicklung

    python3 tools/test_sync.py            # IMAP-Sync gegen Fake-Server
    python3 tools/seed_demo.py            # Demo-Daten (mit XDG_DATA_HOME/XDG_CONFIG_HOME auf Temp-Ordner!)
    python3 tools/screenshot.py out.png   # Fenster-Screenshot
