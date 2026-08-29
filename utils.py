# ============================================================
# UTILS — small, stateless helpers shared across the whole bot:
# audit-log writing, ISO timestamp parsing, the announcement-channel
# sender, the XP progress bar, and the admin/blacklist interaction
# checks used at the top of most slash commands.
# ============================================================
import discord
import aiosqlite
from datetime import datetime

from config import OWNER_ID, DB_PATH
from database import get_setting, is_blacklisted
from persistence import bot_admin_whitelist
from logging_config import logger


# ====================== LOGGING SYSTEM ======================
async def log_event(
    event_type: str,
    details: str,
    user_id: int = None,
    username: str = None,
    guild: discord.Guild = None,
    target_id: int = None,
    target_username: str = None
):
    now = datetime.utcnow().isoformat()
    guild_id = guild.id if guild else None

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO logs (timestamp, event_type, user_id, username, guild_id, details) VALUES (?, ?, ?, ?, ?, ?)",
            (now, event_type, user_id, username, guild_id, details)
        )
        await db.commit()

    if guild:
        channel_id = await get_setting("log_channel")
        if channel_id:
            channel = guild.get_channel(int(channel_id))
            if channel:
                if "ADVANCE" in event_type or "SOVEREIGN" in event_type:
                    color = 0x00ff88
                elif "ADMIN" in event_type:
                    color = 0xffaa00
                elif "ERROR" in event_type:
                    color = 0xff0000
                else:
                    color = 0x7289da

                embed = discord.Embed(
                    title=f"📋 LOG: {event_type}",
                    description=details,
                    color=color,
                    timestamp=datetime.utcnow()
                )
                if user_id:
                    user_display = f"{username} (<@{user_id}>)" if username else f"<@{user_id}>"
                    embed.add_field(name="Done By", value=user_display, inline=True)
                if target_id:
                    target_display = f"{target_username} (<@{target_id}>)" if target_username else f"<@{target_id}>"
                    embed.add_field(name="Target", value=target_display, inline=True)
                embed.add_field(name="Server", value=guild.name, inline=True)
                embed.set_footer(text="LOTM Beyonder Bot • Logging System")
                try:
                    await channel.send(embed=embed)
                except Exception:
                    logger.debug(f"[log_event] Could not post '{event_type}' embed to log channel", exc_info=True)


def parse_iso(ts: str):
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts)
    except ValueError:
        return None

async def send_announcement(guild: discord.Guild, content: str):
    channel_id = await get_setting("announcement_channel")
    if not channel_id:
        return
    channel = guild.get_channel(int(channel_id))
    if not channel:
        return
    await channel.send(content)


def make_xp_bar(xp: int, needed: int, length: int = 12) -> str:
    """XP progress bar matching the HP/SP bar style used in fights."""
    if needed <= 0:
        return "`████████████` **100%** *(Max Sequence)*"
    pct  = min(xp / needed, 1.0)
    filled = max(0, min(length, round(pct * length)))
    bar  = "█" * filled + "░" * (length - filled)
    return f"`{bar}` **{int(pct*100)}%** ({xp:,} / {needed:,} XP)"



# ====================== ADMIN PERMISSION CHECK ======================
def is_bot_admin(user: discord.abc.User) -> bool:
    """True for the bot owner, anyone on the whitelist, or a guild Administrator.
    Used to gate every 'Admin:' command below."""
    if user.id == OWNER_ID or user.id in bot_admin_whitelist:
        return True
    return bool(getattr(user, "guild_permissions", None) and user.guild_permissions.administrator)

async def _require_bot_admin(interaction: discord.Interaction) -> bool:
    """Checks admin access and sends the standard denial message if it fails."""
    if is_bot_admin(interaction.user):
        return True
    await interaction.response.send_message("❌ You don't have permission to use this command.", ephemeral=True)
    return False


# ====================== BLACKLIST INTERACTION CHECK ======================
async def check_blacklist(interaction: discord.Interaction) -> bool:
    """Returns True if the user is blacklisted and sends them an error. Use at the top of every user command."""
    if await is_blacklisted(interaction.user.id, interaction.guild.id):
        await interaction.response.send_message(
            "🚫 You are blacklisted from using this bot.", ephemeral=True
        )
        return True
    return False

