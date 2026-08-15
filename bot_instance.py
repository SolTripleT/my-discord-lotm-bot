# ============================================================
# BOT INSTANCE — the single discord.py Client/CommandTree used across
# the whole project. Kept in its own tiny module (no project imports of
# its own) so every other module — cogs, views, managers, main.py — can
# import `bot` without risking a circular import.
# ============================================================
import discord
from discord.ext import commands

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)
