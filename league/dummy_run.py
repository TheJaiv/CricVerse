"""Dummy data-farm run: balanced XIs drafted from the live player DB, played across
every pitch x weather combo purely to grow global_stats (GS) + conditions_stats (PW).

There is NO tournament document and NO scorecard persistence - the two stat recorders
fire per match (inside bot._run_full_match_sync) and the match object is thrown away.
That is what makes it quick: the slow part of a real tournament is the Mongo tournament
doc + per-match scorecard writes, none of which happen here. Owner-triggered via
`cv dummyrun` in bot.py.

This module is deliberately pure logic - no discord, no engine imports - so
tools/dummy_flow_test.py can exercise team balance + participation without a running
bot or a Mongo connection. bot.py owns the CricketMatch construction and the
_run_full_match_sync loop; here we only turn a player pool into balanced XIs.
"""
import random

# One balanced XI mirrors _EQUAL_TEMPLATE in bot.py (4 top-order bat, a keeper, a pace
# all-rounder, a spin all-rounder, 3 seamers, 1 spinner), collapsed onto six buckets.
XI_SLOTS = {"BAT": 4, "WK": 1, "AR_PACE": 1, "AR_SPIN": 1, "PACE": 3, "SPIN": 1}
assert sum(XI_SLOTS.values()) == 11

# Batting-order rank per bucket (top order first, tail last).
_ORDER = {"BAT": 0, "WK": 1, "AR_PACE": 2, "AR_SPIN": 2, "PACE": 3, "SPIN": 3}

# If a bucket runs dry on a lopsided pool, borrow from these (in order) so a match can
# still be fielded. On the real DB (hundreds per bucket) the fallback never triggers.
_FALLBACK = {
    "BAT":     ["WK", "AR_SPIN", "AR_PACE", "PACE", "SPIN"],
    "WK":      ["BAT", "AR_SPIN", "AR_PACE"],
    "AR_PACE": ["PACE", "AR_SPIN", "BAT"],
    "AR_SPIN": ["SPIN", "AR_PACE", "BAT"],
    "PACE":    ["AR_PACE", "SPIN", "BAT"],
    "SPIN":    ["AR_SPIN", "PACE", "BAT"],
}


def bucket_of(role):
    """Map a DB role string onto one of the six XI buckets."""
    r = role or ""
    if r == "Batter_WK":
        return "WK"
    if r.startswith("All-Rounder"):
        return "AR_SPIN" if "Spin" in r else "AR_PACE"
    if r.startswith("Bowler"):
        return "SPIN" if "Spin" in r else "PACE"
    return "BAT"   # plain "Batter" and any unknown role


def slot_rating(p):
    """Strength in a player's own discipline - the number the draft balances on, so a
    batter is judged on bat and a bowler on bowl (mirrors the OVR-by-strength rule)."""
    b = bucket_of(p.get("role", ""))
    bat, bowl = int(p.get("bat", 0)), int(p.get("bowl", 0))
    if b in ("PACE", "SPIN"):
        return bowl
    if b in ("AR_PACE", "AR_SPIN"):
        return max(bat, bowl)
    return bat


def select_pool(all_players, frac=0.70, rng=random):
    """A random `frac` slice of the DB - the only players who take the field this run."""
    n = min(len(all_players), max(22, round(len(all_players) * frac)))
    pool = list(all_players)
    rng.shuffle(pool)
    return pool[:n]


def bucketize(pool):
    """{bucket: [players]} - the standing pool each match draws from (never emptied)."""
    buckets = {k: [] for k in XI_SLOTS}
    for p in pool:
        buckets[bucket_of(p.get("role", ""))].append(p)
    return buckets


def _collect(buckets, cat, need, used, play_counts, rng):
    """`need` distinct players for category `cat`, preferring the least-played (so game
    time spreads evenly across the pool), skipping anyone already picked this match.
    Falls back to related buckets if `cat` can't cover `need`."""
    out, taken = [], set()
    for c in [cat] + _FALLBACK[cat]:
        avail = [p for p in buckets[c]
                 if p["name"] not in used and p["name"] not in taken]
        avail.sort(key=lambda p: (play_counts.get(p["name"], 0), rng.random()))
        for p in avail:
            if len(out) >= need:
                break
            out.append(p)
            taken.add(p["name"])
        if len(out) >= need:
            break
    return out


def draft_match(buckets, play_counts, rng):
    """Draft two balanced, correctly-composed XIs for one match.

    Picks 2*slot least-played players per bucket, then hands them out strongest-first to
    whichever team is currently weaker (and still needs that slot) - so both XIs end up
    with the same shape and near-identical total strength. Returns
    (team_a_players, team_b_players, strength_a, strength_b); play_counts is bumped for
    the 22 who played.
    """
    used = set()
    picked = {}
    for cat, req in XI_SLOTS.items():
        cands = _collect(buckets, cat, 2 * req, used, play_counts, rng)
        picked[cat] = cands
        used.update(p["name"] for p in cands)

    team_a, team_b = [], []
    need_a = dict(XI_SLOTS)
    need_b = dict(XI_SLOTS)
    str_a = str_b = 0

    flat = [(p, cat) for cat, players in picked.items() for p in players]
    flat.sort(key=lambda pc: slot_rating(pc[0]), reverse=True)
    for p, cat in flat:
        a_ok, b_ok = need_a[cat] > 0, need_b[cat] > 0
        if a_ok and b_ok:
            to_a = str_a < str_b or (str_a == str_b and rng.random() < 0.5)
        else:
            to_a = a_ok
        if to_a:
            team_a.append(p); need_a[cat] -= 1; str_a += slot_rating(p)
        else:
            team_b.append(p); need_b[cat] -= 1; str_b += slot_rating(p)

    for p in team_a + team_b:
        play_counts[p["name"]] = play_counts.get(p["name"], 0) + 1
    return order_xi(team_a), order_xi(team_b), str_a, str_b


def order_xi(players):
    """Top order -> tail: bat/keeper first, all-rounders in the middle, bowlers last,
    stronger bat higher within each band. Gives the engine a sensible batting order."""
    return sorted(players, key=lambda p: (_ORDER[bucket_of(p.get("role", ""))],
                                          -int(p.get("bat", 0))))


def feasibility(pool, frac_note=None):
    """Quick pre-run sanity read: pool size and whether every bucket can field two XIs."""
    buckets = bucketize(pool)
    short = {c: len(buckets[c]) for c in XI_SLOTS if len(buckets[c]) < 2 * XI_SLOTS[c]}
    return {"pool": len(pool), "buckets": {c: len(buckets[c]) for c in XI_SLOTS},
            "short": short, "ok": not short}
