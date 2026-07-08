import random
import math

# ──────────────────────────────────────────────────────────────────────────
# CALIBRATION CONSTANTS (tuned via Monte Carlo — see sim_harness.py)
# Neutral 85v85 target: par ~285, ~7 wkts, ~50/50. Big rating gaps separate
# teams decisively (≤1% upset at 14pt gap, ~0.1% at 24pt gap).
# ──────────────────────────────────────────────────────────────────────────
ODI_SKILL_SCALE = 12.8
# ── DSL LEAGUE-REALISM MODE (matches with tournament_type == "dsl" ONLY) ──────
# Mirror of the T20 engine's DSL mode, for the ODI-format Dominators Super League:
# stars keep natural innings-to-innings variance (the consistency shield is off)
# and rating gaps become realistic odds instead of certainties (flatter skill
# curve). Upsets breathe; ratings still decide the season table. Non-DSL matches
# are completely unaffected.
DSL_ODI_SKILL_SCALE = 24.5
# Flat wicket trim for DSL: removing the cons shield raises dismissal rates a
# touch; this rating-independent trim restores the scoring environment.
DSL_ODI_WKT_TRIM = 0.86
# ── CHASE BALANCE ─────────────────────────────────────────────────────────────
# Innings 2 inherits innings 1's pitch wear (a real 100-over feature worth keeping),
# but unbalanced it made batting first win ~55-58% — a toss-decided format. Real
# ODI chases win ~50% because knowing the target offsets the older surface. Two
# proportional counterweights (both teams get them when chasing, so strong-vs-weak
# is untouched, and low-wear roads are barely touched while crumbling decks get real help):
ODI_WEAR_CARRY     = 0.65   # fraction of innings-1 wear the chase inherits (was 1.0)
ODI_CHASE_RELIEF_K = 0.062  # innings-2 wicket relief per point of wear susceptibility
# Tuned low vs the old (dot=50, wkt=3) baseline, which never bowled teams out
# and produced 370-run innings. Higher wicket base lets innings actually end.
# ODI is a singles/strike-rotation game: ~46% dot, ~37% single, only ~10-11%
# boundary (far less than T20). Boundaries still bring ~55% of runs, but the
# innings is built on rotation, not the rope.
# (Dot/boundary bases retuned 2026-07 with the no-ball fix: ~14 no-balls+free-hits
#  per innings were quietly worth ~25 runs of par — that scoring now comes from
#  legitimate boundaries and strike rotation instead.)
ODI_BASE_DOT   = 52.6; ODI_DOT_SENS = 52.0
ODI_BASE_SINGLE = 47.0
ODI_BASE_BND   = 11.4; ODI_BND_SENS = 18.0
ODI_BASE_WKT   = 4.0;  ODI_WKT_SENS = 8.5
# Pitch, weather, ball-age and phase each scale the wicket rate. Over 300 balls
# their PRODUCT bowls sides out ~100% of the time on bowling-friendly decks.
# After those environmental multipliers we pull the combined inflation partway
# back toward baseline so even green/overcast/cracked tracks let teams bat deep.
ODI_WKT_COMPRESS = 0.34
# Batting-paradise floor (see T20 note): a road/dead deck caps how cheaply a side
# folds, lifting the low tail without changing the boundary-driven mean.
ODI_BAT_PITCH_WKT_CAP = 7.0

# ── 2.0: PITCH DETERIORATION ──
# How fast each surface wears over 100 overs. Dust bowls / worn / cracked decks
# roughen fast (spin lethal by the back half); roads & dead decks barely change.
WEAR_SUSCEPT = {
    "Dusty": 1.5, "Worn": 1.5, "Turning": 1.4, "Cracked": 1.4, "Dry": 1.3,
    "Slow": 1.2, "Two-Paced": 1.1, "Sticky": 0.9, "Soft": 0.8, "Hard": 0.7,
    "Green": 0.6, "Damp": 0.7, "Bouncy": 0.7, "Flat": 0.4, "Dead": 0.3,
}
# Run-out share of all dismissals (slightly higher than T20 — more running).
ODI_RUNOUT_SHARE = 0.075

# ── CRUISE CONTROL (chase realism) ────────────────────────────────────────────
# Audit finding: successful ODI chases finished 8-11 OVERS early (65 balls to
# spare on Hard) because the chasing side batted at full first-innings aggression
# regardless of a modest ask. Real ODI chases are knocked off calmly, ~46-48 ov
# (15-25 balls left). When comfortably AHEAD of the required rate, the batters
# rotate strike instead of blasting: boundaries down, ones/twos up, and — since
# they take no risks — fewer wickets, so a cruise doesn't tip into a collapse.
# Off in the last 5 overs (finish it) and whenever the ask is live.
ODI_CRUISE_K       = 0.34   # damp strength per run/over of cushion (crr − rrr)
ODI_CRUISE_MAX     = 2.6    # cap the cushion so a tiny chase doesn't freeze
ODI_CRUISE_RRR_MAX = 7.0    # only cruise when the ask itself is below this rpo

# ── TAIL STRIKE MANAGEMENT (farming/shielding) ────────────────────────────────
# Ported from the T20 engine — matters MORE over 50 overs (tails face 55-90 balls
# a match). The TAIL hunts a single to hand the recognised batter strike (but not
# off the over's last ball — a dot there keeps his partner on strike via the
# end-change), while the BATTER shields him: declines early-over singles, cashes
# boundaries, and takes one off the last ball to keep strike. Weight nudges, not
# scripts. DISABLED in the clutch (last 18 balls / ≤15 needed) — the run is always
# taken then no matter who's at the other end.
ODI_TAIL_BAT_MAX       = 60
ODI_SHIELD_SINGLE      = 1.45   # tail on strike, balls 1-5: hunt the single
ODI_SHIELD_BND         = 0.75   #   ...and don't slog
ODI_SHIELD_WKT         = 0.90
ODI_SHIELD_LAST_SINGLE = 0.60   # tail, last ball: DON'T take one (keep partner on strike)
ODI_FARM_SINGLE        = 0.65   # batter with tail behind, balls 1-5: decline the single
ODI_FARM_BND           = 1.12
ODI_FARM_LAST_SINGLE   = 1.60   # batter, last ball: take ONE to keep strike
ODI_STRIKE_MGMT_BALLS  = 18     # chase gate: off when ≤ this many balls left
ODI_STRIKE_MGMT_RUNS   = 15     # chase gate: off when ≤ this many runs needed

# ── BOWLER-TYPE STRIKE IDENTITY ── {pitch: (favoured, fav_mult, other_mult)}
# On a turner the spinner out-strikes; on a green top pace does. Paired so the
# pitch's total wicket rate stays neutral (other_mult tuned for spin bowling ~44%
# of the balls / pace ~56%). Ported from the T20 engine.
ODI_TYPE_STRIKE = {
    "Turning": ("Spin", 1.20, 0.85), "Dusty": ("Spin", 1.16, 0.88),
    "Worn":    ("Spin", 1.13, 0.90), "Dry":  ("Spin", 1.09, 0.93), "Slow": ("Spin", 1.10, 0.92),
    "Green":   ("Pace", 1.16, 0.79), "Damp": ("Pace", 1.13, 0.83),
    "Bouncy":  ("Pace", 1.11, 0.86), "Hard": ("Pace", 1.06, 0.93),
}


def _odi_is_tail(p):
    """Genuine tailender: a pure Bowler-role player (all-rounders excluded) batting
    at or below ODI_TAIL_BAT_MAX."""
    role = p.get("role", "")
    return "Bowler" in role and "All-Rounder" not in role and float(p.get("bat", 50)) <= ODI_TAIL_BAT_MAX
# Overstepping no-ball chance per delivery. Real ODIs see well under 1 no-ball an
# innings; the old 1% + the AI bowling into the 2nd-bouncer rule produced ~14 per
# innings (≈35 runs of hidden par inflation, free-hit spree included).
ODI_NOBALL_RATE = 0.003

# ── RATING-SCALED CONSISTENCY (all matches) ──────────────────────────────────
# Same intent as the T20 engine: cut a HIGH-rated player's match-to-match swing so
# their season aggregate is reliable, while LOW-rated players (cons=0) keep their
# full variance and upsets survive. ODI ignores form_factor, so only the wicket-
# timing levers apply. Thresholds are ODI-scaled (longer to get set, higher
# milestone). cons(r): 0 ≤ LOW (no change) → 1 ≥ HIGH (max steadiness).
ODI_CONS_LOW  = 68.0
ODI_CONS_HIGH = 88.0
ODI_CONS_SET_BALLS    = 16     # protected "getting set" window (balls)
ODI_CONS_EARLY_PROTECT = 0.42  # gentler than T20 — ODI innings are long, so a little goes far
ODI_CONS_EARLY_BND_DAMP = 0.34  # protected stars score watchfully early → keeps par flat
ODI_CONS_BIG_SCORE    = 34      # past this, wicket risk ESCALATES with the score so a star
ODI_CONS_LATE_SLOPE   = 0.020   #   gets out near his expected total → par flat, spread tighter

def odi_cons(rating: float) -> float:
    if rating <= ODI_CONS_LOW:
        return 0.0
    if rating >= ODI_CONS_HIGH:
        return 1.0
    return (rating - ODI_CONS_LOW) / (ODI_CONS_HIGH - ODI_CONS_LOW)

SPIN_SHOT_MATRIX = {
    "Off spin": ["Sweep", "Drive", "Flick"],
    "Carrom": ["Cut", "Drive", "Loft"],
    "Arm ball": ["Loft", "Drive", "Block"],
    "Doosra": ["Cut", "Sweep", "Drive"],
    "Top spin": ["Cut", "Drive", "Pull"],
    "Leg spin": ["Cut", "Drive", "Loft", "Sweep"],
    "Googly": ["Pull", "Drive", "Sweep"],
    "Flipper": ["Drive", "Flick", "Block"],
    "Drifter": ["Loft", "Drive", "Cut"],
    "Slider": ["Flick", "Drive", "Sweep"],
    "Mystery": ["Block", "Sweep", "Drive"] 
}

def get_smart_ai_shot_odi(deliv, innings, is_death_overs, archetype, pressure_multiplier=1.0):
    total_balls = innings.total_balls
    is_powerplay = total_balls < 60
    is_middle = 60 <= total_balls < 240
    is_collapse = ((innings.wickets >= 3 and total_balls < 120) or (innings.wickets >= 5 and total_balls < 240)) and innings.partnership_runs < 40

    # High pressure forces aggressive mindset (RRR > ~7-8 depending on phase)
    force_aggression = pressure_multiplier > 1.2 or is_death_overs
        
    if is_collapse and not force_aggression:
        if "Yorker" in deliv: return random.choices(["Block", "Defensive", "Drive"], weights=[40, 30, 30], k=1)[0]
        elif "Bouncer" in deliv: return random.choices(["Leave", "Block", "Pull"], weights=[40, 40, 20], k=1)[0]
        else: return random.choices(["Block", "Defensive", "Drive", "Flick"], weights=[30, 30, 25, 15], k=1)[0]

    if force_aggression:
        if "Yorker" in deliv:
            return random.choices(["Drive", "Block", "Flick", "Scoop", "Pull"], weights=[40, 10, 20, 20, 10], k=1)[0]
        elif "Bouncer" in deliv:
            return random.choices(["Pull", "Cut", "Loft", "Drive"], weights=[40, 30, 20, 10], k=1)[0]
        elif "Full" in deliv:
            return random.choices(["Loft", "Drive", "Sweep", "Scoop"], weights=[40, 30, 15, 15], k=1)[0]
        elif deliv in SPIN_SHOT_MATRIX:
            if random.random() < 0.8: return random.choice(SPIN_SHOT_MATRIX[deliv])
            return random.choices(["Loft", "Sweep", "Drive"], weights=[40, 40, 20], k=1)[0]
        else:
            return random.choices(["Loft", "Pull", "Drive", "Scoop"], weights=[30, 30, 25, 15], k=1)[0]
            
    # Standard Match Phase AI
    if "Yorker" in deliv:
        return random.choices(["Block", "Defensive", "Drive", "Flick", "Cut"], weights=[35, 25, 20, 10, 10], k=1)[0]
    elif "Bouncer" in deliv:
        return random.choices(["Leave", "Block", "Pull", "Cut", "Drive"], weights=[25, 25, 25, 15, 10], k=1)[0]
    elif "Full Toss" in deliv or "Full" in deliv:
        return random.choices(["Drive", "Flick", "Loft", "Block", "Leave"], weights=[45, 25, 15, 10, 5], k=1)[0]
    elif deliv in SPIN_SHOT_MATRIX:
        if random.random() < 0.65:
            return random.choice(SPIN_SHOT_MATRIX[deliv])
        return random.choices(["Drive", "Sweep", "Cut", "Block", "Leave"], weights=[30, 20, 20, 20, 10], k=1)[0]
    else:
        return random.choices(["Drive", "Cut", "Flick", "Block", "Loft"], weights=[30, 25, 25, 15, 5], k=1)[0]

def get_smart_ai_bowler_odi(innings, pitch, weather="Clear", format_overs=50):
    bowler_quota = max(1, (format_overs + 4) // 5)
    current_over = innings.total_balls // 6
    overs_remaining = format_overs - current_over

    def _live(p):   return not getattr(innings.bowling_stats.get(p["name"]), "is_subbed_out", False)
    def _quota(p):  return innings.bowling_stats[p["name"]].balls_bowled // 6 < bowler_quota
    def _nc(p):     return not innings.current_bowler or innings.current_bowler["name"] != p["name"]
    def _main(p):   return "Bowler" in p["role"] or "All-Rounder" in p["role"]

    all_live = [p for p in innings.bowling_team["players"] if _live(p)]
    mains    = [p for p in all_live if _main(p)]

    def _rem(p): return bowler_quota - innings.bowling_stats[p["name"]].balls_bowled // 6

    # ── Smart quota management (prevents the back-to-back corner) ─────────────────────
    # A main bowler is eligible only if, AFTER they bowl THIS over, the remaining overs can
    # still be covered by the main attack with NO two-in-a-row (a no-consecutive schedule of
    # n overs exists iff Σ min(remᵢ, ⌈n/2⌉) ≥ n). Stops the AI front-loading bowlers into a
    # corner where one man is forced into consecutive death overs.
    def _feasible_if(cand):
        n = overs_remaining - 1
        if n <= 0:
            return True
        half = (n + 1) // 2
        tot = 0
        for p in mains:
            r = _rem(p) - (1 if p is cand else 0)
            if r > 0:
                tot += min(r, half)
        return tot >= n

    safe = [p for p in mains if _quota(p) and _feasible_if(p)]

    pool = [p for p in safe if _nc(p)]
    # Tight attack (little spare quota): drain the fullest-quota bowler first so depletion
    # stays balanced and the weakest bowler is never stranded into back-to-back overs.
    if pool:
        slack = sum(_rem(p) for p in mains if _quota(p)) - overs_remaining
        if slack <= 1 and overs_remaining > 1:
            mx = max(_rem(p) for p in pool)
            pool = [p for p in pool if _rem(p) == mx]
    if not pool: pool = safe
    if not pool: pool = [p for p in mains if _quota(p) and _nc(p)]
    if not pool: pool = [p for p in mains if _quota(p)]
    if not pool: pool = [p for p in all_live if _quota(p) and _nc(p)]   # part-timers
    if not pool: pool = [p for p in all_live if _quota(p)]
    if not pool: pool = [p for p in all_live if _nc(p)] or all_live     # absolute last resort
    if not pool:
        return None

    valid_bowlers = pool

    weights = []
    for p in valid_bowlers:
        stats = innings.bowling_stats[p["name"]]
        overs_bowled = stats.balls_bowled // 6
        overs_left   = bowler_quota - overs_bowled

        is_frontline = "Bowler" in p["role"] or float(p["bowl"]) >= 80
        base_score = (float(p["bowl"]) / 10.0) ** 2.0
        base_score *= (3.0 if is_frontline else 0.1)

        # ── Urgency boost ──
        if overs_remaining > 0 and overs_left > 0:
            urgency = overs_left / max(1, overs_remaining)
            if urgency >= 1.0:   base_score *= 6.0
            elif urgency >= 0.6: base_score *= 2.5

        # ODI Pitch Adjustments
        if pitch == "Dusty" and "Spin" in p["role"]:                        base_score *= 1.5
        elif pitch == "Dry" and "Spin" in p["role"] and current_over >= 25: base_score *= 1.4
        elif pitch == "Green" and "Pace" in p["role"]:                      base_score *= 1.5
        elif pitch == "Hard" and "Pace" in p["role"] and current_over < 10: base_score *= 1.4
        elif pitch == "Cracked":                                             base_score *= 1.3
        elif pitch == "Damp" and "Pace" in p["role"] and current_over < 15: base_score *= 1.6
        elif pitch == "Worn" and "Spin" in p["role"] and current_over >= 25: base_score *= 1.5
        elif pitch == "Dead":                                                base_score *= 0.8
        elif pitch == "Turning" and "Spin" in p["role"]:                    base_score *= 2.0
        elif pitch == "Slow" and "Spin" in p["role"]:                       base_score *= 1.4
        elif pitch == "Bouncy" and "Pace" in p["role"]:                     base_score *= 1.5
        elif pitch == "Sticky":                                              base_score *= 1.5

        # Weather Adjustments
        if weather == "Cloudy" and "Pace" in p["role"] and current_over < 10:  base_score *= 1.1
        elif weather == "Overcast":
            if "Pace" in p["role"]: base_score *= 1.4
            elif "Spin" in p["role"]: base_score *= 0.7
        elif weather == "Humid" and "Pace" in p["role"]:                    base_score *= 1.2
        elif weather == "Dry Heat":
            if "Spin" in p["role"] and current_over >= 25:                  base_score *= 1.3
            elif "Pace" in p["role"] and current_over >= 25:                base_score *= 0.7
        elif weather == "Windy" and "Pace" in p["role"]:                    base_score *= 1.3
        elif weather in ["Light Rain", "Drizzle"]:
            base_score *= (0.6 if "Spin" in p["role"] else 0.9)
        elif weather in ["Heavy Rain", "Thunderstorm"]:
            base_score *= (0.4 if "Spin" in p["role"] else 0.7)

        # ODI Phase Adjustments
        if current_over < 10:
            if "Pace" in p["role"]: base_score *= 2.0
            if "Spin" in p["role"]: base_score *= 0.1
        elif current_over < 38:
            if "Spin" in p["role"]:  base_score *= 2.5
            if "Pace" in p["role"]:  base_score *= 0.5
        else:
            if "Pace" in p["role"]:  base_score *= 2.5
            if "Spin" in p["role"]:  base_score *= 0.3

        # Death specialist (over 38+, same 8x as T20 proportionally)
        if current_over >= 38 and float(p["bowl"]) >= 88 and "Pace" in p["role"] and overs_left > 0:
            base_score *= 8.0

        # Light saving penalty: Finisher pace before over 20, only if overs to spare
        if current_over < 20 and p["archetype"] == "Finisher" and "Pace" in p["role"]:
            if overs_left >= 5 and overs_remaining >= 30:
                base_score *= 0.35

        # Economy factor
        if overs_bowled > 0:
            eco = (stats.runs_conceded / max(1, stats.balls_bowled)) * 6
            if eco <= 5.0:  base_score *= 2.0
            elif eco > 8.0: base_score *= 0.3

        weights.append(max(1.0, base_score))

    return random.choices(valid_bowlers, weights=weights, k=1)[0]

def execute_ball_math_odi(match):
    innings = match.current_innings
    striker = innings.batting_team["players"][innings.current_striker_idx]
    bowler = innings.current_bowler

    b_stats = innings.batting_stats[striker["name"]]
    bow_stats = innings.bowling_stats[bowler["name"]]

    # No random match-start form variance in ODI (form_factor intentionally ignored).
    bat_rating = striker["bat"]
    bowl_rating = bowler["bowl"]

    # Rating-scaled consistency applies to every match (high-rated steadier, low-rated
    # full variance) EXCEPT the DSL league: there the star shield is off and the skill
    # curve is flatter, so stars can fail like humans — see DSL_ODI_SKILL_SCALE note.
    _is_dsl = getattr(match, "tournament_type", None) == "dsl"
    _cons_bat = 0.0 if _is_dsl else odi_cons(striker["bat"])

    # ── 2.0 PITCH DETERIORATION ──
    # Surface roughens across the match (innings 2 inherits innings 1's wear),
    # scaled by the pitch's susceptibility so roads stay roads. Worn decks give
    # spin extra turn — a defining feature of a 50-over surface by the back half.
    _balls_in = innings.total_balls + (match.max_balls * ODI_WEAR_CARRY if match.current_innings_num == 2 else 0)
    wear = (_balls_in / (2 * match.max_balls)) * WEAR_SUSCEPT.get(match.pitch, 1.0)
    if "Spin" in bowler["role"]:
        bowl_rating += wear * 5.0

    if match.pitch == "Flat":
        bat_rating += 5
    elif match.pitch == "Green" and "Pace" in bowler["role"]:
        bowl_rating += 5
    elif match.pitch == "Dry" and "Spin" in bowler["role"] and innings.total_balls > 150:
        bowl_rating += 5
    elif match.pitch == "Dusty":
        if "Spin" in bowler["role"]: bowl_rating += 3
        bat_rating -= 2
    elif match.pitch == "Hard":
        if "Pace" in bowler["role"] and innings.total_balls < 60: bowl_rating += 4
        bat_rating += 3
    elif match.pitch == "Soft":
        bat_rating -= 3
    elif match.pitch == "Cracked":
        bowl_rating += 3
        bat_rating -= 2
    elif match.pitch == "Damp":
        if "Pace" in bowler["role"] and innings.total_balls < 90: bowl_rating += 4
        if innings.total_balls < 90: bat_rating -= 3
    elif match.pitch == "Dead":
        bat_rating += 3
        bowl_rating -= 3
    elif match.pitch == "Worn":
        if "Spin" in bowler["role"] and innings.total_balls > 150: bowl_rating += 5
        if innings.total_balls > 150: bat_rating -= 2
    elif match.pitch == "Turning":
        if "Spin" in bowler["role"]: bowl_rating += 5
        bat_rating -= 2
    elif match.pitch == "Two-Paced":
        bat_rating -= 2
    elif match.pitch == "Slow":
        if "Spin" in bowler["role"]: bowl_rating += 4
        bat_rating -= 2
    elif match.pitch == "Bouncy":
        if "Pace" in bowler["role"]: bowl_rating += 5
        bat_rating -= 1
    elif match.pitch == "Sticky":
        bowl_rating += 5
        bat_rating -= 2
        
    # Weather Mechanics — new-ball conditions scale with innings.total_balls so the
    # advantage applies to the START of BOTH innings equally (not just innings 1).
    _new_ball = innings.total_balls < 90   # first 15 overs of whichever innings
    _mid_ball = innings.total_balls < 180  # overs 15-30
    if match.weather == "Clear":
        bat_rating += 3
    elif match.weather == "Cloudy" and "Pace" in bowler["role"]:
        bowl_rating += (4 if _new_ball else 1 if _mid_ball else 0)
    elif match.weather == "Overcast":
        if "Pace" in bowler["role"]: bowl_rating += (5 if _new_ball else 2 if _mid_ball else 1)
        bat_rating -= (2 if _new_ball else 1 if _mid_ball else 0)
    elif match.weather == "Humid" and "Pace" in bowler["role"]:
        bowl_rating += (5 if _new_ball else 2 if _mid_ball else 0)
    elif match.weather == "Dry Heat":
        if "Pace" in bowler["role"]: bowl_rating -= 5
        elif "Spin" in bowler["role"] and innings.total_balls > 150: bowl_rating += 5
    elif match.weather == "Windy":
        if "Pace" in bowler["role"]: bowl_rating += (5 if _new_ball else 3 if _mid_ball else 1)
    elif match.weather in ["Light Rain", "Drizzle"]:
        bowl_rating -= 3
        bat_rating += 1
    elif match.weather in ["Heavy Rain", "Thunderstorm"]:
        bowl_rating -= 2
        bat_rating += 1

    # ODI Batter form progression (Realistic pacing & late fatigue prevents 180+ spam)
    if b_stats.balls_faced < 10:
        bat_rating -= 3
    elif 10 <= b_stats.balls_faced < 30:
        bat_rating -= 1
    elif 30 <= b_stats.balls_faced <= 80:
        bat_rating += 4
    elif 80 < b_stats.balls_faced <= 120:
        bat_rating += 7
    elif b_stats.balls_faced > 120:
        bat_rating -= 2
        
    # ODI Bowler fatigue
    if bow_stats.balls_bowled >= 42 and "Pace" in bowler["role"]:
        bowl_rating -= 3
    elif bow_stats.balls_bowled >= 54:
        bowl_rating -= 5
        
    total_balls = innings.total_balls
    is_powerplay = total_balls < 60
    is_middle = 60 <= total_balls < 240
    is_death_overs = total_balls >= 240
    
    is_collapse = ((innings.wickets >= 3 and total_balls < 120) or (innings.wickets >= 5 and total_balls < 240)) and innings.partnership_runs < 40
    is_set_partnership = innings.partnership_runs >= 50
    has_wickets_in_hand = innings.total_balls >= 240 and innings.wickets <= 4

    pressure_multiplier = 1.0
    runs_needed = 0
    balls_left = match.max_balls - total_balls
    if match.current_innings_num == 2 and not is_collapse:
        target = getattr(match, "target", match.innings1.total_runs + 1)
        runs_needed = target - innings.total_runs
        if balls_left > 0:
            rrr = (runs_needed / balls_left) * 6
            
            # Smart Chasing Phase: Teams delay heavy panic until the later overs
            if total_balls < 120:  # Overs 1-20
                threshold = 8.0
                max_p = 1.20
                scale = 0.05
            elif total_balls < 210:  # Overs 21-35
                threshold = 7.5
                max_p = 1.35
                scale = 0.08
            else:  # Overs 36-50
                threshold = 6.5
                max_p = 1.60
                scale = 0.12
                
            if rrr > threshold:
                pressure_multiplier = min(max_p, 1.0 + ((rrr - threshold) * scale))

    if match.current_delivery_selection:
        deliv = match.current_delivery_selection
    else:
        if "Spin" in bowler["role"]:
            if "Off" in bowler["role"]:
                opts = ["Off spin", "Carrom", "Arm ball", "Doosra", "Top spin", "Mystery"]
            else:
                opts = ["Leg spin", "Googly", "Flipper", "Drifter", "Slider", "Mystery"]
            if getattr(innings, "mystery_bowled_this_over", False):
                opts.remove("Mystery")
            deliv = random.choice(opts)
            if deliv == "Mystery":
                innings.mystery_bowled_this_over = True
        else:
            if random.random() < 0.08:
                deliv = random.choice(["Off Cutter", "Leg Cutter", "Knuckle"])
            else:
                # A real bowler doesn't bowl himself into the 2nd-bouncer no-ball — he
                # stops at the ODI limit. (Humans picking deliveries can still risk it.)
                lengths = ['Bouncer', 'Full', 'Good', 'Yorker']
                if getattr(innings, "bouncers_in_over", 0) >= 1:
                    lengths = ['Full', 'Good', 'Yorker']
                deliv = f"{random.choice(['Inswing', 'Outswing', 'Fast', 'Slow'])} {random.choice(lengths)}"
            
    shot = match.current_shot_selection or get_smart_ai_shot_odi(deliv, innings, is_death_overs, striker["archetype"], pressure_multiplier)
        
    match.current_delivery_selection = None
    match.current_shot_selection = None
    match.temp_variation = None

    # ── Non-linear skill contest (replaces the old linear bat-bowl diff) ──
    # Logistic response: each rating mapped to an exponential curve, then the
    # batter's share of control = bat_eff / (bat_eff + bowl_eff). Equal ratings
    # → 0.5; gaps between elite ratings matter far more than between poor ones.
    _scale = DSL_ODI_SKILL_SCALE if _is_dsl else ODI_SKILL_SCALE
    bat_eff  = math.exp((bat_rating  - 80.0) / _scale)
    bowl_eff = math.exp((bowl_rating - 80.0) / _scale)
    dominance = bat_eff / (bat_eff + bowl_eff)   # 0..1
    edge = dominance - 0.5                          # ~[-0.45, +0.45]
    diff = edge * 100.0  # legacy scale, kept for any downstream heuristics

    free_hit_active = getattr(match, "free_hit", False)
    is_wide = False
    is_no_ball = False
    prefix = getattr(match, "last_commentary_prefix", "")
    match.last_commentary_prefix = ""
    
    if not hasattr(innings, "bouncers_in_over"): innings.bouncers_in_over = 0
    is_bouncer_no_ball = False
    if "Bouncer" in deliv:
        innings.bouncers_in_over += 1
        if innings.bouncers_in_over == 2:  # ODI: 1 bouncer per over, 2nd = no ball
            is_no_ball = True
            is_bouncer_no_ball = True
            prefix += "🚨 **NO BALL!** (Second bouncer of the over — ODI limit is 1)\n"
            # Bouncer no balls do NOT give free hits in ODI (ICC rules)

    # Cutter tracking — Off Cutter / Leg Cutter / Knuckle
    # 1st cutter per over: safe. 2nd+ in same over: 50% chance of wide (grip loss)
    _CUTTERS = ("Off Cutter", "Leg Cutter", "Knuckle")
    is_cutter = deliv in _CUTTERS
    if not hasattr(innings, "cutters_in_over"): innings.cutters_in_over = 0
    if is_cutter:
        innings.cutters_in_over += 1
        if innings.cutters_in_over > 1 and random.random() < 0.50:
            innings.total_runs += 1
            if not hasattr(innings, "extras"): innings.extras = 0
            innings.extras += 1
            bow_stats.runs_conceded += 1
            innings.over_log.append("<:wide:1520143046900191344>")
            nth = {2: "2nd", 3: "3rd"}.get(innings.cutters_in_over, f"{innings.cutters_in_over}th")
            match.last_commentary = (
                prefix +
                f"**{bowler['name']}** bowls a **{deliv}**!\n"
                f"💨 **Wide!** **Bowling multiple cutters in one over reduces grip and control — "
                f"this is the {nth} cutter of the over (50% slip chance per extra cutter).**\n"
                f"💥 **Result:** 1 Extra"
            )
            match.wide_extra_msg = match.last_commentary
            if free_hit_active: match.last_commentary_prefix = "🛡️ *(Free Hit continues)*\n"
            return
    else:
        is_cutter = False

    if not is_no_ball and random.random() < 0.04 and "Yorker" not in deliv and "Slow" not in deliv:
        is_wide = True
        innings.total_runs += 1
        if not hasattr(innings, 'extras'): innings.extras = 0
        innings.extras += 1
        bow_stats.runs_conceded += 1
        innings.over_log.append("<:wide:1520143046900191344>")
        match.last_commentary = prefix + f"**{bowler['name']}** bowled a **Wide!**\n💥 **Result:** 1 Extra Run"
        if free_hit_active: match.last_commentary_prefix = "🛡️ *(Free Hit continues)*\n"
        return

    if not is_no_ball and random.random() < ODI_NOBALL_RATE:
        is_no_ball = True
        if not free_hit_active:
            prefix += "🚨 **NO BALL!** Overstepping!\n➡️ **NEXT BALL IS A FREE HIT!**\n"
        else:
            prefix += "🚨 **NO BALL!** (Still a Free Hit)\n"

    if is_no_ball:
        if not hasattr(innings, 'extras'): innings.extras = 0
        innings.extras += 1
        innings.total_runs += 1
        bow_stats.runs_conceded += 1
        if not is_bouncer_no_ball:  # Only front-foot no balls give free hits in ODI
            match.free_hit = True

    # Baseline ODI Weights - High discipline, lower boundary frequency
    dot_weight      = max(14.0, ODI_BASE_DOT  - edge * ODI_DOT_SENS)
    single_weight   = ODI_BASE_SINGLE
    boundary_weight = max(1.0,  ODI_BASE_BND  + edge * ODI_BND_SENS)
    wicket_weight   = max(0.6,  ODI_BASE_WKT  - edge * ODI_WKT_SENS)
    if _is_dsl:
        wicket_weight *= DSL_ODI_WKT_TRIM   # par-restore for league-realism mode (see constant)
    if match.current_innings_num == 2:
        # toss-neutrality counterweight, proportional to how much the deck wears (see constants)
        wicket_weight *= max(0.75, 1.0 - ODI_CHASE_RELIEF_K * WEAR_SUSCEPT.get(match.pitch, 1.0))
    
    # Pitch Extreme Modifiers (Balanced)
    if match.pitch == "Green" and "Pace" in bowler["role"]:
        wicket_weight *= 1.15
        boundary_weight *= 0.90
    elif match.pitch == "Dusty" and "Spin" in bowler["role"]:
        wicket_weight *= 1.15
        boundary_weight *= 0.85
        dot_weight *= 1.10
    elif match.pitch == "Dry" and "Spin" in bowler["role"] and total_balls > 150:
        wicket_weight *= 1.10
        dot_weight *= 1.05
    elif match.pitch == "Hard":
        if total_balls < 60 and "Pace" in bowler["role"]:
            wicket_weight *= 1.10
        else:
            boundary_weight *= 1.05
    elif match.pitch == "Soft":
        dot_weight *= 1.20
        boundary_weight *= 0.80
    elif match.pitch == "Cracked":
        wicket_weight *= 1.20
        boundary_weight *= 0.85
    elif match.pitch == "Damp":
        if "Pace" in bowler["role"] and total_balls < 90:
            wicket_weight *= 1.25
            boundary_weight *= 0.80
    elif match.pitch == "Dead":
        boundary_weight *= 1.12
        wicket_weight *= 0.88
    elif match.pitch == "Worn":
        if "Spin" in bowler["role"] and total_balls > 150:
            wicket_weight *= 1.20
            dot_weight *= 1.10
            boundary_weight *= 0.85
    elif match.pitch == "Turning":
        if "Spin" in bowler["role"]:
            wicket_weight *= 1.25
            boundary_weight *= 0.75
            dot_weight *= 1.15
    elif match.pitch == "Two-Paced":
        dot_weight *= 1.25
        boundary_weight *= 0.80
        wicket_weight *= 1.10
    elif match.pitch == "Slow":
        dot_weight *= 1.20
        boundary_weight *= 0.75
        if "Spin" in bowler["role"]:
            wicket_weight *= 1.15
    elif match.pitch == "Bouncy":
        if "Pace" in bowler["role"]:
            wicket_weight *= 1.15
    elif match.pitch == "Sticky":
        wicket_weight *= 1.40
        boundary_weight *= 0.65
        dot_weight *= 1.35
    elif match.pitch == "Flat":
        boundary_weight *= 1.16
        wicket_weight *= 0.92
        dot_weight *= 0.96
        
    # Weather Advanced Modifiers — innings.total_balls used so both innings
    # get the new-ball swing boost, not just innings 1.
    _new_ball = total_balls < 90   # first 15 overs
    _mid_ball = total_balls < 180  # overs 15-30
    if match.weather == "Overcast":
        wicket_weight *= (1.30 if _new_ball else 1.15 if _mid_ball else 1.05)
        boundary_weight *= (0.80 if _new_ball else 0.88 if _mid_ball else 0.95)
    elif match.weather == "Cloudy":
        if _new_ball and "Pace" in bowler["role"]:
            wicket_weight *= 1.12
            boundary_weight *= 0.93
        elif _mid_ball and "Pace" in bowler["role"]:
            wicket_weight *= 1.05
    elif match.weather == "Humid":
        if _new_ball and "Pace" in bowler["role"]:
            wicket_weight *= 1.10
        elif _mid_ball and "Pace" in bowler["role"]:
            wicket_weight *= 1.04
    elif match.weather == "Dry Heat":
        dot_weight *= 1.10
    elif match.weather == "Windy":
        wicket_weight *= (1.20 if _new_ball else 1.10 if _mid_ball else 1.05)
        boundary_weight *= (0.87 if _new_ball else 0.92 if _mid_ball else 0.97)
    elif match.weather in ["Light Rain", "Drizzle"]:
        wicket_weight *= 0.88
        boundary_weight *= 1.04
    elif match.weather in ["Heavy Rain", "Thunderstorm"]:
        wicket_weight *= 0.80
        boundary_weight *= 1.05

    # ── BALL AGE / HARDNESS ──────────────────────────────────────────────
    # Over 50 overs the ball ages enough for all three classic phases to show:
    # a hard new ball that seams & swings (pace threat + flush boundaries), a
    # soft middle where the old ball stops coming on (accumulation, spin grips),
    # and genuine reverse swing in the back 10-15 overs that makes pace lethal
    # again — yorkers and full balls reversing late is the defining ODI weapon.
    _ball_frac = total_balls / max(1, match.max_balls)
    _is_pace_b = "Pace" in bowler["role"]
    _is_spin_b = "Spin" in bowler["role"]
    if _ball_frac < 0.20:            # brand new, hard ball (first ~10 overs)
        if _is_pace_b:
            wicket_weight *= 1.08
            boundary_weight *= 1.04
        elif _is_spin_b:
            boundary_weight *= 1.05
            wicket_weight *= 0.94
    elif _ball_frac < 0.66:          # old, soft ball through the middle
        boundary_weight *= 0.96
        dot_weight *= 1.05
        if _is_spin_b:
            wicket_weight *= 1.08
    else:                            # back third — reverse swing for pace
        if _is_pace_b:
            wicket_weight *= 1.10
            if "Yorker" in deliv or "Full" in deliv:
                wicket_weight *= 1.06   # reversing yorkers are deadly
        boundary_weight *= 1.04

    # ── 2.0 PITCH DETERIORATION (weight effects) — environmental, so it sits
    # before the compressor and gets dampened alongside pitch/weather. ──
    if _is_spin_b:
        wicket_weight *= (1.0 + wear * 0.40)
    boundary_weight *= (1.0 - wear * 0.07)
    dot_weight *= (1.0 + wear * 0.06)

    if is_powerplay:
        if "Pace" in bowler["role"]:
            wicket_weight *= 1.12
        boundary_weight *= 1.15
        dot_weight *= 1.10
    elif is_middle:
        single_weight *= 1.15 # Strike rotation (but batters still find the rope)
        dot_weight *= 0.90

    # ── ENVIRONMENTAL WICKET COMPRESSOR ──────────────────────────────────
    # Everything above (pitch + weather + ball-age + phase) has scaled the
    # wicket rate. Compress that *combined* inflation back toward baseline so
    # bowling decks don't fold sides 100% of the time over 50 overs. Per-ball
    # tactical wicket logic (shot choice, archetypes, collapse) comes AFTER
    # this line and keeps its full effect.
    if wicket_weight > ODI_BASE_WKT:
        wicket_weight = ODI_BASE_WKT + (wicket_weight - ODI_BASE_WKT) * ODI_WKT_COMPRESS

    # ── BOWLER-TYPE STRIKE IDENTITY ── post-compressor so it isn't flattened:
    # on a turner the spinner hunts while the seamer contains (vice versa on a
    # green top). Mild paired multipliers keep the pitch's total wicket rate
    # ~neutral — the SHARE moves, par doesn't. (Ported from the T20 engine.)
    _ts = ODI_TYPE_STRIKE.get(match.pitch)
    if _ts:
        _fav, _fmul, _omul = _ts
        _role = bowler["role"]
        if _fav in _role:
            wicket_weight *= _fmul
        elif "Pace" in _role or "Spin" in _role:
            wicket_weight *= _omul

    # ── 2.0 BATTING MOMENTUM ──
    # New batsman vulnerable until set, set batsman dangerous. Post-compressor
    # because it's about the player, not the conditions. ODI batsmen take longer
    # to "get in" than in T20, so the vulnerable window runs ~10 balls.
    _bf = b_stats.balls_faced
    if _bf < 10:
        wicket_weight *= (1.20 - _bf * 0.018)   # ~1.20 first ball → ~1.04 at 10
        boundary_weight *= (0.80 + _bf * 0.020)
    elif _bf >= 25:
        boundary_weight *= 1.10                  # well set — cashing in

    # ── RATING-SCALED CONSISTENCY (tournament only; high-rated → steadier) ──
    # Protect elite batters through the set phase (fewer freak cheap dismissals)
    # and nudge their risk up once past a big score (fewer freak 150s) → their
    # innings cluster, so tournament aggregates stay reliable. Low-rated: no change.
    if _cons_bat > 0.0:
        if _bf < ODI_CONS_SET_BALLS:
            wicket_weight *= (1.0 - _cons_bat * ODI_CONS_EARLY_PROTECT)
            boundary_weight *= (1.0 - _cons_bat * ODI_CONS_EARLY_BND_DAMP)
        if b_stats.runs_scored > ODI_CONS_BIG_SCORE:
            wicket_weight *= (1.0 + _cons_bat * ODI_CONS_LATE_SLOPE * (b_stats.runs_scored - ODI_CONS_BIG_SCORE))

    bad_shot_selection = False
    perfect_shot_selection = False
    
    # Tactical Spin & Pace UI
    if "Yorker" in deliv:
        if shot in ["Pull", "Cut", "Leave"]: bad_shot_selection = True
        elif shot in ["Defensive", "Drive"]: perfect_shot_selection = True
    elif "Bouncer" in deliv:
        if shot in ["Drive", "Sweep", "Scoop"]: bad_shot_selection = True
        elif shot in ["Pull", "Leave"]: perfect_shot_selection = True
    elif is_cutter:
        if shot in ["Block", "Leave", "Defensive"]: perfect_shot_selection = True
        elif shot in ["Loft", "Scoop"]:             bad_shot_selection = True
    elif "Full Toss" in deliv:
        if shot in ["Defensive", "Leave"]: bad_shot_selection = True
        elif shot in ["Loft", "Drive"]: perfect_shot_selection = True
    elif deliv in SPIN_SHOT_MATRIX:
        if shot in SPIN_SHOT_MATRIX[deliv]: perfect_shot_selection = True
        elif shot == "Leave": bad_shot_selection = True
        else:
            boundary_weight *= 0.50   # spin is most of the ODI middle — batters still pierce the field
            dot_weight *= 1.2
            single_weight *= 1.1

    if shot in ["Block", "Defensive"]:
        dot_weight *= 1.6; single_weight *= 0.9; boundary_weight = 0.1; wicket_weight *= 0.5
    elif shot == "Leave":
        dot_weight *= 3.0; single_weight = 0.0; boundary_weight = 0.0; wicket_weight *= 1.2
    else:
        if bad_shot_selection:
            wicket_weight *= 1.8; boundary_weight *= 0.3; dot_weight *= 1.5
        elif perfect_shot_selection:
            boundary_weight *= 1.3; single_weight *= 1.1; wicket_weight *= 0.8
            
        # Required run rate (chase only) tells set batters when to lift the tempo.
        _rrr_now = (runs_needed / balls_left * 6) if (match.current_innings_num == 2 and balls_left > 0) else 0.0
        _set = b_stats.balls_faced >= 35
        _lift = is_death_overs or _rrr_now >= 7.0   # death overs OR the ask has climbed above par

        if striker["archetype"] == "Aggressor":
            boundary_weight *= 1.15; wicket_weight *= 1.15
        elif striker["archetype"] == "Anchor":
            # ODI anchor: a LONG, slow build — safety is paid for with tempo (more dots,
            # fewer boundaries early) so the run gap to a Standard mirrors the wicket gap.
            # He only truly accelerates once set and the ask climbs (ODI ramps gentler than T20).
            if _set and _lift:
                boundary_weight *= 1.25; wicket_weight *= 1.00   # cuts loose as the RRR rises — finds gaps, stays secure
            elif _set:
                boundary_weight *= 1.10; wicket_weight *= 0.88    # keeps the score ticking, not blocking
            else:
                dot_weight *= 1.14; boundary_weight *= 0.85; wicket_weight *= 0.76   # still playing himself in
        elif striker["archetype"] == "Standard":
            # The middle-ground: a touch quicker than the Anchor but a touch less secure at
            # EVERY stage — the difference shows up in runs AND in risk, not just risk.
            if _set and _lift:
                boundary_weight *= 1.15; wicket_weight *= 1.06
            elif _set:
                boundary_weight *= 1.06; wicket_weight *= 0.96
            else:
                dot_weight *= 1.04; boundary_weight *= 0.95; wicket_weight *= 0.88
        elif striker["archetype"] == "Finisher" and is_death_overs:
            boundary_weight *= 1.25

        if is_collapse: boundary_weight *= 0.7; wicket_weight *= 0.75; single_weight *= 1.2
        if is_set_partnership: wicket_weight *= 0.85
        if has_wickets_in_hand: boundary_weight *= 1.2; wicket_weight *= 1.15; dot_weight *= 0.75

        # ── CRUISE CONTROL ── comfortably ahead of the ask → bat calmly (see constants).
        if (match.current_innings_num == 2 and balls_left > 0 and not is_death_overs
                and total_balls >= 30 and not is_collapse):
            _crr = innings.total_runs / total_balls * 6.0 if total_balls else 0.0
            _rrr = runs_needed / balls_left * 6.0
            if _rrr < _crr and _rrr < ODI_CRUISE_RRR_MAX:
                _damp = min(ODI_CRUISE_MAX, _crr - _rrr) * ODI_CRUISE_K
                boundary_weight *= max(0.42, 1.0 - _damp)
                single_weight   *= (1.0 + _damp * 0.6)
                dot_weight      *= (1.0 + _damp * 0.30)
                wicket_weight   *= max(0.70, 1.0 - _damp * 0.45)
            
        active_multiplier = pressure_multiplier
        if is_death_overs:
            if match.current_innings_num == 1:
                active_multiplier = max(1.35, pressure_multiplier)
            else:
                if balls_left > 0 and (runs_needed / balls_left * 6) > 6.5:
                    active_multiplier = max(1.35, pressure_multiplier)
                    
        if active_multiplier > 1.0:
            boundary_weight *= active_multiplier
            if total_balls < 240:
                wicket_weight *= (1.0 + (active_multiplier - 1.0) * 0.5)
            else:
                wicket_weight *= (1.0 + (active_multiplier - 1.0) * 0.8)
                
        if innings.last_ball_boundary: boundary_weight *= 1.15; wicket_weight *= 1.15

        # ── TAIL STRIKE MANAGEMENT ── (see constants) gated OFF in clutch chases.
        _mgmt_on = True
        if match.current_innings_num == 2 and balls_left > 0:
            if balls_left <= ODI_STRIKE_MGMT_BALLS or runs_needed <= ODI_STRIKE_MGMT_RUNS:
                _mgmt_on = False
        if _mgmt_on:
            try:
                _ns = innings.batting_team["players"][innings.current_non_striker_idx]
                _ns_live = innings.batting_stats[_ns["name"]].dismissal == "not out"
            except Exception:
                _ns_live = False
            if _ns_live:
                _last_ball = (total_balls % 6 == 5)
                _st_tail, _ns_tail = _odi_is_tail(striker), _odi_is_tail(_ns)
                if _st_tail and not _ns_tail:
                    if _last_ball:
                        single_weight *= ODI_SHIELD_LAST_SINGLE
                    else:
                        single_weight *= ODI_SHIELD_SINGLE
                        boundary_weight *= ODI_SHIELD_BND
                        wicket_weight *= ODI_SHIELD_WKT
                elif _ns_tail and not _st_tail:
                    if _last_ball:
                        single_weight *= ODI_FARM_LAST_SINGLE
                    else:
                        single_weight *= ODI_FARM_SINGLE
                        boundary_weight *= ODI_FARM_BND

    if "Mystery" in deliv:
        wicket_weight *= 1.6
        dot_weight *= 1.5
        boundary_weight *= 0.6
        single_weight *= 0.8

    if is_cutter:
        dot_weight *= 1.35; boundary_weight *= 0.65; wicket_weight *= 1.15
        if deliv == "Knuckle": dot_weight *= 1.10; boundary_weight *= 0.85

    four_weight = boundary_weight
    six_weight = boundary_weight * 0.33
    
    # ODI Exploit fixes
    if shot in ["Loft", "Scoop"]:
        four_weight *= 0.6
        six_weight *= 3.0
        if not is_death_overs: 
            wicket_weight *= 3.0 # Highly suicidal in overs 1-40
        else:
            wicket_weight *= 1.5
        dot_weight *= 0.8
    elif shot in ["Block", "Defensive", "Leave"]:
        four_weight = 0.0
        six_weight = 0.0
    elif shot in ["Drive", "Cut", "Pull", "Flick", "Sweep"]:
        four_weight *= 1.2
        six_weight *= 0.4
        
    if "Slow" in deliv and shot in ["Loft", "Pull", "Sweep", "Scoop"]: wicket_weight *= 1.5; six_weight *= 0.5
    elif "Fast" in deliv and shot in ["Scoop", "Sweep", "Pull", "Loft"]: wicket_weight *= 1.5
    elif "Outswing" in deliv and shot in ["Drive", "Cut"]: wicket_weight *= 1.4; four_weight *= 1.2
    elif "Inswing" in deliv and shot in ["Drive", "Flick", "Sweep"]: wicket_weight *= 1.4
    elif is_cutter and shot in ["Drive", "Cut"]: wicket_weight *= 1.25; four_weight *= 0.85
        
    # 🚨 ANTI-OVERCOOK SAFETIES (Prevents stacked conditions from breaking the game)
    four_weight = max(0.5, min(four_weight, 25.0)) # Hard cap to prevent 450+ scores
    six_weight = max(0.1, min(six_weight, 15.0))
    if match.pitch in ("Flat", "Dead"):
        wicket_weight = min(wicket_weight, ODI_BAT_PITCH_WKT_CAP)  # batting-paradise floor
    wicket_weight = max(1.0, min(wicket_weight, 25.0)) # Hard cap to prevent 10/10 scenarios
    dot_weight = max(15.0, min(dot_weight, 120.0))

    weights = [dot_weight, single_weight, single_weight * 0.18, single_weight * 0.04, four_weight, six_weight, wicket_weight]
    outcome = random.choices(["dot", "single", "two", "three", "four", "six", "wicket"], weights=weights)[0]
    
    if is_no_ball and outcome == "wicket":
        outcome = "dot"
        prefix += "*(Wicket denied due to No Ball)*\n"
    if free_hit_active and not is_no_ball and outcome == "wicket":
        outcome = random.choice(["dot", "single", "two"])
        prefix += "🛡️ **FREE HIT!** Batter escapes dismissal!\n"
        
    b_stats.balls_faced += 1
    outcome_text = ""

    if outcome == "wicket":
        innings.wickets += 1
        innings.partnership_runs = 0
        d_types = ["Bowled", "Caught", "LBW"]

        # ── 2.0 DISMISSAL VARIETY ──
        # Run-out (no bowler credit) first; then stumping vs spin and hit-wicket.
        if random.random() < ODI_RUNOUT_SHARE:
            dismissal_type = "Run Out"
            fielders = [p["name"] for p in innings.bowling_team["players"] if p["name"] != bowler["name"]]
            b_stats.dismissal = f"run out ({random.choice(fielders) if fielders else 'Fielder'})"
        else:
            if deliv in SPIN_SHOT_MATRIX and shot in ["Loft", "Sweep", "Drive"] and random.random() < 0.20:
                dismissal_type = "Stumped"
            elif ("Bouncer" in deliv or "Fast" in deliv) and shot == "Pull" and random.random() < 0.05:
                dismissal_type = "Hit Wicket"
            elif "Outswing" in deliv and shot in ["Drive", "Cut"]: dismissal_type = "Caught Behind"
            elif "Inswing" in deliv and shot in ["Drive", "Flick"]: dismissal_type = random.choice(["Bowled", "LBW"])
            elif "Slow" in deliv and shot in ["Loft", "Pull", "Scoop"]: dismissal_type = "Caught"
            elif bad_shot_selection and "Yorker" in deliv: dismissal_type = "Bowled"
            elif bad_shot_selection and "Bouncer" in deliv: dismissal_type = "Caught"
            elif is_cutter and shot in ["Loft", "Scoop"]:  dismissal_type = "Caught"
            elif is_cutter and shot in ["Drive", "Cut"]:   dismissal_type = random.choice(["Caught", "Bowled"])
            elif shot in ["Loft", "Scoop"]: dismissal_type = "Caught"
            else: dismissal_type = random.choices(["Caught", "Bowled", "LBW"], weights=[52, 27, 21])[0]  # caught-heavy like real cricket

            if dismissal_type == "Bowled":
                b_stats.dismissal = f"b. {bowler['name']}"
            elif dismissal_type == "LBW":
                b_stats.dismissal = f"lbw b. {bowler['name']}"
            elif dismissal_type == "Hit Wicket":
                b_stats.dismissal = f"hit wkt b. {bowler['name']}"
            elif dismissal_type in ("Stumped", "Caught Behind"):
                wk = next((p["name"] for p in innings.bowling_team["players"] if "WK" in p["role"]), "Keeper")
                pre = "st." if dismissal_type == "Stumped" else "c."
                b_stats.dismissal = f"{pre} {wk} b. {bowler['name']}"
            else:
                fielders = [p["name"] for p in innings.bowling_team["players"] if p["name"] != bowler["name"]]
                fielder = random.choice(fielders) if fielders else "Fielder"
                b_stats.dismissal = f"c. {fielder} b. {bowler['name']}"

            bow_stats.wickets_taken += 1
        innings.over_log.append("<:wicket:1520143043683156051>")
        match.prev_striker_idx = innings.current_striker_idx
        if dismissal_type in ["LBW", "Caught Behind"] and match.simulation_mode == "interactive":
            match.pending_drs = True
            match.drs_dismissal = dismissal_type
            
        max_wickets = 2 if getattr(match, "is_super_over", False) else 10
        if innings.wickets < max_wickets:
            is_ai_batting = match.is_ai_game and match.get_striker_user_id() == match.p2_id
            if match.simulation_mode == "whole_match" or is_ai_batting:
                innings.current_striker_idx = innings.next_batter_idx
                innings.next_batter_idx += 1
            else:
                match.pending_next_batter = True
                match.out_batter_profile = striker
        outcome_text = f"WICKET! ({dismissal_type.upper()})"
    else:
        runs_map = {"dot": 0, "single": 1, "two": 2, "three": 3, "four": 4, "six": 6}
        runs = runs_map[outcome]
        
        is_bye = False
        if runs == 0 and random.random() < 0.03:
            is_bye = True
            runs = random.choice([1, 2, 4])
            innings.total_runs += runs
            if not hasattr(innings, 'extras'): innings.extras = 0
            innings.extras += runs
            outcome_text = f"{runs} Leg Byes"
            log_entry = f"{runs}LB"
        else:
            innings.total_runs += runs
            innings.partnership_runs += runs
            b_stats.runs_scored += runs
            bow_stats.runs_conceded += runs
            outcome_text = f"{runs} Runs" if runs > 0 else "Dot Ball"
                
            emoji_map = {0: "<:0run:1520141253604544633>", 1: "<:1run:1520143026381656104>", 2: "<:2run:1520143029015548026>", 3: "<:3run:1520143031682990202>", 4: "<:4run:1520143034573131807>", 6: "<:6run:1520143037945090105>"}
            log_entry = emoji_map[runs]
            
        if is_no_ball:
            log_entry = "<:noball:1520143040516325516>" + (log_entry if runs > 0 and not is_bye else "")
            outcome_text += " (NO BALL)"
            
        if runs in [4, 6] and not is_bye:
            innings.last_ball_boundary = True
            if runs == 4:
                b_stats.fours = getattr(b_stats, 'fours', 0) + 1
            elif runs == 6:
                b_stats.sixes = getattr(b_stats, 'sixes', 0) + 1
            
        innings.over_log.append(log_entry)
        if runs in [1, 3]: innings.current_striker_idx, innings.current_non_striker_idx = innings.current_non_striker_idx, innings.current_striker_idx

    if not is_no_ball:
        bow_stats.balls_bowled += 1
        innings.total_balls += 1
        match.free_hit = False
        if innings.total_balls % 6 == 0:
            match.over_completed = True
            # End-of-over strike rotation: the ends switch, so the non-striker takes
            # strike for the next over — UNLESS the last ball was a 1/3 (already swapped
            # mid-ball above, which nets back to the same batter keeping strike).
            if outcome == "wicket" or runs not in [1, 3]:
                innings.current_striker_idx, innings.current_non_striker_idx = innings.current_non_striker_idx, innings.current_striker_idx

    match.last_commentary = prefix + f"**{bowler['name']}** bowled a **{deliv}**\n**{striker['name']}** played: **{shot}**\n💥 **Result:** {outcome_text}"