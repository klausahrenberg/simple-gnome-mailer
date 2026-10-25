#!/bin/sh
# Kompiliert die Übersetzungen (po/*.po) nach postfach/locale/<sprache>/LC_MESSAGES/postfach.mo
cd "$(dirname "$0")/.." || exit 1
for po in po/*.po; do
  lang="$(basename "$po" .po)"
  mkdir -p "postfach/locale/$lang/LC_MESSAGES"
  msgfmt -o "postfach/locale/$lang/LC_MESSAGES/postfach.mo" "$po" || exit 1
done
echo "Übersetzungen kompiliert: $(ls po/*.po | wc -l)"
