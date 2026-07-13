"""
Compare the CURRENT t20 engine vs an OLD backup engine on the same matched
matches: 1st-innings avg, 2nd-innings avg, and bat-first / bowl-first win split.

Lets us tune chase logic toward a 50/50 split WITHOUT drifting the innings
scores away from the backup's calibration.

Usage:
    python tools/compare_chase.py 3000 Sticky
    python tools/compare_chase.py 3000 Sticky Flat Dusty
"""
import random
import sys
import os
import importlib

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tools.sim_harness import (BatterStats, BowlerStats, InningsState,
                               CricketMatch, build_team)

CUR = importlib.import_module("engine.t20_simulation")
BK = importlib.import_module("archive.t20_simulation_backup_20260628")


def _run_match(match, ball_fn, bowler_fn):
    """Same loop as sim_harness.run_full_match but with injected engine fns."""
    def _sim(innings):
        while True:
            if innings.wickets >= 10 or innings.total_balls >= match.max_balls:
                break
            if match.current_innings_num == 2 and innings.total_runs >= getattr(match, "target", innings.total_runs + 1):
                break
            if innings.total_balls % 6 == 0 and not innings.over_log:
                bowler = bowler_fn(innings, match.pitch, match.weather, match.format_overs)
                if not bowler:
                    break
                innings.current_bowler = bowler
            ball_fn(match)
            if innings.total_balls % 6 == 0 and innings.total_balls > 0:
                innings.over_log.clear()
                innings.bouncers_in_over = 0
                innings.cutters_in_over = 0
                innings.mystery_bowled_this_over = False

    match.innings1 = InningsState(match.team1, match.team2)
    match.current_innings = match.innings1
    match.current_innings_num = 1
    _sim(match.innings1)
    match.target = match.innings1.total_runs + 1
    match.innings2 = InningsState(match.team2, match.team1)
    match.current_innings = match.innings2
    match.current_innings_num = 2
    _sim(match.innings2)
    return match


def run_engine(mod, pitch, n, rating=85, weather="Clear"):
    ball_fn = mod.execute_ball_math_t20
    bowler_fn = mod.get_smart_ai_bowler_t20
    bat_w = bowl_w = ties = 0
    inn1 = inn2 = 0
    w1 = w2 = 0                      # avg wickets lost per innings
    ao1 = ao2 = 0                    # all-out counts
    lost_allout = lost_balls = 0    # how the chase was LOST
    for _ in range(n):
        tb = build_team("BAT", rating, rating)
        tw = build_team("BWL", rating, rating)
        m = CricketMatch(tb, tw, 20, pitch, weather)
        _run_match(m, ball_fn, bowler_fn)
        first, second = m.innings1.total_runs, m.innings2.total_runs
        inn1 += first
        inn2 += second
        w1 += m.innings1.wickets
        w2 += m.innings2.wickets
        ao1 += (m.innings1.wickets >= 10)
        ao2 += (m.innings2.wickets >= 10)
        if first > second:
            bat_w += 1
            if m.innings2.wickets >= 10:
                lost_allout += 1        # chase bowled out short
            else:
                lost_balls += 1         # chase ran out of balls with wickets in hand
        elif second > first:
            bowl_w += 1
        else:
            ties += 1
    chase_losses = max(1, bat_w)
    return {
        "inn1": inn1 / n, "inn2": inn2 / n,
        "w1": w1 / n, "w2": w2 / n,
        "ao1": 100 * ao1 / n, "ao2": 100 * ao2 / n,
        "bat_pct": 100 * bat_w / n, "bowl_pct": 100 * bowl_w / n,
        "tie_pct": 100 * ties / n,
        "lost_allout_pct": 100 * lost_allout / chase_losses,
        "lost_balls_pct": 100 * lost_balls / chase_losses,
    }


def main():
    args = sys.argv[1:]
    n = int(args[0]) if args else 3000
    pitches = args[1:] if len(args) > 1 else ["Sticky"]
    print(f"# {n} sims/pitch | two equal 85/85 teams | T20 | Clear\n")
    hdr = (f"{'PITCH':<9} {'ENGINE':<8} {'1st':>6} {'2nd':>6} {'w1':>5} {'w2':>5} "
           f"{'ao2%':>6} {'bat%':>6} {'bowl%':>6} | chase loss: {'allout/balls':>12}")
    for p in pitches:
        print(hdr)
        print("-" * len(hdr))
        for label, mod in (("BACKUP", BK), ("CURRENT", CUR)):
            random.seed(42)  # SAME matched teams/sequence per engine
            r = run_engine(mod, p, n)
            print(f"{p:<9} {label:<8} {r['inn1']:6.1f} {r['inn2']:6.1f} "
                  f"{r['w1']:5.1f} {r['w2']:5.1f} {r['ao2']:6.1f} "
                  f"{r['bat_pct']:6.1f} {r['bowl_pct']:6.1f} | "
                  f"{r['lost_allout_pct']:5.0f}% / {r['lost_balls_pct']:.0f}%")
        print()


if __name__ == "__main__":
    main()
