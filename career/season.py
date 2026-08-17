"""
Career life sim: seasons, clubs, contracts, transfers, awards, history, ageing.

Before this, a career was a bag of lifetime totals with no arc - nothing to play
FOR beyond a bigger number. This adds the shape of a playing career: you sign for
a club, play a season of fixtures for a wage, get judged on it, and either move
up the ladder or get moved down it. Eventually you get old and retire.

THE SEASON IS A SEPARATE STORYLINE. It has its OWN rating (`story.rating`), and
it never reads or writes the real career's attributes, OVR or tier. Clubs judge
you on the story rating; ageing erodes the story rating; a bad season drops it.
What crosses back over is COINS - wages and awards - which are spent upgrading
the real career through the normal upgrade curve. That way the storyline can be
as brutal as it likes without ever undoing progress the player paid for.

Design notes:
  * Everything is lazily defaulted, so career documents written before this
    existed keep loading and simply start Season 1 on their next match.
  * The season rolls over on FIXTURES PLAYED, not on a real-world date. Players
    play at wildly different rates and a calendar season would punish the casual
    ones.
  * Match history is capped: careers are one Mongo document each, which is
    exactly why they were split out of the main blob in the first place.
  * Wages are per match and deliberately modest - see the economy note in
    add_wage(). The upgrade curve is the grind that gives tiers meaning, and a
    salary big enough to short-circuit it would delete that.
"""
import json
import os
import random
import time

_DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "data", "career_clubs.json")

SEASON_FIXTURES = 14          # matches in a season before it rolls over
HISTORY_CAP = 100             # per-career match log entries kept
START_AGE = 18
PEAK_AGE = 28
DECLINE_AGE = 33
RETIREMENT_AGE = 38

_CLUBS = None


def clubs():
    """The club ladder, loaded once from data/career_clubs.json."""
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


STORY_START_RATING = 60
STORY_MIN, STORY_MAX = 40, 99


def ensure(career):
    """Add the season fields to a career that predates them."""
    career.setdefault("season_no", 1)
    career.setdefault("retired", False)
    career.setdefault("trophies", [])
    career.setdefault("history", [])
    career.setdefault("offers", [])
    if not isinstance(career.get("season_stats"), dict):
        career["season_stats"] = blank_season()
    if "contract" not in career:
        career["contract"] = None
    # The storyline's own rating and age. Kept in its own sub-document so it is
    # obvious at a glance that none of this is the real career's rating.
    if not isinstance(career.get("story"), dict):
        career["story"] = {"rating": STORY_START_RATING, "peak": STORY_START_RATING,
                           "age": START_AGE}
    st = career["story"]
    st.setdefault("rating", STORY_START_RATING)
    st.setdefault("peak", st["rating"])
    st.setdefault("age", START_AGE)
    return career


def story_rating(career):
    """The rating clubs judge you on. NOT the career OVR."""
    ensure(career)
    return int(round(career["story"]["rating"]))


def story_age(career):
    ensure(career)
    return int(career["story"]["age"])


def _set_story_rating(career, value):
    st = career["story"]
    st["rating"] = max(STORY_MIN, min(STORY_MAX, round(value, 1)))
    st["peak"] = max(st.get("peak", st["rating"]), st["rating"])
    return st["rating"]


def blank_season():
    return {"played": 0, "won": 0, "runs": 0, "balls": 0, "wickets": 0,
            "fifties": 0, "hundreds": 0, "hs": 0, "wages": 0, "best_w": 0}


# Contracts
def eligible_clubs(career):
    r = story_rating(career)
    return [c for c in clubs() if c["min_ovr"] <= r]


def sign(career, club_id, matches=SEASON_FIXTURES):
    """Sign for a club. Returns (contract, error)."""
    ensure(career)
    club = club_by_id(club_id)
    if not club:
        return None, "No such club."
    rating = story_rating(career)
    if rating < club["min_ovr"]:
        return None, (f"**{club['name']}** want a **{club['min_ovr']}** reputation — "
                      f"yours is **{rating}**. Play seasons to build it.")
    cur = career.get("contract")
    if cur and cur.get("matches_left", 0) > 0 and cur.get("club_id") != club_id:
        return None, (f"You're under contract at **{cur['club']}** for "
                      f"{cur['matches_left']} more match(es).")
    career["contract"] = {
        "club_id": club["id"], "club": club["name"], "wage": club["wage"],
        "matches_left": matches, "signed_season": career.get("season_no", 1),
        "prestige": club.get("prestige", 1),
    }
    career["offers"] = []
    return career["contract"], None


def current_club(career):
    ensure(career)
    c = career.get("contract")
    return c["club"] if c else None


def add_wage(career):
    """Pay the match wage and burn one contract match.

    Wages are the season's steady income; match performance pay and the daily
    remain the variable part. Kept per-match and modest on purpose: a full
    Bronze season is worth a few hundred coins against an upgrade curve where a
    single point at OVR 90 costs ~2,400. Wages should make a season feel like a
    job, not shortcut the grind.
    """
    ensure(career)
    c = career.get("contract")
    if not c or c.get("matches_left", 0) <= 0:
        return 0
    wage = int(c.get("wage", 0))
    career["coins"] = career.get("coins", 0) + wage
    c["matches_left"] -= 1
    career["season_stats"]["wages"] = career["season_stats"].get("wages", 0) + wage
    if c["matches_left"] <= 0:
        c["expired"] = True
    return wage


# Match results
def record_match(career, *, runs=0, balls=0, wickets=0, balls_bowled=0, won=False,
                 opponent="", fifties=0, hundreds=0, coins=0, when=None):
    """Fold a finished match into the season, the history and the wage.

    Returns a dict describing anything the player should be told about
    (wage paid, season completed, awards won).
    """
    ensure(career)
    ss = career["season_stats"]
    ss["played"] = ss.get("played", 0) + 1
    ss["won"] = ss.get("won", 0) + (1 if won else 0)
    ss["runs"] = ss.get("runs", 0) + runs
    ss["balls"] = ss.get("balls", 0) + balls
    ss["wickets"] = ss.get("wickets", 0) + wickets
    ss["fifties"] = ss.get("fifties", 0) + fifties
    ss["hundreds"] = ss.get("hundreds", 0) + hundreds
    ss["hs"] = max(ss.get("hs", 0), runs)
    ss["best_w"] = max(ss.get("best_w", 0), wickets)

    entry = {
        "s": career.get("season_no", 1),
        "t": int(when or time.time()),
        "o": str(opponent)[:24],
        "r": runs, "b": balls, "w": wickets, "bb": balls_bowled,
        "won": 1 if won else 0, "c": coins,
        "club": (career.get("contract") or {}).get("club", ""),
    }
    hist = career.setdefault("history", [])
    hist.append(entry)
    if len(hist) > HISTORY_CAP:
        del hist[:len(hist) - HISTORY_CAP]

    out = {"wage": add_wage(career), "season_done": False, "awards": [], "offers": []}
    if ss["played"] >= SEASON_FIXTURES:
        out.update(end_season(career))
    return out


# Season rollover
def _season_grade(career):
    """0..100 rating of the season just played, used for awards and offers."""
    ss = career["season_stats"]
    played = max(1, ss.get("played", 0))
    runs_pm = ss.get("runs", 0) / played
    wkts_pm = ss.get("wickets", 0) / played
    win_rate = ss.get("won", 0) / played
    grade = runs_pm * 1.6 + wkts_pm * 12.0 + win_rate * 20.0
    return max(0.0, min(100.0, grade))


def _season_awards(career, grade):
    awards = []
    ss = career["season_stats"]
    season = career.get("season_no", 1)
    if grade >= 62:
        awards.append("Player of the Season")
    if ss.get("runs", 0) >= 400:
        awards.append("Leading Run-Scorer")
    if ss.get("wickets", 0) >= 20:
        awards.append("Leading Wicket-Taker")
    if ss.get("hundreds", 0) >= 2:
        awards.append("Century Maker")
    if season == 1 and grade >= 45:
        awards.append("Breakthrough Player")
    for a in awards:
        career.setdefault("trophies", []).append({"name": a, "season": season})
    return awards


def make_offers(career, grade):
    """Generate end-of-season contract offers.

    A strong season opens clubs one rung above; a poor one at a big club gets you
    pushed back down the ladder. Judged on the STORY rating, not the career OVR.
    """
    ensure(career)
    ovr = story_rating(career)
    cur = career.get("contract") or {}
    cur_prestige = cur.get("prestige", 0)

    reach = 0
    if grade >= 70:
        reach = 2
    elif grade >= 50:
        reach = 1
    elif grade < 30:
        reach = -1

    offers = []
    for c in clubs():
        if c["min_ovr"] > ovr:
            continue
        gap = c.get("prestige", 1) - cur_prestige
        if gap > reach:
            continue
        if gap < -2:
            continue
        wage = int(round(c["wage"] * (0.9 + grade / 200.0)))
        offers.append({"club_id": c["id"], "club": c["name"], "tier": c["tier"],
                       "wage": wage, "prestige": c.get("prestige", 1),
                       "matches": SEASON_FIXTURES})
    offers.sort(key=lambda o: (-o["prestige"], -o["wage"]))
    offers = offers[:5]
    career["offers"] = offers
    return offers


def end_season(career):
    """Close the season: grade it, hand out awards, age the player, make offers."""
    ensure(career)
    grade = _season_grade(career)
    awards = _season_awards(career, grade)
    season = career.get("season_no", 1)

    career.setdefault("season_log", []).append({
        "season": season, "grade": round(grade, 1),
        "club": (career.get("contract") or {}).get("club", ""),
        **{k: career["season_stats"].get(k, 0)
           for k in ("played", "won", "runs", "wickets", "hs", "wages")},
    })

    career["season_no"] = season + 1
    career["season_stats"] = blank_season()
    career["story"]["age"] = story_age(career) + 1

    before = story_rating(career)
    _apply_progression(career, grade)
    decline = _apply_ageing(career)
    after = story_rating(career)
    offers = make_offers(career, grade)

    contract = career.get("contract")
    if contract and contract.get("matches_left", 0) <= 0:
        career["contract"] = None

    return {"season_done": True, "season": season, "grade": round(grade, 1),
            "awards": awards, "offers": offers, "decline": decline,
            "rating_before": before, "rating_after": after,
            "retire_due": story_age(career) >= RETIREMENT_AGE}


def _apply_progression(career, grade):
    """A season's showing moves the STORY rating up or down.

    This is the storyline's own ladder: play well for a mid-table club and better
    clubs come calling; have a bad season and you slide back down. It touches
    nothing the player bought with coins.
    """
    delta = (grade - 45.0) / 10.0          # ~ -4.5 .. +5.5 a season
    delta = max(-4.0, min(5.0, delta))
    return _set_story_rating(career, career["story"]["rating"] + delta)


def _apply_ageing(career):
    """Past the decline age the STORY rating erodes each season.

    Explicitly NOT the career's attributes: the real career is what the player
    paid coins for and must never be taken away by the storyline. Ageing is a
    story about this club career ending, not about losing your progress.
    """
    age = story_age(career)
    if age < DECLINE_AGE:
        return 0
    steps = 1 + (age - DECLINE_AGE) // 3
    before = career["story"]["rating"]
    _set_story_rating(career, before - steps)
    return round(before - career["story"]["rating"], 1)


def retire(career):
    """End the career. Everything is kept - the document becomes a record."""
    ensure(career)
    career["retired"] = True
    career["retired_at"] = int(time.time())
    career["contract"] = None
    career["offers"] = []
    return legacy(career)


def legacy(career):
    """Career-long summary for the retirement card and the hall of fame."""
    ensure(career)
    bat = (career.get("stats") or {}).get("bat", {})
    bowl = (career.get("stats") or {}).get("bowl", {})
    outs = bat.get("outs", 0)
    return {
        "name": career.get("username", ""),
        "seasons": max(0, career.get("season_no", 1) - 1),
        "age": story_age(career),
        "peak_rating": career["story"].get("peak", STORY_START_RATING),
        "story_rating": story_rating(career),
        "peak_ovr": career.get("peak_ovr", career.get("ovr", 60)),
        "ovr": career.get("ovr", 60),
        "tier": career.get("tier", "Bronze"),
        "matches": bat.get("matches", 0),
        "runs": bat.get("runs", 0),
        "hs": bat.get("hs", 0),
        "avg": (bat.get("runs", 0) / outs) if outs else None,
        "wickets": bowl.get("wickets", 0),
        "trophies": list(career.get("trophies", [])),
        "clubs": sorted({s.get("club") for s in career.get("season_log", []) if s.get("club")}),
    }


def track_peak(career):
    """Remember the highest OVR ever reached, for the legacy card."""
    ensure(career)
    if career.get("ovr", 0) > career.get("peak_ovr", 0):
        career["peak_ovr"] = career["ovr"]
    return career["peak_ovr"]
