# ============================================================
# OWNER COMMANDS — bot-owner-only slash commands: wiping a user's
# data, blacklisting, kicking/banning, and the one-off cross-server
# data migration tool.
# ============================================================
import discord
import aiosqlite
from datetime import datetime
from discord import app_commands
from discord.ext import commands

from config import DB_PATH, OWNER_ID
from utils import log_event
from roles import remove_all_lotm_sequence_roles
from database import get_user_data
from persistence import player_economy, save_economy

_TRANSFER_SRC_GUILD = 1452586968486514842
_TRANSFER_DST_GUILD = 1514229914050498660


class OwnerCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="wipe_user", description="Owner only: Completely wipe a user's data (XP, pathway, sequence)")
    @app_commands.describe(user="The user to wipe")
    async def wipe_user(self, interaction: discord.Interaction, user: discord.Member):
        if interaction.user.id != OWNER_ID:
            await interaction.response.send_message("❌ You don't have permission to use this command.", ephemeral=True)
            return

        data = await get_user_data(user.id, interaction.guild.id)
        if not data or not data["pathway"]:
            await interaction.response.send_message(f"{user.mention} has no data to wipe.", ephemeral=True)
            return

        # Remove all LOTM sequence roles from the member
        await remove_all_lotm_sequence_roles(user)

        # Delete their row from the DB entirely
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("DELETE FROM users WHERE user_id = ? AND guild_id = ?", (user.id, interaction.guild.id))
            await db.commit()

        await log_event(
            event_type="OWNER_WIPE_USER",
            details="Completely wiped user data (pathway, sequence, XP, cooldowns)",
            user_id=interaction.user.id,
            username=str(interaction.user),
            guild=interaction.guild,
            target_id=user.id,
            target_username=str(user)
        )

        await interaction.response.send_message(
            f"🗑️ {user.mention}'s data has been completely wiped.\n"
            f"They have no pathway, no sequence, and no XP. They can start fresh with `/choose_pathway`.",
            ephemeral=True
        )

    @app_commands.command(name="blacklist", description="Owner only: Blacklist or unblacklist a user from using the bot")
    @app_commands.describe(action="Add or remove from the blacklist", user="The target user", reason="Reason (only used when adding)")
    @app_commands.choices(action=[
        app_commands.Choice(name="Add", value="add"),
        app_commands.Choice(name="Remove", value="remove"),
    ])
    async def blacklist_user(self, interaction: discord.Interaction, action: app_commands.Choice[str], user: discord.Member, reason: str = "No reason provided"):
        if interaction.user.id != OWNER_ID:
            await interaction.response.send_message("❌ You don't have permission to use this command.", ephemeral=True)
            return

        if action.value == "add":
            if user.id == OWNER_ID:
                await interaction.response.send_message("❌ You cannot blacklist yourself.", ephemeral=True)
                return

            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute(
                    "INSERT OR REPLACE INTO blacklist (user_id, guild_id, reason) VALUES (?, ?, ?)",
                    (user.id, interaction.guild.id, reason)
                )
                await db.commit()

            await log_event(
                event_type="OWNER_BLACKLIST",
                details=f"Blacklisted user — Reason: {reason}",
                user_id=interaction.user.id,
                username=str(interaction.user),
                guild=interaction.guild,
                target_id=user.id,
                target_username=str(user)
            )

            await interaction.response.send_message(
                f"🚫 {user.mention} has been blacklisted from using the bot.\nReason: **{reason}**",
                ephemeral=True
            )
        else:
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute(
                    "DELETE FROM blacklist WHERE user_id = ? AND guild_id = ?",
                    (user.id, interaction.guild.id)
                )
                await db.commit()

            await log_event(
                event_type="OWNER_UNBLACKLIST",
                details="Removed user from blacklist",
                user_id=interaction.user.id,
                username=str(interaction.user),
                guild=interaction.guild,
                target_id=user.id,
                target_username=str(user)
            )

            await interaction.response.send_message(
                f"✅ {user.mention} has been removed from the blacklist.",
                ephemeral=True
            )


    @app_commands.command(name="kick", description="Owner only: Kick a user from the server")
    @app_commands.describe(user="The user to kick", reason="Reason for kick")
    async def kick_user(self, interaction: discord.Interaction, user: discord.Member, reason: str = "No reason provided"):
        if interaction.user.id != OWNER_ID:
            await interaction.response.send_message("❌ You don't have permission to use this command.", ephemeral=True)
            return

        if user.id == OWNER_ID:
            await interaction.response.send_message("❌ You cannot kick yourself.", ephemeral=True)
            return

        try:
            await user.kick(reason=reason)
        except discord.Forbidden:
            await interaction.response.send_message("❌ I don't have permission to kick that user.", ephemeral=True)
            return

        await log_event(
            event_type="OWNER_KICK",
            details=f"Kicked user — Reason: {reason}",
            user_id=interaction.user.id,
            username=str(interaction.user),
            guild=interaction.guild,
            target_id=user.id,
            target_username=str(user)
        )

        await interaction.response.send_message(
            f"👢 {user.mention} has been kicked.\nReason: **{reason}**",
            ephemeral=True
        )


    @app_commands.command(name="ban", description="Owner only: Ban a user from the server")
    @app_commands.describe(user="The user to ban", reason="Reason for ban")
    async def ban_user(self, interaction: discord.Interaction, user: discord.Member, reason: str = "No reason provided"):
        if interaction.user.id != OWNER_ID:
            await interaction.response.send_message("❌ You don't have permission to use this command.", ephemeral=True)
            return

        if user.id == OWNER_ID:
            await interaction.response.send_message("❌ You cannot ban yourself.", ephemeral=True)
            return

        try:
            await user.ban(reason=reason, delete_message_days=0)
        except discord.Forbidden:
            await interaction.response.send_message("❌ I don't have permission to ban that user.", ephemeral=True)
            return

        await log_event(
            event_type="OWNER_BAN",
            details=f"Banned user — Reason: {reason}",
            user_id=interaction.user.id,
            username=str(interaction.user),
            guild=interaction.guild,
            target_id=user.id,
            target_username=str(user)
        )

        await interaction.response.send_message(
            f"🔨 {user.mention} has been banned.\nReason: **{reason}**",
            ephemeral=True
        )



    # ── /transfer_data command (owner only) ──────────────────────────────────────
    # Copies ALL data from source guild 1452586968486514842
    #                  → destination guild 1514229914050498660
    # Covers: users, player_items, chair_shop (SQLite) + economy JSON

    @app_commands.command(name="transfer_data", description="Owner only: Copy all data from the source server to the destination server.")
    async def transfer_data(self, interaction: discord.Interaction):
        if interaction.user.id != OWNER_ID:
            return await interaction.response.send_message("❌ Owner only.", ephemeral=True)

        await interaction.response.defer(ephemeral=True)
        import copy
        src = _TRANSFER_SRC_GUILD
        dst = _TRANSFER_DST_GUILD
        report = []

        async with aiosqlite.connect(DB_PATH) as db:

            # ── 1. users ─────────────────────────────────────────────────────────
            async with db.execute(
                "SELECT user_id, pathway, sequence, xp, last_message, last_daily, last_pray FROM users WHERE guild_id=?",
                (src,)
            ) as cur:
                user_rows = await cur.fetchall()

            if user_rows:
                await db.execute("DELETE FROM users WHERE guild_id=?", (dst,))
                for row in user_rows:
                    await db.execute(
                        """INSERT OR REPLACE INTO users
                           (user_id, guild_id, pathway, sequence, xp, last_message, last_daily, last_pray)
                           VALUES (?,?,?,?,?,?,?,?)""",
                        (row[0], dst, row[1], row[2], row[3], row[4], row[5], row[6])
                    )
                report.append(f"✅ **users**: {len(user_rows)} player(s) transferred")
            else:
                report.append("⚠️ **users**: no data in source guild — skipped")

            # ── 2. player_items ──────────────────────────────────────────────────
            async with db.execute(
                "SELECT user_id, item_name, quantity FROM player_items WHERE guild_id=?", (src,)
            ) as cur:
                item_rows = await cur.fetchall()

            if item_rows:
                await db.execute("DELETE FROM player_items WHERE guild_id=?", (dst,))
                for user_id, item_name, qty in item_rows:
                    await db.execute(
                        "INSERT INTO player_items(user_id,guild_id,item_name,quantity) VALUES(?,?,?,?)",
                        (user_id, dst, item_name, qty)
                    )
                report.append(f"✅ **player_items**: {len(item_rows)} row(s) transferred")
            else:
                report.append("⚠️ **player_items**: nothing to transfer")

            # ── 3. chair_shop ────────────────────────────────────────────────────
            async with db.execute(
                "SELECT user_id, last_buy, bonus_chair, bonus_expires FROM chair_shop WHERE guild_id=?", (src,)
            ) as cur:
                chair_rows = await cur.fetchall()

            if chair_rows:
                await db.execute("DELETE FROM chair_shop WHERE guild_id=?", (dst,))
                for user_id, last_buy, bonus_chair, bonus_expires in chair_rows:
                    await db.execute(
                        """INSERT OR REPLACE INTO chair_shop(user_id,guild_id,last_buy,bonus_chair,bonus_expires)
                           VALUES(?,?,?,?,?)""",
                        (user_id, dst, last_buy, bonus_chair, bonus_expires)
                    )
                report.append(f"✅ **chair_shop**: {len(chair_rows)} row(s) transferred")
            else:
                report.append("⚠️ **chair_shop**: nothing to transfer")

            await db.commit()

        # ── 4. Economy JSON ──────────────────────────────────────────────────────
        # Keys are formatted as "guild_id:user_id" — copy all keys for src guild
        src_prefix = f"{src}:"
        dst_prefix = f"{dst}:"
        src_eco_keys = [k for k in player_economy if k.startswith(src_prefix)]

        if src_eco_keys:
            # Remove existing dst entries
            for k in [k for k in player_economy if k.startswith(dst_prefix)]:
                del player_economy[k]
            for k in src_eco_keys:
                user_id_part = k[len(src_prefix):]
                new_key = f"{dst}:{user_id_part}"
                player_economy[new_key] = copy.deepcopy(player_economy[k])
            save_economy()
            report.append(f"✅ **economy**: {len(src_eco_keys)} player(s) transferred")
        else:
            report.append("⚠️ **economy**: no entries for source guild — skipped")

        await log_event(
            event_type="OWNER_TRANSFER_GUILD_DATA",
            details=f"Transferred all guild data from {src} → {dst}",
            user_id=interaction.user.id,
            username=str(interaction.user),
            guild=interaction.guild,
            target_id=dst
        )

        embed = discord.Embed(
            title="🔄 Guild Data Transfer Complete",
            description=f"**Source Guild:** `{src}`\n**Destination Guild:** `{dst}`\n\n" + "\n".join(report),
            color=0x00ff88,
            timestamp=datetime.utcnow()
        )
        embed.set_footer(text="LOTM Beyonder Bot — Owner Transfer")
        await interaction.followup.send(embed=embed, ephemeral=True)


    # ╔══════════════════════════════════════════════════════════════════════════════╗
    # ║          UNIFIED SHOP  (Abilities)                                          ║
    # ╚══════════════════════════════════════════════════════════════════════════════╝



async def setup(bot):
    await bot.add_cog(OwnerCommands(bot))
