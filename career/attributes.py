"""
The career attribute tree.

The old model had four attributes (power, control, bowling, stamina). That was
thin in two ways: every career ended up shaped the same, and there was almost
nothing to spend coins on, so the economy's only sink was pushing the same four
numbers up. Widening the tree fixes both at once - more ways to build a player,
and roughly two and a half times the sink without touching a single price.

Ten attributes in four groups:

  BATTING   power      clearing the rope
            timing     placement and control (the old `control`)
            technique  surviving the good ball
  BOWLING   bowling    raw wicket threat
            accuracy   economy, fewer loose balls
            variation  deception - cutters, mystery, changes of pace
  FIELDING  catching   holding the chance          (career-only, see below)
            throwing   run-outs from the deep      (career-only)
            agility    reach, diving, ground speed (career-only)
  PHYSICAL  stamina    workload resilience

The three FIELDING attributes never reach the simulation. The engine takes only
a bat and a bowl rating, and it is not to be changed - so fielding matters purely
inside career mode's own catch and run-out system, which is exactly where the
player experiences it.

Old careers are migrated on read (see migrate), so nothing has to be reset.
"""

# key -> (label, group, one-line blurb)
ATTR_INFO = {
    "power":     ("Power",     "batting",  "Clear the rope. Drives sixes and boundary rate."),
    "timing":    ("Timing",    "batting",  "Placement and strike rotation. The old Control."),
    "technique": ("Technique", "batting",  "Survive the good ball. Fewer soft dismissals."),
    "bowling":   ("Bowling",   "bowling",  "Raw wicket threat with ball in hand."),
    "accuracy":  ("Accuracy",  "bowling",  "Tight lines. Lower economy, fewer extras."),
    "variation": ("Variation", "bowling",  "Deception - cutters, mystery, change of pace."),
    "catching":  ("Catching",  "fielding", "Holding the chance when it comes to you."),
    "throwing":  ("Throwing",  "fielding", "Run-outs. Arm strength and accuracy."),
    "agility":   ("Agility",   "fielding", "Reach, dives and ground speed."),
    "stamina":   ("Stamina",   "physical", "Workload resilience. Slows fitness drain."),
}

ATTRS = tuple(ATTR_INFO.keys())
GROUPS = ("batting", "bowling", "fielding", "physical")

BATTING_ATTRS = tuple(k for k, v in ATTR_INFO.items() if v[1] == "batting")
BOWLING_ATTRS = tuple(k for k, v in ATTR_INFO.items() if v[1] == "bowling")
FIELDING_ATTRS = tuple(k for k, v in ATTR_INFO.items() if v[1] == "fielding")

# A rookie's starting spread. Deliberately uneven so a fresh career already has a
# shape to lean into rather than ten identical numbers.
BASE_ATTRS = {
    "power": 58, "timing": 62, "technique": 57,
    "bowling": 56, "accuracy": 59, "variation": 54,
    "catching": 57, "throwing": 55, "agility": 58,
    "stamina": 60,
}

# How the old four map onto the new ten when a career is first read.
_LEGACY_SOURCE = {
    "timing": "control",
    "technique": "control",
    "accuracy": "bowling",
    "variation": "bowling",
    "catching": "stamina",
    "throwing": "stamina",
    "agility": "stamina",
}


def group_of(attr):
    return ATTR_INFO.get(attr, ("", "physical", ""))[1]


def label_of(attr):
    return ATTR_INFO.get(attr, (attr.title(),))[0]


def by_group():
    out = {g: [] for g in GROUPS}
    for k, (_, g, _) in ATTR_INFO.items():
        out[g].append(k)
    return out


def resolve(name):
    """Accept an attribute name or a unique prefix; returns the key or None.

    `cv upgrade tech` should work - ten attributes is a lot to type exactly.
    """
    if not name:
        return None
    n = str(name).strip().lower()
    if n in ATTR_INFO:
        return n
    if n == "control":            # the old name for timing
        return "timing"
    hits = [k for k in ATTR_INFO if k.startswith(n)]
    return hits[0] if len(hits) == 1 else None


def migrate(career):
    """Fill in the new attributes on a career written against the old four.

    Nothing is lost and nothing has to be reset: `control` becomes `timing`, the
    derived attributes start from whichever old attribute they descend from, and
    a small deduction is applied so widening the tree does not hand every
    existing player a free rating boost.
    """
    a = career.get("attributes")
    if not isinstance(a, dict):
        career["attributes"] = dict(BASE_ATTRS)
        return True
    if all(k in a for k in ATTRS):
        return bool(a.pop("control", None) is not None)

    for key in ATTRS:
        if key in a:
            continue
        src = _LEGACY_SOURCE.get(key)
        if src and src in a:
            # Derived attributes come in a touch below their parent: a player who
            # only ever trained "bowling" has not also mastered variation.
            a[key] = max(1, min(99, int(round(a[src] * 0.92))))
        else:
            a[key] = BASE_ATTRS.get(key, 55)
    a.pop("control", None)
    return True                     # tells the caller this was a legacy document


# Skill derivations. These are what the engine actually receives.
def _blend(a, weights):
    total = sum(weights.values())
    return sum(a.get(k, 55) * w for k, w in weights.items()) / total


def bat_skill(a):
    """Batting rating for the engine.

    Timing carries the most because it decides whether the bat meets the ball at
    all; power converts that into boundaries; technique is what keeps you in.
    """
    return int(round(max(0, min(99, _blend(a, {"timing": 0.42, "power": 0.33,
                                               "technique": 0.17, "stamina": 0.08})))))


def bowl_skill(a):
    """Bowling rating for the engine."""
    return int(round(max(0, min(99, _blend(a, {"bowling": 0.46, "accuracy": 0.28,
                                               "variation": 0.18, "stamina": 0.08})))))


def field_skill(a):
    """Overall fielding, career-side only - never sent to the engine."""
    return int(round(max(0, min(99, _blend(a, {"catching": 0.45, "agility": 0.32,
                                               "throwing": 0.23})))))


def catch_skill(a):
    """Chance-holding rating: catching, with agility for the hard ones."""
    return int(round(max(0, min(99, _blend(a, {"catching": 0.72, "agility": 0.28})))))


def throw_skill(a):
    """Run-out rating: arm, plus the agility to get to the ball."""
    return int(round(max(0, min(99, _blend(a, {"throwing": 0.70, "agility": 0.30})))))
