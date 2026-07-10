# Bowler realism audit — does a bowler's RATING show up in his FIGURES, and are
# those figures consistent match to match? Runs the identical controlled attack
# through BOTH engines so the approved T20 engine acts as the feel reference.
# Run from repo root:  python tools/bowler_audit.py [n] [pitch]
#
# Real-ODI anchors (modern era, frontline 10-over spells):
#   elite (93): econ ~4.6-5.0 · ~2.2 wkts/inn · avg ~25 · 4+ hauls ~10% · 0-fer ~15-20%
#   weak  (76): econ ~6.0-6.5 · ~1.0 wkts/inn · avg ~45
#   econ spread elite→weak ≥ 1.0 rpo · maidens ~1-2/inn (team) · econ SD ~0.9-1.2
import os
import random
import statistics
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from sim_harness import CricketMatch, InningsState, build_team
from t20_simulation import execute_ball_math_t20, get_smart_ai_bowler_t20
from odi_simulation import execute_ball_math_odi, get_smart_ai_bowler_odi

N = int(sys.argv[1]) if len(sys.argv) > 1 else 400
PITCH = sys.argv[2] if len(sys.argv) > 2 else "Hard"


def attack_team(name, pb1=93, pb2=76, sp1=85, sp2=80, ar=80, bat=85):
    """Same shape as sim_harness.build_team (noise=0) but with a controlled,
    spread-out bowling attack so rating->figures separation is measurable."""
    return {"name": name, "subs": [], "players": [
        {"name": f"{name}_OP1", "bat": bat,      "bowl": 35, "role": "Batter",    "archetype": "Aggressor"},
        {"name": f"{name}_OP2", "bat": bat,      "bowl": 35, "role": "Batter",    "archetype": "Anchor"},
        {"name": f"{name}_T3",  "bat": bat + 2,  "bowl": 35, "role": "Batter",    "archetype": "Anchor"},
        {"name": f"{name}_T4",  "bat": bat,      "bowl": 35, "role": "Batter",    "archetype": "Aggressor"},
        {"name": f"{name}_WK",  "bat": bat - 3,  "bowl": 20, "role": "Batter_WK", "archetype": "Finisher"},
        {"name": f"{name}_AR",  "bat": bat - 8,  "bowl": ar, "role": "All-Rounder_Pace",     "archetype": "Finisher"},
        {"name": f"{name}_T7",  "bat": bat - 12, "bowl": 75, "role": "All-Rounder_Spin_Off", "archetype": "Finisher"},
        {"name": f"{name}_PB1", "bat": 40, "bowl": pb1, "role": "Bowler_Pace",     "archetype": "Standard"},
        {"name": f"{name}_PB2", "bat": 40, "bowl": pb2, "role": "Bowler_Pace",     "archetype": "Finisher"},
        {"name": f"{name}_SP1", "bat": 35, "bowl": sp1, "role": "Bowler_Spin_Leg", "archetype": "Standard"},
        {"name": f"{name}_SP2", "bat": 33, "bowl": sp2, "role": "Bowler_Spin_Off", "archetype": "Standard"},
    ]}


def bowl_one_innings(bat_team, bowl_team, fmt, pitch, weather="Clear"):
    """Sim a single 1st innings (bat_team bats, bowl_team bowls). Returns
    (innings, {bowler: maidens})."""
    m = CricketMatch(bat_team, bowl_team, format_overs=fmt, pitch=pitch, weather=weather)
    ball = execute_ball_math_odi if fmt == 50 else execute_ball_math_t20
    pick = get_smart_ai_bowler_odi if fmt == 50 else get_smart_ai_bowler_t20
    m.innings1 = InningsState(bat_team, bowl_team)
    m.current_innings = m.innings1
    m.current_innings_num = 1
    inn = m.innings1
    maidens = defaultdict(int)
    over_start_runs = 0
    while inn.wickets < 10 and inn.total_balls < m.max_balls:
        if inn.total_balls % 6 == 0 and not inn.over_log:
            bw = pick(inn, pitch, weather, fmt)
            if not bw:
                break
            inn.current_bowler = bw
            over_start_runs = inn.bowling_stats[bw["name"]].runs_conceded
        b0 = inn.total_balls
        ball(m)
        if inn.total_balls > b0 and inn.total_balls % 6 == 0:
            st = inn.bowling_stats[inn.current_bowler["name"]]
            if st.runs_conceded == over_start_runs:
                maidens[inn.current_bowler["name"]] += 1
            inn.over_log.clear()
            inn.bouncers_in_over = 0
            if hasattr(inn, "cutters_in_over"): inn.cutters_in_over = 0
            if hasattr(inn, "mystery_bowled_this_over"): inn.mystery_bowled_this_over = False
    return inn, maidens


def run_block(fmt, pitch, n=N):
    label = "ODI" if fmt == 50 else "T20"
    per = defaultdict(lambda: {"balls": [], "runs": [], "wkts": [], "maid": [], "econ": []})
    team_wkts, team_runout, pars, team_maid = [], 0, [], []
    total_wkts_credited = 0
    for _ in range(n):
        a = attack_team("A")
        b = build_team("B", 85, 85, noise=0)
        inn, maid = bowl_one_innings(b, a, fmt, pitch)
        pars.append(inn.total_runs)
        team_wkts.append(inn.wickets)
        team_maid.append(sum(maid.values()))
        credited = 0
        for name, st in inn.bowling_stats.items():
            if st.balls_bowled == 0:
                continue
            credited += st.wickets_taken
            d = per[name]
            d["balls"].append(st.balls_bowled)
            d["runs"].append(st.runs_conceded)
            d["wkts"].append(st.wickets_taken)
            d["maid"].append(maid.get(name, 0))
            d["econ"].append(st.runs_conceded / st.balls_bowled * 6)
        total_wkts_credited += credited
        team_runout += inn.wickets - credited
    tw = sum(team_wkts)
    print(f"\n━━ {label} · {pitch} · n={n} · bat-85 opposition ━━")
    print(f"  team: par {statistics.mean(pars):.0f} · wkts/inn {statistics.mean(team_wkts):.2f} · "
          f"runout-share {100*team_runout/max(1,tw):.1f}% · maidens/inn {statistics.mean(team_maid):.2f}")
    print(f"  {'bowler':<7}{'bowl':>5}{'ov/in':>6}{'econ':>6}{'ecoSD':>6}{'wk/in':>6}{'avg':>6}{'SR':>5}"
          f"{'  P0':>5}{'P2+':>5}{'P3+':>5}{'P4+':>5}{'maid':>5}")
    order = ["A_PB1", "A_PB2", "A_SP1", "A_SP2", "A_AR", "A_T7"]
    ratings = {p["name"]: p["bowl"] for p in attack_team("A")["players"]}
    for name in order:
        d = per.get(name)
        if not d or not d["balls"]:
            continue
        inns = len(d["balls"])
        tot_r, tot_w, tot_b = sum(d["runs"]), sum(d["wkts"]), sum(d["balls"])
        econ = tot_r / tot_b * 6
        p = lambda k: 100 * sum(1 for w in d["wkts"] if w >= k) / inns
        p0 = 100 * sum(1 for w in d["wkts"] if w == 0) / inns
        print(f"  {name:<7}{ratings[name]:>5}{tot_b/6/inns:>6.1f}{econ:>6.2f}"
              f"{statistics.stdev(d['econ']):>6.2f}{tot_w/inns:>6.2f}"
              f"{tot_r/max(1,tot_w):>6.1f}{tot_b/max(1,tot_w):>5.0f}"
              f"{p0:>5.0f}{p(2):>5.0f}{p(3):>5.0f}{p(4):>5.0f}{statistics.mean(d['maid']):>5.2f}")
    return per


def rating_sweep(fmt, pitch, n=N):
    """One pace bowler's rating swept 70→94 (rest of attack fixed at 82):
    the rating→figures curve, i.e. 'does bowler skill matter'."""
    label = "ODI" if fmt == 50 else "T20"
    print(f"\n━━ {label} rating→figures sweep (A_PB1, attack rest=82) · {pitch} ━━")
    print(f"  {'rating':>6}{'ov/in':>6}{'econ':>6}{'ecoSD':>6}{'wk/in':>6}{'avg':>6}{'P0':>5}{'P3+':>5}")
    for r in (70, 78, 86, 94):
        econs, wkts, balls_t, runs_t, ovs = [], [], 0, 0, []
        for _ in range(n):
            a = attack_team("A", pb1=r, pb2=82, sp1=82, sp2=82, ar=82)
            b = build_team("B", 85, 85, noise=0)
            inn, _ = bowl_one_innings(b, a, fmt, pitch)
            st = inn.bowling_stats["A_PB1"]
            if st.balls_bowled == 0:
                continue
            econs.append(st.runs_conceded / st.balls_bowled * 6)
            wkts.append(st.wickets_taken)
            balls_t += st.balls_bowled
            runs_t += st.runs_conceded
            ovs.append(st.balls_bowled / 6)
        inns = len(econs)
        tot_w = sum(wkts)
        p0 = 100 * sum(1 for w in wkts if w == 0) / inns
        p3 = 100 * sum(1 for w in wkts if w >= 3) / inns
        print(f"  {r:>6}{statistics.mean(ovs):>6.1f}{runs_t/balls_t*6:>6.2f}{statistics.stdev(econs):>6.2f}"
              f"{tot_w/inns:>6.2f}{runs_t/max(1,tot_w):>6.1f}{p0:>5.0f}{p3:>5.0f}")


if __name__ == "__main__":
    random.seed(7)
    for fmt in (50, 20):
        run_block(fmt, PITCH)
    for fmt in (50, 20):
        rating_sweep(fmt, PITCH)
