"""
Career Mode — Phase 4: Club Matches (PvP that pays coins).

Built in independently-testable sub-phases:
  4.1  Lobby   : create / join / leave / view / start — forms balanced teams. (THIS FILE)
  4.2  Simulate: build both XIs from careers and run a full match -> scorecard.
  4.3  Payouts : coins + lifetime stats per player (PvP only — AI never pays).
  4.4  Interactive, turn-gated ball-by-ball club match.

Lobbies are ephemeral (in-memory, per channel) just like the bot's active_games.
"""
import time

import career_manager as CM

LOBBIES = {}          # channel_id -> ClubLobby

MAX_PER_SIDE = 11     # short-sided is fine; both sides must be EQUAL
MIN_OVERS, MAX_OVERS = 2, 20
DEFAULT_OVERS = 5


class ClubLobby:
    def __init__(self, channel_id, host_id, host_name, overs=DEFAULT_OVERS):
        self.channel_id = channel_id
        self.host_id = host_id
        self.host_name = host_name
        self.overs = max(MIN_OVERS, min(int(overs), MAX_OVERS))
        self.created_at = int(time.time())
        self.started = False
        self.players = []           # list of {"id": int, "name": str}
        self.add(host_id, host_name)

    # ── membership ──
    def has(self, uid):
        return any(p["id"] == uid for p in self.players)

    def add(self, uid, name):
        if self.has(uid):
            return False, "already_in"
        if len(self.players) >= MAX_PER_SIDE * 2:
            return False, "full"
        self.players.append({"id": uid, "name": name})
        return True, None

    def remove(self, uid):
        before = len(self.players)
        self.players = [p for p in self.players if p["id"] != uid]
        return len(self.players) != before

    # ── readiness / teams ──
    def count(self):
        return len(self.players)

    def per_side(self):
        return len(self.players) // 2

    def is_ready(self):
        n = len(self.players)
        return n >= 2 and n % 2 == 0

    def _ovr(self, uid):
        c = CM.get_career(uid)
        return c["ovr"] if c else CM.BASE_OVR

    def make_teams(self):
        """Snake-draft players by OVR into two equal, balanced sides.
        Returns (team_a, team_b) as lists of {id,name,ovr}."""
        ranked = sorted(
            ({"id": p["id"], "name": p["name"], "ovr": self._ovr(p["id"])} for p in self.players),
            key=lambda x: x["ovr"], reverse=True,
        )
        a, b = [], []
        for i, p in enumerate(ranked):
            (a if i % 4 in (0, 3) else b).append(p)   # snake: A B B A A B B A ...
        return a, b

    def team_strength(self, team):
        return sum(p["ovr"] for p in team)
