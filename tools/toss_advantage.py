"""
Bat-first vs Bowl-first win-rate engine.

Pits two STATISTICALLY EQUAL teams (same bat/bowl ratings) against each other in
T20 across every pitch type. Because the teams are identical, any deviation from
a 50/50 split is the pitch's intrinsic batting-first / chasing advantage.

  innings1 batting team  -> "BAT FIRST"
  innings2 batting team  -> "BOWL FIRST" (the chaser)

Usage:
    python tools/toss_advantage.py                 # 5000 sims, all pitches, Sticky last
    python tools/toss_advantage.py 5000 Sticky     # N sims, single pitch
"""
import random
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tools.sim_harness import build_team, CricketMatch, run_full_match

# Canonical pitch list (mirrors tournament_manager.ALL_PITCHES — kept inline to
# avoid importing discord). Sticky is placed last so it prints at the bottom.
ALL_PITCHES = ["Flat", "Green", "Dry", "Dusty", "Hard", "Soft", "Cracked", "Damp",
               "Dead", "Worn", "Turning", "Two-Paced", "Slow", "Bouncy", "Sticky"]

RATING = 85          # both teams: 85 bat / 85 bowl
WEATHER = "Clear"


def run_pitch(pitch, n, rating=RATING, weather=WEATHER, format_overs=20):
    """Run n matches of two equal teams on one pitch. Returns count dict."""
    bat_first_wins = bowl_first_wins = ties = 0
    par_total = 0
    for _ in range(n):
        # Two identical specs. Distinct names so the engine treats them as 2 sides.
        t_bat = build_team("BAT", rating, rating)
        t_bowl = build_team("BWL", rating, rating)
        # t_bat always bats first; t_bowl always chases. (No swap — we WANT to
        # measure the innings-order effect, not remove it.)
        m = CricketMatch(t_bat, t_bowl, format_overs, pitch, weather)
        run_full_match(m)
        first = m.innings1.total_runs   # bat-first total
        second = m.innings2.total_runs  # chaser total
        par_total += first
        if first > second:
            bat_first_wins += 1
        elif second > first:
            bowl_first_wins += 1
        else:
            ties += 1
    return {
        "pitch": pitch, "n": n,
        "bat_first_wins": bat_first_wins,
        "bowl_first_wins": bowl_first_wins,
        "ties": ties,
        "bat_first_pct": 100 * bat_first_wins / n,
        "bowl_first_pct": 100 * bowl_first_wins / n,
        "tie_pct": 100 * ties / n,
        "par_mean": par_total / n,
    }


def report(r):
    decisive = r["bat_first_wins"] + r["bowl_first_wins"]
    lean = "BAT FIRST" if r["bat_first_wins"] > r["bowl_first_wins"] else "BOWL FIRST"
    edge = abs(r["bat_first_pct"] - r["bowl_first_pct"]) / 2  # +/- from 50
    print(f"{r['pitch']:<10}  bat-first {r['bat_first_pct']:5.1f}%   "
          f"bowl-first {r['bowl_first_pct']:5.1f}%   ties {r['tie_pct']:4.1f}%   "
          f"par {r['par_mean']:5.1f}   -> {lean} +{edge:.1f}")


def main():
    args = sys.argv[1:]
    n = int(args[0]) if args else 5000
    pitches = [args[1].title()] if len(args) > 1 else ALL_PITCHES

    random.seed(42)  # reproducible
    print(f"# Two EQUAL teams ({RATING}/{RATING}) | T20 20 overs | {WEATHER} weather | "
          f"{n} sims/pitch")
    print(f"# 'edge' = points above 50/50 toward whichever side wins more.\n")
    print(f"{'PITCH':<10}  {'BAT FIRST':<10}     {'BOWL FIRST':<11}    {'TIES':<8}    "
          f"{'PAR':<8}   LEAN")
    print("-" * 92)

    results = []
    for p in pitches:
        r = run_pitch(p, n)
        results.append(r)
        report(r)

    if len(results) > 1:
        print("-" * 92)
        avg_bat = sum(r["bat_first_pct"] for r in results) / len(results)
        big = max(results, key=lambda r: abs(r["bat_first_pct"] - r["bowl_first_pct"]))
        print(f"avg bat-first across pitches: {avg_bat:.1f}%   |   "
              f"strongest toss bias: {big['pitch']} "
              f"({big['bat_first_pct']:.1f}/{big['bowl_first_pct']:.1f})")


if __name__ == "__main__":
    main()
