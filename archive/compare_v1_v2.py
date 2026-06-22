"""Head-to-head: original engine (v1) vs Engine 2.0. Same teams, same conditions."""
import random, statistics
from collections import Counter
import sim_harness as H
import t20_simulation_v1 as T1, odi_simulation_v1 as O1   # original engine
import t20_simulation as T2, odi_simulation as O2          # 2.0


def run(engine_t20, engine_odi, fo, specA, specB, n, pitch="Hard", weather="Clear"):
    import importlib
    # patch the harness to use the chosen engine's functions
    H.execute_ball_math_t20 = engine_t20.execute_ball_math_t20
    H.get_smart_ai_bowler_t20 = engine_t20.get_smart_ai_bowler_t20
    H.execute_ball_math_odi = engine_odi.execute_ball_math_odi
    H.get_smart_ai_bowler_odi = engine_odi.get_smart_ai_bowler_odi

    par, a_wins, b_wins, allout = [], 0, 0, 0
    dmix = Counter()
    for i in range(n):
        ta = H.build_team("A", *specA); tb = H.build_team("B", *specB)
        if i % 2:
            m = H.CricketMatch(tb, ta, fo, pitch, weather); H.run_full_match(m)
            sb, sa = m.innings1.total_runs, m.innings2.total_runs
        else:
            m = H.CricketMatch(ta, tb, fo, pitch, weather); H.run_full_match(m)
            sa, sb = m.innings1.total_runs, m.innings2.total_runs
        par.append(m.innings1.total_runs)
        allout += (m.innings1.wickets >= 10)
        a_wins += (sa > sb); b_wins += (sb > sa)
        for inn in (m.innings1, m.innings2):
            for st in inn.batting_stats.values():
                d = st.dismissal
                if d in ("not out", "Subbed Out"): continue
                if d.startswith("run out"): dmix["RunOut"] += 1
                elif d.startswith("st."): dmix["Stumped"] += 1
                elif d.startswith("hit wkt"): dmix["HitWkt"] += 1
    return {
        "par": statistics.mean(par), "allout": 100 * allout / n,
        "a_win": 100 * a_wins / max(1, a_wins + b_wins),
        "new_dismissals": dmix["RunOut"] + dmix["Stumped"] + dmix["HitWkt"],
    }


def line(tag, r):
    return f"  {tag:9} par {r['par']:5.0f}   all-out {r['allout']:4.0f}%   strong-win {r['a_win']:5.1f}%   new-dismissal types: {'YES' if r['new_dismissals'] else 'none'}"


print("Real-cricket targets:  T20 par ~165 (flat ~200) | ODI par ~270 | equal teams ~50% | strong beats weak\n")

scenarios = [
    ("T20 neutral (equal 85v85)", 20, (85, 85), (85, 85), "Hard"),
    ("T20 FLAT (equal 85v85)",     20, (85, 85), (85, 85), "Flat"),
    ("T20 strong 90/88 vs weak 76/74", 20, (90, 88), (76, 74), "Hard"),
    ("ODI neutral (equal 85v85)", 50, (85, 85), (85, 85), "Hard"),
    ("ODI strong 90/88 vs weak 76/74", 50, (90, 88), (76, 74), "Hard"),
]

N = 1500
for title, fo, sa, sb, pitch in scenarios:
    random.seed(2024); r1 = run(T1, O1, fo, sa, sb, N, pitch)
    random.seed(2024); r2 = run(T2, O2, fo, sa, sb, N, pitch)
    print(f"\n### {title} ###")
    print(line("OLD", r1))
    print(line("2.0", r2))
