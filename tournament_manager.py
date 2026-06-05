import discord
from discord import app_commands
from discord.ext import commands
import itertools
import difflib
import io
from PIL import Image, ImageDraw, ImageFont
import asyncio
from subscription_manager import DB_CACHE, async_save_to_bin, get_all_players, get_tier_status

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

def get_tournament_standings(tourney):
    teams = {t["name"]: {"P":0, "W":0, "L":0, "T":0, "Pts":0, "RF":0, "OF":0.0, "RA":0, "OA":0.0} for t in tourney["teams"]}
    for m in tourney.get("schedule", []):
        # Only count Group Stage (integer rounds) for the Points Table!
        if m["status"] == "completed" and "result" in m and isinstance(m.get("round"), int):
            res = m["result"]
            t1, t2 = m["team1"], m["team2"]
            if t1 not in teams: teams[t1] = {"P":0, "W":0, "L":0, "T":0, "Pts":0, "RF":0, "OF":0.0, "RA":0, "OA":0.0}
            if t2 not in teams: teams[t2] = {"P":0, "W":0, "L":0, "T":0, "Pts":0, "RF":0, "OF":0.0, "RA":0, "OA":0.0}
            
            teams[t1]["P"] += 1; teams[t2]["P"] += 1
            
            if res["winner"] == "TIE":
                teams[t1]["T"] += 1; teams[t2]["T"] += 1
                teams[t1]["Pts"] += 1; teams[t2]["Pts"] += 1
            elif res["winner"] == t1:
                teams[t1]["W"] += 1; teams[t2]["L"] += 1; teams[t1]["Pts"] += 2
            else:
                teams[t2]["W"] += 1; teams[t1]["L"] += 1; teams[t2]["Pts"] += 2
                
            def get_overs(w, b, fmt): return float(fmt) if w >= 10 else b / 6.0
            t1_o = get_overs(res["t1_wickets"], res["t1_balls"], res["format_overs"])
            t2_o = get_overs(res["t2_wickets"], res["t2_balls"], res["format_overs"])
            
            teams[t1]["RF"] += res["t1_runs"]; teams[t1]["OF"] += t1_o
            teams[t1]["RA"] += res["t2_runs"]; teams[t1]["OA"] += t2_o
            teams[t2]["RF"] += res["t2_runs"]; teams[t2]["OF"] += t2_o
            teams[t2]["RA"] += res["t1_runs"]; teams[t2]["OA"] += t1_o
            
    for t_name, data in teams.items():
        data["NRR"] = ((data["RF"]/data["OF"]) if data["OF"] > 0 else 0) - ((data["RA"]/data["OA"]) if data["OA"] > 0 else 0)
        
    return sorted(teams.items(), key=lambda x: (x[1]["Pts"], x[1]["NRR"]), reverse=True)

class TournamentCog(commands.GroupCog, group_name="tournament"):
    def __init__(self, bot):
        self.bot = bot

    # Helper to authenticate Tournament Managers safely
    def is_manager(self, interaction: discord.Interaction, tourney):
        if interaction.user.id == 1087369198801526836: return True # Global Admin Override
        if interaction.user.guild_permissions.administrator: return True # Server Admin Override
        return str(interaction.user.id) in tourney.get("managers", [])

    @app_commands.command(name="create", description="[ADMIN] Create a new tournament for this server.")
    @app_commands.choices(format=[
        app_commands.Choice(name="T20 (20 Overs)", value="20"),
        app_commands.Choice(name="ODI (50 Overs)", value="50"),
        app_commands.Choice(name="Test (90 Overs/Inn)", value="90"),
        app_commands.Choice(name="Custom Format", value="custom")
    ])
    async def create(self, interaction: discord.Interaction, name: str, format: app_commands.Choice[str], min_squad: int = 11, max_squad: int = 15, impact_player: bool = False, custom_overs: int = None):
        if not interaction.user.guild_permissions.administrator and interaction.user.id != 1087369198801526836:
            return await interaction.response.send_message("❌ Only Server Admins can initialize a tournament.", ephemeral=True)
            
        server_id = str(interaction.guild.id)
        
        _, _, _, s_tier, _ = get_tier_status(str(interaction.user.id), server_id)
        if s_tier not in ["Gold", "Diamond"]:
            return await interaction.response.send_message("❌ **Access Denied:** Only servers with an active **Gold** or **Diamond** tier can host tournaments! Contact the bot owner to upgrade.", ephemeral=True)

        if get_server_tournament(server_id):
            return await interaction.response.send_message("❌ A tournament already exists in this server! Use `/tournament status` to check.", ephemeral=True)
            
        if format.value == "custom" and not custom_overs:
            return await interaction.response.send_message("❌ You must provide `custom_overs` if selecting Custom Format.", ephemeral=True)
            
        if format.value != "custom": custom_overs = int(format.value)
        if min_squad < 11: return await interaction.response.send_message("❌ Minimum squad size must be at least 11.", ephemeral=True)
        if impact_player and min_squad < 12: return await interaction.response.send_message("❌ Minimum squad size must be at least 12 if Impact Player is enabled.", ephemeral=True)
        if max_squad < min_squad: return await interaction.response.send_message("❌ Max squad size cannot be less than Min squad size.", ephemeral=True)
            
        t_data = {
            "server_id": server_id,
            "name": name,
            "managers": [str(interaction.user.id)],
            "teams": [],
            "status": "registration", # Modes: registration, active, completed
            "schedule": [],
            "current_match_idx": 0,
            "stats": {},
            "format_overs": custom_overs,
            "min_squad": min_squad,
            "max_squad": max_squad,
            "impact_player": impact_player
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

    @app_commands.command(name="remove_team", description="[MANAGER] Remove a team from the tournament.")
    async def remove_team(self, interaction: discord.Interaction, team_name: str):
        server_id = str(interaction.guild.id)
        tourney = get_server_tournament(server_id)
        
        if not tourney: return await interaction.response.send_message("❌ No tournament exists.", ephemeral=True)
        if not self.is_manager(interaction, tourney): return await interaction.response.send_message("❌ Managers only.", ephemeral=True)
        if tourney["status"] != "registration": return await interaction.response.send_message("❌ Cannot remove teams after the tournament has started.", ephemeral=True)
        
        team_idx = next((i for i, t in enumerate(tourney["teams"]) if t["name"].lower() == team_name.lower()), None)
        if team_idx is None:
            return await interaction.response.send_message(f"❌ Team **{team_name}** not found.", ephemeral=True)
            
        del tourney["teams"][team_idx]
        save_tournament(tourney)
        await interaction.response.send_message(f"✅ Team **{team_name}** has been successfully removed from the tournament.")

    @app_commands.command(name="replace_player", description="[MANAGER] Replace a player in a team's squad.")
    async def replace_player(self, interaction: discord.Interaction, team_name: str, out_player: str, in_player: str):
        server_id = str(interaction.guild.id)
        tourney = get_server_tournament(server_id)
        
        if not tourney: return await interaction.response.send_message("❌ No tournament exists.", ephemeral=True)
        if not self.is_manager(interaction, tourney): return await interaction.response.send_message("❌ Managers only.", ephemeral=True)
        
        team = next((t for t in tourney["teams"] if t["name"].lower() == team_name.lower()), None)
        if not team: return await interaction.response.send_message(f"❌ Team '{team_name}' not found.", ephemeral=True)
        
        if not team.get("squad"):
            return await interaction.response.send_message(f"❌ Team '{team_name}' has no squad submitted yet.", ephemeral=True)
            
        old_p = next((p for p in team["squad"] if p["name"].lower() == out_player.lower()), None)
        if not old_p:
            close = difflib.get_close_matches(out_player, [p["name"] for p in team["squad"]], n=1, cutoff=0.5)
            if close: old_p = next(p for p in team["squad"] if p["name"] == close[0])
            else: return await interaction.response.send_message(f"❌ Player '{out_player}' not found in team '{team_name}'.", ephemeral=True)
            
        db_players = get_all_players()
        new_p = next((p for p in db_players if p["name"].lower() == in_player.lower()), None)
        if not new_p:
            close = difflib.get_close_matches(in_player, [p["name"] for p in db_players], n=1, cutoff=0.6)
            if close: new_p = next(p for p in db_players if p["name"] == close[0])
            else: return await interaction.response.send_message(f"❌ Player '{in_player}' not found in the global database.", ephemeral=True)
            
        if any(p["name"] == new_p["name"] for p in team["squad"]):
            return await interaction.response.send_message(f"❌ '{new_p['name']}' is already in the squad.", ephemeral=True)
            
        idx = team["squad"].index(old_p)
        team["squad"][idx] = new_p
        
        save_tournament(tourney)
        await interaction.response.send_message(f"✅ **Squad Updated for {team['name']}:**\n🔴 OUT: {old_p['name']}\n🟢 IN: {new_p['name']}")

    @app_commands.command(name="submit_squad", description="[OWNER/MANAGER] Submit a tournament squad (15 players).")
    async def submit_squad(self, interaction: discord.Interaction, team_name: str = None):
        server_id = str(interaction.guild.id)
        tourney = get_server_tournament(server_id)
        
        if not tourney: return await interaction.response.send_message("❌ No tournament exists.", ephemeral=True)
        if tourney["status"] != "registration": return await interaction.response.send_message("❌ Registration is closed.", ephemeral=True)
        
        is_mgr = self.is_manager(interaction, tourney)
        
        if team_name:
            if not is_mgr:
                return await interaction.response.send_message("❌ Only Managers can use the team_name parameter to submit for others.", ephemeral=True)
            team = next((t for t in tourney["teams"] if t["name"].lower() == team_name.lower()), None)
            if not team: return await interaction.response.send_message(f"❌ Team '{team_name}' not found.", ephemeral=True)
        else:
            team = next((t for t in tourney["teams"] if t["owner_id"] == str(interaction.user.id)), None)
            if not team: return await interaction.response.send_message("❌ You do not own a team. Managers must provide the `team_name` parameter.", ephemeral=True)
        
        min_s = tourney.get("min_squad", 11)
        max_s = tourney.get("max_squad", 15)
        await interaction.response.send_message(f"📋 Please reply to this message with the **{min_s} to {max_s} Player Squad** for **{team['name']}** (One player name per line). You have 3 minutes.", ephemeral=True)
        
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
        for line in lines[:(max_s + 3)]: # Allow slight buffer for duplicates/mistakes
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
            
        min_s = tourney.get("min_squad", 11)
        for t in tourney["teams"]:
            if len(t.get("squad", [])) < min_s:
                return await interaction.response.send_message(f"❌ Team **{t['name']}** does not have a valid squad yet.", ephemeral=True)
                
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
            teams.insert(1, teams.pop()) # Standard Circle Method Rotation
            
        schedule = [{"match_id": i + 1, "round": m["round"], "team1": m["team1"], "team2": m["team2"], "status": "pending", "result": None} for i, m in enumerate(matchups)]
            
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
                    return await interaction.response.send_message(embed=discord.Embed(title=f"🏆 Tournament: {tourney['name']}", description="🏁 **Group Stage Completed!**\nUse `/tournament generate_knockouts` to begin the Semi-Finals.", color=discord.Color.gold()))
                else:
                    return await interaction.response.send_message(embed=discord.Embed(title=f"🏆 Tournament: {tourney['name']}", description="🏁 **All matches are completed!**", color=discord.Color.gold()))
                
            embed.description = f"🔥 **Active Phase**\nUse `/tournament play <match_id>` to launch your matches!"
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
            embed.description = f"🏆 **TOURNAMENT COMPLETED!**\n👑 **Champions: {winner}**\n\nCheck `/tournament leaderboard` for top performers!"
            
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="generate_knockouts", description="[MANAGER] Generate Knockouts (Semi-Finals) for Top 4 teams.")
    async def generate_knockouts(self, interaction: discord.Interaction):
        server_id = str(interaction.guild.id)
        tourney = get_server_tournament(server_id)
        if not tourney: return await interaction.response.send_message("❌ No tournament exists.", ephemeral=True)
        if not self.is_manager(interaction, tourney): return await interaction.response.send_message("❌ Managers only.", ephemeral=True)
        if tourney["status"] != "active": return await interaction.response.send_message("❌ Tournament is not active.", ephemeral=True)
        
        gs_matches = [m for m in tourney["schedule"] if isinstance(m.get("round"), int)]
        if any(m["status"] == "pending" for m in gs_matches):
            return await interaction.response.send_message("❌ Cannot generate knockouts until all Group Stage matches are completed.", ephemeral=True)
            
        if any(not isinstance(m.get("round"), int) for m in tourney["schedule"]):
            return await interaction.response.send_message("❌ Knockouts have already been generated.", ephemeral=True)
            
        standings = get_tournament_standings(tourney)
        real_teams = [t[0] for t in standings if t[0] != "BYE"]
        
        if len(real_teams) < 4:
            return await interaction.response.send_message("❌ Need at least 4 teams to play Semi-Finals.", ephemeral=True)
            
        top4 = real_teams[:4]
        
        sf1 = {"match_id": len(tourney["schedule"]) + 1, "round": "Semi-Final 1", "team1": top4[0], "team2": top4[3], "status": "pending", "result": None}
        sf2 = {"match_id": len(tourney["schedule"]) + 2, "round": "Semi-Final 2", "team1": top4[1], "team2": top4[2], "status": "pending", "result": None}
        
        tourney["schedule"].extend([sf1, sf2])
        save_tournament(tourney)
        
        await interaction.response.send_message(f"🔥 **Knockout Stage Set!**\n**Semi-Final 1:** {top4[0]} vs {top4[3]}\n**Semi-Final 2:** {top4[1]} vs {top4[2]}\n\nUse `/tournament play_next` to begin!")

    @app_commands.command(name="force_delete", description="[OWNER] Forcefully delete a server's tournament.")
    async def force_delete(self, interaction: discord.Interaction):
        if interaction.user.id != 1087369198801526836:
            return await interaction.response.send_message("❌ Owner only.", ephemeral=True)
            
        server_id = str(interaction.guild.id)
        if not get_server_tournament(server_id):
            return await interaction.response.send_message("❌ No tournament exists in this server.", ephemeral=True)
            
        DB_CACHE["tournaments"] = [t for t in DB_CACHE["tournaments"] if t.get("server_id") != server_id]
        async_save_to_bin()
        await interaction.response.send_message("🗑️ **Tournament Successfully Deleted.** You can now create a new one.")

    @app_commands.command(name="set_theme", description="[OWNER] Set a custom image theme for this server's tournament.")
    async def set_theme(self, interaction: discord.Interaction, theme_name: str):
        if interaction.user.id != 1087369198801526836:
            return await interaction.response.send_message("❌ Owner only.", ephemeral=True)
            
        server_id = str(interaction.guild.id)
        tourney = get_server_tournament(server_id)
        
        if not tourney:
            return await interaction.response.send_message("❌ No tournament exists in this server.", ephemeral=True)
            
        tourney["theme"] = theme_name
        save_tournament(tourney)
        await interaction.response.send_message(f"✅ Tournament theme set to `{theme_name}` for this server.", ephemeral=True)

    @app_commands.command(name="play_next", description="[MANAGER] Launch the next pending tournament match in this channel.")
    async def play_next(self, interaction: discord.Interaction):
        server_id = str(interaction.guild.id)
        tourney = get_server_tournament(server_id)
        
        if not tourney: return await interaction.response.send_message("❌ No tournament exists.", ephemeral=True)
        if not self.is_manager(interaction, tourney): return await interaction.response.send_message("❌ Managers only.", ephemeral=True)
        if tourney["status"] != "active": return await interaction.response.send_message("❌ Tournament is not active.", ephemeral=True)
        
        schedule = tourney.get("schedule", [])
        current_round = next((m["round"] for m in schedule if m["status"] == "pending"), None)
        
        pending = next((m for m in schedule if m["status"] == "pending" and m["round"] == current_round), None)
        if not pending:
            return await interaction.response.send_message("🏆 All matches have been completed!", ephemeral=True)
            
        r_label = f"Round {current_round}" if isinstance(current_round, int) else current_round
        await interaction.response.send_message(f"🚀 **Launching {r_label} — Match {pending['match_id']}...**")
        self.bot.dispatch("start_tournament_match", interaction.channel, interaction.user.id, tourney, pending)

    @app_commands.command(name="play", description="[MANAGER] Launch a specific tournament match by its ID.")
    async def play_match(self, interaction: discord.Interaction, match_id: int):
        server_id = str(interaction.guild.id)
        tourney = get_server_tournament(server_id)
        
        if not tourney: return await interaction.response.send_message("❌ No tournament exists.", ephemeral=True)
        if not self.is_manager(interaction, tourney): return await interaction.response.send_message("❌ Managers only.", ephemeral=True)
        if tourney["status"] != "active": return await interaction.response.send_message("❌ Tournament is not active.", ephemeral=True)
        
        match = next((m for m in tourney.get("schedule", []) if m["match_id"] == match_id), None)
        if not match:
            return await interaction.response.send_message(f"❌ Match ID {match_id} does not exist.", ephemeral=True)
        if match["status"] != "pending":
            return await interaction.response.send_message(f"❌ Match {match_id} is already completed.", ephemeral=True)
            
        r_label = f"Round {match['round']}" if isinstance(match['round'], int) else match['round']
        await interaction.response.send_message(f"🚀 **Manually Launching Match {match['match_id']} ({r_label})...**")
        self.bot.dispatch("start_tournament_match", interaction.channel, interaction.user.id, tourney, match)

    @app_commands.command(name="next_match", description="[OWNER] Automatically launch your team's next pending match.")
    async def next_match(self, interaction: discord.Interaction):
        server_id = str(interaction.guild.id)
        tourney = get_server_tournament(server_id)
        
        if not tourney: return await interaction.response.send_message("❌ No tournament exists.", ephemeral=True)
        if tourney["status"] != "active": return await interaction.response.send_message("❌ Tournament is not active.", ephemeral=True)
        
        my_team = next((t for t in tourney["teams"] if t["owner_id"] == str(interaction.user.id)), None)
        if not my_team:
            return await interaction.response.send_message("❌ You are not a Team Owner in this tournament.", ephemeral=True)
            
        my_team_name = my_team["name"]
        my_matches = [m for m in tourney.get("schedule", []) if m["status"] == "pending" and (m["team1"] == my_team_name or m["team2"] == my_team_name)]
        
        if not my_matches:
            return await interaction.response.send_message(f"✅ Your team (**{my_team_name}**) has no pending matches right now!", ephemeral=True)
            
        match = my_matches[0]
        r_label = f"Round {match['round']}" if isinstance(match['round'], int) else match['round']
        
        await interaction.response.send_message(f"🚀 **Launching Next Match for {my_team_name}: Match {match['match_id']} ({r_label})...**")
        self.bot.dispatch("start_tournament_match", interaction.channel, interaction.user.id, tourney, match)

    @app_commands.command(name="admin_record_result", description="[MANAGER] Manually record a completed match result (for stuck/bugged matches).")
    async def admin_record_result(
        self, interaction: discord.Interaction,
        match_id: int,
        winner: str,
        t1_runs: int, t1_wickets: int, t1_balls: int,
        t2_runs: int, t2_wickets: int, t2_balls: int,
    ):
        server_id = str(interaction.guild.id)
        tourney = get_server_tournament(server_id)
        if not tourney:
            return await interaction.response.send_message("❌ No tournament exists.", ephemeral=True)
        if not self.is_manager(interaction, tourney):
            return await interaction.response.send_message("❌ Managers only.", ephemeral=True)
        if tourney["status"] != "active":
            return await interaction.response.send_message("❌ Tournament is not active.", ephemeral=True)

        m_data = next((m for m in tourney.get("schedule", []) if m["match_id"] == match_id), None)
        if not m_data:
            return await interaction.response.send_message(f"❌ Match ID {match_id} not found.", ephemeral=True)
        if m_data["status"] == "completed":
            return await interaction.response.send_message(f"❌ Match {match_id} is already marked completed.", ephemeral=True)

        t1_name, t2_name = m_data["team1"], m_data["team2"]
        winner_clean = winner.strip()
        if winner_clean not in (t1_name, t2_name, "TIE"):
            return await interaction.response.send_message(
                f"❌ Winner must be **{t1_name}**, **{t2_name}**, or **TIE**.", ephemeral=True
            )

        m_data["status"] = "completed"
        m_data["result"] = {
            "winner": winner_clean,
            "format_overs": tourney.get("format_overs", 20),
            "t1_runs": t1_runs, "t1_wickets": t1_wickets, "t1_balls": t1_balls,
            "t2_runs": t2_runs, "t2_wickets": t2_wickets, "t2_balls": t2_balls,
        }
        tourney["current_match_idx"] = tourney.get("current_match_idx", 0) + 1

        # Auto-generate Final if both semis are now complete
        sf1 = next((m for m in tourney["schedule"] if m["round"] == "Semi-Final 1"), None)
        sf2 = next((m for m in tourney["schedule"] if m["round"] == "Semi-Final 2"), None)
        if sf1 and sf2 and sf1["status"] == "completed" and sf2["status"] == "completed":
            if not any(m["round"] == "Final" for m in tourney["schedule"]):
                tourney["schedule"].append({
                    "match_id": len(tourney["schedule"]) + 1, "round": "Final",
                    "team1": sf1["result"]["winner"], "team2": sf2["result"]["winner"],
                    "status": "pending", "result": None
                })

        final_match = next((m for m in tourney["schedule"] if m["round"] == "Final"), None)
        if final_match and final_match["status"] == "completed" and tourney["status"] != "completed":
            tourney["status"] = "completed"

        save_tournament(tourney)

        r_label = f"Round {m_data['round']}" if isinstance(m_data['round'], int) else m_data['round']
        overs1 = f"{t1_balls // 6}.{t1_balls % 6}"
        overs2 = f"{t2_balls // 6}.{t2_balls % 6}"
        embed = discord.Embed(
            title=f"✅ Match {match_id} Result Recorded",
            description=(
                f"**{r_label}** — {t1_name} vs {t2_name}\n\n"
                f"🏏 **{t1_name}:** {t1_runs}/{t1_wickets} ({overs1} ov)\n"
                f"🏏 **{t2_name}:** {t2_runs}/{t2_wickets} ({overs2} ov)\n\n"
                f"🏆 **Winner: {winner_clean}**\n\n"
                f"⚠️ *Player stats for this match were not recorded (match object unavailable). "
                f"Patch them manually in JSONBin if needed.*"
            ),
            color=discord.Color.green()
        )
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="admin_restore_schedule", description="[MANAGER] Regenerate schedule deterministically (no shuffle). Use after restart.")
    async def admin_restore_schedule(self, interaction: discord.Interaction):
        server_id = str(interaction.guild.id)
        tourney = get_server_tournament(server_id)
        if not tourney:
            return await interaction.response.send_message("❌ No tournament exists.", ephemeral=True)
        if not self.is_manager(interaction, tourney):
            return await interaction.response.send_message("❌ Managers only.", ephemeral=True)

        completed = [m for m in tourney.get("schedule", []) if m["status"] == "completed"]
        if completed:
            return await interaction.response.send_message(
                f"⚠️ This tournament already has **{len(completed)} completed match(es)**. "
                f"Running this will wipe the schedule and reset all matches to pending.\n"
                f"If you still want to proceed, use `/tournament admin_force_restore_schedule`.",
                ephemeral=True
            )
        await self._do_restore_schedule(interaction, tourney)

    @app_commands.command(name="admin_force_restore_schedule", description="[MANAGER] Force-regenerate schedule even if matches are already completed. Use with caution.")
    async def admin_force_restore_schedule(self, interaction: discord.Interaction):
        server_id = str(interaction.guild.id)
        tourney = get_server_tournament(server_id)
        if not tourney:
            return await interaction.response.send_message("❌ No tournament exists.", ephemeral=True)
        if not self.is_manager(interaction, tourney):
            return await interaction.response.send_message("❌ Managers only.", ephemeral=True)
        await self._do_restore_schedule(interaction, tourney)

    async def _do_restore_schedule(self, interaction: discord.Interaction, tourney: dict):
        teams = [t["name"] for t in tourney["teams"]]
        if len(teams) < 2:
            return await interaction.response.send_message("❌ Need at least 2 teams registered.", ephemeral=True)

        if len(teams) % 2 != 0:
            teams.append("BYE")

        n = len(teams)
        matchups = []
        for r in range(n - 1):
            for i in range(n // 2):
                t1, t2 = teams[i], teams[n - 1 - i]
                if t1 != "BYE" and t2 != "BYE":
                    matchups.append({
                        "round": r + 1,
                        "team1": t1 if r % 2 == 0 else t2,
                        "team2": t2 if r % 2 == 0 else t1,
                    })
            teams.insert(1, teams.pop())

        schedule = [
            {"match_id": i + 1, "round": m["round"], "team1": m["team1"], "team2": m["team2"], "status": "pending", "result": None}
            for i, m in enumerate(matchups)
        ]

        tourney["schedule"] = schedule
        tourney["status"] = "active"
        tourney["current_match_idx"] = 0
        save_tournament(tourney)

        # Build a preview of the first two rounds
        r1 = [m for m in schedule if m["round"] == 1]
        r2 = [m for m in schedule if m["round"] == 2]
        preview = "**Round 1:**\n" + "\n".join(f"  Match {m['match_id']}: {m['team1']} vs {m['team2']}" for m in r1)
        preview += "\n\n**Round 2:**\n" + "\n".join(f"  Match {m['match_id']}: {m['team1']} vs {m['team2']}" for m in r2)

        embed = discord.Embed(
            title=f"✅ Schedule Restored — {tourney['name']}",
            description=(
                f"Generated **{len(schedule)} matches** across **{n - 1} rounds**.\n"
                f"All matches set to pending. Use `/tournament admin_record_result` to re-enter already-played results.\n\n"
                f"{preview}"
            ),
            color=discord.Color.blue()
        )
        await interaction.response.send_message(embed=embed)

    @commands.Cog.listener()
    async def on_tournament_match_complete(self, match):
        server_id = match.tournament_server_id
        tourney = get_server_tournament(server_id)
        if not tourney: return
        
        match_idx = match.tournament_match_id - 1
        m_data = tourney["schedule"][match_idx]
        
        t1_name, t2_name = match.team1["name"], match.team2["name"]
        if match.innings1.batting_team["name"] == t1_name:
            t1_inn, t2_inn = match.innings1, match.innings2
        else:
            t1_inn, t2_inn = match.innings2, match.innings1
            
        target = getattr(match, "target", match.innings1.total_runs + 1)
        is_tied = (match.innings2.total_runs == target - 1)
        
        if getattr(match, 'tiebreak_winner_name', None):
            winner = match.tiebreak_winner_name
        elif is_tied: winner = "TIE"
        elif match.innings2.total_runs >= target: winner = match.innings2.batting_team["name"]
        else: winner = match.innings1.batting_team["name"]
            
        if winner == "TIE" and not isinstance(m_data.get("round"), int):
            # In knockouts, ties advance the higher seed naturally!
            winner = m_data["team1"]
            
        m_data["status"] = "completed"
        m_data["result"] = {
            "winner": winner, "format_overs": match.format_overs,
            "t1_runs": t1_inn.total_runs, "t1_wickets": t1_inn.wickets, "t1_balls": t1_inn.total_balls,
            "t2_runs": t2_inn.total_runs, "t2_wickets": t2_inn.wickets, "t2_balls": t2_inn.total_balls,
        }
        tourney["current_match_idx"] += 1
        
        # --- PHASE 3: STATS AGGREGATION ---
        if "stats" not in tourney: tourney["stats"] = {}
        if t1_name not in tourney["stats"]: tourney["stats"][t1_name] = {}
        if t2_name not in tourney["stats"]: tourney["stats"][t2_name] = {}
        
        def process_team_stats(team_name, batting_inn, bowling_inn):
            for p in batting_inn.batting_team["players"]:
                p_name = p["name"]
                p_stats = tourney["stats"][team_name].setdefault(p_name, {"matches": 0, "runs": 0, "balls_faced": 0, "outs": 0, "fours": 0, "sixes": 0, "fifties": 0, "hundreds": 0, "wickets": 0, "runs_conceded": 0, "balls_bowled": 0})
                p_stats["matches"] += 1
                
                if p_name in batting_inn.batting_stats:
                    b_stat = batting_inn.batting_stats[p_name]
                    p_stats["runs"] += b_stat.runs_scored
                    p_stats["balls_faced"] += b_stat.balls_faced
                    if b_stat.dismissal != "not out": p_stats["outs"] += 1
                    p_stats["fours"] += getattr(b_stat, "fours", 0)
                    p_stats["sixes"] += getattr(b_stat, "sixes", 0)
                    if b_stat.runs_scored >= 100: p_stats["hundreds"] += 1
                    elif b_stat.runs_scored >= 50: p_stats["fifties"] += 1
                    
            for p_name, bw_stat in bowling_inn.bowling_stats.items():
                if bw_stat.balls_bowled > 0:
                    p_stats = tourney["stats"][team_name].setdefault(p_name, {"matches": 0, "runs": 0, "balls_faced": 0, "outs": 0, "fours": 0, "sixes": 0, "fifties": 0, "hundreds": 0, "wickets": 0, "runs_conceded": 0, "balls_bowled": 0})
                    p_stats["wickets"] += bw_stat.wickets_taken
                    p_stats["runs_conceded"] += bw_stat.runs_conceded
                    p_stats["balls_bowled"] += bw_stat.balls_bowled

        process_team_stats(t1_name, t1_inn, t2_inn)
        process_team_stats(t2_name, t2_inn, t1_inn)
        
        # --- PHASE 4: KNOCKOUTS AUTO-PROGRESSION ---
        sf1 = next((m for m in tourney["schedule"] if m["round"] == "Semi-Final 1"), None)
        sf2 = next((m for m in tourney["schedule"] if m["round"] == "Semi-Final 2"), None)
        
        if sf1 and sf2 and sf1["status"] == "completed" and sf2["status"] == "completed":
            if not any(m["round"] == "Final" for m in tourney["schedule"]):
                tourney["schedule"].append({
                    "match_id": len(tourney["schedule"]) + 1, "round": "Final",
                    "team1": sf1["result"]["winner"], "team2": sf2["result"]["winner"],
                    "status": "pending", "result": None
                })
                
        final_match = next((m for m in tourney["schedule"] if m["round"] == "Final"), None)
        if final_match and final_match["status"] == "completed" and tourney["status"] != "completed":
            tourney["status"] = "completed"
            
        save_tournament(tourney)

    @app_commands.command(name="standings", description="View the Tournament Points Table & NRR.")
    async def standings(self, interaction: discord.Interaction):
        server_id = str(interaction.guild.id)
        tourney = get_server_tournament(server_id)
        if not tourney: return await interaction.response.send_message("❌ No tournament exists.", ephemeral=True)
        
        standings = get_tournament_standings(tourney)
        theme = tourney.get("theme", "Default")
        
        # --- 1. SHARED FONTS ---
        try:
            font_title = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 46)
            font_small = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 18)
            font_hdr = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 22)
            font_row = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 26)
            font_bold = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 22)
        except:
            font_title = font_small = font_hdr = font_row = font_bold = ImageFont.load_default()
            
        def get_tw(text, font):
            if hasattr(font, 'getbbox'): return font.getbbox(text)[2]
            return len(text) * 12
            
        # --- 2. CRIMSON CRICKET TEMPLATE ---
        if theme == "Crimson Cricket":
            try:
                img = Image.open("points_table_crimson.png").convert("RGB")
                d = ImageDraw.Draw(img)
                
                # 🛠️ CONFIGURATION: Adjust these pixels if it doesn't line up perfectly with your PNG!
                start_y = 275       # Y-pixel where the first team row starts
                row_height = 40     # Spacing between each team row
              
                c_text = "#FFFFFF"  # Text color
                
                # X-pixel coordinates for the center of each column (Adjust left/right as needed)
                # Note: The template already has 1-10 written for POS, so we skip drawing the rank!
                
                cols = {"TEAM": 140, "P": 445, "W": 555, "L": 665, "NR": 775, "PTS": 885, "NRR": 995}
                
                y = start_y
                for i, (t_name, data) in enumerate(standings, 1):
                    if i > 10: break # Template only supports up to 10 teams
                    
                    # Team Name (Left Aligned)
                    d.text((cols["TEAM"], y + 8), t_name[:20].upper(), fill=c_text, font=font_row)
                    # Stats
                    d.text((cols["P"] - (get_tw(str(data['P']), font_row)/2), y + 8), str(data['P']), fill=c_text, font=font_row)
                    d.text((cols["W"] - (get_tw(str(data['W']), font_row)/2), y + 8), str(data['W']), fill=c_text, font=font_row)
                    d.text((cols["L"] - (get_tw(str(data['L']), font_row)/2), y + 8), str(data['L']), fill=c_text, font=font_row)
                    d.text((cols["NR"] - (get_tw(str(data['T']), font_row)/2), y + 8), str(data['T']), fill=c_text, font=font_row)
                    d.text((cols["PTS"] - (get_tw(str(data['Pts']), font_row)/2), y + 8), str(data['Pts']), fill=c_text, font=font_row)
                    # NRR
                    nrr_str = f"{data['NRR']:+.2f}"
                    d.text((cols["NRR"] - (get_tw(nrr_str, font_row)/2), y + 8), nrr_str, fill=c_text, font=font_row)
                    
                    y += row_height
                    
                buf = io.BytesIO()
                img.save(buf, format="PNG")
                buf.seek(0)
                return await interaction.response.send_message(file=discord.File(fp=buf, filename="crimson_standings.png"))
                
            except FileNotFoundError:
                # If you misspelled the file or it's missing, gracefully fall back to the default!
                print("⚠️ Warning: points_table_crimson.png not found. Falling back to default layout.")
                pass
                
        # --- 3. DEFAULT THEME (Auto-Generated) ---
        c_bg = "#101820"
        c_panel = "#F8F9FA"
        c_header = "#0B2B5C"
        c_cyan = "#1DA1F2"
        c_text_navy = "#0F172A"
        c_text_grey = "#64748B"
        c_white = "#FFFFFF"
        c_line = "#E2E8F0"
        c_green = "#39B54A"
        c_red = "#E84135"

        row_height = 60
        header_height = 120
        footer_height = 80
        
        img_height = 80 + header_height + 50 + (len(standings) * row_height) + footer_height + 80
        
        img = Image.new("RGB", (1200, img_height), color=c_bg)
        d = ImageDraw.Draw(img)
            
        # Panel Background
        d.rounded_rectangle([(100, 80), (1100, img_height - 80)], radius=20, fill=c_panel)
            
        # Header
        d.rounded_rectangle([(100, 80), (1100, 80 + header_height)], radius=20, fill=c_header)
        d.rectangle([(100, 80 + header_height - 20), (1100, 80 + header_height)], fill=c_header)
        
        d.text((140, 105), tourney['name'][:30].upper(), fill=c_white, font=font_title)
        d.text((140, 155), "POINTS TABLE - GROUP STAGE", fill="#A5F3FC", font=font_small)
        d.text((1060 - get_tw("SERVER LOGO", font_bold), 120), "SERVER LOGO", fill=c_white, font=font_bold)
        
        # Column Headers
        cols = [("POS", 40), ("TEAM", 150), ("P", 550), ("W", 650), ("L", 750), ("T", 850), ("PTS", 950), ("NRR", 1050)]
        for name, x in cols:
            w = get_tw(name, font_hdr)
            align_x = x - w/2 if name != "TEAM" else x
            d.text((align_x, 80 + header_height + 15), name, fill=c_text_grey, font=font_hdr)
            
        # Rows
        y = 80 + header_height + 50
        for i, (t_name, data) in enumerate(standings, 1):
            d.line([(100, y), (1100, y)], fill=c_line, width=2)
            
            # Rank Accent Line
            if i <= 4: d.rectangle([(100, y), (108, y + row_height)], fill=c_cyan) 
            
            d.text((140 - (get_tw(str(i), font_row)/2), y + 15), str(i), fill=c_text_navy, font=font_row)
            d.text((220, y + 15), t_name[:20].upper(), fill=c_text_navy, font=font_row)
            
            d.text((550 - (get_tw(str(data['P']), font_row)/2), y + 15), str(data['P']), fill=c_text_grey, font=font_row)
            d.text((650 - (get_tw(str(data['W']), font_row)/2), y + 15), str(data['W']), fill=c_green, font=font_row)
            d.text((750 - (get_tw(str(data['L']), font_row)/2), y + 15), str(data['L']), fill=c_red, font=font_row)
            d.text((850 - (get_tw(str(data['T']), font_row)/2), y + 15), str(data['T']), fill=c_text_grey, font=font_row)
            
            d.text((950 - (get_tw(str(data['Pts']), font_row)/2), y + 15), str(data['Pts']), fill=c_text_navy, font=font_row)
            nrr_str = f"{data['NRR']:+.3f}"
            d.text((1050 - (get_tw(nrr_str, font_row)/2), y + 15), nrr_str, fill=c_text_navy, font=font_row)
            
            y += row_height
            
        # Footer Block
        footer_y = img_height - 80 - footer_height
        d.rounded_rectangle([(100, footer_y), (1100, img_height - 80)], radius=20, fill=c_header)
        d.rectangle([(100, footer_y), (1100, footer_y + 20)], fill=c_header) # square top
        
        d.text((600 - get_tw("SIMULATION ENGINE PRO", font_bold)//2, footer_y + 25), "SIMULATION ENGINE PRO", fill=c_white, font=font_bold)
            
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)
        
        await interaction.response.send_message(file=discord.File(fp=buf, filename="standings.png"))

    @app_commands.command(name="leaderboard", description="View the top performing players in the tournament.")
    @app_commands.choices(category=[
        app_commands.Choice(name="Most Runs", value="runs"),
        app_commands.Choice(name="Most Wickets", value="wickets"),
        app_commands.Choice(name="Highest Strike Rate (Min 50 Runs)", value="sr"),
        app_commands.Choice(name="Highest Batting Avg (Min 50 Runs)", value="bat_avg"),
        app_commands.Choice(name="Most 4s", value="fours"),
        app_commands.Choice(name="Most 6s", value="sixes"),
        app_commands.Choice(name="Most 50s", value="fifties"),
        app_commands.Choice(name="Most 100s", value="hundreds"),
        app_commands.Choice(name="Best Economy (Min 5 Overs)", value="econ"),
        app_commands.Choice(name="Best Bowling Avg (Min 3 Wickets)", value="bowl_avg")
    ])
    async def leaderboard(self, interaction: discord.Interaction, category: app_commands.Choice[str]):
        server_id = str(interaction.guild.id)
        tourney = get_server_tournament(server_id)
        if not tourney: return await interaction.response.send_message("❌ No tournament exists.", ephemeral=True)
        
        all_players = []
        for t_name, players in tourney.get("stats", {}).items():
            for p_name, stats in players.items():
                all_players.append({"name": p_name, "team": t_name, "stats": stats})
                
        if not all_players:
            return await interaction.response.send_message("❌ No stats available yet. Complete a match first!", ephemeral=True)
            
        c_val = category.value
        if c_val == "runs": sorted_players = sorted(all_players, key=lambda x: x["stats"]["runs"], reverse=True)
        elif c_val == "wickets": sorted_players = sorted(all_players, key=lambda x: x["stats"]["wickets"], reverse=True)
        elif c_val == "sr":
            qualifiers = [p for p in all_players if p["stats"]["runs"] >= 50]
            sorted_players = sorted(qualifiers, key=lambda x: (x["stats"]["runs"] / x["stats"]["balls_faced"]) if x["stats"]["balls_faced"] > 0 else 0, reverse=True)
        elif c_val == "bat_avg":
            qualifiers = [p for p in all_players if p["stats"]["runs"] >= 50]
            sorted_players = sorted(qualifiers, key=lambda x: x["stats"]["runs"] / max(1, x["stats"]["outs"]), reverse=True)
        elif c_val in ["fours", "sixes", "fifties", "hundreds"]:
            sorted_players = sorted(all_players, key=lambda x: x["stats"][c_val], reverse=True)
        elif c_val == "econ":
            qualifiers = [p for p in all_players if p["stats"]["balls_bowled"] >= 30] 
            sorted_players = sorted(qualifiers, key=lambda x: (x["stats"]["runs_conceded"] / x["stats"]["balls_bowled"])*6 if x["stats"]["balls_bowled"]>0 else 999)
        elif c_val == "bowl_avg":
            qualifiers = [p for p in all_players if p["stats"]["wickets"] >= 3]
            sorted_players = sorted(qualifiers, key=lambda x: x["stats"]["runs_conceded"] / x["stats"]["wickets"] if x["stats"]["wickets"]>0 else 999)

        embed = discord.Embed(title=f"🏆 Tournament Leaderboard: {category.name}", color=discord.Color.gold())
        
        lines = []
        for i, p in enumerate(sorted_players[:10], 1):
            s = p["stats"]
            if c_val == "runs": val = f"**{s['runs']}** runs"
            elif c_val == "wickets": val = f"**{s['wickets']}** wickets"
            elif c_val == "sr": 
                sr = (s['runs']/s['balls_faced']*100) if s['balls_faced']>0 else 0
                val = f"**{sr:.1f}** SR"
            elif c_val == "bat_avg":
                avg = s['runs']/max(1, s['outs'])
                val = f"**{avg:.1f}** Avg"
            elif c_val in ["fours", "sixes", "fifties", "hundreds"]:
                val = f"**{s[c_val]}**"
            elif c_val == "econ":
                econ = (s['runs_conceded']/s['balls_bowled']*6) if s['balls_bowled']>0 else 0
                val = f"**{econ:.1f}** Econ"
            elif c_val == "bowl_avg":
                avg = s['runs_conceded']/s['wickets'] if s['wickets']>0 else 0
                val = f"**{avg:.1f}** Avg"
                
            lines.append(f"`{i:>2}.` **{p['name']}** ({p['team']}) — {val}")
            
        embed.description = "\n".join(lines) if lines else "No players qualify for this leaderboard yet."
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="player_stats", description="View a specific player's tournament stats.")
    async def player_stats(self, interaction: discord.Interaction, team_name: str, player_name: str):
        server_id = str(interaction.guild.id)
        tourney = get_server_tournament(server_id)
        if not tourney: return await interaction.response.send_message("❌ No tournament exists.", ephemeral=True)
        
        t_match = next((t for t in tourney.get("stats", {}).keys() if t.lower() == team_name.lower()), None)
        if not t_match:
            return await interaction.response.send_message(f"❌ Team '{team_name}' not found or hasn't played a match yet.", ephemeral=True)
            
        p_match = next((p for p in tourney["stats"][t_match].keys() if p.lower() == player_name.lower()), None)
        if not p_match:
            close = difflib.get_close_matches(player_name, list(tourney["stats"][t_match].keys()), n=1, cutoff=0.5)
            if close: p_match = close[0]
            else: return await interaction.response.send_message(f"❌ Player '{player_name}' not found in team '{t_match}'.", ephemeral=True)
            
        stats = tourney["stats"][t_match][p_match]
        
        sr = (stats["runs"] / stats["balls_faced"] * 100) if stats["balls_faced"] > 0 else 0.0
        bat_avg = (stats["runs"] / stats["outs"]) if stats["outs"] > 0 else float(stats["runs"])
        bowl_avg = (stats["runs_conceded"] / stats["wickets"]) if stats["wickets"] > 0 else 0.0
        econ = (stats["runs_conceded"] / stats["balls_bowled"] * 6) if stats["balls_bowled"] > 0 else 0.0
        
        embed = discord.Embed(title=f"📊 Tournament Stats: {p_match}", description=f"**Team:** {t_match} | **Matches:** {stats['matches']}", color=discord.Color.blue())
        
        bat_str = f"**Runs:** {stats['runs']}\n**Strike Rate:** {sr:.1f}\n**Average:** {bat_avg:.1f}\n"
        bat_str += f"**4s:** {stats['fours']} | **6s:** {stats['sixes']}\n**50s:** {stats['fifties']} | **100s:** {stats['hundreds']}"
        embed.add_field(name="🏏 Batting", value=bat_str, inline=True)
        
        bowl_str = f"**Wickets:** {stats['wickets']}\n**Economy:** {econ:.1f}\n**Bowling Avg:** {bowl_avg:.1f}\n"
        o = stats['balls_bowled'] // 6
        b = stats['balls_bowled'] % 6
        bowl_str += f"**Overs:** {o}.{b}"
        embed.add_field(name="🎯 Bowling", value=bowl_str, inline=True)
        
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="squad", description="View a team's tournament squad and player ratings.")
    async def squad(self, interaction: discord.Interaction, team_name: str = None):
        server_id = str(interaction.guild.id)
        tourney = get_server_tournament(server_id)
        if not tourney: return await interaction.response.send_message("❌ No tournament exists.", ephemeral=True)
        
        if team_name:
            team = next((t for t in tourney["teams"] if t["name"].lower() == team_name.lower()), None)
            if not team: return await interaction.response.send_message(f"❌ Team '{team_name}' not found.", ephemeral=True)
        else:
            team = next((t for t in tourney["teams"] if t["owner_id"] == str(interaction.user.id)), None)
            if not team: return await interaction.response.send_message("❌ You do not own a team. Please specify a `team_name`.", ephemeral=True)
            
        if not team.get("squad"):
            return await interaction.response.send_message(f"❌ **{team['name']}** has not submitted their squad yet.", ephemeral=True)
            
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
            
            if cat in ["bat", "wk"]:
                return f"`{p['bat']:>2} BAT` • **{p['name']}** *(Type: {arch})*"
            elif cat == "ar":
                return f"`{p['bat']:>2} BAT | {p['bowl']:>2} BWL` • **{p['name']}** *({style} | {arch})*"
            else:
                return f"`{p['bowl']:>2} BWL` • **{p['name']}** *({style})*"

        if batters: embed.add_field(name="🏏 Batters", value="\n".join([format_player(p, "bat") for p in batters]), inline=False)
        if wks: embed.add_field(name="🧤 Wicket-Keepers", value="\n".join([format_player(p, "wk") for p in wks]), inline=False)
        if all_rounders: embed.add_field(name="⚔️ All-Rounders", value="\n".join([format_player(p, "ar") for p in all_rounders]), inline=False)
        if bowlers: embed.add_field(name="🎯 Bowlers", value="\n".join([format_player(p, "bowl") for p in bowlers]), inline=False)
        
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="help", description="Show the Tournament module help guide.")
    async def tournament_help(self, interaction: discord.Interaction):
        embed = discord.Embed(title="🏆 Tournament Commands & Guide", color=discord.Color.gold())
        
        setup = "`/tournament create` - [ADMIN] Start a new tournament & set rules.\n`/tournament add_manager` - [MANAGER] Assign a co-manager.\n`/tournament add_team` - [MANAGER] Add a team and assign an Owner.\n`/tournament submit_squad` - [OWNER] Submit your squad.\n`/tournament start` - [MANAGER] Locks registration & generates schedule."
        embed.add_field(name="🛠️ 1. Setup & Registration", value=setup, inline=False)
        
        play = "`/tournament next_match` - [OWNER] Instantly launch your team's next match.\n`/tournament play` - [MANAGER] Force start a specific Match ID.\n`/tournament play_next` - [MANAGER] Force start the next sequential match."
        embed.add_field(name="🏏 2. Playing Matches", value=play, inline=False)
        
        stats = "`/tournament status` - View the live schedule and upcoming matches.\n`/tournament standings` - View the Points Table and NRR.\n`/tournament leaderboard` - View top Runs, Wickets, Strike Rates, etc.\n`/tournament player_stats` - Check a specific player's exact stats.\n`/tournament squad` - View the full roster & ratings of any team."
        embed.add_field(name="📊 3. Stats & Standings", value=stats, inline=False)
        
        knockouts = "`/tournament generate_knockouts` - [MANAGER] Starts the Semi-Finals for the Top 4 teams!\n`/tournament force_delete` - [OWNER] Completely deletes the tournament."
        embed.add_field(name="🔥 4. Knockouts & Admin", value=knockouts, inline=False)
        
        await interaction.response.send_message(embed=embed, ephemeral=True)
