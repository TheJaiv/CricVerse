# Full T20 pitch realism audit - Monte Carlo every pitch (Clear weather, equal
# 85v85 teams) in BOTH engine modes (normal + DSL league-realism) and report
# the numbers that matter for realism:
#   1st-inn par / spread / run-rate / wickets / all-out% / boundary balls per
#   innings (4s+6s) / six share / chase-win% / tie%.
# Run from repo root: python tools/pitch_audit.py [n_per_pitch]

import os
import random
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from sim_harness import CricketMatch, InningsState, build_team, run_full_match
from league.tournament_manager import ALL_PITCHES

N = int(sys.argv[1]) if len(sys.argv) > 1 else 500


def audit_pitch(pitch, dsl, n):
    r = {"runs": [], "wkts": [], "balls": [], "bnd4": [], "bnd6": [],
         "allout": 0, "chase_win": 0, "tie": 0, "runs2": [], "wkts2": []}
    for _ in range(n):
        a = build_team("A", 85, 85, noise=0)
        b = build_team("B", 85, 85, noise=0)
        m = CricketMatch(a, b, format_overs=20, pitch=pitch, weather="Clear")
        if dsl:
            m.tournament_type = "dsl"
        m.innings1 = InningsState(a, b)
        m.current_innings = m.innings1
        m.current_innings_num = 1
        run_full_match(m)
        i1, i2 = m.innings1, m.innings2
        r["runs"].append(i1.total_runs)
        r["wkts"].append(i1.wickets)
        r["balls"].append(i1.total_balls)
        r["bnd4"].append(sum(bs.fours for bs in i1.batting_stats.values()))
        r["bnd6"].append(sum(bs.sixes for bs in i1.batting_stats.values()))
        r["runs2"].append(i2.total_runs)
        r["wkts2"].append(i2.wickets)
        if i1.wickets >= 10:
            r["allout"] += 1
        if i2.total_runs > i1.total_runs:
            r["chase_win"] += 1
        elif i2.total_runs == i1.total_runs:
            r["tie"] += 1
    balls = statistics.mean(r["balls"])
    return {
        "par": statistics.mean(r["runs"]),
        "std": statistics.stdev(r["runs"]),
        "lo": min(r["runs"]), "hi": max(r["runs"]),
        "rr": statistics.mean(r["runs"]) / (balls / 6.0),
        "wkts": statistics.mean(r["wkts"]),
        "allout%": 100 * r["allout"] / n,
        "bnd": statistics.mean(r["bnd4"]) + statistics.mean(r["bnd6"]),
        "sixes": statistics.mean(r["bnd6"]),
        "chase%": 100 * r["chase_win"] / n,
        "tie%": 100 * r["tie"] / n,
    }


def line(pitch, s):
    return (f"{pitch:<11}{s['par']:>6.0f} ±{s['std']:>3.0f} {s['lo']:>4}-{s['hi']:<4}"
            f"{s['rr']:>6.2f}{s['wkts']:>6.1f}{s['allout%']:>8.1f}%"
            f"{s['bnd']:>6.1f}{s['sixes']:>6.1f}{s['chase%']:>8.1f}%{s['tie%']:>6.1f}%")


HDR = (f"{'pitch':<11}{'par':>6} {'':>4} {'range':>9}{'rr':>6}{'wkts':>6}{'allout':>9}"
       f"{'bnds':>6}{'6s':>6}{'chase%':>9}{'tie':>7}")


def main():
    random.seed(99)
    print(f"n={N} matches per pitch per mode · 85v85 · Clear weather\n")
    for label, dsl in (("NORMAL ENGINE", False), ("DSL LEAGUE-REALISM MODE", True)):
        print(f"━━ {label} ━━")
        print(HDR)
        for pitch in ALL_PITCHES:
            print(line(pitch, audit_pitch(pitch, dsl, N)))
        print()


if __name__ == "__main__":
    main()
