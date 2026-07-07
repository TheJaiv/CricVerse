# DSL league-realism calibration — ODI edition (DSL became a 50-over league).
# Compares the normal ODI engine vs DSL mode (tournament_type="dsl": consistency
# shield off + flatter DSL_ODI_SKILL_SCALE + DSL_ODI_WKT_TRIM).
# Run from repo root:  python tools/dsl_realism_calib.py [n]
#
# Targets:
#   • equal 85v85: DSL par within ~±8 of normal, all-out% similar
#   • 15-pt team gap: favourite ~88-93% (normal engine ≈ 99%)
#   • 93-rated star vs ~78 attack: fails (<20) noticeably more than normal,
#     season average still clearly elite

import os
import random
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from sim_harness import CricketMatch, InningsState, build_team
from odi_simulation import execute_ball_math_odi, get_smart_ai_bowler_odi

N = int(sys.argv[1]) if len(sys.argv) > 1 else 500


def sim_inn(m, inn):
    while inn.wickets < 10 and inn.total_balls < m.max_balls:
        if m.current_innings_num == 2 and inn.total_runs >= m.target:
            break
        if inn.total_balls % 6 == 0 and not inn.over_log:
            bw = get_smart_ai_bowler_odi(inn, m.pitch, m.weather, 50)
            if not bw:
                break
            inn.current_bowler = bw
        b0 = inn.total_balls
        execute_ball_math_odi(m)
        if inn.total_balls > b0 and inn.total_balls % 6 == 0:
            inn.over_log.clear()
            inn.bouncers_in_over = 0
            if hasattr(inn, "cutters_in_over"): inn.cutters_in_over = 0
            if hasattr(inn, "mystery_bowled_this_over"): inn.mystery_bowled_this_over = 0


def play(a, b, dsl, pitch="Hard"):
    m = CricketMatch(a, b, format_overs=50, pitch=pitch, weather="Clear")
    if dsl:
        m.tournament_type = "dsl"
    m.innings1 = InningsState(a, b); m.current_innings = m.innings1; m.current_innings_num = 1
    sim_inn(m, m.innings1)
    m.target = m.innings1.total_runs + 1
    m.innings2 = InningsState(b, a); m.current_innings = m.innings2; m.current_innings_num = 2
    sim_inn(m, m.innings2)
    return m


def main():
    from odi_simulation import DSL_ODI_SKILL_SCALE, DSL_ODI_WKT_TRIM
    print(f"n={N} per cell · ODI (50ov) · DSL_ODI_SKILL_SCALE={DSL_ODI_SKILL_SCALE} · TRIM={DSL_ODI_WKT_TRIM}")

    for label, dsl in (("NORMAL", False), ("DSL", True)):
        random.seed(21)
        pars, allout, aw, ties = [], 0, 0, 0
        for _ in range(N):
            m = play(build_team("A", 85, 85, noise=0), build_team("B", 85, 85, noise=0), dsl)
            pars.append(m.innings1.total_runs)
            if m.innings1.wickets >= 10: allout += 1
            if m.innings1.total_runs > m.innings2.total_runs: aw += 1
            elif m.innings1.total_runs == m.innings2.total_runs: ties += 1
        print(f"{label:<7} equal 85v85: par {statistics.mean(pars):.0f} · allout {100*allout/N:.0f}% · bat-first wins {100*aw/(N-ties):.1f}%")

    for label, dsl in (("NORMAL", False), ("DSL", True)):
        random.seed(22)
        fav = ties = 0
        for _ in range(N):
            m = play(build_team("A", 90, 90, noise=0), build_team("B", 75, 75, noise=0), dsl)
            if m.innings1.total_runs > m.innings2.total_runs: fav += 1
            elif m.innings1.total_runs == m.innings2.total_runs: ties += 1
        print(f"{label:<7} gap 90v75: favourite wins {100*fav/(N-ties):.1f}%")

    for label, dsl in (("NORMAL", False), ("DSL", True)):
        random.seed(23)
        scores, fails, inns = [], 0, 0
        for _ in range(N):
            a = build_team("A", 88, 88, noise=0); b = build_team("B", 85, 78, noise=0)
            a["players"][0]["name"] = "STAR"; a["players"][0]["bat"] = 93
            m = play(a, b, dsl)
            bs = m.innings1.batting_stats["STAR"]
            if bs.balls_faced > 0 or bs.dismissal != "not out":
                inns += 1; scores.append(bs.runs_scored)
                if bs.runs_scored < 20 and bs.dismissal != "not out":
                    fails += 1
        print(f"{label:<7} 93-STAR: avg {statistics.mean(scores):.0f} · P(out <20) {100*fails/inns:.0f}%")


if __name__ == "__main__":
    main()
