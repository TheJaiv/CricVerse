# ── Stadiums ──────────────────────────────────────────────────────────────────
# Cosmetic venue labels for tournament fixtures. A "stadium" is purely a name tag
# shown in fixtures / status (and, later, on the scoreboard image). It does NOT
# affect gameplay — pitch and weather are rolled independently by the conditions
# system in tournament_manager.py.
#
# Flow:
#   • A tournament carries a manager-editable pool of stadium names: tourney["stadiums"].
#   • New ACL tournaments are seeded with DEFAULT_ACL_STADIUMS (managers can edit the
#     pool with the cvt stadium_add / stadium_remove / stadium_clear commands).
#   • When the tournament starts (and whenever conditions are filled), each fixture is
#     assigned a random stadium from the pool via assign_stadiums().
#   • Managers can override a single match's venue with cvt set_stadium.
#
# PORTABILITY: the feature is gated to the tournament types listed in
# STADIUM_TOURNEY_TYPES (currently ACL only). To bring stadiums to the main bot for
# other formats, add the type here (or make stadiums_enabled() return True). Nothing
# else in this module is ACL-specific, so it lifts cleanly.

import random

# Tournament types for which stadiums are active. Add types here to enable elsewhere.
# ("dsl" venues are NOT random — each team has a home ground; see dsl_manager.py.
#  "ccodi" venues are ROUND-AWARE — every venue is used at most once per round.)
STADIUM_TOURNEY_TYPES = {"acl", "dsl", "ccodi"}

# Starter pool seeded onto new ACL tournaments. Cosmetic — rename/replace freely with
# cvt stadium_add / stadium_remove / stadium_clear before the tournament starts.
DEFAULT_ACL_STADIUMS = [
    "Konoha Stadium",
    "Suna Cricket Ground",
    "Kiri Oval",
    "Iwa Arena",
    "Kumo Coliseum",
    "Akatsuki Dome",
    "Uchiha Grounds",
    "Hokage Park",
    "Valley of the End Arena",
    "Rain Village Stadium",
]

# Starter pool for new CCODI seasons — 5 venues (a CCODI round has 4 matches, so no
# venue ever repeats within a round and the pool rotates evenly across rounds).
DEFAULT_CCODI_STADIUMS = [
    "Crimson Bowl",
    "Sapphire Oval",
    "Golden Palm Stadium",
    "Thunder Ridge Ground",
    "Royal Crown Arena",
]


def linked_stadiums(tourney):
    """True when the tournament was created with stadiums=linked: every team sets a
    home stadium carrying a FIXED home pitch, and home fixtures are played there."""
    return bool(tourney) and tourney.get("stadium_mode") == "linked"


def stadiums_enabled(tourney):
    """True if this tournament's type supports stadiums."""
    if not tourney:
        return False
    return tourney.get("tournament_type") in STADIUM_TOURNEY_TYPES or linked_stadiums(tourney)


def default_stadium_pool(tournament_type):
    """The pool to seed onto a freshly-created tournament of this type."""
    if tournament_type == "ccodi":
        return list(DEFAULT_CCODI_STADIUMS)
    if tournament_type in STADIUM_TOURNEY_TYPES:
        return list(DEFAULT_ACL_STADIUMS)
    return []


def get_stadium_pool(tourney):
    """The tournament's stadium pool (never None)."""
    return tourney.get("stadiums") or []


def canonical_stadium(name, pool):
    """Case-insensitive match of `name` within `pool`; returns the pool's spelling or None."""
    if not name:
        return None
    nm = name.strip().lower()
    return next((s for s in pool if s.lower() == nm), None)


def _assign_ccodi_stadiums(tourney):
    """CCODI: round-aware venues — every stadium is used AT MOST ONCE per round
    (a round has 4 matches, the pool 5 venues), rotating the pool start by round
    number so all venues cycle evenly across the season. Knockouts get a random
    venue. Idempotent: already-assigned / completed matches are respected, and a
    manager's cvt set_stadium pick is treated as that round's 'used' venue.
    Legacy CCODI seasons (string rounds, empty pool) are untouched."""
    pool = get_stadium_pool(tourney)
    if not pool:
        return
    schedule = tourney.get("schedule", [])
    by_round = {}
    ko = []
    for m in schedule:
        if m.get("stage") == "group" and isinstance(m.get("round"), int):
            by_round.setdefault(m["round"], []).append(m)
        else:
            ko.append(m)
    for rnd, ms in by_round.items():
        used = {m.get("stadium") for m in ms if m.get("stadium")}
        rotation = [pool[(rnd - 1 + i) % len(pool)] for i in range(len(pool))]
        avail = [v for v in rotation if v not in used]
        for m in ms:
            if m.get("status") == "completed" or m.get("stadium"):
                continue
            m["stadium"] = avail.pop(0) if avail else random.choice(pool)
    for m in ko:
        if m.get("status") != "completed" and not m.get("stadium"):
            m["stadium"] = random.choice(pool)


def assign_stadiums(tourney):
    """Idempotently assign a random stadium from the pool to each non-completed match
    that doesn't already have one. No-op when stadiums are disabled or the pool is empty.
    Safe to call repeatedly (alongside assign_tournament_conditions)."""
    if not stadiums_enabled(tourney):
        return
    if tourney.get("tournament_type") == "dsl":
        # DSL: league matches at the home (team1) team's ground, playoffs per policy.
        from dsl_manager import assign_dsl_stadiums   # lazy — avoids circular import
        return assign_dsl_stadiums(tourney)
    if tourney.get("tournament_type") == "ccodi":
        return _assign_ccodi_stadiums(tourney)
    if linked_stadiums(tourney):
        # Linked mode: each fixture is played at the HOME (team1) team's stadium.
        # Knockouts (where team1 is a seed, not a host) get a random linked ground.
        homes = [t.get("home_stadium") for t in tourney.get("teams", []) if t.get("home_stadium")]
        by_team = {t["name"]: t.get("home_stadium") for t in tourney.get("teams", [])}
        for m in tourney.get("schedule", []):
            if m.get("status") == "completed" or m.get("stadium"):
                continue
            venue = by_team.get(m.get("team1"))
            if not venue and homes:
                venue = random.choice(homes)
            if venue:
                m["stadium"] = venue
        return
    pool = get_stadium_pool(tourney)
    if not pool:
        return
    for m in tourney.get("schedule", []):
        if m.get("status") == "completed":
            continue
        if not m.get("stadium"):
            m["stadium"] = random.choice(pool)


def reroll_stadiums(tourney):
    """Reassign a fresh random stadium to every non-completed match from the pool.
    Returns the number of matches reassigned (0 if disabled / empty pool)."""
    if not stadiums_enabled(tourney):
        return 0
    if tourney.get("tournament_type") == "dsl" or linked_stadiums(tourney):
        return 0   # home-ground based venues — a random reroll would break the mapping
    pool = get_stadium_pool(tourney)
    if not pool:
        return 0
    if tourney.get("tournament_type") == "ccodi":
        # Round-aware reroll: clear pending venues, then reassign with a random
        # rotation offset so uniqueness-per-round is preserved.
        n = 0
        for m in tourney.get("schedule", []):
            if m.get("status") != "completed" and m.get("stadium"):
                m["stadium"] = None
                n += 1
        random.shuffle(tourney["stadiums"])
        _assign_ccodi_stadiums(tourney)
        return n
    n = 0
    for m in tourney.get("schedule", []):
        if m.get("status") == "completed":
            continue
        m["stadium"] = random.choice(pool)
        n += 1
    return n


def stadium_label(m):
    """Short venue tag for a scheduled match, or '' when none is set."""
    s = m.get("stadium")
    return f"📍 {s}" if s else ""
