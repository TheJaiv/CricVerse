import discord
from discord import app_commands
from discord.ext import commands, tasks
import random
import csv
import difflib
import asyncio
import io
import os
from PIL import Image, ImageDraw, ImageFont
from keep_alive import keep_alive
from odi_simulation import execute_ball_math_odi, get_smart_ai_bowler_odi
from t20_simulation import execute_ball_math_t20, get_smart_ai_bowler_t20
from tournament_manager import get_server_tournament, save_tournament, get_tournament_standings
from subscription_manager import (
    load_data_from_bin, save_data_to_bin, check_potential_quota, consume_quota, 
    update_user_tier, update_server_tier, get_auth_admins, toggle_auth_admin, 
    get_all_players, add_player, add_players_bulk, update_player, delete_players, clean_duplicate_players,
    get_tier_status, is_channel_restricted, toggle_restricted_channel, DB_CACHE
)

# ==========================================
# ⚙️ 1. SETUP & CONFIGURATION
# ==========================================
ADMIN_DISCORD_ID = 1087369198801526836 # Your ID
_log_env = os.environ.get("LOG_CHANNEL_ID")
LOG_CHANNEL_ID = int(_log_env) if _log_env and _log_env.isdigit() else 0

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

# ==========================================
# 🗄️ 1.5 CLOUD DATABASE & SECURITY
# ==========================================
@tasks.loop(hours=1)
async def auto_sync_jsonbin():
    """Automatically backs up memory to JSONBin every hour"""
    save_data_to_bin()

@bot.event
async def on_ready():
    print(f"🏏 Logged in successfully as {bot.user.name}")
    load_data_from_bin()
    auto_sync_jsonbin.start()
    print("✅ Memory Cache Loaded and Ready.")
# ==========================================
# 📊 2. CORE DATA STRUCTURES & FALLBACKS
# ==========================================

# Hardcoded fallback database to prevent crashes if the CSV is empty
TEAMS_DATA = {
    "Team 1": {
        "name": "The Protagonists",
        "players": [
            {"name": "Ruturaj Gaikwad", "bat": 83, "bowl": 10, "archetype": "Anchor", "role": "Batter"},
            {"name": "Sanju Samson", "bat": 86, "bowl": 10, "archetype": "Aggressor", "role": "Batter"},
            {"name": "Daryl Mitchell", "bat": 87, "bowl": 56, "archetype": "Anchor", "role": "All-Rounder_Pace"},
            {"name": "Shivam Dube", "bat": 86, "bowl": 65, "archetype": "Finisher", "role": "All-Rounder_Pace"},
            {"name": "MS Dhoni", "bat": 95, "bowl": 10, "archetype": "Finisher", "role": "Batter_WK"},
            {"name": "Ravindra Jadeja", "bat": 86, "bowl": 90, "archetype": "Anchor", "role": "All-Rounder_Spin_Off"},
            {"name": "Mitchell Santner", "bat": 82, "bowl": 87, "archetype": "Finisher", "role": "All-Rounder_Spin_Off"},
            {"name": "Deepak Chahar", "bat": 55, "bowl": 83, "archetype": "Anchor", "role": "Bowler_Pace"},
            {"name": "Shardul Thakur", "bat": 78, "bowl": 83, "archetype": "Aggressor", "role": "Bowler_Pace"},
            {"name": "Matheesha Pathirana", "bat": 35, "bowl": 83, "archetype": "Finisher", "role": "Bowler_Pace"},
            {"name": "Maheesh Theekshana", "bat": 32, "bowl": 85, "archetype": "Anchor", "role": "Bowler_Spin_Off"}
        ]
    },
    "Team 2": {
        "name": "The Rivals",
        "players": [
            {"name": "Rohit Sharma", "bat": 93, "bowl": 48, "archetype": "Aggressor", "role": "Batter"},
            {"name": "Ishan Kishan", "bat": 85, "bowl": 25, "archetype": "Aggressor", "role": "Batter_WK"},
            {"name": "Suryakumar Yadav", "bat": 86, "bowl": 33, "archetype": "Aggressor", "role": "Batter"},
            {"name": "Hardik Pandya", "bat": 89, "bowl": 85, "archetype": "Finisher", "role": "All-Rounder_Pace"},
            {"name": "Tim David", "bat": 85, "bowl": 39, "archetype": "Finisher", "role": "Batter"},
            {"name": "Romario Shepherd", "bat": 80, "bowl": 80, "archetype": "Finisher", "role": "Bowler_Pace"},
            {"name": "Mohammad Nabi", "bat": 82, "bowl": 83, "archetype": "Finisher", "role": "All-Rounder_Spin_Off"},
            {"name": "Gerald Coetzee", "bat": 40, "bowl": 81, "archetype": "Aggressor", "role": "Bowler_Pace"},
            {"name": "Jasprit Bumrah", "bat": 35, "bowl": 96, "archetype": "Finisher", "role": "Bowler_Pace"},
            {"name": "Akash Madhwal", "bat": 38, "bowl": 78, "archetype": "Anchor", "role": "Bowler_Pace"},
            {"name": "Allah Ghazanfar", "bat": 40, "bowl": 80, "archetype": "Anchor", "role": "Bowler_Spin_Off"}
        ]
    }
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
        team = match.team1
    else:
        match.t2_impact_used = True
        team = match.team2
        
    if in_player not in team["players"]:
        team["players"].append(in_player)
        
    for inn in [match.innings1, match.innings2]:
        if not inn: continue
        
        is_batting = (inn.batting_team["name"] == team["name"])
        if is_batting:
            if in_player["name"] not in inn.batting_stats:
                inn.batting_team["players"].append(in_player)
                inn.batting_stats[in_player["name"]] = BatterStats(in_player)
            
            b_stats = inn.batting_stats.get(out_name)
            if b_stats:
                if b_stats.dismissal == "not out" and b_stats.balls_faced == 0:
                    b_stats.dismissal = "Subbed Out"
                elif b_stats.dismissal == "not out":
                    b_stats.dismissal = "Retired (Sub)"
        else:
            if in_player["name"] not in inn.bowling_stats:
                inn.bowling_team["players"].append(in_player)
                inn.bowling_stats[in_player["name"]] = BowlerStats(in_player)
            
            bw_stats = inn.bowling_stats.get(out_name)
            if bw_stats:
                bw_stats.is_subbed_out = True

def try_ai_impact_player(match: CricketMatch, innings: InningsState):
    if not getattr(match, "impact_player", False): return
    if not match.is_ai_game: return
    if getattr(match, "t2_impact_used", False): return
    
    subs = getattr(match, "t2_subs", [])
    if not subs: return
    
    team = match.team2
    is_batting = (innings.batting_team["name"] == team["name"])
    
    if is_batting:
        if innings.wickets >= 3 and innings.total_balls < match.max_balls - 12:
            batters = [s for s in subs if "Batter" in s["role"] or "All-Rounder" in s["role"]]
            if batters:
                best_bat = max(batters, key=lambda x: x["bat"])
                curr = [innings.batting_team["players"][innings.current_striker_idx]["name"], innings.batting_team["players"][innings.current_non_striker_idx]["name"]]
                cands = [p for p in innings.batting_team["players"] if p["name"] not in curr]
                if cands:
                    worst_bowl = min(cands, key=lambda x: x["bat"])
                    swap_impact_player(match, 2, worst_bowl["name"], best_bat)
                    match.last_commentary_prefix = f"🔄 **AI TACTIC:** {team['name']} uses IMPACT PLAYER! **{best_bat['name']}** IN for **{worst_bowl['name']}**!\n" + getattr(match, "last_commentary_prefix", "")
                    cands = sorted(cands, key=lambda x: x["bat"])
                    worst_bat = cands[0]
                    
                    if best_bat["bat"] > worst_bat["bat"] + 15 and best_bat["bat"] >= 75:
                        swap_impact_player(match, 2, worst_bat["name"], best_bat)
                        match.last_commentary_prefix = f"🔄 **AI TACTIC:** {team['name']} uses IMPACT PLAYER! **{best_bat['name']}** IN for **{worst_bat['name']}**!\n" + getattr(match, "last_commentary_prefix", "")
    else:
        if innings.total_balls >= match.max_balls - 30:
            bowlers = [s for s in subs if "Bowler" in s["role"] or "All-Rounder" in s["role"]]
            if bowlers:
                best_bowl = max(bowlers, key=lambda x: x["bowl"])
                cands = [p for p in innings.bowling_team["players"] if (not innings.current_bowler or p["name"] != innings.current_bowler["name"])]
                if cands:
                    worst_bat = min(cands, key=lambda x: x["bowl"])
                    swap_impact_player(match, 2, worst_bat["name"], best_bowl)
                    match.last_commentary_prefix = f"🔄 **AI TACTIC:** {team['name']} uses IMPACT PLAYER! **{best_bowl['name']}** IN for **{worst_bat['name']}**!\n" + getattr(match, "last_commentary_prefix", "")
                    cands = sorted(cands, key=lambda x: x["bowl"])
                    worst_bowl = cands[0]
                    
                    if best_bowl["bowl"] > worst_bowl["bowl"] + 15 and best_bowl["bowl"] >= 75:
                        swap_impact_player(match, 2, worst_bowl["name"], best_bowl)
                        match.last_commentary_prefix = f"🔄 **AI TACTIC:** {team['name']} uses IMPACT PLAYER! **{best_bowl['name']}** IN for **{worst_bowl['name']}**!\n" + getattr(match, "last_commentary_prefix", "")

def get_smart_ai_bowler(innings, pitch, weather="Clear", format_overs=20):
    if format_overs == 50:
        return get_smart_ai_bowler_odi(innings, pitch, weather, format_overs)
    return get_smart_ai_bowler_t20(innings, pitch, weather, format_overs)

def execute_ball_math(match: CricketMatch):
    if match.format_overs == 50:
        return execute_ball_math_odi(match)
    return execute_ball_math_t20(match)

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
                impact += (bowl.wickets_taken * 30) + ((10 - eco) * (bowl.balls_bowled / 6) * 2)
                
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
                    impact += (bowl.wickets_taken * 30) + ((10 - eco) * (bowl.balls_bowled / 6) * 2)
        
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
        
    timeline_raw = innings.over_log[-6:] if innings.over_log else []
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
    except:
        font_large = ImageFont.load_default()
        font_title = ImageFont.load_default()
        font_bold = ImageFont.load_default()
        font_small = ImageFont.load_default()

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
    if getattr(match, 'is_super_over', False):
        c_accent = "#FFD700" # Gold for Super Over
    elif match.format_overs == 50:
        c_accent = "#39B54A" # Green for ODI
    elif match.format_overs == 20:
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
        fmt = "ODI" if match.format_overs == 50 else "T20" if match.format_overs == 20 else "CUSTOM"
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

def generate_tournament_score_image(match: CricketMatch) -> io.BytesIO:
    theme = "Default"
    if getattr(match, "tournament_server_id", None):
        tourney = next((t for t in DB_CACHE.get("tournaments", []) if t.get("server_id") == match.tournament_server_id), None)
        if tourney: theme = tourney.get("theme", "Default")

    if theme == "Crimson Cricket":
        try:
            img = Image.open("scoreboard_crimson.png").convert("RGB")
            d = ImageDraw.Draw(img)
            W, H = img.width, img.height
            
            try:
                # Dynamically scales font size based on the image height!
                font_title = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", int(H*0.030))
                font_score = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", int(H*0.040))
                font_bold = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", int(H*0.022))
            except:
                font_title = font_score = font_bold = ImageFont.load_default()

            def get_tw(text, font):
                if hasattr(font, 'getbbox'): return font.getbbox(text)[2]
                return len(text) * 12
                
            c_text = "#FFFFFF"
            
            t1_name = match.team1["name"].upper()
            t2_name = match.team2["name"].upper()

            # Top Boxes - Team Names
            d.text((W*0.28 - get_tw(t1_name, font_title)/2, H*0.255), t1_name, fill=c_text, font=font_title)
            d.text((W*0.715 - get_tw(t2_name, font_title)/2, H*0.255), t2_name, fill=c_text, font=font_title)

            # Top Boxes - Scores
            inn1 = match.innings1
            if inn1:
                score1 = f"{inn1.total_runs}-{inn1.wickets} ({inn1.total_balls//6}.{inn1.total_balls%6})"
                d.text((W*0.28 - get_tw(score1, font_score)/2, H*0.285), score1, fill=c_text, font=font_score)

            inn2 = match.innings2 if match.current_innings_num == 2 else None
            if inn2:
                score2 = f"{inn2.total_runs}-{inn2.wickets} ({inn2.total_balls//6}.{inn2.total_balls%6})"
            else:
                score2 = "YET TO BAT"
            d.text((W*0.715 - get_tw(score2, font_score)/2, H*0.285), score2, fill=c_text, font=font_score)

            def draw_grid(inn, start_x, start_y, width, row_h, is_batting):
                if not inn: return
                
                c_hdr = "#CCCCCC"
                if is_batting:
                    d.text((start_x, start_y), "BATTER", fill=c_hdr, font=font_bold)
                    d.text((start_x + width*0.75 - get_tw("R", font_bold)/2, start_y), "R", fill=c_hdr, font=font_bold)
                    d.text((start_x + width*0.92 - get_tw("B", font_bold)/2, start_y), "B", fill=c_hdr, font=font_bold)
                else:
                    d.text((start_x, start_y), "BOWLER", fill=c_hdr, font=font_bold)
                    d.text((start_x + width*0.75 - get_tw("W-R", font_bold)/2, start_y), "W-R", fill=c_hdr, font=font_bold)
                    d.text((start_x + width*0.92 - get_tw("O", font_bold)/2, start_y), "O", fill=c_hdr, font=font_bold)
                    
                y = start_y + row_h
                if is_batting:
                    active = [b for b in inn.batting_stats.values() if b.balls_faced > 0 or b.dismissal != "not out"]
                    top = sorted(active, key=lambda x: x.runs_scored, reverse=True)[:6]
                    for b in top:
                        name = b.profile['name'][:18].upper()
                        runs = f"{b.runs_scored}*" if b.dismissal == "not out" else str(b.runs_scored)
                        balls = str(b.balls_faced)
                        d.text((start_x, y), name, fill=c_text, font=font_bold)
                        d.text((start_x + width*0.75 - get_tw(runs, font_bold)/2, y), runs, fill=c_text, font=font_bold)
                        d.text((start_x + width*0.92 - get_tw(balls, font_bold)/2, y), balls, fill=c_text, font=font_bold)
                        y += row_h
                else:
                    active = [b for b in inn.bowling_stats.values() if b.balls_bowled > 0]
                    top = sorted(active, key=lambda x: (x.wickets_taken, -x.runs_conceded), reverse=True)[:6]
                    for b in top:
                        name = b.profile['name'][:18].upper()
                        wr = f"{b.wickets_taken}-{b.runs_conceded}"
                        ov = f"{b.balls_bowled // 6}.{b.balls_bowled % 6}"
                        d.text((start_x, y), name, fill=c_text, font=font_bold)
                        d.text((start_x + width*0.75 - get_tw(wr, font_bold)/2, y), wr, fill=c_text, font=font_bold)
                        d.text((start_x + width*0.92 - get_tw(ov, font_bold)/2, y), ov, fill=c_text, font=font_bold)
                        y += row_h

            grid_w = W * 0.40
            row_h = H * 0.043

            # Left column is Team 1, Right column is Team 2
            if match.team1["name"] == match.innings1.batting_team["name"]:
                t1_bat_inn, t1_bowl_inn = match.innings1, inn2
                t2_bat_inn, t2_bowl_inn = inn2, match.innings1
            else:
                t2_bat_inn, t2_bowl_inn = match.innings1, inn2
                t1_bat_inn, t1_bowl_inn = inn2, match.innings1

            # 🛠️ CONFIGURATION: Tweaking these percentages moves the text!
            draw_grid(t1_bat_inn, W*0.09, H*0.33, grid_w, row_h, True)
            draw_grid(t1_bowl_inn, W*0.09, H*0.61, grid_w, row_h, False)
            draw_grid(t2_bat_inn, W*0.58, H*0.33, grid_w, row_h, True)
            draw_grid(t2_bowl_inn, W*0.58, H*0.61, grid_w, row_h, False)

            # Bottom Banner
            if match.current_innings_num == 1:
                res = f"TARGET SET: {match.innings1.total_runs + 1} RUNS"
            else:
                target = getattr(match, "target", match.innings1.total_runs + 1)
                max_w = 2 if getattr(match, 'is_super_over', False) else 10
                if inn2.total_runs >= target: res = f"{inn2.batting_team['name'].upper()} WON BY {max_w - inn2.wickets} WICKETS"
                elif inn2.total_runs == target - 1: res = "MATCH TIED"
                else: res = f"{match.innings1.batting_team['name'].upper()} WON BY {(target - 1) - inn2.total_runs} RUNS"
                if getattr(match, "dls_active", False): res += " (DLS)"
                potm = get_player_of_the_match(match)
                if potm: res += f"  •  POTM: {potm.upper()}"

            d.text((W*0.50 - get_tw(res, font_title)/2, H*0.925), res, fill=c_text, font=font_title)

            buf = io.BytesIO()
            img.save(buf, format="PNG")
            buf.seek(0)
            return buf
        except Exception as e:
            print(f"⚠️ Warning: scoreboard_crimson.png not found or error: {e}. Falling back to default layout.")
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
    
    t_name = getattr(match, "tournament_name", "TOURNAMENT").upper()
    d.text((140, 100), t_name[:30], fill=c_white, font=font_huge)
    
    match_id = getattr(match, "tournament_match_id", "1")
    d.text((140, 145), f"MATCH {match_id} - {match.format_overs} OVERS", fill="#A5F3FC", font=font_small)

    # Server Logo right
    d.text((1060 - get_tw("SERVER LOGO", font_bold), 115), "SERVER LOGO", fill=c_white, font=font_bold)

    # Helper for drawing Team Section (Header Bar + Grid)
    def draw_team_section(inn, team_dict, y_start):
        # Team Bar
        d.rectangle([(100, y_start), (1100, y_start + 60)], fill=c_team_bar)
        
        # Flag placeholder
        d.rectangle([(140, y_start + 18), (170, y_start + 42)], fill=c_header)
        
        d.text((185, y_start + 12), team_dict['name'].upper(), fill=c_white, font=font_large)
        
        if inn:
            overs_txt = f"OVERS {inn.total_balls // 6}.{inn.total_balls % 6}"
            score_txt = f"{inn.total_runs}-{inn.wickets}"
        else:
            overs_txt = ""
            score_txt = "YET TO BAT"
            
        sw = get_tw(score_txt, font_huge)
        d.text((1060 - sw, y_start + 5), score_txt, fill=c_white, font=font_huge)
        if overs_txt:
            d.text((1060 - sw - get_tw(overs_txt, font_bold) - 20, y_start + 18), overs_txt, fill=c_white, font=font_bold)

        # Grid Headers
        g_y = y_start + 60
        if not inn: return 

        # Middle Divider
        d.line([(600, g_y), (600, g_y + 210)], fill=c_grid_line, width=2)

        # Left Col (Batting)
        d.text((140, g_y + 10), "BATTER", fill=c_text_grey, font=font_small)
        d.text((490 - get_tw("R", font_small)//2, g_y + 10), "R", fill=c_text_grey, font=font_small)
        d.text((550 - get_tw("B", font_small)//2, g_y + 10), "B", fill=c_text_grey, font=font_small)

        active_batters = [b for b in inn.batting_stats.values() if b.balls_faced > 0 or b.dismissal != "not out"]
        top_b = sorted(active_batters, key=lambda x: x.runs_scored, reverse=True)[:4]
        
        for idx, b in enumerate(top_b):
            r_y = g_y + 40 + (idx * 40)
            d.line([(100, r_y), (600, r_y)], fill=c_grid_line, width=1)
            runs = f"{b.runs_scored}*" if b.dismissal == "not out" else str(b.runs_scored)
            
            name = b.profile['name'][:16].upper()
            d.text((140, r_y + 8), name, fill=c_text_navy, font=font_bold)
            d.text((490 - get_tw(runs, font_bold)//2, r_y + 8), runs, fill=c_text_navy, font=font_bold)
            d.text((550 - get_tw(str(b.balls_faced), font_small)//2, r_y + 8), str(b.balls_faced), fill=c_text_grey, font=font_bold)

        # Right Col (Bowling)
        d.text((640, g_y + 10), "BOWLER", fill=c_text_grey, font=font_small)
        d.text((950 - get_tw("W-R", font_small)//2, g_y + 10), "W-R", fill=c_text_grey, font=font_small)
        d.text((1050 - get_tw("O", font_small)//2, g_y + 10), "O", fill=c_text_grey, font=font_small)

        active_bowlers = [b for b in inn.bowling_stats.values() if b.balls_bowled > 0]
        top_bowl = sorted(active_bowlers, key=lambda x: (x.wickets_taken, -x.runs_conceded), reverse=True)[:4]
        
        for idx, b in enumerate(top_bowl):
            r_y = g_y + 40 + (idx * 40)
            d.line([(600, r_y), (1100, r_y)], fill=c_grid_line, width=1)
            
            wr = f"{b.wickets_taken}-{b.runs_conceded}"
            ov = f"{b.balls_bowled // 6}.{b.balls_bowled % 6}"
            
            name = b.profile['name'][:16].upper()
            d.text((640, r_y + 8), name, fill=c_text_navy, font=font_bold)
            d.text((950 - get_tw(wr, font_bold)//2, r_y + 8), wr, fill=c_text_navy, font=font_bold)
            d.text((1050 - get_tw(ov, font_small)//2, r_y + 8), ov, fill=c_text_grey, font=font_bold)

    # 2 & 3. Team 1 Section (Starts at 180px)
    draw_team_section(match.innings1, match.team1, 180)
    
    # 4 & 5. Team 2 Section (Starts at 450px)
    draw_team_section(match.innings2 if match.current_innings_num == 2 else None, match.team2, 450)

    # 6. Footer Block (720 to 820px)
    d.rounded_rectangle([(100, 720), (1100, 820)], radius=20, fill=c_header)
    d.rectangle([(100, 720), (1100, 780)], fill=c_header) # square top
    
    if match.current_innings_num == 1:
        result_str = f"TARGET SET: {match.innings1.total_runs + 1} RUNS"
    else:
        inn1, inn2 = match.innings1, match.innings2
        target = getattr(match, "target", inn1.total_runs + 1)
        max_w = 2 if getattr(match, 'is_super_over', False) else 10
        if inn2.total_runs >= target:
            result_str = f"{inn2.batting_team['name'].upper()} WON BY {max_w - inn2.wickets} WICKETS"
        elif inn2.total_runs == target - 1:
            result_str = "MATCH TIED"
        else:
            result_str = f"{inn1.batting_team['name'].upper()} WON BY {(target - 1) - inn2.total_runs} RUNS"
            
        if getattr(match, "dls_active", False): result_str += " (DLS)"
        
        potm_name = get_player_of_the_match(match)
        if potm_name: result_str += f"  •  POTM: {potm_name.upper()}"

    d.text((600 - get_tw(result_str, font_title)//2, 755), result_str, fill=c_white, font=font_title)

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf

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

async def loop_entire_match_simulation(interaction, match: CricketMatch):
    channel = interaction.channel if hasattr(interaction, 'channel') else interaction
    
    while True:
        innings = match.current_innings
        max_w = 2 if getattr(match, 'is_super_over', False) else 10
        if innings.wickets >= max_w or innings.total_balls >= match.max_balls or (match.current_innings_num == 2 and innings.total_runs >= getattr(match, "target", match.innings1.total_runs + 1)):
            await handle_innings_end(interaction, match)
            break
            
        if innings.total_balls % 6 == 0:
            try_ai_impact_player(match, innings)
            new_bowler = get_smart_ai_bowler(innings, match.pitch, match.weather, match.format_overs)
            if not new_bowler:
                await channel.send("🚨 **CRITICAL ERROR:** Could not find a valid bowler to continue simulation. Match has been stopped.")
                if channel.id in active_games:
                    del active_games[channel.id]
                return
            innings.current_bowler = new_bowler
            innings.over_log.clear()
            innings.bouncers_in_over = 0
            innings.mystery_bowled_this_over = False
            
        execute_ball_math(match)
        
        # Only print scoreboard if user chose Verbose mode
        if getattr(match, 'verbose', False) and innings.total_balls % 6 == 0:
            await channel.send(embed=render_embed_scoreboard(match))
            await asyncio.sleep(0.5)
            
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
    so_match.sim_only = getattr(match, 'sim_only', False)
    so_match.verbose = getattr(match, 'verbose', True)
    so_match.batting_first_id = match.bowling_first_id
    so_match.bowling_first_id = match.batting_first_id
    so_match.innings1 = InningsState(match.innings2.batting_team, match.innings1.batting_team)
    so_match.current_innings = so_match.innings1
    active_games[channel.id] = so_match
    
    await channel.send("🚨 **SCORES ARE TIED!** 🚨\nGet ready for the **SUPER OVER!**\n*The team that batted second will bat first. Max 2 wickets.*")
    if so_match.sim_only: await loop_entire_match_simulation(channel, so_match)
    else: await prompt_over_pacing_hub(channel, so_match)

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
            await prompt_over_pacing_hub(channel, match)
        
    else:
        inn1 = match.innings1
        inn2 = match.innings2
        target = getattr(match, "target", inn1.total_runs + 1)
        is_tied = (inn2.total_runs == target - 1)
        
        if is_tied and not getattr(match, "tie_accepted", False):
            if getattr(match, "is_super_over", False):
                await channel.send("🤯 **THE SUPER OVER IS TIED!** We are going to ANOTHER Super Over!")
                return await trigger_super_over(channel, match)
            if match.format_overs != 50:
                return await trigger_super_over(channel, match)
            else:
                return await channel.send("🏆 **The Match has TIED!** Do you want to play a Super Over?", view=ODISuperOverPrompt(match))

        if getattr(match, "tournament_server_id", None):
            img_buf = generate_tournament_score_image(match)
        else:
            img_buf = generate_final_score_image(match)
            
        file = discord.File(fp=img_buf, filename="final_scoreboard.png")
        embed_full = render_full_scorecard_embed(match, 2)
        
        await channel.send(
            "🏆 **Match over! Here is the final detailed scorecard and broadcast graphic:**", 
            embed=embed_full, 
            file=file
        )
        
        if channel.id in active_games:
            del active_games[channel.id]
            
        if getattr(match, "tournament_server_id", None):
            bot.dispatch("tournament_match_complete", match)

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
            options.append(discord.SelectOption(label=p["name"], description=f"Bat: {p['bat']} | {role_short}", value=p["name"]))
        
    view = discord.ui.View(timeout=120)
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
        innings.bouncers_in_over = 0
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
            
        options.append(discord.SelectOption(label=f"{p['name']} [{p['bowl']} OVR]{suffix}", value=p["name"]))
        
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
        innings.bouncers_in_over = 0
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

async def prompt_over_pacing_hub(interaction, match: CricketMatch):
    view = OverControlHubView(match)
    embed = render_embed_scoreboard(match)
    channel = interaction.channel if hasattr(interaction, 'channel') else interaction
    
    msg = f"⚡ <@{match.p1_id}> **Over Hub** - How to progress the next 6 deliveries?"
    if getattr(match, "impact_player", False):
        msg += "\n💡 **TIP:** Any player can use the `🔄 Impact Player` button below to make a sub!"
        
    await channel.send(msg, embed=embed, view=view)

class OverControlHubView(discord.ui.View):
    def __init__(self, match: CricketMatch):
        super().__init__(timeout=60)
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
        await prompt_new_over_bowler(interaction, self.match)
        
    @discord.ui.button(label="Simulate 1 Over", style=discord.ButtonStyle.primary)
    async def sim_over(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        await interaction.message.edit(view=None)
        innings = self.match.current_innings
        start_runs = innings.total_runs; start_wkts = innings.wickets
        
        prev_mode = self.match.simulation_mode
        self.match.simulation_mode = "whole_match"
        
        if innings.total_balls % 6 == 0:
            new_bowler = get_smart_ai_bowler(innings, self.match.pitch, self.match.weather, self.match.format_overs)
            if not new_bowler:
                channel = interaction.channel if hasattr(interaction, 'channel') else interaction
                await channel.send("🚨 **CRITICAL ERROR:** Could not find a valid bowler.")
                return
            innings.current_bowler = new_bowler
            innings.over_log.clear()
            innings.bouncers_in_over = 0
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
        
    @discord.ui.button(label="Simulate Match (Fast)", style=discord.ButtonStyle.danger)
    async def sim_match_fast(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        await interaction.message.edit(view=None)
        self.match.simulation_mode = "whole_match"
        self.match.verbose = False # Fast mode: No mid-match spam
        await loop_entire_match_simulation(interaction, self.match)

    @discord.ui.button(label="Simulate Match (Verbose)", style=discord.ButtonStyle.secondary)
    async def sim_match_verbose(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        await interaction.message.edit(view=None)
        self.match.simulation_mode = "whole_match"
        self.match.verbose = True # Verbose mode: Every over summary
        await loop_entire_match_simulation(interaction, self.match)
        
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
            
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.channel.id not in active_games or active_games[interaction.channel.id] != self.match:
            await interaction.response.send_message("❌ This match has been ended.", ephemeral=True)
            return False
        if interaction.user.id != self.uid and interaction.user.id != getattr(self.match, "manager_id", None):
            await interaction.response.send_message("Not your turn.", ephemeral=True)
            return False
        return True
        
    async def process_action(self, interaction: discord.Interaction, label: str, action_type: str):
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
            ("Block", discord.ButtonStyle.secondary, 1)
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
        if random.random() < 0.35:
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
        await prompt_over_pacing_hub(interaction, match)
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
        
        if "Spin" in role:
            spin_type = "off" if "Off" in role else "leg"
            title = "Off-Spin" if spin_type == "off" else "Leg-Spin"
            await channel.send(f"🔮 <@{match.get_bowler_user_id()}> (**{innings.current_bowler['name']}**), select your {title} Variation:", view=SpinBowlingView(match, spin_type))
        else:
            await channel.send(f"🔮 <@{match.get_bowler_user_id()}> (**{innings.current_bowler['name']}**), select your Pace Variation:", view=PaceBowlingView(match))

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
                if random.random() < 0.35:
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
        await channel.send(f"⚔️ <@{match.get_striker_user_id()}> (**{sn}**)\n🚨 The bowler bowled a **{match.current_delivery_selection}**!\nSelect your shot:", view=BattingView(match))


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
        lines.append(f"`{i:>2}.` **{p['name']}** — {role_short} *(Bat: {p['bat']} | Bowl: {p['bowl']})*")
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
        discord.SelectOption(label="TEST (90 Overs/Innings)", value="90", emoji="🎩"),
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
            allowed, reason = await asyncio.to_thread(consume_quota, str(interaction.user.id), str(interaction.guild.id) if interaction.guild else None, val, str(ADMIN_DISCORD_ID))
            if not allowed:
            
                return await interaction.followup.send(reason, ephemeral=True)

            self.state.format_overs = int(val)
            # 🚨 FIX: Atomic edit prevents the "Already Acknowledged" Crash
            if val == "20":
            
                await interaction.edit_original_response(content=f"✅ Format set: **T20 (20 overs)**\n\n🌟 <@{self.state.p1_id}> — Enable **Impact Player** rule?", view=ImpactPlayerView(self.state, self.channel))
            else:
                label = {"50": "ODI (50 overs)", "90": "Test (90 overs/innings)"}.get(val, f"{val} overs")
                
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
                    options.append(discord.SelectOption(label=p["name"], description=f"Bat: {p['bat']} | {role_short}", value=p["name"]))
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
        subs = [p for p in self.squad if p not in self.selected_players][:5]
        if self.team_num == 1:
            self.state.t1_roster = self.selected_players
            self.state.t1_subs = subs
            await interaction.response.edit_message(content="✅ **Team 1 XI Confirmed!**", view=None)
            await prompt_tournament_xi(self.channel, self.state, 2)
        else:
            self.state.t2_roster = self.selected_players
            self.state.t2_subs = subs
            await interaction.response.edit_message(content="✅ **Team 2 XI Confirmed!**", view=None)
            await ask_pitch_and_weather(self.channel, self.state)
            
    def get_msg_content(self):
        t_name = self.state.t1_name if self.team_num == 1 else self.state.t2_name
        msg = f"📋 <@{self.owner_id}> (or Manager) — **{t_name} XI Selection**\n"
        msg += f"Select {self.req_count} players from your squad using the dropdown below.\n"
        msg += f"⚠️ **IMPORTANT:** The order you select them determines your exact batting order!\n\n"
        for i, p in enumerate(self.selected_players, 1):
            msg += f"`{i:>2}.` **{p['name']}**\n"
            
        if getattr(self.state, "impact_player", False) and len(self.selected_players) == self.req_count:
            subs = [p for p in self.squad if p not in self.selected_players][:5]
            if subs:
                msg += "\n**Impact Subs (Automatically assigned from remaining squad):**\n"
                for i, p in enumerate(subs, 1):
                    role_short = p["role"].replace("All-Rounder", "AR").replace("Bowler", "BWL").replace("Batter", "BAT").replace("_", " ")
                    msg += f"`{i:>2}.` **{p['name']}** — {role_short} *(Bat: {p['bat']} | Bowl: {p['bowl']})*\n"
        return msg

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
    t1 = {"name": state.t1_name, "players": state.t1_roster, "subs": getattr(state, 't1_subs', [])}
    t2 = {"name": state.t2_name, "players": state.t2_roster, "subs": getattr(state, 't2_subs', [])}

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
            await prompt_over_pacing_hub(channel, match)
    else:
        await channel.send(f"🪙 **Toss Time!** <@{match.p2_id}> — call the coin!", view=TossCallView(match))

class TossCallView(discord.ui.View):
    def __init__(self, match):
        super().__init__(timeout=60)
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
        super().__init__(timeout=60)
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
        await prompt_over_pacing_hub(interaction, self.match)
    @discord.ui.button(label="🏏 Bat First", style=discord.ButtonStyle.success)
    async def bat(self, interaction, button): await self.finalize_toss(interaction, "Bat")
    @discord.ui.button(label="🎯 Bowl First", style=discord.ButtonStyle.danger)
    async def bowl(self, interaction, button): await self.finalize_toss(interaction, "Bowl")


# --- The Listener connecting Chat inputs to the State Machine ---

@bot.event
async def on_message(message: discord.Message):
    if message.author.bot: return

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
                state.t2_subs = [{"name": "Extra Batter 2", "bat": 86, "bowl": 10, "archetype": "Aggressor", "role": "Batter"}, {"name": "Extra Bowler 2", "bat": 10, "bowl": 86, "archetype": "Standard", "role": "Bowler_Pace"}]
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
                state.t2_subs = [{"name": "Extra Batter 2", "bat": 86, "bowl": 10, "archetype": "Aggressor", "role": "Batter"}, {"name": "Extra Bowler 2", "bat": 10, "bowl": 86, "archetype": "Standard", "role": "Bowler_Pace"}]
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
    t1_data = next(t for t in tourney["teams"] if t["name"] == team1_name)
    t2_data = next(t for t in tourney["teams"] if t["name"] == team2_name)
    
    p1_id = int(t1_data["owner_id"])
    p2_id = int(t2_data["owner_id"])
    
    state = MatchSetupState(None, None, p1_id, p2_id)
    state.t1_name = team1_name
    state.t2_name = team2_name
    state.t1_squad = t1_data["squad"]
    state.t2_squad = t2_data["squad"]
    state.tournament_server_id = tourney["server_id"]
    state.tournament_match_id = match_data["match_id"]
    state.manager_id = manager_id
    state.tournament_name = tourney["name"]
    state.format_overs = tourney.get("format_overs", 20)
    state.impact_player = tourney.get("impact_player", False)
    
    active_setups[channel.id] = ("tournament_setup", state)
    
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
        
    if cleared:
        await interaction.response.send_message("🛑 **Match and setup forcefully terminated.** Memory cleared.")
    else:
        await interaction.response.send_message("⚠️ There is no active match or setup running in this channel.", ephemeral=True)

class HelpView(discord.ui.View):
    def __init__(self, pages):
        super().__init__(timeout=180)
        self.pages = pages
        self.current_page = 0
        self.update_buttons()

    def update_buttons(self):
        self.prev_button.disabled = self.current_page == 0
        self.next_button.disabled = self.current_page == len(self.pages) - 1

    @discord.ui.button(label="◀️ Prev", style=discord.ButtonStyle.secondary)
    async def prev_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_page -= 1
        self.update_buttons()
        await interaction.response.edit_message(embed=self.pages[self.current_page], view=self)

    @discord.ui.button(label="Next ▶️", style=discord.ButtonStyle.primary)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_page += 1
        self.update_buttons()
        await interaction.response.edit_message(embed=self.pages[self.current_page], view=self)

@bot.tree.command(name="help", description="Show the list of bot commands and how to use them.")
async def help_cmd(interaction: discord.Interaction):
    embed1 = discord.Embed(title="🏏 Help - Playing Matches (1/3)", color=discord.Color.green())
    embed1.add_field(name="`/match [opponent]`", value="Start an interactive match. Challenge a user or play vs AI.", inline=False)
    embed1.add_field(name="`/simulatematch`", value="Instantly simulate a custom match with AI teams.", inline=False)
    embed1.add_field(name="`/endmatch`", value="Force stop the current match in the channel.", inline=False)
    embed1.add_field(name="`/my_tier`", value="Check your current subscription tier and daily match limits.", inline=False)

    embed2 = discord.Embed(title="🔍 Help - Players & DB (2/3)", color=discord.Color.blue())
    embed2.add_field(name="`/searchplayer [name]`", value="Search for a player in the database to see stats & roles.", inline=False)
    embed2.add_field(name="📋 How to enter Playing XI?", value="Copy and paste a list of 11 player names (one per line) from the database when prompted.", inline=False)
    embed2.add_field(name="🏟️ Conditions", value="Choose from 15 Pitches and 10 Weather conditions, dynamically affecting the simulation engine!", inline=False)
    
    embed3 = discord.Embed(title="🛡️ Help - Admin Settings (3/3)", color=discord.Color.red())
    embed3.add_field(name="`/addplayer`, `/updateplayer`, `/deleteplayer`", value="Manage the player database.", inline=False)
    embed3.add_field(name="`/cleanduplicates`", value="Clean up duplicate players in the DB.", inline=False)
    embed3.add_field(name="`/authadmin`", value="Toggle Admin permissions for player management.", inline=False)
    embed3.add_field(name="`/set_user_tier`, `/set_server_tier`", value="Manage Subscriptions & Daily limits.", inline=False)

    pages = [embed1, embed2, embed3]
    view = HelpView(pages)
    await interaction.response.send_message(embed=pages[0], view=view, ephemeral=True)

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
        swap_impact_player(self.match, self.team_id, out_name, in_player)
        await interaction.response.edit_message(content=f"🔄 **IMPACT PLAYER SWAP:** **{in_name}** comes IN for **{out_name}**!", view=None)

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

async def send_player_profile(interaction: discord.Interaction, player: dict):
    embed = discord.Embed(title=f"🏏 Player Profile: {player['name']}", color=0x1D4ED8)
    embed.add_field(name="🔥 Batting", value=f"`{player['bat']}`", inline=True)
    embed.add_field(name="🎯 Bowling", value=f"`{player['bowl']}`", inline=True)
    embed.add_field(name="📋 Role", value=player["role"].replace("_", " "), inline=True)
    embed.add_field(name="🧠 Archetype", value=player["archetype"], inline=True)
    
    await interaction.followup.send(embed=embed)

@bot.tree.command(name="searchplayer", description="Search for a player in the Cloud DB.")
async def searchplayer(interaction: discord.Interaction, name: str):
    await interaction.response.defer()
    search_query = name.strip()
    
    # 🚨 PULL FROM MEMORY CACHE
    all_players = get_all_players()
    player_names = [p["name"] for p in all_players]
    
    if not all_players:
        return await interaction.followup.send("❌ Error: Cache is empty.")
        
    exact = next((p for p in all_players if p["name"].lower() == search_query.lower()), None)
    if exact:
        return await send_player_profile(interaction, exact)

    subs = [p for p in all_players if search_query.lower() in p["name"].lower()]
    fuzz = difflib.get_close_matches(search_query, player_names, n=1, cutoff=0.2)

    if not subs and not fuzz:
        return await interaction.followup.send(f"❌ Player `{search_query}` not found.")
    
    if fuzz:
        best_name = fuzz[0] 
    else:
        best_name = subs[0]["name"]
        
    if len(subs) == 1 and not fuzz:
        return await send_player_profile(interaction, subs[0])

    other = [p["name"] for p in subs if p["name"] != best_name]
    msg = f"🔍 **Not found exactly.**\n💡 **Best Match:** `{best_name}`\n👉 Rerun: `/searchplayer name: {best_name}`"
    
    if other:
        msg += f"\n\n📂 **Alternatives:**\n" + "\n".join([f"• {o}" for o in other[:5]])
        
    await interaction.followup.send(msg)

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

class AddPlayerModal(discord.ui.Modal, title="Add New Player"):
    p_name = discord.ui.TextInput(label="Player Name", required=True)
    bat_r = discord.ui.TextInput(label="Batting Rating (1-99)", max_length=2, required=True)
    bowl_r = discord.ui.TextInput(label="Bowling Rating (1-99)", max_length=2, required=True)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            bat = int(self.bat_r.value)
            bowl = int(self.bowl_r.value)
            if not (0 <= bat <= 100 and 0 <= bowl <= 100):
                raise ValueError
        except:
            return await interaction.response.send_message("❌ Need numbers 0-100.", ephemeral=True)
            
        await interaction.response.send_message("Select Role/Archetype below:", view=PlayerRoleSelectView(self.p_name.value.strip(), bat, bowl), ephemeral=True)

class PlayerRoleSelectView(discord.ui.View):
    def __init__(self, name, bat, bowl):
        super().__init__(timeout=180)
        self.n = name
        self.bat = bat
        self.bowl = bowl
        self.s_role = None
        self.s_arch = None

    @discord.ui.select(placeholder="Select Role...", options=[discord.SelectOption(label=r, value=r) for r in ["Batter", "Batter_WK", "Bowler_Pace", "Bowler_Spin_Off", "Bowler_Spin_Leg", "All-Rounder_Pace", "All-Rounder_Spin_Off", "All-Rounder_Spin_Leg"]])
    async def s_role_cb(self, inter, sel):
        self.s_role = sel.values[0]
        await inter.response.defer()
        await self.save(inter)
        
    @discord.ui.select(placeholder="Select Archetype...", options=[discord.SelectOption(label=a, value=a) for a in ["Aggressor", "Anchor", "Finisher", "Standard"]])
    async def s_arch_cb(self, inter, sel):
        self.s_arch = sel.values[0]
        await inter.response.defer()
        await self.save(inter)
        
    async def save(self, inter):
        if self.s_role and self.s_arch:
            for c in self.children:
                c.disabled = True
                
            success = add_player({
                "name": self.n,
                "bat": self.bat,
                "bowl": self.bowl,
                "role": self.s_role,
                "archetype": self.s_arch
            })
            
            if not success:
                return await inter.followup.send(f"❌ Cancelled: `{self.n}` already exists in DB!", ephemeral=True)
                
            await inter.followup.send(f"✅ Saved `{self.n}` to JSONBin!", ephemeral=True)
            await log_db_update("Player Added", self.n, inter.user, f"Bat: {self.bat} | Bowl: {self.bowl}\nRole: {self.s_role}\nArchetype: {self.s_arch}")

@bot.tree.command(name="addplayer", description="[ADMIN] Add player to Cloud DB.")
async def add_p_cmd(interaction: discord.Interaction):
    admins = get_auth_admins()
    if interaction.user.id != ADMIN_DISCORD_ID and str(interaction.user.id) not in admins: 
        return await interaction.response.send_message("❌ Access Denied: Admin only.", ephemeral=True)
        
    await interaction.response.send_modal(AddPlayerModal())

@bot.tree.command(name="force_sync", description="[OWNER] Manually force backup memory cache to Cloud DB.")
async def force_sync_cmd(interaction: discord.Interaction):
    if interaction.user.id != ADMIN_DISCORD_ID:
        return await interaction.response.send_message("❌ Owner only.", ephemeral=True)
        
    await interaction.response.defer(ephemeral=True)
    try:
        # Forces the synchronous JSONBin save instead of waiting for the 1-hour loop
        save_data_to_bin()
        await interaction.followup.send("✅ Memory cache successfully force-synced to the Cloud DB!")
        await log_db_update("Manual Cloud Sync", "Database Backup", interaction.user, "Force synced local memory cache to JSONBin.")
    except Exception as e:
        await interaction.followup.send(f"❌ Error during sync: {e}")

@bot.tree.command(name="sync_csv", description="[OWNER] Sync missing players from players_master.csv to Cloud DB.")
async def sync_csv_cmd(interaction: discord.Interaction):
    if interaction.user.id != ADMIN_DISCORD_ID:
        return await interaction.response.send_message("❌ Owner only.", ephemeral=True)
    
    await interaction.response.defer(ephemeral=True)
    
    if not os.path.exists("players_master.csv"):
        return await interaction.followup.send("❌ Error: `players_master.csv` not found.")
        
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
            await interaction.followup.send(f"✅ Sync complete! Added **{added_count}** new players to the JSONBin database.")
            await log_db_update("CSV Sync", "Batch Import", interaction.user, f"Added {added_count} new players from CSV.")
        else:
            await interaction.followup.send("✅ Sync complete! No new players found in CSV (database is already up to date).")
            
    except Exception as e:
        await interaction.followup.send(f"❌ Error during sync: {e}")

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

@bot.tree.command(name="authadmin", description="[OWNER] Toggle a user's permission to add/update players.")
async def auth_admin_cmd(interaction: discord.Interaction, user: discord.Member):
    if interaction.user.id != ADMIN_DISCORD_ID:
        return await interaction.response.send_message("❌ Owner only.", ephemeral=True)
    
    uid = str(user.id)
    added = toggle_auth_admin(uid)
    if added:
        msg = f"✅ {user.mention} is now an **Admin** and can add/update players."
    else:
        msg = f"🚫 Admin permissions **revoked** for {user.mention}."
        
    await interaction.response.send_message(msg, ephemeral=True)

@bot.tree.command(name="toggle_channel_lock", description="[ADMIN] Lock or unlock matches in the current channel.")
@app_commands.default_permissions(manage_channels=True)
async def toggle_channel_lock_cmd(interaction: discord.Interaction):
    is_owner = interaction.user.id == ADMIN_DISCORD_ID
    has_perms = getattr(interaction.user, 'guild_permissions', None) and interaction.user.guild_permissions.manage_channels
    if not (is_owner or has_perms):
        return await interaction.response.send_message("❌ You need Manage Channels permission to do this.", ephemeral=True)
        
    locked = toggle_restricted_channel(str(interaction.channel.id))
    if locked:
        await interaction.response.send_message("🔒 **Channel Locked:** Matches can no longer be played in this channel.")
    else:
        await interaction.response.send_message("🔓 **Channel Unlocked:** Matches can now be played in this channel.")

# ==================== UPDATE PLAYER ====================

class UpdatePlayerModal(discord.ui.Modal, title="Update Player"):
    def __init__(self, cur_player, all_p):
        super().__init__()
        self.cur = cur_player
        self.all_p = all_p
        
        self.new_name = discord.ui.TextInput(label="Player Name (Edit to change)", default=self.cur["name"], required=True)
        self.bat_r = discord.ui.TextInput(label="Batting Rating", default=str(self.cur["bat"]), required=True)
        self.bowl_r = discord.ui.TextInput(label="Bowling Rating", default=str(self.cur["bowl"]), required=True)
        
        self.add_item(self.new_name)
        self.add_item(self.bat_r)
        self.add_item(self.bowl_r)
        
    async def on_submit(self, inter: discord.Interaction):
        try:
            bat = int(self.bat_r.value)
            bowl = int(self.bowl_r.value)
        except:
            return await inter.response.send_message("❌ Must be numbers.", ephemeral=True)
            
        new_n = self.new_name.value.strip()
        
        # Prevent renaming to a player that already exists
        if new_n.lower() != self.cur["name"].lower():
            if any(p["name"].lower() == new_n.lower() for p in self.all_p):
                return await inter.response.send_message(f"❌ A player named `{new_n}` already exists in the DB!", ephemeral=True)
                
        await inter.response.send_message("Select New Role/Archetype below:", view=UpdateRoleSelectView(self.cur["name"], new_n, bat, bowl, self.all_p), ephemeral=True)

class UpdateRoleSelectView(discord.ui.View):
    def __init__(self, old_name, new_name, bat, bowl, all_p):
        super().__init__(timeout=180)
        self.old_name = old_name
        self.new_name = new_name
        self.bat = bat
        self.bowl = bowl
        self.all_p = all_p
        self.s_role = None
        self.s_arch = None

    @discord.ui.select(placeholder="Select Role...", options=[discord.SelectOption(label=r, value=r) for r in ["Batter", "Batter_WK", "Bowler_Pace", "Bowler_Spin_Off", "Bowler_Spin_Leg", "All-Rounder_Pace", "All-Rounder_Spin_Off", "All-Rounder_Spin_Leg"]])
    async def s_role_cb(self, inter, sel):
        self.s_role = sel.values[0]
        await inter.response.defer()
        await self.save(inter)
        
    @discord.ui.select(placeholder="Select Archetype...", options=[discord.SelectOption(label=a, value=a) for a in ["Aggressor", "Anchor", "Finisher", "Standard"]])
    async def s_arch_cb(self, inter, sel):
        self.s_arch = sel.values[0]
        await inter.response.defer()
        await self.save(inter)
        
    async def save(self, inter):
        if self.s_role and self.s_arch:
            for c in self.children:
                c.disabled = True
                
            update_player(self.old_name, {
                "name": self.new_name,
                "bat": self.bat,
                "bowl": self.bowl,
                "role": self.s_role,
                "archetype": self.s_arch
            })
            
            await inter.followup.send(f"✅ Successfully updated `{self.new_name}` in JSONBin!", ephemeral=True)
            change_str = f"Old Name: {self.old_name}\n" if self.old_name != self.new_name else ""
            change_str += f"Bat: {self.bat} | Bowl: {self.bowl}\nRole: {self.s_role}\nArchetype: {self.s_arch}"
            await log_db_update("Player Updated", self.new_name, inter.user, change_str)

@bot.tree.command(name="updateplayer", description="[ADMIN] Update player stats in DB.")
async def up_p_cmd(interaction: discord.Interaction, name: str):
    admins = get_auth_admins()
    if interaction.user.id != ADMIN_DISCORD_ID and str(interaction.user.id) not in admins:
        return await interaction.response.send_message("❌ Access Denied: Admin only.", ephemeral=True)
        
    all_p = get_all_players()
    cur_player = next((p for p in all_p if p["name"].lower() == name.strip().lower()), None)
        
    if not cur_player:
        return await interaction.response.send_message(f"❌ `{name}` not found in the database.", ephemeral=True)
        
    await interaction.response.send_modal(UpdatePlayerModal(cur_player, all_p))

@bot.tree.command(name="cleanduplicates", description="[ADMIN] Find and remove duplicate players (case-insensitive) from DB.")
async def clean_dup_cmd(interaction: discord.Interaction):
    admins = get_auth_admins()
    if interaction.user.id != ADMIN_DISCORD_ID and str(interaction.user.id) not in admins:
        return await interaction.response.send_message("❌ Access Denied: Admin only.", ephemeral=True)
        
    await interaction.response.defer(ephemeral=True)
    try:
        removed_names = clean_duplicate_players()
            
        if removed_names:
            await interaction.followup.send(f"✅ Removed {len(removed_names)} duplicate player(s):\n" + ", ".join(removed_names[:50]))
            await log_db_update("Database Cleaned", "Duplicates Removed", interaction.user, f"Removed {len(removed_names)} duplicates:\n{', '.join(removed_names[:50])}")
        else:
            await interaction.followup.send("✅ Database is already clean. No duplicate players found.")
    except Exception as e:
        await interaction.followup.send(f"❌ Error: {e}")

@bot.tree.command(name="deleteplayer", description="[ADMIN] Delete a specific player from the Cloud DB.")
async def del_p_cmd(interaction: discord.Interaction, name: str):
    admins = get_auth_admins()
    if interaction.user.id != ADMIN_DISCORD_ID and str(interaction.user.id) not in admins:
        return await interaction.response.send_message("❌ Access Denied: Admin only.", ephemeral=True)
        
    await interaction.response.defer(ephemeral=True)
    try:
        all_p = get_all_players()
        found = [p["name"] for p in all_p if p["name"].lower().strip() == name.lower().strip()]
        if not found:
            return await interaction.followup.send(f"❌ Could not find `{name}` in the database.")
        
        delete_players(found)
            
        await interaction.followup.send(f"✅ Successfully deleted `{', '.join(found)}` from the database.")
        await log_db_update("Player Deleted", name, interaction.user, f"Removed player(s): {', '.join(found)}")
    except Exception as e:
        await interaction.followup.send(f"❌ Error: {e}")


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
        embed = discord.Embed(title="🏏 Cricket Bot Commands", color=discord.Color.blue())
        embed.description = f"Use `{self.context.prefix}help <command>` for more info on a specific command."
        
        for cog, cmds in mapping.items():
            if cog and hasattr(cog, 'qualified_name') and cog.qualified_name == "PrefixCog":
                main_cmds = [c for c in cmds if not isinstance(c, commands.Group)]
                tourney_group = next((c for c in cmds if c.name == 'tournament'), None)

                if main_cmds:
                    main_list = " ".join(f"`{c.name}`" for c in sorted(main_cmds, key=lambda x: x.name))
                    embed.add_field(name="Core Commands", value=main_list, inline=False)
                
                if tourney_group:
                    tourney_list = " ".join(f"`{c.name}`" for c in sorted(tourney_group.commands, key=lambda x: x.name))
                    embed.add_field(name="Tournament Commands (`cv tournament ...`)", value=tourney_list, inline=False)

        await self.get_destination().send(embed=embed)

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

    @commands.command(name="match", help="Start a new Cricket Match simulation.\nUsage: match [@opponent]")
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

    @commands.command(name="endmatch", help="Force cancel the current match or setup in this channel.\nUsage: endmatch")
    async def endmatch(self, ctx):
        channel_id = ctx.channel.id
        cleared = False
        if channel_id in active_games:
            del active_games[channel_id]
            cleared = True
        if channel_id in active_setups:
            del active_setups[channel_id]
            cleared = True
        if cleared:
            await ctx.send("🛑 **Match and setup forcefully terminated.** Memory cleared.")
        else:
            await ctx.send("⚠️ There is no active match or setup running in this channel.")

    @commands.command(name="searchplayer", help="Search for a player in the Cloud DB.\nUsage: searchplayer <name>")
    async def searchplayer(self, ctx, *, name: str):
        search_query = name.strip()
        all_players = get_all_players()
        player_names = [p["name"] for p in all_players]
        
        if not all_players:
            return await ctx.send("❌ Error: Cache is empty.")
            
        exact = next((p for p in all_players if p["name"].lower() == search_query.lower()), None)
        
        class FakeFollowup:
            async def send(self, *args, **kwargs):
                await ctx.send(*args, **kwargs)
                
        class FakeInteraction:
            def __init__(self):
                self.followup = FakeFollowup()
        
        if exact:
            return await send_player_profile(FakeInteraction(), exact)

        subs = [p for p in all_players if search_query.lower() in p["name"].lower()]
        fuzz = difflib.get_close_matches(search_query, player_names, n=1, cutoff=0.2)

        if not subs and not fuzz:
            return await ctx.send(f"❌ Player `{search_query}` not found.")
        
        best_name = fuzz[0] if fuzz else subs[0]["name"]
        msg = f"🔍 **Not found exactly.**\n💡 **Best Match:** `{best_name}`\n👉 Rerun: `cv searchplayer \"{best_name}\"`"
        await ctx.send(msg)

    @commands.command(name="force_sync", help="[OWNER] Manually force backup memory cache to Cloud DB.\nUsage: force_sync")
    async def force_sync(self, ctx):
        if ctx.author.id != ADMIN_DISCORD_ID:
            return await ctx.send("❌ Owner only.")
        try:
            save_data_to_bin()
            await ctx.send("✅ Memory cache successfully force-synced to the Cloud DB!")
        except Exception as e:
            await ctx.send(f"❌ Error during sync: {e}")

    @commands.group(name="tournament", invoke_without_command=True, help="Main command for tournaments.\nUsage: tournament")
    async def tournament(self, ctx):
        await ctx.send_help(ctx.command)

    @tournament.command(name="create", help="[ADMIN] Create a new tournament.\nUsage: tournament create \"<name>\" <format> [impact_player=true/false]")
    async def t_create(self, ctx, name: str, format_str: str, *options: str):
        kwargs = { 'impact_player': False }
        for opt in options:
            try:
                key, value = opt.split('=', 1)
                if key == 'impact_player': kwargs['impact_player'] = to_bool(value)
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
            "format_overs": format_overs, "min_squad": 11, "max_squad": 15, "impact_player": kwargs['impact_player']
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
            if cat in ["bat", "wk"]: return f"`{p['bat']:>2} BAT` • **{p['name']}** *(Type: {arch})*"
            elif cat == "ar": return f"`{p['bat']:>2} BAT | {p['bowl']:>2} BWL` • **{p['name']}** *({style} | {arch})*"
            else: return f"`{p['bowl']:>2} BWL` • **{p['name']}** *({style})*"

        if batters: embed.add_field(name="🏏 Batters", value="\n".join([format_player(p, "bat") for p in batters]), inline=False)
        if wks: embed.add_field(name="🧤 Wicket-Keepers", value="\n".join([format_player(p, "wk") for p in wks]), inline=False)
        if all_rounders: embed.add_field(name="⚔️ All-Rounders", value="\n".join([format_player(p, "ar") for p in all_rounders]), inline=False)
        if bowlers: embed.add_field(name="🎯 Bowlers", value="\n".join([format_player(p, "bowl") for p in bowlers]), inline=False)
        
        await ctx.send(embed=embed)

    @tournament.command(name="start", help="[MANAGER] Lock registration and generate Round Robin schedule.\nUsage: tournament start")
    async def t_start(self, ctx):
        server_id = str(ctx.guild.id)
        tourney = get_server_tournament(server_id)
        
        is_mgr = (ctx.author.id == ADMIN_DISCORD_ID) or (ctx.author.guild_permissions.administrator) or (tourney and str(ctx.author.id) in tourney.get("managers", []))
        if not tourney: return await ctx.send("❌ No tournament exists.")
        if not is_mgr: return await ctx.send("❌ Managers only.")
        if tourney["status"] != "registration": return await ctx.send("❌ Tournament already started.")
        
        if len(tourney["teams"]) < 2:
            return await ctx.send("❌ Need at least 2 teams.")
            
        min_s = tourney.get("min_squad", 11)
        for t in tourney["teams"]:
            if len(t.get("squad", [])) < min_s:
                return await ctx.send(f"❌ Team **{t['name']}** does not have a valid squad yet.")
                
        teams = [t["name"] for t in tourney["teams"]]
        if len(teams) % 2 != 0:
            teams.append("BYE")
            
        import random
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
                matchups.append({
                    "round": r + 1,
                    "team1": m[0],
                    "team2": m[1]
                })
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
            
        embed = discord.Embed(title=f"🏆 Tournament: {tourney['name']}", color=discord.Color.gold())
        fmt = tourney.get('format_overs', 20)
        embed.set_footer(text=f"Format: {fmt} Overs | Squad Rules: {tourney.get('min_squad', 11)}-{tourney.get('max_squad', 15)} Players")
        
        if tourney["status"] == "registration":
            embed.description = "📝 **Registration Phase**"
            teams_str = ""
            for t in tourney["teams"]:
                squad_len = len(t.get("squad", []))
                teams_str += f"• **{t['name']}** (<@{t['owner_id']}>) - {squad_len}/{tourney.get('max_squad', 15)} Players\n"
            if not teams_str: teams_str = "No teams added yet."
            embed.add_field(name="Registered Teams", value=teams_str, inline=False)
            
        elif tourney["status"] == "active":
            schedule = tourney.get("schedule", [])
            pending_matches = [m for m in schedule if m["status"] == "pending"]
            
            if not pending_matches:
                gs_matches = [m for m in schedule if isinstance(m.get("round"), int)]
                if all(m["status"] == "completed" for m in gs_matches) and not any(not isinstance(m.get("round"), int) for m in schedule):
                    return await ctx.send(embed=discord.Embed(title=f"🏆 Tournament: {tourney['name']}", description="🏁 **Group Stage Completed!**\nUse `cv tournament generate_knockouts` to begin the Semi-Finals.", color=discord.Color.gold()))
                else:
                    return await ctx.send(embed=discord.Embed(title=f"🏆 Tournament: {tourney['name']}", description="🏁 **All matches are completed!**", color=discord.Color.gold()))
                
            embed.description = f"🔥 **Active Phase**\nUse `cv tournament play <match_id>` to launch your matches!"
            sched_str = ""
            for m in pending_matches[:10]:
                r_label = f"Round {m['round']}" if isinstance(m['round'], int) else m['round']
                sched_str += f"**Match {m['match_id']}** ({r_label}): **{m['team1']}** vs **{m['team2']}**\n"
            
            if len(pending_matches) > 10:
                sched_str += f"\n*...and {len(pending_matches) - 10} more matches.*"
            embed.add_field(name="Upcoming Matches", value=sched_str, inline=False)
            
        elif tourney["status"] == "completed":
            final = next((m for m in tourney.get("schedule", []) if m["round"] == "Final"), None)
            winner = final["result"]["winner"] if final else "TBD"
            embed.description = f"🏆 **TOURNAMENT COMPLETED!**\n👑 **Champions: {winner}**\n\nCheck `cv tournament leaderboard` for top performers!"
            
        await ctx.send(embed=embed)

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
        
        sf1 = {"match_id": len(tourney["schedule"]) + 1, "round": "Semi-Final 1", "team1": top4[0], "team2": top4[3], "status": "pending", "result": None}
        sf2 = {"match_id": len(tourney["schedule"]) + 2, "round": "Semi-Final 2", "team1": top4[1], "team2": top4[2], "status": "pending", "result": None}
        
        tourney["schedule"].extend([sf1, sf2])
        save_tournament(tourney)
        
        await ctx.send(f"🔥 **Knockout Stage Set!**\n**Semi-Final 1:** {top4[0]} vs {top4[3]}\n**Semi-Final 2:** {top4[1]} vs {top4[2]}\n\nUse `cv tournament play_next` to begin!")

    @tournament.command(name="standings", help="View the Tournament Points Table & NRR.\nUsage: tournament standings")
    async def t_standings(self, ctx):
        server_id = str(ctx.guild.id)
        tourney = get_server_tournament(server_id)
        if not tourney: return await ctx.send("❌ No tournament exists.")
        
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
