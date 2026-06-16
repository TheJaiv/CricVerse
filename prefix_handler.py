import discord
from discord.ext import commands
import asyncio
import difflib
import io
from PIL import Image, ImageDraw, ImageFont

# Imports from other bot files
from bot import (
    is_channel_restricted, check_potential_quota, consume_quota, active_games, active_setups,
    ADMIN_DISCORD_ID, MatchSetupState, FormatSelectView, ImpactPlayerSelectView, send_player_profile
)
from tournament_manager import (
    get_server_tournament, save_tournament, get_tournament_standings
)
from subscription_manager import get_all_players, get_tier_status, get_auth_admins
# Career Mode (WIP) — imported defensively so a failure here can't break the cog
# (and therefore the whole bot).
try:
    from bot import CAREER_MODE_ENABLED, ADMIN_DISCORD_ID
except Exception:
    CAREER_MODE_ENABLED = False
    ADMIN_DISCORD_ID = 0
try:
    import career_manager as CM
    import career_ui
    _CAREER_OK = True
except Exception as _career_err:
    print(f"⚠️ Career UI not loaded ({_career_err}); Career commands disabled.")
    _CAREER_OK = False

def _can_use_career(ctx):
    """During development Career Mode is restricted to owner + admins. When
    CAREER_MODE_ENABLED is flipped on, it opens to everyone."""
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
    except Exception:
        pass
    return False

_SOON = "🚧 **Career Mode is coming soon!** It's still in development."

def _is_premium(ctx):
    """Weekly/monthly perks: Nitro boosters, paid sub tiers, or owner (testing)."""
    try:
        if ctx.author.id == ADMIN_DISCORD_ID:
            return True
        if getattr(ctx.author, "premium_since", None):  # server booster
            return True
        u_tier, _, _, _, _ = get_tier_status(str(ctx.author.id), str(ctx.guild.id) if ctx.guild else "")
        if u_tier and u_tier != "Free":
            return True
    except Exception:
        pass
    return False

# Helper to convert "true"/"false" strings to bool
def to_bool(value: str) -> bool:
    return value.lower() in ['true', '1', 't', 'y', 'yes']

# Custom Help Command for the 'cv' prefix
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

# The main Cog for all prefix commands
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

    # --- CORE COMMANDS ---

    @commands.command(name="match", help="Start a new Cricket Match simulation.\nUsage: cv match [@opponent]")
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

    @commands.command(name="endmatch", help="Force cancel the current match or setup in this channel.\nUsage: cv endmatch")
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

    @commands.command(name="searchplayer", help="Search for a player in the Cloud DB.\nUsage: cv searchplayer <name>")
    async def searchplayer(self, ctx, *, name: str):
        search_query = name.strip()
        all_players = get_all_players()
        player_names = [p["name"] for p in all_players]
        
        if not all_players:
            return await ctx.send("❌ Error: Cache is empty.")
            
        exact = next((p for p in all_players if p["name"].lower() == search_query.lower()), None)
        
        # Fake interaction to reuse the embed function
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

    # --- CAREER MODE ---

    @commands.command(name="start_career", aliases=["startcareer"], help="Create your career all-rounder.\nUsage: cv start_career")
    async def start_career(self, ctx):
        if not _can_use_career(ctx):
            return await ctx.send(_SOON)
        if CM.get_career(ctx.author.id):
            return await ctx.send("❌ You already have a career! Use `cv profile` to view it.")
        await ctx.send(
            f"🏏 **Start your Career, {ctx.author.display_name}!**\nEvery player is an **all-rounder** — "
            f"pick how you bowl and how you bat:",
            view=career_ui.CareerCreateView(ctx.author.id, ctx.author.display_name),
        )

    @commands.command(name="profile", aliases=["card", "me"], help="View a player card.\nUsage: cv profile [@user]")
    async def profile(self, ctx, member: discord.Member = None):
        if not _can_use_career(ctx):
            return await ctx.send(_SOON)
        target = member or ctx.author
        career = CM.get_career(target.id)
        if not career:
            who = "You don't" if target.id == ctx.author.id else f"{target.display_name} doesn't"
            return await ctx.send(f"❌ {who} have a career yet. Use `cv start_career` to begin.")
        await ctx.send(file=discord.File(career_ui.render_career_card(career), "career_card.png"))

    @commands.command(name="debut", help="Play your Academy Trial to unlock your card.\nUsage: cv debut")
    async def debut(self, ctx):
        if not _can_use_career(ctx):
            return await ctx.send(_SOON)
        career = CM.get_career(ctx.author.id)
        if not career:
            return await ctx.send("❌ Start a career first: `cv start_career`.")
        if career.get("debut_done"):
            return await ctx.send("✅ You've already made your debut! Use `cv profile`.")
        passed, lines, headline = career_ui.run_debut_trial(career)
        embed = discord.Embed(title=f"🎓 ACADEMY TRIAL — {headline}", description="\n".join(lines),
                              color=discord.Color.green() if passed else discord.Color.red())
        if passed:
            career["debut_done"] = True
            CM.async_save_career(career)
            embed.set_footer(text="Official card unlocked! 🎉  Earn coins with cv daily, then cv upgrade.")
        else:
            embed.set_footer(text="Unlucky — run cv debut again to retry.")
        await ctx.send(embed=embed)

    @commands.command(name="daily", help="Claim your daily coins (24h).\nUsage: cv daily")
    async def daily(self, ctx):
        if not _can_use_career(ctx):
            return await ctx.send(_SOON)
        career = CM.get_career(ctx.author.id)
        if not career:
            return await ctx.send("❌ Start a career first: `cv start_career`.")
        amount, err = CM.claim_daily(career)
        if err:
            return await ctx.send(err)
        await ctx.send(f"🪙 **+{amount} coins!** Daily claimed. Balance: **{career['coins']:,}**.")

    @commands.command(name="balance", aliases=["bal", "coins"], help="Check your coin balance.\nUsage: cv balance")
    async def balance(self, ctx):
        if not _can_use_career(ctx):
            return await ctx.send(_SOON)
        career = CM.get_career(ctx.author.id)
        if not career:
            return await ctx.send("❌ Start a career first: `cv start_career`.")
        await ctx.send(f"🪙 **{ctx.author.display_name}** — **{career['coins']:,} coins**  ·  OVR {career['ovr']} ({career['tier']})")

    @commands.command(name="upgrade", aliases=["ug", "train"], help="Spend coins to raise an attribute.\nUsage: cv upgrade <power|control|bowling|stamina> [amount]")
    async def upgrade(self, ctx, attribute: str = None, amount: int = 1):
        if not _can_use_career(ctx):
            return await ctx.send(_SOON)
        career = CM.get_career(ctx.author.id)
        if not career:
            return await ctx.send("❌ Start a career first: `cv start_career`.")
        if not attribute or attribute.lower() not in CM.ATTRS:
            a = career["attributes"]
            costs = " · ".join(f"**{k}** {a[k]} (next {CM.upgrade_cost(a[k])}🪙)" for k in CM.ATTRS)
            return await ctx.send(
                f"🏋️ **Upgrade an attribute** — `cv upgrade <attribute> [amount]`\n{costs}\n"
                f"Balance: **{career['coins']:,}** 🪙  ·  OVR {career['ovr']} ({career['tier']})")
        attribute = attribute.lower()
        amount = max(1, min(amount, 30))
        old_ovr, old_tier = career["ovr"], career["tier"]
        bought, spent, msg = CM.upgrade_attribute(career, attribute, amount)
        if bought == 0:
            return await ctx.send(f"❌ {msg}")
        line = (f"💪 **+{bought} {attribute}** for **{spent:,}** 🪙 → now **{career['attributes'][attribute]}**.\n"
                f"OVR **{old_ovr} → {career['ovr']}**  ·  Balance **{career['coins']:,}** 🪙")
        if career["tier"] != old_tier:
            line += f"\n🏅 **TIER UP! {old_tier} → {career['tier']}!**"
        await ctx.send(line)

    @commands.command(name="delete_career", aliases=["delcareer", "resetcareer"], help="[DEV] Wipe a user's career.\nUsage: cv delete_career [@user]")
    async def delete_career(self, ctx, member: discord.Member = None):
        if not _can_use_career(ctx):
            return await ctx.send(_SOON)
        target = member or ctx.author
        if not CM.get_career(target.id):
            return await ctx.send(f"❌ {target.display_name} has no career to delete.")
        ok = CM.delete_career(target.id)
        await ctx.send(f"🗑️ Wiped **{target.display_name}**'s career — they can `cv start_career` fresh."
                       if ok else "❌ Delete failed (see logs).")

    @commands.command(name="weekly", help="[Premium/Booster] Weekly coins + 5% boost.\nUsage: cv weekly")
    async def weekly(self, ctx):
        if not _can_use_career(ctx):
            return await ctx.send(_SOON)
        career = CM.get_career(ctx.author.id)
        if not career:
            return await ctx.send("❌ Start a career first: `cv start_career`.")
        if not _is_premium(ctx):
            return await ctx.send("🔒 **Weekly** is a perk for **Premium members / server boosters**.")
        amt, err = CM.claim_weekly(career)
        if err:
            return await ctx.send(err)
        await ctx.send(f"🪙 **+{amt:,} coins** + a **5% coin boost for 7 days**! Balance: **{career['coins']:,}**.")

    @commands.command(name="monthly", help="[Premium/Booster] Monthly coins + title.\nUsage: cv monthly")
    async def monthly(self, ctx):
        if not _can_use_career(ctx):
            return await ctx.send(_SOON)
        career = CM.get_career(ctx.author.id)
        if not career:
            return await ctx.send("❌ Start a career first: `cv start_career`.")
        if not _is_premium(ctx):
            return await ctx.send("🔒 **Monthly** is a perk for **Premium members / server boosters**.")
        amt, err = CM.claim_monthly(career)
        if err:
            return await ctx.send(err)
        await ctx.send(f"🪙 **+{amt:,} coins** and the **[Patron]** profile title unlocked! Balance: **{career['coins']:,}**.")

    @commands.command(name="career", aliases=["careerhelp"], help="Career Mode help menu.\nUsage: cv career")
    async def career_help(self, ctx):
        if not _can_use_career(ctx):
            return await ctx.send(_SOON)
        e = discord.Embed(
            title="🏏 Career Mode — Help",
            description="Build **one** global all-rounder, earn coins, upgrade your attributes and climb the tiers from **Bronze → Diamond**.",
            color=discord.Color.blurple())
        e.add_field(name="🚀 Getting Started", value=(
            "`cv start_career` — create your all-rounder (choose bowling type + batting mindset)\n"
            "`cv debut` — pass the Academy Trial to unlock your official card"), inline=False)
        e.add_field(name="💰 Economy", value=(
            "`cv daily` — daily coins (24h)\n"
            "`cv weekly` — *Premium/Booster:* coins + 5% week boost\n"
            "`cv monthly` — *Premium/Booster:* coins + cosmetic title\n"
            "`cv balance` — check your coins\n"
            "🪙 *Coins come from dailies and **real club matches only** — matches vs AI pay nothing (practice/quests only).*"), inline=False)
        e.add_field(name="📈 Progress", value=(
            "`cv upgrade <power|control|bowling|stamina> [amount]` — spend coins to raise an attribute & OVR\n"
            "`cv profile [@user]` — view a player card"), inline=False)
        e.add_field(name="🏅 Tiers", value="Bronze 70–73 · Silver 74–79 · Gold 80–85 · Platinum 86–91 · Diamond 92–99", inline=False)
        if ctx.author.id == ADMIN_DISCORD_ID or (ctx.guild and ctx.author.guild_permissions.administrator):
            e.add_field(name="🛠️ Dev", value="`cv delete_career [@user]` — wipe a career", inline=False)
        e.set_footer(text="Career Mode is in development — currently admin/owner only.")
        await ctx.send(embed=e)

    # --- TOURNAMENT COMMANDS ---

    @commands.group(name="tournament", invoke_without_command=True, help="Main command for tournaments. Use 'cv help tournament' for subcommands.")
    async def tournament(self, ctx):
        await self.bot.help_command.send_group_help(ctx.command)

    @tournament.command(name="create", help="[ADMIN] Create a new tournament.\nUsage: cv tournament create \"<name>\" <format> [impact_player=true/false]")
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

    @tournament.command(name="add_team", help="[MANAGER] Add a team and assign an Owner.\nUsage: cv tournament add_team \"<team_name>\" <@owner>")
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

    @tournament.command(name="replace_player", help="[MANAGER] Replace a player in a team's squad.\nUsage: cv tournament replace_player \"<team>\" \"<out_player>\" \"<in_player>\"")
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

    @tournament.command(name="standings", help="View the Tournament Points Table & NRR.\nUsage: cv tournament standings")
    async def t_standings(self, ctx):
        server_id = str(ctx.guild.id)
        tourney = get_server_tournament(server_id)
        if not tourney: return await ctx.send("❌ No tournament exists.")
        
        # Replicating the image generation logic from the slash command
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
        
        # Fallback to sending a text-based table if image fails or theme is default
        if not standings: return await ctx.send("No matches have been completed yet.")
        header = f"`{'#':<3}{'Team':<20}{'P':>3}{'W':>3}{'L':>3}{'T':>3}{'Pts':>4}{'NRR':>7}`\n"
        rows = [header]
        for i, (t_name, data) in enumerate(standings, 1):
            nrr = f"{data['NRR']:+.2f}"
            rows.append(f"`{i:<3}{t_name:<20}{data['P']:>3}{data['W']:>3}{data['L']:>3}{data['T']:>3}{data['Pts']:>4}{nrr:>7}`")
        await ctx.send("🏆 **Tournament Standings**\n" + "\n".join(rows))

async def setup(bot):
    await bot.add_cog(PrefixCog(bot))