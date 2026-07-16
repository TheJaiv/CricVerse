"""Custom tournament engine + setup wizard.

A "custom" tournament is driven entirely by a declarative config that the
creator builds through a wizard when they pick event type Custom at create:

    tourney["custom_config"] = {
        "stages": [
            # stage 1 defines the field; later stages derive their team count
            # from the previous stage's qualifiers.
            {"name": "Group Stage", "groups": 4, "teams_per_group": 4,
             "legs": 1, "qualify": 2, "assignment": "manual"|"random"},
            {"name": "Super Stage", "groups": 2, "legs": 1, "qualify": 2,
             "regroup": "seeded"|"random", "carry_points": False},
        ],
        "playoff": {
            "mode": "none" | "bracket",
            # bracket slots: S3 = seed 3, W2 / L2 = winner / loser of match 2.
            "matches": [
                {"n": 1, "t1": "S1", "t2": "S2", "label": "Qualifier 1",
                 "final": False, "depth": 1},
                ...
            ],
        },
    }

League matches are emitted with stage="group" (stage 1) / "stage2" / "stage3",
a group letter and an INTEGER round that keeps counting up across stages, so
the existing standings, conditions, match-order and status plumbing treats
them like any other group stage. Playoff matches use stage="knockout" with
the (possibly user-named) label as the round, and are added progressively as
their feeder results arrive - the same pattern as ipl_try_advance.

Seeds into the playoff are rank-major over the FINAL league stage:
seed 1 = A1, seed 2 = B1, ..., then A2, B2, ... (single group: table order).

Everything here is additive - no other tournament type ever enters this file.
"""

import random
import re

import discord

MAX_STAGES = 3          # league stages (someone will try 6)
MAX_GROUPS = 8
MAX_TEAMS_PER_GROUP = 16
MAX_TEAMS = 64
MAX_LEGS = 4
MAX_SEEDS = 16          # total playoff qualifiers
MAX_BRACKET_MATCHES = 32
MAX_TOTAL_MATCHES = 300
GROUP_LETTERS = "ABCDEFGH"

CUSTOM_TYPE_LABEL = "Custom Tournament"


# ---------------------------------------------------------------------------
# config helpers

def stage_key(idx):
    """Schedule stage tag for league stage idx. Stage 1 reuses "group" so every
    group-stage code path (standings, conditions, tie rules) works unchanged."""
    return "group" if idx == 0 else f"stage{idx + 1}"


def stage_index_of(skey):
    """Inverse of stage_key. None for non-league stages ("knockout")."""
    if skey == "group":
        return 0
    m = re.fullmatch(r"stage(\d+)", str(skey))
    return int(m.group(1)) - 1 if m else None


def default_stage_name(idx):
    return "Group Stage" if idx == 0 else f"Stage {idx + 1}"


def stage_name(cfg, idx):
    st = cfg["stages"][idx]
    return st.get("name") or default_stage_name(idx)


def stage_letters(st):
    return GROUP_LETTERS[:st["groups"]]


def stage_incoming(cfg, idx):
    """How many teams enter league stage idx."""
    if idx == 0:
        return cfg["stages"][0]["groups"] * cfg["stages"][0]["teams_per_group"]
    prev = cfg["stages"][idx - 1]
    return prev["groups"] * prev["qualify"]


def stage_tpg(cfg, idx):
    """Teams per group in stage idx (derived for later stages)."""
    if idx == 0:
        return cfg["stages"][0]["teams_per_group"]
    return stage_incoming(cfg, idx) // cfg["stages"][idx]["groups"]


def total_qualifiers(cfg):
    last = cfg["stages"][-1]
    return last["groups"] * last["qualify"]


def custom_total_matches(cfg):
    total = 0
    for idx, st in enumerate(cfg["stages"]):
        tpg = stage_tpg(cfg, idx)
        total += st["groups"] * (tpg * (tpg - 1) // 2) * st["legs"]
    po = cfg.get("playoff") or {}
    total += len(po.get("matches") or [])
    return total


def custom_config_errors(cfg):
    """Structural sanity of a config, independent of the registered teams.
    Returns a list of problems (empty = valid). The wizard can only produce
    valid configs, but /start re-checks in case one was hand-edited."""
    errs = []
    stages = cfg.get("stages") or []
    if not stages:
        return ["No league stages configured."]
    if len(stages) > MAX_STAGES:
        errs.append(f"At most {MAX_STAGES} league stages allowed.")
    for idx, st in enumerate(stages):
        nm = stage_name(cfg, idx)
        g = st.get("groups", 0)
        if not 1 <= g <= MAX_GROUPS:
            errs.append(f"{nm}: groups must be 1-{MAX_GROUPS}.")
            return errs   # sizes below would divide by garbage
        if not 1 <= st.get("legs", 0) <= MAX_LEGS:
            errs.append(f"{nm}: round robins must be 1-{MAX_LEGS}.")
        if idx == 0:
            if not 2 <= st.get("teams_per_group", 0) <= MAX_TEAMS_PER_GROUP:
                errs.append(f"{nm}: teams per group must be 2-{MAX_TEAMS_PER_GROUP}.")
        else:
            incoming = stage_incoming(cfg, idx)
            if incoming % g != 0:
                errs.append(f"{nm}: {incoming} qualifiers don't split into {g} equal groups.")
                return errs
            if stage_tpg(cfg, idx) < 2:
                errs.append(f"{nm}: needs at least 2 teams per group.")
        tpg = stage_tpg(cfg, idx)
        q = st.get("qualify", 0)
        is_last = idx == len(stages) - 1
        po = cfg.get("playoff") or {"mode": "none"}
        if is_last and po.get("mode") == "none":
            if q != 0:
                errs.append(f"{nm}: a league-only final stage should have qualify=0.")
            if g != 1:
                errs.append(f"{nm}: 'league table decides the champion' needs a single group.")
        else:
            if not 1 <= q < tpg:
                errs.append(f"{nm}: qualifiers per group must be 1-{tpg - 1}.")
    if stage_incoming(cfg, 0) > MAX_TEAMS:
        errs.append(f"Total teams can't exceed {MAX_TEAMS}.")
    po = cfg.get("playoff") or {"mode": "none"}
    if po.get("mode") == "bracket":
        n_seeds = total_qualifiers(cfg)
        if n_seeds > MAX_SEEDS:
            errs.append(f"At most {MAX_SEEDS} teams can qualify for the playoffs.")
        _, err = _check_bracket(po.get("matches") or [], n_seeds)
        if err:
            errs.append(f"Playoff bracket: {err}")
    if custom_total_matches(cfg) > MAX_TOTAL_MATCHES:
        errs.append(f"{custom_total_matches(cfg)} total matches — the cap is {MAX_TOTAL_MATCHES}.")
    return errs


# ---------------------------------------------------------------------------
# bracket parsing / validation

_TOK_SEED = re.compile(r"(?:seed\s*|s)?(\d+)", re.I)
_TOK_REF = re.compile(r"(w|l)(?:inner|oser)?\s*(?:of\s*)?(?:match\s*|m)?(\d+)", re.I)


def _parse_token(raw):
    """One bracket slot -> normalized token 'S3' / 'W2' / 'L1', or None."""
    s = raw.strip()
    m = _TOK_REF.fullmatch(s)
    if m:
        return f"{m.group(1).upper()}{int(m.group(2))}"
    m = _TOK_SEED.fullmatch(s)
    if m:
        return f"S{int(m.group(1))}"
    return None


def parse_custom_bracket(text, n_seeds):
    """Parse the bracket mini-language (one match per line) into match dicts.

        1 vs 2
        3 vs 4
        w2 vs l1
        final: w3 vs w1

    Line number = match number. Also accepted: a `matchN:`/`N:` prefix (which
    must agree with the line's position), `wmatch2`, `v` for `vs`, and a
    `(final)` tag in the prefix. Returns (matches, None) or (None, error).
    """
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    if not lines:
        return None, "The bracket is empty — one match per line."
    if len(lines) > MAX_BRACKET_MATCHES:
        return None, f"At most {MAX_BRACKET_MATCHES} playoff matches."
    matches = []
    for i, ln in enumerate(lines, 1):
        body, is_final = ln, False
        # optional prefixes: "final:", "match4:", "4:", "match4(final):"
        m = re.match(r"^final\s*[:\-]\s*(.*)$", body, re.I)
        if m:
            is_final, body = True, m.group(1)
        else:
            # "match4:", "4.", "match4(final):" - the separator is required, so a
            # bare "1 vs 2" line is never mistaken for a prefix.
            m = re.match(r"^(?:match\s*)?(\d+)\s*(\(\s*final\s*\))?\s*[:.)]\s*(.*)$", body, re.I)
            if m:
                if int(m.group(1)) != i:
                    return None, f"Line {i}: labelled `match {m.group(1)}` but it's line {i} — match number = line number."
                is_final, body = bool(m.group(2)), m.group(3)
        parts = re.split(r"\s+vs?\s+|\s+v\s+", body, flags=re.I)
        if len(parts) != 2:
            return None, f"Line {i}: expected `<slot> vs <slot>`, got `{ln}`."
        t1, t2 = _parse_token(parts[0]), _parse_token(parts[1])
        if not t1 or not t2:
            bad = parts[0] if not t1 else parts[1]
            return None, (f"Line {i}: can't read `{bad.strip()}` — use a seed number, "
                          f"`w<match>` or `l<match>` (e.g. `3`, `w2`, `l1`).")
        matches.append({"n": i, "t1": t1, "t2": t2, "final": is_final})
    matches, err = _check_bracket(matches, n_seeds)
    if err:
        return None, err
    return matches, None


def _check_bracket(matches, n_seeds):
    """The 6 rules that make a bracket converge on one champion. On success the
    matches come back with `depth` filled (longest feeder chain, for ranking)."""
    if not matches:
        return None, "no matches."
    seeds_used, w_used, l_used = {}, {}, {}
    depth = {}
    for m in matches:
        i = m["n"]
        for tok in (m["t1"], m["t2"]):
            if tok[0] == "S":
                k = int(tok[1:])
                if not 1 <= k <= n_seeds:
                    return None, f"Match {i}: seed {k} doesn't exist — {n_seeds} teams qualify."
                if k in seeds_used:
                    return None, f"Seed {k} enters twice (matches {seeds_used[k]} and {i})."
                seeds_used[k] = i
            else:
                ref = int(tok[1:])
                if ref >= i:
                    return None, f"Match {i}: references match {ref} — only EARLIER matches allowed."
                used = w_used if tok[0] == "W" else l_used
                if ref in used:
                    who = "winner" if tok[0] == "W" else "loser"
                    return None, f"Match {ref}'s {who} is used twice (matches {used[ref]} and {i})."
                used[ref] = i
        if m["t1"] == m["t2"]:
            return None, f"Match {i}: both slots are `{m['t1']}`."
        d1 = depth.get(int(m["t1"][1:]), 0) if m["t1"][0] != "S" else 0
        d2 = depth.get(int(m["t2"][1:]), 0) if m["t2"][0] != "S" else 0
        depth[i] = 1 + max(d1, d2)
    missing = [k for k in range(1, n_seeds + 1) if k not in seeds_used]
    if missing:
        return None, f"Seed(s) {', '.join(map(str, missing))} never enter the bracket."
    finals = [m for m in matches if m.get("final")]
    if len(finals) != 1:
        return None, ("exactly one line must be marked `final:` — it decides the champion."
                      if not finals else "only ONE match can be the final.")
    if finals[0]["n"] != matches[-1]["n"]:
        return None, "the `final:` match must be the LAST line."
    for m in matches[:-1]:
        if m["n"] not in w_used:
            return None, (f"Match {m['n']}'s winner goes nowhere — every winner except "
                          f"the final's must feed a later match.")
    for m in matches:
        m["depth"] = depth[m["n"]]
    return matches, None


def default_knockout_bracket(n_seeds):
    """The standard seeded single-elimination bracket for 2/4/8/16 qualifiers."""
    if n_seeds == 2:
        return [{"n": 1, "t1": "S1", "t2": "S2", "label": "Final", "final": True, "depth": 1}]
    if n_seeds == 4:
        return [
            {"n": 1, "t1": "S1", "t2": "S4", "label": "Semi-Final 1", "final": False, "depth": 1},
            {"n": 2, "t1": "S2", "t2": "S3", "label": "Semi-Final 2", "final": False, "depth": 1},
            {"n": 3, "t1": "W1", "t2": "W2", "label": "Final", "final": True, "depth": 2},
        ]
    if n_seeds == 8:
        pairs = [(1, 8), (4, 5), (2, 7), (3, 6)]
        ms = [{"n": i + 1, "t1": f"S{a}", "t2": f"S{b}", "label": f"Quarter-Final {i + 1}",
               "final": False, "depth": 1} for i, (a, b) in enumerate(pairs)]
        ms += [
            {"n": 5, "t1": "W1", "t2": "W2", "label": "Semi-Final 1", "final": False, "depth": 2},
            {"n": 6, "t1": "W3", "t2": "W4", "label": "Semi-Final 2", "final": False, "depth": 2},
            {"n": 7, "t1": "W5", "t2": "W6", "label": "Final", "final": True, "depth": 3},
        ]
        return ms
    if n_seeds == 16:
        pairs = [(1, 16), (8, 9), (4, 13), (5, 12), (2, 15), (7, 10), (3, 14), (6, 11)]
        ms = [{"n": i + 1, "t1": f"S{a}", "t2": f"S{b}", "label": f"Round of 16 — Match {i + 1}",
               "final": False, "depth": 1} for i, (a, b) in enumerate(pairs)]
        ms += [{"n": 8 + j + 1, "t1": f"W{2 * j + 1}", "t2": f"W{2 * j + 2}",
                "label": f"Quarter-Final {j + 1}", "final": False, "depth": 2} for j in range(4)]
        ms += [
            {"n": 13, "t1": "W9", "t2": "W10", "label": "Semi-Final 1", "final": False, "depth": 3},
            {"n": 14, "t1": "W11", "t2": "W12", "label": "Semi-Final 2", "final": False, "depth": 3},
            {"n": 15, "t1": "W13", "t2": "W14", "label": "Final", "final": True, "depth": 4},
        ]
        return ms
    return None


def default_bracket_labels(matches):
    """Fill missing labels: 'Playoff Match N', the final becomes 'Final'."""
    for m in matches:
        if not m.get("label"):
            m["label"] = "Final" if m.get("final") else f"Playoff Match {m['n']}"
    return matches


def seed_table_lines(cfg):
    """Human seed map shown before bracket entry and on the confirm screen."""
    last = cfg["stages"][-1]
    letters = stage_letters(last)
    lines, s = [], 1
    for rank in range(1, last["qualify"] + 1):
        for g in letters:
            src = f"#{rank} of the table" if last["groups"] == 1 else f"{g}{rank} (Group {g} #{rank})"
            lines.append(f"seed {s} = {src}")
            s += 1
    return lines


# ---------------------------------------------------------------------------
# standings (carry-over aware)

_CARRY_FIELDS = ("P", "W", "L", "T", "Pts", "RF", "OF", "RA", "OA")


def custom_stage_standings(tourney, idx, letter):
    """get_group_standings for one custom stage/group, plus any carried points
    from the previous stage, re-sorted on Pts then NRR."""
    from league.tournament_manager import get_group_standings
    rows = {n: d for n, d in get_group_standings(tourney, stage_key(idx), letter) if n != "BYE"}
    carry = (tourney.get("custom_carry") or {}).get(stage_key(idx)) or {}
    for team, add in carry.items():
        if team in rows:
            for f in _CARRY_FIELDS:
                rows[team][f] = rows[team].get(f, 0) + add.get(f, 0)
    for d in rows.values():
        d["NRR"] = ((d["RF"] / d["OF"]) if d["OF"] > 0 else 0) - ((d["RA"] / d["OA"]) if d["OA"] > 0 else 0)
    return sorted(rows.items(), key=lambda x: (x[1]["Pts"], x[1]["NRR"]), reverse=True)


def _stage_qualifiers(tourney, idx):
    """Rank-major qualifier list off stage idx: [(team, 'A'), ...] ordered
    A1, B1, C1, ..., A2, B2, ... — the seed order."""
    cfg = tourney["custom_config"]
    st = cfg["stages"][idx]
    per_group = {}
    for g in stage_letters(st):
        rows = custom_stage_standings(tourney, idx, g)
        if len(rows) < st["qualify"]:
            return None
        per_group[g] = [n for n, _ in rows[:st["qualify"]]]
    out = []
    for rank in range(st["qualify"]):
        for g in stage_letters(st):
            out.append((per_group[g][rank], g))
    return out


# ---------------------------------------------------------------------------
# schedule generation

def _gen_stage_matches(tourney, idx, teams_by_group):
    """Append one league stage's fixtures: per-group circle-method rounds x legs,
    interleaved so every global round has at most one match per team. Round
    numbers continue from the schedule's current max so labels stay unique and
    the strict-round match-order gate keeps working across stages."""
    from league.tournament_manager import _circle_rounds, _tm_next_mid
    st = tourney["custom_config"]["stages"][idx]
    sched = tourney["schedule"]
    base_round = max((m["round"] for m in sched if isinstance(m.get("round"), int)), default=0)

    rounds_by_group = {}
    for g, teams in teams_by_group.items():
        base = list(teams)
        random.shuffle(base)
        leg1 = _circle_rounds(base)
        all_rounds = []
        for leg in range(st["legs"]):
            for pairs in leg1:
                all_rounds.append([(b, a) if leg % 2 else (a, b) for a, b in pairs])
        rounds_by_group[g] = all_rounds

    n_rounds = max(len(r) for r in rounds_by_group.values())
    skey = stage_key(idx)
    for r in range(n_rounds):
        rnd = []
        for g in sorted(rounds_by_group):
            if r < len(rounds_by_group[g]):
                rnd.extend((g, a, b) for a, b in rounds_by_group[g][r])
        random.shuffle(rnd)
        for g, a, b in rnd:
            sched.append({"match_id": _tm_next_mid(tourney), "round": base_round + r + 1,
                          "stage": skey, "group": g, "team1": a, "team2": b,
                          "status": "pending", "result": None})


def custom_generate_first_stage(tourney):
    """Build stage-1 groups (manual letters or a random draw) and its fixtures.
    Returns an error string, or None on success. Random draws also stamp the
    drawn letter onto each team dict so rosters read the same as manual ones."""
    cfg = tourney.get("custom_config")
    if not cfg:
        return "❌ No custom config — run the setup wizard (`/tournament create` → Custom)."
    st = cfg["stages"][0]
    letters = stage_letters(st)
    teams_by_group = {g: [] for g in letters}
    if st.get("assignment") == "manual":
        for t in tourney["teams"]:
            g = t.get("group")
            if g not in teams_by_group:
                return f"❌ **{t['name']}** has no valid group — assign one of {'/'.join(letters)}."
            teams_by_group[g].append(t["name"])
        for g in letters:
            if len(teams_by_group[g]) != st["teams_per_group"]:
                return (f"❌ Group **{g}** has **{len(teams_by_group[g])}** teams — "
                        f"needs exactly **{st['teams_per_group']}**.")
    else:
        names = [t["name"] for t in tourney["teams"]]
        random.shuffle(names)
        for i, n in enumerate(names):
            teams_by_group[letters[i // st["teams_per_group"]]].append(n)
        for t in tourney["teams"]:
            t["group"] = next(g for g, ts in teams_by_group.items() if t["name"] in ts)
    tourney.setdefault("custom_groups", {})[stage_key(0)] = teams_by_group
    _gen_stage_matches(tourney, 0, teams_by_group)
    return None


def _regroup(qualifiers, n_groups, mode):
    """Split the rank-major qualifier list [(team, old_group), ...] into new
    groups. 'seeded' snakes the seed order across the groups, then swaps
    same-rank teams between groups to pull old group-mates apart (best effort
    - like the real Super 8's fixed slots). 'random' is a fresh draw."""
    if mode == "random":
        pool = [t for t, _ in qualifiers]
        random.shuffle(pool)
        size = len(pool) // n_groups
        return {GROUP_LETTERS[i]: pool[i * size:(i + 1) * size] for i in range(n_groups)}

    groups = [[] for _ in range(n_groups)]
    origin = [[] for _ in range(n_groups)]
    for i, (team, old_g) in enumerate(qualifiers):
        block, pos = divmod(i, n_groups)
        gi = pos if block % 2 == 0 else n_groups - 1 - pos   # snake
        groups[gi].append(team)
        origin[gi].append(old_g)

    def conflicts(gi):
        return len(origin[gi]) - len(set(origin[gi]))

    for gi in range(n_groups):
        for slot in range(len(groups[gi])):
            if conflicts(gi) == 0:
                break
            for gj in range(n_groups):
                if gj == gi or slot >= len(groups[gj]):
                    continue
                before = conflicts(gi) + conflicts(gj)
                origin[gi][slot], origin[gj][slot] = origin[gj][slot], origin[gi][slot]
                groups[gi][slot], groups[gj][slot] = groups[gj][slot], groups[gi][slot]
                if conflicts(gi) + conflicts(gj) < before:
                    break   # keep the swap
                origin[gi][slot], origin[gj][slot] = origin[gj][slot], origin[gi][slot]
                groups[gi][slot], groups[gj][slot] = groups[gj][slot], groups[gi][slot]

    return {GROUP_LETTERS[i]: groups[i] for i in range(n_groups)}


def _gen_next_stage(tourney, idx):
    """Generate league stage idx from stage idx-1's qualifiers (regroup rule +
    optional points carry-over), then its fixtures."""
    cfg = tourney["custom_config"]
    st = cfg["stages"][idx]
    qualifiers = _stage_qualifiers(tourney, idx - 1)
    if not qualifiers:
        return
    teams_by_group = _regroup(qualifiers, st["groups"], st.get("regroup", "seeded"))
    tourney.setdefault("custom_groups", {})[stage_key(idx)] = teams_by_group
    if st.get("carry_points"):
        carry = {}
        prev = cfg["stages"][idx - 1]
        for g in stage_letters(prev):
            for name, d in custom_stage_standings(tourney, idx - 1, g):
                if any(name in ts for ts in teams_by_group.values()):
                    carry[name] = {f: d.get(f, 0) for f in _CARRY_FIELDS}
        tourney.setdefault("custom_carry", {})[stage_key(idx)] = carry
    _gen_stage_matches(tourney, idx, teams_by_group)


# ---------------------------------------------------------------------------
# advancement engine

def _compute_seeds(tourney):
    q = _stage_qualifiers(tourney, len(tourney["custom_config"]["stages"]) - 1)
    return [t for t, _ in q] if q else None


def _bracket_match(sched, n):
    return next((m for m in sched if m.get("bracket_n") == n), None)


def _resolve_slot(tok, seeds, sched, po_matches):
    """Token -> (team or None, source label). Seeds resolve immediately; W/L
    resolve once the referenced bracket match has a result."""
    if tok[0] == "S":
        k = int(tok[1:])
        return seeds[k - 1], f"Seed {k}"
    ref = int(tok[1:])
    label = next((bm["label"] for bm in po_matches if bm["n"] == ref), f"Match {ref}")
    who = "Winner" if tok[0] == "W" else "Loser"
    m = _bracket_match(sched, ref)
    if not (m and m.get("status") == "completed" and m.get("result")):
        return None, f"{who} · {label}"
    w = m["result"]["winner"]
    l = m["result"].get("loser") or (m["team2"] if w == m["team1"] else m["team1"])
    return (w if tok[0] == "W" else l), f"{who} · {m['round']}"


def custom_try_advance(tourney):
    """Called after every completed match: generate the next league stage the
    moment the previous finishes, then feed the playoff bracket progressively,
    then crown the champion. Idempotent - safe to call any number of times."""
    cfg = tourney.get("custom_config")
    if not cfg:
        return
    sched = tourney["schedule"]
    from league.tournament_manager import _tm_next_mid

    for idx in range(len(cfg["stages"])):
        ms = [m for m in sched if m.get("stage") == stage_key(idx)]
        if not ms:
            if idx == 0:
                return   # stage 1 is created at /start, never here
            _gen_next_stage(tourney, idx)
            return
        if any(m["status"] != "completed" for m in ms):
            return

    # every league stage is done
    po = cfg.get("playoff") or {"mode": "none"}
    last = len(cfg["stages"]) - 1

    if po.get("mode") != "bracket":
        rows = custom_stage_standings(tourney, last, stage_letters(cfg["stages"][last])[0])
        if rows and tourney.get("status") != "completed":
            tourney["custom_champion"] = rows[0][0]
            if len(rows) > 1:
                tourney["custom_runner_up"] = rows[1][0]
            tourney["status"] = "completed"
        return

    seeds = tourney.get("custom_seeds")
    if not seeds:
        seeds = _compute_seeds(tourney)
        if not seeds or len(seeds) < total_qualifiers(cfg):
            return
        tourney["custom_seeds"] = seeds

    po_matches = default_bracket_labels([dict(m) for m in po["matches"]])
    added = True
    while added:   # one result can unlock a chain (e.g. a bye-like W-only line)
        added = False
        for bm in po_matches:
            if _bracket_match(sched, bm["n"]):
                continue
            t1, src1 = _resolve_slot(bm["t1"], seeds, sched, po_matches)
            t2, src2 = _resolve_slot(bm["t2"], seeds, sched, po_matches)
            if not (t1 and t2):
                continue
            sched.append({"match_id": _tm_next_mid(tourney), "round": bm["label"],
                          "stage": "knockout", "bracket_n": bm["n"],
                          "ko_depth": bm.get("depth", 1), "final": bool(bm.get("final")),
                          "team1": t1, "team2": t2, "team1_src": src1, "team2_src": src2,
                          "status": "pending", "result": None})
            added = True

    fin = next((m for m in sched if m.get("stage") == "knockout" and m.get("final")), None)
    if fin and fin["status"] == "completed" and fin.get("result") and tourney.get("status") != "completed":
        w = fin["result"]["winner"]
        tourney["custom_champion"] = w
        tourney["custom_runner_up"] = (fin["result"].get("loser")
                                       or (fin["team2"] if w == fin["team1"] else fin["team1"]))
        tourney["status"] = "completed"


# ---------------------------------------------------------------------------
# revert / rank support

def custom_match_rank(m):
    """Dependency rank for revert: league stages count up from 0 in stage
    order, playoff matches sit above every league stage at 10 + bracket depth."""
    if m.get("stage") == "knockout":
        return 10 + m.get("ko_depth", 1)
    idx = stage_index_of(m.get("stage"))
    return idx if idx is not None else 0


def custom_revert_cleanup(tourney, m):
    """After a custom match is reopened: drop the not-yet-played matches that
    were built on it (later league stages regenerate from the fresh standings,
    bracket slots re-resolve) and clear the derived crowns/seeds. The caller's
    blockers guard has already refused if a later COMPLETED match exists.
    Returns the removed matches."""
    sched = tourney["schedule"]
    rank = custom_match_rank(m)
    removed = [x for x in sched
               if x is not m and custom_match_rank(x) > rank
               and x.get("status") in ("pending", "locked")]
    for x in removed:
        sched.remove(x)
    for k in ("custom_champion", "custom_runner_up"):
        tourney.pop(k, None)
    if m.get("stage") != "knockout":
        tourney.pop("custom_seeds", None)
        idx = stage_index_of(m.get("stage")) or 0
        for later in range(idx + 1, MAX_STAGES):
            skey = stage_key(later)
            if not any(x.get("stage") == skey for x in sched):
                (tourney.get("custom_groups") or {}).pop(skey, None)
                (tourney.get("custom_carry") or {}).pop(skey, None)
    return removed


# ---------------------------------------------------------------------------
# start validation

def custom_start_error(tourney):
    """Registration-complete check for /start. Returns an error string or None."""
    cfg = tourney.get("custom_config")
    if not cfg:
        return "❌ This custom tournament has no config — the setup wizard wasn't finished. `cvt force_delete` and recreate it."
    errs = custom_config_errors(cfg)
    if errs:
        return "❌ **Config problem:** " + " · ".join(errs)
    st = cfg["stages"][0]
    want = st["groups"] * st["teams_per_group"]
    have = len(tourney["teams"])
    if have != want:
        return (f"❌ This custom format needs exactly **{want}** teams "
                f"({st['groups']} × {st['teams_per_group']}) — currently {have}.")
    if st.get("assignment") == "manual":
        letters = stage_letters(st)
        counts = {g: 0 for g in letters}
        for t in tourney["teams"]:
            g = t.get("group")
            if g not in counts:
                return f"❌ **{t['name']}** needs a group ({'/'.join(letters)}) — re-add it with one."
            counts[g] += 1
        bad = [g for g, c in counts.items() if c != st["teams_per_group"]]
        if bad:
            detail = " · ".join(f"**{g}**: {counts[g]}/{st['teams_per_group']}" for g in letters)
            return f"❌ Groups aren't even — every group needs {st['teams_per_group']} teams. {detail}"
    return None


# ---------------------------------------------------------------------------
# display: summary, standings, status pages

def custom_config_summary_lines(cfg, *, include_seeds=True):
    """The whole pipeline as text - the wizard confirm screen and create
    announcements both use this."""
    lines = []
    for idx, st in enumerate(cfg["stages"]):
        tpg = stage_tpg(cfg, idx)
        rr = {1: "single", 2: "double", 3: "triple", 4: "quadruple"}[st["legs"]]
        grp = f"{st['groups']} groups of {tpg}" if st["groups"] > 1 else f"single league of {stage_incoming(cfg, idx)}"
        bits = [f"**{stage_name(cfg, idx)}:** {grp} · {rr} round robin"]
        if st.get("qualify"):
            bits.append(f"top {st['qualify']}{' per group' if st['groups'] > 1 else ''} advance")
        if idx == 0:
            bits.append("manual groups" if st.get("assignment") == "manual" else "random draw")
        else:
            bits.append("seeded split" if st.get("regroup", "seeded") == "seeded" else "random redraw")
            if st.get("carry_points"):
                bits.append("points carry over")
        lines.append(" · ".join(bits))
    po = cfg.get("playoff") or {"mode": "none"}
    if po.get("mode") == "bracket":
        ms = default_bracket_labels([dict(m) for m in po["matches"]])
        n = total_qualifiers(cfg)
        lines.append(f"**Playoffs:** {n} qualifiers → {len(ms)} matches")
        if include_seeds:
            lines.extend("· " + s for s in seed_table_lines(cfg))
        def slot(tok):
            if tok[0] == "S":
                return f"seed {tok[1:]}"
            ref = next(x["label"] for x in ms if x["n"] == int(tok[1:]))
            return f"{'W' if tok[0] == 'W' else 'L'}({ref})"
        for m in ms:
            lines.append(f"`{m['n']}.` **{m['label']}** — {slot(m['t1'])} vs {slot(m['t2'])}")
    else:
        lines.append("**No playoffs** — the final table decides the champion.")
    lines.append(f"📊 **{custom_total_matches(cfg)} matches** · **{stage_incoming(cfg, 0)} teams** total")
    return lines


def build_custom_standings_message(tourney):
    """Text points table(s) for a custom tournament: one block per group of
    every stage that has fixtures, with the qualifying places marked, plus the
    playoff picture. Returns an embed, or None before any match is played."""
    from league.tournament_manager import _standings_table
    cfg = tourney.get("custom_config")
    if not cfg:
        return None
    sched = tourney.get("schedule", [])
    if not any(m["status"] == "completed" for m in sched):
        return None
    embed = discord.Embed(title=f"🏆 {tourney['name']} — Points Table", color=discord.Color.gold())
    po = cfg.get("playoff") or {"mode": "none"}
    for idx, st in enumerate(cfg["stages"]):
        if not any(m.get("stage") == stage_key(idx) for m in sched):
            continue
        for g in stage_letters(st):
            rows = custom_stage_standings(tourney, idx, g)
            if not rows:
                continue
            cutoff = st.get("qualify") or None
            block = "```\n" + "\n".join(_standings_table(rows, cutoff)) + "\n```"
            name = stage_name(cfg, idx) + (f" — Group {g}" if st["groups"] > 1 else "")
            embed.add_field(name=name, value=block[:1024], inline=False)
    ko = [m for m in sched if m.get("stage") == "knockout"]
    if ko:
        lines = []
        for m in sorted(ko, key=lambda x: x.get("match_id", 0)):
            res = m.get("result")
            tail = f" → 🏆 **{res['winner']}**" if res else " *(pending)*"
            lines.append(f"**{m.get('round')}**: {m['team1']} vs {m['team2']}{tail}")
        embed.add_field(name="🔥 Playoffs", value="\n".join(lines)[:1024], inline=False)
    if tourney.get("custom_champion"):
        embed.description = f"👑 **CHAMPIONS: {tourney['custom_champion']}**"
    return embed


_CUSTOM_PAGE_SIZE = 10


def build_custom_status_pages(tourney):
    """(title, stage_type, group_key, matches) pages: each stage's groups
    (chunked so big double-RR groups don't overflow an embed) + Knockouts.
    stage_type carries the custom stage key so the status embed can attach
    that stage's standings to the page."""
    cfg = tourney.get("custom_config")
    sched = tourney.get("schedule", [])
    pages = []
    if not cfg:
        return pages
    for idx, st in enumerate(cfg["stages"]):
        skey = stage_key(idx)
        for g in stage_letters(st):
            ms = [m for m in sched if m.get("stage") == skey and m.get("group") == g]
            if not ms:
                continue
            base = stage_name(cfg, idx) + (f" — Group {g}" if st["groups"] > 1 else "")
            chunks = [ms[i:i + _CUSTOM_PAGE_SIZE] for i in range(0, len(ms), _CUSTOM_PAGE_SIZE)]
            for ci, chunk in enumerate(chunks):
                title = base if len(chunks) == 1 else f"{base} ({ci + 1}/{len(chunks)})"
                # standings only on the last chunk of a group page-set
                stype = skey if ci == len(chunks) - 1 else "flat"
                pages.append((title, stype, g, chunk))
    ko = [m for m in sched if m.get("stage") == "knockout"]
    if ko:
        pages.append(("Knockouts", "knockout", None, ko))
    return pages


def custom_stage_cutoff(tourney, skey):
    """Qualifier count for the stage a status page is showing (for the '→ top N
    advance' footer). 0 for a league-only final stage."""
    cfg = tourney.get("custom_config") or {}
    idx = stage_index_of(skey)
    if idx is None or idx >= len(cfg.get("stages", [])):
        return 0
    return cfg["stages"][idx].get("qualify") or 0


# ---------------------------------------------------------------------------
# the setup wizard

def _delete_configuring(server_id):
    """Remove a tournament that never finished the wizard."""
    from core.subscription_manager import DB_CACHE, async_save_tournament_to_bin
    before = DB_CACHE.get("tournaments", []) or []
    DB_CACHE["tournaments"] = [t for t in before
                               if not (str(t.get("server_id")) == str(server_id)
                                       and t.get("status") == "configuring")]
    if len(DB_CACHE["tournaments"]) != len(before):
        async_save_tournament_to_bin()


class CustomBracketModal(discord.ui.Modal, title="Custom Playoff Bracket"):
    def __init__(self, view):
        super().__init__()
        self.wizard = view
        n = total_qualifiers(view._cfg())
        example = "1 vs 2\n3 vs 4\nw2 vs l1\nfinal: w3 vs w1"
        self.bracket = discord.ui.TextInput(
            label=f"One match per line — {n} seeds qualify",
            style=discord.TextStyle.paragraph,
            placeholder=example, default=view.bracket_text or None,
            max_length=1500, required=True,
        )
        self.add_item(self.bracket)

    async def on_submit(self, interaction: discord.Interaction):
        text = str(self.bracket.value)
        self.wizard.bracket_text = text   # kept so a failed attempt can be edited, not retyped
        matches, err = parse_custom_bracket(text, total_qualifiers(self.wizard._cfg()))
        if err:
            return await interaction.response.send_message(
                f"❌ {err}\nPress **Enter bracket** again — your text was kept.", ephemeral=True)
        self.wizard.playoff = {"mode": "bracket", "matches": matches}
        self.wizard.phase = "names"
        await self.wizard.refresh(interaction)


class CustomNamesModal(discord.ui.Modal, title="Name the stages & matches"):
    def __init__(self, view):
        super().__init__()
        self.wizard = view
        self.stage_input = self.match_input = None
        if len(view.stages) > 1:
            self.stage_input = discord.ui.TextInput(
                label=f"Stage names — {len(view.stages)} lines",
                style=discord.TextStyle.paragraph,
                default="\n".join(stage_name(view._cfg(), i) for i in range(len(view.stages))),
                max_length=200, required=True,
            )
            self.add_item(self.stage_input)
        po = view.playoff or {}
        if po.get("mode") == "bracket":
            ms = default_bracket_labels([dict(m) for m in po["matches"]])
            self.match_input = discord.ui.TextInput(
                label=f"Playoff match names — {len(ms)} lines",
                style=discord.TextStyle.paragraph,
                default="\n".join(m["label"] for m in ms),
                max_length=1000, required=True,
            )
            self.add_item(self.match_input)

    async def on_submit(self, interaction: discord.Interaction):
        if self.stage_input:
            names = [ln.strip()[:30] for ln in str(self.stage_input.value).splitlines() if ln.strip()]
            if len(names) != len(self.wizard.stages):
                return await interaction.response.send_message(
                    f"❌ Need exactly {len(self.wizard.stages)} stage names (one per line).", ephemeral=True)
            for st, nm in zip(self.wizard.stages, names):
                st["name"] = nm
        if self.match_input:
            po = self.wizard.playoff
            labels = [ln.strip()[:30] for ln in str(self.match_input.value).splitlines() if ln.strip()]
            if len(labels) != len(po["matches"]):
                return await interaction.response.send_message(
                    f"❌ Need exactly {len(po['matches'])} match names (one per line).", ephemeral=True)
            # round labels double as bracket lookups - keep them unique
            seen = {}
            for i, lb in enumerate(labels):
                if lb.lower() in seen:
                    labels[i] = f"{lb} ({i + 1})"
                seen[labels[i].lower()] = True
            for m, lb in zip(po["matches"], labels):
                m["label"] = lb
        self.wizard.phase = "confirm"
        await self.wizard.refresh(interaction)


class CustomSetupView(discord.ui.View):
    """The create-time wizard. Walks: per stage - groups → (teams/group) →
    (regroup/carry) → round robins → qualifiers → next step - recursing into
    up to 3 league stages, then the playoff branch, naming, and a confirm
    screen that flips the tournament from 'configuring' to 'registration'."""

    def __init__(self, server_id, author_id, tourney_name):
        super().__init__(timeout=900)
        self.server_id = server_id
        self.author_id = author_id
        self.tourney_name = tourney_name
        self.message = None
        self.stages = []          # completed stage dicts
        self.cur = {}             # the stage being asked about
        self.playoff = None
        self.bracket_text = ""
        self.phase = "groups"
        self._build_items()

    # -- config assembly ----------------------------------------------------
    def _cfg(self):
        stages = self.stages + ([dict(self.cur)] if self.cur else [])
        return {"stages": stages, "playoff": self.playoff}

    def _stage_idx(self):
        return len(self.stages)

    def _incoming(self):
        if self._stage_idx() == 0:
            return None
        return self.stages[-1]["groups"] * self.stages[-1]["qualify"]

    # -- phase machinery -----------------------------------------------------
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("❌ Only the tournament creator can run this wizard.", ephemeral=True)
            return False
        return True

    def _options(self):
        """(placeholder, [(label, value, description), ...]) for select phases."""
        idx = self._stage_idx()
        if self.phase == "groups":
            if idx == 0:
                opts = [(f"{n} group{'s' if n > 1 else ''}", n,
                         "one single league" if n == 1 else f"teams split into {n} groups")
                        for n in (1, 2, 3, 4, 6, 8)]
            else:
                inc = self._incoming()
                valid = [d for d in range(1, MAX_GROUPS + 1)
                         if inc % d == 0 and inc // d >= 2]
                opts = [(f"{d} group{'s' if d > 1 else ''} of {inc // d}", d,
                         f"the {inc} qualifiers play as " +
                         ("one league" if d == 1 else f"{d} groups")) for d in valid]
            return f"Stage {idx + 1}: how many groups?", opts
        if self.phase == "tpg":
            g = self.cur["groups"]
            hi = min(MAX_TEAMS_PER_GROUP, MAX_TEAMS // g)
            opts = [(f"{n} per group ({g * n} teams total)" if g > 1 else f"{n} teams", n, None)
                    for n in range(2, hi + 1)][:25]
            return "How many teams per group?", opts
        if self.phase == "regroup":
            return "How are qualifiers placed into the new groups?", [
                ("Seeded split", "seeded", "spread so old group-mates avoid each other (like the Super 8)"),
                ("Random redraw", "random", "a fresh random draw"),
            ]
        if self.phase == "carry":
            return "Do points carry into this stage?", [
                ("Fresh table", 0, "everyone restarts on 0 points"),
                ("Carry points over", 1, "previous-stage points & NRR come along"),
            ]
        if self.phase == "legs":
            word = {1: "Single", 2: "Double", 3: "Triple", 4: "Quadruple"}
            return "How many round robins?", [
                (f"{word[n]} round robin", n,
                 "everyone meets once" if n == 1 else f"every pairing plays {n} times") for n in range(1, MAX_LEGS + 1)]
        if self.phase == "qualify":
            g, tpg = self.cur["groups"], self._cur_tpg()
            opts = []
            if g == 1:
                opts.append(("Nobody — league table decides the champion", 0,
                             "no playoffs, top of the table wins the title"))
            for q in range(1, tpg):
                total = q * g
                if total > MAX_SEEDS:
                    break
                if g == 1 and total < 2:
                    continue
                opts.append((f"Top {q}" + (" per group" if g > 1 else ""), q,
                             f"{total} team{'s' if total > 1 else ''} advance"))
            return "How many teams qualify from this stage?", opts[:25]
        if self.phase == "next":
            total = self.stages[-1]["groups"] * self.stages[-1]["qualify"]
            opts = [("Playoffs", "playoffs", f"the {total} qualifiers enter a knockout bracket")]
            if len(self.stages) < MAX_STAGES and total >= 4:
                opts.insert(0, ("Another schedule stage", "stage",
                                f"the {total} qualifiers play another group/league stage"))
            return "What happens to the qualified teams?", opts
        if self.phase == "playoff_mode":
            total = self.stages[-1]["groups"] * self.stages[-1]["qualify"]
            return "Playoff format?", [
                ("Simple knockout", "simple", f"standard seeded bracket for {total} teams"),
                ("Custom bracket", "custom", "type the matches yourself — any ladder you like"),
            ]
        return None, []

    def _cur_tpg(self):
        if self._stage_idx() == 0:
            return self.cur["teams_per_group"]
        return self._incoming() // self.cur["groups"]

    async def _on_select(self, interaction: discord.Interaction):
        val = self._select.values[0]
        idx = self._stage_idx()
        if self.phase == "groups":
            self.cur = {"groups": int(val)}
            if idx == 0:
                self.phase = "tpg"
            elif self.cur["groups"] == 1:
                self.cur["regroup"] = "seeded"   # one league - nothing to regroup
                self.phase = "carry"
            else:
                self.phase = "regroup"
        elif self.phase == "tpg":
            self.cur["teams_per_group"] = int(val)
            self.phase = "assign"
        elif self.phase == "assign":
            self.cur["assignment"] = val
            self.phase = "legs"
        elif self.phase == "regroup":
            self.cur["regroup"] = val
            self.phase = "carry"
        elif self.phase == "carry":
            self.cur["carry_points"] = bool(int(val))
            self.phase = "legs"
        elif self.phase == "legs":
            self.cur["legs"] = int(val)
            self.phase = "qualify"
        elif self.phase == "qualify":
            q = int(val)
            self.cur["qualify"] = q
            self.stages.append(dict(self.cur))
            self.cur = {}
            if q == 0:
                self.playoff = {"mode": "none"}
                self.phase = "names" if len(self.stages) > 1 else "confirm"
            else:
                self.phase = "next"
        elif self.phase == "next":
            if val == "stage":
                self.phase = "groups"
            else:
                total = self.stages[-1]["groups"] * self.stages[-1]["qualify"]
                if default_knockout_bracket(total):
                    self.phase = "playoff_mode"
                else:
                    self.playoff = {"mode": "bracket", "matches": None}
                    self.phase = "bracket"
        elif self.phase == "playoff_mode":
            if val == "simple":
                total = self.stages[-1]["groups"] * self.stages[-1]["qualify"]
                self.playoff = {"mode": "bracket", "matches": default_knockout_bracket(total)}
                self.phase = "names"
            else:
                self.playoff = {"mode": "bracket", "matches": None}
                self.phase = "bracket"
        await self.refresh(interaction)

    # special select for stage-1 assignment (kept out of _options for clarity)
    def _assign_options(self):
        return "How do teams get their group?", [
            ("Manual", "manual", "the manager passes a group letter on every add_team"),
            ("Random draw", "random", "groups are drawn randomly when the tournament starts"),
        ]

    # -- rendering ------------------------------------------------------------
    def _build_items(self):
        self.clear_items()
        if self.phase in ("groups", "tpg", "assign", "regroup", "carry", "legs", "qualify", "next", "playoff_mode"):
            ph, opts = self._assign_options() if self.phase == "assign" else self._options()
            # 1-group stages skip the assignment question - nothing to assign
            if self.phase == "assign" and self.cur.get("groups") == 1:
                self.cur["assignment"] = "random"
                self.phase = "legs"
                return self._build_items()
            sel = discord.ui.Select(placeholder=ph, min_values=1, max_values=1)
            for label, value, desc in opts:
                sel.add_option(label=str(label)[:100], value=str(value),
                               description=(desc or "")[:100] or None)
            sel.callback = self._on_select
            self._select = sel
            self.add_item(sel)
        elif self.phase == "bracket":
            btn = discord.ui.Button(label="📝 Enter bracket", style=discord.ButtonStyle.primary)
            btn.callback = self._open_bracket
            self.add_item(btn)
        elif self.phase == "names":
            ok = discord.ui.Button(label="✅ Use default names", style=discord.ButtonStyle.success)
            ok.callback = self._default_names
            self.add_item(ok)
            cus = discord.ui.Button(label="✏️ Customize names", style=discord.ButtonStyle.secondary)
            cus.callback = self._open_names
            self.add_item(cus)
        elif self.phase == "confirm":
            ok = discord.ui.Button(label="✅ Confirm — open registration", style=discord.ButtonStyle.success)
            ok.callback = self._confirm
            self.add_item(ok)
        restart = discord.ui.Button(label="🔁 Start over", style=discord.ButtonStyle.secondary)
        restart.callback = self._restart
        self.add_item(restart)
        cancel = discord.ui.Button(label="❌ Cancel setup", style=discord.ButtonStyle.danger)
        cancel.callback = self._cancel
        self.add_item(cancel)

    def _embed(self):
        e = discord.Embed(title=f"🛠️ Custom Tournament Setup — {self.tourney_name}",
                          color=discord.Color.blurple())
        done = []
        if self.stages:
            tmp_cfg = {"stages": self.stages, "playoff": self.playoff}
            for i in range(len(self.stages)):
                st = self.stages[i]
                tpg = stage_tpg(tmp_cfg, i)
                rr = {1: "1×", 2: "2×", 3: "3×", 4: "4×"}[st["legs"]]
                q = st.get("qualify")
                done.append(f"**{stage_name(tmp_cfg, i)}** — {st['groups']}×{tpg} teams · {rr} RR"
                            + (f" · top {q} advance" if q else " · table decides champion"))
        if done:
            e.add_field(name="So far", value="\n".join(done), inline=False)
        prompts = {
            "groups": "**How many groups in this stage?** (1 = one single league)",
            "tpg": "**How many teams per group?** — this fixes the total team count.",
            "assign": "**How do teams get their group?**",
            "regroup": "**How are the qualifiers regrouped for this stage?**",
            "carry": "**Fresh points table, or carry points from the previous stage?**",
            "legs": "**How many round robins?** (single / double / triple / quadruple)",
            "qualify": "**How many teams qualify out of this stage?**",
            "next": "**What do the qualifiers play next?**",
            "playoff_mode": "**Simple knockout, or your own custom bracket?**",
        }
        if self.phase in prompts:
            e.description = prompts[self.phase]
        elif self.phase == "bracket":
            n = total_qualifiers(self._cfg())
            seed_lines = "\n".join(seed_table_lines(self._cfg()))
            e.description = (
                f"**Type your own bracket** — {n} seeds, one match per line, "
                f"`w2` = winner of match 2, `l1` = loser of match 1, "
                f"mark the decider with `final:`.\n\n"
                f"**Your seeds:**\n```\n{seed_lines}\n```\n"
                "**Example (IPL-style ladder):**\n"
                "```\n1 vs 2\n3 vs 4\nw2 vs l1\nfinal: w3 vs w1\n```"
            )
        elif self.phase == "names":
            e.description = ("**Name your playoff matches** (and stages, if you built more than one) — "
                             "or keep the defaults (`Playoff Match 1`, …, `Final`).")
        elif self.phase == "confirm":
            cfg = self._cfg()
            errs = custom_config_errors(cfg)
            if errs:
                e.description = "❌ **Config problem:**\n" + "\n".join(f"• {x}" for x in errs) + \
                                "\n\nPress **Start over** to rebuild."
                e.color = discord.Color.red()
            else:
                e.description = ("**Review your format** — Confirm opens registration.\n\n"
                                 + "\n".join(custom_config_summary_lines(cfg)))
        return e

    async def refresh(self, interaction: discord.Interaction):
        self._build_items()
        await interaction.response.edit_message(embed=self._embed(), view=self)

    # -- buttons ---------------------------------------------------------------
    async def _open_bracket(self, interaction: discord.Interaction):
        await interaction.response.send_modal(CustomBracketModal(self))

    async def _open_names(self, interaction: discord.Interaction):
        await interaction.response.send_modal(CustomNamesModal(self))

    async def _default_names(self, interaction: discord.Interaction):
        if self.playoff and self.playoff.get("mode") == "bracket":
            self.playoff["matches"] = default_bracket_labels(self.playoff["matches"])
        self.phase = "confirm"
        await self.refresh(interaction)

    async def _restart(self, interaction: discord.Interaction):
        self.stages, self.cur, self.playoff = [], {}, None
        self.bracket_text = ""
        self.phase = "groups"
        await self.refresh(interaction)

    async def _cancel(self, interaction: discord.Interaction):
        _delete_configuring(self.server_id)
        self.stop()
        await interaction.response.edit_message(
            embed=discord.Embed(title="🛠️ Custom setup cancelled",
                                description="The tournament was deleted — `create` again any time.",
                                color=discord.Color.red()),
            view=None)

    async def _confirm(self, interaction: discord.Interaction):
        from league.tournament_manager import get_server_tournament, save_tournament
        cfg = self._cfg()
        if self.playoff and self.playoff.get("mode") == "bracket":
            self.playoff["matches"] = default_bracket_labels(self.playoff["matches"])
        errs = custom_config_errors(cfg)
        if errs:
            return await interaction.response.send_message(
                "❌ " + " · ".join(errs) + " — press **Start over**.", ephemeral=True)
        tourney = get_server_tournament(self.server_id)
        if not tourney or tourney.get("status") != "configuring":
            return await interaction.response.send_message(
                "❌ This setup is no longer valid (the tournament was deleted or already configured).", ephemeral=True)
        tourney["custom_config"] = cfg
        tourney["status"] = "registration"
        save_tournament(tourney)
        self.stop()
        st = cfg["stages"][0]
        want = st["groups"] * st["teams_per_group"]
        how = (f"`add_team \"<name>\" @owner <{'/'.join(stage_letters(st))}>` — group letter required"
               if st.get("assignment") == "manual" and st["groups"] > 1
               else "`add_team \"<name>\" @owner` — groups are drawn at start")
        e = discord.Embed(title=f"🏆 {self.tourney_name} — Custom format locked in!",
                          color=discord.Color.gold(),
                          description="\n".join(custom_config_summary_lines(cfg)))
        e.add_field(name="Next steps",
                    value=(f"1️⃣ {how} — **exactly {want} teams**\n"
                           "2️⃣ owners submit squads\n"
                           "3️⃣ `start` to generate the fixtures"),
                    inline=False)
        await interaction.response.edit_message(embed=e, view=None)

    async def on_timeout(self):
        _delete_configuring(self.server_id)
        if self.message:
            try:
                await self.message.edit(
                    embed=discord.Embed(title="🛠️ Custom setup expired",
                                        description="No activity for 15 minutes — the half-configured tournament was deleted.",
                                        color=discord.Color.red()),
                    view=None)
            except Exception:
                pass
