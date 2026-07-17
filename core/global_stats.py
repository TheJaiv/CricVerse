"""Global career stats for every player, split by format (t20 / odi / test / custom).

Stored in a LOCAL json file (data/global_stats.json), not MongoDB, and the file is
gitignored. Render wipes the disk on every deploy/restart, so persistence works via
Discord instead of git: the owner gets the file DM'd on SIGTERM + every few hours
(see bot.py), and re-uploads it with the importstats command after a restart.

Player-test matches (cv testplayer) and super overs never reach these recorders.
"""
import hashlib
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
        "matches": 0,   # only matches where the player actually batted or bowled
        # batting
        "bat_innings": 0, "runs": 0, "balls": 0, "outs": 0,
        "fours": 0, "sixes": 0, "hs": 0, "hs_balls": 0, "hs_not_out": False,
        "fifties": 0, "hundreds": 0, "ducks": 0,
        # bowling (maidens only tracked by the test engine)
        "bowl_innings": 0, "balls_bowled": 0, "runs_conceded": 0,
        "wickets": 0, "maidens": 0, "best_wkts": 0, "best_runs": -1,
        "three_hauls": 0, "five_hauls": 0, "hattricks": 0,
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
    """Fold one innings' batting + bowling cards into the totals. `played` collects
    only players who actually batted or bowled - fielding-only XIs don't get a match."""
    for name, bs in innings.batting_stats.items():
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

    for name, ws in innings.bowling_stats.items():
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


# hs/best figures aren't summable - they take the best across formats instead
_NON_SUM_KEYS = ("hs", "hs_balls", "hs_not_out", "best_wkts", "best_runs")

def combined_totals():
    """Per-player counters summed across all formats, for the all-format leaderboards."""
    with _lock:
        out = {}
        for name, fmts in _load().items():
            t = _blank()
            for f in fmts.values():
                for k in t:
                    if k not in _NON_SUM_KEYS:
                        t[k] += f.get(k, 0)
                if (f.get("hs", 0) > t["hs"]
                        or (f.get("hs", 0) == t["hs"] and f.get("hs_not_out") and not t["hs_not_out"])):
                    t["hs"], t["hs_not_out"] = f.get("hs", 0), f.get("hs_not_out", False)
                    t["hs_balls"] = f.get("hs_balls", 0)
                fbr = f.get("best_runs", -1)
                if fbr >= 0 and (t["best_runs"] < 0
                                 or f["best_wkts"] > t["best_wkts"]
                                 or (f["best_wkts"] == t["best_wkts"] and fbr < t["best_runs"])):
                    t["best_wkts"], t["best_runs"] = f["best_wkts"], fbr
            out[name] = t
        return out


def format_totals(fmt):
    """Per-player counters for ONE format (players who never played it are absent).
    Values are copies with defaults backfilled, safe for callers to read freely."""
    with _lock:
        out = {}
        for name, fmts in _load().items():
            f = fmts.get(fmt)
            if f:
                t = _blank()
                t.update(f)
                out[name] = t
        return out


def player_count():
    with _lock:
        return len(_load())


def flush_to_disk():
    """Make sure the on-disk file matches memory (used right before exporting it)."""
    with _lock:
        _load()
        _save()
    return STATS_PATH if os.path.exists(STATS_PATH) else None


def _merge_bucket(dst, src):
    """Fold one format bucket from a backup into the live one: counters add up,
    HS and best figures keep the better of the two."""
    s = _blank()
    s.update(src)
    for k in _blank():
        if k not in _NON_SUM_KEYS:
            dst[k] += s[k]
    if (s["hs"] > dst["hs"]
            or (s["hs"] == dst["hs"] and s["hs_not_out"] and not dst["hs_not_out"])):
        dst["hs"], dst["hs_balls"], dst["hs_not_out"] = s["hs"], s["hs_balls"], s["hs_not_out"]
    if s["best_runs"] >= 0 and (dst["best_runs"] < 0
                                or s["best_wkts"] > dst["best_wkts"]
                                or (s["best_wkts"] == dst["best_wkts"] and s["best_runs"] < dst["best_runs"])):
        dst["best_wkts"], dst["best_runs"] = s["best_wkts"], s["best_runs"]


_last_import_digest = None   # guards against merging the identical file twice in a session


def import_raw(raw, mode="merge"):
    """Fold an uploaded backup into the current stats (mode="merge", default) or
    replace everything with it (mode="replace"). Merge exists for the missed-restart
    case: matches recorded after the wipe stay counted, the backup adds the history.
    Validates before touching anything so a bad upload can never nuke current data.
    Returns (ok, message)."""
    global _stats, _dirty, _last_import_digest
    try:
        data = json.loads(raw)
    except Exception as e:
        return False, f"that file isn't valid JSON ({e})"
    if not isinstance(data, dict):
        return False, "that JSON isn't a stats file (expected an object of players)"
    for name, fmts in data.items():
        if not isinstance(fmts, dict) or not all(k in FORMATS and isinstance(v, dict) for k, v in fmts.items()):
            return False, f"that JSON isn't a stats file (bad entry for '{name}')"

    digest = hashlib.sha1(raw if isinstance(raw, bytes) else raw.encode()).hexdigest()
    with _lock:
        _load()
        if mode == "replace":
            _stats = data
            _dirty = False
            _last_import_digest = digest
            _save()
            return True, f"replaced everything - stats restored for **{len(data)}** players"

        # merging the same file twice would double every counter in it
        if digest == _last_import_digest:
            return False, ("that's the exact file already merged - doing it again would "
                           "double-count it. Use `importstats replace` to restore exactly this file")
        new_players = 0
        for name, fmts in data.items():
            if name not in _stats:
                new_players += 1
            for fmt, f in fmts.items():
                _merge_bucket(_bucket(name, fmt), f)
        _dirty = True   # merged state exists nowhere else - make sure a backup goes out
        _last_import_digest = digest
        _save()
    return True, (f"merged backup into current stats - **{len(data)}** players in file, "
                  f"**{new_players}** of them new, matches played since the restart kept")


def is_dirty():
    return _dirty


def clear_dirty():
    global _dirty
    _dirty = False
