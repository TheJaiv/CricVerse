# Ball-by-ball / over-by-over T20 engine audit — the things total-score audits hide:
#   1. CHASE STRENGTH: P(successful chase) vs a FIXED target (innings 2 simulated alone)
#   2. EXTRAS: wides / no-balls / leg-byes per innings, vs real T20 counts
#   3. OVER VOLATILITY: over-score histogram + how often the SAME bowler concedes
#      both a ≤4 over and a ≥16 over in one innings
# Run from repo root:  python tools/over_audit.py [n]

import os
import random
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from sim_harness import CricketMatch, InningsState, build_team
from t20_simulation import execute_ball_math_t20, get_smart_ai_bowler_t20

N = int(sys.argv[1]) if len(sys.argv) > 1 else 400


def sim_innings_instrumented(m, innings, stats):
    """Simulate one innings ball-by-ball, recording per-over runs/bowler + extras."""
    over_runs = 0
    over_bowler = None
    while True:
        if innings.wickets >= 10 or innings.total_balls >= m.max_balls:
            break
        if m.current_innings_num == 2 and innings.total_runs >= m.target:
            break
        if innings.total_balls % 6 == 0 and not innings.over_log:
            b = get_smart_ai_bowler_t20(innings, m.pitch, m.weather, m.format_overs)
            if not b:
                break
            innings.current_bowler = b
            over_bowler = b["name"]
            over_runs = 0

        log_len = len(innings.over_log)
        runs0, balls0 = innings.total_runs, innings.total_balls
        execute_ball_math_t20(m)
        over_runs += innings.total_runs - runs0

        for entry in innings.over_log[log_len:]:
            if "wide" in entry:
                stats["wides"] += 1
            elif "noball" in entry:
                stats["noballs"] += 1
            elif entry.endswith("LB"):
                stats["legbye_balls"] += 1
                stats["legbye_runs"] += int(entry[:-2])

        if innings.total_balls > balls0 and innings.total_balls % 6 == 0:
            stats["overs"].append(over_runs)
            stats.setdefault("by_bowler", {}).setdefault(over_bowler, []).append(over_runs)
            innings.over_log.clear()
            innings.bouncers_in_over = 0
            innings.cutters_in_over = 0
            innings.mystery_bowled_this_over = False
            over_runs = 0
    # partial last over still counts toward volatility if it had 3+ balls
    if innings.total_balls % 6 >= 3 and over_runs > 0:
        stats["overs"].append(over_runs)


# ── 1. CHASE STRENGTH vs fixed target ──────────────────────────────────────────
def chase_test(pitch, target, n, dsl=False):
    wins = 0
    balls_left_on_win = []
    wkts_on_win = []
    for _ in range(n):
        a = build_team("A", 85, 85, noise=0)
        b = build_team("B", 85, 85, noise=0)
        m = CricketMatch(a, b, format_overs=20, pitch=pitch, weather="Clear")
        if dsl:
            m.tournament_type = "dsl"
        m.innings1 = InningsState(a, b)
        m.innings1.total_runs = target          # pretend team A made `target`
        m.innings2 = InningsState(b, a)
        m.current_innings = m.innings2
        m.current_innings_num = 2
        m.target = target + 1
        st = {"wides": 0, "noballs": 0, "legbye_balls": 0, "legbye_runs": 0, "overs": []}
        sim_innings_instrumented(m, m.innings2, st)
        if m.innings2.total_runs >= m.target:
            wins += 1
            balls_left_on_win.append(m.max_balls - m.innings2.total_balls)
            wkts_on_win.append(m.innings2.wickets)
    return {
        "win%": 100 * wins / n,
        "balls_left": statistics.mean(balls_left_on_win) if balls_left_on_win else 0,
        "wkts_used": statistics.mean(wkts_on_win) if wkts_on_win else 0,
    }


# ── 2+3. EXTRAS & OVER VOLATILITY from full innings ───────────────────────────
def innings_audit(pitch, n, chase=False, target=None):
    agg = {"wides": [], "noballs": [], "lb_balls": [], "lb_runs": [],
           "overs": [], "swing_matches": 0, "bowler_innings": 0}
    for _ in range(n):
        a = build_team("A", 85, 85, noise=0)
        b = build_team("B", 85, 85, noise=0)
        m = CricketMatch(a, b, format_overs=20, pitch=pitch, weather="Clear")
        st = {"wides": 0, "noballs": 0, "legbye_balls": 0, "legbye_runs": 0, "overs": []}
        if chase:
            m.innings1 = InningsState(a, b)
            m.innings1.total_runs = target
            m.innings2 = InningsState(b, a)
            m.current_innings = m.innings2
            m.current_innings_num = 2
            m.target = target + 1
            sim_innings_instrumented(m, m.innings2, st)
        else:
            m.innings1 = InningsState(a, b)
            m.current_innings = m.innings1
            m.current_innings_num = 1
            sim_innings_instrumented(m, m.innings1, st)
        agg["wides"].append(st["wides"])
        agg["noballs"].append(st["noballs"])
        agg["lb_balls"].append(st["legbye_balls"])
        agg["lb_runs"].append(st["legbye_runs"])
        agg["overs"].extend(st["overs"])
        for bowler, overs in st.get("by_bowler", {}).items():
            if len(overs) >= 2:
                agg["bowler_innings"] += 1
                if min(overs) <= 4 and max(overs) >= 16:
                    agg["swing_matches"] += 1
    overs = agg["overs"]
    hist = {}
    for lo, hi, label in ((0, 4, "0-4"), (5, 9, "5-9"), (10, 15, "10-15"), (16, 19, "16-19"), (20, 99, "20+")):
        hist[label] = 100 * sum(1 for o in overs if lo <= o <= hi) / max(1, len(overs))
    return {
        "wides": statistics.mean(agg["wides"]),
        "noballs": statistics.mean(agg["noballs"]),
        "lb_balls": statistics.mean(agg["lb_balls"]),
        "lb_runs": statistics.mean(agg["lb_runs"]),
        "over_mean": statistics.mean(overs),
        "over_std": statistics.stdev(overs),
        "hist": hist,
        "swing%": 100 * agg["swing_matches"] / max(1, agg["bowler_innings"]),
    }


def main():
    random.seed(7)
    print(f"n={N} per cell · 85v85 · Clear\n")

    print("━━ 1. CHASE STRENGTH — P(chase down a fixed target), innings 2 only ━━")
    print(f"{'target':>7} | " + " | ".join(f"{p:^24}" for p in ("Dead", "Hard", "Slow")))
    print(f"{'':>7} | " + " | ".join(f"{'win%':>6}{'balls left':>11}{'wkts':>6}" for _ in range(3)))
    for tgt in (150, 170, 180, 190, 200, 210, 220, 230):
        row = []
        for pitch in ("Dead", "Hard", "Slow"):
            r = chase_test(pitch, tgt, N)
            row.append(f"{r['win%']:>5.1f}%{r['balls_left']:>10.1f}{r['wkts_used']:>6.1f}")
        print(f"{tgt:>7} | " + " | ".join(row))

    print("\n━━ 2. EXTRAS per innings (real T20: ~5-6 wides · ~0.2-0.4 no-balls · ~2-3 lb runs) ━━")
    print(f"{'pitch':<8}{'wides':>8}{'no-balls':>10}{'lb balls':>10}{'lb runs':>9}")
    ex = {}
    for pitch in ("Dead", "Flat", "Hard", "Dusty", "Sticky"):
        r = innings_audit(pitch, max(150, N // 2))
        ex[pitch] = r
        print(f"{pitch:<8}{r['wides']:>8.1f}{r['noballs']:>10.1f}{r['lb_balls']:>10.1f}{r['lb_runs']:>9.1f}")

    print("\n━━ 3. OVER VOLATILITY (1st innings; real T20: mean ~8.3, std ~5.3, 20+ overs ~2-3%) ━━")
    print(f"{'pitch':<8}{'mean':>6}{'std':>6}{'0-4':>7}{'5-9':>7}{'10-15':>7}{'16-19':>7}{'20+':>6}{'  same-bowler 4→16+ swing'}")
    for pitch in ("Dead", "Flat", "Hard", "Dusty", "Sticky"):
        r = ex[pitch]
        h = r["hist"]
        print(f"{pitch:<8}{r['over_mean']:>6.1f}{r['over_std']:>6.1f}"
              f"{h['0-4']:>6.1f}%{h['5-9']:>6.1f}%{h['10-15']:>6.1f}%{h['16-19']:>6.1f}%{h['20+']:>5.1f}%"
              f"{r['swing%']:>12.1f}%")

    print("\n━━ 3b. CHASE over volatility on Dead, target 190 (the user's scenario) ━━")
    r = innings_audit("Dead", max(150, N // 2), chase=True, target=190)
    h = r["hist"]
    print(f"mean {r['over_mean']:.1f} · std {r['over_std']:.1f} · "
          f"0-4: {h['0-4']:.1f}% · 5-9: {h['5-9']:.1f}% · 10-15: {h['10-15']:.1f}% · "
          f"16-19: {h['16-19']:.1f}% · 20+: {h['20+']:.1f}% · same-bowler swing {r['swing%']:.1f}%")


if __name__ == "__main__":
    main()
