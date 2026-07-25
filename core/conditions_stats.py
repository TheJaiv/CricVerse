"""Pitch + weather conditions tracker.

Aggregates the outcome of every real match, SPLIT BY FORMAT (t20 / odi / test /
custom), into one small document per pitch*weather*format combo, so
`cv conditions <pitch> [weather]` (with the format buttons) can show what a
surface actually plays like: avg innings scores, avg wickets fallen, chase-win %,
and whether pace or spin does the damage there.

Formats are kept apart on purpose - averaging a 15-over custom score in with a
50-over ODI is meaningless, so each format has its own bucket and its own button.

Stored in MongoDB (collection `pitch_conditions`), NOT the local json that
global_stats uses. The data is tiny (<= pitches * weathers * formats), so Mongo
survives Render restarts natively - no export/import/backup DM needed. Reuses
subscription_manager._get_db() so the connection is identical to the rest of the bot.

Updates are atomic ($inc counters, $max/$min extremes, upsert) so concurrent
recorders running inside asyncio.to_thread can't race. Player-test matches
(cv testplayer) and super overs never reach the recorder, same as global_stats.
"""
from core.subscription_manager import _get_db

COLLECTION = "pitch_conditions"
FORMATS = ("t20", "odi", "test", "custom")


def _combo_id(pitch, weather, fmt):
    return f"{pitch}|{weather}|{fmt}"


def format_key(match):
    """t20 / odi / custom for a limited-overs match. DLS may shrink format_overs
    mid-match, so judge by the ORIGINAL format (mirrors global_stats.format_key)."""
    base = getattr(match, "original_format_overs", None) or getattr(match, "format_overs", 20)
    if base == 50:
        return "odi"
    if base == 20:
        return "t20"
    return "custom"


def _role_map(innings):
    """{bowler name: role} from the fielding XI - the reliable role source (the
    test engine's BowlerStats carries no profile)."""
    try:
        return {p["name"]: p.get("role", "") for p in innings.bowling_team["players"]}
    except Exception:
        return {}


def _bowler_type(role):
    if "Spin" in role:
        return "spin"
    if "Pace" in role:
        return "pace"
    return None   # a batter sending down an over counts toward neither bucket


def _fold_bowling(innings, acc):
    """Add one innings' pace/spin wicket + ball + run splits into acc (in place)."""
    roles = _role_map(innings)
    for name, ws in getattr(innings, "bowling_stats", {}).items():
        balls = getattr(ws, "balls_bowled", 0)
        if not balls:
            continue
        role = roles.get(name, "")
        if not role:   # fall back to the limited-overs BowlerStats.profile
            prof = getattr(ws, "profile", None)
            role = prof.get("role", "") if isinstance(prof, dict) else ""
        t = _bowler_type(role)
        if t is None:
            continue
        acc[f"{t}_wkts"] += getattr(ws, "wickets_taken", 0)
        acc[f"{t}_balls"] += balls
        acc[f"{t}_runs"] += getattr(ws, "runs_conceded", 0)


def _write(pitch, weather, fmt, inc, hi, lo, match):
    try:
        _get_db()[COLLECTION].update_one(
            {"_id": _combo_id(pitch, weather, fmt)},
            {
                "$inc": inc,
                "$max": {"hi_total": hi},
                "$min": {"lo_total": lo},
                "$setOnInsert": {"pitch": pitch, "weather": weather, "fmt": fmt},
            },
            upsert=True,
        )
        match._conditions_recorded = True
    except Exception as e:
        print(f"conditions_stats record failed: {e}")


_LO_FIELDS = (
    "matches",
    "i1_runs", "i1_wkts", "i1_balls", "i1_allout",
    "i2_runs", "i2_wkts", "i2_balls", "i2_allout",
    "chase_wins", "ties",
    "pace_wkts", "pace_balls", "pace_runs",
    "spin_wkts", "spin_balls", "spin_runs",
)
_TEST_FIELDS = (
    "matches", "t_inns", "t_runs", "t_wkts", "t_balls",
    "pace_wkts", "pace_balls", "pace_runs",
    "spin_wkts", "spin_balls", "spin_runs",
)


def _lo_contribution(match):
    """(pitch, weather, fmt, acc, hi, lo) for a finished LO match, or None if it must
    not be recorded (player test / super over / half-finished / no conditions). Same
    numbers record_limited_overs_match would write - split out so a bulk run can
    accumulate many matches in memory before touching Mongo (see accumulate)."""
    if getattr(match, "is_player_test", False) or getattr(match, "is_super_over", False):
        return None
    i1 = getattr(match, "innings1", None)
    i2 = getattr(match, "innings2", None)
    if not i1 or not i2:
        return None
    pitch, weather = getattr(match, "pitch", None), getattr(match, "weather", None)
    if not pitch or not weather:
        return None

    acc = {k: 0 for k in _LO_FIELDS}
    acc["matches"] = 1
    acc["i1_runs"], acc["i1_wkts"], acc["i1_balls"] = i1.total_runs, i1.wickets, i1.total_balls
    acc["i2_runs"], acc["i2_wkts"], acc["i2_balls"] = i2.total_runs, i2.wickets, i2.total_balls
    acc["i1_allout"] = 1 if i1.wickets >= 10 else 0
    acc["i2_allout"] = 1 if i2.wickets >= 10 else 0
    if i2.total_runs > i1.total_runs:
        acc["chase_wins"] = 1
    elif i2.total_runs == i1.total_runs:
        acc["ties"] = 1
    _fold_bowling(i1, acc)
    _fold_bowling(i2, acc)
    return (pitch, weather, format_key(match), acc,
            max(i1.total_runs, i2.total_runs), min(i1.total_runs, i2.total_runs))


def record_limited_overs_match(match):
    """Fold a finished T20/ODI/custom CricketMatch into its pitch*weather*format
    doc. Idempotent per match object; skips player tests, super overs, and
    half-finished matches."""
    if getattr(match, "_conditions_recorded", False):
        return
    c = _lo_contribution(match)
    if c is None:
        return
    pitch, weather, fmt, acc, hi, lo = c
    _write(pitch, weather, fmt, acc, hi, lo, match)


def accumulate(match, batch):
    """Merge one match's conditions contribution into an in-memory `batch` dict (keyed
    by combo id) instead of writing to Mongo now. The dummy data-farm collapses
    thousands of matches this way, then flush_batch() writes them in ONE bulk round-trip.
    Marks the match recorded so it can never be double-counted."""
    if getattr(match, "_conditions_recorded", False):
        return
    c = _lo_contribution(match)
    if c is None:
        return
    pitch, weather, fmt, acc, hi, lo = c
    key = _combo_id(pitch, weather, fmt)
    b = batch.get(key)
    if b is None:
        batch[key] = {"pitch": pitch, "weather": weather, "fmt": fmt,
                      "inc": dict(acc), "hi": hi, "lo": lo}
    else:
        binc = b["inc"]
        for k, v in acc.items():
            binc[k] = binc.get(k, 0) + v
        b["hi"] = max(b["hi"], hi)
        b["lo"] = min(b["lo"], lo)
    match._conditions_recorded = True


def flush_batch(batch):
    """Write an accumulated `batch` to Mongo in one bulk_write (falling back to
    per-combo upserts if pymongo's UpdateOne is unavailable), then clear it. Returns
    the number of pitch*weather*format docs touched. No-op on an empty batch."""
    if not batch:
        return 0
    n = len(batch)
    try:
        from pymongo import UpdateOne
    except Exception:
        UpdateOne = None
    col = _get_db()[COLLECTION]
    ops = []
    for key, b in batch.items():
        upd = {"$inc": b["inc"],
               "$max": {"hi_total": b["hi"]},
               "$min": {"lo_total": b["lo"]},
               "$setOnInsert": {"pitch": b["pitch"], "weather": b["weather"], "fmt": b["fmt"]}}
        if UpdateOne is not None:
            ops.append(UpdateOne({"_id": key}, upd, upsert=True))
        else:
            col.update_one({"_id": key}, upd, upsert=True)
    if ops:
        col.bulk_write(ops, ordered=False)
    batch.clear()
    return n


def record_test_match(match):
    """Fold a finished TestMatch (any number of completed innings) into its
    pitch*weather*test doc. Test uses a per-INNINGS model (4 innings, no single
    'chase'), so it tracks avg innings total + wickets + the pace/spin split."""
    if getattr(match, "is_player_test", False):
        return
    if getattr(match, "_conditions_recorded", False):
        return
    pitch, weather = getattr(match, "pitch", None), getattr(match, "weather", None)
    if not pitch or not weather:
        return
    innings = [i for i in getattr(match, "innings_list", [])
               if getattr(i, "total_balls", 0) > 0 or getattr(i, "wickets", 0) > 0]
    if not innings:
        return

    acc = {k: 0 for k in _TEST_FIELDS}
    acc["matches"] = 1
    totals = []
    for inn in innings:
        acc["t_inns"] += 1
        acc["t_runs"] += inn.total_runs
        acc["t_wkts"] += inn.wickets
        acc["t_balls"] += inn.total_balls
        _fold_bowling(inn, acc)
        totals.append(inn.total_runs)
    _write(pitch, weather, "test", acc, max(totals), min(totals), match)


# ---- Read side (conditions command) ----

def _add_doc(dst, src):
    """Fold one raw mongo doc into an aggregate dict (dst mutated in place).
    Counters sum; hi_total keeps the max, lo_total the min."""
    for k, v in src.items():
        if k in ("_id", "pitch", "weather", "fmt") or not isinstance(v, (int, float)):
            continue
        if k == "hi_total":
            dst[k] = max(dst.get(k, 0), v)
        elif k == "lo_total":
            dst[k] = v if not dst.get(k) else min(dst[k], v)
        else:
            dst[k] = dst.get(k, 0) + v


def _econ(runs, balls):
    return (runs / (balls / 6.0)) if balls else 0.0


def _bowling_verdict(pw, sw, pe, se):
    """Who does more of the damage: more wickets wins; a >5% gap is decisive, else
    the better economy breaks it, else genuinely even. '-' means no bowling data."""
    if not (pw or sw):
        return "-"
    gap = (pw - sw) / max(1.0, pw + sw)
    if gap > 0.05:
        return "Pace"
    if gap < -0.05:
        return "Spin"
    if pe and se:
        return "Pace" if pe < se else "Spin"
    return "Even"


def summarise(raw, fmt):
    """Turn a raw/aggregated doc of sums into the derived numbers the command
    prints. Returns None if the combo has no matches. Test carries is_test=True
    and a per-innings shape; limited-overs carries the 1st/2nd-innings shape."""
    m = raw.get("matches", 0)
    if not m:
        return None
    pw, sw = raw.get("pace_wkts", 0), raw.get("spin_wkts", 0)
    pe, se = _econ(raw.get("pace_runs", 0), raw.get("pace_balls", 0)), _econ(raw.get("spin_runs", 0), raw.get("spin_balls", 0))
    out = {
        "matches": m,
        "pace_wpm": pw / m, "spin_wpm": sw / m,
        "pace_econ": pe, "spin_econ": se,
        "better_bowling": _bowling_verdict(pw, sw, pe, se),
        "hi_total": raw.get("hi_total", 0), "lo_total": raw.get("lo_total", 0),
    }
    if fmt == "test":
        inns = raw.get("t_inns", 0) or 1
        out.update({
            "is_test": True,
            "innings": raw.get("t_inns", 0),
            "avg_inns": raw.get("t_runs", 0) / inns,
            "avg_wkts": raw.get("t_wkts", 0) / inns,
        })
        return out
    out.update({
        "is_test": False,
        "i1_avg": raw.get("i1_runs", 0) / m, "i1_wkts": raw.get("i1_wkts", 0) / m,
        "i2_avg": raw.get("i2_runs", 0) / m, "i2_wkts": raw.get("i2_wkts", 0) / m,
        "i1_allout_pct": 100.0 * raw.get("i1_allout", 0) / m,
        "i2_allout_pct": 100.0 * raw.get("i2_allout", 0) / m,
        "chase_pct": 100.0 * raw.get("chase_wins", 0) / m,
        "tie_pct": 100.0 * raw.get("ties", 0) / m,
    })
    return out


def combo(pitch, weather, fmt):
    """Summary for one pitch*weather*format combo, or None if never played."""
    try:
        doc = _get_db()[COLLECTION].find_one({"_id": _combo_id(pitch, weather, fmt)})
    except Exception as e:
        print(f"conditions_stats combo read failed: {e}")
        return None
    return summarise(doc, fmt) if doc else None


def pitch_summary(pitch, fmt):
    """(overall_summary, [(weather, summary), ...]) for one pitch in one format,
    aggregated across every weather seen. (None, []) if never played."""
    try:
        docs = list(_get_db()[COLLECTION].find({"pitch": pitch, "fmt": fmt}))
    except Exception as e:
        print(f"conditions_stats pitch read failed: {e}")
        return None, []
    if not docs:
        return None, []
    agg = {}
    per_weather = []
    for d in docs:
        _add_doc(agg, d)
        s = summarise(d, fmt)
        if s:
            per_weather.append((d.get("weather", "?"), s))
    per_weather.sort(key=lambda ws: ws[1]["matches"], reverse=True)
    return summarise(agg, fmt), per_weather


def overview(fmt):
    """[(pitch, matches, avg_score), ...] per pitch in one format, most-played
    first - the no-argument landing view. avg_score is 1st-innings (LO) or per-
    innings (test)."""
    try:
        docs = list(_get_db()[COLLECTION].find({"fmt": fmt}))
    except Exception as e:
        print(f"conditions_stats overview read failed: {e}")
        return []
    by_pitch = {}
    for d in docs:
        _add_doc(by_pitch.setdefault(d.get("pitch", "?"), {}), d)
    rows = []
    for p, agg in by_pitch.items():
        m = agg.get("matches", 0)
        if not m:
            continue
        if fmt == "test":
            avg = agg.get("t_runs", 0) / (agg.get("t_inns", 0) or 1)
        else:
            avg = agg.get("i1_runs", 0) / m
        rows.append((p, m, avg))
    rows.sort(key=lambda r: r[1], reverse=True)
    return rows


def available_formats(pitch=None, weather=None):
    """Formats (in FORMATS order) that have ANY recorded data for the given scope -
    drives which format buttons to show. No pitch/weather = whole tracker."""
    flt = {}
    if pitch:
        flt["pitch"] = pitch
    if weather:
        flt["weather"] = weather
    try:
        docs = list(_get_db()[COLLECTION].find(flt, {"fmt": 1}))
    except Exception as e:
        print(f"conditions_stats available_formats read failed: {e}")
        return []
    seen = {d.get("fmt") for d in docs}
    return [f for f in FORMATS if f in seen]


def reset():
    """Wipe every stored combo (owner tool). Returns True on success."""
    try:
        _get_db()[COLLECTION].delete_many({})
        return True
    except Exception as e:
        print(f"conditions_stats reset failed: {e}")
        return False
