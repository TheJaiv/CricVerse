# Dummy data-farm draft test (headless, no Discord / no Mongo / no engine).
# Run from the repo root: python tools/dummy_flow_test.py
#
# Verifies the pure drafting logic in league/dummy_run.py that bot's `cv dummyrun`
# relies on: 70% pool selection, correctly-composed balanced XIs, no player on both
# sides of a match, even game-time rotation across the pool, participation reaching
# the whole pool over enough matches, and the pitch x weather schedule covering every
# surface + sky (and all 170 combos) via the i%17 / i%10 cycle bot.py uses.

import os
import sys
import csv
import random
from collections import Counter

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

    # mixed T20+ODI: each format walks its OWN pitch=s%17 / weather=s%10 counter (as the
    # worker does), so both formats still cover all 170 combos - a shared global index
    # would only hit even weather slots per format.
    seq = {20: 0, 50: 0}
    cells = set()
    for i in range(2 * len(pitches) * len(weathers)):   # 340 matches, 50/50 mix
        overs = 20 if i % 2 == 0 else 50
        s = seq[overs]
        cells.add((pitches[s % len(pitches)], weathers[s % len(weathers)], overs))
        seq[overs] += 1
    t20_cells = {(p, w) for p, w, o in cells if o == 20}
    odi_cells = {(p, w) for p, w, o in cells if o == 50}
    check(len(t20_cells) == len(pitches) * len(weathers), f"mixed run covers all T20 combos ({len(t20_cells)})")
    check(len(odi_cells) == len(pitches) * len(weathers), f"mixed run covers all ODI combos ({len(odi_cells)})")

    # --- lopsided pool still fields teams (fallback path) ---
    bat_heavy = [p for p in db if bucket_of(p["role"]) in ("BAT", "WK")][:40]
    lb = bucketize(bat_heavy)
    la, lbb, _, _ = draft_match(lb, {}, rng)
    check(len(la) == 11 and len(lbb) == 11, "bat-heavy pool still fields two XIs via fallback")

    # --- fast (batched) PW recording == slow (per-match) PW recording ---
    test_pw_batch_equivalence(rng)

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


if __name__ == "__main__":
    main()
