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
        self.last_edit = 0.0
        self.gifs_sent = 0
        self.last_gif_ball = -99
        self.shown_ball = None      # ball_index already replayed
        self.failed = False         # one hard failure disables the card for the match

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

    async def highlight(self, match):
        """Post a replay clip if the last ball earned one."""
        if self.failed:
            return
        rec = getattr(match, "last_ball", None)
        if not rec or rec.get("ball_index") == self.shown_ball:
            return
        if not BF.is_highlight(rec):
            return
        if self.gifs_sent >= MAX_GIFS_PER_MATCH:
            return
        if rec.get("ball_index", 0) - self.last_gif_ball < GIF_MIN_BALL_GAP:
            return
        self.shown_ball = rec.get("ball_index")
        try:
            buf = await asyncio.to_thread(MO.build_replay_gif, rec)
            await self.channel.send(file=discord.File(buf, filename="replay.gif"))
            self.gifs_sent += 1
            self.last_gif_ball = rec.get("ball_index", 0)
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


def session_for(channel):
    s = _SESSIONS.get(channel.id)
    if s is None:
        s = _SESSIONS[channel.id] = Session(channel)
    return s


def end(channel_id):
    _SESSIONS.pop(channel_id, None)


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
