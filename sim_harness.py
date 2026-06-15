"""
Standalone Monte Carlo harness for CricVerse simulation engines.
Replicates the minimal data model from bot.py (no Discord dependency)
and the _run_full_match_sync loop, so we can run thousands of matches
and measure win-rates / score distributions for calibration.
"""
import random
import statistics
from t20_simulation import execute_ball_math_t20, get_smart_ai_bowler_t20
from odi_simulation import execute_ball_math_odi, get_smart_ai_bowler_odi


# ---- minimal mirrors of bot.py classes ----
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


def run_full_match(match):
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


# ---- team builder with controlled ratings ----
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
    'par' = first-innings totals only (no chase truncation) — the true realism metric."""
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
