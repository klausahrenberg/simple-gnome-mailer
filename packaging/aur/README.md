# Postfach im AUR veröffentlichen

Paketname: **postfach** (im AUR frei, Stand 2026-10-25). Quelle: Release-Tarball des GitHub-Tags `v<pkgver>`.

## Einmalig
1. Konto auf https://aur.archlinux.org anlegen, öffentlichen SSH-Schlüssel im Profil hinterlegen.
2. Leeres AUR-Repository für das Paket anlegen (entsteht beim ersten Push):

       git clone ssh://aur@aur.archlinux.org/postfach.git aur-postfach

## Erste Veröffentlichung (v0.1.0)
1. Tag auf GitHub: `git push origin main v0.1.0`
2. Prüfsumme des Tarballs eintragen (statt `SKIP`):

       curl -L https://github.com/klausahrenberg/simple-gnome-mailer/archive/refs/tags/v0.1.0.tar.gz | sha256sum

   → in `PKGBUILD` bei `sha256sums=(…)` einsetzen (oder `updpkgsums` aus `pacman-contrib`).
3. `.SRCINFO` erzeugen und in einem sauberen Verzeichnis prüfen:

       makepkg --printsrcinfo > .SRCINFO
       makepkg -f            # Testbau (optional: in einem sauberen chroot mit `extra-x86_64-build`)

4. Beide Dateien ins AUR-Repository kopieren und pushen:

       cp PKGBUILD .SRCINFO ../aur-postfach/ && cd ../aur-postfach
       git add PKGBUILD .SRCINFO && git commit -m "postfach 0.1.0-1" && git push

## Neue Version
`pkgver` im PKGBUILD erhöhen (`pkgrel=1`), Tag setzen und pushen, Prüfsumme + `.SRCINFO` neu erzeugen, committen, pushen.
Dieselbe `postfach/__init__.py`-Version und `data/…metainfo.xml` (`<release>`) vorher anpassen.

## Hinweise
- Nutzer installieren mit `yay -S postfach` (oder jedem anderen AUR-Helfer); in GNOME Software erscheint das Paket nur, wenn dort
  AUR-Unterstützung aktiviert ist (z. B. über `pamac`).
- Das Paket installiert die Beschreibungsdatei (AppStream), den Starter, die Symbole und die Übersetzungen.
