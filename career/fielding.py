"""
Career Mode fielding: catches and run-outs you actually take part in.

The engine decides that a batter is caught or run out. It has never modelled WHO
fielded it or whether the chance was held - which is why every career's catches
and run-outs sat permanently at zero. This module puts the player in that moment:
the assigned fielder gets a prompt, and their fielding attributes plus the choice
they make decide whether the chance sticks.

Rules (as specified):
  * Three fielders are assigned per side at the start of a match.
  * Catching is a toggle, set at match start.
  * It is FORCED OFF when a side has fewer than three players - with two people
    there is nobody to assign, and prompting the batter to catch himself is
    nonsense.

The engine is not touched. A dropped catch is unwound with the same rewind DRS
uses (career/drs.undo_wicket), so there is one implementation of "take that
wicket back" rather than two that must be kept in step.
"""
import random

from career import attributes as _AT

MIN_PLAYERS_FOR_FIELDING = 3
FIELDERS_PER_SIDE = 3

# How hard the chance is, by how the ball got there. 1.0 is a regulation catch.
CHANCE_DIFFICULTY = {
    "Loft": 0.72, "Scoop": 0.55, "Pull": 0.68, "Hook": 0.58,
    "Drive": 0.80, "Cut": 0.78, "Sweep": 0.70, "Flick": 0.82,
    "Block": 0.95, "Defensive": 0.95, "Leave": 1.0,
}

# What the fielder chooses to do. Aggressive options catch more and drop more.
CATCH_ACTIONS = {
    "dive":   {"label": "Dive",        "emoji": "🤸", "reach": 0.22, "risk": 0.16},
    "steady": {"label": "Steady Hands", "emoji": "🧤", "reach": 0.0,  "risk": 0.0},
    "charge": {"label": "Charge In",   "emoji": "🏃", "reach": 0.12, "risk": 0.08},
}
THROW_ACTIONS = {
    "direct": {"label": "Direct Hit",   "emoji": "🎯", "bonus": 0.0,  "risk": 0.22},
    "keeper": {"label": "Throw to Keeper", "emoji": "🧤", "bonus": 0.18, "risk": 0.0},
}


def can_enable(team_sizes):
    """Fielding needs three players a side. `team_sizes` is an iterable of counts."""
    return all(n >= MIN_PLAYERS_FOR_FIELDING for n in team_sizes)


def is_enabled(match):
    return bool(getattr(match, "fielding_enabled", False))


def setup(match, enabled, assignments=None):
    """Turn fielding on/off for a match and record the assigned fielders.

    `assignments` is {team_name: [player_name, ...]}. Returns (enabled, reason).
    """
    sizes = []
    for team in (getattr(match, "team1", None), getattr(match, "team2", None)):
        if team:
            sizes.append(len(team.get("players", [])))
    if enabled and not can_enable(sizes):
        match.fielding_enabled = False
        return False, (f"fielding needs {MIN_PLAYERS_FOR_FIELDING} players a side "
                       f"(sides here: {', '.join(str(s) for s in sizes)})")
    match.fielding_enabled = bool(enabled)
    match.fielders = assignments or {}
    if enabled and not match.fielders:
        match.fielders = auto_assign(match)
    return match.fielding_enabled, None


def auto_assign(match):
    """Pick three fielders a side: the best fielders available.

    Career players are ranked on their fielding rating; engine-only players (bots
    and filler) fall back to a neutral rating so they can still be assigned.
    """
    out = {}
    for team in (getattr(match, "team1", None), getattr(match, "team2", None)):
        if not team:
            continue
        ranked = sorted(team.get("players", []),
                        key=lambda p: -_player_field_rating(p))
        out[team["name"]] = [p["name"] for p in ranked[:FIELDERS_PER_SIDE]]
    return out


def _player_field_rating(player):
    """Fielding rating for an engine player dict.

    Engine players carry only bat/bowl, so a career's real fielding attributes are
    attached at team-build time as `field_rating`. Anything without one is treated
    as an average fielder.
    """
    r = player.get("field_rating")
    return int(r) if r is not None else 58


def attach_ratings(engine_player, career):
    """Carry a career's fielding numbers onto its engine player.

    The engine ignores these completely - they exist so the fielding prompts can
    find them again mid-match without a database lookup.
    """
    a = career.get("attributes", {})
    engine_player["field_rating"] = _AT.field_skill(a)
    engine_player["catch_rating"] = _AT.catch_skill(a)
    engine_player["throw_rating"] = _AT.throw_skill(a)
    return engine_player


def fielders_for(match, team_name):
    return list((getattr(match, "fielders", {}) or {}).get(team_name, []))


def pick_fielder(match, rec=None):
    """Who is under this chance? One of the bowling side's assigned fielders.

    Deterministic per ball so the same delivery always falls to the same fielder,
    even if the card is re-rendered.
    """
    innings = match.current_innings
    if not innings:
        return None
    names = fielders_for(match, innings.bowling_team["name"])
    if not names:
        return None
    idx = (rec or {}).get("ball_index", 0) if rec else random.randrange(len(names))
    return names[idx % len(names)]


def find_player(match, name):
    for team in (getattr(match, "team1", None), getattr(match, "team2", None)):
        for p in (team or {}).get("players", []):
            if p["name"] == name:
                return p
    return None


def catch_chance(player, rec, action="steady"):
    """Probability this catch is held.

    Built from the fielder's catching rating, how hard the chance is, and how
    ambitious the attempt was. A dive reaches balls steady hands never would, but
    spills more of the ones it does reach.
    """
    skill = _player_field_rating(player) if player else 58
    catch = (player or {}).get("catch_rating", skill)
    shot = str(rec.get("shot") or "")
    difficulty = CHANCE_DIFFICULTY.get(shot, 0.78)
    if rec.get("runs_off_bat", 0) >= 4:
        difficulty -= 0.14          # it was middled - much harder chance
    act = CATCH_ACTIONS.get(action, CATCH_ACTIONS["steady"])

    # A good fielder should be genuinely reliable: an elite pair of hands holds
    # ~90% of regulation chances, and only the awkward ones (scoop, top-edged
    # hook, a middled drive) drag that down. An earlier curve topped out near 70%,
    # which made every catch feel like a coin flip.
    base = 0.45 + (catch / 99.0) * 0.50          # 0.45 at 0, ~0.95 at 99
    # Capped near 1.0: the reach bonus is what lets a dive REACH a hard chance,
    # not a licence to exceed your own hands on an easy one.
    factor = min(1.02, (difficulty + act["reach"]) / 0.75)
    p = base * factor
    # The risk of an ambitious attempt scales with how easy the chance was: diving
    # at a regulation catch you would have taken standing up is how you drop it,
    # while on a genuinely hard chance the extra reach is worth the risk. Without
    # this, diving was strictly better every time and the choice was fake.
    p -= act["risk"] * (1.0 - catch / 140.0) * (difficulty / 0.70)
    return max(0.05, min(0.97, p))


def run_out_chance(player, rec, action="keeper"):
    """Probability the run-out is completed."""
    skill = _player_field_rating(player) if player else 58
    throw = (player or {}).get("throw_rating", skill)
    act = THROW_ACTIONS.get(action, THROW_ACTIONS["keeper"])
    base = 0.30 + (throw / 99.0) * 0.45
    p = base + act["bonus"] - act["risk"] * (1.0 - throw / 150.0)
    return max(0.05, min(0.94, p))


def resolve(kind, player, rec, action):
    """Roll the attempt. Returns a verdict dict for the UI and the caller."""
    if kind == "catch":
        p = catch_chance(player, rec, action)
        held = random.random() < p
        label = CATCH_ACTIONS.get(action, CATCH_ACTIONS["steady"])["label"]
        return {"kind": "catch", "success": held, "chance": round(p, 3), "action": label,
                "headline": "CAUGHT!" if held else "DROPPED!",
                "detail": (f"{label} — held onto it." if held
                           else f"{label} — it bursts through the hands.")}
    p = run_out_chance(player, rec, action)
    hit = random.random() < p
    label = THROW_ACTIONS.get(action, THROW_ACTIONS["keeper"])["label"]
    return {"kind": "run_out", "success": hit, "chance": round(p, 3), "action": label,
            "headline": "RUN OUT!" if hit else "SAFE!",
            "detail": (f"{label} — the stumps go back." if hit
                       else f"{label} — missed, and they scramble home.")}


def opportunity(match, rec):
    """Is there a fielding chance on this ball, and whose is it?

    Only the two dismissals that are genuinely a fielder's to take: a catch and a
    run-out. Everything else (bowled, LBW, stumped) belongs to the bowler or the
    keeper and is left alone.
    """
    if not is_enabled(match) or not rec:
        return None
    dismissal = rec.get("dismissal")
    if dismissal not in ("Caught", "Run Out"):
        return None
    name = pick_fielder(match, rec)
    if not name:
        return None
    player = find_player(match, name)
    return {"kind": "catch" if dismissal == "Caught" else "run_out",
            "fielder": name, "player": player,
            "owner_id": (player or {}).get("owner_id")}


def credit(career, verdict):
    """Record the outcome on the fielder's career stats.

    These counters existed but could never move, because the engine does not
    attribute fielding. Career mode now fills them itself.
    """
    st = career.setdefault("stats", {}).setdefault(
        "field", {"catches": 0, "stumpings": 0, "run_outs": 0, "drops": 0})
    if verdict["kind"] == "catch":
        st["catches" if verdict["success"] else "drops"] = \
            st.get("catches" if verdict["success"] else "drops", 0) + 1
    elif verdict["success"]:
        st["run_outs"] = st.get("run_outs", 0) + 1
    return st
