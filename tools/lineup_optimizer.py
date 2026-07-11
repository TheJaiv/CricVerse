"""
CricVerse Lineup Optimizer
==========================
Given a SQUAD (11+), pick the best XI and best batting order by Monte-Carlo
simulating thousands of matches across all 15 pitch conditions, then do the same
PER PITCH. The opponent is a FIXED balanced side (pace+spin) at +3/+2/same/-2/-3
OVR -- NOT a clone of your XI (a mirror copies your players, so strong picks and
pitch specialists cancel out and can't be selected for).

Why this is tractable (11! = 40M orders is impossible):
  1. ROLE CONSTRAINT -> the batting order is always
        [ specialist batters ] + [ all-rounders ] + [ bowlers ]
     so openers are always batters and bowlers are always tail (your rule).
     We only permute WITHIN the batter group and WITHIN the all-rounder group
     (tail order barely moves results), collapsing the space to a few hundred
     genuinely different line-ups.
  2. COARSE -> FINE FUNNEL -> rank every candidate cheaply (small N), then only
     re-simulate the top few at full N (>=100 per case).
  3. COMMON RANDOM NUMBERS -> every candidate faces the SAME sequence of
     opponent XIs / form draws, so the coarse ranking is paired (low variance),
     not RNG noise.

Give 11 players OR a bigger SQUAD: if you pass >11, the tool first picks the
best legal XI (keeper + bowling depth) and then optimises that XI's order.

Usage:
  - Edit MY_SQUAD below (11+ players), or call optimize(squad).
  - Run:  PYTHONDONTWRITEBYTECODE=1 python3 tools/lineup_optimizer.py
  - Flags: --odi --coarse-n N --fine-n N --fine-k K --max-cand M --procs P
           --stats-n N --toss-n N --select-n N --max-combos M --quick
"""
import argparse
import difflib
import itertools
import os
import random
import re
import sys
import time
from math import comb
from multiprocessing import Pool

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO_ROOT)
from tools.sim_harness import CricketMatch, run_full_match, build_team  # noqa: E402

# Master ratings list shipped in the repo (NAME BAT BOWL OVR ROLE ARCHETYPE).
RATINGS_FILE = os.path.join(_REPO_ROOT, "cricverse_players_ratings.txt")
_RATINGS_LINE = re.compile(r"^\s*\d+\s+(.+?)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\S+)\s+(\S+)\s*$")


def _key(s):
    return re.sub(r"[^a-z0-9]", "", s.lower())


def load_ratings(path=RATINGS_FILE):
    """Parse the ratings .txt into {normalised_name: player_dict}."""
    table = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            m = _RATINGS_LINE.match(line)
            if not m:
                continue
            name, bat, bowl, ovr, role, arch = m.groups()
            table[_key(name)] = {"name": name.strip(), "bat": int(bat),
                                 "bowl": int(bowl), "ovr": int(ovr),
                                 "role": role, "archetype": arch}
    return table


def resolve_squad(names, table=None, cutoff=0.82):
    """Turn a list of name strings into full player dicts via the ratings table.
    Fuzzy-matches near-misses; raises with the offending name if nothing is close."""
    if table is None:
        table = load_ratings()
    keys = list(table)
    out, missing = [], []
    for raw in names:
        k = _key(raw)
        if k in table:
            out.append(dict(table[k]))
            continue
        cm = difflib.get_close_matches(k, keys, n=1, cutoff=cutoff)
        if cm:
            out.append(dict(table[cm[0]]))
        else:
            sug = difflib.get_close_matches(k, keys, n=1, cutoff=0.0)
            hint = f"  (closest: {table[sug[0]]['name']})" if sug else ""
            missing.append(f"{raw!r}{hint}")
    if missing:
        raise ValueError("Could not resolve these squad names:\n   "
                         + "\n   ".join(missing))
    return out

# The 15 canonical pitches (must match tournament_manager.ALL_PITCHES).
PITCHES = ["Flat", "Green", "Dry", "Dusty", "Hard", "Soft", "Cracked", "Damp",
           "Dead", "Worn", "Turning", "Two-Paced", "Slow", "Bouncy", "Sticky"]
# Stable global index per pitch -> used for common-random-number seeds so a pitch
# gets the same seed whether it's evaluated alone or as part of the full set.
PITCH_IX = {p: i for i, p in enumerate(PITCHES)}

# Opponent tiers: per-player rating offset applied to a clone of YOUR XI.
# Ordered hardest -> easiest for the report columns.
TIERS = [("+3", +3), ("+2", +2), ("same", 0), ("-2", -2), ("-3", -3)]


# ─────────────────────────────────────────────────────────────────────────────
# Player / team helpers
# ─────────────────────────────────────────────────────────────────────────────
def _norm(p):
    """Accept capitalised CSV keys OR lowercase engine keys; return engine shape."""
    g = lambda *ks, d=None: next((p[k] for k in ks if k in p), d)
    ovr = g("ovr", "OVR")
    return {
        "name": g("name", "Name", d="Player"),
        "bat": int(g("bat", "Bat", d=50)),
        "bowl": int(g("bowl", "Bowl", d=20)),
        "ovr": int(ovr) if ovr is not None else None,
        "role": g("role", "Role", d="Batter"),
        "archetype": g("archetype", "Archetype", d="Standard"),
    }


def category(p):
    r = p["role"]
    if "All-Rounder" in r:
        return "AR"
    if "Bowler" in r:
        return "BWL"
    return "BAT"          # Batter, Batter_WK


def player_ovr(p):
    """Rank every player by their STRENGTH, never penalised for their weaker suit:
    a batsman by batting, a bowler by bowling, an all-rounder by their best of the
    two. For specialists this equals the ratings-file OVR; for all-rounders it
    (correctly) lifts them above the file's blended-down number."""
    cat = category(p)
    if cat == "AR":                       # all-rounder: judged by their best skill
        return max(p["bat"], p["bowl"])
    if p.get("ovr") is not None:          # specialist: file OVR == primary skill
        return p["ovr"]
    return p["bowl"] if cat == "BWL" else p["bat"]


def team_ovr(xi):
    return round(sum(player_ovr(p) for p in xi) / len(xi))


# Pitches that favour a bowling type (mirrors the engine's bowler pitch multipliers).
SPIN_PITCHES = {"Turning": 2.0, "Dusty": 1.5, "Worn": 1.5, "Slow": 1.4, "Dry": 1.3}
PACE_PITCHES = {"Green": 1.5, "Damp": 1.6, "Bouncy": 1.5, "Hard": 1.4}


def pitch_value(p, pitch):
    """OVR adjusted for a pitch: a spinner on a turning deck (or a pacer on a green
    one) is worth more than its raw OVR. Used ONLY to choose which XIs are worth
    simulating per pitch, so pitch-specialist XIs aren't cut by a pitch-blind OVR
    prefilter. The simulation still decides the actual winner."""
    v = float(player_ovr(p))
    role = p["role"]
    if pitch in SPIN_PITCHES and "Spin" in role:
        v += (SPIN_PITCHES[pitch] - 1) * 0.10 * p["bowl"]
    elif pitch in PACE_PITCHES and "Pace" in role and category(p) != "BAT":
        v += (PACE_PITCHES[pitch] - 1) * 0.10 * p["bowl"]
    return v


# Match perks (both sides get them, symmetrically). Captain = highest-OVR player
# in the XI gets +1 bat & bowl every match; impact player = a 12th man the engine
# AI subs in from the bench at over breaks.
CAPTAIN_ON = True
IMPACT_ON = True
BENCH_SIZE = 4          # how many leftover squad players sit on the impact bench


def _captain_of(players):
    """The captain = the XI's highest-OVR batter/all-rounder (captains aren't tail
    bowlers). Falls back to highest-OVR overall if somehow no bat/AR exists."""
    if not players:
        return None
    cands = [p for p in players if category(p) in ("BAT", "AR")] or players
    return max(cands, key=player_ovr)


def _apply_captain(players, captain_name=None):
    """+1 bat & bowl to the captain (mutates the given match-copy list). Defaults to
    the highest-OVR player; pass `captain_name` to boost a specific player."""
    if not (CAPTAIN_ON and players):
        return
    cap = None
    if captain_name:
        cap = next((p for p in players if p["name"] == captain_name), None)
    if cap is None:
        cap = _captain_of(players)
    cap["bat"] = min(99, cap["bat"] + 1)
    cap["bowl"] = min(99, cap["bowl"] + 1)


def _impact_on(format_overs):
    """Impact player is a T20-only rule — never in ODIs (or other formats)."""
    return IMPACT_ON and format_overs == 20


def _my_bench(order, squad, format_overs=20):
    """Impact bench = the best leftover SPECIALISTS not in this XI. Empty unless it's
    a T20 (impact is a T20-only rule). All-rounders are excluded on purpose: an AR
    already bats and bowls across both innings, so using one wastes the slot."""
    if not (_impact_on(format_overs) and squad):
        return []
    names = {p["name"] for p in order}
    rest = sorted((p for p in squad if p["name"] not in names and category(p) != "AR"),
                  key=lambda p: -player_ovr(p))
    return [dict(p) for p in rest[:BENCH_SIZE]]


def _build_opponent(opp_ovr, format_overs=20):
    """A fresh balanced opponent at `opp_ovr`, with the SAME perks as us: a captain
    +1, and (T20 only) an impact bench."""
    opp = build_team("OPP", opp_ovr, opp_ovr)
    _apply_captain(opp["players"])
    if _impact_on(format_overs):
        opp["subs"] = [
            {"name": "OPP_IMP_BAT", "bat": min(99, opp_ovr), "bowl": 35,
             "role": "Batter", "archetype": "Finisher"},
            {"name": "OPP_IMP_BWL", "bat": 30, "bowl": min(99, opp_ovr),
             "role": "Bowler_Pace", "archetype": "Standard"},
        ]
    return opp


# The "varied field" a home pitch must beat: opponents of different styles.
FIELD_KINDS = ["balanced", "spin", "pace", "bat"]


def _variant_opponent(opp_ovr, kind, format_overs=20):
    """A fixed opponent of a given STYLE at `opp_ovr` (with the same perks as us).
    Used to test whether a pitch suits us against a varied field, not just one
    balanced side: spin-strong/pace-strong sides neutralise turning/green decks."""
    opp = build_team("OPP", opp_ovr, opp_ovr)
    for p in opp["players"]:
        c = category(p)
        if kind == "spin" and c in ("BWL", "AR"):
            p["role"] = "All-Rounder_Spin_Off" if c == "AR" else "Bowler_Spin_Off"
        elif kind == "pace" and c in ("BWL", "AR"):
            p["role"] = "All-Rounder_Pace" if c == "AR" else "Bowler_Pace"
        elif kind == "bat" and c == "BAT":
            p["bat"] = min(99, p["bat"] + 5)
    _apply_captain(opp["players"])
    if _impact_on(format_overs):
        bowl_role = "Bowler_Spin_Off" if kind == "spin" else "Bowler_Pace"
        opp["subs"] = [
            {"name": "OPP_IMP_BAT", "bat": min(99, opp_ovr), "bowl": 35,
             "role": "Batter", "archetype": "Finisher"},
            {"name": "OPP_IMP_BWL", "bat": 30, "bowl": min(99, opp_ovr),
             "role": bowl_role, "archetype": "Standard"},
        ]
    return opp


def _default_order(xi):
    """A fixed, sensible batting order: batters then all-rounders then bowlers,
    each group by batting ability descending. Used to seed selection candidates."""
    bats = sorted((p for p in xi if category(p) == "BAT"), key=lambda p: -p["bat"])
    ars = sorted((p for p in xi if category(p) == "AR"), key=lambda p: -p["bat"])
    bwls = sorted((p for p in xi if category(p) == "BWL"), key=lambda p: -p["bat"])
    return bats + ars + bwls


# ─────────────────────────────────────────────────────────────────────────────
# Candidate batting-order generation (role-constrained)
# ─────────────────────────────────────────────────────────────────────────────
def _archetype_seed(bats):
    """A cricket-sensible opener pairing: Aggressor + Anchor up top, Finishers low."""
    prio = {"Aggressor": 0, "Anchor": 1, "Standard": 2, "Finisher": 3}
    return sorted(bats, key=lambda p: (prio.get(p["archetype"], 2), -p["bat"]))


def generate_candidates(xi, max_cand, rng):
    """Return a list of distinct batting orders (each a list of 11 player dicts).

    Constraint: ONLY bowlers are locked to the tail. Batters AND all-rounders form
    one 'top group' that is permuted freely, so an all-rounder can bat ANY position
    including opening. Bowlers fill the tail sorted by batting ability (best first)."""
    top = [p for p in xi if category(p) != "BWL"]          # batters + all-rounders
    bwls = sorted((p for p in xi if category(p) == "BWL"), key=lambda p: -p["bat"])

    n_top = len(top)
    # Enumerate top-group permutations if small, else sample a capped set.
    full = list(itertools.permutations(top)) if n_top <= 7 else None
    if full is not None and len(full) <= max_cand * 8:
        top_perms = full
        rng.shuffle(top_perms)
    else:
        top_perms = []
        seen_p = set()
        attempts = 0
        while len(top_perms) < max_cand * 4 and attempts < max_cand * 40:
            attempts += 1
            cand = rng.sample(top, n_top)
            pk = tuple(p["name"] for p in cand)      # dicts aren't hashable
            if pk not in seen_p:
                seen_p.add(pk)
                top_perms.append(cand)

    bats = [p for p in top if category(p) == "BAT"]
    ars = [p for p in top if category(p) == "AR"]
    # Seeds are TOP-group orderings (no bowlers); `bwls` is appended below. Using
    # the full xi here would duplicate the bowlers, so seed 0 is xi's top order.
    seeds = [                          # always-included, known-good starting orders
        [p for p in xi if category(p) != "BWL"],                    # as supplied
        sorted(top, key=lambda p: -p["bat"]),                       # best bat first
        _archetype_seed(top),                                       # archetype logic
        sorted(bats, key=lambda p: -p["bat"]) +                     # bats then ARs
        sorted(ars, key=lambda p: -p["bat"]),
    ]

    orders, seen = [], set()

    def add(order):
        key = tuple(p["name"] for p in order)
        if key not in seen:
            seen.add(key)
            orders.append(order)

    for s in seeds:
        add(s + bwls)
    for tp in top_perms:
        if len(orders) >= max_cand:
            break
        add(list(tp) + bwls)
    return orders


# ─────────────────────────────────────────────────────────────────────────────
# Evaluation (one candidate -> win% on every pitch x tier)
# ─────────────────────────────────────────────────────────────────────────────
def _eval_one(order, pitch, opp_ovr, n, format_overs, base_seed, squad=None):
    """Win% for `order` on one pitch vs a FIXED balanced opponent rated `opp_ovr`.
    A fixed (not self-mirror) opponent is essential: a clone copies your players,
    so strong picks / pitch specialists cancel out. Against an even, balanced side
    they actually win you games, so selection is meaningful. Both sides carry the
    same perks: a captain (+1) and an impact bench. Common random numbers: seeding
    means every candidate faces the identical opponent + form draws here."""
    random.seed(base_seed)
    me_players = [dict(p) for p in order]
    _apply_captain(me_players)
    me_subs = _my_bench(order, squad, format_overs)
    wins = ties = 0
    for i in range(n):
        me = {"name": "YOU", "players": [dict(p) for p in me_players],
              "subs": [dict(p) for p in me_subs]}
        opp = _build_opponent(opp_ovr, format_overs)         # fresh balanced side, same perks
        if i % 2 == 0:
            m = CricketMatch(me, opp, format_overs, pitch, "Clear")
            m.impact_player = IMPACT_ON and format_overs == 20   # T20-only rule
            run_full_match(m)
            s_me, s_opp = m.innings1.total_runs, m.innings2.total_runs
        else:
            m = CricketMatch(opp, me, format_overs, pitch, "Clear")
            m.impact_player = IMPACT_ON and format_overs == 20   # T20-only rule
            run_full_match(m)
            s_opp, s_me = m.innings1.total_runs, m.innings2.total_runs
        if s_me > s_opp:
            wins += 1
        elif s_me == s_opp:
            ties += 1
    return 100.0 * (wins + 0.5 * ties) / n


def _eval_candidate(args):
    """Worker: evaluate one candidate across the given pitches x tiers -> grid."""
    idx, order, opp_specs, n, format_overs, seed0, pitches, squad = args
    grid = {}                      # (pitch, tier_name) -> win%
    # Fixed integer index per tier (NOT hash(): string hashing is randomised per
    # worker process, which would break common random numbers across candidates).
    tier_ix = {name: i for i, (name, _d) in enumerate(TIERS)}
    for pitch in pitches:
        for tier_name, spec in opp_specs.items():
            # Stable per (pitch,tier) seed -> common random numbers across candidates.
            cell_seed = seed0 + PITCH_IX[pitch] * 97 + tier_ix[tier_name] * 13
            grid[(pitch, tier_name)] = _eval_one(order, pitch, spec, n,
                                                  format_overs, cell_seed, squad)
    overall = sum(grid.values()) / len(grid)
    return idx, overall, grid


def _run_stage(orders, opp_specs, n, format_overs, seed0, procs, label,
               pitches=PITCHES, squad=None):
    tasks = [(i, o, opp_specs, n, format_overs, seed0, pitches, squad)
             for i, o in enumerate(orders)]
    t0 = time.time()
    if procs > 1:
        with Pool(procs) as pool:
            results = pool.map(_eval_candidate, tasks)
    else:
        results = [_eval_candidate(t) for t in tasks]
    results.sort(key=lambda r: -r[1])
    dt = time.time() - t0
    n_matches = len(orders) * len(pitches) * len(opp_specs) * n
    print(f"  [{label}] {len(orders)} orders x {len(pitches)} pitches x "
          f"{len(opp_specs)} tiers x {n} = {n_matches:,} matches in {dt:.1f}s")
    return results


# ─────────────────────────────────────────────────────────────────────────────
# Per-player career stats for the winning order
# ─────────────────────────────────────────────────────────────────────────────
def collect_player_stats(order, n, format_overs, seed, opp_ovr, squad=None):
    """Run `n` matches with the winning order (vs a fixed even opponent, pitches
    cycled) and accumulate batting & bowling stats per player, like a career log."""
    names = [p["name"] for p in order]
    st = {nm: {"inn": 0, "runs": 0, "balls": 0, "outs": 0, "fours": 0, "sixes": 0,
               "hs": 0, "bwl_balls": 0, "conceded": 0, "wkts": 0} for nm in names}
    me_players = [dict(p) for p in order]
    _apply_captain(me_players)
    me_subs = _my_bench(order, squad, format_overs)
    random.seed(seed)
    for i in range(n):
        pitch = PITCHES[i % len(PITCHES)]
        me = {"name": "YOU", "players": [dict(p) for p in me_players],
              "subs": [dict(p) for p in me_subs]}
        opp = _build_opponent(opp_ovr, format_overs)
        if i % 2 == 0:
            m = CricketMatch(me, opp, format_overs, pitch, "Clear")
            m.impact_player = IMPACT_ON and format_overs == 20   # T20-only rule
            run_full_match(m)
            bat_inn, bowl_inn = m.innings1, m.innings2
        else:
            m = CricketMatch(opp, me, format_overs, pitch, "Clear")
            m.impact_player = IMPACT_ON and format_overs == 20   # T20-only rule
            run_full_match(m)
            bat_inn, bowl_inn = m.innings2, m.innings1
        for nm, bs in bat_inn.batting_stats.items():
            if nm not in st:
                continue
            if bs.balls_faced > 0 or bs.dismissal != "not out":   # actually batted
                s = st[nm]
                s["inn"] += 1
                s["runs"] += bs.runs_scored
                s["balls"] += bs.balls_faced
                s["fours"] += bs.fours
                s["sixes"] += bs.sixes
                s["hs"] = max(s["hs"], bs.runs_scored)
                if bs.dismissal != "not out":
                    s["outs"] += 1
        for nm, bw in bowl_inn.bowling_stats.items():
            if nm not in st:
                continue
            if bw.balls_bowled > 0:
                s = st[nm]
                s["bwl_balls"] += bw.balls_bowled
                s["conceded"] += bw.runs_conceded
                s["wkts"] += bw.wickets_taken
    return st


def _overs_str(balls):
    return f"{balls // 6}.{balls % 6}"


def _print_player_stats(order, st, n):
    print(f"\n  PLAYER STATS IN BEST XI  (over {n} matches vs a fixed even "
          f"opponent, all 15 pitches)")
    print("  BATTING")
    print(f"  {'#':>2} {'Player':<22}{'Inn':>4}{'Runs':>6}{'Avg':>7}{'SR':>7}"
          f"{'HS':>5}{'4s':>5}{'6s':>5}")
    print("  " + "-" * 65)
    for i, p in enumerate(order, 1):
        s = st[p["name"]]
        if s["inn"] == 0:
            continue
        avg = s["runs"] / s["outs"] if s["outs"] else float(s["runs"]) / s["inn"]
        avg_str = f"{avg:.1f}*" if s["outs"] == 0 else f"{avg:.1f}"
        sr = 100 * s["runs"] / s["balls"] if s["balls"] else 0.0
        print(f"  {i:>2} {p['name']:<22}{s['inn']:>4}{s['runs']:>6}{avg_str:>7}"
              f"{sr:>7.1f}{s['hs']:>5}{s['fours']:>5}{s['sixes']:>5}")

    bowlers = [p for p in order if st[p["name"]]["bwl_balls"] > 0]
    if bowlers:
        print("\n  BOWLING")
        print(f"  {'#':>2} {'Player':<22}{'Overs':>7}{'Wkts':>6}{'Econ':>7}"
              f"{'Avg':>7}{'SR':>7}")
        print("  " + "-" * 58)
        for i, p in enumerate(order, 1):
            s = st[p["name"]]
            if s["bwl_balls"] == 0:
                continue
            econ = s["conceded"] / (s["bwl_balls"] / 6)
            avg = f"{s['conceded'] / s['wkts']:.1f}" if s["wkts"] else "-"
            sr = f"{s['bwl_balls'] / s['wkts']:.1f}" if s["wkts"] else "-"
            print(f"  {i:>2} {p['name']:<22}{_overs_str(s['bwl_balls']):>7}"
                  f"{s['wkts']:>6}{econ:>7.2f}{avg:>7}{sr:>7}")


# ─────────────────────────────────────────────────────────────────────────────
# Toss decision (bat first vs field first) per pitch
# ─────────────────────────────────────────────────────────────────────────────
def _win_pct_fixed_innings(order, pitch, opp_ovr, n, format_overs, seed, me_first,
                           squad=None):
    """Win% for the best XI on one pitch when it ALWAYS bats first (me_first=True)
    or ALWAYS fields first / chases (me_first=False), vs a fixed even opponent."""
    random.seed(seed)
    me_players = [dict(p) for p in order]
    _apply_captain(me_players)
    me_subs = _my_bench(order, squad, format_overs)
    wins = ties = 0
    for _ in range(n):
        me = {"name": "YOU", "players": [dict(p) for p in me_players],
              "subs": [dict(p) for p in me_subs]}
        opp = _build_opponent(opp_ovr, format_overs)
        if me_first:
            m = CricketMatch(me, opp, format_overs, pitch, "Clear")
            m.impact_player = IMPACT_ON and format_overs == 20   # T20-only rule
            run_full_match(m)
            s_me, s_opp = m.innings1.total_runs, m.innings2.total_runs
        else:
            m = CricketMatch(opp, me, format_overs, pitch, "Clear")
            m.impact_player = IMPACT_ON and format_overs == 20   # T20-only rule
            run_full_match(m)
            s_opp, s_me = m.innings1.total_runs, m.innings2.total_runs
        if s_me > s_opp:
            wins += 1
        elif s_me == s_opp:
            ties += 1
    return 100.0 * (wins + 0.5 * ties) / n


def toss_advice(order, n, format_overs, seed, opp_ovr, squad=None):
    """Per pitch: win% batting first vs fielding first (chasing). Returns a dict
    pitch -> (bat_first%, field_first%, decision)."""
    out = {}
    for pi, pitch in enumerate(PITCHES):
        bf = _win_pct_fixed_innings(order, pitch, opp_ovr, n, format_overs,
                                    seed + pi, me_first=True, squad=squad)
        ff = _win_pct_fixed_innings(order, pitch, opp_ovr, n, format_overs,
                                    seed + pi, me_first=False, squad=squad)
        out[pitch] = (bf, ff, "BAT" if bf >= ff else "FIELD")
    return out


def _print_toss_advice(adv, n):
    print(f"\n  WIN THE TOSS -> WHAT TO DO  (N={n} each, vs a fixed even opponent)")
    print(f"  {'Pitch':<12}{'Bat 1st':>9}{'Field 1st':>11}{'Edge':>7}  Decision")
    print("  " + "-" * 52)
    bat_pitches, field_pitches = [], []
    for pitch in PITCHES:
        bf, ff, dec = adv[pitch]
        edge = bf - ff
        (bat_pitches if dec == "BAT" else field_pitches).append(pitch)
        flag = "BAT FIRST" if dec == "BAT" else "FIELD FIRST"
        print(f"  {pitch:<12}{bf:>8.1f}{ff:>11.1f}{edge:>+7.1f}  {flag}")
    print("  " + "-" * 52)
    print(f"  >> BAT first on:   {', '.join(bat_pitches) or '(none)'}")
    print(f"  >> FIELD first on: {', '.join(field_pitches) or '(none)'}")


# ─────────────────────────────────────────────────────────────────────────────
# Best HOME pitch — which deck gives the biggest edge vs a VARIED field
# ─────────────────────────────────────────────────────────────────────────────
def _winpct_vs_kind(order, pitch, opp_ovr, kind, n, format_overs, seed, squad):
    random.seed(seed)
    me_players = [dict(p) for p in order]
    _apply_captain(me_players)
    me_subs = _my_bench(order, squad, format_overs)
    w = t = 0
    for i in range(n):
        me = {"name": "YOU", "players": [dict(p) for p in me_players],
              "subs": [dict(p) for p in me_subs]}
        opp = _variant_opponent(opp_ovr, kind, format_overs)
        if i % 2 == 0:
            m = CricketMatch(me, opp, format_overs, pitch, "Clear")
        else:
            m = CricketMatch(opp, me, format_overs, pitch, "Clear")
        m.impact_player = IMPACT_ON and format_overs == 20   # T20-only rule
        run_full_match(m)
        if i % 2 == 0:
            sm, so = m.innings1.total_runs, m.innings2.total_runs
        else:
            so, sm = m.innings1.total_runs, m.innings2.total_runs
        w += sm > so
        t += sm == so
    return 100.0 * (w + 0.5 * t) / n


def _home_pitch_worker(args):
    pitch, order, opp_ovr, squad, n, format_overs, seed = args
    res = {kind: _winpct_vs_kind(order, pitch, opp_ovr, kind, n, format_overs,
                                 seed + PITCH_IX[pitch] * 97 + ki * 13, squad)
           for ki, kind in enumerate(FIELD_KINDS)}
    return pitch, res


def home_pitch_advice(pitch_orders, opp_ovr, squad, n, format_overs, seed, procs):
    """For each pitch, sim THAT pitch's own best XI vs each opponent style. The best
    HOME pitch is the one with the highest AVERAGE win% across the varied field (and
    ideally no weak column). `pitch_orders` maps pitch -> its best batting order."""
    tasks = [(pitch, pitch_orders[pitch], opp_ovr, squad, n, format_overs, seed)
             for pitch in PITCHES]
    if procs > 1:
        with Pool(procs) as pool:
            out = pool.map(_home_pitch_worker, tasks)
    else:
        out = [_home_pitch_worker(t) for t in tasks]
    return dict(out)


def _print_home_pitch(adv, n):
    avg = lambda r: sum(r.values()) / len(r)
    worst = lambda r: min(r.values())
    print(f"\n  BEST HOME PITCH  (win% vs a VARIED field, N={n} each)")
    print(f"  {'Pitch':<12}{'Balanced':>9}{'vsSpin':>8}{'vsPace':>8}{'vsBat':>7}"
          f"{'AVG':>7}{'WORST':>7}")
    print("  " + "-" * 58)
    # Ranked by WORST-CASE (then AVG): the deck no visiting style can exploit.
    rows = sorted(((p, adv[p]) for p in PITCHES),
                  key=lambda x: (worst(x[1]), avg(x[1])), reverse=True)
    for pitch, r in rows:
        print(f"  {pitch:<12}{r['balanced']:>9.1f}{r['spin']:>8.1f}{r['pace']:>8.1f}"
              f"{r['bat']:>7.1f}{avg(r):>7.1f}{worst(r):>7.1f}")
    print("  " + "-" * 58)
    best_p, best_r = rows[0]
    bw = min(best_r, key=best_r.get)
    print(f"  >> BEST HOME PITCH: {best_p}  (worst-case {worst(best_r):.1f}% vs "
          f"'{bw}', avg {avg(best_r):.1f}%) -- no visiting style can exploit it")
    # Flag the flashy-average trap: highest AVG but a soft underbelly.
    by_avg = max(PITCHES, key=lambda p: avg(adv[p]))
    if by_avg != best_p:
        wk = min(adv[by_avg], key=adv[by_avg].get)
        print(f"     AVOID as home: {by_avg} has the highest avg ({avg(adv[by_avg]):.1f}%) "
              f"but only {adv[by_avg][wk]:.1f}% vs '{wk}'-strong sides -- a {wk} visitor "
              f"beats you there")


# ─────────────────────────────────────────────────────────────────────────────
# Captaincy test — every candidate captain (+1) tested on every pitch
# ─────────────────────────────────────────────────────────────────────────────
def _captain_winpct(order, captain_name, pitch, opp_ovr, squad, n, format_overs, seed):
    """Win% with `captain_name` boosted +1, on ONE pitch (common random numbers)."""
    random.seed(seed)
    me_players = [dict(p) for p in order]
    _apply_captain(me_players, captain_name)
    me_subs = _my_bench(order, squad, format_overs)
    w = t = 0
    for i in range(n):
        me = {"name": "YOU", "players": [dict(p) for p in me_players],
              "subs": [dict(p) for p in me_subs]}
        opp = _build_opponent(opp_ovr, format_overs)
        if i % 2 == 0:
            m = CricketMatch(me, opp, format_overs, pitch, "Clear")
        else:
            m = CricketMatch(opp, me, format_overs, pitch, "Clear")
        m.impact_player = IMPACT_ON and format_overs == 20   # T20-only rule
        run_full_match(m)
        if i % 2 == 0:
            sm, so = m.innings1.total_runs, m.innings2.total_runs
        else:
            so, sm = m.innings1.total_runs, m.innings2.total_runs
        w += sm > so
        t += sm == so
    return 100.0 * (w + 0.5 * t) / n


def _pp_cap_worker(args):
    pitch, order, name, opp_ovr, squad, n, fo, seed = args
    return pitch, name, _captain_winpct(order, name, pitch, opp_ovr, squad, n, fo, seed)


def per_pitch_captaincy(per_pitch, opp_ovr, squad, n, format_overs, seed, procs):
    """For EVERY pitch, test EVERY realistic captain (batter/all-rounder) in that
    pitch's XI with the +1 boost. Returns {pitch: [(name, win%), ...] best-first}."""
    tasks = []
    for pi, pitch in enumerate(PITCHES):
        order = per_pitch[pitch][0]
        cands = [p for p in order if category(p) in ("BAT", "AR")] or order
        for ci, p in enumerate(cands):
            tasks.append((pitch, order, p["name"], opp_ovr, squad, n, format_overs,
                          seed + pi * 131 + ci * 17))
    if procs > 1:
        with Pool(procs) as pool:
            out = pool.map(_pp_cap_worker, tasks)
    else:
        out = [_pp_cap_worker(t) for t in tasks]
    res = {p: [] for p in PITCHES}
    for pitch, name, wp in out:
        res[pitch].append((name, wp))
    for p in res:
        res[p].sort(key=lambda x: -x[1])
    return res


def _print_captaincy(res, order, n, pitch):
    cat = {p["name"]: category(p) for p in order}
    band = 100 * (0.5 / n) ** 0.5     # win% gaps under this are noise at this N
    rec = res[0][0]                   # tested best captain on this pitch
    near = [nm for nm, wp in res if res[0][1] - wp <= band]
    print(f"\n  CAPTAIN TEST on {pitch}  (+1 OVR, every candidate, N={n} each)")
    for rnk, (nm, wp) in enumerate(res[:6], 1):
        tag = "   <- best" if nm == rec else ""
        print(f"    {rnk}. {nm:<22} {cat.get(nm, ''):<3} {wp:5.2f}%{tag}")
    print(f"     (+1 OVR is a small lever; gaps under ~{band:.1f}% at N={n} are noise"
          + (f" -- top {len(near)} are a statistical tie)" if len(near) > 1 else ")"))
    return rec


# ─────────────────────────────────────────────────────────────────────────────
# Impact-player usage — when & where the AI brings the 12th man in
# ─────────────────────────────────────────────────────────────────────────────
def impact_usage_stats(order, opp_ovr, squad, n, format_overs, seed):
    me_subs = _my_bench(order, squad, format_overs)
    if not me_subs:
        return None
    me_players = [dict(p) for p in order]
    _apply_captain(me_players)
    random.seed(seed)
    used = bat = bowl = 0
    overs, subs_count = [], {}
    phases = {"powerplay": 0, "middle": 0, "death": 0}
    pp_end = 6 if format_overs == 20 else 10
    death_start = format_overs - 5
    for i in range(n):
        pitch = PITCHES[i % len(PITCHES)]
        me = {"name": "YOU", "players": [dict(p) for p in me_players],
              "subs": [dict(p) for p in me_subs]}
        opp = _build_opponent(opp_ovr, format_overs)
        my_id = 1 if i % 2 == 0 else 2
        if i % 2 == 0:
            m = CricketMatch(me, opp, format_overs, pitch, "Clear")
        else:
            m = CricketMatch(opp, me, format_overs, pitch, "Clear")
        m.impact_player = IMPACT_ON and format_overs == 20   # T20-only rule
        run_full_match(m)
        ev = [e for e in m.impact_log if e[0] == my_id]
        if ev:
            used += 1
        for _tid, over, role, nm in ev:
            overs.append(over)
            if role == "bat":
                bat += 1
            else:
                bowl += 1
            subs_count[nm] = subs_count.get(nm, 0) + 1
            if over < pp_end:
                phases["powerplay"] += 1
            elif over >= death_start:
                phases["death"] += 1
            else:
                phases["middle"] += 1
    return {"n": n, "used": used, "bat": bat, "bowl": bowl, "overs": overs,
            "subs_count": subs_count, "phases": phases, "total": bat + bowl,
            "bench": [p["name"] for p in me_subs]}


def _print_impact_usage(s):
    if not s or s["total"] == 0:
        print("\n  IMPACT PLAYER: no specialist bench available (squad too small).")
        return
    ev = s["total"]
    avg_over = sum(s["overs"]) / len(s["overs"])
    print(f"\n  IMPACT PLAYER -- WHEN & WHERE USED  (over {s['n']} matches)")
    print(f"     bench (best in is auto-picked): {', '.join(s['bench'])}")
    print(f"     used in {100 * s['used'] / s['n']:.0f}% of matches")
    print(f"     role:   batting {100 * s['bat'] / ev:.0f}%   |   "
          f"bowling {100 * s['bowl'] / ev:.0f}%")
    print(f"     timing: avg over {avg_over:.1f}   (powerplay "
          f"{100 * s['phases']['powerplay'] / ev:.0f}% / middle "
          f"{100 * s['phases']['middle'] / ev:.0f}% / death "
          f"{100 * s['phases']['death'] / ev:.0f}%)")
    top = sorted(s["subs_count"].items(), key=lambda x: -x[1])[:3]
    print("     most subbed in: " + ", ".join(f"{nm} ({c})" for nm, c in top))


# ─────────────────────────────────────────────────────────────────────────────
# Squad -> best XI selection
# ─────────────────────────────────────────────────────────────────────────────
def _is_valid_xi(players, require_wk=False, min_bowl=5, min_bat=4):
    """A pickable XI: enough bowling to get through an innings, a real top order,
    and (if `require_wk`) at least one wicketkeeper."""
    n_bowl = sum(1 for p in players if category(p) in ("AR", "BWL"))
    n_bat = sum(1 for p in players if category(p) == "BAT")
    if require_wk and not any("WK" in p["role"] for p in players):
        return False
    return n_bowl >= min_bowl and n_bat >= min_bat


def _pool_size_for(max_combos, n_squad, margin=3):
    """Smallest pool whose legal XIs comfortably exceed `max_combos`, plus a margin
    so some lower-OVR specialists can enter. Because the top-`max_combos` XIs *by
    OVR* only ever use the highest-OVR players, this pool provably contains them
    all while keeping enumeration cheap (we never need all C(25,11)=4.4M combos)."""
    k = 11
    while k < n_squad and comb(k, 11) < max_combos:
        k += 1
    return min(n_squad, k + margin)


def _selection_pool(squad, max_combos):
    """The candidate players to draw an XI from. Take the top players by OVR (pool
    sized to the budget), then top up to guarantee role feasibility (bowling, WK)."""
    pool_size = _pool_size_for(max_combos, len(squad))
    by_ovr = sorted(squad, key=lambda p: -player_ovr(p))
    if len(squad) <= pool_size:
        return list(squad)
    pool = by_ovr[:pool_size]
    rest = by_ovr[pool_size:]

    def n_in(plist, pred):
        return sum(1 for p in plist if pred(p))
    can_bowl = lambda p: category(p) in ("AR", "BWL")
    is_bat = lambda p: category(p) == "BAT"
    is_wk = lambda p: "WK" in p["role"]

    feasibility = [(6, can_bowl), (4, is_bat)]
    if any(is_wk(p) for p in squad):          # guarantee a keeper is available
        feasibility.append((1, is_wk))
    for need, pred in feasibility:            # role feasibility top-up
        for p in rest:
            if n_in(pool, pred) >= need:
                break
            if pred(p) and p not in pool:
                pool.append(p)
    return pool


def select_best_xi(squad, format_overs, select_n, max_combos, seed, procs, opp_specs,
                   select_fine_n=200, select_k=10, pitches=PITCHES, verbose=True,
                   rank_pitch=None):
    """Pick the strongest legal XI from a squad of >11 via a COARSE->FINE funnel.

    Gaps between near-identical XIs are tiny (often <1%), so judging them at a low
    sim count picks noise. We coarse-rank all candidate XIs cheaply, then re-sim the
    top `select_k` at high `select_fine_n` so the final pick is on real signal.
    `pitches` limits the conditions judged on (used for per-pitch best XIs);
    `rank_pitch` makes the candidate prefilter pitch-aware (spin/pace specialists)."""
    pool = _selection_pool(squad, max_combos)
    need_wk = any("WK" in p["role"] for p in squad)     # keeper mandatory if available
    combos = [list(c) for c in itertools.combinations(pool, 11)
              if _is_valid_xi(list(c), require_wk=need_wk)]
    relaxed = False
    if not combos:                       # constraints too tight -> relax to OVR-only
        relaxed = True
        combos = [list(c) for c in itertools.combinations(pool, 11)]

    n_legal = len(combos)
    # Prefilter the combos worth simulating. For a single pitch use a pitch-aware
    # value so spin/pace-specialist XIs aren't cut by a pitch-blind OVR ranking.
    if rank_pitch is not None:
        combos.sort(key=lambda c: -sum(pitch_value(p, rank_pitch) for p in c))
    else:
        combos.sort(key=lambda c: -sum(player_ovr(p) for p in c))
    combos = combos[:max_combos]
    orders = [_default_order(c) for c in combos]

    if verbose:
        pool_names = ", ".join(p["name"] for p in pool)
        print(f"  Pool of {len(pool)}/{len(squad)} players (by OVR): {pool_names}")
        print(f"  {n_legal:,} legal XIs in pool -> coarse-ranking the top "
              f"{len(combos)} by OVR{' (relaxed)' if relaxed else ''}.")

    coarse = _run_stage(orders, opp_specs, select_n, format_overs, seed, procs,
                        "SELECT-COARSE" if verbose else "  pp-select-coarse",
                        pitches=pitches, squad=squad)
    short = [orders[i] for i, _ov, _g in coarse[:select_k]]
    if verbose:
        print(f"  Re-simulating top {len(short)} XIs at N={select_fine_n} ...")
    fine = _run_stage(short, opp_specs, select_fine_n, format_overs, seed + 1, procs,
                      "SELECT-FINE" if verbose else "  pp-select-fine",
                      pitches=pitches, squad=squad)
    best_xi = short[fine[0][0]]

    if verbose:
        print("\n  Top XIs (fine win%):")
        base = set(p["name"] for p in best_xi)
        for rnk, (li, ov, _g) in enumerate(fine[:5], 1):
            diff = [p["name"] for p in short[li] if p["name"] not in base] or ["= best"]
            print(f"    {rnk}. {ov:5.2f}%   (vs best: +{', '.join(diff)})")
        chosen = {p["name"] for p in best_xi}
        benched = [p for p in squad if p["name"] not in chosen]
        print(f"\n  Best XI selected ({fine[0][1]:.2f}% default-order win):")
        for p in best_xi:
            print(f"     + {p['name']:<22} {p['bat']:>3}/{p['bowl']:<3} {category(p)}")
        if benched:
            print("  Left out:")
            for p in benched:
                print(f"     - {p['name']:<22} {p['bat']:>3}/{p['bowl']:<3} {category(p)}")
    return best_xi


# ─────────────────────────────────────────────────────────────────────────────
# Batting-order optimization (reusable for the overall run AND each pitch)
# ─────────────────────────────────────────────────────────────────────────────
def optimize_order(xi, opp_specs, coarse_n, fine_n, fine_k, max_cand, format_overs,
                   seed, procs, rng, pitches=PITCHES, verbose=True, squad=None):
    """Coarse->fine search for the best batting order of a fixed XI over `pitches`.
    Returns (best_order, best_overall_win%, best_grid, fine_results, finalists)."""
    cands = generate_candidates(xi, max_cand, rng)
    if verbose:
        print(f"\nGenerated {len(cands)} role-valid candidate orders.\n")
    coarse = _run_stage(cands, opp_specs, coarse_n, format_overs, seed, procs,
                        "COARSE" if verbose else "  pp-order-coarse",
                        pitches=pitches, squad=squad)
    if verbose:
        print("\n  Coarse top 5:")
        for rnk, (idx, ov, _g) in enumerate(coarse[:5], 1):
            top = " / ".join(p["name"] for p in cands[idx][:4])
            print(f"    {rnk}. {ov:5.1f}%  top4: {top} ...")
    finalists = [cands[idx] for idx, _ov, _g in coarse[:fine_k]]
    if verbose:
        print(f"\nFine stage: re-simulating top {len(finalists)} at N={fine_n} ...")
    fine = _run_stage(finalists, opp_specs, fine_n, format_overs, seed + 1, procs,
                      "FINE" if verbose else "  pp-order-fine", pitches=pitches,
                      squad=squad)
    best_local_idx, best_overall, best_grid = fine[0]
    return finalists[best_local_idx], best_overall, best_grid, fine, finalists


def best_per_pitch(squad, xi, opp_specs, format_overs, procs, seed, rng, params):
    """For EACH of the 15 pitches, pick the best XI from the squad AND optimise its
    batting order on that pitch. Compute-only (printing happens after the varied-
    field win% is known, so one consistent number is shown). Returns
    {pitch: (order, grid, captain, impact)}."""
    out = {}
    print("\nOptimising best XI + order for each of the 15 pitches ...")
    for pi, pitch in enumerate(PITCHES):
        pseed = seed + 1000 + pi * 31
        # Re-select the best XI for THIS pitch (skip if a flat 11 was supplied).
        if len(squad) > 11:
            pxi = select_best_xi(squad, format_overs, params["select_n"],
                                 params["max_combos"], pseed + 5, procs, opp_specs,
                                 select_fine_n=params["select_fine_n"],
                                 select_k=params["select_k"], pitches=[pitch],
                                 verbose=False, rank_pitch=pitch)
        else:
            pxi = xi
        # Optimise the batting order for THIS pitch.
        order, _ov, grid, _fine, _fin = optimize_order(
            pxi, opp_specs, params["coarse_n"], params["fine_n"], params["fine_k"],
            params["max_cand"], format_overs, pseed, procs, rng, pitches=[pitch],
            verbose=False, squad=squad)
        captain = _captain_of(order)
        bench = _my_bench(order, squad, format_overs)
        # Per-pitch impact pick = the bench player best suited to THIS deck.
        impact = max(bench, key=lambda p: pitch_value(p, pitch)) if bench else None
        out[pitch] = (order, grid, captain, impact)
    return out


def _print_one_pitch(pitch, order, grid, field, base_xi, captain, impact):
    favg = sum(field.values()) / len(field)
    base = set(p["name"] for p in base_xi)
    ins = [p["name"] for p in order if p["name"] not in base]
    print(f"\n  ── {pitch.upper()}  ──  win {favg:.1f}% vs the field   "
          f"[bal:{field['balanced']:.0f} vsSpin:{field['spin']:.0f} "
          f"vsPace:{field['pace']:.0f} vsBat:{field['bat']:.0f}]")
    print("     " + "  ".join(f"{i}.{p['name']}" for i, p in enumerate(order, 1)))
    tiers = "  ".join(f"{t}:{grid[(pitch, t)]:.0f}" for t, _d in TIERS)
    print(f"     vs even side at ±OVR: [{tiers}]")
    cap = captain["name"] if captain else "-"
    # Impact player is T20-only, so it's None in ODIs -> omit the field entirely.
    imp = f"   |   IMPACT PLAYER: {impact['name']} ({category(impact)})" if impact else ""
    print(f"     CAPTAIN: {cap}{imp}")
    if ins:
        outs = [n for n in base if n not in {p["name"] for p in order}]
        print(f"     vs default XI:  IN {', '.join(ins)}   OUT {', '.join(outs)}")


# ─────────────────────────────────────────────────────────────────────────────
# Public entry point
# ─────────────────────────────────────────────────────────────────────────────
def optimize(squad, format_overs=20, coarse_n=30, fine_n=150, fine_k=6,
             max_cand=150, procs=None, seed=42, quick=False, stats_n=300,
             toss_n=200, select_n=20, max_combos=150, select_fine_n=200,
             select_k=10, home_n=150, cap_n=150):
    rng = random.Random(seed)
    # Names (strings) are looked up from the ratings file; dicts are used as-is.
    if squad and isinstance(squad[0], str):
        squad = resolve_squad(squad)
    squad = [_norm(p) for p in squad]
    assert len(squad) >= 11, f"Need at least 11 players, got {len(squad)}"
    if procs is None:
        procs = max(1, (os.cpu_count() or 2) - 1)
    if quick:
        coarse_n, fine_n, fine_k, max_cand, stats_n, toss_n = 12, 40, 4, 40, 60, 60
        select_n, max_combos, select_fine_n, select_k = 10, 30, 60, 6
        home_n, cap_n = 50, 50

    fmt = "ODI (50)" if format_overs == 50 else "T20 (20)"
    print("=" * 74)
    print(f"  CricVerse Lineup Optimizer  |  {fmt}  |  Squad of {len(squad)}")
    print("=" * 74)

    # Opponent strength = OVR of the squad's best 11, so the 'same' tier is an even
    # contest. The opponent is a FIXED balanced side (pace+spin) at ref ± tier,
    # NOT a clone of your own XI -- a mirror copies your players so strong picks and
    # pitch specialists cancel out and can't be selected for.
    ref_ovr = team_ovr(sorted(squad, key=lambda p: -player_ovr(p))[:11])
    opp_specs = {name: ref_ovr + off for name, off in TIERS}

    print(f"  Opponent = a FIXED balanced side (pace+spin) at OVR {ref_ovr} (same) / "
          f"{ref_ovr + 3} (+3) / {ref_ovr - 3} (-3)  | procs={procs}")
    # Baseline XI for the per-pitch IN/OUT diff = the squad's best 11 by OVR.
    default_xi = (sorted(squad, key=lambda p: -player_ovr(p))[:11]
                  if len(squad) > 11 else squad)

    # ── CORE: best XI + best order for EACH of the 15 pitches ──
    pp_params = {
        "select_n": select_n, "max_combos": min(max_combos, 100),
        "select_fine_n": select_fine_n, "select_k": select_k,
        "coarse_n": coarse_n, "fine_n": fine_n, "fine_k": fine_k,
        "max_cand": min(max_cand, 100),
    }
    per_pitch = best_per_pitch(squad, default_xi, opp_specs, format_overs, procs,
                               seed, rng, pp_params)

    # Varied-field win% per pitch (each pitch fields its OWN best XI). This is the
    # ONE win% shown everywhere -- the per-pitch headline AND the home table use it.
    pitch_orders = {p: per_pitch[p][0] for p in PITCHES}
    print(f"Scoring each pitch's XI vs a varied field ({home_n} each) ...")
    hp = home_pitch_advice(pitch_orders, opp_specs["same"], squad, home_n,
                           format_overs, seed + 9, procs)
    # Rank by AVERAGE + FLOOR: reward a big advantage (so you dominate, not merely
    # survive) while penalising any exploitable weak column. Pure average hides a
    # fatal hole (turning crushes pace but loses to spin); pure floor (maximin) just
    # picks a mediocre deck. The sum wants both: strong AND no soft underbelly.
    ranked = sorted(PITCHES,
                    key=lambda p: sum(hp[p].values()) / len(hp[p]) + min(hp[p].values()),
                    reverse=True)
    best_home = ranked[0]
    home_order = per_pitch[best_home][0]

    # Captaincy TESTED on every pitch (each candidate +1, simulated per deck).
    print(f"Testing every captain on every pitch ({cap_n} each) ...")
    pp_caps = per_pitch_captaincy(per_pitch, opp_specs["same"], squad, cap_n,
                                  format_overs, seed + 11, procs)

    # ── PER-PITCH best XI + order, sorted best home pitch first ──
    print("\n" + "=" * 74)
    print("  PER-PITCH BEST XI + ORDER  (win% = vs a VARIED field; best deck first)")
    print("=" * 74)
    for pitch in ranked:
        order, grid, _cap, impact = per_pitch[pitch]
        cap_name = pp_caps[pitch][0][0]                    # tested best captain
        captain = next(p for p in order if p["name"] == cap_name)
        _print_one_pitch(pitch, order, grid, hp[pitch], default_xi, captain, impact)
    print("\n" + "=" * 74)
    _print_home_pitch(hp, home_n)

    # ── Detailed analysis for the recommended HOME pitch's XI ──
    print("\n" + "=" * 74)
    print(f"  HOME-PITCH SETUP  ·  {best_home.upper()}  ·  best XI captaincy / impact "
          f"/ toss / stats")
    print("=" * 74)
    _print_captaincy(pp_caps[best_home], home_order, cap_n, best_home)
    if format_overs == 20:      # impact player is a T20-only rule
        iu = impact_usage_stats(home_order, opp_specs["same"], squad, stats_n,
                                format_overs, seed + 13)
        _print_impact_usage(iu)
    adv = toss_advice(home_order, toss_n, format_overs, seed + 3, opp_specs["same"],
                      squad)
    _print_toss_advice(adv, toss_n)
    st = collect_player_stats(home_order, stats_n, format_overs, seed + 7,
                              opp_specs["same"], squad)
    _print_player_stats(home_order, st, stats_n)
    print("=" * 74)
    return per_pitch, hp, best_home


# ─────────────────────────────────────────────────────────────────────────────
# Single-scenario recommender (one pitch, one opponent) — used by the Discord bot.
# Self-contained, NO multiprocessing, so it's safe to call from an async thread.
# ─────────────────────────────────────────────────────────────────────────────
def _winpct_vs_team(order, pitch, opp_factory, n, format_overs, seed, squad,
                    weather="Clear"):
    random.seed(seed)
    me_players = [dict(p) for p in order]
    _apply_captain(me_players)
    me_subs = _my_bench(order, squad, format_overs)
    w = t = 0
    for i in range(n):
        me = {"name": "YOU", "players": [dict(p) for p in me_players],
              "subs": [dict(p) for p in me_subs]}
        opp = opp_factory()
        if i % 2 == 0:
            m = CricketMatch(me, opp, format_overs, pitch, weather)
        else:
            m = CricketMatch(opp, me, format_overs, pitch, weather)
        m.impact_player = IMPACT_ON and format_overs == 20   # T20-only rule
        run_full_match(m)
        if i % 2 == 0:
            sm, so = m.innings1.total_runs, m.innings2.total_runs
        else:
            so, sm = m.innings1.total_runs, m.innings2.total_runs
        w += sm > so
        t += sm == so
    return 100.0 * (w + 0.5 * t) / n


def _winpct_innings_vs_team(order, pitch, opp_factory, n, format_overs, seed, squad,
                            me_first, weather="Clear"):
    """Win% on `pitch` vs the opponent when this XI ALWAYS bats first (me_first) or
    always fields first / chases. Used for the toss recommendation."""
    random.seed(seed)
    me_players = [dict(p) for p in order]
    _apply_captain(me_players)
    me_subs = _my_bench(order, squad, format_overs)
    w = t = 0
    for _ in range(n):
        me = {"name": "YOU", "players": [dict(p) for p in me_players],
              "subs": [dict(p) for p in me_subs]}
        opp = opp_factory()
        if me_first:
            m = CricketMatch(me, opp, format_overs, pitch, weather)
            m.impact_player = IMPACT_ON and format_overs == 20   # T20-only rule
            run_full_match(m)
            sm, so = m.innings1.total_runs, m.innings2.total_runs
        else:
            m = CricketMatch(opp, me, format_overs, pitch, weather)
            m.impact_player = IMPACT_ON and format_overs == 20   # T20-only rule
            run_full_match(m)
            so, sm = m.innings1.total_runs, m.innings2.total_runs
        w += sm > so
        t += sm == so
    return 100.0 * (w + 0.5 * t) / n


def _opponent_factory(opp_spec, ref_ovr, format_overs=20):
    """opp_spec is a STYLE string ('balanced'/'spin'/'pace'/'bat') -> a fresh varied
    side each match; OR a list of 11 player dicts -> that exact XI (with perks)."""
    if isinstance(opp_spec, str):
        return lambda: _variant_opponent(ref_ovr, opp_spec, format_overs)
    opp_xi = [_norm(p) for p in opp_spec]
    o = team_ovr(opp_xi)

    def make():
        players = [dict(p) for p in _default_order(opp_xi)]
        _apply_captain(players)
        team = {"name": "OPP", "players": players, "subs": []}
        if _impact_on(format_overs):
            team["subs"] = [
                {"name": "OPP_IMP_BAT", "bat": min(99, o), "bowl": 35,
                 "role": "Batter", "archetype": "Finisher"},
                {"name": "OPP_IMP_BWL", "bat": 30, "bowl": min(99, o),
                 "role": "Bowler_Pace", "archetype": "Standard"},
            ]
        return team
    return make


def recommend_xi(squad, pitch, opp_spec, weather="Clear", format_overs=20, n_coarse=24,
                 n_fine=120, k=6, max_combos=80, max_cand=60, seed=42, toss_n=150):
    """Best XI + order for ONE pitch vs ONE opponent (a style string or an explicit
    11). Returns a dict: order, captain, impact, winpct, benched, ref_ovr.
    Single-process and quick (~a few thousand matches) so a bot can await it."""
    squad = [_norm(p) for p in squad]
    ref_ovr = team_ovr(sorted(squad, key=lambda p: -player_ovr(p))[:11])
    opp_factory = _opponent_factory(opp_spec, ref_ovr, format_overs)

    # Candidate XIs: legal combos from an OVR pool, pitch-aware-prefiltered.
    pool = _selection_pool(squad, max_combos)
    need_wk = any("WK" in p["role"] for p in squad)
    combos = [list(c) for c in itertools.combinations(pool, 11)
              if _is_valid_xi(list(c), require_wk=need_wk)]
    if not combos:
        combos = [list(c) for c in itertools.combinations(pool, 11)]
    combos.sort(key=lambda c: -sum(pitch_value(p, pitch) for p in c))
    combos = combos[:max_combos]
    rng = random.Random(seed)

    def best_of(orders, base_seed, coarse=True):
        n = n_coarse if coarse else n_fine
        scored = [(o, _winpct_vs_team(o, pitch, opp_factory, n, format_overs,
                                      base_seed + i, squad, weather))
                  for i, o in enumerate(orders)]
        scored.sort(key=lambda x: -x[1])
        return scored

    # Pick the XI (coarse -> fine), then optimise its batting order (coarse -> fine).
    sel = best_of([_default_order(c) for c in combos], seed, coarse=True)
    sel_fine = best_of([o for o, _w in sel[:k]], seed + 500, coarse=False)
    best_xi = sel_fine[0][0]

    ords = best_of(generate_candidates(best_xi, max_cand, rng), seed + 1000, coarse=True)
    ord_fine = best_of([o for o, _w in ords[:k]], seed + 1500, coarse=False)
    best_order, winpct = ord_fine[0]

    # Toss: win% batting first vs fielding first (chasing) on this pitch/opponent.
    bf = _winpct_innings_vs_team(best_order, pitch, opp_factory, toss_n, format_overs,
                                 seed + 2000, squad, me_first=True, weather=weather)
    ff = _winpct_innings_vs_team(best_order, pitch, opp_factory, toss_n, format_overs,
                                 seed + 2500, squad, me_first=False, weather=weather)

    # Impact player is a T20-only rule — no impact 12th man in ODIs.
    bench = _my_bench(best_order, squad) if format_overs == 20 else []
    chosen = {p["name"] for p in best_order}
    return {
        "order": best_order,
        "captain": _captain_of(best_order),
        "impact": max(bench, key=lambda p: pitch_value(p, pitch)) if bench else None,
        "winpct": winpct,
        "benched": [p for p in squad if p["name"] not in chosen],
        "ref_ovr": ref_ovr,
        "toss": {"bat_first": bf, "field_first": ff,
                 "decision": "BAT FIRST" if bf >= ff else "FIELD FIRST"},
    }


def _quick_xi(squad, pitch):
    """A quick, legal, pitch-appropriate XI (no simulation): the highest pitch_value
    combo from a small role-feasible pool. Used by the fast best-home-pitch scan."""
    need_wk = any("WK" in p["role"] for p in squad)
    pv = lambda p: pitch_value(p, pitch)
    pool = sorted(squad, key=lambda p: -pv(p))[:13]
    if need_wk and not any("WK" in p["role"] for p in pool):
        pool.append(max((p for p in squad if "WK" in p["role"]), key=pv))
    for ok, need in ((lambda p: category(p) in ("AR", "BWL"), 6),
                     (lambda p: category(p) == "BAT", 5)):
        for p in sorted(squad, key=lambda q: -pv(q)):
            if sum(1 for q in pool if ok(q)) >= need:
                break
            if ok(p) and p not in pool:
                pool.append(p)
    best, best_pv = None, -1
    for c in itertools.combinations(pool, 11):
        cl = list(c)
        if not _is_valid_xi(cl, require_wk=need_wk):
            continue
        s = sum(pv(p) for p in cl)
        if s > best_pv:
            best_pv, best = s, cl
    return best or sorted(squad, key=lambda p: -pv(p))[:11]


def best_home_pitch(squad, n=60, format_overs=20, seed=42):
    """The pitch where THIS squad is strongest — i.e. the deck whose WORST-CASE win%
    against a varied field (balanced/spin/pace/bat) is highest, so however good a
    visitor is, your home advantage holds. Returns ranked pitches + the field grid."""
    squad = [_norm(p) for p in squad]
    ref = team_ovr(sorted(squad, key=lambda p: -player_ovr(p))[:11])
    field = {}
    for pi, pitch in enumerate(PITCHES):
        order = _default_order(_quick_xi(squad, pitch))
        field[pitch] = {kind: _winpct_vs_kind(order, pitch, ref, kind, n, format_overs,
                                               seed + pi * 131 + ki * 17, squad)
                        for ki, kind in enumerate(FIELD_KINDS)}
    # Score = average advantage + worst-case floor. Rewards a BIG edge (so you
    # dominate, not just survive) while still penalising any exploitable weak column
    # -- so the pick is the deck you're strongest on with no soft underbelly.
    def score(p):
        v = field[p]
        return sum(v.values()) / len(v) + min(v.values())
    ranked = sorted(PITCHES, key=score, reverse=True)
    return {"ranked": ranked, "field": field, "best": ranked[0], "ref_ovr": ref}


def _report(best_order, best_overall, best_grid, fine, finalists, fine_n):
    print("\n" + "=" * 74)
    print("  BEST BATTING ORDER")
    print("=" * 74)
    for i, p in enumerate(best_order, 1):
        wk = " (WK)" if "WK" in p["role"] else ""
        print(f"   {i:>2}. {p['name']:<22} {p['bat']:>3}/{p['bowl']:<3} "
              f"{category(p):<3} {p['archetype']}{wk}")

    # Per-pitch x tier win% table for the winning order (columns = TIERS).
    tier_names = [name for name, _d in TIERS]
    print(f"\n  WIN% BY PITCH  (N={fine_n} per cell)  -- vs a fixed even opponent")
    header = f"  {'Pitch':<12}" + "".join(f"{'vs ' + t:>8}" for t in tier_names) + f"{'Avg':>9}"
    print(header)
    width = len(header) - 2
    print("  " + "-" * width)
    pitch_avgs = {}
    for pitch in PITCHES:
        vals = [best_grid[(pitch, t)] for t in tier_names]
        avg = sum(vals) / len(vals)
        pitch_avgs[pitch] = avg
        row = f"  {pitch:<12}" + "".join(f"{v:>8.1f}" for v in vals) + f"{avg:>9.1f}"
        print(row)
    print("  " + "-" * width)
    by_tier = lambda t: sum(best_grid[(p, t)] for p in PITCHES) / len(PITCHES)
    overall_row = (f"  {'OVERALL':<12}"
                   + "".join(f"{by_tier(t):>8.1f}" for t in tier_names)
                   + f"{best_overall:>9.1f}")
    print(overall_row)

    best_pitch = max(pitch_avgs, key=pitch_avgs.get)
    worst_pitch = min(pitch_avgs, key=pitch_avgs.get)
    print(f"\n  >> BEST pitch for this XI:  {best_pitch}  "
          f"({pitch_avgs[best_pitch]:.1f}% avg win)")
    print(f"  >> WORST pitch for this XI: {worst_pitch}  "
          f"({pitch_avgs[worst_pitch]:.1f}% avg win)")
    print(f"  >> Overall win rate across all 15 pitches x {len(TIERS)} tiers: "
          f"{best_overall:.1f}%")

    print("\n  Finalist ranking (overall win%):")
    for rnk, (li, ov, _g) in enumerate(fine, 1):
        top4 = " / ".join(p["name"] for p in finalists[li][:4])
        print(f"    {rnk}. {ov:5.1f}%   {top4} ...")
    print("=" * 74)


# ─────────────────────────────────────────────────────────────────────────────
# Your SQUAD — just names; ratings/roles are looked up from cricverse_players_ratings.txt
# Give 11 OR MORE; if >11 the tool picks the best XI first.
# ─────────────────────────────────────────────────────────────────────────────
MY_SQUAD = [
    "Sachin Tendulkar", "Virat Kohli", "Jasprit Bumrah", "Chris Gayle", "Vijay Merchant",
    "Everton Weekes", "MS Dhoni", "Garfield Sobers", "Mike Procter", "Bob Appleyard",
    "Tim Southee", "Bill O'Reilly", "Anil Kumble", "Jim Laker", "Richard Hadlee",
    "Adam Zampa", "Ricky Ponting", "Quinton de Kock", "Sydney Barnes", "Michael Hussey",
    "Michael Vaughan", "Hashim Amla", "Jonny Bairstow", "Eoin Morgan", "Graham Gooch"
]
def _cli():
    ap = argparse.ArgumentParser(description="CricVerse lineup optimizer")
    ap.add_argument("--odi", action="store_true",
                    help="50-over format (skips the interactive T20/ODI prompt)")
    ap.add_argument("--t20", action="store_true",
                    help="20-over format (skips the interactive T20/ODI prompt)")
    ap.add_argument("--coarse-n", type=int, default=30)
    ap.add_argument("--fine-n", type=int, default=150)
    ap.add_argument("--fine-k", type=int, default=6)
    ap.add_argument("--max-cand", type=int, default=150)
    ap.add_argument("--stats-n", type=int, default=300,
                    help="matches used to compute best-XI player stats")
    ap.add_argument("--toss-n", type=int, default=200,
                    help="matches per pitch for the bat-first vs field-first toss call")
    ap.add_argument("--home-n", type=int, default=150,
                    help="matches per pitch-vs-field-type for the best home pitch")
    ap.add_argument("--cap-n", type=int, default=150,
                    help="matches per captain candidate in the captaincy test")
    ap.add_argument("--select-n", type=int, default=20,
                    help="coarse matches/cell when picking best XI from a squad of >11")
    ap.add_argument("--select-fine-n", type=int, default=200,
                    help="fine matches/cell to resolve the top selection candidates")
    ap.add_argument("--select-k", type=int, default=10,
                    help="how many top XIs to re-sim at fine N during selection")
    ap.add_argument("--max-combos", type=int, default=150,
                    help="how many top-OVR legal XIs to coarse-rank in selection")
    ap.add_argument("--procs", type=int, default=None)
    ap.add_argument("--quick", action="store_true", help="fast sanity run")
    a = ap.parse_args()

    # Format: honour an explicit flag, else ask interactively.
    if a.odi:
        format_overs = 50
    elif a.t20:
        format_overs = 20
    else:
        ans = input("Format?  [1] T20 (20 overs)   [2] ODI (50 overs)  > ").strip().lower()
        format_overs = 50 if ans in ("2", "odi", "50", "o") else 20
        print(f"→ {'ODI (50 overs)' if format_overs == 50 else 'T20 (20 overs)'}\n")

    optimize(MY_SQUAD, format_overs=format_overs, coarse_n=a.coarse_n,
             fine_n=a.fine_n, fine_k=a.fine_k, max_cand=a.max_cand,
             procs=a.procs, quick=a.quick, stats_n=a.stats_n, toss_n=a.toss_n,
             select_n=a.select_n, max_combos=a.max_combos,
             select_fine_n=a.select_fine_n, select_k=a.select_k, home_n=a.home_n,
             cap_n=a.cap_n)


if __name__ == "__main__":
    _cli()
