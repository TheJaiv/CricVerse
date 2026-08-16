"""
Player condition: form, fitness, injuries, workload.

A career used to be a bag of lifetime totals - a Gold player was exactly as good
in his twentieth match as his first. This gives a career a state that moves:
runs and wickets build form, workload drains fitness, heavy workload risks an
injury, and rest restores.

Two deliberate constraints:

1. NO ENGINE CHANGES. Condition is applied where a career is converted into an
   engine player (career_manager.career_to_engine), so the simulation itself is
   untouched and every other match type is unaffected. That rules out in-match
   stamina decay, which would need engine work - the engine's own bowler-fatigue
   term still handles that side.

2. The rating swing is BOUNDED (+/-3). The sim is calibrated; an unbounded form
   multiplier would quietly move CricVerse's run-rate and wicket targets. Form
   should decide close games, not rewrite the balance.

All fields are lazily defaulted, so career documents written before this existed
load unchanged.
"""
import random
import time

FORM_BASE = 50
FORM_WINDOW = 10           # innings kept in the rolling form record
FORM_ALPHA = 0.35          # EWMA weight on the newest innings
MAX_RATING_SWING = 3.0     # hard cap on what form+fitness can move a rating by

FITNESS_MAX = 100
FITNESS_RECOVERY_PER_DAY = 22.0
FITNESS_FLOOR_FOR_INJURY = 45

# Workload costs. Bowling is the hard part of a cricketer's day, so it drains
# several times faster than batting.
DRAIN_PER_BALL_BOWLED = 0.55
DRAIN_PER_BALL_FACED = 0.10

INJURY_TIERS = [
    # (name, matches out, fitness floor after it lands, weight)
    ("Niggle", 1, 70, 60),
    ("Strain", 2, 55, 30),
    ("Tear", 4, 35, 10),
]


def ensure(career):
    """Add the condition fields to a career that predates them."""
    if "form" not in career or not isinstance(career.get("form"), dict):
        career["form"] = {"rating": FORM_BASE, "recent": []}
    if "fitness" not in career or not isinstance(career.get("fitness"), dict):
        career["fitness"] = {"value": FITNESS_MAX, "injury": None, "updated": int(time.time())}
    if "workload" not in career or not isinstance(career.get("workload"), dict):
        career["workload"] = {"balls_bowled": 0, "balls_faced": 0, "matches_since_rest": 0}
    career["fitness"].setdefault("updated", int(time.time()))
    career["fitness"].setdefault("injury", None)
    return career


# Fitness
def _apply_recovery(career, now=None):
    """Fitness regenerates with real time since it was last touched."""
    ensure(career)
    now = now or int(time.time())
    f = career["fitness"]
    last = f.get("updated", now)
    days = max(0.0, (now - last) / 86400.0)
    if days <= 0:
        return f["value"]
    ceiling = FITNESS_MAX
    inj = f.get("injury")
    if inj:
        ceiling = inj.get("ceiling", 70)     # an injury caps recovery until it clears
    f["value"] = min(ceiling, f.get("value", FITNESS_MAX) + days * FITNESS_RECOVERY_PER_DAY)
    f["updated"] = now
    return f["value"]


def fitness(career):
    return round(_apply_recovery(career))


def is_injured(career):
    ensure(career)
    inj = career["fitness"].get("injury")
    return bool(inj and inj.get("matches_left", 0) > 0)


def injury_label(career):
    ensure(career)
    inj = career["fitness"].get("injury")
    if not inj or inj.get("matches_left", 0) <= 0:
        return None
    return f"{inj['type']} ({inj['matches_left']} match{'es' if inj['matches_left'] != 1 else ''})"


def rest(career, now=None):
    """`cv rest` - skip playing to recover. Advances the recovery clock a day and
    clears one match off an injury."""
    ensure(career)
    now = now or int(time.time())
    f = career["fitness"]
    f["updated"] = min(now, f.get("updated", now) - 86400)
    _apply_recovery(career, now)
    career["workload"]["matches_since_rest"] = 0
    inj = f.get("injury")
    if inj and inj.get("matches_left", 0) > 0:
        inj["matches_left"] -= 1
        if inj["matches_left"] <= 0:
            f["injury"] = None
    return round(f["value"])


# Form
def expected_runs(career):
    """What this player is expected to make in an innings, from his own rating.
    Form is measured against this, so improving a career raises the bar too."""
    from career import career_manager as CM
    bat = CM.bat_skill(career["attributes"])
    return max(6.0, (bat - 50) * 0.62)


def expected_wickets(career):
    from career import career_manager as CM
    bowl = CM.bowl_skill(career["attributes"])
    return max(0.2, (bowl - 45) * 0.035)


def record_innings(career, runs=0, balls=0, wickets=0, balls_bowled=0, out=True):
    """Fold one match into the rolling form record. Returns the new form rating."""
    ensure(career)
    exp_r = expected_runs(career)
    exp_w = expected_wickets(career)

    # Score this outing 0..100 against expectation. Batting and bowling both
    # count; a player who does neither well drifts down.
    bat_score = 50.0
    if balls > 0 or out:
        ratio = (runs + (8 if not out else 0)) / max(1.0, exp_r)
        bat_score = max(0.0, min(100.0, 50.0 * ratio))
    bowl_score = None
    if balls_bowled > 0:
        ratio = wickets / max(0.2, exp_w)
        bowl_score = max(0.0, min(100.0, 50.0 * ratio))

    if bowl_score is None:
        outing = bat_score
    elif balls == 0:
        outing = bowl_score
    else:
        outing = 0.6 * bat_score + 0.4 * bowl_score

    f = career["form"]
    cur = f.get("rating", FORM_BASE)
    f["rating"] = round(cur + (outing - cur) * FORM_ALPHA, 1)
    recent = f.get("recent", [])
    recent.append(round(outing))
    f["recent"] = recent[-FORM_WINDOW:]
    return f["rating"]


def form(career):
    ensure(career)
    return round(career["form"].get("rating", FORM_BASE))


def form_arrow(career):
    v = form(career)
    if v >= 70:
        return "▲ hot"
    if v >= 55:
        return "▲ good"
    if v >= 40:
        return "– steady"
    if v >= 25:
        return "▼ poor"
    return "▼ cold"


# Workload and injuries
def record_workload(career, balls_faced=0, balls_bowled=0):
    """Drain fitness for a match's work and roll for an injury."""
    ensure(career)
    _apply_recovery(career)
    w = career["workload"]
    w["balls_bowled"] = w.get("balls_bowled", 0) + balls_bowled
    w["balls_faced"] = w.get("balls_faced", 0) + balls_faced
    w["matches_since_rest"] = w.get("matches_since_rest", 0) + 1

    stamina = career["attributes"].get("stamina", 60)
    # A high-stamina player pays less for the same work (0.65x at 99, 1.35x at 1).
    resilience = 1.35 - (stamina / 99.0) * 0.7
    drain = (balls_bowled * DRAIN_PER_BALL_BOWLED
             + balls_faced * DRAIN_PER_BALL_FACED) * resilience

    f = career["fitness"]
    f["value"] = max(0.0, f.get("value", FITNESS_MAX) - drain)
    f["updated"] = int(time.time())

    # tick down an existing injury
    inj = f.get("injury")
    if inj and inj.get("matches_left", 0) > 0:
        inj["matches_left"] -= 1
        if inj["matches_left"] <= 0:
            f["injury"] = None
        return None

    return _roll_injury(career, balls_bowled)


def _roll_injury(career, balls_bowled):
    """Injury chance rises as fitness falls and as the player keeps playing without
    rest. A fresh player is essentially never injured."""
    f = career["fitness"]
    if f.get("injury"):
        return None
    fit = f.get("value", FITNESS_MAX)
    if fit >= FITNESS_FLOOR_FOR_INJURY and career["workload"].get("matches_since_rest", 0) < 6:
        return None

    deficit = max(0.0, (FITNESS_FLOOR_FOR_INJURY - fit) / FITNESS_FLOOR_FOR_INJURY)
    fatigue_matches = max(0, career["workload"].get("matches_since_rest", 0) - 5)
    p = deficit * 0.18 + fatigue_matches * 0.02
    if balls_bowled >= 18:
        p += 0.03
    if random.random() >= min(0.35, p):
        return None

    names = [t[0] for t in INJURY_TIERS]
    weights = [t[3] for t in INJURY_TIERS]
    pick = random.choices(names, weights=weights)[0]
    tier = next(t for t in INJURY_TIERS if t[0] == pick)
    f["injury"] = {"type": tier[0], "matches_left": tier[1], "ceiling": tier[2],
                   "since": int(time.time())}
    f["value"] = min(f["value"], tier[2] - 10)
    return f["injury"]


# The one place condition reaches the simulation
def rating_modifier(career):
    """Bounded rating delta from form and fitness, in engine rating points.

    Applied in career_manager.career_to_engine, i.e. when the career becomes an
    engine player - so the engine itself never learns that condition exists.
    """
    ensure(career)
    f = form(career)
    fit = fitness(career)
    # form 0..100 -> -1..+1, fitness only ever hurts (100 is neutral)
    form_part = (f - FORM_BASE) / FORM_BASE
    fit_part = min(0.0, (fit - 85) / 85.0)
    raw = form_part * 0.7 + fit_part * 1.0
    delta = max(-MAX_RATING_SWING, min(MAX_RATING_SWING, raw * MAX_RATING_SWING))
    if is_injured(career):
        delta = min(delta, -1.0)
    return round(delta, 2)


def summary(career):
    """Compact dict for the cards and the `cv fitness` embed."""
    ensure(career)
    return {
        "form": form(career),
        "form_arrow": form_arrow(career),
        "fitness": fitness(career),
        "injury": injury_label(career),
        "recent": list(career["form"].get("recent", []))[-FORM_WINDOW:],
        "matches_since_rest": career["workload"].get("matches_since_rest", 0),
        "modifier": rating_modifier(career),
    }
