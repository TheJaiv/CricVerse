import discord
from discord import app_commands
from discord.ext import commands
import random
import difflib
import asyncio
import io
import os
import psycopg2
from psycopg2.extras import DictCursor
from PIL import Image, ImageDraw, ImageFont
from keep_alive import keep_alive
from odi_simulation import execute_ball_math_odi, get_smart_ai_bowler_odi
from subscription_manager import init_subs_db, check_potential_quota, consume_quota, update_user_tier, update_server_tier

# ==========================================
# ⚙️ 1. SETUP & CONFIGURATION
# ==========================================
ADMIN_DISCORD_ID = 1087369198801526836 # Your ID
DB_URL = os.environ.get("DATABASE_URL")
_log_env = os.environ.get("LOG_CHANNEL_ID")
LOG_CHANNEL_ID = int(_log_env) if _log_env and _log_env.isdigit() else 0

class CricketBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(command_prefix="!", intents=intents)
    
    async def setup_hook(self):
        await self.tree.sync()
        print("✅ Slash commands synchronized globally.")

bot = CricketBot()
active_games = {}
active_setups = {}

# ==========================================
# 🗄️ 1.5 CLOUD DATABASE & SECURITY
# ==========================================
def get_db():
    return psycopg2.connect(DB_URL, sslmode='require')

def init_db():
    if not DB_URL:
        print("⚠️ DATABASE_URL not found. Cloud DB will not work.")
        return
    
    # 1. Create the Tables if they don't exist
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute('''CREATE TABLE IF NOT EXISTS players (
                name TEXT PRIMARY KEY, bat INTEGER, bowl INTEGER, role TEXT, archetype TEXT
            )''')
            cur.execute('''CREATE TABLE IF NOT EXISTS auth_admins (admin_id TEXT PRIMARY KEY)''')
        conn.commit()
        
    init_subs_db()
    # 2. Auto-Migrate from CSV to SQL Database!
    if os.path.exists("players_master.csv"):
        import csv
        try:
            with open("players_master.csv", "r", encoding="utf-8-sig") as f:
                with get_db() as conn:
                    with conn.cursor() as cur:
                        for row in csv.DictReader(f):
                            cur.execute('''
                                INSERT INTO players (name, bat, bowl, role, archetype) 
                                VALUES (%s, %s, %s, %s, %s)
                                ON CONFLICT (name) DO NOTHING
                            ''', (row["Name"].strip(), int(row["Bat"]), int(row["Bowl"]), row["Role"].strip(), row["Archetype"].strip()))
                    conn.commit()
            print("✅ Legacy CSV data successfully synced to Neon Cloud DB.")
        except Exception as e: print(f"Migration Error: {e}")

@bot.event
async def on_ready():
    print(f"🏏 Logged in successfully as {bot.user.name}")
    init_db()
    load_auth_admins()  # Pre-cache to prevent 10062 Interaction Timeouts
    print("✅ Cloud Database Connected and Ready.")

AUTH_CACHE = {"admins": None}

def load_auth_admins(force=False):
    if not force and AUTH_CACHE["admins"] is not None:
        return AUTH_CACHE["admins"]
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT admin_id FROM auth_admins")
                AUTH_CACHE["admins"] = [row[0] for row in cur.fetchall()]
                return AUTH_CACHE["admins"]
    except: return []

def load_all_players_from_db():
    players = []
    try:
        with get_db() as conn:
            with conn.cursor(cursor_factory=DictCursor) as cur:
                cur.execute("SELECT * FROM players")
                for row in cur.fetchall():
                    players.append({
                        "name": row["name"], "bat": int(row["bat"]), "bowl": int(row["bowl"]),
                        "role": row["role"], "archetype": row["archetype"]
                    })
    except Exception as e: print(f"DB Load Warning: {e}")
    return players
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
        self.form_factor = random.uniform(0.90, 1.10)

class BowlerStats:
    def __init__(self, profile):
        self.profile = profile
        self.runs_conceded = 0
        self.balls_bowled = 0
        self.wickets_taken = 0
        self.form_factor = random.uniform(0.90, 1.10)

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
# 🧠 3. SIMULATION MATH ENGINE (DEEP REALISM V5)
# ==========================================

SPIN_SHOT_MATRIX = {
    "Off spin": ["Sweep", "Drive", "Flick"],
    "Carrom": ["Cut", "Drive", "Loft"],
    "Arm ball": ["Loft", "Drive", "Block"],
    "Doosra": ["Cut", "Sweep", "Drive"],
    "Top spin": ["Cut", "Drive", "Pull"],
    "Leg spin": ["Cut", "Drive", "Loft", "Sweep"],
    "Googly": ["Pull", "Drive", "Sweep"],
    "Flipper": ["Drive", "Flick", "Block"],
    "Drifter": ["Loft", "Drive", "Cut"],
    "Slider": ["Flick", "Drive", "Sweep"],
    "Mystery": ["Block", "Sweep", "Drive"] 
}

def get_smart_ai_shot(deliv, is_collapse, is_death_overs, archetype):
    if is_collapse:
        return random.choices(["Block", "Drive", "Flick", "Cut"], weights=[40, 30, 15, 15], k=1)[0]
        
    if is_death_overs:
        if archetype == "Anchor":
            return random.choices(["Drive", "Loft", "Pull", "Flick"], weights=[30, 30, 20, 20], k=1)[0]
        elif archetype == "Standard":
            return random.choices(["Loft", "Drive", "Pull", "Flick"], weights=[30, 30, 20, 20], k=1)[0]
        else:
            return random.choices(["Loft", "Pull", "Scoop", "Sweep"], weights=[40, 25, 15, 20], k=1)[0]
            
    if "Yorker" in deliv:
        return random.choices(["Block", "Drive", "Flick"], weights=[40, 40, 20], k=1)[0]
    elif "Bouncer" in deliv:
        return random.choices(["Pull", "Cut", "Block"], weights=[50, 40, 10], k=1)[0]
    elif "Full" in deliv:
        return random.choices(["Drive", "Loft", "Flick"], weights=[40, 40, 20], k=1)[0]
    elif deliv in SPIN_SHOT_MATRIX:
        if random.random() < 0.7:
            return random.choice(SPIN_SHOT_MATRIX[deliv])
        else:
            return random.choices(["Drive", "Sweep", "Cut", "Block"], weights=[30, 30, 20, 20], k=1)[0]
    else:
        return random.choices(["Drive", "Cut", "Flick", "Block"], weights=[35, 25, 25, 15], k=1)[0]

def get_smart_ai_bowler(innings, pitch, format_overs=20):
    if format_overs == 50:
        return get_smart_ai_bowler_odi(innings, pitch, format_overs)
        
    valid_bowlers = []
    bowler_quota = max(1, (format_overs + 4) // 5)
    
    for p in innings.bowling_team["players"]:
        if ("Bowler" in p["role"] or "All-Rounder" in p["role"]):
            stats = innings.bowling_stats[p["name"]]
            if (stats.balls_bowled // 6) < bowler_quota:
                # Make sure they aren't bowling two overs in a row
                if not innings.current_bowler or innings.current_bowler["name"] != p["name"]:
                    valid_bowlers.append(p)
                    
    if not valid_bowlers:
        return None

    current_over = innings.total_balls // 6
    weights = []
    
    for p in valid_bowlers:
        stats = innings.bowling_stats[p["name"]]
        overs_bowled = stats.balls_bowled // 6
        overs_left = bowler_quota - overs_bowled
        base_score = float(p["bowl"])
        
        is_frontline = "Bowler" in p["role"] or base_score >= 80
        if is_frontline:
            base_score *= 3.0
        else:
            base_score *= 0.1 
        
        # Pitch adjustments
        if pitch == "Dusty" and "Spin" in p["role"]:
            base_score *= 1.5
        elif pitch == "Green" and "Pace" in p["role"]:
            base_score *= 1.5
        
        # Phase adjustments
        if current_over < 6: 
            if "Pace" in p["role"]:
                base_score *= 1.5
            if "Spin" in p["role"]:
                base_score *= 0.2 
        elif current_over < 15: 
            if "Spin" in p["role"]:
                base_score *= 1.5 
        else: 
            if "Pace" in p["role"] and p["archetype"] == "Finisher":
                base_score *= 2.0 
            if "Spin" in p["role"]:
                base_score *= 0.3 

        # Death Over Specialist Logic
        if current_over >= 16 and p["bowl"] >= 90 and overs_left > 0:
            base_score *= 50.0 

        # Prevent finishing pace bowlers from bowling out early
        if current_over < 15 and p["archetype"] == "Finisher" and "Pace" in p["role"]:
            if overs_left <= 2:
                base_score *= 0.1 
                
        # Form / Economy factor
        if overs_bowled > 0:
            eco = (stats.runs_conceded / max(1, stats.balls_bowled)) * 6
            if eco <= 6.0:
                base_score *= 2.5 
            elif eco > 11.0:
                base_score *= 0.3 
                
        weights.append(max(1.0, base_score))
        
    return random.choices(valid_bowlers, weights=weights, k=1)[0]

def execute_ball_math(match: CricketMatch):
    if match.format_overs == 50:
        return execute_ball_math_odi(match)
        
    innings = match.current_innings
    striker = innings.batting_team["players"][innings.current_striker_idx]
    bowler = innings.current_bowler

    b_stats = innings.batting_stats[striker["name"]]
    bow_stats = innings.bowling_stats[bowler["name"]]

    bat_rating = striker["bat"] * b_stats.form_factor
    bowl_rating = bowler["bowl"] * bow_stats.form_factor
    
    # Pitch Mechanics
    if match.pitch == "Dusty" and "Spin" in bowler["role"]:
        bowl_rating += 10
    elif match.pitch == "Green" and "Pace" in bowler["role"]:
        bowl_rating += 10
    elif match.pitch == "Flat":
        bat_rating += 10

    # Batter form progression
    if b_stats.balls_faced < 6:
        bat_rating -= 5
    elif 6 <= b_stats.balls_faced <= 45:
        bat_rating += 5
    elif b_stats.balls_faced > 45:
        bat_rating -= (b_stats.balls_faced - 45) * 0.5 
        
    # Bowler fatigue
    if bow_stats.balls_bowled >= 12 and "Pace" in bowler["role"]:
        bowl_rating -= 5
        
    is_powerplay = innings.total_balls < 36
    is_death_overs = innings.total_balls >= (match.max_balls - 30)
    
    pressure_multiplier = 1.0
    if match.current_innings_num == 2:
        runs_needed = (match.innings1.total_runs + 1) - innings.total_runs
        balls_left = match.max_balls - innings.total_balls
        if balls_left > 0:
            rrr = (runs_needed / balls_left) * 6
            if rrr > 11.0:
                pressure_multiplier = min(1.4, 1.0 + ((rrr - 11.0) * 0.05))

    is_collapse = innings.over_log[-18:].count("🔴") >= 2 and innings.partnership_runs < 25
    is_set_partnership = innings.partnership_runs >= 30
    has_wickets_in_hand = innings.total_balls >= (match.max_balls - 42) and innings.wickets <= 3

    # Dynamic Delivery Generation based on Bowler Role (for Fast Sim)
    if match.current_delivery_selection:
        deliv = match.current_delivery_selection
    else:
        if "Spin" in bowler["role"]:
            if "Off" in bowler["role"]:
                deliv = random.choice(["Off spin", "Carrom", "Arm ball", "Doosra", "Top spin", "Mystery"])
            else:
                deliv = random.choice(["Leg spin", "Googly", "Flipper", "Drifter", "Slider", "Mystery"])
        else:
            deliv = f"{random.choice(['Inswing', 'Outswing', 'Fast', 'Slow'])} {random.choice(['Bouncer', 'Full', 'Good', 'Yorker'])}"
            
    shot = match.current_shot_selection or get_smart_ai_shot(deliv, is_collapse, is_death_overs, striker["archetype"])
        
    match.current_delivery_selection = None
    match.current_shot_selection = None
    match.temp_variation = None

    diff = bat_rating - bowl_rating
    
    # EXTRAS SYSTEM: Wide Check (Skips ball)
    if random.random() < 0.04 and "Yorker" not in deliv and "Slow" not in deliv:
        innings.total_runs += 1
        if not hasattr(innings, 'extras'): innings.extras = 0
        innings.extras += 1
        bow_stats.runs_conceded += 1
        innings.over_log.append("WD")
        match.last_commentary = f"**{bowler['name']}** bowled a **Wide!**\n💥 **Result:** 1 Extra Run"
        return
        
    # EXTRAS SYSTEM: No Ball Check
    is_no_ball = False
    if random.random() < 0.02:
        is_no_ball = True
        if not hasattr(innings, 'extras'): innings.extras = 0
        innings.extras += 1
        innings.total_runs += 1
        bow_stats.runs_conceded += 1

    dot_weight = max(15.0, 35.0 - diff * 0.4)
    single_weight = 40.0
    boundary_weight = max(2.0, 13.0 + diff * 0.5) 
    wicket_weight = max(1.5, 5.0 - diff * 0.15) 
    
    if b_stats.balls_faced > 45:
        wicket_weight *= 1.5

    bad_shot_selection = False
    perfect_shot_selection = False
    
    # 🚨 TACTICAL USER BALANCING & SPIN LOGIC
    if "Yorker" in deliv:
        if shot in ["Pull", "Cut"]:
            bad_shot_selection = True
        elif shot in ["Defensive", "Drive"]:
            perfect_shot_selection = True
    elif "Bouncer" in deliv:
        if shot in ["Drive", "Sweep", "Scoop"]:
            bad_shot_selection = True
        elif shot in ["Pull", "Leave"]:
            perfect_shot_selection = True
    elif "Full Toss" in deliv:
        if shot in ["Defensive", "Leave"]:
            bad_shot_selection = True
        elif shot in ["Loft", "Drive"]:
            perfect_shot_selection = True
    elif deliv in SPIN_SHOT_MATRIX:
        if shot in SPIN_SHOT_MATRIX[deliv]:
            perfect_shot_selection = True
        elif shot == "Leave":
            bad_shot_selection = True 
        else:
            # Safey spin balancing for non-optimal shots
            boundary_weight *= 0.20
            dot_weight *= 1.4
            single_weight *= 1.1

    # Base Shot Modifications
    if shot in ["Block", "Defensive"]:
        dot_weight *= 2.0
        single_weight *= 0.8
        boundary_weight = 0.2
        wicket_weight *= 0.5
    elif shot == "Leave":
        dot_weight *= 3.0
        single_weight = 0
        boundary_weight = 0
        wicket_weight *= 1.2
    else:
        if bad_shot_selection:
            wicket_weight *= 1.8
            boundary_weight *= 0.3
            dot_weight *= 1.5
        elif perfect_shot_selection:
            boundary_weight *= 1.4
            wicket_weight *= 0.7
        
        # Archetype adjustments
        if striker["archetype"] == "Aggressor":
            boundary_weight *= 1.2
            wicket_weight *= 1.15
        elif striker["archetype"] == "Anchor":
            dot_weight *= 1.1
            wicket_weight *= 0.75
        elif striker["archetype"] == "Finisher" and is_death_overs:
            boundary_weight *= 1.3

        # Match Situation Logic
        if is_collapse:
            boundary_weight *= 0.6
            wicket_weight *= 0.5 
            
        if is_set_partnership:
            wicket_weight *= 0.8
            
        if has_wickets_in_hand:
            boundary_weight *= 1.4
            wicket_weight *= 1.3
            dot_weight *= 0.6
        
        if is_death_overs or pressure_multiplier > 1.0:
            active_multiplier = max(1.3, pressure_multiplier) if is_death_overs else pressure_multiplier
            boundary_weight *= active_multiplier
            wicket_weight *= (active_multiplier * 1.1)
            
        if innings.last_ball_boundary:
            boundary_weight *= 1.15
            wicket_weight *= 1.15
            
        if is_powerplay:
            boundary_weight *= 1.25
            single_weight *= 0.85
            
    four_weight = boundary_weight
    six_weight = boundary_weight * 0.35
    
    # 🚨 THE "CRACKED GAME" OVERPOWERED SHOT FIXES
    if shot in ["Loft", "Scoop"]:
        four_weight *= 0.6
        six_weight *= 3.0    # Massive six potential
        wicket_weight *= 1.8 # But heavily increased wicket risk!
        dot_weight *= 0.8
    elif shot in ["Block", "Defensive"]:
        four_weight *= 0.1
        six_weight = 0.0
    elif shot in ["Drive", "Cut", "Pull", "Flick", "Sweep"]:
        four_weight *= 1.2
        six_weight *= 0.5    # Standard shots rarely go for six
        
    # 🚨 PACE VARIATION REALISM (Missing Criteria)
    if "Slow" in deliv:
        if shot in ["Loft", "Pull", "Sweep", "Scoop"]:
            wicket_weight *= 1.5 # Deceived by lack of pace
            six_weight *= 0.5
    elif "Fast" in deliv:
        if shot in ["Scoop", "Sweep", "Pull", "Loft"]:
            wicket_weight *= 1.5 # Rushed for pace
    elif "Outswing" in deliv:
        if shot in ["Drive", "Cut"]:
            wicket_weight *= 1.4 # Outside edge risk
            four_weight *= 1.2   # Rewarding if gap is found
    elif "Inswing" in deliv:
        if shot in ["Drive", "Flick", "Sweep"]:
            wicket_weight *= 1.4 # Bowled / LBW risk

    choices = ["dot", "single", "two", "three", "four", "six", "wicket"]
    weights = [
        dot_weight, 
        single_weight, 
        single_weight * 0.3, 
        single_weight * 0.05, 
        four_weight, 
        six_weight, 
        wicket_weight
    ]
    
    outcome = random.choices(choices, weights=weights)[0]
    
    if is_no_ball and outcome == "wicket":
        outcome = "dot"
    
    b_stats.balls_faced += 1
    innings.last_ball_boundary = False
    outcome_text = ""

    if outcome == "wicket":
        innings.wickets += 1
        innings.partnership_runs = 0
        d_types = ["Bowled", "Caught", "LBW"]
        
        # Smart Dismissal Context
        if "Outswing" in deliv and shot in ["Drive", "Cut"]:
            dismissal_type = "Caught Behind"
        elif "Inswing" in deliv and shot in ["Drive", "Flick"]:
            dismissal_type = random.choice(["Bowled", "LBW"])
        elif "Slow" in deliv and shot in ["Loft", "Pull", "Scoop"]:
            dismissal_type = "Caught"
        elif bad_shot_selection and "Yorker" in deliv:
            dismissal_type = "Bowled"
        elif bad_shot_selection and "Bouncer" in deliv:
            dismissal_type = "Caught"
        elif shot in ["Loft", "Scoop"]:
            dismissal_type = "Caught"
        else:
            dismissal_type = random.choice(d_types)
            
        if dismissal_type == "Bowled":
            b_stats.dismissal = f"b. {bowler['name']}"
        elif dismissal_type == "LBW":
            b_stats.dismissal = f"lbw b. {bowler['name']}"
        elif dismissal_type == "Caught Behind":
            wk = next((p["name"] for p in innings.bowling_team["players"] if "WK" in p["role"]), "Keeper")
            b_stats.dismissal = f"c. {wk} b. {bowler['name']}"
        else:
            fielders = [p["name"] for p in innings.bowling_team["players"] if p["name"] != bowler["name"]]
            fielder = random.choice(fielders) if fielders else "Fielder"
            b_stats.dismissal = f"c. {fielder} b. {bowler['name']}"
            
        bow_stats.wickets_taken += 1
        innings.over_log.append("🔴")
        outcome_text = f"WICKET! ({dismissal_type.upper()})"
        
        match.prev_striker_idx = innings.current_striker_idx
        if dismissal_type in ["LBW", "Caught Behind"] and match.simulation_mode == "interactive":
            match.pending_drs = True
            match.drs_dismissal = dismissal_type
        
        if innings.wickets < 10:
            is_ai_batting = match.is_ai_game and match.get_striker_user_id() == match.p2_id
            if match.simulation_mode == "whole_match" or is_ai_batting:
                innings.current_striker_idx = innings.next_batter_idx
                innings.next_batter_idx += 1
            else:
                match.pending_next_batter = True
                match.out_batter_profile = striker
    else:
        runs_map = {"dot": 0, "single": 1, "two": 2, "three": 3, "four": 4, "six": 6}
        runs = runs_map[outcome]
        
        is_bye = False
        if runs == 0 and random.random() < 0.05:
            is_bye = True
            runs = random.choice([1, 2, 4])
            innings.total_runs += runs
            if not hasattr(innings, 'extras'): innings.extras = 0
            innings.extras += runs
            outcome_text = f"{runs} Leg Byes"
            log_entry = f"{runs}LB"
        else:
            innings.total_runs += runs
            innings.partnership_runs += runs
            b_stats.runs_scored += runs
            bow_stats.runs_conceded += runs
            if runs > 0:
                outcome_text = f"{runs} Runs"
            else:
                outcome_text = "Dot Ball"
                
            emoji_map = {0: "⚪", 1: "1️⃣", 2: "2️⃣", 3: "3️⃣", 4: "🟢", 6: "🔵"}
            log_entry = emoji_map[runs]
            
        if is_no_ball:
            log_entry = "NB" + (log_entry if runs > 0 and not is_bye else "")
            outcome_text += " (NO BALL)"
            
        if runs in [4, 6] and not is_bye:
            innings.last_ball_boundary = True
            
        innings.over_log.append(log_entry)
        
        # Rotate strike on odd runs
        if runs in [1, 3]:
            innings.current_striker_idx, innings.current_non_striker_idx = innings.current_non_striker_idx, innings.current_striker_idx

    if not is_no_ball:
        bow_stats.balls_bowled += 1
        innings.total_balls += 1
        
    match.last_commentary = f"**{bowler['name']}** bowled a **{deliv}**\n**{striker['name']}** played: **{shot}**\n💥 **Result:** {outcome_text}"

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
        runs_needed = (match.innings1.total_runs + 1) - innings.total_runs
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
    # Dynamically inject the provided animated discord emojis
    timeline_fmt = [
        item.replace("🟢", "<a:Four:1510370392223649966>")
            .replace("🔵", "<a:six_:1510370301429416077>")
            .replace("🔴", "<a:wickett:1510369641959264429>")
        for item in timeline_raw
    ]
    timeline_str = " ".join(timeline_fmt) if timeline_fmt else "Starting over..."
    
    desc += f"**Timeline**\n{timeline_str}\n"
    
    if match.current_innings_num == 2:
        target_needed = (match.innings1.total_runs + 1) - innings.total_runs
        balls_left = match.max_balls - innings.total_balls
        if target_needed > 0 and balls_left > 0:
            desc += f"-# Equation: Need {target_needed} runs from {balls_left} balls"
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
    c_accent = "#39B54A" if match.format_overs == 50 else "#F97316" # Green for ODI, Orange for others
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
        d.line([(1020, y_start), (1020, y_end)], fill=c_grid, width=2) # Right Col 4-5 border
        d.line([(1110, y_start), (1110, y_end)], fill=c_grid, width=2) # Right Col 5-6 border
        
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

    # Green Bar Match Type
    fmt = "ODI" if match.format_overs == 50 else "T20" if match.format_overs == 20 else "CUSTOM"
    m_type = f"SIMULATION MATCH • {fmt} ({match.format_overs} OVERS)"
    d.text((600 - get_tw(m_type, font_bold)//2, 113), m_type, fill=c_navy, font=font_bold)

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
        d.text((offset_x + 60, 235), "BATTER", fill=c_text_grey, font=font_small)
        d.text((offset_x + 465 - get_tw("R", font_small)//2, 235), "R", fill=c_text_grey, font=font_small)
        d.text((offset_x + 555 - get_tw("B", font_small)//2, 235), "B", fill=c_text_grey, font=font_small)
        
        top_b = sorted(inn.batting_stats.values(), key=lambda x: x.runs_scored, reverse=True)[:4]
        for idx, b in enumerate(top_b):
            y = 285 + (idx * 50)
            name = b.profile['name'][:16].upper()
            if b.dismissal == "not out": name += "*"
            d.text((offset_x + 60, y), name, fill=c_navy, font=font_bold)
            
            if potm_name == b.profile['name']:
                nw = get_tw(name, font_bold)
                d.text((offset_x + 60 + nw + 8, y - 4), "★", fill="#FFD700", font=font_title)
            
            runs = str(b.runs_scored)
            d.text((offset_x + 465 - get_tw(runs, font_bold)//2, y), runs, fill=c_navy, font=font_bold)
            
            balls = str(b.balls_faced)
            d.text((offset_x + 555 - get_tw(balls, font_small)//2, y + 4), balls, fill=c_text_grey, font=font_small)

    draw_batters(match.innings1, 0) # Team 1 Batting
    draw_batters(match.innings2 if match.current_innings_num == 2 else None, 600) # Team 2 Batting

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
        d.text((offset_x + 60, 565), "BOWLER", fill=c_text_grey, font=font_small)
        d.text((offset_x + 465 - get_tw("W-R", font_small)//2, 565), "W-R", fill=c_text_grey, font=font_small)
        d.text((offset_x + 555 - get_tw("O", font_small)//2, 565), "O", fill=c_text_grey, font=font_small)
        
        active_bowlers = [b for b in inn.bowling_stats.values() if b.balls_bowled > 0]
        top_bowl = sorted(active_bowlers, key=lambda x: (x.wickets_taken, -x.runs_conceded), reverse=True)[:4]
        for idx, bowl in enumerate(top_bowl):
            y = 615 + (idx * 50)
            name = bowl.profile['name'][:16].upper()
            d.text((offset_x + 60, y), name, fill=c_navy, font=font_bold)
            
            if potm_name == bowl.profile['name']:
                nw = get_tw(name, font_bold)
                d.text((offset_x + 60 + nw + 8, y - 4), "★", fill="#FFD700", font=font_title)
            
            wr = f"{bowl.wickets_taken}-{bowl.runs_conceded}"
            d.text((offset_x + 465 - get_tw(wr, font_bold)//2, y), wr, fill=c_navy, font=font_bold)
            
            bovers = f"{bowl.balls_bowled // 6}.{bowl.balls_bowled % 6}"
            d.text((offset_x + 555 - get_tw(bovers, font_small)//2, y + 4), bovers, fill=c_text_grey, font=font_small)

    draw_bowlers(match.innings1, 0) # Team 2 Bowling to Team 1
    draw_bowlers(match.innings2 if match.current_innings_num == 2 else None, 600) # Team 1 Bowling to Team 2

    if match.current_innings_num == 1:
        result_str = f"TARGET SET: {match.innings1.total_runs + 1} RUNS TO WIN"
    else:
        inn1 = match.innings1
        inn2 = match.innings2
        if inn2.total_runs > inn1.total_runs:
            result_str = f"{inn2.batting_team['name'].upper()} WON BY {10 - inn2.wickets} WICKETS"
        elif inn1.total_runs > inn2.total_runs:
            result_str = f"{inn1.batting_team['name'].upper()} WON BY {inn1.total_runs - inn2.total_runs} RUNS"
        else:
            result_str = "MATCH TIED"
            
        if potm_name:
            result_str += f" • POTM: {potm_name.upper()}"
            
    d.text((600 - get_tw(result_str, font_title)//2, 810), result_str, fill=c_navy, font=font_title)
    
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    
    return buf

# ==========================================
# 🔄 5. MATCH PROGRESSION & LOOPS
# ==========================================

async def advance_match_loop(interaction, match: CricketMatch):
    innings = match.current_innings
    
    if innings.wickets >= 10 or innings.total_balls >= match.max_balls or (match.current_innings_num == 2 and innings.total_runs > match.innings1.total_runs):
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
        if innings.wickets >= 10 or innings.total_balls >= match.max_balls or (match.current_innings_num == 2 and innings.total_runs > match.innings1.total_runs):
            await handle_innings_end(interaction, match)
            break
            
        if innings.total_balls % 6 == 0:
            new_bowler = get_smart_ai_bowler(innings, match.pitch, match.format_overs)
            if not new_bowler:
                await channel.send("🚨 **CRITICAL ERROR:** Could not find a valid bowler to continue simulation. Match has been stopped.")
                if channel.id in active_games:
                    del active_games[channel.id]
                return
            innings.current_bowler = new_bowler
            innings.over_log.clear()
            
        execute_ball_math(match)
        
        # Only print scoreboard if user chose Verbose mode
        if getattr(match, 'verbose', False) and innings.total_balls % 6 == 0:
            await channel.send(embed=render_embed_scoreboard(match))
            await asyncio.sleep(0.5)

async def handle_innings_end(interaction_context, match: CricketMatch):
    channel = interaction_context if isinstance(interaction_context, discord.TextChannel) else interaction_context.channel
    
    if match.current_innings_num == 1:
        img_buf = generate_final_score_image(match)
        file = discord.File(fp=img_buf, filename="innings1_score.png")
        embed_full = render_full_scorecard_embed(match, 1)
        
        match.current_innings_num = 2
        match.innings2 = InningsState(match.innings1.bowling_team, match.innings1.batting_team)
        match.current_innings = match.innings2
        
        await channel.send(
            f"🏁 **Innings 1 Complete!** Target set: **{match.innings1.total_runs + 1} runs** to win.\nHere is the detailed scorecard and broadcast graphic:", 
            embed=embed_full, 
            file=file
        )
        
        # Pass channel directly — no more DummyInteraction needed
        if getattr(match, 'sim_only', False):
            await channel.send("*Simulating 2nd Innings... ⚙️*")
            await loop_entire_match_simulation(channel, match)
        else:
            await prompt_new_over_bowler(channel, match)
        
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
        role_short = p["role"].split("_")[0]
        options.append(discord.SelectOption(label=p["name"], description=f"Bat: {p['bat']} | {role_short}", value=p["name"]))
        
    view = discord.ui.View(timeout=120)
    select = discord.ui.Select(placeholder="Select Next Batter...", options=options[:25])
    
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
    await channel.send(f"🏏 <@{uid}>, select the next batter to walk in:", view=view)

async def prompt_new_over_bowler(interaction, match: CricketMatch):
    innings = match.current_innings
    bowler_uid = match.get_bowler_user_id()
    channel = interaction.channel if hasattr(interaction, 'channel') else interaction
    
    if match.is_ai_game and bowler_uid == match.p2_id:
        new_bowler = get_smart_ai_bowler(innings, match.pitch, match.format_overs)
        if not new_bowler:
            await channel.send("🚨 **CRITICAL ERROR:** Could not find a valid bowler to proceed. The match cannot continue. Please use `/endmatch`.")
            return
        innings.current_bowler = new_bowler
        innings.over_log.clear()
        
        class DummyInt: pass
        dummy = DummyInt()
        dummy.channel = channel
        dummy.response = type('DR', (), {'defer': lambda: None})()
        
        await prompt_over_pacing_hub(dummy, match)
        return

    actual_bowlers = []
    for p in innings.bowling_team["players"]:
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
        await inter.response.defer()
        await prompt_over_pacing_hub(inter, match)
        
    select.callback = b_callback
    view.add_item(select)
    
    async def interaction_check(inter: discord.Interaction) -> bool:
        if inter.channel.id not in active_games or active_games[inter.channel.id] != match:
            await inter.response.send_message("❌ This match has been ended.", ephemeral=True)
            return False
        if inter.user.id != bowler_uid:
            await inter.response.send_message("Not your turn.", ephemeral=True)
            return False
        return True
    view.interaction_check = interaction_check
    
    await channel.send(f"🏏 <@{bowler_uid}>, select bowler for Over {innings.total_balls // 6 + 1}:", view=view)

async def prompt_over_pacing_hub(interaction: discord.Interaction, match: CricketMatch):
    view = OverControlHubView(match)
    embed = render_embed_scoreboard(match)
    await interaction.channel.send(f"⚡ <@{match.p1_id}> **Over Hub** - How to progress the next 6 deliveries?", embed=embed, view=view)

class OverControlHubView(discord.ui.View):
    def __init__(self, match: CricketMatch):
        super().__init__(timeout=60)
        self.match = match
        
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.channel.id not in active_games or active_games[interaction.channel.id] != self.match:
            await interaction.response.send_message("❌ This match has been ended.", ephemeral=True)
            return False
        if interaction.user.id != self.match.p1_id:
            await interaction.response.send_message("❌ Host only.", ephemeral=True)
            return False
        return True
        
    @discord.ui.button(label="Play Interactive Over", style=discord.ButtonStyle.success)
    async def play_over(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        self.match.simulation_mode = "interactive"
        await run_interactive_delivery_sequence(interaction, self.match)
        
    @discord.ui.button(label="Simulate 1 Over", style=discord.ButtonStyle.primary)
    async def sim_over(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        innings = self.match.current_innings
        start_runs = innings.total_runs; start_wkts = innings.wickets
        
        prev_mode = self.match.simulation_mode
        self.match.simulation_mode = "whole_match"
        
        for _ in range(6):
            if innings.wickets < 10 and innings.total_balls < self.match.max_balls:
                if self.match.current_innings_num == 2 and innings.total_runs > self.match.innings1.total_runs: break
                execute_ball_math(self.match)
                
        self.match.simulation_mode = prev_mode
        
        events_str = ' '.join(innings.over_log[-6:])
        await interaction.channel.send(f"⏩ **Simulated Over Complete!**\n**Timeline:** {events_str}\n**Yield:** {innings.total_runs - start_runs} Runs, {innings.wickets - start_wkts} Wickets")
        await advance_match_loop(interaction, self.match)
        
    @discord.ui.button(label="Simulate Match (Fast)", style=discord.ButtonStyle.danger)
    async def sim_match_fast(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        self.match.simulation_mode = "whole_match"
        self.match.verbose = False # Fast mode: No mid-match spam
        await loop_entire_match_simulation(interaction, self.match)

    @discord.ui.button(label="Simulate Match (Verbose)", style=discord.ButtonStyle.secondary)
    async def sim_match_verbose(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        self.match.simulation_mode = "whole_match"
        self.match.verbose = True # Verbose mode: Every over summary
        await loop_entire_match_simulation(interaction, self.match)
        
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
        if interaction.user.id != self.uid:
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
        
        if spin_type == "off":
            opts = ["Off spin", "Carrom", "Arm ball", "Doosra", "Top spin", "Mystery"]
        else:
            opts = ["Leg spin", "Googly", "Flipper", "Drifter", "Slider", "Mystery"]
            
        for idx, spin in enumerate(opts):
            row = 0 if idx < 3 else 1
            self.add_item(ActionButton(spin, discord.ButtonStyle.primary, row, "spin"))
            
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.channel.id not in active_games or active_games[interaction.channel.id] != self.match:
            await interaction.response.send_message("❌ This match has been ended.", ephemeral=True)
            return False
        if interaction.user.id != self.uid:
            await interaction.response.send_message("Not your turn.", ephemeral=True)
            return False
        return True
        
    async def process_action(self, interaction: discord.Interaction, label: str, action_type: str):
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
        if interaction.user.id != self.uid:
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
        if interaction.user.id != uid:
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
            if innings.over_log and innings.over_log[-1] == "🔴":
                innings.over_log[-1] = "⚪"
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
    
    if innings.wickets >= 10 or innings.total_balls >= match.max_balls or (match.current_innings_num == 2 and innings.total_runs > match.innings1.total_runs):
        await handle_innings_end(interaction, match)
        return
        
    if innings.total_balls > 0 and innings.total_balls % 6 == 0 and len(innings.over_log) > 0:
        await prompt_new_over_bowler(interaction, match)
        return
        
    channel = interaction.channel if hasattr(interaction, 'channel') else interaction
    
    if match.is_ai_game and match.get_bowler_user_id() == match.p2_id:
        role = innings.current_bowler["role"]
        
        if "Spin" in role:
            if "Off" in role:
                opts = ["Off spin", "Carrom", "Arm ball", "Doosra", "Top spin", "Mystery"]
            else:
                opts = ["Leg spin", "Googly", "Flipper", "Drifter", "Slider", "Mystery"]
            match.current_delivery_selection = random.choice(opts)
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
                    if innings.over_log and innings.over_log[-1] == "🔴":
                        innings.over_log[-1] = "⚪"
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
        self.t2_name = "Team 2"
        self.t2_roster = []
        self.pitch = "Flat"
        self.weather = "Clear"
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
    for line in lines[:15]:
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
        if interaction.user.id != self.state.p1_id: 
            return await interaction.response.send_message("Only Host.", ephemeral=True)
            
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
            return await interaction.response.send_message(reason, ephemeral=True)
            return await interaction.followup.send(reason, ephemeral=True)

        self.state.format_overs = val
        # 🚨 FIX: Atomic edit prevents the crash
       
        await interaction.edit_original_response(content=f"✅ Format set: **Custom ({val} overs)**", view=None)
        await ask_team1_name(self.channel, self.state)

class ImpactPlayerView(discord.ui.View):
    def __init__(self, state, channel):
        super().__init__()
        self.state = state
        self.channel = channel
    @discord.ui.button(label="Yes (Impact Player)", style=discord.ButtonStyle.success)
    async def btn_yes(self, interaction, button):
        if interaction.user.id != self.state.p1_id: return
        self.state.impact_player = True
        # 🚨 FIX: Atomic edit
        await interaction.response.edit_message(content="✅ **Impact Player rule enabled!**", view=None)
        await ask_team1_name(self.channel, self.state)
    @discord.ui.button(label="No (Standard 11)", style=discord.ButtonStyle.secondary)
    async def btn_no(self, interaction, button):
        if interaction.user.id != self.state.p1_id: return
        self.state.impact_player = False
        # 🚨 FIX: Atomic edit
        await interaction.response.edit_message(content="✅ Standard rules applied.", view=None)
        await ask_team1_name(self.channel, self.state)

# --- Step 2: Chat-Based Roster Collection Prompts ---

async def ask_team1_name(channel, state):
    await channel.send(f"🏏 <@{state.p1_id}> — Type your **team name** (e.g. `India`):\n*(Reply directly in this channel)*")
    active_setups[channel.id] = ("awaiting_team1_name", state)

async def ask_team1_xi(channel, state):
    await channel.send(f"📋 <@{state.p1_id}> — Type your **Playing XI** (one player per line):\n```text\nVirat Kohli\nRohit Sharma\n...```")
    active_setups[channel.id] = ("awaiting_team1_xi", state)

async def ask_team2_name(channel, state):
    target_id = state.p2_id if state.p2_id else state.p1_id
    await channel.send(f"🏏 <@{target_id}> — Type **Team 2's name**:\n*(Reply directly in this channel)*")
    active_setups[channel.id] = ("awaiting_team2_name", state)

async def ask_team2_xi(channel, state):
    target_id = state.p2_id if state.p2_id else state.p1_id
    await channel.send(f"📋 <@{target_id}> — Type **Team 2's Playing XI** (one player per line):\n```text\nPlayer Name\n...```")
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
        discord.SelectOption(label="Dusty — Spin Friendly", value="Dusty", emoji="🌾")
    ])
    async def pitch_cb(self, interaction, select):
        if interaction.user.id != self.state.home_team_id: return
        self.s_pitch = select.values[0]
        await interaction.response.defer()
        await self.check_proceed(interaction)

    @discord.ui.select(placeholder="🌤️ Select Weather...", row=1, options=[
        discord.SelectOption(label="Clear — Full Match", value="Clear", emoji="☀️"),
        discord.SelectOption(label="Overcast — Pace Boost", value="Overcast", emoji="☁️"),
        discord.SelectOption(label="Rain Threat — DLS/Greasy", value="Rain Threat", emoji="🌧️")
    ])
    async def weather_cb(self, interaction, select):
        if interaction.user.id != self.state.home_team_id: return
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
    t1 = {"name": state.t1_name, "players": state.t1_roster}
    t2 = {"name": state.t2_name, "players": state.t2_roster}

    match = CricketMatch(state.p1, state.p2, state.p1_id, state.p2_id, t1, t2, state.format_overs, state.pitch, state.weather)
    match.impact_player = state.impact_player
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
            await prompt_new_over_bowler(channel, match)
    else:
        await channel.send(f"🪙 **Toss Time!** <@{match.p2_id}> — call the coin!", view=TossCallView(match))

class TossCallView(discord.ui.View):
    def __init__(self, match):
        super().__init__(timeout=60)
        self.match = match
    async def handle_call(self, interaction, call):
        if interaction.user.id != self.match.p2_id: return
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
        if interaction.user.id != self.match.toss_winner:
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
        await prompt_new_over_bowler(interaction.channel, self.match)
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
        db = load_all_players_from_db()
        players, missing = parse_pasted_roster(message.content, db)
        
        req_length = 12 if state.impact_player else 11
        if missing or len(players) < req_length:
            err = f"❌ **Roster Validation Failed ({len(players)}/{req_length} Found)**\n\n"
            if players: err += f"✅ **Accepted:** {', '.join([p['name'] for p in players])}\n"
            if missing: err += f"❌ **Missing from DB:** {', '.join(missing)}\n\n"
            err += f"Please check spellings or add missing players to your CSV, then type your full list again."
            return await message.channel.send(err)

        del active_setups[channel_id]
        xi_text = format_xi_display(players)
        await message.channel.send(f"📋 **{state.t1_name} XI** Verified:\n{xi_text}\n\nIs this correct?", view=Team1VerifyView(state, message.channel, players))

    elif stage == "awaiting_team2_name":
        target_id = state.p2_id if state.p2_id else state.p1_id
        if message.author.id != target_id: return
        state.t2_name = message.content.strip()[:30]
        del active_setups[channel_id]
        await message.channel.send(f"✅ Team 2 name set: **{state.t2_name}**")
        
        if state.p2_id is None and not getattr(state, 'sim_only', False):
            state.t2_roster = TEAMS_DATA["Team 2"]["players"]
            await message.channel.send(f"🤖 AI team **{state.t2_name}** will use the built-in roster.")
            await ask_pitch_and_weather(message.channel, state)
        else:
            await ask_team2_xi(message.channel, state)

    elif stage == "awaiting_team2_xi":
        target_id = state.p2_id if state.p2_id else state.p1_id
        if message.author.id != target_id: return
        db = load_all_players_from_db()
        players, missing = parse_pasted_roster(message.content, db)
        
        req_length = 12 if state.impact_player else 11
        if missing or len(players) < req_length:
            err = f"❌ **Roster Validation Failed ({len(players)}/{req_length} Found)**\n\n"
            if players: err += f"✅ **Accepted:** {', '.join([p['name'] for p in players])}\n"
            if missing: err += f"❌ **Missing from DB:** {', '.join(missing)}\n\n"
            err += f"Please check spellings or add missing players to your CSV, then type your full list again."
            return await message.channel.send(err)

        del active_setups[channel_id]
        xi_text = format_xi_display(players)
        await message.channel.send(f"📋 **{state.t2_name} XI** Verified:\n{xi_text}\n\nIs this correct?", view=Team2VerifyView(state, message.channel, players))

    await bot.process_commands(message)

# --- The Slash Command Initialization ---

@bot.tree.command(name="match", description="Start a new Cricket Match simulation.")
async def match_cmd(interaction: discord.Interaction, opponent: discord.Member = None):
    
    # Instantly defer to prevent 10062 timeouts while Cloud DB wakes up
    await interaction.response.defer()
    
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
    
    # 🚨 PULL FROM CLOUD DATABASE
    all_players = load_all_players_from_db()
    player_names = [p["name"] for p in all_players]
    
    if not all_players:
        return await interaction.followup.send("❌ Error: Cloud DB is empty or disconnected.")
        
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
                
            # 🚨 PUSH TO CLOUD DATABASE
            try:
                with get_db() as conn:
                    with conn.cursor() as cur:
                        cur.execute("SELECT name FROM players WHERE LOWER(name) = LOWER(%s)", (self.n,))
                        if cur.fetchone():
                            return await inter.followup.send(f"❌ Cancelled: `{self.n}` already exists in DB!", ephemeral=True)
                            
                        cur.execute("INSERT INTO players (name, bat, bowl, role, archetype) VALUES (%s, %s, %s, %s, %s)",
                                    (self.n, self.bat, self.bowl, self.s_role, self.s_arch))
                    conn.commit()
            except Exception as e:
                return await inter.followup.send(f"❌ DB Error: {e}", ephemeral=True)
                
            await inter.followup.send(f"✅ Saved `{self.n}` to Cloud DB!", ephemeral=True)
            await log_db_update("Player Added", self.n, inter.user, f"Bat: {self.bat} | Bowl: {self.bowl}\nRole: {self.s_role}\nArchetype: {self.s_arch}")

@bot.tree.command(name="addplayer", description="[ADMIN] Add player to Cloud DB.")
async def add_p_cmd(interaction: discord.Interaction):
    admins = load_auth_admins()
    if interaction.user.id != ADMIN_DISCORD_ID and str(interaction.user.id) not in admins: 
        return await interaction.response.send_message("❌ Access Denied: Admin only.", ephemeral=True)
        
    await interaction.response.send_modal(AddPlayerModal())

@bot.tree.command(name="set_user_tier", description="[OWNER] Assign a subscription tier to a user.")
@app_commands.choices(tier=[
    app_commands.Choice(name="Basic (1 Sim/Day | T20/ODI)", value="Basic"),
    app_commands.Choice(name="Standard (1 Sim/Day | All)", value="Standard"),
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
    
    admins = load_auth_admins()
    uid = str(user.id)
    with get_db() as conn:
        with conn.cursor() as cur:
            if uid in admins:
                cur.execute("DELETE FROM auth_admins WHERE admin_id = %s", (uid,))
                msg = f"🚫 Admin permissions **revoked** for {user.mention}."
            else:
                cur.execute("INSERT INTO auth_admins (admin_id) VALUES (%s)", (uid,))
                msg = f"✅ {user.mention} is now an **Admin** and can add/update players."
        conn.commit()
        load_auth_admins(force=True)
    await interaction.response.send_message(msg, ephemeral=True)

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
                
            # 🚨 UPDATE THE CLOUD DATABASE
            try:
                with get_db() as conn:
                    with conn.cursor() as cur:
                        cur.execute("""
                            UPDATE players 
                            SET name = %s, bat = %s, bowl = %s, role = %s, archetype = %s 
                            WHERE name = %s
                        """, (self.new_name, self.bat, self.bowl, self.s_role, self.s_arch, self.old_name))
                    conn.commit()
            except Exception as e:
                return await inter.followup.send(f"❌ DB Error: {e}", ephemeral=True)
                
            await inter.followup.send(f"✅ Successfully updated `{self.new_name}` in the Cloud DB!", ephemeral=True)
            change_str = f"Old Name: {self.old_name}\n" if self.old_name != self.new_name else ""
            change_str += f"Bat: {self.bat} | Bowl: {self.bowl}\nRole: {self.s_role}\nArchetype: {self.s_arch}"
            await log_db_update("Player Updated", self.new_name, inter.user, change_str)

@bot.tree.command(name="updateplayer", description="[ADMIN] Update player stats in DB.")
async def up_p_cmd(interaction: discord.Interaction, name: str):
    admins = load_auth_admins()
    if interaction.user.id != ADMIN_DISCORD_ID and str(interaction.user.id) not in admins:
        return await interaction.response.send_message("❌ Access Denied: Admin only.", ephemeral=True)
        
    all_p = load_all_players_from_db()
    cur_player = next((p for p in all_p if p["name"].lower() == name.strip().lower()), None)
        
    if not cur_player:
        return await interaction.response.send_message(f"❌ `{name}` not found in the database.", ephemeral=True)
        
    await interaction.response.send_modal(UpdatePlayerModal(cur_player, all_p))

@bot.tree.command(name="cleanduplicates", description="[ADMIN] Find and remove duplicate players (case-insensitive) from DB.")
async def clean_dup_cmd(interaction: discord.Interaction):
    admins = load_auth_admins()
    if interaction.user.id != ADMIN_DISCORD_ID and str(interaction.user.id) not in admins:
        return await interaction.response.send_message("❌ Access Denied: Admin only.", ephemeral=True)
        
    await interaction.response.defer(ephemeral=True)
    try:
        removed_count = 0
        removed_names = []
        with get_db() as conn:
            with conn.cursor() as cur:
                # Find duplicates based on lowercase trimmed names
                cur.execute("""
                    SELECT ARRAY_AGG(name)
                    FROM players
                    GROUP BY LOWER(TRIM(name))
                    HAVING COUNT(*) > 1
                """)
                duplicates = cur.fetchall()
                for row in duplicates:
                    names = row[0]
                    # Keep the first one, delete the rest
                    to_delete = names[1:] 
                    cur.execute("DELETE FROM players WHERE name = ANY(%s)", (to_delete,))
                    removed_count += len(to_delete)
                    removed_names.extend(to_delete)
            conn.commit()
            
        if removed_count > 0:
            await interaction.followup.send(f"✅ Removed {removed_count} duplicate player(s):\n" + ", ".join(removed_names))
            await log_db_update("Database Cleaned", "Duplicates Removed", interaction.user, f"Removed {removed_count} duplicates:\n{', '.join(removed_names)}")
        else:
            await interaction.followup.send("✅ Database is already clean. No duplicate players found.")
    except Exception as e:
        await interaction.followup.send(f"❌ DB Error: {e}")

@bot.tree.command(name="deleteplayer", description="[ADMIN] Delete a specific player from the Cloud DB.")
async def del_p_cmd(interaction: discord.Interaction, name: str):
    admins = load_auth_admins()
    if interaction.user.id != ADMIN_DISCORD_ID and str(interaction.user.id) not in admins:
        return await interaction.response.send_message("❌ Access Denied: Admin only.", ephemeral=True)
        
    await interaction.response.defer(ephemeral=True)
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT name FROM players WHERE LOWER(TRIM(name)) = LOWER(TRIM(%s))", (name,))
                found = cur.fetchall()
                if not found:
                    return await interaction.followup.send(f"❌ Could not find `{name}` in the database.")
                
                to_delete = [row[0] for row in found]
                cur.execute("DELETE FROM players WHERE name = ANY(%s)", (to_delete,))
            conn.commit()
            
        await interaction.followup.send(f"✅ Successfully deleted `{', '.join(to_delete)}` from the database.")
        await log_db_update("Player Deleted", name, interaction.user, f"Removed match(es): {', '.join(to_delete)}")
    except Exception as e:
        await interaction.followup.send(f"❌ DB Error: {e}")

# ==========================================
# 🚀 STARTUP SEQUENCE
# ==========================================
keep_alive()

TOKEN = os.environ.get("DISCORD_TOKEN")
if not TOKEN:
    print("🚨 CRITICAL ERROR: DISCORD_TOKEN environment variable is missing from Render!")
else:
    bot.run(TOKEN)
