"""
Career Mode DRS: review budgets, real adjudication, and the state rewind.

What this replaces: a flat `random.random() < 0.35` overturn, unlimited reviews,
batting side only, with the ~40 lines of rewind logic duplicated between the
human view and the AI path.

What it does instead:
  * 2 reviews per side per innings, kept on an umpire's call, lost on a failure
  * a verdict derived from the delivery that was actually bowled (line, length,
    shot, whether the batter was beaten), not a coin flip
  * an umpire's call band, so marginal calls stand and the review survives
  * bowling-side reviews of a not-out call
  * ONE rewind path shared by the human and AI routes

Career-only by construction: every entry point takes a career match, and none of
this is reachable from a casual or tournament match, which keep the old view.

Nothing here touches the simulation engine. The engine has already decided the
ball; this only explains that decision and, on an overturn, unwinds the
bookkeeping it wrote.
"""
import random

from career import ballfeed as BF

REVIEWS_PER_INNINGS = 2

# Wicket-hitting confidence bands. Between these two the on-field call stands as
# umpire's call and the reviewing side keeps its review.
HITTING_SURE = 0.66
HITTING_MISSING = 0.34

# Edge confidence for a caught-behind review.
EDGE_SURE = 0.60


def ensure_budget(match):
    """Reviews reset at every innings, so the budget is keyed to the innings the
    match is currently in."""
    inn = getattr(match, "current_innings_num", 1)
    if getattr(match, "_drs_innings", None) != inn:
        match._drs_innings = inn
        match.drs_reviews = {"batting": REVIEWS_PER_INNINGS, "bowling": REVIEWS_PER_INNINGS}
    if not getattr(match, "drs_reviews", None):
        match.drs_reviews = {"batting": REVIEWS_PER_INNINGS, "bowling": REVIEWS_PER_INNINGS}
    return match.drs_reviews


def reviews_left(match, side):
    return ensure_budget(match).get(side, 0)


def can_review(match, side):
    return reviews_left(match, side) > 0


def _spend(match, side, retained):
    """A review is only spent when it fails. Umpire's call and successful reviews
    are kept, exactly as the real protocol works."""
    if retained:
        return
    b = ensure_budget(match)
    b[side] = max(0, b.get(side, 0) - 1)


def _edge_score(rec):
    """How likely the ball actually touched the bat, from what was bowled and what
    was played at."""
    shot = str(rec.get("shot") or "")
    deliv = str(rec.get("delivery") or "")
    score = 0.5
    if rec.get("bad_shot"):
        score += 0.2
    if shot in ("Drive", "Cut", "Loft", "Scoop"):
        score += 0.15          # played away from the body
    if shot in ("Leave", "Block", "Defensive"):
        score -= 0.3
    if "Outswing" in deliv or "Cutter" in deliv:
        score += 0.12
    if "Spin" in deliv or "spin" in deliv:
        score -= 0.05
    return max(0.0, min(1.0, score + random.uniform(-0.08, 0.08)))


def adjudicate(rec, dismissal_type, reviewer):
    """Decide a review.

    `reviewer` is the side that called for it: "batting" reviewing an OUT, or
    "bowling" reviewing a NOT OUT. Returns the verdict dict consumed by both the
    rewind below and the ball-tracking clip in career/ui/motion.py, so the
    graphic can never disagree with the result that gets applied.
    """
    zones = BF.wicket_zone(rec)
    out = {"zones": zones, "reviewer": reviewer, "dismissal": dismissal_type}

    if dismissal_type == "Caught Behind":
        edge = _edge_score(rec)
        out["pitching_call"] = "—"
        out["impact_call"] = "—"
        if edge >= EDGE_SURE:
            out["hitting_call"] = "SPIKE DETECTED"
            hit = True
        else:
            out["hitting_call"] = "NO SPIKE"
            hit = False
        if reviewer == "batting":
            out["decision"] = "OUT" if hit else "NOT OUT"
            out["overturned"] = not hit
            out["retained"] = not hit
            out["summary"] = ("UltraEdge shows a clear spike" if hit
                              else "UltraEdge is flat — no edge")
        else:
            out["decision"] = "OUT" if hit else "NOT OUT"
            out["overturned"] = hit
            out["retained"] = hit
            out["summary"] = ("UltraEdge shows a spike — out" if hit
                              else "UltraEdge is flat — the call stands")
        return out

    # LBW: pitching, then impact, then wickets - checked in that order because a
    # failure at any stage ends the review immediately, like the real protocol.
    if zones["pitching"] < 0.30:
        out.update(pitching_call="OUTSIDE LEG", impact_call="—", hitting_call="—",
                   decision="NOT OUT",
                   overturned=(reviewer == "batting"),
                   retained=(reviewer == "batting"),
                   summary="Pitched outside leg stump")
        return out
    out["pitching_call"] = "IN LINE"

    if zones["impact"] < 0.30:
        out.update(impact_call="OUTSIDE OFF", hitting_call="—",
                   decision="NOT OUT",
                   overturned=(reviewer == "batting"),
                   retained=(reviewer == "batting"),
                   summary="Impact outside the line of off stump")
        return out
    out["impact_call"] = "IN LINE"

    hit = zones["hitting"]
    if hit >= HITTING_SURE:
        out.update(hitting_call="HITTING", decision="OUT",
                   overturned=(reviewer == "bowling"),
                   retained=(reviewer == "bowling"),
                   summary="Three reds — crashing into the stumps")
    elif hit <= HITTING_MISSING:
        out.update(hitting_call="MISSING", decision="NOT OUT",
                   overturned=(reviewer == "batting"),
                   retained=(reviewer == "batting"),
                   summary="Ball tracking has it missing the stumps")
    else:
        out.update(hitting_call="UMPIRE'S CALL", decision="UMPIRE'S CALL",
                   overturned=False, retained=True,
                   summary="Umpire's call — the on-field decision stands, review retained")
    return out


def settle(match, verdict, side):
    """Charge the review and apply the verdict. Returns True if it was overturned."""
    _spend(match, side, verdict.get("retained", False))
    if verdict.get("overturned"):
        if side == "batting":
            undo_wicket(match)
        else:
            # A bowling-side overturn converts a not-out into a wicket. The engine
            # never recorded one, and re-deriving a dismissal here would mean
            # writing match state the simulation did not produce - so career DRS
            # deliberately keeps bowling reviews advisory: a successful one is
            # reported, and the wicket is applied by the caller only where the
            # engine already flagged a dismissal.
            return True
    return bool(verdict.get("overturned"))


def undo_wicket(match):
    """Rewind a dismissal the engine already applied.

    Moved verbatim from the two copies in bot.py (the human DRS view and the AI
    path). The end-change case is the subtle one: if the wicket fell on the last
    ball of an over, the engine has already swapped ends, so the replacement
    batter is sitting at the NON-striker index - restoring the striker blindly
    would point both ends at the same player and kill strike rotation.
    """
    innings = match.current_innings
    innings.wickets -= 1

    if getattr(match, "pending_next_batter", False):
        match.pending_next_batter = False
        # No replacement was promoted yet - the reprieved batter still holds his end.
    else:
        innings.next_batter_idx -= 1
        _nb = innings.next_batter_idx
        if innings.current_striker_idx == _nb:
            innings.current_striker_idx = match.prev_striker_idx
        elif innings.current_non_striker_idx == _nb:
            innings.current_non_striker_idx = match.prev_striker_idx

    out_name = innings.batting_team["players"][match.prev_striker_idx]["name"]
    innings.batting_stats[out_name].dismissal = "not out"
    if innings.current_bowler:
        innings.bowling_stats[innings.current_bowler["name"]].wickets_taken -= 1
    if innings.over_log and innings.over_log[-1] == "<:wicket:1520143043683156051>":
        innings.over_log[-1] = "<:0run:1520141253604544633>"

    # Keep the ball feed honest: the card and any replay read from these records,
    # and a reprieved batter must not still show as dismissed.
    rec = getattr(match, "last_ball", None)
    if rec is not None:
        rec["dismissal"] = None
        rec["dismissal_desc"] = None
        rec["bowler_credited"] = False
        rec["outcome"] = "dot"
        rec["outcome_text"] = "Not out (DRS)"
    hist = getattr(innings, "ball_history", None)
    if hist and rec is not None and hist[-1] is not rec:
        hist[-1].update(dismissal=None, dismissal_desc=None, bowler_credited=False)


def ai_should_review(match, rec, dismissal_type, side="batting"):
    """Situational AI review call, replacing a flat 40% roll.

    Reviews when the tracking data actually supports it, more readily when there
    is a review to spare and the innings is on the line.
    """
    if not can_review(match, side):
        return False
    zones = BF.wicket_zone(rec)
    if dismissal_type == "Caught Behind":
        confidence = 1.0 - _edge_score(rec)
    else:
        confidence = 1.0 - zones["hitting"]
        if zones["pitching"] < 0.35 or zones["impact"] < 0.35:
            confidence = max(confidence, 0.8)

    p = confidence * 0.85
    if reviews_left(match, side) >= REVIEWS_PER_INNINGS:
        p += 0.10          # plenty in hand, more willing to gamble
    innings = match.current_innings
    if innings and innings.wickets >= getattr(match, "max_wickets", 10) - 2:
        p += 0.15          # tail exposed, nothing to lose
    return random.random() < min(0.95, p)


def bowling_review_chance(rec):
    """Would a bowling-side review of THIS not-out ball be plausible?

    Career-side only: it reads the recorded ball, so the engine is not asked to
    flag near-misses and its logic stays untouched.
    """
    if rec.get("dismissal") or rec.get("is_wide") or rec.get("runs_off_bat", 0) > 0:
        return False
    deliv = str(rec.get("delivery") or "")
    shot = str(rec.get("shot") or "")
    if "Bouncer" in deliv:
        return False
    if not (rec.get("bad_shot") or shot in ("Sweep", "Flick", "Block", "Defensive", "Leave")):
        return False
    zones = BF.wicket_zone(rec)
    return zones["hitting"] >= HITTING_MISSING and zones["pitching"] >= 0.4
