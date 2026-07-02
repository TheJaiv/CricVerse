# ── Dominators Super League (DSL) ─────────────────────────────────────────────
# A recurring, multi-season franchise league. Everything about the league is
# predecided in DSL_CONFIG below (format, team count, fixtures, venues) — a server
# only needs the bot-owner's access grant, then `cvt start dsl` does the rest.
#
# Season model:
#   • The CURRENT season lives in Mongo like any other tournament (tournament_type
#     "dsl", league_key "dsl", season N).
#   • `cvt end_season` exports the season to dsl_archive/<server_id>_s<N>.json and
#     frees the server's tournament slot. The owner commits that file to GitHub so
#     it ships with every future deploy — Mongo stays light.
#   • All-time ("overall") player + venue stats merge the archive files with the
#     current season at query time (cached on file mtime/size).
#
# Venues:
#   • Each venue has a WEIGHTED PROFILE over the existing 15 pitch types — no new
#     pitch names, so the sim engines need zero changes. Each team picks a home
#     venue; league fixtures are played at team1's (the home side's) ground and the
#     match pitch is drawn from that ground's profile.
#
# Import direction: this module may import from tournament_manager / stadium_manager /
# subscription_manager. Those modules must only ever import dsl_manager LAZILY
# (inside function bodies) to avoid circular imports.

import glob
import io
import json
import os
import random
import re
import datetime

import discord

from subscription_manager import DB_CACHE, async_save_to_bin, async_save_tournament_to_bin
from tournament_manager import (
    ALL_PITCHES, canonical_pitch, pick_conditions, get_tournament_standings,
    save_tournament, _acl_fill, _acl_winner_loser, _acl_next_mid, _acl_match_line,
    _TM_STAT_KEYS,
)

# ══════════════════════════════════════════════════════════════════════════════
# CONFIG — the single tuning point for the whole league. Edit freely.
# ══════════════════════════════════════════════════════════════════════════════
# Production venue set (12 grounds, one per franchise) — swap back in after testing
# by setting "team_count": 12 and "venues": _PROD_VENUES below.
_PROD_VENUES = {
    "Wankhede Stadium":            {"Flat": 40, "Hard": 30, "Bouncy": 20, "Two-Paced": 10},
    "M Chinnaswamy Stadium":       {"Flat": 45, "Dead": 25, "Hard": 20, "Two-Paced": 10},
    "MA Chidambaram Stadium":      {"Dry": 30, "Turning": 30, "Dusty": 20, "Slow": 20},
    "Eden Gardens":                {"Flat": 30, "Hard": 25, "Turning": 25, "Worn": 20},
    "Arun Jaitley Stadium":        {"Slow": 30, "Dry": 25, "Dead": 25, "Two-Paced": 20},
    "Rajiv Gandhi Intl Stadium":   {"Flat": 30, "Slow": 25, "Dry": 25, "Turning": 20},
    "Narendra Modi Stadium":       {"Hard": 30, "Flat": 25, "Bouncy": 25, "Dry": 20},
    "Sawai Mansingh Stadium":      {"Flat": 35, "Hard": 25, "Slow": 20, "Worn": 20},
    "PCA Stadium Mohali":          {"Hard": 35, "Bouncy": 25, "Flat": 25, "Green": 15},
    "Ekana Stadium":               {"Slow": 35, "Two-Paced": 25, "Dry": 20, "Dead": 20},
    "DY Patil Stadium":            {"Flat": 30, "Hard": 30, "Bouncy": 20, "Dead": 20},
    "HPCA Stadium Dharamsala":     {"Green": 30, "Hard": 30, "Bouncy": 25, "Flat": 15},
}

# ── TEST venue set: 5 grounds mixing pitch types from the default 15 ──
_TEST_VENUES = {
    "Thunder Dome":        {"Flat": 35, "Hard": 30, "Bouncy": 20, "Two-Paced": 15},   # batting paradise
    "Desert Fort Arena":   {"Dusty": 35, "Dry": 30, "Turning": 20, "Cracked": 15},    # spin hell
    "Emerald Bay Oval":    {"Green": 35, "Damp": 25, "Bouncy": 25, "Hard": 15},       # seamer's dream
    "Misty Hills Ground":  {"Slow": 30, "Soft": 25, "Two-Paced": 25, "Dead": 20},     # grind it out
    "Royal Palm Stadium":  {"Flat": 30, "Dead": 25, "Worn": 25, "Sticky": 20},        # two-faced deck
}

DSL_CONFIG = {
    "type_key": "dsl",                      # tournament_type value
    "league_key": "dsl",                    # archive/league identity (keep stable across renames)
    "display_name": "Dominators Super League",
    "short_name": "DSL",
    "format_overs": 20,
    "team_count": 5,                        # TEST (production: 12)
    "double_round_robin": True,             # home & away legs (5 teams → 20 matches; 12 → 132)
    "min_squad": 11,
    "max_squad": 18,
    "impact_player": True,
    "injuries": False,
    "conditions_mode": "stadium",           # pitch drawn from the match venue's profile
    "require_unique_venues": True,          # every team must claim a different home ground
    "auto_playoffs": True,                  # generate the Semis automatically when league ends
    # Playoff venues: "random" → random ground per playoff match, or a dict like
    # {"Semi-Final 1": "Thunder Dome", "Final": "Thunder Dome"}.
    "playoff_venue_policy": "random",
    # venue → weighted pitch profile (weights over the EXISTING engine pitch types).
    "venues": _TEST_VENUES,                 # TEST (production: _PROD_VENUES)
}

DSL_PLAYOFF_STAGE = "dsl_playoff"
DSL_KO_STAGES = (DSL_PLAYOFF_STAGE,)
DSL_PLAYOFF_ROUNDS = ("Semi-Final 1", "Semi-Final 2", "Final")

ARCHIVE_DIR = "dsl_archive"
ARCHIVE_SCHEMA_VERSION = 1

# ── Config sanity check (import time — fail loudly on a typo'd pitch name) ────
for _v, _profile in DSL_CONFIG["venues"].items():
    if not _profile:
        raise ValueError(f"DSL_CONFIG venue '{_v}' has an empty pitch profile.")
    for _p in _profile:
        if canonical_pitch(_p) != _p:
            raise ValueError(
                f"DSL_CONFIG venue '{_v}' uses unknown pitch '{_p}'. "
                f"Valid pitches: {', '.join(ALL_PITCHES)}"
            )


def is_dsl_tournament(tourney):
    return bool(tourney) and tourney.get("tournament_type") == DSL_CONFIG["type_key"]


# ══════════════════════════════════════════════════════════════════════════════
# ACCESS (bot-owner grants, independent of the Gold/Diamond tier system)
# ══════════════════════════════════════════════════════════════════════════════
def _league_access():
    la = DB_CACHE.setdefault("league_access", {})
    return la.setdefault(DSL_CONFIG["league_key"], {})


def is_dsl_enabled(server_id: str) -> bool:
    return bool(_league_access().get(str(server_id), {}).get("enabled"))


def set_dsl_enabled(server_id: str, enabled: bool):
    entry = _league_access().setdefault(str(server_id), {"enabled": False, "last_season": 0})
    entry["enabled"] = bool(enabled)
    async_save_to_bin()


def dsl_enabled_servers():
    """[(server_id, last_season)] of every server with the DSL grant."""
    return [(sid, e.get("last_season", 0)) for sid, e in _league_access().items() if e.get("enabled")]


def bump_last_season(server_id: str, season: int):
    """Raise the Mongo-persisted season floor (never lowers it)."""
    entry = _league_access().setdefault(str(server_id), {"enabled": False, "last_season": 0})
    entry["last_season"] = max(int(entry.get("last_season", 0)), int(season))
    async_save_to_bin()


# ══════════════════════════════════════════════════════════════════════════════
# SEASON NUMBERING + TOURNAMENT FACTORY
# ══════════════════════════════════════════════════════════════════════════════
def next_season_number(server_id: str) -> int:
    """1 + max(highest archived season on disk, Mongo last_season floor)."""
    archived = [s for s, _ in list_season_archives(server_id)]
    floor = _league_access().get(str(server_id), {}).get("last_season", 0)
    return max(max(archived, default=0), floor) + 1


def create_dsl_tournament(server_id: str, creator_id) -> dict:
    """Fresh preconfigured DSL season in registration. Caller saves + announces."""
    season = next_season_number(server_id)
    return {
        "server_id": str(server_id),
        "name": f"{DSL_CONFIG['display_name']} S{season}",
        "managers": [str(creator_id)],
        "teams": [],
        "status": "registration",
        "schedule": [],
        "current_match_idx": 0,
        "stats": {},
        "format_overs": DSL_CONFIG["format_overs"],
        "min_squad": DSL_CONFIG["min_squad"],
        "max_squad": DSL_CONFIG["max_squad"],
        "impact_player": DSL_CONFIG["impact_player"],
        "injuries_enabled": DSL_CONFIG["injuries"],
        "tournament_type": DSL_CONFIG["type_key"],
        "league_key": DSL_CONFIG["league_key"],
        "season": season,
        "conditions_mode": DSL_CONFIG["conditions_mode"],
        "stadiums": list(DSL_CONFIG["venues"]),
    }


# ══════════════════════════════════════════════════════════════════════════════
# VENUES & CONDITIONS
# ══════════════════════════════════════════════════════════════════════════════
def canonical_venue(name):
    """Case-insensitive match against the configured venue list, or None."""
    if not name:
        return None
    nm = str(name).strip().lower()
    return next((v for v in DSL_CONFIG["venues"] if v.lower() == nm), None)


def set_home_stadium(tourney, team_name, venue):
    """Assign a team's home ground. Returns (ok, message)."""
    v = canonical_venue(venue)
    if not v:
        return False, (f"❌ Unknown venue **{venue}**.\nVenues: " + " · ".join(DSL_CONFIG["venues"]))
    team = next((t for t in tourney.get("teams", []) if t["name"].lower() == str(team_name).lower()), None)
    if not team:
        return False, f"❌ Team **{team_name}** not found."
    if DSL_CONFIG["require_unique_venues"]:
        taken = next((t["name"] for t in tourney["teams"]
                      if t is not team and t.get("home_stadium") == v), None)
        if taken:
            return False, f"❌ **{v}** is already the home of **{taken}** — every team needs its own ground."
    team["home_stadium"] = v
    return True, f"🏟️ **{team['name']}** will play their home games at **{v}**."


def _playoff_venue(round_name, rng=random):
    policy = DSL_CONFIG["playoff_venue_policy"]
    if isinstance(policy, dict):
        v = canonical_venue(policy.get(round_name))
        if v:
            return v
    return rng.choice(list(DSL_CONFIG["venues"]))


def assign_dsl_stadiums(tourney):
    """Idempotent venue assignment: league match → home (team1) team's ground;
    playoff match → per playoff_venue_policy. Called via stadium_manager.assign_stadiums."""
    homes = {t["name"]: t.get("home_stadium") for t in tourney.get("teams", [])}
    for m in tourney.get("schedule", []):
        if m.get("status") == "completed" or m.get("stadium"):
            continue
        if m.get("stage") in DSL_KO_STAGES:
            m["stadium"] = _playoff_venue(m.get("round"))
        else:
            m["stadium"] = homes.get(m.get("team1")) or _playoff_venue(m.get("round"))


def pick_dsl_conditions(stadium, is_knockout: bool):
    """(pitch, weather): pitch from the venue's weighted profile, weather from the
    standard pools. Falls back to the generic picker for unknown venues."""
    profile = DSL_CONFIG["venues"].get(canonical_venue(stadium) or "")
    if not profile:
        return pick_conditions(is_knockout)
    pitch = random.choices(list(profile), weights=list(profile.values()), k=1)[0]
    _, weather = pick_conditions(is_knockout)
    return pitch, weather


# ══════════════════════════════════════════════════════════════════════════════
# LEAGUE SCHEDULE (circle method; team1 = HOME side)
# ══════════════════════════════════════════════════════════════════════════════
def dsl_generate_league_schedule(tourney):
    """Build the league fixtures. Leg 1 = circle-method round robin with the r%2
    flip balancing home/away; leg 2 (double_round_robin) mirrors leg 1 with venues
    swapped, so every team finishes with an identical home/away split and every
    pair meets once at each ground. team1 is ALWAYS the home team."""
    teams = [t["name"] for t in tourney["teams"]]
    if len(teams) % 2 != 0:
        teams.append("BYE")
    n = len(teams)
    rounds_per_leg = n - 1
    leg1 = []
    for r in range(rounds_per_leg):
        round_matches = []
        for i in range(n // 2):
            t1, t2 = teams[i], teams[n - 1 - i]
            if t1 == "BYE" or t2 == "BYE":
                continue
            round_matches.append((t1, t2) if r % 2 == 0 else (t2, t1))
        random.shuffle(round_matches)
        for home, away in round_matches:
            leg1.append({"round": r + 1, "team1": home, "team2": away})
        teams.insert(1, teams.pop())

    matchups = list(leg1)
    if DSL_CONFIG["double_round_robin"]:
        for m in leg1:
            matchups.append({"round": m["round"] + rounds_per_leg,
                             "team1": m["team2"], "team2": m["team1"]})

    return [{"match_id": i + 1, "round": m["round"], "stage": "league",
             "team1": m["team1"], "team2": m["team2"], "status": "pending", "result": None}
            for i, m in enumerate(matchups)]


# ══════════════════════════════════════════════════════════════════════════════
# PLAYOFFS — Top 4: Semi-Final 1 (1v4), Semi-Final 2 (2v3) → Final
# (Locked-slot bracket in the ACL style; reuses its generic helpers.)
# ══════════════════════════════════════════════════════════════════════════════
def _dsl_get(tourney, round_name):
    return next((m for m in tourney.get("schedule", [])
                 if m.get("stage") in DSL_KO_STAGES and m.get("round") == round_name), None)


def dsl_generate_playoffs(tourney):
    """Build the Top-4 knockout bracket (2 Semis → Final). Returns (ok, message).
    Idempotent: refuses when the league isn't finished or the bracket already exists."""
    if not is_dsl_tournament(tourney):
        return False, "This isn't a DSL tournament."
    league = [m for m in tourney.get("schedule", []) if m.get("stage") == "league"]
    if not league:
        return False, "No league schedule found. Start the season first."
    remaining = sum(1 for m in league if m.get("status") != "completed")
    if remaining:
        return False, f"❌ Cannot start the Playoffs yet — **{remaining}** league match(es) still pending."
    if any(m.get("stage") in DSL_KO_STAGES for m in tourney["schedule"]):
        return False, "❌ DSL Playoffs have already been generated."

    standings = get_tournament_standings(tourney)
    seeds = [n for n, _ in standings if n != "BYE"]
    if len(seeds) < 4:
        return False, "❌ Need at least 4 teams to run the Playoffs."
    s1, s2, s3, s4 = seeds[:4]
    tourney["playoff_seeds"] = seeds[:4]

    mid = _acl_next_mid(tourney)
    def mk(rnd, t1, t2, t1s, t2s, status):
        nonlocal mid
        tourney["schedule"].append({
            "match_id": mid, "round": rnd, "stage": DSL_PLAYOFF_STAGE,
            "team1": t1, "team2": t2, "team1_src": t1s, "team2_src": t2s,
            "status": status, "result": None,
        })
        mid += 1

    mk("Semi-Final 1", s1, s4,     "1st · League",         "4th · League",         "pending")
    mk("Semi-Final 2", s2, s3,     "2nd · League",         "3rd · League",         "pending")
    mk("Final",        None, None, "Winner · Semi-Final 1", "Winner · Semi-Final 2", "locked")

    # Venues + conditions for the new matches (lazy import — see module header).
    from tournament_manager import assign_tournament_conditions
    assign_tournament_conditions(tourney)
    save_tournament(tourney)
    return True, "ok"


def _dsl_try_advance(tourney):
    """Resolve TBD playoff slots as feeder matches complete; crown the champion.
    Safe to call after every match completion (idempotent)."""
    if not is_dsl_tournament(tourney):
        return
    sf1 = _dsl_get(tourney, "Semi-Final 1")
    if not sf1:
        return  # playoffs not generated yet
    sf2 = _dsl_get(tourney, "Semi-Final 2")
    fi  = _dsl_get(tourney, "Final")

    sf1w, _ = _acl_winner_loser(sf1)
    if sf1w:
        _acl_fill(fi, "team1", sf1w)
    sf2w, _ = _acl_winner_loser(sf2)
    if sf2w:
        _acl_fill(fi, "team2", sf2w)

    champ, runner_up = _acl_winner_loser(fi)
    if champ:
        tourney["dsl_champion"] = champ
        tourney["dsl_runner_up"] = runner_up
        if tourney.get("status") != "completed":
            tourney["status"] = "completed"


def dsl_bracket_embed(tourney):
    """Compact playoffs view: SF1 / SF2 → Final."""
    color = discord.Color.from_rgb(20, 60, 160)
    season = tourney.get("season", "?")
    e = discord.Embed(title=f"🔵 {tourney.get('name', DSL_CONFIG['display_name'])} — Playoffs", color=color)
    champ = tourney.get("dsl_champion")
    if champ:
        e.description = f"👑 **{DSL_CONFIG['short_name']} S{season} Champions: {champ}**"
    semis = [_dsl_get(tourney, r) for r in ("Semi-Final 1", "Semi-Final 2")]
    fi = _dsl_get(tourney, "Final")
    if any(semis):
        e.add_field(name="🏏 Semi-Finals", value="\n".join(_acl_match_line(m) for m in semis if m), inline=False)
    if fi:
        e.add_field(name="🏆 Final", value=_acl_match_line(fi), inline=False)
    e.set_footer(text="🔒 locked (awaiting feeders) · 🟢 ready to play · ✅ done")
    return e


# ══════════════════════════════════════════════════════════════════════════════
# SEASON ARCHIVES (dsl_archive/<server_id>_s<N>.json — committed to GitHub by the owner)
# ══════════════════════════════════════════════════════════════════════════════
_ARCHIVE_FILE_RE = re.compile(r"_s(\d+)\.json$")
# {server_id: (files_signature, [season dicts])}
_archive_cache = {}


def _archive_path(server_id, season):
    return os.path.join(ARCHIVE_DIR, f"{server_id}_s{season}.json")


def list_season_archives(server_id):
    """Sorted [(season, path)] for a server's on-disk archives."""
    out = []
    for p in glob.glob(os.path.join(ARCHIVE_DIR, f"{server_id}_s*.json")):
        m = _ARCHIVE_FILE_RE.search(os.path.basename(p))
        if m:
            out.append((int(m.group(1)), p))
    return sorted(out)


def invalidate_archive_cache(server_id=None):
    if server_id is None:
        _archive_cache.clear()
    else:
        _archive_cache.pop(str(server_id), None)


def load_all_seasons(server_id):
    """All archived season dicts for a server, oldest first. Cached on the archive
    files' (name, mtime, size) signature so repeated queries don't re-read disk."""
    server_id = str(server_id)
    files = list_season_archives(server_id)
    sig = tuple((p, os.path.getmtime(p), os.path.getsize(p)) for _, p in files)
    cached = _archive_cache.get(server_id)
    if cached and cached[0] == sig:
        return cached[1]
    seasons = []
    for season, path in files:
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if data.get("league_key") == DSL_CONFIG["league_key"]:
                seasons.append(data)
        except Exception as e:
            print(f"⚠️ DSL archive unreadable, skipping {path}: {e}")
    _archive_cache[server_id] = (sig, seasons)
    return seasons


def _compact_match_record(m):
    """Compact per-match record for archives/venue stats, or None if not usable."""
    r = m.get("result") or {}
    if m.get("status") != "completed" or not r:
        return None
    return {
        "match_id": m.get("match_id"),
        "round": m.get("round"),
        "stage": m.get("stage"),
        "team1": m.get("team1"), "team2": m.get("team2"),
        "stadium": r.get("stadium") or m.get("stadium"),
        "pitch": r.get("pitch") or m.get("pitch"),
        "weather": r.get("weather") or m.get("weather"),
        "batted_first": r.get("batted_first"),
        "winner": r.get("winner"),
        "t1_runs": r.get("t1_runs"), "t1_wickets": r.get("t1_wickets"), "t1_balls": r.get("t1_balls"),
        "t2_runs": r.get("t2_runs"), "t2_wickets": r.get("t2_wickets"), "t2_balls": r.get("t2_balls"),
    }


def write_season_archive(tourney):
    """Export a finished season to dsl_archive/. Returns (path, bytes) — the bytes
    are for the Discord attachment so the owner always has an offsite copy to commit."""
    server_id = str(tourney["server_id"])
    season = int(tourney.get("season", next_season_number(server_id)))
    final = _dsl_get(tourney, "Final")
    champ = tourney.get("dsl_champion") or ((final or {}).get("result") or {}).get("winner")

    archive = {
        "schema_version": ARCHIVE_SCHEMA_VERSION,
        "league_key": DSL_CONFIG["league_key"],
        "server_id": server_id,
        "season": season,
        "name": tourney.get("name"),
        "ended_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "champion": champ,
        "runner_up": tourney.get("dsl_runner_up"),
        "final_standings": [[name, st] for name, st in get_tournament_standings(tourney)],
        "teams": [{"name": t["name"], "owner_id": t.get("owner_id"),
                   "home_stadium": t.get("home_stadium")} for t in tourney.get("teams", [])],
        "stats": tourney.get("stats", {}),
        "matches": [rec for rec in (_compact_match_record(m) for m in tourney.get("schedule", [])) if rec],
    }

    os.makedirs(ARCHIVE_DIR, exist_ok=True)
    path = _archive_path(server_id, season)
    blob = json.dumps(archive, indent=1).encode("utf-8")
    with open(path, "wb") as f:
        f.write(blob)
    bump_last_season(server_id, season)
    invalidate_archive_cache(server_id)
    return path, blob


def save_uploaded_archive(raw_bytes):
    """Validate + store an owner-uploaded archive JSON (redeploy recovery).
    Returns (ok, message)."""
    try:
        data = json.loads(raw_bytes.decode("utf-8"))
    except Exception as e:
        return False, f"❌ Not valid JSON: {e}"
    if data.get("league_key") != DSL_CONFIG["league_key"]:
        return False, f"❌ Not a {DSL_CONFIG['short_name']} archive (league_key mismatch)."
    server_id, season = data.get("server_id"), data.get("season")
    if not server_id or not isinstance(season, int) or season < 1:
        return False, "❌ Archive is missing a valid server_id / season."
    if not isinstance(data.get("stats"), dict) or not isinstance(data.get("matches"), list):
        return False, "❌ Archive is missing its stats/matches sections."
    os.makedirs(ARCHIVE_DIR, exist_ok=True)
    path = _archive_path(server_id, season)
    existed = os.path.exists(path)
    with open(path, "wb") as f:
        f.write(raw_bytes)
    bump_last_season(server_id, season)
    invalidate_archive_cache(server_id)
    verb = "Replaced" if existed else "Restored"
    return True, f"✅ {verb} archive **S{season}** for server `{server_id}` → `{path}`."


# ══════════════════════════════════════════════════════════════════════════════
# CROSS-SEASON AGGREGATION (archives + current season)
# ══════════════════════════════════════════════════════════════════════════════
def _current_if_dsl(server_id, current_tourney):
    if current_tourney and is_dsl_tournament(current_tourney) \
            and str(current_tourney.get("server_id")) == str(server_id):
        return current_tourney
    return None


def iter_all_match_records(server_id, current_tourney=None):
    """Yield compact match records from every archived season, then the current one."""
    for season in load_all_seasons(server_id):
        for rec in season.get("matches", []):
            yield rec
    cur = _current_if_dsl(server_id, current_tourney)
    if cur:
        for m in cur.get("schedule", []):
            rec = _compact_match_record(m)
            if rec:
                yield rec


def aggregate_player_stats(server_id, current_tourney=None):
    """{name_lower: {"name", "teams": [..], "seasons": int, <summed _TM_STAT_KEYS>}}
    merged across all archives + the current season (case-insensitive on name)."""
    out = {}

    def _merge(stats_map, season_tag):
        for team, players in (stats_map or {}).items():
            for pname, ps in players.items():
                key = pname.lower()
                agg = out.setdefault(key, {"name": pname, "teams": [], "_seasons": set(),
                                           **{k: 0 for k in _TM_STAT_KEYS}})
                for k in _TM_STAT_KEYS:
                    agg[k] += int(ps.get(k, 0) or 0)
                if team not in agg["teams"]:
                    agg["teams"].append(team)
                agg["_seasons"].add(season_tag)

    for season in load_all_seasons(server_id):
        _merge(season.get("stats"), f"s{season.get('season')}")
    cur = _current_if_dsl(server_id, current_tourney)
    if cur:
        _merge(cur.get("stats"), "current")

    for agg in out.values():
        agg["seasons"] = len(agg.pop("_seasons"))
    return out


def aggregate_venue_stats(server_id, current_tourney=None):
    """Per-venue all-time numbers from every record that carries stadium+batted_first
    (older manually-recorded results without them are skipped):
    matches, avg 1st/2nd innings score, bat-first win % (decided games), hi/lo, pitches."""
    venues = {}
    for rec in iter_all_match_records(server_id, current_tourney):
        stadium = rec.get("stadium")
        bf = rec.get("batted_first")
        if not stadium or not bf:
            continue
        if None in (rec.get("t1_runs"), rec.get("t2_runs")):
            continue
        v = venues.setdefault(stadium, {
            "matches": 0, "first_runs": 0, "second_runs": 0,
            "decided": 0, "bat_first_wins": 0,
            "highest": None, "lowest": None, "pitch_counts": {},
        })
        first = rec["t1_runs"] if bf == rec.get("team1") else rec["t2_runs"]
        second = rec["t2_runs"] if bf == rec.get("team1") else rec["t1_runs"]
        v["matches"] += 1
        v["first_runs"] += first
        v["second_runs"] += second
        for total in (first, second):
            v["highest"] = total if v["highest"] is None else max(v["highest"], total)
            v["lowest"] = total if v["lowest"] is None else min(v["lowest"], total)
        winner = rec.get("winner")
        if winner and winner != "TIE":
            v["decided"] += 1
            if winner == bf:
                v["bat_first_wins"] += 1
        pitch = rec.get("pitch")
        if pitch:
            v["pitch_counts"][pitch] = v["pitch_counts"].get(pitch, 0) + 1

    for v in venues.values():
        n = v["matches"]
        v["avg_1st"] = v["first_runs"] / n if n else 0.0
        v["avg_2nd"] = v["second_runs"] / n if n else 0.0
        v["bat_first_win_pct"] = (v["bat_first_wins"] / v["decided"] * 100) if v["decided"] else None
    return venues


def season_history(server_id, current_tourney=None):
    """[(season, name, champion, runner_up)] oldest→newest; current season appended
    as in-progress (champion None) or completed."""
    rows = [(s.get("season"), s.get("name"), s.get("champion"), s.get("runner_up"))
            for s in load_all_seasons(server_id)]
    cur = _current_if_dsl(server_id, current_tourney)
    if cur:
        rows.append((cur.get("season"), cur.get("name"),
                     cur.get("dsl_champion"), cur.get("dsl_runner_up")))
    return rows
