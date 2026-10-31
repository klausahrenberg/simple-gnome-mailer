"""Ausstehende Serveraktionen (löschen, Spam, Archiv, Flags) abarbeiten – mit Offline-Erkennung.

Die Oberfläche wirkt sofort lokal und legt die Serveraktion mit db.queue_op() ab. Hier wird sie ausgeführt,
sobald das Konto erreichbar ist; schlägt die Verbindung fehl, bleibt die Aktion erhalten und wird später wiederholt."""
import imaplib
import socket

from . import db, graph, imapsync

MAX_TRIES = 3


def is_network_error(e):
    """Verbindungsprobleme (offline, Server nicht erreichbar, Zeitüberschreitung) – im Gegensatz zu Serverfehlern."""
    if isinstance(e, graph.GraphError):
        return e.code is None or e.code >= 500 or e.code == 429
    try:
        from gi.repository import GLib
        if isinstance(e, GLib.Error):          # GNOME-Online-Konten: Token-Abruf ohne Netz
            return True
    except Exception:
        pass
    return isinstance(e, (OSError, EOFError, socket.timeout, imaplib.IMAP4.abort))


def _run(kind, arg, pks):
    if kind == "flag":
        flag, on = arg.split(":")
        imapsync.remote_set_flag(pks, flag, on == "1")
    else:
        {"delete": imapsync.remote_delete, "spam": imapsync.remote_mark_spam, "notspam": imapsync.remote_not_spam,
         "archive": imapsync.remote_move_archive}[kind](pks)


def process():
    """Führt alle ausstehenden Aktionen aus. -> (offline, Fehlertext oder None).
    offline=True: Verbindung fehlgeschlagen, die übrigen Aktionen bleiben in der Warteschlange."""
    groups, error = {}, None
    for op in db.pending_ops():
        m = db.message(op["pk"])
        if not m:                                  # Nachricht gibt es nicht mehr (z. B. vom Server gelöscht)
            db.drop_ops([op["id"]])
            continue
        groups.setdefault((op["kind"], op["arg"], m["account_id"]), []).append(op)
    for (kind, arg, _acc), ops in groups.items():
        pks = [o["pk"] for o in ops]
        try:
            _run(kind, arg, pks)
        except Exception as e:
            if is_network_error(e):
                return True, None
            c = db.conn()                          # Serverfehler: begrenzt wiederholen, dann aufgeben
            c.executemany("UPDATE pending_ops SET tries=tries+1 WHERE id=?", [(o["id"],) for o in ops])
            c.commit()
            for o in ops:
                if o["tries"] + 1 >= MAX_TRIES:
                    db.drop_ops([o["id"]])
                    if kind in db.REMOVE_KINDS:
                        db.unhide(o["pk"])        # Aktion nicht möglich: Nachricht wieder anzeigen
                    error = str(e)[:200]
            continue
        if kind in db.REMOVE_KINDS:
            for pk in pks:
                db.delete_message(pk)
        db.drop_ops([o["id"] for o in ops])
    return False, error
