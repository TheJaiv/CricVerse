# TBECS image previews: renders all four branded images with realistic fake data
# into test_previews/ so alignment can be eyeballed against the templates.
# Run from the repo root: python tools/tbecs_preview.py

import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from league.tbecs_manager import (
    TBECS_CONFIG, build_goat_teams, tbecs_split_groups, tbecs_generate_group_stage,
    tbecs_generate_next, tbecs_fill_default_identity, goat_team_names,
)
from league.tournament_manager import (
    generate_tbecs_group_table, generate_tbecs_super20_table, generate_tbecs_bracket,
)

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "test_previews")
os.makedirs(OUT, exist_ok=True)

rng = random.Random(42)

NAMES = ["Mumbai Mavericks", "Delhi Dynamos", "Chennai Chargers", "Kolkata Kings",
         "Punjab Panthers", "Rajasthan Royals X", "Bangalore Blasters", "Hyderabad Hawks",
         "Gujarat Gladiators", "Lucknow Lions", "Goa Guardians", "Kerala Krakens",
         "Pune Predators", "Indore Invincibles", "Nagpur Ninjas", "Jaipur Jaguars",
         "Surat Strikers", "Kanpur Knights", "Patna Pirates", "Bhopal Bulls",
         "Ranchi Raptors", "Raipur Rhinos", "Amritsar Aces", "Varanasi Vipers",
         "Agra Avengers", "Meerut Marshals", "Thane Titans", "Nashik Nomads",
         "Vadodara Vikings", "Rajkot Renegades", "Madurai Monarchs", "Coimbatore Cobras",
         "Mysore Mustangs", "Hubli Hurricanes", "Vizag Vultures", "Guntur Giants",
         "Warangal Warriors", "Cuttack Cyclones", "Rourkela Rockets", "Durgapur Dragons",
         "Asansol Arrows", "Siliguri Spartans", "Gaya Griffins", "Dhanbad Daredevils",
         "Jamshedpur Jets", "Bokaro Bombers", "Aligarh Archers", "Bareilly Bears",
         "Moradabad Mavens", "Saharanpur Sharks", "Gorakhpur Ghosts", "Jhansi Javelins",
         "Ajmer Aztecs", "Udaipur Ultras"]


def build():
    teams = [{"name": n, "owner_id": str(1000 + i), "squad": []} for i, n in enumerate(NAMES)]
    t = {"server_id": "999", "name": "TBECS Season 1", "managers": ["1"],
         "teams": teams + build_goat_teams(1),
         "status": "active", "schedule": [], "current_match_idx": 0, "stats": {},
         "format_overs": 20, "tournament_type": "tbecs",
         "match_order": "random", "stadium_mode": "linked", "conditions_mode": "home"}
    tbecs_split_groups(t, rng)
    tbecs_fill_default_identity(t)   # default colours + generated initials logos
    tbecs_generate_group_stage(t, rng)
    return t


def fake_result(m, winner=None):
    t1r, t2r = rng.randint(120, 225), rng.randint(120, 225)
    if winner == m["team1"] or (winner is None and t1r > t2r):
        t1r = max(t1r, t2r + 1); w = m["team1"]
    else:
        t2r = max(t2r, t1r + 1); w = m["team2"]
    m["status"] = "completed"
    m["result"] = {"winner": w, "loser": m["team2"] if w == m["team1"] else m["team1"],
                   "format_overs": 20,
                   "t1_runs": t1r, "t1_wickets": rng.randint(2, 10), "t1_balls": rng.randint(100, 120),
                   "t2_runs": t2r, "t2_wickets": rng.randint(2, 10), "t2_balls": rng.randint(100, 120)}


def finish(t, goat_wins=True):
    goats = goat_team_names(t)
    for m in t["schedule"]:
        if m["status"] == "pending":
            gw = next((x for x in (m["team1"], m["team2"]) if x in goats), None)
            fake_result(m, winner=gw if (goat_wins and gw) else None)


t = build()
# mid-group-stage snapshot for the group tables (some played, some not)
for m in rng.sample(t["schedule"], 500):
    fake_result(m)
with open(os.path.join(OUT, "_snap.txt"), "w") as fh:
    fh.write("group tables rendered mid-stage (500/756 played)\n")
open(os.path.join(OUT, "tbecs_group_A_test.png"), "wb").write(generate_tbecs_group_table(t, "A").read())
open(os.path.join(OUT, "tbecs_group_B_test.png"), "wb").write(generate_tbecs_group_table(t, "B").read())

# finish groups (GOATs top the table for the full effect) -> Super 20 -> table
finish(t)
ok, msg = tbecs_generate_next(t, rng); assert ok, msg
for m in rng.sample([m for m in t["schedule"] if m.get("stage") == "super20"], 120):
    fake_result(m)
open(os.path.join(OUT, "tbecs_super20_test.png"), "wb").write(generate_tbecs_super20_table(t).read())

# knockouts all the way to a champion for the bracket
finish(t)
ok, msg = tbecs_generate_next(t, rng); assert ok, msg   # QFs
finish(t)
ok, msg = tbecs_generate_next(t, rng); assert ok, msg   # SFs
finish(t)
ok, msg = tbecs_generate_next(t, rng); assert ok, msg   # Final
finish(t)
open(os.path.join(OUT, "tbecs_bracket_test.png"), "wb").write(generate_tbecs_bracket(t).read())

# scorecard: full fake match data through the from_data bridge
from core.subscription_manager import DB_CACHE
DB_CACHE["tournaments"] = [t]
from bot import generate_tbecs_scorecard_from_data

def _bats(prefix, total):
    out, left = [], total
    for i in range(1, 8):
        r = max(0, min(left, rng.randint(0, 68)))
        left -= r
        out.append({"name": f"{prefix} Batter {i}", "runs": r, "balls": max(1, int(r / rng.uniform(1.0, 1.9))),
                    "fours": r // 12, "sixes": r // 20,
                    "dismissal": "not out" if i == 7 else rng.choice(["b Speed Demon", "c Fielder b Chaos Theory", "lbw b Web Weaver", "run out"])})
    return out

def _bowls(prefix):
    return [{"name": f"{prefix} Bowler {i}", "overs": f"{rng.randint(2, 4)}.0",
             "runs": rng.randint(18, 42), "wickets": rng.randint(0, 3), "maidens": rng.randint(0, 1)}
            for i in range(1, 8)]

data = {
    "server_id": "999",
    "t1": {"name": "Mumbai Mavericks", "raw_name": "Mumbai Mavericks", "runs": 187, "wickets": 6, "balls": 120,
           "batters": _bats("MM", 187), "bowlers": _bowls("GA"), "extras": [9, 1, 2, 2, 4]},
    "t2": {"name": "GOAT XI Apex", "raw_name": "GOAT XI Apex", "runs": 190, "wickets": 4, "balls": 112,
           "batters": _bats("GA", 190), "bowlers": _bowls("MM"), "extras": [7, 0, 3, 1, 3]},
    "result_str": "GOAT XI APEX WON BY 6 WICKETS", "potm": "GA Batter 1",
}
buf = generate_tbecs_scorecard_from_data(data)
open(os.path.join(OUT, "tbecs_scorecard_test.png"), "wb").write(buf.read())

print("previews written to test_previews/:")
for f in sorted(os.listdir(OUT)):
    if f.startswith("tbecs"):
        print("  ", f)
