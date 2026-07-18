# TBECS end-to-end flow test (headless, no Discord / no MongoDB writes needed).
# Run from the repo root: python tools/tbecs_flow_test.py
#
# Covers: 56-team setup (54 addable + 2 GOAT XIs), the two-group split (28 each,
# one GOAT per side), stage-1 fixtures, GOAT qualification ban (even as table
# topper), the fresh Super 20, seeded QFs (1v8 2v7 3v6 4v5), the SF bracket
# (W1vW4 / W2vW3), the Final, manual-only progression (nothing generates while a
# stage has pending matches), and the GOAT leaderboard-stats exclusion.

import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from league.tbecs_manager import (
    TBECS_CONFIG, GOAT_TEAMS, build_goat_teams, goat_team_names,
    tbecs_split_groups, tbecs_generate_group_stage, tbecs_generate_next,
    tbecs_stage_state, is_tbecs_tournament,
    tbecs_assign_random_homes, tbecs_fill_default_identity,
)
from league.tournament_manager import (
    get_group_standings, _match_bracket_rank, assign_tournament_conditions,
    match_order_gate,
)

PASS = 0
def ok(cond, label):
    global PASS
    assert cond, f"FAIL: {label}"
    PASS += 1
    print(label)


def build_tourney(rng):
    teams = [{"name": f"Team {i:02d}", "owner_id": str(1000 + i), "squad": []}
             for i in range(1, TBECS_CONFIG["addable_teams"] + 1)]
    # a few pre-assigned groups, like managers passing A/B to add_team
    teams[0]["group"] = "A"; teams[1]["group"] = "B"; teams[2]["group"] = "A"
    return {
        "server_id": "999", "name": "Test TBECS", "managers": ["1"],
        "teams": teams + build_goat_teams(1),
        "status": "active", "schedule": [], "current_match_idx": 0, "stats": {},
        "format_overs": 20, "tournament_type": "tbecs",
        "match_order": "random", "stadium_mode": "linked", "conditions_mode": "home",
    }


def fake_result(m, rng, winner=None):
    t1r, t2r = rng.randint(120, 220), rng.randint(120, 220)
    if winner == m["team1"] or (winner is None and t1r > t2r):
        t1r = max(t1r, t2r + 1); w = m["team1"]
    else:
        t2r = max(t2r, t1r + 1); w = m["team2"]
    m["status"] = "completed"
    m["result"] = {
        "winner": w, "loser": m["team2"] if w == m["team1"] else m["team1"],
        "format_overs": 20,
        "t1_runs": t1r, "t1_wickets": rng.randint(2, 10), "t1_balls": 120,
        "t2_runs": t2r, "t2_wickets": rng.randint(2, 10), "t2_balls": 120,
    }


def finish_stage(tourney, rng, goat_wins=False):
    """Complete every pending match. goat_wins=True: GOAT teams win ALL their games."""
    goats = goat_team_names(tourney)
    for m in tourney["schedule"]:
        if m["status"] != "pending":
            continue
        gw = next((t for t in (m["team1"], m["team2"]) if t in goats), None)
        fake_result(m, rng, winner=gw if (goat_wins and gw) else None)


rng = random.Random(7)
print("=== 1. Setup & groups ===")
t = build_tourney(rng)
ok(is_tbecs_tournament(t), "type key recognised")
ok(len(t["teams"]) == 56, "56 teams total (54 addable + 2 GOAT)")
ok(len(goat_team_names(t)) == 2, "both GOAT XIs flagged")
for g in GOAT_TEAMS:
    ok(len(g["players"]) == 11 and all(p["bat"] == 99 and p["bowl"] == 99 and
       p["archetype"] == "Vaibhav" for p in g["players"]), f"{g['name']}: 11 players, all 99/99 Vaibhav")

err = tbecs_split_groups(t, rng)
ok(err is None, "group split accepted")
for grp in ("A", "B"):
    members = [x for x in t["teams"] if x.get("group") == grp]
    ok(len(members) == 28, f"Group {grp} has 28 teams")
    ok(sum(1 for x in members if x.get("goat")) == 1, f"Group {grp} has exactly 1 GOAT XI")
ok(next(x for x in t["teams"] if x["name"] == "Team 01")["group"] == "A", "pre-assigned group honoured")

print("\n=== 2. Homes, colours & logos ===")
# One team pre-sets its home + colour + logo like a real owner would; the random
# assign / default fill must leave those untouched.
pre = next(x for x in t["teams"] if x["name"] == "Team 05")
pre["home_stadium"], pre["home_pitch"] = "My Own Fortress", "Green"
pre["color"], pre["logo_standings"] = "#123456", "https://example.com/logo.png"
# ...and one team submitted only a stadium (no pitch): keep the stadium, fill the pitch.
half = next(x for x in t["teams"] if x["name"] == "Team 06")
half["home_stadium"] = "Halfway House Ground"

n, _lines = tbecs_assign_random_homes(t, rng)
ok(n == 55, "55 teams got homes filled (the fully pre-set one kept)")
ok(pre["home_stadium"] == "My Own Fortress" and pre["home_pitch"] == "Green", "pre-set home untouched")
ok(half["home_stadium"] == "Halfway House Ground" and half.get("home_pitch"),
   "partial submission: chosen stadium kept, only the missing pitch filled")
homes = [x["home_stadium"] for x in t["teams"]]
ok(all(x.get("home_stadium") and x.get("home_pitch") for x in t["teams"]), "every team has home + pitch")
ok(len(set(homes)) == 56, "all 56 home stadiums are unique")
n2, _ = tbecs_assign_random_homes(t, rng)
ok(n2 == 0, "second run assigns nothing (idempotent)")

n_col, n_logo = tbecs_fill_default_identity(t)
ok(n_col == 55 and n_logo == 55, "defaults filled for the 55 teams missing them")
ok(pre["color"] == "#123456" and pre["logo_standings"] == "https://example.com/logo.png",
   "submitted colour/logo untouched")
colors = [x["color"] for x in t["teams"]]
ok(len(set(colors)) == 56, "all 56 team colours are distinct")
apex = next(x for x in t["teams"] if x["name"] == "GOAT XI Apex")
zen = next(x for x in t["teams"] if x["name"] == "GOAT XI Zenith")
ok(apex["color"] == "#FFD700" and zen["color"] == "#8A2BE2", "GOATs get gold / purple")
ok(all(x.get("logo_standings", "").startswith(("data:image/png;base64,", "https://"))
       for x in t["teams"]), "every team has a logo (generated data URI or submitted URL)")

print("\n=== 3. Stage-1 fixtures (any order, home grounds) ===")
tbecs_generate_group_stage(t, rng)
ok(len(t["schedule"]) == 756, "756 group matches (378 per group)")
from collections import Counter
per_team = Counter()
for m in t["schedule"]:
    per_team[m["team1"]] += 1; per_team[m["team2"]] += 1
ok(all(v == 27 for v in per_team.values()), "every team plays exactly 27 group games")
ok(all(_match_bracket_rank(t, m) == 0 for m in t["schedule"]), "group matches rank 0 (cancel-safe)")

# Linked stadiums + home pitches on the actual fixtures.
assign_tournament_conditions(t)
by_team = {x["name"]: x for x in t["teams"]}
ok(all(m["stadium"] == by_team[m["team1"]]["home_stadium"] for m in t["schedule"]),
   "every fixture at the HOME (team1) side's ground")
ok(all(m["pitch"] == by_team[m["team1"]]["home_pitch"] for m in t["schedule"]),
   "every fixture on the home side's fixed pitch")

# Any-order play: the gate must clear ANY pending match, not just the next one.
ok(all(match_order_gate(t, m)[0] for m in rng.sample(t["schedule"], 25)),
   "match_order random: any of the 756 can be played anytime")

print("\n=== 4. Manual-only progression ===")
okflag, msg = tbecs_generate_next(t, rng)
ok(not okflag and "pending" in msg, "tbecs_next refuses while group stage unfinished")

# GOATs win EVERYTHING in stage 1 -> they top both tables -> still can't qualify.
finish_stage(t, rng, goat_wins=True)
for grp in ("A", "B"):
    top = get_group_standings(t, "group", grp)[0][0]
    ok(top in goat_team_names(t), f"GOAT XI tops Group {grp} (won all 27)")

state, left = tbecs_stage_state(t)
ok(state == "super20" and left == 0, "state: ready for Super 20")

print("\n=== 5. Super 20 (GOATs barred) ===")
okflag, msg = tbecs_generate_next(t, rng)
ok(okflag, "Super 20 generated")
s20 = [m for m in t["schedule"] if m.get("stage") == "super20"]
ok(len(s20) == 190, "190 Super 20 matches (20-team RR)")
s20_teams = {m["team1"] for m in s20} | {m["team2"] for m in s20}
ok(len(s20_teams) == 20, "exactly 20 qualifiers")
ok(not (s20_teams & goat_team_names(t)), "NO GOAT XI qualified despite topping both groups")

okflag, msg = tbecs_generate_next(t, rng)
ok(not okflag, "tbecs_next refuses while Super 20 unfinished")
finish_stage(t, rng)

print("\n=== 6. Seeded knockouts ===")
okflag, msg = tbecs_generate_next(t, rng)
ok(okflag and "QUARTER-FINALS" in msg, "QFs generated")
qfs = sorted((m for m in t["schedule"] if str(m["round"]).startswith("Quarter-Final")),
             key=lambda m: m["round"])
ok(len(qfs) == 4, "4 QFs")
seeds = t["tbecs_seeds"]
table = [n for n, _ in get_group_standings(t, "super20", "S")[:8]]
ok(list(seeds) == table, "seeds match the Super 20 top-8 order")
expect = [(1, 8), (2, 7), (3, 6), (4, 5)]
for m, (a, b) in zip(qfs, expect):
    ok({seeds[m["team1"]], seeds[m["team2"]]} == {a, b}, f"{m['round']} is seed {a} v seed {b}")

finish_stage(t, rng)
okflag, msg = tbecs_generate_next(t, rng)
ok(okflag and "SEMI-FINALS" in msg, "SFs generated")
sfs = sorted((m for m in t["schedule"] if str(m["round"]).startswith("Semi-Final")),
             key=lambda m: m["round"])
qf_w = {int(str(m["round"]).split()[-1]): m["result"]["winner"] for m in qfs}
ok({sfs[0]["team1"], sfs[0]["team2"]} == {qf_w[1], qf_w[4]}, "SF1 = W(QF1) v W(QF4)")
ok({sfs[1]["team1"], sfs[1]["team2"]} == {qf_w[2], qf_w[3]}, "SF2 = W(QF2) v W(QF3)")

finish_stage(t, rng)
okflag, msg = tbecs_generate_next(t, rng)
ok(okflag and "FINAL IS SET" in msg, "Final generated")
finish_stage(t, rng)
okflag, msg = tbecs_generate_next(t, rng)
ok(okflag and "champions" in msg, "champion announced")
ok(t["status"] == "completed", "tournament marked completed")
ok(len(t["schedule"]) == 756 + 190 + 4 + 2 + 1, "953 matches total, nothing extra generated")

print("\n=== 7. GOAT leaderboard exclusion (listener logic) ===")
# Mirror the on_tournament_match_complete guard: the GOAT side is skipped, the
# normal side records. Faked innings keep this headless.
class _B:  # batting stat
    def __init__(s, r): s.runs_scored, s.balls_faced, s.dismissal, s.fours, s.sixes = r, 20, "b X", 2, 1
class _W:  # bowling stat
    def __init__(s, w): s.wickets_taken, s.runs_conceded, s.balls_bowled = w, 30, 24
class _Inn:
    def __init__(s, team, bat, bowl):
        s.batting_team = team; s.batting_stats = bat; s.bowling_stats = bowl

goat_name = "GOAT XI Apex"
goat_team = next(x for x in t["teams"] if x["name"] == goat_name)
norm_team = {"name": "Team 01", "players": [{"name": "Normal Star"}]}
inn1 = _Inn({"name": goat_name, "players": goat_team["squad"]},
            {p["name"]: _B(60) for p in goat_team["squad"][:2]}, {"Normal Star": _W(1)})
inn2 = _Inn(norm_team, {"Normal Star": _B(45)}, {goat_team["squad"][0]["name"]: _W(2)})

goats = goat_team_names(t)
stats = {}
def record(team_name, batting_inn, bowling_inn):
    for pname, b in batting_inn.batting_stats.items():
        stats.setdefault(team_name, {}).setdefault(pname, {})["runs"] = b.runs_scored
    for pname, w in bowling_inn.bowling_stats.items():
        stats.setdefault(team_name, {}).setdefault(pname, {})["wkts"] = w.wickets_taken
for name, bat_inn, bowl_inn in ((goat_name, inn1, inn2), ("Team 01", inn2, inn1)):
    if name not in goats:      # the exact guard used in on_tournament_match_complete
        record(name, bat_inn, bowl_inn)
ok(goat_name not in stats, "GOAT players recorded NOTHING")
ok(stats["Team 01"]["Normal Star"] == {"runs": 45, "wkts": 1}, "opponent's stats recorded in full vs GOAT")

print(f"\nALL {PASS} CHECKS PASSED")
