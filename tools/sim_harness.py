"""
Standalone Monte Carlo harness for CricVerse simulation engines.
Replicates the minimal data model from bot.py (no Discord dependency)
and the _run_full_match_sync loop, so we can run thousands of matches
and measure win-rates / score distributions for calibration.
"""
import random
import statistics
import sys, os
# Allow running from the repo root (e.g. `python tools/sim_harness.py`) - add root to the import path.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from engine.t20_simulation import execute_ball_math_t20, get_smart_ai_bowler_t20
from engine.odi_simulation import execute_ball_math_odi, get_smart_ai_bowler_odi


# minimal mirrors of bot.py classes
class BatterStats:
    def __init__(self, profile):
        self.profile = profile
        self.runs_scored = 0
        self.balls_faced = 0
        self.dismissal = "not out"
        self.fours = 0
        self.sixes = 0
        self.form_factor = random.uniform(0.96, 1.04)


class BowlerStats:
    def __init__(self, profile):
        self.profile = profile
        self.runs_conceded = 0
        self.balls_bowled = 0
        self.wickets_taken = 0
        self.is_subbed_out = False
        self.form_factor = random.uniform(0.96, 1.04)


class InningsState:
    def __init__(self, batting_team, bowling_team):
        self.batting_team = batting_team
        self.bowling_team = bowling_team
        self.total_runs = 0
        self.wickets = 0
        self.total_balls = 0
        self.over_log = []
        self.partnership_runs = 0
        self.extras = 0
        self.last_ball_boundary = False
        self.current_striker_idx = 0
        self.current_non_striker_idx = 1
        self.next_batter_idx = 2
        self.current_bowler = None
        self.batting_stats = {p["name"]: BatterStats(p) for p in batting_team["players"]}
        self.bowling_stats = {p["name"]: BowlerStats(p) for p in bowling_team["players"]}


class CricketMatch:
    def __init__(self, team1, team2, format_overs=20, pitch="Flat", weather="Clear"):
        self.team1 = team1
        self.team2 = team2
        self.format_overs = format_overs
        self.max_balls = format_overs * 6
        self.pitch = pitch
        self.weather = weather
        self.is_ai_game = False
        self.innings1 = None
        self.innings2 = None
        self.current_innings_num = 1
        self.current_innings = None
        self.simulation_mode = "whole_match"
        self.current_delivery_selection = None
        self.current_shot_selection = None
        self.temp_variation = None
        self.last_commentary = ""
        self.last_commentary_prefix = ""
        self.free_hit = False
        # Impact-player rule (off unless a caller enables it). Mirrors bot.py.
        self.impact_player = False
        self.t1_impact_used = False
        self.t2_impact_used = False
        self.t1_subs = []
        self.t2_subs = []
        self.t1_impact_sub_name = None
        self.t2_impact_sub_name = None
        self.impact_log = []   # (team_id, over, "bat"/"bowl", sub_name) per swap

    def get_striker_user_id(self):  # never AI-batting branch in harness
        return None


def get_smart_ai_bowler(innings, pitch, weather, format_overs):
    if format_overs == 50:
        return get_smart_ai_bowler_odi(innings, pitch, weather, format_overs)
    return get_smart_ai_bowler_t20(innings, pitch, weather, format_overs)


def execute_ball_math(match):
    if match.format_overs == 50:
        return execute_ball_math_odi(match)
    return execute_ball_math_t20(match)


# Impact-player rule (ported from bot.py; AI subs a 12th man at over breaks)
def swap_impact_player(match, team_id, out_name, in_player):
    if team_id == 1:
        match.t1_impact_used = True; match.t1_impact_sub_name = in_player["name"]; team = match.team1
    else:
        match.t2_impact_used = True; match.t2_impact_sub_name = in_player["name"]; team = match.team2
    inn = match.current_innings
    if not inn:
        return
    is_bat_swap = (inn.batting_team["name"] == team["name"])
    match.impact_log.append((team_id, inn.total_balls // 6,
                             "bat" if is_bat_swap else "bowl", in_player["name"]))
    if inn.batting_team["name"] == team["name"]:                       # batting impact
        if in_player["name"] not in inn.batting_stats:
            pos = getattr(inn, "next_batter_idx", len(inn.batting_team["players"]))
            inn.batting_team["players"].insert(pos, in_player)
            inn.batting_stats[in_player["name"]] = BatterStats(in_player)
        b = inn.batting_stats.get(out_name)
        if b:
            if b.dismissal == "not out" and b.balls_faced == 0:
                b.dismissal = "Subbed Out"
            elif b.dismissal == "not out":
                b.dismissal = "Retired (Sub)"
    else:                                                              # bowling impact
        if in_player["name"] not in inn.bowling_stats:
            if in_player not in inn.bowling_team["players"]:
                inn.bowling_team["players"].append(in_player)
            inn.bowling_stats[in_player["name"]] = BowlerStats(in_player)
        bw = inn.bowling_stats.get(out_name)
        if bw:
            bw.is_subbed_out = True


def _ai_batting_impact(match, innings, team_num, subs):
    overs = innings.total_balls // 6
    wkts = innings.wickets
    max_b = match.max_balls
    is_inn1 = (match.current_innings_num == 1)
    bat_subs = [s for s in subs if "Batter" in s["role"] or "All-Rounder" in s["role"]] or subs
    best_sub = max(bat_subs, key=lambda x: x["bat"])
    players = innings.batting_team["players"]
    upcoming = [p for p in players[innings.next_batter_idx:]
                if innings.batting_stats[p["name"]].dismissal == "not out"]
    if not upcoming:
        return
    if is_inn1:
        swappable = [p for p in upcoming if "Bowler" not in p["role"]]
        if not swappable:
            return
    else:
        swappable = upcoming
    next_up = swappable[0]
    worst_up = min(swappable, key=lambda x: x["bat"])
    if not is_inn1 and next_up["bat"] < 60 and best_sub["bat"] > next_up["bat"] + 10:
        return swap_impact_player(match, team_num, next_up["name"], best_sub)
    if wkts >= 2 and overs < 6 and best_sub["bat"] >= 72 and best_sub["bat"] > worst_up["bat"] + 12:
        return swap_impact_player(match, team_num, worst_up["name"], best_sub)
    if wkts >= 3 and overs >= 5 and innings.total_balls < max_b - 18 and best_sub["bat"] > worst_up["bat"] + 10:
        return swap_impact_player(match, team_num, worst_up["name"], best_sub)
    if not is_inn1:
        balls_left = max_b - innings.total_balls
        if balls_left > 0:
            target = getattr(match, "target", match.innings1.total_runs + 1)
            rrr = (target - innings.total_runs) / balls_left * 6
            if rrr >= 9 and best_sub["bat"] >= 75 and best_sub["bat"] > worst_up["bat"] + 8:
                return swap_impact_player(match, team_num, worst_up["name"], best_sub)
    if innings.total_balls >= max_b - 24 and best_sub["bat"] > worst_up["bat"] + 8:
        return swap_impact_player(match, team_num, worst_up["name"], best_sub)


def _ai_bowling_impact(match, innings, team_num, subs):
    balls = innings.total_balls
    max_b = match.max_balls
    bowl_subs = [s for s in subs if "Bowler" in s["role"] or "All-Rounder" in s["role"]] or subs
    best_sub = max(bowl_subs, key=lambda x: x["bowl"])
    curr = innings.current_bowler
    cands = [p for p in innings.bowling_team["players"]
             if not curr or p["name"] != curr["name"]]
    if not cands:
        return
    worst = min(cands, key=lambda x: x["bowl"])
    if best_sub["bowl"] <= worst["bowl"] + 8:
        return
    if balls >= max_b - 30:
        return swap_impact_player(match, team_num, worst["name"], best_sub)
    if match.current_innings_num == 2 and balls >= max_b - 36:
        balls_left = max_b - balls
        if balls_left > 0:
            target = getattr(match, "target", match.innings1.total_runs + 1)
            rrr = (target - innings.total_runs) / balls_left * 6
            if rrr < 7:
                return swap_impact_player(match, team_num, worst["name"], best_sub)
    if balls >= max_b - 12:
        return swap_impact_player(match, team_num, worst["name"], best_sub)


def try_ai_impact_player(match, innings):
    """Over-boundary hook: each team uses its Impact Player once, when the AI
    tactic fires. No is_ai_game gate (the harness is always AI vs AI)."""
    if not getattr(match, "impact_player", False):
        return
    for team_num in (1, 2):
        if getattr(match, f"t{team_num}_impact_used", False):
            continue
        team = match.team1 if team_num == 1 else match.team2
        subs = getattr(match, f"t{team_num}_subs", [])
        if not subs:
            continue
        if innings.batting_team["name"] == team["name"]:
            _ai_batting_impact(match, innings, team_num, subs)
        else:
            _ai_bowling_impact(match, innings, team_num, subs)


def run_full_match(match):
    match.t1_subs = match.team1.get("subs", [])
    match.t2_subs = match.team2.get("subs", [])

    def _sim_innings(innings):
        while True:
            if innings.wickets >= 10 or innings.total_balls >= match.max_balls:
                break
            if match.current_innings_num == 2 and innings.total_runs >= getattr(match, "target", innings.total_runs + 1):
                break
            if innings.total_balls % 6 == 0 and not innings.over_log:
                bowler = get_smart_ai_bowler(innings, match.pitch, match.weather, match.format_overs)
                if not bowler:
                    break
                innings.current_bowler = bowler
            execute_ball_math(match)
            if innings.total_balls % 6 == 0 and innings.total_balls > 0:
                innings.over_log.clear()
                innings.bouncers_in_over = 0
                innings.cutters_in_over = 0
                innings.mystery_bowled_this_over = False
                try_ai_impact_player(match, innings)    # impact sub at over breaks

    match.innings1 = InningsState(match.team1, match.team2)
    match.current_innings = match.innings1
    match.current_innings_num = 1
    _sim_innings(match.innings1)

    match.target = match.innings1.total_runs + 1
    match.innings2 = InningsState(match.team2, match.team1)
    match.current_innings = match.innings2
    match.current_innings_num = 2
    _sim_innings(match.innings2)
    return match


# team builder with controlled ratings
def build_team(name, bat_rating, bowl_rating, noise=3):
    """A realistic XI: 6 specialist batters (1 WK), 1 pace AR, 4 bowlers (2 pace, 2 spin)."""
    def j(r):  # jitter
        return max(20, min(99, int(round(random.gauss(r, noise)))))

    tail_bat = max(15, bat_rating - 45)  # bowlers can't bat much
    players = [
        {"name": f"{name}_OP1", "bat": j(bat_rating), "bowl": j(35), "role": "Batter", "archetype": "Aggressor"},
        {"name": f"{name}_OP2", "bat": j(bat_rating), "bowl": j(35), "role": "Batter", "archetype": "Anchor"},
        {"name": f"{name}_T3",  "bat": j(bat_rating + 2), "bowl": j(35), "role": "Batter", "archetype": "Anchor"},
        {"name": f"{name}_T4",  "bat": j(bat_rating), "bowl": j(35), "role": "Batter", "archetype": "Aggressor"},
        {"name": f"{name}_WK",  "bat": j(bat_rating - 3), "bowl": j(20), "role": "Batter_WK", "archetype": "Finisher"},
        {"name": f"{name}_AR",  "bat": j(bat_rating - 8), "bowl": j(bowl_rating - 5), "role": "All-Rounder_Pace", "archetype": "Finisher"},
        {"name": f"{name}_T7",  "bat": j(bat_rating - 12), "bowl": j(bowl_rating - 10), "role": "All-Rounder_Spin_Off", "archetype": "Finisher"},
        {"name": f"{name}_PB1", "bat": j(tail_bat), "bowl": j(bowl_rating), "role": "Bowler_Pace", "archetype": "Standard"},
        {"name": f"{name}_PB2", "bat": j(tail_bat), "bowl": j(bowl_rating), "role": "Bowler_Pace", "archetype": "Finisher"},
        {"name": f"{name}_SP1", "bat": j(tail_bat - 5), "bowl": j(bowl_rating), "role": "Bowler_Spin_Leg", "archetype": "Standard"},
        {"name": f"{name}_SP2", "bat": j(tail_bat - 5), "bowl": j(bowl_rating - 2), "role": "Bowler_Spin_Off", "archetype": "Standard"},
    ]
    return {"name": name, "players": players, "subs": []}


def series(team_a_spec, team_b_spec, n=2000, format_overs=20, pitch="Flat", weather="Clear", swap_innings=True):
    """team_x_spec = (bat, bowl). Returns dict of stats. A bats first half, B bats first other half.
    'par' = first-innings totals only (no chase truncation) - the true realism metric."""
    a_wins = b_wins = ties = 0
    a_scores, b_scores = [], []
    par_scores = []   # first-innings totals only
    for i in range(n):
        ta = build_team("A", *team_a_spec)
        tb = build_team("B", *team_b_spec)
        # alternate who bats first to remove toss bias
        if swap_innings and i % 2 == 1:
            m = CricketMatch(tb, ta, format_overs, pitch, weather)
            run_full_match(m)
            s_b, s_a = m.innings1.total_runs, m.innings2.total_runs
        else:
            m = CricketMatch(ta, tb, format_overs, pitch, weather)
            run_full_match(m)
            s_a, s_b = m.innings1.total_runs, m.innings2.total_runs
        par_scores.append(m.innings1.total_runs)
        a_scores.append(s_a)
        b_scores.append(s_b)
        if s_a > s_b:
            a_wins += 1
        elif s_b > s_a:
            b_wins += 1
        else:
            ties += 1
    return {
        "n": n, "a_wins": a_wins, "b_wins": b_wins, "ties": ties,
        "a_win_pct": 100 * a_wins / n, "b_win_pct": 100 * b_wins / n,
        "par_mean": statistics.mean(par_scores), "par_std": statistics.pstdev(par_scores),
        "par_min": min(par_scores), "par_max": max(par_scores),
        "upset_pct": 100 * min(a_wins, b_wins) / n,
    }


def report(label, r):
    print(f"\n=== {label} ===")
    print(f"  A win%: {r['a_win_pct']:.2f}   B win%: {r['b_win_pct']:.2f}   ties: {r['ties']}   UPSET: {r['upset_pct']:.2f}%  (n={r['n']})")
    print(f"  1st-innings PAR: mean {r['par_mean']:.1f}  std {r['par_std']:.1f}  range [{r['par_min']}-{r['par_max']}]")


if __name__ == "__main__":
    random.seed(42)
    N = 4000
    print("################ T20 (20 overs, Flat/Clear) ################")
    report("Equal 85 vs 85", series((85, 85), (85, 85), n=N, format_overs=20))
    report("Slight edge 86/84 vs 82/80", series((86, 84), (82, 80), n=N, format_overs=20))
    report("Clear 90/88 vs 76/74 (14pt)", series((90, 88), (76, 74), n=N, format_overs=20))
    report("Huge 96/94 vs 72/70 (24pt)", series((96, 94), (72, 70), n=N, format_overs=20))
    report("Elite 94/92 vs Poor 70/68", series((94, 92), (70, 68), n=N, format_overs=20))

    print("\n\n################ ODI (50 overs, Flat/Clear) ################")
    report("Equal 85 vs 85", series((85, 85), (85, 85), n=N, format_overs=50))
    report("Slight edge 86/84 vs 82/80", series((86, 84), (82, 80), n=N, format_overs=50))
    report("Clear 90/88 vs 76/74 (14pt)", series((90, 88), (76, 74), n=N, format_overs=50))
    report("Huge 96/94 vs 72/70 (24pt)", series((96, 94), (72, 70), n=N, format_overs=50))
    report("Elite 94/92 vs Poor 70/68", series((94, 92), (70, 68), n=N, format_overs=50))
