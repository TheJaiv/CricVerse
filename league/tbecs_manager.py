# TBECS (The Big Event Championship Series)
# A 56-team mega event: 54 addable teams + 2 GOAT teams (fun-only, can never qualify).
#
# Format:
#   Stage 1  - two groups of 28 (one GOAT team each), single round robin (378/group).
#              Top 10 per group qualify; GOAT teams are barred no matter their position.
#   Stage 2  - "Super 20": the 20 qualifiers in one fresh round robin (190 matches).
#   Knockouts- Top 8 seeded into QFs (1v8, 2v7, 3v6, 4v5) -> SFs (W1vW4, W2vW3) -> Final.
#
# NOTHING auto-advances. Every stage is generated manually by the bot owner with
# `cvt tbecs_next` - so `cvt simall` stops dead at the end of a stage and waits.
#
# GOAT teams: 11 players (1 WK, 2 batters, 7 all-rounders, 1 pacer, 1 spinner), every
# one rated 99/99 with the fearless "Vaibhav" archetype. Their matches count in the
# points table and opponents' player stats record normally, but GOAT players
# themselves never enter the tournament leaderboards (see on_tournament_match_complete).
#
# Storage: a schedule this big would blow Mongo's 16MB per-document cap, so the
# skeleton lives in its own document ("tbecs_tournament_data") and every match's
# heavy scorecard is sharded into a per-match document. All of that lives in
# core/subscription_manager (the Mongo leaf).
#
# Import direction: this module may import from core.subscription_manager and
# league.tournament_manager (lazily, function-level, to dodge the import cycle).
# Other managers must import tbecs_manager LAZILY too.

import random

import discord

from core.subscription_manager import DB_CACHE, async_save_to_bin

# ---- Config ----
TBECS_CONFIG = {
    "type_key": "tbecs",                 # tournament_type value (drives Mongo sharding)
    "display_name": "The Big Event Championship Series",
    "short_name": "TBECS",
    "addable_teams": 54,                 # normal teams added by managers
    "group_size": 28,                    # 27 addable + 1 GOAT per group
    "stage1_qualifiers": 10,             # per group (GOATs excluded regardless)
    "super_stage": "super20",            # stage key for the 20-team round robin
    "super_group": "S",                  # group key so get_group_standings can filter it
    "knockout_teams": 8,                 # top 8 of the Super 20 -> QFs
    "min_squad": 11,
    "max_squad": 20,                     # 20 players per team (per-match stats stored for all)
}

# The two GOAT XIs - pure fun sides. 1 WK, 2 batters, 7 all-rounders, 1 pacer,
# 1 spinner; ALL 99/99 with the Vaibhav (fearless) archetype. Fictional names on
# purpose - no real player implied. Squads are exactly 11, no bench.
def _goat(name, role):
    return {"name": name, "bat": 99, "bowl": 99, "role": role, "archetype": "Vaibhav"}

GOAT_TEAMS = [
    {"name": "GOAT XI Apex", "players": [
        _goat("Six Machine",       "Batter"),
        _goat("Boundary Baron",    "Batter"),
        _goat("The Undismissable", "Batter_WK"),
        _goat("Chaos Theory",      "All-Rounder_Pace"),
        _goat("Perfect Storm",     "All-Rounder_Pace"),
        _goat("The Cheat Code",    "All-Rounder_Pace"),
        _goat("Highlight Reel",    "All-Rounder_Pace"),
        _goat("The Final Boss",    "All-Rounder_Spin_Off"),
        _goat("Match Winner",      "All-Rounder_Spin_Leg"),
        _goat("Momentum Shift",    "All-Rounder_Spin_Orthodox"),
        _goat("Speed Demon",       "Bowler_Pace"),
    ]},
    {"name": "GOAT XI Zenith", "players": [
        _goat("Run Tsunami",       "Batter"),
        _goat("Strike Rate God",   "Batter"),
        _goat("Iron Gauntlets",    "Batter_WK"),
        _goat("The Juggernaut",    "All-Rounder_Pace"),
        _goat("Plot Armor",        "All-Rounder_Pace"),
        _goat("God Mode",          "All-Rounder_Pace"),
        _goat("The Protagonist",   "All-Rounder_Pace"),
        _goat("Difficulty Spike",  "All-Rounder_Spin_Off"),
        _goat("The Glitch",        "All-Rounder_Spin_Leg"),
        _goat("Server Admin",      "All-Rounder_Spin_Orthodox"),
        _goat("Web Weaver",        "Bowler_Spin_Leg"),
    ]},
]
# Composition check: user spec is 1 WK / 2 bat / 7 AR / 1 pure bowler+1 more bowler.
# Apex carries the pacer, Zenith the spinner as their 11th; both roles appear across
# the event. (7 ARs all bowl anyway, so each XI always has a full attack.)


def is_tbecs_tournament(tourney):
    return bool(tourney) and tourney.get("tournament_type") == TBECS_CONFIG["type_key"]


def is_tbecs_match(match):
    """True for a live match object belonging to a TBECS tournament."""
    return getattr(match, "tournament_type", None) == TBECS_CONFIG["type_key"]


def goat_team_names(tourney):
    """Names of the GOAT teams in this tournament (flagged at creation)."""
    return {t["name"] for t in tourney.get("teams", []) if t.get("goat")}


def build_goat_teams(owner_id):
    """Fresh GOAT team dicts ready to append to tourney['teams'] at creation.
    Owned by the bot owner so someone can drive them in interactive matches."""
    return [{"name": g["name"], "owner_id": str(owner_id), "goat": True,
             "squad": [dict(p) for p in g["players"]], "group": grp}
            for g, grp in zip(GOAT_TEAMS, ("A", "B"))]


# ---- Home grounds & default identity ----
# TBECS runs stadiums=linked + conditions=home: every fixture is played at the home
# (team1) side's ground on that ground's fixed pitch. Matches have NO ordering rule
# (match_order=random) - any pending fixture can be played whenever.

# 8x8 name parts = 64 unique generated grounds, enough for all 56 teams.
_STADIUM_FIRST = ["Thunder", "Crimson", "Emerald", "Golden", "Iron", "Royal", "Storm", "Obsidian"]
_STADIUM_LAST  = ["Bay Oval", "Peak Arena", "Gardens", "Fort Ground", "Heights Stadium",
                  "Valley Oval", "Point Arena", "Crown Park"]


def tbecs_assign_random_homes(tourney, rng=None):
    """Give every team missing a home ground a UNIQUE generated stadium with a random
    fixed pitch. Teams that already set one via set_home_stadium keep theirs.
    Returns (count_assigned, summary_lines)."""
    from league.tournament_manager import ALL_PITCHES
    rng = rng or random
    taken = {t.get("home_stadium") for t in tourney.get("teams", []) if t.get("home_stadium")}
    names = [f"{a} {b}" for a in _STADIUM_FIRST for b in _STADIUM_LAST]
    rng.shuffle(names)
    free = [n for n in names if n not in taken]
    assigned, lines = 0, []
    for t in tourney.get("teams", []):
        if t.get("home_stadium") and t.get("home_pitch"):
            continue   # fully submitted - never touched
        # Partial submissions keep whatever the owner chose; only the gap is filled.
        if not t.get("home_stadium"):
            t["home_stadium"] = free.pop(0) if free else rng.choice(names)
        if not t.get("home_pitch"):
            t["home_pitch"] = rng.choice(ALL_PITCHES)
        assigned += 1
        lines.append(f"**{t['name']}** → {t['home_stadium']} ({t['home_pitch']})")
    return assigned, lines


def _initials(team_name):
    words = [w for w in team_name.split() if w and w[0].isalnum()]
    return "".join(w[0] for w in words[:3]).upper() or team_name[:2].upper()


def _default_logo_data_uri(team_name, hex_color):
    """A simple generated logo: coloured roundel with the team's initials, returned as
    a base64 PNG data URI - the same storage format set_team_logo uses for uploads,
    so every render path (scorecards, tables, brackets) picks it up unchanged."""
    import base64
    import io as _io
    from PIL import Image, ImageDraw, ImageFont
    size = 96
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse([2, 2, size - 3, size - 3], fill=hex_color, outline="#FFFFFF", width=3)
    text = _initials(team_name)
    font = None
    for fp in ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
               "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
               "C:/Windows/Fonts/arialbd.ttf"):
        try:
            font = ImageFont.truetype(fp, 40 if len(text) < 3 else 30)
            break
        except Exception:
            continue
    if font is None:
        font = ImageFont.load_default()
    bbox = d.textbbox((0, 0), text, font=font)
    d.text(((size - bbox[2] + bbox[0]) / 2 - bbox[0], (size - bbox[3] + bbox[1]) / 2 - bbox[1]),
           text, font=font, fill="#FFFFFF")
    buf = _io.BytesIO()
    img.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def tbecs_fill_default_identity(tourney):
    """Every team without a submitted colour gets a distinct one (golden-angle hue
    spacing so 50+ teams stay tellable-apart; GOATs get gold/purple), and every team
    without a logo gets a generated initials roundel. Submitted ones are untouched.
    Returns (colors_filled, logos_filled)."""
    import colorsys
    teams = tourney.get("teams", [])
    n_col, n_logo, i = 0, 0, 0
    for t in teams:
        if not t.get("color"):
            if t.get("goat"):
                t["color"] = "#FFD700" if t["name"].endswith("Apex") else "#8A2BE2"
            else:
                r, g, b = colorsys.hsv_to_rgb(((i * 137.508) % 360) / 360,
                                              0.65 if i % 2 else 0.85,
                                              0.85 if i % 2 else 0.70)
                t["color"] = f"#{int(r*255):02X}{int(g*255):02X}{int(b*255):02X}"
                i += 1
            n_col += 1
        if not t.get("logo_standings") and not t.get("logo_match"):
            try:
                uri = _default_logo_data_uri(t["name"], t["color"])
                t["logo_standings"] = t["logo_match"] = uri
                n_logo += 1
            except Exception as e:
                print(f"TBECS default logo failed for {t['name']}: {e}")
    return n_col, n_logo


# ---- Stage generation (ALL manual - called only from `cvt tbecs_next`) ----

def _next_mid(tourney):
    return max((m.get("match_id", 0) for m in tourney.get("schedule", [])), default=0) + 1


def _circle_rr(team_names, *, stage, group, rng):
    """Single round robin (circle method) with stage/group tags, interleave-shuffled."""
    teams = list(team_names)
    rng.shuffle(teams)
    if len(teams) % 2:
        teams.append("BYE")
    n = len(teams)
    out = []
    for r in range(n - 1):
        for i in range(n // 2):
            a, b = teams[i], teams[n - 1 - i]
            if a == "BYE" or b == "BYE":
                continue
            out.append({"round": f"Group {group}" if stage == "group" else "Super 20",
                        "stage": stage, "group": group, "group_round": r + 1,
                        "team1": a if r % 2 == 0 else b,
                        "team2": b if r % 2 == 0 else a,
                        "status": "pending", "result": None})
        teams.insert(1, teams.pop())
    rng.shuffle(out)
    return out


def tbecs_split_groups(tourney, rng=None):
    """Assign every team a group (A/B, 28 each, one GOAT per side). Pre-set A/B
    choices from add_team are honoured; the rest fill randomly. Returns an error
    string or None."""
    rng = rng or random
    half = TBECS_CONFIG["group_size"]
    normals = [t for t in tourney["teams"] if not t.get("goat")]
    goats   = [t for t in tourney["teams"] if t.get("goat")]
    for g, grp in zip(goats, ("A", "B")):
        g["group"] = grp
    pre_a = [t for t in normals if t.get("group") == "A"]
    pre_b = [t for t in normals if t.get("group") == "B"]
    if len(pre_a) > half - 1 or len(pre_b) > half - 1:
        return f"❌ A group has too many pre-assigned teams (max {half - 1} per group plus its GOAT XI)."
    rest = [t for t in normals if t.get("group") not in ("A", "B")]
    rng.shuffle(rest)
    need_a = (half - 1) - len(pre_a)
    for t in rest[:need_a]:
        t["group"] = "A"
    for t in rest[need_a:]:
        t["group"] = "B"
    return None


def tbecs_generate_group_stage(tourney, rng=None):
    """Stage-1 fixtures: two 28-team single round robins. Assumes groups are set."""
    rng = rng or random
    matches = []
    for grp in ("A", "B"):
        names = [t["name"] for t in tourney["teams"] if t.get("group") == grp]
        matches += _circle_rr(names, stage="group", group=grp, rng=rng)
    rng.shuffle(matches)
    tourney["schedule"] = [dict(m, match_id=i + 1) for i, m in enumerate(matches)]


def _stage_matches(tourney, stage):
    return [m for m in tourney.get("schedule", []) if m.get("stage") == stage]


def _stage_done(matches):
    return bool(matches) and all(m["status"] == "completed" for m in matches)


def _standings(tourney, stage, group):
    from league.tournament_manager import get_group_standings
    return get_group_standings(tourney, stage, group)


def tbecs_stage_state(tourney):
    """Where the event currently stands: ('group'|'super20'|'quarters'|'semis'|'final'|'done',
    pending_count_in_that_stage)."""
    sched = tourney.get("schedule", [])
    groups = _stage_matches(tourney, "group")
    supers = _stage_matches(tourney, TBECS_CONFIG["super_stage"])
    qfs    = [m for m in sched if str(m.get("round", "")).startswith("Quarter-Final")]
    sfs    = [m for m in sched if str(m.get("round", "")).startswith("Semi-Final")]
    final  = [m for m in sched if m.get("round") == "Final"]
    for name, ms in (("group", groups), ("super20", supers), ("quarters", qfs),
                     ("semis", sfs), ("final", final)):
        if ms and not _stage_done(ms):
            return name, sum(1 for m in ms if m["status"] != "completed")
    if final and _stage_done(final):
        return "done", 0
    if _stage_done(sfs):
        return "final", 0
    if _stage_done(qfs):
        return "semis", 0
    if _stage_done(supers):
        return "quarters", 0
    return "super20", 0   # groups done, super20 not yet generated


def tbecs_generate_next(tourney, rng=None):
    """Generate whatever comes next, refusing while the current stage is unfinished.
    Owner-triggered only - this is what keeps simall from rolling into the next round.
    Returns (ok, message)."""
    rng = rng or random
    q = TBECS_CONFIG["stage1_qualifiers"]
    if not _stage_matches(tourney, "group"):
        return False, "❌ The group stage hasn't been generated yet — `cvt start` first."
    stage, left = tbecs_stage_state(tourney)

    if left:
        label = {"group": "group-stage", "super20": "Super 20", "quarters": "Quarter-Final",
                 "semis": "Semi-Final", "final": "Final"}[stage]
        return False, f"❌ **{left}** {label} match(es) still pending — finish them first."

    if stage == "super20" and not _stage_matches(tourney, TBECS_CONFIG["super_stage"]):
        goats = goat_team_names(tourney)
        qualifiers, cut_lines = [], []
        for grp in ("A", "B"):
            rows = [(n, st) for n, st in _standings(tourney, "group", grp) if n not in goats]
            qualifiers += [n for n, _ in rows[:q]]
            cut_lines.append(f"**Group {grp}:** " + " · ".join(n for n, _ in rows[:q]))
        ms = _circle_rr(qualifiers, stage=TBECS_CONFIG["super_stage"],
                        group=TBECS_CONFIG["super_group"], rng=rng)
        mid = _next_mid(tourney)
        tourney["schedule"] += [dict(m, match_id=mid + i) for i, m in enumerate(ms)]
        return True, (f"🏁 **SUPER 20 GENERATED** — top {q} from each group, fresh table, "
                      f"{len(ms)} matches.\n" + "\n".join(cut_lines) +
                      "\n(GOAT XIs are barred from qualifying — event rule.)")

    if stage == "quarters":
        rows = _standings(tourney, TBECS_CONFIG["super_stage"], TBECS_CONFIG["super_group"])
        top8 = [n for n, _ in rows[:TBECS_CONFIG["knockout_teams"]]]
        tourney["tbecs_seeds"] = {n: i + 1 for i, n in enumerate(top8)}
        mid = _next_mid(tourney)
        pairs = [(0, 7), (1, 6), (2, 5), (3, 4)]   # 1v8 2v7 3v6 4v5
        for i, (a, b) in enumerate(pairs):
            tourney["schedule"].append({
                "match_id": mid + i, "round": f"Quarter-Final {i + 1}", "stage": "knockout",
                "team1": top8[a], "team2": top8[b], "status": "pending", "result": None})
        lines = [f"QF{i+1}: **{top8[a]}** (#{a+1}) vs **{top8[b]}** (#{b+1})" for i, (a, b) in enumerate(pairs)]
        return True, "⚔️ **QUARTER-FINALS GENERATED** — seeded 1v8 · 2v7 · 3v6 · 4v5:\n" + "\n".join(lines)

    if stage == "semis":
        sched = tourney["schedule"]
        w = {int(str(m["round"]).split()[-1]): m["result"]["winner"]
             for m in sched if str(m.get("round", "")).startswith("Quarter-Final")}
        mid = _next_mid(tourney)
        # Bracket halves: W(QF1 1v8) meets W(QF4 4v5); W(QF2 2v7) meets W(QF3 3v6).
        for i, (x, y) in enumerate(((1, 4), (2, 3))):
            sched.append({"match_id": mid + i, "round": f"Semi-Final {i + 1}", "stage": "knockout",
                          "team1": w[x], "team2": w[y], "status": "pending", "result": None})
        return True, (f"🔥 **SEMI-FINALS GENERATED**\nSF1: **{w[1]}** vs **{w[4]}**\n"
                      f"SF2: **{w[2]}** vs **{w[3]}**")

    if stage == "final":
        sched = tourney["schedule"]
        w = [m["result"]["winner"] for m in sched if str(m.get("round", "")).startswith("Semi-Final")]
        sched.append({"match_id": _next_mid(tourney), "round": "Final", "stage": "knockout",
                      "team1": w[0], "team2": w[1], "status": "pending", "result": None})
        return True, f"🏆 **THE FINAL IS SET:** **{w[0]}** vs **{w[1]}** — one match for the title!"

    if stage == "done":
        final = next(m for m in tourney["schedule"] if m.get("round") == "Final")
        tourney["status"] = "completed"
        return True, f"🎉 **{final['result']['winner']}** are the {TBECS_CONFIG['short_name']} champions! Tournament complete."

    return False, "❌ Nothing to generate."

# Discord embed limits we respect when rendering the (unbounded) ad list.
_EMBED_DESC_LIMIT = 4096
_MAX_EMBEDS = 10


def is_tbecs_tournament(tourney):
    return bool(tourney) and tourney.get("tournament_type") == TBECS_CONFIG["type_key"]


def is_tbecs_match(match):
    """True for a live match object belonging to a TBECS tournament."""
    return getattr(match, "tournament_type", None) == TBECS_CONFIG["type_key"]


# ADS - innings-break sponsor messages, managed per server, shown at every innings
# end of a TBECS match. Stored in DB_CACHE["tbecs_ads"] (main doc), keyed by server_id.
def _ads_store():
    if "tbecs_ads" not in DB_CACHE or not isinstance(DB_CACHE["tbecs_ads"], dict):
        DB_CACHE["tbecs_ads"] = {}
    return DB_CACHE["tbecs_ads"]


def get_tbecs_ads(server_id):
    """Return the list of ad strings for a server (a copy; never the live list)."""
    return list(_ads_store().get(str(server_id), []))


def add_tbecs_ad(server_id, text):
    """Append an ad. Returns (ok, message, new_total). Persists to Mongo."""
    text = (text or "").strip()
    if not text:
        return False, "❌ Ad text is empty — give me something to show.", 0
    # Cap comfortably under the 4096 embed-description limit so one ad (plus its
    # "Ad #N" header) always fits a single embed even when multi-line with links.
    if len(text) > 3800:
        return False, f"❌ That ad is too long ({len(text)} chars, max 3800).", 0
    store = _ads_store()
    ads = store.setdefault(str(server_id), [])
    ads.append(text)
    async_save_to_bin()
    return True, f"✅ Ad #{len(ads)} added. It'll show at every innings end in TBECS matches.", len(ads)


def remove_tbecs_ad(server_id, index):
    """Remove the 1-based ad at `index`. Returns (ok, message). Persists to Mongo."""
    ads = _ads_store().get(str(server_id), [])
    if not ads:
        return False, "❌ No ads to remove."
    if index < 1 or index > len(ads):
        return False, f"❌ Ad #{index} doesn't exist — there are {len(ads)} ad(s) (1–{len(ads)})."
    removed = ads.pop(index - 1)
    async_save_to_bin()
    flat = " ⏎ ".join(x.strip() for x in removed.splitlines() if x.strip())
    preview = flat if len(flat) <= 80 else flat[:77] + "…"
    return True, f"🗑️ Removed ad #{index}: “{preview}”. {len(ads)} ad(s) left."


def clear_tbecs_ads(server_id):
    """Remove all ads for a server. Returns (count_cleared, message). Persists to Mongo."""
    ads = _ads_store().get(str(server_id), [])
    n = len(ads)
    if not n:
        return 0, "ℹ️ There are no ads to clear."
    _ads_store()[str(server_id)] = []
    async_save_to_bin()
    return n, f"🧹 Cleared all {n} ad(s) for this server."


def build_tbecs_ad_embeds(server_id):
    """Render every ad for a server as a list of discord.Embeds (usually one).
    Returns [] when the server has no ads. Splits across embeds if the combined
    text exceeds Discord's per-embed description limit, so ANY number of ads fits."""
    ads = get_tbecs_ads(server_id)
    if not ads:
        return []

    # Each ad as its own block, numbered, separated by a divider. Pack blocks into
    # embeds without exceeding the description limit.
    blocks = [f"**📢 Ad #{i}**\n{ad}" for i, ad in enumerate(ads, 1)]
    embeds, buf = [], ""
    for block in blocks:
        candidate = block if not buf else f"{buf}\n\n{block}"
        if len(candidate) > _EMBED_DESC_LIMIT:
            if buf:
                embeds.append(buf)
            # A single block longer than the limit is hard-truncated.
            buf = block[:_EMBED_DESC_LIMIT]
        else:
            buf = candidate
    if buf:
        embeds.append(buf)

    out = []
    for idx, desc in enumerate(embeds[:_MAX_EMBEDS]):
        e = discord.Embed(
            description=desc,
            color=0xF5A623,
        )
        if idx == 0:
            e.title = "📣 A word from our sponsors"
        out.append(e)
    return out
