import discord
from discord import app_commands
from discord.ext import commands
import difflib
import io
import re
import random
import requests
from PIL import Image, ImageDraw, ImageFont
import asyncio
from subscription_manager import DB_CACHE, async_save_tournament_to_bin, get_all_players, get_tier_status

# ── Tournament pitch & weather conditions ────────────────────────────────────
# Canonical engine lists (mirror the PitchWeatherView dropdowns in bot.py).
ALL_PITCHES = ["Flat", "Green", "Dry", "Dusty", "Hard", "Soft", "Cracked", "Damp",
               "Dead", "Worn", "Turning", "Two-Paced", "Slow", "Bouncy", "Sticky"]
ALL_WEATHER = ["Clear", "Cloudy", "Overcast", "Humid", "Dry Heat", "Windy",
               "Light Rain", "Drizzle", "Heavy Rain", "Thunderstorm"]
# "Nice"/standard pools — group stages draw from these 90% of the time, knockouts 100%.
GROUP_PITCHES = ["Flat", "Dead", "Hard", "Green", "Dusty"]
GROUP_WEATHER = ["Clear", "Cloudy"]
_OTHER_PITCHES = [p for p in ALL_PITCHES if p not in GROUP_PITCHES]
_OTHER_WEATHER = [w for w in ALL_WEATHER if w not in GROUP_WEATHER]

def canonical_pitch(name):
    """Return the canonical pitch name for case-insensitive input, or None if invalid."""
    if not name: return None
    return next((p for p in ALL_PITCHES if p.lower() == name.strip().lower()), None)

def canonical_weather(name):
    if not name: return None
    return next((w for w in ALL_WEATHER if w.lower() == name.strip().lower()), None)

def _match_is_knockout(m):
    """Group/league rounds are integer; everything else (playoffs/knockouts/super8) is a knockout."""
    if isinstance(m.get("round"), int):
        return False
    return m.get("stage") not in ("group",)

def pick_conditions(is_knockout: bool):
    """Weighted pitch+weather: 90% from the standard pools in group stages, 100% in knockouts."""
    p_chance = 1.0 if is_knockout else 0.9
    pitch = random.choice(GROUP_PITCHES) if (random.random() < p_chance or not _OTHER_PITCHES) else random.choice(_OTHER_PITCHES)
    weather = random.choice(GROUP_WEATHER) if (random.random() < p_chance or not _OTHER_WEATHER) else random.choice(_OTHER_WEATHER)
    return pitch, weather

def assign_tournament_conditions(tourney):
    """Idempotently fill pitch/weather on every scheduled match per the tournament's
    conditions_mode. Safe to call repeatedly — only fills matches missing conditions.
      manual → leave unset (each match asks interactively)
      auto   → weighted pools (group 90% / knockout 100%)
      home   → pitch = home team's (team1) home_pitch; weather = pooled
    """
    mode = tourney.get("conditions_mode", "manual")
    if mode == "manual":
        return
    homes = {t["name"]: t.get("home_pitch") for t in tourney.get("teams", [])}
    for m in tourney.get("schedule", []):
        if m.get("pitch") and m.get("weather"):
            continue
        ko = _match_is_knockout(m)
        if mode == "home":
            hp = canonical_pitch(homes.get(m.get("team1"))) or random.choice(GROUP_PITCHES)
            _, w = pick_conditions(ko)
            m["pitch"], m["weather"] = hp, w
        else:  # auto
            m["pitch"], m["weather"] = pick_conditions(ko)


def _fetch_emoji_img(emoji_str: str, size: int = 40):
    if not emoji_str:
        return None
    s = emoji_str.strip()
    # Base64 data URI (stored from attachment upload)
    if s.startswith("data:image/"):
        try:
            import base64 as _b64
            _data = s.split(",", 1)[1]
            img = Image.open(io.BytesIO(_b64.b64decode(_data))).convert("RGBA")
            return img.resize((size, size), Image.LANCZOS)
        except Exception:
            return None
    # Direct image URL (PNG/JPG logo upload)
    if s.startswith("http://") or s.startswith("https://"):
        try:
            r = requests.get(s, timeout=5)
            if r.status_code == 200:
                img = Image.open(io.BytesIO(r.content)).convert("RGBA")
                return img.resize((size, size), Image.LANCZOS)
        except Exception:
            pass
        return None
    m = re.match(r'<(a?):(\w+):(\d+)>', s)
    if m:
        ext = "gif" if m.group(1) == "a" else "png"
        url = f"https://cdn.discordapp.com/emojis/{m.group(3)}.{ext}"
    else:
        if all(ord(c) < 128 for c in s):
            return None
        codepoints = "-".join(
            f"{ord(c):x}" for c in s
            if ord(c) not in (0xFE0F, 0xFE0E, 0x200D) and ord(c) > 0x7F
        )
        if not codepoints:
            return None
        url = f"https://cdn.jsdelivr.net/gh/twitter/twemoji@v14.0.2/assets/72x72/{codepoints}.png"
    try:
        r = requests.get(url, timeout=5)
        if r.status_code == 200:
            img = Image.open(io.BytesIO(r.content)).convert("RGBA")
            return img.resize((size, size), Image.LANCZOS)
    except Exception:
        pass
    return None

def get_server_tournament(server_id: str):
    if "tournaments" not in DB_CACHE:
        DB_CACHE["tournaments"] = []
    return next((t for t in DB_CACHE["tournaments"] if t.get("server_id") == server_id), None)

def save_tournament(t_data):
    if "tournaments" not in DB_CACHE:
        DB_CACHE["tournaments"] = []
    tourneys = DB_CACHE["tournaments"]
    for i, t in enumerate(tourneys):
        if t.get("server_id") == t_data["server_id"]:
            tourneys[i] = t_data
            async_save_tournament_to_bin()
            return
    tourneys.append(t_data)
    async_save_tournament_to_bin()

def get_tournament_standings(tourney):
    teams = {t["name"]: {"P":0, "W":0, "L":0, "T":0, "Pts":0, "RF":0, "OF":0.0, "RA":0, "OA":0.0} for t in tourney["teams"]}
    for m in tourney.get("schedule", []):
        # Only count Group Stage (integer rounds) for the Points Table!
        if m["status"] == "completed" and "result" in m and isinstance(m.get("round"), int):
            res = m["result"]
            t1, t2 = m["team1"], m["team2"]
            if t1 not in teams: teams[t1] = {"P":0, "W":0, "L":0, "T":0, "Pts":0, "RF":0, "OF":0.0, "RA":0, "OA":0.0}
            if t2 not in teams: teams[t2] = {"P":0, "W":0, "L":0, "T":0, "Pts":0, "RF":0, "OF":0.0, "RA":0, "OA":0.0}

            teams[t1]["P"] += 1; teams[t2]["P"] += 1

            if res["winner"] == "TIE":
                teams[t1]["T"] += 1; teams[t2]["T"] += 1
                teams[t1]["Pts"] += 1; teams[t2]["Pts"] += 1
            elif res["winner"] == t1:
                teams[t1]["W"] += 1; teams[t2]["L"] += 1; teams[t1]["Pts"] += 2
            else:
                teams[t2]["W"] += 1; teams[t1]["L"] += 1; teams[t2]["Pts"] += 2

            def get_overs(w, b, fmt): return float(fmt) if w >= 10 else b / 6.0
            t1_o = get_overs(res["t1_wickets"], res["t1_balls"], res["format_overs"])
            t2_o = get_overs(res["t2_wickets"], res["t2_balls"], res["format_overs"])

            teams[t1]["RF"] += res["t1_runs"]; teams[t1]["OF"] += t1_o
            teams[t1]["RA"] += res["t2_runs"]; teams[t1]["OA"] += t2_o
            teams[t2]["RF"] += res["t2_runs"]; teams[t2]["OF"] += t2_o
            teams[t2]["RA"] += res["t1_runs"]; teams[t2]["OA"] += t1_o

    for _, data in teams.items():
        data["NRR"] = ((data["RF"]/data["OF"]) if data["OF"] > 0 else 0) - ((data["RA"]/data["OA"]) if data["OA"] > 0 else 0)

    return sorted(teams.items(), key=lambda x: (x[1]["Pts"], x[1]["NRR"]), reverse=True)


def get_group_standings(tourney, stage: str, group: str):
    """Standings for a specific stage+group combo (T20 WC group stage or super8)."""
    teams = {}
    for m in tourney.get("schedule", []):
        if m.get("stage") != stage or m.get("group") != group:
            continue
        for name in [m["team1"], m["team2"]]:
            if name not in teams:
                teams[name] = {"P":0,"W":0,"L":0,"T":0,"Pts":0,"RF":0,"OF":0.0,"RA":0,"OA":0.0}

    for m in tourney.get("schedule", []):
        if m.get("stage") != stage or m.get("group") != group:
            continue
        if m["status"] != "completed" or not m.get("result"):
            continue
        res = m["result"]
        t1, t2 = m["team1"], m["team2"]
        teams[t1]["P"] += 1; teams[t2]["P"] += 1
        if res["winner"] == "TIE":
            teams[t1]["T"] += 1; teams[t2]["T"] += 1
            teams[t1]["Pts"] += 1; teams[t2]["Pts"] += 1
        elif res["winner"] == t1:
            teams[t1]["W"] += 1; teams[t2]["L"] += 1; teams[t1]["Pts"] += 2
        else:
            teams[t2]["W"] += 1; teams[t1]["L"] += 1; teams[t2]["Pts"] += 2
        def _ov(w, b, fmt): return float(fmt) if w >= 10 else b / 6.0
        t1_o = _ov(res["t1_wickets"], res["t1_balls"], res["format_overs"])
        t2_o = _ov(res["t2_wickets"], res["t2_balls"], res["format_overs"])
        teams[t1]["RF"] += res["t1_runs"]; teams[t1]["OF"] += t1_o
        teams[t1]["RA"] += res["t2_runs"]; teams[t1]["OA"] += t2_o
        teams[t2]["RF"] += res["t2_runs"]; teams[t2]["OF"] += t2_o
        teams[t2]["RA"] += res["t1_runs"]; teams[t2]["OA"] += t1_o

    for _, d in teams.items():
        d["NRR"] = ((d["RF"]/d["OF"]) if d["OF"] > 0 else 0) - ((d["RA"]/d["OA"]) if d["OA"] > 0 else 0)
    return sorted(teams.items(), key=lambda x: (x[1]["Pts"], x[1]["NRR"]), reverse=True)


def generate_t20wc_points_table(tourney) -> io.BytesIO:
    """Fill super16_table.png template with live group standings for T20 WC group stage."""
    img = Image.open("assets/super16_table.png").convert("RGBA")
    d   = ImageDraw.Draw(img)
    W, H = img.size  # 1508 × 1043

    team_logos = {t["name"]: t.get("logo_standings") or t.get("logo_match") for t in tourney.get("teams", [])}

    _sz = int(H * 0.018)
    _sz_name = int(H * 0.021)
    try:
        font      = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", _sz)
        font_name = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", _sz_name)
    except Exception:
        try:
            font      = ImageFont.truetype("C:/Windows/Fonts/arialbd.ttf", _sz)
            font_name = ImageFont.truetype("C:/Windows/Fonts/arialbd.ttf", _sz_name)
        except Exception:
            font = font_name = ImageFont.load_default()

    DARK = (4, 18, 58)

    def tw(t, f=font): return f.getbbox(t)[2] if hasattr(f, "getbbox") else len(t) * 9
    def th(f=font):
        bb = f.getbbox("Ag") if hasattr(f, "getbbox") else None
        return (bb[3] - bb[1]) if bb else 14

    # Column X centres — aligned to template header labels; right group adds R_OFF
    L_POS_X  =  93
    L_TEAM_X = 130
    L_P_X    = 402
    L_W_X    = 455
    L_L_X    = 503
    L_NR_X   = 563
    L_PTS_X  = 628
    L_NRR_X  = 701
    R_OFF    = 719

    # Row 0 is the col-header row (skip); team data fills rows 1-4
    TOP_ROWS = [398, 449, 500, 553]
    BOT_ROWS = [735, 786, 837, 887]

    EMOJI_SZ = int(H * 0.038)  # ~40px at 1043px template height

    def draw_group(rows, row_ys, right):
        off = R_OFF if right else 0
        txt_h = th()
        name_h = th(font_name)
        for i, (nm, st) in enumerate(rows):
            if i >= len(row_ys):
                break
            cy  = row_ys[i]
            ty  = cy - txt_h // 2
            ty_name = cy - name_h // 2
            nrr_str = f"{st['NRR']:+.2f}"
            logo = _fetch_emoji_img(team_logos.get(nm), EMOJI_SZ)
            team_x = L_TEAM_X + off
            if logo:
                ey = cy - EMOJI_SZ // 2
                img.paste(logo, (team_x, ey), logo)
                team_x += EMOJI_SZ + 6
            # stat columns (smaller font)
            stats = [
                (str(i + 1),            L_POS_X + off, True ),
                (str(st["P"]),          L_P_X  + off,  True ),
                (str(st["W"]),          L_W_X  + off,  True ),
                (str(st["L"]),          L_L_X  + off,  True ),
                (str(st.get("T", 0)),   L_NR_X + off,  True ),
                (str(st["Pts"]),        L_PTS_X + off, True ),
                (nrr_str,               L_NRR_X + off, True ),
            ]
            for text, cx, centered in stats:
                x = (cx - tw(text) // 2) if centered else cx
                d.text((x, ty), text, fill=DARK, font=font)
            # team name — larger font
            d.text((team_x, ty_name), nm[:14].upper(), fill=DARK, font=font_name)

    for grp, right, row_ys in [("A", False, TOP_ROWS), ("B", True, TOP_ROWS),
                                ("C", False, BOT_ROWS), ("D", True, BOT_ROWS)]:
        st = get_group_standings(tourney, "group", grp)
        if st:
            draw_group(st, row_ys, right)

    out_w = 1024
    out_h = int(H * out_w / W)
    final = Image.new("RGB", img.size, (255, 255, 255))
    final.paste(img, mask=img.split()[3])
    final = final.resize((out_w, out_h), Image.LANCZOS)
    buf = io.BytesIO()
    final.save(buf, format="PNG")
    buf.seek(0)
    return buf


def _acl_pt_font(size):
    for p in ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
              "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
              "C:/Windows/Fonts/arialbd.ttf"):
        try:
            return ImageFont.truetype(p, size)
        except Exception:
            continue
    return ImageFont.load_default()


def generate_acl_points_table(tourney) -> io.BytesIO:
    """Fill assets/acl_pointstable.png with the live 14-team ACL standings.
    Highlights: #1 = League Shield (gold), #2-6 = Playoffs zone (green)."""
    DARK = (16, 28, 70)
    ROW_BOUNDS = [305, 344, 383, 420, 457, 493, 530, 567, 604, 642, 680, 718, 756, 794, 842]
    ROW_Y = [(ROW_BOUNDS[i] + ROW_BOUNDS[i + 1]) // 2 for i in range(14)]
    LOGO_CX, LOGO_SZ, NAME_X = 235, 30, 272
    TINT_X0, TINT_X1 = 200, 1576
    PLAYED_X, WON_X, LOST_X, NRR_X, POINTS_X = 787, 955, 1123, 1290, 1474

    standings = get_tournament_standings(tourney)[:14]
    team_logos = {t["name"]: (t.get("logo_match") or t.get("logo_standings")) for t in tourney.get("teams", [])}

    img = Image.open("assets/acl_pointstable.png").convert("RGBA")
    # zone highlights (under the text): #1 Shield = gold, #2-6 playoffs = green
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    for i in range(len(standings)):
        if i == 0:
            col = (255, 196, 0, 70)
        elif i <= 5:
            col = (32, 170, 90, 55)
        else:
            continue
        od.rectangle([TINT_X0, ROW_BOUNDS[i] + 1, TINT_X1, ROW_BOUNDS[i + 1] - 1], fill=col)
    img = Image.alpha_composite(img, overlay)
    d = ImageDraw.Draw(img)

    f_name, f_stat = _acl_pt_font(26), _acl_pt_font(25)

    def tw(t, f):
        return d.textbbox((0, 0), str(t), font=f)[2]

    def th(f):
        bb = d.textbbox((0, 0), "Ag", f)
        return bb[3] - bb[1], bb[1]

    def ctext(cx, y, s, f):
        w = tw(s, f); h, off = th(f)
        d.text((cx - w / 2, y - h / 2 - off), str(s), fill=DARK, font=f)

    def fit(s, max_w, base):
        sz = base; f = _acl_pt_font(sz)
        while tw(s, f) > max_w and sz > 14:
            sz -= 1; f = _acl_pt_font(sz)
        return f

    for i, (name, st) in enumerate(standings):
        y = ROW_Y[i]
        logo = _fetch_emoji_img(team_logos.get(name), LOGO_SZ)
        if logo is not None:
            img.paste(logo, (int(LOGO_CX - LOGO_SZ / 2), int(y - LOGO_SZ / 2)), logo)
            d = ImageDraw.Draw(img)
        nm = name[:24].upper()
        fnm = fit(nm, 700 - NAME_X - 12, 26)
        hh, off = th(fnm)
        d.text((NAME_X, y - hh / 2 - off), nm, fill=DARK, font=fnm)
        ctext(PLAYED_X, y, st["P"], f_stat)
        ctext(WON_X, y, st["W"], f_stat)
        ctext(LOST_X, y, st["L"], f_stat)
        ctext(NRR_X, y, f"{st['NRR']:+.2f}", f_stat)
        ctext(POINTS_X, y, st["Pts"], f_stat)

    out = Image.new("RGB", img.size, (255, 255, 255))
    out.paste(img, mask=img.split()[3])
    buf = io.BytesIO()
    out.save(buf, format="PNG")
    buf.seek(0)
    return buf


def generate_t20wc_super8_table(tourney) -> io.BytesIO:
    """Fill super8_table.png template with live Super 8 group standings."""
    img = Image.open("assets/super8_table.png").convert("RGBA")
    d   = ImageDraw.Draw(img)
    W, H = img.size  # 1484 × 1060

    team_logos = {t["name"]: t.get("logo_standings") or t.get("logo_match") for t in tourney.get("teams", [])}

    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", int(H * 0.018))
    except Exception:
        try:
            font = ImageFont.truetype("C:/Windows/Fonts/arialbd.ttf", int(H * 0.018))
        except Exception:
            font = ImageFont.load_default()

    _sz_name8 = int(H * 0.021)
    try:
        font_name = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", _sz_name8)
    except Exception:
        try:
            font_name = ImageFont.truetype("C:/Windows/Fonts/arialbd.ttf", _sz_name8)
        except Exception:
            font_name = font

    DARK = (4, 18, 58)

    def tw(t, f=font): return f.getbbox(t)[2] if hasattr(f, "getbbox") else len(t) * 9
    def th(f=font):
        bb = f.getbbox("Ag") if hasattr(f, "getbbox") else None
        return (bb[3] - bb[1]) if bb else 14

    # Column X centres — pixel-scanned from super8_table.png (1484px wide, two groups)
    # POS numbers are pre-printed in the template; L_TEAM_X is where logo/name rendering begins
    L_TEAM_X = 105
    L_P_X    = 365
    L_W_X    = 423
    L_L_X    = 480
    L_NR_X   = 539
    L_PTS_X  = 605
    L_NRR_X  = 682
    R_OFF    = 710

    # 4 team rows per group — sub-header at y=425..492, data rows below it
    ROW_YS = [532, 613, 697, 778]

    EMOJI_SZ = int(H * 0.038)

    def draw_group(rows, right):
        off = R_OFF if right else 0
        txt_h = th()
        name_h = th(font_name)
        for i, (nm, st) in enumerate(rows):
            if i >= len(ROW_YS):
                break
            cy  = ROW_YS[i]
            ty  = cy - txt_h // 2
            ty_name = cy - name_h // 2
            nrr_str = f"{st['NRR']:+.2f}"
            logo = _fetch_emoji_img(team_logos.get(nm), EMOJI_SZ)
            team_x = L_TEAM_X + off
            if logo:
                ey = cy - EMOJI_SZ // 2
                img.paste(logo, (team_x, ey), logo)
                team_x += EMOJI_SZ + 6
            # stat columns — POS numbers are pre-printed in template, skip them
            stats = [
                (str(st["P"]),        L_P_X  + off,  True ),
                (str(st["W"]),        L_W_X  + off,  True ),
                (str(st["L"]),        L_L_X  + off,  True ),
                (str(st.get("T", 0)), L_NR_X + off,  True ),
                (str(st["Pts"]),      L_PTS_X + off, True ),
                (nrr_str,             L_NRR_X + off, True ),
            ]
            for text, cx, centered in stats:
                x = (cx - tw(text) // 2) if centered else cx
                d.text((x, ty), text, fill=DARK, font=font)
            # team name — larger font
            d.text((team_x, ty_name), nm[:14].upper(), fill=DARK, font=font_name)

    for sg, right in [("A", False), ("B", True)]:
        st = get_group_standings(tourney, "super8", sg)
        if st:
            draw_group(st, right)

    out_w = 1024
    out_h = int(H * out_w / W)
    final = Image.new("RGB", img.size, (255, 255, 255))
    final.paste(img, mask=img.split()[3])
    final = final.resize((out_w, out_h), Image.LANCZOS)
    buf = io.BytesIO()
    final.save(buf, format="PNG")
    buf.seek(0)
    return buf


def generate_t20wc_knockouts_image(tourney: dict):
    """Fill t20_knockouts.png template with knockout bracket info (T20 WC)."""
    schedule = tourney.get("schedule", [])
    ko_matches = [m for m in schedule if m.get("stage") == "knockout"]
    if not ko_matches:
        return None

    sf1   = next((m for m in ko_matches if m.get("round") == "Semi-Final 1"), None)
    sf2   = next((m for m in ko_matches if m.get("round") == "Semi-Final 2"), None)
    final_m = next((m for m in ko_matches if m.get("round") == "Final"), None)

    img = Image.open("assets/t20_knockouts.png").convert("RGBA")
    d   = ImageDraw.Draw(img)
    W, H = img.size  # 1535 × 1024

    team_logos = {t["name"]: t.get("logo_standings") or t.get("logo_match") for t in tourney.get("teams", [])}

    DARK      = (30, 30, 30)
    WIN_CLR   = (0, 110, 0)
    LOSE_CLR  = (155, 155, 155)

    _sz = 21
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", _sz)
    except Exception:
        try:
            font = ImageFont.truetype("C:/Windows/Fonts/arialbd.ttf", _sz)
        except Exception:
            font = ImageFont.load_default()

    def _tw(t):
        if hasattr(font, "getbbox"):
            bb = font.getbbox(t)
            return bb[2] - bb[0]
        return len(t) * 9

    def _th():
        bb = font.getbbox("Ag") if hasattr(font, "getbbox") else None
        return (bb[3] - bb[1]) if bb else 14

    def draw_team(team_name, logo_cx, logo_cy, name_cx, name_cy, color, emoji_sz):
        logo = _fetch_emoji_img(team_logos.get(team_name), emoji_sz)
        if logo:
            img.paste(logo, (logo_cx - emoji_sz // 2, logo_cy - emoji_sz // 2), logo)
        label = (team_name[:12].upper() if team_name and team_name != "TBD" else "TBD")
        tx = name_cx - _tw(label) // 2
        ty = name_cy - _th() // 2
        d.text((tx, ty), label, fill=color, font=font)

    def draw_match(match, t1_logo_cx, t2_logo_cx, logo_cy, t1_name_cx, t2_name_cx, name_cy, emoji_sz):
        if not match:
            return
        t1   = match.get("team1") or "TBD"
        t2   = match.get("team2") or "TBD"
        res  = match.get("result")
        w    = res.get("winner") if res else None
        c1   = (WIN_CLR if t1 == w else LOSE_CLR) if w else DARK
        c2   = (WIN_CLR if t2 == w else LOSE_CLR) if w else DARK
        draw_team(t1, t1_logo_cx, logo_cy, t1_name_cx, name_cy, c1, emoji_sz)
        draw_team(t2, t2_logo_cx, logo_cy, t2_name_cx, name_cy, c2, emoji_sz)

    # New template layout (1535×1024):
    # SF1 box x=64-479: T1 left-half cx=155, T2 right-half cx=387, VS at x≈271
    # Final box x=566-968: T1 cx=655, T2 cx=879, VS at x≈767
    # SF2 box x=1053-1466: T1 cx=1145, T2 cx=1375, VS at x≈1261
    # All boxes: white interior starts y≈468, VS center y≈583
    # → name above logo: name_cy=490, logo_cy=583 (aligned with VS row)
    draw_match(sf1,
               t1_logo_cx=155,  t2_logo_cx=387,  logo_cy=583,
               t1_name_cx=155,  t2_name_cx=387,  name_cy=490, emoji_sz=100)

    draw_match(final_m,
               t1_logo_cx=655,  t2_logo_cx=879,  logo_cy=583,
               t1_name_cx=655,  t2_name_cx=879,  name_cy=490, emoji_sz=90)

    draw_match(sf2,
               t1_logo_cx=1145, t2_logo_cx=1375, logo_cy=583,
               t1_name_cx=1145, t2_name_cx=1375, name_cy=490, emoji_sz=100)

    out_w  = 1024
    out_h  = int(H * out_w / W)
    out_img = Image.new("RGB", img.size, (255, 255, 255))
    out_img.paste(img, mask=img.split()[3])
    out_img = out_img.resize((out_w, out_h), Image.LANCZOS)
    buf = io.BytesIO()
    out_img.save(buf, format="PNG")
    buf.seek(0)
    return buf


def generate_t20wc_match_banner(tourney: dict, match_data: dict) -> io.BytesIO:
    """Generate pre-match banner using t20_match.png template."""
    t1 = match_data.get("team1", "TBD")
    t2 = match_data.get("team2", "TBD")
    team_logos = {t["name"]: t.get("logo_standings") or t.get("logo_match") for t in tourney.get("teams", [])}

    img = Image.open("assets/t20_match.png").convert("RGBA")
    d   = ImageDraw.Draw(img)
    W, H = img.size  # 1536 × 1024

    _sz      = 52
    _sz_lbl  = 38
    try:
        font     = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", _sz)
        font_lbl = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", _sz_lbl)
    except Exception:
        try:
            font     = ImageFont.truetype("C:/Windows/Fonts/arialbd.ttf", _sz)
            font_lbl = ImageFont.truetype("C:/Windows/Fonts/arialbd.ttf", _sz_lbl)
        except Exception:
            font = font_lbl = ImageFont.load_default()

    def _tw(t, f=None):
        f = f or font
        if hasattr(f, "getbbox"):
            bb = f.getbbox(t); return bb[2] - bb[0]
        return len(t) * 22

    def _th(f=None):
        f = f or font
        bb = f.getbbox("Ag") if hasattr(f, "getbbox") else None
        return (bb[3] - bb[1]) if bb else 30

    EMOJI_SZ = 200
    WHITE    = (255, 255, 255)
    CYAN     = (0, 200, 232)

    def draw_team(name, cx, logo_cy, name_cy):
        logo = _fetch_emoji_img(team_logos.get(name), EMOJI_SZ)
        if logo:
            img.paste(logo, (cx - EMOJI_SZ // 2, logo_cy - EMOJI_SZ // 2), logo)
        label = name[:14].upper()
        d.text((cx - _tw(label) // 2, name_cy - _th() // 2), label, fill=WHITE, font=font)

    # Template layout: VS starburst center (773, 531)
    # Left team safe zone center (478, 531), right team (1070, 531)
    # Name zone below logos at y=700
    draw_team(t1, cx=478,  logo_cy=531, name_cy=700)
    draw_team(t2, cx=1070, logo_cy=531, name_cy=700)

    # Match number + group/round label at y≈810
    stage   = match_data.get("stage", "")
    group   = match_data.get("group", "")
    round_l = match_data.get("round", "")
    mid     = match_data.get("match_id", "")
    if stage == "group" and group:
        label = f"MATCH {mid}  •  GROUP {group}"
    elif stage == "super8" and group:
        label = f"MATCH {mid}  •  SUPER 8 — GROUP {group}"
    elif stage == "knockout" and round_l:
        label = f"MATCH {mid}  •  {round_l.upper()}"
    elif round_l:
        label = f"MATCH {mid}  •  {round_l.upper()}"
    else:
        label = f"MATCH {mid}" if mid else ""
    if label:
        lx = W // 2 - _tw(label, font_lbl) // 2
        d.text((lx, 820), label, fill=CYAN, font=font_lbl)

    out_w   = 1024
    out_h   = int(H * out_w / W)
    out_img = Image.new("RGB", img.size, (0, 3, 24))
    out_img.paste(img, mask=img.split()[3])
    out_img = out_img.resize((out_w, out_h), Image.LANCZOS)
    buf = io.BytesIO()
    out_img.save(buf, format="PNG")
    buf.seek(0)
    return buf


def _build_status_pages(tourney):
    """Returns list of (title, stage_type, group_key, matches) tuples."""
    schedule = tourney.get("schedule", [])
    t_type = tourney.get("tournament_type", "round_robin")
    pages = []

    if t_type == "t20_world_cup":
        for grp in ["A", "B", "C", "D"]:
            matches = [m for m in schedule if m.get("stage") == "group" and m.get("group") == grp]
            if matches:
                pages.append((f"Group {grp}", "group", grp, matches))
        for sg in ["A", "B"]:
            matches = [m for m in schedule if m.get("stage") == "super8" and m.get("group") == sg]
            if matches:
                pages.append((f"Super 8 — Group {sg}", "super8", sg, matches))
        ko = [m for m in schedule if m.get("stage") == "knockout"]
        if ko:
            pages.append(("Knockouts", "knockout", None, ko))
    else:
        rounds = sorted(set(m["round"] for m in schedule if isinstance(m.get("round"), int)))
        for r in rounds:
            matches = [m for m in schedule if m.get("round") == r]
            pages.append((f"Round {r}", "round", r, matches))
        ko = [m for m in schedule if not isinstance(m.get("round"), int)]
        if ko:
            pages.append(("Knockouts", "knockout", None, ko))

    return pages


_FLAT_PAGE_SIZE = 10

def _build_flat_pages(tourney):
    """Flat pages sorted by match_id — used by cvt status for T20 WC."""
    schedule = sorted(tourney.get("schedule", []), key=lambda m: m["match_id"])
    pages = []
    for i in range(0, len(schedule), _FLAT_PAGE_SIZE):
        chunk = schedule[i:i + _FLAT_PAGE_SIZE]
        first_id = chunk[0]["match_id"]
        last_id = chunk[-1]["match_id"]
        pages.append((f"Fixtures #{first_id}–#{last_id}", "flat", None, chunk))
    return pages


def _conditions_label(m):
    """Short pitch · weather tag for a scheduled match (manual matches show 'at match')."""
    p, w = m.get("pitch"), m.get("weather")
    if p and w:
        return f"🏟️ {p} · 🌤️ {w}"
    return "🏟️ *picked at match*"


def _build_status_embed(tourney, page_info):
    """Build the embed for one status page."""
    title, stage_type, group_key, matches = page_info
    embed = discord.Embed(
        title=f"🏆 {tourney['name']} — {title}",
        color=discord.Color.gold()
    )

    lines = []
    for m in matches:
        # Stage tag for flat view
        if stage_type == "flat":
            ms, mg = m.get("stage", ""), m.get("group", "")
            if ms == "group" and mg:
                tag = f"[G{mg}] "
            elif ms == "super8" and mg:
                tag = f"[S8{mg}] "
            elif ms == "knockout":
                rn = str(m.get("round", ""))
                tag = "[SF] " if "Semi" in rn else "[F] " if "Final" in rn else "[KO] "
            else:
                tag = ""
        else:
            tag = ""

        if m["status"] == "completed" and m.get("result"):
            r = m["result"]
            w = r["winner"]
            t1b = f"**{m['team1']}**" if w == m["team1"] else m["team1"]
            t2b = f"**{m['team2']}**" if w == m["team2"] else m["team2"]
            lines.append(f"`#{m['match_id']}` {tag}{t1b} {r['t1_runs']}/{r['t1_wickets']} vs {t2b} {r['t2_runs']}/{r['t2_wickets']} ✅")
        else:
            # ACL playoff slots may be unresolved (None) — show their TBD source label
            a = m.get("team1") or f"*{m.get('team1_src', 'TBD')}*"
            b = m.get("team2") or f"*{m.get('team2_src', 'TBD')}*"
            icon = "🔒" if m["status"] == "locked" else "⏳"
            lines.append(f"`#{m['match_id']}` {tag}{a} vs {b} {icon}\n     └ {_conditions_label(m)}")
    # Split into multiple fields if content exceeds Discord's 1024-char limit
    chunks, current = [], []
    current_len = 0
    for line in lines:
        if current_len + len(line) + 1 > 1020 and current:
            chunks.append("\n".join(current))
            current, current_len = [], 0
        current.append(line)
        current_len += len(line) + 1
    if current:
        chunks.append("\n".join(current))
    if not chunks:
        chunks = ["No matches"]
    for i, chunk in enumerate(chunks):
        embed.add_field(name="Matches" if i == 0 else "​", value=chunk, inline=False)

    if stage_type in ("group", "super8") and group_key:
        st = get_group_standings(tourney, stage_type, group_key)
        if st:
            rows = ["```", f"{'':2}{'#':<3}{'Team':<20}{'P':>2}{'W':>2}{'L':>2}{'Pts':>4}{'NRR':>7}", "─"*44]
            for i, (nm, d) in enumerate(st, 1):
                arrow = "→ " if i <= 2 else "  "
                rows.append(f"{arrow}{i:<3}{nm[:18]:<20}{d['P']:>2}{d['W']:>2}{d['L']:>2}{d['Pts']:>4}{d['NRR']:>+7.2f}")
            rows.append("```")
            label = "Standings  (→ top 2 advance)" if stage_type == "group" else "Super 8 Standings  (→ top 2 to SF)"
            embed.add_field(name=label, value="\n".join(rows), inline=False)

    pending = sum(1 for m in matches if m["status"] == "pending")
    done = sum(1 for m in matches if m["status"] == "completed")
    locked = sum(1 for m in matches if m["status"] == "locked")
    foot = f"✅ {done} completed  ·  ⏳ {pending} ready" + (f"  ·  🔒 {locked} locked" if locked else "") + f"  ·  {tourney.get('format_overs', 20)} overs"
    embed.set_footer(text=foot)
    return embed


class TournamentStatusView(discord.ui.View):
    def __init__(self, tourney, pages):
        super().__init__(timeout=120)
        self.tourney = tourney
        self.pages = pages
        self.show_banner = (tourney.get("tournament_type") == "t20_world_cup")
        self.idx = 0
        for i, (_, _, _, matches) in enumerate(pages):
            if any(m["status"] == "pending" for m in matches):
                self.idx = i
                break
        else:
            self.idx = max(0, len(pages) - 1)
        self._update_nav()

    def _update_nav(self):
        self.prev_btn.disabled = (self.idx == 0)
        self.next_btn.disabled = (self.idx >= len(self.pages) - 1)
        self.page_btn.label = f"{self.idx + 1} / {len(self.pages)}"

    def _make_embed(self):
        embed = _build_status_embed(self.tourney, self.pages[self.idx])
        if self.show_banner:
            embed.set_image(url="attachment://t20_banner.png")
        return embed

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True

    @discord.ui.button(label="◀", style=discord.ButtonStyle.secondary, row=0)
    async def prev_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.idx -= 1
        self._update_nav()
        if self.show_banner:
            await interaction.response.edit_message(
                embed=self._make_embed(),
                attachments=[discord.File("assets/t20_banner.png")],
                view=self)
        else:
            await interaction.response.edit_message(embed=self._make_embed(), view=self)

    @discord.ui.button(label="1 / 1", style=discord.ButtonStyle.secondary, disabled=True, row=0)
    async def page_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()

    @discord.ui.button(label="▶", style=discord.ButtonStyle.secondary, row=0)
    async def next_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.idx += 1
        self._update_nav()
        if self.show_banner:
            await interaction.response.edit_message(
                embed=self._make_embed(),
                attachments=[discord.File("assets/t20_banner.png")],
                view=self)
        else:
            await interaction.response.edit_message(embed=self._make_embed(), view=self)


class T20StandingsView(discord.ui.View):
    """◀ / ▶ navigation through Group Stage → Super 8 → Knockouts standings images."""

    def __init__(self, pages: list, *, start_idx: int = 0):
        super().__init__(timeout=120)
        # pages: list of (label, filename, buf)
        self.pages = pages
        self.idx   = start_idx
        self._update_nav()

    def _update_nav(self):
        self.prev_btn.disabled = (self.idx == 0)
        self.next_btn.disabled = (self.idx >= len(self.pages) - 1)
        self.page_btn.label    = self.pages[self.idx][0]

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True

    @discord.ui.button(label="◀", style=discord.ButtonStyle.secondary)
    async def prev_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.idx -= 1
        self._update_nav()
        label, fname, buf = self.pages[self.idx]
        buf.seek(0)
        await interaction.response.edit_message(
            attachments=[discord.File(fp=buf, filename=fname)], view=self)

    @discord.ui.button(label="...", style=discord.ButtonStyle.secondary, disabled=True)
    async def page_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()

    @discord.ui.button(label="▶", style=discord.ButtonStyle.secondary)
    async def next_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.idx += 1
        self._update_nav()
        label, fname, buf = self.pages[self.idx]
        buf.seek(0)
        await interaction.response.edit_message(
            attachments=[discord.File(fp=buf, filename=fname)], view=self)


class LeaderboardView(discord.ui.View):
    """◀ / ▶ paginated leaderboard — shows up to `len(lines)` entries, 10 per page.
    Used by the runs / wickets / MVP leaderboards (first 50, 5 pages)."""

    def __init__(self, title: str, header: str, lines: list, per_page: int = 10):
        super().__init__(timeout=180)
        self.title = title
        self.header = header
        self.lines = lines
        self.per_page = per_page
        self.pages_total = max(1, (len(lines) + per_page - 1) // per_page)
        self.idx = 0
        self._update_nav()

    def _update_nav(self):
        self.prev_btn.disabled = (self.idx == 0)
        self.next_btn.disabled = (self.idx >= self.pages_total - 1)
        self.page_btn.label = f"{self.idx + 1} / {self.pages_total}"

    def make_embed(self):
        embed = discord.Embed(title=self.title, color=discord.Color.gold())
        chunk = self.lines[self.idx * self.per_page: self.idx * self.per_page + self.per_page]
        body = "\n".join(chunk) if chunk else "No players qualify for this leaderboard yet."
        embed.description = (self.header + "\n" if self.header else "") + body
        embed.set_footer(text=f"Page {self.idx + 1}/{self.pages_total} · Top {len(self.lines)}")
        return embed

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True

    @discord.ui.button(label="◀", style=discord.ButtonStyle.secondary)
    async def prev_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.idx = max(0, self.idx - 1)
        self._update_nav()
        await interaction.response.edit_message(embed=self.make_embed(), view=self)

    @discord.ui.button(label="1 / 1", style=discord.ButtonStyle.secondary, disabled=True)
    async def page_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()

    @discord.ui.button(label="▶", style=discord.ButtonStyle.secondary)
    async def next_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.idx = min(self.pages_total - 1, self.idx + 1)
        self._update_nav()
        await interaction.response.edit_message(embed=self.make_embed(), view=self)


# ══════════════════════════════════════════════════════════════════════════
#  ACL — Akatsuki Cricket League: playoff + Super Cup engine
#  League (91) → Top-6 Playoffs (6 matches) → Akatsuki Super Cup.
#  Structure note: the moment the league ends, the #1 (League Shield) is
#  already locked into the Super Cup — so the Super Cup is created up front
#  with one finalist known and the other (ACL Trophy Winner) as TBD.
# ══════════════════════════════════════════════════════════════════════════
ACL_PLAYOFF_STAGE  = "acl_playoff"
ACL_SUPERCUP_STAGE = "acl_supercup"
ACL_KO_STAGES      = (ACL_PLAYOFF_STAGE, ACL_SUPERCUP_STAGE)
# Logical render order of the bracket rounds
ACL_PLAYOFF_ORDER  = ["Qualifier", "Eliminator 1", "Eliminator 2", "The Knockout", "Qualifier 2", "Grand Final"]


def _acl_next_mid(tourney):
    """Next contiguous match_id (preserves the id == schedule-index+1 invariant)."""
    return max((m["match_id"] for m in tourney.get("schedule", [])), default=0) + 1


def _acl_get(tourney, round_name):
    return next((m for m in tourney.get("schedule", [])
                 if m.get("stage") in ACL_KO_STAGES and m.get("round") == round_name), None)


def _acl_winner_loser(m):
    """(winner, loser) team names for a completed knockout match, else (None, None)."""
    if not m or m.get("status") != "completed":
        return None, None
    res = m.get("result") or {}
    w = res.get("winner")
    if not w or w == "TIE":
        return None, None
    l = res.get("loser") or (m["team2"] if w == m["team1"] else m["team1"])
    return w, l


def _acl_fill(m, slot, team):
    """Fill a TBD slot on a locked match; flip it to 'pending' once both teams are known."""
    if not m or not team:
        return
    if m.get(slot) is None:
        m[slot] = team
    if m.get("team1") and m.get("team2") and m.get("status") == "locked":
        m["status"] = "pending"


def acl_generate_playoffs(tourney):
    """Build the Top-6 playoff bracket + Super Cup. Returns (ok: bool, message: str).
    Idempotent: refuses if already generated or the league isn't finished."""
    if tourney.get("tournament_type") != "acl":
        return False, "This isn't an ACL tournament."
    league = [m for m in tourney.get("schedule", []) if m.get("stage") == "league"]
    if not league:
        return False, "No league schedule found. Start the tournament first."
    remaining = sum(1 for m in league if m.get("status") != "completed")
    if remaining:
        return False, f"❌ Cannot start the Playoffs yet — **{remaining}** league match(es) still pending."
    if any(m.get("stage") in ACL_KO_STAGES for m in tourney["schedule"]):
        return False, "❌ ACL Playoffs have already been generated."

    standings = get_tournament_standings(tourney)
    seeds = [n for n, _ in standings if n != "BYE"]
    if len(seeds) < 6:
        return False, "❌ Need at least 6 teams to run the Playoffs."
    s1, s2, s3, s4, s5, s6 = seeds[:6]
    tourney["league_shield"] = s1            # #1 → direct Super Cup spot
    tourney["playoff_seeds"] = seeds[:6]

    mid = _acl_next_mid(tourney)
    def mk(rnd, stage, t1, t2, t1s, t2s, status):
        nonlocal mid
        tourney["schedule"].append({
            "match_id": mid, "round": rnd, "stage": stage,
            "team1": t1, "team2": t2, "team1_src": t1s, "team2_src": t2s,
            "status": status, "result": None,
        })
        mid += 1

    # Round 1 — teams known, ready to play (any order / in parallel)
    mk("Qualifier",        ACL_PLAYOFF_STAGE,  s1, s2,   "1st · League", "2nd · League", "pending")
    mk("Eliminator 1",     ACL_PLAYOFF_STAGE,  s3, s6,   "3rd · League", "6th · League", "pending")
    mk("Eliminator 2",     ACL_PLAYOFF_STAGE,  s4, s5,   "4th · League", "5th · League", "pending")
    # Knockouts — locked until feeders resolve
    mk("The Knockout", ACL_PLAYOFF_STAGE,  None, None, "Winner · Eliminator 1", "Winner · Eliminator 2", "locked")
    mk("Qualifier 2",      ACL_PLAYOFF_STAGE,  None, None, "Loser · Qualifier",     "Winner · The Knockout", "locked")
    mk("Grand Final",      ACL_PLAYOFF_STAGE,  None, None, "Winner · Qualifier",    "Winner · Qualifier 2", "locked")
    # Super Cup apex — Shield finalist locked in already; opponent = ACL Trophy Winner (TBD)
    mk("Super Cup",        ACL_SUPERCUP_STAGE, s1, None,  "League Shield",          "ACL Trophy Winner", "locked")

    assign_tournament_conditions(tourney)   # knockouts get 100%-pool conditions up front
    save_tournament(tourney)
    return True, "ok"


def _acl_try_advance(tourney):
    """Resolve TBD slots as feeder matches complete; branch the Super Cup on a Domestic Double.
    Safe to call after every match completion (idempotent)."""
    if tourney.get("tournament_type") != "acl":
        return
    q  = _acl_get(tourney, "Qualifier")
    if not q:
        return  # playoffs not generated yet
    e1 = _acl_get(tourney, "Eliminator 1")
    e2 = _acl_get(tourney, "Eliminator 2")
    ef = _acl_get(tourney, "The Knockout")
    sf = _acl_get(tourney, "Qualifier 2")
    gf = _acl_get(tourney, "Grand Final")
    sc = _acl_get(tourney, "Super Cup")

    # Qualifier → winner to Grand Final, loser to Qualifier 2
    qw, ql = _acl_winner_loser(q)
    if qw:
        _acl_fill(gf, "team1", qw)
        _acl_fill(sf, "team1", ql)
    # Eliminators → The Knockout
    e1w, _ = _acl_winner_loser(e1)
    e2w, _ = _acl_winner_loser(e2)
    if e1w: _acl_fill(ef, "team1", e1w)
    if e2w: _acl_fill(ef, "team2", e2w)
    # The Knockout → Qualifier 2 (slot 2)
    efw, _ = _acl_winner_loser(ef)
    if efw: _acl_fill(sf, "team2", efw)
    # Qualifier 2 → Grand Final (slot 2)
    sfw, _ = _acl_winner_loser(sf)
    if sfw: _acl_fill(gf, "team2", sfw)

    # Grand Final → ACL Trophy Winner, then Super Cup branch
    trophy, runner_up = _acl_winner_loser(gf)
    if trophy:
        tourney["acl_trophy_winner"] = trophy
        tourney["acl_runner_up"] = runner_up
        shield = tourney.get("league_shield")
        slq = _acl_get(tourney, "Super League Qualifier")
        if trophy == shield:
            # DOMESTIC DOUBLE → Super League Qualifier decides the Super Cup challenger
            if not slq:
                seeds = tourney.get("playoff_seeds", [])
                second = seeds[1] if len(seeds) > 1 else None
                # if #2 league team IS the playoffs runner-up, fall back to #3
                entrant = second if (second and second != runner_up) else (seeds[2] if len(seeds) > 2 else None)
                tourney["schedule"].append({
                    "match_id": _acl_next_mid(tourney), "round": "Super League Qualifier",
                    "stage": ACL_SUPERCUP_STAGE, "team1": entrant, "team2": runner_up,
                    "team1_src": "2nd · League", "team2_src": "Playoffs Runner-Up",
                    "status": "pending" if (entrant and runner_up) else "locked", "result": None,
                })
                if sc:
                    sc["team2_src"] = "Winner · Super League Qualifier"
        else:
            _acl_fill(sc, "team2", trophy)

    # Super League Qualifier → Super Cup challenger
    slqw, _ = _acl_winner_loser(_acl_get(tourney, "Super League Qualifier"))
    if slqw:
        _acl_fill(sc, "team2", slqw)

    # Super Cup complete → crown the champion
    champ, _ = _acl_winner_loser(sc)
    if champ:
        tourney["acl_champion"] = champ
        if tourney.get("status") != "completed":
            tourney["status"] = "completed"


def _acl_side(m, slot):
    """Display string for one side of a bracket match: team name if known, else its TBD source."""
    team = m.get(slot)
    if team:
        return team
    return f"*{m.get(slot + '_src', 'TBD')}*"


def _acl_match_line(m):
    icon = {"locked": "🔒", "pending": "🟢", "completed": "✅"}.get(m.get("status"), "•")
    a, b = _acl_side(m, "team1"), _acl_side(m, "team2")
    line = f"{icon} **{m['round']}**  ·  #{m['match_id']}\n    {a}  🆚  {b}"
    if m.get("status") == "completed" and m.get("result"):
        line += f"  →  🏆 **{m['result']['winner']}**"
    return line


def acl_bracket_embed(tourney):
    """One combined view: Super Cup apex (Shield finalist already in) + the full playoff tree."""
    shield = tourney.get("league_shield")
    champ  = tourney.get("acl_champion")
    color  = discord.Color.from_rgb(200, 30, 40)
    title  = f"🔴 {tourney.get('name', 'ACL')} — Road to the Super Cup"
    e = discord.Embed(title=title, color=color)

    sc  = _acl_get(tourney, "Super Cup")
    slq = _acl_get(tourney, "Super League Qualifier")

    # Apex: the Super Cup
    if sc:
        apex = _acl_match_line(sc)
        if slq:
            apex += "\n\n🔥 *Domestic Double!* — challenger decided by the Super League Qualifier:\n" + _acl_match_line(slq)
        e.add_field(name="👑 AKATSUKI SUPER CUP", value=apex, inline=False)

    if shield:
        e.description = f"🛡️ **League Shield:** {shield}  ·  *direct Super Cup finalist*" + (
            f"\n🏆 **ACL Champion:** {champ}" if champ else "")

    # Playoff tree
    r1 = [_acl_get(tourney, r) for r in ["Qualifier", "Eliminator 1", "Eliminator 2"]]
    r2 = [_acl_get(tourney, r) for r in ["The Knockout", "Qualifier 2", "Grand Final"]]
    if any(r1):
        e.add_field(name="🏏 Playoffs — Openers", value="\n".join(_acl_match_line(m) for m in r1 if m), inline=False)
    if any(r2):
        e.add_field(name="🏆 Playoffs — Knockouts → ACL Trophy",
                    value="\n".join(_acl_match_line(m) for m in r2 if m), inline=False)

    e.set_footer(text="🔒 locked (awaiting feeders) · 🟢 ready to play · ✅ done")
    return e


# ── Fixtures & owner-launch (shared by slash + prefix) ───────────────────────
def _tm_round_label(m):
    rnd = m.get("round")
    if isinstance(rnd, int):
        return f"Round {rnd}"
    return str(rnd) if rnd else "Match"


def match_owner_ids(tourney, match):
    """Owner user-ids (as strings) of the two teams currently in a scheduled match."""
    ids = set()
    for tname in (match.get("team1"), match.get("team2")):
        if not tname:
            continue
        tm = next((t for t in tourney.get("teams", []) if t["name"] == tname), None)
        if tm and tm.get("owner_id"):
            ids.add(str(tm["owner_id"]))
    return ids


def owner_can_launch(tourney, match, user_id, is_manager=False):
    """A match may be launched by a manager (any match) or by an owner of either team in it."""
    return bool(is_manager) or str(user_id) in match_owner_ids(tourney, match)


def build_team_fixtures_embed(tourney, team_name):
    """Embed of one team's fixtures: Upcoming (with launchable hint) + Results."""
    sched = tourney.get("schedule", [])
    mine = [m for m in sched if m.get("team1") == team_name or m.get("team2") == team_name]
    is_acl = tourney.get("tournament_type") == "acl"
    color = discord.Color.from_rgb(200, 30, 40) if is_acl else discord.Color.blurple()
    e = discord.Embed(title=f"📋 {team_name} — Fixtures", color=color)

    upcoming, results = [], []
    won = lost = tied = 0
    for m in sched:
        if m.get("team1") != team_name and m.get("team2") != team_name:
            continue
        is_t1 = (m.get("team1") == team_name)
        opp = m.get("team2") if is_t1 else m.get("team1")
        opp_src = (m.get("team2_src") if is_t1 else m.get("team1_src"))
        rlabel = _tm_round_label(m)
        if m["status"] == "completed" and m.get("result"):
            r = m["result"]; w = r.get("winner")
            if is_t1:
                my_r, my_w, op_r, op_w = r["t1_runs"], r["t1_wickets"], r["t2_runs"], r["t2_wickets"]
            else:
                my_r, my_w, op_r, op_w = r["t2_runs"], r["t2_wickets"], r["t1_runs"], r["t1_wickets"]
            if w == "TIE": outcome = "🟰 Tie"; tied += 1
            elif w == team_name: outcome = "✅ Won"; won += 1
            else: outcome = "❌ Lost"; lost += 1
            results.append(f"`#{m['match_id']}` {rlabel} · vs **{opp}**  {my_r}/{my_w} : {op_r}/{op_w}  {outcome}")
        elif m["status"] == "locked":
            upcoming.append(f"`#{m['match_id']}` {rlabel} · vs *{opp_src or 'TBD'}*  🔒 awaiting earlier results\n     └ {_conditions_label(m)}")
        else:  # pending → launchable
            upcoming.append(f"`#{m['match_id']}` {rlabel} · vs **{opp}**  🟢 ready — `cvt play {m['match_id']}`\n     └ {_conditions_label(m)}")

    def _add(title, lines):
        if not lines:
            return
        chunk, cur = [], 0
        first = True
        for ln in lines:
            if cur + len(ln) + 1 > 1000 and chunk:
                e.add_field(name=title if first else "​", value="\n".join(chunk), inline=False)
                chunk, cur, first = [], 0, False
            chunk.append(ln); cur += len(ln) + 1
        if chunk:
            e.add_field(name=title if first else "​", value="\n".join(chunk), inline=False)

    _add(f"🟢 Upcoming ({len(upcoming)})", upcoming or ["—"])
    _add(f"📕 Results ({won}W·{lost}L{'·' + str(tied) + 'T' if tied else ''})", results)
    if not mine:
        e.description = "No fixtures yet for this team."
    e.set_footer(text="Owners can launch any of their own 🟢 matches with  cvt play <id>")
    return e


class TournamentCog(commands.GroupCog, group_name="tournament"):
    def __init__(self, bot):
        self.bot = bot

    def is_manager(self, interaction: discord.Interaction, tourney):
        if interaction.user.id == 1087369198801526836: return True
        if interaction.user.guild_permissions.administrator: return True
        return str(interaction.user.id) in tourney.get("managers", [])

    @app_commands.command(name="create", description="[ADMIN] Create a new tournament for this server.")
    @app_commands.choices(format=[
        app_commands.Choice(name="T20 (20 Overs)", value="20"),
        app_commands.Choice(name="ODI (50 Overs)", value="50"),
        app_commands.Choice(name="Test (90 Overs/Inn)", value="90"),
        app_commands.Choice(name="Custom Format", value="custom")
    ])
    @app_commands.choices(event_type=[
        app_commands.Choice(name="Round Robin", value="round_robin"),
        app_commands.Choice(name="T20 World Cup (4 Groups → Super 8 → Final)", value="t20_world_cup"),
        app_commands.Choice(name="ACL (14 Teams → League → Playoffs → Super Cup)", value="acl"),
    ])
    @app_commands.choices(conditions=[
        app_commands.Choice(name="Manual — pick pitch & weather each match", value="manual"),
        app_commands.Choice(name="Auto — random conditions per match (weighted)", value="auto"),
        app_commands.Choice(name="Home Pitch — each match on the home team's pitch", value="home"),
    ])
    async def create(self, interaction: discord.Interaction, name: str, format: app_commands.Choice[str], event_type: app_commands.Choice[str] = None, min_squad: int = 11, max_squad: int = 15, impact_player: bool = False, injuries: bool = False, custom_overs: int = None, conditions: app_commands.Choice[str] = None):
        if not interaction.user.guild_permissions.administrator and interaction.user.id != 1087369198801526836:
            return await interaction.response.send_message("❌ Only Server Admins can initialize a tournament.", ephemeral=True)

        server_id = str(interaction.guild.id)

        _, _, _, s_tier, _ = get_tier_status(str(interaction.user.id), server_id)
        if s_tier not in ["Gold", "Diamond"]:
            return await interaction.response.send_message("❌ **Access Denied:** Only servers with an active **Gold** or **Diamond** tier can host tournaments! Contact the bot owner to upgrade.", ephemeral=True)

        if get_server_tournament(server_id):
            return await interaction.response.send_message("❌ A tournament already exists in this server! Use `/tournament status` to check.", ephemeral=True)

        if format.value == "custom" and not custom_overs:
            return await interaction.response.send_message("❌ You must provide `custom_overs` if selecting Custom Format.", ephemeral=True)

        if format.value != "custom": custom_overs = int(format.value)
        if min_squad < 11: return await interaction.response.send_message("❌ Minimum squad size must be at least 11.", ephemeral=True)
        if impact_player and min_squad < 12: return await interaction.response.send_message("❌ Minimum squad size must be at least 12 if Impact Player is enabled.", ephemeral=True)
        if max_squad < min_squad: return await interaction.response.send_message("❌ Max squad size cannot be less than Min squad size.", ephemeral=True)

        t_type = event_type.value if event_type else "round_robin"
        type_label = {"t20_world_cup": "T20 World Cup", "acl": "Akatsuki Cricket League"}.get(t_type, "Round Robin")

        t_data = {
            "server_id": server_id,
            "name": name,
            "managers": [str(interaction.user.id)],
            "teams": [],
            "status": "registration",
            "schedule": [],
            "current_match_idx": 0,
            "stats": {},
            "format_overs": custom_overs,
            "min_squad": min_squad,
            "max_squad": max_squad,
            "impact_player": impact_player,
            "injuries_enabled": injuries,
            "tournament_type": t_type,
            "conditions_mode": (conditions.value if conditions else "manual"),
        }
        save_tournament(t_data)

        extra = ""
        if t_type == "t20_world_cup":
            extra = "\n⚠️ **T20 World Cup requires exactly 16 teams (4 groups of 4). Assign each team a group (A/B/C/D) when using `/tournament add_team`.**"
        elif t_type == "acl":
            extra = "\n🔴 **ACL requires exactly 14 teams.** Each plays every other once (91 league matches) → Top 6 Playoffs → Super Cup. No groups needed — just `/tournament add_team` for all 14."
        if t_data["conditions_mode"] == "auto":
            extra += "\n🎲 **Conditions: Auto** — pitch & weather auto-assigned per match."
        elif t_data["conditions_mode"] == "home":
            extra += "\n🏟️ **Conditions: Home Pitch** — set each team's home pitch with `/tournament set_home_pitch` (or `cvt set_home_pitch`). The tournament can't start until **all** teams have one."
        await interaction.response.send_message(
            f"🏆 **Tournament Created:** `{name}`  ·  {type_label}\nYou have been automatically assigned as a Manager.\nUse `/tournament add_manager` or `/tournament add_team` to get started!{extra}"
        )

    @app_commands.command(name="add_manager", description="[MANAGER] Assign a tournament manager.")
    async def add_manager(self, interaction: discord.Interaction, user: discord.Member):
        server_id = str(interaction.guild.id)
        tourney = get_server_tournament(server_id)
        if not tourney: return await interaction.response.send_message("❌ No tournament exists here.", ephemeral=True)
        if not self.is_manager(interaction, tourney): return await interaction.response.send_message("❌ You are not a Tournament Manager.", ephemeral=True)
        uid = str(user.id)
        if uid not in tourney["managers"]:
            tourney["managers"].append(uid)
            save_tournament(tourney)
        await interaction.response.send_message(f"✅ {user.mention} is now a Tournament Manager!")

    @app_commands.command(name="add_team", description="[MANAGER] Add a team and assign a Team Owner.")
    async def add_team(self, interaction: discord.Interaction, team_name: str, owner: discord.Member, group: str = None):
        server_id = str(interaction.guild.id)
        tourney = get_server_tournament(server_id)
        if not tourney: return await interaction.response.send_message("❌ No tournament exists.", ephemeral=True)
        if not self.is_manager(interaction, tourney): return await interaction.response.send_message("❌ Managers only.", ephemeral=True)
        if tourney["status"] != "registration": return await interaction.response.send_message("❌ Cannot add teams after tournament has started.", ephemeral=True)

        t_type = tourney.get("tournament_type", "round_robin")
        group_val = None
        if t_type == "t20_world_cup":
            if not group:
                return await interaction.response.send_message("❌ **Group (A/B/C/D) is required** for T20 World Cup tournaments. Use the `group` parameter.", ephemeral=True)
            group_val = group.strip().upper()
            if group_val not in ["A", "B", "C", "D"]:
                return await interaction.response.send_message("❌ Group must be **A**, **B**, **C**, or **D**.", ephemeral=True)
            group_count = sum(1 for t in tourney["teams"] if t.get("group") == group_val)
            if group_count >= 4:
                return await interaction.response.send_message(f"❌ Group **{group_val}** already has 4 teams.", ephemeral=True)

        for t in tourney["teams"]:
            if t["name"].lower() == team_name.lower():
                return await interaction.response.send_message("❌ Team name already exists.", ephemeral=True)

        tourney["teams"].append({
            "name": team_name,
            "owner_id": str(owner.id),
            "squad": [],
            "group": group_val,
        })
        save_tournament(tourney)
        grp_txt = f" · Group **{group_val}**" if group_val else ""
        await interaction.response.send_message(f"✅ Team **{team_name}**{grp_txt} added!\n👤 Owner: {owner.mention}\n*The owner can now use `/tournament submit_squad` to register their players.*")

    @app_commands.command(name="remove_team", description="[MANAGER] Remove a team from the tournament.")
    async def remove_team(self, interaction: discord.Interaction, team_name: str):
        server_id = str(interaction.guild.id)
        tourney = get_server_tournament(server_id)
        if not tourney: return await interaction.response.send_message("❌ No tournament exists.", ephemeral=True)
        if not self.is_manager(interaction, tourney): return await interaction.response.send_message("❌ Managers only.", ephemeral=True)
        if tourney["status"] != "registration": return await interaction.response.send_message("❌ Cannot remove teams after the tournament has started.", ephemeral=True)
        team_idx = next((i for i, t in enumerate(tourney["teams"]) if t["name"].lower() == team_name.lower()), None)
        if team_idx is None:
            return await interaction.response.send_message(f"❌ Team **{team_name}** not found.", ephemeral=True)
        del tourney["teams"][team_idx]
        save_tournament(tourney)
        await interaction.response.send_message(f"✅ Team **{team_name}** has been successfully removed from the tournament.")

    # NOTE: transfer_team & replace_player are prefix-only (cvt transfer_team / cvt replace_player)
    # to stay under Discord's 25-subcommand limit on the /tournament group.

    @app_commands.command(name="submit_squad", description="[OWNER/MANAGER] Submit a tournament squad (15 players).")
    async def submit_squad(self, interaction: discord.Interaction, team_name: str = None):
        server_id = str(interaction.guild.id)
        tourney = get_server_tournament(server_id)
        if not tourney: return await interaction.response.send_message("❌ No tournament exists.", ephemeral=True)
        if tourney["status"] != "registration": return await interaction.response.send_message("❌ Registration is closed.", ephemeral=True)
        is_mgr = self.is_manager(interaction, tourney)
        if team_name:
            if not is_mgr:
                return await interaction.response.send_message("❌ Only Managers can use the team_name parameter to submit for others.", ephemeral=True)
            team = next((t for t in tourney["teams"] if t["name"].lower() == team_name.lower()), None)
            if not team: return await interaction.response.send_message(f"❌ Team '{team_name}' not found.", ephemeral=True)
        else:
            team = next((t for t in tourney["teams"] if t["owner_id"] == str(interaction.user.id)), None)
            if not team: return await interaction.response.send_message("❌ You do not own a team. Managers must provide the `team_name` parameter.", ephemeral=True)
        min_s = tourney.get("min_squad", 11)
        max_s = tourney.get("max_squad", 15)
        await interaction.response.send_message(f"📋 Please reply to this message with the **{min_s} to {max_s} Player Squad** for **{team['name']}** (One player name per line). You have 3 minutes.", ephemeral=True)
        def check(m):
            return m.author.id == interaction.user.id and m.channel.id == interaction.channel.id
        try:
            msg = await self.bot.wait_for('message', timeout=180.0, check=check)
        except asyncio.TimeoutError:
            return await interaction.followup.send("⏳ Time expired. Please run `/tournament submit_squad` again.", ephemeral=True)
        db_players = get_all_players()
        db_map = {p["name"].lower(): p for p in db_players}
        db_names_list = list(db_map.keys())
        found_players = []
        missing = []
        seen = set()
        lines = [l.strip() for l in msg.content.split("\n") if l.strip()]
        for line in lines[:(max_s + 3)]:
            q = line.lower()
            match = db_map.get(q)
            if not match:
                fuzz = difflib.get_close_matches(q, db_names_list, n=1, cutoff=0.6)
                if fuzz: match = db_map[fuzz[0]]
            if match:
                if match["name"] not in seen and len(found_players) < max_s:
                    found_players.append(match)
                    seen.add(match["name"])
            else:
                missing.append(line)
        if missing or len(found_players) < min_s:
            err = f"❌ **Roster Invalid ({len(found_players)}/{min_s} Minimum Found)**\n"
            if missing: err += f"Missing: {', '.join(missing)}\n"
            err += "Please fix the names and try `/tournament submit_squad` again."
            return await msg.reply(err)
        team["squad"] = found_players
        save_tournament(tourney)
        await msg.reply(f"✅ **Squad Verified and Saved for {team['name']}!**\nRegistered {len(found_players)} players.")

    @app_commands.command(name="status", description="View the current tournament schedule — navigate rounds with arrow buttons.")
    async def status(self, interaction: discord.Interaction):
        server_id = str(interaction.guild.id)
        tourney = get_server_tournament(server_id)
        if not tourney:
            return await interaction.response.send_message("❌ No tournament exists in this server.", ephemeral=True)

        # Registration phase — no schedule yet
        if tourney["status"] == "registration":
            t_type = tourney.get("tournament_type", "round_robin")
            type_label = {"t20_world_cup": "T20 World Cup", "acl": "Akatsuki Cricket League"}.get(t_type, "Round Robin")
            embed = discord.Embed(title=f"🏆 {tourney['name']}", color=discord.Color.gold())
            embed.description = f"📝 **Registration Phase** · {type_label}"
            team_lines = []
            for t in tourney["teams"]:
                grp = f" · Group **{t['group']}**" if t.get("group") else ""
                team_lines.append(f"• **{t['name']}**{grp} (<@{t['owner_id']}>) — {len(t.get('squad', []))}/{tourney.get('max_squad', 15)} players")
            if not team_lines:
                embed.add_field(name="Registered Teams", value="No teams yet.", inline=False)
            else:
                chunks, cur, cur_len = [], [], 0
                for line in team_lines:
                    if cur_len + len(line) + 1 > 1020 and cur:
                        chunks.append("\n".join(cur)); cur, cur_len = [], 0
                    cur.append(line); cur_len += len(line) + 1
                if cur: chunks.append("\n".join(cur))
                for i, chunk in enumerate(chunks):
                    embed.add_field(name="Registered Teams" if i == 0 else "​", value=chunk, inline=False)
            embed.set_footer(text=f"Format: {tourney.get('format_overs', 20)} overs · Squad: {tourney.get('min_squad', 11)}–{tourney.get('max_squad', 15)} players")
            return await interaction.response.send_message(embed=embed)

        pages = _build_status_pages(tourney)
        if not pages:
            return await interaction.response.send_message("❌ No schedule generated yet. Use `/tournament start` first.", ephemeral=True)

        view = TournamentStatusView(tourney, pages)
        embed = _build_status_embed(tourney, pages[view.idx])

        if tourney["status"] == "completed":
            final = next((m for m in tourney.get("schedule", []) if m.get("round") == "Final"), None)
            winner = final["result"]["winner"] if final and final.get("result") else "TBD"
            embed.description = f"👑 **Champions: {winner}** · Use `/tournament leaderboard` for top performers!"

        await interaction.response.send_message(embed=embed, view=view)

    @app_commands.command(name="generate_knockouts", description="[MANAGER] Generate Semi-Finals for Top 4 teams. (Round Robin only)")
    async def generate_knockouts(self, interaction: discord.Interaction):
        server_id = str(interaction.guild.id)
        tourney = get_server_tournament(server_id)
        if not tourney: return await interaction.response.send_message("❌ No tournament exists.", ephemeral=True)
        if not self.is_manager(interaction, tourney): return await interaction.response.send_message("❌ Managers only.", ephemeral=True)
        if tourney["status"] != "active": return await interaction.response.send_message("❌ Tournament is not active.", ephemeral=True)

        if tourney.get("tournament_type") == "t20_world_cup":
            return await interaction.response.send_message("❌ This is a T20 World Cup tournament. Use `/tournament generate_super8` instead.", ephemeral=True)
        if tourney.get("tournament_type") == "acl":
            return await interaction.response.send_message("❌ This is an ACL tournament. Use `/tournament generate_playoffs` instead.", ephemeral=True)

        gs_matches = [m for m in tourney["schedule"] if isinstance(m.get("round"), int)]
        if any(m["status"] == "pending" for m in gs_matches):
            return await interaction.response.send_message("❌ Cannot generate knockouts until all Group Stage matches are completed.", ephemeral=True)
        if any(not isinstance(m.get("round"), int) for m in tourney["schedule"]):
            return await interaction.response.send_message("❌ Knockouts have already been generated.", ephemeral=True)

        standings = get_tournament_standings(tourney)
        real_teams = [t[0] for t in standings if t[0] != "BYE"]
        if len(real_teams) < 4:
            return await interaction.response.send_message("❌ Need at least 4 teams to play Semi-Finals.", ephemeral=True)
        top4 = real_teams[:4]
        sf1 = {"match_id": len(tourney["schedule"]) + 1, "round": "Semi-Final 1", "stage": "knockout", "team1": top4[0], "team2": top4[3], "status": "pending", "result": None}
        sf2 = {"match_id": len(tourney["schedule"]) + 2, "round": "Semi-Final 2", "stage": "knockout", "team1": top4[1], "team2": top4[2], "status": "pending", "result": None}
        tourney["schedule"].extend([sf1, sf2])
        save_tournament(tourney)
        await interaction.response.send_message(f"🔥 **Knockout Stage Set!**\n**Semi-Final 1:** {top4[0]} vs {top4[3]}\n**Semi-Final 2:** {top4[1]} vs {top4[2]}\n\nUse `/tournament play_next` to begin!")

    @app_commands.command(name="generate_super8", description="[MANAGER] Generate Super 8 stage after Group Stage. (T20 World Cup only)")
    async def generate_super8(self, interaction: discord.Interaction):
        server_id = str(interaction.guild.id)
        tourney = get_server_tournament(server_id)
        if not tourney: return await interaction.response.send_message("❌ No tournament exists.", ephemeral=True)
        if not self.is_manager(interaction, tourney): return await interaction.response.send_message("❌ Managers only.", ephemeral=True)
        if tourney["status"] != "active": return await interaction.response.send_message("❌ Tournament is not active.", ephemeral=True)
        if tourney.get("tournament_type") != "t20_world_cup":
            alt = "generate_playoffs" if tourney.get("tournament_type") == "acl" else "generate_knockouts"
            return await interaction.response.send_message(f"❌ This command is for T20 World Cup tournaments only. Use `/tournament {alt}` for this event.", ephemeral=True)

        group_matches = [m for m in tourney["schedule"] if m.get("stage") == "group"]
        if any(m["status"] == "pending" for m in group_matches):
            return await interaction.response.send_message("❌ All Group Stage matches must be completed before generating the Super 8.", ephemeral=True)
        if any(m.get("stage") == "super8" for m in tourney["schedule"]):
            return await interaction.response.send_message("❌ Super 8 has already been generated.", ephemeral=True)

        qualifiers = {}
        for grp in ["A", "B", "C", "D"]:
            st = get_group_standings(tourney, "group", grp)
            real = [n for n, _ in st if n != "BYE"]
            if len(real) < 2:
                return await interaction.response.send_message(f"❌ Group {grp} doesn't have enough qualifying teams.", ephemeral=True)
            qualifiers[grp] = real[:2]  # [1st, 2nd]

        # Super 8 Group A: A1, B2, C1, D2  |  Group B: A2, B1, C2, D1
        s8a = [qualifiers["A"][0], qualifiers["B"][1], qualifiers["C"][0], qualifiers["D"][1]]
        s8b = [qualifiers["A"][1], qualifiers["B"][0], qualifiers["C"][1], qualifiers["D"][0]]

        match_id = max(m["match_id"] for m in tourney["schedule"]) + 1
        for sg, sg_teams in [("A", s8a), ("B", s8b)]:
            teams = list(sg_teams)
            n = len(teams)
            for r in range(n - 1):
                for i in range(n // 2):
                    t1, t2 = teams[i], teams[n - 1 - i]
                    tourney["schedule"].append({
                        "match_id": match_id,
                        "round": f"Super 8 — Group {sg}",
                        "stage": "super8",
                        "group": sg,
                        "group_round": r + 1,
                        "team1": t1 if r % 2 == 0 else t2,
                        "team2": t2 if r % 2 == 0 else t1,
                        "status": "pending",
                        "result": None,
                    })
                    match_id += 1
                teams.insert(1, teams.pop())

        save_tournament(tourney)
        await interaction.response.send_message(
            f"🔥 **Super 8 Generated!**\n\n"
            f"**Super 8 Group A:** {' · '.join(s8a)}\n"
            f"**Super 8 Group B:** {' · '.join(s8b)}\n\n"
            f"Each group plays a round robin (6 matches each). Top 2 from each group advance to the Semi-Finals.\n"
            f"Use `/tournament play_next` to begin!"
        )

    @app_commands.command(name="generate_playoffs", description="[MANAGER] Generate the ACL Top-6 Playoffs + Super Cup. (ACL only)")
    async def generate_playoffs(self, interaction: discord.Interaction):
        server_id = str(interaction.guild.id)
        tourney = get_server_tournament(server_id)
        if not tourney: return await interaction.response.send_message("❌ No tournament exists.", ephemeral=True)
        if not self.is_manager(interaction, tourney): return await interaction.response.send_message("❌ Managers only.", ephemeral=True)
        if tourney.get("tournament_type") != "acl":
            return await interaction.response.send_message("❌ This command is for **ACL** tournaments only.", ephemeral=True)
        if tourney["status"] != "active": return await interaction.response.send_message("❌ Tournament is not active.", ephemeral=True)

        ok, msg = acl_generate_playoffs(tourney)
        if not ok:
            return await interaction.response.send_message(msg, ephemeral=True)
        shield = tourney.get("league_shield")
        await interaction.response.send_message(
            content=f"🏆 **ACL PLAYOFFS ARE SET!**\n🛡️ **{shield}** finished #1 — League Shield Winner, straight into the **Super Cup**.\nThe Top 6 now fight for the ACL Trophy. Owners: `/tournament fixtures` to find your match.",
            embed=acl_bracket_embed(tourney),
        )

    @app_commands.command(name="bracket", description="View the full ACL Playoffs + Super Cup bracket.")
    async def bracket(self, interaction: discord.Interaction):
        server_id = str(interaction.guild.id)
        tourney = get_server_tournament(server_id)
        if not tourney: return await interaction.response.send_message("❌ No tournament exists.", ephemeral=True)
        if tourney.get("tournament_type") != "acl":
            return await interaction.response.send_message("❌ The bracket view is for **ACL** tournaments. Use `/tournament standings` or `/tournament status`.", ephemeral=True)
        if not _acl_get(tourney, "Qualifier"):
            return await interaction.response.send_message("ℹ️ The Playoffs haven't been generated yet. A Manager runs `/tournament generate_playoffs` once all 91 league games are done.", ephemeral=True)
        await interaction.response.send_message(embed=acl_bracket_embed(tourney))

    # NOTE: force_delete & set_theme are prefix-only (cvt force_delete / cvt set_theme) — 25-subcommand limit.

    @app_commands.command(name="set_team_color", description="[MANAGER] Set a team's color for the scorecard. Works anytime, even mid-tournament.")
    async def set_team_color(self, interaction: discord.Interaction, team_name: str, color: str):
        server_id = str(interaction.guild.id)
        tourney = get_server_tournament(server_id)
        if not tourney:
            return await interaction.response.send_message("❌ No tournament exists in this server.", ephemeral=True)
        if not self.is_manager(interaction, tourney):
            return await interaction.response.send_message("❌ You are not a Tournament Manager.", ephemeral=True)
        if not re.match(r'^#[0-9A-Fa-f]{6}$', color):
            return await interaction.response.send_message("❌ Invalid color format. Use a 6-digit hex code like `#FF0000` (red) or `#1DA1F2` (blue).", ephemeral=True)
        team = next((t for t in tourney["teams"] if t["name"].lower() == team_name.lower()), None)
        if not team:
            return await interaction.response.send_message(f"❌ Team **{team_name}** not found.", ephemeral=True)
        team["color"] = color.upper()
        save_tournament(tourney)
        preview = discord.Embed(description=f"✅ **{team['name']}** color set to `{color.upper()}`.", color=int(color.lstrip('#'), 16))
        await interaction.response.send_message(embed=preview)

    @app_commands.command(name="set_team_logo", description="[MANAGER/OWNER] Set a team's logo. Choose standings (tables/bracket) or match (scorecard/banner).")
    @app_commands.describe(
        logo_type="'standings' = shown in points table & bracket | 'match' = shown in scorecards & match start banner",
        team_name="Team name",
        emoji="Emoji, flag, or :shortcode:",
        logo_url="Direct image URL",
        logo_image="Upload a PNG/JPG",
    )
    @app_commands.choices(logo_type=[
        app_commands.Choice(name="standings", value="standings"),
        app_commands.Choice(name="match",     value="match"),
    ])
    async def set_team_logo(self, interaction: discord.Interaction, logo_type: str, team_name: str, emoji: str = None, logo_url: str = None, logo_image: discord.Attachment = None):
        server_id = str(interaction.guild.id)
        tourney = get_server_tournament(server_id)
        if not tourney:
            return await interaction.response.send_message("❌ No tournament exists.", ephemeral=True)
        team = next((t for t in tourney["teams"] if t["name"].lower() == team_name.lower()), None)
        if not team:
            return await interaction.response.send_message(f"❌ Team **{team_name}** not found.", ephemeral=True)
        is_mgr = self.is_manager(interaction, tourney)
        if not is_mgr and team.get("owner_id") != str(interaction.user.id):
            return await interaction.response.send_message("❌ Only Managers or the Team Owner can set the logo.", ephemeral=True)

        field = "logo_standings" if logo_type == "standings" else "logo_match"
        label = "Standings" if logo_type == "standings" else "Match"
        where = "points table & bracket" if logo_type == "standings" else "scorecards & match start banner"

        if logo_image:
            if not logo_image.content_type or not logo_image.content_type.startswith("image/"):
                return await interaction.response.send_message("❌ Attachment must be an image file.", ephemeral=True)
            try:
                img_bytes = await logo_image.read()
                import base64 as _b64
                mime = logo_image.content_type.split(";")[0]
                team[field] = f"data:{mime};base64,{_b64.b64encode(img_bytes).decode()}"
            except Exception:
                team[field] = logo_image.url
            save_tournament(tourney)
            return await interaction.response.send_message(f"✅ {label} logo for **{team['name']}** set from uploaded image — used in {where}.")
        if logo_url:
            team[field] = logo_url.strip()
            save_tournament(tourney)
            return await interaction.response.send_message(f"✅ {label} logo for **{team['name']}** set from URL — used in {where}.")
        if emoji:
            import re as _re
            raw = emoji.strip()
            if not _re.match(r'<a?:\w+:\d+>', raw):
                ge = discord.utils.get(interaction.guild.emojis, name=raw.strip(':'))
                if ge:
                    raw = str(ge)
            team[field] = raw
            save_tournament(tourney)
            return await interaction.response.send_message(f"✅ {label} logo for **{team['name']}** set to {raw} — used in {where}.")
        await interaction.response.send_message("❌ Provide an emoji, a URL, or upload an image.", ephemeral=True)

    # NOTE: set_injury_channel & remove_injury are prefix-only (cvt set_injury_channel / cvt remove_injury) — 25-subcommand limit.

    @app_commands.command(name="match_scorecard", description="View the scorecard image for a completed tournament match.")
    async def match_scorecard(self, interaction: discord.Interaction, match_id: int):
        server_id = str(interaction.guild.id)
        tourney = get_server_tournament(server_id)
        if not tourney:
            return await interaction.response.send_message("❌ No tournament exists in this server.", ephemeral=True)
        m = next((x for x in tourney.get("schedule", []) if x["match_id"] == match_id), None)
        if not m:
            return await interaction.response.send_message(f"❌ Match #{match_id} not found.", ephemeral=True)
        if m["status"] != "completed":
            return await interaction.response.send_message(f"❌ Match #{match_id} hasn't been completed yet.", ephemeral=True)
        r = m["result"]
        t1, t2 = m["team1"], m["team2"]
        winner = r["winner"]
        round_label = m.get("round", f"Match {m['match_id']}")
        embed = discord.Embed(
            title=f"Match #{match_id} — {round_label}",
            description=f"**{t1}** {r['t1_runs']}/{r['t1_wickets']}  vs  **{t2}** {r['t2_runs']}/{r['t2_wickets']}\n🏆 Winner: **{winner}**",
            color=discord.Color.orange()
        )
        embed.set_footer(text=tourney["name"])
        from bot import reconstruct_scorecard_data, generate_scorecard_from_data
        full_data = reconstruct_scorecard_data(tourney, m)
        if full_data:
            try:
                await interaction.response.defer()
                img_buf = generate_scorecard_from_data(full_data)
                file = discord.File(fp=img_buf, filename=f"scorecard_m{match_id}.png")
                await interaction.followup.send(embed=embed, file=file)
                return
            except Exception as _e:
                print(f"⚠️ Scorecard regeneration failed for match {match_id}: {_e}")
        if interaction.response.is_done():
            embed.add_field(name="No image", value="Scorecard image could not be generated.", inline=False)
            await interaction.followup.send(embed=embed)
        else:
            embed.add_field(name="No image", value="No scorecard data saved for this match.", inline=False)
            await interaction.response.send_message(embed=embed)

    @app_commands.command(name="play_next", description="[MANAGER] Launch the next pending tournament match in this channel.")
    async def play_next(self, interaction: discord.Interaction):
        server_id = str(interaction.guild.id)
        tourney = get_server_tournament(server_id)
        if not tourney: return await interaction.response.send_message("❌ No tournament exists.", ephemeral=True)
        if not self.is_manager(interaction, tourney): return await interaction.response.send_message("❌ Managers only.", ephemeral=True)
        if tourney["status"] != "active": return await interaction.response.send_message("❌ Tournament is not active.", ephemeral=True)
        schedule = tourney.get("schedule", [])
        current_round = next((m["round"] for m in schedule if m["status"] == "pending"), None)
        pending = next((m for m in schedule if m["status"] == "pending" and m["round"] == current_round), None)
        if not pending:
            return await interaction.response.send_message("🏆 All matches have been completed!", ephemeral=True)
        r_label = f"Round {current_round}" if isinstance(current_round, int) else current_round
        await interaction.response.send_message(f"🚀 **Launching {r_label} — Match {pending['match_id']}...**")
        self.bot.dispatch("start_tournament_match", interaction.channel, interaction.user.id, tourney, pending)

    @app_commands.command(name="play", description="[MANAGER/OWNER] Launch a match by ID — owners can launch any of their own.")
    async def play_match(self, interaction: discord.Interaction, match_id: int):
        server_id = str(interaction.guild.id)
        tourney = get_server_tournament(server_id)
        if not tourney: return await interaction.response.send_message("❌ No tournament exists.", ephemeral=True)
        if tourney["status"] != "active": return await interaction.response.send_message("❌ Tournament is not active.", ephemeral=True)
        match = next((m for m in tourney.get("schedule", []) if m["match_id"] == match_id), None)
        if not match:
            return await interaction.response.send_message(f"❌ Match ID {match_id} does not exist.", ephemeral=True)
        # Managers can launch any match; owners can launch any match they're playing in.
        if not owner_can_launch(tourney, match, interaction.user.id, self.is_manager(interaction, tourney)):
            return await interaction.response.send_message("❌ You can only launch matches **your team** is playing in. (Managers can launch any.)", ephemeral=True)
        if match["status"] == "locked":
            return await interaction.response.send_message(f"❌ Match {match_id} isn't ready — its teams depend on earlier results.", ephemeral=True)
        if match["status"] != "pending":
            return await interaction.response.send_message(f"❌ Match {match_id} is already completed.", ephemeral=True)
        r_label = _tm_round_label(match)
        await interaction.response.send_message(f"🚀 **Launching Match {match['match_id']} ({r_label})...**\n<@{interaction.user.id}> — make sure your opponent is here to pick their XI.")
        self.bot.dispatch("start_tournament_match", interaction.channel, interaction.user.id, tourney, match)

    @app_commands.command(name="fixtures", description="View a team's fixtures & results (defaults to your own team).")
    async def fixtures(self, interaction: discord.Interaction, team_name: str = None):
        server_id = str(interaction.guild.id)
        tourney = get_server_tournament(server_id)
        if not tourney: return await interaction.response.send_message("❌ No tournament exists.", ephemeral=True)
        if team_name:
            team = next((t for t in tourney["teams"] if t["name"].lower() == team_name.lower()), None)
            if not team:
                return await interaction.response.send_message(f"❌ Team **{team_name}** not found.", ephemeral=True)
        else:
            team = next((t for t in tourney["teams"] if t.get("owner_id") == str(interaction.user.id)), None)
            if not team:
                return await interaction.response.send_message("❌ You don't own a team here. Specify a team name: `/tournament fixtures <team>`.", ephemeral=True)
        await interaction.response.send_message(embed=build_team_fixtures_embed(tourney, team["name"]))

    @app_commands.command(name="next_match", description="[OWNER] Automatically launch your team's next pending match.")
    async def next_match(self, interaction: discord.Interaction):
        server_id = str(interaction.guild.id)
        tourney = get_server_tournament(server_id)
        if not tourney: return await interaction.response.send_message("❌ No tournament exists.", ephemeral=True)
        if tourney["status"] != "active": return await interaction.response.send_message("❌ Tournament is not active.", ephemeral=True)
        my_team = next((t for t in tourney["teams"] if t["owner_id"] == str(interaction.user.id)), None)
        if not my_team:
            return await interaction.response.send_message("❌ You are not a Team Owner in this tournament.", ephemeral=True)
        my_team_name = my_team["name"]
        my_matches = [m for m in tourney.get("schedule", []) if m["status"] == "pending" and (m["team1"] == my_team_name or m["team2"] == my_team_name)]
        if not my_matches:
            return await interaction.response.send_message(f"✅ Your team (**{my_team_name}**) has no pending matches right now!", ephemeral=True)
        match = my_matches[0]
        r_label = f"Round {match['round']}" if isinstance(match['round'], int) else match['round']
        await interaction.response.send_message(f"🚀 **Launching Next Match for {my_team_name}: Match {match['match_id']} ({r_label})...**")
        self.bot.dispatch("start_tournament_match", interaction.channel, interaction.user.id, tourney, match)

    def _try_generate_semis(self, tourney: dict):
        """Auto-generate SF1/SF2 when both Super 8 groups are complete (T20 WC)."""
        s8a = [m for m in tourney["schedule"] if m.get("stage") == "super8" and m.get("group") == "A"]
        s8b = [m for m in tourney["schedule"] if m.get("stage") == "super8" and m.get("group") == "B"]
        if not (s8a and s8b): return
        if any(m["status"] == "pending" for m in s8a + s8b): return
        if any(m.get("round") == "Semi-Final 1" for m in tourney["schedule"]): return
        s8a_st = get_group_standings(tourney, "super8", "A")
        s8b_st = get_group_standings(tourney, "super8", "B")
        a_top2 = [n for n, _ in s8a_st[:2]]
        b_top2 = [n for n, _ in s8b_st[:2]]
        if len(a_top2) < 2 or len(b_top2) < 2: return
        max_id = max(m["match_id"] for m in tourney["schedule"])
        # SF1: Super 8 Group A 1st vs Super 8 Group B 2nd
        # SF2: Super 8 Group B 1st vs Super 8 Group A 2nd
        tourney["schedule"].extend([
            {"match_id": max_id + 1, "round": "Semi-Final 1", "stage": "knockout",
             "team1": a_top2[0], "team2": b_top2[1], "status": "pending", "result": None},
            {"match_id": max_id + 2, "round": "Semi-Final 2", "stage": "knockout",
             "team1": b_top2[0], "team2": a_top2[1], "status": "pending", "result": None},
        ])

    @commands.Cog.listener()
    async def on_tournament_match_complete(self, match):
        server_id = match.tournament_server_id
        tourney = get_server_tournament(server_id)
        if not tourney: return

        match_idx = match.tournament_match_id - 1
        m_data = tourney["schedule"][match_idx]

        t1_name, t2_name = match.team1["name"], match.team2["name"]
        if match.innings1.batting_team["name"] == t1_name:
            t1_inn, t2_inn = match.innings1, match.innings2
        else:
            t1_inn, t2_inn = match.innings2, match.innings1

        target = getattr(match, "target", match.innings1.total_runs + 1)
        is_tied = (match.innings2.total_runs == target - 1)

        if getattr(match, 'tiebreak_winner_name', None):
            winner = match.tiebreak_winner_name
        elif is_tied: winner = "TIE"
        elif match.innings2.total_runs >= target: winner = match.innings2.batting_team["name"]
        else: winner = match.innings1.batting_team["name"]

        # Knockouts can't end in a draw — break a tie toward team1 (the higher seed / home slot)
        if winner == "TIE" and not isinstance(m_data.get("round"), int) and m_data.get("stage") in ("knockout", None, "acl_playoff", "acl_supercup"):
            winner = m_data["team1"]

        # Loser is meaningful for knockout progression (e.g. ACL Semi = Qualifier loser)
        loser = None
        if winner not in ("TIE", None):
            loser = m_data["team2"] if winner == m_data["team1"] else m_data["team1"]

        m_data["status"] = "completed"
        m_data["result"] = {
            "winner": winner, "loser": loser, "format_overs": match.format_overs,
            "t1_runs": t1_inn.total_runs, "t1_wickets": t1_inn.wickets, "t1_balls": t1_inn.total_balls,
            "t2_runs": t2_inn.total_runs, "t2_wickets": t2_inn.wickets, "t2_balls": t2_inn.total_balls,
            "scorecard_players": getattr(match, "_scorecard_players", None),
        }
        tourney["current_match_idx"] += 1

        # --- STATS AGGREGATION ---
        if "stats" not in tourney: tourney["stats"] = {}
        if t1_name not in tourney["stats"]: tourney["stats"][t1_name] = {}
        if t2_name not in tourney["stats"]: tourney["stats"][t2_name] = {}

        def process_team_stats(team_name, batting_inn, bowling_inn):
            for p in batting_inn.batting_team["players"]:
                p_name = p["name"]
                p_stats = tourney["stats"][team_name].setdefault(p_name, {"matches": 0, "runs": 0, "balls_faced": 0, "outs": 0, "fours": 0, "sixes": 0, "fifties": 0, "hundreds": 0, "wickets": 0, "runs_conceded": 0, "balls_bowled": 0})
                p_stats["matches"] += 1
                if p_name in batting_inn.batting_stats:
                    b_stat = batting_inn.batting_stats[p_name]
                    p_stats["runs"] += b_stat.runs_scored
                    p_stats["balls_faced"] += b_stat.balls_faced
                    if b_stat.dismissal != "not out": p_stats["outs"] += 1
                    p_stats["fours"] += getattr(b_stat, "fours", 0)
                    p_stats["sixes"] += getattr(b_stat, "sixes", 0)
                    if b_stat.runs_scored >= 100: p_stats["hundreds"] += 1
                    elif b_stat.runs_scored >= 50: p_stats["fifties"] += 1
            for p_name, bw_stat in bowling_inn.bowling_stats.items():
                if bw_stat.balls_bowled > 0:
                    p_stats = tourney["stats"][team_name].setdefault(p_name, {"matches": 0, "runs": 0, "balls_faced": 0, "outs": 0, "fours": 0, "sixes": 0, "fifties": 0, "hundreds": 0, "wickets": 0, "runs_conceded": 0, "balls_bowled": 0})
                    p_stats["wickets"] += bw_stat.wickets_taken
                    p_stats["runs_conceded"] += bw_stat.runs_conceded
                    p_stats["balls_bowled"] += bw_stat.balls_bowled

        process_team_stats(t1_name, t1_inn, t2_inn)
        process_team_stats(t2_name, t2_inn, t1_inn)

        # --- INJURY ROLL (group/super8/league only, not knockouts; needs injuries_enabled) ---
        if tourney.get("injuries_enabled", False) and m_data.get("stage") in ("group", "super8", "league"):
            import random as _rng
            # ACL injuries are more frequent and RATING-SCALED — a star (high bat/bowl)
            # gets hurt less than a journeyman — and allow one injury per TEAM per match
            # (vs one per whole match for other formats), so the squad depth matters more.
            _is_acl = tourney.get("tournament_type") == "acl"
            _match_injured = False
            for team_name, bat_inn, bowl_inn in [(t1_name, t1_inn, t2_inn), (t2_name, t2_inn, t1_inn)]:
                if not _is_acl and _match_injured: break
                team_obj = next((t for t in tourney["teams"] if t["name"] == team_name), None)
                if not team_obj: continue
                _team_injured = False
                for player in team_obj["squad"]:
                    if (_is_acl and _team_injured) or (not _is_acl and _match_injured): break
                    p_name = player["name"]
                    if player.get("injured"): continue
                    bat_stat  = bat_inn.batting_stats.get(p_name)
                    bowl_stat = bowl_inn.bowling_stats.get(p_name)
                    played = (bat_stat and bat_stat.balls_faced > 0) or \
                             (bowl_stat and bowl_stat.balls_bowled > 0)
                    if not played: continue
                    heavy = (bat_stat and bat_stat.balls_faced >= 20) or \
                            (bowl_stat and bowl_stat.balls_bowled >= 12)
                    if _is_acl:
                        base = 0.065 if heavy else 0.028          # ~2x the other-format rate
                        _rt = max(player.get("bat", 50), player.get("bowl", 50))
                        # factor: 1.0 at "normal" (75) → ~0.6 for a 95-rated star, ~1.3 for a 60-rated
                        _factor = max(0.55, min(1.35, 1.0 - (_rt - 75) * 0.02))
                        chance = base * _factor
                    else:
                        chance = 0.03 if heavy else 0.01
                    if _rng.random() >= chance: continue
                    severity = _rng.choices([1, 2, 3], weights=[60, 30, 10])[0]
                    team_pending = [m for m in tourney["schedule"]
                                    if m["status"] == "pending" and
                                    (m["team1"] == team_name or m["team2"] == team_name)]
                    severity = min(severity, len(team_pending))
                    if severity == 0: continue
                    until_id = team_pending[severity - 1]["match_id"]
                    player["injured"] = True
                    player["injury_until_match"] = until_id
                    player["injury_severity"] = severity
                    tourney.setdefault("pending_injury_news", []).append({
                        "team": team_name, "player": p_name,
                        "severity": severity, "until": until_id,
                    })
                    if _is_acl: _team_injured = True
                    else: _match_injured = True

        # --- KNOCKOUTS AUTO-PROGRESSION ---
        t_type = tourney.get("tournament_type", "round_robin")

        if t_type == "acl":
            # ACL: resolve playoff bracket + Super Cup as feeder results come in
            _acl_try_advance(tourney)
            assign_tournament_conditions(tourney)   # fill conditions on any newly-added matches
            save_tournament(tourney)
            return

        if t_type == "t20_world_cup":
            # Super 8 complete → auto-generate Semi-Finals
            self._try_generate_semis(tourney)

        # SF complete → auto-generate Final (works for both formats)
        sf1 = next((m for m in tourney["schedule"] if m.get("round") == "Semi-Final 1"), None)
        sf2 = next((m for m in tourney["schedule"] if m.get("round") == "Semi-Final 2"), None)
        if sf1 and sf2 and sf1["status"] == "completed" and sf2["status"] == "completed":
            if not any(m.get("round") == "Final" for m in tourney["schedule"]):
                tourney["schedule"].append({
                    "match_id": len(tourney["schedule"]) + 1, "round": "Final", "stage": "knockout",
                    "team1": sf1["result"]["winner"], "team2": sf2["result"]["winner"],
                    "status": "pending", "result": None
                })

        final_match = next((m for m in tourney["schedule"] if m.get("round") == "Final"), None)
        if final_match and final_match["status"] == "completed" and tourney["status"] != "completed":
            tourney["status"] = "completed"

        assign_tournament_conditions(tourney)   # fill conditions on any newly-added matches
        save_tournament(tourney)

    @app_commands.command(name="standings", description="View the Tournament Points Table & NRR.")
    async def standings(self, interaction: discord.Interaction):
        await interaction.response.defer()
        server_id = str(interaction.guild.id)
        tourney = get_server_tournament(server_id)
        if not tourney: return await interaction.followup.send("❌ No tournament exists.", ephemeral=True)

        t_type = tourney.get("tournament_type", "round_robin")

        # T20 World Cup standings
        if t_type == "t20_world_cup":
            schedule      = tourney.get("schedule", [])
            super8_matches = [m for m in schedule if m.get("stage") == "super8"]
            ko_matches     = [m for m in schedule if m.get("stage") == "knockout"]

            if not super8_matches and not ko_matches:
                # Group stage only — single image, no navigation needed
                try:
                    buf = generate_t20wc_points_table(tourney)
                    return await interaction.followup.send(file=discord.File(fp=buf, filename="points_table.png"))
                except Exception as e:
                    print(f"⚠️ Points table image failed: {e}")

            # Build available pages
            pages = []
            try:
                s16_buf = generate_t20wc_points_table(tourney)
                pages.append(("Group Stage", "points_table.png", s16_buf))
            except Exception as e:
                print(f"⚠️ Super16 table failed: {e}")
            if super8_matches:
                try:
                    s8_buf = generate_t20wc_super8_table(tourney)
                    pages.append(("Super 8", "super8_table.png", s8_buf))
                except Exception as e:
                    print(f"⚠️ Super8 table failed: {e}")
            if ko_matches:
                try:
                    ko_buf = generate_t20wc_knockouts_image(tourney)
                    if ko_buf:
                        pages.append(("Knockouts", "knockouts.png", ko_buf))
                except Exception as e:
                    print(f"⚠️ Knockouts image failed: {e}")

            if len(pages) >= 2:
                start_idx = len(pages) - 1
                view = T20StandingsView(pages, start_idx=start_idx)
                _, fname, buf = pages[start_idx]
                buf.seek(0)
                return await interaction.followup.send(file=discord.File(fp=buf, filename=fname), view=view)
            elif pages:
                _, fname, buf = pages[0]
                buf.seek(0)
                return await interaction.followup.send(file=discord.File(fp=buf, filename=fname))

            # Both images failed — text embed fallback
            embed = discord.Embed(title=f"🌍 {tourney['name']} — Standings", color=discord.Color.gold())
            has_data = False
            for sg in ["A", "B"]:
                st = get_group_standings(tourney, "super8", sg)
                if st:
                    has_data = True
                    rows = ["```", f"{'':2}{'Team':<20}{'P':>2}{'W':>2}{'L':>2}{'Pts':>4}{'NRR':>8}", "─"*42]
                    for i, (nm, d) in enumerate(st, 1):
                        arrow = "-> " if i <= 2 else "   "
                        rows.append(f"{arrow}{nm[:18]:<20}{d['P']:>2}{d['W']:>2}{d['L']:>2}{d['Pts']:>4}{d['NRR']:>+8.2f}")
                    rows.append("```")
                    embed.add_field(name=f"Super 8 - Group {sg}", value="\n".join(rows), inline=True)
            if not has_data:
                embed.description = "No matches completed yet."
            embed.set_footer(text="-> marks teams that advance to the next stage")
            return await interaction.followup.send(embed=embed)

        # ACL: bespoke 14-team points table (Shield #1 + Top-6 playoff highlights)
        if t_type == "acl":
            try:
                buf = generate_acl_points_table(tourney)
                return await interaction.followup.send(file=discord.File(fp=buf, filename="acl_points_table.png"))
            except Exception as e:
                print(f"⚠️ ACL points table failed, using default: {e}")
            # fall through to the generic renderer below on failure

        # Round Robin: existing image-based standings
        standings = get_tournament_standings(tourney)
        theme = tourney.get("theme", "Default")

        try:
            font_title = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 46)
            font_small = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 18)
            font_hdr = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 22)
            font_row = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 26)
            font_bold = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 22)
        except:
            font_title = font_small = font_hdr = font_row = font_bold = ImageFont.load_default()

        def get_tw(text, font):
            if hasattr(font, 'getbbox'): return font.getbbox(text)[2]
            return len(text) * 12

        if theme == "Crimson Cricket":
            try:
                img = Image.open("assets/points_table_crimson.png").convert("RGB")
                d = ImageDraw.Draw(img)
                start_y = 275
                row_height = 40
                c_text = "#FFFFFF"
                cols = {"TEAM": 140, "P": 445, "W": 555, "L": 665, "NR": 775, "PTS": 885, "NRR": 995}
                y = start_y
                for i, (t_name, data) in enumerate(standings, 1):
                    if i > 10: break
                    d.text((cols["TEAM"], y + 8), t_name[:20].upper(), fill=c_text, font=font_row)
                    d.text((cols["P"] - (get_tw(str(data['P']), font_row)/2), y + 8), str(data['P']), fill=c_text, font=font_row)
                    d.text((cols["W"] - (get_tw(str(data['W']), font_row)/2), y + 8), str(data['W']), fill=c_text, font=font_row)
                    d.text((cols["L"] - (get_tw(str(data['L']), font_row)/2), y + 8), str(data['L']), fill=c_text, font=font_row)
                    d.text((cols["NR"] - (get_tw(str(data['T']), font_row)/2), y + 8), str(data['T']), fill=c_text, font=font_row)
                    d.text((cols["PTS"] - (get_tw(str(data['Pts']), font_row)/2), y + 8), str(data['Pts']), fill=c_text, font=font_row)
                    nrr_str = f"{data['NRR']:+.2f}"
                    d.text((cols["NRR"] - (get_tw(nrr_str, font_row)/2), y + 8), nrr_str, fill=c_text, font=font_row)
                    y += row_height
                buf = io.BytesIO()
                img.save(buf, format="PNG")
                buf.seek(0)
                return await interaction.followup.send(file=discord.File(fp=buf, filename="crimson_standings.png"))
            except FileNotFoundError:
                print("⚠️ Warning: points_table_crimson.png not found. Falling back to default layout.")
                pass

        c_bg = "#101820"; c_panel = "#F8F9FA"; c_header = "#0B2B5C"
        c_cyan = "#1DA1F2"; c_text_navy = "#0F172A"; c_text_grey = "#64748B"
        c_white = "#FFFFFF"; c_line = "#E2E8F0"; c_green = "#39B54A"; c_red = "#E84135"
        row_height = 60; header_height = 120; footer_height = 80
        img_height = 80 + header_height + 50 + (len(standings) * row_height) + footer_height + 80
        img = Image.new("RGB", (1200, img_height), color=c_bg)
        d = ImageDraw.Draw(img)
        d.rounded_rectangle([(100, 80), (1100, img_height - 80)], radius=20, fill=c_panel)
        d.rounded_rectangle([(100, 80), (1100, 80 + header_height)], radius=20, fill=c_header)
        d.rectangle([(100, 80 + header_height - 20), (1100, 80 + header_height)], fill=c_header)
        d.text((140, 105), tourney['name'][:30].upper(), fill=c_white, font=font_title)
        d.text((140, 155), "POINTS TABLE - GROUP STAGE", fill="#A5F3FC", font=font_small)
        d.text((1060 - get_tw("SERVER LOGO", font_bold), 120), "SERVER LOGO", fill=c_white, font=font_bold)
        cols = [("POS", 40), ("TEAM", 150), ("P", 550), ("W", 650), ("L", 750), ("T", 850), ("PTS", 950), ("NRR", 1050)]
        for name, x in cols:
            w = get_tw(name, font_hdr)
            align_x = x - w/2 if name != "TEAM" else x
            d.text((align_x, 80 + header_height + 15), name, fill=c_text_grey, font=font_hdr)
        y = 80 + header_height + 50
        for i, (t_name, data) in enumerate(standings, 1):
            d.line([(100, y), (1100, y)], fill=c_line, width=2)
            if i <= 4: d.rectangle([(100, y), (108, y + row_height)], fill=c_cyan)
            d.text((140 - (get_tw(str(i), font_row)/2), y + 15), str(i), fill=c_text_navy, font=font_row)
            d.text((220, y + 15), t_name[:20].upper(), fill=c_text_navy, font=font_row)
            d.text((550 - (get_tw(str(data['P']), font_row)/2), y + 15), str(data['P']), fill=c_text_grey, font=font_row)
            d.text((650 - (get_tw(str(data['W']), font_row)/2), y + 15), str(data['W']), fill=c_green, font=font_row)
            d.text((750 - (get_tw(str(data['L']), font_row)/2), y + 15), str(data['L']), fill=c_red, font=font_row)
            d.text((850 - (get_tw(str(data['T']), font_row)/2), y + 15), str(data['T']), fill=c_text_grey, font=font_row)
            d.text((950 - (get_tw(str(data['Pts']), font_row)/2), y + 15), str(data['Pts']), fill=c_text_navy, font=font_row)
            nrr_str = f"{data['NRR']:+.3f}"
            d.text((1050 - (get_tw(nrr_str, font_row)/2), y + 15), nrr_str, fill=c_text_navy, font=font_row)
            y += row_height
        footer_y = img_height - 80 - footer_height
        d.rounded_rectangle([(100, footer_y), (1100, img_height - 80)], radius=20, fill=c_header)
        d.rectangle([(100, footer_y), (1100, footer_y + 20)], fill=c_header)
        d.text((600 - get_tw("SIMULATION ENGINE PRO", font_bold)//2, footer_y + 25), "SIMULATION ENGINE PRO", fill=c_white, font=font_bold)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)
        await interaction.followup.send(file=discord.File(fp=buf, filename="standings.png"))

    @app_commands.command(name="leaderboard", description="View the top performing players in the tournament.")
    @app_commands.choices(category=[
        app_commands.Choice(name="Most Runs", value="runs"),
        app_commands.Choice(name="Most Wickets", value="wickets"),
        app_commands.Choice(name="Highest Strike Rate (Min 50 Runs)", value="sr"),
        app_commands.Choice(name="Highest Batting Avg (Min 50 Runs)", value="bat_avg"),
        app_commands.Choice(name="Most 4s", value="fours"),
        app_commands.Choice(name="Most 6s", value="sixes"),
        app_commands.Choice(name="Most 50s", value="fifties"),
        app_commands.Choice(name="Most 100s", value="hundreds"),
        app_commands.Choice(name="Best Economy (Min 5 Overs)", value="econ"),
        app_commands.Choice(name="Best Bowling Avg (Min 3 Wickets)", value="bowl_avg"),
        app_commands.Choice(name="MVP Score", value="mvp"),
    ])
    async def leaderboard(self, interaction: discord.Interaction, category: app_commands.Choice[str]):
        server_id = str(interaction.guild.id)
        tourney = get_server_tournament(server_id)
        if not tourney: return await interaction.response.send_message("❌ No tournament exists.", ephemeral=True)
        all_players = []
        for t_name, players in tourney.get("stats", {}).items():
            for p_name, stats in players.items():
                all_players.append({"name": p_name, "team": t_name, "stats": stats})
        if not all_players:
            return await interaction.response.send_message("❌ No stats available yet. Complete a match first!", ephemeral=True)
        c_val = category.value

        def _mvp_score(s):
            sr = (s["runs"] / s["balls_faced"] * 100) if s["balls_faced"] > 0 else 0
            bat = float(s["runs"])
            if sr >= 150:   bat *= 1.30
            elif sr >= 130: bat *= 1.20
            elif sr >= 110: bat *= 1.10
            elif sr < 80 and s["balls_faced"] >= 20: bat *= 0.85
            bat += s["fifties"] * 15 + s["hundreds"] * 40
            bat += s["sixes"] * 2 + s["fours"] * 0.5
            econ = (s["runs_conceded"] / s["balls_bowled"] * 6) if s["balls_bowled"] > 0 else 9.0
            bowl = float(s["wickets"] * 40)
            if s["balls_bowled"] >= 12:
                bowl += max(-25.0, min(25.0, (8.0 - econ) * 5))
            return bat + bowl

        if c_val == "runs": sorted_players = sorted(all_players, key=lambda x: x["stats"]["runs"], reverse=True)
        elif c_val == "wickets": sorted_players = sorted(all_players, key=lambda x: x["stats"]["wickets"], reverse=True)
        elif c_val == "sr":
            qualifiers = [p for p in all_players if p["stats"]["runs"] >= 50]
            sorted_players = sorted(qualifiers, key=lambda x: (x["stats"]["runs"] / x["stats"]["balls_faced"]) if x["stats"]["balls_faced"] > 0 else 0, reverse=True)
        elif c_val == "bat_avg":
            qualifiers = [p for p in all_players if p["stats"]["runs"] >= 50]
            sorted_players = sorted(qualifiers, key=lambda x: x["stats"]["runs"] / max(1, x["stats"]["outs"]), reverse=True)
        elif c_val in ["fours", "sixes", "fifties", "hundreds"]:
            sorted_players = sorted(all_players, key=lambda x: x["stats"][c_val], reverse=True)
        elif c_val == "econ":
            qualifiers = [p for p in all_players if p["stats"]["balls_bowled"] >= 30]
            sorted_players = sorted(qualifiers, key=lambda x: (x["stats"]["runs_conceded"] / x["stats"]["balls_bowled"])*6 if x["stats"]["balls_bowled"]>0 else 999)
        elif c_val == "bowl_avg":
            qualifiers = [p for p in all_players if p["stats"]["wickets"] >= 3]
            sorted_players = sorted(qualifiers, key=lambda x: x["stats"]["runs_conceded"] / x["stats"]["wickets"] if x["stats"]["wickets"]>0 else 999)
        elif c_val == "mvp":
            sorted_players = sorted(all_players, key=lambda x: _mvp_score(x["stats"]), reverse=True)

        # runs / wickets / MVP get the first 50, paginated 10-per-page with ◀ ▶ buttons;
        # the qualifier-filtered categories stay a single top-10 embed.
        PAGINATED = {"runs", "wickets", "mvp"}
        limit = 50 if c_val in PAGINATED else 10
        header = ("-# *MVP = Runs (×SR multiplier) + Boundaries bonus + Milestone bonus + Wickets×40 + Economy bonus*"
                  if c_val == "mvp" else "")
        lines = []
        for i, p in enumerate(sorted_players[:limit], 1):
            s = p["stats"]
            if c_val == "runs": val = f"**{s['runs']}** runs"
            elif c_val == "wickets": val = f"**{s['wickets']}** wkts"
            elif c_val == "sr": val = f"**{(s['runs']/s['balls_faced']*100) if s['balls_faced']>0 else 0:.1f}** SR"
            elif c_val == "bat_avg": val = f"**{s['runs']/max(1, s['outs']):.1f}** Avg"
            elif c_val in ["fours", "sixes", "fifties", "hundreds"]: val = f"**{s[c_val]}**"
            elif c_val == "econ": val = f"**{(s['runs_conceded']/s['balls_bowled']*6) if s['balls_bowled']>0 else 0:.1f}** Econ"
            elif c_val == "bowl_avg": val = f"**{s['runs_conceded']/s['wickets'] if s['wickets']>0 else 0:.1f}** Avg"
            elif c_val == "mvp":
                score = _mvp_score(s)
                sr = (s["runs"]/s["balls_faced"]*100) if s["balls_faced"]>0 else 0
                val = f"**{score:.0f}** pts — {s['runs']}R @{sr:.0f}SR · {s['wickets']}W"
            lines.append(f"`{i:>2}.` **{p['name']}** ({p['team']}) — {val}")
        title = f"🏆 Tournament Leaderboard: {category.name}"
        if c_val in PAGINATED:
            view = LeaderboardView(title, header, lines)
            await interaction.response.send_message(embed=view.make_embed(), view=view)
        else:
            embed = discord.Embed(title=title, color=discord.Color.gold())
            body = "\n".join(lines) if lines else "No players qualify for this leaderboard yet."
            embed.description = (header + "\n" if header else "") + body
            await interaction.response.send_message(embed=embed)

    @app_commands.command(name="player_stats", description="View a specific player's tournament stats.")
    async def player_stats(self, interaction: discord.Interaction, team_name: str, player_name: str):
        server_id = str(interaction.guild.id)
        tourney = get_server_tournament(server_id)
        if not tourney: return await interaction.response.send_message("❌ No tournament exists.", ephemeral=True)
        t_match = next((t for t in tourney.get("stats", {}).keys() if t.lower() == team_name.lower()), None)
        if not t_match:
            return await interaction.response.send_message(f"❌ Team '{team_name}' not found or hasn't played a match yet.", ephemeral=True)
        p_match = next((p for p in tourney["stats"][t_match].keys() if p.lower() == player_name.lower()), None)
        if not p_match:
            close = difflib.get_close_matches(player_name, list(tourney["stats"][t_match].keys()), n=1, cutoff=0.5)
            if close: p_match = close[0]
            else: return await interaction.response.send_message(f"❌ Player '{player_name}' not found in team '{t_match}'.", ephemeral=True)
        stats = tourney["stats"][t_match][p_match]
        sr = (stats["runs"] / stats["balls_faced"] * 100) if stats["balls_faced"] > 0 else 0.0
        bat_avg = (stats["runs"] / stats["outs"]) if stats["outs"] > 0 else float(stats["runs"])
        bowl_avg = (stats["runs_conceded"] / stats["wickets"]) if stats["wickets"] > 0 else 0.0
        econ = (stats["runs_conceded"] / stats["balls_bowled"] * 6) if stats["balls_bowled"] > 0 else 0.0
        embed = discord.Embed(title=f"📊 Tournament Stats: {p_match}", description=f"**Team:** {t_match} | **Matches:** {stats['matches']}", color=discord.Color.blue())
        bat_str = f"**Runs:** {stats['runs']}\n**Strike Rate:** {sr:.1f}\n**Average:** {bat_avg:.1f}\n"
        bat_str += f"**4s:** {stats['fours']} | **6s:** {stats['sixes']}\n**50s:** {stats['fifties']} | **100s:** {stats['hundreds']}"
        embed.add_field(name="🏏 Batting", value=bat_str, inline=True)
        bowl_str = f"**Wickets:** {stats['wickets']}\n**Economy:** {econ:.1f}\n**Bowling Avg:** {bowl_avg:.1f}\n"
        o = stats['balls_bowled'] // 6; b = stats['balls_bowled'] % 6
        bowl_str += f"**Overs:** {o}.{b}"
        embed.add_field(name="🎯 Bowling", value=bowl_str, inline=True)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="squad", description="View a team's tournament squad and player ratings.")
    async def squad(self, interaction: discord.Interaction, team_name: str = None):
        server_id = str(interaction.guild.id)
        tourney = get_server_tournament(server_id)
        if not tourney: return await interaction.response.send_message("❌ No tournament exists.", ephemeral=True)
        if team_name:
            team = next((t for t in tourney["teams"] if t["name"].lower() == team_name.lower()), None)
            if not team: return await interaction.response.send_message(f"❌ Team '{team_name}' not found.", ephemeral=True)
        else:
            team = next((t for t in tourney["teams"] if t["owner_id"] == str(interaction.user.id)), None)
            if not team: return await interaction.response.send_message("❌ You do not own a team. Please specify a `team_name`.", ephemeral=True)
        if not team.get("squad"):
            return await interaction.response.send_message(f"❌ **{team['name']}** has not submitted their squad yet.", ephemeral=True)
        batters, wks, all_rounders, bowlers = [], [], [], []
        for p in team["squad"]:
            role = p["role"]
            if "WK" in role: wks.append(p)
            elif "All-Rounder" in role: all_rounders.append(p)
            elif "Bowler" in role: bowlers.append(p)
            else: batters.append(p)
        batters.sort(key=lambda x: x["bat"], reverse=True)
        wks.sort(key=lambda x: x["bat"], reverse=True)
        all_rounders.sort(key=lambda x: (x["bat"] + x["bowl"]), reverse=True)
        bowlers.sort(key=lambda x: x["bowl"], reverse=True)
        grp_txt = f" · Group **{team['group']}**" if team.get("group") else ""
        embed = discord.Embed(title=f"📋 Squad: {team['name']}{grp_txt}", description=f"👤 **Owner:** <@{team['owner_id']}> | **Total Players:** {len(team['squad'])}", color=discord.Color.blue())
        def format_player(p, cat):
            style = p["role"].split("_", 1)[1].replace("_", " ") if "_" in p["role"] else ""
            if p.get("injured"):
                sev = p.get("injury_severity", 1)
                inj = f" 🚑 *(misses next {sev} team match{'es' if sev > 1 else ''})*"
            else:
                inj = ""
            if cat == "bat":  return f"**{p['name']}** *(Batter)*{inj}"
            elif cat == "wk": return f"**{p['name']}** *(WK Batter)*{inj}"
            elif cat == "ar": return f"**{p['name']}** *({style} All-Rounder)*{inj}" if style else f"**{p['name']}** *(All-Rounder)*{inj}"
            else:             return f"**{p['name']}** *({style} Bowler)*{inj}" if style else f"**{p['name']}** *(Bowler)*{inj}"
        if batters: embed.add_field(name="🏏 Batters", value="\n".join([format_player(p, "bat") for p in batters]), inline=False)
        if wks: embed.add_field(name="🧤 Wicket-Keepers", value="\n".join([format_player(p, "wk") for p in wks]), inline=False)
        if all_rounders: embed.add_field(name="⚔️ All-Rounders", value="\n".join([format_player(p, "ar") for p in all_rounders]), inline=False)
        if bowlers: embed.add_field(name="🎯 Bowlers", value="\n".join([format_player(p, "bowl") for p in bowlers]), inline=False)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="help", description="Show the Tournament module help guide.")
    async def tournament_help(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="🏆 Tournament Guide",
            description="Three event types: **Round Robin**, **T20 World Cup**, and **ACL** (14 teams → League → Playoffs → Super Cup).\nEvery command works as both `/tournament …` and the shorter `cvt …`.",
            color=discord.Color.gold(),
        )
        embed.add_field(
            name="🔴 ACL Quickstart",
            value=("**1.** `/tournament create` → pick **ACL** event type\n"
                   "**2.** `cvt add_team` ×**14** (assign each an owner)\n"
                   "**3.** each owner `cvt submit_squad` (alias `ss`)\n"
                   "**4.** `cvt start` → generates 91 league matches\n"
                   "**5.** owners `cvt fixtures` (`fx`) → `cvt play <id>` your games\n"
                   "**6.** after all 91, a Manager `cvt generate_playoffs` (`gp`)\n"
                   "**7.** `cvt bracket` (`br`) tracks Playoffs → **Super Cup**"),
            inline=False,
        )
        embed.add_field(
            name="🛠️ Setup",
            value="`create` · `add_team` · `add_manager` · `submit_squad`/`ss` · `start` · `set_team_logo` · `set_team_color`",
            inline=False,
        )
        embed.add_field(
            name="🏏 Play your matches",
            value=("`fixtures`/`fx` `[team]` — your upcoming + results\n"
                   "`play <id>` — **owners launch any of their own matches**; managers any\n"
                   "`next_match`/`nm` — launch your earliest pending · `play_next`/`pn` — [MGR] next in order"),
            inline=False,
        )
        embed.add_field(
            name="📊 Stats & standings",
            value=("`standings`/`st` · `status`/`sched` · `bracket`/`br` (ACL)\n"
                   "`leaderboard`/`lb <runs|wickets|sr|mvp|…>` · `player_stats` · `squad` · `match_scorecard <id>`"),
            inline=False,
        )
        embed.add_field(
            name="🔥 Knockouts (Managers)",
            value=("**ACL:** `generate_playoffs`/`gp`  ·  **Round Robin:** `generate_knockouts`  ·  **T20 WC:** `generate_super8`"),
            inline=False,
        )
        embed.set_footer(text="More admin tools are prefix-only: cvt transfer_team · replace_player · force_delete · set_theme · remove_injury · simulate_all")
        await interaction.response.send_message(embed=embed, ephemeral=True)
