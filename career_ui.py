"""
Career Mode UI + helpers: archetype picker, PIL player card, debut trial sim.
Kept separate from prefix_handler so the command file stays thin.
"""
import io
import math
import random

import discord
from PIL import Image, ImageDraw, ImageFont

import career_manager as CM

# ── fonts (mirror bot.py's truetype-with-fallback pattern) ──────────────────
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
    """Draw a self-contained player card (no template image needed)."""
    W, H = 520, 300
    tier = career.get("tier", "Bronze")
    accent = TIER_COLOR.get(tier, (176, 113, 70))
    img = Image.new("RGB", (W, H), (18, 20, 28))
    d = ImageDraw.Draw(img)

    # header band + accent stripe
    d.rectangle([0, 0, W, 70], fill=(28, 31, 44))
    d.rectangle([0, 0, 8, H], fill=accent)

    arch = CM.ARCHETYPES[career["archetype"]]
    f_name, f_big, f_lbl, f_val, f_sm = _font(30), _font(54), _font(18), _font(20), _font(16)

    d.text((26, 16), career.get("username", "Rookie")[:22], font=f_name, fill=(255, 255, 255))
    d.text((26, 48), f"{arch['emoji']} {arch['label']}", font=f_sm, fill=(180, 186, 200))

    # OVR + tier (top-right)
    ovr = career.get("ovr", 50)
    d.text((W - 150, 8), str(ovr), font=f_big, fill=accent)
    d.text((W - 152, 64), "OVR", font=f_sm, fill=(150, 156, 170))
    d.rectangle([W - 165, 92, W - 26, 122], fill=accent)
    d.text((W - 158, 96), f"{tier.upper()}", font=f_lbl, fill=(15, 15, 20))

    # attribute bars
    y = 110
    short = {"power": "POW", "control": "CTL", "pace": "PAC", "spin": "SPN", "stamina": "STA"}
    for k in CM.ATTRS:
        v = career["attributes"][k]
        d.text((26, y), short[k], font=f_lbl, fill=(190, 196, 210))
        bx0, bx1 = 90, 360
        d.rectangle([bx0, y + 2, bx1, y + 18], fill=(40, 44, 56))
        fillw = bx0 + int((bx1 - bx0) * v / 99)
        d.rectangle([bx0, y + 2, fillw, y + 18], fill=accent)
        d.text((bx1 + 12, y), str(v), font=f_val, fill=(255, 255, 255))
        y += 34

    # coins footer
    coins = career.get("coins", 0)
    d.text((26, H - 30), f"🪙 {coins:,} coins", font=f_lbl, fill=(212, 175, 55))
    if not career.get("debut_done"):
        d.text((W - 230, H - 30), "⚠ Debut pending", font=f_sm, fill=(220, 120, 120))
    title = career.get("cosmetic_title")
    if title:
        d.text((W - 200, 48), title, font=f_sm, fill=accent)

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf


# ── Debut: 2-over Academy Trial ─────────────────────────────────────────────
# Lightweight self-contained sim using the engine's exponential skill model.
# Tuned so a fresh 50-OVR rookie passes most of the time (it's a debut), but
# can fail and retry.
_SKILL_SCALE = 15.0
_AI_RATING = 52  # academy bowler / batter strength


def _eff(rating):
    return math.exp((rating - 80.0) / _SKILL_SCALE)


def run_debut_trial(career: dict):
    """Return (passed: bool, lines: list[str], headline: str)."""
    eng = CM.career_to_engine(career)
    batting = eng["bat"] >= eng["bowl"]
    lines = []
    if batting:
        dom = _eff(eng["bat"]) / (_eff(eng["bat"]) + _eff(_AI_RATING))
        runs, out, seq = 0, False, []
        for _ in range(12):
            r = random.random()
            if r < 0.05 - dom * 0.03:
                seq.append("W"); out = True; break
            elif r < 0.05 - dom * 0.03 + 0.30:
                seq.append("•")
            elif r < 0.58:
                runs += 1; seq.append("1")
            elif r < 0.72 + dom * 0.12:
                runs += 4; seq.append("4")
            elif r < 0.80 + dom * 0.12:
                runs += 6; seq.append("6")
            else:
                runs += 2; seq.append("2")
        target = 12
        passed = runs >= target
        lines.append("🏏 Academy Trial — **Batting** (face 2 overs)")
        lines.append("`" + " ".join(seq) + "`")
        lines.append(f"You scored **{runs}** {'(out)' if out else ''} — target **{target}**.")
        headline = "✅ TRIAL PASSED" if passed else "❌ TRIAL FAILED"
    else:
        dom = _eff(eng["bowl"]) / (_eff(eng["bowl"]) + _eff(_AI_RATING))
        conceded, wickets = 0, 0
        seq = []
        for _ in range(12):
            r = random.random()
            if r < 0.08 + dom * 0.06:
                wickets += 1; seq.append("W")
            elif r < 0.08 + dom * 0.06 + 0.4:
                seq.append("•")
            elif r < 0.78:
                conceded += 1; seq.append("1")
            elif r < 0.9 - dom * 0.08:
                conceded += 4; seq.append("4")
            else:
                conceded += 2; seq.append("2")
        lines.append(f"🎯 Academy Trial — **Bowling** (2 overs)")
        lines.append("`" + " ".join(seq) + "`")
        lines.append(f"You took **{wickets}** wkt, conceded **{conceded}** — pass: 1 wkt or ≤11 runs.")
        passed = wickets >= 1 or conceded <= 11
        headline = "✅ TRIAL PASSED" if passed else "❌ TRIAL FAILED"
    return passed, lines, headline


# ── Archetype picker ────────────────────────────────────────────────────────
class _ArchetypeSelect(discord.ui.Select):
    def __init__(self, user_id, username):
        self.user_id = user_id
        self.username = username
        opts = [
            discord.SelectOption(label=v["label"], value=k, emoji=v["emoji"],
                                 description=v["desc"][:90])
            for k, v in CM.ARCHETYPES.items()
        ]
        super().__init__(placeholder="Choose your specialty…", options=opts, min_values=1, max_values=1)

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message("This isn't your career setup.", ephemeral=True)
        career, err = CM.create_career(self.user_id, self.username, self.values[0])
        if err:
            return await interaction.response.send_message(f"❌ {err}", ephemeral=True)
        arch = CM.ARCHETYPES[career["archetype"]]
        buf = render_career_card(career)
        await interaction.response.edit_message(
            content=(f"🎉 **Career created!** {arch['emoji']} **{arch['label']}** — "
                     f"50 OVR Rookie (Bronze).\nNext: pass your **`cv debut`** to unlock your official card!"),
            view=None,
        )
        await interaction.followup.send(file=discord.File(buf, "career_card.png"))


class ArchetypeSelectView(discord.ui.View):
    def __init__(self, user_id, username):
        super().__init__(timeout=120)
        self.add_item(_ArchetypeSelect(user_id, username))
