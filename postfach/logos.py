"""Logos für Nicht-Personen-Absender (noreply@, info@, Marken …) von der eigenen Webseite der Absender-Domain.

Es wird nie ein Drittanbieter-Dienst gefragt: nur https://<domain>/ (bzw. die Hauptdomain) wird abgerufen. Ergebnisse
werden im Cache-Ordner gehalten (auch „kein Logo gefunden“, 14 Tage), damit pro Domain nur einmal angefragt wird."""
import hashlib
import ipaddress
import os
import re
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

import cairo
import gi

gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, GdkPixbuf, Gio, GLib

from . import config

CACHE = os.path.join(GLib.get_user_cache_dir(), "postfach", "logos")
RETRY_AFTER = 14 * 86400
SIZE = 96
MAX_BYTES = 1_500_000

GENERIC_LOCAL = re.compile(
    r"^(no[-_.]?reply|do[-_.]?not[-_.]?reply|donotreply|noreply|info|information|kontakt|contact|service|support|kundenservice|"
    r"customer(care|service)?|news|newsletter|mailer|mailing|notifications?|notify|alerts?|updates?|hello|hallo|hi|team|"
    r"office|admin|administrator|help|hilfe|feedback|account|accounts|security|billing|rechnung(en)?|invoice|orders?|"
    r"bestellung|shop|store|sales|vertrieb|marketing|press|presse|jobs|career|careers|karriere|bewerbung|recruiting|"
    r"welcome|member|members|membership|booking|reservierung|reservation|tickets?|abo|post|mail|email|webmaster|"
    r"postmaster|bounce|bounces|system|automated|auto|robot|bot|ticket|care|desk|enquiries|enquiry|general|"
    r"mailservice|datenschutz|privacy|legal|noreply-.*|no-reply-.*|.*-noreply|.*-no-reply|.*\\+.*)$", re.I)
FREEMAIL = {"gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "live.com", "msn.com", "yahoo.com", "yahoo.de",
            "web.de", "gmx.de", "gmx.net", "gmx.at", "gmx.ch", "icloud.com", "me.com", "mac.com", "t-online.de",
            "freenet.de", "posteo.de", "mailbox.org", "protonmail.com", "proton.me", "aol.com", "bluewin.ch", "outlook.de",
            "hotmail.de", "live.de", "online.de", "arcor.de", "1und1.de", "ymail.com"}
TWO_LEVEL = {"co.uk", "org.uk", "ac.uk", "com.au", "co.jp", "co.nz", "com.br", "co.za", "com.tr", "com.cn", "co.in"}

_mem = {}            # domain -> Gdk.Texture | None
_pending = set()
_pool = ThreadPoolExecutor(3, thread_name_prefix="logo")
_on_ready = []


def on_ready(cb):
    _on_ready.append(cb)


def off_ready(cb):
    if cb in _on_ready:
        _on_ready.remove(cb)


def is_organisation(name, addr):
    """Absender, der offensichtlich keine einzelne Person ist."""
    addr = (addr or "").lower()
    if "@" not in addr:
        return False
    local, domain = addr.rsplit("@", 1)
    if domain in FREEMAIL:
        return False
    if GENERIC_LOCAL.match(local):
        return True
    words = re.findall(r"[\w&+-]+", (name or "").lower())
    label = (domain.split(".")[-2] if domain.count(".") else domain).replace("-", "")
    flat = "".join(words).replace("-", "")
    if len(words) == 1 and words[0] not in ("",):        # ein Wort: „LinkedIn“, „ChatGPT“ (Vornamen-Absender in Freemail sind oben ausgenommen)
        return True
    if flat.startswith(label) and len(label) >= 3:         # „ABB Workday“ von abb.com, „Doctolib Support“ von doctolib.de
        return True
    return any(w in ORG_WORDS for w in words)               # „Windows Insider Program“, „… Team“, „… GmbH“


ORG_WORDS = {"team", "support", "service", "services", "newsletter", "program", "programm", "notifications", "benachrichtigungen",
             "gmbh", "ag", "inc", "ltd", "llc", "kg", "ug", "kundenservice", "center", "centre", "bank", "shop", "store", "info",
             "news", "alerts", "security", "sicherheit", "account", "konto", "bestellung", "rechnung", "versand", "noreply"}


def _candidates(domain):
    parts = domain.split(".")
    out = [domain]
    base_n = 3 if ".".join(parts[-2:]) in TWO_LEVEL else 2
    if len(parts) > base_n:
        out.append(".".join(parts[-base_n:]))
    return out


def _public_host(host):
    """Nur öffentliche Ziele: keine IP-Literale, keine lokalen/privaten Adressen."""
    if not host or "." not in host or host.endswith((".local", ".internal", ".localhost", ".lan")):
        return False
    try:
        ipaddress.ip_address(host)
        return False
    except ValueError:
        pass
    try:
        return all(ipaddress.ip_address(i[4][0]).is_global for i in socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP))
    except OSError:
        return False


class _Redirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        u = urllib.parse.urlparse(newurl)
        if u.scheme != "https" or not _public_host(u.hostname):
            return None
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_opener = urllib.request.build_opener(_Redirect)
_UA = {"User-Agent": "Mozilla/5.0 (compatible; Postfach logo fetch)"}


def _get(url, limit=MAX_BYTES, timeout=6):
    u = urllib.parse.urlparse(url)
    if u.scheme != "https" or not _public_host(u.hostname):
        return None, ""
    try:
        with _opener.open(urllib.request.Request(url, headers=_UA), timeout=timeout) as r:
            data = r.read(limit + 1)
            return (data if len(data) <= limit else None), r.headers.get("Content-Type", "")
    except Exception:
        return None, ""


def _icon_urls(domain):
    """Icon-Adressen der Startseite (größte zuerst) + Standardpfade."""
    urls = []
    html, _ = _get(f"https://{domain}/", limit=250_000)
    if html:
        text = html.decode("utf-8", "replace")
        base = f"https://{domain}/"
        found = []
        for tag in re.findall(r"<link\b[^>]*>", text, re.I):
            rel = re.search(r'rel=["\']([^"\']+)["\']', tag, re.I)
            href = re.search(r'href=["\']([^"\']+)["\']', tag, re.I)
            if not rel or not href or "icon" not in rel.group(1).lower():
                continue
            sz = re.search(r'sizes=["\'](\d+)x', tag, re.I)
            size = int(sz.group(1)) if sz else (180 if "apple" in rel.group(1).lower() else 32)
            found.append((size, urllib.parse.urljoin(base, href.group(1))))
        urls += [u for _s, u in sorted(found, reverse=True)]
    urls += [f"https://{domain}/apple-touch-icon.png", f"https://{domain}/favicon.ico"]
    return urls


def _render(data):
    """Bild → 96-px-PNG: Logo zentriert auf weißem Grund (dunkle Logos bleiben auf dunklem Theme sichtbar)."""
    loader = GdkPixbuf.PixbufLoader()

    def prepared(l, w, h):          # Vektorgrafiken (BIMI-SVG) in brauchbarer Größe rendern
        if w and h and max(w, h) < 256:
            f = 256 / max(w, h)
            l.set_size(int(w * f), int(h * f))

    loader.connect("size-prepared", prepared)
    try:
        loader.write(data)
        loader.close()
    except GLib.Error:
        return None
    pb = loader.get_pixbuf()
    if pb is None or pb.get_width() < 16 or pb.get_height() < 16:
        return None
    inner = int(SIZE * 0.68)
    scale = min(inner / pb.get_width(), inner / pb.get_height())
    w, h = max(1, int(pb.get_width() * scale)), max(1, int(pb.get_height() * scale))
    pb = pb.scale_simple(w, h, GdkPixbuf.InterpType.HYPER)
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, SIZE, SIZE)
    ctx = cairo.Context(surf)
    ctx.set_source_rgb(1, 1, 1)
    ctx.paint()
    # Pixbuf (RGBA, gerade Alpha) → cairo (BGRA, vormultipliziert)
    px, rs, ch = pb.get_pixels(), pb.get_rowstride(), pb.get_n_channels()
    icon = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
    buf, stride = icon.get_data(), icon.get_stride()
    for y in range(h):
        for x in range(w):
            i = y * rs + x * ch
            a = px[i + 3] if ch == 4 else 255
            o = y * stride + x * 4
            buf[o:o + 4] = bytes((px[i + 2] * a // 255, px[i + 1] * a // 255, px[i] * a // 255, a))
    icon.mark_dirty()
    ctx.set_source_surface(icon, (SIZE - w) / 2, (SIZE - h) / 2)
    ctx.paint()
    import io
    out = io.BytesIO()
    surf.write_to_png(out)
    return out.getvalue()


def _path(domain):
    return os.path.join(CACHE, hashlib.sha1(domain.encode()).hexdigest()[:16] + "-" + re.sub(r"[^a-z0-9.-]", "_", domain))


def _bimi_url(domain):
    """BIMI (Brand Indicators for Message Identification): Marken-Logo, das die Domain selbst per DNS veröffentlicht."""
    for cand in _candidates(domain):
        try:
            recs = Gio.Resolver.get_default().lookup_records(f"default._bimi.{cand}", Gio.ResolverRecordType.TXT, None)
        except GLib.Error:
            continue
        for v in recs:
            txt = "".join(v.unpack()[0])
            m = re.search(r"\bl=\s*(https://[^;\s]+)", txt)
            if txt.lower().lstrip().startswith("v=bimi1") and m:
                return m.group(1)
    return None


def _fetch(domain):
    """Reihenfolge: BIMI → eigene Webseite (mit und ohne www) → optional Icon-Dienst (nur wenn aktiviert)."""
    url = _bimi_url(domain)
    if url:
        data, _ = _get(url, limit=600_000)
        png = _render(data) if data else None
        if png:
            return png
    tried = set()
    for cand in _candidates(domain):
        for host in (cand, "www." + cand):
            if host in tried:
                continue
            tried.add(host)
            for u in _icon_urls(host):
                data, _ = _get(u)
                png = _render(data) if data else None
                if png:
                    return png
    if config.get("logo_service"):
        for cand in _candidates(domain):
            data, _ = _get(f"https://icons.duckduckgo.com/ip3/{cand}.ico")
            png = _render(data) if data else None
            if png:
                return png
    return None


def _purge_old_misses():
    """Neue Quellen (BIMI, www): frühere „kein Logo“-Ergebnisse einmalig verwerfen."""
    marker = os.path.join(CACHE, ".v2")
    if os.path.exists(marker):
        return
    for f in os.listdir(CACHE):
        if f.endswith(".none"):
            os.remove(os.path.join(CACHE, f))
    open(marker, "w").close()


def _work(domain):
    try:
        os.makedirs(CACHE, mode=0o700, exist_ok=True)
        png = _fetch(domain)
        p = _path(domain)
        if png:
            with open(p + ".png", "wb") as f:
                f.write(png)
        else:
            open(p + ".none", "w").close()
    finally:
        _pending.discard(domain)
        GLib.idle_add(_notify)


def _notify():
    for cb in _on_ready:
        cb()
    return False


def texture(name, addr):
    """Logo-Texture (Hauptthread) – None, solange keines vorhanden ist; ein Abruf wird im Hintergrund gestartet."""
    if os.environ.get("POSTFACH_DEMO") or not config.get("load_logos") or not is_organisation(name, addr):
        return None
    domain = addr.rsplit("@", 1)[-1].strip().lower()
    if domain in _mem:
        return _mem[domain]
    os.makedirs(CACHE, mode=0o700, exist_ok=True)
    _purge_old_misses()
    p = _path(domain)
    if os.path.exists(p + ".png"):
        try:
            _mem[domain] = Gdk.Texture.new_from_filename(p + ".png")
        except GLib.Error:
            _mem[domain] = None
        return _mem[domain]
    if os.path.exists(p + ".none") and time.time() - os.path.getmtime(p + ".none") < RETRY_AFTER:
        _mem[domain] = None
        return None
    if domain not in _pending:
        _pending.add(domain)
        _pool.submit(_work, domain)
    return None


def forget_memory():
    _mem.clear()
