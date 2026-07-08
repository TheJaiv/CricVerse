# ── Ascension League (rating-based open ladder) ───────────────────────────────
# An open, ~2-week Elo league: teams challenge anyone available anytime, nobody is
# eliminated, and SKILL (not volume) decides rank. Squads are set by an EXTERNAL
# auction bot and loaded via the normal `submit_squad` flow — no auction here.
#
# Elo (margin + opponent weighted): beating a higher-rated team gains more; a
# bigger win margin gains more; farming a much weaker team gains ≈0 (diminishing
# returns) so grinding is pointless. A min-games gate ties playoff eligibility to
# activity without rewarding volume.
#
# Two roster-evolution features ride on top (generic to ANY active tournament):
#   • player-for-player TRADES (both owners + a manager confirm), and
#   • a HARD, weak-team-favouring CREDIT economy → small player BOOSTS (+1, capped),
#     so strugglers can slowly grow but nobody can build a super-team.
#
# Import direction: this module imports from the manager modules; they import
# rating_league LAZILY (inside function bodies) to avoid circular imports.

import datetime

import discord

from subscription_manager import DB_CACHE, async_save_tournament_to_bin
from tournament_manager import (
    get_tournament_standings, save_tournament,
    _acl_fill, _acl_winner_loser, _acl_next_mid, _acl_match_line,
)

# ══════════════════════════════════════════════════════════════════════════════
# CONFIG — the single tuning point.
# ══════════════════════════════════════════════════════════════════════════════
RATING_CONFIG = {
    "type_key": "rating",
    "display_name": "Conquest League",
    "short_name": "CQL",
    "format_overs": 20,
    "min_squad": 11,
    "max_squad": 18,
    "impact_player": False,
    "injuries": False,
    # Elo
    "base_rating": 1000,
    "k_factor": 32,
    "k_provisional": 48,
    "provisional_games": 5,
    "margin_cap": 2.0,
    # qualification / playoffs
    "min_games_qualify": 10,
    "playoff_teams": 4,
}

RATING_PLAYOFF_STAGE = "rating_playoff"
RATING_KO_STAGES = (RATING_PLAYOFF_STAGE,)

# ── HARD, weak-team-favouring credit economy (see module header) ──────────────
CREDITS_CONFIG = {
    "win": 8, "tie": 5, "loss": 3,        # base per completed match (loss = consolation floor)
    "catchup_k": 1.0,                     # catch-up mult = 1 + k·(1 − strongness) → weak teams up to ~×2
    "underdog_bonus": 14.0,               # × strength-gap, added on an underdog WIN
    "underdog_pair_cap": 24,              # max underdog bonus farmable vs ONE opponent per season
    "ms_fifty": 1, "ms_hundred": 3, "ms_3wkt": 2, "ms_5wkt": 4,  # small milestone credits
    "per_match_cap": 26,                  # hard ceiling on credits from a single match
}
# Boosts — deliberately hard so no super-teams.
BOOST_COST           = 40    # credits per +1
BOOST_MAX_PER_PLAYER = 3     # total +1s a single player can ever receive
BOOST_MAX_PER_TEAM   = 10    # total +1s across a whole squad
BOOST_RATING_CAP     = 95    # effective bat/bowl can never exceed this


def is_rating_tournament(tourney):
    return bool(tourney) and tourney.get("tournament_type") == RATING_CONFIG["type_key"]


# ══════════════════════════════════════════════════════════════════════════════
# FACTORY
# ══════════════════════════════════════════════════════════════════════════════
def create_rating_tournament(server_id, creator_id):
    """A fresh Ascension League in registration. Caller saves + announces."""
    return {
        "server_id": str(server_id),
        "name": RATING_CONFIG["display_name"],
        "managers": [str(creator_id)],
        "teams": [],
        "status": "registration",
        "schedule": [],
        "current_match_idx": 0,
        "stats": {},
        "format_overs": RATING_CONFIG["format_overs"],
        "min_squad": RATING_CONFIG["min_squad"],
        "max_squad": RATING_CONFIG["max_squad"],
        "impact_player": RATING_CONFIG["impact_player"],
        "injuries_enabled": RATING_CONFIG["injuries"],
        "tournament_type": RATING_CONFIG["type_key"],
        "conditions_mode": "auto",         # open play → auto pitch/weather per match
        "match_order": "random",           # open play is inherently free-order
        "stadiums": [],
    }


def _team(tourney, name):
    return next((t for t in tourney.get("teams", []) if t["name"] == name), None)


def team_rating(team):
    return team.get("rating", RATING_CONFIG["base_rating"])


# ══════════════════════════════════════════════════════════════════════════════
# ELO
# ══════════════════════════════════════════════════════════════════════════════
def expected(r_a, r_b):
    return 1.0 / (1.0 + 10 ** ((r_b - r_a) / 400.0))


def _k_for(team):
    return (RATING_CONFIG["k_provisional"] if team.get("games", 0) < RATING_CONFIG["provisional_games"]
            else RATING_CONFIG["k_factor"])


def margin_multiplier(result):
    """1.0 (nailbiter/tie) → margin_cap (thrashing). Win by runs scales on the run
    gap; win by wickets on wickets in hand. Opponent-weighting is separate (the Elo
    E term) — this is purely how DECISIVE the win was."""
    winner = result.get("winner")
    if not winner or winner == "TIE":
        return 1.0
    bf = result.get("batted_first")
    # winner's own runs/wickets
    if winner == result.get("_t1_name"):
        w_runs, w_wkts, l_runs = result["t1_runs"], result["t1_wickets"], result["t2_runs"]
    else:
        w_runs, w_wkts, l_runs = result["t2_runs"], result["t2_wickets"], result["t1_runs"]
    if bf and winner == bf:                       # defended a total → won by runs
        frac = max(0.0, (w_runs - l_runs)) / 100.0
    else:                                         # chased → won by wickets in hand
        frac = max(0.0, (10 - w_wkts)) / 10.0
    return min(RATING_CONFIG["margin_cap"], 1.0 + min(1.0, frac))


def apply_match_rating(tourney, m_data):
    """Update both teams' Elo from a completed match. Stores rating_before/rating_delta
    on the result for display + revert. Also bumps games/W/L/T. Safe/idempotent-guarded
    by the caller (only called once per completion)."""
    if not is_rating_tournament(tourney):
        return
    res = m_data.get("result") or {}
    a, b = _team(tourney, m_data["team1"]), _team(tourney, m_data["team2"])
    if not a or not b:
        return
    ra, rb = team_rating(a), team_rating(b)
    winner = res.get("winner")
    res["_t1_name"] = a["name"]                    # margin_multiplier needs to know which is t1
    if winner == "TIE" or winner is None:
        sa = 0.5
    elif winner == a["name"]:
        sa = 1.0
    else:
        sa = 0.0
    mm = margin_multiplier(res)
    res.pop("_t1_name", None)
    ea = expected(ra, rb)
    da = _k_for(a) * mm * (sa - ea)
    db = _k_for(b) * mm * ((1.0 - sa) - (1.0 - ea))
    a["rating"] = ra + da
    b["rating"] = rb + db
    for t, s in ((a, sa), (b, 1.0 - sa)):
        t["games"] = t.get("games", 0) + 1
        if winner == "TIE" or winner is None:
            t["ties"] = t.get("ties", 0) + 1
        elif s == 1.0:
            t["wins"] = t.get("wins", 0) + 1
        else:
            t["losses"] = t.get("losses", 0) + 1
    res["rating_before"] = {a["name"]: round(ra, 1), b["name"]: round(rb, 1)}
    res["rating_delta"] = {a["name"]: round(da, 1), b["name"]: round(db, 1)}


def revert_match_rating(tourney, m_data):
    """Undo a completed rating match: subtract the stored deltas and the game counts."""
    res = m_data.get("result") or {}
    delta = res.get("rating_delta") or {}
    for name, d in delta.items():
        t = _team(tourney, name)
        if not t:
            continue
        t["rating"] = team_rating(t) - d
        t["games"] = max(0, t.get("games", 0) - 1)
    winner = res.get("winner")
    for name in delta:
        t = _team(tourney, name)
        if not t:
            continue
        if winner == "TIE" or winner is None:
            t["ties"] = max(0, t.get("ties", 0) - 1)
        elif winner == name:
            t["wins"] = max(0, t.get("wins", 0) - 1)
        else:
            t["losses"] = max(0, t.get("losses", 0) - 1)


# ══════════════════════════════════════════════════════════════════════════════
# CREDITS + BOOSTS (generic — any active tournament)
# ══════════════════════════════════════════════════════════════════════════════
def _player_ovr(p):
    return max(float(p.get("bat", 50)), float(p.get("bowl", 50)))


def team_strongness(tourney, team):
    """0 (weakest) .. 1 (strongest). Rating league → Elo band; else → avg top-11 OVR band."""
    if is_rating_tournament(tourney):
        return max(0.0, min(1.0, (team_rating(team) - 900.0) / 200.0))
    ovrs = sorted((_player_ovr(p) for p in team.get("squad", [])), reverse=True)[:11]
    avg = sum(ovrs) / len(ovrs) if ovrs else 75.0
    return max(0.0, min(1.0, (avg - 70.0) / 20.0))


def _milestone_credits(delta_for_team):
    """Small credits from a team's per-match stats_delta (fifties/hundreds/wkts)."""
    c = 0
    for _p, fields in (delta_for_team or {}).items():
        c += fields.get("fifties", 0) * CREDITS_CONFIG["ms_fifty"]
        c += fields.get("hundreds", 0) * CREDITS_CONFIG["ms_hundred"]
        w = fields.get("wickets", 0)
        if w >= 5: c += CREDITS_CONFIG["ms_5wkt"]
        elif w >= 3: c += CREDITS_CONFIG["ms_3wkt"]
    return c


def award_match_credits(tourney, m_data):
    """Award weak-favouring credits to both teams for a completed match. Idempotent-
    guarded via result['credits_awarded']. Works for every league type."""
    res = m_data.get("result") or {}
    if res.get("credits_awarded"):
        return {}
    a, b = _team(tourney, m_data["team1"]), _team(tourney, m_data["team2"])
    if not a or not b:
        return {}
    winner = res.get("winner")
    delta = res.get("stats_delta") or {}
    awarded = {}
    for team, opp in ((a, b), (b, a)):
        is_tie = winner == "TIE" or winner is None
        is_win = (not is_tie) and winner == team["name"]
        base = CREDITS_CONFIG["tie"] if is_tie else (CREDITS_CONFIG["win"] if is_win else CREDITS_CONFIG["loss"])
        strong = team_strongness(tourney, team)
        credits = base * (1.0 + CREDITS_CONFIG["catchup_k"] * (1.0 - strong))
        # underdog-win bonus (capped per opponent-pairing per season)
        if is_win:
            gap = team_strongness(tourney, opp) - strong
            if gap > 0:
                bonus = CREDITS_CONFIG["underdog_bonus"] * gap
                earned = team.setdefault("underdog_earned", {})
                room = max(0, CREDITS_CONFIG["underdog_pair_cap"] - earned.get(opp["name"], 0))
                bonus = min(bonus, room)
                earned[opp["name"]] = earned.get(opp["name"], 0) + bonus
                credits += bonus
        credits += _milestone_credits(delta.get(team["name"]))
        credits = min(CREDITS_CONFIG["per_match_cap"], int(round(credits)))
        team["credits"] = team.get("credits", 0) + credits
        awarded[team["name"]] = credits
    res["credits_awarded"] = True
    res["credits_amounts"] = awarded          # for exact revert
    return awarded


def revert_match_credits(tourney, m_data):
    """Undo the credits + underdog-pairing tally a match awarded (uses stored amounts)."""
    res = m_data.get("result") or {}
    for name, amt in (res.get("credits_amounts") or {}).items():
        t = _team(tourney, name)
        if t:
            t["credits"] = max(0, t.get("credits", 0) - amt)
    # roll back the underdog-pairing tracker for the winner (best-effort, non-critical)
    winner = res.get("winner")
    if winner and winner != "TIE":
        loser = m_data["team2"] if winner == m_data["team1"] else m_data["team1"]
        wt = _team(tourney, winner)
        if wt and "underdog_earned" in wt and loser in wt["underdog_earned"]:
            # can't know the exact split; clearing the pairing is the safe over-refund
            wt["underdog_earned"].pop(loser, None)


def _boost_totals(team):
    per_player = {}
    total = 0
    for p in team.get("squad", []):
        b = p.get("tboost_bat", 0) + p.get("tboost_bowl", 0)
        if b:
            per_player[p["name"]] = b
            total += b
    return per_player, total


def apply_boost(tourney, team, player_name, skill):
    """Spend BOOST_COST credits to add +1 to a squad player's bat/bowl. Returns (ok, msg).
    Enforces credits, per-player cap, per-team cap, and the 95 effective ceiling."""
    skill = skill.strip().lower()
    if skill not in ("bat", "bowl"):
        return False, "❌ Boost a **bat** or **bowl** rating."
    p = next((x for x in team.get("squad", []) if x["name"].lower() == player_name.strip().lower()), None)
    if not p:
        return False, f"❌ **{player_name}** isn't in **{team['name']}**'s squad."
    if team.get("credits", 0) < BOOST_COST:
        return False, f"❌ Not enough credits — need **{BOOST_COST}**, have **{team.get('credits', 0)}**. Win matches (esp. as the underdog) to earn more."
    cur_player = p.get("tboost_bat", 0) + p.get("tboost_bowl", 0)
    if cur_player >= BOOST_MAX_PER_PLAYER:
        return False, f"❌ **{p['name']}** is maxed out (+{BOOST_MAX_PER_PLAYER} per player)."
    _, team_total = _boost_totals(team)
    if team_total >= BOOST_MAX_PER_TEAM:
        return False, f"❌ Squad boost cap reached (+{BOOST_MAX_PER_TEAM} total for the team)."
    key = "tboost_" + skill
    effective = float(p.get(skill, 50)) + p.get(key, 0) + 1
    if effective > BOOST_RATING_CAP:
        return False, f"❌ **{p['name']}**'s {skill}ting is already at its maximum — can't boost further."
    p[key] = p.get(key, 0) + 1
    team["credits"] = team.get("credits", 0) - BOOST_COST
    _, team_total = _boost_totals(team)
    skill_word = "batting" if skill == "bat" else "bowling"
    # NOTE: never reveal the numeric rating — players don't see ratings in this bot.
    return True, (f"⬆️ **{p['name']}**'s {skill_word} boosted (+1)! "
                  f"(−{BOOST_COST} credits, **{team['credits']}** left · "
                  f"this player {cur_player+1}/{BOOST_MAX_PER_PLAYER}, squad {team_total}/{BOOST_MAX_PER_TEAM})")


def apply_tournament_boosts(roster):
    """Return a NEW roster with each player's stored tournament boost deltas folded onto
    bat/bowl (capped at BOOST_RATING_CAP). Copies each player dict so the PERSISTENT
    squad is never mutated (else boosts would compound every match). MUST be called
    AFTER apply_server_overrides so boosts survive overrides. Players with no boost
    pass through unchanged (cheap). Tournament-scoped by construction."""
    out = []
    for p in roster or []:
        tb, tbo = p.get("tboost_bat", 0), p.get("tboost_bowl", 0)
        if tb or tbo:
            p = dict(p)   # copy — never touch the stored squad dict
            if tb:
                p["bat"] = min(BOOST_RATING_CAP, float(p.get("bat", 50)) + tb)
            if tbo:
                p["bowl"] = min(BOOST_RATING_CAP, float(p.get("bowl", 50)) + tbo)
        out.append(p)
    return out


# ══════════════════════════════════════════════════════════════════════════════
# OPEN PLAY + STANDINGS
# ══════════════════════════════════════════════════════════════════════════════
def create_open_match(tourney, team1, team2):
    """A fresh on-demand ladder match dict (caller appends to schedule + dispatches)."""
    return {
        "match_id": _acl_next_mid(tourney), "round": "Ladder", "stage": "ladder",
        "team1": team1, "team2": team2, "status": "pending", "result": None,
    }


def rating_standings(tourney):
    """[(team_name, dict)] sorted by rating desc. dict: rating, games, W/L/T, qualified."""
    minq = RATING_CONFIG["min_games_qualify"]
    rows = []
    for t in tourney.get("teams", []):
        g = t.get("games", 0)
        rows.append((t["name"], {
            "rating": round(team_rating(t)), "games": g,
            "W": t.get("wins", 0), "L": t.get("losses", 0), "T": t.get("ties", 0),
            "qualified": g >= minq,
        }))
    return sorted(rows, key=lambda r: r[1]["rating"], reverse=True)


def rating_board_embed(tourney):
    rows = rating_standings(tourney)
    minq = RATING_CONFIG["min_games_qualify"]
    e = discord.Embed(title=f"📈 {tourney['name']} — Rating Ladder", color=discord.Color.from_rgb(90, 40, 160))
    if tourney.get("rating_champion"):
        e.description = f"👑 **Champions: {tourney['rating_champion']}**"
    lines = ["```", f"{'#':<3}{'Team':<18}{'Elo':>6}{'P':>4}{'W':>3}{'L':>3}{'T':>3}  {'':<3}", "─" * 44]
    for i, (nm, d) in enumerate(rows, 1):
        flag = "✓" if d["qualified"] else "·"
        lines.append(f"{i:<3}{str(nm)[:16]:<18}{d['rating']:>6}{d['games']:>4}{d['W']:>3}{d['L']:>3}{d['T']:>3}  {flag}")
    lines.append("```")
    e.description = (e.description + "\n" if e.description else "") + "\n".join(lines)
    e.set_footer(text=f"✓ = eligible for playoffs (≥{minq} games) · top {RATING_CONFIG['playoff_teams']} qualified make the finals")
    return e


# ══════════════════════════════════════════════════════════════════════════════
# PLAYOFFS — top-4 eligible → SF1 (1v4) · SF2 (2v3) → Final  (clone of DSL)
# ══════════════════════════════════════════════════════════════════════════════
def _rating_get(tourney, round_name):
    return next((m for m in tourney.get("schedule", [])
                 if m.get("stage") in RATING_KO_STAGES and m.get("round") == round_name), None)


def generate_rating_playoffs(tourney):
    """Build the Top-4 knockout from the eligible ladder. Returns (ok, message)."""
    if not is_rating_tournament(tourney):
        return False, "This isn't an Ascension League."
    if any(m.get("stage") in RATING_KO_STAGES for m in tourney.get("schedule", [])):
        return False, "❌ Playoffs already generated."
    if any(m.get("status") == "pending" and m.get("stage") == "ladder" for m in tourney.get("schedule", [])):
        return False, "❌ Finish or cancel the live ladder match(es) first."
    minq = RATING_CONFIG["min_games_qualify"]
    eligible = [(n, d) for n, d in rating_standings(tourney) if d["qualified"]]
    if len(eligible) < RATING_CONFIG["playoff_teams"]:
        return False, (f"❌ Need **{RATING_CONFIG['playoff_teams']}** teams with ≥**{minq}** games "
                       f"— only **{len(eligible)}** qualify so far.")
    s1, s2, s3, s4 = [n for n, _ in eligible[:4]]
    tourney["playoff_seeds"] = [s1, s2, s3, s4]
    mid = _acl_next_mid(tourney)
    def mk(rnd, t1, t2, t1s, t2s, status):
        nonlocal mid
        tourney["schedule"].append({
            "match_id": mid, "round": rnd, "stage": RATING_PLAYOFF_STAGE,
            "team1": t1, "team2": t2, "team1_src": t1s, "team2_src": t2s,
            "status": status, "result": None,
        })
        mid += 1
    mk("Semi-Final 1", s1, s4, "1st · Ladder", "4th · Ladder", "pending")
    mk("Semi-Final 2", s2, s3, "2nd · Ladder", "3rd · Ladder", "pending")
    mk("Final", None, None, "Winner · Semi-Final 1", "Winner · Semi-Final 2", "locked")
    from tournament_manager import assign_tournament_conditions
    assign_tournament_conditions(tourney)
    save_tournament(tourney)
    return True, "ok"


def _rating_try_advance(tourney):
    if not is_rating_tournament(tourney):
        return
    sf1 = _rating_get(tourney, "Semi-Final 1")
    if not sf1:
        return
    sf2 = _rating_get(tourney, "Semi-Final 2")
    fi = _rating_get(tourney, "Final")
    w1, _ = _acl_winner_loser(sf1)
    if w1: _acl_fill(fi, "team1", w1)
    w2, _ = _acl_winner_loser(sf2)
    if w2: _acl_fill(fi, "team2", w2)
    champ, runner = _acl_winner_loser(fi)
    if champ:
        tourney["rating_champion"] = champ
        tourney["rating_runner_up"] = runner
        if tourney.get("status") != "completed":
            tourney["status"] = "completed"


def rating_bracket_embed(tourney):
    e = discord.Embed(title=f"🏆 {tourney['name']} — Playoffs", color=discord.Color.from_rgb(90, 40, 160))
    if tourney.get("rating_champion"):
        e.description = f"👑 **Champions: {tourney['rating_champion']}**"
    semis = [_rating_get(tourney, r) for r in ("Semi-Final 1", "Semi-Final 2")]
    fi = _rating_get(tourney, "Final")
    if any(semis):
        e.add_field(name="🏏 Semi-Finals", value="\n".join(_acl_match_line(m) for m in semis if m), inline=False)
    if fi:
        e.add_field(name="🏆 Final", value=_acl_match_line(fi), inline=False)
    return e
