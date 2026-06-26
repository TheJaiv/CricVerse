import random
import math

# ──────────────────────────────────────────────────────────────────────────
# CALIBRATION CONSTANTS (tuned via Monte Carlo — see sim_harness.py)
# Neutral 85v85 target: par ~165, ~6-7 wkts, ~50/50. Big rating gaps separate
# teams decisively (≤1% upset at 14pt gap, ~0.1% at 24pt gap).
# ──────────────────────────────────────────────────────────────────────────
# Exponential skill scale: a 90→95 jump is worth far more than 75→80, so
# legends dominate and rating gaps translate into a real per-ball edge.
T20_SKILL_SCALE = 15.0
# Base outcome weights at a neutral (edge=0) contest, and how strongly the
# skill edge pushes them. Tuned low so the downstream phase/pitch multipliers
# land scores in a realistic band instead of inflating to 200+.
# Modern T20: aggressive intent, boundary-driven (~18% of balls), big six-hitting,
# little running for twos. Par ~175 on a neutral deck, 200+ on a road.
T20_BASE_DOT   = 30.5; T20_DOT_SENS = 46.0
T20_BASE_SINGLE = 38.0
T20_BASE_BND   = 11.2; T20_BND_SENS = 26.0
T20_BND_COMPRESS = 0.72   # tame boundary clustering (freak 250s); 1.0 = off
T20_BASE_WKT   = 5.2;  T20_WKT_SENS = 11.0
# Variance compressor: pulls per-ball wicket spikes back toward the rating-driven
# baseline so wickets don't CLUSTER into cascades (30-all-out) between equal sides.
# Lower = more consistent / skill-dominant. 1.0 = off.
T20_WKT_COMPRESS = 0.45
# Batting-paradise floor: on a true road / dead deck there's a ceiling on how
# cheaply a side can be bowled out — even swing only does so much on a featherbed.
# Capping wicket_weight here lifts the low tail (no 49 all-out on a road) without
# touching the mean, which is driven by boundaries.
T20_BAT_PITCH_WKT_CAP = 8.5
# Bowling-deck floor: even the nastiest minefield bottoms out — real T20 on a
# raging turner / cracked deck is ~120-140 all out ~35%, NOT 88 all out 58%.
# Caps how lethal a green/dusty/cracked surface can get so scores stay cricketing.
T20_BOWL_PITCH_WKT_CAP = 12.0
T20_BOWL_DECKS = ("Cracked", "Sticky", "Turning", "Worn", "Dusty", "Dry", "Green", "Damp", "Bouncy")

# ── RATING-SCALED CONSISTENCY (all matches) ──────────────────────────────────
# HIGH-rated players sim more consistently game-to-game (a star reliably delivers
# across a season), while LOW-rated players keep their full variance (still erratic
# → upsets/feel survive). Applies everywhere — casual and every tournament type.
# cons(r): 0 at/below CONS_LOW (no change) → 1 at/above CONS_HIGH (max steadiness).
T20_CONS_LOW  = 68.0
T20_CONS_HIGH = 88.0
T20_CONS_SET_BALLS    = 16     # protected "getting set" window (balls)
T20_CONS_EARLY_PROTECT = 0.62  # set-phase wicket-risk cut at cons=1 (×0.38) — kills cheap 0/15s
T20_CONS_EARLY_BND_DAMP = 0.34  # protected stars bat watchfully early (fewer boundaries)
T20_CONS_BIG_SCORE    = 35      # past this, wicket risk ESCALATES with the score so a
T20_CONS_LATE_SLOPE   = 0.030   #   protected star reliably gets out near his expected total
                                #   (×(1+slope·(runs-BIG))) — removes the runs floor-protection
                                #   added, so the MEAN/par stays flat and only the SPREAD shrinks.
T20_CONS_FORM_DAMP    = 0.60    # shrink the ±4% form wobble for top players

def t20_cons(rating: float) -> float:
    """0 → no change (rating ≤ LOW, current variance); 1 → max consistency (rating ≥ HIGH)."""
    if rating <= T20_CONS_LOW:
        return 0.0
    if rating >= T20_CONS_HIGH:
        return 1.0
    return (rating - T20_CONS_LOW) / (T20_CONS_HIGH - T20_CONS_LOW)

# ── 2.0: PITCH DETERIORATION ──
# How fast each surface wears over the match. Dust bowls / worn / cracked decks
# roughen fast (spin becomes lethal late); roads & dead decks barely change, so a
# flat track stays a flat track and keeps its 200 ceiling.
WEAR_SUSCEPT = {
    "Dusty": 1.5, "Worn": 1.5, "Turning": 1.4, "Cracked": 1.4, "Dry": 1.3,
    "Slow": 1.2, "Two-Paced": 1.1, "Sticky": 0.9, "Soft": 0.8, "Hard": 0.7,
    "Green": 0.6, "Damp": 0.7, "Bouncy": 0.7, "Flat": 0.4, "Dead": 0.3,
}
# Run-out share of all dismissals (not credited to the bowler).
T20_RUNOUT_SHARE = 0.07

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

def get_smart_ai_shot_t20(deliv, is_collapse, is_death_overs, archetype, pressure_multiplier=1.0):
    force_aggression = pressure_multiplier > 1.2 or is_death_overs

    if is_collapse and not force_aggression:
        if "Yorker" in deliv: return random.choices(["Block", "Drive"], weights=[50, 50], k=1)[0]
        elif "Bouncer" in deliv: return random.choices(["Block", "Pull", "Leave"], weights=[40, 40, 20], k=1)[0]
        return random.choices(["Block", "Drive", "Flick", "Cut"], weights=[30, 30, 20, 20], k=1)[0]
        
    if force_aggression:
        if "Yorker" in deliv:
            return random.choices(["Drive", "Block", "Flick", "Scoop", "Pull"], weights=[40, 10, 20, 20, 10], k=1)[0]
        elif "Bouncer" in deliv:
            return random.choices(["Pull", "Cut", "Loft", "Sweep"], weights=[40, 30, 20, 10], k=1)[0]
        elif "Full" in deliv:
            return random.choices(["Loft", "Drive", "Sweep", "Scoop"], weights=[40, 30, 15, 15], k=1)[0]
        elif deliv in SPIN_SHOT_MATRIX:
            if random.random() < 0.8: return random.choice(SPIN_SHOT_MATRIX[deliv])
            return random.choices(["Loft", "Sweep", "Drive"], weights=[40, 40, 20], k=1)[0]
        else:
            return random.choices(["Loft", "Pull", "Drive", "Scoop"], weights=[30, 30, 25, 15], k=1)[0]
            
    if "Yorker" in deliv:
        return random.choices(["Block", "Drive", "Flick", "Cut"], weights=[30, 40, 20, 10], k=1)[0]
    elif "Bouncer" in deliv:
        return random.choices(["Pull", "Cut", "Block", "Drive"], weights=[45, 35, 10, 10], k=1)[0]
    elif "Full" in deliv:
        return random.choices(["Drive", "Loft", "Flick", "Defensive"], weights=[40, 35, 15, 10], k=1)[0]
    elif deliv in SPIN_SHOT_MATRIX:
        if random.random() < 0.65:
            return random.choice(SPIN_SHOT_MATRIX[deliv])
        else:
            return random.choices(["Drive", "Sweep", "Cut", "Block", "Leave"], weights=[25, 25, 25, 15, 10], k=1)[0]
    else:
        return random.choices(["Drive", "Cut", "Flick", "Block", "Loft"], weights=[30, 20, 20, 15, 15], k=1)[0]

def get_smart_ai_bowler_t20(innings, pitch, weather="Clear", format_overs=20):
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
    # still be covered by the main attack with NO two-in-a-row. A schedule of n overs with
    # no consecutive repeats exists iff Σ min(remᵢ, ⌈n/2⌉) ≥ n, so we never burn a bowler
    # to the point where one man is forced to bowl back-to-back death overs — the bad
    # state simply never forms.
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

    # Tier-based pool: a "feasibility-safe" main bowler who isn't repeating comes first,
    # then progressively relax (only if already cornered) down to part-timers / last resort.
    pool = [p for p in safe if _nc(p)]
    # When the attack is TIGHT (little spare quota), depletion must stay balanced or the
    # weakest bowler gets stranded — so drain the fullest-quota bowler first. This is what
    # actually drives the back-to-back rate to ~0 on a 5-man, zero-slack attack.
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

        # ── Urgency boost: bowler has more overs left than available slots ──
        # Prevents wasted quota when match ends early
        if overs_remaining > 0 and overs_left > 0:
            urgency = overs_left / max(1, overs_remaining)
            if urgency >= 1.0:       base_score *= 6.0  # must bowl now or waste overs
            elif urgency >= 0.6:     base_score *= 2.5

        # Pitch adjustments
        if pitch == "Dusty" and "Spin" in p["role"]:             base_score *= 1.5
        elif pitch == "Dry" and "Spin" in p["role"] and current_over >= 10: base_score *= 1.3
        elif pitch == "Green" and "Pace" in p["role"]:            base_score *= 1.5
        elif pitch == "Hard" and "Pace" in p["role"] and current_over < 6:  base_score *= 1.4
        elif pitch == "Cracked":                                  base_score *= 1.3
        elif pitch == "Damp" and "Pace" in p["role"] and current_over < 6:  base_score *= 1.6
        elif pitch == "Worn" and "Spin" in p["role"] and current_over >= 10: base_score *= 1.5
        elif pitch == "Dead":                                     base_score *= 0.8
        elif pitch == "Turning" and "Spin" in p["role"]:         base_score *= 2.0
        elif pitch == "Slow" and "Spin" in p["role"]:            base_score *= 1.4
        elif pitch == "Bouncy" and "Pace" in p["role"]:          base_score *= 1.5
        elif pitch == "Sticky":                                   base_score *= 1.5

        # Weather adjustments
        if weather == "Cloudy" and "Pace" in p["role"] and current_over < 6:   base_score *= 1.1
        elif weather == "Overcast":
            if "Pace" in p["role"]: base_score *= 1.3
            elif "Spin" in p["role"]: base_score *= 0.8
        elif weather == "Humid" and "Pace" in p["role"]:         base_score *= 1.2
        elif weather == "Dry Heat":
            if "Spin" in p["role"] and current_over >= 10:       base_score *= 1.2
            elif "Pace" in p["role"] and current_over >= 10:     base_score *= 0.8
        elif weather == "Windy" and "Pace" in p["role"]:         base_score *= 1.3
        elif weather in ["Light Rain", "Drizzle"]:
            base_score *= (0.6 if "Spin" in p["role"] else 0.9)
        elif weather in ["Heavy Rain", "Thunderstorm"]:
            base_score *= (0.4 if "Spin" in p["role"] else 0.7)

        # Phase adjustments
        if current_over < 6:
            if "Pace" in p["role"]:  base_score *= 1.5
            if "Spin" in p["role"]:  base_score *= 0.2
        elif current_over < 14:
            if "Spin" in p["role"]:  base_score *= 1.5
        else:
            if "Pace" in p["role"] and p["archetype"] == "Finisher": base_score *= 2.0
            if "Spin" in p["role"]:  base_score *= 0.3

        # Death specialist priority (over 13+, widened from 16)
        if current_over >= 13 and float(p["bowl"]) >= 88 and "Pace" in p["role"] and overs_left > 0:
            base_score *= 8.0

        # Light saving penalty for Finisher before over 10 (only if they still have overs to spare)
        if current_over < 10 and p["archetype"] == "Finisher" and "Pace" in p["role"]:
            if overs_left >= 3 and overs_remaining >= 12:
                base_score *= 0.35  # soft save, not hard block

        # Economy factor
        if overs_bowled > 0:
            eco = (stats.runs_conceded / max(1, stats.balls_bowled)) * 6
            if eco <= 6.0:   base_score *= 2.5
            elif eco > 11.0: base_score *= 0.3

        weights.append(max(1.0, base_score))

    return random.choices(valid_bowlers, weights=weights, k=1)[0]


def _solo_batting(innings):
    """True if the non-striker is already out — i.e. the last man is batting ALONE.
    Only reachable in career club matches (max_wickets == squad size); there the lone
    batsman keeps strike and the over-end / single rotation is suppressed."""
    try:
        ns = innings.batting_team["players"][innings.current_non_striker_idx]
        return innings.batting_stats[ns["name"]].dismissal != "not out"
    except Exception:
        return True


def execute_ball_math_t20(match):
    innings = match.current_innings
    striker = innings.batting_team["players"][innings.current_striker_idx]
    bowler = innings.current_bowler

    b_stats = innings.batting_stats[striker["name"]]
    bow_stats = innings.bowling_stats[bowler["name"]]

    # Rating-scaled consistency applies to EVERY match (casual + all tournaments):
    # high-rated players sim more consistently, low-rated keep their full variance.
    _cons_bat  = t20_cons(striker["bat"])
    _cons_bowl = t20_cons(bowler["bowl"])

    # Form wobble: shrink the ±4% random form toward ±2% for high-rated players
    # (low-rated keep the full wobble). Mean-neutral; just steadies a star's rating.
    _bat_form  = 1.0 + (b_stats.form_factor - 1.0) * (1.0 - T20_CONS_FORM_DAMP * _cons_bat)
    _bowl_form = 1.0 + (bow_stats.form_factor - 1.0) * (1.0 - T20_CONS_FORM_DAMP * _cons_bowl)
    bat_rating = striker["bat"] * _bat_form
    bowl_rating = bowler["bowl"] * _bowl_form

    # ── 2.0 PITCH DETERIORATION ──
    # Surface roughens across the match: 0 at the first ball → ~1 by the last,
    # with innings 2 inheriting innings 1's wear. Scaled by the pitch's wear
    # susceptibility so roads stay roads. Worn surfaces give spin extra turn
    # (fed into the rating contest) and make timing slightly harder for everyone.
    _balls_in = innings.total_balls + (match.max_balls if match.current_innings_num == 2 else 0)
    wear = (_balls_in / (2 * match.max_balls)) * WEAR_SUSCEPT.get(match.pitch, 1.0)
    if "Spin" in bowler["role"]:
        bowl_rating += wear * 5.0

    # Pitch Mechanics
    if match.pitch == "Flat":
        bat_rating += 5
    elif match.pitch == "Green" and "Pace" in bowler["role"]:
        bowl_rating += 3
    elif match.pitch == "Dry" and "Spin" in bowler["role"] and innings.total_balls > 60:
        bowl_rating += 3
    elif match.pitch == "Dusty":
        if "Spin" in bowler["role"]: bowl_rating += 4
        bat_rating -= 1
    elif match.pitch == "Hard":
        if "Pace" in bowler["role"] and innings.total_balls < 36: bowl_rating += 3
        bat_rating += 2
    elif match.pitch == "Soft":
        bat_rating -= 2
    elif match.pitch == "Cracked":
        bowl_rating += 3
        bat_rating -= 2
    elif match.pitch == "Damp":
        if "Pace" in bowler["role"] and innings.total_balls < 36: bowl_rating += 4
        if innings.total_balls < 36: bat_rating -= 2
    elif match.pitch == "Dead":
        bat_rating += 4
        bowl_rating -= 3
    elif match.pitch == "Worn":
        if "Spin" in bowler["role"] and innings.total_balls > 60: bowl_rating += 4
        if innings.total_balls > 60: bat_rating -= 1
    elif match.pitch == "Turning":
        if "Spin" in bowler["role"]: bowl_rating += 5
       
    elif match.pitch == "Two-Paced":
        bat_rating -= 2
    elif match.pitch == "Slow":
        if "Spin" in bowler["role"]: bowl_rating += 3
        bat_rating -= 2
    elif match.pitch == "Bouncy":
        if "Pace" in bowler["role"]: bowl_rating += 4
        bat_rating -= 1
    elif match.pitch == "Sticky":
        bowl_rating += 4
        bat_rating -= 2
        
    # Weather Mechanics — new-ball conditions (Overcast, Cloudy, Humid, Windy) scale with
    # innings.total_balls so the advantage applies to the START of BOTH innings equally.
    _new_ball = innings.total_balls < 36  # powerplay of whichever innings is being played
    if match.weather == "Clear":
        bat_rating += 2
    elif match.weather == "Cloudy" and "Pace" in bowler["role"]:
        bowl_rating += (3 if _new_ball else 1)
    elif match.weather == "Overcast":
        if "Pace" in bowler["role"]: bowl_rating += (4 if _new_ball else 2)
        bat_rating -= (2 if _new_ball else 1)
    elif match.weather == "Humid" and "Pace" in bowler["role"]:
        bowl_rating += (3 if _new_ball else 1)
    elif match.weather == "Dry Heat":
        if "Pace" in bowler["role"]: bowl_rating -= 3
        elif "Spin" in bowler["role"] and innings.total_balls > 60: bowl_rating += 4
    elif match.weather == "Windy":
        if "Pace" in bowler["role"]: bowl_rating += (4 if _new_ball else 2)
    elif match.weather in ["Light Rain", "Drizzle"]:
        bowl_rating -= 4
        bat_rating += 2
    elif match.weather in ["Heavy Rain", "Thunderstorm"]:
        bowl_rating -= 3
        bat_rating += 2

    # Batter form progression. Asymmetric: keep the set-batsman scoring (par) but
    # soften the new-batsman penalty so early wickets don't cascade into blowouts.
    if b_stats.balls_faced < 4:
        bat_rating -= 3
    elif 4 <= b_stats.balls_faced <= 35:
        bat_rating += 5
    elif b_stats.balls_faced > 35:
        bat_rating -= (b_stats.balls_faced - 35) * 0.5
        
    # Bowler fatigue
    if bow_stats.balls_bowled >= 12 and "Pace" in bowler["role"]:
        bowl_rating -= 5
        
    total_balls = innings.total_balls
    is_powerplay = total_balls < 36
    is_death_overs = total_balls >= (match.max_balls - 30)
    
    pressure_multiplier = 1.0
    runs_needed = 0
    balls_left = match.max_balls - total_balls
    if match.current_innings_num == 2:
        target = getattr(match, "target", match.innings1.total_runs + 1)
        runs_needed = target - innings.total_runs
        if balls_left > 0:
            rrr = (runs_needed / balls_left) * 6
            if total_balls < 36: # Powerplay
                threshold = 10.0
                max_p = 1.25
                scale = 0.05
            elif total_balls < 90: # Middle
                threshold = 11.0
                max_p = 1.35
                scale = 0.06
            else: # Death
                threshold = 10.5
                max_p = 1.50
                scale = 0.08
                
            if rrr > threshold:
                pressure_multiplier = min(max_p, 1.0 + ((rrr - threshold) * scale))

    # Collapse = 2+ wickets in the last 18 balls with a small current stand. We
    # track wicket ball-numbers on a persistent list because over_log is wiped
    # every over, so over_log[-18:] could only ever see the current over.
    _recent_wkts = sum(1 for _b in getattr(innings, "wkt_balls", []) if _b >= total_balls - 18)
    is_collapse = _recent_wkts >= 2 and innings.partnership_runs < 25
    is_set_partnership = innings.partnership_runs >= 30
    has_wickets_in_hand = total_balls >= (match.max_balls - 42) and innings.wickets <= 3

    # Dynamic Delivery Generation based on Bowler Role (for Fast Sim)
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
                deliv = f"{random.choice(['Inswing', 'Outswing', 'Fast', 'Slow'])} {random.choice(['Bouncer', 'Full', 'Good', 'Yorker'])}"
            
    shot = match.current_shot_selection or get_smart_ai_shot_t20(deliv, is_collapse, is_death_overs, striker["archetype"], pressure_multiplier)
        
    match.current_delivery_selection = None
    match.current_shot_selection = None
    match.temp_variation = None

    # ── Non-linear skill contest (replaces the old linear bat-bowl diff) ──
    # Each rating is mapped onto an exponential curve, then the batter's
    # "share of control" is bat_eff / (bat_eff + bowl_eff). This is a logistic
    # response: equal ratings → 0.5, and the gap between elite ratings matters
    # disproportionately more than the gap between poor ones.
    bat_eff  = math.exp((bat_rating  - 80.0) / T20_SKILL_SCALE)
    bowl_eff = math.exp((bowl_rating - 80.0) / T20_SKILL_SCALE)
    dominance = bat_eff / (bat_eff + bowl_eff)   # 0..1
    edge = dominance - 0.5                          # ~[-0.45, +0.45]
    diff = edge * 100.0  # legacy scale, kept for any downstream heuristics

    free_hit_active = getattr(match, "free_hit", False)
    is_wide = False
    is_no_ball = False
    prefix = getattr(match, "last_commentary_prefix", "")
    match.last_commentary_prefix = ""
    
    if not hasattr(innings, "bouncers_in_over"): innings.bouncers_in_over = 0
    if "Bouncer" in deliv:
        innings.bouncers_in_over += 1
        if innings.bouncers_in_over == 3:
            is_no_ball = True
            prefix += "🚨 **NO BALL!** (Third bouncer of the over)\n➡️ **NEXT BALL IS A FREE HIT!**\n"

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
            innings.over_log.append("<:wide:1520119718638260334>")
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
        innings.over_log.append("<:wide:1520119718638260334>")
        match.last_commentary = prefix + f"**{bowler['name']}** bowled a **Wide!**\n💥 **Result:** 1 Extra Run"
        if free_hit_active: match.last_commentary_prefix = "🛡️ *(Free Hit continues)*\n"
        return
        
    if not is_no_ball and random.random() < 0.02:
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
        match.free_hit = True

    dot_weight      = max(8.0,  T20_BASE_DOT  - edge * T20_DOT_SENS)
    single_weight   = T20_BASE_SINGLE
    boundary_weight = max(1.0,  T20_BASE_BND  + edge * T20_BND_SENS)
    wicket_weight   = max(0.6,  T20_BASE_WKT  - edge * T20_WKT_SENS)
    
    if b_stats.balls_faced > 45:
        wicket_weight *= 1.5

    bad_shot_selection = False
    perfect_shot_selection = False
    
    # Advanced Pitch Base Probability Modifiers
    if match.pitch == "Flat":
        boundary_weight *= 1.14
        wicket_weight *= 0.90
        dot_weight *= 0.95
    elif match.pitch == "Green" and "Pace" in bowler["role"]:
        wicket_weight *= 1.10
        boundary_weight *= 0.95
    elif match.pitch == "Dusty" and "Spin" in bowler["role"]:
        wicket_weight *= 1.10
        boundary_weight *= 0.90
        dot_weight *= 1.05
    elif match.pitch == "Dry" and "Spin" in bowler["role"] and innings.total_balls > 60:
        wicket_weight *= 1.05
        dot_weight *= 1.05
    elif match.pitch == "Hard":
        if innings.total_balls < 36 and "Pace" in bowler["role"]:
            wicket_weight *= 1.05
        else:
            boundary_weight *= 1.05
    elif match.pitch == "Soft":
        dot_weight *= 1.10
        boundary_weight *= 0.90
    elif match.pitch == "Cracked":
        wicket_weight *= 1.15
        boundary_weight *= 0.90
    elif match.pitch == "Damp":
        if "Pace" in bowler["role"] and innings.total_balls < 36:
            wicket_weight *= 1.15
            boundary_weight *= 0.85
    elif match.pitch == "Dead":
        boundary_weight *= 1.15
        wicket_weight *= 0.90
    elif match.pitch == "Worn":
        if "Spin" in bowler["role"] and innings.total_balls > 60:
            wicket_weight *= 1.15
            dot_weight *= 1.05
            boundary_weight *= 0.90
    elif match.pitch == "Turning":
        if "Spin" in bowler["role"]:
            wicket_weight *= 1.15
            boundary_weight *= 0.85
            dot_weight *= 1.10
    elif match.pitch == "Two-Paced":
        dot_weight *= 1.15
        boundary_weight *= 0.85
        wicket_weight *= 1.05
    elif match.pitch == "Slow":
        dot_weight *= 1.15
        boundary_weight *= 0.85
        if "Spin" in bowler["role"]:
            wicket_weight *= 1.10
    elif match.pitch == "Bouncy":
        if "Pace" in bowler["role"]:
            wicket_weight *= 1.10
    elif match.pitch == "Sticky":
        wicket_weight *= 1.25
        boundary_weight *= 0.75
        dot_weight *= 1.20
        
    # Weather Advanced Modifiers — new-ball conditions scale with innings.total_balls
    # so both innings get the powerplay swing advantage (not just innings 1).
    _new_ball = total_balls < 36
    if match.weather == "Overcast":
        wicket_weight *= (1.22 if _new_ball else 1.08)
        boundary_weight *= (0.85 if _new_ball else 0.93)
    elif match.weather == "Cloudy":
        if _new_ball and "Pace" in bowler["role"]:
            wicket_weight *= 1.10
            boundary_weight *= 0.95
    elif match.weather == "Humid":
        if _new_ball and "Pace" in bowler["role"]:
            wicket_weight *= 1.08
    elif match.weather == "Dry Heat":
        dot_weight *= 1.10
    elif match.weather == "Windy":
        wicket_weight *= (1.15 if _new_ball else 1.07)
        boundary_weight *= 0.95
    elif match.weather in ["Light Rain", "Drizzle"]:
        wicket_weight *= 0.90
        boundary_weight *= 1.05
    elif match.weather in ["Heavy Rain", "Thunderstorm"]:
        wicket_weight *= 0.80
        boundary_weight *= 1.15

    # ── BALL AGE / HARDNESS ──────────────────────────────────────────────
    # The ball is a third actor alongside bat & bowl. A hard new ball seams,
    # swings and flies off the edge (pace threat + flush boundaries); it goes
    # soft through the middle (harder to time, spin grips); and in the back
    # third it gets old enough to reverse for pace. 20 overs isn't long enough
    # for heavy reverse, so the late effect is mild compared to ODIs.
    _ball_frac = total_balls / max(1, match.max_balls)
    _is_pace_b = "Pace" in bowler["role"]
    _is_spin_b = "Spin" in bowler["role"]
    if _ball_frac < 0.30:            # brand new, hard ball
        if _is_pace_b:
            wicket_weight *= 1.10
            boundary_weight *= 1.05    # edges fly, but it also comes onto the bat
        elif _is_spin_b:
            boundary_weight *= 1.06    # spin on a hard ball gets hit
            wicket_weight *= 0.95
    elif _ball_frac < 0.70:          # ball has gone soft
        boundary_weight *= 0.94
        dot_weight *= 1.05
        if _is_spin_b:
            wicket_weight *= 1.06      # grip & turn
    else:                            # back third — mild reverse for pace
        if _is_pace_b:
            wicket_weight *= 1.08
        boundary_weight *= 1.03

    # ── 2.0 PITCH DETERIORATION (weight effects) ──
    # A worn surface helps spin take wickets and makes timing fractionally harder.
    if _is_spin_b:
        wicket_weight *= (1.0 + wear * 0.35)
    boundary_weight *= (1.0 - wear * 0.07)
    dot_weight *= (1.0 + wear * 0.06)

    # ── 2.0 BATTING MOMENTUM ──
    # A new batsman is vulnerable until set; a well-set batsman is dangerous.
    # This layers on top of the balls-faced rating curve to sharpen the "playing
    # yourself in vs seeing it like a beachball" texture of an innings.
    _bf = b_stats.balls_faced
    if _bf < 6:
        wicket_weight *= (1.32 - _bf * 0.045)   # ~1.32 first ball → ~1.05 at 6
        boundary_weight *= (0.78 + _bf * 0.035)
    elif _bf >= 15:
        boundary_weight *= 1.10                  # set — cashing in

    # 🚨 TACTICAL USER BALANCING & SPIN LOGIC
    if "Yorker" in deliv:
        if shot in ["Pull", "Cut"]: bad_shot_selection = True
        elif shot in ["Defensive", "Drive"]: perfect_shot_selection = True
    elif "Bouncer" in deliv:
        if shot in ["Drive", "Sweep", "Scoop"]: bad_shot_selection = True
        elif shot in ["Pull", "Leave"]: perfect_shot_selection = True
    elif "Full Toss" in deliv:
        if shot in ["Defensive", "Leave"]: bad_shot_selection = True
        elif shot in ["Loft", "Drive"]: perfect_shot_selection = True
    elif is_cutter:
        if shot in ["Block", "Leave", "Defensive"]: perfect_shot_selection = True
        elif shot in ["Loft", "Scoop"]:             bad_shot_selection = True
    elif deliv in SPIN_SHOT_MATRIX:
        if shot in SPIN_SHOT_MATRIX[deliv]: perfect_shot_selection = True
        elif shot == "Leave": bad_shot_selection = True 
        else:
            boundary_weight *= 0.20
            dot_weight *= 1.4
            single_weight *= 1.1

    if shot in ["Block", "Defensive"]:
        dot_weight *= 1.6; single_weight *= 0.9; boundary_weight = 0.1; wicket_weight *= 0.5
    elif shot == "Leave":
        dot_weight *= 3.0; single_weight = 0; boundary_weight = 0; wicket_weight *= 1.2
    else:
        if bad_shot_selection: wicket_weight *= 1.8; boundary_weight *= 0.3; dot_weight *= 1.5
        elif perfect_shot_selection: boundary_weight *= 1.4; wicket_weight *= 0.7
        
        # Required run rate (chase only) tells set batters when to lift the tempo.
        _rrr_now = (runs_needed / balls_left * 6) if (match.current_innings_num == 2 and balls_left > 0) else 0.0
        _set = b_stats.balls_faced >= 18
        _lift = is_death_overs or _rrr_now >= 9.0   # death overs OR the ask has climbed above par

        if striker["archetype"] == "Aggressor":
            boundary_weight *= 1.2; wicket_weight *= 1.15
        elif striker["archetype"] == "Anchor":
            # Safety is BOUGHT with tempo: while building he's slow (more dots, few
            # boundaries) AND very hard to dislodge; once set he opens up; set + a rising
            # ask, he explodes with control. The run gap mirrors the wicket gap at each stage.
            if _set and _lift:
                boundary_weight *= 1.30; wicket_weight *= 0.96   # cuts loose — finds gaps, stays secure
            elif _set:
                boundary_weight *= 1.12; wicket_weight *= 0.86    # keeps the score ticking, not blocking
            else:
                dot_weight *= 1.16; boundary_weight *= 0.82; wicket_weight *= 0.76   # still playing himself in
        elif striker["archetype"] == "Standard":
            # The middle-ground: scores a touch quicker than the Anchor but is a touch
            # less secure at EVERY stage — the difference shows up in runs AND in risk.
            if _set and _lift:
                boundary_weight *= 1.20; wicket_weight *= 1.06
            elif _set:
                boundary_weight *= 1.08; wicket_weight *= 0.95
            else:
                dot_weight *= 1.04; boundary_weight *= 0.94; wicket_weight *= 0.86
        elif striker["archetype"] == "Finisher" and is_death_overs:
            boundary_weight *= 1.3

        if is_collapse: boundary_weight *= 0.7; wicket_weight *= 0.65; single_weight *= 1.15
        if is_set_partnership: wicket_weight *= 0.8
        if has_wickets_in_hand: boundary_weight *= 1.22; wicket_weight *= 1.15; dot_weight *= 0.75
        
        active_multiplier = pressure_multiplier
        if is_death_overs:
            if match.current_innings_num == 1:
                active_multiplier = max(1.30, pressure_multiplier)
            else:
                # Chasing teams shouldn't commit suicide if they are already cruising to victory
                if balls_left > 0 and (runs_needed / balls_left * 6) > 7.5:
                    active_multiplier = max(1.30, pressure_multiplier)

        if active_multiplier > 1.0:
            boundary_weight *= active_multiplier
            if total_balls < 90:
                wicket_weight *= (1.0 + (active_multiplier - 1.0) * 0.6) # Dampened suicide curve
            else:
                wicket_weight *= (1.0 + (active_multiplier - 1.0) * 0.8) # Dampened suicide curve
                
        if innings.last_ball_boundary: boundary_weight *= 1.15; wicket_weight *= 1.15
        if is_powerplay: boundary_weight *= 1.25; single_weight *= 0.85
            
    if "Mystery" in deliv:
        wicket_weight *= 1.6
        dot_weight *= 1.5
        boundary_weight *= 0.6
        single_weight *= 0.8

    if is_cutter:
        dot_weight *= 1.35; boundary_weight *= 0.65; wicket_weight *= 1.15
        if deliv == "Knuckle": dot_weight *= 1.10; boundary_weight *= 0.85

    # Boundary variance compressor: tame stacked boundary spikes (freak 250s) so
    # scoring is skill-driven, not a boundary lottery. Shot/delivery four-six
    # adjustments still apply on top of the compressed base.
    if boundary_weight > T20_BASE_BND:
        boundary_weight = T20_BASE_BND + (boundary_weight - T20_BASE_BND) * T20_BND_COMPRESS
    four_weight = boundary_weight
    six_weight = boundary_weight * 0.40   # modern T20 six-hitting

    if shot in ["Loft", "Scoop"]: four_weight *= 0.6; six_weight *= 3.0; wicket_weight *= 1.8; dot_weight *= 0.8
    elif shot in ["Block", "Defensive"]: four_weight *= 0.1; six_weight = 0.0
    elif shot in ["Drive", "Cut", "Pull", "Flick", "Sweep"]: four_weight *= 1.2; six_weight *= 0.5

    if "Slow" in deliv and shot in ["Loft", "Pull", "Sweep", "Scoop"]: wicket_weight *= 1.5; six_weight *= 0.5
    elif "Fast" in deliv and shot in ["Scoop", "Sweep", "Pull", "Loft"]: wicket_weight *= 1.5
    elif "Outswing" in deliv and shot in ["Drive", "Cut"]: wicket_weight *= 1.4; four_weight *= 1.2
    elif "Inswing" in deliv and shot in ["Drive", "Flick", "Sweep"]: wicket_weight *= 1.4
    elif is_cutter and shot in ["Drive", "Cut"]: wicket_weight *= 1.25; four_weight *= 0.85

    # ── RATING-SCALED CONSISTENCY (tournament only; high-rated → steadier) ──
    # Cuts a star's match-to-match swing (no more 100,15,0,20,30) so their
    # tournament aggregate is reliable — WITHOUT touching low-rated players
    # (cons=0), so weak sides stay erratic and upsets/feel survive.
    if _cons_bat > 0.0:
        # Set-phase protection: elite batters far less likely to fall cheaply →
        # they reliably get a start (the main driver of freak low scores). Paired
        # with an early-boundary damp so surviving longer doesn't inflate par —
        # the star just plays himself in, then cashes in once set.
        if b_stats.balls_faced < T20_CONS_SET_BALLS:
            wicket_weight *= (1.0 - _cons_bat * T20_CONS_EARLY_PROTECT)
            four_weight   *= (1.0 - _cons_bat * T20_CONS_EARLY_BND_DAMP)
            six_weight    *= (1.0 - _cons_bat * T20_CONS_EARLY_BND_DAMP)
        # Top-end taming: wicket risk escalates with the score past the milestone, so
        # a protected star gets out near his expected total instead of running to 130.
        # This removes the runs the floor-protection added → par flat, spread tighter.
        if b_stats.runs_scored > T20_CONS_BIG_SCORE:
            wicket_weight *= (1.0 + _cons_bat * T20_CONS_LATE_SLOPE * (b_stats.runs_scored - T20_CONS_BIG_SCORE))

    # ── VARIANCE COMPRESSORS ──────────────────────────────────────────────
    # Skill should decide matches, not luck. Per-ball weight spikes (from stacked
    # delivery×shot×situation multipliers) cause wickets/boundaries to CLUSTER →
    # cascades to 30-all-out or freak 250s between equal teams. Pull the upward
    # spikes back toward the rating-driven baseline so good sides score
    # consistently. (Mirrors the ODI engine's ODI_WKT_COMPRESS.)
    if wicket_weight > T20_BASE_WKT:
        wicket_weight = T20_BASE_WKT + (wicket_weight - T20_BASE_WKT) * T20_WKT_COMPRESS

    # 🚨 ANTI-OVERCOOK SAFETIES (Prevents stacked conditions from breaking the game)
    four_weight = max(0.5, min(four_weight, 23.0)) # Hard cap — clips road-deck freak 250s
    six_weight = max(0.1, min(six_weight, 12.5))
    if match.pitch in ("Flat", "Dead"):
        wicket_weight = min(wicket_weight, T20_BAT_PITCH_WKT_CAP)  # batting-paradise floor
    elif match.pitch in T20_BOWL_DECKS:
        wicket_weight = min(wicket_weight, T20_BOWL_PITCH_WKT_CAP)  # minefield floor: ~120-140 not sub-100
    wicket_weight = max(1.0, min(wicket_weight, 30.0)) # Hard cap to prevent 10/10 scenarios
    dot_weight = max(5.0, min(dot_weight, 120.0))

    weights = [dot_weight, single_weight, single_weight * 0.20, single_weight * 0.05, four_weight, six_weight, wicket_weight]
    outcome = random.choices(["dot", "single", "two", "three", "four", "six", "wicket"], weights=weights)[0]
    
    if is_no_ball and outcome == "wicket":
        outcome = "dot"
        prefix += "*(Wicket denied due to No Ball)*\n"
    if free_hit_active and not is_no_ball and outcome == "wicket":
        outcome = random.choice(["dot", "single", "two"])
        prefix += "🛡️ **FREE HIT!** Batter escapes dismissal!\n"
    
    b_stats.balls_faced += 1
    innings.last_ball_boundary = False
    outcome_text = ""

    if outcome == "wicket":
        innings.wickets += 1
        innings.partnership_runs = 0
        if not hasattr(innings, "wkt_balls"): innings.wkt_balls = []
        innings.wkt_balls.append(innings.total_balls)  # for the rolling collapse window
        d_types = ["Bowled", "Caught", "LBW"]

        # ── 2.0 DISMISSAL VARIETY ──
        # Run-out first: a fielding mix-up, NOT credited to the bowler. Then
        # stumping (charging a spinner and missing) and hit-wicket (treading on
        # the stumps to a short ball) join the bowler-credited dismissals.
        if random.random() < T20_RUNOUT_SHARE:
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
            elif is_cutter and shot in ["Loft", "Scoop"]:  dismissal_type = "Caught"
            elif is_cutter and shot in ["Drive", "Cut"]:   dismissal_type = random.choice(["Caught", "Bowled"])
            elif bad_shot_selection and "Yorker" in deliv: dismissal_type = "Bowled"
            elif bad_shot_selection and "Bouncer" in deliv: dismissal_type = "Caught"
            elif shot in ["Loft", "Scoop"]: dismissal_type = "Caught"
            else: dismissal_type = random.choices(["Caught", "Bowled", "LBW"], weights=[51, 28, 21])[0]  # caught-heavy like real cricket

            if dismissal_type == "Bowled": b_stats.dismissal = f"b. {bowler['name']}"
            elif dismissal_type == "LBW": b_stats.dismissal = f"lbw b. {bowler['name']}"
            elif dismissal_type == "Hit Wicket": b_stats.dismissal = f"hit wkt b. {bowler['name']}"
            elif dismissal_type in ("Stumped", "Caught Behind"):
                wk = next((p["name"] for p in innings.bowling_team["players"] if "WK" in p["role"]), "Keeper")
                pre = "st." if dismissal_type == "Stumped" else "c."
                b_stats.dismissal = f"{pre} {wk} b. {bowler['name']}"
            else:
                fielders = [p["name"] for p in innings.bowling_team["players"] if p["name"] != bowler["name"]]
                fielder = random.choice(fielders) if fielders else "Fielder"
                b_stats.dismissal = f"c. {fielder} b. {bowler['name']}"

            bow_stats.wickets_taken += 1
        innings.over_log.append("<:wicket:1520119708802875443>")
        outcome_text = f"WICKET! ({dismissal_type.upper()})"
        
        match.prev_striker_idx = innings.current_striker_idx
        if dismissal_type in ["LBW", "Caught Behind"] and match.simulation_mode == "interactive":
            match.pending_drs = True
            match.drs_dismissal = dismissal_type
        
        max_wickets = 2 if getattr(match, "is_super_over", False) else getattr(match, "max_wickets", 10)
        if innings.wickets < max_wickets:
            is_ai_batting = match.is_ai_game and match.get_striker_user_id() == match.p2_id
            if match.simulation_mode == "whole_match" or is_ai_batting:
                # Skip any players who are already dismissed or subbed out (e.g. impact
                # player subs insert the original player at a later slot with "Subbed Out")
                while innings.next_batter_idx < len(innings.batting_team["players"]):
                    candidate_name = innings.batting_team["players"][innings.next_batter_idx]["name"]
                    candidate_stats = innings.batting_stats.get(candidate_name)
                    if candidate_stats is None or candidate_stats.dismissal == "not out":
                        break
                    innings.next_batter_idx += 1
                innings.current_striker_idx = innings.next_batter_idx
                innings.next_batter_idx += 1
            else:
                match.pending_next_batter = True
                match.out_batter_profile = striker
    else:
        runs_map = {"dot": 0, "single": 1, "two": 2, "three": 3, "four": 4, "six": 6}
        runs = runs_map[outcome]
        
        is_bye = False
        if runs == 0 and random.random() < 0.05:
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
                
            emoji_map = {0: "<:dot:1520118655994695962>", 1: "<:single:1520118720146440312>", 2: "<:double:1520118671865942179>", 3: "<:3run:1520118615201022073>", 4: "<:four1:1520118764555866342>", 6: "<:geminisvg:1520118699720184038>"}
            log_entry = emoji_map[runs]
            
        if is_no_ball:
            log_entry = "<:noball:1520119727786037249>" + (log_entry if runs > 0 and not is_bye else "")
            outcome_text += " (NO BALL)"
            
        if runs in [4, 6] and not is_bye:
            innings.last_ball_boundary = True
            if runs == 4:
                b_stats.fours = getattr(b_stats, 'fours', 0) + 1
            elif runs == 6:
                b_stats.sixes = getattr(b_stats, 'sixes', 0) + 1
            
        innings.over_log.append(log_entry)
        
        if runs in [1, 3] and not (getattr(match, "is_club", False) and _solo_batting(innings)):
            innings.current_striker_idx, innings.current_non_striker_idx = innings.current_non_striker_idx, innings.current_striker_idx

    if not is_no_ball:
        bow_stats.balls_bowled += 1
        innings.total_balls += 1
        match.free_hit = False
        if innings.total_balls % 6 == 0:
            match.over_completed = True
            # End-of-over strike rotation: runs of 1/3 already swapped mid-ball.
            # Suppressed in career last-man (solo) batting so the lone batsman keeps strike.
            if (outcome == "wicket" or runs not in [1, 3]) and not (getattr(match, "is_club", False) and _solo_batting(innings)):
                innings.current_striker_idx, innings.current_non_striker_idx = innings.current_non_striker_idx, innings.current_striker_idx
        
    match.last_commentary = prefix + f"**{bowler['name']}** bowled a **{deliv}**\n**{striker['name']}** played: **{shot}**\n💥 **Result:** {outcome_text}"