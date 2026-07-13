"""
Career Mode hardcore verification harness (no Discord connection, no Mongo).

Covers, against the REAL code paths:
  Part 1 - career_manager unit tests (fake in-memory Mongo)
  Part 2 - career_match lobby logic
  Part 3 - career_ui card rendering + legacy trial
  Part 4 - bot.py headless end-to-end flows: interactive debut, batting &
           bowling scenarios (every difficulty), full 2v2 club match with bots,
           club payout winner logic (tie / super-over), bot-captain toss.

Run:  <python-with-discord.py> tools/career_flow_test.py
Exits non-zero if any check fails.
"""
import os
import sys
import random
import asyncio
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["CAREER_MODE"] = "1"

# Fake Mongo layer (installed BEFORE career_manager import)
class FakeCollection:
    def __init__(self):
        self.docs = {}
    def find(self, q=None):
        return list(self.docs.values())
    def find_one(self, q):
        return self.docs.get(q["_id"])
    def replace_one(self, q, doc, upsert=False):
        self.docs[q["_id"]] = doc
    def delete_one(self, q):
        class R: deleted_count = 0
        r = R()
        if q["_id"] in self.docs:
            del self.docs[q["_id"]]
            r.deleted_count = 1
        return r

class FakeDB(dict):
    def __getitem__(self, name):
        if name not in self:
            dict.__setitem__(self, name, FakeCollection())
        return dict.get(self, name)

FAKE_DB = FakeDB()

class InlineThread:
    """Runs the target synchronously so saves are deterministic in tests."""
    def __init__(self, target=None, args=(), kwargs=None):
        self._t, self._a, self._k = target, args, kwargs or {}
    def start(self):
        if self._t:
            self._t(*self._a, **self._k)

from career import career_manager as CM
CM._get_db = lambda: FAKE_DB
CM.Thread = InlineThread
from career import career_match as CMATCH
from career import career_ui
# Tiny test framework
PASS, FAIL = 0, []

def check(name, cond, detail=""):
    global PASS
    if cond:
        PASS += 1
    else:
        FAIL.append(f"{name}  {detail}")
        print(f"{name}  {detail}")

def section(title):
    print(f"\n── {title} " + "─" * max(0, 60 - len(title)))

def fresh(uid, name="Tester", bt="pace", ms="standard", debut=True, coins=0):
    CM.delete_career(uid)
    c, err = CM.create_career(uid, name, bt, ms)
    assert err is None, err
    c["debut_done"] = debut
    c["coins"] = coins
    CM.async_save_career(c)
    return c


# PART 1: career_manager
def part1():
    section("PART 1 · career_manager")

    # Creation: every bowling type × mindset lands exactly on BASE_OVR
    for bt in CM.BOWLING_TYPES:
        for ms in CM.MINDSETS:
            c = CM.new_career("x", "X", bt, ms)
            check(f"create {bt}/{ms} OVR=={CM.BASE_OVR}", c["ovr"] == CM.BASE_OVR, f"got {c['ovr']}")
            check(f"create {bt}/{ms} tier", c["tier"] == CM.tier_for_ovr(CM.BASE_OVR))

    # Tier boundaries
    for ovr, want in [(60, "Bronze"), (68, "Bronze"), (69, "Silver"), (76, "Silver"),
                      (77, "Gold"), (84, "Gold"), (85, "Platinum"), (92, "Platinum"),
                      (93, "Diamond"), (99, "Diamond")]:
        check(f"tier_for_ovr({ovr})", CM.tier_for_ovr(ovr) == want, CM.tier_for_ovr(ovr))
    check("next_tier_info(60)", CM.next_tier_info(60) == ("Silver", 69))
    check("next_tier_info(95)", CM.next_tier_info(95) == (None, None))

    # Upgrade economics
    costs = [CM.upgrade_cost(v) for v in range(60, 99)]
    check("upgrade_cost strictly increasing", all(b > a for a, b in zip(costs, costs[1:])))

    c = fresh("u1", coins=10**7)
    bought, spent, _ = CM.upgrade_attribute(c, "power", 5)
    check("upgrade buys requested", bought == 5)
    check("upgrade spends coins", spent > 0 and c["coins"] == 10**7 - spent)
    check("upgrade refreshes ovr", c["ovr"] == CM.compute_ovr(c))
    b2, s2, msg = CM.upgrade_attribute(c, "power", 500)
    check("upgrade caps at 99", c["attributes"]["power"] == 99)
    b3, s3, msg3 = CM.upgrade_attribute(c, "power", 1)
    check("maxed attr refuses", b3 == 0 and "maxed" in msg3)
    b4, s4, msg4 = CM.upgrade_attribute(c, "badattr", 1)
    check("unknown attr refuses", b4 == 0)
    poor = fresh("u2", coins=0)
    b5, s5, msg5 = CM.upgrade_attribute(poor, "control", 1)
    check("no coins refuses", b5 == 0 and "Not enough" in msg5)

    # Claims & cooldowns
    c = fresh("u3")
    amt, err = CM.claim_daily(c)
    check("daily pays", err is None and CM.DAILY_MIN <= amt <= CM.DAILY_MAX and c["coins"] == amt)
    amt2, err2 = CM.claim_daily(c)
    check("daily cooldown blocks", amt2 == 0 and err2 and "Already claimed" in err2)
    wamt, werr = CM.claim_weekly(c)
    check("weekly pays + boost", werr is None and wamt == CM.WEEKLY_AMOUNT
          and c["week_boost_until"] > 0)
    _, werr2 = CM.claim_weekly(c)
    check("weekly cooldown blocks", werr2 is not None)
    mamt, merr = CM.claim_monthly(c)
    check("monthly pays + title", merr is None and mamt == CM.MONTHLY_AMOUNT
          and c["cosmetic_title"] == "[Patron]")

    # Boost multiplies daily
    c2 = fresh("u4")
    c2["week_boost_until"] = int(__import__("time").time()) + 3600
    check("boost multiplier active", CM._boost_mult(c2) == CM.WEEK_BOOST)

    # Premium pass
    c3 = fresh("u5")
    check("not premium by default", not CM.career_is_premium(c3))
    CM.grant_premium(c3, 30)
    check("premium grant", CM.career_is_premium(c3) and CM.premium_remaining(c3) > 29 * 86400)
    CM.grant_premium(c3, 30)
    check("premium stacks", CM.premium_remaining(c3) > 59 * 86400)
    CM.grant_premium(c3, 0)
    check("premium revoke", not CM.career_is_premium(c3))

    # Match earnings
    c4 = fresh("u6")
    got = CM.award_match_earnings(c4, runs=60, fifties=1, wickets=2, won=True, is_real_match=True)
    check("earnings formula", got == 60 + 40 + (60 // 12) * 5 + 15 + 2 * 12, f"got {got}")
    got0 = CM.award_match_earnings(c4, runs=100, won=True, is_real_match=False)
    check("AI match pays zero", got0 == 0)

    # Quests: deterministic, 3 unique, claim once
    c5 = fresh("u7")
    q1 = CM._daily_quest_ids(c5["_id"])
    q2 = CM._daily_quest_ids(c5["_id"])
    check("quests deterministic", q1 == q2 and len(set(q1)) == CM.QUESTS_PER_DAY)
    CM._ensure_quests(c5)
    for metric in ("matches", "runs", "wickets", "fours", "sixes", "wins",
                   "scenarios", "fifties", "daily"):
        CM.quest_progress(c5, metric, 999)
    claimed = CM.claim_quests(c5)
    check("all active quests claimable", len(claimed) == CM.QUESTS_PER_DAY, f"got {len(claimed)}")
    check("quest coins paid", c5["coins"] == sum(q["reward"] for q in claimed))
    check("no double claim", CM.claim_quests(c5) == [])
    st = CM.quest_status(c5)
    check("quest_status all claimed", all(done for _, _, done, _ in st))

    # Scenario settle: loss never net-profitable, pass can profit, daily cap
    c6 = fresh("u8", coins=100)
    coins, capped, left = CM.scenario_complete(c6, runs=40, passed=False, mode="bat", difficulty="hard")
    check("scenario loss < entry fee", coins < CM.SCENARIO_ENTRY_FEE, f"got {coins}")
    c7 = fresh("u9", coins=100)
    coins2, _, _ = CM.scenario_complete(c7, runs=30, passed=True, mode="bat", difficulty="easy")
    check("scenario easy pass profits", coins2 > CM.SCENARIO_ENTRY_FEE, f"got {coins2}")
    c8 = fresh("u10", coins=0)
    for i in range(CM.SCENARIO_DAILY_CAP):
        CM.scenario_complete(c8, runs=10, passed=True, mode="bat")
    before = c8["coins"]
    coins3, capped3, left3 = CM.scenario_complete(c8, runs=100, passed=True, mode="bat")
    check("scenario daily cap", capped3 and coins3 == 0 and c8["coins"] == before and left3 == 0)
    check("scenario stats separate", c8["scenario_stats"]["played"] == CM.SCENARIO_DAILY_CAP + 1
          and c8["stats"]["bat"]["runs"] == 0)

    # Persistence round-trip via fake Mongo
    c9 = fresh("u11", coins=42)
    CM.CAREER_CACHE.clear()
    back = CM.get_career("u11")
    check("persistence round-trip", back and back["coins"] == 42)
    check("delete_career", CM.delete_career("u11") and CM.get_career("u11") is None)

    # career_to_engine shape
    c10 = fresh("u12", bt="legspin", ms="aggressor")
    eng = CM.career_to_engine(c10)
    check("engine shape", eng["role"] == "All-Rounder_Spin_Leg" and eng["archetype"] == "Aggressor"
          and 1 <= eng["bat"] <= 99 and 1 <= eng["bowl"] <= 99)


# PART 2: career_match
def part2():
    section("PART 2 · career_match lobby")
    for i in range(1, 9):
        fresh(100 + i, name=f"P{i}")
        cc = CM.get_career(100 + i)
        cc["attributes"]["power"] = 58 + i * 3
        CM.refresh_ovr(cc)

    lob = CMATCH.ClubLobby(1, 101, "P1", overs=5)
    check("host auto-joined", lob.has(101) and lob.count() == 1)
    ok, why = lob.add(101, "P1")
    check("double join blocked", not ok and why == "already_in")
    for i in range(2, 5):
        lob.add(100 + i, f"P{i}")
    check("teams equal 2v2", len(lob.team_a) == 2 == len(lob.team_b))
    check("is_ready 4 even", lob.is_ready() and lob.per_side() == 2)
    lob.add(105, "P5")
    check("odd not ready", not lob.is_ready())
    lob.remove(105)

    strengths = abs(lob.team_strength(lob.team_a) - lob.team_strength(lob.team_b))
    check("snake draft roughly balanced", strengths <= 12, f"gap {strengths}")

    ok, err = lob.swap(1, 3)
    check("swap valid", ok)
    ok2, err2 = lob.swap(1, 99)
    check("swap invalid num", not ok2)
    ok3, err3 = lob.swap(2, 2)
    check("swap same num", not ok3)

    ok, name = lob.add_bot()
    check("add_bot ok", ok and name == "Bot 1")
    bot_entry = next(p for p in lob.players if p.get("is_bot"))
    check("bot has career+ovr", bot_entry["career"]["ovr"] > 0)
    empty = CMATCH.ClubLobby(2, 999, "Ghost")
    empty.players = []
    ok, msg = empty.add_bot()
    check("add_bot needs humans", not ok)

    lob2 = CMATCH.ClubLobby(3, 101, "P1")
    for i in range(2, 23):
        lob2.add(1000 + i, f"X{i}")
    check("lobby caps at 22", lob2.count() == 22)
    ok, why = lob2.add(5000, "Overflow")
    check("23rd rejected", not ok and why == "full")

    check("each_side_has_human", lob.each_side_has_human())


# PART 3: career_ui
def part3():
    section("PART 3 · career_ui")
    c = fresh("u20", name="Card Tester")
    for tier, ovr in [("Bronze", 60), ("Silver", 70), ("Gold", 80), ("Platinum", 88), ("Diamond", 95)]:
        c["ovr"], c["tier"] = ovr, tier
        buf = career_ui.render_career_card(c)
        head = buf.getvalue()[:8]
        check(f"card renders {tier}", head[:4] == b"\x89PNG"[:4] and len(buf.getvalue()) > 5000)
    c["cosmetic_title"] = "[Patron]"
    c["debut_done"] = False
    check("card renders pending debut", career_ui.render_career_card(c).getvalue()[:4] == b"\x89PNG"[:4])

    passed, lines, headline = career_ui.run_debut_trial(CM.get_career("u20"))
    check("legacy trial returns sane", isinstance(passed, bool) and len(lines) == 3 and headline)


# PART 4: bot.py headless flows
import bot as B

B.increment_match_count = lambda fmt: 1
B.get_match_counts = lambda: {"t20": 1, "odi": 1, "test": 1}

class FUser:
    def __init__(self, uid, name):
        self.id, self.display_name = uid, name
        self.mention = f"<@{uid}>"
        self.bot = False

class FMessage:
    def __init__(self, channel, content=None, view=None, embed=None):
        self.channel, self.content, self.view, self.embed = channel, content, view, embed
    async def edit(self, **kw):
        if "view" in kw: self.view = kw["view"]
        if "content" in kw: self.content = kw["content"]
        return self

class FChannel:
    def __init__(self, cid):
        self.id = cid
        self.guild = None
        self.log = []
    async def send(self, content=None, *, embed=None, embeds=None, view=None, file=None, files=None, **kw):
        m = FMessage(self, content, view, embed)
        self.log.append(m)
        return m
    def text(self):
        parts = []
        for m in self.log:
            if m.content:
                parts.append(str(m.content))
            e = getattr(m, "embed", None)
            if e is not None:
                parts.append(f"{getattr(e, 'title', '') or ''}\n{getattr(e, 'description', '') or ''}")
        return "\n".join(parts)

class FResponse:
    def __init__(self, inter):
        self.inter = inter
        self._done = False
    async def defer(self, **kw): self._done = True
    async def edit_message(self, **kw):
        self._done = True
        if self.inter.message is not None:
            await self.inter.message.edit(**kw)
    async def send_message(self, *a, **kw): self._done = True
    async def send_modal(self, *a, **kw): self._done = True
    def is_done(self): return self._done

class FFollowup:
    def __init__(self, channel): self.channel = channel
    async def send(self, *a, **kw): return await self.channel.send(*a, **kw)

class FInteraction:
    def __init__(self, user, channel, message=None):
        self.user, self.channel, self.message = user, channel, message
        self.response = FResponse(self)
        self.followup = FFollowup(channel)

def make_inter(user, msg):
    return FInteraction(user, msg.channel, msg)


async def _act_on_view(msg, actors, rng):
    """Perform ONE user action on the message's live view. Returns True if acted."""
    view = msg.view
    import discord

    async def allowed(inter):
        chk = getattr(view, "interaction_check", None)
        if chk is None:
            return True
        try:
            res = chk(inter)
            if asyncio.iscoroutine(res):
                res = await res
            return bool(res)
        except Exception:
            return True

    async def pick_actor(build):
        for u in actors:
            inter = build(u)
            if await allowed(inter):
                return inter
        return None

    # Club team naming - both captains submit via record()
    if isinstance(view, B.ClubNameView):
        for uid in (view.cap_a_id, view.cap_b_id):
            if uid in view.names:
                continue
            u = next((a for a in actors if a.id == uid), None)
            if u:
                await view.record(FInteraction(u, msg.channel, msg), uid, f"Team of {u.display_name}")
                return True
        return False

    if isinstance(view, B.TossCallView):
        u = next((a for a in actors if a.id == view.match.p2_id), None)
        if not u:
            return False
        await view.handle_call(make_inter(u, msg), rng.choice(["Heads", "Tails"]))
        return True

    if isinstance(view, B.TossDecisionView):
        u = next((a for a in actors if a.id == view.match.toss_winner), None)
        if not u:
            return False
        await view.finalize_toss(make_inter(u, msg), rng.choice(["Bat", "Bowl"]))
        return True

    if isinstance(view, B.DRSView):
        inter = await pick_actor(lambda u: make_inter(u, msg))
        if inter:
            await view.btn_walk.callback(inter)
            view.processed = True
            return True
        return False

    if isinstance(view, B.BattingView):
        inter = await pick_actor(lambda u: make_inter(u, msg))
        if not inter:
            return False
        shot = rng.choice(["Drive", "Cut", "Pull", "Flick", "Loft", "Block"])
        await view.process_action(inter, shot, "shot")
        return True

    if isinstance(view, B.PaceBowlingView):
        inter = await pick_actor(lambda u: make_inter(u, msg))
        if not inter:
            return False
        await view.process_action(inter, rng.choice(["Inswing", "Outswing", "Slow", "Fast"]), "var")
        inter2 = FInteraction(inter.user, msg.channel, msg)
        await view.process_action(inter2, rng.choice(["Full", "Good", "Yorker"]), "len")
        return True

    if isinstance(view, B.SpinBowlingView):
        inter = await pick_actor(lambda u: make_inter(u, msg))
        if not inter:
            return False
        label = rng.choice([c.label for c in view.children
                            if getattr(c, "action_type", "") == "spin" and not c.disabled and c.label != "Mystery"])
        await view.process_action(inter, label, "spin")
        return True

    # Generic select views (bowler pick / openers / next batter)
    selects = [c for c in view.children if isinstance(c, discord.ui.Select)]
    if selects:
        sel = selects[0]
        usable = [o.value for o in sel.options if "Quota Full" not in o.label and "- Prev" not in o.label]
        if not usable:
            usable = [o.value for o in sel.options]
        n = max(sel.min_values, 1)
        sel._values = usable[:n] if n > 1 else [rng.choice(usable)]
        inter = await pick_actor(lambda u: make_inter(u, msg))
        if not inter:
            return False
        await sel.callback(inter)
        return True

    return False


def force_human_caps(lob):
    """Deterministically swap so slot 1 of each lobby team is a human."""
    def gnum(pred):
        for i, p in enumerate(lob.team_a + lob.team_b):
            if pred(p):
                return i + 1
        return None
    if lob.team_a and lob.team_a[0].get("is_bot"):
        avoid = lob.team_b[0] if lob.team_b else None
        n = gnum(lambda p: not p.get("is_bot") and p is not avoid)
        if n:
            lob.swap(n, 1)
    if lob.team_b and lob.team_b[0].get("is_bot"):
        n = gnum(lambda p: not p.get("is_bot") and p is not lob.team_a[0])
        if n:
            lob.swap(n, len(lob.team_a) + 1)


async def drive_match(channel, actors, max_steps=6000, seed=1):
    """Keep acting on the newest live view until the match leaves active_games."""
    rng = random.Random(seed)
    for step in range(max_steps):
        if channel.id not in B.active_games:
            return True
        pending = [m for m in channel.log if m.view is not None and not getattr(m, "_dead", False)]
        if not pending:
            raise AssertionError(f"STALL: match active but no live view.\nLast: {channel.log[-3:] and [m.content for m in channel.log[-5:]]}")
        m = pending[-1]
        acted = await _act_on_view(m, actors, rng)
        if not acted:
            m._dead = True
    raise AssertionError("drive_match exceeded max_steps (possible infinite loop)")


async def part4():
    section("PART 4 · headless flows (real bot.py code)")

    # Debut: strong rookie (should usually pass) and weak floor rookie
    for tag, boost, uid, cid in [("strong", 95, 201, 9001), ("base", None, 202, 9002)]:
        career = fresh(uid, name=f"Debutant{tag[:1].upper()}", debut=False)
        if boost:
            for k in CM.ATTRS:
                career["attributes"][k] = boost
            CM.refresh_ovr(career)
        ch = FChannel(cid)
        user = FUser(uid, career["username"])
        await B.start_debut_match(ch, user, career)
        check(f"debut[{tag}] match registered", cid in B.active_games)
        await drive_match(ch, [user], seed=uid)
        career = CM.get_career(uid)
        txt = ch.text()
        passed = "TRIAL PASSED" in txt
        failed = "TRIAL FAILED" in txt
        check(f"debut[{tag}] reached a verdict", passed or failed)
        check(f"debut[{tag}] debut_done == passed", career.get("debut_done", False) == passed)
        if passed:
            check(f"debut[{tag}] stats recorded", career["stats"]["bat"]["matches"] == 1)

    # Scenarios: bat + bowl across all difficulties
    scount = 0
    for mode in ("bat", "bowl"):
        for diff in ("easy", "medium", "hard"):
            scount += 1
            uid, cid = 300 + scount, 9100 + scount
            career = fresh(uid, name=f"Scen{scount}", coins=500)
            ch = FChannel(cid)
            user = FUser(uid, career["username"])
            view = B.ScenarioConfirmView(uid)
            view.difficulty = diff
            trigger = FMessage(ch, "scenario prompt", view)
            ch.log.append(trigger)
            await view._start(make_inter(user, trigger), mode)
            check(f"scenario {mode}/{diff} fee charged",
                  CM.get_career(uid)["coins"] <= 500 - CM.SCENARIO_ENTRY_FEE + 0)
            check(f"scenario {mode}/{diff} started", cid in B.active_games)
            await drive_match(ch, [user], seed=1000 + scount)
            career = CM.get_career(uid)
            check(f"scenario {mode}/{diff} settled", career["scenario_stats"]["played"] == 1)
            check(f"scenario {mode}/{diff} quest fed",
                  career.get("quests", {}).get("progress", {}).get("scenarios", 0) == 1)
            check(f"scenario {mode}/{diff} lifetime stats untouched",
                  career["stats"]["bat"]["matches"] == 0)

    # Scenario busy-channel guard: fee must not vanish
    uid, cid = 390, 9190
    career = fresh(uid, name="BusyScen", coins=100)
    ch = FChannel(cid)
    B.active_games[cid] = object()
    view = B.ScenarioConfirmView(uid)
    trigger = FMessage(ch, "x", view)
    ch.log.append(trigger)
    await view._start(make_inter(FUser(uid, "BusyScen"), trigger), "bat")
    del B.active_games[cid]
    check("scenario busy channel keeps coins", CM.get_career(uid)["coins"] == 100,
          f"got {CM.get_career(uid)['coins']}")

    # Club match: 2 humans + 2 bots, full interactive match
    u1 = fresh(401, name="CapAlpha", coins=0)
    u2 = fresh(402, name="CapBeta", coins=0)
    u1["attributes"]["power"] = 90; CM.refresh_ovr(u1); CM.async_save_career(u1)
    lob = CMATCH.ClubLobby(9200, 401, "CapAlpha", overs=2)
    lob.add(402, "CapBeta")
    lob.add_bot(); lob.add_bot()
    force_human_caps(lob)
    check("club lobby ready", lob.is_ready() and lob.each_side_has_human()
          and not lob.team_a[0].get("is_bot") and not lob.team_b[0].get("is_bot"))

    ch = FChannel(9200)
    host = FUser(401, "CapAlpha")
    p2 = FUser(402, "CapBeta")
    await B.start_club_match(ch, lob, host)
    check("club match registered", 9200 in B.active_games)
    match = B.active_games[9200]
    check("club match is ClubMatch", isinstance(match, B.ClubMatch))
    await drive_match(ch, [host, p2], seed=77)
    txt = ch.text()
    check("club payout ran", "Match Earnings" in txt, txt[-400:])
    a1, a2 = CM.get_career(401), CM.get_career(402)
    check("club coins paid", a1["coins"] > 0 and a2["coins"] > 0)
    check("club stats recorded", a1["stats"]["bat"]["matches"] == 1 and a2["stats"]["bat"]["matches"] == 1)
    check("club record kept", a1.get("club", {}).get("played") == 1)
    wins = (a1.get("club", {}).get("won", 0)) + (a2.get("club", {}).get("won", 0))
    tied = "Match tied" in txt
    check("club winner recorded (or tie)", tied or wins == 1, f"wins={wins}")
    check("club quest matches fed", a1["quests"]["progress"].get("matches") == 1)

    # Club payout winner logic: super-over-decided + genuine tie
    def craft_payout_match(so_winner=None, tie=False):
        t1 = {"name": "Alphas", "players": [{"name": "CapAlpha", "owner_id": 401, "bat": 70, "bowl": 60,
                                             "role": "All-Rounder_Pace", "archetype": "Standard"},
                                            {"name": "Bot A", "owner_id": -1101, "is_bot": True, "bat": 60,
                                             "bowl": 60, "role": "All-Rounder_Pace", "archetype": "Standard"}],
              "subs": [], "color": "#0f0"}
        t2 = {"name": "Betas", "players": [{"name": "CapBeta", "owner_id": 402, "bat": 70, "bowl": 60,
                                            "role": "All-Rounder_Pace", "archetype": "Standard"},
                                           {"name": "Bot B", "owner_id": -1102, "is_bot": True, "bat": 60,
                                            "bowl": 60, "role": "All-Rounder_Pace", "archetype": "Standard"}],
              "subs": [], "color": "#f00"}
        m = B.ClubMatch("CapAlpha", "CapBeta", 401, 402, t1, t2, format_overs=2)
        m.is_club = True
        m.innings1 = B.InningsState(t1, t2)
        m.innings2 = B.InningsState(t2, t1)
        m.innings1.total_runs = 20
        m.innings2.total_runs = 20 if tie or so_winner else 15
        m.target = 21
        if so_winner:
            m.tiebreak_winner_name = so_winner
        return m

    fresh(401, name="CapAlpha"); fresh(402, name="CapBeta")
    ch2 = FChannel(9201)
    await B._club_match_payout(ch2, craft_payout_match(so_winner="Betas"))
    a2 = CM.get_career(402)
    check("super-over winner gets club win", a2.get("club", {}).get("won", 0) == 1,
          f"club={a2.get('club')}")
    check("super-over payout headline", "Betas win" in ch2.text(), ch2.text()[-200:])

    fresh(401, name="CapAlpha"); fresh(402, name="CapBeta")
    ch3 = FChannel(9202)
    await B._club_match_payout(ch3, craft_payout_match(tie=True))
    check("true tie recorded as tie", "Match tied" in ch3.text()
          and CM.get_career(401).get("club", {}).get("won", 0) == 0)

    # Bot captain toss must auto-resolve (no deadlock)
    t1 = {"name": "Humans", "players": [{"name": "CapAlpha", "owner_id": 401, "bat": 70, "bowl": 60,
                                         "role": "All-Rounder_Pace", "archetype": "Standard"},
                                        {"name": "H2", "owner_id": 402, "bat": 60, "bowl": 60,
                                         "role": "All-Rounder_Pace", "archetype": "Standard"}],
          "subs": [], "color": "#0f0"}
    t2 = {"name": "Bots", "players": [{"name": "Bot 1", "owner_id": -1001, "is_bot": True, "bat": 65,
                                       "bowl": 65, "role": "All-Rounder_Pace", "archetype": "Standard"},
                                      {"name": "Bot 2", "owner_id": -1002, "is_bot": True, "bat": 65,
                                       "bowl": 65, "role": "All-Rounder_Pace", "archetype": "Standard"}],
          "subs": [], "color": "#f00"}
    m = B.ClubMatch("CapAlpha", "Bot 1", 401, -1001, t1, t2, format_overs=2)
    m.is_club = True
    m._caps = {"Humans": 401, "Bots": -1001}
    m._cap_a_id, m._cap_b_id = 401, -1001
    m.max_wickets = 2
    m.bowler_quota = 1
    ch4 = FChannel(9203)
    B.active_games[9203] = m
    await B._club_begin_toss(ch4, m)
    # A bot can never click "call the coin" - the fix must never send TossCallView here.
    stuck = [x for x in ch4.log if isinstance(x.view, B.TossCallView)]
    check("bot-captain toss auto-resolved", not stuck,
          "TossCallView sent to a bot captain (deadlock)")
    if stuck:
        B.active_games.pop(9203, None)
    else:
        # Play it out to prove the whole bot-vs-human match completes.
        await drive_match(ch4, [FUser(401, "CapAlpha"), FUser(402, "H2")], seed=5)
        check("bot-captain match completes", 9203 not in B.active_games)

    # Tied club match: super over must stay a club match
    m2 = craft_payout_match(tie=True)
    m2.current_innings_num = 2
    m2.current_innings = m2.innings2
    ch5 = FChannel(9204)
    B.active_games[9204] = m2
    try:
        await B.trigger_super_over(ch5, m2)
        so = B.active_games.get(9204)
        check("club super over keeps is_club", getattr(so, "is_club", False),
              f"type={type(so).__name__}")
        check("club super over is ClubMatch", isinstance(so, B.ClubMatch), type(so).__name__)
    finally:
        B.active_games.pop(9204, None)


async def part5_stress(n=8):
    """Many random seeds across every interactive flow - catches flaky paths
    (DRS, last-man rule, quota exhaustion, ties, free hits, bot turns)."""
    section(f"PART 5 · stress ({n} debuts · {n} scenarios · {n} club matches)")

    for t in range(n):
        uid, cid = 700 + t, 9500 + t
        career = fresh(uid, name=f"StressD{t}", debut=False)
        ch = FChannel(cid)
        user = FUser(uid, career["username"])
        await B.start_debut_match(ch, user, career)
        await drive_match(ch, [user], seed=9000 + t)
        check(f"stress debut #{t} completes", cid not in B.active_games)

    for t in range(n):
        uid, cid = 730 + t, 9530 + t
        career = fresh(uid, name=f"StressS{t}", coins=200)
        ch = FChannel(cid)
        user = FUser(uid, career["username"])
        mode = "bat" if t % 2 == 0 else "bowl"
        diff = ("easy", "medium", "hard")[t % 3]
        view = B.ScenarioConfirmView(uid)
        view.difficulty = diff
        trigger = FMessage(ch, "prompt", view)
        ch.log.append(trigger)
        await view._start(make_inter(user, trigger), mode)
        await drive_match(ch, [user], seed=9100 + t)
        check(f"stress scenario #{t} ({mode}/{diff}) settles",
              CM.get_career(uid)["scenario_stats"]["played"] == 1)

    for t in range(n):
        cid = 9600 + t
        uids = [800 + t * 10 + k for k in range(4)]
        actors = []
        for k, u in enumerate(uids):
            c = fresh(u, name=f"SC{t}x{k}")
            c["attributes"]["power"] = 58 + (k * 9 + t * 5) % 38
            CM.refresh_ovr(c)
            CM.async_save_career(c)
            actors.append(FUser(u, c["username"]))
        lob = CMATCH.ClubLobby(cid, uids[0], f"SC{t}x0", overs=2 + (t % 3))
        for u in uids[1:]:
            lob.add(u, CM.get_career(u)["username"])
        if t % 2:
            lob.add_bot()
            lob.add_bot()
        force_human_caps(lob)
        if not lob.each_side_has_human():
            check(f"stress club #{t} lobby arrangeable", False)
            continue
        ch = FChannel(cid)
        await B.start_club_match(ch, lob, actors[0])
        await drive_match(ch, actors, seed=9200 + t)
        check(f"stress club #{t} completes", cid not in B.active_games)
        check(f"stress club #{t} payout ran", "Match Earnings" in ch.text(),
              ch.text()[-300:])


def main():
    random.seed(42)
    part1()
    part2()
    part3()
    asyncio.run(part4())
    if os.environ.get("STRESS", "1") == "1":
        asyncio.run(part5_stress(int(os.environ.get("STRESS_N", "8"))))

    print("\n" + "=" * 64)
    print(f"{PASS} checks passed   ·    {len(FAIL)} failed")
    for f in FAIL:
        print(f"   FAIL: {f}")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
