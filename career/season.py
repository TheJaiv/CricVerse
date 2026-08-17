"""
The career storyline: an Indian cricketer's pathway.

This is a SELECTION LADDER, not a transfer market. You do not shop for a club and
negotiate a wage - you play the tournament you are currently in, and selectors
move you up or drop you back down on the strength of your season:

    Club Cricket -> Cooch Behar Trophy (U19) -> Syed Mushtaq Ali Trophy ->
    Vijay Hazare Trophy -> Ranji Trophy -> Duleep Trophy -> IPL ->
    India A -> India T20I -> India ODI -> Test -> World Cup

The ladder itself lives in data/career_ladder.json: real tournaments, their real
formats, the match fee each pays, and the reputation a selector wants before
picking you.

Two things carry across a season:
  * REPUTATION (`story.rating`) - what selectors judge. Earned by playing well,
    lost by failing or by ageing out. Nothing to do with your career OVR.
  * COINS - match fees, central-contract retainers and IPL money, which you spend
    upgrading the real career.

THE STORYLINE NEVER TOUCHES YOUR REAL RATINGS. It cannot read or write your
attributes, OVR or tier. Being dropped costs you standing, never the progress you
paid coins for.
"""
import json
import os
import random
import time

_DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "data", "career_ladder.json")

HISTORY_CAP = 100
START_AGE = 17
PEAK_AGE = 28
DECLINE_AGE = 33
RETIREMENT_AGE = 38

STORY_START_RATING = 55
STORY_MIN, STORY_MAX = 40, 99

_LADDER = None


def _load():
    global _LADDER
    if _LADDER is None:
        try:
            with open(_DATA, "r", encoding="utf-8") as fh:
                _LADDER = json.load(fh)
        except Exception as e:
            print(f"career ladder data missing ({e}); falling back to club cricket only.")
            _LADDER = {"levels": [{
                "id": "club", "name": "Club Cricket", "tournament": "Local League",
                "format": "T20", "overs": 5, "per_side": 4, "fixtures": 8,
                "match_fee": 35, "min_rep": 0, "promote_grade": 52, "relegate_grade": -1,
                "squad": "Club XI", "opponents": ["Rivals CC"]}], "contract_grades": {}}
    return _LADDER


def levels():
    return _load()["levels"]


def level_by_id(lid):
    return next((l for l in levels() if l["id"] == lid), None)


def level_index(lid):
    for i, l in enumerate(levels()):
        if l["id"] == lid:
            return i
    return 0


def contract_grades():
    return _load().get("contract_grades", {})


def ensure(career):
    """Add the storyline fields to a career that predates them."""
    career.setdefault("season_no", 1)
    career.setdefault("retired", False)
    career.setdefault("trophies", [])
    career.setdefault("history", [])
    if not isinstance(career.get("season_stats"), dict):
        career["season_stats"] = blank_season()
    if not isinstance(career.get("story"), dict):
        career["story"] = {}
    st = career["story"]
    st.setdefault("rating", STORY_START_RATING)
    st.setdefault("peak", st["rating"])
    st.setdefault("age", START_AGE)
    st.setdefault("level", levels()[0]["id"])
    st.setdefault("squad", levels()[0].get("squad", ""))
    st.setdefault("peak_level", st["level"])
    # Old club-contract shape from the earlier design - not used any more.
    career.pop("offers", None)
    return career


def blank_season():
    return {"played": 0, "won": 0, "runs": 0, "balls": 0, "wickets": 0,
            "fifties": 0, "hundreds": 0, "hs": 0, "wages": 0, "best_w": 0}


def story_rating(career):
    """Your standing with selectors. NOT your career OVR."""
    ensure(career)
    return int(round(career["story"]["rating"]))


def story_age(career):
    ensure(career)
    return int(career["story"]["age"])


def current_level(career):
    ensure(career)
    return level_by_id(career["story"]["level"]) or levels()[0]


def squad_name(career):
    ensure(career)
    return career["story"].get("squad") or current_level(career).get("squad", "")


def _set_rating(career, value):
    st = career["story"]
    st["rating"] = max(STORY_MIN, min(STORY_MAX, round(value, 1)))
    st["peak"] = max(st.get("peak", st["rating"]), st["rating"])
    return st["rating"]


def contract_retainer(career):
    """Annual retainer for a centrally contracted player, 0 below that level."""
    lvl = current_level(career)
    grade = lvl.get("contract_grade")
    return contract_grades().get(grade, 0) if grade else 0


# Fixtures
def fixtures(career):
    """This season's schedule for the tournament you are currently in.

    Deterministic per player, season and level, so the list does not reshuffle
    between commands.
    """
    ensure(career)
    lvl = current_level(career)
    season = career.get("season_no", 1)
    rng = random.Random(f"{career.get('_id', '?')}:{lvl['id']}:s{season}")
    base = max(45, story_rating(career) - 4 + level_index(lvl["id"]))

    opponents = list(lvl.get("opponents") or ["Rivals"])
    rng.shuffle(opponents)
    sched = []
    for i in range(int(lvl.get("fixtures", 8))):
        sched.append({
            "round": i + 1,
            "opponent": opponents[i % len(opponents)],
            "strength": max(45, min(96, int(base + rng.randint(-6, 10)))),
            "home": i % 2 == 0,
            "level": lvl["id"],
            "tournament": lvl["tournament"],
            "format": lvl.get("format", "T20"),
            "overs": int(lvl.get("overs", 5)),
            "per_side": int(lvl.get("per_side", 4)),
            "fee": int(lvl.get("match_fee", 35)),
        })
    return sched


def next_fixture(career):
    ensure(career)
    played = career["season_stats"].get("played", 0)
    sched = fixtures(career)
    return sched[played] if played < len(sched) else None


def season_length(career):
    return len(fixtures(career))


# Match results
def match_fee(career):
    """Fee for one appearance at the current level, plus a slice of any retainer.

    A centrally contracted player is paid a retainer across the season rather than
    per game, so it is spread over the fixtures instead of landing in one lump.
    """
    lvl = current_level(career)
    fee = int(lvl.get("match_fee", 35))
    retainer = contract_retainer(career)
    if retainer:
        fee += int(retainer / max(1, int(lvl.get("fixtures", 8))))
    return fee


def record_match(career, *, runs=0, balls=0, wickets=0, balls_bowled=0, won=False,
                 opponent="", fifties=0, hundreds=0, coins=0, when=None):
    """Fold a finished appearance into the season, the history and the fee."""
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

    lvl = current_level(career)
    entry = {
        "s": career.get("season_no", 1), "t": int(when or time.time()),
        "o": str(opponent)[:24], "r": runs, "b": balls, "w": wickets, "bb": balls_bowled,
        "won": 1 if won else 0, "c": coins,
        "lvl": lvl["id"], "tour": lvl["tournament"],
    }
    hist = career.setdefault("history", [])
    hist.append(entry)
    if len(hist) > HISTORY_CAP:
        del hist[:len(hist) - HISTORY_CAP]

    fee = match_fee(career)
    career["coins"] = career.get("coins", 0) + fee
    ss["wages"] = ss.get("wages", 0) + fee

    out = {"fee": fee, "season_done": False, "awards": [], "level": lvl}
    if ss["played"] >= season_length(career):
        out.update(end_season(career))
    return out


# Season rollover and selection
def _season_grade(career):
    """0..100 rating of the season just played. This is what selectors read."""
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
    lvl = current_level(career)
    season = career.get("season_no", 1)
    if grade >= 62:
        awards.append(f"{lvl['tournament']} Player of the Tournament")
    if ss.get("runs", 0) >= 350:
        awards.append(f"{lvl['tournament']} Leading Run-Scorer")
    if ss.get("wickets", 0) >= 18:
        awards.append(f"{lvl['tournament']} Leading Wicket-Taker")
    if ss.get("hundreds", 0) >= 2:
        awards.append("Century Maker")
    for a in awards:
        career.setdefault("trophies", []).append({"name": a, "season": season})
    return awards


def _apply_progression(career, grade):
    """A season's showing moves your standing with selectors."""
    delta = max(-4.0, min(5.0, (grade - 45.0) / 10.0))
    return _set_rating(career, career["story"]["rating"] + delta)


def _apply_ageing(career):
    """Past the decline age your standing erodes - selectors look at younger men.

    Explicitly NOT your attributes: the storyline can end your career without ever
    taking away what you bought with coins.
    """
    age = story_age(career)
    if age < DECLINE_AGE:
        return 0
    steps = 1 + (age - DECLINE_AGE) // 3
    before = career["story"]["rating"]
    _set_rating(career, before - steps)
    return round(before - career["story"]["rating"], 1)


def selection_verdict(career, grade):
    """Do the selectors promote you, keep you, or drop you?

    Promotion needs BOTH a good enough season at this level AND the reputation the
    next tournament expects - a great Ranji season does not put you in the Test
    side if nobody rates you yet.
    """
    ensure(career)
    lvl = current_level(career)
    idx = level_index(lvl["id"])
    rep = story_rating(career)

    if grade <= lvl.get("relegate_grade", -1) and idx > 0:
        return "dropped", levels()[idx - 1]
    if idx + 1 < len(levels()):
        nxt = levels()[idx + 1]
        if grade >= lvl.get("promote_grade", 999) and rep >= nxt.get("min_rep", 0):
            return "promoted", nxt
        if grade >= lvl.get("promote_grade", 999):
            return "knocking", nxt          # good enough, not yet rated enough
    return "retained", lvl


def end_season(career):
    """Close the season: grade it, award it, age the player, then selection."""
    ensure(career)
    grade = _season_grade(career)
    awards = _season_awards(career, grade)
    season = career.get("season_no", 1)
    lvl = current_level(career)

    career.setdefault("season_log", []).append({
        "season": season, "grade": round(grade, 1),
        "level": lvl["id"], "tournament": lvl["tournament"],
        **{k: career["season_stats"].get(k, 0)
           for k in ("played", "won", "runs", "wickets", "hs", "wages")},
    })

    career["season_no"] = season + 1
    career["season_stats"] = blank_season()
    career["story"]["age"] = story_age(career) + 1

    before_rep = story_rating(career)
    _apply_progression(career, grade)
    decline = _apply_ageing(career)

    verdict, target = selection_verdict(career, grade)
    if verdict in ("promoted", "dropped"):
        career["story"]["level"] = target["id"]
        career["story"]["squad"] = target.get("squad", "")
        if verdict == "promoted":
            career["story"]["peak_level"] = target["id"]
            career.setdefault("trophies", []).append(
                {"name": f"Selected: {target['tournament']}", "season": season})

    return {"season_done": True, "season": season, "grade": round(grade, 1),
            "awards": awards, "decline": decline,
            "verdict": verdict, "from_level": lvl, "to_level": target,
            "rating_before": before_rep, "rating_after": story_rating(career),
            "retire_due": story_age(career) >= RETIREMENT_AGE}


def retire(career):
    ensure(career)
    career["retired"] = True
    career["retired_at"] = int(time.time())
    return legacy(career)


def legacy(career):
    """Career-long summary for the retirement card."""
    ensure(career)
    bat = (career.get("stats") or {}).get("bat", {})
    bowl = (career.get("stats") or {}).get("bowl", {})
    outs = bat.get("outs", 0)
    peak_lvl = level_by_id(career["story"].get("peak_level")) or current_level(career)
    return {
        "name": career.get("username", ""),
        "seasons": max(0, career.get("season_no", 1) - 1),
        "age": story_age(career),
        "peak_rating": career["story"].get("peak", STORY_START_RATING),
        "story_rating": story_rating(career),
        "peak_level": peak_lvl["name"],
        "peak_tournament": peak_lvl["tournament"],
        "ovr": career.get("ovr", 60),
        "tier": career.get("tier", "Bronze"),
        "matches": bat.get("matches", 0),
        "runs": bat.get("runs", 0),
        "hs": bat.get("hs", 0),
        "avg": (bat.get("runs", 0) / outs) if outs else None,
        "wickets": bowl.get("wickets", 0),
        "trophies": list(career.get("trophies", [])),
        "tournaments": sorted({s.get("tournament") for s in career.get("season_log", [])
                               if s.get("tournament")}),
    }


def track_peak(career):
    ensure(career)
    if career.get("ovr", 0) > career.get("peak_ovr", 0):
        career["peak_ovr"] = career["ovr"]
    return career["peak_ovr"]
