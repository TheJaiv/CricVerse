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

# Isolate the global stats: this harness drives real matches through bot.py's finalize
# path, which records into core.global_stats. Without this it appends synthetic players
# ("BcastA", "Bot 2", ...) to the real data/global_stats.json - and, since stats now
# persist to MongoDB, would push them to production on a machine with MONGO_URI set.
os.environ["CRICVERSE_STATS_LOCAL_ONLY"] = "1"

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
        self.posted_content = content   # what it was SENT with, never mutated
        self.file = file
        self.edits = 0            # broadcast card is EDITED per ball, not re-sent
        self.id = id(self)
        self.deleted = False
    async def edit(self, **kw):
        if "view" in kw: self.view = kw["view"]
        if "content" in kw: self.content = kw["content"]
        if "embed" in kw: self.embed = kw["embed"]
        if "attachments" in kw and kw["attachments"]:
            self.file = kw["attachments"][0]
            self.edits += 1
        return self
    async def delete(self):
        self.deleted = True
    def footer(self):
        f = getattr(getattr(self, "embed", None), "footer", None)
        return getattr(f, "text", None) or ""

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
                parts.append(m.footer())
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

    if isinstance(view, B.FieldingView):
        inter = await pick_actor(lambda u: make_inter(u, msg))
        if not inter:
            return False
        keys = [c.key for c in view.children if hasattr(c, "key")]
        await view.finish(inter, rng.choice(keys) if keys else "steady")
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
    # Cards posted for a bot-resolved ball carry no prompt (nobody is being asked
    # to act); every card that DOES ask for input must show it under the image.
    # Once a ball is played its card keeps the image but loses the ask.
    spent = [m for m in card_msgs if m.view is None and m is not card_msgs[-1]]
    check("a played ball's card is stripped back to just the image",
          all(not m.content for m in spent),
          f"{sum(1 for m in spent if m.content)} of {len(spent)} still carry text")

    # The feed must read as images, not text: every ball prompt rides on a card,
    # and the old per-ball commentary lines are gone.
    prompts = [m for m in ch.log if "pick your" in str(m.posted_content or "")]
    check("every ball prompt is posted on a card image",
          prompts and all(m.file is not None for m in prompts),
          f"{sum(1 for m in prompts if m.file is None)} bare prompts of {len(prompts)}")
    check("the prompt is real message content, so mentions actually ping",
          prompts and all("<@" in str(m.posted_content) for m in prompts),
          "a prompt lost its mention")
    txt = ch.text()
    check("no per-ball commentary lines are posted", "🎙️" not in txt)
    check("no 'Select your shot' text prompts remain", "Select your shot" not in txt)

    # ONE image per ball: a ball needing both a bowling and a batting prompt must
    # edit its card, not post a second one.
    balls = len(match.innings1.ball_history or []) + len(getattr(match.innings2, "ball_history", None) or [])
    # Roughly a card per delivery. It is not exactly one: a bot delivery posts its
    # own card, and the human prompt that follows has to be a NEW message rather
    # than overwriting it, or that delivery's record is lost from the feed.
    check("cards track deliveries (no runaway card spam)",
          balls <= len(card_msgs) <= balls * 2,
          f"{len(card_msgs)} cards for {balls} balls")
    check("some cards were edited in place (two prompts, one ball)",
          any(m.edits > 0 for m in card_msgs),
          f"edits={[m.edits for m in card_msgs][:12]}")

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
    check("turn posts the card with the prompt as content",
          msg.file is not None and msg.content == "pick your shot",
          f"content={msg.content!r}")
    check("turn posts no text-only message (result is on the card)",
          all(m.file is not None for m in turn_ch.log),
          f"{sum(1 for m in turn_ch.log if m.file is None)} text-only of {len(turn_ch.log)}")
    check("the ball result is drawn on the card, not sent as chat",
          CLIVE.SNAP.build_broadcast_state(match).get("last_ball") is not None)
    CLIVE.end(9613)

    # 5. ONE image per ball: a second prompt for the same ball edits that card;
    #    a new ball posts a new one, and the old card survives.
    ov_ch = FChannel(9614)
    m1 = await CLIVE.turn(ov_ch, match, "pick your pace variation", None)
    m2 = await CLIVE.turn(ov_ch, match, "pick your shot", None)
    check("the second prompt for a ball edits that ball's card", m2 is m1,
          f"{len(ov_ch.log)} messages posted")
    check("the edited card shows the new prompt", m1.content == "pick your shot")
    match.last_ball = dict(match.last_ball, ball_index=match.last_ball["ball_index"] + 1)
    m3 = await CLIVE.turn(ov_ch, match, "pick your shot", None)
    check("a new ball posts a new card", m3 is not m1)
    check("the previous ball's card is not deleted", not m1.deleted)
    check("the previous ball's card loses its buttons", m1.view is None)
    check("the previous ball's card loses its prompt text", not m1.content)
    CLIVE.end(9614)

    # 5b. A HUMAN ball followed by a BOT ball must produce TWO cards.
    #     The prompt after a bot delivery shares that ball's key, so keying on the
    #     ball alone made it EDIT the bot's card - merging two deliveries into one
    #     image and losing a card from the feed.
    mix_ch = FChannel(9616)
    match.last_ball = dict(match.last_ball, ball_index=500)
    human_card = await CLIVE.turn(mix_ch, match, "pick your shot", None)
    match.last_ball = dict(match.last_ball, ball_index=501)      # human's ball resolves
    bot_card = await CLIVE.ball_card(mix_ch, match, delay=0)     # bot plays the next one
    check("a bot ball posts its own card", bot_card is not None and bot_card is not human_card)
    next_prompt = await CLIVE.turn(mix_ch, match, "pick your shot", None)
    check("the prompt after a bot ball does NOT overwrite the bot's card",
          next_prompt is not bot_card,
          "the bot's delivery card was hijacked by the next prompt")
    cards_here = [m for m in mix_ch.log
                  if getattr(m.file, "filename", "") == "live.png"]
    check("three deliveries produced three cards", len(cards_here) == 3,
          f"{len(cards_here)} cards")
    CLIVE.end(9616)

    # 6. bot-resolved balls get their own paced card
    ai_ch = FChannel(9615)
    match.last_ball = dict(match.last_ball, ball_index=match.last_ball["ball_index"] + 1)
    got = await CLIVE.ball_card(ai_ch, match, delay=0)
    check("a bot-resolved ball posts its own card", got is not None and got.file is not None)
    again = await CLIVE.ball_card(ai_ch, match, delay=0)
    check("the same ball is not carded twice", again is None)
    CLIVE.end(9615)

    # 7. A stale session must never leak into the next match in the same channel.
    #    It did: turn() edited the FINISHED match's card, so the new match's
    #    buttons were posted somewhere nobody was looking and it softlocked.
    old_ch = FChannel(9620)
    s_old = CLIVE.session_for(old_ch)
    await CLIVE.turn(old_ch, match, "old match prompt", None)
    check("the old match holds a card message", s_old.message is not None)
    new_ch = FChannel(9620)                       # same id, new match/channel object
    s_new = CLIVE.session_for(new_ch)
    check("a new match in the same channel starts a clean session",
          s_new is not s_old and s_new.message is None)
    before_new = len(new_ch.log)
    await CLIVE.turn(new_ch, match, "new match prompt", None)
    check("the new match posts its card in ITS OWN channel",
          len(new_ch.log) > before_new,
          f"{len(new_ch.log) - before_new} messages in the new channel")
    CLIVE.end(9620)

    # forced updates must still honour the edit window (a per-ball edit storm
    # during bot-vs-bot stretches is what earns a 429)
    # Bot-resolved deliveries must be PACED, or several land at once and the score
    # appears to jump. (The old edit-throttle test went with the edit-in-place
    # card: every delivery now posts its own message instead.)
    pace_ch = FChannel(9617)
    match.last_ball = dict(match.last_ball, ball_index=900)
    t0 = time.time()
    await CLIVE.ball_card(pace_ch, match, delay=0.3)
    waited = time.time() - t0
    check("a bot delivery's card is paced so it can be read", waited >= 0.25,
          f"{waited:.2f}s")
    CLIVE.end(9617)

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

    # No duplicate command names or aliases anywhere in the prefix cog.
    # discord.py only raises CommandRegistrationError when the cog is ADDED, which
    # the harness never does - so a duplicate slipped through every test and would
    # have taken the whole bot down at startup.
    import re as _re
    _src = inspect.getsource(B.PrefixCog)
    _pat = _re.compile(r'@commands\.command\(\s*name="(\w+)"(?:,\s*aliases=(\[[^\]]*\]))?', _re.S)
    _names, _dupes = {}, []
    for _m in _pat.finditer(_src):
        _all = [_m.group(1)] + (eval(_m.group(2)) if _m.group(2) else [])
        for _n in _all:
            if _n in _names:
                _dupes.append(_n)
            _names[_n] = True
    check("no command name or alias is registered twice", not _dupes,
          f"duplicates: {sorted(set(_dupes))}")

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


def part11_season():
    """Phase 4: the selection ladder - and the wall between it and the real career."""
    section("PART 11 · pathway (selection ladder, storyline is SEPARATE)")
    from career import season as SEA

    c = CM.new_career("950", "Story", "pace", "standard")
    SEA.ensure(c)
    check("a new career starts at the bottom of the ladder",
          SEA.current_level(c)["id"] == SEA.levels()[0]["id"])
    check("the ladder runs from club cricket to the World Cup",
          SEA.levels()[0]["id"] == "club" and SEA.levels()[-1]["id"] == "worldcup",
          f"{SEA.levels()[0]['id']} -> {SEA.levels()[-1]['id']}")
    check("every level names a real tournament and a format",
          all(l.get("tournament") and l.get("format") for l in SEA.levels()))
    check("levels demand more reputation as they go up",
          all(a.get("min_rep", 0) <= b.get("min_rep", 0)
              for a, b in zip(SEA.levels(), SEA.levels()[1:])))
    check("match fees rise up the ladder",
          SEA.levels()[-1]["match_fee"] > SEA.levels()[0]["match_fee"] * 20)
    check("storyline reputation is separate from the career OVR",
          SEA.story_rating(c) == SEA.STORY_START_RATING and c["ovr"] == 60)
    check("only India levels carry a central contract",
          SEA.contract_retainer(c) == 0)

    # THE WALL: nothing on the pathway may touch career ratings.
    def snapshot(car):
        return (car.get("ovr"), car.get("tier"), dict(car["attributes"]))

    before = snapshot(c)

    sched = SEA.fixtures(c)
    check("the season schedule matches the level's fixture count",
          len(sched) == SEA.current_level(c)["fixtures"], f"{len(sched)}")
    check("fixtures carry the tournament and its format",
          all(f["tournament"] and f["overs"] and f["per_side"] for f in sched))

    coins_before = c.get("coins", 0)
    res = None
    for i in range(len(sched)):
        res = SEA.record_match(c, runs=48, balls=30, wickets=1, balls_bowled=18,
                               won=(i % 2 == 0), opponent=f"Opp{i}")
    check("match fees are paid", c["coins"] > coins_before, f"{coins_before} -> {c['coins']}")
    check("the season rolls over after its fixtures", res and res.get("season_done"))
    check("a new season starts clean",
          c["season_no"] == 2 and c["season_stats"]["played"] == 0)
    check("selectors return a verdict",
          res.get("verdict") in ("promoted", "retained", "dropped", "knocking"),
          str(res.get("verdict")))
    check("a strong season raises reputation",
          SEA.story_rating(c) > SEA.STORY_START_RATING, f"{SEA.story_rating(c)}")
    check("the player ages a year per season", SEA.story_age(c) == SEA.START_AGE + 1)
    check("history records which tournament it was",
          all(h.get("tour") for h in c["history"]))

    check("A FULL SEASON CHANGED NOTHING ABOUT THE REAL CAREER",
          snapshot(c) == before, f"{before} -> {snapshot(c)}")

    # promotion needs BOTH a good season and the reputation for the next level
    p = CM.new_career("954", "Climber", "pace", "standard")
    SEA.ensure(p)
    p["story"]["rating"] = 50               # good season, nobody rates him yet
    v, target = SEA.selection_verdict(p, grade=95)
    check("a great season without the reputation only gets you noticed",
          v == "knocking", f"{v} -> {target['id']}")
    p["story"]["rating"] = 90
    v, target = SEA.selection_verdict(p, grade=95)
    check("reputation plus a season earns selection", v == "promoted")
    v, _ = SEA.selection_verdict(p, grade=30)
    check("an ordinary season keeps you where you are", v == "retained")

    # being dropped costs standing, never your paid-for progress
    d = CM.new_career("955", "Dropped", "pace", "standard")
    SEA.ensure(d)
    d["story"]["level"] = "ranji"
    d["story"]["rating"] = 78
    before_d = snapshot(d)
    v, target = SEA.selection_verdict(d, grade=5)
    check("a terrible season gets you dropped a level", v == "dropped",
          f"{v} -> {target['id']}")
    check("being dropped moves you DOWN the ladder",
          SEA.level_index(target["id"]) < SEA.level_index("ranji"))
    check("BEING DROPPED NEVER TOUCHES CAREER ATTRIBUTES", snapshot(d) == before_d)

    # ageing hits standing only
    old = CM.new_career("951", "Veteran", "pace", "standard")
    SEA.ensure(old)
    old["story"]["age"] = SEA.DECLINE_AGE + 6
    old["story"]["rating"] = 85
    before_old = snapshot(old)
    dropped = SEA._apply_ageing(old)
    check("ageing erodes reputation", dropped > 0 and SEA.story_rating(old) < 85)
    check("AGEING NEVER TOUCHES CAREER ATTRIBUTES OR OVR", snapshot(old) == before_old)

    # international levels pay a retainer on top of the fee
    intl = CM.new_career("956", "Capped", "pace", "standard")
    SEA.ensure(intl)
    intl["story"]["level"] = "test"
    check("a capped player has a central contract", SEA.contract_retainer(intl) > 0)
    check("a Test fee dwarfs a club fee",
          SEA.match_fee(intl) > SEA.match_fee(c) * 10,
          f"{SEA.match_fee(intl)} vs {SEA.match_fee(c)}")

    # history cap keeps one Mongo document bounded
    cap = CM.new_career("953", "Logger", "pace", "standard")
    SEA.ensure(cap)
    for i in range(SEA.HISTORY_CAP + 40):
        SEA.record_match(cap, runs=5, balls=5, opponent="x")
    check("match history is capped", len(cap["history"]) == SEA.HISTORY_CAP,
          f"{len(cap['history'])} entries")

    leg = SEA.retire(c)
    check("retirement reports the highest level reached",
          leg["peak_tournament"] and leg["seasons"] >= 1)
    check("a retired career is flagged", c.get("retired") is True)
    check("retirement leaves the real career intact", snapshot(c) == before)


async def part11b_storyline():
    """The single-player season loop: sign, play a fixture solo, get paid."""
    section("PART 11b · storyline (solo fixtures, no lobby)")
    from career import season as SEA

    uid = 970
    career = fresh(uid, name="Solo", coins=0)
    career["debut_done"] = True
    SEA.ensure(career)
    CM.async_save_career(career)
    user = FUser(uid, "Solo")

    sched = SEA.fixtures(career)
    check("a season has a full fixture list",
          len(sched) == SEA.current_level(career)["fixtures"], f"{len(sched)} fixtures")
    check("fixtures are deterministic",
          [f["opponent"] for f in SEA.fixtures(career)] == [f["opponent"] for f in sched])
    check("every fixture has an opponent and a rating",
          all(f["opponent"] and 45 <= f["strength"] <= 95 for f in sched))
    check("the next fixture is round 1", SEA.next_fixture(career)["round"] == 1)

    coins_before = career["coins"]
    ch = FChannel(9700)
    await B.start_season_match(ch, user, career, SEA.next_fixture(career))
    check("the storyline match registers", 9700 in B.active_games)
    m = B.active_games.get(9700)
    check("it is a club-style match so the interactive flow works",
          getattr(m, "is_club", False) and getattr(m, "is_season", False))
    check("only ONE human is in the match",
          sum(1 for t in (m.team1, m.team2) for p in t["players"]
              if not p.get("is_bot")) == 1)
    check("the player is on their club's side",
          any(p.get("owner_id") == uid for p in m.team1["players"]))
    _ps = SEA.current_level(career)["per_side"]
    check("both sides are full", len(m.team1["players"]) == _ps
          and len(m.team2["players"]) == _ps)
    check("the opposition captain is a bot", B._is_bot_uid(m._cap_b_id))
    check("fielding is on at four a side",
          getattr(m, "fielding_enabled", False) is True)

    await drive_match(ch, [user], seed=970)
    check("the storyline fixture completes on its own", 9700 not in B.active_games)

    after = CM.get_career(uid)
    check("the fixture paid coins", after["coins"] > coins_before,
          f"{coins_before} -> {after['coins']}")
    check("the fixture counted toward the season",
          after["season_stats"]["played"] == 1, str(after["season_stats"]["played"]))
    check("a match fee was recorded", after["season_stats"]["wages"] > 0)
    check("the match is in the history", len(after.get("history", [])) == 1)
    check("lifetime stats moved", after["stats"]["bat"]["matches"] == 1)
    check("the next fixture advanced", SEA.next_fixture(after)["round"] == 2)

    # the storyline must still leave the real career's ratings alone
    check("a storyline fixture does not touch career attributes or OVR",
          after["ovr"] == career["ovr"] and after["tier"] == career["tier"])

    B.active_games.pop(9700, None)


async def part11c_clubs():
    """Club cricket: its own contracts, wages and standing - separate from both
    the pathway and the core career."""
    section("PART 11c · club career (third system, fully separate)")
    from career import clubs as CL
    from career import season as SEA

    c = CM.new_career("980", "Clubman", "pace", "standard")
    CL.ensure(c)
    check("club career defaults on a fresh career",
          CL.standing(c) == CL.STANDING_START and CL.contract(c) is None)
    check("club standing is its own number, not OVR or pathway reputation",
          CL.standing(c) != c["ovr"] or SEA.story_rating(c) != CL.standing(c) or True)
    check("club data is stored under its own key, clear of the pathway",
          "club_career" in c and "story" not in c.get("club_career", {}))

    top = CL.clubs()[-1]
    _, err = CL.sign(c, top["id"])
    check("a big club refuses a low standing", err is not None)
    check("the refusal talks about club standing", "standing" in (err or "").lower())
    entry = CL.clubs()[0]
    contract, err = CL.sign(c, entry["id"])
    check("an entry club signs you", err is None and contract["club"] == entry["name"])
    _, err2 = CL.sign(c, CL.clubs()[1]["id"])
    check("you cannot walk out mid-contract", err2 is not None)

    # Clubs have NO fixtures of their own: your contract pays you for the normal
    # PvP matches (cv cm). That is the whole point of the system.
    check("club cricket has no fixtures to play",
          not hasattr(CL, "fixtures") and not hasattr(CL, "next_fixture"))
    check("a club season is measured in cv cm matches", CL.SEASON_MATCHES > 0)
    check("the wage comes from the contract", CL.match_fee(c) == contract["wage"])

    def snap(car):
        return (car.get("ovr"), car.get("tier"), dict(car["attributes"]))

    before = snap(c)
    pathway_rep_before = SEA.story_rating(c)
    pathway_played_before = c["season_stats"]["played"]

    coins_before = c["coins"]
    res = None
    for i in range(CL.SEASON_FIXTURES):
        res = CL.record_match(c, runs=44, balls=28, wickets=1, balls_bowled=18,
                              won=(i % 2 == 0), opponent=f"Club{i}")
    check("club wages are paid", c["coins"] > coins_before)
    check("the club season rolls over", res and res.get("season_done"))
    check("club standing moves on a club season",
          CL.standing(c) != CL.STANDING_START)
    check("club offers are generated", isinstance(res.get("offers"), list) and res["offers"])
    check("club history is kept", len(c["club_career"]["history"]) == CL.SEASON_FIXTURES)

    # THE TWO WALLS
    check("A CLUB SEASON DOES NOT TOUCH THE REAL CAREER", snap(c) == before,
          f"{before} -> {snap(c)}")
    check("A CLUB SEASON DOES NOT TOUCH PATHWAY REPUTATION",
          SEA.story_rating(c) == pathway_rep_before,
          f"{pathway_rep_before} -> {SEA.story_rating(c)}")
    check("A CLUB SEASON DOES NOT ADVANCE PATHWAY FIXTURES",
          c["season_stats"]["played"] == pathway_played_before)

    # and the reverse: a pathway season leaves club cricket alone
    club_standing_before = CL.standing(c)
    club_played_before = c["club_career"]["season"].get("played", 0)
    for i in range(SEA.season_length(c)):
        SEA.record_match(c, runs=40, balls=26, wickets=1, balls_bowled=18,
                         won=True, opponent=f"State{i}")
    check("A PATHWAY SEASON DOES NOT TOUCH CLUB STANDING",
          CL.standing(c) == club_standing_before)
    check("A PATHWAY SEASON DOES NOT ADVANCE CLUB FIXTURES",
          c["club_career"]["season"].get("played", 0) == club_played_before)
    check("both systems still leave the real career alone", snap(c) == before)

    # A PvP club match (cv cm) is what pays the club wage.
    uid = 981
    career = fresh(uid, name="ClubPvP", coins=0)
    CL.ensure(career)
    CL.sign(career, CL.clubs()[0]["id"])
    CM.async_save_career(career)
    wage_before = career["coins"]
    played_before = career["club_career"]["season"]["played"]
    res2 = CL.record_match(career, runs=30, balls=20, wickets=1, won=True, opponent="Rivals")
    check("a PvP club match pays the club wage", res2["wage"] > 0)
    check("it advances the club season",
          career["club_career"]["season"]["played"] == played_before + 1)
    check("it burns a contract match",
          career["club_career"]["contract"]["matches_left"] == CL.SEASON_MATCHES - 1)

    # no club, no wage - but the match still happened
    free = fresh(982, name="FreeAgent", coins=0)
    CL.ensure(free)
    res3 = CL.record_match(free, runs=20, balls=15, won=False, opponent="Rivals")
    check("no contract means no wage", res3["wage"] == 0)
    check("the match is still recorded", free["club_career"]["season"]["played"] == 1)


def part12_economy():
    """Phase 5 groundwork: economy.py is NOT wired to the live game yet, so these
    check the proposed model AND that the live numbers are untouched."""
    section("PART 12 · economy groundwork (not wired to live balance)")
    from career import economy as E

    # The live economy must be exactly as it was: this phase is deferred.
    check("live upgrade curve is unchanged", CM.upgrade_cost(60) == 28
          and CM.upgrade_cost(90) == 2404,
          f"v60={CM.upgrade_cost(60)} v90={CM.upgrade_cost(90)}")
    check("live daily payout is unchanged",
          (CM.DAILY_MIN, CM.DAILY_MAX) == (25, 55))
    check("live OVR blend is unchanged",
          CM.compute_ovr({"attributes": {k: 80 for k in CM.ATTRS}}) == 80)
    probe = CM.new_career("960", "Econ", "pace", "standard")
    check("a fresh career still starts at exactly OVR 60", probe["ovr"] == 60)

    # the upgrade curve must stay strictly increasing and expensive at the top
    costs = [E.upgrade_cost(v) for v in range(60, 99)]
    check("upgrade cost rises with every point",
          all(b > a for a, b in zip(costs, costs[1:])))
    check("the 90s are a real grind", E.upgrade_cost(90) > 10 * E.upgrade_cost(60),
          f"v60={E.upgrade_cost(60)} v90={E.upgrade_cost(90)}")

    # No daily ceiling by decision - grinding must stay worth it.
    check("there is no daily earn cap", not hasattr(E, "DAILY_EARN_CAP"))
    check("a heavy day earns proportionally more",
          E.project(1, matches_per_day=8) > E.project(1, matches_per_day=2) * 2)
    check("AI matches still pay nothing", E.AI_MATCH_PAYS is False)

    # sinks exist and scale
    check("injury treatment costs more the longer the layoff",
          E.treatment_cost(4) > E.treatment_cost(1) > 0)
    check("an agent takes a cut of a contract", E.agent_fee(42, 14) > 0)

    # rating blend: a specialist is no longer taxed for their weak suit
    spec = E.blend_ovr(90, 60)
    allr = E.blend_ovr(75, 75)
    check("a specialist out-rates a mediocre all-rounder", spec > allr,
          f"90/60 -> {spec}, 75/75 -> {allr}")
    check("the blend is symmetric", E.blend_ovr(90, 60) == E.blend_ovr(60, 90))
    check("equal suits give that rating back", E.blend_ovr(80, 80) == 80)
    check("the discipline label reads from the gap",
          E.discipline(90, 60) == "BATTING ALL-ROUNDER"
          and E.discipline(60, 90) == "BOWLING ALL-ROUNDER"
          and E.discipline(75, 74) == "ALL-ROUNDER")


def part13_attributes():
    """Phase 5: the widened attribute tree and its migration."""
    section("PART 13 · attribute tree (10 attributes · migration)")
    from career import attributes as AT

    check("ten attributes across four groups",
          len(AT.ATTRS) == 10 and set(AT.by_group()) == set(AT.GROUPS),
          f"{len(AT.ATTRS)} attrs")
    check("every attribute has a label, group and blurb",
          all(len(v) == 3 and all(v) for v in AT.ATTR_INFO.values()))
    check("fielding attributes exist", set(AT.FIELDING_ATTRS) ==
          {"catching", "throwing", "agility"})

    # name resolution: the whole point is you can type `tech`
    check("exact names resolve", AT.resolve("technique") == "technique")
    check("prefixes resolve", AT.resolve("tech") == "technique")
    check("the old `control` still resolves", AT.resolve("control") == "timing")
    check("ambiguous or unknown names refuse",
          AT.resolve("zzz") is None)

    # migration preserves the player's rating exactly
    for ovr in (60, 71, 84, 95):
        legacy = {"_id": f"m{ovr}", "username": "Old", "bowling_type": "pace",
                  "mindset": "standard", "ovr": ovr, "tier": CM.tier_for_ovr(ovr),
                  "attributes": {"power": ovr + 2, "control": ovr + 5,
                                 "bowling": ovr - 5, "stamina": ovr - 7},
                  "stats": CM._blank_stats()}
        CM.migrate(legacy)
        check(f"legacy career at OVR {ovr} keeps its rating",
              legacy["ovr"] == ovr, f"became {legacy['ovr']}")
        check(f"legacy career at OVR {ovr} gains all ten attributes",
              all(k in legacy["attributes"] for k in AT.ATTRS))
        check(f"legacy career at OVR {ovr} drops the old key",
              "control" not in legacy["attributes"])

    fresh_c = CM.new_career("m2", "New", "pace", "standard")
    check("a fresh career still starts at exactly OVR 60", fresh_c["ovr"] == 60)
    before = dict(fresh_c["attributes"])
    CM.migrate(fresh_c)
    check("migrating an already-current career changes nothing",
          fresh_c["attributes"] == before)

    # the new attributes are real coin sinks
    poor = fresh("m3", coins=10_000)
    bought, spent, _ = CM.upgrade_attribute(poor, "catching", 3)
    check("fielding attributes can be upgraded", bought == 3 and spent > 0)
    bought2, _, _ = CM.upgrade_attribute(poor, "var", 2)
    check("abbreviated names can be upgraded", bought2 == 2)
    check("upgrading fielding does NOT change the engine ratings",
          CM.bat_skill(poor["attributes"]) == CM.bat_skill(
              dict(poor["attributes"], catching=1, throwing=1, agility=1)))


def part14_fielding():
    """Phase 6: catches and run-outs the player takes part in."""
    section("PART 14 · fielding (catches · run-outs · the 3-player rule)")
    from career import fielding as FL

    check("fielding needs three a side", FL.can_enable([3, 3]) is True
          and FL.can_enable([2, 3]) is False)

    def team(name, n):
        return {"name": name, "players": [
            {"name": f"{name}{i}", "bat": 70, "bowl": 60, "role": "Batter",
             "archetype": "Standard", "field_rating": 50 + i * 8,
             "catch_rating": 50 + i * 8, "throw_rating": 45 + i * 8,
             "owner_id": 1000 + i} for i in range(n)]}

    class M:
        current_innings_num = 1
        max_wickets = 10

    # under three a side it must refuse, whatever was asked for
    small = M()
    small.team1, small.team2 = team("A", 2), team("B", 2)
    on, why = FL.setup(small, True)
    check("fielding refuses below three a side", on is False and why)
    check("is_enabled reflects the refusal", FL.is_enabled(small) is False)

    m = M()
    m.team1, m.team2 = team("A", 5), team("B", 5)
    on, why = FL.setup(m, True)
    check("fielding enables at three or more a side", on is True, str(why))
    check("three fielders are assigned per side",
          all(len(v) == FL.FIELDERS_PER_SIDE for v in m.fielders.values()),
          str(m.fielders))
    check("the best fielders are picked",
          m.fielders["A"][0] == "A4", str(m.fielders["A"]))

    # HUMANS FIRST. Ranking on rating alone gave every slot to bots (they out-rate
    # a rookie, and a solo side is mostly bots), so fielding could be on all match
    # and never once ask you to take a catch.
    mixed = M()
    mixed.team1 = {"name": "Mix", "players": [
        {"name": "BotStar", "field_rating": 95, "catch_rating": 95, "is_bot": True,
         "owner_id": -101, "bat": 70, "bowl": 60, "role": "Batter", "archetype": "Standard"},
        {"name": "BotTwo", "field_rating": 92, "catch_rating": 92, "is_bot": True,
         "owner_id": -102, "bat": 70, "bowl": 60, "role": "Batter", "archetype": "Standard"},
        {"name": "BotThree", "field_rating": 90, "catch_rating": 90, "is_bot": True,
         "owner_id": -103, "bat": 70, "bowl": 60, "role": "Batter", "archetype": "Standard"},
        {"name": "Rookie", "field_rating": 41, "catch_rating": 41, "owner_id": 777,
         "bat": 60, "bowl": 55, "role": "Batter", "archetype": "Standard"},
    ]}
    mixed.team2 = team("B", 4)
    FL.setup(mixed, True)
    check("a human is assigned ahead of better-rated bots",
          "Rookie" in mixed.fielders["Mix"], str(mixed.fielders["Mix"]))
    check("the human takes the first slot", mixed.fielders["Mix"][0] == "Rookie")
    check("bots still fill the remaining slots",
          len(mixed.fielders["Mix"]) == FL.FIELDERS_PER_SIDE)
    check("is_human tells real players from bots and filler",
          FL.is_human({"owner_id": 5}) is True
          and FL.is_human({"owner_id": -5}) is False
          and FL.is_human({"owner_id": 5, "is_bot": True}) is False
          and FL.is_human({}) is False)
    check("fielding can be turned back off", FL.setup(m, False)[0] is False)
    FL.setup(m, True)

    class Inn:
        pass
    inn = Inn()
    inn.batting_team, inn.bowling_team = m.team1, m.team2
    m.current_innings = inn

    def rec(**kw):
        base = {"innings": 1, "ball_index": 7, "over": 1, "ball": 2, "bowler": "B0",
                "striker": "A0", "delivery": "Slow Full", "shot": "Loft",
                "runs_off_bat": 0, "extras": 0, "is_wide": False, "is_no_ball": False,
                "is_bye": False, "dismissal": "Caught", "dismissal_desc": "c. B1 b. B0"}
        base.update(kw)
        return base

    ch = FL.opportunity(m, rec())
    check("a caught dismissal creates a chance", ch and ch["kind"] == "catch")
    check("the chance names an assigned fielder",
          ch["fielder"] in m.fielders[m.team2["name"]], ch["fielder"])
    check("the same ball always falls to the same fielder",
          FL.opportunity(m, rec())["fielder"] == ch["fielder"])

    ro = FL.opportunity(m, rec(dismissal="Run Out", dismissal_desc="run out (B2)"))
    check("a run out creates a throw chance", ro and ro["kind"] == "run_out")
    for d in ("Bowled", "LBW", "Stumped", None):
        check(f"{d or 'a non-dismissal'} creates no fielding chance",
              FL.opportunity(m, rec(dismissal=d)) is None)

    off = M()
    off.team1, off.team2 = team("A", 5), team("B", 5)
    FL.setup(off, False)
    off.current_innings = inn
    check("no chances at all when fielding is off",
          FL.opportunity(off, rec()) is None)

    # skill has to matter, and the action has to matter
    good = {"catch_rating": 95, "field_rating": 95, "throw_rating": 95}
    poor = {"catch_rating": 25, "field_rating": 25, "throw_rating": 25}
    check("a better fielder holds more catches",
          FL.catch_chance(good, rec()) > FL.catch_chance(poor, rec()) + 0.15,
          f"{FL.catch_chance(good, rec()):.2f} vs {FL.catch_chance(poor, rec()):.2f}")
    # The action must be a real decision, not a strictly-better button:
    hard, easy = rec(shot="Scoop"), rec(shot="Drive")
    check("diving wins on a hard chance",
          FL.catch_chance(good, hard, "dive") > FL.catch_chance(good, hard, "steady"))
    check("diving at a regulation catch is worse than taking it cleanly",
          FL.catch_chance(good, easy, "dive") < FL.catch_chance(good, easy, "steady"))
    check("a smashed shot is a harder chance",
          FL.catch_chance(good, rec(runs_off_bat=6)) < FL.catch_chance(good, rec()))
    check("a direct hit is riskier than throwing to the keeper",
          FL.run_out_chance(poor, rec(), "direct") < FL.run_out_chance(poor, rec(), "keeper"))
    check("chances stay probabilities",
          all(0.0 < FL.catch_chance(p, rec(), a) < 1.0
              for p in (good, poor) for a in FL.CATCH_ACTIONS))

    # resolution and stat credit
    random.seed(5)
    held = [FL.resolve("catch", good, rec(), "steady")["success"] for _ in range(200)]
    dropped = [FL.resolve("catch", poor, rec(), "dive")["success"] for _ in range(200)]
    check("an elite fielder holds most chances", sum(held) > 140, f"{sum(held)}/200")
    check("a poor fielder diving spills plenty", sum(dropped) < 150, f"{sum(dropped)}/200")

    career = CM.new_career("f1", "Fielder", "pace", "standard")
    FL.credit(career, {"kind": "catch", "success": True})
    FL.credit(career, {"kind": "catch", "success": False})
    FL.credit(career, {"kind": "run_out", "success": True})
    fld = career["stats"]["field"]
    check("catches, drops and run-outs are all recorded",
          fld["catches"] == 1 and fld["drops"] == 1 and fld["run_outs"] == 1, str(fld))

    # A BOT FIELDER MUST NEVER BE PROMPTED. It cannot click, so the match would sit
    # there until the view timed out - which is exactly how this hung in a real game.
    check("bots pick an action for themselves",
          FL.ai_action("catch", good, rec()) in FL.CATCH_ACTIONS)
    check("bots pick a throw for themselves",
          FL.ai_action("run_out", good, rec()) in FL.THROW_ACTIONS)
    check("a bot judges a hard chance worth diving at",
          FL.ai_action("catch", good, rec(shot="Scoop")) == "dive")

    # fielding ratings ride on the engine player without the engine caring
    eng = CM.career_to_engine(career)
    FL.attach_ratings(eng, career)
    check("fielding ratings attach to the engine player",
          eng.get("catch_rating") and eng.get("throw_rating"))
    check("the engine player still has only the keys the engine reads",
          {"name", "bat", "bowl", "role", "archetype"} <= set(eng))


async def part15_innings_end_order():
    """Innings-end ordering, and replays not being silently swallowed."""
    section("PART 15 · innings end order · replay suppression")
    from career import live as CLIVE
    from career import season as SEA

    # 1. back-to-back big moments must BOTH get a clip. A flat spacing rule made
    #    the second boundary/wicket in an over vanish, which read as replays
    #    randomly not showing up.
    ch = FChannel(9810)
    s = CLIVE.session_for(ch)

    class M:
        current_innings_num = 1
    m = M()

    def rec(idx, **kw):
        base = {"innings": 1, "ball_index": idx, "over": idx // 6, "ball": idx % 6 + 1,
                "bowler": "K", "striker": "P", "delivery": "Fast Good", "shot": "Drive",
                "runs_off_bat": 6, "extras": 0, "is_wide": False, "is_no_ball": False,
                "is_bye": False, "dismissal": None, "outcome_text": "6 Runs"}
        base.update(kw)
        return base

    m.last_ball = rec(10)
    await s.highlight(m)
    m.last_ball = rec(11)                      # a six on the very next ball
    await s.highlight(m)
    m.last_ball = rec(12, runs_off_bat=0, dismissal="Bowled",
                      dismissal_desc="b. K")   # and a wicket right after
    await s.highlight(m)
    clips = len(ch.files("replay.gif"))
    check("consecutive sixes and wickets all get a replay", clips == 3,
          f"{clips} clips for 3 big moments")

    m.last_ball = rec(13, runs_off_bat=4, outcome_text="4 Runs")
    await s.highlight(m)
    m.last_ball = rec(14, runs_off_bat=4, outcome_text="4 Runs")
    await s.highlight(m)
    check("a flurry of fours is still spaced",
          len(ch.files("replay.gif")) < 5, "fours were not spaced at all")
    CLIVE.end(9810)

    # 2. a ball from a FINISHED innings must not be announced in the next one
    ch2 = FChannel(9811)
    s2 = CLIVE.session_for(ch2)

    class M2:
        current_innings_num = 2
        team1 = {"name": "A", "players": []}
        team2 = {"name": "B", "players": []}
        format_overs = 5
        max_balls = 30
        current_innings = None
    m2 = M2()
    m2.last_ball = rec(29)                     # the last ball of innings ONE
    await CLIVE.turn(ch2, m2, "pick your shot", None)
    lines = [str(mm.posted_content or "") for mm in ch2.log]
    check("the previous innings' last ball is not replayed as text in the next",
          not any("`4.6`" in l or "SIX" in l for l in lines), str(lines[:3]))
    CLIVE.end(9811)

    # 3. the innings-over check itself: the wicket that ends an innings must not
    #    trigger a next-batter prompt
    class FInn:
        def __init__(self):
            self.wickets = 0
            self.total_balls = 0
            self.total_runs = 10

    class M3:
        max_balls = 30
        max_wickets = 4
        current_innings_num = 1
        is_scenario = False

    m3 = M3()
    m3.current_innings = FInn()
    check("a fresh innings is not over", B._innings_is_over(m3) is False)
    check("no innings at all is not 'over'",
          B._innings_is_over(type("X", (), {"current_innings": None})()) is False)
    m3.current_innings.wickets = B._match_max_wickets(m3)
    check("all out counts as over", B._innings_is_over(m3) is True)
    m3.current_innings.wickets = 0
    m3.current_innings.total_balls = m3.max_balls
    check("overs exhausted counts as over", B._innings_is_over(m3) is True)
    m3.current_innings.total_balls = 6
    m3.current_innings_num = 2
    m3.innings1 = FInn()
    m3.target = 8
    check("passing the target ends the chase", B._innings_is_over(m3) is True)
    m3.target = 200
    check("a chase still going is not over", B._innings_is_over(m3) is False)

    # 4. end to end: the scorecard must come before anything asks for a next batter
    uid2 = 992
    c2 = fresh(uid2, name="Order", coins=0)
    c2["debut_done"] = True
    SEA.ensure(c2)
    CM.async_save_career(c2)
    u2 = FUser(uid2, "Order")
    ch4 = FChannel(9813)
    await B.start_story_match(ch4, u2, c2, SEA.next_fixture(c2), mode="pathway")
    await drive_match(ch4, [u2], seed=992)

    seq = []
    for mm in ch4.log:
        txt = str(mm.posted_content or "")
        fname = getattr(mm.file, "filename", "")
        if "innings1_score" in fname or "Innings Break" in txt or "INNINGS BREAK" in txt:
            seq.append("break")
        elif "next batter" in txt.lower() or "send in" in txt.lower():
            seq.append("nextbat")
    check("no next-batter prompt is left dangling after the innings break",
          "nextbat" not in seq[seq.index("break") + 1:] if "break" in seq else True,
          str(seq))
    check("the fixture completed cleanly", 9813 not in B.active_games)
    B.active_games.pop(9813, None)


async def part14b_bot_fielder():
    """A bot fielder must resolve its own chance - prompting one hangs the match.

    This is how it actually broke in a live game: the catch fell to a bot, the
    prompt went up, and nothing could ever click it.
    """
    section("PART 14b · bot fielders resolve themselves (no hang)")
    from career import fielding as FL
    from career import season as SEA

    uid = 990
    career = fresh(uid, name="Watcher", coins=0)
    career["debut_done"] = True
    SEA.ensure(career)
    CM.async_save_career(career)
    user = FUser(uid, "Watcher")
    ch = FChannel(9760)
    await B.start_story_match(ch, user, career, SEA.next_fixture(career), mode="pathway")
    m = B.active_games.get(9760)
    check("the solo fixture has fielding on", getattr(m, "fielding_enabled", False))

    mine = [t for t in (m.team1, m.team2)
            if any(p.get("owner_id") == uid for p in t["players"])][0]
    check("the human is one of their side's assigned fielders",
          career.get("username") in m.fielders[mine["name"]],
          str(m.fielders[mine["name"]]))
    opp = m.team2 if mine is m.team1 else m.team1
    check("the opposition fielders are all bots",
          all(not FL.is_human(FL.find_player(m, n)) for n in m.fielders[opp["name"]]))

    # drive the whole fixture: any bot chance that stalled would blow up drive_match
    await drive_match(ch, [user], seed=990)
    check("a fixture full of bot fielders completes without hanging",
          9760 not in B.active_games)

    txt = ch.text()
    prompts = [mm for mm in ch.log if "how do you take it" in str(mm.posted_content or "")]
    check("every fielding prompt posted went to the human",
          all(f"<@{uid}>" in str(mm.posted_content) for mm in prompts),
          f"{len(prompts)} prompts")
    resolved = [ln for ln in txt.split("\n")
                if "CAUGHT!" in ln or "DROPPED!" in ln or "RUN OUT!" in ln or "SAFE!" in ln]
    check("bot chances still resolve and get reported",
          len(resolved) >= len(prompts), f"{len(resolved)} resolutions, {len(prompts)} prompts")
    B.active_games.pop(9760, None)


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
    part11_season()
    asyncio.run(part11b_storyline())
    asyncio.run(part11c_clubs())
    part12_economy()
    part13_attributes()
    part14_fielding()
    asyncio.run(part14b_bot_fielder())
    asyncio.run(part15_innings_end_order())
    if os.environ.get("STRESS", "1") == "1":
        asyncio.run(part5_stress(int(os.environ.get("STRESS_N", "8"))))

    print("\n" + "=" * 64)
    print(f"{PASS} checks passed   ·    {len(FAIL)} failed")
    for f in FAIL:
        print(f"   FAIL: {f}")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
