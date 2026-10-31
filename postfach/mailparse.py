"""RFC822 -> Felder für den Cache; Anzeige-HTML; Anhänge."""
import base64
import email
import email.policy
import email.utils
import html
import re
import time
from html.parser import HTMLParser

from .i18n import _
from .invite import CAL_TYPES

POLICY = email.policy.default


class _Strip(HTMLParser):
    """HTML → Text; verschachtelte <blockquote> werden als „> “-Zitat wiedergegeben."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out, self._skip, self.depth = [], 0, 0

    def _nl(self):
        self.out.append("\n" + ("> " * self.depth))

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "head"):
            self._skip += 1
        elif tag == "blockquote":
            self.depth += 1
            self._nl()
        elif tag in ("br", "p", "div", "tr", "li", "h1", "h2", "h3"):
            self._nl()
            if tag == "li":
                self.out.append("• ")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "head"):
            self._skip = max(0, self._skip - 1)
        elif tag == "blockquote":
            self.depth = max(0, self.depth - 1)
            self._nl()

    def handle_data(self, d):
        if not self._skip:
            self.out.append(d.replace("\n", " ") if self.depth else d)


def html_to_text(s):
    p = _Strip()
    try:
        p.feed(s)
    except Exception:
        return re.sub(r"<[^>]+>", " ", s)
    text = "".join(p.out)
    text = re.sub(r"\n(?:[ \t>]*\n)+", "\n\n", text)      # mehrfache Leerzeilen (auch leere Zitatzeilen) zusammenfassen
    return text.strip()


def _body(msg):
    plain = msg.get_body(preferencelist=("plain",))
    htm = msg.get_body(preferencelist=("html",))
    t = h = ""
    try:
        t = plain.get_content() if plain else ""
    except Exception:
        t = ""
    try:
        h = htm.get_content() if htm else ""
    except Exception:
        h = ""
    return t, h


def _decode(v):
    return str(v) if v is not None else ""


def att_names(raw: bytes):
    """Kleingeschriebene Dateinamen + MIME-Typen der Anhänge (für file:-Suche)."""
    return " ".join(f"{n} {t}".lower() for _, n, t, _ in attachments(raw))


def parse(raw: bytes):
    """Gibt dict für die messages-Tabelle zurück (ohne account/folder/uid/Flags)."""
    msg = email.message_from_bytes(raw, policy=POLICY)
    text, htm = _body(msg)
    if not text and htm:
        text = html_to_text(htm)
    try:
        frm = email.utils.getaddresses([_decode(msg["From"])])
        name, addr = frm[0] if frm else ("", "")
    except Exception:
        name, addr = "", _decode(msg["From"])
    try:
        d = email.utils.parsedate_to_datetime(_decode(msg["Date"]))
        ts = int(d.timestamp())
    except Exception:
        ts = int(time.time())
    to = ", ".join(filter(None, (_decode(msg["To"]), _decode(msg["Cc"]))))
    has_att = any(p.get_content_disposition() == "attachment" for p in msg.walk())
    has_cal = any(p.get_content_type() in CAL_TYPES or (p.get_filename() or "").lower().endswith(".ics") for p in msg.walk())
    return {
        "message_id": _decode(msg["Message-ID"]).strip(),
        "subject": _decode(msg["Subject"]).strip() or "",
        "from_name": name or addr,
        "from_addr": addr,
        "to_addrs": to,
        "date": ts,
        "snippet": re.sub(r"\s+", " ", text)[:600].strip(),
        "body_text": text,
        "body_html": htm,
        "has_att": int(has_att),
        "has_cal": int(has_cal),
        "att_names": att_names(raw) if has_att else "",
    }


def headers(raw: bytes):
    msg = email.message_from_bytes(raw, policy=POLICY)
    return {k: _decode(msg[k]) for k in ("From", "To", "Cc", "Reply-To", "Subject", "Date", "Message-ID", "References")}


def attachments(raw: bytes):
    """Liste (index, filename, content_type, size)."""
    msg = email.message_from_bytes(raw, policy=POLICY)
    out = []
    for i, p in enumerate(msg.walk()):
        if p.get_content_disposition() == "attachment" or (p.get_filename() and not p.is_multipart()):
            payload = p.get_payload(decode=True) or b""
            out.append((i, p.get_filename() or f"anhang-{i}", p.get_content_type(), len(payload)))
    return out


def forwardable_attachments(raw: bytes):
    """Anhänge zum Weiterleiten: (Dateiname, MIME-Typ, Bytes); ohne eingebettete Bilder (Content-ID)."""
    msg = email.message_from_bytes(raw, policy=POLICY)
    out = []
    for p in msg.walk():
        if p.is_multipart() or not (p.get_content_disposition() == "attachment" or p.get_filename()):
            continue
        if p.get("Content-ID") and p.get_content_disposition() != "attachment":
            continue
        out.append((p.get_filename() or "anhang", p.get_content_type(), p.get_payload(decode=True) or b""))
    return out


def attachment_bytes(raw: bytes, index: int):
    msg = email.message_from_bytes(raw, policy=POLICY)
    for i, p in enumerate(msg.walk()):
        if i == index:
            return p.get_payload(decode=True) or b""
    return b""


def inline_cids(raw: bytes):
    msg = email.message_from_bytes(raw, policy=POLICY)
    out = {}
    for p in msg.walk():
        cid = p.get("Content-ID")
        if cid and p.get_content_maintype() == "image":
            data = p.get_payload(decode=True) or b""
            if len(data) < 2_000_000:
                out[cid.strip("<>")] = f"data:{p.get_content_type()};base64,{base64.b64encode(data).decode()}"
    return out


CSS = """
:root { color-scheme: light dark; }
body { font: 15px/1.5 -apple-system, "Cantarell", "Noto Sans", sans-serif; margin: 20px 24px; word-wrap: break-word; }
pre.plain { white-space: pre-wrap; font: inherit; margin: 0; }
a { color: #3584e4; } blockquote { margin: .5em 0 .5em .2em; padding-left: 1em; border-left: 3px solid #8885; }
img { max-width: 100%; height: auto; }
"""


# HTML-Mails setzen Farben meist selbst, aber selten einen Hintergrund: auf dunklem Untergrund wäre dunkler Text unlesbar.
# Deshalb liegen sie auf hellem Grund, außer sie kennen den Dunkelmodus selbst (prefers-color-scheme / color-scheme).
HTML_LIGHT_CSS = "html { background: #ffffff; color: #1c1c1c; color-scheme: light; } body { margin: 0; padding: 16px 20px; }"
_DARK_AWARE = re.compile(r"prefers-color-scheme|color-scheme", re.I)


def render_html(row, raw, allow_remote=False):
    """Fertiges HTML-Dokument für die WebView (JS ist ohnehin abgeschaltet)."""
    csp_img = "data: cid: https: http:" if allow_remote else "data:"
    csp = (f"default-src 'none'; img-src {csp_img}; style-src 'unsafe-inline'; font-src data:")
    meta = f'<meta http-equiv="Content-Security-Policy" content="{csp}"><meta charset="utf-8">'
    if row["body_html"]:
        body = row["body_html"]
        cids = inline_cids(raw) if raw else {}
        for cid, uri in cids.items():
            body = body.replace(f"cid:{cid}", uri)
        has_remote = bool(re.search(r'(src|background)\s*=\s*["\']https?:', body, re.I))
        extra = "" if _DARK_AWARE.search(body) else HTML_LIGHT_CSS
        return (f"<!doctype html><html><head>{meta}<style>{CSS}{extra}</style></head><body>{body}</body></html>",
                has_remote)
    text = html.escape(row["body_text"] or "")
    text = re.sub(r"(https?://[^\s<>&]+)", r'<a href="\1">\1</a>', text)
    return (f"<!doctype html><html><head>{meta}<style>{CSS}</style></head>"
            f'<body><pre class="plain">{text}</pre></body></html>'), False


def display_subject(subject):
    """Betreff für die Anzeige; leere (und früher gespeicherte „(kein Betreff)“) Betreffs durch den Platzhalter ersetzen."""
    s = (subject or "").strip()
    return _("(no subject)") if s in ("", "(kein Betreff)") else s
