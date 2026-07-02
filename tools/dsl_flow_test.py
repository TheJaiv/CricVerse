# DSL end-to-end flow test (headless, no Discord / no MongoDB writes needed).
# Run from the repo root:  python tools/dsl_flow_test.py
#
# Covers: schedule shape (double RR, home/away balance, venues), venue-profile
# pitch draws, playoff generation + slot advancement + champion, season archive
# round-trip, and the cross-season player/venue aggregators.

import os
import sys
import json
import random
import tempfile
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import dsl_manager
from dsl_manager import (
    DSL_CONFIG, DSL_KO_STAGES, create_dsl_tournament, set_home_stadium,
    assign_dsl_stadiums, pick_dsl_conditions, dsl_generate_league_schedule,
    dsl_generate_playoffs, _dsl_try_advance, _dsl_get,
    write_season_archive, load_all_seasons, invalidate_archive_cache,
    aggregate_player_stats, aggregate_venue_stats, season_history,
)
from tournament_manager import get_tournament_standings, assign_tournament_conditions

PASS = 0
def ok(cond, label):
    global PASS
    assert cond, f"FAIL: {label}"
    PASS += 1
    print(f"  ✓ {label}")


def build_tourney():
    t = create_dsl_tournament("999", "1")
    venues = list(DSL_CONFIG["venues"])
    for i in range(DSL_CONFIG["team_count"]):
        t["teams"].append({"name": f"Team {chr(65 + i)}", "owner_id": str(100 + i), "squad": []})
        okk, msg = set_home_stadium(t, f"Team {chr(65 + i)}", venues[i])
        assert okk, msg
    return t


def fake_result(m, winner_first_bias=0.5):
    """Complete a match with a synthetic result in the real result-dict shape."""
    t1, t2 = m["team1"], m["team2"]
    r1, r2 = random.randint(120, 200), random.randint(120, 200)
    while r1 == r2:
        r2 = random.randint(120, 200)
    batted_first = t1 if random.random() < winner_first_bias else t2
    winner = t1 if r1 > r2 else t2
    m["status"] = "completed"
    m["result"] = {
        "winner": winner, "loser": t2 if winner == t1 else t1, "format_overs": 20,
        "t1_runs": r1, "t1_wickets": random.randint(3, 9), "t1_balls": 120,
        "t2_runs": r2, "t2_wickets": random.randint(3, 9), "t2_balls": 120,
        "batted_first": batted_first, "stadium": m.get("stadium"),
        "pitch": m.get("pitch"), "weather": m.get("weather"),
    }


def main():
    random.seed(7)
    n = DSL_CONFIG["team_count"]

    print("— schedule —")
    t = build_tourney()
    t["schedule"] = dsl_generate_league_schedule(t)
    assign_tournament_conditions(t)   # venues (home grounds) + venue-profile pitches

    legs = 2 if DSL_CONFIG["double_round_robin"] else 1
    expect_total = n * (n - 1) // 2 * legs
    ok(len(t["schedule"]) == expect_total, f"{expect_total} league matches generated")

    home = Counter(m["team1"] for m in t["schedule"])
    away = Counter(m["team2"] for m in t["schedule"])
    per_leg = n - 1
    ok(all(home[tm["name"]] == per_leg * legs // 2 for tm in t["teams"]),
       f"every team has exactly {per_leg * legs // 2} home games")
    ok(all(away[tm["name"]] == per_leg * legs // 2 for tm in t["teams"]),
       f"every team has exactly {per_leg * legs // 2} away games")

    if legs == 2:
        pairs = Counter((m["team1"], m["team2"]) for m in t["schedule"])
        ok(all(c == 1 for c in pairs.values()), "each pair meets exactly once per ground (home & away)")

    homes = {tm["name"]: tm["home_stadium"] for tm in t["teams"]}
    ok(all(m["stadium"] == homes[m["team1"]] for m in t["schedule"]),
       "every league match is at the home (team1) team's ground")
    ok(all(m.get("pitch") and m.get("weather") for m in t["schedule"]),
       "every match has pitch + weather assigned")
    for m in t["schedule"]:
        profile = DSL_CONFIG["venues"][m["stadium"]]
        assert m["pitch"] in profile, f"pitch {m['pitch']} not in {m['stadium']} profile"
    ok(True, "every assigned pitch comes from its venue's profile")

    print("— pitch profile draw —")
    sample_venue = next(iter(DSL_CONFIG["venues"]))
    draws = Counter(pick_dsl_conditions(sample_venue, False)[0] for _ in range(2000))
    ok(set(draws) == set(DSL_CONFIG["venues"][sample_venue]),
       f"{sample_venue} 2000-draw sample uses exactly its profile pitches ({dict(draws)})")

    print("— league → playoffs —")
    ko_early, msg = dsl_generate_playoffs(t)
    ok(not ko_early and "pending" in msg, "playoff generation refused while league pending")
    for m in t["schedule"]:
        fake_result(m)
    okk, msg = dsl_generate_playoffs(t)
    ok(okk, f"playoffs generated ({msg})")
    seeds = t["playoff_seeds"]
    standings = [nm for nm, _ in get_tournament_standings(t)]
    ok(seeds == standings[:4], "playoff seeds match the league top 4")
    sf1, sf2 = _dsl_get(t, "Semi-Final 1"), _dsl_get(t, "Semi-Final 2")
    fi = _dsl_get(t, "Final")
    ok(sf1["team1"] == seeds[0] and sf1["team2"] == seeds[3], "SF1 = 1st vs 4th")
    ok(sf2["team1"] == seeds[1] and sf2["team2"] == seeds[2], "SF2 = 2nd vs 3rd")
    ok(fi["status"] == "locked", "Final starts locked")
    ok(all(m.get("stadium") and m.get("pitch") for m in (sf1, sf2, fi)), "playoff matches got venue + pitch")

    dup, msg = dsl_generate_playoffs(t)
    ok(not dup, "second playoff generation refused (idempotent)")

    print("— bracket advancement —")
    fake_result(sf1); _dsl_try_advance(t)
    ok(fi["team1"] == sf1["result"]["winner"] and fi["status"] == "locked",
       "SF1 winner → Final slot 1 (still awaiting SF2)")
    fake_result(sf2); _dsl_try_advance(t)
    ok(fi["team2"] == sf2["result"]["winner"] and fi["status"] == "pending", "SF2 winner → Final, now ready")
    assign_tournament_conditions(t)
    fake_result(fi); _dsl_try_advance(t)
    ok(t.get("dsl_champion") == fi["result"]["winner"], "champion crowned from the Final")
    ok(t.get("status") == "completed", "season status flips to completed")

    print("— archive round-trip + aggregation —")
    # Fake per-player season stats for two teams so the aggregator has data.
    t["stats"] = {
        "Team A": {"Rohit": {"matches": 22, "runs": 700, "balls_faced": 480, "outs": 18,
                             "fours": 60, "sixes": 30, "fifties": 5, "hundreds": 1,
                             "wickets": 0, "runs_conceded": 0, "balls_bowled": 0}},
        "Team B": {"Bumrah": {"matches": 22, "runs": 40, "balls_faced": 50, "outs": 8,
                              "fours": 3, "sixes": 1, "fifties": 0, "hundreds": 0,
                              "wickets": 30, "runs_conceded": 500, "balls_bowled": 480}},
    }
    with tempfile.TemporaryDirectory() as tmp:
        dsl_manager.ARCHIVE_DIR = tmp
        invalidate_archive_cache()
        path, blob = write_season_archive(t)
        ok(os.path.exists(path), f"archive written ({os.path.basename(path)}, {len(blob)} bytes)")
        data = json.loads(blob)
        ok(data["season"] == t["season"] and data["champion"] == t["dsl_champion"],
           "archive carries season + champion")
        ok(len(data["matches"]) == len(t["schedule"]), "archive carries every completed match record")
        ok(all(r["stadium"] and r["batted_first"] for r in data["matches"]),
           "archive match records carry stadium + batted_first")

        seasons = load_all_seasons("999")
        ok(len(seasons) == 1 and seasons[0]["season"] == t["season"], "archive loads back from disk")

        # Overall = archive (S1) + a fresh in-progress S2 with more runs for Rohit.
        t2 = create_dsl_tournament("999", "1")
        ok(t2["season"] == t["season"] + 1, "next season number = archived season + 1")
        t2["stats"] = {"Team A": {"Rohit": {"matches": 3, "runs": 150, "balls_faced": 90, "outs": 2,
                                            "fours": 12, "sixes": 8, "fifties": 2, "hundreds": 0,
                                            "wickets": 0, "runs_conceded": 0, "balls_bowled": 0}}}
        agg = aggregate_player_stats("999", t2)
        ok(agg["rohit"]["runs"] == 850 and agg["rohit"]["matches"] == 25 and agg["rohit"]["seasons"] == 2,
           "player aggregation merges archive + current season")

        vstats = aggregate_venue_stats("999", t2)
        ok(len(vstats) == len(DSL_CONFIG["venues"]), "every venue hosted matches and has stats")
        wank = vstats.get(homes["Team A"])
        ok(wank and wank["matches"] > 0 and wank["avg_1st"] > 0 and wank["bat_first_win_pct"] is not None,
           "venue stats compute matches / avg innings / bat-first win %")

        hist = season_history("999", t2)
        ok(hist[0][2] == t["dsl_champion"] and hist[-1][2] is None,
           "season history shows S1 champion + S2 in progress")
        invalidate_archive_cache()

    print(f"\nALL {PASS} CHECKS PASSED ✅")


if __name__ == "__main__":
    main()
