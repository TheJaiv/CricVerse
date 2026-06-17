"""
Career Mode data layer for CricVerse  (v2 — all-rounder model, synced to the sim DB).

Stores one document per user in a dedicated `careers` Mongo collection (NOT the
single cricket_bot_data blob) to avoid the 16MB limit and concurrent-save races.
Reuses subscription_manager._get_db() so the connection is identical to the bot.

Design (v2):
  • EVERY player is an all-rounder (bats AND bowls).
  • At creation you choose a BOWLING TYPE (pace / off-spin / leg-spin) and a
    BATTING MINDSET (aggressor / standard / anchor).
  • Ratings are SYNCED to the main sim: rookies start at OVR 68 (the DB floor —
    nobody in the sim is rated below ~68), max 99. Progression is deliberately
    expensive so reaching the 90s is a long grind.
A career is GLOBAL (one identity per Discord user across every server).
"""
import time
import random
import datetime
from threading import Thread

from subscription_manager import _get_db

CAREER_CACHE = {}   # user_id(str) -> career dict

# Four upgradeable attributes (pace/spin merged into one "bowling" stat — your
# chosen bowling type decides HOW you bowl, this is HOW WELL).
ATTRS = ("power", "control", "bowling", "stamina")

# Bowling type → sim engine role (all All-Rounder_* since everyone bats+bowls)
BOWLING_TYPES = {
    "pace":    {"label": "Express Pace", "emoji": "🔥", "engine_role": "All-Rounder_Pace"},
    "offspin": {"label": "Off-Spin",     "emoji": "🌀", "engine_role": "All-Rounder_Spin_Off"},
    "legspin": {"label": "Leg-Spin",     "emoji": "🪀", "engine_role": "All-Rounder_Spin_Leg"},
}

# Batting mindset → sim engine archetype
MINDSETS = {
    "aggressor": {"label": "Aggressor", "emoji": "💥", "engine_arch": "Aggressor",
                  "desc": "Attacking intent, high strike rate, clears the ropes."},
    "standard":  {"label": "Standard",  "emoji": "⚖️", "engine_arch": "Standard",
                  "desc": "Balanced — rotates strike, punishes the bad ball."},
    "anchor":    {"label": "Anchor",    "emoji": "🧱", "engine_arch": "Anchor",
                  "desc": "Low-risk accumulator who builds long innings."},
}

# Rookie starts at a clean OVR 60 (normalised exactly at creation) — a raw prospect
# below the sim's pro floor, with a long climb through the tiers to the 90s legends.
BASE_ATTRS = {"power": 58, "control": 62, "bowling": 56, "stamina": 60}
BASE_OVR = 60

# Tiers spread across the full 60→99 career range.
TIERS = [  # (min, max, name, blurb)
    (93, 99, "Diamond",  "The Legend"),
    (85, 92, "Platinum", "The Elite"),
    (77, 84, "Gold",     "The Star"),
    (69, 76, "Silver",   "The Pro"),
    (0,  68, "Bronze",   "The Rookie"),
]


def tier_for_ovr(ovr: int) -> str:
    for lo, hi, name, _ in TIERS:
        if lo <= ovr <= hi:
            return name
    return "Bronze"


def next_tier_info(ovr: int):
    """Return (next_tier_name, min_ovr_needed) for the tier above `ovr`, or (None, None) at Diamond."""
    for lo, hi, name, _ in sorted(TIERS, key=lambda t: t[0]):
        if lo > ovr:
            return name, lo
    return None, None


def _clamp(v, lo=0, hi=99):
    return max(lo, min(hi, int(round(v))))


def bat_skill(a):
    return _clamp(0.45 * a["control"] + 0.35 * a["power"] + 0.20 * a["stamina"])


def bowl_skill(a):
    return _clamp(0.55 * a["bowling"] + 0.25 * a["control"] + 0.20 * a["stamina"])


def compute_ovr(career: dict) -> int:
    a = career["attributes"]
    return round(0.55 * bat_skill(a) + 0.45 * bowl_skill(a))


def career_to_engine(career: dict) -> dict:
    """Convert a career into the sim engine's {name,bat,bowl,role,archetype} shape."""
    a = career["attributes"]
    return {
        "name": career.get("username", "Rookie"),
        "bat": bat_skill(a),
        "bowl": bowl_skill(a),
        "role": BOWLING_TYPES[career["bowling_type"]]["engine_role"],
        "archetype": MINDSETS[career["mindset"]]["engine_arch"],
    }


# ── Progression economics (deliberately HARD — tiers must be earned) ─────────
# Cost ramps steeply so Gold is a multi-week goal, Platinum months, and Diamond
# a long-term grind that even premium players can't rush.
def upgrade_cost(v: int) -> int:
    """Coin cost to raise an attribute from v to v+1. Positive & rising from the
    new ~56 base; steep at the top so the high tiers are a real long-term grind:
      v58≈48  v65≈90  v70≈140  v77≈266  v85≈516  v90≈786  v95≈1426  v98≈2166."""
    return int(round(
        30
        + max(0, v - 55) * 6
        + max(0, v - 70) * 14
        + max(0, v - 82) * 32
        + max(0, v - 90) * 70
        + max(0, v - 95) * 120
    ))


def _blank_stats():
    return {
        "bat": {"matches": 0, "innings": 0, "not_outs": 0, "runs": 0, "hs": 0,
                "balls": 0, "fours": 0, "sixes": 0, "fifties": 0, "hundreds": 0, "outs": 0},
        "bowl": {"balls": 0, "runs": 0, "wickets": 0, "maidens": 0, "best_w": 0, "best_r": 999},
        "field": {"catches": 0, "stumpings": 0},
    }


def _normalize_to_ovr(career: dict, target: int = BASE_OVR):
    """Scale attributes proportionally so OVR == target."""
    raw = compute_ovr(career)
    if raw > 0 and raw != target:
        f = target / raw
        for k in ATTRS:
            career["attributes"][k] = _clamp(career["attributes"][k] * f, 1, 99)
    guard = 0
    while compute_ovr(career) != target and guard < 30:
        a = career["attributes"]
        a["control"] = _clamp(a["control"] + (1 if compute_ovr(career) < target else -1), 1, 99)
        guard += 1
    career["ovr"] = compute_ovr(career)


def new_career(user_id, username, bowling_type, mindset) -> dict:
    c = {
        "_id": str(user_id),
        "username": username,
        "bowling_type": bowling_type,
        "mindset": mindset,
        "attributes": dict(BASE_ATTRS),
        "coins": 0,
        "xp": 0,
        "debut_done": False,
        "created_at": int(time.time()),
        "claims": {"daily": 0, "weekly": 0, "monthly": 0},
        "week_boost_until": 0,
        "cosmetic_title": "",
        "stats": _blank_stats(),
    }
    _normalize_to_ovr(c, BASE_OVR)
    c["tier"] = tier_for_ovr(c["ovr"])
    return c


def refresh_ovr(career: dict):
    old = career.get("tier")
    career["ovr"] = compute_ovr(career)
    career["tier"] = tier_for_ovr(career["ovr"])
    return old, career["tier"]


# ── Persistence ─────────────────────────────────────────────────────────────
def load_careers():
    try:
        col = _get_db()["careers"]
        CAREER_CACHE.clear()
        for doc in col.find({}):
            CAREER_CACHE[doc["_id"]] = doc
        print(f"✅ Loaded {len(CAREER_CACHE)} career(s) from MongoDB!")
    except Exception as e:
        print(f"❌ Career load error: {e}")


def get_career(user_id):
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
    try:
        _get_db()["careers"].replace_one({"_id": career["_id"]}, career, upsert=True)
        return True
    except Exception as e:
        print(f"❌ save_career error: {e}")
        return False


def async_save_career(career: dict):
    CAREER_CACHE[career["_id"]] = career
    Thread(target=save_career, args=(career,)).start()


def delete_career(user_id):
    """Completely remove a user's career (cache + Mongo). Returns True if one existed."""
    uid = str(user_id)
    existed = CAREER_CACHE.pop(uid, None) is not None
    try:
        res = _get_db()["careers"].delete_one({"_id": uid})
        return existed or res.deleted_count > 0
    except Exception as e:
        print(f"❌ delete_career error: {e}")
        return existed


def create_career(user_id, username, bowling_type, mindset):
    """Create + persist a new career. Returns (career, error)."""
    uid = str(user_id)
    if bowling_type not in BOWLING_TYPES:
        return None, "Invalid bowling type."
    if mindset not in MINDSETS:
        return None, "Invalid batting mindset."
    if get_career(uid):
        return None, "You already have a career! Use `cv profile` to view it."
    c = new_career(uid, username, bowling_type, mindset)
    async_save_career(c)
    return c, None


# ── Economy / progression actions ───────────────────────────────────────────
def upgrade_attribute(career: dict, attr: str, want: int = 1):
    """Spend coins to raise `attr` by up to `want` points (as many as affordable).
    Returns (bought:int, spent:int, msg:str)."""
    if attr not in ATTRS:
        return 0, 0, f"Unknown attribute. Choose: {', '.join(ATTRS)}."
    bought = spent = 0
    while bought < want:
        v = career["attributes"][attr]
        if v >= 99:
            if bought == 0:
                return 0, 0, f"Your **{attr}** is already maxed at 99."
            break
        cost = upgrade_cost(v)
        if career["coins"] < cost:
            if bought == 0:
                return 0, 0, f"Not enough coins — next **{attr}** point costs **{cost}** 🪙 (you have {career['coins']:,})."
            break
        career["coins"] -= cost
        career["attributes"][attr] = v + 1
        spent += cost
        bought += 1
    refresh_ovr(career)
    async_save_career(career)
    return bought, spent, "ok"


DAILY_MIN, DAILY_MAX = 25, 55      # tightened — dailies alone are a slow trickle, not a fast-track
WEEKLY_AMOUNT  = 800               # PREMIUM ONLY
MONTHLY_AMOUNT = 3000              # PREMIUM ONLY
WEEK_BOOST = 1.05                  # 5% coin boost for the week (premium weekly perk)


def _boost_mult(career: dict) -> float:
    return WEEK_BOOST if career.get("week_boost_until", 0) > int(time.time()) else 1.0


def _fmt_remaining(secs):
    h, m = secs // 3600, (secs % 3600) // 60
    if h >= 24:
        return f"{h // 24}d {h % 24}h"
    return f"{h}h {m}m"


def _claim(career, key, cooldown, base):
    now = int(time.time())
    last = career.get("claims", {}).get(key, 0)
    if now - last < cooldown:
        return None, f"⏳ Already claimed. Come back in **{_fmt_remaining(cooldown - (now - last))}**."
    career.setdefault("claims", {})[key] = now
    return now, None


def claim_daily(career: dict):
    """Returns (amount, error). 24h cooldown."""
    now, err = _claim(career, "daily", 86400, 0)
    if err:
        return 0, err
    amount = int(round(random.randint(DAILY_MIN, DAILY_MAX) * _boost_mult(career)))
    career["coins"] += amount
    async_save_career(career)
    return amount, None


def claim_weekly(career: dict):
    """Premium/booster weekly: coins + a 5% week-long coin boost. 7d cooldown."""
    now, err = _claim(career, "weekly", 7 * 86400, 0)
    if err:
        return 0, err
    career["coins"] += WEEKLY_AMOUNT
    career["week_boost_until"] = now + 7 * 86400
    async_save_career(career)
    return WEEKLY_AMOUNT, None


def claim_monthly(career: dict):
    """Premium/booster monthly: coins + a cosmetic profile title. 30d cooldown."""
    now, err = _claim(career, "monthly", 30 * 86400, 0)
    if err:
        return 0, err
    career["coins"] += MONTHLY_AMOUNT
    career["cosmetic_title"] = "[Patron]"
    async_save_career(career)
    return MONTHLY_AMOUNT, None


def award_match_earnings(career, *, runs=0, fifties=0, hundreds=0, wickets=0,
                         maidens=0, catches=0, stumpings=0, won=False, is_real_match=True):
    """Match payout — PvP/club matches ONLY. AI matches earn ZERO coins (they exist
    purely for quests/practice). Returns coins awarded."""
    if not is_real_match:
        return 0
    coins = 60                                    # base pay
    if won:
        coins += 40                               # victory bonus
    coins += (runs // 12) * 5 + fifties * 15 + hundreds * 30    # batting
    coins += wickets * 12 + maidens * 12          # bowling
    coins += (catches + stumpings) * 6            # fielding
    coins = int(round(coins * _boost_mult(career)))
    career["coins"] += coins
    async_save_career(career)
    return coins


def get_today_str():
    return datetime.date.today().isoformat()
