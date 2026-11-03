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


def _edge_color(pb):
    """Randfarbe eines bildfüllenden Logos: Mittelwert der Randpunkte, wenn der Rand weitgehend deckend ist
    (Farbflächen auch mit leichtem Verlauf, z. B. DHL); sonst None (durchsichtiger oder leerer Rand)."""
    w, h, rs, ch, px = pb.get_width(), pb.get_height(), pb.get_rowstride(), pb.get_n_channels(), pb.get_pixels()
    pts = [(x, y) for x in range(0, w, max(1, w // 24)) for y in (0, h - 1)]
    pts += [(x, y) for y in range(0, h, max(1, h // 24)) for x in (0, w - 1)]
    opaque = []
    for x, y in pts:
        i = y * rs + x * ch
        if ch == 4 and px[i + 3] < 200:
            continue
        opaque.append((px[i], px[i + 1], px[i + 2]))
    if len(opaque) < 0.7 * len(pts):         # abgerundete Ecken sind durchsichtig
        return None
    return tuple(sum(c[k] for c in opaque) // len(opaque) for k in range(3))


def _near_white(c):
    return min(c) >= 235


def _content_box(pb):
    """Rechteck (x, y, w, h) der sichtbaren, nicht weißen Bildpunkte – schneidet leere/weiße Ränder ab."""
    w, h, rs, ch, px = pb.get_width(), pb.get_height(), pb.get_rowstride(), pb.get_n_channels(), pb.get_pixels()
    x0, y0, x1, y1 = w, h, -1, -1
    for y in range(h):
        row = y * rs
        for x in range(w):
            i = row + x * ch
            if (ch == 4 and px[i + 3] < 200) or min(px[i], px[i + 1], px[i + 2]) >= 235:
                continue
            if x < x0:
                x0 = x
            if x > x1:
                x1 = x
            if y < y0:
                y0 = y
            if y > y1:
                y1 = y
    if x1 < 0:
        return None
    return x0, y0, x1 - x0 + 1, y1 - y0 + 1


def _dominant_color(pb, box):
    """Vorherrschende Farbe innerhalb des Logo-Rechtecks, wenn sie dessen Fläche deutlich (≥ 45 %) bedeckt – eine Farbfläche,
    auf der das Logo sitzt (LinkedIn-Blau, DHL-Gelb) –, sonst None."""
    bx, by, bw, bh = box
    w, rs, ch, px = pb.get_width(), pb.get_rowstride(), pb.get_n_channels(), pb.get_pixels()
    buckets, step = {}, 2
    for y in range(by, by + bh, step):
        for x in range(bx, bx + bw, step):
            i = y * rs + x * ch
            if ch == 4 and px[i + 3] < 200:
                continue
            c = (px[i], px[i + 1], px[i + 2])
            if min(c) >= 235:
                continue
            b = buckets.setdefault((c[0] >> 4, c[1] >> 4, c[2] >> 4), [0, 0, 0, 0])
            b[0] += 1
            for k in range(3):
                b[k + 1] += c[k]
    if not buckets:
        return None
    best = max(buckets.values(), key=lambda b: b[0])
    if best[0] * step * step < 0.45 * bw * bh:
        return None
    color = tuple(best[k + 1] // best[0] for k in range(3))
    # eine Farbfläche reicht bis an den Rand des Logos – eine bloße Figur (PayPal-„P“) nicht, sonst verschwände sie
    pts = [(x, by + yy) for x in range(bx, bx + bw, max(1, bw // 24)) for yy in (0, bh - 1)]
    pts += [(bx + xx, y) for y in range(by, by + bh, max(1, bh // 24)) for xx in (0, bw - 1)]
    near = 0
    for x, y in pts:
        i = y * rs + x * ch
        if (ch == 4 and px[i + 3] < 200) or max(abs(px[i + k] - color[k]) for k in range(3)) > 48:
            continue
        near += 1
    return color if near >= 0.4 * len(pts) else None


def _color_box(pb, color):
    """Rechteck (x, y, w, h) der Punkte in der Farbe `color` – die Farbfläche selbst, ohne Beiwerk wie das ®-Zeichen."""
    w, h, rs, ch, px = pb.get_width(), pb.get_height(), pb.get_rowstride(), pb.get_n_channels(), pb.get_pixels()
    x0, y0, x1, y1 = w, h, -1, -1
    for y in range(h):
        row = y * rs
        for x in range(w):
            i = row + x * ch
            if (ch == 4 and px[i + 3] < 200) or max(abs(px[i + k] - color[k]) for k in range(3)) > 48:
                continue
            x0, x1 = min(x0, x), max(x1, x)
            y0, y1 = min(y0, y), max(y1, y)
    return (x0, y0, x1 - x0 + 1, y1 - y0 + 1) if x1 >= 0 else None


def _render(data):
    """Bild → 96-px-PNG: Logo zentriert; eckige Logos mit eigener Hintergrundfarbe bekommen diese Farbe als Grund,
    alle anderen weißen Grund (dunkle Logos bleiben auf dunklem Theme sichtbar)."""
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
    bg = _edge_color(pb)
    fill = bg is not None and not _near_white(bg)      # bildfüllende Farbfläche (DHL): füllt das ganze Symbol
    if not fill:
        # weißer/durchsichtiger Rand um das Logo: Rand abschneiden; sitzt es auf einer Farbfläche (LinkedIn), diese als Grund
        box = _content_box(pb)
        bg = None
        if box and box[2] >= 12 and box[3] >= 12:
            if box[2] * box[3] < 0.95 * pb.get_width() * pb.get_height():
                pb = pb.new_subpixbuf(*box)
                box = (0, 0, box[2], box[3])
            color = _dominant_color(pb, box)
            if color:
                bg = color
                field = _color_box(pb, color)               # nur die Farbfläche behalten (Beiwerk wie ® liegt außerhalb)
                if field and field[2] >= 12 and field[3] >= 12:
                    if field[2] * field[3] < 0.98 * pb.get_width() * pb.get_height():
                        pb = pb.new_subpixbuf(*field)
                    fill = True
    inner = SIZE if fill else int(SIZE * (0.8 if bg else 0.68))    # Logo auf Farbfläche etwas größer als auf Weiß
    scale = min(inner / pb.get_width(), inner / pb.get_height())
    w, h = max(1, int(pb.get_width() * scale)), max(1, int(pb.get_height() * scale))
    pb = pb.scale_simple(w, h, GdkPixbuf.InterpType.HYPER)
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, SIZE, SIZE)
    ctx = cairo.Context(surf)
    ctx.set_source_rgb(*((c / 255 for c in bg) if bg else (1, 1, 1)))
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
    marker = os.path.join(CACHE, ".v7")          # v7: Farbfläche ohne Beiwerk (LinkedIn-®) zuschneiden -> alle Logos neu rendern
    if os.path.exists(marker):
        return
    for f in os.listdir(CACHE):
        if f.endswith((".none", ".png")):
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
