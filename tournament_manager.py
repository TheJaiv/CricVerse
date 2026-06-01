import discord
from discord import app_commands
from discord.ext import commands
import itertools
import difflib
import asyncio
from subscription_manager import DB_CACHE, async_save_to_bin, get_all_players

def get_server_tournament(server_id: str):
    if "tournaments" not in DB_CACHE:
        DB_CACHE["tournaments"] = []
    return next((t for t in DB_CACHE["tournaments"] if t.get("server_id") == server_id), None)

def save_tournament(t_data):
    if "tournaments" not in DB_CACHE:
        DB_CACHE["tournaments"] = []
    tourneys = DB_CACHE["tournaments"]
    for i, t in enumerate(tourneys):
        if t.get("server_id") == t_data["server_id"]:
            tourneys[i] = t_data
            async_save_to_bin()
            return
    tourneys.append(t_data)
    async_save_to_bin()

class TournamentCog(commands.GroupCog, group_name="tournament"):
    def __init__(self, bot):
        self.bot = bot

    # Helper to authenticate Tournament Managers safely
    def is_manager(self, interaction: discord.Interaction, tourney):
        if interaction.user.id == 1087369198801526836: return True # Global Admin Override
        if interaction.user.guild_permissions.administrator: return True # Server Admin Override
        return str(interaction.user.id) in tourney.get("managers", [])

    @app_commands.command(name="create", description="[ADMIN] Create a new tournament for this server.")
    async def create(self, interaction: discord.Interaction, name: str):
        if not interaction.user.guild_permissions.administrator and interaction.user.id != 1087369198801526836:
            return await interaction.response.send_message("❌ Only Server Admins can initialize a tournament.", ephemeral=True)
            
        server_id = str(interaction.guild.id)
        if get_server_tournament(server_id):
            return await interaction.response.send_message("❌ A tournament already exists in this server! Use `/tournament status` to check.", ephemeral=True)
            
        t_data = {
            "server_id": server_id,
            "name": name,
            "managers": [str(interaction.user.id)],
            "teams": [],
            "status": "registration", # Modes: registration, active, completed
            "schedule": [],
            "current_match_idx": 0,
            "stats": {}
        }
        save_tournament(t_data)
        await interaction.response.send_message(f"🏆 **Tournament Created:** `{name}`\nYou have been automatically assigned as a Manager.\nUse `/tournament add_manager` or `/tournament add_team` to get started!")

    @app_commands.command(name="add_manager", description="[MANAGER] Assign a tournament manager.")
    async def add_manager(self, interaction: discord.Interaction, user: discord.Member):
        server_id = str(interaction.guild.id)
        tourney = get_server_tournament(server_id)
        
        if not tourney: return await interaction.response.send_message("❌ No tournament exists here.", ephemeral=True)
        if not self.is_manager(interaction, tourney): return await interaction.response.send_message("❌ You are not a Tournament Manager.", ephemeral=True)
        
        uid = str(user.id)
        if uid not in tourney["managers"]:
            tourney["managers"].append(uid)
            save_tournament(tourney)
        await interaction.response.send_message(f"✅ {user.mention} is now a Tournament Manager!")

    @app_commands.command(name="add_team", description="[MANAGER] Add a team and assign a Team Owner.")
    async def add_team(self, interaction: discord.Interaction, team_name: str, owner: discord.Member):
        server_id = str(interaction.guild.id)
        tourney = get_server_tournament(server_id)
        
        if not tourney: return await interaction.response.send_message("❌ No tournament exists.", ephemeral=True)
        if not self.is_manager(interaction, tourney): return await interaction.response.send_message("❌ Managers only.", ephemeral=True)
        if tourney["status"] != "registration": return await interaction.response.send_message("❌ Cannot add teams after tournament has started.", ephemeral=True)
        
        for t in tourney["teams"]:
            if t["name"].lower() == team_name.lower():
                return await interaction.response.send_message("❌ Team name already exists.", ephemeral=True)
            if t["owner_id"] == str(owner.id):
                return await interaction.response.send_message(f"❌ {owner.mention} already owns a team.", ephemeral=True)
                
        tourney["teams"].append({
            "name": team_name,
            "owner_id": str(owner.id),
            "squad": []
        })
        save_tournament(tourney)
        await interaction.response.send_message(f"✅ Team **{team_name}** added!\n👤 Owner: {owner.mention}\n*The owner can now use `/tournament submit_squad` to register their players.*")

    @app_commands.command(name="submit_squad", description="[TEAM OWNER] Submit your tournament squad (15 players).")
    async def submit_squad(self, interaction: discord.Interaction):
        server_id = str(interaction.guild.id)
        tourney = get_server_tournament(server_id)
        
        if not tourney: return await interaction.response.send_message("❌ No tournament exists.", ephemeral=True)
        if tourney["status"] != "registration": return await interaction.response.send_message("❌ Registration is closed.", ephemeral=True)
        
        team = next((t for t in tourney["teams"] if t["owner_id"] == str(interaction.user.id)), None)
        if not team: return await interaction.response.send_message("❌ You are not a Team Owner in this tournament.", ephemeral=True)
        
        await interaction.response.send_message("📋 Please reply to this message with your **15 Player Squad** (One player name per line, matching the database). You have 3 minutes.", ephemeral=True)
        
        def check(m):
            return m.author.id == interaction.user.id and m.channel.id == interaction.channel.id
            
        try:
            msg = await self.bot.wait_for('message', timeout=180.0, check=check)
        except asyncio.TimeoutError:
            return await interaction.followup.send("⏳ Time expired. Please run `/tournament submit_squad` again.", ephemeral=True)
            
        db_players = get_all_players()
        db_map = {p["name"].lower(): p for p in db_players}
        db_names_list = list(db_map.keys())
        
        found_players = []
        missing = []
        seen = set()
        
        lines = [l.strip() for l in msg.content.split("\n") if l.strip()]
        for line in lines[:18]: # Allows up to 18 members in a squad
            q = line.lower()
            match = db_map.get(q)
            if not match:
                fuzz = difflib.get_close_matches(q, db_names_list, n=1, cutoff=0.6)
                if fuzz: match = db_map[fuzz[0]]
            
            if match:
                if match["name"] not in seen:
                    found_players.append(match)
                    seen.add(match["name"])
            else:
                missing.append(line)
                
        if missing or len(found_players) < 11:
            err = f"❌ **Roster Invalid ({len(found_players)}/11 Minimum Found)**\n"
            if missing: err += f"Missing: {', '.join(missing)}\n"
            err += "Please fix the names and try `/tournament submit_squad` again."
            return await msg.reply(err)
            
        team["squad"] = found_players
        save_tournament(tourney)
        await msg.reply(f"✅ **Squad Verified and Saved for {team['name']}!**\nRegistered {len(found_players)} players.")

    @app_commands.command(name="start", description="[MANAGER] Lock registration and generate Round Robin schedule.")
    async def start(self, interaction: discord.Interaction):
        server_id = str(interaction.guild.id)
        tourney = get_server_tournament(server_id)
        
        if not tourney: return await interaction.response.send_message("❌ No tournament exists.", ephemeral=True)
        if not self.is_manager(interaction, tourney): return await interaction.response.send_message("❌ Managers only.", ephemeral=True)
        if tourney["status"] != "registration": return await interaction.response.send_message("❌ Tournament already started.", ephemeral=True)
        
        if len(tourney["teams"]) < 2:
            return await interaction.response.send_message("❌ Need at least 2 teams.", ephemeral=True)
            
        for t in tourney["teams"]:
            if len(t.get("squad", [])) < 11:
                return await interaction.response.send_message(f"❌ Team **{t['name']}** does not have a valid squad yet.", ephemeral=True)
                
        teams = [t["name"] for t in tourney["teams"]]
        matchups = list(itertools.combinations(teams, 2))
        
        import random
        random.shuffle(matchups) # Shuffles to create a dynamic round robin schedule
        
        schedule = [{"match_id": i + 1, "team1": t1, "team2": t2, "status": "pending", "result": None} for i, (t1, t2) in enumerate(matchups)]
            
        tourney["schedule"] = schedule
        tourney["status"] = "active"
        tourney["current_match_idx"] = 0
        save_tournament(tourney)
        
        await interaction.response.send_message(f"🏆 **TOURNAMENT STARTED: {tourney['name']}!**\nGenerated **{len(schedule)} matches** in the Round Robin stage.\nUse `/tournament status` to view it!")

    @app_commands.command(name="status", description="View the current tournament schedule and standings.")
    async def status(self, interaction: discord.Interaction):
        server_id = str(interaction.guild.id)
        tourney = get_server_tournament(server_id)
        
        if not tourney:
            return await interaction.response.send_message("❌ No tournament exists in this server.", ephemeral=True)
            
        embed = discord.Embed(title=f"🏆 Tournament: {tourney['name']}", color=discord.Color.gold())
        
        if tourney["status"] == "registration":
            embed.description = "📝 **Registration Phase**"
            teams_str = ""
            for t in tourney["teams"]:
                squad_len = len(t.get("squad", []))
                teams_str += f"• **{t['name']}** (<@{t['owner_id']}>) - {squad_len}/15 Players\n"
            if not teams_str: teams_str = "No teams added yet."
            embed.add_field(name="Registered Teams", value=teams_str, inline=False)
            
        elif tourney["status"] == "active":
            embed.description = "🔥 **Active Phase**"
            schedule = tourney.get("schedule", [])
            pending = [m for m in schedule if m["status"] == "pending"]
            sched_str = ""
            for m in pending[:5]:
                sched_str += f"Match {m['match_id']}: **{m['team1']}** vs **{m['team2']}**\n"
            if not sched_str: sched_str = "All matches completed."
            embed.add_field(name="Upcoming Matches", value=sched_str, inline=False)
            
        await interaction.response.send_message(embed=embed)