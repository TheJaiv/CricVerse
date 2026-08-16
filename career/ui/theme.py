"""
Career Mode visual language: palette, fonts and drawing primitives.

Career Mode deliberately does NOT look like the rest of CricVerse. The tournament
scorecards are bright panels on template PNGs; career is a TV broadcast - deep
navy, team-colour bands, a live badge, lower-third score bars and a ticker.

Everything here is pure Pillow. Nothing in career/ui imports discord or bot, so
every renderer can run headless in tools/career_render_preview.py.

Fonts are VENDORED in assets/fonts (DejaVu, free licence - see LICENSE-DejaVu.txt).
bot.py's own renderers hardcode a Linux-only DejaVu path inside a bare except, so
on macOS they silently fall back to Pillow's tiny bitmap face; loading the shipped
file first means career renders identically on the dev Mac and the Linux host.
"""
import os
from PIL import Image, ImageDraw, ImageFont, ImageFilter

_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_FONT_DIR = os.path.join(_REPO, "assets", "fonts")

# weight -> (vendored file, system fallbacks)
_FACES = {
    "regular": ("DejaVuSans.ttf", (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
    )),
    "bold": ("DejaVuSans-Bold.ttf", (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    )),
    "cond": ("DejaVuSansCondensed-Bold.ttf", (
        "/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed-Bold.ttf",
        "/System/Library/Fonts/Supplemental/Arial Narrow Bold.ttf",
    )),
    "mono": ("DejaVuSansMono-Bold.ttf", (
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf",
        "/System/Library/Fonts/Supplemental/Menlo.ttc",
    )),
}

_FONT_CACHE = {}


def font(size, weight="bold"):
    """Load a face at `size`. Cached - a live card builds ~30 text runs per redraw
    and re-parsing the TTF each time is most of the render cost."""
    key = (size, weight)
    if key in _FONT_CACHE:
        return _FONT_CACHE[key]
    vendored, fallbacks = _FACES.get(weight, _FACES["bold"])
    for path in (os.path.join(_FONT_DIR, vendored),) + fallbacks:
        try:
            f = ImageFont.truetype(path, size)
            _FONT_CACHE[key] = f
            return f
        except Exception:
            continue
    f = ImageFont.load_default()
    _FONT_CACHE[key] = f
    return f


def fonts_are_vendored():
    """True when the shipped TTFs loaded. The preview tool asserts this so a
    missing assets/fonts is a loud failure, not silently ugly output."""
    return all(os.path.exists(os.path.join(_FONT_DIR, v)) for v, _ in _FACES.values())


# Broadcast palette
BG        = (9, 14, 28)         # deep broadcast navy
BG_DEEP   = (5, 8, 18)
PANEL     = (19, 28, 51)
PANEL_2   = (27, 39, 66)
PANEL_HI  = (36, 51, 84)
LINE      = (48, 66, 105)
INK       = (240, 245, 255)
INK_SOFT  = (150, 166, 196)
INK_DIM   = (100, 116, 146)
ACCENT    = (242, 178, 58)      # CricVerse gold - headline numbers, chips
ACCENT_2  = (64, 214, 226)      # cyan - secondary data
LIVE      = (226, 58, 58)
GOOD      = (64, 220, 120)
WARN      = (238, 160, 70)
BAD       = (226, 92, 92)

TIER_COLOR = {
    "Bronze":   (205, 127, 50),
    "Silver":   (176, 186, 199),
    "Gold":     (224, 184, 56),
    "Platinum": (104, 214, 226),
    "Diamond":  (130, 170, 255),
}
TIER_BLURB = {
    "Bronze": "THE ROOKIE", "Silver": "THE PRO", "Gold": "THE STAR",
    "Platinum": "THE ELITE", "Diamond": "THE LEGEND",
}

# Ball-outcome colours, shared by the live card strip, the GIFs and the charts
OUTCOME_COLOR = {
    "dot": (110, 126, 156), "single": (150, 166, 196), "two": (150, 166, 196),
    "three": (150, 166, 196), "four": (64, 214, 226), "six": (168, 120, 255),
    "wicket": (226, 58, 58), "wide": (238, 160, 70), "noball": (238, 160, 70),
}


def mix(a, b, t):
    """Blend two RGB tuples; t=0 gives a, t=1 gives b."""
    t = max(0.0, min(1.0, t))
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def hex_to_rgb(h, fallback=(107, 114, 128)):
    """Team colours are stored as '#RRGGBB' strings on the tournament/team dicts."""
    try:
        h = str(h).lstrip("#")
        if len(h) == 3:
            h = "".join(c * 2 for c in h)
        return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
    except Exception:
        return fallback


def text_on(bg):
    """Black or white ink, whichever reads on `bg`."""
    lum = 0.299 * bg[0] + 0.587 * bg[1] + 0.114 * bg[2]
    return (12, 16, 24) if lum > 140 else INK


def rating_color(v):
    if v >= 90: return GOOD
    if v >= 80: return (140, 220, 110)
    if v >= 72: return ACCENT
    if v >= 64: return WARN
    return BAD


def rrect(d, box, radius, **kw):
    """Rounded rectangle with a hard-edge fallback for ancient Pillow."""
    try:
        d.rounded_rectangle(box, radius=radius, **kw)
    except Exception:
        d.rectangle(box, **kw)


def vgrad(img, box, top, bottom):
    """Vertical gradient inside box. Drawn onto a strip then pasted, which is far
    cheaper than one draw.line per row on the full-size canvas."""
    x1, y1, x2, y2 = [int(v) for v in box]
    h = max(1, y2 - y1)
    strip = Image.new("RGB", (1, h))
    sp = strip.load()
    for y in range(h):
        sp[0, y] = mix(top, bottom, y / max(1, h - 1))
    img.paste(strip.resize((max(1, x2 - x1), h), Image.BILINEAR), (x1, y1))


def hgrad(img, box, left, right):
    x1, y1, x2, y2 = [int(v) for v in box]
    w = max(1, x2 - x1)
    strip = Image.new("RGB", (w, 1))
    sp = strip.load()
    for x in range(w):
        sp[x, 0] = mix(left, right, x / max(1, w - 1))
    img.paste(strip.resize((w, max(1, y2 - y1)), Image.BILINEAR), (x1, y1))


def tw(d, text, f):
    """Width of `text` in font `f`."""
    try:
        return d.textlength(text, font=f)
    except Exception:
        return f.getbbox(text)[2] if hasattr(f, "getbbox") else len(text) * 8


def ctext(d, cx, y, text, f, fill):
    """Draw horizontally centred on cx."""
    d.text((cx - tw(d, text, f) / 2, y), text, font=f, fill=fill)


def rtext(d, rx, y, text, f, fill):
    """Draw right-aligned to rx. Score numerals live on the right edge, so this
    keeps them from drifting as the digit count changes."""
    d.text((rx - tw(d, text, f), y), text, font=f, fill=fill)


def fit_text(d, text, f_factory, max_w, start, min_size=10):
    """Shrink until it fits. Player names are user-supplied (up to 16 chars) and
    club names are longer still, so fixed sizes overflow the panels."""
    size = start
    while size > min_size:
        f = f_factory(size)
        if tw(d, text, f) <= max_w:
            return f
        size -= 1
    return f_factory(min_size)


def chip(d, box, text, f, fill, ink=None, radius=None):
    """Pill label - used for tier, role, conditions and status flags."""
    x1, y1, x2, y2 = box
    rrect(d, box, radius if radius is not None else (y2 - y1) // 2, fill=fill)
    ink = ink if ink is not None else text_on(fill)
    ctext(d, (x1 + x2) / 2, y1 + (y2 - y1 - f.size) / 2 - 1, text, f, ink)


def bar(d, box, frac, fill, track=PANEL_HI, radius=None):
    """Progress/rating bar. frac is clamped, and a non-zero value always paints at
    least a nub so a 1% bar is still visible."""
    x1, y1, x2, y2 = box
    r = radius if radius is not None else (y2 - y1) // 2
    rrect(d, box, r, fill=track)
    frac = max(0.0, min(1.0, frac))
    if frac <= 0:
        return
    w = max(y2 - y1, int((x2 - x1) * frac))
    rrect(d, [x1, y1, x1 + w, y2], r, fill=fill)


def glow(img, box, color, radius=18, strength=0.55):
    """Soft coloured bloom behind a panel - the thing that makes it read as a
    broadcast graphic rather than a flat rectangle."""
    x1, y1, x2, y2 = [int(v) for v in box]
    layer = Image.new("RGB", img.size, (0, 0, 0))
    ImageDraw.Draw(layer).rectangle([x1, y1, x2, y2], fill=color)
    layer = layer.filter(ImageFilter.GaussianBlur(radius))
    img.paste(Image.blend(img, Image.blend(img, layer, strength), 1.0))


def live_badge(d, x, y, f, label="LIVE", on=True):
    """Red LIVE pill with a dot. `on=False` dims it for replays and breaks."""
    col = LIVE if on else INK_DIM
    w = int(tw(d, label, f)) + 42
    h = f.size + 12
    rrect(d, [x, y, x + w, y + h], h // 2, fill=col)
    cy = y + h / 2
    d.ellipse([x + 12, cy - 5, x + 22, cy + 5], fill=(255, 255, 255))
    d.text((x + 30, y + 5), label, font=f, fill=(255, 255, 255))
    return x + w


def team_band(img, d, box, color, label, f, align="left"):
    """Team-colour band with the name knocked out of it - the broadcast staple."""
    x1, y1, x2, y2 = box
    hgrad(img, box, color, mix(color, BG, 0.55))
    ink = text_on(color)
    if align == "left":
        d.text((x1 + 18, y1 + (y2 - y1 - f.size) / 2 - 1), label, font=f, fill=ink)
    else:
        rtext(d, x2 - 18, y1 + (y2 - y1 - f.size) / 2 - 1, label, f, ink)


def initials_disc(size, name, color):
    """Fallback club/team crest: coloured disc with initials. Career clubs have no
    uploaded logos, and this is what tbecs_manager does for unset team logos too."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse([0, 0, size - 1, size - 1], fill=color + (255,))
    parts = [p for p in str(name).split() if p][:2]
    ini = "".join(p[0] for p in parts).upper() or "?"
    f = font(int(size * 0.42), "bold")
    ctext(d, size / 2, size / 2 - f.size * 0.62, ini, f, text_on(color))
    return img


def canvas(w, h, top=None, bottom=None):
    """Standard career backdrop: subtle vertical gradient, returns (img, draw)."""
    img = Image.new("RGB", (w, h), BG)
    vgrad(img, [0, 0, w, h], top or mix(BG, PANEL, 0.35), bottom or BG_DEEP)
    return img, ImageDraw.Draw(img)


def save_png(img):
    import io
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=False)
    buf.seek(0)
    return buf
