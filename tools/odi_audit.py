# ODI engine realism audit - par, chase manner, tail exposure, spin/pace wicket
# identity, extras. Mirrors tools/pitch_audit.py + tools/over_audit.py for the
# 50-over engine. Run from repo root: python tools/odi_audit.py [n]
#
# Reference (modern ODI, successful chases): finish ~46-48 ov · ~15-25 balls
# left · ~5-6 wkts down. All-out ~15-20% overall. Spin takes a bigger wicket
# share than its ball share on turners/dusty. Wides ~8-12/inn, no-balls <1/inn.

import os
import random
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from sim_harness import CricketMatch, InningsState, build_team
from engine.odi_simulation import execute_ball_math_odi, get_smart_ai_bowler_odi
try:
    from league.tournament_manager import ALL_PITCHES
except ImportError:   # tournament_manager pulls in discord; mirror its list when absent
    ALL_PITCHES = ["Flat", "Green", "Dry", "Dusty", "Hard", "Soft", "Cracked", "Damp",
                   "Dead", "Worn", "Turning", "Two-Paced", "Slow", "Bouncy", "Sticky"]

N = int(sys.argv[1]) if len(sys.argv) > 1 else 300


def _is_tail(p):
    role = p.get("role", "")
    return "Bowler" in role and "All-Rounder" not in role and float(p.get("bat", 50)) <= 60


def sim_inn(m, inn, agg=None):
    while inn.wickets < 10 and inn.total_balls < m.max_balls:
        if m.current_innings_num == 2 and inn.total_runs >= m.target:
            break
        if inn.total_balls % 6 == 0 and not inn.over_log:
            bw = get_smart_ai_bowler_odi(inn, m.pitch, m.weather, 50)
            if not bw:
                break
            inn.current_bowler = bw
        L0, b0 = len(inn.over_log), inn.total_balls
        execute_ball_math_odi(m)
        if agg is not None:
            for e in inn.over_log[L0:]:
                if "wide" in e: agg["wides"] += 1
                elif "noball" in e: agg["noballs"] += 1
        if inn.total_balls > b0 and inn.total_balls % 6 == 0:
            inn.over_log.clear(); inn.bouncers_in_over = 0
            if hasattr(inn, "cutters_in_over"): inn.cutters_in_over = 0
            if hasattr(inn, "mystery_bowled_this_over"): inn.mystery_bowled_this_over = 0


def full(a, b, pitch, dsl=False):
    m = CricketMatch(a, b, format_overs=50, pitch=pitch, weather="Clear")
    if dsl: m.tournament_type = "dsl"
    agg = {"wides": 0, "noballs": 0}
    m.innings1 = InningsState(a, b); m.current_innings = m.innings1; m.current_innings_num = 1
    sim_inn(m, m.innings1, agg)
    m.target = m.innings1.total_runs + 1
    m.innings2 = InningsState(b, a); m.current_innings = m.innings2; m.current_innings_num = 2
    sim_inn(m, m.innings2, agg)
    return m, agg


def main():
    dsl = "dsl" in sys.argv
    print(f"n={N}/pitch · 85v85 · Clear{' · DSL MODE' if dsl else ''}\n")
    print("━━ PAR / WICKETS / EXTRAS / SPIN-STRIKE ━━")
    print(f"{'pitch':<9}{'par':>6}{'std':>5}{'wkts':>6}{'allout':>8}{'wides':>7}{'nb':>5}"
          f"{'spin wkt%':>10}{'spin ball%':>11}")
    for pitch in ALL_PITCHES:
        pars, wk, allout, wides, nbs = [], [], 0, [], []
        sw = pw = sb = pb = 0
        for _ in range(N):
            a = build_team("A", 85, 85, noise=0); b = build_team("B", 85, 85, noise=0)
            m, agg = full(a, b, pitch, dsl)
            pars.append(m.innings1.total_runs); wk.append(m.innings1.wickets)
            if m.innings1.wickets >= 10: allout += 1
            wides.append(agg["wides"] / 2); nbs.append(agg["noballs"] / 2)
            for inn in (m.innings1, m.innings2):
                for bw in inn.bowling_stats.values():
                    if bw.balls_bowled == 0: continue
                    if "Spin" in bw.profile["role"]: sw += bw.wickets_taken; sb += bw.balls_bowled
                    else: pw += bw.wickets_taken; pb += bw.balls_bowled
        print(f"{pitch:<9}{statistics.mean(pars):>6.0f}{statistics.stdev(pars):>5.0f}{statistics.mean(wk):>6.1f}"
              f"{100*allout/N:>7.1f}%{statistics.mean(wides):>7.1f}{statistics.mean(nbs):>5.1f}"
              f"{100*sw/max(1,sw+pw):>9.1f}%{100*sb/max(1,sb+pb):>10.1f}%")

    print("\n━━ CHASE MANNER (successful chases) + TAIL EXPOSURE ━━")
    print(f"{'pitch':<9}{'chase%':>8}{'balls left':>11}{'wkts':>6}{'>5ov left':>10}{'last 3ov':>9}"
          f"{'collapse L':>11}{'tail balls':>11}")
    for pitch in ('Flat', 'Hard', 'Dusty', 'Green', 'Slow'):
        bl, wkw, gt30, last18 = [], [], 0, 0
        wins = losses = ties = coll = 0
        tail_balls = []
        for _ in range(N):
            a = build_team("A", 85, 85, noise=0); b = build_team("B", 85, 85, noise=0)
            m, _ = full(a, b, pitch, dsl)
            i1, i2 = m.innings1, m.innings2
            tb = sum(bs.balls_faced for inn in (i1, i2) for bs in inn.batting_stats.values() if _is_tail(bs.profile))
            tail_balls.append(tb)
            if i2.total_runs > i1.total_runs:
                wins += 1; left = 300 - i2.total_balls
                bl.append(left); wkw.append(i2.wickets)
                if left > 30: gt30 += 1
                if left < 18: last18 += 1
            elif i2.total_runs == i1.total_runs: ties += 1
            else:
                losses += 1
                if i2.wickets >= 10 and i2.total_balls <= 270: coll += 1
        w = max(1, wins); L = max(1, losses)
        print(f"{pitch:<9}{100*wins/(N-ties):>7.1f}%{statistics.mean(bl):>11.1f}{statistics.mean(wkw):>6.1f}"
              f"{100*gt30/w:>9.1f}%{100*last18/w:>8.1f}%{100*coll/L:>10.1f}%{statistics.mean(tail_balls):>11.1f}")


if __name__ == "__main__":
    main()
