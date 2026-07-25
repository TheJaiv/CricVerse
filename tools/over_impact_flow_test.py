"""Over-step and Auto Impact Player flow test (headless, no Discord / no Mongo writes).
Run from the repo root: python tools/over_impact_flow_test.py

Drives the real match loops in bot.py with fake Discord channel / message / interaction
objects. Covers:
  - `cv over`: stopping a verbose or ball-by-ball sim at the next over boundary, the over
    card, the reset back to the hub, the innings-ending-over case, resuming afterwards.
  - Auto Impact Player: the plan/apply split, AI sides swapping silently, a managed side
    being offered the swap (confirm / skip / edit / no answer), one offer per innings, who
    is allowed to answer, and /endmatch landing while an offer is on screen.
"""
import asyncio
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bot as B

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"{'PASS' if cond else 'FAIL'} | {name}" + (f"  -> {detail}" if detail and not cond else ""))


# ---------------- fake discord layer ----------------

class FakeMessage:
    def __init__(self, channel, content, embed=None, view=None):
        self.channel, self.content, self.embed, self.view = channel, content, embed, view
        self.view0 = view      # the view as SENT (edit(view=None) clears self.view)
        self.deleted = False

    async def edit(self, **kw):
        if "view" in kw:
            self.view = kw["view"]
        if "embed" in kw:
            self.embed = kw["embed"]
        return self

    async def delete(self):
        self.deleted = True


class FakeChannel:
    """Records every send. `on_send` lets a test react to traffic mid-loop."""
    def __init__(self, cid=12345):
        self.id = cid
        self.sent = []
        self.on_send = None
        self.channel = self      # loops accept either an interaction or a raw channel

    async def send(self, content=None, embed=None, view=None, file=None, **kw):
        m = FakeMessage(self, content, embed, view)
        self.sent.append(m)
        if self.on_send:
            r = self.on_send(m)
            if asyncio.iscoroutine(r):
                await r
        return m

    def texts(self):
        return [m.content or "" for m in self.sent if m.content]

    def views(self):
        return [m.view for m in self.sent if m.view is not None]


class FakeUser:
    def __init__(self, uid):
        self.id = uid


class FakeResponse:
    def __init__(self):
        self.messages = []

    async def defer(self):
        pass

    async def send_message(self, content=None, view=None, ephemeral=False, **kw):
        self.messages.append((content, view))

    async def edit_message(self, **kw):
        self.messages.append(("edit", kw.get("view")))


class FakeInteraction:
    def __init__(self, channel, uid):
        self.channel = channel
        self.user = FakeUser(uid)
        self.response = FakeResponse()
        self.message = FakeMessage(channel, "btn")

    async def edit_original_response(self, **kw):
        pass


def build_match(overs=20, impact=True, p1_id=111, p2_id=None, bat_first=1,
                t1_shape="complete", t2_shape="complete"):
    """t*_shape: 'complete' = 5+ bowling options (Rule 1 fires, needs a bench batter),
    'short' = only 4 bowling options (Rule 2 fires, needs a bench bowler)."""
    def team(prefix, shape):
        players = [
            {"name": f"{prefix}_OP1", "bat": 82, "bowl": 30, "role": "Batter", "archetype": "Aggressor"},
            {"name": f"{prefix}_OP2", "bat": 80, "bowl": 30, "role": "Batter", "archetype": "Anchor"},
            {"name": f"{prefix}_T3", "bat": 84, "bowl": 30, "role": "Batter", "archetype": "Anchor"},
            {"name": f"{prefix}_T4", "bat": 79, "bowl": 30, "role": "Batter", "archetype": "Aggressor"},
            {"name": f"{prefix}_WK", "bat": 77, "bowl": 20, "role": "Batter_WK", "archetype": "Finisher"},
            {"name": f"{prefix}_AR", "bat": 72, "bowl": 78, "role": "All-Rounder_Pace", "archetype": "Finisher"},
            {"name": f"{prefix}_PB1", "bat": 38, "bowl": 84, "role": "Bowler_Pace", "archetype": "Standard"},
            {"name": f"{prefix}_PB2", "bat": 35, "bowl": 82, "role": "Bowler_Pace", "archetype": "Standard"},
            {"name": f"{prefix}_SP1", "bat": 33, "bowl": 83, "role": "Bowler_Spin_Leg", "archetype": "Standard"},
            {"name": f"{prefix}_SP2", "bat": 30, "bowl": 80, "role": "Bowler_Spin_Off", "archetype": "Standard"},
        ]
        if shape == "complete":
            # 5 bowling options (AR + 4 bowlers) -> Rule 1; bench holds a batter.
            players.append({"name": f"{prefix}_T6", "bat": 75, "bowl": 25, "role": "Batter", "archetype": "Finisher"})
            subs = [{"name": f"{prefix}_SUB_BAT", "bat": 81, "bowl": 22, "role": "Batter", "archetype": "Aggressor"}]
        else:
            # drop a bowler -> only 4 options; bench holds the missing bowler.
            players = [p for p in players if not p["name"].endswith("SP2")]
            players.append({"name": f"{prefix}_T6", "bat": 74, "bowl": 25, "role": "Batter", "archetype": "Finisher"})
            players.append({"name": f"{prefix}_T7", "bat": 70, "bowl": 24, "role": "Batter", "archetype": "Standard"})
            subs = [{"name": f"{prefix}_SUB_BOWL", "bat": 28, "bowl": 85, "role": "Bowler_Spin_Off", "archetype": "Standard"}]
        return {"name": prefix, "players": players, "color": "#333333", "subs": subs}

    t1, t2 = team("HOME", t1_shape), team("AWAY", t2_shape)
    m = B.CricketMatch("P1", "P2" if p2_id else None, p1_id, p2_id, t1, t2, overs, "Flat", "Clear")
    m.impact_player = impact
    bat, bowl = (t1, t2) if bat_first == 1 else (t2, t1)
    m.innings1 = B.InningsState(bat, bowl)
    m.current_innings = m.innings1
    m.current_innings_num = 1
    m.batting_first_id = p1_id if bat_first == 1 else p2_id
    m.bowling_first_id = p2_id if bat_first == 1 else p1_id
    return m


def register(match, channel):
    B.active_games[channel.id] = match
    return match


# ---------------- tests ----------------

def arm_rule1(inn):
    """Put the innings where Rule 1 fires: 7 down, so the next man in is a TAIL BOWLER
    (slot 7 once the order is sorted by rating - slot 6 is still the all-rounder)."""
    order = inn.batting_team["players"]
    order.sort(key=lambda p: -p["bat"])
    inn.next_batter_idx = 7
    for p in order[:7]:
        inn.batting_stats[p["name"]].dismissal = "c Fielder b Bowler"
    inn.wickets = 6




async def t_step_verbose():
    """`cv over` during a verbose innings sim stops at the over boundary and re-opens the hub."""
    ch = FakeChannel(1001)
    m = register(build_match(p1_id=111, p2_id=222, impact=False), ch)
    m.simulation_mode = "whole_match"
    m.verbose = True
    m.sim_only = True   # pretend a full-auto sim: the handoff must clear this

    cards = {"n": 0}

    def on_send(msg):
        if msg.embed is not None and msg.view is None:
            cards["n"] += 1
            if cards["n"] == 3:          # after the 3rd over card, ask to step out
                m._step_over = True
    ch.on_send = on_send

    await B.loop_current_innings_simulation(ch, m)
    inn = m.current_innings

    check("verbose step: stopped mid-innings", inn.total_balls < m.max_balls and inn.wickets < 10,
          f"balls={inn.total_balls} wkts={inn.wickets}")
    check("verbose step: stopped ON an over boundary", inn.total_balls % 6 == 0, f"balls={inn.total_balls}")
    check("verbose step: over count matches cards", inn.total_balls // 6 == 3, f"overs={inn.total_balls // 6}")
    check("verbose step: over_log cleared for the next over", inn.over_log == [])
    check("verbose step: dropped out of auto-sim", m.simulation_mode == "interactive" and m.verbose is False)
    check("verbose step: sim_only cleared", m.sim_only is False)
    check("verbose step: _auto_loop released", getattr(m, "_auto_loop", False) is False)
    check("verbose step: flag consumed", m._step_over is False)
    check("verbose step: pause message posted", any("paused before the next over" in t for t in ch.texts()))
    check("verbose step: a hub/bowler prompt with buttons was sent", len(ch.views()) >= 1,
          f"views={len(ch.views())}")
    del B.active_games[ch.id]


async def t_step_bbb():
    """`cv over` during a ball-by-ball broadcast does the same, without cutting the over."""
    ch = FakeChannel(1002)
    m = register(build_match(p1_id=111, p2_id=222, impact=False), ch)
    m.simulation_mode = "whole_match"
    m.verbose = False

    real_sleep = asyncio.sleep

    async def fast_sleep(d):
        await real_sleep(0)          # keep the ball pacing instant for the test
    B.asyncio.sleep = fast_sleep
    try:
        balls = {"n": 0}
        orig_exec = B.execute_ball_math

        def counting_exec(match):
            r = orig_exec(match)
            balls["n"] += 1
            if balls["n"] == 8:      # mid-way through the 2nd over
                match._step_over = True
            return r
        B.execute_ball_math = counting_exec
        try:
            await B.loop_current_innings_bbb(ch, m)
        finally:
            B.execute_ball_math = orig_exec
    finally:
        B.asyncio.sleep = real_sleep

    inn = m.current_innings
    check("bbb step: stopped ON an over boundary", inn.total_balls % 6 == 0, f"balls={inn.total_balls}")
    check("bbb step: finished the over it was in (2 overs, not 8 balls)", inn.total_balls == 12,
          f"balls={inn.total_balls}")
    check("bbb step: _bbb_active cleared", getattr(m, "_bbb_active", True) is False)
    check("bbb step: dropped out of auto-sim", m.simulation_mode == "interactive" and m.verbose is False)
    check("bbb step: _auto_loop released", getattr(m, "_auto_loop", False) is False)
    check("bbb step: hub prompt sent", len(ch.views()) >= 1)
    del B.active_games[ch.id]


async def t_ai_side_auto_swaps():
    """A side with no human manager keeps the old behaviour: the swap just happens."""
    ch = FakeChannel(1003)
    # AI bats first (team2 = AI, p2_id None). Rule 1 needs the batting side, so bat_first=2.
    m = register(build_match(p1_id=111, p2_id=None, bat_first=2), ch)
    m.simulation_mode = "whole_match"
    m.verbose = False
    inn = m.current_innings
    check("auto swap: setup - AI team bats", inn.batting_team["name"] == "AWAY")

    # Force the Rule 1 trigger: next man due is a tail bowler, with batters already out.
    arm_rule1(inn)

    plans = B.plan_ai_impact_swaps(m, inn)
    check("auto swap: a plan was produced", len(plans) >= 1, f"plans={plans}")
    plan = next((p for p in plans if p["team_num"] == 2), None)
    check("auto swap: plan targets the AI side", plan is not None)
    if plan:
        check("auto swap: plan names an incoming bench player", plan["in_player"]["name"] == "AWAY_SUB_BAT")
        check("auto swap: plan is the batting-boost kind", plan["kind"] in ("insert_batter", "replace_batter"))
        check("auto swap: nothing applied by planning alone", m.t2_impact_used is False)

    await B.run_over_break_impact(ch, m, inn)
    check("auto swap: applied without any prompt", m.t2_impact_used is True)
    check("auto swap: no button view was posted", len(ch.views()) == 0)
    check("auto swap: announced to the channel", any("IMPACT PLAYER" in t for t in ch.texts()))
    check("auto swap: sub is on the field", any(p["name"] == "AWAY_SUB_BAT" for p in m.team2["players"]))
    del B.active_games[ch.id]


async def _drive_prompt(match, ch, inn, click=None, uid=111):
    """Run the over-break hook and optionally click a button on the prompt it posts."""
    async def clicker():
        for _ in range(200):
            await asyncio.sleep(0.005)
            v = next((m.view0 for m in ch.sent if isinstance(m.view0, B.AutoImpactPromptView)), None)
            if v is not None:
                if click is None:
                    return
                inter = FakeInteraction(ch, uid)
                await getattr(v, click).callback(inter)
                return v
        return None

    task = asyncio.create_task(clicker())
    await B.run_over_break_impact(ch, match, inn)
    return await task


async def t_human_prompt_confirm():
    """A human-managed side is ASKED, and Confirm applies the planned swap."""
    ch = FakeChannel(1004)
    m = register(build_match(p1_id=111, p2_id=222, bat_first=1), ch)
    inn = m.current_innings
    arm_rule1(inn)

    await _drive_prompt(m, ch, inn, click="confirm_swap", uid=111)

    prompts = [x for x in ch.sent if isinstance(x.view0, B.AutoImpactPromptView)]
    check("prompt: an offer embed was posted", len(prompts) == 1, f"n={len(prompts)}")
    if prompts:
        e = prompts[0].embed
        fields = {f.name: f.value for f in e.fields}
        check("prompt: shows who goes OUT", any("GOING OUT" in k for k in fields))
        check("prompt: shows who comes IN", any("COMING IN" in k for k in fields))
        check("prompt: names the incoming sub", any("HOME_SUB_BAT" in v for v in fields.values()),
              f"{fields}")
        check("prompt: pings the manager", (prompts[0].content or "").strip() == "<@111>")
        check("prompt: offers 3 choices", len(prompts[0].view0.children) == 3)
    check("prompt confirm: swap applied", m.t1_impact_used is True)
    check("prompt confirm: sub is on the field", any(p["name"] == "HOME_SUB_BAT" for p in m.team1["players"]))
    check("prompt confirm: swap announced", any("IMPACT PLAYER" in t for t in ch.texts()))
    del B.active_games[ch.id]


async def t_human_prompt_skip():
    """Skip -> no swap, and no re-prompt for the rest of the innings."""
    ch = FakeChannel(1005)
    m = register(build_match(p1_id=111, p2_id=222, bat_first=1), ch)
    inn = m.current_innings
    arm_rule1(inn)

    await _drive_prompt(m, ch, inn, click="skip_swap", uid=111)
    check("prompt skip: no swap made", m.t1_impact_used is False)
    check("prompt skip: told the channel", any("held back" in t for t in ch.texts()))
    check("prompt skip: innings marked declined", getattr(m, "t1_impact_declined_inn", None) == 1)

    before = len(ch.sent)
    for _ in range(5):
        await B.run_over_break_impact(ch, m, inn)
    reprompts = [x for x in ch.sent[before:] if isinstance(x.view0, B.AutoImpactPromptView)]
    check("prompt skip: never re-asks this innings", len(reprompts) == 0, f"n={len(reprompts)}")

    # A new innings is a clean slate.
    m.current_innings_num = 2
    plans = B.plan_ai_impact_swaps(m, inn)
    check("prompt skip: offer returns next innings", len(plans) >= 1)
    del B.active_games[ch.id]


async def t_human_prompt_timeout():
    """No answer within the window -> no swap, play resumes, no re-prompt this innings."""
    ch = FakeChannel(1006)
    m = register(build_match(p1_id=111, p2_id=222, bat_first=1), ch)
    inn = m.current_innings
    arm_rule1(inn)

    orig = B.AI_IMPACT_DECIDE_SECS
    B.AI_IMPACT_DECIDE_SECS = 0.3
    try:
        t0 = asyncio.get_event_loop().time()
        await B.run_over_break_impact(ch, m, inn)
        elapsed = asyncio.get_event_loop().time() - t0
    finally:
        B.AI_IMPACT_DECIDE_SECS = orig

    check("prompt timeout: waited for the manager", 0.25 <= elapsed < 2.0, f"elapsed={elapsed:.2f}s")
    check("prompt timeout: no swap made", m.t1_impact_used is False)
    check("prompt timeout: told the channel", any("No answer in time" in t for t in ch.texts()))
    check("prompt timeout: innings marked declined", getattr(m, "t1_impact_declined_inn", None) == 1)
    check("prompt timeout: buttons removed", all(x.view is None for x in ch.sent
                                                 if isinstance(x.embed, B.discord.Embed) and x.content == "<@111>"))
    del B.active_games[ch.id]


async def t_human_prompt_edit():
    """Edit opens the manual picker and holds the over until the manager confirms."""
    ch = FakeChannel(1007)
    m = register(build_match(p1_id=111, p2_id=222, bat_first=1), ch)
    inn = m.current_innings
    arm_rule1(inn)

    orig = B.AI_IMPACT_DECIDE_SECS
    B.AI_IMPACT_DECIDE_SECS = 0.3
    holder = {}

    async def clicker():
        for _ in range(200):
            await asyncio.sleep(0.005)
            v = next((x.view0 for x in ch.sent if isinstance(x.view0, B.AutoImpactPromptView)), None)
            if v is None:
                continue
            inter = FakeInteraction(ch, 111)
            await v.edit_swap.callback(inter)
            edit_view = inter.response.messages[-1][1]
            holder["edit_view"] = edit_view
            await asyncio.sleep(0.4)     # longer than the decide window: the over must WAIT
            holder["waited_past_window"] = True
            # Manager picks their own swap.
            edit_view.select_out._values = ["HOME_PB2"]
            edit_view.select_in._values = ["HOME_SUB_BAT"]
            await edit_view.confirm_cb(FakeInteraction(ch, 111))
            return
    try:
        task = asyncio.create_task(clicker())
        await B.run_over_break_impact(ch, m, inn)
        await task
    finally:
        B.AI_IMPACT_DECIDE_SECS = orig

    check("prompt edit: manual picker opened", isinstance(holder.get("edit_view"), B.ImpactPlayerSelectView))
    check("prompt edit: over waited past the decide window", holder.get("waited_past_window") is True)
    check("prompt edit: manager's own swap applied", m.t1_impact_used is True)
    check("prompt edit: manager's chosen sub is in", getattr(m, "t1_impact_sub_name", None) == "HOME_SUB_BAT")
    check("prompt edit: manager's chosen player went out",
          inn.batting_stats["HOME_PB2"].dismissal in ("Subbed Out", "Retired (Sub)"))
    del B.active_games[ch.id]


async def t_prompt_owner_only():
    """Only that side's manager (or the tournament manager) can answer the offer."""
    ch = FakeChannel(1008)
    m = register(build_match(p1_id=111, p2_id=222, bat_first=1), ch)
    inn = m.current_innings
    arm_rule1(inn)

    orig = B.AI_IMPACT_DECIDE_SECS
    B.AI_IMPACT_DECIDE_SECS = 0.6
    outsider = {}

    async def clicker():
        for _ in range(200):
            await asyncio.sleep(0.005)
            v = next((x.view0 for x in ch.sent if isinstance(x.view0, B.AutoImpactPromptView)), None)
            if v is None:
                continue
            inter = FakeInteraction(ch, 999)          # the opponent, not this side's manager
            outsider["allowed"] = await v.interaction_check(inter)
            own = FakeInteraction(ch, 111)
            outsider["owner_allowed"] = await v.interaction_check(own)
            return
    try:
        task = asyncio.create_task(clicker())
        await B.run_over_break_impact(ch, m, inn)
        await task
    finally:
        B.AI_IMPACT_DECIDE_SECS = orig

    check("prompt access: opponent is refused", outsider.get("allowed") is False)
    check("prompt access: own manager is allowed", outsider.get("owner_allowed") is True)
    del B.active_games[ch.id]


async def t_manager_uid_resolution():
    """Who counts as a human manager."""
    m_vs_ai = build_match(p1_id=111, p2_id=None)
    check("uid: human host resolves", B._impact_manager_uid(m_vs_ai, 1) == 111)
    check("uid: AI opponent resolves to None", B._impact_manager_uid(m_vs_ai, 2) is None)

    m_pvp = build_match(p1_id=111, p2_id=222)
    check("uid: both managers resolve in pvp", (B._impact_manager_uid(m_pvp, 1),
                                                B._impact_manager_uid(m_pvp, 2)) == (111, 222))

    m_tourney = build_match(p1_id=0, p2_id=0)
    check("uid: tournament placeholder ids resolve to None",
          B._impact_manager_uid(m_tourney, 1) is None and B._impact_manager_uid(m_tourney, 2) is None)

    m_bot = build_match(p1_id=-5, p2_id=-6)
    check("uid: club bot ids resolve to None",
          B._impact_manager_uid(m_bot, 1) is None and B._impact_manager_uid(m_bot, 2) is None)


async def t_rule2_plan():
    """Rule 2 (attack a bowler short) still plans the bowler swap, and only when bowling."""
    ch = FakeChannel(1009)
    m = register(build_match(p1_id=111, p2_id=222, bat_first=1, t1_shape="short", t2_shape="short"), ch)
    inn = m.current_innings
    check("rule2: setup - side is a bowler short", B._bowling_options(m.team1["players"]) == 4,
          f"opts={B._bowling_options(m.team1['players'])}")

    # Team1 is BATTING first here, so its own Rule 2 must hold; team2 (bowling first) fires
    # only once a frontline bowler has finished his quota, or inside the last 5 overs.
    plans = B.plan_ai_impact_swaps(m, inn)
    check("rule2: batting side holds its bowler swap", all(p["team_num"] != 1 for p in plans), f"{plans}")

    inn.total_balls = m.max_balls - 24     # inside the last 5 overs
    plans = B.plan_ai_impact_swaps(m, inn)
    p2 = next((p for p in plans if p["team_num"] == 2), None)
    check("rule2: bowling side plans late in the innings", p2 is not None, f"{plans}")
    if p2:
        check("rule2: brings the bench bowler on", p2["in_player"]["name"] == "AWAY_SUB_BOWL")
        check("rule2: drops a spare batter", p2["out_name"] in ("AWAY_T7", "AWAY_T6"), p2["out_name"])
        check("rule2: labelled as completing the attack", p2["reason"] == "completing the attack")
    del B.active_games[ch.id]


async def t_impact_off_and_used():
    """No offers when the rule is off, no subs are left, or the impact is already spent."""
    ch = FakeChannel(1010)
    m = register(build_match(p1_id=111, p2_id=222, impact=False, bat_first=1), ch)
    inn = m.current_innings
    arm_rule1(inn)
    check("gate: rule off -> no plans", B.plan_ai_impact_swaps(m, inn) == [])

    m.impact_player = True
    check("gate: rule on -> plans", len(B.plan_ai_impact_swaps(m, inn)) >= 1)

    m.t1_impact_used = True
    check("gate: already used -> no plan for that side",
          all(p["team_num"] != 1 for p in B.plan_ai_impact_swaps(m, inn)))

    m.t1_impact_used = False
    m.t1_subs = []
    check("gate: empty bench -> no plan for that side",
          all(p["team_num"] != 1 for p in B.plan_ai_impact_swaps(m, inn)))
    del B.active_games[ch.id]


async def t_endmatch_during_prompt():
    """/endmatch while the offer is up must stop the sim, not bowl the next over."""
    ch = FakeChannel(1011)
    m = register(build_match(p1_id=111, p2_id=222, bat_first=1), ch)
    m.simulation_mode = "whole_match"
    m.verbose = False
    inn = m.current_innings
    arm_rule1(inn)

    orig = B.AI_IMPACT_DECIDE_SECS
    B.AI_IMPACT_DECIDE_SECS = 0.3
    balls_at_kill = {}

    async def killer():
        for _ in range(400):
            await asyncio.sleep(0.005)
            if any(isinstance(x.view0, B.AutoImpactPromptView) for x in ch.sent):
                balls_at_kill["balls"] = inn.total_balls
                B.active_games.pop(ch.id, None)       # simulates /endmatch
                return
    try:
        task = asyncio.create_task(killer())
        await B.loop_current_innings_simulation(ch, m)
        await task
    finally:
        B.AI_IMPACT_DECIDE_SECS = orig

    check("endmatch: sim stopped", B.active_games.get(ch.id) is None)
    check("endmatch: no further balls bowled after the kill",
          inn.total_balls == balls_at_kill.get("balls"),
          f"{balls_at_kill.get('balls')} -> {inn.total_balls}")
    check("endmatch: _auto_loop released", getattr(m, "_auto_loop", False) is False)
    check("endmatch: no scorecard/innings-end posted", not any("Innings 1 Complete" in t for t in ch.texts()))


async def t_auto_loop_flag():
    """The `cv over` guard flag is only set while a loop is actually running."""
    ch = FakeChannel(1012)
    m = register(build_match(p1_id=111, p2_id=222, impact=False), ch)
    check("flag: not set before any sim", getattr(m, "_auto_loop", False) is False)

    seen = {}
    m.simulation_mode = "whole_match"
    m.verbose = True

    def on_send(msg):
        if msg.embed is not None and msg.view is None:
            seen["during"] = getattr(m, "_auto_loop", False)
            m._step_over = True
    ch.on_send = on_send
    await B.loop_current_innings_simulation(ch, m)

    check("flag: set while the loop runs", seen.get("during") is True)
    check("flag: cleared once the loop exits", getattr(m, "_auto_loop", False) is False)
    del B.active_games[ch.id]


async def t_step_on_last_over_of_innings():
    """`cv over` typed on the over that also ENDS the innings must not open a bowler pick
    for an over that will never be bowled - the innings break takes over instead."""
    ch = FakeChannel(1013)
    m = register(build_match(overs=1, p1_id=111, p2_id=222, impact=False), ch)
    m.simulation_mode = "whole_match"
    m.verbose = True
    m.sim_only = True
    m._step_over = True          # queued from ball one; the only over boundary ends the innings

    await B.loop_current_innings_simulation(ch, m)
    inn = m.innings1

    check("last over: innings actually ended", B._innings_finished(m, inn),
          f"balls={inn.total_balls} wkts={inn.wickets}")
    check("last over: step request consumed", getattr(m, "_step_over", False) is False)
    texts = ch.texts()
    brk = next((i for i, t in enumerate(texts) if "Innings 1 Complete" in t), -1)
    picks = [i for i, t in enumerate(texts) if "pick your bowler" in t.lower()]
    check("last over: no bowler pick before the innings break (no phantom over)",
          brk >= 0 and all(i > brk for i in picks), f"break@{brk} picks@{picks}")
    check("last over: the only bowler pick is over 1 of the NEXT innings",
          all("Over 1" in texts[i] for i in picks), f"{[texts[i] for i in picks]}")
    check("last over: no over-step pause message",
          not any("paused before the next over" in t for t in ch.texts()))
    check("last over: innings break ran", any("Innings 1 Complete" in t for t in ch.texts()),
          f"{[t[:40] for t in ch.texts()]}")
    check("last over: auto-sim switched off so innings 2 starts at the hub",
          m.sim_only is False and m.simulation_mode == "interactive")
    B.active_games.pop(ch.id, None)


async def t_step_then_hub_can_resim():
    """After stepping out, the hub's sim lock is free so the next pick actually runs."""
    ch = FakeChannel(1014)
    m = register(build_match(p1_id=111, p2_id=222, impact=False), ch)
    m.simulation_mode = "whole_match"
    m.verbose = True

    def on_send(msg):
        if msg.embed is not None and msg.view is None:
            m._step_over = True
    ch.on_send = on_send
    await B.loop_current_innings_simulation(ch, m)

    check("re-sim: sim lock is free", getattr(m, "_sim_running", False) is False)
    check("re-sim: _auto_loop free", getattr(m, "_auto_loop", False) is False)

    # Now play on verbose again from the hub state and let it run to the end of the innings.
    ch.on_send = None
    balls_before = m.innings1.total_balls
    m.simulation_mode = "whole_match"
    m.verbose = True
    await B.loop_current_innings_simulation(ch, m)
    check("re-sim: innings continued from where it paused",
          m.innings1.total_balls > balls_before, f"{balls_before} -> {m.innings1.total_balls}")
    check("re-sim: innings ran to completion", B._innings_finished(m, m.innings1))
    B.active_games.pop(ch.id, None)


async def main():
    random.seed(7)
    for t in (t_step_verbose, t_step_bbb, t_ai_side_auto_swaps, t_human_prompt_confirm,
              t_human_prompt_skip, t_human_prompt_timeout, t_human_prompt_edit,
              t_prompt_owner_only, t_manager_uid_resolution, t_rule2_plan,
              t_impact_off_and_used, t_endmatch_during_prompt, t_auto_loop_flag,
              t_step_on_last_over_of_innings, t_step_then_hub_can_resim):
        print(f"\n--- {t.__name__} ---")
        try:
            await t()
        except Exception as e:
            import traceback
            traceback.print_exc()
            FAIL.append(f"{t.__name__} (crashed: {e})")

    print(f"\n================ {len(PASS)} passed, {len(FAIL)} failed ================")
    for f in FAIL:
        print(f"  FAILED: {f}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
