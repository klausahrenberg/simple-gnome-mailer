#!/bin/sh
# Beendet die laufende Postfach-Instanz (falls vorhanden) sauber und startet sie neu.
cd "$(dirname "$0")/.."
NAME=eu.ahrenberg.mail
owned() { gdbus call --session --dest org.freedesktop.DBus --object-path /org/freedesktop/DBus \
          --method org.freedesktop.DBus.NameHasOwner "$NAME" 2>/dev/null; }
if [ "$(owned)" = "(true,)" ]; then
  gdbus call --session --dest "$NAME" --object-path /eu/ahrenberg/mail \
    --method org.gtk.Actions.Activate quit-app '[]' '{}' >/dev/null 2>&1
  for i in 1 2 3 4 5 6 7 8 9 10; do sleep 1; [ "$(owned)" = "(false,)" ] && break; done
fi
setsid nohup ./run.sh >/tmp/postfach.log 2>&1 < /dev/null &
sleep 3
[ "$(owned)" = "(true,)" ] && echo "Postfach läuft" || { echo "Start fehlgeschlagen:"; tail -5 /tmp/postfach.log; exit 1; }
