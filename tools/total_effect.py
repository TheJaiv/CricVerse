"""
Validate the TOTAL-BASED chase model: does the bat-first / bowl-first lean track the
1st-innings total? Below-par totals should favour BOWL-first (easy chase), above-par
totals should favour BAT-first, par ~50/50.

Buckets each pitch's matches by how far the 1st-innings total sits from the engine's par
(derived from pitch difficulty) and prints bat-first win% per bucket.

Usage: python tools/total_effect.py 6000 Sticky Flat Dusty
"""
import random
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tools.sim_harness import build_team, CricketMatch, run_full_match
from t20_simulation import (T20_PITCH_DIFFICULTY, T20_PAR_RATE_FLAT, T20_PAR_RATE_SLOPE)

ALL = ["Flat", "Green", "Dry", "Dusty", "Hard", "Soft", "Cracked", "Damp",
       "Dead", "Worn", "Turning", "Two-Paced", "Slow", "Bouncy", "Sticky"]


def par_for(pitch):
    return (T20_PAR_RATE_FLAT - T20_PITCH_DIFFICULTY.get(pitch, 0.20) * T20_PAR_RATE_SLOPE) * 20


def run(pitch, n):
    par = par_for(pitch)
    # buckets: well below / below / par / above / well above par
    edges = [-25, -10, 10, 25]
    labels = ["<<par", "<par", "~par", ">par", ">>par"]
    bat = [0] * 5
    tot = [0] * 5
    ov_bat = ov_bowl = 0
    inn1 = 0
    for _ in range(n):
        m = CricketMatch(build_team("BAT", 85, 85), build_team("BWL", 85, 85), 20, pitch, "Clear")
        run_full_match(m)
        first, second = m.innings1.total_runs, m.innings2.total_runs
        inn1 += first
        if first > second:
            ov_bat += 1
        elif second > first:
            ov_bowl += 1
        d = first - par
        b = 0
        while b < len(edges) and d >= edges[b]:
            b += 1
        tot[b] += 1
        if first > second:
            bat[b] += 1
    print(f"\n{pitch}  (par≈{par:.0f}, avg 1st-inn {inn1/n:.0f})  "
          f"OVERALL bat-first {100*ov_bat/n:.1f}% / bowl-first {100*ov_bowl/n:.1f}%")
    print("   total vs par : " + "  ".join(
        f"{labels[i]} {100*bat[i]/tot[i]:4.0f}%(n{tot[i]})" if tot[i] else f"{labels[i]}   -  " for i in range(5)))


def main():
    args = sys.argv[1:]
    n = int(args[0]) if args else 6000
    pitches = args[1:] if len(args) > 1 else ALL
    print(f"# {n} sims/pitch | two equal 85/85 teams | bat-first win% by 1st-innings total")
    random.seed(7)
    for p in pitches:
        run(p, n)


if __name__ == "__main__":
    main()
