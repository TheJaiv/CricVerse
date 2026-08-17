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

# Pause after a bot-resolved ball's card so the feed can be followed. Without it
# several deliveries land at once and the score appears to jump.
AI_BALL_DELAY = float(os.environ.get("CAREER_AI_BALL_DELAY", "1.3"))

# Replay budget per match, and the minimum gap between two BOUNDARY clips.
#
# Wickets and sixes ignore the gap entirely: they are the moments the feature
# exists for, and a spacing rule meant two boundaries in the same over silently
# lost the second clip - which read as replays "randomly not showing up".
# Only fours are spaced, because a boundary flurry really can bury the card.
MAX_GIFS_PER_MATCH = 30
GIF_MIN_BALL_GAP = 3

_SESSIONS = {}          # channel_id -> Session


class Session:
    def __init__(self, channel):
        self.channel = channel
        self.message = None
        self.innings_num = None
        self.card_over = None       # (innings, over) the live card belongs to
        self.card_ball = None       # (innings, ball_index) the live card belongs to
        self.card_kind = None       # "prompt" (asking for a ball) or "ball" (one already played)
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
        spaced = not (rec.get("dismissal") or rec.get("runs_off_bat") == 6)
        if spaced and inn == last_inn and idx - last_idx < GIF_MIN_BALL_GAP:
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
    elif s.channel is not channel:
        # A new match object in the same channel: the stored message belongs to
        # the finished match, and editing it would put the new prompt somewhere
        # nobody is looking - which softlocks the new match, because the buttons
        # never appear where the players are. Start clean instead.
        s = _SESSIONS[channel.id] = Session(channel)
    return s


def reset(channel_id):
    """Drop any session for a channel so the next match starts clean."""
    _SESSIONS.pop(channel_id, None)


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


# The prompt is plain message CONTENT, never an embed: a mention inside an embed
# does not notify anyone, and a turn-based match lives on those pings. Once the
# ball has been played the content is stripped, leaving a clean run of images.


async def turn(channel, match, prompt, view=None, career=None):
    """Post or update the card for the CURRENT ball.

    One image per ball. A ball can need two prompts (the bowler picks a variation,
    then the batter picks a shot) and posting a card for each produced two images
    per delivery. The second prompt of the same ball therefore EDITS the card in
    place; only a new ball posts a new image. Ball identity comes from the ball
    feed, so this needs no extra bookkeeping from the callers.

    Returns the message (views that need `.message` can hold onto it).
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

    # 3. the card for this ball.
    #
    # A ball can need two prompts - the bowler picks a variation, then the batter
    # picks a shot - and those two share one card, so the second EDITS the first.
    # But a card showing a ball that has already been PLAYED (posted by ball_card
    # for a bot delivery) must never be edited by the next prompt: the ball key is
    # the same, yet they are different moments, and editing merged two deliveries
    # into a single image. Hence the phase as well as the ball.
    ball_key = s._key(rec) if rec else None
    same_ball = (s.message is not None
                 and s.card_ball == ball_key
                 and s.card_kind == "prompt")
    old = s.message
    msg = None
    if not s.failed:
        try:
            state = SNAP.build_broadcast_state(match, career)
            buf = await asyncio.to_thread(BC.render_live_card, state)
            file = discord.File(buf, filename="live.png")
            if same_ball:
                await old.edit(content=prompt, attachments=[file], view=view)
                msg = old
            else:
                msg = await channel.send(content=prompt, file=file, view=view)
            s.message = msg
            s.card_ball = ball_key
            s.card_kind = "prompt"
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
    # Nothing is deleted, so scrolling back through the images IS the match
    # history. Once a ball has been played its card loses BOTH its buttons and its
    # prompt text, so the feed settles into a clean run of images and only the
    # newest one is asking anybody for anything.
    if old is not None and msg is not None and old.id != msg.id:
        try:
            await old.edit(content=None, view=None)
        except Exception:
            pass
    return msg


async def ball_card(channel, match, career=None, delay=None):
    """Post the card for a ball that resolved without a human prompt.

    When bots are batting or bowling, deliveries resolve back to back and no
    prompt is sent, so the next human prompt used to jump the score forward
    several balls at once. This gives every ball its own image, paced so it can
    be read.
    """
    s = session_for(channel)
    if s.failed:
        return None
    rec = getattr(match, "last_ball", None)
    key = s._key(rec) if rec else None
    if key is not None and key == s.card_ball:
        return None                      # already shown (a human prompt drew it)
    await s.highlight(match)
    try:
        state = SNAP.build_broadcast_state(match, career)
        buf = await asyncio.to_thread(BC.render_live_card, state)
        old = s.message
        msg = await channel.send(file=discord.File(buf, filename="live.png"))
        s.message = msg
        s.card_ball = key
        s.card_kind = "ball"        # already played - a later prompt must not edit it
        s.card_over = (match.current_innings_num, state.get("over_no", 0))
        s.last_edit = time.time()
        if old is not None:
            try:
                await old.edit(content=None, view=None)
            except Exception:
                pass
        await asyncio.sleep(AI_BALL_DELAY if delay is None else delay)
        return msg
    except Exception as e:
        print(f"career ball card failed: {e}")
        s.failed = True
        return None


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
