# ============================================================
# MAIN — entry point. Loads every Cog (slash-command group) onto the
# shared bot, wires up the bot-wide event handlers, and starts the
# Discord connection using the token from haha.env.
#
# Run with:  python main.py
# ============================================================
import os
import asyncio

from bot_instance import bot
import events  # noqa: F401 — importing registers on_message/on_ready/etc. on `bot`

COGS = [
    "cogs.user_commands",
    "cogs.admin_commands",
    "cogs.owner_commands",
    "cogs.economy_commands",
    "cogs.battle_commands",
]


async def main():
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        print("❌ ERROR: DISCORD_TOKEN not set in haha.env!")
        return

    for extension in COGS:
        await bot.load_extension(extension)

    print("Starting Combined LOTM Bot...")
    await bot.start(token)


if __name__ == "__main__":
    asyncio.run(main())
