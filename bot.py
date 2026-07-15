import discord
from discord import app_commands
from discord.ext import commands, tasks
import random
import re
import csv
import signal
import difflib
import asyncio
import io
import os
import json
from PIL import Image, ImageDraw, ImageFont, ImageStat
import math
from core.keep_alive import keep_alive
from engine.odi_simulation import execute_ball_math_odi, get_smart_ai_bowler_odi
from engine.t20_simulation import execute_ball_math_t20, get_smart_ai_bowler_t20
from engine.test_simulation import (
    TestMatch as TestMatchObj,
    simulate_session as _test_sim_session,
    simulate_innings as _test_sim_innings,
    simulate_match as _test_sim_match,
    simulate_one_over_verbose as _test_sim_one_over_verbose,
    simulate_n_overs_verbose as _test_sim_n_overs_verbose,
    simulate_one_ball_interactive as _test_sim_one_ball,
    prepare_over_interactive as _test_prepare_over,
    PITCH_TYPES as TEST_PITCH_TYPES,
    WEATHER_TYPES as TEST_WEATHER_TYPES,
    TEAM_ALPHA, TEAM_BETA,
    _check_result as _test_check_result,
    _start_next_innings as _test_start_next_innings,
    _format_scorecard as _test_format_scorecard,
    _player_of_match as _test_player_of_match,
)
from engine.test_image import (
    generate_test_summary_image as _ti_summary,
    generate_test_scorecard_image as _ti_scorecard,
)
from league.tournament_manager import get_server_tournament, save_tournament, get_tournament_standings, _build_status_pages, _build_flat_pages, _build_ccodi_round_pages, _build_status_embed, TournamentStatusView, generate_t20wc_points_table, generate_t20wc_super8_table, T20StandingsView, generate_t20wc_knockouts_image, generate_t20wc_match_banner, acl_generate_playoffs, acl_bracket_embed, _acl_get, _acl_try_advance, revert_tournament_match, rebuild_tournament_stats, repair_tournament_schedule, _tm_next_mid, owner_can_launch, build_team_fixtures_embed, generate_acl_points_table, assign_tournament_conditions, canonical_pitch, canonical_weather, ALL_PITCHES, ALL_WEATHER, TournamentLeaderboardView, build_player_stats_embed, find_player_in_tournament, PlayerStatsTeamSelectView, stadiums_enabled, default_stadium_pool, get_stadium_pool, canonical_stadium, reroll_stadiums, DEFAULT_ACL_STADIUMS, SquadConfirmView, build_squad_confirm_text, build_squad_confirm_embed, match_order_gate, MATCH_ORDER_LABELS, build_tournament_summary_embeds, generate_round_robin_schedule, generate_ipl_schedule, ipl_try_advance, build_standings_message
from league import rating_league
from league.rating_league import (
    RATING_CONFIG, is_rating_tournament, create_rating_tournament, create_open_match,
    rating_standings, rating_board_embed, rating_bracket_embed,
    generate_rating_playoffs, apply_tournament_boosts, apply_boost,
    BOOST_COST, BOOST_MAX_PER_PLAYER, BOOST_MAX_PER_TEAM,
)
from league import dsl_manager
from league.dsl_manager import (
    DSL_CONFIG, is_dsl_enabled, set_dsl_enabled, dsl_enabled_servers,
    create_dsl_tournament, is_dsl_tournament, canonical_venue, set_home_stadium,
    dsl_generate_league_schedule, dsl_generate_playoffs, dsl_bracket_embed,
    write_season_archive, save_uploaded_archive,
    aggregate_player_stats, aggregate_venue_stats, season_history,
    get_season_summary, season_detail_embed, reset_dsl_server, player_season_history,
)
from core.subscription_manager import (
    load_data_from_bin, load_tournament_data_from_bin,
    save_data_to_bin, save_tournament_data_to_bin,
    check_potential_quota, consume_quota,
    update_user_tier, update_server_tier, get_auth_admins, toggle_auth_admin,
    bulk_grant_tier, list_expiring_subs, list_all_subs, remove_subs_by_indexes,
    get_all_players, add_player, add_players_bulk, update_player, delete_players, clean_duplicate_players,
    get_tier_status, is_channel_restricted, toggle_restricted_channel,
    is_ratings_channel, toggle_ratings_channel,
    get_match_log_channel, set_match_log_channel, clear_match_log_channel, DB_CACHE,
    get_match_counts, increment_match_count, set_match_count,
    apply_server_overrides, set_server_override, reset_server_override, get_server_overrides,
    record_draft_pvp, record_draft_ai, get_draft_stats,
    save_custom_team, get_custom_team, delete_custom_team, list_custom_teams,
)
from league import draft_mode as dm
from core import global_stats as gstats
# Career Mode (LIVE)
# Launched for everyone after the 2026-07-06 hardcore verification pass (see
# tools/career_flow_test.py). Career code still loads defensively: any failure
# here can NEVER crash bot startup. Set env var CAREER_MODE=0 to kill-switch it.
CAREER_MODE_ENABLED = os.environ.get("CAREER_MODE", "1") == "1"
try:
    from career import career_manager as CM
    from career import career_ui
    from career import career_match
    from career.career_manager import load_careers
    _CAREER_OK = True
except Exception as _career_err:
    print(f"Career module not loaded ({_career_err}); Career Mode disabled.")
    CAREER_MODE_ENABLED = False
    _CAREER_OK = False
    def load_careers():  # no-op fallback
        pass

# ---- Setup & configuration ----
ADMIN_DISCORD_ID = int(os.environ.get("ADMIN_DISCORD_ID", "1087369198801526836"))
_log_env = os.environ.get("LOG_CHANNEL_ID")
LOG_CHANNEL_ID = int(_log_env) if _log_env and _log_env.isdigit() else 0

# Career Mode gating helpers
_CAREER_SOON = "🚧 **Career Mode is coming soon!** It's still in development."

def _can_use_career(ctx):
    """During development Career Mode is owner/admin-only; everyone else sees
    'coming soon'. When CAREER_MODE_ENABLED is flipped on, it opens to all."""
    if not _CAREER_OK:
        return False
    if CAREER_MODE_ENABLED:
        return True
    try:
        if ctx.author.id == ADMIN_DISCORD_ID:
            return True
        if ctx.guild and ctx.author.guild_permissions.administrator:
            return True
        if str(ctx.author.id) in get_auth_admins():
            return True
        # Career Beta testers get in before the global launch.
        sid = str(ctx.guild.id) if ctx.guild else ""
        if get_tier_status(str(ctx.author.id), sid)[0] == "Career Beta":
            return True
    except Exception:
        pass
    return True

def _is_premium(ctx):
    """Weekly/monthly perks: bot-granted premium pass, Nitro boosters, paid sub tiers, or owner."""
    try:
        if ctx.author.id == ADMIN_DISCORD_ID:
            return True
        if _CAREER_OK:
            _c = CM.get_career(ctx.author.id)
            if _c and CM.career_is_premium(_c):
                return True
        if getattr(ctx.author, "premium_since", None):
            return True
        u_tier, _, _, _, _ = get_tier_status(str(ctx.author.id), str(ctx.guild.id) if ctx.guild else "")
        if u_tier and u_tier != "Free":
            return True
    except Exception:
        pass
    return False

# Career Mode: tier Discord roles
_TIER_ROLE_PREFIX = "CricVerse"
_TIER_ROLE_COLORS = {
    "Bronze": 0xCD7F32, "Silver": 0xB0BAC7, "Gold": 0xE0B838,
    "Platinum": 0x68D6E2, "Diamond": 0x82AAFF,
}

def _tier_embed_color(tier):
    return discord.Color(_TIER_ROLE_COLORS.get(tier, 0x99AAB5))

def _attr_bar(v, width=12):
    filled = max(0, min(width, int(round(width * v / 99))))
    return "█" * filled + "░" * (width - filled)

async def _sync_tier_role(guild, member, career):
    """Create (if needed) and assign the Discord role for the member's tier, removing
    any other CricVerse tier roles. Returns (role_or_None, note_or_None)."""
    if guild is None or member is None or not _CAREER_OK:
        return None, None
    try:
        me = guild.me
        if me is None or not me.guild_permissions.manage_roles:
            return None, "I don't have the **Manage Roles** permission here."
        tier = career.get("tier", "Bronze")
        want = f"{_TIER_ROLE_PREFIX} {tier}"
        all_names = {f"{_TIER_ROLE_PREFIX} {t[2]}" for t in CM.TIERS}
        role = discord.utils.get(guild.roles, name=want)
        if role is None:
            role = await guild.create_role(
                name=want, colour=discord.Colour(_TIER_ROLE_COLORS.get(tier, 0x99AAB5)),
                hoist=True, mentionable=False, reason="CricVerse career tier role")
        if role >= me.top_role:
            return role, f"Move the **{want}** role below my role so I can assign it."
        to_remove = [r for r in member.roles if r.name in all_names and r.id != role.id]
        if to_remove:
            await member.remove_roles(*to_remove, reason="CricVerse tier change")
        if role not in member.roles:
            await member.add_roles(role, reason="CricVerse tier change")
        return role, None
    except discord.Forbidden:
        return None, "I lack permission (check role hierarchy)."
    except Exception as e:
        print(f"tier role sync error: {e}")
        return None, None

# Career Mode: club-match lobby (Phase 4.1)
def _get_live_lobby(channel_id):
    """Return the channel's lobby, silently dropping it if it expired (30 min, un-started)."""
    if not _CAREER_OK:
        return None
    lobby = career_match.LOBBIES.get(channel_id)
    if lobby and lobby.expired():
        career_match.LOBBIES.pop(channel_id, None)
        return None
    return lobby


def _lobby_embed(lobby, title):
    n = lobby.count()
    e = discord.Embed(
        title=title,
        description=(f"👑 Host: <@{lobby.host_id}>  ·  ⏱️ **{lobby.overs} overs**  ·  "
                     f"👥 **{n}** joined ({lobby.per_side()}-a-side)\n"
                     f"🧢 = captain. Host can `cv swap <a> <b>` to re-order / change captains."),
        color=discord.Color.green() if lobby.is_ready() else discord.Color.orange())

    def fmt(team, start):
        if not team:
            return "*(empty)*"
        rows = []
        for idx, p in enumerate(team):
            cap = " 🧢" if idx == 0 else ""
            bot = " 🤖" if p.get("is_bot") else ""
            rows.append(f"`{start + idx}.` {p['name']} ({p['ovr']}){bot}{cap}")
        return "\n".join(rows)

    e.add_field(name=f"🟢 Team A  ·  {lobby.team_strength(lobby.team_a)} OVR",
                value=fmt(lobby.team_a, 1), inline=True)
    e.add_field(name=f"🔴 Team B  ·  {lobby.team_strength(lobby.team_b)} OVR",
                value=fmt(lobby.team_b, 1 + len(lobby.team_a)), inline=True)

    if lobby.is_ready() and lobby.per_side() >= 2:
        e.set_footer(text="Teams even — host: `cv startmatch` to begin (or `cv swap` to arrange).")
    elif lobby.is_ready():
        e.set_footer(text="Need at least 2 players per side (4 total). Get more to `cv joinmatch`.")
    else:
        e.set_footer(text="Waiting for an even number of players. Join with `cv joinmatch`.")
    return e


# Career leaderboard
_LB_CATS = {
    "ovr":   ("🏆 OVR",        lambda c: (c.get("ovr", 60), c.get("coins", 0))),
    "coins": ("🪙 Coins",      lambda c: c.get("coins", 0)),
    "wins":  ("✅ Club Wins",  lambda c: c.get("club", {}).get("won", 0)),
}

def _build_lb_embed(guild, category, scope, requester_id):
    if not _CAREER_OK:
        return discord.Embed(title="Leaderboard unavailable", color=discord.Color.red())
    careers = [c for c in CM.all_careers() if c.get("debut_done")]
    if scope == "server" and guild:
        careers = [c for c in careers if guild.get_member(int(c["_id"])) is not None]
    label, key = _LB_CATS.get(category, _LB_CATS["ovr"])
    ranked = sorted(careers, key=key, reverse=True)

    e = discord.Embed(title=f"🏏 Career Leaderboard · {label}", color=discord.Color.gold())
    head = f"Scope: **{'This server' if scope == 'server' else 'Global'}**  ·  {len(ranked)} player(s)"
    if not ranked:
        e.description = head + "\n\n*No ranked players yet — `cv start_career` and make your debut!*"
        return e

    medals = ["🥇", "🥈", "🥉"]
    rows = []
    for i, c in enumerate(ranked[:15]):
        rk = medals[i] if i < 3 else f"`#{i+1:<2}`"
        nm = c.get("username", "?")[:16]
        if category == "coins":
            val = f"{c.get('coins', 0):,} 🪙"
        elif category == "wins":
            val = f"{c.get('club', {}).get('won', 0)} W"
        else:
            val = f"OVR {c.get('ovr', 60)} · {c.get('tier', 'Bronze')}"
        mine = " ⬅️ **you**" if c["_id"] == str(requester_id) else ""
        rows.append(f"{rk} **{nm}** — {val}{mine}")
    e.description = head + "\n\n" + "\n".join(rows)

    own = next((i for i, c in enumerate(ranked) if c["_id"] == str(requester_id)), None)
    if own is not None and own >= 15:
        e.set_footer(text=f"Your rank: #{own + 1} of {len(ranked)}")
    return e


class LeaderboardView(discord.ui.View):
    def __init__(self, guild, requester_id, category="ovr", scope="global"):
        super().__init__(timeout=180)
        self.guild = guild
        self.requester = requester_id
        self.category = category
        self.scope = scope

    async def _refresh(self, interaction):
        await interaction.response.edit_message(
            embed=_build_lb_embed(self.guild, self.category, self.scope, self.requester), view=self)

    @discord.ui.button(label="OVR", style=discord.ButtonStyle.primary, emoji="🏆")
    async def by_ovr(self, interaction, button):
        self.category = "ovr"; await self._refresh(interaction)

    @discord.ui.button(label="Coins", style=discord.ButtonStyle.secondary, emoji="🪙")
    async def by_coins(self, interaction, button):
        self.category = "coins"; await self._refresh(interaction)

    @discord.ui.button(label="Wins", style=discord.ButtonStyle.secondary, emoji="✅")
    async def by_wins(self, interaction, button):
        self.category = "wins"; await self._refresh(interaction)

    @discord.ui.button(label="Server / Global", style=discord.ButtonStyle.success, emoji="🌍")
    async def toggle_scope(self, interaction, button):
        self.scope = "global" if self.scope == "server" else "server"
        await self._refresh(interaction)


# Match counters (backed by MongoDB via subscription_manager)

def _increment_match_count(fmt: str) -> int:
    """Increment and return the new match number for the given format."""
    return increment_match_count(fmt)

def _set_match_count(fmt: str, n: int):
    set_match_count(fmt, n)

def _format_match_no_label(fmt: str) -> str:
    """Return 'T20-Match No 25' style label for the given format key."""
    c = get_match_counts()
    display = {"t20": "T20", "odi": "ODI", "test": "TEST"}.get(fmt, fmt.upper())
    return f"{display}-Match No {c.get(fmt, 0)}"

class CricketBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(command_prefix=commands.when_mentioned_or("cv ", "Cv ", "CV ", "cv", "Cv", "CV"), case_insensitive=True, intents=intents, help_command=None)
    
    async def setup_hook(self):
        from league.tournament_manager import TournamentCog
        await self.add_cog(TournamentCog(self))
        
        await self.add_cog(PrefixCog(self))
        await self.tree.sync()
        print("Slash commands synchronized globally.")
        print("Prefix commands loaded.")

        # Catch Render's SIGTERM so the global stats file can be DM'd out before the
        # disk is wiped (no-op on platforms without unix signals, e.g. Windows).
        try:
            asyncio.get_running_loop().add_signal_handler(
                signal.SIGTERM, lambda: asyncio.create_task(_stats_shutdown_backup()))
        except (NotImplementedError, RuntimeError) as e:
            print(f"SIGTERM stats backup not hooked: {e}")

bot = CricketBot()
active_games = {}
active_setups = {}
active_test_matches = {}   # channel_id -> TestMatchObj
active_drafts = set()       # channel_ids with a draft pick-phase in progress
setup_states = {}           # channel_id -> MatchSetupState, kept ALIVE through the whole
                            # pre-match setup (format->impact->names->XI->verify->pitch) so
                            #  endmatch can mark state.cancelled and the setup views bail.
draft_tasks = {}            # channel_id -> asyncio.Task for the running draft (cancellable)

# ---- Cloud database & security ----
@tasks.loop(hours=1)
async def auto_sync_db():
    """Refresh in-memory cache from MongoDB every hour (picks up manual edits)"""
    load_data_from_bin()
    load_tournament_data_from_bin()
    if CAREER_MODE_ENABLED:
        try: load_careers()
        except Exception as e: print(f"⚠️ load_careers failed (ignored): {e}")

@tasks.loop(hours=6)
async def global_stats_backup():
    """Crash safety net for the SIGTERM backup: if the process dies without a graceful
    shutdown (hard kill / crash), the owner still has a DM'd copy at most 6h old."""
    if not gstats.is_dirty():
        return
    try:
        owner = await bot.fetch_user(ADMIN_DISCORD_ID)
        await owner.send(f"🗃️ Periodic global stats backup — **{gstats.player_count()}** players:",
                         file=discord.File(gstats.flush_to_disk(), filename="global_stats.json"))
        gstats.clear_dirty()
    except Exception as e:
        print(f"Periodic stats backup failed: {e}")

async def _stats_shutdown_backup():
    """Render sends SIGTERM ~30s before killing the process (deploy/restart/spin-down).
    The local disk is wiped, so the stats file gets DM'd out while the gateway is
    still connected; the owner re-uploads it with `cv importstats` after the restart."""
    try:
        if gstats.is_dirty() and gstats.player_count():
            owner = await bot.fetch_user(ADMIN_DISCORD_ID)
            await owner.send("⚠️ **Bot is restarting** — restore with `cv importstats` once it's back:",
                             file=discord.File(gstats.flush_to_disk(), filename="global_stats.json"))
            gstats.clear_dirty()
    except Exception as e:
        print(f"Shutdown stats backup failed: {e}")
    finally:
        await bot.close()

@bot.event
async def on_ready():
    print(f"Logged in successfully as {bot.user.name}")
    load_data_from_bin()
    load_tournament_data_from_bin()
    if CAREER_MODE_ENABLED:
        try: load_careers()
        except Exception as e: print(f"⚠️ load_careers failed (ignored): {e}")
    if not auto_sync_db.is_running():
        auto_sync_db.start()
    if not global_stats_backup.is_running():
        global_stats_backup.start()
    print("Memory Cache Loaded and Ready.")
# ---- Core data structures & fallbacks ----

# Hardcoded fallback database to prevent crashes if the CSV is empty
# Default teams: two COMPLETELY EQUAL sides (stat-for-stat mirror images).
# Identical bat/bowl/role/archetype at every position, only the names differ, so
# a default-vs-default match is a true 50/50 contest. Balanced XI: 5 batters
# (incl. WK), 2 all-rounders, 4 bowlers, with a pace + spin mix for all pitches.
# Primary skill ratings span 80-92 (solid pros up to a marquee 92 star). Bowlers
# keep a realistic lower-order bat, batters a token bowl - the 80-92 range is the
# headline (primary) skill of each player. Both XIs share these exact stats.
_EQUAL_TEMPLATE = [
    # (bat, bowl, archetype, role)
    (88, 12, "Aggressor", "Batter"),               # opener
    (84, 12, "Anchor",    "Batter"),               # opener
    (92, 15, "Anchor",    "Batter"),               # marquee No.3
    (86, 18, "Aggressor", "Batter"),               # No.4
    (85, 10, "Finisher",  "Batter_WK"),            # keeper
    (82, 85, "Finisher",  "All-Rounder_Pace"),     # pace all-rounder
    (80, 86, "Anchor",    "All-Rounder_Spin_Off"), # spin all-rounder
    (42, 88, "Aggressor", "Bowler_Pace"),          # frontline pace
    (35, 90, "Finisher",  "Bowler_Pace"),          # spearhead pace
    (38, 80, "Standard",  "Bowler_Pace"),          # third seamer (range floor)
    (30, 86, "Standard",  "Bowler_Spin_Leg"),      # leg-spinner
]
_PROTAGONIST_NAMES = [
    "David Warner", "Usman Khawaja", "Virat Kohli", "Travis Head", "Jos Buttler",
    "Ben Stokes", "Ravichandran Ashwin", "Mitchell Starc", "Jasprit Bumrah",
    "Josh Hazlewood", "Rashid Khan",
]
_RIVAL_NAMES = [
    "Rohit Sharma", "Dimuth Karunaratne", "Joe Root", "Suryakumar Yadav", "Quinton de Kock",
    "Hardik Pandya", "Mohammad Nabi", "Kagiso Rabada", "Trent Boult",
    "Mohammed Shami", "Yuzvendra Chahal",
]
def _build_equal_xi(names):
    return [
        {"name": n, "bat": b, "bowl": bo, "archetype": a, "role": r}
        for n, (b, bo, a, r) in zip(names, _EQUAL_TEMPLATE)
    ]
TEAMS_DATA = {
    "Team 1": {"name": "The Protagonists", "players": _build_equal_xi(_PROTAGONIST_NAMES)},
    "Team 2": {"name": "The Rivals",       "players": _build_equal_xi(_RIVAL_NAMES)},
}


# Player-test helpers (cv testplayer) - build two balanced XIs whose OVR is scaled
#    relative to the tested players, so a single player can be watched in a fair (or
#    deliberately easy/hard) context. The balanced template above is recentred to a
# target OVR; role structure + internal spread are preserved.
_TEST_WK_SLOT = (85, 10, "Finisher", "Batter_WK")

def _clamp_rt(x):
    return max(20, min(99, int(round(x))))

def _scaled_xi_from_template(slots, name_prefix, start_idx, target_ovr):
    """Build filler/opponent players from balanced template `slots`, recentred so the
    XI's average OVR ≈ target_ovr (primary skill shifted fully, secondary half)."""
    base = [_player_overall({"bat": b, "bowl": bo, "role": r}) for (b, bo, a, r) in _EQUAL_TEMPLATE]
    delta = target_ovr - (sum(base) / len(base))
    out = []
    for j, (b, bo, a, r) in enumerate(slots):
        if r.startswith("All-Rounder"):
            nb, nbo = b + delta, bo + delta
        elif r.startswith("Bowler"):
            nb, nbo = b + delta * 0.5, bo + delta
        else:
            nb, nbo = b + delta, bo + delta * 0.5
        out.append({"name": f"{name_prefix} {start_idx + j + 1}", "bat": _clamp_rt(nb),
                    "bowl": _clamp_rt(nbo), "archetype": a, "role": r})
    return out

def build_test_home_xi(tested, target_ovr, prefix="Home Net"):
    """Tested players (their REAL ratings) + balanced fillers scaled to target_ovr,
    completing a legal XI (keeper guaranteed)."""
    core = [dict(p) for p in tested][:11]
    need = 11 - len(core)
    slots = [_EQUAL_TEMPLATE[(len(core) + i) % len(_EQUAL_TEMPLATE)] for i in range(need)]
    fillers = _scaled_xi_from_template(slots, prefix, len(core), target_ovr) if need else []
    xi = core + fillers
    if not _has_wk(xi):
        wk = _scaled_xi_from_template([_TEST_WK_SLOT], prefix, 0, target_ovr)[0]
        wk["name"] = f"{prefix} Keeper"
        if fillers:
            xi[-1] = wk
        elif len(xi) < 11:
            xi.append(wk)
        else:
            # full tested XI, no keeper - hand the gloves to the best pure batter
            cand = max((p for p in xi if (p.get("role") or "").startswith("Batter")),
                       key=lambda p: p.get("bat", 0), default=None)
            if cand:
                cand["role"] = "Batter_WK"
    return xi[:11]

def build_test_away_xi(target_ovr):
    """A full balanced opposition XI recentred to target_ovr."""
    return _scaled_xi_from_template(list(_EQUAL_TEMPLATE), "Net", 0, target_ovr)

# difficulty -> OVR offset applied to every non-tested player, relative to the tested avg
_TEST_DIFFICULTY = {"weak": -8, "balanced": 0, "tough": +8}


def _apply_order_pins(xi, pins):
    """Re-seat pinned players ("Virat Kohli 3" -> position 3) inside an engine-ordered
    XI. Unpinned players keep their engine order around the pins. `pins` maps player
    name -> 1-based position; names not in this XI are ignored (split mode)."""
    mine = {n: pos for n, pos in pins.items() if any(p["name"] == n for p in xi)}
    if not mine:
        return xi
    rest = [p for p in xi if p["name"] not in mine]
    out = rest
    for name, pos in sorted(mine.items(), key=lambda kv: kv[1]):
        player = next(p for p in xi if p["name"] == name)
        out.insert(min(max(pos - 1, 0), len(out)), player)
    return out


class _TestDifficultyView(discord.ui.View):
    """Weak / Balanced / Tough picker for cv testplayer. Sets .value then stops."""
    def __init__(self, owner_id):
        super().__init__(timeout=90)
        self.owner_id = owner_id
        self.value = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("❌ Only the tester can choose.", ephemeral=True)
            return False
        return True

    async def _pick(self, interaction, value, label):
        self.value = value
        await interaction.response.edit_message(content=f"⚔️ Opposition strength: **{label}**", view=None)
        self.stop()

    @discord.ui.button(label="Weak", style=discord.ButtonStyle.success, emoji="🟢")
    async def btn_weak(self, interaction, button):
        await self._pick(interaction, "weak", "Weak (lower OVR)")

    @discord.ui.button(label="Balanced", style=discord.ButtonStyle.primary, emoji="🟡")
    async def btn_balanced(self, interaction, button):
        await self._pick(interaction, "balanced", "Balanced (same OVR)")

    @discord.ui.button(label="Tough", style=discord.ButtonStyle.danger, emoji="🔴")
    async def btn_tough(self, interaction, button):
        await self._pick(interaction, "tough", "Tough (higher OVR)")


class _TestFormatView(discord.ui.View):
    """T20 / ODI picker for cv testplayer. Sets .value (overs) then stops."""
    def __init__(self, owner_id):
        super().__init__(timeout=90)
        self.owner_id = owner_id
        self.value = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("❌ Only the tester can choose.", ephemeral=True)
            return False
        return True

    async def _pick(self, interaction, overs, label):
        self.value = overs
        await interaction.response.edit_message(content=f"🏏 Format: **{label}**", view=None)
        self.stop()

    @discord.ui.button(label="T20 (20 overs)", style=discord.ButtonStyle.primary)
    async def btn_t20(self, interaction, button):
        await self._pick(interaction, 20, "T20")

    @discord.ui.button(label="ODI (50 overs)", style=discord.ButtonStyle.secondary)
    async def btn_odi(self, interaction, button):
        await self._pick(interaction, 50, "ODI")

class BatterStats:
    def __init__(self, profile):
        self.profile = profile
        self.runs_scored = 0
        self.balls_faced = 0
        self.dismissal = "not out"
        self.fours = 0
        self.sixes = 0
        self.form_factor = random.uniform(0.96, 1.04) # Smoothed out to prevent massive RNG blowouts

class BowlerStats:
    def __init__(self, profile):
        self.profile = profile
        self.runs_conceded = 0
        self.balls_bowled = 0
        self.wickets_taken = 0
        self.form_factor = random.uniform(0.96, 1.04)

class InningsState:
    def __init__(self, batting_team, bowling_team):
        self.batting_team = batting_team
        self.bowling_team = bowling_team
        self.total_runs = 0
        self.wickets = 0
        self.total_balls = 0
        self.over_log = []
        self.partnership_runs = 0
        self.extras = 0
        self.last_ball_boundary = False
        
        self.current_striker_idx = 0
        self.current_non_striker_idx = 1
        self.next_batter_idx = 2
        self.current_bowler = None
        
        self.batting_stats = {p["name"]: BatterStats(p) for p in batting_team["players"]}
        self.bowling_stats = {p["name"]: BowlerStats(p) for p in bowling_team["players"]}

class CricketMatch:
    def __init__(self, p1, p2, p1_id, p2_id, team1, team2, format_overs=20, pitch="Flat", weather="Clear"):
        self.p1 = p1
        self.p2 = p2
        self.p1_id = p1_id
        self.p2_id = p2_id
        self.team1 = team1
        self.team2 = team2
        self.t1_subs = team1.get("subs", [])
        self.t2_subs = team2.get("subs", [])
        self.t1_impact_used = False
        self.t2_impact_used = False
        self.format_overs = format_overs
        self.max_balls = format_overs * 6
        self.pitch = pitch
        self.weather = weather
        
        self.is_ai_game = (p2_id is None)
        self.toss_winner = None
        self.batting_first_id = None
        self.bowling_first_id = None
        
        self.innings1 = None
        self.innings2 = None
        self.current_innings_num = 1
        self.current_innings = None
        
        self.simulation_mode = "interactive"
        self.current_delivery_selection = None
        self.current_shot_selection = None
        self.temp_variation = None
        self.last_commentary = "Match is initializing..."
        self.impact_player = False   # T20 impact player rule flag
        self._pending_bowler = None  # bowler selected before over hub, applied when over starts

    def get_striker_user_id(self):
        if self.current_innings_num == 1:
            return self.batting_first_id
        return self.bowling_first_id

    def get_bowler_user_id(self):
        if self.current_innings_num == 1:
            return self.bowling_first_id
        return self.batting_first_id


def _match_max_wickets(match):
    """Wickets that end an innings. Default 10 (full XI); super-overs use 2; club
    matches set `max_wickets` = squad size so the last batter bats alone, then it's all out."""
    if getattr(match, 'is_super_over', False):
        return 2
    return getattr(match, 'max_wickets', 10)


class ClubMatch(CricketMatch):
    """Career club match: per-player turn control. `get_striker_user_id` /
    `get_bowler_user_id` resolve to the OWNER of the player currently batting / bowling
    (so each player clicks their own action), while the captains drive the SELECTION
    steps (openers, next batter, bowler) via the *_captain_id helpers."""
    def get_striker_user_id(self):
        inn = self.current_innings
        try:
            owner = inn.batting_team["players"][inn.current_striker_idx].get("owner_id")
            if owner is not None:
                return owner
        except Exception:
            pass
        return super().get_striker_user_id()

    def get_bowler_user_id(self):
        inn = self.current_innings
        try:
            if inn and inn.current_bowler and inn.current_bowler.get("owner_id") is not None:
                return inn.current_bowler["owner_id"]
        except Exception:
            pass
        return super().get_bowler_user_id()

    def _cap_of(self, team):
        return getattr(self, "_caps", {}).get(team["name"]) if team else None

    def batting_captain_id(self):
        return self._cap_of(self.current_innings.batting_team) if self.current_innings else None

    def bowling_captain_id(self):
        return self._cap_of(self.current_innings.bowling_team) if self.current_innings else None


def _is_bot_uid(uid):
    """Club bots use negative pseudo-ids so no real Discord user can act for them."""
    return uid is not None and uid < 0

def _striker_is_bot(match):
    try:
        inn = match.current_innings
        return bool(inn.batting_team["players"][inn.current_striker_idx].get("is_bot"))
    except Exception:
        return False

def _bowler_is_bot(match):
    try:
        return bool(match.current_innings.current_bowler.get("is_bot"))
    except Exception:
        return False

def _is_career_match(match):
    """Career-mode match (club / debut / scenario) - fully interactive, no Auto-Ball / Sim."""
    return bool(getattr(match, "is_club", False) or getattr(match, "is_debut", False)
                or getattr(match, "is_scenario", False))

# ---- Simulation routing engine ----

def swap_impact_player(match: CricketMatch, team_id: int, out_name: str, in_player: dict):
    if team_id == 1:
        match.t1_impact_used = True
        match.t1_impact_sub_name = in_player["name"]
        team = match.team1
    else:
        match.t2_impact_used = True
        match.t2_impact_sub_name = in_player["name"]
        team = match.team2

    inn = match.current_innings
    if not inn:
        return

    is_batting = (inn.batting_team["name"] == team["name"])

    if is_batting:
        if in_player["name"] not in inn.batting_stats:
            insert_pos = getattr(inn, "next_batter_idx", len(inn.batting_team["players"]))
            inn.batting_team["players"].insert(insert_pos, in_player)
            inn.batting_stats[in_player["name"]] = BatterStats(in_player)

        b_stats = inn.batting_stats.get(out_name)
        if b_stats:
            if b_stats.dismissal == "not out" and b_stats.balls_faced == 0:
                b_stats.dismissal = "Subbed Out"
            elif b_stats.dismissal == "not out":
                b_stats.dismissal = "Retired (Sub)"
    else:
        if in_player["name"] not in inn.bowling_stats:
            if in_player not in inn.bowling_team["players"]:
                inn.bowling_team["players"].append(in_player)
            inn.bowling_stats[in_player["name"]] = BowlerStats(in_player)

        bw_stats = inn.bowling_stats.get(out_name)
        if bw_stats:
            bw_stats.is_subbed_out = True

def _do_impact_swap(match: CricketMatch, team_num: int, out_name: str, in_player: dict):
    swap_impact_player(match, team_num, out_name, in_player)
    team   = match.team1 if team_num == 1 else match.team2
    msg = (
        f"🔄 **AI TACTIC:** {team['name']} uses IMPACT PLAYER! "
        f"**{in_player['name']}** IN for **{out_name}**!"
    )
    # Keep the rolling prefix for any path that reads it, AND return the line so
    # the verbose sim loops can actually surface the swap to the channel.
    match.last_commentary_prefix = msg + "\n" + getattr(match, "last_commentary_prefix", "")
    return msg

def _ai_batting_impact(match: CricketMatch, innings: InningsState, team_num: int, subs):
    overs   = innings.total_balls // 6
    wkts    = innings.wickets
    max_b   = match.max_balls
    is_inn1 = (match.current_innings_num == 1)

    bat_subs = [s for s in subs if "Batter" in s["role"] or "All-Rounder" in s["role"]] or subs
    best_sub = max(bat_subs, key=lambda x: x["bat"])

    players  = innings.batting_team["players"]
    upcoming = [
        p for p in players[innings.next_batter_idx:]
        if innings.batting_stats[p["name"]].dismissal == "not out"
    ]
    if not upcoming:
        return

    # Batting first: never sacrifice a pure bowler - you'll need them in inn2
    if is_inn1:
        swappable = [p for p in upcoming if "Bowler" not in p["role"]]
        if not swappable:
            return
    else:
        swappable = upcoming

    next_up  = swappable[0]
    worst_up = min(swappable, key=lambda x: x["bat"])

    # Guarantee (batting second only): next batter is tail - sub them out before they walk in
    if not is_inn1 and next_up["bat"] < 60 and best_sub["bat"] > next_up["bat"] + 10:
        return _do_impact_swap(match, team_num, next_up["name"], best_sub)

    # Powerplay crisis: 2+ wickets before over 6
    if wkts >= 2 and overs < 6 and best_sub["bat"] >= 72:
        if best_sub["bat"] > worst_up["bat"] + 12:
            return _do_impact_swap(match, team_num, worst_up["name"], best_sub)

    # Mid-innings wicket cluster: 3+ wickets after over 5, not in last 3 overs
    if wkts >= 3 and overs >= 5 and innings.total_balls < max_b - 18:
        if best_sub["bat"] > worst_up["bat"] + 10:
            return _do_impact_swap(match, team_num, worst_up["name"], best_sub)

    # Chase mode (batting second): RRR >= 9, bring in firepower
    if not is_inn1:
        balls_left = max_b - innings.total_balls
        if balls_left > 0:
            target = getattr(match, "target", match.innings1.total_runs + 1)
            rrr = (target - innings.total_runs) / balls_left * 6
            if rrr >= 9 and best_sub["bat"] >= 75 and best_sub["bat"] > worst_up["bat"] + 8:
                _do_impact_swap(match, team_num, worst_up["name"], best_sub)
                return

    # Late guarantee: last 4 overs and sub still unused - don't waste the slot
    if innings.total_balls >= max_b - 24 and best_sub["bat"] > worst_up["bat"] + 8:
        return _do_impact_swap(match, team_num, worst_up["name"], best_sub)

def _ai_bowling_impact(match: CricketMatch, innings: InningsState, team_num: int, subs):
    balls = innings.total_balls
    max_b = match.max_balls

    bowl_subs = [s for s in subs if "Bowler" in s["role"] or "All-Rounder" in s["role"]] or subs
    best_sub  = max(bowl_subs, key=lambda x: x["bowl"])

    curr  = innings.current_bowler
    cands = [p for p in innings.bowling_team["players"]
             if not curr or p["name"] != curr["name"]]
    if not cands:
        return

    worst = min(cands, key=lambda x: x["bowl"])
    if best_sub["bowl"] <= worst["bowl"] + 8:
        return

    # Death overs: last 5 overs
    if balls >= max_b - 30:
        return _do_impact_swap(match, team_num, worst["name"], best_sub)

    # 2nd innings, opponent cruising (low RRR) - use from last 6 overs
    if match.current_innings_num == 2 and balls >= max_b - 36:
        balls_left = max_b - balls
        if balls_left > 0:
            target = getattr(match, "target", match.innings1.total_runs + 1)
            rrr = (target - innings.total_runs) / balls_left * 6
            if rrr < 7:
                return _do_impact_swap(match, team_num, worst["name"], best_sub)

    # Absolute guarantee: last 2 overs, don't leave sub unused
    if balls >= max_b - 12:
        return _do_impact_swap(match, team_num, worst["name"], best_sub)

def try_ai_impact_player(match: CricketMatch, innings: InningsState):
    """Let the AI use its Impact Player at an over boundary. Returns a list of
    announcement lines for any swaps made this call, so verbose sim loops can
    surface the move to the channel (callers may ignore the return value)."""
    announcements = []
    if not getattr(match, "impact_player", False): return announcements
    if not match.is_ai_game: return announcements

    for team_num in (1, 2):
        if getattr(match, f"t{team_num}_impact_used", False): continue
        team = match.team1 if team_num == 1 else match.team2
        subs = getattr(match, f"t{team_num}_subs", [])
        if not subs: continue

        if innings.batting_team["name"] == team["name"]:
            msg = _ai_batting_impact(match, innings, team_num, subs)
        else:
            msg = _ai_bowling_impact(match, innings, team_num, subs)
        if msg:
            announcements.append(msg)

    return announcements

# Engine choice is by MATCH LENGTH, not an exact 50-over check: a DLS-reduced ODI
# (e.g. 48 or 40 overs) must KEEP ODI pacing - only a genuinely short match plays
# T20-style. The ODI engine scales its phases (powerplay/death) to max_balls.
_ODI_ENGINE_MIN_OVERS = 35

def get_smart_ai_bowler(innings, pitch, weather="Clear", format_overs=20):
    if format_overs >= _ODI_ENGINE_MIN_OVERS:
        return get_smart_ai_bowler_odi(innings, pitch, weather, format_overs)
    return get_smart_ai_bowler_t20(innings, pitch, weather, format_overs)

def execute_ball_math(match: CricketMatch):
    if match.format_overs >= _ODI_ENGINE_MIN_OVERS:
        return execute_ball_math_odi(match)
    return execute_ball_math_t20(match)

def _run_full_match_sync(match: CricketMatch):
    """Simulate a complete T20/ODI match synchronously (no Discord messages)."""
    # Headless sims must run in "whole_match" mode so the ball engine auto-promotes
    # the next batter on a wicket. In the default "interactive" mode a fallen wicket
    # only sets pending_next_batter (awaiting a UI pick), so nobody past the opening
    # pair ever bats - leaving only the top 2 batters with runs in stats/scorecard.
    match.simulation_mode = "whole_match"

    def _sim_innings(innings):
        while True:
            if innings.wickets >= 10 or innings.total_balls >= match.max_balls:
                break
            if match.current_innings_num == 2 and innings.total_runs >= getattr(match, "target", innings.total_runs + 1):
                break
            if innings.total_balls % 6 == 0 and not innings.over_log:
                bowler = get_smart_ai_bowler(innings, match.pitch, match.weather, match.format_overs)
                if not bowler:
                    break
                innings.current_bowler = bowler
            tb_before = innings.total_balls
            execute_ball_math(match)
            if innings.total_balls > tb_before and innings.total_balls % 6 == 0:
                innings.over_log.clear()
                innings.bouncers_in_over = 0
                innings.cutters_in_over = 0
                innings.mystery_bowled_this_over = False

    match.current_innings = match.innings1
    match.current_innings_num = 1
    _sim_innings(match.innings1)

    match.target = match.innings1.total_runs + 1
    match.innings2 = InningsState(match.innings1.bowling_team, match.innings1.batting_team)
    match.current_innings = match.innings2
    match.current_innings_num = 2
    _sim_innings(match.innings2)

    # ACL has no ties - a tied ACL sim is decided by a Super Over. Other formats keep their
    # existing behavior (round-robin league ties share a point; knockouts fall back to team1),
    # so round-robin is completely unaffected.
    if (getattr(match, "tournament_type", None) == "acl"
            and match.innings2.total_runs == match.innings1.total_runs
            and not getattr(match, "is_super_over", False)):
        try:
            _sim_super_over(match)
        except Exception as _so_err:
            print(f"Sim super over failed, leaving tie for fallback: {_so_err}")

    # Headless sims never reach handle_innings_end, so global stats are folded in here.
    try:
        gstats.record_limited_overs_match(match)
    except Exception as _gs_err:
        print(f"Global stats record failed (sim): {_gs_err}")


def _sim_super_over(match: CricketMatch):
    """Headless: break a tie via simulated super over(s); sets match.tiebreak_winner_name.
    Team that batted 2nd bats first; max 2 wickets / 6 balls; replays (swapping order) if tied again."""
    bat_team = match.innings2.batting_team   # batted 2nd in the main match -> bats first in the SO
    bowl_team = match.innings1.batting_team
    for _ in range(25):  # safety cap against pathological repeated ties
        so = CricketMatch(match.p1, match.p2, match.p1_id, match.p2_id,
                          match.team1, match.team2, format_overs=1,
                          pitch=match.pitch, weather=match.weather)
        so.is_super_over = True
        so.sim_only = True
        so.simulation_mode = "whole_match"   # auto-promote the next batter on a wicket
        so.max_balls = 6

        def _so_innings(inn, chasing):
            so.current_innings = inn
            attempts = 0
            while inn.wickets < 2 and inn.total_balls < 6 and attempts < 120:
                attempts += 1  # hard cap guards against a pathological run of wides/no-balls
                if chasing and inn.total_runs >= so.target:
                    break
                if inn.total_balls % 6 == 0 and not inn.over_log:
                    b = get_smart_ai_bowler(inn, so.pitch, so.weather, so.format_overs)
                    if b:
                        inn.current_bowler = b
                execute_ball_math(so)

        so.innings1 = InningsState(bat_team, bowl_team)
        so.current_innings_num = 1
        _so_innings(so.innings1, False)
        so.target = so.innings1.total_runs + 1
        so.innings2 = InningsState(bowl_team, bat_team)
        so.current_innings_num = 2
        _so_innings(so.innings2, True)

        r1, r2 = so.innings1.total_runs, so.innings2.total_runs
        if r1 != r2:
            match.tiebreak_winner_name = bat_team["name"] if r1 > r2 else bowl_team["name"]
            return
        bat_team, bowl_team = bowl_team, bat_team  # still tied - swap who bats first and replay

# ---- Embed scoreboards & pil graphics ----

def render_wicket_summary(match: CricketMatch) -> discord.Embed:
    p = match.out_batter_profile
    stats = match.current_innings.batting_stats[p["name"]]
    sr = (stats.runs_scored / stats.balls_faced * 100) if stats.balls_faced > 0 else 0.0
    
    embed = discord.Embed(title=f"🏏 WICKET! {p['name']} is Out!", color=discord.Color.red())
    embed.add_field(name="Score", value=f"**{stats.runs_scored}** ({stats.balls_faced} balls)", inline=True)
    embed.add_field(name="Strike Rate", value=f"**{sr:.1f}**", inline=True)
    embed.add_field(name="Dismissal", value=f"**{stats.dismissal}**", inline=False)
    return embed

def get_player_of_the_match(match: CricketMatch) -> str:
    best_player = "TBD"
    highest_impact = -999

    # Format-aware anchors. Judged by the T20 pars (SR 120, econ 10) every ODI
    # spell looked golden - 10 overs at 5.0 econ banked ~150 pts, more than a
    # century - and normal ODI strike rates were taxed. ODI par: SR ~95, econ ~5.8.
    # ODI economy is BOOST-ONLY (Jaiv): a tight spell adds points, an expensive one
    # never subtracts from the wickets taken. T20 keeps its −30 economy floor.
    _odi = match.format_overs >= _ODI_ENGINE_MIN_OVERS
    _sr_par = 95.0 if _odi else 120.0
    _eco_par, _eco_rate = (5.8, 2.0) if _odi else (10.0, 3.0)
    _eco_floor = 0.0 if _odi else -30.0

    winning_team = None
    if match.current_innings_num == 2 and match.innings2:
        if match.innings2.total_runs > match.innings1.total_runs:
            winning_team = match.innings2.batting_team["name"]
        elif match.innings1.total_runs > match.innings2.total_runs:
            winning_team = match.innings1.batting_team["name"]
            
    all_players = match.team1["players"] + match.team2["players"]
    
    for p in all_players:
        p_name = p["name"]
        impact = 0
        
        # Analyze innings 1 impact
        if p_name in match.innings1.batting_stats:
            bat = match.innings1.batting_stats[p_name]
            sr = (bat.runs_scored / bat.balls_faced * 100) if bat.balls_faced > 0 else 0
            impact += bat.runs_scored + (bat.runs_scored * (sr / _sr_par))
            
        if p_name in match.innings1.bowling_stats:
            bowl = match.innings1.bowling_stats[p_name]
            if bowl.balls_bowled > 0:
                eco = (bowl.runs_conceded / bowl.balls_bowled) * 6
                eco_pts = max(_eco_floor, (_eco_par - eco) * (bowl.balls_bowled / 6) * _eco_rate)
                impact += (bowl.wickets_taken * 40) + eco_pts

        # Analyze innings 2 impact
        if match.current_innings_num == 2 and match.innings2:
            if p_name in match.innings2.batting_stats:
                bat = match.innings2.batting_stats[p_name]
                sr = (bat.runs_scored / bat.balls_faced * 100) if bat.balls_faced > 0 else 0
                impact += bat.runs_scored + (bat.runs_scored * (sr / _sr_par))

            if p_name in match.innings2.bowling_stats:
                bowl = match.innings2.bowling_stats[p_name]
                if bowl.balls_bowled > 0:
                    eco = (bowl.runs_conceded / bowl.balls_bowled) * 6
                    eco_pts = max(_eco_floor, (_eco_par - eco) * (bowl.balls_bowled / 6) * _eco_rate)
                    impact += (bowl.wickets_taken * 40) + eco_pts
        
        # Determine team for multiplier
        if p in match.team1["players"]:
            team_name = match.team1["name"]
        else:
            team_name = match.team2["name"]
            
        if team_name == winning_team:
            impact *= 1.5
            
        if impact > highest_impact:
            highest_impact = impact
            best_player = p_name
            
    return best_player

def render_embed_scoreboard(match: CricketMatch) -> discord.Embed:
    innings = match.current_innings
    overs = f"{innings.total_balls // 6}.{innings.total_balls % 6}"
    embed = discord.Embed(color=0x2B2D31) # Sleek Dark Mode Discord Color
    embed = discord.Embed(color=0xFFFFFF) # Crisp White Embed Color
    
    desc = "**<a:ball:1510370830163640320> LIVE SCOREBOARD**\n"

    if match.current_innings_num == 1:
        t1_name = innings.batting_team['name']
        t2_name = innings.bowling_team['name']
        desc += f"### 🏏 {t1_name}  {innings.total_runs}/{innings.wickets}  ({overs}/{match.format_overs}.0)\n"
        desc += f"**{t2_name}**  Yet to Bat\n"
    else:
        t1_name = match.innings2.batting_team['name']
        t2_name = match.innings1.batting_team['name']
        t1_overs = f"{match.innings1.total_balls // 6}.{match.innings1.total_balls % 6}"
        desc += f"### 🏏 {t1_name}  {innings.total_runs}/{innings.wickets}  ({overs}/{match.format_overs}.0)\n"
        desc += f"### {t2_name}  {match.innings1.total_runs}/{match.innings1.wickets}  ({t1_overs}/{match.format_overs}.0)\n"

    # Inline Codeblock Grid (Tight boxes matching the font size perfectly)
    desc += f"**`{'BATTER':<16}{'R':<5}{'B':<5}{'SR':<6}`**\n"
    for idx, p_item in enumerate(innings.batting_team["players"][:innings.next_batter_idx]):
        stats = innings.batting_stats[p_item["name"]]
        if stats.dismissal == "not out":
            is_stk = "*" if idx == innings.current_striker_idx else ""
            
            sr = (stats.runs_scored / stats.balls_faced * 100) if stats.balls_faced > 0 else 0.0
            desc += f"`{p_item['name'][:14]:<14}{is_stk:<2}{stats.runs_scored:<5}{stats.balls_faced:<5}{sr:<6.1f}`\n"

    crr = (innings.total_runs / innings.total_balls * 6) if innings.total_balls > 0 else 0.0
    if match.current_innings_num == 2:
        target = getattr(match, "target", match.innings1.total_runs + 1)
        runs_needed = target - innings.total_runs
        balls_left = match.max_balls - innings.total_balls
        rrr = (runs_needed / balls_left * 6) if balls_left > 0 else 0.0
        stats_line = f"`P'Ship: {innings.partnership_runs}  CRR: {crr:.1f}  RRR: {rrr:.1f}`"
    else:
        proj = int(crr * match.format_overs)
        stats_line = f"`P'Ship: {innings.partnership_runs}  CRR: {crr:.1f}  Proj: {proj}`"

    desc += f"\n{stats_line}\n\n"

    
    # Inline Codeblock for Bowlers
    desc += f"**`{'BOWLER':<17}{'O':<5}{'R':<5}{'W':<5}`**\n"
    if innings.current_bowler:
        cb = innings.current_bowler
        cbs = innings.bowling_stats[cb["name"]]
        bovers = f"{cbs.balls_bowled // 6}.{cbs.balls_bowled % 6}"
        desc += f"`{cb['name'][:16]:<17}{bovers:<5}{cbs.runs_conceded:<5}{cbs.wickets_taken:<5}`\n"
        
    timeline_raw = innings.over_log if innings.over_log else []
    timeline_str = " ".join(timeline_raw) if timeline_raw else "Starting over..."
    
    desc += f"**Timeline**\n{timeline_str}\n"
    
    balls_left = match.max_balls - innings.total_balls
    if getattr(match, "is_scenario", False):
        if getattr(match, "scenario_mode", "bat") == "bowl":
            tgt = getattr(match, "scenario_wkt_target", 0)
            ws = innings.bowling_stats.get(getattr(match, "scenario_player_name", ""))
            got = ws.wickets_taken if ws else 0
            desc += f"-# 🎳 Take **{tgt}** wickets — you have **{got}/{tgt}** ({balls_left} balls left)"
        else:
            tgt = getattr(match, "scenario_target", 0)
            need = max(0, tgt - innings.total_runs)
            desc += (f"-# 🎯 Chasing **{tgt}** — need **{need}** off {balls_left} balls"
                     if need > 0 else f"-# 🏆 Target {tgt} reached!")
    elif getattr(match, "is_debut", False):
        need = max(0, _DEBUT_TARGET - innings.total_runs)
        desc += (f"-# 🎯 Pass mark **{_DEBUT_TARGET}** team runs — **{need}** to go ({balls_left} balls left)"
                 if need > 0 else f"-# ✅ Pass mark {_DEBUT_TARGET} reached!")
    elif match.current_innings_num == 2:
        target = getattr(match, "target", match.innings1.total_runs + 1)
        target_needed = target - innings.total_runs
        if target_needed > 0 and balls_left > 0:
            dls_txt = " (DLS)" if getattr(match, "dls_active", False) else ""
            desc += f"-# Equation: Need {target_needed} runs from {balls_left} balls{dls_txt}"
    else:
        if match.toss_winner:
            toss_winner_name = match.team1['name'] if match.toss_winner == match.p1_id else match.team2['name']
            decision = "bat" if match.batting_first_id == match.toss_winner else "bowl"
            desc += f"-# 🪙 {toss_winner_name} won the toss and chose to {decision} first"
            
    embed.description = desc
            
    return embed

def render_full_scorecard_embed(match: CricketMatch, innings_num: int) -> discord.Embed:
    innings = match.innings1 if innings_num == 1 else match.innings2
    overs = f"{innings.total_balls // 6}.{innings.total_balls % 6}"
    
    embed = discord.Embed(title=f"📋 Full Scorecard: {innings.batting_team['name']}", color=discord.Color.gold())
    
    # Show POTM if the match is completely over
    potm_str = ""
    if innings_num == 2:
        potm = get_player_of_the_match(match)
        potm_str = f"⭐ **Player of the Match:** {potm}\n\n"
        
    embed.description = f"{potm_str}**Total Score:** {innings.total_runs}/{innings.wickets} in {overs} Overs\n"
    
    b_text = "```text\nBATTER                  R    B    SR\n"
    for p in innings.batting_team["players"]:
        stats = innings.batting_stats[p["name"]]
        if stats.balls_faced > 0 or stats.dismissal != "not out":
            sr = (stats.runs_scored / stats.balls_faced * 100) if stats.balls_faced > 0 else 0.0
            status = "not out" if stats.dismissal == "not out" else stats.dismissal
            b_text += f"{p['name'][:18]:<24}{stats.runs_scored:<5}{stats.balls_faced:<5}{sr:<5.1f}\n"
            b_text += f"  └ {status}\n"
    b_text += "```"
    
    bw_text = "```text\nBOWLER                  O    R    W    ECO\n"
    for p in innings.bowling_team["players"]:
        stats = innings.bowling_stats[p["name"]]
        if stats.balls_bowled > 0:
            o = f"{stats.balls_bowled // 6}.{stats.balls_bowled % 6}"
            eco = (stats.runs_conceded / stats.balls_bowled * 6) if stats.balls_bowled > 0 else 0.0
            bw_text += f"{p['name'][:18]:<24}{o:<5}{stats.runs_conceded:<5}{stats.wickets_taken:<5}{eco:<5.1f}\n"
    bw_text += "```"
    
    embed.add_field(name="Batting", value=b_text, inline=False)
    embed.add_field(name="Bowling", value=bw_text, inline=False)
    
    return embed

def generate_final_score_image(match: CricketMatch) -> io.BytesIO:
    # 1200x850 Symmetrical Grid Canvas
    img = Image.new("RGB", (1200, 850), color="#FFFFFF") 
    d = ImageDraw.Draw(img)

    try:
        font_large = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 48)
        font_title = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 32)
        font_bold = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 24)
        font_small = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 20)
        font_micro = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 13)
    except:
        font_large = ImageFont.load_default()
        font_title = ImageFont.load_default()
        font_bold = ImageFont.load_default()
        font_small = ImageFont.load_default()
        font_micro = ImageFont.load_default()

    # Helper to get text width dynamically to align elements
    def get_tw(text, font):
        if hasattr(font, 'getbbox'):
            return font.getbbox(text)[2]
        elif hasattr(d, 'textsize'):
            return d.textsize(text, font=font)[0]
        else:
            return len(text) * 12

    def get_th(text, font):
        if hasattr(font, 'getbbox'):
            bb = font.getbbox(text)
            return bb[3] - bb[1]
        return 20

    potm_name = get_player_of_the_match(match) if match.current_innings_num == 2 else ""
    
    c_white = "#FFFFFF"
    c_score_bg = "#FFFFFF"
    _base_overs = getattr(match, 'original_format_overs', match.format_overs)
    if getattr(match, 'is_super_over', False):
        c_accent = "#FFD700" # Gold for Super Over
    elif _base_overs == 50:
        c_accent = "#39B54A" # Green for ODI
    elif _base_overs == 20:
        c_accent = "#F97316" # Orange for T20
    else:
        c_accent = "#00B4D8" # Cyan for Custom
    c_navy = "#0A0F24"   # Deep Navy Blue
    c_grid = "#E8E8E8"   # Faint Light Grey
    c_ball = c_accent    # Adaptive color based on format
    c_text_grey = "#777777"

    # Impact Player markers - names of players who came on as an impact sub
    impact_subs = {
        n for n in (getattr(match, "t1_impact_sub_name", None),
                    getattr(match, "t2_impact_sub_name", None)) if n
    }

    def draw_ip_badge(bx, name_y):
        """Small 'IP' badge marking a player who came on as an impact sub.
        Vertically centered on the name; returns the x-advance (badge width + gap)."""
        bw_px = get_tw("IP", font_small) + 8
        bh_px = get_th("IP", font_small) + 6
        by = name_y + (get_th("A", font_bold) - bh_px) // 2
        d.rounded_rectangle([(bx, by), (bx + bw_px, by + bh_px)], radius=3, fill=c_accent)
        d.text((bx + 4, by + 3), "IP", fill=c_white, font=font_small)
        return bw_px + 6

    # ---- Core layout & bars ----
    
    # Green Match Type Bar
    d.rectangle([(0, 110), (1200, 140)], fill=c_accent)
    
    # Upper Header (Batting Scores)
    d.rectangle([(0, 140), (1200, 220)], fill=c_navy)
    d.line([(600, 140), (600, 220)], fill=c_white, width=1) # Center Divider
    
    # Lower Header (Bowling)
    d.rectangle([(0, 470), (1200, 550)], fill=c_navy)
    d.line([(600, 470), (600, 550)], fill=c_white, width=1) # Center Divider

    # Footer
    d.rectangle([(0, 800), (1200, 850)], fill=c_accent)
    
    # ---- Grid system ----
    
    # Grid Backgrounds
    d.rectangle([(0, 220), (1200, 470)], fill=c_score_bg)
    d.rectangle([(0, 550), (1200, 800)], fill=c_score_bg)
    
    # Vertical Column Lines (Spanning both Upper and Lower Grids)
    for y_start, y_end in [(220, 470), (550, 800)]:
        d.line([(600, y_start), (600, y_end)], fill=c_grid, width=2) # Center line
        d.line([(420, y_start), (420, y_end)], fill=c_grid, width=2) # Left Col 1-2 border
        d.line([(510, y_start), (510, y_end)], fill=c_grid, width=2) # Left Col 2-3 border
        
        d.line([(960, y_start), (960, y_end)], fill=c_grid, width=2) # Right Col 4-5 border
        d.line([(1050, y_start), (1050, y_end)], fill=c_grid, width=2) # Right Col 5-6 border
        
    # Horizontal Row Lines (Upper Grid - Batting)
    for y in range(270, 471, 50): d.line([(0, y), (1200, y)], fill=c_grid, width=1)
        
    # Horizontal Row Lines (Lower Grid - Bowling)
    for y in range(600, 801, 50): d.line([(0, y), (1200, y)], fill=c_grid, width=1)
        
    # ---- Floating UI icons ----
    
    # Upper Icons (Bats)
    # Left box: perfectly flush with left edge, rounded on the right
    d.rounded_rectangle([(0, 140), (60, 220)], radius=15, fill=c_white)
    d.rectangle([(0, 140), (30, 220)], fill=c_white) # Squares off left edge
    # Left Bat
    d.line([(15, 195), (32, 177)], fill=c_accent, width=9) # Blade
    d.line([(32, 177), (40, 169)], fill=c_accent, width=3) # Handle
    d.ellipse([(38, 166), (44, 172)], fill=c_accent)       # Handle Knob
    
    # Right box: perfectly flush with right edge, rounded on the left
    d.rounded_rectangle([(1140, 140), (1200, 220)], radius=15, fill=c_white)
    d.rectangle([(1170, 140), (1200, 220)], fill=c_white) # Squares off right edge
    # Right Bat
    d.line([(1185, 195), (1168, 177)], fill=c_accent, width=9) # Blade
    d.line([(1168, 177), (1160, 169)], fill=c_accent, width=3) # Handle
    d.ellipse([(1156, 166), (1162, 172)], fill=c_accent)       # Handle Knob

    # Lower Icons (Balls)
    # Left box
    d.rounded_rectangle([(0, 470), (60, 550)], radius=15, fill=c_white)
    d.rectangle([(0, 470), (30, 550)], fill=c_white)
    # Left Ball
    d.ellipse([(15, 495), (45, 525)], fill=c_ball)
    d.line([(20, 502), (40, 518)], fill=c_white, width=2)
    d.line([(23, 498), (38, 510)], fill=c_white, width=1)
    d.line([(23, 510), (38, 522)], fill=c_white, width=1)
    
    # Right box
    d.rounded_rectangle([(1140, 470), (1200, 550)], radius=15, fill=c_white)
    d.rectangle([(1170, 470), (1200, 550)], fill=c_white)
    # Right Ball
    d.ellipse([(1155, 495), (1185, 525)], fill=c_ball)
    d.line([(1160, 502), (1180, 518)], fill=c_white, width=2)
    d.line([(1163, 498), (1178, 510)], fill=c_white, width=1)
    d.line([(1163, 510), (1178, 522)], fill=c_white, width=1)

    # ---- Data population ----
    
    # Top White Header (Teams & Logo)
    t1_name = match.innings1.batting_team['name'][:18].upper()
    d.text((300 - get_tw(t1_name, font_large)//2, 30), t1_name, fill=c_navy, font=font_large)

    if match.current_innings_num == 2 and match.innings2:
        t2_name = match.innings2.batting_team['name'][:18].upper()
    else:
        t2_name = match.innings1.bowling_team['name'][:18].upper()
    d.text((900 - get_tw(t2_name, font_large)//2, 30), t2_name, fill=c_navy, font=font_large)

    # Match number - top-right corner, small/unobtrusive
    _base_fmt = "odi" if _base_overs == 50 else "t20"
    _ctr_text = _format_match_no_label(_base_fmt)
    _ctr_w = get_tw(_ctr_text, font_micro)
    d.text((1195 - _ctr_w, 8), _ctr_text, fill=c_text_grey, font=font_micro)

    # Pitch & weather - top-left corner, mirrors the match number
    _cond_text = f"PITCH: {str(getattr(match, 'pitch', 'Flat')).upper()}  •  {str(getattr(match, 'weather', 'Clear')).upper()}"
    d.text((5, 8), _cond_text, fill=c_text_grey, font=font_micro)

    # Center Custom Logo (or Placeholder)
    try:
        logo_path = "assets/logo.png" if os.path.exists("assets/logo.png") else "assets/logo.jpg"
        logo_img = Image.open(logo_path).convert("RGBA")
        logo_img = logo_img.resize((90, 90), Image.Resampling.LANCZOS)
        
        # Create a circular mask to cut the square image
        mask = Image.new("L", (90, 90), 0)
        mask_draw = ImageDraw.Draw(mask)
        mask_draw.ellipse((0, 0, 90, 90), fill=255)
        
        img.paste(logo_img, (555, 10), mask)
        d.ellipse([(555, 10), (645, 100)], outline=c_grid, width=2)
    except:
        d.ellipse([(550, 15), (650, 105)], fill=c_white, outline=c_grid, width=3)
        d.text((600 - get_tw("LOGO", font_bold)//2, 45), "LOGO", fill=c_grid, font=font_bold)

    # Green Bar Match Type (Perfectly Center Aligned)
    if getattr(match, 'is_super_over', False):
        fmt_text = "SUPER OVER"
    else:
        _base = getattr(match, 'original_format_overs', match.format_overs)
        fmt = "ODI" if _base == 50 else "T20" if _base == 20 else "CUSTOM"
        fmt_text = f"{fmt} ({match.format_overs} OVERS)"
        
    left_text = "SIMULATION MATCH"
    dot_text = "•"
    
    d.text((600 - get_tw(dot_text, font_bold)//2, 113), dot_text, fill=c_navy, font=font_bold)
    d.text((585 - get_tw(left_text, font_bold), 113), left_text, fill=c_navy, font=font_bold)
    d.text((615, 113), fmt_text, fill=c_navy, font=font_bold)

    # Upper Navy Headers (Scores Only)
    s1_full = f"{match.innings1.total_runs}-{match.innings1.wickets}"
    d.text((300 - get_tw(s1_full, font_title)//2, 165), s1_full, fill=c_white, font=font_title)

    if match.current_innings_num == 2 and match.innings2:
        s2_full = f"{match.innings2.total_runs}-{match.innings2.wickets}"
    else:
        s2_full = "YET TO BAT"
    d.text((900 - get_tw(s2_full, font_title)//2, 165), s2_full, fill=c_white, font=font_title)

    def draw_batters(inn, offset_x):
        if not inn: return
    
        d.text((offset_x + 75, 235), "BATTER", fill=c_text_grey, font=font_small)
        d.text((offset_x + 465 - get_tw("R", font_small)//2, 235), "R", fill=c_text_grey, font=font_small)
        d.text((offset_x + 555 - get_tw("B", font_small)//2, 235), "B", fill=c_text_grey, font=font_small)
        
        active_batters = [b for b in inn.batting_stats.values() if b.balls_faced > 0 or b.dismissal != "not out"]
        top_b = sorted(active_batters, key=lambda x: x.runs_scored, reverse=True)[:4]
        for idx, b in enumerate(top_b):
            y = 285 + (idx * 50)
            name = b.profile['name'][:16].upper()

            d.text((offset_x + 75, y), name, fill=c_navy, font=font_bold)
            marker_x = offset_x + 75 + get_tw(name, font_bold) + 8

            if b.profile['name'] in impact_subs:
                marker_x += draw_ip_badge(marker_x, y)

            if potm_name == b.profile['name']:
                d.text((marker_x, y - 4), "★", fill="#FFD700", font=font_title)

            runs = str(b.runs_scored)
            if b.dismissal == "not out": runs += "*"
            d.text((offset_x + 465 - get_tw(runs, font_bold)//2, y), runs, fill=c_navy, font=font_bold)
            
            balls = str(b.balls_faced)
            d.text((offset_x + 555 - get_tw(balls, font_small)//2, y + 4), balls, fill=c_text_grey, font=font_small)

    draw_batters(match.innings1, 0) # Team 1 Batting
    
    draw_batters(match.innings2 if match.current_innings_num == 2 else None, 540) # Team 2 Batting

    # Lower Headers (Overs Played)
    o1_text = f"{match.innings1.total_balls // 6}.{match.innings1.total_balls % 6} OVERS"
    d.text((300 - get_tw(o1_text, font_title)//2, 495), o1_text, fill=c_white, font=font_title)

    if match.current_innings_num == 2 and match.innings2:
        o2_text = f"{match.innings2.total_balls // 6}.{match.innings2.total_balls % 6} OVERS"
    else:
        o2_text = "0.0 OVERS"
    d.text((900 - get_tw(o2_text, font_title)//2, 495), o2_text, fill=c_white, font=font_title)
    
    def draw_bowlers(inn, offset_x):
        if not inn: return
        
        d.text((offset_x + 75, 565), "BOWLER", fill=c_text_grey, font=font_small)
        d.text((offset_x + 465 - get_tw("W-R", font_small)//2, 565), "W-R", fill=c_text_grey, font=font_small)
        d.text((offset_x + 555 - get_tw("O", font_small)//2, 565), "O", fill=c_text_grey, font=font_small)
        
        active_bowlers = [b for b in inn.bowling_stats.values() if b.balls_bowled > 0]
        top_bowl = sorted(active_bowlers, key=lambda x: (x.wickets_taken, -x.runs_conceded), reverse=True)[:4]
        for idx, bowl in enumerate(top_bowl):
            y = 615 + (idx * 50)
            name = bowl.profile['name'][:16].upper()

            d.text((offset_x + 75, y), name, fill=c_navy, font=font_bold)
            marker_x = offset_x + 75 + get_tw(name, font_bold) + 8

            if bowl.profile['name'] in impact_subs:
                marker_x += draw_ip_badge(marker_x, y)

            if potm_name == bowl.profile['name']:
                d.text((marker_x, y - 4), "★", fill="#FFD700", font=font_title)

            wr = f"{bowl.wickets_taken}-{bowl.runs_conceded}"
            d.text((offset_x + 465 - get_tw(wr, font_bold)//2, y), wr, fill=c_navy, font=font_bold)
            
            bovers = f"{bowl.balls_bowled // 6}.{bowl.balls_bowled % 6}"
            d.text((offset_x + 555 - get_tw(bovers, font_small)//2, y + 4), bovers, fill=c_text_grey, font=font_small)

    draw_bowlers(match.innings1, 0) # Team 2 Bowling to Team 1
    
    draw_bowlers(match.innings2 if match.current_innings_num == 2 else None, 540) # Team 1 Bowling to Team 2

    if match.current_innings_num == 1:
        result_str = f"TARGET SET: {match.innings1.total_runs + 1} RUNS TO WIN"
    else:
        inn1 = match.innings1
        inn2 = match.innings2
        target = getattr(match, "target", inn1.total_runs + 1)
        max_w = _match_max_wickets(match)
        if inn2.total_runs >= target:
            result_str = f"{inn2.batting_team['name'].upper()} WON BY {max_w - inn2.wickets} WICKETS"
        elif inn2.total_runs == target - 1:
            result_str = "MATCH TIED"
        else:
            result_str = f"{inn1.batting_team['name'].upper()} WON BY {target - inn2.total_runs} RUNS"
            result_str = f"{inn1.batting_team['name'].upper()} WON BY {(target - 1) - inn2.total_runs} RUNS"
            
        if getattr(match, "dls_active", False):
            result_str += " (DLS)"
            
        if potm_name:
            result_str += f" • POTM: {potm_name.upper()}"
            
    d.text((600 - get_tw(result_str, font_title)//2, 810), result_str, fill=c_navy, font=font_title)
    
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    
    return buf

def extract_scoreboard_data(match: CricketMatch) -> dict:
    """Serialize all match display data needed to regenerate the scorecard image later."""
    theme = "Default"
    tourney = None
    if getattr(match, "tournament_server_id", None):
        tourney = next((t for t in DB_CACHE.get("tournaments", []) if t.get("server_id") == match.tournament_server_id), None)
        if tourney: theme = tourney.get("theme", "Default")

    def _team_logo(team_name):
        if not tourney: return None
        t = next((x for x in tourney.get("teams", []) if x["name"] == team_name), None)
        if not t: return None
        return t.get("logo_match") or t.get("logo_standings")

    potm = get_player_of_the_match(match)
    inn1 = match.innings1
    inn2 = match.innings2 if match.current_innings_num == 2 else None

    def _top_bat(inn):
        if not inn: return []
        active = [b for b in inn.batting_stats.values() if b.balls_faced > 0 or b.dismissal != "not out"]
        top = sorted(active, key=lambda x: x.runs_scored, reverse=True)[:4]
        return [{"name": b.profile["name"], "runs": b.runs_scored, "balls": b.balls_faced, "not_out": b.dismissal == "not out"} for b in top]

    def _top_bowl(inn):
        if not inn: return []
        active = [b for b in inn.bowling_stats.values() if b.balls_bowled > 0]
        top = sorted(active, key=lambda x: (x.wickets_taken, -x.runs_conceded), reverse=True)[:4]
        return [{"name": b.profile["name"], "wickets": b.wickets_taken, "runs": b.runs_conceded, "overs": f"{b.balls_bowled//6}.{b.balls_bowled%6}"} for b in top]

    # batting-first team is always t1 (shown on top in the scorecard)
    team1_bats_first = (match.team1["name"] == inn1.batting_team["name"])
    bat_first_team  = match.team1 if team1_bats_first else match.team2
    bat_second_team = match.team2 if team1_bats_first else match.team1
    t1_impact_attr  = "t1_impact_sub_name" if team1_bats_first else "t2_impact_sub_name"
    t2_impact_attr  = "t2_impact_sub_name" if team1_bats_first else "t1_impact_sub_name"

    target = getattr(match, "target", inn1.total_runs + 1)
    if inn2:
        max_w = _match_max_wickets(match)
        if inn2.total_runs >= target:
            result_str = f"{inn2.batting_team['name'].upper()} WON BY {max_w - inn2.wickets} WICKETS"
        elif inn2.total_runs == target - 1:
            result_str = "MATCH TIED"
        else:
            result_str = f"{inn1.batting_team['name'].upper()} WON BY {(target - 1) - inn2.total_runs} RUNS"
        if getattr(match, "dls_active", False):
            result_str += " (DLS)"
    else:
        result_str = f"TARGET SET: {inn1.total_runs + 1} RUNS"

    # Build round label from schedule entry
    _mid = getattr(match, "tournament_match_id", None)
    _round_label = ""
    _stadium = None
    if tourney and _mid:
        _m_sched = next((x for x in tourney.get("schedule", []) if x["match_id"] == _mid), None)
        if _m_sched:
            _round_label = _match_round_label(_m_sched)
            _stadium = _m_sched.get("stadium")

    return {
        "theme": theme,
        "tournament_type": tourney.get("tournament_type") if tourney else None,
        "toss_team": (match.team1["name"].upper() if getattr(match, "toss_winner", None) == match.p1_id
                      else match.team2["name"].upper() if getattr(match, "toss_winner", None) == match.p2_id
                      else None),
        "match_id": str(getattr(match, "tournament_match_id", "?")),
        "round_label": _round_label,
        "stadium": _stadium,
        "center_logo": tourney.get("scoreboard_logo") if tourney else None,
        "tourn_name": getattr(match, "tournament_name", "TOURNAMENT").upper(),
        "format_overs": getattr(match, "format_overs", 20),
        "result_str": result_str,
        "potm": potm if potm else None,
        "t1": {
            "name": inn1.batting_team["name"].upper(),
            "color": bat_first_team.get("color", "#6B7280"),
            "logo_emoji": _team_logo(inn1.batting_team["name"]),
            "runs": inn1.total_runs,
            "wickets": inn1.wickets,
            "balls": inn1.total_balls,
            "yet_to_bat": False,
            "batters": _top_bat(inn1),
            "bowlers": _top_bowl(inn1),
            "impact_sub": getattr(match, t1_impact_attr, None),
        },
        "t2": {
            "name": bat_second_team["name"].upper(),
            "color": bat_second_team.get("color", "#6B7280"),
            "logo_emoji": _team_logo(bat_second_team["name"]),
            "runs": inn2.total_runs if inn2 else 0,
            "wickets": inn2.wickets if inn2 else 0,
            "balls": inn2.total_balls if inn2 else 0,
            "yet_to_bat": inn2 is None,
            "batters": _top_bat(inn2),
            "bowlers": _top_bowl(inn2),
            "impact_sub": getattr(match, t2_impact_attr, None),
        },
    }


def extract_scorecard_players(match: CricketMatch) -> dict:
    """Minimal per-match data for scorecard regeneration + full text scorecard.
    Only stores what can't be derived from the existing tournament/result JSON.
    Uses short keys + arrays to keep the stored JSON as small as possible.
    Stores EVERY active batter (with dismissal text) and EVERY bowler so the
    `cv tournament match_scorecard` command can rebuild the exact end-of-innings
    text scorecard - not just the top-4 image summary. Lists stay sorted by
    runs / wickets so the image renderers (which take the first slots) are
    unchanged. Per-match size: ~1–1.5 KB -> 45 matches ≈ 50–70 KB in the bin.
    """
    potm = get_player_of_the_match(match)
    inn1 = match.innings1
    inn2 = match.innings2 if match.current_innings_num == 2 else None

    def _bat(inn):
        if not inn: return []
        active = [b for b in inn.batting_stats.values() if b.balls_faced > 0 or b.dismissal != "not out"]
        ordered = sorted(active, key=lambda x: x.runs_scored, reverse=True)
        return [[b.profile["name"], b.runs_scored, b.balls_faced, b.dismissal,
                 getattr(b, "fours", 0), getattr(b, "sixes", 0)] for b in ordered]

    def _bowl(inn):
        if not inn: return []
        active = [b for b in inn.bowling_stats.values() if b.balls_bowled > 0]
        ordered = sorted(active, key=lambda x: (x.wickets_taken, -x.runs_conceded), reverse=True)
        return [[b.profile["name"], b.wickets_taken, b.runs_conceded, f"{b.balls_bowled//6}.{b.balls_bowled%6}",
                 getattr(b, "maidens", 0)] for b in ordered]

    def _extras(inn):
        if not inn: return None
        return [getattr(inn, "extras", 0), getattr(inn, "byes", 0), getattr(inn, "legbyes", 0),
                getattr(inn, "noballs", 0), getattr(inn, "wides", 0)]

    # bf=1 means m["team1"] batted first, bf=2 means m["team2"] batted first
    team1_bats_first = (match.team1["name"] == inn1.batting_team["name"])
    bf = 1 if team1_bats_first else 2
    t1_impact_attr = "t1_impact_sub_name" if team1_bats_first else "t2_impact_sub_name"
    t2_impact_attr = "t2_impact_sub_name" if team1_bats_first else "t1_impact_sub_name"

    target = getattr(match, "target", inn1.total_runs + 1)
    if inn2:
        max_w = _match_max_wickets(match)
        if getattr(match, "tiebreak_winner_name", None):
            rs = f"{match.tiebreak_winner_name.upper()} WON (SUPER OVER)"
        elif inn2.total_runs >= target:
            rs = f"{inn2.batting_team['name'].upper()} WON BY {max_w - inn2.wickets} WICKETS"
        elif inn2.total_runs == target - 1:
            rs = "MATCH TIED"
        else:
            rs = f"{inn1.batting_team['name'].upper()} WON BY {(target - 1) - inn2.total_runs} RUNS"
        if getattr(match, "dls_active", False): rs += " (DLS)"
    else:
        rs = f"TARGET SET: {inn1.total_runs + 1} RUNS"

    return {
        "rs": rs,
        "p":  potm,
        "bf": bf,
        "i1": getattr(match, t1_impact_attr, None),
        "i2": getattr(match, t2_impact_attr, None),
        "b1": _bat(inn1),   # batting-first team's batters
        "w1": _bowl(inn2),  # batting-first team's bowlers (bowled in inn2)
        "b2": _bat(inn2),   # batting-second team's batters
        "w2": _bowl(inn1),  # batting-second team's bowlers (bowled in inn1)
        "x1": _extras(inn1),  # [extras, byes, legbyes, noballs, wides] per innings
        "x2": _extras(inn2),  #   feeds the CCODI card's EXTRAS breakdown from storage
    }


def _match_round_label(m: dict) -> str:
    """Build a human-readable round label from a schedule match entry."""
    stage     = m.get("stage", "")
    group     = m.get("group", "")
    round_val = m.get("round", "")
    if stage == "group" and group:
        return f"Group {group}"
    if isinstance(round_val, int):
        return f"Round {round_val}"
    return str(round_val) if round_val else ""


def reconstruct_scorecard_data(tourney: dict, m: dict) -> dict:
    """Rebuild the full display dict from minimal stored data + existing tournament JSON.
    Returns None if no scorecard_players entry exists (old match or data missing).
    """
    r = m.get("result") or {}
    p = r.get("scorecard_players")
    if not p:
        return None
    t1_name = m["team1"]
    t2_name = m["team2"]
    t1_team = next((t for t in tourney.get("teams", []) if t["name"] == t1_name), {})
    t2_team = next((t for t in tourney.get("teams", []) if t["name"] == t2_name), {})

    def _bat(arrays):
        out = []
        for a in (arrays or []):
            d4 = a[3] if len(a) > 3 else "not out"
            if isinstance(d4, bool):
                # old format: 4th element was a not_out boolean (no dismissal text)
                not_out, dismissal = d4, ("not out" if d4 else "")
            else:
                dismissal = d4 or "not out"
                not_out = (dismissal == "not out")
            out.append({"name": a[0], "runs": a[1], "balls": a[2], "not_out": not_out, "dismissal": dismissal,
                        "fours": a[4] if len(a) > 4 else None, "sixes": a[5] if len(a) > 5 else None})
        return out
    def _bowl(arrays):
        return [{"name": a[0], "wickets": a[1], "runs": a[2], "overs": a[3],
                 "maidens": a[4] if len(a) > 4 else 0} for a in (arrays or [])]

    # bf=1 -> team1 batted first; bf=2 -> team2 batted first
    # result stores t1_*/t2_* keyed to m["team1"]/m["team2"], not batting order
    bf = p.get("bf", 1)
    if bf == 2:
        # team2 batted first -> swap for display
        top_name, top_team, top_r, top_w, top_b = t2_name, t2_team, r.get("t2_runs", 0), r.get("t2_wickets", 0), r.get("t2_balls", 0)
        bot_name, bot_team, bot_r, bot_w, bot_b = t1_name, t1_team, r.get("t1_runs", 0), r.get("t1_wickets", 0), r.get("t1_balls", 0)
    else:
        top_name, top_team, top_r, top_w, top_b = t1_name, t1_team, r.get("t1_runs", 0), r.get("t1_wickets", 0), r.get("t1_balls", 0)
        bot_name, bot_team, bot_r, bot_w, bot_b = t2_name, t2_team, r.get("t2_runs", 0), r.get("t2_wickets", 0), r.get("t2_balls", 0)

    return {
        "theme":           tourney.get("theme", "Default"),
        "tournament_type": tourney.get("tournament_type", "round_robin"),
        "match_id":        str(m["match_id"]),
        "round_label":     _match_round_label(m),
        "stadium":         m.get("stadium"),
        "center_logo":     tourney.get("scoreboard_logo"),
        "tourn_name":      tourney["name"].upper(),
        "format_overs":    r.get("format_overs", 20),
        "result_str":      p.get("rs", ""),
        "potm":            p.get("p"),
        "server_id":       tourney.get("server_id"),
        "t1": {
            "name":       top_name.upper(),
            "raw_name":   top_name,
            "color":      top_team.get("color", "#6B7280"),
            "logo_emoji": top_team.get("logo_match") or top_team.get("logo_standings"),
            "runs":       top_r,
            "wickets":    top_w,
            "balls":      top_b,
            "yet_to_bat": False,
            "batters":    _bat(p.get("b1")),
            "bowlers":    _bowl(p.get("w2")),
            "impact_sub": p.get("i1"),
            "extras":     p.get("x1"),
        },
        "t2": {
            "name":       bot_name.upper(),
            "raw_name":   bot_name,
            "color":      bot_team.get("color", "#6B7280"),
            "logo_emoji": bot_team.get("logo_match") or bot_team.get("logo_standings"),
            "runs":       bot_r,
            "wickets":    bot_w,
            "balls":      bot_b,
            "yet_to_bat": False,
            "batters":    _bat(p.get("b2")),
            "bowlers":    _bowl(p.get("w1")),
            "impact_sub": p.get("i2"),
            "extras":     p.get("x2"),
        },
    }


def build_stored_scorecard_embeds(data: dict) -> list:
    """Build the detailed text scorecard for a completed match - one embed per
    innings - from reconstructed match data. Mirrors the end-of-innings
    `render_full_scorecard_embed` so a stored match reads the same as it did live.
    Note: batters are listed by runs scored (the order they're stored in), not
    strict batting order. Old matches (stored before full data) lack dismissal
    text - those lines render blank.
    """
    embeds = []
    potm = data.get("potm")

    def _ov(balls):
        return f"{balls // 6}.{balls % 6}"

    def _side_embed(side, is_second):
        runs = side.get("runs", 0)
        wkts = side.get("wickets", 0)
        balls = side.get("balls", 0)
        emb = discord.Embed(
            title=f"📋 Full Scorecard: {side.get('name', '').title()}",
            color=discord.Color.gold(),
        )
        desc = ""
        if is_second and potm:
            desc += f"⭐ **Player of the Match:** {potm}\n\n"
        desc += f"**Total Score:** {runs}/{wkts} in {_ov(balls)} Overs\n"
        emb.description = desc

        b_text = "```text\nBATTER                  R    B    SR\n"
        for b in side.get("batters", []):
            bf = b.get("balls", 0)
            rs = b.get("runs", 0)
            sr = (rs / bf * 100) if bf else 0.0
            status = b.get("dismissal") or ("not out" if b.get("not_out") else "")
            b_text += f"{b['name'][:18]:<24}{rs:<5}{bf:<5}{sr:<5.1f}\n"
            b_text += f"  └ {status}\n"
        b_text += "```"

        bw_text = "```text\nBOWLER                  O    R    W    ECO\n"
        for bw in side.get("bowlers", []):
            ov = str(bw.get("overs", "0.0"))
            try:
                _w, _b = ov.split(".")
                bls = int(_w) * 6 + int(_b)
            except Exception:
                bls = 0
            rc = bw.get("runs", 0)
            eco = (rc / bls * 6) if bls else 0.0
            bw_text += f"{bw['name'][:18]:<24}{ov:<5}{rc:<5}{bw.get('wickets', 0):<5}{eco:<5.1f}\n"
        bw_text += "```"

        emb.add_field(name="Batting", value=b_text, inline=False)
        emb.add_field(name="Bowling", value=bw_text, inline=False)
        return emb

    t1 = data.get("t1") or {}
    t2 = data.get("t2") or {}
    if t1.get("batters") or t1.get("runs"):
        embeds.append(_side_embed(t1, False))
    if t2.get("batters") or t2.get("runs"):
        embeds.append(_side_embed(t2, True))
    return embeds


def _fetch_emoji_img(emoji_str: str, size: int = 72):
    """Download a Discord custom emoji or Unicode emoji as PIL RGBA Image. Returns None on failure."""
    if not emoji_str:
        return None
    import re as _re, requests as _req, io as _io2
    emoji_str = emoji_str.strip()
    try:
        # Base64 data URI (stored from attachment upload - never expires)
        if emoji_str.startswith("data:image/"):
            import base64 as _b64
            _data = emoji_str.split(",", 1)[1]
            return Image.open(_io2.BytesIO(_b64.b64decode(_data))).convert("RGBA").resize((size, size), Image.LANCZOS)
        # Custom Discord emoji <:name:id> or <a:name:id>
        m = _re.match(r'<(a?):(\w+):(\d+)>', emoji_str)
        if m:
            ext = "gif" if m.group(1) == "a" else "png"
            url = f"https://cdn.discordapp.com/emojis/{m.group(3)}.{ext}"
            resp = _req.get(url, timeout=5)
            if resp.status_code == 200:
                pil_img = Image.open(_io2.BytesIO(resp.content))
                if hasattr(pil_img, "seek"):
                    try: pil_img.seek(0)
                    except EOFError: pass
                return pil_img.convert("RGBA").resize((size, size), Image.LANCZOS)

        # Direct image URL
        if emoji_str.startswith("http://") or emoji_str.startswith("https://"):
            resp = _req.get(emoji_str, timeout=5)
            if resp.status_code == 200:
                return Image.open(_io2.BytesIO(resp.content)).convert("RGBA").resize((size, size), Image.LANCZOS)
            return None

        # Plain text / :shortcode: with no ID - can't resolve without guild context
        if all(ord(c) < 128 for c in emoji_str):
            return None
        # Unicode emoji -> Twemoji CDN (skip variation selectors and ZWJ)
        codepoints = "-".join(
            f"{ord(c):x}" for c in emoji_str
            if ord(c) not in (0xFE0F, 0xFE0E, 0x200D) and ord(c) > 0x7F
        )
        if not codepoints:
            return None
        for twurl in [
            f"https://cdn.jsdelivr.net/gh/twitter/twemoji@v14.0.2/assets/72x72/{codepoints}.png",
        ]:
            try:
                resp = _req.get(twurl, timeout=5)
                if resp.status_code == 200:
                    return Image.open(_io2.BytesIO(resp.content)).convert("RGBA").resize((size, size), Image.LANCZOS)
            except Exception:
                continue
    except Exception as _e:
        print(f"Emoji fetch failed ({emoji_str}): {_e}")
    return None


def generate_t20wc_scorecard(data: dict) -> io.BytesIO:
    """Generate an ICC T20 WC themed match summary image using t20_scoreboard.png template."""
    t1 = data["t1"]
    t2 = data["t2"]
    result_str  = data.get("result_str", "")
    potm        = data.get("potm", "")
    match_id    = str(data.get("match_id", "?"))
    round_label = data.get("round_label", "")
    fmt_overs   = data.get("format_overs", 20)

    # Background
    try:
        bg = Image.open("assets/t20_scoreboard.png").convert("RGBA")
    except FileNotFoundError:
        bg = Image.new("RGBA", (975, 634), (15, 15, 40, 255))
    W, H = bg.size
    img = bg.copy()
    d   = ImageDraw.Draw(img)

    # Fonts
    _fbd = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
    _frg = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    try:
        fTeam   = ImageFont.truetype(_fbd, int(H * 0.048))
        fScore  = ImageFont.truetype(_fbd, int(H * 0.058))
        fOvers  = ImageFont.truetype(_frg, int(H * 0.020))
        fName   = ImageFont.truetype(_fbd, int(H * 0.026))
        fRuns   = ImageFont.truetype(_fbd, int(H * 0.030))
        fBalls  = ImageFont.truetype(_frg, int(H * 0.020))
        fMatch  = ImageFont.truetype(_fbd, int(H * 0.022))
        fResult = ImageFont.truetype(_fbd, int(H * 0.028))
        fPotm   = ImageFont.truetype(_fbd, int(H * 0.020))
    except Exception:
        fTeam = fScore = fOvers = fName = fRuns = fBalls = fMatch = fResult = fPotm = ImageFont.load_default()

    def _tw(t, f):
        return f.getbbox(t)[2] if hasattr(f, "getbbox") else len(t) * 9
    def _th(f):
        bb = f.getbbox("Ag") if hasattr(f, "getbbox") else None
        return (bb[3] - bb[1]) if bb else 12

    # Band height: content-fitted (team name + overs + small padding)
    _band_pad = int(H * 0.030)            # padding around text block
    band_h    = _th(fTeam) + 4 + _th(fOvers) + _band_pad

    T1B_Y1 = int(H * 0.138)               # 144
    T1B_Y2 = T1B_Y1 + band_h
    T1S_Y2 = int(H * 0.508)               # 530
    T2B_Y1 = T1S_Y2
    T2B_Y2 = T2B_Y1 + band_h
    T2S_Y2 = int(H * 0.841)               # 878
    RES_Y1 = T2S_Y2
    RES_Y2 = int(H * 0.901)               # 940 - covers template transition pixels at y=939-940

    COL_MID   = W // 2

    BAT_NAME_X = 18
    BAT_R_X    = int(W * 0.412)
    BAT_B_X    = int(W * 0.463)
    BWL_NAME_X = COL_MID + 20
    BWL_WR_X   = int(W * 0.874)
    BWL_OVR_X  = int(W * 0.948)

    WHITE = (255, 255, 255, 255)
    LGRAY = (170, 170, 170, 255)
    DGRAY = (100, 100, 100, 255)
    DKBLK = (28,  28,  28,  255)
    GOLD  = (255, 210,   0, 255)

    # Helpers
    def _overs(balls):
        if not balls: return str(fmt_overs)
        return str(balls // 6) if balls % 6 == 0 else f"{balls // 6}.{balls % 6}"

    def _score(td):
        w = td.get("wickets", 0)
        return str(td["runs"]) if w >= 10 else f"{td['runs']}-{w}"

    def _paste_logo(emoji_str, bx, by, size):
        """Fetch and paste emoji; return x after the logo (or bx if nothing pasted)."""
        logo = _fetch_emoji_img(emoji_str, size) if emoji_str else None
        if logo:
            img.paste(logo, (bx, by), logo)
            return bx + size + 12
        return bx

    # Match number - above template's green "MATCH SUMMARY" (y=64-98)
    ctx_line = f"MATCH {match_id}"
    if round_label:
        ctx_line += f"  •  {round_label.upper()}"
    d.text(((W - _tw(ctx_line, fMatch)) // 2, int(H * 0.022)),
           ctx_line, fill=(200, 200, 200, 255), font=fMatch)

    # Team band
    def _hex_rgba(h, alpha=255):
        h = (h or "#6B7280").lstrip("#")
        try:
            return tuple(int(h[i:i+2], 16) for i in (0, 2, 4)) + (alpha,)
        except Exception:
            return (107, 114, 128, alpha)

    BALL_COLOR = (140, 20, 30, 255)

    def draw_band(td, y1, y2):
        d.rectangle([(0, y1), (W, y2)], fill=_hex_rgba(td.get("color", "#6B7280")))
        band_h  = y2 - y1
        logo_sz = int(band_h * 0.80)
        logo_y  = y1 + (band_h - logo_sz) // 2
        name_x  = _paste_logo(td.get("logo_emoji"), 16, logo_y, logo_sz)
        sep_x   = 16 + logo_sz + 8
        if name_x == 16:
            d.line([(sep_x, y1 + 4), (sep_x, y2 - 4)], fill=(255, 255, 255, 230), width=5)
            name_x = sep_x + 22
        name_h  = _th(fTeam) + 4 + _th(fOvers)
        block_y = y1 + (band_h - name_h) // 2
        d.text((name_x, block_y), td["name"][:18], fill=WHITE, font=fTeam)
        ovr_y   = block_y + _th(fTeam) + 4
        ovr_val = _overs(td.get('balls', 0))
        d.text((name_x,                          ovr_y), "OVERS ",  fill=(150, 150, 150, 255), font=fOvers)
        d.text((name_x + _tw("OVERS ", fOvers),  ovr_y), ovr_val,   fill=WHITE,                font=fOvers)
        sc   = _score(td)
        sc_x = W - _tw(sc, fScore) - 24
        sc_y = y1 + (band_h - _th(fScore)) // 2
        d.text((sc_x, sc_y), sc, fill=WHITE, font=fScore)

    # Stats table (no header row - matches ICC reference style)
    def draw_stats(td, y1, y2):
        d.rectangle([(0, y1), (W, y2)], fill=(255, 255, 255, 255))
        row_h = (y2 - y1) // 4
        # Pass 1: row backgrounds + horizontal dividers
        for i in range(4):
            ry = y1 + i * row_h
            if i % 2 == 1:
                d.rectangle([(0, ry), (W, ry + row_h)], fill=(246, 246, 246, 255))
            d.line([(0, ry + row_h), (W, ry + row_h)], fill=(220, 220, 220, 255), width=1)
        # Column separator drawn AFTER row backgrounds so it shows in all rows
        d.line([(COL_MID, y1), (COL_MID, y2)], fill=(200, 200, 200, 255), width=2)
        # Pass 2: text
        for i in range(4):
            ry  = y1 + i * row_h
            mid = ry + row_h // 2
            ny  = mid - _th(fName)  // 2
            ry2 = mid - _th(fRuns)  // 2
            by2 = mid - _th(fBalls) // 2
            if i < len(td.get("batters", [])):
                b  = td["batters"][i]
                nm = b["name"][:18].upper()
                d.text((BAT_NAME_X, ny), nm, fill=DKBLK, font=fName)
                if potm and b["name"].upper() == potm.upper():
                    d.text((BAT_NAME_X + _tw(nm, fName) + 5, ny), "★", fill=GOLD, font=fName)
                rs = f"{b['runs']}{'*' if b.get('not_out') else ''}"
                d.text((BAT_R_X - _tw(rs,              fRuns )//2, ry2), rs,              fill=DKBLK, font=fRuns)
                d.text((BAT_B_X - _tw(str(b["balls"]), fBalls)//2, by2), str(b["balls"]), fill=DGRAY, font=fBalls)
            if i < len(td.get("bowlers", [])):
                bw = td["bowlers"][i]
                nm = bw["name"][:18].upper()
                d.text((BWL_NAME_X, ny), nm, fill=DKBLK, font=fName)
                if potm and bw["name"].upper() == potm.upper():
                    d.text((BWL_NAME_X + _tw(nm, fName) + 5, ny), "★", fill=GOLD, font=fName)
                wr = f"{bw['wickets']}-{bw['runs']}"
                d.text((BWL_WR_X  - _tw(wr,           fRuns )//2, ry2), wr,           fill=DKBLK, font=fRuns)
                d.text((BWL_OVR_X - _tw(bw["overs"],  fBalls)//2, by2), bw["overs"],  fill=DGRAY, font=fBalls)

    # Render both teams
    draw_band(t1,  T1B_Y1, T1B_Y2)
    draw_stats(t1, T1B_Y2, T1S_Y2)
    draw_band(t2,  T2B_Y1, T2B_Y2)
    draw_stats(t2, T2B_Y2, T2S_Y2)

    # Result area layout
    SPON_DARK = (0, 4, 29, 255)           # matches template sponsor bar colour
    RES_PAD_X = int(W * 0.009)
    RES_PAD_Y = int((RES_Y2 - RES_Y1) * 0.05)
    RES_R     = int(W * 0.011)
    bar_y1    = RES_Y1 + RES_PAD_Y
    bar_y2    = RES_Y2 - RES_PAD_Y
    bar_mid   = (bar_y1 + bar_y2) // 2
    # 1. Cover template: white top half, dark bottom half
    d.rectangle([(0, RES_Y1), (W, bar_mid)], fill=(255, 255, 255, 255))
    d.rectangle([(0, bar_mid), (W, RES_Y2)], fill=SPON_DARK)
    # 2. White rounded bottom corners visible against the dark below
    WH_R = min(int(W * 0.012), (bar_mid - RES_Y1) // 2 - 1)
    d.rounded_rectangle([(0, RES_Y1), (W, bar_mid)], radius=WH_R, fill=(255, 255, 255, 255))
    # 3. Blue result bar straddles both halves
    d.rounded_rectangle([(RES_PAD_X, bar_y1), (W - RES_PAD_X, bar_y2)], radius=RES_R, fill=(0, 30, 138, 255))
    res_cy = (bar_y1 + bar_y2) // 2
    if potm:
        total_h = _th(fResult) + 3 + _th(fPotm)
        res_y   = res_cy - total_h // 2
        d.text(((W - _tw(result_str, fResult)) // 2, res_y), result_str, fill=WHITE, font=fResult)
        potm_txt = f"PLAYER OF THE MATCH: {potm.upper()}"
        d.text(((W - _tw(potm_txt, fPotm)) // 2, res_y + _th(fResult) + 3), potm_txt, fill=GOLD, font=fPotm)
    else:
        d.text(((W - _tw(result_str, fResult)) // 2, res_cy - _th(fResult) // 2),
               result_str, fill=WHITE, font=fResult)

    # Flatten RGBA -> RGB
    final = Image.new("RGB", img.size, (255, 255, 255))
    final.paste(img, mask=img.split()[3])
    buf = io.BytesIO()
    final.save(buf, format="PNG")
    buf.seek(0)
    return buf


def _acl_font(size):
    for p in ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",      # Linux deploy host
              "/System/Library/Fonts/Supplemental/Arial Bold.ttf",         # macOS
              "C:/Windows/Fonts/arialbd.ttf"):                             # Windows
        try:
            return ImageFont.truetype(p, size)
        except Exception:
            continue
    return ImageFont.load_default()


def generate_acl_match_summary(data: dict) -> io.BytesIO:
    """Render the ACL 'MATCH SUMMARY' card by filling assets/acl_scoreboard.png with live
    match data: per-team logo (from the tournament logo command) + colour, score in the dark
    cap, batter runs/balls in the colour block, bowler figures in the BOWLING team's colour,
    and the result on the trophy bar. Text colour auto-contrasts against the template."""
    DARK, WHITE = (12, 28, 68), (255, 255, 255)
    img = Image.open("assets/acl_scoreboard.png").convert("RGBA")
    # Flatten the baked green "best bowler" strip AND its diagonal notch in both bowler
    # panels -> clean uniform white box. Restricted to the safe interior so the panel's
    # outer rounded border is preserved.
    _ip = img.load()
    for (x0, x1, y0, y1) in ((948, 1543, 362, 503), (948, 1543, 605, 747)):
        for yy in range(y0, y1):
            for xx in range(x0, x1):
                r, g, b, a = _ip[xx, yy]
                is_green = g > r + 8 and g > b + 5 and g > 70
                is_line  = abs(r - g) < 20 and abs(g - b) < 20 and 150 < r < 249
                if is_green or is_line:
                    _ip[xx, yy] = (252, 252, 252, 255)

    # Recolor each team-row's coloured frame (header bar + logo cap + score cap +
    # run/over block) to that team's colour - swap only Hue/Saturation in HSV. The
    # template bakes the top row's frame lighter than the bottom, so each frame's
    # brightness is shifted to a common (lighter) mean V -> both rows read the same,
    # with the within-frame gradient kept intact. White panels / grey grid (low
    # saturation) are untouched by the mask.
    from colorsys import rgb_to_hsv as _r2h
    FRAME_V_TARGET = 185
    def _rc_hex(c):
        try:
            c = (c or "").lstrip("#"); return tuple(int(c[i:i+2], 16) for i in (0, 2, 4))
        except Exception:
            return (107, 114, 128)
    def _recolor(box, rgb):
        th, ts, _tv = _r2h(rgb[0]/255, rgb[1]/255, rgb[2]/255)
        H = int(th*255); S = int(ts*255)
        reg = img.crop(box).convert("RGB")
        h, s, v = reg.convert("HSV").split()
        mask = s.point(lambda x: 255 if x > 70 else 0)
        _shift = FRAME_V_TARGET - ImageStat.Stat(v, mask).mean[0]
        v.paste(v.point(lambda x: max(0, min(255, int(x + _shift)))), (0, 0), mask)
        h.paste(Image.new("L", reg.size, H), (0, 0), mask)
        s.paste(Image.new("L", reg.size, S), (0, 0), mask)
        img.paste(Image.merge("HSV", (h, s, v)).convert("RGB"), (box[0], box[1]))
    try:
        _recolor((88, 278, 1556, 515), _rc_hex((data.get("t1") or {}).get("color")))
        _recolor((88, 525, 1556, 760), _rc_hex((data.get("t2") or {}).get("color")))
    except Exception as _rc_err:
        print(f"ACL team-colour recolor skipped: {_rc_err}")

    d = ImageDraw.Draw(img)
    # Redraw a 3-row grid in the bowling box identical to the batting section.
    for gy in (406, 452, 650, 696):
        d.line([(952, gy), (1543, gy)], fill=(229, 229, 233), width=2)
    px = img.convert("RGB").load()

    f_name, f_score, f_overs, f_toss = _acl_font(50), _acl_font(56), _acl_font(27), _acl_font(24)
    f_bname, f_runs, f_balls = _acl_font(31), _acl_font(37), _acl_font(25)
    f_title, f_troph = _acl_font(32), _acl_font(52)

    def tw(t, f): return d.textbbox((0, 0), t, font=f)[2]
    def th(f):    bb = d.textbbox((0, 0), "Ag", f); return bb[3] - bb[1], bb[1]
    def fit(s, max_w, base):
        sz = base; f = _acl_font(sz)
        while tw(s, f) > max_w and sz > 17:
            sz -= 1; f = _acl_font(sz)
        return f
    def _dark_bg(x, y):
        try:
            r, g, b = px[max(0, min(img.width-1, int(x))), max(0, min(img.height-1, int(y)))]
            return (0.299*r + 0.587*g + 0.114*b) < 140
        except Exception:
            return True
    def text(x, y, s, f, anchor="lm", color=None):
        s = str(s); w = tw(s, f); h, off = th(f)
        dx = x - w/2 if anchor[0] == "m" else (x - w if anchor[0] == "r" else x)
        d.text((dx, y - h/2 - off), s, fill=(color or (WHITE if _dark_bg(x, y) else DARK)), font=f)
    def _hex(c):
        try:
            c = (c or "").lstrip("#"); return tuple(int(c[i:i+2], 16) for i in (0, 2, 4))
        except Exception:
            return (107, 114, 128)
    def paste_logo(emoji, cx, cy, size, fb):
        logo = _fetch_emoji_img(emoji, size) if emoji else None
        if logo is None:
            logo = Image.new("RGBA", (size, size), (0, 0, 0, 0))
            ImageDraw.Draw(logo).ellipse([2, 2, size-2, size-2], fill=fb)
        img.paste(logo, (int(cx - size/2), int(cy - size/2)), logo)
    def _overs(b): return str(b // 6) if b % 6 == 0 else f"{b//6}.{b%6}"
    def _score(t):
        if t.get("yet_to_bat"): return "—"
        w = t.get("wickets", 0)
        return str(t.get("runs", 0)) if w >= 10 else f"{t.get('runs', 0)}-{w}"

    # Title strip: "MATCH <id> • <Stadium>" (falls back to round/tournament label if no venue/id).
    _mid = str(data.get("match_id") or "").strip()
    _venue = (data.get("stadium") or "").strip()
    if _mid and _mid != "?" and _venue:
        rl = f"MATCH {_mid} • {_venue}".upper()
    elif _mid and _mid != "?":
        rl = f"MATCH {_mid}".upper()
    else:
        rl = (data.get("round_label") or data.get("tourn_name") or "").upper()
    if rl:
        text(865, 200, rl[:60], fit(rl[:60], 900, 32), "mm")

    rows = {
        "t1": dict(hy=320, logo_cy=415, scap=(1414, 320), rows_y=[383, 432, 481], other="t2"),
        "t2": dict(hy=568, logo_cy=663, scap=(1412, 566), rows_y=[627, 676, 725], other="t1"),
    }
    NAME_X, OVERS_R = 272, 1258
    BAT_NAME_X, RUNS_X, BALLS_R = 270, 722, 898
    # Bowling mirrors batting: NAME (wide) | PERFORMANCE (big, = runs) | OVERS (small, = balls).
    BOWL_NAME_X, BOWL_PERF_X, BOWL_OV_R = 967, 1372, 1528

    def ip_badge(x, y, fsize=20):
        """Small orange 'IP' badge marking an impact-sub player; returns the width drawn."""
        f = _acl_font(fsize)
        hh = th(f)[0]
        bw_px = tw("IP", f) + 12
        bh_px = hh + 10
        d.rounded_rectangle([(x, y - bh_px / 2), (x + bw_px, y + bh_px / 2)], radius=4, fill=(196, 75, 26))
        text(x + 6, y, "IP", f, "lm", WHITE)
        return bw_px + 8

    LOGO_DY = -12   # nudge both team logos higher in their caps
    for key in ("t1", "t2"):
        t = data.get(key) or {}; cfg = rows[key]
        col = _hex(t.get("color")); bowl_col = _hex((data.get(cfg["other"]) or {}).get("color"))
        paste_logo(t.get("logo_emoji"), 172, cfg["logo_cy"] + LOGO_DY, 120, col)

        nm = (t.get("name") or "")[:30]
        _toss_on = data.get("toss_team") and data["toss_team"] == t.get("name")
        # Fit the name into the space before the overs label (reserve a TOSS chip when shown)
        _nm_max_w = (OVERS_R - 156) - NAME_X - 24 - (88 if _toss_on else 0)
        f_nm = fit(nm, _nm_max_w, 50)
        text(NAME_X, cfg["hy"], nm, f_nm, "lm", WHITE)
        if _toss_on:
            text(NAME_X + tw(nm, f_nm) + 22, cfg["hy"], "TOSS", f_toss, "lm", WHITE)
        text(OVERS_R, cfg["hy"], f"{_overs(t.get('balls', 0))} OVERS", f_overs, "rm", WHITE)
        text(cfg["scap"][0], cfg["scap"][1], _score(t), f_score, "mm", WHITE)

        # Names take the team's colour (white fallback only where they sit on a dark strip).
        def name_col(x, y, base): return WHITE if _dark_bg(x, y) else base
        ip_name = (t.get("impact_sub") or "").upper()
        for i, b in enumerate((t.get("batters") or [])[:3]):
            y = cfg["rows_y"][i]; star = "*" if b.get("not_out") else ""
            nm = b["name"].upper()
            bf = fit(nm, 400, 31)
            text(BAT_NAME_X, y, nm, bf, "lm", name_col(BAT_NAME_X, y, col))
            if ip_name and nm == ip_name:
                ip_badge(BAT_NAME_X + tw(nm, bf) + 10, y)
            text(RUNS_X, y, f"{b['runs']}{star}", f_runs, "lm", WHITE)
            text(BALLS_R, y, str(b["balls"]), f_balls, "rm", WHITE)

        for i, bw in enumerate((t.get("bowlers") or [])[:3]):
            y = cfg["rows_y"][i]; nm = bw["name"].upper()
            wf = fit(nm, 380, 31)
            text(BOWL_NAME_X, y, nm, wf, "lm", name_col(BOWL_NAME_X, y, bowl_col))
            if ip_name and nm == ip_name:
                ip_badge(BOWL_NAME_X + tw(nm, wf) + 10, y)
            figcol = WHITE if _dark_bg(BOWL_PERF_X + 30, y) else bowl_col
            text(BOWL_PERF_X, y, f"{bw['wickets']}-{bw['runs']}", f_runs, "lm", figcol)
            text(BOWL_OV_R, y, str(bw.get("overs", "")).split(".")[0], f_balls, "rm")

    res = (data.get("result_str") or "").upper()
    potm = data.get("potm")
    if potm:
        text(910, 810, res[:46], _acl_font(42), "mm", WHITE)
        text(910, 849, f"PLAYER OF THE MATCH  —  {str(potm).upper()}", _acl_font(23), "mm", WHITE)
    else:
        text(910, 828, res[:46], f_troph, "mm", WHITE)

    out = Image.new("RGB", img.size, (255, 255, 255))
    out.paste(img, mask=img.split()[3])
    buf = io.BytesIO(); out.save(buf, format="PNG"); buf.seek(0)
    return buf


def generate_scorecard_from_data(data: dict) -> io.BytesIO:
    """Generate a scorecard image from pre-serialized match display data."""
    if data.get("tournament_type") == "acl":
        try:
            return generate_acl_match_summary(data)
        except Exception as e:
            print(f"ACL match-summary render failed, using default scorecard: {e}")
    if data.get("tournament_type") == "t20_world_cup" or data.get("theme") == "T20 World Cup":
        try:
            return generate_t20wc_scorecard(data)
        except Exception as _e:
            print(f"T20 WC scorecard error: {_e}. Falling back to default.")

    theme      = data.get("theme", "Default")
    t1_data    = data["t1"]
    t2_data    = data["t2"]
    potm       = data.get("potm")
    match_id   = str(data.get("match_id", "?"))
    tourn_name = data.get("tourn_name", "TOURNAMENT")
    result_str = data.get("result_str", "")
    format_overs = data.get("format_overs", 20)

    if theme == "Crimson Cricket":
        try:
            # Layout
            _W, _H       = 1200, 720
            _H_HDR       = 130
            _H_BAR       = 65
            _H_STATS     = 200
            _H_BOT       = 60
            _SCORE_PANEL = 260

            # Colors
            _GRAD_L = (13, 0, 0)
            _GRAD_M = (107, 13, 18)
            _GRAD_R = (196, 75, 26)
            _C_PANEL     = (10, 15, 36)
            _C_SCORE     = "#00D4FF"
            _C_EVEN      = (250, 250, 250)
            _C_ODD       = (241, 241, 241)
            _C_DIV       = (215, 215, 215)
            _C_HDR       = "#999999"
            _C_NAME      = "#111111"
            _C_MAIN      = "#111111"
            _C_SUB       = "#666666"
            _C_WHITE     = "#FFFFFF"
            _C_GOLD      = (255, 215, 0)

            # Canvas
            img = Image.new("RGB", (_W, _H), "#FFFFFF")
            d   = ImageDraw.Draw(img)

            # Fonts
            _fbd = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
            _frg = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
            try:
                _fHUGE  = ImageFont.truetype(_fbd, 46)
                _fTRN   = ImageFont.truetype(_fbd, 22)
                _fMTCH  = ImageFont.truetype(_fbd, 16)
                _fTEAM  = ImageFont.truetype(_fbd, 26)
                _fSCORE = ImageFont.truetype(_fbd, 36)
                _fOVR   = ImageFont.truetype(_frg, 13)
                _fCOL   = ImageFont.truetype(_fbd, 14)
                _fNAME  = ImageFont.truetype(_fbd, 19)
                _fRUNS  = ImageFont.truetype(_fbd, 22)
                _fBALLS = ImageFont.truetype(_fbd, 16)
                _fBOT   = ImageFont.truetype(_fbd, 20)
            except:
                _fHUGE = _fTRN = _fMTCH = _fTEAM = _fSCORE = _fOVR = _fCOL = \
                _fNAME = _fRUNS = _fBALLS = _fBOT = ImageFont.load_default()

            # Helpers
            def _tw(text, font):
                if hasattr(font, 'getbbox'): return font.getbbox(text)[2]
                return len(text) * 10

            def _th(font):
                if hasattr(font, 'getbbox'):
                    bb = font.getbbox("Ag")
                    return bb[3] - bb[1]
                return 14

            def _hex(h):
                h = h.lstrip('#')
                return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))

            def _lerp(a, b, t):
                return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))

            def _star(cx, cy, size):
                outer, inner = size, size * 0.42
                pts = []
                for i in range(10):
                    angle = math.pi * i / 5 - math.pi / 2
                    r = outer if i % 2 == 0 else inner
                    pts.append((cx + r * math.cos(angle), cy + r * math.sin(angle)))
                d.polygon(pts, fill=_C_GOLD)

            # 1. Gradient header
            for x in range(_W):
                t = x / (_W - 1)
                col = _lerp(_GRAD_L, _GRAD_M, t * 2) if t < 0.5 else _lerp(_GRAD_M, _GRAD_R, (t - 0.5) * 2)
                d.line([(x, 0), (x, _H_HDR)], fill=col)

            ms_y = 22
            d.text((40, ms_y), "MATCH SUMMARY", fill=_C_WHITE, font=_fHUGE)
            d.text((44, ms_y + _th(_fHUGE) + 8), f"MATCH {match_id}", fill="#BBBBBB", font=_fMTCH)

            t_tw = _tw(tourn_name, _fTRN)
            pad  = 18
            bx1  = _W - t_tw - pad * 2 - 30;  bx2 = _W - 30
            by1  = 30;                          by2 = by1 + _th(_fTRN) + pad * 2
            d.rounded_rectangle([(bx1, by1), (bx2, by2)], radius=10, fill=(18, 4, 6))
            d.rounded_rectangle([(bx1, by1), (bx2, by2)], radius=10, outline=(196, 75, 26), width=2)
            d.text((bx1 + pad, by1 + pad), tourn_name, fill=_C_GOLD, font=_fTRN)

            # 2. Team section
            _HALF       = _W // 2
            _BN_X       = 30
            _BR_X       = 455
            _BB_X       = 535
            _WN_X       = _HALF + 20
            _WR_X       = _HALF + 400
            _WO_X       = _HALF + 490

            def _draw_team(y_top, td):
                bar_bot = y_top + _H_BAR
                tc = _hex(td.get("color", "#6B7280"))

                d.rectangle([(0, y_top), (_W - _SCORE_PANEL, bar_bot)], fill=tc)
                d.rectangle([(_W - _SCORE_PANEL, y_top), (_W, bar_bot)], fill=_C_PANEL)

                d.text((30, y_top + (_H_BAR - _th(_fTEAM)) // 2), td["name"], fill=_C_WHITE, font=_fTEAM)

                _b = td.get("balls", 0)
                if td.get("yet_to_bat") or _b == 0:
                    _ov_str = str(format_overs)
                elif _b % 6 == 0:
                    _ov_str = str(_b // 6)
                else:
                    _ov_str = f"{_b // 6}.{_b % 6}"
                ovr = f"OVERS  {_ov_str}"
                d.text((_W - _SCORE_PANEL + (_SCORE_PANEL - _tw(ovr, _fOVR)) // 2, y_top + 6),
                       ovr, fill="#AAAAAA", font=_fOVR)

                if td["yet_to_bat"]:
                    ytb = "YET TO BAT"
                    d.text((_W - _SCORE_PANEL + (_SCORE_PANEL - _tw(ytb, _fMTCH)) // 2,
                            y_top + (_H_BAR - _th(_fMTCH)) // 2 + 6), ytb, fill="#AAAAAA", font=_fMTCH)
                else:
                    sc = f"{td['runs']}-{td['wickets']}"
                    d.text((_W - _SCORE_PANEL + (_SCORE_PANEL - _tw(sc, _fSCORE)) // 2,
                            y_top + _H_BAR - _th(_fSCORE) - 6), sc, fill=_C_SCORE, font=_fSCORE)

                sy1 = bar_bot
                sy2 = sy1 + _H_STATS
                d.rectangle([(0, sy1), (_W, sy2)], fill=_C_EVEN)
                d.line([(_HALF, sy1), (_HALF, sy2)], fill=_C_DIV, width=2)

                hdr_y = sy1 + 10
                d.text((_BN_X, hdr_y), "BATTER", fill=_C_HDR, font=_fCOL)
                d.text((_BR_X - _tw("R",   _fCOL) // 2, hdr_y), "R",   fill=_C_HDR, font=_fCOL)
                d.text((_BB_X - _tw("B",   _fCOL) // 2, hdr_y), "B",   fill=_C_HDR, font=_fCOL)
                d.text((_WN_X, hdr_y), "BOWLER", fill=_C_HDR, font=_fCOL)
                d.text((_WR_X - _tw("W-R", _fCOL) // 2, hdr_y), "W-R", fill=_C_HDR, font=_fCOL)
                d.text((_WO_X - _tw("O",   _fCOL) // 2, hdr_y), "O",   fill=_C_HDR, font=_fCOL)

                row_top = hdr_y + _th(_fCOL) + 8
                d.line([(0, row_top), (_W, row_top)], fill=_C_DIV, width=1)
                row_h = (sy2 - row_top) // 4

                for i in range(4):
                    ry  = row_top + i * row_h
                    mid = ry + row_h // 2
                    bg  = _C_ODD if i % 2 == 1 else _C_EVEN
                    d.rectangle([(0, ry), (_HALF - 1, ry + row_h)], fill=bg)
                    d.rectangle([(_HALF + 1, ry), (_W, ry + row_h)], fill=bg)
                    d.line([(0, ry + row_h), (_W, ry + row_h)], fill=_C_DIV, width=1)

                    n_y = mid - _th(_fNAME)  // 2
                    r_y = mid - _th(_fRUNS)  // 2
                    b_y = mid - _th(_fBALLS) // 2 + 2

                    def _ip_badge(bx, bmid):
                        bw_px = _tw("IP", _fCOL) + 8
                        bh_px = _th(_fCOL) + 4
                        by_px = bmid - bh_px // 2
                        d.rounded_rectangle([(bx, by_px), (bx + bw_px, by_px + bh_px)],
                                            radius=3, fill=(196, 75, 26))
                        d.text((bx + 4, by_px + 2), "IP", fill=_C_WHITE, font=_fCOL)
                        return bw_px + 6

                    ip_name = (td.get("impact_sub") or "").upper()

                    if i < len(td["batters"]):
                        b  = td["batters"][i]
                        nm = b["name"][:16].upper()
                        d.text((_BN_X, n_y), nm, fill=_C_NAME, font=_fNAME)
                        _off = _BN_X + _tw(nm, _fNAME) + 8
                        if ip_name and b["name"].upper() == ip_name:
                            _off += _ip_badge(_off, mid)
                        if potm and b["name"].upper() == potm.upper():
                            _star(_off + 9, mid, 9)
                        rs = f"{b['runs']}{'*' if b.get('not_out') else ''}"
                        d.text((_BR_X - _tw(rs, _fRUNS) // 2, r_y), rs, fill=_C_MAIN, font=_fRUNS)
                        d.text((_BB_X - _tw(str(b["balls"]), _fBALLS) // 2, b_y),
                               str(b["balls"]), fill=_C_SUB, font=_fBALLS)

                    if i < len(td["bowlers"]):
                        bw = td["bowlers"][i]
                        nm = bw["name"][:16].upper()
                        d.text((_WN_X, n_y), nm, fill=_C_NAME, font=_fNAME)
                        _off = _WN_X + _tw(nm, _fNAME) + 8
                        if ip_name and bw["name"].upper() == ip_name:
                            _off += _ip_badge(_off, mid)
                        if potm and bw["name"].upper() == potm.upper():
                            _star(_off + 9, mid, 9)
                        wr = f"{bw['wickets']}-{bw['runs']}"
                        d.text((_WR_X - _tw(wr, _fRUNS) // 2, r_y), wr, fill=_C_MAIN, font=_fRUNS)
                        d.text((_WO_X - _tw(bw["overs"], _fBALLS) // 2, b_y),
                               bw["overs"], fill=_C_SUB, font=_fBALLS)

            _draw_team(_H_HDR,                  t1_data)
            _draw_team(_H_HDR + _H_BAR + _H_STATS, t2_data)

            # 3. Bottom bar
            bot_y = _H - _H_BOT
            d.rectangle([(0, bot_y), (_W, _H)], fill=_C_PANEL)

            sep       = "   •   "
            star_gap  = 26
            potm_tail = f"{potm.upper()}  (POTM)" if potm else ""
            full_w    = _tw(result_str, _fBOT)
            if potm:
                full_w += _tw(sep, _fBOT) + star_gap + _tw(potm_tail, _fBOT)

            cx = (_W - full_w) // 2
            cy = bot_y + (_H_BOT - _th(_fBOT)) // 2
            d.text((cx, cy), result_str, fill=_C_WHITE, font=_fBOT)
            cx += _tw(result_str, _fBOT)
            if potm:
                d.text((cx, cy), sep, fill=_C_WHITE, font=_fBOT)
                cx += _tw(sep, _fBOT)
                _star(cx + 9, cy + _th(_fBOT) // 2, 9)
                cx += star_gap
                d.text((cx, cy), potm_tail, fill=_C_WHITE, font=_fBOT)

            buf = io.BytesIO()
            img.save(buf, format="PNG")
            buf.seek(0)
            return buf
        except Exception as e:
            print(f"Crimson Cricket scoreboard error: {e}. Falling back to default.")
            pass

    # Default "broadcast" scoreboard
    # Side-by-side two-team layout: white header (team names + crests +
    # centre logo), format bar, split score band, batter grids, bowler
    # grids, then a navy result/POTM footer. Left column = batting-first
    # team (t1) + the bowlers who bowled to it; right column = t2.
    W, H = 1200, 782
    img = Image.new("RGB", (W, H), "#FFFFFF")
    d   = ImageDraw.Draw(img)

    # Fonts - prefer bundled DejaVu (Linux host), fall back to macOS/Windows,
    # then PIL's default so a missing font never crashes the render.
    def _font(size, bold=True):
        cands = ([
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
            "/Library/Fonts/Arial Bold.ttf",
            "C:/Windows/Fonts/arialbd.ttf",
        ] if bold else [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/System/Library/Fonts/Supplemental/Arial.ttf",
            "/Library/Fonts/Arial.ttf",
            "C:/Windows/Fonts/arial.ttf",
        ])
        for p in cands:
            try: return ImageFont.truetype(p, size)
            except Exception: continue
        return ImageFont.load_default()

    f_name   = _font(42)   # team names in the header
    f_score  = _font(46)   # big score
    f_bar    = _font(22)   # format bar
    f_ovr    = _font(16, bold=False)
    f_col    = _font(16)   # column headers
    f_player = _font(22)   # batter/bowler names
    f_runs   = _font(22)
    f_balls  = _font(17, bold=False)
    f_foot   = _font(24)
    f_micro  = _font(15, bold=False)

    def get_tw(text, font):
        if hasattr(font, 'getbbox'): return font.getbbox(text)[2]
        return len(text) * 12

    def _th(font):
        if hasattr(font, 'getbbox'):
            bb = font.getbbox("Ag"); return bb[3] - bb[1]
        return 14

    def _hex(h, default="#6B7280"):
        h = (h or default).lstrip("#")
        try: return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))
        except Exception: return (107, 114, 128)

    c_navy  = "#0A0F24"
    c_white = "#FFFFFF"
    c_grid  = "#E6E8EC"
    c_alt   = "#F4F5F7"
    c_grey  = "#8A94A6"
    c_gold  = "#FFD54A"
    tc1 = _hex(t1_data.get("color"))
    tc2 = _hex(t2_data.get("color"))
    c_navy_rgb = _hex(c_navy)
    c_gold_rgb = _hex(c_gold)

    # Colour + gradient helpers
    def _lerp(a, b, t):
        return tuple(int(a[k] + (b[k] - a[k]) * t) for k in range(3))

    def _darken(rgb, f=0.62):
        return tuple(int(c * f) for c in rgb)

    def _lighten(rgb, f=0.35):
        return tuple(int(c + (255 - c) * f) for c in rgb)

    def _vgrad(x0, y0, x1, y1, top_rgb, bot_rgb):
        h = max(1, y1 - y0)
        for i in range(h):
            d.line([(x0, y0 + i), (x1, y0 + i)], fill=_lerp(top_rgb, bot_rgb, i / h))

    def _hgrad(x0, y0, x1, y1, l_rgb, r_rgb):
        w = max(1, x1 - x0)
        for i in range(w):
            d.line([(x0 + i, y0), (x0 + i, y1)], fill=_lerp(l_rgb, r_rgb, i / w))

    def _star(cx, cy, size, fill=c_gold):
        pts = []
        for i in range(10):
            ang = math.pi * i / 5 - math.pi / 2
            r = size if i % 2 == 0 else size * 0.42
            pts.append((cx + r * math.cos(ang), cy + r * math.sin(ang)))
        d.polygon(pts, fill=fill)

    def _paste_logo(emoji_str, cx, cy, size, fallback_color, fallback_text):
        """Paste a team crest centred at (cx, cy); draw a colour-disc
        placeholder with initials when no logo image is available."""
        logo = None
        try:
            logo = _fetch_emoji_img(emoji_str, size) if emoji_str else None
        except Exception:
            logo = None
        x0, y0 = int(cx - size / 2), int(cy - size / 2)
        if logo:
            img.paste(logo, (x0, y0), logo)
        else:
            d.ellipse([(x0, y0), (x0 + size, y0 + size)], fill=fallback_color)
            ini = fallback_text[:3].upper()
            d.text((cx - get_tw(ini, f_col) / 2, cy - _th(f_col) / 2), ini, fill=c_white, font=f_col)

    HALF = W // 2

    # 1. Header: soft vertical-gradient bg + crests, names, centre logo
    HDR_H, HDR_MID, LOGO_SZ = 126, 63, 78
    _vgrad(0, 0, W, HDR_H, (255, 255, 255), (243, 245, 248))

    _paste_logo(t1_data.get("logo_emoji"), 88, HDR_MID, LOGO_SZ, tc1, t1_data["name"])
    _paste_logo(t2_data.get("logo_emoji"), W - 88, HDR_MID, LOGO_SZ, tc2, t2_data["name"])

    n1 = t1_data["name"][:16].upper()
    d.text((150, HDR_MID - _th(f_name) // 2 - 4), n1, fill=c_navy, font=f_name)
    n2 = t2_data["name"][:16].upper()
    d.text((W - 150 - get_tw(n2, f_name), HDR_MID - _th(f_name) // 2 - 4), n2, fill=c_navy, font=f_name)

    # Centre logo: a tournament-set custom logo if present, else the CricVerse default.
    _center = data.get("center_logo")
    _logo = None
    try:
        if _center:
            _logo = _fetch_emoji_img(_center, 96)
        if _logo is None:
            _lp = "assets/logo.png" if os.path.exists("assets/logo.png") else "assets/logo.jpg"
            _logo = Image.open(_lp).convert("RGBA").resize((96, 96), Image.LANCZOS)
    except Exception:
        _logo = None
    if _logo is not None:
        # Flatten onto white so transparent logo pixels don't render black
        _flat = Image.new("RGBA", (96, 96), (255, 255, 255, 255))
        _flat.alpha_composite(_logo)
        _mask = Image.new("L", (96, 96), 0)
        ImageDraw.Draw(_mask).ellipse((0, 0, 96, 96), fill=255)
        img.paste(_flat, (W // 2 - 48, HDR_MID - 48), _mask)
    else:
        d.ellipse([(W // 2 - 48, HDR_MID - 48), (W // 2 + 48, HDR_MID + 48)],
                  outline=c_grid, width=3)

    _mno = tourn_name[:28]
    d.text((W - 12 - get_tw(_mno, f_micro), 9), _mno, fill=c_grey, font=f_micro)
    _round = str(data.get("round_label") or "").upper()
    if _round:
        d.text((12, 9), _round, fill=c_grey, font=f_micro)

    # Team-coloured accent strip framing the header bottom.
    _hgrad(0,    HDR_H - 4, HALF, HDR_H, tc1, _lighten(tc1, 0.45))
    _hgrad(HALF, HDR_H - 4, W,    HDR_H, _lighten(tc2, 0.45), tc2)

    # 2. Context bar (compact): "MATCH N • STADIUM • FMT (N OVERS)"
    BAR_Y1, BAR_Y2 = HDR_H, HDR_H + 32
    _vgrad(0, BAR_Y1, W, BAR_Y2, (238, 240, 243), (227, 230, 235))
    _fmt = "T20" if format_overs <= 20 else "ODI"
    _stadium = str(data.get("stadium") or "").strip()
    _left = f"MATCH {match_id}"
    if _stadium:
        _left += f"   •   {_stadium.upper()}"
    bar_txt = f"{_left}   •   {_fmt} ({format_overs} OVERS)"
    d.text(((W - get_tw(bar_txt, f_bar)) // 2, BAR_Y1 + (BAR_Y2 - BAR_Y1 - _th(f_bar)) // 2),
           bar_txt, fill=c_navy, font=f_bar)

    # 3. Score band: diagonal split of two glossy team-colour gradients
    SB_Y1, SB_Y2 = BAR_Y2, BAR_Y2 + 96
    SB_MID = (SB_Y1 + SB_Y2) // 2
    _band_h = SB_Y2 - SB_Y1
    _SEAM = 0   # straight vertical divider between the two teams (no diagonal)
    _lg_img = Image.new("RGB", (W, _band_h))
    _rg_img = Image.new("RGB", (W, _band_h))
    _lgd, _rgd = ImageDraw.Draw(_lg_img), ImageDraw.Draw(_rg_img)
    _lt, _lb = _lighten(tc1, 0.30), _darken(tc1, 0.80)
    _rt, _rb = _lighten(tc2, 0.30), _darken(tc2, 0.80)
    for _i in range(_band_h):
        _t = _i / _band_h
        _lgd.line([(0, _i), (W, _i)], fill=_lerp(_lt, _lb, _t))
        _rgd.line([(0, _i), (W, _i)], fill=_lerp(_rt, _rb, _t))
    _bmask = Image.new("L", (W, _band_h), 0)
    ImageDraw.Draw(_bmask).polygon(
        [(0, 0), (HALF + _SEAM, 0), (HALF - _SEAM, _band_h), (0, _band_h)], fill=255)
    img.paste(Image.composite(_lg_img, _rg_img, _bmask), (0, SB_Y1))
    d.line([(HALF + _SEAM, SB_Y1), (HALF - _SEAM, SB_Y2)], fill=c_gold_rgb, width=3)

    def _ov_str(td):
        b = td.get("balls", 0)
        if td.get("yet_to_bat") or b == 0: return f"{format_overs}.0"
        return f"{b // 6}.{b % 6}"

    def _draw_bat_icon(cx, cy, col):
        d.ellipse([(cx - 30, cy - 30), (cx + 30, cy + 30)], fill=c_white)
        d.line([(cx - 13, cy + 15), (cx + 4, cy - 3)], fill=col, width=9)
        d.line([(cx + 4, cy - 3), (cx + 12, cy - 11)], fill=col, width=3)
        d.ellipse([(cx + 10, cy - 14), (cx + 16, cy - 8)], fill=col)

    def _draw_ball_icon(cx, cy, col):
        d.ellipse([(cx - 30, cy - 30), (cx + 30, cy + 30)], fill=c_white)
        d.ellipse([(cx - 16, cy - 16), (cx + 16, cy + 16)], fill=col)
        d.line([(cx - 11, cy - 7), (cx + 11, cy + 9)], fill=c_white, width=2)
        d.line([(cx - 8, cy - 11), (cx + 9, cy + 2)], fill=c_white, width=1)

    _draw_bat_icon(38, SB_MID, tc1)
    _draw_ball_icon(W - 38, SB_MID, tc2)

    def _draw_score(td, half_x0, icon_side, chip_x, band_rgb):
        if td.get("yet_to_bat"):
            s = "YET TO BAT"
            d.text((half_x0 + (HALF - get_tw(s, f_score)) // 2, SB_MID - _th(f_score) // 2),
                   s, fill=c_white, font=f_score)
            return
        s = f"{td['runs']}-{td['wickets']}"
        # Score sits toward the outer edge, overs chip toward centre.
        s_cx = half_x0 + (285 if icon_side == "left" else HALF - 285)
        d.text((s_cx - get_tw(s, f_score) // 2, SB_MID - _th(f_score) // 2), s, fill=c_white, font=f_score)
        ov = f"{_ov_str(td)} OVERS"
        cw = get_tw(ov, f_ovr) + 24
        cy1, cy2 = SB_MID - 17, SB_MID + 17
        cx1 = chip_x if icon_side == "left" else chip_x - cw
        d.rounded_rectangle([(cx1, cy1), (cx1 + cw, cy2)], radius=8, fill=_darken(band_rgb))
        d.text((cx1 + 12, SB_MID - _th(f_ovr) // 2 - 1), ov, fill=c_white, font=f_ovr)

    _draw_score(t1_data, 0,    "left",  HALF - 150, tc1)
    _draw_score(t2_data, HALF, "right", HALF + 150, tc2)

    # 4 & 5. Stat grids (batters then bowlers)
    GRID_ROWS = 4
    ROW_H = 50
    GHDR_H = 34

    def _grid_col(offset_x):
        # returns (name_x, mid_col_x, right_col_x) for a half starting at offset_x
        return offset_x + 40, offset_x + HALF - 150, offset_x + HALF - 55

    def _ip_badge(bx, mid, col):
        bw = get_tw("IP", f_col) + 8
        bh = _th(f_col) + 6
        by = mid - bh // 2
        d.rounded_rectangle([(bx, by), (bx + bw, by + bh)], radius=3, fill=col)
        d.text((bx + 4, by + 3), "IP", fill=c_white, font=f_col)
        return bw + 6

    def _draw_grid(y_top, kind, col1_hdr, col2_hdr):
        # Batting strips use the batting team's colour. Bowling strips use the
        # BOWLING team's colour - and team 1's column lists team 2's bowlers
        # (they bowled to team 1), so the bowling-bar colours swap sides.
        left_col  = tc1 if kind == "bat" else tc2
        right_col = tc2 if kind == "bat" else tc1
        # Gradient strips: base colour at the outer edge -> darker toward the seam.
        _hgrad(0,    y_top, HALF, y_top + GHDR_H, left_col, _darken(left_col, 0.72))
        _hgrad(HALF, y_top, W,    y_top + GHDR_H, _darken(right_col, 0.72), right_col)
        hy = y_top + (GHDR_H - _th(f_col)) // 2
        for offset_x in (0, HALF):
            nx, c1, c2 = _grid_col(offset_x)
            d.text((nx, hy), "BATTER" if kind == "bat" else "BOWLER", fill=c_white, font=f_col)
            d.text((c1 - get_tw(col1_hdr, f_col) // 2, hy), col1_hdr, fill=c_white, font=f_col)
            d.text((c2 - get_tw(col2_hdr, f_col) // 2, hy), col2_hdr, fill=c_white, font=f_col)

        body_top = y_top + GHDR_H
        for i in range(GRID_ROWS):
            ry = body_top + i * ROW_H
            if i % 2 == 1:
                d.rectangle([(0, ry), (W, ry + ROW_H)], fill=c_alt)
            d.line([(0, ry + ROW_H), (W, ry + ROW_H)], fill=c_grid, width=1)
        d.line([(HALF, body_top), (HALF, body_top + GRID_ROWS * ROW_H)], fill=c_grid, width=2)

        def _rows(td, offset_x, col):
            nx, c1, c2 = _grid_col(offset_x)
            rows = td["batters"][:4] if kind == "bat" else td["bowlers"][:4]
            ip_name = (td.get("impact_sub") or "").upper()
            for i, r in enumerate(rows):
                mid = body_top + i * ROW_H + ROW_H // 2
                nm = r["name"][:22].upper()
                d.text((nx, mid - _th(f_player) // 2), nm, fill=c_navy, font=f_player)
                mx = nx + get_tw(nm, f_player) + 8
                if ip_name and r["name"].upper() == ip_name:
                    mx += _ip_badge(mx, mid, col)
                if potm and r["name"].upper() == potm.upper():
                    _star(mx + 9, mid, 9)
                if kind == "bat":
                    v1 = f"{r['runs']}{'*' if r.get('not_out') else ''}"
                    v2 = str(r["balls"])
                else:
                    v1 = f"{r['wickets']}-{r['runs']}"
                    v2 = r["overs"]
                d.text((c1 - get_tw(v1, f_runs) // 2, mid - _th(f_runs) // 2), v1, fill=c_navy, font=f_runs)
                d.text((c2 - get_tw(v2, f_balls) // 2, mid - _th(f_balls) // 2), v2, fill=c_grey, font=f_balls)

        _rows(t1_data, 0, left_col)
        _rows(t2_data, HALF, right_col)
        return body_top + GRID_ROWS * ROW_H

    bat_bottom = _draw_grid(SB_Y2, "bat", "R", "B")
    bwl_bottom = _draw_grid(bat_bottom, "bowl", "W-R", "O")

    # 6. Footer (result + POTM): team-tinted navy gradient
    _dt1, _dt2 = _darken(tc1, 0.42), _darken(tc2, 0.42)
    for x in range(W):
        t = x / (W - 1)
        col = _lerp(_dt1, c_navy_rgb, t * 2) if t < 0.5 else _lerp(c_navy_rgb, _dt2, (t - 0.5) * 2)
        d.line([(x, bwl_bottom), (x, H)], fill=col)
    fy = bwl_bottom + (H - bwl_bottom - _th(f_foot)) // 2
    if potm:
        sep = "   |   "
        potm_txt = f"POTM: {potm.upper()}"
        total = get_tw(result_str, f_foot) + get_tw(sep, f_foot) + get_tw(potm_txt, f_foot)
        cx = (W - total) // 2
        d.text((cx, fy), result_str, fill=c_white, font=f_foot)
        cx += get_tw(result_str, f_foot)
        d.text((cx, fy), sep, fill=c_grey, font=f_foot)
        cx += get_tw(sep, f_foot)
        d.text((cx, fy), potm_txt, fill=c_gold, font=f_foot)
    else:
        d.text(((W - get_tw(result_str, f_foot)) // 2, fy), result_str, fill=c_white, font=f_foot)

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf

def _ccodi_font(size, bold=True, italic=False):
    """CCODI scorecard font: DejaVu on the Linux host, Arial fallback locally."""
    variants = ([  # (path candidates) in preference order
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf" if bold else "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/Library/Fonts/Arial Bold.ttf" if bold else "/Library/Fonts/Arial.ttf",
    ])
    for p in variants:
        try:
            return ImageFont.truetype(p, size)
        except Exception:
            continue
    return ImageFont.load_default()


# CCODI scorecard layout (measured on assets/ccodi_scorecard.png, 1538×1022)
# One coordinate block per innings panel; edit the numbers here to nudge alignment.
_CCODI_PANELS = {
    1: {"flag_cy": 315, "name_cy": 398, "innings_cy": 438, "score_cy": 322,
        "overs_box": (290, 398, 500, 442), "overs_cy": 406,
        "extras_y": 484, "rr_y": 519, "bg": (2, 24, 96), "bat_y0": 279, "bowl_y0": 278},
    2: {"flag_cy": 653, "name_cy": 736, "innings_cy": 776, "score_cy": 660,
        "overs_box": (290, 736, 500, 780), "overs_cy": 744,
        "extras_y": 830, "rr_y": 865, "bg": (1, 65, 16), "bat_y0": 617, "bowl_y0": 623},
}
_CCODI_BAT_ROW_H = 41     # 5 batter rows (band ends at the EXTRAS/RR strip)
_CCODI_BOWL_ROW_H = 41    # 7 bowler rows seated in the template's baked grid cells (separators
                          # ~41px apart); first row aligns with batters, last sits at the RR line
_CCODI_LEFT_CX = 152     # centre of the flag / team-name / innings block
_CCODI_SCORE_CX = 393    # centre of the big total + overs block
_CCODI_FLAG_SZ = 108
_CCODI_EXTRAS_VX = 378   # x-start of the EXTRAS value (after the "EXTRAS" label ~x302-365)
_CCODI_RR_VX = 340       # x-start of the RR value (after the "RR" label ~x302-322)
# Column CENTRES, from the template grid separators - numbers are centre-aligned in
# each cell (matching the centred headers). Batters seps: 741,800,855,908,961,1020.
# Bowlers seps: 1205,1258,1309,1358,1408,1494. (name columns are left-aligned.)
_CCODI_BAT_COLS = {"name": 545, "R": 773, "B": 830, "4s": 884, "6s": 936, "SR": 995}
_CCODI_BOWL_COLS = {"name": 1040, "O": 1236, "M": 1284, "R": 1331, "W": 1381, "ECON": 1437}
_CCODI_ROW_H = 42


# Panel recolour boxes + the template's base fill hue (PIL HSV 0-255: blue≈170, green≈85).
_CCODI_RECOLOR = {
    1: {"box": (34, 216, 1504, 551), "hue_lo": 150, "hue_hi": 200},   # blue innings-1 panel
    2: {"box": (34, 555, 1504, 899), "hue_lo": 52, "hue_hi": 118},    # green innings-2 panel
}


def _ccodi_recolor_panel(img, box, hex_color, hue_lo, hue_hi):
    """Hue-shift a panel's SATURATED base-hue fill to the team's colour, leaving the
    crimson borders and stadium background untouched. Skips grey/undefined colours."""
    import colorsys
    from PIL import ImageChops
    try:
        r, g, b = (int(hex_color.lstrip("#")[i:i+2], 16) for i in (0, 2, 4))
    except Exception:
        return
    hh, ss, _vv = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
    if ss < 0.22:            # grey / default colour -> keep the template panel as-is
        return
    target_h = int(hh * 255)
    region = img.crop(box).convert("HSV")
    h, s, v = region.split()
    satmask = s.point(lambda p: 255 if p > 55 else 0)
    huemask = h.point(lambda p: 255 if hue_lo <= p <= hue_hi else 0)
    mask = ImageChops.multiply(satmask, huemask)
    h = Image.composite(Image.new("L", region.size, target_h), h, mask)
    img.paste(Image.merge("HSV", (h, s, v)).convert("RGB"), (box[0], box[1]))


def generate_ccodi_scorecard(match: CricketMatch) -> io.BytesIO:
    """CCODI-branded ODI scorecard onto assets/ccodi_scorecard.png from the live match
    (full 4s/6s/econ + extras breakdown). Panels tint to each team's colour."""
    img = Image.open("assets/ccodi_scorecard.png").convert("RGB")
    d = ImageDraw.Draw(img)
    f_name = _ccodi_font(28); f_score = _ccodi_font(52); f_overs = _ccodi_font(22, italic=True)
    f_inns = _ccodi_font(18); f_row = _ccodi_font(20, bold=False); f_rowb = _ccodi_font(21)
    f_bowl = _ccodi_font(19, bold=False); f_bowlb = _ccodi_font(20)   # 7-row bowler table (taller band)
    f_ext = _ccodi_font(19, bold=False); f_res = _ccodi_font(38)
    WHITE = "#FFFFFF"; DIM = "#C9D4F0"; GDIM = "#CDE8D2"

    def _rt(x, y, s, font, fill=WHITE):
        d.text((x - font.getbbox(str(s))[2], y), str(s), font=font, fill=fill)
    def _ct(cx, y, s, font, fill=WHITE):
        d.text((cx - font.getbbox(str(s))[2] / 2, y), str(s), font=font, fill=fill)

    # Team logo/colour lookup (the match's team dicts carry no logos -> read the tourney).
    tourney = None
    if getattr(match, "tournament_server_id", None):
        tourney = next((t for t in DB_CACHE.get("tournaments", []) if t.get("server_id") == match.tournament_server_id), None)
    def _team_meta(name):
        if not tourney: return None, "#334155"
        t = next((x for x in tourney.get("teams", []) if x["name"] == name), None)
        if not t: return None, "#334155"
        return (t.get("logo_match") or t.get("logo_standings")), t.get("color", "#334155")

    inns = [match.innings1]
    if match.current_innings_num == 2 and match.innings2:
        inns.append(match.innings2)

    # Tint each panel to its team's colour (before any text is drawn).
    for idx, inn in enumerate(inns, 1):
        _, colr = _team_meta(inn.batting_team["name"])
        RC = _CCODI_RECOLOR[idx]
        _ccodi_recolor_panel(img, RC["box"], colr, RC["hue_lo"], RC["hue_hi"])

    for idx, inn in enumerate(inns, 1):
        P = _CCODI_PANELS[idx]
        dim = DIM if idx == 1 else GDIM
        tname = inn.batting_team["name"]
        logo, colr = _team_meta(tname)

        # far-left block: flag/emoji · team name · INNINGS n
        sz = _CCODI_FLAG_SZ
        x0, y0 = int(_CCODI_LEFT_CX - sz / 2), int(P["flag_cy"] - sz / 2)
        crest = None
        try:
            crest = _fetch_emoji_img(logo, sz) if logo else None
        except Exception:
            crest = None
        if crest:
            img.paste(crest, (x0, y0), crest)
        else:
            try: fill = tuple(int(colr.lstrip("#")[i:i+2], 16) for i in (0, 2, 4))
            except Exception: fill = (51, 65, 85)
            d.rounded_rectangle([(x0, y0), (x0 + sz, y0 + sz)], radius=12, fill=fill)
            _ct(_CCODI_LEFT_CX, P["flag_cy"] - 18, tname[:3].upper(), f_name)
        _ct(_CCODI_LEFT_CX, P["name_cy"], tname.upper()[:14], f_name)
        _ct(_CCODI_LEFT_CX, P["innings_cy"], f"INNINGS {idx}", f_inns, dim)

        # middle: big total + overs (cover the template's placeholder, redraw)
        _ct(_CCODI_SCORE_CX, P["score_cy"], f"{inn.total_runs}/{inn.wickets}", f_score)
        overs = f"{inn.total_balls // 6}.{inn.total_balls % 6}"
        bx = P["overs_box"]
        d.rectangle(bx, fill=img.getpixel((bx[0] - 6, bx[1] - 4)))   # cover placeholder w/ tinted panel
        _ct(_CCODI_SCORE_CX, P["overs_cy"], f"{overs} OVERS", f_overs)

        # EXTRAS (with breakdown) + RR
        extras = getattr(inn, "extras", 0)
        byes = getattr(inn, "byes", 0); lb = getattr(inn, "legbyes", 0)
        nb = getattr(inn, "noballs", 0); wd = getattr(inn, "wides", 0)
        ext_txt = f"{extras}  (B {byes}, LB {lb}, NB {nb}, WD {wd})"
        d.text((_CCODI_EXTRAS_VX, P["extras_y"]), ext_txt, font=f_ext, fill=dim)
        rr = (inn.total_runs / inn.total_balls * 6) if inn.total_balls else 0.0
        d.text((_CCODI_RR_VX, P["rr_y"]), f"{rr:.2f}", font=f_ext, fill=dim)

        def _fit(s, font, maxw):   # truncate a name to fit a column width
            if font.getbbox(s)[2] <= maxw:
                return s
            while s and font.getbbox(s + "…")[2] > maxw:
                s = s[:-1]
            return s + "…"

        # batters: top 5 by runs, centred numbers, integer SR
        bats = sorted([b for b in inn.batting_stats.values() if b.balls_faced > 0 or b.dismissal != "not out"],
                      key=lambda x: x.runs_scored, reverse=True)[:5]
        y = P["bat_y0"]; C = _CCODI_BAT_COLS
        for b in bats:
            sr = (b.runs_scored / b.balls_faced * 100) if b.balls_faced else 0.0
            star = "*" if b.dismissal == "not out" else ""
            d.text((C["name"], y), _fit(f"{b.profile['name']}{star}", f_row, 190), font=f_row, fill=WHITE)
            _ct(C["R"], y, b.runs_scored, f_rowb)
            _ct(C["B"], y, b.balls_faced, f_row, dim)
            _ct(C["4s"], y, getattr(b, "fours", 0), f_row, dim)
            _ct(C["6s"], y, getattr(b, "sixes", 0), f_row, dim)
            _ct(C["SR"], y, f"{sr:.0f}", f_row, dim)
            y += _CCODI_BAT_ROW_H

        # bowlers: top 7 by wickets, tighter rows, smaller font
        bowls = sorted([b for b in inn.bowling_stats.values() if b.balls_bowled > 0],
                       key=lambda x: (x.wickets_taken, -x.runs_conceded), reverse=True)[:7]
        y = P["bowl_y0"]; C = _CCODI_BOWL_COLS
        for b in bowls:
            ov = f"{b.balls_bowled // 6}.{b.balls_bowled % 6}"
            econ = (b.runs_conceded / b.balls_bowled * 6) if b.balls_bowled else 0.0
            d.text((C["name"], y), _fit(b.profile["name"], f_bowl, 155), font=f_bowl, fill=WHITE)
            _ct(C["O"], y, ov, f_bowl, dim)
            _ct(C["M"], y, getattr(b, "maidens", 0), f_bowl, dim)
            _ct(C["R"], y, b.runs_conceded, f_bowl, dim)
            _ct(C["W"], y, b.wickets_taken, f_bowlb)
            _ct(C["ECON"], y, f"{econ:.2f}", f_bowl, dim)
            y += _CCODI_BOWL_ROW_H

    # result banner + POTM (bottom ribbon, y≈918-992) - only once the chase is done
    if len(inns) == 2:
        i1, i2 = match.innings1, match.innings2
        target = getattr(match, "target", i1.total_runs + 1)
        mw = _match_max_wickets(match)
        if getattr(match, "tiebreak_winner_name", None):
            res = f"{match.tiebreak_winner_name.upper()} WON (SUPER OVER)"
        elif i2.total_runs >= target:
            res = f"{i2.batting_team['name'].upper()} WON BY {mw - i2.wickets} WICKETS"
        elif i2.total_runs == target - 1:
            res = "MATCH TIED"
        else:
            res = f"{i1.batting_team['name'].upper()} WON BY {(target - 1) - i2.total_runs} RUNS"

        def _ctv(cx, cy, s, font, fill=WHITE):   # centred on BOTH axes (ribbon layout)
            bb = font.getbbox(str(s))
            d.text((cx - bb[2] / 2, cy - (bb[3] + bb[1]) / 2), str(s), font=font, fill=fill)

        potm = getattr(match, "_potm_name", None)
        if potm is None:
            try:
                potm = get_player_of_the_match(match)
            except Exception:
                potm = None
        if potm:
            _ctv(769, 942, res, _ccodi_font(34))
            _ctv(769, 977, f"PLAYER OF THE MATCH  •  {str(potm).upper()}", _ccodi_font(19), (240, 194, 66))
        else:
            _ctv(769, 955, res, f_res)   # no POTM -> single line, dead-centre of the ribbon

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf


def generate_tournament_score_image(match: CricketMatch) -> io.BytesIO:
    # CCODI has its own branded template rendered straight from the match object.
    if getattr(match, "tournament_type", None) == "ccodi":
        try:
            return generate_ccodi_scorecard(match)
        except Exception as _e:
            print(f"CCODI scorecard render failed, using generic: {_e}")
    return generate_scorecard_from_data(extract_scoreboard_data(match))


def generate_ccodi_scorecard_from_data(data: dict):
    """Rebuild a match-like object from STORED scorecard data (reconstruct_scorecard_data)
    and render the CCODI template - so `cvt match_scorecard` / `post_scorecards` show the
    branded card, not the generic one. Returns None for matches stored before the CCODI
    fields (no 4s/6s) - those keep the generic card rather than showing wrong zeros."""
    from types import SimpleNamespace as _NS
    t1, t2 = data.get("t1"), data.get("t2")
    if not t1 or not t2 or not t1.get("batters") or not t2.get("batters"):
        return None
    if all(b.get("fours") is None for b in t1["batters"] + t2["batters"]):
        return None   # pre-CCODI storage -> fall back to the generic card

    def _inn(td):
        bats = {}
        for b in td.get("batters", []):
            dis = b.get("dismissal") or ("not out" if b.get("not_out") else "c. Fielder")
            bats[b["name"]] = _NS(runs_scored=b["runs"], balls_faced=b["balls"],
                                  fours=b.get("fours") or 0, sixes=b.get("sixes") or 0,
                                  dismissal=dis, profile={"name": b["name"]})
        bowls = {}
        for w in td.get("bowlers", []):
            parts = str(w.get("overs", "0.0")).split(".")
            balls = int(parts[0]) * 6 + (int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0)
            bowls[w["name"]] = _NS(balls_bowled=balls, runs_conceded=w["runs"],
                                   wickets_taken=w["wickets"], maidens=w.get("maidens") or 0,
                                   profile={"name": w["name"]})
        ex = td.get("extras") or [0, 0, 0, 0, 0]
        return _NS(batting_team={"name": td.get("raw_name") or td["name"]},
                   total_runs=td["runs"], wickets=td["wickets"], total_balls=td.get("balls") or 0,
                   extras=ex[0], byes=ex[1], legbyes=ex[2], noballs=ex[3], wides=ex[4],
                   batting_stats=bats, bowling_stats=bowls)

    rs = data.get("result_str") or ""
    tiebreak = rs.split(" WON")[0].strip() if "(SUPER OVER)" in rs else None
    m = _NS(tournament_server_id=data.get("server_id"), tournament_type="ccodi",
            innings1=_inn(t1), innings2=_inn(t2), current_innings_num=2,
            target=t1["runs"] + 1, tiebreak_winner_name=tiebreak,
            _potm_name=data.get("potm"))
    return generate_ccodi_scorecard(m)

# ---- Match progression & loops ----

async def advance_match_loop(interaction, match: CricketMatch):
    innings = match.current_innings
    
    max_w = _match_max_wickets(match)
    if innings.wickets >= max_w or innings.total_balls >= match.max_balls or (match.current_innings_num == 2 and innings.total_runs >= getattr(match, "target", match.innings1.total_runs + 1)):
        await handle_innings_end(interaction, match)
    else:
        if match.simulation_mode == "whole_match":
            await loop_entire_match_simulation(interaction, match)
        elif match.simulation_mode == "interactive":
            await run_interactive_delivery_sequence(interaction, match)

async def loop_current_innings_simulation(interaction, match: CricketMatch):
    """Simulate the current innings only, then hand back to the Over Hub for the next innings."""
    channel = interaction.channel if hasattr(interaction, 'channel') else interaction

    while True:
        # /endmatch (or anything that tears the match down) must stop the sim INSTANTLY
        # the loop only holds a private reference, so re-check the registry every ball.
        if active_games.get(channel.id) is not match:
            return
        innings = match.current_innings
        max_w = _match_max_wickets(match)
        if innings.wickets >= max_w or innings.total_balls >= match.max_balls or (
                match.current_innings_num == 2 and
                innings.total_runs >= getattr(match, "target", match.innings1.total_runs + 1)):
            # Verbose: render the final partial over's card if the innings ended mid-over.
            if getattr(match, 'verbose', False) and innings.over_log:
                await channel.send(embed=render_embed_scoreboard(match))
                await asyncio.sleep(0.5)
            orig_sim_only = getattr(match, 'sim_only', False)
            match.sim_only = False   # always return to hub after this innings
            await handle_innings_end(interaction, match)
            match.sim_only = orig_sim_only
            break

        if innings.total_balls % 6 == 0 and not innings.over_log:
            for _ip_msg in try_ai_impact_player(match, innings):
                await channel.send(_ip_msg)
            # The bowler the user just picked at the hub bowls THIS over; the AI only
            # takes over from the following over onward.
            pending = getattr(match, '_pending_bowler', None)
            if pending:
                innings.current_bowler = pending
                match._pending_bowler = None
            else:
                new_bowler = get_smart_ai_bowler(innings, match.pitch, match.weather, match.format_overs)
                if not new_bowler:
                    await channel.send("🚨 **CRITICAL ERROR:** Could not find a valid bowler.")
                    if channel.id in active_games:
                        del active_games[channel.id]
                    return
                innings.current_bowler = new_bowler

        tb_before = innings.total_balls
        execute_ball_math(match)

        # Only run over-end housekeeping when a LEGAL ball actually completed the over.
        # A wide/no-ball leaves total_balls on the over boundary (6N); without this guard
        # the clear would wipe over_log mid-over and the next bowler-pick would re-select
        # a NEW bowler - the "verbose sim hands the over to a different bowler" bug.
        if innings.total_balls > tb_before and innings.total_balls % 6 == 0:
            if getattr(match, 'verbose', False):
                await channel.send(embed=render_embed_scoreboard(match))
                await asyncio.sleep(0.5)
            innings.over_log.clear()
            innings.bouncers_in_over = 0; innings.cutters_in_over = 0
            innings.mystery_bowled_this_over = False


async def loop_current_innings_bbb(interaction, match: CricketMatch):
    """Ball-by-ball verbose: post ONE live scoreboard per over and EDIT it after each
    delivery (at a readable pace), with a fresh card for every new over. Then hand back
    to the Over Hub for the next innings - same end-of-innings handling as the other sims."""
    channel = interaction.channel if hasattr(interaction, 'channel') else interaction
    BALL_DELAY = 1.3   # seconds between deliveries - fast enough to follow, slow enough to read

    def _innings_over(inn):
        mw = _match_max_wickets(match)
        if inn.wickets >= mw or inn.total_balls >= match.max_balls:
            return True
        if match.current_innings_num == 2 and inn.total_runs >= getattr(match, "target", match.innings1.total_runs + 1):
            return True
        return False

    match._bbb_active = True   # lets `cv verbose` know a bbb broadcast owns this match
    while True:
        # /endmatch must stop a ball-by-ball broadcast INSTANTLY.
        if active_games.get(channel.id) is not match:
            match._bbb_active = False
            return
        innings = match.current_innings

        if _innings_over(innings):
            match._bbb_active = False
            match._switch_to_verbose = False
            orig_sim_only = getattr(match, 'sim_only', False)
            match.sim_only = False   # return to the hub for the next innings
            await handle_innings_end(interaction, match)
            match.sim_only = orig_sim_only
            break

        # Pick the over's bowler at a true over start (over_log empty so wides don't re-pick).
        if innings.total_balls % 6 == 0 and not innings.over_log:
            for _ip_msg in try_ai_impact_player(match, innings):
                await channel.send(_ip_msg)
            # The hub-selected bowler gets THIS over; AI picks from the next over on.
            pending = getattr(match, '_pending_bowler', None)
            if pending:
                innings.current_bowler = pending
                match._pending_bowler = None
            else:
                new_bowler = get_smart_ai_bowler(innings, match.pitch, match.weather, match.format_overs)
                if not new_bowler:
                    await channel.send("🚨 **CRITICAL ERROR:** Could not find a valid bowler. Match stopped.")
                    if channel.id in active_games:
                        del active_games[channel.id]
                    match._bbb_active = False
                    return
                innings.current_bowler = new_bowler

        # Fresh scoreboard card for this over.
        try:
            over_msg = await channel.send(embed=render_embed_scoreboard(match))
        except Exception:
            over_msg = None

        # Bowl the over, one legal delivery at a time, editing the card after each.
        while True:
            if active_games.get(channel.id) is not match:
                match._bbb_active = False
                return   # /endmatch mid-over - stop dead, no more balls or edits
            if _innings_over(innings):
                break   # outer loop renders the final state + ends the innings cleanly
            tb_before = innings.total_balls
            execute_ball_math(match)
            await asyncio.sleep(BALL_DELAY)
            if over_msg is not None:
                try:
                    await over_msg.edit(embed=render_embed_scoreboard(match))
                except Exception:
                    pass
            # Over complete only when a LEGAL ball lands on the 6-ball boundary.
            if innings.total_balls > tb_before and innings.total_balls % 6 == 0:
                break

        # Reset per-over state for the next over's fresh card.
        innings.over_log.clear()
        innings.bouncers_in_over = 0; innings.cutters_in_over = 0
        innings.mystery_bowled_this_over = False

        # `cv verbose` was typed during this over: the over is now complete (or the
        # innings ended mid-over) - hand the REST of the match to the verbose sim.
        # sim_only=True keeps innings 2 auto-simming instead of returning to the hub;
        # match end still flows through handle_innings_end, so tournament stats,
        # standings and the result dispatch are recorded exactly as normal.
        if getattr(match, '_switch_to_verbose', False):
            match._switch_to_verbose = False
            match._bbb_active = False
            match.verbose = True
            match.sim_only = True
            await channel.send("📋 **Over complete — switching to verbose simulation.** The rest of the match will be simmed over-by-over.")
            await loop_entire_match_simulation(interaction, match)
            return


async def loop_entire_match_simulation(interaction, match: CricketMatch):
    channel = interaction.channel if hasattr(interaction, 'channel') else interaction
    
    while True:
        # /endmatch must stop a running whole-match sim INSTANTLY.
        if active_games.get(channel.id) is not match:
            return
        innings = match.current_innings
        max_w = _match_max_wickets(match)
        if innings.wickets >= max_w or innings.total_balls >= match.max_balls or (match.current_innings_num == 2 and innings.total_runs >= getattr(match, "target", match.innings1.total_runs + 1)):
            # Verbose: if the innings ended MID-over (winning run / last wicket on a non-6th
            # ball), that final partial over's card was never shown - render it before the
            # scorecard so the last over isn't skipped.
            if getattr(match, 'verbose', False) and innings.over_log:
                await channel.send(embed=render_embed_scoreboard(match))
                await asyncio.sleep(0.5)
            await handle_innings_end(interaction, match)
            break

        # Only select a new bowler at the TRUE start of a new over (over_log empty = no
        # deliveries yet this over, including wides). This prevents wides from triggering
        # a mid-over bowler swap when total_balls % 6 == 0.
        if innings.total_balls % 6 == 0 and not innings.over_log:
            for _ip_msg in try_ai_impact_player(match, innings):
                await channel.send(_ip_msg)
            # A hub-selected bowler gets THIS over; AI picks from the next over on.
            pending = getattr(match, '_pending_bowler', None)
            if pending:
                innings.current_bowler = pending
                match._pending_bowler = None
            else:
                new_bowler = get_smart_ai_bowler(innings, match.pitch, match.weather, match.format_overs)
                if not new_bowler:
                    await channel.send("🚨 **CRITICAL ERROR:** Could not find a valid bowler to continue simulation. Match has been stopped.")
                    if channel.id in active_games:
                        del active_games[channel.id]
                    return
                innings.current_bowler = new_bowler

        tb_before = innings.total_balls
        execute_ball_math(match)

        # After each completed over (6 LEGAL balls), reset over-specific state so the
        # next iteration's bowler-selection guard (not over_log) triggers correctly.
        # Gating on total_balls increasing stops a wide/no-ball on the over boundary
        # from wiping over_log mid-over (which re-picks a different bowler).
        if innings.total_balls > tb_before and innings.total_balls % 6 == 0:
            # Send verbose scoreboard BEFORE clearing over_log so the timeline is visible
            if getattr(match, 'verbose', False):
                await channel.send(embed=render_embed_scoreboard(match))
                await asyncio.sleep(0.5)
            innings.over_log.clear()
            innings.bouncers_in_over = 0; innings.cutters_in_over = 0
            innings.mystery_bowled_this_over = False
            
class ODISuperOverPrompt(discord.ui.View):
    def __init__(self, match):
        super().__init__(timeout=120)
        self.match = match
        
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.match.p1_id:
            await interaction.response.send_message("Only the Host can decide.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Play Super Over", style=discord.ButtonStyle.success)
    async def yes_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        await interaction.message.edit(view=None)
        await trigger_super_over(interaction.channel, self.match)

    @discord.ui.button(label="End as Tie", style=discord.ButtonStyle.danger)
    async def no_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        await interaction.message.edit(view=None)
        self.match.tie_accepted = True
        await handle_innings_end(interaction, self.match)

def _so_reorder_batting(innings, opener_names):
    """Put the two chosen openers at the crease for a Super Over innings by reordering a COPY
    of the batting team (never the shared main-match team). Resets indices + fresh batting_stats
    (safe: the innings hasn't started). Returns True on success, False to fall back to default order."""
    try:
        players = list(innings.batting_team["players"])
        chosen, seen = [], set()
        for nm in opener_names:
            p = next((x for x in players if x["name"] == nm), None)
            if p and p["name"] not in seen:
                chosen.append(p); seen.add(p["name"])
        if len(chosen) < 2:
            return False
        rest = [p for p in players if p["name"] not in seen]
        new_players = chosen[:2] + rest + chosen[2:]
        innings.batting_team = {**innings.batting_team, "players": new_players}
        innings.current_striker_idx = 0
        innings.current_non_striker_idx = 1
        innings.next_batter_idx = 2
        innings.batting_stats = {p["name"]: BatterStats(p) for p in new_players}
        return True
    except Exception as e:
        print(f"super over reorder failed: {e}")
        return False


def _so_auto_openers(innings):
    """Auto-pick the two highest-rated batters as Super Over openers (sim / AI sides)."""
    try:
        top2 = sorted(innings.batting_team["players"], key=lambda p: p.get("bat", 0), reverse=True)[:2]
        _so_reorder_batting(innings, [p["name"] for p in top2])
    except Exception as e:
        print(f"super over auto-openers failed: {e}")


class SuperOverOpenersView(discord.ui.View):
    """Lets the batting side pick its 2 Super Over openers. Mirrors XISelectView."""
    def __init__(self, match, batting_uid, channel):
        super().__init__(timeout=120)
        self.match = match
        self.uid = batting_uid
        self.channel = channel
        self.picked = []          # player names, in order
        self._done = False
        self.update_ui()

    def update_ui(self):
        self.clear_items()
        players = self.match.current_innings.batting_team["players"]
        if len(self.picked) < 2:
            opts = [discord.SelectOption(label=p["name"], description=f"{p['role'].split('_')[0]} · {p.get('archetype','')}".strip(" ·"), value=p["name"])
                    for p in players if p["name"] not in self.picked][:25]
            sel = discord.ui.Select(placeholder=f"Pick opener {len(self.picked)+1} of 2...", options=opts)
            sel.callback = self.select_cb
            self.add_item(sel)
        undo = discord.ui.Button(label="Undo", style=discord.ButtonStyle.secondary, disabled=not self.picked)
        undo.callback = self.undo_cb
        self.add_item(undo)
        conf = discord.ui.Button(label="Confirm Openers", style=discord.ButtonStyle.success, disabled=len(self.picked) < 2)
        conf.callback = self.confirm_cb
        self.add_item(conf)

    async def interaction_check(self, interaction: discord.Interaction):
        if interaction.channel.id not in active_games or active_games[interaction.channel.id] != self.match:
            await interaction.response.send_message("❌ This match has ended.", ephemeral=True)
            return False
        if interaction.user.id != self.uid and interaction.user.id != getattr(self.match, "manager_id", None):
            await interaction.response.send_message("❌ Only the batting side can pick the Super Over openers.", ephemeral=True)
            return False
        return True

    async def select_cb(self, interaction: discord.Interaction):
        val = interaction.data["values"][0]
        if val not in self.picked:
            self.picked.append(val)
        self.update_ui()
        await interaction.response.edit_message(view=self)

    async def undo_cb(self, interaction: discord.Interaction):
        if self.picked:
            self.picked.pop()
        self.update_ui()
        await interaction.response.edit_message(view=self)

    async def confirm_cb(self, interaction: discord.Interaction):
        if self._done:
            return
        self._done = True
        await interaction.response.edit_message(view=None)
        innings = self.match.current_innings
        if not _so_reorder_batting(innings, self.picked):
            _so_auto_openers(innings)   # safety fallback
        s = innings.batting_team["players"]
        await interaction.channel.send(f"🧤 **Super Over openers — {innings.batting_team['name']}:** {s[0]['name']} & {s[1]['name']}")
        await prompt_bowler_then_hub(interaction.channel, self.match)

    async def on_timeout(self):
        if self._done:
            return
        self._done = True
        try:
            _so_auto_openers(self.match.current_innings)   # default to top-2 and carry on
            s = self.match.current_innings.batting_team["players"]
            await self.channel.send(f"⏳ Openers not picked in time — defaulting to **{s[0]['name']} & {s[1]['name']}**.")
            await prompt_bowler_then_hub(self.channel, self.match)
        except Exception as e:
            print(f"super over openers timeout fallback failed: {e}")


async def begin_super_over_innings(channel, match):
    """Start a Super Over innings: AI/sim sides auto-pick openers; human sides pick interactively.
    Always proceeds to bowling even if anything goes wrong (never stalls the Super Over)."""
    innings = match.current_innings
    batting_uid = match.batting_first_id if getattr(match, "current_innings_num", 1) == 1 else match.bowling_first_id
    ai_bats = getattr(match, "is_ai_game", False) and batting_uid == getattr(match, "p2_id", None)
    auto = getattr(match, "sim_only", False) or getattr(match, "is_club", False) or batting_uid is None or ai_bats

    if auto:
        _so_auto_openers(innings)
        if getattr(match, "sim_only", False):
            await loop_entire_match_simulation(channel, match)
        else:
            s = innings.batting_team["players"]
            await channel.send(f"🤖 **Super Over openers — {innings.batting_team['name']}:** {s[0]['name']} & {s[1]['name']}")
            await prompt_bowler_then_hub(channel, match)
        return

    try:
        view = SuperOverOpenersView(match, batting_uid, channel)
        view.message = await channel.send(
            f"🧤 <@{batting_uid}> — pick your **2 Super Over openers** for **{innings.batting_team['name']}** (order = who's on strike):",
            view=view,
        )
    except Exception as e:
        print(f"super over openers view failed, using defaults: {e}")
        await prompt_bowler_then_hub(channel, match)


async def trigger_super_over(channel, match: CricketMatch):
    # type(match) keeps a ClubMatch Super Over a ClubMatch (per-player turn control).
    so_match = type(match)(match.p1, match.p2, match.p1_id, match.p2_id, match.team1, match.team2, format_overs=1, pitch=match.pitch, weather=match.weather)
    so_match.is_super_over = True
    if getattr(match, "is_club", False):
        so_match.is_club = True
        so_match._caps = getattr(match, "_caps", {})
        so_match._cap_a_id = getattr(match, "_cap_a_id", None)
        so_match._cap_b_id = getattr(match, "_cap_b_id", None)
        so_match._club_per_side = getattr(match, "_club_per_side", None)
    # Chain every super over back to the ORIGINAL match (not the previous super over),
    # so repeated super overs still finalize the main match and image the decisive result.
    so_match.original_match_object = getattr(match, "original_match_object", match)
    so_match.super_over_number = getattr(match, "super_over_number", 0) + 1
    so_match.sim_only = getattr(match, 'sim_only', False)
    so_match.verbose = getattr(match, 'verbose', True)
    so_match.batting_first_id = match.bowling_first_id
    so_match.bowling_first_id = match.batting_first_id
    so_match.innings1 = InningsState(match.innings2.batting_team, match.innings1.batting_team)
    so_match.current_innings = so_match.innings1
    so_match.tournament_server_id = getattr(match, "tournament_server_id", None)
    so_match.tournament_match_id = getattr(match, "tournament_match_id", None)
    so_match.manager_id = getattr(match, "manager_id", None)
    so_match.tournament_name = getattr(match, "tournament_name", "TOURNAMENT")
    so_match.tournament_type = getattr(match, "tournament_type", None)   # DSL realism carries into super overs
    active_games[channel.id] = so_match
    
    await channel.send("🚨 **SCORES ARE TIED!** 🚨\nGet ready for the **SUPER OVER!**\n*The team that batted second will bat first. Max 2 wickets.*")
    # Pick this innings' two openers (interactive) / auto-pick (sim/AI), then bowl.
    await begin_super_over_innings(channel, so_match)

async def _maybe_send_tbecs_ads(channel, match):
    """TBECS only: post the server's sponsor ads at an innings end. Silent for every
    other match type and when no ads are configured. Super-over innings are skipped
    so a tie-break doesn't spam the break ads again."""
    try:
        from league.tbecs_manager import is_tbecs_match, build_tbecs_ad_embeds
        if not is_tbecs_match(match) or getattr(match, "is_super_over", False):
            return
        sid = getattr(match, "tournament_server_id", None)
        if not sid:
            return
        embeds = build_tbecs_ad_embeds(sid)
        if embeds:
            await channel.send(embeds=embeds[:10])   # Discord caps a message at 10 embeds
    except Exception as _ad_err:
        print(f"TBECS ad send failed: {_ad_err}")


async def handle_innings_end(interaction_context, match: CricketMatch):
    if getattr(match, "is_debut", False):   # Career debut: never start innings 2 - score the trial.
        await handle_debut_end(interaction_context, match)
        return
    if getattr(match, "is_scenario", False):   # Solo scenario: single innings, then settle.
        await handle_scenario_end(interaction_context, match)
        return
    channel = interaction_context if isinstance(interaction_context, discord.TextChannel) else interaction_context.channel

    if match.current_innings_num == 1:
        img_buf = generate_final_score_image(match)
        if getattr(match, "tournament_server_id", None):
            img_buf = generate_tournament_score_image(match)
        else:
            img_buf = generate_final_score_image(match)
        file = discord.File(fp=img_buf, filename="innings1_score.png")
        embed_full = render_full_scorecard_embed(match, 1)
        
        match.current_innings_num = 2
        match.innings2 = InningsState(match.innings1.bowling_team, match.innings1.batting_team)
        match.current_innings = match.innings2
        
        # DLS INTERRUPT SYSTEM
        target = match.innings1.total_runs + 1
        dls_msg = ""
        rain_chances = {"Light Rain": 0.15, "Drizzle": 0.30, "Heavy Rain": 0.65, "Thunderstorm": 0.85}
        if match.weather in rain_chances and random.random() < rain_chances[match.weather]:
            if match.weather in ["Light Rain", "Drizzle"]:
                # Minor delay: Lose at most 15% of the match
                lost_overs = random.randint(1, max(1, int(match.format_overs * 0.15)))
            else:
                # Major delay: Lose between 15% and 45% of the match
                lost_overs = random.randint(max(2, int(match.format_overs * 0.15)), max(2, int(match.format_overs * 0.45)))
                
            revised_overs = match.format_overs - lost_overs
            match.original_format_overs = match.format_overs
            match.format_overs = revised_overs
            match.max_balls = revised_overs * 6
            
            # Basic DLS Target Approximation (Resources scale non-linearly)
            resource_remaining = revised_overs / (revised_overs + lost_overs)
            target = int(match.innings1.total_runs * (resource_remaining ** 0.85)) + 1
            match.target = target
            match.dls_active = True
            dls_msg = f"\n\n🌧️ **RAIN DELAY!** Play was interrupted.\nThe match has been reduced to **{revised_overs} overs**.\n🎯 **Revised DLS Target:** **{target} runs**."
        else:
            match.target = target
            
        await channel.send(
            f"🏁 **Innings 1 Complete!** Target set: **{match.innings1.total_runs + 1} runs** to win.{dls_msg}\nHere is the detailed scorecard and broadcast graphic:",
            embed=embed_full,
            file=file
        )

        # TBECS innings-break ads (no-op for every other match type).
        await _maybe_send_tbecs_ads(channel, match)

        # Reset per-innings sim controls so the 2nd innings starts FRESH at the hub.
        # Otherwise a "Sim Innings (Verbose)" / whole-match pick from the 1st innings leaks
        # in - e.g. "Sim 1 Over" in the 2nd innings would auto-sim the whole innings verbose.
        if not getattr(match, 'sim_only', False):
            match.simulation_mode = "interactive"
            match.verbose = False

        # Pass channel directly - no more DummyInteraction needed
        if getattr(match, 'is_super_over', False):
            # Super Over innings 2: pick openers (interactive) / auto (sim·AI), then bowl.
            await begin_super_over_innings(channel, match)
        elif getattr(match, 'sim_only', False):
            await channel.send("*Simulating 2nd Innings... ⚙️*")
            await loop_entire_match_simulation(channel, match)
        elif getattr(match, "is_club", False):
            await prompt_club_openers(channel, match)
        else:
            await prompt_bowler_then_hub(channel, match)

    else:
        inn1 = match.innings1
        inn2 = match.innings2
        target = getattr(match, "target", inn1.total_runs + 1)
        is_tied = (inn2.total_runs == target - 1)
        
        if is_tied and not getattr(match, "tie_accepted", False) and not getattr(match, 'is_super_over', False):
            # DLS may have reduced format_overs - judge the tie rule by the ORIGINAL format.
            if getattr(match, "original_format_overs", match.format_overs) != 50:
                # Show the completed (tied) scoreboard before the Super Over begins.
                try:
                    _tie_img = generate_tournament_score_image(match) if getattr(match, "tournament_server_id", None) else generate_final_score_image(match)
                    await channel.send(
                        "🤝 **SCORES LEVEL — THE MATCH IS TIED!** Final scoreboard before the Super Over:",
                        file=discord.File(fp=_tie_img, filename="tied_scoreboard.png"),
                    )
                except Exception as _tie_err:
                    print(f"Tied scoreboard render failed: {_tie_err}")
                await trigger_super_over(channel, match)
                return

            else:
                await channel.send("🏆 **The Match has TIED!** Do you want to play a Super Over?", view=ODISuperOverPrompt(match))
                return
        if is_tied and getattr(match, 'is_super_over', False):
            _son = getattr(match, "super_over_number", 1)
            await channel.send(f"🤯 **SUPER OVER #{_son} IS TIED TOO!** On to **Super Over #{_son + 1}**!")
            await trigger_super_over(channel, match)
            return

        match_to_finalize = match
        is_so_finish = getattr(match, 'is_super_over', False) and hasattr(match, 'original_match_object')
        if is_so_finish:
            original_match = match.original_match_object
            so_winner_name = match.innings2.batting_team['name'] if match.innings2.total_runs > match.innings1.total_runs else match.innings1.batting_team['name']
            original_match.tiebreak_winner_name = so_winner_name
            match_to_finalize = original_match

        # Increment counter BEFORE generating image so the scorecard shows the correct match number.
        # Skip super overs - they're continuations, not standalone matches.
        if (not getattr(match_to_finalize, 'is_super_over', False)
                and not getattr(match_to_finalize, 'is_player_test', False)):
            _base = getattr(match_to_finalize, 'original_format_overs', match_to_finalize.format_overs)
            _increment_match_count("odi" if _base == 50 else "t20")
            try:
                gstats.record_limited_overs_match(match_to_finalize)
            except Exception as _gs_err:
                print(f"Global stats record failed: {_gs_err}")

        # At a Super Over's end, show the SUPER OVER's own summary (the main-match scoreboard was
        # already shown when scores tied). A normal match shows itself. Result recording below
        # always uses match_to_finalize (the main match) so stats/standings/bracket stay correct.
        img_match = match if is_so_finish else match_to_finalize
        try:
            if getattr(img_match, "tournament_server_id", None):
                img_buf = generate_tournament_score_image(img_match)
            else:
                img_buf = generate_final_score_image(img_match)
            embed_full = render_full_scorecard_embed(img_match, 2)
        except Exception as _img_err:
            print(f"Summary image failed ({'super over' if is_so_finish else 'match'}): {_img_err}")
            img_buf = generate_final_score_image(match_to_finalize)
            embed_full = render_full_scorecard_embed(match_to_finalize, 2)

        file = discord.File(fp=img_buf, filename="final_scoreboard.png")
        if is_so_finish:
            _son = getattr(match, "super_over_number", 1)
            header = f"🤯 **SUPER OVER #{_son} — {match_to_finalize.tiebreak_winner_name} win it!** Super Over summary:"
        else:
            header = "🏆 **Match over! Here is the final detailed scorecard and broadcast graphic:**"
        await channel.send(header, embed=embed_full, file=file)

        # TBECS second-innings (match end) ads - shown at every innings end per spec.
        await _maybe_send_tbecs_ads(channel, match_to_finalize)

        # Send scorecard to match log channel if configured for this server
        if channel.guild:
            log_channel_id = get_match_log_channel(str(channel.guild.id))
            if log_channel_id:
                try:
                    log_channel = bot.get_channel(int(log_channel_id))
                    if log_channel:
                        img_buf.seek(0)
                        log_file = discord.File(fp=img_buf, filename="final_scoreboard.png")
                        t1 = match_to_finalize.innings1.batting_team["name"]
                        t2 = match_to_finalize.innings2.batting_team["name"]
                        await log_channel.send(
                            f"📋 **Match Log** · {t1} vs {t2} · <#{channel.id}>",
                            file=log_file
                        )
                except Exception as _log_err:
                    print(f"Match log send failed: {_log_err}")

        if getattr(match_to_finalize, "tournament_server_id", None):
            try:
                match_to_finalize._scorecard_players = extract_scorecard_players(match_to_finalize)
            except Exception as _e:
                print(f"Could not extract scorecard players: {_e}")
                match_to_finalize._scorecard_players = None

        if getattr(match_to_finalize, "is_club", False) and _CAREER_OK:
            try:
                await _club_match_payout(channel, match_to_finalize)
            except Exception as _e:
                print(f"Club match payout failed: {_e}")

        if getattr(match_to_finalize, "is_draft", False):
            try:
                await _record_draft_result(channel, match_to_finalize)
            except Exception as _e:
                print(f"Draft result record failed: {_e}")

        if channel.id in active_games:
            del active_games[channel.id]

        if getattr(match_to_finalize, "tournament_server_id", None):
            bot.dispatch("tournament_match_complete", match_to_finalize, channel)

# ---- Over hub & interactive menus ----

def _insert_next_batter(innings, match, sel_idx):
    """Bring the chosen batter (currently at sel_idx) into the next batting slot and put
    them at the OUT batter's crease end.

    Why not just `current_striker_idx = next_batter_idx`: if the wicket fell on the LAST
    ball of an over, the engine has already done its end-of-over strike swap using the
    dismissed batter's index - so the out batter (prev_striker_idx) is now sitting at the
    NON-striker index and the surviving partner is the striker. Blindly overwriting the
    striker would orphan the survivor (he shows as a 3rd batter on the card and the
    non-striker pointer keeps pointing at a dismissed man, which also makes _solo_batting
    read True forever and kills all further strike rotation). So we replace whichever
    crease end currently points at the out batter."""
    players = innings.batting_team["players"]
    nb = innings.next_batter_idx
    players[nb], players[sel_idx] = players[sel_idx], players[nb]
    innings.next_batter_idx += 1
    out_idx = getattr(match, "prev_striker_idx", innings.current_striker_idx)
    if innings.current_non_striker_idx == out_idx:
        innings.current_non_striker_idx = nb     # over-end wicket: new man to the non-striker end
    else:
        innings.current_striker_idx = nb         # normal wicket: new man takes strike


async def prompt_next_batter(interaction, match: CricketMatch):
    channel = interaction.channel if hasattr(interaction, 'channel') else interaction
    uid = match.get_striker_user_id()
    innings = match.current_innings
    if getattr(match, "is_debut", False):   # Career debut: the trial ends the moment YOU are out.
        bs = innings.batting_stats.get(match.debut_player_name)
        if bs and bs.dismissal != "not out":
            await handle_debut_end(interaction, match)
            return
    if getattr(match, "is_scenario", False):   # Scenario: ends the moment YOU are out.
        bs = innings.batting_stats.get(match.scenario_player_name)
        if bs and bs.dismissal != "not out":
            await handle_scenario_end(interaction, match)
            return
    # Innings is over once the wicket cap is reached - Super Over = 2, normal = 10.
    # (Without this, a Super Over wouldn't stop at 2 because the rest of the XI is still
    #  "available", so it would try to send a 3rd batter and crash the flow.)
    if innings.wickets >= _match_max_wickets(match):
        await handle_innings_end(interaction, match)
        return
    is_club = getattr(match, "is_club", False)
    if is_club:
        uid = match.batting_captain_id() or uid

    # Players who can still come in: not yet batted AND not out.
    available = [p for p in innings.batting_team["players"][innings.next_batter_idx:]
                 if innings.batting_stats[p["name"]].dismissal == "not out"]

    if not available:
        if is_club:
            # Career last-man rule: if the non-striker survivor is still in, he bats ALONE.
            try:
                ns_idx = innings.current_non_striker_idx
                ns = innings.batting_team["players"][ns_idx]
                if innings.batting_stats[ns["name"]].dismissal == "not out":
                    out_idx = innings.current_striker_idx
                    innings.current_striker_idx = ns_idx        # survivor takes strike
                    innings.current_non_striker_idx = out_idx    # out placeholder => solo detected
                    await channel.send(f"🧍 **{ns['name']}** is the last man in — batting **alone** (keeps strike on odd runs)!")
            except Exception:
                pass
        await run_interactive_delivery_sequence(interaction, match)
        return

    if is_club and _is_bot_uid(uid):
        # Bot captain sends in the best available batsman.
        best = max(available, key=lambda p: p["bat"])
        idx = next(i for i, p in enumerate(innings.batting_team["players"]) if p["name"] == best["name"])
        _insert_next_batter(innings, match, idx)
        await channel.send(f"🤖 **{innings.batting_team['name']}** (bot) sends in **{best['name']}**.")
        await run_interactive_delivery_sequence(interaction, match)
        return

    options = []
    for p in available:
        role_short = p["role"].split("_")[0]
        options.append(discord.SelectOption(label=p["name"], description=f"{role_short} · {p.get('archetype','')}".strip(" ·"), value=p["name"]))

    view = discord.ui.View(timeout=300)
    select = discord.ui.Select(placeholder="Select the next batter…", options=options[:25])

    async def interaction_check(inter: discord.Interaction) -> bool:
        if inter.channel.id not in active_games or active_games[inter.channel.id] != match:
            await inter.response.send_message("❌ Match ended.", ephemeral=True)
            return False
        if inter.user.id != uid and inter.user.id != getattr(match, "manager_id", None):
            await inter.response.send_message("❌ Only the batting captain selects the next batter." if is_club else "Not your turn.", ephemeral=True)
            return False
        return True
    view.interaction_check = interaction_check

    async def cb(inter: discord.Interaction):
        if getattr(view, "_picked", False):     # guard against a double-tap selecting two batters
            return await inter.response.defer()
        view._picked = True
        sel_name = select.values[0]
        idx = next(i for i, p in enumerate(innings.batting_team["players"]) if p["name"] == sel_name)
        _insert_next_batter(innings, match, idx)
        await inter.response.defer()
        await inter.message.edit(view=None)
        await run_interactive_delivery_sequence(inter, match)

    select.callback = cb
    view.add_item(select)

    who = "batting captain" if is_club else "batting team"
    msg = f"🧢 <@{uid}> ({who}) — select the next batter to walk in:"
    if getattr(match, "impact_player", False):
        msg += "\n💡 *(Need to sub someone in? Run `/impactplayer` first!)*"
    await channel.send(msg, view=view)

async def prompt_new_over_bowler(interaction, match: CricketMatch):
    innings = match.current_innings
    bowler_uid = match.get_bowler_user_id()
    if getattr(match, "is_club", False):
        bowler_uid = match.bowling_captain_id() or bowler_uid
    channel = interaction.channel if hasattr(interaction, 'channel') else interaction
    
    if match.is_ai_game and bowler_uid == match.p2_id:
        for _ip_msg in try_ai_impact_player(match, innings):
            await channel.send(_ip_msg)
        new_bowler = get_smart_ai_bowler(innings, match.pitch, match.weather, match.format_overs)
        if not new_bowler:
            await channel.send("🚨 **CRITICAL ERROR:** Could not find a valid bowler to proceed. The match cannot continue. Please use `/endmatch`.")
            return
        innings.current_bowler = new_bowler
        innings.over_log.clear()
        innings.bouncers_in_over = 0; innings.cutters_in_over = 0
        innings.mystery_bowled_this_over = False
        
        class DummyInt: pass
        dummy = DummyInt()
        dummy.channel = channel
        await run_interactive_delivery_sequence(dummy, match)
        return

    actual_bowlers = []
    for p in innings.bowling_team["players"]:
        if not getattr(innings.bowling_stats.get(p["name"]), "is_subbed_out", False):
            if "Bowler" in p["role"] or "All-Rounder" in p["role"]:
                actual_bowlers.append(p)
            
    options = []
    bowler_quota = getattr(match, "bowler_quota", None) or max(1, (match.format_overs + 4) // 5)
    for p in actual_bowlers:
        stats = innings.bowling_stats[p["name"]]
        rem = bowler_quota - (stats.balls_bowled // 6)
        
        if rem <= 0:
            suffix = " (Quota Full)"
        elif innings.current_bowler and innings.current_bowler["name"] == p["name"]:
            suffix = f" ({rem} Over Rem) - Prev"
        else:
            suffix = f" ({rem} Over Rem)"
            
        options.append(discord.SelectOption(label=f"{p['name']}{suffix}", value=p["name"]))
        
    view = discord.ui.View()
    select = discord.ui.Select(placeholder="Select Bowler for Next Over...", options=options[:25])
    
    async def b_callback(inter: discord.Interaction):
        b_name = select.values[0]
        b_stats = innings.bowling_stats[b_name]
        
        if b_stats.balls_bowled // 6 >= bowler_quota or (innings.current_bowler and innings.current_bowler["name"] == b_name):
            await inter.response.send_message("❌ Illegal selection.", ephemeral=True)
            return
            
        innings.current_bowler = next(p for p in innings.bowling_team["players"] if p["name"] == b_name)
        innings.over_log.clear()
        innings.bouncers_in_over = 0; innings.cutters_in_over = 0
        innings.mystery_bowled_this_over = False
        await inter.response.defer()
        await inter.message.edit(view=None)
        await run_interactive_delivery_sequence(inter, match)
        
    select.callback = b_callback
    view.add_item(select)
    
    async def interaction_check(inter: discord.Interaction) -> bool:
        if inter.channel.id not in active_games or active_games[inter.channel.id] != match:
            await inter.response.send_message("❌ This match has been ended.", ephemeral=True)
            return False
        if inter.user.id != bowler_uid and inter.user.id != getattr(match, "manager_id", None):
            await inter.response.send_message("Not your turn.", ephemeral=True)
            return False
        return True
    view.interaction_check = interaction_check
    
    msg = f"🏏 <@{bowler_uid}>, select bowler for Over {innings.total_balls // 6 + 1}:"
    if getattr(match, "impact_player", False):
        msg += "\n💡 *(Need to sub someone in? Run `/impactplayer` first!)*"
    await channel.send(msg, view=view)

async def prompt_bowler_then_hub(interaction, match: CricketMatch):
    """Show bowler select dropdown first, then over hub. For AI-bowling games, AI picks and hub shows directly."""
    innings = match.current_innings
    channel = interaction.channel if hasattr(interaction, 'channel') else interaction
    bowler_uid = match.get_bowler_user_id()
    if getattr(match, "is_club", False):
        bowler_uid = match.bowling_captain_id() or bowler_uid

    # AI game where AI is bowling: AI picks, reset over state, show hub directly
    if match.is_ai_game and bowler_uid == match.p2_id:
        for _ip_msg in try_ai_impact_player(match, innings):
            await channel.send(_ip_msg)
        new_bowler = get_smart_ai_bowler(innings, match.pitch, match.weather, match.format_overs)
        if not new_bowler:
            await channel.send("🚨 **CRITICAL ERROR:** Could not find a valid bowler to proceed. The match cannot continue. Please use `/endmatch`.")
            return
        innings.current_bowler = new_bowler
        innings.over_log.clear()
        innings.bouncers_in_over = 0; innings.cutters_in_over = 0
        innings.mystery_bowled_this_over = False
        # Career debut / scenario / club: no Sim hub - go straight to interactive play.
        if getattr(match, "is_debut", False) or getattr(match, "is_club", False) or getattr(match, "is_scenario", False):
            await run_interactive_delivery_sequence(interaction, match)
        else:
            await prompt_over_pacing_hub(interaction, match)
        return

    # Bowling scenario: YOU bowl every over - auto-select the player, no dropdown/quota.
    if getattr(match, "is_scenario", False) and getattr(match, "scenario_mode", "bat") == "bowl":
        innings.current_bowler = innings.bowling_team["players"][0]
        innings.over_log.clear()
        innings.bouncers_in_over = 0; innings.cutters_in_over = 0
        innings.mystery_bowled_this_over = False
        await run_interactive_delivery_sequence(interaction, match)
        return

    # Club match with a BOT captain bowling: bot auto-picks the next bowler.
    if getattr(match, "is_club", False) and _is_bot_uid(bowler_uid):
        new_bowler = get_smart_ai_bowler(innings, match.pitch, match.weather, match.format_overs)
        if not new_bowler:
            new_bowler = next((p for p in innings.bowling_team["players"]
                               if not innings.current_bowler or p["name"] != innings.current_bowler["name"]), None)
        if not new_bowler:
            await channel.send("🚨 No valid bowler for the bot — ending. Use `cv endmatch`.")
            return
        innings.current_bowler = new_bowler
        innings.over_log.clear()
        innings.bouncers_in_over = 0; innings.cutters_in_over = 0
        innings.mystery_bowled_this_over = False
        await channel.send(f"🤖 **{innings.bowling_team['name']}** (bot) brings on **{new_bowler['name']}**.")
        await run_interactive_delivery_sequence(interaction, match)
        return

    # Human bowling: show bowler select, then show hub after selection
    actual_bowlers = [p for p in innings.bowling_team["players"]
                      if not getattr(innings.bowling_stats.get(p["name"]), "is_subbed_out", False)
                      and ("Bowler" in p["role"] or "All-Rounder" in p["role"])]

    bowler_quota = getattr(match, "bowler_quota", None) or max(1, (match.format_overs + 4) // 5)
    options = []
    for p in actual_bowlers:
        stats = innings.bowling_stats[p["name"]]
        rem = bowler_quota - (stats.balls_bowled // 6)
        if rem <= 0:
            suffix = " (Quota Full)"
        elif innings.current_bowler and innings.current_bowler["name"] == p["name"]:
            suffix = f" ({rem} Over Rem) - Prev"
        else:
            suffix = f" ({rem} Over Rem)"
        options.append(discord.SelectOption(label=f"{p['name']}{suffix}", value=p["name"]))

    if not options:
        await channel.send("🚨 **ERROR:** No eligible bowlers available.")
        return

    # No timeout: a bowler pick can sit past 5 min in a live match (someone steps away);
    # a discarded view would freeze the over, and the match can't progress without a bowler.
    view = discord.ui.View(timeout=None)
    select = discord.ui.Select(
        placeholder=f"🎳 Pick bowler for Over {innings.total_balls // 6 + 1}...",
        options=options[:25]
    )

    async def b_callback(inter: discord.Interaction):
        b_name = select.values[0]
        b_stats = innings.bowling_stats[b_name]
        if b_stats.balls_bowled // 6 >= bowler_quota or (innings.current_bowler and innings.current_bowler["name"] == b_name):
            try:
                await inter.response.send_message("❌ Illegal selection (quota full or same bowler back-to-back).", ephemeral=True)
            except discord.NotFound:
                pass
            return
        match._pending_bowler = next(p for p in innings.bowling_team["players"] if p["name"] == b_name)
        # The interaction token can be dead (10062) if the loop stalled between render and
        # click. defer()/edit then 404 - but the downstream flow only needs the channel
        # (bot token), so swallow the failure and keep the over progressing instead of
        # crashing the match. The dropdown is removed via the bot-token message edit below.
        try:
            await inter.response.defer()
        except discord.NotFound:
            pass
        try:
            await inter.message.edit(view=None)
        except discord.HTTPException:
            pass
        if getattr(match, "is_club", False):
            # Club matches are interactive-only - no Sim hub. Apply the bowler and bowl.
            innings.current_bowler = match._pending_bowler
            match._pending_bowler = None
            innings.over_log.clear()
            innings.bouncers_in_over = 0; innings.cutters_in_over = 0
            innings.mystery_bowled_this_over = False
            await run_interactive_delivery_sequence(inter, match)
        else:
            await prompt_over_pacing_hub(inter, match)

    select.callback = b_callback
    view.add_item(select)

    async def interaction_check(inter: discord.Interaction) -> bool:
        if inter.channel.id not in active_games or active_games[inter.channel.id] != match:
            await inter.response.send_message("❌ This match has been ended.", ephemeral=True)
            return False
        if inter.user.id != bowler_uid and inter.user.id != getattr(match, "manager_id", None):
            await inter.response.send_message("❌ Not your turn.", ephemeral=True)
            return False
        return True
    view.interaction_check = interaction_check

    msg = f"🎳 <@{bowler_uid}>, pick your bowler for **Over {innings.total_balls // 6 + 1}**:"
    if getattr(match, "impact_player", False):
        msg += "\n💡 *(Need to sub someone in? Run `/impactplayer` first!)*"
    await channel.send(msg, view=view)

async def prompt_over_pacing_hub(interaction, match: CricketMatch):
    view = OverControlHubView(match)
    embed = render_embed_scoreboard(match)
    channel = interaction.channel if hasattr(interaction, 'channel') else interaction

    msg = f"⚡ <@{match.p1_id}> **Over Hub** - How to progress the next 6 deliveries?"
    if getattr(match, "impact_player", False):
        msg += "\n💡 **TIP:** Any player can use the `🔄 Impact Player` button below to make a sub!"

    # Tournament-branded over hub: show the event logo as the embed thumbnail.
    _logo_file = None
    _tid = getattr(match, "tournament_server_id", None)
    if _tid:
        _tv = get_server_tournament(_tid)
        _tt = _tv.get("tournament_type") if _tv else None
        if _tt == "t20_world_cup":
            _logo_file = "assets/t20_logo.png"
        elif _tt == "acl":
            _logo_file = "assets/acl_logo.png"

    if _logo_file:
        _fname = os.path.basename(_logo_file)
        embed.set_thumbnail(url=f"attachment://{_fname}")
        await channel.send(msg, embed=embed, view=view, file=discord.File(_logo_file))
    else:
        await channel.send(msg, embed=embed, view=view)

class OverControlHubView(discord.ui.View):
    def __init__(self, match: CricketMatch):
        super().__init__(timeout=300)
        self.match = match
        
        if getattr(match, "impact_player", False):
            btn = discord.ui.Button(label="🔄 Impact Player", style=discord.ButtonStyle.secondary, row=1, custom_id="impact_btn")
            btn.callback = self.impact_btn
            self.add_item(btn)
        
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.channel.id not in active_games or active_games[interaction.channel.id] != self.match:
            await interaction.response.send_message("❌ This match has been ended.", ephemeral=True)
            return False
            
        if interaction.data.get("custom_id") == "impact_btn":
            if interaction.user.id in [self.match.p1_id, self.match.p2_id]: return True
            await interaction.response.send_message("❌ You are not playing in this match.", ephemeral=True)
            return False
            
        if interaction.user.id != self.match.p1_id and interaction.user.id != getattr(self.match, "manager_id", None):
            await interaction.response.send_message("❌ Host only.", ephemeral=True)
            return False
        return True
        
    @discord.ui.button(label="Play Interactive Over", style=discord.ButtonStyle.success)
    async def play_over(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        await interaction.message.edit(view=None)
        self.match.simulation_mode = "interactive"
        innings = self.match.current_innings
        pending = getattr(self.match, '_pending_bowler', None)
        if pending:
            innings.current_bowler = pending
            self.match._pending_bowler = None
            innings.over_log.clear()
            innings.bouncers_in_over = 0; innings.cutters_in_over = 0
            innings.mystery_bowled_this_over = False
        await run_interactive_delivery_sequence(interaction, self.match)
        
    @discord.ui.button(label="Simulate 1 Over", style=discord.ButtonStyle.primary)
    async def sim_over(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        await interaction.message.edit(view=None)
        innings = self.match.current_innings
        start_runs = innings.total_runs; start_wkts = innings.wickets

        prev_mode = self.match.simulation_mode
        self.match.simulation_mode = "whole_match"

        # Apply the pre-selected bowler (set in prompt_bowler_then_hub); fall back to AI if missing
        pending = getattr(self.match, '_pending_bowler', None)
        if pending:
            innings.current_bowler = pending
            self.match._pending_bowler = None
            innings.over_log.clear()
            innings.bouncers_in_over = 0; innings.cutters_in_over = 0
            innings.mystery_bowled_this_over = False
        elif not innings.current_bowler:
            new_bowler = get_smart_ai_bowler(innings, self.match.pitch, self.match.weather, self.match.format_overs)
            if not new_bowler:
                channel = interaction.channel if hasattr(interaction, 'channel') else interaction
                await channel.send("🚨 **CRITICAL ERROR:** Could not find a valid bowler.")
                return
            innings.current_bowler = new_bowler
            innings.over_log.clear()
            innings.bouncers_in_over = 0; innings.cutters_in_over = 0
            innings.mystery_bowled_this_over = False
            
        target_balls = (innings.total_balls // 6 + 1) * 6
            
        while True:
            max_w = 2 if getattr(self.match, 'is_super_over', False) else 10
            if innings.wickets >= max_w or innings.total_balls >= self.match.max_balls: break
            if self.match.current_innings_num == 2 and innings.total_runs >= getattr(self.match, "target", self.match.innings1.total_runs + 1): break
            if innings.total_balls >= target_balls: break
            
            execute_ball_math(self.match)
                
        self.match.simulation_mode = prev_mode
        
        events_str = ' '.join(innings.over_log) if innings.over_log else "Maiden"
        await interaction.channel.send(f"⏩ **Simulated Over Complete!**\n**Timeline:** {events_str}\n**Yield:** {innings.total_runs - start_runs} Runs, {innings.wickets - start_wkts} Wickets")
        await advance_match_loop(interaction, self.match)
        
    @discord.ui.button(label="⏩ Sim Innings", style=discord.ButtonStyle.danger)
    async def sim_innings_fast(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        await interaction.message.edit(view=None)
        self.match.simulation_mode = "whole_match"
        self.match.verbose = False
        # _pending_bowler is kept: the sim loop gives the hub-selected bowler the
        # first over (it used to be discarded here, silently handing the over to AI).
        innings = self.match.current_innings
        innings.over_log.clear()
        innings.bouncers_in_over = 0; innings.cutters_in_over = 0
        innings.mystery_bowled_this_over = False
        await loop_current_innings_simulation(interaction, self.match)

    @discord.ui.button(label="📋 Sim Innings (Verbose)", style=discord.ButtonStyle.secondary)
    async def sim_innings_verbose(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        await interaction.message.edit(view=None)
        self.match.simulation_mode = "whole_match"
        self.match.verbose = True
        # _pending_bowler kept - the selected bowler opens the verbose sim.
        innings = self.match.current_innings
        innings.over_log.clear()
        innings.bouncers_in_over = 0; innings.cutters_in_over = 0
        innings.mystery_bowled_this_over = False
        await loop_current_innings_simulation(interaction, self.match)

    @discord.ui.button(label="🎬 Ball-by-Ball", style=discord.ButtonStyle.secondary, row=2)
    async def sim_innings_bbb(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        await interaction.message.edit(view=None)
        self.match.simulation_mode = "whole_match"
        self.match.verbose = False              # bbb does its own per-ball rendering
        # _pending_bowler kept - the selected bowler bowls the first broadcast over.
        innings = self.match.current_innings
        innings.over_log.clear()
        innings.bouncers_in_over = 0; innings.cutters_in_over = 0
        innings.mystery_bowled_this_over = False
        await loop_current_innings_bbb(interaction, self.match)

    async def impact_btn(self, interaction: discord.Interaction):
        team_id = 1 if interaction.user.id == self.match.p1_id else (2 if interaction.user.id == self.match.p2_id else None)
        if not team_id: return await interaction.response.send_message("❌ You are not playing in this match.", ephemeral=True)
        
        if (team_id == 1 and getattr(self.match, "t1_impact_used", False)) or (team_id == 2 and getattr(self.match, "t2_impact_used", False)):
            return await interaction.response.send_message("❌ You have already used your Impact Player.", ephemeral=True)
            
        subs = self.match.t1_subs if team_id == 1 else self.match.t2_subs
        if not subs: return await interaction.response.send_message("❌ You have no subs available.", ephemeral=True)
            
        await interaction.response.send_message("🔄 **Select your Impact Player Swap:**", view=ImpactPlayerSelectView(self.match, team_id), ephemeral=True)
        
class ActionButton(discord.ui.Button):
    def __init__(self, label, style, row, action_type, disabled=False):
        super().__init__(label=label, style=style, row=row, disabled=disabled)
        self.action_type = action_type
        
    async def callback(self, interaction: discord.Interaction):
        await self.view.process_action(interaction, self.label, self.action_type)

class PaceBowlingView(discord.ui.View):
    def __init__(self, match: CricketMatch):
        super().__init__(timeout=120)
        self.match = match
        self.uid = match.get_bowler_user_id()

        for var in ["Inswing", "Outswing", "Slow", "Fast"]:
            self.add_item(ActionButton(var, discord.ButtonStyle.primary, 0, "var"))

        for length in ["Bouncer", "Full", "Good", "Yorker"]:
            self.add_item(ActionButton(length, discord.ButtonStyle.danger, 1, "len", True))

        for cutter in ["Off Cutter", "Leg Cutter", "Knuckle"]:
            self.add_item(ActionButton(cutter, discord.ButtonStyle.secondary, 2, "cutter"))

        # No Auto Ball in career mode - every delivery is picked by hand.
        if not _is_career_match(match):
            self.add_item(ActionButton("🎲 Auto Ball", discord.ButtonStyle.secondary, 3, "auto"))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.channel.id not in active_games or active_games[interaction.channel.id] != self.match:
            await interaction.response.send_message("❌ This match has been ended.", ephemeral=True)
            return False
        if interaction.user.id != self.uid and interaction.user.id != getattr(self.match, "manager_id", None):
            await interaction.response.send_message("Not your turn.", ephemeral=True)
            return False
        return True
            
    async def process_action(self, interaction: discord.Interaction, label: str, action_type: str):
        if action_type == "var":
            self.match.temp_variation = label
            for c in self.children:
                if c.row == 0:
                    c.disabled = True
                else:
                    c.disabled = False
            await interaction.response.edit_message(view=self)
            
        elif action_type == "len":
            self.match.current_delivery_selection = f"{self.match.temp_variation} {label}"
            await interaction.response.edit_message(view=None)
            await prompt_batter_shot(interaction.channel, self.match, interaction)

        elif action_type == "cutter":
            self.match.current_delivery_selection = label
            await interaction.response.edit_message(view=None)
            await prompt_batter_shot(interaction.channel, self.match, interaction)

        elif action_type == "auto":
            var    = random.choice(["Inswing", "Outswing", "Fast", "Slow"])
            length = random.choice(["Bouncer", "Full", "Good", "Yorker"])
            self.match.current_delivery_selection = f"{var} {length}"
            self.match.current_shot_selection = ""  # simulation auto-picks
            await interaction.response.edit_message(view=None)
            execute_ball_math(self.match)
            await interaction.channel.send(embed=render_embed_scoreboard(self.match))
            class _D: pass
            d = _D(); d.channel = interaction.channel
            await run_interactive_delivery_sequence(d, self.match)

class SpinBowlingView(discord.ui.View):
    def __init__(self, match: CricketMatch, spin_type: str):
        super().__init__(timeout=120)
        self.match = match
        self.uid = match.get_bowler_user_id()
        
        mystery_used = getattr(match.current_innings, "mystery_bowled_this_over", False)
        
        if spin_type == "off":
            opts = ["Off spin", "Carrom", "Arm ball", "Doosra", "Top spin", "Mystery"]
        else:
            opts = ["Leg spin", "Googly", "Flipper", "Drifter", "Slider", "Mystery"]
            
        for idx, spin in enumerate(opts):
            row = 0 if idx < 3 else 1
            disabled = (spin == "Mystery" and mystery_used)
            self.add_item(ActionButton(spin, discord.ButtonStyle.primary, row, "spin", disabled=disabled))

        # No Auto Ball in career mode - every delivery is picked by hand.
        if not _is_career_match(match):
            self.add_item(ActionButton("🎲 Auto Ball", discord.ButtonStyle.secondary, 2, "auto"))
            
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.channel.id not in active_games or active_games[interaction.channel.id] != self.match:
            await interaction.response.send_message("❌ This match has been ended.", ephemeral=True)
            return False
        if interaction.user.id != self.uid and interaction.user.id != getattr(self.match, "manager_id", None):
            await interaction.response.send_message("Not your turn.", ephemeral=True)
            return False
        return True
        
    async def process_action(self, interaction: discord.Interaction, label: str, action_type: str):
        if action_type == "auto":
            innings = self.match.current_innings
            role = innings.current_bowler.get("role", "") if innings.current_bowler else ""
            if "Off" in role:
                opts = ["Off spin", "Carrom", "Arm ball", "Doosra", "Top spin"]
            else:
                opts = ["Leg spin", "Googly", "Flipper", "Drifter", "Slider"]
            self.match.current_delivery_selection = random.choice(opts)
            self.match.current_shot_selection = ""
            await interaction.response.edit_message(view=None)
            execute_ball_math(self.match)
            await interaction.channel.send(embed=render_embed_scoreboard(self.match))
            class _D: pass
            d = _D(); d.channel = interaction.channel
            await run_interactive_delivery_sequence(d, self.match)
            return
        if label == "Mystery":
            self.match.current_innings.mystery_bowled_this_over = True
        self.match.current_delivery_selection = label
        await interaction.response.edit_message(view=None)
        await prompt_batter_shot(interaction.channel, self.match, interaction)

class BattingView(discord.ui.View):
    def __init__(self, match: CricketMatch):
        super().__init__(timeout=120)
        self.match = match
        self.uid = match.get_striker_user_id()
        
        shots = [
            ("Drive", discord.ButtonStyle.primary, 0),
            ("Cut", discord.ButtonStyle.primary, 0),
            ("Pull", discord.ButtonStyle.success, 0),
            ("Flick", discord.ButtonStyle.success, 0),
            ("Loft", discord.ButtonStyle.danger, 1),
            ("Sweep", discord.ButtonStyle.danger, 1),
            ("Scoop", discord.ButtonStyle.danger, 1),
            ("Block", discord.ButtonStyle.secondary, 1),
            ("Leave", discord.ButtonStyle.secondary, 1)
        ]
        for label, style, row in shots:
            self.add_item(ActionButton(label, style, row, "shot"))
            
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.channel.id not in active_games or active_games[interaction.channel.id] != self.match:
            await interaction.response.send_message("❌ This match has been ended.", ephemeral=True)
            return False
        if interaction.user.id != self.uid and interaction.user.id != getattr(self.match, "manager_id", None):
            await interaction.response.send_message("Not your turn.", ephemeral=True)
            return False
        return True
        
    async def process_action(self, interaction: discord.Interaction, label: str, action_type: str):
        if getattr(self, "processed", False): return
        self.processed = True
        self.match.current_shot_selection = label
        await interaction.response.edit_message(view=None)

        execute_ball_math(self.match)

        if getattr(self.match, "wide_extra_msg", ""):
            await interaction.channel.send(self.match.wide_extra_msg)
            self.match.wide_extra_msg = ""

        if getattr(self.match, "pending_drs", False):
            self.match.pending_drs = False
            msg = await interaction.channel.send(f"🚨 **{self.match.drs_dismissal.upper()} GIVEN!**\n<@{self.uid}>, you have 20 seconds to take a review.", view=None)
            view = DRSView(self.match, interaction)
            view.message = msg
            await msg.edit(view=view)
            return
            
        if getattr(self.match, "pending_next_batter", False):
            self.match.pending_next_batter = False
            await interaction.channel.send(embed=render_embed_scoreboard(self.match))
            await interaction.channel.send(embed=render_wicket_summary(self.match))
            await prompt_next_batter(interaction, self.match)
            return
            
        await interaction.channel.send(embed=render_embed_scoreboard(self.match))
        await run_interactive_delivery_sequence(interaction, self.match)

class DRSView(discord.ui.View):
    def __init__(self, match: CricketMatch, origin_inter: discord.Interaction):
        super().__init__(timeout=20)
        self.match = match
        self.origin_inter = origin_inter
        self.processed = False
        
    async def on_timeout(self):
        if self.processed: return
        self.processed = True
        try:
            await self.message.edit(view=None)
            await self.message.channel.send("⏱️ **DRS Timer Expired.** The batter accepts the decision and walks.")
            if getattr(self.match, "pending_next_batter", False):
                self.match.pending_next_batter = False
                await self.message.channel.send(embed=render_embed_scoreboard(self.match))
                await self.message.channel.send(embed=render_wicket_summary(self.match))
                await prompt_next_batter(self.origin_inter, self.match)
                return
            await self.message.channel.send(embed=render_embed_scoreboard(self.match))
            await run_interactive_delivery_sequence(self.origin_inter, self.match)
        except: pass

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.channel.id not in active_games or active_games[interaction.channel.id] != self.match:
            await interaction.response.send_message("❌ This match has been ended.", ephemeral=True)
            return False
        uid = self.match.batting_first_id if self.match.current_innings_num == 1 else self.match.bowling_first_id
        if interaction.user.id != uid and interaction.user.id != getattr(self.match, "manager_id", None):
            await interaction.response.send_message("Only the batting team can review.", ephemeral=True)
            return False
        return True
        
    @discord.ui.button(label="T (Review)", style=discord.ButtonStyle.primary, emoji="📺")
    async def btn_review(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.processed: return
        self.processed = True
        await interaction.response.defer()
        await self.message.edit(view=None)
        is_caught_behind = getattr(self.match, "drs_dismissal", "") == "Caught Behind"
        if random.random() < 0.35:
            if is_caught_behind:
                await interaction.channel.send("📺 **DRS REVIEW:** Hot Spot check... Snickometer flat! **NO EDGE — DECISION OVERTURNED!** 🟢")
            else:
                await interaction.channel.send("📺 **DRS REVIEW:** Pitching... Impact... Wickets Missing! **DECISION OVERTURNED!** 🟢")
            innings = self.match.current_innings
            innings.wickets -= 1

            if getattr(self.match, "pending_next_batter", False):
                self.match.pending_next_batter = False
                # No replacement was promoted yet - the reprieved batter still holds
                # his crease end (after a last-ball wicket the end-change moved him to
                # the non-striker end; that's where he belongs for the new over).
            else:
                innings.next_batter_idx -= 1
                _nb = innings.next_batter_idx
                # Un-promote the auto-promoted new man from whichever end he took
                # if the over ended after the wicket, the end-change parked him at
                # the NON-striker end (blindly restoring the striker would point both
                # ends at the same batter and kill strike rotation).
                if innings.current_striker_idx == _nb:
                    innings.current_striker_idx = self.match.prev_striker_idx
                elif innings.current_non_striker_idx == _nb:
                    innings.current_non_striker_idx = self.match.prev_striker_idx
            innings.batting_stats[innings.batting_team["players"][self.match.prev_striker_idx]["name"]].dismissal = "not out"
            innings.bowling_stats[innings.current_bowler["name"]].wickets_taken -= 1
            if innings.over_log and innings.over_log[-1] == "<:wicket:1520143043683156051>":
                innings.over_log[-1] = "<:0run:1520141253604544633>"
            self.match.last_commentary += "\n📺 **DRS:** Decision Overturned (Not Out)."
        else:
            if is_caught_behind:
                await interaction.channel.send("📺 **DRS REVIEW:** Hot Spot: Clear Edge! Snickometer spike confirmed! **UMPIRING DECISION UPHELD!** 🔴")
            else:
                await interaction.channel.send("📺 **DRS REVIEW:** Three Reds! **UMPIRING DECISION UPHELD!** 🔴")
            self.match.last_commentary += "\n📺 **DRS:** Decision Upheld (Out)."
            if getattr(self.match, "pending_next_batter", False):
                self.match.pending_next_batter = False
                await interaction.channel.send(embed=render_embed_scoreboard(self.match))
                await interaction.channel.send(embed=render_wicket_summary(self.match))
                await prompt_next_batter(interaction, self.match)
                return
        await interaction.channel.send(embed=render_embed_scoreboard(self.match))
        await run_interactive_delivery_sequence(self.origin_inter, self.match)
        
    @discord.ui.button(label="Walk Away", style=discord.ButtonStyle.secondary)
    async def btn_walk(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.processed: return
        self.processed = True
        await interaction.response.defer()
        await self.message.edit(view=None)
        await interaction.channel.send("🚶 Batter accepts the decision and walks off.")
        if getattr(self.match, "pending_next_batter", False):
            self.match.pending_next_batter = False
            await interaction.channel.send(embed=render_embed_scoreboard(self.match))
            await interaction.channel.send(embed=render_wicket_summary(self.match))
            await prompt_next_batter(interaction, self.match)
            return
        await interaction.channel.send(embed=render_embed_scoreboard(self.match))
        await run_interactive_delivery_sequence(self.origin_inter, self.match)

async def _send_career_commentary(channel, match):
    """Career matches: post the last ball's commentary as a plain line, just above
    the next selection prompt (kept out of the scoreboard embed)."""
    if not (getattr(match, "is_club", False) or getattr(match, "is_debut", False) or getattr(match, "is_scenario", False)):
        return
    comm = getattr(match, "last_commentary", "") or ""
    # Dedupe: this is called once before the bowling prompt and once before the shot
    # prompt of the SAME ball - only post a given line once (fixes double commentary).
    if comm and comm == getattr(match, "_last_shown_commentary", None):
        return
    if comm and "initial" not in comm.lower():
        match._last_shown_commentary = comm
        try:
            await channel.send(f"🎙️ {comm}")
        except Exception:
            pass


async def run_interactive_delivery_sequence(interaction, match: CricketMatch):
    innings = match.current_innings
    
    max_w = _match_max_wickets(match)
    _scenario_done = getattr(match, "is_scenario", False) and innings.total_runs >= getattr(match, "scenario_target", 10**9)
    if innings.wickets >= max_w or innings.total_balls >= match.max_balls or _scenario_done or (match.current_innings_num == 2 and innings.total_runs >= getattr(match, "target", match.innings1.total_runs + 1)):
        await handle_innings_end(interaction, match)
        return

    if getattr(match, "over_completed", False):
        match.over_completed = False
        await prompt_bowler_then_hub(interaction, match)
        return
        
    channel = interaction.channel if hasattr(interaction, 'channel') else interaction
    
    if (match.is_ai_game and match.get_bowler_user_id() == match.p2_id) or _bowler_is_bot(match):
        if getattr(innings, "total_balls", 0) % 6 == 0 or (innings.over_log and innings.over_log[-1] == "<:wicket:1520143043683156051>"):
            for _ip_msg in try_ai_impact_player(match, innings):
                await channel.send(_ip_msg)
        role = innings.current_bowler["role"]
        
        if "Spin" in role:
            if "Off" in role:
                opts = ["Off spin", "Carrom", "Arm ball", "Doosra", "Top spin", "Mystery"]
            else:
                opts = ["Leg spin", "Googly", "Flipper", "Drifter", "Slider", "Mystery"]
            if getattr(innings, "mystery_bowled_this_over", False):
                opts.remove("Mystery")
            match.current_delivery_selection = random.choice(opts)
            if match.current_delivery_selection == "Mystery":
                innings.mystery_bowled_this_over = True
        else:
            var = random.choice(['Inswing', 'Outswing', 'Fast', 'Slow'])
            length = random.choice(['Bouncer', 'Full', 'Good', 'Yorker'])
            match.current_delivery_selection = f"{var} {length}"
            
        await prompt_batter_shot(channel, match)
    else:
        role = innings.current_bowler["role"]
        free_hit_notice = "\n🛡️ **FREE HIT BALL!** Batter cannot be dismissed (except run out)!" if getattr(match, "free_hit", False) else ""

        await _send_career_commentary(channel, match)
        if "Spin" in role:
            spin_type = "off" if "Off" in role else "leg"
            title = "Off-Spin" if spin_type == "off" else "Leg-Spin"
            await channel.send(f"🔮 <@{match.get_bowler_user_id()}> (**{innings.current_bowler['name']}**), select your {title} Variation:{free_hit_notice}", view=SpinBowlingView(match, spin_type))
        else:
            await channel.send(f"🔮 <@{match.get_bowler_user_id()}> (**{innings.current_bowler['name']}**), select your Pace Variation:{free_hit_notice}", view=PaceBowlingView(match))

async def prompt_batter_shot(channel, match: CricketMatch, prev=None):
    if (match.is_ai_game and match.get_striker_user_id() == match.p2_id) or _striker_is_bot(match):
        if getattr(match.current_innings, "total_balls", 0) % 6 == 0 or (match.current_innings.over_log and match.current_innings.over_log[-1] == "<:wicket:1520143043683156051>"):
            try_ai_impact_player(match, match.current_innings)
        execute_ball_math(match)
            
        if getattr(match, "pending_drs", False):
            match.pending_drs = False
            if random.random() < 0.4:
                await channel.send("📺 **AI has opted for a DRS Review!**")
                await asyncio.sleep(2)
                is_caught_behind = getattr(match, "drs_dismissal", "") == "Caught Behind"
                if random.random() < 0.35:
                    if is_caught_behind:
                        await channel.send("📺 **DRS REVIEW:** Hot Spot check... Snickometer flat! **NO EDGE — DECISION OVERTURNED!** 🟢")
                    else:
                        await channel.send("📺 **DRS REVIEW:** Pitching... Impact... Wickets Missing! **DECISION OVERTURNED!** 🟢")
                    innings = match.current_innings
                    innings.wickets -= 1
                    if getattr(match, "pending_next_batter", False):
                        match.pending_next_batter = False
                        # reprieved batter still holds his crease end (see DRS view note)
                    else:
                        innings.next_batter_idx -= 1
                        _nb = innings.next_batter_idx
                        # un-promote the new man from whichever end he took (after a
                        # last-ball wicket the end-change parked him at the non-striker end)
                        if innings.current_striker_idx == _nb:
                            innings.current_striker_idx = match.prev_striker_idx
                        elif innings.current_non_striker_idx == _nb:
                            innings.current_non_striker_idx = match.prev_striker_idx
                    innings.batting_stats[innings.batting_team["players"][match.prev_striker_idx]["name"]].dismissal = "not out"
                    innings.bowling_stats[innings.current_bowler["name"]].wickets_taken -= 1
                    if innings.over_log and innings.over_log[-1] == "<:wicket:1520143043683156051>":
                        innings.over_log[-1] = "<:0run:1520141253604544633>"
                    match.last_commentary += "\n📺 **DRS:** Decision Overturned (Not Out)."
                else:
                    if is_caught_behind:
                        await channel.send("📺 **DRS REVIEW:** Hot Spot: Clear Edge! Snickometer spike confirmed! **UMPIRING DECISION UPHELD!** 🔴")
                    else:
                        await channel.send("📺 **DRS REVIEW:** Three Reds! **UMPIRING DECISION UPHELD!** 🔴")
                    match.last_commentary += "\n📺 **DRS:** Decision Upheld (Out)."
            else:
                await channel.send("🚶 AI Batter accepts the decision and walks off.")
            
        await channel.send(embed=render_embed_scoreboard(match))

        class Dummy: pass
        d = Dummy()
        d.channel = channel

        # A BOT batter can be dismissed too. In club matches (is_ai_game False) the engine
        # sets pending_next_batter instead of auto-advancing, so we MUST hand off to the
        # next-batter flow here - otherwise the dismissed bot keeps facing balls and the
        # captain is never asked to send a replacement.
        if getattr(match, "pending_next_batter", False):
            match.pending_next_batter = False
            await channel.send(embed=render_wicket_summary(match))
            await prompt_next_batter(d, match)
            return

        await run_interactive_delivery_sequence(d, match)
    else:
        # Failsafe clamp to prevent IndexError edge cases
        if match.current_innings.current_striker_idx >= len(match.current_innings.batting_team["players"]):
            match.current_innings.current_striker_idx = len(match.current_innings.batting_team["players"]) - 1
            
        sn = match.current_innings.batting_team["players"][match.current_innings.current_striker_idx]["name"]
        free_hit_notice = "\n🛡️ **FREE HIT!** You cannot be dismissed (except run out)!" if getattr(match, "free_hit", False) else ""
        await _send_career_commentary(channel, match)
        await channel.send(f"⚔️ <@{match.get_striker_user_id()}> (**{sn}**)\n🚨 The bowler bowled a **{match.current_delivery_selection}**!{free_hit_notice}\nSelect your shot:", view=BattingView(match))


# ---- CAREER MODE - INTERACTIVE DEBUT (reuses the real engine + BattingView) ----
# A 2-over batting trial: you open vs an AI academy attack and play it ball-by-ball
# exactly like a real match. Two guarded hooks (`is_debut`) reroute match-end to
# handle_debut_end - they no-op for every normal match (attr defaults to False).
_DEBUT_OVERS = 2
_DEBUT_TARGET = 16   # runs needed in the trial to pass


def _academy_player(name, bat, bowl, role, arch):
    return {"name": name, "bat": bat, "bowl": bowl, "role": role, "archetype": arch}


def _build_debut_teams(career, author_name):
    eng = CM.career_to_engine(career)
    you = _academy_player(author_name, eng["bat"], eng["bowl"], eng["role"], eng["archetype"])
    # You open; modest academy partners fill the order so the line-up is valid and a
    # single non-striker run-out doesn't abort the innings.
    partners = [_academy_player(f"Academy Partner {i+1}", 52 - i, 46, "Batter", "Standard")
                for i in range(10)]
    bat_team = {"name": f"{author_name}'s XI", "players": [you] + partners, "subs": [], "color": "#3BA55D"}
    # A fair academy attack scaled to a fresh OVR-60 rookie (~60-63).
    attack = [
        _academy_player("Academy Quick A",  28, 63, "Bowler_Pace",     "Standard"),
        _academy_player("Academy Quick B",  28, 61, "Bowler_Pace",     "Standard"),
        _academy_player("Academy Off-Spin", 28, 62, "Bowler_Spin_Off", "Standard"),
        _academy_player("Academy Leg-Spin", 28, 60, "Bowler_Spin_Leg", "Standard"),
        _academy_player("Academy Seamer",   34, 58, "All-Rounder_Pace", "Standard"),
        _academy_player("Academy Keeper",   48, 18, "Batter_WK",       "Standard"),
    ]
    while len(attack) < 11:
        attack.append(_academy_player(f"Academy Fielder {len(attack)}", 45, 25, "Batter", "Standard"))
    bowl_team = {"name": "Academy XI", "players": attack, "subs": [], "color": "#ED4245"}
    return bat_team, bowl_team


async def start_debut_match(channel, author, career):
    if channel.id in active_games or channel.id in active_setups:
        await channel.send("❌ A match or setup is already running in this channel. Finish it (or `cv endmatch`) first.")
        return
    pname = career.get("username", author.display_name)
    bat_team, bowl_team = _build_debut_teams(career, pname)
    pitch = random.choice(["Flat", "Hard", "Green", "Dry"])
    match = CricketMatch(pname, "Academy XI", author.id, None,
                         bat_team, bowl_team, format_overs=_DEBUT_OVERS, pitch=pitch, weather="Clear")
    match.is_debut = True
    match.debut_user_id = author.id
    match.debut_player_name = pname
    match.batting_first_id = author.id   # you bat first
    match.bowling_first_id = None        # AI bowls (p2_id is None -> is_ai_game)
    match.innings1 = InningsState(bat_team, bowl_team)
    match.current_innings = match.innings1
    match.current_innings_num = 1
    active_games[channel.id] = match
    await channel.send(
        f"🎓 **ACADEMY TRIAL — {pname}**\n"
        f"🏟️ Pitch: **{pitch}**  ·  ⏱️ **{_DEBUT_OVERS} overs**  ·  🎯 Pass mark: **{_DEBUT_TARGET}+ team runs**\n"
        f"You're opening vs the academy attack — pick your shots ball-by-ball. "
        f"Put **{_DEBUT_TARGET}** on the board before you're out or the overs run out!"
    )
    await prompt_bowler_then_hub(channel, match)


async def handle_debut_end(interaction_context, match: CricketMatch):
    channel = interaction_context.channel if hasattr(interaction_context, "channel") else interaction_context
    active_games.pop(getattr(channel, "id", None), None)

    innings = match.current_innings
    bs = innings.batting_stats.get(match.debut_player_name)
    runs  = bs.runs_scored if bs else 0
    balls = bs.balls_faced if bs else 0
    fours = bs.fours if bs else 0
    sixes = bs.sixes if bs else 0
    out   = bool(bs and bs.dismissal != "not out")
    sr    = (runs / balls * 100) if balls else 0.0
    dism  = bs.dismissal if (bs and out) else "not out"

    career = CM.get_career(match.debut_user_id)
    if not career:
        await channel.send("⚠️ Couldn't find your career to finalize the debut.")
        return

    team = innings.total_runs
    overs_str = f"{innings.total_balls // 6}.{innings.total_balls % 6}"
    passed = team >= _DEBUT_TARGET
    line = (f"**Team {team}/{innings.wickets}** in {overs_str} ov  (needed {_DEBUT_TARGET}).\n"
            f"Your knock: **{runs}** ({balls}b · {fours}×4 · {sixes}×6 · SR {sr:.0f}) — *{dism}*.")

    if passed:
        # Record the official debut innings into lifetime stats (once).
        try:
            st = career.setdefault("stats", CM._blank_stats())["bat"]
            st["matches"] += 1; st["innings"] += 1; st["runs"] += runs; st["balls"] += balls
            st["fours"] += fours; st["sixes"] += sixes; st["hs"] = max(st["hs"], runs)
            st["not_outs" if not out else "outs"] += 1
            if runs >= 50:  st["fifties"]  += 1
            if runs >= 100: st["hundreds"] += 1
        except Exception:
            pass
        career["debut_done"] = True
        CM.quest_progress(career, "matches", 1)
        if runs: CM.quest_progress(career, "runs", runs)
        if fours: CM.quest_progress(career, "fours", fours)
        if sixes: CM.quest_progress(career, "sixes", sixes)
        CM.async_save_career(career)
        e = discord.Embed(
            title="✅ TRIAL PASSED — Welcome to the pros!",
            description=f"{line}\n\nYour official card is **unlocked**. Earn coins with `cv daily`, then `cv upgrade` to climb the tiers.",
            color=discord.Color.green())
        await channel.send(embed=e)
        await channel.send(file=discord.File(career_ui.render_career_card(career), "career_card.png"))
        # Grant the starting tier role (Bronze) on debut so everyone has a role from day one.
        try:
            guild = getattr(channel, "guild", None)
            member = guild.get_member(match.debut_user_id) if guild else None
            if member:
                await _sync_tier_role(guild, member, career)
        except Exception:
            pass
    else:
        e = discord.Embed(
            title="❌ TRIAL FAILED",
            description=f"{line}\n\nYou needed **{_DEBUT_TARGET}+** team runs. Run `cv debut` to try again.",
            color=discord.Color.red())
        await channel.send(embed=e)


# ---- CAREER MODE - INTERACTIVE SCENARIO (solo, difficult, small reward + quests) ----
_SCENARIO_DEFS = [("Quickfire", 2), ("Run Chase", 3), ("Pressure Cooker", 4)]


def _scenario_player_team(career, pname, roles_are_field=False):
    eng = CM.career_to_engine(career)
    you = _academy_player(pname, eng["bat"], eng["bowl"], eng["role"], eng["archetype"])
    if roles_are_field:   # bowling scenario: you + fielders (you bowl every over)
        rest = [_academy_player(f"Fielder {i+1}", 42, 30, "Batter", "Standard") for i in range(10)]
    else:                 # batting scenario: partners rated the SAME as you
        rest = [_academy_player(f"Partner {i+1}", eng["bat"], eng["bowl"], "Batter", "Standard") for i in range(5)]
    return {"name": f"{pname}'s XI", "players": [you] + rest, "subs": [], "color": "#3BA55D"}


def _challenge_attack(lvl, rlo=8, rhi=16):
    def bw(name, role):
        return _academy_player(name, 24, min(95, lvl + random.randint(rlo, rhi)), role, "Standard")
    a = [bw("Pace Ace", "Bowler_Pace"), bw("New-Ball Quick", "Bowler_Pace"),
         bw("Off-Spinner", "Bowler_Spin_Off"), bw("Leg-Spinner", "Bowler_Spin_Leg"),
         bw("Seamer", "All-Rounder_Pace"), _academy_player("Keeper", 44, 18, "Batter_WK", "Standard")]
    while len(a) < 11:
        a.append(_academy_player(f"Fielder {len(a)}", 40, 22, "Batter", "Standard"))
    return {"name": "Challenge XI", "players": a, "subs": [], "color": "#ED4245"}


def _challenge_batting(lvl, rlo=8, rhi=16):
    def bt(name, role="Batter"):
        return _academy_player(name, min(95, lvl + random.randint(rlo, rhi)), 24, role, "Aggressor")
    b = [bt("C. Opener"), bt("S. Opener"), bt("T. No.3"), bt("M. No.4"),
         bt("F. Finisher", "Batter"), bt("WK Bat", "Batter_WK")]
    while len(b) < 11:
        b.append(_academy_player(f"Tail {len(b)}", 40, 60, "Bowler_Pace", "Standard"))
    return {"name": "Challenge XI", "players": b, "subs": [], "color": "#ED4245"}


async def start_scenario_match(channel, author, career, mode="bat", difficulty="medium"):
    if channel.id in active_games or channel.id in active_setups:
        # The entry fee was charged at the confirm step - refund it, don't eat it.
        career["coins"] += CM.SCENARIO_ENTRY_FEE
        CM.async_save_career(career)
        return await channel.send("❌ A match or setup is already running here — entry fee refunded. Finish it (or `cv endmatch`) first.")
    pname = career.get("username", author.display_name)
    eng = CM.career_to_engine(career)
    title, overs = random.choice(_SCENARIO_DEFS)
    pitch = random.choice(["Flat", "Hard", "Green", "Dry", "Bouncy"])
    diff = CM.SCENARIO_DIFFS.get(difficulty, CM.SCENARIO_DIFFS["medium"])
    rlo, rhi = diff["rlo"], diff["rhi"]
    dlabel = diff["label"]
    opp_desc = "matched" if difficulty == "easy" else ("strong" if difficulty == "medium" else "elite")

    if mode == "bowl":
        you_team = _scenario_player_team(career, pname, roles_are_field=True)
        opp = _challenge_batting(eng["bowl"], rlo, rhi)
        wkt_target = 2 if overs <= 3 else 3
        # team1 = your (bowling) side, team2 = Challenge XI (bats) - AI bats, you bowl.
        match = CricketMatch(pname, "Challenge XI", author.id, None, you_team, opp,
                             format_overs=overs, pitch=pitch, weather="Clear")
        match.batting_first_id = None          # AI bats
        match.bowling_first_id = author.id      # you bowl
        match.innings1 = InningsState(opp, you_team)
        match.scenario_wkt_target = wkt_target
        intro = (f"🎳 **SCENARIO — {title}**  ({overs} overs · BOWLING · **{dlabel}**)\n"
                 f"🎯 Take **{wkt_target}** wickets vs a {opp_desc} **Challenge XI**  ·  🏟️ {pitch}\n"
                 f"You bowl every over — pick your deliveries. Strike!")
    else:
        you_team = _scenario_player_team(career, pname)
        opp = _challenge_attack(eng["bat"], rlo, rhi)
        target = max(overs * 8, round(overs * (9 + max(0, eng["bat"] - 60) / 15.0)))
        match = CricketMatch(pname, "Challenge XI", author.id, None, you_team, opp,
                             format_overs=overs, pitch=pitch, weather="Clear")
        match.batting_first_id = author.id      # you bat
        match.bowling_first_id = None           # AI bowls
        match.innings1 = InningsState(you_team, opp)
        match.scenario_target = target
        intro = (f"🏏 **SCENARIO — {title}**  ({overs} overs · BATTING · **{dlabel}**)\n"
                 f"🎯 Chase **{target}** vs a {opp_desc} **Challenge XI**  ·  🏟️ {pitch}\n"
                 f"One innings — ends when you're out, the overs run out, or you reach the target.")

    match.is_scenario = True
    match.scenario_mode = mode
    match.scenario_difficulty = difficulty
    match.scenario_user_id = author.id
    match.scenario_player_name = pname
    match.scenario_title = title
    match.current_innings = match.innings1
    match.current_innings_num = 1
    active_games[channel.id] = match
    await channel.send(intro)
    await prompt_bowler_then_hub(channel, match)


async def handle_scenario_end(interaction_context, match: CricketMatch):
    channel = interaction_context.channel if hasattr(interaction_context, "channel") else interaction_context
    active_games.pop(getattr(channel, "id", None), None)
    innings = match.current_innings
    career = CM.get_career(match.scenario_user_id)
    if not career:
        return await channel.send("⚠️ Couldn't find your career to finalize the scenario.")
    ov = f"{innings.total_balls // 6}.{innings.total_balls % 6}"

    if getattr(match, "scenario_mode", "bat") == "bowl":
        ws = innings.bowling_stats.get(match.scenario_player_name)
        wkts = ws.wickets_taken if ws else 0
        conceded = ws.runs_conceded if ws else 0
        tgt = match.scenario_wkt_target
        passed = wkts >= tgt
        coins, capped, remaining = CM.scenario_complete(career, wickets=wkts, passed=passed, mode="bowl",
                                                        difficulty=getattr(match, "scenario_difficulty", "medium"))
        line = (f"Challenge XI **{innings.total_runs}/{innings.wickets}** in {ov} ov.\n"
                f"Your figures: **{wkts}/{conceded}** — needed **{tgt}** wickets.")
        title = (f"✅ SCENARIO CLEARED — {match.scenario_title}!" if passed
                 else f"❌ {match.scenario_title} — only {wkts}/{tgt} wickets")
    else:
        bs = innings.batting_stats.get(match.scenario_player_name)
        runs = bs.runs_scored if bs else 0
        balls = bs.balls_faced if bs else 0
        fours = bs.fours if bs else 0
        sixes = bs.sixes if bs else 0
        target = match.scenario_target
        passed = innings.total_runs >= target
        coins, capped, remaining = CM.scenario_complete(career, runs=runs, fours=fours, sixes=sixes, passed=passed, mode="bat",
                                                        difficulty=getattr(match, "scenario_difficulty", "medium"))
        line = (f"**Team {innings.total_runs}/{innings.wickets}** in {ov} ov (target {target}).\n"
                f"Your knock: **{runs}** ({balls}b · {fours}×4 · {sixes}×6).")
        title = (f"✅ SCENARIO CLEARED — {match.scenario_title}!" if passed
                 else f"❌ {match.scenario_title} — target missed")

    reward = (f"🪙 **+{coins}** coins  ·  {remaining} paid scenarios left today"
              if coins else "🪙 No coins (cap reached or fee forfeited) — still counts toward quests!")
    e = discord.Embed(title=title, description=f"{line}\n{reward}",
                      color=discord.Color.green() if passed else discord.Color.orange())
    e.set_footer(text="📜 Quest progress updated (claim with cv quests). Scenario stats are separate from cv stats.")
    await channel.send(embed=e)


class _ScenarioDiffSelect(discord.ui.Select):
    def __init__(self, owner):
        self._owner = owner                       # NOTE: `parent` is reserved by discord.ui
        opts = [
            discord.SelectOption(label="Medium", description="A strong Challenge XI", value="medium", emoji="🟡", default=True),
            discord.SelectOption(label="Easy", description="A matched, beatable Challenge XI", value="easy", emoji="🟢"),
            discord.SelectOption(label="Hard", description="An elite Challenge XI", value="hard", emoji="🔴"),
        ]
        super().__init__(placeholder="Difficulty: Medium", options=opts, row=0)

    async def callback(self, interaction):
        self._owner.difficulty = self.values[0]
        lbl = CM.SCENARIO_DIFFS[self.values[0]]["label"]
        self.placeholder = f"Difficulty: {lbl}"
        for o in self.options:
            o.default = (o.value == self.values[0])
        await interaction.response.edit_message(view=self._owner)


class ScenarioConfirmView(discord.ui.View):
    """Pick difficulty + Batting or Bowling, pay the entry fee, launch an interactive scenario."""
    def __init__(self, user_id):
        super().__init__(timeout=60)
        self.uid = user_id
        self.difficulty = "medium"
        self.add_item(_ScenarioDiffSelect(self))

    async def interaction_check(self, interaction):
        if interaction.user.id != self.uid:
            await interaction.response.send_message("This isn't your scenario.", ephemeral=True)
            return False
        return True

    async def _start(self, interaction, mode):
        career = CM.get_career(self.uid)
        if not career:
            return await interaction.response.edit_message(content="❌ No career found.", view=None)
        if interaction.channel.id in active_games or interaction.channel.id in active_setups:
            return await interaction.response.edit_message(content="❌ A match is already running in this channel.", view=None)
        fee = CM.SCENARIO_ENTRY_FEE
        if career["coins"] < fee:
            return await interaction.response.edit_message(
                content=f"❌ Not enough coins — entry fee is **{fee}** 🪙 (you have {career['coins']:,}).", view=None)
        career["coins"] -= fee
        CM.async_save_career(career)
        dlabel = CM.SCENARIO_DIFFS.get(self.difficulty, CM.SCENARIO_DIFFS["medium"])["label"]
        await interaction.response.edit_message(
            content=f"🎟️ Entry fee paid (−{fee} 🪙). Starting your **{dlabel}** "
                    f"**{'bowling' if mode=='bowl' else 'batting'}** scenario…", view=None)
        await start_scenario_match(interaction.channel, interaction.user, career, mode, self.difficulty)

    @discord.ui.button(label="Bat", style=discord.ButtonStyle.success, emoji="🏏", row=1)
    async def bat(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._start(interaction, "bat")

    @discord.ui.button(label="Bowl", style=discord.ButtonStyle.primary, emoji="🎳", row=1)
    async def bowl(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._start(interaction, "bowl")

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary, row=1)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content="🚫 Scenario cancelled — no fee charged.", view=None)


# ---- CAREER MODE - CLUB MATCH (Phase 4.2): interactive, captain-controlled PvP ----
# Both XIs are built from the joiners' careers. The top-OVR player on each side is
# the captain who plays the match for their team. Reuses the full interactive engine
# (toss -> innings -> BattingView / bowling views). `is_club` keeps it interactive-only
# (no Sim hub); `max_wickets` makes short-sided sides go all-out correctly.
def _build_club_team(players, name, color):
    eng, seen = [], {}
    for p in players:
        c = p["career"] if p.get("is_bot") else CM.get_career(p["id"])
        if not c:
            continue
        e = CM.career_to_engine(c)
        e["owner_id"] = p["id"]
        if p.get("is_bot"):
            e["is_bot"] = True
        nm = e["name"]                       # engine stats key by name - keep them unique per team
        if nm in seen:
            seen[nm] += 1
            e["name"] = f"{nm} ({seen[nm]})"
        else:
            seen[nm] = 1
        eng.append(e)
    return {"name": name, "players": eng, "subs": [], "color": color}


async def _club_match_payout(channel, match):
    """Phase 4.3 - pay coins + record lifetime stats for every HUMAN player after a
    club match. Bots earn nothing. Winner's side gets the victory bonus."""
    inns = [i for i in (match.innings1, match.innings2) if i]
    target = getattr(match, "target", match.innings1.total_runs + 1)
    inn2 = match.innings2
    tiebreak = getattr(match, "tiebreak_winner_name", None)
    if tiebreak:
        winner = tiebreak   # scores were tied, but a Super Over decided it
    elif inn2 and inn2.total_runs >= target:
        winner = inn2.batting_team["name"]
    elif inn2 and inn2.total_runs == target - 1:
        winner = None   # tie
    else:
        winner = match.innings1.batting_team["name"]

    def _inn(team_name, role):
        for i in inns:
            if role == "bat" and i.batting_team["name"] == team_name: return i
            if role == "bowl" and i.bowling_team["name"] == team_name: return i
        return None

    lines = []
    for team in (match.team1, match.team2):
        tn = team["name"]
        bat_inn, bowl_inn = _inn(tn, "bat"), _inn(tn, "bowl")
        won = (winner == tn)
        for p in team["players"]:
            oid = p.get("owner_id")
            if p.get("is_bot") or oid is None or oid < 0:
                continue
            career = CM.get_career(oid)
            if not career:
                continue
            bs = bat_inn.batting_stats.get(p["name"]) if bat_inn else None
            ws = bowl_inn.bowling_stats.get(p["name"]) if bowl_inn else None
            runs = bs.runs_scored if bs else 0
            balls = bs.balls_faced if bs else 0
            fours = bs.fours if bs else 0
            sixes = bs.sixes if bs else 0
            out = bool(bs and bs.dismissal != "not out")
            batted = bool(bs and (balls > 0 or out))
            wkts = ws.wickets_taken if ws else 0
            b_balls = ws.balls_bowled if ws else 0
            b_runs = ws.runs_conceded if ws else 0
            fifties = 1 if 50 <= runs < 100 else 0
            hundreds = 1 if runs >= 100 else 0

            coins = CM.award_match_earnings(career, runs=runs, fifties=fifties, hundreds=hundreds,
                                            wickets=wkts, won=won, is_real_match=True)
            try:
                st = career.setdefault("stats", CM._blank_stats())
                b = st["bat"]
                b["matches"] += 1
                if batted:
                    b["innings"] += 1
                    b["not_outs" if not out else "outs"] += 1
                    b["runs"] += runs; b["balls"] += balls; b["fours"] += fours; b["sixes"] += sixes
                    b["hs"] = max(b["hs"], runs)
                    b["fifties"] += fifties; b["hundreds"] += hundreds
                bw = st["bowl"]
                bw["balls"] += b_balls; bw["runs"] += b_runs; bw["wickets"] += wkts
                if wkts > bw.get("best_w", 0) or (wkts == bw.get("best_w", 0) and b_runs < bw.get("best_r", 999)):
                    bw["best_w"] = wkts; bw["best_r"] = b_runs
                club = career.setdefault("club", {"played": 0, "won": 0})
                club["played"] += 1
                if won:
                    club["won"] += 1
            except Exception:
                pass
            # Daily-quest progress (claimed later via `cv quests`)
            CM.quest_progress(career, "matches", 1)
            if runs:           CM.quest_progress(career, "runs", runs)
            if wkts:           CM.quest_progress(career, "wickets", wkts)
            if won:            CM.quest_progress(career, "wins", 1)
            if fours:          CM.quest_progress(career, "fours", fours)
            if sixes:          CM.quest_progress(career, "sixes", sixes)
            if fifties:        CM.quest_progress(career, "fifties", 1)
            CM.async_save_career(career)
            tag = []
            if runs or batted: tag.append(f"{runs}{'*' if not out and batted else ''}")
            if wkts: tag.append(f"{wkts}w")
            lines.append(f"<@{oid}> **+{coins}**🪙" + (f" ({', '.join(tag)})" if tag else ""))

    if lines:
        head = f"🏆 **{winner} win!**" if winner else "🤝 **Match tied!**"
        await channel.send(f"💰 **Match Earnings** — {head}\n" + "  ·  ".join(lines))


async def start_club_match(channel, lobby, host):
    a, b = lobby.team_a, lobby.team_b
    cap_a, cap_b = a[0], b[0]
    team1 = _build_club_team(a, f"{cap_a['name']}'s XI", "#3BA55D")
    team2 = _build_club_team(b, f"{cap_b['name']}'s XI", "#ED4245")
    if len(team1["players"]) < 2 or len(team2["players"]) < 2:
        active_games.pop(channel.id, None)
        return await channel.send("❌ Couldn't build the teams (a member may have deleted their career). Re-create the lobby.")

    pitch = random.choice(["Flat", "Hard", "Green", "Dry", "Dusty", "Bouncy"])
    match = ClubMatch(cap_a["name"], cap_b["name"], cap_a["id"], cap_b["id"],
                      team1, team2, format_overs=lobby.overs, pitch=pitch, weather="Clear")
    match.is_club = True
    match._caps = {team1["name"]: cap_a["id"], team2["name"]: cap_b["id"]}
    # All-out at squad size: the last batter bats ALONE (career-mode rule only).
    match.max_wickets = len(team1["players"])
    # Everyone's an all-rounder, so all can bowl - give each enough overs to cover the
    # innings (ceil(overs / squad)), else short sides run out of bowlers.
    match.bowler_quota = max(1, -(-lobby.overs // len(team1["players"])))
    match._cap_a_id = cap_a["id"]
    match._cap_b_id = cap_b["id"]
    match._club_per_side = lobby.per_side()
    active_games[channel.id] = match

    # Both captains name their team first; the toss starts once both names are in.
    nview = ClubNameView(match, cap_a, cap_b)
    nview.message = await channel.send(
        f"🏟️ **CLUB MATCH** · 🌱 {pitch} · ⏱️ {lobby.overs} overs · {lobby.per_side()}-a-side\n"
        f"🧢 Captains <@{cap_a['id']}> & <@{cap_b['id']}> — tap **Name Your Team** to begin.\n"
        f"-# <@{cap_a['id']}>: ⌛   ·   <@{cap_b['id']}>: ⌛",
        view=nview)
    await nview.kickoff(channel)


def _apply_club_names(match, name_a, name_b):
    """Rename both club teams and rebuild the name->captain map (_cap_of keys on team name)."""
    name_a = " ".join(str(name_a or "").split()).strip()[:24] or "Team A"
    name_b = " ".join(str(name_b or "").split()).strip()[:24] or "Team B"
    if name_a.lower() == name_b.lower():
        name_b = (name_b + " B")[:24]
    match.team1["name"] = name_a
    match.team2["name"] = name_b
    match._caps = {name_a: match._cap_a_id, name_b: match._cap_b_id}


async def _club_begin_toss(channel, match):
    header = (
        f"🏟️ **CLUB MATCH — {match.team1['name']} vs {match.team2['name']}**\n"
        f"🌱 Pitch: **{match.pitch}**  ·  ⏱️ **{match.format_overs} overs**  ·  "
        f"{getattr(match, '_club_per_side', '')}-a-side\n"
        f"🧢 Captains: <@{match._cap_a_id}> vs <@{match._cap_b_id}>. Each player bats & bowls their own turn.\n")

    # Bots can't click: if either captain is a bot, auto-run the toss so the match
    # never stalls (a swap can legitimately hand a bot the captaincy).
    if _is_bot_uid(match._cap_a_id) or _is_bot_uid(match._cap_b_id):
        flip = random.choice(["Heads", "Tails"])
        match.toss_winner = random.choice([match.p1_id, match.p2_id])
        win_team = match.team1["name"] if match.toss_winner == match.p1_id else match.team2["name"]
        if _is_bot_uid(match.toss_winner):
            choice = random.choice(["Bat", "Bowl"])
            apply_toss_decision(match, choice)
            await channel.send(header + f"🪙 Coin lands **{flip}** — 🤖 **{win_team}** win the toss and **{choice.lower()} first**!")
            await prompt_club_openers(channel, match)
        else:
            await channel.send(header + f"🪙 Coin lands **{flip}** — **{win_team}** win the toss! <@{match.toss_winner}>, choose:",
                               view=TossDecisionView(match))
        return

    await channel.send(header + f"🪙 **Toss!** <@{match._cap_b_id}>, call the coin:",
                       view=TossCallView(match))


class ClubNameModal(discord.ui.Modal, title="Name Your Team"):
    tname = discord.ui.TextInput(label="Team Name", placeholder="e.g. Mumbai Strikers",
                                 min_length=2, max_length=24, required=True)

    def __init__(self, view, uid, default):
        super().__init__()
        self._view = view
        self._uid = uid
        if default:
            self.tname.default = str(default)[:24]

    async def on_submit(self, interaction):
        name = " ".join(str(self.tname.value).split()).strip()[:24] or "XI"
        await self._view.record(interaction, self._uid, name)


class ClubNameView(discord.ui.View):
    """After `cv startmatch`: both captains name their team, then the toss begins."""
    def __init__(self, match, cap_a, cap_b):
        super().__init__(timeout=300)
        self.match = match
        self.cap_a_id = cap_a["id"]
        self.cap_b_id = cap_b["id"]
        self.default_a = f"{cap_a['name']}'s XI"
        self.default_b = f"{cap_b['name']}'s XI"
        self.names = {}
        self.message = None
        self.done = False
        # Bots can't type - auto-name them with the default.
        if _is_bot_uid(self.cap_a_id): self.names[self.cap_a_id] = self.default_a
        if _is_bot_uid(self.cap_b_id): self.names[self.cap_b_id] = self.default_b

    async def kickoff(self, channel):
        if self.cap_a_id in self.names and self.cap_b_id in self.names:
            await self._finish(channel)     # both captains are bots -> straight to toss

    @discord.ui.button(label="🏷️ Name Your Team", style=discord.ButtonStyle.primary)
    async def name_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        uid = interaction.user.id
        if uid not in (self.cap_a_id, self.cap_b_id):
            return await interaction.response.send_message("❌ Only the two captains can name their teams.", ephemeral=True)
        default = self.default_a if uid == self.cap_a_id else self.default_b
        await interaction.response.send_modal(ClubNameModal(self, uid, self.names.get(uid, default)))

    async def record(self, interaction, uid, name):
        self.names[uid] = name
        await interaction.response.send_message(f"✅ Your team is **{name}**.", ephemeral=True)
        try:
            a_mark = f"**{self.names[self.cap_a_id]}**" if self.cap_a_id in self.names else "⌛"
            b_mark = f"**{self.names[self.cap_b_id]}**" if self.cap_b_id in self.names else "⌛"
            await self.message.edit(content=(
                f"🏟️ **CLUB MATCH** — name your teams!\n"
                f"-# <@{self.cap_a_id}>: {a_mark}   ·   <@{self.cap_b_id}>: {b_mark}"))
        except Exception:
            pass
        if self.cap_a_id in self.names and self.cap_b_id in self.names:
            await self._finish(self.message.channel if self.message else interaction.channel)

    async def _finish(self, channel):
        if self.done:
            return
        self.done = True
        self.stop()
        _apply_club_names(self.match, self.names.get(self.cap_a_id, self.default_a),
                          self.names.get(self.cap_b_id, self.default_b))
        try:
            if self.message:
                await self.message.edit(view=None)
        except Exception:
            pass
        await _club_begin_toss(channel, self.match)

    async def on_timeout(self):
        if self.done or not self.message:
            return
        try:
            await self.message.channel.send("⌛ Team-naming timed out — using default names.")
        except Exception:
            pass
        await self._finish(self.message.channel)


async def prompt_club_openers(interaction, match):
    """Batting captain picks the 2 opening batsmen at the start of an innings."""
    innings = match.current_innings
    cap_id = match.batting_captain_id()
    channel = interaction.channel if hasattr(interaction, 'channel') else interaction
    players = innings.batting_team["players"]

    if _is_bot_uid(cap_id):
        # Bot captain: open with the two best batsmen.
        idxs = sorted(sorted(range(len(players)), key=lambda i: players[i]["bat"], reverse=True)[:2])
        chosen = [players[i] for i in idxs]
        rest = [p for k, p in enumerate(players) if k not in idxs]
        innings.batting_team["players"][:] = chosen + rest
        innings.current_striker_idx = 0
        innings.current_non_striker_idx = 1
        innings.next_batter_idx = 2
        await channel.send(f"🤖 **{innings.batting_team['name']}** (bot) opens with **{chosen[0]['name']}** & **{chosen[1]['name']}**.")
        await prompt_bowler_then_hub(interaction, match)
        return

    view = discord.ui.View(timeout=300)
    opts = [discord.SelectOption(label=f"{p['name']}", description=f"{p['role'].split('_')[0]} · {p.get('archetype','')}".strip(" ·"),
                                 value=str(i)) for i, p in enumerate(players)]
    select = discord.ui.Select(placeholder="Pick your 2 opening batsmen…",
                               min_values=2, max_values=2, options=opts[:25])

    async def cb(inter):
        idxs = sorted(int(v) for v in select.values)
        chosen = [players[i] for i in idxs]
        rest = [p for k, p in enumerate(players) if k not in idxs]
        innings.batting_team["players"][:] = chosen + rest   # openers to slots 0,1
        innings.current_striker_idx = 0
        innings.current_non_striker_idx = 1
        innings.next_batter_idx = 2
        await inter.response.defer()
        await inter.message.edit(view=None)
        await channel.send(f"🏏 Openers: **{chosen[0]['name']}** & **{chosen[1]['name']}** to the middle.")
        await prompt_bowler_then_hub(inter, match)

    select.callback = cb
    view.add_item(select)

    async def icheck(inter):
        if inter.channel.id not in active_games or active_games[inter.channel.id] != match:
            await inter.response.send_message("❌ This match has ended.", ephemeral=True)
            return False
        if inter.user.id != cap_id and inter.user.id != getattr(match, "manager_id", None):
            await inter.response.send_message("❌ Only the batting captain picks the openers.", ephemeral=True)
            return False
        return True
    view.interaction_check = icheck

    await channel.send(f"🧢 <@{cap_id}> (batting captain) — pick your **2 opening batsmen**:", view=view)


# ---- New step-by-step match setup flow ----

active_setups = {}

class MatchSetupState:
    def __init__(self, p1, p2, p1_id, p2_id):
        self.p1 = p1
        self.p2 = p2
        self.p1_id = p1_id
        self.p2_id = p2_id
        self.format_overs = 20
        self.impact_player = False
        self.t1_name = "Team 1"
        self.t1_roster = []
        self.t1_squad = []
        self.t1_captain = None
        self.t2_name = "Team 2"
        self.t2_roster = []
        self.t2_squad = []
        self.t2_captain = None
        self.pitch = "Flat"
        self.weather = "Clear"
        # The toss now runs BEFORE the XIs are entered, so its result lives on the
        # setup state until the match object is built. winner None = AI opponent.
        self.toss_winner_id = None
        self.toss_choice = None      # "Bat" / "Bowl"
        self.tournament_name = "TOURNAMENT"
        self.home_team_id = p1_id
        self.sim_only = False
        # Player-test matches are a sandbox for evaluating players. They must not
        # consume a subscriber's daily allowance or advance the public match totals.
        self.is_player_test = False


# Captain & Wicket-Keeper rules (enforced at the start of every match setup)
# Every XI must carry a keeper, and exactly one player is captain (+1 to their main
# skill). A line in a typed XI may end with '(C)' to name the captain inline; '(c)'
# and '(captain)' are accepted too, though '(C)' is the documented form.
_CAPTAIN_MARK_RE = re.compile(r"\s*\(\s*(c|captain)\s*\)\s*$", re.IGNORECASE)


def _strip_captain_mark(line):
    """(clean_line, marked, lowercase_c_used) - pull a trailing captain marker off a
    typed player line. lowercase_c_used flags the '(c)' form so we can nudge towards
    the documented '(C)'."""
    m = _CAPTAIN_MARK_RE.search(line)
    if not m:
        return line, False, False
    return line[:m.start()].rstrip(), True, (m.group(1) == "c")


# 'L' = a reluctant bowler: the AI should avoid giving them overs unless the frontline
# attack runs out of quota. Written as a trailing '(L)' or a bare trailing ' L'.
_NOBOWL_MARK_RE = re.compile(r"\s*(?:\(\s*l\s*\)|\s+l)\s*$", re.IGNORECASE)


def _strip_nobowl_mark(line):
    """(clean_line, marked) - pull a trailing 'don't bowl him' marker off a typed line."""
    m = _NOBOWL_MARK_RE.search(line)
    if not m:
        return line, False
    return line[:m.start()].rstrip(), True


def _has_wk(players):
    """True if the XI contains at least one wicket-keeper (any 'WK' role)."""
    return any("WK" in (p.get("role") or "") for p in players)


def _captain_skill_field(p):
    """Which skill a captain's +1 lands on: a batter's bat, a bowler's bowl, an
    all-rounder's STRONGER suit (bat==bowl -> random) so we never boost a weak suit."""
    role = p.get("role") or ""
    bat, bowl = int(p.get("bat", 0)), int(p.get("bowl", 0))
    if "All-Rounder" in role:
        if bat > bowl: return "bat"
        if bowl > bat: return "bowl"
        return random.choice(["bat", "bowl"])
    if "Bowler" in role:
        return "bowl"
    return "bat"          # Batter / Batter_WK


def _player_strength(p):
    """A player's headline rating (their best suit) - used to auto-pick a captain."""
    role = p.get("role") or ""
    bat, bowl = int(p.get("bat", 0)), int(p.get("bowl", 0))
    if "All-Rounder" in role: return max(bat, bowl)
    if "Bowler" in role: return bowl
    return bat


def apply_captain_boost(players, captain_name):
    """Return a NEW list with the named captain's primary skill +1 (capped 99) and an
    is_captain flag. The captain dict is COPIED so the shared global DB dict is never
    mutated (rosters often hold the same object the player DB does)."""
    if not captain_name:
        return players
    out, done = [], False
    for p in players:
        if not done and p.get("name") == captain_name and not p.get("is_captain"):
            fld = _captain_skill_field(p)
            out.append({**p, fld: min(99, int(p.get(fld, 0)) + 1),
                        "is_captain": True, "captain_skill": fld})
            done = True
        else:
            out.append(p)
    return out


def with_captain(players):
    """Guarantee a team has exactly one captain at match-build time: honor a captain
    chosen during setup (is_captain already set), otherwise auto-pick the strongest
    player. Keeps AI / sim-only / default sides on a level footing with chosen XIs."""
    if not players or any(p.get("is_captain") for p in players):
        return players
    return apply_captain_boost(players, max(players, key=_player_strength).get("name"))


def captain_note(players):
    """One-line summary of who the captain is and where their boost went, for setup msgs."""
    cap = next((p for p in players if p.get("is_captain")), None)
    if not cap:
        return ""
    skill = "Batting" if cap.get("captain_skill") == "bat" else "Bowling"
    return f"🧢 **Captain:** {cap['name']}  ·  Boost to {skill}"


def parse_pasted_roster(raw_text, db_players, max_lines=16):
    """Resolve typed names -> player dicts. A player line may end with a captain marker
    ('(C)' documented; '(c)'/'(captain)' also accepted) to name the captain inline and
    skip the captain prompt. `max_lines` caps how many lines are read (16 = 11 XI + 5
    impact; pass more for a squad). Returns
    (found_players, missing_names, captain_name, captain_error, lowercase_mark_used)."""
    # Create a lookup map where keys are lowercase for easy matching
    db_map = {p["name"].lower(): p for p in db_players}
    db_names_list = list(db_map.keys())

    found_players = []
    missing_names = []
    seen_names = set() # NEW: Tracks who is already in the XI
    captain_name = None
    captain_marks = 0
    lowercase_mark_used = False

    lines = [l.strip() for l in raw_text.split("\n") if l.strip()]
    for line in lines[:max_lines]:
        # Strip the no-bowl 'L' on both sides of the captain mark so "X (C) L" and
        # "X L (C)" both work.
        core, _nb1 = _strip_nobowl_mark(line)
        core, marked, low = _strip_captain_mark(core)
        core, _nb2 = _strip_nobowl_mark(core)
        nobowl = _nb1 or _nb2
        if marked:
            captain_marks += 1
            lowercase_mark_used = lowercase_mark_used or low
        query = core.lower()
        matched_player = None

        # 1. Exact match
        if query in db_map:
            matched_player = db_map[query]
        # 2. Fuzzy match
        else:
            matches = difflib.get_close_matches(query, db_names_list, n=1, cutoff=0.6)
            if matches:
                matched_player = db_map[matches[0]]
            else:
                missing_names.append(core)

        # 3. Duplicate check before adding to the team
        if matched_player:
            if nobowl:
                # Copy first - the matched dict is the shared DB object.
                matched_player = dict(matched_player)
                matched_player["avoid_bowl"] = True
            if matched_player["name"] not in seen_names:
                found_players.append(matched_player)
                seen_names.add(matched_player["name"])
                if marked and captain_name is None:
                    captain_name = matched_player["name"]
            else:
                # If they try to add a duplicate, flag it as an error!
                missing_names.append(f"{core} (Duplicate Entry)")

    # More than one '(C)' is ambiguous -> invalid (the caller re-prompts).
    captain_error = "multiple" if captain_marks > 1 else None
    return found_players, missing_names, captain_name, captain_error, lowercase_mark_used

def format_xi_display(players):
    lines = []
    for i, p in enumerate(players, 1):
        role_short = p["role"].replace("All-Rounder", "AR").replace("Bowler", "BWL").replace("Batter", "BAT").replace("_", " ")
        mark = " · 🚫 no-bowl (L)" if p.get("avoid_bowl") else ""
        lines.append(f"`{i:>2}.` **{p['name']}** — {role_short}{mark}")
    return "\n".join(lines)


def _saved_team_lineup(ct):
    """Resolve a saved team's stored names back to live DB dicts, re-applying the
    persisted no-bowl 'L' flags (flagged players are COPIED so the shared DB dicts
    are never mutated). Returns (players, impact, missing_xi, missing_impact)."""
    dbmap = {p["name"].lower(): p for p in get_all_players()}
    nobowl = {n.lower() for n in ct.get("nobowl", [])}

    def _take(names, cap):
        out = []
        for nm in names:
            p = dbmap.get(nm.lower())
            if p:
                out.append({**p, "avoid_bowl": True} if nm.lower() in nobowl else p)
        return out[:cap]

    players = _take(ct.get("players", []), 11)
    impact = _take(ct.get("impact", []), 5)
    missing_xi = [nm for nm in ct.get("players", []) if nm.lower() not in dbmap]
    missing_impact = [nm for nm in ct.get("impact", []) if nm.lower() not in dbmap]
    return players, impact, missing_xi, missing_impact


# Step 1: Format & Impact Player
# Step 1: Format & Impact Player

def _setup_cancelled_check(view):
    """Fail-open guard for pre-match setup views: only blocks if endmatch explicitly
    marked this setup cancelled. Leaves tournament/other flows (no flag) untouched."""
    async def interaction_check(interaction: discord.Interaction) -> bool:
        if getattr(getattr(view, "state", None), "cancelled", False):
            try:
                await interaction.response.send_message(
                    "🛑 This setup was ended. Start again with `cv match`.", ephemeral=True)
            except Exception:
                pass
            return False
        return True
    return interaction_check

class FormatSelectView(discord.ui.View):
    def __init__(self, state: MatchSetupState, channel):
        super().__init__(timeout=120)
        self.state = state
        self.channel = channel
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return await _setup_cancelled_check(self)(interaction)

    @discord.ui.select(placeholder="Select Match Format...", options=[
        discord.SelectOption(label="T20 (20 Overs)", value="20", emoji="⚡"),
        discord.SelectOption(label="ODI (50 Overs)", value="50", emoji="🏆"),
        discord.SelectOption(label="TEST (5 Days · 4 Innings)", value="90", emoji="🎩"),
        discord.SelectOption(label="Custom Format", value="custom", emoji="⚙️")
    ])
    async def select_format(self, interaction: discord.Interaction, select: discord.ui.Select):
        if interaction.user.id != self.state.p1_id and interaction.user.id != getattr(self.state, "manager_id", None):
            return await interaction.response.send_message("Only Host or Manager.", ephemeral=True)

        val = select.values[0]
        if val == "custom":
            await interaction.response.send_modal(CustomOversModal(self.state, self.channel))
        else:
            await interaction.response.defer()

            # Player tests are deliberately quota-free; all other setup flows still
            # reserve their daily allowance once a format has been selected.
            if not getattr(self.state, "is_player_test", False):
                allowed, reason = await asyncio.to_thread(consume_quota, str(interaction.user.id), str(interaction.guild.id) if interaction.guild else None, val, str(ADMIN_DISCORD_ID))
                if not allowed:
                    return await interaction.followup.send(reason, ephemeral=True)

            self.state.format_overs = int(val)
            if val == "20":
                await interaction.edit_original_response(content=f"✅ Format set: **T20 (20 overs)**\n\n🌟 <@{self.state.p1_id}> — Enable **Impact Player** rule?", view=ImpactPlayerView(self.state, self.channel))
            else:
                label = {"50": "ODI (50 overs)", "90": "Test (5 Days · 4 Innings)"}.get(val, f"{val} overs")
                await interaction.edit_original_response(content=f"✅ Format set: **{label}**", view=None)
                if getattr(self.state, "tournament_server_id", None):
                    await proceed_to_conditions(self.channel, self.state)
                else:
                    await ask_team1_name(self.channel, self.state)

class CustomOversModal(discord.ui.Modal, title="Custom Over Count"):
    overs_input = discord.ui.TextInput(label="Number of Overs (1-90)", max_length=2, required=True)
    def __init__(self, state: MatchSetupState, channel):
        super().__init__()
        self.state = state
        self.channel = channel
    async def on_submit(self, interaction: discord.Interaction):
        try:
            val = int(self.overs_input.value)
            if not (1 <= val <= 90): raise ValueError
        except: return await interaction.response.send_message("❌ Enter a number between 1 and 90.", ephemeral=True)
        
    
        await interaction.response.defer()
        if not getattr(self.state, "is_player_test", False):
            allowed, reason = await asyncio.to_thread(consume_quota, str(interaction.user.id), str(interaction.guild.id) if interaction.guild else None, "custom", str(ADMIN_DISCORD_ID))
            if not allowed:
                return await interaction.followup.send(reason, ephemeral=True)

        self.state.format_overs = val
        # FIX: Atomic edit prevents the crash
    
        await interaction.edit_original_response(content=f"✅ Format set: **Custom ({val} overs)**", view=None)
        if getattr(self.state, "tournament_server_id", None):
            await proceed_to_conditions(self.channel, self.state)
        else:
            await ask_team1_name(self.channel, self.state)

class ImpactPlayerView(discord.ui.View):
    def __init__(self, state, channel):
        super().__init__()
        self.state = state
        self.channel = channel
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return await _setup_cancelled_check(self)(interaction)
    @discord.ui.button(label="Yes (Impact Player)", style=discord.ButtonStyle.success)
    async def btn_yes(self, interaction, button):
        if interaction.user.id != self.state.p1_id and interaction.user.id != getattr(self.state, "manager_id", None): return
        self.state.impact_player = True
        
        await interaction.response.edit_message(content="✅ **Impact Player rule enabled!**", view=None)
        if getattr(self.state, "tournament_server_id", None): await proceed_to_conditions(self.channel, self.state)
        else: await ask_team1_name(self.channel, self.state)
    @discord.ui.button(label="No (Standard 11)", style=discord.ButtonStyle.secondary)
    async def btn_no(self, interaction, button):
        if interaction.user.id != self.state.p1_id and interaction.user.id != getattr(self.state, "manager_id", None): return
        self.state.impact_player = False
        # FIX: Atomic edit
        await interaction.response.edit_message(content="✅ Standard rules applied.", view=None)
        if getattr(self.state, "tournament_server_id", None): await proceed_to_conditions(self.channel, self.state)
        else: await ask_team1_name(self.channel, self.state)

# Step 2: Chat-Based Team Name / XI Prompts
# (Setup order: names -> pitch & weather -> toss -> XIs, so captains can shape
#  their 11 around the toss result and conditions.)

async def ask_team1_name(channel, state):
    await channel.send(f"🏏 <@{state.p1_id}> — Type your **team name** (e.g. `India`):\n*(Reply directly in this channel)*")
    active_setups[channel.id] = ("awaiting_team1_name", state)

def _saved_teams_hint(channel):
    """A one-line hint about loading saved custom teams at the XI step (no name list - the
    global pool can be large, so we just point to `cv teams` to browse)."""
    if not list_custom_teams():
        return "\n-# 💡 Tip: save a lineup with `cv saveteam \"<name>\"`, then just type its name here to load it."
    return "\n-# 💡 Or type a **saved team** name to load it instantly  ·  browse them with `cv teams`."

async def ask_team1_xi(channel, state):
    if state.impact_player:
        await channel.send(f"📋 <@{state.p1_id}> — Type your **Playing XI + up to 5 Subs** (one per line, 11-16 total) OR type `default`:\n```text\nPlayer 1\n...\nPlayer 11\nSub 1\n...```" + _saved_teams_hint(channel))
    else:
        await channel.send(f"📋 <@{state.p1_id}> — Type your **Playing XI** (one per line) OR type `default` for a built-in team:\n```text\nVirat Kohli\nRohit Sharma\n...```" + _saved_teams_hint(channel))
    active_setups[channel.id] = ("awaiting_team1_xi", state)

async def ask_team2_name(channel, state):
    target_id = state.p2_id if state.p2_id else state.p1_id
    await channel.send(f"🏏 <@{target_id}> — Type **Team 2's name**:\n*(Reply directly in this channel)*")
    active_setups[channel.id] = ("awaiting_team2_name", state)

async def ask_team2_xi(channel, state):
    target_id = state.p2_id if state.p2_id else state.p1_id
    if state.impact_player:
        await channel.send(f"📋 <@{target_id}> — Type **Team 2's Playing XI + up to 5 Subs** (one per line, 11-16 total) OR type `default`:\n```text\nPlayer 1\n...\nPlayer 11\nSub 1\n...```" + _saved_teams_hint(channel))
    else:
        await channel.send(f"📋 <@{target_id}> — Type **Team 2's Playing XI** (one per line) OR type `default` for a built-in team:\n```text\nPlayer Name\n...```" + _saved_teams_hint(channel))
    active_setups[channel.id] = ("awaiting_team2_xi", state)


# Step 5: XI Verification UI (the XIs are the LAST setup step, after the toss)

def _role_short(p):
    return (p.get("role") or "").replace("All-Rounder", "AR").replace("Bowler", "BWL").replace("Batter", "BAT").replace("_", " ")


def _finalize_captain(state, team_num, name):
    """Lock in the captain for a team and apply the +1 boost to its roster."""
    if team_num == 1:
        state.t1_captain = name
        state.t1_roster = apply_captain_boost(state.t1_roster, name)
    else:
        state.t2_captain = name
        state.t2_roster = apply_captain_boost(state.t2_roster, name)


class CaptainSelectView(discord.ui.View):
    """Pick the captain from a locked XI. The chosen player gets +1 to their main skill
    (an all-rounder's stronger suit; bat==bowl -> random). `after(channel, state)` is the
    coroutine that continues setup once a captain is chosen."""
    def __init__(self, state, channel, team_num, players, after):
        super().__init__(timeout=120)
        self.state = state
        self.channel = channel
        self.team_num = team_num
        self.after = after
        opts = [discord.SelectOption(label=p["name"][:100], description=_role_short(p)[:100], value=p["name"])
                for p in players[:25]]
        sel = discord.ui.Select(placeholder="🧢 Choose your Captain...", options=opts)
        sel.callback = self._pick
        self.add_item(sel)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if not await _setup_cancelled_check(self)(interaction):
            return False
        owner = self.state.p1_id if self.team_num == 1 else (getattr(self.state, "p2_id", None) or self.state.p1_id)
        if interaction.user.id != owner and interaction.user.id != getattr(self.state, "manager_id", None):
            await interaction.response.send_message("❌ Only this team's owner can choose the captain.", ephemeral=True)
            return False
        return True

    async def _pick(self, interaction: discord.Interaction):
        _finalize_captain(self.state, self.team_num, interaction.data["values"][0])
        roster = self.state.t1_roster if self.team_num == 1 else self.state.t2_roster
        await interaction.response.edit_message(content=captain_note(roster), view=None)
        await self.after(self.channel, self.state)


async def handle_captain_step(channel, state, team_num, players, after):
    """After an XI is locked: if a captain was named inline with '(C)', finalize and move
    on; otherwise ask who the captain is. `after(channel, state)` runs once one is set."""
    chosen = getattr(state, "t1_captain" if team_num == 1 else "t2_captain", None)
    if chosen and chosen in {p["name"] for p in players}:
        _finalize_captain(state, team_num, chosen)
        roster = state.t1_roster if team_num == 1 else state.t2_roster
        await channel.send(captain_note(roster))
        return await after(channel, state)
    owner_id = state.p1_id if team_num == 1 else (getattr(state, "p2_id", None) or state.p1_id)
    await channel.send(
        f"🧢 <@{owner_id}> — **Who is your Captain?** They get a **boost** to their main skill.\n"
        f"-# Tip: next time put `(C)` after a name in your XI to set the captain inline and skip this step.",
        view=CaptainSelectView(state, channel, team_num, players, after))


class Team1VerifyView(discord.ui.View):
    def __init__(self, state, channel, players):
        super().__init__(timeout=120)
        self.state = state
        self.channel = channel
        self.players = players
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return await _setup_cancelled_check(self)(interaction)
    @discord.ui.button(label="✅ Confirm XI", style=discord.ButtonStyle.success)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.state.p1_id: return await interaction.response.send_message("Only Team 1 can confirm.", ephemeral=True)
        self.state.t1_roster = self.players
        await interaction.response.defer()
        await interaction.message.edit(view=None)
        await self.channel.send("✅ **Team 1 XI confirmed!**")
        await handle_captain_step(self.channel, self.state, 1, self.players, after_team1_xi)
    @discord.ui.button(label="✏️ Re-enter XI", style=discord.ButtonStyle.danger)
    async def redo(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.state.p1_id: return
        await interaction.response.defer()
        await interaction.message.edit(view=None)
        await ask_team1_xi(self.channel, self.state)

class Team2VerifyView(discord.ui.View):
    def __init__(self, state, channel, players):
        super().__init__(timeout=120)
        self.state = state
        self.channel = channel
        self.players = players
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return await _setup_cancelled_check(self)(interaction)
    @discord.ui.button(label="✅ Confirm XI", style=discord.ButtonStyle.success)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        target_id = self.state.p2_id if self.state.p2_id else self.state.p1_id
        if interaction.user.id != target_id: return await interaction.response.send_message("Only Team 2 can confirm.", ephemeral=True)
        self.state.t2_roster = self.players
        await interaction.response.defer()
        await interaction.message.edit(view=None)
        await self.channel.send("✅ **Team 2 XI confirmed!**")
        await handle_captain_step(self.channel, self.state, 2, self.players, start_match)
    @discord.ui.button(label="✏️ Re-enter XI", style=discord.ButtonStyle.danger)
    async def redo(self, interaction: discord.Interaction, button: discord.ui.Button):
        target_id = self.state.p2_id if self.state.p2_id else self.state.p1_id
        if interaction.user.id != target_id: return
        await interaction.response.defer()
        await interaction.message.edit(view=None)
        await ask_team2_xi(self.channel, self.state)
        
async def prompt_tournament_xi(channel, state, team_num):
    """Tournament XI selection: paste the 11 (order = batting order, optional '(C)'
    captain marker) or hit  Use Default XI. Replaces the old one-by-one dropdown
    picker. Validates: every name in the (injury-filtered) squad, no duplicates,
    exactly 11, and a wicket-keeper present."""
    owner_id = state.p1_id if team_num == 1 else state.p2_id
    t_name = state.t1_name if team_num == 1 else state.t2_name
    squad = state.t1_squad if team_num == 1 else state.t2_squad
    default_xi = getattr(state, f"t{team_num}_default_xi", None)
    default_cap = getattr(state, f"t{team_num}_default_captain", None)
    default_subs = getattr(state, f"t{team_num}_default_subs", None) or []
    impact_on = getattr(state, "impact_player", False)

    async def _lock_xi(xi, captain_name, via, subs=None):
        remaining = [p for p in squad if p not in xi and not p.get("injured")]
        if team_num == 1:
            state.t1_roster = xi; state.t1_subs = list(subs) if subs else []
        else:
            state.t2_roster = xi; state.t2_subs = list(subs) if subs else []

        async def _after(ch, st):
            if impact_on and not subs and remaining:
                v = TournamentSubSelectView(st, ch, team_num, remaining)
                await ch.send(v.get_msg_content(), view=v)
            elif team_num == 1:
                await prompt_tournament_xi(ch, st, 2)
            else:
                await start_match(ch, st)

        locked = f"✅ **{t_name} XI locked** ({via}):\n" + format_xi_display(xi)
        if subs:
            locked += "\n🔄 Impact Subs: " + ", ".join(f"**{p['name']}**" for p in subs)
        await channel.send(locked)
        if captain_name and captain_name in {p["name"] for p in xi}:
            setattr(state, f"t{team_num}_captain", captain_name)
        await handle_captain_step(channel, state, team_num, xi, _after)

    # prompt message: instructions + the squad for reference
    inj_note = ""
    full_squad = state.t1_squad if team_num == 1 else state.t2_squad
    msg = (f"📋 <@{owner_id}> (or Manager) — **{t_name} XI Selection**\n"
           f"**Paste your XI below** — 11 names, one per line. "
           f"⚠️ The order is your exact **batting order**; add `(C)` after a name to set the captain.\n")
    if impact_on:
        msg += "🔄 Lines 12-16 (optional): your **Impact Subs** — skips the sub picker.\n"
    if default_xi:
        msg += "…or press ✅ **Use Default XI** (also works by typing `default`)"
        msg += f" — includes its {len(default_subs)} saved Impact Sub(s).\n" if default_subs else ".\n"
    elif (getattr(state, 'tournament_server_id', None)
          and (getattr(state, f't{team_num}_default_xi', 'x') is None)):
        msg += "-# *(saved default XI unavailable: player injured or no longer in squad)*\n"
    msg += "\n**Squad:** " + " · ".join(p["name"] for p in squad)

    view = discord.ui.View(timeout=None)
    view.done = False
    if default_xi:
        btn = discord.ui.Button(label="✅ Use Default XI", style=discord.ButtonStyle.success)

        async def _use_default(inter: discord.Interaction):
            if inter.user.id != owner_id and inter.user.id != getattr(state, "manager_id", None):
                return await inter.response.send_message("❌ Only the Team Owner or Manager can pick this XI.", ephemeral=True)
            if view.done:
                return await inter.response.defer()
            view.done = True
            try:
                await inter.response.edit_message(view=None)
            except discord.HTTPException:
                pass
            await _lock_xi(list(default_xi), default_cap, "default XI", subs=default_subs)
        btn.callback = _use_default
        view.add_item(btn)

    await channel.send(msg, view=view if default_xi else None)

    def _check(m):
        return (m.channel.id == channel.id and not m.author.bot
                and m.author.id in (owner_id, getattr(state, "manager_id", None)))

    while not view.done:
        # Abort silently if this setup was cancelled/replaced (endmatch, new match...).
        cur = active_setups.get(channel.id)
        if not cur or cur[1] is not state:
            return
        try:
            reply = await bot.wait_for("message", timeout=300, check=_check)
        except asyncio.TimeoutError:
            continue   # no hard timeout - same behaviour as the old no-timeout picker
        if view.done:
            return
        content = reply.content.strip()
        if content.lower() in ("default", "default xi", "d"):
            if default_xi:
                view.done = True
                return await _lock_xi(list(default_xi), default_cap, "default XI", subs=default_subs)
            await reply.reply("❌ No valid default XI saved for this team — paste the 11 names instead.")
            continue
        lines = [l for l in content.split("\n") if l.strip()]
        if len(lines) < 8:
            continue   # ordinary chat - ignore, keep waiting for a pasted XI
        found, missing, cap_name, cap_err, _low = parse_pasted_roster(
            content, squad, max_lines=16 if impact_on else 13)
        xi, subs = found[:11], found[11:]
        errs = []
        if missing:
            errs.append("Not in the (fit) squad: " + ", ".join(f"**{n}**" for n in missing[:6]))
        if len(found) < 11:
            errs.append(f"Need at least **11** players — found **{len(found)}**.")
        if not impact_on and len(found) > 11:
            errs.append(f"No Impact Player rule in this tournament — paste exactly 11 (found {len(found)}).")
        if cap_err == "multiple":
            errs.append("Multiple `(C)` markers — mark exactly one captain (or none).")
        if cap_name and len(found) >= 11 and cap_name not in {p["name"] for p in xi}:
            errs.append(f"Captain **{cap_name}** is among the impact subs — the `(C)` goes on one of the first 11.")
        if len(xi) == 11 and not _has_wk(xi):
            errs.append("No **Wicket-Keeper** in the XI — include a `WK`-role player.")
        if errs:
            await reply.reply("❌ **Invalid XI:**\n• " + "\n• ".join(errs) + "\n*Fix and paste again.*")
            continue
        view.done = True
        for item in view.children:
            item.disabled = True
        return await _lock_xi(xi, cap_name, "typed", subs=subs if impact_on else None)


# (The old one-by-one TournamentXIView dropdown picker was replaced by the
#  paste / default-XI flow in prompt_tournament_xi above.)


class TournamentSubSelectView(discord.ui.View):
    def __init__(self, state, channel, team_num, remaining):
        # No timeout - same reason as TournamentXIView: don't let the picker expire
        # mid-selection.
        super().__init__(timeout=None)
        self.state = state
        self.channel = channel
        self.team_num = team_num
        self.remaining = remaining
        self.owner_id = state.p1_id if team_num == 1 else state.p2_id
        self.selected_subs = []
        self.max_subs = min(5, len(remaining))
        self.update_ui()

    def update_ui(self):
        self.clear_items()
        if len(self.selected_subs) < self.max_subs:
            options = []
            for p in self.remaining:
                if p not in self.selected_subs:
                    role_short = p["role"].replace("All-Rounder", "AR").replace("Bowler", "BWL").replace("Batter", "BAT").replace("_", " ")
                    options.append(discord.SelectOption(label=p["name"], description=role_short, value=p["name"]))
            if options:
                select = discord.ui.Select(placeholder=f"Add Impact Sub {len(self.selected_subs)+1}...", options=options[:25])
                select.callback = self.select_cb
                self.add_item(select)

        btn_undo = discord.ui.Button(label="Remove Last", style=discord.ButtonStyle.danger, disabled=len(self.selected_subs) == 0)
        btn_undo.callback = self.undo_cb
        self.add_item(btn_undo)

        btn_confirm = discord.ui.Button(label="Confirm Subs", style=discord.ButtonStyle.success)
        btn_confirm.callback = self.confirm_cb
        self.add_item(btn_confirm)

    async def interaction_check(self, interaction: discord.Interaction):
        if interaction.user.id != self.owner_id and interaction.user.id != getattr(self.state, "manager_id", None):
            await interaction.response.send_message("❌ Only the Team Owner or Manager can select subs.", ephemeral=True)
            return False
        return True

    def get_msg_content(self):
        t_name = self.state.t1_name if self.team_num == 1 else self.state.t2_name
        msg = f"🔄 <@{self.owner_id}> (or Manager) — **{t_name} Impact Subs**\n"
        msg += f"Select up to **{self.max_subs} sub(s)** from your remaining squad, or click **Confirm Subs** with 0 to play without subs.\n\n"
        if self.selected_subs:
            for i, p in enumerate(self.selected_subs, 1):
                role_short = p["role"].replace("All-Rounder", "AR").replace("Bowler", "BWL").replace("Batter", "BAT").replace("_", " ")
                msg += f"`{i:>2}.` **{p['name']}** — {role_short}\n"
        else:
            msg += "*No subs selected yet.*\n"
        return msg

    async def _safe_edit(self, interaction: discord.Interaction, *, content=None, view=None):
        # Survive a dead interaction token (10062) by falling back to a bot-token
        # message edit, so a stalled/restarted loop never crashes the picker.
        content = self.get_msg_content() if content is None else content
        try:
            await interaction.response.edit_message(content=content, view=view)
        except discord.NotFound:
            try:
                await interaction.message.edit(content=content, view=view)
            except discord.HTTPException:
                pass

    async def select_cb(self, interaction: discord.Interaction):
        val = interaction.data["values"][0]
        player = next(p for p in self.remaining if p["name"] == val)
        self.selected_subs.append(player)
        self.update_ui()
        await self._safe_edit(interaction, view=self)

    async def undo_cb(self, interaction: discord.Interaction):
        self.selected_subs.pop()
        self.update_ui()
        await self._safe_edit(interaction, view=self)

    async def confirm_cb(self, interaction: discord.Interaction):
        t_name = self.state.t1_name if self.team_num == 1 else self.state.t2_name
        msg = f"✅ **{t_name} Impact Subs Confirmed!** ({len(self.selected_subs)} selected)"
        # Guard the edit so a dead token can't stall the hand-off to the next step.
        await self._safe_edit(interaction, content=msg, view=None)
        if self.team_num == 1:
            self.state.t1_subs = self.selected_subs
            await prompt_tournament_xi(self.channel, self.state, 2)
        else:
            self.state.t2_subs = self.selected_subs
            await start_match(self.channel, self.state)


# Step 3: Pitch & Weather Select

async def ask_pitch_and_weather(channel, state):
    await channel.send(f"🏟️ <@{state.home_team_id}> (**{state.t1_name}** — Home Team) — Select **Pitch & Weather** conditions:", view=PitchWeatherView(state, channel))

async def proceed_to_conditions(channel, state):
    """Tournament matches in auto/home conditions mode have pitch+weather preset -> skip the
    picker and go straight to the toss. Manual mode (and casual/draft) still asks."""
    if getattr(state, "conditions_preset", False) and getattr(state, "pitch", None) and getattr(state, "weather", None):
        await channel.send(f"🏟️ **Pitch:** {state.pitch}  ·  🌤️ **Weather:** {state.weather}\n\nProceeding to the **toss**...")
        await begin_pre_toss(channel, state)
    else:
        await ask_pitch_and_weather(channel, state)

class PitchWeatherView(discord.ui.View):
    def __init__(self, state, channel):
        super().__init__(timeout=120)
        self.state = state
        self.channel = channel
        self.s_pitch = None
        self.s_weather = None
        # Test only: pick Red (day) or Pink (day-night) ball. None = not yet chosen (gates proceed).
        self._is_test = getattr(state, "format_overs", 20) == 90
        self.s_pink = None if self._is_test else False
        if self._is_test:
            ball_sel = discord.ui.Select(placeholder="🏏 Ball Type (Day or Day-Night)...", row=2, options=[
                discord.SelectOption(label="Red Ball — Day Test", value="red", emoji="🔴"),
                discord.SelectOption(label="Pink Ball — Day-Night Test", value="pink", emoji="<:pink:1518481735266996255>",
                                     description="Swings & seams under lights — twilight is lethal for pace"),
            ])
            ball_sel.callback = self._ball_cb
            self.add_item(ball_sel)
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return await _setup_cancelled_check(self)(interaction)
        

    async def _ball_cb(self, interaction):
        if interaction.user.id != self.state.home_team_id and interaction.user.id != getattr(self.state, "manager_id", None):
            return
        self.s_pink = (interaction.data["values"][0] == "pink")
        await interaction.response.defer()
        await self.check_proceed(interaction)

    @discord.ui.select(placeholder="🏏 Select Pitch Type...", row=0, options=[
        discord.SelectOption(label="Flat — Batting Paradise", value="Flat", emoji="🟩"),
        discord.SelectOption(label="Green — Seam & Swing", value="Green", emoji="🌿"),
        discord.SelectOption(label="Dry — Hard & Cracking", value="Dry", emoji="🏜️"),
        discord.SelectOption(label="Dusty — Spin Friendly", value="Dusty", emoji="🌾"),
        discord.SelectOption(label="Hard — Bounce & Pace", value="Hard", emoji="🪨"),
        discord.SelectOption(label="Soft — Slow & Low", value="Soft", emoji="🧽"),
        discord.SelectOption(label="Cracked — Uneven Bounce", value="Cracked", emoji="🕸️"),
        discord.SelectOption(label="Damp — Early Seam", value="Damp", emoji="💧"),
        discord.SelectOption(label="Dead — Absolute Road", value="Dead", emoji="🛣️"),
        discord.SelectOption(label="Worn — Late Spin", value="Worn", emoji="🕰️"),
        discord.SelectOption(label="Turning — Sharp Spin", value="Turning", emoji="🌀"),
        discord.SelectOption(label="Two-Paced — Variable Speed", value="Two-Paced", emoji="⚖️"),
        discord.SelectOption(label="Slow — Hard to Score", value="Slow", emoji="🐢"),
        discord.SelectOption(label="Bouncy — Extra Carry", value="Bouncy", emoji="🦘"),
        discord.SelectOption(label="Sticky — Unplayable", value="Sticky", emoji="🍯")
    ])
    async def pitch_cb(self, interaction, select):
        if interaction.user.id != self.state.home_team_id and interaction.user.id != getattr(self.state, "manager_id", None): return
        self.s_pitch = select.values[0]
        await interaction.response.defer()
        await self.check_proceed(interaction)

    @discord.ui.select(placeholder="🌤️ Select Weather...", row=1, options=[
        discord.SelectOption(label="Clear — Ideal Batting", value="Clear", emoji="☀️"),
        discord.SelectOption(label="Cloudy — Balanced", value="Cloudy", emoji="⛅"),
        discord.SelectOption(label="Overcast — Heavy Swing", value="Overcast", emoji="☁️"),
        discord.SelectOption(label="Humid — Sweaty & Swing", value="Humid", emoji="🥵"),
        discord.SelectOption(label="Dry Heat — Late Spin", value="Dry Heat", emoji="🏜️"),
        discord.SelectOption(label="Windy — Fast Swing", value="Windy", emoji="🌬️"),
        discord.SelectOption(label="Light Rain — Wet Ball", value="Light Rain", emoji="🌦️"),
        discord.SelectOption(label="Drizzle — Slippery", value="Drizzle", emoji="🌧️"),
        discord.SelectOption(label="Heavy Rain — DLS Active", value="Heavy Rain", emoji="⛈️"),
        discord.SelectOption(label="Thunderstorm — Severe DLS", value="Thunderstorm", emoji="🌩️")
    ])
    async def weather_cb(self, interaction, select):
        if interaction.user.id != self.state.home_team_id and interaction.user.id != getattr(self.state, "manager_id", None): return
        self.s_weather = select.values[0]
        await interaction.response.defer()
        await self.check_proceed(interaction)

    async def check_proceed(self, interaction):
        if not (self.s_pitch and self.s_weather):
            return
        if self._is_test and self.s_pink is None:
            return  # wait until the Red/Pink ball is chosen
        self.state.pitch = self.s_pitch
        self.state.weather = self.s_weather
        self.state.pink_ball = bool(self.s_pink)
        await interaction.message.edit(view=None)
        note = " *(DLS rules active)*" if self.state.weather == "Rain Threat" else ""
        ball_txt = "  ·  <:pink:1518481735266996255> **Pink Ball (Day-Night)**" if self.s_pink else ("  ·  🔴 Red Ball" if self._is_test else "")
        await self.channel.send(f"✅ Pitch: **{self.s_pitch}** | Weather: **{self.s_weather}**{ball_txt}{note}\n\nProceeding to the **toss**...")
        await begin_pre_toss(self.channel, self.state)


# Step 4: Toss Engine (runs BEFORE the XIs so captains can shape their 11 around it)

async def begin_pre_toss(channel, state):
    """Interactive toss on the setup state. The result (winner + Bat/Bowl choice)
    is stored on the state; start_match applies it once the XIs are locked."""
    if getattr(state, 'sim_only', False):
        # /simulatematch rolls its own toss at sim time - nothing to ask here.
        return await continue_to_xi(channel, state)
    if state.p2_id is None:
        # vs AI: auto coin flip, the human only decides if they win it
        if random.choice([True, False]):
            state.toss_winner_id = state.p1_id
            await channel.send(f"🪙 **Toss!** You won the toss, <@{state.p1_id}>. Select your decision:", view=PreTossDecisionView(state, channel))
        else:
            state.toss_winner_id = None   # AI (team 2) won
            state.toss_choice = random.choice(["Bat", "Bowl"])
            await channel.send(f"🪙 **Toss!** AI wins and elects to **{state.toss_choice} First**!")
            await continue_to_xi(channel, state)
    else:
        await channel.send(f"🪙 **Toss Time!** <@{state.p2_id}> — call the coin!", view=PreTossCallView(state, channel))


class PreTossCallView(discord.ui.View):
    """Pre-XI toss: the opponent calls the coin while both XIs are still open."""
    def __init__(self, state, channel):
        super().__init__(timeout=300)
        self.state = state
        self.channel = channel
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return await _setup_cancelled_check(self)(interaction)
    async def handle_call(self, interaction, call):
        if interaction.user.id != self.state.p2_id and interaction.user.id != getattr(self.state, "manager_id", None): return
        flip = random.choice(["Heads", "Tails"])
        self.state.toss_winner_id = self.state.p2_id if call == flip else self.state.p1_id
        await interaction.response.defer()
        await interaction.message.edit(view=None)
        await interaction.channel.send(f"🪙 Landed on **{flip}**! <@{self.state.toss_winner_id}> wins the toss — choose:", view=PreTossDecisionView(self.state, self.channel))
    @discord.ui.button(label="Heads", style=discord.ButtonStyle.primary)
    async def heads(self, interaction, button): await self.handle_call(interaction, "Heads")
    @discord.ui.button(label="Tails", style=discord.ButtonStyle.secondary)
    async def tails(self, interaction, button): await self.handle_call(interaction, "Tails")


class PreTossDecisionView(discord.ui.View):
    """Toss winner picks Bat/Bowl; the XIs are entered after this."""
    def __init__(self, state, channel):
        super().__init__(timeout=300)
        self.state = state
        self.channel = channel
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if not await _setup_cancelled_check(self)(interaction):
            return False
        if interaction.user.id != self.state.toss_winner_id and interaction.user.id != getattr(self.state, "manager_id", None):
            await interaction.response.send_message("Not your turn.", ephemeral=True)
            return False
        return True
    async def finalize_toss(self, interaction, choice):
        self.state.toss_choice = choice
        await interaction.response.defer()
        await interaction.message.edit(view=None)
        win_name = self.state.t1_name if self.state.toss_winner_id == self.state.p1_id else self.state.t2_name
        await self.channel.send(f"🪙 **{win_name}** win the toss and elect to **{choice.lower()} first**!")
        await continue_to_xi(self.channel, self.state)
    @discord.ui.button(label="🏏 Bat First", style=discord.ButtonStyle.success)
    async def bat(self, interaction, button): await self.finalize_toss(interaction, "Bat")
    @discord.ui.button(label="🎯 Bowl First", style=discord.ButtonStyle.danger)
    async def bowl(self, interaction, button): await self.finalize_toss(interaction, "Bowl")


async def continue_to_xi(channel, state):
    """After the toss: collect the XIs - or start straight away if they're already
    locked (draft and player-test matches hand their rosters in pre-made)."""
    if state.t1_roster and state.t2_roster:
        return await start_match(channel, state)
    if getattr(state, "tournament_server_id", None):
        return await prompt_tournament_xi(channel, state, 1)
    await ask_team1_xi(channel, state)


async def after_team1_xi(channel, state):
    """Continuation once Team 1's XI + captain are locked. An AI opponent's roster
    was preset at the name step, so the match can start immediately."""
    if state.t2_roster:
        return await start_match(channel, state)
    await ask_team2_xi(channel, state)


# Final step: build the match object and start play (the toss already ran pre-XI)

async def start_match(channel, state):
    # Test format (90 overs) uses a completely different simulation engine
    if state.format_overs == 90:
        return await _begin_test_match(channel, state)

    _sid = getattr(state, "tournament_server_id", None) or (str(channel.guild.id) if getattr(channel, "guild", None) else None)
    t1 = {"name": state.t1_name, "players": with_captain(apply_tournament_boosts(apply_server_overrides(state.t1_roster, _sid))), "subs": apply_tournament_boosts(apply_server_overrides(getattr(state, 't1_subs', []), _sid)), "color": getattr(state, 't1_color', '#6B7280')}
    t2 = {"name": state.t2_name, "players": with_captain(apply_tournament_boosts(apply_server_overrides(state.t2_roster, _sid))), "subs": apply_tournament_boosts(apply_server_overrides(getattr(state, 't2_subs', []), _sid)), "color": getattr(state, 't2_color', '#6B7280')}

    match = CricketMatch(state.p1, state.p2, state.p1_id, state.p2_id, t1, t2, state.format_overs, state.pitch, state.weather)
    match.impact_player = state.impact_player
    match.is_player_test = getattr(state, "is_player_test", False)
    match.tournament_server_id = getattr(state, "tournament_server_id", None)
    match.tournament_match_id = getattr(state, "tournament_match_id", None)
    match.manager_id = getattr(state, "manager_id", None)
    match.tournament_name = getattr(state, "tournament_name", "TOURNAMENT")
    match.tournament_type = getattr(state, "tournament_type", None)   # "dsl" flips the engine's league-realism mode
    # Draft mode: carry the result-recording info so the leaderboard updates on finish
    if getattr(state, "is_draft", False):
        match.is_draft = True
        match.draft_host_id = state.draft_host_id
        match.draft_host_name = state.draft_host_name
        match.draft_opp_id = state.draft_opp_id      # None == vs AI
        match.draft_opp_name = state.draft_opp_name
        match.draft_host_team = state.t1_name        # team1 is always the host
    active_games[channel.id] = match
    # Setup is done - hand the cancellation baton to active_games (play views guard on it).
    setup_states.pop(channel.id, None)

    if getattr(state, 'sim_only', False):
        match.sim_only = True
        match.simulation_mode = "whole_match"
        match.verbose = False
        
        winner_name = random.choice([match.team1["name"], match.team2["name"]])
        decision = random.choice(["Bat", "Bowl"])
        await channel.send(f"🪙 **Toss!** **{winner_name}** wins the toss and elects to **{decision}** first!\n*Simulating match in the background... ⚙️*")
        
        if winner_name == match.team1["name"]:
            match.batting_first_id = match.p1_id
            match.bowling_first_id = match.p1_id
            t_bat = match.team1 if decision == "Bat" else match.team2
            t_bowl = match.team2 if decision == "Bat" else match.team1
        else:
            match.batting_first_id = match.p1_id
            match.bowling_first_id = match.p1_id
            t_bat = match.team2 if decision == "Bat" else match.team1
            t_bowl = match.team1 if decision == "Bat" else match.team2
            
        match.innings1 = InningsState(t_bat, t_bowl)
        match.current_innings = match.innings1
        
        await loop_entire_match_simulation(channel, match)
        return

    # Apply the pre-XI toss result (winner None = the AI side, team 2).
    match.toss_winner = state.toss_winner_id
    apply_toss_decision(match, state.toss_choice)
    win_name = match.team1["name"] if state.toss_winner_id == match.p1_id else match.team2["name"]
    lose_name = match.team2["name"] if state.toss_winner_id == match.p1_id else match.team1["name"]
    bat_name = win_name if state.toss_choice == "Bat" else lose_name
    await channel.send(f"🪙 Toss recap: **{win_name}** chose to **{state.toss_choice.lower()} first** — **{bat_name}** will bat. Let's play!")
    await prompt_bowler_then_hub(channel, match)

class TossCallView(discord.ui.View):
    def __init__(self, match):
        super().__init__(timeout=300)
        self.match = match
    async def handle_call(self, interaction, call):
        if interaction.user.id != self.match.p2_id and interaction.user.id != getattr(self.match, "manager_id", None): return
        flip = random.choice(["Heads", "Tails"])
        self.match.toss_winner = interaction.user.id if call == flip else self.match.p1_id
        await interaction.response.defer()
        await interaction.message.edit(view=None)
        await interaction.channel.send(f"🪙 Landed on **{flip}**! <@{self.match.toss_winner}> wins the toss — choose:", view=TossDecisionView(self.match))
    @discord.ui.button(label="Heads", style=discord.ButtonStyle.primary)
    async def heads(self, interaction, button): await self.handle_call(interaction, "Heads")
    @discord.ui.button(label="Tails", style=discord.ButtonStyle.secondary)
    async def tails(self, interaction, button): await self.handle_call(interaction, "Tails")

def apply_toss_decision(match, choice):
    """Set batting/bowling order + innings1 from the toss winner's Bat/Bowl choice."""
    if choice == "Bat":
        match.batting_first_id = match.toss_winner
        match.bowling_first_id = match.p1_id if match.toss_winner == match.p2_id else match.p2_id
        t_bat = match.team1 if match.toss_winner == match.p1_id else match.team2
        t_bowl = match.team2 if match.toss_winner == match.p1_id else match.team1
    else:
        match.bowling_first_id = match.toss_winner
        match.batting_first_id = match.p1_id if match.toss_winner == match.p2_id else match.p2_id
        t_bowl = match.team1 if match.toss_winner == match.p1_id else match.team2
        t_bat = match.team2 if match.toss_winner == match.p1_id else match.team1
    match.innings1 = InningsState(t_bat, t_bowl)
    match.current_innings = match.innings1


class TossDecisionView(discord.ui.View):
    def __init__(self, match):
        super().__init__(timeout=300)
        self.match = match
        
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.channel.id not in active_games or active_games[interaction.channel.id] != self.match:
            await interaction.response.send_message("❌ This match has been ended.", ephemeral=True)
            return False
        if interaction.user.id != self.match.toss_winner and interaction.user.id != getattr(self.match, "manager_id", None):
            await interaction.response.send_message("Not your turn.", ephemeral=True)
            return False
        return True
        
    async def finalize_toss(self, interaction, choice):
        apply_toss_decision(self.match, choice)
        await interaction.response.defer()
        await interaction.message.edit(view=None)
        if getattr(self.match, "is_club", False):
            await prompt_club_openers(interaction, self.match)
        else:
            await prompt_bowler_then_hub(interaction, self.match)
    @discord.ui.button(label="🏏 Bat First", style=discord.ButtonStyle.success)
    async def bat(self, interaction, button): await self.finalize_toss(interaction, "Bat")
    @discord.ui.button(label="🎯 Bowl First", style=discord.ButtonStyle.danger)
    async def bowl(self, interaction, button): await self.finalize_toss(interaction, "Bowl")


# The Listener connecting Chat inputs to the State Machine

@bot.event
async def on_message(message: discord.Message):
    if message.author.bot: return

    content = message.content
    if content.startswith("cvt "):
        message.content = "cv tournament " + content[4:]
    elif content.startswith("cv t "):
        message.content = "cv tournament " + content[5:]
    elif content.startswith("cvd "):
        message.content = "cv draft " + content[4:]
    elif content.strip() == "cvd":
        message.content = "cv draft"

    channel_id = message.channel.id
    if channel_id not in active_setups:
        return await bot.process_commands(message)

    stage, state = active_setups[channel_id]

    if stage == "awaiting_team1_name":
        if message.author.id != state.p1_id: return
        state.t1_name = message.content.strip()[:30]
        del active_setups[channel_id]
        await message.channel.send(f"✅ Team 1 name set: **{state.t1_name}**")
        await ask_team2_name(message.channel, state)

    elif stage == "awaiting_team1_xi":
        if message.author.id != state.p1_id: return
        
        req_length = 11
        typed = message.content.strip()
        _ct = get_custom_team(typed)
        cap_name, cap_err, cap_low = None, None, False
        if typed.lower() == "default":
            players = list(TEAMS_DATA["Team 1"]["players"])
            if state.impact_player:
                state.t1_subs = [{"name": "Faf du Plessis", "bat": 85, "bowl": 10, "archetype": "Aggressor", "role": "Batter"}, {"name": "Lockie Ferguson", "bat": 10, "bowl": 85, "archetype": "Standard", "role": "Bowler_Pace"}]
            else:
                state.t1_subs = []
            missing = []
        elif _ct:
            players, _impact, missing, _ = _saved_team_lineup(_ct)
            # Impact subs only matter in impact-mode matches; ignored otherwise.
            state.t1_subs = _impact if state.impact_player else []
        else:
            db = get_all_players()
            parsed_players, missing, cap_name, cap_err, cap_low = parse_pasted_roster(message.content, db)
            players = parsed_players[:11]
            state.t1_subs = parsed_players[11:16] if state.impact_player else []

        if missing or len(players) < req_length:
            err = f"❌ **Roster Validation Failed ({len(players)}/{req_length} Found)**\n\n"
            if players: err += f"✅ **Accepted:** {', '.join([p['name'] for p in players])}\n"
            if missing: err += f"❌ **Missing from DB:** {', '.join(missing)}\n\n"
            err += f"Please check spellings or add missing players to your CSV, then type your full list again."
            return await message.channel.send(err)
        if cap_err == "multiple":
            return await message.channel.send("❌ **Invalid — more than one captain marked.**\nPut `(C)` after exactly **one** player, then type your XI again.")
        if not _has_wk(players):
            return await message.channel.send("❌ **Invalid XI — no Wicket-Keeper.**\nEvery team must include at least one keeper (a `WK`-role player). Add a wicket-keeper, then type your full XI again.")

        # A captain marked with '(C)' must be one of the XI; otherwise we'll prompt for it.
        state.t1_captain = cap_name if (cap_name and cap_name in {p["name"] for p in players}) else None

        del active_setups[channel_id]
        xi_text = format_xi_display(players)
        if state.impact_player and state.t1_subs:
            xi_text += "\n\n**Impact Subs:**\n" + format_xi_display(state.t1_subs)
        cap_line = ""
        if state.t1_captain:
            cap_line = f"\n\n🧢 **Captain (from your list):** {state.t1_captain}"
            if cap_low:
                cap_line += "\n-# Noted `(c)` — the documented marker is uppercase `(C)`."
        await message.channel.send(f"📋 **{state.t1_name} XI** Verified:\n{xi_text}{cap_line}\n\nIs this correct?", view=Team1VerifyView(state, message.channel, players))

    elif stage == "awaiting_team2_name":
        target_id = state.p2_id if state.p2_id else state.p1_id
        if message.author.id != target_id: return
        state.t2_name = message.content.strip()[:30]
        del active_setups[channel_id]
        await message.channel.send(f"✅ Team 2 name set: **{state.t2_name}**")
        
        if state.p2_id is None and not getattr(state, 'sim_only', False):
            state.t2_roster = TEAMS_DATA["Team 2"]["players"]
            if getattr(state, "impact_player", False):
                state.t2_subs = [{"name": "Devon Conway", "bat": 85, "bowl": 10, "archetype": "Aggressor", "role": "Batter"}, {"name": "Anrich Nortje", "bat": 10, "bowl": 85, "archetype": "Standard", "role": "Bowler_Pace"}]
            else:
                state.t2_subs = []
            await message.channel.send(f"🤖 AI team **{state.t2_name}** will use the built-in roster.")
            await proceed_to_conditions(message.channel, state)
        else:
            await proceed_to_conditions(message.channel, state)

    elif stage == "awaiting_team2_xi":
        target_id = state.p2_id if state.p2_id else state.p1_id
        if message.author.id != target_id: return
        
        req_length = 11
        typed = message.content.strip()
        _ct = get_custom_team(typed)
        cap_name, cap_err, cap_low = None, None, False
        if typed.lower() == "default":
            players = list(TEAMS_DATA["Team 2"]["players"])
            if state.impact_player:
                state.t2_subs = [{"name": "Devon Conway", "bat": 85, "bowl": 10, "archetype": "Aggressor", "role": "Batter"}, {"name": "Anrich Nortje", "bat": 10, "bowl": 85, "archetype": "Standard", "role": "Bowler_Pace"}]
            else:
                state.t2_subs = []
            missing = []
        elif _ct:
            players, _impact, missing, _ = _saved_team_lineup(_ct)
            # Impact subs only matter in impact-mode matches; ignored otherwise.
            state.t2_subs = _impact if state.impact_player else []
        else:
            db = get_all_players()
            parsed_players, missing, cap_name, cap_err, cap_low = parse_pasted_roster(message.content, db)
            players = parsed_players[:11]
            state.t2_subs = parsed_players[11:16] if state.impact_player else []

        if missing or len(players) < req_length:
            err = f"❌ **Roster Validation Failed ({len(players)}/{req_length} Found)**\n\n"
            if players: err += f"✅ **Accepted:** {', '.join([p['name'] for p in players])}\n"
            if missing: err += f"❌ **Missing from DB:** {', '.join(missing)}\n\n"
            err += f"Please check spellings or add missing players to your CSV, then type your full list again."
            return await message.channel.send(err)
        if cap_err == "multiple":
            return await message.channel.send("❌ **Invalid — more than one captain marked.**\nPut `(C)` after exactly **one** player, then type your XI again.")
        if not _has_wk(players):
            return await message.channel.send("❌ **Invalid XI — no Wicket-Keeper.**\nEvery team must include at least one keeper (a `WK`-role player). Add a wicket-keeper, then type your full XI again.")

        # A captain marked with '(C)' must be one of the XI; otherwise we'll prompt for it.
        state.t2_captain = cap_name if (cap_name and cap_name in {p["name"] for p in players}) else None

        del active_setups[channel_id]
        xi_text = format_xi_display(players)
        if state.impact_player and state.t2_subs:
            xi_text += "\n\n**Impact Subs:**\n" + format_xi_display(state.t2_subs)
        cap_line = ""
        if state.t2_captain:
            cap_line = f"\n\n🧢 **Captain (from your list):** {state.t2_captain}"
            if cap_low:
                cap_line += "\n-# Noted `(c)` — the documented marker is uppercase `(C)`."
        await message.channel.send(f"📋 **{state.t2_name} XI** Verified:\n{xi_text}{cap_line}\n\nIs this correct?", view=Team2VerifyView(state, message.channel, players))

    await bot.process_commands(message)

# The Slash Command Initialization

@bot.tree.command(name="match", description="Start a new Cricket Match simulation.")
async def match_cmd(interaction: discord.Interaction, opponent: discord.Member = None):
    
    # Instantly defer to prevent 10062 timeouts while Cloud DB wakes up
    await interaction.response.defer()
    
    if is_channel_restricted(str(interaction.channel.id)):
        return await interaction.edit_original_response(content="❌ Matches are **disabled** in this channel. Please switch to a dedicated bot channel to play!")
        
    # Run DB check in background thread so bot heartbeat doesn't block
    allowed, reason = await asyncio.to_thread(check_potential_quota, str(interaction.user.id), str(interaction.guild.id) if interaction.guild else None, str(ADMIN_DISCORD_ID))
    if not allowed: return await interaction.edit_original_response(content=reason)

    if interaction.channel.id in active_games: 
        
        return await interaction.edit_original_response(content="❌ A match is already in progress in this channel. Use `/endmatch` to stop it.")
    if interaction.channel.id in active_setups: 
        
        return await interaction.edit_original_response(content="❌ A setup is already happening here. Use `/endmatch` to cancel it.")
    if opponent and opponent.bot: 
    
        return await interaction.edit_original_response(content="❌ Cannot challenge a bot user.")

    state = MatchSetupState(interaction.user, opponent, interaction.user.id, opponent.id if opponent else None)
    
    # FIX: Register the setup immediately so /endmatch works instantly!
    active_setups[interaction.channel.id] = ("format_selection", state)
    setup_states[interaction.channel.id] = state
    
    opp_str = opponent.mention if opponent else "🤖 AI"
    
    await interaction.edit_original_response(content=f"🏏 **Match Setup**\n**Host:** {interaction.user.mention}\n**Opponent:** {opp_str}\n\nStep 1: Select Format below:", view=FormatSelectView(state, interaction.channel))

# ---- Test match simulation ----

_TEST_SESSION_NAMES = {1: "Morning", 2: "Afternoon", 3: "Evening"}
_TEST_SESSION_NAMES_PINK = {1: "Afternoon", 2: "Twilight", 3: "Night"}   # day-night Test

def _test_session_name(match, session=None):
    s = match.session if session is None else session
    names = _TEST_SESSION_NAMES_PINK if getattr(match, "pink_ball", False) else _TEST_SESSION_NAMES
    return names.get(s, "Morning")

# Rain weather types aren't in the test engine - map them to closest equivalent


def render_test_embed(match: TestMatchObj) -> discord.Embed:
    """Live scoreboard - ODI/T20 style with per-team rows showing all innings."""
    innings = match.current_innings
    embed   = discord.Embed(color=0xFFFFFF)

    sess  = _test_session_name(match)
    if getattr(match, "pink_ball", False):
        sess = f"<:pink:1518481735266996255> {sess}"
    inns  = match.innings_list

    overs_done_today = (match.session - 1) * 30 + match.overs_in_session
    overs_left_today = max(0, 90 - overs_done_today)

    desc  = f"**<a:ball:1510370830163640320> LIVE SCOREBOARD**\n"
    desc += f"-# Day {match.day} · {sess} Session  ·  {match.pitch} / {match.weather}  ·  {overs_left_today} ov left today"
    if match.follow_on_enforced:
        desc += "  ·  ⚠️ Follow-on"
    desc += "\n\n"

    # Helper: format one innings score
    def _sc(inn):
        dec = "(d)" if getattr(inn, "declared", False) else ""
        if inn is innings and not inn.is_complete:
            return f"{inn.total_runs}/{inn.wickets}  ({inn.overs_str})"
        return f"{inn.total_runs}/{inn.wickets}{dec}"

    # One row per team - both innings on the same line separated by " & "
    for team in [match.team1, match.team2]:
        tnm         = team["name"]
        is_batting  = (innings.batting_team["name"] == tnm)
        team_inns   = [i for i in inns if i.batting_team["name"] == tnm]

        if not team_inns:
            score_str = "Yet to Bat"
        else:
            score_str = " & ".join(_sc(i) for i in team_inns)

        ball   = "<a:live:1510367738684641463> " if is_batting else ""
        prefix = "### "
        desc  += f"{prefix}{ball}**{tnm}**  {score_str}\n"

    # Lead / Trail totals
    t1_tot = sum(i.total_runs for i in inns if i.batting_team["name"] == match.team1["name"])
    t2_tot = sum(i.total_runs for i in inns if i.batting_team["name"] == match.team2["name"])
    diff   = abs(t1_tot - t2_tot)
    if diff == 0:
        lead_str = "Scores level"
    else:
        leading  = match.team1["name"] if t1_tot > t2_tot else match.team2["name"]
        lead_str = f"{leading[:12]} lead by {diff}"

    # Current batters
    desc += f"\n**`{'BATTER':<16}{'R':<5}{'B':<5}{'SR':<6}`**\n"
    for idx in range(min(innings.next_batter_idx, len(innings.batting_team["players"]))):
        p  = innings.batting_team["players"][idx]
        st = innings.batting_stats[p["name"]]
        if st.dismissal == "not out":
            mk = "*" if idx == innings.current_striker_idx else " "
            sr = round(st.runs_scored / st.balls_faced * 100, 1) if st.balls_faced else 0.0
            desc += f"`{p['name'][:14]:<14}{mk:<2}{st.runs_scored:<5}{st.balls_faced:<5}{sr:<6.1f}`\n"

    desc += f"\n`P'Ship: {innings.partnership_runs}  {lead_str}`\n"

    # Current bowler row + over timeline (matches T20/ODI layout)
    desc += f"\n**`{'BOWLER':<17}{'O':<8}{'R':<5}{'W'}`**\n"
    cb = innings.current_bowler or innings.prev_bowler
    if cb:
        cbs = innings.bowling_stats[cb["name"]]
        desc += f"`{cb['name'][:16]:<17}{cbs.overs_str:<8}{cbs.runs_conceded:<5}{cbs.wickets_taken}`\n"
    tl = getattr(innings, "over_log", [])[-6:]
    timeline_str = " ".join(tl) if tl else "Starting over..."
    desc += f"**Timeline**\n{timeline_str}\n"

    # Innings-4 chase equation
    if match.current_innings_idx == 3:
        bat_nm     = innings.batting_team["name"]
        t_bat_prev = sum(i.total_runs for i in inns[:-1] if i.batting_team["name"] == bat_nm)
        t_field    = sum(i.total_runs for i in inns if i.batting_team["name"] != bat_nm)
        still_need = max(0, t_field - t_bat_prev - innings.total_runs + 1)
        if still_need > 0:
            desc += f"-# Equation: Need **{still_need}** more runs to win"
        else:
            desc += f"-# 🏆 Target reached!"

    embed.description = desc[:4096]
    return embed


def render_test_final_embed(match: TestMatchObj) -> discord.Embed:
    """Final match embed - compact per-innings summary (image has the full detail)."""
    embed = discord.Embed(title="📋 Test Match Scorecard", color=discord.Color.gold())
    result = match.result or "Match Drawn"
    embed.description = f"**Result:** {result}\n"

    for inn in match.innings_list:
        # Top 3 batters by runs
        top_bat = sorted(
            [(p, inn.batting_stats[p["name"]]) for p in inn.batting_team["players"]
             if inn.batting_stats[p["name"]].balls_faced > 0],
            key=lambda x: x[1].runs_scored, reverse=True
        )[:3]
        # Top 2 bowlers by wickets
        top_bowl = sorted(
            [(p, inn.bowling_stats[p["name"]]) for p in inn.bowling_team["players"]
             if inn.bowling_stats[p["name"]].balls_bowled > 0],
            key=lambda x: (x[1].wickets_taken, -x[1].runs_conceded), reverse=True
        )[:2]

        lines = [f"**Total: {inn.total_runs}/{inn.wickets}** ({inn.overs_str} ov)\n```"]
        for p, st in top_bat:
            not_out = "*" if st.dismissal == "not out" else ""
            lines.append(f"{p['name'][:18]:<20} {st.runs_scored}{not_out} ({st.balls_faced}b)")
        lines.append("---")
        for p, st in top_bowl:
            lines.append(f"{p['name'][:18]:<20} {st.wickets_taken}/{st.runs_conceded} ({st.overs_str})")
        lines.append("```")

        embed.add_field(
            name=f"Innings {inn.innings_num} — {inn.batting_team['name']}",
            value="\n".join(lines),
            inline=True
        )

    embed.set_footer(text=f"Pitch: {match.pitch}  ·  Weather: {match.weather}")
    return embed


def _test_post_innings_logic(match: TestMatchObj):
    """After a session/innings completes: check result, handle follow-on, start next innings if needed.
    Returns the follow-on message string (empty if none)."""
    follow_on_msg = ""
    if match.follow_on_msg:
        follow_on_msg = match.follow_on_msg
        match.follow_on_msg = ""

    curr = match.current_innings
    if curr.wickets >= 10:
        curr.is_complete = True

    res = _test_check_result(match)
    if res:
        match.result = res

    if not match.result and match.day <= 5 and curr.is_complete:
        if len(match.innings_list) < 4:
            _test_start_next_innings(match)
        else:
            match.result = match.result or "Match Drawn"

    if match.day > 5 and not match.result:
        match.result = "Match Drawn (time)"

    return follow_on_msg


def _test_take_snapshot(match: TestMatchObj) -> dict:
    """Capture match state before a simulation step so we can compute deltas afterwards."""
    return {
        "day": match.day,
        "session": match.session,
        "innings_count": len(match.innings_list),
        "innings": [
            {
                "runs":     inn.total_runs,
                "wkts":     inn.wickets,
                "balls":    inn.total_balls,
                "fow_len":  len(inn.fow),
                "complete": inn.is_complete,
                "inn_num":  inn.innings_num,
                "bat_name": inn.batting_team["name"],
                "bowl_stats": {
                    p["name"]: {
                        "balls": inn.bowling_stats[p["name"]].balls_bowled,
                        "runs":  inn.bowling_stats[p["name"]].runs_conceded,
                        "wkts":  inn.bowling_stats[p["name"]].wickets_taken,
                    }
                    for p in inn.bowling_team["players"]
                },
            }
            for inn in match.innings_list
        ],
    }


def _test_session_commentary(
    runs_added: int, wkts_fell: int, balls_used: int, rr: float,
    new_fow: list, sess_bowlers: list, match: TestMatchObj, inn
) -> str:
    lines = []

    # Session character
    if balls_used == 0:
        return ""
    if rr >= 4.5:
        lines.append(random.choice([
            "The batting side were in full flow, finding the boundary at will.",
            "A dominant session with the bat — the bowlers had no answers.",
            "Runs flowed freely as the batting side took complete control.",
        ]))
    elif rr >= 3.5:
        lines.append(random.choice([
            "A productive session with bat meeting ball cleanly throughout.",
            "Good strokeplay from the batting side kept the scoreboard ticking.",
            "The batters built on a solid base, rotating strike and punishing the bad ball.",
        ]))
    elif rr >= 2.5:
        lines.append(random.choice([
            "A hard-fought session with neither side gaining a decisive edge.",
            "Both bat and ball had their moments in a closely contested session.",
            "Controlled cricket from both sides — the contest remained tight.",
        ]))
    elif rr >= 1.5:
        lines.append(random.choice([
            "The bowlers were on top, making every run hard to come by.",
            "A testing session for the batters — survival was as important as scoring.",
            "Discipline in the field restricted the batting side to a below-par rate.",
        ]))
    else:
        lines.append(random.choice([
            "A session of extreme bowling dominance — the batting side were under the pump all day.",
            "The bowlers were virtually unplayable, extracting movement and turn throughout.",
            "A miserable session for the bat — the pitch and conditions did the bowlers every favour.",
        ]))

    # Wicket narrative
    if wkts_fell == 0:
        lines.append(random.choice([
            "No wickets fell — the batting side will be pleased with their discipline.",
            "The partnership(s) held firm, denying the bowlers any reward.",
            "A clean session for the batting side — not a single wicket lost.",
        ]))
    elif wkts_fell >= 6:
        lines.append(random.choice([
            f"A catastrophic collapse saw {wkts_fell} wickets fall in quick succession — the dressing room will be shaking.",
            f"The bowling side ran through the order, taking {wkts_fell} wickets to wreck the innings.",
            f"{wkts_fell} wickets tumbled in a remarkable session — the tail is now well and truly exposed.",
        ]))
    elif wkts_fell >= 4:
        lines.append(random.choice([
            f"{wkts_fell} wickets in the session tipped the balance firmly toward the bowling side.",
            f"A damaging session — {wkts_fell} wickets have put the batting team on the back foot.",
            f"Four wickets or more in a session is always significant — the bowling side will be confident.",
        ]))
    elif wkts_fell >= 2:
        lines.append(random.choice([
            f"{wkts_fell} wickets fell at key moments, keeping the bowling team in the game.",
            f"A couple of wickets in the session maintained the pressure for the fielding side.",
            f"The bowling side were rewarded for their patience with {wkts_fell} timely wickets.",
        ]))
    else:
        lines.append(random.choice([
            "One wicket in the session — a minor success that keeps the bowling side interested.",
            "A solitary wicket, but it may prove crucial given the match situation.",
            "Just the one wicket — the batting side largely had the better of the exchange.",
        ]))

    # Standout bowler
    if sess_bowlers:
        _, _, best_name, best_ovs, best_runs, best_wkts = sess_bowlers[0]
        if best_wkts >= 4:
            lines.append(f"**{best_name}** was simply outstanding — **{best_wkts}/{best_runs}** in {best_ovs} overs.")
        elif best_wkts >= 2:
            lines.append(f"**{best_name}** led the attack with {best_wkts} wickets for {best_runs} runs.")
        elif best_runs <= 12 and int(best_ovs.split(".")[0]) >= 5:
            lines.append(f"**{best_name}** was the standout in economy terms — miserly figures of {best_ovs}-{best_runs}-{best_wkts}.")

    # Pitch / weather flavour
    pitch = match.pitch
    weather = match.weather
    if weather in ("Heavy Rain", "Thunderstorm") and balls_used > 0:
        lines.append("Conditions were extremely tough — the wet outfield and overcast skies made it a bowler's paradise.")
    elif weather in ("Drizzle", "Light Rain"):
        lines.append("The drizzle throughout the session made conditions slippery and uncomfortable for the batters.")
    elif pitch in ("Turning", "Cracked", "Dusty") and match.day >= 3:
        lines.append(f"The {pitch.lower()} surface is taking more and more spin as the match wears on.")
    elif pitch in ("Green", "Damp") and match.current_innings_idx == 0:
        lines.append("The lush surface continued to offer the seamers generous movement throughout.")

    return "\n".join(f"> *{l}*" for l in lines)


def _render_test_session_embed(match: TestMatchObj, snap: dict) -> discord.Embed:
    """Session summary embed - replaces the old ASCII code-block output."""
    embed    = discord.Embed(color=0xFFFFFF)
    sess_nm  = _test_session_name(match, snap["session"])
    pink_tag = "<:pink:1518481735266996255> " if getattr(match, "pink_ball", False) else ""
    embed.title = f"🏏 {pink_tag}Day {snap['day']} · {sess_nm} Session"

    desc       = ""
    curr_inns  = match.innings_list
    snap_inns  = snap["innings"]

    for i, sdata in enumerate(snap_inns):
        if i >= len(curr_inns):
            break
        inn        = curr_inns[i]
        runs_added = inn.total_runs  - sdata["runs"]
        wkts_fell  = inn.wickets     - sdata["wkts"]
        balls_used = inn.total_balls - sdata["balls"]
        if balls_used == 0:
            continue

        ovs = f"{balls_used // 6}.{balls_used % 6}"
        rr  = round(runs_added / max(1, balls_used) * 6, 2)
        wkt_str = f"{wkts_fell} wkt{'s' if wkts_fell != 1 else ''}"

        completed = inn.is_complete and not sdata["complete"]
        if completed:
            status = "  ✅ *Declared*" if getattr(inn, "declared", False) else "  ✅ *All Out*"
        else:
            status = ""
        desc += f"### **{inn.batting_team['name']}**  ·  Innings {inn.innings_num}{status}\n"
        desc += f"Score: **{inn.total_runs}/{inn.wickets}** ({inn.overs_str} ov)"
        desc += f"  ·  +{runs_added} runs  ·  {wkt_str}  ·  {ovs} ov  ·  {rr} RPO\n"

        new_fow = inn.fow[sdata["fow_len"]:]
        if new_fow:
            desc += "```\n"
            for w in new_fow[:6]:
                desc += f"  {w}\n"
            desc += "```\n"

        # Session bowling figures
        snap_bowl = sdata.get("bowl_stats", {})
        sess_bowlers = []
        for p in inn.bowling_team["players"]:
            pn  = p["name"]
            old = snap_bowl.get(pn, {"balls": 0, "runs": 0, "wkts": 0})
            bs  = inn.bowling_stats[pn]
            db  = bs.balls_bowled  - old["balls"]
            dr  = bs.runs_conceded - old["runs"]
            dw  = bs.wickets_taken - old["wkts"]
            if db > 0:
                ovs = f"{db // 6}.{db % 6}"
                sess_bowlers.append((dw, -dr, pn, ovs, dr, dw))
        sess_bowlers.sort(reverse=True)
        if sess_bowlers:
            desc += "**Bowling:**\n```\n"
            desc += f"{'Name':<18} {'O':>5}  {'R':>4}  W\n"
            desc += "─" * 33 + "\n"
            for _, _, name, ovs, runs, wkts in sess_bowlers:
                desc += f"{name[:17]:<18} {ovs:>5}  {runs:>4}  {wkts}\n"
            desc += "```\n"

        commentary = _test_session_commentary(
            runs_added, wkts_fell, balls_used, rr,
            new_fow, sess_bowlers, match, inn
        )
        if commentary:
            desc += commentary + "\n"

        desc += "\n"

    # New innings started mid-session (carry-over)
    for i in range(snap["innings_count"], len(curr_inns)):
        inn = curr_inns[i]
        desc += f"### ✦ **{inn.batting_team['name']}**  ·  Innings {inn.innings_num} *(new innings)*\n"
        desc += f"Score: **{inn.total_runs}/{inn.wickets}** ({inn.overs_str} ov)\n\n"

    if match.follow_on_enforced:
        desc += "⚠️ *Follow-on enforced*\n\n"

    # Lead/trail line
    inns = match.innings_list
    t1   = sum(i.total_runs for i in inns if i.batting_team["name"] == match.team1["name"])
    t2   = sum(i.total_runs for i in inns if i.batting_team["name"] == match.team2["name"])
    if t1 != t2:
        leading = match.team1["name"] if t1 > t2 else match.team2["name"]
        desc += f"-# {leading} lead by {abs(t1 - t2)} runs"
    else:
        desc += "-# Scores are level"

    embed.description = desc.strip()
    embed.set_footer(text=f"Pitch: {match.pitch}  ·  Weather: {match.weather}")
    return embed


def _render_test_innings_embed(match: TestMatchObj, inn_idx: int) -> discord.Embed:
    """Full innings scorecard embed - all batters with dismissals and all bowlers."""
    inn   = match.innings_list[inn_idx]
    rr    = round(inn.total_runs / max(1, inn.total_balls) * 6, 2)
    dec   = "(dec)" if inn.declared else ""
    embed = discord.Embed(
        title = f"📋 Innings {inn.innings_num} {dec}— {inn.batting_team['name']}",
        color = discord.Color.gold(),
    )

    potm  = _test_player_of_match(match)
    potm_line = f"⭐ **Player of the Match:** {potm}\n\n" if potm else ""
    total_line = f"**{inn.total_runs}/{inn.wickets}{dec}**  ({inn.overs_str} ov)  ·  RR: {rr}\n\n"

    # All batters
    bat_lines = []
    for p in inn.batting_team["players"]:
        st = inn.batting_stats[p["name"]]
        if st.balls_faced == 0 and st.dismissal == "not out":
            continue
        no  = "*" if st.dismissal == "not out" else ""
        dis = st.dismissal if st.dismissal == "not out" else st.dismissal[:22]
        bat_lines.append(f"{p['name'][:14]:<14}  {dis:<22}  {str(st.runs_scored)+no:>4}  {st.balls_faced:>4}")
    if inn.extras:
        bat_lines.append(f"{'Extras':<14}  {'':22}  {inn.extras:>4}")

    bat_block  = "**Batting**\n```\n"
    bat_block += f"{'Name':<14}  {'Dismissal':<22}  {'R':>4}  {'B':>4}\n"
    bat_block += "─" * 50 + "\n"
    bat_block += "\n".join(bat_lines) + "\n"
    bat_block += "```\n"

    # All bowlers
    bowl_lines = []
    for p in inn.bowling_team["players"]:
        st = inn.bowling_stats[p["name"]]
        if st.balls_bowled == 0:
            continue
        eco = round(st.runs_conceded / st.balls_bowled * 6, 2)
        bowl_lines.append(
            f"{p['name'][:16]:<16}  {st.overs_str:>5}  {st.runs_conceded:>4}  {st.wickets_taken:>2}  {eco:>5.2f}"
        )

    bowl_block  = "**Bowling**\n```\n"
    bowl_block += f"{'Name':<16}  {'O':>5}  {'R':>4}  {'W':>2}  {'ECO':>7}\n"
    bowl_block += "─" * 40 + "\n"
    bowl_block += "\n".join(bowl_lines) + "\n"
    bowl_block += "```"

    embed.description = potm_line + total_line + bat_block + bowl_block
    return embed


def _test_potm_name(match):
    return (_test_player_of_match(match) or "").split(" (")[0]


def generate_test_summary_image(match: TestMatchObj) -> io.BytesIO:
    """Compact per-innings Test summary (rendered by test_image - previewable locally)."""
    return _ti_summary(match, _format_match_no_label("test"), _test_potm_name(match))


def generate_test_scorecard_image(match: TestMatchObj) -> io.BytesIO:
    """Full broadcast Test scorecard (rendered by test_image - previewable locally)."""
    return _ti_scorecard(match, _format_match_no_label("test"), _test_potm_name(match))


class TestScorecardView(discord.ui.View):
    """Navigate through per-innings scorecards after a Test match ends."""

    def __init__(self, match: TestMatchObj):
        super().__init__(timeout=300)
        self.match = match
        self.idx   = 0
        self._update_buttons()

    def _update_buttons(self):
        total = len(self.match.innings_list)
        self.prev_btn.disabled = (self.idx == 0)
        self.next_btn.disabled = (self.idx >= total - 1)
        self.page_btn.label    = f"Innings {self.idx + 1} / {total}"

    async def _navigate(self, interaction: discord.Interaction):
        try:
            embed = _render_test_innings_embed(self.match, self.idx)
            self._update_buttons()
            await interaction.response.edit_message(embed=embed, view=self)
        except Exception as e:
            try:
                await interaction.response.send_message(f"❌ Could not load scorecard: {e}", ephemeral=True)
            except Exception:
                pass

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True

    @discord.ui.button(label="◀ Prev", style=discord.ButtonStyle.secondary, row=0)
    async def prev_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.idx -= 1
        await self._navigate(interaction)

    @discord.ui.button(label="Innings 1 / ?", style=discord.ButtonStyle.secondary, disabled=True, row=0)
    async def page_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()

    @discord.ui.button(label="Next ▶", style=discord.ButtonStyle.secondary, row=0)
    async def next_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.idx += 1
        await self._navigate(interaction)


class TestImageToggleView(discord.ui.View):
    """Switch the result graphic between the compact Summary and the Full Scorecard."""
    def __init__(self, match: TestMatchObj):
        super().__init__(timeout=900)
        self.match = match

    async def _swap(self, interaction, gen, fname):
        try:
            file = discord.File(fp=gen(self.match), filename=fname)
            await interaction.response.edit_message(attachments=[file], view=self)
        except Exception as e:
            print(f"Test image toggle failed: {e}")
            try:
                await interaction.response.send_message("⚠️ Couldn't switch the view.", ephemeral=True)
            except Exception:
                pass

    @discord.ui.button(label="Summary", style=discord.ButtonStyle.primary, emoji="📊")
    async def summary(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._swap(interaction, generate_test_summary_image, "test_summary.png")

    @discord.ui.button(label="Full Scorecard", style=discord.ButtonStyle.secondary, emoji="📋")
    async def full(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._swap(interaction, generate_test_scorecard_image, "test_full.png")


async def _test_finish_match(match: TestMatchObj, channel_id: int, channel):
    """Post a Test result, advancing the counter unless this is a player test."""
    if not getattr(match, "is_player_test", False):
        _increment_match_count("test")
        try:
            gstats.record_test_match(match)
        except Exception as _gs_err:
            print(f"Global stats record failed (test): {_gs_err}")
    result_text = match.result or "Match Drawn"
    try:
        file = discord.File(fp=generate_test_summary_image(match), filename="test_summary.png")
        await channel.send(
            f"🏆 **Test Match Complete · {result_text}**\n"
            f"-# 📊 Summary shown — tap **Full Scorecard** for the detailed card.",
            file=file, view=TestImageToggleView(match)
        )
    except Exception as _e:
        print(f"Test summary image failed: {_e}")
        await channel.send(f"🏆 **Test Match Result:** {result_text}")

    # Interactive per-innings scorecard navigator
    view  = TestScorecardView(match)
    embed = _render_test_innings_embed(match, 0)
    await channel.send("📋 **Full Scorecard** — use the arrows to browse innings:", embed=embed, view=view)

    if channel.guild:
        log_channel_id = get_match_log_channel(str(channel.guild.id))
        if log_channel_id:
            try:
                log_channel = bot.get_channel(int(log_channel_id))
                if log_channel:
                    log_file = discord.File(fp=generate_test_summary_image(match), filename="test_summary.png")
                    t1 = match.team1["name"]
                    t2 = match.team2["name"]
                    await log_channel.send(
                        f"📋 **Match Log** · {t1} vs {t2} · <#{channel.id}>",
                        file=log_file
                    )
            except Exception as _log_err:
                print(f"Test match log send failed: {_log_err}")

    active_test_matches.pop(channel_id, None)


def _split_text_chunks(text: str, limit: int = 4000) -> list:
    """Break a string into chunks of at most `limit` chars, splitting on newlines."""
    chunks = []
    while len(text) > limit:
        idx = text.rfind("\n", 0, limit)
        if idx == -1:
            idx = limit
        chunks.append(text[:idx])
        text = text[idx:].lstrip("\n")
    if text:
        chunks.append(text)
    return chunks


class TestMultipleOversModal(discord.ui.Modal, title="Simulate Multiple Overs"):
    overs_input = discord.ui.TextInput(
        label="Number of overs to simulate (1–30)",
        placeholder="e.g. 10",
        min_length=1,
        max_length=2,
        required=True,
    )

    def __init__(self, match: TestMatchObj, channel_id: int, channel):
        super().__init__()
        self.match      = match
        self.channel_id = channel_id
        self.channel    = channel

    async def on_submit(self, interaction: discord.Interaction):
        try:
            n = int(self.overs_input.value.strip())
            if not (1 <= n <= 30):
                raise ValueError
        except ValueError:
            return await interaction.response.send_message(
                "❌ Enter a whole number between 1 and 30.", ephemeral=True
            )

        await interaction.response.defer()
        match      = self.match
        inn_idx    = match.current_innings_idx

        verbose_text, innings_ended = await asyncio.to_thread(_test_sim_n_overs_verbose, match, n)

        if match.overs_in_session >= 30:
            match.advance_session()

        _test_post_innings_logic(match)

        if verbose_text:
            for chunk in _split_text_chunks(verbose_text):
                emb = discord.Embed(description=chunk, color=0x3498db)
                await self.channel.send(embed=emb)

        if match.result or match.day > 5:
            return await _test_finish_match(match, self.channel_id, self.channel)

        if innings_ended:
            await self.channel.send(embed=_render_test_innings_embed(match, inn_idx))

        await self.channel.send(embed=render_test_embed(match), view=TestSimView(match, self.channel_id))


# Interactive over-by-over mode
#
# Flow per over:
# 1. TestInteractiveBowlerSelectView - pick who bowls
# 2. TestInteractiveDeliveryView - pick delivery type (each ball)
# 3. TestInteractiveShotView - pick shot (each ball) -> execute -> show result
#   After over: loop back to step 1.

async def _test_ia_start_over(channel, match: TestMatchObj, channel_id: int):
    """Show bowler-select for the next over, or handle innings/match end."""
    innings = match.current_innings
    if innings.is_complete:
        _test_post_innings_logic(match)
        if match.result or match.day > 5:
            return await _test_finish_match(match, channel_id, channel)
        return await _test_ia_start_over(channel, match, channel_id)

    # AI auto-selects its own bowler
    if _test_ai_bowling(match):
        prev_nm = innings.prev_bowler["name"] if innings.prev_bowler else None
        candidates = [p for p in innings.bowling_team["players"]
                      if ("Bowler" in p["role"] or "All-Rounder" in p["role"])
                      and p["name"] != prev_nm]
        if not candidates:
            candidates = [p for p in innings.bowling_team["players"]
                          if "Bowler" in p["role"] or "All-Rounder" in p["role"]]
        if candidates:
            _test_prepare_over(match, random.choice(candidates)["name"])
        innings.over_log = []
        return await _test_ia_show_delivery(channel, match, channel_id)

    ov_num   = innings.total_balls // 6 + 1
    prev_nm  = innings.prev_bowler["name"] if innings.prev_bowler else None
    bowlers  = [p for p in innings.bowling_team["players"]
                if ("Bowler" in p["role"] or "All-Rounder" in p["role"])
                and p["name"] != prev_nm]
    if not bowlers:
        bowlers = [p for p in innings.bowling_team["players"]
                   if "Bowler" in p["role"] or "All-Rounder" in p["role"]]

    options = []
    for p in bowlers[:25]:
        st  = innings.bowling_stats[p["name"]]
        ovs = f"{st.balls_bowled // 6}.{st.balls_bowled % 6}"
        options.append(discord.SelectOption(
            label=p["name"],
            value=p["name"],
            description=f"{ovs} ov · {st.runs_conceded}r · {st.wickets_taken}w",
        ))

    striker = innings.batting_team["players"][innings.current_striker_idx]
    sess_name = "Morning" if match.session == 1 else ("Afternoon" if match.session == 2 else "Evening")
    embed = discord.Embed(
        title=f"🎳 Over {ov_num} — Select Bowler",
        description=(
            f"**{innings.bowling_team['name']}** bowling to **{innings.batting_team['name']}**\n"
            f"Striker: **{striker['name']}**"
        ),
        color=0x2c3e50,
    )
    embed.add_field(
        name="Score",
        value=f"**{innings.total_runs}/{innings.wickets}** ({innings.overs_str} ov)  "
              f"Day {match.day} · {sess_name}",
        inline=False,
    )
    _owner = _test_bowling_owner(match)
    _ping = f"<@{_owner}> 🎳 your over — pick your bowler:" if _owner else None
    await channel.send(content=_ping, embed=embed, view=TestInteractiveBowlerSelectView(match, channel_id, options))


async def _test_ia_show_delivery(channel, match: TestMatchObj, channel_id: int):
    """Show delivery-type buttons for the current ball (AI auto-picks if bowling)."""
    innings = match.current_innings
    bowler  = innings.current_bowler
    striker = innings.batting_team["players"][innings.current_striker_idx]

    # AI auto-selects its delivery
    if _test_ai_bowling(match):
        role = bowler.get("role", "")
        if "Spin" in role:
            if "Off" in role:
                opts = ["Off spin", "Carrom", "Arm ball", "Doosra", "Top spin", "Mystery"]
            else:
                opts = ["Leg spin", "Googly", "Flipper", "Drifter", "Slider", "Mystery"]
            if getattr(innings, "mystery_bowled_this_over", False):
                opts = [o for o in opts if o != "Mystery"]
            delivery = random.choice(opts)
            if delivery == "Mystery":
                innings.mystery_bowled_this_over = True
        else:
            if random.random() < 0.08:
                delivery = random.choice(["Off Cutter", "Leg Cutter", "Knuckle"])
            else:
                var    = random.choice(["Inswing", "Outswing", "Seam", "Fast", "Slow"])
                length = random.choice(["Bouncer", "Full", "Good length", "Yorker", "Short"])
                delivery = f"{var} {length}"
        match.current_delivery_selection = delivery
        return await _test_ia_show_shot(channel, match, channel_id, delivery)

    ball_in_ov = innings.total_balls % 6 + 1
    ov_num     = innings.total_balls // 6 + 1
    embed = discord.Embed(
        title=f"🏏 Over {ov_num}, Ball {ball_in_ov}",
        description=(
            f"**{bowler['name']}** bowling to **{striker['name']}**\n"
            f"Score: **{innings.total_runs}/{innings.wickets}** ({innings.overs_str} ov)"
        ),
        color=0x1a252f,
    )
    is_spin = "Spin" in bowler["role"]
    _owner = _test_bowling_owner(match)
    _ping = f"<@{_owner}> 🔮 (**{bowler['name']}**) — choose your delivery:" if _owner else None
    await channel.send(content=_ping, embed=embed, view=TestInteractiveDeliveryView(match, channel_id, is_spin, bowler))


async def _test_ia_show_shot(channel, match: TestMatchObj, channel_id: int, delivery: str):
    """Show shot-selection buttons, or auto-execute if AI is batting."""
    innings = match.current_innings
    striker = innings.batting_team["players"][innings.current_striker_idx]

    # AI auto-picks shot and executes
    if _test_ai_batting(match):
        _ai_shots = ["Drive", "Cut", "Pull", "Flick", "Loft", "Sweep", "Scoop", "Block", "Leave"]
        match.current_shot_selection = random.choice(_ai_shots)
        inn_idx = match.current_innings_idx
        desc, over_ended, innings_ended, session_ended = await asyncio.to_thread(_test_sim_one_ball, match)
        await _test_ia_ball_result(channel, match, channel_id, desc, over_ended, innings_ended, session_ended, inn_idx)
        return

    embed = discord.Embed(
        title=f"🏏 {striker['name']} faces {delivery}",
        description=f"Score: **{innings.total_runs}/{innings.wickets}** ({innings.overs_str} ov)",
        color=0x27ae60,
    )
    _owner = _test_batting_owner(match)
    _ping = f"<@{_owner}> ⚔️ (**{striker['name']}**) — a **{delivery}** is coming, pick your shot:" if _owner else None
    await channel.send(content=_ping, embed=embed, view=TestInteractiveShotView(match, channel_id))


async def _test_ia_ball_result(channel, match: TestMatchObj, channel_id: int,
                                desc: str, over_ended: bool, innings_ended: bool,
                                session_ended: bool, inn_idx: int):
    """Post ball result and advance to next delivery/over/innings as needed."""
    innings = match.innings_list[inn_idx]

    if "WICKET" in desc:
        color = 0xe74c3c
    elif "FOUR" in desc:
        color = 0xf39c12
    elif "SIX" in desc:
        color = 0x9b59b6
    elif "Dot" in desc:
        color = 0x7f8c8d
    else:
        color = 0x2ecc71

    embed = discord.Embed(description=desc, color=color)
    embed.add_field(
        name="Score",
        value=f"**{innings.total_runs}/{innings.wickets}** ({innings.overs_str} ov)",
        inline=True,
    )
    if not innings_ended:
        striker = innings.batting_team["players"][innings.current_striker_idx]
        embed.add_field(name="On Strike", value=striker["name"], inline=True)

    footer_parts = []
    if over_ended:
        footer_parts.append(f"Over complete")
    if session_ended:
        footer_parts.append("Session over!")
    if footer_parts:
        embed.set_footer(text=" · ".join(footer_parts))

    if session_ended:
        time_up = match.advance_session()
        if time_up:
            match.result = match.result or "Match Drawn"
            embed.set_footer(text="Day 5 complete — Match Drawn")

    _test_post_innings_logic(match)

    await channel.send(embed=embed)

    if match.result or match.day > 5:
        return await _test_finish_match(match, channel_id, channel)

    if innings_ended:
        # Show innings card then start fresh over
        await channel.send(embed=_render_test_innings_embed(match, inn_idx))
        return await _test_ia_start_over(channel, match, channel_id)

    if over_ended:
        # Show live scoreboard then pick next bowler
        await channel.send(embed=render_test_embed(match))
        return await _test_ia_start_over(channel, match, channel_id)

    # Same over continues - show next delivery prompt
    await _test_ia_show_delivery(channel, match, channel_id)


def _test_bowling_owner(match: TestMatchObj):
    """User ID that owns the team CURRENTLY BOWLING (None = AI). Keyed by team
    identity, so it's correct no matter who won the toss / bats first."""
    bt = match.current_innings.bowling_team
    if bt is getattr(match, "host_team", None):
        return getattr(match, "host_id", None)
    if bt is getattr(match, "p2_team", None):
        return getattr(match, "p2_id", None)
    return None

def _test_batting_owner(match: TestMatchObj):
    """User ID that owns the team CURRENTLY BATTING (None = AI)."""
    bt = match.current_innings.batting_team
    if bt is getattr(match, "host_team", None):
        return getattr(match, "host_id", None)
    if bt is getattr(match, "p2_team", None):
        return getattr(match, "p2_id", None)
    return None

def _test_ai_bowling(match: TestMatchObj) -> bool:
    """True when the AI (no p2) controls the team currently bowling."""
    return getattr(match, "p2_id", None) is None and \
        match.current_innings.bowling_team is getattr(match, "p2_team", match.team2)

def _test_ai_batting(match: TestMatchObj) -> bool:
    """True when the AI (no p2) controls the team currently batting."""
    return getattr(match, "p2_id", None) is None and \
        match.current_innings.batting_team is getattr(match, "p2_team", match.team2)


class TestInteractiveBowlerSelectView(discord.ui.View):
    def __init__(self, match: TestMatchObj, channel_id: int, options: list):
        super().__init__(timeout=300)
        self.match      = match
        self.channel_id = channel_id

        sel = discord.ui.Select(placeholder="Pick bowler...", options=options)
        sel.callback = self._on_select
        self.add_item(sel)

        exit_btn = discord.ui.Button(label="⏩ Exit to Menu", style=discord.ButtonStyle.secondary, row=1)
        exit_btn.callback = self._exit
        self.add_item(exit_btn)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        owner = _test_bowling_owner(self.match)
        if owner and interaction.user.id != owner:
            await interaction.response.send_message("❌ Only the bowling captain can select the bowler.", ephemeral=True)
            return False
        return True

    async def _on_select(self, interaction: discord.Interaction):
        await interaction.response.defer()
        await interaction.message.edit(view=None)
        bowler_name = interaction.data["values"][0]
        _test_prepare_over(self.match, bowler_name)
        self.match.current_innings.over_log = []   # fresh over
        await _test_ia_show_delivery(interaction.channel, self.match, self.channel_id)

    async def _exit(self, interaction: discord.Interaction):
        await interaction.response.defer()
        await interaction.message.edit(view=None)
        await interaction.channel.send(
            embed=render_test_embed(self.match),
            view=TestSimView(self.match, self.channel_id)
        )


class TestInteractiveDeliveryView(discord.ui.View):
    _PACE_VARIATIONS = ["Inswing", "Outswing", "Seam", "Fast", "Slow"]
    _PACE_LENGTHS    = ["Bouncer", "Full", "Good length", "Yorker", "Short"]
    _CUTTERS         = ["Off Cutter", "Leg Cutter", "Knuckle"]
    _OFF_SPIN = [
        ("Off spin",  discord.ButtonStyle.primary,   0),
        ("Arm ball",  discord.ButtonStyle.primary,   0),
        ("Doosra",    discord.ButtonStyle.danger,    0),
        ("Carrom",    discord.ButtonStyle.secondary, 0),
        ("Top spin",  discord.ButtonStyle.secondary, 1),
        ("Mystery",   discord.ButtonStyle.danger,    1),
    ]
    _LEG_SPIN = [
        ("Leg spin",  discord.ButtonStyle.primary,   0),
        ("Googly",    discord.ButtonStyle.danger,    0),
        ("Flipper",   discord.ButtonStyle.primary,   0),
        ("Slider",    discord.ButtonStyle.secondary, 0),
        ("Drifter",   discord.ButtonStyle.secondary, 1),
        ("Mystery",   discord.ButtonStyle.danger,    1),
    ]

    def __init__(self, match: TestMatchObj, channel_id: int, is_spin: bool, bowler: dict):
        super().__init__(timeout=120)
        self.match      = match
        self.channel_id = channel_id
        self._temp_var  = None
        self._is_spin   = is_spin

        mystery_used = getattr(match.current_innings, "mystery_bowled_this_over", False)

        if is_spin:
            opts = self._OFF_SPIN if "Off" in bowler["role"] else self._LEG_SPIN
            for label, style, row in opts:
                disabled = (label == "Mystery" and mystery_used)
                btn = discord.ui.Button(label=label, style=style, row=row, disabled=disabled)
                btn.callback = self._make_direct_cb(label)
                self.add_item(btn)
        else:
            # Row 0: Variation (same as T20/ODI PaceBowlingView)
            for var in self._PACE_VARIATIONS:
                btn = discord.ui.Button(label=var, style=discord.ButtonStyle.primary, row=0)
                btn.callback = self._make_var_cb(var)
                self.add_item(btn)
            # Row 1: Length (disabled until variation selected)
            for length in self._PACE_LENGTHS:
                btn = discord.ui.Button(label=length, style=discord.ButtonStyle.danger, row=1, disabled=True)
                btn.callback = self._make_len_cb(length)
                self.add_item(btn)
            # Row 2: Cutters (direct, no variation needed)
            for cutter in self._CUTTERS:
                btn = discord.ui.Button(label=cutter, style=discord.ButtonStyle.secondary, row=2)
                btn.callback = self._make_direct_cb(cutter)
                self.add_item(btn)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        owner = _test_bowling_owner(self.match)
        if owner and interaction.user.id != owner:
            await interaction.response.send_message("❌ Only the bowling captain selects the delivery.", ephemeral=True)
            return False
        return True

    def _make_var_cb(self, var: str):
        async def cb(interaction: discord.Interaction):
            self._temp_var = var
            for item in self.children:
                if item.row == 0:
                    item.disabled = True
                elif item.row == 1:
                    item.disabled = False
            await interaction.response.edit_message(view=self)
        return cb

    def _make_len_cb(self, length: str):
        async def cb(interaction: discord.Interaction):
            await interaction.response.defer()
            await interaction.message.edit(view=None)
            delivery = f"{self._temp_var} {length}"
            self.match.current_delivery_selection = delivery
            await _test_ia_show_shot(interaction.channel, self.match, self.channel_id, delivery)
        return cb

    def _make_direct_cb(self, delivery: str):
        async def cb(interaction: discord.Interaction):
            await interaction.response.defer()
            await interaction.message.edit(view=None)
            if delivery == "Mystery":
                self.match.current_innings.mystery_bowled_this_over = True
            self.match.current_delivery_selection = delivery
            await _test_ia_show_shot(interaction.channel, self.match, self.channel_id, delivery)
        return cb


class TestInteractiveShotView(discord.ui.View):
    _SHOTS = [
        ("Drive",  discord.ButtonStyle.primary,   0),
        ("Cut",    discord.ButtonStyle.primary,   0),
        ("Pull",   discord.ButtonStyle.success,   0),
        ("Flick",  discord.ButtonStyle.success,   0),
        ("Loft",   discord.ButtonStyle.danger,    1),
        ("Sweep",  discord.ButtonStyle.danger,    1),
        ("Scoop",  discord.ButtonStyle.danger,    1),
        ("Block",  discord.ButtonStyle.secondary, 1),
        ("Leave",  discord.ButtonStyle.secondary, 1),
    ]

    def __init__(self, match: TestMatchObj, channel_id: int):
        super().__init__(timeout=120)
        self.match      = match
        self.channel_id = channel_id

        for label, style, row in self._SHOTS:
            btn = discord.ui.Button(label=label, style=style, row=row)
            btn.callback = self._make_cb(label)
            self.add_item(btn)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        owner = _test_batting_owner(self.match)
        if owner and interaction.user.id != owner:
            await interaction.response.send_message("❌ Only the batting captain selects the shot.", ephemeral=True)
            return False
        return True

    def _make_cb(self, shot: str):
        async def cb(interaction: discord.Interaction):
            await interaction.response.defer()
            await interaction.message.edit(view=None)
            self.match.current_shot_selection = shot
            inn_idx = self.match.current_innings_idx
            desc, over_ended, innings_ended, session_ended = await asyncio.to_thread(
                _test_sim_one_ball, self.match
            )
            await _test_ia_ball_result(
                interaction.channel, self.match, self.channel_id,
                desc, over_ended, innings_ended, session_ended, inn_idx
            )
        return cb


class TestSimView(discord.ui.View):
    def __init__(self, match: TestMatchObj, channel_id: int):
        super().__init__(timeout=600)
        self.match      = match
        self.channel_id = channel_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        host = getattr(self.match, "host_id", None)
        p2   = getattr(self.match, "p2_id",   None)
        uid  = interaction.user.id
        if host and uid != host and uid != p2:
            await interaction.response.send_message("❌ Only match participants can interact with this.", ephemeral=True)
            return False
        return True

    async def _check_host(self, interaction: discord.Interaction) -> bool:
        """Simulation buttons are host-only; batting declare is handled separately."""
        if interaction.user.id != getattr(self.match, "host_id", interaction.user.id):
            await interaction.response.send_message("❌ Only the match host can control simulation.", ephemeral=True)
            return False
        return True

    # Row 0: Interactive / quick simulation

    @discord.ui.button(label="🏏 Play Interactive", style=discord.ButtonStyle.success, row=0)
    async def play_interactive(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_host(interaction): return
        await interaction.response.defer()
        await interaction.message.edit(view=None)
        await _test_ia_start_over(interaction.channel, self.match, self.channel_id)

    @discord.ui.button(label="1 Over", style=discord.ButtonStyle.primary, row=0)
    async def sim_one_over(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_host(interaction): return
        await interaction.response.defer()
        await interaction.message.edit(view=None)
        match   = self.match
        inn_idx = match.current_innings_idx

        verbose_text, _ = await asyncio.to_thread(_test_sim_one_over_verbose, match)

        if match.overs_in_session >= 30:
            match.advance_session()

        sim_inn       = match.innings_list[inn_idx]
        innings_ended = sim_inn.is_complete

        _test_post_innings_logic(match)

        if verbose_text:
            await interaction.channel.send(embed=discord.Embed(description=verbose_text, color=0x2c3e50))

        if match.result or match.day > 5:
            if innings_ended:
                await interaction.channel.send(embed=_render_test_innings_embed(match, inn_idx))
            return await _test_finish_match(match, self.channel_id, interaction.channel)

        if innings_ended:
            await interaction.channel.send(embed=_render_test_innings_embed(match, inn_idx))
        await interaction.channel.send(embed=render_test_embed(match), view=TestSimView(match, self.channel_id))

    @discord.ui.button(label="🎲 1 Ball", style=discord.ButtonStyle.secondary, row=0)
    async def sim_one_ball(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_host(interaction): return
        await interaction.response.defer()
        await interaction.message.edit(view=None)
        match   = self.match
        inn_idx = match.current_innings_idx

        desc, over_ended, innings_ended, session_ended = await asyncio.to_thread(_test_sim_one_ball, match)

        if "WICKET" in desc:    color = 0xe74c3c
        elif "FOUR" in desc:    color = 0xf39c12
        elif "SIX" in desc:     color = 0x9b59b6
        elif "Dot" in desc:     color = 0x7f8c8d
        else:                   color = 0x2ecc71

        inn    = match.innings_list[inn_idx]
        embed  = discord.Embed(description=desc, color=color)
        embed.add_field(name="Score", value=f"**{inn.total_runs}/{inn.wickets}** ({inn.overs_str} ov)", inline=True)
        if not innings_ended:
            striker = inn.batting_team["players"][inn.current_striker_idx]
            embed.add_field(name="On Strike", value=striker["name"], inline=True)
        if session_ended:
            time_up = match.advance_session()
            if time_up:
                match.result = match.result or "Match Drawn"
                embed.set_footer(text="Day 5 complete — Match Drawn")
            else:
                embed.set_footer(text="Session complete")
        elif over_ended:
            embed.set_footer(text="Over complete")
            inn.over_log = []   # reset timeline for the next over

        _test_post_innings_logic(match)
        await interaction.channel.send(embed=embed)

        if match.result or match.day > 5:
            if innings_ended:
                await interaction.channel.send(embed=_render_test_innings_embed(match, inn_idx))
            return await _test_finish_match(match, self.channel_id, interaction.channel)

        if innings_ended:
            await interaction.channel.send(embed=_render_test_innings_embed(match, inn_idx))

        await interaction.channel.send(embed=render_test_embed(match), view=TestSimView(match, self.channel_id))

    @discord.ui.button(label="⚡ Multiple Overs", style=discord.ButtonStyle.primary, row=0)
    async def sim_multiple_overs(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_host(interaction): return
        await interaction.response.send_modal(
            TestMultipleOversModal(self.match, self.channel_id, interaction.channel)
        )

    # Row 1: Session / Day / Innings

    @discord.ui.button(label="☕ Session", style=discord.ButtonStyle.secondary, row=1)
    async def sim_session(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_host(interaction): return
        await interaction.response.defer()
        await interaction.message.edit(view=None)
        match   = self.match
        snap    = _test_take_snapshot(match)
        inn_idx = match.current_innings_idx

        await asyncio.to_thread(_test_sim_session, match)

        # Detect mid-session innings end.
        # After FULL session: advance_session() resets overs_in_session -> 0.
        # After MID-SESSION stop: overs_in_session left at partial count (> 0).
        sim_inn         = match.innings_list[inn_idx]
        innings_ended   = sim_inn.is_complete and match.overs_in_session > 0
        remaining_overs = 30 - match.overs_in_session if innings_ended else 0

        _test_post_innings_logic(match)

        sess_embed = _render_test_session_embed(match, snap)
        if innings_ended and remaining_overs > 0:
            sess_embed.set_footer(
                text=f"⚠️  Innings ended mid-session — {remaining_overs} over{'s' if remaining_overs != 1 else ''} still remain. Next 'Session' will continue this session."
            )

        if match.result or match.day > 5:
            await interaction.channel.send(embed=sess_embed)
            if innings_ended:
                await interaction.channel.send(embed=_render_test_innings_embed(match, inn_idx))
            return await _test_finish_match(match, self.channel_id, interaction.channel)

        await interaction.channel.send(embed=sess_embed)
        if innings_ended:
            await interaction.channel.send(embed=_render_test_innings_embed(match, inn_idx))
        await interaction.channel.send(embed=render_test_embed(match), view=TestSimView(match, self.channel_id))

    @discord.ui.button(label="📅 Full Day", style=discord.ButtonStyle.secondary, row=1)
    async def sim_full_day(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_host(interaction): return
        await interaction.response.defer()
        await interaction.message.edit(view=None)
        match     = self.match
        start_day = match.day
        iters     = 0

        while match.day == start_day and not match.result and match.day <= 5 and iters < 8:
            iters  += 1
            inn_idx = match.current_innings_idx
            snap    = _test_take_snapshot(match)

            await asyncio.to_thread(_test_sim_session, match)

            sim_inn         = match.innings_list[inn_idx]
            innings_ended   = sim_inn.is_complete and match.overs_in_session > 0
            remaining_overs = 30 - match.overs_in_session if innings_ended else 0

            _test_post_innings_logic(match)

            sess_embed = _render_test_session_embed(match, snap)
            if innings_ended and remaining_overs > 0:
                sess_embed.set_footer(
                    text=f"⚠️  Innings ended mid-session — {remaining_overs} over{'s' if remaining_overs != 1 else ''} still remain."
                )

            await interaction.channel.send(embed=sess_embed)
            if innings_ended:
                await interaction.channel.send(embed=_render_test_innings_embed(match, inn_idx))

            if match.result or match.day > 5:
                return await _test_finish_match(match, self.channel_id, interaction.channel)

        await interaction.channel.send(embed=render_test_embed(match), view=TestSimView(match, self.channel_id))

    @discord.ui.button(label="🏟️ Innings", style=discord.ButtonStyle.danger, row=1)
    async def sim_innings(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_host(interaction): return
        await interaction.response.defer()
        await interaction.message.edit(view=None)
        match   = self.match
        inn_idx = match.current_innings_idx
        await asyncio.to_thread(_test_sim_innings, match)
        _test_post_innings_logic(match)
        inn_embed = _render_test_innings_embed(match, inn_idx)
        if match.result or match.day > 5:
            await interaction.channel.send(embed=inn_embed)
            return await _test_finish_match(match, self.channel_id, interaction.channel)
        await interaction.channel.send(embed=inn_embed)
        await interaction.channel.send(embed=render_test_embed(match), view=TestSimView(match, self.channel_id))

    # Row 2: Declaration

    @discord.ui.button(label="📣 Declare", style=discord.ButtonStyle.danger, row=2)
    async def declare_innings(self, interaction: discord.Interaction, button: discord.ui.Button):
        match = self.match
        inn   = match.current_innings
        if inn.is_complete:
            await interaction.response.send_message("❌ The current innings is already over.", ephemeral=True)
            return
        # AI team cannot declare via button (simulation handles AI declarations automatically)
        if _test_ai_batting(match):
            await interaction.response.send_message("❌ The AI team manages its own innings.", ephemeral=True)
            return
        # Only the batting captain may declare
        batting_owner = match.host_id if inn.batting_team is match.team1 else getattr(match, "p2_id", match.host_id)
        if interaction.user.id != batting_owner:
            await interaction.response.send_message("❌ Only the batting captain can declare.", ephemeral=True)
            return
        score = f"{inn.total_runs}/{inn.wickets} ({inn.overs_str} ov)"
        await interaction.response.send_message(
            f"📣 **Declare at {score}?** This will close the innings immediately.",
            view=TestDeclareConfirmView(match, self.channel_id, interaction.message),
            ephemeral=True
        )


class TestDeclareConfirmView(discord.ui.View):
    def __init__(self, match: TestMatchObj, channel_id: int, sim_message):
        super().__init__(timeout=60)
        self.match       = match
        self.channel_id  = channel_id
        self.sim_message = sim_message

    @discord.ui.button(label="✅ Yes, Declare", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        match = self.match
        inn   = match.current_innings
        inn_idx = match.current_innings_idx

        inn.declared    = True
        inn.is_complete = True

        _test_post_innings_logic(match)

        score = f"{inn.total_runs}/{inn.wickets}d ({inn.overs_str} ov)"
        await interaction.response.send_message(
            f"📣 **{inn.batting_team['name']} have declared at {score}!**"
        )

        try:
            await self.sim_message.edit(view=None)
        except Exception:
            pass

        if match.result or match.day > 5:
            await interaction.channel.send(embed=_render_test_innings_embed(match, inn_idx))
            return await _test_finish_match(match, self.channel_id, interaction.channel)

        await interaction.channel.send(embed=_render_test_innings_embed(match, inn_idx))
        await interaction.channel.send(embed=render_test_embed(match), view=TestSimView(match, self.channel_id))

    @discord.ui.button(label="❌ Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("Declaration cancelled.", ephemeral=True)
        self.stop()


async def _begin_test_match(channel, state):
    """Branch from start_match for Test (format_overs == 90) matches."""
    _sid = getattr(state, "tournament_server_id", None) or (str(channel.guild.id) if getattr(channel, "guild", None) else None)
    t1 = {"name": state.t1_name, "players": with_captain(apply_server_overrides(state.t1_roster, _sid)), "color": getattr(state, 't1_color', '#1D4ED8')}
    t2 = {"name": state.t2_name, "players": with_captain(apply_server_overrides(state.t2_roster, _sid)), "color": getattr(state, 't2_color', '#DC2626')}
    weather = state.weather

    # sim_only (/simulatematch): fully auto
    if getattr(state, 'sim_only', False):
        winner_team = random.choice([t1, t2])
        loser_team  = t2 if winner_team is t1 else t1
        decision    = random.choice(["Bat", "Bowl"])
        t_bat  = winner_team if decision == "Bat" else loser_team
        t_bowl = loser_team  if decision == "Bat" else winner_team
        await channel.send(
            f"🪙 **Toss!** **{winner_team['name']}** wins and elects to **{decision}** first!\n"
            f"*Simulating 5-day Test... ⚙️*")
        match = TestMatchObj(t_bat, t_bowl, state.pitch, weather, pink_ball=getattr(state, "pink_ball", False))
        match.is_player_test = getattr(state, "is_player_test", False)
        active_test_matches[channel.id] = match
        await asyncio.to_thread(_test_sim_match, match)
        await _test_finish_match(match, channel.id, channel)
        return

    # The toss already ran before the XIs - just apply the stored result.
    # winner None = the AI side (team 2).
    winner_is_p1 = (state.toss_winner_id == state.p1_id)
    winning_team = t1 if winner_is_p1 else t2
    losing_team  = t2 if winner_is_p1 else t1
    t_bat  = winning_team if state.toss_choice == "Bat" else losing_team
    t_bowl = losing_team  if state.toss_choice == "Bat" else winning_team

    match          = TestMatchObj(t_bat, t_bowl, state.pitch, weather, pink_ball=getattr(state, "pink_ball", False))
    match.is_player_test = getattr(state, "is_player_test", False)
    match.host_id  = state.p1_id
    match.p2_id    = getattr(state, "p2_id", None)
    match.host_team = t1            # team identity -> owner (NOT bat/bowl order)
    match.p2_team   = t2
    active_test_matches[channel.id] = match
    await channel.send(f"**{t_bat['name']}** will bat first.\n\nChoose how to simulate:")
    await channel.send(embed=render_test_embed(match), view=TestSimView(match, channel.id))


@bot.tree.command(name="simulatematch", description="Simulate a full match between two custom teams instantly.")
async def simulatematch_cmd(interaction: discord.Interaction):
    
    await interaction.response.defer()
    if is_channel_restricted(str(interaction.channel.id)):
        return await interaction.edit_original_response(content="❌ Matches are **disabled** in this channel. Please switch to a dedicated bot channel to play!")

    allowed, reason = await asyncio.to_thread(check_potential_quota, str(interaction.user.id), str(interaction.guild.id) if interaction.guild else None, str(ADMIN_DISCORD_ID))
    if not allowed: return await interaction.edit_original_response(content=reason)

    if interaction.channel.id in active_games: 
        
        return await interaction.edit_original_response(content="❌ A match is already in progress in this channel. Use `/endmatch` to stop it.")
    if interaction.channel.id in active_setups: 
        
        return await interaction.edit_original_response(content="❌ A setup is already happening here. Use `/endmatch` to cancel it.")

    state = MatchSetupState(interaction.user, None, interaction.user.id, None)
    state.sim_only = True
    
    active_setups[interaction.channel.id] = ("format_selection", state)
    setup_states[interaction.channel.id] = state

    await interaction.edit_original_response(content=f"⚙️ **Custom Simulation Setup**\n**Host:** {interaction.user.mention}\n\nYou will be prompted to provide the Playing XI for *both* teams.\nStep 1: Select Format below:", view=FormatSelectView(state, interaction.channel))

@bot.event
async def on_start_tournament_match(channel, manager_id, tourney, match_data):
    team1_name = match_data["team1"]
    team2_name = match_data["team2"]
    # Reload fresh tourney so injury data from the last match is current
    tourney = get_server_tournament(str(tourney["server_id"])) or tourney
    t1_data = next(t for t in tourney["teams"] if t["name"] == team1_name)
    t2_data = next(t for t in tourney["teams"] if t["name"] == team2_name)

    current_mid = match_data["match_id"]

    # Injury expiry is COUNT-based and counted down at match COMPLETION
    # (see on_tournament_match_complete) - NOT here. That way, starting a match that is
    # then abandoned/incomplete does not consume an injury. At match start we only need to
    # leave still-injured players out of the XI, which available_squad() does below.

    # Injuries are now reported immediately when a match completes
    # (see on_tournament_match_complete), so nothing to announce here.
    tourney.pop("pending_injury_news", None)

    save_tournament(tourney)

    # Filter injured players from XI selection (fallback to full squad if < 11 fit)
    def available_squad(squad):
        fit = [p for p in squad if not p.get("injured")]
        return fit if len(fit) >= 11 else squad

    p1_id = int(t1_data["owner_id"])
    p2_id = int(t2_data["owner_id"])

    state = MatchSetupState(None, None, p1_id, p2_id)
    state.t1_name = team1_name
    state.t2_name = team2_name
    state.t1_squad = available_squad(t1_data["squad"])
    state.t2_squad = available_squad(t2_data["squad"])
    state.t1_color = t1_data.get("color", "#6B7280")
    state.t2_color = t2_data.get("color", "#6B7280")
    state.tournament_server_id = tourney["server_id"]
    state.tournament_match_id = match_data["match_id"]
    state.manager_id = manager_id
    state.tournament_name = tourney["name"]
    state.tournament_type = tourney.get("tournament_type", "round_robin")   # engine reads this (DSL realism mode)

    # Default XIs (resolved against the FIT squad - an injured/missing player
    # invalidates the default and the owner types the XI instead).
    state.t1_default_xi = resolve_default_xi(t1_data, state.t1_squad)
    state.t2_default_xi = resolve_default_xi(t2_data, state.t2_squad)
    state.t1_default_captain = t1_data.get("default_captain")
    state.t2_default_captain = t2_data.get("default_captain")
    # Default impact subs (impact tournaments): auto-applied with the default XI.
    state.t1_default_subs = resolve_default_subs(t1_data, state.t1_squad, state.t1_default_xi or [])
    state.t2_default_subs = resolve_default_subs(t2_data, state.t2_squad, state.t2_default_xi or [])
    state.format_overs = tourney.get("format_overs", 20)
    state.impact_player = tourney.get("impact_player", False)

    # Auto/home conditions mode: preset pitch+weather so this match skips the picker.
    if tourney.get("conditions_mode", "manual") != "manual":
        md = next((m for m in tourney.get("schedule", []) if m["match_id"] == match_data["match_id"]), match_data)
        if not (md.get("pitch") and md.get("weather")):
            assign_tournament_conditions(tourney)
            save_tournament(tourney)
            md = next((m for m in tourney.get("schedule", []) if m["match_id"] == match_data["match_id"]), md)
        if md.get("pitch") and md.get("weather"):
            state.pitch = md["pitch"]
            state.weather = md["weather"]
            state.conditions_preset = True

    active_setups[channel.id] = ("tournament_setup", state)

    if tourney.get("tournament_type") == "t20_world_cup":
        r_label = match_data.get("round", f"Match {match_data['match_id']}")
        try:
            banner_buf = generate_t20wc_match_banner(tourney, match_data)
            await channel.send(
                f"🏆 **{tourney['name']}** — **{r_label}**\n<@{p1_id}> vs <@{p2_id}>",
                file=discord.File(banner_buf, filename="match_banner.png")
            )
        except Exception as e:
            print(f"Match banner failed: {e}")
            await channel.send(f"🏆 **Tournament Match {match_data['match_id']}**\n**{team1_name}** (<@{p1_id}>) vs **{team2_name}** (<@{p2_id}>)\n\nFormat: **{state.format_overs} Overs**")
    else:
        await channel.send(f"🏆 **Tournament Match {match_data['match_id']}**\n**{team1_name}** (<@{p1_id}>) vs **{team2_name}** (<@{p2_id}>)\n\nFormat: **{state.format_overs} Overs**")

    # Conditions + toss first, so both owners pick their XI knowing the result.
    await proceed_to_conditions(channel, state)

def _force_end_channel(channel_id) -> bool:
    """Tear down EVERY kind of activity in a channel (match / setup / test / draft).
    Returns True if anything was actually cleared. Shared by slash + prefix endmatch."""
    cleared = False
    if channel_id in active_games:
        del active_games[channel_id]; cleared = True
    if channel_id in active_setups:
        del active_setups[channel_id]; cleared = True
    if channel_id in active_test_matches:
        del active_test_matches[channel_id]; cleared = True
    # Pre-match setup (format->pitch select): flag it cancelled so the live setup views bail.
    st = setup_states.pop(channel_id, None)
    if st is not None:
        st.cancelled = True; cleared = True
    # Running draft: cancel its asyncio task so the wait_for loop aborts immediately.
    if channel_id in active_drafts:
        active_drafts.discard(channel_id); cleared = True
    task = draft_tasks.pop(channel_id, None)
    if task is not None and not task.done():
        task.cancel(); cleared = True
    return cleared


@bot.tree.command(name="endmatch", description="Force cancel the current match or setup in this channel.")
async def endmatch_cmd(interaction: discord.Interaction):
    channel_id = interaction.channel.id
    cleared = _force_end_channel(channel_id)
    if cleared:
        await interaction.response.send_message("🛑 **Match and setup forcefully terminated.** Memory cleared.")
    else:
        await interaction.response.send_message("⚠️ There is no active match or setup running in this channel.", ephemeral=True)

# Help embeds

def _help_home_embed():
    e = discord.Embed(
        title="🏏 CricVerse",
        description="Cricket simulation bot — play matches, run tournaments, manage players.\n\nPick a category below to explore commands.",
        color=0x1D4ED8
    )
    e.add_field(name="🎮 Match Play",   value="Interactive & instant matches",   inline=True)
    e.add_field(name="🔍 Players",      value="Search & manage player database", inline=True)
    e.add_field(name="🏆 Tournament",   value="Create & run tournaments",        inline=True)
    e.add_field(
        name="⚡ Shortcut aliases",
        value=(
            "`cv m` · `cv em` · `cv sp` · `cv ap` · `cv up` · `cv dp` · `cv cd`\n"
            "`cv fs` · `cv fl` · `cv dc` · `cv scsv` · `cv sc` · `cv sut` · `cv sst`\n"
            "`cv tcl` · `cv slc` · `cv trc` · `cv aa` · `cvt` · `cv sq`\n"
            "-# All shortcuts use the same `cv` prefix"
        ),
        inline=False
    )
    e.set_footer(text="cv help <command>  for detailed prefix command usage  ·  cvt = cv tournament")
    return e

def _help_match_embed():
    e = discord.Embed(title="🎮 Match Play", color=discord.Color.green())
    e.add_field(name="/match [@opponent]  ·  `cv match`  ·  `cv m`",  value="Start an interactive match vs a user, or leave blank to play vs AI.", inline=False)
    e.add_field(name="/simulatematch",                                  value="Instantly simulate a full match — pick teams, format and conditions.", inline=False)
    e.add_field(name="`cv testplayer`  ·  `cv tp`",                    value="Test up to **22 players** in a live match: paste all the names at once. 1-11 join a balanced XI vs a Weak/Balanced/Tough net side; 12-22 split into two even Test XIs that play each other. Engine picks the batting order — `Virat Kohli 3` **pins him to bat #3**. Then the normal pitch → toss → match flow runs.\n-# Player tests don't use your daily match allowance or global match totals.", inline=False)
    e.add_field(name="TEST format in /match",                           value="Select 'TEST (90 overs)' in the format dropdown to play a 5-day Test with session/innings/full-match modes.", inline=False)
    e.add_field(name="/impactplayer",                                   value="During an active match, swap in your Impact Player (if rule is on).", inline=False)
    e.add_field(name="`cv verbose`  ·  `cv vb`",                       value="During a 🎬 Ball-by-Ball broadcast: finishes the current over ball-by-ball, then sims the **rest of the match** in verbose (one card per over). Tournament stats & results record as normal.", inline=False)
    e.add_field(name="`cv resume`  ·  `cv forcehub`",                  value="Match stuck with no buttons (Discord hiccup ate the prompt)? Re-shows the lost over hub / bowler pick / next-batter prompt — no progress is lost.", inline=False)
    e.add_field(name="/endmatch  ·  `cv endmatch`  ·  `cv em`",       value="Force-cancel the current match or setup in this channel.", inline=False)
    e.add_field(name="/my_tier",                                        value="Check your subscription tier and remaining daily match limits.", inline=False)
    e.set_footer(text="Slash commands work from anywhere  ·  cv / cv<shortcut> need the cv prefix")
    return e

def _help_players_embed(is_admin: bool):
    e = discord.Embed(title="🔍 Players & Database", color=discord.Color.blue())
    e.add_field(name="/searchplayer <name>  ·  `cv sp`", value="Search for a player — shows their role.", inline=False)
    e.add_field(name="/playerlist  ·  `cv playerlist`  ·  `cv pl`", value="Download the full player database as a .txt file — names only, grouped by tier, shuffled within each tier.", inline=False)
    e.add_field(name="`cv playerlistcompact`  ·  `cv pla`", value="Same tier-grouped list, but names are comma-separated within each tier (compact).", inline=False)
    e.add_field(name="📋 How to enter Playing XI",        value="When prompted during a match, paste 11 player names (one per line). Names must match the database exactly.", inline=False)
    e.add_field(name="🏟️ Pitch & Weather Conditions",    value="15 pitch types · 10 weather conditions — each affects pace, spin and batting differently across T20 and ODI.", inline=False)
    if is_admin:
        e.add_field(name="​", value="─── **Admin — DB Management** ───", inline=False)
        e.add_field(name="/addplayer  ·  `cv ap`",            value="Add a player: name & ratings modal → then role & archetype dropdowns.", inline=False)
        e.add_field(name="/updateplayer <name>  ·  `cv up`",  value="Edit an existing player — all fields pre-filled, change only what you need.", inline=False)
        e.add_field(name="`cv deleteplayer`  ·  `cv dp`",     value="Remove a player from the database.", inline=False)
        e.add_field(name="`cv cleanduplicates`  ·  `cv cd`",  value="Remove duplicate entries from the database.", inline=False)
    return e

def _help_tournament_embed():
    e = discord.Embed(title="🏆 Tournament", color=discord.Color.gold())
    e.description = "All commands: **`cv tournament <cmd>`** · shortcut **`cvt <cmd>`** · group alias **`cv t <cmd>`**"
    e.add_field(name="👁️ View",
        value=("`status` · `standings` · `groups` · `leaderboard <category>`\n"
               "`squad [team]`  ·  shortcut: **`cv squad`** / **`cv sq`**\n"
               "`player_stats <player>` (alias `ps`) · `match_scorecard <id>`"),
        inline=False)
    e.add_field(name="🏏 Play",
        value=("`submit_squad` · `next_match` · `play <id>` · `play_next`\n"
               "`simulate_all`  ·  alias: **`simall`** — [Owner] instantly sim all pending matches"),
        inline=False)
    e.add_field(name="⚙️ Manage",
        value=("`create <name> <format>` · `add_team <name> @owner` · `start`\n"
               "`set_theme` · `set_team_color` · `set_team_logo` · `set_schedule`\n"
               "`generate_knockouts` · `generate_finals`/`gf` · `force_delete`"),
        inline=False)
    e.add_field(name="🔧 Schedule / Dev",
        value=("`admin_restore_schedule` · `admin_force_restore_schedule`\n"
               "`dev_setup` — [Owner] fill squads & auto-start for testing"),
        inline=False)
    e.add_field(name="📊 Leaderboard categories",
        value="`runs` · `wickets` · `sr` · `bat_avg` · `fours` · `sixes` · `fifties` · `hundreds` · `econ` · `bowl_avg` · `mvp`",
        inline=False)
    e.set_footer(text="cvt help  for full argument details on any tournament subcommand")
    return e

def _help_admin_embed():
    e = discord.Embed(title="🛡️ Admin Commands", color=discord.Color.red())
    e.add_field(name="Channel Controls",
        value=("`cv toggle_channel_lock`  ·  `cv tcl` — lock/unlock matches in this channel\n"
               "`cv set_log_channel`  ·  `cv slc` — set/unset this channel as match log\n"
               "`cv toggle_ratings_channel`  ·  `cv trc` — toggle rating visibility here"),
        inline=False)
    e.add_field(name="Tournament Admin",
        value=("`cvt add_manager @user` · `cvt remove_team` · `cvt replace_player`\n"
               "`cvt force_result <id> ...` · `cvt admin_record_result` · `cvt force_delete`\n"
               "`cvt admin_restore_schedule` · `cvt admin_force_restore_schedule`\n"
               "`cvt set_team_color` · `cvt set_team_logo`"),
        inline=False)
    e.set_footer(text="Player DB commands are in the 🔍 Players section")
    return e

def _help_owner_embed():
    e = discord.Embed(title="👑 Owner Commands", color=discord.Color.purple())
    e.add_field(name="Cache",
        value=("`cv force_sync`  ·  `cv fs` — save cache to MongoDB\n"
               "`cv force_load`  ·  `cv fl` — reload cache from MongoDB\n"
               "`cv dump_cache`  ·  `cv dc` — export tournament cache as JSON\n"
               "`cv sync_csv`  ·  `cv scsv` — import players from CSV"),
        inline=False)
    e.add_field(name="Match Counters",
        value=("`cv counts`  ·  `cv matchcounts` — show total matches played per format\n"
               "`cv setcount <format> <n>`  ·  `cv sc` — manually set a format's match counter"),
        inline=False)
    e.add_field(name="Subscriptions",
        value=("`/set_user_tier @user <tier>`  ·  `cv sut @user <tier>`\n"
               "`/set_server_tier <server_id> <tier>`  ·  `cv sst <id> <tier>`\n"
               "`cv subs`  ·  `cv list_subs` — list ALL active subs (user + server) with indexes\n"
               "`cv remove_sub <index>`  ·  `cv rsub` — remove a sub by its `cv subs` index\n"
               "`cv authadmin @user`  ·  `cv aa` — toggle admin access"),
        inline=False)
    e.add_field(name="Tournament Owner",
        value=("`cvt simulate_all`  ·  `cvt simall` — instantly sim all pending matches\n"
               "`cvt dev_setup` — fill squads with random players & auto-start (testing)\n"
               "`cvt set_schedule` — set a custom fixture order"),
        inline=False)
    e.add_field(name="User Tiers",   value="`Basic` · `Standard` · `Single` · `Server Pro` · `Career Beta` · `None`", inline=True)
    e.add_field(name="Server Tiers", value="`Bronze` · `Silver` · `Gold` · `Diamond` · `None`",       inline=True)
    return e

# Help navigation view

class HelpNavigator(discord.ui.View):
    _PAGES = {"home": _help_home_embed, "match": _help_match_embed,
              "tournament": _help_tournament_embed, "admin": _help_admin_embed, "owner": _help_owner_embed}

    def __init__(self, user_id: int, is_admin: bool, is_owner: bool, page: str = "home"):
        super().__init__(timeout=180)
        self._user     = user_id
        self._is_admin = is_admin
        self._is_owner = is_owner
        self._page     = page
        if is_admin or is_owner:
            b = discord.ui.Button(label="🛡️ Admin", style=discord.ButtonStyle.danger, row=1)
            b.callback = self._admin_cb
            self.add_item(b)
        if is_owner:
            b = discord.ui.Button(label="👑 Owner", style=discord.ButtonStyle.danger, row=1)
            b.callback = self._owner_cb
            self.add_item(b)
        self._sync_disabled()

    def _sync_disabled(self):
        label_map = {"🏠 Home": "home", "🎮 Match": "match", "🔍 Players": "players",
                     "🏆 Tournament": "tournament", "🛡️ Admin": "admin", "👑 Owner": "owner"}
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                item.disabled = (label_map.get(item.label) == self._page)

    def _current_embed(self):
        if self._page == "players":
            return _help_players_embed(self._is_admin)
        fn = self._PAGES.get(self._page, _help_home_embed)
        return fn()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self._user:
            await interaction.response.send_message("Not your help menu.", ephemeral=True)
            return False
        return True

    async def _goto(self, interaction: discord.Interaction, page: str):
        self._page = page
        self._sync_disabled()
        await interaction.response.edit_message(embed=self._current_embed(), view=self)

    @discord.ui.button(label="🏠 Home",        style=discord.ButtonStyle.secondary, row=0, disabled=True)
    async def home_btn(self, interaction, _):    await self._goto(interaction, "home")

    @discord.ui.button(label="🎮 Match",        style=discord.ButtonStyle.primary, row=0)
    async def match_btn(self, interaction, _):   await self._goto(interaction, "match")

    @discord.ui.button(label="🔍 Players",      style=discord.ButtonStyle.primary, row=0)
    async def players_btn(self, interaction, _): await self._goto(interaction, "players")

    @discord.ui.button(label="🏆 Tournament",   style=discord.ButtonStyle.primary, row=0)
    async def tourn_btn(self, interaction, _):   await self._goto(interaction, "tournament")

    async def _admin_cb(self, interaction):      await self._goto(interaction, "admin")
    async def _owner_cb(self, interaction):      await self._goto(interaction, "owner")

@bot.tree.command(name="help", description="Show CricVerse commands and how to use them.")
async def help_cmd(interaction: discord.Interaction):
    is_owner = interaction.user.id == ADMIN_DISCORD_ID
    is_admin = is_owner or str(interaction.user.id) in get_auth_admins()
    view = HelpNavigator(interaction.user.id, is_admin, is_owner)
    await interaction.response.send_message(embed=_help_home_embed(), view=view, ephemeral=True)

class ImpactPlayerSelectView(discord.ui.View):
    def __init__(self, match: CricketMatch, team_id: int):
        super().__init__(timeout=120)
        self.match = match
        self.team_id = team_id
        self.team = match.team1 if team_id == 1 else match.team2
        self.subs = match.t1_subs if team_id == 1 else match.t2_subs
        
        out_opts = []
        for p in self.team["players"]:
            if len(out_opts) < 25:
                inn = self.match.current_innings
                if inn:
                    if inn.batting_team["name"] == self.team["name"]:
                        curr_strikers = [inn.batting_team["players"][inn.current_striker_idx]["name"], inn.batting_team["players"][inn.current_non_striker_idx]["name"]]
                        if p["name"] in curr_strikers: continue
                    if inn.bowling_team["name"] == self.team["name"]:
                        if inn.current_bowler and p["name"] == inn.current_bowler["name"]: continue
                out_opts.append(discord.SelectOption(label=f"OUT: {p['name']}", value=p["name"]))
                
        in_opts = []
        for p in self.subs:
            role_short = p["role"].split("_")[0]
            in_opts.append(discord.SelectOption(label=f"IN: {p['name']} ({role_short})", value=p["name"]))
            
        self.select_out = discord.ui.Select(placeholder="Player to swap OUT...", options=out_opts, custom_id="out")
        self.select_in = discord.ui.Select(placeholder="Player to bring IN...", options=in_opts, custom_id="in")
        self.select_out.callback = self.cb
        self.select_in.callback = self.cb
        self.add_item(self.select_out)
        self.add_item(self.select_in)
        
        self.btn = discord.ui.Button(label="Confirm Swap", style=discord.ButtonStyle.success, disabled=True)
        self.btn.callback = self.confirm_cb
        self.add_item(self.btn)
        
    async def cb(self, interaction: discord.Interaction):
        if self.select_out.values and self.select_in.values:
            self.btn.disabled = False
        await interaction.response.edit_message(view=self)
        
    async def confirm_cb(self, interaction: discord.Interaction):
        out_name = self.select_out.values[0]
        in_name = self.select_in.values[0]
        in_player = next(p for p in self.subs if p["name"] == in_name)
        team = self.match.team1 if self.team_id == 1 else self.match.team2
        swap_impact_player(self.match, self.team_id, out_name, in_player)
        await interaction.response.edit_message(content=f"✅ Swap confirmed!", view=None)
        await interaction.channel.send(
            f"🔄 **IMPACT PLAYER!** | **{team['name']}**\n"
            f"🚪 **OUT:** {out_name}\n"
            f"✅ **IN:** {in_name}"
        )

@bot.tree.command(name="impactplayer", description="Swap in your Impact Player during an active match.")
async def impact_player_cmd(interaction: discord.Interaction):
    channel_id = interaction.channel.id
    if channel_id not in active_games: return await interaction.response.send_message("❌ No active match in this channel.", ephemeral=True)
    match = active_games[channel_id]
    if not getattr(match, "impact_player", False): return await interaction.response.send_message("❌ Impact Player rule is not enabled for this match.", ephemeral=True)
    
    team_id = 1 if interaction.user.id == match.p1_id else (2 if interaction.user.id == match.p2_id else None)
    if not team_id: return await interaction.response.send_message("❌ You are not playing in this match.", ephemeral=True)
    
    if (team_id == 1 and getattr(match, "t1_impact_used", False)) or (team_id == 2 and getattr(match, "t2_impact_used", False)):
        return await interaction.response.send_message("❌ You have already used your Impact Player.", ephemeral=True)
        
    subs = match.t1_subs if team_id == 1 else match.t2_subs
    if not subs: return await interaction.response.send_message("❌ You have no subs available.", ephemeral=True)
        
    await interaction.response.send_message("🔄 **Select your Impact Player Swap:**", view=ImpactPlayerSelectView(match, team_id), ephemeral=True)

# ---- Public database search ----

def _can_see_ratings(user_id: int, channel_id: int) -> bool:
    if user_id == ADMIN_DISCORD_ID:
        return True
    if str(user_id) in get_auth_admins():
        return is_ratings_channel(str(channel_id))
    return False

async def send_player_profile(interaction, player: dict, show_ratings: bool = True):
    if show_ratings:
        embed = discord.Embed(title=f"🏏 Player Profile: {player['name']}", color=0x1D4ED8)
        embed.add_field(name="🔥 Batting", value=f"`{player['bat']}`", inline=True)
        embed.add_field(name="🎯 Bowling", value=f"`{player['bowl']}`", inline=True)
        embed.add_field(name="📋 Role", value=player["role"].replace("_", " "), inline=True)
        embed.add_field(name="🧠 Archetype", value=player["archetype"], inline=True)
    else:
        role_str = _ROLE_DISPLAY.get(player.get("role", ""), player.get("role", "Unknown"))
        embed = discord.Embed(title=f"🏏 {player['name']}", color=0x1D4ED8)
        embed.add_field(name="📋 Role", value=role_str, inline=False)
        embed.description = "*Use `/match` to pick this player and put them to the test!*"
    await interaction.followup.send(embed=embed)

@bot.tree.command(name="searchplayer", description="Search for a player in the Cloud DB.")
async def searchplayer(interaction: discord.Interaction, name: str):
    await interaction.response.defer()
    search_query = name.strip()
    all_players = get_all_players()
    player_names = [p["name"] for p in all_players]

    if not all_players:
        return await interaction.followup.send("❌ Error: Cache is empty.")

    show_ratings = _can_see_ratings(interaction.user.id, interaction.channel_id)

    exact = next((p for p in all_players if p["name"].lower() == search_query.lower()), None)
    if exact:
        return await send_player_profile(interaction, exact, show_ratings)

    subs = [p for p in all_players if search_query.lower() in p["name"].lower()]
    fuzz = difflib.get_close_matches(search_query, player_names, n=1, cutoff=0.2)

    if not subs and not fuzz:
        return await interaction.followup.send(f"❌ Player `{search_query}` not found in the database.")

    if fuzz:
        best_name = fuzz[0]
    else:
        best_name = subs[0]["name"]

    if len(subs) == 1 and not fuzz:
        return await send_player_profile(interaction, subs[0], show_ratings)

    other = [p["name"] for p in subs if p["name"] != best_name]
    msg = f"🔍 **Not found exactly.**\n💡 **Best Match:** `{best_name}`\n👉 Rerun: `/searchplayer name: {best_name}`"
    if other:
        msg += "\n\n📂 **Alternatives:**\n" + "\n".join(f"• {o}" for o in other[:5])
    await interaction.followup.send(msg)


# Player list helpers

_ROLE_DISPLAY = {
    "Batter":               "Batter",
    "Batter_WK":            "WK-Batter",
    "Bowler_Pace":          "Pace Bowler",
    "Bowler_Spin_Off":      "Off-Spin Bowler",
    "Bowler_Spin_Leg":      "Leg-Spin Bowler",
    "All-Rounder_Pace":     "Pace All-Rounder",
    "All-Rounder_Spin_Off": "Off-Spin All-Rounder",
    "All-Rounder_Spin_Leg": "Leg-Spin All-Rounder",
}

def _player_overall(p: dict) -> float:
    bat  = float(p.get("bat",  50))
    bowl = float(p.get("bowl", 50))
    role = p.get("role", "")
    if role.startswith("All-Rounder"):
        return (bat + bowl) / 2
    if role.startswith("Bowler"):
        return bowl
    return bat   # Batter / WK-Batter

def _best_xi(squad: list, n: int = 11, min_bowlers: int = 5) -> list:
    """Pick the strongest BALANCED XI from a squad: top players by overall rating, but
    ensure at least `min_bowlers` who can bowl (swap the weakest pure batters for the
    best available bowlers if the top-n comes out batting-heavy)."""
    pool = sorted(squad, key=_player_overall, reverse=True)
    xi = list(pool[:n])
    def _bowls(p): r = p.get("role", ""); return "Bowler" in r or "All-Rounder" in r
    have = sum(1 for p in xi if _bowls(p))
    if have < min_bowlers:
        in_names = {p["name"] for p in xi}
        spare = [p for p in pool if _bowls(p) and p["name"] not in in_names]
        drop  = sorted([p for p in xi if not _bowls(p)], key=_player_overall)  # weakest batters first
        for i in range(min(min_bowlers - have, len(spare), len(drop))):
            xi.remove(drop[i]); xi.append(spare[i])
    return xi

def resolve_default_xi(team_data: dict, fit_squad: list):
    """A team's saved default XI resolved against the currently-FIT squad, in the
    saved (batting) order. Returns the list of player dicts, or None unless every
    one of the 11 names resolves to a fit squad member, uniquely, with a keeper -
    so an injured/transferred player automatically invalidates the default."""
    names = team_data.get("default_xi") or []
    if len(names) != 11:
        return None
    by_name = {p["name"].lower(): p for p in fit_squad}
    xi, seen = [], set()
    for nm in names:
        p = by_name.get(str(nm).strip().lower())
        if not p or p["name"] in seen:
            return None
        xi.append(p)
        seen.add(p["name"])
    if not _has_wk(xi):
        return None
    return xi


def resolve_default_subs(team_data: dict, fit_squad: list, xi: list):
    """The saved default IMPACT subs resolved against the fit squad - best-effort:
    invalid entries (injured / left squad / already in the XI) are silently dropped
    rather than invalidating the whole default. Capped at 5, like the sub picker."""
    names = team_data.get("default_impact") or []
    xi_names = {p["name"] for p in xi}
    by_name = {p["name"].lower(): p for p in fit_squad}
    subs, seen = [], set()
    for nm in names:
        p = by_name.get(str(nm).strip().lower())
        if p and p["name"] not in xi_names and p["name"] not in seen:
            subs.append(p)
            seen.add(p["name"])
    return subs[:5]


def _batting_order(players: list) -> list:
    """Arrange an XI into a realistic batting order (the sim bats in roster order).

    Mostly driven by batting rating (better batters bat earlier) but shaped by role and
    archetype into a proper team sheet: aggressors/anchors open the top order, finishers
    sit at 5-7, all-rounders in the lower-middle, and bowlers form the tail (best-batting
    bowler first). Role weights dominate so a bowler never bats up top, but rating still
    decides within a band - a strong specialist always out-ranks a weak one."""
    def _pos(p):
        role = p.get("role", "")
        arch = p.get("archetype", "Standard")
        bat  = float(p.get("bat", 50))
        score = 100.0 - bat                       # better batters earlier
        if "Bowler" in role:        score += 80   # tail, no matter the rating
        elif "All-Rounder" in role: score += 35   # lower-middle (6-8)
        if arch == "Vaibhav":       score -= 8    # ultra-aggressor - bat him at the top
        elif arch == "Aggressor":   score -= 6    # push openers up a touch
        elif arch == "Anchor":      score -= 2    # top order
        elif arch == "Finisher":    score += 8    # slot finishers at 5-7
        return score
    return sorted(players, key=_pos)

def _build_playerlist_txt(players: list) -> str:
    tiers = {"LEGENDS": [], "ELITE": [], "GOLD": [], "SILVER": [], "BRONZE": []}
    for p in players:
        ov = _player_overall(p)
        if   ov > 95: tiers["LEGENDS"].append(p)
        elif ov > 90: tiers["ELITE"].append(p)
        elif ov >= 85: tiers["GOLD"].append(p)
        elif ov >= 80: tiers["SILVER"].append(p)
        else:          tiers["BRONZE"].append(p)
    for lst in tiers.values():
        random.shuffle(lst)

    lines = [
        "═" * 52,
        f"  CricVerse Player Database  —  {len(players)} players",
        "═" * 52,
        "",
    ]
    tier_labels = {
        "LEGENDS": "👑  LEGENDS",
        "ELITE":   "⭐⭐⭐  ELITE",
        "GOLD":    "⭐⭐    GOLD",
        "SILVER":  "⭐      SILVER",
        "BRONZE":  "         BRONZE",
    }
    for tier, label in tier_labels.items():
        grp = tiers[tier]
        if not grp:
            continue
        lines.append(f"── {label} ({len(grp)}) " + "─" * max(1, 44 - len(label) - len(str(len(grp)))))
        for p in grp:
            lines.append(f"  {p['name']}")
        lines.append("")

    lines.append("═" * 52)
    from datetime import datetime, timezone
    lines.append(f"  Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    lines.append("═" * 52)
    return "\n".join(lines)


def _build_playerlist_csv_txt(players: list) -> str:
    """Same tier grouping as _build_playerlist_txt, but names are comma-separated per tier."""
    tiers = {"LEGENDS": [], "ELITE": [], "GOLD": [], "SILVER": [], "BRONZE": []}
    for p in players:
        ov = _player_overall(p)
        if   ov > 95: tiers["LEGENDS"].append(p)
        elif ov > 90: tiers["ELITE"].append(p)
        elif ov >= 85: tiers["GOLD"].append(p)
        elif ov >= 80: tiers["SILVER"].append(p)
        else:          tiers["BRONZE"].append(p)
    for lst in tiers.values():
        random.shuffle(lst)

    lines = [
        "═" * 52,
        f"  CricVerse Player Database  —  {len(players)} players",
        "═" * 52,
        "",
    ]
    tier_labels = {
        "LEGENDS": "👑  LEGENDS",
        "ELITE":   "⭐⭐⭐  ELITE",
        "GOLD":    "⭐⭐    GOLD",
        "SILVER":  "⭐      SILVER",
        "BRONZE":  "         BRONZE",
    }
    for tier, label in tier_labels.items():
        grp = tiers[tier]
        if not grp:
            continue
        lines.append(f"── {label} ({len(grp)}) " + "─" * max(1, 44 - len(label) - len(str(len(grp)))))
        lines.append(", ".join(p["name"] for p in grp))
        lines.append("")

    lines.append("═" * 52)
    from datetime import datetime, timezone
    lines.append(f"  Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    lines.append("═" * 52)
    return "\n".join(lines)


def _build_playerlist_ratings_txt(players: list) -> str:
    """[OWNER] Full database WITH ratings - sorted by OVR. Separate from the ratings-hidden cv pl."""
    rows = sorted(players, key=lambda p: _player_overall(p), reverse=True)
    lines = [
        "═" * 88,
        f"  CricVerse Player Database — RATINGS  ·  {len(players)} players",
        "═" * 88,
        f"  {'#':>4}  {'NAME':<26}{'BAT':>4}{'BOWL':>5}{'OVR':>5}   {'ROLE':<28}{'ARCHETYPE'}",
        "─" * 88,
    ]
    for i, p in enumerate(rows, 1):
        lines.append(
            f"  {i:>4}  {str(p.get('name',''))[:25]:<26}"
            f"{int(p.get('bat',0)):>4}{int(p.get('bowl',0)):>5}{int(_player_overall(p)):>5}   "
            f"{str(p.get('role','')):<28}{p.get('archetype','')}"
        )
    lines.append("═" * 88)
    from datetime import datetime, timezone
    lines.append(f"  Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    lines.append("═" * 88)
    return "\n".join(lines)


@bot.tree.command(name="playerlist", description="Download the full player database grouped by tier.")
async def playerlist_cmd(interaction: discord.Interaction):
    await interaction.response.defer()
    players = get_all_players()
    if not players:
        return await interaction.followup.send("❌ Player database is empty.")
    txt = _build_playerlist_txt(players)
    buf = io.BytesIO(txt.encode("utf-8"))
    buf.seek(0)
    await interaction.followup.send(
        f"📋 **Player Database** — {len(players)} players across 4 tiers.\nPlayers within each tier are shuffled.",
        file=discord.File(fp=buf, filename="cricverse_players.txt")
    )


@bot.tree.command(name="srating", description="[OWNER] Override a player's ratings for THIS server only (blank player = list · reset = clear).")
@app_commands.describe(
    player="Player name. Leave blank to LIST all overrides on this server.",
    bat="Batting rating 0-99 (this server only)",
    bowl="Bowling rating 0-99 (this server only)",
    role="Role override (this server only)",
    archetype="Archetype override (this server only)",
    reset="Remove this player's override on this server (back to global).",
)
@app_commands.choices(
    role=[
        app_commands.Choice(name="Batter", value="Batter"),
        app_commands.Choice(name="Wicket-Keeper", value="Batter_WK"),
        app_commands.Choice(name="Pace Bowler", value="Bowler_Pace"),
        app_commands.Choice(name="Off-Spin Bowler", value="Bowler_Spin_Off"),
        app_commands.Choice(name="Leg-Spin Bowler", value="Bowler_Spin_Leg"),
        app_commands.Choice(name="Pace All-Rounder", value="All-Rounder_Pace"),
        app_commands.Choice(name="Off-Spin All-Rounder", value="All-Rounder_Spin_Off"),
        app_commands.Choice(name="Leg-Spin All-Rounder", value="All-Rounder_Spin_Leg"),
    ],
    archetype=[
        app_commands.Choice(name="Aggressor", value="Aggressor"),
        app_commands.Choice(name="Anchor", value="Anchor"),
        app_commands.Choice(name="Finisher", value="Finisher"),
        app_commands.Choice(name="Standard", value="Standard"),
        app_commands.Choice(name="Vaibhav (ultra-aggressive)", value="Vaibhav"),
    ],
)
async def srating_slash(interaction: discord.Interaction, player: str = None, bat: int = None, bowl: int = None,
                        role: app_commands.Choice[str] = None, archetype: app_commands.Choice[str] = None, reset: bool = False):
    if interaction.user.id != ADMIN_DISCORD_ID:
        return await interaction.response.send_message("❌ Owner only.", ephemeral=True)
    if not interaction.guild:
        return await interaction.response.send_message("❌ Use this inside a server.", ephemeral=True)
    sid = str(interaction.guild.id)
    all_p = get_all_players()

    # No player -> list this server's overrides
    if not player:
        srv = get_server_overrides(sid)
        if not srv:
            return await interaction.response.send_message("ℹ️ No rating overrides on this server. Set one with `/srating player:<name> bat:.. bowl:..`.", ephemeral=True)
        base = {p["name"].lower(): p for p in all_p}
        lines = []
        for key, o in sorted(srv.items()):
            g = base.get(key, {})
            parts = []
            for f, lbl in (("bat", "bat"), ("bowl", "bowl"), ("role", "role"), ("archetype", "arch")):
                if f in o:
                    gv = g.get(f, "?")
                    parts.append(f"{lbl} {gv}→**{o[f]}**" if gv != o[f] else f"{lbl} **{o[f]}**")
            lines.append(f"• **{o.get('name', key)}** — {' · '.join(parts)}")
        embed = discord.Embed(title=f"🎚️ Server Rating Overrides — {interaction.guild.name}", description="\n".join(lines), color=discord.Color.teal())
        embed.set_footer(text="Owner-only · applies to all matches on THIS server · global DB unchanged")
        return await interaction.response.send_message(embed=embed)

    # Resolve the player against the global DB
    cur = next((p for p in all_p if p["name"].lower() == player.strip().lower()), None)
    if not cur:
        close = difflib.get_close_matches(player, [p["name"] for p in all_p], n=1, cutoff=0.6)
        cur = next((p for p in all_p if p["name"] == close[0]), None) if close else None
    if not cur:
        return await interaction.response.send_message(f"❌ Player '{player}' not found in the global database.", ephemeral=True)
    name = cur["name"]

    if reset:
        if reset_server_override(sid, name):
            return await interaction.response.send_message(f"✅ Removed the override for **{name}** on this server — back to global (bat {cur['bat']} · bowl {cur['bowl']}).")
        return await interaction.response.send_message(f"ℹ️ **{name}** has no override on this server.", ephemeral=True)

    fields = {}
    if bat is not None:
        if not (0 <= bat <= 99):
            return await interaction.response.send_message("❌ `bat` must be 0-99.", ephemeral=True)
        fields["bat"] = bat
    if bowl is not None:
        if not (0 <= bowl <= 99):
            return await interaction.response.send_message("❌ `bowl` must be 0-99.", ephemeral=True)
        fields["bowl"] = bowl
    if role is not None:
        fields["role"] = role.value
    if archetype is not None:
        fields["archetype"] = archetype.value
    if not fields:
        return await interaction.response.send_message("❌ Provide bat/bowl/role/archetype to set, `reset:True` to clear, or leave **player** blank to list.", ephemeral=True)

    set_server_override(sid, name, fields)
    eff = {**cur, **get_server_overrides(sid).get(name.lower(), {})}
    await interaction.response.send_message(
        f"✅ **{name}** overridden on **this server only**:\n"
        f"🏏 bat **{eff['bat']}** · 🎯 bowl **{eff['bowl']}** · {eff['role']} · {eff['archetype']}\n"
        f"-# Global DB unchanged (bat {cur['bat']} · bowl {cur['bowl']}). Applies to all matches on this server."
    )


_ROLE_OPTIONS = [
    discord.SelectOption(label="Batter",                value="Batter"),
    discord.SelectOption(label="Wicket-Keeper",         value="Batter_WK"),
    discord.SelectOption(label="Pace Bowler",           value="Bowler_Pace"),
    discord.SelectOption(label="Off-Spin Bowler",       value="Bowler_Spin_Off"),
    discord.SelectOption(label="Leg-Spin Bowler",       value="Bowler_Spin_Leg"),
    discord.SelectOption(label="Pace All-Rounder",      value="All-Rounder_Pace"),
    discord.SelectOption(label="Off-Spin All-Rounder",  value="All-Rounder_Spin_Off"),
    discord.SelectOption(label="Leg-Spin All-Rounder",  value="All-Rounder_Spin_Leg"),
]
_ARCH_OPTIONS = [
    discord.SelectOption(label="Aggressor", value="Aggressor"),
    discord.SelectOption(label="Anchor",    value="Anchor"),
    discord.SelectOption(label="Finisher",  value="Finisher"),
    discord.SelectOption(label="Standard",  value="Standard"),
    discord.SelectOption(label="Vaibhav", value="Vaibhav", description="Ultra-aggressive — 200+ SR, high wicket risk"),
]

class AddPlayerModal(discord.ui.Modal, title="Add New Player"):
    p_name = discord.ui.TextInput(label="Player Name", placeholder="e.g. Virat Kohli", max_length=50)
    bat    = discord.ui.TextInput(label="Bat Rating (0–100)", placeholder="e.g. 88", max_length=3)
    bowl   = discord.ui.TextInput(label="Bowl Rating (0–100)", placeholder="e.g. 45", max_length=3)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            bat_v  = int(self.bat.value)
            bowl_v = int(self.bowl.value)
        except ValueError:
            return await interaction.response.send_message("❌ Bat/Bowl must be whole numbers.", ephemeral=True)
        if not (0 <= bat_v <= 100 and 0 <= bowl_v <= 100):
            return await interaction.response.send_message("❌ Bat/Bowl must be 0–100.", ephemeral=True)
        name_v = self.p_name.value.strip()
        view = PlayerRoleView(interaction.user.id, name_v, bat_v, bowl_v, mode="add")
        await interaction.response.send_message(
            f"**{name_v}** — Bat `{bat_v}` | Bowl `{bowl_v}`\nNow pick Role and Archetype:",
            view=view, ephemeral=True
        )

class UpdatePlayerModal(discord.ui.Modal, title="Update Player"):
    p_name = discord.ui.TextInput(label="Player Name", max_length=50)
    bat    = discord.ui.TextInput(label="Bat Rating (0–100)", max_length=3)
    bowl   = discord.ui.TextInput(label="Bowl Rating (0–100)", max_length=3)

    def __init__(self, cur: dict):
        super().__init__()
        self._original  = cur["name"]
        self._cur_role  = cur["role"]
        self._cur_arch  = cur["archetype"]
        self.p_name.default = cur["name"]
        self.bat.default    = str(cur["bat"])
        self.bowl.default   = str(cur["bowl"])

    async def on_submit(self, interaction: discord.Interaction):
        try:
            bat_v  = int(self.bat.value)
            bowl_v = int(self.bowl.value)
        except ValueError:
            return await interaction.response.send_message("❌ Bat/Bowl must be whole numbers.", ephemeral=True)
        if not (0 <= bat_v <= 100 and 0 <= bowl_v <= 100):
            return await interaction.response.send_message("❌ Bat/Bowl must be 0–100.", ephemeral=True)
        name_v = self.p_name.value.strip()
        view = PlayerRoleView(interaction.user.id, name_v, bat_v, bowl_v, mode="update", original_name=self._original)
        view._role = self._cur_role
        view._arch = self._cur_arch
        await interaction.response.send_message(
            f"**{name_v}** — Bat `{bat_v}` | Bowl `{bowl_v}`\n"
            f"Change Role/Archetype if needed (current: **{self._cur_role}** | **{self._cur_arch}**):",
            view=view, ephemeral=True
        )

class PlayerRoleView(discord.ui.View):
    def __init__(self, author_id: int, name: str, bat: int, bowl: int, mode: str, original_name: str = None):
        super().__init__(timeout=120)
        self._author   = author_id
        self._name     = name
        self._bat      = bat
        self._bowl     = bowl
        self._mode     = mode
        self._original = original_name
        self._role     = None
        self._arch     = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self._author:
            await interaction.response.send_message("Not your form.", ephemeral=True)
            return False
        return True

    @discord.ui.select(placeholder="Select Role…", options=_ROLE_OPTIONS)
    async def role_select(self, interaction: discord.Interaction, select: discord.ui.Select):
        self._role = select.values[0]
        await interaction.response.defer()

    @discord.ui.select(placeholder="Select Archetype…", options=_ARCH_OPTIONS)
    async def arch_select(self, interaction: discord.Interaction, select: discord.ui.Select):
        self._arch = select.values[0]
        await interaction.response.defer()

    @discord.ui.button(label="Confirm →", style=discord.ButtonStyle.success, row=2)
    async def confirm_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self._role or not self._arch:
            return await interaction.response.send_message("⚠️ Select both Role and Archetype first.", ephemeral=True)
        if self._mode == "add":
            success = add_player({"name": self._name, "bat": self._bat, "bowl": self._bowl, "role": self._role, "archetype": self._arch})
            if not success:
                return await interaction.response.send_message(f"❌ `{self._name}` already exists in the database!", ephemeral=True)
            await interaction.response.send_message(f"✅ Added **{self._name}** to the database!")
            await log_db_update("Player Added", self._name, interaction.user, f"Bat: {self._bat} | Bowl: {self._bowl}\nRole: {self._role}\nArchetype: {self._arch}")
        else:
            all_p = get_all_players()
            if self._name.lower() != self._original.lower() and any(p["name"].lower() == self._name.lower() for p in all_p):
                return await interaction.response.send_message(f"❌ A player named `{self._name}` already exists!", ephemeral=True)
            update_player(self._original, {"name": self._name, "bat": self._bat, "bowl": self._bowl, "role": self._role, "archetype": self._arch})
            await interaction.response.send_message(f"✅ Updated **{self._name}** in the database!")
            await log_db_update("Player Updated", self._name, interaction.user, f"Bat: {self._bat} | Bowl: {self._bowl}\nRole: {self._role}\nArchetype: {self._arch}")
        self.stop()

@bot.tree.command(name="addplayer", description="[ADMIN] Add a new player to the Cloud DB.")
async def addplayer_slash(interaction: discord.Interaction):
    admins = get_auth_admins()
    if interaction.user.id != ADMIN_DISCORD_ID and str(interaction.user.id) not in admins:
        return await interaction.response.send_message("❌ Access Denied: Admin only.", ephemeral=True)
    await interaction.response.send_modal(AddPlayerModal())

@bot.tree.command(name="updateplayer", description="[ADMIN] Update an existing player in the Cloud DB.")
async def updateplayer_slash(interaction: discord.Interaction, name: str):
    admins = get_auth_admins()
    if interaction.user.id != ADMIN_DISCORD_ID and str(interaction.user.id) not in admins:
        return await interaction.response.send_message("❌ Access Denied: Admin only.", ephemeral=True)
    all_p = get_all_players()
    cur = next((p for p in all_p if p["name"].lower() == name.strip().lower()), None)
    if not cur:
        return await interaction.response.send_message(f"❌ `{name}` not found in the database.", ephemeral=True)
    await interaction.response.send_modal(UpdatePlayerModal(cur))

@bot.tree.command(name="my_tier", description="Check your current subscription tier and daily match limits.")
async def my_tier_cmd(interaction: discord.Interaction):
    server_id = str(interaction.guild.id) if interaction.guild else None
    u_tier, u_used, u_server_used, s_tier, s_used = get_tier_status(str(interaction.user.id), server_id)
    
    embed = discord.Embed(title="📊 Subscription Status", color=discord.Color.blue())
    
    # Format User Tier
    if u_tier == "Basic":
        u_limit, u_feat = "1/Day", "T20 & ODI Formats"
    elif u_tier == "Standard":
        u_limit, u_feat = "1/Day", "All Formats"
    elif u_tier == "Single":
        u_limit, u_feat = "1 (Consumable)", "All Formats"
    elif u_tier == "Server Pro":
        u_limit, u_feat = "0/Day", "Unlimited on Premium Servers"
    else:
        u_limit, u_feat = "0/Day", "Basic Access"
        
    u_val = f"**Tier:** {u_tier}\n**Personal Sims:** {u_used} / {u_limit}\n**Access:** {u_feat}"
    
    if u_tier not in ["Server Pro", "Standard"]:
        u_val += f"\n**Premium Server Limits:** {u_server_used} / 7 per day"
        
    embed.add_field(name="👤 Personal Profile", value=u_val, inline=False)
    
    # Format Server Tier
    if server_id:
        if s_tier == "Bronze":
            s_limit, s_feat = "10", "All Formats"
        elif s_tier in ["Silver", "Diamond"]:
            s_limit, s_feat = "Unlimited", "All Formats"
        elif s_tier == "Gold":
            s_limit, s_feat = "0", "Tournament Only"
        else:
            s_limit, s_feat = "0", "No active server tier."
            
        embed.add_field(
            name="🏟️ Server Tier (This Server)", 
            value=f"**Name:** {s_tier}\n**Daily Sims Used:** {s_used} / {s_limit}\n**Access:** {s_feat}", 
            inline=False
        )
        
    await interaction.response.send_message(embed=embed, ephemeral=True)

# ---- Admin database controls ----

async def log_db_update(action: str, player_name: str, user: discord.User, details: str):
    if not LOG_CHANNEL_ID: return
    channel = bot.get_channel(LOG_CHANNEL_ID)
    if channel:
        embed = discord.Embed(title=f"📋 Database Log: {action}", color=discord.Color.brand_green())
        embed.add_field(name="Player", value=player_name, inline=True)
        embed.add_field(name="Admin", value=user.mention, inline=True)
        embed.add_field(name="Details", value=f"```text\n{details}\n```", inline=False)
        embed.timestamp = discord.utils.utcnow()
        try:
            await channel.send(embed=embed)
        except:
            pass


# ---- OWNER SLASH COMMANDS (dropdown-based) ----

@bot.tree.command(name="set_user_tier", description="[OWNER] Assign a subscription tier to a user.")
@app_commands.choices(tier=[
    app_commands.Choice(name="Basic (1 Sim/Day | T20/ODI)", value="Basic"),
    app_commands.Choice(name="Standard (1 Sim/Day | All)", value="Standard"),
    app_commands.Choice(name="Single (1 Match Consumable)", value="Single"),
    app_commands.Choice(name="Server Pro (Unlimited on Silver/Diamond)", value="Server Pro"),
    app_commands.Choice(name="Career Beta (Career Mode Access)", value="Career Beta"),
    app_commands.Choice(name="None (Remove)", value="None")
])
@app_commands.describe(days="Optional: auto-remove the tier after this many days (0/blank = permanent).")
async def set_user_tier_cmd(interaction: discord.Interaction, user: discord.Member, tier: app_commands.Choice[str], days: int = 0):
    if interaction.user.id != ADMIN_DISCORD_ID:
        return await interaction.response.send_message("❌ Owner only.", ephemeral=True)
    msg = update_user_tier(str(user.id), tier.value, tier.name, user.mention, days=days)
    await interaction.response.send_message(msg, ephemeral=True)

@bot.tree.command(name="set_server_tier", description="[OWNER] Assign a subscription tier to a server.")
@app_commands.choices(tier=[
    app_commands.Choice(name="Bronze (10 Sims/Day | All)", value="Bronze"),
    app_commands.Choice(name="Silver (Unlimited | All)", value="Silver"),
    app_commands.Choice(name="Gold (Tournament Only)", value="Gold"),
    app_commands.Choice(name="Diamond (Unlimited + Tournament)", value="Diamond"),
    app_commands.Choice(name="None (Remove)", value="None")
])
@app_commands.describe(days="Optional: auto-remove the tier after this many days (0/blank = permanent).")
async def set_server_tier_cmd(interaction: discord.Interaction, server_id: str, tier: app_commands.Choice[str], days: int = 0):
    if interaction.user.id != ADMIN_DISCORD_ID:
        return await interaction.response.send_message("❌ Owner only.", ephemeral=True)
    msg = update_server_tier(server_id, tier.value, tier.name, days=days)
    await interaction.response.send_message(msg, ephemeral=True)

# ---- Prefix commands & cog ----

def to_bool(value: str) -> bool:
    return value.lower() in ['true', '1', 't', 'y', 'yes']

class CustomHelpCommand(commands.HelpCommand):
    def get_command_signature(self, command):
        usage = command.help.split("Usage: ")[1] if command.help and "Usage: " in command.help else command.name
        return f'`{self.context.prefix}{usage}`'

    async def send_bot_help(self, mapping):
        is_owner = self.context.author.id == ADMIN_DISCORD_ID
        is_admin = is_owner or str(self.context.author.id) in get_auth_admins()
        view = HelpNavigator(self.context.author.id, is_admin, is_owner)
        await self.get_destination().send(embed=_help_home_embed(), view=view)

    async def send_command_help(self, command):
        embed = discord.Embed(title=f"Help: `{command.name}`", color=discord.Color.green())
        help_text = command.help or "No description provided."
        usage = help_text.splitlines()[1] if help_text and "Usage:" in help_text else f"{self.context.prefix}{command.name} [arguments...]"
        embed.add_field(name="Description", value=help_text.splitlines()[0], inline=False)
        embed.add_field(name="Usage", value=f"`{usage}`", inline=False)
        await self.get_destination().send(embed=embed)

    async def send_group_help(self, group):
        embed = discord.Embed(title=f"Help: `{group.name}`", color=discord.Color.gold())
        embed.description = group.help or "No description provided."
        
        sub_cmds = []
        for cmd in sorted(group.commands, key=lambda x: x.name):
            usage = cmd.help.split("Usage: ")[1] if cmd.help and "Usage: " in cmd.help else f"{group.name} {cmd.name}"
            sub_cmds.append(f"**`{self.context.prefix}{usage}`**\n{cmd.help.splitlines()[0]}")
        
        if sub_cmds:
            embed.add_field(name="Subcommands", value="\n".join(sub_cmds), inline=False)
        await self.get_destination().send(embed=embed)


_AI_TEAM_NAMES = ["The Machines", "Cyber XI", "Bot Brigade", "Neural Knights", "Silicon Stars", "Algo Allstars"]

class DraftModeView(discord.ui.View):
    """Host picks the player pool tier before a draft. OVR caps are never shown."""
    def __init__(self, host_id):
        super().__init__(timeout=60)
        self.host_id = host_id
        self.mode = None

    async def interaction_check(self, interaction):
        if interaction.user.id != self.host_id:
            await interaction.response.send_message("Only the host picks the pool.", ephemeral=True)
            return False
        return True

    async def _choose(self, interaction, mode, label):
        self.mode = mode
        await interaction.response.edit_message(content=f"🎚️ Pool locked: **{label}**.", view=None)
        self.stop()

    @discord.ui.button(label="Legends", style=discord.ButtonStyle.success, emoji="👑")
    async def legends(self, interaction, button):
        await self._choose(interaction, "legends", "Legends")

    @discord.ui.button(label="Greats", style=discord.ButtonStyle.primary, emoji="⭐")
    async def greats(self, interaction, button):
        await self._choose(interaction, "greats", "Greats")

    @discord.ui.button(label="Youngsters", style=discord.ButtonStyle.secondary, emoji="🌱")
    async def youngsters(self, interaction, button):
        await self._choose(interaction, "youngsters", "Youngsters")


async def _record_draft_result(channel, match):
    """On a draft match's finish, record the win - PvP to the leaderboard, vs-AI separately."""
    inn1, inn2 = match.innings1, match.innings2
    target = getattr(match, "target", inn1.total_runs + 1)
    if getattr(match, "tiebreak_winner_name", None):
        win_name = match.tiebreak_winner_name
    elif inn2.total_runs >= target:
        win_name = inn2.batting_team["name"]
    elif inn2.total_runs == target - 1:
        win_name = None   # tie - but tournament/draft ties go to a Super Over, so rare
    else:
        win_name = inn1.batting_team["name"]
    if win_name is None:
        return

    host_id   = match.draft_host_id
    host_name = match.draft_host_name
    opp_id    = match.draft_opp_id        # None == vs AI
    opp_name  = match.draft_opp_name
    host_won  = (win_name == match.draft_host_team)

    if opp_id is None:
        record_draft_ai(host_id, host_name, host_won)
        verdict = (f"🏆 **{host_name}** beat the AI — vs-AI record updated."
                   if host_won else f"🤖 The **AI** won — {host_name}'s vs-AI record updated.")
    else:
        if host_won:
            record_draft_pvp(host_id, host_name, opp_id, opp_name); winner = host_name
        else:
            record_draft_pvp(opp_id, opp_name, host_id, host_name); winner = opp_name
        verdict = f"🏆 Draft win recorded for **{winner}**!"
    try:
        await channel.send(f"📋 **Draft Match Complete** — {verdict}\n-# See the table with `cvd lb`.")
    except Exception:
        pass


class CSVSyncConfirmView(discord.ui.View):
    """Owner confirmation for `cv sync_csv` - previews the new players found in the
    CSV and lets the owner toggle any of them off before committing the import."""
    PAGE = 25   # Discord select menus cap at 25 options

    def __init__(self, ctx, new_players):
        super().__init__(timeout=180)
        self.ctx = ctx
        self.players = new_players    # CSV order
        self.skipped = set()          # names toggled off the import
        self.page = 0
        self.message = None
        self._done = False
        self.update_ui()

    @property
    def pages(self):
        return (len(self.players) - 1) // self.PAGE + 1

    def _page_players(self):
        start = self.page * self.PAGE
        return self.players[start:start + self.PAGE]

    def build_embed(self):
        adding = len(self.players) - len(self.skipped)
        lines = []
        for p in self._page_players():
            mark = "⛔" if p["name"] in self.skipped else "✅"
            lines.append(f"{mark} **{p['name']}** — {p['role']} · Bat {p['bat']} / Bowl {p['bowl']}")
        e = discord.Embed(title="📥 CSV Sync — Confirm Import",
                          description="\n".join(lines),
                          color=discord.Color.orange())
        footer = f"Adding {adding} of {len(self.players)} new players · pick from the menu to toggle off/on"
        if self.pages > 1:
            footer += f" · Page {self.page + 1}/{self.pages}"
        e.set_footer(text=footer)
        return e

    def update_ui(self):
        self.clear_items()
        opts = [discord.SelectOption(label=p["name"][:100],
                                     description=f"{p['role']} · Bat {p['bat']} / Bowl {p['bowl']}"[:100],
                                     value=p["name"],
                                     emoji="⛔" if p["name"] in self.skipped else "✅")
                for p in self._page_players()]
        sel = discord.ui.Select(placeholder="Pick players to toggle skip/add…",
                                options=opts, min_values=1, max_values=len(opts))
        sel.callback = self.toggle_cb
        self.add_item(sel)
        if self.pages > 1:
            prev = discord.ui.Button(label="◀", style=discord.ButtonStyle.secondary, disabled=self.page == 0)
            prev.callback = self.prev_cb
            self.add_item(prev)
            nxt = discord.ui.Button(label="▶", style=discord.ButtonStyle.secondary, disabled=self.page >= self.pages - 1)
            nxt.callback = self.next_cb
            self.add_item(nxt)
        adding = len(self.players) - len(self.skipped)
        conf = discord.ui.Button(label=f"Add {adding} Player{'s' if adding != 1 else ''}",
                                 style=discord.ButtonStyle.success, disabled=adding == 0)
        conf.callback = self.confirm_cb
        self.add_item(conf)
        cancel = discord.ui.Button(label="Cancel", style=discord.ButtonStyle.danger)
        cancel.callback = self.cancel_cb
        self.add_item(cancel)

    async def interaction_check(self, interaction: discord.Interaction):
        if interaction.user.id != ADMIN_DISCORD_ID:
            await interaction.response.send_message("❌ Owner only.", ephemeral=True)
            return False
        return True

    async def _refresh(self, interaction):
        self.update_ui()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    async def toggle_cb(self, interaction: discord.Interaction):
        for name in interaction.data["values"]:
            if name in self.skipped:
                self.skipped.discard(name)
            else:
                self.skipped.add(name)
        await self._refresh(interaction)

    async def prev_cb(self, interaction: discord.Interaction):
        self.page = max(0, self.page - 1)
        await self._refresh(interaction)

    async def next_cb(self, interaction: discord.Interaction):
        self.page = min(self.pages - 1, self.page + 1)
        await self._refresh(interaction)

    async def confirm_cb(self, interaction: discord.Interaction):
        if self._done:
            return
        self._done = True
        self.stop()
        chosen = [p for p in self.players if p["name"] not in self.skipped]
        added = add_players_bulk(chosen)
        skipped_n = len(self.players) - len(chosen)
        msg = f"✅ Sync complete! Added **{added}** new players."
        if skipped_n:
            msg += f" Skipped **{skipped_n}** (deselected)."
        await interaction.response.edit_message(content=msg, embed=None, view=None)
        if added > 0:
            await log_db_update("CSV Sync", "Batch Import", self.ctx.author,
                                f"Added {added} new players from CSV ({skipped_n} deselected).")

    async def cancel_cb(self, interaction: discord.Interaction):
        if self._done:
            return
        self._done = True
        self.stop()
        await interaction.response.edit_message(content="❌ CSV sync cancelled — no players added.", embed=None, view=None)

    async def on_timeout(self):
        if self._done:
            return
        self._done = True
        try:
            await self.message.edit(content="⏳ CSV sync confirmation timed out — no players added.", embed=None, view=None)
        except Exception:
            pass


# ---- Global stats leaderboards (cv gs) ----

# key -> (label, emoji, qualification note shown in the footer)
GS_BOARDS = {
    "runs":       ("Most Runs", "🏏", None),
    "wickets":    ("Most Wickets", "🎯", None),
    "sixes":      ("Most Sixes", "💥", None),
    "fours":      ("Most Fours", "🏹", None),
    "hundreds":   ("Most 100s", "💯", None),
    "fifties":    ("Most 50s", "🎖️", None),
    "hs":         ("Highest Score", "🚀", None),
    "bat_avg":    ("Best Batting Average", "📈", "min 30 balls faced + 1 dismissal"),
    "sr":         ("Best Strike Rate", "⚡", "min 30 balls faced"),
    "econ":       ("Best Economy", "🪙", "min 5 overs bowled"),
    "bowl_avg":   ("Best Bowling Average", "📉", "min 3 wickets"),
    "five_hauls": ("Most 5-Wicket Hauls", "🖐️", None),
    "ducks":      ("Most Ducks", "🦆", None),
}

def _gs_board_rows(cat_key, top=10):
    """(sort_value, name, display) rows for one leaderboard, best first. Rate boards
    carry a volume hint in the display so a 3-ball cameo is readable next to a career."""
    rows = []
    for name, t in gstats.combined_totals().items():
        if cat_key in ("runs", "wickets", "sixes", "fours", "hundreds", "fifties", "five_hauls", "ducks"):
            if t[cat_key] > 0:
                rows.append((t[cat_key], name, str(t[cat_key])))
        elif cat_key == "hs":
            if t["hs"] > 0:
                # +0.5 so 105* outranks 105 in the sort, matching the HS convention
                rows.append((t["hs"] + (0.5 if t["hs_not_out"] else 0), name,
                             f"{t['hs']}{'*' if t['hs_not_out'] else ''}"))
        elif cat_key == "bat_avg":
            if t["balls"] >= 30 and t["outs"] > 0:
                v = t["runs"] / t["outs"]
                rows.append((v, name, f"{v:.1f} ({t['runs']} runs)"))
        elif cat_key == "sr":
            if t["balls"] >= 30:
                v = t["runs"] / t["balls"] * 100
                rows.append((v, name, f"{v:.1f} ({t['balls']} balls)"))
        elif cat_key == "econ":
            if t["balls_bowled"] >= 30:
                v = t["runs_conceded"] / t["balls_bowled"] * 6
                rows.append((-v, name, f"{v:.2f} ({t['balls_bowled'] // 6}.{t['balls_bowled'] % 6} ov)"))
        elif cat_key == "bowl_avg":
            if t["wickets"] >= 3:
                v = t["runs_conceded"] / t["wickets"]
                rows.append((-v, name, f"{v:.1f} ({t['wickets']} wkts)"))
    rows.sort(key=lambda r: r[0], reverse=True)
    return rows[:top]

def build_gs_board_embed(cat_key):
    label, emoji, note = GS_BOARDS[cat_key]
    rows = _gs_board_rows(cat_key)
    desc = "\n".join(f"`{i + 1:>2}.` **{n}** — {disp}" for i, (_, n, disp) in enumerate(rows)) \
        or "*Nobody qualifies for this board yet.*"
    embed = discord.Embed(title=f"🌍 Global Leaderboard — {emoji} {label}",
                          description=desc, color=discord.Color.gold())
    foot = f"All formats combined · {gstats.player_count()} players tracked"
    if note:
        foot += f" · {note}"
    embed.set_footer(text=foot + " · cv gs <name> for a player card")
    return embed

_GS_FMT_LABELS = {"t20": "T20", "odi": "ODI", "test": "TEST", "custom": "CUSTOM OVERS"}

def build_gs_player_embed(name, p, fmt):
    """Two-column scorecard-sheet embed for one player in one format."""
    f = p[fmt]
    g = lambda k: f.get(k, 0)

    bat_avg = f"{g('runs') / g('outs'):.2f}" if g("outs") else ("—" if not g("runs") else f"{g('runs')}*")
    bat_sr = f"{g('runs') / g('balls') * 100:.1f}" if g("balls") else "0"
    hs = "—"
    if g("bat_innings"):
        hs = f"{g('hs')}{'*' if f.get('hs_not_out') else ''}"
        if g("hs_balls"):
            hs += f"({g('hs_balls')})"
    bat_rows = [
        ("Inns", g("bat_innings")), ("Runs", g("runs")),
        ("50s", g("fifties")), ("100s", g("hundreds")),
        ("4/6", f"{g('fours')}/{g('sixes')}"), ("Avg", bat_avg),
        ("SR", bat_sr), ("Ducks", g("ducks")), ("HS", hs),
    ]

    bowl_avg = f"{g('runs_conceded') / g('wickets'):.2f}" if g("wickets") else "0"
    bowl_sr = f"{g('balls_bowled') / g('wickets'):.1f}" if g("wickets") else "0"
    econ = f"{g('runs_conceded') / g('balls_bowled') * 6:.1f}" if g("balls_bowled") else "0"
    bbf = f"{g('best_wkts')}/{g('best_runs')}" if f.get("best_runs", -1) >= 0 else "—"
    bowl_rows = [
        ("Inns", g("bowl_innings")), ("Wickets", g("wickets")),
        ("3-Fers", g("three_hauls")), ("5-Fers", g("five_hauls")),
        ("Hattricks", g("hattricks")), ("Avg", bowl_avg),
        ("Economy", econ), ("SR", bowl_sr), ("BBF", bbf),
    ]

    left = ["Batting"] + [f"{k}: {v}" for k, v in bat_rows]
    right = ["Bowling"] + [f"{k}: {v}" for k, v in bowl_rows]
    width = max(len(s) for s in left) + 4
    sheet = "\n".join(f"{l:<{width}}{r}".rstrip() for l, r in zip(left, right))

    embed = discord.Embed(
        title=f"🌍 Global Stats — {name}",
        description=f"**{_GS_FMT_LABELS[fmt]}** · {f.get('matches', 0)} matches\n```\n{sheet}\n```",
        color=discord.Color.blurple(),
    )
    embed.set_footer(text="Matches count only games where they batted or bowled · cv gs for leaderboards")
    return embed

class GlobalPlayerCardView(discord.ui.View):
    """Format pager for the player card - one page per format the player has stats in."""
    def __init__(self, name, p, active):
        super().__init__(timeout=600)
        self.name, self.p = name, p
        for fmt in (k for k in gstats.FORMATS if k in p):
            self.add_item(self._fmt_button(fmt, fmt == active))

    def _fmt_button(self, fmt, is_active):
        btn = discord.ui.Button(
            label=_GS_FMT_LABELS[fmt],
            style=discord.ButtonStyle.primary if is_active else discord.ButtonStyle.secondary,
            disabled=is_active,
        )
        async def _flip(interaction: discord.Interaction, _f=fmt):
            await interaction.response.edit_message(
                embed=build_gs_player_embed(self.name, self.p, _f),
                view=GlobalPlayerCardView(self.name, self.p, _f),
            )
        btn.callback = _flip
        return btn


class GlobalBoardView(discord.ui.View):
    """cv gs leaderboard with a dropdown to flip between stat categories."""
    def __init__(self):
        super().__init__(timeout=600)
        self.select = discord.ui.Select(
            placeholder="📊 Pick a leaderboard…",
            options=[discord.SelectOption(label=lbl, value=key, emoji=em, default=(key == "runs"))
                     for key, (lbl, em, _n) in GS_BOARDS.items()],
        )
        self.select.callback = self._pick
        self.add_item(self.select)

    async def _pick(self, interaction: discord.Interaction):
        cat = self.select.values[0]
        for o in self.select.options:   # keep the picked entry shown in the closed dropdown
            o.default = o.value == cat
        await interaction.response.edit_message(embed=build_gs_board_embed(cat), view=self)


class PrefixCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.bot.help_command = CustomHelpCommand()
        self.bot.help_command.cog = self

    @commands.Cog.listener()
    async def on_command_error(self, ctx, error):
        if isinstance(error, commands.CommandNotFound):
            return
        
        if isinstance(error, (commands.MissingRequiredArgument, commands.BadArgument, commands.TooManyArguments)):
            usage = ctx.command.help.split("Usage: ")[1] if ctx.command.help and "Usage: " in ctx.command.help else f"{ctx.command.name} [arguments...]"
            await ctx.send(f"❌ **Invalid Usage!** {ctx.author.mention}, the correct format is:\n`{ctx.prefix}{usage}`")
        else:
            import traceback as _tb
            orig = getattr(error, "original", error)
            print(f"An error occurred in a prefix command '{ctx.command}': {orig!r}")
            _tb.print_exception(type(orig), orig, orig.__traceback__)
            # Surface the real error to the owner/admins to speed up debugging.
            try:
                _is_admin = (ctx.author.id == ADMIN_DISCORD_ID
                             or (ctx.guild and ctx.author.guild_permissions.administrator)
                             or str(ctx.author.id) in get_auth_admins())
            except Exception:
                _is_admin = False
            if _is_admin:
                await ctx.send(f"⚠️ `{type(orig).__name__}`: {str(orig)[:1800]}")
            else:
                await ctx.send("An unexpected error occurred while running that command.")

    @commands.command(name="match", aliases=["m"], help="Start a new Cricket Match simulation.\nUsage: match [@opponent]")
    async def match(self, ctx, opponent: discord.Member = None):
        if is_channel_restricted(str(ctx.channel.id)):
            return await ctx.send("❌ Matches are **disabled** in this channel.")
        
        allowed, reason = await asyncio.to_thread(check_potential_quota, str(ctx.author.id), str(ctx.guild.id) if ctx.guild else None, str(ADMIN_DISCORD_ID))
        if not allowed: return await ctx.send(reason)

        if ctx.channel.id in active_games or ctx.channel.id in active_setups:
            return await ctx.send("❌ A match or setup is already in progress. Use `cv endmatch` to cancel it.")
        if opponent and opponent.bot:
            return await ctx.send("❌ Cannot challenge a bot user.")

        state = MatchSetupState(ctx.author, opponent, ctx.author.id, opponent.id if opponent else None)
        active_setups[ctx.channel.id] = ("format_selection", state)
        setup_states[ctx.channel.id] = state
        opp_str = opponent.mention if opponent else "🤖 AI"
        await ctx.send(f"🏏 **Match Setup**\n**Host:** {ctx.author.mention}\n**Opponent:** {opp_str}\n\nStep 1: Select Format below:", view=FormatSelectView(state, ctx.channel))

    @commands.command(name="verbose", aliases=["vb"], help="During a Ball-by-Ball broadcast: finish the current over ball-by-ball, then sim the REST of the match in verbose (one scoreboard card per over). Works in tournament matches too — stats and the result are recorded as normal.\nUsage: verbose")
    async def verbose(self, ctx):
        match = active_games.get(ctx.channel.id)
        if not match or not getattr(match, '_bbb_active', False):
            return await ctx.send("⚠️ No Ball-by-Ball broadcast is running in this channel — `cv verbose` only works while a 🎬 Ball-by-Ball sim is live.")
        if getattr(match, '_switch_to_verbose', False):
            return await ctx.send("⏳ Already queued — the verbose sim takes over as soon as this over ends.")
        match._switch_to_verbose = True
        await ctx.send("📋 **Got it!** Finishing this over ball-by-ball, then simming the rest of the match in verbose.")

    @commands.command(name="endmatch", aliases=["em"], help="Force cancel the current match or setup in this channel.\nUsage: endmatch")
    async def endmatch(self, ctx):
        cleared = _force_end_channel(ctx.channel.id)
        if cleared:
            await ctx.send("🛑 **Match and setup forcefully terminated.** Memory cleared.")
        else:
            await ctx.send("⚠️ There is no active match or setup running in this channel.")

    # ---- Global player stats (local json - see core/global_stats.py) ----

    @commands.command(name="globalstats", aliases=["gstats", "gs"],
                      help="Global career stats across every real match (testplayer excluded), split by T20/ODI/Test/Custom.\nUsage: globalstats <player> — or no name for the all-format leaderboards.")
    async def globalstats(self, ctx, *, player_name: str = None):
        if not gstats.player_count():
            return await ctx.send("📭 No global stats recorded yet — finish some matches first!")

        if not player_name:
            # Most Runs by default; the dropdown flips between every other board.
            return await ctx.send(embed=build_gs_board_embed("runs"), view=GlobalBoardView())

        names = gstats.player_names()
        target = next((n for n in names if n.lower() == player_name.lower()), None)
        if not target:
            close = difflib.get_close_matches(player_name, names, n=1, cutoff=0.6)
            if not close:
                return await ctx.send(f"❌ No global stats for **{player_name}** yet.")
            target = close[0]

        p = gstats.player_stats(target)
        first = next((fmt for fmt in gstats.FORMATS if fmt in p), None)
        if not first:
            return await ctx.send(f"❌ No global stats for **{target}** yet.")
        await ctx.send(embed=build_gs_player_embed(target, p, first),
                       view=GlobalPlayerCardView(target, p, first))

    @commands.command(name="exportstats", aliases=["exps"],
                      help="[OWNER] DM yourself the global stats json backup.\nUsage: exportstats")
    async def exportstats(self, ctx):
        if ctx.author.id != ADMIN_DISCORD_ID:
            return await ctx.send("❌ Owner only.")
        if not gstats.player_count():
            return await ctx.send("📭 Nothing to export yet.")
        path = gstats.flush_to_disk()
        try:
            await ctx.author.send(f"📦 Global stats backup — **{gstats.player_count()}** players:",
                                  file=discord.File(path, filename="global_stats.json"))
        except discord.Forbidden:
            return await ctx.send("❌ Couldn't DM you — check your DM privacy settings.")
        gstats.clear_dirty()
        if ctx.guild:
            await ctx.send("📬 Backup sent to your DMs.")

    @commands.command(name="importstats", aliases=["imps"],
                      help="[OWNER] Restore global stats from an exported json — REPLACES everything currently tracked.\nUsage: importstats (attach global_stats.json to the message)")
    async def importstats(self, ctx):
        if ctx.author.id != ADMIN_DISCORD_ID:
            return await ctx.send("❌ Owner only.")
        if not ctx.message.attachments:
            return await ctx.send("❌ Attach the exported `global_stats.json` to the command message.")
        raw = await ctx.message.attachments[0].read()
        ok, msg = gstats.import_raw(raw)
        await ctx.send(f"✅ {msg}." if ok else f"❌ {msg}.")

    @commands.command(name="testplayer", aliases=["tp", "playertest", "test_player"],
                      help="Test up to 22 players in a live match. 1-11: they join a balanced XI vs a Weak/Balanced/Tough net side. 12-22: split into two even Test XIs that play each other. Engine picks the batting order — add a number after a name (\"Virat Kohli 3\") to pin their spot.\nUsage: testplayer")
    async def testplayer(self, ctx):
        if is_channel_restricted(str(ctx.channel.id)):
            return await ctx.send("❌ Matches are **disabled** in this channel.")
        if ctx.channel.id in active_games or ctx.channel.id in active_setups:
            return await ctx.send("❌ A match or setup is already in progress. Use `cv endmatch` to cancel it.")

        state = MatchSetupState(ctx.author, None, ctx.author.id, None)
        state.impact_player = False
        state.is_player_test = True
        active_setups[ctx.channel.id] = ("testplayer_setup", state)
        setup_states[ctx.channel.id] = state

        def _alive():   # endmatch clears the dicts - abort quietly if that happened
            return active_setups.get(ctx.channel.id, (None, None))[1] is state

        handed_off = False

        def _cleanup():
            if _alive():
                del active_setups[ctx.channel.id]
                # On hand-off, setup_states must SURVIVE until start_match pops it
                # endmatch flags state.cancelled through it, and cv order edits it.
                if not handed_off:
                    setup_states.pop(ctx.channel.id, None)

        def chk(m):
            return (m.author.id == ctx.author.id and m.channel.id == ctx.channel.id
                    and m.content.strip() and not m.content.lower().startswith("cv"))

        async def _ask(prompt):
            await ctx.send(prompt)
            try:
                msg = await self.bot.wait_for("message", timeout=90.0, check=chk)
            except asyncio.TimeoutError:
                return None
            return msg.content.strip() if _alive() else None

        try:
            # 1: collect ALL the names in one message (real DB ratings, overrides applied)
            raw = await _ask("🧪 **Player Test** — paste **all the players you want to test** in one message\n"
                             "*(one per line or comma-separated · **1-11** = they join one XI vs a net side · "
                             "**12-22** = split into two Test XIs that play each other)*\n"
                             "-# ✏️ A number **pins the batting spot** — `Virat Kohli 3` or `3. Virat Kohli` bats at #3. "
                             "No number = the engine picks the order.")
            if raw is None:
                return await ctx.send("⏳ **Cancelled** — no reply.")

            pool = apply_server_overrides(get_all_players(), str(ctx.guild.id) if ctx.guild else None)
            by_name = {p["name"].lower(): p for p in pool}
            tested = None
            for attempt in range(3):
                names = [s.strip() for line in raw.splitlines() for s in line.split(",") if s.strip()]
                if not 1 <= len(names) <= 22:
                    raw = await _ask(f"❌ That's **{len(names)}** names — I need **1-22**. Paste the list again:")
                    if raw is None:
                        return await ctx.send("⏳ **Cancelled** — no reply.")
                    continue
                found, missing, seen, pins = [], [], set(), {}
                for nm in names:
                    # a 1-11 pins the batting position - both "Virat Kohli 3" and
                    # "3. Virat Kohli" work (so a pasted numbered list IS the order)
                    pin = None
                    pm = re.match(r"^(\d{1,2})\s*[.):\-]?\s+(.*\S)$", nm)
                    if pm and 1 <= int(pm.group(1)) <= 11:
                        nm, pin = pm.group(2), int(pm.group(1))
                    else:
                        pm = re.match(r"^(.*\S)\s+(\d{1,2})$", nm)
                        if pm and 1 <= int(pm.group(2)) <= 11:
                            nm, pin = pm.group(1), int(pm.group(2))
                    cand = by_name.get(nm.lower())
                    if not cand:
                        close = difflib.get_close_matches(nm.lower(), list(by_name.keys()), n=1, cutoff=0.6)
                        cand = by_name.get(close[0]) if close else None
                    if not cand:
                        missing.append(nm)
                    elif cand["name"] not in seen:
                        seen.add(cand["name"])
                        found.append(dict(cand))
                        if pin:
                            pins[cand["name"]] = pin
                if missing:
                    err = ""
                    if found:
                        err += f"✅ **Found:** {', '.join(p['name'] for p in found)}\n"
                    err += (f"❌ **Not in the DB:** {', '.join(missing)}\n\n"
                            "Check the spellings and paste the **full list** again:")
                    raw = await _ask(err)
                    if raw is None:
                        return await ctx.send("⏳ **Cancelled** — no reply.")
                    continue
                tested = found
                break
            if tested is None:
                return await ctx.send("❌ **Test cancelled** — couldn't resolve the list.")
            await ctx.send(f"✅ **Testing {len(tested)}:** " +
                           ", ".join(f"{p['name']} ({_role_short(p)}" +
                                     (f" · pinned #{pins[p['name']]})" if p["name"] in pins else ")")
                                     for p in tested))

            # 3: difficulty of everyone else, relative to the tested players
            avg_ovr = sum(_player_overall(p) for p in tested) / len(tested)
            view = _TestDifficultyView(ctx.author.id)
            await ctx.send("⚔️ **What type of other players do you need?**\n"
                           "🟢 **Weak** — lower OVR than your test players\n"
                           "🟡 **Balanced** — nearly the same\n"
                           "🔴 **Tough** — higher OVR", view=view)
            await view.wait()
            if view.value is None or not _alive():
                return await ctx.send("⏳ **Cancelled** — no difficulty chosen.")
            target = max(40.0, min(95.0, avg_ovr + _TEST_DIFFICULTY[view.value]))

            # 4: format, then hand off to the NORMAL pitch/weather -> toss -> match flow
            fview = _TestFormatView(ctx.author.id)
            await ctx.send("🏏 **Format?**", view=fview)
            await fview.wait()
            if fview.value is None or not _alive():
                return await ctx.send("⏳ **Cancelled** — no format chosen.")
            state.format_overs = fview.value

            # Batting order: the ENGINE decides (_batting_order: rating + archetype +
            # role) - except players PINNED with a trailing number, who bat exactly there.
            if len(tested) <= 11:
                state.t1_name = "Test XI"
                state.t1_roster = _apply_order_pins(_batting_order(build_test_home_xi(tested, target)), pins)
                state.t2_name = "Net Opposition"
                state.t2_roster = _batting_order(build_test_away_xi(target))
            else:
                # 12-22 tested -> snake-split by OVR into two even sides that play EACH
                # OTHER; fillers (at the chosen difficulty) complete each XI.
                ranked = sorted(tested, key=_player_overall, reverse=True)
                side_a, side_b = [], []
                for i, p in enumerate(ranked):
                    (side_a if i % 4 in (0, 3) else side_b).append(p)
                state.t1_name = "Test XI A"
                state.t1_roster = _apply_order_pins(_batting_order(build_test_home_xi(side_a, target, prefix="A Net")), pins)
                state.t2_name = "Test XI B"
                state.t2_roster = _apply_order_pins(_batting_order(build_test_home_xi(side_b, target, prefix="B Net")), pins)
            state.home_team_id = ctx.author.id

            handed_off = True
            _cleanup()   # views/toss take over from here, exactly like a normal match
            if len(tested) <= 11:
                vs_line = f"**{state.t1_name}** (your picks + balanced fillers)  vs  **{state.t2_name}**"
            else:
                a_names = ", ".join(p["name"] for p in state.t1_roster if any(t["name"] == p["name"] for t in tested))
                b_names = ", ".join(p["name"] for p in state.t2_roster if any(t["name"] == p["name"] for t in tested))
                vs_line = f"**{state.t1_name}:** {a_names}\n**{state.t2_name}:** {b_names}"
            await ctx.send(f"🧪 **Test ready!**  ·  Fillers/opposition: **{view.value.title()}**\n{vs_line}\n"
                           f"-# ✏️ Tip: `Virat Kohli 3` (or `3. Virat Kohli`) in the list pins him to bat #3 — no number = engine decides.")
            await proceed_to_conditions(ctx.channel, state)
            return
        finally:
            _cleanup()

    # DRAFT MODE (blind, knowledge-based)
    @commands.group(name="draft", invoke_without_command=True,
                    help="Blind player draft → interactive match. Answer by typing a name.\nUsage: draft [@opponent]   (no opponent = vs AI)   ·   draft lb")
    async def draft(self, ctx, opponent: discord.Member = None):
        if not ctx.guild:
            return await ctx.send("❌ Use draft inside a server.")
        if ctx.channel.id in active_games or ctx.channel.id in active_setups or ctx.channel.id in active_drafts:
            return await ctx.send("❌ A match or draft is already running here. Use `cv endmatch` first.")
        if opponent:
            if opponent.bot:
                return await ctx.send("❌ Can't draft against a bot account. Leave blank to play **vs AI**.")
            if opponent.id == ctx.author.id:
                return await ctx.send("❌ You can't draft against yourself.")
        await self._run_draft(ctx, ctx.author, opponent)

    @draft.command(name="lb", aliases=["leaderboard", "wins"], help="Draft win leaderboard (vs-AI tracked separately).\nUsage: cvd lb")
    async def draft_lb(self, ctx):
        stats = get_draft_stats()
        if not stats:
            return await ctx.send("📋 No drafts played yet. Start one with `cvd @opponent` (or `cvd` vs AI).")
        rows = sorted(stats.items(), key=lambda kv: (kv[1].get("wins", 0), -kv[1].get("losses", 0)), reverse=True)
        rows = [r for r in rows if (r[1].get("wins", 0) or r[1].get("losses", 0))]   # PvP players only
        lines = []
        for i, (uid, r) in enumerate(rows[:15], 1):
            w, l = r.get("wins", 0), r.get("losses", 0)
            tot = w + l
            pct = f"{(w / tot * 100):.0f}%" if tot else "—"
            medal = {1: "🥇", 2: "🥈", 3: "🥉"}.get(i, f"`{i:>2}`")
            lines.append(f"{medal} **{r.get('name', '?')}** — {w}W / {l}L · {pct}")
        embed = discord.Embed(title="🏆 Draft Leaderboard — PvP Wins",
                              description="\n".join(lines) or "No PvP drafts yet.", color=discord.Color.gold())
        me = stats.get(str(ctx.author.id))
        if me and (me.get("ai_wins", 0) or me.get("ai_losses", 0)):
            embed.add_field(name="Your vs-AI record (separate)",
                            value=f"{me.get('ai_wins', 0)}W / {me.get('ai_losses', 0)}L", inline=False)
        embed.set_footer(text="Only 1v1 drafts count toward the leaderboard · vs-AI is tracked separately")
        await ctx.send(embed=embed)

    async def _draft_get_pick(self, channel, uid, uname, question, pool, taken, ri, tag, base_pool, mode):
        """Collect one human pick: 3 tries, then a 78-OVR net filler for the slot."""
        mode_label = dm.TIER_LABELS.get(mode, mode.title())
        for attempt in range(3):
            left = 3 - attempt
            await channel.send(f"<@{uid}> — type a player's name  *({left} {'try' if left == 1 else 'tries'} left)*")
            def check(m):
                return (m.author.id == uid and m.channel.id == channel.id and m.content.strip()
                        and not m.content.lower().startswith("cv"))   # let cv-commands (endmatch) through
            try:
                msg = await self.bot.wait_for("message", timeout=60.0, check=check)
            except asyncio.TimeoutError:
                await channel.send("⏳ **Time up.**")
                break
            player, reason = dm.verify_answer(msg.content, question, pool, taken, full_pool=base_pool, ovr_of=_player_overall)
            if reason == "ok":
                await channel.send(f"✅ **{player['name']}** → **{uname}**")
                return player
            if reason == "unknown":
                await channel.send("❌ Player not found — check the spelling.")
            elif reason == "ambiguous":
                cands = dm.resolve_candidates(msg.content, pool)
                await channel.send("❓ Too many matches — be more specific: " + ", ".join(cands))
            elif reason == "taken":
                await channel.send("❌ Already drafted — pick someone else.")
            elif reason == "wrong_type":
                await channel.send(f"❌ That player doesn't fit **{question['q']}** — try again.")
            elif reason == "out_of_tier":
                tier = dm.player_tier(_player_overall(player))
                tier_one = {"Legends": "Legend", "Greats": "Great", "Youngsters": "Youngster"}.get(tier, tier)
                await channel.send(f"🚫 **{player['name']}** is a **{tier_one}** — this draft only allows the **{mode_label}** pool. Pick a {mode_label} player.")
        filler = dm.make_filler(ri, tag)
        await channel.send(f"🪹 No valid pick — **{uname}** gets a net player: **{filler['name']}**.")
        return filler

    async def _ask_team_name(self, channel, uid, default):
        await channel.send(f"<@{uid}> — type your **team name**  *(or `skip` for “{default}”)*")
        def check(m):
            return m.author.id == uid and m.channel.id == channel.id and m.content.strip()
        try:
            msg = await self.bot.wait_for("message", timeout=60.0, check=check)
            name = msg.content.strip()
            return default if name.lower() == "skip" else name[:28]
        except asyncio.TimeoutError:
            return default

    async def _run_draft(self, ctx, host, opponent):
        channel = ctx.channel
        active_drafts.add(channel.id)
        draft_tasks[channel.id] = asyncio.current_task()
        try:
            base_pool = apply_server_overrides(get_all_players(), str(ctx.guild.id))
            if len(base_pool) < 60:
                return await ctx.send("❌ Not enough players in the database to draft.")
            vs_ai = opponent is None
            host_name = host.display_name
            opp_name = "AI" if vs_ai else opponent.display_name

            # 1) Host picks the player pool (OVR caps are backend-only - never shown).
            mv = DraftModeView(host.id)
            await channel.send(
                f"🎚️ **{host_name}, choose the player pool:**\n"
                f"👑 **Legends** — everyone, all-time greats included\n"
                f"⭐ **Greats** — top stars only (the handful of all-time untouchables are out)\n"
                f"🌱 **Youngsters** — emerging & squad players only (no superstars)",
                view=mv,
            )
            await mv.wait()
            mode = mv.mode or "legends"
            pool = dm.filter_pool(base_pool, mode, _player_overall)

            # 2) Team names.
            host_team = await self._ask_team_name(channel, host.id, f"{host_name}'s XI")
            opp_team = random.choice(_AI_TEAM_NAMES) if vs_ai else await self._ask_team_name(channel, opponent.id, f"{opp_name}'s XI")

            host_first = random.random() < 0.5
            first_team = host_team if host_first else opp_team
            await channel.send(
                f"🎲 **DRAFT — {host_team} vs {opp_team}**\n"
                f"🪙 Toss won by **{first_team}** — they answer first each round.\n"
                f"📋 **{dm.NUM_ROUNDS} rounds**, same question both answer · pick by **typing a player's name** · "
                f"**pure cricket knowledge** — build a balanced XI!"
            )

            taken, host_xi, opp_xi = set(), [], []
            for ri in range(dm.NUM_ROUNDS):
                q = dm.pick_question(ri)
                await channel.send(f"━━━━━━━━━━━━━━━\n**Round {ri+1}/{dm.NUM_ROUNDS} · {dm.slot_label(ri)}**\n🎯 Name **{q['q']}**")
                order = ["host", "opp"] if host_first else ["opp", "host"]
                for who in order:
                    if who == "host":
                        pick = await self._draft_get_pick(channel, host.id, host_team, q, pool, taken, ri, f"H{ri+1}", base_pool, mode)
                        host_xi.append(pick); taken.add(pick["name"])
                    elif vs_ai:
                        pick = dm.ai_pick(pool, taken, q, _player_overall) or dm.make_filler(ri, f"AI{ri+1}")
                        await channel.send(f"🤖 **{opp_team}** picks **{pick['name']}**.")
                        opp_xi.append(pick); taken.add(pick["name"])
                    else:
                        pick = await self._draft_get_pick(channel, opponent.id, opp_team, q, pool, taken, ri, f"O{ri+1}", base_pool, mode)
                        opp_xi.append(pick); taken.add(pick["name"])

            await channel.send(
                "✅ **Draft complete!**\n\n"
                f"**{host_team}**\n" + "\n".join(f"`{i+1:>2}.` {p['name']}" for i, p in enumerate(host_xi)) +
                f"\n\n**{opp_team}**\n" + "\n".join(f"`{i+1:>2}.` {p['name']}" for i, p in enumerate(opp_xi)) +
                "\n\n🏏 Now play it out — same as a normal match. Pick conditions below…"
            )

            # Hand off to the EXACT normal-match flow from the point both XIs are set.
            state = MatchSetupState(host, (None if vs_ai else opponent), host.id, (None if vs_ai else opponent.id))
            state.format_overs = 20
            state.t1_name = host_team[:28]
            state.t2_name = opp_team[:28]
            state.t1_roster = host_xi
            state.t2_roster = opp_xi
            state.home_team_id = host.id
            state.is_draft = True
            state.draft_host_id = host.id
            state.draft_host_name = host_name        # PERSON name -> leaderboard
            state.draft_opp_id = (None if vs_ai else opponent.id)
            state.draft_opp_name = opp_name          # PERSON name -> leaderboard
            active_drafts.discard(channel.id)
            # Pick phase done - hand the cancellation baton to the setup flow so endmatch
            # still works while choosing pitch/weather for the drafted match.
            setup_states[channel.id] = state
            await ask_pitch_and_weather(channel, state)
        except asyncio.CancelledError:
            try:
                await channel.send("🛑 **Draft ended.** Use `cv draft` to start a new one.")
            except Exception:
                pass
            return
        except Exception as e:
            print(f"draft error: {e}")
            await channel.send(f"❌ Draft error: {e}")
        finally:
            active_drafts.discard(channel.id)
            draft_tasks.pop(channel.id, None)

    @commands.command(name="playerlist", aliases=["pl"], help="Download full player database grouped by tier.\nUsage: playerlist")
    async def playerlist(self, ctx):
        players = get_all_players()
        if not players:
            return await ctx.send("❌ Player database is empty.")
        txt = _build_playerlist_txt(players)
        buf = io.BytesIO(txt.encode("utf-8"))
        buf.seek(0)
        await ctx.send(
            f"📋 **Player Database** — {len(players)} players across 4 tiers.\nPlayers within each tier are shuffled.",
            file=discord.File(fp=buf, filename="cricverse_players.txt")
        )

    @commands.command(name="playerlistcompact", aliases=["pla"], help="Download full player database grouped by tier, names comma-separated.\nUsage: pla")
    async def playerlistcompact(self, ctx):
        players = get_all_players()
        if not players:
            return await ctx.send("❌ Player database is empty.")
        txt = _build_playerlist_csv_txt(players)
        buf = io.BytesIO(txt.encode("utf-8"))
        buf.seek(0)
        await ctx.send(
            f"📋 **Player Database (compact)** — {len(players)} players across 4 tiers.\nPlayers within each tier are shuffled, comma-separated.",
            file=discord.File(fp=buf, filename="cricverse_players_compact.txt")
        )

    # Custom saved teams (server-shared XI presets)
    async def _log_team_action(self, ctx, action, team_name, players, impact):
        """Log saveteam/editteam to the global player-database update channel (teams are global)."""
        try:
            xi = "\n".join(f"{i}. {p['name']}" for i, p in enumerate(players, 1)) or "—"
            details = f"Playing XI ({len(players)}):\n{xi}"
            if impact:
                details += f"\n\nImpact ({len(impact)}): " + ", ".join(p["name"] for p in impact)
            details += f"\n\nBy {ctx.author}"
            await log_db_update(f"Team {action}", team_name, ctx.author, details)
        except Exception as _e:
            print(f"Team action log send failed: {_e}")

    @staticmethod
    def _team_admin(ctx):
        """Saving/editing/deleting teams: server admins, auth admins, or Server Pro users.
        Anyone can still view & use saved teams."""
        try:
            if (ctx.author.id == ADMIN_DISCORD_ID
                    or (ctx.guild and ctx.author.guild_permissions.administrator)
                    or str(ctx.author.id) in get_auth_admins()):
                return True
            # Server Pro tier users may also manage saved teams.
            sid = str(ctx.guild.id) if ctx.guild else ""
            return get_tier_status(str(ctx.author.id), sid)[0] == "Server Pro"
        except Exception:
            return False

    @commands.command(name="saveteam", aliases=["setteam", "customteam"], help="[Admin] Save a custom XI preset globally (loadable by name at the XI step in any server).\nUsage: saveteam \"<name>\"  then paste 11 player names (optionally up to 5 more as impact players)")
    async def saveteam(self, ctx, *, name: str = None):
        if not ctx.guild:
            return await ctx.send("❌ Use this inside a server.")
        if not self._team_admin(ctx):
            return await ctx.send("🔒 Only **server admins** or **Server Pro** users can save teams. Anyone can view them with `cv teams` and load them at the XI step.")
        name = (name or "").strip()[:24]
        if not name:
            return await ctx.send("❌ Give it a name, e.g. `cv saveteam \"RCB\"`.")
        if name.lower() == "default":
            return await ctx.send("❌ `default` is reserved — pick another name.")
        await ctx.send(
            f"📋 Reply with the **11 player names** for **{name}** (one per line). You have 3 minutes.\n"
            f"-# 💡 Optional: add up to **5 more names** after the 11 as **impact players** — they're only used in impact-mode matches and ignored otherwise."
        )
        def check(m):
            return m.author.id == ctx.author.id and m.channel.id == ctx.channel.id and m.content.strip()
        try:
            msg = await self.bot.wait_for("message", timeout=180.0, check=check)
        except asyncio.TimeoutError:
            return await ctx.send("⏳ Timed out — run `cv saveteam` again.")
        parsed, missing, *_cap = parse_pasted_roster(msg.content, get_all_players())
        players = parsed[:11]
        impact = parsed[11:16]
        if missing or len(players) < 11:
            err = f"❌ Need **11 valid players** ({len(players)}/11 found).\n"
            if missing:
                err += f"Not in DB: {', '.join(missing)}\n"
            return await msg.reply(err + "Fix the names and run `cv saveteam` again.")
        save_custom_team(name, [p["name"] for p in players], [p["name"] for p in impact],
                         [p["name"] for p in players + impact if p.get("avoid_bowl")])
        out = f"✅ Saved team **{name}**! Load it in any match by typing **{name}** at the XI step.\n\n{format_xi_display(players)}"
        if impact:
            out += "\n\n**Impact players:**\n" + format_xi_display(impact)
        await msg.reply(out)
        await self._log_team_action(ctx, "Saved", name, players, impact)

    @commands.command(name="teams", aliases=["customteams", "myteams"], help="List all saved custom teams (shared globally across servers).\nUsage: teams")
    async def teams(self, ctx):
        t = list_custom_teams()
        if not t:
            embed = discord.Embed(
                title="📋 Saved Teams",
                description="No saved teams yet.\nAn admin can create one with `cv saveteam \"<name>\"`.",
                color=0x5865F2,
            )
            return await ctx.send(embed=embed)

        teams_sorted = sorted(t.values(), key=lambda x: x["name"].lower())
        lines = []
        for i, v in enumerate(teams_sorted, 1):
            n_xi = len(v.get("players", []))
            n_imp = len(v.get("impact", []))
            badge = f" · ⚡ {n_imp} impact" if n_imp else ""
            warn = " ⚠️" if n_xi < 11 else ""
            lines.append(f"`{i:>2}.` **{v['name']}** — {n_xi}/11 players{badge}{warn}")

        embed = discord.Embed(
            title=f"📋 Saved Teams — {ctx.guild.name}",
            description="\n".join(lines),
            color=0x5865F2,
        )
        embed.set_footer(text="Type a team's name at the XI step to load it · cv team \"<name>\" to view the full XI")
        await ctx.send(embed=embed)

    @commands.command(name="team", aliases=["viewteam"], help="View a saved custom team's XI.\nUsage: team \"<name>\"")
    async def team(self, ctx, *, name: str = None):
        ct = get_custom_team(name or "")
        if not ct:
            return await ctx.send(f"❌ No saved team named **{name}**. See `cv teams`.")
        players, impact, missing_xi, missing_impact = _saved_team_lineup(ct)
        gone = missing_xi + missing_impact
        note = f"\n⚠️ No longer in DB: {', '.join(gone)}" if gone else ""
        out = f"📋 **{ct['name']}**\n{format_xi_display(players)}"
        if impact:
            out += "\n\n**Impact players:**\n" + format_xi_display(impact)
        await ctx.send(out + note)

    @commands.command(name="deleteteam", aliases=["delteam", "removeteam"], help="[Admin] Delete a saved custom team.\nUsage: deleteteam \"<name>\"")
    async def deleteteam(self, ctx, *, name: str = None):
        if not ctx.guild:
            return await ctx.send("❌ Use this inside a server.")
        if not self._team_admin(ctx):
            return await ctx.send("🔒 Only **server admins** or **Server Pro** users can delete teams.")
        if delete_custom_team(name or ""):
            await ctx.send(f"🗑️ Deleted saved team **{name}**.")
        else:
            await ctx.send(f"❌ No saved team named **{name}**.")

    @commands.command(name="editteam", aliases=["updateteam"], help="[Admin] Replace a saved team's XI with a fresh 11.\nUsage: editteam \"<name>\"  then paste the new 11 player names")
    async def editteam(self, ctx, *, name: str = None):
        if not ctx.guild:
            return await ctx.send("❌ Use this inside a server.")
        if not self._team_admin(ctx):
            return await ctx.send("🔒 Only **server admins** or **Server Pro** users can edit teams.")
        ct = get_custom_team(name or "")
        if not ct:
            return await ctx.send(f"❌ No saved team named **{name}**. See `cv teams`, or create one with `cv saveteam`.")
        cur, *_ = _saved_team_lineup(ct)
        await ctx.send(
            f"✏️ Editing **{ct['name']}** — current XI:\n{format_xi_display(cur)}\n\n"
            f"Reply with the **new 11 player names** (one per line). You have 3 minutes. Type `cancel` to abort.\n"
            f"-# 💡 Optional: add up to **5 more names** after the 11 as **impact players** (used only in impact-mode matches)."
        )
        def check(m):
            return m.author.id == ctx.author.id and m.channel.id == ctx.channel.id and m.content.strip()
        try:
            msg = await self.bot.wait_for("message", timeout=180.0, check=check)
        except asyncio.TimeoutError:
            return await ctx.send("⏳ Timed out — `cv editteam` again. Team unchanged.")
        if msg.content.strip().lower() == "cancel":
            return await msg.reply("❎ Cancelled — team unchanged.")
        parsed, missing, *_cap = parse_pasted_roster(msg.content, get_all_players())
        players = parsed[:11]
        impact = parsed[11:16]
        if missing or len(players) < 11:
            err = f"❌ Need **11 valid players** ({len(players)}/11 found). Team unchanged.\n"
            if missing:
                err += f"Not in DB: {', '.join(missing)}\n"
            return await msg.reply(err + "Fix the names and run `cv editteam` again.")
        save_custom_team(ct["name"], [p["name"] for p in players], [p["name"] for p in impact],
                         [p["name"] for p in players + impact if p.get("avoid_bowl")])
        out = f"✅ Updated **{ct['name']}**!\n\n{format_xi_display(players)}"
        if impact:
            out += "\n\n**Impact players:**\n" + format_xi_display(impact)
        await msg.reply(out)
        await self._log_team_action(ctx, "Edited", ct["name"], players, impact)

    async def _roster_collect(self, ctx, prompt, cap, min_n, label):
        """Paste -> smart-resolve (same fuzzy matcher as the match-XI flow) -> confirm
        (Yes / re-enter), looping until confirmed. Returns the player dicts or None on
        timeout. Shared by `cv bestxi` and `cv bestpitch`."""
        db = get_all_players()

        def check(m):
            return (m.author.id == ctx.author.id and m.channel.id == ctx.channel.id
                    and m.content.strip())

        async def confirm(players):
            listing = "\n".join(
                f"`{i:>2}.` {p['name']} · {p['bat']}/{p['bowl']}"
                + (" · 🚫L" if p.get("avoid_bowl") else "")
                for i, p in enumerate(players, 1))

            class _Confirm(discord.ui.View):
                def __init__(self):
                    super().__init__(timeout=120)
                    self.ok = None

                async def interaction_check(self, it):
                    if it.user.id != ctx.author.id:
                        await it.response.send_message("Not your panel.", ephemeral=True)
                        return False
                    return True

                @discord.ui.button(label="Yes, correct", style=discord.ButtonStyle.success, emoji="✅")
                async def yes(self, it, btn):
                    self.ok = True
                    await it.response.defer()
                    self.stop()

                @discord.ui.button(label="No, re-enter", style=discord.ButtonStyle.danger, emoji="✏️")
                async def no(self, it, btn):
                    self.ok = False
                    await it.response.defer()
                    self.stop()

            v = _Confirm()
            m = await ctx.send(f"**{label}** — {len(players)} players:\n{listing}\n\n"
                               f"Is this correct?", view=v)
            await v.wait()
            await m.edit(view=None)
            return v.ok

        while True:
            await ctx.send(prompt)
            try:
                m = await self.bot.wait_for("message", timeout=180.0, check=check)
            except asyncio.TimeoutError:
                return None
            found, missing, *_ = parse_pasted_roster(m.content, db, max_lines=cap)
            if missing or len(found) < min_n:
                e = f"❌ Need **≥{min_n} valid players** ({len(found)} found)."
                if missing:
                    e += f"\nNot in DB: {', '.join(missing)}"
                await m.reply(e + "\nLet's try again.")
                continue
            ok = await confirm(found)
            if ok is None:
                return None
            if ok:
                return found
            # 'No' -> loop and re-paste

    async def _optimize_in_subprocess(self, fn_name, *args, on_progress=None,
                                      proc_box=None):
        """Run a tools.lineup_optimizer entry point in its OWN process and return
        its result. The optimizer seeds the global `random` module for repeatable
        results, and that state is process-wide: run via asyncio.to_thread, any
        other bot activity touching random.* mid-run shifts the stream and the
        same command gives a different XI each time.
        `on_progress` (async, int 0..100) receives the worker's progress lines;
        `proc_box` (a dict) exposes the process under "proc" so a cancel button
        can kill it. The worker gets its own session so a cancel can take its
        multiprocessing children down with it (killpg)."""
        import pickle
        import sys
        worker = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "tools", "optimizer_worker.py")
        proc = await asyncio.create_subprocess_exec(
            sys.executable, worker,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, start_new_session=True)
        if proc_box is not None:
            proc_box["proc"] = proc
        err_tail = []

        async def _pump_stderr():
            while True:
                line = await proc.stderr.readline()
                if not line:
                    return
                s = line.decode(errors="replace").strip()
                if s.startswith("P ") and on_progress:
                    try:
                        await on_progress(int(s[2:]))
                    except Exception:
                        pass
                elif s:
                    err_tail.append(s)

        pump = asyncio.create_task(_pump_stderr())
        proc.stdin.write(pickle.dumps((fn_name, args)))
        await proc.stdin.drain()
        proc.stdin.close()
        out = await proc.stdout.read()
        await proc.wait()
        await pump
        if proc.returncode != 0 or not out:
            raise RuntimeError("optimizer worker failed: "
                               + (" · ".join(err_tail)[-400:] or
                                  f"exit code {proc.returncode}"))
        ok, payload = pickle.loads(out)
        if not ok:
            raise RuntimeError(payload)
        return payload

    @commands.command(name="bestpitch", aliases=["bp", "homepitch"], help="[OWNER] Find the pitch your squad is strongest on (vs any opponent style).\nUsage: bestpitch → paste squad")
    async def bestpitch(self, ctx):
        if ctx.author.id != ADMIN_DISCORD_ID:
            return await ctx.send("🔒 Owner only.")

        squad = await self._roster_collect(
            ctx, "🏟️ **Best Pitch Finder** (owner) — paste your **SQUAD** (11–30 names, "
            "one per line). You have 3 minutes.", 30, 11, "Your SQUAD")
        if squad is None:
            return await ctx.send("⏳ Timed out — run `cv bestpitch` again.")

        working = await ctx.send("🏟️ Finding the pitch your team is strongest on… (~7s)")
        try:
            hp = await self._optimize_in_subprocess("best_home_pitch", squad)
        except Exception as ex:
            return await working.edit(content=f"❌ Error: {ex}")

        f = hp["field"][hp["best"]]
        worst_kind = min(f, key=f.get)
        best_avg = sum(f.values()) / len(f)
        lines = []
        for p in hp["ranked"][:8]:
            ff = hp["field"][p]
            lines.append(f"`{p:<10}` adv **{sum(ff.values()) / len(ff):.0f}%** · "
                         f"floor {min(ff.values()):.0f}% · "
                         f"[bal{ff['balanced']:.0f} spin{ff['spin']:.0f} "
                         f"pace{ff['pace']:.0f} bat{ff['bat']:.0f}]")
        e = discord.Embed(
            title=f"🏟️ Best HOME pitch: {hp['best']}", color=0x3498db,
            description=(f"The deck your squad is strongest on: **{best_avg:.0f}%** "
                        f"average advantage, and even its toughest matchup "
                        f"(**{worst_kind}-strong**) is still a win at "
                        f"**{f[worst_kind]:.0f}%** — no style can exploit it.\n\n"
                        f"**Top decks (advantage + floor):**\n" + "\n".join(lines)))
        e.set_footer(text=f"Squad {len(squad)} · team OVR {hp['ref_ovr']} · owner-only · "
                          f"ranked by average advantage + worst-case floor")
        await working.edit(content=None, embed=e)

    @commands.command(name="bestxi", aliases=["bxi", "optimizexi"], help="[OWNER] Find the best XI from your squad for a chosen pitch vs an opponent.\nUsage: bestxi [odi]  → paste squad → pick pitch + what the other team is good at (or paste their XI).\nDefaults to T20; pass `odi` for 50 overs.")
    async def bestxi(self, ctx, fmt: str = None):
        if ctx.author.id != ADMIN_DISCORD_ID:
            return await ctx.send("🔒 Owner only.")
        from tools.lineup_optimizer import PITCHES, category

        is_odi = (fmt or "").strip().lower() in ("odi", "50", "od", "50ov")
        format_overs = 50 if is_odi else 20
        fmt_label = "ODI · 50 ov" if is_odi else "T20 · 20 ov"

        # 1) squad (with confirm)
        squad = await self._roster_collect(
            ctx, f"🧠 **Best XI Finder** ({fmt_label}, owner) — paste your **SQUAD** "
            f"(11–30 names, one per line). You have 3 minutes.", 30, 11, "Your SQUAD")
        if squad is None:
            return await ctx.send("⏳ Timed out — run `cv bestxi` again.")

        # 2) pitch + opponent picker
        class _BestXIView(discord.ui.View):
            def __init__(self):
                super().__init__(timeout=240)
                self.pitch = None
                self.opp = None
                self.weather = "Clear"
                self.go = False
                self.ps = discord.ui.Select(
                    placeholder="1) Pick the PITCH…",
                    options=[discord.SelectOption(label=p, value=p) for p in PITCHES][:25])
                self.osel = discord.ui.Select(
                    placeholder="2) The opponent is strong at…",
                    options=[
                        discord.SelectOption(label="Balanced side", value="balanced", emoji="⚖️"),
                        discord.SelectOption(label="Spin-strong", value="spin", emoji="🌀"),
                        discord.SelectOption(label="Pace-strong", value="pace", emoji="🔥"),
                        discord.SelectOption(label="Batting-heavy", value="bat", emoji="🏏"),
                        discord.SelectOption(label="Enter their exact XI", value="custom", emoji="📝"),
                    ])
                self.wsel = discord.ui.Select(
                    placeholder="3) WEATHER (optional · default Clear)…",
                    options=[discord.SelectOption(label=w, value=w,
                             default=(w == "Clear")) for w in ALL_WEATHER][:25])
                self.ps.callback = self._p
                self.osel.callback = self._o
                self.wsel.callback = self._w
                self.add_item(self.ps)
                self.add_item(self.osel)
                self.add_item(self.wsel)

            async def interaction_check(self, it):
                if it.user.id != ctx.author.id:
                    await it.response.send_message("Not your panel.", ephemeral=True)
                    return False
                return True

            async def _p(self, it):
                self.pitch = self.ps.values[0]
                await it.response.defer()

            async def _o(self, it):
                self.opp = self.osel.values[0]
                await it.response.defer()

            async def _w(self, it):
                self.weather = self.wsel.values[0]
                await it.response.defer()

            @discord.ui.button(label="Find Best XI", style=discord.ButtonStyle.success, emoji="🧠")
            async def run_btn(self, it, btn):
                if not self.pitch or not self.opp:
                    return await it.response.send_message(
                        "Pick a pitch AND an opponent type first.", ephemeral=True)
                self.go = True
                await it.response.defer()
                self.stop()

        view = _BestXIView()
        panel = await ctx.send(
            f"✅ Squad: **{len(squad)} players**. Now choose the **pitch** and **opponent**, "
            f"then hit **Find Best XI**.", view=view)
        await view.wait()
        if not view.go:
            return await panel.edit(content="⏳ Timed out — run `cv bestxi` again.", view=None)
        await panel.edit(view=None)

        # 3) opponent: a style, or an explicit XI (with confirm)
        opp_spec = view.opp
        if view.opp == "custom":
            opp_players = await self._roster_collect(
                ctx, "📝 Paste the **opponent's 11** (one per line; end a line with "
                "` L` if they use less-bowling on that player). 3 minutes.",
                11, 11, "Opponent XI")
            if opp_players is None:
                return await ctx.send("⏳ Timed out — run `cv bestxi` again.")
            opp_spec = opp_players[:11]

        opp_label = "their XI" if view.opp == "custom" else f"a {view.opp}-strong side"
        job = {}

        class _CancelView(discord.ui.View):
            def __init__(self):
                super().__init__(timeout=3600)
                self.cancelled = False

            async def interaction_check(self, it):
                if it.user.id != ctx.author.id:
                    await it.response.send_message("Not your panel.", ephemeral=True)
                    return False
                return True

            @discord.ui.button(label="Cancel", style=discord.ButtonStyle.danger, emoji="🛑")
            async def cancel_btn(self, it, btn):
                self.cancelled = True
                p = job.get("proc")
                if p and p.returncode is None:
                    import signal
                    try:
                        # Kill the whole session: the worker fans out over a
                        # multiprocessing pool, and killing just the parent
                        # would orphan the pool children mid-simulation.
                        os.killpg(os.getpgid(p.pid), signal.SIGKILL)
                    except (OSError, ProcessLookupError):
                        p.kill()
                await it.response.defer()
                self.stop()

        cview = _CancelView()
        base_msg = (f"🧠 Simulating the best **{fmt_label}** XI on **{view.pitch}** "
                    f"({view.weather}) vs {opp_label}…")
        working = await ctx.send(base_msg + " **0%**", view=cview)

        import time
        last_edit = [0.0]

        async def on_prog(pct):
            # Throttle Discord edits (rate limits); the final state is written
            # by the result/cancel path, so dropped ticks don't matter.
            now = time.monotonic()
            if now - last_edit[0] < 4 or cview.cancelled or pct >= 100:
                return
            last_edit[0] = now
            try:
                await working.edit(content=f"{base_msg} **{pct}%**")
            except discord.HTTPException:
                pass

        try:
            r = await self._optimize_in_subprocess(
                "recommend_xi", squad, view.pitch, opp_spec,
                view.weather, format_overs, on_progress=on_prog, proc_box=job)
        except Exception as ex:
            cview.stop()
            if cview.cancelled:
                return await working.edit(content="🛑 Simulation cancelled.", view=None)
            return await working.edit(content=f"❌ Error: {ex}", view=None)
        cview.stop()

        # 4) format result
        cap = r["captain"]["name"] if r["captain"] else "-"
        imp = r["impact"]
        rows = []
        for i, p in enumerate(r["order"], 1):
            wk = " (WK)" if "WK" in p["role"] else ""
            c = " 🧢" if p["name"] == cap else ""
            lb = " 🚫L" if p.get("avoid_bowl") else ""
            rows.append(f"`{i:>2}.` {p['name']}{wk} · {p['bat']}/{p['bowl']}{c}{lb}")
        e = discord.Embed(title=f"🧠 Best XI ({fmt_label}) · {view.pitch} / {view.weather} "
                                f"vs {opp_label}",
                          description="\n".join(rows), color=0x2ecc71)
        e.add_field(name="🧢 Captain", value=cap, inline=True)
        if imp:   # impact player is a T20-only rule - omit the field for ODIs
            e.add_field(name="⚡ Impact Player",
                        value=f"{imp['name']} ({category(imp)})", inline=True)
        e.add_field(name="Win %", value=f"{r['winpct']:.0f}%", inline=True)
        if r.get("l_tags"):
            e.add_field(name="🚫 Less bowling (L)",
                        value=", ".join(r["l_tags"]) + " — keeping them out of the "
                        "main attack raises your win%", inline=False)
        toss = r.get("toss")
        if toss:
            e.add_field(
                name="🪙 Win the toss →",
                value=f"**{toss['decision']}**  (bat {toss['bat_first']:.0f}% · "
                      f"field {toss['field_first']:.0f}%)", inline=False)
        if r["benched"]:
            e.add_field(name="Left out",
                        value=", ".join(p["name"] for p in r["benched"][:14]), inline=False)
        e.set_footer(text=f"{fmt_label} · squad {len(squad)} · team OVR {r['ref_ovr']} · "
                          f"owner-only · batting order optimised for this deck")
        await working.edit(content=None, embed=e, view=None)

    @commands.command(name="playerlistratings", aliases=["plr", "plratings"], help="[OWNER] Download the full player database WITH ratings.\nUsage: plr")
    async def playerlistratings(self, ctx):
        if ctx.author.id != ADMIN_DISCORD_ID:
            return await ctx.send("❌ Owner only.")
        players = get_all_players()
        if not players:
            return await ctx.send("❌ Player database is empty.")
        txt = _build_playerlist_ratings_txt(players)
        buf = io.BytesIO(txt.encode("utf-8"))
        buf.seek(0)
        await ctx.send(
            f"📊 **Player Database — WITH RATINGS** ({len(players)} players, sorted by OVR).\n-# Owner-only · global DB ratings.",
            file=discord.File(fp=buf, filename="cricverse_players_ratings.txt")
        )

    @commands.command(name="counts", aliases=["matchcounts"], help="Show the total number of matches played per format.\nUsage: counts")
    async def counts(self, ctx):
        c = get_match_counts()
        await ctx.send(
            f"📊 **Matches Played on CricVerse**\n"
            f"⚡ T20: **{c['t20']}**\n"
            f"🏆 ODI: **{c['odi']}**\n"
            f"🎩 TEST: **{c['test']}**"
        )

    @commands.command(name="setcount", aliases=["sc"], help="[ADMIN] Set the match counter for a format.\nUsage: setcount <t20|odi|test> <number>")
    async def setcount(self, ctx, fmt: str = None, count: str = None):
        is_owner = ctx.author.id == ADMIN_DISCORD_ID
        is_admin_user = is_owner or str(ctx.author.id) in get_auth_admins()
        if not is_admin_user:
            return await ctx.send("❌ Admin or owner only.")

        fmt_key = (fmt or "").lower().strip()
        if fmt_key not in ("t20", "odi", "test"):
            return await ctx.send("❌ Format must be one of: `t20`, `odi`, `test`\nExample: `cv setcount t20 150`")

        try:
            n = int(count)
            if n < 0: raise ValueError
        except (TypeError, ValueError):
            return await ctx.send("❌ Count must be a non-negative integer.\nExample: `cv setcount t20 150`")

        old = get_match_counts().get(fmt_key, 0)
        _set_match_count(fmt_key, n)
        await ctx.send(f"✅ **{fmt_key.upper()}** count updated: `{old}` → `{n}`")

    @commands.command(name="searchplayer", aliases=["sp"], help="Search for a player in the Cloud DB.\nUsage: searchplayer <name>")
    async def searchplayer(self, ctx, *, name: str):
        search_query = name.strip()
        all_players = get_all_players()
        player_names = [p["name"] for p in all_players]

        if not all_players:
            return await ctx.send("❌ Error: Cache is empty.")

        show_ratings = _can_see_ratings(ctx.author.id, ctx.channel.id)

        class FakeFollowup:
            async def send(self, *args, **kwargs):
                await ctx.send(*args, **kwargs)
        class FakeInteraction:
            def __init__(self): self.followup = FakeFollowup()

        # Show THIS server's overridden ratings/OVR if the player has a server override.
        _sid = str(ctx.guild.id) if ctx.guild else None
        def _disp(p):
            return apply_server_overrides([p], _sid)[0]
        async def _send_profile(p):
            await send_player_profile(FakeInteraction(), _disp(p), show_ratings)
            if show_ratings and _sid and p["name"].lower() in get_server_overrides(_sid):
                await ctx.send(f"-# 🎚️ Showing **{ctx.guild.name}** server-override ratings for **{p['name']}** — the global DB value is different.")

        exact = next((p for p in all_players if p["name"].lower() == search_query.lower()), None)
        if exact:
            return await _send_profile(exact)

        subs = [p for p in all_players if search_query.lower() in p["name"].lower()]
        fuzz = difflib.get_close_matches(search_query, player_names, n=1, cutoff=0.2)

        if not subs and not fuzz:
            return await ctx.send(f"❌ Player `{search_query}` not found in the database.")

        if len(subs) == 1 and not fuzz:
            return await _send_profile(subs[0])

        best_name = fuzz[0] if fuzz else subs[0]["name"]
        other = [p["name"] for p in subs if p["name"] != best_name]
        msg = f"🔍 **Not found exactly.**\n💡 **Best Match:** `{best_name}`\n👉 Rerun: `cv searchplayer \"{best_name}\"`"
        if other:
            msg += "\n\n📂 **Alternatives:**\n" + "\n".join(f"• {o}" for o in other[:5])
        await ctx.send(msg)

    @commands.command(name="force_sync", aliases=["fs"], help="[OWNER] Manually force backup memory cache to Cloud DB.\nUsage: force_sync")
    async def force_sync(self, ctx):
        if ctx.author.id != ADMIN_DISCORD_ID:
            return await ctx.send("❌ Owner only.")
        try:
            res = save_data_to_bin()
            res_t = save_tournament_data_to_bin()
            lines = []
            if res is None:
                lines.append("❌ Main DB skipped — MONGO_URI missing.")
            elif res:
                lines.append("✅ Main DB synced to MongoDB.")
            else:
                lines.append("❌ Main DB save failed — check bot logs.")
            if res_t is None:
                lines.append("❌ Tournament DB skipped — MONGO_URI missing.")
            elif res_t:
                lines.append("✅ Tournament DB synced to MongoDB.")
            else:
                lines.append("❌ Tournament DB save failed — check bot logs.")
            await ctx.send("\n".join(lines))
        except Exception as e:
            await ctx.send(f"❌ Error during sync: {e}")

    @commands.command(name="force_load", aliases=["fl"], help="[OWNER] Reload in-memory cache from MongoDB.\nUsage: force_load")
    async def force_load(self, ctx):
        if ctx.author.id != ADMIN_DISCORD_ID:
            return await ctx.send("❌ Owner only.")
        try:
            load_data_from_bin()
            load_tournament_data_from_bin()
            await ctx.send(
                f"✅ Cache reloaded from MongoDB — {len(DB_CACHE['players'])} players, "
                f"{len(DB_CACHE['tournaments'])} tournament(s)."
            )
        except Exception as e:
            await ctx.send(f"❌ Error during reload: {e}")

    @commands.command(name="addplayer", aliases=["ap"], help="[ADMIN] Add player to DB.\nUsage: addplayer \"<name>\" <bat> <bowl> <role> <archetype>")
    async def addplayer(self, ctx, name: str, bat: int, bowl: int, role: str, archetype: str):
        admins = get_auth_admins()
        if ctx.author.id != ADMIN_DISCORD_ID and str(ctx.author.id) not in admins:
            return await ctx.send("❌ Access Denied: Admin only.")
        valid_roles = ["Batter", "Batter_WK", "Bowler_Pace", "Bowler_Spin_Off", "Bowler_Spin_Leg", "All-Rounder_Pace", "All-Rounder_Spin_Off", "All-Rounder_Spin_Leg"]
        valid_archs = ["Aggressor", "Anchor", "Finisher", "Standard", "Vaibhav"]
        if not (0 <= bat <= 100 and 0 <= bowl <= 100):
            return await ctx.send("❌ Bat/Bowl ratings must be 0-100.")
        if role not in valid_roles:
            return await ctx.send(f"❌ Invalid role. Choose from: {', '.join(valid_roles)}")
        if archetype not in valid_archs:
            return await ctx.send(f"❌ Invalid archetype. Choose from: {', '.join(valid_archs)}")
        success = add_player({"name": name.strip(), "bat": bat, "bowl": bowl, "role": role, "archetype": archetype})
        if not success:
            return await ctx.send(f"❌ `{name}` already exists in the database!")
        await ctx.send(f"✅ Added `{name}` to the database!")
        await log_db_update("Player Added", name, ctx.author, f"Bat: {bat} | Bowl: {bowl}\nRole: {role}\nArchetype: {archetype}")

    @commands.command(name="updateplayer", aliases=["up"], help="[ADMIN] Update player in DB.\nUsage: updateplayer \"<name>\" <bat> <bowl> <role> <archetype> [\"<newname>\"]")
    async def updateplayer(self, ctx, name: str, bat: int, bowl: int, role: str, archetype: str, *, new_name: str = None):
        admins = get_auth_admins()
        if ctx.author.id != ADMIN_DISCORD_ID and str(ctx.author.id) not in admins:
            return await ctx.send("❌ Access Denied: Admin only.")
        valid_roles = ["Batter", "Batter_WK", "Bowler_Pace", "Bowler_Spin_Off", "Bowler_Spin_Leg", "All-Rounder_Pace", "All-Rounder_Spin_Off", "All-Rounder_Spin_Leg"]
        valid_archs = ["Aggressor", "Anchor", "Finisher", "Standard", "Vaibhav"]
        all_p = get_all_players()
        cur = next((p for p in all_p if p["name"].lower() == name.strip().lower()), None)
        if not cur:
            return await ctx.send(f"❌ `{name}` not found in the database.")
        if not (0 <= bat <= 100 and 0 <= bowl <= 100):
            return await ctx.send("❌ Bat/Bowl ratings must be 0-100.")
        if role not in valid_roles:
            return await ctx.send(f"❌ Invalid role. Choose from: {', '.join(valid_roles)}")
        if archetype not in valid_archs:
            return await ctx.send(f"❌ Invalid archetype. Choose from: {', '.join(valid_archs)}")
        final_name = new_name.strip() if new_name else cur["name"]
        if final_name.lower() != cur["name"].lower() and any(p["name"].lower() == final_name.lower() for p in all_p):
            return await ctx.send(f"❌ A player named `{final_name}` already exists!")
        update_player(cur["name"], {"name": final_name, "bat": bat, "bowl": bowl, "role": role, "archetype": archetype})
        await ctx.send(f"✅ Updated `{final_name}` in the database!")
        await log_db_update("Player Updated", final_name, ctx.author, f"Bat: {bat} | Bowl: {bowl}\nRole: {role}\nArchetype: {archetype}")

    # Per-server rating overrides (OWNER-only, separate from the global player DB)
    @commands.command(name="srating", aliases=["serverrating", "soverride"], help="[OWNER] Override a player's ratings for THIS server only (global DB untouched).\nUsage: srating \"<player>\" bat=86 bowl=20 [role=Batter] [arch=Anchor]   ·   srating \"<player>\" reset")
    async def srating(self, ctx, player: str, *args):
        if ctx.author.id != ADMIN_DISCORD_ID:
            return await ctx.send("❌ Owner only.")
        if not ctx.guild:
            return await ctx.send("❌ Use this inside a server.")
        sid = str(ctx.guild.id)
        all_p = get_all_players()
        cur = next((p for p in all_p if p["name"].lower() == player.strip().lower()), None)
        if not cur:
            close = difflib.get_close_matches(player, [p["name"] for p in all_p], n=1, cutoff=0.6)
            cur = next((p for p in all_p if p["name"] == close[0]), None) if close else None
        if not cur:
            return await ctx.send(f"❌ Player '{player}' not found in the global database.")
        name = cur["name"]

        if len(args) == 1 and args[0].lower() in ("reset", "clear", "remove", "default"):
            if reset_server_override(sid, name):
                return await ctx.send(f"✅ Removed the override for **{name}** on this server — back to global (bat {cur['bat']} · bowl {cur['bowl']}).")
            return await ctx.send(f"ℹ️ **{name}** has no override on this server.")

        valid_roles = ["Batter", "Batter_WK", "Bowler_Pace", "Bowler_Spin_Off", "Bowler_Spin_Leg", "All-Rounder_Pace", "All-Rounder_Spin_Off", "All-Rounder_Spin_Leg"]
        valid_archs = ["Aggressor", "Anchor", "Finisher", "Standard", "Vaibhav"]
        fields = {}
        for a in args:
            if "=" not in a:
                continue
            k, v = a.split("=", 1); k = k.strip().lower(); v = v.strip()
            if k in ("bat", "bowl"):
                if not v.isdigit() or not (0 <= int(v) <= 99):
                    return await ctx.send(f"❌ `{k}` must be a whole number 0-99.")
                fields[k] = int(v)
            elif k == "role":
                if v not in valid_roles:
                    return await ctx.send(f"❌ Invalid role. Choose from: {', '.join(valid_roles)}")
                fields["role"] = v
            elif k in ("arch", "archetype"):
                if v not in valid_archs:
                    return await ctx.send(f"❌ Invalid archetype. Choose from: {', '.join(valid_archs)}")
                fields["archetype"] = v
        if not fields:
            return await ctx.send("❌ Nothing to set. Try `srating \"Virat Kohli\" bat=96 bowl=50`  or  `srating \"Virat Kohli\" reset`.")
        set_server_override(sid, name, fields)
        eff = {**cur, **get_server_overrides(sid).get(name.lower(), {})}
        await ctx.send(
            f"✅ **{name}** overridden on **this server only**:\n"
            f"🏏 bat **{eff['bat']}** · 🎯 bowl **{eff['bowl']}** · {eff['role']} · {eff['archetype']}\n"
            f"-# Global DB unchanged (bat {cur['bat']} · bowl {cur['bowl']}). Applies to all matches on this server."
        )

    @commands.command(name="sratings", aliases=["serverratings", "soverrides"], help="[OWNER] List this server's player rating overrides.\nUsage: sratings")
    async def sratings(self, ctx):
        if ctx.author.id != ADMIN_DISCORD_ID:
            return await ctx.send("❌ Owner only.")
        if not ctx.guild:
            return await ctx.send("❌ Use this inside a server.")
        srv = get_server_overrides(str(ctx.guild.id))
        if not srv:
            return await ctx.send("ℹ️ No rating overrides on this server. Set one with `srating \"<player>\" bat=.. bowl=..`.")
        base = {p["name"].lower(): p for p in get_all_players()}
        lines = []
        for key, o in sorted(srv.items()):
            g = base.get(key, {})
            nm = o.get("name", key)
            parts = []
            for f, lbl in (("bat", "bat"), ("bowl", "bowl"), ("role", "role"), ("archetype", "arch")):
                if f in o:
                    gv = g.get(f, "?")
                    parts.append(f"{lbl} {gv}→**{o[f]}**" if gv != o[f] else f"{lbl} **{o[f]}**")
            lines.append(f"• **{nm}** — {' · '.join(parts)}")
        embed = discord.Embed(title=f"🎚️ Server Rating Overrides — {ctx.guild.name}", description="\n".join(lines), color=discord.Color.teal())
        embed.set_footer(text="Owner-only · applies to all matches on THIS server · global DB unchanged")
        await ctx.send(embed=embed)

    @commands.command(name="deleteplayer", aliases=["dp"], help="[ADMIN] Delete a player from DB.\nUsage: deleteplayer \"<name>\"")
    async def deleteplayer(self, ctx, *, name: str):
        admins = get_auth_admins()
        if ctx.author.id != ADMIN_DISCORD_ID and str(ctx.author.id) not in admins:
            return await ctx.send("❌ Access Denied: Admin only.")
        all_p = get_all_players()
        found = [p["name"] for p in all_p if p["name"].lower().strip() == name.lower().strip()]
        if not found:
            return await ctx.send(f"❌ Could not find `{name}` in the database.")
        delete_players(found)
        await ctx.send(f"✅ Deleted `{', '.join(found)}` from the database.")
        await log_db_update("Player Deleted", name, ctx.author, f"Removed: {', '.join(found)}")

    @commands.command(name="cleanduplicates", aliases=["cd"], help="[ADMIN] Remove duplicate players from DB.\nUsage: cleanduplicates")
    async def cleanduplicates(self, ctx):
        admins = get_auth_admins()
        if ctx.author.id != ADMIN_DISCORD_ID and str(ctx.author.id) not in admins:
            return await ctx.send("❌ Access Denied: Admin only.")
        removed_names = clean_duplicate_players()
        if removed_names:
            await ctx.send(f"✅ Removed {len(removed_names)} duplicate(s): " + ", ".join(removed_names[:50]))
            await log_db_update("Database Cleaned", "Duplicates Removed", ctx.author, f"Removed {len(removed_names)} duplicates:\n{', '.join(removed_names[:50])}")
        else:
            await ctx.send("✅ No duplicates found. Database is clean.")

    @commands.command(name="dump_cache", aliases=["dc"], help="[OWNER] Export tournament cache as JSON file.\nUsage: dump_cache")
    async def dump_cache(self, ctx):
        if ctx.author.id != ADMIN_DISCORD_ID:
            return await ctx.send("❌ Owner only.")
        try:
            data = {"tournaments": DB_CACHE.get("tournaments", [])}
            raw = json.dumps(data, indent=2, ensure_ascii=False)
            file = discord.File(fp=io.BytesIO(raw.encode("utf-8")), filename="tournament_cache_dump.json")
            await ctx.send("📦 Current in-memory tournament data:", file=file)
        except Exception as e:
            await ctx.send(f"❌ Dump failed: {e}")

    @commands.command(name="restore_tournament", aliases=["restore_tourney", "rt"], help="[OWNER] Restore tournament(s) from an attached JSON backup.\nUsage: attach the backup JSON + restore_tournament")
    async def restore_tournament(self, ctx):
        if ctx.author.id != ADMIN_DISCORD_ID:
            return await ctx.send("❌ Owner only.")
        # Find the JSON from: this message, a replied-to message, or recent channel history
        att = None
        if ctx.message.attachments:
            att = ctx.message.attachments[0]
        elif ctx.message.reference and ctx.message.reference.message_id:
            try:
                ref = await ctx.channel.fetch_message(ctx.message.reference.message_id)
                if ref.attachments:
                    att = ref.attachments[0]
            except Exception:
                pass
        if att is None:
            try:
                async for _m in ctx.channel.history(limit=25):
                    _js = [a for a in _m.attachments if a.filename.lower().endswith(".json")]
                    if _js:
                        att = _js[0]; break
            except Exception:
                pass
        if att is None:
            return await ctx.send(
                "❌ No JSON file found. Send it any of these ways:\n"
                "• attach the file **with** `cv restore_tournament` typed as the caption, **or**\n"
                "• upload the file, then **reply** to it with `cv restore_tournament`, **or**\n"
                "• upload the file, then send `cv restore_tournament` right after (I scan the last 25 messages).")
        try:
            raw = await att.read()
            data = json.loads(raw.decode("utf-8"))
        except Exception as e:
            return await ctx.send(f"❌ Could not read/parse `{att.filename}`: {e}")
        # accept {"tournaments":[...]} OR a single tournament object
        if isinstance(data, dict) and "tournaments" in data:
            incoming = data["tournaments"]
        elif isinstance(data, dict) and "server_id" in data and "schedule" in data:
            incoming = [data]
        else:
            return await ctx.send("❌ Not a valid tournament backup (expected a `tournaments` list or a tournament object).")
        if not incoming:
            return await ctx.send("❌ Backup contained no tournaments.")
        try:
            load_tournament_data_from_bin()  # pull current state first (don't clobber other servers)
            tours = DB_CACHE.get("tournaments", []) or []
            lines = []
            for td in incoming:
                sid, name = td.get("server_id"), td.get("name")
                tours = [x for x in tours if not (x.get("server_id") == sid and x.get("name") == name)]
                tours.append(td)
                done = sum(1 for m in td.get("schedule", []) if m.get("status") == "completed")
                lines.append(f"• **{name}** (server `{sid}`) — {done}/{len(td.get('schedule', []))} matches")
            DB_CACHE["tournaments"] = tours
            ok = save_tournament_data_to_bin()
            if not ok:
                return await ctx.send("❌ Wrote to memory but **MongoDB save failed** — check MONGO_URI / logs.")
            await ctx.send("✅ Restored & saved to MongoDB (also live in memory now):\n" + "\n".join(lines) +
                           "\n\nCheck with your tournament status command. `cv force_load` re-reads from Mongo to double-confirm.")
        except Exception as e:
            await ctx.send(f"❌ Restore failed: {e}")

    # Match recovery
    @commands.command(name="resume", aliases=["forcehub", "showhub", "rescue"],
                      help="Recover a stuck match — re-shows whatever prompt was lost (over hub / bowler pick / next batter / toss / innings end).\nUsage: resume")
    async def resume_match_cmd(self, ctx):
        """If a Discord hiccup (5xx, timeout) eats the message carrying the next
        view, the match is alive in memory but has no buttons. This inspects the
        match state and re-issues the correct prompt. Re-showing a prompt never
        mutates match state, so it's safe even if the old view is still alive."""
        channel = ctx.channel
        match = active_games.get(channel.id)

        # 90-over Test matches live in their own registry with their own hub.
        if match is None and channel.id in active_test_matches:
            tmatch = active_test_matches[channel.id]
            try:
                await channel.send("🛟 **Match recovered — here's your Test hub again:**",
                                   embed=render_test_embed(tmatch), view=TestSimView(tmatch, channel.id))
            except Exception as e:
                await channel.send(f"❌ Couldn't rebuild the Test hub: {e}")
            return

        if match is None:
            if channel.id in active_setups:
                return await ctx.send("ℹ️ A match **setup** is in progress here (no live match yet). Re-run the last setup step, or `/endmatch` to scrap it and start over.")
            return await ctx.send("❌ No active match in this channel.")

        allowed = (ctx.author.id in (match.p1_id, match.p2_id)
                   or ctx.author.id == getattr(match, "manager_id", None)
                   or ctx.author.id == ADMIN_DISCORD_ID
                   or (ctx.guild and ctx.author.guild_permissions.administrator))
        if not allowed:
            return await ctx.send("❌ Only the players, the match manager, or a server admin can resume this match.")

        # Toss never finished -> re-issue the right toss prompt.
        if match.current_innings is None or match.innings1 is None:
            if match.toss_winner is not None:
                return await channel.send(f"🛟 Re-sending the toss decision — <@{match.toss_winner}>, choose:",
                                          view=TossDecisionView(match))
            if not match.is_ai_game:
                return await channel.send(f"🛟 Re-sending the toss — <@{match.p2_id}>, call the coin!",
                                          view=TossCallView(match))
            return await ctx.send("❌ This match never got past the toss — use `/endmatch` and start again.")

        # A DRS window lost to the crash can't be rebuilt mid-flight - decision stands.
        if getattr(match, "pending_drs", False):
            match.pending_drs = False
            await channel.send("⚖️ The pending **DRS window was lost** in the crash — the on-field decision stands.")

        await channel.send("🛟 **Resuming the match from its last saved state…**")
        innings = match.current_innings

        # Innings/match already decided -> the end-of-innings router handles 1st->2nd
        # innings, super overs, and full completion (incl. tournament recording).
        target = getattr(match, "target", match.innings1.total_runs + 1) if match.current_innings_num == 2 else None
        if (innings.wickets >= _match_max_wickets(match)
                or innings.total_balls >= match.max_balls
                or (target is not None and innings.total_runs >= target)):
            return await handle_innings_end(channel, match)

        # A wicket was waiting on the next-batter pick.
        if getattr(match, "pending_next_batter", False):
            return await prompt_next_batter(channel, match)

        # Headless / full-sim flows: re-enter the sim loop where it stopped.
        if getattr(match, "sim_only", False) or match.simulation_mode == "whole_match":
            return await loop_entire_match_simulation(channel, match)

        # Interactive flows:
        if getattr(match, "_pending_bowler", None):
            return await prompt_over_pacing_hub(channel, match)          # bowler picked, hub message lost
        if innings.total_balls % 6 == 0 and (innings.over_log or not innings.current_bowler):
            match.over_completed = False                                  # over just ended (or fresh over, no bowler)
            return await prompt_bowler_then_hub(channel, match)
        return await run_interactive_delivery_sequence(channel, match)    # mid-over -> bowl the next ball

    # DSL (Dominators Super League) owner controls
    @commands.command(name="enable_dsl", help="[OWNER] Grant a server access to the Dominators Super League.\nUsage: enable_dsl [server_id]  (defaults to this server)")
    async def enable_dsl(self, ctx, server_id: str = None):
        if ctx.author.id != ADMIN_DISCORD_ID:
            return await ctx.send("❌ Owner only.")
        sid = str(server_id or ctx.guild.id)
        set_dsl_enabled(sid, True)
        await ctx.send(f"✅ **{DSL_CONFIG['display_name']}** enabled for server `{sid}`. Admins there can now run `cvt start dsl`.")

    @commands.command(name="disable_dsl", help="[OWNER] Revoke a server's Dominators Super League access.\nUsage: disable_dsl [server_id]")
    async def disable_dsl(self, ctx, server_id: str = None):
        if ctx.author.id != ADMIN_DISCORD_ID:
            return await ctx.send("❌ Owner only.")
        sid = str(server_id or ctx.guild.id)
        set_dsl_enabled(sid, False)
        await ctx.send(f"🚫 **{DSL_CONFIG['display_name']}** disabled for server `{sid}`. (An in-progress season is untouched.)")

    @commands.command(name="dsl_servers", help="[OWNER] List servers with DSL access.\nUsage: dsl_servers")
    async def dsl_servers_cmd(self, ctx):
        if ctx.author.id != ADMIN_DISCORD_ID:
            return await ctx.send("❌ Owner only.")
        rows = dsl_enabled_servers()
        if not rows:
            return await ctx.send("ℹ️ No servers have DSL access yet. `cv enable_dsl <server_id>` to grant one.")
        lines = []
        for sid, last in rows:
            g = self.bot.get_guild(int(sid)) if sid.isdigit() else None
            lines.append(f"• `{sid}`{f' — {g.name}' if g else ''} · last archived season: **{last or '—'}**")
        await ctx.send(f"🔵 **{DSL_CONFIG['display_name']} servers:**\n" + "\n".join(lines))

    @commands.command(name="dsl_reset", help="[OWNER] Factory-reset a server's DSL data (wipe TEST seasons): deletes its DSL tournament, archive files, and season counter. Access grant stays.\nUsage: dsl_reset [server_id]")
    async def dsl_reset_cmd(self, ctx, server_id: str = None):
        if ctx.author.id != ADMIN_DISCORD_ID:
            return await ctx.send("❌ Owner only.")
        sid = str(server_id or ctx.guild.id)
        from league.dsl_manager import list_season_archives
        n_arch = len(list_season_archives(sid))
        has_t = any(str(t.get("server_id")) == sid and t.get("tournament_type") == "dsl"
                    for t in DB_CACHE.get("tournaments", []))
        if not n_arch and not has_t:
            return await ctx.send(f"ℹ️ No DSL data found for server `{sid}` — already clean. Next `cvt start dsl` will be Season 1.")
        view = SquadConfirmView(ctx.author.id)
        prompt = await ctx.send(
            f"⚠️ **Factory-reset DSL for server `{sid}`?** This wipes:\n"
            f"• current DSL tournament: {'**yes**' if has_t else 'none'}\n"
            f"• archive files on this host: **{n_arch}**\n"
            f"• the Mongo season counter → next season becomes **S1**\n"
            f"(The access grant stays. Archives already committed to GitHub must be deleted from the repo separately!)",
            view=view)
        await view.wait()
        if not view.value:
            return await prompt.edit(content="❌ Reset cancelled — nothing touched.", view=None)
        removed_t, removed_a = reset_dsl_server(sid)
        await prompt.edit(content=(f"🧹 **DSL reset for `{sid}`** — removed {removed_t} tournament(s) and {removed_a} archive file(s); "
                                   f"season counter zeroed. Next `cvt start dsl` = a fresh **Season 1**.\n"
                                   f"⚠️ If test archives were committed to GitHub (`dsl_archive/{sid}_s*.json`), delete them from the repo too "
                                   f"or they'll come back on the next deploy."), view=None)

    @commands.command(name="upload_archive", aliases=["dsl_upload"], help="[OWNER] Restore a DSL season archive JSON (attach it, or reply to it).\nUsage: attach <server>_s<N>.json + upload_archive")
    async def upload_archive(self, ctx):
        if ctx.author.id != ADMIN_DISCORD_ID:
            return await ctx.send("❌ Owner only.")
        att = None
        if ctx.message.attachments:
            att = ctx.message.attachments[0]
        elif ctx.message.reference and ctx.message.reference.message_id:
            try:
                ref = await ctx.channel.fetch_message(ctx.message.reference.message_id)
                if ref.attachments:
                    att = ref.attachments[0]
            except Exception:
                pass
        if att is None:
            try:
                async for _m in ctx.channel.history(limit=25):
                    _js = [a for a in _m.attachments if a.filename.lower().endswith(".json")]
                    if _js:
                        att = _js[0]; break
            except Exception:
                pass
        if att is None:
            return await ctx.send("❌ No JSON file found — attach the archive with the command, or reply to the message carrying it.")
        try:
            raw = await att.read()
        except Exception as e:
            return await ctx.send(f"❌ Could not read `{att.filename}`: {e}")
        ok, msg = save_uploaded_archive(raw)
        await ctx.send(msg)

    @commands.command(name="sync_csv", aliases=["scsv"], help="[OWNER] Sync players from players_master.csv to DB.\nShows the new players first — toggle off any you don't want, then confirm.\nUsage: sync_csv")
    async def sync_csv(self, ctx):
        if ctx.author.id != ADMIN_DISCORD_ID:
            return await ctx.send("❌ Owner only.")
        if not os.path.exists("data/players_master.csv"):
            return await ctx.send("❌ `data/players_master.csv` not found.")
        try:
            existing = {p["name"].lower() for p in get_all_players()}
            new_players, seen = [], set()
            with open("data/players_master.csv", "r", encoding="utf-8-sig") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    name = row["Name"].strip()
                    key = name.lower()
                    if key in existing or key in seen:
                        continue
                    seen.add(key)
                    new_players.append({
                        "name": name,
                        "bat": int(row["Bat"]),
                        "bowl": int(row["Bowl"]),
                        "role": row["Role"].strip(),
                        "archetype": row["Archetype"].strip()
                    })
            if not new_players:
                return await ctx.send("✅ Sync complete! No new players found (database already up to date).")
            view = CSVSyncConfirmView(ctx, new_players)
            view.message = await ctx.send(embed=view.build_embed(), view=view)
        except Exception as e:
            await ctx.send(f"❌ Error during sync: {e}")

    @commands.command(name="set_user_tier", aliases=["sut"], help="[OWNER] Assign subscription tier to a user (optional auto-expiry).\nUsage: set_user_tier @user <tier> [days]\nTiers: Basic, Standard, Single, Server Pro, Career Beta, None\n`days` optional — e.g. `sut @user Standard 30` auto-removes after 30 days.")
    async def set_user_tier(self, ctx, user: discord.Member, *, tier: str):
        if ctx.author.id != ADMIN_DISCORD_ID:
            return await ctx.send("❌ Owner only.")
        valid = {
            "Basic": "Basic (1 Sim/Day | T20/ODI)",
            "Standard": "Standard (1 Sim/Day | All)",
            "Single": "Single (1 Match Consumable)",
            "Server Pro": "Server Pro (Unlimited on Silver/Diamond)",
            "Career Beta": "Career Beta (Career Mode Access)",
            "None": "None (Remove)"
        }
        # Optional trailing day-count: "Standard 30" -> 30-day auto-expiry.
        days = 0
        tier = tier.strip()
        parts = tier.rsplit(None, 1)
        if len(parts) == 2 and parts[1].isdigit():
            tier, days = parts[0].strip(), int(parts[1])
        if tier not in valid:
            return await ctx.send(f"❌ Invalid tier. Choose from: {', '.join(valid.keys())}")
        msg = update_user_tier(str(user.id), tier, valid[tier], user.mention, days=days)
        await ctx.send(msg)

    @commands.command(name="giveaway_tier", aliases=["gift_tier", "bulk_tier"], help="[OWNER] Grant a tier to MANY users at once, auto-expiring after N days.\nUsage: giveaway_tier <tier> <days> <@user/id> <@user/id> …  (days=0 = permanent)\nPaste any mix of mentions and raw IDs; they auto-remove when the days run out.")
    async def giveaway_tier(self, ctx, tier: str, days: int, *, targets: str = ""):
        if ctx.author.id != ADMIN_DISCORD_ID:
            return await ctx.send("❌ Owner only.")
        valid = {
            "Basic": "Basic (1 Sim/Day | T20/ODI)",
            "Standard": "Standard (1 Sim/Day | All)",
            "Single": "Single (1 Match Consumable)",
            "Server Pro": "Server Pro (Unlimited on Silver/Diamond)",
            "Career Beta": "Career Beta (Career Mode Access)",
        }
        tier = tier.strip()
        if tier not in valid:
            return await ctx.send(f"❌ Invalid tier. Choose from: {', '.join(valid)} *(cannot bulk-remove; use `None` via set_user_tier)*")
        # Collect user IDs from mentions AND raw numeric IDs pasted in the message.
        ids = {str(m.id) for m in ctx.message.mentions}
        ids |= set(re.findall(r'\b(\d{15,20})\b', targets))
        if not ids:
            return await ctx.send("❌ No users found. Paste mentions and/or raw user IDs after the tier & days.\n"
                                  "Example: `cv giveaway_tier Standard 30 @a @b 123456789012345678`")
        count, expires = bulk_grant_tier(ids, tier, days)
        if expires:
            await ctx.send(f"🎁 Granted **{valid[tier]}** to **{count}** user(s) — auto-expires on **{expires}** "
                           f"({days} days). No cleanup needed; they'll be removed automatically.\n"
                           f"-# Check anytime with `cv expiring_tiers`.")
        else:
            await ctx.send(f"🎁 Granted **{valid[tier]}** to **{count}** user(s) — **permanent** (no expiry).")

    @commands.command(name="expiring_tiers", aliases=["giveaway_list", "timed_tiers"], help="[OWNER] List every timed subscription and when it expires.\nUsage: expiring_tiers")
    async def expiring_tiers(self, ctx):
        if ctx.author.id != ADMIN_DISCORD_ID:
            return await ctx.send("❌ Owner only.")
        rows = list_expiring_subs()
        if not rows:
            return await ctx.send("📭 No timed subscriptions active.")
        from datetime import date
        lines = []
        for kind, ident, tier, exp in rows[:40]:
            left = (date.fromisoformat(exp) - date.today()).days
            who = f"<@{ident}>" if kind == "user" else f"Server `{ident}`"
            lines.append(f"• {who} — **{tier}** · expires **{exp}** ({left}d left)")
        e = discord.Embed(title=f"⏳ Timed Subscriptions ({len(rows)})",
                          description="\n".join(lines), color=discord.Color.blurple())
        if len(rows) > 40:
            e.set_footer(text=f"showing 40 of {len(rows)} · expired ones auto-remove on next use")
        await ctx.send(embed=e)

    @commands.command(name="list_subs", aliases=["subs", "all_subs"], help="[OWNER] List EVERY active subscription (users + servers) with index numbers.\nUsage: list_subs\nRemove one with `cv remove_sub <index>`.")
    async def list_subs(self, ctx):
        if ctx.author.id != ADMIN_DISCORD_ID:
            return await ctx.send("❌ Owner only.")
        rows = list_all_subs()
        if not rows:
            return await ctx.send("📭 No active subscriptions.")
        user_lines, server_lines = [], []
        for i, (kind, ident, tier, exp) in enumerate(rows, 1):
            tail = f" · expires **{exp}**" if exp else ""
            if kind == "user":
                user_lines.append(f"`{i:>2}.` <@{ident}> — **{tier}**{tail}")
            else:
                g = self.bot.get_guild(int(ident)) if ident.isdigit() else None
                who = f"**{g.name}** (`{ident}`)" if g else f"Server `{ident}`"
                server_lines.append(f"`{i:>2}.` {who} — **{tier}**{tail}")
        e = discord.Embed(title=f"📋 Active Subscriptions ({len(rows)})",
                          color=discord.Color.blurple())
        # Embed fields cap at 1024 chars - chunk each section into as many fields as needed.
        for label, lines in (("👤 Users", user_lines), ("🏠 Servers", server_lines)):
            if not lines:
                continue
            chunk, first = [], True
            for ln in lines:
                if sum(len(c) + 1 for c in chunk) + len(ln) > 1000:
                    e.add_field(name=label if first else f"{label} (cont.)",
                                value="\n".join(chunk), inline=False)
                    chunk, first = [], False
                chunk.append(ln)
            e.add_field(name=label if first else f"{label} (cont.)",
                        value="\n".join(chunk), inline=False)
        e.set_footer(text="Remove with: cv remove_sub <index>")
        await ctx.send(embed=e)

    @commands.command(name="remove_sub", aliases=["rsub", "del_sub"], help="[OWNER] Remove subscription(s) by index from `list_subs`.\nUsage: remove_sub <index> [index …]\nAll indexes are read from the SAME `cv subs` list — e.g. `cv rsub 1 3 5` removes rows 1, 3 and 5 as shown.")
    async def remove_sub(self, ctx, *indexes: int):
        if ctx.author.id != ADMIN_DISCORD_ID:
            return await ctx.send("❌ Owner only.")
        if not indexes:
            return await ctx.send("❌ Give at least one index — e.g. `cv rsub 2` or `cv rsub 1 3 5`. Run `cv subs` to see them.")
        removed, invalid = remove_subs_by_indexes(indexes)
        lines = []
        for kind, ident, tier, exp in removed:
            if kind == "user":
                who = f"<@{ident}>"
            else:
                g = self.bot.get_guild(int(ident)) if ident.isdigit() else None
                who = f"**{g.name}** (`{ident}`)" if g else f"Server `{ident}`"
            lines.append(f"🗑️ Removed **{tier}** subscription from {who}.")
        msg = "\n".join(lines) if lines else "❌ Nothing removed."
        if invalid:
            msg += f"\n⚠️ No subscription at index(es): {', '.join(map(str, invalid))} — run `cv subs` to check."
        await ctx.send(msg)

    @commands.command(name="set_server_tier", aliases=["sst"], help="[OWNER] Assign subscription tier to a server (optional auto-expiry).\nUsage: set_server_tier <server_id> <tier> [days]\nTiers: Bronze, Silver, Gold, Diamond, None\n`days` optional — e.g. `sst 12345 Gold 30` auto-removes after 30 days.")
    async def set_server_tier(self, ctx, server_id: str, *, tier: str):
        if ctx.author.id != ADMIN_DISCORD_ID:
            return await ctx.send("❌ Owner only.")
        valid = {
            "Bronze": "Bronze (10 Sims/Day | All)",
            "Silver": "Silver (Unlimited | All)",
            "Gold": "Gold (Tournament Only)",
            "Diamond": "Diamond (Unlimited + Tournament)",
            "None": "None (Remove)"
        }
        days = 0
        tier = tier.strip()
        parts = tier.rsplit(None, 1)
        if len(parts) == 2 and parts[1].isdigit():
            tier, days = parts[0].strip(), int(parts[1])
        if tier not in valid:
            return await ctx.send(f"❌ Invalid tier. Choose from: {', '.join(valid.keys())}")
        msg = update_server_tier(server_id, tier, valid[tier], days=days)
        await ctx.send(msg)

    @commands.command(name="authadmin", aliases=["aa"], help="[OWNER] Toggle admin permissions for a user.\nUsage: authadmin @user")
    async def authadmin(self, ctx, user: discord.Member):
        if ctx.author.id != ADMIN_DISCORD_ID:
            return await ctx.send("❌ Owner only.")
        added = toggle_auth_admin(str(user.id))
        if added:
            await ctx.send(f"✅ {user.mention} is now an **Admin** and can add/update players.")
        else:
            await ctx.send(f"🚫 Admin permissions **revoked** for {user.mention}.")

    @commands.command(name="toggle_channel_lock", aliases=["tcl"], help="[ADMIN] Lock or unlock matches in this channel.\nUsage: toggle_channel_lock")
    async def toggle_channel_lock(self, ctx):
        is_owner = ctx.author.id == ADMIN_DISCORD_ID
        has_perms = ctx.author.guild_permissions.manage_channels
        if not (is_owner or has_perms):
            return await ctx.send("❌ You need Manage Channels permission.")
        locked = toggle_restricted_channel(str(ctx.channel.id))
        if locked:
            await ctx.send("🔒 **Channel Locked:** Matches can no longer be played in this channel.")
        else:
            await ctx.send("🔓 **Channel Unlocked:** Matches can now be played in this channel.")

    @commands.command(name="toggle_ratings_channel", aliases=["trc"], help="[OWNER] Toggle player ratings visibility in this channel.\nUsage: toggle_ratings_channel")
    async def toggle_ratings_channel_cmd(self, ctx):
        if ctx.author.id != ADMIN_DISCORD_ID:
            return await ctx.send("❌ Owner only.")
        added = toggle_ratings_channel(str(ctx.channel.id))
        if added:
            await ctx.send("📊 **Ratings Channel Enabled:** Player ratings are now visible to everyone in this channel.")
        else:
            await ctx.send("🔒 **Ratings Channel Disabled:** Player ratings are now hidden in this channel.")

    @commands.command(name="set_log_channel", aliases=["slc"], help="[ADMIN] Set this channel as the match log channel for this server. Run again to disable.\nUsage: set_log_channel")
    async def set_log_channel_cmd(self, ctx):
        if not ctx.author.guild_permissions.administrator and ctx.author.id != ADMIN_DISCORD_ID:
            return await ctx.send("❌ Server Admins only.")
        if not ctx.guild:
            return await ctx.send("❌ This command must be used in a server.")
        server_id = str(ctx.guild.id)
        existing = get_match_log_channel(server_id)
        if existing and existing == str(ctx.channel.id):
            clear_match_log_channel(server_id)
            await ctx.send("🔕 **Match Log Disabled:** Final scorecards will no longer be logged in this server.")
        else:
            set_match_log_channel(server_id, str(ctx.channel.id))
            await ctx.send(f"📋 **Match Log Enabled:** Final scorecards for all matches on this server will be sent here.")

    @commands.command(name="squad", aliases=["sq"], help="View a team's tournament squad.\nUsage: squad [team_name]")
    async def squad_shortcut(self, ctx, *, team_name: str = None):
        server_id = str(ctx.guild.id)
        tourney = get_server_tournament(server_id)
        if not tourney: return await ctx.send("❌ No tournament exists on this server.")

        if ctx.message.mentions:
            owner = ctx.message.mentions[0]
            team = next((t for t in tourney["teams"] if t.get("owner_id") == str(owner.id)), None)
            if not team: return await ctx.send(f"❌ {owner.mention} does not own a team in this tournament.")
        elif team_name:
            team = next((t for t in tourney["teams"] if t["name"].lower() == team_name.lower()), None)
            if not team: return await ctx.send(f"❌ Team '{team_name}' not found.")
        else:
            team = next((t for t in tourney["teams"] if t["owner_id"] == str(ctx.author.id)), None)
            if not team: return await ctx.send("❌ You do not own a team. Please specify a `team_name`.")

        if not team.get("squad"):
            return await ctx.send(f"❌ **{team['name']}** has not submitted their squad yet.")

        batters, wks, all_rounders, bowlers = [], [], [], []
        for p in team["squad"]:
            role = p["role"]
            if "WK" in role: wks.append(p)
            elif "All-Rounder" in role: all_rounders.append(p)
            elif "Bowler" in role: bowlers.append(p)
            else: batters.append(p)

        batters.sort(key=lambda x: x["bat"], reverse=True)
        wks.sort(key=lambda x: x["bat"], reverse=True)
        all_rounders.sort(key=lambda x: (x["bat"] + x["bowl"]), reverse=True)
        bowlers.sort(key=lambda x: x["bowl"], reverse=True)

        embed = discord.Embed(title=f"📋 Squad: {team['name']}", description=f"👤 **Owner:** <@{team['owner_id']}> | **Total Players:** {len(team['squad'])}", color=discord.Color.blue())

        def _fmt(p, cat):
            style = p["role"].split("_", 1)[1].replace("_", " ") if "_" in p["role"] else ""
            if p.get("injured"):
                sev = p.get("injury_severity", 1)
                inj = f" 🚑 *(misses next {sev} team match{'es' if sev > 1 else ''})*"
            else:
                inj = ""
            if cat == "bat":  return f"**{p['name']}** *(Batter)*{inj}"
            elif cat == "wk": return f"**{p['name']}** *(WK Batter)*{inj}"
            elif cat == "ar": return f"**{p['name']}** *({style} All-Rounder)*{inj}" if style else f"**{p['name']}** *(All-Rounder)*{inj}"
            else:             return f"**{p['name']}** *({style} Bowler)*{inj}" if style else f"**{p['name']}** *(Bowler)*{inj}"

        if batters: embed.add_field(name="🏏 Batters", value="\n".join([_fmt(p, "bat") for p in batters]), inline=False)
        if wks: embed.add_field(name="🧤 Wicket-Keepers", value="\n".join([_fmt(p, "wk") for p in wks]), inline=False)
        if all_rounders: embed.add_field(name="⚔️ All-Rounders", value="\n".join([_fmt(p, "ar") for p in all_rounders]), inline=False)
        if bowlers: embed.add_field(name="🎯 Bowlers", value="\n".join([_fmt(p, "bowl") for p in bowlers]), inline=False)

        await ctx.send(embed=embed)

    # ============================= CAREER MODE =============================
    @commands.command(name="career", aliases=["careerhelp"], help="Career Mode help menu.\nUsage: career")
    async def career_help(self, ctx):
        if not _can_use_career(ctx):
            return await ctx.send(_CAREER_SOON)
        e = discord.Embed(
            title="🏏 Career Mode",
            description=("Build your own all-rounder and climb **Bronze → Diamond**.\n"
                         "**The loop:** `start_career` → `debut` → earn → `upgrade` → `create_match` 🏆"),
            color=discord.Color.blurple())
        e.add_field(name="🚀 Your Player",
                    value="`start_career` · `debut` · `profile` · `stats` · `leaderboard` · `rename`", inline=False)
        e.add_field(name="💰 Earn Coins",
                    value=("`daily` (🔥 streak bonus!) · `quests` (3/day) · `scenario` (solo practice)\n"
                           "Premium: `weekly` · `monthly`  ·  *bots/AI never pay*"), inline=False)
        e.add_field(name="📈 Improve",
                    value="`upgrade <power|control|bowling|stamina> [n]` · `balance`", inline=False)
        e.add_field(name="⚔️ Club Matches (PvP)",
                    value=("`create_match [overs]` · `joinmatch` · `addbot` · `lobby` · `swap` · `startmatch`\n"
                           "*Each player bats & bowls their own turn; captains pick openers/bowler.*"), inline=False)
        e.add_field(name="🏅 Tiers",
                    value=("Bronze 60 · Silver 69 · Gold 77 · Platinum 85 · Diamond 93\n"
                           "Auto Discord roles on promotion · `synctier` to re-apply"), inline=False)
        if ctx.author.id == ADMIN_DISCORD_ID or (ctx.guild and ctx.author.guild_permissions.administrator):
            e.add_field(name="🛠️ Admin",
                        value="`grant_premium @user [days]` · `delete_career [@user]`", inline=False)
        e.set_footer(text="Tip: prefix every command with cv  ·  e.g. cv start_career")
        await ctx.send(embed=e)

    @commands.command(name="start_career", aliases=["startcareer"], help="Create your career all-rounder.\nUsage: start_career")
    async def start_career(self, ctx):
        if not _can_use_career(ctx):
            return await ctx.send(_CAREER_SOON)
        if CM.get_career(ctx.author.id):
            return await ctx.send("❌ You already have a career! Use `cv profile` to view it.")
        view = career_ui.CareerCreateView(ctx.author.id, ctx.author.display_name)
        view.message = await ctx.send(
            f"🏏 **Start your Career, {ctx.author.display_name}!**\nEvery player is an **all-rounder** — "
            f"pick how you bowl and bat, then **name your player**:",
            view=view)

    @commands.command(name="profile", aliases=["card", "me"], help="View a player card.\nUsage: profile [@user]")
    async def profile(self, ctx, member: discord.Member = None):
        if not _can_use_career(ctx):
            return await ctx.send(_CAREER_SOON)
        target = member or ctx.author
        career = CM.get_career(target.id)
        if not career:
            who = "You don't" if target.id == ctx.author.id else f"{target.display_name} doesn't"
            return await ctx.send(f"❌ {who} have a career yet. Use `cv start_career` to begin.")
        await ctx.send(file=discord.File(career_ui.render_career_card(career), "career_card.png"))

    @commands.command(name="leaderboard", aliases=["lb", "top", "rankings"], help="Career rankings — OVR / coins / club wins.\nUsage: leaderboard")
    async def leaderboard(self, ctx):
        if not _can_use_career(ctx):
            return await ctx.send(_CAREER_SOON)
        scope = "server" if ctx.guild else "global"
        view = LeaderboardView(ctx.guild, ctx.author.id, "ovr", scope)
        await ctx.send(embed=_build_lb_embed(ctx.guild, "ovr", scope, ctx.author.id), view=view)

    @commands.command(name="stats", aliases=["careerstats", "cstats"], help="View lifetime career statistics.\nUsage: stats [@user]")
    async def stats(self, ctx, member: discord.Member = None):
        if not _can_use_career(ctx):
            return await ctx.send(_CAREER_SOON)
        target = member or ctx.author
        career = CM.get_career(target.id)
        if not career:
            who = "You don't" if target.id == ctx.author.id else f"{target.display_name} doesn't"
            return await ctx.send(f"❌ {who} have a career yet. Use `cv start_career` to begin.")

        s = career.get("stats", CM._blank_stats())
        b, bw, fd = s["bat"], s["bowl"], s["field"]
        bt = CM.BOWLING_TYPES[career["bowling_type"]]["label"]
        ms = CM.MINDSETS[career["mindset"]]["label"]

        # Batting derived
        avg = f"{b['runs'] / b['outs']:.1f}" if b["outs"] else ("—" if not b["runs"] else f"{b['runs']:.1f}")
        sr  = f"{b['runs'] / b['balls'] * 100:.1f}" if b["balls"] else "—"
        hs  = f"{b['hs']}" + ("" if b["outs"] >= b["innings"] else "")  # plain HS
        # Bowling derived
        overs = f"{bw['balls'] // 6}.{bw['balls'] % 6}"
        b_avg = f"{bw['runs'] / bw['wickets']:.1f}" if bw["wickets"] else "—"
        econ  = f"{bw['runs'] / (bw['balls'] / 6):.2f}" if bw["balls"] else "—"
        best  = f"{bw['best_w']}/{bw['best_r']}" if bw.get("best_w") else "—"

        def line(k, v): return f"{k:<13}{v}"
        bat_block = "```\n" + "\n".join([
            line("Matches", b["matches"]),
            line("Innings", f"{b['innings']}  (NO {b['not_outs']})"),
            line("Runs", b["runs"]),
            line("High Score", hs),
            line("Average", avg),
            line("Strike Rate", sr),
            line("4s / 6s", f"{b['fours']} / {b['sixes']}"),
            line("50s / 100s", f"{b['fifties']} / {b['hundreds']}"),
        ]) + "\n```"
        bowl_block = "```\n" + "\n".join([
            line("Overs", overs),
            line("Wickets", bw["wickets"]),
            line("Best", best),
            line("Average", b_avg),
            line("Economy", econ),
            line("Maidens", bw["maidens"]),
        ]) + "\n```"
        field_block = f"🧤 **Catches:** {fd['catches']}   ·   **Stumpings:** {fd['stumpings']}"

        e = discord.Embed(
            title=f"📊 {career.get('username', target.display_name)} — Career Statistics",
            description=f"**OVR {career['ovr']}** · {career['tier']}  ·  {ms} batter / {bt} bowler",
            color=_tier_embed_color(career["tier"]))
        e.add_field(name="🏏 Batting", value=bat_block, inline=True)
        e.add_field(name="🎳 Bowling", value=bowl_block, inline=True)
        e.add_field(name="🧤 Fielding", value=field_block, inline=False)
        if b["matches"] == 0:
            e.set_footer(text="No matches yet — stats fill up as you play. Start with cv debut.")
        else:
            e.set_footer(text="Career Mode · lifetime totals")
        await ctx.send(embed=e)

    @commands.command(name="synctier", aliases=["tierrole"], help="Re-apply your tier role in this server.\nUsage: synctier")
    async def synctier(self, ctx):
        if not _can_use_career(ctx):
            return await ctx.send(_CAREER_SOON)
        career = CM.get_career(ctx.author.id)
        if not career:
            return await ctx.send("❌ Start a career first: `cv start_career`.")
        if ctx.guild is None:
            return await ctx.send("❌ Run this in a server, not DMs.")
        role, note = await _sync_tier_role(ctx.guild, ctx.author, career)
        if role and not note:
            await ctx.send(f"🎖️ You're set as **{career['tier']}** — {role.mention} applied.")
        elif role and note:
            await ctx.send(f"⚠️ {note}")
        else:
            await ctx.send(f"⚠️ Couldn't assign your tier role. {note or ''}")

    @commands.command(name="debut", help="Play your Academy Trial to unlock your card.\nUsage: debut")
    async def debut(self, ctx):
        if not _can_use_career(ctx):
            return await ctx.send(_CAREER_SOON)
        career = CM.get_career(ctx.author.id)
        if not career:
            return await ctx.send("❌ Start a career first: `cv start_career`.")
        if career.get("debut_done"):
            return await ctx.send("✅ You've already made your debut! Use `cv profile`.")
        if is_channel_restricted(str(ctx.channel.id)):
            return await ctx.send("❌ Matches are **disabled** in this channel.")
        await start_debut_match(ctx.channel, ctx.author, career)

    @commands.command(name="daily", aliases=["d"], help="Claim your daily coins (24h).\nUsage: daily  (alias: cv d)")
    async def daily(self, ctx):
        if not _can_use_career(ctx):
            return await ctx.send(_CAREER_SOON)
        career = CM.get_career(ctx.author.id)
        if not career:
            return await ctx.send("❌ Start a career first: `cv start_career`.")
        amount, err = CM.claim_daily(career)
        if err:
            return await ctx.send(err)
        CM.quest_progress(career, "daily", 1)
        CM.async_save_career(career)
        streak = career.get("daily_streak", {}).get("count", 1)
        streak_line = f"  ·  🔥 **{streak}-day streak**" + (" (max bonus!)" if streak - 1 >= CM.STREAK_BONUS_CAP_DAYS else f" (+{min(max(0, streak - 1), CM.STREAK_BONUS_CAP_DAYS) * CM.STREAK_BONUS_PER_DAY} bonus)") if streak > 1 else ""
        await ctx.send(f"🪙 **+{amount} coins!** Daily claimed. Balance: **{career['coins']:,}**.{streak_line}\n-# 📜 Quest progress updated — check `cv quests`. Claim daily to build your 🔥 streak bonus.")

    @commands.command(name="quests", aliases=["quest", "dq"], help="View & claim your 3 daily quests.\nUsage: quests")
    async def quests(self, ctx):
        if not _can_use_career(ctx):
            return await ctx.send(_CAREER_SOON)
        career = CM.get_career(ctx.author.id)
        if not career:
            return await ctx.send("❌ Start a career first: `cv start_career`.")
        claimed = CM.claim_quests(career)              # pay out anything ready
        rows = CM.quest_status(career)
        e = discord.Embed(
            title="📜 Daily Quests",
            description="3 random quests each day. Progress comes from matches & `cv scenario`.",
            color=discord.Color.blurple())
        for q, cur, done, ready in rows:
            icon = "✅" if done else ("🎁" if ready else "⬜")
            state = "**CLAIMED**" if done else (f"{cur}/{q['target']}")
            e.add_field(name=f"{icon} {q['desc']}", value=f"{state}  ·  **{q['reward']}** 🪙", inline=False)
        if claimed:
            tot = sum(q["reward"] for q in claimed)
            e.set_footer(text=f"🎉 Claimed {len(claimed)} quest(s) just now: +{tot} coins! Balance {career['coins']:,}.")
        else:
            e.set_footer(text=f"Balance: {career['coins']:,} 🪙  ·  resets daily.")
        await ctx.send(embed=e)

    @commands.command(name="scenario", aliases=["practice", "scn"], help="Play a solo scenario for coins + quest progress.\nUsage: scenario")
    async def scenario(self, ctx):
        if not _can_use_career(ctx):
            return await ctx.send(_CAREER_SOON)
        career = CM.get_career(ctx.author.id)
        if not career:
            return await ctx.send("❌ Start a career first: `cv start_career`.")
        if not career.get("debut_done"):
            return await ctx.send("❌ Make your debut first: `cv debut`.")
        if is_channel_restricted(str(ctx.channel.id)):
            return await ctx.send("❌ Matches are **disabled** in this channel.")
        if ctx.channel.id in active_games or ctx.channel.id in active_setups:
            return await ctx.send("❌ A match or setup is already running in this channel.")
        fee = CM.SCENARIO_ENTRY_FEE
        if career["coins"] < fee:
            return await ctx.send(f"❌ You need **{fee}** 🪙 to enter a scenario (you have {career['coins']:,}). Earn coins via `cv daily` and quests.")
        done = CM.scenarios_done_today(career)
        capped = done >= CM.SCENARIO_DAILY_CAP
        cap_line = ("⚠️ Daily coin cap reached — this one pays **0 coins**, but still counts for quests.\n"
                    if capped else f"📊 **{CM.SCENARIO_DAILY_CAP - done}** paid scenarios left today\n")
        e = discord.Embed(
            title="🎯 Play a Scenario?",
            description=(f"A solo challenge vs an AI Challenge XI — beat it to **profit**.\n"
                         f"🎚️ **Pick a difficulty** below, then **Bat** or **Bowl**.\n"
                         f"🏏 **Bat:** chase a target before you're out.\n"
                         f"🎳 **Bowl:** take the required wickets in your overs.\n\n"
                         f"🎟️ **Entry fee:** {fee} 🪙   (you have {career['coins']:,})\n"
                         f"🪙 **Reward:** scaled to your performance & difficulty — clear it to beat the fee; a poor effort forfeits it.\n"
                         f"{cap_line}"
                         f"📜 Feeds your daily quests. *(Scenario stats stay separate from `cv stats`.)*"),
            color=discord.Color.blurple())
        await ctx.send(embed=e, view=ScenarioConfirmView(ctx.author.id))

    @commands.command(name="weekly", help="[Premium/Booster] Weekly coins + 5% boost.\nUsage: weekly")
    async def weekly(self, ctx):
        if not _can_use_career(ctx):
            return await ctx.send(_CAREER_SOON)
        career = CM.get_career(ctx.author.id)
        if not career:
            return await ctx.send("❌ Start a career first: `cv start_career`.")
        if not _is_premium(ctx):
            return await ctx.send("🔒 **Weekly** is a perk for **Premium members / server boosters**.")
        amt, err = CM.claim_weekly(career)
        if err:
            return await ctx.send(err)
        await ctx.send(f"🪙 **+{amt:,} coins** + a **5% coin boost for 7 days**! Balance: **{career['coins']:,}**.")

    @commands.command(name="monthly", help="[Premium/Booster] Monthly coins + title.\nUsage: monthly")
    async def monthly(self, ctx):
        if not _can_use_career(ctx):
            return await ctx.send(_CAREER_SOON)
        career = CM.get_career(ctx.author.id)
        if not career:
            return await ctx.send("❌ Start a career first: `cv start_career`.")
        if not _is_premium(ctx):
            return await ctx.send("🔒 **Monthly** is a perk for **Premium members / server boosters**.")
        amt, err = CM.claim_monthly(career)
        if err:
            return await ctx.send(err)
        await ctx.send(f"🪙 **+{amt:,} coins** and the **[Patron]** profile title unlocked! Balance: **{career['coins']:,}**.")

    @commands.command(name="balance", aliases=["bal", "coins"], help="Check your coin balance.\nUsage: balance")
    async def balance(self, ctx):
        if not _can_use_career(ctx):
            return await ctx.send(_CAREER_SOON)
        career = CM.get_career(ctx.author.id)
        if not career:
            return await ctx.send("❌ Start a career first: `cv start_career`.")
        await ctx.send(f"🪙 **{career.get('username', ctx.author.display_name)}** — **{career['coins']:,} coins**  ·  OVR {career['ovr']} ({career['tier']})")

    @commands.command(name="rename", aliases=["setname"],
                      help=f"Rename your career player.\nUsage: rename <new name>  (costs coins)")
    async def rename(self, ctx, *, new_name: str = None):
        if not _can_use_career(ctx):
            return await ctx.send(_CAREER_SOON)
        career = CM.get_career(ctx.author.id)
        if not career:
            return await ctx.send("❌ Start a career first: `cv start_career`.")
        cost = 0 if ctx.author.id == ADMIN_DISCORD_ID else CM.RENAME_COST
        if not new_name:
            return await ctx.send(f"✏️ Usage: `cv rename <new name>` (2–16 chars) — costs **{cost}** 🪙.")
        name = " ".join(str(new_name).split()).strip()[:16]
        if len(name) < 2:
            return await ctx.send("❌ Name must be **2–16** characters.")
        if name == career.get("username"):
            return await ctx.send("❌ That's already your name.")
        if career["coins"] < cost:
            return await ctx.send(f"❌ Renaming costs **{cost}** 🪙 — you have {career['coins']:,}.")
        old = career.get("username", "?")
        career["coins"] -= cost
        career["username"] = name
        CM.async_save_career(career)
        await ctx.send(f"✏️ **{old}** is now **{name}**!" + (f"  (−{cost} 🪙 · balance {career['coins']:,})" if cost else ""))

    @commands.command(name="upgrade", aliases=["ug", "train"], help="Spend coins to raise an attribute.\nUsage: upgrade <power|control|bowling|stamina> [amount]")
    async def upgrade(self, ctx, attribute: str = None, amount: int = 1):
        if not _can_use_career(ctx):
            return await ctx.send(_CAREER_SOON)
        career = CM.get_career(ctx.author.id)
        if not career:
            return await ctx.send("❌ Start a career first: `cv start_career`.")
        ICON = {"power": "🏏 POWER", "control": "🎯 CONTROL", "bowling": "🎳 BOWLING", "stamina": "🫀 STAMINA"}
        # Menu (no/invalid attribute)
        if not attribute or attribute.lower() not in CM.ATTRS:
            a = career["attributes"]
            coins = career["coins"]
            e = discord.Embed(
                title=f"🏋️ Training Ground — {career.get('username', ctx.author.display_name)}",
                description=(f"**OVR {career['ovr']}**  ·  {career['tier']}  ·  🪙 **{coins:,}** coins\n"
                             f"Raise an attribute with `cv upgrade <attribute> [amount]`."),
                color=_tier_embed_color(career["tier"]))
            for k in CM.ATTRS:
                v = a[k]
                if v >= 99:
                    val = "**MAXED** (99)"
                else:
                    cost = CM.upgrade_cost(v)
                    tick = "✅ affordable" if coins >= cost else f"❌ need {cost - coins:,} more"
                    val = f"next +1 → **{cost:,}** 🪙  ·  {tick}"
                e.add_field(name=f"{ICON[k]} — {v}", value=f"`{_attr_bar(v)}`\n{val}", inline=False)
            nt_name, nt_min = CM.next_tier_info(career["ovr"])
            if nt_name:
                e.set_footer(text=f"Next tier: {nt_name} at OVR {nt_min}  (+{nt_min - career['ovr']} to go)")
            else:
                e.set_footer(text="💎 Diamond — the summit. Keep pushing toward a 99 OVR.")
            return await ctx.send(embed=e)

        # Buy
        attribute = attribute.lower()
        amount = max(1, min(amount, 30))
        old_ovr, old_tier = career["ovr"], career["tier"]
        bought, spent, msg = CM.upgrade_attribute(career, attribute, amount)
        if bought == 0:
            return await ctx.send(f"❌ {msg}")
        v = career["attributes"][attribute]
        e = discord.Embed(
            title=f"💪 {ICON.get(attribute, attribute.upper())}  +{bought}",
            description=(f"`{_attr_bar(v)}`  **{v}**\n\n"
                         f"Spent **{spent:,}** 🪙  ·  OVR **{old_ovr} → {career['ovr']}**\n"
                         f"Balance: **{career['coins']:,}** 🪙"),
            color=_tier_embed_color(career["tier"]))
        if career["tier"] != old_tier:
            e.add_field(name="🏅 TIER UP!", value=f"**{old_tier} → {career['tier']}!**", inline=False)
            role, note = await _sync_tier_role(ctx.guild, ctx.author, career)
            if role:
                e.add_field(name="🎖️ Role Awarded", value=f"You've been given the {role.mention} role!", inline=False)
            elif note:
                e.add_field(name="⚠️ Role not assigned", value=note, inline=False)
        else:
            nt_name, nt_min = CM.next_tier_info(career["ovr"])
            if nt_name:
                e.set_footer(text=f"Next tier: {nt_name} at OVR {nt_min}  (+{nt_min - career['ovr']} to go)")
        await ctx.send(embed=e)

    @commands.command(name="delete_career", aliases=["delcareer", "resetcareer"], help="[DEV] Wipe a user's career.\nUsage: delete_career [@user]")
    async def delete_career(self, ctx, member: discord.Member = None):
        if not _can_use_career(ctx):
            return await ctx.send(_CAREER_SOON)
        target = member or ctx.author
        if not CM.get_career(target.id):
            return await ctx.send(f"❌ {target.display_name} has no career to delete.")
        ok = CM.delete_career(target.id)
        await ctx.send(f"🗑️ Wiped **{target.display_name}**'s career — they can `cv start_career` fresh."
                       if ok else "❌ Delete failed (see logs).")

    @commands.command(name="grant_premium", aliases=["givepremium", "setpremium"],
                      help="[ADMIN] Grant career premium (weekly/monthly access).\nUsage: grant_premium @user [days=30]  (days=0 revokes)")
    async def grant_premium(self, ctx, member: discord.Member = None, days: int = 30):
        if not _can_use_career(ctx):
            return await ctx.send(_CAREER_SOON)
        is_admin = (ctx.author.id == ADMIN_DISCORD_ID
                    or (ctx.guild and ctx.author.guild_permissions.administrator)
                    or str(ctx.author.id) in get_auth_admins())
        if not is_admin:
            return await ctx.send("❌ Admin or owner only.")
        if member is None:
            return await ctx.send("Usage: `cv grant_premium @user [days]` — days=0 to revoke.")
        career = CM.get_career(member.id)
        if not career:
            return await ctx.send(f"❌ {member.display_name} has no career yet (they must `cv start_career`).")
        days = max(0, min(int(days), 3650))
        CM.grant_premium(career, days)
        if days <= 0:
            return await ctx.send(f"🔓 Premium **revoked** for **{career.get('username', member.display_name)}**.")
        total_days = CM.premium_remaining(career) // 86400
        await ctx.send(
            f"⭐ **Premium granted** to {member.mention} (**+{days} days**, ~{total_days}d total remaining).\n"
            f"They can now claim `cv weekly` & `cv monthly`.")

    # ===================== CAREER CLUB MATCHES (Phase 4) =====================
    @commands.command(name="create_match", aliases=["creatematch", "cmatch", "hostmatch", "cm"],
                      help="Create a club-match lobby (PvP).\nUsage: create_match [overs]  (alias: cv cm)")
    async def create_match(self, ctx, overs: int = career_match.DEFAULT_OVERS if _CAREER_OK else 5):
        if not _can_use_career(ctx):
            return await ctx.send(_CAREER_SOON)
        career = CM.get_career(ctx.author.id)
        if not career:
            return await ctx.send("❌ Start a career first: `cv start_career`.")
        if not career.get("debut_done"):
            return await ctx.send("❌ Make your debut first: `cv debut`.")
        if is_channel_restricted(str(ctx.channel.id)):
            return await ctx.send("❌ Matches are **disabled** in this channel.")
        if ctx.channel.id in active_games or ctx.channel.id in active_setups:
            return await ctx.send("❌ A match or setup is already running in this channel.")
        if _get_live_lobby(ctx.channel.id):
            return await ctx.send("❌ A club-match lobby already exists here. Use `cv lobby` or `cv cancelmatch`.")
        lobby = career_match.ClubLobby(ctx.channel.id, ctx.author.id, career["username"], overs)
        career_match.LOBBIES[ctx.channel.id] = lobby
        await ctx.send(embed=_lobby_embed(lobby, "🏟️ Club Match — Lobby Open!"))

    @commands.command(name="joinmatch", aliases=["jm", "joinclub", "j", "join"],
                      help="Join the club-match lobby in this channel.\nUsage: joinmatch  (alias: cv j)")
    async def joinmatch(self, ctx):
        if not _can_use_career(ctx):
            return await ctx.send(_CAREER_SOON)
        lobby = _get_live_lobby(ctx.channel.id)
        if not lobby:
            return await ctx.send("❌ No lobby here. Create one with `cv create_match`.")
        if lobby.started:
            return await ctx.send("❌ This match has already started.")
        career = CM.get_career(ctx.author.id)
        if not career or not career.get("debut_done"):
            return await ctx.send("❌ You need a **debuted** career to join. `cv start_career` → `cv debut`.")
        ok, reason = lobby.add(ctx.author.id, career["username"])
        if not ok:
            return await ctx.send("❌ You're already in the lobby." if reason == "already_in"
                                  else "❌ The lobby is full (22 players max).")
        await ctx.send(embed=_lobby_embed(lobby, f"✅ {career['username']} joined the lobby!"))

    @commands.command(name="leavematch", aliases=["leaveclub", "lm", "leave"],
                      help="Leave the club-match lobby.\nUsage: leavematch  (alias: cv lm)")
    async def leavematch(self, ctx):
        if not _can_use_career(ctx):
            return await ctx.send(_CAREER_SOON)
        lobby = _get_live_lobby(ctx.channel.id)
        if not lobby:
            return await ctx.send("❌ No lobby here.")
        if lobby.started:
            return await ctx.send("❌ The match has already started.")
        if ctx.author.id == lobby.host_id:
            # Host leaving cancels the lobby.
            career_match.LOBBIES.pop(ctx.channel.id, None)
            return await ctx.send("🛑 Host left — lobby cancelled.")
        if not lobby.remove(ctx.author.id):
            return await ctx.send("❌ You're not in this lobby.")
        await ctx.send(embed=_lobby_embed(lobby, f"👋 {ctx.author.display_name} left the lobby."))

    @commands.command(name="lobby", aliases=["viewmatch", "mlobby", "l"],
                      help="Show the current club-match lobby.\nUsage: lobby  (alias: cv l)")
    async def lobby_view(self, ctx):
        if not _can_use_career(ctx):
            return await ctx.send(_CAREER_SOON)
        lobby = _get_live_lobby(ctx.channel.id)
        if not lobby:
            return await ctx.send("❌ No lobby here. Create one with `cv create_match`.")
        await ctx.send(embed=_lobby_embed(lobby, "🏟️ Club Match — Lobby"))

    @commands.command(name="addbot", aliases=["addai", "ab", "bot"],
                      help="Add an AI bot (avg of joined players) to the lobby (host).\nUsage: addbot  (alias: cv ab)")
    async def addbot(self, ctx):
        if not _can_use_career(ctx):
            return await ctx.send(_CAREER_SOON)
        lobby = _get_live_lobby(ctx.channel.id)
        if not lobby:
            return await ctx.send("❌ No lobby here. Create one with `cv create_match`.")
        if lobby.started:
            return await ctx.send("❌ The match has already started.")
        is_admin = ctx.author.id == ADMIN_DISCORD_ID or (ctx.guild and ctx.author.guild_permissions.administrator)
        if ctx.author.id != lobby.host_id and not is_admin:
            return await ctx.send("❌ Only the host (or an admin) can add bots.")
        ok, info = lobby.add_bot()
        if not ok:
            return await ctx.send("❌ The lobby is full (22 max)." if info == "full" else f"❌ {info}")
        await ctx.send(embed=_lobby_embed(lobby, f"🤖 **{info}** added (avg of joined players)"))

    @commands.command(name="swap", aliases=["swapplayer", "sw"],
                      help="Swap two players by their lobby number (host).\nUsage: swap <num1> <num2>  (alias: cv sw)")
    async def swap(self, ctx, a: int = None, b: int = None):
        if not _can_use_career(ctx):
            return await ctx.send(_CAREER_SOON)
        lobby = _get_live_lobby(ctx.channel.id)
        if not lobby:
            return await ctx.send("❌ No lobby here. Create one with `cv create_match`.")
        if lobby.started:
            return await ctx.send("❌ The match has already started.")
        is_admin = ctx.author.id == ADMIN_DISCORD_ID or (ctx.guild and ctx.author.guild_permissions.administrator)
        if ctx.author.id != lobby.host_id and not is_admin:
            return await ctx.send("❌ Only the host (or an admin) can swap players.")
        if a is None or b is None:
            return await ctx.send("Usage: `cv swap <num1> <num2>` — numbers are from `cv lobby`. Slot 1 of each team is captain.")
        ok, err = lobby.swap(a, b)
        if not ok:
            return await ctx.send(f"❌ {err}")
        await ctx.send(embed=_lobby_embed(lobby, f"🔁 Swapped #{a} ↔ #{b}"))

    @commands.command(name="cancelmatch", aliases=["endlobby", "cancel"],
                      help="Cancel the club-match lobby (host/admin).\nUsage: cancelmatch  (alias: cv cancel)")
    async def cancelmatch(self, ctx):
        if not _can_use_career(ctx):
            return await ctx.send(_CAREER_SOON)
        lobby = _get_live_lobby(ctx.channel.id)
        if not lobby:
            return await ctx.send("❌ No lobby here.")
        is_admin = ctx.author.id == ADMIN_DISCORD_ID or (ctx.guild and ctx.author.guild_permissions.administrator)
        if ctx.author.id != lobby.host_id and not is_admin:
            return await ctx.send("❌ Only the host (or an admin) can cancel the lobby.")
        career_match.LOBBIES.pop(ctx.channel.id, None)
        await ctx.send("🛑 Club-match lobby cancelled.")

    @commands.command(name="startmatch", aliases=["beginmatch", "sm"],
                      help="Start the club match — locks the teams (host).\nUsage: startmatch  (alias: cv sm)")
    async def startmatch(self, ctx):
        if not _can_use_career(ctx):
            return await ctx.send(_CAREER_SOON)
        lobby = _get_live_lobby(ctx.channel.id)
        if not lobby:
            return await ctx.send("❌ No lobby here. Create one with `cv create_match`.")
        is_admin = ctx.author.id == ADMIN_DISCORD_ID or (ctx.guild and ctx.author.guild_permissions.administrator)
        if ctx.author.id != lobby.host_id and not is_admin:
            return await ctx.send("❌ Only the host (or an admin) can start the match.")
        if not lobby.is_ready() or lobby.per_side() < 2:
            return await ctx.send("❌ Club matches need an **even** number of players, **min 2 per side (4 total)**. Get more to `cv joinmatch` (or `cv addbot`).")
        if lobby.human_count() < 2:
            return await ctx.send("❌ Need at least **2 real players** to start — bots don't count. Get another person to `cv joinmatch`.")
        if not lobby.each_side_has_human():
            return await ctx.send("❌ Each side needs at least **1 real player**. Use `cv swap` to put a human on each team.")
        if is_channel_restricted(str(ctx.channel.id)):
            return await ctx.send("❌ Matches are **disabled** in this channel.")
        if ctx.channel.id in active_games or ctx.channel.id in active_setups:
            return await ctx.send("❌ A match is already running in this channel.")
        lobby.started = True
        career_match.LOBBIES.pop(ctx.channel.id, None)
        await start_club_match(ctx.channel, lobby, ctx.author)


    # Tournament helpers
    def _is_tourney_mgr(self, ctx, tourney):
        """True if the caller may act as a tournament manager."""
        return ((ctx.author.id == ADMIN_DISCORD_ID)
                or ctx.author.guild_permissions.administrator
                or (str(ctx.author.id) in (tourney or {}).get("managers", [])))

    def _team_by_ref(self, ctx, tourney, team_name):
        """Resolve a team by @owner mention (preferred) or by name - so anywhere a
        <team_name> is accepted you can ping the owner instead. Returns team or None."""
        if ctx.message.mentions:
            oid = str(ctx.message.mentions[0].id)
            return next((t for t in tourney["teams"] if t.get("owner_id") == oid), None)
        if team_name:
            nm = re.sub(r"<@!?\d+>", "", team_name).strip()
            return next((t for t in tourney["teams"] if t["name"].lower() == nm.lower()), None)
        return None

    def _resolve_squad_target(self, ctx, tourney, args):
        """For owner/manager squad-edit commands, decide which team is being edited.
        - Ping an @owner -> that owner's team (caller must be that owner or a manager).
        - No mention -> the caller's own team.
        Returns (team, cleaned_args, error_msg); on error team is None and error_msg is set."""
        if ctx.message.mentions:
            owner = ctx.message.mentions[0]
            oid = str(owner.id)
            team = next((t for t in tourney["teams"] if t.get("owner_id") == oid), None)
            if not team:
                return None, args, f"❌ {owner.mention} does not own a team in this tournament."
            if oid != str(ctx.author.id) and not self._is_tourney_mgr(ctx, tourney):
                return None, args, "❌ Only managers can edit another team's squad."
            return team, re.sub(r"<@!?\d+>", "", args).strip(), None
        team = next((t for t in tourney["teams"] if t.get("owner_id") == str(ctx.author.id)), None)
        if not team:
            return None, args, "❌ You do not own a team. Managers: ping the team's `@owner` to target it."
        return team, args.strip(), None

    # Read-only tournament subcommands that should NOT be audit-logged.
    _TLOG_SKIP = {
        "squad", "status", "groups", "fixtures", "bracket", "homepitch", "stadiums",
        "leaderboard", "player_stats", "help_guide", "standings", "match_scorecard",
        "next_match", "help",
    }

    async def cog_after_invoke(self, ctx):
        """Audit-log every tournament-mutating command into the configured log channel."""
        try:
            cmd = ctx.command
            if not cmd or not cmd.parent or cmd.parent.name != "tournament":
                return
            if cmd.name in self._TLOG_SKIP or not ctx.guild:
                return
            tourney = get_server_tournament(str(ctx.guild.id))
            if not tourney:
                return
            ch_id = tourney.get("log_channel")
            if not ch_id:
                return
            ch = self.bot.get_channel(int(ch_id))
            if ch is None:
                return
            content = (ctx.message.content or "")[:900]
            embed = discord.Embed(
                description=(f"**Command:** `cvt {cmd.name}`\n"
                             f"**By:** {ctx.author.mention} in {ctx.channel.mention}\n"
                             f"```\n{content}\n```"),
                color=discord.Color.blurple(),
                timestamp=discord.utils.utcnow(),
            )
            embed.set_author(name=str(ctx.author), icon_url=ctx.author.display_avatar.url)
            await ch.send(embed=embed)
        except Exception:
            pass

    @commands.group(name="tournament", aliases=["t"], invoke_without_command=True, help="Main command for tournaments.\nUsage: tournament")
    async def tournament(self, ctx):
        await ctx.send_help(ctx.command)

    @tournament.command(name="create", help="[ADMIN] Create a new tournament.\nUsage: tournament create \"<name>\" <format> [event=roundrobin/double_rr/t20wc/acl] [impact_player=true/false] [injuries=true/false] [order=random/schedule/round]")
    async def t_create(self, ctx, name: str, format_str: str, *options: str):
        kwargs = { 'impact_player': False, 'injuries': False, 'conditions': 'manual', 'match_order': 'random', 'stadium_mode': 'random' }
        event_map = {
            "roundrobin": "round_robin", "round_robin": "round_robin", "rr": "round_robin",
            "double": "double_round_robin", "double_rr": "double_round_robin", "drr": "double_round_robin",
            "double_roundrobin": "double_round_robin", "double_round_robin": "double_round_robin",
            "t20wc": "t20_world_cup", "t20_world_cup": "t20_world_cup", "worldcup": "t20_world_cup", "wc": "t20_world_cup",
            "acl": "acl",
            "ccodi": "ccodi",   # 10 teams · 2 groups of 5 · round-wise double RR · top-2 -> KO1/KO2 -> Q1/Eliminator -> Q2 -> Final
            "ipl": "ipl", "indian_premier_league": "ipl",   # 10 teams · 2 groups of 5 · 14 matches each · combined table · top-4 playoffs
        }
        cond_map = {"manual": "manual", "auto": "auto", "home": "home", "home_pitch": "home", "homepitch": "home"}
        order_map = {"random": "random", "any": "random",
                     "schedule": "sequential", "strict": "sequential", "sequential": "sequential",
                     "round": "round", "rounds": "round"}
        t_type = "round_robin"
        for opt in options:
            try:
                key, value = opt.split('=', 1)
                if key == 'impact_player': kwargs['impact_player'] = to_bool(value)
                elif key == 'injuries': kwargs['injuries'] = to_bool(value)
                elif key in ('conditions', 'cond'):
                    cm = cond_map.get(value.strip().lower())
                    if not cm:
                        return await ctx.send(f"❌ Invalid conditions `{value}`. Use `manual`, `auto`, or `home`.")
                    kwargs['conditions'] = cm
                elif key in ('order', 'match_order'):
                    om = order_map.get(value.strip().lower())
                    if not om:
                        return await ctx.send(f"❌ Invalid order `{value}`. Use `random` (any match), `schedule` (strict order), or `round` (round by round).")
                    kwargs['match_order'] = om
                elif key in ('stadiums', 'stadium'):
                    sm = {"random": "random", "none": "random", "linked": "linked", "link": "linked", "home": "linked"}.get(value.strip().lower())
                    if not sm:
                        return await ctx.send(f"❌ Invalid stadiums `{value}`. Use `random` (default) or `linked` (home stadium with fixed pitch).")
                    kwargs['stadium_mode'] = sm
                elif key in ('event', 'event_type', 'type'):
                    et = event_map.get(value.strip().lower())
                    if not et:
                        return await ctx.send(f"❌ Invalid event `{value}`. Use `roundrobin`, `double_rr`, `t20wc`, `ccodi`, `ipl`, or `acl`.")
                    t_type = et
            except ValueError:
                return await ctx.send(f"❌ Invalid option format: `{opt}`. Must be `key=value`.")

        if not ctx.author.guild_permissions.administrator and ctx.author.id != ADMIN_DISCORD_ID:
            return await ctx.send("❌ Only Server Admins can initialize a tournament.")
        
        server_id = str(ctx.guild.id)
        _, _, _, s_tier, _ = get_tier_status(str(ctx.author.id), server_id)
        if s_tier not in ["Gold", "Diamond"]:
            return await ctx.send("❌ **Access Denied:** Only servers with **Gold** or **Diamond** tier can host tournaments.")

        if get_server_tournament(server_id):
            return await ctx.send("❌ A tournament already exists in this server!")
        
        format_map = {"t20": 20, "odi": 50, "test": 90}
        format_overs = format_map.get(format_str.lower())
        if not format_overs:
            return await ctx.send(f"❌ Invalid format '{format_str}'. Use one of: T20, ODI, Test.")

        t_data = {
            "server_id": server_id, "name": name, "managers": [str(ctx.author.id)], "teams": [],
            "status": "registration", "schedule": [], "current_match_idx": 0, "stats": {},
            "format_overs": format_overs, "min_squad": 11, "max_squad": 15,
            "impact_player": kwargs['impact_player'], "injuries_enabled": kwargs['injuries'],
            "tournament_type": t_type,
            "conditions_mode": ("home" if kwargs['stadium_mode'] == "linked" else kwargs['conditions']),
            "match_order": kwargs['match_order'],
            "stadium_mode": kwargs['stadium_mode'],
            "stadiums": default_stadium_pool(t_type),
        }
        save_tournament(t_data)
        type_label = {"double_round_robin": "Double Round Robin", "t20_world_cup": "T20 World Cup", "acl": "Akatsuki Cricket League", "ccodi": "CCODI", "dsl": "Dominators Super League", "rating": "Conquest League", "ipl": "Indian Premier League"}.get(t_type, "Round Robin")
        extra = ""
        if t_type == "acl":
            extra = "\n🔴 **ACL needs exactly 14 teams** — each plays every other once (91 matches) → Top 6 Playoffs → Super Cup."
            extra += f"\n🏟️ **Stadiums:** {len(DEFAULT_ACL_STADIUMS)} venues pre-loaded — fixtures get a random one at start. Edit with `cvt stadium_add`/`cvt stadiums` before `cvt start`."
        elif t_type == "t20_world_cup":
            extra = "\n⚠️ **T20 World Cup needs exactly 16 teams** in 4 groups of 4."
        elif t_type == "ccodi":
            extra = ("\n🏏 **CCODI needs exactly 10 teams** in 2 groups of 5 (`cvt add_team \"<team>\" @owner A/B`).\n"
                     "🗓️ Round-wise double round robin (10 rounds × 4 matches, one game per team per round) — "
                     "**5 stadiums** pre-loaded, no venue repeats within a round "
                     "(edit with `cvt stadium_add`/`cvt stadiums`).\n"
                     "🏆 Top 2 per group → KO1 (A1vB1) & KO2 (A2vB2) → Qualifier 1 / Eliminator → Qualifier 2 → Final.")
        elif t_type == "ipl":
            extra = ("\n🏆 **IPL needs exactly 10 teams** — no groups (`cvt add_team \"<team>\" @owner`).\n"
                     "📋 **Add order = seeding** — the draw pairs seeds 1&2, 3&4, … and each pair plays **twice** (the CSK-v-MI slot). Add your strongest sides first.\n"
                     "🗓️ **70 league matches, 14 per team** (5 opponents twice, 4 once) over **14 rounds of 5** — one game per team per round, 7 home & 7 away.\n"
                     "🏅 One combined table → Top 4: Qualifier 1 (1v2) · Eliminator (3v4) → Qualifier 2 → Final.")
        elif t_type == "double_round_robin":
            extra = "\n🔁 **Double Round Robin:** every team plays every other team twice, once each way."
        if kwargs['stadium_mode'] == "linked":
            extra += ("\n🏟️ **Stadiums: Linked** — every team sets a home ground with a FIXED pitch: "
                      "`cvt set_home_stadium \"<team>\" <stadium name> <pitch>`. Home fixtures are played there, "
                      "on that pitch. Can't start until all teams have one (`cvt home_stadiums` to check).")
        elif kwargs['conditions'] == "auto":
            extra += "\n🎲 **Conditions: Auto** — pitch & weather auto-assigned per match."
        elif kwargs['conditions'] == "home":
            extra += "\n🏟️ **Conditions: Home Pitch** — set each team's home pitch with `cvt set_home_pitch \"<team>\" <pitch>`. Can't start until **all** teams have one (`cvt homepitch` to check)."
        if kwargs['match_order'] != "random":
            extra += f"\n{MATCH_ORDER_LABELS[kwargs['match_order']]}"
        await ctx.send(f"🏆 **Tournament Created:** `{name}`  ·  {type_label}\nUse `cv tournament add_team` to get started!{extra}")

    @tournament.command(name="add_team", help="[MANAGER] Add a team and assign an Owner.\nUsage: tournament add_team \"<team_name>\" <@owner> [group]\nGroup (A/B/C/D) required for T20 World Cup & CCODI. Order doesn't matter — the @owner and group are found anywhere in the line.")
    async def t_add_team(self, ctx, *, args: str = ""):
        server_id = str(ctx.guild.id)
        tourney = get_server_tournament(server_id)
        if not tourney: return await ctx.send("❌ No tournament exists.")

        is_mgr = (ctx.author.id == ADMIN_DISCORD_ID) or (ctx.author.guild_permissions.administrator) or (str(ctx.author.id) in tourney.get("managers", []))
        if not is_mgr: return await ctx.send("❌ Managers only.")
        if tourney["status"] != "registration": return await ctx.send("❌ Cannot add teams after tournament has started.")

        # Flexible parsing: the @owner mention and the group letter are found ANYWHERE in
        # the line (quoted or unquoted team names, any argument order, `group=A` too).
        if not ctx.message.mentions:
            return await ctx.send("❌ Ping the team's owner — `cvt add_team \"<team>\" @owner [group]`.")
        owner = ctx.message.mentions[0]
        txt = re.sub(r"<@!?\d+>", "", args).strip()

        t_type = tourney.get("tournament_type", "round_robin")
        group_val = None
        if t_type in ("t20_world_cup", "ccodi"):
            valid_groups = ["A", "B"] if t_type == "ccodi" else ["A", "B", "C", "D"]
            cap = 5 if t_type == "ccodi" else 4
            gm = (re.search(r"(?:^|\s)group\s*[=:]?\s*([a-dA-D])(?=\s|$)", txt)
                  or re.search(r"(?:^|\s)([a-dA-D])\s*$", txt)
                  or re.search(r"^\s*([a-dA-D])(?=\s)", txt))
            if not gm:
                return await ctx.send(f"❌ **Group ({'/'.join(valid_groups)}) is required** — `cvt add_team \"<team>\" @owner <group>`.")
            group_val = gm.group(1).upper()
            txt = (txt[:gm.start()] + " " + txt[gm.end():]).strip()
            if group_val not in valid_groups:
                return await ctx.send(f"❌ Group must be **{', '.join(valid_groups)}**.")
            if sum(1 for t in tourney["teams"] if t.get("group") == group_val) >= cap:
                return await ctx.send(f"❌ Group **{group_val}** already has {cap} teams.")
        elif t_type == "ipl" and len(tourney["teams"]) >= 10:
            return await ctx.send("❌ **IPL is full** — 10 teams already added.")

        team_name = txt.strip().strip('"').strip("'").strip()[:30]
        if not team_name:
            return await ctx.send("❌ Missing the team name — `cvt add_team \"<team>\" @owner [group]`.")

        if any(t["name"].lower() == team_name.lower() for t in tourney["teams"]):
            return await ctx.send("❌ Team name already exists.")
        if any(t["owner_id"] == str(owner.id) for t in tourney["teams"]):
            return await ctx.send(f"❌ {owner.mention} already owns a team.")

        tourney["teams"].append({"name": team_name, "owner_id": str(owner.id), "squad": [], "group": group_val})
        save_tournament(tourney)
        grp_txt = f" · Group **{group_val}**" if group_val else ""
        if t_type == "ipl":
            # Add order = seed order, which is what pairs teams up in the fixture draw.
            n = len(tourney["teams"])
            grp_txt = f" · Seed **{n}**" + (f" · {10 - n} to go" if n < 10 else " · **squad complete**")
        await ctx.send(f"✅ Team **{team_name}**{grp_txt} added! Owner: {owner.mention}")

    @tournament.command(name="replace_player", help="[MANAGER] Replace a player in a team's squad.\nUsage: tournament replace_player \"<team>\" \"<out_player>\" \"<in_player>\"")
    async def t_replace_player(self, ctx, team_name: str, out_player: str, in_player: str):
        server_id = str(ctx.guild.id)
        tourney = get_server_tournament(server_id)
        
        is_mgr = (ctx.author.id == ADMIN_DISCORD_ID) or (ctx.author.guild_permissions.administrator) or (tourney and str(ctx.author.id) in tourney.get("managers", []))
        if not tourney: return await ctx.send("❌ No tournament exists.")
        if not is_mgr: return await ctx.send("❌ Managers only.")
        
        team = self._team_by_ref(ctx, tourney, team_name)
        if not team: return await ctx.send(f"❌ Team '{team_name}' not found (use the team name or ping its @owner).")
        if not team.get("squad"): return await ctx.send(f"❌ Team '{team['name']}' has no squad submitted yet.")
            
        old_p = next((p for p in team["squad"] if p["name"].lower() == out_player.lower()), None)
        if not old_p:
            close = difflib.get_close_matches(out_player, [p["name"] for p in team["squad"]], n=1, cutoff=0.5)
            if close: old_p = next(p for p in team["squad"] if p["name"] == close[0])
            else: return await ctx.send(f"❌ Player '{out_player}' not found in team '{team_name}'.")
            
        db_players = get_all_players()
        new_p = next((p for p in db_players if p["name"].lower() == in_player.lower()), None)
        if not new_p:
            close = difflib.get_close_matches(in_player, [p["name"] for p in db_players], n=1, cutoff=0.6)
            if close: new_p = next(p for p in db_players if p["name"] == close[0])
            else: return await ctx.send(f"❌ Player '{in_player}' not found in the global database.")
            
        if any(p["name"] == new_p["name"] for p in team["squad"]):
            return await ctx.send(f"❌ '{new_p['name']}' is already in the squad.")
            
        idx = team["squad"].index(old_p)
        team["squad"][idx] = new_p
        
        save_tournament(tourney)
        await ctx.send(f"✅ **Squad Updated for {team['name']}:**\n🔴 OUT: {old_p['name']}\n🟢 IN: {new_p['name']}")

    @tournament.command(name="submit_squad", aliases=["ss"], help="[OWNER/MANAGER] Submit a tournament squad (15 players).\nUsage: tournament submit_squad [team_name]")
    async def t_submit_squad(self, ctx, *, team_name: str = None):
        server_id = str(ctx.guild.id)
        tourney = get_server_tournament(server_id)
        
        if not tourney: return await ctx.send("❌ No tournament exists.")
        if tourney["status"] != "registration": return await ctx.send("❌ Registration is closed.")
        
        is_mgr = (ctx.author.id == ADMIN_DISCORD_ID) or (ctx.author.guild_permissions.administrator) or (str(ctx.author.id) in tourney.get("managers", []))
        
        if team_name:
            if not is_mgr:
                return await ctx.send("❌ Only Managers can submit for another team.")
            team = self._team_by_ref(ctx, tourney, team_name)
            if not team: return await ctx.send(f"❌ Team '{team_name}' not found (use the team name or ping its @owner).")
        else:
            team = next((t for t in tourney["teams"] if t["owner_id"] == str(ctx.author.id)), None)
            if not team: return await ctx.send("❌ You do not own a team. Managers must provide the `team_name` parameter.")
        
        min_s = tourney.get("min_squad", 11)
        max_s = tourney.get("max_squad", 15)
        await ctx.send(f"📋 Please reply to this message with the **{min_s} to {max_s} Player Squad** for **{team['name']}** (One player name per line). You have 3 minutes.")
        
        def check(m):
            return m.author.id == ctx.author.id and m.channel.id == ctx.channel.id
            
        try:
            msg = await self.bot.wait_for('message', timeout=180.0, check=check)
        except asyncio.TimeoutError:
            return await ctx.send("⏳ Time expired. Please run `cv tournament submit_squad` again.")
            
        db_players = get_all_players()
        db_map = {p["name"].lower(): p for p in db_players}
        db_names_list = list(db_map.keys())
        
        found_players = []
        missing = []
        fuzzy_corrections = []
        seen = set()

        lines = [l.strip() for l in msg.content.split("\n") if l.strip()]
        for line in lines[:(max_s + 3)]:
            q = line.lower()
            match = db_map.get(q)
            if not match:
                fuzz = difflib.get_close_matches(q, db_names_list, n=1, cutoff=0.6)
                if fuzz: match = db_map[fuzz[0]]

            if match:
                if match["name"] not in seen and len(found_players) < max_s:
                    found_players.append(match)
                    seen.add(match["name"])
                    if match["name"].lower() != q:
                        fuzzy_corrections.append((line, match["name"]))
            else:
                missing.append(line)

        if missing or len(found_players) < min_s:
            err = f"❌ **Roster Invalid ({len(found_players)}/{min_s} Minimum Found)**\n"
            if missing: err += f"Missing: {', '.join(missing)}\n"
            err += "Please fix the names and try again."
            return await ctx.send(err)

        view = SquadConfirmView(ctx.author.id)
        confirm_msg = await ctx.send(embed=build_squad_confirm_embed(team["name"], found_players, fuzzy_corrections), view=view)
        await view.wait()
        if view.value is None:
            return await confirm_msg.edit(content="⏳ Confirmation timed out — squad **not** saved. Run `cv tournament submit_squad` again.", embed=None, view=None)
        if view.value is False:
            return await confirm_msg.edit(content="❌ Squad submission cancelled. Run `cv tournament submit_squad` again to retry.", embed=None, view=None)

        team["squad"] = found_players
        save_tournament(tourney)
        await confirm_msg.edit(content=f"✅ **Squad Confirmed and Saved for {team['name']}!**\nRegistered {len(found_players)} players.", embed=None, view=None)

    @tournament.command(name="add_player", aliases=["addp", "ap"], help="[OWNER/MANAGER] Add player(s) to a squad before the tournament starts.\nUsage: tournament add_player [@owner] <player1>, <player2>, ...\n(Owners edit their own team; managers can target another team by pinging its @owner.)")
    async def t_add_player(self, ctx, *, args: str = ""):
        server_id = str(ctx.guild.id)
        tourney = get_server_tournament(server_id)
        if not tourney: return await ctx.send("❌ No tournament exists.")
        if tourney.get("status") != "registration":
            return await ctx.send("❌ Squads are locked — players can only be added before the tournament starts.")

        team, args, err = self._resolve_squad_target(ctx, tourney, args)
        if err: return await ctx.send(err)

        names = [n.strip() for n in args.split(",") if n.strip()]
        if not names:
            return await ctx.send("❌ Usage: `cv tournament add_player <player1>, <player2>, ...`")

        squad = team.get("squad") or []
        max_s = tourney.get("max_squad", 15)
        db_players = get_all_players()
        db_names = [p["name"] for p in db_players]

        added, already, notfound, full = [], [], [], []
        for nm in names:
            if len(squad) >= max_s:
                full.append(nm); continue
            p = next((x for x in db_players if x["name"].lower() == nm.lower()), None)
            if not p:
                close = difflib.get_close_matches(nm, db_names, n=1, cutoff=0.6)
                if close: p = next(x for x in db_players if x["name"] == close[0])
            if not p:
                notfound.append(nm); continue
            if any(x["name"] == p["name"] for x in squad):
                already.append(p["name"]); continue
            squad.append(p); added.append(p["name"])

        team["squad"] = squad
        save_tournament(tourney)

        lines = [f"📋 **{team['name']}** — {len(squad)}/{max_s} players"]
        if added: lines.append(f"🟢 Added: {', '.join(added)}")
        if already: lines.append(f"⚪ Already in squad: {', '.join(already)}")
        if notfound: lines.append(f"🔴 Not found in DB: {', '.join(notfound)}")
        if full: lines.append(f"🚫 Squad full ({max_s}) — skipped: {', '.join(full)}")
        await ctx.send("\n".join(lines))

    @tournament.command(name="remove_player", aliases=["removep", "rmp", "delp"], help="[OWNER/MANAGER] Remove player(s) from a squad before the tournament starts.\nUsage: tournament remove_player [@owner] <player1>, <player2>, ...\n(Owners edit their own team; managers can target another team by pinging its @owner.)")
    async def t_remove_player(self, ctx, *, args: str = ""):
        server_id = str(ctx.guild.id)
        tourney = get_server_tournament(server_id)
        if not tourney: return await ctx.send("❌ No tournament exists.")
        if tourney.get("status") != "registration":
            return await ctx.send("❌ Squads are locked — players can only be removed before the tournament starts.")

        team, args, err = self._resolve_squad_target(ctx, tourney, args)
        if err: return await ctx.send(err)
        squad = team.get("squad") or []
        if not squad:
            return await ctx.send(f"❌ **{team['name']}**'s squad is empty — nothing to remove.")

        names = [n.strip() for n in args.split(",") if n.strip()]
        if not names:
            return await ctx.send("❌ Usage: `cv tournament remove_player <player1>, <player2>, ...`")

        removed, notfound = [], []
        for nm in names:
            p = next((x for x in squad if x["name"].lower() == nm.lower()), None)
            if not p:
                close = difflib.get_close_matches(nm, [x["name"] for x in squad], n=1, cutoff=0.5)
                if close: p = next(x for x in squad if x["name"] == close[0])
            if not p:
                notfound.append(nm); continue
            squad.remove(p); removed.append(p["name"])

        team["squad"] = squad
        save_tournament(tourney)

        min_s = tourney.get("min_squad", 11)
        lines = [f"📋 **{team['name']}** — {len(squad)} players"]
        if removed: lines.append(f"🔴 Removed: {', '.join(removed)}")
        if notfound: lines.append(f"⚪ Not in squad: {', '.join(notfound)}")
        if len(squad) < min_s:
            lines.append(f"⚠️ Below minimum ({len(squad)}/{min_s}) — add more before `cv tournament start`.")
        await ctx.send("\n".join(lines))

    @tournament.command(name="squad", help="View a team's tournament squad and player ratings.\nUsage: tournament squad [team_name | @owner]")
    async def t_squad(self, ctx, *, team_name: str = None):
        server_id = str(ctx.guild.id)
        tourney = get_server_tournament(server_id)
        if not tourney: return await ctx.send("❌ No tournament exists.")

        if ctx.message.mentions:
            owner = ctx.message.mentions[0]
            team = next((t for t in tourney["teams"] if t["owner_id"] == str(owner.id)), None)
            if not team: return await ctx.send(f"❌ {owner.mention} does not own a team in this tournament.")
        elif team_name:
            team = next((t for t in tourney["teams"] if t["name"].lower() == team_name.lower()), None)
            if not team: return await ctx.send(f"❌ Team '{team_name}' not found.")
        else:
            team = next((t for t in tourney["teams"] if t["owner_id"] == str(ctx.author.id)), None)
            if not team: return await ctx.send("❌ You do not own a team. Specify a `team_name` or `@owner`.")
            
        if not team.get("squad"):
            return await ctx.send(f"❌ **{team['name']}** has not submitted their squad yet.")
            
        batters, wks, all_rounders, bowlers = [], [], [], []
        for p in team["squad"]:
            role = p["role"]
            if "WK" in role: wks.append(p)
            elif "All-Rounder" in role: all_rounders.append(p)
            elif "Bowler" in role: bowlers.append(p)
            else: batters.append(p)
            
        batters.sort(key=lambda x: x["bat"], reverse=True)
        wks.sort(key=lambda x: x["bat"], reverse=True)
        all_rounders.sort(key=lambda x: (x["bat"] + x["bowl"]), reverse=True)
        bowlers.sort(key=lambda x: x["bowl"], reverse=True)
        
        embed = discord.Embed(title=f"📋 Squad: {team['name']}", description=f"👤 **Owner:** <@{team['owner_id']}> | **Total Players:** {len(team['squad'])}", color=discord.Color.blue())
        
        def format_player(p, cat):
            arch = p["archetype"]
            style = p["role"].split("_", 1)[1].replace("_", " ") if "_" in p["role"] else ""
            if p.get("injured"):
                sev = p.get("injury_severity", 1)
                inj = f" 🚑 *(misses next {sev} team match{'es' if sev > 1 else ''})*"
            else:
                inj = ""
            if cat == "bat":  return f"**{p['name']}** *(Batter)*{inj}"
            elif cat == "wk": return f"**{p['name']}** *(WK Batter)*{inj}"
            elif cat == "ar": return f"**{p['name']}** *({style} All-Rounder)*{inj}" if style else f"**{p['name']}** *(All-Rounder)*{inj}"
            else:             return f"**{p['name']}** *({style} Bowler)*{inj}" if style else f"**{p['name']}** *(Bowler)*{inj}"

        if batters: embed.add_field(name="🏏 Batters", value="\n".join([format_player(p, "bat") for p in batters]), inline=False)
        if wks: embed.add_field(name="🧤 Wicket-Keepers", value="\n".join([format_player(p, "wk") for p in wks]), inline=False)
        if all_rounders: embed.add_field(name="⚔️ All-Rounders", value="\n".join([format_player(p, "ar") for p in all_rounders]), inline=False)
        if bowlers: embed.add_field(name="🎯 Bowlers", value="\n".join([format_player(p, "bowl") for p in bowlers]), inline=False)

        if tourney.get("tournament_type") == "t20_world_cup":
            embed.set_image(url="attachment://t20_banner.png")
            await ctx.send(embed=embed, file=discord.File("assets/t20_banner.png"))
        else:
            await ctx.send(embed=embed)

    @tournament.command(name="start", help="[MANAGER] Lock registration and generate schedule.\nUsage: tournament start [dsl]\n`cvt start dsl` creates a preconfigured Dominators Super League season (owner-granted servers only).")
    async def t_start(self, ctx, league: str = None):
        server_id = str(ctx.guild.id)
        tourney = get_server_tournament(server_id)

        # `cvt start dsl` with no tournament -> create the preconfigured DSL season.
        if league and league.strip().lower() in ("dsl", DSL_CONFIG["display_name"].lower()):
            if not tourney:
                if not is_dsl_enabled(server_id):
                    return await ctx.send(f"❌ **{DSL_CONFIG['display_name']}** is not enabled for this server. Contact the bot owner for access.")
                if not ctx.author.guild_permissions.administrator and ctx.author.id != ADMIN_DISCORD_ID:
                    return await ctx.send("❌ Only Server Admins can start a DSL season.")
                tourney = create_dsl_tournament(server_id, ctx.author.id)
                save_tournament(tourney)
                venues = " · ".join(DSL_CONFIG["venues"])
                return await ctx.send(
                    f"🔵 **{tourney['name']} — REGISTRATION OPEN!**\n"
                    f"Predecided format: **{DSL_CONFIG['format_overs']} overs** · **{DSL_CONFIG['team_count']} teams** · "
                    f"{'double' if DSL_CONFIG['double_round_robin'] else 'single'} round robin → Playoffs (2 Semi-Finals → Final)\n"
                    f"🏟️ **Venues:** {venues}\n\n"
                    f"**Next steps:**\n"
                    f"1️⃣ `cvt add_team \"<name>\" <@owner>` ×{DSL_CONFIG['team_count']}\n"
                    f"2️⃣ owners `cvt ss` to submit squads ({DSL_CONFIG['min_squad']}–{DSL_CONFIG['max_squad']} players)\n"
                    f"3️⃣ `cvt set_home_stadium \"<team>\" <venue>` for every team (home games use that ground's pitch!)\n"
                    f"4️⃣ `cvt start` to generate the fixtures"
                )
            elif not is_dsl_tournament(tourney):
                return await ctx.send("❌ A different tournament already exists in this server. Finish or `cvt force_delete` it first.")
            # DSL tournament already exists -> fall through to the normal start validation.

        # `cvt start rating` / `cvt start conquest` -> create the Conquest (rating) League.
        if league and league.strip().lower() in ("rating", "conquest", "cql", RATING_CONFIG["display_name"].lower()):
            if not tourney:
                if not ctx.author.guild_permissions.administrator and ctx.author.id != ADMIN_DISCORD_ID:
                    return await ctx.send("❌ Only Server Admins can start a Conquest League.")
                tourney = create_rating_tournament(server_id, ctx.author.id)
                save_tournament(tourney)
                return await ctx.send(
                    f"🟣 **{tourney['name']} ({RATING_CONFIG['short_name']}) — REGISTRATION OPEN!**\n"
                    f"An **open rating ladder** — challenge anyone, anytime; **skill climbs the ranks, not grinding**. "
                    f"No elimination; play ≥{RATING_CONFIG['min_games_qualify']} games to make the Top-{RATING_CONFIG['playoff_teams']} playoffs.\n\n"
                    f"**Next steps:**\n"
                    f"1️⃣ `cvt add_team \"<name>\" <@owner>` for each team\n"
                    f"2️⃣ owners `cvt ss` to submit squads (loaded from your auction)\n"
                    f"3️⃣ `cvt start` → the ladder goes live\n"
                    f"4️⃣ owners `cvt challenge \"<team>\"` to play · `cvt ratings` for the ladder\n"
                    f"⚔️ Trades (`cvt trade`) & performance boosts (`cvt boost`) are live all season."
                )
            elif not is_rating_tournament(tourney):
                return await ctx.send("❌ A different tournament already exists in this server. Finish or `cvt force_delete` it first.")

        is_mgr = (ctx.author.id == ADMIN_DISCORD_ID) or (ctx.author.guild_permissions.administrator) or (tourney and str(ctx.author.id) in tourney.get("managers", []))
        if not tourney: return await ctx.send("❌ No tournament exists. (DSL servers: `cvt start dsl` to open a season.)")
        if not is_mgr: return await ctx.send("❌ Managers only.")
        if tourney["status"] != "registration": return await ctx.send("❌ Tournament already started.")

        # Validate startability (team counts, squads, and home pitches if home mode).
        err = self._validate_startable(tourney)
        if err:
            return await ctx.send(err)

        # Conditions mode was chosen at create - generate straight away.
        await self._generate_and_start(ctx.channel, tourney)

    def _validate_startable(self, tourney):
        """Return an error string if the tournament can't start yet, else None."""
        min_s = tourney.get("min_squad", 11)
        t_type = tourney.get("tournament_type", "round_robin")
        if t_type == "t20_world_cup":
            for grp in ["A", "B", "C", "D"]:
                if len([t for t in tourney["teams"] if t.get("group") == grp]) != 4:
                    return f"❌ Group **{grp}** needs exactly 4 teams."
        elif t_type == "ccodi":
            for grp in ["A", "B"]:
                n = len([t for t in tourney["teams"] if t.get("group") == grp])
                if n != 5:
                    return f"❌ **CCODI:** Group **{grp}** needs exactly 5 teams (currently {n})."
        elif t_type == "ipl":
            if len(tourney["teams"]) != 10:
                return f"❌ **IPL requires exactly 10 teams** (currently {len(tourney['teams'])})."
        elif t_type == "acl":
            if len(tourney["teams"]) != 14:
                return f"❌ **ACL requires exactly 14 teams** (currently {len(tourney['teams'])})."
        elif t_type == "rating":
            if len(tourney["teams"]) < RATING_CONFIG["playoff_teams"]:
                return f"❌ **{RATING_CONFIG['short_name']} needs at least {RATING_CONFIG['playoff_teams']} teams** (currently {len(tourney['teams'])})."
        elif t_type == "dsl":
            want = DSL_CONFIG["team_count"]
            if len(tourney["teams"]) != want:
                return f"❌ **{DSL_CONFIG['short_name']} requires exactly {want} teams** (currently {len(tourney['teams'])})."
            missing = [t["name"] for t in tourney["teams"] if not canonical_venue(t.get("home_stadium"))]
            if missing:
                return ("❌ **Every team needs a home stadium** before the season can start.\n"
                        "Missing: " + ", ".join(f"**{m}**" for m in missing) +
                        "\nUse `cvt set_home_stadium \"<team>\" <venue>` · `cvt home_stadiums` to review.")
            if DSL_CONFIG["require_unique_venues"]:
                seen = {}
                for t in tourney["teams"]:
                    v = canonical_venue(t.get("home_stadium"))
                    if v in seen:
                        return f"❌ **{t['name']}** and **{seen[v]}** share **{v}** — every team needs its own home ground."
                    seen[v] = t["name"]
        else:
            if len(tourney["teams"]) < 2:
                return "❌ Need at least 2 teams."
        for t in tourney["teams"]:
            if len(t.get("squad", [])) < min_s:
                return f"❌ Team **{t['name']}** does not have a valid squad yet."
        # Linked stadiums: every team needs a home ground (which carries its fixed pitch).
        if tourney.get("stadium_mode") == "linked" and t_type != "dsl":
            missing = [t["name"] for t in tourney["teams"]
                       if not t.get("home_stadium") or not canonical_pitch(t.get("home_pitch"))]
            if missing:
                return ("❌ **Linked stadiums:** every team needs a home ground with its fixed pitch before starting.\n"
                        "Missing: " + ", ".join(f"**{m}**" for m in missing) +
                        "\nUse `cvt set_home_stadium \"<team>\" <stadium name> <pitch>` · `cvt home_stadiums` to review.")
        # Home-pitch mode: every team must have a home pitch set before starting.
        elif tourney.get("conditions_mode") == "home":
            missing = [t["name"] for t in tourney["teams"] if not canonical_pitch(t.get("home_pitch"))]
            if missing:
                return ("❌ **Home-Pitch mode:** set a home pitch for every team before starting.\n"
                        "Missing: " + ", ".join(f"**{m}**" for m in missing) +
                        "\nUse `cvt set_home_pitch \"<team>\" <pitch>` · `cvt homepitch` to review.")
        return None

    async def _generate_and_start(self, channel, tourney):
        """Generate the schedule for the tournament type, assign conditions per the chosen
        mode, mark active, and announce. Conditions mode must already be set on `tourney`."""
        t_type = tourney.get("tournament_type", "round_robin")

        # Conquest League: no schedule - open play. Just go live.
        if t_type == "rating":
            tourney["status"] = "active"
            tourney["current_match_idx"] = 0
            save_tournament(tourney)
            n = len(tourney["teams"])
            return await channel.send(
                f"🟣 **{tourney['name']} IS LIVE!** — {n} teams on the ladder, all at **{RATING_CONFIG['base_rating']}**.\n"
                f"⚔️ Owners: `cvt challenge \"<team>\"` to play anyone available · `cvt ratings` for the live ladder.\n"
                f"📈 Beat higher-rated teams to climb fast; farming weak teams is pointless. Play ≥{RATING_CONFIG['min_games_qualify']} games to qualify.\n"
                f"💪 Weak squads earn more **credits** — spend them on `cvt boost`. Deal via `cvt trade`.\n"
                f"🏆 When you're ready to finish, a manager runs `cvt end_league` → Top-{RATING_CONFIG['playoff_teams']} playoffs."
            )

        if t_type == "t20_world_cup":
            teams_by_group = {"A": [], "B": [], "C": [], "D": []}
            for t in tourney["teams"]:
                teams_by_group[t["group"]].append(t["name"])
            all_matches = []
            for group, group_teams in teams_by_group.items():
                teams = list(group_teams)
                random.shuffle(teams)
                n = len(teams)
                for r in range(n - 1):
                    for i in range(n // 2):
                        t1, t2 = teams[i], teams[n - 1 - i]
                        all_matches.append({
                            "round": f"Group {group}", "stage": "group", "group": group,
                            "group_round": r + 1,
                            "team1": t1 if r % 2 == 0 else t2,
                            "team2": t2 if r % 2 == 0 else t1,
                            "status": "pending", "result": None,
                        })
                    teams.insert(1, teams.pop())
            random.shuffle(all_matches)
            schedule = [dict(m, match_id=i + 1) for i, m in enumerate(all_matches)]
            tourney["schedule"] = schedule
            tourney["status"] = "active"
            tourney["current_match_idx"] = 0
            assign_tournament_conditions(tourney)
            save_tournament(tourney)
            groups_txt = "\n".join(f"**Group {g}:** {' · '.join(teams_by_group[g])}" for g in "ABCD")
            return await channel.send(f"🏆 **TOURNAMENT STARTED: {tourney['name']}!** — T20 World Cup\n{groups_txt}\nGenerated **{len(schedule)} group stage matches** (interleaved){self._cond_note(tourney)}. Use `cv tournament status` to view fixtures!")

        # IPL - 10 teams, no groups: one flat 70-match league (14 per team) laid out as
        # 14 rounds of 5, then a Top-4 playoff off the single combined table.
        if t_type == "ipl":
            teams = [t["name"] for t in tourney["teams"]]   # add order = seeding
            tourney["schedule"] = generate_ipl_schedule(teams)
            tourney["status"] = "active"
            tourney["current_match_idx"] = 0
            assign_tournament_conditions(tourney)   # round-aware venues + conditions
            save_tournament(tourney)
            return await channel.send(
                f"🏆 **IPL STARTED: {tourney['name']}!**\n"
                f"Generated **{len(tourney['schedule'])} league matches** — **14 per team**, across "
                f"**14 rounds of 5** (every team plays once a round, 7 home & 7 away)"
                f"{self._cond_note(tourney)}.\n"
                f"Just like the real thing: each side meets **5 opponents twice** and the other "
                f"**4 once** — one combined points table, no groups.\n"
                f"🏅 Top 4 → **Qualifier 1** (1v2) & **Eliminator** (3v4) → **Qualifier 2** → **Final**.\n"
                f"`cv tournament status` for fixtures · `cv tournament standings` for the table."
            )

        # CCODI - 2 groups of 5, DOUBLE round robin organised into ROUNDS (each team
        # plays at most once per round; venues never repeat within a round) -> top 2
        # per group -> IPL-style knockout ladder (KO1/KO2 -> Q1/Eliminator -> Q2 -> Final).
        if t_type == "ccodi":
            teams_by_group = {"A": [], "B": []}
            for t in tourney["teams"]:
                teams_by_group.setdefault(t.get("group"), []).append(t["name"])
            # Circle method per group -> per-round pair lists (5 teams + BYE = 5 rounds
            # a leg, 2 matches + 1 bye per round). Leg 2 mirrors leg 1 home/away.
            rounds_by_group = {}
            for group in ("A", "B"):
                base = list(teams_by_group[group])
                random.shuffle(base)
                teams = base + (["BYE"] if len(base) % 2 else [])
                n = len(teams)
                leg1 = []
                for r in range(n - 1):
                    pairs = []
                    for i in range(n // 2):
                        a, b = teams[i], teams[n - 1 - i]
                        if a == "BYE" or b == "BYE":
                            continue
                        pairs.append((a, b) if r % 2 == 0 else (b, a))
                    leg1.append(pairs)
                    teams.insert(1, teams.pop())
                rounds_by_group[group] = leg1 + [[(b, a) for (a, b) in pairs] for pairs in leg1]
            # Global rounds: round r = group A's round r + group B's round r (4 matches).
            schedule, mid = [], 1
            for r in range(len(rounds_by_group["A"])):
                rnd_matches = ([("A", a, b) for a, b in rounds_by_group["A"][r]] +
                               [("B", a, b) for a, b in rounds_by_group["B"][r]])
                random.shuffle(rnd_matches)
                for group, a, b in rnd_matches:
                    schedule.append({
                        "match_id": mid, "round": r + 1, "stage": "group", "group": group,
                        "team1": a, "team2": b, "status": "pending", "result": None,
                    })
                    mid += 1
            tourney["schedule"] = schedule
            tourney["status"] = "active"
            tourney["current_match_idx"] = 0
            assign_tournament_conditions(tourney)   # round-aware venues + conditions
            save_tournament(tourney)
            n_rounds = len(rounds_by_group["A"])
            groups_txt = "\n".join(f"**Group {g}:** {' · '.join(teams_by_group[g])}" for g in ("A", "B"))
            return await channel.send(
                f"🏏 **CCODI STARTED: {tourney['name']}!**\n{groups_txt}\n"
                f"Generated **{len(schedule)} group matches** across **{n_rounds} rounds** — each group is a "
                f"**double round robin**; every team plays at most once per round and no venue repeats within a "
                f"round{self._cond_note(tourney)}.\n"
                f"🏆 Top 2 per group → **Knockout 1** (A1 v B1) & **Knockout 2** (A2 v B2) → winners to "
                f"**Qualifier 1**, losers to the **Eliminator** → **Qualifier 2** → **Final**.\n"
                f"`cv tournament status` for the round-wise fixtures · `cv tournament standings` for the tables."
            )

        # DSL - Dominators Super League (home/away league on home grounds -> Top-4 Playoffs)
        if t_type == "dsl":
            tourney["schedule"] = dsl_generate_league_schedule(tourney)
            tourney["status"] = "active"
            tourney["current_match_idx"] = 0
            assign_tournament_conditions(tourney)   # venues (home grounds) + venue-profile pitches
            save_tournament(tourney)
            per_team = sum(1 for m in tourney["schedule"]
                           if m["team1"] == tourney["teams"][0]["name"] or m["team2"] == tourney["teams"][0]["name"])
            return await channel.send(
                f"🔵 **{tourney['name'].upper()} IS UNDERWAY!**\n"
                f"Generated **{len(tourney['schedule'])} league matches** ({per_team} per team, "
                f"{'home & away' if DSL_CONFIG['double_round_robin'] else 'single round robin'}) — "
                f"every home game is played at the home team's ground, and **the venue decides the pitch**.\n"
                f"📋 Owners: `cvt fixtures` to see your matches · `cvt play <id>` to launch them.\n"
                f"🏆 When the league ends, the **Top-4 Playoffs** (Semi-Final 1: 1v4 · Semi-Final 2: 2v3 → Final) generate automatically."
            )

        # ACL - Akatsuki Cricket League (14-team single round robin -> Playoffs -> Super Cup)
        if t_type == "acl":
            teams = [t["name"] for t in tourney["teams"]]
            n = len(teams)  # 14, even - no BYE needed
            matchups = []
            for r in range(n - 1):  # 13 rounds
                round_matches = []
                for i in range(n // 2):  # 7 matches/round
                    t1, t2 = teams[i], teams[n - 1 - i]
                    round_matches.append((t1, t2) if r % 2 == 0 else (t2, t1))
                random.shuffle(round_matches)
                for m in round_matches:
                    matchups.append({"round": r + 1, "team1": m[0], "team2": m[1]})
                teams.insert(1, teams.pop())
            schedule = [{"match_id": i + 1, "round": m["round"], "stage": "league",
                         "team1": m["team1"], "team2": m["team2"], "status": "pending", "result": None}
                        for i, m in enumerate(matchups)]
            tourney["schedule"] = schedule
            tourney["status"] = "active"
            tourney["current_match_idx"] = 0
            assign_tournament_conditions(tourney)
            save_tournament(tourney)
            return await channel.send(
                f"🔴 **AKATSUKI CRICKET LEAGUE — {tourney['name']} HAS BEGUN!**\n"
                f"Generated **{len(schedule)} league matches** ({n} teams, single round robin){self._cond_note(tourney)}.\n"
                f"📋 Owners: use `cv tournament fixtures` to see your matches · `cv tournament standings` for the table.\n"
                f"🏆 After all 91 league games, a Manager runs `cv tournament generate_playoffs` to start the Top-6 Playoffs."
            )

        # Double Round Robin
        if t_type == "double_round_robin":
            teams = [t["name"] for t in tourney["teams"]]
            schedule = generate_round_robin_schedule(teams, double=True, stage="league")
            tourney["schedule"] = schedule
            tourney["status"] = "active"
            tourney["current_match_idx"] = 0
            assign_tournament_conditions(tourney)
            save_tournament(tourney)
            per_team = max(0, (len(teams) - 1) * 2)
            return await channel.send(
                f"🔁 **DOUBLE ROUND ROBIN STARTED: {tourney['name']}!**\n"
                f"Generated **{len(schedule)} matches** ({len(teams)} teams, {per_team} per team): "
                f"everyone plays everyone twice, once each way{self._cond_note(tourney)}.\n"
                f"Use `cv tournament status` to view fixtures · `cv tournament standings` for the table."
            )

        # Round Robin
        teams = [t["name"] for t in tourney["teams"]]
        schedule = generate_round_robin_schedule(teams)
        tourney["schedule"] = schedule
        tourney["status"] = "active"
        tourney["current_match_idx"] = 0
        assign_tournament_conditions(tourney)
        save_tournament(tourney)
        await channel.send(f"🏆 **TOURNAMENT STARTED: {tourney['name']}!**\nGenerated **{len(schedule)} matches** in the Round Robin stage{self._cond_note(tourney)}.\nUse `cv tournament status` to view it!")

    @staticmethod
    def _cond_note(tourney):
        m = tourney.get("conditions_mode", "manual")
        return {"auto": " · 🎲 auto conditions", "home": " · 🏟️ home-pitch conditions",
                "manual": " · 🎛️ manual conditions"}.get(m, "")

    @tournament.command(name="status", aliases=["sched"], help="View the current tournament schedule and standings.\nUsage: tournament status")
    async def t_status(self, ctx):
        server_id = str(ctx.guild.id)
        tourney = get_server_tournament(server_id)
        if not tourney:
            return await ctx.send("❌ No tournament exists in this server.")

        if tourney["status"] == "registration":
            t_type = tourney.get("tournament_type", "round_robin")
            type_label = {"double_round_robin": "Double Round Robin", "t20_world_cup": "T20 World Cup", "acl": "Akatsuki Cricket League", "ccodi": "CCODI", "dsl": "Dominators Super League", "rating": "Conquest League", "ipl": "Indian Premier League"}.get(t_type, "Round Robin")
            embed = discord.Embed(title=f"🏆 {tourney['name']}", color=discord.Color.gold())
            cmode = tourney.get("conditions_mode", "manual")
            cmode_txt = {"auto": "🎲 Auto conditions", "home": "🏟️ Home-Pitch conditions"}.get(cmode, "🎛️ Manual conditions")
            embed.description = f"📝 **Registration Phase** · {type_label} · {cmode_txt}"
            is_home = (cmode == "home")
            team_lines = []
            for t in tourney["teams"]:
                grp = f" · Group **{t['group']}**" if t.get("group") else ""
                hp = ""
                if is_home:
                    cp = canonical_pitch(t.get("home_pitch"))
                    hp = f" · 🏟️ **{cp}**" if cp else " · 🏟️ ❌ *no home pitch*"
                team_lines.append(f"• **{t['name']}**{grp} (<@{t['owner_id']}>) — {len(t.get('squad', []))}/{tourney.get('max_squad', 15)} players{hp}")
            if is_home:
                set_n = sum(1 for t in tourney["teams"] if canonical_pitch(t.get("home_pitch")))
                embed.add_field(name="🏟️ Home Pitches", value=f"**{set_n}/{len(tourney['teams'])}** teams set — all required before `cvt start`. Use `cvt set_home_pitch \"<team>\" <pitch>`.", inline=False)
            if not team_lines:
                embed.add_field(name="Registered Teams", value="No teams yet.", inline=False)
            else:
                chunks, cur, cur_len = [], [], 0
                for line in team_lines:
                    if cur_len + len(line) + 1 > 1020 and cur:
                        chunks.append("\n".join(cur)); cur, cur_len = [], 0
                    cur.append(line); cur_len += len(line) + 1
                if cur: chunks.append("\n".join(cur))
                for i, chunk in enumerate(chunks):
                    embed.add_field(name="Registered Teams" if i == 0 else "​", value=chunk, inline=False)
            embed.set_footer(text=f"Format: {tourney.get('format_overs', 20)} overs · Squad: {tourney.get('min_squad', 11)}–{tourney.get('max_squad', 15)} players")
            if t_type == "t20_world_cup":
                embed.set_image(url="attachment://t20_banner.png")
                return await ctx.send(embed=embed, file=discord.File("assets/t20_banner.png"))
            return await ctx.send(embed=embed)

        t_type = tourney.get("tournament_type", "round_robin")
        _ccodi_roundwise = (t_type == "ccodi" and
                            any(isinstance(m.get("round"), int) for m in tourney.get("schedule", [])))
        if _ccodi_roundwise:
            # New CCODI seasons: one page per round (4 matches, distinct venues) + knockouts.
            pages = _build_ccodi_round_pages(tourney)
            hint = "Round-wise fixtures — every team plays once per round. `cvt groups` for group views."
        elif t_type in ("t20_world_cup", "ccodi"):
            pages = _build_flat_pages(tourney)
            hint = "Use `cvt groups` to view fixtures by group."
        else:
            pages = _build_status_pages(tourney)
            hint = ("14 rounds — every team plays once per round, 14 matches each."
                    if t_type == "ipl" else None)

        if not pages:
            return await ctx.send("❌ No schedule generated yet. Run `cv tournament start` first.")

        view = TournamentStatusView(tourney, pages)
        embed = _build_status_embed(tourney, pages[view.idx])

        if tourney["status"] == "completed":
            final = next((m for m in tourney.get("schedule", []) if m.get("round") == "Final"), None)
            winner = final["result"]["winner"] if final and final.get("result") else "TBD"
            embed.description = f"👑 **Champions: {winner}**"
        elif hint:
            embed.set_footer(text=hint)

        if t_type == "t20_world_cup":
            embed.set_image(url="attachment://t20_banner.png")
            await ctx.send(embed=embed, view=view, file=discord.File("assets/t20_banner.png"))
        else:
            await ctx.send(embed=embed, view=view)

        if t_type == "t20_world_cup":
            ko_buf = generate_t20wc_knockouts_image(tourney)
            if ko_buf:
                await ctx.send(file=discord.File(ko_buf, filename="knockouts.png"))

    @tournament.command(name="groups", help="[T20 WC] View schedule grouped by stage/group.\nUsage: tournament groups")
    async def t_groups(self, ctx):
        server_id = str(ctx.guild.id)
        tourney = get_server_tournament(server_id)
        if not tourney:
            return await ctx.send("❌ No tournament exists in this server.")
        if tourney.get("tournament_type") not in ("t20_world_cup", "ccodi"):
            return await ctx.send("❌ This command is only available for group-based tournaments (T20 World Cup / CCODI).")
        if tourney["status"] == "registration":
            return await ctx.send("❌ Tournament hasn't started yet. Use `cv tournament status` to see registration info.")

        pages = _build_status_pages(tourney)
        if not pages:
            return await ctx.send("❌ No schedule generated yet. Run `cv tournament start` first.")

        view = TournamentStatusView(tourney, pages)
        embed = _build_status_embed(tourney, pages[view.idx])

        if tourney["status"] == "completed":
            final = next((m for m in tourney.get("schedule", []) if m.get("round") == "Final"), None)
            winner = final["result"]["winner"] if final and final.get("result") else "TBD"
            embed.description = f"👑 **Champions: {winner}**"

        embed.set_image(url="attachment://t20_banner.png")
        await ctx.send(embed=embed, view=view, file=discord.File("assets/t20_banner.png"))

    @tournament.command(name="set_schedule", help="[OWNER] Set a custom fixture order for the tournament.\nUsage: tournament set_schedule")
    async def t_set_schedule(self, ctx):
        if ctx.author.id != ADMIN_DISCORD_ID:
            return await ctx.send("❌ Owner only.")
        server_id = str(ctx.guild.id)
        tourney = get_server_tournament(server_id)
        if not tourney:
            return await ctx.send("❌ No tournament found.")
        if tourney["status"] == "registration":
            return await ctx.send("❌ Generate the schedule first (`cv tournament start`).")
        schedule = tourney.get("schedule", [])
        if not schedule:
            return await ctx.send("❌ No schedule exists yet.")

        # Show current fixture list for reference
        ref_lines = []
        for m in sorted(schedule, key=lambda x: x["match_id"]):
            ms, mg = m.get("stage", ""), m.get("group", "")
            if ms == "group" and mg:       tag = f"[G{mg}]"
            elif ms == "super8" and mg:    tag = f"[S8{mg}]"
            else:                          tag = "[KO]"
            icon = "✅" if m["status"] == "completed" else "⏳"
            ref_lines.append(f"`#{m['match_id']}` {tag} {m['team1']} vs {m['team2']} {icon}")

        # Send in chunks of 20 to avoid 2000-char limit
        for i in range(0, len(ref_lines), 20):
            await ctx.send("\n".join(ref_lines[i:i + 20]))

        await ctx.send(
            "📋 **Reply with the desired fixture order** — one match per line:\n"
            "`Team1 vs Team2`\n"
            "Matches not listed will be appended at the end in their current order. You have **5 minutes**."
        )

        def check(m):
            return m.author.id == ctx.author.id and m.channel.id == ctx.channel.id

        try:
            msg = await self.bot.wait_for("message", timeout=300.0, check=check)
        except asyncio.TimeoutError:
            return await ctx.send("⏳ Timed out. Schedule unchanged.")

        import re as _re
        lines = [l.strip() for l in msg.content.split("\n") if l.strip()]

        # Build lookup: frozenset of lowercased names -> match entry
        schedule_map = {}
        for m in schedule:
            schedule_map[frozenset([m["team1"].lower(), m["team2"].lower()])] = m

        all_team_names = list({name.lower() for m in schedule for name in [m["team1"], m["team2"]]})

        ordered, used_ids, errors = [], set(), []
        for line in lines:
            parts = _re.split(r'\s+vs\s+', line, maxsplit=1, flags=_re.IGNORECASE)
            if len(parts) != 2:
                errors.append(f"⚠️ Couldn't parse: `{line}`")
                continue
            t1_raw, t2_raw = parts[0].strip().lower(), parts[1].strip().lower()
            key = frozenset([t1_raw, t2_raw])
            match = schedule_map.get(key)
            if not match:
                t1c = difflib.get_close_matches(t1_raw, all_team_names, n=1, cutoff=0.6)
                t2c = difflib.get_close_matches(t2_raw, all_team_names, n=1, cutoff=0.6)
                if t1c and t2c:
                    match = schedule_map.get(frozenset([t1c[0], t2c[0]]))
            if not match:
                errors.append(f"❌ Not found: `{line}`")
                continue
            mid = match["match_id"]
            if mid in used_ids:
                errors.append(f"⚠️ Duplicate (skipped): `{line}`")
                continue
            ordered.append(match)
            used_ids.add(mid)

        if errors:
            await ctx.send("\n".join(errors[:10]) + (f"\n_…{len(errors) - 10} more errors_" if len(errors) > 10 else ""))

        if not ordered:
            return await ctx.send("❌ No valid matches found. Schedule unchanged.")

        # Append unspecified matches in their current order
        remainder = [m for m in sorted(schedule, key=lambda x: x["match_id"]) if m["match_id"] not in used_ids]
        new_schedule = ordered + remainder

        # Reassign match_ids so array index == match_id - 1 (invariant required by match engine)
        for i, m in enumerate(new_schedule):
            m["match_id"] = i + 1

        tourney["schedule"] = new_schedule
        save_tournament(tourney)

        conf = []
        for m in new_schedule[:15]:
            ms, mg = m.get("stage", ""), m.get("group", "")
            if ms == "group" and mg:    tag = f"[G{mg}]"
            elif ms == "super8" and mg: tag = f"[S8{mg}]"
            else:                       tag = "[KO]"
            icon = "✅" if m["status"] == "completed" else "⏳"
            conf.append(f"`#{m['match_id']}` {tag} {m['team1']} vs {m['team2']} {icon}")
        if len(new_schedule) > 15:
            conf.append(f"_…and {len(new_schedule) - 15} more_")
        await ctx.send(f"✅ **Schedule updated!** ({len(ordered)} reordered · {len(remainder)} appended)\n" + "\n".join(conf))

    @tournament.command(name="play_next", aliases=["pn"], help="[MANAGER] Launch the next pending tournament match.\nUsage: tournament play_next")
    async def t_play_next(self, ctx):
        server_id = str(ctx.guild.id)
        tourney = get_server_tournament(server_id)
        
        is_mgr = (ctx.author.id == ADMIN_DISCORD_ID) or (ctx.author.guild_permissions.administrator) or (tourney and str(ctx.author.id) in tourney.get("managers", []))
        if not tourney: return await ctx.send("❌ No tournament exists.")
        if not is_mgr: return await ctx.send("❌ Managers only.")
        if tourney["status"] != "active": return await ctx.send("❌ Tournament is not active.")
        
        schedule = tourney.get("schedule", [])
        current_round = next((m["round"] for m in schedule if m["status"] == "pending"), None)
        
        pending = next((m for m in schedule if m["status"] == "pending" and m["round"] == current_round), None)
        if not pending:
            return await ctx.send("🏆 All matches have been completed!")
        ok, gate_msg = match_order_gate(tourney, pending)
        if not ok:
            return await ctx.send(gate_msg)

        r_label = f"Round {current_round}" if isinstance(current_round, int) else current_round
        await ctx.send(f"🚀 **Launching {r_label} — Match {pending['match_id']}...**")
        self.bot.dispatch("start_tournament_match", ctx.channel, ctx.author.id, tourney, pending)

    @tournament.command(name="play", help="[MANAGER/OWNER] Launch a match by ID. Owners can launch any of their own matches.\nUsage: tournament play <match_id>")
    async def t_play_match(self, ctx, match_id: int):
        server_id = str(ctx.guild.id)
        tourney = get_server_tournament(server_id)
        if not tourney: return await ctx.send("❌ No tournament exists.")
        if tourney["status"] != "active": return await ctx.send("❌ Tournament is not active.")

        match = next((m for m in tourney.get("schedule", []) if m["match_id"] == match_id), None)
        if not match:
            return await ctx.send(f"❌ Match ID {match_id} does not exist.")

        is_mgr = (ctx.author.id == ADMIN_DISCORD_ID) or (ctx.author.guild_permissions.administrator) or (str(ctx.author.id) in tourney.get("managers", []))
        # Managers launch any match; owners launch any match their team is in.
        if not owner_can_launch(tourney, match, ctx.author.id, is_mgr):
            return await ctx.send("❌ You can only launch matches **your team** is playing in. (Managers can launch any.)")
        if match["status"] == "locked":
            return await ctx.send(f"❌ Match {match_id} isn't ready — its teams depend on earlier results. Try `cvt bracket`.")
        if match["status"] != "pending":
            return await ctx.send(f"❌ Match {match_id} is already completed.")
        ok, gate_msg = match_order_gate(tourney, match)
        if not ok:
            return await ctx.send(gate_msg)

        r_label = f"Round {match['round']}" if isinstance(match['round'], int) else match['round']
        await ctx.send(f"🚀 **Launching Match {match['match_id']} ({r_label})...**\n<@{ctx.author.id}> — make sure your opponent is here to pick their XI.")
        self.bot.dispatch("start_tournament_match", ctx.channel, ctx.author.id, tourney, match)

    @tournament.command(name="fixtures", aliases=["fx"], help="View a team's fixtures & results (defaults to your own team).\nUsage: tournament fixtures [team name]")
    async def t_fixtures(self, ctx, *, team_name: str = None):
        server_id = str(ctx.guild.id)
        tourney = get_server_tournament(server_id)
        if not tourney: return await ctx.send("❌ No tournament exists.")
        if team_name:
            team = self._team_by_ref(ctx, tourney, team_name)
            if not team:
                import difflib as _dl
                close = _dl.get_close_matches(team_name, [t["name"] for t in tourney["teams"]], n=1, cutoff=0.5)
                team = next((t for t in tourney["teams"] if t["name"] == close[0]), None) if close else None
            if not team:
                return await ctx.send(f"❌ Team **{team_name}** not found.")
        else:
            team = next((t for t in tourney["teams"] if t.get("owner_id") == str(ctx.author.id)), None)
            if not team:
                return await ctx.send("❌ You don't own a team here. Specify a team: `cvt fixtures <team name>`.")
        from league.tournament_manager import build_fixtures_view
        view = build_fixtures_view(tourney, team["name"])
        embed = build_team_fixtures_embed(tourney, team["name"])
        if view:
            await ctx.send(embed=embed, view=view)
        else:
            await ctx.send(embed=embed)

    # TBECS innings-break ads
    # Managed per server; shown at every innings end of a TBECS match (see
    # _maybe_send_tbecs_ads). Manager/admin/owner gated. Store is per-server, so these
    # work whether or not a TBECS tournament is currently live in the server.
    def _is_ad_manager(self, ctx):
        tourney = get_server_tournament(str(ctx.guild.id))
        return ((ctx.author.id == ADMIN_DISCORD_ID)
                or ctx.author.guild_permissions.administrator
                or (tourney and str(ctx.author.id) in tourney.get("managers", [])))

    @tournament.command(name="tbecs_ad", aliases=["ad_add", "tbecs_ad_add", "add_ad"],
                        help="[MANAGER] Add a TBECS ad shown at every innings end.\n"
                             "Ads can be multi-line with links. Three ways to add:\n"
                             "• Reply to the ad message with `cvt tbecs_ad`\n"
                             "• Bare `cvt tbecs_ad` → the bot asks for the ad as your next message\n"
                             "• `cvt tbecs_ad <text>` for a quick one-liner")
    async def t_tbecs_ad(self, ctx, *, message: str = None):
        if not self._is_ad_manager(ctx):
            return await ctx.send("❌ Managers only.")
        from league.tbecs_manager import add_tbecs_ad
        # Reply-capture: `cvt tbecs_ad` as a reply saves the replied-to message verbatim
        # the reliable path for pre-composed multi-line ads with links/formatting.
        if message is None and ctx.message.reference and ctx.message.reference.message_id:
            try:
                ref = await ctx.channel.fetch_message(ctx.message.reference.message_id)
                message = ref.content
            except Exception:
                return await ctx.send("❌ Couldn't read the replied-to message — try again.")
        # Next-message capture: bare `cvt tbecs_ad` waits for the full ad as its own message.
        if message is None:
            await ctx.send("📝 Send the ad as your **next message** — multiple lines and links are fine. (5 min, `cancel` to abort)")
            def check(m):
                return m.author.id == ctx.author.id and m.channel.id == ctx.channel.id
            try:
                reply = await self.bot.wait_for("message", timeout=300.0, check=check)
            except asyncio.TimeoutError:
                return await ctx.send("⌛ Timed out — no ad added.")
            if reply.content.strip().lower() == "cancel":
                return await ctx.send("❌ Cancelled — no ad added.")
            message = reply.content
        _ok, msg, _ = add_tbecs_ad(str(ctx.guild.id), message)
        await ctx.send(msg)

    @tournament.command(name="tbecs_ads", aliases=["ad_list", "tbecs_ad_list", "ads"],
                        help="[MANAGER] List this server's TBECS ads.\nUsage: tournament tbecs_ads")
    async def t_tbecs_ads(self, ctx):
        if not self._is_ad_manager(ctx):
            return await ctx.send("❌ Managers only.")
        from league.tbecs_manager import get_tbecs_ads
        ads = get_tbecs_ads(str(ctx.guild.id))
        if not ads:
            return await ctx.send("ℹ️ No TBECS ads set. Add one with `cvt tbecs_ad <message>`.")
        buf = f"📢 **TBECS Ads ({len(ads)})** — shown at every innings end:\n"
        for i, a in enumerate(ads, 1):
            flat = " ⏎ ".join(x.strip() for x in a.splitlines() if x.strip())   # one line per ad in the list
            ln = f"**#{i}** — {flat if len(flat) <= 150 else flat[:147] + '…'}\n"
            if len(buf) + len(ln) > 1990:   # Discord's 2000-char message limit
                await ctx.send(buf)
                buf = ""
            buf += ln
        if buf.strip():
            await ctx.send(buf)

    @tournament.command(name="tbecs_ad_remove", aliases=["ad_remove", "ad_del", "remove_ad"],
                        help="[MANAGER] Remove a TBECS ad by its number.\nUsage: tournament tbecs_ad_remove <n>")
    async def t_tbecs_ad_remove(self, ctx, index: int):
        if not self._is_ad_manager(ctx):
            return await ctx.send("❌ Managers only.")
        from league.tbecs_manager import remove_tbecs_ad
        _ok, msg = remove_tbecs_ad(str(ctx.guild.id), index)
        await ctx.send(msg)

    @tournament.command(name="tbecs_ad_clear", aliases=["ad_clear", "clear_ads"],
                        help="[MANAGER] Remove ALL TBECS ads for this server.\nUsage: tournament tbecs_ad_clear")
    async def t_tbecs_ad_clear(self, ctx):
        if not self._is_ad_manager(ctx):
            return await ctx.send("❌ Managers only.")
        from league.tbecs_manager import clear_tbecs_ads
        _n, msg = clear_tbecs_ads(str(ctx.guild.id))
        await ctx.send(msg)

    @tournament.command(name="tbecs_ad_preview", aliases=["ad_preview", "preview_ads"],
                        help="[MANAGER] Preview the ads exactly as they'll appear at an innings end.\nUsage: tournament tbecs_ad_preview")
    async def t_tbecs_ad_preview(self, ctx):
        if not self._is_ad_manager(ctx):
            return await ctx.send("❌ Managers only.")
        from league.tbecs_manager import build_tbecs_ad_embeds
        embeds = build_tbecs_ad_embeds(str(ctx.guild.id))
        if not embeds:
            return await ctx.send("ℹ️ No TBECS ads set. Add one with `cvt tbecs_ad`.")
        await ctx.send("👀 **Preview** — this is what shows at every innings end:", embeds=embeds[:10])

    @tournament.command(name="force_result", help="[MANAGER] Manually set match result.\nUsage: tournament force_result <id> <winner> <t1_r> <t1_w> <t1_b> <t2_r> <t2_w> <t2_b>")
    async def t_force_result(self, ctx, match_id: int, winner_team: str, t1_runs: int, t1_wkts: int, t1_balls: int, t2_runs: int, t2_wkts: int, t2_balls: int):
        server_id = str(ctx.guild.id)
        tourney = get_server_tournament(server_id)
        
        is_mgr = (ctx.author.id == ADMIN_DISCORD_ID) or (ctx.author.guild_permissions.administrator) or (tourney and str(ctx.author.id) in tourney.get("managers", []))
        if not tourney: return await ctx.send("❌ No tournament exists.")
        if not is_mgr: return await ctx.send("❌ Managers only.")
        
        match_data = next((m for m in tourney.get("schedule", []) if m["match_id"] == match_id), None)
        if not match_data: return await ctx.send(f"❌ Match ID {match_id} does not exist.")
        if match_data["status"] == "completed": return await ctx.send(f"❌ Match {match_id} is already completed.")
        if match_data["status"] == "locked":
            return await ctx.send(f"❌ Match {match_id} is **locked** — its teams aren't decided yet (waiting on earlier results).")

        # Normalize winner to the actual team name (or TIE) so bracket progression matches reliably
        w_in = (winner_team or "").strip().lower()
        if w_in == "tie":
            winner_team, loser_team = "TIE", None
        elif w_in in (str(match_data.get("team1", "")).lower(), str(match_data.get("team2", "")).lower()):
            winner_team = match_data["team1"] if w_in == str(match_data["team1"]).lower() else match_data["team2"]
            loser_team = match_data["team2"] if winner_team == match_data["team1"] else match_data["team1"]
        else:
            return await ctx.send(f"❌ Winner must be **{match_data['team1']}**, **{match_data['team2']}**, or **TIE**.")

        match_data["status"] = "completed"
        match_data["result"] = {
            "winner": winner_team, "loser": loser_team,
            "format_overs": tourney.get("format_overs", 20),
            "t1_runs": t1_runs, "t1_wickets": t1_wkts, "t1_balls": t1_balls,
            "t2_runs": t2_runs, "t2_wickets": t2_wkts, "t2_balls": t2_balls
        }
        tourney["current_match_idx"] += 1
        # Advance the ACL bracket / Super Cup / IPL playoffs if applicable
        if tourney.get("tournament_type") == "acl":
            _acl_try_advance(tourney)
        elif tourney.get("tournament_type") == "ipl":
            ipl_try_advance(tourney)
        save_tournament(tourney)
        extra = "\n🏆 Bracket updated — `cv tournament bracket` to view." if tourney.get("tournament_type") == "acl" else "\nPoints Table and NRR updated."
        await ctx.send(f"✅ **Match {match_id} forcefully completed!**\nWinner: **{winner_team}**{extra}")

    @tournament.command(name="cancel_match", aliases=["cancel", "redo", "redo_match"], help="[MANAGER] Cancel a completed match so it can be replayed.\nUsage: tournament cancel_match <match_id>")
    async def t_cancel_match(self, ctx, match_id: int):
        server_id = str(ctx.guild.id)
        tourney = get_server_tournament(server_id)
        is_mgr = (ctx.author.id == ADMIN_DISCORD_ID) or (ctx.author.guild_permissions.administrator) or (tourney and str(ctx.author.id) in tourney.get("managers", []))
        if not tourney: return await ctx.send("❌ No tournament exists.")
        if not is_mgr: return await ctx.send("❌ Managers only.")
        _ok, msg = revert_tournament_match(tourney, match_id)
        await ctx.send(msg)

    @tournament.command(name="repair_schedule", aliases=["fix_schedule", "fix_ids"], help="[MANAGER] Fix duplicate match IDs in the schedule.\nUsage: tournament repair_schedule")
    async def t_repair_schedule(self, ctx):
        server_id = str(ctx.guild.id)
        tourney = get_server_tournament(server_id)
        is_mgr = (ctx.author.id == ADMIN_DISCORD_ID) or (ctx.author.guild_permissions.administrator) or (tourney and str(ctx.author.id) in tourney.get("managers", []))
        if not tourney: return await ctx.send("❌ No tournament exists.")
        if not is_mgr: return await ctx.send("❌ Managers only.")
        _changed, msg = repair_tournament_schedule(tourney)
        await ctx.send(msg)

    @tournament.command(name="generate_knockouts", help="[MANAGER] Generate Knockouts (Semi-Finals) for Top 4 teams.\nUsage: tournament generate_knockouts")
    async def t_generate_knockouts(self, ctx):
        server_id = str(ctx.guild.id)
        tourney = get_server_tournament(server_id)
        is_mgr = (ctx.author.id == ADMIN_DISCORD_ID) or (ctx.author.guild_permissions.administrator) or (tourney and str(ctx.author.id) in tourney.get("managers", []))
        if not tourney: return await ctx.send("❌ No tournament exists.")
        if not is_mgr: return await ctx.send("❌ Managers only.")
        if tourney["status"] != "active": return await ctx.send("❌ Tournament is not active.")
        if tourney.get("tournament_type") == "acl":
            return await ctx.send("❌ This is an ACL tournament. Use `cv tournament generate_playoffs` (alias `gp`) instead.")

        gs_matches = [m for m in tourney["schedule"] if isinstance(m.get("round"), int)]
        if any(m["status"] == "pending" for m in gs_matches):
            return await ctx.send("❌ Cannot generate knockouts until all Group Stage matches are completed.")
            
        if any(not isinstance(m.get("round"), int) for m in tourney["schedule"]):
            return await ctx.send("❌ Knockouts have already been generated.")
            
        standings = get_tournament_standings(tourney)
        real_teams = [t[0] for t in standings if t[0] != "BYE"]
        
        if len(real_teams) < 4:
            return await ctx.send("❌ Need at least 4 teams to play Semi-Finals.")
            
        top4 = real_teams[:4]
        
        _base = _tm_next_mid(tourney)
        sf1 = {"match_id": _base, "round": "Semi-Final 1", "stage": "knockout", "team1": top4[0], "team2": top4[3], "status": "pending", "result": None}
        sf2 = {"match_id": _base + 1, "round": "Semi-Final 2", "stage": "knockout", "team1": top4[1], "team2": top4[2], "status": "pending", "result": None}
        
        tourney["schedule"].extend([sf1, sf2])
        save_tournament(tourney)
        
        await ctx.send(f"🔥 **Knockout Stage Set!**\n**Semi-Final 1:** {top4[0]} vs {top4[3]}\n**Semi-Final 2:** {top4[1]} vs {top4[2]}\n\nUse `cv tournament play_next` to begin!")

    @tournament.command(name="generate_finals", aliases=["gf", "finals"], help="[MANAGER] Generate the Final for the Top 2 teams. (Double Round Robin only)\nUsage: tournament generate_finals")
    async def t_generate_finals(self, ctx):
        server_id = str(ctx.guild.id)
        tourney = get_server_tournament(server_id)
        is_mgr = (ctx.author.id == ADMIN_DISCORD_ID) or (ctx.author.guild_permissions.administrator) or (tourney and str(ctx.author.id) in tourney.get("managers", []))
        if not tourney: return await ctx.send("❌ No tournament exists.")
        if not is_mgr: return await ctx.send("❌ Managers only.")
        if tourney["status"] != "active": return await ctx.send("❌ Tournament is not active.")
        if tourney.get("tournament_type") != "double_round_robin":
            return await ctx.send("❌ This command is for **Double Round Robin** tournaments only. Use `cvt generate_knockouts` for Round Robin, or `cvt generate_playoffs` for ACL/DSL.")

        gs_matches = [m for m in tourney["schedule"] if isinstance(m.get("round"), int)]
        if any(m["status"] == "pending" for m in gs_matches):
            return await ctx.send("❌ Cannot generate the Final until all league matches are completed.")

        if any(not isinstance(m.get("round"), int) for m in tourney["schedule"]):
            return await ctx.send("❌ The Final has already been generated.")

        standings = get_tournament_standings(tourney)
        real_teams = [t[0] for t in standings if t[0] != "BYE"]

        if len(real_teams) < 2:
            return await ctx.send("❌ Need at least 2 teams to play a Final.")

        top2 = real_teams[:2]

        final = {"match_id": _tm_next_mid(tourney), "round": "Final", "stage": "knockout", "team1": top2[0], "team2": top2[1], "status": "pending", "result": None}
        tourney["schedule"].append(final)
        save_tournament(tourney)

        await ctx.send(f"🏆 **The Final is Set!**\n**{top2[0]}** (1st) vs **{top2[1]}** (2nd)\n\nUse `cv tournament play_next` to begin!")

    @tournament.command(name="generate_playoffs", aliases=["gp", "playoffs"], help="[MANAGER] Generate the Playoffs (ACL Top-6 / DSL Top-4).\nUsage: tournament generate_playoffs")
    async def t_generate_playoffs(self, ctx):
        server_id = str(ctx.guild.id)
        tourney = get_server_tournament(server_id)
        is_mgr = (ctx.author.id == ADMIN_DISCORD_ID) or (ctx.author.guild_permissions.administrator) or (tourney and str(ctx.author.id) in tourney.get("managers", []))
        if not tourney: return await ctx.send("❌ No tournament exists.")
        if not is_mgr: return await ctx.send("❌ Managers only.")
        t_type = tourney.get("tournament_type")
        if t_type not in ("acl", "dsl"):
            return await ctx.send("❌ This command is for **ACL** or **DSL** tournaments only.")
        if tourney["status"] != "active": return await ctx.send("❌ Tournament is not active.")

        if t_type == "dsl":
            ok, msg = dsl_generate_playoffs(tourney)
            if not ok:
                return await ctx.send(msg)
            seeds = tourney.get("playoff_seeds", [])
            return await ctx.send(
                content=(f"🏆 **{DSL_CONFIG['short_name']} PLAYOFFS ARE SET!**\n"
                         f"Top 4: {' · '.join(f'**{s}**' for s in seeds)}\n"
                         f"Owners: `cvt fixtures` to find your match."),
                embed=dsl_bracket_embed(tourney),
            )

        ok, msg = acl_generate_playoffs(tourney)
        if not ok:
            return await ctx.send(msg)
        shield = tourney.get("league_shield")
        await ctx.send(
            content=f"🏆 **ACL PLAYOFFS ARE SET!**\n🛡️ **{shield}** finished #1 — League Shield Winner, straight into the **Super Cup**.\nThe Top 6 now fight for the ACL Trophy. Owners: `cv tournament fixtures` to find your match.",
            embed=acl_bracket_embed(tourney),
        )

    @tournament.command(name="bracket", aliases=["br"], help="View the Playoffs bracket (ACL / DSL).\nUsage: tournament bracket")
    async def t_bracket(self, ctx):
        server_id = str(ctx.guild.id)
        tourney = get_server_tournament(server_id)
        if not tourney: return await ctx.send("❌ No tournament exists.")
        t_type = tourney.get("tournament_type")
        if t_type == "dsl":
            from league.dsl_manager import _dsl_get
            if not _dsl_get(tourney, "Semi-Final 1"):
                return await ctx.send("ℹ️ The Playoffs haven't been generated yet — they appear automatically once every league match is done.")
            return await ctx.send(embed=dsl_bracket_embed(tourney))
        if t_type == "rating":
            from league.rating_league import _rating_get
            if not _rating_get(tourney, "Semi-Final 1"):
                return await ctx.send("ℹ️ Playoffs not generated yet — a manager runs `cvt end_league` once teams have played enough games.")
            return await ctx.send(embed=rating_bracket_embed(tourney))
        if t_type != "acl":
            return await ctx.send("❌ The bracket view is for **ACL/DSL/Conquest** tournaments. Use `cv tournament standings` or `cv tournament status`.")
        if not _acl_get(tourney, "Qualifier"):
            return await ctx.send("ℹ️ The Playoffs haven't been generated yet. A Manager runs `cv tournament generate_playoffs` once all 91 league games are done.")
        await ctx.send(embed=acl_bracket_embed(tourney))

    # Conquest League: open play, ladder, trades, credits & boosts
    @tournament.command(name="challenge", aliases=["chal", "play_open", "vs"], help="[Conquest/OWNER] Challenge another team to a ladder match — play anyone available.\nUsage: tournament challenge \"<team>\"")
    async def t_challenge(self, ctx, *, team_name: str):
        server_id = str(ctx.guild.id)
        tourney = get_server_tournament(server_id)
        if not tourney: return await ctx.send("❌ No tournament exists.")
        if not is_rating_tournament(tourney):
            return await ctx.send("❌ Open challenges are a **Conquest League** feature.")
        if tourney["status"] != "active": return await ctx.send("❌ The league isn't live yet.")
        my = next((t for t in tourney["teams"] if t.get("owner_id") == str(ctx.author.id)), None)
        is_mgr = (ctx.author.id == ADMIN_DISCORD_ID) or ctx.author.guild_permissions.administrator or (str(ctx.author.id) in tourney.get("managers", []))
        opp = self._team_by_ref(ctx, tourney, team_name) or next((t for t in tourney["teams"] if t["name"].lower() == team_name.strip().lower()), None)
        if not opp: return await ctx.send(f"❌ Team **{team_name}** not found.")
        if not my and not is_mgr:
            return await ctx.send("❌ You don't own a team here. (Managers can launch any pairing.)")
        if my and my["name"] == opp["name"]:
            return await ctx.send("❌ You can't challenge your own team.")
        if my is None:  # manager launching - needs both teams named? default: pick opp only is ambiguous
            return await ctx.send("❌ Owners challenge with `cvt challenge \"<team>\"`. Managers: use `cvt play <id>` after a challenge, or own a team.")
        for tm in (my, opp):
            if any(len(t.get("squad", [])) < 2 for t in (my, opp)):
                return await ctx.send("❌ Both teams need submitted squads.")
        # Playoffs generated -> ladder is closed.
        from league.rating_league import RATING_KO_STAGES
        if any(m.get("stage") in RATING_KO_STAGES for m in tourney.get("schedule", [])):
            return await ctx.send("❌ The ladder is closed — the playoffs have begun.")
        m = create_open_match(tourney, my["name"], opp["name"])
        tourney.setdefault("schedule", []).append(m)
        save_tournament(tourney)
        await ctx.send(f"⚔️ **Ladder Challenge!** **{my['name']}** vs **{opp['name']}** — Match #{m['match_id']}.\n<@{ctx.author.id}> vs <@{opp['owner_id']}>, get ready to pick your XIs!")
        self.bot.dispatch("start_tournament_match", ctx.channel, ctx.author.id, tourney, m)

    @tournament.command(name="ratings", aliases=["ladder", "elo"], help="[Conquest] The live rating ladder.\nUsage: tournament ratings")
    async def t_ratings(self, ctx):
        server_id = str(ctx.guild.id)
        tourney = get_server_tournament(server_id)
        if not tourney: return await ctx.send("❌ No tournament exists.")
        if not is_rating_tournament(tourney):
            return await ctx.send("❌ The rating ladder is a **Conquest League** feature. Use `cvt standings`.")
        await ctx.send(embed=rating_board_embed(tourney))

    @tournament.command(name="end_league", aliases=["endleague", "finish_league"], help="[MANAGER/Conquest] Close the ladder and generate the Top-4 playoffs.\nUsage: tournament end_league")
    async def t_end_league(self, ctx):
        server_id = str(ctx.guild.id)
        tourney = get_server_tournament(server_id)
        if not tourney: return await ctx.send("❌ No tournament exists.")
        if not is_rating_tournament(tourney):
            return await ctx.send("❌ `end_league` is for the **Conquest League**.")
        is_mgr = (ctx.author.id == ADMIN_DISCORD_ID) or ctx.author.guild_permissions.administrator or (str(ctx.author.id) in tourney.get("managers", []))
        if not is_mgr: return await ctx.send("❌ Managers only.")
        ok, msg = generate_rating_playoffs(tourney)
        if not ok:
            return await ctx.send(msg)
        seeds = tourney.get("playoff_seeds", [])
        await ctx.send(
            content=(f"🏆 **{tourney['name']} PLAYOFFS ARE SET!**\nTop 4 by rating: {' · '.join(f'**{s}**' for s in seeds)}\n"
                     f"The ladder is now closed. Owners: `cvt fixtures`/`cvt play <id>` your semis."),
            embed=rating_bracket_embed(tourney),
        )

    @tournament.command(name="trade", help="[OWNER] Propose a player-for-player trade (both owners + a manager confirm).\nUsage: tournament trade \"<other team>\" | <my player>, ... | <their player>, ...")
    async def t_trade(self, ctx, *, spec: str):
        server_id = str(ctx.guild.id)
        tourney = get_server_tournament(server_id)
        if not tourney: return await ctx.send("❌ No tournament exists.")
        if tourney["status"] not in ("active", "registration"):
            return await ctx.send("❌ Trades are only allowed while the tournament is running.")
        parts = [s.strip() for s in spec.split("|")]
        if len(parts) != 3:
            return await ctx.send("❌ Format: `cvt trade \"<other team>\" | <my players> | <their players>`\n"
                                  "Players comma-separated. Example: `cvt trade \"Team B\" | Rohit, Bumrah | Kohli`")
        other_name, my_raw, their_raw = parts
        other_name = other_name.strip().strip('"')
        my_team = next((t for t in tourney["teams"] if t.get("owner_id") == str(ctx.author.id)), None)
        is_mgr = (ctx.author.id == ADMIN_DISCORD_ID) or ctx.author.guild_permissions.administrator or (str(ctx.author.id) in tourney.get("managers", []))
        if not my_team and not is_mgr:
            return await ctx.send("❌ You don't own a team here.")
        other = next((t for t in tourney["teams"] if t["name"].lower() == other_name.lower()), None)
        if not other: return await ctx.send(f"❌ Team **{other_name}** not found.")
        if my_team and my_team["name"] == other["name"]:
            return await ctx.send("❌ You can't trade with yourself.")
        if my_team is None:
            return await ctx.send("❌ Only a team owner can propose a trade.")

        def _resolve(raw, squad):
            found, missing = [], []
            for nm in [x.strip() for x in raw.split(",") if x.strip()]:
                p = next((x for x in squad if x["name"].lower() == nm.lower()), None)
                if p: found.append(p)
                else: missing.append(nm)
            return found, missing
        my_players, m1 = _resolve(my_raw, my_team.get("squad", []))
        their_players, m2 = _resolve(their_raw, other.get("squad", []))
        if m1 or m2:
            return await ctx.send("❌ Not found — " + ", ".join(f"**{x}**" for x in m1 + m2) +
                                  f"\n(check each player is in the right squad: yours = {my_team['name']}, theirs = {other['name']})")
        if not my_players or not their_players:
            return await ctx.send("❌ Both sides must send at least one player.")
        # squad-size + live-match guards
        min_s = tourney.get("min_squad", 11)
        if len(my_team["squad"]) - len(my_players) + len(their_players) < min_s:
            return await ctx.send(f"❌ **{my_team['name']}** would fall below the {min_s}-player minimum.")
        if len(other["squad"]) - len(their_players) + len(my_players) < min_s:
            return await ctx.send(f"❌ **{other['name']}** would fall below the {min_s}-player minimum.")
        for tm in (my_team, other):
            for cid, mt in list(active_games.items()):
                if getattr(mt, "tournament_server_id", None) == server_id and tm["name"] in (mt.team1.get("name"), mt.team2.get("name")):
                    return await ctx.send(f"❌ **{tm['name']}** is in a live match right now — finish it before trading.")

        give = ", ".join(f"**{p['name']}**" for p in my_players)
        get = ", ".join(f"**{p['name']}**" for p in their_players)
        summary = f"🔁 **Trade proposal**\n**{my_team['name']}** sends: {give}\n**{other['name']}** sends: {get}"
        # Owner B confirmation, then manager approval, both via SquadConfirmView.
        vb = SquadConfirmView(int(other["owner_id"]))
        pb = await ctx.send(summary + f"\n\n<@{other['owner_id']}> — do you **accept** this trade?", view=vb)
        await vb.wait()
        if not vb.value:
            return await pb.edit(content=summary + "\n\n❌ The other owner declined (or it timed out).", view=None)
        # manager approval - any manager/admin may approve (custom interaction check)
        mgr_ids = set(tourney.get("managers", [])) | {str(ADMIN_DISCORD_ID)}
        vm = SquadConfirmView(None)
        async def _mgr_check(inter):
            allowed = (str(inter.user.id) in mgr_ids) or inter.user.guild_permissions.administrator
            if not allowed:
                await inter.response.send_message("❌ A manager must approve this trade.", ephemeral=True)
            return allowed
        vm.interaction_check = _mgr_check
        pm = await ctx.send(summary + "\n\n🧑‍⚖️ A **manager** must approve — accept to finalise.", view=vm)
        await vm.wait()
        if not vm.value:
            return await pm.edit(content=summary + "\n\n❌ No manager approved (or it timed out).", view=None)
        # execute
        for p in my_players:
            my_team["squad"].remove(p); other["squad"].append(p)
        for p in their_players:
            other["squad"].remove(p); my_team["squad"].append(p)
        # scrub traded-away players from each team's default XI / impact
        def _scrub(team, gone):
            names = {p["name"] for p in gone}
            if team.get("default_xi") and any(n in names for n in team["default_xi"]):
                team.pop("default_xi", None); team.pop("default_captain", None)
            if team.get("default_impact"):
                team["default_impact"] = [n for n in team["default_impact"] if n not in names]
        _scrub(my_team, my_players); _scrub(other, their_players)
        save_tournament(tourney)
        await pm.edit(content=summary + "\n\n✅ **Trade complete!** Squads updated.", view=None)

    @tournament.command(name="boost", help="[OWNER] Spend credits to boost a squad player's bat/bowl.\nUsage: tournament boost \"<player>\" <bat|bowl>")
    async def t_boost(self, ctx, player_name: str, skill: str):
        server_id = str(ctx.guild.id)
        tourney = get_server_tournament(server_id)
        if not tourney: return await ctx.send("❌ No tournament exists.")
        team = next((t for t in tourney["teams"] if t.get("owner_id") == str(ctx.author.id)), None)
        is_mgr = (ctx.author.id == ADMIN_DISCORD_ID) or ctx.author.guild_permissions.administrator or (str(ctx.author.id) in tourney.get("managers", []))
        if not team:
            if is_mgr:
                return await ctx.send("❌ Managers: run boosts from the owning account, or use `cvt boost_team \"<team>\" ...` (owner-driven by design).")
            return await ctx.send("❌ You don't own a team here.")
        for cid, mt in list(active_games.items()):
            if getattr(mt, "tournament_server_id", None) == server_id and team["name"] in (mt.team1.get("name"), mt.team2.get("name")):
                return await ctx.send(f"❌ **{team['name']}** is in a live match — boost after it finishes.")
        ok, msg = apply_boost(tourney, team, player_name, skill)
        if ok:
            save_tournament(tourney)
        await ctx.send(msg)

    @tournament.command(name="credits", aliases=["cr"], help="View a team's credit balance (earn by playing — weak teams earn more).\nUsage: tournament credits [team]")
    async def t_credits(self, ctx, *, team_name: str = None):
        server_id = str(ctx.guild.id)
        tourney = get_server_tournament(server_id)
        if not tourney: return await ctx.send("❌ No tournament exists.")
        if team_name:
            team = next((t for t in tourney["teams"] if t["name"].lower() == team_name.strip().lower()), None)
        else:
            team = next((t for t in tourney["teams"] if t.get("owner_id") == str(ctx.author.id)), None)
        if not team: return await ctx.send("❌ Team not found (specify one: `cvt credits <team>`).")
        e = discord.Embed(title=f"💰 {team['name']} — Credits", color=discord.Color.gold())
        e.description = (f"**Balance: {team.get('credits', 0)}** credits\n\n"
                         f"Earn by playing — **weak teams earn more**, and beating a *stronger* team pays a big bonus. "
                         f"Spend **{BOOST_COST}** on `cvt boost` for +1 to a player (max +{BOOST_MAX_PER_PLAYER}/player, +{BOOST_MAX_PER_TEAM}/squad).")
        await ctx.send(embed=e)

    @tournament.command(name="boosts", help="View a team's applied player boosts.\nUsage: tournament boosts [team]")
    async def t_boosts(self, ctx, *, team_name: str = None):
        server_id = str(ctx.guild.id)
        tourney = get_server_tournament(server_id)
        if not tourney: return await ctx.send("❌ No tournament exists.")
        if team_name:
            team = next((t for t in tourney["teams"] if t["name"].lower() == team_name.strip().lower()), None)
        else:
            team = next((t for t in tourney["teams"] if t.get("owner_id") == str(ctx.author.id)), None)
        if not team: return await ctx.send("❌ Team not found (specify one: `cvt boosts <team>`).")
        lines, total = [], 0
        for p in team.get("squad", []):
            bb, bo = p.get("tboost_bat", 0), p.get("tboost_bowl", 0)
            if bb or bo:
                bits = []
                if bb: bits.append(f"batting +{bb}")
                if bo: bits.append(f"bowling +{bo}")
                lines.append(f"• **{p['name']}** — {', '.join(bits)}")
                total += bb + bo
        e = discord.Embed(title=f"⬆️ {team['name']} — Player Boosts",
                          description="\n".join(lines) if lines else "*No boosts applied yet.*",
                          color=discord.Color.from_rgb(90, 40, 160))
        e.set_footer(text=f"{total}/{BOOST_MAX_PER_TEAM} squad boosts used · {team.get('credits', 0)} credits left")
        await ctx.send(embed=e)

    @tournament.command(name="simulate_all", aliases=["simall"], help="[OWNER] Instantly simulate all pending tournament matches.\nUsage: tournament simulate_all")
    async def t_simulate_all(self, ctx):
        if ctx.author.id != ADMIN_DISCORD_ID:
            return await ctx.send("❌ Owner only.")
        server_id = str(ctx.guild.id)
        tourney = get_server_tournament(server_id)
        if not tourney:
            return await ctx.send("❌ No tournament exists.")
        if tourney["status"] != "active":
            return await ctx.send("❌ Tournament is not active.")

        if not any(m["status"] == "pending" for m in tourney.get("schedule", [])):
            return await ctx.send("✅ No pending matches — tournament is fully simulated!")

        status_msg = await ctx.send("⚡ **Simulating all pending matches...** (playoff matches are auto-included as they unlock)")
        results = []
        injuries_log = []
        errored = set()

        # Mirror the real match-start filter: leave injured players out of the XI,
        # falling back to the full squad only if fewer than 11 are fit.
        def _available(squad):
            fit = [p for p in squad if not p.get("injured")]
            return fit if len(fit) >= 11 else squad

        # Re-scan after every match so newly-unlocked knockout matches (ACL playoffs, T20WC semis/final) get played too
        def _next_pending():
            return next((m for m in tourney.get("schedule", [])
                         if m["status"] == "pending" and m["match_id"] not in errored), None)

        while True:
            m_data = _next_pending()
            if m_data is None:
                break
            t1_data = next((t for t in tourney["teams"] if t["name"] == m_data["team1"]), None)
            t2_data = next((t for t in tourney["teams"] if t["name"] == m_data["team2"]), None)
            r_label = f"R{m_data['round']}" if isinstance(m_data['round'], int) else m_data['round']

            if not t1_data or not t2_data:
                errored.add(m_data["match_id"])
                results.append(f"M{m_data['match_id']} ({r_label}): ❌ Team not found")
                continue
            s1 = t1_data.get("squad", [])
            s2 = t2_data.get("squad", [])
            if len(s1) < 2 or len(s2) < 2:
                errored.add(m_data["match_id"])
                results.append(f"M{m_data['match_id']} ({r_label}): ❌ Squad not set")
                continue

            # Injuries carry over between simmed matches exactly like real ones: first heal
            # any that have now expired, then leave the still-injured out of THIS match's XI.
            current_mid = m_data["match_id"]
            for _td in (t1_data, t2_data):
                for _p in _td.get("squad", []):
                    if _p.get("injured") and _p.get("injury_until_match", 0) < current_mid:
                        _p.pop("injured", None); _p.pop("injury_until_match", None); _p.pop("injury_severity", None)

            # XI priority: the team's saved DEFAULT XI (kept in its saved batting order,
            # when all 11 are fit) - otherwise the BEST balanced XI (top 11 by rating,
            # ≥5 who can bowl) sorted into a proper batting order.
            def _sim_roster(tdata, squad):
                fit = _available(squad)
                dxi = resolve_default_xi(tdata, fit)
                if dxi:
                    return with_captain(apply_tournament_boosts(apply_server_overrides(dxi, tourney["server_id"])))
                return with_captain(apply_tournament_boosts(_batting_order(_best_xi(apply_server_overrides(fit, tourney["server_id"])))))
            roster1 = _sim_roster(t1_data, s1)
            roster2 = _sim_roster(t2_data, s2)
            # Use the match's assigned conditions if valid; otherwise pick a fully random
            # pitch from the FULL valid set (not a tiny flat/dead-heavy list) so sims cover
            # every surface - green seamers, dustbowls, crackers, the lot.
            pitch = canonical_pitch(m_data.get("pitch")) or random.choice(ALL_PITCHES)
            weather = canonical_weather(m_data.get("weather")) or "Clear"
            t1 = {"name": m_data["team1"], "players": roster1, "color": t1_data.get("color", "#6B7280")}
            t2 = {"name": m_data["team2"], "players": roster2, "color": t2_data.get("color", "#6B7280")}

            match = CricketMatch(None, None, 0, 0, t1, t2, tourney.get("format_overs", 20), pitch, weather)
            match.tournament_server_id = tourney["server_id"]
            match.tournament_match_id = m_data["match_id"]
            match.tournament_type = tourney.get("tournament_type", "round_robin")
            match.manager_id = ctx.author.id
            match.tournament_name = tourney["name"]
            match.sim_only = True
            match._scorecard_players = None

            t_bat, t_bowl = (t1, t2) if random.random() < 0.5 else (t2, t1)
            match.innings1 = InningsState(t_bat, t_bowl)

            try:
                await asyncio.to_thread(_run_full_match_sync, match)
            except Exception as e:
                errored.add(m_data["match_id"])
                results.append(f"M{m_data['match_id']} ({r_label}): ❌ Error: {e}")
                continue

            # Capture the full scorecard the same way a real match does, so simmed
            # matches also power `cv tournament match_scorecard` (image + text).
            try:
                match._scorecard_players = extract_scorecard_players(match)
            except Exception as _e:
                print(f"simall scorecard extract failed M{m_data['match_id']}: {_e}")
                match._scorecard_players = None

            # Trigger the existing stats + progression listener directly (sequential, no race)
            from league.tournament_manager import TournamentCog as _TC
            tc = self.bot.cogs.get("TournamentCog")
            if tc:
                await tc.on_tournament_match_complete(match)
            else:
                self.bot.dispatch("tournament_match_complete", match)
                await asyncio.sleep(0.5)

            # tourney dict has been updated in-place by the listener; re-fetch for fresh ref
            tourney = get_server_tournament(server_id)

            # Log this match's freshly-rolled injuries one-by-one and CONSUME them, so they
            # aren't re-announced when a real match later starts. The injured flags stay on
            # the squad, so those players sit out the next simmed match (handled above).
            _news = tourney.pop("pending_injury_news", [])
            injury_suffix = ""
            if _news:
                save_tournament(tourney)
                injuries_log.extend(_news)
                injury_suffix = "  🚑 " + ", ".join(f"{it['player']} ({it['team']}, {it['severity']}m)" for it in _news)

            inn1, inn2 = match.innings1, match.innings2
            if inn2.total_runs >= match.target:
                win_str = f"{inn2.batting_team['name']} won by {10 - inn2.wickets}W"
            else:
                diff = inn1.total_runs - inn2.total_runs
                win_str = f"{inn1.batting_team['name']} won by {diff}R"

            i1o = f"{inn1.total_balls // 6}.{inn1.total_balls % 6}"
            i2o = f"{inn2.total_balls // 6}.{inn2.total_balls % 6}"
            results.append(
                f"**M{m_data['match_id']}** ({r_label}): "
                f"{t1['name']} {inn1.total_runs if match.innings1.batting_team['name'] == t1['name'] else inn2.total_runs}"
                f"/{inn1.wickets if match.innings1.batting_team['name'] == t1['name'] else inn2.wickets} "
                f"vs {t2['name']} {inn2.total_runs if match.innings2.batting_team['name'] == t2['name'] else inn1.total_runs}"
                f"/{inn2.wickets if match.innings2.batting_team['name'] == t2['name'] else inn1.wickets} "
                f"— **{win_str}**{injury_suffix}"
            )

        header = f"✅ **Simulation Complete! ({len(results)} matches)**\n"
        lines = "\n".join(results)
        if len(header) + len(lines) <= 1990:
            await status_msg.edit(content=header + lines)
        else:
            # too long for one message - show a tail and post the rest in follow-ups
            await status_msg.edit(content=header + f"*(showing last results; {len(results)} total)*")
            chunk, buf = [], 0
            for ln in results:
                if buf + len(ln) + 1 > 1900:
                    await ctx.send("\n".join(chunk)); chunk, buf = [], 0
                chunk.append(ln); buf += len(ln) + 1
            if chunk:
                await ctx.send("\n".join(chunk))

        # Consolidated injury report to the injury channel (mirrors real matches; pings owners).
        if injuries_log:
            team_owners = {t["name"]: t.get("owner_id") for t in tourney.get("teams", [])}
            rep, pings = [f"🚑 **Injury Report — {len(injuries_log)} from simulated matches:**"], []
            for it in injuries_log:
                mw = "match" if it["severity"] == 1 else "matches"
                rep.append(f"• **{it['player']}** ({it['team']}) — out **{it['severity']}** {mw}")
                oid = team_owners.get(it["team"])
                if oid and oid not in pings: pings.append(oid)
            if pings: rep.append(" ".join(f"<@{u}>" for u in pings))
            inj_ch_id = tourney.get("injury_channel_id")
            inj_ch = (self.bot.get_channel(int(inj_ch_id)) if inj_ch_id else None) or ctx.channel
            chunk, buf = [], 0
            for ln in rep:
                if buf + len(ln) + 1 > 1900:
                    await inj_ch.send("\n".join(chunk)); chunk, buf = [], 0
                chunk.append(ln); buf += len(ln) + 1
            if chunk:
                await inj_ch.send("\n".join(chunk))

    @tournament.command(name="sim", aliases=["simulate", "sim_match"], help="[MANAGER] Instantly simulate ONE match (scorecard + stats saved, default XI used).\nUsage: tournament sim <match_id>")
    async def t_sim_match(self, ctx, match_id: int):
        """Single-match version of simulate_all: same headless engine, same stats/
        scorecard recording, same injury handling - for exactly one pending match.
        Uses each team's default XI when valid, else the built-in best-XI picker."""
        server_id = str(ctx.guild.id)
        tourney = get_server_tournament(server_id)
        if not tourney: return await ctx.send("❌ No tournament exists.")
        is_mgr = (ctx.author.id == ADMIN_DISCORD_ID) or (ctx.author.guild_permissions.administrator) or (str(ctx.author.id) in tourney.get("managers", []))
        if not is_mgr: return await ctx.send("❌ Managers only.")
        if tourney["status"] != "active": return await ctx.send("❌ Tournament is not active.")
        m_data = next((m for m in tourney.get("schedule", []) if m.get("match_id") == match_id), None)
        if not m_data: return await ctx.send(f"❌ Match #{match_id} not found.")
        if m_data["status"] == "locked":
            return await ctx.send(f"❌ Match #{match_id} isn't ready — its teams depend on earlier results.")
        if m_data["status"] != "pending":
            return await ctx.send(f"❌ Match #{match_id} is already completed.")
        ok, gate_msg = match_order_gate(tourney, m_data)
        if not ok: return await ctx.send(gate_msg)

        t1_data = next((t for t in tourney["teams"] if t["name"] == m_data["team1"]), None)
        t2_data = next((t for t in tourney["teams"] if t["name"] == m_data["team2"]), None)
        if not t1_data or not t2_data:
            return await ctx.send("❌ A team in this match no longer exists.")
        if len(t1_data.get("squad", [])) < 2 or len(t2_data.get("squad", [])) < 2:
            return await ctx.send("❌ Both teams need submitted squads first.")

        def _available(squad):
            fit = [p for p in squad if not p.get("injured")]
            return fit if len(fit) >= 11 else squad

        # Heal expired injuries exactly like simulate_all, then leave the rest out.
        for _td in (t1_data, t2_data):
            for _p in _td.get("squad", []):
                if _p.get("injured") and _p.get("injury_until_match", 0) < match_id:
                    _p.pop("injured", None); _p.pop("injury_until_match", None); _p.pop("injury_severity", None)

        def _sim_roster(tdata):
            fit = _available(tdata.get("squad", []))
            dxi = resolve_default_xi(tdata, fit)
            if dxi:
                return with_captain(apply_tournament_boosts(apply_server_overrides(dxi, server_id))), "default XI"
            return with_captain(apply_tournament_boosts(_batting_order(_best_xi(apply_server_overrides(fit, server_id))))), "best XI"

        roster1, src1 = _sim_roster(t1_data)
        roster2, src2 = _sim_roster(t2_data)
        pitch = canonical_pitch(m_data.get("pitch")) or random.choice(ALL_PITCHES)
        weather = canonical_weather(m_data.get("weather")) or "Clear"
        t1 = {"name": m_data["team1"], "players": roster1, "color": t1_data.get("color", "#6B7280")}
        t2 = {"name": m_data["team2"], "players": roster2, "color": t2_data.get("color", "#6B7280")}

        match = CricketMatch(None, None, 0, 0, t1, t2, tourney.get("format_overs", 20), pitch, weather)
        match.tournament_server_id = tourney["server_id"]
        match.tournament_match_id = match_id
        match.tournament_type = tourney.get("tournament_type", "round_robin")
        match.manager_id = ctx.author.id
        match.tournament_name = tourney["name"]
        match.sim_only = True
        match._scorecard_players = None
        t_bat, t_bowl = (t1, t2) if random.random() < 0.5 else (t2, t1)
        match.innings1 = InningsState(t_bat, t_bowl)

        r_label = f"R{m_data['round']}" if isinstance(m_data['round'], int) else m_data['round']
        status_msg = await ctx.send(f"⚡ Simulating **Match #{match_id}** ({r_label}) — {t1['name']} ({src1}) vs {t2['name']} ({src2})…")
        try:
            await asyncio.to_thread(_run_full_match_sync, match)
        except Exception as e:
            return await status_msg.edit(content=f"❌ Simulation failed: {e}")
        try:
            match._scorecard_players = extract_scorecard_players(match)
        except Exception as _e:
            print(f"cvt sim scorecard extract failed M{match_id}: {_e}")
            match._scorecard_players = None

        tc = self.bot.cogs.get("TournamentCog")
        if tc:
            await tc.on_tournament_match_complete(match)
        else:
            self.bot.dispatch("tournament_match_complete", match)
            await asyncio.sleep(0.5)

        tourney = get_server_tournament(server_id)
        _news = tourney.pop("pending_injury_news", []) if tourney else []
        injury_suffix = ""
        if _news:
            save_tournament(tourney)
            injury_suffix = "\n🚑 " + ", ".join(f"**{it['player']}** ({it['team']}, out {it['severity']}m)" for it in _news)

        inn1, inn2 = match.innings1, match.innings2
        if inn2.total_runs >= match.target:
            win_str = f"{inn2.batting_team['name']} won by {10 - inn2.wickets} wickets"
        elif inn2.total_runs == match.target - 1:
            win_str = "Match tied" if not getattr(match, "tiebreak_winner_name", None) else f"{match.tiebreak_winner_name} won (Super Over)"
        else:
            win_str = f"{inn1.batting_team['name']} won by {inn1.total_runs - inn2.total_runs} runs"
        await status_msg.edit(content=(
            f"✅ **Match #{match_id}** ({r_label}) simulated:\n"
            f"**{inn1.batting_team['name']}** {inn1.total_runs}/{inn1.wickets} ({inn1.total_balls // 6}.{inn1.total_balls % 6})  vs  "
            f"**{inn2.batting_team['name']}** {inn2.total_runs}/{inn2.wickets} ({inn2.total_balls // 6}.{inn2.total_balls % 6})\n"
            f"🏆 **{win_str}**{injury_suffix}\n"
            f"-# `cvt match_scorecard {match_id}` for the full card"))

    @tournament.command(name="add_manager", help="[MANAGER] Assign a tournament manager.\nUsage: tournament add_manager <@user>")
    async def t_add_manager(self, ctx, user: discord.Member):
        server_id = str(ctx.guild.id)
        tourney = get_server_tournament(server_id)
        is_mgr = (ctx.author.id == ADMIN_DISCORD_ID) or (ctx.author.guild_permissions.administrator) or (tourney and str(ctx.author.id) in tourney.get("managers", []))
        if not tourney: return await ctx.send("❌ No tournament exists.")
        if not is_mgr: return await ctx.send("❌ Managers only.")
        uid = str(user.id)
        if uid not in tourney["managers"]:
            tourney["managers"].append(uid)
            save_tournament(tourney)
        await ctx.send(f"✅ {user.mention} is now a Tournament Manager!")

    @tournament.command(name="remove_team", help="[MANAGER] Remove a team from the tournament.\nUsage: tournament remove_team \"<team_name>\"")
    async def t_remove_team(self, ctx, *, team_name: str):
        server_id = str(ctx.guild.id)
        tourney = get_server_tournament(server_id)
        is_mgr = (ctx.author.id == ADMIN_DISCORD_ID) or (ctx.author.guild_permissions.administrator) or (tourney and str(ctx.author.id) in tourney.get("managers", []))
        if not tourney: return await ctx.send("❌ No tournament exists.")
        if not is_mgr: return await ctx.send("❌ Managers only.")
        if tourney["status"] != "registration": return await ctx.send("❌ Cannot remove teams after tournament has started.")
        team = self._team_by_ref(ctx, tourney, team_name)
        if team is None: return await ctx.send(f"❌ Team **{team_name}** not found (use the team name or ping its @owner).")
        tname = team["name"]
        tourney["teams"].remove(team)
        save_tournament(tourney)
        await ctx.send(f"✅ Team **{tname}** removed.")

    @tournament.command(name="transfer_team", help="[MANAGER] Transfer team ownership to a new user.\nUsage: tournament transfer_team \"<team_name>\" @new_owner")
    async def t_transfer_team(self, ctx, team_name: str, new_owner: discord.Member):
        server_id = str(ctx.guild.id)
        tourney = get_server_tournament(server_id)
        if not tourney:
            return await ctx.send("❌ No tournament exists.")
        is_mgr = (ctx.author.id == ADMIN_DISCORD_ID) or ctx.author.guild_permissions.administrator or (str(ctx.author.id) in tourney.get("managers", []))
        if not is_mgr:
            return await ctx.send("❌ Managers only.")
        team = next((t for t in tourney["teams"] if t["name"].lower() == team_name.lower()), None)
        if not team:
            return await ctx.send(f"❌ Team **{team_name}** not found.")
        if str(new_owner.id) == team.get("owner_id"):
            return await ctx.send(f"❌ {new_owner.mention} already owns **{team['name']}**.")
        old_owner_id = team.get("owner_id")
        team["owner_id"] = str(new_owner.id)
        save_tournament(tourney)
        old_mention = f"<@{old_owner_id}>" if old_owner_id else "*(no previous owner)*"
        await ctx.send(f"✅ **{team['name']}** ownership transferred from {old_mention} → {new_owner.mention}.")

    @tournament.command(name="summary", aliases=["recap", "full_summary", "report"], help="The complete tournament report: overview, standings, knockout results, EVERY leaderboard in detail, and match records. Run it (and pin it!) before deleting a finished tournament.\nUsage: tournament summary")
    async def t_summary(self, ctx):
        server_id = str(ctx.guild.id)
        tourney = get_server_tournament(server_id)
        if not tourney:
            return await ctx.send("❌ No tournament exists in this server.")
        if tourney.get("status") == "registration":
            return await ctx.send("❌ Nothing to report yet — the tournament hasn't started.")
        try:
            embeds = build_tournament_summary_embeds(tourney)
        except Exception as e:
            print(f"tournament summary failed: {e}")
            return await ctx.send(f"❌ Couldn't build the report: {e}")
        for e in embeds:
            await ctx.send(embed=e)
            await asyncio.sleep(0.4)   # gentle pacing - the report is 4-6 embeds

    @tournament.command(name="force_delete", help="[ADMIN] Forcefully delete this server's tournament.\nUsage: tournament force_delete")
    async def t_force_delete(self, ctx):
        if not ctx.author.guild_permissions.administrator and ctx.author.id != ADMIN_DISCORD_ID:
            return await ctx.send("❌ Server Admins only.")
        server_id = str(ctx.guild.id)
        from core.subscription_manager import DB_CACHE
        before = DB_CACHE.get("tournaments", []) or []
        # Match on str() of both sides so an entry saved with an int server_id still gets cleaned.
        DB_CACHE["tournaments"] = [t for t in before if str(t.get("server_id")) != server_id]
        removed = len(before) - len(DB_CACHE["tournaments"])
        if removed == 0:
            return await ctx.send("❌ No tournament exists for this server.")
        save_tournament_data_to_bin()
        await ctx.send(f"🗑️ Tournament deleted ({removed} removed).")

    @tournament.command(name="set_theme", help="[ADMIN] Set the scorecard theme.\nUsage: tournament set_theme <Default|Crimson Cricket>")
    async def t_set_theme(self, ctx, *, theme_name: str):
        if not ctx.author.guild_permissions.administrator and ctx.author.id != ADMIN_DISCORD_ID:
            return await ctx.send("❌ Server Admins only.")
        server_id = str(ctx.guild.id)
        tourney = get_server_tournament(server_id)
        if not tourney: return await ctx.send("❌ No tournament exists.")
        valid = ["Default", "Crimson Cricket"]
        match_theme = next((v for v in valid if v.lower() == theme_name.lower()), None)
        if not match_theme: return await ctx.send(f"❌ Invalid theme. Options: {', '.join(valid)}")
        tourney["theme"] = match_theme
        save_tournament(tourney)
        await ctx.send(f"✅ Theme set to `{match_theme}`.")

    @tournament.command(name="set_home_pitch", aliases=["sethome", "home_pitch"], help="[MANAGER] Set a team's home pitch (used by Home-Pitch conditions mode).\nUsage: tournament set_home_pitch \"<team>\" <pitch>")
    async def t_set_home_pitch(self, ctx, team_name: str, *, pitch: str):
        server_id = str(ctx.guild.id)
        tourney = get_server_tournament(server_id)
        if not tourney: return await ctx.send("❌ No tournament exists.")
        is_mgr = (ctx.author.id == ADMIN_DISCORD_ID) or ctx.author.guild_permissions.administrator or (str(ctx.author.id) in tourney.get("managers", []))
        if not is_mgr: return await ctx.send("❌ Managers only.")
        team = self._team_by_ref(ctx, tourney, team_name)
        if not team: return await ctx.send(f"❌ No team **{team_name}** in this tournament (use the team name or ping its @owner).")
        cp = canonical_pitch(pitch)
        if not cp: return await ctx.send(f"❌ Invalid pitch **{pitch}**.\nOptions: {', '.join(ALL_PITCHES)}")
        team["home_pitch"] = cp
        save_tournament(tourney)
        await ctx.send(f"🏟️ **{team['name']}** home pitch set to **{cp}**.")

    @tournament.command(name="set_conditions", aliases=["setcond", "conditions"], help="[MANAGER] Override a pending match's pitch/weather.\nUsage: tournament set_conditions <match_id> <pitch> [weather]")
    async def t_set_conditions(self, ctx, match_id: int, pitch: str, *, weather: str = None):
        server_id = str(ctx.guild.id)
        tourney = get_server_tournament(server_id)
        if not tourney: return await ctx.send("❌ No tournament exists.")
        is_mgr = (ctx.author.id == ADMIN_DISCORD_ID) or ctx.author.guild_permissions.administrator or (str(ctx.author.id) in tourney.get("managers", []))
        if not is_mgr: return await ctx.send("❌ Managers only.")
        m = next((x for x in tourney.get("schedule", []) if x["match_id"] == match_id), None)
        if not m: return await ctx.send(f"❌ No match **#{match_id}** in this tournament.")
        if m.get("status") == "completed": return await ctx.send(f"❌ Match #{match_id} is already completed.")
        cp = canonical_pitch(pitch)
        if not cp: return await ctx.send(f"❌ Invalid pitch **{pitch}**.\nOptions: {', '.join(ALL_PITCHES)}")
        m["pitch"] = cp
        if weather:
            cw = canonical_weather(weather)
            if not cw: return await ctx.send(f"❌ Invalid weather **{weather}**.\nOptions: {', '.join(ALL_WEATHER)}")
            m["weather"] = cw
        elif not m.get("weather"):
            m["weather"] = "Clear"
        save_tournament(tourney)
        await ctx.send(f"🛠️ Match **#{match_id}** ({m['team1']} vs {m['team2']}) → 🏟️ **{m['pitch']}** · 🌤️ **{m.get('weather', 'Clear')}**")

    @tournament.command(name="homepitch", aliases=["homepitches", "home_pitches"], help="List each team's home pitch.\nUsage: tournament homepitch")
    async def t_homepitch(self, ctx):
        server_id = str(ctx.guild.id)
        tourney = get_server_tournament(server_id)
        if not tourney: return await ctx.send("❌ No tournament exists.")
        teams = tourney.get("teams", [])
        if not teams: return await ctx.send("📋 No teams yet.")
        mode = tourney.get("conditions_mode", "manual")
        lines = []
        for t in sorted(teams, key=lambda x: x["name"].lower()):
            cp = canonical_pitch(t.get("home_pitch"))
            lines.append(f"• **{t['name']}** — {('🏟️ ' + cp) if cp else '❌ *not set*'}")
        set_n = sum(1 for t in teams if canonical_pitch(t.get("home_pitch")))
        e = discord.Embed(title=f"🏟️ {tourney['name']} — Home Pitches",
                          description="\n".join(lines), color=discord.Color.green())
        foot = f"{set_n}/{len(teams)} set"
        foot += " · all required before `cvt start`" if mode == "home" else f" · conditions mode: {mode} (home pitch used only in 'home' mode)"
        e.set_footer(text=foot)
        await ctx.send(embed=e)

    # Stadiums (cosmetic ACL venue labels)
    @tournament.command(name="stadiums", aliases=["venues", "stadium_list", "stadium"], help="List the stadium pool — or one venue's all-time stats (DSL).\nUsage: tournament stadiums [venue]")
    async def t_stadiums(self, ctx, *, name: str = None):
        server_id = str(ctx.guild.id)
        tourney = get_server_tournament(server_id)
        if not tourney: return await ctx.send("❌ No tournament exists.")
        if name:
            # `cvt stadium <name>` -> full all-time venue stats (DSL); cosmetic label elsewhere.
            if is_dsl_tournament(tourney):
                return await self.t_venue_stats.callback(self, ctx, venue=name)
            return await ctx.send("🏟️ ACL stadiums are cosmetic labels — per-venue stats are a **DSL** feature (`cvt venue_stats`).")
        from league.stadium_manager import linked_stadiums
        if linked_stadiums(tourney) and not is_dsl_tournament(tourney):
            # Linked mode: the "pool" is the teams' home grounds - show those.
            return await self.t_home_stadiums.callback(self, ctx)
        pool = get_stadium_pool(tourney)
        sched = tourney.get("schedule", [])
        counts = {}
        for m in sched:
            s = m.get("stadium")
            if s: counts[s] = counts.get(s, 0) + 1
        assigned = sum(counts.values())
        if is_dsl_tournament(tourney):
            # DSL: fixed venue list, each with its ONE pitch + home team
            homes = {}
            for t in tourney.get("teams", []):
                v = canonical_venue(t.get("home_stadium"))
                if v: homes[v] = t["name"]
            lines = []
            for i, (v, pitch) in enumerate(DSL_CONFIG["venues"].items()):
                c = counts.get(v, 0)
                tail = f" · {c} fixture{'s' if c != 1 else ''}" if c else ""
                home = f" · 🏠 **{homes[v]}**" if v in homes else ""
                lines.append(f"`{i+1:>2}` 📍 **{v}** — 🏟️ *{pitch}*{home}{tail}")
            e = discord.Embed(title=f"🏟️ {tourney['name']} — Venues",
                              description="\n".join(lines), color=discord.Color.from_rgb(20, 60, 160))
            e.set_footer(text=f"{len(DSL_CONFIG['venues'])} venues · every ground has ONE fixed pitch — same stadium, same pitch, every match · cvt stadium <name> for its all-time stats")
            return await ctx.send(embed=e)
        e = discord.Embed(title=f"🏟️ {tourney['name']} — Stadiums",
                          color=discord.Color.from_rgb(200, 30, 40))
        if pool:
            lines = []
            for i, s in enumerate(pool):
                c = counts.get(s, 0)
                tail = f"  · {c} fixture{'s' if c != 1 else ''}" if c else ""
                lines.append(f"`{i+1:>2}` 📍 **{s}**{tail}")
            e.description = "\n".join(lines)
        else:
            e.description = "*No stadiums in the pool.* Add some with `cvt stadium_add \"<name>\"`."
        foot = f"{len(pool)} venue(s)"
        if sched: foot += f" · {assigned}/{len(sched)} fixtures assigned"
        foot += " · cosmetic only (pitch & weather are separate)"
        e.set_footer(text=foot)
        await ctx.send(embed=e)

    @tournament.command(name="stadium_add", aliases=["addstadium", "add_stadium"], help="[MANAGER] Add a stadium to the tournament's venue pool.\nUsage: tournament stadium_add \"<name>\"")
    async def t_stadium_add(self, ctx, *, name: str):
        server_id = str(ctx.guild.id)
        tourney = get_server_tournament(server_id)
        if not tourney: return await ctx.send("❌ No tournament exists.")
        is_mgr = (ctx.author.id == ADMIN_DISCORD_ID) or ctx.author.guild_permissions.administrator or (str(ctx.author.id) in tourney.get("managers", []))
        if not is_mgr: return await ctx.send("❌ Managers only.")
        if is_dsl_tournament(tourney):
            return await ctx.send(f"🔒 **{DSL_CONFIG['short_name']}** venues are fixed by the league config — the pool can't be edited.")
        nm = name.strip().strip('"').strip()
        if not nm: return await ctx.send("❌ Provide a stadium name.")
        pool = tourney.setdefault("stadiums", [])
        if canonical_stadium(nm, pool):
            return await ctx.send(f"⚠️ **{nm}** is already in the pool.")
        pool.append(nm)
        tourney.pop("stadiums_cleared", None)   # the pool is deliberate again
        save_tournament(tourney)
        await ctx.send(f"🏟️ Added 📍 **{nm}** to the stadium pool ({len(pool)} total).")

    @tournament.command(name="stadium_remove", aliases=["removestadium", "remove_stadium", "delstadium"], help="[MANAGER] Remove a stadium from the tournament's venue pool.\nUsage: tournament stadium_remove \"<name>\"")
    async def t_stadium_remove(self, ctx, *, name: str):
        server_id = str(ctx.guild.id)
        tourney = get_server_tournament(server_id)
        if not tourney: return await ctx.send("❌ No tournament exists.")
        is_mgr = (ctx.author.id == ADMIN_DISCORD_ID) or ctx.author.guild_permissions.administrator or (str(ctx.author.id) in tourney.get("managers", []))
        if not is_mgr: return await ctx.send("❌ Managers only.")
        if is_dsl_tournament(tourney):
            return await ctx.send(f"🔒 **{DSL_CONFIG['short_name']}** venues are fixed by the league config — the pool can't be edited.")
        pool = tourney.get("stadiums", [])
        cs = canonical_stadium(name, pool)
        if not cs: return await ctx.send(f"❌ **{name.strip()}** isn't in the pool. `cvt stadiums` to view.")
        pool.remove(cs)
        if not pool:
            tourney["stadiums_cleared"] = True   # empty on purpose - don't reseed defaults
        save_tournament(tourney)
        await ctx.send(f"🗑️ Removed **{cs}** from the pool ({len(pool)} left). *Matches already on it keep the label — re-roll or `cvt set_stadium` to change.*")

    @tournament.command(name="stadium_clear", aliases=["clearstadiums", "clear_stadiums"], help="[MANAGER] Clear the entire stadium pool (fixtures then show no venue).\nUsage: tournament stadium_clear")
    async def t_stadium_clear(self, ctx):
        server_id = str(ctx.guild.id)
        tourney = get_server_tournament(server_id)
        if not tourney: return await ctx.send("❌ No tournament exists.")
        is_mgr = (ctx.author.id == ADMIN_DISCORD_ID) or ctx.author.guild_permissions.administrator or (str(ctx.author.id) in tourney.get("managers", []))
        if not is_mgr: return await ctx.send("❌ Managers only.")
        if is_dsl_tournament(tourney):
            return await ctx.send(f"🔒 **{DSL_CONFIG['short_name']}** venues are fixed by the league config — the pool can't be edited.")
        tourney["stadiums"] = []
        tourney["stadiums_cleared"] = True   # don't reseed the defaults behind their back
        save_tournament(tourney)
        await ctx.send("🧹 Stadium pool cleared — fixtures will show no venue. Add new ones with `cvt stadium_add \"<name>\"`.")

    @tournament.command(name="reroll_stadiums", aliases=["stadium_reroll", "reroll_venues"], help="[MANAGER] Randomly reassign stadiums to all upcoming matches from the pool.\nUsage: tournament reroll_stadiums")
    async def t_reroll_stadiums(self, ctx):
        server_id = str(ctx.guild.id)
        tourney = get_server_tournament(server_id)
        if not tourney: return await ctx.send("❌ No tournament exists.")
        is_mgr = (ctx.author.id == ADMIN_DISCORD_ID) or ctx.author.guild_permissions.administrator or (str(ctx.author.id) in tourney.get("managers", []))
        if not is_mgr: return await ctx.send("❌ Managers only.")
        from league.stadium_manager import linked_stadiums
        if is_dsl_tournament(tourney) or linked_stadiums(tourney):
            return await ctx.send("🔒 Matches are played at the **home team's ground** — venues can't be rerolled.")
        pool = get_stadium_pool(tourney)
        if not pool: return await ctx.send("❌ The stadium pool is empty. Add venues with `cvt stadium_add` first.")
        n = reroll_stadiums(tourney)
        save_tournament(tourney)
        await ctx.send(f"🎲 Reassigned stadiums to **{n}** upcoming match(es) from {len(pool)} venue(s).")

    @tournament.command(name="set_stadium", aliases=["setstadium", "set_venue", "venue"], help="[MANAGER] Set/override a match's stadium (cosmetic).\nUsage: tournament set_stadium <match_id> <name | none>")
    async def t_set_stadium(self, ctx, match_id: int, *, name: str):
        server_id = str(ctx.guild.id)
        tourney = get_server_tournament(server_id)
        if not tourney: return await ctx.send("❌ No tournament exists.")
        is_mgr = (ctx.author.id == ADMIN_DISCORD_ID) or ctx.author.guild_permissions.administrator or (str(ctx.author.id) in tourney.get("managers", []))
        if not is_mgr: return await ctx.send("❌ Managers only.")
        m = next((x for x in tourney.get("schedule", []) if x["match_id"] == match_id), None)
        if not m: return await ctx.send(f"❌ No match **#{match_id}** in this tournament.")
        if m.get("status") == "completed": return await ctx.send(f"❌ Match #{match_id} is already completed.")
        nm = name.strip().strip('"').strip()
        if nm.lower() in ("none", "clear", "remove", "-"):
            m.pop("stadium", None)
            save_tournament(tourney)
            return await ctx.send(f"🏟️ Cleared the stadium for match **#{match_id}**.")
        pool = tourney.get("stadiums", [])
        match_name = canonical_stadium(nm, pool)
        m["stadium"] = match_name or nm
        note = "" if match_name else " *(not in pool — set as a one-off; `cvt stadium_add` to add it)*"
        save_tournament(tourney)
        await ctx.send(f"🏟️ Match **#{match_id}** ({m['team1']} vs {m['team2']}) → 📍 **{m['stadium']}**{note}")

    # DSL (Dominators Super League) - home venues, venue stats & seasons
    @tournament.command(name="set_home_stadium", aliases=["sethomestadium", "home_stadium", "shs"], help="[MANAGER/OWNER] Set a team's home ground.\nDSL: tournament set_home_stadium \"<team>\" <venue>\nLinked-stadium tournaments: tournament set_home_stadium \"<team>\" <stadium name> <pitch>")
    async def t_set_home_stadium(self, ctx, team_name: str, *, venue: str):
        server_id = str(ctx.guild.id)
        tourney = get_server_tournament(server_id)
        if not tourney: return await ctx.send("❌ No tournament exists.")
        from league.stadium_manager import linked_stadiums
        if not is_dsl_tournament(tourney) and not linked_stadiums(tourney):
            return await ctx.send("❌ Home stadiums need a **DSL** season or a tournament created with **stadiums=linked**.")
        if tourney["status"] != "registration":
            return await ctx.send("❌ Home grounds are locked once the tournament starts.")
        team = self._team_by_ref(ctx, tourney, team_name)
        if not team: return await ctx.send(f"❌ No team **{team_name}** in this tournament (use the team name or ping its @owner).")
        is_mgr = (ctx.author.id == ADMIN_DISCORD_ID) or ctx.author.guild_permissions.administrator or (str(ctx.author.id) in tourney.get("managers", []))
        if not is_mgr and team.get("owner_id") != str(ctx.author.id):
            return await ctx.send("❌ Only Managers or the Team Owner can set the home stadium.")

        if is_dsl_tournament(tourney):
            ok, msg = set_home_stadium(tourney, team["name"], venue)
            if ok:
                save_tournament(tourney)
            return await ctx.send(msg)

        # Linked mode: free-form stadium name, LAST word must be the fixed home pitch.
        parts = venue.strip().strip('"').rsplit(None, 1)
        if len(parts) < 2:
            return await ctx.send("❌ Linked stadiums need a name **and** a pitch: `cvt set_home_stadium \"<team>\" <stadium name> <pitch>`\n"
                                  f"Pitches: {', '.join(ALL_PITCHES)}")
        stadium_name, pitch_raw = parts[0].strip().strip('"'), parts[1]
        cp = canonical_pitch(pitch_raw)
        if not cp:
            return await ctx.send(f"❌ Invalid pitch **{pitch_raw}** (the LAST word must be the pitch).\nOptions: {', '.join(ALL_PITCHES)}")
        if not stadium_name:
            return await ctx.send("❌ Provide a stadium name before the pitch.")
        team["home_stadium"] = stadium_name
        team["home_pitch"] = cp   # the linked ground CARRIES the fixed pitch (home-conditions machinery)
        save_tournament(tourney)
        await ctx.send(f"🏟️ **{team['name']}** will play their home games at **{stadium_name}** — a fixed **{cp}** pitch.")

    @tournament.command(name="home_stadiums", aliases=["homestadiums", "venues_home", "hs"], help="List each team's home ground and its pitch (DSL / linked-stadium tournaments).\nUsage: tournament home_stadiums")
    async def t_home_stadiums(self, ctx):
        server_id = str(ctx.guild.id)
        tourney = get_server_tournament(server_id)
        if not tourney: return await ctx.send("❌ No tournament exists.")
        from league.stadium_manager import linked_stadiums
        is_dsl = is_dsl_tournament(tourney)
        if not is_dsl and not linked_stadiums(tourney):
            return await ctx.send("❌ Home stadiums need a **DSL** season or a tournament created with **stadiums=linked**.")
        teams = tourney.get("teams", [])
        if not teams: return await ctx.send("📋 No teams yet.")
        lines = []
        for t in sorted(teams, key=lambda x: x["name"].lower()):
            if is_dsl:
                v = canonical_venue(t.get("home_stadium"))
                pitch = DSL_CONFIG["venues"].get(v) if v else None
            else:
                v = t.get("home_stadium")
                pitch = canonical_pitch(t.get("home_pitch"))
            if v and pitch:
                lines.append(f"• **{t['name']}** — 📍 {v} *({pitch} pitch)*")
            else:
                lines.append(f"• **{t['name']}** — ❌ *not set*")
        set_n = sum(1 for ln in lines if "❌" not in ln)
        e = discord.Embed(title=f"🏟️ {tourney['name']} — Home Grounds",
                          description="\n".join(lines), color=discord.Color.from_rgb(20, 60, 160))
        e.set_footer(text=f"{set_n}/{len(teams)} set · all required before cvt start")
        await ctx.send(embed=e)

    # Default XI
    @tournament.command(name="set_default_xi", aliases=["sdxi", "set_default11", "setdefaultxi"], help="[OWNER/MANAGER] Save your team's default XI (paste 11 names; order = batting order; '(C)' marks captain; impact tournaments: lines 12-16 = Impact Subs; 'clear' removes).\nUsage: tournament set_default_xi [team]")
    async def t_set_default_xi(self, ctx, *, team_name: str = None):
        server_id = str(ctx.guild.id)
        tourney = get_server_tournament(server_id)
        if not tourney: return await ctx.send("❌ No tournament exists.")
        is_mgr = (ctx.author.id == ADMIN_DISCORD_ID) or ctx.author.guild_permissions.administrator or (str(ctx.author.id) in tourney.get("managers", []))
        if team_name:
            team = self._team_by_ref(ctx, tourney, team_name)
            if not team: return await ctx.send(f"❌ Team **{team_name}** not found.")
            if not is_mgr and team.get("owner_id") != str(ctx.author.id):
                return await ctx.send("❌ Only Managers can set another team's default XI.")
        else:
            team = next((t for t in tourney["teams"] if t.get("owner_id") == str(ctx.author.id)), None)
            if not team: return await ctx.send("❌ You don't own a team here. Managers: `cvt set_default_xi <team>`.")
        squad = team.get("squad", [])
        if len(squad) < 11:
            return await ctx.send(f"❌ **{team['name']}** hasn't submitted a squad yet.")

        impact_on = bool(tourney.get("impact_player", False))
        impact_hint = (" You can add up to **5 Impact Subs** on lines 12-16 — they'll auto-apply with the default XI."
                       if impact_on else "")
        await ctx.send(f"📋 Paste the **default XI for {team['name']}** — 11 names, one per line "
                       f"(order = batting order, `(C)` after a name marks the captain).{impact_hint} "
                       f"Type `clear` to remove the saved default. *3 minutes.*")
        def check(m): return m.author.id == ctx.author.id and m.channel.id == ctx.channel.id
        try:
            msg = await self.bot.wait_for("message", timeout=180.0, check=check)
        except asyncio.TimeoutError:
            return await ctx.send("⏳ Timed out — run `cvt set_default_xi` again.")
        if msg.content.strip().lower() == "clear":
            team.pop("default_xi", None); team.pop("default_captain", None); team.pop("default_impact", None)
            save_tournament(tourney)
            return await msg.reply(f"🧹 Default XI cleared for **{team['name']}**.")

        max_lines = 16 if impact_on else 13
        found, missing, cap_name, cap_err, _low = parse_pasted_roster(msg.content, squad, max_lines=max_lines)
        xi, subs = found[:11], found[11:]
        errs = []
        if missing: errs.append("Not in the squad: " + ", ".join(f"**{n}**" for n in missing[:6]))
        if len(found) < 11: errs.append(f"Need at least **11** — found **{len(found)}**.")
        if not impact_on and len(found) > 11:
            errs.append(f"This tournament has **no Impact Player rule** — paste exactly 11 (found {len(found)}).")
        if cap_err == "multiple": errs.append("Multiple `(C)` markers — mark exactly one captain (or none).")
        if cap_name and len(found) >= 11 and cap_name not in {p["name"] for p in xi}:
            errs.append(f"Captain **{cap_name}** is in the impact subs — the `(C)` must be on one of the first 11.")
        if len(xi) == 11 and not _has_wk(xi): errs.append("No **Wicket-Keeper** in the XI — include a `WK`-role player.")
        if errs:
            return await msg.reply("❌ **Invalid XI:**\n• " + "\n• ".join(errs) + "\n*Run `cvt set_default_xi` again.*")

        team["default_xi"] = [p["name"] for p in xi]
        if impact_on and subs:
            team["default_impact"] = [p["name"] for p in subs]
        else:
            team.pop("default_impact", None)
        if cap_name: team["default_captain"] = cap_name
        else: team.pop("default_captain", None)
        save_tournament(tourney)
        cap_note = f"\n🧢 Captain: **{cap_name}**" if cap_name else ""
        sub_note = ("\n🔄 Impact Subs: " + ", ".join(f"**{p['name']}**" for p in subs)) if (impact_on and subs) else ""
        await msg.reply(f"✅ **Default XI saved for {team['name']}** — offered at every match start and used by sims:\n"
                        + format_xi_display(xi) + cap_note + sub_note +
                        "\n-# invalid automatically if a member is injured or leaves the squad")

    @tournament.command(name="default_xi", aliases=["dxi", "defaultxi"], help="View a team's saved default XI.\nUsage: tournament default_xi [team]")
    async def t_default_xi(self, ctx, *, team_name: str = None):
        server_id = str(ctx.guild.id)
        tourney = get_server_tournament(server_id)
        if not tourney: return await ctx.send("❌ No tournament exists.")
        if team_name:
            team = self._team_by_ref(ctx, tourney, team_name)
            if not team: return await ctx.send(f"❌ Team **{team_name}** not found.")
        else:
            team = next((t for t in tourney["teams"] if t.get("owner_id") == str(ctx.author.id)), None)
            if not team: return await ctx.send("❌ You don't own a team here — specify one: `cvt default_xi <team>`.")
        names = team.get("default_xi")
        if not names:
            return await ctx.send(f"ℹ️ **{team['name']}** has no default XI saved. `cvt set_default_xi` to save one.")
        by_name = {p["name"].lower(): p for p in team.get("squad", [])}
        lines = []
        for i, nm in enumerate(names, 1):
            p = by_name.get(nm.lower())
            if p is None:
                lines.append(f"`{i:>2}.` ~~{nm}~~ ❌ *no longer in squad*")
            elif p.get("injured"):
                lines.append(f"`{i:>2}.` **{nm}** 🚑 *injured*")
            else:
                lines.append(f"`{i:>2}.` **{nm}**")
        fit = resolve_default_xi(team, [p for p in team.get("squad", []) if not p.get("injured")])
        status = "🟢 valid — will be offered at match start" if fit else "🔴 currently INVALID (injury/missing player) — the match will ask for a typed XI"
        cap = team.get("default_captain")
        desc = "\n".join(lines) + (f"\n🧢 Captain: **{cap}**" if cap else "")
        imp_names = team.get("default_impact") or []
        if imp_names:
            imp_lines = []
            for nm in imp_names:
                p = by_name.get(nm.lower())
                if p is None: imp_lines.append(f"~~{nm}~~ ❌")
                elif p.get("injured"): imp_lines.append(f"{nm} 🚑")
                else: imp_lines.append(nm)
            desc += "\n🔄 Impact Subs: " + ", ".join(imp_lines)
        e = discord.Embed(title=f"📋 {team['name']} — Default XI",
                          description=desc,
                          color=discord.Color.green() if fit else discord.Color.red())
        e.set_footer(text=status)
        await ctx.send(embed=e)

    @tournament.command(name="duplicates", aliases=["dupes", "duplicate_players", "check_duplicates"], help="List any player who appears in more than one team's squad.\nUsage: tournament duplicates")
    async def t_duplicates(self, ctx):
        server_id = str(ctx.guild.id)
        tourney = get_server_tournament(server_id)
        if not tourney: return await ctx.send("❌ No tournament exists.")
        teams = tourney.get("teams", [])
        if not teams: return await ctx.send("📋 No teams registered yet.")

        # Map each player (case-insensitive) to the list of teams that claim him.
        owners = {}
        for t in teams:
            for p in t.get("squad", []):
                key = p["name"].strip().lower()
                entry = owners.setdefault(key, {"name": p["name"], "teams": []})
                if t["name"] not in entry["teams"]:
                    entry["teams"].append(t["name"])

        dupes = sorted((e for e in owners.values() if len(e["teams"]) > 1),
                       key=lambda e: (-len(e["teams"]), e["name"].lower()))
        submitted = sum(1 for t in teams if t.get("squad"))
        if not dupes:
            return await ctx.send(f"✅ **No duplicate players** — every player across the {submitted} submitted squad(s) is in exactly one team.")

        lines = [f"• **{e['name']}** ({len(e['teams'])} teams): "
                 + " · ".join(f"**{tm}**" for tm in e["teams"]) for e in dupes]
        e = discord.Embed(
            title=f"⚠️ {tourney['name']} — Duplicate Players ({len(dupes)})",
            description="These players appear in more than one squad — fix with `cvt replace_player` before starting:",
            color=discord.Color.red())
        # chunk to respect Discord's 1024-char field limit
        chunk, cur, first = [], 0, True
        for ln in lines:
            if cur + len(ln) + 1 > 1000 and chunk:
                e.add_field(name="Duplicates" if first else "​", value="\n".join(chunk), inline=False)
                chunk, cur, first = [], 0, False
            chunk.append(ln); cur += len(ln) + 1
        if chunk:
            e.add_field(name="Duplicates" if first else "​", value="\n".join(chunk), inline=False)
        e.set_footer(text=f"{len(dupes)} duplicated player(s) across {submitted} submitted squad(s)")
        await ctx.send(embed=e)

    @tournament.command(name="fill_squads", aliases=["fillsquads", "autofill_squads"], help="[MANAGER] Auto-fill under-min squads with unpicked players below a rating cap.\nUsage: tournament fill_squads <max_rating>")
    async def t_fill_squads(self, ctx, cap: float):
        server_id = str(ctx.guild.id)
        tourney = get_server_tournament(server_id)
        if not tourney: return await ctx.send("❌ No tournament exists.")
        is_mgr = (ctx.author.id == ADMIN_DISCORD_ID) or ctx.author.guild_permissions.administrator or (str(ctx.author.id) in tourney.get("managers", []))
        if not is_mgr: return await ctx.send("❌ Managers only.")
        if tourney["status"] != "registration":
            return await ctx.send("❌ Squads can only be auto-filled before the tournament starts.")
        min_s = tourney.get("min_squad", 11)
        under = [t for t in tourney["teams"] if len(t.get("squad", [])) < min_s]
        if not under:
            return await ctx.send(f"✅ Every team already meets the minimum squad size ({min_s}).")

        taken = {p["name"].lower() for t in tourney["teams"] for p in t.get("squad", [])}
        pool = [p for p in get_all_players()
                if p["name"].lower() not in taken and _player_overall(p) < cap]
        random.shuffle(pool)
        need_total = sum(min_s - len(t.get("squad", [])) for t in under)
        if not pool:
            return await ctx.send(f"❌ No unpicked players below rating **{cap:g}** available.")

        # Deal round-robin (one player per team per pass) so no team hogs the pool.
        plan = {t["name"]: [] for t in under}
        idx = 0
        while idx < len(pool) and any(len(t.get("squad", [])) + len(plan[t["name"]]) < min_s for t in under):
            for t in under:
                if idx >= len(pool): break
                if len(t.get("squad", [])) + len(plan[t["name"]]) < min_s:
                    plan[t["name"]].append(pool[idx]); idx += 1

        summary = "\n".join(f"• **{tn}** +{len(ps)}: " + ", ".join(p["name"] for p in ps)
                            for tn, ps in plan.items() if ps)
        short = "" if idx >= need_total or all(len(t.get("squad", [])) + len(plan[t["name"]]) >= min_s for t in under) \
            else f"\n⚠️ Pool ran out — **{need_total - idx}** slot(s) stay unfilled (raise the cap and re-run)."
        view = SquadConfirmView(ctx.author.id)
        prompt = await ctx.send(f"🧾 **Auto-fill plan** (players with rating < **{cap:g}**, unpicked by any team):\n{summary}{short}\n\nApply?", view=view)
        await view.wait()
        if not view.value:
            return await prompt.edit(content="❌ Auto-fill cancelled — squads untouched.", view=None)
        for t in under:
            t["squad"].extend(plan[t["name"]])
        save_tournament(tourney)
        filled = sum(len(ps) for ps in plan.values())
        await prompt.edit(content=f"✅ **Auto-fill complete** — added **{filled}** player(s):\n{summary}{short}", view=None)

    @tournament.command(name="venue_stats", aliases=["pitch_stats", "venuestats"], help="[DSL] All-time venue numbers across every season.\nUsage: tournament venue_stats [venue]")
    async def t_venue_stats(self, ctx, *, venue: str = None):
        server_id = str(ctx.guild.id)
        tourney = get_server_tournament(server_id)   # may be None between seasons - that's fine
        stats = aggregate_venue_stats(server_id, tourney)
        if not stats:
            return await ctx.send("📊 No venue data yet — stats build up as DSL matches are completed.")

        def _fmt(v, st, detailed=False):
            bf = f"{st['bat_first_win_pct']:.0f}%" if st["bat_first_win_pct"] is not None else "—"
            base = (f"**M:** {st['matches']} · **Avg 1st inn:** {st['avg_1st']:.0f} · **Avg 2nd inn:** {st['avg_2nd']:.0f}\n"
                    f"**Bat-first wins:** {bf} · **Hi/Lo:** {st['highest']}/{st['lowest']}")
            if detailed and st["pitch_counts"]:
                dist = " · ".join(f"{p} ×{c}" for p, c in sorted(st["pitch_counts"].items(), key=lambda kv: -kv[1]))
                base += f"\n**Pitches seen:** {dist}"
            return base

        if venue:
            v = canonical_venue(venue) or next((s for s in stats if s.lower() == venue.strip().lower()), None)
            if not v:
                return await ctx.send(f"❌ Unknown venue **{venue}**.\nVenues: " + " · ".join(DSL_CONFIG["venues"]))
            st = stats.get(v)
            if not st:
                return await ctx.send(f"📊 **{v}** hasn't hosted a completed match yet.")
            e = discord.Embed(title=f"📍 {v} — All-Time Venue Stats",
                              description=_fmt(v, st, detailed=True),
                              color=discord.Color.from_rgb(20, 60, 160))
            pitch = DSL_CONFIG["venues"].get(v)
            if pitch:
                e.add_field(name="Pitch", value=f"🏟️ **{pitch}** — fixed (same pitch every match here)", inline=False)
            e.set_footer(text="All seasons combined (archives + current)")
            return await ctx.send(embed=e)

        e = discord.Embed(title=f"📊 {DSL_CONFIG['display_name']} — Venue Stats (All-Time)",
                          color=discord.Color.from_rgb(20, 60, 160))
        for v, st in sorted(stats.items(), key=lambda kv: -kv[1]["matches"])[:12]:
            e.add_field(name=f"📍 {v}", value=_fmt(v, st), inline=True)
        e.set_footer(text="All seasons combined · cvt venue_stats <venue> for detail")
        await ctx.send(embed=e)

    @tournament.command(name="end_season", aliases=["endseason", "archive_season"], help="[MANAGER/DSL] Archive the finished season to a JSON file and free the slot for the next one.\nUsage: tournament end_season")
    async def t_end_season(self, ctx):
        server_id = str(ctx.guild.id)
        tourney = get_server_tournament(server_id)
        if not tourney: return await ctx.send("❌ No tournament exists.")
        if not is_dsl_tournament(tourney):
            return await ctx.send(f"❌ Seasons are a **{DSL_CONFIG['short_name']}** feature. Other tournaments use `cvt force_delete`.")
        is_mgr = (ctx.author.id == ADMIN_DISCORD_ID) or ctx.author.guild_permissions.administrator or (str(ctx.author.id) in tourney.get("managers", []))
        if not is_mgr: return await ctx.send("❌ Managers only.")
        if tourney.get("status") != "completed" or not tourney.get("dsl_champion"):
            return await ctx.send("❌ The season isn't finished — the **Final** must be completed first.")
        if any(m.get("status") == "pending" for m in tourney.get("schedule", [])):
            return await ctx.send("❌ There are still pending matches — finish or cancel them first.")

        season = tourney.get("season", "?")
        try:
            path, blob = write_season_archive(tourney)
        except Exception as e:
            return await ctx.send(f"❌ Archive failed — season left untouched: {e}")

        # Free the server's tournament slot (the archive is now the season of record).
        DB_CACHE["tournaments"] = [t for t in DB_CACHE.get("tournaments", [])
                                   if str(t.get("server_id")) != server_id]
        save_tournament_data_to_bin()

        file = discord.File(fp=io.BytesIO(blob), filename=os.path.basename(path))
        await ctx.send(
            f"🏁 **{tourney['name']} is in the books!** 👑 Champions: **{tourney.get('dsl_champion')}**\n"
            f"📦 Season archived to `{path}` — **commit this file to the bot's GitHub repo** so it survives redeploys "
            f"(it's also attached here as a backup).\n"
            f"🔵 Season **S{int(season) + 1 if str(season).isdigit() else '?'}** is ready whenever you are: `cvt start dsl`",
            file=file,
        )

    @tournament.command(name="seasons", aliases=["history", "champions", "season"], help="[DSL] Honours board · a season's full review · or a player's stats in that season.\nUsage: tournament seasons [number] [player]\n`cvt season 1` → S1 review · `cvt season 1 Kohli` → Kohli's S1 stats + team")
    async def t_seasons(self, ctx, season: int = None, *, player: str = None):
        server_id = str(ctx.guild.id)
        tourney = get_server_tournament(server_id)

        # `cvt season 1 <player>` -> that player's stats IN that season, with his team
        if season is not None and player:
            rows = player_season_history(server_id, player, tourney)
            if not rows:
                # fuzzy-resolve the name across all seasons, then retry exact
                agg = aggregate_player_stats(server_id, tourney)
                close = difflib.get_close_matches(player.strip().lower(), list(agg.keys()), n=1, cutoff=0.5)
                if close:
                    rows = player_season_history(server_id, agg[close[0]]["name"], tourney)
            srows = [r for r in rows if r[0] == season]
            if not srows:
                played = sorted({r[0] for r in rows})
                hint = f" They appear in: {', '.join(f'S{s}' for s in played)}." if played else ""
                return await ctx.send(f"❌ No stats for **{player}** in **S{season}**.{hint}")
            for s_no, team, pname, ps in srows:   # usually one; multiple if mid-season transfer
                await ctx.send(embed=build_player_stats_embed(
                    ps, pname, team, season_label=f"{DSL_CONFIG['short_name']} Season {s_no}"))
            return

        # `cvt season 1` -> the full season review (from the archive JSON / live season)
        if season is not None:
            data = get_season_summary(server_id, season, tourney)
            if not data:
                have = [str(s) for s, *_ in season_history(server_id, tourney)]
                hint = f" Available: {', '.join('S' + s for s in have)}" if have else ""
                return await ctx.send(f"❌ No **S{season}** found for this server.{hint}")
            return await ctx.send(embed=season_detail_embed(data))

        rows = season_history(server_id, tourney)
        if not rows:
            return await ctx.send(f"📜 No {DSL_CONFIG['short_name']} seasons yet. `cvt start dsl` begins Season 1!")
        lines = []
        for s_no, name, champ, runner in rows:
            if champ:
                lines.append(f"`S{s_no}` 👑 **{champ}**" + (f" *(def. {runner})*" if runner else ""))
            else:
                lines.append(f"`S{s_no}` ⏳ *in progress*")
        e = discord.Embed(title=f"📜 {DSL_CONFIG['display_name']} — Honours Board",
                          description="\n".join(lines), color=discord.Color.gold())
        e.set_footer(text="cvt season <number> for a season's full review")
        await ctx.send(embed=e)

    @tournament.command(name="career", aliases=["overall_stats", "alltime"], help="[DSL] A player's all-time stats across every season.\nUsage: tournament career <player>")
    async def t_career(self, ctx, *, player_name: str):
        server_id = str(ctx.guild.id)
        tourney = get_server_tournament(server_id)
        agg = aggregate_player_stats(server_id, tourney)
        if not agg:
            return await ctx.send("📊 No stats yet — they build up as DSL matches are completed.")
        key = player_name.strip().lower()
        rec = agg.get(key)
        if not rec:
            close = difflib.get_close_matches(key, list(agg.keys()), n=1, cutoff=0.5)
            if close:
                rec = agg[close[0]]
            else:
                return await ctx.send(f"❌ No DSL stats found for **{player_name}**.")
        sr   = (rec["runs"] / rec["balls_faced"] * 100) if rec["balls_faced"] > 0 else 0.0
        avg  = (rec["runs"] / rec["outs"]) if rec["outs"] > 0 else float(rec["runs"])
        econ = (rec["runs_conceded"] / rec["balls_bowled"] * 6) if rec["balls_bowled"] > 0 else 0.0
        o, b = rec["balls_bowled"] // 6, rec["balls_bowled"] % 6
        e = discord.Embed(title=f"🌏 {DSL_CONFIG['short_name']} Career: {rec['name']}",
                          description=f"**Seasons:** {rec['seasons']} · **Matches:** {rec['matches']} · **Teams:** {', '.join(rec['teams'])}",
                          color=discord.Color.from_rgb(20, 60, 160))
        e.add_field(name="🏏 Batting",
                    value=(f"**Runs:** {rec['runs']}\n**SR:** {sr:.1f} · **Avg:** {avg:.1f}\n"
                           f"**4s/6s:** {rec['fours']}/{rec['sixes']}\n**50s/100s:** {rec['fifties']}/{rec['hundreds']}"),
                    inline=True)
        e.add_field(name="🎯 Bowling",
                    value=f"**Wickets:** {rec['wickets']}\n**Economy:** {econ:.1f}\n**Overs:** {o}.{b}",
                    inline=True)
        # Season-by-season: which team he played for each season, with his headline numbers.
        rows = player_season_history(server_id, rec["name"], tourney)
        if rows:
            hist_lines = []
            for s_no, team, _pname, ps in sorted(rows, key=lambda r: (r[0] is None, r[0])):
                hist_lines.append(f"`S{s_no}` **{team}** — {ps.get('runs', 0)} runs · {ps.get('wickets', 0)} wkts ({ps.get('matches', 0)}m)")
            e.add_field(name="📜 Season history", value="\n".join(hist_lines[:12]), inline=False)
        e.set_footer(text="All seasons combined (archives + current) · cvt season <n> " + rec["name"] + " for one season's full card")
        await ctx.send(embed=e)

    @tournament.command(name="set_team_color", help="[MANAGER] Set a team's scorecard color.\nUsage: tournament set_team_color \"<team_name>\" #RRGGBB")
    async def t_set_team_color(self, ctx, team_name: str, color: str):
        import re as _re
        server_id = str(ctx.guild.id)
        tourney = get_server_tournament(server_id)
        is_mgr = (ctx.author.id == ADMIN_DISCORD_ID) or (ctx.author.guild_permissions.administrator) or (tourney and str(ctx.author.id) in tourney.get("managers", []))
        if not tourney: return await ctx.send("❌ No tournament exists.")
        if not is_mgr: return await ctx.send("❌ Managers only.")
        if not _re.match(r'^#[0-9A-Fa-f]{6}$', color):
            return await ctx.send("❌ Invalid color format. Use a 6-digit hex code like `#FF0000`.")
        team = self._team_by_ref(ctx, tourney, team_name)
        if not team: return await ctx.send(f"❌ Team **{team_name}** not found (use the team name or ping its @owner).")
        team["color"] = color.upper()
        save_tournament(tourney)
        await ctx.send(embed=discord.Embed(description=f"✅ **{team['name']}** color set to `{color.upper()}`.", color=int(color.lstrip('#'), 16)))

    @tournament.command(name="set_team_logo", help="[MANAGER/OWNER] Set a team's logo.\nUsage: cvt set_team_logo <standings|match> \"<team_name>\" <emoji_or_url>  (or attach an image)")
    async def t_set_team_logo(self, ctx, logo_type: str, team_name: str, *, value: str = None):
        server_id = str(ctx.guild.id)
        tourney = get_server_tournament(server_id)
        if not tourney:
            return await ctx.send("❌ No tournament exists.")
        if logo_type not in ("standings", "match"):
            return await ctx.send("❌ First argument must be `standings` or `match`.\nUsage: `cvt set_team_logo <standings|match> \"<team_name>\" <emoji_or_url>`")
        team = self._team_by_ref(ctx, tourney, team_name)
        if not team:
            return await ctx.send(f"❌ Team **{team_name}** not found (use the team name or ping its @owner).")
        is_mgr = (ctx.author.id == ADMIN_DISCORD_ID) or ctx.author.guild_permissions.administrator or (str(ctx.author.id) in tourney.get("managers", []))
        if not is_mgr and team.get("owner_id") != str(ctx.author.id):
            return await ctx.send("❌ Only Managers or the Team Owner can set the logo.")

        field = "logo_standings" if logo_type == "standings" else "logo_match"
        label = "Standings" if logo_type == "standings" else "Match"
        where = "points table & bracket" if logo_type == "standings" else "scorecards & match start banner"

        if ctx.message.attachments:
            att = ctx.message.attachments[0]
            if not (att.content_type and att.content_type.startswith("image/")):
                return await ctx.send("❌ Attachment must be an image file.")
            try:
                img_bytes = await att.read()
                import base64 as _b64
                mime = att.content_type.split(";")[0]
                team[field] = f"data:{mime};base64,{_b64.b64encode(img_bytes).decode()}"
            except Exception:
                team[field] = att.url
            save_tournament(tourney)
            return await ctx.send(f"✅ {label} logo for **{team['name']}** set from uploaded image — used in {where}.")
        if not value:
            return await ctx.send("❌ Provide an emoji, a URL, or attach an image.")
        import re as _re
        raw = value.strip()
        if raw.startswith("http://") or raw.startswith("https://"):
            team[field] = raw
            save_tournament(tourney)
            return await ctx.send(f"✅ {label} logo for **{team['name']}** set from URL — used in {where}.")
        if not _re.match(r'<a?:\w+:\d+>', raw):
            ge = discord.utils.get(ctx.guild.emojis, name=raw.strip(':'))
            if ge:
                raw = str(ge)
        team[field] = raw
        save_tournament(tourney)
        await ctx.send(f"✅ {label} logo for **{team['name']}** set to {raw} — used in {where}.")

    @tournament.command(name="set_scoreboard_logo", aliases=["scoreboard_logo", "set_logo", "center_logo"], help="[MANAGER] Set the centre logo on the default scoreboard.\nUsage: cvt set_scoreboard_logo custom <emoji_or_url>  (or attach an image)\n       cvt set_scoreboard_logo default   (revert to the CricVerse logo)")
    async def t_set_scoreboard_logo(self, ctx, mode: str = "custom", *, value: str = None):
        server_id = str(ctx.guild.id)
        tourney = get_server_tournament(server_id)
        if not tourney:
            return await ctx.send("❌ No tournament exists.")
        is_mgr = (ctx.author.id == ADMIN_DISCORD_ID) or ctx.author.guild_permissions.administrator or (str(ctx.author.id) in tourney.get("managers", []))
        if not is_mgr:
            return await ctx.send("❌ Managers only.")

        mode = (mode or "custom").lower()
        if mode in ("default", "reset", "clear", "remove", "cricverse", "none", "off"):
            tourney.pop("scoreboard_logo", None)
            save_tournament(tourney)
            return await ctx.send("✅ Scoreboard centre logo reset to the **default CricVerse logo**.")
        if mode != "custom":
            return await ctx.send("❌ First argument must be `custom` or `default`.\nUsage: `cvt set_scoreboard_logo custom <emoji_or_url>` (or attach an image) · `cvt set_scoreboard_logo default`")

        if ctx.message.attachments:
            att = ctx.message.attachments[0]
            if not (att.content_type and att.content_type.startswith("image/")):
                return await ctx.send("❌ Attachment must be an image file.")
            try:
                img_bytes = await att.read()
                import base64 as _b64
                mime = att.content_type.split(";")[0]
                tourney["scoreboard_logo"] = f"data:{mime};base64,{_b64.b64encode(img_bytes).decode()}"
            except Exception:
                tourney["scoreboard_logo"] = att.url
            save_tournament(tourney)
            return await ctx.send("✅ Custom scoreboard centre logo set from uploaded image.")
        if not value:
            return await ctx.send("❌ Provide an emoji, a URL, or attach an image — or use `cvt set_scoreboard_logo default` to revert.")
        import re as _re
        raw = value.strip()
        if raw.startswith("http://") or raw.startswith("https://"):
            tourney["scoreboard_logo"] = raw
        else:
            if not _re.match(r'<a?:\w+:\d+>', raw):
                ge = discord.utils.get(ctx.guild.emojis, name=raw.strip(':'))
                if ge:
                    raw = str(ge)
            tourney["scoreboard_logo"] = raw
        save_tournament(tourney)
        await ctx.send(f"✅ Custom scoreboard centre logo set to {raw}.")

    @tournament.command(name="set_injury_channel", help="[MANAGER] Set this channel as the injury report channel.\nUsage: tournament set_injury_channel")
    async def t_set_injury_channel(self, ctx):
        server_id = str(ctx.guild.id)
        tourney = get_server_tournament(server_id)
        if not tourney: return await ctx.send("❌ No tournament exists.")
        is_mgr = (ctx.author.id == ADMIN_DISCORD_ID) or ctx.author.guild_permissions.administrator or (str(ctx.author.id) in tourney.get("managers", []))
        if not is_mgr: return await ctx.send("❌ Managers only.")
        tourney["injury_channel_id"] = str(ctx.channel.id)
        save_tournament(tourney)
        await ctx.send(f"✅ Injury reports will now be posted in {ctx.channel.mention}.")

    @tournament.command(name="set_log_channel", aliases=["setlog", "log_channel"], help="[MANAGER] Set this channel as the tournament audit-log channel (logs every tournament-changing command: squad edits, pitch/stadium/theme/colour/logo changes, injuries, results, etc.).\nUsage: tournament set_log_channel [off]")
    async def t_set_log_channel(self, ctx, mode: str = None):
        server_id = str(ctx.guild.id)
        tourney = get_server_tournament(server_id)
        if not tourney: return await ctx.send("❌ No tournament exists.")
        if not self._is_tourney_mgr(ctx, tourney): return await ctx.send("❌ Managers only.")
        if mode and mode.lower() in ("off", "disable", "none", "remove", "stop"):
            tourney.pop("log_channel", None)
            save_tournament(tourney)
            return await ctx.send("🛑 Tournament logging disabled.")
        tourney["log_channel"] = str(ctx.channel.id)
        save_tournament(tourney)
        await ctx.send(f"📝 Tournament actions will now be logged in {ctx.channel.mention}.\n-# Use `cvt set_log_channel off` to stop.")

    @tournament.command(name="remove_injury", help="[MANAGER] Manually clear a player's injury.\nUsage: cvt remove_injury \"<team_name>\" \"<player_name>\"")
    async def t_remove_injury(self, ctx, team_name: str, player_name: str):
        server_id = str(ctx.guild.id)
        tourney = get_server_tournament(server_id)
        if not tourney: return await ctx.send("❌ No tournament exists.")
        is_mgr = (ctx.author.id == ADMIN_DISCORD_ID) or ctx.author.guild_permissions.administrator or (str(ctx.author.id) in tourney.get("managers", []))
        if not is_mgr: return await ctx.send("❌ Managers only.")
        team = self._team_by_ref(ctx, tourney, team_name)
        if not team: return await ctx.send(f"❌ Team **{team_name}** not found (use the team name or ping its @owner).")
        player = next((p for p in team.get("squad", []) if p["name"].lower() == player_name.lower()), None)
        if not player: return await ctx.send(f"❌ Player **{player_name}** not found in **{team['name']}**.")
        if not player.get("injured"): return await ctx.send(f"ℹ️ **{player['name']}** is not currently injured.")
        player.pop("injured", None)
        player.pop("injury_until_match", None)
        player.pop("injury_severity", None)
        player.pop("injury_matches_left", None)
        tourney["pending_injury_news"] = [
            n for n in tourney.get("pending_injury_news", [])
            if not (n["team"] == team["name"] and n["player"] == player["name"])
        ]
        save_tournament(tourney)
        await ctx.send(f"✅ Injury cleared for **{player['name']}** ({team['name']}).")

    @tournament.command(name="clear_injuries", aliases=["clearinjuries", "clear_all_injuries", "heal_all"],
                        help="[MANAGER] Clear EVERY injury in the tournament at once.\nUsage: cvt clear_injuries")
    async def t_clear_injuries(self, ctx):
        server_id = str(ctx.guild.id)
        tourney = get_server_tournament(server_id)
        if not tourney: return await ctx.send("❌ No tournament exists.")
        is_mgr = (ctx.author.id == ADMIN_DISCORD_ID) or ctx.author.guild_permissions.administrator or (str(ctx.author.id) in tourney.get("managers", []))
        if not is_mgr: return await ctx.send("❌ Managers only.")
        healed = []
        for team in tourney.get("teams", []):
            for p in team.get("squad", []):
                if p.get("injured"):
                    p.pop("injured", None)
                    p.pop("injury_until_match", None)
                    p.pop("injury_severity", None)
                    p.pop("injury_matches_left", None)
                    healed.append(f"**{p['name']}** ({team['name']})")
        tourney["pending_injury_news"] = []
        if not healed:
            return await ctx.send("ℹ️ No injured players — the tournament is fully fit.")
        save_tournament(tourney)
        listing = "\n".join(f"• {h}" for h in healed[:25])
        if len(healed) > 25:
            listing += f"\n… and {len(healed) - 25} more"
        await ctx.send(f"🏥 **All injuries cleared!** {len(healed)} player{'s' if len(healed) != 1 else ''} back to full fitness:\n{listing}")

    @tournament.command(name="add_injury", help="[MANAGER] Manually injure a player for N matches.\nUsage: cvt add_injury \"<team_name>\" \"<player_name>\" [matches=1]")
    async def t_add_injury(self, ctx, team_name: str, player_name: str, matches: int = 1):
        server_id = str(ctx.guild.id)
        tourney = get_server_tournament(server_id)
        if not tourney: return await ctx.send("❌ No tournament exists.")
        is_mgr = (ctx.author.id == ADMIN_DISCORD_ID) or ctx.author.guild_permissions.administrator or (str(ctx.author.id) in tourney.get("managers", []))
        if not is_mgr: return await ctx.send("❌ Managers only.")
        team = self._team_by_ref(ctx, tourney, team_name)
        if not team: return await ctx.send(f"❌ Team **{team_name}** not found (use the team name or ping its @owner).")
        player = next((p for p in team.get("squad", []) if p["name"].lower() == player_name.lower()), None)
        if not player: return await ctx.send(f"❌ Player **{player_name}** not found in **{team['name']}**.")
        if player.get("injured"): return await ctx.send(f"ℹ️ **{player['name']}** is already injured. Use `cvt remove_injury` first to change it.")
        matches = max(1, min(matches, 10))
        team_pending = [m for m in tourney.get("schedule", [])
                        if m["status"] == "pending" and (m["team1"] == team["name"] or m["team2"] == team["name"])]
        sev = min(matches, len(team_pending)) if team_pending else matches
        if sev <= 0:
            return await ctx.send(f"ℹ️ **{team['name']}** has no pending matches to sit out.")
        player["injured"] = True
        player["injury_severity"] = sev
        player["injury_matches_left"] = sev
        if team_pending:
            player["injury_until_match"] = team_pending[sev - 1]["match_id"]
        save_tournament(tourney)
        m_word = "match" if sev == 1 else "matches"
        # Report to the injury channel, mirroring an auto-rolled injury.
        report = f"🚑 **Injury Report:**\n• **{player['name']}** ({team['name']}) — ruled out for their next **{sev}** team {m_word}"
        owner_id = team.get("owner_id")
        if owner_id:
            report += f"\n<@{owner_id}>"
        inj_ch_id = tourney.get("injury_channel_id")
        announce_ch = (self.bot.get_channel(int(inj_ch_id)) if inj_ch_id else None) or ctx.channel
        try:
            await announce_ch.send(report)
        except Exception as _e:
            print(f"Injury report send failed: {_e}")
        await ctx.send(f"✅ **{player['name']}** ({team['name']}) injured for **{sev}** {m_word}.")

    @tournament.command(name="match_scorecard", help="View the scorecard image for a completed match.\nUsage: tournament match_scorecard <match_id>")
    async def t_match_scorecard(self, ctx, match_id: int):
        server_id = str(ctx.guild.id)
        tourney = get_server_tournament(server_id)
        if not tourney: return await ctx.send("❌ No tournament exists.")
        m = next((x for x in tourney.get("schedule", []) if x["match_id"] == match_id), None)
        if not m: return await ctx.send(f"❌ Match #{match_id} not found.")
        if m["status"] != "completed": return await ctx.send(f"❌ Match #{match_id} hasn't been completed yet.")
        r = m["result"]
        t1_s    = f"{r['t1_runs']}/{r['t1_wickets']}"
        t2_s    = f"{r['t2_runs']}/{r['t2_wickets']}"
        r_label = m.get("round", f"Match {m['match_id']}")
        embed = discord.Embed(
            title=f"Match #{match_id} — {r_label}",
            description=f"**{m['team1']}** {t1_s}  vs  **{m['team2']}** {t2_s}\n🏆 Winner: **{r['winner']}**",
            color=discord.Color.orange()
        )
        embed.set_footer(text=tourney["name"])
        full_data = reconstruct_scorecard_data(tourney, m)
        if full_data:
            sent = False
            try:
                img_buf = None
                if tourney.get("tournament_type") == "ccodi":
                    try:
                        img_buf = generate_ccodi_scorecard_from_data(full_data)
                    except Exception as _ce:
                        print(f"CCODI stored-card render failed, using generic: {_ce}")
                if img_buf is None:
                    img_buf = generate_scorecard_from_data(full_data)
                file = discord.File(fp=img_buf, filename=f"scorecard_m{match_id}.png")
                await ctx.send(embed=embed, file=file)
                sent = True
            except Exception as _e:
                print(f"Scorecard image render failed for match {match_id}: {_e}")
            try:
                card_embeds = build_stored_scorecard_embeds(full_data)
                if card_embeds:
                    await ctx.send(embeds=card_embeds)
                    sent = True
            except Exception as _e:
                print(f"Text scorecard render failed for match {match_id}: {_e}")
            if sent:
                return
        embed.add_field(name="No scorecard", value="No scorecard data saved for this match.", inline=False)
        await ctx.send(embed=embed)

    @tournament.command(name="dev_setup", help="[OWNER] Instantly fill teams with random squads and auto-start. Usage: tournament dev_setup [no]")
    async def t_dev_setup(self, ctx, auto_start: str = "yes"):
        if ctx.author.id != ADMIN_DISCORD_ID:
            return await ctx.send("❌ Owner only.")
        server_id = str(ctx.guild.id)
        tourney = get_server_tournament(server_id)
        if not tourney:
            return await ctx.send("❌ No tournament found. Run `cv tournament create` first.")
        if tourney["status"] != "registration":
            return await ctx.send("❌ Tournament already started. Use `cv tournament force_delete` first.")

        db_players = get_all_players()
        if len(db_players) < 11:
            return await ctx.send("❌ Not enough players in the database.")

        # Curated pool from draft_pool.txt: use ONLY the players listed there
        #    (one per line, flag emoji ignored). Category headers and any name not found in
        # the DB are skipped silently - never crashes on a missing/misspelt player.
        def _strip_emoji(s):
            keep = []
            for c in s:
                o = ord(c)
                if (0x1F000 <= o <= 0x1FAFF or 0x1F1E6 <= o <= 0x1F1FF or 0x2600 <= o <= 0x27BF
                        or 0xFE00 <= o <= 0xFE0F or o == 0x200D or 0xE0000 <= o <= 0xE007F
                        or 0x2B00 <= o <= 0x2BFF or 0x2190 <= o <= 0x21FF):
                    continue
                keep.append(c)
            return "".join(keep).strip()
        def _has_emoji(s):
            return any(0x1F000 <= ord(c) <= 0x1FAFF or 0x1F1E6 <= ord(c) <= 0x1F1FF
                       or 0xE0000 <= ord(c) <= 0xE007F for c in s)

        db_players = list(db_players)
        _skipped = 0
        _used_curated = False
        try:
            if tourney.get("tournament_type") == "ccodi":
                raise FileNotFoundError   # CCODI drafts straight from the full player DB
            with open("data/draft_pool.txt", encoding="utf-8") as _fh:
                _lines = _fh.read().splitlines()
            _dbmap = {p["name"].lower(): p for p in db_players}
            _curated, _seen = [], set()
            for _ln in _lines:
                if not _has_emoji(_ln):          # category header / blank -> ignore
                    continue
                _nm = _strip_emoji(_ln)
                if not _nm:
                    continue
                _p = _dbmap.get(_nm.lower())
                if _p:
                    if _p["name"] not in _seen:
                        _curated.append(_p); _seen.add(_p["name"])
                else:
                    _skipped += 1                # listed but not in DB -> skip
            if len(_curated) >= 11:
                db_players = _curated            # use the curated set as the draft pool
                _used_curated = True
        except FileNotFoundError:
            pass                                 # no file -> fall back to the full DB
        _pool_note = (f"\n📋 Used **{len(db_players)}** curated players from the list · skipped **{_skipped}** not in DB."
                      if _used_curated else "")

        t_type = tourney.get("tournament_type", "round_robin")
        min_s  = tourney.get("min_squad", 11)
        max_s  = tourney.get("max_squad", 15)

        if t_type == "t20_world_cup":
            team_config = [
                ("India", "A"), ("Pakistan", "A"), ("Australia", "A"), ("England", "A"),
                ("New Zealand", "B"), ("South Africa", "B"), ("West Indies", "B"), ("Sri Lanka", "B"),
                ("Bangladesh", "C"), ("Afghanistan", "C"), ("Zimbabwe", "C"), ("Ireland", "C"),
                ("Scotland", "D"), ("Netherlands", "D"), ("Namibia", "D"), ("Uganda", "D"),
            ]
        elif t_type == "acl":
            team_config = [
                ("Bangalore Knights", None), ("Mumbai Marathas", None), ("Karachi Supernovas", None),
                ("Rome Gladiators", None), ("London Sovereigns", None), ("Paris Vanguard", None),
                ("Sydney Skyhawks", None), ("Tokyo Ninjas", None), ("Dhaka Dynamites", None),
                ("New York Empires", None), ("Los Angeles Vipers", None), ("Cape Town Cobalts", None),
                ("Kingston Calypso", None), ("Cairo Pharaohs", None),
            ]
        elif t_type == "ccodi":
            team_config = [
                ("India", "A"), ("Australia", "A"), ("England", "A"), ("New Zealand", "A"), ("Pakistan", "A"),
                ("South Africa", "B"), ("Sri Lanka", "B"), ("Bangladesh", "B"), ("West Indies", "B"), ("Afghanistan", "B"),
            ]
        elif t_type == "dsl":
            team_config = [
                ("Mumbai Dominators", None), ("Chennai Chargers", None), ("Bangalore Blasters", None),
                ("Kolkata Krakens", None), ("Delhi Daredevils", None), ("Hyderabad Hurricanes", None),
                ("Ahmedabad Avengers", None), ("Jaipur Jaguars", None), ("Punjab Panthers", None),
                ("Lucknow Legends", None), ("Navi Mumbai Ninjas", None), ("Dharamsala Dragons", None),
            ][:DSL_CONFIG["team_count"]]
        else:
            team_config = [
                ("Thunder Kings", None), ("Lightning Bolts", None), ("Storm Riders", None),
                ("Fire Hawks", None), ("Ice Wolves", None), ("Steel Giants", None),
                ("Golden Lions", None), ("Silver Eagles", None),
            ]

        # Snake draft from one ranked pool: prioritises high-rated players, balances the
        # teams, and guarantees every player is dealt to ONLY ONE team (no shared players).
        # Rating DOMINATES (small ±2.5 jitter only breaks near-ties) so every genuinely good
        # player is guaranteed into the drafted pool - the top (teams × squad) all get a squad,
        # and _best_xi then puts the best of each squad into the XI that actually plays.
        num_teams = len(team_config)
        ranked = sorted(db_players, key=lambda p: _player_overall(p) + random.uniform(-2.5, 2.5), reverse=True)
        squads = [[] for _ in range(num_teams)]
        _idx = 0
        for _rnd in range(max_s):
            order = range(num_teams) if _rnd % 2 == 0 else range(num_teams - 1, -1, -1)
            for _t in order:
                if _idx >= len(ranked):
                    break
                squads[_t].append(ranked[_idx]); _idx += 1
        tourney["teams"] = []
        for _i, (name, grp) in enumerate(team_config):
            tourney["teams"].append({"name": name, "owner_id": str(ctx.author.id), "squad": squads[_i], "group": grp})

        should_start = auto_start.lower() not in ("no", "false", "0")
        if not should_start:
            save_tournament(tourney)
            return await ctx.send(f"✅ **Dev Setup Complete!** Added {len(tourney['teams'])} teams. Run `cv tournament start` when ready.{_pool_note}")

        # Auto-start via the shared generator (which also applies the conditions mode).
        # For home-pitch mode, give any team without one a random standard home pitch so
        # dev_setup remains one-shot.
        if tourney.get("conditions_mode") == "home":
            for _t in tourney["teams"]:
                if not canonical_pitch(_t.get("home_pitch")):
                    _t["home_pitch"] = random.choice(["Flat", "Dead", "Hard", "Green", "Dusty"])
        # DSL: deal each team a distinct home venue so the start validation passes one-shot.
        if t_type == "dsl":
            _venues = list(DSL_CONFIG["venues"])
            for _i, _t in enumerate(tourney["teams"]):
                if not canonical_venue(_t.get("home_stadium")):
                    _t["home_stadium"] = _venues[_i % len(_venues)]
        # Linked stadiums: invent a ground + pitch per team so dev_setup stays one-shot.
        elif tourney.get("stadium_mode") == "linked":
            _pitches = ["Flat", "Dead", "Hard", "Green", "Dusty", "Slow", "Turning", "Bouncy"]
            for _i, _t in enumerate(tourney["teams"]):
                if not _t.get("home_stadium"):
                    _t["home_stadium"] = f"{_t['name']} Stadium"
                if not canonical_pitch(_t.get("home_pitch")):
                    _t["home_pitch"] = _pitches[_i % len(_pitches)]
        save_tournament(tourney)
        await ctx.send(f"⚡ **Dev Setup** — added **{len(tourney['teams'])}** teams. Generating…{_pool_note}")
        await self._generate_and_start(ctx.channel, tourney)

    @tournament.command(name="next_match", aliases=["nm"], help="[OWNER] Launch your team's next pending match.\nUsage: tournament next_match")
    async def t_next_match(self, ctx):
        server_id = str(ctx.guild.id)
        tourney = get_server_tournament(server_id)
        if not tourney: return await ctx.send("❌ No tournament exists.")
        if tourney["status"] != "active": return await ctx.send("❌ Tournament is not active.")
        my_team = next((t for t in tourney["teams"] if t["owner_id"] == str(ctx.author.id)), None)
        if not my_team: return await ctx.send("❌ You are not a Team Owner in this tournament.")
        my_matches = [m for m in tourney.get("schedule", []) if m["status"] == "pending" and (m["team1"] == my_team["name"] or m["team2"] == my_team["name"])]
        if not my_matches: return await ctx.send(f"✅ **{my_team['name']}** has no pending matches right now!")
        # Under an order policy, pick my earliest LAUNCHABLE match (not just earliest).
        match, gate_msg = None, ""
        for m in my_matches:
            ok, gate_msg = match_order_gate(tourney, m)
            if ok:
                match = m
                break
        if match is None:
            return await ctx.send(gate_msg)
        r_label = f"Round {match['round']}" if isinstance(match['round'], int) else match['round']
        await ctx.send(f"🚀 **Launching Match {match['match_id']} ({r_label})...**")
        self.bot.dispatch("start_tournament_match", ctx.channel, ctx.author.id, tourney, match)

    @tournament.command(name="award_win", aliases=["walkover", "award"], help="[MANAGER] Award a match to a team — pure walkover: 2 points & a W, no stats, no NRR impact.\nUsage: tournament award_win <match_id> <team>")
    async def t_award_win(self, ctx, match_id: int, *, team_name: str):
        server_id = str(ctx.guild.id)
        tourney = get_server_tournament(server_id)
        if not tourney: return await ctx.send("❌ No tournament exists.")
        is_mgr = (ctx.author.id == ADMIN_DISCORD_ID) or (ctx.author.guild_permissions.administrator) or (str(ctx.author.id) in tourney.get("managers", []))
        if not is_mgr: return await ctx.send("❌ Managers only.")
        if tourney["status"] != "active": return await ctx.send("❌ Tournament is not active.")
        m_data = next((m for m in tourney.get("schedule", []) if m.get("match_id") == match_id), None)
        if not m_data: return await ctx.send(f"❌ Match #{match_id} not found.")
        if m_data["status"] == "completed": return await ctx.send(f"❌ Match #{match_id} is already completed. (`cvt redo {match_id}` first to replay/re-award.)")
        if m_data["status"] == "locked":
            return await ctx.send(f"❌ Match #{match_id} is **locked** — its teams aren't decided yet.")
        t1_name, t2_name = m_data["team1"], m_data["team2"]
        winner = next((n for n in (t1_name, t2_name) if n.lower() == team_name.strip().lower()), None)
        if not winner:
            return await ctx.send(f"❌ **{team_name}** isn't in this match. It's **{t1_name}** vs **{t2_name}**.")
        loser = t2_name if winner == t1_name else t1_name

        view = SquadConfirmView(ctx.author.id)
        prompt = await ctx.send(
            f"⚠️ Award **Match #{match_id}** ({t1_name} vs {t2_name}) to **{winner}** as a **walkover**?\n"
            f"• {winner}: +1 W, +2 points · {loser}: +1 L\n"
            f"• **No player stats, no NRR impact** for either side.", view=view)
        await view.wait()
        if not view.value:
            return await prompt.edit(content="❌ Award cancelled — match left as-is.", view=None)

        # Walkover result: zero runs/balls contribute nothing to NRR (0 runs over 0 overs),
        # no batted_first -> venue stats skip it, no stats_delta -> nothing on leaderboards.
        m_data["status"] = "completed"
        m_data["result"] = {
            "winner": winner, "loser": loser, "format_overs": tourney.get("format_overs", 20),
            "t1_runs": 0, "t1_wickets": 0, "t1_balls": 0,
            "t2_runs": 0, "t2_wickets": 0, "t2_balls": 0,
            "walkover": True, "stats_delta": None,
        }
        tourney["current_match_idx"] = tourney.get("current_match_idx", 0) + 1

        # Knockout / bracket progression - same paths a played match triggers.
        t_type = tourney.get("tournament_type", "round_robin")
        if t_type == "acl":
            _acl_try_advance(tourney)
        elif t_type == "ipl":
            ipl_try_advance(tourney)
        elif t_type == "dsl":
            from league.dsl_manager import DSL_CONFIG, dsl_generate_playoffs, _dsl_try_advance
            if DSL_CONFIG["auto_playoffs"]:
                dsl_generate_playoffs(tourney)
            _dsl_try_advance(tourney)
        else:
            tc = self.bot.cogs.get("TournamentCog")
            if t_type == "t20_world_cup" and tc:
                tc._try_generate_semis(tourney)
            sf1 = next((m for m in tourney["schedule"] if m.get("round") == "Semi-Final 1"), None)
            sf2 = next((m for m in tourney["schedule"] if m.get("round") == "Semi-Final 2"), None)
            if sf1 and sf2 and sf1["status"] == "completed" and sf2["status"] == "completed":
                if not any(m.get("round") == "Final" for m in tourney["schedule"]):
                    tourney["schedule"].append({"match_id": _tm_next_mid(tourney), "round": "Final", "stage": "knockout",
                                                "team1": sf1["result"]["winner"], "team2": sf2["result"]["winner"],
                                                "status": "pending", "result": None})
            final_m = next((m for m in tourney["schedule"] if m.get("round") == "Final"), None)
            if final_m and final_m["status"] == "completed" and tourney["status"] != "completed":
                tourney["status"] = "completed"
        assign_tournament_conditions(tourney)
        save_tournament(tourney)
        r_label = f"Round {m_data['round']}" if isinstance(m_data['round'], int) else m_data['round']
        await prompt.edit(content=(f"🏆 **Match #{match_id}** ({r_label}) awarded to **{winner}** — walkover recorded.\n"
                                   f"Points table gets the W/L; player stats and NRR are untouched."), view=None)

    @tournament.command(name="scorecard_channel", aliases=["scc", "set_scorecard_channel"], help="[MANAGER] Auto-post every completed match's scoreboard image to a channel (off to disable).\nUsage: tournament scorecard_channel <#channel | off>")
    async def t_scorecard_channel(self, ctx, target: str):
        server_id = str(ctx.guild.id)
        tourney = get_server_tournament(server_id)
        if not tourney: return await ctx.send("❌ No tournament exists.")
        is_mgr = (ctx.author.id == ADMIN_DISCORD_ID) or (ctx.author.guild_permissions.administrator) or (str(ctx.author.id) in tourney.get("managers", []))
        if not is_mgr: return await ctx.send("❌ Managers only.")
        if target.strip().lower() in ("off", "none", "clear", "disable"):
            tourney.pop("scorecard_channel_id", None)
            save_tournament(tourney)
            return await ctx.send("🔕 Scorecard channel cleared — match images no longer auto-post.")
        ch = None
        if ctx.message.channel_mentions:
            ch = ctx.message.channel_mentions[0]
        elif target.strip().isdigit():
            ch = ctx.guild.get_channel(int(target.strip()))
        if ch is None:
            return await ctx.send("❌ Mention a channel (`cvt scorecard_channel #match-gallery`) or pass its ID, or `off` to disable.")
        tourney["scorecard_channel_id"] = str(ch.id)
        save_tournament(tourney)
        await ctx.send(f"🖼️ **Scorecard channel set:** every completed match's scoreboard image now auto-posts to {ch.mention} "
                       f"(real matches and sims alike).\n-# `cvt post_scorecards {ch.mention}` back-fills matches already played.")

    @tournament.command(name="post_scorecards", aliases=["dump_scorecards", "scorecards_dump", "psc"], help="[MANAGER] Slowly post EVERY completed match's scoreboard image to a channel — the tournament's permanent gallery/archive. Works after the tournament has finished.\nUsage: tournament post_scorecards [#channel]")
    async def t_post_scorecards(self, ctx, target: str = None):
        server_id = str(ctx.guild.id)
        tourney = get_server_tournament(server_id)
        if not tourney: return await ctx.send("❌ No tournament exists.")
        is_mgr = (ctx.author.id == ADMIN_DISCORD_ID) or (ctx.author.guild_permissions.administrator) or (str(ctx.author.id) in tourney.get("managers", []))
        if not is_mgr: return await ctx.send("❌ Managers only.")
        # NOTE: deliberately NO status check - this is the post-tournament archive dump.

        ch = None
        if ctx.message.channel_mentions:
            ch = ctx.message.channel_mentions[0]
        elif target and target.strip().isdigit():
            ch = ctx.guild.get_channel(int(target.strip()))
        elif target is None:
            sc_id = tourney.get("scorecard_channel_id")
            ch = self.bot.get_channel(int(sc_id)) if sc_id else ctx.channel
        if ch is None:
            return await ctx.send("❌ Couldn't resolve that channel — mention it like `cvt post_scorecards #gallery`.")

        done_matches = sorted((m for m in tourney.get("schedule", []) if m.get("status") == "completed"),
                              key=lambda m: m.get("match_id", 0))
        if not done_matches:
            return await ctx.send("❌ No completed matches to post yet.")

        est = len(done_matches) * 2
        status = await ctx.send(f"🖼️ Posting **{len(done_matches)}** match scoreboards to {ch.mention}, one by one "
                                f"(~{est // 60}m {est % 60}s — paced so Discord doesn't rate-limit)…")
        header = f"📚 **{tourney['name']} — Match Archive** ({len(done_matches)} matches)"
        try:
            await ch.send(header)
        except Exception as e:
            return await status.edit(content=f"❌ Can't post in {ch.mention}: {e}")

        posted = skipped = 0
        for i, m in enumerate(done_matches, 1):
            # Fresh lookup guard: bail out cleanly if the tournament vanishes mid-dump.
            if get_server_tournament(server_id) is not tourney:
                break
            try:
                full = reconstruct_scorecard_data(tourney, m)
                if not full:
                    skipped += 1          # walkovers / manually-recorded results have no card
                    continue
                buf = None
                if tourney.get("tournament_type") == "ccodi":
                    try:
                        buf = generate_ccodi_scorecard_from_data(full)
                    except Exception as _ce:
                        print(f"CCODI stored-card render failed, using generic: {_ce}")
                if buf is None:
                    buf = generate_scorecard_from_data(full)
                r_label = f"Round {m['round']}" if isinstance(m.get("round"), int) else m.get("round", "")
                await ch.send(f"**Match #{m['match_id']}** · {r_label}",
                              file=discord.File(fp=buf, filename=f"scorecard_m{m['match_id']}.png"))
                posted += 1
            except Exception as e:
                skipped += 1
                print(f"post_scorecards failed for match {m.get('match_id')}: {e}")
            # Pacing: one image every ~2s keeps well inside Discord's rate limits
            # even for a 132-match season dump.
            await asyncio.sleep(2.0)
            if i % 20 == 0:
                try:
                    await status.edit(content=f"🖼️ Posting to {ch.mention}… **{i}/{len(done_matches)}** done.")
                except discord.HTTPException:
                    pass

        note = f"  ·  ⚠️ {skipped} without stored card data (walkovers/manual results)" if skipped else ""
        await status.edit(content=f"✅ **Archive complete:** {posted} scoreboards posted to {ch.mention}{note}.")

    @tournament.command(name="lock_stats", aliases=["lockstats", "stats_lock"], help="[MANAGER] Freeze the player-stats table — matches played while locked record NO player stats (points/NRR still count).\nUsage: tournament lock_stats")
    async def t_lock_stats(self, ctx):
        server_id = str(ctx.guild.id)
        tourney = get_server_tournament(server_id)
        if not tourney: return await ctx.send("❌ No tournament exists.")
        is_mgr = (ctx.author.id == ADMIN_DISCORD_ID) or (ctx.author.guild_permissions.administrator) or (str(ctx.author.id) in tourney.get("managers", []))
        if not is_mgr: return await ctx.send("❌ Managers only.")
        if tourney.get("stats_locked"):
            return await ctx.send("🔒 The stats table is **already locked**. `cvt unlock_stats` to resume recording.")
        tourney["stats_locked"] = True
        save_tournament(tourney)
        await ctx.send("🔒 **Stats table LOCKED** — matches played from now on won't add to any player's tournament stats "
                       "(results, points and NRR still count). `cvt unlock_stats` to resume.")

    @tournament.command(name="unlock_stats", aliases=["unlockstats", "stats_unlock"], help="[MANAGER] Resume recording player stats after a lock.\nUsage: tournament unlock_stats")
    async def t_unlock_stats(self, ctx):
        server_id = str(ctx.guild.id)
        tourney = get_server_tournament(server_id)
        if not tourney: return await ctx.send("❌ No tournament exists.")
        is_mgr = (ctx.author.id == ADMIN_DISCORD_ID) or (ctx.author.guild_permissions.administrator) or (str(ctx.author.id) in tourney.get("managers", []))
        if not is_mgr: return await ctx.send("❌ Managers only.")
        if not tourney.get("stats_locked"):
            return await ctx.send("🔓 The stats table isn't locked.")
        tourney["stats_locked"] = False
        save_tournament(tourney)
        await ctx.send("🔓 **Stats table UNLOCKED** — player stats record normally again. "
                       "*(Matches played during the lock stay excluded.)*")

    @tournament.command(name="rebuild_stats", aliases=["rebuildstats", "fix_stats", "recount_stats"],
                        help="[MANAGER] Recompute the whole player-stats leaderboard from the schedule — fixes double-counted stats from a match that was recorded twice.\nUsage: tournament rebuild_stats")
    async def t_rebuild_stats(self, ctx):
        server_id = str(ctx.guild.id)
        tourney = get_server_tournament(server_id)
        if not tourney: return await ctx.send("❌ No tournament exists.")
        is_mgr = (ctx.author.id == ADMIN_DISCORD_ID) or (ctx.author.guild_permissions.administrator) or (str(ctx.author.id) in tourney.get("managers", []))
        if not is_mgr: return await ctx.send("❌ Managers only.")

        counted, exact, approx, players = rebuild_tournament_stats(tourney)
        save_tournament(tourney)
        msg = (f"🧮 **Leaderboard rebuilt from the schedule.**\n"
               f"• Counted **{counted}** completed match{'es' if counted != 1 else ''} — each contributes exactly once, "
               f"so any double-counted stats are now corrected.\n"
               f"• **{players}** players tallied.")
        if approx:
            msg += (f"\n⚠️ {approx} older match{'es' if approx != 1 else ''} had no exact stats snapshot — those were "
                    f"rebuilt best-effort from their scorecards (a benched player's match count may be off by a little).")
        msg += "\n📊 `cvt leaderboard` to check · points & NRR were never affected (they come straight from results)."
        await ctx.send(msg)

    @tournament.command(name="admin_record_result", help="[MANAGER] Manually record a match result.\nUsage: tournament admin_record_result <id> <winner> <t1_r> <t1_w> <t1_b> <t2_r> <t2_w> <t2_b>")
    async def t_admin_record_result(self, ctx, match_id: int, winner: str, t1_runs: int, t1_wickets: int, t1_balls: int, t2_runs: int, t2_wickets: int, t2_balls: int):
        server_id = str(ctx.guild.id)
        tourney = get_server_tournament(server_id)
        is_mgr = (ctx.author.id == ADMIN_DISCORD_ID) or (ctx.author.guild_permissions.administrator) or (tourney and str(ctx.author.id) in tourney.get("managers", []))
        if not tourney: return await ctx.send("❌ No tournament exists.")
        if not is_mgr: return await ctx.send("❌ Managers only.")
        if tourney["status"] != "active": return await ctx.send("❌ Tournament is not active.")
        m_data = next((m for m in tourney.get("schedule", []) if m["match_id"] == match_id), None)
        if not m_data: return await ctx.send(f"❌ Match ID {match_id} not found.")
        if m_data["status"] == "completed": return await ctx.send(f"❌ Match {match_id} already completed.")
        if m_data["status"] == "locked":
            return await ctx.send(f"❌ Match {match_id} is **locked** — its teams aren't decided yet (waiting on earlier results).")
        t1_name, t2_name = m_data["team1"], m_data["team2"]
        winner_clean = winner.strip()
        if winner_clean not in (t1_name, t2_name, "TIE"):
            return await ctx.send(f"❌ Winner must be **{t1_name}**, **{t2_name}**, or **TIE**.")
        loser_clean = None
        if winner_clean in (t1_name, t2_name):
            loser_clean = t2_name if winner_clean == t1_name else t1_name
        m_data["status"] = "completed"
        m_data["result"] = {
            "winner": winner_clean, "loser": loser_clean, "format_overs": tourney.get("format_overs", 20),
            "t1_runs": t1_runs, "t1_wickets": t1_wickets, "t1_balls": t1_balls,
            "t2_runs": t2_runs, "t2_wickets": t2_wickets, "t2_balls": t2_balls,
        }
        tourney["current_match_idx"] = tourney.get("current_match_idx", 0) + 1
        if tourney.get("tournament_type") == "acl":
            _acl_try_advance(tourney)
        elif tourney.get("tournament_type") == "ipl":
            ipl_try_advance(tourney)
        else:
            sf1 = next((m for m in tourney["schedule"] if m["round"] == "Semi-Final 1"), None)
            sf2 = next((m for m in tourney["schedule"] if m["round"] == "Semi-Final 2"), None)
            if sf1 and sf2 and sf1["status"] == "completed" and sf2["status"] == "completed":
                if not any(m["round"] == "Final" for m in tourney["schedule"]):
                    tourney["schedule"].append({"match_id": _tm_next_mid(tourney), "round": "Final", "stage": "knockout", "team1": sf1["result"]["winner"], "team2": sf2["result"]["winner"], "status": "pending", "result": None})
            final_m = next((m for m in tourney["schedule"] if m["round"] == "Final"), None)
            if final_m and final_m["status"] == "completed":
                tourney["status"] = "completed"
        save_tournament(tourney)
        overs1 = f"{t1_balls//6}.{t1_balls%6}"; overs2 = f"{t2_balls//6}.{t2_balls%6}"
        r_label = f"Round {m_data['round']}" if isinstance(m_data['round'], int) else m_data['round']
        await ctx.send(embed=discord.Embed(title=f"✅ Match {match_id} Result Recorded", description=f"**{r_label}** — {t1_name} vs {t2_name}\n🏏 {t1_name}: {t1_runs}/{t1_wickets} ({overs1})\n🏏 {t2_name}: {t2_runs}/{t2_wickets} ({overs2})\n🏆 **Winner: {winner_clean}**", color=discord.Color.green()))

    @tournament.command(name="admin_restore_schedule", help="[MANAGER] Regenerate schedule (no shuffle, for post-restart).\nUsage: tournament admin_restore_schedule")
    async def t_admin_restore_schedule(self, ctx):
        server_id = str(ctx.guild.id)
        tourney = get_server_tournament(server_id)
        is_mgr = (ctx.author.id == ADMIN_DISCORD_ID) or (ctx.author.guild_permissions.administrator) or (tourney and str(ctx.author.id) in tourney.get("managers", []))
        if not tourney: return await ctx.send("❌ No tournament exists.")
        if not is_mgr: return await ctx.send("❌ Managers only.")
        completed = [m for m in tourney.get("schedule", []) if m["status"] == "completed"]
        if completed:
            return await ctx.send(f"⚠️ **{len(completed)} completed match(es)** found. This will wipe the schedule.\nUse `cv tournament admin_force_restore_schedule` to proceed anyway.")
        await self._do_restore_schedule_prefix(ctx, tourney)

    @tournament.command(name="admin_force_restore_schedule", help="[MANAGER] Force-regenerate schedule (wipes completed matches).\nUsage: tournament admin_force_restore_schedule")
    async def t_admin_force_restore_schedule(self, ctx):
        server_id = str(ctx.guild.id)
        tourney = get_server_tournament(server_id)
        is_mgr = (ctx.author.id == ADMIN_DISCORD_ID) or (ctx.author.guild_permissions.administrator) or (tourney and str(ctx.author.id) in tourney.get("managers", []))
        if not tourney: return await ctx.send("❌ No tournament exists.")
        if not is_mgr: return await ctx.send("❌ Managers only.")
        await self._do_restore_schedule_prefix(ctx, tourney)

    async def _do_restore_schedule_prefix(self, ctx, tourney):
        teams = [t["name"] for t in tourney["teams"]]
        if len(teams) < 2: return await ctx.send("❌ Need at least 2 teams.")
        schedule = generate_round_robin_schedule(
            teams,
            double=(tourney.get("tournament_type") == "double_round_robin"),
            stage=("league" if tourney.get("tournament_type") == "double_round_robin" else None),
            shuffle=False,
        )
        tourney["schedule"] = schedule; tourney["status"] = "active"; tourney["current_match_idx"] = 0
        save_tournament(tourney)
        r1 = [m for m in schedule if m["round"] == 1]
        preview = "**Round 1:**\n" + "\n".join(f"  Match {m['match_id']}: {m['team1']} vs {m['team2']}" for m in r1)
        await ctx.send(embed=discord.Embed(title=f"✅ Schedule Restored — {tourney['name']}", description=preview, color=discord.Color.green()))

    @tournament.command(name="leaderboard", aliases=["lb"], help="View the tournament leaderboard.\nUsage: tournament lb <runs|wickets|sr|bat_avg|fours|sixes|fifties|hundreds|econ|bowl_avg|mvp>")
    async def t_leaderboard(self, ctx, category: str = "runs"):
        server_id = str(ctx.guild.id)
        tourney = get_server_tournament(server_id)
        if not tourney: return await ctx.send("❌ No tournament exists.")
        valid_cats = {"runs", "wickets", "sr", "bat_avg", "fours", "sixes", "fifties", "hundreds", "econ", "bowl_avg", "mvp"}
        c_val = category.lower()
        if c_val not in valid_cats:
            return await ctx.send(f"❌ Invalid category. Choose from: {', '.join(sorted(valid_cats))}")
        all_players = [{"name": p_name, "team": t_name, "stats": stats} for t_name, players in tourney.get("stats", {}).items() for p_name, stats in players.items()]
        if not all_players: return await ctx.send("❌ No stats yet. Complete a match first!")

        def _mvp(s):
            sr = (s["runs"] / s["balls_faced"] * 100) if s["balls_faced"] > 0 else 0
            bat = float(s["runs"])
            if sr >= 150: bat *= 1.30
            elif sr >= 130: bat *= 1.20
            elif sr >= 110: bat *= 1.10
            elif sr < 80 and s["balls_faced"] >= 20: bat *= 0.85
            bat += s["fifties"] * 15 + s["hundreds"] * 40 + s["sixes"] * 2 + s["fours"] * 0.5
            econ = (s["runs_conceded"] / s["balls_bowled"] * 6) if s["balls_bowled"] > 0 else 9.0
            bowl = float(s["wickets"] * 40)
            if s["balls_bowled"] >= 12: bowl += max(-25.0, min(25.0, (8.0 - econ) * 5))
            return bat + bowl

        if c_val == "runs": sp = sorted(all_players, key=lambda x: x["stats"]["runs"], reverse=True)
        elif c_val == "wickets": sp = sorted(all_players, key=lambda x: x["stats"]["wickets"], reverse=True)
        elif c_val == "sr": sp = sorted([p for p in all_players if p["stats"]["runs"] >= 50], key=lambda x: (x["stats"]["runs"]/x["stats"]["balls_faced"]*100) if x["stats"]["balls_faced"] > 0 else 0, reverse=True)
        elif c_val == "bat_avg": sp = sorted([p for p in all_players if p["stats"]["runs"] >= 50], key=lambda x: x["stats"]["runs"]/max(1, x["stats"]["outs"]), reverse=True)
        elif c_val in {"fours", "sixes", "fifties", "hundreds"}: sp = sorted(all_players, key=lambda x: x["stats"][c_val], reverse=True)
        elif c_val == "econ": sp = sorted([p for p in all_players if p["stats"]["balls_bowled"] >= 30], key=lambda x: (x["stats"]["runs_conceded"]/x["stats"]["balls_bowled"]*6) if x["stats"]["balls_bowled"] > 0 else 999)
        elif c_val == "bowl_avg": sp = sorted([p for p in all_players if p["stats"]["wickets"] >= 3], key=lambda x: x["stats"]["runs_conceded"]/x["stats"]["wickets"] if x["stats"]["wickets"] > 0 else 999)
        elif c_val == "mvp": sp = sorted(all_players, key=lambda x: _mvp(x["stats"]), reverse=True)
        else: sp = []
        cat_labels = {"runs":"Most Runs","wickets":"Most Wickets","sr":"Best Strike Rate","bat_avg":"Best Batting Avg","fours":"Most Fours","sixes":"Most Sixes","fifties":"Most 50s","hundreds":"Most 100s","econ":"Best Economy","bowl_avg":"Best Bowling Avg","mvp":"MVP Score"}
        # runs / wickets / MVP -> first 50, paginated 10-per-page with buttons.
        PAGINATED = {"runs", "wickets", "mvp"}
        limit = 50 if c_val in PAGINATED else 10
        title = f"🏆 Leaderboard: {cat_labels.get(c_val, c_val)}"
        header = ("-# MVP = Runs (×SR mult) + boundary/milestone bonus + Wickets×40 + economy bonus"
                  if c_val == "mvp" else "")
        lines = []
        for i, p in enumerate(sp[:limit], 1):
            s = p["stats"]
            if c_val == "runs": val = f"**{s['runs']}** runs"
            elif c_val == "wickets": val = f"**{s['wickets']}** wkts"
            elif c_val == "sr": val = f"**{(s['runs']/s['balls_faced']*100):.1f}** SR" if s['balls_faced'] > 0 else "N/A"
            elif c_val == "bat_avg": val = f"**{s['runs']/max(1,s['outs']):.1f}** avg"
            elif c_val in {"fours","sixes","fifties","hundreds"}: val = f"**{s[c_val]}**"
            elif c_val == "econ": val = f"**{(s['runs_conceded']/s['balls_bowled']*6):.1f}** econ" if s['balls_bowled'] > 0 else "N/A"
            elif c_val == "bowl_avg": val = f"**{s['runs_conceded']/s['wickets']:.1f}** avg" if s['wickets'] > 0 else "N/A"
            elif c_val == "mvp": val = f"**{_mvp(s):.0f}** pts"
            else: val = ""
            lines.append(f"`{i:>2}.` **{p['name']}** ({p['team']}) — {val}")
        if c_val in PAGINATED:
            view = TournamentLeaderboardView(title, header, lines)
            await ctx.send(embed=view.make_embed(), view=view)
        else:
            embed = discord.Embed(title=title, color=discord.Color.gold())
            embed.description = (header + "\n" if header else "") + ("\n".join(lines) if lines else "No players qualify yet.")
            await ctx.send(embed=embed)

    @tournament.command(name="player_stats", aliases=["ps"], help="View a player's tournament stats — team optional.\nUsage: tournament player_stats <player>")
    async def t_player_stats(self, ctx, *, player_name: str):
        server_id = str(ctx.guild.id)
        tourney = get_server_tournament(server_id)
        if not tourney: return await ctx.send("❌ No tournament exists.")
        stats_map = tourney.get("stats", {})
        if not stats_map: return await ctx.send("❌ No stats yet. Complete a match first!")
        # Search every team by player name; ask which team if the name is shared.
        matches = find_player_in_tournament(tourney, player_name)
        if not matches:
            return await ctx.send(f"❌ Player '{player_name}' not found in any team.")
        if len(matches) == 1:
            t, p = matches[0]
            overall, season_label = None, None
            if is_dsl_tournament(tourney):
                overall = aggregate_player_stats(server_id, tourney).get(p.lower())
                season_label = f"{DSL_CONFIG['short_name']} Season {tourney.get('season', '?')}"
            return await ctx.send(embed=build_player_stats_embed(
                stats_map[t][p], p, t, overall=overall, season_label=season_label))
        view = PlayerStatsTeamSelectView(stats_map, matches)
        await ctx.send(f"🔎 **{matches[0][1]}** is on multiple teams — pick which one:", view=view)

    @tournament.command(name="help_guide", aliases=["help", "commands", "guide"], help="Show the tournament commands guide.\nUsage: cvt help")
    async def t_help_guide(self, ctx):
        # Card 1: quickstart flows per event type
        qs = discord.Embed(
            title="🏆 Tournament Guide  ·  Quickstarts",
            description=("Event types: **Round Robin** · **Double Round Robin** · **T20 World Cup** (4 groups → Super 8 → KO) · "
                         "**ACL** (14 teams → League → Playoffs → Super Cup) · **CCODI** (10 teams, ODI, "
                         "2 groups of 5, round-wise double RR → Qualifiers ladder) · **DSL** (recurring seasons) · "
                         "**Conquest** (open Elo ladder).\nFull command list is in the second card below. ⬇️"),
            color=discord.Color.gold(),
        )
        qs.add_field(
            name="🔴 ACL",
            value=("**1.** `cvt create \"ACL S1\" t20 event=acl`\n"
                   "**2.** `cvt add_team \"<team>\" @owner` ×**14** · owners `cvt ss`\n"
                   "**3.** `cvt start` → 91 league matches · `cvt fx` → `cvt play <id>`\n"
                   "**4.** after all 91: mgr `cvt gp` → `cvt br` → Playoffs → **Super Cup** 👑"),
            inline=False,
        )
        qs.add_field(
            name="🟠 CCODI (10 teams · ODI)",
            value=("**1.** `cvt create \"CCODI S1\" odi event=ccodi`\n"
                   "**2.** `cvt add_team \"<team>\" @owner A` — **group A/B required**, 5 per group\n"
                   "**3.** owners `cvt ss` · `cvt start` → double round robin (40 group games)\n"
                   "**4.** top 2 per group → **KO1** (A1 v B1) & **KO2** (A2 v B2) → **Qualifier 1**/**Eliminator** → **Qualifier 2** → Final — all auto-generated"),
            inline=False,
        )
        qs.add_field(
            name="🔁 Double Round Robin",
            value=("**1.** `cvt create \"League S1\" t20 event=double_rr`\n"
                   "**2.** `cvt add_team \"<team>\" @owner` for every team · owners `cvt ss`\n"
                   "**3.** `cvt start` → everyone plays everyone twice, once each way\n"
                   "**4.** after league matches: mgr `cvt generate_knockouts` for Top-4 semis, **or** `cvt gf` for a direct Top-2 Final"),
            inline=False,
        )
        qs.add_field(
            name="🔵 DSL (Dominators Super League)",
            value=("**1.** `cvt start dsl` — opens a preconfigured season (S1, S2, …)\n"
                   "**2.** `cvt add_team \"<team>\" @owner` ×12 · owners `cvt ss`\n"
                   "**3.** `cvt set_home_stadium \"<team>\" <venue>` — one fixed pitch per ground\n"
                   "**4.** `cvt start` → home & away → Top-4 Playoffs · after the Final `cvt end_season`"),
            inline=False,
        )
        qs.add_field(
            name="🟣 Conquest (open rating ladder)",
            value=("**1.** `cvt start rating` · `cvt add_team \"<team>\" @owner` · owners `cvt ss`\n"
                   "**2.** `cvt start` → ladder live (every team at 1000)\n"
                   "**3.** `cvt challenge \"<team>\"` anytime · `cvt ratings` · beat *stronger* teams to climb\n"
                   "**4.** `cvt trade` · `cvt boost` (spend credits) · mgr `cvt end_league` → Top-4 (≥10 games)"),
            inline=False,
        )
        qs.set_footer(text="At match start: paste your XI (order = batting order, (C) = captain) or ✅ Use Default XI")

        # Card 2: the complete command reference
        ref = discord.Embed(title="📖 Tournament Command Reference  ·  `cvt …`", color=discord.Color.blurple())
        ref.add_field(
            name="🛠️ Setup & squads",
            value=("`create \"<name>\" <format> [event=roundrobin/double_rr/acl/t20wc/ccodi]`\n"
                   "`add_team \"<team>\" @owner [group]` — group A/B for CCODI & T20 WC\n"
                   "`add_manager @user` · `remove_team \"<team>\"`\n"
                   "`submit_squad`/`ss` — owners paste a full squad\n"
                   "`add_player`/`ap` `[@owner] <p1>, <p2>…` — **add player(s)** (pre-start)\n"
                   "`remove_player`/`rmp` `[@owner] <p1>, <p2>…` — drop player(s) (pre-start)\n"
                   "`squad [team|@owner]` — view squad + ratings\n"
                   "`duplicates`/`dupes` — players in 2+ squads · `fill_squads <cap>` — auto-fill under-min\n"
                   "`set_default_xi`/`sdxi` · `default_xi`/`dxi [team]` — save & view a reusable XI\n"
                   "`dev_setup [no]` — [OWNER] fill random squads & auto-start (testing)"),
            inline=False,
        )
        ref.add_field(
            name="▶️ Running matches",
            value=("`start` — begin & generate the schedule\n"
                   "`fixtures`/`fx [team]` — upcoming + results · `next_match`/`nm` — your earliest pending\n"
                   "`play <id>` — owners launch their own, managers any\n"
                   "`play_next`/`pn` — [MGR] next in order · `sim <id>` — [MGR] instant-sim one\n"
                   "`simulate_all`/`simall` — [MGR] sim every remaining match\n"
                   "`cancel_match <id>` (redo) — [MGR] wipe a result to replay\n"
                   "`force_result <id> …` — [MGR] set a result manually\n"
                   "`set_schedule` — [OWNER] custom fixture order · `repair_schedule` (fix_ids) — [MGR] fix dup IDs"),
            inline=False,
        )
        ref.add_field(
            name="🏟️ Stadiums & conditions",
            value=("`stadiums [venue]` — the pool / one venue's stats\n"
                   "`stadium_add \"<name>\"` · `stadium_remove \"<name>\"` · `stadium_clear` — [MGR] edit the venue pool\n"
                   "`reroll_stadiums` — [MGR] reassign venues to upcoming matches\n"
                   "`set_stadium <id> <name|none>` — [MGR] set one match's venue\n"
                   "`set_home_pitch \"<team>\" <pitch>` · `set_home_stadium \"<team>\" <venue>` — [MGR] home ground\n"
                   "`home_stadiums`/`hs` · `homepitch` — list home grounds/pitches\n"
                   "`set_conditions <id> <pitch> [weather]` — [MGR] override a pending match"),
            inline=False,
        )
        ref.add_field(
            name="🟣 Conquest ladder (rating type)",
            value=("`challenge`/`vs \"<team>\"` — play anyone, anytime\n"
                   "`ratings`/`ladder`/`elo` — the Elo ladder\n"
                   "`trade \"<team>\" | mine | theirs` — owners + mgr confirm\n"
                   "`boost \"<player>\" bat|bowl` — spend credits · `credits`/`cr [team]` · `boosts [team]`\n"
                   "`end_league` — [MGR] close the season → Top-4 playoffs"),
            inline=False,
        )
        ref.add_field(
            name="🔥 Knockouts (Managers)",
            value=("`generate_knockouts` — Semis for Round Robin / Double RR / T20 WC\n"
                   "`generate_finals`/`gf` — direct Top-2 Final, no semis (Double RR)\n"
                   "`generate_playoffs`/`gp` — ACL Top-6 / DSL Top-4\n"
                   "`bracket`/`br` — the knockout bracket\n"
                   "*(CCODI semis auto-generate once both groups finish.)*"),
            inline=False,
        )
        ref.add_field(
            name="📊 Stats & standings",
            value=("`standings`/`st` — points table / group tables / Elo ladder · `status`/`sched` · `groups`\n"
                   "`leaderboard`/`lb <cat>` (categories in the footer) · `player_stats`/`ps <player>`\n"
                   "`match_scorecard <id>` — a completed match's image\n"
                   "`career <player>` · `seasons`/`season [n] [player]` · `venue_stats [venue]` — [DSL] all-time\n"
                   "`summary`/`recap` — the COMPLETE report (run & pin before deleting!)"),
            inline=False,
        )
        ref.add_field(
            name="🎨 Cosmetics · 📮 scorecards & logging",
            value=("`set_team_logo <standings|match> \"<team>\" <emoji|url>` · `set_team_color \"<team>\" <hex>`\n"
                   "`set_scoreboard_logo custom <emoji|url>` / `… default` · `set_theme` — [ADMIN]\n"
                   "`scorecard_channel`/`scc #ch` — auto-post every match image\n"
                   "`post_scorecards`/`psc #ch` — slow-dump ALL scorecards (archive)\n"
                   "`set_log_channel [off]` — [MGR] audit-log every change\n"
                   "`set_injury_channel #ch` · `add_injury` · `remove_injury` · `clear_injuries` (heal ALL)"),
            inline=False,
        )
        ref.add_field(
            name="⚙️ Admin (prefix-only)",
            value=("`transfer_team` · `replace_player` · `force_delete`\n"
                   "`award_win`/`walkover <id> <team>` — W only, no stats/NRR\n"
                   "`lock_stats`/`unlock_stats` — freeze player-stat recording\n"
                   "`rebuild_stats` — recompute the leaderboard from the schedule (fixes double-counted stats)\n"
                   "`admin_record_result` · `admin_restore_schedule` · `end_season`"),
            inline=False,
        )
        ref.set_footer(text="leaderboard categories — runs · wickets · sr · bat_avg · econ · bowl_avg · mvp · fours · sixes · fifties · hundreds")
        await ctx.send(embeds=[qs, ref])

    @tournament.command(name="standings", aliases=["st", "table"], help="View the Tournament Points Table & NRR.\nUsage: tournament standings")
    async def t_standings(self, ctx):
        server_id = str(ctx.guild.id)
        tourney = get_server_tournament(server_id)
        if not tourney: return await ctx.send("❌ No tournament exists.")

        # Conquest League: the standings ARE the Elo rating ladder.
        if tourney.get("tournament_type") == "rating":
            from league.rating_league import rating_board_embed
            return await ctx.send(embed=rating_board_embed(tourney))

        # CCODI: custom points-table image (assets/ccodi_table.png) with logos; the
        # text embed below is the fallback if the render fails.
        if tourney.get("tournament_type") == "ccodi":
            from league.tournament_manager import get_group_standings, generate_ccodi_points_table
            try:
                buf = generate_ccodi_points_table(tourney)
                ko = [m for m in tourney.get("schedule", []) if m.get("stage") == "knockout"]
                ko_txt = None
                if ko:
                    kl = []
                    for m in sorted(ko, key=lambda x: x.get("match_id", 0)):
                        r = m.get("result")
                        tail = f" → 🏆 **{r['winner']}**" if r else " *(pending)*"
                        kl.append(f"**{m.get('round')}**: {m['team1']} vs {m['team2']}{tail}")
                    ko_txt = "🔥 **Knockouts**\n" + "\n".join(kl)
                return await ctx.send(content=ko_txt, file=discord.File(fp=buf, filename="ccodi_points_table.png"))
            except Exception as _e:
                print(f"CCODI points table image failed: {_e}")
            e = discord.Embed(title=f"🏏 {tourney['name']} — Standings", color=discord.Color.blue())
            for grp in ("A", "B"):
                st = [(n, d) for n, d in get_group_standings(tourney, "group", grp) if n != "BYE"]
                if not st:
                    continue
                rows = ["```", f"{'':2}{'Team':<16}{'P':>3}{'W':>3}{'L':>3}{'Pts':>4}{'NRR':>7}", "─" * 40]
                for i, (nm, d) in enumerate(st, 1):
                    arrow = "▶ " if i <= 2 else "  "
                    rows.append(f"{arrow}{nm[:14]:<16}{d['P']:>3}{d['W']:>3}{d['L']:>3}{d['Pts']:>4}{d['NRR']:>+7.2f}")
                rows.append("```")
                e.add_field(name=f"Group {grp}", value="\n".join(rows), inline=False)
            ko = [m for m in tourney.get("schedule", []) if m.get("stage") == "knockout"]
            if ko:
                kl = []
                for m in sorted(ko, key=lambda x: x.get("match_id", 0)):
                    r = m.get("result")
                    tail = f" → 🏆 **{r['winner']}**" if r else " *(pending)*"
                    kl.append(f"**{m.get('round')}**: {m['team1']} vs {m['team2']}{tail}")
                e.add_field(name="🔥 Knockouts", value="\n".join(kl), inline=False)
            e.set_footer(text="▶ = reaches the knockouts (top 2 per group → KO1/KO2 → Q1/Eliminator → Q2 → Final)")
            return await ctx.send(embed=e)

        # T20 World Cup standings
        if tourney.get("tournament_type") == "t20_world_cup":
            schedule       = tourney.get("schedule", [])
            super8_matches = [m for m in schedule if m.get("stage") == "super8"]
            ko_matches     = [m for m in schedule if m.get("stage") == "knockout"]

            if not super8_matches and not ko_matches:
                # Group stage only - single image, no navigation needed
                try:
                    buf = generate_t20wc_points_table(tourney)
                    return await ctx.send(file=discord.File(fp=buf, filename="points_table.png"))
                except Exception as e:
                    print(f"Points table image failed: {e}")

            # Build available pages
            pages = []
            try:
                s16_buf = generate_t20wc_points_table(tourney)
                pages.append(("Group Stage", "points_table.png", s16_buf))
            except Exception as e:
                print(f"Super16 table failed: {e}")
            if super8_matches:
                try:
                    s8_buf = generate_t20wc_super8_table(tourney)
                    pages.append(("Super 8", "super8_table.png", s8_buf))
                except Exception as e:
                    print(f"Super8 table failed: {e}")
            if ko_matches:
                try:
                    ko_buf = generate_t20wc_knockouts_image(tourney)
                    if ko_buf:
                        pages.append(("Knockouts", "knockouts.png", ko_buf))
                except Exception as e:
                    print(f"Knockouts image failed: {e}")

            if len(pages) >= 2:
                start_idx = len(pages) - 1
                view = T20StandingsView(pages, start_idx=start_idx)
                _, fname, buf = pages[start_idx]
                buf.seek(0)
                return await ctx.send(file=discord.File(fp=buf, filename=fname), view=view)
            elif pages:
                _, fname, buf = pages[0]
                buf.seek(0)
                return await ctx.send(file=discord.File(fp=buf, filename=fname))

            # Both images failed - text embed fallback
            from league.tournament_manager import get_group_standings
            embed = discord.Embed(title=f"🌍 {tourney['name']} — Standings", color=discord.Color.gold())
            for sg in ["A", "B"]:
                st = get_group_standings(tourney, "super8", sg)
                if st:
                    rows = ["```", f"{'':2}{'Team':<20}{'P':>2}{'W':>2}{'L':>2}{'Pts':>4}{'NRR':>8}", "-"*42]
                    for i, (nm, d) in enumerate(st, 1):
                        rows.append(f"{'-> ' if i<=2 else '   '}{nm[:18]:<20}{d['P']:>2}{d['W']:>2}{d['L']:>2}{d['Pts']:>4}{d['NRR']:>+8.2f}")
                    rows.append("```")
                    embed.add_field(name=f"Super 8 - Group {sg}", value="\n".join(rows), inline=True)
            embed.set_footer(text="-> marks teams that advance to the next stage")
            return await ctx.send(embed=embed)

        # ACL: bespoke 14-team points table (Shield #1 + Top-6 playoff highlights)
        if tourney.get("tournament_type") == "acl":
            try:
                buf = generate_acl_points_table(tourney)
                return await ctx.send(file=discord.File(fp=buf, filename="acl_points_table.png"))
            except Exception as e:
                print(f"ACL points table failed, using default: {e}")

        # Everything else (Round Robin / Double RR / IPL): the shared points-table
        # image, delivered inside an embed titled with the tournament name.
        embed = build_standings_message(tourney)
        if not embed:
            return await ctx.send("No matches have been completed yet.")
        await ctx.send(embed=embed)

# ---- Startup sequence ----

if __name__ == "__main__":
    import time as _time

    keep_alive()

    TOKEN = os.environ.get("DISCORD_TOKEN")
    if not TOKEN:
        print("CRITICAL ERROR: DISCORD_TOKEN environment variable is missing from Render!")
    else:
        try:
            bot.run(TOKEN)
        except discord.HTTPException as e:
            # 429 at login == Cloudflare error 1015 (host IP temporarily rate-limited).
            # If we exit now, the supervisor (Render) restarts instantly and logs in again,
            # which KEEPS the ban alive. Instead stay alive and back off ~12 min - the keep_alive
            # web server keeps the process healthy so Render won't cycle it - letting the ban clear.
            if getattr(e, "status", None) == 429:
                print("429 / Cloudflare 1015 at login: host IP is temporarily rate-limited by Discord.")
                print("   Backing off ~12 min before exit to avoid a restart storm that sustains the ban.")
                print("   If this repeats, STOP the service and leave it down for ~1 hour.")
                _time.sleep(720)
            raise
