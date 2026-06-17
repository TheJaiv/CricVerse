"""
Career Mode UI + helpers (v2): all-rounder creation picker, PIL player card,
debut trial. Kept separate from prefix_handler so the command file stays thin.
"""
import io
import math
import random

import discord
from PIL import Image, ImageDraw, ImageFont

import career_manager as CM


def _font(size, bold=True):
    paths = (
        # Linux (server)
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        # macOS (local)
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/Library/Fonts/Arial.ttf",
    ) if bold else (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/Library/Fonts/Arial.ttf",
    )
    for p in paths:
        try:
            return ImageFont.truetype(p, size)
        except Exception:
            continue
    return ImageFont.load_default()


TIER_COLOR = {
    "Bronze": (205, 127, 50), "Silver": (176, 186, 199), "Gold": (224, 184, 56),
    "Platinum": (104, 214, 226), "Diamond": (130, 170, 255),
}
TIER_BLURB = {
    "Bronze": "THE ROOKIE", "Silver": "THE PRO", "Gold": "THE STAR",
    "Platinum": "THE ELITE", "Diamond": "THE LEGEND",
}


def _mix(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def _rrect(d, box, radius, **kw):
    try:
        d.rounded_rectangle(box, radius=radius, **kw)
    except Exception:
        d.rectangle(box, **kw)


def _rating_color(v):
    if v >= 90: return (64, 220, 120)    # elite green
    if v >= 80: return (140, 220, 110)   # strong
    if v >= 72: return (224, 200, 90)    # gold
    if v >= 64: return (235, 165, 80)    # orange
    return (224, 116, 110)               # rookie red


def _text_on(bg):
    """Black or white text for best contrast on a coloured background."""
    lum = 0.299 * bg[0] + 0.587 * bg[1] + 0.114 * bg[2]
    return (16, 18, 24) if lum > 140 else (244, 247, 251)


def _ctext(d, cx, y, text, font, fill):
    """Draw horizontally-centered text at center-x = cx."""
    try:
        w = d.textlength(text, font=font)
    except Exception:
        w = font.getbbox(text)[2] if hasattr(font, "getbbox") else len(text) * 8
    d.text((cx - w / 2, y), text, font=font, fill=fill)


def render_career_card(career: dict) -> io.BytesIO:
    W, H = 760, 420
    PW = 280                     # left identity panel width
    tier = career.get("tier", "Bronze")
    accent = TIER_COLOR.get(tier, (205, 127, 50))
    ink = _text_on(accent)       # readable text colour on the tier panel
    ink_soft = _mix(ink, accent, 0.30)

    img = Image.new("RGB", (W, H), (14, 16, 22))
    d = ImageDraw.Draw(img)

    # Right stats panel — dark vertical gradient
    for y in range(H):
        d.line([(PW, y), (W, y)], fill=_mix((28, 32, 44), (13, 14, 19), y / H))
    # Left identity panel — vibrant tier-colour gradient
    a_top = _mix(accent, (255, 255, 255), 0.10)
    a_bot = _mix(accent, (0, 0, 0), 0.42)
    for y in range(H):
        d.line([(0, y), (PW, y)], fill=_mix(a_top, a_bot, y / H))
    # Card frame
    _rrect(d, [5, 5, W - 6, H - 6], 24, outline=_mix(accent, (255, 255, 255), 0.25), width=2)

    bt = CM.BOWLING_TYPES[career["bowling_type"]]
    ms = CM.MINDSETS[career["mindset"]]
    f_ovr = _font(96); f_ovrl = _font(15); f_tier = _font(30); f_pos = _font(19)
    f_chip = _font(15); f_name = _font(40); f_title = _font(17, bold=False)
    f_attr = _font(19); f_num = _font(30); f_coin = _font(17); f_small = _font(14, bold=False)

    cx = PW // 2
    # ── Left panel: OVR · tier · role ──
    _ctext(d, cx, 22, str(career.get("ovr", CM.BASE_OVR)), f_ovr, ink)
    _ctext(d, cx, 138, "OVERALL", f_ovrl, ink_soft)
    _ctext(d, cx, 168, tier.upper(), f_tier, ink)
    d.line([(46, 214), (PW - 46, 214)], fill=ink_soft, width=2)
    _ctext(d, cx, 228, "ALL-ROUNDER", f_pos, ink)
    # mindset + bowling chips
    for i, txt in enumerate((f"{ms['label']} bat".upper(), f"{bt['label']} bowl".upper())):
        cy = 296 + i * 40
        _rrect(d, [40, cy, PW - 40, cy + 30], 15, fill=_mix(accent, (0, 0, 0), 0.30))
        _ctext(d, cx, cy + 6, txt, f_chip, ink)

    # ── Right panel: name + attributes + coins ──
    nx = PW + 30
    d.text((nx, 28), str(career.get("username", "Rookie"))[:16], font=f_name, fill=(245, 247, 252))
    if career.get("cosmetic_title"):
        d.text((nx, 76), career["cosmetic_title"], font=f_title, fill=accent)

    short = {"power": "POWER", "control": "CONTROL", "bowling": "BOWLING", "stamina": "STAMINA"}
    y = 116
    bar_x1 = W - 96
    for k in CM.ATTRS:
        v = int(career["attributes"][k])
        col = _rating_color(v)
        d.text((nx, y + 2), short[k], font=f_attr, fill=(202, 208, 222))
        d.text((W - 78, y - 4), str(v), font=f_num, fill=col)
        _rrect(d, [nx, y + 30, bar_x1, y + 40], 5, fill=(42, 46, 60))
        _rrect(d, [nx, y + 30, nx + int((bar_x1 - nx) * max(2, v) / 99), y + 40], 5, fill=col)
        y += 58

    fy = H - 46
    _rrect(d, [nx, fy, nx + 210, fy + 32], 15, fill=(36, 30, 13))
    d.text((nx + 16, fy + 7), f"COINS  {career.get('coins', 0):,}", font=f_coin, fill=(234, 198, 90))
    if not career.get("debut_done"):
        _rrect(d, [W - 196, fy, W - 30, fy + 32], 15, fill=(62, 24, 24))
        _ctext(d, W - 113, fy + 8, "DEBUT PENDING", f_small, (236, 132, 132))

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf


# ── Debut: 2-over Academy Trial (every player is an all-rounder; bat trial) ──
_SKILL_SCALE = 15.0
_AI_RATING = 72  # academy bowler — a real test for an OVR-68 rookie


def _eff(r):
    return math.exp((r - 80.0) / _SKILL_SCALE)


def run_debut_trial(career: dict):
    """Return (passed, lines, headline). Rookie bats 2 overs vs an academy attack."""
    eng = CM.career_to_engine(career)
    dom = _eff(eng["bat"]) / (_eff(eng["bat"]) + _eff(_AI_RATING))
    runs, seq = 0, []
    for _ in range(12):
        r = random.random()
        if r < 0.06 - dom * 0.03:
            seq.append("W")
        elif r < 0.06 - dom * 0.03 + 0.30:
            seq.append("•")
        elif r < 0.58:
            runs += 1; seq.append("1")
        elif r < 0.72 + dom * 0.12:
            runs += 4; seq.append("4")
        elif r < 0.80 + dom * 0.12:
            runs += 6; seq.append("6")
        else:
            runs += 2; seq.append("2")
    target = 16
    passed = runs >= target
    lines = [
        "🏏 **Academy Trial** — bat 2 overs vs the academy attack.",
        "`" + " ".join(seq) + "`",
        f"You scored **{runs}** — target **{target}**.",
    ]
    return passed, lines, ("✅ TRIAL PASSED" if passed else "❌ TRIAL FAILED")


# ── Creation picker: bowling type + batting mindset ─────────────────────────
class _BowlingSelect(discord.ui.Select):
    def __init__(self, picker):
        opts = [discord.SelectOption(label=f"{v['emoji']} {v['label']}", value=k,
                                     description="How you bowl") for k, v in CM.BOWLING_TYPES.items()]
        super().__init__(placeholder="1) Choose your bowling type", options=opts, row=0)
        self.picker = picker

    async def callback(self, interaction):
        if interaction.user.id != self.picker.uid:
            return await interaction.response.send_message("This isn't your setup.", ephemeral=True)
        self.picker.bowling = self.values[0]
        self.picker.sync()
        await interaction.response.edit_message(view=self.picker)


class _MindsetSelect(discord.ui.Select):
    def __init__(self, picker):
        opts = [discord.SelectOption(label=f"{v['emoji']} {v['label']}", value=k,
                                     description=v["desc"][:90]) for k, v in CM.MINDSETS.items()]
        super().__init__(placeholder="2) Choose your batting mindset", options=opts, row=1)
        self.picker = picker

    async def callback(self, interaction):
        if interaction.user.id != self.picker.uid:
            return await interaction.response.send_message("This isn't your setup.", ephemeral=True)
        self.picker.mindset = self.values[0]
        self.picker.sync()
        await interaction.response.edit_message(view=self.picker)


class _CreateButton(discord.ui.Button):
    def __init__(self, picker):
        super().__init__(label="Create Career", style=discord.ButtonStyle.success, row=2, disabled=True)
        self.picker = picker

    async def callback(self, interaction):
        p = self.picker
        if interaction.user.id != p.uid:
            return await interaction.response.send_message("This isn't your setup.", ephemeral=True)
        career, err = CM.create_career(p.uid, p.username, p.bowling, p.mindset)
        if err:
            return await interaction.response.send_message(f"❌ {err}", ephemeral=True)
        bt, ms = CM.BOWLING_TYPES[p.bowling], CM.MINDSETS[p.mindset]
        await interaction.response.edit_message(
            content=(f"🎉 **Career created!** All-Rounder — {ms['emoji']} **{ms['label']}** batter, "
                     f"{bt['emoji']} **{bt['label']}** bowler — **{CM.BASE_OVR} OVR** Bronze.\n"
                     f"Next: pass your **`cv debut`** to unlock your official card!"),
            view=None,
        )
        await interaction.followup.send(file=discord.File(render_career_card(career), "career_card.png"))


class CareerCreateView(discord.ui.View):
    def __init__(self, user_id, username):
        super().__init__(timeout=180)
        self.uid = user_id
        self.username = username
        self.bowling = None
        self.mindset = None
        self.add_item(_BowlingSelect(self))
        self.add_item(_MindsetSelect(self))
        self.btn = _CreateButton(self)
        self.add_item(self.btn)

    def sync(self):
        self.btn.disabled = not (self.bowling and self.mindset)
