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
        
        _, _, _, s_tier, _ = get_tier_status(str(interaction.user.id), server_id)
        if s_tier not in ["Gold", "Diamond"]:
            return await interaction.response.send_message("❌ **Access Denied:** Only servers with an active **Gold** or **Diamond** tier can host tournaments! Contact the bot owner to upgrade.", ephemeral=True)

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
        
        await interaction.response.send_message(f"📋 Please reply to this message with the **15 Player Squad** for **{team['name']}** (One player name per line). You have 3 minutes.", ephemeral=True)
        
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
            current_round = None
            for m in schedule:
                if m["status"] == "pending":
                    current_round = m["round"]
                    break
                    
            if current_round is None:
                return await interaction.response.send_message(embed=discord.Embed(title=f"🏆 Tournament: {tourney['name']}", description="🏁 **All matches are completed!** Check `/tournament standings`.", color=discord.Color.gold()))
                
            embed.description = f"🔥 **Active Phase (Round {current_round})**"
            pending = [m for m in schedule if m["status"] == "pending" and m["round"] == current_round]
            sched_str = ""
            for m in pending:
                sched_str += f"Match {m['match_id']}: **{m['team1']}** vs **{m['team2']}**\n"
            
            embed.add_field(name="Upcoming Matches", value=sched_str, inline=False)
            
        await interaction.response.send_message(embed=embed)

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
            
        await interaction.response.send_message(f"🚀 **Launching Round {current_round} — Match {pending['match_id']}...**")
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
            
        await interaction.response.send_message(f"🚀 **Manually Launching Match {match['match_id']} (Round {match['round']})...**")
        self.bot.dispatch("start_tournament_match", interaction.channel, interaction.user.id, tourney, match)

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
        if is_tied: winner = "TIE"
        elif match.innings2.total_runs >= target: winner = match.innings2.batting_team["name"]
        else: winner = match.innings1.batting_team["name"]
            
        m_data["status"] = "completed"
        m_data["result"] = {
            "winner": winner, "format_overs": match.format_overs,
            "t1_runs": t1_inn.total_runs, "t1_wickets": t1_inn.wickets, "t1_balls": t1_inn.total_balls,
            "t2_runs": t2_inn.total_runs, "t2_wickets": t2_inn.wickets, "t2_balls": t2_inn.total_balls,
        }
        tourney["current_match_idx"] += 1
        save_tournament(tourney)

    @app_commands.command(name="standings", description="View the Tournament Points Table & NRR.")
    async def standings(self, interaction: discord.Interaction):
        server_id = str(interaction.guild.id)
        tourney = get_server_tournament(server_id)
        if not tourney: return await interaction.response.send_message("❌ No tournament exists.", ephemeral=True)
        
        teams = {t["name"]: {"P":0, "W":0, "L":0, "T":0, "Pts":0, "RF":0, "OF":0.0, "RA":0, "OA":0.0} for t in tourney["teams"]}
        for m in tourney.get("schedule", []):
            if m["status"] == "completed" and "result" in m:
                res = m["result"]
                t1, t2 = m["team1"], m["team2"]
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
            
        standings = sorted(teams.items(), key=lambda x: (x[1]["Pts"], x[1]["NRR"]), reverse=True)
        
        # Generate PIL Image
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
