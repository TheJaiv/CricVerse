"""
Per-ball fact record shared by the T20 and ODI engines.

The engines decide a delivery entirely in local variables and expose nothing but
a formatted commentary string (match.last_commentary) plus an emoji pushed onto
innings.over_log - and over_log is wiped by the callers at every over break. So
nothing downstream can redraw a ball, chart an innings or re-examine a dismissal.

record_ball() writes the facts onto match.last_ball and appends them to
innings.ball_history, which is NEVER cleared. It is additive: no caller reads a
return value from execute_ball_math_*, so nothing existing changes behaviour.

Only what the engine actually decided goes in here. Presentation geometry (pitch
point, line, trajectory) is derived from these fields in career/ballfeed.py - it
is not invented in the engine.
"""

# One innings can only hold max_balls + extras deliveries; the cap is a guard
# against a stuck sim growing the list without bound, not a real limit.
BALL_HISTORY_CAP = 2000


def wants_records(match):
    """Recording is CAREER-ONLY.

    Nothing outside career mode reads these records, and every other match type -
    casual, tournaments, and especially the bulk `cv dummyrun` sims - would pay
    ~200 KB of history per match for data nobody consumes. `record_balls` is the
    explicit opt-in flag if another mode ever wants the feed.
    """
    return bool(getattr(match, "record_balls", False)
                or getattr(match, "is_club", False)
                or getattr(match, "is_debut", False)
                or getattr(match, "is_scenario", False))


def record_ball(match, innings, ball_index, **facts):
    """Store one delivery's facts on the match and the innings.

    Purely observational: it is called AFTER the outcome is decided and consumes
    no randomness, so the simulation is bit-identical whether it runs or not.

    ball_index is innings.total_balls as it was BEFORE this delivery, snapshotted
    at the top of the ball so the over/ball numbering is identical for legal
    balls (which increment total_balls) and wides/no-balls (which do not).
    """
    if not wants_records(match):
        return None
    rec = {
        "innings": getattr(match, "current_innings_num", 1),
        "over": ball_index // 6,
        "ball": ball_index % 6 + 1,
        "ball_index": ball_index,
        "runs_total": innings.total_runs,
        "wickets": innings.wickets,
    }
    rec.update(facts)

    match.last_ball = rec
    hist = getattr(innings, "ball_history", None)
    if hist is None:
        hist = innings.ball_history = []
    if len(hist) < BALL_HISTORY_CAP:
        hist.append(rec)
    return rec
