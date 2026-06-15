import discord
from discord import app_commands
from discord.ext import commands, tasks
import random
import csv
import difflib
import asyncio
import io
import os
import json
from PIL import Image, ImageDraw, ImageFont
import math
from keep_alive import keep_alive
from odi_simulation import execute_ball_math_odi, get_smart_ai_bowler_odi
from t20_simulation import execute_ball_math_t20, get_smart_ai_bowler_t20
from test_simulation import (
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
from tournament_manager import get_server_tournament, save_tournament, get_tournament_standings, _build_status_pages, _build_flat_pages, _build_status_embed, TournamentStatusView, generate_t20wc_points_table, generate_t20wc_super8_table, T20StandingsView, generate_t20wc_knockouts_image, generate_t20wc_match_banner
from subscription_manager import (
    load_data_from_bin, load_tournament_data_from_bin,
    save_data_to_bin, save_tournament_data_to_bin,
    check_potential_quota, consume_quota,
    update_user_tier, update_server_tier, get_auth_admins, toggle_auth_admin,
    get_all_players, add_player, add_players_bulk, update_player, delete_players, clean_duplicate_players,
    get_tier_status, is_channel_restricted, toggle_restricted_channel,
    is_ratings_channel, toggle_ratings_channel,
    get_match_log_channel, set_match_log_channel, clear_match_log_channel, DB_CACHE,
    get_match_counts, increment_match_count, set_match_count,
)

# ==========================================
# ⚙️ 1. SETUP & CONFIGURATION
# ==========================================
ADMIN_DISCORD_ID = 1087369198801526836 # Your ID
_log_env = os.environ.get("LOG_CHANNEL_ID")
LOG_CHANNEL_ID = int(_log_env) if _log_env and _log_env.isdigit() else 0

# ── Match counters (backed by MongoDB via subscription_manager) ───────────────

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
        from tournament_manager import TournamentCog
        await self.add_cog(TournamentCog(self))
        
        await self.add_cog(PrefixCog(self))
        await self.tree.sync()
        print("✅ Slash commands synchronized globally.")
        print("✅ Prefix commands loaded.")

bot = CricketBot()
active_games = {}
active_setups = {}
active_test_matches = {}   # channel_id → TestMatchObj

# ==========================================
# 🗄️ 1.5 CLOUD DATABASE & SECURITY
# ==========================================
@tasks.loop(hours=1)
async def auto_sync_db():
    """Refresh in-memory cache from MongoDB every hour (picks up manual edits)"""
    load_data_from_bin()
    load_tournament_data_from_bin()

@bot.event
async def on_ready():
    print(f"🏏 Logged in successfully as {bot.user.name}")
    load_data_from_bin()
    load_tournament_data_from_bin()
    if not auto_sync_db.is_running():
        auto_sync_db.start()
    print("✅ Memory Cache Loaded and Ready.")
# ==========================================
# 📊 2. CORE DATA STRUCTURES & FALLBACKS
# ==========================================

# Hardcoded fallback database to prevent crashes if the CSV is empty
# ── Default teams: two COMPLETELY EQUAL sides (stat-for-stat mirror images). ──
# Identical bat/bowl/role/archetype at every position, only the names differ, so
# a default-vs-default match is a true 50/50 contest. Balanced XI: 5 batters
# (incl. WK), 2 all-rounders, 4 bowlers, with a pace + spin mix for all pitches.
_EQUAL_TEMPLATE = [
    # (bat, bowl, archetype, role)
    (85, 12, "Anchor",    "Batter"),
    (86, 12, "Aggressor", "Batter"),
    (87, 18, "Anchor",    "Batter"),
    (86, 22, "Aggressor", "Batter"),
    (85, 10, "Finisher",  "Batter_WK"),
    (80, 80, "Finisher",  "All-Rounder_Pace"),
    (78, 84, "Anchor",    "All-Rounder_Spin_Off"),
    (48, 85, "Aggressor", "Bowler_Pace"),
    (38, 87, "Finisher",  "Bowler_Pace"),
    (32, 85, "Standard",  "Bowler_Pace"),
    (30, 85, "Standard",  "Bowler_Spin_Leg"),
]
_PROTAGONIST_NAMES = [
    "Adam Frost", "Ben Carter", "Cole Hayes", "Dean Walsh", "Eli Brooks",
    "Finn Doyle", "Gabe Mercer", "Hugo Blake", "Ira Nash", "Jude Pike", "Kit Rowe",
]
_RIVAL_NAMES = [
    "Axel Stone", "Boyd Lane", "Cyrus Vale", "Dane Webb", "Enzo Hart",
    "Flynn Cole", "Gray Olsen", "Hank Ross", "Ike Sterns", "Joss Kerr", "Lev Pryor",
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

# ==========================================
# 🧠 3. SIMULATION ROUTING ENGINE
# ==========================================

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
    prefix = getattr(match, "last_commentary_prefix", "")
    match.last_commentary_prefix = (
        f"🔄 **AI TACTIC:** {team['name']} uses IMPACT PLAYER! "
        f"**{in_player['name']}** IN for **{out_name}**!\n" + prefix
    )

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

    # Batting first: never sacrifice a pure bowler — you'll need them in inn2
    if is_inn1:
        swappable = [p for p in upcoming if "Bowler" not in p["role"]]
        if not swappable:
            return
    else:
        swappable = upcoming

    next_up  = swappable[0]
    worst_up = min(swappable, key=lambda x: x["bat"])

    # Guarantee (batting second only): next batter is tail — sub them out before they walk in
    if not is_inn1 and next_up["bat"] < 60 and best_sub["bat"] > next_up["bat"] + 10:
        _do_impact_swap(match, team_num, next_up["name"], best_sub)
        return

    # Powerplay crisis: 2+ wickets before over 6
    if wkts >= 2 and overs < 6 and best_sub["bat"] >= 72:
        if best_sub["bat"] > worst_up["bat"] + 12:
            _do_impact_swap(match, team_num, worst_up["name"], best_sub)
            return

    # Mid-innings wicket cluster: 3+ wickets after over 5, not in last 3 overs
    if wkts >= 3 and overs >= 5 and innings.total_balls < max_b - 18:
        if best_sub["bat"] > worst_up["bat"] + 10:
            _do_impact_swap(match, team_num, worst_up["name"], best_sub)
            return

    # Chase mode (batting second): RRR >= 9, bring in firepower
    if not is_inn1:
        balls_left = max_b - innings.total_balls
        if balls_left > 0:
            target = getattr(match, "target", match.innings1.total_runs + 1)
            rrr = (target - innings.total_runs) / balls_left * 6
            if rrr >= 9 and best_sub["bat"] >= 75 and best_sub["bat"] > worst_up["bat"] + 8:
                _do_impact_swap(match, team_num, worst_up["name"], best_sub)
                return

    # Late guarantee: last 4 overs and sub still unused — don't waste the slot
    if innings.total_balls >= max_b - 24 and best_sub["bat"] > worst_up["bat"] + 8:
        _do_impact_swap(match, team_num, worst_up["name"], best_sub)

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
        _do_impact_swap(match, team_num, worst["name"], best_sub)
        return

    # 2nd innings, opponent cruising (low RRR) — use from last 6 overs
    if match.current_innings_num == 2 and balls >= max_b - 36:
        balls_left = max_b - balls
        if balls_left > 0:
            target = getattr(match, "target", match.innings1.total_runs + 1)
            rrr = (target - innings.total_runs) / balls_left * 6
            if rrr < 7:
                _do_impact_swap(match, team_num, worst["name"], best_sub)
                return

    # Absolute guarantee: last 2 overs, don't leave sub unused
    if balls >= max_b - 12:
        _do_impact_swap(match, team_num, worst["name"], best_sub)

def try_ai_impact_player(match: CricketMatch, innings: InningsState):
    if not getattr(match, "impact_player", False): return
    if not match.is_ai_game: return

    for team_num in (1, 2):
        if getattr(match, f"t{team_num}_impact_used", False): continue
        team = match.team1 if team_num == 1 else match.team2
        subs = getattr(match, f"t{team_num}_subs", [])
        if not subs: continue

        if innings.batting_team["name"] == team["name"]:
            _ai_batting_impact(match, innings, team_num, subs)
        else:
            _ai_bowling_impact(match, innings, team_num, subs)

def get_smart_ai_bowler(innings, pitch, weather="Clear", format_overs=20):
    if format_overs == 50:
        return get_smart_ai_bowler_odi(innings, pitch, weather, format_overs)
    return get_smart_ai_bowler_t20(innings, pitch, weather, format_overs)

def execute_ball_math(match: CricketMatch):
    if match.format_overs == 50:
        return execute_ball_math_odi(match)
    return execute_ball_math_t20(match)

def _run_full_match_sync(match: CricketMatch):
    """Simulate a complete T20/ODI match synchronously (no Discord messages)."""
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
            execute_ball_math(match)
            if innings.total_balls % 6 == 0 and innings.total_balls > 0:
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

# ==========================================
# 🖼️ 4. EMBED SCOREBOARDS & PIL GRAPHICS
# ==========================================

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
            impact += bat.runs_scored + (bat.runs_scored * (sr / 120))
            
        if p_name in match.innings1.bowling_stats:
            bowl = match.innings1.bowling_stats[p_name]
            if bowl.balls_bowled > 0:
                eco = (bowl.runs_conceded / bowl.balls_bowled) * 6
                eco_pts = max(-30.0, (10 - eco) * (bowl.balls_bowled / 6) * 3)
                impact += (bowl.wickets_taken * 40) + eco_pts

        # Analyze innings 2 impact
        if match.current_innings_num == 2 and match.innings2:
            if p_name in match.innings2.batting_stats:
                bat = match.innings2.batting_stats[p_name]
                sr = (bat.runs_scored / bat.balls_faced * 100) if bat.balls_faced > 0 else 0
                impact += bat.runs_scored + (bat.runs_scored * (sr / 120))

            if p_name in match.innings2.bowling_stats:
                bowl = match.innings2.bowling_stats[p_name]
                if bowl.balls_bowled > 0:
                    eco = (bowl.runs_conceded / bowl.balls_bowled) * 6
                    eco_pts = max(-30.0, (10 - eco) * (bowl.balls_bowled / 6) * 3)
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
    
    if match.current_innings_num == 2:
        target = getattr(match, "target", match.innings1.total_runs + 1)
        target_needed = target - innings.total_runs
        balls_left = match.max_balls - innings.total_balls
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

    # ==========================================
    # 1. CORE LAYOUT & BARS
    # ==========================================
    
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
    
    # ==========================================
    # 2. GRID SYSTEM
    # ==========================================
    
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
        
    # ==========================================
    # 3. FLOATING UI ICONS
    # ==========================================
    
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

    # ==========================================
    # 4. DATA POPULATION
    # ==========================================
    
    # Top White Header (Teams & Logo)
    t1_name = match.innings1.batting_team['name'][:18].upper()
    d.text((300 - get_tw(t1_name, font_large)//2, 30), t1_name, fill=c_navy, font=font_large)

    if match.current_innings_num == 2 and match.innings2:
        t2_name = match.innings2.batting_team['name'][:18].upper()
    else:
        t2_name = match.innings1.bowling_team['name'][:18].upper()
    d.text((900 - get_tw(t2_name, font_large)//2, 30), t2_name, fill=c_navy, font=font_large)

    # Match number — top-right corner, small/unobtrusive
    _base_fmt = "odi" if _base_overs == 50 else "t20"
    _ctr_text = _format_match_no_label(_base_fmt)
    _ctr_w = get_tw(_ctr_text, font_micro)
    d.text((1195 - _ctr_w, 8), _ctr_text, fill=c_text_grey, font=font_micro)

    # Center Custom Logo (or Placeholder)
    try:
        logo_path = "logo.png" if os.path.exists("logo.png") else "logo.jpg"
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
            
            if potm_name == b.profile['name']:
                nw = get_tw(name, font_bold)
            
                d.text((offset_x + 75 + nw + 8, y - 4), "★", fill="#FFD700", font=font_title)
            
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
            
            if potm_name == bowl.profile['name']:
                nw = get_tw(name, font_bold)
                d.text((offset_x + 75 + nw + 8, y - 4), "★", fill="#FFD700", font=font_title)
            
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
        max_w = 2 if getattr(match, 'is_super_over', False) else 10
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
        max_w = 2 if getattr(match, 'is_super_over', False) else 10
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
    if tourney and _mid:
        _m_sched = next((x for x in tourney.get("schedule", []) if x["match_id"] == _mid), None)
        if _m_sched:
            _round_label = _match_round_label(_m_sched)

    return {
        "theme": theme,
        "match_id": str(getattr(match, "tournament_match_id", "?")),
        "round_label": _round_label,
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
    """Minimal per-match data for scorecard regeneration.
    Only stores what can't be derived from the existing tournament/result JSON.
    Uses short keys + arrays to keep the stored JSON as small as possible.
    Per-match size: ~580 bytes → 45 matches ≈ 26 KB added to the bin.
    """
    potm = get_player_of_the_match(match)
    inn1 = match.innings1
    inn2 = match.innings2 if match.current_innings_num == 2 else None

    def _bat(inn):
        if not inn: return []
        active = [b for b in inn.batting_stats.values() if b.balls_faced > 0 or b.dismissal != "not out"]
        top = sorted(active, key=lambda x: x.runs_scored, reverse=True)[:4]
        return [[b.profile["name"], b.runs_scored, b.balls_faced, b.dismissal == "not out"] for b in top]

    def _bowl(inn):
        if not inn: return []
        active = [b for b in inn.bowling_stats.values() if b.balls_bowled > 0]
        top = sorted(active, key=lambda x: (x.wickets_taken, -x.runs_conceded), reverse=True)[:4]
        return [[b.profile["name"], b.wickets_taken, b.runs_conceded, f"{b.balls_bowled//6}.{b.balls_bowled%6}"] for b in top]

    # bf=1 means m["team1"] batted first, bf=2 means m["team2"] batted first
    team1_bats_first = (match.team1["name"] == inn1.batting_team["name"])
    bf = 1 if team1_bats_first else 2
    t1_impact_attr = "t1_impact_sub_name" if team1_bats_first else "t2_impact_sub_name"
    t2_impact_attr = "t2_impact_sub_name" if team1_bats_first else "t1_impact_sub_name"

    target = getattr(match, "target", inn1.total_runs + 1)
    if inn2:
        max_w = 2 if getattr(match, 'is_super_over', False) else 10
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
        return [{"name": a[0], "runs": a[1], "balls": a[2], "not_out": a[3]} for a in (arrays or [])]
    def _bowl(arrays):
        return [{"name": a[0], "wickets": a[1], "runs": a[2], "overs": a[3]} for a in (arrays or [])]

    # bf=1 → team1 batted first; bf=2 → team2 batted first
    # result stores t1_*/t2_* keyed to m["team1"]/m["team2"], not batting order
    bf = p.get("bf", 1)
    if bf == 2:
        # team2 batted first → swap for display
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
        "tourn_name":      tourney["name"].upper(),
        "format_overs":    r.get("format_overs", 20),
        "result_str":      p.get("rs", ""),
        "potm":            p.get("p"),
        "t1": {
            "name":       top_name.upper(),
            "color":      top_team.get("color", "#6B7280"),
            "logo_emoji": top_team.get("logo_match") or top_team.get("logo_standings"),
            "runs":       top_r,
            "wickets":    top_w,
            "balls":      top_b,
            "yet_to_bat": False,
            "batters":    _bat(p.get("b1")),
            "bowlers":    _bowl(p.get("w2")),
            "impact_sub": p.get("i1"),
        },
        "t2": {
            "name":       bot_name.upper(),
            "color":      bot_team.get("color", "#6B7280"),
            "logo_emoji": bot_team.get("logo_match") or bot_team.get("logo_standings"),
            "runs":       bot_r,
            "wickets":    bot_w,
            "balls":      bot_b,
            "yet_to_bat": False,
            "batters":    _bat(p.get("b2")),
            "bowlers":    _bowl(p.get("w1")),
            "impact_sub": p.get("i2"),
        },
    }


def _fetch_emoji_img(emoji_str: str, size: int = 72):
    """Download a Discord custom emoji or Unicode emoji as PIL RGBA Image. Returns None on failure."""
    if not emoji_str:
        return None
    import re as _re, requests as _req, io as _io2
    emoji_str = emoji_str.strip()
    try:
        # Base64 data URI (stored from attachment upload — never expires)
        if emoji_str.startswith("data:image/"):
            import base64 as _b64
            _data = emoji_str.split(",", 1)[1]
            return Image.open(_io2.BytesIO(_b64.b64decode(_data))).convert("RGBA").resize((size, size), Image.LANCZOS)
        # Custom Discord emoji  <:name:id>  or  <a:name:id>
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

        # Plain text / :shortcode: with no ID — can't resolve without guild context
        if all(ord(c) < 128 for c in emoji_str):
            return None
        # Unicode emoji → Twemoji CDN (skip variation selectors and ZWJ)
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
        print(f"⚠️ Emoji fetch failed ({emoji_str}): {_e}")
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

    # ── Background ──────────────────────────────────────────────
    try:
        bg = Image.open("t20_scoreboard.png").convert("RGBA")
    except FileNotFoundError:
        bg = Image.new("RGBA", (975, 634), (15, 15, 40, 255))
    W, H = bg.size
    img = bg.copy()
    d   = ImageDraw.Draw(img)

    # ── Fonts ────────────────────────────────────────────────────
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
    RES_Y2 = int(H * 0.901)               # 940 — covers template transition pixels at y=939-940

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

    # ── Helpers ───────────────────────────────────────────────────
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

    # ── Match number — above template's green "MATCH SUMMARY" (y=64-98) ──
    ctx_line = f"MATCH {match_id}"
    if round_label:
        ctx_line += f"  •  {round_label.upper()}"
    d.text(((W - _tw(ctx_line, fMatch)) // 2, int(H * 0.022)),
           ctx_line, fill=(200, 200, 200, 255), font=fMatch)

    # ── Team band ──────────────────────────────────────────────────
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

    # ── Stats table (no header row — matches ICC reference style) ──
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

    # ── Render both teams ─────────────────────────────────────────
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

    # ── Flatten RGBA → RGB ────────────────────────────────────────
    final = Image.new("RGB", img.size, (255, 255, 255))
    final.paste(img, mask=img.split()[3])
    buf = io.BytesIO()
    final.save(buf, format="PNG")
    buf.seek(0)
    return buf


def generate_scorecard_from_data(data: dict) -> io.BytesIO:
    """Generate a scorecard image from pre-serialized match display data."""
    if data.get("tournament_type") == "t20_world_cup" or data.get("theme") == "T20 World Cup":
        try:
            return generate_t20wc_scorecard(data)
        except Exception as _e:
            print(f"⚠️ T20 WC scorecard error: {_e}. Falling back to default.")

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
            # ── Layout ───────────────────────────────────────────
            _W, _H       = 1200, 720
            _H_HDR       = 130
            _H_BAR       = 65
            _H_STATS     = 200
            _H_BOT       = 60
            _SCORE_PANEL = 260

            # ── Colors ───────────────────────────────────────────
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

            # ── Canvas ───────────────────────────────────────────
            img = Image.new("RGB", (_W, _H), "#FFFFFF")
            d   = ImageDraw.Draw(img)

            # ── Fonts ────────────────────────────────────────────
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

            # ── Helpers ──────────────────────────────────────────
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

            # ── 1. Gradient header ───────────────────────────────
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

            # ── 2. Team section ──────────────────────────────────
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

            # ── 3. Bottom bar ────────────────────────────────────
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
            print(f"⚠️ Crimson Cricket scoreboard error: {e}. Falling back to default.")
            pass

    # Dark blurred stadium/background proxy
    img = Image.new("RGB", (1200, 900), color="#101820") 
    d = ImageDraw.Draw(img)

    try:
        font_huge = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 46)
        font_large = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 36)
        font_title = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 28)
        font_bold = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 22)
        font_small = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 18)
    except:
        font_huge = font_large = font_title = font_bold = font_small = ImageFont.load_default()

    def get_tw(text, font):
        if hasattr(font, 'getbbox'): return font.getbbox(text)[2]
        return len(text) * 12
        
    c_panel_bg = "#F8F9FA"
    c_header = "#0B2B5C"
    c_team_bar = "#1DA1F2"
    c_grid_line = "#E2E8F0"
    c_text_navy = "#0F172A"
    c_text_grey = "#64748B"
    c_white = "#FFFFFF"
    
    # Main Panel Rounding Setup (100px padding left/right)
    d.rounded_rectangle([(100, 80), (1100, 820)], radius=20, fill=c_panel_bg)

    # 1. Main Header Block (80 to 180px)
    d.rounded_rectangle([(100, 80), (1100, 200)], radius=20, fill=c_header)
    d.rectangle([(100, 120), (1100, 180)], fill=c_header) # square bottom for seamless connection
    
    d.text((140, 100), tourn_name[:30], fill=c_white, font=font_huge)
    d.text((140, 145), f"MATCH {match_id} - {format_overs} OVERS", fill="#A5F3FC", font=font_small)
    d.text((1060 - get_tw("SERVER LOGO", font_bold), 115), "SERVER LOGO", fill=c_white, font=font_bold)

    def draw_team_section(td, y_start):
        d.rectangle([(100, y_start), (1100, y_start + 60)], fill=c_team_bar)
        d.rectangle([(140, y_start + 18), (170, y_start + 42)], fill=c_header)
        d.text((185, y_start + 12), td["name"], fill=c_white, font=font_large)
        if td["yet_to_bat"]:
            score_txt, overs_txt = "YET TO BAT", ""
        else:
            score_txt = f"{td['runs']}-{td['wickets']}"
            b = td.get("balls", 0)
            _ov = f"{b // 6}" if b % 6 == 0 else f"{b // 6}.{b % 6}"
            overs_txt = f"OVERS {_ov}"
        sw = get_tw(score_txt, font_huge)
        d.text((1060 - sw, y_start + 5), score_txt, fill=c_white, font=font_huge)
        if overs_txt:
            d.text((1060 - sw - get_tw(overs_txt, font_bold) - 20, y_start + 18), overs_txt, fill=c_white, font=font_bold)
        g_y = y_start + 60
        if td["yet_to_bat"]: return
        d.line([(600, g_y), (600, g_y + 210)], fill=c_grid_line, width=2)
        d.text((140, g_y + 10), "BATTER", fill=c_text_grey, font=font_small)
        d.text((490 - get_tw("R", font_small)//2, g_y + 10), "R", fill=c_text_grey, font=font_small)
        d.text((550 - get_tw("B", font_small)//2, g_y + 10), "B", fill=c_text_grey, font=font_small)
        for idx, b in enumerate(td["batters"][:4]):
            r_y = g_y + 40 + idx * 40
            d.line([(100, r_y), (600, r_y)], fill=c_grid_line, width=1)
            runs = f"{b['runs']}{'*' if b.get('not_out') else ''}"
            d.text((140, r_y + 8), b["name"][:16].upper(), fill=c_text_navy, font=font_bold)
            d.text((490 - get_tw(runs, font_bold)//2, r_y + 8), runs, fill=c_text_navy, font=font_bold)
            d.text((550 - get_tw(str(b["balls"]), font_small)//2, r_y + 8), str(b["balls"]), fill=c_text_grey, font=font_bold)
        d.text((640, g_y + 10), "BOWLER", fill=c_text_grey, font=font_small)
        d.text((950 - get_tw("W-R", font_small)//2, g_y + 10), "W-R", fill=c_text_grey, font=font_small)
        d.text((1050 - get_tw("O", font_small)//2, g_y + 10), "O", fill=c_text_grey, font=font_small)
        for idx, bw in enumerate(td["bowlers"][:4]):
            r_y = g_y + 40 + idx * 40
            d.line([(600, r_y), (1100, r_y)], fill=c_grid_line, width=1)
            wr = f"{bw['wickets']}-{bw['runs']}"
            d.text((640, r_y + 8), bw["name"][:16].upper(), fill=c_text_navy, font=font_bold)
            d.text((950 - get_tw(wr, font_bold)//2, r_y + 8), wr, fill=c_text_navy, font=font_bold)
            d.text((1050 - get_tw(bw["overs"], font_small)//2, r_y + 8), bw["overs"], fill=c_text_grey, font=font_bold)

    draw_team_section(t1_data, 180)
    draw_team_section(t2_data, 450)

    d.rounded_rectangle([(100, 720), (1100, 820)], radius=20, fill=c_header)
    d.rectangle([(100, 720), (1100, 780)], fill=c_header)
    footer = result_str
    if potm: footer += f"  •  POTM: {potm.upper()}"
    d.text((600 - get_tw(footer, font_title)//2, 755), footer, fill=c_white, font=font_title)

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf

def generate_tournament_score_image(match: CricketMatch) -> io.BytesIO:
    return generate_scorecard_from_data(extract_scoreboard_data(match))

# ==========================================
# 🔄 5. MATCH PROGRESSION & LOOPS
# ==========================================

async def advance_match_loop(interaction, match: CricketMatch):
    innings = match.current_innings
    
    max_w = 2 if getattr(match, 'is_super_over', False) else 10
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
        innings = match.current_innings
        max_w = 2 if getattr(match, 'is_super_over', False) else 10
        if innings.wickets >= max_w or innings.total_balls >= match.max_balls or (
                match.current_innings_num == 2 and
                innings.total_runs >= getattr(match, "target", match.innings1.total_runs + 1)):
            orig_sim_only = getattr(match, 'sim_only', False)
            match.sim_only = False   # always return to hub after this innings
            await handle_innings_end(interaction, match)
            match.sim_only = orig_sim_only
            break

        if innings.total_balls % 6 == 0 and not innings.over_log:
            try_ai_impact_player(match, innings)
            new_bowler = get_smart_ai_bowler(innings, match.pitch, match.weather, match.format_overs)
            if not new_bowler:
                await channel.send("🚨 **CRITICAL ERROR:** Could not find a valid bowler.")
                if channel.id in active_games:
                    del active_games[channel.id]
                return
            innings.current_bowler = new_bowler

        execute_ball_math(match)

        if innings.total_balls % 6 == 0 and innings.total_balls > 0:
            if getattr(match, 'verbose', False):
                await channel.send(embed=render_embed_scoreboard(match))
                await asyncio.sleep(0.5)
            innings.over_log.clear()
            innings.bouncers_in_over = 0; innings.cutters_in_over = 0
            innings.mystery_bowled_this_over = False


async def loop_entire_match_simulation(interaction, match: CricketMatch):
    channel = interaction.channel if hasattr(interaction, 'channel') else interaction
    
    while True:
        innings = match.current_innings
        max_w = 2 if getattr(match, 'is_super_over', False) else 10
        if innings.wickets >= max_w or innings.total_balls >= match.max_balls or (match.current_innings_num == 2 and innings.total_runs >= getattr(match, "target", match.innings1.total_runs + 1)):
            await handle_innings_end(interaction, match)
            break
            
        # Only select a new bowler at the TRUE start of a new over (over_log empty = no
        # deliveries yet this over, including wides). This prevents wides from triggering
        # a mid-over bowler swap when total_balls % 6 == 0.
        if innings.total_balls % 6 == 0 and not innings.over_log:
            try_ai_impact_player(match, innings)
            new_bowler = get_smart_ai_bowler(innings, match.pitch, match.weather, match.format_overs)
            if not new_bowler:
                await channel.send("🚨 **CRITICAL ERROR:** Could not find a valid bowler to continue simulation. Match has been stopped.")
                if channel.id in active_games:
                    del active_games[channel.id]
                return
            innings.current_bowler = new_bowler

        execute_ball_math(match)

        # After each completed over (6 legal balls), reset over-specific state so the
        # next iteration's bowler-selection guard (not over_log) triggers correctly.
        if innings.total_balls % 6 == 0 and innings.total_balls > 0:
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

async def trigger_super_over(channel, match: CricketMatch):
    so_match = CricketMatch(match.p1, match.p2, match.p1_id, match.p2_id, match.team1, match.team2, format_overs=1, pitch=match.pitch, weather=match.weather)
    so_match.is_super_over = True
    so_match.original_match_object = match
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
    active_games[channel.id] = so_match
    
    await channel.send("🚨 **SCORES ARE TIED!** 🚨\nGet ready for the **SUPER OVER!**\n*The team that batted second will bat first. Max 2 wickets.*")
    if so_match.sim_only: await loop_entire_match_simulation(channel, so_match)
    else: await prompt_bowler_then_hub(channel, so_match)

async def handle_innings_end(interaction_context, match: CricketMatch):
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
        
        # 🌧️ DLS INTERRUPT SYSTEM
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
        
        # Pass channel directly — no more DummyInteraction needed
        if getattr(match, 'sim_only', False):
            await channel.send("*Simulating 2nd Innings... ⚙️*")
            await loop_entire_match_simulation(channel, match)
        else:
            await prompt_bowler_then_hub(channel, match)
        
    else:
        inn1 = match.innings1
        inn2 = match.innings2
        target = getattr(match, "target", inn1.total_runs + 1)
        is_tied = (inn2.total_runs == target - 1)
        
        if is_tied and not getattr(match, "tie_accepted", False) and not getattr(match, 'is_super_over', False):
            if match.format_overs != 50:
                await trigger_super_over(channel, match)
                return

            else:
                await channel.send("🏆 **The Match has TIED!** Do you want to play a Super Over?", view=ODISuperOverPrompt(match))
                return
        if is_tied and getattr(match, 'is_super_over', False):
            await channel.send("🤯 **THE SUPER OVER IS TIED!** We are going to ANOTHER Super Over!")
            await trigger_super_over(channel, match)
            return

        match_to_finalize = match
        if getattr(match, 'is_super_over', False) and hasattr(match, 'original_match_object'):
            original_match = match.original_match_object
            so_winner_name = match.innings2.batting_team['name'] if match.innings2.total_runs > match.innings1.total_runs else match.innings1.batting_team['name']
            original_match.tiebreak_winner_name = so_winner_name
            match_to_finalize = original_match

        # Increment counter BEFORE generating image so the scorecard shows the correct match number.
        # Skip super overs — they're continuations, not standalone matches.
        if not getattr(match_to_finalize, 'is_super_over', False):
            _base = getattr(match_to_finalize, 'original_format_overs', match_to_finalize.format_overs)
            _increment_match_count("odi" if _base == 50 else "t20")

        if getattr(match_to_finalize, "tournament_server_id", None):
            img_buf = generate_tournament_score_image(match_to_finalize)
        else:
            img_buf = generate_final_score_image(match_to_finalize)

        file = discord.File(fp=img_buf, filename="final_scoreboard.png")
        embed_full = render_full_scorecard_embed(match_to_finalize, 2)

        await channel.send(
            "🏆 **Match over! Here is the final detailed scorecard and broadcast graphic:**",
            embed=embed_full,
            file=file
        )

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
                    print(f"⚠️ Match log send failed: {_log_err}")

        if getattr(match_to_finalize, "tournament_server_id", None):
            try:
                match_to_finalize._scorecard_players = extract_scorecard_players(match_to_finalize)
            except Exception as _e:
                print(f"⚠️ Could not extract scorecard players: {_e}")
                match_to_finalize._scorecard_players = None

        if channel.id in active_games:
            del active_games[channel.id]

        if getattr(match_to_finalize, "tournament_server_id", None):
            bot.dispatch("tournament_match_complete", match_to_finalize)

# ==========================================
# 🏏 6. OVER HUB & INTERACTIVE MENUS
# ==========================================

async def prompt_next_batter(interaction, match: CricketMatch):
    channel = interaction.channel if hasattr(interaction, 'channel') else interaction
    uid = match.get_striker_user_id()
    innings = match.current_innings
    available = innings.batting_team["players"][innings.next_batter_idx:]
    
    if not available:
        await run_interactive_delivery_sequence(interaction, match)
        return
        
    options = []
    for p in available:
        st = innings.batting_stats[p["name"]]
        if st.dismissal == "not out":
            role_short = p["role"].split("_")[0]
            options.append(discord.SelectOption(label=p["name"], description=role_short, value=p["name"]))

    view = discord.ui.View(timeout=300)
    select = discord.ui.Select(placeholder="Select Next Batter...", options=options[:25])
    
    async def interaction_check(inter: discord.Interaction) -> bool:
        if inter.channel.id not in active_games or active_games[inter.channel.id] != match:
            await inter.response.send_message("❌ Match ended.", ephemeral=True)
            return False
        if inter.user.id != uid and inter.user.id != getattr(match, "manager_id", None):
            await inter.response.send_message("Not your turn.", ephemeral=True)
            return False
        return True
    view.interaction_check = interaction_check

    async def cb(inter: discord.Interaction):
        sel_name = select.values[0]
        idx = next(i for i, p in enumerate(innings.batting_team["players"]) if p["name"] == sel_name)
        
        # Reorder the lineup naturally so scoreboard works perfectly!
        innings.batting_team["players"][innings.next_batter_idx], innings.batting_team["players"][idx] = innings.batting_team["players"][idx], innings.batting_team["players"][innings.next_batter_idx]
        innings.current_striker_idx = innings.next_batter_idx
        innings.next_batter_idx += 1
        
        await inter.response.defer()
        await inter.message.edit(view=None)
        await run_interactive_delivery_sequence(inter, match)
        
    select.callback = cb
    view.add_item(select)
    
    msg = f"🏏 <@{uid}>, select the next batter to walk in:"
    if getattr(match, "impact_player", False):
        msg += "\n💡 *(Need to sub someone in? Run `/impactplayer` first!)*"
    await channel.send(msg, view=view)

async def prompt_new_over_bowler(interaction, match: CricketMatch):
    innings = match.current_innings
    bowler_uid = match.get_bowler_user_id()
    channel = interaction.channel if hasattr(interaction, 'channel') else interaction
    
    if match.is_ai_game and bowler_uid == match.p2_id:
        try_ai_impact_player(match, innings)
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
    bowler_quota = max(1, (match.format_overs + 4) // 5)
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

    # AI game where AI is bowling: AI picks, reset over state, show hub directly
    if match.is_ai_game and bowler_uid == match.p2_id:
        try_ai_impact_player(match, innings)
        new_bowler = get_smart_ai_bowler(innings, match.pitch, match.weather, match.format_overs)
        if not new_bowler:
            await channel.send("🚨 **CRITICAL ERROR:** Could not find a valid bowler to proceed. The match cannot continue. Please use `/endmatch`.")
            return
        innings.current_bowler = new_bowler
        innings.over_log.clear()
        innings.bouncers_in_over = 0; innings.cutters_in_over = 0
        innings.mystery_bowled_this_over = False
        await prompt_over_pacing_hub(interaction, match)
        return

    # Human bowling: show bowler select, then show hub after selection
    actual_bowlers = [p for p in innings.bowling_team["players"]
                      if not getattr(innings.bowling_stats.get(p["name"]), "is_subbed_out", False)
                      and ("Bowler" in p["role"] or "All-Rounder" in p["role"])]

    bowler_quota = max(1, (match.format_overs + 4) // 5)
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

    view = discord.ui.View(timeout=300)
    select = discord.ui.Select(
        placeholder=f"🎳 Pick bowler for Over {innings.total_balls // 6 + 1}...",
        options=options[:25]
    )

    async def b_callback(inter: discord.Interaction):
        b_name = select.values[0]
        b_stats = innings.bowling_stats[b_name]
        if b_stats.balls_bowled // 6 >= bowler_quota or (innings.current_bowler and innings.current_bowler["name"] == b_name):
            await inter.response.send_message("❌ Illegal selection (quota full or same bowler back-to-back).", ephemeral=True)
            return
        match._pending_bowler = next(p for p in innings.bowling_team["players"] if p["name"] == b_name)
        await inter.response.defer()
        await inter.message.edit(view=None)
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

    _t20wc_hub = False
    _tid = getattr(match, "tournament_server_id", None)
    if _tid:
        _tv = get_server_tournament(_tid)
        if _tv and _tv.get("tournament_type") == "t20_world_cup":
            _t20wc_hub = True

    if _t20wc_hub:
        embed.set_thumbnail(url="attachment://t20_logo.png")
        await channel.send(msg, embed=embed, view=view, file=discord.File("t20_logo.png"))
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
        self.match._pending_bowler = None
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
        self.match._pending_bowler = None
        innings = self.match.current_innings
        innings.over_log.clear()
        innings.bouncers_in_over = 0; innings.cutters_in_over = 0
        innings.mystery_bowled_this_over = False
        await loop_current_innings_simulation(interaction, self.match)
        
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
            else:
                innings.next_batter_idx -= 1

            innings.current_striker_idx = self.match.prev_striker_idx
            innings.batting_stats[innings.batting_team["players"][innings.current_striker_idx]["name"]].dismissal = "not out"
            innings.bowling_stats[innings.current_bowler["name"]].wickets_taken -= 1
            if innings.over_log and innings.over_log[-1] == "<a:wickett:1510369641959264429>":
                innings.over_log[-1] = "<a:0run:1510601371483897896>"
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

async def run_interactive_delivery_sequence(interaction, match: CricketMatch):
    innings = match.current_innings
    
    max_w = 2 if getattr(match, 'is_super_over', False) else 10
    if innings.wickets >= max_w or innings.total_balls >= match.max_balls or (match.current_innings_num == 2 and innings.total_runs >= getattr(match, "target", match.innings1.total_runs + 1)):
        await handle_innings_end(interaction, match)
        return
        
    if getattr(match, "over_completed", False):
        match.over_completed = False
        await prompt_bowler_then_hub(interaction, match)
        return
        
    channel = interaction.channel if hasattr(interaction, 'channel') else interaction
    
    if match.is_ai_game and match.get_bowler_user_id() == match.p2_id:
        if getattr(innings, "total_balls", 0) % 6 == 0 or (innings.over_log and innings.over_log[-1] == "<a:wickett:1510369641959264429>"):
            try_ai_impact_player(match, innings)
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

        if "Spin" in role:
            spin_type = "off" if "Off" in role else "leg"
            title = "Off-Spin" if spin_type == "off" else "Leg-Spin"
            await channel.send(f"🔮 <@{match.get_bowler_user_id()}> (**{innings.current_bowler['name']}**), select your {title} Variation:{free_hit_notice}", view=SpinBowlingView(match, spin_type))
        else:
            await channel.send(f"🔮 <@{match.get_bowler_user_id()}> (**{innings.current_bowler['name']}**), select your Pace Variation:{free_hit_notice}", view=PaceBowlingView(match))

async def prompt_batter_shot(channel, match: CricketMatch, prev=None):
    if match.is_ai_game and match.get_striker_user_id() == match.p2_id:
        if getattr(match.current_innings, "total_balls", 0) % 6 == 0 or (match.current_innings.over_log and match.current_innings.over_log[-1] == "<a:wickett:1510369641959264429>"):
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
                    else:
                        innings.next_batter_idx -= 1
                    innings.current_striker_idx = match.prev_striker_idx
                    innings.batting_stats[innings.batting_team["players"][innings.current_striker_idx]["name"]].dismissal = "not out"
                    innings.bowling_stats[innings.current_bowler["name"]].wickets_taken -= 1
                    if innings.over_log and innings.over_log[-1] == "<a:wickett:1510369641959264429>":
                        innings.over_log[-1] = "<a:0run:1510601371483897896>"
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
        
        await run_interactive_delivery_sequence(d, match)
    else:
        # Failsafe clamp to prevent IndexError edge cases
        if match.current_innings.current_striker_idx >= len(match.current_innings.batting_team["players"]):
            match.current_innings.current_striker_idx = len(match.current_innings.batting_team["players"]) - 1
            
        sn = match.current_innings.batting_team["players"][match.current_innings.current_striker_idx]["name"]
        free_hit_notice = "\n🛡️ **FREE HIT!** You cannot be dismissed (except run out)!" if getattr(match, "free_hit", False) else ""
        await channel.send(f"⚔️ <@{match.get_striker_user_id()}> (**{sn}**)\n🚨 The bowler bowled a **{match.current_delivery_selection}**!{free_hit_notice}\nSelect your shot:", view=BattingView(match))


# ==========================================
# 🛠️ 7. NEW STEP-BY-STEP MATCH SETUP FLOW
# ==========================================

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
        self.t2_name = "Team 2"
        self.t2_roster = []
        self.t2_squad = []
        self.pitch = "Flat"
        self.weather = "Clear"
        self.tournament_name = "TOURNAMENT"
        self.home_team_id = p1_id
        self.sim_only = False


def parse_pasted_roster(raw_text, db_players):
    # Create a lookup map where keys are lowercase for easy matching
    db_map = {p["name"].lower(): p for p in db_players}
    db_names_list = list(db_map.keys())
    
    found_players = []
    missing_names = []
    seen_names = set() # 🚨 NEW: Tracks who is already in the XI
    
    lines = [l.strip() for l in raw_text.split("\n") if l.strip()]
    for line in lines[:16]:
        query = line.lower()
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
                missing_names.append(line)
                
        # 3. Duplicate check before adding to the team
        if matched_player:
            if matched_player["name"] not in seen_names:
                found_players.append(matched_player)
                seen_names.add(matched_player["name"])
            else:
                # If they try to add a duplicate, flag it as an error!
                missing_names.append(f"{line} (Duplicate Entry)")
                
    return found_players, missing_names
    
def format_xi_display(players):
    lines = []
    for i, p in enumerate(players, 1):
        role_short = p["role"].replace("All-Rounder", "AR").replace("Bowler", "BWL").replace("Batter", "BAT").replace("_", " ")
        lines.append(f"`{i:>2}.` **{p['name']}** — {role_short}")
    return "\n".join(lines)


# --- Step 1: Format & Impact Player ---
# --- Step 1: Format & Impact Player ---

class FormatSelectView(discord.ui.View):
    def __init__(self, state: MatchSetupState, channel):
        super().__init__(timeout=120)
        self.state = state
        self.channel = channel

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

            # Test format is restricted to admins and bot owner only
            if val == "90":
                is_owner = interaction.user.id == ADMIN_DISCORD_ID
                is_admin_user = is_owner or str(interaction.user.id) in get_auth_admins()
                if not is_admin_user:
                    return await interaction.followup.send(
                        "❌ **Test format** is currently restricted to **admins and bot owner** only.",
                        ephemeral=True
                    )

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
                    await prompt_tournament_xi(self.channel, self.state, 1)
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
        allowed, reason = await asyncio.to_thread(consume_quota, str(interaction.user.id), str(interaction.guild.id) if interaction.guild else None, "custom", str(ADMIN_DISCORD_ID))
        if not allowed:
            return await interaction.followup.send(reason, ephemeral=True)

        self.state.format_overs = val
        # 🚨 FIX: Atomic edit prevents the crash
    
        await interaction.edit_original_response(content=f"✅ Format set: **Custom ({val} overs)**", view=None)
        if getattr(self.state, "tournament_server_id", None):
            await prompt_tournament_xi(self.channel, self.state, 1)
        else:
            await ask_team1_name(self.channel, self.state)

class ImpactPlayerView(discord.ui.View):
    def __init__(self, state, channel):
        super().__init__()
        self.state = state
        self.channel = channel
    @discord.ui.button(label="Yes (Impact Player)", style=discord.ButtonStyle.success)
    async def btn_yes(self, interaction, button):
        if interaction.user.id != self.state.p1_id and interaction.user.id != getattr(self.state, "manager_id", None): return
        self.state.impact_player = True
        
        await interaction.response.edit_message(content="✅ **Impact Player rule enabled!**", view=None)
        if getattr(self.state, "tournament_server_id", None): await prompt_tournament_xi(self.channel, self.state, 1)
        else: await ask_team1_name(self.channel, self.state)
    @discord.ui.button(label="No (Standard 11)", style=discord.ButtonStyle.secondary)
    async def btn_no(self, interaction, button):
        if interaction.user.id != self.state.p1_id and interaction.user.id != getattr(self.state, "manager_id", None): return
        self.state.impact_player = False
        # 🚨 FIX: Atomic edit
        await interaction.response.edit_message(content="✅ Standard rules applied.", view=None)
        if getattr(self.state, "tournament_server_id", None): await prompt_tournament_xi(self.channel, self.state, 1)
        else: await ask_team1_name(self.channel, self.state)

# --- Step 2: Chat-Based Roster Collection Prompts ---

async def ask_team1_name(channel, state):
    await channel.send(f"🏏 <@{state.p1_id}> — Type your **team name** (e.g. `India`):\n*(Reply directly in this channel)*")
    active_setups[channel.id] = ("awaiting_team1_name", state)

async def ask_team1_xi(channel, state):
    if state.impact_player:
        await channel.send(f"📋 <@{state.p1_id}> — Type your **Playing XI + up to 5 Subs** (one per line, 11-16 total) OR type `default`:\n```text\nPlayer 1\n...\nPlayer 11\nSub 1\n...```")
    else:
        await channel.send(f"📋 <@{state.p1_id}> — Type your **Playing XI** (one per line) OR type `default` for a built-in team:\n```text\nVirat Kohli\nRohit Sharma\n...```")
    active_setups[channel.id] = ("awaiting_team1_xi", state)

async def ask_team2_name(channel, state):
    target_id = state.p2_id if state.p2_id else state.p1_id
    await channel.send(f"🏏 <@{target_id}> — Type **Team 2's name**:\n*(Reply directly in this channel)*")
    active_setups[channel.id] = ("awaiting_team2_name", state)

async def ask_team2_xi(channel, state):
    target_id = state.p2_id if state.p2_id else state.p1_id
    if state.impact_player:
        await channel.send(f"📋 <@{target_id}> — Type **Team 2's Playing XI + up to 5 Subs** (one per line, 11-16 total) OR type `default`:\n```text\nPlayer 1\n...\nPlayer 11\nSub 1\n...```")
    else:
        await channel.send(f"📋 <@{target_id}> — Type **Team 2's Playing XI** (one per line) OR type `default` for a built-in team:\n```text\nPlayer Name\n...```")
    active_setups[channel.id] = ("awaiting_team2_xi", state)


# --- Step 3: XI Verification UI ---

class Team1VerifyView(discord.ui.View):
    def __init__(self, state, channel, players):
        super().__init__(timeout=120)
        self.state = state
        self.channel = channel
        self.players = players
    @discord.ui.button(label="✅ Confirm XI", style=discord.ButtonStyle.success)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.state.p1_id: return await interaction.response.send_message("Only Team 1 can confirm.", ephemeral=True)
        self.state.t1_roster = self.players
        await interaction.response.defer()
        await interaction.message.edit(view=None)
        await self.channel.send("✅ **Team 1 XI confirmed!**")
        await ask_team2_name(self.channel, self.state)
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
    @discord.ui.button(label="✅ Confirm XI", style=discord.ButtonStyle.success)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        target_id = self.state.p2_id if self.state.p2_id else self.state.p1_id
        if interaction.user.id != target_id: return await interaction.response.send_message("Only Team 2 can confirm.", ephemeral=True)
        self.state.t2_roster = self.players
        await interaction.response.defer()
        await interaction.message.edit(view=None)
        await self.channel.send("✅ **Team 2 XI confirmed!**")
        await ask_pitch_and_weather(self.channel, self.state)
    @discord.ui.button(label="✏️ Re-enter XI", style=discord.ButtonStyle.danger)
    async def redo(self, interaction: discord.Interaction, button: discord.ui.Button):
        target_id = self.state.p2_id if self.state.p2_id else self.state.p1_id
        if interaction.user.id != target_id: return
        await interaction.response.defer()
        await interaction.message.edit(view=None)
        await ask_team2_xi(self.channel, self.state)
        
async def prompt_tournament_xi(channel, state, team_num):
    owner_id = state.p1_id if team_num == 1 else state.p2_id
    t_name = state.t1_name if team_num == 1 else state.t2_name
    view = TournamentXIView(state, channel, team_num)
    await channel.send(view.get_msg_content(), view=view)

class TournamentXIView(discord.ui.View):
    def __init__(self, state, channel, team_num):
        super().__init__(timeout=300)
        self.state = state
        self.channel = channel
        self.team_num = team_num
        self.squad = state.t1_squad if team_num == 1 else state.t2_squad
        self.owner_id = state.p1_id if team_num == 1 else state.p2_id
        self.selected_players = []
        self.req_count = 11
        self.update_ui()
        
    def update_ui(self):
        self.clear_items()
        if len(self.selected_players) < self.req_count:
            options = []
            for p in self.squad:
                if p not in self.selected_players:
                    role_short = p["role"].replace("All-Rounder", "AR").replace("Bowler", "BWL").replace("Batter", "BAT").replace("_", " ")
                    options.append(discord.SelectOption(label=p["name"], description=role_short, value=p["name"]))
            select = discord.ui.Select(placeholder=f"Pick Player {len(self.selected_players)+1} of {self.req_count}...", options=options[:25])
            select.callback = self.select_cb
            self.add_item(select)
            
        btn_undo = discord.ui.Button(label="Undo Last", style=discord.ButtonStyle.danger, disabled=len(self.selected_players)==0)
        btn_undo.callback = self.undo_cb
        self.add_item(btn_undo)
        
        btn_confirm = discord.ui.Button(label="Confirm XI", style=discord.ButtonStyle.success, disabled=len(self.selected_players) < self.req_count)
        btn_confirm.callback = self.confirm_cb
        self.add_item(btn_confirm)
        
    async def interaction_check(self, interaction: discord.Interaction):
        if interaction.user.id != self.owner_id and interaction.user.id != getattr(self.state, "manager_id", None):
            await interaction.response.send_message("❌ Only the Team Owner or Manager can select this XI.", ephemeral=True)
            return False
        return True
        
    async def select_cb(self, interaction: discord.Interaction):
        val = interaction.data["values"][0]
        player = next(p for p in self.squad if p["name"] == val)
        self.selected_players.append(player)
        self.update_ui()
        await interaction.response.edit_message(content=self.get_msg_content(), view=self)
        
    async def undo_cb(self, interaction: discord.Interaction):
        self.selected_players.pop()
        self.update_ui()
        await interaction.response.edit_message(content=self.get_msg_content(), view=self)
        
    async def confirm_cb(self, interaction: discord.Interaction):
        if self.team_num == 1:
            self.state.t1_roster = self.selected_players
            self.state.t1_subs = []
            await interaction.response.edit_message(content="✅ **Team 1 XI Confirmed!**", view=None)
            remaining = [p for p in self.squad if p not in self.selected_players]
            if getattr(self.state, "impact_player", False) and remaining:
                view = TournamentSubSelectView(self.state, self.channel, 1, remaining)
                await self.channel.send(view.get_msg_content(), view=view)
            else:
                await prompt_tournament_xi(self.channel, self.state, 2)
        else:
            self.state.t2_roster = self.selected_players
            self.state.t2_subs = []
            await interaction.response.edit_message(content="✅ **Team 2 XI Confirmed!**", view=None)
            remaining = [p for p in self.squad if p not in self.selected_players]
            if getattr(self.state, "impact_player", False) and remaining:
                view = TournamentSubSelectView(self.state, self.channel, 2, remaining)
                await self.channel.send(view.get_msg_content(), view=view)
            else:
                await ask_pitch_and_weather(self.channel, self.state)

    def get_msg_content(self):
        t_name = self.state.t1_name if self.team_num == 1 else self.state.t2_name
        msg = f"📋 <@{self.owner_id}> (or Manager) — **{t_name} XI Selection**\n"
        msg += f"Select {self.req_count} players from your squad using the dropdown below.\n"
        msg += f"⚠️ **IMPORTANT:** The order you select them determines your exact batting order!\n\n"
        for i, p in enumerate(self.selected_players, 1):
            msg += f"`{i:>2}.` **{p['name']}**\n"
        if getattr(self.state, "impact_player", False) and len(self.selected_players) == self.req_count:
            msg += "\n*After confirming, you'll choose your Impact Subs from the remaining squad.*"
        return msg

class TournamentSubSelectView(discord.ui.View):
    def __init__(self, state, channel, team_num, remaining):
        super().__init__(timeout=300)
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

    async def select_cb(self, interaction: discord.Interaction):
        val = interaction.data["values"][0]
        player = next(p for p in self.remaining if p["name"] == val)
        self.selected_subs.append(player)
        self.update_ui()
        await interaction.response.edit_message(content=self.get_msg_content(), view=self)

    async def undo_cb(self, interaction: discord.Interaction):
        self.selected_subs.pop()
        self.update_ui()
        await interaction.response.edit_message(content=self.get_msg_content(), view=self)

    async def confirm_cb(self, interaction: discord.Interaction):
        t_name = self.state.t1_name if self.team_num == 1 else self.state.t2_name
        if self.team_num == 1:
            self.state.t1_subs = self.selected_subs
            await interaction.response.edit_message(content=f"✅ **{t_name} Impact Subs Confirmed!** ({len(self.selected_subs)} selected)", view=None)
            await prompt_tournament_xi(self.channel, self.state, 2)
        else:
            self.state.t2_subs = self.selected_subs
            await interaction.response.edit_message(content=f"✅ **{t_name} Impact Subs Confirmed!** ({len(self.selected_subs)} selected)", view=None)
            await ask_pitch_and_weather(self.channel, self.state)


# --- Step 4: Pitch & Weather Select ---

async def ask_pitch_and_weather(channel, state):
    await channel.send(f"🏟️ <@{state.home_team_id}> (**{state.t1_name}** — Home Team) — Select **Pitch & Weather** conditions:", view=PitchWeatherView(state, channel))

class PitchWeatherView(discord.ui.View):
    def __init__(self, state, channel):
        super().__init__(timeout=120)
        self.state = state
        self.channel = channel
        self.s_pitch = None
        self.s_weather = None

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
        if self.s_pitch and self.s_weather:
            self.state.pitch = self.s_pitch
            self.state.weather = self.s_weather
            await interaction.message.edit(view=None)
            note = " *(DLS rules active)*" if self.state.weather == "Rain Threat" else ""
            await self.channel.send(f"✅ Pitch: **{self.s_pitch}** | Weather: **{self.s_weather}**{note}\n\nProceeding to the **toss**...")
            await begin_toss(self.channel, self.state)


# --- Step 5: Toss Engine ---

async def begin_toss(channel, state):
    # Test format (90 overs) uses a completely different simulation engine
    if state.format_overs == 90:
        return await _begin_test_match(channel, state)

    t1 = {"name": state.t1_name, "players": state.t1_roster, "subs": getattr(state, 't1_subs', []), "color": getattr(state, 't1_color', '#6B7280')}
    t2 = {"name": state.t2_name, "players": state.t2_roster, "subs": getattr(state, 't2_subs', []), "color": getattr(state, 't2_color', '#6B7280')}

    match = CricketMatch(state.p1, state.p2, state.p1_id, state.p2_id, t1, t2, state.format_overs, state.pitch, state.weather)
    match.impact_player = state.impact_player
    match.tournament_server_id = getattr(state, "tournament_server_id", None)
    match.tournament_match_id = getattr(state, "tournament_match_id", None)
    match.manager_id = getattr(state, "manager_id", None)
    match.tournament_name = getattr(state, "tournament_name", "TOURNAMENT")
    active_games[channel.id] = match

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

    if match.is_ai_game:
        if random.choice([True, False]):
            match.toss_winner = match.p1_id
            await channel.send(f"🪙 **Toss!** You won the toss, <@{match.p1_id}>. Select your decision:", view=TossDecisionView(match))
        else:
            ai_choice = random.choice(["Bat", "Bowl"])
            await channel.send(f"🪙 **Toss!** AI wins and elects to **{ai_choice} First**!")
            match.batting_first_id = match.p2_id if ai_choice == "Bat" else match.p1_id
            match.bowling_first_id = match.p1_id if ai_choice == "Bat" else match.p2_id
            match.innings1 = InningsState(match.team2 if ai_choice == "Bat" else match.team1, match.team1 if ai_choice == "Bat" else match.team2)
            match.current_innings = match.innings1
            await prompt_bowler_then_hub(channel, match)
    else:
        await channel.send(f"🪙 **Toss Time!** <@{match.p2_id}> — call the coin!", view=TossCallView(match))

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
        if choice == "Bat":
            self.match.batting_first_id = self.match.toss_winner
            self.match.bowling_first_id = self.match.p1_id if self.match.toss_winner == self.match.p2_id else self.match.p2_id
            t_bat = self.match.team1 if self.match.toss_winner == self.match.p1_id else self.match.team2
            t_bowl = self.match.team2 if self.match.toss_winner == self.match.p1_id else self.match.team1
        else:
            self.match.bowling_first_id = self.match.toss_winner
            self.match.batting_first_id = self.match.p1_id if self.match.toss_winner == self.match.p2_id else self.match.p2_id
            t_bowl = self.match.team1 if self.match.toss_winner == self.match.p1_id else self.match.team2
            t_bat = self.match.team2 if self.match.toss_winner == self.match.p1_id else self.match.team1

        self.match.innings1 = InningsState(t_bat, t_bowl)
        self.match.current_innings = self.match.innings1
        await interaction.response.defer()
        await interaction.message.edit(view=None)
        await prompt_bowler_then_hub(interaction, self.match)
    @discord.ui.button(label="🏏 Bat First", style=discord.ButtonStyle.success)
    async def bat(self, interaction, button): await self.finalize_toss(interaction, "Bat")
    @discord.ui.button(label="🎯 Bowl First", style=discord.ButtonStyle.danger)
    async def bowl(self, interaction, button): await self.finalize_toss(interaction, "Bowl")


# --- The Listener connecting Chat inputs to the State Machine ---

@bot.event
async def on_message(message: discord.Message):
    if message.author.bot: return

    content = message.content
    if content.startswith("cvt "):
        message.content = "cv tournament " + content[4:]
    elif content.startswith("cv t "):
        message.content = "cv tournament " + content[5:]

    channel_id = message.channel.id
    if channel_id not in active_setups:
        return await bot.process_commands(message)

    stage, state = active_setups[channel_id]

    if stage == "awaiting_team1_name":
        if message.author.id != state.p1_id: return
        state.t1_name = message.content.strip()[:30]
        del active_setups[channel_id]
        await message.channel.send(f"✅ Team 1 name set: **{state.t1_name}**")
        await ask_team1_xi(message.channel, state)

    elif stage == "awaiting_team1_xi":
        if message.author.id != state.p1_id: return
        
        req_length = 11
        if message.content.strip().lower() == "default":
            players = list(TEAMS_DATA["Team 1"]["players"])
            if state.impact_player:
                state.t1_subs = [{"name": "Extra Batter", "bat": 85, "bowl": 10, "archetype": "Aggressor", "role": "Batter"}, {"name": "Extra Bowler", "bat": 10, "bowl": 85, "archetype": "Standard", "role": "Bowler_Pace"}]
            else:
                state.t1_subs = []
            missing = []
        else:
            db = get_all_players()
            parsed_players, missing = parse_pasted_roster(message.content, db)
            players = parsed_players[:11]
            state.t1_subs = parsed_players[11:16] if state.impact_player else []
            
        if missing or len(players) < req_length:
            err = f"❌ **Roster Validation Failed ({len(players)}/{req_length} Found)**\n\n"
            if players: err += f"✅ **Accepted:** {', '.join([p['name'] for p in players])}\n"
            if missing: err += f"❌ **Missing from DB:** {', '.join(missing)}\n\n"
            err += f"Please check spellings or add missing players to your CSV, then type your full list again."
            return await message.channel.send(err)

        del active_setups[channel_id]
        xi_text = format_xi_display(players)
        if state.impact_player and state.t1_subs:
            xi_text += "\n\n**Impact Subs:**\n" + format_xi_display(state.t1_subs)
        await message.channel.send(f"📋 **{state.t1_name} XI** Verified:\n{xi_text}\n\nIs this correct?", view=Team1VerifyView(state, message.channel, players))

    elif stage == "awaiting_team2_name":
        target_id = state.p2_id if state.p2_id else state.p1_id
        if message.author.id != target_id: return
        state.t2_name = message.content.strip()[:30]
        del active_setups[channel_id]
        await message.channel.send(f"✅ Team 2 name set: **{state.t2_name}**")
        
        if state.p2_id is None and not getattr(state, 'sim_only', False):
            state.t2_roster = TEAMS_DATA["Team 2"]["players"]
            if getattr(state, "impact_player", False):
                state.t2_subs = [{"name": "Extra Batter 2", "bat": 85, "bowl": 10, "archetype": "Aggressor", "role": "Batter"}, {"name": "Extra Bowler 2", "bat": 10, "bowl": 85, "archetype": "Standard", "role": "Bowler_Pace"}]
            else:
                state.t2_subs = []
            await message.channel.send(f"🤖 AI team **{state.t2_name}** will use the built-in roster.")
            await ask_pitch_and_weather(message.channel, state)
        else:
            await ask_team2_xi(message.channel, state)

    elif stage == "awaiting_team2_xi":
        target_id = state.p2_id if state.p2_id else state.p1_id
        if message.author.id != target_id: return
        
        req_length = 11
        if message.content.strip().lower() == "default":
            players = list(TEAMS_DATA["Team 2"]["players"])
            if state.impact_player:
                state.t2_subs = [{"name": "Extra Batter 2", "bat": 85, "bowl": 10, "archetype": "Aggressor", "role": "Batter"}, {"name": "Extra Bowler 2", "bat": 10, "bowl": 85, "archetype": "Standard", "role": "Bowler_Pace"}]
            else:
                state.t2_subs = []
            missing = []
        else:
            db = get_all_players()
            parsed_players, missing = parse_pasted_roster(message.content, db)
            players = parsed_players[:11]
            state.t2_subs = parsed_players[11:16] if state.impact_player else []
            
        if missing or len(players) < req_length:
            err = f"❌ **Roster Validation Failed ({len(players)}/{req_length} Found)**\n\n"
            if players: err += f"✅ **Accepted:** {', '.join([p['name'] for p in players])}\n"
            if missing: err += f"❌ **Missing from DB:** {', '.join(missing)}\n\n"
            err += f"Please check spellings or add missing players to your CSV, then type your full list again."
            return await message.channel.send(err)

        del active_setups[channel_id]
        xi_text = format_xi_display(players)
        if state.impact_player and state.t2_subs:
            xi_text += "\n\n**Impact Subs:**\n" + format_xi_display(state.t2_subs)
        await message.channel.send(f"📋 **{state.t2_name} XI** Verified:\n{xi_text}\n\nIs this correct?", view=Team2VerifyView(state, message.channel, players))

    await bot.process_commands(message)

# --- The Slash Command Initialization ---

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
    
    # 🚨 FIX: Register the setup immediately so /endmatch works instantly!
    active_setups[interaction.channel.id] = ("format_selection", state)
    
    opp_str = opponent.mention if opponent else "🤖 AI"
    
    await interaction.edit_original_response(content=f"🏏 **Match Setup**\n**Host:** {interaction.user.mention}\n**Opponent:** {opp_str}\n\nStep 1: Select Format below:", view=FormatSelectView(state, interaction.channel))

# ==========================================
# 🏏 TEST MATCH SIMULATION
# ==========================================

_TEST_SESSION_NAMES = {1: "Morning", 2: "Afternoon", 3: "Evening"}

# Rain weather types aren't in the test engine — map them to closest equivalent


def render_test_embed(match: TestMatchObj) -> discord.Embed:
    """Live scoreboard — ODI/T20 style with per-team rows showing all innings."""
    innings = match.current_innings
    embed   = discord.Embed(color=0xFFFFFF)

    sess  = _TEST_SESSION_NAMES.get(match.session, "Morning")
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

    # One row per team — both innings on the same line separated by " & "
    for team in [match.team1, match.team2]:
        tnm         = team["name"]
        is_batting  = (innings.batting_team["name"] == tnm)
        team_inns   = [i for i in inns if i.batting_team["name"] == tnm]

        if not team_inns:
            score_str = "Yet to Bat"
        else:
            score_str = " & ".join(_sc(i) for i in team_inns)

        ball   = "<a:ball:1510370830163640320> " if is_batting else ""
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
    """Final match embed — compact per-innings summary (image has the full detail)."""
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

    # ── Session character ──────────────────────────────────────────────────
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

    # ── Wicket narrative ───────────────────────────────────────────────────
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

    # ── Standout bowler ────────────────────────────────────────────────────
    if sess_bowlers:
        _, _, best_name, best_ovs, best_runs, best_wkts = sess_bowlers[0]
        if best_wkts >= 4:
            lines.append(f"**{best_name}** was simply outstanding — **{best_wkts}/{best_runs}** in {best_ovs} overs.")
        elif best_wkts >= 2:
            lines.append(f"**{best_name}** led the attack with {best_wkts} wickets for {best_runs} runs.")
        elif best_runs <= 12 and int(best_ovs.split(".")[0]) >= 5:
            lines.append(f"**{best_name}** was the standout in economy terms — miserly figures of {best_ovs}-{best_runs}-{best_wkts}.")

    # ── Pitch / weather flavour ────────────────────────────────────────────
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
    """Session summary embed — replaces the old ASCII code-block output."""
    embed    = discord.Embed(color=0xFFFFFF)
    sess_nm  = _TEST_SESSION_NAMES.get(snap["session"], "Morning")
    embed.title = f"🏏 Day {snap['day']} · {sess_nm} Session"

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
    """Full innings scorecard embed — all batters with dismissals and all bowlers."""
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

    # ── All batters ──────────────────────────────────────────────────────────
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

    # ── All bowlers ──────────────────────────────────────────────────────────
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


def generate_test_scorecard_image(match: TestMatchObj) -> io.BytesIO:
    """Crimson-Cricket-inspired 4-innings Test scorecard (1200 × 750)."""
    _W, _H = 1200, 750
    _HDR_H, _RES_H = 120, 38
    _PANEL_W = 600
    _PANEL_H = (_H - _HDR_H - _RES_H) // 2   # ≈ 296 px per row

    _GRAD_L = (10, 15, 50);  _GRAD_M = (15, 60, 110);  _GRAD_R = (20, 100, 145)
    _C_EVEN = (248, 248, 248);  _C_ODD = (236, 236, 236);  _C_DIV = (200, 200, 200)
    _C_WH   = "#FFFFFF";  _C_GY = "#888888";  _C_NM = "#111111";  _C_SC = "#00D4FF"
    _C_GOLD = (255, 215, 0)

    img = Image.new("RGB", (_W, _H), "#F5F5F5")
    d   = ImageDraw.Draw(img)

    def _lerp(a, b, t): return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))
    def _hex(h):
        h = h.lstrip('#')
        return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))
    def _tw(text, font):
        return font.getbbox(text)[2] if hasattr(font, 'getbbox') else len(text) * 10
    def _th(font):
        bb = font.getbbox("Ag") if hasattr(font, 'getbbox') else None
        return (bb[3] - bb[1]) if bb else 14

    try:
        _fbd = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
        _frg = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
        fHUGE = ImageFont.truetype(_fbd, 38);  fRES = ImageFont.truetype(_fbd, 17)
        fSUB  = ImageFont.truetype(_frg, 14);  fTEAM = ImageFont.truetype(_fbd, 18)
        fSC   = ImageFont.truetype(_fbd, 24);  fOVR = ImageFont.truetype(_frg, 12)
        fCOL  = ImageFont.truetype(_fbd, 12);  fNM  = ImageFont.truetype(_fbd, 14)
        fRUN  = ImageFont.truetype(_fbd, 16);  fSM  = ImageFont.truetype(_frg, 12)
    except Exception:
        fHUGE = fRES = fSUB = fTEAM = fSC = fOVR = fCOL = fNM = fRUN = fSM = ImageFont.load_default()

    # Gradient header
    for x in range(_W):
        t   = x / (_W - 1)
        col = _lerp(_GRAD_L, _GRAD_M, t * 2) if t < 0.5 else _lerp(_GRAD_M, _GRAD_R, (t - 0.5) * 2)
        d.line([(x, 0), (x, _HDR_H)], fill=col)
    d.text((40, 18), "TEST MATCH", fill=_C_WH, font=fHUGE)
    sub = f"{match.team1['name']}  vs  {match.team2['name']}  ·  {match.pitch} / {match.weather}"
    d.text((44, 18 + _th(fHUGE) + 6), sub[:72], fill="#BBBBBB", font=fSUB)

    # Match number — top-right of header, small/unobtrusive
    _ctr = _format_match_no_label("test")
    _ctr_w = _tw(_ctr, fSM)
    d.text((_W - 12 - _ctr_w, _HDR_H - _th(fSM) - 8), _ctr, fill="#AAAAAA", font=fSM)

    # Result bar
    d.rectangle([(0, _HDR_H), (_W, _HDR_H + _RES_H)], fill=(22, 22, 22))
    res_str = (match.result or "IN PROGRESS").upper()
    rx = 600 - _tw(res_str, fRES) // 2
    d.text((rx, _HDR_H + (_RES_H - _th(fRES)) // 2), res_str, fill="#FFD700", font=fRES)
    _potm = _test_player_of_match(match)
    if _potm:
        _potm_str = f"★ POTM: {_potm.upper()}"
        _py = _HDR_H + (_RES_H - _th(fSUB)) // 2
        d.text((_W - 12 - _tw(_potm_str, fSUB), _py), _potm_str, fill=_C_GOLD, font=fSUB)

    # 4 innings panels  (positions: top-left, top-right, bot-left, bot-right)
    panel_top = _HDR_H + _RES_H
    for inn_idx in range(4):
        col  = inn_idx % 2
        row  = inn_idx // 2
        x0   = col * _PANEL_W
        y0   = panel_top + row * _PANEL_H
        x1, y1 = x0 + _PANEL_W, y0 + _PANEL_H
        bg   = _C_EVEN if inn_idx % 2 == 0 else _C_ODD
        d.rectangle([(x0, y0), (x1, y1)], fill=bg)
        d.rectangle([(x0, y0), (x1, y1)], outline=_C_DIV, width=1)

        _BAR = 48
        if inn_idx < len(match.innings_list):
            inn  = match.innings_list[inn_idx]
            tc   = _hex(inn.batting_team.get("color", "#1D4ED8"))
            d.rectangle([(x0, y0), (x1, y0 + _BAR)], fill=tc)

            lbl  = f"INNINGS {inn_idx + 1}"
            if getattr(inn, "declared", False):
                lbl += "  (DEC)"
            elif inn_idx == 2 and match.follow_on_enforced:
                lbl += "  (FOLLOW-ON)"
            d.text((x0 + 10, y0 + 5),  lbl, fill="#DDDDDD", font=fOVR)
            tnm  = inn.batting_team["name"][:20].upper()
            d.text((x0 + 10, y0 + 18), tnm, fill=_C_WH,     font=fTEAM)

            dec_tag = "d" if getattr(inn, "declared", False) else ""
            sc   = f"{inn.total_runs}/{inn.wickets}{dec_tag}"
            d.text((x1 - _tw(sc, fSC) - 10, y0 + (_BAR - _th(fSC)) // 2), sc, fill=_C_SC, font=fSC)
            ovr  = f"{inn.overs_str} ov  RR:{inn.run_rate}"
            d.text((x1 - _tw(ovr, fOVR) - 10, y0 + _BAR - _th(fOVR) - 3), ovr, fill="#CCCCCC", font=fOVR)

            # Stats columns
            _NX, _RX, _BX = x0 + 10, x0 + 400, x0 + 460
            sy = y0 + _BAR + 6

            # Batting
            d.text((_NX, sy), "BATTER", fill=_C_GY, font=fCOL)
            d.text((_RX, sy), "R",      fill=_C_GY, font=fCOL)
            d.text((_BX, sy), "B",      fill=_C_GY, font=fCOL)
            sy += _th(fCOL) + 3;  d.line([(x0, sy), (x1, sy)], fill=_C_DIV, width=1);  sy += 4

            bats = sorted(
                [(p, inn.batting_stats[p["name"]]) for p in inn.batting_team["players"]
                 if inn.batting_stats[p["name"]].balls_faced > 0],
                key=lambda x: x[1].runs_scored, reverse=True
            )[:2]
            for p, st in bats:
                nm = p["name"][:22].upper()
                rs = f"{st.runs_scored}{'*' if st.dismissal == 'not out' else ''}"
                d.text((_NX, sy), nm, fill=_C_NM, font=fNM)
                d.text((_RX, sy), rs, fill=_C_NM, font=fRUN)
                d.text((_BX, sy), str(st.balls_faced), fill="#555555", font=fSM)
                sy += _th(fNM) + 5

            sy += 4;  d.line([(x0 + 10, sy), (x1 - 10, sy)], fill=_C_DIV, width=1);  sy += 6

            # Bowling
            _WX = x0 + 400;  _OX = x0 + 470
            d.text((_NX, sy), "BOWLER", fill=_C_GY, font=fCOL)
            d.text((_WX, sy), "W-R",    fill=_C_GY, font=fCOL)
            d.text((_OX, sy), "O",      fill=_C_GY, font=fCOL)
            sy += _th(fCOL) + 3;  d.line([(x0, sy), (x1, sy)], fill=_C_DIV, width=1);  sy += 4

            bowls = sorted(
                [(p, inn.bowling_stats[p["name"]]) for p in inn.bowling_team["players"]
                 if inn.bowling_stats[p["name"]].balls_bowled > 0],
                key=lambda x: (x[1].wickets_taken, -x[1].runs_conceded), reverse=True
            )[:2]
            for p, st in bowls:
                nm  = p["name"][:22].upper()
                wr  = f"{st.wickets_taken}-{st.runs_conceded}"
                d.text((_NX, sy), nm, fill=_C_NM, font=fNM)
                d.text((_WX, sy), wr, fill=_C_NM, font=fRUN)
                d.text((_OX, sy), st.overs_str, fill="#555555", font=fSM)
                sy += _th(fNM) + 5
        else:
            # Panel not yet played
            d.rectangle([(x0, y0), (x1, y0 + _BAR)], fill=(70, 70, 70))
            d.text((x0 + 10, y0 + 5),  f"INNINGS {inn_idx + 1}", fill="#CCCCCC", font=fOVR)
            ytb = "YET TO BAT"
            d.text((x0 + (_PANEL_W - _tw(ytb, fTEAM)) // 2, y0 + _BAR + 30), ytb, fill="#AAAAAA", font=fTEAM)

    # Grid lines
    d.line([(_PANEL_W, panel_top), (_PANEL_W, _H)], fill=_C_DIV, width=2)
    d.line([(0, panel_top + _PANEL_H), (_W, panel_top + _PANEL_H)], fill=_C_DIV, width=2)

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf


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


async def _test_finish_match(match: TestMatchObj, channel_id: int, channel):
    """Shared finish routine: increment counter, post final scorecard, clean up."""
    _increment_match_count("test")
    result_text = match.result or "Match Drawn"
    img_buf = None
    try:
        img_buf = generate_test_scorecard_image(match)
        file    = discord.File(fp=img_buf, filename="test_scorecard.png")
        await channel.send(
            f"🏆 **Test Match Complete · {result_text}**",
            file=file
        )
    except Exception:
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
                    if img_buf:
                        img_buf.seek(0)
                        log_file = discord.File(fp=img_buf, filename="test_scorecard.png")
                    else:
                        log_file = discord.File(fp=generate_test_scorecard_image(match), filename="test_scorecard.png")
                    t1 = match.team1["name"]
                    t2 = match.team2["name"]
                    await log_channel.send(
                        f"📋 **Match Log** · {t1} vs {t2} · <#{channel.id}>",
                        file=log_file
                    )
            except Exception as _log_err:
                print(f"⚠️ Test match log send failed: {_log_err}")

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


# ── Interactive over-by-over mode ─────────────────────────────────────────────
#
# Flow per over:
#   1. TestInteractiveBowlerSelectView  — pick who bowls
#   2. TestInteractiveDeliveryView      — pick delivery type (each ball)
#   3. TestInteractiveShotView          — pick shot (each ball) → execute → show result
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
    await channel.send(embed=embed, view=TestInteractiveBowlerSelectView(match, channel_id, options))


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
    await channel.send(embed=embed, view=TestInteractiveDeliveryView(match, channel_id, is_spin, bowler))


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
    await channel.send(embed=embed, view=TestInteractiveShotView(match, channel_id))


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

    # Same over continues — show next delivery prompt
    await _test_ia_show_delivery(channel, match, channel_id)


def _test_bowling_owner(match: TestMatchObj):
    """Return the user ID that owns the bowling team (None = AI owns it)."""
    inn = match.current_innings
    if inn.bowling_team is match.team1:
        return getattr(match, "host_id", None)
    return getattr(match, "p2_id", None)

def _test_batting_owner(match: TestMatchObj):
    """Return the user ID that owns the batting team (None = AI owns it)."""
    inn = match.current_innings
    if inn.batting_team is match.team1:
        return getattr(match, "host_id", None)
    return getattr(match, "p2_id", None)

def _test_ai_bowling(match: TestMatchObj) -> bool:
    """True when the AI (no p2) controls the bowling team."""
    return getattr(match, "p2_id", None) is None and match.current_innings.bowling_team is match.team2

def _test_ai_batting(match: TestMatchObj) -> bool:
    """True when the AI (no p2) controls the batting team."""
    return getattr(match, "p2_id", None) is None and match.current_innings.batting_team is match.team2


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

    # ── Row 0: Interactive / quick simulation ──────────────────────────────────

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

    # ── Row 1: Session / Day / Innings ─────────────────────────────────────────

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
        # After FULL session: advance_session() resets overs_in_session → 0.
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

    # ── Row 2: Declaration ─────────────────────────────────────────────────────

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


# ── Test match toss views ──────────────────────────────────────────────────────

class TestTossCallView(discord.ui.View):
    """P2 calls heads/tails for the Test match toss."""
    def __init__(self, state, channel):
        super().__init__(timeout=120)
        self.state   = state
        self.channel = channel

    async def _call(self, interaction: discord.Interaction, call: str):
        if interaction.user.id != self.state.p2_id:
            return await interaction.response.send_message("Only the opponent calls the toss.", ephemeral=True)
        flip = random.choice(["Heads", "Tails"])
        winner_id = self.state.p2_id if call == flip else self.state.p1_id
        self.state._test_toss_winner = winner_id
        await interaction.response.defer()
        await interaction.message.edit(view=None)
        await interaction.channel.send(
            f"🪙 Landed on **{flip}**! <@{winner_id}> wins the toss — choose:",
            view=TestTossDecisionView(self.state, self.channel))

    @discord.ui.button(label="Heads", style=discord.ButtonStyle.primary)
    async def heads(self, i, b): await self._call(i, "Heads")
    @discord.ui.button(label="Tails", style=discord.ButtonStyle.secondary)
    async def tails(self, i, b): await self._call(i, "Tails")


class TestTossDecisionView(discord.ui.View):
    """Toss winner picks Bat/Bowl for the Test match."""
    def __init__(self, state, channel):
        super().__init__(timeout=120)
        self.state   = state
        self.channel = channel

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.state._test_toss_winner:
            await interaction.response.send_message("Only the toss winner can decide.", ephemeral=True)
            return False
        return True

    async def _decide(self, interaction: discord.Interaction, choice: str):
        state = self.state
        t1 = {"name": state.t1_name, "players": state.t1_roster, "color": getattr(state, 't1_color', '#1D4ED8')}
        t2 = {"name": state.t2_name, "players": state.t2_roster, "color": getattr(state, 't2_color', '#DC2626')}
        winner_is_p1 = (state._test_toss_winner == state.p1_id)
        winning_team = t1 if winner_is_p1 else t2
        losing_team  = t2 if winner_is_p1 else t1
        t_bat  = winning_team if choice == "Bat" else losing_team
        t_bowl = losing_team  if choice == "Bat" else winning_team

        match          = TestMatchObj(t_bat, t_bowl, state.pitch, state.weather)
        match.host_id  = state.p1_id
        match.p2_id    = getattr(state, "p2_id", None)
        active_test_matches[self.channel.id] = match

        await interaction.response.defer()
        await interaction.message.edit(view=None)
        await self.channel.send(
            f"**{t_bat['name']}** will bat first.\n\nChoose how to simulate:")
        await self.channel.send(embed=render_test_embed(match), view=TestSimView(match, self.channel.id))

    @discord.ui.button(label="🏏 Bat First", style=discord.ButtonStyle.success)
    async def bat(self, i, b): await self._decide(i, "Bat")
    @discord.ui.button(label="🎯 Bowl First", style=discord.ButtonStyle.danger)
    async def bowl(self, i, b): await self._decide(i, "Bowl")


async def _begin_test_match(channel, state):
    """Branch from begin_toss for Test (format_overs == 90) matches."""
    t1 = {"name": state.t1_name, "players": state.t1_roster, "color": getattr(state, 't1_color', '#1D4ED8')}
    t2 = {"name": state.t2_name, "players": state.t2_roster, "color": getattr(state, 't2_color', '#DC2626')}
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
        match = TestMatchObj(t_bat, t_bowl, state.pitch, weather)
        active_test_matches[channel.id] = match
        await asyncio.to_thread(_test_sim_match, match)
        await _test_finish_match(match, channel.id, channel)
        return

    # AI game: auto-toss, show TestSimView
    if state.p2_id is None:
        ai_choice   = random.choice(["Bat", "Bowl"])
        t_bat  = t2 if ai_choice == "Bat" else t1   # AI is team2
        t_bowl = t1 if ai_choice == "Bat" else t2
        await channel.send(
            f"🪙 **Toss!** AI wins and elects to **{ai_choice}** first! "
            f"**{t_bat['name']}** will bat.\n\nChoose simulation mode:")
        match          = TestMatchObj(t_bat, t_bowl, state.pitch, weather)
        match.host_id  = state.p1_id
        match.p2_id    = getattr(state, "p2_id", None)
        active_test_matches[channel.id] = match
        await channel.send(embed=render_test_embed(match), view=TestSimView(match, channel.id))
        return

    # Two-player: interactive toss
    state._test_toss_winner = None
    await channel.send(
        f"🪙 **Toss Time!** <@{state.p2_id}> — call the coin!",
        view=TestTossCallView(state, channel))


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

    # Clear injuries that have expired before this match
    for team_data in [t1_data, t2_data]:
        for p in team_data["squad"]:
            if p.get("injured") and p.get("injury_until_match", 0) < current_mid:
                p.pop("injured", None)
                p.pop("injury_until_match", None)
                p.pop("injury_severity", None)

    # Announce any injury news queued from the last match
    injury_news = tourney.pop("pending_injury_news", [])
    if injury_news:
        team_owners = {t["name"]: t.get("owner_id") for t in tourney.get("teams", [])}
        lines = ["🚑 **Injury Report:**"]
        pings = []
        for item in injury_news:
            m_word = "team match" if item["severity"] == 1 else "team matches"
            lines.append(f"• **{item['player']}** ({item['team']}) — ruled out for their next **{item['severity']}** {m_word}")
            owner_id = team_owners.get(item["team"])
            if owner_id and owner_id not in pings:
                pings.append(owner_id)
        if pings:
            lines.append(" ".join(f"<@{uid}>" for uid in pings))
        inj_ch_id = tourney.get("injury_channel_id")
        announce_ch = (bot.get_channel(int(inj_ch_id)) if inj_ch_id else None) or channel
        await announce_ch.send("\n".join(lines))

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
    state.format_overs = tourney.get("format_overs", 20)
    state.impact_player = tourney.get("impact_player", False)
    
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
            print(f"⚠️ Match banner failed: {e}")
            await channel.send(f"🏆 **Tournament Match {match_data['match_id']}**\n**{team1_name}** (<@{p1_id}>) vs **{team2_name}** (<@{p2_id}>)\n\nFormat: **{state.format_overs} Overs**")
    else:
        await channel.send(f"🏆 **Tournament Match {match_data['match_id']}**\n**{team1_name}** (<@{p1_id}>) vs **{team2_name}** (<@{p2_id}>)\n\nFormat: **{state.format_overs} Overs**")

    await prompt_tournament_xi(channel, state, 1)

@bot.tree.command(name="endmatch", description="Force cancel the current match or setup in this channel.")
async def endmatch_cmd(interaction: discord.Interaction):
    channel_id = interaction.channel.id
    cleared = False
    
    if channel_id in active_games:
        del active_games[channel_id]
        cleared = True

    if channel_id in active_setups:
        del active_setups[channel_id]
        cleared = True

    if channel_id in active_test_matches:
        del active_test_matches[channel_id]
        cleared = True

    if cleared:
        await interaction.response.send_message("🛑 **Match and setup forcefully terminated.** Memory cleared.")
    else:
        await interaction.response.send_message("⚠️ There is no active match or setup running in this channel.", ephemeral=True)

# ── Help embeds ──────────────────────────────────────────────────────────────

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
    e.add_field(name="TEST format in /match",                           value="Select 'TEST (90 overs)' in the format dropdown to play a 5-day Test with session/innings/full-match modes.", inline=False)
    e.add_field(name="/impactplayer",                                   value="During an active match, swap in your Impact Player (if rule is on).", inline=False)
    e.add_field(name="/endmatch  ·  `cv endmatch`  ·  `cv em`",       value="Force-cancel the current match or setup in this channel.", inline=False)
    e.add_field(name="/my_tier",                                        value="Check your subscription tier and remaining daily match limits.", inline=False)
    e.set_footer(text="Slash commands work from anywhere  ·  cv / cv<shortcut> need the cv prefix")
    return e

def _help_players_embed(is_admin: bool):
    e = discord.Embed(title="🔍 Players & Database", color=discord.Color.blue())
    e.add_field(name="/searchplayer <name>  ·  `cv sp`", value="Search for a player — shows their role (ratings & archetype hidden unless you're an admin in a ratings channel).", inline=False)
    e.add_field(name="/playerlist  ·  `cv playerlist`  ·  `cv pl`", value="Download the full player database as a .txt file — names only, grouped by tier, shuffled within each tier.", inline=False)
    e.add_field(name="📋 How to enter Playing XI",        value="When prompted during a match, paste 11 player names (one per line). Names must match the database exactly.", inline=False)
    e.add_field(name="🏟️ Pitch & Weather Conditions",    value="15 pitch types · 10 weather conditions — each affects pace, spin and batting differently across T20 and ODI.", inline=False)
    if is_admin:
        e.add_field(name="​", value="─── **Admin — DB Management** ───", inline=False)
        e.add_field(name="/addplayer  ·  `cv ap`",            value="Add a player: name & ratings modal → then role & archetype dropdowns.", inline=False)
        e.add_field(name="/updateplayer <name>  ·  `cv up`",  value="Edit an existing player — all fields pre-filled, change only what you need.", inline=False)
        e.add_field(name="`cv deleteplayer`  ·  `cv dp`",     value="Remove a player from the database.", inline=False)
        e.add_field(name="`cv cleanduplicates`  ·  `cv cd`",  value="Remove duplicate entries from the database.", inline=False)
    e.set_footer(text="Ratings are hidden in public channels — use a ratings channel or contact owner")
    return e

def _help_tournament_embed():
    e = discord.Embed(title="🏆 Tournament", color=discord.Color.gold())
    e.description = "All commands: **`cv tournament <cmd>`** · shortcut **`cvt <cmd>`** · group alias **`cv t <cmd>`**"
    e.add_field(name="👁️ View",
        value=("`status` · `standings` · `groups` · `leaderboard <category>`\n"
               "`squad [team]`  ·  shortcut: **`cv squad`** / **`cv sq`**\n"
               "`player_stats <team> <player>` · `match_scorecard <id>`"),
        inline=False)
    e.add_field(name="🏏 Play",
        value=("`submit_squad` · `next_match` · `play <id>` · `play_next`\n"
               "`simulate_all`  ·  alias: **`simall`** — [Owner] instantly sim all pending matches"),
        inline=False)
    e.add_field(name="⚙️ Manage",
        value=("`create <name> <format>` · `add_team <name> @owner` · `start`\n"
               "`set_theme` · `set_team_color` · `set_team_logo` · `set_schedule`\n"
               "`generate_knockouts` · `force_delete`"),
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
               "`cv authadmin @user`  ·  `cv aa` — toggle admin access"),
        inline=False)
    e.add_field(name="Tournament Owner",
        value=("`cvt simulate_all`  ·  `cvt simall` — instantly sim all pending matches\n"
               "`cvt dev_setup` — fill squads with random players & auto-start (testing)\n"
               "`cvt set_schedule` — set a custom fixture order"),
        inline=False)
    e.add_field(name="User Tiers",   value="`Basic` · `Standard` · `Single` · `Server Pro` · `None`", inline=True)
    e.add_field(name="Server Tiers", value="`Bronze` · `Silver` · `Gold` · `Diamond` · `None`",       inline=True)
    return e

# ── Help navigation view ──────────────────────────────────────────────────────

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

# ==========================================
# 🔍 8. PUBLIC DATABASE SEARCH
# ==========================================

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
        embed.set_footer(text="Ratings & archetype are hidden.")
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


# ── Player list helpers ───────────────────────────────────────────────────────

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


@bot.tree.command(name="playerlist", description="Download the full player database grouped by tier (no ratings shown).")
async def playerlist_cmd(interaction: discord.Interaction):
    await interaction.response.defer()
    players = get_all_players()
    if not players:
        return await interaction.followup.send("❌ Player database is empty.")
    txt = _build_playerlist_txt(players)
    buf = io.BytesIO(txt.encode("utf-8"))
    buf.seek(0)
    await interaction.followup.send(
        f"📋 **Player Database** — {len(players)} players across 4 tiers.\nRatings are hidden. Players within each tier are shuffled.",
        file=discord.File(fp=buf, filename="cricverse_players.txt")
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

# ==========================================
# 🛡️ 9. ADMIN DATABASE CONTROLS 
# ==========================================

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



# ==========================================
# 🛡️ OWNER SLASH COMMANDS (dropdown-based)
# ==========================================

@bot.tree.command(name="set_user_tier", description="[OWNER] Assign a subscription tier to a user.")
@app_commands.choices(tier=[
    app_commands.Choice(name="Basic (1 Sim/Day | T20/ODI)", value="Basic"),
    app_commands.Choice(name="Standard (1 Sim/Day | All)", value="Standard"),
    app_commands.Choice(name="Single (1 Match Consumable)", value="Single"),
    app_commands.Choice(name="Server Pro (Unlimited on Silver/Diamond)", value="Server Pro"),
    app_commands.Choice(name="None (Remove)", value="None")
])
async def set_user_tier_cmd(interaction: discord.Interaction, user: discord.Member, tier: app_commands.Choice[str]):
    if interaction.user.id != ADMIN_DISCORD_ID:
        return await interaction.response.send_message("❌ Owner only.", ephemeral=True)
    msg = update_user_tier(str(user.id), tier.value, tier.name, user.mention)
    await interaction.response.send_message(msg, ephemeral=True)

@bot.tree.command(name="set_server_tier", description="[OWNER] Assign a subscription tier to a server.")
@app_commands.choices(tier=[
    app_commands.Choice(name="Bronze (10 Sims/Day | All)", value="Bronze"),
    app_commands.Choice(name="Silver (Unlimited | All)", value="Silver"),
    app_commands.Choice(name="Gold (Tournament Only)", value="Gold"),
    app_commands.Choice(name="Diamond (Unlimited + Tournament)", value="Diamond"),
    app_commands.Choice(name="None (Remove)", value="None")
])
async def set_server_tier_cmd(interaction: discord.Interaction, server_id: str, tier: app_commands.Choice[str]):
    if interaction.user.id != ADMIN_DISCORD_ID:
        return await interaction.response.send_message("❌ Owner only.", ephemeral=True)
    msg = update_server_tier(server_id, tier.value, tier.name)
    await interaction.response.send_message(msg, ephemeral=True)

# ==========================================
# 💬 10. PREFIX COMMANDS & COG
# ==========================================

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
            print(f"An error occurred in a prefix command '{ctx.command}': {error}")
            await ctx.send(f"An unexpected error occurred while running that command.")

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
        opp_str = opponent.mention if opponent else "🤖 AI"
        await ctx.send(f"🏏 **Match Setup**\n**Host:** {ctx.author.mention}\n**Opponent:** {opp_str}\n\nStep 1: Select Format below:", view=FormatSelectView(state, ctx.channel))

    @commands.command(name="endmatch", aliases=["em"], help="Force cancel the current match or setup in this channel.\nUsage: endmatch")
    async def endmatch(self, ctx):
        channel_id = ctx.channel.id
        cleared = False
        if channel_id in active_games:
            del active_games[channel_id]
            cleared = True
        if channel_id in active_setups:
            del active_setups[channel_id]
            cleared = True
        if channel_id in active_test_matches:
            del active_test_matches[channel_id]
            cleared = True
        if cleared:
            await ctx.send("🛑 **Match and setup forcefully terminated.** Memory cleared.")
        else:
            await ctx.send("⚠️ There is no active match or setup running in this channel.")

    @commands.command(name="playerlist", aliases=["pl"], help="Download full player database grouped by tier (no ratings).\nUsage: playerlist")
    async def playerlist(self, ctx):
        players = get_all_players()
        if not players:
            return await ctx.send("❌ Player database is empty.")
        txt = _build_playerlist_txt(players)
        buf = io.BytesIO(txt.encode("utf-8"))
        buf.seek(0)
        await ctx.send(
            f"📋 **Player Database** — {len(players)} players across 4 tiers.\nRatings are hidden. Players within each tier are shuffled.",
            file=discord.File(fp=buf, filename="cricverse_players.txt")
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

        exact = next((p for p in all_players if p["name"].lower() == search_query.lower()), None)
        if exact:
            return await send_player_profile(FakeInteraction(), exact, show_ratings)

        subs = [p for p in all_players if search_query.lower() in p["name"].lower()]
        fuzz = difflib.get_close_matches(search_query, player_names, n=1, cutoff=0.2)

        if not subs and not fuzz:
            return await ctx.send(f"❌ Player `{search_query}` not found in the database.")

        if len(subs) == 1 and not fuzz:
            return await send_player_profile(FakeInteraction(), subs[0], show_ratings)

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
        valid_archs = ["Aggressor", "Anchor", "Finisher", "Standard"]
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
        valid_archs = ["Aggressor", "Anchor", "Finisher", "Standard"]
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

    @commands.command(name="sync_csv", aliases=["scsv"], help="[OWNER] Sync players from players_master.csv to DB.\nUsage: sync_csv")
    async def sync_csv(self, ctx):
        if ctx.author.id != ADMIN_DISCORD_ID:
            return await ctx.send("❌ Owner only.")
        if not os.path.exists("players_master.csv"):
            return await ctx.send("❌ `players_master.csv` not found.")
        try:
            new_players = []
            with open("players_master.csv", "r", encoding="utf-8-sig") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    new_players.append({
                        "name": row["Name"].strip(),
                        "bat": int(row["Bat"]),
                        "bowl": int(row["Bowl"]),
                        "role": row["Role"].strip(),
                        "archetype": row["Archetype"].strip()
                    })
            added_count = add_players_bulk(new_players)
            if added_count > 0:
                await ctx.send(f"✅ Sync complete! Added **{added_count}** new players.")
                await log_db_update("CSV Sync", "Batch Import", ctx.author, f"Added {added_count} new players from CSV.")
            else:
                await ctx.send("✅ Sync complete! No new players found (database already up to date).")
        except Exception as e:
            await ctx.send(f"❌ Error during sync: {e}")

    @commands.command(name="set_user_tier", aliases=["sut"], help="[OWNER] Assign subscription tier to a user.\nUsage: set_user_tier @user <tier>\nTiers: Basic, Standard, Single, Server Pro, None")
    async def set_user_tier(self, ctx, user: discord.Member, *, tier: str):
        if ctx.author.id != ADMIN_DISCORD_ID:
            return await ctx.send("❌ Owner only.")
        valid = {
            "Basic": "Basic (1 Sim/Day | T20/ODI)",
            "Standard": "Standard (1 Sim/Day | All)",
            "Single": "Single (1 Match Consumable)",
            "Server Pro": "Server Pro (Unlimited on Silver/Diamond)",
            "None": "None (Remove)"
        }
        tier = tier.strip()
        if tier not in valid:
            return await ctx.send(f"❌ Invalid tier. Choose from: {', '.join(valid.keys())}")
        msg = update_user_tier(str(user.id), tier, valid[tier], user.mention)
        await ctx.send(msg)

    @commands.command(name="set_server_tier", aliases=["sst"], help="[OWNER] Assign subscription tier to a server.\nUsage: set_server_tier <server_id> <tier>\nTiers: Bronze, Silver, Gold, Diamond, None")
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
        tier = tier.strip()
        if tier not in valid:
            return await ctx.send(f"❌ Invalid tier. Choose from: {', '.join(valid.keys())}")
        msg = update_server_tier(server_id, tier, valid[tier])
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

        if team_name:
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

    @commands.group(name="tournament", aliases=["t"], invoke_without_command=True, help="Main command for tournaments.\nUsage: tournament")
    async def tournament(self, ctx):
        await ctx.send_help(ctx.command)

    @tournament.command(name="create", help="[ADMIN] Create a new tournament.\nUsage: tournament create \"<name>\" <format> [impact_player=true/false] [injuries=true/false]")
    async def t_create(self, ctx, name: str, format_str: str, *options: str):
        kwargs = { 'impact_player': False, 'injuries': False }
        for opt in options:
            try:
                key, value = opt.split('=', 1)
                if key == 'impact_player': kwargs['impact_player'] = to_bool(value)
                elif key == 'injuries': kwargs['injuries'] = to_bool(value)
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
        }
        save_tournament(t_data)
        await ctx.send(f"🏆 **Tournament Created:** `{name}`\nUse `cv tournament add_team` to get started!")

    @tournament.command(name="add_team", help="[MANAGER] Add a team and assign an Owner.\nUsage: tournament add_team \"<team_name>\" <@owner>")
    async def t_add_team(self, ctx, team_name: str, owner: discord.Member):
        server_id = str(ctx.guild.id)
        tourney = get_server_tournament(server_id)
        
        is_mgr = (ctx.author.id == ADMIN_DISCORD_ID) or (ctx.author.guild_permissions.administrator) or (str(ctx.author.id) in tourney.get("managers", []))

        if not tourney: return await ctx.send("❌ No tournament exists.")
        if not is_mgr: return await ctx.send("❌ Managers only.")
        if tourney["status"] != "registration": return await ctx.send("❌ Cannot add teams after tournament has started.")
        
        if any(t["name"].lower() == team_name.lower() for t in tourney["teams"]):
            return await ctx.send("❌ Team name already exists.")
        if any(t["owner_id"] == str(owner.id) for t in tourney["teams"]):
            return await ctx.send(f"❌ {owner.mention} already owns a team.")
                
        tourney["teams"].append({"name": team_name, "owner_id": str(owner.id), "squad": []})
        save_tournament(tourney)
        await ctx.send(f"✅ Team **{team_name}** added! Owner: {owner.mention}")

    @tournament.command(name="replace_player", help="[MANAGER] Replace a player in a team's squad.\nUsage: tournament replace_player \"<team>\" \"<out_player>\" \"<in_player>\"")
    async def t_replace_player(self, ctx, team_name: str, out_player: str, in_player: str):
        server_id = str(ctx.guild.id)
        tourney = get_server_tournament(server_id)
        
        is_mgr = (ctx.author.id == ADMIN_DISCORD_ID) or (ctx.author.guild_permissions.administrator) or (tourney and str(ctx.author.id) in tourney.get("managers", []))
        if not tourney: return await ctx.send("❌ No tournament exists.")
        if not is_mgr: return await ctx.send("❌ Managers only.")
        
        team = next((t for t in tourney["teams"] if t["name"].lower() == team_name.lower()), None)
        if not team: return await ctx.send(f"❌ Team '{team_name}' not found.")
        if not team.get("squad"): return await ctx.send(f"❌ Team '{team_name}' has no squad submitted yet.")
            
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

    @tournament.command(name="submit_squad", help="[OWNER/MANAGER] Submit a tournament squad (15 players).\nUsage: tournament submit_squad [team_name]")
    async def t_submit_squad(self, ctx, *, team_name: str = None):
        server_id = str(ctx.guild.id)
        tourney = get_server_tournament(server_id)
        
        if not tourney: return await ctx.send("❌ No tournament exists.")
        if tourney["status"] != "registration": return await ctx.send("❌ Registration is closed.")
        
        is_mgr = (ctx.author.id == ADMIN_DISCORD_ID) or (ctx.author.guild_permissions.administrator) or (str(ctx.author.id) in tourney.get("managers", []))
        
        if team_name:
            if not is_mgr:
                return await ctx.send("❌ Only Managers can use the team_name parameter to submit for others.")
            team = next((t for t in tourney["teams"] if t["name"].lower() == team_name.lower()), None)
            if not team: return await ctx.send(f"❌ Team '{team_name}' not found.")
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
            else:
                missing.append(line)
                
        if missing or len(found_players) < min_s:
            err = f"❌ **Roster Invalid ({len(found_players)}/{min_s} Minimum Found)**\n"
            if missing: err += f"Missing: {', '.join(missing)}\n"
            err += "Please fix the names and try again."
            return await ctx.send(err)
            
        team["squad"] = found_players
        save_tournament(tourney)
        await ctx.send(f"✅ **Squad Verified and Saved for {team['name']}!**\nRegistered {len(found_players)} players.")

    @tournament.command(name="squad", help="View a team's tournament squad and player ratings.\nUsage: tournament squad [team_name]")
    async def t_squad(self, ctx, *, team_name: str = None):
        server_id = str(ctx.guild.id)
        tourney = get_server_tournament(server_id)
        if not tourney: return await ctx.send("❌ No tournament exists.")
        
        if team_name:
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
            await ctx.send(embed=embed, file=discord.File("t20_banner.png"))
        else:
            await ctx.send(embed=embed)

    @tournament.command(name="start", help="[MANAGER] Lock registration and generate schedule.\nUsage: tournament start")
    async def t_start(self, ctx):
        server_id = str(ctx.guild.id)
        tourney = get_server_tournament(server_id)

        is_mgr = (ctx.author.id == ADMIN_DISCORD_ID) or (ctx.author.guild_permissions.administrator) or (tourney and str(ctx.author.id) in tourney.get("managers", []))
        if not tourney: return await ctx.send("❌ No tournament exists.")
        if not is_mgr: return await ctx.send("❌ Managers only.")
        if tourney["status"] != "registration": return await ctx.send("❌ Tournament already started.")

        min_s = tourney.get("min_squad", 11)
        t_type = tourney.get("tournament_type", "round_robin")

        if t_type == "t20_world_cup":
            for grp in ["A", "B", "C", "D"]:
                grp_teams = [t for t in tourney["teams"] if t.get("group") == grp]
                if len(grp_teams) != 4:
                    return await ctx.send(f"❌ Group **{grp}** needs exactly 4 teams (currently has {len(grp_teams)}).")
            for t in tourney["teams"]:
                if len(t.get("squad", [])) < min_s:
                    return await ctx.send(f"❌ Team **{t['name']}** does not have a valid squad yet.")

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
            save_tournament(tourney)
            groups_txt = "\n".join(f"**Group {g}:** {' · '.join(teams_by_group[g])}" for g in "ABCD")
            return await ctx.send(f"🏆 **TOURNAMENT STARTED: {tourney['name']}!** — T20 World Cup\n{groups_txt}\nGenerated **{len(schedule)} group stage matches** (interleaved). Use `cv tournament status` to view fixtures!")

        # Round Robin
        if len(tourney["teams"]) < 2:
            return await ctx.send("❌ Need at least 2 teams.")
        for t in tourney["teams"]:
            if len(t.get("squad", [])) < min_s:
                return await ctx.send(f"❌ Team **{t['name']}** does not have a valid squad yet.")

        teams = [t["name"] for t in tourney["teams"]]
        if len(teams) % 2 != 0:
            teams.append("BYE")
        n = len(teams)
        matchups = []
        for r in range(n - 1):
            round_matches = []
            for i in range(n // 2):
                t1, t2 = teams[i], teams[n - 1 - i]
                if t1 != "BYE" and t2 != "BYE":
                    round_matches.append((t1, t2) if r % 2 == 0 else (t2, t1))
            random.shuffle(round_matches)
            for m in round_matches:
                matchups.append({"round": r + 1, "team1": m[0], "team2": m[1]})
            teams.insert(1, teams.pop())
        schedule = [{"match_id": i + 1, "round": m["round"], "team1": m["team1"], "team2": m["team2"], "status": "pending", "result": None} for i, m in enumerate(matchups)]
        tourney["schedule"] = schedule
        tourney["status"] = "active"
        tourney["current_match_idx"] = 0
        save_tournament(tourney)
        await ctx.send(f"🏆 **TOURNAMENT STARTED: {tourney['name']}!**\nGenerated **{len(schedule)} matches** in the Round Robin stage.\nUse `cv tournament status` to view it!")

    @tournament.command(name="status", help="View the current tournament schedule and standings.\nUsage: tournament status")
    async def t_status(self, ctx):
        server_id = str(ctx.guild.id)
        tourney = get_server_tournament(server_id)
        if not tourney:
            return await ctx.send("❌ No tournament exists in this server.")

        if tourney["status"] == "registration":
            t_type = tourney.get("tournament_type", "round_robin")
            type_label = "T20 World Cup" if t_type == "t20_world_cup" else "Round Robin"
            embed = discord.Embed(title=f"🏆 {tourney['name']}", color=discord.Color.gold())
            embed.description = f"📝 **Registration Phase** · {type_label}"
            team_lines = []
            for t in tourney["teams"]:
                grp = f" · Group **{t['group']}**" if t.get("group") else ""
                team_lines.append(f"• **{t['name']}**{grp} (<@{t['owner_id']}>) — {len(t.get('squad', []))}/{tourney.get('max_squad', 15)} players")
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
                return await ctx.send(embed=embed, file=discord.File("t20_banner.png"))
            return await ctx.send(embed=embed)

        t_type = tourney.get("tournament_type", "round_robin")
        if t_type == "t20_world_cup":
            pages = _build_flat_pages(tourney)
            hint = "Use `cvt groups` to view fixtures by group."
        else:
            pages = _build_status_pages(tourney)
            hint = None

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
            await ctx.send(embed=embed, view=view, file=discord.File("t20_banner.png"))
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
        if tourney.get("tournament_type") != "t20_world_cup":
            return await ctx.send("❌ This command is only available for T20 World Cup tournaments.")
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
        await ctx.send(embed=embed, view=view, file=discord.File("t20_banner.png"))

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

        # Build lookup: frozenset of lowercased names → match entry
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

    @tournament.command(name="play_next", help="[MANAGER] Launch the next pending tournament match.\nUsage: tournament play_next")
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
            
        r_label = f"Round {current_round}" if isinstance(current_round, int) else current_round
        await ctx.send(f"🚀 **Launching {r_label} — Match {pending['match_id']}...**")
        self.bot.dispatch("start_tournament_match", ctx.channel, ctx.author.id, tourney, pending)

    @tournament.command(name="play", help="[MANAGER] Launch a specific tournament match by its ID.\nUsage: tournament play <match_id>")
    async def t_play_match(self, ctx, match_id: int):
        server_id = str(ctx.guild.id)
        tourney = get_server_tournament(server_id)
        
        is_mgr = (ctx.author.id == ADMIN_DISCORD_ID) or (ctx.author.guild_permissions.administrator) or (tourney and str(ctx.author.id) in tourney.get("managers", []))
        if not tourney: return await ctx.send("❌ No tournament exists.")
        if not is_mgr: return await ctx.send("❌ Managers only.")
        if tourney["status"] != "active": return await ctx.send("❌ Tournament is not active.")
        
        match = next((m for m in tourney.get("schedule", []) if m["match_id"] == match_id), None)
        if not match:
            return await ctx.send(f"❌ Match ID {match_id} does not exist.")
        if match["status"] != "pending":
            return await ctx.send(f"❌ Match {match_id} is already completed.")
            
        r_label = f"Round {match['round']}" if isinstance(match['round'], int) else match['round']
        await ctx.send(f"🚀 **Manually Launching Match {match['match_id']} ({r_label})...**")
        self.bot.dispatch("start_tournament_match", ctx.channel, ctx.author.id, tourney, match)

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
            
        match_data["status"] = "completed"
        match_data["result"] = {
            "winner": winner_team,
            "format_overs": tourney.get("format_overs", 20),
            "t1_runs": t1_runs, "t1_wickets": t1_wkts, "t1_balls": t1_balls,
            "t2_runs": t2_runs, "t2_wickets": t2_wkts, "t2_balls": t2_balls
        }
        tourney["current_match_idx"] += 1
        save_tournament(tourney)
        await ctx.send(f"✅ **Match {match_id} forcefully completed!**\nWinner: **{winner_team}**\nPoints Table and NRR updated.")

    @tournament.command(name="generate_knockouts", help="[MANAGER] Generate Knockouts (Semi-Finals) for Top 4 teams.\nUsage: tournament generate_knockouts")
    async def t_generate_knockouts(self, ctx):
        server_id = str(ctx.guild.id)
        tourney = get_server_tournament(server_id)
        is_mgr = (ctx.author.id == ADMIN_DISCORD_ID) or (ctx.author.guild_permissions.administrator) or (tourney and str(ctx.author.id) in tourney.get("managers", []))
        if not tourney: return await ctx.send("❌ No tournament exists.")
        if not is_mgr: return await ctx.send("❌ Managers only.")
        if tourney["status"] != "active": return await ctx.send("❌ Tournament is not active.")
        
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
        
        sf1 = {"match_id": len(tourney["schedule"]) + 1, "round": "Semi-Final 1", "stage": "knockout", "team1": top4[0], "team2": top4[3], "status": "pending", "result": None}
        sf2 = {"match_id": len(tourney["schedule"]) + 2, "round": "Semi-Final 2", "stage": "knockout", "team1": top4[1], "team2": top4[2], "status": "pending", "result": None}
        
        tourney["schedule"].extend([sf1, sf2])
        save_tournament(tourney)
        
        await ctx.send(f"🔥 **Knockout Stage Set!**\n**Semi-Final 1:** {top4[0]} vs {top4[3]}\n**Semi-Final 2:** {top4[1]} vs {top4[2]}\n\nUse `cv tournament play_next` to begin!")

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

        pending = [m for m in tourney.get("schedule", []) if m["status"] == "pending"]
        if not pending:
            return await ctx.send("✅ No pending matches — tournament is fully simulated!")

        _PITCHES = ["Flat", "Green Seamer", "Dry", "Dusty", "Spin-Friendly", "Bouncy", "Hard"]
        status_msg = await ctx.send(f"⚡ **Simulating {len(pending)} pending match(es)...**")
        results = []

        for m_data in pending:
            t1_data = next((t for t in tourney["teams"] if t["name"] == m_data["team1"]), None)
            t2_data = next((t for t in tourney["teams"] if t["name"] == m_data["team2"]), None)
            r_label = f"R{m_data['round']}" if isinstance(m_data['round'], int) else m_data['round']

            if not t1_data or not t2_data:
                results.append(f"M{m_data['match_id']} ({r_label}): ❌ Team not found")
                continue
            s1 = t1_data.get("squad", [])
            s2 = t2_data.get("squad", [])
            if len(s1) < 2 or len(s2) < 2:
                results.append(f"M{m_data['match_id']} ({r_label}): ❌ Squad not set")
                continue

            roster1 = s1[:11]
            roster2 = s2[:11]
            pitch = random.choice(_PITCHES)
            t1 = {"name": m_data["team1"], "players": roster1, "color": t1_data.get("color", "#6B7280")}
            t2 = {"name": m_data["team2"], "players": roster2, "color": t2_data.get("color", "#6B7280")}

            match = CricketMatch(None, None, 0, 0, t1, t2, tourney.get("format_overs", 20), pitch, "Clear")
            match.tournament_server_id = tourney["server_id"]
            match.tournament_match_id = m_data["match_id"]
            match.manager_id = ctx.author.id
            match.tournament_name = tourney["name"]
            match.sim_only = True
            match._scorecard_players = None

            t_bat, t_bowl = (t1, t2) if random.random() < 0.5 else (t2, t1)
            match.innings1 = InningsState(t_bat, t_bowl)

            try:
                await asyncio.to_thread(_run_full_match_sync, match)
            except Exception as e:
                results.append(f"M{m_data['match_id']} ({r_label}): ❌ Error: {e}")
                continue

            # Trigger the existing stats + progression listener directly (sequential, no race)
            from tournament_manager import TournamentCog as _TC
            tc = self.bot.cogs.get("TournamentCog")
            if tc:
                await tc.on_tournament_match_complete(match)
            else:
                self.bot.dispatch("tournament_match_complete", match)
                await asyncio.sleep(0.5)

            # tourney dict has been updated in-place by the listener; re-fetch for fresh ref
            tourney = get_server_tournament(server_id)

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
                f"— **{win_str}**"
            )

        lines = "\n".join(results)
        await status_msg.edit(content=f"✅ **Simulation Complete! ({len(results)} matches)**\n{lines}")

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
        idx = next((i for i, t in enumerate(tourney["teams"]) if t["name"].lower() == team_name.lower()), None)
        if idx is None: return await ctx.send(f"❌ Team **{team_name}** not found.")
        del tourney["teams"][idx]
        save_tournament(tourney)
        await ctx.send(f"✅ Team **{team_name}** removed.")

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

    @tournament.command(name="force_delete", help="[ADMIN] Forcefully delete this server's tournament.\nUsage: tournament force_delete")
    async def t_force_delete(self, ctx):
        if not ctx.author.guild_permissions.administrator and ctx.author.id != ADMIN_DISCORD_ID:
            return await ctx.send("❌ Server Admins only.")
        server_id = str(ctx.guild.id)
        tourney = get_server_tournament(server_id)
        if not tourney: return await ctx.send("❌ No tournament exists.")
        from subscription_manager import DB_CACHE
        DB_CACHE["tournaments"].pop(server_id, None)
        save_tournament_data_to_bin()
        await ctx.send("🗑️ Tournament deleted.")

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
        team = next((t for t in tourney["teams"] if t["name"].lower() == team_name.lower()), None)
        if not team: return await ctx.send(f"❌ Team **{team_name}** not found.")
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
        team = next((t for t in tourney["teams"] if t["name"].lower() == team_name.lower()), None)
        if not team:
            return await ctx.send(f"❌ Team **{team_name}** not found.")
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

    @tournament.command(name="remove_injury", help="[MANAGER] Manually clear a player's injury.\nUsage: cvt remove_injury \"<team_name>\" \"<player_name>\"")
    async def t_remove_injury(self, ctx, team_name: str, player_name: str):
        server_id = str(ctx.guild.id)
        tourney = get_server_tournament(server_id)
        if not tourney: return await ctx.send("❌ No tournament exists.")
        is_mgr = (ctx.author.id == ADMIN_DISCORD_ID) or ctx.author.guild_permissions.administrator or (str(ctx.author.id) in tourney.get("managers", []))
        if not is_mgr: return await ctx.send("❌ Managers only.")
        team = next((t for t in tourney["teams"] if t["name"].lower() == team_name.lower()), None)
        if not team: return await ctx.send(f"❌ Team **{team_name}** not found.")
        player = next((p for p in team.get("squad", []) if p["name"].lower() == player_name.lower()), None)
        if not player: return await ctx.send(f"❌ Player **{player_name}** not found in **{team['name']}**.")
        if not player.get("injured"): return await ctx.send(f"ℹ️ **{player['name']}** is not currently injured.")
        player.pop("injured", None)
        player.pop("injury_until_match", None)
        player.pop("injury_severity", None)
        tourney["pending_injury_news"] = [
            n for n in tourney.get("pending_injury_news", [])
            if not (n["team"] == team["name"] and n["player"] == player["name"])
        ]
        save_tournament(tourney)
        await ctx.send(f"✅ Injury cleared for **{player['name']}** ({team['name']}).")

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
            try:
                img_buf = generate_scorecard_from_data(full_data)
                file = discord.File(fp=img_buf, filename=f"scorecard_m{match_id}.png")
                await ctx.send(embed=embed, file=file)
                return
            except Exception as _e:
                print(f"⚠️ Scorecard regeneration failed for match {match_id}: {_e}")
        embed.add_field(name="No image", value="No scorecard data for this match.", inline=False)
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

        t_type = tourney.get("tournament_type", "round_robin")
        min_s  = tourney.get("min_squad", 11)
        max_s  = tourney.get("max_squad", 15)

        def make_squad():
            shuffled = list(db_players)
            random.shuffle(shuffled)
            return shuffled[:min(max_s, len(shuffled))]

        if t_type == "t20_world_cup":
            team_config = [
                ("India", "A"), ("Pakistan", "A"), ("Australia", "A"), ("England", "A"),
                ("New Zealand", "B"), ("South Africa", "B"), ("West Indies", "B"), ("Sri Lanka", "B"),
                ("Bangladesh", "C"), ("Afghanistan", "C"), ("Zimbabwe", "C"), ("Ireland", "C"),
                ("Scotland", "D"), ("Netherlands", "D"), ("Namibia", "D"), ("Uganda", "D"),
            ]
        else:
            team_config = [
                ("Thunder Kings", None), ("Lightning Bolts", None), ("Storm Riders", None),
                ("Fire Hawks", None), ("Ice Wolves", None), ("Steel Giants", None),
                ("Golden Lions", None), ("Silver Eagles", None),
            ]

        tourney["teams"] = []
        for name, grp in team_config:
            tourney["teams"].append({"name": name, "owner_id": str(ctx.author.id), "squad": make_squad(), "group": grp})

        should_start = auto_start.lower() not in ("no", "false", "0")
        if not should_start:
            save_tournament(tourney)
            return await ctx.send(f"✅ **Dev Setup Complete!** Added {len(tourney['teams'])} teams. Run `cv tournament start` when ready.")

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
            save_tournament(tourney)
            await ctx.send(
                f"⚡ **Dev Setup + Auto-Start!** — T20 World Cup\n"
                f"**A:** India · Pakistan · Australia · England\n"
                f"**B:** New Zealand · South Africa · West Indies · Sri Lanka\n"
                f"**C:** Bangladesh · Afghanistan · Zimbabwe · Ireland\n"
                f"**D:** Scotland · Netherlands · Namibia · Uganda\n"
                f"Generated **{len(schedule)} group stage matches**. Use `cv tournament play_next` to begin!"
            )
        else:
            teams = [t["name"] for t in tourney["teams"]]
            if len(teams) % 2 != 0:
                teams.append("BYE")
            n = len(teams)
            matchups = []
            for r in range(n - 1):
                round_matches = []
                for i in range(n // 2):
                    t1, t2 = teams[i], teams[n - 1 - i]
                    if t1 != "BYE" and t2 != "BYE":
                        round_matches.append((t1, t2) if r % 2 == 0 else (t2, t1))
                random.shuffle(round_matches)
                for m in round_matches:
                    matchups.append({"round": r + 1, "team1": m[0], "team2": m[1]})
                teams.insert(1, teams.pop())
            schedule = [{"match_id": i + 1, "round": m["round"], "team1": m["team1"], "team2": m["team2"], "status": "pending", "result": None} for i, m in enumerate(matchups)]
            tourney["schedule"] = schedule
            tourney["status"] = "active"
            tourney["current_match_idx"] = 0
            save_tournament(tourney)
            await ctx.send(
                f"⚡ **Dev Setup + Auto-Start!** — Round Robin\n"
                f"**Teams:** {' · '.join(t['name'] for t in tourney['teams'])}\n"
                f"Generated **{len(schedule)} matches**. Use `cv tournament play_next` to begin!"
            )

    @tournament.command(name="next_match", help="[OWNER] Launch your team's next pending match.\nUsage: tournament next_match")
    async def t_next_match(self, ctx):
        server_id = str(ctx.guild.id)
        tourney = get_server_tournament(server_id)
        if not tourney: return await ctx.send("❌ No tournament exists.")
        if tourney["status"] != "active": return await ctx.send("❌ Tournament is not active.")
        my_team = next((t for t in tourney["teams"] if t["owner_id"] == str(ctx.author.id)), None)
        if not my_team: return await ctx.send("❌ You are not a Team Owner in this tournament.")
        my_matches = [m for m in tourney.get("schedule", []) if m["status"] == "pending" and (m["team1"] == my_team["name"] or m["team2"] == my_team["name"])]
        if not my_matches: return await ctx.send(f"✅ **{my_team['name']}** has no pending matches right now!")
        match = my_matches[0]
        r_label = f"Round {match['round']}" if isinstance(match['round'], int) else match['round']
        await ctx.send(f"🚀 **Launching Match {match['match_id']} ({r_label})...**")
        self.bot.dispatch("start_tournament_match", ctx.channel, ctx.author.id, tourney, match)

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
        t1_name, t2_name = m_data["team1"], m_data["team2"]
        winner_clean = winner.strip()
        if winner_clean not in (t1_name, t2_name, "TIE"):
            return await ctx.send(f"❌ Winner must be **{t1_name}**, **{t2_name}**, or **TIE**.")
        m_data["status"] = "completed"
        m_data["result"] = {
            "winner": winner_clean, "format_overs": tourney.get("format_overs", 20),
            "t1_runs": t1_runs, "t1_wickets": t1_wickets, "t1_balls": t1_balls,
            "t2_runs": t2_runs, "t2_wickets": t2_wickets, "t2_balls": t2_balls,
        }
        tourney["current_match_idx"] = tourney.get("current_match_idx", 0) + 1
        sf1 = next((m for m in tourney["schedule"] if m["round"] == "Semi-Final 1"), None)
        sf2 = next((m for m in tourney["schedule"] if m["round"] == "Semi-Final 2"), None)
        if sf1 and sf2 and sf1["status"] == "completed" and sf2["status"] == "completed":
            if not any(m["round"] == "Final" for m in tourney["schedule"]):
                tourney["schedule"].append({"match_id": len(tourney["schedule"]) + 1, "round": "Final", "stage": "knockout", "team1": sf1["result"]["winner"], "team2": sf2["result"]["winner"], "status": "pending", "result": None})
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
        if len(teams) % 2 != 0: teams.append("BYE")
        n = len(teams); matchups = []
        for r in range(n - 1):
            for i in range(n // 2):
                t1, t2 = teams[i], teams[n - 1 - i]
                if t1 != "BYE" and t2 != "BYE":
                    matchups.append({"round": r + 1, "team1": t1 if r % 2 == 0 else t2, "team2": t2 if r % 2 == 0 else t1})
            teams.insert(1, teams.pop())
        schedule = [{"match_id": i + 1, "round": m["round"], "team1": m["team1"], "team2": m["team2"], "status": "pending", "result": None} for i, m in enumerate(matchups)]
        tourney["schedule"] = schedule; tourney["status"] = "active"; tourney["current_match_idx"] = 0
        save_tournament(tourney)
        r1 = [m for m in schedule if m["round"] == 1]
        preview = "**Round 1:**\n" + "\n".join(f"  Match {m['match_id']}: {m['team1']} vs {m['team2']}" for m in r1)
        await ctx.send(embed=discord.Embed(title=f"✅ Schedule Restored — {tourney['name']}", description=preview, color=discord.Color.green()))

    @tournament.command(name="leaderboard", help="View the tournament leaderboard.\nUsage: tournament leaderboard <runs|wickets|sr|bat_avg|fours|sixes|fifties|hundreds|econ|bowl_avg>")
    async def t_leaderboard(self, ctx, category: str = "runs"):
        server_id = str(ctx.guild.id)
        tourney = get_server_tournament(server_id)
        if not tourney: return await ctx.send("❌ No tournament exists.")
        valid_cats = {"runs", "wickets", "sr", "bat_avg", "fours", "sixes", "fifties", "hundreds", "econ", "bowl_avg"}
        c_val = category.lower()
        if c_val not in valid_cats:
            return await ctx.send(f"❌ Invalid category. Choose from: {', '.join(sorted(valid_cats))}")
        all_players = [{"name": p_name, "team": t_name, "stats": stats} for t_name, players in tourney.get("stats", {}).items() for p_name, stats in players.items()]
        if not all_players: return await ctx.send("❌ No stats yet. Complete a match first!")
        if c_val == "runs": sp = sorted(all_players, key=lambda x: x["stats"]["runs"], reverse=True)
        elif c_val == "wickets": sp = sorted(all_players, key=lambda x: x["stats"]["wickets"], reverse=True)
        elif c_val == "sr": sp = sorted([p for p in all_players if p["stats"]["runs"] >= 50], key=lambda x: (x["stats"]["runs"]/x["stats"]["balls_faced"]*100) if x["stats"]["balls_faced"] > 0 else 0, reverse=True)
        elif c_val == "bat_avg": sp = sorted([p for p in all_players if p["stats"]["runs"] >= 50], key=lambda x: x["stats"]["runs"]/max(1, x["stats"]["outs"]), reverse=True)
        elif c_val in {"fours", "sixes", "fifties", "hundreds"}: sp = sorted(all_players, key=lambda x: x["stats"][c_val], reverse=True)
        elif c_val == "econ": sp = sorted([p for p in all_players if p["stats"]["balls_bowled"] >= 30], key=lambda x: (x["stats"]["runs_conceded"]/x["stats"]["balls_bowled"]*6) if x["stats"]["balls_bowled"] > 0 else 999)
        elif c_val == "bowl_avg": sp = sorted([p for p in all_players if p["stats"]["wickets"] >= 3], key=lambda x: x["stats"]["runs_conceded"]/x["stats"]["wickets"] if x["stats"]["wickets"] > 0 else 999)
        else: sp = []
        cat_labels = {"runs":"Most Runs","wickets":"Most Wickets","sr":"Best Strike Rate","bat_avg":"Best Batting Avg","fours":"Most Fours","sixes":"Most Sixes","fifties":"Most 50s","hundreds":"Most 100s","econ":"Best Economy","bowl_avg":"Best Bowling Avg"}
        embed = discord.Embed(title=f"🏆 Leaderboard: {cat_labels.get(c_val, c_val)}", color=discord.Color.gold())
        lines = []
        for i, p in enumerate(sp[:10], 1):
            s = p["stats"]
            if c_val == "runs": val = f"**{s['runs']}** runs"
            elif c_val == "wickets": val = f"**{s['wickets']}** wkts"
            elif c_val == "sr": val = f"**{(s['runs']/s['balls_faced']*100):.1f}** SR" if s['balls_faced'] > 0 else "N/A"
            elif c_val == "bat_avg": val = f"**{s['runs']/max(1,s['outs']):.1f}** avg"
            elif c_val in {"fours","sixes","fifties","hundreds"}: val = f"**{s[c_val]}**"
            elif c_val == "econ": val = f"**{(s['runs_conceded']/s['balls_bowled']*6):.1f}** econ" if s['balls_bowled'] > 0 else "N/A"
            elif c_val == "bowl_avg": val = f"**{s['runs_conceded']/s['wickets']:.1f}** avg" if s['wickets'] > 0 else "N/A"
            else: val = ""
            lines.append(f"`{i:>2}.` **{p['name']}** ({p['team']}) — {val}")
        embed.description = "\n".join(lines) if lines else "No players qualify yet."
        await ctx.send(embed=embed)

    @tournament.command(name="player_stats", help="View a specific player's tournament stats.\nUsage: tournament player_stats \"<team>\" \"<player>\"")
    async def t_player_stats(self, ctx, team_name: str, *, player_name: str):
        server_id = str(ctx.guild.id)
        tourney = get_server_tournament(server_id)
        if not tourney: return await ctx.send("❌ No tournament exists.")
        t_match = next((t for t in tourney.get("stats", {}).keys() if t.lower() == team_name.lower()), None)
        if not t_match: return await ctx.send(f"❌ Team '{team_name}' not found or hasn't played yet.")
        p_match = next((p for p in tourney["stats"][t_match].keys() if p.lower() == player_name.lower()), None)
        if not p_match:
            close = difflib.get_close_matches(player_name, list(tourney["stats"][t_match].keys()), n=1, cutoff=0.5)
            if close: p_match = close[0]
            else: return await ctx.send(f"❌ Player '{player_name}' not found in team '{t_match}'.")
        stats = tourney["stats"][t_match][p_match]
        sr = (stats["runs"]/stats["balls_faced"]*100) if stats["balls_faced"] > 0 else 0.0
        bat_avg = stats["runs"]/stats["outs"] if stats["outs"] > 0 else float(stats["runs"])
        bowl_avg = stats["runs_conceded"]/stats["wickets"] if stats["wickets"] > 0 else 0.0
        econ = (stats["runs_conceded"]/stats["balls_bowled"]*6) if stats["balls_bowled"] > 0 else 0.0
        embed = discord.Embed(title=f"📊 {p_match} — {t_match}", description=f"Matches: {stats['matches']}", color=discord.Color.blue())
        embed.add_field(name="🏏 Batting", value=f"**Runs:** {stats['runs']}\n**SR:** {sr:.1f}\n**Avg:** {bat_avg:.1f}\n**4s:** {stats['fours']} | **6s:** {stats['sixes']}\n**50s:** {stats['fifties']} | **100s:** {stats['hundreds']}", inline=True)
        embed.add_field(name="🎯 Bowling", value=f"**Wkts:** {stats['wickets']}\n**Econ:** {econ:.1f}\n**Avg:** {bowl_avg:.1f}\n**Overs:** {stats['balls_bowled']//6}.{stats['balls_bowled']%6}", inline=True)
        await ctx.send(embed=embed)

    @tournament.command(name="help_guide", help="Show the tournament commands guide.\nUsage: tournament help_guide")
    async def t_help_guide(self, ctx):
        embed = discord.Embed(title="🏆 Tournament Commands (cv prefix)", color=discord.Color.gold())
        embed.add_field(name="🛠️ Setup", value="`create` `add_manager` `add_team` `remove_team` `submit_squad` `start` `force_delete`", inline=False)
        embed.add_field(name="🏏 Playing", value="`play <id>` `play_next` `next_match`", inline=False)
        embed.add_field(name="📊 Stats & Standings", value="`status` `standings` `leaderboard <cat>` `player_stats <team> <player>` `squad` `match_scorecard <id>`", inline=False)
        embed.add_field(name="⚙️ Admin", value="`set_theme` `set_team_color` `replace_player` `admin_record_result` `admin_restore_schedule` `admin_force_restore_schedule` `force_result`", inline=False)
        embed.set_footer(text="All commands start with: cv tournament ...")
        await ctx.send(embed=embed)

    @tournament.command(name="standings", help="View the Tournament Points Table & NRR.\nUsage: tournament standings")
    async def t_standings(self, ctx):
        server_id = str(ctx.guild.id)
        tourney = get_server_tournament(server_id)
        if not tourney: return await ctx.send("❌ No tournament exists.")

        # T20 World Cup standings
        if tourney.get("tournament_type") == "t20_world_cup":
            schedule       = tourney.get("schedule", [])
            super8_matches = [m for m in schedule if m.get("stage") == "super8"]
            ko_matches     = [m for m in schedule if m.get("stage") == "knockout"]

            if not super8_matches and not ko_matches:
                # Group stage only — single image, no navigation needed
                try:
                    buf = generate_t20wc_points_table(tourney)
                    return await ctx.send(file=discord.File(fp=buf, filename="points_table.png"))
                except Exception as e:
                    print(f"⚠️ Points table image failed: {e}")

            # Build available pages
            pages = []
            try:
                s16_buf = generate_t20wc_points_table(tourney)
                pages.append(("Group Stage", "points_table.png", s16_buf))
            except Exception as e:
                print(f"⚠️ Super16 table failed: {e}")
            if super8_matches:
                try:
                    s8_buf = generate_t20wc_super8_table(tourney)
                    pages.append(("Super 8", "super8_table.png", s8_buf))
                except Exception as e:
                    print(f"⚠️ Super8 table failed: {e}")
            if ko_matches:
                try:
                    ko_buf = generate_t20wc_knockouts_image(tourney)
                    if ko_buf:
                        pages.append(("Knockouts", "knockouts.png", ko_buf))
                except Exception as e:
                    print(f"⚠️ Knockouts image failed: {e}")

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

            # Both images failed — text embed fallback
            from tournament_manager import get_group_standings
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

        standings = get_tournament_standings(tourney)
        theme = tourney.get("theme", "Default")
        
        if theme == "Crimson Cricket":
            try:
                img = Image.open("points_table_crimson.png").convert("RGB")
                d = ImageDraw.Draw(img)
                font_row = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 26)
                def get_tw(text, font): return font.getbbox(text)[2] if hasattr(font, 'getbbox') else len(text) * 12
                
                start_y, row_height, c_text = 275, 38, "#FFFFFF"
                cols = {"TEAM": 140, "P": 445, "W": 555, "L": 665, "NR": 775, "PTS": 885, "NRR": 995}
                y = start_y
                for i, (t_name, data) in enumerate(standings, 1):
                    if i > 10: break
                    d.text((cols["TEAM"], y + 8), t_name[:20].upper(), fill=c_text, font=font_row)
                    d.text((cols["P"] - (get_tw(str(data['P']), font_row)/2), y + 8), str(data['P']), fill=c_text, font=font_row)
                    d.text((cols["W"] - (get_tw(str(data['W']), font_row)/2), y + 8), str(data['W']), fill=c_text, font=font_row)
                    d.text((cols["L"] - (get_tw(str(data['L']), font_row)/2), y + 8), str(data['L']), fill=c_text, font=font_row)
                    d.text((cols["NR"] - (get_tw(str(data['T']), font_row)/2), y + 8), str(data['T']), fill=c_text, font=font_row)
                    d.text((cols["PTS"] - (get_tw(str(data['Pts']), font_row)/2), y + 8), str(data['Pts']), fill=c_text, font=font_row)
                    nrr_str = f"{data['NRR']:+.2f}"
                    d.text((cols["NRR"] - (get_tw(nrr_str, font_row)/2), y + 8), nrr_str, fill=c_text, font=font_row)
                    y += row_height
                    
                buf = io.BytesIO()
                img.save(buf, format="PNG")
                buf.seek(0)
                return await ctx.send(file=discord.File(fp=buf, filename="crimson_standings.png"))
            except (FileNotFoundError, OSError):
                await ctx.send("⚠️ Crimson theme file not found, falling back to default.")
        
        if not standings: return await ctx.send("No matches have been completed yet.")
        header = f"`{'#':<3}{'Team':<20}{'P':>3}{'W':>3}{'L':>3}{'T':>3}{'Pts':>4}{'NRR':>7}`\n"
        rows = [header]
        for i, (t_name, data) in enumerate(standings, 1):
            nrr = f"{data['NRR']:+.2f}"
            rows.append(f"`{i:<3}{t_name:<20}{data['P']:>3}{data['W']:>3}{data['L']:>3}{data['T']:>3}{data['Pts']:>4}{nrr:>7}`")
        await ctx.send("🏆 **Tournament Standings**\n" + "\n".join(rows))

# ==========================================
# 🚀 STARTUP SEQUENCE
# ==========================================

if __name__ == "__main__":
    keep_alive()

    TOKEN = os.environ.get("DISCORD_TOKEN")
    if not TOKEN:
        print("🚨 CRITICAL ERROR: DISCORD_TOKEN environment variable is missing from Render!")
    else:
        bot.run(TOKEN)