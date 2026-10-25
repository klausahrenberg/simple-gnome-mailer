"""Bilder für den Versand verkleinern (Skalieren + JPEG-Neukodierung)."""
import os

import gi

gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf, GLib

from .i18n import _

SHRINKABLE = {"image/jpeg", "image/png", "image/webp", "image/bmp", "image/tiff"}   # kein GIF (Animation), kein SVG
PRESETS = [(_("Medium – longest side 1600 px (recommended)"), 1600), (_("Small – 1024 px"), 1024), (_("Large – 2400 px"), 2400)]


def is_shrinkable(ctype):
    return (ctype or "").lower() in SHRINKABLE


def shrink(data, name, max_px, quality="85"):
    """-> (Bytes, MIME, neuer Name) oder None, wenn das Ergebnis nicht kleiner wäre / das Bild nicht lesbar ist."""
    loader = GdkPixbuf.PixbufLoader()
    try:
        loader.write(data)
        loader.close()
    except GLib.Error:
        return None
    pb = loader.get_pixbuf()
    if pb is None:
        return None
    try:
        pb = pb.apply_embedded_orientation() or pb         # Handyfotos: Drehung aus den EXIF-Daten übernehmen
    except Exception:
        pass
    w, h = pb.get_width(), pb.get_height()
    if max(w, h) > max_px:                                  # nie vergrößern
        f = max_px / max(w, h)
        pb = pb.scale_simple(max(1, int(w * f)), max(1, int(h * f)), GdkPixbuf.InterpType.HYPER)
    base, _ = os.path.splitext(name)
    if pb.get_has_alpha():
        # Transparenz nur bei echten Alpha-Werten erhalten (PNG), sonst als JPEG auf weißem Grund
        px, ch = pb.get_pixels(), pb.get_n_channels()
        if any(px[i] < 255 for i in range(3, len(px), ch * 97)):
            ok, out = pb.save_to_bufferv("png", [], [])
            return (bytes(out), "image/png", base + ".png") if ok and len(out) < len(data) else None
        w2, h2 = pb.get_width(), pb.get_height()
        bg = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, w2, h2)
        bg.fill(0xFFFFFFFF)
        pb.composite(bg, 0, 0, w2, h2, 0, 0, 1, 1, GdkPixbuf.InterpType.BILINEAR, 255)
        pb = bg
    ok, out = pb.save_to_bufferv("jpeg", ["quality"], [quality])
    if not ok or len(out) >= len(data):
        return None
    return bytes(out), "image/jpeg", base + ".jpg"
