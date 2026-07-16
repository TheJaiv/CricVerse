# Custom tournament end-to-end flow test (headless, no Discord / no Mongo writes).
# Run from the repo root: python tools/custom_flow_test.py
#
# Covers: the bracket mini-language (parse + all six rejection rules), config
# validation, and full simulated seasons over a matrix of configs - multi-stage
# regrouping (seeded/random), points carry-over, seeded playoff entry, custom
# ladders (the IPL-style w/l example), league-only champions, 16-team knockouts,
# and revert behaviour (league + knockout).

import os
import sys
import random
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from league.custom_tournament import (
    parse_custom_bracket, default_knockout_bracket, default_bracket_labels,
    custom_config_errors, custom_generate_first_stage, custom_try_advance,
    custom_stage_standings, custom_start_error, stage_key, stage_letters,
    custom_total_matches, build_custom_status_pages,
)
from league.tournament_manager import revert_tournament_match, _match_bracket_rank

PASS = 0
def ok(cond, label):
    global PASS
    assert cond, f"FAIL: {label}"
    PASS += 1
    print(f"{label}")


def build_tourney(cfg, n_teams):
    st0 = cfg["stages"][0]
    letters = stage_letters(st0)
    teams = []
    for i in range(n_teams):
        t = {"name": f"T{i + 1:02d}", "owner_id": str(100 + i), "squad": []}
        if st0.get("assignment") == "manual":
            t["group"] = letters[i // st0["teams_per_group"]]
        teams.append(t)
    return {
        "server_id": "999", "name": "Test Custom", "managers": ["1"],
        "teams": teams, "status": "active", "schedule": [], "current_match_idx": 0,
        "stats": {}, "format_overs": 20, "tournament_type": "custom",
        "custom_config": cfg,
    }


def fake_result(m, rng):
    t1r, t2r = rng.randint(120, 220), rng.randint(120, 220)
    if t1r == t2r:
        t1r += 1
    winner = m["team1"] if t1r > t2r else m["team2"]
    m["status"] = "completed"
    m["result"] = {
        "winner": winner, "loser": m["team2"] if winner == m["team1"] else m["team1"],
        "format_overs": 20,
        "t1_runs": t1r, "t1_wickets": rng.randint(2, 10), "t1_balls": 120,
        "t2_runs": t2r, "t2_wickets": rng.randint(2, 10), "t2_balls": 120,
    }
    return winner


def play_out(tourney, rng, max_steps=1000):
    """Complete pending matches one at a time (advance after each, like the real
    hook) until nothing is pending and no new matches appear."""
    for _ in range(max_steps):
        m = next((x for x in tourney["schedule"] if x["status"] == "pending"), None)
        if m is None:
            return
        fake_result(m, rng)
        custom_try_advance(tourney)
    raise AssertionError("play_out did not converge")


# 1. Bracket language
def test_bracket_language():
    print("\n[1] Bracket mini-language")
    jaiv = "match1 : 1 vs 2\nmatch2 : 3 vs 4\nmatch3 : wmatch2 vs lmatch1\nmatch4(final): wmatch3 vs wmatch1"
    ms, err = parse_custom_bracket(jaiv, 4)
    ok(err is None, "the spec example (IPL-style ladder, matchN prefixes) parses")
    ok([(m["t1"], m["t2"]) for m in ms] == [("S1", "S2"), ("S3", "S4"), ("W2", "L1"), ("W3", "W1")],
       "tokens normalize to S/W/L form")
    ok(ms[3]["final"] and not ms[0]["final"], "(final) tag lands on match 4 only")
    ok([m["depth"] for m in ms] == [1, 1, 2, 3], "feeder depths computed (1,1,2,3)")

    ms, err = parse_custom_bracket("1 vs 2\n3 vs 4\nw2 vs l1\nfinal: w3 vs w1", 4)
    ok(err is None and len(ms) == 4, "bare short form parses too")
    ms, err = parse_custom_bracket("seed1 v winner of match nothing", 2)
    ok(err is not None, "garbage slot rejected")

    bad = [
        ("1 vs 2\nfinal: w1 vs w3", 2, "forward/unknown reference"),
        ("1 vs 2\n1 vs 3\nfinal: w1 vs w2", 3, "seed enters twice"),
        ("1 vs 2\n3 vs w1\n4 vs w1\nfinal: w2 vs w3", 4, "winner consumed twice"),
        ("1 vs 2\n3 vs 4\nw1 vs w2", 4, "no final marked"),
        ("final: 1 vs 2\n3 vs 4", 4, "final not last"),
        ("1 vs 2\n3 vs 4\nfinal: w1 vs 5", 5, "dead-end winner (match 2 goes nowhere)"),
        ("1 vs 1\nfinal: w1 vs 2", 2, "team vs itself"),
        ("1 vs 2\nfinal: w1 vs 9", 3, "seed out of range"),
    ]
    for text, n, why in bad:
        _, err = parse_custom_bracket(text, n)
        ok(err is not None, f"rejected: {why}")

    for n in (2, 4, 8, 16):
        br = default_knockout_bracket(n)
        _, err = (br, None) if br is None else (None, None)
        ok(br is not None and br[-1]["final"], f"default knockout for {n} seeds ends in a final")
        # the default brackets must satisfy the same rules the parser enforces
        from league.custom_tournament import _check_bracket
        checked, err = _check_bracket([dict(m) for m in br], n)
        ok(err is None, f"default {n}-seed bracket passes validation")


# 2. Config validation
def test_config_validation():
    print("\n[2] Config validation")
    good = {"stages": [{"groups": 2, "teams_per_group": 4, "legs": 1, "qualify": 2, "assignment": "manual"}],
            "playoff": {"mode": "bracket", "matches": default_knockout_bracket(4)}}
    ok(custom_config_errors(good) == [], "clean 2x4 -> semis config validates")

    uneven = {"stages": [{"groups": 2, "teams_per_group": 3, "legs": 1, "qualify": 1, "assignment": "manual"},
                         {"groups": 3, "legs": 1, "qualify": 1, "regroup": "seeded", "carry_points": False}],
              "playoff": {"mode": "bracket", "matches": default_knockout_bracket(2)}}
    ok(custom_config_errors(uneven), "2 qualifiers into 3 groups rejected")

    tabletop = {"stages": [{"groups": 2, "teams_per_group": 3, "legs": 1, "qualify": 0, "assignment": "manual"}],
                "playoff": {"mode": "none"}}
    ok(custom_config_errors(tabletop), "league-only champion with 2 groups rejected")

    monster = {"stages": [{"groups": 1, "teams_per_group": 16, "legs": 4, "qualify": 0, "assignment": "random"}],
               "playoff": {"mode": "none"}}
    ok(custom_config_errors(monster), f"{custom_total_matches(monster)}-match config over the cap rejected")


# 3. League-only (no playoffs)
def test_league_only(rng):
    print("\n[3] League-only — table decides the champion")
    cfg = {"stages": [{"groups": 1, "teams_per_group": 4, "legs": 2, "qualify": 0, "assignment": "random"}],
           "playoff": {"mode": "none"}}
    t = build_tourney(cfg, 4)
    ok(custom_start_error(t) is None, "start validation passes")
    ok(custom_generate_first_stage(t) is None, "stage 1 generates")
    ok(len(t["schedule"]) == 12, "4 teams x double RR = 12 matches")
    per_round = defaultdict(list)
    for m in t["schedule"]:
        per_round[m["round"]] += [m["team1"], m["team2"]]
    ok(all(len(v) == len(set(v)) for v in per_round.values()), "no team plays twice in a round")
    play_out(t, rng)
    table = custom_stage_standings(t, 0, "A")
    ok(t["status"] == "completed", "season completes with no bracket")
    ok(t.get("custom_champion") == table[0][0], "champion = top of table")
    ok(t.get("custom_runner_up") == table[1][0], "runner-up = 2nd")


# 4. Groups -> simple knockout
def test_groups_knockout(rng):
    print("\n[4] 2 groups -> default semis + final")
    cfg = {"stages": [{"groups": 2, "teams_per_group": 4, "legs": 1, "qualify": 2, "assignment": "manual"}],
           "playoff": {"mode": "bracket", "matches": default_knockout_bracket(4)}}
    t = build_tourney(cfg, 8)
    ok(custom_generate_first_stage(t) is None, "stage 1 generates")
    ok(len(t["schedule"]) == 12, "2 groups x C(4,2) = 12 group matches")
    play_out(t, rng)
    ko = [m for m in t["schedule"] if m["stage"] == "knockout"]
    ok(len(ko) == 3, "3 knockout matches appear")
    ok([m["round"] for m in ko] == ["Semi-Final 1", "Semi-Final 2", "Final"], "default labels")
    # seeds are rank-major: S1=A1 S2=B1 S3=A2 S4=B2; SF1 = S1vS4, SF2 = S2vS3
    a = [n for n, _ in custom_stage_standings(t, 0, "A")][:2]
    b = [n for n, _ in custom_stage_standings(t, 0, "B")][:2]
    ok(t["custom_seeds"] == [a[0], b[0], a[1], b[1]], "seed order is A1,B1,A2,B2")
    sf1 = ko[0]
    ok({sf1["team1"], sf1["team2"]} == {a[0], b[1]}, "SF1 is A1 v B2 (1v4 cross)")
    fin = ko[2]
    ok(t["status"] == "completed" and t["custom_champion"] == fin["result"]["winner"],
       "champion = final winner")


# 5. The spec example: custom IPL-style ladder
def test_custom_ladder(rng):
    print("\n[5] Custom ladder — w/l references resolve progressively")
    ms, err = parse_custom_bracket("1 vs 2\n3 vs 4\nw2 vs l1\nfinal: w3 vs w1", 4)
    assert err is None
    ms = default_bracket_labels(ms)
    cfg = {"stages": [{"groups": 2, "teams_per_group": 3, "legs": 1, "qualify": 2, "assignment": "manual"}],
           "playoff": {"mode": "bracket", "matches": ms}}
    t = build_tourney(cfg, 6)
    ok(custom_generate_first_stage(t) is None, "stage 1 generates")
    play_out(t, rng)
    ko = {m["bracket_n"]: m for m in t["schedule"] if m["stage"] == "knockout"}
    ok(len(ko) == 4, "all 4 ladder matches played")
    w1 = ko[1]["result"]["winner"]; l1 = ko[1]["result"]["loser"]
    w2 = ko[2]["result"]["winner"]
    ok({ko[3]["team1"], ko[3]["team2"]} == {w2, l1}, "match 3 = W2 vs L1")
    w3 = ko[3]["result"]["winner"]
    ok({ko[4]["team1"], ko[4]["team2"]} == {w3, w1}, "final = W3 vs W1 (Q1 winner waits)")
    ok(ko[4].get("final") and t["custom_champion"] == ko[4]["result"]["winner"], "ladder crowns the champion")
    ok(ko[3]["team1_src"].startswith(("Winner", "Loser")), "TBD source labels stored")


# 6. Multi-stage (T20WC-like) with seeded regroup
def test_multi_stage(rng):
    print("\n[6] 4 groups -> Super-stage (seeded regroup) -> semis")
    cfg = {"stages": [
        {"groups": 4, "teams_per_group": 4, "legs": 1, "qualify": 2, "assignment": "manual"},
        {"name": "Super Stage", "groups": 2, "legs": 1, "qualify": 2, "regroup": "seeded", "carry_points": False},
    ], "playoff": {"mode": "bracket", "matches": default_knockout_bracket(4)}}
    t = build_tourney(cfg, 16)
    ok(custom_generate_first_stage(t) is None, "stage 1 generates (24 matches)")
    stage1 = list(t["schedule"])
    play_out(t, rng)
    s2 = [m for m in t["schedule"] if m["stage"] == "stage2"]
    ok(len(s2) == 12, "Super Stage = 2 groups x C(4,2) = 12 matches")
    ok(min(m["round"] for m in s2) > max(m["round"] for m in stage1),
       "stage-2 rounds continue after stage-1 rounds")
    # seeded regroup: the 8 qualifiers are 4 old-group pairs - a clean split has
    # no two old group-mates in the same new group
    old_group = {}
    for tm in t["teams"]:
        old_group[tm["name"]] = tm["group"]
    for g, names in t["custom_groups"]["stage2"].items():
        origins = [old_group[n] for n in names]
        ok(len(origins) == len(set(origins)), f"Super Stage {g}: no old group-mates together")
    ok(t["status"] == "completed" and t.get("custom_champion"), "multi-stage season completes")
    pages = build_custom_status_pages(t)
    ok(any(p[0].startswith("Super Stage") for p in pages), "status pages show the named stage")
    ok(pages[-1][0] == "Knockouts", "knockouts page last")


# 7. Points carry-over
def test_carry_points(rng):
    print("\n[7] Carry-over — stage-2 table includes stage-1 points")
    cfg = {"stages": [
        {"groups": 2, "teams_per_group": 3, "legs": 1, "qualify": 2, "assignment": "manual"},
        {"groups": 1, "legs": 1, "qualify": 2, "regroup": "seeded", "carry_points": True},
    ], "playoff": {"mode": "bracket", "matches": default_knockout_bracket(2)}}
    t = build_tourney(cfg, 6)
    ok(custom_generate_first_stage(t) is None, "stage 1 generates")
    play_out(t, rng)
    carried = t.get("custom_carry", {}).get("stage2", {})
    ok(len(carried) == 4, "carry rows stored for all 4 qualifiers")
    rows = dict(custom_stage_standings(t, 1, "A"))
    raw = {m["team1"] for m in t["schedule"] if m["stage"] == "stage2"} | \
          {m["team2"] for m in t["schedule"] if m["stage"] == "stage2"}
    for name in raw:
        s2_played = sum(1 for m in t["schedule"] if m["stage"] == "stage2"
                        and name in (m["team1"], m["team2"]))
        ok(rows[name]["P"] == s2_played + carried[name]["P"],
           f"{name}: games played includes the carried {carried[name]['P']}")
    ok(t["status"] == "completed", "carry season completes")


# 8. 16-team default knockout
def test_ko16(rng):
    print("\n[8] 16 qualifiers -> Round of 16 -> QF -> SF -> Final")
    cfg = {"stages": [{"groups": 4, "teams_per_group": 5, "legs": 1, "qualify": 4, "assignment": "random"}],
           "playoff": {"mode": "bracket", "matches": default_knockout_bracket(16)}}
    t = build_tourney(cfg, 20)
    ok(custom_start_error(t) is None, "random-assignment start validation passes")
    ok(custom_generate_first_stage(t) is None, "stage 1 generates")
    ok(all(tm.get("group") for tm in t["teams"]), "random draw stamps a letter on every team")
    play_out(t, rng)
    ko = [m for m in t["schedule"] if m["stage"] == "knockout"]
    ok(len(ko) == 15, "15 knockout matches (8+4+2+1)")
    ok(t["status"] == "completed" and t.get("custom_champion"), "16-seed bracket completes")


# 9. Reverts
def test_reverts(rng):
    print("\n[9] Reverts — knockout and cross-stage")
    cfg = {"stages": [
        {"groups": 2, "teams_per_group": 3, "legs": 1, "qualify": 2, "assignment": "manual"},
        {"groups": 1, "legs": 1, "qualify": 2, "regroup": "seeded", "carry_points": False},
    ], "playoff": {"mode": "bracket", "matches": default_knockout_bracket(2)}}
    t = build_tourney(cfg, 6)
    custom_generate_first_stage(t)
    play_out(t, rng)
    ok(t["status"] == "completed", "season completed before reverts")

    # 9a. revert the final: champion cleared, replay re-crowns
    fin = next(m for m in t["schedule"] if m.get("stage") == "knockout" and m.get("final"))
    ok_, msg = revert_tournament_match(t, fin["match_id"])
    ok(ok_, "final can be reverted")
    ok("custom_champion" not in t and t["status"] == "active", "champion cleared, season reopened")
    play_out(t, rng)
    ok(t["status"] == "completed" and t.get("custom_champion"), "replayed final re-crowns")

    # 9b. a stage-1 match can't be reverted over completed stage-2 matches
    s1_done = next(m for m in t["schedule"] if m["stage"] == "group")
    ok_, msg = revert_tournament_match(t, s1_done["match_id"])
    ok(not ok_, "stage-1 revert blocked while stage-2/KO results stand")

    # 9c. fresh season: stage 2 generated but unplayed -> stage-1 revert drops it
    t2 = build_tourney(cfg, 6)
    custom_generate_first_stage(t2)
    rng2 = random.Random(7)
    for m in [x for x in t2["schedule"] if x["stage"] == "group"]:
        fake_result(m, rng2)
        custom_try_advance(t2)
    ok(any(m["stage"] == "stage2" for m in t2["schedule"]), "stage 2 generated")
    s1 = next(m for m in t2["schedule"] if m["stage"] == "group")
    ok_, msg = revert_tournament_match(t2, s1["match_id"])
    ok(ok_, "stage-1 revert allowed while stage 2 is unplayed")
    ok(not any(m["stage"] == "stage2" for m in t2["schedule"]), "pending stage-2 fixtures dropped")
    ok("stage2" not in t2.get("custom_groups", {}), "stage-2 groups cleared for a fresh redraw")
    fake_result(s1, rng2)
    custom_try_advance(t2)
    ok(any(m["stage"] == "stage2" for m in t2["schedule"]), "stage 2 regenerates after the replay")
    ok(_match_bracket_rank(t2, s1) == 0, "league stage-1 rank stays 0")


def main():
    rng = random.Random(42)
    test_bracket_language()
    test_config_validation()
    test_league_only(rng)
    test_groups_knockout(rng)
    test_custom_ladder(rng)
    test_multi_stage(rng)
    test_carry_points(rng)
    test_ko16(rng)
    test_reverts(rng)
    print(f"\nALL {PASS} CHECKS PASSED ✅")


if __name__ == "__main__":
    main()
