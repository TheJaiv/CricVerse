# Ascension League headless tests — Elo math, weak-team credit economy, boost
# caps, playoff flow. Run from repo root:  python tools/rating_league_test.py
import os, sys, random
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import rating_league as R

PASS = 0
def ok(c, label):
    global PASS
    assert c, "FAIL: " + label
    PASS += 1
    print("  ✓ " + label)


def mk_tourney(n_teams=6):
    return {"server_id": "1", "name": "ASL", "tournament_type": "rating", "format_overs": 20,
            "status": "active", "teams": [{"name": f"T{i}", "owner_id": str(i), "squad": []} for i in range(n_teams)],
            "schedule": [], "stats": {}}


def result(t1, t2, winner, t1r=180, t1w=6, t2r=170, t2w=8, bf=None, delta=None):
    return {"winner": winner, "batted_first": bf or t1,
            "t1_runs": t1r, "t1_wickets": t1w, "t2_runs": t2r, "t2_wickets": t2w,
            "stats_delta": delta}


def play(tourney, t1, t2, winner, **kw):
    m = {"match_id": len(tourney["schedule"]) + 1, "round": "Ladder", "stage": "ladder",
         "team1": t1, "team2": t2, "status": "completed",
         "result": result(t1, t2, winner, **kw)}
    tourney["schedule"].append(m)
    R.apply_match_rating(tourney, m)
    R.award_match_credits(tourney, m)
    return m


def main():
    random.seed(1)

    print("— Elo: volume independence —")
    t = mk_tourney(2)
    # A plays 20 games at 70% vs B (equal start). B mirror.
    # Instead: two identical-strength opponents; team that wins MORE % ends higher.
    t = mk_tourney(3)
    # T0 = 70% winner over 20 games; T1 = 55% winner over 40 games; both vs T2 (equal).
    for i in range(20):
        play(t, "T0", "T2", "T0" if i < 14 else "T2")   # 70%
    for i in range(40):
        play(t, "T1", "T2", "T1" if i < 22 else "T2")   # 55%
    r = dict((n, d["rating"]) for n, d in R.rating_standings(t))
    ok(r["T0"] > r["T1"], f"20-game/70% ({r['T0']}) out-ranks 40-game/55% ({r['T1']}) — volume can't buy rank")

    print("— Elo: farming a weak team gains ≈0 —")
    t = mk_tourney(2)
    t["teams"][0]["rating"] = 1400; t["teams"][0]["games"] = 20   # strong, past provisional
    t["teams"][1]["rating"] = 700;  t["teams"][1]["games"] = 20
    before = R.team_rating(t["teams"][0])
    play(t, "T0", "T1", "T0", t1r=300, t1w=2, t2r=120)   # thrash the weakling
    gain = R.team_rating(t["teams"][0]) - before
    ok(abs(gain) < 3.0, f"beating a 700-rated team as a 1400 gains ~0 ({gain:+.1f})")

    print("— Elo: margin + opponent weighting —")
    t = mk_tourney(2)
    for x in t["teams"]: x["games"] = 20
    before = R.team_rating(t["teams"][0])
    play(t, "T0", "T1", "T0", t1r=300, t1w=0, t2r=150)   # 150-run thrash, equal ratings
    big = R.team_rating(t["teams"][0]) - before
    t = mk_tourney(2)
    for x in t["teams"]: x["games"] = 20
    before = R.team_rating(t["teams"][0])
    play(t, "T0", "T1", "T0", t1r=181, t1w=8, t2r=180)   # 1-run squeaker
    small = R.team_rating(t["teams"][0]) - before
    ok(big > small * 1.4, f"thrashing gains more than a squeaker ({big:+.1f} vs {small:+.1f})")

    print("— Elo: deltas ≈ zero-sum + revert —")
    t = mk_tourney(2)
    for x in t["teams"]: x["games"] = 20
    m = play(t, "T0", "T1", "T0")
    d = m["result"]["rating_delta"]
    ok(abs(d["T0"] + d["T1"]) < 0.05, f"winner +{d['T0']} ≈ −loser {d['T1']} (zero-sum)")
    R.revert_match_rating(t, m)
    ok(abs(R.team_rating(t["teams"][0]) - 1000) < 0.01 and t["teams"][0]["games"] == 20,
       "revert restores rating + games")

    print("— credits: weak teams earn more, strong teams less —")
    t = mk_tourney(2)
    t["teams"][0]["rating"] = 1400; t["teams"][0]["games"] = 20   # strong
    t["teams"][1]["rating"] = 760;  t["teams"][1]["games"] = 20   # weak
    # weak team beats strong team (underdog win) vs strong team beats weak
    t2 = mk_tourney(2)
    t2["teams"][0]["rating"] = 1400; t2["teams"][0]["games"] = 20
    t2["teams"][1]["rating"] = 760;  t2["teams"][1]["games"] = 20
    play(t,  "T1", "T0", "T1")   # weak (T1) beats strong (T0) — underdog
    play(t2, "T0", "T1", "T0")   # strong beats weak
    weak_earn = t["teams"][1]["credits"]
    strong_earn = t2["teams"][0]["credits"]
    ok(weak_earn > strong_earn * 2, f"underdog weak win earns {weak_earn} vs strong routine win {strong_earn}")
    ok(all(mm["result"]["credits_awarded"] for mm in t["schedule"]), "credits idempotency flag set")

    print("— credits: underdog farming capped per opponent —")
    t = mk_tourney(2)
    t["teams"][0]["rating"] = 1400; t["teams"][0]["games"] = 20
    t["teams"][1]["rating"] = 760;  t["teams"][1]["games"] = 20
    for _ in range(15):
        play(t, "T1", "T0", "T1")   # weak beats strong 15 times
    farmed = t["teams"][1].get("underdog_earned", {}).get("T0", 0)
    ok(farmed <= R.CREDITS_CONFIG["underdog_pair_cap"] + 0.5,
       f"underdog bonus vs one opponent capped at {R.CREDITS_CONFIG['underdog_pair_cap']} (got {farmed:.0f})")

    print("— boosts: hard caps + 95 ceiling —")
    t = mk_tourney(2)
    team = t["teams"][0]
    team["credits"] = 10_000
    team["squad"] = [{"name": "Star", "bat": 80, "bowl": 40, "role": "Batsman"},
                     {"name": "Ace", "bat": 50, "bowl": 95, "role": "Pace_Fast Bowler"}]
    for i in range(R.BOOST_MAX_PER_PLAYER):
        okb, _ = R.apply_boost(t, team, "Star", "bat"); assert okb, f"boost {i} should work"
    okb, msg = R.apply_boost(t, team, "Star", "bat")
    ok(not okb and "maxed" in msg, "per-player cap (+3) enforced")
    ok(team["squad"][0]["tboost_bat"] == R.BOOST_MAX_PER_PLAYER, "3 boosts recorded")
    okb, msg = R.apply_boost(t, team, "Ace", "bowl")   # 95 already → +1 would be 96
    ok(not okb and "maximum" in msg, "95 ceiling enforced (95→96 blocked)")
    # per-team cap: fresh team, keep boosting distinct players until team cap hits
    t2 = mk_tourney(1); tm = t2["teams"][0]; tm["credits"] = 100_000
    tm["squad"] = [{"name": f"P{i}", "bat": 60, "bowl": 40} for i in range(8)]
    applied = 0
    for i in range(8):
        for _ in range(R.BOOST_MAX_PER_PLAYER):
            okb, _ = R.apply_boost(t2, tm, f"P{i}", "bat")
            if okb: applied += 1
    ok(applied == R.BOOST_MAX_PER_TEAM, f"per-team cap enforced (+{R.BOOST_MAX_PER_TEAM} total, applied {applied})")

    print("— boosts survive server overrides via apply_tournament_boosts —")
    src = [{"name": "Star", "bat": 83, "bowl": 40, "tboost_bat": 3}]   # server-override already applied bat=83
    roster = R.apply_tournament_boosts(src)
    ok(roster[0]["bat"] == 86, f"boost folds onto post-override bat (83+3=86, got {roster[0]['bat']})")
    ok(src[0]["bat"] == 83, "source squad dict NOT mutated (no compounding across matches)")
    roster = R.apply_tournament_boosts([{"name": "Cap", "bat": 94, "bowl": 40, "tboost_bat": 3}])
    ok(roster[0]["bat"] == 95, "boost respects 95 cap when applied")

    print("— playoffs: top-4 eligible → SFs → Final → champion —")
    t = mk_tourney(6)
    minq = R.RATING_CONFIG["min_games_qualify"]
    # give 4 teams enough games + spread ratings; 2 teams under min
    for i, tm in enumerate(t["teams"]):
        tm["rating"] = 1200 - i * 40
        tm["games"] = minq + 2 if i < 4 else 3
    okg, msg = R.generate_rating_playoffs(t)
    ok(okg, f"playoffs generated ({msg})")
    seeds = t["playoff_seeds"]
    ok(seeds == ["T0", "T1", "T2", "T3"], f"top-4 ELIGIBLE seeded (got {seeds})")
    sf1 = R._rating_get(t, "Semi-Final 1"); sf2 = R._rating_get(t, "Semi-Final 2")
    ok(sf1["team1"] == "T0" and sf1["team2"] == "T3", "SF1 = 1v4")
    ok(sf2["team1"] == "T1" and sf2["team2"] == "T2", "SF2 = 2v3")
    for m in (sf1, sf2):
        m["status"] = "completed"; m["result"] = {"winner": m["team1"], "batted_first": m["team1"],
            "t1_runs": 180, "t1_wickets": 5, "t2_runs": 160, "t2_wickets": 8, "stats_delta": None}
    R._rating_try_advance(t)
    fi = R._rating_get(t, "Final")
    ok(fi["team1"] == "T0" and fi["team2"] == "T1" and fi["status"] == "pending", "Final = SF winners, ready")
    fi["status"] = "completed"; fi["result"] = {"winner": "T0", "batted_first": "T0",
        "t1_runs": 200, "t1_wickets": 4, "t2_runs": 180, "t2_wickets": 9, "stats_delta": None}
    R._rating_try_advance(t)
    ok(t.get("rating_champion") == "T0" and t["status"] == "completed", "champion crowned")

    print(f"\nALL {PASS} CHECKS PASSED ✅")


if __name__ == "__main__":
    main()
