# DSL league-realism calibration — Monte Carlo comparison of the normal T20
# engine vs DSL mode (tournament_type="dsl": flatter DSL_SKILL_SCALE + star
# consistency shield off). Run from the repo root:
#   python tools/dsl_realism_calib.py [n_matches]
#
# Targets (medium realism):
#   • equal 85v85: win split 50±2%, par within ±5 of the normal engine
#   • 15-pt team gap: favourite ~85-92% (normal engine ≈ 99%)
#   • 93-rated star vs 78-rated attack: P(score < 15) ≈ 0.35-0.45 (normal ≈ ~0.1)
#   • a 78 bowler's over vs stars: ~≤11 runs average, wicket in ~15-25% of overs
#   • star still clearly tops the season averages

import os
import random
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sim_harness import CricketMatch, InningsState, build_team, run_full_match

N = int(sys.argv[1]) if len(sys.argv) > 1 else 1500


def play(team_a, team_b, dsl, seed=None):
    if seed is not None:
        random.seed(seed)
    m = CricketMatch(team_a, team_b, format_overs=20, pitch="Flat", weather="Clear")
    if dsl:
        m.tournament_type = "dsl"
    m.innings1 = InningsState(team_a, team_b)
    m.current_innings = m.innings1
    m.current_innings_num = 1
    run_full_match(m)
    return m


def series_stats(spec_a, spec_b, dsl, n=N, star_name=None, weak_bowler_rating=None):
    a_wins = ties = 0
    inn1_scores, star_scores = [], []
    star_outs_under_15 = 0
    star_inns = 0
    bowler_over_runs, bowler_over_wkts = [], 0
    total_overs_tracked = 0
    for i in range(n):
        ta = build_team("A", *spec_a, noise=0)
        tb = build_team("B", *spec_b, noise=0)
        if star_name:  # plant one 93-rated star opener in team A
            ta["players"][0]["name"] = star_name
            ta["players"][0]["bat"] = 93
        m = play(ta, tb, dsl)
        r1, r2 = m.innings1.total_runs, m.innings2.total_runs
        inn1_scores.append(r1)
        if r1 > r2: a_wins += 1
        elif r1 == r2: ties += 1
        if star_name:
            bs = m.innings1.batting_stats.get(star_name)
            if bs and (bs.balls_faced > 0 or bs.dismissal != "not out"):
                star_inns += 1
                star_scores.append(bs.runs_scored)
                if bs.runs_scored < 15 and bs.dismissal != "not out":
                    star_outs_under_15 += 1
        if weak_bowler_rating:
            for name, bw in m.innings1.bowling_stats.items():
                overs = bw.balls_bowled // 6
                if overs > 0:
                    bowler_over_runs.append(bw.runs_conceded / overs)
                    bowler_over_wkts += bw.wickets_taken
                    total_overs_tracked += overs
    out = {
        "a_win%": 100 * a_wins / n,
        "par": statistics.mean(inn1_scores),
    }
    if star_scores:
        out["star_avg_score"] = statistics.mean(star_scores)
        out["star_P(<15 out)%"] = 100 * star_outs_under_15 / max(1, star_inns)
    if bowler_over_runs:
        out["bowler_rpo"] = statistics.mean(bowler_over_runs)
        out["wkts_per_over%"] = 100 * bowler_over_wkts / max(1, total_overs_tracked)
    return out


def show(label, normal, dsl):
    keys = sorted(set(normal) | set(dsl))
    print(f"\n── {label} ──")
    print(f"{'metric':<20}{'NORMAL':>12}{'DSL':>12}")
    for k in keys:
        nv, dv = normal.get(k), dsl.get(k)
        fmt = lambda v: f"{v:.1f}" if isinstance(v, float) else str(v)
        print(f"{k:<20}{fmt(nv) if nv is not None else '—':>12}{fmt(dv) if dv is not None else '—':>12}")


def main():
    from t20_simulation import DSL_SKILL_SCALE
    print(f"n={N} per cell · DSL_SKILL_SCALE={DSL_SKILL_SCALE}")

    random.seed(11)
    eq_n = series_stats((85, 85), (85, 85), dsl=False)
    random.seed(11)
    eq_d = series_stats((85, 85), (85, 85), dsl=True)
    show("EQUAL TEAMS (85v85)", eq_n, eq_d)

    random.seed(22)
    gap_n = series_stats((90, 90), (75, 75), dsl=False)
    random.seed(22)
    gap_d = series_stats((90, 90), (75, 75), dsl=True)
    show("15-PT TEAM GAP (90v75, A=favourite)", gap_n, gap_d)

    random.seed(33)
    star_n = series_stats((88, 88), (85, 78), dsl=False, star_name="STAR", weak_bowler_rating=78)
    random.seed(33)
    star_d = series_stats((88, 88), (85, 78), dsl=True, star_name="STAR", weak_bowler_rating=78)
    show("93-STAR vs 78-ATTACK", star_n, star_d)


if __name__ == "__main__":
    main()
