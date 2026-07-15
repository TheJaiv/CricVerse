
"""
Test Cricket Simulation v2.0 - standalone local runner.
Run: $env:PYTHONIOENCODING="utf-8"; python CricVerse/test_simulation.py
Three modes: simulate_session | simulate_innings | simulate_match
"""
import random
from dataclasses import dataclass
from typing import Optional, List, Dict

# ---- Constants ----
SPIN_SHOT_MATRIX = {
    "Off spin":  ["Sweep", "Drive", "Flick"],
    "Carrom":    ["Cut", "Drive", "Loft"],
    "Arm ball":  ["Loft", "Drive", "Block"],
    "Doosra":    ["Cut", "Sweep", "Drive"],
    "Top spin":  ["Cut", "Drive", "Pull"],
    "Leg spin":  ["Cut", "Drive", "Loft", "Sweep"],
    "Googly":    ["Pull", "Drive", "Sweep"],
    "Flipper":   ["Drive", "Flick", "Block"],
    "Drifter":   ["Loft", "Drive", "Cut"],
    "Slider":    ["Flick", "Drive", "Sweep"],
    "Mystery":   ["Block", "Sweep", "Drive"],
}
SESSION_NAMES = {1: "Morning", 2: "Afternoon", 3: "Evening"}

# How fast each pitch surface wears (relative to 1 day = 90 overs)
PITCH_WEAR_RATE: Dict[str, float] = {
    "Turning": 1.6, "Cracked": 1.5, "Dusty": 1.4, "Worn":      1.3,
    "Sticky":  1.3, "Slow":    1.2, "Two-Paced": 1.1, "Hard":   1.0,
    "Bouncy":  0.9, "Soft":    0.9, "Green":     0.8, "Damp":   0.7,
    "Flat":    0.7, "Dead":    0.5, "Dry":       1.1,
}
PITCH_TYPES   = list(PITCH_WEAR_RATE.keys())
WEATHER_TYPES = ["Clear", "Cloudy", "Overcast", "Humid", "Windy", "Dry Heat",
                 "Drizzle", "Light Rain", "Heavy Rain", "Thunderstorm"]

# Ball-outcome emojis (shared with T20/ODI over_log convention)
_OV_DOT = "<:0run:1520141253604544633>"
_OV_1   = "<:1run:1520143026381656104>"
_OV_2   = "<:2run:1520143029015548026>"
_OV_3   = "<:3run:1520143031682990202>"
_OV_4   = "<:4run:1520143034573131807>"
_OV_6   = "<:6run:1520143037945090105>"
_OV_W   = "<:wicket:1520143043683156051>"
_OV_WD  = "<:wide:1520143046900191344>"

# ---- Data classes ----
@dataclass
class TestBattingStats:
    balls_faced: int = 0
    runs_scored: int = 0
    fours:       int = 0
    sixes:       int = 0
    dismissal:   str = "not out"

@dataclass
class TestBowlingStats:
    balls_bowled:     int = 0
    runs_conceded:    int = 0
    wickets_taken:    int = 0
    maidens:          int = 0
    spell_balls:      int = 0   # balls in current unbroken spell
    last_over_bowled: int = -5  # over index when last bowled (for spell-reset logic)
    over_run_start:   int = 0   # runs_conceded at the start of current over (maiden check)

    @property
    def overs_str(self) -> str:
        return f"{self.balls_bowled // 6}.{self.balls_bowled % 6}"

    @property
    def economy(self) -> float:
        return round((self.runs_conceded / self.balls_bowled) * 6, 2) if self.balls_bowled else 0.0

class TestInnings:
    def __init__(self, batting_team: dict, bowling_team: dict, innings_num: int):
        self.batting_team  = batting_team
        self.bowling_team  = bowling_team
        self.innings_num   = innings_num

        self.batting_stats: Dict[str, TestBattingStats] = {
            p["name"]: TestBattingStats() for p in batting_team["players"]
        }
        self.bowling_stats: Dict[str, TestBowlingStats] = {
            p["name"]: TestBowlingStats() for p in bowling_team["players"]
        }

        self.total_runs    = 0
        self.wickets       = 0
        self.total_balls   = 0   # legal deliveries only
        self.extras        = 0

        self.current_striker_idx     = 0
        self.current_non_striker_idx = 1
        self.next_batter_idx         = 2

        self.current_bowler: Optional[dict] = None
        self.prev_bowler:    Optional[dict] = None
        self.last_ball_boundary = False
        self.partnership_runs   = 0
        self.is_complete        = False
        self.declared           = False   # innings closed by declaration (not all-out)
        self.over_log: list     = []      # emoji timeline for current over (reset each over)

        # Ball condition
        self.ball_age              = 0    # legal balls since last new ball
        self.second_new_ball_taken = False
        self.mystery_bowled_this_over = False

        self.fow: List[str] = []   # fall of wickets strings

    @property
    def run_rate(self) -> float:
        return round((self.total_runs / self.total_balls) * 6, 2) if self.total_balls else 0.0

    @property
    def overs_str(self) -> str:
        return f"{self.total_balls // 6}.{self.total_balls % 6}"

class TestMatch:
    def __init__(self, team1: dict, team2: dict, pitch: str = "Flat", weather: str = "Clear", pink_ball: bool = False):
        self.team1   = team1
        self.team2   = team2
        self.pitch   = pitch
        self.weather = weather
        # Day-night Test: the pink ball swings/seams under lights - twilight (session 2)
        # is the danger period and it stays lively into the night (session 3).
        self.pink_ball = pink_ball

        self.innings_list: List[TestInnings] = []
        self.current_innings_idx = 0

        self.day            = 1
        self.session        = 1   # 1=Morning 2=Afternoon 3=Evening
        self.overs_in_session = 0
        self.total_match_overs = 0   # running total across all innings/days

        self.result: Optional[str] = None
        self.follow_on_enforced = False
        self.follow_on_msg: str = ""
        self.over_completed     = False

        # Interactive mode: player-chosen delivery/shot (empty string = auto)
        self.current_delivery_selection: str = ""
        self.current_shot_selection: str     = ""
        self.new_ball_msg: str               = ""   # set when second new ball is taken

        self._new_innings(team1, team2)

    def _new_innings(self, batting: dict, bowling: dict):
        self.innings_list.append(TestInnings(batting, bowling, len(self.innings_list) + 1))

    @property
    def current_innings(self) -> TestInnings:
        return self.innings_list[self.current_innings_idx]

    def advance_session(self) -> bool:
        """Returns True when all 5 days are used up."""
        self.session += 1
        self.overs_in_session = 0
        if self.session > 3:
            self.session = 1
            self.day += 1
        return self.day > 5

# ---- Pitch / condition helpers ----
def _wear_level(match: TestMatch) -> float:
    """Dynamic pitch wear: 1.0 (fresh) -> 5.0 (crumbling), based on total match overs."""
    rate = PITCH_WEAR_RATE.get(match.pitch, 1.0)
    return min(5.0, max(1.0, 1.0 + (match.total_match_overs / 90.0) * rate))

def _wear_mods(wear: float, bowler: dict):
    """Returns (bowl_bonus, bat_penalty, wicket_mult) for given wear level and bowler type."""
    pace = "Pace" in bowler["role"]
    spin = "Spin" in bowler["role"]
    if wear < 1.5:    # fresh - pacers have edge, not overwhelming
        if pace: return  3, -1, 1.06
        if spin: return -3,  0, 0.78
    elif wear < 2.2:  # batting day - even contest
        if pace: return  1,  0, 1.00
        if spin: return  1,  0, 0.94
    elif wear < 3.0:  # spinners bite
        if spin: return  4, -2, 1.18
        if pace: return -1,  0, 0.95
    elif wear < 4.0:  # significant wear
        if spin: return  7, -3, 1.32
        if pace: return -2,  0, 0.90
    else:             # crumbling day-5
        if spin: return 11, -5, 1.55
        if pace: return -4,  0, 0.80
    return 0, 0, 1.0

def _ball_condition_bonus(ball_age: int, bowler: dict) -> float:
    """
    Extra bowl_rating from ball condition for pace bowlers.
    Phases: new ball swing -> old ball -> reverse swing -> second new ball.
    """
    if "Pace" not in bowler["role"]:
        return 0.0
    bowl = float(bowler["bowl"])
    if ball_age < 120:    # overs 1-20: conventional swing
        return 6.0 * (1.0 - ball_age / 180.0)
    elif ball_age < 300:  # overs 20-50: swing fading
        return max(0.0, 2.5 - (ball_age - 120) / 60.0)
    elif ball_age < 480:  # overs 50-80: reverse swing for elite bowlers only
        if bowl >= 84:
            rev = (ball_age - 300) / 180.0 * 6.0 * ((bowl - 78) / 18.0)
            return min(6.0, rev)
        return 0.0
    return 0.0   # second new ball: ball_age reset to 0 elsewhere

def _pitch_intent(pitch: str) -> float:
    """Baseline batting tempo for the pitch (innings 1-3). ASYMMETRIC: roads get a big
    attack boost (the original 'flat is too slow' fix), but bowler decks only ease off
    mildly - grinding them to a crawl makes innings eat 180 overs and forces fake draws.
    Flat 1.33 · Dead 1.44 · Hard 1.17 · neutral 1.0 · Green 0.92 · Turning 0.90 · Sticky 0.87."""
    dev = _PITCH_SCORING_RATE.get(pitch, 3.0) - 3.0
    return max(0.85, min(1.55, 1.0 + dev * (0.55 if dev >= 0 else 0.26)))


def _batting_intent(match: TestMatch) -> float:
    """
    Match-aware intent: 0.2 = survive for draw, 1.0 = normal, 1.8 = all-out attack.
    4th innings: weighs runs needed, overs left, AND wickets in hand.
    """
    idx = match.current_innings_idx
    if idx < 2:
        return _pitch_intent(match.pitch)

    innings = match.current_innings
    target  = _get_chase_target(match)
    if target is None:
        return _pitch_intent(match.pitch)

    runs_needed  = target - innings.total_runs
    if runs_needed <= 0:
        return 1.0

    wickets_left  = 10 - innings.wickets
    sessions_left = (5 - match.day) * 3 + (3 - match.session)
    # Include remaining overs in the CURRENT session - sessions_left only counts
    # FUTURE sessions, so without this correction, a last-session chase reads
    # overs_left = 0 and drops into "survive" mode even with 25 overs remaining.
    overs_left = max(0, sessions_left * 30 + max(0, 30 - match.overs_in_session))

    if overs_left == 0:
        return 0.2   # time up - just survive last over

    rpo_needed = runs_needed / overs_left

    # The chase keys off WICKETS IN HAND, not "a wicket fell": losing 2 early
    # (8 in hand) keeps chasing; only a thinning line-up plays for the draw.

    # Win is a near-formality - knock it off regardless of how many are down.
    if rpo_needed < 1.3:
        return 0.90

    # 0-3 down (7+ in hand): chase freely, the win is clearly on
    if wickets_left >= 7:
        if rpo_needed < 2.5:  return 1.00
        if rpo_needed < 4.0:  return 1.25
        if rpo_needed < 5.5:  return 1.55
        if rpo_needed < 8.0:  return 1.78   # going for the win
        return 0.35                          # truly impossible -> bat for draw

    # 4-5 down (5-6 in hand): push if the win is realistic, else protect
    if wickets_left >= 5:
        if rpo_needed < 3.0:  return 1.10
        if rpo_needed < 4.5:  return 1.35
        if rpo_needed < 6.0:  return 1.45   # last real push
        return 0.24                          # win gone + wickets thinning -> save it

    # 6-7 down (3-4 in hand): tail exposed - the draw is the floor
    if wickets_left >= 3:
        if rpo_needed < 2.2:  return 0.85   # gettable - knock it off carefully
        return 0.18                          # otherwise dig in for the draw

    # 8-9 down (1-2 in hand): survive - unless a tiny target is right there
    if rpo_needed < 2.0:
        return 0.70
    return 0.16

# ---- Shot selection ----
def _get_delivery(bowler: dict, innings: TestInnings) -> str:
    if "Spin" in bowler["role"]:
        if "Off" in bowler["role"]:
            opts = ["Off spin","Carrom","Arm ball","Doosra","Top spin","Mystery"]
        else:
            opts = ["Leg spin","Googly","Flipper","Drifter","Slider","Mystery"]
        if innings.mystery_bowled_this_over and "Mystery" in opts:
            opts.remove("Mystery")
        deliv = random.choice(opts)
        if deliv == "Mystery":
            innings.mystery_bowled_this_over = True
        return deliv
    if random.random() < 0.08:
        return random.choice(["Off Cutter", "Leg Cutter", "Knuckle"])
    swing = random.choice(["Inswing","Outswing","Seam","Fast","Slow"])
    length = random.choice(["Bouncer","Full","Good length","Yorker","Short"])
    return f"{swing} {length}"

# Batting-style tempo multiplier applied to match intent (syncs Test SR with style):
# Aggressors push the tempo up, Anchors grind it down, bowlers (Wicket-Takers) just survive.
_ARCH_TEMPO = {"Vaibhav": 1.34, "Aggressor": 1.18, "Finisher": 1.06, "Anchor": 0.84, "Wicket-Taker": 0.74}

# Wicket multiplier when a batter is in full survival/block-for-the-draw mode. Low enough
# that blocking is genuinely hard to break in a short defence, high enough that a long
# rearguard (80+ overs) realistically gets prised open - so 3rd-innings declarations win.
_SURVIVAL_WKT = 0.52


def _get_shot(deliv: str, is_collapse: bool, balls_faced: int, archetype: str, intent: float, bat_position: int = 0) -> str:
    is_new  = balls_faced < 15
    is_set  = balls_faced > 50
    is_tail = bat_position >= 7   # No. 8-11: different shot instincts

    # Tail-enders: survival first, no expansive drives against swing
    if is_tail:
        if intent < 0.5:
            return random.choices(["Block", "Leave", "Defensive"], [50, 35, 15])[0]
        if "Bouncer" in deliv or "Short" in deliv:
            return random.choices(["Duck", "Leave", "Block"], [45, 40, 15])[0]
        if "Yorker" in deliv:
            return random.choices(["Block", "Defensive"], [65, 35])[0]
        # Never drive at swing - tail-enders prod/leave it
        if "Outswing" in deliv or "Inswing" in deliv or "Seam" in deliv:
            return random.choices(["Block", "Leave", "Defensive"], [42, 38, 20])[0]
        if deliv in SPIN_SHOT_MATRIX:
            return random.choices(["Block", "Leave", "Defensive", "Sweep"], [38, 28, 22, 12])[0]
        # Other pace: mostly defend, rare prod drive
        return random.choices(["Block", "Leave", "Defensive", "Drive"], [44, 30, 20, 6])[0]

    # Survival mode: leave/block everything
    if intent < 0.35:
        if "Bouncer" in deliv or "Short" in deliv:
            return random.choices(["Leave","Duck","Block"], [50,35,15])[0]
        if "Yorker" in deliv:
            return random.choices(["Block","Defensive"], [65,35])[0]
        return random.choices(["Block","Defensive","Leave","Drive"], [40,35,20,5])[0]

    force_ag = intent >= 1.35
    # Collapse: defensive regardless of intent
    if is_collapse and not force_ag:
        if "Bouncer" in deliv or "Short" in deliv:
            return random.choices(["Leave","Duck","Block"], [45,35,20])[0]
        if "Yorker" in deliv:
            return random.choices(["Block","Defensive","Drive"], [40,35,25])[0]
        return random.choices(["Block","Defensive","Leave","Drive"], [35,30,20,15])[0]

    # Aggressive intent
    if force_ag:
        if "Bouncer" in deliv or "Short" in deliv:
            return random.choices(["Pull","Hook","Cut","Duck"], [40,25,25,10])[0]
        if "Yorker" in deliv:
            return random.choices(["Drive","Flick","Block"], [50,35,15])[0]
        if "Full" in deliv:
            return random.choices(["Drive","Loft","Flick","Scoop"], [45,25,20,10])[0]
        if deliv in SPIN_SHOT_MATRIX:
            return random.choices(["Sweep","Loft","Drive","Cut"], [35,30,25,10])[0]
        return random.choices(["Drive","Pull","Cut","Loft"], [35,25,25,15])[0]

    # New batter: careful but not paralysed
    if is_new:
        if "Bouncer" in deliv or "Short" in deliv:
            return random.choices(["Leave","Duck","Block","Pull"], [35,25,25,15])[0]
        if "Yorker" in deliv:
            return random.choices(["Block","Defensive","Drive"], [45,30,25])[0]
        if deliv in SPIN_SHOT_MATRIX:
            return random.choices(["Block","Leave","Drive","Flick"], [25,15,35,25])[0]
        return random.choices(["Block","Defensive","Leave","Drive","Cut"], [20,18,12,30,20])[0]

    # Set batter standard play
    if "Bouncer" in deliv or "Short" in deliv:
        return random.choices(["Pull","Hook","Duck","Leave","Cut","Block"], [32,18,20,15,10,5])[0]
    if "Yorker" in deliv:
        return random.choices(["Block","Drive","Flick","Defensive"], [30,30,25,15])[0]
    if "Full" in deliv:
        return random.choices(["Drive","Flick","Loft","Block"], [45,25,20,10])[0]
    if deliv in SPIN_SHOT_MATRIX:
        if random.random() < 0.60:
            return random.choice(SPIN_SHOT_MATRIX[deliv])
        return random.choices(["Drive","Sweep","Block","Leave","Cut"], [30,22,20,15,13])[0]
    return random.choices(["Drive","Cut","Flick","Block","Leave","Defensive"], [27,22,20,15,10,6])[0]

# ---- AI bowler selection ----
def get_smart_test_bowler(innings: TestInnings, match: TestMatch) -> Optional[dict]:
    current_over = innings.total_balls // 6
    wear  = _wear_level(match)
    ball_age = innings.ball_age

    # No back-to-back overs: exclude the bowler who bowled the PREVIOUS over. (current_bowler
    # is None at selection time - it's reset at over-end - so we must check prev_bowler.)
    def _nc(p):   return innings.prev_bowler is None or innings.prev_bowler["name"] != p["name"]
    def _main(p): return "Bowler" in p["role"] or "All-Rounder" in p["role"]
    def _pace(p): return "Pace" in p["role"]
    def _spin(p): return "Spin" in p["role"]

    def _fresh_spell(p) -> bool:
        st = innings.bowling_stats[p["name"]]
        # Reset spell if bowler had a rest of 4+ overs
        if current_over - st.last_over_bowled >= 4:
            st.spell_balls = 0
        if _pace(p):
            return st.spell_balls < 54   # 9 overs max per spell
        return st.spell_balls < 126      # 21 overs max per spell

    all_b = innings.bowling_team["players"]

    # Tier-based pool (same priority as T20/ODI)
    pool = [p for p in all_b if _main(p) and _fresh_spell(p) and _nc(p)]
    if not pool:
        pool = [p for p in all_b if _main(p) and _fresh_spell(p)]
    if not pool:
        pool = [p for p in all_b if _fresh_spell(p) and _nc(p)]
    if not pool:
        pool = [p for p in all_b if _fresh_spell(p)]
    if not pool:
        pool = [p for p in all_b if _nc(p)] or all_b
    if not pool:
        return None

    weights = []
    for p in pool:
        st  = innings.bowling_stats[p["name"]]
        ovb = st.balls_bowled // 6
        base = (float(p["bowl"]) / 10.0) ** 2.2
        base *= 3.0 if _main(p) else 0.12

        # Ball condition: prefer pace with new ball, spin in middle
        bc_bonus = _ball_condition_bonus(ball_age, p)
        base *= (1.0 + bc_bonus * 0.10)

        # Phase & wear-based preference
        if ball_age < 180:         # first 30 overs (new ball period)
            if _pace(p): base *= 2.0
            if _spin(p): base *= 0.10
        elif ball_age < 480:       # middle (old ball, spin territory)
            if _spin(p): base *= (2.5 + max(0, wear - 2.0) * 0.8)
            if _pace(p): base *= 0.40
        else:                      # second new ball territory
            if _pace(p): base *= 2.5
            if _spin(p): base *= 0.45

        # Wear level preference
        wb, _, _ = _wear_mods(wear, p)
        base *= (1.0 + wb * 0.06)

        # Pitch type
        if match.pitch in ("Turning","Dusty","Dry","Worn","Cracked") and _spin(p):
            base *= 1.6
        if match.pitch in ("Green","Damp","Bouncy") and _pace(p):
            base *= 1.5

        # Weather
        if match.weather == "Overcast" and _pace(p) and ball_age < 180:
            base *= 1.5
        elif match.weather == "Drizzle" and _pace(p) and ball_age < 180:
            base *= 1.4
        elif match.weather in ("Light Rain", "Heavy Rain") and _pace(p) and ball_age < 180:
            base *= 1.8
        elif match.weather == "Thunderstorm" and _pace(p) and ball_age < 180:
            base *= 2.2
        elif match.weather == "Dry Heat" and _spin(p) and ball_age > 180:
            base *= 1.4

        # Pink ball under lights - captains turn to pace at twilight / night
        if getattr(match, "pink_ball", False) and _pace(p) and match.session >= 2:
            base *= (1.6 if match.session == 2 else 1.35)

        # Economy reward
        if ovb >= 5:
            eco = st.economy
            if   eco <= 2.2: base *= 2.2
            elif eco <= 3.5: base *= 1.4
            elif eco >  6.0: base *= 0.45

        weights.append(max(0.5, base))

    return random.choices(pool, weights=weights, k=1)[0]

# ---- Ball math ----
def execute_test_ball(match: TestMatch) -> bool:
    """
    Execute one ball. Returns True if it was a LEGAL delivery (ball counts toward over).
    Returns False for wides (not counted); no-balls give 1 extra but ARE replayed
    (caller must loop until a legal ball is bowled).
    """
    innings  = match.current_innings
    striker  = innings.batting_team["players"][innings.current_striker_idx]
    bowler   = innings.current_bowler
    b_stats  = innings.batting_stats[striker["name"]]
    bow_stats = innings.bowling_stats[bowler["name"]]

    bat_r  = float(striker["bat"])
    bowl_r = float(bowler["bowl"])

    # Form factor: batter builds innings slowly
    bf = b_stats.balls_faced
    if   bf < 12:   bat_r *= 0.85
    elif bf < 30:   bat_r *= 0.93
    elif bf < 80:   bat_r *= 1.00
    elif bf < 160:  bat_r *= 1.08
    elif bf < 250:  bat_r *= 1.04
    else:           bat_r *= 1.02

    # Batting position - tail-enders are less equipped
    # Kept modest: tail bat ratings are already low; we just add a small
    # context penalty for the pressure of batting with the tail.
    position = innings.current_striker_idx
    if position >= 9:       # No. 10, 11 - genuine rabbits
        bat_r -= 0
    elif position >= 7:     # No. 8, 9 - lower-order
        bat_r -= 0

    # Bowler spell fatigue
    if "Pace" in bowler["role"]:
        if bow_stats.spell_balls >= 108:  bowl_r -= 14   # 18+ over spell
        elif bow_stats.spell_balls >= 72: bowl_r -= 7

    # Ball condition (swing / reverse)
    bowl_r += _ball_condition_bonus(innings.ball_age, bowler)

    # Pitch wear
    wear = _wear_level(match)
    wb, bat_pen, wkt_mult = _wear_mods(wear, bowler)
    bowl_r += wb
    bat_r  += bat_pen

    # Pitch type
    cb       = innings.total_balls             # balls in this innings (new-ball periods)
    total_cb = match.total_match_overs * 6    # balls across whole match (cross-innings wear)
    if   match.pitch == "Green"  and "Pace" in bowler["role"]:                  bowl_r += 7
    elif match.pitch == "Flat":                                                  bat_r  += 10
    elif match.pitch == "Dusty"  and "Spin" in bowler["role"]:
        bowl_r += 6;  bat_r -= 4
    elif match.pitch == "Hard"   and "Pace" in bowler["role"]:
        # True bounce all innings: bats beautifully (fast outfield, full value for shots),
        # so it posts BIG scores - results come from the fast tempo + declarations, with
        # the odd extra edge off the carry. A road, never a seamer.
        bowl_r += 2;  bat_r  += 6
    elif match.pitch == "Cracked":
        bowl_r += 5;  bat_r -= 4
    elif match.pitch == "Damp"   and "Pace" in bowler["role"] and cb < 180:
        bowl_r += 7;  bat_r -= 4
    elif match.pitch == "Dead":
        bat_r += 10;  bowl_r -= 5
    elif match.pitch == "Worn"   and "Spin" in bowler["role"] and total_cb > 180:
        bowl_r += 7;  bat_r -= 4
    elif match.pitch == "Turning" and "Spin" in bowler["role"]:
        bowl_r += 8;  bat_r -= 5
    elif match.pitch == "Sticky":
        bowl_r += 8;  bat_r -= 6
    elif match.pitch == "Bouncy" and "Pace" in bowler["role"]:
        bowl_r += 6;  bat_r -= 4
    elif match.pitch == "Slow"   and "Spin" in bowler["role"]:
        bowl_r += 4
    elif match.pitch == "Dry"    and "Spin" in bowler["role"] and total_cb > 150:
        bowl_r += 6;  bat_r -= 4

    # Weather
    new_ball_period = cb < 180
    mid_period      = 180 <= cb < 480
    if   match.weather == "Clear":                                               bat_r  += 4
    elif match.weather == "Overcast" and "Pace" in bowler["role"]:
        if   cb < 90:   bowl_r += 9;  bat_r -= 4   # first 15 overs: heavy swing
        elif cb < 180:  bowl_r += 5;  bat_r -= 2   # overs 15-30: moderate
        elif cb < 300:  bowl_r += 2                 # overs 30-50: fading
        # 50+ overs: no effect
    elif match.weather == "Cloudy"   and "Pace" in bowler["role"]:
        bowl_r += (5 if new_ball_period else 2 if cb < 300 else 0)
    elif match.weather == "Humid"    and "Pace" in bowler["role"]:
        bowl_r += (6 if new_ball_period else 2 if mid_period else 0)
    elif match.weather == "Windy"    and "Pace" in bowler["role"]:
        bowl_r += (5 if new_ball_period else 2)
    elif match.weather == "Dry Heat":
        if "Spin" in bowler["role"] and cb > 180:  bowl_r += 8
        elif "Pace" in bowler["role"]:             bowl_r -= 6
    elif match.weather == "Drizzle" and "Pace" in bowler["role"]:
        bowl_r += (5 if new_ball_period else 2 if cb < 300 else 0)
    elif match.weather == "Light Rain" and "Pace" in bowler["role"]:
        bowl_r += (7 if new_ball_period else 4);  bat_r -= 1
    elif match.weather == "Heavy Rain" and "Pace" in bowler["role"]:
        bowl_r += (9 if new_ball_period else 5);  bat_r -= 3
    elif match.weather == "Thunderstorm" and "Pace" in bowler["role"]:
        bowl_r += (11 if new_ball_period else 6); bat_r -= 4

    # Pink ball (day-night Test): swings & seams under lights for PACE. Twilight
    # (session 2) is the famous danger session; it stays lively at night (session 3).
    # A fresh pink ball is lethal; once it scuffs (lacquer gone) the movement calms.
    if getattr(match, "pink_ball", False) and "Pace" in bowler["role"] and match.session >= 2:
        _fresh = innings.ball_age < 240   # roughly the first 40 overs of the ball
        if match.session == 2:            # twilight - the danger period
            bowl_r += 8 if _fresh else 4
            bat_r  -= 4 if _fresh else 2
        else:                             # session 3 - under lights at night
            bowl_r += 5 if _fresh else 2
            bat_r  -= 2 if _fresh else 1

    # Cap total condition bonus - raised to 18 to let extremes show through
    raw_bowl_bonus = bowl_r - float(bowler["bowl"])
    if raw_bowl_bonus > 18:
        bowl_r = float(bowler["bowl"]) + 18
    raw_bat_bonus = bat_r - float(striker["bat"])
    if raw_bat_bonus > 18:
        bat_r = float(striker["bat"]) + 18
    raw_bat_penalty = float(striker["bat"]) - bat_r
    if raw_bat_penalty > 18:
        bat_r = float(striker["bat"]) - 18

    # No random match-start rating variance in Test format.

    diff = bat_r - bowl_r   # positive = batter advantage

    # Base weights
    # Calibrated to REAL Test cricket: wickets are dear (innings last ~100 overs),
    # run-rate ~3.2, so first innings average ~300-340, matches consume time and
    # ~25-35% are drawn. Conditions still bite (collapses on bowler pitches).
    # At diff=0, neutral 80v80: ~330 runs, wicket every ~62 balls (~105 ov all out).
    dot_w  = max(40.0,  64.0 - diff * 0.26)
    sing_w = max(13.0,  22.0 + diff * 0.055)
    two_w  = 6.0
    thr_w  = 0.4
    four_w = max(0.3,   4.0  + diff * 0.095)
    six_w  = max(0.05,  0.30 + diff * 0.018)
    wkt_w  = max(0.62,  1.55 - diff * 0.045)

    wkt_w *= wkt_mult

    # True-bounce surfaces: pace gets extra carry -> more edges/catches (results, not
    # draws) without choking the run-rate the way a green/turning deck does.
    if "Pace" in bowler["role"]:
        if   match.pitch == "Hard":   wkt_w *= 1.06   # true carry - a few extra edges, still bats well
        elif match.pitch == "Bouncy": wkt_w *= 1.15
        elif match.pitch == "Green":  wkt_w *= 1.08   # seam movement finds the edge
        # Pink ball under lights - extra edges carry to slip/keeper (twilight worst)
        if getattr(match, "pink_ball", False) and match.session >= 2:
            _fresh = innings.ball_age < 240
            if match.session == 2:   wkt_w *= (1.20 if _fresh else 1.10)
            else:                    wkt_w *= (1.12 if _fresh else 1.05)

    # Per-batter intent: pitch/chase baseline × the striker's batting STYLE
    # This is what syncs Test with the other formats - an Aggressor on a road goes
    # into attack mode, an Anchor controls, a tail-ender just survives.
    intent = _batting_intent(match)
    intent *= _ARCH_TEMPO.get(striker.get("archetype", ""), 1.0)
    if innings.current_striker_idx >= 7:      # tail: rein it in further
        intent *= 0.88
    intent = max(0.12, min(1.60, intent))
    if intent < 0.5:          # survival: dig in for the draw - hard, but breakable over time
        four_w *= 0.35; six_w *= 0.20; wkt_w *= _SURVIVAL_WKT; dot_w *= 1.45
    else:
        # Smooth scaling around 1.0: higher intent (flat tracks / chases) rotates more
        # strike and finds the boundary -> fewer dots, faster SR; lower intent grinds.
        f = intent - 1.0
        dot_w  *= max(0.45, 1.0 - f * 0.55)
        sing_w *= (1.0 + f * 0.20)
        two_w  *= (1.0 + f * 0.20)
        four_w *= (1.0 + f * 1.05)
        six_w  *= (1.0 + f * 1.25)
        wkt_w  *= (1.0 + max(0.0, f) * 0.30)   # attacking carries a little more risk

    # Partnership protection
    if innings.partnership_runs > 100:
        wkt_w *= 0.80
    elif innings.partnership_runs > 60:
        wkt_w *= 0.88

    # Collapse: batters tighten up
    is_collapse = innings.wickets >= 4 and innings.partnership_runs < 25
    if is_collapse:
        four_w *= 0.70; six_w *= 0.50; dot_w *= 1.15

    # Delivery + Shot
    if match.current_delivery_selection:
        deliv = match.current_delivery_selection
        match.current_delivery_selection = ""
    else:
        deliv = _get_delivery(bowler, innings)

    if match.current_shot_selection:
        shot = match.current_shot_selection
        match.current_shot_selection = ""
    else:
        shot = _get_shot(deliv, is_collapse, b_stats.balls_faced, striker["archetype"], intent, innings.current_striker_idx)

    bad_shot  = False
    perf_shot = False

    if "Yorker" in deliv:
        if shot in ["Pull","Cut","Duck"]:        bad_shot  = True
        elif shot in ["Block","Defensive"]:      perf_shot = True
    elif "Bouncer" in deliv or "Short" in deliv:
        if shot in ["Drive","Flick"]:            bad_shot  = True
        elif shot in ["Pull","Hook","Duck","Leave"]: perf_shot = True
    elif deliv in ("Off Cutter", "Leg Cutter", "Knuckle"):
        if shot in ["Block","Leave","Defensive"]: perf_shot = True
        elif shot in ["Loft","Scoop"]:            bad_shot  = True
    elif deliv in SPIN_SHOT_MATRIX:
        if shot in SPIN_SHOT_MATRIX[deliv]:      perf_shot = True
        elif shot == "Leave":                    bad_shot  = True
        else:                                    four_w *= 0.3; dot_w *= 1.3
    elif "Full" in deliv:
        if shot in ["Drive","Flick"]:            perf_shot = True

    # Shot modifiers
    if shot in ["Block","Defensive"]:
        dot_w *= 1.35; four_w = 0.2; six_w = 0.0; wkt_w *= 0.45
    elif shot in ["Leave","Duck"]:
        dot_w *= 2.2; sing_w = 0; four_w = 0; six_w = 0; wkt_w *= 1.05
    else:
        if bad_shot:
            wkt_w *= 2.2; four_w *= 0.20; dot_w *= 1.45
        elif perf_shot:
            four_w *= 1.35; wkt_w *= 0.70

        # Batting style - quality nuance only (tempo is already set via intent above):
        # Aggressors take a touch more risk, Anchors are harder to dislodge, Finishers
        # cut loose once the tail is exposed.
        arch = striker.get("archetype", "")
        if arch == "Vaibhav":
            four_w *= 1.35; six_w *= 1.5; wkt_w *= 1.5   # attacks relentlessly, gifts his wicket
        elif arch == "Aggressor":
            wkt_w *= 1.06
        elif arch == "Anchor":
            wkt_w *= 0.82
        elif arch == "Finisher" and innings.wickets >= 6:
            four_w *= 1.15; six_w *= 1.30

    if "Mystery" in deliv:
        wkt_w *= 1.6; dot_w *= 1.35; four_w *= 0.50

    if deliv in ("Off Cutter", "Leg Cutter", "Knuckle"):
        dot_w *= 1.35; four_w *= 0.65; wkt_w *= 1.15
        if deliv == "Knuckle": dot_w *= 1.10; four_w *= 0.85

    # Swing / seam dismissal probability
    if "Outswing" in deliv and shot in ["Drive","Cut"]:  wkt_w *= 1.45; four_w *= 1.1
    elif "Inswing" in deliv and shot in ["Drive","Flick"]: wkt_w *= 1.40
    elif "Seam"    in deliv and shot in ["Drive","Cut","Flick"]: wkt_w *= 1.20
    elif deliv in ("Off Cutter", "Leg Cutter", "Knuckle") and shot in ["Drive","Cut"]: wkt_w *= 1.25; four_w *= 0.85

    # Modern-Test strike rotation
    # Real Test batters milk singles rather than soak up dot after dot, so even
    # bowler-friendly decks tick along at 2-3 an over (and a road nearer 3.5)
    # instead of crawling at 1.5-2.0. Rotate a slice of the remaining DOT weight
    # into SINGLES - this lifts the run-rate and trims the overs an innings eats
    # and bump the WICKET weight in lock-step so runs-per-wicket (hence the innings
    # TOTAL) barely moves: same score, fewer overs. Deliberately skipped on
    # defensive shots and during collapses/survival so a real wobble still grinds
    # (a sub-2 run-rate stays justified ONLY while wickets are actually tumbling).
    if _MODERN_ROTATION and intent >= 0.5 and not is_collapse and shot not in ("Block", "Defensive", "Leave", "Duck"):
        rot       = 0.27
        shifted   = dot_w * rot
        dot_w    -= shifted
        sing_w   += shifted
        wkt_w    *= 1.0 + rot * 1.15   # hold runs-per-wicket -> totals steady, overs fall

    # Hard caps
    four_w = max(0.1, min(four_w, 18.0))
    six_w  = max(0.05, min(six_w,  8.0))
    wkt_w  = max(0.30, min(wkt_w, 18.0))
    dot_w  = max(22.0, min(dot_w, 140.0))

    # Wide (not a legal ball, return False immediately)
    if random.random() < 0.012 and "Yorker" not in deliv:
        innings.total_runs += 1
        innings.extras     += 1
        bow_stats.runs_conceded += 1
        innings.over_log.append(_OV_WD)
        return False   # illegal - caller must re-loop

    # No ball (1 extra, then STILL bowl a legal delivery this call)
    is_no_ball = random.random() < 0.005
    if is_no_ball:
        innings.total_runs += 1
        innings.extras     += 1
        bow_stats.runs_conceded += 1
        # Continue - the ball is still bowled (no-ball delivery in Test is replayed
        # but we count the runs off the bat too; we simplify: just add the extra and proceed)

    # Outcome
    weights = [dot_w, sing_w, two_w, thr_w, four_w, six_w, wkt_w]
    outcome = random.choices(["dot","single","two","three","four","six","wicket"], weights=weights)[0]

    # No-ball: wicket off a no-ball doesn't count
    if is_no_ball and outcome == "wicket":
        outcome = "dot"

    # Legal ball statistics
    # hat-trick bookkeeping: the streak only survives this ball if it's a
    # bowler-credited wicket (run outs and denied no-ball wickets break it)
    _hat_prev = getattr(bow_stats, "hat_streak", 0)
    bow_stats.hat_streak = 0
    b_stats.balls_faced   += 1
    bow_stats.balls_bowled += 1
    bow_stats.spell_balls  += 1
    bow_stats.last_over_bowled = innings.total_balls // 6
    innings.total_balls   += 1
    innings.ball_age      += 1
    innings.last_ball_boundary = False

    # Second new ball availability (after 80 overs = 480 legal balls)
    if innings.ball_age >= 480 and not innings.second_new_ball_taken:
        innings.second_new_ball_taken = True
        innings.ball_age = 0   # fresh ball resets swing clock

    if outcome == "wicket":
        innings.wickets += 1
        innings.fow.append(
            f"{innings.total_runs}-{innings.wickets} ({striker['name']} "
            f"{b_stats.runs_scored}, {innings.overs_str} ov)"
        )
        innings.partnership_runs = 0

        # Run-out (5% of all wickets): no credit to bowler
        if random.random() < 0.05:
            b_stats.dismissal = "run out"
        else:
            if "Outswing" in deliv and shot in ["Drive","Cut"]:
                d = "Caught Behind"
            elif "Inswing" in deliv and shot in ["Drive","Flick"]:
                d = random.choice(["Bowled","LBW"])
            elif bad_shot and "Yorker" in deliv:
                d = "Bowled"
            elif bad_shot and ("Bouncer" in deliv or "Short" in deliv):
                d = "Caught"
            elif deliv in ("Off Cutter","Leg Cutter","Knuckle") and shot in ["Loft","Scoop"]:
                d = "Caught"
            elif deliv in ("Off Cutter","Leg Cutter","Knuckle") and shot in ["Drive","Cut"]:
                d = random.choice(["Caught","Bowled"])
            elif shot in ["Loft","Scoop","Hook"]:
                d = "Caught"
            elif "Spin" in bowler["role"] and perf_shot:
                d = random.choice(["Bowled","Stumped","LBW","Bowled"])
            else:
                d = random.choice(["Bowled","Caught","LBW","Caught","Caught"])

            wk = next((p["name"] for p in innings.bowling_team["players"] if "WK" in p["role"]), "Keeper")
            fielders = [p["name"] for p in innings.bowling_team["players"] if p["name"] != bowler["name"]]
            fielder  = random.choice(fielders) if fielders else "sub"

            if d == "Bowled":
                b_stats.dismissal = f"b. {bowler['name']}"
            elif d == "LBW":
                b_stats.dismissal = f"lbw b. {bowler['name']}"
            elif d == "Stumped":
                b_stats.dismissal = f"st. {wk} b. {bowler['name']}"
            elif d == "Caught Behind":
                b_stats.dismissal = f"c. {wk} b. {bowler['name']}"
            elif d == "Caught":
                # 20% caught-and-bowled
                if random.random() < 0.20:
                    b_stats.dismissal = f"c-and-b {bowler['name']}"
                else:
                    b_stats.dismissal = f"c. {fielder} b. {bowler['name']}"

            bow_stats.wickets_taken += 1
            bow_stats.hat_streak = _hat_prev + 1
            if bow_stats.hat_streak >= 3:
                bow_stats.hattricks = getattr(bow_stats, "hattricks", 0) + 1

        if innings.wickets < 10:
            innings.current_striker_idx = innings.next_batter_idx
            innings.next_batter_idx += 1
        else:
            innings.is_complete = True
    else:
        runs_map = {"dot":0,"single":1,"two":2,"three":3,"four":4,"six":6}
        runs = runs_map[outcome]

        # Leg bye on dot (rare)
        if runs == 0 and random.random() < 0.012:
            lb = random.choices([1,2,4], weights=[65,28,7])[0]
            innings.total_runs += lb
            innings.extras     += lb
        else:
            innings.total_runs     += runs
            innings.partnership_runs += runs
            b_stats.runs_scored    += runs
            bow_stats.runs_conceded += runs
            if runs == 4:
                b_stats.fours += 1;  innings.last_ball_boundary = True
            elif runs == 6:
                b_stats.sixes += 1;  innings.last_ball_boundary = True

        if runs in [1, 3]:
            innings.current_striker_idx, innings.current_non_striker_idx = (
                innings.current_non_striker_idx, innings.current_striker_idx)

    # End of over
    if innings.total_balls % 6 == 0:
        match.over_completed = True
        innings.mystery_bowled_this_over = False
        # End-of-over END CHANGE: ALWAYS switch - a 1/3 off the last ball already
        # crossed the batters mid-ball, so this second switch puts the single-taker
        # BACK on strike ("single to keep the strike"); otherwise the partner faces.
        innings.current_striker_idx, innings.current_non_striker_idx = (
            innings.current_non_striker_idx, innings.current_striker_idx)

        innings.prev_bowler    = innings.current_bowler
        innings.current_bowler = None   # new bowler must be chosen next over

    # Append emoji to current over timeline
    if outcome == "wicket":
        innings.over_log.append(_OV_W)
    elif outcome == "four":
        innings.over_log.append(_OV_4)
    elif outcome == "six":
        innings.over_log.append(_OV_6)
    elif outcome == "single":
        innings.over_log.append(_OV_1)
    elif outcome == "two":
        innings.over_log.append(_OV_2)
    elif outcome == "three":
        innings.over_log.append(_OV_3)
    else:
        innings.over_log.append(_OV_DOT)

    return True   # legal delivery

# ---- Bowler selection for upcoming over ----
def _select_bowler(match: TestMatch):
    innings = match.current_innings

    # Maiden check for the over that just ended
    if innings.prev_bowler:
        st = innings.bowling_stats[innings.prev_bowler["name"]]
        if st.runs_conceded == st.over_run_start:
            st.maidens += 1

    if innings.current_bowler is not None:
        return

    # Reset spell for any bowler who rested 4+ overs
    current_over = innings.total_balls // 6
    for p in innings.bowling_team["players"]:
        st = innings.bowling_stats[p["name"]]
        if current_over - st.last_over_bowled >= 4:
            st.spell_balls = 0

    # Second new ball: if available and not taken yet, take it now
    if innings.ball_age >= 480 and not innings.second_new_ball_taken:
        innings.second_new_ball_taken = True
        innings.ball_age = 0
        match.new_ball_msg = f"🆕 **New ball taken** ({innings.overs_str} overs)"

    bowler = get_smart_test_bowler(innings, match)
    if bowler is None:
        cands = [p for p in innings.bowling_team["players"]
                 if innings.prev_bowler is None or p["name"] != innings.prev_bowler["name"]]
        bowler = random.choice(cands) if cands else innings.bowling_team["players"][0]

    innings.current_bowler = bowler
    # Snapshot runs_conceded for maiden detection at end of over
    innings.bowling_stats[bowler["name"]].over_run_start = (
        innings.bowling_stats[bowler["name"]].runs_conceded
    )


def prepare_over_interactive(match: TestMatch, bowler_name: str):
    """Apply a human bowler choice for interactive mode.
    Handles maiden-check for previous over, spell-resets, second new ball, then sets the bowler."""
    innings = match.current_innings

    # Maiden check for the bowler who just finished
    if innings.prev_bowler:
        st = innings.bowling_stats[innings.prev_bowler["name"]]
        if st.runs_conceded == st.over_run_start:
            st.maidens += 1

    # Reset spell counter for any bowler rested 4+ overs
    current_over = innings.total_balls // 6
    for p in innings.bowling_team["players"]:
        st = innings.bowling_stats[p["name"]]
        if current_over - st.last_over_bowled >= 4:
            st.spell_balls = 0

    # Take second new ball if available
    if innings.ball_age >= 480 and not innings.second_new_ball_taken:
        innings.second_new_ball_taken = True
        innings.ball_age = 0
        match.new_ball_msg = f"🆕 **New ball taken** ({innings.overs_str} overs)"

    bowler = next(p for p in innings.bowling_team["players"] if p["name"] == bowler_name)
    innings.current_bowler = bowler
    innings.bowling_stats[bowler_name].over_run_start = innings.bowling_stats[bowler_name].runs_conceded


# ---- Result helpers ----
def _get_chase_target(match: TestMatch) -> Optional[int]:
    """Returns runs STILL NEEDED in innings 4 by the current batting team."""
    if match.current_innings_idx != 3:
        return None
    inns    = match.innings_list
    curr    = match.current_innings
    bat_nm  = curr.batting_team["name"]
    # Runs batting team scored in ALL previous innings (not including innings 4 in progress)
    t_bat_prev  = sum(i.total_runs for i in inns[:-1] if i.batting_team["name"] == bat_nm)
    # Total runs for fielding team (innings 4 is excluded since they are not batting)
    t_field     = sum(i.total_runs for i in inns if i.batting_team["name"] != bat_nm)
    return max(1, t_field - t_bat_prev + 1)

def _check_result(match: TestMatch) -> Optional[str]:
    inns = match.innings_list
    n    = len([i for i in inns if i.is_complete or i.wickets >= 10])
    if n < 2:
        return None
    t1 = sum(i.total_runs for i in inns if i.batting_team["name"] == match.team1["name"])
    t2 = sum(i.total_runs for i in inns if i.batting_team["name"] == match.team2["name"])

    if n == 2 and len(inns) == 2:
        # Guard len(inns)==2: if inn3 already started, t1/t2 include its partial runs
        # which would falsely re-trigger the follow-on check.
        if not match.follow_on_enforced:
            lead = inns[0].total_runs - inns[1].total_runs   # raw first-innings scores only
            if lead >= 200:
                match.follow_on_enforced = True
                match.follow_on_msg = (
                    f"\n  *** FOLLOW-ON ENFORCED — {match.team1['name']} lead by {lead} ***\n"
                )
        return None

    if n == 3 and len(inns) == 3:
        # Guard len(inns)==3: once the 4th innings is appended, t1/t2 include its
        # in-progress runs - so a chase drawing LEVEL (aggregate equal) would
        # falsely return "Match Tied" and stop a live 4th innings that still has
        # wickets/overs in hand. Let it fall through to None until inn4 completes.
        if match.follow_on_enforced:
            if t1 > t2:
                return f"{match.team1['name']} won by an innings and {t1 - t2} runs"
            return None   # need 4th innings
        else:
            # Normal: team1 just completed their 2nd innings
            if inns[2].batting_team["name"] == match.team1["name"]:
                if t2 > t1:
                    return f"{match.team2['name']} won by {t2 - t1} runs"
                if t1 == t2:
                    return "Match Tied"
            return None   # team2 needs innings 4 to chase

    if n == 4:
        last = inns[-1]
        if t1 == t2:  return "Match Tied"
        winner = match.team1["name"] if t1 > t2 else match.team2["name"]
        margin = abs(t1 - t2)
        if last.wickets < 10:
            return f"{last.batting_team['name']} won by {10 - last.wickets} wickets"
        return f"{winner} won by {margin} runs"

    return None

def _start_next_innings(match: TestMatch):
    n    = len(match.innings_list)
    inns = match.innings_list
    if n == 1:
        match._new_innings(match.team2, match.team1)
    elif n == 2:
        if match.follow_on_enforced:
            match._new_innings(match.team2, match.team1)
        else:
            match._new_innings(match.team1, match.team2)
    elif n == 3:
        last = inns[2].batting_team["name"]
        if last == match.team2["name"]:
            match._new_innings(match.team1, match.team2)
        else:
            match._new_innings(match.team2, match.team1)
    match.current_innings_idx = len(match.innings_list) - 1

# DECLARATION LOGIC

# Typical runs-per-over on each pitch type (used by both declaration and intent)
# Modern-Test strike rotation (see execute_test_ball). On by default; flip off only
# to reproduce the legacy slow-tempo engine for A/B calibration runs.
_MODERN_ROTATION = True

_PITCH_SCORING_RATE: dict = {
    "Dead": 3.8, "Flat": 3.6, "Slow": 3.1, "Hard": 3.3, "Bouncy": 3.1,
    "Green": 2.7, "Damp": 2.7, "Turning": 2.6, "Cracked": 2.5, "Sticky": 2.5,
    "Dusty": 2.8, "Worn": 2.7, "Two-Paced": 3.0, "Soft": 2.8, "Dry": 3.1,
}

def _should_declare(match: TestMatch) -> bool:
    """Return True if the current batting team should declare.

    Based on real Test cricket patterns (research of 100+ Tests):
    - First innings: NEVER declared.
    - Second innings onward: declare when required run rate (lead ÷ overs remaining)
      exceeds 85% of the pitch's natural scoring rate. This prevents soft declarations
      on batting-friendly pitches (e.g. Flat needs RRR ≥ 3.06, not just ≥ 2.5).
    """
    if match.current_innings_idx == 3 or match.result:
        return False

    inn      = match.current_innings
    bat_name = inn.batting_team["name"]

    # First-innings declarations are rare, but real teams DO close a huge total
    # rather than bat forever (e.g. 7/600 dec) - this also caps runaway innings.
    batted_before = sum(
        1 for i in match.innings_list[:-1]
        if i.batting_team["name"] == bat_name and i.is_complete
    )
    if batted_before == 0:
        # Even a flat/dead road won't be batted forever - close a massive total.
        inn_overs = inn.total_balls / 6
        if inn.total_runs >= 550 and inn_overs >= 125:
            return True
        return False

    # Lead from batting team's perspective
    inns = match.innings_list
    t1   = sum(i.total_runs for i in inns if i.batting_team["name"] == match.team1["name"])
    t2   = sum(i.total_runs for i in inns if i.batting_team["name"] == match.team2["name"])
    lead = (t1 - t2) if bat_name == match.team1["name"] else (t2 - t1)
    # Overs remaining = full future sessions + remainder of current session
    sessions_after = 15 - (match.day - 1) * 3 - match.session
    overs_left     = sessions_after * 30 + max(0, 30 - match.overs_in_session)

    typical_rpo = _PITCH_SCORING_RATE.get(match.pitch, 3.0)

    # A captain declares the 3rd innings to SET A TARGET and still leave enough overs to
    # bowl the opposition out. Two things matter: the lead must be defensible, and there
    # must be time to take 10 wickets. Declaring too late (over-batting) is what kills
    # results - so once the lead is safe we close and attack.
    #
    # Defensible-lead floor scales with the pitch (a flat road needs more runs in the
    # bank than a turner): Dead 206 · Flat 192 · default 150 · Green 129 · Turning 122.
    min_lead = 170 + (typical_rpo - 3.0) * 70   # neutral 170 · Flat 212 · Turning 142
    if lead < min_lead:
        return False

    # Almost no time left: declare with whatever defensible lead we have and force a result.
    if overs_left < 20:
        return True

    # The cardinal rule: NEVER set a soft, gettable target. The opponent must be asked to
    # chase at ABOVE the pitch's par rate - a below-par asking rate is a free stroll that
    # loses Tests, so a captain bats on instead. With plenty of time we hold out for a
    # clearly-tough target (≥ 1.10× par); as the clock runs down we'll accept par.
    asking = lead / overs_left
    par    = typical_rpo
    if overs_left >= 45:
        return asking >= par * 1.10
    return asking >= par * 1.0


# ---- Simulation modes ----
MAX_SESSION_OVERS = 30

def _rain_overs_lost(weather: str, available: int) -> int:
    """Return overs lost to rain interruption at the start of a session (0 = no rain)."""
    prob = {"Drizzle": 0.25, "Light Rain": 0.50, "Heavy Rain": 0.80, "Thunderstorm": 0.92}.get(weather, 0.0)
    if prob == 0.0 or random.random() > prob:
        return 0
    max_loss = {"Drizzle": 5, "Light Rain": 12, "Heavy Rain": available, "Thunderstorm": available}
    return min(available, random.randint(1, max(1, max_loss.get(weather, 5))))


def simulate_session(match: TestMatch) -> str:
    """Simulate up to (30 − already_bowled) overs in the current session.
    Stops at the session boundary OR when the innings ends - whichever comes first.
    Does NOT carry over into the next innings; the caller (simulate_match /
    simulate_innings / the Discord buttons) handles innings transitions."""

    innings = match.current_innings

    # Advance past already-complete innings without bowling
    if innings.is_complete:
        if match.overs_in_session >= 30:
            match.advance_session()
        return ""

    remaining = 30 - match.overs_in_session
    if remaining <= 0:
        match.advance_session()
        return ""

    # Rain interruption: lose some overs at the start of this session
    rain_lost = _rain_overs_lost(match.weather, remaining)
    rain_note = ""
    if rain_lost:
        if rain_lost >= remaining:
            # Entire session washed out
            match.overs_in_session = 30
            time_up = match.advance_session() if match.overs_in_session >= 30 else False
            label = f"Day {match.day}, {SESSION_NAMES[match.session]} Session"
            msg = f"\n{'═'*57}\n  {label}\n{'─'*57}\n  🌧️  Session washed out — {rain_lost} overs lost to rain.\n{'═'*57}"
            if time_up:
                msg += "\n  *** DAY 5 COMPLETE — MATCH DRAWN ***"
            return msg
        remaining  -= rain_lost
        rain_note   = f"  🌧️  {rain_lost} over(s) lost to rain — {remaining} overs available this session.\n"

    sess_label    = f"Day {match.day}, {SESSION_NAMES[match.session]} Session"
    session_overs = 0   # overs bowled in this particular call
    start_runs    = innings.total_runs
    start_wkts    = innings.wickets
    start_balls   = innings.total_balls
    fallen: list  = []

    while session_overs < remaining:
        # Innings complete - stop session, no carry-over
        if innings.is_complete or innings.wickets >= 10:
            innings.is_complete = True
            res = _check_result(match)
            if match.follow_on_msg:
                pass   # caller reads follow_on_msg from match
            if res:
                match.result = res
            break

        # Chase target reached mid-over
        if match.current_innings_idx == 3:
            tgt = _get_chase_target(match)
            if tgt is not None and innings.total_runs >= tgt:
                innings.is_complete = True
                break

        _select_bowler(match)
        innings.over_log = []          # reset timeline for each new over
        wkts_before = innings.wickets

        legal_balls_this_over = 0
        while legal_balls_this_over < 6:
            if innings.is_complete or innings.wickets >= 10:
                innings.is_complete = True
                break
            legal = execute_test_ball(match)
            if legal:
                legal_balls_this_over += 1
            if match.current_innings_idx == 3:
                tgt = _get_chase_target(match)
                if tgt is not None and innings.total_runs >= tgt:
                    innings.is_complete = True
                    break
            if match.over_completed:
                match.over_completed = False
                session_overs           += 1
                match.overs_in_session  += 1
                match.total_match_overs += 1
                break

        for i in range(wkts_before, innings.wickets):
            if i < len(innings.fow):
                fallen.append(innings.fow[i])

        # Declaration check after each completed over
        if not innings.is_complete and _should_declare(match):
            innings.is_complete = True
            innings.declared    = True

    # Build text summary (used by simulate_match text output)
    runs_scored  = innings.total_runs  - start_runs
    wkts_fallen  = innings.wickets     - start_wkts
    balls_played = innings.total_balls - start_balls
    ovs   = balls_played // 6
    balls = balls_played % 6
    rr    = round((runs_scored / max(1, balls_played)) * 6, 2)

    lines = [
        f"\n{'═'*57}",
        f"  {sess_label}",
        f"{'─'*57}",
    ]
    if rain_note:
        lines.append(rain_note.strip())
        lines.append(f"{'─'*57}")
    lines += [
        f"  {innings.batting_team['name']}  —  Innings {innings.innings_num}",
        f"  Score :  {innings.total_runs}/{innings.wickets}  ({innings.overs_str} ov)",
        f"  Session:  +{runs_scored} runs / {wkts_fallen} wkts  ({ovs}.{balls} ov @ {rr} RPO)",
    ]
    if fallen:
        lines.append(f"  FoW   :  " + " | ".join(fallen))
    if innings.is_complete:
        tag = "DECLARED" if innings.declared else "INNINGS COMPLETE"
        lines.append(f"  *** {tag} ***")
    lines.append(f"{'═'*57}")

    # Advance session only when the full 30 overs are done
    if match.overs_in_session >= 30:
        time_up = match.advance_session()
        if time_up:
            lines.append(f"  *** DAY 5 COMPLETE — MATCH DRAWN ***")

    return "\n".join(lines)


def simulate_one_over_verbose(match: TestMatch) -> tuple:
    """Simulate one over (or remaining balls of a partial over) with emoji timeline.
    Returns (text, innings_ended). Updates overs_in_session / total_match_overs.
    Does NOT advance the session - caller checks overs_in_session >= 30."""
    innings = match.current_innings
    if innings.is_complete:
        return "", True

    # Mid-over support: if balls are already done in the current over, keep the
    # existing bowler and start the legal-ball counter from where we are.
    balls_done = innings.total_balls % 6
    if balls_done > 0 and innings.current_bowler is not None:
        bowler = innings.current_bowler        # continue same bowler
    else:
        innings.over_log = []                  # fresh over - reset timeline
        _select_bowler(match)
        bowler = innings.current_bowler

    ov_num        = innings.total_balls // 6   # 0-indexed
    ov_start_runs = innings.total_runs
    ov_start_wkts = innings.wickets
    legal_balls   = balls_done                 # count from where we are

    while legal_balls < 6:
        if innings.is_complete or innings.wickets >= 10:
            innings.is_complete = True
            break
        if match.current_innings_idx == 3:
            tgt = _get_chase_target(match)
            if tgt is not None and innings.total_runs >= tgt:
                innings.is_complete = True
                break

        legal = execute_test_ball(match)
        if legal:
            legal_balls += 1

        if match.over_completed:
            match.over_completed    = False
            match.overs_in_session  += 1
            match.total_match_overs += 1
            break

    if not innings.is_complete and _should_declare(match):
        innings.is_complete = True
        innings.declared    = True

    ov_runs   = innings.total_runs - ov_start_runs
    ov_wkts   = innings.wickets    - ov_start_wkts
    timeline  = " ".join(innings.over_log) if innings.over_log else "•"
    footer    = (
        f"{innings.batting_team['name']} "
        f"**{innings.total_runs}/{innings.wickets}** ({innings.overs_str} ov)"
        f"  +{ov_runs}r {ov_wkts}w"
    )
    if innings.is_complete:
        tag = "DECLARED" if getattr(innings, "declared", False) else "ALL OUT"
        footer += f"  **[{tag}]**"

    nb_msg = match.new_ball_msg
    match.new_ball_msg = ""
    text = f"**Ov {ov_num + 1}** ({bowler['name']})\n{timeline}\n{footer}"
    if nb_msg:
        text = nb_msg + "\n" + text
    return text, innings.is_complete


def simulate_n_overs_verbose(match: TestMatch, n: int) -> tuple:
    """Simulate up to n overs with verbose over-by-over commentary.
    Stops at innings end or session boundary. Returns (text, innings_ended)."""
    blocks: list   = []
    innings_ended  = False

    for _ in range(n):
        innings = match.current_innings
        if innings.is_complete:
            innings_ended = True
            break

        remaining = 30 - match.overs_in_session
        if remaining <= 0:
            time_up = match.advance_session()
            blocks.append("☕ **Session complete.**" + (" Day 5 done — Match Drawn." if time_up else ""))
            break

        text, ended = simulate_one_over_verbose(match)
        if text:
            blocks.append(text)

        if ended:
            innings_ended = True
            break

        if match.overs_in_session >= 30:
            time_up = match.advance_session()
            blocks.append("☕ **Session complete.**" + (" Day 5 done — Match Drawn." if time_up else ""))
            break

    return "\n\n".join(blocks), innings_ended


def simulate_one_ball_interactive(match: TestMatch) -> tuple:
    """Execute one legal delivery (wides auto-replayed). Returns
    (description, over_ended, innings_ended, session_ended).
    session_ended=True means overs_in_session >= 30; caller decides on advance_session()."""
    innings = match.current_innings
    if innings.is_complete:
        return "", False, True, False

    if innings.current_bowler is None:
        _select_bowler(match)

    bowler      = innings.current_bowler
    striker     = innings.batting_team["players"][innings.current_striker_idx]
    runs_before = innings.total_runs
    wkts_before = innings.wickets
    balls_before = innings.total_balls

    wide_extras = 0
    legal = False
    while not legal:
        legal = execute_test_ball(match)
        if not legal:
            wide_extras += 1

    ball_label = f"{balls_before // 6}.{balls_before % 6 + 1}"
    runs_this  = innings.total_runs - runs_before - wide_extras

    if innings.wickets > wkts_before:
        b_stats = innings.batting_stats[striker["name"]]
        desc = f"**{ball_label}** WICKET — _{b_stats.dismissal}_"
    elif runs_this == 4:
        desc = f"**{ball_label}** FOUR!  (+4)"
    elif runs_this == 6:
        desc = f"**{ball_label}** SIX!  (+6)"
    elif runs_this == 0 and wide_extras == 0:
        desc = f"**{ball_label}** Dot ball"
    else:
        desc = f"**{ball_label}** {runs_this} run(s)"
    if wide_extras:
        desc += f"  +{wide_extras} wide(s)"

    over_ended = session_ended = False
    if match.over_completed:
        match.over_completed    = False
        match.overs_in_session  += 1
        match.total_match_overs += 1
        over_ended = True
        if not innings.is_complete and _should_declare(match):
            innings.is_complete = True
            innings.declared    = True
        if match.overs_in_session >= 30:
            session_ended = True

    if innings.wickets >= 10:
        innings.is_complete = True
    if match.current_innings_idx == 3:
        tgt = _get_chase_target(match)
        if tgt is not None and innings.total_runs >= tgt:
            innings.is_complete = True

    return desc, over_ended, innings.is_complete, session_ended


def simulate_innings(match: TestMatch) -> str:
    """Simulate the full current innings. Returns innings scorecard."""
    innings = match.current_innings
    while not innings.is_complete and innings.wickets < 10:
        if match.day > 5:
            break
        simulate_session(match)
        if match.current_innings_idx == 3:
            tgt = _get_chase_target(match)
            if tgt is not None and innings.total_runs >= tgt:
                break
    if innings.wickets >= 10:
        innings.is_complete = True
    return _format_scorecard(innings)

def simulate_match(match: TestMatch) -> str:
    """Simulate the full Test match. Returns complete result string."""
    lines = [
        f"\n{'═'*62}",
        f"  TEST MATCH  |  {match.team1['name']}  vs  {match.team2['name']}",
        f"  Pitch: {match.pitch:<12}  Weather: {match.weather}",
        f"{'═'*62}",
    ]

    while True:
        if match.day > 5:
            match.result = match.result or "Match Drawn (time)"
            break

        sc = simulate_innings(match)
        lines.append(sc)

        # Chase won during innings (mid-over win)
        if match.current_innings_idx == 3:
            curr = match.current_innings
            tgt  = _get_chase_target(match)
            if tgt is not None and curr.total_runs >= tgt:
                match.result = f"{curr.batting_team['name']} won by {10 - curr.wickets} wickets"
                break

        res = _check_result(match)
        if match.follow_on_msg:
            lines.append(match.follow_on_msg)
            match.follow_on_msg = ""
        if res:
            match.result = res
            break

        if len(match.innings_list) >= 4:
            match.result = match.result or "Match Drawn"
            break

        if match.day > 5:
            match.result = "Match Drawn (time)"
            break

        _start_next_innings(match)

    lines += [
        f"\n{'═'*62}",
        f"  RESULT :  {match.result or 'Match Drawn'}",
        f"{'═'*62}",
        f"\n  SCORECARD SUMMARY",
    ]
    for inn in match.innings_list:
        lines.append(f"  {inn.batting_team['name']:<30}  {inn.total_runs:>4}/{inn.wickets}"
                     f"  ({inn.overs_str} ov)  RR:{inn.run_rate}")
    pom = _player_of_match(match)
    if pom:
        lines.append(f"\n  Player of the Match :  {pom}")
    return "\n".join(lines)

# ---- Output ----
def _format_scorecard(innings: TestInnings) -> str:
    lines = [
        f"\n{'─'*62}",
        f"  {innings.batting_team['name'].upper()}  —  INNINGS {innings.innings_num}",
        f"{'─'*62}",
    ]
    for p in innings.batting_team["players"]:
        st = innings.batting_stats[p["name"]]
        if st.balls_faced == 0 and st.dismissal == "not out":
            continue
        sr  = round(st.runs_scored / st.balls_faced * 100, 1) if st.balls_faced else 0
        bnd = f"4s:{st.fours} 6s:{st.sixes}"
        lines.append(
            f"  {p['name']:<22} {st.dismissal:<30} {st.runs_scored:>4}"
            f" ({st.balls_faced}b)  SR:{sr:<6} {bnd}"
        )
    lines += [
        f"  {'·'*60}",
        f"  Extras : {innings.extras}",
        f"  TOTAL  : {innings.total_runs}/{innings.wickets}"
        f"{'(dec)' if innings.declared else ''}  ({innings.overs_str} ov)"
        f"  RR: {innings.run_rate}",
    ]
    if innings.fow:
        lines.append(f"\n  FoW: " + ",  ".join(innings.fow))

    lines += [
        f"\n  {'Bowler':<22} {'O':<7} {'M':<5} {'R':<6} {'W':<4} {'Econ'}",
        f"  {'─'*54}",
    ]
    for p in innings.bowling_team["players"]:
        st = innings.bowling_stats[p["name"]]
        if st.balls_bowled == 0:
            continue
        lines.append(
            f"  {p['name']:<22} {st.overs_str:<7} {st.maidens:<5}"
            f" {st.runs_conceded:<6} {st.wickets_taken:<4} {st.economy}"
        )
    lines.append(f"{'─'*62}")
    return "\n".join(lines)

def _player_of_match(match: TestMatch) -> str:
    """POTM by whole-match contribution. Impact = aggregate RUNS + 20 per WICKET,
    summed across BOTH innings. There is deliberately NO strike-rate or economy
    term - Test cricket values accumulation and wicket-taking, not tempo, so slow
    batters aren't penalised and tight bowlers aren't artificially boosted.
    All-rounders are rewarded since runs and wickets add into one impact score."""
    agg = {}   # name -> {"runs", "wkts"} aggregated over the match
    for inn in match.innings_list:
        for n, st in inn.batting_stats.items():
            agg.setdefault(n, {"runs": 0, "wkts": 0})["runs"] += st.runs_scored
        for n, st in inn.bowling_stats.items():
            agg.setdefault(n, {"runs": 0, "wkts": 0})["wkts"] += st.wickets_taken
    if not agg:
        return ""
    name, a = max(agg.items(), key=lambda kv: kv[1]["runs"] + kv[1]["wkts"] * 20)
    parts = []
    if a["runs"] > 0: parts.append(f"{a['runs']} runs")
    if a["wkts"] > 0: parts.append(f"{a['wkts']} wkts")
    return f"{name} ({' & '.join(parts)})" if parts else name

# ---- TEAMS (good vs bad ratings clearly matter) ----
TEAM_ALPHA = {
    "name": "Thunderstrike XI",
    "players": [
        {"name": "R. Sharma",      "bat": 89, "bowl": 38, "role": "Batter",               "archetype": "Aggressor"},
        {"name": "D. Warner",      "bat": 87, "bowl": 30, "role": "Batter",               "archetype": "Aggressor"},
        {"name": "K. Williamson",  "bat": 93, "bowl": 42, "role": "Batter",               "archetype": "Anchor"},
        {"name": "S. Smith",       "bat": 95, "bowl": 38, "role": "Batter",               "archetype": "Anchor"},
        {"name": "B. Stokes",      "bat": 84, "bowl": 82, "role": "All-Rounder Pace",     "archetype": "Finisher"},
        {"name": "R. Pant",        "bat": 80, "bowl": 20, "role": "Batter WK",            "archetype": "Aggressor"},
        {"name": "R. Jadeja",      "bat": 68, "bowl": 83, "role": "All-Rounder Off Spin", "archetype": "Anchor"},
        {"name": "J. Bumrah",      "bat": 22, "bowl": 94, "role": "Bowler Pace",          "archetype": "Wicket-Taker"},
        {"name": "P. Cummins",     "bat": 44, "bowl": 91, "role": "Bowler Pace",          "archetype": "Wicket-Taker"},
        {"name": "N. Lyon",        "bat": 28, "bowl": 85, "role": "Bowler Off Spin",      "archetype": "Wicket-Taker"},
        {"name": "M. Starc",       "bat": 36, "bowl": 87, "role": "Bowler Pace",          "archetype": "Finisher"},
    ],
}
TEAM_BETA = {
    "name": "Cyclone Warriors",
    "players": [
        {"name": "Z. Crawley",     "bat": 72, "bowl": 28, "role": "Batter",               "archetype": "Aggressor"},
        {"name": "D. Elgar",       "bat": 76, "bowl": 40, "role": "Batter",               "archetype": "Anchor"},
        {"name": "M. Labuschagne", "bat": 85, "bowl": 52, "role": "Batter",               "archetype": "Anchor"},
        {"name": "J. Root",        "bat": 92, "bowl": 62, "role": "Batter",               "archetype": "Anchor"},
        {"name": "J. Bairstow",    "bat": 78, "bowl": 18, "role": "Batter WK",            "archetype": "Aggressor"},
        {"name": "S. Curran",      "bat": 68, "bowl": 74, "role": "All-Rounder Pace",     "archetype": "Finisher"},
        {"name": "R. Ashwin",      "bat": 60, "bowl": 87, "role": "Bowler Off Spin",      "archetype": "Wicket-Taker"},
        {"name": "S. Broad",       "bat": 38, "bowl": 84, "role": "Bowler Pace",          "archetype": "Finisher"},
        {"name": "J. Anderson",    "bat": 22, "bowl": 89, "role": "Bowler Pace",          "archetype": "Wicket-Taker"},
        {"name": "K. Rabada",      "bat": 33, "bowl": 90, "role": "Bowler Pace",          "archetype": "Wicket-Taker"},
        {"name": "Shakib Al H.",   "bat": 63, "bowl": 77, "role": "All-Rounder Off Spin", "archetype": "Anchor"},
    ],
}

# ---- MAIN - demonstrates all three modes ----
def run_test_simulation():
    pitch   = random.choice(PITCH_TYPES)
    weather = random.choice(WEATHER_TYPES)
    print(f"  Pitch: {pitch}   |   Weather: {weather}\n")

    print("=" * 62)
    print("  MODE 1 — SIMULATE SESSION  (one session, innings 1)")
    print("=" * 62)
    m1 = TestMatch(TEAM_ALPHA, TEAM_BETA, pitch, weather)
    print(simulate_session(m1))

    input("\n  [Press Enter to run Mode 2 — Full Innings...]\n")

    print("=" * 62)
    print("  MODE 2 — SIMULATE INNINGS  (full innings 1)")
    print("=" * 62)
    m2 = TestMatch(TEAM_ALPHA, TEAM_BETA, pitch, weather)
    print(simulate_innings(m2))

    input("\n  [Press Enter to run Mode 3 — Full Match...]\n")

    print("=" * 62)
    print("  MODE 3 — SIMULATE FULL MATCH")
    print("=" * 62)
    m3 = TestMatch(TEAM_ALPHA, TEAM_BETA, pitch, weather)
    print(simulate_match(m3))

if __name__ == "__main__":
    run_test_simulation()