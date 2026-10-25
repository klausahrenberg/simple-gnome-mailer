# Flathub-Vorbereitung (Stand der Voraussetzungen)

App-Kennung: **eu.ahrenberg.mail** (die Domain ahrenberg.eu gehört dem Autor).

## Erledigt
- [x] Kennung nach eigener Domain; Starter, Icons, Metadaten, D-Bus-Name und Statusleisten-Symbol verwenden sie
- [x] Lizenz: GPL-3.0-or-later (`LICENSE`), Metadaten unter CC0-1.0
- [x] Starter (`data/eu.ahrenberg.mail.desktop`) besteht `desktop-file-validate`
- [x] AppStream-Metainfo (`data/eu.ahrenberg.mail.metainfo.xml`): einzige Warnung der Prüfung ist die fehlende Homepage (siehe unten)
- [x] Symbole als SVG (farbig, symbolisch, ungelesen), Branding-Farben
- [x] Screenshots mit frei erfundenen Demo-Daten: `data/screenshots/*.png` (neu erzeugen: `tools/make_screenshots.py`)
- [x] Demo-Modus (`POSTFACH_DEMO=1`): kein Konten-, Kontakte- oder Netzzugriff, keine Serveraktionen
- [x] Mehrsprachig: Quelltexte englisch, `po/de.po`; Starter und Metainfo mit deutschen Übersetzungen
- [x] Arch-Paket (`packaging/arch/PKGBUILD`) und Python-Paket (`pyproject.toml`)

## Noch offen (braucht Entscheidungen oder Zugänge)
- [x] Öffentliches Repository: https://github.com/klausahrenberg/simple-gnome-mailer (Homepage-, Fehler- und Quellcode-Adresse sind in der Metainfo eingetragen)
- [ ] Screenshot-URLs in der Metainfo (https, z. B. Raw-Dateien aus dem Repository):

      <screenshots>
        <screenshot type="default"><caption>Zwei-Spalten-Ansicht mit gemeinsamem Posteingang</caption>
          <image>https://…/data/screenshots/01-zwei-spalten-hell.png</image></screenshot>
        <screenshot><caption>Dunkelmodus mit HTML-Nachricht</caption>
          <image>https://…/data/screenshots/02-zwei-spalten-dunkel.png</image></screenshot>
        <screenshot><caption>Klappbarer Ordnerbaum</caption>
          <image>https://…/data/screenshots/03-ordnerbaum.png</image></screenshot>
        <screenshot><caption>Verfassen mit Formatierung und Anhängen</caption>
          <image>https://…/data/screenshots/04-verfassen.png</image></screenshot>
      </screenshots>

- [ ] Domain-Verifizierung bei Flathub (optional, für das „verifiziert“-Zeichen): Flathub nennt eine Token-Zeile, die als Datei
      `https://ahrenberg.eu/.well-known/org.flathub.VerifiedApps.txt` abgelegt wird. Dort muss nichts veröffentlicht werden – nur diese Textdatei.
- [ ] Flatpak-Manifest (`eu.ahrenberg.mail.yml`) mit Berechtigungen für GNOME-Online-Konten, Kontakte (Evolution Data Server),
      Statusleiste (StatusNotifier), Schlüsselbund und Benachrichtigungen; Autostart über das Hintergrund-Portal
- [x] Übersetzbarkeit (gettext): Englisch (Standard) und Deutsch, Sprache einstellbar; weitere Sprachen über `po/`
- [ ] Hinweis in der Beschreibung: Nachrichten liegen unverschlüsselt im lokalen Cache
- [ ] Namensprüfung „Postfach“ (Verwechslung/Markenrecht)
