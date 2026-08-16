"""
Derived per-ball geometry and innings aggregates.

The engine records only what it actually decided (see engine/ball_record.py). It
does NOT model where a ball pitched or where a shot went, because nothing in the
simulation depends on that. The broadcast graphics do, so the geometry is derived
HERE, from the recorded facts, and stays out of the engine.

Derivation is deterministic: seeded from the ball's own position and content, so
the same delivery always draws the same way. A replay must not move between the
live card, the GIF and the end-of-innings chart.
"""
import math
import random

# Where a delivery pitches, as a fraction of the way down the pitch toward the
# batter. Full/yorker land late, bouncers land early.
_LENGTH_FRAC = {
    "Yorker": 0.93, "Full": 0.78, "Good": 0.60, "Bouncer": 0.40,
}
# Sideways bias of the delivery, -1 (leg side) .. +1 (off side).
_LINE_BIAS = {
    "Inswing": -0.35, "Outswing": 0.40, "Off Cutter": -0.25, "Leg Cutter": 0.30,
    "Off spin": -0.20, "Doosra": 0.25, "Arm ball": 0.10, "Carrom": 0.20,
    "Leg spin": 0.30, "Googly": -0.25, "Flipper": 0.05, "Slider": 0.15,
    "Top spin": 0.0, "Drifter": 0.20, "Knuckle": 0.0, "Mystery": 0.0,
}
# Where each shot sends the ball, in degrees. 0 = straight down the ground,
# negative = leg side, positive = off side (right-hander's view).
_SHOT_ANGLE = {
    "Drive": 8, "Cover Drive": 45, "Cut": 72, "Late Cut": 100,
    "Pull": -68, "Hook": -95, "Sweep": -55, "Reverse Sweep": 62,
    "Flick": -40, "Loft": 5, "Scoop": -120, "Block": 0, "Defensive": 0,
    "Leave": 0, "Slog": -25, "Reverse": 62,
}


def _seed(rec):
    return random.Random(
        f"{rec.get('innings')}:{rec.get('ball_index')}:{rec.get('delivery')}:{rec.get('shot')}"
    )


def geometry(rec):
    """Presentation geometry for one delivery.

    line       -1..1  sideways position at the batter (0 = at the stumps)
    pitch_frac 0..1   how far down the pitch it lands
    bounce     0..1   how steeply it climbs after pitching
    angle      deg    direction the ball leaves the bat (None if it didn't)
    carry      0..1   how far it travels
    """
    rng = _seed(rec)
    deliv = str(rec.get("delivery") or "")

    frac = 0.62
    for token, v in _LENGTH_FRAC.items():
        if token in deliv:
            frac = v
            break
    frac = min(0.97, max(0.30, frac + rng.uniform(-0.05, 0.05)))

    bias = 0.0
    for token, v in _LINE_BIAS.items():
        if token in deliv:
            bias = v
            break
    if rec.get("is_wide"):
        bias = (0.85 if bias >= 0 else -0.85) + rng.uniform(-0.08, 0.08)
    line = max(-1.0, min(1.0, bias + rng.uniform(-0.18, 0.18)))

    bounce = 0.75 if "Bouncer" in deliv else (0.15 if "Yorker" in deliv else 0.42)

    angle = None
    carry = 0.0
    shot = rec.get("shot")
    runs = rec.get("runs_off_bat", 0)
    if shot and shot not in ("Leave",) and not rec.get("is_wide"):
        # Wide jitter on purpose: the engine only picks from ~10 shot names, so a
        # tight spread draws every innings as three straight lines instead of a wheel.
        angle = _SHOT_ANGLE.get(shot, 0) + rng.uniform(-24, 24)
        carry = {0: 0.18, 1: 0.42, 2: 0.58, 3: 0.72, 4: 0.95, 6: 1.0}.get(runs, 0.35)
        if rec.get("dismissal") == "Caught":
            carry = rng.uniform(0.45, 0.75)
    return {"line": line, "pitch_frac": frac, "bounce": bounce,
            "angle": angle, "carry": carry, "is_six": runs == 6}


def wicket_zone(rec):
    """For DRS: does the ball pitch in line, hit in line, and go on to hit?

    Returns three 0..1 confidences. This is presentation-side detail derived from
    the delivery, NOT a second dismissal model - the engine already decided the
    batter was out; this only explains the decision visually and lets an
    umpire's-call band exist.
    """
    g = geometry(rec)
    rng = _seed(rec)
    line = abs(g["line"])
    pitching = max(0.0, 1.0 - max(0.0, line - 0.25) / 0.75)
    impact = max(0.0, 1.0 - max(0.0, line - 0.15) / 0.85)
    # A yorker-length ball that beats the bat is far more likely to be crashing in.
    hitting = max(0.0, min(1.0, (g["pitch_frac"] - 0.35) * 1.5 + rng.uniform(-0.15, 0.15)))
    if "Bouncer" in str(rec.get("delivery") or ""):
        hitting *= 0.3
    return {"pitching": pitching, "impact": impact, "hitting": hitting}


def over_runs(history):
    """[(over_number, runs, wickets)] for the Manhattan chart."""
    out = {}
    for r in history:
        o = r.get("over", 0)
        runs, wkts = out.get(o, (0, 0))
        out[o] = (runs + r.get("runs_off_bat", 0) + r.get("extras", 0),
                  wkts + (1 if r.get("dismissal") else 0))
    return [(o, v[0], v[1]) for o, v in sorted(out.items())]


def scoring_shots(history, batter):
    """Wagon-wheel points for one batter: (angle_deg, carry, runs)."""
    pts = []
    for r in history:
        if r.get("striker") != batter or r.get("is_wide"):
            continue
        g = geometry(r)
        if g["angle"] is None:
            continue
        pts.append((g["angle"], g["carry"], r.get("runs_off_bat", 0)))
    return pts


def milestone(rec, prev_runs):
    """'fifty' / 'hundred' when this ball took the striker through the mark."""
    now = rec.get("batter_runs")
    if now is None:
        return None
    for mark, name in ((100, "hundred"), (50, "fifty")):
        if prev_runs < mark <= now:
            return name
    return None


def is_highlight(rec):
    """Should this ball get an animated replay? Kept in one place so the live loop
    and the highlights command agree. Wickets, sixes and fours - the three things
    players actually want to see again."""
    if rec.get("dismissal"):
        return True
    if rec.get("runs_off_bat") in (4, 6):
        return True
    return False


def polar(angle_deg, dist):
    """Wagon-wheel angle to (dx, dy); 0deg is straight down the ground (up-screen)."""
    a = math.radians(angle_deg)
    return math.sin(a) * dist, -math.cos(a) * dist
