# Stadiums
# Cosmetic venue labels for tournament fixtures. A "stadium" is purely a name tag
# shown in fixtures / status (and, later, on the scoreboard image). It does NOT
# affect gameplay - pitch and weather are rolled independently by the conditions
# system in tournament_manager.py.
#
# Flow:
#   • A tournament carries a manager-editable pool of stadium names: tourney["stadiums"].
#   • EVERY new tournament is seeded with a default pool for its type (managers can edit
#     it with the cvt stadium_add / stadium_remove / stadium_clear commands). A tournament
#     created before stadiums went global is backfilled on its next assign_stadiums().
#   • When the tournament starts (and whenever conditions are filled), each fixture is
#     assigned a random stadium from the pool via assign_stadiums().
#   • Managers can override a single match's venue with cvt set_stadium.
#
# Stadiums are ON for EVERY tournament type. How a venue gets picked still varies:
# • "dsl" - NOT random: each team has a home ground (see dsl_manager.py).
# • "ccodi", "ipl" - ROUND-AWARE: a venue is used at most once per round.
# • linked mode - every fixture at the HOME (team1) team's ground.
# • everything else - a random venue from the pool.

import random

# Types whose venues are picked round-aware (their league is a series of int rounds
# in which every team plays at most once, so a venue need never repeat inside one).
ROUND_AWARE_STADIUM_TYPES = {"ccodi", "ipl"}

# Starter pool seeded onto new ACL tournaments. Cosmetic - rename/replace freely with
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

# Starter pool for new CCODI seasons - 5 venues (a CCODI round has 4 matches, so no
# venue ever repeats within a round and the pool rotates evenly across rounds).
DEFAULT_CCODI_STADIUMS = [
    "Crimson Bowl",
    "Sapphire Oval",
    "Golden Palm Stadium",
    "Thunder Ridge Ground",
    "Royal Crown Arena",
]

# Starter pool for IPL seasons - the ten real IPL home grounds, one per franchise.
DEFAULT_IPL_STADIUMS = [
    "Wankhede Stadium",
    "M. A. Chidambaram Stadium",
    "Eden Gardens",
    "M. Chinnaswamy Stadium",
    "Narendra Modi Stadium",
    "Arun Jaitley Stadium",
    "Rajiv Gandhi International Stadium",
    "Sawai Mansingh Stadium",
    "Ekana Cricket Stadium",
    "Maharaja Yadavindra Singh Stadium",
]

# Starter pool for every other format (round robin, double RR, T20 World Cup ...).
DEFAULT_STADIUMS = [
    "The Oval",
    "Lord's",
    "Newlands",
    "Eden Park",
    "The Gabba",
    "Galle International Stadium",
    "Kensington Oval",
    "National Stadium",
    "Sharjah Cricket Stadium",
    "Seddon Park",
]


def linked_stadiums(tourney):
    """True when the tournament was created with stadiums=linked: every team sets a
    home stadium carrying a FIXED home pitch, and home fixtures are played there."""
    return bool(tourney) and tourney.get("stadium_mode") == "linked"


def stadiums_enabled(tourney):
    """Stadiums are available to every tournament type."""
    return bool(tourney)


def default_stadium_pool(tournament_type):
    """The pool to seed onto a freshly-created tournament of this type."""
    if tournament_type == "ccodi":
        return list(DEFAULT_CCODI_STADIUMS)
    if tournament_type == "ipl":
        return list(DEFAULT_IPL_STADIUMS)
    if tournament_type in ("acl", "dsl"):
        return list(DEFAULT_ACL_STADIUMS)
    return list(DEFAULT_STADIUMS)


def ensure_stadium_pool(tourney):
    """Seed the default pool onto a tournament that hasn't got one - which covers every
    tournament created back when stadiums were gated to ACL/DSL/CCODI. A manager who
    deliberately ran `stadium_clear` keeps their empty pool. Returns the pool."""
    pool = tourney.get("stadiums") or []
    if pool or tourney.get("stadiums_cleared"):
        return pool
    pool = default_stadium_pool(tourney.get("tournament_type"))
    if pool:
        tourney["stadiums"] = pool
    return pool


def get_stadium_pool(tourney):
    """The tournament's stadium pool (never None), seeding the default one if it has
    none yet."""
    return ensure_stadium_pool(tourney)


def canonical_stadium(name, pool):
    """Case-insensitive match of `name` within `pool`; returns the pool's spelling or None."""
    if not name:
        return None
    nm = name.strip().lower()
    return next((s for s in pool if s.lower() == nm), None)


def _assign_round_aware_stadiums(tourney):
    """CCODI / IPL: round-aware venues - every stadium is used AT MOST ONCE per round
    (CCODI rounds hold 4 matches and IPL rounds 5, against pools of 5 and 10), rotating
    the pool start by round number so all venues cycle evenly across the season.
    Knockouts get a random venue. Idempotent: already-assigned / completed matches are
    respected, and a manager's cvt set_stadium pick is treated as that round's 'used'
    venue. Legacy seasons (string rounds, empty pool) are untouched."""
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
        from league.dsl_manager import assign_dsl_stadiums   # lazy - avoids circular import
        return assign_dsl_stadiums(tourney)
    if tourney.get("tournament_type") in ROUND_AWARE_STADIUM_TYPES and not linked_stadiums(tourney):
        return _assign_round_aware_stadiums(tourney)
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
        return 0   # home-ground based venues - a random reroll would break the mapping
    pool = get_stadium_pool(tourney)
    if not pool:
        return 0
    if tourney.get("tournament_type") in ROUND_AWARE_STADIUM_TYPES:
        # Round-aware reroll: clear pending venues, then reassign with a random
        # rotation offset so uniqueness-per-round is preserved.
        n = 0
        for m in tourney.get("schedule", []):
            if m.get("status") != "completed" and m.get("stadium"):
                m["stadium"] = None
                n += 1
        random.shuffle(tourney["stadiums"])
        _assign_round_aware_stadiums(tourney)
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
