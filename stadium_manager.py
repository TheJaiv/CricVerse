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
# ("dsl" venues are NOT random — each team has a home ground; see dsl_manager.py.)
STADIUM_TOURNEY_TYPES = {"acl", "dsl"}

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


def stadiums_enabled(tourney):
    """True if this tournament's type supports stadiums."""
    return bool(tourney) and tourney.get("tournament_type") in STADIUM_TOURNEY_TYPES


def default_stadium_pool(tournament_type):
    """The pool to seed onto a freshly-created tournament of this type."""
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
    if tourney.get("tournament_type") == "dsl":
        return 0   # DSL venues are home-ground based — a random reroll would break the mapping
    pool = get_stadium_pool(tourney)
    if not pool:
        return 0
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
