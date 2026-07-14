"""Global career stats for every player, split by format (t20 / odi / test / custom).

Stored in a LOCAL json file (data/global_stats.json), not MongoDB, and the file is
gitignored. Render wipes the disk on every deploy/restart, so persistence works via
Discord instead of git: the owner gets the file DM'd on SIGTERM + every few hours
(see bot.py), and re-uploads it with the importstats command after a restart.

Player-test matches (cv testplayer) and super overs never reach these recorders.
"""
import json
import os
import threading

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATS_PATH = os.path.join(_REPO_ROOT, "data", "global_stats.json")

FORMATS = ("t20", "odi", "test", "custom")

_lock = threading.Lock()   # recorders also run inside asyncio.to_thread (simulate_all)
_stats = None              # {player_name: {format_key: {counters}}}
_dirty = False             # something recorded since the last owner backup DM


def _blank():
    return {
        "matches": 0,
        # batting
        "bat_innings": 0, "runs": 0, "balls": 0, "outs": 0,
        "fours": 0, "sixes": 0, "hs": 0, "hs_not_out": False,
        "fifties": 0, "hundreds": 0, "ducks": 0,
        # bowling (maidens only tracked by the test engine)
        "bowl_innings": 0, "balls_bowled": 0, "runs_conceded": 0,
        "wickets": 0, "maidens": 0, "best_wkts": 0, "best_runs": -1, "five_hauls": 0,
    }


def _load():
    global _stats
    if _stats is None:
        try:
            with open(STATS_PATH, "r", encoding="utf-8") as f:
                _stats = json.load(f)
        except FileNotFoundError:
            _stats = {}
        except (json.JSONDecodeError, OSError) as e:
            print(f"global_stats: could not read {STATS_PATH} ({e}) - starting empty")
            _stats = {}
    return _stats


def _save():
    # temp file + os.replace so a crash mid-write can't corrupt the real file
    os.makedirs(os.path.dirname(STATS_PATH), exist_ok=True)
    tmp = STATS_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(_stats, f)
    os.replace(tmp, STATS_PATH)


def _bucket(name, fmt):
    b = _stats.setdefault(name, {}).setdefault(fmt, _blank())
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


def _apply_innings(innings, fmt, played):
    """Fold one innings' batting + bowling cards into the totals.
    `played` collects everyone in either XI so match counts include non-batting fielders."""
    for name, bs in innings.batting_stats.items():
        played.add(name)
        out = bs.dismissal != "not out"
        if bs.balls_faced == 0 and bs.runs_scored == 0 and not out:
            continue   # did not bat
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
            b["hs_not_out"] = not out

    for name, ws in innings.bowling_stats.items():
        played.add(name)
        if ws.balls_bowled == 0:
            continue
        b = _bucket(name, fmt)
        b["bowl_innings"] += 1
        b["balls_bowled"] += ws.balls_bowled
        b["runs_conceded"] += ws.runs_conceded
        b["wickets"] += ws.wickets_taken
        b["maidens"] += getattr(ws, "maidens", 0)
        if ws.wickets_taken >= 5:
            b["five_hauls"] += 1
        # best_runs -1 means "no best yet"; more wickets wins, fewer runs breaks ties
        if (b["best_runs"] < 0
                or ws.wickets_taken > b["best_wkts"]
                or (ws.wickets_taken == b["best_wkts"] and ws.runs_conceded < b["best_runs"])):
            b["best_wkts"] = ws.wickets_taken
            b["best_runs"] = ws.runs_conceded


def _record(innings_iter, fmt, match):
    global _dirty
    # a match object must only ever be folded in once (super-over finishes re-enter
    # the finalize path with the ORIGINAL match object)
    if getattr(match, "_global_stats_recorded", False):
        return
    with _lock:
        _load()
        played = set()
        for inn in innings_iter:
            _apply_innings(inn, fmt, played)
        for name in played:
            _bucket(name, fmt)["matches"] += 1
        _dirty = True
        _save()
    match._global_stats_recorded = True


def record_limited_overs_match(match):
    """Record a finished T20/ODI/custom-overs CricketMatch. Skips player tests and
    super overs (the main match is recorded instead when the SO settles it)."""
    if getattr(match, "is_player_test", False) or getattr(match, "is_super_over", False):
        return
    if not getattr(match, "innings1", None) or not getattr(match, "innings2", None):
        return
    _record((match.innings1, match.innings2), format_key(match), match)


def record_test_match(match):
    """Record a finished TestMatch (any number of completed innings, draws included)."""
    if getattr(match, "is_player_test", False):
        return
    innings = [i for i in getattr(match, "innings_list", []) if i.total_balls > 0 or i.wickets > 0]
    if not innings:
        return
    _record(innings, "test", match)


# ---- Read side (stats command / export / import) ----

def player_names():
    with _lock:
        return list(_load().keys())


def player_stats(name):
    with _lock:
        p = _load().get(name)
        return json.loads(json.dumps(p)) if p else None   # copy - callers must not mutate


def leaderboard(key, top=10):
    """Top players by a counter summed across all formats, e.g. leaderboard('runs')."""
    with _lock:
        rows = [(n, sum(f.get(key, 0) for f in fmts.values())) for n, fmts in _load().items()]
    rows = [(n, v) for n, v in rows if v > 0]
    rows.sort(key=lambda r: r[1], reverse=True)
    return rows[:top]


def player_count():
    with _lock:
        return len(_load())


def flush_to_disk():
    """Make sure the on-disk file matches memory (used right before exporting it)."""
    with _lock:
        _load()
        _save()
    return STATS_PATH if os.path.exists(STATS_PATH) else None


def import_raw(raw):
    """Replace ALL stats with an uploaded backup. Validates before overwriting so a
    bad upload can never nuke the current data. Returns (ok, message)."""
    global _stats, _dirty
    try:
        data = json.loads(raw)
    except Exception as e:
        return False, f"that file isn't valid JSON ({e})"
    if not isinstance(data, dict):
        return False, "that JSON isn't a stats file (expected an object of players)"
    for name, fmts in data.items():
        if not isinstance(fmts, dict) or not all(k in FORMATS and isinstance(v, dict) for k, v in fmts.items()):
            return False, f"that JSON isn't a stats file (bad entry for '{name}')"
    with _lock:
        _stats = data
        _dirty = False
        _save()
    return True, f"restored stats for **{len(data)}** players"


def is_dirty():
    return _dirty


def clear_dirty():
    global _dirty
    _dirty = False
