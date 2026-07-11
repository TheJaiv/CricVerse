# Jaiv's reported miracle: chasing 197 on Green, 85/7 in 19 ov, a 36-bat tail
# blocked out 40(80)* alongside a 90-batter and WON vs a 97 attack. This
# recreates that exact match state and measures how often the engine allows it.
# Run from repo root:  python tools/rescue_check.py [n]
import os
import random
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from sim_harness import CricketMatch, InningsState, build_team
from odi_simulation import execute_ball_math_odi, get_smart_ai_bowler_odi

N = int(sys.argv[1]) if len(sys.argv) > 1 else 2000


def chasing_team(name):
    """Top 7 (already out in the scenario), then the 90-rated No.8-partner is
    index 7, the 36-bat tailender index 8, and 30-bat bunnies at 9/10."""
    ps = [
        {"name": f"{name}{i}", "bat": 85, "bowl": 40, "role": "Batter", "archetype": "Standard"}
        for i in range(7)
    ]
    ps.append({"name": f"{name}_HERO", "bat": 90, "bowl": 40, "role": "Batter", "archetype": "Standard"})
    ps.append({"name": f"{name}_T36", "bat": 36, "bowl": 82, "role": "Bowler_Pace", "archetype": "Standard"})
    ps.append({"name": f"{name}_T30a", "bat": 30, "bowl": 82, "role": "Bowler_Spin_Leg", "archetype": "Standard"})
    ps.append({"name": f"{name}_T30b", "bat": 30, "bowl": 82, "role": "Bowler_Pace", "archetype": "Standard"})
    return {"name": name, "players": ps, "subs": []}


def main():
    random.seed(13)
    wins = 0
    tail_scores, tail_balls = [], []
    tail_30plus = tail_60balls = 0
    for _ in range(N):
        chasers = chasing_team("C")
        bowlers = build_team("B", 85, 97, noise=0)
        m = CricketMatch(bowlers, chasers, 50, "Green", "Clear")
        m.innings1 = InningsState(bowlers, chasers)
        m.innings1.total_runs = 196
        m.target = 197
        m.innings2 = InningsState(chasers, bowlers)
        m.current_innings = m.innings2
        m.current_innings_num = 2
        inn = m.innings2
        # ── recreate 85/7 after 19 overs ──
        inn.total_runs = 85
        inn.total_balls = 114
        inn.wickets = 7
        inn.partnership_runs = 4
        for i in range(7):
            inn.batting_stats[f"C{i}"].dismissal = "b. someone"
            inn.batting_stats[f"C{i}"].runs_scored = 11
            inn.batting_stats[f"C{i}"].balls_faced = 15
        hero = inn.batting_stats["C_HERO"]
        hero.runs_scored, hero.balls_faced = 8, 12   # just getting set
        inn.current_striker_idx = 7
        inn.current_non_striker_idx = 8
        inn.next_batter_idx = 9
        while inn.wickets < 10 and inn.total_balls < 300 and inn.total_runs < m.target:
            if inn.total_balls % 6 == 0 and not inn.over_log:
                bw = get_smart_ai_bowler_odi(inn, "Green", "Clear", 50)
                if not bw:
                    break
                inn.current_bowler = bw
            execute_ball_math_odi(m)
            if inn.total_balls % 6 == 0 and inn.total_balls and inn.over_log:
                inn.over_log.clear(); inn.bouncers_in_over = 0
                inn.cutters_in_over = 0; inn.mystery_bowled_this_over = False
        if inn.total_runs >= m.target:
            wins += 1
        t = inn.batting_stats["C_T36"]
        if t.balls_faced > 0:
            tail_scores.append(t.runs_scored)
            tail_balls.append(t.balls_faced)
            if t.runs_scored >= 30: tail_30plus += 1
            if t.balls_faced >= 60: tail_60balls += 1
    n36 = max(1, len(tail_scores))
    print(f"chasing 112 off 31 ov · 85/7 · 90+36 pair vs 97-attack on Green (n={N})")
    print(f"  chase WIN: {100*wins/N:.1f}%   [real feel: ~10-18%]")
    print(f"  36-bat tail: avg {statistics.mean(tail_scores):.1f} off {statistics.mean(tail_balls):.1f} balls")
    print(f"  P(tail 30+ runs) {100*tail_30plus/n36:.1f}%   P(tail faces 60+ balls) {100*tail_60balls/n36:.1f}%"
          f"   [both should be freak-rare, <5%]")


if __name__ == "__main__":
    main()
