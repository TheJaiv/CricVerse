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
import time
import random
import asyncio
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["CAREER_MODE"] = "1"
# The live card's rate-limit window is real-Discord pacing; with fake channels it
# would just add ~1.5s of sleep per delivery. Part 6 raises it back to assert the
# throttle actually works.
os.environ.setdefault("CAREER_GUI_EDIT_INTERVAL", "0")

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
    def __init__(self, channel, content=None, view=None, embed=None, file=None):
        self.channel, self.content, self.view, self.embed = channel, content, view, embed
        self.file = file
        self.edits = 0            # broadcast card is EDITED per ball, not re-sent
        self.id = id(self)
        self.deleted = False
    async def edit(self, **kw):
        if "view" in kw: self.view = kw["view"]
        if "content" in kw: self.content = kw["content"]
        if "attachments" in kw and kw["attachments"]:
            self.file = kw["attachments"][0]
            self.edits += 1
        return self
    async def delete(self):
        self.deleted = True

class FChannel:
    def __init__(self, cid):
        self.id = cid
        self.guild = None
        self.log = []
    async def send(self, content=None, *, embed=None, embeds=None, view=None, file=None, files=None, **kw):
        m = FMessage(self, content, view, embed, file or (files[0] if files else None))
        self.log.append(m)
        return m
    def files(self, suffix=None):
        out = [m.file for m in self.log if m.file is not None]
        if suffix:
            out = [f for f in out if str(getattr(f, "filename", "")).endswith(suffix)]
        return out
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


async def part6_broadcast():
    """Broadcast GUI: ball records, snapshot, live card, replay clips."""
    section("PART 6 · broadcast GUI (live card · replays · ball feed)")
    from career import snapshot as SNAP
    from career import ballfeed as BF
    from career import live as CLIVE
    from career.ui import theme as TH
    from career.ui import broadcast as BC
    from career.ui import motion as MO

    check("fonts are vendored", TH.fonts_are_vendored())

    u1 = fresh(601, name="BcastA", coins=0)
    fresh(602, name="BcastB", coins=0)
    lob = CMATCH.ClubLobby(9600, 601, "BcastA", overs=3)
    lob.add(602, "BcastB")
    lob.add_bot(); lob.add_bot()
    force_human_caps(lob)
    ch = FChannel(9600)
    host, p2 = FUser(601, "BcastA"), FUser(602, "BcastB")
    await B.start_club_match(ch, lob, host)
    match = B.active_games[9600]

    # snapshot must survive being called before a single ball is bowled
    st = SNAP.build_broadcast_state(match)
    check("snapshot builds pre-first-ball", isinstance(st, dict) and "batting" in st)

    await drive_match(ch, [host, p2], seed=606)

    cards = ch.files("live.png")
    check("live card posted", len(cards) >= 1, f"got {len(cards)}")
    card_msgs = [m for m in ch.log if m.file is not None and getattr(m.file, "filename", "") == "live.png"]

    # A card per ball, all kept: scrolling back through the images IS the match
    # history. Nothing is deleted; only the previous message's buttons are stripped
    # so the sole live controls are on the newest image.
    kept = [m for m in card_msgs if not m.deleted]
    check("every card is kept (the image feed is the history)",
          len(kept) == len(card_msgs), f"{len(card_msgs) - len(kept)} deleted")
    check("more than one card per over (a card per ball)",
          len(card_msgs) > max(1, match.innings1.total_balls // 6), f"got {len(card_msgs)}")
    check("superseded cards have their buttons stripped",
          all(m.view is None for m in card_msgs[:-1]),
          f"{sum(1 for m in card_msgs[:-1] if m.view is not None)} still clickable")
    check("every card message carries its prompt text",
          all(m.content for m in card_msgs))

    # The feed must read as images, not text: every ball prompt rides on a card,
    # and the old per-ball commentary lines are gone.
    prompts = [m for m in ch.log if m.content and "pick your" in str(m.content)]
    check("every ball prompt is posted on a card image",
          prompts and all(m.file is not None for m in prompts),
          f"{sum(1 for m in prompts if m.file is None)} bare prompts of {len(prompts)}")
    txt = ch.text()
    check("no per-ball commentary lines are posted", "🎙️" not in txt)
    check("no 'Select your shot' text prompts remain", "Select your shot" not in txt)

    hist = (match.innings1.ball_history or []) + (getattr(match.innings2, "ball_history", None) or [])
    check("ball history captured for the match", len(hist) > 10, f"{len(hist)} balls")
    check("every record carries an outcome", all(r.get("outcome") for r in hist))

    # charts must appear for BOTH innings, not just the innings-1 break
    charts = ch.files("manhattan.png")
    check("runs-per-over chart posted for both innings", len(charts) >= 2,
          f"got {len(charts)}")

    gifs = ch.files("replay.gif")
    highlights = [r for r in hist if BF.is_highlight(r)]
    check("replays only when a highlight happened",
          len(gifs) <= len(highlights), f"{len(gifs)} gifs vs {len(highlights)} highlights")
    check("replay budget respected", len(gifs) <= CLIVE.MAX_GIFS_PER_MATCH)

    # renderers produce real, decodable images
    import io
    from PIL import Image
    png = BC.render_live_card(SNAP.build_broadcast_state(match))
    im = Image.open(io.BytesIO(png.getvalue()))
    check("live card is a valid PNG of the right size", im.size == (BC.W, BC.H), str(im.size))

    if hist:
        rec = next((r for r in hist if r.get("dismissal")), hist[-1])
        gif = MO.build_replay_gif(rec)
        g = Image.open(io.BytesIO(gif.getvalue()))
        check("replay gif is animated", getattr(g, "n_frames", 1) > 5, f"{getattr(g,'n_frames',1)} frames")
        check("replay gif stays under 400 KB", len(gif.getvalue()) < 400_000,
              f"{len(gif.getvalue())//1024} KB")
        verdict = {"decision": "OUT", "pitching_call": "IN LINE", "impact_call": "IN LINE",
                   "hitting_call": "HITTING", "summary": "three reds"}
        drs = MO.build_drs_gif(rec, verdict)
        check("drs gif renders", len(drs.getvalue()) > 1000)
        geo = BF.geometry(rec)
        check("geometry is deterministic", geo == BF.geometry(rec))
        check("geometry stays in range",
              -1 <= geo["line"] <= 1 and 0 <= geo["pitch_frac"] <= 1)

    # PLAYTEST REGRESSIONS
    # 1. a NON-career match must still get the plain text scoreboard, and must not
    #    recurse (the fallback line inside _send_scoreboard was once rewritten to
    #    call itself, which would blow the stack on every casual match)
    plain_ch = FChannel(9610)
    plain = B.CricketMatch("A", "B", 1, 2,
                           {"name": "A", "players": [], "subs": []},
                           {"name": "B", "players": [], "subs": []},
                           format_overs=5, pitch="Hard", weather="Clear")
    plain.innings1 = B.InningsState(plain.team1, plain.team2)
    plain.current_innings = plain.innings1
    try:
        await B._send_scoreboard(plain_ch, plain)
        ok = any(m.embed is not None for m in plain_ch.log)
    except RecursionError:
        ok = False
    check("non-career match still gets the text scoreboard (no recursion)", ok)

    # 2. career matches must NOT get a second card from _send_scoreboard
    card_ch = FChannel(9611)
    before = len(card_ch.log)
    await B._send_scoreboard(card_ch, match)
    check("career _send_scoreboard does not duplicate the card",
          len(card_ch.log) == before)

    # 3. replays must fire in BOTH innings - the gap check used to compare an
    #    innings-2 ball index against an innings-1 one and suppress every clip
    i2_hist = getattr(match.innings2, "ball_history", None) or []
    i2_high = [r for r in i2_hist if BF.is_highlight(r)]
    if i2_high:
        s2 = CLIVE.Session(FChannel(9612))
        s2.last_gif_ball = (1, 40)          # as if innings 1 ended at ball 40
        rec = dict(i2_high[0]); rec["innings"] = 2; rec["ball_index"] = 3
        match.last_ball = rec
        await s2.highlight(match)
        check("innings-2 replay is not suppressed by innings-1 state",
              len(s2.channel.files("replay.gif")) == 1,
              f"gifs={len(s2.channel.files('replay.gif'))}")

    # 4. a turn posts ONE prompt message carrying the card, and the result line
    turn_ch = FChannel(9613)
    match.last_ball = dict((match.innings1.ball_history or [{}])[-1])
    msg = await CLIVE.turn(turn_ch, match, "pick your shot", None)
    check("turn posts the card with the prompt",
          msg.file is not None and msg.content == "pick your shot")
    check("turn posts no text-only message (result is on the card)",
          all(m.file is not None for m in turn_ch.log),
          f"{sum(1 for m in turn_ch.log if m.file is None)} text-only of {len(turn_ch.log)}")
    check("the ball result is drawn on the card, not sent as chat",
          CLIVE.SNAP.build_broadcast_state(match).get("last_ball") is not None)
    CLIVE.end(9613)

    # 5. consecutive balls: both cards survive, only the newest keeps its buttons
    ov_ch = FChannel(9614)
    m1 = await CLIVE.turn(ov_ch, match, "a", None)
    m2 = await CLIVE.turn(ov_ch, match, "b", None)
    check("previous ball's card is not deleted", not m1.deleted)
    check("a fresh card is posted for the next ball", m2 is not m1)
    CLIVE.end(9614)

    # forced updates must still honour the edit window (a per-ball edit storm
    # during bot-vs-bot stretches is what earns a 429)
    sess = CLIVE.session_for(ch)
    _saved = CLIVE.EDIT_INTERVAL
    CLIVE.EDIT_INTERVAL = 0.4
    await sess.push(match)          # establish the card - only an EDIT is throttled
    check("session holds a card message after the first push", sess.message is not None)
    sess.last_edit = time.time()
    t0 = time.time()
    await sess.push(match, force=True)
    waited = time.time() - t0
    CLIVE.EDIT_INTERVAL = _saved
    check("forced card update waits out the edit window", waited >= 0.3, f"{waited:.2f}s")

    # a failing renderer must fall back to the text embed, not kill the match
    ch2 = FChannel(9601)
    sess = CLIVE.session_for(ch2)
    sess.failed = True
    ok = await sess.push(match)
    check("dead session reports failure so the caller falls back", ok is False)
    CLIVE.end(9601)


async def part7_access_and_admin():
    """Access gate (career mode is admin-only during the rebuild) + owner tools."""
    section("PART 7 · access gate · owner ratings dump")

    class FAuthor:
        def __init__(self, uid, admin=False):
            self.id = uid
            self.name = f"u{uid}"
            self.display_name = f"u{uid}"

            class P: administrator = admin
            self.guild_permissions = P()

    class FGuild:
        id = 4242

    class FCtx:
        def __init__(self, uid, admin=False, guild=True):
            self.author = FAuthor(uid, admin)
            self.guild = FGuild() if guild else None
            self.channel = FChannel(9700)
            self.sent = []
            self.bot = None
        async def send(self, content=None, **kw):
            self.sent.append((content, kw))
            return FMessage(self.channel, content)

    owner = FCtx(B.ADMIN_DISCORD_ID)
    rando = FCtx(500123)
    guild_admin = FCtx(500124, admin=True)

    check("public flag is off (career is admin-only)", B.CAREER_PUBLIC is False)
    check("owner passes the gate", B._can_use_career(owner) is True)
    check("guild admin passes the gate", B._can_use_career(guild_admin) is True)
    check("ordinary user is BLOCKED", B._can_use_career(rando) is False)

    # the gate must not be dead code: flipping the public flag reopens it
    _saved = B.CAREER_PUBLIC
    B.CAREER_PUBLIC = True
    check("public flag reopens the mode", B._can_use_career(rando) is True)
    B.CAREER_PUBLIC = _saved
    check("flag restored", B._can_use_career(rando) is False)

    # kill switch still wins over everything
    _mode = B.CAREER_MODE_ENABLED
    B.CAREER_MODE_ENABLED = False
    check("CAREER_MODE=0 blocks even the owner", B._can_use_career(owner) is False)
    B.CAREER_MODE_ENABLED = _mode

    # every career command must be behind the gate
    import inspect
    cog = None
    for c in (B.PrefixCog,):
        cog = c
    src = inspect.getsource(cog)
    career_cmds = ["start_career", "profile", "stats", "debut", "daily", "quests", "scenario",
                   "balance", "upgrade", "create_match", "joinmatch", "startmatch"]
    missing = [n for n in career_cmds
               if f"async def {n}" in src
               and "_can_use_career" not in src.split(f"async def {n}", 1)[1][:900]]
    check("every sampled career command calls the gate", not missing, f"ungated: {missing}")

    # owner ratings dump
    fresh(701, name="DumpA", coins=500)
    c2 = fresh(702, name="DumpB", coins=90)
    c2["attributes"]["power"] = 95
    CM.refresh_ovr(c2); CM.async_save_career(c2)

    class FBot:
        def get_user(self, uid): return None

    cog_inst = B.PrefixCog(FBot())
    owner.bot = FBot()
    await B.PrefixCog.all_ratings.callback(cog_inst, owner)
    blob = "\n".join(str(c) for c, _ in owner.sent if c)
    check("dump lists the careers", "DumpA" in blob and "DumpB" in blob, blob[:200])
    check("dump reports a summary", "careers" in blob and "avg OVR" in blob)
    files = [kw.get("file") for _, kw in owner.sent if kw.get("file")]
    check("dump attaches a CSV", any(getattr(f, "filename", "") == "career_ratings.csv" for f in files))

    denied = FCtx(500125)
    denied.bot = FBot()
    await B.PrefixCog.all_ratings.callback(cog_inst, denied)
    check("dump is owner-only", any("Owner-only" in str(c) for c, _ in denied.sent))


def part8_engine_isolation():
    """The ball feed must be observation only, and career-only.

    Guards two promises: (1) recording consumes no randomness, so a match plays
    out identically with it on or off - the engine's simulation logic is
    untouched; (2) nothing outside career mode pays for it.
    """
    section("PART 8 · engine isolation (ball feed changes nothing)")

    def _team(name, seed):
        rng = random.Random(seed)
        roles = ["Batter"] * 5 + ["All-Rounder_Pace", "All-Rounder_Spin"] + \
                ["Bowler_Pace", "Bowler_Pace", "Bowler_Spin_Off", "Bowler_Spin_Leg"]
        return {"name": name, "subs": [], "players": [
            {"name": f"{name}{i}", "bat": rng.randint(55, 92), "bowl": rng.randint(40, 88),
             "role": roles[i], "archetype": rng.choice(["Aggressor", "Anchor", "Standard"])}
            for i in range(11)]}

    def _run(seed, record, overs=20):
        random.seed(seed)
        t1, t2 = _team("A", 1), _team("B", 2)
        m = B.CricketMatch("A", "B", 1, 2, t1, t2, format_overs=overs,
                           pitch="Hard", weather="Clear")
        m.sim_only = True; m.verbose = False
        m.simulation_mode = "whole_match"; m._defer_stats = True
        if record:
            m.record_balls = True
        m.batting_first_id, m.bowling_first_id = 1, 2
        m.innings1 = B.InningsState(t1, t2)
        m.current_innings = m.innings1
        B._run_full_match_sync(m)
        return m

    def _card(i):
        return (i.total_runs, i.wickets, i.total_balls, getattr(i, "extras", 0),
                tuple(sorted((n, s.runs_scored, s.balls_faced, s.dismissal)
                             for n, s in i.batting_stats.items())),
                tuple(sorted((n, s.runs_conceded, s.balls_bowled, s.wickets_taken)
                             for n, s in i.bowling_stats.items())))

    mismatches = []
    for seed in range(12):
        off, on = _run(seed, False), _run(seed, True)
        if (_card(off.innings1), _card(off.innings2)) != (_card(on.innings1), _card(on.innings2)):
            mismatches.append(seed)
    check("recording never changes the simulation (12 seeds, T20)",
          not mismatches, f"differed at seeds {mismatches}")

    odi_off, odi_on = _run(101, False, overs=50), _run(101, True, overs=50)
    check("recording never changes the simulation (ODI)",
          (_card(odi_off.innings1), _card(odi_off.innings2))
          == (_card(odi_on.innings1), _card(odi_on.innings2)))

    plain = _run(5, False)
    check("non-career match stores no ball history",
          not hasattr(plain.innings1, "ball_history"))
    check("non-career match stores no last_ball",
          getattr(plain, "last_ball", None) is None)

    for attr in ("is_club", "is_debut", "is_scenario"):
        m = _run(6, False)
        setattr(m, attr, True)
        from engine.ball_record import wants_records
        check(f"{attr} matches opt into the feed", wants_records(m) is True)
    check("a plain match does not opt in",
          __import__("engine.ball_record", fromlist=["x"]).wants_records(plain) is False)


async def part9b_drs_live():
    """The bot.py wiring: a career review end to end, budget charged, clip posted."""
    from career import drs as DRS
    from career import live as CLIVE

    # A real career match played out headlessly, so last_ball is a genuine record
    # written by the engine rather than a hand-built dict.
    def _t(name, seed):
        rng = random.Random(seed)
        roles = ["Batter"] * 5 + ["All-Rounder_Pace", "All-Rounder_Spin"] + \
                ["Bowler_Pace", "Bowler_Pace", "Bowler_Spin_Off", "Bowler_Spin_Leg"]
        return {"name": name, "subs": [], "color": "#2E6BE6", "players": [
            {"name": f"{name}{i}", "bat": rng.randint(55, 92), "bowl": rng.randint(40, 88),
             "role": roles[i], "archetype": "Standard"} for i in range(11)]}

    random.seed(4242)
    t1, t2 = _t("Alpha", 1), _t("Bravo", 2)
    m = B.ClubMatch("A", "B", 1, 2, t1, t2, format_overs=5, pitch="Hard", weather="Clear")
    m.is_club = True
    m.sim_only = True
    m.simulation_mode = "whole_match"
    m._defer_stats = True
    m.batting_first_id, m.bowling_first_id = 1, 2
    m.innings1 = B.InningsState(t1, t2)
    m.current_innings = m.innings1
    B._run_full_match_sync(m)
    m.current_innings = m.innings1
    m.prev_striker_idx = m.innings1.current_striker_idx

    ch = FChannel(9900)
    rec = getattr(m, "last_ball", None)
    check("a ball record exists to review", rec is not None)

    m.drs_dismissal = "LBW"
    before = DRS.reviews_left(m, "batting")
    verdict = await B._run_career_review(ch, m, "batting")
    check("career review returns a verdict", isinstance(verdict, dict) and "decision" in verdict)
    after = DRS.reviews_left(m, "batting")
    check("a review is charged only when it fails",
          after == (before if verdict.get("retained") else before - 1),
          f"{before} -> {after}, retained={verdict.get('retained')}")
    check("the verdict is announced in the channel",
          "DRS" in ch.text() and verdict["decision"] in ch.text())
    check("a ball-tracking clip is posted", len(ch.files("drs.gif")) == 1,
          f"{len(ch.files('drs.gif'))} clips")

    # exhausting the budget must actually lock further reviews out
    m.drs_reviews = {"batting": 0, "bowling": 2}
    check("an exhausted side cannot review", DRS.can_review(m, "batting") is False)
    check("the other side still can", DRS.can_review(m, "bowling") is True)

    CLIVE.end(9900)


def part9_drs():
    """Phase 2: review budgets, real adjudication, one shared rewind."""
    section("PART 9 · DRS (budget · adjudication · rewind)")
    from career import drs as DRS

    class M:
        current_innings_num = 1
        max_wickets = 10

    m = M()
    b = DRS.ensure_budget(m)
    check("both sides start with 2 reviews",
          b == {"batting": 2, "bowling": 2}, str(b))

    # a failed review is spent, a successful one and an umpire's call are kept
    DRS._spend(m, "batting", retained=False)
    check("a failed review is spent", DRS.reviews_left(m, "batting") == 1)
    DRS._spend(m, "batting", retained=True)
    check("a retained review is not spent", DRS.reviews_left(m, "batting") == 1)
    DRS._spend(m, "batting", retained=False)
    check("budget can be exhausted", DRS.reviews_left(m, "batting") == 0)
    check("can_review is false when exhausted", DRS.can_review(m, "batting") is False)
    check("the other side is unaffected", DRS.reviews_left(m, "bowling") == 2)

    m.current_innings_num = 2
    check("budget resets at the innings change",
          DRS.ensure_budget(m) == {"batting": 2, "bowling": 2})

    # adjudication is driven by the delivery, not a coin flip
    def rec(**kw):
        base = {"innings": 1, "ball_index": 12, "over": 2, "ball": 1,
                "bowler": "Khan", "striker": "Patel", "delivery": "Inswing Full",
                "shot": "Flick", "runs_off_bat": 0, "extras": 0, "is_wide": False,
                "is_no_ball": False, "is_bye": False, "dismissal": "LBW",
                "dismissal_desc": "lbw b. Khan", "bad_shot": False}
        base.update(kw)
        return base

    # Sample across delivery types: a full inswinger is genuinely almost always
    # hitting, so one delivery cannot produce the whole range of verdicts.
    delivs = ["Inswing Full", "Outswing Good", "Fast Bouncer", "Off spin",
              "Leg spin", "Slow Good", "Fast Yorker"]
    seen = set()
    for i in range(210):
        v = DRS.adjudicate(rec(ball_index=i, delivery=delivs[i % len(delivs)]),
                           "LBW", "batting")
        seen.add(v["decision"])
        check_once = None
        if v["decision"] == "UMPIRE'S CALL":
            check_once = v["retained"] and not v["overturned"]
            if not check_once:
                check("umpire's call retains the review and stands", False, str(v))
                break
    check("LBW reviews produce more than one outcome", len(seen) > 1, str(seen))
    check("umpire's call band is reachable", "UMPIRE'S CALL" in seen, str(seen))
    check("both OUT and NOT OUT are reachable",
          {"OUT", "NOT OUT"} <= seen, str(seen))

    v = DRS.adjudicate(rec(), "Caught Behind", "batting")
    check("caught-behind reviews report an edge verdict",
          v["hitting_call"] in ("SPIKE DETECTED", "NO SPIKE"), v["hitting_call"])
    check("a verdict always carries what the clip needs",
          all(k in v for k in ("decision", "summary", "zones", "pitching_call",
                               "impact_call", "hitting_call")))

    # verdicts must be internally consistent: never overturn AND uphold
    for i in range(60):
        v = DRS.adjudicate(rec(ball_index=500 + i), "LBW", "bowling")
        if v["overturned"] and v["decision"] == "NOT OUT":
            check("a bowling overturn never reports NOT OUT", False, str(v))
            break
    else:
        check("bowling-side verdicts are self-consistent", True)

    # the rewind: a dismissal is undone cleanly, including the end-change case
    class Stats:
        def __init__(self):
            self.dismissal = "lbw b. Khan"
            self.wickets_taken = 3
            self.runs_scored = 10
            self.balls_faced = 8

    class Inn:
        pass

    inn = Inn()
    inn.wickets = 3
    inn.next_batter_idx = 4
    inn.current_striker_idx = 3
    inn.current_non_striker_idx = 1
    inn.batting_team = {"players": [{"name": f"P{i}"} for i in range(6)]}
    inn.batting_stats = {f"P{i}": Stats() for i in range(6)}
    inn.current_bowler = {"name": "Khan"}
    inn.bowling_stats = {"Khan": Stats()}
    inn.over_log = ["<:wicket:1520143043683156051>"]
    inn.ball_history = [rec()]

    class M2:
        pass

    m2 = M2()
    m2.current_innings = inn
    m2.prev_striker_idx = 2
    m2.pending_next_batter = False
    m2.last_ball = inn.ball_history[-1]

    DRS.undo_wicket(m2)
    check("rewind decrements the wicket", inn.wickets == 2)
    check("rewind un-promotes the replacement", inn.next_batter_idx == 3)
    check("rewind restores the reprieved batter to the crease",
          inn.current_striker_idx == 2)
    check("rewind marks him not out", inn.batting_stats["P2"].dismissal == "not out")
    check("rewind takes the wicket off the bowler", inn.bowling_stats["Khan"].wickets_taken == 2)
    check("rewind fixes the timeline emoji",
          inn.over_log[-1] == "<:0run:1520141253604544633>")
    check("rewind clears the dismissal from the ball feed",
          m2.last_ball["dismissal"] is None)

    # end-change case: replacement parked at the NON-striker end
    inn.wickets = 3
    inn.next_batter_idx = 4
    inn.current_striker_idx = 1
    inn.current_non_striker_idx = 3
    inn.batting_stats["P2"].dismissal = "lbw b. Khan"
    DRS.undo_wicket(m2)
    check("rewind handles the end-change (wicket on the last ball of an over)",
          inn.current_non_striker_idx == 2 and inn.current_striker_idx == 1,
          f"striker={inn.current_striker_idx} non={inn.current_non_striker_idx}")

    # AI review decision respects the budget
    m3 = M()
    DRS.ensure_budget(m3)
    m3.drs_reviews = {"batting": 0, "bowling": 0}
    m3.current_innings = inn
    check("AI never reviews with an empty budget",
          DRS.ai_should_review(m3, rec(), "LBW", "batting") is False)


def part10_condition():
    """Phase 3: form, fitness, injuries - and the bound on what they can move."""
    section("PART 10 · condition (form · fitness · injury)")
    from career import condition as CD

    legacy = {"_id": "900", "username": "Legacy", "bowling_type": "pace",
              "mindset": "standard", "attributes": dict(CM.BASE_ATTRS),
              "coins": 0, "stats": CM._blank_stats()}
    CD.ensure(legacy)
    check("legacy career gains condition fields",
          all(k in legacy for k in ("form", "fitness", "workload")))
    check("legacy career starts neutral",
          CD.form(legacy) == CD.FORM_BASE and CD.fitness(legacy) == 100)
    check("neutral condition barely moves ratings", abs(CD.rating_modifier(legacy)) < 0.6,
          str(CD.rating_modifier(legacy)))

    # form responds to performance, in the right direction
    hot = dict(legacy, form={"rating": 50, "recent": []})
    CD.ensure(hot)
    for _ in range(5):
        CD.record_innings(hot, runs=70, balls=40, wickets=2, balls_bowled=24, out=True)
    cold = dict(legacy, form={"rating": 50, "recent": []})
    CD.ensure(cold)
    for _ in range(5):
        CD.record_innings(cold, runs=1, balls=5, wickets=0, balls_bowled=24, out=True)
    check("good returns raise form", CD.form(hot) > 60, f"form={CD.form(hot)}")
    check("failures lower form", CD.form(cold) < 40, f"form={CD.form(cold)}")
    check("form is recorded per innings", len(hot["form"]["recent"]) == 5)
    check("in-form player gets a positive modifier", CD.rating_modifier(hot) > 0)
    check("out-of-form player gets a negative modifier", CD.rating_modifier(cold) < 0)

    # THE BOUND: condition must never move a rating more than +/-3
    worst = dict(legacy, form={"rating": 0, "recent": []},
                 fitness={"value": 0, "injury": None, "updated": int(time.time())})
    best = dict(legacy, form={"rating": 100, "recent": []},
                fitness={"value": 100, "injury": None, "updated": int(time.time())})
    for c in (worst, best):
        CD.ensure(c)
    check("modifier is bounded at the extremes",
          abs(CD.rating_modifier(worst)) <= CD.MAX_RATING_SWING
          and abs(CD.rating_modifier(best)) <= CD.MAX_RATING_SWING,
          f"worst={CD.rating_modifier(worst)} best={CD.rating_modifier(best)}")

    # fitness drains with work and recovers with rest
    tired = dict(legacy)
    CD.ensure(tired)
    tired["fitness"] = {"value": 100, "injury": None, "updated": int(time.time())}
    CD.record_workload(tired, balls_faced=30, balls_bowled=24)
    drained = CD.fitness(tired)
    check("workload drains fitness", drained < 100, f"fitness={drained}")
    CD.rest(tired)
    check("rest restores fitness", CD.fitness(tired) > drained)
    check("rest clears the matches-since-rest counter",
          tired["workload"]["matches_since_rest"] == 0)

    # stamina buys resilience
    weak = dict(legacy); CD.ensure(weak)
    weak["attributes"] = dict(CM.BASE_ATTRS, stamina=10)
    weak["fitness"] = {"value": 100, "injury": None, "updated": int(time.time())}
    strong = dict(legacy); CD.ensure(strong)
    strong["attributes"] = dict(CM.BASE_ATTRS, stamina=99)
    strong["fitness"] = {"value": 100, "injury": None, "updated": int(time.time())}
    CD.record_workload(weak, balls_faced=30, balls_bowled=24)
    CD.record_workload(strong, balls_faced=30, balls_bowled=24)
    check("high stamina drains less", CD.fitness(strong) > CD.fitness(weak),
          f"strong={CD.fitness(strong)} weak={CD.fitness(weak)}")

    # injuries do eventually happen when a player is run into the ground
    random.seed(11)
    injured = None
    grind = dict(legacy); CD.ensure(grind)
    grind["fitness"] = {"value": 100, "injury": None, "updated": int(time.time())}
    for _ in range(40):
        inj = CD.record_workload(grind, balls_faced=24, balls_bowled=24)
        if inj:
            injured = inj
            break
    check("heavy unrested workload eventually causes an injury", injured is not None)
    if injured:
        check("an injury has a type and a length",
              injured.get("type") and injured.get("matches_left", 0) > 0, str(injured))
        check("an injured player is flagged", CD.is_injured(grind) is True)
        check("injury forces a rating penalty", CD.rating_modifier(grind) <= -1.0)
        check("injury label reads for the UI", bool(CD.injury_label(grind)))
        for _ in range(6):
            CD.rest(grind)
        check("rest eventually clears an injury", CD.is_injured(grind) is False)

    # a fresh player is not injured out of nowhere
    random.seed(3)
    fresh_p = dict(legacy); CD.ensure(fresh_p)
    fresh_p["fitness"] = {"value": 100, "injury": None, "updated": int(time.time())}
    hits = sum(1 for _ in range(20)
               if CD.record_workload(dict(fresh_p, fitness=dict(fresh_p["fitness"]),
                                          workload={"balls_bowled": 0, "balls_faced": 0,
                                                    "matches_since_rest": 0}),
                                     balls_faced=12, balls_bowled=6))
    check("a fresh, rested player is never injured", hits == 0, f"{hits} injuries")

    # the engine conversion applies the modifier, stays in range, and never raises
    eng_hot = CM.career_to_engine(hot)
    eng_cold = CM.career_to_engine(cold)
    check("condition reaches the engine ratings", eng_hot["bat"] > eng_cold["bat"],
          f"{eng_hot['bat']} vs {eng_cold['bat']}")
    base_bat = CM.bat_skill(hot["attributes"])
    check("engine rating stays within the bound of the base rating",
          abs(eng_hot["bat"] - base_bat) <= CD.MAX_RATING_SWING + 1)
    check("engine ratings stay in 0-99",
          0 <= eng_hot["bat"] <= 99 and 0 <= eng_cold["bowl"] <= 99)

    # Condition must move only what the ENGINE sees. The stored rating, OVR and
    # tier are the player's earned progress and must not drift with form.
    ovr_probe = CM.new_career("902", "Probe", "pace", "standard")
    CD.ensure(ovr_probe)
    before = (ovr_probe["ovr"], ovr_probe["tier"], dict(ovr_probe["attributes"]))
    ovr_probe["form"] = {"rating": 100, "recent": []}
    ovr_probe["fitness"] = {"value": 20, "injury": None, "updated": int(time.time())}
    CM.career_to_engine(ovr_probe)
    CM.refresh_ovr(ovr_probe)
    after = (ovr_probe["ovr"], ovr_probe["tier"], dict(ovr_probe["attributes"]))
    check("condition never changes stored OVR, tier or attributes",
          before == after, f"{before} -> {after}")

    plain = {"_id": "901", "username": "NoCondition", "bowling_type": "offspin",
             "mindset": "anchor", "attributes": dict(CM.BASE_ATTRS)}
    e = CM.career_to_engine(plain)
    check("a career with no condition fields still converts", e["bat"] > 0 and e["bowl"] > 0)


def main():
    random.seed(42)
    part1()
    part2()
    part3()
    asyncio.run(part4())
    asyncio.run(part6_broadcast())
    asyncio.run(part7_access_and_admin())
    part8_engine_isolation()
    part9_drs()
    asyncio.run(part9b_drs_live())
    part10_condition()
    if os.environ.get("STRESS", "1") == "1":
        asyncio.run(part5_stress(int(os.environ.get("STRESS_N", "8"))))

    print("\n" + "=" * 64)
    print(f"{PASS} checks passed   ·    {len(FAIL)} failed")
    for f in FAIL:
        print(f"   FAIL: {f}")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
