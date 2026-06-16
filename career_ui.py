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
    paths = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold
        else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/Library/Fonts/Arial.ttf",
    ]
    for p in paths:
        try:
            return ImageFont.truetype(p, size)
        except Exception:
            continue
    return ImageFont.load_default()


TIER_COLOR = {
    "Bronze": (176, 113, 70), "Silver": (158, 168, 181), "Gold": (212, 175, 55),
    "Platinum": (110, 200, 210), "Diamond": (120, 160, 255),
}


def render_career_card(career: dict) -> io.BytesIO:
    W, H = 540, 320
    tier = career.get("tier", "Bronze")
    accent = TIER_COLOR.get(tier, (176, 113, 70))
    img = Image.new("RGB", (W, H), (18, 20, 28))
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, W, 74], fill=(28, 31, 44))
    d.rectangle([0, 0, 8, H], fill=accent)

    bt = CM.BOWLING_TYPES[career["bowling_type"]]
    ms = CM.MINDSETS[career["mindset"]]
    f_name, f_big, f_lbl, f_val, f_sm = _font(30), _font(56), _font(18), _font(20), _font(16)

    d.text((26, 14), career.get("username", "Rookie")[:22], font=f_name, fill=(255, 255, 255))
    d.text((26, 50), f"{ms['emoji']} {ms['label']}  •  {bt['emoji']} {bt['label']}", font=f_sm, fill=(180, 186, 200))

    ovr = career.get("ovr", CM.BASE_OVR)
    d.text((W - 150, 6), str(ovr), font=f_big, fill=accent)
    d.text((W - 150, 64), "OVR  ·  ALL-ROUNDER", font=f_sm, fill=(150, 156, 170))
    d.rectangle([W - 170, 96, W - 26, 126], fill=accent)
    d.text((W - 162, 100), tier.upper(), font=f_lbl, fill=(15, 15, 20))

    y = 118
    short = {"power": "POWER", "control": "CONTROL", "bowling": "BOWLING", "stamina": "STAMINA"}
    for k in CM.ATTRS:
        v = career["attributes"][k]
        d.text((26, y), short[k], font=f_lbl, fill=(190, 196, 210))
        bx0, bx1 = 150, 380
        d.rectangle([bx0, y + 2, bx1, y + 18], fill=(40, 44, 56))
        d.rectangle([bx0, y + 2, bx0 + int((bx1 - bx0) * v / 99), y + 18], fill=accent)
        d.text((bx1 + 12, y), str(v), font=f_val, fill=(255, 255, 255))
        y += 36

    d.text((26, H - 30), f"🪙 {career.get('coins', 0):,} coins", font=f_lbl, fill=(212, 175, 55))
    if not career.get("debut_done"):
        d.text((W - 230, H - 30), "⚠ Debut pending", font=f_sm, fill=(220, 120, 120))
    if career.get("cosmetic_title"):
        d.text((W - 200, 50), career["cosmetic_title"], font=f_sm, fill=accent)

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
    def __init__(self, parent):
        self.parent = parent
        opts = [discord.SelectOption(label=v["label"], value=k, emoji=v["emoji"],
                                     description="How you bowl") for k, v in CM.BOWLING_TYPES.items()]
        super().__init__(placeholder="① Choose your bowling type…", options=opts, row=0)

    async def callback(self, interaction):
        if interaction.user.id != self.parent.uid:
            return await interaction.response.send_message("This isn't your setup.", ephemeral=True)
        self.parent.bowling = self.values[0]
        self.parent.sync()
        await interaction.response.edit_message(view=self.parent)


class _MindsetSelect(discord.ui.Select):
    def __init__(self, parent):
        self.parent = parent
        opts = [discord.SelectOption(label=v["label"], value=k, emoji=v["emoji"],
                                     description=v["desc"][:90]) for k, v in CM.MINDSETS.items()]
        super().__init__(placeholder="② Choose your batting mindset…", options=opts, row=1)

    async def callback(self, interaction):
        if interaction.user.id != self.parent.uid:
            return await interaction.response.send_message("This isn't your setup.", ephemeral=True)
        self.parent.mindset = self.values[0]
        self.parent.sync()
        await interaction.response.edit_message(view=self.parent)


class _CreateButton(discord.ui.Button):
    def __init__(self, parent):
        super().__init__(label="Create Career", style=discord.ButtonStyle.success, row=2, disabled=True)
        self.parent = parent

    async def callback(self, interaction):
        p = self.parent
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
