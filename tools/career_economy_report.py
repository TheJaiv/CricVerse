"""
Print what the career economy actually does, so the balance is measured.

Reports the upgrade curve, what an active player earns, and how long each tier
climb takes at a few play rates - checked against the targets declared in
career/economy.py.

Run:  python3 tools/career_economy_report.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from career import economy as E

TIERS = [(60, 69, "Bronze  -> Silver", "bronze_to_silver"),
         (69, 77, "Silver  -> Gold", "silver_to_gold"),
         (77, 85, "Gold    -> Platinum", "gold_to_platinum"),
         (85, 93, "Plat    -> Diamond", "platinum_to_diamond")]

# (label, matches/day, avg runs, avg wickets, club wage)
PROFILES = [
    ("casual   (1 match/day)", 1.0, 22, 0.8, 25),
    ("regular  (2 matches/day)", 2.0, 28, 1.0, 42),
    ("grinder  (4 matches/day)", 4.0, 34, 1.3, 68),
]


def main():
    print("=" * 74)
    print("UPGRADE CURVE   cost = %.0f * %.3f^(v-%d)" % (E.UPGRADE_BASE, E.UPGRADE_RATE,
                                                         E.UPGRADE_FLOOR_OVR))
    print("=" * 74)
    print(f"{'attr value':>11} {'+1 costs':>10}   {'OVR':>4} {'cumulative to 99':>18}")
    for v in (60, 65, 70, 75, 80, 85, 90, 95, 98):
        print(f"{v:>11} {E.upgrade_cost(v):>10,}   {v:>4} "
              f"{E.cost_to_reach_ovr(v, 99):>18,}")

    print()
    print("=" * 74)
    print("DAILY INCOME")
    print("=" * 74)
    for d in (1, 5, 12, 30):
        lo, hi = E.daily_amount(d)
        print(f"  streak day {d:>2}: {lo}-{hi} coins")
    print(f"  match (28 runs, 1 wkt, loss): {E.match_payout(runs=28, wickets=1)}")
    print(f"  match (28 runs, 1 wkt, win):  {E.match_payout(runs=28, wickets=1, won=True)}")
    print(f"  match (fifty, 2 wkts, win):   "
          f"{E.match_payout(runs=62, wickets=2, fifties=1, won=True)}")

    print()
    print("=" * 74)
    print("PLAY vs LOGIN SHARE   (target: %.0f%% of income from playing)"
          % (E.PLAY_INCOME_SHARE * 100))
    print("  the target applies from 'regular' upward; someone playing one match a")
    print("  day is expected to be more login-weighted, and that is fine")
    print("=" * 74)
    for label, mpd, runs, wkts, wage in PROFILES:
        lo, hi = E.daily_amount(10)
        login = (lo + hi) / 2
        play = mpd * (E.match_payout(runs=runs, wickets=wkts) + wage)
        share = play / (play + login)
        flag = "ok" if share >= E.PLAY_INCOME_SHARE else "LOW"
        print(f"  {label:<26} login {login:6.0f}  play {play:7.0f}   "
              f"play share {share*100:5.1f}%  [{flag}]")

    print()
    print("=" * 74)
    print("TIER CLIMBS   (active days; target in brackets)")
    print("=" * 74)
    for label, mpd, runs, wkts, wage in PROFILES:
        print(f"\n  {label}")
        for lo_o, hi_o, name, key in TIERS:
            days = E.days_to_climb(lo_o, hi_o, matches_per_day=mpd, avg_runs=runs,
                                   avg_wickets=wkts, wage=wage)
            target = E.TARGETS[key]
            ratio = days / target
            flag = "ok" if 0.5 <= ratio <= 2.0 else ("FAST" if ratio < 0.5 else "SLOW")
            print(f"    {name:<20} {days:>5} days   "
                  f"[target {target:>3}]  {ratio:>4.1f}x  {flag}")

    print()
    print("=" * 74)
    print("SINKS")
    print("=" * 74)
    print(f"  rename                 {E.RENAME_COST:>7,}")
    print(f"  treat a 2-match injury {E.treatment_cost(2):>7,}")
    print(f"  training camp          {E.TRAINING_CAMP_COST:>7,}")
    print(f"  agent fee (42/match x14){E.agent_fee(42, 14):>6,}")

    print()
    print("=" * 74)
    print("RATING BLEND   OVR = %.2f*primary + %.2f*secondary"
          % (E.PRIMARY_WEIGHT, E.SECONDARY_WEIGHT))
    print("=" * 74)
    for bat, bowl in ((80, 80), (90, 60), (60, 90), (95, 45), (70, 68)):
        print(f"  bat {bat:>3}  bowl {bowl:>3}  ->  OVR {E.blend_ovr(bat, bowl):>3}"
              f"   {E.discipline(bat, bowl)}")


if __name__ == "__main__":
    main()
