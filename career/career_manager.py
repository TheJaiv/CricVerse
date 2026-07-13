"""
Career Mode data layer for CricVerse  (v2 - all-rounder model, synced to the sim DB).

Stores one document per user in a dedicated `careers` Mongo collection (NOT the
single cricket_bot_data blob) to avoid the 16MB limit and concurrent-save races.
Reuses subscription_manager._get_db() so the connection is identical to the bot.

Design (v2):
  • EVERY player is an all-rounder (bats AND bowls).
  • At creation you choose a BOWLING TYPE (pace / off-spin / leg-spin) and a
    BATTING MINDSET (aggressor / standard / anchor).
  • Ratings are SYNCED to the main sim: rookies start at OVR 60 (below the pro
    floor), max 99. Progression is deliberately expensive so reaching the 90s
    is a long grind.
A career is GLOBAL (one identity per Discord user across every server).
"""
import time
import random
import datetime
from threading import Thread

from core.subscription_manager import _get_db

CAREER_CACHE = {}   # user_id(str) -> career dict

# Four upgradeable attributes (pace/spin merged into one "bowling" stat - your
# chosen bowling type decides HOW you bowl, this is HOW WELL).
ATTRS = ("power", "control", "bowling", "stamina")

# Bowling type -> sim engine role (all All-Rounder_* since everyone bats+bowls)
BOWLING_TYPES = {
    "pace":    {"label": "Express Pace", "emoji": "🔥", "engine_role": "All-Rounder_Pace"},
    "offspin": {"label": "Off-Spin",     "emoji": "🌀", "engine_role": "All-Rounder_Spin_Off"},
    "legspin": {"label": "Leg-Spin",     "emoji": "🪀", "engine_role": "All-Rounder_Spin_Leg"},
}

# Batting mindset -> sim engine archetype
MINDSETS = {
    "aggressor": {"label": "Aggressor", "emoji": "💥", "engine_arch": "Aggressor",
                  "desc": "Attacking intent, high strike rate, clears the ropes."},
    "standard":  {"label": "Standard",  "emoji": "⚖️", "engine_arch": "Standard",
                  "desc": "Balanced — rotates strike, punishes the bad ball."},
    "anchor":    {"label": "Anchor",    "emoji": "🧱", "engine_arch": "Anchor",
                  "desc": "Low-risk accumulator who builds long innings."},
}

# Rookie starts at a clean OVR 60 (normalised exactly at creation) - a raw prospect
# below the sim's pro floor, with a long climb through the tiers to the 90s legends.
BASE_ATTRS = {"power": 58, "control": 62, "bowling": 56, "stamina": 60}
BASE_OVR = 60

# Tiers spread across the full 60->99 career range.
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


# Progression economics (deliberately HARD - tiers must be earned)
# Cost ramps steeply so Gold is a multi-week goal, Platinum months, and Diamond
# a long-term grind that even premium players can't rush.
def upgrade_cost(v: int) -> int:
    """Coin cost to raise an attribute from v to v+1 - EXPONENTIAL: every point is
    dearer than the last, so the higher tiers get punishingly hard (you can't sprint
    to 95). Each +1 costs ~16% more than the one before:
      v60≈28  v68≈92  v77≈365  v85≈1146  v90≈2403  v95≈5045  v98≈7876  v99≈9136."""
    return int(round(28 * (1.16 ** (max(0, v - 60)))))


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


# Persistence
def load_careers():
    try:
        col = _get_db()["careers"]
        CAREER_CACHE.clear()
        for doc in col.find({}):
            CAREER_CACHE[doc["_id"]] = doc
        print(f"Loaded {len(CAREER_CACHE)} career(s) from MongoDB!")
    except Exception as e:
        print(f"Career load error: {e}")


def all_careers():
    """Return every career doc (loads the full collection from Mongo into cache)."""
    try:
        docs = list(_get_db()["careers"].find({}))
        for d in docs:
            CAREER_CACHE[d["_id"]] = d
        return docs
    except Exception as e:
        print(f"all_careers error: {e}")
        return list(CAREER_CACHE.values())


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
        print(f"get_career error: {e}")
    return None


def save_career(career: dict):
    try:
        _get_db()["careers"].replace_one({"_id": career["_id"]}, career, upsert=True)
        return True
    except Exception as e:
        print(f"save_career error: {e}")
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
        print(f"delete_career error: {e}")
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


# Economy / progression actions
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


DAILY_MIN, DAILY_MAX = 25, 55      # tightened - dailies alone are a slow trickle, not a fast-track
STREAK_BONUS_PER_DAY = 3           # +3 coins per consecutive day beyond the first...
STREAK_BONUS_CAP_DAYS = 10         # ...capped at +30/day so streaks reward habit, not wealth
STREAK_GRACE = 48 * 3600           # claim within 48h of the last one to keep the streak
RENAME_COST = 250                  # cv rename - cosmetic, so priced as a luxury
WEEKLY_AMOUNT  = 800               # PREMIUM ONLY
MONTHLY_AMOUNT = 3000              # PREMIUM ONLY
WEEK_BOOST = 1.05                  # 5% coin boost for the week (premium weekly perk)


def _boost_mult(career: dict) -> float:
    return WEEK_BOOST if career.get("week_boost_until", 0) > int(time.time()) else 1.0


def career_is_premium(career: dict) -> bool:
    """True if this career has an active bot-granted premium pass (weekly/monthly access)."""
    return bool(career) and career.get("premium_until", 0) > int(time.time())


def grant_premium(career: dict, days: int):
    """Grant (or extend) a premium pass by `days` (0 = revoke). Returns the new expiry ts."""
    now = int(time.time())
    if days <= 0:
        career["premium_until"] = 0
    else:
        base = max(now, career.get("premium_until", 0))   # stack onto remaining time
        career["premium_until"] = base + days * 86400
    async_save_career(career)
    return career["premium_until"]


def premium_remaining(career: dict) -> int:
    return max(0, career.get("premium_until", 0) - int(time.time()))


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
    """Returns (amount, error). 24h cooldown. Consecutive-day claims build a streak
    (kept if you claim within 48h of the last): +3 coins per extra day, capped +30."""
    now, err = _claim(career, "daily", 86400, 0)
    if err:
        return 0, err
    streak = career.get("daily_streak", {"count": 0, "last": 0})
    if now - streak.get("last", 0) <= STREAK_GRACE:
        streak["count"] = streak.get("count", 0) + 1
    else:
        streak["count"] = 1
    streak["last"] = now
    career["daily_streak"] = streak
    bonus = min(max(0, streak["count"] - 1), STREAK_BONUS_CAP_DAYS) * STREAK_BONUS_PER_DAY
    amount = int(round((random.randint(DAILY_MIN, DAILY_MAX) + bonus) * _boost_mult(career)))
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
    """Match payout - PvP/club matches ONLY. AI matches earn ZERO coins (they exist
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


# Daily quests
# A pool of 25 quests; each player is dealt 3 RANDOM ones per day (deterministic by
# player+date). Progress is tracked all day; rewards are CLAIMED via `cv quests`.
QUEST_POOL = [
    {"id": "play1",  "desc": "Play 1 match or scenario",  "metric": "matches",  "target": 1,   "reward": 15},
    {"id": "play2",  "desc": "Play 2 matches/scenarios",  "metric": "matches",  "target": 2,   "reward": 25},
    {"id": "play3",  "desc": "Play 3 matches/scenarios",  "metric": "matches",  "target": 3,   "reward": 40},
    {"id": "play5",  "desc": "Play 5 matches/scenarios",  "metric": "matches",  "target": 5,   "reward": 70},
    {"id": "runs20", "desc": "Score 20 runs today",       "metric": "runs",     "target": 20,  "reward": 20},
    {"id": "runs30", "desc": "Score 30 runs today",       "metric": "runs",     "target": 30,  "reward": 28},
    {"id": "runs50", "desc": "Score 50 runs today",       "metric": "runs",     "target": 50,  "reward": 42},
    {"id": "runs75", "desc": "Score 75 runs today",       "metric": "runs",     "target": 75,  "reward": 60},
    {"id": "runs100","desc": "Score 100 runs today",      "metric": "runs",     "target": 100, "reward": 80},
    {"id": "runs150","desc": "Score 150 runs today",      "metric": "runs",     "target": 150, "reward": 120},
    {"id": "wkt1",   "desc": "Take 1 wicket today",       "metric": "wickets",  "target": 1,   "reward": 20},
    {"id": "wkt2",   "desc": "Take 2 wickets today",      "metric": "wickets",  "target": 2,   "reward": 34},
    {"id": "wkt3",   "desc": "Take 3 wickets today",      "metric": "wickets",  "target": 3,   "reward": 48},
    {"id": "wkt5",   "desc": "Take 5 wickets today",      "metric": "wickets",  "target": 5,   "reward": 85},
    {"id": "win1",   "desc": "Win a club match",          "metric": "wins",     "target": 1,   "reward": 50},
    {"id": "win2",   "desc": "Win 2 club matches",        "metric": "wins",     "target": 2,   "reward": 100},
    {"id": "four3",  "desc": "Hit 3 fours today",         "metric": "fours",    "target": 3,   "reward": 25},
    {"id": "four6",  "desc": "Hit 6 fours today",         "metric": "fours",    "target": 6,   "reward": 45},
    {"id": "four10", "desc": "Hit 10 fours today",        "metric": "fours",    "target": 10,  "reward": 70},
    {"id": "six1",   "desc": "Hit a six today",           "metric": "sixes",    "target": 1,   "reward": 25},
    {"id": "six3",   "desc": "Hit 3 sixes today",         "metric": "sixes",    "target": 3,   "reward": 55},
    {"id": "fifty1", "desc": "Score a fifty",             "metric": "fifties",  "target": 1,   "reward": 60},
    {"id": "scen2",  "desc": "Complete 2 scenarios",      "metric": "scenarios","target": 2,   "reward": 25},
    {"id": "scen4",  "desc": "Complete 4 scenarios",      "metric": "scenarios","target": 4,   "reward": 50},
    {"id": "daily",  "desc": "Claim your daily reward",   "metric": "daily",    "target": 1,   "reward": 15},
]
QUEST_BY_ID = {q["id"]: q for q in QUEST_POOL}
QUESTS_PER_DAY = 3


def _daily_quest_ids(career_id):
    rng = random.Random(f"{career_id}:{get_today_str()}")
    return rng.sample([q["id"] for q in QUEST_POOL], QUESTS_PER_DAY)


def _ensure_quests(career):
    q = career.get("quests")
    if not isinstance(q, dict) or q.get("date") != get_today_str():
        q = {"date": get_today_str(), "ids": _daily_quest_ids(career.get("_id", "?")),
             "progress": {}, "claimed": []}
        career["quests"] = q
    if not q.get("ids"):
        q["ids"] = _daily_quest_ids(career.get("_id", "?"))
    return q


def _active_quests(career):
    q = _ensure_quests(career)
    return [QUEST_BY_ID[i] for i in q["ids"] if i in QUEST_BY_ID]


def quest_progress(career, metric, amount=1):
    """Track progress toward today's 3 quests (does not pay - claim via claim_quests)."""
    if amount <= 0:
        return
    q = _ensure_quests(career)
    q["progress"][metric] = q["progress"].get(metric, 0) + amount


def claim_quests(career):
    """Pay out every completed-but-unclaimed active quest. Returns the claimed list."""
    q = _ensure_quests(career)
    claimed = []
    for quest in _active_quests(career):
        if quest["id"] in q["claimed"]:
            continue
        if q["progress"].get(quest["metric"], 0) >= quest["target"]:
            q["claimed"].append(quest["id"])
            career["coins"] += quest["reward"]
            claimed.append(quest)
    if claimed:
        async_save_career(career)
    return claimed


def quest_status(career):
    """Return [(quest, current, claimed, ready)] for the 3 active quests."""
    q = _ensure_quests(career)
    out = []
    for quest in _active_quests(career):
        prog = q["progress"].get(quest["metric"], 0)
        claimed = quest["id"] in q["claimed"]
        out.append((quest, min(prog, quest["target"]), claimed,
                    (not claimed) and prog >= quest["target"]))
    return out


# Solo scenarios (interactive challenges; small coins, separate stats)
SCENARIO_DAILY_CAP = 6   # paid scenarios per day; extras still feed quests, pay 0
SCENARIO_ENTRY_FEE = 10  # coins to enter a scenario (skill bet - beat it to profit)

# Difficulty tiers. `rlo`/`rhi` = how many rating points ABOVE the player the Challenge XI
# is (Easy = same rating as you). `mult` scales the performance coins; `pass_bonus` is the
# clear bonus - Easy's clear bonus is deliberately > the entry fee so beating Easy profits.
SCENARIO_DIFFS = {
    "easy":   {"label": "Easy",   "rlo": 0, "rhi": 0, "mult": 0.8, "pass_bonus": 14},
    "medium": {"label": "Medium", "rlo": 4, "rhi": 6, "mult": 1.0, "pass_bonus": 22},
    "hard":   {"label": "Hard",   "rlo": 7, "rhi": 9, "mult": 1.7, "pass_bonus": 40},
}


def scenarios_done_today(career):
    return _ensure_quests(career)["progress"].get("scenarios", 0)


def scenario_complete(career, runs=0, fours=0, sixes=0, wickets=0, passed=False, mode="bat", difficulty="medium"):
    """Settle a finished interactive scenario (batting OR bowling). Reward is tied to
    PERFORMANCE and DIFFICULTY - no flat freebie, so a poor loss pays ~0 and you forfeit
    the entry fee. Stats are SEPARATE from cv stats; quests are fed. Returns (coins, capped, left)."""
    done = scenarios_done_today(career)
    capped = done >= SCENARIO_DAILY_CAP
    d = SCENARIO_DIFFS.get(difficulty, SCENARIO_DIFFS["medium"])
    perf = (wickets * 6) if mode == "bowl" else (runs // 5)
    raw  = perf * d["mult"] + (d["pass_bonus"] if passed else 0)
    coins = 0 if capped else max(0, int(round(raw)))
    # A LOSS must never be net-profitable. Performance coins are only a partial consolation
    # on a defeat - capped below the entry fee so you always forfeit something (otherwise a
    # couple of wickets in a losing bowling scenario would out-earn the fee). Clearing the
    # scenario (passed) is the only path to profit, via the pass_bonus.
    if coins and not passed:
        coins = min(coins, SCENARIO_ENTRY_FEE - 1)
    if coins:
        career["coins"] += coins

    # Separate practice stats - NOT part of the real career stats shown in `cv stats`.
    ss = career.setdefault("scenario_stats", {"played": 0, "runs": 0, "best": 0, "passed": 0, "wickets": 0})
    ss["played"] += 1
    ss["runs"] += runs
    ss["best"] = max(ss.get("best", 0), runs)
    ss["wickets"] = ss.get("wickets", 0) + wickets
    if passed:
        ss["passed"] += 1

    # Quest progress (scenarios count toward quests, just not lifetime stats).
    quest_progress(career, "scenarios", 1)
    quest_progress(career, "matches", 1)
    if runs:       quest_progress(career, "runs", runs)
    if fours:      quest_progress(career, "fours", fours)
    if sixes:      quest_progress(career, "sixes", sixes)
    if runs >= 50: quest_progress(career, "fifties", 1)
    if wickets:    quest_progress(career, "wickets", wickets)
    async_save_career(career)
    return coins, capped, max(0, SCENARIO_DAILY_CAP - done - 1)
