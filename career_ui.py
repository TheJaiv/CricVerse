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
    if v >= 88: return (64, 220, 120)    # elite green
    if v >= 80: return (140, 220, 110)   # strong
    if v >= 74: return (224, 200, 90)    # gold
    if v >= 68: return (235, 165, 80)    # orange
    return (220, 110, 110)               # weak red


def _ctext(d, cx, y, text, font, fill):
    """Draw horizontally-centered text at center-x = cx."""
    try:
        w = d.textlength(text, font=font)
    except Exception:
        w = font.getbbox(text)[2] if hasattr(font, "getbbox") else len(text) * 8
    d.text((cx - w / 2, y), text, font=font, fill=fill)


def render_career_card(career: dict) -> io.BytesIO:
    W, H = 660, 380
    tier = career.get("tier", "Bronze")
    accent = TIER_COLOR.get(tier, (205, 127, 50))

    # ── Vertical gradient background, faintly tinted by the tier colour ──
    img = Image.new("RGB", (W, H))
    d = ImageDraw.Draw(img)
    top = _mix((24, 27, 38), accent, 0.10)
    bot = (10, 11, 16)
    for y in range(H):
        d.line([(0, y), (W, y)], fill=_mix(top, bot, y / H))

    # Card border + left accent rail
    _rrect(d, [6, 6, W - 7, H - 7], 22, outline=_mix(accent, (255, 255, 255), 0.15), width=2)
    _rrect(d, [6, 6, 18, H - 7], 6, fill=accent)

    bt = CM.BOWLING_TYPES[career["bowling_type"]]
    ms = CM.MINDSETS[career["mindset"]]
    f_name = _font(40); f_sub = _font(19, bold=False); f_lbl = _font(20)
    f_val = _font(24); f_ovr = _font(72); f_tag = _font(17); f_pill = _font(18)

    # ── OVR badge (top-right) ──
    bx, by, br = W - 96, 92, 60
    _rrect(d, [bx - br, by - br, bx + br, by + br], br, fill=_mix(accent, (0, 0, 0), 0.18))
    _rrect(d, [bx - br, by - br, bx + br, by + br], br, outline=_mix(accent, (255, 255, 255), 0.35), width=3)
    ovr = career.get("ovr", CM.BASE_OVR)
    _ctext(d, bx, by - 46, str(ovr), f_ovr, (16, 16, 20))
    _ctext(d, bx, by + 30, "OVERALL", f_tag, (30, 30, 36))

    # Tier pill under the badge
    pill = [bx - 78, by + br + 12, bx + 78, by + br + 44]
    _rrect(d, pill, 16, fill=accent)
    _ctext(d, bx, by + br + 16, f"{tier.upper()}", f_pill, (16, 16, 20))
    _ctext(d, bx, by + br + 50, TIER_BLURB.get(tier, ""), f_tag, _mix(accent, (255, 255, 255), 0.3))

    # ── Identity block ──
    d.text((40, 34), str(career.get("username", "Rookie"))[:18], font=f_name, fill=(245, 247, 252))
    d.text((42, 86), "ALL-ROUNDER", font=f_lbl, fill=accent)
    d.text((42, 114), f"{ms['label']} batter   ·   {bt['label']} bowler", font=f_sub, fill=(176, 182, 198))
    if career.get("cosmetic_title"):
        d.text((42, 140), career["cosmetic_title"], font=f_sub, fill=_mix(accent, (255, 255, 255), 0.4))

    # ── Attribute bars ──
    short = {"power": "POWER", "control": "CONTROL", "bowling": "BOWLING", "stamina": "STAMINA"}
    y = 180
    bx0, bx1 = 160, 470
    for k in CM.ATTRS:
        v = int(career["attributes"][k])
        col = _rating_color(v)
        d.text((42, y - 2), short[k], font=f_lbl, fill=(206, 212, 226))
        _rrect(d, [bx0, y + 2, bx1, y + 24], 11, fill=(38, 41, 52))
        fill_w = bx0 + int((bx1 - bx0) * max(2, v) / 99)
        _rrect(d, [bx0, y + 2, fill_w, y + 24], 11, fill=col)
        d.text((bx1 + 16, y - 2), str(v), font=f_val, fill=col)
        y += 44

    # ── Footer: coins (+ debut state) ──
    _rrect(d, [40, H - 56, 300, H - 18], 14, fill=(30, 26, 12))
    d.text((56, H - 50), f"COINS   {career.get('coins', 0):,}", font=f_lbl, fill=(232, 196, 86))
    if not career.get("debut_done"):
        _rrect(d, [W - 220, H - 56, W - 40, H - 18], 14, fill=(60, 24, 24))
        _ctext(d, W - 130, H - 50, "DEBUT PENDING", f_lbl, (235, 130, 130))

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
