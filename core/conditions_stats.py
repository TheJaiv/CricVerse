"""Pitch + weather conditions tracker.

Aggregates the outcome of every real limited-overs match (T20 / ODI / custom-overs)
into one small document per pitch*weather combo, so `cv conditions <pitch> [weather]`
can show what a surface actually plays like: avg 1st/2nd-innings score, avg wickets
fallen, chase-win %, and whether pace or spin does the damage there.

Stored in MongoDB (collection `pitch_conditions`), NOT the local json that
global_stats uses. The data is tiny (<= pitches * weathers ~ 170 rows), so Mongo
survives Render restarts natively - no export/import/backup DM needed. Reuses
subscription_manager._get_db() so the connection is identical to the rest of the bot.

Updates are atomic ($inc counters, $max/$min extremes, upsert) so concurrent
recorders running inside asyncio.to_thread can't race. Player-test matches
(cv testplayer) and super overs never reach the recorder, same as global_stats.
"""
from core.subscription_manager import _get_db

COLLECTION = "pitch_conditions"

# Counter fields that are simple running sums (averages are derived on read).
_SUM_FIELDS = (
    "matches",
    "i1_runs", "i1_wkts", "i1_balls", "i1_allout",
    "i2_runs", "i2_wkts", "i2_balls", "i2_allout",
    "chase_wins", "ties",
    "pace_wkts", "pace_balls", "pace_runs",
    "spin_wkts", "spin_balls", "spin_runs",
)


def _combo_id(pitch, weather):
    return f"{pitch}|{weather}"


def _bowler_type(profile):
    """pace / spin / None from a bowler's engine role. Part-timers with neither
    tag (a batter sending down an over) count toward neither type bucket."""
    role = (profile or {}).get("role", "") if isinstance(profile, dict) else ""
    if "Spin" in role:
        return "spin"
    if "Pace" in role:
        return "pace"
    return None


def _fold_bowling(innings, acc):
    """Add one innings' pace/spin wicket + ball + run splits into acc (in place)."""
    for ws in getattr(innings, "bowling_stats", {}).values():
        balls = getattr(ws, "balls_bowled", 0)
        if not balls:
            continue
        t = _bowler_type(getattr(ws, "profile", None))
        if t is None:
            continue
        acc[f"{t}_wkts"] += getattr(ws, "wickets_taken", 0)
        acc[f"{t}_balls"] += balls
        acc[f"{t}_runs"] += getattr(ws, "runs_conceded", 0)


def record_limited_overs_match(match):
    """Fold a finished T20/ODI/custom CricketMatch into its pitch*weather doc.
    Idempotent per match object (super-over finalisation re-enters with the same
    object). Skips player tests, super overs, and half-finished matches."""
    if getattr(match, "is_player_test", False) or getattr(match, "is_super_over", False):
        return
    if getattr(match, "_conditions_recorded", False):
        return
    i1 = getattr(match, "innings1", None)
    i2 = getattr(match, "innings2", None)
    if not i1 or not i2:
        return
    pitch = getattr(match, "pitch", None)
    weather = getattr(match, "weather", None)
    if not pitch or not weather:
        return

    acc = {k: 0 for k in _SUM_FIELDS}
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

    hi = max(i1.total_runs, i2.total_runs)
    lo = min(i1.total_runs, i2.total_runs)
    try:
        _get_db()[COLLECTION].update_one(
            {"_id": _combo_id(pitch, weather)},
            {
                "$inc": acc,
                "$max": {"hi_total": hi},
                "$min": {"lo_total": lo},
                "$setOnInsert": {"pitch": pitch, "weather": weather},
            },
            upsert=True,
        )
        match._conditions_recorded = True
    except Exception as e:
        print(f"conditions_stats record failed: {e}")


# ---- Read side (conditions command) ----

def _blank_doc():
    d = {k: 0 for k in _SUM_FIELDS}
    d["hi_total"] = 0
    d["lo_total"] = 0
    return d


def _add_doc(dst, src):
    """Fold one raw mongo doc into an aggregate (dst mutated in place)."""
    for k in _SUM_FIELDS:
        dst[k] += src.get(k, 0)
    if src.get("hi_total", 0) > dst["hi_total"]:
        dst["hi_total"] = src["hi_total"]
    slo = src.get("lo_total", 0)
    if slo and (dst["lo_total"] == 0 or slo < dst["lo_total"]):
        dst["lo_total"] = slo


def summarise(raw):
    """Turn a raw/aggregated doc of sums into the derived averages the command
    prints. Returns None if the combo has no matches."""
    m = raw.get("matches", 0)
    if not m:
        return None

    def _econ(runs, balls):
        return (runs / (balls / 6.0)) if balls else 0.0

    pace_w, spin_w = raw["pace_wkts"], raw["spin_wkts"]
    pace_econ, spin_econ = _econ(raw["pace_runs"], raw["pace_balls"]), _econ(raw["spin_runs"], raw["spin_balls"])
    # "who does the damage": more wickets wins; a >5% wicket gap is decisive, else
    # the tie breaks on economy, else genuinely even.
    if pace_w or spin_w:
        gap = (pace_w - spin_w) / max(1.0, pace_w + spin_w)
        if gap > 0.05:
            verdict = "Pace"
        elif gap < -0.05:
            verdict = "Spin"
        elif pace_econ and spin_econ:
            verdict = "Pace" if pace_econ < spin_econ else "Spin"
        else:
            verdict = "Even"
    else:
        verdict = "-"

    return {
        "matches": m,
        "i1_avg": raw["i1_runs"] / m, "i1_wkts": raw["i1_wkts"] / m,
        "i2_avg": raw["i2_runs"] / m, "i2_wkts": raw["i2_wkts"] / m,
        "i1_allout_pct": 100.0 * raw["i1_allout"] / m,
        "i2_allout_pct": 100.0 * raw["i2_allout"] / m,
        "chase_pct": 100.0 * raw["chase_wins"] / m,
        "tie_pct": 100.0 * raw["ties"] / m,
        "hi_total": raw["hi_total"], "lo_total": raw["lo_total"],
        "pace_wpm": pace_w / m, "spin_wpm": spin_w / m,
        "pace_econ": pace_econ, "spin_econ": spin_econ,
        "better_bowling": verdict,
    }


def combo(pitch, weather):
    """Summary for one pitch*weather combo, or None if never played."""
    try:
        doc = _get_db()[COLLECTION].find_one({"_id": _combo_id(pitch, weather)})
    except Exception as e:
        print(f"conditions_stats combo read failed: {e}")
        return None
    return summarise(doc) if doc else None


def pitch_summary(pitch):
    """(overall_summary, [(weather, summary), ...]) aggregated across every weather
    seen on this pitch. Returns (None, []) if the pitch has never been played."""
    try:
        docs = list(_get_db()[COLLECTION].find({"pitch": pitch}))
    except Exception as e:
        print(f"conditions_stats pitch read failed: {e}")
        return None, []
    if not docs:
        return None, []
    agg = _blank_doc()
    per_weather = []
    for d in docs:
        _add_doc(agg, d)
        s = summarise(d)
        if s:
            per_weather.append((d.get("weather", "?"), s))
    per_weather.sort(key=lambda ws: ws[1]["matches"], reverse=True)
    return summarise(agg), per_weather


def overview():
    """[(pitch, matches, avg_1st_inn_score), ...] per pitch, most-played first -
    the no-argument landing view."""
    try:
        docs = list(_get_db()[COLLECTION].find({}))
    except Exception as e:
        print(f"conditions_stats overview read failed: {e}")
        return []
    by_pitch = {}
    for d in docs:
        p = d.get("pitch", "?")
        agg = by_pitch.setdefault(p, _blank_doc())
        _add_doc(agg, d)
    rows = []
    for p, agg in by_pitch.items():
        m = agg["matches"]
        if m:
            rows.append((p, m, agg["i1_runs"] / m))
    rows.sort(key=lambda r: r[1], reverse=True)
    return rows


def reset():
    """Wipe every stored combo (owner tool). Returns True on success."""
    try:
        _get_db()[COLLECTION].delete_many({})
        return True
    except Exception as e:
        print(f"conditions_stats reset failed: {e}")
        return False
