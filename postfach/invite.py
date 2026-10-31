"""Termineinladungen (iCalendar, RFC 5545/5546) in E-Mails: erkennen, lesen, beantworten (METHOD:REPLY)."""
import email
import email.policy
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None

from .i18n import N_, _, ngettext

CAL_TYPES = ("text/calendar", "application/ics", "application/calendar")

# Windows-Zeitzonennamen (Outlook/Exchange) -> IANA
WINDOWS_TZ = {
    "UTC": "UTC", "Greenwich Standard Time": "Atlantic/Reykjavik", "GMT Standard Time": "Europe/London",
    "W. Europe Standard Time": "Europe/Berlin", "Central Europe Standard Time": "Europe/Budapest",
    "Central European Standard Time": "Europe/Warsaw", "Romance Standard Time": "Europe/Paris",
    "E. Europe Standard Time": "Europe/Chisinau", "FLE Standard Time": "Europe/Kiev", "GTB Standard Time": "Europe/Bucharest",
    "Russian Standard Time": "Europe/Moscow", "Turkey Standard Time": "Europe/Istanbul",
    "Eastern Standard Time": "America/New_York", "Central Standard Time": "America/Chicago",
    "Mountain Standard Time": "America/Denver", "Pacific Standard Time": "America/Los_Angeles",
    "US Mountain Standard Time": "America/Phoenix", "Alaskan Standard Time": "America/Anchorage",
    "Atlantic Standard Time": "America/Halifax", "E. South America Standard Time": "America/Sao_Paulo",
    "India Standard Time": "Asia/Kolkata", "China Standard Time": "Asia/Shanghai", "Tokyo Standard Time": "Asia/Tokyo",
    "Korea Standard Time": "Asia/Seoul", "Singapore Standard Time": "Asia/Singapore", "Arab Standard Time": "Asia/Riyadh",
    "Israel Standard Time": "Asia/Jerusalem", "South Africa Standard Time": "Africa/Johannesburg",
    "AUS Eastern Standard Time": "Australia/Sydney", "New Zealand Standard Time": "Pacific/Auckland",
}

# Namen für die Anzeige (übersetzbar; unabhängig von der Prozess-Locale)
WEEKDAYS = [N_("Monday"), N_("Tuesday"), N_("Wednesday"), N_("Thursday"), N_("Friday"), N_("Saturday"), N_("Sunday")]
MONTHS = [N_("January"), N_("February"), N_("March"), N_("April"), N_("May"), N_("June"), N_("July"), N_("August"),
          N_("September"), N_("October"), N_("November"), N_("December")]


@dataclass
class Person:
    name: str = ""
    email: str = ""
    partstat: str = ""
    role: str = ""
    line: str = ""      # Originalzeile (für Antworten)

    @property
    def display(self):
        return self.name or self.email


@dataclass
class Invite:
    method: str = "PUBLISH"
    uid: str = ""
    summary: str = ""
    location: str = ""
    description: str = ""
    status: str = ""
    sequence: int = 0
    start: object = None            # datetime (mit Zeitzone) oder date (ganztägig)
    end: object = None
    all_day: bool = False
    rrule: str = ""
    recurrence_id: str = ""
    organizer: Person = field(default_factory=Person)
    attendees: list = field(default_factory=list)
    url: str = ""
    raw_lines: dict = field(default_factory=dict)   # DTSTART/DTEND/RECURRENCE-ID Originalzeilen
    source: str = ""                                 # vollständiger ICS-Text


# ---------------------------------------------------------------- Lesen
def extract(raw: bytes):
    """ICS-Texte aus text/calendar-Teilen und .ics-Anhängen der Nachricht."""
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    out = []
    for part in msg.walk():
        name = (part.get_filename() or "").lower()
        if part.is_multipart():
            continue
        if part.get_content_type() in CAL_TYPES or name.endswith(".ics"):
            try:
                payload = part.get_payload(decode=True) or b""
                charset = part.get_content_charset() or "utf-8"
                text = payload.decode(charset, "replace")
            except Exception:
                continue
            if "BEGIN:VCALENDAR" in text.upper():
                out.append(text)
    return out


def has_calendar(raw: bytes):
    return bool(extract(raw))


def _unfold(text):
    lines = []
    for l in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if l[:1] in (" ", "\t") and lines:
            lines[-1] += l[1:]
        else:
            lines.append(l)
    return lines


def _unescape(v):
    return re.sub(r"\\([nN,;\\])", lambda m: "\n" if m.group(1) in "nN" else m.group(1), v)


def _prop(line):
    """'ATTENDEE;CN="A, B";PARTSTAT=X:mailto:a@b' -> (NAME, {PARAM: wert}, wert)"""
    m = re.match(r"^([A-Za-z0-9-]+)((?:;[^:;=]+=(?:\"[^\"]*\"|[^;:]*))*):(.*)$", line)
    if not m:
        return None
    params = {}
    for pm in re.finditer(r';([^:;=]+)=("[^"]*"|[^;:]*)', m.group(2)):
        params[pm.group(1).upper()] = pm.group(2).strip('"')
    return m.group(1).upper(), params, m.group(3)


def _tz(name):
    if not name or ZoneInfo is None:
        return None
    for cand in (name, WINDOWS_TZ.get(name)):
        if cand:
            try:
                return ZoneInfo(cand)
            except Exception:
                pass
    return None


def _datetime(value, params):
    """-> (datetime|date, ganztägig)"""
    value = value.strip()
    if params.get("VALUE") == "DATE" or re.fullmatch(r"\d{8}", value):
        return date(int(value[:4]), int(value[4:6]), int(value[6:8])), True
    m = re.fullmatch(r"(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})(Z?)", value)
    if not m:
        return None, False
    y, mo, d, h, mi, s = (int(x) for x in m.groups()[:6])
    if m.group(7):
        tz = timezone.utc
    else:
        tz = _tz(params.get("TZID", "")) or datetime.now().astimezone().tzinfo   # unbekannte Zone: lokale Zeit
    return datetime(y, mo, d, h, mi, s, tzinfo=tz), False


def _cn(params):
    """Anzeigename ohne umschließende Anführungszeichen (Outlook liefert z. B. 'name@host')."""
    return params.get("CN", "").strip().strip("'\" ").strip()


def _mailto(v):
    v = v.strip()
    return v[7:] if v.lower().startswith("mailto:") else v


def parse(text):
    """Erstes VEVENT eines ICS-Textes -> Invite (oder None)."""
    lines = _unfold(text)
    inv = Invite(source=text)
    in_event, nested = False, 0
    for line in lines:
        up = line.upper()
        if up == "BEGIN:VEVENT" and not in_event:
            in_event = True
            continue
        if in_event and up.startswith("BEGIN:"):      # verschachtelte Komponenten (VALARM) überspringen
            nested += 1
            continue
        if in_event and up.startswith("END:") and nested:
            nested -= 1
            continue
        if up == "END:VEVENT" and in_event:
            break
        if nested:
            continue
        p = _prop(line)
        if not p:
            continue
        name, params, value = p
        if not in_event:
            if name == "METHOD":
                inv.method = value.strip().upper()
            continue
        if name == "UID":
            inv.uid = value.strip()
        elif name == "SUMMARY":
            inv.summary = _unescape(value)
        elif name == "LOCATION":
            inv.location = _unescape(value)
        elif name == "DESCRIPTION":
            inv.description = _unescape(value)
        elif name == "STATUS":
            inv.status = value.strip().upper()
        elif name == "SEQUENCE":
            inv.sequence = int(value) if value.strip().isdigit() else 0
        elif name == "RRULE":
            inv.rrule = value
        elif name == "URL":
            inv.url = value
        elif name == "RECURRENCE-ID":
            inv.recurrence_id = value
            inv.raw_lines["RECURRENCE-ID"] = line
        elif name == "DTSTART":
            inv.start, inv.all_day = _datetime(value, params)
            inv.raw_lines["DTSTART"] = line
        elif name == "DTEND":
            inv.end, _ad = _datetime(value, params)
            inv.raw_lines["DTEND"] = line
        elif name == "DURATION" and inv.end is None and inv.start is not None:
            m = re.fullmatch(r"P(?:(\d+)W)?(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?", value.strip())
            if m:
                w, d, h, mi, s = (int(x or 0) for x in m.groups())
                inv.end = inv.start + timedelta(weeks=w, days=d, hours=h, minutes=mi, seconds=s)
        elif name == "ORGANIZER":
            inv.organizer = Person(_cn(params), _mailto(value), "", "", line)
        elif name == "ATTENDEE":
            inv.attendees.append(Person(_cn(params), _mailto(value), params.get("PARTSTAT", "NEEDS-ACTION").upper(),
                                        params.get("ROLE", "").upper(), line))
    if inv.start is None:
        return None
    if inv.end is None:
        inv.end = inv.start + (timedelta(days=1) if inv.all_day else timedelta(0))
    return inv


def find(raw: bytes):
    """Erste verwertbare Einladung der Nachricht oder None."""
    for text in extract(raw):
        inv = parse(text)
        if inv:
            return inv
    return None


# ---------------------------------------------------------------- Anzeige
def _local(dt):
    return dt.astimezone() if isinstance(dt, datetime) else dt


def _day(d):
    return _("{weekday}, {month} {day}, {year}").format(
        weekday=_(WEEKDAYS[d.weekday()]), month=_(MONTHS[d.month - 1]), day=d.day, year=d.year)


def when_text(inv):
    """Zeitangabe in lokaler Zeit, z. B. „Dienstag, 27. Oktober 2026, 10:00–11:00“."""
    s, e = _local(inv.start), _local(inv.end)
    if inv.all_day:
        last = e - timedelta(days=1) if e and e > s else s
        return _day(s) if last == s else f"{_day(s)} – {_day(last)}"
    if e.date() == s.date():
        return f"{_day(s)}, {s:%H:%M}–{e:%H:%M}" if e != s else f"{_day(s)}, {s:%H:%M}"
    return f"{_day(s)}, {s:%H:%M} – {_day(e)}, {e:%H:%M}"


def repeat_text(inv):
    """Kurze Beschreibung der Wiederholung (RRULE) oder ''."""
    if not inv.rrule:
        return ""
    freq = re.search(r"FREQ=(\w+)", inv.rrule)
    interval = re.search(r"INTERVAL=(\d+)", inv.rrule)
    f = freq.group(1) if freq else ""
    n = int(interval.group(1)) if interval else 1
    names = {"DAILY": _("daily"), "WEEKLY": _("weekly"), "MONTHLY": _("monthly"), "YEARLY": _("yearly")}
    if f not in names:
        return _("Repeats")
    if n == 1:
        return _("Repeats {freq}").format(freq=names[f])
    units = {"DAILY": ngettext("day", "days", n), "WEEKLY": ngettext("week", "weeks", n),
             "MONTHLY": ngettext("month", "months", n), "YEARLY": ngettext("year", "years", n)}
    return _("Repeats every {n} {unit}").format(n=n, unit=units[f])


# ---------------------------------------------------------------- Antworten
PARTSTAT = {"accept": "ACCEPTED", "tentative": "TENTATIVE", "decline": "DECLINED"}


def _esc(v):
    return v.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def my_attendee(inv, my_addresses):
    """Der in der Einladung genannte Teilnehmer, der einer eigenen Adresse entspricht (oder None)."""
    mine = {a.lower() for a in my_addresses}
    return next((a for a in inv.attendees if a.email.lower() in mine), None)


def build_reply(inv, action, my_email, my_name="", comment=""):
    """METHOD:REPLY (ICS-Text) für accept | tentative | decline."""
    now = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    cn = f";CN={_esc(my_name)}" if my_name else ""
    lines = ["BEGIN:VCALENDAR", "PRODID:-//Postfach//Mail//EN", "VERSION:2.0", "CALSCALE:GREGORIAN", "METHOD:REPLY",
             "BEGIN:VEVENT", f"UID:{inv.uid}", f"DTSTAMP:{now}", f"SEQUENCE:{inv.sequence}"]
    for key in ("DTSTART", "DTEND", "RECURRENCE-ID"):
        if inv.raw_lines.get(key):
            lines.append(inv.raw_lines[key])
    if inv.summary:
        lines.append(f"SUMMARY:{_esc(inv.summary)}")
    if inv.organizer.email:
        lines.append(inv.organizer.line or f"ORGANIZER:mailto:{inv.organizer.email}")
    lines.append(f"ATTENDEE;PARTSTAT={PARTSTAT[action]}{cn}:mailto:{my_email}")
    if comment.strip():
        lines.append(f"COMMENT:{_esc(comment.strip())}")        # Nachricht an den Organisator
    lines += ["END:VEVENT", "END:VCALENDAR"]
    return "\r\n".join(_fold(l) for l in lines) + "\r\n"


def _fold(line):
    out, b = [], line.encode("utf-8")
    while len(b) > 74:
        cut = 74
        while cut > 0 and (b[cut] & 0xC0) == 0x80:     # nicht mitten in einem UTF-8-Zeichen trennen
            cut -= 1
        out.append(b[:cut].decode("utf-8"))
        b = b" " + b[cut:]
    out.append(b.decode("utf-8"))
    return "\r\n".join(out)


def reply_subject(inv, action):
    prefix = {"accept": _("Accepted"), "tentative": _("Tentative"), "decline": _("Declined")}[action]
    return f"{prefix}: {inv.summary}".strip()


def reply_body(inv, action, comment=""):
    text = {"accept": _("I will attend: {summary}"), "tentative": _("I might attend: {summary}"),
            "decline": _("I cannot attend: {summary}")}[action].format(summary=inv.summary) + "\n"
    return (comment.strip() + "\n\n" + text) if comment.strip() else text


def importable(inv, tentative=False):
    """ICS-Text zum Hinzufügen in den Kalender: METHOD auf PUBLISH, damit Kalender-Apps ihn als Termin importieren."""
    text = inv.source.replace("\r\n", "\n")
    if re.search(r"^METHOD:", text, re.M | re.I):
        text = re.sub(r"^METHOD:.*$", "METHOD:PUBLISH", text, flags=re.M | re.I)
    else:
        text = text.replace("BEGIN:VCALENDAR\n", "BEGIN:VCALENDAR\nMETHOD:PUBLISH\n", 1)
    if tentative:                                   # „Vielleicht“: im Kalender als vorläufig eintragen
        if re.search(r"^STATUS:", text, re.M):
            text = re.sub(r"^STATUS:.*$", "STATUS:TENTATIVE", text, count=1, flags=re.M)
        else:
            text = text.replace("END:VEVENT", "STATUS:TENTATIVE\nEND:VEVENT", 1)
    return text.replace("\n", "\r\n")
