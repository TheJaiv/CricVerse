# Measures Jaiv's two complaints:
# 1. Tail (48-bat bowlers) scoring vs 95+ elite attack and vs 80 attack.
# 2. Death-chase "cheat code": win% when the ask is steep at the death, and
#    chase RR by phase (does the bot idle then reliably blast at 8+?).
import random, statistics, sys
import os
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT); sys.path.insert(0, ROOT + "/tools")
from sim_harness import CricketMatch, InningsState, build_team, run_full_match
from engine.odi_simulation import execute_ball_math_odi, get_smart_ai_bowler_odi

N = int(sys.argv[1]) if len(sys.argv) > 1 else 400
PITCH = "Hard"


def team_with_tail(name, bowl, tail_bat=48):
    t = build_team(name, 85, bowl, noise=0)
    for p in t["players"]:
        if "Bowler" in p["role"]:
            p["bat"] = tail_bat
    return t


def tail_check(attack_rating):
    random.seed(31)
    scores, balls, sr_pool, dis_balls = [], [], [], []
    p20 = 0
    tot = 0
    for _ in range(N):
        bat_team = team_with_tail("B", 80)          # its tail bats at 48
        bowl_team = build_team("A", 85, attack_rating, noise=0)
        m = CricketMatch(bat_team, bowl_team, 50, PITCH, "Clear")
        m.innings1 = InningsState(bat_team, bowl_team)
        m.current_innings = m.innings1; m.current_innings_num = 1
        inn = m.innings1
        while inn.wickets < 10 and inn.total_balls < 300:
            if inn.total_balls % 6 == 0 and not inn.over_log:
                bw = get_smart_ai_bowler_odi(inn, PITCH, "Clear", 50)
                if not bw: break
                inn.current_bowler = bw
            execute_ball_math_odi(m)
            if inn.total_balls % 6 == 0 and inn.total_balls and not len(inn.over_log) % 999:
                pass
            if inn.total_balls % 6 == 0 and inn.total_balls:
                inn.over_log.clear(); inn.bouncers_in_over = 0
                inn.cutters_in_over = 0; inn.mystery_bowled_this_over = False
        for bs in inn.batting_stats.values():
            if float(bs.profile.get("bat", 99)) <= 50 and bs.balls_faced > 0:
                tot += 1
                scores.append(bs.runs_scored); balls.append(bs.balls_faced)
                if bs.balls_faced >= 5:
                    sr_pool.append(bs.runs_scored / bs.balls_faced * 100)
                if bs.runs_scored >= 20: p20 += 1
                if bs.dismissal != "not out":
                    dis_balls.append(bs.balls_faced)
    print(f"  48-bat tail vs {attack_rating}-attack: avg {statistics.mean(scores):.1f} "
          f"off {statistics.mean(balls):.1f} balls · SR {statistics.mean(sr_pool):.0f} · "
          f"P(20+) {100*p20/tot:.1f}% · balls-to-dismissal {statistics.mean(dis_balls):.1f}")


def chase_death_check():
    random.seed(32)
    steep_n = steep_w = 0
    ph_runs = {"pp": 0, "mid": 0, "death": 0}; ph_balls = dict.fromkeys(ph_runs, 0)
    for _ in range(N * 3):
        a = build_team("A", 85, 85, noise=0); b = build_team("B", 85, 85, noise=0)
        m = CricketMatch(a, b, 50, PITCH, "Clear")
        m.innings1 = InningsState(a, b); m.current_innings = m.innings1; m.current_innings_num = 1
        inn = m.innings1
        while inn.wickets < 10 and inn.total_balls < 300:
            if inn.total_balls % 6 == 0 and not inn.over_log:
                bw = get_smart_ai_bowler_odi(inn, PITCH, "Clear", 50)
                if not bw: break
                inn.current_bowler = bw
            execute_ball_math_odi(m)
            if inn.total_balls % 6 == 0 and inn.total_balls:
                inn.over_log.clear(); inn.bouncers_in_over = 0
                inn.cutters_in_over = 0; inn.mystery_bowled_this_over = False
        m.target = inn.total_runs + 1
        m.innings2 = InningsState(b, a); m.current_innings = m.innings2; m.current_innings_num = 2
        inn2 = m.innings2
        snap = None
        while inn2.wickets < 10 and inn2.total_balls < 300 and inn2.total_runs < m.target:
            if inn2.total_balls % 6 == 0 and not inn2.over_log:
                bw = get_smart_ai_bowler_odi(inn2, PITCH, "Clear", 50)
                if not bw: break
                inn2.current_bowler = bw
            if inn2.total_balls == 240:   # start of death
                need = m.target - inn2.total_runs
                rrr = need / 60 * 6
                if 7.5 <= rrr <= 9.5 and inn2.wickets <= 5:
                    snap = True
            over = inn2.total_balls // 6
            ph = "pp" if over < 10 else ("mid" if over < 40 else "death")
            r0, b0 = inn2.total_runs, inn2.total_balls
            execute_ball_math_odi(m)
            ph_runs[ph] += inn2.total_runs - r0
            if inn2.total_balls > b0: ph_balls[ph] += 1
            if inn2.total_balls % 6 == 0 and inn2.total_balls:
                inn2.over_log.clear(); inn2.bouncers_in_over = 0
                inn2.cutters_in_over = 0; inn2.mystery_bowled_this_over = False
        if snap:
            steep_n += 1
            if inn2.total_runs >= m.target: steep_w += 1
    rr = {p: ph_runs[p] / max(1, ph_balls[p] / 6) for p in ph_runs}
    print(f"  chase RR by phase: pp {rr['pp']:.2f} · mid {rr['mid']:.2f} · death {rr['death']:.2f}")
    print(f"  steep death ask (rrr 7.5-9.5 at over 40, <=5 down): win {100*steep_w/max(1,steep_n):.0f}%  (n={steep_n})"
          f"   [real-cricket feel: ~30-40%]")


if __name__ == "__main__":
    which = sys.argv[2] if len(sys.argv) > 2 else "both"
    if which in ("both", "tail"):
        tail_check(96); tail_check(80)
    if which in ("both", "chase"):
        chase_death_check()
