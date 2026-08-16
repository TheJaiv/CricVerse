"""
The live broadcast controller for career matches.

One pinned message per innings holds the broadcast card; every ball edits that
message instead of posting a new one, which is what makes the feed read as a live
graphic rather than a wall of scoreboards. Highlight balls additionally get an
animated replay posted as its own message, so the pinned card stays put.

Rules this module exists to enforce:
  * rendering happens in a worker thread - Pillow is CPU-bound and the event loop
    is also driving every other match on the bot
  * edits are throttled, because Discord will 429 a per-ball edit storm
  * a render or upload failure NEVER propagates - the caller falls back to the
    text embed. A picture is not worth killing a live match over.
"""
import asyncio
import os
import time

import discord

from career import snapshot as SNAP
from career import ballfeed as BF
from career.ui import broadcast as BC
from career.ui import motion as MO

# Minimum seconds between two edits of the same live card. Interactive career
# matches wait on human button presses anyway, so this only bites during the
# AI/bot stretches where balls resolve back to back. Overridable so the offline
# harness - which drives thousands of balls with no real Discord behind it - is
# not forced to sit out a real rate-limit window per delivery.
EDIT_INTERVAL = float(os.environ.get("CAREER_GUI_EDIT_INTERVAL", "1.5"))

# Replay budget per match, and the minimum gap between two clips. A six-fest
# would otherwise bury the card under GIFs.
MAX_GIFS_PER_MATCH = 25
GIF_MIN_BALL_GAP = 4

_SESSIONS = {}          # channel_id -> Session


class Session:
    def __init__(self, channel):
        self.channel = channel
        self.message = None
        self.innings_num = None
        self.card_over = None       # (innings, over) the live card belongs to
        self.last_edit = 0.0
        self.gifs_sent = 0
        self.last_gif_ball = (0, -99)   # (innings, ball_index) of the last replay
        self.shown_ball = None          # (innings, ball_index) already replayed
        self.logged_ball = None         # (innings, ball_index) already written to the log
        self.failed = False             # one hard failure disables the card for the match

    async def _render(self, state):
        return await asyncio.to_thread(BC.render_live_card, state)

    async def push(self, match, career=None, force=False):
        """Redraw the card. Returns True if the card is carrying the scoreboard."""
        if self.failed:
            return False
        now = time.time()
        gap = now - self.last_edit
        if self.message is not None and gap < EDIT_INTERVAL:
            if not force:
                return True                 # card is live and current enough
            # A forced update is one the viewer must see (a ball resolved), so it
            # waits out the rest of the window rather than skipping the guard.
            # Human turns are slower than this anyway; it only paces the stretches
            # where bot batters resolve balls back to back, which is exactly where
            # an unthrottled edit loop would earn a 429.
            await asyncio.sleep(EDIT_INTERVAL - gap)
        try:
            state = SNAP.build_broadcast_state(match, career)
            buf = await self._render(state)
            file = discord.File(buf, filename="live.png")
            if self.message is None or self.innings_num != match.current_innings_num:
                # New innings gets its own card so the first innings stays readable
                # in the channel history instead of being overwritten.
                self.message = await self.channel.send(file=file)
                self.innings_num = match.current_innings_num
            else:
                # Editing a message that carries a file requires resending it via
                # attachments - passing only `file` silently keeps the old image.
                await self.message.edit(attachments=[file])
            self.last_edit = time.time()
            return True
        except Exception as e:
            print(f"career live card failed: {e}")
            self.failed = True
            return False

    @staticmethod
    def _key(rec):
        """Ball identity across the whole match.

        ball_index restarts at 0 every innings, so keying on it alone made the
        innings-2 gap check compare against an innings-1 index - a negative gap
        that silently suppressed every replay for the entire second innings.
        """
        return (rec.get("innings", 1), rec.get("ball_index", 0))

    async def highlight(self, match):
        """Post a replay clip if the last ball earned one."""
        if self.failed:
            return
        rec = getattr(match, "last_ball", None)
        if not rec or self._key(rec) == self.shown_ball:
            return
        if not BF.is_highlight(rec):
            return
        if self.gifs_sent >= MAX_GIFS_PER_MATCH:
            return
        inn, idx = self._key(rec)
        last_inn, last_idx = self.last_gif_ball
        if inn == last_inn and idx - last_idx < GIF_MIN_BALL_GAP:
            return
        self.shown_ball = self._key(rec)
        try:
            buf = await asyncio.to_thread(MO.build_replay_gif, rec)
            await self.channel.send(file=discord.File(buf, filename="replay.gif"))
            self.gifs_sent += 1
            self.last_gif_ball = self._key(rec)
        except Exception as e:
            print(f"career replay failed: {e}")

    async def drs_clip(self, match, verdict):
        """Ball-tracking clip for a review. Always posted - a review is the moment
        the graphic exists for, so it ignores the highlight budget."""
        if self.failed:
            return
        rec = getattr(match, "last_ball", None)
        if not rec:
            return
        try:
            buf = await asyncio.to_thread(MO.build_drs_gif, rec, verdict)
            await self.channel.send(file=discord.File(buf, filename="drs.gif"))
        except Exception as e:
            print(f"career drs clip failed: {e}")


def is_degraded(channel):
    """True when this channel's graphics have failed and the caller should fall
    back to the text scoreboard."""
    s = _SESSIONS.get(getattr(channel, "id", None))
    return bool(s and s.failed)


def session_for(channel):
    s = _SESSIONS.get(channel.id)
    if s is None:
        s = _SESSIONS[channel.id] = Session(channel)
    return s


def end(channel_id):
    _SESSIONS.pop(channel_id, None)


_TONE_ICON = {"wicket": "🔴", "six": "🟣", "four": "🔵", "wide": "🟠",
              "noball": "🟠", "dot": "⚪"}


def result_line(rec):
    """One-line summary of a delivery.

    Replaces the three-line commentary block that used to be posted per ball. At
    four-plus messages a ball, the prompts and results buried each other - see
    the playtest. One dense line per ball reads as a scorecard log instead.
    """
    if not rec:
        return None
    tone = SNAP.ball_tone(rec)
    icon = _TONE_ICON.get(tone, "⚫")
    over = f"{rec.get('over', 0)}.{rec.get('ball', 1)}"
    bits = [f"{icon} `{over}`  **{rec.get('bowler','')}** to **{rec.get('striker','')}**"]
    deliv = rec.get("delivery")
    shot = rec.get("shot")
    detail = " · ".join(x for x in (deliv, shot) if x)
    if detail:
        bits.append(detail)

    if rec.get("dismissal"):
        bits.append(f"**WICKET** — {rec.get('dismissal_desc') or rec['dismissal']}")
    elif rec.get("is_wide"):
        bits.append("**WIDE** +1")
    else:
        runs = rec.get("runs_off_bat", 0)
        word = {0: "dot", 1: "1 run", 2: "2 runs", 3: "3 runs",
                4: "**FOUR**", 6: "**SIX**"}.get(runs, f"{runs} runs")
        if rec.get("is_bye"):
            word = f"{rec.get('extras', 0)} leg byes"
        if rec.get("is_no_ball"):
            word = f"NO BALL · {word}"
        bits.append(word)
    if rec.get("free_hit"):
        bits.append("🛡️ free hit")
    return "  ·  ".join(bits)


async def turn(channel, match, prompt, view=None, career=None):
    """Post one turn: the ball that just happened, then the card + the prompt.

    The card is DELETED and re-posted rather than edited in place. Editing kept
    the graphic pinned wherever it was first sent, so after a few balls of
    prompts it had scrolled far above the action and players were looking at a
    scoreboard they had to hunt for. Re-posting keeps it as the newest message,
    directly above the buttons the player is about to press.

    Returns the new message (views that need `.message` can hold onto it).
    """
    s = session_for(channel)

    # 1. The ball that just resolved is drawn ON the card (see the last-ball
    #    banner in career/ui/broadcast.py), not posted as its own chat line - the
    #    feed is meant to read as a run of images with nothing between them.
    #    Draining wide_extra_msg here stops the old text notice being posted.
    rec = getattr(match, "last_ball", None)
    if rec:
        s.logged_ball = s._key(rec)
    if getattr(match, "wide_extra_msg", ""):
        match.wide_extra_msg = ""

    # 2. replay clip for the big moments, before the card so the card stays last
    if not s.failed:
        await s.highlight(match)

    # 3. a fresh card for this ball
    old = s.message
    old_over = s.card_over
    state = None
    msg = None
    if not s.failed:
        try:
            state = SNAP.build_broadcast_state(match, career)
            buf = await asyncio.to_thread(BC.render_live_card, state)
            msg = await channel.send(content=prompt,
                                     file=discord.File(buf, filename="live.png"),
                                     view=view)
            s.message = msg
            s.card_over = (match.current_innings_num, state.get("over_no", 0))
            s.innings_num = match.current_innings_num
            s.last_edit = time.time()
        except Exception as e:
            print(f"career turn card failed: {e}")
            s.failed = True
            msg = None

    if msg is None:      # card unavailable - still send the prompt so play continues
        msg = await channel.send(content=prompt, view=view)
        s.message = msg

    # A CARD PER BALL, all kept.
    # Every ball posts its own image and none are deleted, so the channel reads as
    # a continuous run of graphics and scrolling back through them IS the match
    # history - which is what was lost when a single card was edited in place.
    # Only the previous message's BUTTONS are stripped, so old cards can't be
    # clicked and the only live controls are the ones on the newest image.
    if old is not None and old.id != msg.id:
        try:
            await old.edit(view=None)
        except Exception:
            pass
    return msg


async def push(channel, match, career=None, force=False):
    """Update the live card and post any replay the ball earned.

    Returns True when the card is carrying the scoreboard, so the caller can skip
    the text embed; False means the caller should fall back.
    """
    s = session_for(channel)
    ok = await s.push(match, career, force=force)
    if ok:
        await s.highlight(match)
    return ok


async def drs_clip(channel, match, verdict):
    await session_for(channel).drs_clip(match, verdict)


async def post_innings_charts(channel, innings):
    """One combined chart post at an innings break: runs per over, plus the top
    scorer's wagon wheel. Both are read off innings.ball_history, which is why
    they only exist now that the engine records every delivery.
    """
    hist = list(getattr(innings, "ball_history", None) or [])
    if len(hist) < 6:
        return
    try:
        team = innings.batting_team["name"]
        files = []
        buf = await asyncio.to_thread(
            BC.render_manhattan, hist, f"RUNS PER OVER · {team.upper()}")
        files.append(discord.File(buf, filename="manhattan.png"))

        top = None
        best = 0
        for name, st in innings.batting_stats.items():
            if st.runs_scored > best:
                top, best = name, st.runs_scored
        if top and best >= 10:
            wbuf = await asyncio.to_thread(BC.render_wagon_wheel, hist, top)
            files.append(discord.File(wbuf, filename="wagon.png"))

        await channel.send(files=files)
    except Exception as e:
        print(f"career innings charts failed: {e}")
