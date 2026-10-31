"""Füllt eine Demo-Datenbank mit frei erfundenen Nachrichten (für Screenshots/Entwicklung).
Aufruf mit gesetzten POSTFACH_DATA_DIR/XDG_CONFIG_HOME auf Temp-Ordner und POSTFACH_DEMO=1 beim Starten der App."""
import sys
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import format_datetime

sys.path.insert(0, __file__.rsplit("/tools/", 1)[0])
from postfach import db, mailparse  # noqa: E402

c = db.conn()
a1 = db.add_account(name="Anna", email="anna.beispiel@example.org", kind="imap", imap_host="imap.example.org",
                    smtp_host="smtp.example.org", username="anna", color=1)
a2 = db.add_account(name="Anna", email="anna@lenz-studio.example", kind="imap", imap_host="imap.lenz-studio.example",
                    smtp_host="smtp.lenz-studio.example", username="anna", color=2)
for a in (a1, a2):
    for name, role, title in (("INBOX", "inbox", None), ("Sent", "sent", None), ("Drafts", "drafts", None),
                              ("Archive", "archive", None), ("Trash", "trash", None), ("Junk", "junk", None)):
        c.execute("INSERT INTO folders(account_id,name,role,delim,title) VALUES (?,?,?,'/',?)", (a, name, role, title))
c.execute("INSERT INTO folders(account_id,name,role,delim) VALUES (?,?,?,'/')", (a1, "Projekte", "other"))
c.execute("INSERT INTO folders(account_id,name,role,delim) VALUES (?,?,?,'/')", (a1, "Projekte/Umzug", "other"))

HTML_NEWS = ("<div style='font-family:Arial;color:#333;max-width:520px'><h2 style='color:#26a269'>Ihre Bestellung ist unterwegs</h2>"
             "<p>Hallo Anna,<br>Ihr Paket mit <b>3 Artikeln</b> wurde heute versendet und kommt voraussichtlich am "
             "<b>Donnerstag</b> an.</p><p><a href='https://example.org/sendung'>Sendung verfolgen</a></p>"
             "<p style='color:#888;font-size:12px'>Dies ist eine automatisch erzeugte Nachricht.</p></div>")
now = datetime.now(timezone.utc)
# (Konto, Absender, Adresse, Betreff, Text, Stunden zurück, gelesen, markiert, Anhang, HTML)
M = [
    (a1, "Lena Hoffmann", "lena@example.net", "Wochenend-Wanderung am Samstag?", "Hallo Anna, ich plane am Samstag eine Tour auf den Hausberg – Start um 9 Uhr am Parkplatz. Hast du Lust mitzukommen? Ich bringe Kaffee mit. Viele Grüße, Lena", 1, 0, 0, None, None),
    (a2, "Paketdienst Express", "no-reply@paketexpress.example", "Ihre Sendung ist unterwegs", "Ihr Paket wurde versendet.", 3, 0, 0, None, HTML_NEWS),
    (a1, "Markus Weber", "markus.weber@example.com", "Rechnung Oktober", "Guten Tag Anna, im Anhang finden Sie die Rechnung für den Monat Oktober. Bitte überweisen Sie den Betrag bis zum 15. des Monats. Freundliche Grüße, Markus Weber", 5, 0, 1, ("Rechnung-Oktober.pdf", "pdf"), None),
    (a2, "Studio Lenz Team", "team@lenz-studio.example", "Einladung: Planungsmeeting Dienstag 10 Uhr", "Liebe Anna, wir treffen uns am Dienstag um 10 Uhr zum Planungsmeeting für das neue Projekt. Bitte bring deine Skizzen mit. Bis dann! Das Team", 9, 1, 0, None, None),
    (a1, "Stadtbibliothek", "service@stadtbibliothek.example", "Erinnerung: Rückgabe in 3 Tagen", "Die folgenden Medien sind bald fällig. Sie können die Leihfrist online verlängern.", 20, 1, 0, None, None),
    (a1, "Jonas Keller", "jonas@example.org", "Re: Fotos vom Ausflug", "Danke für die Bilder, die sind richtig gut geworden! Das mit dem Sonnenuntergang schicke ich dir nachher in voller Auflösung. Jonas", 27, 1, 1, None, None),
    (a2, "Café Sonnenhof", "info@cafe-sonnenhof.example", "Dein Gutschein wartet", "Zeig diese Nachricht bis Ende des Monats und erhalte ein Stück Kuchen gratis zu jedem Kaffee.", 30, 1, 0, None, None),
    (a1, "Sabine Roth", "sabine.roth@example.net", "Umzug: Kartons und Transporter", "Hi Anna, den Transporter habe ich für den 14. reserviert. Kartons bringe ich am Wochenende vorbei. Sag Bescheid, falls du noch Hilfe beim Tragen brauchst. Sabine", 44, 1, 0, None, None),
    (a2, "Nina Brandt", "nina@lenz-studio.example", "Entwurf Logo – Rückmeldung", "Ich habe die Farben angepasst und die Schrift etwas größer gesetzt. Schau bitte, ob dir die zweite Variante besser gefällt. Nina", 52, 1, 0, ("Logo-Entwurf-v2.png", "png"), None),
    (a1, "Newsletter Gartenfreunde", "newsletter@gartenfreunde.example", "Was im November noch gepflanzt werden kann", "Zwiebelblumen, Knoblauch und winterharte Salate: Unsere Tipps für den Spätherbst im Überblick.", 70, 1, 0, None, None),
    (a1, "Tobias Lang", "tobias.lang@example.com", "Kurze Frage zum Mietvertrag", "Hallo Anna, ist im Vertrag die Kaution schon eingerechnet? Ich finde die Stelle gerade nicht. Danke dir! Tobias", 95, 1, 0, None, None),
    (a2, "Buchhaltung", "buchhaltung@lenz-studio.example", "Spesenabrechnung September", "Deine Spesenabrechnung wurde freigegeben und wird mit dem nächsten Gehalt ausgezahlt.", 120, 1, 0, None, None),
]
for i, (acc, name, addr, subj, body, hours, seen, flag, att, html) in enumerate(M, 1):
    m = EmailMessage()
    m["From"], m["To"], m["Subject"] = f"{name} <{addr}>", c.execute("select email from accounts where id=?", (acc,)).fetchone()[0], subj
    m["Date"], m["Message-ID"] = format_datetime(now - timedelta(hours=hours)), f"<demo-{i}@example.org>"
    m.set_content(body)
    if html:
        m.add_alternative(html, subtype="html")
    if "Planungsmeeting" in subj:          # Termineinladung (iCalendar) wie von Kalenderprogrammen verschickt
        start = (now + timedelta(days=5)).replace(hour=9, minute=0, second=0, microsecond=0)
        fmt = lambda d: d.strftime("%Y%m%dT%H%M%SZ")
        m.add_alternative(
            "BEGIN:VCALENDAR\r\nPRODID:-//Beispiel//Kalender//DE\r\nVERSION:2.0\r\nMETHOD:REQUEST\r\nBEGIN:VEVENT\r\n"
            f"UID:demo-planung-1@lenz-studio.example\r\nDTSTAMP:{fmt(now)}\r\nDTSTART:{fmt(start)}\r\nDTEND:{fmt(start + timedelta(hours=1))}\r\n"
            "SUMMARY:Planungsmeeting neues Projekt\r\nLOCATION:Besprechungsraum 2\r\n"
            "DESCRIPTION:Bitte Skizzen mitbringen.\\nAgenda: Ziele\\, Zeitplan\\, Aufgaben\r\n"
            "ORGANIZER;CN=Studio Lenz Team:mailto:team@lenz-studio.example\r\n"
            "ATTENDEE;CN=Anna;PARTSTAT=NEEDS-ACTION;RSVP=TRUE:mailto:anna@lenz-studio.example\r\n"
            "ATTENDEE;CN=Nina Brandt;PARTSTAT=ACCEPTED:mailto:nina@lenz-studio.example\r\n"
            "END:VEVENT\r\nEND:VCALENDAR\r\n", subtype="calendar", params={"method": "REQUEST"})
    if att:
        data = b"%PDF-1.4 demo" if att[1] == "pdf" else b"\x89PNG demo"
        m.add_attachment(data, maintype="application" if att[1] == "pdf" else "image", subtype=att[1], filename=att[0])
    raw = m.as_bytes()
    p = mailparse.parse(raw)
    p.update(account_id=acc, folder="INBOX", uid=i, seen=seen, flagged=flag)
    db.insert_message(p, raw)
# ein paar gesendete Nachrichten
for j, (name, addr, subj) in enumerate([("Lena Hoffmann", "lena@example.net", "Re: Wochenend-Wanderung"), ("Markus Weber", "markus.weber@example.com", "Re: Rechnung Oktober")], 100):
    m = EmailMessage()
    m["From"], m["To"], m["Subject"] = "Anna <anna.beispiel@example.org>", f"{name} <{addr}>", subj
    m["Date"], m["Message-ID"] = format_datetime(now - timedelta(hours=2 + j - 100)), f"<demo-{j}@example.org>"
    m.set_content("Danke, das klingt gut – ich bin dabei!")
    raw = m.as_bytes()
    p = mailparse.parse(raw)
    p.update(account_id=a1, folder="Sent", uid=j, seen=1, flagged=0)
    db.insert_message(p, raw)
c.commit()
print("seeded", c.execute("select count(*) from messages").fetchone()[0])
