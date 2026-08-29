# ============================================================
# ADMIN COMMANDS — server-admin-gated slash commands (require
# `is_bot_admin`): whitelist management, role/channel setup, granting/
# taking XP or soli, resets, and moderation-adjacent utilities.
# ============================================================
import discord
import aiosqlite
from datetime import datetime
from discord import app_commands
from discord.ext import commands

from bot_instance import bot
from logging_config import logger
from config import DB_PATH, OWNER_ID, pathways, pathway_colors
from utils import log_event, _require_bot_admin
from roles import (
    get_sequence_name, get_or_create_role, assign_sequence_role,
    is_lotm_sequence_role_name, remove_all_lotm_sequence_roles,
)
from xp import apply_xp_gain, handle_xp_removal
from database import get_user_data, update_user, get_setting, set_setting, db_add_item
from economy import add_pounds, remove_pounds, wipe_pounds, get_pounds
from persistence import allowed_channel, save_config, log_channel, bot_admin_whitelist
from battle.views import PathwaySelectView


class AdminCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="pathway_menu", description="Admin only: Post the pathway select menu in this channel")
    async def pathway_menu(self, interaction: discord.Interaction):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("❌ Only admins can post the pathway menu.", ephemeral=True)
            return

        embed = discord.Embed(
            title="🃏 SELECT YOUR BEYONDER PATHWAY",
            description=(
                "Each pathway has **9 Sequences** — you begin at **Sequence 9**.\n\n"
                "**This choice is yours.**"
            ),
            color=0xf5c400
        )
        embed.set_footer(text="Lord of the Mysteries — Beyonder Tracker")

        view = PathwaySelectView(interaction.guild)
        # Non-ephemeral — posted publicly in the channel
        await interaction.response.send_message(embed=embed, view=view)



    @app_commands.command(name="admin_whitelist", description="Owner only: Grant or revoke access to Admin: commands for a user")
    @app_commands.describe(action="Add or remove from the whitelist", user="The target user")
    @app_commands.choices(action=[
        app_commands.Choice(name="Add", value="add"),
        app_commands.Choice(name="Remove", value="remove"),
        app_commands.Choice(name="List", value="list"),
    ])
    async def admin_whitelist_cmd(self, interaction: discord.Interaction, action: app_commands.Choice[str], user: discord.Member = None):
        if interaction.user.id != OWNER_ID:
            await interaction.response.send_message("❌ Only the bot owner can manage the admin whitelist.", ephemeral=True)
            return

        if action.value == "list":
            if not bot_admin_whitelist:
                await interaction.response.send_message("The admin whitelist is empty.", ephemeral=True)
                return
            lines = "\n".join(f"• <@{uid}>" for uid in bot_admin_whitelist)
            await interaction.response.send_message(f"**Whitelisted for Admin: commands:**\n{lines}", ephemeral=True)
            return

        if not user:
            await interaction.response.send_message("❌ You must specify a user for add/remove.", ephemeral=True)
            return

        if action.value == "add":
            bot_admin_whitelist.add(user.id)
            save_config()
            await interaction.response.send_message(f"✅ {user.mention} can now use Admin: commands.", ephemeral=True)
        else:
            bot_admin_whitelist.discard(user.id)
            save_config()
            await interaction.response.send_message(f"✅ {user.mention} can no longer use Admin: commands (unless they're a server Administrator).", ephemeral=True)

        await log_event(
            event_type="OWNER_ADMIN_WHITELIST",
            details=f"{action.value} whitelist entry",
            user_id=interaction.user.id,
            username=str(interaction.user),
            guild=interaction.guild,
            target_id=user.id,
            target_username=str(user)
        )

    @app_commands.command(name="force_sync", description="Force sync all slash commands (Admin only)")
    async def force_sync(self, interaction: discord.Interaction):
        if not await _require_bot_admin(interaction):
            return
        synced = await bot.tree.sync()
        await interaction.response.send_message(f"✅ Synced {len(synced)} slash commands.", ephemeral=True)
        await log_event(
            event_type="ADMIN_FORCE_SYNC",
            details=f"Force synced {len(synced)} slash commands",
            user_id=interaction.user.id,
            username=str(interaction.user),
            guild=interaction.guild
        )

    @app_commands.command(name="migrate_guild", description="Admin: Migrate existing user data to this server (run once after update)")
    async def migrate_guild(self, interaction: discord.Interaction):
        if not await _require_bot_admin(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        guild_id = interaction.guild.id
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute("SELECT COUNT(*) FROM users WHERE guild_id = 0") as cursor:
                count = (await cursor.fetchone())[0]
            if count == 0:
                await interaction.followup.send("✅ No orphaned data found — everyone is already assigned to a server. Nothing to migrate.", ephemeral=True)
                return
            await db.execute("UPDATE users SET guild_id = ? WHERE guild_id = 0", (guild_id,))
            await db.commit()
        await interaction.followup.send(
            f"✅ Migration complete! **{count} user(s)** have been assigned to this server.\n"
            f"All XP, pathways, and sequences are preserved.",
            ephemeral=True
        )
        await log_event(
            event_type="ADMIN_MIGRATE_GUILD",
            details=f"Migrated {count} user(s) to guild {guild_id}",
            user_id=interaction.user.id,
            username=str(interaction.user),
            guild=interaction.guild
        )

    @app_commands.command(name="setup_roles", description="Admin: Create all Sequence roles")
    async def setup_roles(self, interaction: discord.Interaction):
        if not await _require_bot_admin(interaction):
            return
        await interaction.response.defer()
        count = 0
        for pathway in pathways:
            for seq in range(9, -1, -1):
                seq_name = get_sequence_name(pathway, seq)
                role_name = f"[{pathway}] True God — {seq_name}" if seq == 0 else f"[{pathway}] Seq {seq} — {seq_name}"
                await get_or_create_role(interaction.guild, role_name, pathway)
                count += 1
        await interaction.followup.send(f"✅ Created/Verified **{count}** Sequence roles!")
        await log_event(
            event_type="ADMIN_SETUP_ROLES",
            details=f"Created/Verified {count} Sequence roles",
            user_id=interaction.user.id,
            username=str(interaction.user),
            guild=interaction.guild
        )

    @app_commands.command(name="delete_all_roles", description="Admin: Delete all LOTM sequence roles from the server")
    async def delete_all_roles(self, interaction: discord.Interaction):
        if not await _require_bot_admin(interaction):
            return
        await interaction.response.defer()
        lotm_roles = [r for r in interaction.guild.roles if is_lotm_sequence_role_name(r.name)]
        deleted = 0
        failed = 0
        for role in lotm_roles:
            try:
                await role.delete(reason="LOTM bulk role deletion by admin")
                deleted += 1
            except Exception:
                logger.warning(f"[delete_all_roles] Could not delete role '{role.name}'", exc_info=True)
                failed += 1
        msg = f"✅ Deleted **{deleted}** LOTM sequence roles."
        if failed:
            msg += f"\n⚠️ Failed to delete **{failed}** roles (check bot permissions)."
        await interaction.followup.send(msg)
        await log_event(
            event_type="ADMIN_DELETE_ALL_ROLES",
            details=f"Deleted {deleted} LOTM roles ({failed} failed)",
            user_id=interaction.user.id,
            username=str(interaction.user),
            guild=interaction.guild
        )

    @app_commands.command(name="set_announcement_channel", description="Admin: Set channel for announcements")
    async def set_announcement_channel(self, interaction: discord.Interaction, channel: discord.TextChannel):
        if not await _require_bot_admin(interaction):
            return
        await set_setting("announcement_channel", str(channel.id))
        await interaction.response.send_message(f"✅ Announcements will now go to {channel.mention}")
        await log_event(
            event_type="ADMIN_SET_ANNOUNCEMENT_CHANNEL",
            details=f"Set announcement channel to #{channel.name}",
            user_id=interaction.user.id,
            username=str(interaction.user),
            guild=interaction.guild
        )

    @app_commands.command(name="set_admin_log_channel", description="Admin: Set the channel where the bot sends ALL admin/audit logs")
    async def set_admin_log_channel(self, interaction: discord.Interaction, channel: discord.TextChannel):
        if not await _require_bot_admin(interaction):
            return
        await set_setting("log_channel", str(channel.id))
        await interaction.response.send_message(
            f"✅ **Log channel successfully set to {channel.mention}**\n"
            f"All bot events, commands, and actions will now be logged here."
        )
        # Write to DB directly, then send embed straight to channel
        # (can't use log_event here — it reads log_channel before the setting above is visible)
        now = datetime.utcnow().isoformat()
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "INSERT INTO logs (timestamp, event_type, user_id, username, guild_id, details) VALUES (?, ?, ?, ?, ?, ?)",
                (now, "LOG_CHANNEL_SET", interaction.user.id, str(interaction.user), interaction.guild.id, f"Log channel set to #{channel.name}")
            )
            await db.commit()
        embed = discord.Embed(
            title="📋 LOG: LOG_CHANNEL_SET",
            description=f"Log channel set to #{channel.name}",
            color=0xffaa00,
            timestamp=datetime.utcnow()
        )
        embed.add_field(name="User", value=f"{interaction.user} (<@{interaction.user.id}>)", inline=True)
        embed.add_field(name="Server", value=interaction.guild.name, inline=True)
        embed.set_footer(text="LOTM Beyonder Bot • Logging System")
        try:
            await channel.send(embed=embed)
        except Exception:
            logger.warning(f"[set_admin_log_channel] Could not post confirmation to #{channel.name}", exc_info=True)

    @app_commands.command(name="xp", description="Admin: Give, take, or wipe a user's XP")
    @app_commands.describe(action="Give, take, or wipe XP", user="Target user", amount="Amount of XP (not needed for Wipe)")
    @app_commands.choices(action=[
        app_commands.Choice(name="Give", value="give"),
        app_commands.Choice(name="Take", value="take"),
        app_commands.Choice(name="Wipe", value="wipe"),
    ])
    async def xp_cmd(self, interaction: discord.Interaction, action: app_commands.Choice[str], user: discord.Member, amount: int = 0):
        if not await _require_bot_admin(interaction):
            return
        if action.value in ("give", "take") and amount <= 0:
            await interaction.response.send_message("Amount must be positive.", ephemeral=True)
            return

        data = await get_user_data(user.id, interaction.guild.id)
        if not data or not data["pathway"]:
            await interaction.response.send_message(f"{user.mention} has not chosen a pathway yet.", ephemeral=True)
            return

        if action.value == "give":
            seq, xp, _ = await apply_xp_gain(
                user_id=user.id,
                guild=interaction.guild,
                pathway=data["pathway"],
                current_seq=data["sequence"],
                current_xp=data["xp"],
                gained_xp=amount,
                mention=user.mention
            )
            await interaction.response.send_message(
                f"✅ Gave **{amount} XP** to {user.mention}.\nNow at **Seq {seq}** with **{xp} XP**."
            )
            await log_event(
                event_type="ADMIN_GIVE_XP",
                details=f"Admin gave **{amount} XP**",
                user_id=interaction.user.id,
                username=str(interaction.user),
                guild=interaction.guild,
                target_id=user.id,
                target_username=str(user)
            )

        elif action.value == "take":
            new_seq, new_xp, demoted = await handle_xp_removal(interaction.guild, user.id, data, amount)
            await update_user(user.id, guild_id=interaction.guild.id, sequence=new_seq, xp=new_xp)

            if demoted:
                try:
                    member = interaction.guild.get_member(user.id) or await interaction.guild.fetch_member(user.id)
                except (discord.NotFound, discord.HTTPException):
                    member = None
                if member:
                    await assign_sequence_role(member, data["pathway"], new_seq)
                seq_name = get_sequence_name(data["pathway"], new_seq)
                channel_id = await get_setting("announcement_channel")
                if channel_id:
                    channel = interaction.guild.get_channel(int(channel_id))
                    if channel:
                        color = pathway_colors.get(data["pathway"], 0xf5c400)
                        embed = discord.Embed(title="⚠️ SEQUENCE DEMOTION", color=color)
                        embed.description = (
                            f"{user.mention} has been demoted to **Sequence {new_seq} — {seq_name}**\n"
                            f"in the **{data['pathway']} Pathway**.\n\n"
                            f"*The path of a Beyonder is not without its setbacks...*"
                        )
                        embed.set_footer(text="Lord of the Mysteries — Beyonder Tracker")
                        await channel.send(embed=embed)

            await interaction.response.send_message(
                f"✅ Removed **{amount} XP** from {user.mention}. "
                f"Now at **Seq {new_seq}** with **{new_xp} XP**."
            )
            await log_event(
                event_type="ADMIN_TAKE_XP",
                details=f"Admin removed **{amount} XP** (Demoted: {demoted})",
                user_id=interaction.user.id,
                username=str(interaction.user),
                guild=interaction.guild,
                target_id=user.id,
                target_username=str(user)
            )

        else:  # wipe
            await update_user(user.id, guild_id=interaction.guild.id, xp=0)
            seq_name = get_sequence_name(data["pathway"], data["sequence"])
            await interaction.response.send_message(
                f"✅ Wiped all XP from {user.mention}. "
                f"They remain at **Sequence {data['sequence']} — {seq_name}** with **0 XP**."
            )
            await log_event(
                event_type="ADMIN_WIPE_XP",
                details=f"Wiped all XP (kept Sequence {data['sequence']})",
                user_id=interaction.user.id,
                username=str(interaction.user),
                guild=interaction.guild,
                target_id=user.id,
                target_username=str(user)
            )

    @app_commands.command(name="reset_user", description="Admin: Reset a user to Sequence 9")
    @app_commands.describe(user="Target user")
    async def reset_user(self, interaction: discord.Interaction, user: discord.Member):
        if not await _require_bot_admin(interaction):
            return
        data = await get_user_data(user.id, interaction.guild.id)
        if not data or not data["pathway"]:
            await interaction.response.send_message(f"{user.mention} has no pathway set.", ephemeral=True)
            return
        await update_user(user.id, guild_id=interaction.guild.id, sequence=9, xp=0)
        try:
            member = interaction.guild.get_member(user.id) or await interaction.guild.fetch_member(user.id)
        except (discord.NotFound, discord.HTTPException):
            member = None
        if member:
            await assign_sequence_role(member, data["pathway"], 9)
        await interaction.response.send_message(f"✅ {user.mention} has been reset to Sequence 9.")
        await log_event(
            event_type="ADMIN_RESET_USER",
            details=f"Reset user to Sequence 9",
            user_id=interaction.user.id,
            username=str(interaction.user),
            guild=interaction.guild,
            target_id=user.id,
            target_username=str(user)
        )

    @app_commands.command(name="reset_all", description="Admin: Reset ALL users' XP and sequence to 9 in this server")
    async def reset_all(self, interaction: discord.Interaction):
        if not await _require_bot_admin(interaction):
            return
        # Defer since this could take a moment
        await interaction.response.defer(ephemeral=True)

        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute(
                "SELECT user_id FROM users WHERE guild_id = ? AND pathway IS NOT NULL",
                (interaction.guild.id,)
            ) as cursor:
                rows = await cursor.fetchall()

            await db.execute(
                "UPDATE users SET xp = 0, sequence = 9 WHERE guild_id = ?",
                (interaction.guild.id,)
            )
            await db.commit()

        count = len(rows)

        await log_event(
            event_type="ADMIN_RESET_ALL",
            details=f"Reset ALL {count} users to Sequence 9 with 0 XP",
            user_id=interaction.user.id,
            username=str(interaction.user),
            guild=interaction.guild
        )

        await interaction.followup.send(
            f"✅ Reset **{count} Beyonders** in this server to **Sequence 9 / 0 XP**.",
            ephemeral=True
        )

    @app_commands.command(name="force_choose_pathway", description="Admin: Force a user to a new pathway")
    @app_commands.describe(user="Target user", pathway="New pathway")
    @app_commands.choices(pathway=[app_commands.Choice(name=p, value=p) for p in sorted(pathways.keys())])
    async def force_choose_pathway(self, interaction: discord.Interaction, user: discord.Member, pathway: str):
        if not await _require_bot_admin(interaction):
            return
        await update_user(user.id, guild_id=interaction.guild.id, pathway=pathway, sequence=9, xp=0)
        member = interaction.guild.get_member(user.id)
        if member:
            await remove_all_lotm_sequence_roles(member)
            await assign_sequence_role(member, pathway, 9)
        await interaction.response.send_message(f"✅ Forced {user.mention} to the **{pathway} Pathway**.")
        await log_event(
            event_type="ADMIN_FORCE_PATHWAY",
            details=f"Force changed pathway to **{pathway}**",
            user_id=interaction.user.id,
            username=str(interaction.user),
            guild=interaction.guild,
            target_id=user.id,
            target_username=str(user)
        )

    @app_commands.command(name="bot_stats", description="Admin: Show server statistics")
    async def bot_stats(self, interaction: discord.Interaction):
        if not await _require_bot_admin(interaction):
            return
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute("SELECT COUNT(*) FROM users WHERE pathway IS NOT NULL AND guild_id = ?", (interaction.guild.id,)) as cursor:
                total = (await cursor.fetchone())[0]
            async with db.execute("SELECT COUNT(*) FROM users WHERE sequence = 0 AND guild_id = ?", (interaction.guild.id,)) as cursor:
                true_gods = (await cursor.fetchone())[0]
        embed = discord.Embed(title="📊 Bot Statistics", color=0xf5c400)
        embed.add_field(name="Registered Beyonders", value=str(total), inline=True)
        embed.add_field(name="Active Pathways", value="22", inline=True)
        embed.add_field(name="Sequence 0 True Gods", value=str(true_gods), inline=True)
        await interaction.response.send_message(embed=embed)
        await log_event(
            event_type="ADMIN_BOT_STATS",
            details="Viewed bot statistics",
            user_id=interaction.user.id,
            username=str(interaction.user),
            guild=interaction.guild
        )


    @app_commands.command(name="money", description="Admin: Give, take, or wipe a player's soli.")
    @app_commands.describe(action="Give, take, or wipe soli", user="Target user", amount="Amount of soli (not needed for Wipe)")
    @app_commands.choices(action=[
        app_commands.Choice(name="Give", value="give"),
        app_commands.Choice(name="Take", value="take"),
        app_commands.Choice(name="Wipe", value="wipe"),
    ])
    async def money_cmd(self, interaction: discord.Interaction, action: app_commands.Choice[str], user: discord.Member, amount: int = 0):
        if not await _require_bot_admin(interaction):
            return
        if action.value in ("give", "take") and amount <= 0:
            return await interaction.response.send_message("❌ Amount must be positive.", ephemeral=True)

        if action.value == "give":
            add_pounds(user.id, amount, guild_id=interaction.guild_id)
            await interaction.response.send_message(
                f"✅ Gave **{amount:,} soli** to **{user.display_name}**. New balance: **{get_pounds(user.id, guild_id=interaction.guild_id):,} soli**."
            )
        elif action.value == "take":
            before = get_pounds(user.id, guild_id=interaction.guild_id)
            removed = min(amount, before)
            remove_pounds(user.id, amount, guild_id=interaction.guild_id)
            after = get_pounds(user.id, guild_id=interaction.guild_id)
            await interaction.response.send_message(
                f"✅ Took **{removed:,} soli** from **{user.display_name}**. Balance: **{before:,}** → **{after:,} soli**."
            )
        else:  # wipe
            before = get_pounds(user.id, guild_id=interaction.guild_id)
            wipe_pounds(user.id, guild_id=interaction.guild_id)
            await interaction.response.send_message(
                f"🗑️ Wiped **{user.display_name}**'s balance. **{before:,} soli** → **0 soli**."
            )



    @app_commands.command(name="set_fight_channel", description="Add or remove this channel from the bot's allowed list. Works across multiple channels.")
    async def set_fight_channel(self, interaction: discord.Interaction):
        if not await _require_bot_admin(interaction):
            return
        guild_id = interaction.guild_id
        channel_id = int(interaction.channel_id)
        channels = [int(c) for c in allowed_channel.get(guild_id, [])]

        if channel_id in channels:
            channels.remove(channel_id)
            if channels:
                allowed_channel[guild_id] = channels
            else:
                allowed_channel.pop(guild_id, None)
            save_config()
            remaining = ", ".join(f"<#{c}>" for c in channels) if channels else "none (fight works everywhere)"
            await interaction.response.send_message(
                f"✅ <#{channel_id}> removed from fight channels.\n"
                f"**Active fight channels:** {remaining}",
                ephemeral=True
            )
        else:
            channels.append(channel_id)
            allowed_channel[guild_id] = channels
            save_config()
            active = ", ".join(f"<#{c}>" for c in channels)
            await interaction.response.send_message(
                f"✅ <#{channel_id}> added as a fight channel.\n"
                f"**Active fight channels:** {active}\n"
                f"`/fight` is now restricted to these channels. Run `/set_fight_channel` here again to remove it.",
                ephemeral=True
            )




    # ── Pathway ability lookup ─────────────────────────────────


    @app_commands.command(name="assign_role", description="Give or remove a role from a member.")
    @app_commands.describe(member="The member to give the role to.", role="The role to assign or remove.")
    async def role_cmd(self, interaction: discord.Interaction, member: discord.Member, role: discord.Role):
        if not await _require_bot_admin(interaction):
            return
        try:
            if role in member.roles:
                await member.remove_roles(role)
                await interaction.response.send_message(
                    f"✅ Removed **{role.name}** from **{member.display_name}**.", ephemeral=True)
            else:
                await member.add_roles(role)
                await interaction.response.send_message(
                    f"✅ Gave **{role.name}** to **{member.display_name}**.", ephemeral=True)
        except discord.Forbidden:
            await interaction.response.send_message("❌ I don't have permission to manage that role.", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"❌ Error: `{e}`", ephemeral=True)


    @app_commands.command(name="set_battle_log_channel", description="Set or clear this channel as the battle results log channel.")
    async def set_battle_log_channel(self, interaction: discord.Interaction):
        if not await _require_bot_admin(interaction):
            return
        guild_id = interaction.guild_id
        ch_id = interaction.channel_id
        if log_channel.get(guild_id) == ch_id:
            log_channel.pop(guild_id, None)
            save_config()
            await interaction.response.send_message(
                f"✅ <#{ch_id}> cleared as the battle log channel. Battle results will no longer be posted.",
                ephemeral=True
            )
        else:
            log_channel[guild_id] = ch_id
            save_config()
            await interaction.response.send_message(
                f"✅ <#{ch_id}> set as the battle log channel. All fight results will be posted here!\n"
                f"Run `/set_battle_log_channel` here again to remove it.",
                ephemeral=True
            )


    @app_commands.command(name="give_item", description="Give any item to a player. (Admin only)")
    @app_commands.describe(
        user="The recipient.",
        item="Exact item name.",
        quantity="How many to give (default 1).")
    async def give_item(self, interaction: discord.Interaction, user: discord.Member,
                        item: str, quantity: int = 1):
        if not await _require_bot_admin(interaction):
            return
        if quantity <= 0:
            return await interaction.response.send_message("Quantity must be positive.", ephemeral=True)
        gid = interaction.guild_id or 0
        await db_add_item(user.id, gid, item, quantity)
        await interaction.response.send_message(
            f"Gave **{quantity}x {item}** to **{user.display_name}**.", ephemeral=True)



async def setup(bot):
    await bot.add_cog(AdminCommands(bot))
