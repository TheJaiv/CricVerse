# Batting stats BY LINEUP SLOT — catches the "bowlers are the best batters"
# inversion: tailenders walking into collapse-mode armor and out-averaging the
# top order. Real ODI shape: openers/middle avg 30-45 · #8 ~15 · #9-11 ≤ ~12.
# Run from repo root:  python tools/position_check.py [n] [pitch]
import os
import random
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from sim_harness import CricketMatch, InningsState, build_team
from odi_simulation import execute_ball_math_odi, get_smart_ai_bowler_odi

N = int(sys.argv[1]) if len(sys.argv) > 1 else 600
PITCH = sys.argv[2] if len(sys.argv) > 2 else "Hard"


def main():
    random.seed(19)
    slots = {i: {"runs": 0, "balls": 0, "outs": 0, "inns": 0, "hs": 0} for i in range(11)}
    pars = []
    for _ in range(N):
        bat = build_team("A", 85, 85, noise=0)
        bowl = build_team("B", 85, 85, noise=0)
        m = CricketMatch(bat, bowl, 50, PITCH, "Clear")
        m.innings1 = InningsState(bat, bowl)
        m.current_innings = m.innings1
        m.current_innings_num = 1
        inn = m.innings1
        while inn.wickets < 10 and inn.total_balls < 300:
            if inn.total_balls % 6 == 0 and not inn.over_log:
                bw = get_smart_ai_bowler_odi(inn, PITCH, "Clear", 50)
                if not bw:
                    break
                inn.current_bowler = bw
            execute_ball_math_odi(m)
            if inn.total_balls % 6 == 0 and inn.total_balls:
                inn.over_log.clear(); inn.bouncers_in_over = 0
                inn.cutters_in_over = 0; inn.mystery_bowled_this_over = False
        pars.append(inn.total_runs)
        for i, p in enumerate(bat["players"]):
            bs = inn.batting_stats[p["name"]]
            if bs.balls_faced == 0 and bs.dismissal == "not out":
                continue
            d = slots[i]
            d["inns"] += 1
            d["runs"] += bs.runs_scored
            d["balls"] += bs.balls_faced
            d["hs"] = max(d["hs"], bs.runs_scored)
            if bs.dismissal != "not out":
                d["outs"] += 1
    print(f"batting by lineup slot · {PITCH} · 85v85 · n={N} (par {statistics.mean(pars):.0f})")
    print(f"  {'slot':<6}{'bat':>4}{'avg':>7}{'runs/inn':>9}{'SR':>6}{'HS':>5}{'NO%':>6}")
    team = build_team("A", 85, 85, noise=0)
    for i in range(11):
        d = slots[i]
        if not d["inns"]:
            continue
        avg = d["runs"] / max(1, d["outs"])
        sr = d["runs"] / max(1, d["balls"]) * 100
        no = 100 * (d["inns"] - d["outs"]) / d["inns"]
        print(f"  #{i+1:<5}{team['players'][i]['bat']:>4}{avg:>7.1f}{d['runs']/d['inns']:>9.1f}{sr:>6.0f}{d['hs']:>5}{no:>6.0f}")


if __name__ == "__main__":
    main()
