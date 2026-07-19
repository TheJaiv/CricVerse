import datetime
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
from core.subscription_manager import DB_CACHE, async_save_tournament_to_bin, get_all_players, get_tier_status
from league.stadium_manager import (
    stadiums_enabled, default_stadium_pool, get_stadium_pool, canonical_stadium,
    assign_stadiums, reroll_stadiums, stadium_label, DEFAULT_ACL_STADIUMS,
)
from league.custom_tournament import (
    CUSTOM_TYPE_LABEL, CustomSetupView, custom_try_advance, custom_match_rank,
    custom_revert_cleanup, custom_stage_standings, custom_stage_cutoff,
    build_custom_status_pages, build_custom_standings_message,
    custom_config_summary_lines, stage_letters as custom_stage_letters,
)

# Tournament pitch & weather conditions
# Canonical engine lists (mirror the PitchWeatherView dropdowns in bot.py).
ALL_PITCHES = ["Flat", "Green", "Dry", "Dusty", "Hard", "Soft", "Cracked", "Damp",
               "Dead", "Worn", "Turning", "Two-Paced", "Slow", "Bouncy", "Sticky"]
ALL_WEATHER = ["Clear", "Cloudy", "Overcast", "Humid", "Dry Heat", "Windy",
               "Light Rain", "Drizzle", "Heavy Rain", "Thunderstorm"]
# "Nice"/standard pools - group stages draw from these 90% of the time, knockouts 100%.
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
    conditions_mode. Safe to call repeatedly - only fills matches missing conditions.
      manual  -> leave unset (each match asks interactively)
      auto    -> weighted pools (group 90% / knockout 100%)
      home    -> pitch = home team's (team1) home_pitch; weather = pooled
      stadium -> (DSL) pitch drawn from the match venue's weighted profile
    """
    assign_stadiums(tourney)   # venue labels (ACL random / DSL home-ground; no-op otherwise) - before pitch draw
    mode = tourney.get("conditions_mode", "manual")
    if mode == "manual":
        return
    homes = {t["name"]: t.get("home_pitch") for t in tourney.get("teams", [])}
    for m in tourney.get("schedule", []):
        if m.get("pitch") and m.get("weather"):
            continue
        ko = _match_is_knockout(m)
        if mode == "stadium":
            from league.dsl_manager import pick_dsl_conditions   # lazy - avoids circular import
            m["pitch"], m["weather"] = pick_dsl_conditions(m.get("stadium"), ko)
        elif mode == "home":
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

def generate_round_robin_schedule(team_names, *, double=False, stage=None, shuffle=True):
    """Circle-method fixtures for single/double round robin.
    Double mode mirrors leg 1 with home/away swapped and keeps round numbers ordered.
    """
    teams = list(team_names)
    if shuffle:
        random.shuffle(teams)
    if len(teams) % 2 != 0:
        teams.append("BYE")

    n = len(teams)
    leg1_rounds = []
    for r in range(n - 1):
        round_matches = []
        for i in range(n // 2):
            t1, t2 = teams[i], teams[n - 1 - i]
            if t1 == "BYE" or t2 == "BYE":
                continue
            a, b = (t1, t2) if r % 2 == 0 else (t2, t1)
            match = {"round": r + 1, "team1": a, "team2": b, "status": "pending", "result": None}
            if stage:
                match["stage"] = stage
            round_matches.append(match)
        if shuffle:
            random.shuffle(round_matches)
        leg1_rounds.append(round_matches)
        teams.insert(1, teams.pop())

    all_matches = []
    for round_matches in leg1_rounds:
        all_matches.extend(round_matches)
    if double:
        offset = n - 1
        for round_matches in leg1_rounds:
            for m in round_matches:
                mirrored = dict(m)
                mirrored["round"] = m["round"] + offset
                mirrored["team1"], mirrored["team2"] = m["team2"], m["team1"]
                all_matches.append(mirrored)

    return [dict(m, match_id=i + 1) for i, m in enumerate(all_matches)]


def _circle_rounds(items):
    """Circle-method 1-factorization -> list of rounds, each a list of (a, b) pairs.
    An odd count gets a BYE, whose pair is dropped (that item sits the round out)."""
    ts = list(items)
    if len(ts) % 2:
        ts.append("BYE")
    n = len(ts)
    rounds = []
    for r in range(n - 1):
        pairs = []
        for i in range(n // 2):
            a, b = ts[i], ts[n - 1 - i]
            if a == "BYE" or b == "BYE":
                continue
            pairs.append((a, b) if r % 2 == 0 else (b, a))
        rounds.append(pairs)
        ts.insert(1, ts.pop())
    return rounds


def generate_ipl_schedule(team_names):
    """Real-IPL fixture list: 10 teams, 14 matches each (70 league matches).

    The IPL has NO groups - one combined table, and nobody ever sees an 'A' or 'B'.
    The split below is purely the device the real IPL uses to *build* the fixture:
    the 10 teams are seeded 1-10, dealt alternately into two columns of 5, and each
    seed row (1&2, 3&4, ...) is a 'mirror' pair. From that, each team plays:

      · the 4 teams in its own column ............ twice (home & away)  = 8
      · its mirror - the other team on its row ... twice               = 2
      · the other 4 teams of the other column .... once (2 home, 2 away) = 4
                                                                  total = 14

    So the top two seeds (row 1 - the CSK/MI slot) meet twice, as in the real thing.
    `team_names` is taken in seed order (the order teams were added).

    Laid out as 14 rounds of 5 so every team plays exactly once per round:
      · rounds 1-9   - a single round robin over all 10 teams: this supplies every
                       one-off cross-column meeting, plus leg 1 of the twice-met pairs.
      · rounds 10-14 - the return legs: a K5 circle method inside each column (2+2
                       matches) plus the mirror match of the row that byes in both
                       columns that round (1) = 5.

    team1 is the home side. Every team ends on 7 home / 7 away.
    """
    seeds = list(team_names)
    col_a, col_b = seeds[0::2], seeds[1::2]   # seed 1 -> A, seed 2 -> B, seed 3 -> A ...
    all_teams = seeds
    row_of, grp_of = {}, {}
    for i, t in enumerate(col_a):
        row_of[t], grp_of[t] = i, "A"
    for i, t in enumerate(col_b):
        row_of[t], grp_of[t] = i, "B"

    def is_single(a, b):
        """Different column, different row -> the pair meets exactly once."""
        return grp_of[a] != grp_of[b] and row_of[a] != row_of[b]

    def home_of_single(t1, t2):
        """A[i] hosts iff (j - i) mod 5 ∈ {1, 2}, else B[j] hosts. This is what leaves
        every team with exactly 2 home + 2 away among its four single meetings."""
        a, b = (t1, t2) if grp_of[t1] == "A" else (t2, t1)
        d = (row_of[b] - row_of[a]) % 5
        return (a, b) if d in (1, 2) else (b, a)

    rounds = []
    leg1_home = {}   # frozenset(pair) -> who hosted leg 1, for the pairs that meet twice

    for pairs in _circle_rounds(all_teams):
        rnd = []
        for a, b in pairs:
            if is_single(a, b):
                h, aw = home_of_single(a, b)
            else:
                h, aw = a, b            # same column or mirror: leg 1, host as drawn
                leg1_home[frozenset((a, b))] = h
            rnd.append({"team1": h, "team2": aw})
        rounds.append(rnd)

    def return_leg(a, b):
        """Whoever was away in leg 1 hosts the return."""
        return (b, a) if leg1_home[frozenset((a, b))] == a else (a, b)

    # Return legs. Both columns run the SAME index rotation, so the row that byes in
    # column A byes in column B too - that row's mirror match fills the round to 5.
    for pairs in _circle_rounds(list(range(5))):
        bye = next(i for i in range(5) if i not in {x for p in pairs for x in p})
        rnd = []
        for i, j in pairs:
            rnd.append(dict(zip(("team1", "team2"), return_leg(col_a[i], col_a[j]))))
            rnd.append(dict(zip(("team1", "team2"), return_leg(col_b[i], col_b[j]))))
        rnd.append(dict(zip(("team1", "team2"), return_leg(col_a[bye], col_b[bye]))))
        rounds.append(rnd)

    # One flat league - no `group` on the match, because the IPL has no groups.
    schedule, mid = [], 1
    for r, rnd in enumerate(rounds, 1):
        random.shuffle(rnd)
        for m in rnd:
            schedule.append({
                "match_id": mid, "round": r, "stage": "group",
                "team1": m["team1"], "team2": m["team2"],
                "status": "pending", "result": None,
            })
            mid += 1
    return schedule


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


# ---- TBECS branded images ----------------------------------------------------
# All coordinates below are pixel-measured on the 1536x1024 templates in assets/
# (grid-line probe + zoomed crops). Edit the constant blocks to nudge alignment.
# Stage keys mirror TBECS_CONFIG (kept literal — this module must not import
# tbecs_manager at the top level).
TBECS_SUPER_STAGE = "super20"
TBECS_SUPER_GROUP = "S"

def _tbecs_font(size, bold=True):
    for p in ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
              "/System/Library/Fonts/Supplemental/Arial Bold.ttf" if bold else "/System/Library/Fonts/Supplemental/Arial.ttf",
              "C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf"):
        try:
            return ImageFont.truetype(p, size)
        except Exception:
            continue
    return ImageFont.load_default()


def _tbecs_team_logos(tourney):
    return {t["name"]: t.get("logo_standings") or t.get("logo_match") for t in tourney.get("teams", [])}


def _tbecs_fit(d, s, font, maxw):
    if font.getbbox(s)[2] <= maxw:
        return s
    while s and font.getbbox(s + "…")[2] > maxw:
        s = s[:-1]
    return s + "…"


def _tbecs_fit_full(s, base_size, maxw, min_size=14):
    """Shrink the font until the FULL name fits; only truncate below min_size.
    Returns (font, text)."""
    for size in range(base_size, min_size - 1, -1):
        f = _tbecs_font(size)
        if f.getbbox(s)[2] <= maxw:
            return f, s
    f = _tbecs_font(min_size)
    while s and f.getbbox(s + "…")[2] > maxw:
        s = s[:-1]
    return f, s + "…"


# Group-stage table (tbecs_groupstage.png): ONE group of 28 per image, split into
# two 14-row halves (POS 1-14 left, 15-28 right). Group name goes right of the
# POINTS TABLE ribbon.
_TBECS_GRP = {
    "row0_cy": 288.0, "row_h": 49.05,            # 14 rows per half
    "L": {"pos": 75,  "team": 133, "P": 403,  "W": 475,  "L": 548,  "PTS": 620,  "NRR": 704},
    "R": {"pos": 821, "team": 881, "P": 1155, "W": 1227, "L": 1298, "PTS": 1370, "NRR": 1454},
    "team_w_l": 228, "team_w_r": 232,            # name width budget (incl. the logo)
    "label_cx": 1075, "label_cy": 190,           # "GROUP A" — blank space right of the ribbon
}

def generate_tbecs_group_table(tourney, group) -> io.BytesIO:
    """One tbecs_groupstage.png render per group: that group's 28 teams over the
    1-14 / 15-28 halves, group name stamped beside the POINTS TABLE ribbon."""
    img = Image.open("assets/tbecs_groupstage.png").convert("RGBA")
    d = ImageDraw.Draw(img)
    G = _TBECS_GRP
    f_row = _tbecs_font(22); f_name = _tbecs_font(20); f_label = _tbecs_font(40)
    WHITE = "#FFFFFF"; GOLD = "#F0C242"
    logos = _tbecs_team_logos(tourney)
    goats = {t["name"] for t in tourney.get("teams", []) if t.get("goat")}

    d.text((G["label_cx"], G["label_cy"]), f"GROUP {group}", font=f_label, fill=WHITE, anchor="mm")

    rows = get_group_standings(tourney, "group", group)
    LOGO = 28
    for i, (nm, st) in enumerate(rows[:28]):
        half, r = ("L", i) if i < 14 else ("R", i - 14)
        C = G[half]
        cy = int(G["row0_cy"] + G["row_h"] * r)
        fill = GOLD if nm in goats else WHITE   # GOATs pop, and gold marks "can't qualify"
        logo = _fetch_emoji_img(logos.get(nm), LOGO)
        tx = C["team"]
        if logo:
            img.paste(logo, (tx, cy - LOGO // 2), logo)
            tx += LOGO + 6
        maxw = (G["team_w_l"] if half == "L" else G["team_w_r"]) - (LOGO + 6 if logo else 0)
        nf, ntxt = _tbecs_fit_full(nm.upper(), 20, maxw)   # full name: shrink, don't chop
        d.text((tx, cy), ntxt, font=nf, fill=fill, anchor="lm")
        for k in ("P", "W", "L", "PTS", "NRR"):
            v = f"{st['NRR']:+.2f}" if k == "NRR" else str(st["Pts"] if k == "PTS" else st[k])
            d.text((C[k], cy), v, font=f_row, fill=fill, anchor="mm")

    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="PNG")
    buf.seek(0)
    return buf


# Super 20 table (tbecs_20.png): single 20-row table.
_TBECS_S20 = {
    "row0_cy": 283.0, "row_h": 36.16,
    "pos": 106, "team": 185, "team_w": 470,
    "P": 762, "W": 915, "L": 1069, "PTS": 1225, "NRR": 1394,
}

def generate_tbecs_super20_table(tourney) -> io.BytesIO:
    img = Image.open("assets/tbecs_20.png").convert("RGBA")
    d = ImageDraw.Draw(img)
    S = _TBECS_S20
    f_row = _tbecs_font(21); f_name = _tbecs_font(22)
    WHITE = "#FFFFFF"; Q = "#7FE07F"   # top-8 qualification zone tint
    logos = _tbecs_team_logos(tourney)

    rows = get_group_standings(tourney, TBECS_SUPER_STAGE, TBECS_SUPER_GROUP)
    LOGO = 28
    for i, (nm, st) in enumerate(rows[:20]):
        cy = int(S["row0_cy"] + S["row_h"] * i)
        fill = Q if i < 8 else WHITE
        logo = _fetch_emoji_img(logos.get(nm), LOGO)
        tx = S["team"]
        if logo:
            img.paste(logo, (tx, cy - LOGO // 2), logo)
            tx += LOGO + 8
        d.text((tx, cy), _tbecs_fit(d, nm.upper(), f_name, S["team_w"] - (LOGO + 8 if logo else 0)),
               font=f_name, fill=fill, anchor="lm")
        for k in ("P", "W", "L", "PTS", "NRR"):
            v = f"{st['NRR']:+.2f}" if k == "NRR" else str(st["Pts"] if k == "PTS" else st[k])
            d.text((S[k], cy), v, font=f_row, fill=fill, anchor="mm")

    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="PNG")
    buf.seek(0)
    return buf


# Knockout bracket (tbecs_knockouts.png). Per slot: logo into the outlined square,
# name centred above its underline. Square tops probed per box (spacing is uneven).
_TBECS_KO = {
    "QF": [{"sq1": 328, "sq2": 394}, {"sq1": 515, "sq2": 578},
           {"sq1": 696, "sq2": 759}, {"sq1": 876, "sq2": 939}],
    "qf_x": 91, "qf_sq": 48, "qf_ncx": 261, "qf_n1": 22, "qf_n2": 12, "qf_nw": 215,
    "SF": [{"sq1": 411, "sq2": 483}, {"sq1": 693, "sq2": 765}],
    "sf_x": 500, "sf_sq": 48, "sf_ncx": 660, "sf_n1": 31, "sf_n2": 15, "sf_nw": 210,
    "F": {"sq1": 507, "sq2": 589, "x": 888, "sq": 47, "ncx": 1033, "n1": 44, "n2": 24, "nw": 175},
    "CH": {"cx": 1347, "cy": 583, "sz": 128, "name_cy": 689, "nw": 200},
}

def generate_tbecs_bracket(tourney) -> io.BytesIO:
    img = Image.open("assets/tbecs_knockouts.png").convert("RGBA")
    d = ImageDraw.Draw(img)
    K = _TBECS_KO
    WHITE = "#FFFFFF"
    logos = _tbecs_team_logos(tourney)
    colors = {t["name"]: t.get("color", "#334155") for t in tourney.get("teams", [])}
    sched = tourney.get("schedule", [])

    def _slot(name, sq_x, sq_top, sq_sz, ncx, n_cy, nw, base_size):
        if not name:
            return
        logo = _fetch_emoji_img(logos.get(name), sq_sz - 4)
        if logo:
            img.paste(logo, (sq_x + 2, sq_top + 2), logo)
        else:
            try:
                fill = tuple(int(colors.get(name, "#334155").lstrip("#")[i:i+2], 16) for i in (0, 2, 4))
            except Exception:
                fill = (51, 65, 85)
            d.rectangle([sq_x + 3, sq_top + 3, sq_x + sq_sz - 3, sq_top + sq_sz - 3], fill=fill)
        nf, ntxt = _tbecs_fit_full(name.upper(), base_size, nw, min_size=13)
        d.text((ncx, n_cy), ntxt, font=nf, fill=WHITE, anchor="mm")

    def _round(prefix, n):
        m = next((x for x in sched if x.get("round") == f"{prefix} {n}"), None)
        return m or {}

    for i, box in enumerate(K["QF"], 1):
        m = _round("Quarter-Final", i)
        _slot(m.get("team1"), K["qf_x"], box["sq1"], K["qf_sq"], K["qf_ncx"], box["sq1"] + K["qf_n1"], K["qf_nw"], 19)
        _slot(m.get("team2"), K["qf_x"], box["sq2"], K["qf_sq"], K["qf_ncx"], box["sq2"] + K["qf_n2"], K["qf_nw"], 19)
    for i, box in enumerate(K["SF"], 1):
        m = _round("Semi-Final", i)
        _slot(m.get("team1"), K["sf_x"], box["sq1"], K["sf_sq"], K["sf_ncx"], box["sq1"] + K["sf_n1"], K["sf_nw"], 19)
        _slot(m.get("team2"), K["sf_x"], box["sq2"], K["sf_sq"], K["sf_ncx"], box["sq2"] + K["sf_n2"], K["sf_nw"], 19)
    fm = next((x for x in sched if x.get("round") == "Final"), {})
    F = K["F"]
    _slot(fm.get("team1"), F["x"], F["sq1"], F["sq"], F["ncx"], F["sq1"] + F["n1"], F["nw"], 19)
    _slot(fm.get("team2"), F["x"], F["sq2"], F["sq"], F["ncx"], F["sq2"] + F["n2"], F["nw"], 19)

    champ = (fm.get("result") or {}).get("winner")
    if champ:
        CH = K["CH"]
        logo = _fetch_emoji_img(logos.get(champ), CH["sz"])
        if logo:
            img.paste(logo, (CH["cx"] - CH["sz"] // 2, CH["cy"] - CH["sz"] // 2), logo)
        nf, ntxt = _tbecs_fit_full(champ.upper(), 24, CH["nw"], min_size=14)
        d.text((CH["cx"], CH["name_cy"]), ntxt, font=nf, fill="#F0C242", anchor="mm")

    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="PNG")
    buf.seek(0)
    return buf


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

    # Column X centres - aligned to template header labels; right group adds R_OFF
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
            # team name - larger font
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


def generate_ccodi_points_table(tourney) -> io.BytesIO:
    """Fill assets/ccodi_table.png (dark navy, Group A left / Group B right, 5 rows each,
    POS digits baked in) with live group standings - team logos included. Top-2 (the
    semi-final qualifiers) get gold names + points."""
    img = Image.open("assets/ccodi_table.png").convert("RGBA")
    d = ImageDraw.Draw(img)

    team_logos = {t["name"]: t.get("logo_standings") or t.get("logo_match") for t in tourney.get("teams", [])}
    f_name = _acl_pt_font(22)
    f_stat = _acl_pt_font(24)
    WHITE = (255, 255, 255); GOLD = (240, 194, 66); DIM = (201, 212, 240)

    # Measured on the template: stat-column header centres; logo x = team cell's left edge.
    COLS = {
        "A": {"logo": 192, "name_max": 406, "P": 430, "W": 483, "L": 537, "NR": 593, "PTS": 660, "NRR": 738},
        "B": {"logo": 882, "name_max": 1100, "P": 1125, "W": 1178, "L": 1232, "NR": 1288, "PTS": 1354, "NRR": 1434},
    }
    ROWS = [436, 517, 597, 674, 754]   # row y-centres (aligned to the baked POS digits)
    LOGO_SZ = 42

    def _ct(cx, cy, s, f, fill=WHITE):
        bb = f.getbbox(str(s))
        d.text((cx - bb[2] / 2, cy - (bb[3] + bb[1]) / 2), str(s), font=f, fill=fill)

    def _fit(s, f, maxw):
        if f.getbbox(s)[2] <= maxw:
            return s
        while s and f.getbbox(s + "…")[2] > maxw:
            s = s[:-1]
        return s + "…"

    for grp in ("A", "B"):
        C = COLS[grp]
        rows = [(n, st) for n, st in get_group_standings(tourney, "group", grp) if n != "BYE"]
        for i, (nm, st) in enumerate(rows[:5]):
            cy = ROWS[i]
            x = C["logo"]
            logo = _fetch_emoji_img(team_logos.get(nm), LOGO_SZ)
            if logo:
                img.paste(logo, (x, cy - LOGO_SZ // 2), logo)
            x += LOGO_SZ + 8
            # shrink the font until the FULL name fits (floor 15px, then truncate)
            nf = f_name
            for fs in (22, 20, 18, 16, 15):
                nf = _acl_pt_font(fs)
                if nf.getbbox(nm.upper())[2] <= C["name_max"] - x:
                    break
            nm_txt = _fit(nm.upper(), nf, C["name_max"] - x)
            bb = nf.getbbox(nm_txt)
            d.text((x, cy - (bb[3] + bb[1]) / 2), nm_txt, font=nf,
                   fill=GOLD if i < 2 else WHITE)
            _ct(C["P"],   cy, st["P"], f_stat, DIM)
            _ct(C["W"],   cy, st["W"], f_stat, WHITE)
            _ct(C["L"],   cy, st["L"], f_stat, DIM)
            _ct(C["NR"],  cy, st.get("T", 0), f_stat, DIM)
            _ct(C["PTS"], cy, st["Pts"], f_stat, GOLD if i < 2 else WHITE)
            _ct(C["NRR"], cy, f"{st['NRR']:+.2f}", f_stat, DIM)

    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="PNG")
    buf.seek(0)
    return buf


# Default points table (every format without a bespoke one - incl. IPL)
# How many top rows are flagged as qualifying for the knockouts.
_STANDINGS_CUTOFF = {"ipl": 4, "dsl": 4, "acl": 6}


def _standings_table(standings, cutoff=None):
    """The points table as monospace rows. `cutoff` marks the qualifying places with ▸.
    The NR column only appears if someone actually has a tie/no-result."""
    show_nr = any(d["T"] for _, d in standings)

    head = f"{'#':>3} {'TEAM':<15} {'P':>2} {'W':>2} {'L':>2}"
    if show_nr:
        head += f" {'NR':>2}"
    head += f" {'PTS':>3} {'NRR':>6}"

    rows = [head, "─" * len(head)]
    for i, (name, d) in enumerate(standings, 1):
        mark = "▸" if cutoff and i <= cutoff else " "
        row = f"{mark}{i:>2} {name[:15]:<15} {d['P']:>2} {d['W']:>2} {d['L']:>2}"
        if show_nr:
            row += f" {d['T']:>2}"
        row += f" {d['Pts']:>3} {d['NRR']:>+6.2f}"
        rows.append(row)
    return rows


def build_standings_message(tourney):
    """The default standings: the points table as text, inside an embed titled with the
    tournament name. Returns the embed, or None if nothing has been played yet.

    Formats with a bespoke table image (ACL, CCODI, T20 World Cup) never reach this -
    they render their own and return before the default path.
    """
    standings = get_tournament_standings(tourney)
    if not standings or not any(d["P"] for _, d in standings):
        return None

    t_type = tourney.get("tournament_type", "round_robin")
    cutoff = _STANDINGS_CUTOFF.get(t_type)

    embed = discord.Embed(title=f"🏆 {tourney['name']} — Points Table", color=discord.Color.gold())

    played = sum(d["P"] for _, d in standings) // 2
    total = len([m for m in tourney.get("schedule", []) if isinstance(m.get("round"), int)])
    parts = [f"**{played}/{total}** league matches played · 🥇 **{standings[0][0]}** on top"]

    # The table lives in the description (4096 chars - room for far more teams than any
    # format here); it only spills into extra fields if a huge roster ever overflows it.
    rows = _standings_table(standings, cutoff)
    block = "```\n" + "\n".join(rows) + "\n```"
    overflow = []
    if len(block) + len(parts[0]) + 2 > 4000:
        keep = rows[:2] + rows[2:12]
        block = "```\n" + "\n".join(keep) + "\n```"
        rest = rows[12:]
        while rest:
            chunk, rest = rest[:15], rest[15:]
            overflow.append("```\n" + "\n".join(chunk) + "\n```")
    parts.append(block)
    embed.description = "\n".join(parts)
    for i, chunk in enumerate(overflow):
        embed.add_field(name="\u200b", value=chunk, inline=False)

    # Playoff picture, once the bracket exists.
    ko = [m for m in tourney.get("schedule", []) if m.get("stage") == "knockout"]
    if ko:
        lines = []
        for m in sorted(ko, key=lambda x: x.get("match_id", 0)):
            res = m.get("result")
            tail = f" → 🏆 **{res['winner']}**" if res else " *(pending)*"
            lines.append(f"**{m.get('round')}**: {m['team1']} vs {m['team2']}{tail}")
        embed.add_field(name="🔥 Playoffs", value="\n".join(lines), inline=False)
    elif cutoff:
        embed.set_footer(text=f"▸ the top {cutoff} qualify for the playoffs")
    return embed


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
    ROW_BOUNDS = [303, 344, 383, 420, 457, 495, 532, 569, 607, 644, 681, 719, 756, 796, 845]
    ROW_Y = [(ROW_BOUNDS[i] + ROW_BOUNDS[i + 1]) // 2 for i in range(14)]
    LOGO_CX, LOGO_SZ, NAME_X, LOGO_DY = 235, 30, 272, -4
    TINT_X0, TINT_X1 = 200, 1577
    PLAYED_X, WON_X, LOST_X, POINTS_X, NRR_X = 789, 958, 1133, 1320, 1496

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
            img.paste(logo, (int(LOGO_CX - LOGO_SZ / 2), int(y - LOGO_SZ / 2 + LOGO_DY)), logo)
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


def generate_acl_fixtures_image(tourney, team_name) -> io.BytesIO:
    """Fill assets/acl_fixtures.png with one team's fixtures (up to 13 league rows).
    Coordinates pixel-scanned from the template (1024×1536):
      • header team-logo placeholder box: x299–433, y62–182 (center 366,122)
      • 8 columns - MATCH NO | TEAM | VS | TEAM | PITCH | WEATHER | STADIUM | STATUS
      • 13 data rows, ~79px pitch
    The VS badges, match-no pills, status pills and labels are already on the template -
    we only render text/logos into the cells.
    """
    DARK  = (16, 28, 70)
    GREEN = (22, 140, 78)
    RED   = (192, 40, 52)
    GOLD  = (190, 140, 0)
    GREY  = (108, 108, 120)
    WHITE = (245, 247, 252)

    # Column x-centres
    COL_MATCH = 72      # number sits on the navy parallelogram pill
    COL_T1    = 213     # cell centre between match-no pill & VS badge (symmetric w/ T2 about VS)
    COL_T2    = 435
    COL_PITCH = 564
    COL_WEA   = 693
    COL_STAD  = 823
    COL_STAT  = 937
    # Max text widths per column (px) before wrap/shrink
    W_TEAM, W_PITCH, W_WEA, W_STAD, W_STAT = 150, 120, 124, 134, 138
    # Cell rows align to the VS badges / status pills (the row's visual centre);
    # the match NUMBER sits on its own navy pill, ~14px higher.
    ROW_CELL = [336, 415, 494, 573, 652, 731, 810, 888, 967, 1046, 1123, 1202, 1279]
    ROW_NUM  = [322, 402, 484, 563, 642, 721, 800, 879, 958, 1037, 1113, 1192, 1270]
    # Header: viewing team's logo placeholder + name gradient pill
    PH_CX, PH_CY, PH_SZ = 366, 122, 104
    NAME_CX, NAME_CY, NAME_W = 648, 186, 280

    img = Image.open("assets/acl_fixtures.png").convert("RGBA")
    d = ImageDraw.Draw(img)

    def tw(s, f):
        return d.textbbox((0, 0), str(s), font=f)[2]

    def th(f):
        bb = d.textbbox((0, 0), "Ag", font=f)
        return bb[3] - bb[1], bb[1]

    def _wrap(words, f, max_w):
        """Greedily pack words into lines each ≤max_w; None if a single word overflows."""
        lines, cur = [], ""
        for w in words:
            if tw(w, f) > max_w:
                return None
            trial = w if not cur else cur + " " + w
            if tw(trial, f) <= max_w:
                cur = trial
            else:
                lines.append(cur); cur = w
        if cur:
            lines.append(cur)
        return lines

    def _draw_lines(lines, f, cx, cy, fill):
        h, off = th(f); gap = 4; lh = h + gap
        total = len(lines) * lh - gap
        y0 = cy - total / 2
        for i, ln in enumerate(lines):
            d.text((cx - tw(ln, f) / 2, y0 + i * lh - off), ln, font=f, fill=fill)

    CELL_SZ = 20   # ONE constant font size for every data cell - uniform look

    def cell(cx, cy, s, max_w, fill=DARK, max_lines=3):
        """Render a centred cell value at the constant CELL_SZ. If it doesn't fit on
        one line, word-wrap onto ≤max_lines at the SAME size (keeps every cell the
        same font). Only a single over-long word/over-wrapped value drops below the
        constant size, and ellipsizes as the final fallback."""
        s = str(s)
        f = _acl_pt_font(CELL_SZ)
        words = s.split()
        if tw(s, f) <= max_w:
            return _draw_lines([s], f, cx, cy, fill)
        if len(words) > 1:
            lines = _wrap(words, f, max_w)
            if lines and len(lines) <= max_lines:
                return _draw_lines(lines, f, cx, cy, fill)
        # rare: a single word (or too many wrapped lines) wider than the column ->
        # shrink just this value a little until it fits the line budget.
        for sz in range(CELL_SZ - 1, 12, -1):
            f = _acl_pt_font(sz)
            if tw(s, f) <= max_w:
                return _draw_lines([s], f, cx, cy, fill)
            if len(words) > 1:
                lines = _wrap(words, f, max_w)
                if lines and len(lines) <= max_lines:
                    return _draw_lines(lines, f, cx, cy, fill)
        f = _acl_pt_font(13)
        while len(s) > 1 and tw(s + "…", f) > max_w:
            s = s[:-1]
        _draw_lines([s + "…"], f, cx, cy, fill)

    # Header: viewing team's logo into the placeholder box
    team = next((t for t in tourney.get("teams", []) if t["name"] == team_name), {})
    logo_str = team.get("logo_match") or team.get("logo_standings")
    logo = _fetch_emoji_img(logo_str, PH_SZ) if logo_str else None
    if logo is not None:
        img.paste(logo, (int(PH_CX - PH_SZ / 2), int(PH_CY - PH_SZ / 2)), logo)
        d = ImageDraw.Draw(img)

    # Header: team name on the gradient pill below FIXTURES (its own larger size)
    f_name = _acl_pt_font(30)
    _nm = team_name.upper()
    while tw(_nm, f_name) > NAME_W and f_name.size > 16:
        f_name = _acl_pt_font(f_name.size - 1)
    _draw_lines([_nm], f_name, NAME_CX, NAME_CY, WHITE)

    # Rows: this team's matches in schedule order, capped to the 13 template rows
    mine = [m for m in tourney.get("schedule", [])
            if m.get("team1") == team_name or m.get("team2") == team_name]
    mine.sort(key=lambda m: m.get("match_id", 0))

    f_num = _acl_pt_font(26)
    for m, y_cell, y_num in zip(mine[:len(ROW_CELL)], ROW_CELL, ROW_NUM):
        _draw_lines([str(m.get("match_id", "?"))], f_num, COL_MATCH, y_num, WHITE)
        t1, t2 = m.get("team1", "TBD"), m.get("team2", "TBD")
        # locked knockout slots store a source label instead of a resolved team
        if m.get("status") == "locked":
            t1 = t1 if t1 != "TBD" else (m.get("team1_src") or "TBD")
            t2 = t2 if t2 != "TBD" else (m.get("team2_src") or "TBD")
        cell(COL_T1, y_cell, t1.upper(), W_TEAM)
        cell(COL_T2, y_cell, t2.upper(), W_TEAM)
        cell(COL_PITCH, y_cell, m.get("pitch") or "—", W_PITCH)
        cell(COL_WEA,   y_cell, m.get("weather") or "—", W_WEA)
        cell(COL_STAD,  y_cell, m.get("stadium") or "—", W_STAD)

        status = m.get("status")
        if status == "completed" and m.get("result"):
            w = m["result"].get("winner")
            if w == "TIE":            stat_txt, stat_col = "TIE", GOLD
            elif w == team_name:      stat_txt, stat_col = "WON", GREEN
            else:                     stat_txt, stat_col = "LOST", RED
        elif status == "locked":      stat_txt, stat_col = "TBD", GREY
        else:                         stat_txt, stat_col = "READY", GREEN
        cell(COL_STAT, y_cell, stat_txt, W_STAT, fill=stat_col, max_lines=1)

    out = Image.new("RGB", img.size, (255, 255, 255))
    out.paste(img, mask=img.split()[3])
    buf = io.BytesIO()
    out.save(buf, format="PNG")
    buf.seek(0)
    return buf


class FixturesView(discord.ui.View):
    """Adds a button under `cv fixtures` to toggle between the text embed and the
    ACL fixtures image. Only attached for ACL tournaments (the template is ACL)."""
    def __init__(self, tourney, team_name, *, timeout=300):
        super().__init__(timeout=timeout)
        self.tourney = tourney
        self.team_name = team_name
        self.showing_image = False

    @discord.ui.button(label="View as Image", emoji="🖼️", style=discord.ButtonStyle.primary)
    async def toggle(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        if not self.showing_image:
            try:
                buf = generate_acl_fixtures_image(self.tourney, self.team_name)
            except Exception as e:
                print(f"Fixtures image render failed: {e}")
                return await interaction.followup.send(f"⚠️ Couldn't render the fixtures image: {e}", ephemeral=True)
            self.showing_image = True
            button.label, button.emoji = "View as List", "📋"
            file = discord.File(buf, filename=f"{self.team_name}_fixtures.png")
            await interaction.message.edit(embed=None, attachments=[file], view=self)
        else:
            self.showing_image = False
            button.label, button.emoji = "View as Image", "🖼️"
            await interaction.message.edit(
                embed=build_team_fixtures_embed(self.tourney, self.team_name),
                attachments=[], view=self,
            )


def build_fixtures_view(tourney, team_name):
    """Return a FixturesView for ACL tournaments, else None (no image template)."""
    if tourney.get("tournament_type") == "acl":
        return FixturesView(tourney, team_name)
    return None


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

    # Column X centres - pixel-scanned from super8_table.png (1484px wide, two groups)
    # POS numbers are pre-printed in the template; L_TEAM_X is where logo/name rendering begins
    L_TEAM_X = 105
    L_P_X    = 365
    L_W_X    = 423
    L_L_X    = 480
    L_NR_X   = 539
    L_PTS_X  = 605
    L_NRR_X  = 682
    R_OFF    = 710

    # 4 team rows per group - sub-header at y=425..492, data rows below it
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
            # stat columns - POS numbers are pre-printed in template, skip them
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
            # team name - larger font
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
    # > name above logo: name_cy=490, logo_cy=583 (aligned with VS row)
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

    if t_type == "custom":
        # Per stage x group pages (chunked) + Knockouts, straight off the config.
        return build_custom_status_pages(tourney)

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
    elif t_type == "ccodi":
        # Per-group pages (with standings) + knockouts - used by cvt groups.
        for grp in ["A", "B"]:
            matches = [m for m in schedule if m.get("stage") == "group" and m.get("group") == grp]
            if matches:
                pages.append((f"Group {grp}", "group", grp, matches))
        ko = [m for m in schedule if m.get("stage") == "knockout"]
        if ko:
            pages.append(("Knockouts", "knockout", None, ko))
    else:
        rounds = sorted(set(m["round"] for m in schedule if isinstance(m.get("round"), int)))
        if t_type == "double_round_robin":
            chunk_rounds, chunk_matches = [], []
            for r in rounds:
                matches = [m for m in schedule if m.get("round") == r]
                if chunk_matches and len(chunk_matches) + len(matches) > _FLAT_PAGE_SIZE:
                    first, last = chunk_rounds[0], chunk_rounds[-1]
                    title = f"Round {first}" if first == last else f"Rounds {first}-{last}"
                    pages.append((title, "round", (first, last), chunk_matches))
                    chunk_rounds, chunk_matches = [], []
                chunk_rounds.append(r)
                chunk_matches.extend(matches)
            if chunk_matches:
                first, last = chunk_rounds[0], chunk_rounds[-1]
                title = f"Round {first}" if first == last else f"Rounds {first}-{last}"
                pages.append((title, "round", (first, last), chunk_matches))
        else:
            for r in rounds:
                matches = [m for m in schedule if m.get("round") == r]
                pages.append((f"Round {r}", "round", r, matches))
        ko = [m for m in schedule if not isinstance(m.get("round"), int)]
        if ko:
            pages.append(("Knockouts", "knockout", None, ko))

    return pages


def _build_ccodi_round_pages(tourney):
    """One page per round (4 matches, distinct venues) + a Knockouts page -
    the round-wise cvt status view for new CCODI seasons."""
    schedule = tourney.get("schedule", [])
    pages = []
    rounds = sorted({m["round"] for m in schedule if isinstance(m.get("round"), int)})
    for r in rounds:
        pages.append((f"Round {r}", "round", r, [m for m in schedule if m.get("round") == r]))
    ko = [m for m in schedule if m.get("stage") == "knockout"]
    if ko:
        pages.append(("Knockouts", "knockout", None, ko))
    return pages


_FLAT_PAGE_SIZE = 10

def _build_flat_pages(tourney):
    """Flat pages sorted by match_id - used by cvt status for T20 WC."""
    schedule = sorted(tourney.get("schedule", []), key=lambda m: m["match_id"])
    pages = []
    for i in range(0, len(schedule), _FLAT_PAGE_SIZE):
        chunk = schedule[i:i + _FLAT_PAGE_SIZE]
        first_id = chunk[0]["match_id"]
        last_id = chunk[-1]["match_id"]
        pages.append((f"Fixtures #{first_id}–#{last_id}", "flat", None, chunk))
    return pages


def _conditions_label(m):
    """Short pitch · weather (· venue) tag for a scheduled match (manual matches show 'at match')."""
    p, w = m.get("pitch"), m.get("weather")
    base = f"🏟️ {p} · 🌤️ {w}" if (p and w) else "🏟️ *picked at match*"
    venue = stadium_label(m)
    if venue:
        base += f" · {venue}"
    return base


def _build_status_embed(tourney, page_info):
    """Build the embed for one status page."""
    title, stage_type, group_key, matches = page_info
    embed = discord.Embed(
        title=f"🏆 {tourney['name']} — {title}",
        color=discord.Color.gold()
    )

    lines = []
    show_round_headers = (stage_type == "round" and isinstance(group_key, tuple))
    last_round = None
    for m in matches:
        if show_round_headers and m.get("round") != last_round:
            if lines:
                lines.append("")
            last_round = m.get("round")
            lines.append(f"**Round {last_round}**")

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
            if r.get("walkover"):
                lines.append(f"`#{m['match_id']}` {tag}{t1b} vs {t2b} — 🏆 awarded to **{w}** ✅")
            else:
                lines.append(f"`#{m['match_id']}` {tag}{t1b} {r['t1_runs']}/{r['t1_wickets']} vs {t2b} {r['t2_runs']}/{r['t2_wickets']} ✅")
        else:
            # ACL playoff slots may be unresolved (None) - show their TBD source label
            a = f"**{m['team1']}**" if m.get("team1") else f"*{m.get('team1_src', 'TBD')}*"
            b = f"**{m['team2']}**" if m.get("team2") else f"*{m.get('team2_src', 'TBD')}*"
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

    if tourney.get("tournament_type") == "custom" and group_key and stage_type not in ("knockout", "flat"):
        # Custom pages carry their stage key in stage_type; standings are
        # carry-over aware and the cutoff comes from the stage's config.
        from league.custom_tournament import stage_index_of
        idx = stage_index_of(stage_type)
        st = custom_stage_standings(tourney, idx, group_key) if idx is not None else []
        cutoff = custom_stage_cutoff(tourney, stage_type)
        if st:
            rows = ["```", f"{'':2}{'#':<3}{'Team':<20}{'P':>2}{'W':>2}{'L':>2}{'Pts':>4}{'NRR':>7}", "─"*44]
            for i, (nm, d) in enumerate(st, 1):
                arrow = "→ " if cutoff and i <= cutoff else "  "
                rows.append(f"{arrow}{i:<3}{nm[:18]:<20}{d['P']:>2}{d['W']:>2}{d['L']:>2}{d['Pts']:>4}{d['NRR']:>+7.2f}")
            rows.append("```")
            label = f"Standings  (→ top {cutoff} advance)" if cutoff else "Standings  (table decides the champion)"
            embed.add_field(name=label, value="\n".join(rows), inline=False)
    elif stage_type in ("group", "super8") and group_key:
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
    """ /  navigation through Group Stage -> Super 8 -> Knockouts standings images."""

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


class TournamentLeaderboardView(discord.ui.View):
    """ /  paginated leaderboard - shows up to `len(lines)` entries, 10 per page.
    Used by the runs / wickets / MVP leaderboards (first 50, 5 pages).
    (Named distinctly from bot.py's career `LeaderboardView` to avoid a shadow clash.)"""

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


def build_player_stats_embed(stats, pname, tname, overall=None, season_label=None):
    """Shared tournament player-stats embed (slash + prefix, after the team is resolved).
    `overall` (DSL): merged all-season totals dict (same keys + 'seasons'/'teams') -
    rendered as an extra field. `season_label` retitles the current block (e.g. 'Season 3')."""
    sr = (stats["runs"] / stats["balls_faced"] * 100) if stats["balls_faced"] > 0 else 0.0
    bat_avg = (stats["runs"] / stats["outs"]) if stats["outs"] > 0 else float(stats["runs"])
    bowl_avg = (stats["runs_conceded"] / stats["wickets"]) if stats["wickets"] > 0 else 0.0
    econ = (stats["runs_conceded"] / stats["balls_bowled"] * 6) if stats["balls_bowled"] > 0 else 0.0
    title = f"📊 {season_label} Stats: {pname}" if season_label else f"📊 Tournament Stats: {pname}"
    embed = discord.Embed(title=title,
                          description=f"**Team:** {tname} | **Matches:** {stats['matches']}",
                          color=discord.Color.blue())
    bat_str = (f"**Runs:** {stats['runs']}\n**Strike Rate:** {sr:.1f}\n**Average:** {bat_avg:.1f}\n"
               f"**4s:** {stats['fours']} | **6s:** {stats['sixes']}\n**50s:** {stats['fifties']} | **100s:** {stats['hundreds']}")
    embed.add_field(name="🏏 Batting", value=bat_str, inline=True)
    o = stats['balls_bowled'] // 6; b = stats['balls_bowled'] % 6
    bowl_str = f"**Wickets:** {stats['wickets']}\n**Economy:** {econ:.1f}\n**Bowling Avg:** {bowl_avg:.1f}\n**Overs:** {o}.{b}"
    embed.add_field(name="🎯 Bowling", value=bowl_str, inline=True)
    if overall and overall.get("matches", 0) > stats.get("matches", 0):
        osr  = (overall["runs"] / overall["balls_faced"] * 100) if overall["balls_faced"] > 0 else 0.0
        oavg = (overall["runs"] / overall["outs"]) if overall["outs"] > 0 else float(overall["runs"])
        oeco = (overall["runs_conceded"] / overall["balls_bowled"] * 6) if overall["balls_bowled"] > 0 else 0.0
        embed.add_field(
            name=f"🌏 Overall — {overall.get('seasons', '?')} season(s)",
            value=(f"**M:** {overall['matches']} · **Runs:** {overall['runs']} (@{osr:.0f} SR, avg {oavg:.1f})\n"
                   f"**50s/100s:** {overall['fifties']}/{overall['hundreds']} · "
                   f"**Wkts:** {overall['wickets']} (econ {oeco:.1f})"),
            inline=False)
    return embed


def find_player_in_tournament(tourney, player_name):
    """Every (team, player_key) whose stats hold `player_name`. Exact (case-insensitive)
    match across all teams first; if none, one fuzzy close-match. Team need not be given."""
    stats_map = tourney.get("stats", {})
    pl = player_name.lower()
    exact = [(t, p) for t, players in stats_map.items() for p in players if p.lower() == pl]
    if exact:
        return exact
    all_pairs = [(t, p) for t, players in stats_map.items() for p in players]
    close = difflib.get_close_matches(player_name, [p for _, p in all_pairs], n=1, cutoff=0.5)
    if close:
        return [(t, p) for t, p in all_pairs if p == close[0]]
    return []


class PlayerStatsTeamSelectView(discord.ui.View):
    """Asks which team's player to show when the same name is on more than one team."""

    def __init__(self, stats_map, matches):
        super().__init__(timeout=60)
        self.stats_map = stats_map
        self.by_team = {t: p for t, p in matches}
        opts = [discord.SelectOption(label=t[:100], description=f"{p}'s stats"[:100]) for t, p in matches[:25]]
        self.sel = discord.ui.Select(placeholder="Choose a team…", options=opts)
        self.sel.callback = self._cb
        self.add_item(self.sel)

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True

    async def _cb(self, interaction: discord.Interaction):
        t = self.sel.values[0]
        p = self.by_team[t]
        await interaction.response.edit_message(
            content=None, embed=build_player_stats_embed(self.stats_map[t][p], p, t), view=None)


class SquadConfirmView(discord.ui.View):
    """Confirm/Cancel prompt shown before a parsed squad is saved to a team.
    Restricted to the submitter. value: None=timeout, True=confirm, False=cancel."""

    def __init__(self, author_id: int):
        super().__init__(timeout=120)
        self.author_id = author_id
        self.value = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("❌ Only the person submitting this squad can confirm it.", ephemeral=True)
            return False
        return True

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True

    @discord.ui.button(label="✅ Confirm", style=discord.ButtonStyle.success)
    async def confirm_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.value = True
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(view=self)
        self.stop()

    @discord.ui.button(label="❌ Cancel", style=discord.ButtonStyle.danger)
    async def cancel_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.value = False
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(view=self)
        self.stop()


def build_squad_confirm_text(team_name: str, found_players: list, fuzzy_corrections: list) -> str:
    """Preview message for a parsed squad awaiting confirmation. Flags any name that
    wasn't an exact match so the submitter can catch a wrong auto-correction."""
    roster = "\n".join(f"{i}. {p['name']}" for i, p in enumerate(found_players, 1))
    txt = f"📋 **Confirm Squad for {team_name}** — {len(found_players)} players:\n{roster}\n"
    if fuzzy_corrections:
        corr = ", ".join(f"`{inp}` → **{nm}**" for inp, nm in fuzzy_corrections)
        txt += f"\n⚠️ **Auto-corrected names:** {corr}\n"
    txt += "\nClick **✅ Confirm** to save or **❌ Cancel** to abort."
    return txt


def build_squad_confirm_embed(team_name: str, found_players: list, fuzzy_corrections: list) -> discord.Embed:
    """Embed version of the squad-confirm preview. Embeds allow up to 4096 chars in the
    description (vs 2000 for plain content), so large squads (e.g. 30 players) don't
    blow the message limit. Roster is laid out in two columns to stay compact."""
    roster_lines = [f"{i}. {p['name']}" for i, p in enumerate(found_players, 1)]
    desc = "\n".join(roster_lines)
    if len(desc) > 3900:  # hard safety margin under the 4096 description cap
        desc = desc[:3900].rsplit("\n", 1)[0] + "\n…"
    embed = discord.Embed(
        title=f"📋 Confirm Squad for {team_name}",
        description=desc,
        color=discord.Color.gold(),
    )
    embed.set_author(name=f"{len(found_players)} players")
    if fuzzy_corrections:
        corr = ", ".join(f"`{inp}` → **{nm}**" for inp, nm in fuzzy_corrections)
        if len(corr) > 1000:
            corr = corr[:1000].rsplit(",", 1)[0] + ", …"
        embed.add_field(name="⚠️ Auto-corrected names", value=corr, inline=False)
    embed.set_footer(text="✅ Confirm to save · ❌ Cancel to abort")
    return embed


# ACL - Akatsuki Cricket League: playoff + Super Cup engine
# League (91) -> Top-6 Playoffs (6 matches) -> Akatsuki Super Cup.
#  Structure note: the moment the league ends, the #1 (League Shield) is
# already locked into the Super Cup - so the Super Cup is created up front
#  with one finalist known and the other (ACL Trophy Winner) as TBD.
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
    tourney["league_shield"] = s1            # #1 -> direct Super Cup spot
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

    # Round 1 - teams known, ready to play (any order / in parallel)
    mk("Qualifier",        ACL_PLAYOFF_STAGE,  s1, s2,   "1st · League", "2nd · League", "pending")
    mk("Eliminator 1",     ACL_PLAYOFF_STAGE,  s3, s6,   "3rd · League", "6th · League", "pending")
    mk("Eliminator 2",     ACL_PLAYOFF_STAGE,  s4, s5,   "4th · League", "5th · League", "pending")
    # Knockouts - locked until feeders resolve
    mk("The Knockout", ACL_PLAYOFF_STAGE,  None, None, "Winner · Eliminator 1", "Winner · Eliminator 2", "locked")
    mk("Qualifier 2",      ACL_PLAYOFF_STAGE,  None, None, "Loser · Qualifier",     "Winner · The Knockout", "locked")
    mk("Grand Final",      ACL_PLAYOFF_STAGE,  None, None, "Winner · Qualifier",    "Winner · Qualifier 2", "locked")
    # Super Cup apex - Shield finalist locked in already; opponent = ACL Trophy Winner (TBD)
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

    # Qualifier -> winner to Grand Final, loser to Qualifier 2
    qw, ql = _acl_winner_loser(q)
    if qw:
        _acl_fill(gf, "team1", qw)
        _acl_fill(sf, "team1", ql)
    # Eliminators -> The Knockout
    e1w, _ = _acl_winner_loser(e1)
    e2w, _ = _acl_winner_loser(e2)
    if e1w: _acl_fill(ef, "team1", e1w)
    if e2w: _acl_fill(ef, "team2", e2w)
    # The Knockout -> Qualifier 2 (slot 2)
    efw, _ = _acl_winner_loser(ef)
    if efw: _acl_fill(sf, "team2", efw)
    # Qualifier 2 -> Grand Final (slot 2)
    sfw, _ = _acl_winner_loser(sf)
    if sfw: _acl_fill(gf, "team2", sfw)

    # Grand Final -> ACL Trophy Winner, then Super Cup branch
    trophy, runner_up = _acl_winner_loser(gf)
    if trophy:
        tourney["acl_trophy_winner"] = trophy
        tourney["acl_runner_up"] = runner_up
        shield = tourney.get("league_shield")
        slq = _acl_get(tourney, "Super League Qualifier")
        if trophy == shield:
            # DOMESTIC DOUBLE -> Super League Qualifier decides the Super Cup challenger
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

    # Super League Qualifier -> Super Cup challenger
    slqw, _ = _acl_winner_loser(_acl_get(tourney, "Super League Qualifier"))
    if slqw:
        _acl_fill(sc, "team2", slqw)

    # Super Cup complete -> crown the champion
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


# IPL playoffs
# Seeded off the SINGLE combined 10-team table (the groups only shape the fixture
# list - exactly as the real IPL does):
# Qualifier 1: 1st v 2nd Eliminator: 3rd v 4th
#   Qualifier 2: L(Q1) v W(Eliminator)
# Final: W(Q1) v W(Q2)
IPL_PLAYOFF_ORDER = ["Qualifier 1", "Eliminator", "Qualifier 2", "Final"]


def ipl_try_advance(tourney):
    """Progressively build the IPL playoff ladder as feeder results come in."""
    sched = tourney["schedule"]

    def _get(round_name):
        return next((m for m in sched if m.get("round") == round_name), None)

    def _add(round_name, t1, t2, t1_src=None, t2_src=None):
        m = {"match_id": _tm_next_mid(tourney), "round": round_name, "stage": "knockout",
             "team1": t1, "team2": t2, "status": "pending", "result": None}
        if t1_src: m["team1_src"] = t1_src
        if t2_src: m["team2_src"] = t2_src
        sched.append(m)

    def _done(m):
        return m and m["status"] == "completed" and m.get("result")

    def _wl(m):
        w = m["result"]["winner"]
        l = m["result"].get("loser") or (m["team2"] if w == m["team1"] else m["team1"])
        return w, l

    # Stage 1: league complete -> Qualifier 1 (1v2) + Eliminator (3v4)
    league = [m for m in sched if m.get("stage") == "group"]
    if not league or any(m["status"] != "completed" for m in league):
        return
    if not _get("Qualifier 1"):
        top = [n for n, _ in get_tournament_standings(tourney)][:4]
        if len(top) < 4:
            return
        _add("Qualifier 1", top[0], top[1], "1st · League", "2nd · League")
        _add("Eliminator",  top[2], top[3], "3rd · League", "4th · League")
        return

    # Stage 2: Q1 + Eliminator complete -> Qualifier 2 (Q1 loser v Eliminator winner)
    q1, elim = _get("Qualifier 1"), _get("Eliminator")
    if _done(q1) and _done(elim) and not _get("Qualifier 2"):
        _, lq1 = _wl(q1)
        welim, _ = _wl(elim)
        _add("Qualifier 2", lq1, welim, "Loser · Qualifier 1", "Winner · Eliminator")
        return

    # Stage 3: Q2 complete -> Final (Q1 winner v Q2 winner)
    q2 = _get("Qualifier 2")
    if _done(q1) and _done(q2) and not _get("Final"):
        wq1, _ = _wl(q1)
        wq2, _ = _wl(q2)
        _add("Final", wq1, wq2, "Winner · Qualifier 1", "Winner · Qualifier 2")

    # Final done -> season over
    if _done(_get("Final")) and tourney.get("status") != "completed":
        tourney["status"] = "completed"


# Fixtures & owner-launch (shared by slash + prefix)
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


# Match-order policies (chosen at creation; stored as tourney["match_order"]).
# Older tournaments have no field -> "random", the pre-existing behaviour.
MATCH_ORDER_LABELS = {
    "random":     "🎲 Random — anyone can start any ready match",
    "sequential": "🔢 Strict Schedule — matches must be played in exact order",
    "round":      "🔁 Strict Round — any match within the current round",
}


def match_order_gate(tourney, match):
    """(ok, message): may this pending match be launched under the tournament's
    match-order policy? Locked matches are rejected elsewhere; knockout ordering
    is already enforced by the locked-slot system, so:
      random     -> always ok
      sequential -> every earlier-numbered pending match must be done first
      round      -> no earlier ROUND may still have pending matches (integer rounds)
    """
    mode = tourney.get("match_order", "random")
    if mode == "random":
        return True, ""
    sched = tourney.get("schedule", [])
    if mode == "sequential":
        blocker = min((m for m in sched
                       if m.get("status") == "pending" and m.get("match_id", 0) < match.get("match_id", 0)),
                      key=lambda m: m.get("match_id", 0), default=None)
        if blocker:
            return False, (f"❌ **Strict Schedule:** Match **#{blocker['match_id']}** "
                           f"({blocker.get('team1', '?')} vs {blocker.get('team2', '?')}) must be played first.")
        return True, ""
    if mode == "round":
        if not isinstance(match.get("round"), int):
            return True, ""   # knockouts: the bracket's locks already order them
        pending_rounds = [m["round"] for m in sched
                          if m.get("status") == "pending" and isinstance(m.get("round"), int)]
        cur = min(pending_rounds) if pending_rounds else None
        if cur is not None and match["round"] > cur:
            left = sum(1 for m in sched if m.get("status") == "pending" and m.get("round") == cur)
            return False, (f"❌ **Strict Round:** Round **{cur}** still has **{left}** pending "
                           f"match(es) — finish those before Round {match['round']}.")
        return True, ""
    return True, ""


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
            if r.get("walkover"):
                results.append(f"`#{m['match_id']}` {rlabel} · vs **{opp}**  *(walkover)*  {outcome}")
            else:
                results.append(f"`#{m['match_id']}` {rlabel} · vs **{opp}**  {my_r}/{my_w} : {op_r}/{op_w}  {outcome}")
        elif m["status"] == "locked":
            upcoming.append(f"`#{m['match_id']}` {rlabel} · vs *{opp_src or 'TBD'}*  🔒 awaiting earlier results\n     └ {_conditions_label(m)}")
        else:  # pending -> launchable
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


_TM_STAT_KEYS = ("matches", "runs", "balls_faced", "outs", "fours", "sixes",
                 "fifties", "hundreds", "wickets", "runs_conceded", "balls_bowled")
_TM_STAT_DEFAULT = {k: 0 for k in _TM_STAT_KEYS}


# Full tournament report (cvt summary) - the keep-before-you-delete record
def _summary_mvp(s, odi=False):
    """Same MVP formula as the leaderboard command. Format-aware: the SR tiers and
    economy anchor were T20 numbers (SR 110+ earns a bonus, econ 8 is par) - judged by
    those, every ODI batter reads "slow" and every ODI bowler "miserly", so ODI uses its
    own anchors (SR ~90 par, econ ~5.8 par).
    ODI scoring is BOOST-ONLY (Jaiv): a high SR / good economy is a bonus on top of runs /
    wickets, but a low SR is never a penalty (runs stand on their own) and a bowler is never
    docked for going for runs (the wickets stand on their own). T20 keeps its penalties."""
    sr = (s["runs"] / s["balls_faced"] * 100) if s["balls_faced"] > 0 else 0
    bat = float(s["runs"])
    if odi:
        if sr >= 115:   bat *= 1.30      # ODI: SR is a bonus only - no low-SR penalty
        elif sr >= 100: bat *= 1.20
        elif sr >= 90:  bat *= 1.10
    else:
        if sr >= 150:   bat *= 1.30
        elif sr >= 130: bat *= 1.20
        elif sr >= 110: bat *= 1.10
        elif sr < 80 and s["balls_faced"] >= 20: bat *= 0.85
    bat += s["fifties"] * 15 + s["hundreds"] * 40
    bat += s["sixes"] * 2 + s["fours"] * 0.5
    econ = (s["runs_conceded"] / s["balls_bowled"] * 6) if s["balls_bowled"] > 0 else 9.0
    bowl = float(s["wickets"] * 40)
    if odi:
        if s["balls_bowled"] >= 30:
            bowl += max(0.0, min(25.0, (5.8 - econ) * 6))    # ODI: economy is a bonus only
    elif s["balls_bowled"] >= 12:
        bowl += max(-25.0, min(25.0, (8.0 - econ) * 5))
    return bat + bowl


def _standings_block(rows, top_n=None):
    """Fixed-width points table code block from get_tournament_standings-style rows."""
    out = ["```", f"{'#':<3}{'Team':<19}{'P':>3}{'W':>3}{'L':>3}{'T':>3}{'Pts':>5}{'NRR':>8}", "─" * 47]
    for i, (nm, d) in enumerate(rows[:top_n] if top_n else rows, 1):
        out.append(f"{i:<3}{str(nm)[:17]:<19}{d['P']:>3}{d['W']:>3}{d['L']:>3}{d.get('T', 0):>3}{d['Pts']:>5}{d['NRR']:>+8.2f}")
    out.append("```")
    return "\n".join(out)


def build_tournament_summary_embeds(tourney):
    """The complete tournament record as a list of embeds: overview, standings,
    knockout results, every leaderboard in detail, and match records. Designed to
    be posted (and screenshotted/pinned) before the tournament is deleted."""
    sched = tourney.get("schedule", [])
    done = [m for m in sched if m.get("status") == "completed" and m.get("result")]
    t_type = tourney.get("tournament_type", "round_robin")
    type_label = {"double_round_robin": "Double Round Robin", "t20_world_cup": "T20 World Cup",
                  "acl": "Akatsuki Cricket League", "ccodi": "CCODI",
                  "dsl": "Dominators Super League", "rating": "Conquest League",
                  "ipl": "Indian Premier League", "custom": CUSTOM_TYPE_LABEL}.get(t_type, "Round Robin")
    gold = discord.Color.gold()
    embeds = []

    # 1. OVERVIEW
    champion = (tourney.get("acl_champion") or tourney.get("dsl_champion")
                or tourney.get("custom_champion")
                or next((m["result"]["winner"] for m in done
                         if str(m.get("round")) in ("Final", "Grand Final")), None))
    runner = tourney.get("acl_runner_up") or tourney.get("dsl_runner_up") or tourney.get("custom_runner_up")
    if champion and not runner:
        fin = next((m for m in done if str(m.get("round")) in ("Final", "Grand Final")), None)
        if fin:
            runner = fin["result"].get("loser") or (fin["team2"] if champion == fin["team1"] else fin["team1"])
    ties = sum(1 for m in done if m["result"].get("winner") == "TIE")
    walkovers = sum(1 for m in done if m["result"].get("walkover"))
    ov = discord.Embed(title=f"📖 {tourney['name']} — Complete Tournament Report", color=gold)
    desc = f"**{type_label}** · {tourney.get('format_overs', 20)} overs · status: **{tourney.get('status', '?')}**"
    if tourney.get("season"): desc += f" · Season **{tourney['season']}**"
    if champion:
        desc += f"\n\n👑 **CHAMPIONS: {champion}**" + (f"  ·  🥈 Runner-up: **{runner}**" if runner else "")
    if tourney.get("league_shield"):
        desc += f"\n🛡️ League Shield: **{tourney['league_shield']}**"
    ov.description = desc
    ov.add_field(name="Teams", value=str(len(tourney.get("teams", []))), inline=True)
    ov.add_field(name="Matches", value=f"{len(done)}/{len(sched)} played", inline=True)
    ov.add_field(name="Ties / Walkovers", value=f"{ties} / {walkovers}", inline=True)
    embeds.append(ov)

    # 2. STANDINGS
    st_e = discord.Embed(title="🏁 Final Standings", color=gold)
    if t_type == "custom":
        from league.custom_tournament import stage_key, stage_name
        cfg = tourney.get("custom_config") or {"stages": []}
        for idx, stg in enumerate(cfg["stages"]):
            if not any(m.get("stage") == stage_key(idx) for m in sched):
                continue
            for grp in custom_stage_letters(stg):
                rows = custom_stage_standings(tourney, idx, grp)
                if rows:
                    nm = stage_name(cfg, idx) + (f" — Group {grp}" if stg["groups"] > 1 else "")
                    st_e.add_field(name=nm, value=_standings_block(rows), inline=False)
    elif t_type == "t20_world_cup":
        for grp in ["A", "B", "C", "D"]:
            rows = get_group_standings(tourney, "group", grp)
            if rows:
                st_e.add_field(name=f"Group {grp}", value=_standings_block(rows), inline=False)
        for sg in ["A", "B"]:
            rows = get_group_standings(tourney, "super8", sg)
            if rows:
                st_e.add_field(name=f"Super 8 — Group {sg}", value=_standings_block(rows), inline=False)
    else:
        rows = [(n, d) for n, d in get_tournament_standings(tourney) if n != "BYE"]
        if rows:
            st_e.description = _standings_block(rows)
    if st_e.description or st_e.fields:
        embeds.append(st_e)

    # 3. KNOCKOUT / PLAYOFF RESULTS
    ko = [m for m in done if not isinstance(m.get("round"), int) and m.get("stage") != "group"]
    if ko:
        lines = []
        for m in sorted(ko, key=lambda x: x.get("match_id", 0)):
            r = m["result"]
            if r.get("walkover"):
                lines.append(f"**{m.get('round')}** · {m['team1']} vs {m['team2']} → 🏆 **{r['winner']}** *(walkover)*")
            else:
                lines.append(f"**{m.get('round')}** · {m['team1']} {r['t1_runs']}/{r['t1_wickets']} vs "
                             f"{m['team2']} {r['t2_runs']}/{r['t2_wickets']} → 🏆 **{r['winner']}**")
        ko_e = discord.Embed(title="🔥 Knockout Stage", description="\n".join(lines[:20]), color=gold)
        embeds.append(ko_e)

    # 4. LEADERBOARDS (all of them, in detail)
    players = [(t, p, s) for t, m in tourney.get("stats", {}).items() for p, s in m.items()]
    if players:
        def top(key_fn, n=10, cond=lambda s: True):
            pool = [(t, p, s) for t, p, s in players if cond(s)]
            return sorted(pool, key=lambda x: key_fn(x[2]), reverse=True)[:n]

        def fmt(rows, val_fn):
            return "\n".join(f"`{i:>2}.` **{p}** ({t}) — {val_fn(s)}"
                             for i, (t, p, s) in enumerate(rows, 1)) or "—"

        bat_e = discord.Embed(title="🏏 Batting Leaderboards", color=discord.Color.orange())
        bat_e.add_field(name="🧢 Most Runs", value=fmt(
            top(lambda s: s["runs"]),
            lambda s: f"**{s['runs']}** runs · {s['runs']/s['balls_faced']*100 if s['balls_faced'] else 0:.0f} SR · "
                      f"avg {s['runs']/s['outs'] if s['outs'] else s['runs']:.1f} · {s['fifties']}×50 {s['hundreds']}×100"), inline=False)
        bat_e.add_field(name="⚡ Best Strike Rate (min 50 runs)", value=fmt(
            top(lambda s: s["runs"]/s["balls_faced"] if s["balls_faced"] else 0, 5, lambda s: s["runs"] >= 50),
            lambda s: f"**{s['runs']/s['balls_faced']*100:.1f}** SR ({s['runs']} runs)"), inline=False)
        bat_e.add_field(name="🧮 Best Average (min 50 runs)", value=fmt(
            top(lambda s: s["runs"]/max(1, s["outs"]), 5, lambda s: s["runs"] >= 50),
            lambda s: f"**{s['runs']/max(1, s['outs']):.1f}** avg ({s['runs']} runs, {s['outs']} outs)"), inline=False)
        bat_e.add_field(name="💣 Most Sixes", value=fmt(
            top(lambda s: s["sixes"], 5), lambda s: f"**{s['sixes']}** sixes"), inline=True)
        bat_e.add_field(name="🎯 Most Fours", value=fmt(
            top(lambda s: s["fours"], 5), lambda s: f"**{s['fours']}** fours"), inline=True)
        bat_e.add_field(name="🏅 50s / 100s", value=fmt(
            top(lambda s: s["fifties"] + 2 * s["hundreds"], 5, lambda s: s["fifties"] + s["hundreds"] > 0),
            lambda s: f"**{s['fifties']}**×50 · **{s['hundreds']}**×100"), inline=True)
        embeds.append(bat_e)

        bowl_e = discord.Embed(title="🎳 Bowling & MVP Leaderboards", color=discord.Color.purple())
        bowl_e.add_field(name="🟣 Most Wickets", value=fmt(
            top(lambda s: s["wickets"]),
            lambda s: f"**{s['wickets']}** wkts · econ {s['runs_conceded']/s['balls_bowled']*6 if s['balls_bowled'] else 0:.2f} · "
                      f"{s['balls_bowled']//6}.{s['balls_bowled']%6} ov"), inline=False)
        bowl_e.add_field(name="🪙 Best Economy (min 5 overs)", value=fmt(
            sorted([(t, p, s) for t, p, s in players if s["balls_bowled"] >= 30],
                   key=lambda x: x[2]["runs_conceded"]/x[2]["balls_bowled"])[:5],
            lambda s: f"**{s['runs_conceded']/s['balls_bowled']*6:.2f}** rpo ({s['wickets']} wkts)"), inline=False)
        bowl_e.add_field(name="📐 Best Bowling Avg (min 3 wkts)", value=fmt(
            sorted([(t, p, s) for t, p, s in players if s["wickets"] >= 3],
                   key=lambda x: x[2]["runs_conceded"]/x[2]["wickets"])[:5],
            lambda s: f"**{s['runs_conceded']/s['wickets']:.1f}** avg ({s['wickets']} wkts)"), inline=False)
        _mvp_odi = tourney.get("format_overs", 20) >= 35
        bowl_e.add_field(name="🏆 MVP Standings", value=fmt(
            top(lambda s: _summary_mvp(s, _mvp_odi)),
            lambda s: f"**{_summary_mvp(s, _mvp_odi):.0f}** pts — {s['runs']}R · {s['wickets']}W"), inline=False)
        embeds.append(bowl_e)

    # 5. MATCH RECORDS
    real = [m for m in done if not m["result"].get("walkover")]
    if real:
        rec_e = discord.Embed(title="📜 Match Records", color=discord.Color.teal())

        def innings_list(m):
            r = m["result"]
            return [(m["team1"], r["t1_runs"], r["t1_wickets"], m["team2"], m),
                    (m["team2"], r["t2_runs"], r["t2_wickets"], m["team1"], m)]

        all_inns = [x for m in real for x in innings_list(m) if x[1] > 0 or x[2] > 0]
        if all_inns:
            hi = max(all_inns, key=lambda x: x[1])
            lo = min(all_inns, key=lambda x: x[1])
            rec_e.add_field(name="📈 Highest Total",
                            value=f"**{hi[0]}** {hi[1]}/{hi[2]} vs {hi[3]}  (M#{hi[4]['match_id']})", inline=False)
            rec_e.add_field(name="📉 Lowest Total",
                            value=f"**{lo[0]}** {lo[1]}/{lo[2]} vs {lo[3]}  (M#{lo[4]['match_id']})", inline=False)

        margins_r, chases = [], []
        for m in real:
            r = m["result"]
            w, bf = r.get("winner"), r.get("batted_first")
            if not w or w == "TIE":
                continue
            w_runs = r["t1_runs"] if w == m["team1"] else r["t2_runs"]
            l_runs = r["t2_runs"] if w == m["team1"] else r["t1_runs"]
            if bf:
                if w == bf: margins_r.append((w_runs - l_runs, w, m))
                else: chases.append((w_runs, w, m))
        if margins_r:
            big = max(margins_r, key=lambda x: x[0])
            close = min(margins_r, key=lambda x: x[0])
            rec_e.add_field(name="💥 Biggest Win (runs)",
                            value=f"**{big[1]}** by **{big[0]} runs**  (M#{big[2]['match_id']})", inline=True)
            rec_e.add_field(name="😅 Narrowest Defence",
                            value=f"**{close[1]}** by **{close[0]} run(s)**  (M#{close[2]['match_id']})", inline=True)
        if chases:
            hc = max(chases, key=lambda x: x[0])
            rec_e.add_field(name="🏃 Highest Successful Chase",
                            value=f"**{hc[1]}** chased **{hc[0]}**  (M#{hc[2]['match_id']})", inline=False)
        if rec_e.fields:
            embeds.append(rec_e)

    embeds[-1].set_footer(text=f"{tourney['name']} · {type_label} · save/pin this report before deleting the tournament")
    return embeds


def _tm_next_mid(tourney):
    """Next free match_id = max existing id + 1. MUST be used instead of len()+1 -
    once any match is removed (e.g. by cancel_match), len()+1 collides with an
    existing id and creates a duplicate the lookups can't tell apart."""
    return max((m.get("match_id", 0) for m in tourney.get("schedule", [])), default=0) + 1


def repair_tournament_schedule(tourney):
    """Heal a schedule that has duplicate match_ids (from the old len()+1 bug): keep
    the first entry for each id, hand every later duplicate a fresh unique id.
    Returns (changed: bool, message: str). No matches are deleted."""
    sched = tourney.get("schedule", [])
    seen, remaps = set(), []
    next_id = _tm_next_mid(tourney)
    for m in sched:
        mid = m.get("match_id")
        if mid in seen:
            new_id = next_id; next_id += 1
            remaps.append((mid, new_id, _tm_round_label(m) or m.get("status", "?")))
            m["match_id"] = new_id
        seen.add(m.get("match_id"))
    if not remaps:
        return False, "✅ No duplicate match IDs found — schedule is healthy."
    save_tournament(tourney)
    lines = "\n".join(f"• #{old} → **#{new}**  ({lbl})" for old, new, lbl in remaps)
    return True, (f"🛠️ Fixed **{len(remaps)}** duplicate match ID(s):\n{lines}\n"
                  "The completed match keeps its original number; the stray copy was renumbered.")


def _match_bracket_rank(tourney, m):
    """How far into the tournament a match sits - higher = later. Used to find
    the matches that were built on (i.e. depend on) another match's result."""
    if tourney.get("tournament_type") == "custom":
        # Custom league stages depend on each other in config order, so stage 2
        # ranks above stage 1 even though both use integer rounds.
        return custom_match_rank(m)
    if m.get("stage") in ("group", "league", "ladder") or isinstance(m.get("round"), int):
        return 0
    t_type = tourney.get("tournament_type", "round_robin")
    if t_type == "rating":
        return {"Semi-Final 1": 1, "Semi-Final 2": 1, "Final": 2}.get(m.get("round"), 1)
    if t_type == "acl":
        return {"Qualifier": 1, "Eliminator 1": 1, "Eliminator 2": 1,
                "The Knockout": 2, "Qualifier 2": 3,
                "Grand Final": 4, "Super League Qualifier": 4, "Super Cup": 5}.get(m.get("round"), 1)
    if t_type == "dsl":
        return {"Semi-Final 1": 1, "Semi-Final 2": 1, "Final": 2}.get(m.get("round"), 1)
    if t_type == "ccodi":
        return {"Knockout 1": 1, "Knockout 2": 1, "Qualifier 1": 2, "Eliminator": 2,
                "Qualifier 2": 3, "Final": 4,
                # legacy crossover-semis seasons
                "Semi-Final 1": 1, "Semi-Final 2": 1}.get(m.get("round"), 1)
    if t_type == "ipl":
        return {"Qualifier 1": 1, "Eliminator": 1,
                "Qualifier 2": 2, "Final": 3}.get(m.get("round"), 1)
    if t_type == "tbecs":
        # group(0) < Super 20(1) < QFs(2) < SFs(3) < Final(4)
        if m.get("stage") == "super20":
            return 1
        r = str(m.get("round", ""))
        if r.startswith("Quarter-Final"): return 2
        if r.startswith("Semi-Final"):    return 3
        return 4   # Final
    if m.get("stage") == "super8":
        return 1
    return 4 if str(m.get("round")) == "Final" else 3   # knockout: Semi-Finals(3) < Final(4)


def _revert_match_stats(tourney, m_data):
    """Undo a completed match's contribution to the tournament player stats.
    Returns (reverted: bool, exact: bool)."""
    result = m_data.get("result") or {}
    stats = tourney.get("stats", {})
    delta = result.get("stats_delta")
    if delta:
        for team, players in delta.items():
            tstats = stats.get(team, {})
            for pname, fields in players.items():
                ps = tstats.get(pname)
                if not ps: continue
                for f, amt in fields.items():
                    ps[f] = ps.get(f, 0) - amt
                if all(ps.get(k, 0) <= 0 for k in _TM_STAT_KEYS):
                    tstats.pop(pname, None)
        return True, True

    # Fallback for matches recorded before stats_delta existed: best-effort from the
    # stored scorecard. Can't recover fours/sixes or bench players' match counts, so
    # totals come back approximate.
    sc = result.get("scorecard_players")
    if not sc:
        return False, False
    t1, t2 = m_data["team1"], m_data["team2"]
    first_team, second_team = (t1, t2) if sc.get("bf", 1) == 1 else (t2, t1)

    def _sub(team, pname, field, amt):
        ps = stats.get(team, {}).get(pname)
        if ps is not None:
            ps[field] = max(0, ps.get(field, 0) - amt)

    def _sub_bat(team, arr):
        for a in (arr or []):
            name = a[0]
            _sub(team, name, "matches", 1)
            _sub(team, name, "runs", a[1]); _sub(team, name, "balls_faced", a[2])
            dism = a[3] if len(a) > 3 else "not out"
            if not isinstance(dism, bool) and dism and dism != "not out":
                _sub(team, name, "outs", 1)
            if a[1] >= 100: _sub(team, name, "hundreds", 1)
            elif a[1] >= 50: _sub(team, name, "fifties", 1)

    def _sub_bowl(team, arr):
        for a in (arr or []):
            name = a[0]
            _sub(team, name, "wickets", a[1]); _sub(team, name, "runs_conceded", a[2])
            ov = str(a[3]); balls = 0
            if "." in ov:
                o, b = ov.split("."); balls = int(o) * 6 + int(b)
            elif ov.isdigit():
                balls = int(ov) * 6
            _sub(team, name, "balls_bowled", balls)

    _sub_bat(first_team, sc.get("b1")); _sub_bat(second_team, sc.get("b2"))
    _sub_bowl(first_team, sc.get("w1")); _sub_bowl(second_team, sc.get("w2"))
    return True, False


def rebuild_tournament_stats(tourney):
    """Recompute tourney['stats'] FROM SCRATCH by summing every completed match's stored
    contribution exactly once. Self-heals a leaderboard corrupted by a match whose
    completion event fired twice (double-counted stats): the schedule is the source of
    truth, so each match contributes exactly once no matter how many times it doubled.
    Uses the exact per-match `stats_delta` where present (recent matches always have it),
    else a best-effort recompute from `scorecard_players` (older matches - can't recover
    bench players' match counts). Walkovers / stats-locked matches contribute nothing.
    Returns (matches_counted, exact_count, approx_count, players)."""
    new_stats = {}

    def _add(team, pname, field, amt):
        if not amt:
            return
        ps = new_stats.setdefault(team, {}).setdefault(pname, dict(_TM_STAT_DEFAULT))
        ps[field] = ps.get(field, 0) + amt

    counted = exact = approx = 0
    for m in tourney.get("schedule", []):
        if m.get("status") != "completed":
            continue
        r = m.get("result") or {}
        delta = r.get("stats_delta")
        if delta:
            for team, players in delta.items():
                for pname, fields in players.items():
                    for f, amt in fields.items():
                        _add(team, pname, f, amt)
            counted += 1
            exact += 1
            continue
        sc = r.get("scorecard_players")
        if not sc:
            continue   # walkover / stats-locked / manual result -> no player stats
        t1, t2 = m["team1"], m["team2"]
        first, second = (t1, t2) if sc.get("bf", 1) == 1 else (t2, t1)

        def _addbat(team, arr):
            for a in (arr or []):
                _add(team, a[0], "matches", 1)
                _add(team, a[0], "runs", a[1]); _add(team, a[0], "balls_faced", a[2])
                dism = a[3] if len(a) > 3 else "not out"
                if not isinstance(dism, bool) and dism and dism != "not out":
                    _add(team, a[0], "outs", 1)
                if len(a) > 4:
                    _add(team, a[0], "fours", a[4])
                if len(a) > 5:
                    _add(team, a[0], "sixes", a[5])
                if a[1] >= 100:
                    _add(team, a[0], "hundreds", 1)
                elif a[1] >= 50:
                    _add(team, a[0], "fifties", 1)

        def _addbowl(team, arr):
            for a in (arr or []):
                ov = str(a[3]); balls = 0
                if "." in ov:
                    o, b = ov.split("."); balls = int(o) * 6 + int(b)
                elif ov.isdigit():
                    balls = int(ov) * 6
                _add(team, a[0], "wickets", a[1]); _add(team, a[0], "runs_conceded", a[2])
                _add(team, a[0], "balls_bowled", balls)

        _addbat(first, sc.get("b1")); _addbat(second, sc.get("b2"))
        _addbowl(first, sc.get("w1")); _addbowl(second, sc.get("w2"))
        counted += 1
        approx += 1

    tourney["stats"] = new_stats
    return counted, exact, approx, sum(len(v) for v in new_stats.values())


def revert_tournament_match(tourney, match_id):
    """Cancel a completed match so it can be replayed. Reverts the result, the player
    stats it fed, and any downstream bracket matches generated from it (the points
    table & NRR recompute from results automatically). Returns (ok, message)."""
    sched = tourney.get("schedule", [])
    m = next((x for x in sched if x.get("match_id") == match_id), None)
    if not m:
        return False, f"❌ Match #{match_id} not found."
    if m.get("status") != "completed":
        return False, f"❌ Match #{match_id} isn't completed — nothing to cancel."

    rank = _match_bracket_rank(tourney, m)
    t_type = tourney.get("tournament_type", "round_robin")

    # Guard: don't strand a later, already-played match that was built on this result.
    blockers = [x for x in sched
                if x is not m and x.get("status") == "completed"
                and _match_bracket_rank(tourney, x) > rank]
    if blockers:
        ids = ", ".join(f"#{x['match_id']} ({_tm_round_label(x)})" for x in blockers[:6])
        return False, ("❌ Can't cancel this match — later match(es) built on its result are "
                       f"already completed: {ids}. Cancel those first, then retry.")

    reverted, exact = _revert_match_stats(tourney, m)
    if not reverted:
        stat_note = "\n⚠️ No per-player data was stored for this match, so leaderboard stats were left untouched."
    elif not exact:
        stat_note = "\n⚠️ This match predates exact stat tracking — leaderboard totals were reversed approximately."
    else:
        stat_note = ""

    # Conquest League: reverse this match's Elo + credits (team-level, not covered by
    # _revert_match_stats) while the result is still intact - it's cleared just below.
    if t_type == "rating":
        from league.rating_league import revert_match_rating, revert_match_credits
        revert_match_rating(tourney, m)
        revert_match_credits(tourney, m)

    # Reopen the match itself.
    m["status"] = "pending"
    m["result"] = None
    # TBECS stores each match's scorecard in its own sharded Mongo doc; drop it so the
    # replay writes a fresh one instead of being skipped as "already persisted".
    if t_type == "tbecs":
        from core.subscription_manager import tbecs_forget_match
        tbecs_forget_match(tourney.get("server_id"), match_id)
    tourney["current_match_idx"] = max(0, tourney.get("current_match_idx", 0) - 1)
    if tourney.get("status") == "completed":
        tourney["status"] = "active"

    # Retract downstream matches whose teams were derived from the now-stale result.
    # Only ever removes tail (highest-id) matches, preserving the match_id == index+1 invariant.
    removed = []
    if t_type == "acl":
        if m.get("stage") == "league":
            # A changed league result reshuffles the seeding -> the whole generated bracket is stale.
            ko = [x for x in sched if x.get("stage") in ACL_KO_STAGES]
            for x in ko:
                sched.remove(x)
            removed = ko
            for k in ("league_shield", "playoff_seeds", "acl_trophy_winner",
                      "acl_runner_up", "acl_champion"):
                tourney.pop(k, None)
        else:
            # A playoff/Super Cup result: clear every derived (TBD-origin) slot, then re-derive.
            for x in sched:
                if x is m:
                    continue
                rnd = x.get("round")
                if rnd in ("The Knockout", "Qualifier 2", "Grand Final"):
                    x["team1"] = None; x["team2"] = None; x["status"] = "locked"; x["result"] = None
                elif rnd == "Super Cup":
                    x["team2"] = None; x["status"] = "locked"; x["result"] = None
            slq = _acl_get(tourney, "Super League Qualifier")
            if slq:
                sched.remove(slq); removed.append(slq)
            for k in ("acl_trophy_winner", "acl_runner_up", "acl_champion"):
                tourney.pop(k, None)
            _acl_try_advance(tourney)
    elif t_type == "dsl":
        from league.dsl_manager import DSL_KO_STAGES, _dsl_try_advance
        if m.get("stage") == "league":
            # A changed league result reshuffles the seeding -> the generated bracket is stale.
            ko = [x for x in sched if x.get("stage") in DSL_KO_STAGES]
            for x in ko:
                sched.remove(x)
            removed = ko
            for k in ("playoff_seeds", "dsl_champion", "dsl_runner_up"):
                tourney.pop(k, None)
        else:
            # A playoff result: reset only the LATER derived matches (the blockers guard
            # above ensures those are still pending/locked), then re-derive the slots.
            for x in sched:
                if x is m or x.get("stage") not in DSL_KO_STAGES:
                    continue
                if x.get("round") == "Final" and _match_bracket_rank(tourney, x) > rank:
                    x["team1"] = None; x["team2"] = None; x["status"] = "locked"; x["result"] = None
            for k in ("dsl_champion", "dsl_runner_up"):
                tourney.pop(k, None)
            _dsl_try_advance(tourney)
    elif t_type == "rating":
        from league.rating_league import RATING_KO_STAGES, _rating_try_advance
        if m.get("stage") == "ladder":
            # A changed ladder result restales playoff seeding -> drop any generated bracket.
            ko = [x for x in sched if x.get("stage") in RATING_KO_STAGES]
            for x in ko:
                sched.remove(x)
            removed = ko
            for k in ("playoff_seeds", "rating_champion", "rating_runner_up"):
                tourney.pop(k, None)
        else:
            for x in sched:
                if x is m or x.get("stage") not in RATING_KO_STAGES:
                    continue
                if x.get("round") == "Final" and _match_bracket_rank(tourney, x) > rank:
                    x["team1"] = None; x["team2"] = None; x["status"] = "locked"; x["result"] = None
            for k in ("rating_champion", "rating_runner_up"):
                tourney.pop(k, None)
            _rating_try_advance(tourney)
    elif t_type == "custom":
        # Later league stages / bracket slots regenerate off the fresh result;
        # derived groups, carried points, seeds and crowns are cleared with them.
        removed = custom_revert_cleanup(tourney, m)
    else:
        # Round Robin / T20 World Cup: drop later-stage matches - they regenerate
        # automatically once the earlier stage is completed again.
        later = [x for x in sched
                 if _match_bracket_rank(tourney, x) > rank and x.get("status") in ("pending", "locked")]
        for x in later:
            sched.remove(x)
        removed = later

    assign_tournament_conditions(tourney)
    save_tournament(tourney)

    msg = f"✅ **Match #{match_id} cancelled** — it's back to *pending* and ready to replay."
    if removed:
        rlabels = ", ".join(sorted({(_tm_round_label(x) or f"#{x['match_id']}") for x in removed}))
        msg += f"\n♻️ Reset downstream match(es): {rlabels}."
    msg += stat_note
    return True, msg


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
        app_commands.Choice(name="Double Round Robin", value="double_round_robin"),
        app_commands.Choice(name="T20 World Cup (4 Groups → Super 8 → Final)", value="t20_world_cup"),
        app_commands.Choice(name="CCODI (2 Groups of 5 → Double RR → Qualifiers → Final)", value="ccodi"),
        app_commands.Choice(name="ACL (14 Teams → League → Playoffs → Super Cup)", value="acl"),
        app_commands.Choice(name="IPL (10 Teams → 14 Matches Each → Top 4 Playoffs)", value="ipl"),
        app_commands.Choice(name="Custom — build your own format (setup wizard)", value="custom"),
    ])
    @app_commands.choices(conditions=[
        app_commands.Choice(name="Manual — pick pitch & weather each match", value="manual"),
        app_commands.Choice(name="Auto — random conditions per match (weighted)", value="auto"),
        app_commands.Choice(name="Home Pitch — each match on the home team's pitch", value="home"),
    ])
    @app_commands.choices(match_order=[
        app_commands.Choice(name="Random — anyone can start any match (default)", value="random"),
        app_commands.Choice(name="Strict Schedule — match 2 only after match 1", value="sequential"),
        app_commands.Choice(name="Strict Round — any match in the ongoing round", value="round"),
    ])
    @app_commands.choices(stadiums=[
        app_commands.Choice(name="Random — venue labels assigned randomly (default)", value="random"),
        app_commands.Choice(name="Linked — each team sets a home stadium with a FIXED home pitch", value="linked"),
    ])
    async def create(self, interaction: discord.Interaction, name: str, format: app_commands.Choice[str], event_type: app_commands.Choice[str] = None, min_squad: int = 11, max_squad: int = 15, impact_player: bool = False, injuries: bool = False, custom_overs: int = None, conditions: app_commands.Choice[str] = None, match_order: app_commands.Choice[str] = None, stadiums: app_commands.Choice[str] = None):
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
        type_label = {"double_round_robin": "Double Round Robin", "t20_world_cup": "T20 World Cup", "acl": "Akatsuki Cricket League", "ccodi": "CCODI", "dsl": "Dominators Super League", "rating": "Conquest League", "ipl": "Indian Premier League", "custom": CUSTOM_TYPE_LABEL}.get(t_type, "Round Robin")

        stadium_mode = stadiums.value if stadiums else "random"
        cond_mode = conditions.value if conditions else "manual"
        if stadium_mode == "linked":
            cond_mode = "home"   # the linked stadium CARRIES the fixed home pitch

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
            "conditions_mode": cond_mode,
            "match_order": (match_order.value if match_order else "random"),
            "stadium_mode": stadium_mode,
            "stadiums": default_stadium_pool(t_type),
        }

        # Custom: park the tournament in "configuring" and run the setup wizard -
        # registration only opens once the creator confirms their format.
        if t_type == "custom":
            t_data["status"] = "configuring"
            save_tournament(t_data)
            view = CustomSetupView(server_id, interaction.user.id, name)
            await interaction.response.send_message(embed=view._embed(), view=view)
            view.message = await interaction.original_response()
            return

        save_tournament(t_data)

        extra = ""
        if t_type == "t20_world_cup":
            extra = "\n⚠️ **T20 World Cup requires exactly 16 teams (4 groups of 4). Assign each team a group (A/B/C/D) when using `/tournament add_team`.**"
        elif t_type == "double_round_robin":
            extra = "\n🔁 **Double Round Robin:** every team plays every other team twice, once each way."
        elif t_type == "acl":
            extra = "\n🔴 **ACL requires exactly 14 teams.** Each plays every other once (91 league matches) → Top 6 Playoffs → Super Cup. No groups needed — just `/tournament add_team` for all 14."
            extra += f"\n🏟️ **Stadiums:** {len(DEFAULT_ACL_STADIUMS)} venues pre-loaded — fixtures get a random one at start. Edit the pool with `cvt stadium_add` / `cvt stadiums` before starting."
        elif t_type == "ipl":
            extra = ("\n🏆 **IPL requires exactly 10 teams** — no groups, just `/tournament add_team` for all 10.\n"
                     "📋 **Add order = seeding.** Add your strongest sides first: the draw pairs seeds 1&2, 3&4, etc., and each pair plays **twice** (the CSK-v-MI slot).\n"
                     "🗓️ **70 league matches — 14 per team**, 5 opponents twice & 4 once, played over **14 rounds of 5** (every team plays once a round, 7 home & 7 away).\n"
                     "🏅 One combined table → **Top 4:** Qualifier 1 (1v2) · Eliminator (3v4) → Qualifier 2 → **Final**.")
        if stadium_mode == "linked":
            extra += ("\n🏟️ **Stadiums: Linked** — every team sets a home ground with a FIXED pitch: "
                      "`cvt set_home_stadium \"<team>\" <stadium name> <pitch>`. Home fixtures are played there, "
                      "on that pitch. Can't start until all teams have one (`cvt home_stadiums` to check).")
        elif t_data["conditions_mode"] == "auto":
            extra += "\n🎲 **Conditions: Auto** — pitch & weather auto-assigned per match."
        elif t_data["conditions_mode"] == "home":
            extra += "\n🏟️ **Conditions: Home Pitch** — set each team's home pitch with `/tournament set_home_pitch` (or `cvt set_home_pitch`). The tournament can't start until **all** teams have one."
        if t_data["match_order"] != "random":
            extra += f"\n{MATCH_ORDER_LABELS[t_data['match_order']]}"
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
        elif t_type == "ipl":
            if len(tourney["teams"]) >= 10:
                return await interaction.response.send_message("❌ **IPL is full** — 10 teams already added.", ephemeral=True)
        elif t_type == "custom":
            cfg = tourney.get("custom_config") or {}
            st0 = (cfg.get("stages") or [{}])[0]
            want = st0.get("groups", 0) * st0.get("teams_per_group", 0)
            if want and len(tourney["teams"]) >= want:
                return await interaction.response.send_message(f"❌ **Tournament is full** — this custom format takes exactly {want} teams.", ephemeral=True)
            if st0.get("assignment") == "manual" and st0.get("groups", 1) > 1:
                letters = custom_stage_letters(st0)
                if not group:
                    return await interaction.response.send_message(f"❌ **Group ({'/'.join(letters)}) is required** for this custom format. Use the `group` parameter.", ephemeral=True)
                group_val = group.strip().upper()
                if group_val not in letters:
                    return await interaction.response.send_message(f"❌ Group must be one of **{'/'.join(letters)}**.", ephemeral=True)
                if sum(1 for t in tourney["teams"] if t.get("group") == group_val) >= st0["teams_per_group"]:
                    return await interaction.response.send_message(f"❌ Group **{group_val}** already has {st0['teams_per_group']} teams.", ephemeral=True)

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
        if t_type == "ipl":
            # Add order = seed order, which is what pairs teams up in the fixture draw.
            n = len(tourney["teams"])
            grp_txt = f" · Seed **{n}**" + (f" · {10 - n} to go" if n < 10 else " · **squad complete**")
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
        fuzzy_corrections = []
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
                    if match["name"].lower() != q:
                        fuzzy_corrections.append((line, match["name"]))
            else:
                missing.append(line)
        if missing or len(found_players) < min_s:
            err = f"❌ **Roster Invalid ({len(found_players)}/{min_s} Minimum Found)**\n"
            if missing: err += f"Missing: {', '.join(missing)}\n"
            err += "Please fix the names and try `/tournament submit_squad` again."
            return await msg.reply(err)
        view = SquadConfirmView(interaction.user.id)
        confirm_msg = await msg.reply(build_squad_confirm_text(team["name"], found_players, fuzzy_corrections), view=view)
        await view.wait()
        if view.value is None:
            return await confirm_msg.edit(content="⏳ Confirmation timed out — squad **not** saved. Run `/tournament submit_squad` again.", view=None)
        if view.value is False:
            return await confirm_msg.edit(content="❌ Squad submission cancelled. Run `/tournament submit_squad` again to retry.", view=None)
        team["squad"] = found_players
        save_tournament(tourney)
        await confirm_msg.edit(content=f"✅ **Squad Confirmed and Saved for {team['name']}!**\nRegistered {len(found_players)} players.", view=None)

    @app_commands.command(name="status", description="View the current tournament schedule — navigate rounds with arrow buttons.")
    async def status(self, interaction: discord.Interaction):
        server_id = str(interaction.guild.id)
        tourney = get_server_tournament(server_id)
        if not tourney:
            return await interaction.response.send_message("❌ No tournament exists in this server.", ephemeral=True)

        # TBECS: 953 matches would mean hundreds of fixture pages - status is ONE
        # dashboard embed instead (progress, leaders, latest results, knockouts).
        if tourney.get("tournament_type") == "tbecs":
            from league.tbecs_manager import build_tbecs_status_embed
            return await interaction.response.send_message(embed=build_tbecs_status_embed(tourney))

        # Registration phase - no schedule yet
        if tourney["status"] == "registration":
            t_type = tourney.get("tournament_type", "round_robin")
            type_label = {"double_round_robin": "Double Round Robin", "t20_world_cup": "T20 World Cup", "acl": "Akatsuki Cricket League", "ccodi": "CCODI", "dsl": "Dominators Super League", "rating": "Conquest League", "ipl": "Indian Premier League", "custom": "Custom Tournament"}.get(t_type, "Round Robin")
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

    @app_commands.command(name="generate_knockouts", description="[MANAGER] Generate Semi-Finals for Top 4 teams. (Round Robin / Double RR only)")
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
        if tourney.get("tournament_type") == "dsl":
            return await interaction.response.send_message("❌ This is a DSL season — its Top-4 Playoffs generate automatically when the league ends (or `cvt gp`).", ephemeral=True)
        if tourney.get("tournament_type") == "custom":
            return await interaction.response.send_message("❌ This is a Custom tournament — its stages and playoffs generate automatically from your config.", ephemeral=True)

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
        _base = _tm_next_mid(tourney)
        sf1 = {"match_id": _base, "round": "Semi-Final 1", "stage": "knockout", "team1": top4[0], "team2": top4[3], "status": "pending", "result": None}
        sf2 = {"match_id": _base + 1, "round": "Semi-Final 2", "stage": "knockout", "team1": top4[1], "team2": top4[2], "status": "pending", "result": None}
        tourney["schedule"].extend([sf1, sf2])
        save_tournament(tourney)
        await interaction.response.send_message(f"🔥 **Knockout Stage Set!**\n**Semi-Final 1:** {top4[0]} vs {top4[3]}\n**Semi-Final 2:** {top4[1]} vs {top4[2]}\n\nUse `/tournament play_next` to begin!")

    @app_commands.command(name="generate_finals", description="[MANAGER] Generate the Final for the Top 2 teams — skips Semi-Finals. (Double Round Robin only)")
    async def generate_finals(self, interaction: discord.Interaction):
        server_id = str(interaction.guild.id)
        tourney = get_server_tournament(server_id)
        if not tourney: return await interaction.response.send_message("❌ No tournament exists.", ephemeral=True)
        if not self.is_manager(interaction, tourney): return await interaction.response.send_message("❌ Managers only.", ephemeral=True)
        if tourney["status"] != "active": return await interaction.response.send_message("❌ Tournament is not active.", ephemeral=True)
        if tourney.get("tournament_type") != "double_round_robin":
            return await interaction.response.send_message("❌ This command is for **Double Round Robin** tournaments only. Use `/tournament generate_knockouts` for Round Robin, or `/tournament generate_playoffs` for ACL/DSL.", ephemeral=True)

        gs_matches = [m for m in tourney["schedule"] if isinstance(m.get("round"), int)]
        if any(m["status"] == "pending" for m in gs_matches):
            return await interaction.response.send_message("❌ Cannot generate the Final until all league matches are completed.", ephemeral=True)
        if any(not isinstance(m.get("round"), int) for m in tourney["schedule"]):
            return await interaction.response.send_message("❌ The Final has already been generated.", ephemeral=True)

        standings = get_tournament_standings(tourney)
        real_teams = [t[0] for t in standings if t[0] != "BYE"]
        if len(real_teams) < 2:
            return await interaction.response.send_message("❌ Need at least 2 teams to play a Final.", ephemeral=True)
        top2 = real_teams[:2]
        final = {"match_id": _tm_next_mid(tourney), "round": "Final", "stage": "knockout", "team1": top2[0], "team2": top2[1], "status": "pending", "result": None}
        tourney["schedule"].append(final)
        save_tournament(tourney)
        await interaction.response.send_message(f"🏆 **The Final is Set!**\n**{top2[0]}** (1st) vs **{top2[1]}** (2nd)\n\nUse `/tournament play_next` to begin!")

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

        # Super 8 Group A: A1, B2, C1, D2 | Group B: A2, B1, C2, D1
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
        if tourney.get("tournament_type") not in ("acl", "dsl"):
            return await interaction.response.send_message("❌ This command is for **ACL/DSL** tournaments only.", ephemeral=True)
        if tourney["status"] != "active": return await interaction.response.send_message("❌ Tournament is not active.", ephemeral=True)

        if tourney.get("tournament_type") == "dsl":
            from league.dsl_manager import dsl_generate_playoffs, dsl_bracket_embed, DSL_CONFIG
            ok, msg = dsl_generate_playoffs(tourney)
            if not ok:
                return await interaction.response.send_message(msg, ephemeral=True)
            seeds = tourney.get("playoff_seeds", [])
            return await interaction.response.send_message(
                content=(f"🏆 **{DSL_CONFIG['short_name']} PLAYOFFS ARE SET!**\n"
                         f"Top 4: {' · '.join(f'**{s}**' for s in seeds)}\n"
                         f"Owners: `/tournament fixtures` to find your match."),
                embed=dsl_bracket_embed(tourney),
            )

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
        if tourney.get("tournament_type") == "dsl":
            from league.dsl_manager import _dsl_get, dsl_bracket_embed
            if not _dsl_get(tourney, "Semi-Final 1"):
                return await interaction.response.send_message("ℹ️ The Playoffs haven't been generated yet — they appear automatically once every league match is done.", ephemeral=True)
            return await interaction.response.send_message(embed=dsl_bracket_embed(tourney))
        if tourney.get("tournament_type") != "acl":
            return await interaction.response.send_message("❌ The bracket view is for **ACL/DSL** tournaments. Use `/tournament standings` or `/tournament status`.", ephemeral=True)
        if not _acl_get(tourney, "Qualifier"):
            return await interaction.response.send_message("ℹ️ The Playoffs haven't been generated yet. A Manager runs `/tournament generate_playoffs` once all 91 league games are done.", ephemeral=True)
        await interaction.response.send_message(embed=acl_bracket_embed(tourney))

    # NOTE: force_delete & set_theme are prefix-only (cvt force_delete / cvt set_theme) - 25-subcommand limit.

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

    # NOTE: set_injury_channel & remove_injury are prefix-only (cvt set_injury_channel / cvt remove_injury) - 25-subcommand limit.

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
        from bot import reconstruct_scorecard_data, generate_scorecard_from_data, build_stored_scorecard_embeds
        full_data = reconstruct_scorecard_data(tourney, m)
        if full_data:
            await interaction.response.defer()
            sent = False
            try:
                img_buf = None
                if tourney.get("tournament_type") == "tbecs":
                    try:
                        from bot import generate_tbecs_scorecard_from_data
                        img_buf = generate_tbecs_scorecard_from_data(full_data)
                    except Exception as _te:
                        print(f"TBECS card failed for match {match_id}, using generic: {_te}")
                if img_buf is None:
                    img_buf = generate_scorecard_from_data(full_data)
                file = discord.File(fp=img_buf, filename=f"scorecard_m{match_id}.png")
                await interaction.followup.send(embed=embed, file=file)
                sent = True
            except Exception as _e:
                print(f"Scorecard image render failed for match {match_id}: {_e}")
            try:
                card_embeds = build_stored_scorecard_embeds(full_data)
                if card_embeds:
                    await interaction.followup.send(embeds=card_embeds)
                    sent = True
            except Exception as _e:
                print(f"Text scorecard render failed for match {match_id}: {_e}")
            if sent:
                return
        if interaction.response.is_done():
            embed.add_field(name="No image", value="Scorecard image could not be generated.", inline=False)
            await interaction.followup.send(embed=embed)
        else:
            embed.add_field(name="No image", value="No scorecard data saved for this match.", inline=False)
            await interaction.response.send_message(embed=embed)

    @app_commands.command(name="cancel_match", description="[MANAGER] Cancel a completed match so it can be replayed.")
    @app_commands.describe(match_id="The match number to cancel (see /tournament status).")
    async def cancel_match(self, interaction: discord.Interaction, match_id: int):
        server_id = str(interaction.guild.id)
        tourney = get_server_tournament(server_id)
        if not tourney:
            return await interaction.response.send_message("❌ No tournament exists.", ephemeral=True)
        if not self.is_manager(interaction, tourney):
            return await interaction.response.send_message("❌ Managers only.", ephemeral=True)
        m = next((x for x in tourney.get("schedule", []) if x.get("match_id") == match_id), None)
        if not m:
            return await interaction.response.send_message(f"❌ Match #{match_id} not found.", ephemeral=True)
        if m.get("status") != "completed":
            return await interaction.response.send_message(f"❌ Match #{match_id} isn't completed — nothing to cancel.", ephemeral=True)

        r = m.get("result") or {}
        label = _tm_round_label(m) or f"Match {match_id}"
        summary = (f"**{m['team1']}** {r.get('t1_runs', 0)}/{r.get('t1_wickets', 0)}  vs  "
                   f"**{m['team2']}** {r.get('t2_runs', 0)}/{r.get('t2_wickets', 0)}  —  🏆 {r.get('winner', '?')}")
        view = SquadConfirmView(interaction.user.id)
        await interaction.response.send_message(
            f"⚠️ Cancel **Match #{match_id}** ({label})?\n{summary}\n\n"
            "This wipes the result and the stats it recorded, and reopens the match for a replay. "
            "Any downstream knockout matches built on it will be reset.",
            view=view)
        await view.wait()
        if not view.value:
            return await interaction.edit_original_response(content="❌ Cancellation aborted — match left as-is.", view=None)
        _ok, msg = revert_tournament_match(tourney, match_id)
        await interaction.edit_original_response(content=msg, view=None)

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
        ok, gate_msg = match_order_gate(tourney, pending)
        if not ok:
            return await interaction.response.send_message(gate_msg, ephemeral=True)
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
        ok, gate_msg = match_order_gate(tourney, match)
        if not ok:
            return await interaction.response.send_message(gate_msg, ephemeral=True)
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
        view = build_fixtures_view(tourney, team["name"])
        embed = build_team_fixtures_embed(tourney, team["name"])
        if view:
            await interaction.response.send_message(embed=embed, view=view)
        else:
            await interaction.response.send_message(embed=embed)

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
        # Under an order policy, pick my earliest LAUNCHABLE match (not just earliest).
        match, gate_msg = None, ""
        for m in my_matches:
            ok, gate_msg = match_order_gate(tourney, m)
            if ok:
                match = m
                break
        if match is None:
            return await interaction.response.send_message(gate_msg, ephemeral=True)
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

    def _ccodi_try_advance(self, tourney: dict):
        """CCODI IPL-style knockout ladder, generated progressively:
          Knockout 1: A1 vs B1        Knockout 2: A2 vs B2
          Qualifier 1: W(KO1) vs W(KO2)   Eliminator: L(KO1) vs L(KO2)
          Qualifier 2: L(Q1) vs W(Eliminator)
          Final:       W(Q1) vs W(Q2)
        LEGACY GUARD: seasons that already generated the old crossover semis keep
        finishing on that bracket (the generic SF->Final block handles them)."""
        sched = tourney["schedule"]
        if any(m.get("round") == "Semi-Final 1" for m in sched):
            return   # legacy crossover-semis season - don't touch it

        def _get(round_name):
            return next((m for m in sched if m.get("round") == round_name), None)

        def _add(round_name, t1, t2):
            sched.append({"match_id": _tm_next_mid(tourney), "round": round_name,
                          "stage": "knockout", "team1": t1, "team2": t2,
                          "status": "pending", "result": None})

        def _done(m):
            return m and m["status"] == "completed" and m.get("result")

        def _wl(m):
            w = m["result"]["winner"]
            l = m["result"].get("loser") or (m["team2"] if w == m["team1"] else m["team1"])
            return w, l

        # Stage 1: both groups complete -> Knockout 1 (A1 v B1) + Knockout 2 (A2 v B2)
        grp = [m for m in sched if m.get("stage") == "group"]
        if not grp or any(m["status"] == "pending" for m in grp):
            return
        if not _get("Knockout 1"):
            a_top2 = [n for n, _ in get_group_standings(tourney, "group", "A") if n != "BYE"][:2]
            b_top2 = [n for n, _ in get_group_standings(tourney, "group", "B") if n != "BYE"][:2]
            if len(a_top2) < 2 or len(b_top2) < 2:
                return
            _add("Knockout 1", a_top2[0], b_top2[0])   # A1 v B1
            _add("Knockout 2", a_top2[1], b_top2[1])   # A2 v B2
            return

        # Stage 2: KO1 + KO2 complete -> Qualifier 1 (winners) + Eliminator (losers)
        ko1, ko2 = _get("Knockout 1"), _get("Knockout 2")
        if _done(ko1) and _done(ko2) and not _get("Qualifier 1"):
            w1, l1 = _wl(ko1); w2, l2 = _wl(ko2)
            _add("Qualifier 1", w1, w2)
            _add("Eliminator", l1, l2)
            return

        # Stage 3: Q1 + Eliminator complete -> Qualifier 2 (Q1 loser v Eliminator winner)
        q1, elim = _get("Qualifier 1"), _get("Eliminator")
        if _done(q1) and _done(elim) and not _get("Qualifier 2"):
            _, lq1 = _wl(q1); welim, _ = _wl(elim)
            _add("Qualifier 2", lq1, welim)
            return

        # Stage 4: Q2 complete -> Final (Q1 winner v Q2 winner)
        q2 = _get("Qualifier 2")
        if _done(q1) and _done(q2) and not _get("Final"):
            wq1, _ = _wl(q1); wq2, _ = _wl(q2)
            _add("Final", wq1, wq2)

    @commands.Cog.listener()
    async def on_tournament_match_complete(self, match, channel=None):
        server_id = match.tournament_server_id
        tourney = get_server_tournament(server_id)
        if not tourney: return

        # Look up the schedule entry by its match_id, NOT by (id-1) as a list index
        # index-based lookup silently writes to the wrong match if ids ever stop being
        # a contiguous 1..N run (removed/regenerated knockouts, repaired duplicates).
        _mid = match.tournament_match_id
        m_data = next((x for x in tourney["schedule"] if x.get("match_id") == _mid), None)
        if m_data is None:
            try:
                m_data = tourney["schedule"][_mid - 1]   # legacy fallback
            except (IndexError, TypeError):
                print(f"on_tournament_match_complete: match_id {_mid} not found in schedule.")
                return

        # DUPLICATE-EVENT GUARD: if this schedule entry is already completed with a
        # result, a second completion event would double-count every player's stats
        # (and points/credits). Seen in the wild when an error mid-finalize made the
        # hook fire twice - ignore the replay outright. (cancel_match clears the
        # result BEFORE a redo, so legitimate replays pass through unaffected.)
        if m_data.get("status") == "completed" and m_data.get("result"):
            print(f"Duplicate completion event for match {_mid} ignored (already recorded).")
            return

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

        # Knockouts can't end in a draw - break a tie toward team1 (the higher seed / home slot)
        if winner == "TIE" and not isinstance(m_data.get("round"), int) and m_data.get("stage") in ("knockout", None, "acl_playoff", "acl_supercup", "dsl_playoff"):
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
            # Completion timestamp: with any-order play (TBECS) it's the only way to
            # know which results are the LATEST for the status dashboard.
            "ts": int(datetime.datetime.now().timestamp()),
            # Context snapshot (all formats): who batted first + where/on what it was
            # played - feeds the DSL all-time venue stats and survives schedule edits.
            "batted_first": match.innings1.batting_team["name"],
            "stadium": m_data.get("stadium"),
            "pitch": m_data.get("pitch") or match.pitch,
            "weather": m_data.get("weather") or match.weather,
        }
        tourney["current_match_idx"] += 1

        # STATS AGGREGATION
        if "stats" not in tourney: tourney["stats"] = {}
        if t1_name not in tourney["stats"]: tourney["stats"][t1_name] = {}
        if t2_name not in tourney["stats"]: tourney["stats"][t2_name] = {}

        # Record every increment into stats_delta too, keyed {team: {player: {field: amount}}},
        # and stash it on the result. That lets `cancel_match` reverse this match's contribution
        # exactly when it's redone, instead of guessing from the summarised scorecard.
        stats_delta = {}
        def _add(team_name, p_name, field, amt):
            if not amt: return
            p_stats = tourney["stats"][team_name].setdefault(p_name, dict(_TM_STAT_DEFAULT))
            p_stats[field] = p_stats.get(field, 0) + amt
            fdelta = stats_delta.setdefault(team_name, {}).setdefault(p_name, {})
            fdelta[field] = fdelta.get(field, 0) + amt

        def process_team_stats(team_name, batting_inn, bowling_inn):
            for p in batting_inn.batting_team["players"]:
                p_name = p["name"]
                tourney["stats"][team_name].setdefault(p_name, dict(_TM_STAT_DEFAULT))
                _add(team_name, p_name, "matches", 1)
                if p_name in batting_inn.batting_stats:
                    b_stat = batting_inn.batting_stats[p_name]
                    _add(team_name, p_name, "runs", b_stat.runs_scored)
                    _add(team_name, p_name, "balls_faced", b_stat.balls_faced)
                    if b_stat.dismissal != "not out": _add(team_name, p_name, "outs", 1)
                    _add(team_name, p_name, "fours", getattr(b_stat, "fours", 0))
                    _add(team_name, p_name, "sixes", getattr(b_stat, "sixes", 0))
                    if b_stat.runs_scored >= 100: _add(team_name, p_name, "hundreds", 1)
                    elif b_stat.runs_scored >= 50: _add(team_name, p_name, "fifties", 1)
            for p_name, bw_stat in bowling_inn.bowling_stats.items():
                if bw_stat.balls_bowled > 0:
                    tourney["stats"][team_name].setdefault(p_name, dict(_TM_STAT_DEFAULT))
                    _add(team_name, p_name, "wickets", bw_stat.wickets_taken)
                    _add(team_name, p_name, "runs_conceded", bw_stat.runs_conceded)
                    _add(team_name, p_name, "balls_bowled", bw_stat.balls_bowled)

        # A locked stats table (cvt lock_stats) freezes the player leaderboard: the
        # match result/points/NRR still count, but no player stats are recorded
        # (and stats_delta stays empty, so cancel_match has nothing to reverse).
        # TBECS GOAT XIs: the match counts (points/NRR) and the NORMAL side's players
        # record stats as usual, but GOAT players never enter the leaderboards - skip
        # their whole side. stats_delta only carries the normal side, so cancel_match
        # reverses exactly what was recorded.
        _goat_names = set()
        if tourney.get("tournament_type") == "tbecs":
            from league.tbecs_manager import goat_team_names
            _goat_names = goat_team_names(tourney)
        if tourney.get("stats_locked"):
            m_data["result"]["stats_delta"] = None
        else:
            if t1_name not in _goat_names:
                process_team_stats(t1_name, t1_inn, t2_inn)
            if t2_name not in _goat_names:
                process_team_stats(t2_name, t2_inn, t1_inn)
            m_data["result"]["stats_delta"] = stats_delta

        # CONQUEST (rating) LEAGUE: Elo update, and CREDIT economy for any league
        # Elo uses the result's winner/margin; credits (weak-favouring) use stats_delta
        # for milestones - so both run AFTER the result + stats_delta are set above.
        if tourney.get("tournament_type") == "rating":
            from league.rating_league import apply_match_rating
            apply_match_rating(tourney, m_data)
        try:
            from league.rating_league import award_match_credits
            award_match_credits(tourney, m_data)   # generic; idempotent-guarded
        except Exception as _cr_err:
            print(f"credit award failed for match {m_data.get('match_id')}: {_cr_err}")

        # SCORECARD GALLERY CHANNEL (cvt scorecard_channel)
        # Auto-post this match's scoreboard image to the configured channel - real
        # matches AND sims alike, so the channel becomes a complete match gallery.
        sc_ch_id = tourney.get("scorecard_channel_id")
        if sc_ch_id:
            try:
                sc_ch = self.bot.get_channel(int(sc_ch_id))
                if sc_ch:
                    from bot import reconstruct_scorecard_data, generate_scorecard_from_data, generate_ccodi_scorecard_from_data
                    _full = reconstruct_scorecard_data(tourney, m_data)
                    if _full:
                        _buf = None
                        if tourney.get("tournament_type") == "ccodi":
                            try:
                                _buf = generate_ccodi_scorecard_from_data(_full)
                            except Exception as _ce:
                                print(f"CCODI gallery card failed, using generic: {_ce}")
                        elif tourney.get("tournament_type") == "tbecs":
                            try:
                                from bot import generate_tbecs_scorecard_from_data
                                _buf = generate_tbecs_scorecard_from_data(_full)
                            except Exception as _ce:
                                print(f"TBECS gallery card failed, using generic: {_ce}")
                        if _buf is None:
                            _buf = generate_scorecard_from_data(_full)
                        _rl = _tm_round_label(m_data)
                        await sc_ch.send(f"**Match #{m_data['match_id']}** · {_rl}",
                                         file=discord.File(fp=_buf, filename=f"scorecard_m{m_data['match_id']}.png"))
            except Exception as _sc_err:
                print(f"Scorecard-channel post failed for match {m_data.get('match_id')}: {_sc_err}")

        # INJURY COUNTDOWN (real matches only; count COMPLETED matches, not started ones)
        # Players already injured coming into this match sat it out; now that it has actually
        # FINISHED, burn one match off their spell. Doing this at completion (not at start)
        # means starting a match that's then abandoned/incomplete won't consume the injury.
        # Runs BEFORE the roll so freshly-injured players aren't decremented the same match.
        # channel is None only on the sim path, which keeps its own expiry - leave it alone.
        if channel is not None:
            for _tn in (t1_name, t2_name):
                _tobj = next((t for t in tourney["teams"] if t["name"] == _tn), None)
                if not _tobj: continue
                for _p in _tobj["squad"]:
                    if not _p.get("injured"): continue
                    _left = _p.get("injury_matches_left", _p.get("injury_severity", 1)) - 1
                    if _left <= 0:
                        _p.pop("injured", None); _p.pop("injury_until_match", None)
                        _p.pop("injury_severity", None); _p.pop("injury_matches_left", None)
                    else:
                        _p["injury_matches_left"] = _left

        # INJURY ROLL (group/super8/league only, needs injuries_enabled)
        if tourney.get("injuries_enabled", False) and m_data.get("stage") in ("group", "super8", "league"):
            import random as _rng
            # ACL injuries are more frequent and RATING-SCALED - a star (high bat/bowl)
            # gets hurt less than a journeyman - and allow one injury per TEAM per match
            # (vs one per whole match for other formats), so the squad depth matters more.
            _is_acl = tourney.get("tournament_type") == "acl"
            _match_injured = False
            _new_injuries = []
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
                        # factor: 1.0 at "normal" (75) -> ~0.6 for a 95-rated star, ~1.3 for a 60-rated
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
                    player["injury_until_match"] = until_id       # kept for sim/display
                    player["injury_severity"] = severity
                    player["injury_matches_left"] = severity      # real expiry: count matches actually played
                    _inj_entry = {"team": team_name, "player": p_name,
                                  "severity": severity, "until": until_id}
                    _new_injuries.append(_inj_entry)
                    if channel is None:
                        # Sim path (no channel): queue for the consolidated sim report.
                        tourney.setdefault("pending_injury_news", []).append(_inj_entry)
                    if _is_acl: _team_injured = True
                    else: _match_injured = True

            # Real match: report injuries to the injury/log channel immediately,
            # right after this match - no waiting for the next match to start.
            if channel is not None and _new_injuries:
                team_owners = {t["name"]: t.get("owner_id") for t in tourney.get("teams", [])}
                _lines, _pings = ["🚑 **Injury Report:**"], []
                for item in _new_injuries:
                    m_word = "team match" if item["severity"] == 1 else "team matches"
                    _lines.append(f"• **{item['player']}** ({item['team']}) — ruled out for their next **{item['severity']}** {m_word}")
                    oid = team_owners.get(item["team"])
                    if oid and oid not in _pings: _pings.append(oid)
                if _pings:
                    _lines.append(" ".join(f"<@{uid}>" for uid in _pings))
                inj_ch_id = tourney.get("injury_channel_id")
                announce_ch = (self.bot.get_channel(int(inj_ch_id)) if inj_ch_id else None) or channel
                try:
                    await announce_ch.send("\n".join(_lines))
                except Exception as _e:
                    print(f"Injury report send failed: {_e}")

        # KNOCKOUTS AUTO-PROGRESSION
        t_type = tourney.get("tournament_type", "round_robin")

        if t_type == "custom":
            # Custom engine: next league stage the moment one finishes, then the
            # configured playoff bracket, then the champion - all from the config.
            custom_try_advance(tourney)
            assign_tournament_conditions(tourney)
            save_tournament(tourney)
            return

        if t_type == "acl":
            # ACL: resolve playoff bracket + Super Cup as feeder results come in
            _acl_try_advance(tourney)
            assign_tournament_conditions(tourney)   # fill conditions on any newly-added matches
            save_tournament(tourney)
            return

        if t_type == "rating":
            # Conquest League: open ladder - no auto-schedule; just resolve any playoff
            # bracket the manager generated (ladder games simply save the rating update).
            from league.rating_league import _rating_try_advance
            _rating_try_advance(tourney)
            assign_tournament_conditions(tourney)
            save_tournament(tourney)
            return

        if t_type == "dsl":
            # DSL: auto-generate the Top-4 playoffs the moment the league finishes,
            # then resolve bracket slots as feeder results come in.
            from league.dsl_manager import DSL_CONFIG, dsl_generate_playoffs, _dsl_try_advance
            if DSL_CONFIG["auto_playoffs"]:
                dsl_generate_playoffs(tourney)   # refuses (no-op) unless the league just completed
            _dsl_try_advance(tourney)
            assign_tournament_conditions(tourney)
            save_tournament(tourney)
            return

        if t_type == "tbecs":
            # TBECS: NOTHING auto-advances - not Super 20, not QFs, not even the Final.
            # Every stage is generated by the owner with `cvt tbecs_next`, which is what
            # lets `cvt simall` stop dead at a stage boundary instead of rolling on.
            assign_tournament_conditions(tourney)
            save_tournament(tourney)
            return

        if t_type == "t20_world_cup":
            # Super 8 complete -> auto-generate Semi-Finals
            self._try_generate_semis(tourney)

        if t_type == "ipl":
            # League done -> Q1 (1v2) + Eliminator (3v4) -> Q2 -> Final
            ipl_try_advance(tourney)
            assign_tournament_conditions(tourney)
            save_tournament(tourney)
            return

        if t_type == "ccodi":
            # Groups done -> KO1/KO2 -> Q1/Eliminator -> Q2 -> Final (legacy semis
            # seasons are left to the generic SF->Final block below).
            self._ccodi_try_advance(tourney)

        # SF complete -> auto-generate Final (works for all group formats)
        sf1 = next((m for m in tourney["schedule"] if m.get("round") == "Semi-Final 1"), None)
        sf2 = next((m for m in tourney["schedule"] if m.get("round") == "Semi-Final 2"), None)
        if sf1 and sf2 and sf1["status"] == "completed" and sf2["status"] == "completed":
            if not any(m.get("round") == "Final" for m in tourney["schedule"]):
                tourney["schedule"].append({
                    "match_id": _tm_next_mid(tourney), "round": "Final", "stage": "knockout",
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

        # TBECS: whichever stage is live decides the image(s).
        #   group stage  -> one branded 28-team table per group (A then B)
        #   Super 20     -> the 20-row table (top 8 tinted)
        #   knockouts    -> the bracket, plus the final Super 20 table for reference
        if t_type == "tbecs":
            try:
                sched = tourney.get("schedule", [])
                has_s20 = any(m.get("stage") == TBECS_SUPER_STAGE for m in sched)
                has_ko = any(m.get("stage") == "knockout" for m in sched)
                files = []
                if has_ko:
                    files.append(discord.File(fp=generate_tbecs_bracket(tourney), filename="tbecs_bracket.png"))
                    files.append(discord.File(fp=generate_tbecs_super20_table(tourney), filename="tbecs_super20.png"))
                elif has_s20:
                    files.append(discord.File(fp=generate_tbecs_super20_table(tourney), filename="tbecs_super20.png"))
                else:
                    for grp in ("A", "B"):
                        files.append(discord.File(fp=generate_tbecs_group_table(tourney, grp),
                                                  filename=f"tbecs_group_{grp}.png"))
                return await interaction.followup.send(files=files)
            except Exception as _e:
                print(f"TBECS standings render failed, using text fallback: {_e}")

        # T20 World Cup standings
        if t_type == "t20_world_cup":
            schedule      = tourney.get("schedule", [])
            super8_matches = [m for m in schedule if m.get("stage") == "super8"]
            ko_matches     = [m for m in schedule if m.get("stage") == "knockout"]

            if not super8_matches and not ko_matches:
                # Group stage only - single image, no navigation needed
                try:
                    buf = generate_t20wc_points_table(tourney)
                    return await interaction.followup.send(file=discord.File(fp=buf, filename="points_table.png"))
                except Exception as e:
                    print(f"Points table image failed: {e}")

            # Build available pages
            pages = []
            try:
                s16_buf = generate_t20wc_points_table(tourney)
                pages.append(("Group Stage", "points_table.png", s16_buf))
            except Exception as e:
                print(f"Super16 table failed: {e}")
            if super8_matches:
                try:
                    s8_buf = generate_t20wc_super8_table(tourney)
                    pages.append(("Super 8", "super8_table.png", s8_buf))
                except Exception as e:
                    print(f"Super8 table failed: {e}")
            if ko_matches:
                try:
                    ko_buf = generate_t20wc_knockouts_image(tourney)
                    if ko_buf:
                        pages.append(("Knockouts", "knockouts.png", ko_buf))
                except Exception as e:
                    print(f"Knockouts image failed: {e}")

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

            # Both images failed - text embed fallback
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
                print(f"ACL points table failed, using default: {e}")
            # fall through to the generic renderer below on failure

        # Custom: per-stage/group text tables with the configured qualifying cutoffs.
        if t_type == "custom":
            embed = build_custom_standings_message(tourney)
            if not embed:
                return await interaction.followup.send("No matches have been completed yet.")
            return await interaction.followup.send(embed=embed)

        # Everything else (Round Robin / Double RR / IPL): the shared points-table
        # image, delivered inside an embed titled with the tournament name.
        embed = build_standings_message(tourney)
        if not embed:
            return await interaction.followup.send("No matches have been completed yet.")
        await interaction.followup.send(embed=embed)

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

        _lb_odi = tourney.get("format_overs", 20) >= 35

        def _mvp_score(s):
            return _summary_mvp(s, _lb_odi)   # single formula, format-aware

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

        # runs / wickets / MVP get the first 50, paginated 10-per-page with buttons;
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
            view = TournamentLeaderboardView(title, header, lines)
            await interaction.response.send_message(embed=view.make_embed(), view=view)
        else:
            embed = discord.Embed(title=title, color=discord.Color.gold())
            body = "\n".join(lines) if lines else "No players qualify for this leaderboard yet."
            embed.description = (header + "\n" if header else "") + body
            await interaction.response.send_message(embed=embed)

    @app_commands.command(name="player_stats", description="View a player's tournament stats (team optional).")
    @app_commands.describe(player_name="Player to look up", team_name="Optional — only needed if the same name is on multiple teams")
    async def player_stats(self, interaction: discord.Interaction, player_name: str, team_name: str = None):
        server_id = str(interaction.guild.id)
        tourney = get_server_tournament(server_id)
        if not tourney: return await interaction.response.send_message("❌ No tournament exists.", ephemeral=True)
        stats_map = tourney.get("stats", {})
        if not stats_map: return await interaction.response.send_message("❌ No stats available yet. Complete a match first!", ephemeral=True)

        # Team given -> resolve within that team (old behaviour).
        if team_name:
            t_match = next((t for t in stats_map if t.lower() == team_name.lower()), None)
            if not t_match:
                return await interaction.response.send_message(f"❌ Team '{team_name}' not found or hasn't played a match yet.", ephemeral=True)
            p_match = next((p for p in stats_map[t_match] if p.lower() == player_name.lower()), None)
            if not p_match:
                close = difflib.get_close_matches(player_name, list(stats_map[t_match].keys()), n=1, cutoff=0.5)
                if close: p_match = close[0]
                else: return await interaction.response.send_message(f"❌ Player '{player_name}' not found in team '{t_match}'.", ephemeral=True)
            return await interaction.response.send_message(embed=build_player_stats_embed(stats_map[t_match][p_match], p_match, t_match))

        # No team -> search every team; ask which one if the name is shared.
        matches = find_player_in_tournament(tourney, player_name)
        if not matches:
            return await interaction.response.send_message(f"❌ Player '{player_name}' not found in any team.", ephemeral=True)
        if len(matches) == 1:
            t, p = matches[0]
            return await interaction.response.send_message(embed=build_player_stats_embed(stats_map[t][p], p, t))
        view = PlayerStatsTeamSelectView(stats_map, matches)
        await interaction.response.send_message(f"🔎 **{matches[0][1]}** is on multiple teams — pick which one:", view=view)

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
            description="Event types: **Round Robin**, **Double Round Robin**, **T20 World Cup**, **CCODI**, and **ACL** (14 teams → League → Playoffs → Super Cup).\nEvery command works as both `/tournament …` and the shorter `cvt …`.",
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
                   "`next_match`/`nm` — launch your earliest pending · `play_next`/`pn` — [MGR] next in order\n"
                   "`cancel_match`/`redo <id>` — [MGR] wipe a completed match & reopen it for a replay"),
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
            value=("**ACL:** `generate_playoffs`/`gp`  ·  **Round Robin/Double RR:** `generate_knockouts`  ·  **Double RR (Top-2 Final):** `generate_finals`/`gf`  ·  **T20 WC:** `generate_super8`"),
            inline=False,
        )
        embed.set_footer(text="More admin tools are prefix-only: cvt transfer_team · replace_player · force_delete · set_theme · remove_injury · repair_schedule · simulate_all")
        await interaction.response.send_message(embed=embed, ephemeral=True)
