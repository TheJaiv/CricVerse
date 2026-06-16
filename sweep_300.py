"""
300-POSSIBILITY HEAD-TO-HEAD:  v1  vs  2.0
Spans format x pitch x weather x matchup (300 distinct scenarios). For each one,
both engines play many matches and earn a "realism error" against what real
cricket expects for that scenario:
  - par must sit in the realistic band for the pitch/format
  - equal teams must finish ~50/50 (fairness)
  - strong-vs-weak: favourite should win big BUT upsets must stay rare (not 100%)
  - all-out rate must be plausible (not 0%, not ~always)
Lower total error = more realistic engine. We report per-scenario winner + totals.
"""
import random, statistics
import sim_harness as H
import t20_simulation_v1 as T1, odi_simulation_v1 as O1
import t20_simulation as T2, odi_simulation as O2

PITCHES = ["Flat", "Green", "Dry", "Dusty", "Hard", "Soft", "Cracked", "Damp",
           "Dead", "Worn", "Turning", "Two-Paced", "Slow", "Bouncy", "Sticky"]
WEATHER = ["Clear", "Cloudy", "Overcast", "Humid", "Dry Heat", "Windy",
           "Light Rain", "Drizzle", "Heavy Rain", "Thunderstorm"]
# pitch scoring character: multiplier on the format's neutral par
PITCH_MULT = {"Flat": 1.18, "Dead": 1.15, "Hard": 1.0, "Slow": 0.9, "Two-Paced": 0.9,
              "Dry": 0.97, "Bouncy": 0.95, "Green": 0.82, "Damp": 0.82, "Soft": 0.88,
              "Dusty": 0.85, "Turning": 0.84, "Worn": 0.85, "Cracked": 0.8, "Sticky": 0.78}
# matchups: (batA,bowlA),(batB,bowlB),label,expected favourite win% (None = equal->50)
MATCHUPS = [
    ((85, 85), (85, 85), "equal", None),
    ((90, 88), (78, 76), "strong-vs-weak", 92),
    ((92, 90), (74, 72), "mismatch", 96),
    ((87, 85), (83, 81), "slight-edge", 64),
]


def use(t, o):
    H.execute_ball_math_t20 = t.execute_ball_math_t20
    H.get_smart_ai_bowler_t20 = t.get_smart_ai_bowler_t20
    H.execute_ball_math_odi = o.execute_ball_math_odi
    H.get_smart_ai_bowler_odi = o.get_smart_ai_bowler_odi


def play(fo, pitch, weather, specA, specB, n):
    par = []; allout = 0; a = b = 0
    for i in range(n):
        ta = H.build_team("A", *specA); tb = H.build_team("B", *specB)
        flip = i % 2 == 1
        m = H.CricketMatch(tb if flip else ta, ta if flip else tb, fo, pitch, weather)
        H.run_full_match(m)
        s1, s2 = m.innings1.total_runs, m.innings2.total_runs
        sa, sb = (s2, s1) if flip else (s1, s2)
        par.append(m.innings1.total_runs); allout += m.innings1.wickets >= 10
        a += sa > sb; b += sb > sa
    return statistics.mean(par), 100 * allout / n, 100 * a / max(1, a + b)


def realism_error(fo, pitch, fav, par, allout, awin):
    """Lower = more realistic. Sums normalized penalties."""
    base = 175 if fo == 20 else 280
    exp_par = base * PITCH_MULT.get(pitch, 1.0)
    band = 28 if fo == 20 else 45        # acceptable +/- around expected par
    e_par = max(0.0, abs(par - exp_par) - band) / 10.0
    # fairness / favouritism
    if fav is None:                       # equal teams -> 50/50
        e_win = abs(awin - 50) / 4.0
    else:                                 # favourite should win ~fav%, never 100 (upsets exist)
        e_win = abs(awin - fav) / 8.0
        if awin >= 99.8: e_win += 1.5     # zero upsets is unrealistic
    # all-out plausibility
    e_ao = 0.0
    if allout <= 1: e_ao += 1.0           # teams ~never bowled out is unrealistic
    if allout >= 92: e_ao += 1.0
    return e_par + e_win + e_ao


if __name__ == "__main__":
    N = 200
    scen = []
    for fo in (20, 50):
        for p in PITCHES:
            # every pitch x all 10 weathers, rotating matchups -> 2*15*10 = 300 distinct
            for k in range(len(WEATHER)):
                w = WEATHER[k]
                mu = MATCHUPS[(PITCHES.index(p) + k) % len(MATCHUPS)]
                scen.append((fo, p, w, mu))
    print(f"Total scenarios: {len(scen)}  (x2 engines x {N} matches = {len(scen)*2*N:,} sims)\n")

    v1_pts = v2_pts = ties = 0
    v1_err_tot = v2_err_tot = 0.0
    worst = []
    for idx, (fo, p, w, (sA, sB, lbl, fav)) in enumerate(scen):
        sd = hash((fo, p, w, lbl)) & 0xFFFF
        random.seed(sd); use(T1, O1); r1 = play(fo, p, w, sA, sB, N)
        random.seed(sd); use(T2, O2); r2 = play(fo, p, w, sA, sB, N)
        e1 = realism_error(fo, p, fav, *r1)
        e2 = realism_error(fo, p, fav, *r2)
        v1_err_tot += e1; v2_err_tot += e2
        if e2 < e1 - 1e-6: v2_pts += 1
        elif e1 < e2 - 1e-6: v1_pts += 1; worst.append((e1 - e2, fo, p, w, lbl, r1, r2))
        else: ties += 1
    worst.sort()
    print(f"{'='*70}")
    print(f"PER-SCENARIO REALISM WINNER  (300 scenarios)")
    print(f"  2.0 more realistic : {v2_pts}")
    print(f"  v1  more realistic : {v1_pts}")
    print(f"  ties               : {ties}")
    print(f"{'-'*70}")
    print(f"AGGREGATE REALISM ERROR  (lower = better)")
    print(f"  v1  total error : {v1_err_tot:8.1f}   avg {v1_err_tot/len(scen):.3f}")
    print(f"  2.0 total error : {v2_err_tot:8.1f}   avg {v2_err_tot/len(scen):.3f}")
    print(f"  ==> 2.0 is {100*(v1_err_tot-v2_err_tot)/v1_err_tot:.0f}% more realistic overall")
    print(f"{'='*70}")
    if worst:
        print("Scenarios where v1 edged 2.0 (par1/ao1/win1  vs  par2/ao2/win2):")
        for d, fo, p, w, lbl, r1, r2 in worst[:8]:
            print(f"  {fo}ov {p:<10}{w:<12}{lbl:<15} "
                  f"v1 {r1[0]:3.0f}/{r1[1]:2.0f}%/{r1[2]:3.0f}  2.0 {r2[0]:3.0f}/{r2[1]:2.0f}%/{r2[2]:3.0f}")
