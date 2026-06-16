"""
Career Mode data layer for CricVerse.

Stores one document per user in a dedicated `careers` Mongo collection (NOT the
single cricket_bot_data blob) to avoid the 16MB limit and concurrent-save races.
Reuses subscription_manager._get_db() so the Mongo connection / URI-encoding is
identical to the rest of the bot.

A career is GLOBAL (one identity per Discord user across every server).
"""
import time
import datetime
from threading import Thread

from subscription_manager import _get_db

# In-memory cache: user_id(str) -> career dict
CAREER_CACHE = {}

# ────────────────────────────────────────────────────────────────────────────
# ARCHETYPES
# Each defines: a label, starting attributes (tuned so OVR ≈ 50 at creation),
# the OVR weighting across the 5 attributes (weights sum to 1.0), and how the
# career maps onto the sim engine's role/archetype.
# Attributes: power, control, pace, spin, stamina  (0–99)
# ────────────────────────────────────────────────────────────────────────────
ATTRS = ("power", "control", "pace", "spin", "stamina")

ARCHETYPES = {
    "anchor": {
        "label": "Top-Order Anchor", "emoji": "🧱",
        "desc": "High control, low risk. Builds long, steady partnerships.",
        "start": {"power": 48, "control": 58, "pace": 30, "spin": 30, "stamina": 52},
        "weights": {"power": 0.20, "control": 0.42, "pace": 0.09, "spin": 0.09, "stamina": 0.20},
        "engine_role": "Batter", "engine_arch": "Anchor",
    },
    "power": {
        "label": "Power-Hitter", "emoji": "💥",
        "desc": "Massive boundary potential, huge strike rate, clears the ropes.",
        "start": {"power": 60, "control": 46, "pace": 28, "spin": 28, "stamina": 50},
        "weights": {"power": 0.45, "control": 0.20, "pace": 0.075, "spin": 0.075, "stamina": 0.20},
        "engine_role": "Batter", "engine_arch": "Aggressor",
    },
    "pacer": {
        "label": "Express Pacer", "emoji": "🔥",
        "desc": "Pure raw speed. High chance to dismantle stumps & force edges.",
        "start": {"power": 28, "control": 46, "pace": 60, "spin": 28, "stamina": 52},
        "weights": {"power": 0.05, "control": 0.15, "pace": 0.50, "spin": 0.05, "stamina": 0.25},
        "engine_role": "Bowler_Pace", "engine_arch": "Aggressor",
    },
    "spinner": {
        "label": "Mystery Spinner", "emoji": "🌀",
        "desc": "Hard to read. Restricts runs heavily and forces mistimed shots.",
        "start": {"power": 28, "control": 50, "pace": 28, "spin": 60, "stamina": 48},
        "weights": {"power": 0.05, "control": 0.25, "pace": 0.05, "spin": 0.50, "stamina": 0.15},
        "engine_role": "Bowler_Spin_Off", "engine_arch": "Standard",
    },
    "death": {
        "label": "Slingy Death Specialist", "emoji": "🎯",
        "desc": "Master of yorkers & slower balls at the back end of an innings.",
        "start": {"power": 28, "control": 54, "pace": 56, "spin": 30, "stamina": 50},
        "weights": {"power": 0.05, "control": 0.30, "pace": 0.40, "spin": 0.05, "stamina": 0.20},
        "engine_role": "Bowler_Pace", "engine_arch": "Finisher",
    },
    "keeper": {
        "label": "Wicketkeeper-Batter", "emoji": "🧤",
        "desc": "Crucial behind the stumps; a solid middle-order bat.",
        "start": {"power": 50, "control": 54, "pace": 28, "spin": 30, "stamina": 52},
        "weights": {"power": 0.27, "control": 0.35, "pace": 0.08, "spin": 0.10, "stamina": 0.20},
        "engine_role": "Batter_WK", "engine_arch": "Finisher",
    },
    "allrounder": {
        "label": "Genuine All-Rounder", "emoji": "⚔️",
        "desc": "Decent at both; slower to upgrade but highly versatile.",
        "start": {"power": 46, "control": 48, "pace": 46, "spin": 40, "stamina": 50},
        "weights": {"power": 0.20, "control": 0.20, "pace": 0.20, "spin": 0.20, "stamina": 0.20},
        "engine_role": "All-Rounder_Pace", "engine_arch": "Standard",
    },
}

TIERS = [  # (min_ovr, max_ovr, name)
    (90, 99, "Diamond"), (80, 89, "Platinum"), (70, 79, "Gold"),
    (60, 69, "Silver"), (0, 59, "Bronze"),
]


def tier_for_ovr(ovr: int) -> str:
    for lo, hi, name in TIERS:
        if lo <= ovr <= hi:
            return name
    return "Bronze"


def compute_ovr(career: dict) -> int:
    w = ARCHETYPES[career["archetype"]]["weights"]
    a = career["attributes"]
    return round(sum(a[k] * w[k] for k in ATTRS))


def _clamp(v, lo=0, hi=99):
    return max(lo, min(hi, int(round(v))))


def career_to_engine(career: dict) -> dict:
    """Convert a career into the sim engine's {name,bat,bowl,role,archetype} shape."""
    a = career["attributes"]
    arch = ARCHETYPES[career["archetype"]]
    bat = _clamp(0.45 * a["control"] + 0.35 * a["power"] + 0.20 * a["stamina"])
    hi_bowl, lo_bowl = max(a["pace"], a["spin"]), min(a["pace"], a["spin"])
    bowl = _clamp(0.50 * hi_bowl + 0.25 * a["control"] + 0.15 * a["stamina"] + 0.10 * lo_bowl)
    role = arch["engine_role"]
    # spinners: choose off/leg flavour (default off; leg if spin-leaning naming later)
    return {
        "name": career.get("username", "Rookie"),
        "bat": bat, "bowl": bowl,
        "role": role, "archetype": arch["engine_arch"],
    }


def _blank_stats():
    return {
        "bat": {"matches": 0, "innings": 0, "not_outs": 0, "runs": 0, "hs": 0,
                "balls": 0, "fours": 0, "sixes": 0, "fifties": 0, "hundreds": 0, "outs": 0},
        "bowl": {"balls": 0, "runs": 0, "wickets": 0, "maidens": 0, "best_w": 0, "best_r": 999},
        "field": {"catches": 0, "stumpings": 0},
    }


def _normalize_to_ovr(career: dict, target: int = 50):
    """Scale attributes proportionally so OVR == target, preserving archetype shape."""
    raw = compute_ovr(career)
    if raw > 0 and raw != target:
        f = target / raw
        for k in ATTRS:
            career["attributes"][k] = _clamp(career["attributes"][k] * f, 1, 99)
    # fix rounding drift via the heaviest-weighted attribute
    w = ARCHETYPES[career["archetype"]]["weights"]
    heavy = max(w, key=w.get)
    guard = 0
    while compute_ovr(career) != target and guard < 30:
        a = career["attributes"]
        a[heavy] = _clamp(a[heavy] + (1 if compute_ovr(career) < target else -1), 1, 99)
        guard += 1
    career["ovr"] = compute_ovr(career)


def new_career(user_id: str, username: str, archetype: str) -> dict:
    arch = ARCHETYPES[archetype]
    c = {
        "_id": str(user_id),
        "username": username,
        "archetype": archetype,
        "attributes": dict(arch["start"]),
        "coins": 0,
        "xp": 0,
        "debut_done": False,
        "created_at": int(time.time()),
        "claims": {"daily": 0, "weekly": 0, "monthly": 0},
        "week_boost_until": 0,
        "cosmetic_title": "",
        "stats": _blank_stats(),
    }
    _normalize_to_ovr(c, 50)
    c["tier"] = tier_for_ovr(c["ovr"])
    return c


# ── Persistence ─────────────────────────────────────────────────────────────

def load_careers():
    """Load all careers into CAREER_CACHE at startup."""
    try:
        col = _get_db()["careers"]
        CAREER_CACHE.clear()
        for doc in col.find({}):
            CAREER_CACHE[doc["_id"]] = doc
        print(f"✅ Loaded {len(CAREER_CACHE)} career(s) from MongoDB!")
    except Exception as e:
        print(f"❌ Career load error: {e}")


def get_career(user_id: str):
    """Return a career from cache, lazily fetching from Mongo if not cached."""
    uid = str(user_id)
    if uid in CAREER_CACHE:
        return CAREER_CACHE[uid]
    try:
        doc = _get_db()["careers"].find_one({"_id": uid})
        if doc:
            CAREER_CACHE[uid] = doc
            return doc
    except Exception as e:
        print(f"❌ get_career error: {e}")
    return None


def save_career(career: dict):
    """Upsert a single career document (synchronous)."""
    try:
        _get_db()["careers"].replace_one({"_id": career["_id"]}, career, upsert=True)
        return True
    except Exception as e:
        print(f"❌ save_career error: {e}")
        return False


def async_save_career(career: dict):
    """Persist a career in a background thread; update cache immediately."""
    CAREER_CACHE[career["_id"]] = career
    Thread(target=save_career, args=(career,)).start()


def create_career(user_id: str, username: str, archetype: str):
    """Create + persist a new career. Returns (career, error_msg)."""
    uid = str(user_id)
    if archetype not in ARCHETYPES:
        return None, f"Unknown archetype '{archetype}'."
    if get_career(uid):
        return None, "You already have a career! Use `cv profile` to view it."
    c = new_career(uid, username, archetype)
    async_save_career(c)
    return c, None


def refresh_ovr(career: dict):
    """Recompute OVR + tier after an attribute change. Returns (old_tier, new_tier)."""
    old_tier = career.get("tier")
    career["ovr"] = compute_ovr(career)
    career["tier"] = tier_for_ovr(career["ovr"])
    return old_tier, career["tier"]


def get_today_str():
    return datetime.date.today().isoformat()
