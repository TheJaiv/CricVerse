# Bat-first vs chase win% on EVERY pitch - the toss-neutrality audit.
# Equal 85v85 teams, Clear weather; ties excluded. A pitch is toss-fair when
# bat-first sits within ~50±2.5% (1σ at n=3000 is ±0.9%).
# Run from repo root: python tools/toss_sweep.py [n] [odi|t20|dsl] [pitch1,pitch2,...]
import os
import random
import sys
from multiprocessing import Pool

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

ALL_PITCHES = ["Flat", "Green", "Dry", "Dusty", "Hard", "Soft", "Cracked", "Damp",
               "Dead", "Worn", "Turning", "Two-Paced", "Slow", "Bouncy", "Sticky"]


def sweep_pitch(args):
    pitch, n, fmt, dsl, seed = args
    from sim_harness import CricketMatch, run_full_match, build_team
    random.seed(seed)
    first = second = 0
    for _ in range(n):
        a = build_team("A", 85, 85, noise=0)
        b = build_team("B", 85, 85, noise=0)
        m = CricketMatch(a, b, fmt, pitch, "Clear")
        if dsl:
            m.tournament_type = "dsl"
        run_full_match(m)
        if m.innings1.total_runs > m.innings2.total_runs:
            first += 1
        elif m.innings2.total_runs > m.innings1.total_runs:
            second += 1
    return pitch, first, second


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 3000
    mode = sys.argv[2] if len(sys.argv) > 2 else "odi"
    fmt = 20 if mode == "t20" else 50
    dsl = mode == "dsl"
    label = {"odi": "ODI", "t20": "T20", "dsl": "ODI · DSL MODE"}[mode]
    pitches = sys.argv[3].split(",") if len(sys.argv) > 3 else ALL_PITCHES
    print(f"━━ TOSS AUDIT · {label} · equal 85v85 · Clear · n={n}/pitch (±{100*(0.25/n)**0.5:.1f}% 1σ) ━━")
    print(f"{'pitch':<11}{'bat-first%':>11}{'chase%':>9}{'verdict':>12}")
    tasks = [(p, n, fmt, dsl, 1000 + i) for i, p in enumerate(pitches)]
    tf = tn = 0
    with Pool(min(8, len(tasks))) as pool:
        for pitch, first, second in pool.imap(sweep_pitch, tasks):
            tot = first + second
            bf = 100 * first / tot
            tf += first; tn += tot
            verdict = "OK" if abs(bf - 50) <= 2.5 else ("BAT-FIRST+" if bf > 50 else "CHASE+")
            print(f"{pitch:<11}{bf:>10.1f}%{100 - bf:>8.1f}%{verdict:>12}")
    print(f"{'OVERALL':<11}{100 * tf / tn:>10.1f}%{100 - 100 * tf / tn:>8.1f}%")


if __name__ == "__main__":
    main()
