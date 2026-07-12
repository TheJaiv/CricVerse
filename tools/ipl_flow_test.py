# IPL end-to-end flow test (headless, no Discord / no MongoDB writes needed).
# Run from the repo root:  python tools/ipl_flow_test.py
#
# Covers: the real-IPL fixture rule (10 teams, 14 matches each — 5 opponents twice
# and 4 once), the 14-round layout, home/away balance, the single combined table
# (no groups), and the Top-4 playoff ladder (Q1/Eliminator → Q2 → Final → champion).

import os
import sys
import random
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tournament_manager import (
    generate_ipl_schedule, ipl_try_advance, get_tournament_standings,
    _match_bracket_rank, revert_tournament_match, IPL_PLAYOFF_ORDER,
)

PASS = 0
def ok(cond, label):
    global PASS
    assert cond, f"FAIL: {label}"
    PASS += 1
    print(f"  ✓ {label}")


TEAMS = ["CSK", "MI", "KKR", "SRH", "RR", "GT", "RCB", "DC", "PBKS", "LSG"]


def build_tourney(teams=TEAMS):
    return {
        "server_id": "999", "name": "Test IPL", "managers": ["1"],
        "teams": [{"name": n, "owner_id": str(100 + i), "squad": []} for i, n in enumerate(teams)],
        "status": "active", "schedule": [], "current_match_idx": 0, "stats": {},
        "format_overs": 20, "tournament_type": "ipl",
    }


def fake_result(m, rng):
    """Give a match a plausible completed result so standings/NRR have something to chew on."""
    t1r, t2r = rng.randint(120, 220), rng.randint(120, 220)
    if t1r == t2r:
        t1r += 1
    winner = m["team1"] if t1r > t2r else m["team2"]
    loser = m["team2"] if winner == m["team1"] else m["team1"]
    m["status"] = "completed"
    m["result"] = {
        "winner": winner, "loser": loser, "format_overs": 20,
        "t1_runs": t1r, "t1_wickets": rng.randint(2, 10), "t1_balls": 120,
        "t2_runs": t2r, "t2_wickets": rng.randint(2, 10), "t2_balls": 120,
    }
    return winner


def play_all(tourney, matches, rng):
    for m in matches:
        fake_result(m, rng)
        ipl_try_advance(tourney)


# ── 1. Fixture shape ─────────────────────────────────────────────────────────
def test_fixture_shape(rng):
    print("\n[1] Fixture shape — the real IPL rule")
    sched = generate_ipl_schedule(TEAMS)

    ok(len(sched) == 70, "70 league matches")
    ok(all(m["stage"] == "group" for m in sched), "every league match is stage=group")
    ok(not any("group" in m for m in sched), "no `group` field on any match — the IPL has no groups")
    ok([m["match_id"] for m in sched] == list(range(1, 71)), "match_ids are 1..70, contiguous")

    played = Counter()
    home, away = Counter(), Counter()
    meetings = Counter()
    for m in sched:
        t1, t2 = m["team1"], m["team2"]
        played[t1] += 1; played[t2] += 1
        home[t1] += 1; away[t2] += 1
        meetings[frozenset((t1, t2))] += 1
        assert t1 != t2, "a team cannot play itself"

    ok(all(played[t] == 14 for t in TEAMS), "every team plays exactly 14 matches")
    ok(all(home[t] == 7 and away[t] == 7 for t in TEAMS), "every team has 7 home & 7 away")

    # Each team: exactly 5 opponents twice, 4 opponents once.
    for t in TEAMS:
        counts = Counter()
        for pair, n in meetings.items():
            if t in pair:
                other = next(x for x in pair if x != t)
                counts[other] = n
        twice = [o for o, n in counts.items() if n == 2]
        once = [o for o, n in counts.items() if n == 1]
        assert len(counts) == 9, f"{t} should face all 9 others, faced {len(counts)}"
        assert len(twice) == 5, f"{t} plays {len(twice)} teams twice (want 5)"
        assert len(once) == 4, f"{t} plays {len(once)} teams once (want 4)"
    ok(True, "every team meets 5 opponents twice and 4 opponents once")

    # The two top seeds (add order 1 & 2 — the CSK/MI slot) are a mirror pair → meet twice.
    ok(meetings[frozenset((TEAMS[0], TEAMS[1]))] == 2,
       f"top two seeds ({TEAMS[0]} & {TEAMS[1]}) meet twice")

    # Home/away split of the twice-met pairs: one each way.
    for pair, n in meetings.items():
        if n != 2:
            continue
        a, b = tuple(pair)
        hosts = [m["team1"] for m in sched if {m["team1"], m["team2"]} == set(pair)]
        assert sorted(hosts) == sorted([a, b]), f"{a} v {b} twice, but hosts were {hosts}"
    ok(True, "pairs that meet twice play one leg each at home")


# ── 2. Round layout ──────────────────────────────────────────────────────────
def test_rounds(rng):
    print("\n[2] Round layout")
    sched = generate_ipl_schedule(TEAMS)
    by_round = defaultdict(list)
    for m in sched:
        by_round[m["round"]].append(m)

    ok(sorted(by_round) == list(range(1, 15)), "14 rounds, numbered 1..14")
    ok(all(len(v) == 5 for v in by_round.values()), "every round has exactly 5 matches")

    for r, matches in by_round.items():
        seen = [t for m in matches for t in (m["team1"], m["team2"])]
        assert len(seen) == len(set(seen)) == 10, f"round {r} does not feature each team exactly once"
    ok(True, "every team plays exactly once per round")

    ok(all(isinstance(m["round"], int) for m in sched),
       "league rounds are ints (so they count toward the points table)")
    ok(all(_match_bracket_rank(build_tourney(), m) == 0 for m in sched),
       "league matches rank 0 in the bracket order")


# ── 3. Single combined table ─────────────────────────────────────────────────
def test_standings(rng):
    print("\n[3] Points table")
    t = build_tourney()
    t["schedule"] = generate_ipl_schedule(TEAMS)
    play_all(t, [m for m in t["schedule"] if m["stage"] == "group"], rng)

    table = get_tournament_standings(t)
    ok(len(table) == 10, "one combined 10-team table (not two group tables)")
    ok(all(d["P"] == 14 for _, d in table), "every team shows 14 played")
    ok(sum(d["W"] for _, d in table) == 70, "wins across the table sum to 70")
    ok(all(d["Pts"] == 2 * d["W"] + d["T"] for _, d in table), "points = 2·W + 1·T")

    pts = [d["Pts"] for _, d in table]
    ok(pts == sorted(pts, reverse=True), "table is sorted by points")
    return t, table


# ── 4. Playoffs ──────────────────────────────────────────────────────────────
def test_playoffs(rng):
    print("\n[4] Top-4 playoffs")
    t = build_tourney()
    t["schedule"] = generate_ipl_schedule(TEAMS)

    league = [m for m in t["schedule"] if m["stage"] == "group"]

    # No playoff match may appear while a single league game is outstanding.
    for m in league[:-1]:
        fake_result(m, rng)
        ipl_try_advance(t)
    ok(not any(m["stage"] == "knockout" for m in t["schedule"]),
       "no playoff generated while a league match is still pending")

    fake_result(league[-1], rng)
    ipl_try_advance(t)

    top4 = [n for n, _ in get_tournament_standings(t)][:4]
    q1 = next(m for m in t["schedule"] if m["round"] == "Qualifier 1")
    elim = next(m for m in t["schedule"] if m["round"] == "Eliminator")
    ok({q1["team1"], q1["team2"]} == {top4[0], top4[1]}, "Qualifier 1 is 1st v 2nd")
    ok({elim["team1"], elim["team2"]} == {top4[2], top4[3]}, "Eliminator is 3rd v 4th")
    ok(not any(m["round"] in ("Qualifier 2", "Final") for m in t["schedule"]),
       "Qualifier 2 / Final not generated until their feeders finish")

    # Q1 + Eliminator → Qualifier 2
    wq1 = fake_result(q1, rng)
    lq1 = q1["result"]["loser"]
    ipl_try_advance(t)
    ok(not any(m["round"] == "Qualifier 2" for m in t["schedule"]),
       "Qualifier 2 waits for the Eliminator")
    welim = fake_result(elim, rng)
    ipl_try_advance(t)

    q2 = next(m for m in t["schedule"] if m["round"] == "Qualifier 2")
    ok({q2["team1"], q2["team2"]} == {lq1, welim},
       "Qualifier 2 is the Qualifier 1 loser v the Eliminator winner")
    ok(t["status"] != "completed", "season still active before the Final")

    # Q2 → Final
    wq2 = fake_result(q2, rng)
    ipl_try_advance(t)
    final = next(m for m in t["schedule"] if m["round"] == "Final")
    ok({final["team1"], final["team2"]} == {wq1, wq2},
       "Final is the Qualifier 1 winner v the Qualifier 2 winner")
    ok(elim["result"]["loser"] not in (final["team1"], final["team2"]),
       "the Eliminator loser is knocked out")

    champ = fake_result(final, rng)
    ipl_try_advance(t)
    ok(t["status"] == "completed", f"season completes when the Final is played (champion: {champ})")

    ok(len([m for m in t["schedule"] if m["stage"] == "knockout"]) == 4,
       "exactly 4 playoff matches (Q1, Eliminator, Q2, Final)")
    ok(len(t["schedule"]) == 74, "74 matches in total, like the real IPL")

    ranks = [_match_bracket_rank(t, next(m for m in t["schedule"] if m["round"] == r))
             for r in IPL_PLAYOFF_ORDER]
    ok(ranks == [1, 1, 2, 3], "playoff bracket ranks order Q1/Eliminator → Q2 → Final")

    ids = [m["match_id"] for m in t["schedule"]]
    ok(len(ids) == len(set(ids)), "match_ids stay unique once playoffs are appended")
    return t


# ── 5. Revert ────────────────────────────────────────────────────────────────
def test_revert(rng):
    print("\n[5] Revert")
    t = test_playoffs(rng)

    # The Final is the last word — reverting it reopens the season.
    final = next(m for m in t["schedule"] if m["round"] == "Final")
    good, _ = revert_tournament_match(t, final["match_id"])
    ok(good and t["status"] == "active", "reverting the Final reopens the season")

    # A league match can't be pulled out from under a completed playoff.
    league_m = next(m for m in t["schedule"] if m["stage"] == "group")
    good, msg = revert_tournament_match(t, league_m["match_id"])
    ok(not good, "a league match is protected while playoffs built on it are completed")

    # Unwind the bracket, then revert a league game: the stale bracket must be dropped.
    for rnd in ("Qualifier 2", "Eliminator", "Qualifier 1"):
        m = next(x for x in t["schedule"] if x["round"] == rnd)
        good, msg = revert_tournament_match(t, m["match_id"])
        assert good, f"could not revert {rnd}: {msg}"
    good, msg = revert_tournament_match(t, league_m["match_id"])
    ok(good, "a league match reverts once the bracket above it is unwound")
    ok(not any(m["stage"] == "knockout" for m in t["schedule"]),
       "the stale playoff bracket is torn down with it")

    # Replaying it rebuilds the bracket from the fresh seeding.
    fake_result(league_m, rng)
    ipl_try_advance(t)
    ok(any(m["round"] == "Qualifier 1" for m in t["schedule"]),
       "replaying the league match regenerates the playoffs")
    top4 = [n for n, _ in get_tournament_standings(t)][:4]
    q1 = next(m for m in t["schedule"] if m["round"] == "Qualifier 1")
    ok({q1["team1"], q1["team2"]} == {top4[0], top4[1]},
       "the regenerated Qualifier 1 uses the re-seeded top 2")


# ── 6. Fuzz ──────────────────────────────────────────────────────────────────
def test_fuzz():
    print("\n[6] Fuzz — 300 random seasons")
    for seed in range(300):
        rng = random.Random(seed)
        teams = list(TEAMS)
        rng.shuffle(teams)
        sched = generate_ipl_schedule(teams)

        assert len(sched) == 70, f"seed {seed}: {len(sched)} matches"
        played, home, meetings = Counter(), Counter(), Counter()
        by_round = defaultdict(list)
        for m in sched:
            played[m["team1"]] += 1; played[m["team2"]] += 1
            home[m["team1"]] += 1
            meetings[frozenset((m["team1"], m["team2"]))] += 1
            by_round[m["round"]].append(m)

        for t in teams:
            assert played[t] == 14, f"seed {seed}: {t} played {played[t]}"
            assert home[t] == 7, f"seed {seed}: {t} had {home[t]} home"
        for r, ms in by_round.items():
            seen = [x for m in ms for x in (m["team1"], m["team2"])]
            assert len(seen) == len(set(seen)) == 10, f"seed {seed}: round {r} malformed"
        assert sorted(Counter(meetings.values()).items()) == [(1, 20), (2, 25)], \
            f"seed {seed}: wrong meeting spread {Counter(meetings.values())}"

        # A full season still yields a champion.
        t_obj = build_tourney(teams)
        t_obj["schedule"] = sched
        for m in list(sched):
            fake_result(m, rng)
            ipl_try_advance(t_obj)
        guard = 0
        while t_obj["status"] != "completed" and guard < 10:
            for m in [x for x in t_obj["schedule"] if x["status"] == "pending"]:
                fake_result(m, rng)
                ipl_try_advance(t_obj)
            guard += 1
        assert t_obj["status"] == "completed", f"seed {seed}: season never completed"
        assert len(t_obj["schedule"]) == 74, f"seed {seed}: {len(t_obj['schedule'])} total matches"
    ok(True, "300 randomized seasons: fixtures valid and every season crowns a champion")


if __name__ == "__main__":
    rng = random.Random(7)
    print("═══ IPL flow test ═══")
    test_fixture_shape(rng)
    test_rounds(rng)
    test_standings(rng)
    test_playoffs(rng)
    test_revert(rng)
    test_fuzz()
    print(f"\n═══ {PASS} checks passed ═══")
