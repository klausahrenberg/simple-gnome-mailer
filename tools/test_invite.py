"""Test: Termineinladungen erkennen, lesen und beantworten (ohne Netz)."""
import os
import sys
import tempfile

os.environ["POSTFACH_DATA_DIR"] = os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp()
sys.path.insert(0, __file__.rsplit("/tools/", 1)[0])
from email.message import EmailMessage

from postfach import invite

OUTLOOK = """BEGIN:VCALENDAR\r
METHOD:REQUEST\r
PRODID:Microsoft Exchange Server 2010\r
VERSION:2.0\r
BEGIN:VEVENT\r
ORGANIZER;CN="Mustermann, Max":MAILTO:max@firma.example\r
ATTENDEE;ROLE=REQ-PARTICIPANT;PARTSTAT=NEEDS-ACTION;RSVP=TRUE;CN=Anna Beispiel:MAILTO:anna@example.org\r
ATTENDEE;ROLE=OPT-PARTICIPANT;PARTSTAT=ACCEPTED;CN=Lena:MAILTO:lena@example.net\r
DESCRIPTION;LANGUAGE=de-DE:Agenda:\\n1. Start\\, Ziele\\n2. Ende\r
SUMMARY;LANGUAGE=de-DE:Projekt-Kickoff\r
DTSTART;TZID=W. Europe Standard Time:20261103T100000\r
DTEND;TZID=W. Europe Standard Time:20261103T113000\r
UID:040000008200E00074C5B7101A82E008\r
LOCATION:Raum 4.12 / Teams\r
SEQUENCE:2\r
RRULE:FREQ=WEEKLY;INTERVAL=1\r
BEGIN:VALARM\r
DESCRIPTION:Erinnerung\r
TRIGGER:-PT15M\r
ACTION:DISPLAY\r
END:VALARM\r
END:VEVENT\r
END:VCALENDAR\r
"""
GOOGLE = """BEGIN:VCALENDAR
PRODID:-//Google Inc//Google Calendar 70.9054//EN
VERSION:2.0
METHOD:REQUEST
BEGIN:VEVENT
DTSTART:20261104T150000Z
DTEND:20261104T160000Z
DTSTAMP:20261026T080000Z
ORGANIZER;CN=Chef:mailto:chef@example.com
UID:abc123@google.com
ATTENDEE;CUTYPE=INDIVIDUAL;ROLE=REQ-PARTICIPANT;PARTSTAT=NEEDS-ACTION;RSVP=TRUE;CN=anna@example.org;X-NUM-GUESTS=0:mailto:anna@example.org
SUMMARY:Sync mit einer sehr langen Beschreibung, die über die Zeilengrenze hinausgeht und gefaltet wurde ä ö
 ü
STATUS:CONFIRMED
END:VEVENT
END:VCALENDAR
"""
ALLDAY = "BEGIN:VCALENDAR\nMETHOD:PUBLISH\nBEGIN:VEVENT\nUID:x1\nSUMMARY:Feiertag\nDTSTART;VALUE=DATE:20261225\nDTEND;VALUE=DATE:20261227\nEND:VEVENT\nEND:VCALENDAR\n"


def mail(ics, how):
    m = EmailMessage()
    m["From"], m["To"], m["Subject"] = "Max <max@firma.example>", "anna@example.org", "Einladung"
    m.set_content("Bitte antworten.")
    if how == "alt":
        m.add_alternative(ics, subtype="calendar", params={"method": "REQUEST"})
    else:
        m.add_attachment(ics.encode(), maintype="application", subtype="ics", filename="invite.ics")
    return m.as_bytes()


i = invite.find(mail(OUTLOOK, "alt"))
assert i and i.method == "REQUEST" and i.summary == "Projekt-Kickoff", i
assert i.organizer.name == "Mustermann, Max" and i.organizer.email == "max@firma.example", i.organizer
assert [a.email for a in i.attendees] == ["anna@example.org", "lena@example.net"] and i.attendees[1].partstat == "ACCEPTED"
assert i.location == "Raum 4.12 / Teams" and i.description.startswith("Agenda:\n1. Start, Ziele") and "Erinnerung" not in i.description, i.description
assert i.start.utcoffset().total_seconds() in (3600, 7200) and i.start.hour == 10 and not i.all_day, i.start   # Berlin
assert i.sequence == 2 and invite.repeat_text(i), i.rrule
print("Outlook:", invite.when_text(i), "|", invite.repeat_text(i))

g = invite.find(mail(GOOGLE, "att"))
assert g and g.summary.endswith("ä ö ü") or g.summary.endswith("ä ö ü".replace(" ü", "ü")), g.summary
assert g.start.utcoffset().total_seconds() == 0 and g.uid == "abc123@google.com", g.start
assert invite.my_attendee(g, ["Anna@Example.org"]).email == "anna@example.org"
print("Google (als .ics-Anhang):", invite.when_text(g))

a = invite.find(mail(ALLDAY, "alt"))
assert a.all_day and a.method == "PUBLISH" and str(a.start) == "2026-12-25", a
print("Ganztägig:", invite.when_text(a))

assert invite.find(mail("BEGIN:VCALENDAR\nEND:VCALENDAR\n", "alt")) is None
m = EmailMessage(); m["Subject"] = "Normal"; m.set_content("kein Termin")
assert invite.find(m.as_bytes()) is None

r = invite.build_reply(i, "accept", "anna@example.org", "Anna Beispiel")
assert "METHOD:REPLY" in r and "PARTSTAT=ACCEPTED" in r and "UID:040000008200E00074C5B7101A82E008" in r and "ORGANIZER" in r
assert "DTSTART;TZID=W. Europe Standard Time:20261103T100000" in r and "\r\n" in r and "VALARM" not in r
assert all(len(l.encode()) <= 75 for l in r.split("\r\n")), "Zeilen müssen auf 75 Byte gefaltet sein"
back = invite.parse(r)       # die eigene Antwort muss sich wieder lesen lassen
assert back.method == "REPLY" and back.attendees[0].partstat == "ACCEPTED" and back.attendees[0].email == "anna@example.org"
r2 = invite.build_reply(i, "decline", "anna@example.org", "Anna", "Leider verhindert,\nbitte ohne mich.")
assert "PARTSTAT=DECLINED" in r2 and "COMMENT:Leider verhindert\\,\\nbitte ohne mich." in r2, r2
assert invite.reply_body(i, "decline", "Leider verhindert").startswith("Leider verhindert\n\n")
imp = invite.importable(i)
assert "METHOD:PUBLISH" in imp and "METHOD:REQUEST" not in imp
tent = invite.importable(i, tentative=True)
assert "STATUS:TENTATIVE" in tent and tent.count("STATUS:") == 1
conf = invite.importable(invite.find(mail(GOOGLE, "att")), tentative=True)      # vorhandenes STATUS:CONFIRMED wird ersetzt
assert "STATUS:TENTATIVE" in conf and "CONFIRMED" not in conf
print("Antwort/Import OK:", invite.reply_subject(i, "decline"))
print("OK – Einladungen: Outlook/Google/ganztägig, Antwort, Import")
