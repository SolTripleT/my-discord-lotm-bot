# ============================================================
# USER COMMANDS — everyday player-facing slash commands: profile,
# sequence lookup, comparisons, the pathway list, leaderboard, daily/
# pray XP claims, the /guide browser, pathway ability reference, combat
# stats, recent battle log, and win-streak lookup.
# ============================================================
import random
import discord
import aiosqlite
from discord import app_commands
from discord.ext import commands
from datetime import datetime, timedelta

from config import DB_PATH, pathways, pathway_colors
from utils import check_blacklist, log_event, make_xp_bar, parse_iso
from roles import get_sequence_name, assign_sequence_role
from xp import get_xp_required, apply_xp_gain
from database import get_user_data, update_user
from economy import get_economy, get_pounds, add_pounds
from persistence import daily_data, save_daily
from fighter import build_stats_embed, get_or_create_stats
from game_data import INFO_PAGES
from battle.views import get_pathway_emoji, PathwayAbilitySelectView, StatsView, InfoView
from battle.manager import battle_log


class UserCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="profile", description="View your (or another Beyonder's) profile.")
    @app_commands.describe(user="The user to check. Leave blank for your own profile.")
    async def profile(self, interaction: discord.Interaction, user: discord.Member = None):
        if await check_blacklist(interaction): return
        target = user or interaction.user
        is_self = target.id == interaction.user.id

        data = await get_user_data(target.id, interaction.guild.id)
        if not data or not data["pathway"]:
            msg = "Use `/choose_pathway` first!" if is_self else f"{target.mention} has not chosen a pathway yet."
            await interaction.response.send_message(msg, ephemeral=True)
            return
        seq    = data["sequence"]
        name   = get_sequence_name(data["pathway"], seq)
        xp     = data["xp"]
        needed = get_xp_required(seq)
        color  = pathway_colors.get(data["pathway"], 0xf5c400)
        eco    = get_economy(target.id, interaction.guild.id)
        wins   = eco.get("wins", 0)
        losses = eco.get("losses", 0)
        streak = eco.get("win_streak", 0)
        emoji  = get_pathway_emoji(interaction.guild, data["pathway"])

        embed = discord.Embed(
            title=f"𓂃 {emoji} {target.display_name}",
            color=color,
        )
        embed.add_field(name="ᯓ★ Pathway", value=f"**{data['pathway']}**", inline=True)
        embed.add_field(name="ᯓ★ Sequence", value=f"Seq **{seq}** — {name}", inline=True)
        embed.add_field(name="Progress to Next Seq", value=make_xp_bar(xp, needed), inline=False)
        if is_self:
            bal = get_pounds(target.id, guild_id=interaction.guild.id)
            embed.add_field(name="💰 Soli", value=f"{bal:,}", inline=True)
        embed.add_field(name="⚔️ Record", value=f"{wins}W / {losses}L  |  🔥 {streak} streak", inline=True)
        embed.set_thumbnail(url=target.display_avatar.url)
        await interaction.response.send_message(embed=embed)


    @app_commands.command(name="sequence_info", description="Show info for a specific sequence in your pathway")
    @app_commands.describe(sequence_number="Sequence number (0–9)")
    async def sequence_info(self, interaction: discord.Interaction, sequence_number: int):
        if await check_blacklist(interaction): return
        data = await get_user_data(interaction.user.id, interaction.guild.id)
        if not data or not data["pathway"]:
            await interaction.response.send_message("Choose a pathway first!", ephemeral=True)
            return
        if not 0 <= sequence_number <= 9:
            await interaction.response.send_message("Sequence number must be between 0 and 9.", ephemeral=True)
            return
        name   = get_sequence_name(data["pathway"], sequence_number)
        needed = get_xp_required(sequence_number)
        color  = pathway_colors.get(data["pathway"], 0xf5c400)
        emoji  = get_pathway_emoji(interaction.guild, data["pathway"])
        embed  = discord.Embed(
            title=f"{emoji} Sequence {sequence_number}: {name}",
            description=f"**{name}** — {data['pathway']} Pathway\n**XP Required:** {needed:,}",
            color=color,
        )
        await interaction.response.send_message(embed=embed)


    @app_commands.command(name="compare", description="Compare your Beyonder progress with another user")
    @app_commands.describe(user="User to compare with")
    async def compare(self, interaction: discord.Interaction, user: discord.Member):
        if await check_blacklist(interaction): return
        my_data    = await get_user_data(interaction.user.id, interaction.guild.id)
        their_data = await get_user_data(user.id, interaction.guild.id)
        if not my_data or not my_data["pathway"]:
            await interaction.response.send_message("You have not chosen a pathway yet.", ephemeral=True)
            return
        if not their_data or not their_data["pathway"]:
            await interaction.response.send_message(f"{user.mention} has not chosen a pathway yet.", ephemeral=True)
            return

        def build_field(d, member):
            seq    = d["sequence"]
            name   = get_sequence_name(d["pathway"], seq)
            xp     = d["xp"]
            needed = get_xp_required(seq)
            eco    = get_economy(member.id, interaction.guild.id)
            wins   = eco.get("wins", 0)
            losses = eco.get("losses", 0)
            streak = eco.get("win_streak", 0)
            bar    = make_xp_bar(xp, needed)
            return (
                f"**{d['pathway']}** — Seq {seq} ({name})\n"
                f"{bar}\n"
                f"⚔️ {wins}W / {losses}L  🔥 {streak} streak"
            )

        my_emoji    = get_pathway_emoji(interaction.guild, my_data["pathway"])
        their_emoji = get_pathway_emoji(interaction.guild, their_data["pathway"])
        embed = discord.Embed(title="⚔️ Beyonder Comparison", color=0xf5c400)
        embed.add_field(
            name=f"{my_emoji} {interaction.user.display_name}",
            value=build_field(my_data, interaction.user),
            inline=False,
        )
        embed.add_field(
            name=f"{their_emoji} {user.display_name}",
            value=build_field(their_data, user),
            inline=False,
        )
        # Who's ahead
        my_seq = my_data["sequence"]; their_seq = their_data["sequence"]
        my_pct = my_data["xp"] / max(1, get_xp_required(my_seq))
        their_pct = their_data["xp"] / max(1, get_xp_required(their_seq))
        if my_seq < their_seq or (my_seq == their_seq and my_pct > their_pct):
            ahead = interaction.user.display_name
        elif their_seq < my_seq or (their_seq == my_seq and their_pct > my_pct):
            ahead = user.display_name
        else:
            ahead = None
        if ahead:
            embed.set_footer(text=f"🏆 {ahead} is ahead")
        await interaction.response.send_message(embed=embed)


    @app_commands.command(name="pathways", description="List all 22 pathways")
    async def pathways_cmd(self, interaction: discord.Interaction):
        if await check_blacklist(interaction): return
        embed = discord.Embed(title="🃏 THE 22 pathways", color=0xf5c400)
        embed.description = "Use `/choose_pathway` to select yours.\n\n"
        for i, p in enumerate(pathways.keys(), 1):
            embed.description += f"`{i:02d}` **{p}**\n"
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="choose_pathway", description="Choose your Beyonder Pathway (once for normal users)")
    @app_commands.describe(pathway="Your chosen path")
    @app_commands.choices(pathway=[app_commands.Choice(name=p, value=p) for p in sorted(pathways.keys())])
    async def choose_pathway(self, interaction: discord.Interaction, pathway: str):
        if await check_blacklist(interaction): return
        data = await get_user_data(interaction.user.id, interaction.guild.id)
        if data and data["pathway"] and not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message(
                "❌ You have already chosen a pathway. Only admins can switch.", ephemeral=True
            )
            return

        await update_user(interaction.user.id, guild_id=interaction.guild.id, pathway=pathway, sequence=9, xp=0)
        try:
            member = interaction.guild.get_member(interaction.user.id) or await interaction.guild.fetch_member(interaction.user.id)
        except (discord.NotFound, discord.HTTPException):
            member = None
        if member:
            await assign_sequence_role(member, pathway, 9)

        name = get_sequence_name(pathway, 9)

        await log_event(
            event_type="PATHWAY_CHOICE",
            details=f"Chose the **{pathway}** Pathway",
            user_id=interaction.user.id,
            username=str(interaction.user),
            guild=interaction.guild
        )

        color = pathway_colors.get(pathway, 0xf5c400)
        embed = discord.Embed(
            title="🔥 YOU HAVE CHOSEN!",
            description=f"{interaction.user.mention} is now **Sequence 9 — {name}** of the **{pathway} Pathway**!",
            color=color
        )
        await interaction.response.send_message(embed=embed)


    @app_commands.command(name="leaderboard", description="Top Beyonders by Sequence")
    async def leaderboard(self, interaction: discord.Interaction):
        if await check_blacklist(interaction): return
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute(
                "SELECT user_id, pathway, sequence, xp FROM users WHERE pathway IS NOT NULL AND guild_id = ? ORDER BY sequence ASC, xp DESC LIMIT 10"
            , (interaction.guild.id,)) as cursor:
                rows = await cursor.fetchall()
        embed = discord.Embed(title="🏆 TOP BEYONDERS", color=0xf5c400)
        desc = ""
        medals = ["🥇", "🥈", "🥉"]
        for rank, (uid, path, seq, xp) in enumerate(rows, 1):
            member = interaction.guild.get_member(uid)
            user_str = member.display_name if member else f"<@{uid}>"
            name   = get_sequence_name(path, seq)
            needed = get_xp_required(seq)
            pct    = min(int((xp / needed) * 100), 100) if needed else 100
            prefix = medals[rank - 1] if rank <= 3 else f"`#{rank:02d}`"
            emoji  = get_pathway_emoji(interaction.guild, path)
            desc  += f"{prefix} {emoji} **{user_str}** — {path} Seq {seq} ({name})\n"
        embed.description = desc or "No Beyonders yet..."
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="daily", description="Claim your daily XP bonus and soli reward")
    async def daily(self, interaction: discord.Interaction):
        import time
        if await check_blacklist(interaction): return
        data = await get_user_data(interaction.user.id, interaction.guild.id)
        if not data or not data["pathway"]:
            await interaction.response.send_message("Choose a pathway first with `/choose_pathway`!", ephemeral=True)
            return

        now_dt = datetime.utcnow()
        now_ts = time.time()
        TWENTY_FOUR_HOURS = 86400
        FORTY_EIGHT_HOURS = 172800

        # ── XP cooldown check ──
        last_daily = parse_iso(data["last_daily"])
        if last_daily and (now_dt - last_daily) < timedelta(hours=24):
            remaining = timedelta(hours=24) - (now_dt - last_daily)
            hours, rem = divmod(int(remaining.total_seconds()), 3600)
            minutes = rem // 60
            await interaction.response.send_message(
                f"⏳ You already claimed your daily. Come back in **{hours}h {minutes}m**.", ephemeral=True
            )
            return

        # ── XP reward ──
        bonus = 150
        seq, xp, _ = await apply_xp_gain(
            user_id=interaction.user.id,
            guild=interaction.guild,
            pathway=data["pathway"],
            current_seq=data["sequence"],
            current_xp=data["xp"],
            gained_xp=bonus,
            mention=interaction.user.mention
        )
        await update_user(interaction.user.id, guild_id=interaction.guild.id, last_daily=now_dt.isoformat())

        # ── Soli reward ──
        user_id = interaction.user.id
        soli_entry = daily_data.get(user_id, {"last": 0, "streak": 0})
        elapsed = now_ts - soli_entry["last"]
        if elapsed > FORTY_EIGHT_HOURS and soli_entry["last"] != 0:
            streak = 1
        else:
            streak = soli_entry["streak"] + 1
        reward = 1000 + (streak - 1) * 20
        daily_data[user_id] = {"last": now_ts, "streak": streak}
        save_daily()
        add_pounds(user_id, reward, guild_id=interaction.guild_id)
        bal = get_pounds(user_id, guild_id=interaction.guild_id)

        streak_line = f"🔥 **Streak: {streak} day{'s' if streak != 1 else ''}** — bonus **+{(streak - 1) * 20} soli**!" if streak > 1 else "🌅 Streak started!"

        await log_event(
            event_type="DAILY_CLAIM",
            details=f"Claimed daily bonus (+{bonus} XP, +{reward} soli)",
            user_id=interaction.user.id,
            username=str(interaction.user),
            guild=interaction.guild
        )

        embed = discord.Embed(
            title="📅 Daily Reward Claimed!",
            description=(
                f"**{interaction.user.display_name}** collected their daily reward.\n\n"
                f"✨ **+{bonus} XP** — now at **Seq {seq}** with **{xp} XP**\n"
                f"💰 **+{reward:,} soli** added!\n"
                f"{streak_line}\n\n"
                f"💼 Balance: **{bal:,} soli**"
            ),
            color=0xF1C40F
        )
        embed.set_footer(text="Come back tomorrow to keep your streak!")
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="pray", description="Pray for a small XP blessing")
    async def pray(self, interaction: discord.Interaction):
        if await check_blacklist(interaction): return
        data = await get_user_data(interaction.user.id, interaction.guild.id)
        if not data or not data["pathway"]:
            await interaction.response.send_message("Choose a pathway first with `/choose_pathway`!", ephemeral=True)
            return

        now = datetime.utcnow()
        pray_cd = timedelta(hours=1)
        last_pray = parse_iso(data["last_pray"])

        if last_pray and (now - last_pray) < pray_cd:
            remaining = pray_cd - (now - last_pray)
            hours, rem = divmod(int(remaining.total_seconds()), 3600)
            minutes = rem // 60
            await interaction.response.send_message(
                f"⏳ Your prayer is still on cooldown. Come back in **{hours}h {minutes}m**.",
                ephemeral=True
            )
            return

        bonus = random.randint(30, 80)
        seq, xp, _ = await apply_xp_gain(
            user_id=interaction.user.id,
            guild=interaction.guild,
            pathway=data["pathway"],
            current_seq=data["sequence"],
            current_xp=data["xp"],
            gained_xp=bonus,
            mention=interaction.user.mention
        )

        await update_user(interaction.user.id, guild_id=interaction.guild.id, last_pray=now.isoformat())

        await log_event(
            event_type="PRAY",
            details=f"Prayed and received +{bonus} XP blessing",
            user_id=interaction.user.id,
            username=str(interaction.user),
            guild=interaction.guild
        )

        await interaction.response.send_message(
            f"🙏 The Fool smiles upon you... You gained **{bonus} XP**!\nNow at **Seq {seq}** with **{xp} XP**."
        )


    @app_commands.command(name="guide", description="Learn how the bot works — commands, fighting, economy and more.")
    async def guide_cmd(self, interaction: discord.Interaction):
        await interaction.response.send_message(embed=INFO_PAGES[0], view=InfoView(page=0), ephemeral=True)


    @app_commands.command(name="pathway_ability", description="See what ability each sequence of a pathway has.")
    async def pathway_ability(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="Pathway Ability Lookup",
            description="Select a pathway below to see its Seq 9 through 5 abilities.",
            color=0x9B59B6
        )
        await interaction.response.send_message(embed=embed, view=PathwayAbilitySelectView(interaction.guild), ephemeral=True)


    # ── Pathway flavour for /stats ─────────────────────────────
    # Describes each pathway's combat identity shown in /stats

    @app_commands.command(name="stats_user", description="View your (or another Beyonder's) combat stats.")
    @app_commands.describe(user="The user to check. Leave blank for your own stats.")
    async def stats_user(self, interaction: discord.Interaction, user: discord.Member = None):
        try:
            target = user or interaction.user
            embed = build_stats_embed(target)

            is_self = target.id == interaction.user.id
            view = None
            if is_self:
                raw = get_or_create_stats(target.id, target)
                if raw.get("points", 0) > 0:
                    view = StatsView(target.id)

            await interaction.response.send_message(embed=embed, view=view)
        except Exception as e:
            await interaction.response.send_message(f"❌ Error loading stats: `{e}`", ephemeral=True)


    @app_commands.command(name="log_apostle", description="View recent battle results.")
    async def log_apostle(self, interaction: discord.Interaction):
        if not battle_log:
            return await interaction.response.send_message("📜 No battles recorded yet.", ephemeral=True)
        lines = ["📜 **Battle Log — Recent Fights**\n"]
        for entry in reversed(battle_log[-20:]):
            import datetime
            ts = datetime.datetime.fromtimestamp(entry["ts"]).strftime("%d/%m %H:%M")
            lines.append(
                f"`{ts}` 🏆 **{entry['winner']}** *(Seq {entry['winner_seq']})* "
                f"defeated **{entry['loser']}** *(Seq {entry['loser_seq']})*"
            )
        embed = discord.Embed(
            title="📜 Apostle Battle Log",
            description="\n".join(lines),
            color=0x9B59B6
        )
        await interaction.response.send_message(embed=embed)



    @app_commands.command(name="winstreak", description="Check your battle record and win streak.")
    @app_commands.describe(user="The user to check. Leave blank for your own.")
    async def winstreak_cmd(self, interaction: discord.Interaction, user: discord.Member = None):
        target = user or interaction.user
        eco = get_economy(target.id, guild_id=interaction.guild_id)
        wins = eco.get("wins", 0)
        losses = eco.get("losses", 0)
        streak = eco.get("win_streak", 0)
        total = wins + losses
        winrate = int((wins / total) * 100) if total > 0 else 0
        streak_txt = f"🔥 **{streak}** win streak" if streak >= 2 else ("✨ On a win" if streak == 1 else "💔 No active streak")
        embed = discord.Embed(
            title=f"𓂃 {target.display_name}'s Battle Record",
            description=(
                f"🏆 **Wins:** {wins}\n"
                f"💀 **Losses:** {losses}\n"
                f"📈 **Win Rate:** {winrate}%\n"
                f"{streak_txt}"
            ),
            color=0xF1C40F if streak >= 2 else 0x9B59B6
        )
        embed.set_thumbnail(url=target.display_avatar.url)
        await interaction.response.send_message(embed=embed)




async def setup(bot):
    await bot.add_cog(UserCommands(bot))
