# Dummy data-farm draft test (headless, no Discord / no Mongo / no engine).
# Run from the repo root: python tools/dummy_flow_test.py
#
# Verifies the pure drafting logic in league/dummy_run.py that bot's `cv dummyrun`
# relies on: 70% pool selection, correctly-composed balanced XIs, no player on both
# sides of a match, even game-time rotation across the pool, participation reaching
# the whole pool over enough matches, and the pitch x weather schedule covering every
# surface + sky (and all 170 combos) via the i%17 / i%10 cycle bot.py uses - for a
# single-format run and for the mixed T20+ODI / T20+ODI+Test cycles.
# Also checks the batched (fast) conditions-stats path matches the per-match (slow)
# path for both limited-overs and Test matches.

import os
import sys
import csv
import random
from collections import Counter

# Isolate the global stats: this harness drives real matches through bot.py's finalize
# path, which records into core.global_stats. Without this it appends synthetic players
# ("BcastA", "Bot 2", ...) to the real data/global_stats.json - and, since stats now
# persist to MongoDB, would push them to production on a machine with MONGO_URI set.
os.environ["CRICVERSE_STATS_LOCAL_ONLY"] = "1"

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from league.dummy_run import (
    XI_SLOTS, bucket_of, slot_rating, select_pool, bucketize, draft_match, feasibility,
)

PASS = FAIL = 0


def check(cond, label):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        print(f"  FAIL: {label}")


def load_db():
    # Mirror get_all_players(): the live DB is de-duplicated by name (sync_csv skips
    # names already seen), so collapse CSV dupes the same way.
    path = os.path.join(os.path.dirname(__file__), "..", "data", "players_master.csv")
    seen, out = set(), []
    with open(path, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            name = r["Name"].strip()
            if name.lower() in seen:
                continue
            seen.add(name.lower())
            out.append({"name": name, "bat": int(r["Bat"]), "bowl": int(r["Bowl"]),
                        "role": r["Role"].strip(), "archetype": r["Archetype"].strip()})
    return out


def xi_composition(players):
    return Counter(bucket_of(p["role"]) for p in players)


def main():
    rng = random.Random(20260725)
    db = load_db()
    print(f"DB players: {len(db)}")

    # --- pool selection ~70% ---
    pool = select_pool(db, 0.70, rng)
    check(abs(len(pool) - round(len(db) * 0.70)) <= 1, "pool is ~70% of DB")
    check(len({p["name"] for p in pool}) == len(pool), "pool has no duplicate players")
    feas = feasibility(pool)
    check(feas["ok"], f"every bucket can field two XIs (short: {feas['short']})")
    print(f"pool: {len(pool)}  buckets: {feas['buckets']}")

    # --- one drafted match: composition, balance, disjoint teams ---
    buckets = bucketize(pool)
    play_counts = {}
    a, b, sa, sb = draft_match(buckets, play_counts, rng)
    check(len(a) == 11 and len(b) == 11, "both XIs have 11 players")
    check(xi_composition(a) == Counter(XI_SLOTS), f"team A composition = template ({xi_composition(a)})")
    check(xi_composition(b) == Counter(XI_SLOTS), f"team B composition = template ({xi_composition(b)})")
    names_a = {p["name"] for p in a}
    names_b = {p["name"] for p in b}
    check(not (names_a & names_b), "no player appears on both sides")
    gap = abs(sa - sb) / max(sa, sb)
    check(gap <= 0.06, f"team strengths within 6% ({sa} vs {sb}, gap {gap:.1%})")

    # --- balance holds across many independent matches ---
    worst = 0.0
    for _ in range(400):
        pc = {}
        x, y, sx, sy = draft_match(buckets, pc, rng)
        worst = max(worst, abs(sx - sy) / max(sx, sy))
    check(worst <= 0.10, f"worst strength gap over 400 matches <= 10% ({worst:.1%})")
    print(f"worst team-strength gap over 400 matches: {worst:.1%}")

    # --- even rotation + full participation over a long run ---
    buckets2 = bucketize(pool)
    pc = {}
    for i in range(600):
        draft_match(buckets2, pc, rng)
    played = [p["name"] for p in pool]
    counts = [pc.get(n, 0) for n in played]
    featured = sum(1 for c in counts if c > 0)
    check(featured == len(pool), f"every pool player featured over 600 matches ({featured}/{len(pool)})")
    # Game time is even WITHIN each bucket (LRU rotation); it differs BETWEEN buckets
    # only because a match uses 8 batters but 2 keepers - that's composition, not bias.
    worst_bucket_spread = 0
    for cat in XI_SLOTS:
        bc = [pc.get(p["name"], 0) for p in pool if bucket_of(p["role"]) == cat]
        worst_bucket_spread = max(worst_bucket_spread, max(bc) - min(bc))
    check(worst_bucket_spread <= 2, f"within-bucket game-time spread tight (max-min = {worst_bucket_spread})")
    print(f"participation: {featured}/{len(pool)} of pool; per-player games {min(counts)}-{max(counts)}; "
          f"worst within-bucket spread {worst_bucket_spread}")

    # --- pitch x weather schedule (mirrors bot: pitch=i%17, weather=i%10) ---
    pitches = ["Flat", "Green", "Dry", "Dusty", "Hard", "Soft", "Cracked", "Damp",
               "Dead", "Worn", "Turning", "Two-Paced", "Slow", "Bouncy", "Sticky",
               "Sporting", "Balanced"]
    weathers = ["Clear", "Cloudy", "Overcast", "Humid", "Dry Heat", "Windy",
                "Light Rain", "Drizzle", "Heavy Rain", "Thunderstorm"]
    combos = {(pitches[i % len(pitches)], weathers[i % len(weathers)])
              for i in range(len(pitches) * len(weathers))}
    seen_p = {p for p, _ in combos}
    seen_w = {w for _, w in combos}
    check(seen_p == set(pitches), "all 17 pitches covered within 170 matches")
    check(seen_w == set(weathers), "all 10 weathers covered within 170 matches")
    check(len(combos) == len(pitches) * len(weathers), f"all {len(pitches)*len(weathers)} combos hit exactly once in a 170-cycle")
    # a short run still spans many surfaces/skies
    short_p = {pitches[i % len(pitches)] for i in range(20)}
    short_w = {weathers[i % len(weathers)] for i in range(20)}
    check(len(short_p) >= 15 and len(short_w) == 10, f"even 20 matches span {len(short_p)} pitches / {len(short_w)} weathers")

    # mixed runs: each format walks its OWN pitch=s%17 / weather=s%10 counter (as the
    # worker does), so every format still covers all 170 combos - a shared global index
    # would only hit every 2nd/3rd weather slot per format.
    def mixed_cells(cycle):
        seq = {f: 0 for f in cycle}
        cells = set()
        for i in range(len(cycle) * len(pitches) * len(weathers)):
            fkey = cycle[i % len(cycle)]        # bot.py's _format_for
            s = seq[fkey]
            cells.add((pitches[s % len(pitches)], weathers[s % len(weathers)], fkey))
            seq[fkey] += 1
        return cells

    full = len(pitches) * len(weathers)
    for cycle in (("t20", "odi"), ("t20", "odi", "test")):
        cells = mixed_cells(cycle)
        label = "+".join(cycle)
        for fkey in cycle:
            per = {(p, w) for p, w, f in cells if f == fkey}
            check(len(per) == full, f"{label} run covers all {fkey.upper()} combos ({len(per)}/{full})")

    # --- lopsided pool still fields teams (fallback path) ---
    bat_heavy = [p for p in db if bucket_of(p["role"]) in ("BAT", "WK")][:40]
    lb = bucketize(bat_heavy)
    la, lbb, _, _ = draft_match(lb, {}, rng)
    check(len(la) == 11 and len(lbb) == 11, "bat-heavy pool still fields two XIs via fallback")

    # --- fast (batched) PW recording == slow (per-match) PW recording ---
    test_pw_batch_equivalence(rng)
    test_pw_batch_equivalence_test_format(rng)

    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)


class _WS:   # stand-in for engine BowlerStats
    def __init__(self, balls, runs, wkts):
        self.balls_bowled, self.runs_conceded, self.wickets_taken = balls, runs, wkts


class _Inn:
    def __init__(self, runs, wkts, balls, bowlers):   # bowlers: (name, role, balls, runs, wkts)
        self.total_runs, self.wickets, self.total_balls = runs, wkts, balls
        self.bowling_team = {"players": [{"name": n, "role": r} for n, r, *_ in bowlers]}
        self.bowling_stats = {n: _WS(b, rr, w) for n, _, b, rr, w in bowlers}


class _Match:
    def __init__(self, i1, i2, pitch, weather):
        self.innings1, self.innings2 = i1, i2
        self.pitch, self.weather, self.format_overs = pitch, weather, 20


class _FakeCol:
    """In-memory Mongo: applies $inc/$max/$min/$setOnInsert like the real collection."""
    def __init__(self): self.store = {}
    def _apply(self, _id, upd):
        d = self.store.setdefault(_id, {})
        for k, v in upd.get("$setOnInsert", {}).items(): d.setdefault(k, v)
        for k, v in upd.get("$inc", {}).items(): d[k] = d.get(k, 0) + v
        for k, v in upd.get("$max", {}).items(): d[k] = max(d.get(k, v), v)
        for k, v in upd.get("$min", {}).items(): d[k] = min(d.get(k, v), v)
    def update_one(self, flt, upd, upsert=False): self._apply(flt["_id"], upd)
    def bulk_write(self, ops, ordered=True):        # only used if UpdateOne is present
        for f, u in ops: self._apply(f["_id"], u)


def _rand_match(rng, pitch, weather):
    def side():
        bowlers = ([(f"P{i}", "Bowler_Pace", rng.randint(6, 24), rng.randint(5, 45), rng.randint(0, 4)) for i in range(4)]
                   + [(f"S{i}", "Bowler_Spin_Leg", rng.randint(6, 24), rng.randint(5, 45), rng.randint(0, 3)) for i in range(2)])
        return _Inn(rng.randint(90, 230), rng.randint(2, 10), rng.randint(90, 120), bowlers)
    return _Match(side(), side(), pitch, weather)


def test_pw_batch_equivalence(rng):
    """The batched fast path must fold matches into exactly the same per-combo numbers
    as recording each match individually. Skips cleanly if the Mongo deps aren't present."""
    try:
        import pymongo
        import core.conditions_stats as cstats
    except Exception as e:
        print(f"  SKIP: PW batch-equivalence ({type(e).__name__}: {e})")
        return

    fake = {"col": _FakeCol()}
    orig_getdb = cstats._get_db
    cstats._get_db = lambda: {cstats.COLLECTION: fake["col"]}
    # Force flush_batch's documented fallback (per-combo update_one) so the check never
    # depends on pymongo UpdateOne internals; restored in finally.
    had_uo = hasattr(pymongo, "UpdateOne")
    saved_uo = getattr(pymongo, "UpdateOne", None)
    if had_uo:
        del pymongo.UpdateOne
    try:
        pitches = ["Flat", "Green", "Dusty", "Hard", "Damp"]
        weathers = ["Clear", "Cloudy", "Humid"]
        matches = [_rand_match(rng, pitches[i % len(pitches)], weathers[i % len(weathers)])
                   for i in range(300)]   # > combos, so several matches share a combo

        fake["col"] = _FakeCol()
        for m in matches:
            m._conditions_recorded = False
            cstats.record_limited_overs_match(m)
        slow = fake["col"].store

        fake["col"] = _FakeCol()
        batch = {}
        for m in matches:
            m._conditions_recorded = False
            cstats.accumulate(m, batch)
        wrote = cstats.flush_batch(batch)
        fast = fake["col"].store

        check(not batch and wrote > 0, "flush_batch clears the batch and writes")
        check(slow.keys() == fast.keys(), "fast + slow touch the same combos")
        check(all(slow[k] == fast[k] for k in slow), "fast batched aggregates == slow per-match aggregates")
        check(sum(d.get("matches", 0) for d in fast.values()) == 300, "all 300 matches folded in")
        print(f"PW batch-equivalence: {len(fast)} combos, aggregates identical to per-match path")
    finally:
        cstats._get_db = orig_getdb
        if had_uo:
            pymongo.UpdateOne = saved_uo


def _rand_test_match(rng, pitch, weather):
    """Stand-in for a finished TestMatch: innings_list instead of innings1/innings2."""
    m = _TestMatchStub(pitch, weather)
    for _ in range(rng.choice([3, 4])):          # a Test can end in 3 completed innings
        bowlers = ([(f"P{i}", "Bowler_Pace", rng.randint(60, 180), rng.randint(40, 160), rng.randint(0, 6)) for i in range(3)]
                   + [(f"S{i}", "Bowler_Spin_Off", rng.randint(60, 200), rng.randint(40, 170), rng.randint(0, 6)) for i in range(2)])
        m.innings_list.append(_Inn(rng.randint(90, 550), rng.randint(3, 10), rng.randint(300, 700), bowlers))
    return m


class _TestMatchStub:
    def __init__(self, pitch, weather):
        self.pitch, self.weather = pitch, weather
        self.innings_list = []


def test_pw_batch_equivalence_test_format(rng):
    """Same equivalence guarantee for Tests: `cv dummyrun ... test` in fast mode routes
    Tests through cstats.accumulate, which must fold them exactly as record_test_match
    would - and into the `test` bucket, never the limited-overs one."""
    try:
        import pymongo
        import core.conditions_stats as cstats
    except Exception as e:
        print(f"  SKIP: Test PW batch-equivalence ({type(e).__name__}: {e})")
        return

    fake = {"col": _FakeCol()}
    orig_getdb = cstats._get_db
    cstats._get_db = lambda: {cstats.COLLECTION: fake["col"]}
    # Force flush_batch's per-combo update_one fallback, as the limited-overs check does,
    # so _FakeCol never has to imitate pymongo's UpdateOne; restored in finally.
    had_uo = hasattr(pymongo, "UpdateOne")
    saved_uo = getattr(pymongo, "UpdateOne", None)
    if had_uo:
        del pymongo.UpdateOne
    try:
        pitches = ["Flat", "Green", "Dusty", "Hard", "Damp"]
        weathers = ["Clear", "Cloudy", "Humid"]
        matches = [_rand_test_match(rng, pitches[i % len(pitches)], weathers[i % len(weathers)])
                   for i in range(120)]

        fake["col"] = _FakeCol()
        for m in matches:
            m._conditions_recorded = False
            cstats.record_test_match(m)
        slow = fake["col"].store

        fake["col"] = _FakeCol()
        batch = {}
        for m in matches:
            m._conditions_recorded = False
            cstats.accumulate(m, batch)
        cstats.flush_batch(batch)
        fast = fake["col"].store

        check(slow.keys() == fast.keys(), "Test fast + slow touch the same combos")
        check(all(slow[k] == fast[k] for k in slow), "Test fast batched aggregates == slow per-match aggregates")
        check(all(k.endswith("|test") for k in fast), "Tests land in the test bucket only")
        check(sum(d.get("matches", 0) for d in fast.values()) == 120, "all 120 Tests folded in")
        check(all(d.get("t_inns", 0) >= d.get("matches", 0) * 3 for d in fast.values()),
              "per-innings Test counters accumulate (>=3 innings per match)")
        print(f"Test PW batch-equivalence: {len(fast)} combos, aggregates identical to per-match path")
    finally:
        cstats._get_db = orig_getdb
        if had_uo:
            pymongo.UpdateOne = saved_uo


if __name__ == "__main__":
    main()
