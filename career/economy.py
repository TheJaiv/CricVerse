"""
Career Mode economy and rating logic — GROUNDWORK, NOT YET WIRED.

Status: this module is analysis for a future update. career_manager still uses
its own live numbers; nothing here changes what players earn today. It exists so
the rebalance can be argued from measured numbers instead of guesses when it is
picked up.

Why it will be needed: the live economy was tuned for a mode with no wages, no
seasons and no fitness. Seasons and condition added all three, so the money wants
re-deriving rather than patching, and the constants are currently scattered
across career_manager, the scenario settle and the club payout.

Everything below is driven by the stated TARGETS. Change those, run
tools/career_economy_report.py, and read what the curve actually does.

NOTE: there is deliberately NO daily earn cap. Capping a day's income would
punish exactly the players who most want to grind, which is not the kind of
brake this economy should use - if farming needs limiting later, it should be
diminishing returns on repeated low-effort sources, not a wall.
"""
import math

# ---------------------------------------------------------------- design targets
# Days of ACTIVE play (dailies claimed, a few matches) a climb should take.
TARGETS = {
    "bronze_to_silver": 7,      # OVR 60 -> 69, the first week should feel quick
    "silver_to_gold": 24,       # 69 -> 77
    "gold_to_platinum": 70,     # 77 -> 85
    "platinum_to_diamond": 165, # 85 -> 93, a genuine long haul
}
# Share of income that should come from PLAYING rather than logging in.
PLAY_INCOME_SHARE = 0.65

# ---------------------------------------------------------------- upgrade curve
# Exponential on purpose: every point costs more than the last, so the 90s stay a
# grind no matter how much income grows. BASE and RATE are solved against the
# targets above - see tools/career_economy_report.py.
UPGRADE_BASE = 26.0
UPGRADE_RATE = 1.155
UPGRADE_FLOOR_OVR = 60


def upgrade_cost(v: int) -> int:
    """Coins to raise one attribute from v to v+1."""
    return int(round(UPGRADE_BASE * (UPGRADE_RATE ** max(0, v - UPGRADE_FLOOR_OVR))))


def cost_between(lo: int, hi: int) -> int:
    """Total cost to take a single attribute from lo to hi."""
    return sum(upgrade_cost(v) for v in range(lo, hi))


def cost_to_reach_ovr(from_ovr: int, to_ovr: int) -> int:
    """Rough coins to move OVR from one value to another.

    All four attributes sit near the OVR, and the OVR is a weighted blend of
    them, so lifting the OVR by a point means lifting the attributes by about a
    point each - four upgrades per OVR point.
    """
    return sum(cost_between(v, v + 1) * 4 for v in range(from_ovr, to_ovr))


# ---------------------------------------------------------------- income
# Daily login. Deliberately the SMALLER half of income (see PLAY_INCOME_SHARE):
# it exists to reward the habit, not to be a substitute for playing.
DAILY_MIN, DAILY_MAX = 22, 50
STREAK_BONUS_PER_DAY = 3
STREAK_BONUS_CAP_DAYS = 10

# Match performance pay. Scales with what you actually did.
MATCH_BASE = 55
MATCH_WIN_BONUS = 45
MATCH_RUNS_DIVISOR = 10       # coins per this many runs
MATCH_RUNS_COINS = 5
MATCH_FIFTY = 18
MATCH_HUNDRED = 40
MATCH_WICKET = 14
MATCH_MAIDEN = 12
MATCH_CATCH = 6

# Club wages are per match and live in data/career_clubs.json. They are the
# steady half of play income; performance pay is the variable half.

# Premium extras (unchanged in spirit - a convenience, never a shortcut).
WEEKLY_AMOUNT = 800
MONTHLY_AMOUNT = 3000
WEEK_BOOST = 1.05

# ---------------------------------------------------------------- anti-farm
# NO daily earn ceiling, by decision: someone who wants to play twenty matches a
# day should be rewarded for it. The only farming rule kept is that matches
# against bots pay nothing, which stops coins being minted without an opponent.
AI_MATCH_PAYS = False
SCENARIO_DAILY_CAP = 6
SCENARIO_ENTRY_FEE = 10

# ---------------------------------------------------------------- sinks
# Without sinks, wages inflate the currency until upgrades stop mattering.
RENAME_COST = 250
TREATMENT_COST_PER_MATCH = 120    # cv treat - buy off an injury's remaining matches
TRAINING_CAMP_COST = 400          # cv camp  - a guaranteed form reset upward
AGENT_FEE_RATE = 0.10             # taken from the first wage of a new contract


def treatment_cost(matches_left: int) -> int:
    return max(0, int(matches_left) * TREATMENT_COST_PER_MATCH)


def agent_fee(wage: int, matches: int) -> int:
    return int(round(wage * matches * AGENT_FEE_RATE))


def daily_amount(streak_days: int, boost: float = 1.0) -> tuple:
    """(min, max) daily payout for a streak length - the caller rolls between them."""
    bonus = min(max(0, streak_days - 1), STREAK_BONUS_CAP_DAYS) * STREAK_BONUS_PER_DAY
    return (int((DAILY_MIN + bonus) * boost), int((DAILY_MAX + bonus) * boost))


def match_payout(*, runs=0, fifties=0, hundreds=0, wickets=0, maidens=0,
                 catches=0, stumpings=0, won=False) -> int:
    """Performance pay for one real match, before any boost."""
    coins = MATCH_BASE
    if won:
        coins += MATCH_WIN_BONUS
    coins += (runs // MATCH_RUNS_DIVISOR) * MATCH_RUNS_COINS
    coins += fifties * MATCH_FIFTY + hundreds * MATCH_HUNDRED
    coins += wickets * MATCH_WICKET + maidens * MATCH_MAIDEN
    coins += (catches + stumpings) * MATCH_CATCH
    return coins


# ---------------------------------------------------------------- rating logic
# OVR by PRIMARY STRENGTH.
#
# The old blend (0.55*bat + 0.45*bowl) meant a player who specialised was taxed
# for the suit they neglected: a genuine batting talent with weak bowling scored
# below a mediocre all-rounder. That contradicts how CricVerse rates everyone
# else - a batsman is judged on batting - and it quietly forced every career into
# the same shape. Primary-weighted fixes it: your stronger discipline carries the
# rating and the weaker one still counts for something.
PRIMARY_WEIGHT = 0.72
SECONDARY_WEIGHT = 0.28


def blend_ovr(bat: float, bowl: float) -> int:
    hi, lo = max(bat, bowl), min(bat, bowl)
    return int(round(PRIMARY_WEIGHT * hi + SECONDARY_WEIGHT * lo))


def discipline(bat: float, bowl: float) -> str:
    """What this career actually is, for display."""
    gap = bat - bowl
    if gap >= 12:
        return "BATTING ALL-ROUNDER"
    if gap <= -12:
        return "BOWLING ALL-ROUNDER"
    return "ALL-ROUNDER"


# ---------------------------------------------------------------- projection
def project(days: int, matches_per_day=2.0, avg_runs=28, avg_wickets=1.0,
            win_rate=0.5, wage=30, streak=True) -> int:
    """Coins an active player earns over `days`. Used by the report tool to check
    the targets above are actually met, instead of being hoped for."""
    total = 0.0
    for d in range(days):
        lo, hi = daily_amount(d + 1 if streak else 1)
        day = (lo + hi) / 2.0
        for _ in range(int(matches_per_day)):
            day += match_payout(runs=avg_runs, wickets=avg_wickets,
                                fifties=1 if avg_runs >= 50 else 0,
                                won=False) * win_rate
            day += match_payout(runs=avg_runs, wickets=avg_wickets,
                                fifties=1 if avg_runs >= 50 else 0,
                                won=True) * (1 - win_rate)
            day += wage
        total += day
    return int(total)


def days_to_climb(from_ovr, to_ovr, **kw) -> float:
    """How many active days the climb takes at the given play rate."""
    need = cost_to_reach_ovr(from_ovr, to_ovr)
    if need <= 0:
        return 0.0
    lo, hi = 1, 4000
    while lo < hi:
        mid = (lo + hi) // 2
        if project(mid, **kw) >= need:
            hi = mid
        else:
            lo = mid + 1
    return lo
