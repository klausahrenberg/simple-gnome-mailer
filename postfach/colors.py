"""Kontofarben (GNOME-Palette) und zugehöriges CSS."""
from .i18n import _
PALETTE = [(_("Blue"), "#3584e4"), (_("Green"), "#33d17a"), (_("Yellow"), "#f6d32d"), (_("Orange"), "#ff7800"),
           (_("Red"), "#e01b24"), (_("Purple"), "#9141ac"), (_("Teal"), "#2190a4"), (_("Brown"), "#986a44")]


def index_for(acc, ordinal):
    """accounts.color: 0 = automatisch (nach Reihenfolge), sonst 1-basierter Palettenindex."""
    c = acc["color"] or 0
    return (c - 1) % len(PALETTE) if c else ordinal % len(PALETTE)


def css():
    rules = ["", ".acc-bar { min-width: 3px; border-radius: 2px; }",
             ".acc-dot { min-width: 10px; min-height: 10px; border-radius: 50%; }",
             ".remote-bar { background: alpha(currentColor, .08); padding: 6px 12px; }",
             "button.row-star { min-width: 22px; min-height: 22px; padding: 0; }",
             "button.swatch { min-width: 26px; min-height: 26px; padding: 0; border-radius: 50%; }"]
    rules += [f".acc-color-{i} {{ background: {hexv}; }}" for i, (_n, hexv) in enumerate(PALETTE)]
    return "\n".join(rules)
