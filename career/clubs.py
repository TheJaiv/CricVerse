"""
Club cricket: the contract system.

This is the THIRD system in career mode, and it is deliberately separate from
both of the others:

  CAREER   attributes, upgrades, coins, form and fitness      (career_manager)
  CLUBS    you sign for a club, play its season, draw a wage  (this module)
  PATHWAY  selectors move you up Ranji / IPL / India          (season.py)

Real cricketers have both at once: you turn out for your club side AND you are
picked - or not - for representative cricket. So club standing and pathway
reputation are independent numbers. Playing well for your club earns you wages
and better contracts; it does not put you in the Test side, and being dropped by
India does not cost you your club place.

Like the pathway, this never touches your real attributes, OVR or tier. The only
thing that crosses back into the core career is COINS.
"""
import json
import os
import random
import time

_DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "data", "career_clubs.json")

SEASON_FIXTURES = 10
HISTORY_CAP = 60

STANDING_START = 55
STANDING_MIN, STANDING_MAX = 40, 99

FIXTURE_OVERS = 5
FIXTURE_PER_SIDE = 4

_CLUBS = None


def clubs():
    global _CLUBS
    if _CLUBS is None:
        try:
            with open(_DATA, "r", encoding="utf-8") as fh:
                _CLUBS = json.load(fh)["clubs"]
        except Exception as e:
            print(f"career clubs data missing ({e}); using a single fallback club.")
            _CLUBS = [{"id": "riverside", "name": "Riverside CC", "tier": "Bronze",
                       "min_ovr": 0, "wage": 22, "prestige": 1, "city": "Riverside"}]
    return _CLUBS


def club_by_id(cid):
    return next((c for c in clubs() if c["id"] == cid), None)


def ensure(career):
    """Add the club-career sub-document to a career that predates it.

    Kept under its own key so it can never be confused with the pathway's `story`
    or with the PvP club-match record already stored under `club`.
    """
    cc = career.get("club_career")
    if not isinstance(cc, dict):
        cc = career["club_career"] = {}
    cc.setdefault("standing", STANDING_START)
    cc.setdefault("peak", cc["standing"])
    cc.setdefault("contract", None)
    cc.setdefault("season_no", 1)
    cc.setdefault("offers", [])
    cc.setdefault("history", [])
    cc.setdefault("log", [])
    if not isinstance(cc.get("season"), dict):
        cc["season"] = blank_season()
    return career


def blank_season():
    return {"played": 0, "won": 0, "runs": 0, "balls": 0, "wickets": 0,
            "fifties": 0, "hundreds": 0, "hs": 0, "wages": 0, "best_w": 0}


def standing(career):
    """How club cricket rates you. Not your OVR, and not pathway reputation."""
    ensure(career)
    return int(round(career["club_career"]["standing"]))


def _set_standing(career, value):
    cc = career["club_career"]
    cc["standing"] = max(STANDING_MIN, min(STANDING_MAX, round(value, 1)))
    cc["peak"] = max(cc.get("peak", cc["standing"]), cc["standing"])
    return cc["standing"]


def contract(career):
    ensure(career)
    return career["club_career"].get("contract")


def current_club(career):
    c = contract(career)
    return c["club"] if c else None


def eligible_clubs(career):
    s = standing(career)
    return [c for c in clubs() if c["min_ovr"] <= s]


def sign(career, club_id, matches=SEASON_FIXTURES):
    """Sign a club contract. Returns (contract, error)."""
    ensure(career)
    club = club_by_id(club_id)
    if not club:
        return None, "No such club."
    s = standing(career)
    if s < club["min_ovr"]:
        return None, (f"**{club['name']}** want a **{club['min_ovr']}** club standing — "
                      f"yours is **{s}**. Play club cricket to build it.")
    cur = contract(career)
    if cur and cur.get("matches_left", 0) > 0 and cur.get("club_id") != club_id:
        return None, (f"You're contracted to **{cur['club']}** for "
                      f"{cur['matches_left']} more match(es).")
    cc = career["club_career"]
    cc["contract"] = {
        "club_id": club["id"], "club": club["name"], "wage": club["wage"],
        "matches_left": matches, "signed_season": cc.get("season_no", 1),
        "prestige": club.get("prestige", 1), "tier": club.get("tier", "Bronze"),
    }
    cc["offers"] = []
    return cc["contract"], None


# Fixtures
_OPPONENTS = [
    "Shivaji Park CC", "Dadar Union", "Fort Vijay CC", "Payyade SC", "Karnatak SA",
    "Islam Gymkhana", "Parsee Gymkhana", "New Hind SC", "Jolly Cricketers",
    "Sassanian CC", "Bombay Gymkhana", "Cricket Club of India",
]


def fixtures(career):
    """This club season's schedule. Deterministic per player and season."""
    ensure(career)
    cc = career["club_career"]
    season = cc.get("season_no", 1)
    club = cc.get("contract") or {}
    rng = random.Random(f"{career.get('_id', '?')}:club{season}")
    base = standing(career) + club.get("prestige", 1) * 2 - 6

    names = [n for n in _OPPONENTS if n != club.get("club")]
    rng.shuffle(names)
    return [{
        "round": i + 1,
        "opponent": names[i % len(names)],
        "strength": max(42, min(94, int(base + rng.randint(-7, 9)))),
        "home": i % 2 == 0,
        "tournament": f"{club.get('tier', 'Club')} Club League",
        "overs": FIXTURE_OVERS,
        "per_side": FIXTURE_PER_SIDE,
        "fee": int(club.get("wage", 22)),
    } for i in range(SEASON_FIXTURES)]


def next_fixture(career):
    ensure(career)
    played = career["club_career"]["season"].get("played", 0)
    sched = fixtures(career)
    return sched[played] if played < len(sched) else None


def match_fee(career):
    c = contract(career)
    return int(c.get("wage", 0)) if c else 0


# Results
def record_match(career, *, runs=0, balls=0, wickets=0, balls_bowled=0, won=False,
                 opponent="", fifties=0, hundreds=0, coins=0, when=None):
    """Fold a club appearance into the club season, pay the wage, burn a contract match."""
    ensure(career)
    cc = career["club_career"]
    ss = cc["season"]
    ss["played"] = ss.get("played", 0) + 1
    ss["won"] = ss.get("won", 0) + (1 if won else 0)
    ss["runs"] = ss.get("runs", 0) + runs
    ss["balls"] = ss.get("balls", 0) + balls
    ss["wickets"] = ss.get("wickets", 0) + wickets
    ss["fifties"] = ss.get("fifties", 0) + fifties
    ss["hundreds"] = ss.get("hundreds", 0) + hundreds
    ss["hs"] = max(ss.get("hs", 0), runs)
    ss["best_w"] = max(ss.get("best_w", 0), wickets)

    entry = {"s": cc.get("season_no", 1), "t": int(when or time.time()),
             "o": str(opponent)[:24], "r": runs, "b": balls, "w": wickets,
             "won": 1 if won else 0, "club": current_club(career) or ""}
    hist = cc.setdefault("history", [])
    hist.append(entry)
    if len(hist) > HISTORY_CAP:
        del hist[:len(hist) - HISTORY_CAP]

    wage = 0
    c = cc.get("contract")
    if c and c.get("matches_left", 0) > 0:
        wage = int(c.get("wage", 0))
        career["coins"] = career.get("coins", 0) + wage
        ss["wages"] = ss.get("wages", 0) + wage
        c["matches_left"] -= 1

    out = {"wage": wage, "season_done": False, "offers": []}
    if ss["played"] >= SEASON_FIXTURES:
        out.update(end_season(career))
    return out


def _grade(career):
    ss = career["club_career"]["season"]
    played = max(1, ss.get("played", 0))
    return max(0.0, min(100.0, ss.get("runs", 0) / played * 1.6
                        + ss.get("wickets", 0) / played * 12.0
                        + ss.get("won", 0) / played * 20.0))


def make_offers(career, grade):
    """End-of-season contract offers from clubs willing to take you."""
    ensure(career)
    cc = career["club_career"]
    s = standing(career)
    cur_prestige = (cc.get("contract") or {}).get("prestige", 0)
    reach = 2 if grade >= 70 else (1 if grade >= 50 else (-1 if grade < 30 else 0))

    offers = []
    for c in clubs():
        if c["min_ovr"] > s:
            continue
        gap = c.get("prestige", 1) - cur_prestige
        if gap > reach or gap < -2:
            continue
        offers.append({"club_id": c["id"], "club": c["name"], "tier": c["tier"],
                       "wage": int(round(c["wage"] * (0.9 + grade / 200.0))),
                       "prestige": c.get("prestige", 1), "matches": SEASON_FIXTURES})
    offers.sort(key=lambda o: (-o["prestige"], -o["wage"]))
    cc["offers"] = offers[:5]
    return cc["offers"]


def end_season(career):
    """Close the club season: grade it, move club standing, generate offers."""
    ensure(career)
    cc = career["club_career"]
    grade = _grade(career)
    season = cc.get("season_no", 1)

    cc.setdefault("log", []).append({
        "season": season, "grade": round(grade, 1),
        "club": current_club(career) or "",
        **{k: cc["season"].get(k, 0) for k in ("played", "won", "runs", "wickets", "hs", "wages")},
    })

    cc["season_no"] = season + 1
    cc["season"] = blank_season()
    before = standing(career)
    _set_standing(career, before + max(-4.0, min(5.0, (grade - 45.0) / 10.0)))
    offers = make_offers(career, grade)

    c = cc.get("contract")
    if c and c.get("matches_left", 0) <= 0:
        cc["contract"] = None

    return {"season_done": True, "season": season, "grade": round(grade, 1),
            "offers": offers, "standing_before": before, "standing_after": standing(career)}
