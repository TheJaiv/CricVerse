"""
Flatten a live CricketMatch into a plain dict the renderers can draw.

This is the seam that keeps career/ui free of bot.py: nothing in career/ui ever
touches a match object, so every card can be rendered headless from fixture data
in tools/career_render_preview.py. Same idea as bot.py's extract_scoreboard_data,
but tuned for the broadcast card instead of the tournament scorecard themes.

Everything here is defensive - a career match is driven by humans pressing
buttons, and a render must never be the thing that kills a live match.
"""

# Fallback team colours when a match has none (debut and scenario build their
# teams inline and never set one).
_FALLBACK_COLORS = ("#2E6BE6", "#E2603A", "#37A06B", "#8B5CF6", "#E2B33A", "#12A3B4")


def _team_color(team, idx):
    c = (team or {}).get("color")
    if c:
        return c
    return _FALLBACK_COLORS[idx % len(_FALLBACK_COLORS)]


def _overs_str(balls):
    return f"{balls // 6}.{balls % 6}"


def ball_label(rec):
    """Short scorecard token for one delivery: 4, 6, W, WD, NB2, 1B."""
    if rec.get("is_wide"):
        return "WD"
    if rec.get("dismissal"):
        return "W"
    runs = rec.get("runs_off_bat", 0)
    if rec.get("is_bye"):
        return f"{rec.get('extras', 0)}B"
    if rec.get("is_no_ball"):
        return f"NB{runs}" if runs else "NB"
    return str(runs)


def ball_tone(rec):
    """Key into theme.OUTCOME_COLOR for this delivery."""
    if rec.get("is_wide"):
        return "wide"
    if rec.get("is_no_ball"):
        return "noball"
    if rec.get("dismissal"):
        return "wicket"
    r = rec.get("runs_off_bat", 0)
    return {0: "dot", 1: "single", 2: "two", 3: "three", 4: "four", 6: "six"}.get(r, "single")


def _current_over_no(innings):
    over_no = innings.total_balls // 6
    # A completed over has total_balls landing exactly on the boundary; the strip
    # should then still show the over that was just bowled, not an empty next one.
    if innings.total_balls and innings.total_balls % 6 == 0:
        over_no -= 1
    return max(0, over_no)


def over_balls(innings, over_no):
    """The deliveries bowled in one over, oldest first.

    Read off ball_history rather than over_log: over_log holds Discord emoji ids
    (useless to a renderer) and the match loops clear it at every over break -
    which is exactly why the card could never show a previous over.
    """
    hist = getattr(innings, "ball_history", None) or []
    return [{"label": ball_label(r), "tone": ball_tone(r), "legal": r.get("legal", True)}
            for r in hist if r.get("over") == over_no]


def this_over(innings):
    return over_balls(innings, _current_over_no(innings))


def last_over(innings):
    """The over before the current one - players lose the thread of a match when
    the card only ever shows six balls of context."""
    n = _current_over_no(innings)
    return over_balls(innings, n - 1) if n > 0 else []


def recent_overs(innings, count=6):
    """[(over_number, runs, wickets)] for the last `count` completed overs."""
    hist = getattr(innings, "ball_history", None) or []
    if not hist:
        return []
    agg = {}
    for r in hist:
        o = r.get("over", 0)
        runs, wkts = agg.get(o, (0, 0))
        agg[o] = (runs + r.get("runs_off_bat", 0) + r.get("extras", 0)
                  + (1 if r.get("is_wide") else 0),
                  wkts + (1 if r.get("dismissal") else 0))
    cur = _current_over_no(innings)
    done = [(o, v[0], v[1]) for o, v in sorted(agg.items()) if o < cur]
    return done[-count:]


def _batter_rows(innings):
    rows = []
    players = innings.batting_team["players"]
    for idx in (innings.current_striker_idx, innings.current_non_striker_idx):
        if idx is None or idx >= len(players):
            continue
        p = players[idx]
        st = innings.batting_stats.get(p["name"])
        if not st:
            continue
        row = {
            "name": p["name"],
            "runs": st.runs_scored,
            "balls": st.balls_faced,
            "fours": getattr(st, "fours", 0),
            "sixes": getattr(st, "sixes", 0),
            "sr": (st.runs_scored / st.balls_faced * 100) if st.balls_faced else 0.0,
            "striker": idx == innings.current_striker_idx,
            "owner_id": p.get("owner_id"),
        }
        if not any(r["name"] == row["name"] for r in rows):
            rows.append(row)
    rows.sort(key=lambda r: not r["striker"])   # striker first
    return rows


def _bowler_row(innings):
    cb = innings.current_bowler
    if not cb:
        return None
    st = innings.bowling_stats.get(cb["name"])
    if not st:
        return None
    return {
        "name": cb["name"],
        "balls": st.balls_bowled,
        "overs": _overs_str(st.balls_bowled),
        "runs": st.runs_conceded,
        "wickets": st.wickets_taken,
        "econ": (st.runs_conceded / st.balls_bowled * 6) if st.balls_bowled else 0.0,
        "role": cb.get("role", ""),
        "owner_id": cb.get("owner_id"),
    }


def _objective(match, innings, balls_left):
    """The career-specific goal line: scenario target, debut pass mark, or chase."""
    if getattr(match, "is_scenario", False):
        if getattr(match, "scenario_mode", "bat") == "bowl":
            tgt = getattr(match, "scenario_wkt_target", 0)
            st = innings.bowling_stats.get(getattr(match, "scenario_player_name", ""))
            got = st.wickets_taken if st else 0
            return {"kind": "wickets", "text": f"TAKE {tgt} WICKETS",
                    "detail": f"{got}/{tgt} · {balls_left} balls left", "done": got >= tgt}
        tgt = getattr(match, "scenario_target", 0)
        need = max(0, tgt - innings.total_runs)
        return {"kind": "chase", "text": f"TARGET {tgt}",
                "detail": (f"need {need} off {balls_left}" if need else "target reached"),
                "done": need == 0}
    if getattr(match, "is_debut", False):
        tgt = getattr(match, "debut_target", None)
        if tgt is not None:
            need = max(0, tgt - innings.total_runs)
            return {"kind": "chase", "text": f"PASS MARK {tgt}",
                    "detail": (f"need {need} off {balls_left}" if need else "pass mark reached"),
                    "done": need == 0}
    return None


def match_kind(match):
    for attr, kind in (("is_debut", "debut"), ("is_scenario", "scenario"), ("is_club", "club")):
        if getattr(match, attr, False):
            return kind
    return "casual"


def _pregame_state(match):
    """Between the lobby and the first ball there is no innings yet - the toss and
    the opener picks both happen first - so the card falls back to a team-sheet
    graphic instead of exploding on a None innings."""
    t1 = getattr(match, "team1", None) or {"name": "Team A"}
    t2 = getattr(match, "team2", None) or {"name": "Team B"}
    return {
        "kind": match_kind(match), "format_overs": getattr(match, "format_overs", 0),
        "innings_num": 1, "pregame": True,
        "pitch": getattr(match, "pitch", ""), "weather": getattr(match, "weather", ""),
        "batting": {"name": t1["name"], "color": _team_color(t1, 0), "runs": 0, "wickets": 0,
                    "balls": 0, "overs": "0.0", "extras": 0},
        "bowling": {"name": t2["name"], "color": _team_color(t2, 1)},
        "max_wickets": getattr(match, "max_wickets", 10),
        "crr": 0.0, "partnership": 0, "batters": [], "bowler": None, "this_over": [],
        "last_over": [], "recent_overs": [], "over_no": 0,
        "balls_left": getattr(match, "max_balls", 0), "free_hit": False,
        "reviews": dict(getattr(match, "drs_reviews", {}) or {}), "last_ball": None,
        "objective": None, "target": None, "need": None, "rrr": None, "proj": 0,
        "first_innings": None, "toss": None,
    }


def _innings_card(innings):
    """Full batting + bowling card for one innings."""
    if innings is None:
        return None
    bat = []
    for p in innings.batting_team["players"]:
        st = innings.batting_stats.get(p["name"])
        if not st or (st.balls_faced == 0 and st.dismissal == "not out"):
            continue
        bat.append({
            "name": p["name"], "runs": st.runs_scored, "balls": st.balls_faced,
            "fours": getattr(st, "fours", 0), "sixes": getattr(st, "sixes", 0),
            "sr": (st.runs_scored / st.balls_faced * 100) if st.balls_faced else 0.0,
            "how": st.dismissal, "out": st.dismissal != "not out",
        })
    bowl = []
    for p in innings.bowling_team["players"]:
        st = innings.bowling_stats.get(p["name"])
        if not st or st.balls_bowled == 0:
            continue
        bowl.append({
            "name": p["name"], "overs": _overs_str(st.balls_bowled),
            "runs": st.runs_conceded, "wickets": st.wickets_taken,
            "econ": (st.runs_conceded / st.balls_bowled * 6) if st.balls_bowled else 0.0,
        })
    return {
        "team": innings.batting_team["name"],
        "bowling_team": innings.bowling_team["name"],
        "color": _team_color(innings.batting_team, 0),
        "runs": innings.total_runs, "wickets": innings.wickets,
        "overs": _overs_str(innings.total_balls),
        "extras": getattr(innings, "extras", 0),
        "batting": bat, "bowling": bowl,
        "history": list(getattr(innings, "ball_history", None) or []),
    }


def build_scorecard_state(match, result=None, potm=None):
    """Both innings in full, for the innings-break, result and scorecard images."""
    return {
        "kind": match_kind(match),
        "format_overs": match.format_overs,
        "pitch": getattr(match, "pitch", ""), "weather": getattr(match, "weather", ""),
        "innings": [c for c in (_innings_card(match.innings1), _innings_card(match.innings2)) if c],
        "result": result,
        "potm": potm,
        "target": getattr(match, "target", None),
        "max_wickets": getattr(match, "max_wickets", 10),
    }


def build_player_state(career):
    """Career document -> plain dict for the player card.

    career_manager is imported lazily so career/ui stays renderable from fixtures
    with no Mongo driver in the picture.
    """
    from career import career_manager as CM

    a = career.get("attributes", {})
    bt = CM.BOWLING_TYPES.get(career.get("bowling_type", "pace"), {})
    ms = CM.MINDSETS.get(career.get("mindset", "standard"), {})
    bat = (career.get("stats") or {}).get("bat", {})
    bowl = (career.get("stats") or {}).get("bowl", {})

    innings = bat.get("innings", 0)
    outs = bat.get("outs", 0)
    balls = bat.get("balls", 0)
    b_balls = bowl.get("balls", 0)
    form = career.get("form") or {}
    fitness = career.get("fitness") or {}

    return {
        "name": career.get("username", "Rookie"),
        "title": career.get("cosmetic_title", ""),
        "ovr": career.get("ovr", CM.BASE_OVR),
        "tier": career.get("tier", "Bronze"),
        "role": "ALL-ROUNDER",
        "chips": [f"{ms.get('label', '')} BAT".strip().upper(),
                  f"{bt.get('label', '')} BOWL".strip().upper()],
        "attributes": [(k.upper(), int(a.get(k, 0))) for k in CM.ATTRS],
        "coins": career.get("coins", 0),
        "debut_done": bool(career.get("debut_done")),
        "premium": CM.career_is_premium(career),
        "batting": {
            "matches": bat.get("matches", 0), "runs": bat.get("runs", 0),
            "hs": bat.get("hs", 0), "fifties": bat.get("fifties", 0),
            "hundreds": bat.get("hundreds", 0),
            "avg": (bat.get("runs", 0) / outs) if outs else None,
            "sr": (bat.get("runs", 0) / balls * 100) if balls else 0.0,
            "innings": innings,
        },
        "bowling": {
            "wickets": bowl.get("wickets", 0),
            "best": (f"{bowl.get('best_w', 0)}/{bowl.get('best_r', 0)}"
                     if bowl.get("best_w") else "-"),
            "econ": (bowl.get("runs", 0) / b_balls * 6) if b_balls else 0.0,
            "overs": f"{b_balls // 6}.{b_balls % 6}",
        },
        # Phase 3 fields - absent on every career document until condition ships,
        # so the card must treat them as optional rather than assume defaults.
        "form": form.get("rating"),
        "fitness": fitness.get("value"),
        "injury": (fitness.get("injury") or {}).get("type"),
        "club": (career.get("contract") or {}).get("club"),
        "season": career.get("season_no"),
    }


def build_broadcast_state(match, career=None):
    """CricketMatch -> plain dict for career/ui. Never raises on a partial match."""
    innings = match.current_innings
    if innings is None:
        return _pregame_state(match)
    balls = innings.total_balls
    balls_left = max(0, match.max_balls - balls)
    crr = (innings.total_runs / balls * 6) if balls else 0.0

    bat_team, bowl_team = innings.batting_team, innings.bowling_team
    first_idx = 0 if bat_team is getattr(match, "team1", None) else 1

    state = {
        "kind": match_kind(match),
        "format_overs": match.format_overs,
        "innings_num": match.current_innings_num,
        "pitch": getattr(match, "pitch", ""),
        "weather": getattr(match, "weather", ""),
        "batting": {"name": bat_team["name"], "color": _team_color(bat_team, first_idx),
                    "runs": innings.total_runs, "wickets": innings.wickets,
                    "balls": balls, "overs": _overs_str(balls), "extras": getattr(innings, "extras", 0)},
        "bowling": {"name": bowl_team["name"], "color": _team_color(bowl_team, 1 - first_idx)},
        "max_wickets": getattr(match, "max_wickets", 10),
        "crr": crr,
        "partnership": innings.partnership_runs,
        "batters": _batter_rows(innings),
        "bowler": _bowler_row(innings),
        "this_over": this_over(innings),
        "last_over": last_over(innings),
        "recent_overs": recent_overs(innings),
        "over_no": _current_over_no(innings),
        "balls_left": balls_left,
        "free_hit": bool(getattr(match, "free_hit", False)),
        "reviews": dict(getattr(match, "drs_reviews", {}) or {}),
        "last_ball": getattr(match, "last_ball", None),
        "objective": None,
        "target": None, "need": None, "rrr": None, "proj": None,
        "first_innings": None,
        "toss": None,
    }

    if match.current_innings_num == 2 and match.innings1:
        target = getattr(match, "target", match.innings1.total_runs + 1)
        need = target - innings.total_runs
        state["target"] = target
        state["need"] = max(0, need)
        state["rrr"] = (need / balls_left * 6) if balls_left > 0 and need > 0 else 0.0
        state["first_innings"] = {
            "name": match.innings1.batting_team["name"],
            "runs": match.innings1.total_runs, "wickets": match.innings1.wickets,
            "overs": _overs_str(match.innings1.total_balls),
        }
        state["dls"] = bool(getattr(match, "dls_active", False))
    else:
        state["proj"] = int(crr * match.format_overs)

    if match.toss_winner:
        winner = match.team1["name"] if match.toss_winner == match.p1_id else match.team2["name"]
        decision = "BAT" if match.batting_first_id == match.toss_winner else "BOWL"
        state["toss"] = f"{winner} won the toss · chose to {decision}"

    state["objective"] = _objective(match, innings, balls_left)

    if career:
        state["career"] = {
            "name": career.get("username", ""),
            "tier": career.get("tier", "Bronze"),
            "ovr": career.get("ovr", 0),
            "form": (career.get("form") or {}).get("rating"),
            "fitness": (career.get("fitness") or {}).get("value"),
        }
    return state
