# ============================================================
# EVENTS — bot-wide (non-command) event handlers: the keep-alive HTTP
# ping server, passive chat XP on every message, global slash-command
# error handling, the /fight-channel restriction check, and startup
# (on_ready: DB init, data load, persistent views, command sync).
#
# Importing this module registers all of its handlers on the shared
# `bot` object (same effect as the decorators in the original single
# file) — main.py just needs to `import events` once before running.
# ============================================================
import os
import asyncio
import discord
from discord import app_commands
from datetime import datetime, timedelta
from aiohttp import web

from bot_instance import bot
from logging_config import logger
from database import is_blacklisted, get_user_data, update_user, init_db
from utils import parse_iso
from xp import get_base_xp_gain, apply_xp_gain
from persistence import allowed_channel, load_all, auto_save_loop
from battle.manager import safe_respond
from battle.views import PathwaySelectView

async def start_http_server():
    port = int(os.getenv("PORT", 8080))
    app = web.Application()
    app.router.add_get('/ping', lambda r: web.Response(text="OK - LOTM Beyonder Bot is alive!"))
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    logger.info(f"✅ HTTP ping server started on port {port}")


@bot.event
async def on_message(message):
    if message.author.bot or not message.guild:
        return

    if await is_blacklisted(message.author.id, message.guild.id):
        return

    data = await get_user_data(message.author.id, message.guild.id)
    if not data or not data["pathway"]:
        await bot.process_commands(message)
        return

    now = datetime.utcnow()
    last = parse_iso(data["last_message"])
    if last and (now - last) < timedelta(seconds=45):
        await bot.process_commands(message)
        return

    await update_user(message.author.id, guild_id=message.guild.id, last_message=now.isoformat())

    xp_gain = get_base_xp_gain(data["sequence"])
    await apply_xp_gain(
        user_id=message.author.id,
        guild=message.guild,
        pathway=data["pathway"],
        current_seq=data["sequence"],
        current_xp=data["xp"],
        gained_xp=xp_gain,
        mention=message.author.mention
    )

    await bot.process_commands(message)


@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    """Catch any unhandled slash command errors so they don't silently die."""
    logger.error(f"[Command error] /{interaction.command.name if interaction.command else '?'}: {error}", exc_info=error)
    await safe_respond(interaction, f"❌ Command error: `{type(error).__name__}: {error}`")

@bot.event
async def on_error(event: str, *args, **kwargs):
    """Catch any unhandled bot-level errors and log them instead of crashing."""
    logger.exception(f"[on_error] Unhandled error in event '{event}'")

@bot.tree.interaction_check
async def global_channel_check(interaction: discord.Interaction) -> bool:
    # Only restrict /fight (and related battle commands) to the set channel.
    # All other commands work everywhere.
    FIGHT_ONLY_COMMANDS = {"fight", "bet", "leave"}
    if not interaction.command or interaction.command.name not in FIGHT_ONLY_COMMANDS:
        return True
    guild_id = interaction.guild_id
    channels = allowed_channel.get(guild_id)
    if channels and interaction.channel_id not in channels:
        ch_mentions = " or ".join(f"<#{c}>" for c in channels)
        await interaction.response.send_message(
            f"❌ `/fight` is only allowed in {ch_mentions}.", ephemeral=True
        )
        return False
    return True


@bot.event
async def on_ready():
    await init_db()
    load_all()
    bot.add_view(PathwaySelectView())
    asyncio.create_task(auto_save_loop())
    logger.info(f"✅ {bot.user} — LOTM Bot loaded!")
    asyncio.create_task(start_http_server())
    try:
        synced = await bot.tree.sync()
        logger.info(f"🔄 Synced {len(synced)} slash commands")
    except Exception:
        logger.exception("Sync error")
