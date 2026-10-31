"""Antworten, Allen antworten, Weiterleiten: Empfänger, Betreff und zitierter Text im Format der Originalnachricht."""
import html
import re
import time
from email.utils import getaddresses

from . import db, mailparse
from .i18n import _

QUOTE_STYLE = "margin:0 0 0 .8ex;border-left:2px solid #c0c0c0;padding-left:1ex"


def _addrs(value):
    return [(n, a) for n, a in getaddresses([value or ""]) if a]


def _fmt(name, addr):
    return f"{name} <{addr}>" if name and name != addr else addr


def _prefixed(subject, prefix, variants):
    s = (subject or "").strip()
    if re.match(rf"^(?:{'|'.join(variants)})\s*:", s, re.I):
        return s
    return f"{prefix} {s}".strip()


def reply_subject(subject):
    return _prefixed(subject, "Re:", ("re", "aw"))


def forward_subject(subject):
    return _prefixed(subject, "Fwd:", ("fwd", "fw", "wg"))


def sanitize_html(body):
    """Body-Inhalt einer HTML-Mail für den Editor: ohne Skripte, Styles, Event-Handler und Kopfbereich."""
    m = re.search(r"<body[^>]*>(.*)</body>", body, re.I | re.S)
    body = m.group(1) if m else body
    body = re.sub(r"<(script|style|head|title|iframe|object|embed)\b.*?</\1\s*>", "", body, flags=re.I | re.S)
    body = re.sub(r"<(meta|link|base)\b[^>]*>", "", body, flags=re.I)
    body = re.sub(r"\son\w+\s*=\s*(\"[^\"]*\"|'[^']*'|[^\s>]+)", "", body, flags=re.I)
    body = re.sub(r"(href|src)\s*=\s*([\"'])\s*javascript:[^\"']*\2", r"\1=\2#\2", body, flags=re.I)
    return body.strip()


def original_html(row, raw):
    """HTML-Inhalt der Originalnachricht mit eingebetteten Bildern (cid → data-URI)."""
    body = sanitize_html(row["body_html"])
    for cid, uri in (mailparse.inline_cids(raw) if raw else {}).items():
        body = body.replace(f"cid:{cid}", uri)
    return body


def _when(row):
    """Zeitpunkt für „Am … schrieb …“"""
    return time.strftime(_("%Y-%m-%d at %H:%M"), time.localtime(row["date"] or 0))


def _date_plain(row):
    return time.strftime(_("%Y-%m-%d %H:%M"), time.localtime(row["date"] or 0))


def _own(accounts):
    return {a["email"].lower() for a in accounts}


def build_reply(row, raw, reply_all=False):
    """-> dict(account_id, to, cc, subject, body_text, body_html|None, in_reply_to, references)"""
    h = mailparse.headers(raw or b"")
    own = _own(db.mail_accounts())
    sender = (row["from_name"], row["from_addr"])
    reply_to = _addrs(h.get("Reply-To")) or [sender]
    from_me = (row["from_addr"] or "").lower() in own
    to = _addrs(h.get("To")) if from_me else list(reply_to)
    cc = []
    if reply_all:
        to += [] if from_me else _addrs(h.get("To"))
        cc = _addrs(h.get("Cc"))
    seen, to_out, cc_out = set(), [], []
    for bucket, src in ((to_out, to), (cc_out, cc)):
        for n, a in src:
            k = a.lower()
            if k in seen or (k in own and not (from_me and src is to and k == (row["from_addr"] or "").lower() and len(src) == 1)):
                continue
            seen.add(k)
            bucket.append(_fmt(n, a))
    if not to_out and sender[1]:
        to_out = [_fmt(*sender)]
    who = row["from_name"] or row["from_addr"]
    head = _("On {when}, {who} wrote:").format(when=_when(row), who=who)
    out = {"account_id": row["account_id"], "to": ", ".join(to_out), "cc": ", ".join(cc_out),
           "subject": reply_subject(row["subject"]), "in_reply_to": h.get("Message-ID") or None,
           "references": h.get("References") or None, "body_html": None}
    if row["body_html"]:
        out["body_html"] = ('<div><br></div><div class="quote"><div>' + html.escape(head) + "</div>"
                            f'<blockquote style="{QUOTE_STYLE}">{original_html(row, raw)}</blockquote></div>')
        out["body_text"] = ""            # wird beim Senden aus dem HTML erzeugt
    else:
        lines = (row["body_text"] or "").splitlines()
        out["body_text"] = "\n\n" + head + "\n" + "\n".join(("> " + l) if l else ">" for l in lines) + "\n"
    return out


def build_forward(row, raw):
    """-> dict(account_id, subject, body_text, body_html|None, attachments)"""
    h = mailparse.headers(raw or b"")
    frm = _fmt(row["from_name"], row["from_addr"])
    fields = [(_("From"), frm), (_("Date"), _date_plain(row)), (_("Subject"), row["subject"] or ""),
              (_("To"), h.get("To") or row["to_addrs"] or "")]
    if h.get("Cc"):
        fields.append((_("Cc"), h["Cc"]))
    fwd = _("Forwarded message")
    out = {"account_id": row["account_id"], "subject": forward_subject(row["subject"]), "to": "", "cc": "",
           "in_reply_to": None, "references": None, "body_html": None,
           "attachments": mailparse.forwardable_attachments(raw) if raw else []}
    if row["body_html"]:
        block = "<br>".join(f"<b>{k}:</b> {html.escape(v)}" for k, v in fields)
        out["body_html"] = ("<div><br></div><div>---------- " + fwd + " ----------<br>" + block + "</div><br>"
                            f"<div>{original_html(row, raw)}</div>")
        out["body_text"] = ""
    else:
        out["body_text"] = ("\n\n---------- " + fwd + " ----------\n"
                            + "\n".join(f"{k}: {v}" for k, v in fields) + "\n\n" + (row["body_text"] or ""))
    return out


def text_to_html(text):
    """Nur-Text → HTML-Fragment für den Editor (Zeilenumbrüche erhalten)."""
    return "<div>" + html.escape(text or "").replace("\n", "<br>") + "</div>" if text else "<div><br></div>"


def apply_format_policy(kw):
    """Format der Nachricht nach den Einstellungen: HTML oder Nur-Text für Neues; Antworten/Weiterleiten wahlweise
    im Format der Originalmail (kw enthält dann „body_html“, ggf. None = Original war Text)."""
    from . import config
    want_html = bool(config.get("compose_html"))
    is_reply = "body_html" in kw                     # von build_reply/build_forward: Format des Originals vorgegeben
    if is_reply and config.get("reply_follow_original"):
        return kw
    body, body_html = kw.get("body", ""), kw.get("body_html")
    if want_html and body_html is None:               # Text → HTML (Zitatzeilen „>“ bleiben als Text erhalten)
        kw["body_html"], kw["body"] = text_to_html(body), ""
    elif not want_html and body_html is not None:     # HTML → Nur-Text (Zitat als „> “-Zeilen)
        kw["body"], kw["body_html"] = "\n\n" + mailparse.html_to_text(body_html), None
    elif not is_reply and want_html:
        kw["body_html"], kw["body"] = text_to_html(body), ""
    elif not is_reply:
        kw["body_html"] = None
    return kw
