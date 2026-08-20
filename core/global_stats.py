"""Global career stats for every player, split by format (t20 / odi / test / custom).

Stored in MongoDB (doc `global_stats_data` in the `main` collection) with the local
json file (data/global_stats.json, gitignored) kept as a warm cache. Render wipes the
disk on every deploy/restart, so Mongo is the SOURCE OF TRUTH: _load() reads Mongo
first and only falls back to the file, and every save mirrors to Mongo on a debounced
background thread. The owner can force a write with `cv syncstats`.

Player names are dict KEYS and ~a third of them contain a dot ("R. Bose"), which is a
hazard as Mongo field names - so the whole store goes in as ONE json string field
("blob") rather than a name-keyed sub-document. It is read whole into memory anyway,
so nothing is lost by not making it queryable.

The DM backup (SIGTERM + the periodic loop) and importstats still work as an offline
escape hatch, but are no longer how the stats survive a restart.

Player-test matches (cv testplayer), super overs and GOAT XIs never reach the recorders.

On-disk shape (v2):
    {"_v": 2,
     "players": {name: {fmt: {counters + rating fields}}},
     "teams":   {fmt: {"high": [team-total records], "low": [...]}}}
v1 files (a bare {name: {fmt: {...}}} dict) are migrated on load and on import.

Ratings are an ICC-style, opposition-adjusted, recency-weighted moving average - see
the "Player ratings" section for the model.
"""
import hashlib
import json
import os
import threading
import time

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# TEST ISOLATION. The flow harnesses drive real matches through bot.py's finalize path,
# which records into this module - so without a switch they append synthetic players
# ("BcastA", "Bot 2", ...) to the real stats. With CRICVERSE_STATS_LOCAL_ONLY=1 this
# process touches NEITHER production store: no MongoDB at all, and the cache file moves
# aside to global_stats.test.json. Set it in any harness that plays matches.
LOCAL_ONLY = os.environ.get("CRICVERSE_STATS_LOCAL_ONLY") == "1"
STATS_PATH = os.path.join(_REPO_ROOT, "data",
                          "global_stats.test.json" if LOCAL_ONLY else "global_stats.json")

FORMATS = ("t20", "odi", "test", "custom")
_TEAM_RECORD_CAP = 10   # keep this many high + low team totals per format

_lock = threading.Lock()   # recorders also run inside asyncio.to_thread (simulate_all)
_stats = None              # the v2 wrapper dict (see module docstring)
_dirty = False             # something recorded since the last owner backup DM
_defer_save = False        # when True, recorders aggregate in memory but skip the
                           # per-match disk write (bulk dummy runs flush() once)

# ---- MongoDB persistence ----
# The store lives in ONE document as a json string (see the module docstring for why
# it is a blob and not a name-keyed sub-document).
MONGO_DOC_ID = "global_stats_data"
MONGO_COLLECTION = "main"
_PUSH_DEBOUNCE = 2.0       # seconds; coalesces a burst of saves into a single write
_push_lock = threading.Lock()
_push_pending = False
_push_thread = None
_last_push_error = None    # surfaced by `cv syncstats status`


def _mongo_col():
    """The collection holding the stats doc. Reuses subscription_manager's pooled
    client so this module never opens a second connection. Raises if MONGO_URI is
    unset - every caller here treats that as "Mongo unavailable", not as fatal."""
    from core.subscription_manager import _get_db
    return _get_db()[MONGO_COLLECTION]


def pull_from_mongo():
    """Read the store from Mongo. Returns the migrated store, or None if the doc is
    missing / Mongo is unreachable (the caller then falls back to the local file)."""
    global _last_push_error
    if LOCAL_ONLY:
        return None
    try:
        doc = _mongo_col().find_one({"_id": MONGO_DOC_ID})
    except Exception as e:
        _last_push_error = f"load failed: {e}"
        print(f"global_stats: Mongo load failed ({e}) - falling back to {STATS_PATH}")
        return None
    if not doc or not doc.get("blob"):
        return None
    try:
        store = _migrate(json.loads(doc["blob"]))
    except Exception as e:
        _last_push_error = f"stored blob is not valid json: {e}"
        print(f"global_stats: Mongo doc unreadable ({e}) - falling back to {STATS_PATH}")
        return None
    ok, msg = _validate_store(store)
    if not ok:
        _last_push_error = f"stored blob rejected: {msg}"
        print(f"global_stats: Mongo doc rejected ({msg}) - falling back to {STATS_PATH}")
        return None
    return store


def push_to_mongo():
    """Write the in-memory store to Mongo NOW (synchronous). Safe from any thread -
    it snapshots under _lock before touching the network. Returns (ok, message)."""
    global _last_push_error
    if LOCAL_ONLY:
        return False, "CRICVERSE_STATS_LOCAL_ONLY=1 - refusing to write to MongoDB"
    with _lock:
        if _stats is None:
            return False, "nothing loaded yet - play a match first"
        blob = json.dumps(_stats, separators=(",", ":"))
        n_players = len(_stats.get("players", {}))
    try:
        _mongo_col().replace_one(
            {"_id": MONGO_DOC_ID},
            {"_id": MONGO_DOC_ID, "blob": blob, "players": n_players,
             "bytes": len(blob), "updated": int(time.time())},
            upsert=True)
    except Exception as e:
        _last_push_error = str(e)
        return False, f"Mongo write failed: {e}"
    _last_push_error = None
    return True, f"{n_players} players ({len(blob) / 1024:.1f} KB) written to MongoDB"


def mongo_status():
    """What Mongo currently holds vs what is in memory - for `cv syncstats status`."""
    with _lock:
        mem_players = len(_stats.get("players", {})) if _stats else 0
    try:
        doc = _mongo_col().find_one({"_id": MONGO_DOC_ID}, {"blob": 0})
    except Exception as e:
        return {"ok": False, "error": str(e), "memory_players": mem_players}
    return {"ok": True, "memory_players": mem_players, "doc": doc,
            "last_error": _last_push_error}


def _push_worker():
    """Debounced background writer: sleeps, then writes once for the whole burst.
    Errors are printed and left for the next save (or a manual `cv syncstats`)."""
    global _push_pending
    while True:
        time.sleep(_PUSH_DEBOUNCE)
        with _push_lock:
            if not _push_pending:
                return
            _push_pending = False
        ok, msg = push_to_mongo()
        if not ok:
            print(f"global_stats: background Mongo push failed - {msg}")


def _schedule_mongo_push():
    """Mark the store dirty for Mongo and make sure a writer thread is running.
    Never blocks the caller - the per-match save path stays as fast as the local
    file write it already was."""
    global _push_pending, _push_thread
    with _push_lock:
        _push_pending = True
        if _push_thread is not None and _push_thread.is_alive():
            return
        _push_thread = threading.Thread(target=_push_worker, daemon=True)
        _push_thread.start()


def _blank():
    return {
        "matches": 0,   # only matches where the player actually batted or bowled
        # batting
        "bat_innings": 0, "runs": 0, "balls": 0, "outs": 0,
        "fours": 0, "sixes": 0, "hs": 0, "hs_balls": 0, "hs_not_out": False,
        "fifties": 0, "hundreds": 0, "ducks": 0,
        # bowling (maidens only tracked by the test engine)
        "bowl_innings": 0, "balls_bowled": 0, "runs_conceded": 0,
        "wickets": 0, "maidens": 0, "best_wkts": 0, "best_runs": -1,
        "three_hauls": 0, "five_hauls": 0, "hattricks": 0,
        # ICC-style ratings (raw EMA + rated-innings count for the damping factor)
        "bat_rating": 0.0, "rated_bat_inns": 0,
        "bowl_rating": 0.0, "rated_bowl_inns": 0,
    }


# counters that add up on merge / combine; everything else is handled specially
_NON_SUM_KEYS = ("hs", "hs_balls", "hs_not_out", "best_wkts", "best_runs",
                 "bat_rating", "rated_bat_inns", "bowl_rating", "rated_bowl_inns")


def _empty_store():
    return {"_v": 2, "players": {}, "teams": {}}


def _migrate(data):
    """Wrap a v1 bare-players dict into the v2 store; pass v2 through untouched."""
    if isinstance(data, dict) and data.get("_v") == 2 and "players" in data:
        data.setdefault("teams", {})
        return data
    # v1: the whole dict is the players map (or {} on first run)
    return {"_v": 2, "players": data if isinstance(data, dict) else {}, "teams": {}}


def _load():
    """Mongo is the source of truth; the local file is only a warm cache for when
    Mongo is unreachable (and for exportstats). Render wipes the disk on restart, so
    in production the Mongo read is the one that actually restores the history."""
    global _stats
    if _stats is None:
        store = pull_from_mongo()
        if store is not None:
            _stats = store
            print(f"global_stats: loaded {len(store['players'])} players from MongoDB")
            try:
                _save_file()   # refresh the local cache so exportstats matches Mongo
            except OSError:
                pass
            return _stats
        try:
            with open(STATS_PATH, "r", encoding="utf-8") as f:
                _stats = _migrate(json.load(f))
            print(f"global_stats: MongoDB empty/unreachable - loaded {len(_stats['players'])} "
                  f"players from the local file")
        except FileNotFoundError:
            _stats = _empty_store()
        except (json.JSONDecodeError, OSError) as e:
            print(f"global_stats: could not read {STATS_PATH} ({e}) - starting empty")
            _stats = _empty_store()
    return _stats


def _players():
    return _load()["players"]


def _teams():
    return _load()["teams"]


def _save_file():
    # temp file + os.replace so a crash mid-write can't corrupt the real file
    os.makedirs(os.path.dirname(STATS_PATH), exist_ok=True)
    tmp = STATS_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(_stats, f)
    os.replace(tmp, STATS_PATH)


def _save():
    """Persist: local file immediately (fast, and keeps exportstats working), Mongo on
    a debounced background thread so a match never waits on the network."""
    _save_file()
    if not LOCAL_ONLY:
        _schedule_mongo_push()


def set_defer_save(on):
    """Turn the per-match disk write off (on=True) or back on. A bulk dummy run defers
    saving so it isn't rewriting the whole JSON every match, then calls flush(). Live
    recording leaves this False, so real matches persist immediately as before."""
    global _defer_save
    _defer_save = bool(on)


def flush():
    """Persist the in-memory stats to disk now. Used with set_defer_save during bulk
    runs (safe to call any time; a no-op-ish full save otherwise)."""
    with _lock:
        if _stats is not None:
            _save()


def _bucket(name, fmt):
    b = _players().setdefault(name, {}).setdefault(fmt, _blank())
    for k, v in _blank().items():   # older files may predate newer counters
        b.setdefault(k, v)
    return b


def format_key(match):
    """DLS may shrink format_overs mid-match - judge by the ORIGINAL format."""
    base = getattr(match, "original_format_overs", None) or getattr(match, "format_overs", 20)
    if base == 50:
        return "odi"
    if base == 20:
        return "t20"
    return "custom"


def _goat_names():
    """TBECS GOAT XI players: fun-only sides whose performances never enter the
    global stats (mirrors the tournament-leaderboard rule). Lazy + guarded so a
    league-module problem can never break stat recording."""
    try:
        from league.tbecs_manager import GOAT_PLAYER_NAMES
        return GOAT_PLAYER_NAMES
    except Exception:
        return frozenset()


# ============================ Player ratings ============================
# Faithful-but-simplified ICC model. Each innings a player bats/bowls in gets a
# 0-1000 PERFORMANCE rating; a player's stored rating is an exponential moving
# average of those, so recent games dominate and old ones fade (ICC's "recent
# weighted most heavily"). A career damping factor holds new players below their
# raw rating until ~20 rated innings. All-rounder = bat x bowl / 1000.
#
# Opposition strength uses the real per-player skill attributes (profile["bat"] /
# profile["bowl"], 0-99) of the XI actually involved - a non-circular proxy for
# ICC's "quality of opposition faced". Match context boosts runs/wickets that came
# in a low/high-scoring game. Calibration constants below are deliberately simple
# and tunable; they are NOT trying to reproduce ICC's proprietary numbers.

_EMA_W = 0.18          # weight of each new innings in the moving average
_DAMP_FULL_INNS = 20   # rated innings needed before the damping factor reaches 1.0
_SKILL_BASELINE = 75.0 # opposition skill that counts as "par" (factor 1.0)


def _clamp(v, lo, hi):
    return lo if v < lo else hi if v > hi else v


def _rating_refs(fmt, overs):
    """Per-format reference points the performance formulas normalise against."""
    if fmt == "test":
        return {"runs_ref": 55, "sr_ref": None, "econ_ref": 3.2, "wkt_ref": 2.5, "par_total": 330}
    ov = overs or (20 if fmt == "t20" else 50 if fmt == "odi" else 20)
    return {
        "runs_ref": max(15, round(ov * 1.75)),
        "sr_ref": 130 if ov <= 20 else 110 if ov <= 30 else 88,
        "econ_ref": 8.0 if ov <= 20 else 6.5 if ov <= 30 else 5.2,
        "wkt_ref": 1.5,
        "par_total": round(ov * (8.0 if ov <= 20 else 6.0)),
    }


def _bat_perf(runs, balls, out, avg_opp_bowl, match_env, refs):
    """0-1000 rating for one batting innings."""
    base = 1000.0 * runs / (runs + refs["runs_ref"])   # saturating in runs
    factor = _clamp(avg_opp_bowl / _SKILL_BASELINE, 0.8, 1.25)   # opposition bowlers
    if refs["sr_ref"] and balls > 0:
        sr = runs / balls * 100
        factor *= _clamp(sr / refs["sr_ref"], 0.8, 1.25)         # tempo (not in Tests)
    # runs are worth more in a low-scoring game
    factor *= _clamp(refs["par_total"] / max(match_env, 1), 0.85, 1.2)
    return _clamp(base * factor, 0.0, 1000.0)


def _bowl_perf(wkts, balls, runs_conceded, avg_opp_bat, match_env, refs):
    """0-1000 rating for one bowling innings (wicket-weighted, economy as a floor)."""
    wkt_score = 1000.0 * wkts / (wkts + refs["wkt_ref"])
    econ = runs_conceded / balls * 6 if balls else refs["econ_ref"]
    econ_base = 1000.0 * _clamp((2 * refs["econ_ref"] - econ) / (2 * refs["econ_ref"]), 0.0, 1.0)
    base = wkt_score * 0.7 + econ_base * 0.3
    factor = _clamp(avg_opp_bat / _SKILL_BASELINE, 0.8, 1.25)    # opposition batters
    # wickets are worth more in a high-scoring game
    factor *= _clamp(match_env / max(refs["par_total"], 1), 0.85, 1.2)
    return _clamp(base * factor, 0.0, 1000.0)


def _ema(bucket, rating_key, inns_key, perf):
    if bucket[inns_key] == 0:
        bucket[rating_key] = perf
    else:
        bucket[rating_key] = bucket[rating_key] * (1 - _EMA_W) + perf * _EMA_W
    bucket[inns_key] += 1


def _damp(inns):
    if inns <= 0:
        return 0.0
    return _clamp(0.45 + 0.55 * inns / _DAMP_FULL_INNS, 0.0, 1.0)


def _shown(raw, inns):
    """Career-damped rating actually displayed to users."""
    return round(raw * _damp(inns))


# ============================ Recording ============================

def _avg_skill(players, key):
    vals = [float(p.get(key, _SKILL_BASELINE)) for p in players]
    return sum(vals) / len(vals) if vals else _SKILL_BASELINE


def _apply_innings(innings, fmt, played, refs, match_env):
    """Fold one innings' batting + bowling cards into totals AND ratings. `played`
    collects only players who actually batted or bowled - fielding-only XIs get no match."""
    goats = _goat_names()

    # opposition skill proxies for this innings
    bowlers = [p for p in innings.bowling_team["players"]
               if innings.bowling_stats.get(p["name"]) and innings.bowling_stats[p["name"]].balls_bowled > 0]
    avg_opp_bowl = _avg_skill(bowlers or innings.bowling_team["players"], "bowl")
    avg_opp_bat = _avg_skill(innings.batting_team["players"], "bat")

    for name, bs in innings.batting_stats.items():
        if name in goats:
            continue
        out = bs.dismissal != "not out"
        if bs.balls_faced == 0 and bs.runs_scored == 0 and not out:
            continue   # did not bat
        played.add(name)
        b = _bucket(name, fmt)
        b["bat_innings"] += 1
        b["runs"] += bs.runs_scored
        b["balls"] += bs.balls_faced
        b["fours"] += bs.fours
        b["sixes"] += bs.sixes
        if out:
            b["outs"] += 1
            if bs.runs_scored == 0:
                b["ducks"] += 1
        if bs.runs_scored >= 100:
            b["hundreds"] += 1
        elif bs.runs_scored >= 50:
            b["fifties"] += 1
        # 100* beats 100, so a not-out equal score takes the HS slot
        if (bs.runs_scored > b["hs"]
                or (bs.runs_scored == b["hs"] and not out and not b["hs_not_out"])):
            b["hs"] = bs.runs_scored
            b["hs_balls"] = bs.balls_faced
            b["hs_not_out"] = not out
        _ema(b, "bat_rating", "rated_bat_inns",
             _bat_perf(bs.runs_scored, bs.balls_faced, out, avg_opp_bowl, match_env, refs))

    for name, ws in innings.bowling_stats.items():
        if name in goats:
            continue
        if ws.balls_bowled == 0:
            continue
        played.add(name)
        b = _bucket(name, fmt)
        b["bowl_innings"] += 1
        b["balls_bowled"] += ws.balls_bowled
        b["runs_conceded"] += ws.runs_conceded
        b["wickets"] += ws.wickets_taken
        b["maidens"] += getattr(ws, "maidens", 0)
        b["hattricks"] += getattr(ws, "hattricks", 0)
        if ws.wickets_taken >= 3:
            b["three_hauls"] += 1
        if ws.wickets_taken >= 5:
            b["five_hauls"] += 1
        # best_runs -1 means "no best yet"; more wickets wins, fewer runs breaks ties
        if (b["best_runs"] < 0
                or ws.wickets_taken > b["best_wkts"]
                or (ws.wickets_taken == b["best_wkts"] and ws.runs_conceded < b["best_runs"])):
            b["best_wkts"] = ws.wickets_taken
            b["best_runs"] = ws.runs_conceded
        _ema(b, "bowl_rating", "rated_bowl_inns",
             _bowl_perf(ws.wickets_taken, ws.balls_bowled, ws.runs_conceded, avg_opp_bat, match_env, refs))


def _apply_team_totals(team_innings, fmt):
    """team_innings: list of dicts {total, wickets, balls, team, opponent, low_eligible}.
    Highest board takes every innings; lowest board only completed (all-out / full) ones."""
    slot = _teams().setdefault(fmt, {"high": [], "low": []})
    for ti in team_innings:
        rec = {"total": ti["total"], "wickets": ti["wickets"], "balls": ti["balls"],
               "team": ti["team"], "opponent": ti["opponent"]}
        slot["high"].append(rec)
        if ti["low_eligible"]:
            slot["low"].append(dict(rec))
    slot["high"].sort(key=lambda r: r["total"], reverse=True)
    slot["low"].sort(key=lambda r: r["total"])
    slot["high"] = slot["high"][:_TEAM_RECORD_CAP]
    slot["low"] = slot["low"][:_TEAM_RECORD_CAP]


def _record(innings_list, team_innings, fmt, match):
    global _dirty
    # a match object must only ever be folded in once (super-over finishes re-enter
    # the finalize path with the ORIGINAL match object)
    if getattr(match, "_global_stats_recorded", False):
        return
    with _lock:
        _load()
        overs = getattr(match, "format_overs", None)
        refs = _rating_refs(fmt, overs)
        # match environment = average completed-side total, the low/high-scoring signal
        totals = [ti["total"] for ti in team_innings] or [refs["par_total"]]
        match_env = sum(totals) / len(totals)
        played = set()
        for inn in innings_list:
            _apply_innings(inn, fmt, played, refs, match_env)
        for name in played:
            _bucket(name, fmt)["matches"] += 1
        _apply_team_totals(team_innings, fmt)
        _dirty = True
        if not _defer_save:
            _save()
    match._global_stats_recorded = True


def _lo_team_innings(match):
    """Build the team-total records for a limited-overs match. An innings is
    low-eligible only if it actually finished - all out, or every over bowled -
    so a chase won at 51/2 never lands on the lowest-total board."""
    try:
        from bot import _match_max_wickets
        max_w = _match_max_wickets(match)
    except Exception:
        max_w = getattr(match, "max_wickets", 10)
    max_balls = getattr(match, "max_balls", 0)
    out = []
    for inn in (match.innings1, match.innings2):
        complete = inn.wickets >= max_w or (max_balls and inn.total_balls >= max_balls)
        out.append({
            "total": inn.total_runs, "wickets": inn.wickets, "balls": inn.total_balls,
            "team": inn.batting_team["name"], "opponent": inn.bowling_team["name"],
            "low_eligible": complete,
        })
    return out


def record_limited_overs_match(match):
    """Record a finished T20/ODI/custom-overs CricketMatch. Skips player tests and
    super overs (the main match is recorded instead when the SO settles it)."""
    if getattr(match, "is_player_test", False) or getattr(match, "is_super_over", False):
        return
    if not getattr(match, "innings1", None) or not getattr(match, "innings2", None):
        return
    _record((match.innings1, match.innings2), _lo_team_innings(match), format_key(match), match)


def record_test_match(match):
    """Record a finished TestMatch (any number of completed innings, draws included).
    A declared innings is complete but not bowled out, so it never counts as a low total."""
    if getattr(match, "is_player_test", False):
        return
    innings = [i for i in getattr(match, "innings_list", []) if i.total_balls > 0 or i.wickets > 0]
    if not innings:
        return
    team_innings = [{
        "total": inn.total_runs, "wickets": inn.wickets, "balls": inn.total_balls,
        "team": inn.batting_team["name"], "opponent": inn.bowling_team["name"],
        "low_eligible": bool(getattr(inn, "is_complete", False)) and not getattr(inn, "declared", False),
    } for inn in innings]
    _record(innings, team_innings, "test", match)


# ============================ Read side ============================

def player_names():
    with _lock:
        return list(_players().keys())


def player_stats(name):
    with _lock:
        p = _players().get(name)
        return json.loads(json.dumps(p)) if p else None   # copy - callers must not mutate


def _combine_hs_best(t, f):
    if (f.get("hs", 0) > t["hs"]
            or (f.get("hs", 0) == t["hs"] and f.get("hs_not_out") and not t["hs_not_out"])):
        t["hs"], t["hs_not_out"] = f.get("hs", 0), f.get("hs_not_out", False)
        t["hs_balls"] = f.get("hs_balls", 0)
    fbr = f.get("best_runs", -1)
    if fbr >= 0 and (t["best_runs"] < 0
                     or f.get("best_wkts", 0) > t["best_wkts"]
                     or (f.get("best_wkts", 0) == t["best_wkts"] and fbr < t["best_runs"])):
        t["best_wkts"], t["best_runs"] = f.get("best_wkts", 0), fbr


def combined_totals():
    """Per-player counters summed across all formats, for the all-format leaderboards."""
    with _lock:
        out = {}
        for name, fmts in _players().items():
            t = _blank()
            for f in fmts.values():
                for k in t:
                    if k not in _NON_SUM_KEYS:
                        t[k] += f.get(k, 0)
                _combine_hs_best(t, f)
            out[name] = t
        return out


def format_totals(fmt):
    """Per-player counters for ONE format (players who never played it are absent).
    Values are copies with defaults backfilled, safe for callers to read freely."""
    with _lock:
        out = {}
        for name, fmts in _players().items():
            f = fmts.get(fmt)
            if f:
                t = _blank()
                t.update(f)
                out[name] = t
        return out


def player_ratings(name):
    """{fmt: {bat, bowl, ar, bat_inns, bowl_inns}} damped ratings for the player card.
    Only formats the player has a rated innings in appear."""
    with _lock:
        fmts = _players().get(name)
        if not fmts:
            return {}
        out = {}
        for fmt, f in fmts.items():
            bi, wi = f.get("rated_bat_inns", 0), f.get("rated_bowl_inns", 0)
            if not (bi or wi):
                continue
            bat = _shown(f.get("bat_rating", 0.0), bi)
            bowl = _shown(f.get("bowl_rating", 0.0), wi)
            out[fmt] = {
                "bat": bat, "bowl": bowl,
                "ar": round(bat * bowl / 1000) if (bi and wi) else 0,
                "bat_inns": bi, "bowl_inns": wi,
            }
        return out


_RATING_MIN_INNS = 5   # innings before a player shows on a rating leaderboard

def _rating_values(kind, fmt):
    """Lock-free (caller holds _lock): full sorted [(value, name, note)] for a rating
    board. kind in {"bat","bowl","ar"}; fmt None combines formats as an innings-weighted
    average of the damped per-format ratings. Shared by the board and the rank lookup so
    a player's card rank always matches the leaderboard."""
    bat_kind, bowl_kind = kind in ("bat", "ar"), kind in ("bowl", "ar")
    rows = []
    for name, fmts in _players().items():
        if fmt:
            if fmt not in fmts:
                continue
            buckets = [fmts[fmt]]
        else:
            buckets = list(fmts.values())
        num_bat = den_bat = num_bowl = den_bowl = 0.0
        for f in buckets:
            bi, wi = f.get("rated_bat_inns", 0), f.get("rated_bowl_inns", 0)
            if bi:
                num_bat += _shown(f.get("bat_rating", 0.0), bi) * bi
                den_bat += bi
            if wi:
                num_bowl += _shown(f.get("bowl_rating", 0.0), wi) * wi
                den_bowl += wi
        bat = num_bat / den_bat if den_bat else 0
        bowl = num_bowl / den_bowl if den_bowl else 0
        if bat_kind and den_bat < _RATING_MIN_INNS:
            continue
        if bowl_kind and den_bowl < _RATING_MIN_INNS:
            continue
        if kind == "bat":
            val, note = round(bat), f"{int(den_bat)} inns"
        elif kind == "bowl":
            val, note = round(bowl), f"{int(den_bowl)} inns"
        else:
            val, note = round(bat * bowl / 1000), "all-round"
        if val > 0:
            rows.append((val, name, note))
    rows.sort(key=lambda r: r[0], reverse=True)
    return rows


def rating_leaderboard(kind, fmt=None, top=10):
    """Sorted [(value, name, note)] for kind in {"bat","bowl","ar"}, best first."""
    with _lock:
        return _rating_values(kind, fmt)[:top]


def rating_rank(name, kind, fmt=None):
    """(rank, qualified_players) for a player on a rating board, or None if they don't
    qualify. Ties share a rank (standard competition ranking)."""
    with _lock:
        rows = _rating_values(kind, fmt)
    mine = next((v for v, n, _ in rows if n == name), None)
    if mine is None:
        return None
    rank = 1 + sum(1 for v, _, _ in rows if v > mine)
    return rank, len(rows)


def team_records(fmt=None, high=True, top=10):
    """Sorted team-total records. fmt None merges every format's records."""
    with _lock:
        if fmt:
            recs = list(_teams().get(fmt, {}).get("high" if high else "low", []))
        else:
            recs = []
            for slot in _teams().values():
                recs += slot.get("high" if high else "low", [])
    recs.sort(key=lambda r: r["total"], reverse=high)
    return recs[:top]


def player_count():
    with _lock:
        return len(_players())


def flush_to_disk():
    """Make sure the on-disk file matches memory (used right before exporting it)."""
    with _lock:
        _load()
        _save()
    return STATS_PATH if os.path.exists(STATS_PATH) else None


# ============================ Import / merge ============================

def _merge_bucket(dst, src):
    """Fold one format bucket from a backup into the live one: counters add up,
    HS/best figures keep the better, ratings reconcile as an innings-weighted blend."""
    s = _blank()
    s.update(src)
    # ratings first (need the pre-merge innings counts as weights)
    for rk, ik in (("bat_rating", "rated_bat_inns"), ("bowl_rating", "rated_bowl_inns")):
        da, db = dst[ik], s[ik]
        if da + db > 0:
            dst[rk] = (dst[rk] * da + s[rk] * db) / (da + db)
    for k in _blank():
        if k not in _NON_SUM_KEYS:
            dst[k] += s[k]
    dst["rated_bat_inns"] += s["rated_bat_inns"]
    dst["rated_bowl_inns"] += s["rated_bowl_inns"]
    _combine_hs_best(dst, s)


def _merge_team_records(src_teams):
    for fmt, slot in (src_teams or {}).items():
        dst = _teams().setdefault(fmt, {"high": [], "low": []})
        for rec in slot.get("high", []):
            if rec not in dst["high"]:
                dst["high"].append(rec)
        for rec in slot.get("low", []):
            if rec not in dst["low"]:
                dst["low"].append(rec)
        dst["high"].sort(key=lambda r: r["total"], reverse=True)
        dst["low"].sort(key=lambda r: r["total"])
        dst["high"] = dst["high"][:_TEAM_RECORD_CAP]
        dst["low"] = dst["low"][:_TEAM_RECORD_CAP]


def _validate_store(store):
    """True if `store` (already migrated to v2) looks like a real stats file."""
    if not isinstance(store.get("players"), dict) or not isinstance(store.get("teams"), dict):
        return False, "that JSON isn't a stats file (missing players/teams)"
    for name, fmts in store["players"].items():
        if not isinstance(fmts, dict) or not all(k in FORMATS and isinstance(v, dict) for k, v in fmts.items()):
            return False, f"that JSON isn't a stats file (bad entry for '{name}')"
    return True, ""


_last_import_digest = None   # guards against merging the identical file twice in a session


def import_raw(raw, mode="merge"):
    """Fold an uploaded backup into the current stats (mode="merge", default) or
    replace everything with it (mode="replace"). Merge exists for the missed-restart
    case: matches recorded after the wipe stay counted, the backup adds the history.
    Accepts both v1 (bare players) and v2 backups. Validates before touching anything
    so a bad upload can never nuke current data. Returns (ok, message)."""
    global _stats, _dirty, _last_import_digest
    try:
        data = json.loads(raw)
    except Exception as e:
        return False, f"that file isn't valid JSON ({e})"
    if not isinstance(data, dict):
        return False, "that JSON isn't a stats file (expected an object)"
    store = _migrate(data)
    ok, msg = _validate_store(store)
    if not ok:
        return False, msg

    digest = hashlib.sha1(raw if isinstance(raw, bytes) else raw.encode()).hexdigest()
    with _lock:
        _load()
        n_players = len(store["players"])
        if mode == "replace":
            _stats = store
            _dirty = False
            _last_import_digest = digest
            _save()
            return True, f"replaced everything - stats restored for **{n_players}** players"

        # merging the same file twice would double every counter in it
        if digest == _last_import_digest:
            return False, ("that's the exact file already merged - doing it again would "
                           "double-count it. Use `importstats replace` to restore exactly this file")
        new_players = 0
        for name, fmts in store["players"].items():
            if name not in _players():
                new_players += 1
            for fmt, f in fmts.items():
                _merge_bucket(_bucket(name, fmt), f)
        _merge_team_records(store["teams"])
        _dirty = True   # merged state exists nowhere else - make sure a backup goes out
        _last_import_digest = digest
        _save()
    return True, (f"merged backup into current stats - **{n_players}** players in file, "
                  f"**{new_players}** of them new, matches played since the restart kept")


def replace_store(store):
    """Swap the in-memory store for `store` (already migrated + validated) and refresh
    the local cache file. Used by `cv syncstats pull` to adopt what Mongo holds -
    deliberately does NOT push back, so a pull can never overwrite Mongo."""
    global _stats, _dirty, _last_import_digest
    with _lock:
        _stats = store
        _dirty = False
        _last_import_digest = None   # a different store: the old merge guard is meaningless
        try:
            _save_file()
        except OSError as e:
            print(f"global_stats: local cache refresh failed after pull ({e})")
        return len(_stats.get("players", {}))


def is_dirty():
    return _dirty


def clear_dirty():
    global _dirty
    _dirty = False
