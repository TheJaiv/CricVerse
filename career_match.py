"""
Career Mode — Phase 4: Club Matches (PvP that pays coins).

4.1  Lobby   : create / join / leave / view / swap / start — numbered roster, two
               balanced teams, slot 1 of each team = captain (host can re-order via swap).
4.2  Match   : interactive, per-player control (each player bats/bowls their own turn;
               the captain picks openers, the next batter, and the bowler).
4.3  Payouts : coins + lifetime stats per player (PvP only — AI never pays).

Lobbies are ephemeral (in-memory, per channel) like the bot's active_games.
"""
import time
import random

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
        self.players = []          # flat join list: {"id", "name"}
        self.team_a = []           # ordered: {"id","name","ovr"}  (index 0 = captain)
        self.team_b = []
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
        self._rebuild_teams()
        return True, None

    def remove(self, uid):
        before = len(self.players)
        self.players = [p for p in self.players if p["id"] != uid]
        changed = len(self.players) != before
        if changed:
            self._rebuild_teams()
        return changed

    def add_bot(self):
        """Add an AI bot whose ratings are the AVERAGE of the joined human players'.
        Returns (ok, name_or_error)."""
        humans = [p for p in self.players if not p.get("is_bot")]
        careers = [c for c in (CM.get_career(p["id"]) for p in humans) if c]
        if not careers:
            return False, "Add at least one human player before adding bots."
        if len(self.players) >= MAX_PER_SIDE * 2:
            return False, "full"
        attrs = {k: round(sum(c["attributes"][k] for c in careers) / len(careers)) for k in CM.ATTRS}
        n = sum(1 for p in self.players if p.get("is_bot")) + 1
        fake = {
            "username": f"Bot {n}",
            "attributes": attrs,
            "bowling_type": random.choice(list(CM.BOWLING_TYPES)),
            "mindset": random.choice(list(CM.MINDSETS)),
        }
        CM.refresh_ovr(fake)
        self.players.append({"id": -(1000 + n), "name": f"Bot {n}", "is_bot": True, "career": fake})
        self._rebuild_teams()
        return True, f"Bot {n}"

    # ── teams ──
    def _entry(self, p):
        if p.get("is_bot"):
            return {"id": p["id"], "name": p["name"], "ovr": p["career"]["ovr"],
                    "is_bot": True, "career": p["career"]}
        c = CM.get_career(p["id"])
        return {"id": p["id"], "name": p["name"], "ovr": c["ovr"] if c else CM.BASE_OVR}

    def _rebuild_teams(self):
        """Snake-draft current players by OVR into two balanced sides. Called on every
        join/leave, so finalize joins BEFORE using `cv swap` to arrange captains/order."""
        ranked = sorted((self._entry(p) for p in self.players), key=lambda x: x["ovr"], reverse=True)
        self.team_a, self.team_b = [], []
        for i, p in enumerate(ranked):
            (self.team_a if i % 4 in (0, 3) else self.team_b).append(p)

    def count(self):
        return len(self.players)

    def per_side(self):
        return min(len(self.team_a), len(self.team_b))

    def is_ready(self):
        n = len(self.players)
        return n >= 2 and n % 2 == 0 and len(self.team_a) == len(self.team_b)

    def captain_a(self):
        return self.team_a[0] if self.team_a else None

    def captain_b(self):
        return self.team_b[0] if self.team_b else None

    def team_strength(self, team):
        return sum(p["ovr"] for p in team)

    # ── swap (host re-orders by global number; slot 1 of a team = captain) ──
    def _locate(self, num):
        if 1 <= num <= len(self.team_a):
            return ("a", num - 1)
        k = num - len(self.team_a)
        if 1 <= k <= len(self.team_b):
            return ("b", k - 1)
        return None

    def swap(self, i, j):
        if i == j:
            return False, "Pick two different numbers."
        li, lj = self._locate(i), self._locate(j)
        if not li or not lj:
            return False, "Invalid player number — check `cv lobby`."
        ta = self.team_a if li[0] == "a" else self.team_b
        tb = self.team_a if lj[0] == "a" else self.team_b
        ta[li[1]], tb[lj[1]] = tb[lj[1]], ta[li[1]]
        return True, None
