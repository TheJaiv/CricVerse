"""
Rigorous realism benchmark: original engine (v1) vs 2.0, scored against MODERN
real-cricket ground truth (modern aggressive T20; singles-driven ODI).
Ball-by-ball composition + dismissals + outcomes. Whichever engine is closer to
each real target 'wins' it. Surfaces any dimension where 2.0 regresses.
"""
import random, statistics
from collections import Counter
import sim_harness as H
import t20_simulation_v1 as T1, odi_simulation_v1 as O1
from engine import t20_simulation as T2, odi_simulation as O2


def use(t20, odi):
    H.execute_ball_math_t20 = t20.execute_ball_math_t20
    H.get_smart_ai_bowler_t20 = t20.get_smart_ai_bowler_t20
    H.execute_ball_math_odi = odi.execute_ball_math_odi
    H.get_smart_ai_bowler_odi = odi.get_smart_ai_bowler_odi


def sim_innings(fo, n, specA=(85, 85), specB=(85, 85), pitch="Hard"):
    """Ball-by-ball first-innings tallies + match win split (alternating bat-first)."""
    c = Counter(); dmix = Counter()
    par = []; allout = 0; a_w = b_w = 0
    for i in range(n):
        ta = H.build_team("A", *specA); tb = H.build_team("B", *specB)
        bat_first, bowl_first = (ta, tb)
        m = H.CricketMatch(bat_first, bowl_first, fo, pitch, "Clear")
        inn = H.InningsState(bat_first, bowl_first)
        m.innings1 = inn; m.current_innings = inn; m.current_innings_num = 1
        while inn.wickets < 10 and inn.total_balls < m.max_balls:
            if inn.total_balls % 6 == 0 and not inn.over_log:
                b = H.get_smart_ai_bowler(inn, pitch, "Clear", fo)
                if not b: break
                inn.current_bowler = b
            pr, pw, pb = inn.total_runs, inn.wickets, inn.total_balls
            H.execute_ball_math(m)
            if inn.total_balls > pb:
                d = inn.total_runs - pr
                if inn.wickets > pw: c["wkt"] += 1
                elif d >= 6: c["six"] += 1
                elif d >= 4: c["four"] += 1
                elif d == 2: c["two"] += 1
                elif d == 3: c["two"] += 1
                elif d == 1: c["single"] += 1
                else: c["dot"] += 1
            if inn.total_balls % 6 == 0 and inn.total_balls > 0:
                inn.over_log.clear(); inn.bouncers_in_over = 0
                inn.cutters_in_over = 0; inn.mystery_bowled_this_over = False
        par.append(inn.total_runs); allout += inn.wickets >= 10
        for st in inn.batting_stats.values():
            dd = st.dismissal
            if dd in ("not out", "Subbed Out"): continue
            if dd.startswith("run out"): dmix["RunOut"] += 1
            elif dd.startswith("st."): dmix["Stumped"] += 1
            elif dd.startswith("hit wkt"): dmix["HitWkt"] += 1
            elif dd.startswith("lbw"): dmix["LBW"] += 1
            elif dd.startswith("c."): dmix["Caught"] += 1
            elif dd.startswith("b."): dmix["Bowled"] += 1
    tb_ = max(1, sum(v for k, v in c.items()))
    td = max(1, sum(dmix.values()))
    pc = lambda k: 100 * c[k] / tb_
    pd = lambda k: 100 * dmix[k] / td
    return {
        "par": statistics.mean(par), "allout": 100 * allout / n,
        "dot": pc("dot"), "single": pc("single"), "boundary": pc("four") + pc("six"),
        "six": pc("six"), "caught": pd("Caught"), "lbw": pd("LBW"),
        "runout": pd("RunOut"), "stump": pd("Stumped"),
    }


def winsplit(fo, n, specA, specB, pitch="Hard"):
    a = b = 0
    for i in range(n):
        ta = H.build_team("A", *specA); tb = H.build_team("B", *specB)
        flip = i % 2 == 1
        m = H.CricketMatch(tb if flip else ta, ta if flip else tb, fo, pitch, "Clear")
        H.run_full_match(m)
        s1, s2 = m.innings1.total_runs, m.innings2.total_runs
        sa, sb = (s2, s1) if flip else (s1, s2)
        a += sa > sb; b += sb > sa
    return 100 * a / max(1, a + b)


tally = Counter()
def row(name, target, v1, v2, unit=""):
    d1, d2 = abs(v1 - target), abs(v2 - target)
    win = "2.0" if d2 < d1 - 1e-9 else ("v1" if d1 < d2 - 1e-9 else "tie")
    tally[win] += 1
    mark = {"2.0": "2.0", "v1": "v1  <== v1 better", "tie": "tie"}[win]
    print(f"  {name:26} real {str(target)+unit:>6} | v1 {v1:6.1f}{unit}  2.0 {v2:6.1f}{unit}   winner: {mark}")


if __name__ == "__main__":
    N = 2500
    print("=" * 92)
    print("RIGOROUS REALISM BENCHMARK  —  v1  vs  2.0   (closer to real cricket wins)")
    print("=" * 92)

    # MODERN targets
    T20_T = {"par": 175, "allout": 28, "dot": 38, "single": 33, "boundary": 18, "six": 4,
             "caught": 57, "lbw": 13, "runout": 8, "stump": 4}
    ODI_T = {"par": 280, "allout": 50, "dot": 46, "single": 37, "boundary": 11, "six": 2,
             "caught": 57, "lbw": 13, "runout": 8, "stump": 4}

    SEEDS = [2024, 77, 333, 9001, 55555]  # multi-seed average kills single-draw RNG noise

    def avg_innings(eng_t, eng_o, fo):
        use(eng_t, eng_o); acc = None
        for sd in SEEDS:
            random.seed(sd); r = sim_innings(fo, N)
            if acc is None: acc = {k: 0.0 for k in r}
            for k in r: acc[k] += r[k] / len(SEEDS)
        return acc

    for fo, lbl, T in [(20, "T20 (modern, neutral)", T20_T), (50, "ODI (singles game, neutral)", ODI_T)]:
        v1 = avg_innings(T1, O1, fo)
        v2 = avg_innings(T2, O2, fo)
        print(f"\n--- {lbl}  (avg of {len(SEEDS)} seeds) ---")
        for k, nm, u in [("par", "Par score", ""), ("allout", "All-out rate", "%"),
                         ("dot", "Dot %", "%"), ("single", "Single %", "%"),
                         ("boundary", "Boundary %", "%"), ("six", "Six %", "%"),
                         ("caught", "Caught % (dismissals)", "%"), ("lbw", "LBW %", "%"),
                         ("runout", "Run-out %", "%"), ("stump", "Stumped %", "%")]:
            row(nm, T[k], v1[k], v2[k], u)

    print("\n--- Fairness / rating sensitivity  (avg of seeds) ---")
    def avg_split(eng_t, eng_o, specA, specB):
        use(eng_t, eng_o); tot = 0.0
        for sd in SEEDS:
            random.seed(sd); tot += winsplit(20, N, specA, specB) / len(SEEDS)
        return tot
    e1 = avg_split(T1, O1, (85, 85), (85, 85)); e2 = avg_split(T2, O2, (85, 85), (85, 85))
    row("Equal-team win split", 50, e1, e2, "%")
    s1 = avg_split(T1, O1, (90, 88), (76, 74)); s2 = avg_split(T2, O2, (90, 88), (76, 74))
    row("Strong-vs-weak win%", 97, s1, s2, "%")

    print("\n" + "=" * 92)
    print(f"FINAL TALLY:   2.0 wins {tally['2.0']}   |   v1 wins {tally['v1']}   |   ties {tally['tie']}")
    print("=" * 92)
