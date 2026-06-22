"""
CricVerse DRAFT MODE — pure logic (no Discord, no DB imports; everything takes data as args).

A knowledge game: 11 rounds build a BALANCED XI. Each round = one team slot with a pool of
5 questions (the bot fires one). Both captains answer the SAME question (toss-winner first),
naming a player from memory — RATINGS ARE NEVER SHOWN. Validation is role/archetype ONLY.

Wrong/unknown/taken answer → up to 3 retries → a 78-OVR "net" filler of that slot (keeps the
XI at 11 and balanced).
"""
import random
import difflib

# ── Role categories (match the exact DB role strings) ───────────────────────
def _is_bat(r):       return r in ("Batter", "Batter_WK")
def _is_wk(r):        return r == "Batter_WK"
def _is_pace_bowl(r): return r == "Bowler_Pace"
def _is_spin_bowl(r): return r.startswith("Bowler_Spin")
def _is_leg_spin(r):  return r == "Bowler_Spin_Leg"
def _is_off_spin(r):  return r == "Bowler_Spin_Off"
def _is_left_spin(r): return r.startswith("Bowler_Spin_Orth")        # left-arm orthodox
def _is_pace_ar(r):   return r == "All-Rounder_Pace"
def _is_spin_ar(r):   return r.startswith("All-Rounder_Spin")
def _is_finisher_role(r):  # a finisher must be someone who actually bats: specialist bat or all-rounder
    return _is_bat(r) or r.startswith("All-Rounder")

_CATS = {
    "BAT": _is_bat, "WK": _is_wk, "PACE_BOWL": _is_pace_bowl, "SPIN_BOWL": _is_spin_bowl,
    "LEG_SPIN": _is_leg_spin, "OFF_SPIN": _is_off_spin, "LEFT_SPIN": _is_left_spin,
    "PACE_AR": _is_pace_ar, "SPIN_AR": _is_spin_ar, "FIN_ROLE": _is_finisher_role,
}

# A question: prompt + category (role rule) + arch (None=any · str · tuple of allowed archetypes)
def _q(prompt, cat, arch=None):
    return {"q": prompt, "cat": cat, "arch": arch}

# ── The 11 balanced slots (each: label · filler spec · 5 questions) ──────────
# filler: (name_base, bat, bowl, role, archetype)  → ~78 OVR "net" player for the slot
DRAFT_SLOTS = [
    ("Aggressive Opener",  ("Net Opener", 78, 20, "Batter", "Aggressor"), [
        _q("an aggressive opening batter", "BAT", "Aggressor"),
        _q("a power-hitting top-order batter", "BAT", "Aggressor"),
        _q("an explosive batter", "BAT", "Aggressor"),
        _q("an attacking top-order batter", "BAT", "Aggressor"),
        _q("a top-order batter", "BAT", None),
    ]),
    ("Top-order Anchor",   ("Net Anchor", 78, 20, "Batter", "Anchor"), [
        _q("a solid top-order batter (anchor)", "BAT", "Anchor"),
        _q("an anchor batter", "BAT", "Anchor"),
        _q("a reliable No.3 batter", "BAT", "Anchor"),
        _q("a classical/technically sound batter", "BAT", "Anchor"),
        _q("a steady accumulator", "BAT", "Anchor"),
    ]),
    ("Batter",             ("Net Batter", 78, 20, "Batter", "Standard"), [
        _q("a specialist batter", "BAT", None),
        _q("a middle-order batter", "BAT", None),
        _q("a top-six batter", "BAT", None),
        _q("a versatile batter", "BAT", None),
        _q("any frontline batter", "BAT", None),
    ]),
    ("Power Hitter",       ("Net Hitter", 78, 20, "Batter", "Aggressor"), [
        _q("a big-hitting batter", "BAT", "Aggressor"),
        _q("a six-hitting batter", "BAT", ("Aggressor", "Finisher")),
        _q("a middle-order power hitter", "BAT", ("Aggressor", "Finisher")),
        _q("an aggressive stroke-maker", "BAT", "Aggressor"),
        _q("a hard-hitting batter", "BAT", ("Aggressor", "Finisher")),
    ]),
    ("Wicket-Keeper",      ("Net Keeper", 78, 20, "Batter_WK", "Standard"), [
        _q("a wicket-keeper", "WK", None),
        _q("a keeper-batter", "WK", None),
        _q("an attacking wicket-keeper", "WK", "Aggressor"),
        _q("a finisher wicket-keeper", "WK", "Finisher"),
        _q("a dependable wicket-keeper", "WK", None),
    ]),
    ("Finisher",           ("Net Finisher", 78, 22, "Batter", "Finisher"), [
        _q("a finisher", "FIN_ROLE", "Finisher"),
        _q("a death-overs finisher", "FIN_ROLE", "Finisher"),
        _q("a lower-middle-order hitter (finisher)", "FIN_ROLE", "Finisher"),
        _q("a clutch finisher", "FIN_ROLE", "Finisher"),
        _q("a No.6/7 finisher", "FIN_ROLE", "Finisher"),
    ]),
    ("Pace All-Rounder",   ("Net Pace AR", 60, 78, "All-Rounder_Pace", "Standard"), [
        _q("a pace-bowling all-rounder", "PACE_AR", None),
        _q("a seam-bowling all-rounder", "PACE_AR", None),
        _q("a fast-bowling all-rounder", "PACE_AR", None),
        _q("an all-rounder who bowls pace", "PACE_AR", None),
        _q("a medium-pace all-rounder", "PACE_AR", None),
    ]),
    ("Spin All-Rounder",   ("Net Spin AR", 60, 78, "All-Rounder_Spin_Off", "Standard"), [
        _q("a spin-bowling all-rounder", "SPIN_AR", None),
        _q("an all-rounder who bowls spin", "SPIN_AR", None),
        _q("a slow-bowling all-rounder", "SPIN_AR", None),
        _q("an off-spinning all-rounder", "SPIN_AR", None),
        _q("a spin-bowling all-rounder", "SPIN_AR", None),
    ]),
    ("Pace Bowler",        ("Net Pacer", 18, 78, "Bowler_Pace", "Standard"), [
        _q("a fast bowler", "PACE_BOWL", None),
        _q("a new-ball seamer", "PACE_BOWL", None),
        _q("an express/aggressive pacer", "PACE_BOWL", "Aggressor"),
        _q("a swing bowler", "PACE_BOWL", None),
        _q("a frontline pace bowler", "PACE_BOWL", None),
    ]),
    ("Pace Bowler",        ("Net Pacer", 18, 78, "Bowler_Pace", "Standard"), [
        _q("a fast bowler", "PACE_BOWL", None),
        _q("a pace bowler", "PACE_BOWL", None),
        _q("a death-overs specialist pacer", "PACE_BOWL", ("Finisher", "Aggressor")),
        _q("a seam bowler", "PACE_BOWL", None),
        _q("a quick bowler", "PACE_BOWL", None),
    ]),
    ("Frontline Spinner",  ("Net Spinner", 18, 78, "Bowler_Spin_Off", "Standard"), [
        _q("a leg-spinner", "LEG_SPIN", None),
        _q("an off-spinner", "OFF_SPIN", None),
        _q("a frontline spinner", "SPIN_BOWL", None),
        _q("a wrist-spinner", "LEG_SPIN", None),
        _q("a left-arm spinner", "LEFT_SPIN", None),
    ]),
]

NUM_ROUNDS = len(DRAFT_SLOTS)   # 11

# Player-pool tiers. OVR caps are BACKEND ONLY — never shown to players.
#   legends   = everyone · greats = OVR <= 92 (all-time untouchables excluded) · youngsters = OVR <= 85
POOL_CAPS = {"legends": None, "greats": 92, "youngsters": 85}


def filter_pool(players, mode, ovr_of):
    """Filter the draft pool by tier. `ovr_of` is a player->OVR function (kept out of this pure module)."""
    cap = POOL_CAPS.get(mode)
    if cap is None:
        return list(players)
    return [p for p in players if ovr_of(p) <= cap]


def slot_label(round_idx):
    return DRAFT_SLOTS[round_idx][0]


def pick_question(round_idx):
    """Return one of the round's 5 questions (the prompt the captains must answer)."""
    return random.choice(DRAFT_SLOTS[round_idx][2])


def question_matches(player, question):
    """Does this player satisfy the question's role + archetype condition? (No ratings involved.)"""
    role = str(player.get("role", ""))
    if not _CATS[question["cat"]](role):
        return False
    arch = question.get("arch")
    if arch is None:
        return True
    pa = player.get("archetype", "")
    return pa == arch if isinstance(arch, str) else pa in arch


# ── Name resolution (accurate verification of a typed answer) ────────────────
def resolve_name(text, players):
    """Resolve a typed name to a DB player.
    Returns (player|None, status): 'ok' | 'none' | 'ambiguous'(player=None, plus candidates via resolve_candidates).
    Order: exact (case-insensitive) → unique substring → close fuzzy."""
    t = (text or "").strip().lower()
    if not t:
        return None, "none"
    # exact
    for p in players:
        if p["name"].lower() == t:
            return p, "ok"
    # substring — must be unique to accept
    subs = [p for p in players if t in p["name"].lower()]
    if len(subs) == 1:
        return subs[0], "ok"
    if len(subs) > 1:
        # if the typed text exactly equals a first/last token of one player, prefer it
        exacts = [p for p in subs if t in [w.lower() for w in p["name"].split()]]
        if len(exacts) == 1:
            return exacts[0], "ok"
        return None, "ambiguous"
    # fuzzy (tight cutoff so wrong text doesn't match)
    names = [p["name"] for p in players]
    close = difflib.get_close_matches(text.strip(), names, n=1, cutoff=0.82)
    if close:
        return next(p for p in players if p["name"] == close[0]), "ok"
    return None, "none"


def resolve_candidates(text, players, limit=6):
    """For an ambiguous answer, list the matching names so the user can be specific."""
    t = (text or "").strip().lower()
    return [p["name"] for p in players if t in p["name"].lower()][:limit]


def verify_answer(text, question, players, taken_names):
    """Full verification of one typed answer.
    Returns (player|None, reason): reason in
      'ok' · 'unknown' · 'ambiguous' · 'taken' · 'wrong_type'."""
    player, status = resolve_name(text, players)
    if status == "ambiguous":
        return None, "ambiguous"
    if player is None:
        return None, "unknown"
    if player["name"] in taken_names:
        return None, "taken"
    if not question_matches(player, question):
        return None, "wrong_type"
    return player, "ok"


# ── AI captain ──────────────────────────────────────────────────────────────
def ai_pick(players, taken_names, question, ovr_of):
    """AI names a valid available player for the question — competent but not always the best."""
    valid = [p for p in players if p["name"] not in taken_names and question_matches(p, question)]
    if not valid:
        return None
    valid.sort(key=ovr_of, reverse=True)
    top = valid[: max(3, len(valid) // 5)]   # top slice → realistic but beatable
    return random.choice(top)


# ── 78-OVR "net" filler (3 strikes and you're out) ──────────────────────────
def make_filler(round_idx, tag=""):
    """A fresh ~78-OVR generic player for the slot. `tag` keeps the name unique across both XIs."""
    base, bat, bowl, role, arch = DRAFT_SLOTS[round_idx][1]
    name = f"{base} {tag}".strip()
    return {"name": name, "bat": bat, "bowl": bowl, "role": role, "archetype": arch, "is_filler": True}
