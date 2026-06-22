"""
Full pitch x weather stress grid for the CricVerse engines.
Runs equal (85v85) teams across every combination, both formats, and flags
anomalies: crashes, absurd par, extreme all-out rates, or win-split bias
(equal teams MUST be ~50/50 in every condition).
"""
import random
import statistics
import traceback
from sim_harness import build_team, CricketMatch, run_full_match

PITCHES = ["Flat", "Green", "Dry", "Dusty", "Hard", "Soft", "Cracked", "Damp",
           "Dead", "Worn", "Turning", "Two-Paced", "Slow", "Bouncy", "Sticky"]
WEATHER = ["Clear", "Cloudy", "Overcast", "Humid", "Dry Heat", "Windy",
           "Light Rain", "Drizzle", "Heavy Rain", "Thunderstorm"]


def run_combo(fo, pitch, weather, n):
    par, allout, wkts, balls = [], 0, [], []
    a_wins = b_wins = 0
    for i in range(n):
        ta = build_team("A", 85, 85)
        tb = build_team("B", 85, 85)
        if i % 2 == 1:
            m = CricketMatch(tb, ta, fo, pitch, weather)
            run_full_match(m)
            s_b, s_a = m.innings1.total_runs, m.innings2.total_runs
        else:
            m = CricketMatch(ta, tb, fo, pitch, weather)
            run_full_match(m)
            s_a, s_b = m.innings1.total_runs, m.innings2.total_runs
        i1 = m.innings1
        par.append(i1.total_runs)
        wkts.append(i1.wickets)
        balls.append(i1.total_balls)
        if i1.wickets >= 10:
            allout += 1
        if s_a > s_b:
            a_wins += 1
        elif s_b > s_a:
            b_wins += 1
    decided = max(1, a_wins + b_wins)
    return {
        "par": statistics.mean(par), "std": statistics.pstdev(par),
        "min": min(par), "max": max(par),
        "allout": 100 * allout / n, "wkts": statistics.mean(wkts),
        "balls": statistics.mean(balls), "a_win": 100 * a_wins / decided,
    }


def grid(fo, label, n):
    print(f"\n{'='*92}\n{label}  (equal 85v85, n={n}/combo)\n{'='*92}")
    print(f"{'PITCH':<11}{'WEATHER':<13}{'par':>5}{'std':>5}{'min':>5}{'max':>5}{'allout%':>9}{'wkts':>6}{'Awin%':>7}  flags")
    anomalies = []
    for p in PITCHES:
        for w in WEATHER:
            random.seed(hash((p, w)) & 0xFFFF)
            try:
                r = run_combo(fo, p, w, n)
            except Exception:
                anomalies.append(f"CRASH {p}/{w}: {traceback.format_exc().splitlines()[-1]}")
                print(f"{p:<11}{w:<13}  *** CRASH ***")
                continue
            flags = []
            # win-split bias (equal teams should be ~50/50; >n-dependent band = bug)
            band = 6.0 if n >= 600 else 9.0
            if abs(r["a_win"] - 50) > band:
                flags.append(f"WINBIAS({r['a_win']:.0f}%)")
            # absurd par for the format
            lo, hi = (70, 250) if fo == 20 else (120, 400)
            if r["par"] < lo or r["par"] > hi:
                flags.append(f"PAR({r['par']:.0f})")
            # innings almost never reaching a natural end / always collapsing
            if r["allout"] > 90:
                flags.append(f"ALLOUT({r['allout']:.0f}%)")
            if r["wkts"] < 2.0:
                flags.append(f"FEWKTS({r['wkts']:.1f})")
            flagstr = " ".join(flags)
            if flags:
                anomalies.append(f"{p}/{w}: {flagstr}")
            print(f"{p:<11}{w:<13}{r['par']:>5.0f}{r['std']:>5.0f}{r['min']:>5}{r['max']:>5}"
                  f"{r['allout']:>8.0f}%{r['wkts']:>6.1f}{r['a_win']:>6.0f}%  {flagstr}")
    return anomalies


if __name__ == "__main__":
    N_T20 = 400
    N_ODI = 250
    a1 = grid(20, "T20", N_T20)
    a2 = grid(50, "ODI", N_ODI)
    print(f"\n{'#'*92}\nANOMALY SUMMARY\n{'#'*92}")
    alla = a1 + a2
    if not alla:
        print("None flagged.")
    else:
        for a in alla:
            print(" -", a)
