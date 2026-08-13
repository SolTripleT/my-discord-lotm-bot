# ============================================================
# COMBINED BOT — LOTM Beyonder Tracker + Battle System
# bv21 (progression) + batv39 (PvP fighting) merged
# Token: bv21's token (loaded from haha.env)
# ============================================================

import discord
from discord import app_commands
from discord.ext import commands
import aiosqlite
import asyncio
import random
import os
import re
import json
from dotenv import load_dotenv
import json
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "lotm_beyonders.db")
load_dotenv(os.path.join(BASE_DIR, "haha.env"))
from datetime import datetime, timedelta
from aiohttp import web

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)


# ====================== read LOTM pathway names from json ======================
with open("pathways.json", "r", encoding="utf-8") as f:
    pathways = json.load(f)
# ====================== PATHWAY SYMBOL COLORS ======================

with open("pathway_colors.json","r",encoding="utf-8") as f:
    pathway_colors = json.load(f)

# ====================== DATABASE ======================
async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER,
            guild_id INTEGER,
            pathway TEXT,
            sequence INT DEFAULT 9,
            xp INTEGER DEFAULT 0,
            last_message TIMESTAMP,
            last_daily TIMESTAMP,
            last_pray TIMESTAMP,
            PRIMARY KEY (user_id, guild_id)
        )""")
        await db.execute("""CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY, value TEXT
        )""")
        # LOGS TABLE
        await db.execute("""CREATE TABLE IF NOT EXISTS logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            event_type TEXT,
            user_id INTEGER,
            username TEXT,
            guild_id INTEGER,
            details TEXT
        )""")

        # Migration: add guild_id column for per-server XP
        try:
            await db.execute("ALTER TABLE users ADD COLUMN guild_id INTEGER NOT NULL DEFAULT 0")
        except Exception:
            pass

        # Migration: add pray cooldown column
        try:
            await db.execute("ALTER TABLE users ADD COLUMN last_pray TIMESTAMP")
        except Exception:
            pass

        # Migration: add username column to logs (added in v5.20)
        try:
            await db.execute("ALTER TABLE logs ADD COLUMN username TEXT")
        except Exception:
            pass

        # BLACKLIST TABLE
        await db.execute("""CREATE TABLE IF NOT EXISTS blacklist (
            user_id INTEGER,
            guild_id INTEGER,
            reason TEXT,
            PRIMARY KEY (user_id, guild_id)
        )""")

        # Migration: use settings table to track if guild_id=0 cleanup has already run
        try:
            async with db.execute("SELECT value FROM settings WHERE key = 'migration_guild0_cleaned'") as cursor:
                done = await cursor.fetchone()
            if not done:
                await db.execute("DELETE FROM users WHERE guild_id = 0")
                await db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('migration_guild0_cleaned', '1')")
        except Exception:
            pass

        await db.commit()

    # ── Extension tables (chairs, items, events) ──────────────────
    await _init_extension_tables()

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
                except:
                    pass

# ====================== HELPERS ======================
async def get_setting(key: str):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT value FROM settings WHERE key = ?", (key,)) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else None

async def set_setting(key: str, value: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))
        await db.commit()

async def is_blacklisted(user_id: int, guild_id: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT 1 FROM blacklist WHERE user_id = ? AND guild_id = ?", (user_id, guild_id)
        ) as cursor:
            return await cursor.fetchone() is not None

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

def get_sequence_name(pathway: str, seq_num: int) -> str:
    if pathway not in pathways or not (0 <= seq_num <= 9):
        return "Unknown"
    return pathways[pathway][9 - seq_num]

async def get_user_data(user_id: int, guild_id: int = 0):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT pathway, sequence, xp, last_message, last_daily, last_pray FROM users WHERE user_id = ? AND guild_id = ?",
            (user_id, guild_id)
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                return {
                    "pathway": row[0],
                    "sequence": row[1],
                    "xp": row[2],
                    "last_message": row[3],
                    "last_daily": row[4],
                    "last_pray": row[5],
                }
            return None

async def update_user(user_id: int, guild_id: int = 0, pathway=None, sequence=None, xp=None, last_message=None, last_daily=None, last_pray=None):
    async with aiosqlite.connect(DB_PATH) as db:
        # Fetch existing row first so we don't overwrite fields we're not updating
        async with db.execute(
            "SELECT pathway, sequence, xp, last_message, last_daily, last_pray FROM users WHERE user_id = ? AND guild_id = ?",
            (user_id, guild_id)
        ) as cursor:
            row = await cursor.fetchone()

        existing = {
            "pathway":      row[0] if row else None,
            "sequence":     row[1] if row else 9,
            "xp":           row[2] if row else 0,
            "last_message": row[3] if row else None,
            "last_daily":   row[4] if row else None,
            "last_pray":    row[5] if row else None,
        }

        final_pathway      = pathway      if pathway      is not None else existing["pathway"]
        final_sequence     = sequence     if sequence     is not None else existing["sequence"]
        final_xp           = xp           if xp           is not None else existing["xp"]
        final_last_message = last_message if last_message is not None else existing["last_message"]
        final_last_daily   = last_daily   if last_daily   is not None else existing["last_daily"]
        final_last_pray    = last_pray    if last_pray    is not None else existing["last_pray"]

        # Use INSERT OR REPLACE to handle both new rows and existing rows cleanly
        await db.execute(
            """INSERT OR REPLACE INTO users
               (user_id, guild_id, pathway, sequence, xp, last_message, last_daily, last_pray)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (user_id, guild_id, final_pathway, final_sequence, final_xp,
             final_last_message, final_last_daily, final_last_pray)
        )
        await db.commit()
        print(f"[DB] update_user: user={user_id} guild={guild_id} pathway={final_pathway} seq={final_sequence} xp={final_xp}")

# ====================== ROLE MANAGEMENT ======================
async def get_or_create_role(guild: discord.Guild, name: str, pathway: str):
    role = discord.utils.get(guild.roles, name=name)
    if role:
        print(f"[ROLE] Found existing role '{name}' in {guild.name}")
        return role
    color = pathway_colors.get(pathway, 0xf5c400)
    print(f"[ROLE] Creating new role '{name}' in {guild.name}")
    try:
        r = await guild.create_role(
            name=name, color=discord.Color(color),
            mentionable=True, reason="LOTM Beyonder Sequence role"
        )
        print(f"[ROLE] Created role '{name}' successfully")
        return r
    except discord.Forbidden:
        print(f"[ROLE] ERROR: Forbidden to create role '{name}' in {guild.name} — bot needs Manage Roles")
        return None
    except Exception as e:
        print(f"[ROLE] ERROR creating role: {e}")
        return None

def is_lotm_sequence_role_name(role_name: str) -> bool:
    return (" Seq " in role_name and role_name.startswith("[")) or "] True God" in role_name

async def remove_all_lotm_sequence_roles(member: discord.Member):
    to_remove = [r for r in member.roles if is_lotm_sequence_role_name(r.name)]
    if to_remove:
        try:
            await member.remove_roles(*to_remove)
        except discord.Forbidden:
            pass

async def assign_sequence_role(member: discord.Member, pathway: str, seq_num: int):
    seq_name = get_sequence_name(pathway, seq_num)
    if seq_num == 0:
        role_name = f"[{pathway}] True God — {seq_name}"
    else:
        role_name = f"[{pathway}] Seq {seq_num} — {seq_name}"

    print(f"[ROLE] Assigning role '{role_name}' to {member.id}")
    role = await get_or_create_role(member.guild, role_name, pathway)
    if role is None:
        print(f"[ROLE] ERROR: Could not get or create role '{role_name}' (Forbidden?)")
        return

    roles_to_remove = [
        r for r in member.roles
        if r.name.startswith(f"[{pathway}] Seq ") or r.name.startswith(f"[{pathway}]")
    ]
    try:
        if roles_to_remove:
            await member.remove_roles(*roles_to_remove)
        await member.add_roles(role)
        print(f"[ROLE] Successfully assigned '{role_name}' to {member.id}")
    except discord.Forbidden:
        print(f"[ROLE] ERROR: Forbidden — bot lacks permission to assign roles to {member.id}")
    except Exception as e:
        print(f"[ROLE] ERROR: {e}")

# ====================== XP CURVES ======================
def get_base_xp_gain(current_seq: int) -> int:
    if current_seq <= 0:
        return 0
    base = random.randint(12, 28)
    multiplier = max(0.35, (current_seq / 9.0) ** 0.78)
    return max(5, int(base * multiplier))

def get_xp_required(current_seq: int) -> int:
    # ~50-90 PvP wins per tier. Chat helps but can't carry you alone.
    req = {
        9: 8000,     # Seq 9 -> 8  (~53 wins)
        8: 15000,    # Seq 8 -> 7  (~60 wins)
        7: 25000,    # Seq 7 -> 6  (~63 wins)
        6: 40000,    # Seq 6 -> 5  (~67 wins)
        5: 70000,    # Seq 5 -> 4  (~78 wins)
        4: 120000,   # Seq 4 -> 3  (~86 wins)
        0: 999999999 # True God -- no further advancement
    }
    return req.get(current_seq, 9999999)

# ====================== SEQUENCE ADVANCE ANNOUNCEMENTS ======================
SEQ_ADVANCE_FLAVOUR = {
    8: "The first step on the path of a Beyonder. The ritual is complete.",
    7: "Deeper into the mysteries. The Formula takes hold.",
    6: "Extraordinary. The supernatural flows through their veins.",
    5: "A mid-sequence powerhouse. Few dare walk this far.",
    4: "The threshold of true power. Legends are born here.",
    3: "Beyond mortal comprehension. A figure of the extraordinary.",
    2: "Near the pinnacle. Even gods take notice.",
    1: "One step from True God. The world trembles.",
    0: "A True God is born. The madness is here.",
}

async def announce_sequence_advance(guild: discord.Guild, user_id: int, pathway: str, new_seq: int):
    mention = f"<@{user_id}>"
    seq_name = get_sequence_name(pathway, new_seq)
    color = pathway_colors.get(pathway, 0xf5c400)
    flavour = SEQ_ADVANCE_FLAVOUR.get(new_seq, "Another step forward on the extraordinary path.")

    channel_id = await get_setting("announcement_channel")
    if channel_id:
        channel = guild.get_channel(int(channel_id))
        if channel:
            title = "⚡ TRUE GOD ASCENSION!" if new_seq == 0 else "🌟 SEQUENCE ADVANCE!"
            embed = discord.Embed(title=title, color=color)
            embed.description = (
                f"{mention} has advanced to **Sequence {new_seq} — {seq_name}**\n"
                f"in the **{pathway} Pathway**!\n\n"
                f"*{flavour}*"
            )
            embed.set_footer(text="Lord of the Mysteries — Beyonder Tracker")
            # FIXED: mention is now in content so the user actually gets pinged
            await channel.send(content=mention, embed=embed)

    await log_event(
        event_type="SEQUENCE_ADVANCE",
        details=f"Advanced to **Sequence {new_seq} — {seq_name}** in **{pathway}** Pathway",
        user_id=user_id,
        username=str(guild.get_member(user_id)) if guild.get_member(user_id) else None,
        guild=guild
    )

async def apply_xp_gain(user_id: int, guild: discord.Guild, pathway: str, current_seq: int, current_xp: int, gained_xp: int, mention: str):
    if current_seq <= 0:
        return 0, current_xp, False

    # Always re-fetch fresh data from DB to avoid stale XP overwrites
    fresh = await get_user_data(user_id, guild.id)
    if fresh and fresh["pathway"]:
        current_xp = fresh["xp"]
        current_seq = fresh["sequence"]

    if current_seq <= 0:
        return 0, current_xp, False

    seq = current_seq
    xp = current_xp + gained_xp
    leveled = False

    while seq > 0:
        req = get_xp_required(seq)
        if xp >= req:
            xp -= req
            seq -= 1
            leveled = True
        else:
            break

    # Only update sequence if it changed, always update XP
    if leveled:
        await update_user(user_id, guild_id=guild.id, sequence=seq, xp=xp)
        try:
            member = guild.get_member(user_id) or await guild.fetch_member(user_id)
        except (discord.NotFound, discord.HTTPException):
            member = None
        if member:
            await assign_sequence_role(member, pathway, seq)
        await announce_sequence_advance(guild, user_id, pathway, seq)
    else:
        await update_user(user_id, guild_id=guild.id, xp=xp)

    return seq, xp, leveled

# ====================== DEMOTION HELPER ======================
async def handle_xp_removal(guild: discord.Guild, user_id: int, data: dict, amount: int):
    new_xp = data["xp"] - amount
    seq = data["sequence"]
    demoted = False

    while new_xp < 0 and seq < 9:
        seq += 1
        new_xp = get_xp_required(seq) + new_xp
        demoted = True

    if seq >= 9:
        seq = 9
        new_xp = max(0, new_xp)

    return seq, new_xp, demoted

# ====================== BOT EVENTS ======================
async def start_http_server():
    port = int(os.getenv("PORT", 8080))
    app = web.Application()
    app.router.add_get('/ping', lambda r: web.Response(text="OK - LOTM Beyonder Bot is alive!"))
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    print(f"✅ HTTP ping server started on port {port}")


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

# ====================== USER COMMANDS ======================
def make_xp_bar(xp: int, needed: int, length: int = 12) -> str:
    """XP progress bar matching the HP/SP bar style used in fights."""
    if needed <= 0:
        return "`████████████` **100%** *(Max Sequence)*"
    pct  = min(xp / needed, 1.0)
    filled = max(0, min(length, round(pct * length)))
    bar  = "█" * filled + "░" * (length - filled)
    return f"`{bar}` **{int(pct*100)}%** ({xp:,} / {needed:,} XP)"


@bot.tree.command(name="profile", description="View your (or another Beyonder's) profile.")
@app_commands.describe(user="The user to check. Leave blank for your own profile.")
async def profile(interaction: discord.Interaction, user: discord.Member = None):
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


@bot.tree.command(name="sequence_info", description="Show info for a specific sequence in your pathway")
@app_commands.describe(sequence_number="Sequence number (0–9)")
async def sequence_info(interaction: discord.Interaction, sequence_number: int):
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


@bot.tree.command(name="compare", description="Compare your Beyonder progress with another user")
@app_commands.describe(user="User to compare with")
async def compare(interaction: discord.Interaction, user: discord.Member):
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


@bot.tree.command(name="pathways", description="List all 22 pathways")
async def pathways_cmd(interaction: discord.Interaction):
    if await check_blacklist(interaction): return
    embed = discord.Embed(title="🃏 THE 22 pathways", color=0xf5c400)
    embed.description = "Use `/choose_pathway` to select yours.\n\n"
    for i, p in enumerate(pathways.keys(), 1):
        embed.description += f"`{i:02d}` **{p}**\n"
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="choose_pathway", description="Choose your Beyonder Pathway (once for normal users)")
@app_commands.describe(pathway="Your chosen path")
@app_commands.choices(pathway=[app_commands.Choice(name=p, value=p) for p in sorted(pathways.keys())])
async def choose_pathway(interaction: discord.Interaction, pathway: str):
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

# ====================== PATHWAY SELECT MENU ======================
# Server custom emoji names — resolved at runtime from the guild
PATHWAY_EMOJI_NAMES = {
    "Fool":           "SeerN",
    "Error":          "MarauderN",
    "Door":           "ApprenticeN",
    "Visionary":      "SpectatorN",
    "Sun":            "BardN",
    "Tyrant":         "SailorN",
    "Hanged Man":     "SecreSuppN",
    "White Tower":    "ReaderN",
    "Darkness":       "SleeplessN",
    "Death":          "CorColleN",
    "Twilight Giant": "WarriorN",
    "Demoness":       "AssassinN",
    "Red Priest":     "HunterN",
    "Hermit":         "MysPryerN",
    "Paragon":        "SavantN",
    "Wheel of Fortune": "MonsterN",
    "Moon":           "ApothecaryN",
    "Mother":         "PlanterN",
    "Chained":        "PrisonerN",
    "Abyss":          "CriminalN",
    "Black Emperor":  "LawyerN",
    "Justiciar":      "ArbiterN",
}

def get_pathway_emoji(guild: discord.Guild, pathway: str):
    """Return the custom guild emoji object for a pathway, or None."""
    emoji_name = PATHWAY_EMOJI_NAMES.get(pathway)
    if not emoji_name or not guild:
        return None
    return discord.utils.get(guild.emojis, name=emoji_name)

PATHWAY_DESCRIPTIONS = {
    "Fool": "Mystery, deception, and fate", "Error": "Time, cryptology, and paradox",
    "Door": "Travel, secrets, and the stars", "Visionary": "Dreams, telepathy, and the mind",
    "Sun": "Light, justice, and divine radiance", "Tyrant": "Sea, storms, and raw fury",
    "Hanged Man": "Shadows, sacrifice, and dark angels", "White Tower": "Logic, wisdom, and omniscience",
    "Darkness": "Night, horror, and misfortune", "Death": "Souls, undeath, and the underworld",
    "Twilight Giant": "Battle, glory, and divine strength", "Demoness": "Desire, affliction, and ruin",
    "Red Priest": "Blood, war, and conquest", "Hermit": "Arcane lore and hidden knowledge",
    "Paragon": "Alchemy, invention, and illumination", "Wheel of Fortune": "Luck, chaos, and fate's whims",
    "Moon": "Beasts, potions, and life's beauty", "Mother": "Nature, growth, and ancient earth",
    "Chained": "Madness, curses, and ancient banes", "Abyss": "Sin, demons, and corruption",
    "Black Emperor": "Disorder, corruption, and entropy", "Justiciar": "Order, law, and balance",
}

PATHWAY_LIST = list(pathways.keys())  # 22 pathways — fits in one Discord select (max 25)


class PathwaySelect(discord.ui.Select):
    def __init__(self, guild=None):
        # guild=None when re-registering persistent view on startup
        options = [
            discord.SelectOption(
                label=p,
                value=p,
                description=PATHWAY_DESCRIPTIONS.get(p, ""),
                emoji=get_pathway_emoji(guild, p) if guild else None
            ) for p in PATHWAY_LIST
        ]
        super().__init__(
            placeholder="Choose your Beyonder Pathway…",
            options=options,
            custom_id="pathway_select"
        )

    async def callback(self, interaction: discord.Interaction):
        await handle_pathway_selection(interaction, self.values[0])


class PathwaySelectView(discord.ui.View):
    def __init__(self, guild=None):
        super().__init__(timeout=None)
        self.add_item(PathwaySelect(guild))


async def handle_pathway_selection(interaction: discord.Interaction, pathway: str):
    """Shared handler called when a member picks a pathway from the select menu."""
    guild_id = interaction.guild_id
    guild = interaction.guild or bot.get_guild(guild_id)
    print(f"[DEBUG] pathway selection: user={interaction.user.id} guild_id={guild_id} pathway={pathway}")

    if await is_blacklisted(interaction.user.id, guild_id):
        await interaction.response.send_message("🚫 You are blacklisted from using this bot.", ephemeral=True)
        return

    old_data = await get_user_data(interaction.user.id, guild_id)
    old_pathway = old_data["pathway"] if old_data else None

    # Always reset progress and switch pathway
    await update_user(interaction.user.id, guild_id=interaction.guild_id, pathway=pathway, sequence=9, xp=0,
                      last_daily=None, last_pray=None)

    # Verify the save actually worked
    verify = await get_user_data(interaction.user.id, guild_id)
    print(f"[DEBUG] verify after save: {verify}")

    member = None
    if guild:
        try:
            member = guild.get_member(interaction.user.id) or await guild.fetch_member(interaction.user.id)
            print(f"[ROLE] member fetched: {member}")
        except Exception as e:
            print(f"[ROLE] Could not fetch member {interaction.user.id}: {e}")
    else:
        print(f"[ROLE] guild is None for guild_id={guild_id}")

    if member:
        await remove_all_lotm_sequence_roles(member)
        await assign_sequence_role(member, pathway, 9)
    else:
        print(f"[ROLE] Skipping role assignment — member is None")

    name = get_sequence_name(pathway, 9)

    await log_event(
        event_type="PATHWAY_CHANGE" if old_pathway else "PATHWAY_CHOICE",
        details=f"Switched from **{old_pathway}** to **{pathway}** Pathway (progress reset)" if old_pathway else f"Chose the **{pathway}** Pathway via select menu",
        user_id=interaction.user.id,
        username=str(interaction.user),
        guild=guild
    )

    color = pathway_colors.get(pathway, 0xf5c400)
    emoji = get_pathway_emoji(guild, pathway) if guild else None
    emoji_str = str(emoji) if emoji else ""

    if old_pathway and old_pathway != pathway:
        desc = (
            f"{interaction.user.mention} has left the **{old_pathway} Pathway** and entered the **{pathway} Pathway**!\n\n"
            f"You begin fresh as **Sequence 9 — {name}**.\n"
            f"*{PATHWAY_DESCRIPTIONS.get(pathway, '')}*\n\n"
            f"Use `/profile` to view your status."
        )
    else:
        desc = (
            f"{interaction.user.mention} has entered the **{pathway} Pathway**!\n\n"
            f"You begin as **Sequence 9 — {name}**.\n"
            f"*{PATHWAY_DESCRIPTIONS.get(pathway, '')}*\n\n"
            f"Use `/profile` to view your status, `/daily` to claim XP, and `/pray` for blessings."
        )

    embed = discord.Embed(title=f"{emoji_str} PATHWAY CHOSEN!", description=desc, color=color)
    embed.set_footer(text="Lord of the Mysteries — Beyonder Tracker")
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="pathway_menu", description="Admin only: Post the pathway select menu in this channel")
async def pathway_menu(interaction: discord.Interaction):
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


@bot.tree.command(name="leaderboard", description="Top Beyonders by Sequence")
async def leaderboard(interaction: discord.Interaction):
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

@bot.tree.command(name="daily", description="Claim your daily XP bonus and soli reward")
async def daily(interaction: discord.Interaction):
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

@bot.tree.command(name="pray", description="Pray for a small XP blessing")
async def pray(interaction: discord.Interaction):
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

# ====================== ADMIN COMMANDS ======================
OWNER_ID = 787147722444505110

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

@bot.tree.command(name="admin_whitelist", description="Owner only: Grant or revoke access to Admin: commands for a user")
@app_commands.describe(action="Add or remove from the whitelist", user="The target user")
@app_commands.choices(action=[
    app_commands.Choice(name="Add", value="add"),
    app_commands.Choice(name="Remove", value="remove"),
    app_commands.Choice(name="List", value="list"),
])
async def admin_whitelist_cmd(interaction: discord.Interaction, action: app_commands.Choice[str], user: discord.Member = None):
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

@bot.tree.command(name="force_sync", description="Force sync all slash commands (Admin only)")
async def force_sync(interaction: discord.Interaction):
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

@bot.tree.command(name="migrate_guild", description="Admin: Migrate existing user data to this server (run once after update)")
async def migrate_guild(interaction: discord.Interaction):
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

@bot.tree.command(name="setup_roles", description="Admin: Create all Sequence roles")
async def setup_roles(interaction: discord.Interaction):
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

@bot.tree.command(name="delete_all_roles", description="Admin: Delete all LOTM sequence roles from the server")
async def delete_all_roles(interaction: discord.Interaction):
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

@bot.tree.command(name="set_announcement_channel", description="Admin: Set channel for announcements")
async def set_announcement_channel(interaction: discord.Interaction, channel: discord.TextChannel):
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

@bot.tree.command(name="set_admin_log_channel", description="Admin: Set the channel where the bot sends ALL admin/audit logs")
async def set_admin_log_channel(interaction: discord.Interaction, channel: discord.TextChannel):
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
        pass

@bot.tree.command(name="xp", description="Admin: Give, take, or wipe a user's XP")
@app_commands.describe(action="Give, take, or wipe XP", user="Target user", amount="Amount of XP (not needed for Wipe)")
@app_commands.choices(action=[
    app_commands.Choice(name="Give", value="give"),
    app_commands.Choice(name="Take", value="take"),
    app_commands.Choice(name="Wipe", value="wipe"),
])
async def xp_cmd(interaction: discord.Interaction, action: app_commands.Choice[str], user: discord.Member, amount: int = 0):
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

@bot.tree.command(name="reset_user", description="Admin: Reset a user to Sequence 9")
@app_commands.describe(user="Target user")
async def reset_user(interaction: discord.Interaction, user: discord.Member):
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

@bot.tree.command(name="reset_all", description="Admin: Reset ALL users' XP and sequence to 9 in this server")
async def reset_all(interaction: discord.Interaction):
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

@bot.tree.command(name="force_choose_pathway", description="Admin: Force a user to a new pathway")
@app_commands.describe(user="Target user", pathway="New pathway")
@app_commands.choices(pathway=[app_commands.Choice(name=p, value=p) for p in sorted(pathways.keys())])
async def force_choose_pathway(interaction: discord.Interaction, user: discord.Member, pathway: str):
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

@bot.tree.command(name="bot_stats", description="Admin: Show server statistics")
async def bot_stats(interaction: discord.Interaction):
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

# ====================== BLACKLIST INTERACTION CHECK ======================
async def check_blacklist(interaction: discord.Interaction) -> bool:
    """Returns True if the user is blacklisted and sends them an error. Use at the top of every user command."""
    if await is_blacklisted(interaction.user.id, interaction.guild.id):
        await interaction.response.send_message(
            "🚫 You are blacklisted from using this bot.", ephemeral=True
        )
        return True
    return False

# ====================== OWNER-ONLY COMMANDS ======================
@bot.tree.command(name="wipe_user", description="Owner only: Completely wipe a user's data (XP, pathway, sequence)")
@app_commands.describe(user="The user to wipe")
async def wipe_user(interaction: discord.Interaction, user: discord.Member):
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

@bot.tree.command(name="blacklist", description="Owner only: Blacklist or unblacklist a user from using the bot")
@app_commands.describe(action="Add or remove from the blacklist", user="The target user", reason="Reason (only used when adding)")
@app_commands.choices(action=[
    app_commands.Choice(name="Add", value="add"),
    app_commands.Choice(name="Remove", value="remove"),
])
async def blacklist_user(interaction: discord.Interaction, action: app_commands.Choice[str], user: discord.Member, reason: str = "No reason provided"):
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


@bot.tree.command(name="kick", description="Owner only: Kick a user from the server")
@app_commands.describe(user="The user to kick", reason="Reason for kick")
async def kick_user(interaction: discord.Interaction, user: discord.Member, reason: str = "No reason provided"):
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


@bot.tree.command(name="ban", description="Owner only: Ban a user from the server")
@app_commands.describe(user="The user to ban", reason="Reason for ban")
async def ban_user(interaction: discord.Interaction, user: discord.Member, reason: str = "No reason provided"):
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


battles = {}
player_stats = {}
player_economy = {}
active_bets = {}
blackjack_games = {}
gambling_cooldowns = {}  # user_id -> last gambling timestamp
daily_data = {}          # user_id -> {"last": timestamp, "streak": int}
beg_cooldowns = {}       # user_id -> last beg timestamp
allowed_channel = {}     # guild_id -> list of channel_ids (empty = unrestricted)
log_channel = {}         # guild_id -> channel_id for battle result broadcasts
bot_admin_whitelist = set()  # user_id -> can use Admin: commands even without Discord Administrator perm

GAMBLE_COOLDOWN = 5  # seconds

def check_gamble_cooldown(user_id: int) -> float:
    """Returns seconds remaining on cooldown, or 0 if ready."""
    import time
    last = gambling_cooldowns.get(user_id, 0)
    elapsed = time.time() - last
    return max(0.0, GAMBLE_COOLDOWN - elapsed)

def set_gamble_cooldown(user_id: int):
    import time
    gambling_cooldowns[user_id] = time.time()

# ── Database ──────────────────────────────────────────────
DB_ECONOMY = os.path.join(BASE_DIR, "economy.json")
DB_STATS   = os.path.join(BASE_DIR, "stats.json")
DB_DAILY   = os.path.join(BASE_DIR, "daily.json")
DB_BEG     = os.path.join(BASE_DIR, "beg.json")
DB_CONFIG  = os.path.join(BASE_DIR, "config.json")

def _load(path: str) -> dict:
    if os.path.exists(path):
        try:
            with open(path, "r") as f:
                return json.load(f)
        except Exception as e:
            print(f"[DB] ⚠️ Failed to read {path} — starting with empty data for this file! Error: {e}")
            return {}
    else:
        print(f"[DB] {path} does not exist yet — starting fresh for this file.")
    return {}

def _save(path: str, data: dict):
    try:
        with open(path, "w") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        print(f"[DB] Save error ({path}): {e}")

def load_all():
    global player_economy, player_stats, daily_data, beg_cooldowns, allowed_channel, log_channel, bot_admin_whitelist
    raw_eco = _load(DB_ECONOMY)
    player_economy.clear()
    for k, v in raw_eco.items():
        k = str(k)
        # Normalise key format — always "guild_id:user_id"
        if ":" not in k:
            # Old plain user_id key — store under guild 0 for backward compat
            player_economy[f"0:{k}"] = v
        else:
            player_economy[k] = v
    raw_stats = _load(DB_STATS)
    player_stats = {int(k): v for k, v in raw_stats.items()}
    raw_daily = _load(DB_DAILY)
    daily_data.clear()
    daily_data.update({int(k): v for k, v in raw_daily.items()})
    raw_beg = _load(DB_BEG)
    beg_cooldowns.clear()
    beg_cooldowns.update({int(k): v for k, v in raw_beg.items()})
    raw_config = _load(DB_CONFIG)
    raw_ac = raw_config.get("allowed_channel", {})
    allowed_channel = {}
    for k, v in raw_ac.items():
        raw_list = v if isinstance(v, list) else [v]
        allowed_channel[int(k)] = [int(c) for c in raw_list]
    raw_lc = raw_config.get("log_channel", {})
    log_channel = {int(k): int(v) for k, v in raw_lc.items()}
    bot_admin_whitelist.clear()
    bot_admin_whitelist.update(int(u) for u in raw_config.get("bot_admin_whitelist", []))
    print(f"[DB] Data directory: {BASE_DIR}")
    print(f"[DB] Loaded {len(player_economy)} economy entries, {len(daily_data)} daily entries.")


async def _auto_save_loop():
    """Periodically flush all JSON data to disk every 5 minutes as a safety net."""
    await asyncio.sleep(60)  # wait 1 min after boot before first save
    while True:
        try:
            save_economy()
            save_daily()
            save_beg()
            save_config()
        except Exception as e:
            print(f"[auto-save] Error: {e}")
        await asyncio.sleep(300)  # every 5 minutes

def save_economy():
    _save(DB_ECONOMY, {str(k): v for k, v in player_economy.items()})

def save_stats():
    _save(DB_STATS, {str(k): v for k, v in player_stats.items()})

def save_daily():
    _save(DB_DAILY, {str(k): v for k, v in daily_data.items()})

def save_beg():
    _save(DB_BEG, {str(k): v for k, v in beg_cooldowns.items()})

def save_config():
    _save(DB_CONFIG, {
        "allowed_channel": {str(k): v for k, v in allowed_channel.items()},
        "log_channel": {str(k): v for k, v in log_channel.items()},
        "bot_admin_whitelist": list(bot_admin_whitelist),
    })

# ── Economy ───────────────────────────────────────────────

def _eco_key(guild_id, user_id):
    """Composite key for per-server economy storage."""
    return f"{guild_id}:{user_id}"

def get_economy(user_id, guild_id=0):
    key = _eco_key(guild_id, user_id)
    if key not in player_economy:
        # Check if data exists under guild 0 (migrated from old format)
        fallback_key = f"0:{user_id}"
        if guild_id != 0 and fallback_key in player_economy:
            # Migrate it to the real guild key and remove old one
            player_economy[key] = player_economy.pop(fallback_key)
            save_economy()
        else:
            player_economy[key] = {"pounds": 1000, "spells": []}
            save_economy()
    return player_economy[key]

def get_pounds(user_id, guild_id=0):
    return get_economy(user_id, guild_id)["pounds"]

def add_pounds(user_id, amount, guild_id=0):
    get_economy(user_id, guild_id)["pounds"] += amount
    save_economy()

def remove_pounds(user_id, amount, guild_id=0):
    get_economy(user_id, guild_id)["pounds"] = max(0, get_economy(user_id, guild_id)["pounds"] - amount)
    save_economy()

def set_pounds(user_id, amount, guild_id=0):
    get_economy(user_id, guild_id)["pounds"] = max(0, amount)
    save_economy()

def wipe_pounds(user_id, guild_id=0):
    get_economy(user_id, guild_id)["pounds"] = 0
    save_economy()

def owns_spell(user_id, spell_key, guild_id=0):
    return spell_key in get_economy(user_id, guild_id)["spells"]

def buy_spell(user_id, spell_key, guild_id=0):
    eco = get_economy(user_id, guild_id)
    if spell_key not in eco["spells"]:
        eco["spells"].append(spell_key)
        save_economy()

# ── Sequence base stats ───────────────────────────────────

SEQ_BASE_STATS = {
    9: {"hp": 240,  "sp": 110},
    8: {"hp": 275,  "sp": 125},
    7: {"hp": 315,  "sp": 150},
    6: {"hp": 360,  "sp": 175},
    5: {"hp": 420,  "sp": 205},
    4: {"hp": 540,  "sp": 265},
    3: {"hp": 705,  "sp": 350},
    2: {"hp": 915,  "sp": 475},
    1: {"hp": 1190, "sp": 595},
    0: {"hp": 1400, "sp": 740},
}

# ── Sequence average HP (across all pathways) — used for scaling damage ──
# Damage = pct * SEQ_AVG_HP[attacker's seq], so power grows naturally as you advance
SEQ_AVG_HP = {
    9: 270,
    8: 310,
    7: 355,
    6: 410,
    5: 420,
    4: 540,
    3: 705,
    2: 915,
    1: 1190,
    0: 1400,
}

def apply_healing(fighter: "Fighter", amount: int) -> int:
    """Apply healing respecting no_heal_permanent flag."""
    if getattr(fighter, "_no_heal_permanent", False):
        amount = 0
    elif getattr(fighter, "_catastrophe_dot_turns", 0) > 0 or getattr(fighter, "_inquisition_no_heal", 0) > 0:
        amount = amount // 2  # healing halved during catastrophe/inquisition
    old_hp = fighter.hp
    fighter.hp = min(fighter.max_hp, fighter.hp + amount)
    return fighter.hp - old_hp


def seq_gap_check(attacker: "Fighter", defender: "Fighter", max_gap: int = 2) -> tuple[bool, int]:
    """
    LOTM rule: weaker Beyonders (higher seq number) cannot easily impose their will
    on stronger ones (lower seq number). Returns (allowed, gap).
    gap = atk_seq - def_seq  (positive = attacker is weaker)
    """
    atk_seq = get_seq_number(attacker.role) if attacker.role else 9
    def_seq = get_seq_number(defender.role) if defender.role else 9
    gap = atk_seq - def_seq
    return gap <= max_gap, gap


def seq_dmg(attacker: "Fighter", pct: float) -> int:
    """Return a damage value scaled to the attacker's sequence average HP.
    pct is a fraction (e.g. 0.30 = 30%).  Minimum 1."""
    seq = get_seq_number(attacker.role) if attacker.role else 9
    base = SEQ_AVG_HP.get(seq, 220)
    return max(1, int(base * pct))

def seq_val(fighter: "Fighter", base_at_seq9: float) -> int:
    """Scale a flat value (balanced for Seq 9) up to the fighter's actual sequence.
    e.g. seq_val(f, 15) → 15 at seq9, 22 at seq7, 33 at seq5, 53 at seq3."""
    seq = get_seq_number(fighter.role) if fighter.role else 9
    avg_hp = SEQ_AVG_HP.get(seq, 270)
    seq9_hp = SEQ_AVG_HP[9]  # 270
    return max(1, int(base_at_seq9 * (avg_hp / seq9_hp)))

def seq_heal(fighter: "Fighter", base_at_seq9: float) -> int:
    """Scale a flat heal value to the fighter's sequence."""
    return seq_val(fighter, base_at_seq9)

def get_seq_number(role_name: str) -> int:
    """Extract sequence number from role name like '[Fool] Seq 9 — Seer'"""
    try:
        parts = role_name.split("Seq ")
        if len(parts) > 1:
            return int(parts[1].split(" ")[0].split("—")[0].strip())
    except Exception:
        pass
    return 9

def get_pathway_name(role_name: str) -> str:
    """Extract pathway name from role name like '[Fool] Seq 9 — Seer' -> 'Fool'"""
    try:
        return role_name.split("[")[1].split("]")[0]
    except Exception:
        return ""

# Per-pathway base stats keyed as (pathway_name, seq_number)
# Falls back to SEQ_BASE_STATS if not listed
PATHWAY_BASE_STATS = {
    # ── High HP / Low SP (Physical/Combat) ──
    ("Twilight Giant", 9): {"hp":  310, "sp":   90},
    ("Twilight Giant", 8): {"hp":  360, "sp":  105},
    ("Twilight Giant", 7): {"hp":  415, "sp":  125},
    ("Twilight Giant", 6): {"hp":  490, "sp":  150},
    ("Chained",        9): {"hp":  300, "sp":   90},
    ("Chained",        8): {"hp":  345, "sp":  105},
    ("Chained",        7): {"hp":  390, "sp":  125},
    ("Chained",        6): {"hp":  465, "sp":  155},
    ("Abyss",          9): {"hp":  295, "sp":   95},
    ("Abyss",          8): {"hp":  340, "sp":  110},
    ("Abyss",          7): {"hp":  380, "sp":  130},
    ("Abyss",          6): {"hp":  445, "sp":  160},
    ("Red Priest",     9): {"hp":  280, "sp":   95},
    ("Red Priest",     8): {"hp":  325, "sp":  110},
    ("Red Priest",     7): {"hp":  365, "sp":  130},
    ("Red Priest",     6): {"hp":  430, "sp":  165},
    ("Tyrant",         9): {"hp":  275, "sp":  100},
    ("Tyrant",         8): {"hp":  320, "sp":  115},
    ("Tyrant",         7): {"hp":  360, "sp":  135},
    ("Tyrant",         6): {"hp":  425, "sp":  170},
    ("Justiciar",      9): {"hp":  275, "sp":  100},
    ("Justiciar",      8): {"hp":  315, "sp":  115},
    ("Justiciar",      7): {"hp":  355, "sp":  135},
    ("Justiciar",      6): {"hp":  420, "sp":  170},
    ("Black Emperor",  9): {"hp":  270, "sp":  100},
    ("Black Emperor",  8): {"hp":  310, "sp":  115},
    ("Black Emperor",  7): {"hp":  355, "sp":  135},
    ("Black Emperor",  6): {"hp":  415, "sp":  175},
    # ── Balanced ──
    ("Demoness",       9): {"hp":  250, "sp":  115},
    ("Demoness",       8): {"hp":  290, "sp":  135},
    ("Demoness",       7): {"hp":  325, "sp":  165},
    ("Demoness",       6): {"hp":  385, "sp":  205},
    ("Death",          9): {"hp":  260, "sp":  115},
    ("Death",          8): {"hp":  295, "sp":  135},
    ("Death",          7): {"hp":  335, "sp":  165},
    ("Death",          6): {"hp":  400, "sp":  205},
    ("Darkness",       9): {"hp":  255, "sp":  115},
    ("Darkness",       8): {"hp":  295, "sp":  135},
    ("Darkness",       7): {"hp":  335, "sp":  165},
    ("Darkness",       6): {"hp":  395, "sp":  205},
    ("Sun",            9): {"hp":  265, "sp":  115},
    ("Sun",            8): {"hp":  305, "sp":  135},
    ("Sun",            7): {"hp":  345, "sp":  160},
    ("Sun",            6): {"hp":  405, "sp":  200},
    ("Moon",           9): {"hp":  260, "sp":  115},
    ("Moon",           8): {"hp":  300, "sp":  135},
    ("Moon",           7): {"hp":  340, "sp":  165},
    ("Moon",           6): {"hp":  400, "sp":  205},
    ("Mother",         9): {"hp":  265, "sp":  115},
    ("Mother",         8): {"hp":  305, "sp":  135},
    ("Mother",         7): {"hp":  345, "sp":  165},
    ("Mother",         6): {"hp":  410, "sp":  205},
    ("Door",           9): {"hp":  255, "sp":  115},
    ("Door",           8): {"hp":  295, "sp":  135},
    ("Door",           7): {"hp":  335, "sp":  165},
    ("Door",           6): {"hp":  390, "sp":  205},
    ("Error",          9): {"hp":  250, "sp":  115},
    ("Error",          8): {"hp":  290, "sp":  135},
    ("Error",          7): {"hp":  330, "sp":  165},
    ("Error",          6): {"hp":  390, "sp":  205},
    # ── Low HP / High SP (Spiritual/Knowledge) ──
    ("Hanged Man",     9): {"hp":  250, "sp":  140},
    ("Hanged Man",     8): {"hp":  285, "sp":  165},
    ("Hanged Man",     7): {"hp":  320, "sp":  195},
    ("Hanged Man",     6): {"hp":  375, "sp":  240},
    ("Wheel of Fortune",9): {"hp":  245, "sp":  145},
    ("Wheel of Fortune",8): {"hp":  285, "sp":  170},
    ("Wheel of Fortune",7): {"hp":  320, "sp":  205},
    ("Wheel of Fortune",6): {"hp":  370, "sp":  250},
    ("Hermit",         9): {"hp":  260, "sp":  145},
    ("Hermit",         8): {"hp":  295, "sp":  170},
    ("Hermit",         7): {"hp":  335, "sp":  205},
    ("Hermit",         6): {"hp":  390, "sp":  255},
    ("White Tower",    9): {"hp":  245, "sp":  140},
    ("White Tower",    8): {"hp":  285, "sp":  165},
    ("White Tower",    7): {"hp":  320, "sp":  195},
    ("White Tower",    6): {"hp":  370, "sp":  240},
    ("Visionary",      9): {"hp":  250, "sp":  135},
    ("Visionary",      8): {"hp":  285, "sp":  160},
    ("Visionary",      7): {"hp":  320, "sp":  195},
    ("Visionary",      6): {"hp":  370, "sp":  235},
    ("Paragon",        9): {"hp":  250, "sp":  130},
    ("Paragon",        8): {"hp":  290, "sp":  150},
    ("Paragon",        7): {"hp":  330, "sp":  185},
    ("Paragon",        6): {"hp":  380, "sp":  220},
    ("Fool",           9): {"hp":  250, "sp":  135},
    ("Fool",           8): {"hp":  290, "sp":  160},
    ("Fool",           7): {"hp":  325, "sp":  190},
    ("Fool",           6): {"hp":  375, "sp":  230},
    # Seq 5
    ("Twilight Giant", 5): {"hp":  560, "sp":  200},
    ("Chained",        5): {"hp":  540, "sp":  200},
    ("Abyss",          5): {"hp":  510, "sp":  210},
    ("Red Priest",     5): {"hp":  490, "sp":  205},
    ("Tyrant",         5): {"hp":  480, "sp":  210},
    ("Justiciar",      5): {"hp":  475, "sp":  210},
    ("Black Emperor",  5): {"hp":  460, "sp":  215},
    ("Demoness",       5): {"hp":  430, "sp":  240},
    ("Death",          5): {"hp":  440, "sp":  240},
    ("Darkness",       5): {"hp":  435, "sp":  240},
    ("Sun",            5): {"hp":  445, "sp":  240},
    ("Moon",           5): {"hp":  440, "sp":  240},
    ("Mother",         5): {"hp":  450, "sp":  235},
    ("Door",           5): {"hp":  420, "sp":  240},
    ("Error",          5): {"hp":  415, "sp":  240},
    ("Hanged Man",     5): {"hp":  405, "sp":  270},
    ("Wheel of Fortune",5):{"hp":  400, "sp":  275},
    ("Hermit",         5): {"hp":  415, "sp":  275},
    ("White Tower",    5): {"hp":  400, "sp":  275},
    ("Visionary",      5): {"hp":  405, "sp":  265},
    ("Paragon",        5): {"hp":  410, "sp":  255},
    ("Fool",           5): {"hp":  405, "sp":  260},
}

# ── Role definitions ──────────────────────────────────────

ROLE_MODIFIERS = {
    # Seq 9
    "[Hermit] Seq 9 — Mystery Pryer":            {"health": 0,  "attack": 0,  "luck": 0,  "spirituality": +2, "speed": 0,  "spirit": +2},
    "[Paragon] Seq 9 — Savant":                  {"health": 0,  "attack": 0,  "luck": 0,  "spirituality": 0,  "speed": 0,  "spirit": +3},
    "[Fool] Seq 9 — Seer":                       {"health": -1, "attack": 0,  "luck": 0,  "spirituality": +2, "speed": 0,  "spirit": +1},
    "[Error] Seq 9 — Marauder":                  {"health": 0,  "attack": 0,  "luck": 0,  "spirituality": 0,  "speed": +2, "spirit": 0},
    "[Door] Seq 9 — Apprentice":                 {"health": 0,  "attack": 0,  "luck": 0,  "spirituality": 0,  "speed": +2, "spirit": 0},
    "[Red Priest] Seq 9 — Hunter":               {"health": 0,  "attack": +1, "luck": 0,  "spirituality": 0,  "speed": +1, "spirit": 0},
    "[Demoness] Seq 9 — Assassin":               {"health": 0,  "attack": 0,  "luck": 0,  "spirituality": 0,  "speed": +2, "spirit": 0},
    "[Abyss] Seq 9 — Criminal":                  {"health": 0,  "attack": +2, "luck": 0,  "spirituality": 0,  "speed": 0,  "spirit": 0},
    "[Chained] Seq 9 — Prisoner":                {"health": +1, "attack": 0,  "luck": 0,  "spirituality": 0,  "speed": 0,  "spirit": 0},
    "[Twilight Giant] Seq 9 — Warrior":          {"health": 0,  "attack": +1, "luck": 0,  "spirituality": 0,  "speed": +1, "spirit": 0},
    "[Darkness] Seq 9 — Sleepless":              {"health": 0,  "attack": 0,  "luck": 0,  "spirituality": +1, "speed": 0,  "spirit": 0},
    "[Death] Seq 9 — Corpse Collector":          {"health": +1, "attack": 0,  "luck": 0,  "spirituality": 0,  "speed": 0,  "spirit": 0},
    "[Tyrant] Seq 9 — Sailor":                   {"health": 0,  "attack": +2, "luck": 0,  "spirituality": 0,  "speed": 0,  "spirit": 0},
    "[Sun] Seq 9 — Bard":                        {"health": +1, "attack": +1, "luck": +1, "spirituality": +1, "speed": +1, "spirit": +1},
    "[Hanged Man] Seq 9 — Secrets Suppliant":    {"health": 0,  "attack": 0,  "luck": 0,  "spirituality": +2, "speed": 0,  "spirit": 0},
    "[White Tower] Seq 9 — Reader":              {"health": 0,  "attack": 0,  "luck": 0,  "spirituality": 0,  "speed": 0,  "spirit": +3},
    "[Visionary] Seq 9 — Spectator":             {"health": 0,  "attack": 0,  "luck": 0,  "spirituality": 0,  "speed": 0,  "spirit": +2},
    "[Mother] Seq 9 — Planter":                  {"health": +2, "attack": 0,  "luck": 0,  "spirituality": 0,  "speed": 0,  "spirit": 0},
    "[Moon] Seq 9 — Apothecary":                 {"health": +1, "attack": 0,  "luck": 0,  "spirituality": +1, "speed": 0,  "spirit": 0},
    "[Wheel of Fortune] Seq 9 — Monster":        {"health": 0,  "attack": 0,  "luck": +3, "spirituality": 0,  "speed": 0,  "spirit": 0},
    "[Black Emperor] Seq 9 — Lawyer":            {"health": 0,  "attack": 0,  "luck": 0,  "spirituality": 0,  "speed": 0,  "spirit": +2},
    "[Justiciar] Seq 9 — Arbiter":               {"health": +1, "attack": +2, "luck": 0,  "spirituality": 0,  "speed": 0,  "spirit": 0},
    # Seq 8
    "[Hermit] Seq 8 — Melee Scholar":            {"health": 0,  "attack": 0,  "luck": 0,  "spirituality": +1, "speed": +2, "spirit": +2},
    "[Paragon] Seq 8 — Archaeologist":           {"health": +2, "attack": 0,  "luck": 0,  "spirituality": +1, "speed": 0,  "spirit": +2},
    "[Door] Seq 8 — Trickmaster":                {"health": 0,  "attack": 0,  "luck": 0,  "spirituality": +2, "speed": +3, "spirit": 0},
    "[Fool] Seq 8 — Clown":                      {"health": 0,  "attack": +2, "luck": +1, "spirituality": +1, "speed": +1, "spirit": 0},
    "[Error] Seq 8 — Swindler":                  {"health": 0,  "attack": 0,  "luck": +1, "spirituality": 0,  "speed": +2, "spirit": +1},
    "[Red Priest] Seq 8 — Provoker":             {"health": +1, "attack": 0,  "luck": 0,  "spirituality": +1, "speed": +1, "spirit": 0},
    "[Demoness] Seq 8 — Instigator":             {"health": 0,  "attack": 0,  "luck": 0,  "spirituality": 0,  "speed": +2, "spirit": 0},
    "[Abyss] Seq 8 — Unwinged Angel":            {"health": 0,  "attack": +2, "luck": 0,  "spirituality": 0,  "speed": 0,  "spirit": 0},
    "[Chained] Seq 8 — Lunatic":                 {"health": +2, "attack": 0,  "luck": 0,  "spirituality": 0,  "speed": 0,  "spirit": 0},
    "[Twilight Giant] Seq 8 — Pugilist":         {"health": +1, "attack": +2, "luck": 0,  "spirituality": 0,  "speed": 0,  "spirit": 0},
    "[Darkness] Seq 8 — Midnight Poet":          {"health": +1, "attack": 0,  "luck": +1, "spirituality": 0,  "speed": 0,  "spirit": 0},
    "[Death] Seq 8 — Gravedigger":               {"health": 0,  "attack": 0,  "luck": 0,  "spirituality": +1, "speed": +1, "spirit": 0},
    "[Hanged Man] Seq 8 — Listener":             {"health": 0,  "attack": 0,  "luck": 0,  "spirituality": +3, "speed": 0,  "spirit": 0},
    "[Tyrant] Seq 8 — Folk of Rage":             {"health": +1, "attack": 0,  "luck": 0,  "spirituality": 0,  "speed": 0,  "spirit": 0},
    "[White Tower] Seq 8 — Student of Ratiocination": {"health": 0, "attack": 0, "luck": 0, "spirituality": 0, "speed": 0, "spirit": +3},
    "[Visionary] Seq 8 — Telepathist":           {"health": 0,  "attack": 0,  "luck": +1, "spirituality": 0,  "speed": 0,  "spirit": +2},
    "[Sun] Seq 8 — Light Suppliant":             {"health": 0,  "attack": 0,  "luck": 0,  "spirituality": +2, "speed": 0,  "spirit": +1},
    "[Mother] Seq 8 — Doctor":                   {"health": +2, "attack": 0,  "luck": 0,  "spirituality": +1, "speed": 0,  "spirit": 0},
    "[Moon] Seq 8 — Beast Tamer":                {"health": +1, "attack": 0,  "luck": +1, "spirituality": 0,  "speed": 0,  "spirit": 0},
    "[Wheel of Fortune] Seq 8 — Robot":          {"health": +1, "attack": 0,  "luck": +2, "spirituality": +1, "speed": 0,  "spirit": +2},
    "[Black Emperor] Seq 8 — Barbarian":         {"health": 0,  "attack": +2, "luck": 0,  "spirituality": 0,  "speed": 0,  "spirit": +1},
    "[Justiciar] Seq 8 — Sheriff":               {"health": 0,  "attack": +1, "luck": 0,  "spirituality": 0,  "speed": +2, "spirit": 0},
    # Seq 7
    "[Fool] Seq 7 — Magician":                   {"health": 0,  "attack": 0,  "luck": +1, "spirituality": +1, "speed": +1, "spirit": +1},
    "[Door] Seq 7 — Astrologer":                 {"health": 0,  "attack": 0,  "luck": +1, "spirituality": +2, "speed": +1, "spirit": +1},
    "[Error] Seq 7 — Cryptologist":              {"health": 0,  "attack": 0,  "luck": 0,  "spirituality": +1, "speed": +2, "spirit": +2},
    "[Visionary] Seq 7 — Psychiatrist":          {"health": +1, "attack": 0,  "luck": 0,  "spirituality": +2, "speed": 0,  "spirit": +2},
    "[Sun] Seq 7 — Solar High Priest":           {"health": +2, "attack": 0,  "luck": +1, "spirituality": +2, "speed": 0,  "spirit": +1},
    "[Tyrant] Seq 7 — Seafarer":                 {"health": +1, "attack": +1, "luck": 0,  "spirituality": +1, "speed": +2, "spirit": 0},
    "[Hanged Man] Seq 7 — Shadow Ascetic":       {"health": 0,  "attack": +1, "luck": +1, "spirituality": +1, "speed": +2, "spirit": 0},
    "[White Tower] Seq 7 — Detective":           {"health": 0,  "attack": 0,  "luck": 0,  "spirituality": +1, "speed": 0,  "spirit": +3},
    "[Darkness] Seq 7 — Nightmare":              {"health": +1, "attack": 0,  "luck": +1, "spirituality": +2, "speed": 0,  "spirit": +1},
    "[Death] Seq 7 — Spirit Medium":             {"health": 0,  "attack": 0,  "luck": +1, "spirituality": +2, "speed": +1, "spirit": 0},
    "[Twilight Giant] Seq 7 — Weapon Master":    {"health": +1, "attack": +2, "luck": 0,  "spirituality": +1, "speed": +1, "spirit": 0},
    "[Demoness] Seq 7 — Witch":                  {"health": 0,  "attack": 0,  "luck": +1, "spirituality": +2, "speed": +1, "spirit": +1},
    "[Red Priest] Seq 7 — Pyromaniac":           {"health": 0,  "attack": +2, "luck": 0,  "spirituality": +1, "speed": +1, "spirit": 0},
    "[Hermit] Seq 7 — Warlock":                  {"health": 0,  "attack": +1, "luck": +1, "spirituality": +2, "speed": 0,  "spirit": +1},
    "[Paragon] Seq 7 — Appraiser":               {"health": 0,  "attack": 0,  "luck": 0,  "spirituality": +1, "speed": 0,  "spirit": +3},
    "[Wheel of Fortune] Seq 7 — Lucky One":      {"health": 0,  "attack": 0,  "luck": +3, "spirituality": +1, "speed": 0,  "spirit": +1},
    "[Moon] Seq 7 — Vampire":                    {"health": +1, "attack": +1, "luck": 0,  "spirituality": +1, "speed": +1, "spirit": 0},
    "[Mother] Seq 7 — Harvest Priest":           {"health": +2, "attack": 0,  "luck": 0,  "spirituality": +1, "speed": 0,  "spirit": +1},
    "[Chained] Seq 7 — Werewolf":                {"health": +3, "attack": +2, "luck": 0,  "spirituality": 0,  "speed": +1, "spirit": 0},
    "[Abyss] Seq 7 — Serial Killer":             {"health": 0,  "attack": +2, "luck": +1, "spirituality": +1, "speed": +1, "spirit": 0},
    "[Black Emperor] Seq 7 — Briber":            {"health": +1, "attack": 0,  "luck": +1, "spirituality": 0,  "speed": 0,  "spirit": +2},
    "[Justiciar] Seq 7 — Interrogator":          {"health": +1, "attack": +2, "luck": 0,  "spirituality": +1, "speed": +1, "spirit": 0},
    # Seq 6
    "[Fool] Seq 6 — Faceless":                   {"health": +1, "attack": +1, "luck": +1, "spirituality": +1, "speed": +1, "spirit": +2},
    "[Error] Seq 6 — Prometheus":                {"health": 0,  "attack": 0,  "luck": +1, "spirituality": +1, "speed": +2, "spirit": +2},
    "[Door] Seq 6 — Scribe":                     {"health": 0,  "attack": 0,  "luck": +1, "spirituality": +2, "speed": +2, "spirit": +2},
    "[Visionary] Seq 6 — Hypnotist":             {"health": +1, "attack": 0,  "luck": +1, "spirituality": +2, "speed": 0,  "spirit": +3},
    "[Sun] Seq 6 — Notary":                      {"health": +2, "attack": +1, "luck": +1, "spirituality": +2, "speed": +1, "spirit": +2},
    "[Tyrant] Seq 6 — Wind-blessed":             {"health": +2, "attack": +3, "luck": 0,  "spirituality": +1, "speed": +2, "spirit": 0},
    "[Hanged Man] Seq 6 — Rose Bishop":          {"health": +1, "attack": 0,  "luck": 0,  "spirituality": +3, "speed": 0,  "spirit": +2},
    "[White Tower] Seq 6 — Polymath":            {"health": 0,  "attack": 0,  "luck": 0,  "spirituality": +2, "speed": 0,  "spirit": +4},
    "[Darkness] Seq 6 — Soul Assurer":           {"health": +1, "attack": 0,  "luck": +1, "spirituality": +2, "speed": 0,  "spirit": +2},
    "[Death] Seq 6 — Spirit Guide":              {"health": +2, "attack": 0,  "luck": +1, "spirituality": +2, "speed": 0,  "spirit": +1},
    "[Twilight Giant] Seq 6 — Dawn Paladin":     {"health": +3, "attack": +2, "luck": 0,  "spirituality": +1, "speed": +1, "spirit": +1},
    "[Demoness] Seq 6 — Pleasure":               {"health": 0,  "attack": 0,  "luck": +2, "spirituality": +1, "speed": +2, "spirit": +2},
    "[Red Priest] Seq 6 — Conspirer":            {"health": 0,  "attack": +1, "luck": 0,  "spirituality": +1, "speed": +2, "spirit": +2},
    "[Hermit] Seq 6 — Scrolls Professor":        {"health": 0,  "attack": 0,  "luck": +2, "spirituality": +3, "speed": 0,  "spirit": +2},
    "[Paragon] Seq 6 — Artisan":                 {"health": 0,  "attack": 0,  "luck": +1, "spirituality": +2, "speed": 0,  "spirit": +4},
    "[Wheel of Fortune] Seq 6 — Calamity Priest":{"health": +1, "attack": 0,  "luck": +3, "spirituality": +1, "speed": 0,  "spirit": +2},
    "[Moon] Seq 6 — Potions Professor":          {"health": +2, "attack": 0,  "luck": +2, "spirituality": +2, "speed": 0,  "spirit": +1},
    "[Mother] Seq 6 — Biologist":                {"health": +3, "attack": +1, "luck": +2, "spirituality": +1, "speed": 0,  "spirit": +1},
    "[Chained] Seq 6 — Zombie":                  {"health": +3, "attack": +2, "luck": +1, "spirituality": 0,  "speed": +1, "spirit": 0},
    "[Abyss] Seq 6 — Devil":                     {"health": +1, "attack": +3, "luck": +1, "spirituality": +1, "speed": +1, "spirit": 0},
    "[Black Emperor] Seq 6 — Baron of Corruption":{"health": +1, "attack": 0, "luck": +1, "spirituality": +1, "speed": 0,  "spirit": +3},
    "[Justiciar] Seq 6 — Judge":                 {"health": +2, "attack": +2, "luck": 0,  "spirituality": +1, "speed": +1, "spirit": +2},
    # Seq 5
    "[Fool] Seq 5 — Marionettist":               {"health": +2, "attack": +1, "luck": +2, "spirituality": +3, "speed": +1, "spirit": +3},
    "[Error] Seq 5 — Dream Stealer":             {"health": +1, "attack": +1, "luck": +2, "spirituality": +3, "speed": +2, "spirit": +3},
    "[Door] Seq 5 — Traveler":                   {"health": +2, "attack": +1, "luck": +1, "spirituality": +2, "speed": +4, "spirit": +2},
    "[Visionary] Seq 5 — Dreamwalker":           {"health": +1, "attack": +1, "luck": +2, "spirituality": +3, "speed": +1, "spirit": +4},
    "[Sun] Seq 5 — Priest of Light":             {"health": +3, "attack": +2, "luck": +2, "spirituality": +3, "speed": +1, "spirit": +3},
    "[Tyrant] Seq 5 — Ocean Songster":           {"health": +3, "attack": +3, "luck": +1, "spirituality": +2, "speed": +2, "spirit": +1},
    "[Hanged Man] Seq 5 — Shepherd":             {"health": +2, "attack": +1, "luck": +2, "spirituality": +3, "speed": +1, "spirit": +4},
    "[White Tower] Seq 5 — Mysticism Magister":  {"health": +1, "attack": +1, "luck": +1, "spirituality": +3, "speed": +1, "spirit": +5},
    "[Darkness] Seq 5 — Spirit Warlock":         {"health": +2, "attack": +2, "luck": +1, "spirituality": +3, "speed": +1, "spirit": +3},
    "[Death] Seq 5 — Gatekeeper":                {"health": +3, "attack": +2, "luck": +1, "spirituality": +3, "speed": +1, "spirit": +2},
    "[Twilight Giant] Seq 5 — Guardian":         {"health": +5, "attack": +3, "luck": 0,  "spirituality": +2, "speed": +1, "spirit": +1},
    "[Demoness] Seq 5 — Affliction":             {"health": +2, "attack": +2, "luck": +2, "spirituality": +2, "speed": +2, "spirit": +2},
    "[Red Priest] Seq 5 — Reaper":               {"health": +2, "attack": +4, "luck": +1, "spirituality": +2, "speed": +2, "spirit": +1},
    "[Hermit] Seq 5 — Mysticologist":            {"health": +1, "attack": +2, "luck": +2, "spirituality": +3, "speed": +1, "spirit": +3},
    "[Paragon] Seq 5 — Astronomer":              {"health": +2, "attack": +2, "luck": +3, "spirituality": +2, "speed": +1, "spirit": +3},
    "[Wheel of Fortune] Seq 5 — Winner":         {"health": +2, "attack": +2, "luck": +5, "spirituality": +2, "speed": +1, "spirit": +2},
    "[Moon] Seq 5 — Scarlet Scholar":            {"health": +3, "attack": +2, "luck": +2, "spirituality": +3, "speed": +1, "spirit": +2},
    "[Mother] Seq 5 — Druid":                    {"health": +4, "attack": +2, "luck": +2, "spirituality": +2, "speed": +1, "spirit": +1},
    "[Chained] Seq 5 — Wraith":                  {"health": +3, "attack": +3, "luck": +1, "spirituality": +2, "speed": +2, "spirit": +1},
    "[Abyss] Seq 5 — Desire Apostle":            {"health": +2, "attack": +3, "luck": +2, "spirituality": +3, "speed": +1, "spirit": +1},
    "[Black Emperor] Seq 5 — Mentor of Disorder":{"health": +2, "attack": +2, "luck": +2, "spirituality": +2, "speed": +1, "spirit": +3},
    "[Justiciar] Seq 5 — Disciplinary Paladin":  {"health": +3, "attack": +3, "luck": +1, "spirituality": +2, "speed": +1, "spirit": +2},
}

ROLE_ABILITIES = {
    # Seq 9
    "[Hermit] Seq 9 — Mystery Pryer":            "prying",
    "[Paragon] Seq 9 — Savant":                  "memorise",
    "[Fool] Seq 9 — Seer":                       "danger_intuition",
    "[Error] Seq 9 — Marauder":                  "pickpocket",
    "[Door] Seq 9 — Apprentice":                 "phasing",
    "[Red Priest] Seq 9 — Hunter":               "trap",
    "[Demoness] Seq 9 — Assassin":               "vital_strike",
    "[Abyss] Seq 9 — Criminal":                  "crime",
    "[Chained] Seq 9 — Prisoner":                "break_out",
    "[Twilight Giant] Seq 9 — Warrior":          "invigorate",
    "[Darkness] Seq 9 — Sleepless":              "night_boost",
    "[Death] Seq 9 — Corpse Collector":          "desecration",
    "[Tyrant] Seq 9 — Sailor":                   "balancing_act",
    "[Sun] Seq 9 — Bard":                        "song",
    "[Hanged Man] Seq 9 — Secrets Suppliant":    "prayer",
    "[White Tower] Seq 9 — Reader":              "study",
    "[Visionary] Seq 9 — Spectator":             "spectate",
    "[Mother] Seq 9 — Planter":                  "planting",
    "[Moon] Seq 9 — Apothecary":                 "curing",
    "[Wheel of Fortune] Seq 9 — Monster":        "glance_into_fate",
    "[Black Emperor] Seq 9 — Lawyer":            "testimony",
    "[Justiciar] Seq 9 — Arbiter":               "arbitration",
    # Seq 8
    "[Hermit] Seq 8 — Melee Scholar":            "combat_studies",
    "[Paragon] Seq 8 — Archaeologist":           "excavation",
    "[Door] Seq 8 — Trickmaster":                "magic_trick",
    "[Fool] Seq 8 — Clown":                      "card_tricks",
    "[Error] Seq 8 — Swindler":                  "swindling",
    "[Red Priest] Seq 8 — Provoker":             "provocation",
    "[Demoness] Seq 8 — Instigator":             "instigation",
    "[Abyss] Seq 8 — Unwinged Angel":            "moral_freedom",
    "[Chained] Seq 8 — Lunatic":                 "rage_baited",
    "[Twilight Giant] Seq 8 — Pugilist":         "fighting_spirit",
    "[Darkness] Seq 8 — Midnight Poet":          "poem",
    "[Death] Seq 8 — Gravedigger":               "burial_restoration",
    "[Hanged Man] Seq 8 — Listener":             "listening",
    "[Tyrant] Seq 8 — Folk of Rage":             "power_up_punch",
    "[White Tower] Seq 8 — Student of Ratiocination": "ritualistic_reasoning",
    "[Visionary] Seq 8 — Telepathist":           "reading",
    "[Sun] Seq 8 — Light Suppliant":             "brilliant_light",
    "[Mother] Seq 8 — Doctor":                   "treatment",
    "[Moon] Seq 8 — Beast Tamer":                "animal_companion",
    "[Wheel of Fortune] Seq 8 — Robot":          "computing",
    "[Black Emperor] Seq 8 — Barbarian":         "domination",
    "[Justiciar] Seq 8 — Sheriff":               "jurisdiction",
    # Seq 7
    "[Fool] Seq 7 — Magician":                   "paper_figurine",
    "[Door] Seq 7 — Astrologer":                 "astrology",
    "[Error] Seq 7 — Cryptologist":              "observation",
    "[Visionary] Seq 7 — Psychiatrist":          "therapy",
    "[Sun] Seq 7 — Solar High Priest":           "holy_water",
    "[Tyrant] Seq 7 — Seafarer":                 "water_bullet",
    "[Hanged Man] Seq 7 — Shadow Ascetic":       "sneak_attack",
    "[White Tower] Seq 7 — Detective":           "analyse",
    "[Darkness] Seq 7 — Nightmare":              "sleep_spell",
    "[Death] Seq 7 — Spirit Medium":             "summoning_dead",
    "[Twilight Giant] Seq 7 — Weapon Master":    "weapon_throw",
    "[Demoness] Seq 7 — Witch":                  "witch_curse",
    "[Red Priest] Seq 7 — Pyromaniac":           "fire_ravens",
    "[Hermit] Seq 7 — Warlock":                  "magic_spell",
    "[Paragon] Seq 7 — Appraiser":               "appraisal",
    "[Wheel of Fortune] Seq 7 — Lucky One":      "lucky_day",
    "[Moon] Seq 7 — Vampire":                    "blood_sucker",
    "[Mother] Seq 7 — Harvest Priest":           "vine_bomb",
    "[Chained] Seq 7 — Werewolf":               "transformation",
    "[Abyss] Seq 7 — Serial Killer":             "vile_crime",
    "[Black Emperor] Seq 7 — Briber":            "bribe",
    "[Justiciar] Seq 7 — Interrogator":          "mental_piercing",
    # Seq 6
    "[Fool] Seq 6 — Faceless":                   "faceless",
    "[Error] Seq 6 — Prometheus":                "prometheus",
    "[Door] Seq 6 — Scribe":                     "scribe",
    "[Visionary] Seq 6 — Hypnotist":             "hypnotist",
    "[Sun] Seq 6 — Notary":                      "notary",
    "[Tyrant] Seq 6 — Wind-blessed":             "wind_blessed",
    "[Hanged Man] Seq 6 — Rose Bishop":          "rose_bishop",
    "[White Tower] Seq 6 — Polymath":            "polymath",
    "[Darkness] Seq 6 — Soul Assurer":           "pacification",
    "[Death] Seq 6 — Spirit Guide":              "spirit_guide",
    "[Twilight Giant] Seq 6 — Dawn Paladin":     "hurricane_of_light",
    "[Demoness] Seq 6 — Pleasure":               "pleasure_witch",
    "[Red Priest] Seq 6 — Conspirer":            "conspiracy",
    "[Hermit] Seq 6 — Scrolls Professor":        "scrolls_professor",
    "[Paragon] Seq 6 — Artisan":                 "artisan",
    "[Wheel of Fortune] Seq 6 — Calamity Priest":"calamity_priest",
    "[Moon] Seq 6 — Potions Professor":          "potions_professor",
    "[Mother] Seq 6 — Biologist":                "biologist",
    "[Chained] Seq 6 — Zombie":                  "zombie",
    "[Abyss] Seq 6 — Devil":                     "devil_form",
    "[Black Emperor] Seq 6 — Baron of Corruption":"distortion",
    "[Justiciar] Seq 6 — Judge":                 "judge",
    # Seq 5
    "[Fool] Seq 5 — Marionettist":               "spirit_thread",
    "[Error] Seq 5 — Dream Stealer":             "mental_theft",
    "[Door] Seq 5 — Traveler":                   "blink",
    "[Visionary] Seq 5 — Dreamwalker":           "dream_visitation",
    "[Sun] Seq 5 — Priest of Light":             "purification_halo",
    "[Tyrant] Seq 5 — Ocean Songster":           "singing",
    "[Hanged Man] Seq 5 — Shepherd":             "grazing",
    "[White Tower] Seq 5 — Mysticism Magister":  "combination_spell",
    "[Darkness] Seq 5 — Spirit Warlock":         "spiritual_suppression",
    "[Death] Seq 5 — Gatekeeper":                "dragged_to_hell",
    "[Twilight Giant] Seq 5 — Guardian":         "protection",
    "[Demoness] Seq 5 — Affliction":             "disease_propagation",
    "[Red Priest] Seq 5 — Reaper":               "cull",
    "[Hermit] Seq 5 — Mysticologist":            "stellar_self",
    "[Paragon] Seq 5 — Astronomer":              "fire_storm",
    "[Wheel of Fortune] Seq 5 — Winner":         "curse_of_misfortune",
    "[Moon] Seq 5 — Scarlet Scholar":            "artificial_moon",
    "[Mother] Seq 5 — Druid":                    "spirit_animal_transformation",
    "[Chained] Seq 5 — Wraith":                  "possession",
    "[Abyss] Seq 5 — Desire Apostle":            "desire_explosion",
    "[Black Emperor] Seq 5 — Mentor of Disorder":"disorder",
    "[Justiciar] Seq 5 — Disciplinary Paladin":  "prohibition",

}

# Secondary abilities for Seq 5 (each Seq 5 role gets 2 abilities)
ROLE_ABILITIES_2 = {
    "[Fool] Seq 5 — Marionettist":               "marionette_summon",
    "[Error] Seq 5 — Dream Stealer":             "rewards_theft",
    "[Door] Seq 5 — Traveler":                   "travellers_door",
    "[Visionary] Seq 5 — Dreamwalker":           "dream_alteration",
    "[Sun] Seq 5 — Priest of Light":             "light_of_holiness",
    "[Tyrant] Seq 5 — Ocean Songster":           "arrow_of_lightning",
    "[Hanged Man] Seq 5 — Shepherd":             "ability_bag",
    "[White Tower] Seq 5 — Mysticism Magister":  "combination_spell",
    "[Darkness] Seq 5 — Spirit Warlock":         "spiritual_takeover",
    "[Death] Seq 5 — Gatekeeper":                "evil_sealing",
    "[Twilight Giant] Seq 5 — Guardian":         "dawn_armor",
    "[Demoness] Seq 5 — Affliction":             "thread_storm",
    "[Red Priest] Seq 5 — Reaper":               "weakness_development",
    "[Hermit] Seq 5 — Mysticologist":            "star_pillar",
    "[Paragon] Seq 5 — Astronomer":              "star_of_curses",
    "[Wheel of Fortune] Seq 5 — Winner":         "active_luck_boost",
    "[Moon] Seq 5 — Scarlet Scholar":            "scarlet_transformation",
    "[Mother] Seq 5 — Druid":                    "wrath_of_nature",
    "[Chained] Seq 5 — Wraith":                  "wraith_shriek",
    "[Abyss] Seq 5 — Desire Apostle":            "desire_symbiosis",
    "[Black Emperor] Seq 5 — Mentor of Disorder":  "gift_of_corruption",
    "[Justiciar] Seq 5 — Disciplinary Paladin":     "punishment",
}

SPELL_DEFENCE_ROLES = {
    "[Death] Seq 9 — Corpse Collector",
    "[Moon] Seq 9 — Apothecary",
    "[Chained] Seq 9 — Prisoner",
    "[Mother] Seq 8 — Doctor",
    "[Wheel of Fortune] Seq 8 — Robot",
    "[Sun] Seq 7 — Solar High Priest",
    "[Visionary] Seq 7 — Psychiatrist",
}

ALL_ROLE_ABILITY_KEYS = set(ROLE_ABILITIES.values())

ROLE_ABILITY_INFO = {
    # Seq 9
    "prying":               {"name": "🔍 Prying",             "cost": 20, "description": "Lower enemy defence by 15-25% for the rest of the battle."},
    "memorise":             {"name": "📖 Memorise",            "cost": 20, "description": "Use a selected ability SP-free for 1-2 turns."},
    "danger_intuition":     {"name": "👁️ Danger Intuition",    "cost": 20, "description": "Toggle on/off. While active: 35% dodge, 65% take 50% damage. Costs 20 SP upkeep per turn. No cooldown."},
    "pickpocket":           {"name": "🖐️ Pickpocket",          "cost": 18, "description": "Steal 15-20 SP. 1/10 chance to steal 5 HP too. (2 turn cooldown)"},
    "phasing":              {"name": "👻 Phasing",             "cost": 20, "description": "Dodge the next attack or increase flee chance. (1 turn cooldown)"},
    "trap":                 {"name": "🪤 Trap",                "cost": 0,  "display_cost": "35 SP", "description": "Spend your turn to secretly lay 2 traps (35 SP, 8 turn cd). Activates after 1 turn, randomly within 5 rounds: Flames (burn 3 turns), Trip (miss attack), Dog Chase (−5–7% base HP/turn, 2 turns), Tripwire (30% backlash or 7% base HP damage). After the trap ends it goes on cooldown for 3-4 turns. Traps cannot be stacked — setting new ones replaces old ones."},
    "vital_strike":         {"name": "🗡️ Vital Strike",        "cost": 25, "description": "Sneak attack — channel all strength into one point for **66 damage**. (4 turn cooldown)"},
    "crime":                {"name": "🦹 Crime",               "cost": 22, "description": "Randomly inflict bleed, paralysis (2 turn cd if lands), or deal damage."},
    "break_out":            {"name": "⛓️ Break Out",           "cost": 20, "description": "Remove all harmful status effects — bleed, burn, paralysis, sleep, freeze, defence reduction, sneak attack, trap, charm, scroll affliction, moral freedom, debuffs, and notary debuff."},
    "invigorate":           {"name": "💢 Invigorate",          "cost": 22, "description": "Deal 30% more damage on your next attack."},
    "night_boost":          {"name": "🌙 Night Boost",         "cost": 30, "description": "Deal +10 damage for the rest of the battle. One use only."},
    "desecration":          {"name": "💀 Desecration",         "cost": 25, "description": "Paralysis for 1 turn and 15 damage. (4 turn cooldown)"},
    "balancing_act":        {"name": "⚖️ Balancing Act",       "cost": 22, "description": "50% each: remove opponent status OR gain 5% permanent damage. (2 turn cd)"},
    "song":                 {"name": "🎵 Song",                "cost": 8,  "description": "Restore 20% of max SP. No cooldown."},
    "prayer":               {"name": "🙏 Prayer",              "cost": 25, "description": "All damage +50% permanently, but you bleed 5-8 HP per turn forever."},
    "study":                {"name": "📚 Study",               "cost": 0,  "display_cost": "Skip turn", "description": "Restore all SP at the cost of skipping your next turn."},
    "spectate":             {"name": "👁️ Spectate",            "cost": 20, "description": "Boost flee chance to 60% and improve dodge. Lasts the battle."},
    "planting":             {"name": "🌱 Planting",            "cost": 18, "description": "Recover 20 HP per turn for 3 turns."},
    "curing":               {"name": "🧪 Curing",              "cost": 0,  "display_cost": "24% max SP", "description": "Recover 32% of max HP at the cost of 24% of max SP."},
    "glance_into_fate":     {"name": "🎴 Glance into Fate",    "cost": 30, "description": "Spend 30 SP to suppress the opponent's role ability for 2 turns, leaving them without it. (4 turn cooldown)"},
    "testimony":            {"name": "⚖️ Testimony",           "cost": 25, "description": "Randomly: drain 15-25% enemy base HP (max 35% user base HP), OR heal 20-25% user base HP, OR regen 20-25% user base SP. (2 turn cooldown)"},
    "arbitration":          {"name": "⚖️ Arbiter",             "cost": 30, "description": "Declare a ceasefire — both fighters recuperate for 1-3 turns (no attacks or debuffs). 40% chance of disagreement and failure. Violation deals 40% of violator's HP as divine punishment. (6 turn cooldown)"},
    "jurisdiction":         {"name": "🏛️ Jurisdiction",        "cost": 40, "description": "Declare the battlefield your domain — +20% damage for 3 turns, increasing by 7% each turn. (12 turn cooldown)"},
    # Seq 8
    "combat_studies":       {"name": "📘 Combat Studies",      "cost": 0,  "display_cost": "Skip turn", "description": "Spend a turn studying — permanently increase all attacks by 20%."},
    "excavation":           {"name": "⛏️ Excavation",          "cost": 20, "description": "Dig for ancient treasure — gain 20-30% max SP, 12-20% max HP, or stun block + 6% max HP."},
    "magic_trick":          {"name": "🎩 Magic Trick",         "cost": 22, "description": "Random: freeze 2 turns (4 turn cd), burn 2 turns, or stun 1 turn (2 turn cd). Or escape from battle."},
    "card_tricks":          {"name": "🃏 Card Tricks",         "cost": 25, "description": "Deal 3×10 damage with bleed for 3 turns. (2 turn cooldown)"},
    "swindling":            {"name": "🎭 Swindling",           "cost": 15, "description": "Steal 20 SP. Failure chance increases each use."},
    "provocation":          {"name": "😤 Provocation",         "cost": 25, "description": "Force enemy to skip their next 1 turn and drain 25 SP from them. (4 turn cooldown)"},
    "instigation":          {"name": "😈 Instigation",         "cost": 18, "description": "Pick one of opponent's moves to force them to use. 50% success."},
    "moral_freedom":        {"name": "👼 Moral Freedom",       "cost": 30, "description": "Apply a status dealing 15 damage/turn to opponent. One use only."},
    "rage_baited":          {"name": "😡 Rage Baited",         "cost": 22, "description": "Double damage at 50% miss chance for 2 turns. (2 turn cooldown)"},
    "fighting_spirit":      {"name": "🛡️ Supernatural Resist",  "cost": 20, "description": "Gain immunity to status effects. Each time a status is attempted against you, take 40 damage instead of being afflicted."},
    "poem":                 {"name": "📜 Midnight Poem",        "cost": 25, "description": "Recite a dark verse — 33% sleep opponent 1 turn, 33% deal 15 self-damage, 33% drain 20 SP. (3 turn cooldown)"},
    "burial_restoration":   {"name": "⚰️ Burial Restoration",  "cost": 35, "description": "Restore the grave of an unfortunate soul and obtain their blessing — fully restores your SP. (4-round cooldown)"},
    "listening":            {"name": "👂 Listening",           "cost": 15, "description": "Fully restore SP. Each turn after has a 1/3 chance to stun yourself."},
    "power_up_punch":       {"name": "👊 Power Up Punch",      "cost": 22, "description": "Normal attacks amplified by 5% per round for 5 rounds (max 25%). Can't stack."},
    "ritualistic_reasoning":{"name": "🔬 Ritualistic Reasoning","cost": 30, "description": "Spend 30 SP to sabotage opponent's next move — it fails or backfires."},
    "reading":              {"name": "🔭 Reading",             "cost": 20, "description": "Secretly predict an opponent's move. If correct, it misses AND they are stunned for 1 turn. If wrong, you are stunned for 1 turn as backlash. The opponent cannot see what you predicted."},
    "brilliant_light":      {"name": "☀️ Brilliant Light",     "cost": 20, "description": "Flash of light deals 10 damage and stuns opponent for 1 turn. (2 turn cooldown if stun lands)"},
    "treatment":            {"name": "💊 Treatment",           "cost": 25, "description": "Grant yourself immunity to status effects for 3 turns."},
    "animal_companion":     {"name": "🐾 Animal Companion",    "cost": 20, "description": "Summon a companion that attacks for 10 damage every other turn."},
    "computing":            {"name": "🤖 Computing",           "cost": 30, "description": "Increase accuracy of all chance-based attacks to 100% for the rest of battle."},
    "domination":           {"name": "👊 Domination",           "cost": 30, "description": "Boost damage by 40% for 3 turns. Stuns from same seq have 50% reduced chance, from lower seq are 70% ineffective. (5 turn CD)"},
    "gun_shot":             {"name": "🔫 Gun Shot",            "cost": 50, "description": "Deal damage and apply permanent bleed for the rest of the battle. (4 turn cooldown)"},
    # Seq 7
    "paper_figurine":       {"name": "🪆 Paper Figurine",       "cost": 20, "description": "All attacks against you fail for 2 turns — including beyonder abilities, physical hits, special attacks, and unique shop abilities (e.g. Leodero)."},
    "astrology":            {"name": "🌟 Astrology",            "cost": 35, "description": "One-time use: if opponent is higher seq, option to immediately flee. Otherwise strike a weak point for 40% of your current HP as damage. (One-time use)"},
    "observation":          {"name": "🔎 Observation",          "cost": 18, "description": "You take 40% less damage from all sources next turn. (2 turn cooldown)"},
    "therapy":              {"name": "🛋️ Therapy",              "cost": 0,  "display_cost": "Skip turn", "description": "Spend a turn clearing all harmful effects — bleed, burn, paralysis, sleep, freeze, defence reduction, sneak attack, trap, charm, gunshot bleed, scroll affliction, moral freedom, debuffs, and notary debuff. (1 turn cooldown)"},
    "holy_water":           {"name": "💧 Holy Water",           "cost": 20, "description": "Heal ~12% max HP now and ~3% max HP/turn for 2 turns."},
    "water_bullet":         {"name": "💧🔫 Water Bullet",       "cost": 18, "description": "Deal 30 damage. 25% chance to freeze for 1 turn."},
    "sneak_attack":         {"name": "🥷 Sneak Attack",         "cost": 20, "description": "Strike from shadows for **30 damage** and stun opponent for 1 turn. (2 turn cooldown)"},
    "analyse":              {"name": "🧐 Analyse",              "cost": 18, "description": "Pick a skill — it permanently deals 30% less damage. 2 uses total."},
    "sleep_spell":          {"name": "😴 Sleep Spell",          "cost": 22, "description": "Put opponent to sleep for 2 turns. (4 turn cooldown)"},
    "summoning_dead":       {"name": "💀 Summoning Dead",       "cost": 0,  "display_cost": "20–30 SP", "description": "Summon 1-3 spirits (5/25/70%) — each deals 5 damage, max stuns. (2 turn cooldown)"},
    "weapon_throw":         {"name": "🗡️ Weapon Throw",         "cost": 22, "description": "Deal 40 damage + bleed 3 turns. (4 turn cooldown)"},
    "witch_curse":          {"name": "🧙 Witch's Curse",        "cost": 30, "description": "Applies a random curse: Black Flames (burn 10 dmg/turn × 2 turns), Frost (freeze 2 turns, no actions or fleeing), or Voodoo Curse (128 damage). (2 turn cooldown)"},
    "fire_ravens":          {"name": "🐦‍🔥 Fire Ravens",        "cost": 40, "description": "Summon 4-7 fire ravens, each dealing 10-15 damage. (3 turn cooldown)"},
    "magic_spell":          {"name": "✨ Magic Spell",           "cost": 32, "description": "Random: +30% permanent damage boost, heal 40 HP, or restore 30 SP. (1 turn cooldown)"},
    "appraisal":            {"name": "🏷️ Appraisal",            "cost": 18, "description": "Next action deals 40% more damage. (3 turn cooldown)"},
    "lucky_day":            {"name": "🍀 Lucky Day",            "cost": 20, "description": "Halve opponent's hit chance for 4 rounds. One use only."},
    "blood_sucker":         {"name": "🧛 Blood Sucker",         "cost": 20, "description": "Drain 15-30 HP from opponent — gain that HP yourself. (1 turn cooldown)"},
    "vine_bomb":            {"name": "🌿 Vine Bomb",            "cost": 50, "description": "Deal 25 damage + trap opponent for 2 turns (no actions). (4 turn cooldown)"},
    "transformation":       {"name": "🐺 Transformation",       "cost": 25, "description": "One-use: ×1.5 max HP, ~2% HP regen/turn, dodge 15%→85% over 5 turns. Reverts to 50% HP after 5 turns."},
    "vile_crime":           {"name": "🔪 Vile Crime",           "cost": 12, "description": "One-use: track 3 standard attack hits — then apply permanent bleed and burn."},
    "bribe":                {"name": "💰 Bribe",                "cost": 0,  "display_cost": "100k soli", "description": "Spend 100,000 soli to choose a bribe: Weaken (−40% next attack), Charm (can't attack for 2 turns), or Connect (share 40% of your damage taken with them for 1 turn). Disguised as an evaded attack. 10% fail per spirit stat enemy has over you. 5 turn CD."},
    "mental_piercing":      {"name": "🧠 Mental Piercing",      "cost": 22, "description": "Deal 30 HP damage + drain 10 SP. (2 turn cooldown)"},
    # Seq 6
    "faceless":             {"name": "🎭 Faceless",              "cost": 30, "description": "Infiltrate your enemy's trust then betray them — 35 damage + opponent skips next turn. (2 turn cooldown)"},
    "prometheus":           {"name": "🔥 Prometheus",            "cost": 35, "description": "Steal a random ability your enemy has used, removing it from them. If higher seq than enemy, pick any ability to steal. Stored until used or replaced. (3 turn cooldown)"},
    "scribe":               {"name": "📖 Scribe",                "cost": 40, "description": "Record an ability 1 turn after it is used against you, even while stunned. Only one ability stored at a time — recording a new one replaces the old. (3 turn cooldown)"},
    "hypnotist":            {"name": "🌀 Hypnotist",             "cost": 20, "description": "Trance your opponent — deal 20 self-damage AND stun them for 1 turn. Both effects apply simultaneously. (2 turn cooldown)"},
    "notary":               {"name": "📜 Notary",                "cost": 35, "description": "Proclaim in the name of your god — choose: +50% damage for yourself for 3 turns OR -50% damage on opponent for 2 turns. (4 turn cooldown)"},
    "wind_blessed":         {"name": "🌪️ Wind-blessed",          "cost": 0,  "display_cost": "25 SP/round", "description": "Channel the wind for 2 build-up rounds (25 SP each), then unleash 60 damage. (5 turn cooldown after activation)"},
    "rose_bishop":          {"name": "🌹 Rose Bishop",           "cost": 0,  "display_cost": "Choose",      "description": "Flesh & Blood magic — Flesh Bomb: 13.6% seq dmg at cost of 20 HP + 15 SP. Blood Regen: restore 15% max HP at cost of 25 SP. (2 turn cooldown)"},
    "polymath":             {"name": "📚 Polymath",              "cost": 15, "description": "Study an opponent's ability and use it at 70% potency for the rest of battle. Each use swaps to a different ability. (2 turn cooldown)"},
    "pacification":         {"name": "☮️ Pacification",         "cost": 38, "description": "Clear all buffs from your opponent — OR clear all debuffs/status effects from yourself. Your choice. (6 turn cooldown)"},
    "spirit_guide":         {"name": "👻 Spirit Guide",          "cost": 50, "description": "Summon a great spirit for 5 turns — strikes ~9% seq HP/turn, costs ~6% max SP/turn. 30% chance to possess you (stun) instead of attacking. (7 turn cooldown)"},
    "hurricane_of_light":   {"name": "🌪️ Hurricane of Light",   "cost": 60, "description": "Stab Sword of Dawn into ground (1 turn charge), then auto-releases next turn dealing 35-40% avg base HP damage. Evasion (distortion/phasing) only mitigates 50%. Backlash: 30% of your hit. 3 uses max. (12 turn cooldown)"},
    "pleasure_witch":       {"name": "💋 Pleasure Witch",        "cost": 25, "description": "Charm your opponent into skipping their turn. 40% chance to linger each round, dropping by 10% per round until it fades. (3 turn cooldown)"},
    "conspiracy":           {"name": "🕸️ Conspiracy",            "cost": 50, "description": "Guaranteed: one random effect always lands. Then each remaining effect has 33% to also hit — miss debuff (3 rounds), forced random ability (3 rounds), or 40 dmg + 60 SP drain. (5 turn cooldown)"},
    "scrolls_professor":    {"name": "📜 Scrolls Professor",     "cost": 50, "description": "Summon a scroll of semi-permanent affliction — randomly applies freeze/bleed/burn/flinch/sleep. Each round has a 40% chance to re-apply that same status. Re-use to swap the status. (4 turn cooldown)"},
    "artisan":              {"name": "⚗️ Artisan",               "cost": 40, "description": "Craft a weapon of moderate destruction from the materials around you — Bomb (50 damage), Sword (40 damage), or Rifle (35 damage + bleed). Each has a 33% chance. (2 turn cooldown)"},
    "calamity_priest":      {"name": "☄️ Calamity Priest",       "cost": 20, "description": "Foresee calamity and share the damage — 50%: split 50/50 | 40%: take 40, deal 60 | 5%: take 30, deal 70 | 5%: take 20, deal 80. (3 turn cooldown)"},
    "potions_professor":    {"name": "⚗️ Potions Professor",     "cost": 30, "description": "Brew a cure or poison — Cure: restore 40 HP + 15 SP (or remove 1 status + 10 HP + 5 SP if afflicted). Poison: deal 50 damage + drain 20 SP from opponent. (3 turn cooldown)"},
    "biologist":            {"name": "🧬 Biologist",             "cost": 40, "description": "Crossbreed effects for a random result — double status (paralysis+bleed), or summon a creature for 67 damage. (3 turn cooldown)"},
    "zombie":               {"name": "🧟 Zombie",                "cost": 30, "description": "Summon the recently deceased — 1–4 zombies (60%) or 5–7 zombies (40%), each dealing 10 damage. 10% chance to flinch. (3 turn cooldown)"},
    "devil_form":           {"name": "😈 Devil Form",            "cost": 30, "description": "Transform into a devil — +50% strength, SP and HP. 30% chance to lose your own turn to madness. Maintenance mode. (4 turn cooldown after cancel)"},
    "distortion":           {"name": "👑 Distortion",            "cost": 30, "description": "Distort the enemy's next attack — 100% dodge. 40% chance to reflect 60% of the damage back. If attack ≥60% of your base HP, only dodges (no reflect). (3 turn cooldown)"},
    "judge":                {"name": "⚖️ Judge",                 "cost": 30, "description": "Deliver a random verdict — Imprisonment (3 turns trapped, −25 SP, 7cd) | Flogging (15% current HP dmg, 2cd) | Death (40%: 60-90 dmg | 30%: 50% HP | 30%: miss)."},
    # Seq 5
    "spirit_thread":        {"name": "🧵 Spirit Thread Control",  "cost": 70, "description": "Seize opponent's spirit body threads — 10% stun chance per turn (+10%/round). Disrupted by critical hit or special attack. 5 turn cooldown."},
    "marionette_summon":    {"name": "🎭 Sacrifice Marionette",   "cost": 60, "description": "Sacrifice a beyonder ability to summon a marionette — 50% it attacks each round, 50% it intercepts an attack for you. Lasts 3 turns. 3 turn cooldown."},
    "mental_theft":         {"name": "🧠 Mental Theft",           "cost": 50, "description": "Remove the thought of attack from opponent's mind — they lose their turn. 3 turn cooldown."},
    "rewards_theft":        {"name": "🏆 Rewards Theft",          "cost": 60, "description": "Steal an active buff from your opponent for yourself. 4 turn cooldown."},
    "blink":                {"name": "⚡ Blink",                  "cost": 35, "description": "Escape all stuns + 3-turn stun immunity, then surprise attack for 80 dmg. 6 turn cooldown."},
    "travellers_door":      {"name": "🚪 Traveller's Door",       "cost": 80, "description": "Banish opponent to a remote location (10% to deal 20 dmg) and fully regenerate all resources. 2 uses per match. Can escape battle if uses remain. 6 turn cooldown."},
    "dream_visitation":     {"name": "🌙 Dream Visitation",       "cost": 50, "description": "Enter opponent's dreams — 50% chance to force them to flee or drain 40% of their SP. 4 turn cooldown."},
    "dream_alteration":     {"name": "💭 Dream Alteration",       "cost": 40, "description": "Influence opponent's thoughts — lower their attack accuracy by 5% each round for the rest of battle. 1 use only."},
    "purification_halo":    {"name": "☀️ Purification Halo",      "cost": 65, "description": "Massive sun halo effect — boosts SP of non-death/criminal/chained allies for 3 rounds; deals 50/100/150 dmg + 5-round burn to those pathways respectively. 5 turn cooldown."},
    "light_of_holiness":    {"name": "✨ Light of Holiness",      "cost": 90, "description": "Giant ray of holy light — 70 dmg, skip 1 turn + 2-round burn. Chained pathway takes double damage. Revokes conditional criminal/death curses. 4 turn cooldown."},
    "singing":              {"name": "🎵 Singing",                "cost": 80, "description": "Ocean Songster's voice — 33% each: Spirit Interference (stun 2 turns, −30 SP), Vocal Enlightenment (+15% spirit/+20% strength for 3 turns), or Sound-Wave Explosion (40 dmg + 1 turn stun). 3 turn cooldown."},
    "arrow_of_lightning":   {"name": "⚡ Arrow of Lightning",     "cost": 65, "description": "Charge 1 turn then unleash a lightning arrow for 90 damage. 5 turn cooldown."},
    "grazing":              {"name": "🌾 Grazing",                "cost": 40, "description": "Copy 3 abilities from opponent's pathway at the start of battle. Copies last 3 matches (max 5 total). 1 use per round."},
    "ability_bag":          {"name": "🎒 Ability Bag",            "cost": 0,  "description": "Activate a grazed ability — costs the same SP as the original, same cooldowns apply."},
    "combination_spell":    {"name": "🔮 Combination Spell",      "cost": 0,  "display_cost": "varies", "description": "Fuse ability pairs for powerful combo spells: Lightning Curse, Spirit Seal, Greater Rejuvenation, or Cursed Betrayal. Activates automatically when correct ability sequence is used."},
    "spiritual_suppression":{"name": "🦷 Spiritual Suppression",  "cost": 45, "description": "Use spirits locked in your teeth — negate any ability costing more than 50 SP for 2 turns. 2 total uses with 4 turn cooldown."},
    "spiritual_takeover":   {"name": "👻 Spiritual Takeover",     "cost": 50, "description": "Plant a spirit trap — on trigger, opponent takes 15 dmg/round until battle ends. Negated by purification halo/twilight armour. 1 use only."},
    "dragged_to_hell":      {"name": "🔥 Dragged to Hell",        "cost": 50, "description": "Rip open the gateway in your glabella — deal 70 damage and drain 15 SP from yourself each round for the rest of the match. 1 use only."},
    "evil_sealing":         {"name": "💀 Evil Sealing",           "cost": 40, "display_cost": "40 SP/round", "description": "Compress a wraith into a spiritual bullet — gain a random debuff but charge up +30 dmg/round with 10 recoil. Activate again to fire. Deactivates for rest of match after firing. "},
    "protection":           {"name": "🛡️ Protection",            "cost": 40, "description": "Give up offense — deal 50% less damage while active. Damage taken is reduced by 80 (drops to 10 for 1 turn after any attack). Choose: Active (until next attack) or Maintenance (toggle, 3 turn cooldown on cancel)."},
    "dawn_armor":           {"name": "🌅 Dawn Armor",             "cost": 30, "description": "Don radiant armor — +20% max HP and +10% strength for the duration. Cannot be cancelled once cast (like Pugilist). 3 turn cooldown if dispelled."},
    "disease_propagation":  {"name": "🦠 Disease Propagation",    "cost": 50, "description": "Unleash a plague — 15 HP damage + 1% extra dmg per round, grows stronger until battle ends. 1 use only. Stacks with other effects."},
    "thread_storm":         {"name": "🕸️ Thread Storm",           "cost": 40, "description": "Wrap opponent in steel-like threads — skip 1-2 of their turns + 2-round bleed per turn skipped. 4 turn cooldown."},
    "cull":                 {"name": "☠️ Cull",                   "cost": 70, "description": "Strike any part of the body as a critical weak point — bonus damage on next attack (scales with sequence). 4 turn cooldown, 3 total uses."},
    "weakness_development": {"name": "🎯 Weakness Development",   "cost": 50, "description": "Halve the power of one opponent skill OR place a 20% damage debuff on them. 2 total uses with 3 turn cooldown."},
    "stellar_self":         {"name": "⭐ Stellar Self",           "cost": 0,  "description": "Form a substitute fused with your essence — for the next 2 turns, you cannot be hit by beyonders of your sequence or lower."},
    "star_pillar":          {"name": "💫 Star Pillar",            "cost": 40, "description": "Channel magnificent starlight into a disintegrating pillar — deal 45 damage. 3 turn cooldown."},
    "fire_storm":           {"name": "☄️ Fire Storm",             "cost": 0,  "display_cost": "5 turn cd", "description": "Redirect a falling meteor — 40% chance for 50 damage, 60% chance for 30 damage. 5 turn cooldown."},
    "star_of_curses":       {"name": "🌟 Star of Curses",         "cost": 50, "description": "Deal 5 damage then curse opponent for 5 rounds — each round applies one of: Slow (−30% accuracy), Weakness (−30% damage), Mutation (30 dmg), or Trauma (flinch). 1 use only."},
    "curse_of_misfortune":  {"name": "🎲 Curse of Misfortune",    "cost": 50, "description": "Transfer misfortune via a standard hit — for 5 rounds their accuracy is 10–40% with 20 recoil on each of their attacks. 5 turn cooldown."},
    "active_luck_boost":    {"name": "🍀 Active Luck Boost",      "cost": 70, "description": "Focus all gathered luck into 2 rounds — 90% chance to dodge attacks, best probability outcomes, buffs +1 duration, and 10% chance events deal 40 dmg to opponent. 1 use only."},
    "artificial_moon":      {"name": "🌕 Artificial Moon",        "cost": 50, "description": "Channel the red moon — +50% SP and +40% beyonder ability damage for 5 rounds. 1 use only."},
    "scarlet_transformation":{"name": "🌙 Scarlet Transformation","cost": 50, "description": "Transform into moonlight — all physical and special attacks are completely useless against you for 4 turns. 7 turn cooldown."},
    "spirit_animal_transformation":{"name": "🐻 Spirit Animal Transformation","cost": 35,"display_cost":"35 SP activate, 5 SP/round","description": "Transform into a giant bear — +40% HP and +50% physical strength. Maintained each round for 5 SP. 10 turn cooldown or large SP cost to restart."},
    "wrath_of_nature":      {"name": "🌿 Wrath of Nature",        "cost": 0,  "display_cost": "5 turn cd", "description": "Command the forest — 30% swamp trap (3 turns), 30% wooden coat (+20 HP + thorn recoil 5 dmg/hit), 40% poison vine (4 turns, 15 dmg/turn). 5 turn cooldown."},
    "possession":           {"name": "👁️ Possession",             "cost": 60, "description": "Enter opponent's body as a wraith — 20 dmg/turn for 3-5 turns. Sun pathway Seq 5+ is fully immune. 6 turn cooldown."},
    "wraith_shriek":        {"name": "👻 Wraith Shriek",          "cost": 50, "description": "Disturbing shriek — strip 40 SP from opponent and apply 5-round bleed. Your own accuracy is −40% for 2 turns after. 4 turn cooldown."},
    "desire_explosion":     {"name": "💥 Desire Explosion",       "cost": 0,  "display_cost": "Requires vile crime", "description": "Tear off a horn to trigger a mental explosion — 50 dmg + stun: 50% 1 turn, 40% 2 turns, 10% 3 turns. 2 uses, 5 turn cooldown. Requires a vile crime first."},
    "desire_symbiosis":     {"name": "😈 Desire Symbiosis",       "cost": 0,  "description": "Choose one emotion — Wrath (+50% attack, −20% accuracy), Lust (100% accuracy, −60% strength), or Envy (+70% SP, −30% strength & accuracy). 1 use only."},
    "prohibition":          {"name": "🚫 Prohibition",            "cost": 65, "description": "Seal one of opponent's beyonder abilities for the rest of the match. Reactivate to reset the seal. 5 turn cooldown, 2 total uses."},
    "punishment":           {"name": "⛓️ Punishment",             "cost": 60, "description": "Activate on target — all physical abilities used against them are boosted 45%, and they become petrified (−20% offensive ability power). 4 turn cooldown."},
    "disciplinary_strike":  {"name": "⚖️ Disciplinary Strike",    "cost": 65, "description": "Channel disciplinary authority — deal 60 damage and apply a 3-turn judgement debuff that reduces opponent's damage by 25% and strips one active buff. 4 turn cooldown."},
    "disorder":             {"name": "🌀 Disorder",            "cost": 35, "description": "–35 SP, 5 CD. 25% accuracy decrease. Random: descend enemy's mind into chaos (7-10% HP self-damage), or skip 2 turns, or −20% strength. 5 turn cooldown."},
    "gift_of_corruption":   {"name": "🎁 Gift of Corruption",  "cost": 35, "description": "–35 SP, 4 CD. Transfer all your negative status effects to the enemy. 10% chance of failure — if it fails, you are stunned 1 turn by a higher power. 4 turn cooldown."},
    "sacred_conviction":    {"name": "🌟 Sacred Conviction",      "cost": 60, "description": "Invoke the unyielding power of order — cleanse all debuffs on yourself, gain a 20% damage boost for 3 turns, and deal 40 damage to the opponent through righteous force. 5 turn cooldown."},

}

# ── Stats (Pathway base + Sequence-unlocked allocation points) ──
# Base combat stats are fixed per Pathway (some pathways run hotter in one
# stat than others). As a Beyonder advances Sequence — Seq 9 down to Seq 0 —
# they unlock a growing pool of stat points, which they spend themselves via
# the dropdown in /stats_user.

COMBAT_STAT_KEYS = ["health", "attack", "luck", "spirituality", "speed"]
DEFAULT_BASE_COMBAT_STATS = {"health": 2, "attack": 2, "luck": 2, "spirituality": 2, "speed": 2}

# Fixed base stats per pathway at Seq 9 — same for every player in that pathway.
# (Spirit has been folded into Spirituality — one stat, no redundancy.)
PATHWAY_BASE_COMBAT_STATS = {
    "Fool":             {"health": 2, "attack": 2, "luck": 2, "spirituality": 5, "speed": 2},
    "Error":            {"health": 2, "attack": 2, "luck": 2, "spirituality": 4, "speed": 3},
    "Door":             {"health": 2, "attack": 2, "luck": 2, "spirituality": 4, "speed": 3},
    "Visionary":        {"health": 2, "attack": 2, "luck": 2, "spirituality": 5, "speed": 2},
    "Sun":              {"health": 2, "attack": 2, "luck": 3, "spirituality": 4, "speed": 2},
    "Tyrant":           {"health": 2, "attack": 3, "luck": 2, "spirituality": 4, "speed": 2},
    "Hanged Man":       {"health": 2, "attack": 2, "luck": 2, "spirituality": 5, "speed": 2},
    "White Tower":      {"health": 2, "attack": 2, "luck": 2, "spirituality": 5, "speed": 2},
    "Darkness":         {"health": 2, "attack": 2, "luck": 2, "spirituality": 4, "speed": 3},
    "Death":            {"health": 3, "attack": 2, "luck": 2, "spirituality": 4, "speed": 2},
    "Twilight Giant":   {"health": 2, "attack": 3, "luck": 2, "spirituality": 4, "speed": 2},
    "Demoness":         {"health": 2, "attack": 2, "luck": 2, "spirituality": 4, "speed": 3},
    "Red Priest":       {"health": 2, "attack": 3, "luck": 2, "spirituality": 4, "speed": 2},
    "Hermit":           {"health": 2, "attack": 2, "luck": 2, "spirituality": 5, "speed": 2},
    "Paragon":          {"health": 2, "attack": 2, "luck": 3, "spirituality": 4, "speed": 2},
    "Wheel of Fortune": {"health": 2, "attack": 2, "luck": 3, "spirituality": 4, "speed": 2},
    "Moon":             {"health": 3, "attack": 2, "luck": 2, "spirituality": 4, "speed": 2},
    "Mother":           {"health": 3, "attack": 2, "luck": 2, "spirituality": 4, "speed": 2},
    "Chained":          {"health": 3, "attack": 2, "luck": 2, "spirituality": 4, "speed": 2},
    "Abyss":            {"health": 2, "attack": 3, "luck": 2, "spirituality": 4, "speed": 2},
    "Black Emperor":    {"health": 2, "attack": 2, "luck": 2, "spirituality": 5, "speed": 2},
    "Justiciar":        {"health": 2, "attack": 3, "luck": 2, "spirituality": 4, "speed": 2},
}

# Cumulative pool of allocatable stat points unlocked at each Sequence tier.
# Seq 9 = 0 (nothing unlocked yet); Seq 0 (Sovereign-adjacent) = full pool.
SEQ_STAT_POINTS = {
    9: 0,
    8: 6,
    7: 14,
    6: 24,
    5: 36,
    4: 52,
    3: 70,
    2: 90,
    1: 115,
    0: 145,
}

# Small random chance for the winner of a fight to earn a bonus stat point
# on top of whatever their Sequence pool already grants — a bit of "gamer"
# unpredictability without making stats purely EXP/win-farmable.
BONUS_STAT_POINT_CHANCE = 0.15  # 15% chance per win

def get_user_role(member: discord.Member):
    if not member:
        return None
    for role in member.roles:
        discord_name = role.name.lower().replace("–", "—").replace("-", "—").strip()
        for role_name in ROLE_MODIFIERS:
            code_name = role_name.lower().replace("–", "—").strip()
            # Full match (original behaviour)
            if code_name == discord_name:
                return role_name
            if code_name in discord_name:
                return role_name
            # Partial match: require both [Pathway] family AND Seq N to match
            try:
                family_part = code_name.split("]")[0] + "]"   # e.g. "[white tower]"
                seq_part = code_name.split("seq ")[1].split(" ")[0]  # e.g. "9"
                if family_part in discord_name and f"seq {seq_part}" in discord_name:
                    return role_name
            except Exception:
                pass
    return None

def get_pathway_family(role_name: str):
    """Extract pathway family name from a role string, e.g. '[Fool] Seq 9 — Seer' -> 'Fool'."""
    match = re.match(r'\[(.+?)\]', role_name)
    return match.group(1) if match else None

def get_all_user_roles(member: discord.Member) -> list:
    """
    Return all pathway roles the member can use.

    Rule: if a user has [Fool] Seq 8, they also unlock [Fool] Seq 9 abilities
    because a higher rank (lower seq number) subsumes all previous ranks.
    So we find the *highest rank* (lowest seq number) per pathway family,
    then return every role in that family with seq >= that number (i.e. all earlier ranks).

    Returns list of role_name strings sorted by family then seq ascending (9 first, then 8, 7...).
    """
    if not member:
        return []

    # Step 1: find which roles the member actually has
    owned = []
    for role in member.roles:
        discord_name = role.name.lower().replace("–", "—").replace("-", "—").strip()
        for role_name in ROLE_MODIFIERS:
            if role_name in owned:
                continue
            code_name = role_name.lower().replace("–", "—").strip()
            matched = code_name == discord_name or code_name in discord_name
            if not matched:
                try:
                    family_part = code_name.split("]")[0] + "]"
                    seq_part = code_name.split("seq ")[1].split(" ")[0]
                    matched = family_part in discord_name and f"seq {seq_part}" in discord_name
                except Exception:
                    pass
            if matched:
                owned.append(role_name)

    if not owned:
        return []

    # Step 2: per family, find the highest rank (lowest seq number) the member holds
    family_min_seq = {}  # family -> lowest seq number owned
    for role_name in owned:
        family = get_pathway_family(role_name)
        seq = get_seq_number(role_name)
        if family:
            if family not in family_min_seq or seq < family_min_seq[family]:
                family_min_seq[family] = seq

    # Step 3: collect every role in ROLE_MODIFIERS whose family matches
    # and whose seq >= min_seq (i.e. Seq 9, 8, 7... down to min_seq)
    unlocked = []
    for role_name in ROLE_MODIFIERS:
        family = get_pathway_family(role_name)
        seq = get_seq_number(role_name)
        if family and family in family_min_seq and seq >= family_min_seq[family]:
            if role_name not in unlocked:
                unlocked.append(role_name)

    # Step 4: sort by family name, then by seq descending (9 first, then 8, 7...)
    unlocked.sort(key=lambda r: (get_pathway_family(r) or "", get_seq_number(r)), reverse=False)
    # Actually sort seq descending within a family (show Seq 9 before Seq 8)
    unlocked.sort(key=lambda r: (get_pathway_family(r) or "", -get_seq_number(r)))
    return unlocked

async def get_all_roles_from_db_and_discord(member: discord.Member, guild_id: int) -> list:
    """
    Return every pathway role the fighter can use, combining:
      • Discord roles the member currently has (via get_all_user_roles)
      • The pathway + sequence stored in lotm_beyonders.db for this guild

    This allows ability access even when a Discord role is missing/unassigned,
    and supports members with multiple pathways simultaneously.
    """
    # Start with whatever Discord roles give us
    from_discord = get_all_user_roles(member)
    owned_role_names = set(from_discord)

    # Pull DB record
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute(
                "SELECT pathway, sequence FROM users WHERE user_id = ? AND guild_id = ?",
                (member.id, guild_id)
            ) as cursor:
                row = await cursor.fetchone()
    except Exception:
        row = None

    if row and row[0]:
        db_pathway, db_seq = row[0], row[1]
        # Add every role in this pathway from db_seq down to Seq 9 (all unlocked tiers)
        for role_name in ROLE_MODIFIERS:
            family = get_pathway_family(role_name)
            seq = get_seq_number(role_name)
            if family == db_pathway and seq >= db_seq and role_name not in owned_role_names:
                owned_role_names.add(role_name)

    # Re-sort: by family name then seq descending (Seq 9 first within a family)
    result = list(owned_role_names)
    result.sort(key=lambda r: (get_pathway_family(r) or "", -get_seq_number(r)))
    return result


def get_user_role_modifier(member: discord.Member):
    role = get_user_role(member)
    return ROLE_MODIFIERS.get(role) if role else None

def get_role_ability_key(member: discord.Member):
    role = get_user_role(member)
    return ROLE_ABILITIES.get(role) if role else None

def get_ability_info(ability_key):
    if ability_key in ABILITIES:
        return ABILITIES[ability_key], False
    if ability_key in ROLE_ABILITY_INFO:
        return ROLE_ABILITY_INFO[ability_key], True
    return None, False

def get_scaled_description(ability_key: str, fighter) -> str:
    """Return ability description with flat damage values scaled to the fighter's sequence."""
    info, _ = get_ability_info(ability_key)
    if not info:
        return ""
    desc = info.get("description", "")
    if fighter is None:
        return desc
    replacements = [
        ("100 damage", f"{seq_val(fighter, 100)} damage"),
        ("100 dmg",    f"{seq_val(fighter, 100)} dmg"),
        ("90 damage",  f"{seq_val(fighter, 90)} damage"),
        ("80 damage",  f"{seq_val(fighter, 80)} damage"),
        ("80 dmg",     f"{seq_val(fighter, 80)} dmg"),
        ("70 damage",  f"{seq_val(fighter, 70)} damage"),
        ("70 dmg",     f"{seq_val(fighter, 70)} dmg"),
        ("60 damage",  f"{seq_val(fighter, 60)} damage"),
        ("60 dmg",     f"{seq_val(fighter, 60)} dmg"),
        ("50 damage",  f"{seq_val(fighter, 50)} damage"),
        ("50 dmg",     f"{seq_val(fighter, 50)} dmg"),
        ("45 damage",  f"{seq_val(fighter, 45)} damage"),
        ("40 damage",  f"{seq_val(fighter, 40)} damage"),
        ("40 dmg",     f"{seq_val(fighter, 40)} dmg"),
        ("30 damage",  f"{seq_val(fighter, 30)} damage"),
        ("30 dmg",     f"{seq_val(fighter, 30)} dmg"),
        ("20 damage",  f"{seq_val(fighter, 20)} damage"),
        ("15 damage",  f"{seq_val(fighter, 15)} damage"),
        ("15 HP",      f"{seq_val(fighter, 15)} HP"),
        ("20 HP",      f"{seq_val(fighter, 20)} HP"),
        ("10 damage",  f"{seq_val(fighter, 10)} damage"),
        ("5 damage",   f"{seq_val(fighter, 5)} damage"),
        ("+50 bonus",  f"+{seq_val(fighter, 50)} bonus"),
        ("+100 bonus", f"+{seq_val(fighter, 100)} bonus"),
    ]
    for pattern, replacement in replacements:
        desc = desc.replace(pattern, replacement, 1)
    return desc

def roll_stats(member: discord.Member = None):
    """Return pathway-fixed base stats — same for everyone in that pathway at Seq 9."""
    if member:
        role = get_user_role(member)
        pathway = get_pathway_name(role) if role else None
        if pathway and pathway in PATHWAY_BASE_COMBAT_STATS:
            return dict(PATHWAY_BASE_COMBAT_STATS[pathway])
    return dict(DEFAULT_BASE_COMBAT_STATS)

def get_or_create_stats(user_id, member: discord.Member = None):
    """Fetch (or initialize) a player's stat record.
    Record shape: {health, attack, luck, spirituality, speed, points, points_granted_total}
    `points` is the player's unspent allocation pool; it grows automatically
    as their Sequence advances (Seq 9 -> Seq 0), tracked via points_granted_total
    so we only ever grant the *new* points unlocked since we last checked.
    """
    role = get_user_role(member) if member else None
    seq = get_seq_number(role) if role else 9
    target_total = SEQ_STAT_POINTS.get(seq, 0)

    if user_id in player_stats:
        stats = player_stats[user_id]
        changed = False
        # Legacy migration: 'iq' and 'spirit' have both been folded into 'spirituality'
        if "iq" in stats:
            stats["spirituality"] = stats.get("spirituality", 2) + stats.pop("iq")
            changed = True
        if "spirit" in stats:
            stats["spirituality"] = stats.get("spirituality", 2) + stats.pop("spirit")
            changed = True
        # Drop fields from the old generic leveling system, if present
        if "level" in stats or "exp" in stats:
            stats.pop("level", None)
            stats.pop("exp", None)
            changed = True
        # Backfill combat stat keys for older records, using pathway base as the floor
        base_pathway_stats = roll_stats(member)
        for key in COMBAT_STAT_KEYS:
            if key not in stats:
                stats[key] = base_pathway_stats[key]
                changed = True
        if "points" not in stats:
            stats["points"] = 0
            changed = True
        if "points_granted_total" not in stats:
            stats["points_granted_total"] = 0
            changed = True
        # Grant any new points unlocked by Sequence advancement since we last saw this player
        if target_total > stats["points_granted_total"]:
            gained = target_total - stats["points_granted_total"]
            stats["points"] += gained
            stats["points_granted_total"] = target_total
            changed = True
        if changed:
            save_stats()
        return stats

    new_stats = roll_stats(member)
    new_stats["points"] = target_total
    new_stats["points_granted_total"] = target_total
    player_stats[user_id] = new_stats
    save_stats()
    return player_stats[user_id]

def get_effective_stats(user_id, member: discord.Member = None):
    """Returns the player's current combat stats (health/attack/luck/
    spirituality/speed) — Pathway base plus whatever they've
    manually allocated from their Sequence-unlocked point pool."""
    record = get_or_create_stats(user_id, member)
    return {k: record[k] for k in COMBAT_STAT_KEYS}

def stat_bar(value, max_value=7):
    filled = round((value / max(1, max_value)) * 8)
    filled = max(0, min(8, filled))
    return "▰" * filled + "▱" * (8 - filled)

INFO_PAGES = [
    # Page 1 — Overview + Combat
    discord.Embed(
        title="𓂃 Lord Mysteries — Bot Guide  (1/5)",
        description=(
            "Welcome to the server, Beyonder. Battle, advance your Sequence, "
            "build wealth, and gamble your soli in the world of Lord of the Mysteries."
        ),
        color=0x9B59B6
    ).add_field(
        name="⚔️ Duelling",
        value=(
            "`/fight @user` — Challenge someone. They have **60s** to accept.\n"
            "Highest **Speed** goes first. Turns alternate until one side falls.\n\n"
            "**Actions each turn:**\n"
            "• **Hit** — Physical strike. Scales with Attack, restores a little SP.\n"
            "• **Special** — Spirit-enhanced bullet. Hits harder, costs SP.\n"
            "• **Beyonder Ability** — Your Sequence's unique power.\n"
            "• **Ability** — A purchased power from `/shop`.\n"
            "• **Flee** — Attempt to escape. Not always successful."
        ),
        inline=False
    ).add_field(
        name="❤️ HP & ✨ SP",
        value=(
            "**HP** — Your health. Reach 0 and you lose.\n"
            "**SP** (Spirit Points) — Spent on abilities, regenerates each turn.\n"
            "Base HP & SP scale with your **Pathway** and **Sequence**."
        ),
        inline=False
    ).add_field(
        name="🚪 Forfeiting & Records",
        value=(
            "`/leave` — Forfeit at any time.\n"
            "`/winstreak [@user]` — Check your battle record, win rate, and active streak."
        ),
        inline=False
    ).set_footer(text="◀ Prev  ·  𓂃  ·  Page 1 of 5  |  Next ▶"),

    # Page 2 — Stats + Abilities
    discord.Embed(
        title="𓂃 Lord Mysteries — Bot Guide  (2/5)",
        description="Everything about your Beyonder stats and abilities.",
        color=0x9B59B6
    ).add_field(
        name="ᯓ★ Combat Stats",
        value=(
            "Five stats shape every Beyonder:\n"
            "❤️ **Health** — Increases your max HP.\n"
            "🗡️ **Attack** — Scales your Hit and Special damage.\n"
            "🍀 **Luck** — Favour with probability-based attacks.\n"
            "✨ **Spirituality** — Increases max SP, boosts Special damage, and resists debuffs/status effects.\n"
            "⚡ **Speed** — Who goes first each round.\n\n"
            "Base stats come from your **Pathway**; bonus points unlock as you advance **Sequence**. "
            "Use `/stats_user` to view and allocate yours."
        ),
        inline=False
    ).add_field(
        name="🔮 Beyonder Abilities",
        value=(
            "Each Pathway grants abilities per Sequence rank:\n"
            "• **Seq 9** — First ability unlocked.\n"
            "• **Seq 8** — Second unlocked. Seq 9 ability still accessible.\n"
            "• **Seq 7** — Third unlocked, and so on upward.\n"
            "Use `/pathway_ability` to browse every Pathway's full list."
        ),
        inline=False
    ).add_field(
        name="🏪 Shop Abilities & Chairs",
        value=(
            "Visit `/shop` to open the unified shop with two categories:\n"
            "✨ **Spell Shop** — Buy extra combat abilities with soli. Available in any fight."
        ),
        inline=False
    ).set_footer(text="◀ Prev  ·  𓂃  ·  Page 2 of 5  |  Next ▶"),

    # Page 3 — Economy + Gambling
    discord.Embed(
        title="𓂃 Lord Mysteries — Bot Guide  (3/5)",
        description="Soli is the lifeblood of the realm. Earn it, spend it, lose it.",
        color=0x9B59B6
    ).add_field(
        name="💰 Economy — Soli",
        value=(
            "You start with **1,000 soli**. Earn more through battles, dailies, and gambling.\n\n"
            "`/daily` — **150 XP** + **1,000 soli** every 24h. Streak adds **+20 soli/day**.\n"
            "`/beg` — **67 or 69 soli** every 2 hours.\n"
            "`/balance` — Check your soli (or anyone else's).\n"
            "`/share @user <amount>` — Send soli to another Beyonder.\n"
            "`/money` — Admin-only give/take/wipe."
        ),
        inline=False
    ).add_field(
        name="🎰 The Shameless Corner — Gambling",
        value=(
            "The fastest path to riches — or ruin.\n\n"
            "`/coinflip <amount>` — Heads or tails. 50/50 to double up.\n"
            "`/slots <amount>` — Spin the reels for multiplied payouts.\n"
            "`/blackjack <amount>` — Beat the dealer to **21** for **2.5×**.\n\n"
            "All gambling has a **5-second cooldown** between uses."
        ),
        inline=False
    ).add_field(
        name="🏆 Betting on Duels",
        value=(
            "`/bet` — Wager on an active fight within the **first 3 turns**.\n"
            "Pick the winner correctly → receive **double** your bet back."
        ),
        inline=False
    ).set_footer(text="◀ Prev  ·  𓂃  ·  Page 3 of 5  |  Next ▶"),

    # Page 4 — Items
    discord.Embed(
        title="𓂃 Lord Mysteries — Bot Guide  (4/5)",
        description="Items — the material rewards of a Beyonder's journey.",
        color=0x9B59B6
    ).add_field(
        name="🎒 Items",
        value=(
            "Items drop from events and battles.\n\n"
            "`/item` — View your inventory and use items you own.\n"
            "Admins can grant items with `/give_item`."
        ),
        inline=False
    ).set_footer(text="◀ Prev  ·  𓂃  ·  Page 4 of 5  |  Next ▶"),

    # Page 5 — Server Layout + Battle Rewards
    discord.Embed(
        title="𓂃 Lord Mysteries — Bot Guide  (5/5)",
        description="Server layout and how the world rewards victorious Beyonders.",
        color=0x9B59B6
    ).add_field(
        name="📡 Server Sections",
        value=(
            "🏟️ **Battlefields** — Duel channels that keep main chat clean.\n"
            "💡 **Suggestions** — Submit ideas for server or bot improvements.\n"
            "📈 **Advancement Feed** — Notifies when anyone advances their Sequence.\n"
            "📜 **Pathway Abilities** — Updated weekly with new Sequence abilities.\n"
            "💬 **Bot Channel** — Main hub for commands, chat, and post-duel rage."
        ),
        inline=False
    ).add_field(
        name="💸 Battle Rewards",
        value=(
            "Win a duel and earn soli based on your opponent's Sequence:\n"
            "Seq 9 → **1,000** | Seq 8 → **2,000** | Seq 7 → **3,000**\n"
            "Seq 6 → **4,000** | Seq 5 → **5,000**\n\n"
            "Winners also gain **XP** based on the opponent's Sequence rank."
        ),
        inline=False
    ).add_field(
        name="🔑 Quick Reference",
        value=(
            "`/profile` · `/leaderboard` · `/pathway_ability` · `/stats_user`\n"
            "`/shop` · `/item` · `/winstreak` · `/daily` · `/pray`"
        ),
        inline=False
    ).set_footer(text="◀ Prev  ·  𓂃  ·  Page 5 of 5  |  Use /pathway_ability • /shop • /stats_user"),
]

class InfoView(discord.ui.View):
    def __init__(self, page: int = 0):
        super().__init__(timeout=120)
        self.page = page
        self._update_buttons()

    async def on_error(self, interaction: discord.Interaction, error: Exception, item) -> None:
        await _view_on_error(interaction, error, item)

    def _update_buttons(self):
        self.prev_btn.disabled = self.page == 0
        self.next_btn.disabled = self.page == len(INFO_PAGES) - 1

    @discord.ui.button(label="◀ Prev", style=discord.ButtonStyle.secondary)
    async def prev_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page = max(0, self.page - 1)
        self._update_buttons()
        await interaction.response.edit_message(embed=INFO_PAGES[self.page], view=self)

    @discord.ui.button(label="Next ▶", style=discord.ButtonStyle.secondary)
    async def next_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page = min(len(INFO_PAGES) - 1, self.page + 1)
        self._update_buttons()
        await interaction.response.edit_message(embed=INFO_PAGES[self.page], view=self)

def info_embed() -> discord.Embed:
    return INFO_PAGES[0]

# ── Fighter ───────────────────────────────────────────────

class Fighter:
    def __init__(self, member: discord.Member):
        self.user = member
        self.stats = get_effective_stats(member.id, member)
        self.role = get_user_role(member)
        self.all_roles = get_all_user_roles(member)  # all pathway roles (Seq 9, Seq 8, etc.)
        self.role_ability = get_role_ability_key(member)
        self.has_spell_defence = self.role in SPELL_DEFENCE_ROLES

        # Sequence-based base stats — use pathway-specific values if available
        seq = get_seq_number(self.role) if self.role else 9
        pathway = get_pathway_name(self.role) if self.role else ""
        base = PATHWAY_BASE_STATS.get((pathway, seq)) or SEQ_BASE_STATS.get(seq, SEQ_BASE_STATS[9])
        self.base_hp = base["hp"]
        self.max_hp = base["hp"] + self.stats["health"] * 6
        self.hp = self.max_hp
        self.max_sp = base["sp"] + self.stats["spirituality"] * 2
        self.sp = self.max_sp

        # Status effects
        self.bleed = 0
        self.paralysis = 0
        self.slumber = 0
        self.freeze = 0
        self.freeze_weakened = False
        self.burn = 0
        self.defence_reduction = 0.0
        self.status_immune = 0      # turns of immunity (Doctor treatment)
        self.supernatural_resist_active = False  # Pugilist: permanent immunity, 40 self-dmg per attempt
        self.stun_block = False     # Archaeologist stun shield

        # Buffs and states
        self.strength_boost = 0
        self.divination_dodge = False
        self._seer_dodge_chance = None
        self.divination_dodge_turns = 0        # how many turns divination dodge lasts
        self._pending_divination_msg = ""      # set by apply_damage when divination dodges
        self.seer_divination_cooldown = 0      # 3 turn cooldown
        self.hurricane_interrupt_cd = 0        # 5 turn CD when hurricane is interrupted by stun
        self.blessed = False
        self.night_boost_active = False
        self.night_boost_used = False
        self.night_boost_damage = 0
        self.invigorated = False
        self.arbitration_boost = 0
        self.arbitration_cooldown = 0
        self.testimony_cooldown = 0
        self.scribe_copy_pending = False   # True when ability is being fired as a scribe copy
        self.prayer_active = False
        self.phasing_active = False
        self.phasing_cooldown = 0
        self.study_skip = False
        self.planting_turns = 0
        self.planting_cooldown = 0
        self.spectate_active = False
        self.spectate_cooldown = 0
        self.memorise_ability = None
        self.memorise_turns = 0
        self.desecration_cooldown = 0
        self.balancing_cooldown = 0
        self.balancing_act_evasion_turns = 0
        self.balancing_act_status_immune = False
        self.pickpocket_cooldown = 0
        self.vital_strike_cooldown = 0
        self.card_tricks_cooldown = 0
        self.song_cooldown = 0
        self.damage_boost_pct = 0.0
        # Glance into Fate (Monster Seq 9) — fate curse on opponent
        self.fate_cursed_turns = 0
        self.fate_cursed_backfire = False
        # Fighting Spirit (Pugilist Seq 8) — attack boost
        self.fighting_spirit_active = False
        self.fighting_spirit_turns = 0
        self.fighting_spirit_boost = 0.0
        self.fighting_spirit_cooldown = 0
        # Computing (Robot Seq 8) — duration limit
        self.computing_turns = 0
        self.computing_cooldown = 0
        # Grazing (Shepherd Seq 5)
        self.grazed_abilities = []
        self.grazing_used = False
        self.grazing_cooldown = 0
        self.role_ability_disabled = 0      # turns remaining where role ability is suppressed
        self.debuff_stacks = 0          # each stack = 20% less damage dealt
        self.damage_vulnerability = 0   # Weakness Development: 20% more damage taken
        self.debuff_cooldown = 0        # 1 turn cooldown between stacks
        self.debuff_turns = 0           # turns remaining on debuff effect
        self.debuff_used = False        # one-time use per battle
        self.debuff_applied = False     # tracks if debuff was ever applied this battle

        # Seq 8 states
        self.combat_studies_active = False  # 15% attack boost (permanent)
        self.combat_studies_skip = False    # skip next turn to study
        self.swindling_uses = 0             # failure stacks
        self.provoked_block = None          # action key blocked by provocation
        self.instigated_action = None       # action key forced by instigation
        self.provocation_cooldown = 0
        self.provocation_trapped = False    # True when trapped by Provocation (not vine bomb)
        self.trap_slots = []                # list of pending trap types
        self.trap_set_turn = 0             # turn traps were laid
        self.trap_cooldown = 0             # 10 turn cooldown
        self.dog_chase_turns = 0           # dog chase turns remaining
        self._dog_chase_dmg_pct = 0.10     # damage % per turn (default 10%, trap sets 5-7%)
        self._trip_trap_miss = False       # next attack misses from trip trap
        self.last_attack_dmg = 0           # track for tripwire backlash
        self.abilities_used_this_battle = set()  # track for Prometheus
        self.moral_freedom_used = False
        self.moral_freedom_active = False   # 15 dmg/turn to opponent
        self.rage_baited_turns = 0
        self.rage_baited_cooldown = 0
        self.poem_cooldown = 0
        self.burial_cooldown = 0
        self.crime_cooldown = 0
        self.magic_trick_cooldown = 0
        self.brilliant_light_cooldown = 0
        self.listening_active = False
        self.folk_of_rage_turns = 0
        self.folk_of_rage_pct = 0.0
        self.folk_of_rage_active = False
        self.ritualistic_reasoning_used = False  # sabotage opponent next move
        self.ritualistic_reasoning_cooldown = 0
        self.sabotaged = False              # this fighter's next move is sabotaged
        self.reading_prediction = None     # predicted action key
        self.animal_companion_active = False
        self.animal_companion_turn = 0     # attacks every other attacker turn
        self.computing_active = False
        self.forceful_cooldown = 0
        self.domination_cooldown = 0
        self.domination_active = False
        self.domination_turns = 0
        self.bribe_cooldown = 0
        self.bribe_pending = None
        self.bribe_concealed_turn = False
        self.connect_share_turns = 0
        self.bribe_weakened = False        # next attack deals 40% less
        self.charm_turns = 0              # can't attack for N turns
        self.danger_intuition_active = False  # toggle on/off
        self.intuition_upkeep_sp = 20      # SP drained per turn while active
        self.gun_shot_used = False
        self.gun_shot_cooldown = 0
        self.gun_shot_bleed = False        # permanent bleed
        self.witch_burn = False            # witch's black flames: 10 dmg instead of 7

        # Seq 7 states
        self.paper_figurine_turns = 0    # attacks against attacker fail for N turns
        self.astrology_active = False    # -30% miss chance permanently
        self.astrology_used = False      # one-time use flag
        self.astrology_flee_ready = False  # waiting for flee confirmation
        self.fire_ravens_cooldown = 0
        self.scribe_pending = False      # waiting to record ability used against us
        self.scribe_record_in = 0        # turns until recording fires
        self.observation_active = False  # this fighter takes 40% less damage next turn
        self.observation_cooldown = 0
        self.therapy_cooldown = 0
        self.holy_water_regen = 0        # turns of 5 HP/turn regen
        self.water_bullet_cooldown = 0
        self.sneak_attack_cooldown = 0
        self.sneak_attack_pending = False # opponent loses next turn
        self.analyse_uses = 2            # Detective: 2 uses remaining
        self.analysed_skills = {}        # skill_key -> 0.30 damage reduction
        self.sleep_spell_cooldown = 0
        self.summoning_dead_cooldown = 0
        self.weapon_throw_cooldown = 0
        self.witch_curse_cooldown = 0
        self.appraisal_active = False    # 40% boost on next action
        self.appraisal_cooldown = 0
        self.lucky_day_used = False
        self.lucky_day_turns = 0         # defender's hit chance halved for N rounds
        self.blood_sucker_cooldown = 0
        self.vine_bomb_cooldown = 0
        self.trapped = 0                 # vine bomb: turns trapped (can't act at all)
        self.transformation_used = False
        self.transformed = False         # werewolf form active
        self.transform_regen = 0
        self.transform_turns = 0         # counts turns active, reverts at 5
        self.transform_hp_bonus = 0      # extra HP added so we can remove it on revert
        self.transform_dodge_reduction = 0.25  # incoming hit chance starts at 25% (nerfed)
        self.vile_crime_used = False
        self.vile_crime_count = 0        # hit counter (needs 3)
        self.illegal_trade_cooldown = 0
        self.mental_piercing_cooldown = 0
        self.magic_spell_cooldown = 0

        # Seq 6 states
        self.faceless_cooldown = 0
        self.prometheus_cooldown = 0
        self.prometheus_stolen_ability = None   # ability key stolen from opponent
        self.scribe_cooldown = 0
        self.scribe_copied_ability = None       # ability key copied from last hit
        self.scribe_active = False              # copy ready to fire next turn
        self.hypnotist_cooldown = 0
        self.notary_cooldown = 0
        self.notary_buff_turns = 0             # turns of +50% dmg on self
        self.notary_debuff_turns = 0           # turns of -50% dmg on opponent
        self.wind_blessed_buildup = 0          # 0=idle, 1=first round, 2=second round
        self.wind_blessed_cooldown = 0
        self.rose_bishop_cooldown = 0
        self.polymath_cooldown = 0
        self.polymath_ability = None           # ability key currently studied
        self.polymath_pending = False          # True during the turn the studied ability is used (70% potency)
        self.soul_assurer_cooldown = 0
        self.spirit_guide_active = False
        self.spirit_guide_turns = 0   # counts turns active, max 5
        self.spirit_guide_cooldown = 0
        self.glance_fate_cooldown = 0
        self.dawn_paladin_active = False
        self.dawn_paladin_cooldown = 0
        self.dawn_paladin_hp_bonus = 0         # extra HP added by armour
        self.hurricane_charging = False        # turn 1 of charge
        self.hurricane_cooldown = 0
        self.hurricane_uses = 0                # max 3 uses
        self.pleasure_witch_cooldown = 0
        self.charm_turns = 0                   # opponent is charmed for N turns
        self.charm_chance = 0.0                # current chance of maintaining charm
        self.conspirer_cooldown = 0
        self.conspiracy_used = False          # one use per match
        self.conspiracy_miss_turns = 0        # 70% miss debuff turns remaining
        self.conspiracy_force_turns = 0       # force random ability turns remaining
        self.conspiracy_dmg_turns = 0         # 40dmg+60sp drain turns remaining
        self.scrolls_professor_cooldown = 0
        self.scroll_status = None              # active semi-permanent status key
        self.artisan_cooldown = 0
        self.artisan_item = None               # ability key the item replicates
        self.artisan_item_cooldown = 0
        self.calamity_priest_cooldown = 0
        self.potions_professor_cooldown = 0
        self.biologist_cooldown = 0
        self.zombie_cooldown = 0
        self.devil_form_active = False
        self.devil_form_cooldown = 0
        self.devil_form_hp_bonus = 0
        self.devil_form_sp_bonus = 0
        self.distortion_cooldown = 0
        self.distortion_active = False
        self.judge_cooldown = 0
        self.judge_restricted_move = None
        self.judge_move_pool = []
        self.jurisdiction_cooldown = 0
        self.jurisdiction_active = False
        self.jurisdiction_turns = 0
        self.jurisdiction_dmg_boost = 0.0
        self.arbiter_cooldown = 0
        self.arbiter_active = False
        self.arbiter_turns = 0              # turns of ceasefire remaining

        # Seq 5 states
        self.spirit_thread_active = False       # Marionettist: control active
        self.spirit_thread_stun_pct = 0         # current stun chance (10 base, +10/turn)
        self.spirit_thread_turns = 0            # turns elapsed
        self.spirit_thread_cooldown = 0
        self.marionette_active = False          # marionette summon active
        self.marionette_turns = 0
        self.marionette_sacrificed_ability = None
        self.marionette_cooldown = 0
        self.mental_theft_cooldown = 0
        self.rewards_theft_cooldown = 0
        self.blink_cooldown = 0
        self.blink_stun_immune_turns = 0        # turns of stun immunity
        self.travellers_door_uses = 2
        self.travellers_door_cooldown = 0
        self.dream_visitation_cooldown = 0
        self.dream_alteration_used = False
        self.dream_alteration_acc_loss = 0.0    # cumulative accuracy loss
        self.purification_halo_cooldown = 0
        self.purification_halo_sp_boost_turns = 0
        self.light_of_holiness_cooldown = 0
        self.singing_cooldown = 0
        self.singing_enlightened_turns = 0      # vocal enlightenment buff turns
        self.singing_str_boost = 0.0
        self.singing_spirit_boost = 0.0
        self.arrow_charging = False
        self.arrow_cooldown = 0
        self.grazing_abilities = []             # list of grazed ability keys
        self.grazing_cooldown = 0
        self.combination_spell_active = False   # combo spells unlocked
        self.combo_last_ability = None
        self.spiritual_suppression_uses = 2
        self.spiritual_suppression_turns = 0    # turns suppression is active
        self.spiritual_suppression_cooldown = 0
        self.spiritual_takeover_used = False
        self.spiritual_takeover_active = False  # 15 dmg/round to opponent
        self.dragged_to_hell_used = False
        self.dragged_to_hell_active = False     # 15 sp drain per round
        self.evil_sealing_active = False
        self.evil_sealing_damage = 0
        self.evil_sealing_debuff = None
        self.protection_active = False          # Guardian: damage reduction shield
        self.protection_mode = None             # "active" or "maintenance"
        self.protection_weakened_turn = False   # reduction drops to 10 for 1 turn after attacking
        self.protection_cooldown = 0
        self.last_stand_bonus = 0               # bonus damage on next attack (Last Stand ability)
        self.dawn_armor_active = False          # Guardian: HP/strength boost
        self.dawn_armor_hp_bonus = 0
        self.dawn_armor_cooldown = 0
        self.disease_propagation_used = False
        self.disease_propagation_active = False
        self.disease_propagation_turns = 0
        self.thread_storm_cooldown = 0
        self.cull_uses = 3
        self.cull_bonus = 0
        self.cull_cooldown = 0
        self.weakness_development_uses = 2
        self.weakness_development_cooldown = 0
        self.stellar_self_turns = 0
        self.star_pillar_cooldown = 0
        self.fire_storm_cooldown = 0
        self.star_of_curses_used = False
        self.star_of_curses_turns = 0
        self.curse_of_misfortune_cooldown = 0
        self.curse_of_misfortune_turns = 0      # turns victim is under misfortune
        self.active_luck_boost_used = False
        self.active_luck_boost_turns = 0
        self.artificial_moon_used = False
        self.artificial_moon_turns = 0
        self.scarlet_transformation_active = False
        self.scarlet_transformation_cooldown = 0
        self.spirit_animal_active = False
        self.spirit_animal_turns = 0
        self.spirit_animal_hp_bonus = 0
        self.spirit_animal_cooldown = 0
        self.wrath_of_nature_cooldown = 0
        self.wrath_of_nature_thorn = False
        self.wrath_of_nature_poison_turns = 0
        self.possession_active = False
        self.possession_turns = 0
        self.possession_cooldown = 0
        self.wraith_shriek_cooldown = 0
        self.wraith_shriek_acc_debuff = 0       # turns of −40% accuracy
        self.desire_explosion_uses = 2
        self.desire_explosion_cooldown = 0
        self.desire_symbiosis_used = False
        self.desire_emotion = None
        self.prohibition_uses = 2
        self.prohibition_cooldown = 0
        self.prohibited_ability = None
        self.punishment_cooldown = 0
        self.punishment_active = False          # target is petrified
        self.punishment_dmg_boost = 0.45        # 45% boost on attacks against punished target
        self.disciplinary_strike_cooldown = 0
        self.judgement_debuff_turns = 0         # −25% damage + lose buff
        self.sacred_conviction_cooldown = 0
        self.sacred_conviction_boost_turns = 0  # 3-turn +20% damage boost

    @classmethod
    async def load(cls, member: discord.Member, guild_id: int) -> "Fighter":
        """Async constructor — populates all_roles from both DB and Discord roles."""
        fighter = cls.__new__(cls)
        fighter.__init__(member)          # run sync init first (uses Discord roles only)
        # Now override all_roles with the richer combined source
        fighter.all_roles = await get_all_roles_from_db_and_discord(member, guild_id)
        # Also refresh spell_defence — it depends on all_roles now spanning DB pathway too
        fighter.has_spell_defence = any(r in SPELL_DEFENCE_ROLES for r in fighter.all_roles)
        return fighter

    def hp_bar(self):
        return f"[{self.hp}/{self.max_hp}]"

    def sp_bar(self):
        return f"[{self.sp}/{self.max_sp}]"

    def is_alive(self):
        return self.hp > 0

    def status(self):
        buffs = []
        if self.bleed > 0:              buffs.append(f"🩸×{self.bleed}")
        if self.gun_shot_bleed:         buffs.append("🩸∞")
        if self.paralysis > 0:          buffs.append(f"⚡×{self.paralysis}")
        if self.slumber > 0:            buffs.append(f"💤×{self.slumber}")
        if self.freeze > 0:             buffs.append(f"🧊×{self.freeze}")
        if self.burn > 0:               buffs.append(f"🔥×{self.burn}")
        if self.defence_reduction > 0:  buffs.append("🔍")
        if self.moral_freedom_active:   buffs.append("👼")
        if self.strength_boost > 0:     buffs.append("💪")
        if self.divination_dodge:       buffs.append("🔮")
        if self.danger_intuition_active: buffs.append("👁️intuition")
        if self.domination_active:      buffs.append(f"👊dom×{self.domination_turns}")
        if self.charm_turns > 0:        buffs.append(f"💰charm×{self.charm_turns}")
        if self.connect_share_turns > 0: buffs.append(f"🔗connect×{self.connect_share_turns}")
        if self.bribe_weakened:         buffs.append("💰weak")
        if self.invigorated:            buffs.append("💢")
        if self.prayer_active:          buffs.append("🙏🩸")
        if self.night_boost_active:     buffs.append("🌙")
        if self.phasing_active:         buffs.append("👻")
        if self.planting_turns > 0:     buffs.append(f"🌱×{self.planting_turns}")
        if self.spectate_active:        buffs.append("👁️")
        if self.arbitration_boost > 0:  buffs.append("⚔️+")
        if self.combat_studies_active:  buffs.append("📘")
        if self.rage_baited_turns > 0:  buffs.append(f"😡×{self.rage_baited_turns}")
        if self.folk_of_rage_active:    buffs.append(f"👊{int(self.folk_of_rage_pct*100)}%")
        if self.animal_companion_active: buffs.append("🐾")
        if self.computing_active:       buffs.append("🤖")
        if self.status_immune > 0:      buffs.append(f"💊×{self.status_immune}")
        if self.supernatural_resist_active: buffs.append("🛡️resist")
        if self.stun_block:             buffs.append("🛡️stun")
        # Seq 7
        if self.paper_figurine_turns > 0: buffs.append(f"🪆×{self.paper_figurine_turns}")
        if self.astrology_active:       buffs.append("🌟+hit")
        if self.observation_active:     buffs.append("🔎-dmg")
        if self.holy_water_regen > 0:   buffs.append(f"💧×{self.holy_water_regen}")
        if self.sneak_attack_pending:   buffs.append("🥷next")
        if self.appraisal_active:       buffs.append("🏷️+40%")
        if self.lucky_day_turns > 0:    buffs.append(f"🍀×{self.lucky_day_turns}")
        if self.trapped > 0:            buffs.append(f"🌿×{self.trapped}")
        if self.transformed:            buffs.append("🐺")
        if self.vile_crime_count > 0:   buffs.append(f"🔪×{self.vile_crime_count}/3")
        if self.debuff_stacks > 0:      buffs.append(f"🔻×{self.debuff_stacks}")
        if self.role_ability_disabled > 0: buffs.append(f"🎴×{self.role_ability_disabled}")
        # Seq 6
        if self.wind_blessed_buildup > 0: buffs.append(f"🌪️build×{self.wind_blessed_buildup}")
        if self.notary_buff_turns > 0:  buffs.append(f"📜+50%×{self.notary_buff_turns}")
        if self.notary_debuff_turns > 0: buffs.append(f"📜-50%×{self.notary_debuff_turns}")
        if self.charm_turns > 0:        buffs.append(f"💋charm×{self.charm_turns}")
        if self.distortion_active:      buffs.append("👑distort")
        if self.spirit_guide_active:    buffs.append(f"👻guide×{5 - self.spirit_guide_turns}")
        if self.dawn_paladin_active:    buffs.append("🛡️paladin")
        if self.devil_form_active:      buffs.append("😈")
        if self.scroll_status:          buffs.append(f"📜{self.scroll_status}")
        if self.scribe_active or self.scribe_copied_ability: buffs.append("📖copy")
        if self.polymath_ability:       buffs.append("📚studied")
        suffix = "  " + " ".join(buffs) if buffs else ""
        return f"❤️ {self.hp_bar()}  ✨ {self.sp_bar()}{suffix}"

    def can_receive_status(self):
        """Returns True if status effects can be applied."""
        return self.status_immune <= 0 and not self.supernatural_resist_active

    def apply_status_effect(self, effect: str, turns: int = 1, attacker=None):
        """Apply a status effect, respecting immunity and stun block."""
        if self.supernatural_resist_active:
            self.hp = max(0, self.hp - 40)
            return False
        # Balancing Act (Sailor Seq 9) — blocks the next 1 status effect
        if getattr(self, "balancing_act_status_immune", False):
            self.balancing_act_status_immune = False
            return False
        # Immortal Will — immune to stun/charm/seal when below 40% HP
        if getattr(self, "_immortal_will_active", False):
            if self.hp / max(1, self.max_hp) <= 0.40:
                if effect in ("stun", "charm", "paralysis", "slumber", "freeze"):
                    return False
        # Beast Form — only physical effects land (no ability seals etc.)
        if getattr(self, "_beast_form_turns", 0) > 0:
            if effect not in ("bleed", "burn", "paralysis"):
                return False
        if not self.can_receive_status():
            return False
        if effect in ("paralysis", "slumber", "freeze") and self.stun_block:
            self.stun_block = False
            return False
        # Seq 5: Blink stun immunity
        if effect in ("paralysis", "slumber", "freeze") and self.blink_stun_immune_turns > 0:
            return False
        # Domination stun resistance
        if self.domination_active and effect in ("paralysis", "slumber", "freeze"):
            if attacker is not None:
                atk_seq = get_seq_number(attacker.role) if attacker.role else 9
                def_seq = get_seq_number(self.role) if self.role else 9
                if atk_seq == def_seq:
                    if random.random() < 0.50:  # 50% resist from same seq
                        return False
                elif atk_seq > def_seq:  # attacker is lower seq (higher number)
                    if random.random() < 0.70:  # 70% resist from lower seq
                        return False
        if effect == "bleed":
            self.bleed = max(self.bleed, turns)
        elif effect == "paralysis":
            self.paralysis = max(self.paralysis, turns)
        elif effect == "slumber":
            self.slumber = max(self.slumber, turns)
        elif effect == "freeze":
            self.freeze = max(self.freeze, turns)
        elif effect == "burn":
            self.burn = max(self.burn, turns)
        return True

    def get_damage(self, pct_min: float, pct_max: float):
        lo = seq_dmg(self, pct_min)
        hi = seq_dmg(self, pct_max)
        base = random.randint(lo, hi)
        atk_bonus = self.stats["attack"] * 0.5
        spirit_mult = 1 + (self.stats["spirituality"] * 0.024)
        bonus = self.strength_boost + self.arbitration_boost + self.night_boost_damage
        self.strength_boost = 0
        self.arbitration_boost = 0
        dmg = int((base + atk_bonus + bonus) * spirit_mult)
        dmg = int(dmg * self.get_attack_mult())
        crit_chance = (self.stats["luck"] / 5) * 8
        crit = random.uniform(0, 100) < crit_chance
        if crit:
            dmg = int(dmg * 1.4)
            return dmg, True
        return dmg, False

    def apply_damage(self, dmg, is_ability=False, skill_key=None, attacker=None):
        # Radiant Armour — 50% damage reduction for duration
        if getattr(self, "_radiant_armour_turns", 0) > 0:
            dmg = int(dmg * 0.50)
            # Recoil on attacker handled elsewhere
        # Moon Manifestation / Moon Divinity — immune to physical/special
        if getattr(self, "_moon_manifest_turns", 0) > 0 and not is_ability:
            return 0
        # Planar Shift — fully immune
        if getattr(self, "_planar_shift_turns", 0) > 0:
            return 0
        # Divine Radiance — fully immune
        if getattr(self, "_divine_radiance_turns", 0) > 0:
            return 0
        # Omniscience — immune to all, attacker takes 40 counter (handled in process_action)
        if getattr(self, "_omniscience_turns", 0) > 0:
            return 0
        # True damage mode — ignore all reductions (skip paper_figurine/distortion etc.)
        # _true_damage_mode flag checked in process_action before calling apply_damage
        # Inquisition damage amplification — +50% incoming damage
        if getattr(self, "_inquisition_dmg_amp", 0) > 0:
            dmg = int(dmg * 1.50)
        # Permanent damage reduction
        if getattr(self, "_permanent_dmg_reduction", 0) > 0:
            pass  # handled in get_attack_mult on attacker side
        # Phase Dodge (Door Seq 9) — takes 0 damage from next hit
        if getattr(self, "_phase_dodge", False):
            self._phase_dodge = False
            return 0
        # Perfect Dodge (Error Seq 8 Swindler) — 100% dodge next turn
        if getattr(self, "_perfect_dodge", False):
            self._perfect_dodge = False
            return 0
        # Parry/Riposte (Twilight Giant Seq 7) — blocks hit, sets riposte flag
        if getattr(self, "_parry_active", False):
            self._parry_active = False
            self._riposte_pending = True
            return 0
        # Seq 5: Scarlet Transformation — immune to physical/special
        if self.scarlet_transformation_active and not is_ability:
            return 0
        # Divination dodge — fires on all ability damage
        if is_ability and getattr(self, "divination_dodge", False):
            dodge_chance = self._seer_dodge_chance or 70
            self.divination_dodge_turns = max(0, self.divination_dodge_turns - 1)
            if self.divination_dodge_turns <= 0:
                self.divination_dodge = False
                self._seer_dodge_chance = None
            if random.uniform(0, 100) < dodge_chance:
                self._pending_divination_msg = (
                    f"🔮 **{self.user.display_name}** reads the ability and sidesteps! "
                    f"*(divination {int(dodge_chance)}%)*"
                )
                return 0
        self._pending_divination_msg = ""
        # Seq 5: Stellar Self — completely unhittable for 2 turns vs same/lower seq
        if self.stellar_self_turns > 0:
            if attacker is not None:
                atk_seq = get_seq_number(attacker.role) if attacker.role else 9
                def_seq = get_seq_number(self.role) if self.role else 9
                if atk_seq >= def_seq:
                    return 0
        # Seq 5: Protection — damage reduction (80 normally, 10 for 1 turn after attacker attacked)
        if getattr(self, "protection_active", False):
            reduction = seq_val(self, 10) if getattr(self, "protection_weakened_turn", False) else seq_val(self, 80)
            dmg = max(0, dmg - reduction)
            self.protection_weakened_turn = False
            if self.protection_mode == "active":
                self.protection_active = False  # deactivates after absorbing one hit
        # Seq 5: Judgement debuff — -25% outgoing damage
        if self.judgement_debuff_turns > 0:
            dmg = int(dmg * 0.75)
        # Seq 5: Punishment — punished target takes +45% incoming damage
        if getattr(self, "punishment_active", False):
            dmg = int(dmg * 1.45)
        # Weakness Development: 20% damage vulnerability
        if getattr(self, "damage_vulnerability", 0) > 0:
            dmg = int(dmg * 1.20)
        if self.defence_reduction > 0:
            dmg = int(dmg * (1 + self.defence_reduction))
        if is_ability and self.has_spell_defence:
            dmg = int(dmg * 0.80)
        if skill_key and skill_key in self.analysed_skills:
            dmg = int(dmg * (1 - self.analysed_skills[skill_key]))
        if self.observation_active:
            dmg = max(1, int(dmg * random.uniform(0.50, 0.60)))
            self.observation_active = False
        if self.phasing_active:
            dmg = dmg // 2
            self.phasing_active = False
        # Bribe — Weaken: attacker's next attack deals 40% less
        if attacker is not None and getattr(attacker, "bribe_weakened", False):
            dmg = max(1, int(dmg * 0.60))
            attacker.bribe_weakened = False
        # Seq 6: notary debuff
        if self.notary_debuff_turns > 0:
            dmg = int(dmg * 0.50)
            return dmg
        # Last Stand (Death Gate / Undying Bone artifact) — survive lethal hit with 1 HP
        if dmg >= self.hp and getattr(self, "_last_stand", False):
            self._last_stand = False
            self.hp = 1
            return dmg
        self.hp = max(0, self.hp - dmg)
        # Connect: share 40% of damage taken with the attacker
        if attacker is not None and getattr(attacker, "connect_share_turns", 0) > 0:
            shared = max(1, int(dmg * 0.40))
            attacker.hp = max(0, attacker.hp - shared)
        return dmg

    def get_miss_chance(self, base_miss: int) -> int:
        """Return miss chance, modified by freeze and computing."""
        miss = base_miss
        if self.freeze_weakened:
            miss = min(95, miss + 30)
        if self.computing_active:
            miss = 0  # 100% accuracy
        if self.rage_baited_turns > 0:
            miss = 50
        if self.astrology_active:
            miss = max(0, miss - 30)
        # Conspiracy: 70% miss debuff
        if getattr(self, "conspiracy_miss_turns", 0) > 0:
            miss = min(95, miss + 70)
        # Seq 5: Desire Symbiosis modifiers
        if getattr(self, "desire_emotion", None) == "wrath":
            miss = min(95, miss + 20)   # −20% accuracy
        elif getattr(self, "desire_emotion", None) == "lust":
            miss = 0                    # 100% accuracy
        elif getattr(self, "desire_emotion", None) == "envy":
            miss = min(95, miss + 30)   # −30% accuracy
        # Seq 5: Wraith Shriek accuracy debuff
        if getattr(self, "wraith_shriek_acc_debuff", 0) > 0:
            miss = min(95, miss + 40)
        # Seq 5: Dream Alteration cumulative accuracy loss
        if getattr(self, "dream_alteration_acc_loss", 0) > 0:
            self.dream_alteration_acc_loss = min(0.50, self.dream_alteration_acc_loss + 0.05)
            miss = min(95, miss + int(self.dream_alteration_acc_loss * 100))
        # Seq 5: Active luck boost — 90% dodge vs incoming
        return miss

    def hit_chance_vs(self, defender) -> bool:
        """Returns True if attacker's hit lands considering lucky_day and conspiracy on attacker."""
        # Conspiracy miss debuff on the attacker
        if getattr(self, "conspiracy_miss_turns", 0) > 0:
            if random.random() < 0.70:
                return False
        # Glance into Fate curse — 30% miss chance for duration
        if getattr(self, "fate_cursed_turns", 0) > 0:
            if random.random() < 0.30:
                return False
        # Force miss (Fate Mastery)
        if getattr(self, "_force_miss_on_defender", 0) > 0:
            self._force_miss_on_defender -= 1
            return False
        # Absolute accuracy (Absolute Analysis / Absolute Vision / Paragon Divinity) — always hit
        if getattr(self, "_absolute_accuracy", False):
            return True
        if defender.lucky_day_turns > 0:
            return random.random() >= 0.5
        # Seq 5: Active Luck Boost — 90% dodge chance
        if getattr(defender, "active_luck_boost_turns", 0) > 0:
            if random.random() < 0.90:
                return False
        # Seq 5: Curse of Misfortune accuracy debuff on the attacker
        if getattr(self, "curse_of_misfortune_turns", 0) > 0:
            acc_roll = random.randint(10, 40)
            if random.randint(1, 100) > acc_roll:
                return False
        return True

    def get_ability_cost(self, base_cost, ability_key=None):
        if ability_key and self.memorise_turns > 0 and self.memorise_ability == ability_key:
            return 0
        if base_cost == 0:
            return 0
        # Lunar Domain — all abilities free
        if getattr(self, "_lunar_domain_turns", 0) > 0:
            return 0
        # Knowledge Divinity — all abilities free
        if getattr(self, "_knowledge_divinity_turns", 0) > 0:
            return 0
        reduction = min(0.15, self.stats["spirituality"] * 0.03)
        return max(1, int(base_cost * (1 - reduction)))

    def get_attack_mult(self) -> float:
        """Returns the combined attack multiplier from all active buffs/debuffs.
        Used by both get_damage() and direct ability damage calls."""
        mult = 1.0
        if self.invigorated:
            mult *= 1.30
            self.invigorated = False
        if self.prayer_active:
            mult *= 1.50
        if self.damage_boost_pct > 0:
            mult *= (1 + self.damage_boost_pct)
        if self.combat_studies_active:
            mult *= 1.20
        if self.folk_of_rage_active and self.folk_of_rage_pct > 0:
            mult *= (1 + self.folk_of_rage_pct)
        if self.rage_baited_turns > 0:
            mult *= 2.0
        if self.freeze_weakened:
            mult *= 0.70
        if self.appraisal_active:
            mult *= 1.40
            self.appraisal_active = False
        if self.debuff_stacks > 0 or self.debuff_turns > 0:
            mult *= 0.80  # 20% damage reduction while debuffed
        # Seq 6: notary buff
        if self.notary_buff_turns > 0:
            mult *= 1.50
        # Domination: +40% damage boost
        if self.domination_active:
            mult *= 1.40
        # Jurisdiction: starts at +20%, grows by 7% each turn
        if self.jurisdiction_active and self.jurisdiction_dmg_boost > 0:
            mult *= (1 + self.jurisdiction_dmg_boost)
        # Seq 5: Desire Symbiosis
        if getattr(self, "desire_emotion", None) == "lust":
            mult *= 0.40   # −60% strength
        elif getattr(self, "desire_emotion", None) == "envy":
            mult *= 0.70   # −30% strength
        # Seq 5: Punishment/Petrification — punished fighter deals -20% ability damage
        if getattr(self, "punishment_active", False):
            mult *= 0.80
        # Seq 5: Protection — −50% outgoing damage while shielding
        if getattr(self, "protection_active", False):
            mult *= 0.50
        # Fighting Spirit (Pugilist Seq 8) — +30% physical attack
        if self.fighting_spirit_active and self.fighting_spirit_boost > 0:
            mult *= (1 + self.fighting_spirit_boost)
        # Night Boost (Sleepless Seq 9) — damage bonus already applied via strength_boost
        # Moral Freedom (Instigator/Demoness) — handled as DOT, not mult
        # Sacred Conviction (Justiciar Seq 7) — +25% damage boost
        if getattr(self, "sacred_conviction_boost_turns", 0) > 0:
            mult *= 1.25
        # Strength Boost from various abilities
        if getattr(self, "strength_boost", 0) > 0:
            mult *= (1 + self.strength_boost * 0.05)  # each stack = +5%
        # Fear debuff (from spectate/nightmare abilities) — -25% damage
        if getattr(self, "_fear_turns", 0) > 0:
            mult *= 0.75
        # Sacred Conviction (Justiciar Seq 7) — +25% damage boost
        if getattr(self, "sacred_conviction_boost_turns", 0) > 0:
            mult *= 1.25
        # Fear debuff — -25% damage when feared
        if getattr(self, "_fear_turns", 0) > 0:
            mult *= 0.75
        # Radiant Armour — damage reduction (handled in apply_damage, not mult)
        # War Incarnation — 3x attack boost
        if getattr(self, "_war_incarnation_turns", 0) > 0:
            mult *= getattr(self, "_war_incarnation_boost", 3.0)
        # Fate Weave — double damage
        if getattr(self, "_fate_weave_dmg_boost", 0) > 0:
            mult *= 2.0
            self._fate_weave_dmg_boost -= 1
        # Fate Rewrite — max damage mode
        if getattr(self, "_fate_rewrite_max_dmg", 0) > 0:
            mult *= 2.5
            self._fate_rewrite_max_dmg -= 1
        # Moon Manifestation — double ability damage
        if getattr(self, "_moon_manifest_turns", 0) > 0:
            mult *= 2.0
        # Divine Radiance — 150% damage
        if getattr(self, "_divine_radiance_turns", 0) > 0:
            mult *= 1.50
        # Civilization Light — +50% damage
        if getattr(self, "_civilization_light_turns", 0) > 0:
            mult *= 1.50
        # Permanent damage reduction from omniscient analysis / tower divinity
        if getattr(self, "_permanent_dmg_reduction", 0) > 0:
            mult *= max(0.05, 1 - self._permanent_dmg_reduction)
        # Demonic corruption — -30% damage dealt
        if getattr(self, "_demonic_corruption_turns", 0) > 0 and getattr(self, "_demonic_corruption_dmg_reduce", 0) > 0:
            mult *= (1 - self._demonic_corruption_dmg_reduce)
        # Inquisition — +50% incoming damage (this is attacker's mult on defender's side,
        # we apply it here for the attacker's own outgoing damage amplification check below)
        # No damage turns (Miracle Wish)
        if getattr(self, "_no_damage_turns", 0) > 0:
            mult *= 0.0
        # Punishment active (Justiciar Seq 5) — +25% damage for 5 turns
        if getattr(self, "punishment_active", False) and getattr(self, "punishment_turns", 0) > 0:
            mult *= 1.25
        # Comeback mechanic — low HP fighter deals slightly more damage
        hp_pct = self.hp / max(1, self.max_hp)
        if hp_pct <= 0.20:
            mult *= 1.20   # last legs: +20% damage under 20% HP
        elif hp_pct <= 0.40:
            mult *= 1.10   # bloodied: +10% damage under 40% HP
        # Desperation bonus — empty SP means a raw physical push
        if self.sp <= 0:
            mult *= 1.08
        return mult

    def get_sp_regen(self):
        return 4 + int(self.stats["spirituality"] / 3)

    def evasion_chance(self):
        base = min(15, self.stats["speed"] * 1.5 + self.stats["luck"] * 0.75)
        if self.spectate_active:
            base = min(25, base + 8)
        # Balancing Act (Sailor Seq 9) — +20% evasion for duration
        if self.balancing_act_evasion_turns > 0:
            base = min(45, base + 20)
        # Absolute Luck — 100% evasion
        if getattr(self, "_absolute_luck_turns", 0) > 0:
            return 100
        # Fortune Divinity — 100% evasion
        if getattr(self, "_fortune_misfortune_turns", 0) > 0:
            return 100
        # Planar Shift — fully immune (no evasion needed, handled in apply_damage)
        # Moon Manifestation — immune to physical/special (handled in apply_damage)
        # No evasion turns (law alteration)
        if getattr(self, "_no_evasion_turns", 0) > 0:
            return 0
        if self.role == "[Fool] Seq 8 — Clown":
            base = min(30, base + 5)
        if self.role == "[Error] Seq 8 — Swindler":
            base = min(25, base + 4)
        if self.role == "[Darkness] Seq 8 — Midnight Poet":
            base = min(22, base + 3)
        if self.role == "[Moon] Seq 8 — Beast Tamer":
            base = min(22, base + 3)
        if self.role == "[Visionary] Seq 8 — Telepathist":
            base = min(22, base + 3)
        # Seq 7 role evasion boosts
        if self.role == "[Hanged Man] Seq 7 — Shadow Ascetic":
            base = min(28, base + 5)
        if self.role == "[Demoness] Seq 7 — Witch":
            base = min(25, base + 4)
        if self.role == "[Darkness] Seq 7 — Nightmare":
            base = min(25, base + 4)
        if self.role == "[Moon] Seq 7 — Vampire":
            base = min(24, base + 3)
        return base

    def divination_chance(self, defender_speed):
        speed_diff = self.stats["speed"] - defender_speed
        return max(40, min(90, 75 + speed_diff * 5))

    def tick_cooldowns(self):
        if self.desecration_cooldown > 0:   self.desecration_cooldown -= 1
        if self.trap_cooldown > 0:          self.trap_cooldown -= 1
        if self.balancing_cooldown > 0:     self.balancing_cooldown -= 1
        if self.pickpocket_cooldown > 0:    self.pickpocket_cooldown -= 1
        if self.vital_strike_cooldown > 0:  self.vital_strike_cooldown -= 1
        if self.card_tricks_cooldown > 0:   self.card_tricks_cooldown -= 1
        if self.phasing_cooldown > 0:       self.phasing_cooldown -= 1
        if self.arbitration_cooldown > 0:   self.arbitration_cooldown -= 1
        if self.testimony_cooldown > 0:     self.testimony_cooldown -= 1
        if self.memorise_turns > 0:         self.memorise_turns -= 1
        if self.rage_baited_cooldown > 0:   self.rage_baited_cooldown -= 1
        if self.poem_cooldown > 0:          self.poem_cooldown -= 1
        if self.burial_cooldown > 0:        self.burial_cooldown -= 1
        if self.crime_cooldown > 0:         self.crime_cooldown -= 1
        if self.magic_trick_cooldown > 0:   self.magic_trick_cooldown -= 1
        if self.brilliant_light_cooldown > 0: self.brilliant_light_cooldown -= 1
        if self.witch_curse_cooldown > 0:   self.witch_curse_cooldown -= 1
        if self.forceful_cooldown > 0:      self.forceful_cooldown -= 1
        if self.ritualistic_reasoning_cooldown > 0: self.ritualistic_reasoning_cooldown -= 1
        if self.domination_cooldown > 0:    self.domination_cooldown -= 1
        if self.bribe_cooldown > 0:         self.bribe_cooldown -= 1
        if self.domination_active:
            self.domination_turns -= 1
            if self.domination_turns <= 0:
                self.domination_active = False
        if self.status_immune > 0:          self.status_immune -= 1
        # Rage baited turns
        if self.rage_baited_turns > 0:
            self.rage_baited_turns -= 1
        # Folk of rage tick
        if self.folk_of_rage_active and self.folk_of_rage_turns > 0:
            self.folk_of_rage_turns -= 1
            if self.folk_of_rage_turns > 0:
                self.folk_of_rage_pct = min(0.25, self.folk_of_rage_pct + 0.05)
        # Freeze weakened clears after 1 turn
        if self.freeze_weakened and self.freeze == 0:
            self.freeze_weakened = False
        # Seq 7 cooldowns
        if self.observation_cooldown > 0:   self.observation_cooldown -= 1
        if self.therapy_cooldown > 0:       self.therapy_cooldown -= 1
        if self.water_bullet_cooldown > 0:  self.water_bullet_cooldown -= 1
        if self.gun_shot_cooldown > 0:      self.gun_shot_cooldown -= 1
        if self.sneak_attack_cooldown > 0:  self.sneak_attack_cooldown -= 1
        if self.sleep_spell_cooldown > 0:   self.sleep_spell_cooldown -= 1
        if self.summoning_dead_cooldown > 0: self.summoning_dead_cooldown -= 1
        if self.weapon_throw_cooldown > 0:  self.weapon_throw_cooldown -= 1
        if self.appraisal_cooldown > 0:     self.appraisal_cooldown -= 1
        if self.blood_sucker_cooldown > 0:  self.blood_sucker_cooldown -= 1
        if self.vine_bomb_cooldown > 0:     self.vine_bomb_cooldown -= 1
        if self.planting_cooldown > 0:      self.planting_cooldown -= 1
        if self.illegal_trade_cooldown > 0: self.illegal_trade_cooldown -= 1
        if self.mental_piercing_cooldown > 0: self.mental_piercing_cooldown -= 1
        if self.magic_spell_cooldown > 0:   self.magic_spell_cooldown -= 1
        if self.debuff_cooldown > 0:        self.debuff_cooldown -= 1
        if self.debuff_turns > 0:           self.debuff_turns -= 1
        # Role ability suppression (glance into fate)
        if self.role_ability_disabled > 0:  self.role_ability_disabled -= 1
        # Lucky day turns
        if self.lucky_day_turns > 0:        self.lucky_day_turns -= 1
        # Paper figurine
        if self.paper_figurine_turns > 0:   self.paper_figurine_turns -= 1
        # Holy water regen
        if self.holy_water_regen > 0:       self.holy_water_regen -= 1
        # Werewolf: increase dodge reduction each round, revert after 5 turns
        if self.transformed:
            self.transform_turns += 1
            self.transform_dodge_reduction = min(0.85, self.transform_dodge_reduction + 0.05)
            if self.transform_turns >= 5:
                self.transformed = False
                # Remove the HP bonus — drop user to 50% of their base max HP
                self.max_hp = max(1, self.max_hp - self.transform_hp_bonus)
                self.hp = max(1, self.max_hp // 2)   # 50% HP on revert
                self.transform_hp_bonus = 0
                self.transform_regen = 0
        # Seq 6 cooldowns
        if self.faceless_cooldown > 0:          self.faceless_cooldown -= 1
        if self.prometheus_cooldown > 0:        self.prometheus_cooldown -= 1
        if self.scribe_cooldown > 0:            self.scribe_cooldown -= 1
        if self.hypnotist_cooldown > 0:         self.hypnotist_cooldown -= 1
        if self.notary_cooldown > 0:            self.notary_cooldown -= 1
        if self.notary_buff_turns > 0:          self.notary_buff_turns -= 1
        if self.notary_debuff_turns > 0:        self.notary_debuff_turns -= 1
        if self.wind_blessed_cooldown > 0:      self.wind_blessed_cooldown -= 1
        if self.rose_bishop_cooldown > 0:       self.rose_bishop_cooldown -= 1
        if self.polymath_cooldown > 0:          self.polymath_cooldown -= 1
        if self.soul_assurer_cooldown > 0:      self.soul_assurer_cooldown -= 1
        if self.spirit_guide_cooldown > 0:      self.spirit_guide_cooldown -= 1
        if self.glance_fate_cooldown > 0:       self.glance_fate_cooldown -= 1
        if self.spirit_guide_active:
            self.spirit_guide_turns += 1
        if self.dawn_paladin_cooldown > 0:      self.dawn_paladin_cooldown -= 1
        if self.pleasure_witch_cooldown > 0:    self.pleasure_witch_cooldown -= 1
        if self.conspirer_cooldown > 0:         self.conspirer_cooldown -= 1
        if self.fire_ravens_cooldown > 0:       self.fire_ravens_cooldown -= 1
        if self.hurricane_cooldown > 0:         self.hurricane_cooldown -= 1
        if self.conspiracy_miss_turns > 0:      self.conspiracy_miss_turns -= 1
        if self.conspiracy_force_turns > 0:     self.conspiracy_force_turns -= 1
        if self.conspiracy_dmg_turns > 0:       self.conspiracy_dmg_turns -= 1
        if self.provocation_cooldown > 0:       self.provocation_cooldown -= 1
        if self.scrolls_professor_cooldown > 0: self.scrolls_professor_cooldown -= 1
        if self.artisan_cooldown > 0:           self.artisan_cooldown -= 1
        if self.artisan_item_cooldown > 0:      self.artisan_item_cooldown -= 1
        if self.calamity_priest_cooldown > 0:   self.calamity_priest_cooldown -= 1
        if self.potions_professor_cooldown > 0: self.potions_professor_cooldown -= 1
        if self.biologist_cooldown > 0:         self.biologist_cooldown -= 1
        if self.zombie_cooldown > 0:            self.zombie_cooldown -= 1
        if self.devil_form_cooldown > 0:        self.devil_form_cooldown -= 1
        if self.distortion_cooldown > 0:  self.distortion_cooldown -= 1
        if self.judge_cooldown > 0:             self.judge_cooldown -= 1
        if self.hurricane_interrupt_cd > 0:     self.hurricane_interrupt_cd -= 1
        if self.seer_divination_cooldown > 0:   self.seer_divination_cooldown -= 1
        if self.jurisdiction_cooldown > 0:      self.jurisdiction_cooldown -= 1
        if self.jurisdiction_active:
            self.jurisdiction_turns -= 1
            self.jurisdiction_dmg_boost += 0.07  # grows by 7% each turn
            if self.jurisdiction_turns <= 0:
                self.jurisdiction_active = False
                self.jurisdiction_dmg_boost = 0.0
        # Fear debuff tick
        if getattr(self, "_fear_turns", 0) > 0:    self._fear_turns -= 1
        # Balancing Act evasion buff tick
        if self.balancing_act_evasion_turns > 0:   self.balancing_act_evasion_turns -= 1
        # Fate curse tick
        if self.fate_cursed_turns > 0:             self.fate_cursed_turns -= 1
        # Fighting Spirit buff tick
        if self.fighting_spirit_cooldown > 0:      self.fighting_spirit_cooldown -= 1
        if self.fighting_spirit_active:
            self.fighting_spirit_turns -= 1
            if self.fighting_spirit_turns <= 0:
                self.fighting_spirit_active = False
                self.fighting_spirit_boost = 0.0
        # Computing duration tick
        if self.computing_cooldown > 0:            self.computing_cooldown -= 1
        if self.computing_active and self.computing_turns > 0:
            self.computing_turns -= 1
            if self.computing_turns <= 0:
                self.computing_active = False
        # Song / spectate cooldowns
        if self.song_cooldown > 0:                 self.song_cooldown -= 1
        if self.spectate_cooldown > 0:             self.spectate_cooldown -= 1
        if self.grazing_cooldown > 0:              self.grazing_cooldown -= 1

        # Seq 5 cooldowns
        if self.spirit_thread_cooldown > 0:         self.spirit_thread_cooldown -= 1
        if self.marionette_cooldown > 0:            self.marionette_cooldown -= 1
        if self.mental_theft_cooldown > 0:          self.mental_theft_cooldown -= 1
        if self.rewards_theft_cooldown > 0:         self.rewards_theft_cooldown -= 1
        if self.blink_cooldown > 0:                 self.blink_cooldown -= 1
        if self.blink_stun_immune_turns > 0:        self.blink_stun_immune_turns -= 1
        if self.travellers_door_cooldown > 0:       self.travellers_door_cooldown -= 1
        if self.dream_visitation_cooldown > 0:      self.dream_visitation_cooldown -= 1
        if self.purification_halo_cooldown > 0:     self.purification_halo_cooldown -= 1
        if self.purification_halo_sp_boost_turns > 0: self.purification_halo_sp_boost_turns -= 1
        if self.light_of_holiness_cooldown > 0:     self.light_of_holiness_cooldown -= 1
        if self.singing_cooldown > 0:               self.singing_cooldown -= 1
        if self.singing_enlightened_turns > 0:
            self.singing_enlightened_turns -= 1
            if self.singing_enlightened_turns <= 0:
                self.singing_str_boost = 0.0
                self.singing_spirit_boost = 0.0
        if self.arrow_cooldown > 0:                 self.arrow_cooldown -= 1
        if self.grazing_cooldown > 0:               self.grazing_cooldown -= 1
        if self.spiritual_suppression_cooldown > 0: self.spiritual_suppression_cooldown -= 1
        if self.spiritual_suppression_turns > 0:    self.spiritual_suppression_turns -= 1
        if self.damage_vulnerability > 0:           self.damage_vulnerability -= 1
        if self.protection_cooldown > 0:            self.protection_cooldown -= 1
        if self.dawn_armor_cooldown > 0:            self.dawn_armor_cooldown -= 1
        if self.thread_storm_cooldown > 0:          self.thread_storm_cooldown -= 1
        if self.cull_cooldown > 0:                  self.cull_cooldown -= 1
        if self.weakness_development_cooldown > 0:  self.weakness_development_cooldown -= 1
        if self.stellar_self_turns > 0:             self.stellar_self_turns -= 1
        if self.star_pillar_cooldown > 0:           self.star_pillar_cooldown -= 1
        if self.fire_storm_cooldown > 0:            self.fire_storm_cooldown -= 1
        if self.star_of_curses_turns > 0:           self.star_of_curses_turns -= 1
        if self.curse_of_misfortune_cooldown > 0:   self.curse_of_misfortune_cooldown -= 1
        if self.curse_of_misfortune_turns > 0:      self.curse_of_misfortune_turns -= 1
        if self.active_luck_boost_turns > 0:        self.active_luck_boost_turns -= 1
        if self.artificial_moon_turns > 0:          self.artificial_moon_turns -= 1
        if self.scarlet_transformation_cooldown > 0: self.scarlet_transformation_cooldown -= 1
        # Auto-deactivate scarlet transformation after 4 turns (cooldown starts at 7, deactivate when at 3)
        if self.scarlet_transformation_active and self.scarlet_transformation_cooldown == 3:
            self.scarlet_transformation_active = False
        if self.spirit_animal_cooldown > 0:         self.spirit_animal_cooldown -= 1
        if self.wrath_of_nature_cooldown > 0:       self.wrath_of_nature_cooldown -= 1
        if self.wrath_of_nature_poison_turns > 0:   self.wrath_of_nature_poison_turns -= 1
        if self.possession_cooldown > 0:            self.possession_cooldown -= 1
        if self.wraith_shriek_cooldown > 0:         self.wraith_shriek_cooldown -= 1
        if self.wraith_shriek_acc_debuff > 0:       self.wraith_shriek_acc_debuff -= 1
        if self.desire_explosion_cooldown > 0:      self.desire_explosion_cooldown -= 1
        if self.prohibition_cooldown > 0:           self.prohibition_cooldown -= 1
        if self.punishment_cooldown > 0:            self.punishment_cooldown -= 1
        if self.disciplinary_strike_cooldown > 0:   self.disciplinary_strike_cooldown -= 1
        if self.judgement_debuff_turns > 0:
            self.judgement_debuff_turns -= 1
            if self.judgement_debuff_turns <= 0:
                self.punishment_active = False  # Petrification ends with judgement
        if self.sacred_conviction_cooldown > 0:     self.sacred_conviction_cooldown -= 1
        if self.sacred_conviction_boost_turns > 0:
            self.sacred_conviction_boost_turns -= 1
            if self.sacred_conviction_boost_turns <= 0:
                self.damage_boost_pct = max(0.0, self.damage_boost_pct - 0.20)

# ── Purchasable Abilities ─────────────────────────────────

ABILITIES = {
    "leodero": {
        "name": "⚡ LEODERO", "cost": 22,
        "description": "Divine lightning — deals 30-40 damage but 10% rebounds to you.",
        "type": "leodero", "pct_min": 0.090, "pct_max": 0.145,
        "price": 10000, "purchasable": True,
    },
    "heal": {
        "name": "💚 Sacred Heal", "cost": 25,
        "description": "Mend your wounds with spiritual energy.",
        "type": "heal", "pct_min": 0.073, "pct_max": 0.109,
        "price": 10000, "purchasable": True,
    },
    "drain": {
        "name": "🌑 Soul Drain", "cost": 15,
        "description": "Siphon the enemy's life force and convert it into spirit.",
        "type": "drain", "pct_min": 0.045, "pct_max": 0.073,
        "price": 10000, "purchasable": True,
    },
    "shield": {
        "name": "🛡️ Spirit Shield", "cost": 20,
        "description": "Erect a barrier to absorb the next blow.",
        "type": "shield",
        "price": 10000, "purchasable": True,
    },
    "debuff": {
        "name": "🔻 Debuff", "cost": 70,
        "description": "Weaken the enemy — reduces their damage by 20% for 5 turns. One-time use per battle.",
        "type": "debuff",
        "price": 15000, "purchasable": True,
    },
    "ritual": {
        "name": "🕯️ Ritualistic Magic", "cost": 18,
        "description": "Channel ancient rites — choose Strength, Divination, or Blessing.",
        "type": "ritual",
        "price": 12000, "purchasable": True,
    },
}

def check_divination_dodge(attacker: "Fighter", defender: "Fighter") -> tuple:
    """Check if defender dodges an incoming ability via Divination.
    Returns (dodged: bool, dodge_msg: str)."""
    if not getattr(defender, "divination_dodge", False):
        return False, ""
    dodge_chance = defender._seer_dodge_chance or 70
    defender.divination_dodge_turns = max(0, defender.divination_dodge_turns - 1)
    if defender.divination_dodge_turns <= 0:
        defender.divination_dodge = False
        defender._seer_dodge_chance = None
    if random.uniform(0, 100) < dodge_chance:
        return True, f"🔮 **{defender.user.display_name}** reads the ability and vanishes! *(divination dodge {int(dodge_chance)}%)*"
    return False, ""

def apply_ability_damage(attacker: "Fighter", defender: "Fighter", pct: float,
                         is_ability=True, skill_key=None, battle=None) -> int:
    """Apply ability damage scaled to attacker's sequence average HP.
    pct is a fraction (e.g. 0.30 = 30% of SEQ_AVG_HP).
    Returns 0 if divination dodges it — caller should check and append dodge_msg."""
    dodged, _dodge_msg = check_divination_dodge(attacker, defender)
    if dodged:
        # Store the message for the caller to append
        attacker._last_divination_msg = _dodge_msg
        return 0
    attacker._last_divination_msg = ""
    dmg = int(seq_dmg(attacker, pct) * attacker.get_attack_mult())
    # Polymath: studied ability fires at 70% potency
    if getattr(attacker, "polymath_pending", False):
        dmg = int(dmg * 0.70)
        attacker.polymath_pending = False
    actual = defender.apply_damage(dmg, is_ability=is_ability, skill_key=skill_key)
    # Mental Reflect (Visionary Seq 8) — reflects 50% ability damage back to attacker
    if getattr(defender, "_mental_reflect", False):
        defender._mental_reflect = False
        reflect = max(1, actual // 2)
        attacker.hp = max(0, attacker.hp - reflect)
        attacker._last_divination_msg += f"\n🪞 **Mental Reflect!** {reflect} damage reflected back at **{attacker.user.display_name}**!"
    return actual

def _abil_dmg(attacker, defender, pct, msg_list, is_ability=True, skill_key=None):
    """Apply ability damage and append any divination dodge message to msg_list[0].
    Returns actual damage dealt (0 if dodged)."""
    result = apply_ability_damage(attacker, defender, pct, is_ability=is_ability, skill_key=skill_key)
    div_msg = getattr(attacker, "_last_divination_msg", "")
    if div_msg:
        msg_list[0] += div_msg + "\n"
    return result

def _battle_key(channel_id, user1_id, user2_id):
    return (channel_id, frozenset({user1_id, user2_id}))

def get_battle(channel_id, user=None):
    """Get the battle for a channel. If user is provided, finds their specific battle."""
    if user is not None:
        for key, battle in battles.items():
            if key[0] == channel_id:
                c = battle["challenger"].user
                o = battle["opponent"].user
                if user in (c, o):
                    return battle
        return None
    # Legacy: return first battle in channel (for compat where user context isn't available)
    for key, battle in battles.items():
        if key[0] == channel_id:
            return battle
    return None

def get_battle_key_for_user(channel_id, user):
    for key, battle in battles.items():
        if key[0] == channel_id:
            c = battle["challenger"].user
            o = battle["opponent"].user
            if user in (c, o):
                return key
    return None

def find_battle_by_players(challenger: discord.Member, opponent: discord.Member):
    for key, battle in battles.items():
        c = battle["challenger"].user
        o = battle["opponent"].user
        if (c == challenger and o == opponent) or (c == opponent and o == challenger):
            channel_id = key[0]
            return channel_id, battle
    return None, None

def swap_turn(battle):
    import time
    c = battle["challenger"]
    o = battle["opponent"]
    battle["turn"] = o.user if battle["turn"] == c.user else c.user
    battle["turn_count"] = battle.get("turn_count", 0) + 1
    battle["last_action_time"] = time.time()  # reset inactivity timer

    # Sudden death after turn 40 — both fighters take escalating damage
    turn_count = battle["turn_count"]
    if turn_count >= 40:
        overtime = turn_count - 39
        c_drain = max(1, int(c.max_hp * 0.04 * overtime))
        o_drain = max(1, int(o.max_hp * 0.04 * overtime))
        c.hp = max(0, c.hp - c_drain)
        o.hp = max(0, o.hp - o_drain)
        battle["_sudden_death_msg"] = (
            f"⚠️ **SUDDEN DEATH — Turn {turn_count}!** "
            f"The arena itself drains both fighters! "
            f"**{c.user.display_name}** −{c_drain} HP, **{o.user.display_name}** −{o_drain} HP!\n"
        )

def get_fighters(user, battle):
    c = battle["challenger"]
    o = battle["opponent"]
    if user == c.user:
        return c, o
    return o, c

def validate_turn(user, battle):
    if not battle or not battle["accepted"]:
        return False
    if user not in [battle["challenger"].user, battle["opponent"].user]:
        return False
    if battle["turn"] != user:
        return False
    return True

def hp_bar_visual(current, maximum, length=10):
    """Compact filled bar — e.g. ████████░░ 80%"""
    if maximum <= 0:
        return "`░░░░░░░░░░` **0%**"
    pct = current / maximum
    filled = max(0, min(length, round(pct * length)))
    bar = "█" * filled + "░" * (length - filled)
    return f"`{bar}` **{int(pct*100)}%**"

def get_status_icons(f: "Fighter") -> str:
    """Compact icon row for active effects only."""
    icons = []
    if f.bleed > 0:          icons.append(f"🩸{f.bleed}")
    if f.burn > 0:           icons.append(f"🔥{f.burn}")
    if f.paralysis > 0:      icons.append(f"⚡{f.paralysis}")
    if f.slumber > 0:        icons.append(f"😴{f.slumber}")
    if f.freeze > 0:         icons.append(f"❄️{f.freeze}")
    if f.trapped > 0:        icons.append(f"🪤{f.trapped}")
    if getattr(f, "blink_stun_immune_turns", 0) > 0: icons.append("💨imm")
    if getattr(f, "protection_active", False):     icons.append(f"🛡️prot")
    if getattr(f, "dawn_armor_active", False):      icons.append("🌅armor")
    if getattr(f, "scarlet_transformation_active", False): icons.append("🌙imm")
    if getattr(f, "marionette_active", False):         icons.append("🎭")
    if getattr(f, "artificial_moon_turns", 0) > 0:    icons.append(f"🌕{f.artificial_moon_turns}")
    if getattr(f, "active_luck_boost_turns", 0) > 0:  icons.append(f"🍀{f.active_luck_boost_turns}")
    if getattr(f, "desire_emotion", None):             icons.append(f"😈{f.desire_emotion[:3]}")
    if getattr(f, "stellar_self_turns", 0) > 0:       icons.append(f"⭐{f.stellar_self_turns}")
    if getattr(f, "possession_active", False):         icons.append("👁️")
    if getattr(f, "disease_propagation_active", False):icons.append("🦠")
    if getattr(f, "spiritual_suppression_turns", 0) > 0: icons.append(f"🦷{f.spiritual_suppression_turns}")
    if getattr(f, "judgement_debuff_turns", 0) > 0:   icons.append(f"⛓️{f.judgement_debuff_turns}")
    if getattr(f, "curse_of_misfortune_turns", 0) > 0:icons.append(f"🎲{f.curse_of_misfortune_turns}")
    if getattr(f, "star_of_curses_turns", 0) > 0:     icons.append(f"🌟{f.star_of_curses_turns}")
    if getattr(f, "wrath_of_nature_poison_turns", 0) > 0: icons.append(f"☠️{f.wrath_of_nature_poison_turns}")
    if getattr(f, "dream_alteration_acc_loss", 0) > 0:icons.append(f"💭-{int(f.dream_alteration_acc_loss*100)}%")
    return " ".join(icons)

def battle_status(battle):
    c = battle["challenger"]
    o = battle["opponent"]
    turn_count = battle.get("turn_count", 0)

    def row(f, is_turn):
        arrow = "▶ " if is_turn else "     "
        name  = f"**{arrow}{f.user.display_name}**"
        hp    = hp_bar_visual(f.hp, f.max_hp)
        sp    = hp_bar_visual(f.sp, f.max_sp, length=8)
        icons = get_status_icons(f)
        lines = [
            name,
            f"❤️ {hp} `{f.hp}/{f.max_hp}`",
            f"✨ {sp} `{f.sp}/{f.max_sp}`",
        ]
        if icons:
            lines.append(icons)
        return "\n".join(lines)

    sep = f"\n{'─'*32}\n"
    return f"{row(c, battle['turn']==c.user)}{sep}{row(o, battle['turn']==o.user)}\n\n*Turn {turn_count}*"

def apply_turn_effects(attacker: Fighter, defender: Fighter, battle):
    log = ""
    skip_turn = False

    # Death Gate retaliate — auto-deal damage the turn after surviving a lethal hit
    if getattr(attacker, "_death_gate_retaliate", False):
        attacker._death_gate_retaliate = False
        ret_dmg = seq_val(attacker, 70)
        defender.hp = max(0, defender.hp - ret_dmg)
        log += f"🗝️ **Death Gate Retaliate!** **{attacker.user.display_name}** unleashes **{ret_dmg}** spirit damage!\n"

    # Bribe — resolve pending effect at start of the briber's turn after the concealed throw
    if attacker.bribe_pending and not attacker.bribe_concealed_turn:
        effect = attacker.bribe_pending
        attacker.bribe_pending = None
        if effect == "weaken":
            defender.bribe_weakened = True
            log += f"💰 **Bribe — Weaken!** A secret deal weakens **{defender.user.display_name}** — their next attack deals **40% less damage**!\n"
        elif effect == "charm":
            defender.charm_turns = 2
            log += f"💰 **Bribe — Charm!** **{defender.user.display_name}** is enchanted — cannot attack for **2 turns**!\n"
        elif effect == "connect":
            attacker.connect_share_turns = 1
            log += f"💰 **Bribe — Connect!** A mystical link is forged — **40% of damage {attacker.user.display_name} takes** is shared with **{defender.user.display_name}** this turn!\n"
    attacker.bribe_concealed_turn = False  # clear the concealed flag

    # Blessing from previous turn
    if attacker.blessed:
        attacker.strength_boost += 3
        attacker.blessed = False
        log += f"🌿 **{attacker.user.display_name}**'s blessing awakens!\n"

    # Planting heal
    if attacker.planting_turns > 0:
        plant_heal = seq_dmg(attacker, 0.074)  # ~20 at Seq 9, scales with sequence
        attacker.hp = min(attacker.max_hp, attacker.hp + plant_heal)
        attacker.planting_turns -= 1
        log += f"🌱 Seed blooms — **{attacker.user.display_name}** restored **{plant_heal} HP**! ({attacker.planting_turns} left)\n"

    # Prayer self-bleed
    if attacker.prayer_active:
        bleed_dmg = random.randint(max(1, int(attacker.max_hp * 0.02)), max(1, int(attacker.max_hp * 0.03)))
        attacker.hp = max(0, attacker.hp - bleed_dmg)
        log += f"🙏🩸 Prayer toll — **{bleed_dmg}** damage to **{attacker.user.display_name}**!\n"

    # Bleed
    if attacker.bleed > 0:
        bleed_dmg = max(1, int(attacker.max_hp * 0.02))
        attacker.hp = max(0, attacker.hp - bleed_dmg)
        attacker.bleed -= 1
        log += f"🩸 **{attacker.user.display_name}** bleeds **{bleed_dmg}** damage! ({attacker.bleed} left)\n"

    # Gun shot permanent bleed
    if attacker.gun_shot_bleed:
        gs_bleed = max(1, int(attacker.max_hp * 0.02))
        attacker.hp = max(0, attacker.hp - gs_bleed)
        log += f"🔫🩸 Gunshot wound bleeds **{gs_bleed}** damage!\n"

    # Burn (witch_burn = 4% max HP, else 3%)
    if attacker.burn > 0:
        b_dmg = max(1, int(attacker.max_hp * (0.04 if attacker.witch_burn else 0.03)))
        attacker.hp = max(0, attacker.hp - b_dmg)
        attacker.burn -= 1
        if attacker.burn == 0:
            attacker.witch_burn = False
        log += f"🔥 **{attacker.user.display_name}** burns for **{b_dmg}** damage! ({attacker.burn} left)\n"

    # Moral freedom: fighter takes scaled damage per turn
    if attacker.moral_freedom_active:
        mf_dmg = getattr(attacker, "_moral_freedom_dot", None)
        if mf_dmg is None:
            mf_dmg = max(1, int(attacker.max_hp * 0.05))
        attacker.hp = max(0, attacker.hp - mf_dmg)
        log += f"👼 **{attacker.user.display_name}** suffers moral freedom — **{mf_dmg}** damage!\n"

    # Dog chase (from trap)
    if attacker.dog_chase_turns > 0:
        dc_pct = getattr(attacker, "_dog_chase_dmg_pct", 0.10)
        dc_dmg = max(1, int(attacker.base_hp * dc_pct))
        attacker.hp = max(0, attacker.hp - dc_dmg)
        attacker.dog_chase_turns -= 1
        log += f"🐕 **{attacker.user.display_name}** is being chased — **{dc_dmg}** damage! ({attacker.dog_chase_turns} left)\n"

    # Conspiracy forced random ability
    if attacker.conspiracy_force_turns > 0 and not attacker.instigated_action:
        all_acts = [k for k in [attacker.role_ability, "attack"] if k]
        if all_acts:
            attacker.instigated_action = random.choice(all_acts)

    # Trap activation (activates randomly across 5 rounds, minimum 1 turn delay)
    # Traps are laid by the defender and triggered when the attacker takes their turn
    if hasattr(defender, "trap_slots") and defender.trap_slots:
        turn_count = battle.get("turn_count", 0) if battle else 0
        turns_since = turn_count - defender.trap_set_turn
        if turns_since >= 1:
            activation_chance = min(0.9, turns_since * 0.18)
            if random.random() < activation_chance:
                trap = defender.trap_slots.pop(0)
                if trap == "flames":
                    attacker.apply_status_effect("burn", 3)
                    log += f"🪤 **TRAP!** Fire trap ignites — **{attacker.user.display_name}** burns for **3 turns**!\n"
                elif trap == "trip":
                    attacker._trip_trap_miss = True
                    log += f"🪤 **TRAP!** Trip wire! **{attacker.user.display_name}** stumbles — next attack misses!\n"
                elif trap == "dog_chase":
                    pct = random.uniform(0.05, 0.07)
                    dog_dmg = int(attacker.base_hp * pct)
                    attacker.dog_chase_turns = max(getattr(attacker, "dog_chase_turns", 0), 2)
                    attacker._dog_chase_dmg_pct = pct
                    log += f"🪤 **TRAP!** Dogs released! **{attacker.user.display_name}** chased — **{int(pct*100)}% base HP/turn** for **2 turns**!\n"
                elif trap == "tripwire":
                    backlash = max(int(getattr(attacker, "last_attack_dmg", 0) * 0.30), int(attacker.base_hp * 0.07))
                    attacker.hp = max(0, attacker.hp - backlash)
                    log += f"🪤 **TRAP!** Tripwire! **{attacker.user.display_name}** falls — **{backlash}** backlash damage!\n"
                # Start cooldown after last trap fires
                if not defender.trap_slots:
                    defender.trap_cooldown = random.randint(3, 4)

    # Holy water regen
    if attacker.holy_water_regen > 0:
        hw_heal = max(1, int(attacker.max_hp * 0.03))
        attacker.hp = min(attacker.max_hp, attacker.hp + hw_heal)
        log += f"💧 Holy water heals **{attacker.user.display_name}** **{hw_heal} HP**! ({attacker.holy_water_regen - 1} left)\n"
        # tick_cooldowns will decrement this

    # Werewolf regen
    if attacker.transformed:
        wolf_regen = max(1, int(attacker.max_hp * 0.02))
        attacker.hp = min(attacker.max_hp, attacker.hp + wolf_regen)
        log += f"🐺 **{attacker.user.display_name}** regenerates **{wolf_regen} HP**!\n"

    # Sneak attack pending — the attacker is the one who had sneak applied to them, so they lose their turn
    if attacker.sneak_attack_pending:
        attacker.sneak_attack_pending = False
        log += f"🥷 **{attacker.user.display_name}** is reeling from the sneak attack — turn lost!\n"
        regen = attacker.get_sp_regen()
        attacker.sp = min(attacker.max_sp, attacker.sp + regen)
        attacker.tick_cooldowns()
        return log, True

    # Trapped (vine bomb or provocation)
    if attacker.trapped > 0:
        turns_left = attacker.trapped - 1
        attacker.trapped -= 1
        if getattr(attacker, "provocation_trapped", False):
            if attacker.trapped == 0:
                attacker.provocation_trapped = False
            log += f"😤 **{attacker.user.display_name}** chokes on their own rage, unable to act! " + (f"({turns_left} turn{'s' if turns_left != 1 else ''} remaining)\n" if turns_left > 0 else "*(trap ends next turn)*\n")
        else:
            log += f"🌿 **{attacker.user.display_name}** is trapped in vines — turn lost! " + (f"({turns_left} remaining)\n" if turns_left > 0 else "*(trap ends next turn)*\n")
        regen = attacker.get_sp_regen()
        attacker.sp = min(attacker.max_sp, attacker.sp + regen)
        attacker.tick_cooldowns()
        return log, True

    # Charm (bribe) — cannot attack
    if attacker.charm_turns > 0:
        turns_left = attacker.charm_turns - 1
        attacker.charm_turns -= 1
        if attacker.hurricane_charging:
            attacker.hurricane_charging = False
            attacker.hurricane_interrupt_cd = 5
            log += f"💰 **{attacker.user.display_name}**'s Hurricane of Light is interrupted by charm!\n"
        log += f"💰 **{attacker.user.display_name}** is charmed — cannot attack! " + (f"({turns_left} turns remaining)\n" if turns_left > 0 else "*(charm ends next turn)*\n")
        regen = attacker.get_sp_regen()
        attacker.sp = min(attacker.max_sp, attacker.sp + regen)
        attacker.tick_cooldowns()
        return log, True

    # Paralysis
    if attacker.paralysis > 0:
        turns_left = attacker.paralysis - 1
        attacker.paralysis -= 1
        if attacker.hurricane_charging:
            attacker.hurricane_charging = False
            attacker.hurricane_interrupt_cd = 5
            log += f"⚡ **{attacker.user.display_name}**'s Hurricane of Light is interrupted by paralysis! *(5 turn cd before recharge)*\n"
        log += f"⚡ **{attacker.user.display_name}** is paralysed — turn lost! " + (f"({turns_left} remaining)\n" if turns_left > 0 else "*(paralysis ends next turn)*\n")
        regen = attacker.get_sp_regen()
        attacker.sp = min(attacker.max_sp, attacker.sp + regen)
        attacker.tick_cooldowns()
        return log, True

    # Slumber (sleep)
    if attacker.slumber > 0:
        turns_left = attacker.slumber - 1
        attacker.slumber -= 1
        if attacker.hurricane_charging:
            attacker.hurricane_charging = False
            attacker.hurricane_interrupt_cd = 5
            log += f"💤 **{attacker.user.display_name}**'s Hurricane of Light is interrupted by sleep! *(5 turn cd before recharge)*\n"
        log += f"💤 **{attacker.user.display_name}** is asleep — turn lost! " + (f"({turns_left} remaining)\n" if turns_left > 0 else "*(sleep ends next turn)*\n")
        regen = attacker.get_sp_regen()
        attacker.sp = min(attacker.max_sp, attacker.sp + regen)
        attacker.tick_cooldowns()
        return log, True

    # Freeze skip (first turn)
    if attacker.freeze > 0:
        turns_left = attacker.freeze - 1
        attacker.freeze -= 1
        if attacker.freeze_weakened is False and attacker.hurricane_charging:
            attacker.hurricane_charging = False
            attacker.hurricane_interrupt_cd = 5
            log += f"🧊 **{attacker.user.display_name}**'s Hurricane of Light is interrupted by freeze! *(5 turn cd before recharge)*\n"
        if attacker.freeze == 0:
            attacker.freeze_weakened = True
        log += f"🧊 **{attacker.user.display_name}** is frozen — turn lost! " + (f"({turns_left} remaining)\n" if turns_left > 0 else "*(freeze ends next turn, accuracy weakened)*\n")
        regen = attacker.get_sp_regen()
        attacker.sp = min(attacker.max_sp, attacker.sp + regen)
        attacker.tick_cooldowns()
        return log, True

    # Listener self-stun check
    if attacker.listening_active:
        if random.randint(1, 3) == 1:
            log += f"👂 **{attacker.user.display_name}**'s listening backfires — stunned this turn!\n"
            regen = attacker.get_sp_regen()
            attacker.sp = min(attacker.max_sp, attacker.sp + regen)
            attacker.tick_cooldowns()
            return log, True

    # Study restore
    if attacker.study_skip:
        attacker.study_skip = False
        attacker.sp = attacker.max_sp
        log += f"📚 **{attacker.user.display_name}** finishes studying — SP fully restored!\n"

    # Combat studies skip
    if attacker.combat_studies_skip:
        attacker.combat_studies_skip = False
        attacker.combat_studies_active = True
        log += f"📘 **{attacker.user.display_name}** finishes studying — attacks permanently boosted by **20%**!\n"
        regen = attacker.get_sp_regen()
        attacker.sp = min(attacker.max_sp, attacker.sp + regen)
        attacker.tick_cooldowns()
        return log, True

    # Animal companion attacks
    if attacker.animal_companion_active:
        attacker.animal_companion_turn += 1
        if attacker.animal_companion_turn % 2 == 0:
            actual_dmg = defender.apply_damage(seq_dmg(attacker, 0.045))
            log += f"🐾 **{attacker.user.display_name}**'s companion strikes **{defender.user.display_name}** for **{actual_dmg}** damage!\n"

    # Seq 6: Spirit Guide deals 20 damage per turn and costs 15 SP to maintain
    if attacker.spirit_guide_active:
        sg_sp_cost = max(5, int(attacker.max_sp * 0.06))  # ~6% max SP per turn
        if attacker.spirit_guide_turns >= 5:
            attacker.spirit_guide_active = False
            attacker.spirit_guide_turns = 0
            attacker.spirit_guide_cooldown = 7
            log += f"👻 **Spirit Guide** departs — reached its limit of 5 turns! *(7 turn cd)*\n"
        elif attacker.sp < sg_sp_cost:
            attacker.spirit_guide_active = False
            attacker.spirit_guide_turns = 0
            attacker.spirit_guide_cooldown = 7
            log += f"👻 **Spirit Guide** fades — insufficient SP to maintain! *(7 turn cd)*\n"
        elif random.random() < 0.30:
            # 30% chance: spirit possesses the user — they lose their turn
            attacker.sp -= sg_sp_cost
            log += f"👻 **Spirit Guide** turns on **{attacker.user.display_name}** — possessed! Turn lost! *(−{sg_sp_cost} SP)*\n"
            regen = attacker.get_sp_regen()
            attacker.sp = min(attacker.max_sp, attacker.sp + regen)
            attacker.tick_cooldowns()
            return log, True
        else:
            attacker.sp -= sg_sp_cost
            actual_dmg = defender.apply_damage(seq_dmg(attacker, 0.091))
            log += f"👻 **Spirit Guide** strikes **{defender.user.display_name}** for **{actual_dmg}** damage! *(−{sg_sp_cost} SP, {5 - attacker.spirit_guide_turns} turns left)*\n"

    # Seq 6: Devil Form madness — 30% chance to lose own turn
    if attacker.devil_form_active:
        if random.random() < 0.30:
            log += f"😈 **{attacker.user.display_name}** succumbs to devil madness — turn lost!\n"
            regen = attacker.get_sp_regen()
            attacker.sp = min(attacker.max_sp, attacker.sp + regen)
            attacker.tick_cooldowns()
            return log, True

    # Seq 6: Charm — attacker (who is charmed) loses their turn
    if attacker.charm_turns > 0:
        attacker.charm_turns -= 1
        log += f"💋 **{attacker.user.display_name}** is charmed — turn lost! ({attacker.charm_turns} left)\n"
        regen = attacker.get_sp_regen()
        attacker.sp = min(attacker.max_sp, attacker.sp + regen)
        attacker.tick_cooldowns()
        return log, True

    # Seq 6: Scroll semi-permanent status re-apply (20% chance each turn, applies to defender)
    if attacker.scroll_status and random.random() < 0.20:
        defender.apply_status_effect(attacker.scroll_status, 1)
        log += f"📜 **Scroll** re-applies **{attacker.scroll_status}** to **{defender.user.display_name}**!\n"

    # Seq 6: Wind Blessed SP drain during buildup
    if attacker.wind_blessed_buildup > 0:
        if attacker.sp >= 25:
            attacker.sp -= 25
            attacker.wind_blessed_buildup += 1
            log += f"🌪️ **Wind Blessed** charging… *(round {attacker.wind_blessed_buildup - 1}/2, −25 SP)*\n"
            if attacker.wind_blessed_buildup > 2:
                attacker.wind_blessed_buildup = 0
                attacker.wind_blessed_cooldown = 5
                if defender.phasing_active:
                    defender.phasing_active = False
                    defender.phasing_cooldown = 1
                    log += f"🌪️ **WIND BLESSED UNLEASHED!** — but **{defender.user.display_name}** phases through the blast!\n"
                else:
                    actual_dmg = defender.apply_damage(seq_dmg(attacker, 0.273), is_ability=True)
                    log += f"🌪️ **WIND BLESSED UNLEASHED!** **{actual_dmg}** crushing damage! *(5 turn cd)*\n"
        else:
            attacker.wind_blessed_buildup = 0
            log += f"🌪️ **Wind Blessed** fizzled — insufficient SP during buildup!\n"

    attacker.tick_cooldowns()

    # Seq 5: Spirit Thread — escalating stun chance
    if attacker.spirit_thread_active:
        attacker.spirit_thread_turns += 1
        attacker.spirit_thread_stun_pct = min(100, attacker.spirit_thread_turns * 10)
        if random.randint(1, 100) <= attacker.spirit_thread_stun_pct:
            attacker.spirit_thread_active = False
            attacker.spirit_thread_cooldown = 5
            log += f"🧵 **Spirit Thread** — **{attacker.user.display_name}**'s spirit threads snap — stunned and thread control ends! *(5 turn cd)*\n"
            regen = attacker.get_sp_regen()
            attacker.sp = min(attacker.max_sp, attacker.sp + regen)
            return log, True

    # Seq 5: Marionette — 50% attack / 50% intercept each turn
    if attacker.marionette_active:
        attacker.marionette_turns -= 1
        if attacker.marionette_turns <= 0:
            attacker.marionette_active = False
            log += f"🎭 **Marionette** dissolves!\n"
        elif random.random() < 0.50:
            m_dmg = seq_val(attacker, 10)
            actual_dmg = defender.apply_damage(m_dmg, is_ability=True)
            log += f"🎭 **Marionette** strikes **{defender.user.display_name}** for **{actual_dmg}** damage! ({attacker.marionette_turns} turns left)\n"

    # Seq 5: Disease Propagation — growing DoT
    if attacker.disease_propagation_active:
        attacker.disease_propagation_turns += 1
        base_dot = seq_val(attacker, 15)
        base_dmg = base_dot + int(attacker.disease_propagation_turns * attacker.max_hp * 0.01)
        actual_dmg = defender.apply_damage(base_dmg, is_ability=True)
        log += f"🦠 **Plague** ravages **{defender.user.display_name}** for **{actual_dmg}** damage! (turn {attacker.disease_propagation_turns})\n"

    # Seq 5: Dragged to Hell — drain self 15 SP/round (scales as pct of max_sp)
    if attacker.dragged_to_hell_active:
        drain = max(1, int(attacker.max_sp * 0.07))
        attacker.sp = max(0, attacker.sp - drain)
        log += f"🔥 **Gate to Hell** drains **{drain} SP** from **{attacker.user.display_name}**!\n"

    # Seq 5: Evil Sealing — 10 recoil per round charged (scaled)
    if attacker.evil_sealing_active:
        recoil = seq_val(attacker, 10)
        charge = seq_val(attacker, 30)
        attacker.hp = max(0, attacker.hp - recoil)
        attacker.evil_sealing_damage += charge
        log += f"💀 **Evil Sealing** charging — power at **{attacker.evil_sealing_damage}** dmg ({recoil} recoil taken)!\n"

    # Seq 5: Spiritual Takeover — scaled dmg/round on victim
    if defender.spiritual_takeover_active:
        dot = seq_val(attacker, 15)
        actual_dmg = defender.apply_damage(dot, is_ability=True)
        log += f"👻 **Spiritual Takeover** — **{defender.user.display_name}** suffers **{actual_dmg}** spirit damage!\n"

    # Seq 5: Possession — scaled dmg/round
    if attacker.possession_active:
        attacker.possession_turns -= 1
        if attacker.possession_turns <= 0:
            attacker.possession_active = False
            log += f"👁️ **Possession** ends!\n"
        else:
            dot = seq_val(attacker, 20)
            actual_dmg = defender.apply_damage(dot, is_ability=True)
            log += f"👁️ **Possession** tears at **{defender.user.display_name}** — **{actual_dmg}** damage! ({attacker.possession_turns} turns left)\n"

    # Seq 5: Wrath of Nature — thorn recoil (scaled)
    if attacker.wrath_of_nature_thorn and defender.hp > 0:
        recoil = seq_val(attacker, 5)
        defender.hp = max(0, defender.hp - recoil)
        log += f"🌿 **Thorn Coat** deals **{recoil}** recoil to **{defender.user.display_name}**!\n"

    # Seq 5: Wrath of Nature — poison DoT (scaled)
    if attacker.wrath_of_nature_poison_turns > 0:
        dot = seq_val(attacker, 15)
        actual_dmg = defender.apply_damage(dot, is_ability=True)
        log += f"☠️ **Poison Vine** poisons **{defender.user.display_name}** for **{actual_dmg}** dmg! ({attacker.wrath_of_nature_poison_turns} left)\n"

    # Seq 5: Star of Curses — one random curse per round
    if attacker.star_of_curses_turns > 0:
        curse = random.choice(["slow", "weakness", "mutation", "trauma"])
        if curse == "slow":
            log += f"🌟 **Star of Curses — Slow!** **{defender.user.display_name}** accuracy reduced by 30%!\n"
            defender.conspiracy_miss_turns = max(defender.conspiracy_miss_turns, 1)
        elif curse == "weakness":
            defender.debuff_stacks = max(defender.debuff_stacks, 1)
            log += f"🌟 **Star of Curses — Weakness!** **{defender.user.display_name}** damage reduced by 30%!\n"
        elif curse == "mutation":
            mut_dmg = seq_val(attacker, 30)
            actual_dmg = defender.apply_damage(mut_dmg, is_ability=True)
            log += f"🌟 **Star of Curses — Mutation!** **{defender.user.display_name}** takes **{actual_dmg}** damage!\n"
        elif curse == "trauma":
            log += f"🌟 **Star of Curses — Trauma!** **{defender.user.display_name}** flinches — turn lost!\n"
            regen = defender.get_sp_regen()
            defender.sp = min(defender.max_sp, defender.sp + regen)

    # Seq 5: Curse of Misfortune — recoil on the cursed fighter each round
    if attacker.curse_of_misfortune_turns > 0:
        recoil = seq_val(attacker, 20)
        attacker.hp = max(0, attacker.hp - recoil)
        log += f"🎲 **Misfortune** recoils — **{attacker.user.display_name}** takes **{recoil}** self damage!\n"

    # Seq 5: Active Luck Boost — 10% random event each turn (scaled)
    if attacker.active_luck_boost_turns > 0:
        if random.random() < 0.10:
            lucky_hit = seq_val(attacker, 40)
            actual_dmg = defender.apply_damage(lucky_hit, is_ability=True)
            log += f"🍀 **Lucky Event!** Fortune strikes **{defender.user.display_name}** for **{actual_dmg}** damage!\n"
    if attacker.danger_intuition_active:
        if attacker.sp >= attacker.intuition_upkeep_sp:
            attacker.sp -= attacker.intuition_upkeep_sp
            log += f"👁️ **Danger Intuition** active — **{attacker.intuition_upkeep_sp} SP** upkeep paid.\n"
        else:
            attacker.danger_intuition_active = False
            log += f"👁️ **Danger Intuition** deactivated — insufficient SP!\n"

    # Connect damage share — tick down
    if attacker.connect_share_turns > 0:
        attacker.connect_share_turns -= 1

    regen = attacker.get_sp_regen()
    attacker.sp = min(attacker.max_sp, attacker.sp + regen)
    if not battle.get("trap_set_this_turn"):
        if attacker.sp < attacker.max_sp * 0.3:
            log += f"*⚠️ {attacker.user.display_name} is low on SP! `{attacker.sp}/{attacker.max_sp}`*\n"
        # Only show regen when meaningful — skip if full
        elif attacker.sp < attacker.max_sp:
            log += f"*+{regen} SP → `{attacker.sp}/{attacker.max_sp}`*\n"
    # Comeback alert — compact single line
    hp_pct = attacker.hp / max(1, attacker.max_hp)
    if hp_pct <= 0.20 and attacker.hp > 0:
        log += f"*🔥 {attacker.user.display_name} is on the brink (+20% dmg)*\n"
    elif hp_pct <= 0.40 and attacker.hp > 0:
        log += f"*💢 {attacker.user.display_name} is bloodied (+10% dmg)*\n"
    battle.pop("trap_set_this_turn", None)
    return log, False

def make_battle_embed(description, battle, next_turn_name, color=0x9B59B6):
    turn_count = battle.get("turn_count", 0)
    # Keep description tight — trim if too long
    if len(description) > 800:
        description = "…" + description[-797:]
    embed = discord.Embed(description=description, color=color)
    embed.add_field(name="\u200b", value=battle_status(battle), inline=False)
    embed.set_footer(text=f"Turn {turn_count}  •  ⚡ {next_turn_name}'s move  •  /leave to forfeit")
    return embed

def _make_battle_view(battle):
    old_view = battle.get("view")
    if old_view is not None:
        old_view.stop()  # disable buttons on old view so players can't click stale UI
    view = BattleView(battle)
    battle["view"] = view
    return view

def resolve_bets(channel_id, winner: discord.Member, loser: discord.Member, guild_id: int = 0):
    bets = active_bets.pop(channel_id, {})
    if not bets:
        return ""
    log = "\n\n💰 **Betting Results:**\n"
    for bettor_id, bet in bets.items():
        amount = bet["amount"]
        target = bet["target"]
        if target.id == winner.id:
            winnings = amount * 2
            add_pounds(bettor_id, winnings, guild_id=guild_id)
            log += f"> 🏆 <@{bettor_id}> won **{winnings:,} soli**!\n"
        else:
            log += f"> 💸 <@{bettor_id}> lost **{amount:,} soli**.\n"
    return log

async def safe_edit(message: discord.Message, **kwargs):
    try:
        await message.edit(**kwargs)
    except (discord.errors.NotFound, discord.errors.HTTPException):
        pass

async def safe_respond(interaction: discord.Interaction, content: str, ephemeral: bool = True):
    """Send a response safely — handles already-acknowledged and expired interactions."""
    try:
        if interaction.response.is_done():
            await interaction.followup.send(content, ephemeral=ephemeral)
        else:
            await interaction.response.send_message(content, ephemeral=ephemeral)
    except (discord.errors.NotFound, discord.errors.InteractionResponded, discord.errors.HTTPException):
        pass

async def _view_on_error(interaction: discord.Interaction, error: Exception, item):
    """Shared on_error for all Views — prevents silent button death."""
    import traceback
    traceback.print_exc()
    await safe_respond(interaction, f"❌ Something went wrong: `{type(error).__name__}`")

async def send_and_replace(battle: dict, channel, embed, view=None):
    """Delete old message instantly, send new one with player pings."""
    c = battle["challenger"]
    o = battle["opponent"]
    ping = f"{c.user.mention} {o.user.mention}"
    old_msg = battle.get("battle_message")
    if old_msg:
        try:
            await old_msg.delete()
        except Exception:
            pass
    try:
        new_msg = await channel.send(content=ping, embed=embed, view=view)
        battle["battle_message"] = new_msg
        return new_msg
    except (discord.errors.HTTPException, discord.errors.NotFound) as e:
        print(f"[send_and_replace] Failed to send: {e}")
        return None


INACTIVITY_TIMEOUT = 180  # 3 minutes

async def inactivity_watcher(battle_key: tuple, channel_id: int):
    """Ends a fight if nobody takes an action for INACTIVITY_TIMEOUT seconds."""
    import time
    while True:
        await asyncio.sleep(30)  # check every 30 seconds
        battle = battles.get(battle_key)
        if battle is None:
            return  # battle already ended normally
        if not battle.get("accepted"):
            return  # not started yet — ChallengeView timeout handles this
        last = battle.get("last_action_time", time.time())
        if (time.time() - last) >= INACTIVITY_TIMEOUT:
            # Inactivity timeout — end the fight
            battles.pop(battle_key, None)
            # Refund any bets
            bets = active_bets.pop(channel_id, {})
            for bettor_id, bet in bets.items():
                add_pounds(bettor_id, bet["amount"], guild_id=battle.get("guild_id", 0))
            channel = bot.get_channel(channel_id)
            if channel:
                c = battle["challenger"]
                o = battle["opponent"]
                embed = discord.Embed(
                    title="⏳ Fight Over — Inactivity",
                    description=(
                        f"Neither **{c.user.display_name}** nor **{o.user.display_name}** "
                        f"responded for **3 minutes**.\n\n"
                        f"The fight has been called off. No winner, no loser.\n"
                        f"💰 Bets refunded."
                    ),
                    color=0x95A5A6
                )
                old_msg = battle.get("battle_message")
                if old_msg:
                    try:
                        await old_msg.delete()
                    except Exception:
                        pass
                try:
                    await channel.send(
                        content=f"{c.user.mention} {o.user.mention}",
                        embed=embed
                    )
                except Exception:
                    pass
            return

# ── Battle log ────────────────────────────────────────────
battle_log = []  # list of dicts recorded per battle

def record_battle(winner: discord.Member, loser: discord.Member, winner_seq: int, loser_seq: int, guild_id: int = 0):
    import time
    battle_log.append({
        "winner": winner.display_name,
        "winner_id": winner.id,
        "loser": loser.display_name,
        "loser_id": loser.id,
        "winner_seq": winner_seq,
        "loser_seq": loser_seq,
        "ts": int(time.time()),
    })
    if len(battle_log) > 100:
        battle_log.pop(0)
    # Track win/loss streaks in economy data
    w_eco = get_economy(winner.id, guild_id=guild_id)
    l_eco = get_economy(loser.id, guild_id=guild_id)
    w_eco.setdefault("wins", 0); w_eco.setdefault("losses", 0); w_eco.setdefault("win_streak", 0)
    l_eco.setdefault("wins", 0); l_eco.setdefault("losses", 0); l_eco.setdefault("win_streak", 0)
    w_eco["wins"] += 1
    w_eco["win_streak"] += 1
    l_eco["losses"] += 1
    l_eco["win_streak"] = 0
    save_economy()

async def post_battle_log(guild: discord.Guild, winner: discord.Member, loser: discord.Member,
                          winner_seq: int, loser_seq: int, reward: int, guild_id: int = 0):
    """Send a battle result embed to the guild's log channel if configured."""
    if not guild:
        return
    ch_id = log_channel.get(guild.id)
    if not ch_id:
        return
    ch = guild.get_channel(ch_id)
    if not ch:
        return
    w_eco = get_economy(winner.id, guild_id=guild_id)
    streak = w_eco.get("win_streak", 1)
    wins = w_eco.get("wins", 1)
    losses = get_economy(loser.id, guild_id=guild_id).get("losses", 1)
    streak_txt = f" 🔥 **{streak} win streak!**" if streak >= 2 else ""
    embed = discord.Embed(
        title="⚔️ Battle Result",
        description=(
            f"🏆 **{winner.display_name}** *(Seq {winner_seq})* defeated **{loser.display_name}** *(Seq {loser_seq})*{streak_txt}\n"
            f"💰 **+{reward:,} soli** awarded\n"
            f"📊 {winner.mention} — {wins}W / {w_eco.get('losses', 0)}L | "
            f"{loser.mention} — {get_economy(loser.id, guild_id=guild_id).get('wins', 0)}W / {losses}L"
        ),
        color=0xF1C40F
    )
    import time
    embed.set_footer(text=time.strftime("%d/%m/%Y %H:%M"))
    try:
        await ch.send(embed=embed)
    except Exception as e:
        print(f"[log] Failed to post battle log: {e}")

SEQ_WIN_REWARD = {
    9: 10000,
    8: 20000,
    7: 30000,
    6: 40000,
    5: 50000,
    4: 60000,
    3: 70000,
    2: 80000,
    1: 90000,
    0: 100000,
}


def check_victory(attacker, defender, battle_key, msg):
    if not defender.is_alive() or not attacker.is_alive():
        winner = attacker if defender.hp <= 0 else defender
        loser = defender if defender.hp <= 0 else attacker
        channel_id = battle_key[0] if isinstance(battle_key, tuple) else battle_key
        # Extract guild_id BEFORE popping the battle from the dict
        b = battles.get(battle_key)
        guild_id = b["guild_id"] if b else 0
        bet_log = resolve_bets(channel_id, winner.user, loser.user, guild_id)
        battles.pop(battle_key, None)

        # Soli reward based on loser's sequence
        loser_seq = get_seq_number(loser.role) if loser.role else 9
        winner_seq = get_seq_number(winner.role) if winner.role else 9
        reward = SEQ_WIN_REWARD.get(loser_seq, 1000)
        add_pounds(winner.user.id, reward, guild_id=guild_id)

        # Bounty claim — if loser had a bounty, winner claims it
        bounty_amount = claim_bounty(guild_id, loser.user.id)
        if bounty_amount > 0:
            add_pounds(winner.user.id, bounty_amount, guild_id=guild_id)
            _bchan = bot.get_channel(channel_id)
            if _bchan:
                async def _send_bounty_msg(ch=_bchan, w=winner, l=loser, amt=bounty_amount):
                    await ch.send(
                        f"🎯 **Bounty Claimed!** **{w.user.display_name}** collected "
                        f"**{amt:,} soli** for defeating **{l.user.display_name}**!"
                    )
                bot.loop.create_task(_send_bounty_msg())

        record_battle(winner.user, loser.user, winner_seq, loser_seq, guild_id)
        guild = winner.user.guild if hasattr(winner.user, "guild") else None
        bot.loop.create_task(post_battle_log(guild, winner.user, loser.user, winner_seq, loser_seq, reward, guild_id))
        bot.loop.create_task(award_combat_xp(winner.user, loser_seq, guild))
        bot.loop.create_task(penalize_combat_xp(loser.user, loser_seq, guild))

        xp_gain = SEQ_WIN_XP.get(loser_seq, 150)
        xp_penalty = SEQ_LOSS_XP.get(loser_seq, 60)
        turns_fought = b.get("turn_count", 0) if b else 0
        hp_remaining_pct = int((winner.hp / max(1, winner.max_hp)) * 100)
        # Flavour line based on how close the fight was
        if loser.hp <= 0 and winner.hp <= int(winner.max_hp * 0.15):
            fight_flavour = "⚡ *A razor-thin victory — both fighters nearly destroyed!*"
        elif turns_fought >= 30:
            fight_flavour = f"⏳ *An epic {turns_fought}-turn war of attrition.*"
        elif turns_fought <= 5:
            fight_flavour = "💨 *A dominant, lightning-fast finish.*"
        else:
            fight_flavour = f"⚔️ *Fought across {turns_fought} turns.*"

        # Random chance for the winner to score a bonus stat point, independent
        # of the Sequence-based pool
        bonus_point_line = ""
        if random.random() < BONUS_STAT_POINT_CHANCE:
            bonus_record = get_or_create_stats(winner.user.id, winner.user)
            bonus_record["points"] += 1
            save_stats()
            bonus_point_line = (
                f"\n🎲 **{winner.user.display_name}** gains a flash of insight mid-battle — "
                f"**+1 bonus stat point!** *(spend it with `/stats_user`)*"
            )

        embed = discord.Embed(
            title="𓂃 🏆 Victory!",
            description=(
                msg
                + f"\n\n**{loser.user.display_name}** falls.\n"
                + f"✨ **{winner.user.display_name}** stands victorious at **{winner.hp}/{winner.max_hp} HP** ({hp_remaining_pct}%)!\n"
                + f"{fight_flavour}\n\n"
                + f"💰 **+{reward:,} soli** awarded for defeating a Seq {loser_seq}!\n"
                + f"📈 **{winner.user.display_name}** gains **+{xp_gain} EXP**\n"
                + f"📉 **{loser.user.display_name}** loses **-{xp_penalty} EXP**"
                + bonus_point_line
                + bet_log
            ),
            color=0xF1C40F
        )
        return embed
    return None

def get_available_actions(fighter: Fighter, battle) -> list:
    """Returns list of action keys available to this fighter."""
    actions = ["attack", "special"]
    # Add purchased spells
    owned = get_economy(fighter.user.id, guild_id=battle["guild_id"])["spells"]
    actions.extend(owned)
    # Add all pathway role abilities (Seq 9, 8, 7...)
    for role_name in fighter.all_roles:
        ak = ROLE_ABILITIES.get(role_name)
        if ak and ak not in actions and not fighter.role_ability_disabled > 0:
            actions.append(ak)
        ak2 = ROLE_ABILITIES_2.get(role_name)
        if ak2 and ak2 not in actions:
            actions.append(ak2)
    return actions

# ── Views ─────────────────────────────────────────────────

class BattleView(discord.ui.View):
    # Fool, Error, and Door are the escape/misdirection pathways — they get a
    # clean, no-drawback Flee button instead of Surrender. Every other pathway
    # keeps Surrender (forfeit with an EXP penalty). Applies at any Sequence.
    FLEE_PATHWAYS = {"Fool", "Error", "Door"}

    def __init__(self, battle):
        super().__init__(timeout=None)  # inactivity_watcher is the sole timeout mechanism
        fighter, _ = get_fighters(battle["turn"], battle)
        pathway = get_pathway_name(fighter.role) if fighter.role else ""

        if pathway in self.FLEE_PATHWAYS:
            flee_button = discord.ui.Button(label="🏃 Flee", style=discord.ButtonStyle.success)
            flee_button.callback = self.flee_btn
            self.add_item(flee_button)
        else:
            surrender_button = discord.ui.Button(label="🏳️ Surrender", style=discord.ButtonStyle.danger)
            surrender_button.callback = self.surrender_btn
            self.add_item(surrender_button)

    async def on_timeout(self):
        pass  # never fires (timeout=None) — inactivity_watcher handles all fight endings

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        battle = get_battle(interaction.channel.id, interaction.user)
        if not battle:
            await safe_respond(interaction, "❌ No active battle!")
            return False
        if interaction.user != battle["turn"]:
            await safe_respond(interaction, "❌ It's not your turn!")
            return False
        return True

    @discord.ui.button(label="⚔️ Attack", style=discord.ButtonStyle.primary, disabled=True)
    async def attack_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await process_action(interaction, "attack")

    @discord.ui.button(label="⚡ Strike", style=discord.ButtonStyle.danger)
    async def special_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await process_action(interaction, "special")

    @discord.ui.button(label="🌟 Pathway", style=discord.ButtonStyle.secondary)
    async def pathway_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        battle = get_battle(interaction.channel.id, interaction.user)
        attacker, _ = get_fighters(interaction.user, battle)

        # Group abilities by pathway family
        family_pages = {}   # family_name -> [(role_name, ak), ...]
        for role_name in attacker.all_roles:
            ak = ROLE_ABILITIES.get(role_name)
            if not ak:
                continue
            family = get_pathway_family(role_name) or "Other"
            if family not in family_pages:
                family_pages[family] = []
            if not any(a == ak for _, a in family_pages[family]):
                family_pages[family].append((role_name, ak))
            # Also include Seq 5 secondary ability
            ak2 = ROLE_ABILITIES_2.get(role_name)
            if ak2 and not any(a == ak2 for _, a in family_pages[family]):
                family_pages[family].append((role_name, ak2))

        # Special one-shot entries (Prometheus/Scribe/Polymath) go on page 0
        special_keys = []
        if attacker.prometheus_stolen_ability:
            pstolen = attacker.prometheus_stolen_ability
            info_p, _ = get_ability_info(pstolen)
            if info_p:
                special_keys.append(("_prometheus_stolen", pstolen))
        if attacker.scribe_copied_ability and not attacker.scribe_active:
            pcopied = attacker.scribe_copied_ability
            info_c, _ = get_ability_info(pcopied)
            if info_c:
                special_keys.append(("_scribe_copy", pcopied))
        if attacker.polymath_ability and attacker.polymath_cooldown == 0:
            ppoly = attacker.polymath_ability
            info_poly, _ = get_ability_info(ppoly)
            if info_poly:
                special_keys.append(("_polymath_use", ppoly))

        if not family_pages and not special_keys:
            return await interaction.response.send_message(
                "❌ You have no pathway abilities.", ephemeral=True)

        pages = []  # list of (title, [(role_name, ak)])
        if special_keys:
            pages.append(("✨ Special", special_keys))
        for family, pairs in family_pages.items():
            pages.append((f"[{family}] Pathway", pairs))

        await interaction.response.send_message(
            **_build_pathway_page(attacker, pages, 0),
            view=PathwayPageView(attacker, pages, 0),
            ephemeral=True
        )

    @discord.ui.button(label="✨ Abilities", style=discord.ButtonStyle.success)
    async def abilities_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        battle = get_battle(interaction.channel.id, interaction.user)
        attacker, _ = get_fighters(interaction.user, battle)
        purchased = get_economy(interaction.user.id, guild_id=interaction.guild_id)["spells"]
        if not purchased:
            return await interaction.response.send_message(
                "❌ No purchased abilities! Visit `/shop`.\n*(Pathway ability → 🌟 Pathway button)*",
                ephemeral=True)
        lines = [f"**✨ Choose an ability** — Spirit: `{attacker.sp}/{attacker.max_sp}`\n"]
        for key in purchased:
            info, _ = get_ability_info(key)
            if not info:
                continue
            cost = attacker.get_ability_cost(info["cost"], key)
            cost_label = info.get("display_cost") or f"{cost} SP"
            affordable = "✅" if (info.get("display_cost") or attacker.sp >= cost) else "❌"
            lines.append(f"{affordable} **{info['name']}** — `{cost_label}`\n> {get_scaled_description(key, attacker)}\n")
        await interaction.response.send_message(
            "\n".join(lines), view=AbilityView(attacker, purchased), ephemeral=True)

    async def surrender_btn(self, interaction: discord.Interaction):
        battle = get_battle(interaction.channel.id, interaction.user)
        if not battle or interaction.user not in [battle["challenger"].user, battle["opponent"].user]:
            return await safe_respond(interaction, "❌ You're not in this fight!")
        # Show confirmation
        await interaction.response.send_message(
            "🏳️ **Surrender?** You'll forfeit the match immediately — no soli penalty but you lose the EXP.\n"
            "Click confirm to surrender.",
            view=SurrenderConfirmView(interaction.user, battle, interaction.channel.id),
            ephemeral=True
        )

    async def flee_btn(self, interaction: discord.Interaction):
        battle = get_battle(interaction.channel.id, interaction.user)
        if not battle or interaction.user not in [battle["challenger"].user, battle["opponent"].user]:
            return await safe_respond(interaction, "❌ You're not in this fight!")
        # Show confirmation
        await interaction.response.send_message(
            "🏃 **Flee the fight?** Your pathway lets you slip away clean — "
            "no soli lost, no EXP lost, nothing recorded.\n"
            "Click confirm to flee.",
            view=FleeConfirmView(interaction.user, battle, interaction.channel.id),
            ephemeral=True
        )


class AbilityBagView(discord.ui.View):
    """Shows grazed abilities as buttons — selecting one fires it as the player's turn action."""
    def __init__(self, attacker: "Fighter", entries: list, battle: dict, channel):
        super().__init__(timeout=30)
        self.attacker = attacker
        self.battle   = battle
        self.channel  = channel
        for ability_key, name, cost_txt in entries[:5]:  # max 5 buttons
            btn = discord.ui.Button(
                label=f"{name} ({cost_txt})",
                style=discord.ButtonStyle.primary,
                custom_id=f"bag_{ability_key}"
            )
            btn.callback = self._make_callback(ability_key, name)
            self.add_item(btn)

    def _make_callback(self, ability_key: str, name: str):
        async def callback(interaction: discord.Interaction):
            if interaction.user != self.attacker.user:
                return await interaction.response.send_message("❌ Not your bag!", ephemeral=True)
            self.stop()
            await interaction.response.defer()
            # Fire as a normal ability action — reuse process_action
            await process_action(interaction, "ability", ability_key=ability_key, from_bag=True)
        return callback


class SurrenderConfirmView(discord.ui.View):
    def __init__(self, user: discord.Member, battle: dict, channel_id: int):
        super().__init__(timeout=20)
        self.user = user
        self.battle = battle
        self.channel_id = channel_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user != self.user:
            await safe_respond(interaction, "❌ Not your surrender!")
            return False
        return True

    @discord.ui.button(label="✅ Confirm Surrender", style=discord.ButtonStyle.danger)
    async def confirm_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        battle = self.battle
        battle_key = get_battle_key_for_user(self.channel_id, self.user)
        if not battle_key or battle_key not in battles:
            return await safe_respond(interaction, "❌ Battle already ended.")
        attacker, defender = get_fighters(self.user, battle)
        battles.pop(battle_key, None)
        bets = active_bets.pop(self.channel_id, {})
        guild_id = battle.get("guild_id", 0)
        for bettor_id, bet in bets.items():
            add_pounds(bettor_id, bet["amount"], guild_id=guild_id)
        loser_seq = get_seq_number(attacker.role) if attacker.role else 9
        winner_seq = get_seq_number(defender.role) if defender.role else 9
        # No soli penalty for surrender — just XP
        record_battle(defender.user, attacker.user, winner_seq, loser_seq, guild_id)
        guild = defender.user.guild if hasattr(defender.user, "guild") else None
        bot.loop.create_task(penalize_combat_xp(attacker.user, loser_seq, guild))
        xp_penalty = SEQ_LOSS_XP.get(loser_seq, 60)
        embed = discord.Embed(
            title="🏳️ Surrender",
            description=(
                f"**{attacker.user.display_name}** raises the white flag.\n\n"
                f"**{defender.user.display_name}** wins by surrender!\n"
                f"*(No soli penalty — {attacker.user.display_name} loses **{xp_penalty} EXP**)*\n"
                f"💰 Bets refunded."
            ),
            color=0x95A5A6
        )
        channel = bot.get_channel(self.channel_id)
        if channel:
            old_msg = battle.get("battle_message")
            if old_msg:
                try: await old_msg.delete()
                except: pass
            await channel.send(
                content=f"{attacker.user.mention} {defender.user.mention}",
                embed=embed
            )
        await interaction.response.send_message("🏳️ You have surrendered.", ephemeral=True)

    @discord.ui.button(label="❌ Cancel", style=discord.ButtonStyle.secondary)
    async def cancel_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        await interaction.response.send_message("Surrender cancelled.", ephemeral=True)


class FleeConfirmView(discord.ui.View):
    """No-drawback escape — only ever shown to Fool/Error/Door fighters (see BattleView)."""
    def __init__(self, user: discord.Member, battle: dict, channel_id: int):
        super().__init__(timeout=20)
        self.user = user
        self.battle = battle
        self.channel_id = channel_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user != self.user:
            await safe_respond(interaction, "❌ Not your flee!")
            return False
        return True

    @discord.ui.button(label="🏃 Confirm Flee", style=discord.ButtonStyle.success)
    async def confirm_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        battle = self.battle
        battle_key = get_battle_key_for_user(self.channel_id, self.user)
        if not battle_key or battle_key not in battles:
            return await safe_respond(interaction, "❌ Battle already ended.")
        attacker, defender = get_fighters(self.user, battle)
        battles.pop(battle_key, None)
        bets = active_bets.pop(self.channel_id, {})
        guild_id = battle.get("guild_id", 0)
        for bettor_id, bet in bets.items():
            add_pounds(bettor_id, bet["amount"], guild_id=guild_id)
        # No soli penalty, no EXP penalty, no win/loss recorded — a clean escape
        embed = discord.Embed(
            title="🏃 Flee",
            description=(
                f"**{attacker.user.display_name}** slips away without a trace!\n\n"
                f"*(No soli lost, no EXP lost — nothing recorded)*\n"
                f"💰 Bets refunded."
            ),
            color=0x2ECC71
        )
        channel = bot.get_channel(self.channel_id)
        if channel:
            old_msg = battle.get("battle_message")
            if old_msg:
                try: await old_msg.delete()
                except: pass
            await channel.send(
                content=f"{attacker.user.mention} {defender.user.mention}",
                embed=embed
            )
        await interaction.response.send_message("🏃 You fled the fight — no penalty.", ephemeral=True)

    @discord.ui.button(label="❌ Cancel", style=discord.ButtonStyle.secondary)
    async def cancel_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        await interaction.response.send_message("Flee cancelled.", ephemeral=True)


def _check_cooldown(attacker: Fighter, key: str) -> str:
    """Returns cooldown message or empty string."""
    checks = {
        "desecration":          (attacker.desecration_cooldown,    "Desecration"),
        "trap":                 (attacker.trap_cooldown,           "Trap"),
        "balancing_act":        (attacker.balancing_cooldown,      "Balancing Act"),
        "phasing":              (attacker.phasing_cooldown,        "Phasing"),
        "pickpocket":           (attacker.pickpocket_cooldown,     "Pickpocket"),
        "vital_strike":         (attacker.vital_strike_cooldown,   "Vital Strike"),
        "card_tricks":          (attacker.card_tricks_cooldown,    "Card Tricks"),
        "arbitration":          (attacker.arbiter_cooldown,        "Arbiter"),
        "jurisdiction":         (attacker.jurisdiction_cooldown,   "Jurisdiction"),
        "testimony":            (attacker.testimony_cooldown,       "Testimony"),
        "rage_baited":          (attacker.rage_baited_cooldown,    "Rage Baited"),
        "poem":                 (attacker.poem_cooldown,           "Poem"),
        "burial_restoration":   (attacker.burial_cooldown,        "Burial Restoration"),
        "crime":                (attacker.crime_cooldown,          "Crime"),
        "magic_trick":          (attacker.magic_trick_cooldown,    "Magic Trick"),
        "brilliant_light":      (attacker.brilliant_light_cooldown,"Brilliant Light"),
        "witch_curse":          (attacker.witch_curse_cooldown,    "Witch's Curse"),
        "domination":           (attacker.domination_cooldown,     "Domination"),
        "bribe":                (attacker.bribe_cooldown,          "Bribe"),
        # Seq 7
        "observation":          (attacker.observation_cooldown,    "Observation"),
        "therapy":              (attacker.therapy_cooldown,        "Therapy"),
        "water_bullet":         (attacker.water_bullet_cooldown,   "Water Bullet"),
        "gun_shot":             (attacker.gun_shot_cooldown,        "Gun Shot"),
        "sneak_attack":         (attacker.sneak_attack_cooldown,   "Sneak Attack"),
        "sleep_spell":          (attacker.sleep_spell_cooldown,    "Sleep Spell"),
        "summoning_dead":       (attacker.summoning_dead_cooldown, "Summoning Dead"),
        "weapon_throw":         (attacker.weapon_throw_cooldown,   "Weapon Throw"),
        "appraisal":            (attacker.appraisal_cooldown,      "Appraisal"),
        "blood_sucker":         (attacker.blood_sucker_cooldown,   "Blood Sucker"),
        "vine_bomb":            (attacker.vine_bomb_cooldown,      "Vine Bomb"),
        "planting":             (attacker.planting_cooldown,       "Planting"),
        "illegal_trade":        (attacker.illegal_trade_cooldown,  "Illegal Trade"),
        "mental_piercing":      (attacker.mental_piercing_cooldown,"Mental Piercing"),
        "magic_spell":          (attacker.magic_spell_cooldown,    "Magic Spell"),
        # Seq 6
        "faceless":             (attacker.faceless_cooldown,       "Faceless"),
        "prometheus":           (attacker.prometheus_cooldown,     "Prometheus"),
        "scribe":               (attacker.scribe_cooldown,         "Scribe"),
        "hypnotist":            (attacker.hypnotist_cooldown,      "Hypnotist"),
        "notary":               (attacker.notary_cooldown,         "Notary"),
        "wind_blessed":         (attacker.wind_blessed_cooldown,   "Wind Blessed"),
        "rose_bishop":          (attacker.rose_bishop_cooldown,    "Rose Bishop"),
        "polymath":             (attacker.polymath_cooldown,       "Polymath"),
        "ritualistic_reasoning":(attacker.ritualistic_reasoning_cooldown, "Ritualistic Reasoning"),
        "pacification":         (attacker.soul_assurer_cooldown,   "Pacification"),
        "spirit_guide":         (attacker.spirit_guide_cooldown,   "Spirit Guide"),
        "glance_into_fate":     (attacker.glance_fate_cooldown,    "Glance into Fate"),
        "pleasure_witch":       (attacker.pleasure_witch_cooldown, "Pleasure Witch"),
        "fire_ravens":          (attacker.fire_ravens_cooldown,    "Fire Ravens"),
        "conspiracy":           (attacker.conspirer_cooldown,      "Conspiracy"),
        "hurricane_of_light":   (attacker.hurricane_cooldown,     "Hurricane of Light"),
        "scrolls_professor":    (attacker.scrolls_professor_cooldown, "Scrolls Professor"),
        "artisan":              (attacker.artisan_cooldown,        "Artisan"),
        "calamity_priest":      (attacker.calamity_priest_cooldown,"Calamity Priest"),
        "potions_professor":    (attacker.potions_professor_cooldown, "Potions Professor"),
        "biologist":            (attacker.biologist_cooldown,      "Biologist"),
        "zombie":               (attacker.zombie_cooldown,         "Zombie"),
        "devil_form":           (attacker.devil_form_cooldown,     "Devil Form"),
        "distortion":           (attacker.distortion_cooldown,     "Distortion"),
        "judge":                (attacker.judge_cooldown,          "Judge"),
    }
    one_use = {
        "night_boost":    attacker.night_boost_used,
        "moral_freedom":  attacker.moral_freedom_used,
        "power_up_punch": attacker.folk_of_rage_active,
        # gun_shot now uses cooldown, not one-use flag
        "lucky_day":      attacker.lucky_day_used,
        "transformation": attacker.transformation_used,
        "vile_crime":     attacker.vile_crime_used,
        "astrology":      attacker.astrology_used,
    }
    for k, used in one_use.items():
        if key == k and used:
            return f"❌ **{k.replace('_',' ').title()}** already used this battle!"
    if key in checks:
        cd, name = checks[key]
        if cd > 0:
            return f"❌ **{name}** on cooldown for **{cd}** more turn(s)."
    if key == "analyse" and attacker.analyse_uses <= 0:
        return "❌ **Analyse** — no uses remaining!"
    return ""


def _build_pathway_page(attacker: "Fighter", pages: list, idx: int) -> dict:
    """Build the content dict for a pathway page."""
    title, pairs = pages[idx]
    sp_now = attacker.sp
    lines = [f"**🌟 {title}** — SP: `{sp_now}/{attacker.max_sp}`\n"]
    for role_name, ak in pairs:
        info, _ = get_ability_info(ak)
        if not info:
            continue
        if role_name == "_prometheus_stolen":
            cost = attacker.get_ability_cost(info["cost"], ak)
            affd = "✅" if attacker.sp >= cost else "❌"
            lines.append(f"🔥 {affd} **{info['name']}** — `{cost} SP` *(Stolen — 1 use)*\n> {info['description']}")
        elif role_name == "_scribe_copy":
            lines.append(f"📖 ✅ **{info['name']}** — `Free` *(Copied — 1 use)*\n> {info['description']}")
        elif role_name == "_polymath_use":
            cost = attacker.get_ability_cost(info["cost"], ak)
            affd = "✅" if attacker.sp >= cost else "❌"
            lines.append(f"📚 {affd} **{info['name']}** — `{cost} SP` *(Studied — 70% potency)*\n> {info['description']}")
        else:
            seq_num = get_seq_number(role_name)
            if ak == attacker.role_ability and attacker.role_ability_disabled > 0:
                lines.append(f"**Seq {seq_num}** — ~~{info['name']}~~ `[Stripped]`")
                continue
            cost = attacker.get_ability_cost(info["cost"], ak)
            cd_str = _check_cooldown(attacker, ak)
            cost_label = info.get("display_cost") or f"{cost} SP"
            affd = "✅" if (info.get("display_cost") or attacker.sp >= cost) else "❌"
            cd_tag = " *(cd)*" if cd_str else ""
            lines.append(f"**Seq {seq_num}** — {affd} **{info['name']}** — `{cost_label}`{cd_tag}\n> {get_scaled_description(ak, attacker)}")
    return {"content": "\n".join(lines)}


class PathwayPageView(discord.ui.View):
    """Paginated pathway view — dropdown to select pathway, then ability buttons."""
    async def on_error(self, interaction: discord.Interaction, error: Exception, item) -> None:
        await _view_on_error(interaction, error, item)

    def __init__(self, attacker: "Fighter", pages: list, page_idx: int):
        super().__init__(timeout=45)
        self.attacker = attacker
        self.pages = pages
        self.page_idx = page_idx
        self._build()

    def _build(self):
        self.clear_items()
        _, pairs = self.pages[self.page_idx]

        # Only show dropdown if there are 2+ pathways
        if len(self.pages) > 1:
            options = []
            for i, (title, _) in enumerate(self.pages):
                # Extract pathway name from title like "[Fool] Pathway" or "✨ Special"
                pathway_name = None
                if title.startswith("[") and "] Pathway" in title:
                    pathway_name = title[1:title.index("]")]
                emoji = None
                if pathway_name:
                    guild = getattr(self.attacker.user, "guild", None)
                    if guild:
                        emoji = get_pathway_emoji(guild, pathway_name)
                options.append(discord.SelectOption(
                    label=title[:100],
                    value=str(i),
                    default=(i == self.page_idx),
                    emoji=emoji
                ))
            select = discord.ui.Select(
                placeholder="Select Pathway…",
                options=options,
                custom_id="pp_select",
                row=4
            )
            select.callback = self._select_cb
            self.add_item(select)

        # Ability buttons (max 20 to stay under Discord's 25-item limit)
        added = 0
        for role_name, ak in pairs:
            if added >= 20:
                break
            info, _ = get_ability_info(ak)
            if not info:
                continue
            if role_name not in ("_prometheus_stolen", "_scribe_copy", "_polymath_use"):
                if ak == self.attacker.role_ability and self.attacker.role_ability_disabled > 0:
                    continue
            cd_str = "" if role_name.startswith("_") else _check_cooldown(self.attacker, ak)
            if role_name == "_prometheus_stolen":
                label = f"🔥 {info['name']}"[:80]
            elif role_name == "_scribe_copy":
                label = f"📖 {info['name']}"[:80]
            elif role_name == "_polymath_use":
                label = f"📚 {info['name']}"[:80]
            else:
                seq_num = get_seq_number(role_name)
                label = f"Seq {seq_num} — {info['name']}"[:80]
            btn = discord.ui.Button(
                label=label,
                style=discord.ButtonStyle.success if not cd_str else discord.ButtonStyle.secondary,
                custom_id=f"ppv_{ak}_{role_name[:10]}",
                disabled=bool(cd_str),
                row=min(3, added // 5)
            )
            btn.callback = self._ability_cb(ak, role_name)
            self.add_item(btn)
            added += 1

    async def _select_cb(self, interaction: discord.Interaction):
        self.page_idx = int(interaction.data["values"][0])
        self._build()
        await interaction.response.edit_message(
            **_build_pathway_page(self.attacker, self.pages, self.page_idx),
            view=self
        )

    def _ability_cb(self, ability_key: str, role_name: str):
        async def callback(interaction: discord.Interaction):
            self.stop()
            battle = get_battle(interaction.channel.id, interaction.user)
            attacker, _ = get_fighters(interaction.user, battle)
            if role_name == "_prometheus_stolen":
                attacker.prometheus_stolen_ability = None
                await process_action(interaction, "ability", ability_key)
                return
            if role_name == "_scribe_copy":
                attacker.scribe_copied_ability = None
                attacker.scribe_copy_pending = True
                await process_action(interaction, "ability", ability_key)
                return
            if role_name == "_polymath_use":
                attacker.polymath_pending = True   # flag 70% potency for this turn
                attacker.polymath_cooldown = 2
                attacker.polymath_ability = None
                await process_action(interaction, "ability", ability_key)
                return
            if ability_key == "ritual":         return await show_ritual_menu(interaction)
            if ability_key == "memorise":       return await show_memorise_menu(interaction)
            if ability_key == "magic_trick":    return await show_magic_trick_menu(interaction)
            if ability_key == "bribe":          return await show_bribe_menu(interaction)
            if ability_key == "instigation":    return await show_provocation_menu(interaction, block=False)
            if ability_key == "reading":        return await show_reading_menu(interaction)
            if ability_key == "witch_curse":    return await show_witch_curse_menu(interaction)
            if ability_key == "analyse":        return await show_analyse_menu(interaction)
            if ability_key == "hypnotist":      return await show_hypnotist_menu(interaction)
            if ability_key == "notary":         return await show_notary_menu(interaction)
            if ability_key == "rose_bishop":    return await show_rose_bishop_menu(interaction)
            if ability_key == "protection":     return await show_protection_menu(interaction)
            if ability_key == "potions_professor": return await show_potions_menu(interaction)
            if ability_key == "pacification":   return await show_pacification_menu(interaction)
            await process_action(interaction, "ability", ability_key)
        return callback

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        battle = get_battle(interaction.channel.id, interaction.user)
        if not battle or interaction.user != battle["turn"]:
            await safe_respond(interaction, "❌ It's not your turn!")
            return False
        return True


class PathwayMenuView(discord.ui.View):
    """Dynamic view with one button per available pathway ability (Seq 9, Seq 8, etc.)."""
    async def on_error(self, interaction: discord.Interaction, error: Exception, item) -> None:
        await _view_on_error(interaction, error, item)

    def __init__(self, attacker: Fighter, role_ability_pairs: list):
        super().__init__(timeout=30)
        self.attacker = attacker
        for role_name, ak in role_ability_pairs:
            info, _ = get_ability_info(ak)
            if not info:
                continue
            # Special synthetic entries for Prometheus stolen / Scribe copied / Polymath studied
            if role_name == "_prometheus_stolen":
                label = f"🔥 Stolen — {info['name']}"[:80]
                cd_str = ""
            elif role_name == "_scribe_copy":
                label = f"📖 Copy — {info['name']}"[:80]
                cd_str = ""
            elif role_name == "_polymath_use":
                label = f"📚 Studied — {info['name']}"[:80]
                cd_str = ""
            else:
                seq_num = get_seq_number(role_name)
                label = f"Seq {seq_num} — {info['name']}"[:80]
                cd_str = _check_cooldown(attacker, ak)
            btn = discord.ui.Button(
                label=label,
                style=discord.ButtonStyle.success if not cd_str else discord.ButtonStyle.secondary,
                custom_id=f"pathway_{ak}_{role_name[:12]}",
                disabled=bool(cd_str)
            )
            btn.callback = self._make_callback(ak, role_name)
            self.add_item(btn)

    def _make_callback(self, ability_key: str, role_name: str = ""):
        async def callback(interaction: discord.Interaction):
            self.stop()
            battle = get_battle(interaction.channel.id, interaction.user)
            attacker, _ = get_fighters(interaction.user, battle)
            # Prometheus: consume stolen ability on use
            if role_name == "_prometheus_stolen":
                attacker.prometheus_stolen_ability = None
                await process_action(interaction, "ability", ability_key)
                return
            # Scribe: consume copied ability on use (free, skip SP check)
            if role_name == "_scribe_copy":
                attacker.scribe_copied_ability = None
                attacker.scribe_copy_pending = True
                await process_action(interaction, "ability", ability_key)
                return
            # Polymath: use studied ability at 70% potency (flag set, cleared after use)
            if role_name == "_polymath_use":
                attacker.polymath_pending = True   # flag 70% potency for this turn
                attacker.polymath_cooldown = 2
                attacker.polymath_ability = None
                await process_action(interaction, "ability", ability_key)
                return
            # Sub-menus that need a picker first
            if ability_key == "ritual":
                return await show_ritual_menu(interaction)
            if ability_key == "memorise":
                return await show_memorise_menu(interaction)
            if ability_key == "magic_trick":
                return await show_magic_trick_menu(interaction)
            if ability_key == "bribe":
                return await show_bribe_menu(interaction)
            if ability_key == "instigation":
                return await show_provocation_menu(interaction, block=False)
            if ability_key == "reading":
                return await show_reading_menu(interaction)
            if ability_key == "witch_curse":
                return await show_witch_curse_menu(interaction)
            if ability_key == "analyse":
                return await show_analyse_menu(interaction)
            if ability_key == "hypnotist":
                return await show_hypnotist_menu(interaction)
            if ability_key == "notary":
                return await show_notary_menu(interaction)
            if ability_key == "rose_bishop":
                return await show_rose_bishop_menu(interaction)
            if ability_key == "potions_professor":
                return await show_potions_menu(interaction)
            if ability_key == "pacification":
                return await show_pacification_menu(interaction)
            await process_action(interaction, "ability", ability_key)
        return callback

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        battle = get_battle(interaction.channel.id, interaction.user)
        if not battle or interaction.user != battle["turn"]:
            await safe_respond(interaction, "❌ It's not your turn!")
            return False
        return True


# Keep PathwayUseView as alias for backwards compat
PathwayUseView = PathwayMenuView


class AbilityView(discord.ui.View):
    def __init__(self, attacker: Fighter, owned_spells: list):
        super().__init__(timeout=30)
        for key in owned_spells:
            info, _ = get_ability_info(key)
            if not info:
                continue
            btn = discord.ui.Button(
                label=info["name"][:80],
                style=discord.ButtonStyle.primary,
                custom_id=f"ability_{key}"
            )
            btn.callback = self._make_callback(key)
            self.add_item(btn)

    async def on_error(self, interaction: discord.Interaction, error: Exception, item) -> None:
        await _view_on_error(interaction, error, item)

    def _make_callback(self, spell_key):
        async def callback(interaction: discord.Interaction):
            if spell_key == "ritual":
                await show_ritual_menu(interaction)
            elif spell_key == "memorise":
                await show_memorise_menu(interaction)
            else:
                await process_action(interaction, "ability", spell_key)
        return callback

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        battle = get_battle(interaction.channel.id, interaction.user)
        if not battle or interaction.user != battle["turn"]:
            await safe_respond(interaction, "❌ It's not your turn!")
            return False
        return True



class RitualView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=30)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        battle = get_battle(interaction.channel.id, interaction.user)
        if not battle or interaction.user != battle["turn"]:
            await safe_respond(interaction, "❌ It's not your turn!")
            return False
        return True

    @discord.ui.button(label="💪 Strength", style=discord.ButtonStyle.danger)
    async def strength_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await process_action(interaction, "ability", "ritual", ritual_choice="strength")

    @discord.ui.button(label="🔮 Divination", style=discord.ButtonStyle.primary)
    async def divination_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await process_action(interaction, "ability", "ritual", ritual_choice="divination")

    @discord.ui.button(label="🌿 Blessing", style=discord.ButtonStyle.success)
    async def blessing_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await process_action(interaction, "ability", "ritual", ritual_choice="blessing")


class MemoriseView(discord.ui.View):
    async def on_error(self, interaction: discord.Interaction, error: Exception, item) -> None:
        await _view_on_error(interaction, error, item)

    def __init__(self, attacker: Fighter, guild_id=0):
        super().__init__(timeout=60)
        self.attacker = attacker
        # Include purchased spells AND all unlocked pathway abilities
        owned = list(get_economy(attacker.user.id, guild_id=guild_id)["spells"])
        for role_name in attacker.all_roles:
            ak = ROLE_ABILITIES.get(role_name)
            if ak and ak not in owned and ak != "memorise":
                owned.append(ak)
        added = 0
        for key in owned:
            if key == "memorise":
                continue
            info, _ = get_ability_info(key)
            if not info:
                continue
            if added >= 24:  # Discord limit
                break
            btn = discord.ui.Button(
                label=info["name"][:80],
                style=discord.ButtonStyle.primary,
                custom_id=f"mem_{key}"
            )
            btn.callback = self._make_callback(key)
            self.add_item(btn)
            added += 1

    def _make_callback(self, spell_key):
        async def callback(interaction: discord.Interaction):
            if interaction.user != self.attacker.user:
                return await safe_respond(interaction, "❌ This isn't yours!")
            self.attacker.memorise_ability = spell_key
            self.attacker.memorise_turns = random.randint(1, 2)
            info, _ = get_ability_info(spell_key)
            self.stop()
            await interaction.response.send_message(
                f"📖 Memorised **{info['name']}** — SP-free for **{self.attacker.memorise_turns}** turn(s)!\n"
                f"Use it via your Pathway or Abilities menu — cost will show as **0 SP**.",
                ephemeral=True)
        return callback


class MagicTrickView(discord.ui.View):
    """Sub-menu for Trickmaster magic trick."""
    async def on_error(self, interaction: discord.Interaction, error: Exception, item) -> None:
        await _view_on_error(interaction, error, item)

    def __init__(self):
        super().__init__(timeout=30)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        battle = get_battle(interaction.channel.id, interaction.user)
        if not battle or interaction.user != battle["turn"]:
            await safe_respond(interaction, "❌ It's not your turn!")
            return False
        return True

    @discord.ui.button(label="🎲 Random Element", style=discord.ButtonStyle.primary)
    async def random_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await process_action(interaction, "ability", "magic_trick", ritual_choice="random")

    @discord.ui.button(label="🏃 Escape Trick", style=discord.ButtonStyle.secondary)
    async def escape_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await process_action(interaction, "ability", "magic_trick", ritual_choice="escape")


class BribeView(discord.ui.View):
    """Sub-menu for Briber — player chooses which bribe effect to apply."""
    async def on_error(self, interaction: discord.Interaction, error: Exception, item) -> None:
        await _view_on_error(interaction, error, item)

    def __init__(self):
        super().__init__(timeout=30)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        battle = get_battle(interaction.channel.id, interaction.user)
        if not battle or interaction.user != battle["turn"]:
            await safe_respond(interaction, "❌ It's not your turn!")
            return False
        return True

    @discord.ui.button(label="⚔️ Weaken (−40% next attack)", style=discord.ButtonStyle.danger)
    async def weaken_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await process_action(interaction, "ability", "bribe", ritual_choice="weaken")

    @discord.ui.button(label="💋 Charm (can't attack 2 turns)", style=discord.ButtonStyle.primary)
    async def charm_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await process_action(interaction, "ability", "bribe", ritual_choice="charm")

    @discord.ui.button(label="🔗 Connect (share 40% dmg taken)", style=discord.ButtonStyle.secondary)
    async def connect_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await process_action(interaction, "ability", "bribe", ritual_choice="connect")


class ProvocationView(discord.ui.View):
    """Shows opponent's moves so Provoker/Instigator can pick one."""
    async def on_error(self, interaction: discord.Interaction, error: Exception, item) -> None:
        await _view_on_error(interaction, error, item)

    def __init__(self, attacker: Fighter, defender: Fighter, battle, block: bool):
        super().__init__(timeout=30)
        self.attacker = attacker
        self.defender = defender
        self.block = block  # True = provocation (block), False = instigation (force)
        self.seq_blocked = False

        # LOTM rule: you cannot instigate/provoke someone more than 2 sequences
        # stronger than you. Sequence numbers are inverted — lower = stronger.
        # e.g. attacker Seq 8, defender Seq 6 → gap = 2, borderline OK
        #      attacker Seq 8, defender Seq 5 → gap = 3, BLOCKED
        atk_seq = get_seq_number(attacker.role) if attacker.role else 9
        def_seq = get_seq_number(defender.role) if defender.role else 9
        seq_gap = atk_seq - def_seq  # positive = attacker is weaker (higher number)
        if seq_gap > 2:
            self.seq_blocked = True
            return  # no buttons added — handled in process_action

        actions = get_available_actions(defender, battle)
        # Only show abilities the defender can actually use at their sequence level
        # Filter out abilities from sequences stronger than the defender's current seq
        filtered = []
        for key in actions:
            if key in ("attack", "special"):
                filtered.append(key)
                continue
            info, is_role = get_ability_info(key)
            if not info:
                continue
            # Only include abilities that belong to defender's current seq or weaker
            if is_role:
                # Check the role name sequence number
                for role_name in defender.all_roles:
                    if ROLE_ABILITIES.get(role_name) == key or ROLE_ABILITIES_2.get(role_name) == key:
                        # Extract seq number from role name e.g. "Seq 8"
                        import re
                        m = re.search(r'Seq (\d+)', role_name)
                        if m and int(m.group(1)) >= def_seq:
                            filtered.append(key)
                        break
            else:
                filtered.append(key)  # purchased spells always includeable

        for key in filtered[:5]:
            info, is_role = get_ability_info(key)
            label = info["name"][:40] if info else key
            if key == "attack":
                label = "⚔️ Attack"
            elif key == "special":
                label = "⚡ Strike"
            btn = discord.ui.Button(
                label=label,
                style=discord.ButtonStyle.danger if block else discord.ButtonStyle.primary,
                custom_id=f"prov_{key}"
            )
            btn.callback = self._make_callback(key)
            self.add_item(btn)

    def _make_callback(self, action_key: str):
        async def callback(interaction: discord.Interaction):
            if interaction.user != self.attacker.user:
                return await safe_respond(interaction, "❌ This isn't yours!")
            self.stop()
            if self.block:
                await process_action(interaction, "ability", "provocation", ritual_choice=action_key)
            else:
                await process_action(interaction, "ability", "instigation", ritual_choice=action_key)
        return callback

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        battle = get_battle(interaction.channel.id, interaction.user)
        if not battle or interaction.user != battle["turn"]:
            await safe_respond(interaction, "❌ It's not your turn!")
            return False
        return True


class ReadingView(discord.ui.View):
    """Telepathist picks which move they think opponent will use."""
    async def on_error(self, interaction: discord.Interaction, error: Exception, item) -> None:
        await _view_on_error(interaction, error, item)

    def __init__(self, attacker: Fighter, defender: Fighter, battle):
        super().__init__(timeout=30)
        self.attacker = attacker
        actions = get_available_actions(defender, battle)
        for key in actions[:5]:
            info, _ = get_ability_info(key)
            label = info["name"][:40] if info else key
            if key == "attack":
                label = "⚔️ Attack"
            elif key == "special":
                label = "⚡ Strike"
            btn = discord.ui.Button(
                label=label,
                style=discord.ButtonStyle.primary,
                custom_id=f"read_{key}"
            )
            btn.callback = self._make_callback(key)
            self.add_item(btn)

    def _make_callback(self, action_key: str):
        async def callback(interaction: discord.Interaction):
            if interaction.user != self.attacker.user:
                return await safe_respond(interaction, "❌ This isn't yours!")
            self.stop()
            await process_action(interaction, "ability", "reading", ritual_choice=action_key)
        return callback

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        battle = get_battle(interaction.channel.id, interaction.user)
        if not battle or interaction.user != battle["turn"]:
            await safe_respond(interaction, "❌ It's not your turn!")
            return False
        return True


async def show_ritual_menu(interaction: discord.Interaction):
    battle = get_battle(interaction.channel.id, interaction.user)
    attacker, _ = get_fighters(interaction.user, battle)
    ab = ABILITIES["ritual"]
    actual_cost = attacker.get_ability_cost(ab["cost"], "ritual")
    if attacker.sp < actual_cost:
        return await interaction.response.send_message(
            f"❌ Not enough spirit! Costs **{actual_cost} SP** — you have **{attacker.sp} SP**.", ephemeral=True)
    await interaction.response.send_message(
        f"**🕯️ Ritualistic Magic** — `{actual_cost} SP`\n\n"
        f"💪 **Strength** — Boost your next attack.\n"
        f"🔮 **Divination** — High dodge chance for next incoming attack.\n"
        f"🌿 **Blessing** — Heal now, gain strength next turn.",
        view=RitualView(), ephemeral=True)


# ── Seq 6 sub-menu views ──────────────────────────────────

class HypnotistView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=30)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        battle = get_battle(interaction.channel.id, interaction.user)
        if not battle or interaction.user != battle["turn"]:
            await safe_respond(interaction, "❌ It's not your turn!")
            return False
        return True

    @discord.ui.button(label="💥 Self-Damage (20)", style=discord.ButtonStyle.danger)
    async def self_dmg_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await process_action(interaction, "ability", "hypnotist", ritual_choice="self_damage")

    @discord.ui.button(label="⏭️ Skip Turn", style=discord.ButtonStyle.secondary)
    async def skip_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await process_action(interaction, "ability", "hypnotist", ritual_choice="skip_turn")


class NotaryView(discord.ui.View):
    async def on_error(self, interaction: discord.Interaction, error: Exception, item) -> None:
        await _view_on_error(interaction, error, item)

    def __init__(self):
        super().__init__(timeout=30)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        battle = get_battle(interaction.channel.id, interaction.user)
        if not battle or interaction.user != battle["turn"]:
            await safe_respond(interaction, "❌ It's not your turn!")
            return False
        return True

    @discord.ui.button(label="💪 Buff Self (+50% dmg)", style=discord.ButtonStyle.success)
    async def buff_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await process_action(interaction, "ability", "notary", ritual_choice="buff_self")

    @discord.ui.button(label="🔻 Debuff Opponent (−50%)", style=discord.ButtonStyle.danger)
    async def debuff_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await process_action(interaction, "ability", "notary", ritual_choice="debuff_opponent")


class RoseBishopView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=30)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        battle = get_battle(interaction.channel.id, interaction.user)
        if not battle or interaction.user != battle["turn"]:
            await safe_respond(interaction, "❌ It's not your turn!")
            return False
        return True

    @discord.ui.button(label="💣 Flesh Bomb (30 dmg, −20 HP, −15 SP)", style=discord.ButtonStyle.danger)
    async def flesh_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await process_action(interaction, "ability", "rose_bishop", ritual_choice="flesh_bomb")

    @discord.ui.button(label="💉 Blood Regen (+40 HP, −25 SP)", style=discord.ButtonStyle.primary)
    async def regen_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await process_action(interaction, "ability", "rose_bishop", ritual_choice="blood_regen")


class ProtectionView(discord.ui.View):
    async def on_error(self, interaction: discord.Interaction, error: Exception, item) -> None:
        await _view_on_error(interaction, error, item)

    def __init__(self):
        super().__init__(timeout=30)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        battle = get_battle(interaction.channel.id, interaction.user)
        if not battle or interaction.user != battle["turn"]:
            await safe_respond(interaction, "❌ It's not your turn!")
            return False
        return True

    @discord.ui.button(label="⚔️ Active (until next attack)", style=discord.ButtonStyle.primary)
    async def active_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await process_action(interaction, "ability", "protection", ritual_choice="active")

    @discord.ui.button(label="🔄 Maintenance (toggle, 3 turn cd)", style=discord.ButtonStyle.secondary)
    async def maintenance_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await process_action(interaction, "ability", "protection", ritual_choice="maintenance")


class PotionsView(discord.ui.View):
    async def on_error(self, interaction: discord.Interaction, error: Exception, item) -> None:
        await _view_on_error(interaction, error, item)

    def __init__(self):
        super().__init__(timeout=30)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        battle = get_battle(interaction.channel.id, interaction.user)
        if not battle or interaction.user != battle["turn"]:
            await safe_respond(interaction, "❌ It's not your turn!")
            return False
        return True

    @discord.ui.button(label="🧪 Cure (Heal HP/SP)", style=discord.ButtonStyle.success)
    async def cure_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await process_action(interaction, "ability", "potions_professor", ritual_choice="cure")

    @discord.ui.button(label="☠️ Poison (50 dmg + −20 SP)", style=discord.ButtonStyle.danger)
    async def poison_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await process_action(interaction, "ability", "potions_professor", ritual_choice="poison")


async def show_hypnotist_menu(interaction: discord.Interaction):
    battle = get_battle(interaction.channel.id, interaction.user)
    attacker, _ = get_fighters(interaction.user, battle)
    info = ROLE_ABILITY_INFO["hypnotist"]
    cost = attacker.get_ability_cost(info["cost"], "hypnotist")
    if attacker.sp < cost:
        return await interaction.response.send_message(
            f"❌ Not enough spirit! Costs **{cost} SP**.", ephemeral=True)
    await process_action(interaction, "ability", "hypnotist", ritual_choice="both")


async def show_notary_menu(interaction: discord.Interaction):
    battle = get_battle(interaction.channel.id, interaction.user)
    attacker, _ = get_fighters(interaction.user, battle)
    info = ROLE_ABILITY_INFO["notary"]
    cost = attacker.get_ability_cost(info["cost"], "notary")
    if attacker.sp < cost:
        return await interaction.response.send_message(
            f"❌ Not enough spirit! Costs **{cost} SP**.", ephemeral=True)
    await interaction.response.send_message(
        f"**📜 Notary** — `{cost} SP`\n\n"
        f"Proclaim in the name of your god — choose your decree:",
        view=NotaryView(), ephemeral=True)


async def show_rose_bishop_menu(interaction: discord.Interaction):
    battle = get_battle(interaction.channel.id, interaction.user)
    attacker, _ = get_fighters(interaction.user, battle)
    info = ROLE_ABILITY_INFO["rose_bishop"]
    cost = attacker.get_ability_cost(0, "rose_bishop")  # cost varies by choice
    await interaction.response.send_message(
        f"**🌹 Rose Bishop** — Flesh & Blood Magic:\n\n"
        f"💣 **Flesh Bomb** — 30 dmg, costs **20 HP + 15 SP**\n"
        f"💉 **Blood Regen** — restore 40 SP, costs **25 SP**",
        view=RoseBishopView(), ephemeral=True)


async def show_protection_menu(interaction: discord.Interaction):
    battle = get_battle(interaction.channel.id, interaction.user)
    attacker, _ = get_fighters(interaction.user, battle)
    if attacker.protection_active:
        # Already active — handle toggle/cancel directly without showing menu
        await process_action(interaction, "ability", "protection")
        return
    if attacker.protection_cooldown > 0:
        await interaction.response.send_message(
            f"🛡️ **Protection** on cooldown for **{attacker.protection_cooldown}** turn(s)!", ephemeral=True)
        return
    cost = attacker.get_ability_cost(40, "protection")
    if attacker.sp < cost:
        return await interaction.response.send_message(
            f"❌ Not enough spirit! Costs **{cost} SP**.", ephemeral=True)
    reduction = seq_val(attacker, 80)
    await interaction.response.send_message(
        f"**🛡️ Protection** — `{cost} SP`\n\n"
        f"Give up offense (−50% damage dealt) in exchange for **{reduction} damage reduction** "
        f"(drops to **{seq_val(attacker, 10)}** for 1 turn after any attack).\n\n"
        f"Choose your mode:",
        view=ProtectionView(), ephemeral=True)


async def show_potions_menu(interaction: discord.Interaction):
    battle = get_battle(interaction.channel.id, interaction.user)
    attacker, defender = get_fighters(interaction.user, battle)
    info = ROLE_ABILITY_INFO["potions_professor"]
    cost = attacker.get_ability_cost(info["cost"], "potions_professor")
    if attacker.sp < cost:
        return await interaction.response.send_message(
            f"❌ Not enough spirit! Costs **{cost} SP**.", ephemeral=True)
    await interaction.response.send_message(
        f"**⚗️ Potions Professor** — `{cost} SP`\n\n"
        f"🧪 **Cure** — Restore HP & SP (more if no status effects)\n"
        f"☠️ **Poison** — Deal 50 damage + drain 20 SP from **{defender.user.display_name}**",
        view=PotionsView(), ephemeral=True)


class PacificationView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=30)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        battle = get_battle(interaction.channel.id, interaction.user)
        if not battle or interaction.user != battle["turn"]:
            await safe_respond(interaction, "❌ It's not your turn!")
            return False
        return True

    @discord.ui.button(label="💥 Strip Enemy Buffs", style=discord.ButtonStyle.danger)
    async def strip_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await process_action(interaction, "ability", "pacification", ritual_choice="strip_enemy")

    @discord.ui.button(label="✨ Cleanse My Debuffs", style=discord.ButtonStyle.success)
    async def cleanse_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await process_action(interaction, "ability", "pacification", ritual_choice="cleanse_self")


async def show_pacification_menu(interaction: discord.Interaction):
    battle = get_battle(interaction.channel.id, interaction.user)
    attacker, _ = get_fighters(interaction.user, battle)
    info = ROLE_ABILITY_INFO["pacification"]
    cost = attacker.get_ability_cost(info["cost"], "pacification")
    if attacker.sp < cost:
        return await interaction.response.send_message(
            f"❌ Not enough spirit! Costs **{cost} SP**.", ephemeral=True)
    await interaction.response.send_message(
        f"**☮️ Pacification** — `{cost} SP`\n\n"
        f"💥 **Strip Enemy Buffs** — Remove all active positive effects from your opponent.\n"
        f"✨ **Cleanse My Debuffs** — Clear all negative status effects from yourself.\n\n"
        f"*(6 turn cooldown)*",
        view=PacificationView(), ephemeral=True)


class JudgeView(discord.ui.View):
    """Judge picks one of opponent's moves to restrict."""
    async def on_error(self, interaction: discord.Interaction, error: Exception, item) -> None:
        await _view_on_error(interaction, error, item)

    def __init__(self, attacker: Fighter, defender: Fighter, battle):
        super().__init__(timeout=30)
        self.attacker = attacker
        actions = get_available_actions(defender, battle)
        for key in actions[:5]:
            info, _ = get_ability_info(key)
            if key == "attack":
                label = "⚔️ Attack"
            elif key == "special":
                label = "⚡ Strike"
            else:
                label = info["name"][:40] if info else key
            btn = discord.ui.Button(
                label=label,
                style=discord.ButtonStyle.danger,
                custom_id=f"judge_{key}"
            )
            btn.callback = self._make_callback(key)
            self.add_item(btn)

    def _make_callback(self, action_key: str):
        async def callback(interaction: discord.Interaction):
            if interaction.user != self.attacker.user:
                return await safe_respond(interaction, "❌ This isn't yours!")
            self.stop()
            await process_action(interaction, "ability", "judge", ritual_choice=action_key)
        return callback

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        battle = get_battle(interaction.channel.id, interaction.user)
        if not battle or interaction.user != battle["turn"]:
            await safe_respond(interaction, "❌ It's not your turn!")
            return False
        return True


async def show_judge_menu(interaction: discord.Interaction):
    battle = get_battle(interaction.channel.id, interaction.user)
    attacker, defender = get_fighters(interaction.user, battle)
    info = ROLE_ABILITY_INFO["judge"]
    cost = attacker.get_ability_cost(info["cost"], "judge")
    if attacker.sp < cost:
        return await interaction.response.send_message(
            f"❌ Not enough spirit! Costs **{cost} SP**.", ephemeral=True)
    await interaction.response.send_message(
        f"**⚖️ Judge** — Pick a move to restrict on **{defender.user.display_name}**:\n"
        f"*(If they use it anyway, they take **30 damage** as backfire)*",
        view=JudgeView(attacker, defender, battle), ephemeral=True)


async def show_memorise_menu(interaction: discord.Interaction):
    battle = get_battle(interaction.channel.id, interaction.user)
    attacker, _ = get_fighters(interaction.user, battle)
    if attacker.memorise_ability and attacker.memorise_turns > 0:
        info, _ = get_ability_info(attacker.memorise_ability)
        return await interaction.response.send_message(
            f"📖 Already memorised **{info['name']}** — **{attacker.memorise_turns}** SP-free turn(s) left.",
            ephemeral=True)
    await interaction.response.send_message(
        "**📖 Memorise** — Pick an ability to use SP-free for 1-2 turns:",
        view=MemoriseView(attacker, guild_id=interaction.guild_id), ephemeral=True)


async def show_magic_trick_menu(interaction: discord.Interaction):
    battle = get_battle(interaction.channel.id, interaction.user)
    attacker, _ = get_fighters(interaction.user, battle)
    info = ROLE_ABILITY_INFO["magic_trick"]
    cost = attacker.get_ability_cost(info["cost"], "magic_trick")
    if attacker.sp < cost:
        return await interaction.response.send_message(
            f"❌ Not enough spirit! Costs **{cost} SP** — you have **{attacker.sp} SP**.", ephemeral=True)
    await interaction.response.send_message(
        f"**🎩 Magic Trick** — `{cost} SP`\n\n"
        f"🎲 **Random Element** — Apply freeze, burn, or stun at random for 2 turns.\n"
        f"🏃 **Escape Trick** — Attempt to flee based on speed difference.",
        view=MagicTrickView(), ephemeral=True)


async def show_bribe_menu(interaction: discord.Interaction):
    battle = get_battle(interaction.channel.id, interaction.user)
    attacker, defender = get_fighters(interaction.user, battle)
    if attacker.bribe_cooldown > 0:
        return await interaction.response.send_message(
            f"❌ **Bribe** is on cooldown for **{attacker.bribe_cooldown}** more turn(s)!", ephemeral=True)
    cost_soli = 70000
    bal = get_pounds(attacker.user.id, guild_id=battle["guild_id"])
    if bal < cost_soli:
        return await interaction.response.send_message(
            f"❌ **Bribe** needs **{cost_soli:,} soli** — you only have **{bal:,}**.", ephemeral=True)
    spirit_diff = max(0, defender.stats.get("spirit", 0) - attacker.stats.get("spirit", 0))
    fail_pct = int(spirit_diff * 10)
    await interaction.response.send_message(
        f"**💰 Bribe** — `100,000 soli`\n\n"
        f"Choose your bribe for **{defender.user.display_name}**:\n"
        f"*(Fail chance: **{fail_pct}%** based on their spirit stat)*",
        view=BribeView(), ephemeral=True)


async def show_provocation_menu(interaction: discord.Interaction, block: bool):
    battle = get_battle(interaction.channel.id, interaction.user)
    attacker, defender = get_fighters(interaction.user, battle)
    ability_key = "provocation" if block else "instigation"
    info = ROLE_ABILITY_INFO[ability_key]
    cost = attacker.get_ability_cost(info["cost"], ability_key)
    if block and attacker.provocation_cooldown > 0:
        return await interaction.response.send_message(
            f"❌ **Provocation** on cooldown for **{attacker.provocation_cooldown}** more turn(s)! *(4t cd)*", ephemeral=True)
    if attacker.sp < cost:
        return await interaction.response.send_message(
            f"❌ Not enough spirit! Costs **{cost} SP**.", ephemeral=True)

    # LOTM sequence gap check — can't instigate/provoke someone more than 2 seqs stronger
    atk_seq = get_seq_number(attacker.role) if attacker.role else 9
    def_seq = get_seq_number(defender.role) if defender.role else 9
    if atk_seq - def_seq > 2:
        return await interaction.response.send_message(
            f"❌ **{info['name']} failed!**\n"
            f"**{defender.user.display_name}** is **Seq {def_seq}** — the gap is too great. "
            f"A Beyonder cannot easily impose their will on someone {atk_seq - def_seq} sequences above them.",
            ephemeral=True)

    view = ProvocationView(attacker, defender, battle, block)
    action_word = "block" if block else "force"
    await interaction.response.send_message(
        f"**{info['name']}** — Pick a move to {action_word} from **{defender.user.display_name}**:\n"
        f"*(Enemy skips **1 turn** and loses **25 SP**)*",
        view=view, ephemeral=True)


async def show_reading_menu(interaction: discord.Interaction):
    battle = get_battle(interaction.channel.id, interaction.user)
    attacker, defender = get_fighters(interaction.user, battle)
    info = ROLE_ABILITY_INFO["reading"]
    cost = attacker.get_ability_cost(info["cost"], "reading")
    if attacker.sp < cost:
        return await interaction.response.send_message(
            f"❌ Not enough spirit! Costs **{cost} SP**.", ephemeral=True)
    await interaction.response.send_message(
        f"**🔭 Reading** — Which move do you think **{defender.user.display_name}** will use?\n"
        f"*(If correct, it misses)*",
        view=ReadingView(attacker, defender, battle), ephemeral=True)


class WitchCurseView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=30)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        battle = get_battle(interaction.channel.id, interaction.user)
        if not battle or interaction.user != battle["turn"]:
            await safe_respond(interaction, "❌ It's not your turn!")
            return False
        return True

    @discord.ui.button(label="🔥 Black Flames", style=discord.ButtonStyle.danger)
    async def flames_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await process_action(interaction, "ability", "witch_curse", ritual_choice="black_flames")

    @discord.ui.button(label="🧊 Frost", style=discord.ButtonStyle.primary)
    async def frost_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await process_action(interaction, "ability", "witch_curse", ritual_choice="frost")

    @discord.ui.button(label="🪆 Voodoo", style=discord.ButtonStyle.secondary)
    async def voodoo_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await process_action(interaction, "ability", "witch_curse", ritual_choice="voodoo")


class AnalyseView(discord.ui.View):
    """Pick one of defender's abilities to permanently reduce its damage by 30%."""
    async def on_error(self, interaction: discord.Interaction, error: Exception, item) -> None:
        await _view_on_error(interaction, error, item)

    def __init__(self, attacker: Fighter, defender: Fighter, battle):
        super().__init__(timeout=30)
        self.attacker = attacker
        actions = get_available_actions(defender, battle)
        # Filter to actual abilities only (exclude basic attack/special)
        ability_actions = [k for k in actions if k not in ("attack", "special")]
        added = 0
        for key in ability_actions:
            if added >= 5:
                break
            info, _ = get_ability_info(key)
            label = info["name"][:40] if info else key
            btn = discord.ui.Button(label=label, style=discord.ButtonStyle.primary, custom_id=f"analyse_{key}", row=min(4, added // 3))
            btn.callback = self._make_callback(key)
            self.add_item(btn)
            added += 1

    def _make_callback(self, action_key: str):
        async def callback(interaction: discord.Interaction):
            if interaction.user != self.attacker.user:
                return await safe_respond(interaction, "❌ This isn't yours!")
            self.stop()
            await process_action(interaction, "ability", "analyse", ritual_choice=action_key)
        return callback

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        battle = get_battle(interaction.channel.id, interaction.user)
        if not battle or interaction.user != battle["turn"]:
            await safe_respond(interaction, "❌ It's not your turn!")
            return False
        return True


async def show_witch_curse_menu(interaction: discord.Interaction):
    battle = get_battle(interaction.channel.id, interaction.user)
    attacker, _ = get_fighters(interaction.user, battle)
    info = ROLE_ABILITY_INFO["witch_curse"]
    cost = attacker.get_ability_cost(info["cost"], "witch_curse")
    if attacker.sp < cost:
        return await interaction.response.send_message(
            f"❌ Not enough spirit! Costs **{cost} SP**.", ephemeral=True)
    ritual_choice = random.choice(["black_flames", "frost", "voodoo"])
    await process_action(interaction, "ability", "witch_curse", ritual_choice=ritual_choice)


async def show_analyse_menu(interaction: discord.Interaction):
    battle = get_battle(interaction.channel.id, interaction.user)
    attacker, defender = get_fighters(interaction.user, battle)
    info = ROLE_ABILITY_INFO["analyse"]
    cost = attacker.get_ability_cost(info["cost"], "analyse")
    if attacker.sp < cost:
        return await interaction.response.send_message(
            f"❌ Not enough spirit! Costs **{cost} SP**.", ephemeral=True)
    if attacker.analyse_uses <= 0:
        return await interaction.response.send_message("❌ No analyse uses remaining!", ephemeral=True)
    await interaction.response.send_message(
        f"**🧐 Analyse** — Pick a skill from **{defender.user.display_name}** to permanently reduce by 30%.\n"
        f"*({attacker.analyse_uses} use(s) remaining)*",
        view=AnalyseView(attacker, defender, battle), ephemeral=True)


class ChallengeView(discord.ui.View):
    def __init__(self, challenger, opponent, channel_id):
        super().__init__(timeout=60)
        self.challenger = challenger
        self.opponent = opponent
        self.channel_id = channel_id
        self.battle_key = _battle_key(channel_id, challenger.id, opponent.id)

    async def on_timeout(self):
        battles.pop(self.battle_key, None)
        channel = bot.get_channel(self.channel_id)
        if channel:
            embed = discord.Embed(
                title="⌛ Challenge Expired",
                description=(
                    f"**{self.challenger.display_name}** waited bravely, but "
                    f"**{self.opponent.display_name}** never answered.\n"
                    f"The arena stands empty."
                ),
                color=0x95A5A6
            )
            await channel.send(embed=embed)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user != self.opponent:
            await safe_respond(interaction, "❌ This challenge isn't for you!")
            return False
        return True

    @discord.ui.button(label="✅ Accept", style=discord.ButtonStyle.success)
    async def accept_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        battle = battles.get(self.battle_key)
        if not battle or battle["accepted"]:
            return await interaction.response.send_message("❌ No pending challenge.", ephemeral=True)
        battle["accepted"] = True
        self.stop()
        first = battle["turn"]
        c = battle["challenger"]
        o = battle["opponent"]

        def fighter_intro(f):
            if f.role:
                pathway = get_pathway_name(f.role)
                seq_num = get_seq_number(f.role)
                role_short = f.role.split("—")[-1].strip() if "—" in f.role else ""
                seq_label = f"**{pathway}** Seq {seq_num} — *{role_short}*"
            else:
                seq_label = "*No pathway*"
            stats = f.stats
            return (
                f"{seq_label}\n"
                f"❤️ `{f.max_hp} HP`  ✨ `{f.max_sp} SP`  ⚔️ `{stats['attack']} Atk`  🍀 `{stats['luck']} Luck`"
            )

        embed = discord.Embed(
            title="⚔️ The Battle Begins!",
            description=f"⚡ **{first.display_name}** moves first!",
            color=0xE74C3C
        )
        embed.add_field(name=f"🔵 {c.user.display_name}", value=fighter_intro(c), inline=True)
        embed.add_field(name=f"🔴 {o.user.display_name}", value=fighter_intro(o), inline=True)
        embed.add_field(name="\u200b", value=battle_status(battle), inline=False)
        embed.set_footer(text=f"Turn 0  •  ⚡ {first.display_name}'s move  •  /leave to forfeit")
        view = _make_battle_view(battle)
        msg = await interaction.channel.send(
            content=f"{c.user.mention} {o.user.mention}",
            embed=embed,
            view=view
        )
        await interaction.response.defer()
        battle["battle_message"] = msg
        # Start inactivity timer — ends fight if nobody acts for 3 minutes
        import time
        battle["last_action_time"] = time.time()
        asyncio.create_task(inactivity_watcher(self.battle_key, self.channel_id))

    @discord.ui.button(label="❌ Decline", style=discord.ButtonStyle.danger)
    async def decline_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        battles.pop(self.battle_key, None)
        self.stop()
        await interaction.response.send_message(embed=discord.Embed(
            description=f"🏳️ **{interaction.user.display_name}** declines. Duel cancelled.",
            color=0x95A5A6
        ))


class BetView(discord.ui.View):
    def __init__(self, channel_id, challenger, opponent, bettor_id, amount, guild_id=0):
        super().__init__(timeout=30)
        self.channel_id = channel_id
        self.bettor_id = bettor_id
        self.amount = amount
        self.guild_id = guild_id
        btn1 = discord.ui.Button(label=f"🏆 {challenger.display_name}", style=discord.ButtonStyle.primary, custom_id="bet_c")
        btn1.callback = self._make_callback(challenger)
        self.add_item(btn1)
        btn2 = discord.ui.Button(label=f"🏆 {opponent.display_name}", style=discord.ButtonStyle.danger, custom_id="bet_o")
        btn2.callback = self._make_callback(opponent)
        self.add_item(btn2)

    async def on_error(self, interaction: discord.Interaction, error: Exception, item) -> None:
        await _view_on_error(interaction, error, item)

    def _make_callback(self, target: discord.Member):
        async def callback(interaction: discord.Interaction):
            if interaction.user.id != self.bettor_id:
                return await safe_respond(interaction, "❌ Not your bet!")
            battle = get_battle(self.channel_id)
            if not battle or battle.get("turns", 0) >= 3:
                return await interaction.response.send_message("❌ Betting closed.", ephemeral=True)
            if self.channel_id not in active_bets:
                active_bets[self.channel_id] = {}
            if self.bettor_id in active_bets[self.channel_id]:
                return await interaction.response.send_message("❌ Already placed a bet!", ephemeral=True)
            remove_pounds(self.bettor_id, self.amount, guild_id=self.guild_id)
            active_bets[self.channel_id][self.bettor_id] = {"amount": self.amount, "target": target}
            self.stop()
            await interaction.response.send_message(
                f"✅ Bet **{self.amount:,} soli** on **{target.display_name}**!\n"
                f"💰 Remaining: **{get_pounds(self.bettor_id, guild_id=self.guild_id):,} soli**", ephemeral=True)
        return callback

# ── Action processor ──────────────────────────────────────

async def process_action(
    interaction: discord.Interaction,
    action: str,
    ability_key: str = None,
    ritual_choice: str = None,
    from_bag: bool = False
):
    battle = get_battle(interaction.channel.id, interaction.user)
    if not validate_turn(interaction.user, battle):
        return await safe_respond(interaction, "❌ It's not your turn or no battle active!")

    try:
        if not from_bag:
            await interaction.response.defer()
    except (discord.errors.InteractionResponded, discord.errors.NotFound, discord.errors.HTTPException):
        pass  # already deferred or responded

    try:
        await _process_action_inner(interaction, action, ability_key, ritual_choice)
    except Exception as e:
        import traceback
        traceback.print_exc()
        try:
            await interaction.followup.send(f"❌ An error occurred: `{type(e).__name__}: {e}`", ephemeral=True)
        except Exception:
            pass

async def _process_action_inner(
    interaction: discord.Interaction,
    action: str,
    ability_key: str = None,
    ritual_choice: str = None
):
    battle = get_battle(interaction.channel.id, interaction.user)
    # Capture the dict key so we can pop correctly
    battle_key = get_battle_key_for_user(interaction.channel.id, interaction.user)
    channel_id = interaction.channel.id

    attacker, defender = get_fighters(interaction.user, battle)
    is_challenger = attacker.user == battle["challenger"].user
    shield_key_def = "shield_opponent" if is_challenger else "shield_challenger"
    shield_key_att = "shield_challenger" if is_challenger else "shield_opponent"
    battle_msg = battle.get("battle_message")

    battle["turns"] = battle.get("turns", 0) + 1

    # ── Pre-check: if it's an ability on cooldown/no-uses, bail early before turn effects ──
    if action == "ability" and ability_key:
        ab_pre, _ = get_ability_info(ability_key)
        if ab_pre:
            _pre_cost = attacker.get_ability_cost(ab_pre.get("cost", 0), ability_key)
            # Check prohibition
            if getattr(attacker, "prohibited_ability", None) and ability_key == attacker.prohibited_ability:
                ainfo2, _ = get_ability_info(ability_key)
                aname2 = ainfo2["name"] if ainfo2 else ability_key
                await interaction.followup.send(f"🚫 **{aname2}** has been **Prohibited** for the rest of the match!", ephemeral=True)
                return
            # Check Spiritual Suppression
            if getattr(defender, "spiritual_suppression_turns", 0) > 0 and _pre_cost > 50:
                await interaction.followup.send(
                    f"🦷 **Spiritual Suppression** blocks abilities costing more than **50 SP**! "
                    f"*({defender.spiritual_suppression_turns} turn(s) remaining)*", ephemeral=True)
                return
            # Check insufficient SP
            if attacker.sp < _pre_cost:
                await interaction.followup.send(
                    f"❌ Not enough spirit! Costs **{_pre_cost} SP** — you have **{attacker.sp} SP**.", ephemeral=True)
                return
            # Check generic cooldown (from _check_cooldown)
            _pre_cd = _check_cooldown(attacker, ability_key)
            if _pre_cd:
                await interaction.followup.send(_pre_cd, ephemeral=True)
                return

    # ── Cleanse override: allow break_out / therapy / blink to fire even when stunned ──
    CLEANSE_ABILITIES = {"break_out", "therapy", "blink"}
    is_cleanse_action = (
        action == "ability" and (
            ability_key in CLEANSE_ABILITIES or
            (ability_key == "pacification" and ritual_choice == "cleanse_self")
        )
    )
    would_skip = (
        attacker.sneak_attack_pending or
        attacker.trapped > 0 or
        attacker.paralysis > 0 or
        attacker.slumber > 0 or
        attacker.freeze > 0 or
        attacker.charm_turns > 0
    )
    if is_cleanse_action and would_skip:
        # Apply only the non-skip turn effects (damage ticks etc.) without the skip
        msg = ""
        if attacker.bleed > 0:
            bleed_dmg = max(1, int(attacker.max_hp * 0.02))
            attacker.hp = max(0, attacker.hp - bleed_dmg)
            attacker.bleed -= 1
            msg += f"🩸 **{attacker.user.display_name}** bleeds **{bleed_dmg}** damage! ({attacker.bleed} left)\n"
        if attacker.gun_shot_bleed:
            gs_bleed = max(1, int(attacker.max_hp * 0.02))
            attacker.hp = max(0, attacker.hp - gs_bleed)
            msg += f"🔫🩸 Gunshot wound bleeds **{gs_bleed}** damage!\n"
        if attacker.burn > 0:
            b_dmg = max(1, int(attacker.max_hp * (0.04 if attacker.witch_burn else 0.03)))
            attacker.hp = max(0, attacker.hp - b_dmg)
            attacker.burn -= 1
            if attacker.burn == 0:
                attacker.witch_burn = False
            msg += f"🔥 **{attacker.user.display_name}** burns for **{b_dmg}** damage! ({attacker.burn} left)\n"
        if attacker.moral_freedom_active:
            mf_dmg = max(1, int(attacker.max_hp * 0.05))
            attacker.hp = max(0, attacker.hp - mf_dmg)
            msg += f"👼 **{attacker.user.display_name}** suffers moral freedom — **{mf_dmg}** damage!\n"
        status_name = (
            "paralysis" if attacker.paralysis > 0 else
            "sleep" if attacker.slumber > 0 else
            "freeze" if attacker.freeze > 0 else
            "charm" if attacker.charm_turns > 0 else
            "sneak attack" if attacker.sneak_attack_pending else
            "trap"
        )
        msg += f"💥 **{attacker.user.display_name}** fights through the {status_name} to use their cleanse!\n"
        skip_turn = False
        color = 0x9B59B6
    else:
        msg, skip_turn = apply_turn_effects(attacker, defender, battle)
        color = 0x9B59B6

    # Death from turn effects
    if not attacker.is_alive() or not defender.is_alive():
        winner = defender if not attacker.is_alive() else attacker
        loser = attacker if not attacker.is_alive() else defender
        bet_log = resolve_bets(channel_id, winner.user, loser.user, battle.get("guild_id", 0))
        battles.pop(battle_key, None)

        # Soli reward based on loser's sequence
        loser_seq = get_seq_number(loser.role) if loser.role else 9
        winner_seq = get_seq_number(winner.role) if winner.role else 9
        reward = SEQ_WIN_REWARD.get(loser_seq, 1000)
        _gid = battle.get("guild_id", 0)
        add_pounds(winner.user.id, reward, guild_id=_gid)

        # Bounty claim
        bounty_amount = claim_bounty(_gid, loser.user.id)
        if bounty_amount > 0:
            add_pounds(winner.user.id, bounty_amount, guild_id=_gid)
            _bchan = bot.get_channel(channel_id)
            if _bchan:
                async def _send_bounty_msg2(ch=_bchan, w=winner, l=loser, amt=bounty_amount):
                    await ch.send(
                        f"🎯 **Bounty Claimed!** **{w.user.display_name}** collected "
                        f"**{amt:,} soli** for defeating **{l.user.display_name}**!"
                    )
                bot.loop.create_task(_send_bounty_msg2())

        record_battle(winner.user, loser.user, winner_seq, loser_seq, _gid)
        guild = winner.user.guild if hasattr(winner.user, "guild") else None
        bot.loop.create_task(post_battle_log(guild, winner.user, loser.user, winner_seq, loser_seq, reward, _gid))
        bot.loop.create_task(award_combat_xp(winner.user, loser_seq, guild))
        bot.loop.create_task(penalize_combat_xp(loser.user, loser_seq, guild))

        xp_gain = SEQ_WIN_XP.get(loser_seq, 150)
        xp_penalty = SEQ_LOSS_XP.get(loser_seq, 60)

        # Random chance for the winner to score a bonus stat point, independent
        # of the Sequence-based pool
        bonus_point_line = ""
        if random.random() < BONUS_STAT_POINT_CHANCE:
            bonus_record = get_or_create_stats(winner.user.id, winner.user)
            bonus_record["points"] += 1
            save_stats()
            bonus_point_line = (
                f"\n🎲 **{winner.user.display_name}** gains a flash of insight mid-battle — "
                f"**+1 bonus stat point!** *(spend it with `/stats_user`)*"
            )

        embed = discord.Embed(
            title="𓂃 💀 Fallen",
            description=(
                msg
                + f"\n**{loser.user.display_name}** collapses.\n"
                + f"🏆 **{winner.user.display_name}** wins!\n"
                + f"💰 **+{reward:,} soli** awarded for defeating a Seq {loser_seq}!\n"
                + f"📈 **{winner.user.display_name}** gains **+{xp_gain} EXP**\n"
                + f"📉 **{loser.user.display_name}** loses **-{xp_penalty} EXP**"
                + bonus_point_line
                + bet_log
            ),
            color=0x2C3E50
        )
        await send_and_replace(battle, interaction.channel, embed)
        return

    # Paralysis / sleep / freeze skip
    if skip_turn:
        swap_turn(battle)
        embed = make_battle_embed(msg, battle, battle["turn"].display_name, 0x95A5A6)
        await send_and_replace(battle, interaction.channel, embed, view=_make_battle_view(battle))
        return

    # Check if this action was blocked by provocation
    if attacker.provoked_block == action:
        attacker.provoked_block = None
        msg += f"😤 **{attacker.user.display_name}**'s **{action}** was blocked by provocation! Must choose another action.\n"
        swap_turn(battle)
        embed = make_battle_embed(msg, battle, battle["turn"].display_name, 0xF39C12)
        await send_and_replace(battle, interaction.channel, embed, view=_make_battle_view(battle))
        return

    # Check if action was forced by instigation
    if attacker.instigated_action and action != attacker.instigated_action:
        forced = attacker.instigated_action
        attacker.instigated_action = None
        msg += f"😈 **{attacker.user.display_name}** was instigated into using **{forced}**!\n"
        action = forced
        ability_key = forced if forced not in ("attack", "special", "flee") else ability_key

    # Check sabotage (ritualistic reasoning)
    if attacker.sabotaged:
        attacker.sabotaged = False
        if random.random() < 0.5:
            # Backfire — hits themselves
            self_dmg = random.randint(8, 18)
            attacker.hp = max(0, attacker.hp - self_dmg)
            msg += f"🔬 **{attacker.user.display_name}**'s move backfires — **{self_dmg}** self-damage!\n"
        else:
            msg += f"🔬 **{attacker.user.display_name}**'s move fizzles — it fails completely!\n"
        swap_turn(battle)
        v = check_victory(attacker, defender, battle_key, msg)
        if v:
            await send_and_replace(battle, interaction.channel, v)
            return
        embed = make_battle_embed(msg, battle, battle["turn"].display_name, 0x95A5A6)
        await send_and_replace(battle, interaction.channel, embed, view=_make_battle_view(battle))
        return

    # Reading prediction check — if defender predicted this action
    if defender.reading_prediction is not None:
        predicted = defender.reading_prediction
        defender.reading_prediction = None
        if predicted == action:
            # Correct — move misses AND attacker is stunned
            applied = attacker.apply_status_effect("paralysis", 1)
            stun_txt = " and is **stunned for 1 turn**" if applied else ""
            msg += f"🔭 **{defender.user.display_name}** read the move perfectly — it **misses**{stun_txt}!\n"
            swap_turn(battle)
            embed = make_battle_embed(msg, battle, battle["turn"].display_name, 0x9B59B6)
            await send_and_replace(battle, interaction.channel, embed, view=_make_battle_view(battle))
            return
        else:
            # Wrong prediction — Telepathist suffers backlash stun for 1 round
            msg += f"🔭 **{defender.user.display_name}**'s prediction was wrong — backlash stun! **{defender.user.display_name}** loses their next turn.\n"
            defender.paralysis = max(defender.paralysis, 1)

    # Seq 6: Judge — backfire if attacker uses restricted move
    if attacker.judge_restricted_move and attacker.judge_restricted_move == (ability_key or action):
        restricted_move = attacker.judge_restricted_move
        attacker.judge_restricted_move = None
        backfire_dmg = 30
        attacker.hp = max(0, attacker.hp - backfire_dmg)
        info_r, _ = get_ability_info(restricted_move)
        rname = info_r["name"] if info_r else restricted_move
        msg += f"⚖️ **Judge's Ruling!** **{attacker.user.display_name}** used the restricted **{rname}** — **{backfire_dmg}** backfire damage!\n"

    # Arbiter ceasefire violation check
    ARBITER_VIOLATIONS = {"attack", "special"}
    if attacker.arbiter_active and attacker.arbiter_turns > 0 and action in ARBITER_VIOLATIONS:
        punishment = int(attacker.hp * 0.40)
        attacker.hp = max(1, attacker.hp - punishment)
        attacker.arbiter_active = False
        defender.arbiter_active = False
        msg += f"⚖️ **ARBITER VIOLATION!** **{attacker.user.display_name}** broke the ceasefire — a higher power strikes them for **{punishment}** damage (**40% of their HP**)!\n"
    elif defender.arbiter_active and defender.arbiter_turns > 0 and action in ARBITER_VIOLATIONS:
        punishment = int(defender.hp * 0.40)
        defender.hp = max(1, defender.hp - punishment)
        attacker.arbiter_active = False
        defender.arbiter_active = False
        msg += f"⚖️ **ARBITER VIOLATION!** **{defender.user.display_name}** broke the ceasefire by provoking — a higher power strikes them for **{punishment}** damage (**40% of their HP**)!\n"

    # Seq 6: Charm maintain-or-fade logic (checked each turn, charm already handled in apply_turn_effects)
    # Charm chance decays after a charmed turn passes
    if attacker.charm_turns == 0 and attacker.charm_chance > 0:
        if random.random() < attacker.charm_chance:
            attacker.charm_turns = 1
            attacker.charm_chance = max(0.0, attacker.charm_chance - 0.10)
        else:
            attacker.charm_chance = 0.0  # charm fully faded

    # Seq 6: Scribe — new system: pending copy fires 1 turn after activation, even through stuns
    # If scribe_pending is set on the defender, record whatever ability was just used against them
    if defender.scribe_pending and action == "ability" and ability_key:
        defender.scribe_copied_ability = ability_key
        defender.scribe_pending = False
        defender.scribe_record_in = 0
        info_sc, _ = get_ability_info(ability_key)
        scname = info_sc["name"] if info_sc else ability_key
        msg += f"📖 **Scribe** recorded **{scname}** — **{defender.user.display_name}** can use it next turn!\n"
    # Legacy scribe_active support (in case copied ability fires immediately)
    elif defender.scribe_active and action == "ability" and ability_key:
        defender.scribe_copied_ability = ability_key
        defender.scribe_active = False
        info_sc, _ = get_ability_info(ability_key)
        scname = info_sc["name"] if info_sc else ability_key
        msg += f"📖 **Scribe** copied **{scname}** — **{defender.user.display_name}** can use it next turn!\n"

    # ── Hurricane of Light auto-release ───────────────────
    # If the attacker planted the sword last turn and isn't stunned, it fires automatically
    if attacker.hurricane_charging and ability_key != "hurricane_of_light":
        attacker.hurricane_charging = False
        attacker.hurricane_uses += 1
        attacker.hurricane_cooldown = 12
        avg_base = (attacker.base_hp + defender.base_hp) // 2
        pct = random.uniform(0.35, 0.40)
        raw_dmg = int(avg_base * pct)
        if defender.distortion_active:
            defender.distortion_active = False
            raw_dmg = max(1, raw_dmg // 2)
            msg += f"👑 **{defender.user.display_name}** distorts — hurricane damage halved!\n"
        elif defender.phasing_active:
            defender.phasing_active = False
            defender.phasing_cooldown = 1
            raw_dmg = max(1, raw_dmg // 2)
            msg += f"👻 **{defender.user.display_name}** phases — hurricane damage halved!\n"
        actual_dmg = defender.apply_damage(int(raw_dmg * attacker.get_attack_mult()), is_ability=True)
        backlash = int(actual_dmg * 0.30)
        attacker.hp = max(0, attacker.hp - backlash)
        msg += f"🌪️ **HURRICANE OF LIGHT — AUTO-RELEASE!** The stored hurricane erupts! **{actual_dmg}** damage dealt — **{backlash}** backlash to you! *(use {attacker.hurricane_uses}/3, 12 turn cd)*\n"
        color = 0xF5D020
        # Check for victory after auto-release
        v = check_victory(attacker, defender, battle_key, msg)
        if v:
            await send_and_replace(battle, interaction.channel, v)
            return

    # ── Attack ────────────────────────────────────────────
    if action == "attack":
        # Trip trap — next attack misses
        if attacker._trip_trap_miss:
            attacker._trip_trap_miss = False
            msg += f"🪜 **{attacker.user.display_name}** trips! Attack misses!"; color = 0x95A5A6
            swap_turn(battle)
            await send_and_replace(battle, interaction.channel, make_battle_embed(msg, battle, battle["turn"].display_name), view=_make_battle_view(battle))
            return
        # Seq 6: Distortion — guaranteed dodge, 40% reflect (disguised as evade)
        if defender.distortion_active:
            defender.distortion_active = False
            base_dmg_est, _ = attacker.get_damage(0.055, 0.090)
            if random.random() < 0.40 and base_dmg_est < int(defender.max_hp * 0.60):
                reflect_dmg = int(base_dmg_est * 0.60)
                attacker.hp = max(0, attacker.hp - reflect_dmg)
                msg += f"💨 **{attacker.user.display_name}**'s attack misses — **{defender.user.display_name}** sidesteps cleanly! *(+{reflect_dmg} reflected)*"; color = 0x8E44AD
            else:
                msg += f"💨 **{attacker.user.display_name}** swings wide — **{defender.user.display_name}** steps back!"; color = 0x3498DB
        # Seq 7: paper figurine blocks all attacks
        elif defender.paper_figurine_turns > 0:
            defender.paper_figurine_turns -= 1
            msg += f"🪆 **{defender.user.display_name}**'s paper figurine blocks the attack! *({defender.paper_figurine_turns} turn{'s' if defender.paper_figurine_turns != 1 else ''} left)*"; color = 0x9B59B6
        # Seq 7: lucky_day — attacker's hit chance halved
        elif not attacker.hit_chance_vs(defender):
            msg += f"🍀 **{attacker.user.display_name}**'s attack is deflected by fortune!"; color = 0x2ECC71
        # Seq 7: werewolf dodge (on defender)
        elif defender.transformed and random.random() < defender.transform_dodge_reduction:
            msg += f"🐺 **{defender.user.display_name}** lunges aside — attack misses! *(dodge {int(defender.transform_dodge_reduction*100)}%)*"; color = 0x95A5A6
        else:
            if attacker.protection_active:
                attacker.protection_weakened_turn = True  # reduction drops to 10 for 1 turn after attacking
                if attacker.protection_mode == "active":
                    attacker.protection_active = False  # Active mode: ends after attacker attacks
            dmg, crit = attacker.get_damage(0.055, 0.090)
            if defender.divination_dodge:
                dodge_chance = defender._seer_dodge_chance or 70
                if random.uniform(0, 100) < dodge_chance:
                    defender.divination_dodge_turns = max(0, defender.divination_dodge_turns - 1)
                    if defender.divination_dodge_turns <= 0:
                        defender.divination_dodge = False
                        defender._seer_dodge_chance = None
                    msg += f"🔮 **{defender.user.display_name}** vanishes! *(dodge {int(dodge_chance)}%)*"
                    swap_turn(battle)
                    await send_and_replace(battle, interaction.channel, make_battle_embed(msg, battle, battle["turn"].display_name), view=_make_battle_view(battle))
                    return
                else:
                    defender.divination_dodge_turns = max(0, defender.divination_dodge_turns - 1)
                    if defender.divination_dodge_turns <= 0:
                        defender.divination_dodge = False
                        defender._seer_dodge_chance = None
            # Danger Intuition: 35% full dodge, 65% graze (50% dmg) — single linked roll
            if defender.danger_intuition_active:
                roll = random.random()
                if roll < 0.35:
                    msg += f"👁️ **{defender.user.display_name}** senses danger — full dodge!"
                    swap_turn(battle)
                    await send_and_replace(battle, interaction.channel, make_battle_embed(msg, battle, battle["turn"].display_name), view=_make_battle_view(battle))
                    return
                else:
                    dmg = max(1, dmg // 2)
                    msg += f"👁️ **{defender.user.display_name}** senses it — grazed for **{dmg}** (50%)!\n"
            if defender.phasing_active:
                defender.phasing_active = False
                defender.phasing_cooldown = 1
                dmg = max(1, int(dmg * random.uniform(0.40, 0.50)))  # reduce to 40-50%, not full block
                msg += f"👻 **{defender.user.display_name}** phases — damage reduced to **{dmg}**!\n"
            if random.uniform(0, 100) < defender.evasion_chance():
                msg += f"💨 **{defender.user.display_name}** sidesteps!"; color = 0x3498DB
            else:
                if battle.get(shield_key_def):
                    dmg = dmg // 2
                    battle[shield_key_def] = False
                    msg += "🛡️ Shield shatters!\n"
                actual_dmg = defender.apply_damage(dmg)
                # Storm Divinity — +20 lightning on every attack
                # Radiant Armour recoil — attacker takes recoil on hitting defender
                if getattr(defender, "_radiant_armour_turns", 0) > 0 and getattr(defender, "_radiant_armour_recoil", 0) > 0:
                    recoil = defender._radiant_armour_recoil
                    attacker.hp = max(0, attacker.hp - recoil)
                    msg += f"✨ **Radiant Armour** — {recoil} recoil! "
                # Thorn recoil (Creation Divinity)
                if getattr(defender, "_thorn_recoil", 0) > 0 and getattr(defender, "_thorn_recoil_dmg", 0) > 0:
                    attacker.hp = max(0, attacker.hp - defender._thorn_recoil_dmg)
                    msg += f"🌿 **Thorn recoil** — {defender._thorn_recoil_dmg}! "
                # Parry/Riposte — defender parried, deal 35 back to attacker
                if getattr(defender, "_riposte_pending", False):
                    defender._riposte_pending = False
                    riposte_dmg = seq_val(defender, 35)
                    attacker.hp = max(0, attacker.hp - riposte_dmg)
                    msg += f"🛡️ **{defender.user.display_name}** PARRIES and ripostes for **{riposte_dmg}** damage!\n"
                    actual_dmg = 0
                if attacker.last_stand_bonus > 0:
                    extra = attacker.last_stand_bonus
                    attacker.last_stand_bonus = 0
                    defender.hp = max(0, defender.hp - extra)
                    actual_dmg += extra
                    msg_bonus = f" *(+{extra} Last Stand!)*"
                else:
                    msg_bonus = ""
                # Seq 5: Cull bonus
                if attacker.cull_bonus > 0:
                    extra = attacker.cull_bonus
                    attacker.cull_bonus = 0
                    defender.hp = max(0, defender.hp - extra)
                    actual_dmg += extra
                    msg_bonus += f" *(+{extra} Cull!)*"
                attacker.last_attack_dmg = actual_dmg
                attacker.sp = min(attacker.max_sp, attacker.sp + 6)
                if crit:
                    # Seq 5: Spirit Thread — broken by critical hit
                    if defender.spirit_thread_active:
                        defender.spirit_thread_active = False
                        defender.spirit_thread_cooldown = 5
                        msg += f"\n🧵 **Spirit Thread** disrupted by critical hit!"
                    msg += f"💥 **CRITICAL!** **{attacker.user.display_name}** strikes for **{actual_dmg}** damage{msg_bonus}! *(+6 SP)*"; color = 0xF39C12
                else:
                    msg += f"⚔️ **{attacker.user.display_name}** strikes for **{actual_dmg}** damage{msg_bonus}. *(+6 SP)*"
                # Seq 7: vile crime tracking
                if attacker.vile_crime_count > 0 and not attacker.vile_crime_used:
                    attacker.vile_crime_count += 1
                    if attacker.vile_crime_count > 3:
                        attacker.vile_crime_used = True
                        attacker.vile_crime_count = 0
                        defender.apply_status_effect("bleed", 999)  # permanent
                        defender.apply_status_effect("burn", 999)   # permanent
                        msg += f"\n🔪 **Vile Crime complete!** **{defender.user.display_name}** afflicted with permanent bleed and burn!"
                    else:
                        msg += f"\n🔪 Vile Crime: *({attacker.vile_crime_count - 1}/3 hits)*"

    # ── Special ───────────────────────────────────────────
    elif action == "special":
        # Seq 6: Distortion intercept
        if defender.distortion_active:
            defender.distortion_active = False
            base_dmg_est, _ = attacker.get_damage(0.090, 0.140)
            if random.random() < 0.40 and base_dmg_est < int(defender.max_hp * 0.60):
                reflect_dmg = int(base_dmg_est * 0.60)
                attacker.hp = max(0, attacker.hp - reflect_dmg)
                msg += f"💨 **{attacker.user.display_name}**'s special misses — **{defender.user.display_name}** deflects it cleanly! *(+{reflect_dmg} reflected)*"; color = 0x8E44AD
            else:
                msg += f"💨 **{attacker.user.display_name}**'s special whiffs — **{defender.user.display_name}** moves aside!"; color = 0x3498DB
        # Seq 7: paper figurine blocks
        elif defender.paper_figurine_turns > 0:
            defender.paper_figurine_turns -= 1
            msg += f"🪆 **{defender.user.display_name}**'s paper figurine blocks the special! *({defender.paper_figurine_turns} turn{'s' if defender.paper_figurine_turns != 1 else ''} left)*"; color = 0x9B59B6
        # Seq 7: lucky_day
        elif not attacker.hit_chance_vs(defender):
            msg += f"🍀 Fortune deflects the special attack!"; color = 0x2ECC71
        # Seq 7: werewolf dodge
        elif defender.transformed and random.random() < defender.transform_dodge_reduction:
            msg += f"🐺 **{defender.user.display_name}** leaps away — special misses! *(dodge {int(defender.transform_dodge_reduction*100)}%)*"; color = 0x95A5A6
        else:
            base_miss = max(5, 25 - attacker.stats["luck"] * 3)
            miss_chance = attacker.get_miss_chance(base_miss)
            if random.randint(1, 100) <= miss_chance:
                attacker.sp = min(attacker.max_sp, attacker.sp + 4)
                msg += f"💨 **{attacker.user.display_name}** misses! *(+4 SP)*"; color = 0x95A5A6
            else:
                if defender.divination_dodge:
                    dodge_chance = defender._seer_dodge_chance or 70
                    if random.uniform(0, 100) < dodge_chance:
                        defender.divination_dodge_turns = max(0, defender.divination_dodge_turns - 1)
                        if defender.divination_dodge_turns <= 0:
                            defender.divination_dodge = False
                            defender._seer_dodge_chance = None
                        msg += f"🔮 **{defender.user.display_name}** slips the special! *(dodge {int(dodge_chance)}%)*"
                        swap_turn(battle)
                        await send_and_replace(battle, interaction.channel, make_battle_embed(msg, battle, battle["turn"].display_name), view=_make_battle_view(battle))
                        return
                    else:
                        defender.divination_dodge_turns = max(0, defender.divination_dodge_turns - 1)
                        if defender.divination_dodge_turns <= 0:
                            defender.divination_dodge = False
                            defender._seer_dodge_chance = None
                # Danger Intuition on special — single linked roll
                if defender.danger_intuition_active:
                    roll = random.random()
                    if roll < 0.35:
                        msg += f"👁️ **{defender.user.display_name}** senses the special — full dodge!"
                        swap_turn(battle)
                        await send_and_replace(battle, interaction.channel, make_battle_embed(msg, battle, battle["turn"].display_name), view=_make_battle_view(battle))
                        return
                    else:
                        msg += f"👁️ **{defender.user.display_name}** senses it — grazed for 50%!\n"
                        # dmg halved below after phasing/evasion checks
                        _danger_intuition_graze = True
                else:
                    _danger_intuition_graze = False
                if defender.phasing_active:
                    defender.phasing_active = False
                    defender.phasing_cooldown = 1
                    _phasing_reduction = random.uniform(0.40, 0.50)
                    msg += f"👻 **{defender.user.display_name}** phases — damage halved!\n"
                else:
                    _phasing_reduction = None
                if random.uniform(0, 100) < defender.evasion_chance():
                    msg += f"💨 **{defender.user.display_name}** evades!"; color = 0x3498DB
                else:
                    dmg, crit = attacker.get_damage(0.090, 0.140)
                    # Special Boost (White Tower Seq 8) — next special deals 50% more
                    if getattr(attacker, "_special_boost", False):
                        attacker._special_boost = False
                        dmg = int(dmg * 1.50)
                    if _phasing_reduction is not None:
                        dmg = max(1, int(dmg * _phasing_reduction))
                    if _danger_intuition_graze:
                        dmg = max(1, dmg // 2)
                    if battle.get(shield_key_def):
                        dmg = dmg // 2
                        battle[shield_key_def] = False
                        msg += "🛡️ Shield shatters!\n"
                    actual_dmg = defender.apply_damage(dmg)
                    # Seq 5: Last Stand bonus
                    if attacker.last_stand_bonus > 0:
                        extra = attacker.last_stand_bonus
                        attacker.last_stand_bonus = 0
                        defender.hp = max(0, defender.hp - extra)
                        actual_dmg += extra
                        msg_bonus = f" *(+{extra} Last Stand!)*"
                    else:
                        msg_bonus = ""
                    # Seq 5: Cull bonus
                    if attacker.cull_bonus > 0:
                        extra = attacker.cull_bonus
                        attacker.cull_bonus = 0
                        defender.hp = max(0, defender.hp - extra)
                        actual_dmg += extra
                        msg_bonus += f" *(+{extra} Cull!)*"
                    attacker.sp = min(attacker.max_sp, attacker.sp + 4)
                    # Seq 5: Spirit Thread broken by special attack
                    if defender.spirit_thread_active:
                        defender.spirit_thread_active = False
                        defender.spirit_thread_cooldown = 5
                        msg += f"\n🧵 **Spirit Thread** disrupted by special attack!"
                    if crit:
                        msg += f"💥 **CRITICAL SPECIAL!** **{actual_dmg}** damage{msg_bonus}! *(+4 SP)*"; color = 0xE74C3C
                    else:
                        msg += f"💥 **{attacker.user.display_name}** strikes for **{actual_dmg}** damage{msg_bonus}! *(+4 SP)*"; color = 0xE67E22

    # ── Ability ───────────────────────────────────────────
    elif action == "ability":
        ab, is_role = get_ability_info(ability_key)
        if not ab:
            await interaction.followup.send("❌ Unknown ability.", ephemeral=True)
            return
        # Allow scribe-copied shop abilities (e.g. leodero) even if not owned
        _is_scribe_copy = attacker.scribe_copy_pending
        attacker.scribe_copy_pending = False
        if not is_role and not owns_spell(attacker.user.id, ability_key, guild_id=battle["guild_id"]) and not _is_scribe_copy:
            await interaction.followup.send("❌ You don't own that spell!", ephemeral=True)
            return
        if is_role and attacker.role_ability_disabled > 0:
            await interaction.followup.send(f"❌ Your role ability is suppressed for **{attacker.role_ability_disabled}** more turn(s)!", ephemeral=True)
            return
        # Prohibition: only block the one specifically sealed ability
        if getattr(attacker, "prohibited_ability", None) and ability_key == attacker.prohibited_ability:
            ainfo, _ = get_ability_info(ability_key)
            aname = ainfo["name"] if ainfo else ability_key
            await interaction.followup.send(f"🚫 **{aname}** has been **Prohibited** for the rest of the match!", ephemeral=True)
            return

        cd_msg = _check_cooldown(attacker, ability_key)
        if cd_msg:
            await interaction.followup.send(cd_msg, ephemeral=True)
            return

        actual_cost = attacker.get_ability_cost(ab["cost"], ability_key)
        if attacker.sp < actual_cost:
            await interaction.followup.send(
                f"❌ Not enough spirit! Costs **{actual_cost} SP** — you have **{attacker.sp} SP**.", ephemeral=True)
            return

        # Seq 5: Spiritual Suppression — block abilities costing >50 SP
        if defender.spiritual_suppression_turns > 0 and actual_cost > 50:
            await interaction.followup.send(
                f"🦷 **Spiritual Suppression** blocks abilities costing more than **50 SP**! "
                f"*({defender.spiritual_suppression_turns} turn(s) remaining)*", ephemeral=True)
            return

        # Free Ability (White Tower Seq 6) — next ability costs 0 SP
        if getattr(attacker, "_free_ability", False) and actual_cost > 0:
            attacker._free_ability = False
            actual_cost = 0
        attacker.sp -= actual_cost
        cd_blocked = False  # set True in cooldown/already-used branches so turn is NOT consumed
        # Soul Parasite — drain 50% of SP cost from opponent when they use ability
        if action == "ability" and getattr(defender, "_soul_parasite_active", False):
            parasite_drain = actual_cost // 2
            if parasite_drain > 0:
                attacker.sp = min(attacker.max_sp, attacker.sp + parasite_drain)
                msg += f"🦠 **Soul Parasite** drained **{parasite_drain} SP**! "
        # Mind Dominated — actions heal attacker instead
        if getattr(attacker, "_mind_dominated_turns", 0) > 0:
            pass  # handled in process_action result — attacker's damage heals defender

        # Chaos Dominion — 50% chance ability backfires
        if getattr(attacker, "_chaos_dominion_abilities", 0) > 0:
            attacker._chaos_dominion_abilities -= 1
            if random.random() < 0.50:
                backfire = int(attacker.max_hp * 0.10)
                attacker.hp = max(0, attacker.hp - backfire)
                await interaction.followup.send(
                    f"🌀 **Chaos Dominion Backfire!** **{attacker.user.display_name}**'s ability backfired for **{backfire}** self-damage!", ephemeral=False)
                if attacker.hp <= 0:
                    return
        # Bizarro reflect — if flagged, redirect to self
        if getattr(attacker, "_bizarro_reflect_next", False):
            attacker._bizarro_reflect_next = False
            # ability hits self instead — swap attacker/defender for this ability
        # Holy Domain seal — only Sun pathway abilities allowed
        if getattr(defender, "_holy_domain_sealed", 0) > 0:
            atk_pathway = attacker.role.split("] ")[0].replace("[","") if attacker.role else ""
            if atk_pathway != "Sun":
                defender._holy_domain_sealed -= 1
                # The ability still fires but we note the seal is active
        # Mystery Authority — next ability is a miracle
        # Spatial lock — double SP cost
        if getattr(attacker, "_spatial_lock_abilities", 0) > 0:
            attacker._spatial_lock_abilities -= 1
            actual_cost = actual_cost * 2
        # Glance into Fate — 30% chance ability backfires onto the caster
        if getattr(attacker, "fate_cursed_backfire", False) and ability_key not in ("grazing", "spectate", "song", "study", "computing", "night_boost"):
            attacker.fate_cursed_backfire = False
            if random.random() < 0.30:
                backfire_dmg = int(attacker.max_hp * 0.10)
                attacker.hp = max(0, attacker.hp - backfire_dmg)
                await interaction.followup.send(
                    f"🎲 **Fate Backfire!** The cursed fate threads unravel — **{attacker.user.display_name}**'s ability backfires for **{backfire_dmg}** self-damage!", ephemeral=False)
                if attacker.hp <= 0:
                    return  # will be caught by hp check below

        # Track for Prometheus — record every ability the attacker uses so opponent can steal them
        if not hasattr(attacker, "abilities_used_this_battle"):
            attacker.abilities_used_this_battle = set()
        if ability_key not in ("prometheus", "scribe"):
            attacker.abilities_used_this_battle.add(ability_key)
        OFFENSIVE_ABILITIES = {
            "leodero","drain","debuff","trap","vital_strike","crime","desecration",
            "prying","card_tricks","moral_freedom",
            "poem","brilliant_light","gun_shot","water_bullet","sneak_attack","weapon_throw",
            "witch_curse","fire_ravens","blood_sucker","vine_bomb","mental_piercing",
            "faceless","hypnotist","conspiracy","scrolls_professor","calamity_priest",
            "zombie","distortion","soul_assurer","pacification",
            "provocation","instigation","glance_into_fate","testimony",
            "summoning_dead","astrology","observation","mental_theft","dream_visitation",
            "spiritual_takeover","dragged_to_hell","evil_sealing","thread_storm","cull",
            "star_of_curses","curse_of_misfortune","purification_halo","light_of_holiness",
            "arrow_of_lightning","disease_propagation","possession","wraith_shriek",
            "desire_explosion","fire_storm","star_pillar","vile_crime","bribe",
            "artisan","biologist","potions_professor","devil_form",
        }
        if ability_key in OFFENSIVE_ABILITIES and defender.paper_figurine_turns > 0:
            defender.paper_figurine_turns -= 1
            attacker.sp += actual_cost  # refund
            msg += f"🪆 **{defender.user.display_name}**'s paper figurine blocks the ability! *({defender.paper_figurine_turns} turn{'s' if defender.paper_figurine_turns != 1 else ''} left)*"; color = 0x9B59B6
            swap_turn(battle)
            embed = make_battle_embed(msg, battle, battle["turn"].display_name, color)
            await send_and_replace(battle, interaction.channel, embed, view=_make_battle_view(battle))
            return

        # Seq 6: Distortion also intercepts offensive abilities
        if ability_key in OFFENSIVE_ABILITIES and defender.distortion_active:
            defender.distortion_active = False
            attacker.sp += actual_cost  # refund
            msg += f"💨 **{attacker.user.display_name}**'s ability fizzles — **{defender.user.display_name}** distorts it away!"; color = 0x8E44AD
            swap_turn(battle)
            embed = make_battle_embed(msg, battle, battle["turn"].display_name, color)
            await send_and_replace(battle, interaction.channel, embed, view=_make_battle_view(battle))
            return

        if ability_key == "leodero":
            if random.uniform(0, 100) < defender.evasion_chance():
                msg += f"💨 **{defender.user.display_name}** leaps away!"; color = 0x3498DB
            else:
                dmg = int(random.randint(seq_val(attacker, 30), seq_val(attacker, 40)) * attacker.get_attack_mult())
                if battle.get(shield_key_def):
                    dmg = dmg // 2; battle[shield_key_def] = False; msg += "🛡️ Shield shatters!\n"
                actual_dmg = defender.apply_damage(dmg, is_ability=True)
                recoil = max(1, int(actual_dmg * 0.10))
                attacker.hp = max(0, attacker.hp - recoil)
                msg += (f"⚡ **LEODERO!** **{defender.user.display_name}** struck for **{actual_dmg}** damage!\n"
                        f"🔥 **{attacker.user.display_name}** suffers **{recoil}** recoil!"); color = 0xF1C40F

        elif ability_key == "heal":
            lo, hi = seq_dmg(attacker, ab["pct_min"]), seq_dmg(attacker, ab["pct_max"])
            heal = int(random.randint(lo, hi) * (1 + attacker.stats["luck"] * 0.02))
            attacker.hp = min(attacker.max_hp, attacker.hp + heal)
            msg += f"💚 **{attacker.user.display_name}** recovers **{heal} HP**!"; color = 0x2ECC71

        elif ability_key == "drain":
            drain = random.randint(seq_dmg(attacker, ab["pct_min"]), seq_dmg(attacker, ab["pct_max"]))
            actual_dmg = defender.apply_damage(drain, is_ability=True)
            attacker.sp = min(attacker.max_sp, attacker.sp + actual_dmg)
            msg += f"🌑 Drained **{actual_dmg}** HP as spirit!"; color = 0x8E44AD

        elif ability_key == "shield":
            battle[shield_key_att] = True
            msg += f"🛡️ **{attacker.user.display_name}** raises a spirit shield!"; color = 0x3498DB

        elif ability_key == "debuff":
            if attacker.debuff_used:
                attacker.sp += actual_cost
                msg += f"🔻 **Debuff** already used this battle!"; color = 0x95A5A6
                cd_blocked = True
            else:
                attacker.debuff_used = True
                defender.debuff_turns = 5
                defender.debuff_stacks = 1  # keep for compatibility with damage mult
                msg += f"🔻 **Debuff!** **{defender.user.display_name}**'s damage reduced by **20%** for **5 turns**!"; color = 0x8E44AD

        elif ability_key == "ritual":
            if ritual_choice == "strength":
                boost = random.randint(3, 6); attacker.strength_boost += boost
                msg += f"🕯️ **Ritual of Strength!** Next attack **+{boost}** damage!"; color = 0xE74C3C
            elif ritual_choice == "divination":
                attacker.divination_dodge = True; attacker._seer_dodge_chance = None; attacker.divination_dodge_turns = 1
                dodge_display = attacker.divination_chance(defender.stats["speed"])
                msg += f"🕯️ **Ritual of Divination!** **{int(dodge_display)}%** dodge chance!"; color = 0x9B59B6
            elif ritual_choice == "blessing":
                heal = random.randint(seq_dmg(attacker, 0.040), seq_dmg(attacker, 0.065)); attacker.hp = min(attacker.max_hp, attacker.hp + heal); attacker.blessed = True
                msg += f"🕯️ **Ritual of Blessing!** Recovered **{heal} HP**, strength rises next turn!"; color = 0x2ECC71

        # ── Seq 9 Role abilities ──────────────────────────

        elif ability_key == "prying":
            reduction = round(random.uniform(0.15, 0.25), 2)
            defender.defence_reduction = max(defender.defence_reduction, reduction)
            msg += f"🔍 **{defender.user.display_name}** takes **{int(reduction*100)}%** more damage!"; color = 0xF39C12

        elif ability_key == "danger_intuition":
            # Toggle on/off — no cooldown
            if attacker.danger_intuition_active:
                attacker.danger_intuition_active = False
                attacker.sp += actual_cost  # refund — toggle off costs nothing
                msg += f"👁️ **Danger Intuition** deactivated."; color = 0x95A5A6
            else:
                attacker.danger_intuition_active = True
                msg += f"👁️ **Danger Intuition** activated! **35% dodge** / **65% take 50% damage** — **{attacker.intuition_upkeep_sp} SP/turn** upkeep. Toggle off to stop."; color = 0x9B59B6

        elif ability_key == "pickpocket":
            stolen_sp = min(random.randint(seq_val(attacker, 15), seq_val(attacker, 20)), defender.sp)
            defender.sp = max(0, defender.sp - stolen_sp); attacker.sp = min(attacker.max_sp, attacker.sp + stolen_sp)
            msg += f"🖐️ Pickpocketed **{stolen_sp} SP** from **{defender.user.display_name}**!"
            if random.randint(1, 10) == 1:
                stolen_hp = min(seq_val(attacker, 5), defender.hp); defender.hp -= stolen_hp; attacker.hp = min(attacker.max_hp, attacker.hp + stolen_hp)
                msg += f" Also swiped **{stolen_hp} HP**!"
            attacker.pickpocket_cooldown = 2; color = 0xF39C12

        elif ability_key == "phasing":
            attacker.phasing_active = True; attacker.phasing_cooldown = 4
            msg += f"👻 **{attacker.user.display_name}** phases — next attack completely dodged! *(4 turn cd)*"; color = 0x9B59B6

        elif ability_key == "trap":
            trap_sp_cost = 35
            if attacker.trap_cooldown > 0 or getattr(attacker, "trap_slots", []):
                cd_turns = attacker.trap_cooldown
                pending = len(getattr(attacker, "trap_slots", []))
                if pending:
                    msg += f"🪤 **Trap** — traps still active ({pending} remaining)!"; color = 0x95A5A6
                else:
                    msg += f"🪤 **Trap** on cooldown for **{cd_turns}** more turn(s)!"; color = 0x95A5A6
                cd_blocked = True
            elif attacker.sp < trap_sp_cost:
                battle["turns"] = max(0, battle.get("turns", 0) - 1)
                await interaction.followup.send(
                    f"❌ **Trap** needs **{trap_sp_cost} SP** — you only have **{attacker.sp} SP**!",
                    ephemeral=True
                )
                await send_and_replace(battle, interaction.channel, make_battle_embed(msg, battle, attacker.user.display_name), view=_make_battle_view(battle))
                return
            else:
                attacker.sp -= trap_sp_cost  # actual_cost was 0, so no double-charge
                trap_options = ["flames", "trip", "dog_chase", "tripwire"]
                trap1 = random.choice(trap_options)
                trap2 = random.choice(trap_options)
                attacker.trap_slots = [trap1, trap2]  # replaces old traps (no stacking)
                attacker.trap_set_turn = battle.get("turn_count", 0)
                attacker.trap_cooldown = 0  # cooldown starts after last trap fires
                battle["trap_set_this_turn"] = True  # suppress "channels SP" message
                msg += f"🪤 **Trap!** Two traps secretly laid — activating after **1 turn**, randomly within **5 rounds**!\n*(Contents hidden from opponent)* *(8 turn cd — starts after trap ends)*"; color = 0x8E44AD

        elif ability_key == "vital_strike":
            flat_dmg = seq_val(attacker, 55)
            actual_dmg = defender.apply_damage(int(flat_dmg * attacker.get_attack_mult()), is_ability=True, skill_key="vital_strike")
            attacker.vital_strike_cooldown = 5
            attacker._vital_strike_weaken_turns = 5  # -40% ability damage for 5 rounds
            attacker._vital_strike_weaken_pct = 0.40
            msg += f"🗡️ **VITAL STRIKE!** Every ounce of strength slams into one point — **{actual_dmg}** damage! *(5t cd — ability damage −40% for 5 turns)*"; color = 0xE74C3C

        elif ability_key == "crime":
            roll = random.randint(1, 3)
            if roll == 1:
                applied = defender.apply_status_effect("bleed", 3)
                msg += f"🦹 **Crime — Bleed!**" + ("" if not applied else f" **{defender.user.display_name}** bleeds 3 turns!"); color = 0xE74C3C
            elif roll == 2:
                applied = defender.apply_status_effect("paralysis", 1)
                if applied:
                    attacker.crime_cooldown = 2
                msg += f"🦹 **Crime — Paralysis!**" + (" Resisted!" if not applied else f" **{defender.user.display_name}** paralysed 1 turn! *(2 turn cd)*"); color = 0x95A5A6
            else:
                actual_dmg = apply_ability_damage(attacker, defender, 0.041)
                msg += f"🦹 **Crime — Backfire! {actual_dmg}** damage!"; color = 0xE67E22

        elif ability_key == "break_out":
            cleared = []
            if attacker.bleed > 0:              attacker.bleed = 0;                       cleared.append("bleed")
            if attacker.gun_shot_bleed:         attacker.gun_shot_bleed = False;          cleared.append("gunshot bleed")
            if attacker.paralysis > 0:          attacker.paralysis = 0;                   cleared.append("paralysis")
            if attacker.slumber > 0:            attacker.slumber = 0;                     cleared.append("sleep")
            if attacker.freeze > 0:             attacker.freeze = 0;                      cleared.append("freeze")
            if attacker.freeze_weakened:        attacker.freeze_weakened = False;         cleared.append("freeze weakened")
            if attacker.burn > 0:               attacker.burn = 0;                        cleared.append("burn")
            if attacker.defence_reduction > 0:  attacker.defence_reduction = 0;          cleared.append("defence reduction")
            if attacker.sneak_attack_pending:   attacker.sneak_attack_pending = False;    cleared.append("sneak attack")
            if attacker.trapped > 0:            attacker.trapped = 0;                     cleared.append("trapped")
            if attacker.charm_turns > 0:        attacker.charm_turns = 0; attacker.charm_chance = 0.0; cleared.append("charm")
            if attacker.scroll_status:          attacker.scroll_status = None;            cleared.append("scroll affliction")
            if attacker.moral_freedom_active:   attacker.moral_freedom_active = False;    cleared.append("moral freedom")
            if attacker.debuff_stacks > 0:      attacker.debuff_stacks = 0;              cleared.append("debuff stacks")
            if attacker.notary_debuff_turns > 0: attacker.notary_debuff_turns = 0;       cleared.append("notary debuff")
            if cleared:
                msg += f"⛓️ **Break Out!** Cleared: **{', '.join(cleared)}**!"; color = 0x2ECC71
            else:
                attacker.sp += actual_cost; msg += f"⛓️ No status effects to clear!"; color = 0x95A5A6

        elif ability_key == "invigorate":
            attacker.invigorated = True
            msg += f"💢 Next attack deals **30% more** damage!"; color = 0xE74C3C

        elif ability_key == "night_boost":
            uses_left = 2 - getattr(attacker, "night_boost_uses_count", 0)
            if uses_left <= 0:
                attacker.sp += actual_cost; msg += f"🌙 **Night Boost** — both uses spent!"; cd_blocked = True
            else:
                nb = seq_val(attacker, 10)
                attacker.night_boost_active = True; attacker.night_boost_used = True; attacker.night_boost_damage = nb
                attacker.night_boost_turns = 7
                attacker.night_boost_uses_count = getattr(attacker, "night_boost_uses_count", 0) + 1
                remaining = uses_left - 1
                msg += f"🌙 **Night Boost!** **+{nb} damage** per attack for **7 turns**! ({remaining} use{'s' if remaining!=1 else ''} remaining)"; color = 0x2C3E50

        elif ability_key == "desecration":
            actual_dmg = apply_ability_damage(attacker, defender, 0.068)
            applied = defender.apply_status_effect("paralysis", 1)
            para_txt = " + paralysed 1 turn" if applied else ""
            attacker.desecration_cooldown = 4
            msg += f"💀 **Desecration!** **{actual_dmg}** damage{para_txt}! *(4 turn cd)*"; color = 0x2C3E50

        elif ability_key == "balancing_act":
            # Sailor Seq 9 — phantom balance on spirit scales. Raises evasion + resists next status.
            attacker.balancing_act_evasion_turns = 3
            attacker.balancing_act_status_immune = True   # blocks next 1 status effect
            attacker.balancing_cooldown = 4
            msg += (f"⚓ **Balancing Act!** **{attacker.user.display_name}** steadies on phantom scales — "
                    f"**+20% evasion** for **3 turns** and blocks the next status effect! *(4 turn cd)*"); color = 0x3498DB

        elif ability_key == "song":
            # Bard Seq 9 — spiritual melody restores the singer's spirit and unsettles the listener
            regen = int(attacker.max_sp * 0.20)
            attacker.sp = min(attacker.max_sp, attacker.sp + regen)
            charm_txt = ""
            if random.random() < 0.25 and not defender.charm_turns > 0:
                defender.charm_turns = 1
                defender.charm_chance = 0.20
                charm_txt = f" and charms **{defender.user.display_name}** for **1 turn** (20% linger)!"
            attacker.song_cooldown = 3
            msg += f"🎵 **Song!** Spiritual melody restores **{regen} SP**{charm_txt or '!'}  *(3 turn cd)*"; color = 0x2ECC71

        elif ability_key == "prayer":
            if attacker.prayer_active:
                attacker.sp += actual_cost; msg += f"🙏 Prayer already active!"
                cd_blocked = True
            else:
                attacker.prayer_active = True
                msg += f"🙏 All damage **+50%** — but bleeds **5-8 HP/turn** forever!"; color = 0xE74C3C

        elif ability_key == "study":
            attacker.study_skip = True; attacker.sp += actual_cost
            msg += f"📚 Studying — SP fully restored next turn at cost of that turn!"; color = 0x3498DB

        elif ability_key == "spectate":
            # Spectator Seq 9 — spirit vision; see through defenses and read opponent's state
            attacker.spectate_active = True  # improves flee chance, handled in flee logic
            # Reveal opponent's current SP and active buffs as intel
            defender_buffs = defender.get_status_line() if hasattr(defender, "get_status_line") else "unknown"
            attacker.spectate_cooldown = 3
            msg += (f"👁️ **Spectate!** Spirit Vision activated — **{defender.user.display_name}** has "
                    f"**{defender.sp}/{defender.max_sp} SP** | Status: **{defender_buffs or 'none'}**\n"
                    f"Flee chance improved to **60%** for **3 turns**. *(3 turn cd)*"); color = 0x9B59B6

        elif ability_key == "planting":
            if attacker.planting_cooldown > 0:
                attacker.sp += actual_cost
                msg += f"🌱 **Planting** on cooldown for **{attacker.planting_cooldown}** more turn(s)!"; color = 0x95A5A6
                cd_blocked = True
            elif attacker.planting_turns > 0:
                attacker.sp += actual_cost; msg += f"🌱 Already growing!"
            else:
                attacker.planting_turns = 3
                attacker.planting_cooldown = 2
                msg += f"🌱 **Planting!** Recovers seq-scaled HP/turn for **3 turns**! *(2 turn cd)*"; color = 0x2ECC71

        elif ability_key == "curing":
            sp_cost = int(attacker.max_sp * 0.24)
            if attacker.sp + actual_cost < sp_cost:
                attacker.sp += actual_cost
                await interaction.followup.send(f"❌ Needs **{sp_cost} SP** total.", ephemeral=True); return
            heal = seq_dmg(attacker, 0.320); attacker.sp = max(0, attacker.sp - sp_cost + actual_cost)
            attacker.hp = min(attacker.max_hp, attacker.hp + heal)
            msg += f"🧪 Recovered **{heal} HP** at cost of **{sp_cost} SP**!"; color = 0x2ECC71

        elif ability_key == "glance_into_fate":
            # Monster (Wheel of Fortune Seq 9) — glance at fate's threads; twist opponent's luck
            # Curses the opponent: their next 3 random rolls are unfavourable
            # Mechanically: 30% miss chance on their next 3 attacks + 30% chance their next ability backfires
            attacker.glance_fate_cooldown = 4
            defender.fate_cursed_turns = 3        # -30% accuracy for 3 turns
            defender.fate_cursed_backfire = True  # next ability has 30% chance to self-hit
            msg += (f"🎲 **Glance into Fate!** **{attacker.user.display_name}** peers at **{defender.user.display_name}**'s fate — "
                    f"**30% miss chance** on their next **3 attacks** and their next ability risks **30% backfire**! *(4 turn cd)*"); color = 0x8E44AD

        elif ability_key == "testimony":
            allowed_gap, gap = seq_gap_check(attacker, defender, max_gap=3)
            if not allowed_gap:
                attacker.sp += actual_cost
                msg += f"📜 **Testimony failed!** Cannot impose testimony on a Beyonder {gap} sequences stronger."; color = 0x95A5A6
                cd_blocked = True
            attacker.testimony_cooldown = 2
            roll = random.randint(1, 3)
            if roll == 1:
                # Option 1: drain 15-25% enemy base HP, capped at 35% user base HP
                pct = random.uniform(0.15, 0.25)
                raw = int(defender.base_hp * pct)
                cap = int(attacker.base_hp * 0.35)
                dmg = min(raw, cap)
                actual_dmg = defender.apply_damage(int(dmg * attacker.get_attack_mult()), is_ability=True)
                msg += f"⚖️ **Testimony — Judgement!** Drained **{actual_dmg} HP** from **{defender.user.display_name}**! *(2 turn cd)*"; color = 0xE74C3C
            elif roll == 2:
                # Option 2: heal 20-25% user base HP
                pct = random.uniform(0.20, 0.25)
                heal = int(attacker.base_hp * pct)
                attacker.hp = min(attacker.max_hp, attacker.hp + heal)
                msg += f"⚖️ **Testimony — Acquittal!** Healed **{heal} HP** *(20-25% base HP)*! *(2 turn cd)*"; color = 0x2ECC71
            else:
                # Option 3: regen 20-25% user base SP
                pct = random.uniform(0.20, 0.25)
                sp_gain = int(attacker.max_sp * pct)
                attacker.sp = min(attacker.max_sp, attacker.sp + sp_gain + actual_cost)  # refund cost too
                msg += f"⚖️ **Testimony — Absolution!** Regained **{sp_gain} SP** *(20-25% base SP)*! *(2 turn cd)*"; color = 0x9B59B6

        elif ability_key == "arbitration":
            if attacker.arbiter_cooldown > 0:
                attacker.sp += actual_cost
                msg += f"⚖️ **Arbiter** on cooldown for **{attacker.arbiter_cooldown}** more turn(s)!"; color = 0x95A5A6
                cd_blocked = True
            elif random.random() < 0.40:
                # 40% failure — disagreement
                attacker.arbiter_cooldown = 6
                msg += f"⚖️ **Arbiter** — **{defender.user.display_name}** refuses to stand down! Arbitration fails. *(6 turn cd)*"; color = 0xE74C3C
            else:
                ceasefire_turns = random.randint(1, 3)
                # Both sides forced into non-violence
                attacker.arbiter_active = True; attacker.arbiter_turns = ceasefire_turns
                defender.arbiter_active = True; defender.arbiter_turns = ceasefire_turns
                attacker.arbiter_cooldown = 6
                msg += (f"⚖️ **ARBITER!** Both fighters stand down for **{ceasefire_turns} turn(s)**!\n"
                        f"⚠️ Attacking during this period summons divine punishment — **40% of the violator's HP**!\n"
                        f"*({attacker.user.display_name} is also bound by this decree)* *(6t cd)*"); color = 0xF1C40F

        elif ability_key == "jurisdiction":
            allowed_gap, gap = seq_gap_check(attacker, defender, max_gap=2)
            if not allowed_gap:
                attacker.sp += actual_cost
                msg += f"⚖️ **Jurisdiction failed!** The authority of a weaker Beyonder cannot bind Seq {get_seq_number(defender.role) if defender.role else '?'}. (gap: {gap})"; color = 0x95A5A6
                cd_blocked = True
            if attacker.jurisdiction_cooldown > 0:
                attacker.sp += actual_cost
                msg += f"🏛️ **Jurisdiction** on cooldown for **{attacker.jurisdiction_cooldown}** more turn(s)!"; color = 0x95A5A6
                cd_blocked = True
            else:
                attacker.jurisdiction_active = True
                attacker.jurisdiction_turns = 3
                attacker.jurisdiction_dmg_boost = 0.20
                attacker.jurisdiction_cooldown = 12
                msg += f"🏛️ **Jurisdiction!** The Sheriff has arrived — **+20% damage** for 3 turns, growing by **+7% each turn**! *(12 turn cd)*"; color = 0xF39C12

        elif ability_key == "memorise":
            pass  # handled via menu

        # ── Seq 8 Role abilities ──────────────────────────

        elif ability_key == "combat_studies":
            attacker.combat_studies_skip = True
            attacker.sp += actual_cost  # refund, costs a turn instead
            msg += f"📘 **{attacker.user.display_name}** begins studying — attacks permanently +20% next turn!"; color = 0x3498DB

        elif ability_key == "excavation":
            roll = random.randint(1, 3)
            if roll == 1:
                gain = random.randint(int(attacker.max_sp * 0.20), int(attacker.max_sp * 0.30))
                attacker.sp = min(attacker.max_sp, attacker.sp + gain)
                msg += f"⛏️ **Excavation!** Found ancient spirit — **+{gain} SP**!"; color = 0x9B59B6
            elif roll == 2:
                gain = random.randint(seq_dmg(attacker, 0.120), seq_dmg(attacker, 0.200))
                attacker.hp = min(attacker.max_hp, attacker.hp + gain)
                msg += f"⛏️ **Excavation!** Found a healing relic — **+{gain} HP**!"; color = 0x2ECC71
            else:
                exc_hp = seq_dmg(attacker, 0.060)
                attacker.stun_block = True
                attacker.hp = min(attacker.max_hp, attacker.hp + exc_hp)
                msg += f"⛏️ **Excavation!** Found an ancient ward — **stun block** + **+{exc_hp} HP**!"; color = 0xF39C12

        elif ability_key == "magic_trick":
            if ritual_choice == "escape":
                speed_diff = attacker.stats["speed"] - defender.stats["speed"]
                flee_chance = max(20, min(80, 50 + speed_diff * 8))
                if random.uniform(0, 100) < flee_chance:
                    bets = active_bets.pop(channel_id, {})
                    for bettor_id, bet in bets.items(): add_pounds(bettor_id, bet["amount"], guild_id=battle["guild_id"])
                    battles.pop(battle_key, None)
                    embed = discord.Embed(description=f"🎩 **{attacker.user.display_name}** vanishes in a puff of smoke!\n\n💰 Bets refunded.", color=0x95A5A6)
                    await interaction.channel.send(embed=embed)
                    old_msg = battle.get("battle_message")
                    if old_msg:
                        try: await old_msg.delete()
                        except: pass
                    return
                else:
                    msg += f"🎩 The escape trick failed — **{attacker.user.display_name}** couldn't vanish!"; color = 0x95A5A6
            else:
                element = random.choice(["freeze", "burn", "stun"])
                if element == "freeze":
                    applied = defender.apply_status_effect("freeze", 2)
                    if applied:
                        attacker.magic_trick_cooldown = 4
                    msg += f"🎩🧊 **Ice Trick!** **{defender.user.display_name}** is frozen for **2 turns**! *(4 turn cd)*" if applied else f"🎩 **{defender.user.display_name}** resisted the freeze!"; color = 0x3498DB
                elif element == "burn":
                    applied = defender.apply_status_effect("burn", 2)
                    if applied:
                        attacker.magic_trick_cooldown = 4
                    msg += f"🎩🔥 **Fire Trick!** **{defender.user.display_name}** burns for **2 turns**! *(4 turn cd)*" if applied else f"🎩 Burn resisted!"; color = 0xE74C3C
                else:
                    applied = defender.apply_status_effect("paralysis", 1)
                    if applied:
                        attacker.magic_trick_cooldown = 2
                    msg += f"🎩⚡ **Stun Trick!** **{defender.user.display_name}** stunned for **1 turn**! *(2 turn cd)*" if applied else f"🎩 Stun resisted!"; color = 0xF39C12

        elif ability_key == "card_tricks":
            # Clown Seq 8 — paper daggers: razor-hard paper that pierces flesh and sticks in bone
            count = random.randint(2, 4)
            total_dmg = 0
            hits = []
            for _ in range(count):
                dmg = defender.apply_damage(int(seq_dmg(attacker, 0.055) * attacker.get_attack_mult()), is_ability=True)
                hits.append(str(dmg))
                total_dmg += dmg
            applied = defender.apply_status_effect("bleed", 2)
            attacker.card_tricks_cooldown = 2
            bleed_txt = " + bleed 2 turns" if applied else ""
            msg += (f"🃏 **Paper Daggers!** **{count}** daggers fly — **{' + '.join(hits)}** = **{total_dmg}** total damage{bleed_txt}! "
                    f"*(2 turn cd)*"); color = 0xE74C3C

        elif ability_key == "swindling":
            attacker.swindling_uses += 1
            fail_chance = min(90, (attacker.swindling_uses - 1) * 4)
            if random.randint(1, 100) <= fail_chance:
                attacker.sp += actual_cost
                msg += f"🎭 **Swindling** failed! *(fail chance was {fail_chance}%)*"; color = 0x95A5A6
            else:
                stolen = min(seq_val(attacker, 20), defender.sp)
                defender.sp = max(0, defender.sp - stolen)
                attacker.sp = min(attacker.max_sp, attacker.sp + stolen)
                msg += f"🎭 **Swindled {stolen} SP** from **{defender.user.display_name}**! *(fail chance {fail_chance}%)*"; color = 0xF39C12

        elif ability_key == "provocation":
            if attacker.provocation_cooldown > 0:
                attacker.sp += actual_cost
                msg += f"😤 **Provocation** on cooldown for **{attacker.provocation_cooldown}** more turn(s)!"; color = 0x95A5A6
                cd_blocked = True
            else:
                attacker.provocation_cooldown = 4
                # Force defender to skip their next 1 turn and drain scaled SP
                defender.trapped = max(getattr(defender, "trapped", 0), 1)
                defender.provocation_trapped = True
                sp_drain = min(seq_val(attacker, 25), defender.sp)
                defender.sp = max(0, defender.sp - sp_drain)
                msg += f"😤 **Provocation!** **{defender.user.display_name}** is enraged into submission — forced to skip **1 turn** and loses **{sp_drain} SP**! *(4 turn cd)*"; color = 0xF39C12

        elif ability_key == "instigation":
            if random.random() < 0.5:
                defender.instigated_action = ritual_choice
                msg += f"😈 **Instigation!** **{defender.user.display_name}** will be forced to use **{ritual_choice}** next turn!"; color = 0x8E44AD
            else:
                msg += f"😈 **Instigation** failed!"; color = 0x95A5A6

        elif ability_key == "moral_freedom":
            if attacker.moral_freedom_used:
                attacker.sp += actual_cost
                msg += f"👼 Moral Freedom already used!"; color = 0x95A5A6
                cd_blocked = True
            else:
                attacker.moral_freedom_used = True
                defender.moral_freedom_active = True
                dot = seq_val(attacker, 15)
                defender._moral_freedom_dot = dot
                msg += f"👼 **Moral Freedom!** **{defender.user.display_name}** suffers **{dot} damage/turn** for the rest of battle!"; color = 0x2C3E50

        elif ability_key == "rage_baited":
            if attacker.rage_baited_cooldown > 0:
                attacker.sp += actual_cost
                msg += f"😡 Rage Baited on cooldown!"; color = 0x95A5A6
                cd_blocked = True
            else:
                attacker.rage_baited_turns = 2
                attacker.rage_baited_cooldown = 2
                msg += f"😡 **Rage Baited!** Double damage at 50% miss for **2 turns**!"; color = 0xE74C3C

        elif ability_key == "fighting_spirit":
            # Pugilist Seq 8 — iron fist fighting spirit; rouses physical power
            if attacker.fighting_spirit_active:
                attacker.sp += actual_cost
                msg += f"👊 **Fighting Spirit** already active!"; color = 0x95A5A6
                cd_blocked = True
            else:
                attacker.fighting_spirit_active = True
                attacker.fighting_spirit_turns = 4
                attacker.fighting_spirit_boost = 0.30  # +30% attack
                attacker.fighting_spirit_cooldown = 6
                msg += (f"👊 **Fighting Spirit!** **{attacker.user.display_name}** ignites their iron will — "
                        f"**+30% physical damage** for **4 turns**! Stuns from lower-seq opponents are halved. *(6 turn cd)*"); color = 0xE74C3C

        elif ability_key == "poem":
            attacker.poem_cooldown = 3
            roll = random.randint(1, 3)
            if roll == 1:
                applied = defender.apply_status_effect("slumber", 1)
                if applied:
                    msg += f"📜 **Midnight Poem!** The verse lulls **{defender.user.display_name}** to sleep for **1 turn**! *(3 turn cd)*"; color = 0x9B59B6
                else:
                    attacker.sp += actual_cost
                    msg += f"📜 **Midnight Poem!** The sleep effect was resisted! *(3 turn cd)*"; color = 0x95A5A6
            elif roll == 2:
                self_dmg = seq_val(attacker, 15)
                attacker.hp = max(0, attacker.hp - self_dmg)
                msg += f"📜 **Midnight Poem!** The dark verse turns inward — **{attacker.user.display_name}** suffers **{self_dmg} self-inflicted damage**! *(3 turn cd)*"; color = 0xE74C3C
            else:
                drained = min(seq_val(attacker, 20), defender.sp)
                defender.sp = max(0, defender.sp - drained)
                msg += f"📜 **Midnight Poem!** The verse drains **{drained} SP** from **{defender.user.display_name}**! *(3 turn cd)*"; color = 0x8E44AD

        elif ability_key == "burial_restoration":
            restored = attacker.max_sp - attacker.sp
            attacker.sp = attacker.max_sp
            attacker.burial_cooldown = 4
            msg += f"⚰️ **Burial Restoration!** **{attacker.user.display_name}** honours the grave and receives a soul's blessing — SP fully restored! **(+{restored} SP)**"; color = 0x8E44AD

        elif ability_key == "listening":
            attacker.listening_active = True
            attacker.sp = attacker.max_sp - actual_cost
            msg += f"👂 **{attacker.user.display_name}** listens — SP fully restored! Each turn has a 1/3 chance to stun."; color = 0x9B59B6

        elif ability_key == "power_up_punch":
            if attacker.folk_of_rage_active:
                attacker.sp += actual_cost
                msg += f"👊 **Power Up Punch** already active!"
                cd_blocked = True
            else:
                attacker.folk_of_rage_active = True
                attacker.folk_of_rage_turns = 5
                attacker.folk_of_rage_pct = 0.05
                msg += f"👊 **Power Up Punch!** Normal strikes amplified — grows up to **25%** over **5 rounds**!"; color = 0xE74C3C

        elif ability_key == "ritualistic_reasoning":
            if attacker.ritualistic_reasoning_cooldown > 0:
                attacker.sp += actual_cost
                msg += f"🔬 **Ritualistic Reasoning** on cooldown for **{attacker.ritualistic_reasoning_cooldown}** turn(s)!"; cd_blocked = True
            else:
                defender.sabotaged = True
                attacker.ritualistic_reasoning_cooldown = 1
                msg += f"🔬 **Ritualistic Reasoning!** **{defender.user.display_name}**'s next move will fail or backfire! *(1 turn cd)*"; color = 0x8E44AD

        elif ability_key == "reading":
            attacker.reading_prediction = ritual_choice
            msg += f"🔭 **Reading!** **{attacker.user.display_name}** focuses their mind, silently predicting **{defender.user.display_name}**'s next move…"; color = 0x9B59B6

        elif ability_key == "brilliant_light":
            actual_dmg = apply_ability_damage(attacker, defender, 0.045)
            applied = defender.apply_status_effect("paralysis", 1)
            if applied:
                attacker.brilliant_light_cooldown = 2
            stun_txt = " + stunned 1 turn *(2 turn cd)*" if applied else ""
            msg += f"☀️ **Brilliant Light!** **{actual_dmg}** damage{stun_txt}!"; color = 0xF1C40F

        elif ability_key == "treatment":
            # Clear all existing status effects first
            cleared = []
            for attr, label in [
                ("bleed","bleed"), ("burn","burn"), ("paralysis","paralysis"),
                ("slumber","sleep"), ("freeze","freeze"), ("trapped","trap"),
                ("debuff_stacks","debuff"), ("sneak_attack_pending","sneak attack"),
                ("moral_freedom_active","moral freedom"),
            ]:
                if getattr(attacker, attr, 0):
                    setattr(attacker, attr, 0); cleared.append(label)
            if attacker.freeze_weakened:          attacker.freeze_weakened = False;          cleared.append("freeze weakness")
            if attacker.gun_shot_bleed:           attacker.gun_shot_bleed = False;           cleared.append("gunshot bleed")
            if attacker.charm_turns > 0:          attacker.charm_turns = 0;                  cleared.append("charm")
            if attacker.scroll_status:            attacker.scroll_status = None;             cleared.append("scroll")
            if attacker.judgement_debuff_turns>0: attacker.judgement_debuff_turns = 0;       cleared.append("judgement")
            if attacker.curse_of_misfortune_turns>0: attacker.curse_of_misfortune_turns = 0; cleared.append("misfortune")
            if attacker.spiritual_takeover_active: attacker.spiritual_takeover_active = False; cleared.append("spirit takeover")
            if getattr(attacker,"dream_alteration_acc_loss",0)>0: attacker.dream_alteration_acc_loss=0; cleared.append("dream alteration")
            attacker.status_immune = 3
            cleared_txt = f" Cleared: **{', '.join(cleared)}**." if cleared else ""
            msg += f"💊 **Treatment!**{cleared_txt} Immune to status effects for **3 turns**!"; color = 0x2ECC71

        elif ability_key == "animal_companion":
            attacker.animal_companion_active = True
            attacker.animal_companion_turn = 0
            ac_dmg = seq_val(attacker, 10)
            msg += f"🐾 **Animal Companion** summoned — attacks **{defender.user.display_name}** every other turn for **{ac_dmg} damage**!"; color = 0x2ECC71

        elif ability_key == "computing":
            # Robot Seq 8 — compute optimal attack vectors; guarantee accuracy for limited duration
            if attacker.computing_active:
                attacker.sp += actual_cost
                msg += f"🤖 **Computing** already active!"; color = 0x95A5A6
                cd_blocked = True
            else:
                attacker.computing_active = True
                attacker.computing_turns = 4
                attacker.computing_cooldown = 6
                msg += (f"🤖 **Computing!** **{attacker.user.display_name}** calculates optimal attack vectors — "
                        f"**100% accuracy** and **+15% crit chance** for **4 turns**! *(6 turn cd)*"); color = 0x3498DB

        elif ability_key == "domination":
            allowed_gap, gap = seq_gap_check(attacker, defender, max_gap=2)
            if not allowed_gap:
                attacker.sp += actual_cost
                msg += f"🧠 **Domination failed!** Too strong to dominate. (gap: {gap})"; color = 0x95A5A6; cd_blocked = True
            elif attacker.domination_cooldown > 0:
                attacker.sp += actual_cost
                msg += f"👊 **Domination** on cooldown for **{attacker.domination_cooldown}** more turn(s)!"; color = 0x95A5A6; cd_blocked = True
            else:
                attacker.domination_active = True
                attacker.domination_cooldown = 5
                if random.random() < 0.5:
                    attacker.domination_turns = 3
                    attacker.domination_boost = 1.0  # +100%
                    msg += f"👊 **Domination!** **+100% all damage** for **3 turns**! *(5 turn cd)*"; color = 0xE74C3C
                else:
                    attacker.domination_turns = 5
                    attacker.domination_boost = 0.5  # +50%
                    msg += f"👊 **Domination!** **+50% all damage** for **5 turns**! *(5 turn cd)*"; color = 0xE74C3C

        elif ability_key == "gun_shot":
            attacker.gun_shot_cooldown = 4
            actual_dmg = apply_ability_damage(attacker, defender, 0.068)
            defender.gun_shot_bleed = True
            msg += f"🔫 **Gun Shot!** **{actual_dmg}** damage + permanent bleed **5/turn** for the rest of battle! *(4 turn cd)*"; color = 0xE74C3C

        # ── Seq 7 Role abilities ──────────────────────────

        elif ability_key == "paper_figurine":
            attacker.paper_figurine_turns = 1
            msg += f"🪆 **Paper Figurine!** All attacks against **{attacker.user.display_name}** fail for **1 turn**!"; color = 0x9B59B6

        elif ability_key == "astrology":
            attacker.astrology_used = True
            atk_seq = get_seq_number(attacker.role) if attacker.role else 9
            def_seq = get_seq_number(defender.role) if defender.role else 9
            if def_seq < atk_seq:
                # Opponent is stronger — flee or stand
                attacker.astrology_flee_ready = True
                msg += f"🌟 **Astrology!** The stars foretell doom — **{defender.user.display_name}** outranks you! Choose to flee or stand your ground."; color = 0x9B59B6
            elif def_seq == atk_seq:
                # Same seq — only 20% dodge chance
                if random.random() < 0.20:
                    astro_dmg = int(attacker.hp * 0.40)
                    actual_astro_dmg = defender.apply_damage(astro_dmg, is_ability=True)
                    msg += f"🌟 **Astrology!** *(same seq — 20% chance)* The stars aligned — **{actual_astro_dmg}** damage (40% current HP)! *(one-time use)*"; color = 0x9B59B6
                else:
                    attacker.sp += actual_cost
                    msg += f"🌟 **Astrology!** *(same seq — 20% chance)* The stars did not align this time!"; color = 0x95A5A6
                    cd_blocked = True
            else:
                # Opponent is weaker — full 40% HP damage
                astro_dmg = int(attacker.hp * 0.40)
                actual_astro_dmg = defender.apply_damage(astro_dmg, is_ability=True)
                msg += f"🌟 **Astrology!** The stars align — **{actual_astro_dmg}** damage (40% current HP)! *(one-time use)*"; color = 0x9B59B6

        elif ability_key == "observation":
            attacker.observation_active = True
            attacker.observation_cooldown = 2
            msg += f"🔎 **Observation!** **{attacker.user.display_name}** takes **40% less damage** from all sources next turn! *(2 turn cd)*"; color = 0x3498DB

        elif ability_key == "therapy":
            attacker.sp += actual_cost  # refund — costs a turn instead
            cleared = []
            for attr, name in [("bleed","bleed"),("paralysis","paralysis"),("burn","burn"),
                                ("freeze","freeze"),("slumber","sleep"),("defence_reduction","defence reduction"),
                                ("moral_freedom_active","moral freedom"),("debuff_stacks","debuff stacks")]:
                if getattr(attacker, attr, 0):
                    setattr(attacker, attr, 0); cleared.append(name)
            if attacker.freeze_weakened:         attacker.freeze_weakened = False;          cleared.append("freeze weakened")
            if attacker.gun_shot_bleed:          attacker.gun_shot_bleed = False;           cleared.append("gunshot bleed")
            if attacker.sneak_attack_pending:    attacker.sneak_attack_pending = False;     cleared.append("sneak attack")
            if attacker.trapped > 0:             attacker.trapped = 0;                      cleared.append("trapped")
            if attacker.charm_turns > 0:         attacker.charm_turns = 0; attacker.charm_chance = 0.0; cleared.append("charm")
            if attacker.scroll_status:           attacker.scroll_status = None;             cleared.append("scroll affliction")
            if attacker.notary_debuff_turns > 0: attacker.notary_debuff_turns = 0;          cleared.append("notary debuff")
            attacker.therapy_cooldown = 1
            msg += f"🛋️ **Therapy!** Cleared: **{', '.join(cleared) if cleared else 'nothing'}**! Turn lost. *(1 turn cd)*"; color = 0x2ECC71
            # Costs the turn — swap and return early
            swap_turn(battle)
            v = check_victory(attacker, defender, battle_key, msg)
            if v:
                await interaction.channel.send(embed=v)
                old_msg = battle.get("battle_message")
                if old_msg:
                    try: await old_msg.delete()
                    except: pass
                return
            embed = make_battle_embed(msg, battle, battle["turn"].display_name, color)
            await send_and_replace(battle, interaction.channel, embed, view=_make_battle_view(battle))
            return

        elif ability_key == "holy_water":
            hw_instant = max(1, int(attacker.max_hp * 0.15))
            attacker.hp = min(attacker.max_hp, attacker.hp + hw_instant)
            attacker.holy_water_regen = 2
            msg += f"💧 **Holy Water!** Healed **{hw_instant} HP** (15% max HP) + **~3% HP/turn** for **2 turns**!"; color = 0x2ECC71

        elif ability_key == "water_bullet":
            actual_dmg = apply_ability_damage(attacker, defender, 0.136, skill_key="water_bullet")
            freeze_applied = False
            if random.random() < 0.25:
                freeze_applied = defender.apply_status_effect("freeze", 1)
            attacker.water_bullet_cooldown = 2
            freeze_txt = " + frozen 1 turn" if freeze_applied else ""
            msg += f"💧🔫 **Water Bullet!** **{actual_dmg}** damage{freeze_txt}!"; color = 0x3498DB

        elif ability_key == "sneak_attack":
            actual_dmg = apply_ability_damage(attacker, defender, 0.136, skill_key="sneak_attack")
            defender.sneak_attack_pending = True
            attacker.sneak_attack_cooldown = 2
            msg += f"🥷 **Sneak Attack!** **{actual_dmg}** damage — **{defender.user.display_name}** loses their next turn! *(2 turn cd)*"; color = 0x2C3E50

        elif ability_key == "analyse":
            skill_to_analyse = ritual_choice
            defender.analysed_skills[skill_to_analyse] = 0.30
            attacker.analyse_uses -= 1
            skill_info, _ = get_ability_info(skill_to_analyse)
            skill_name = skill_info["name"] if skill_info else skill_to_analyse
            msg += (f"🧐 **Analyse!** **{skill_name}** from **{defender.user.display_name}** permanently deals **30% less** damage! "
                    f"*({attacker.analyse_uses} use(s) left)*"); color = 0x3498DB

        elif ability_key == "sleep_spell":
            applied = defender.apply_status_effect("slumber", 2)
            attacker.sleep_spell_cooldown = 4
            if applied:
                msg += f"😴 **Sleep Spell!** **{defender.user.display_name}** slumbers for **2 turns**! *(4 turn cd)*"; color = 0x9B59B6
            else:
                attacker.sp += actual_cost
                msg += f"😴 Sleep Spell resisted!"; color = 0x95A5A6

        elif ability_key == "summoning_dead":
            roll = random.randint(1, 100)
            if roll <= 5:
                spirits = 3
            elif roll <= 30:
                spirits = 2
            else:
                spirits = 1
            sp_cost = 30 if spirits == 3 else 20
            if attacker.sp < sp_cost:
                await interaction.followup.send(f"❌ Need **{sp_cost} SP** — only have **{attacker.sp}**.", ephemeral=True); return
            attacker.sp -= sp_cost
            total_dmg = sum(apply_ability_damage(attacker, defender, 0.023) for _ in range(spirits))
            stun_applied = False
            if spirits >= 2 and defender.can_receive_status():
                stun_applied = defender.apply_status_effect("paralysis", 1)
            attacker.summoning_dead_cooldown = 2
            stun_txt = " + stun" if stun_applied else ""
            msg += f"💀 **Summoning Dead!** **{spirits}** spirit(s) — **{total_dmg}** damage{stun_txt}!"; color = 0x8E44AD

        elif ability_key == "weapon_throw":
            actual_dmg = apply_ability_damage(attacker, defender, 0.182, skill_key="weapon_throw")
            applied = defender.apply_status_effect("bleed", 3)
            attacker.weapon_throw_cooldown = 4
            bleed_txt = " + bleed 3 turns" if applied else ""
            msg += f"🗡️ **Weapon Throw!** **{actual_dmg}** damage{bleed_txt}! *(4 turn cd)*"; color = 0xE74C3C

        elif ability_key == "witch_curse":
            if attacker.witch_curse_cooldown > 0:
                attacker.sp += actual_cost
                msg += f"🧙 **Witch Curse** on cooldown ({attacker.witch_curse_cooldown}t)!"; color = 0x95A5A6; cd_blocked = True
            else:
                attacker.witch_curse_cooldown = 4
                roll = random.random()
                if roll < 0.40:
                    # Black Flame — ~20 dmg/turn + SP drain for 3 turns
                    black_dmg = seq_val(attacker, 20)
                    defender.hp = max(0, defender.hp - black_dmg)
                    defender._black_flame_dot_turns = 3; defender._black_flame_dot_dmg = black_dmg
                    defender._black_flame_sp_drain = 10
                    msg += f"🔥 **Witch Curse — Black Flame!** **{black_dmg}** damage + **{black_dmg} dmg/turn** + **10 SP drain/turn** for **3 turns**! *(4t cd)*"; color = 0x8E44AD
                elif roll < 0.70:
                    # Frost — stun in ice for 2 turns
                    applied = defender.apply_status_effect("stun", 2) or defender.apply_status_effect("freeze", 2)
                    msg += f"❄️ **Witch Curse — Frost!** **{defender.user.display_name}** frozen in ice for **2 turns**! *(4t cd)*"; color = 0x3498DB
                else:
                    # Mirror Substitution — secretly take all damage meant for you next turn
                    attacker._mirror_substitution_active = True
                    msg += f"🪞 **Witch Curse — Mirror Substitution!** Set secretly... *(4t cd)*"; color = 0x9B59B6

        elif ability_key == "fire_ravens":
            if attacker.fire_ravens_cooldown > 0:
                attacker.sp += actual_cost
                msg += f"🐦‍🔥 **Fire Ravens** on cooldown ({attacker.fire_ravens_cooldown}t)!"; color = 0x95A5A6; cd_blocked = True
            else:
                # 40% → 3, 25% → 4, 15% → 5, 10% → 6 (10-20% → 7)
                roll = random.random()
                if roll < 0.40:   count = 3
                elif roll < 0.65: count = 4
                elif roll < 0.80: count = 5
                elif roll < 0.90: count = 6
                else:             count = 7
                total_dmg = sum(apply_ability_damage(attacker, defender, 0.038) for _ in range(count))
                attacker.fire_ravens_cooldown = 5
                msg += f"🐦‍🔥 **Fire Ravens! {count}** ravens strike — **{total_dmg}** total damage! *(5t cd)*"; color = 0xE74C3C

        elif ability_key == "magic_spell":
            if attacker.magic_spell_cooldown > 0:
                attacker.sp += actual_cost
                msg += f"✨ **Magic Spell** is on cooldown for **{attacker.magic_spell_cooldown}** more turn(s)!"; color = 0x95A5A6
                cd_blocked = True
            else:
                attacker.magic_spell_cooldown = 1
                roll = random.randint(1, 3)
                if roll == 1:
                    attacker.damage_boost_pct = min(1.0, attacker.damage_boost_pct + 0.30)
                    msg += f"✨ **Magic Spell!** Permanent **+30% damage boost**!"; color = 0xF1C40F
                elif roll == 2:
                    ms_heal = seq_dmg(attacker, 0.150)
                    attacker.hp = min(attacker.max_hp, attacker.hp + ms_heal)
                    msg += f"✨ **Magic Spell!** Healed **{ms_heal} HP**!"; color = 0x2ECC71
                else:
                    ms_sp = max(1, int(attacker.max_sp * 0.20))
                    attacker.sp = min(attacker.max_sp, attacker.sp + ms_sp + actual_cost)
                    msg += f"✨ **Magic Spell!** Restored **{ms_sp} SP**!"; color = 0x9B59B6

        elif ability_key == "appraisal":
            attacker.appraisal_active = True
            attacker.appraisal_cooldown = 3
            msg += f"🏷️ **Appraisal!** Next action deals **40% more** damage! *(3 turn cd)*"; color = 0xF39C12

        elif ability_key == "lucky_day":
            attacker.lucky_day_used = True
            defender.lucky_day_turns = 4
            msg += f"🍀 **Lucky Day!** **{defender.user.display_name}**'s hit chance halved for **4 rounds**!"; color = 0x2ECC71

        elif ability_key == "blood_sucker":
            drain = random.randint(seq_dmg(attacker, 0.068), seq_dmg(attacker, 0.136))
            actual_dmg = apply_ability_damage(attacker, defender, drain / SEQ_AVG_HP.get(get_seq_number(attacker.role) if attacker.role else 9, 220), skill_key="blood_sucker")
            attacker.hp = min(attacker.max_hp, attacker.hp + actual_dmg)
            attacker.blood_sucker_cooldown = 1
            msg += f"🧛 **Blood Sucker!** Drained **{actual_dmg} HP** from **{defender.user.display_name}**! *(1 turn cd)*"; color = 0x8E44AD

        elif ability_key == "vine_bomb":
            actual_dmg = apply_ability_damage(attacker, defender, 0.070, skill_key="vine_bomb")
            defender.trapped = 2
            attacker.vine_bomb_cooldown = 4
            msg += f"🌿 **Vine Bomb!** **{actual_dmg}** damage + **{defender.user.display_name}** trapped **2 turns**! *(4 turn cd)*"; color = 0x2ECC71

        elif ability_key == "transformation":
            if attacker.transformation_used:
                attacker.sp += actual_cost
                msg += f"🐺 Already transformed!"
                cd_blocked = True
            else:
                attacker.transformation_used = True
                attacker.transformed = True
                bonus_hp = int(attacker.max_hp * 0.5)  # 1.5× total (add 0.5× current)
                attacker.transform_hp_bonus = bonus_hp
                attacker.max_hp += bonus_hp
                attacker.hp = min(attacker.max_hp, attacker.hp + bonus_hp)
                attacker.transform_dodge_reduction = 0.15  # starts lower — debuffed
                attacker.transform_regen = 5
                attacker.transform_turns = 0
                msg += (f"🐺 **Transformation!** **{attacker.user.display_name}** shifts into werewolf form! "
                        f"HP ×1.5 (+{bonus_hp}), **5 HP regen/turn**. "
                        f"Dodge starts at **15%**, rises **5%/round** (max 85%). Lasts **5 turns**. Reverts to **50% HP**!"); color = 0x8E44AD

        elif ability_key == "vile_crime":
            if getattr(attacker, "vile_crime_cd", 0) > 0:
                attacker.sp += actual_cost
                msg += f"🔪 **Vile Crime** on cooldown ({attacker.vile_crime_cd}t)!"; cd_blocked = True
            elif attacker.vile_crime_used:
                attacker.sp += actual_cost
                msg += f"🔪 **Vile Crime** already completed!"; cd_blocked = True
            elif attacker.vile_crime_count == 0:
                attacker.vile_crime_count = 1
                attacker.vile_crime_hits_needed = 2  # track 2 hits
                msg += f"🔪 **Vile Crime!** Tracking — land **2 standard attacks** to apply **bleed 6 turns**! *(0/2)*"; color = 0xE74C3C
            else:
                attacker.sp += actual_cost
                msg += f"🔪 Vile Crime already tracking — use standard attacks! *({attacker.vile_crime_count}/2)*"; cd_blocked = True

        elif ability_key == "bribe":
            if attacker.bribe_cooldown > 0:
                attacker.sp += actual_cost
                msg += f"💰 **Bribe** on cooldown for **{attacker.bribe_cooldown}** more turn(s)!"; color = 0x95A5A6
            else:
                cost_soli = 70000
                bal = get_pounds(attacker.user.id, guild_id=battle["guild_id"])
                if bal < cost_soli:
                    attacker.sp += actual_cost
                    await interaction.followup.send(f"❌ **Bribe** needs **{cost_soli:,} soli** — you only have **{bal:,}**.", ephemeral=True); return
                # Fail chance: 10% per spirit stat enemy has over attacker
                spirit_diff = max(0, defender.stats.get("spirit", 0) - attacker.stats.get("spirit", 0))
                fail_chance = spirit_diff * 0.10
                if random.random() < fail_chance:
                    # Failed — 50% soli refunded, 50% lost
                    refund = cost_soli // 2
                    remove_pounds(attacker.user.id, cost_soli - refund, guild_id=battle["guild_id"])
                    attacker.bribe_cooldown = 5
                    msg += (f"💰 **Bribe failed!** **{defender.user.display_name}**'s spirit resisted the bribe — "
                            f"**{refund:,} soli** refunded, **{refund:,} soli** lost. *(5 turn cd)*"); color = 0x95A5A6
                else:
                    remove_pounds(attacker.user.id, cost_soli, guild_id=battle["guild_id"])
                    add_pounds(defender.user.id, cost_soli, guild_id=battle["guild_id"])
                    bribe_choice = ritual_choice if ritual_choice in ("weaken", "charm", "connect") else "weaken"
                    attacker.bribe_pending = bribe_choice
                    attacker.bribe_cooldown = 5
                    attacker.bribe_concealed_turn = True
                    # Fully concealed — only show evade
                    msg += f"⚔️ **{attacker.user.display_name}** attacks — **{defender.user.display_name}** evades!"; color = 0x95A5A6

        elif ability_key == "mental_piercing":
            actual_dmg = apply_ability_damage(attacker, defender, 0.136, skill_key="mental_piercing")
            sp_drain = min(max(1, int(defender.max_sp * 0.10)), defender.sp)
            defender.sp = max(0, defender.sp - sp_drain)
            attacker.mental_piercing_cooldown = 2
            msg += f"🧠 **Mental Piercing!** **{actual_dmg}** HP damage + **{sp_drain} SP** drained! *(2 turn cd)*"; color = 0xE74C3C

        # ── Seq 6 Role abilities ──────────────────────────

        elif ability_key == "faceless":
            allowed_gap, gap = seq_gap_check(attacker, defender, max_gap=2)
            if not allowed_gap:
                attacker.sp += actual_cost
                msg += f"👤 **Faceless failed!** Cannot steal the identity of someone {gap} sequences stronger."; color = 0x95A5A6
                cd_blocked = True
            actual_dmg = apply_ability_damage(attacker, defender, 0.159, is_ability=True, skill_key="faceless")
            applied = defender.apply_status_effect("paralysis", 1)
            attacker.faceless_cooldown = 2
            stun_txt = " + turn skip" if applied else ""
            msg += f"🎭 **Faceless!** Betrayal strikes — **{actual_dmg}** damage{stun_txt}! *(2 turn cd)*"; color = 0x8E44AD

        elif ability_key == "prometheus":
            if attacker.prometheus_cooldown > 0:
                attacker.sp += actual_cost
                msg += f"🔥 **Prometheus** on cooldown for **{attacker.prometheus_cooldown}** more turn(s)!"; color = 0x95A5A6
                cd_blocked = True
            else:
                # Build list of abilities the defender has USED this battle
                used = list(getattr(defender, "abilities_used_this_battle", set()))
                if not used:
                    attacker.sp += actual_cost
                    msg += f"🔥 **Prometheus** — nothing to steal yet!"; color = 0x95A5A6
                    cd_blocked = True
                else:
                    atk_seq = get_seq_number(attacker.role) if attacker.role else 9
                    def_seq = get_seq_number(defender.role) if defender.role else 9
                    if atk_seq <= def_seq:
                        # Higher seq (lower number) or equal — pick any used ability
                        stolen = ritual_choice if ritual_choice and ritual_choice in used else used[0]
                    else:
                        # Lower seq — steal random
                        stolen = random.choice(used)

                    # Remove from defender permanently
                    if hasattr(defender, "abilities_used_this_battle"):
                        defender.abilities_used_this_battle.discard(stolen)
                    if defender.role_ability == stolen:
                        defender.role_ability = None
                    attacker.prometheus_stolen_ability = stolen
                    attacker.prometheus_cooldown = 3
                    info_stolen, _ = get_ability_info(stolen)
                    sname = info_stolen["name"] if info_stolen else stolen
                    strip_log = []

                    if stolen == "hurricane_of_light" and defender.hurricane_charging:
                        defender.hurricane_charging = False
                        strip_log.append("Hurricane charge interrupted")
                    elif stolen == "devil_form" and defender.devil_form_active:
                        defender.devil_form_active = False
                        hp_remove = defender.devil_form_hp_bonus
                        defender.max_hp = max(1, defender.max_hp - hp_remove)
                        defender.hp = min(defender.max_hp, defender.hp)
                        defender.devil_form_hp_bonus = 0
                        sp_remove = defender.devil_form_sp_bonus
                        defender.max_sp = max(1, defender.max_sp - sp_remove)
                        defender.sp = min(defender.max_sp, defender.sp)
                        defender.devil_form_hp_bonus = 0
                        defender.damage_boost_pct = max(0.0, defender.damage_boost_pct - 0.50)
                        strip_log.append("Devil Form shattered")
                    elif stolen == "spirit_guide" and defender.spirit_guide_active:
                        defender.spirit_guide_active = False
                        defender.spirit_guide_turns = 0
                        defender.spirit_guide_cooldown = 7
                        strip_log.append("Spirit Guide banished")
                    elif stolen == "transformation" and defender.transformed:
                        defender.transformed = False
                        bonus_hp = defender.max_hp * 2 // 3
                        defender.max_hp = max(1, defender.max_hp - bonus_hp)
                        defender.hp = min(defender.max_hp, defender.hp)
                        strip_log.append("Transformation broken")
                    elif stolen == "night_boost" and defender.night_boost_active:
                        defender.night_boost_active = False
                        defender.night_boost_damage = 0
                        strip_log.append("Night Boost extinguished")
                    elif stolen == "prayer" and defender.prayer_active:
                        defender.prayer_active = False
                        strip_log.append("Prayer silenced")
                    elif stolen == "combat_studies" and defender.combat_studies_active:
                        defender.combat_studies_active = False
                        strip_log.append("Combat Studies erased")

                    strip_txt = f"\n> Torn down: *{'; '.join(strip_log)}*" if strip_log else ""
                    pick_txt = " *(you picked it)*" if atk_seq <= def_seq else " *(random steal)*"
                    msg += f"🔥 **Prometheus!** Stole **{sname}**{pick_txt} from **{defender.user.display_name}**!{strip_txt} Use it via Pathway. *(3 turn cd)*"; color = 0xE67E22

        elif ability_key == "scribe":
            if attacker.scribe_cooldown > 0:
                attacker.sp += actual_cost
                msg += f"📖 **Scribe** on cooldown for **{attacker.scribe_cooldown}** more turn(s)!"; color = 0x95A5A6
                cd_blocked = True
            else:
                # Mark pending — will copy the NEXT ability used against attacker (even while stunned)
                attacker.scribe_pending = True
                attacker.scribe_record_in = 1   # record ability used against us next turn
                attacker.scribe_cooldown = 3
                msg += f"📖 **Scribe!** Soul as pen, spirit body as paper — the next ability used against you will be recorded, even through a stun! *(3 turn cd)*"; color = 0x3498DB

        elif ability_key == "hypnotist":
            attacker.hypnotist_cooldown = 2
            actual_dmg = defender.apply_damage(seq_dmg(attacker, 0.091), is_ability=True)
            applied = defender.apply_status_effect("paralysis", 1)
            stun_txt = " + stunned 1 turn" if applied else " *(stun resisted)*"
            msg += f"🌀 **Hypnotist!** **{defender.user.display_name}** is deep in trance — **{actual_dmg}** self-damage{stun_txt}! *(2 turn cd)*"; color = 0x8E44AD

        elif ability_key == "notary":
            attacker.notary_cooldown = 4
            if ritual_choice == "buff_self":
                attacker.notary_buff_turns = 3
                msg += f"📜 **Notary!** Proclaimed in the name of your god — **+50% damage** for **3 turns**! *(4 turn cd)*"; color = 0xF1C40F
            elif ritual_choice == "debuff_opponent":
                defender.notary_debuff_turns = 2
                msg += f"📜 **Notary!** Proclaimed weakness upon **{defender.user.display_name}** — **−50% damage** for **2 turns**! *(4 turn cd)*"; color = 0x3498DB

        elif ability_key == "wind_blessed":
            if _is_scribe_copy:
                # Scribe copy: fire immediately — no build-up, no extra SP cost
                attacker.wind_blessed_cooldown = 5
                if defender.phasing_active:
                    defender.phasing_active = False
                    defender.phasing_cooldown = 1
                    msg += f"🌪️ **WIND BLESSED (Copied)!** — **{defender.user.display_name}** phases through the blast!"; color = 0x3498DB
                else:
                    actual_dmg = defender.apply_damage(seq_dmg(attacker, 0.273), is_ability=True)
                    msg += f"🌪️ **WIND BLESSED (Copied)!** Unleashed immediately — **{actual_dmg}** damage! *(5 turn cd)*"; color = 0x3498DB
            elif attacker.wind_blessed_buildup == 0:
                if attacker.sp >= 25:
                    attacker.sp -= 25
                    attacker.wind_blessed_buildup = 1
                    msg += f"🌪️ **Wind Blessed** — channelling wind *(round 1/2, −25 SP)*. Attack next turn to continue building!"; color = 0x3498DB
                else:
                    attacker.sp += actual_cost
                    msg += f"🌪️ Not enough SP to begin Wind Blessed!"; color = 0x95A5A6
            else:
                attacker.sp += actual_cost
                msg += f"🌪️ Wind Blessed is already building — wait for it to fire automatically!"; color = 0x95A5A6
                cd_blocked = True

        elif ability_key == "rose_bishop":
            attacker.rose_bishop_cooldown = 2
            if ritual_choice == "flesh_bomb":
                fb_hp_cost = seq_val(attacker, 20)
                fb_sp_cost = seq_val(attacker, 15)
                if attacker.hp <= fb_hp_cost:
                    attacker.sp += actual_cost
                    msg += f"🌹 **Flesh Bomb** — not enough HP to cast (need >{fb_hp_cost} HP)!"; color = 0x95A5A6
                    cd_blocked = True
                else:
                    attacker.hp = max(1, attacker.hp - fb_hp_cost)
                    attacker.sp = max(0, attacker.sp - fb_sp_cost)
                    actual_dmg = apply_ability_damage(attacker, defender, 0.136, is_ability=True)
                    msg += f"🌹 **Flesh Bomb!** **{actual_dmg}** damage at cost of **{fb_hp_cost} HP + {fb_sp_cost} SP**! *(2 turn cd)*"; color = 0xE74C3C
            elif ritual_choice == "blood_regen":
                if attacker.sp < 25:
                    attacker.sp += actual_cost
                    msg += f"🌹 **Blood Regen** — need at least 25 SP!"; color = 0x95A5A6
                    cd_blocked = True
                else:
                    rr_heal = seq_dmg(attacker, 0.150)
                    attacker.sp = max(0, attacker.sp - 25)
                    attacker.hp = min(attacker.max_hp, attacker.hp + rr_heal)
                    msg += f"🌹 **Blood Regen!** Restored **{rr_heal} HP** at cost of **25 SP**! *(2 turn cd)*"; color = 0x2ECC71

        elif ability_key == "polymath":
            if defender.role_ability:
                attacker.polymath_ability = defender.role_ability
                attacker.polymath_cooldown = 2
                info_poly, _ = get_ability_info(defender.role_ability)
                pname = info_poly["name"] if info_poly else defender.role_ability
                msg += f"📚 **Polymath!** Studied **{pname}** — can now use it at **70% potency** via Pathway! *(2 turn cd)*"; color = 0x1ABC9C
            else:
                attacker.sp += actual_cost
                msg += f"📚 **Polymath** — opponent has no role ability to study!"; color = 0x95A5A6

        elif ability_key == "pacification":
            attacker.soul_assurer_cooldown = 6
            if ritual_choice == "strip_enemy":
                # Clear all positive effects from opponent
                stripped = []
                if defender.strength_boost > 0:       defender.strength_boost = 0;              stripped.append("strength boost")
                if defender.invigorated:               defender.invigorated = False;             stripped.append("invigorated")
                if defender.prayer_active:             defender.prayer_active = False;           stripped.append("prayer")
                if defender.night_boost_active:        defender.night_boost_active = False; defender.night_boost_damage = 0; stripped.append("night boost")
                if defender.phasing_active:            defender.phasing_active = False;          stripped.append("phasing")
                if defender.planting_turns > 0:        defender.planting_turns = 0;              stripped.append("planting")
                if defender.spectate_active:           defender.spectate_active = False;         stripped.append("spectate")
                if defender.arbitration_boost > 0:    defender.arbitration_boost = 0;           stripped.append("arbitration boost")
                if defender.combat_studies_active:     defender.combat_studies_active = False;   stripped.append("combat studies")
                if defender.folk_of_rage_active:       defender.folk_of_rage_active = False; defender.folk_of_rage_pct = 0; stripped.append("power up punch")
                if defender.animal_companion_active:   defender.animal_companion_active = False; stripped.append("animal companion")
                if defender.computing_active:          defender.computing_active = False;        stripped.append("computing")
                if defender.paper_figurine_turns > 0: defender.paper_figurine_turns = 0;        stripped.append("paper figurine")
                if defender.astrology_active:          defender.astrology_active = False;        stripped.append("astrology")
                if defender.holy_water_regen > 0:     defender.holy_water_regen = 0;            stripped.append("holy water")
                if defender.appraisal_active:          defender.appraisal_active = False;        stripped.append("appraisal")
                if defender.damage_boost_pct > 0:     defender.damage_boost_pct = 0.0;          stripped.append("damage boost")
                if defender.dawn_paladin_active:
                    defender.dawn_paladin_active = False
                    hp_remove = defender.dawn_paladin_hp_bonus
                    defender.max_hp = max(1, defender.max_hp - hp_remove)
                    defender.hp = min(defender.max_hp, defender.hp)
                    defender.dawn_paladin_hp_bonus = 0
                    stripped.append("dawn paladin")
                if defender.notary_buff_turns > 0:    defender.notary_buff_turns = 0;           stripped.append("notary buff")
                if stripped:
                    msg += f"☮️ **Pacification!** Stripped from **{defender.user.display_name}**: **{', '.join(stripped)}**! *(6 turn cd)*"; color = 0x4A148C
                else:
                    attacker.sp += actual_cost
                    attacker.soul_assurer_cooldown = 0
                    msg += f"☮️ **Pacification** — **{defender.user.display_name}** has no active buffs to strip!"; color = 0x95A5A6
            elif ritual_choice == "cleanse_self":
                # Clear all negative effects from self
                cleared = []
                if attacker.bleed > 0:             attacker.bleed = 0;                       cleared.append("bleed")
                if attacker.gun_shot_bleed:         attacker.gun_shot_bleed = False;          cleared.append("gunshot bleed")
                if attacker.paralysis > 0:          attacker.paralysis = 0;                   cleared.append("paralysis")
                if attacker.slumber > 0:            attacker.slumber = 0;                     cleared.append("sleep")
                if attacker.freeze > 0:             attacker.freeze = 0;                      cleared.append("freeze")
                if attacker.freeze_weakened:        attacker.freeze_weakened = False;         cleared.append("freeze weakened")
                if attacker.burn > 0:               attacker.burn = 0;                        cleared.append("burn")
                if attacker.defence_reduction > 0:  attacker.defence_reduction = 0;          cleared.append("defence reduction")
                if attacker.moral_freedom_active:   attacker.moral_freedom_active = False;    cleared.append("moral freedom")
                if attacker.debuff_stacks > 0:      attacker.debuff_stacks = 0;              cleared.append("debuff stacks")
                if attacker.gun_shot_bleed:         attacker.gun_shot_bleed = False;          cleared.append("gunshot bleed")
                if attacker.charm_turns > 0:        attacker.charm_turns = 0; attacker.charm_chance = 0.0; cleared.append("charm")
                if attacker.notary_debuff_turns > 0: attacker.notary_debuff_turns = 0;       cleared.append("notary debuff")
                if attacker.scroll_status:          attacker.scroll_status = None;            cleared.append("scroll affliction")
                if attacker.trapped > 0:            attacker.trapped = 0;                     cleared.append("trapped")
                if attacker.sneak_attack_pending:   attacker.sneak_attack_pending = False;    cleared.append("sneak attack")
                if cleared:
                    msg += f"☮️ **Pacification!** Cleansed from **{attacker.user.display_name}**: **{', '.join(cleared)}**! *(6 turn cd)*"; color = 0x4A148C
                else:
                    attacker.sp += actual_cost
                    attacker.soul_assurer_cooldown = 0
                    msg += f"☮️ **Pacification** — nothing to cleanse!"; color = 0x95A5A6

        elif ability_key == "spirit_guide":
            if attacker.spirit_guide_active:
                attacker.spirit_guide_active = False
                attacker.spirit_guide_turns = 0
                attacker.spirit_guide_cooldown = 7
                msg += f"👻 **Spirit Guide** dismissed — **7 turn cooldown** begins."; color = 0x8E44AD
            else:
                attacker.spirit_guide_active = True
                attacker.spirit_guide_turns = 0
                sg_sp_preview = max(5, int(attacker.max_sp * 0.06))
                msg += f"👻 **Spirit Guide** summoned — strikes each turn (~9% seq HP damage), costs **{sg_sp_preview} SP/turn**. 30% chance to possess you! Lasts **5 turns**. *(7 turn cd after)*"; color = 0x8E44AD

        elif ability_key == "hurricane_of_light":
            if attacker.hurricane_cooldown > 0:
                attacker.sp += actual_cost
                msg += f"🌪️ **Hurricane of Light** on cooldown for **{attacker.hurricane_cooldown}** more turn(s)!"; color = 0x95A5A6
                cd_blocked = True
            elif attacker.hurricane_interrupt_cd > 0:
                attacker.sp += actual_cost
                msg += f"🌪️ **Hurricane of Light** interrupted — sword must be re-planted in **{attacker.hurricane_interrupt_cd}** turn(s)!"; color = 0x95A5A6
                cd_blocked = True
            elif attacker.hurricane_uses >= 3:
                attacker.sp += actual_cost
                msg += f"🌪️ **Hurricane of Light** — all 3 uses exhausted!"; color = 0x95A5A6
                cd_blocked = True
            elif not attacker.hurricane_charging:
                attacker.hurricane_charging = True
                attacker.sp += actual_cost
                msg += f"🌪️ **Hurricane of Light** — sword driven into the ground, light gathering... *(releases automatically next turn)*"; color = 0xF1C40F
            else:
                attacker.hurricane_charging = False
                attacker.hurricane_uses += 1
                attacker.hurricane_cooldown = 12
                avg_base = (attacker.base_hp + defender.base_hp) // 2
                pct = random.uniform(0.35, 0.40)
                raw_dmg = int(avg_base * pct)
                # Evasion abilities (distortion, phasing) only mitigate 50% of hurricane damage
                if defender.distortion_active:
                    defender.distortion_active = False
                    raw_dmg = max(1, raw_dmg // 2)
                    msg += f"👑 **{defender.user.display_name}** distorts — hurricane damage halved!\n"
                elif defender.phasing_active:
                    defender.phasing_active = False
                    defender.phasing_cooldown = 1
                    raw_dmg = max(1, raw_dmg // 2)
                    msg += f"👻 **{defender.user.display_name}** phases — hurricane damage halved!\n"
                actual_dmg = defender.apply_damage(int(raw_dmg * attacker.get_attack_mult()), is_ability=True)
                backlash = int(actual_dmg * 0.30)
                attacker.hp = max(0, attacker.hp - backlash)
                msg += f"🌪️ **HURRICANE OF LIGHT!** A massive hurricane of light tears through! **{actual_dmg}** damage dealt — **{backlash}** backlash to you! *(use {attacker.hurricane_uses}/3, 12 turn cd)*"; color = 0xF5D020

        elif ability_key == "pleasure_witch":
            if defender.charm_turns > 0:
                attacker.sp += actual_cost
                msg += f"💋 **Pleasure Witch** — opponent already charmed!"; color = 0x95A5A6
                cd_blocked = True
            else:
                defender.charm_turns = 1
                defender.charm_chance = 0.40
                attacker.pleasure_witch_cooldown = 3
                msg += f"💋 **Pleasure Witch!** **{defender.user.display_name}** is charmed — skips next turn! *(40% linger chance, −10%/round, 3 turn cd)*"; color = 0xE91E8C

        elif ability_key == "conspiracy":
            allowed_gap, gap = seq_gap_check(attacker, defender, max_gap=2)
            if not allowed_gap:
                attacker.sp += actual_cost
                msg += f"🕸️ **Conspiracy failed!** The web cannot ensnare a Beyonder {gap} sequences above you."; color = 0x95A5A6
                cd_blocked = True
            if attacker.conspirer_cooldown > 0:
                attacker.sp += actual_cost
                msg += f"🕸️ **Conspiracy** on cooldown for **{attacker.conspirer_cooldown}** more turn(s)!"; color = 0x95A5A6
                cd_blocked = True
            else:
                attacker.conspirer_cooldown = 5
                parts = []

                # Guarantee at least one effect always lands by forcing the guaranteed one first
                CONSPIRACY_EFFECTS = ["miss", "force", "damage"]
                guaranteed = random.choice(CONSPIRACY_EFFECTS)
                remaining = [e for e in CONSPIRACY_EFFECTS if e != guaranteed]

                def apply_conspiracy_effect(effect):
                    if effect == "miss":
                        defender.conspiracy_miss_turns = 3
                        parts.append("🎯 **33% miss** applied for 3 rounds")
                    elif effect == "force":
                        defender.conspiracy_force_turns = 3
                        parts.append("🌀 **forced random ability** for 3 rounds")
                    elif effect == "damage":
                        actual_dmg = defender.apply_damage(40, is_ability=True)
                        sp_drained = min(60, defender.sp)
                        defender.sp = max(0, defender.sp - sp_drained)
                        parts.append(f"💥 **{actual_dmg} damage** + **{sp_drained} SP** drained")

                # Guaranteed hit
                apply_conspiracy_effect(guaranteed)
                # Other two each still roll at 33%
                for effect in remaining:
                    if random.random() < 0.33:
                        apply_conspiracy_effect(effect)

                msg += f"🕸️ **Conspiracy!** The web closes in:\n" + "\n".join(f"> {p}" for p in parts); color = 0x8E44AD

        elif ability_key == "scrolls_professor":
            statuses = ["freeze", "bleed", "burn", "paralysis", "slumber"]
            chosen = random.choice(statuses)
            attacker.scroll_status = chosen
            applied = defender.apply_status_effect(chosen, 1)
            attacker.scrolls_professor_cooldown = 4
            if applied:
                msg += f"📜 **Scrolls Professor!** Scroll afflicts **{defender.user.display_name}** with **{chosen}**! Rolls 20% chance each turn to re-apply. *(4 turn cd)*"; color = 0x8E44AD
            else:
                msg += f"📜 **Scrolls Professor!** Scroll chose **{chosen}** but was resisted. Still rolls 20%/turn. *(4 turn cd)*"; color = 0x95A5A6

        elif ability_key == "artisan":
            attacker.artisan_cooldown = 2
            roll = random.randint(1, 3)
            if roll == 1:
                # Bomb — 50 damage
                actual_dmg = apply_ability_damage(attacker, defender, 0.227, skill_key="artisan")
                msg += f"⚗️ **Artisan — Bomb!** Crafted from the materials around you — **{actual_dmg}** explosive damage! *(2 turn cd)*"; color = 0xE74C3C
            elif roll == 2:
                # Sword — 40 damage
                actual_dmg = apply_ability_damage(attacker, defender, 0.182, skill_key="artisan")
                msg += f"⚗️ **Artisan — Sword!** Forged a blade from the surroundings — **{actual_dmg}** damage! *(2 turn cd)*"; color = 0xF39C12
            else:
                # Rifle — 35 damage + bleed
                actual_dmg = apply_ability_damage(attacker, defender, 0.159, skill_key="artisan")
                applied = defender.apply_status_effect("bleed", 3)
                bleed_txt = " + bleed 3 turns" if applied else ""
                msg += f"⚗️ **Artisan — Rifle!** Assembled a firearm — **{actual_dmg}** damage{bleed_txt}! *(2 turn cd)*"; color = 0xE67E22

        elif ability_key == "calamity_priest":
            roll = random.random()
            if roll < 0.50:
                share_attacker, share_defender = 50, 50
            elif roll < 0.90:
                share_attacker, share_defender = 40, 60
            elif roll < 0.95:
                share_attacker, share_defender = 30, 70
            else:
                share_attacker, share_defender = 20, 80
            total_dmg = seq_dmg(attacker, 0.455)
            self_dmg = int(total_dmg * share_attacker / 100)
            opp_dmg = int(total_dmg * share_defender / 100)
            attacker.hp = max(0, attacker.hp - self_dmg)
            actual_opp = defender.apply_damage(int(opp_dmg * attacker.get_attack_mult()), is_ability=True)
            attacker.calamity_priest_cooldown = 3
            msg += f"☄️ **Calamity Priest!** Calamity shared — **{self_dmg}** to you, **{actual_opp}** to **{defender.user.display_name}**! *({share_attacker}/{share_defender} split)* *(3 turn cd)*"; color = 0xFFA000

        elif ability_key == "potions_professor":
            attacker.potions_professor_cooldown = 3
            if ritual_choice == "cure":
                if attacker.bleed > 0 or attacker.burn > 0 or attacker.paralysis > 0 or attacker.freeze > 0 or attacker.slumber > 0:
                    # Has status — lesser heal + remove one status
                    for attr in ["bleed", "burn", "paralysis", "freeze", "slumber"]:
                        if getattr(attacker, attr, 0) > 0:
                            setattr(attacker, attr, 0)
                            break
                    pot_hp_s = max(1, int(attacker.max_hp * 0.05))
                    pot_sp_s = max(1, int(attacker.max_sp * 0.04))
                    attacker.hp = min(attacker.max_hp, attacker.hp + pot_hp_s)
                    attacker.sp = min(attacker.max_sp, attacker.sp + pot_sp_s)
                    msg += f"🧪 **Cure!** Removed a status effect + restored **{pot_hp_s} HP + {pot_sp_s} SP**! *(3 turn cd)*"; color = 0x2ECC71
                else:
                    pot_hp = max(1, int(attacker.max_hp * 0.15))
                    pot_sp = max(1, int(attacker.max_sp * 0.10))
                    attacker.hp = min(attacker.max_hp, attacker.hp + pot_hp)
                    attacker.sp = min(attacker.max_sp, attacker.sp + pot_sp)
                    msg += f"🧪 **Cure!** Restored **{pot_hp} HP + {pot_sp} SP**! *(3 turn cd)*"; color = 0x2ECC71
            elif ritual_choice == "poison":
                actual_dmg = apply_ability_damage(attacker, defender, 0.227, is_ability=True)
                sp_drain = min(seq_val(attacker, 20), defender.sp)
                defender.sp = max(0, defender.sp - sp_drain)
                msg += f"☠️ **Poison!** **{actual_dmg}** damage + drained **{sp_drain} SP** from **{defender.user.display_name}**! *(3 turn cd)*"; color = 0xE74C3C

        elif ability_key == "biologist":
            attacker.biologist_cooldown = 3
            roll = random.randint(1, 2)
            if roll == 1:
                # Double status: paralysis + bleed
                a1 = defender.apply_status_effect("paralysis", 2)
                a2 = defender.apply_status_effect("bleed", 2)
                applied_names = []
                if a1: applied_names.append("paralysis")
                if a2: applied_names.append("bleed")
                msg += f"🧬 **Biologist!** Crossbred afflictions — **{', '.join(applied_names) if applied_names else 'resisted'}** on **{defender.user.display_name}**! *(3 turn cd)*"; color = 0x8E44AD
            else:
                actual_dmg = apply_ability_damage(attacker, defender, 0.163, is_ability=True)
                msg += f"🧬 **Biologist!** Summoned creature — **{actual_dmg}** damage! *(3 turn cd)*"; color = 0xE74C3C

        elif ability_key == "zombie":
            attacker.zombie_cooldown = 3
            roll = random.random()
            if roll < 0.60:
                # 1–4 zombies (60% chance), each deals 10 damage
                count = random.randint(1, 4)
                total_base = count * seq_dmg(attacker, 0.045)
                actual_dmg = defender.apply_damage(int(total_base * attacker.get_attack_mult()), is_ability=True)
                flinch_txt = ""
                if random.random() < 0.10:
                    applied = defender.apply_status_effect("paralysis", 1)
                    flinch_txt = " + flinch!" if applied else ""
                msg += f"🧟 **Zombie!** **{count}** zombie(s) summoned — **{actual_dmg}** damage{flinch_txt}! *(3 turn cd)*"; color = 0x4A148C
            else:
                # 5–7 zombies (40% chance), each deals 10 damage
                count = random.randint(5, 7)
                total_base = count * seq_dmg(attacker, 0.045)
                actual_dmg = defender.apply_damage(int(total_base * attacker.get_attack_mult()), is_ability=True)
                flinch_txt = ""
                if random.random() < 0.10:
                    applied = defender.apply_status_effect("paralysis", 1)
                    flinch_txt = " + flinch!" if applied else ""
                msg += f"🧟 **Zombie Horde!** **{count}** zombies — **{actual_dmg}** damage{flinch_txt}! *(3 turn cd)*"; color = 0x2C3E50

        elif ability_key == "devil_form":
            if attacker.devil_form_active:
                # Cancel
                attacker.devil_form_active = False
                hp_remove = attacker.devil_form_hp_bonus
                attacker.max_hp = max(1, attacker.max_hp - hp_remove)
                attacker.hp = min(attacker.max_hp, attacker.hp)
                attacker.devil_form_hp_bonus = 0
                sp_remove = attacker.devil_form_sp_bonus
                attacker.max_sp = max(1, attacker.max_sp - sp_remove)
                attacker.sp = min(attacker.max_sp, attacker.sp)
                attacker.devil_form_sp_bonus = 0
                attacker.damage_boost_pct = max(0.0, attacker.damage_boost_pct - 0.50)
                attacker.devil_form_cooldown = 4
                msg += f"😈 **Devil Form** released — **4 turn cooldown** begins."; color = 0xF39C12
            else:
                hp_bonus = int(attacker.max_hp * 0.50)
                attacker.devil_form_hp_bonus = hp_bonus
                attacker.max_hp += hp_bonus
                attacker.hp = min(attacker.max_hp, attacker.hp + hp_bonus)
                sp_bonus = int(attacker.max_sp * 0.50)
                attacker.devil_form_sp_bonus = sp_bonus
                attacker.max_sp += sp_bonus
                attacker.damage_boost_pct += 0.50
                attacker.devil_form_active = True
                msg += f"😈 **Devil Form!** Transformed — **+50% HP, SP, and damage**! 30% chance to lose turn to madness. Use again to cancel."; color = 0xE74C3C

        elif ability_key == "distortion":
            attacker.distortion_active = True
            attacker.distortion_cooldown = 3
            attacker.distortion_concealed = True  # conceal next action from opponent
            msg += f"👑 **Distortion!** **{attacker.user.display_name}** distorts reality — next incoming attack misses + next action concealed! *(3t cd)*"; color = 0x8E44AD

        elif ability_key == "judge":
            # Random verdict — no choice, fate decides
            verdict = random.choices(
                ["imprisonment", "flogging", "death"],
                weights=[33, 33, 34]
            )[0]
            attacker.judge_cooldown = 2

            if verdict == "imprisonment":
                # Imprisonment: trap opponent for 3 turns, costs them 25 SP
                defender.trapped = max(defender.trapped, 3)
                sp_drain = min(25, defender.sp)
                defender.sp = max(0, defender.sp - sp_drain)
                attacker.judge_cooldown = 7  # 4 + 3 turn cooldown
                msg += (f"⚖️ **Judge — Imprisonment!** The verdict is delivered — "
                        f"**{defender.user.display_name}** is imprisoned for **3 turns** "
                        f"and loses **{sp_drain} SP**! *(7 turn cd)*"); color = 0x1565C0

            elif verdict == "flogging":
                # Flogging: 15% of defender's current HP as damage
                dmg = max(1, int(defender.hp * 0.15))
                actual_dmg = defender.apply_damage(int(dmg * attacker.get_attack_mult()), is_ability=True)
                msg += (f"⚖️ **Judge — Flogging!** The whip cracks — **{actual_dmg}** damage "
                        f"(**15%** of **{defender.user.display_name}**'s current HP)! *(2 turn cd)*"); color = 0x1565C0

            elif verdict == "death":
                # Death: 40% flat dmg | 35% 50% HP | 25% miss
                roll = random.random()
                if roll < 0.40:
                    dmg = random.randint(seq_dmg(attacker, 0.273), seq_dmg(attacker, 0.409))
                    actual_dmg = defender.apply_damage(int(dmg * attacker.get_attack_mult()), is_ability=True)
                    msg += (f"⚖️ **Judge — Death Sentence!** ☠️ Sentence carried out — "
                            f"**{actual_dmg}** flat damage! *(2 turn cd)*"); color = 0xE74C3C
                elif roll < 0.75:
                    dmg = max(1, int(defender.hp * 0.50))
                    actual_dmg = defender.apply_damage(int(dmg * attacker.get_attack_mult()), is_ability=True)
                    msg += (f"⚖️ **Judge — Death Sentence!** ☠️ Half their life taken — "
                            f"**{actual_dmg}** damage (**50%** of current HP)! *(2 turn cd)*"); color = 0xE74C3C
                else:
                    attacker.sp += actual_cost
                    attacker.judge_cooldown = 0
                    msg += (f"⚖️ **Judge — Death Sentence!** The executioner hesitates — "
                            f"**{defender.user.display_name}** escapes unscathed! *(no cd)*"); color = 0x95A5A6

        # ── Seq 5 Role abilities ──────────────────────────

        elif ability_key == "spirit_thread":
            if attacker.spirit_thread_active:
                attacker.sp += actual_cost
                msg += f"🧵 **Spirit Thread** already active!"; cd_blocked = True
            elif attacker.spirit_thread_cooldown > 0:
                attacker.sp += actual_cost
                msg += f"🧵 **Spirit Thread** on cooldown for **{attacker.spirit_thread_cooldown}** turn(s)!"; cd_blocked = True
            else:
                attacker.spirit_thread_active = True
                attacker.spirit_thread_turns = 0
                attacker.spirit_thread_stun_pct = 0
                msg += (f"🧵 **Spirit Thread Control!** **{attacker.user.display_name}** seizes **{defender.user.display_name}**'s spirit threads! "
                        f"10% stun chance this turn, growing +10% each round. Broken by critical hit or special. *(5 turn cd)*"); color = 0x9B59B6

        elif ability_key == "marionette_summon":
            if attacker.marionette_active:
                attacker.sp += actual_cost
                msg += f"🎭 **Marionette** already active!"; cd_blocked = True
            elif attacker.marionette_cooldown > 0:
                attacker.sp += actual_cost
                msg += f"🎭 **Marionette** on cooldown for **{attacker.marionette_cooldown}** turn(s)!"; cd_blocked = True
            else:
                attacker.marionette_active = True
                attacker.marionette_turns = 3
                attacker.marionette_cooldown = 3
                msg += (f"🎭 **Sacrifice Marionette!** A marionette is summoned — each turn **50%** chance it attacks opponent or **50%** chance it intercepts an attack! "
                        f"Lasts **3 turns**. *(3 turn cd)*"); color = 0x8E44AD

        elif ability_key == "mental_theft":
            allowed_gap, gap = seq_gap_check(attacker, defender, max_gap=2)
            if not allowed_gap:
                attacker.sp += actual_cost
                msg += f"🧠 **Mental Theft failed!** Cannot pierce the mind of a Beyonder {gap} sequences stronger."; color = 0x95A5A6
                cd_blocked = True
            if attacker.mental_theft_cooldown > 0:
                attacker.sp += actual_cost
                msg += f"🧠 **Mental Theft** on cooldown for **{attacker.mental_theft_cooldown}** turn(s)!"; cd_blocked = True
            else:
                attacker.mental_theft_cooldown = 3
                defender.sneak_attack_pending = True
                msg += (f"🧠 **Mental Theft!** **{attacker.user.display_name}** removes the thought of attack from "
                        f"**{defender.user.display_name}**'s mind — they lose their next turn! *(3 turn cd)*"); color = 0x9B59B6

        elif ability_key == "rewards_theft":
            if attacker.rewards_theft_cooldown > 0:
                attacker.sp += actual_cost
                msg += f"🏆 **Rewards Theft** on cooldown for **{attacker.rewards_theft_cooldown}** turn(s)!"; cd_blocked = True
            else:
                # Try to steal a buff from defender
                stolen = None
                if defender.notary_buff_turns > 0:
                    attacker.notary_buff_turns = max(attacker.notary_buff_turns, defender.notary_buff_turns)
                    defender.notary_buff_turns = 0
                    stolen = "Notary Buff (+50% dmg)"
                elif defender.domination_active:
                    attacker.domination_active = True
                    attacker.domination_turns = defender.domination_turns
                    defender.domination_active = False
                    stolen = "Domination"
                elif defender.invigorated:
                    attacker.invigorated = True
                    defender.invigorated = False
                    stolen = "Invigorated"
                elif defender.appraisal_active:
                    attacker.appraisal_active = True
                    defender.appraisal_active = False
                    stolen = "Appraisal (+40%)"
                elif defender.damage_boost_pct > 0:
                    attacker.damage_boost_pct += defender.damage_boost_pct * 0.5
                    defender.damage_boost_pct = 0
                    stolen = "Damage Boost"
                elif defender.artificial_moon_turns > 0:
                    attacker.artificial_moon_turns = max(attacker.artificial_moon_turns, defender.artificial_moon_turns)
                    defender.artificial_moon_turns = 0
                    stolen = "Artificial Moon"
                elif defender.singing_enlightened_turns > 0:
                    attacker.singing_enlightened_turns = defender.singing_enlightened_turns
                    attacker.singing_str_boost = defender.singing_str_boost
                    defender.singing_enlightened_turns = 0
                    defender.singing_str_boost = 0
                    stolen = "Vocal Enlightenment"
                if stolen:
                    attacker.rewards_theft_cooldown = 4
                    msg += (f"🏆 **Rewards Theft!** **{attacker.user.display_name}** steals "
                            f"**{stolen}** from **{defender.user.display_name}**! *(4 turn cd)*"); color = 0xF39C12
                else:
                    attacker.sp += actual_cost
                    msg += f"🏆 **Rewards Theft** — nothing to steal!"; cd_blocked = True

        elif ability_key == "blink":
            if attacker.blink_cooldown > 0:
                attacker.sp += actual_cost
                msg += f"⚡ **Blink** on cooldown for **{attacker.blink_cooldown}** turn(s)!"; cd_blocked = True
            else:
                attacker.blink_cooldown = 6
                attacker.blink_stun_immune_turns = 3
                # Clear ALL stun types
                escaped = []
                if attacker.paralysis > 0:          attacker.paralysis = 0;          escaped.append("paralysis")
                if attacker.slumber > 0:            attacker.slumber = 0;            escaped.append("sleep")
                if attacker.freeze > 0:             attacker.freeze = 0;             escaped.append("freeze")
                if attacker.trapped > 0:            attacker.trapped = 0;            escaped.append("trap")
                if attacker.sneak_attack_pending:   attacker.sneak_attack_pending = False; escaped.append("sneak attack")
                if attacker.charm_turns > 0:        attacker.charm_turns = 0;        escaped.append("charm")
                escape_txt = f" Escaped: **{', '.join(escaped)}**." if escaped else ""
                surprise_dmg = apply_ability_damage(attacker, defender, 0.190, is_ability=True)
                # Override the "fights through X to use cleanse" message — blink has its own
                msg = msg.replace(
                    f"💥 **{attacker.user.display_name}** fights through the {msg.split('fights through the ')[-1].split(' to use')[0] if 'fights through' in msg else ''} to use their cleanse!\n",
                    ""
                )
                msg += (f"⚡ **Blink!**{escape_txt} **{attacker.user.display_name}** is stun-immune for **3 turns** and "
                        f"surprise-strikes **{defender.user.display_name}** for **{surprise_dmg}** damage! *(6 turn cd)*"); color = 0x3498DB

        elif ability_key == "travellers_door":
            if attacker.travellers_door_cooldown > 0:
                attacker.sp += actual_cost
                msg += f"🚪 **Traveller's Door** on cooldown for **{attacker.travellers_door_cooldown}** turn(s)!"; cd_blocked = True
            elif attacker.travellers_door_uses <= 0:
                attacker.sp += actual_cost
                msg += f"🚪 **Traveller's Door** — no uses remaining!"; cd_blocked = True
            else:
                attacker.travellers_door_uses -= 1
                attacker.travellers_door_cooldown = 6
                # Full resource regen
                attacker.sp = attacker.max_sp
                attacker.hp = min(attacker.max_hp, attacker.hp + int(attacker.max_hp * 0.30))
                # Clear all debuffs on self
                attacker.bleed = 0; attacker.burn = 0; attacker.paralysis = 0
                attacker.slumber = 0; attacker.freeze = 0
                env_dmg = 0
                if random.random() < 0.10:
                    env_dmg = apply_ability_damage(attacker, defender, 0.048)
                env_txt = f" + **{env_dmg}** environmental damage!" if env_dmg else ""
                msg += (f"🚪 **Traveller's Door!** **{defender.user.display_name}** is flung to a remote location! "
                        f"**{attacker.user.display_name}** fully recuperates (SP restored, +30% HP, debuffs cleared){env_txt}. "
                        f"*({attacker.travellers_door_uses} use(s) left, 6 turn cd)*"); color = 0x2ECC71

        elif ability_key == "dream_visitation":
            allowed_gap, gap = seq_gap_check(attacker, defender, max_gap=3)
            if not allowed_gap:
                attacker.sp += actual_cost
                msg += f"💭 **Dream Visitation failed!** **{defender.user.display_name}**'s mind is too strong to enter. (gap: {gap})"; color = 0x95A5A6
                cd_blocked = True
            if attacker.dream_visitation_cooldown > 0:
                attacker.sp += actual_cost
                msg += f"🌙 **Dream Visitation** on cooldown for **{attacker.dream_visitation_cooldown}** turn(s)!"; cd_blocked = True
            else:
                attacker.dream_visitation_cooldown = 4
                if random.random() < 0.50:
                    # Force flee or SP drain — SP drain for bot fairness
                    sp_drained = min(int(defender.max_sp * 0.40), defender.sp)
                    defender.sp = max(0, defender.sp - sp_drained)
                    msg += (f"🌙 **Dream Visitation!** **{attacker.user.display_name}** enters the dreams of "
                            f"**{defender.user.display_name}** — their secrets are exposed! Drained **{sp_drained} SP** "
                            f"(**40%** of max SP)! *(4 turn cd)*"); color = 0x9B59B6
                else:
                    msg += (f"🌙 **Dream Visitation** — **{defender.user.display_name}**'s mind resists the intrusion! *(4 turn cd)*"); color = 0x95A5A6

        elif ability_key == "dream_alteration":
            if attacker.dream_alteration_used:
                attacker.sp += actual_cost
                msg += f"💭 **Dream Alteration** already used!"; cd_blocked = True
            else:
                attacker.dream_alteration_used = True
                defender.dream_alteration_acc_loss = 0.05  # Start with 5%
                msg += (f"💭 **Dream Alteration!** **{attacker.user.display_name}** corrupts **{defender.user.display_name}**'s thoughts — "
                        f"accuracy drops **5% every round** for the rest of battle! *(one use)*"); color = 0x9B59B6

        elif ability_key == "purification_halo":
            if attacker.purification_halo_cooldown > 0:
                attacker.sp += actual_cost
                msg += f"☀️ **Purification Halo** on cooldown for **{attacker.purification_halo_cooldown}** turn(s)!"; cd_blocked = True
            else:
                attacker.purification_halo_cooldown = 5
                attacker.purification_halo_sp_boost_turns = 3
                sp_boost = max(5, int(attacker.max_sp * 0.15))
                attacker.sp = min(attacker.max_sp, attacker.sp + sp_boost)
                def_pathway = get_pathway_name(defender.role) if defender.role else ""
                halo_dmg = 0
                burn_turns = 0
                if def_pathway == "Death":
                    halo_dmg = 50; burn_turns = 5
                elif def_pathway == "Abyss":
                    halo_dmg = 100; burn_turns = 5
                elif def_pathway == "Chained":
                    halo_dmg = 150; burn_turns = 5
                if halo_dmg > 0:
                    actual_dmg = apply_ability_damage(attacker, defender, halo_dmg / SEQ_AVG_HP.get(get_seq_number(attacker.role) if attacker.role else 9, 420))
                    defender.apply_status_effect("burn", 5)
                    msg += (f"☀️ **Purification Halo!** A holy sun halo erupts — **{defender.user.display_name}** ({def_pathway} pathway) "
                            f"suffers **{actual_dmg}** holy damage + **5-round burn**! **{attacker.user.display_name}** gains **+{sp_boost} SP**. *(5 turn cd)*"); color = 0xFFD700
                else:
                    msg += (f"☀️ **Purification Halo!** A radiant sun halo bathes the area in holy light — "
                            f"**{attacker.user.display_name}** gains **+{sp_boost} SP** and an SP surge for **3 turns**! *(5 turn cd)*"); color = 0xFFD700

        elif ability_key == "light_of_holiness":
            if attacker.light_of_holiness_cooldown > 0:
                attacker.sp += actual_cost
                msg += f"✨ **Light of Holiness** on cooldown for **{attacker.light_of_holiness_cooldown}** turn(s)!"; cd_blocked = True
            else:
                attacker.light_of_holiness_cooldown = 4
                def_pathway = get_pathway_name(defender.role) if defender.role else ""
                base_dmg = 70
                if def_pathway == "Chained":
                    base_dmg = 140
                actual_dmg = apply_ability_damage(attacker, defender, base_dmg / SEQ_AVG_HP.get(get_seq_number(attacker.role) if attacker.role else 9, 420))
                defender.apply_status_effect("burn", 2)
                defender.sneak_attack_pending = True  # force turn skip
                # Revoke conditional curses
                if def_pathway in ("Abyss", "Death"):
                    defender.scroll_status = None
                    defender.conspiracy_miss_turns = 0
                chain_txt = " (**doubled** — Chained pathway!)" if def_pathway == "Chained" else ""
                msg += (f"✨ **Light of Holiness!** A colossal ray of holy light descends — "
                        f"**{actual_dmg}** damage{chain_txt} + **2-round burn** + **{defender.user.display_name}** loses next turn! *(4 turn cd)*"); color = 0xFFD700

        elif ability_key == "singing":
            if attacker.singing_cooldown > 0:
                attacker.sp += actual_cost
                msg += f"🎵 **Singing** on cooldown for **{attacker.singing_cooldown}** turn(s)!"; cd_blocked = True
            else:
                attacker.singing_cooldown = 3
                roll = random.randint(1, 3)
                if roll == 1:
                    # Spirit Body Interference
                    applied = defender.apply_status_effect("paralysis", 2)
                    sp_strip = min(seq_val(attacker, 30), defender.sp)
                    defender.sp = max(0, defender.sp - sp_strip)
                    stun_txt = " + stunned 2 turns" if applied else " (stun resisted)"
                    msg += (f"🎵 **Singing — Spirit Interference!** A terrible singing voice disrupts **{defender.user.display_name}**'s spirit body — "
                            f"**{sp_strip} SP** drained{stun_txt}! *(3 turn cd)*"); color = 0x9B59B6
                elif roll == 2:
                    # Vocal Enlightenment
                    attacker.singing_enlightened_turns = 3
                    attacker.singing_str_boost = 0.20
                    attacker.damage_boost_pct += 0.20
                    msg += (f"🎵 **Singing — Vocal Enlightenment!** A magnificent performance empowers **{attacker.user.display_name}** — "
                            f"**+15% spirit** and **+20% strength** for **3 turns**! *(3 turn cd)*"); color = 0xF39C12
                else:
                    # Sound-Wave Explosion
                    actual_dmg = apply_ability_damage(attacker, defender, 0.095)
                    defender.apply_status_effect("paralysis", 1)
                    msg += (f"🎵 **Singing — Sound-Wave Explosion!** A devastating shockwave blasts **{defender.user.display_name}** for "
                            f"**{actual_dmg}** damage + stun 1 turn! *(3 turn cd)*"); color = 0xE74C3C

        elif ability_key == "arrow_of_lightning":
            if attacker.arrow_charging:
                # Fire the arrow
                attacker.arrow_charging = False
                attacker.arrow_cooldown = 5
                actual_dmg = apply_ability_damage(attacker, defender, 0.214, is_ability=True)  # ~90 at seq5
                msg += (f"⚡ **Arrow of Lightning FIRES!** A bolt faster than sight strikes **{defender.user.display_name}** for "
                        f"**{actual_dmg}** damage! *(5 turn cd)*"); color = 0xFFD700
            elif attacker.arrow_cooldown > 0:
                attacker.sp += actual_cost
                msg += f"⚡ **Arrow of Lightning** on cooldown for **{attacker.arrow_cooldown}** turn(s)!"; cd_blocked = True
            else:
                attacker.arrow_charging = True
                msg += (f"⚡ **Arrow of Lightning** charging — **{attacker.user.display_name}** draws back the lightning! "
                        f"Will fire automatically next turn for **90 damage**!"); color = 0xF39C12

        elif ability_key == "ability_bag":
            if not attacker.grazing_abilities:
                attacker.sp += actual_cost
                msg += f"🎒 **Ability Bag** — nothing grazed yet! Use **Grazing** first to copy abilities."; cd_blocked = True
            else:
                attacker.sp += actual_cost   # refund — opening the bag doesn't cost SP
                # Show the bag contents as an ephemeral select menu
                bag_entries = []
                for gk in attacker.grazing_abilities:
                    ab_info, _ = get_ability_info(gk)
                    if ab_info:
                        cost = ab_info.get("cost", 0)
                        display_cost = ab_info.get("display_cost", f"{cost} SP")
                        bag_entries.append((gk, ab_info["name"], display_cost))
                if not bag_entries:
                    msg += f"🎒 **Ability Bag** — no valid abilities found."; cd_blocked = True
                else:
                    view = AbilityBagView(attacker, bag_entries, battle, interaction.channel)
                    names_txt = ", ".join(f"**{n}** ({c})" for _, n, c in bag_entries)
                    await interaction.followup.send(
                        f"🎒 **Ability Bag** — choose an ability to activate:\n{names_txt}",
                        view=view,
                        ephemeral=True
                    )
                    return
            if attacker.grazing_cooldown > 0:
                attacker.sp += actual_cost
                msg += f"🌾 **Grazing** on cooldown for **{attacker.grazing_cooldown}** turn(s)!"; cd_blocked = True
            elif len(attacker.grazing_abilities) >= 5:
                attacker.sp += actual_cost
                msg += f"🌾 **Grazing** — ability bag full (5 max)!"; cd_blocked = True
            else:
                # Copy 3 random abilities from defender's pathway
                attacker.grazing_cooldown = 1
                def_pathway = get_pathway_name(defender.role) if defender.role else ""
                def_seq = get_seq_number(defender.role) if defender.role else 9
                pool = []
                for seq in range(9, def_seq - 1, -1):
                    role_key = next((r for r in ROLE_ABILITIES if f"[{def_pathway}] Seq {seq}" in r), None)
                    if role_key:
                        pool.append(ROLE_ABILITIES[role_key])
                picked = random.sample(pool, min(3, len(pool))) if pool else []
                for ak in picked:
                    if ak not in attacker.grazing_abilities and len(attacker.grazing_abilities) < 5:
                        attacker.grazing_abilities.append(ak)
                if picked:
                    names = [get_ability_info(k)[0]["name"] if get_ability_info(k)[0] else k for k in picked]
                    msg += (f"🌾 **Grazing!** Copied **{len(picked)}** abilities from **{defender.user.display_name}**: "
                            f"**{', '.join(names)}**! *(1 use per round)*"); color = 0x2ECC71
                else:
                    attacker.sp += actual_cost
                    msg += f"🌾 **Grazing** — nothing to copy!"; cd_blocked = True

        elif ability_key == "combination_spell":
            # Activate the combo system — from now on using certain pairs triggers combos
            if attacker.combination_spell_active:
                attacker.sp += actual_cost
                msg += f"🔮 **Combination Spell** already active!"; cd_blocked = True
            else:
                attacker.combination_spell_active = True
                msg += (f"🔮 **Combination Spell!** **{attacker.user.display_name}** unlocks ability fusion — "
                        f"use **Leodero+Debuff**, **Analyse+Ritualistic Reasoning**, "
                        f"**Study+Polymath**, or **Debuff+Analyse** in sequence to unleash combo spells!"); color = 0x9B59B6

        elif ability_key == "spiritual_suppression":
            if attacker.spiritual_suppression_uses <= 0:
                attacker.sp += actual_cost
                msg += f"🦷 **Spiritual Suppression** — no uses remaining!"; cd_blocked = True
            elif attacker.spiritual_suppression_cooldown > 0:
                attacker.sp += actual_cost
                msg += f"🦷 **Spiritual Suppression** on cooldown for **{attacker.spiritual_suppression_cooldown}** turn(s)!"; cd_blocked = True
            else:
                attacker.spiritual_suppression_uses -= 1
                attacker.spiritual_suppression_turns = 2
                attacker.spiritual_suppression_cooldown = 4
                msg += (f"🦷 **Spiritual Suppression!** Spirits locked within teeth surge forth — all of "
                        f"**{defender.user.display_name}**'s abilities costing **>50 SP** are negated for **2 turns**! "
                        f"*({attacker.spiritual_suppression_uses} use(s) left, 4 turn cd)*"); color = 0x4B0082

        elif ability_key == "spiritual_takeover":
            allowed_gap, gap = seq_gap_check(attacker, defender, max_gap=2)
            if not allowed_gap:
                attacker.sp += actual_cost
                msg += f"👁️ **Spiritual Takeover failed!** Cannot seize the spirit of someone {gap} sequences stronger."; color = 0x95A5A6
                cd_blocked = True
            if attacker.spiritual_takeover_used:
                attacker.sp += actual_cost
                msg += f"👻 **Spiritual Takeover** already used!"; cd_blocked = True
            else:
                # Check Sun pathway immunity
                def_pathway = get_pathway_name(defender.role) if defender.role else ""
                def_seq = get_seq_number(defender.role) if defender.role else 9
                if def_pathway == "Sun" and def_seq <= 5:
                    attacker.sp += actual_cost
                    msg += f"👻 **Spiritual Takeover** — Sun pathway Seq 5+ is completely resistant!"; cd_blocked = True
                else:
                    attacker.spiritual_takeover_used = True
                    defender.spiritual_takeover_active = True
                    msg += (f"👻 **Spiritual Takeover!** A corrupted spirit is planted near **{defender.user.display_name}** — "
                            f"they will suffer **15 damage per round** until battle ends! *(1 use only)*"); color = 0x8E44AD

        elif ability_key == "dragged_to_hell":
            if attacker.dragged_to_hell_used:
                attacker.sp += actual_cost
                msg += f"🔥 **Dragged to Hell** already used!"; cd_blocked = True
            else:
                attacker.dragged_to_hell_used = True
                attacker.dragged_to_hell_active = True
                actual_dmg = apply_ability_damage(attacker, defender, 0.167, is_ability=True)  # ~70 at seq5
                msg += (f"🔥 **Dragged to Hell!** The gateway within **{attacker.user.display_name}**'s glabella tears open — "
                        f"**{actual_dmg}** damage to **{defender.user.display_name}**! **{attacker.user.display_name}** drains "
                        f"**15 SP** each round for the rest of the match. *(1 use only)*"); color = 0xE74C3C

        elif ability_key == "evil_sealing":
            if attacker.evil_sealing_active:
                # Fire the stored shot
                total_dmg = attacker.evil_sealing_damage
                attacker.evil_sealing_active = False
                attacker.evil_sealing_damage = 0
                attacker.evil_sealing_debuff = None
                # Ability deactivates for rest of match — mark as used
                attacker.dragged_to_hell_used = True  # reuse flag to mark fired
                actual_dmg = apply_ability_damage(attacker, defender, total_dmg / SEQ_AVG_HP.get(get_seq_number(attacker.role) if attacker.role else 9, 420))
                msg += (f"💀 **Evil Sealing FIRES!** The compressed wraith-bullet erupts for **{actual_dmg}** damage! "
                        f"*(ability deactivated for rest of match)*"); color = 0x8E44AD
            else:
                # Start charging
                debuff_choices = ["burn", "bleed", "paralysis"]
                attacker.evil_sealing_debuff = random.choice(debuff_choices)
                attacker.apply_status_effect(attacker.evil_sealing_debuff, 1)
                attacker.evil_sealing_active = True
                attacker.evil_sealing_damage = 30
                msg += (f"💀 **Evil Sealing** — **{attacker.user.display_name}** begins compressing a wraith! "
                        f"Afflicted with **{attacker.evil_sealing_debuff}** as payment. Charges **+30 dmg/round** with 10 recoil. "
                        f"Use again to fire!"); color = 0x2C3E50

        elif ability_key == "protection":
            if attacker.protection_cooldown > 0:
                attacker.sp += actual_cost
                msg += f"🛡️ **Protection** on cooldown for **{attacker.protection_cooldown}** turn(s)!"; cd_blocked = True
            elif attacker.protection_active:
                # Deactivate (maintenance mode only)
                if attacker.protection_mode == "maintenance":
                    attacker.protection_active = False
                    attacker.protection_cooldown = 3
                    attacker.protection_mode = None
                    msg += f"🛡️ **Protection** cancelled — **3 turn cooldown**!"; color = 0x3498DB
                else:
                    attacker.sp += actual_cost
                    msg += f"🛡️ **Protection** is active in **Active** mode — lasts until your next attack!"; cd_blocked = True
            else:
                chosen_mode = ritual_choice if ritual_choice in ("active", "maintenance") else "maintenance"
                attacker.protection_active = True
                attacker.protection_weakened_turn = False
                attacker.protection_mode = chosen_mode
                reduction = seq_val(attacker, 80)
                if chosen_mode == "active":
                    mode_desc = "**Active** mode — protection lasts until your next attack, then automatically ends."
                else:
                    mode_desc = "**Maintenance** mode — toggle off anytime (3 turn cd on cancel)."
                msg += (f"🛡️ **Protection!** **{attacker.user.display_name}** abandons offense — damage dealt **-50%**, "
                        f"damage taken reduced by **{reduction}** (drops to **{seq_val(attacker, 10)}** for 1 turn after any attack). "
                        f"{mode_desc} *(40 SP)*"); color = 0x3498DB

        elif ability_key == "dawn_armor":
            if attacker.dawn_armor_active:
                attacker.sp += actual_cost
                msg += f"🌅 **Dawn Armor** is already active and cannot be cancelled!"; cd_blocked = True
            elif attacker.dawn_armor_cooldown > 0:
                attacker.sp += actual_cost
                msg += f"🌅 **Dawn Armor** on cooldown for **{attacker.dawn_armor_cooldown}** turn(s)!"; cd_blocked = True
            else:
                attacker.dawn_armor_active = True
                hp_bonus = int(attacker.max_hp * 0.20)
                attacker.dawn_armor_hp_bonus = hp_bonus
                attacker.max_hp += hp_bonus
                attacker.hp = min(attacker.max_hp, attacker.hp + hp_bonus)
                attacker.damage_boost_pct += 0.10
                msg += (f"🌅 **Dawn Armor!** Radiant armor envelops **{attacker.user.display_name}** — "
                        f"**+{hp_bonus} max HP** (+20%) and **+10% strength**! Cannot be cancelled. *(30 SP)*"); color = 0xFFD700

        elif ability_key == "disease_propagation":
            if attacker.disease_propagation_used:
                attacker.sp += actual_cost
                msg += f"🦠 **Disease Propagation** already active!"; cd_blocked = True
            else:
                attacker.disease_propagation_used = True
                attacker.disease_propagation_active = True
                attacker.disease_propagation_turns = 0
                msg += (f"🦠 **Disease Propagation!** **{attacker.user.display_name}** channels the power of plague — "
                        f"**{defender.user.display_name}** is infected! 15 HP + growing damage every round until death. "
                        f"*(1 use only)*"); color = 0x8E44AD

        elif ability_key == "thread_storm":
            # Mirror Curse (Demoness Seq 5 ability 2 — renamed from thread_storm)
            mc_used = getattr(attacker, "mirror_curse_used", 0)
            mc_cd   = getattr(attacker, "mirror_curse_cd", 0)
            if mc_used >= 2:
                attacker.sp += actual_cost; msg += f"🪞 **Mirror Curse** — both uses spent!"; cd_blocked = True
            elif mc_cd > 0:
                attacker.sp += actual_cost; msg += f"🪞 **Mirror Curse** on cooldown ({mc_cd}t)!"; cd_blocked = True
            elif not getattr(attacker, "mirror_curse_charging", False):
                # Start charging — 2 turn charge, no actions during
                attacker.mirror_curse_charging = True; attacker.mirror_curse_charge_turns = 2
                attacker.sp += actual_cost  # refund — charging turn
                msg += f"🪞 **Mirror Curse** — charging for **2 turns**... Cannot act during this time!"; color = 0x8E44AD; cd_blocked = True
            else:
                # Should be handled via charge completion in apply_turn_effects
                attacker.sp += actual_cost; msg += f"🪞 **Mirror Curse** — still charging!"; cd_blocked = True

        elif ability_key == "cull":
            if attacker.cull_uses <= 0:
                attacker.sp += actual_cost
                msg += f"☠️ **Cull** — no uses remaining!"; cd_blocked = True
            elif attacker.cull_cooldown > 0:
                attacker.sp += actual_cost
                msg += f"☠️ **Cull** on cooldown for **{attacker.cull_cooldown}** turn(s)!"; cd_blocked = True
            elif attacker.cull_bonus > 0:
                attacker.sp += actual_cost
                msg += f"☠️ **Cull** already charged!"; cd_blocked = True
            else:
                attacker.cull_uses -= 1
                attacker.cull_cooldown = 4
                attacker.cull_bonus = seq_val(attacker, 100)
                msg += (f"☠️ **Cull!** **{attacker.user.display_name}** marks the ultimate weak point — "
                        f"next attack gains **+{seq_val(attacker, 100)} bonus damage**! "
                        f"*({attacker.cull_uses} use(s) left, 4 turn cd)*"); color = 0xE74C3C

        elif ability_key == "weakness_development":
            if attacker.weakness_development_uses <= 0:
                attacker.sp += actual_cost
                msg += f"🎯 **Weakness Development** — no uses remaining!"; cd_blocked = True
            elif attacker.weakness_development_cooldown > 0:
                attacker.sp += actual_cost
                msg += f"🎯 **Weakness Development** on cooldown for **{attacker.weakness_development_cooldown}** turn(s)!"; cd_blocked = True
            else:
                attacker.weakness_development_uses -= 1
                attacker.weakness_development_cooldown = 3
                # Randomly choose: halve a skill or apply debuff
                if random.random() < 0.50:
                    # Place 20% damage vulnerability (more damage taken) for 3 turns
                    defender.damage_vulnerability = 3
                    msg += (f"🎯 **Weakness Development!** A weakpoint is exposed — "
                            f"**{defender.user.display_name}** takes **20% more damage**! "
                            f"*({attacker.weakness_development_uses} use(s) left, 3 turn cd)*"); color = 0xF39C12
                else:
                    # Halve defender's role ability by adding it to analysed
                    def_ak = defender.role_ability
                    if def_ak:
                        defender.analysed_skills[def_ak] = max(defender.analysed_skills.get(def_ak, 0), 0.50)
                        info_d, _ = get_ability_info(def_ak)
                        dname = info_d["name"] if info_d else def_ak
                        msg += (f"🎯 **Weakness Development!** **{dname}** from **{defender.user.display_name}** "
                                f"is permanently halved in power! *({attacker.weakness_development_uses} use(s) left, 3 turn cd)*"); color = 0xF39C12
                    else:
                        defender.damage_vulnerability = 3
                        msg += (f"🎯 **Weakness Development!** **{defender.user.display_name}** takes **20% more damage**! "
                                f"*({attacker.weakness_development_uses} use(s) left, 3 turn cd)*"); color = 0xF39C12

        elif ability_key == "stellar_self":
            if attacker.stellar_self_turns > 0:
                attacker.sp += actual_cost
                msg += f"⭐ **Stellar Self** already active!"; cd_blocked = True
            else:
                attacker.stellar_self_turns = 2
                msg += (f"⭐ **Stellar Self!** **{attacker.user.display_name}** fuses with a substitute star-clone — "
                        f"completely unhittable by Beyonders of the same sequence or lower for **2 turns**!"); color = 0xF1C40F

        elif ability_key == "star_pillar":
            if attacker.star_pillar_cooldown > 0:
                attacker.sp += actual_cost
                msg += f"💫 **Star Pillar** on cooldown for **{attacker.star_pillar_cooldown}** turn(s)!"; cd_blocked = True
            else:
                attacker.star_pillar_cooldown = 3
                actual_dmg = apply_ability_damage(attacker, defender, 0.107, is_ability=True)
                msg += (f"💫 **Star Pillar!** Layers of starlight coalesce into a disintegrating column — "
                        f"**{actual_dmg}** damage! *(3 turn cd)*"); color = 0xF1C40F

        elif ability_key == "fire_storm":
            if attacker.fire_storm_cooldown > 0:
                attacker.sp += actual_cost
                msg += f"☄️ **Fire Storm** on cooldown for **{attacker.fire_storm_cooldown}** turn(s)!"; cd_blocked = True
            else:
                attacker.fire_storm_cooldown = 5
                roll = random.random()
                if roll < 0.40:
                    actual_dmg = apply_ability_damage(attacker, defender, 0.119, is_ability=True)  # ~50 at seq5
                    msg += (f"☄️ **Fire Storm!** A burning meteor is redirected — **{actual_dmg}** damage! *(5 turn cd)*"); color = 0xE74C3C
                else:
                    actual_dmg = apply_ability_damage(attacker, defender, 0.071, is_ability=True)  # ~30 at seq5
                    msg += (f"☄️ **Fire Storm!** Partial meteor impact — **{actual_dmg}** damage. *(5 turn cd)*"); color = 0xF39C12

        elif ability_key == "star_of_curses":
            if attacker.star_of_curses_used:
                attacker.sp += actual_cost
                msg += f"🌟 **Star of Curses** already active!"; cd_blocked = True
            else:
                attacker.star_of_curses_used = True
                attacker.star_of_curses_turns = 5
                initial_dmg = apply_ability_damage(attacker, defender, 0.012, is_ability=True)  # ~5 dmg
                msg += (f"🌟 **Star of Curses!** An insidious constellation appears — **{initial_dmg}** initial damage. "
                        f"**{defender.user.display_name}** will be cursed with a random debuff **every round for 5 turns**! "
                        f"*(1 use only)*"); color = 0xF1C40F

        elif ability_key == "curse_of_misfortune":
            if attacker.curse_of_misfortune_cooldown > 0:
                attacker.sp += actual_cost
                msg += f"🎲 **Curse of Misfortune** on cooldown for **{attacker.curse_of_misfortune_cooldown}** turn(s)!"; cd_blocked = True
            else:
                attacker.curse_of_misfortune_cooldown = 5
                defender.curse_of_misfortune_turns = 5
                msg += (f"🎲 **Curse of Misfortune!** Misfortune is transferred to **{defender.user.display_name}** via a standard hit — "
                        f"for **5 rounds**: accuracy **10-40%** and **20 recoil** on each of their attacks! *(5 turn cd)*"); color = 0xDAA520

        elif ability_key == "active_luck_boost":
            if attacker.active_luck_boost_used:
                attacker.sp += actual_cost
                msg += f"🍀 **Active Luck Boost** already used!"; cd_blocked = True
            else:
                attacker.active_luck_boost_used = True
                attacker.active_luck_boost_turns = 2
                msg += (f"🍀 **Active Luck Boost!** **{attacker.user.display_name}** focuses all gathered fortune — "
                        f"for **2 turns**: 90% dodge chance, best probability outcomes, +1 buff duration, "
                        f"and 10% chance for 40 random damage per turn! *(1 use only)*"); color = 0x2ECC71

        elif ability_key == "artificial_moon":
            if attacker.artificial_moon_used:
                attacker.sp += actual_cost
                msg += f"🌕 **Artificial Moon** already used!"; cd_blocked = True
            else:
                attacker.artificial_moon_used = True
                attacker.artificial_moon_turns = 5
                sp_bonus = int(attacker.max_sp * 0.50)
                attacker.sp = min(attacker.max_sp, attacker.sp + sp_bonus)
                attacker.damage_boost_pct += 0.40
                msg += (f"🌕 **Artificial Moon!** The red moon's power channels through **{attacker.user.display_name}** — "
                        f"**+{sp_bonus} SP** and all beyonder ability damage boosted **+40%** for **5 rounds**! *(1 use only)*"); color = 0xC0C0C0

        elif ability_key == "scarlet_transformation":
            if attacker.scarlet_transformation_active:
                attacker.sp += actual_cost
                msg += f"🌙 **Scarlet Transformation** already active!"; cd_blocked = True
            elif attacker.scarlet_transformation_cooldown > 0:
                attacker.sp += actual_cost
                msg += f"🌙 **Scarlet Transformation** on cooldown for **{attacker.scarlet_transformation_cooldown}** turn(s)!"; cd_blocked = True
            else:
                attacker.scarlet_transformation_active = True
                attacker.scarlet_transformation_cooldown = 7  # deactivates at 3 (7-4=3) via tick_cooldowns
                msg += (f"🌙 **Scarlet Transformation!** **{attacker.user.display_name}** transforms into living moonlight — "
                        f"completely immune to all physical and special attacks for **4 turns**! *(7 turn cd)*"); color = 0xC0C0C0

        elif ability_key == "spirit_animal_transformation":
            if attacker.spirit_animal_active:
                # Deactivate
                attacker.spirit_animal_active = False
                attacker.max_hp = max(1, attacker.max_hp - attacker.spirit_animal_hp_bonus)
                attacker.hp = min(attacker.max_hp, attacker.hp)
                attacker.spirit_animal_hp_bonus = 0
                attacker.spirit_animal_cooldown = 10
                attacker.damage_boost_pct = max(0.0, attacker.damage_boost_pct - 0.50)
                msg += (f"🐻 **Spirit Animal** deactivated — **{attacker.user.display_name}** reverts! "
                        f"*(10 turn cd)*"); color = 0x2ECC71
            elif attacker.spirit_animal_cooldown > 0:
                attacker.sp += actual_cost
                msg += f"🐻 **Spirit Animal** on cooldown for **{attacker.spirit_animal_cooldown}** turn(s)!"; cd_blocked = True
            else:
                attacker.spirit_animal_active = True
                attacker.spirit_animal_turns = 0
                hp_bonus = int(attacker.max_hp * 0.40)
                attacker.spirit_animal_hp_bonus = hp_bonus
                attacker.max_hp += hp_bonus
                attacker.hp = min(attacker.max_hp, attacker.hp + hp_bonus)
                attacker.damage_boost_pct += 0.50
                msg += (f"🐻 **Spirit Animal Transformation!** **{attacker.user.display_name}** becomes a giant bear — "
                        f"**+40% HP** (+{hp_bonus}) and **+50% physical strength**! Costs **5 SP/round** to maintain. *(Use again to deactivate, 10 turn cd)*"); color = 0x228B22

        elif ability_key == "wrath_of_nature":
            if attacker.wrath_of_nature_cooldown > 0:
                attacker.sp += actual_cost
                msg += f"🌿 **Wrath of Nature** on cooldown for **{attacker.wrath_of_nature_cooldown}** turn(s)!"; cd_blocked = True
            else:
                attacker.wrath_of_nature_cooldown = 5
                roll = random.random()
                if roll < 0.30:
                    defender.trapped = max(defender.trapped, 3)
                    msg += (f"🌿 **Wrath of Nature — Swamp!** The earth swallows **{defender.user.display_name}** — "
                            f"trapped for **3 turns**! *(5 turn cd)*"); color = 0x228B22
                elif roll < 0.60:
                    wood_hp = 20
                    attacker.hp = min(attacker.max_hp, attacker.hp + wood_hp)
                    attacker.wrath_of_nature_thorn = True
                    msg += (f"🌿 **Wrath of Nature — Child of the Oak!** Wooden armour grants **+{wood_hp} HP** "
                            f"+ thorn recoil **5 dmg** each time **{attacker.user.display_name}** is struck! *(5 turn cd)*"); color = 0x228B22
                else:
                    attacker.wrath_of_nature_poison_turns = 4
                    msg += (f"🌿 **Wrath of Nature — Poison Creation!** Poisonous vines unleash a shroud — "
                            f"**{defender.user.display_name}** suffers **15 dmg/round** for **4 rounds**! *(5 turn cd)*"); color = 0x228B22

        elif ability_key == "possession":
            allowed_gap, gap = seq_gap_check(attacker, defender, max_gap=2)
            if not allowed_gap:
                attacker.sp += actual_cost
                msg += f"😈 **Possession failed!** **{defender.user.display_name}**'s spirit is too refined to be possessed. (gap: {gap})"; color = 0x95A5A6
                cd_blocked = True
            if attacker.possession_active:
                attacker.sp += actual_cost
                msg += f"👁️ **Possession** already active!"; cd_blocked = True
            elif attacker.possession_cooldown > 0:
                attacker.sp += actual_cost
                msg += f"👁️ **Possession** on cooldown for **{attacker.possession_cooldown}** turn(s)!"; cd_blocked = True
            else:
                def_pathway = get_pathway_name(defender.role) if defender.role else ""
                def_seq = get_seq_number(defender.role) if defender.role else 9
                if def_pathway == "Sun" and def_seq <= 5:
                    attacker.sp += actual_cost
                    msg += f"👁️ **Possession** — Sun pathway Seq 5+ is completely immune!"; cd_blocked = True
                else:
                    attacker.possession_active = True
                    turns = random.choices([3, 4, 5], weights=[50, 30, 20])[0]
                    attacker.possession_turns = turns
                    attacker.possession_cooldown = 6
                    msg += (f"👁️ **Possession!** **{attacker.user.display_name}** slips into **{defender.user.display_name}**'s body — "
                            f"**20 damage/turn** for **{turns} turns**! *(6 turn cd)*"); color = 0x8B0000

        elif ability_key == "wraith_shriek":
            if attacker.wraith_shriek_cooldown > 0:
                attacker.sp += actual_cost
                msg += f"👻 **Wraith Shriek** on cooldown for **{attacker.wraith_shriek_cooldown}** turn(s)!"; cd_blocked = True
            else:
                attacker.wraith_shriek_cooldown = 4
                attacker.wraith_shriek_acc_debuff = 2
                sp_stripped = min(seq_val(attacker, 40), defender.sp)
                defender.sp = max(0, defender.sp - sp_stripped)
                defender.apply_status_effect("bleed", 5)
                msg += (f"👻 **Wraith Shriek!** A soul-rending shriek — **{sp_stripped} SP** stripped from "
                        f"**{defender.user.display_name}** + **5-round bleed**! "
                        f"**{attacker.user.display_name}**'s accuracy is **-40%** for 2 turns. *(4 turn cd)*"); color = 0x4B0082

        elif ability_key == "grazing":
            allowed_gap, gap = seq_gap_check(attacker, defender, max_gap=3)
            if not allowed_gap:
                attacker.sp += actual_cost
                msg += f"🌾 **Grazing failed!** Cannot graze the spirit body of someone {gap} sequences stronger."; color = 0x95A5A6
                cd_blocked = True
            # Shepherd Seq 5 (Hanged Man) — graze the opponent's spirit body to copy their abilities
            if attacker.grazing_used:
                attacker.sp += actual_cost
                msg += f"🌾 **Grazing** — already grazed this battle!"; color = 0x95A5A6
                cd_blocked = True
            else:
                attacker.grazing_used = True
                # Copy up to 3 of the opponent's available abilities (role ability + used abilities)
                available = list(getattr(defender, "abilities_used_this_battle", set()))
                if defender.role_ability and defender.role_ability not in available:
                    available.insert(0, defender.role_ability)
                copied = available[:3]
                attacker.grazed_abilities = list(copied)
                names = []
                for ak in copied:
                    info_g, _ = get_ability_info(ak)
                    names.append(info_g["name"] if info_g else ak)
                attacker.grazing_cooldown = 5
                if copied:
                    msg += (f"🌾 **Grazing!** **{attacker.user.display_name}** grazes **{defender.user.display_name}**'s spirit body — "
                            f"copied **{len(copied)}** abilities: **{', '.join(names)}**! Use them with **Ability Bag**. *(5 turn cd)*"); color = 0x2ECC71
                else:
                    attacker.sp += actual_cost
                    msg += f"🌾 **Grazing** — nothing to copy yet!"; color = 0x95A5A6

        elif ability_key == "desire_explosion":
            if attacker.desire_explosion_uses <= 0:
                attacker.sp += actual_cost
                msg += f"💥 **Desire Explosion** — no uses remaining!"; cd_blocked = True
            elif attacker.desire_explosion_cooldown > 0:
                attacker.sp += actual_cost
                msg += f"💥 **Desire Explosion** on cooldown for **{attacker.desire_explosion_cooldown}** turn(s)!"; cd_blocked = True
            elif not attacker.vile_crime_used:
                attacker.sp += actual_cost
                msg += f"💥 **Desire Explosion** — requires a vile crime to be committed first!"; cd_blocked = True
            else:
                attacker.desire_explosion_uses -= 1
                attacker.desire_explosion_cooldown = 5
                actual_dmg = apply_ability_damage(attacker, defender, 0.119, is_ability=True)  # ~50 at seq5
                stun_roll = random.random()
                if stun_roll < 0.50:
                    stun = 1
                elif stun_roll < 0.90:
                    stun = 2
                else:
                    stun = 3
                defender.apply_status_effect("paralysis", stun)
                msg += (f"💥 **Desire Explosion!** A mental shockwave erupts — **{actual_dmg}** damage + "
                        f"**{stun}-turn stun**! *({attacker.desire_explosion_uses} use(s) left, 5 turn cd)*"); color = 0xC71585

        elif ability_key == "desire_symbiosis":
            if attacker.desire_symbiosis_used:
                attacker.sp += actual_cost
                msg += f"😈 **Desire Symbiosis** already used!"; cd_blocked = True
            else:
                attacker.desire_symbiosis_used = True
                emotion = random.choice(["wrath", "lust", "envy"])
                attacker.desire_emotion = emotion
                if emotion == "wrath":
                    attacker.damage_boost_pct += 0.50
                    msg += (f"😈 **Desire Symbiosis — Wrath!** **{attacker.user.display_name}** is consumed by rage — "
                            f"**+50% attack** but **-20% accuracy**! *(1 use only)*"); color = 0xE74C3C
                elif emotion == "lust":
                    msg += (f"😈 **Desire Symbiosis — Lust!** **{attacker.user.display_name}** is consumed by desire — "
                            f"**100% accuracy** but **-60% strength**! *(1 use only)*"); color = 0xC71585
                else:
                    sp_bonus = int(attacker.max_sp * 0.70)
                    attacker.sp = min(attacker.max_sp, attacker.sp + sp_bonus)
                    msg += (f"😈 **Desire Symbiosis — Envy!** **{attacker.user.display_name}** covets power — "
                            f"**+70% SP** (+{sp_bonus}) but **-30% strength & accuracy**! *(1 use only)*"); color = 0x9B59B6

        elif ability_key == "prohibition":
            if attacker.prohibition_cooldown > 0:
                attacker.sp += actual_cost; msg += f"🚫 **Prohibition** on cooldown ({attacker.prohibition_cooldown}t)!"; cd_blocked = True
            elif attacker.prohibition_uses <= 0:
                attacker.sp += actual_cost; msg += f"🚫 **Prohibition** — no uses remaining!"; cd_blocked = True
            else:
                # Show menu to select which ability to prohibit
                allowed_gap, gap = seq_gap_check(attacker, defender, max_gap=3)
                if not allowed_gap:
                    attacker.sp += actual_cost
                    msg += f"🚫 **Prohibition failed!** {gap} sequences too strong."; cd_blocked = True
                else:
                    # Build list of defender abilities to choose from
                    def_abilities = []
                    if defender.role_ability:
                        info_p, _ = get_ability_info(defender.role_ability)
                        def_abilities.append((defender.role_ability, info_p["name"] if info_p else defender.role_ability))
                    for spell in getattr(defender, "purchased_spells", {}).keys() if hasattr(defender, "purchased_spells") else []:
                        info_p, _ = get_ability_info(spell)
                        def_abilities.append((spell, info_p["name"] if info_p else spell))
                    if not def_abilities:
                        attacker.sp += actual_cost; msg += f"🚫 **Prohibition** — no abilities to prohibit!"; cd_blocked = True
                    else:
                        # For now prohibit role ability by default (menu would be ideal but complex)
                        target_key = ritual_choice if ritual_choice and ritual_choice in dict(def_abilities) else def_abilities[0][0]
                        target_name = dict(def_abilities).get(target_key, target_key)
                        setattr(defender, f"_prohibited_{target_key}", True)
                        setattr(defender, f"_prohibited_{target_key}_turns", 10)
                        attacker.prohibition_cooldown = 10
                        attacker.prohibition_uses -= 1
                        msg += (f"🚫 **Prohibition!** **{target_name}** prohibited for **10 turns**! "
                                f"({attacker.prohibition_uses} use(s) remaining) *(10t cd)*"); color = 0xF39C12

        elif ability_key == "punishment":
            if attacker.punishment_cooldown > 0:
                attacker.sp += actual_cost; msg += f"⛓️ **Punishment** on cooldown ({attacker.punishment_cooldown}t)!"; cd_blocked = True
            elif getattr(attacker, "punishment_active", False):
                attacker.sp += actual_cost; msg += f"⛓️ **Punishment** already active!"; cd_blocked = True
            else:
                attacker.punishment_cooldown = 8
                attacker.punishment_active = True
                attacker.punishment_turns = 5
                # Cleanse all current debuffs
                for attr in ["bleed","burn","paralysis","slumber","freeze","debuff_stacks","charm_turns"]:
                    if getattr(attacker, attr, 0): setattr(attacker, attr, 0)
                attacker.bleed = 0; attacker.burn = 0; attacker.paralysis = 0
                attacker.slumber = 0; attacker.freeze = 0
                if attacker.charm_turns > 0: attacker.charm_turns = 0; attacker.charm_chance = 0.0
                # Immune to debuffs from enemies ≤1 seq higher
                attacker._punishment_immunity_turns = 5
                msg += (f"⛓️ **Punishment!** All debuffs cleansed + **immune to debuffs** (from enemies ≤1 seq higher) for **5 turns**! "
                        f"Punishment state active — deal **+25% damage** for 5 turns. *(8t cd)*"); color = 0xF39C12

        elif ability_key == "disciplinary_strike":
            if attacker.disciplinary_strike_cooldown > 0:
                attacker.sp += actual_cost
                msg += f"⚖️ **Disciplinary Strike** on cooldown for **{attacker.disciplinary_strike_cooldown}** turn(s)!"; cd_blocked = True
            else:
                attacker.disciplinary_strike_cooldown = 4
                actual_dmg = apply_ability_damage(attacker, defender, 0.143, is_ability=True)
                defender.judgement_debuff_turns = max(defender.judgement_debuff_turns, 3)
                stripped = None
                if defender.notary_buff_turns > 0:
                    defender.notary_buff_turns = 0; stripped = "Notary Buff"
                elif defender.domination_active:
                    defender.domination_active = False; stripped = "Domination"
                elif defender.appraisal_active:
                    defender.appraisal_active = False; stripped = "Appraisal"
                elif defender.invigorated:
                    defender.invigorated = False; stripped = "Invigorate"
                elif defender.artificial_moon_turns > 0:
                    defender.artificial_moon_turns = 0; stripped = "Artificial Moon"
                elif getattr(defender, "dawn_armor_active", False):
                    defender.dawn_armor_active = False
                    defender.max_hp = max(1, defender.max_hp - defender.dawn_armor_hp_bonus)
                    defender.hp = min(defender.max_hp, defender.hp)
                    defender.damage_boost_pct = max(0.0, defender.damage_boost_pct - 0.10)
                    defender.dawn_armor_cooldown = 3
                    stripped = "Dawn Armor"
                strip_txt = f" Stripped **{stripped}**!" if stripped else ""
                msg += (f"⚖️ **Disciplinary Strike!** A blow of supreme authority — **{actual_dmg}** damage!{strip_txt} "
                        f"Judgement debuff extended. *(4 turn cd)*"); color = 0x4169E1

        elif ability_key == "disorder":
            cd = getattr(attacker, "disorder_cd", 0)
            if cd > 0: attacker.sp += actual_cost; msg += f"🌀 **Disorder** on cooldown ({cd}t)!"; cd_blocked = True
            else:
                attacker.disorder_cd = 5
                # -25% accuracy for 3 turns
                attacker._force_miss_on_defender = getattr(attacker, "_force_miss_on_defender", 0)
                defender.conspiracy_miss_turns = max(defender.conspiracy_miss_turns, 3)
                roll = random.random()
                if roll < 0.33:
                    # Chaos — enemy deals self-damage 7-10% HP
                    pct = random.uniform(0.07, 0.10)
                    dmg = max(1, int(defender.hp * pct))
                    defender.hp = max(0, defender.hp - dmg)
                    msg += f"🌀 **Disorder — Chaos!** **{defender.user.display_name}** descends into chaos — deals **{dmg}** self-damage ({int(pct*100)}% HP)! *(5t cd)*"; color = 0x8E44AD
                elif roll < 0.66:
                    # Skip 2 turns
                    defender.stun = 2
                    msg += f"🌀 **Disorder — Mind Break!** **{defender.user.display_name}** skips **2 turns**! *(5t cd)*"; color = 0x8E44AD
                else:
                    # -20% strength 3 turns
                    defender._disorder_str_debuff_turns = 3
                    msg += f"🌀 **Disorder — Weakness!** **{defender.user.display_name}**'s strength −**20%** for **3 turns**! *(5t cd)*"; color = 0x8E44AD

        elif ability_key == "gift_of_corruption":
            cd = getattr(attacker, "gift_of_corruption_cd", 0)
            if cd > 0: attacker.sp += actual_cost; msg += f"🎁 **Gift of Corruption** on cooldown ({cd}t)!"; cd_blocked = True
            else:
                attacker.gift_of_corruption_cd = 4
                if random.random() < 0.10:
                    # 10% failure — stunned by higher power
                    attacker.stun = 1
                    msg += f"🎁 **Gift of Corruption — Failed!** A higher power intervenes — **{attacker.user.display_name}** is stunned for **1 turn**!"; color = 0x95A5A6
                else:
                    # Transfer all negative statuses to defender
                    transferred = []
                    for attr, label in [("bleed","bleed"),("burn","burn"),("paralysis","paralysis"),
                                        ("slumber","sleep"),("freeze","freeze"),("debuff_stacks","debuffs")]:
                        val = getattr(attacker, attr, 0)
                        if val:
                            setattr(attacker, attr, 0)
                            cur = getattr(defender, attr, 0)
                            setattr(defender, attr, max(cur, val))
                            transferred.append(label)
                    if attacker.bleed > 0: defender.bleed = max(defender.bleed, attacker.bleed); attacker.bleed = 0; transferred.append("bleed") if "bleed" not in transferred else None
                    if attacker.stun > 0 and not hasattr(attacker, '_stun_transferred'):
                        defender.stun = max(defender.stun, attacker.stun); attacker.stun = 0; transferred.append("stun")
                    txt = ", ".join(transferred) if transferred else "nothing to transfer"
                    msg += f"🎁 **Gift of Corruption!** Transferred **{txt}** to **{defender.user.display_name}**! *(4t cd)*"; color = 0x8E44AD

        elif ability_key == "sacred_conviction":
            if attacker.sacred_conviction_cooldown > 0:
                attacker.sp += actual_cost
                msg += f"🌟 **Sacred Conviction** on cooldown for **{attacker.sacred_conviction_cooldown}** turn(s)!"; cd_blocked = True
            else:
                attacker.sacred_conviction_cooldown = 5
                # Cleanse all debuffs on self
                cleared = []
                if attacker.bleed > 0:      attacker.bleed = 0;      cleared.append("bleed")
                if attacker.burn > 0:       attacker.burn = 0;       cleared.append("burn")
                if attacker.paralysis > 0:  attacker.paralysis = 0;  cleared.append("paralysis")
                if attacker.slumber > 0:    attacker.slumber = 0;    cleared.append("slumber")
                if attacker.freeze > 0:     attacker.freeze = 0;     cleared.append("freeze")
                if attacker.judgement_debuff_turns > 0: attacker.judgement_debuff_turns = 0; cleared.append("judgement")
                if attacker.curse_of_misfortune_turns > 0: attacker.curse_of_misfortune_turns = 0; cleared.append("misfortune")
                if getattr(attacker, "dream_alteration_acc_loss", 0) > 0: attacker.dream_alteration_acc_loss = 0; cleared.append("dream alteration")
                # +20% damage boost for 3 turns
                attacker.sacred_conviction_boost_turns = 3
                attacker.damage_boost_pct += 0.20
                # Deal righteous damage to opponent
                actual_dmg = apply_ability_damage(attacker, defender, 0.095, is_ability=True)
                cleanse_txt = f" Cleansed: **{', '.join(cleared)}**." if cleared else " No debuffs to cleanse."
                msg += (f"🌟 **Sacred Conviction!** The power of order surges — **{actual_dmg}** righteous damage!{cleanse_txt} "
                        f"**+20% damage** for **3 turns**! *(5 turn cd)*"); color = 0x4169E1


    # ── Victory check ──────────────────────────────────────
    v = check_victory(attacker, defender, battle_key, msg)
    if v:
        await interaction.channel.send(embed=v)
        old_msg = battle.get("battle_message")
        if old_msg:
            try: await old_msg.delete()
            except: pass
        return

    # Append any divination dodge message generated during ability damage
    div_msg = getattr(defender, "_pending_divination_msg", "")
    if div_msg:
        msg += f"\n{div_msg}"
        defender._pending_divination_msg = ""

    if action == "ability" and cd_blocked:
        # Cooldown or already-used — SP already refunded, turn is NOT consumed
        attacker.polymath_pending = False  # clean up if ability was blocked
        embed = make_battle_embed(msg, battle, battle["turn"].display_name, color)
        await send_and_replace(battle, interaction.channel, embed, view=_make_battle_view(battle))
        return

    attacker.polymath_pending = False  # ensure cleared after any ability resolves

    # ── Combo Spell trigger (Mysticism Magister) ──────────────
    if getattr(attacker, "combination_spell_active", False) and not cd_blocked:
        COMBOS = {
            frozenset({"leodero", "debuff"}):                  "lightning_curse",
            frozenset({"analyse", "ritualistic_reasoning"}):   "spirit_seal",
            frozenset({"study", "polymath"}):                  "greater_rejuvenation",
            frozenset({"debuff", "analyse"}):                  "cursed_betrayal",
        }
        prev = attacker.combo_last_ability
        curr = ability_key if action == "ability" else action
        triggered_combo = None
        if prev and curr:
            pair = frozenset({prev, curr})
            triggered_combo = COMBOS.get(pair)
        attacker.combo_last_ability = curr  # update for next turn

        if triggered_combo == "lightning_curse":
            combo_dmg = apply_ability_damage(attacker, defender, 0.30, skill_key="lightning_curse")
            applied = defender.apply_status_effect("paralysis", 2)
            msg += (f"\n⚡🔮 **COMBO — Lightning Curse!** Arcane lightning surges — **{combo_dmg}** damage"
                    f"{' + paralysis 2 turns' if applied else ''}!")
            color = 0xF1C40F
        elif triggered_combo == "spirit_seal":
            applied = defender.apply_status_effect("slumber", 2)
            seal_dmg = apply_ability_damage(attacker, defender, 0.12)
            msg += (f"\n🔮💤 **COMBO — Spirit Seal!** Consciousness locked away — **{seal_dmg}** damage"
                    f"{' + sleep 2 turns' if applied else ''}!")
            color = 0x9B59B6
        elif triggered_combo == "greater_rejuvenation":
            heal = int(attacker.max_hp * 0.20)
            sp_gain = int(attacker.max_sp * 0.30)
            attacker.hp = min(attacker.max_hp, attacker.hp + heal)
            attacker.sp = min(attacker.max_sp, attacker.sp + sp_gain)
            msg += f"\n🔮💚 **COMBO — Greater Rejuvenation!** Restored **{heal} HP** and **{sp_gain} SP**!"
            color = 0x2ECC71
        elif triggered_combo == "cursed_betrayal":
            betrayal_total = 0
            for _ in range(3):
                betrayal_total += apply_ability_damage(attacker, defender, 0.09)
            defender.debuff_stacks = max(defender.debuff_stacks, 2)
            msg += f"\n🔮☠️ **COMBO — Cursed Betrayal!** Triple arcane strike — **{betrayal_total}** total damage + defence broken!"
            color = 0xE74C3C

    # ── Combo Victory check (combo spells can kill) ──────────────
    v = check_victory(attacker, defender, battle_key, msg)
    if v:
        await interaction.channel.send(embed=v)
        old_msg = battle.get("battle_message")
        if old_msg:
            try: await old_msg.delete()
            except: pass
        return

    swap_turn(battle)

    # Prepend sudden death drain message if it was set during swap_turn
    sudden_msg = battle.pop("_sudden_death_msg", "")
    if sudden_msg:
        msg = sudden_msg + msg
        # Check if sudden death killed someone
        v = check_victory(attacker, defender, battle_key, msg)
        if v:
            await send_and_replace(battle, interaction.channel, v)
            return

    embed = make_battle_embed(msg, battle, battle["turn"].display_name, color)
    await send_and_replace(battle, interaction.channel, embed, view=_make_battle_view(battle))

# ── Gambling ──────────────────────────────────────────────

class CoinFlipView(discord.ui.View):
    def __init__(self, user_id: int, amount: int):
        super().__init__(timeout=30)
        self.user_id = user_id
        self.amount = amount

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await safe_respond(interaction, "❌ This isn't your game!")
            return False
        return True

    @discord.ui.button(label="🟡 Heads", style=discord.ButtonStyle.primary)
    async def heads_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await resolve_coinflip(interaction, "heads", self.amount)
        self.stop()

    @discord.ui.button(label="⚪ Tails", style=discord.ButtonStyle.secondary)
    async def tails_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await resolve_coinflip(interaction, "tails", self.amount)
        self.stop()

async def resolve_coinflip(interaction: discord.Interaction, choice: str, amount: int):
    result = random.choice(["heads", "tails"])
    won = choice == result
    emoji = "🟡" if result == "heads" else "⚪"
    if won:
        add_pounds(interaction.user.id, amount * 2, guild_id=interaction.guild_id)  # return bet + equal winnings
        new_bal = get_pounds(interaction.user.id, guild_id=interaction.guild_id)
        desc = f"{emoji} **{result.capitalize()}**!\n✅ Won **{amount:,} soli**!\n💰 Balance: **{new_bal:,} soli**"
        color = 0x2ECC71
    else:
        new_bal = get_pounds(interaction.user.id, guild_id=interaction.guild_id)
        desc = f"{emoji} **{result.capitalize()}**!\n❌ Lost **{amount:,} soli**.\n💰 Balance: **{new_bal:,} soli**"
        color = 0xE74C3C
    embed = discord.Embed(title="🪙 Coin Flip", description=desc, color=color)
    await interaction.response.edit_message(embed=embed, view=None)

SLOT_SYMBOLS = ["🍒", "🍋", "🍊", "🔔", "⭐", "💎"]
SLOT_WEIGHTS = [35, 25, 18, 12, 7, 3]
SLOT_JACKPOTS = {
    "💎": (15, "💎 JACKPOT"), "⭐": (10, "⭐ Big Win"),
    "🔔": (7, "🔔 Winner"),  "🍊": (5, "🍊 Winner"),
    "🍋": (3, "🍋 Winner"),  "🍒": (2, "🍒 Winner"),
}

def spin_slots():
    return random.choices(SLOT_SYMBOLS, weights=SLOT_WEIGHTS, k=3)

def get_slot_result(reels, bet):
    a, b, c = reels
    if a == b == c:
        mult, label = SLOT_JACKPOTS[a]
        return int(bet * mult), mult, label, True
    return 0, 0, "No Match", False

def make_slots_embed(user, reels, bet, payout, mult, label, jackpot, guild_id=0):
    reel_str = f"**[ {reels[0]} | {reels[1]} | {reels[2]} ]**"
    color = 0xF1C40F if jackpot else 0x2C2F33
    title = f"🎰  {label}!" if jackpot else "🎰  Slots"
    desc = [reel_str, ""]
    if payout > 0:
        add_pounds(user.id, payout, guild_id=guild_id)
        new_bal = get_pounds(user.id, guild_id=guild_id)
        desc.append(f"**{label}!** Won **{payout:,} soli** (**{mult}x**)")
    else:
        new_bal = get_pounds(user.id, guild_id=guild_id)
        desc.append(f"No match. Lost **{bet:,} soli**.")
    desc.append(f"💰 Balance: **{new_bal:,} soli**")
    embed = discord.Embed(title=title, description="\n".join(desc), color=color)
    embed.set_footer(text="3 of a kind wins  |  💎=15x  ⭐=10x  🔔=7x  🍊=5x  🍋=3x  🍒=2x")
    return embed

def card_value(card):
    rank = card[:-1]
    if rank in ("J", "Q", "K"): return 10
    if rank == "A": return 11
    return int(rank)

def hand_value(hand):
    total = sum(card_value(c) for c in hand)
    aces = sum(1 for c in hand if c[:-1] == "A")
    while total > 21 and aces:
        total -= 10; aces -= 1
    return total

def make_deck():
    suits = ["♠", "♥", "♦", "♣"]
    ranks = ["A","2","3","4","5","6","7","8","9","10","J","Q","K"]
    deck = [r+s for s in suits for r in ranks]
    random.shuffle(deck)
    return deck

def _ensure_deck(game, needed=5):
    """Reshuffle if deck is running low."""
    if len(game["deck"]) < needed:
        game["deck"] = make_deck()

def fmt_hand(hand, hide_hole=False):
    if hide_hole and len(hand) >= 2:
        return f"{hand[0]}  🂠"
    return "  ".join(hand)

def make_bj_embed(user, game, *, reveal=False, result_text="", color=0x2B2D31):
    pval = hand_value(game["player"])
    dval = hand_value(game["dealer"])
    player_str = f"{fmt_hand(game['player'])}  `= {pval}`"
    dealer_str = f"{fmt_hand(game['dealer'])}  `= {dval}`" if reveal else f"{fmt_hand(game['dealer'], hide_hole=True)}  `= ?`"
    embed = discord.Embed(title="🃏  Blackjack", color=color)
    embed.add_field(name="Dealer", value=dealer_str, inline=False)
    embed.add_field(name=user.display_name, value=player_str, inline=False)
    if result_text:
        embed.add_field(name="Result", value=result_text, inline=False)
    else:
        doubled = " *(doubled)*" if game.get("doubled") else ""
        embed.set_footer(text=f"Bet: 💰{game['bet']:,}{doubled}  |  Hit · Stand · Double Down")
    return embed

def bj_finish(user, game, guild_id=0):
    _ensure_deck(game)
    while hand_value(game["dealer"]) < 17:
        game["dealer"].append(game["deck"].pop())
    pval = hand_value(game["player"])
    dval = hand_value(game["dealer"])
    bet  = game["bet"]
    # Natural blackjack (21 with 2 cards) pays 3:2
    natural = (pval == 21 and len(game["player"]) == 2)
    if natural and not (dval == 21 and len(game["dealer"]) == 2):
        payout = int(bet * 2.5)
        add_pounds(user.id, payout, guild_id=guild_id)
        new_bal = get_pounds(user.id, guild_id=guild_id)
        result = f"🃏 **Blackjack!** Won **{payout - bet:,} soli** (3:2)!"
        color = 0xF1C40F
    elif dval > 21 or pval > dval:
        add_pounds(user.id, bet * 2, guild_id=guild_id)
        new_bal = get_pounds(user.id, guild_id=guild_id)
        result = f"✅ Won **{bet:,} soli**!"
        color = 0x57F287
    elif pval == dval:
        add_pounds(user.id, bet, guild_id=guild_id)
        new_bal = get_pounds(user.id, guild_id=guild_id)
        result = f"🤝 Push — **{bet:,} soli** returned."
        color = 0xFEE75C
    else:
        new_bal = get_pounds(user.id, guild_id=guild_id)
        result = f"❌ Lost **{bet:,} soli**."
        color = 0xED4245
    result += f"\n💰 Balance: **{new_bal:,} soli**"
    return make_bj_embed(user, game, reveal=True, result_text=result, color=color), color

class BlackjackView(discord.ui.View):
    async def on_error(self, interaction: discord.Interaction, error: Exception, item) -> None:
        try:
            await interaction.response.send_message(f"❌ Error: `{error}`", ephemeral=True)
        except Exception:
            try:
                await interaction.followup.send(f"❌ Error: `{error}`", ephemeral=True)
            except Exception:
                pass

    def __init__(self, user_id):
        super().__init__(timeout=180)   # 3 minutes — plenty of time
        self.user_id = user_id

    async def interaction_check(self, interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("❌ Not your game!", ephemeral=True)
            return False
        return True

    async def on_timeout(self):
        # Auto-stand on timeout — don't just silently expire
        game = blackjack_games.pop(self.user_id, None)
        # Can't edit without interaction; just clean up the game state
        if game:
            blackjack_games.pop(self.user_id, None)

    async def _safe_edit(self, interaction, embed, view=None):
        """Edit the message, falling back to followup if already responded."""
        try:
            await interaction.response.edit_message(embed=embed, view=view)
        except discord.errors.InteractionResponded:
            try:
                await interaction.edit_original_response(embed=embed, view=view)
            except Exception:
                pass
        except discord.errors.NotFound:
            pass

    @discord.ui.button(label="Hit", style=discord.ButtonStyle.primary, emoji="👊")
    async def hit_btn(self, interaction, button):
        game = blackjack_games.get(interaction.user.id)
        if not game:
            return await interaction.response.send_message("❌ No active game — use `/blackjack` to start.", ephemeral=True)
        _ensure_deck(game)
        game["player"].append(game["deck"].pop())
        pval = hand_value(game["player"])
        if pval > 21:
            blackjack_games.pop(interaction.user.id, None)
            loss = f"❌ Bust! Lost **{game['bet']:,} soli**.\n💰 Balance: **{get_pounds(interaction.user.id, guild_id=interaction.guild_id):,} soli**"
            embed = make_bj_embed(interaction.user, game, reveal=True, result_text=loss, color=0xED4245)
            self.stop()
            return await self._safe_edit(interaction, embed, view=None)
        if pval == 21:
            g = blackjack_games.pop(interaction.user.id, game)
            embed, _ = bj_finish(interaction.user, g, guild_id=interaction.guild_id)
            self.stop()
            return await self._safe_edit(interaction, embed, view=None)
        await self._safe_edit(interaction, make_bj_embed(interaction.user, game), view=self)

    @discord.ui.button(label="Stand", style=discord.ButtonStyle.danger, emoji="🛑")
    async def stand_btn(self, interaction, button):
        game = blackjack_games.pop(interaction.user.id, None)
        if not game:
            return await interaction.response.send_message("❌ No active game.", ephemeral=True)
        embed, _ = bj_finish(interaction.user, game, guild_id=interaction.guild_id)
        self.stop()
        await self._safe_edit(interaction, embed, view=None)

    @discord.ui.button(label="Double Down", style=discord.ButtonStyle.secondary, emoji="💰")
    async def double_btn(self, interaction, button):
        game = blackjack_games.get(interaction.user.id)
        if not game:
            return await interaction.response.send_message("❌ No active game.", ephemeral=True)
        if game.get("doubled"):
            return await interaction.response.send_message("❌ Already doubled down!", ephemeral=True)
        extra = game["bet"]
        bal = get_pounds(interaction.user.id, guild_id=interaction.guild_id)
        if bal < extra:
            return await interaction.response.send_message(f"❌ Need **{extra:,} soli** more to double.", ephemeral=True)
        remove_pounds(interaction.user.id, extra, guild_id=interaction.guild_id)
        game["bet"] += extra
        game["doubled"] = True
        _ensure_deck(game)
        game["player"].append(game["deck"].pop())
        pval = hand_value(game["player"])
        g = blackjack_games.pop(interaction.user.id, game)
        if pval > 21:
            loss = f"❌ Bust after double! Lost **{g['bet']:,} soli**.\n💰 Balance: **{get_pounds(interaction.user.id, guild_id=interaction.guild_id):,} soli**"
            embed = make_bj_embed(interaction.user, g, reveal=True, result_text=loss, color=0xED4245)
        else:
            embed, _ = bj_finish(interaction.user, g, guild_id=interaction.guild_id)
        self.stop()
        await self._safe_edit(interaction, embed, view=None)

# ── Slash commands ────────────────────────────────────────

@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    """Catch any unhandled slash command errors so they don't silently die."""
    import traceback
    traceback.print_exc()
    await safe_respond(interaction, f"❌ Command error: `{type(error).__name__}: {error}`")

@bot.event
async def on_error(event: str, *args, **kwargs):
    """Catch any unhandled bot-level errors and print them instead of crashing."""
    import traceback
    print(f"[on_error] Unhandled error in event '{event}':")
    traceback.print_exc()

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

@bot.tree.command(name="fight", description="Challenge another warrior to a turn-based spiritual duel.")
@app_commands.describe(opponent="The warrior you wish to challenge.")
async def fight(interaction: discord.Interaction, opponent: discord.Member):
    if opponent == interaction.user:
        return await interaction.response.send_message("❌ You cannot challenge yourself.", ephemeral=True)
    if opponent.bot:
        return await interaction.response.send_message("❌ Bots do not bleed.", ephemeral=True)

    # Check if either player is already in a fight in this channel
    for key, b in battles.items():
        if key[0] == interaction.channel.id:
            involved = {b["challenger"].user, b["opponent"].user}
            if interaction.user in involved:
                return await interaction.response.send_message("❌ You are already in a duel!", ephemeral=True)
            if opponent in involved:
                return await interaction.response.send_message("❌ That opponent is already in a duel!", ephemeral=True)

    c = await Fighter.load(interaction.user, interaction.guild_id)
    o = await Fighter.load(opponent, interaction.guild_id)

    first = interaction.user
    if c.stats["speed"] < o.stats["speed"]:
        first = opponent
    elif c.stats["speed"] == o.stats["speed"]:
        first = interaction.user if c.stats["luck"] >= o.stats["luck"] else opponent

    bkey = _battle_key(interaction.channel.id, interaction.user.id, opponent.id)
    battles[bkey] = {
        "challenger": c, "opponent": o,
        "turn": first, "accepted": False,
        "shield_challenger": False, "shield_opponent": False,
        "turns": 0, "battle_message": None,
        "guild_id": interaction.guild_id,
    }

    embed = discord.Embed(
        title="⚔️ A Challenge Has Been Issued!",
        description=(
            f"**{interaction.user.display_name}** steps into the arena and locks eyes with **{opponent.display_name}**.\n\n"
            f"Will you answer the call, or shrink from glory?"
        ),
        color=0xE74C3C
    )
    embed.set_footer(text="This challenge expires in 60 seconds.")
    await interaction.response.send_message(
        content=f"{interaction.user.mention} {opponent.mention}",
        embed=embed,
        view=ChallengeView(interaction.user, opponent, interaction.channel.id)
    )

@bot.tree.command(name="guide", description="Learn how the bot works — commands, fighting, economy and more.")
async def guide_cmd(interaction: discord.Interaction):
    await interaction.response.send_message(embed=INFO_PAGES[0], view=InfoView(page=0), ephemeral=True)

@bot.tree.command(name="balance", description="Check your pound balance — or someone else's.")
@app_commands.describe(user="The user to check. Leave blank for your own.")
async def balance_cmd(interaction: discord.Interaction, user: discord.Member = None):
    target = user or interaction.user
    bal = get_pounds(target.id, guild_id=interaction.guild_id)
    embed = discord.Embed(description=f"💰 **{target.display_name}** has **{bal:,} soli**.", color=0xF39C12)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="share", description="Send some of your soli to another user.")
@app_commands.describe(user="The user to send money to.", amount="How much to send.")
async def share(interaction: discord.Interaction, user: discord.Member, amount: int):
    if user == interaction.user:
        return await interaction.response.send_message("❌ Can't send to yourself.", ephemeral=True)
    if user.bot:
        return await interaction.response.send_message("❌ Bots don't need money.", ephemeral=True)
    if amount <= 0:
        return await interaction.response.send_message("❌ Amount must be positive.", ephemeral=True)
    bal = get_pounds(interaction.user.id, guild_id=interaction.guild_id)
    if bal < amount:
        return await interaction.response.send_message(f"❌ Only have **{bal:,} soli**.", ephemeral=True)
    remove_pounds(interaction.user.id, amount, guild_id=interaction.guild_id)
    add_pounds(user.id, amount, guild_id=interaction.guild_id)
    embed = discord.Embed(
        title="💸 Money Sent!",
        description=(
            f"**{interaction.user.display_name}** sent **{amount:,} soli** to **{user.display_name}**!\n\n"
            f"💰 {interaction.user.display_name}: **{get_pounds(interaction.user.id, guild_id=interaction.guild_id):,} soli**\n"
            f"💰 {user.display_name}: **{get_pounds(user.id, guild_id=interaction.guild_id):,} soli**"
        ),
        color=0x2ECC71
    )
    await interaction.response.send_message(content=f"{interaction.user.mention} ➜ {user.mention}", embed=embed)

@bot.tree.command(name="coinflip", description="Flip a coin — bet soli on heads or tails.")
@app_commands.describe(amount="How much to bet.")
async def coinflip(interaction: discord.Interaction, amount: int):
    remaining = check_gamble_cooldown(interaction.user.id)
    if remaining > 0:
        return await interaction.response.send_message(f"⏳ Gambling cooldown! Wait **{remaining:.1f}s**.", ephemeral=True)
    if amount <= 0:
        return await interaction.response.send_message("❌ Bet must be positive.", ephemeral=True)
    bal = get_pounds(interaction.user.id, guild_id=interaction.guild_id)
    if bal < amount:
        return await interaction.response.send_message(f"❌ Only have ****{bal:,} soli**.", ephemeral=True)
    set_gamble_cooldown(interaction.user.id)
    remove_pounds(interaction.user.id, amount, guild_id=interaction.guild_id)
    embed = discord.Embed(
        title="🪙 Coin Flip",
        description=f"**{interaction.user.display_name}** bets **{amount:,} soli**! Pick a side:",
        color=0xF39C12
    )
    await interaction.response.send_message(embed=embed, view=CoinFlipView(interaction.user.id, amount))

@bot.tree.command(name="slots", description="Spin the slot machine!")
@app_commands.describe(amount="How much to bet.")
async def slots(interaction: discord.Interaction, amount: int):
    remaining = check_gamble_cooldown(interaction.user.id)
    if remaining > 0:
        return await interaction.response.send_message(f"⏳ Gambling cooldown! Wait **{remaining:.1f}s**.", ephemeral=True)
    if amount <= 0:
        return await interaction.response.send_message("❌ Bet must be positive.", ephemeral=True)
    bal = get_pounds(interaction.user.id, guild_id=interaction.guild_id)
    if bal < amount:
        return await interaction.response.send_message(f"❌ Only have ****{bal:,} soli**.", ephemeral=True)
    set_gamble_cooldown(interaction.user.id)
    remove_pounds(interaction.user.id, amount, guild_id=interaction.guild_id)
    reels = spin_slots()
    payout, mult, label, jackpot = get_slot_result(reels, amount)
    embed = make_slots_embed(interaction.user, reels, amount, payout, mult, label, jackpot, guild_id=interaction.guild_id)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="blackjack", description="Play a hand of blackjack against the dealer.")
@app_commands.describe(amount="How much to bet.")
async def blackjack_cmd(interaction: discord.Interaction, amount: int):
    remaining = check_gamble_cooldown(interaction.user.id)
    if remaining > 0:
        return await interaction.response.send_message(f"⏳ Gambling cooldown! Wait **{remaining:.1f}s**.", ephemeral=True)
    if amount <= 0:
        return await interaction.response.send_message("❌ Bet must be positive.", ephemeral=True)
    bal = get_pounds(interaction.user.id, guild_id=interaction.guild_id)
    if bal < amount:
        return await interaction.response.send_message(f"❌ Only have ****{bal:,} soli**.", ephemeral=True)
    if interaction.user.id in blackjack_games:
        return await interaction.response.send_message("❌ Already have an active game!", ephemeral=True)
    set_gamble_cooldown(interaction.user.id)
    remove_pounds(interaction.user.id, amount, guild_id=interaction.guild_id)
    deck = make_deck()
    player_hand = [deck.pop(), deck.pop()]
    dealer_hand = [deck.pop(), deck.pop()]
    game = {"deck": deck, "player": player_hand, "dealer": dealer_hand, "bet": amount, "doubled": False, "guild_id": interaction.guild_id}
    pval = hand_value(player_hand)
    if pval == 21:
        while hand_value(dealer_hand) < 17:
            dealer_hand.append(deck.pop())
        dval = hand_value(dealer_hand)
        if dval == 21:
            add_pounds(interaction.user.id, amount, guild_id=interaction.guild_id)
            result = f"🤝 Both Blackjack — Push! **{amount:,} soli** returned.\n💰 Balance: **{get_pounds(interaction.user.id, guild_id=interaction.guild_id):,} soli**"
            color = 0xFEE75C
        else:
            payout = int(amount * 2.5)
            add_pounds(interaction.user.id, payout, guild_id=interaction.guild_id)
            result = f"🎉 **BLACKJACK!** Won **{payout:,} soli** total!\n💰 Balance: **{get_pounds(interaction.user.id, guild_id=interaction.guild_id):,} soli**"
            color = 0xF1C40F
        embed = make_bj_embed(interaction.user, game, reveal=True, result_text=result, color=color)
        return await interaction.response.send_message(embed=embed)
    blackjack_games[interaction.user.id] = game
    embed = make_bj_embed(interaction.user, game)
    await interaction.response.send_message(embed=embed, view=BlackjackView(interaction.user.id))

BET_MIN = 5_000
BET_MAX = 100_000

@bot.tree.command(name="bet", description="Bet on an ongoing fight within the first 3 turns.")
@app_commands.describe(
    challenger="The person who issued the challenge.",
    opponent="The person who was challenged.",
    amount="How many soli to bet. (Min 5,000 — Max 100,000)"
)
async def bet(interaction: discord.Interaction, challenger: discord.Member, opponent: discord.Member, amount: int):
    if amount < BET_MIN:
        return await interaction.response.send_message(f"❌ Minimum bet is **{BET_MIN:,} soli**.", ephemeral=True)
    if amount > BET_MAX:
        return await interaction.response.send_message(f"❌ Maximum bet is **{BET_MAX:,} soli**.", ephemeral=True)
    if interaction.user in [challenger, opponent]:
        return await interaction.response.send_message("❌ Can't bet on your own fight.", ephemeral=True)
    bal = get_pounds(interaction.user.id, guild_id=interaction.guild_id)
    if bal < amount:
        return await interaction.response.send_message(f"❌ Only have **{bal:,} soli**.", ephemeral=True)
    channel_id, battle = find_battle_by_players(challenger, opponent)
    if not battle or not battle.get("accepted"):
        return await interaction.response.send_message("❌ No active accepted battle found.", ephemeral=True)
    if battle.get("turns", 0) >= 3:
        return await interaction.response.send_message("❌ Betting closed — past 3 turns.", ephemeral=True)
    if channel_id in active_bets and interaction.user.id in active_bets[channel_id]:
        return await interaction.response.send_message("❌ Already placed a bet.", ephemeral=True)
    await interaction.response.send_message(
        f"💰 Betting **{amount:,} soli**. Who wins?",
        view=BetView(channel_id, challenger, opponent, interaction.user.id, amount, guild_id=interaction.guild_id),
        ephemeral=True
    )


# ── Bounty System ─────────────────────────────────────────────────────────────
# Stored in economy JSON under key "bounties": {str(target_id): {"amount": int, "placed_by": int}}

BOUNTY_MIN = 5_000
BOUNTY_MAX = 500_000

def get_bounties(guild_id: int) -> dict:
    key = f"bounties_{guild_id}"
    eco = get_economy(key, guild_id=0)
    return eco.get("bounty_board", {})

def set_bounties(guild_id: int, board: dict):
    key = f"bounties_{guild_id}"
    eco = get_economy(key, guild_id=0)
    eco["bounty_board"] = board
    # Save via player_economy
    player_economy[(key, 0)] = eco
    save_economy()

def add_bounty(guild_id: int, target_id: int, placer_id: int, amount: int):
    board = get_bounties(guild_id)
    tid = str(target_id)
    if tid in board:
        board[tid]["amount"] += amount
    else:
        board[tid] = {"amount": amount, "placed_by": placer_id}
    set_bounties(guild_id, board)

def claim_bounty(guild_id: int, target_id: int) -> int:
    board = get_bounties(guild_id)
    tid = str(target_id)
    if tid not in board:
        return 0
    amount = board.pop(tid)["amount"]
    set_bounties(guild_id, board)
    return amount


@bot.tree.command(name="bounty", description="Place or remove a bounty on another Beyonder.")
@app_commands.describe(
    action="Place a new bounty or remove one you placed",
    target="The Beyonder the bounty is on.",
    amount="Soli to offer as reward. (Min 5,000 — Max 500,000, only used when placing)"
)
@app_commands.choices(action=[
    app_commands.Choice(name="Place", value="place"),
    app_commands.Choice(name="Remove", value="remove"),
])
async def bounty_cmd(interaction: discord.Interaction, action: app_commands.Choice[str], target: discord.Member, amount: int = 0):
    if await check_blacklist(interaction): return

    if action.value == "place":
        if target.id == interaction.user.id:
            return await interaction.response.send_message("❌ Can't place a bounty on yourself.", ephemeral=True)
        if target.bot:
            return await interaction.response.send_message("❌ Can't bounty a bot.", ephemeral=True)
        if amount < BOUNTY_MIN:
            return await interaction.response.send_message(f"❌ Minimum bounty is **{BOUNTY_MIN:,} soli**.", ephemeral=True)
        if amount > BOUNTY_MAX:
            return await interaction.response.send_message(f"❌ Maximum bounty is **{BOUNTY_MAX:,} soli**.", ephemeral=True)
        bal = get_pounds(interaction.user.id, guild_id=interaction.guild_id)
        if bal < amount:
            return await interaction.response.send_message(f"❌ You only have **{bal:,} soli**.", ephemeral=True)
        remove_pounds(interaction.user.id, amount, guild_id=interaction.guild_id)
        add_bounty(interaction.guild_id, target.id, interaction.user.id, amount)
        await interaction.response.send_message(
            f"🎯 **Bounty placed!**\n"
            f"**{interaction.user.display_name}** has put **{amount:,} soli** on **{target.display_name}**'s head.\n"
            f"Defeat them in PvP to claim it!"
        )
    else:  # remove
        board = get_bounties(interaction.guild_id)
        tid = str(target.id)
        if tid not in board:
            return await interaction.response.send_message("❌ No bounty on that user.", ephemeral=True)
        if board[tid]["placed_by"] != interaction.user.id:
            return await interaction.response.send_message("❌ You didn't place that bounty.", ephemeral=True)
        removed_amount = board.pop(tid)["amount"]
        set_bounties(interaction.guild_id, board)
        refund = removed_amount // 2
        add_pounds(interaction.user.id, refund, guild_id=interaction.guild_id)
        await interaction.response.send_message(
            f"✅ Bounty on **{target.display_name}** removed. Refunded **{refund:,} soli** (50%).",
            ephemeral=True
        )


@bot.tree.command(name="bounties", description="View all active bounties on this server.")
async def bounties_cmd(interaction: discord.Interaction):
    if await check_blacklist(interaction): return
    board = get_bounties(interaction.guild_id)
    if not board:
        return await interaction.response.send_message("📋 No active bounties right now.", ephemeral=True)
    embed = discord.Embed(title="🎯 Bounty Board", color=0xE74C3C)
    lines = []
    for tid, info in sorted(board.items(), key=lambda x: x[1]["amount"], reverse=True):
        member = interaction.guild.get_member(int(tid))
        name   = member.display_name if member else f"<@{tid}>"
        lines.append(f"💀 **{name}** — **{info['amount']:,} soli**")
    embed.description = "\n".join(lines)
    embed.set_footer(text="Defeat a wanted Beyonder in /fight to claim their bounty.")
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="money", description="Admin: Give, take, or wipe a player's soli.")
@app_commands.describe(action="Give, take, or wipe soli", user="Target user", amount="Amount of soli (not needed for Wipe)")
@app_commands.choices(action=[
    app_commands.Choice(name="Give", value="give"),
    app_commands.Choice(name="Take", value="take"),
    app_commands.Choice(name="Wipe", value="wipe"),
])
async def money_cmd(interaction: discord.Interaction, action: app_commands.Choice[str], user: discord.Member, amount: int = 0):
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


@bot.tree.command(name="leave", description="Forfeit your current duel.")
async def leave(interaction: discord.Interaction):
    import time
    user = interaction.user
    channel_id = interaction.channel.id

    found_key = None
    found_battle = None
    for key, battle in list(battles.items()):
        if key[0] != channel_id:
            continue
        if user in (battle["challenger"].user, battle["opponent"].user):
            found_key = key
            found_battle = battle
            break

    if not found_battle:
        return await interaction.response.send_message(
            "❌ You are not in any duel in this channel.", ephemeral=True
        )

    c = found_battle["challenger"]
    o = found_battle["opponent"]
    other = o.user if user == c.user else c.user
    is_pending = not found_battle["accepted"]

    battles.pop(found_key, None)

    bets = active_bets.pop(channel_id, {})
    for bettor_id, bet in bets.items():
        add_pounds(bettor_id, bet["amount"], guild_id=battle["guild_id"])
    bet_notice = "\n💰 All bets have been refunded." if bets else ""

    old_msg = found_battle.get("battle_message")
    if old_msg:
        try:
            await old_msg.delete()
        except Exception:
            pass

    if is_pending:
        desc = (
            f"🏳️ **{user.display_name}** withdrew their challenge against **{other.display_name}**.\n"
            f"Duel cancelled.{bet_notice}\n\n"
            f"The duel is cancelled."
        )
    else:
        # Check astrology flee — no soli penalty
        leaver_fighter = c if user == c.user else o
        other_fighter = o if user == c.user else c
        astrology_escape = getattr(leaver_fighter, "astrology_flee_ready", False)
        if astrology_escape:
            leaver_fighter.astrology_flee_ready = False
            winner_seq = get_seq_number(other_fighter.role) if other_fighter.role else 9
            loser_seq  = get_seq_number(leaver_fighter.role) if leaver_fighter.role else 9
            desc = (
                f"🌟 **{user.display_name}** reads the stars and vanishes — **guaranteed escape** via Astrology!\n"
                f"No soli penalty.{bet_notice}"
            )
        else:
            # Record forfeit as a loss for the leaver, win for the other
            winner_seq = get_seq_number(other_fighter.role) if other_fighter.role else 9
            loser_seq  = get_seq_number(leaver_fighter.role) if leaver_fighter.role else 9
            record_battle(other, user, winner_seq, loser_seq, interaction.guild_id or 0)
            # Soli penalty: 20-40% of leaver's balance goes to winner
            leaver_bal = get_pounds(user.id, guild_id=interaction.guild_id)
            soli_pct = random.uniform(0.20, 0.40)
            soli_lost = int(leaver_bal * soli_pct)
            if soli_lost > 0:
                add_pounds(user.id, -soli_lost, guild_id=interaction.guild_id)
                add_pounds(other.id, soli_lost, guild_id=interaction.guild_id)
            soli_line = f"\n💸 **{user.display_name}** forfeits **{soli_lost:,} soli** ({int(soli_pct*100)}%) to **{other.display_name}**!" if soli_lost > 0 else ""
            desc = (
                f"🏳️ **{user.display_name}** fled from the duel!\n"
                f"**{other.display_name}** wins by default.{soli_line}{bet_notice}"
            )

    embed = discord.Embed(title="🚪 Duel Forfeited", description=desc, color=0x95A5A6)
    await interaction.response.send_message(content=f"{user.mention} {other.mention}", embed=embed)



@bot.tree.command(name="beg", description="Beg for soli. Returns 67 or 69 soli every 2 hours.")
async def beg(interaction: discord.Interaction):
    import time
    user_id = interaction.user.id
    now = time.time()
    BEG_COOLDOWN = 7200

    last = beg_cooldowns.get(user_id, 0)
    elapsed = now - last
    if elapsed < BEG_COOLDOWN:
        remaining = BEG_COOLDOWN - elapsed
        hours = int(remaining // 3600)
        minutes = int((remaining % 3600) // 60)
        return await interaction.response.send_message(
            f"🙏 You already begged recently. Try again in **{hours}h {minutes}m**.",
            ephemeral=True
        )

    beg_cooldowns[user_id] = now
    save_beg()
    amount = random.choice([67, 69])
    add_pounds(user_id, amount, guild_id=interaction.guild_id)
    bal = get_pounds(user_id, guild_id=interaction.guild_id)

    begs = [
        "gets down on their knees and pleads with the universe.",
        "rattles an empty cup in the marketplace.",
        "holds up a sign: *'Will duel for soli'*.",
        "cries dramatically until someone takes pity.",
        "whispers ancient prayers to the Outer Gods.",
    ]
    action = random.choice(begs)

    embed = discord.Embed(
        title="🙏 Begging...",
        description=(
            f"**{interaction.user.display_name}** {action}\n\n"
            f"Someone tosses **{amount} soli** their way!\n"
            f"💼 Balance: **{bal:,} soli**"
        ),
        color=0x95A5A6
    )
    embed.set_footer(text="You can beg again in 2 hours.")
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="set_fight_channel", description="Add or remove this channel from the bot's allowed list. Works across multiple channels.")
async def set_fight_channel(interaction: discord.Interaction):
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

PATHWAY_LIST = [
    "Fool", "Door", "Error", "Hermit", "Paragon",
    "Red Priest", "Demoness", "Abyss", "Chained", "Twilight Giant",
    "Darkness", "Death", "Tyrant", "Sun", "Hanged Man",
    "White Tower", "Visionary", "Mother", "Moon",
    "Wheel of Fortune", "Black Emperor", "Justiciar",
]

def get_pathway_abilities_text(pathway: str) -> str:
    lines = [f"**{pathway} Pathway — Seq 9 to 5**\n"]

    def format_ability(ability_key: str) -> str:
        info = ROLE_ABILITY_INFO.get(ability_key) or ABILITIES.get(ability_key)
        if not info:
            return "> *(ability info not found)*\n"
        name_clean = re.sub(r'^[\U00010000-\U0010ffff\u2000-\u3300\U0001F000-\U0001FAFF\uFE0F\uFE0E]+\s*', '', info['name']).lstrip()
        cost_str = f"`{info['cost']} SP`" if info['cost'] > 0 else f"`{info.get('display_cost','Free')}`"
        return f"{name_clean} — {cost_str}\n> {info['description']}\n"

    for seq in [9, 8, 7, 6, 5]:
        role_name = None
        ability_key = None
        for rn, ak in ROLE_ABILITIES.items():
            if f"[{pathway}] Seq {seq}" in rn:
                role_name = rn
                ability_key = ak
                break
        if not role_name:
            lines.append(f"**Seq {seq}** — *(no entry)*\n")
            continue
        try:
            title = role_name.split("—")[1].strip()
        except IndexError:
            title = role_name

        block = f"**Seq {seq} — {title}**\n" + format_ability(ability_key)

        # Seq 5 is the one rank where every role gets a second ability
        if seq == 5:
            ability_key_2 = ROLE_ABILITIES_2.get(role_name)
            if ability_key_2:
                block += format_ability(ability_key_2)

        lines.append(block)
    return "\n".join(lines)


class PathwayAbilitySelectView(discord.ui.View):
    def __init__(self, guild=None):
        super().__init__(timeout=60)

        options = [
            discord.SelectOption(
                label=p,
                value=p,
                emoji=get_pathway_emoji(guild, p) if guild else None
            )
            for p in PATHWAY_LIST
        ]
        select = discord.ui.Select(
            placeholder="Choose a pathway…",
            options=options,
            custom_id="pathway_ability_select"
        )
        select.callback = self._make_callback()
        self.add_item(select)

    async def on_error(self, interaction: discord.Interaction, error: Exception, item) -> None:
        await _view_on_error(interaction, error, item)

    def _make_callback(self):
        async def callback(interaction: discord.Interaction):
            pathway = interaction.data["values"][0]
            text = get_pathway_abilities_text(pathway)
            color = pathway_colors.get(pathway, 0x9B59B6)
            embed = discord.Embed(description=text, color=color)
            embed.set_footer(text="Abilities for Seq 9, 8, 7, 6, and 5.")
            await interaction.response.send_message(embed=embed, ephemeral=True)
        return callback


@bot.tree.command(name="pathway_ability", description="See what ability each sequence of a pathway has.")
async def pathway_ability(interaction: discord.Interaction):
    embed = discord.Embed(
        title="Pathway Ability Lookup",
        description="Select a pathway below to see its Seq 9 through 5 abilities.",
        color=0x9B59B6
    )
    await interaction.response.send_message(embed=embed, view=PathwayAbilitySelectView(interaction.guild), ephemeral=True)


# ── Pathway flavour for /stats ─────────────────────────────
# Describes each pathway's combat identity shown in /stats
PATHWAY_FLAVOUR = {
    "Fool":            "A wildcard of fate — balanced across all stats, unpredictable in battle.",
    "Door":            "Masters of space and movement. Exceptional Speed lets them strike first.",
    "Error":           "Elusive swindlers who slip through gaps. High Speed, tricky to pin down.",
    "Hermit":          "Scholars who blend intellect with spirituality. High Spirituality and SP efficiency.",
    "Paragon":         "Gifted with extraordinary Spirit — they resist debuffs better than anyone.",
    "Red Priest":      "Aggressive hunters. High Attack and Speed make them dangerous openers.",
    "Demoness":        "Silent assassins. They move fast and strike vital points without warning.",
    "Abyss":           "Pure, unapologetic offence. The highest Attack of any Seq 9 Pathway.",
    "Chained":         "Resilient brawlers. High HP means they absorb punishment and keep swinging.",
    "Twilight Giant":  "Combat-born titans. Enormous HP and strong Attack — built to outlast foes.",
    "Darkness":        "Night-walkers with strong spirituality, lethal in prolonged fights.",
    "Death":           "Durable and unsettling. High HP and eerie SP abilities wear opponents down.",
    "Tyrant":          "Raw brute force. High Attack stat — they hit hard from the opening blow.",
    "Sun":             "A rare jack-of-all-trades with bonuses across every stat. Brilliant and bold.",
    "Hanged Man":      "Secretive seers. Exceptional Spirituality fuels powerful SP-based abilities.",
    "White Tower":     "The sharpest minds on any Pathway. Unmatched Spirit and debuff resist.",
    "Visionary":       "Perceptive and cerebral. High Spirit lets them read and counter enemy moves.",
    "Mother":          "Enduring nurturers. The highest base HP — difficult to bring down.",
    "Moon":            "Adaptable and lucky. Decent Luck stat with surprising combat flexibility.",
    "Wheel of Fortune":"Fortune's favourite. The highest Luck stat — probability attacks love them.",
    "Black Emperor":   "Sharp commanders with high Spirit. They control the pace of any fight.",
    "Justiciar":       "Enforcers with strong Attack and HP. They deal justice swiftly and firmly.",
}

PATHWAY_COLOURS = {
    "Fool": 0x9B59B6, "Door": 0x3498DB, "Error": 0x2ECC71,
    "Hermit": 0x1ABC9C, "Paragon": 0xF1C40F, "Red Priest": 0xE74C3C,
    "Demoness": 0xE91E8C, "Abyss": 0x2C3E50, "Chained": 0x795548,
    "Twilight Giant": 0xF39C12, "Darkness": 0x4A148C, "Death": 0x607D8B,
    "Tyrant": 0xB71C1C, "Sun": 0xFFD600, "Hanged Man": 0x37474F,
    "White Tower": 0xECEFF1, "Visionary": 0x0288D1, "Mother": 0x43A047,
    "Moon": 0xB0BEC5, "Wheel of Fortune": 0xFFA000, "Black Emperor": 0x212121,
    "Justiciar": 0x1565C0,
}

STAT_DISPLAY_NAMES = {
    "health":       "❤️ Health",
    "attack":       "🗡️ Attack",
    "luck":         "🍀 Luck",
    "spirituality": "✨ Spirituality",
    "speed":        "⚡ Speed",
}

BAR_STAT_MAX = 15  # nominal cap for combat-stat bar visualization

def build_stats_embed(target: discord.Member) -> discord.Embed:
    role_name = get_user_role(target)
    pathway   = get_pathway_name(role_name) if role_name else None
    seq       = get_seq_number(role_name)   if role_name else 9

    # Resolve title (e.g. "Seer", "Clown"…)
    title_name = ""
    if role_name and "—" in role_name:
        title_name = role_name.split("—")[1].strip()

    raw = get_or_create_stats(target.id, target)
    stats = get_effective_stats(target.id, target)
    points = raw.get("points", 0)

    # Compute actual HP/SP using same formula as Fighter
    base = (
        PATHWAY_BASE_STATS.get((pathway, seq))
        or SEQ_BASE_STATS.get(seq, SEQ_BASE_STATS[9])
    ) if pathway else SEQ_BASE_STATS.get(seq, SEQ_BASE_STATS[9])
    max_hp = base["hp"] + stats["health"] * 6
    max_sp = base["sp"] + stats["spirituality"] * 2

    colour = PATHWAY_COLOURS.get(pathway, 0x9B59B6)
    flavour = PATHWAY_FLAVOUR.get(pathway, "A Beyonder of unknown origin.")

    if role_name and pathway and title_name:
        header = f"[{pathway}] Seq {seq} — {title_name}"
    elif role_name:
        header = role_name
    else:
        header = "No Pathway role detected"

    embed = discord.Embed(
        title=f"𓂃 {target.display_name}'s Stats",
        description=f"*{header}*\n{flavour}",
        color=colour
    )
    base_hp = base["hp"]
    base_sp = base["sp"]
    hp_from_stats = stats["health"] * 6
    sp_from_stats = stats["spirituality"] * 2
    embed.add_field(
        name="ᯓ★ Vitals",
        value=(
            f"❤️ **HP:** {max_hp} *(base {base_hp} + {hp_from_stats} from stats)*\n"
            f"✨ **SP:** {max_sp} *(base {base_sp} + {sp_from_stats} from stats)*"
        ),
        inline=True
    )
    embed.add_field(
        name="🔸 Unspent Points",
        value=f"**{points}** available\n*(unlocked by advancing Sequence)*",
        inline=True
    )
    embed.add_field(
        name="ᯓ★ Combat Stats",
        value=(
            f"🗡️ **Attack** {stat_bar(stats['attack'], BAR_STAT_MAX)} `{stats['attack']}`\n"
            f"⚡ **Speed** {stat_bar(stats['speed'], BAR_STAT_MAX)} `{stats['speed']}`\n"
            f"🍀 **Luck** {stat_bar(stats['luck'], BAR_STAT_MAX)} `{stats['luck']}`\n"
            f"❤️ **Health** {stat_bar(stats['health'], BAR_STAT_MAX)} `{stats['health']}`\n"
            f"✨ **Spriti** {stat_bar(stats['spirituality'], BAR_STAT_MAX)} `{stats['spirituality']}`"
        ),
        inline=False
    )
    bal = get_pounds(target.id, guild_id=0)
    s_eco = get_economy(target.id, guild_id=0)
    wins = s_eco.get("wins", 0); losses = s_eco.get("losses", 0); streak = s_eco.get("win_streak", 0)
    total = wins + losses; winrate = int((wins / total) * 100) if total > 0 else 0
    streak_icon = "🔥" if streak >= 2 else "✨" if streak == 1 else "💔"
    embed.add_field(name="💰 Soli", value=f"**{bal:,}** soli", inline=True)
    embed.add_field(name="⚔️ Record", value=f"**{wins}W / {losses}L** ({winrate}%)\n{streak_icon} Streak: **{streak}**", inline=True)
    embed.set_thumbnail(url=target.display_avatar.url)
    embed.set_footer(text="𓂃 Base stats come from your Pathway — bonus points unlock as you advance Sequence. See /guide for stat details.")
    return embed


STAT_LABELS = {
    "health": ("Health", "❤️"),
    "attack": ("Attack", "🗡️"),
    "luck": ("Luck", "🍀"),
    "spirituality": ("Spirituality", "✨"),
    "speed": ("Speed", "⚡"),
}


class StatAllocateModal(discord.ui.Modal):
    def __init__(self, user_id: int, stat_key: str, available: int):
        label, emoji = STAT_LABELS[stat_key]
        super().__init__(title=f"Allocate {label} points")
        self.user_id = user_id
        self.stat_key = stat_key
        self.amount_input = discord.ui.TextInput(
            label=f"How many points? (1-{available})",
            placeholder=f"Enter a number up to {available}",
            default=str(available),
            required=True,
            max_length=6
        )
        self.add_item(self.amount_input)

    async def on_submit(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message("❌ Not your stat panel!", ephemeral=True)

        record = get_or_create_stats(self.user_id, interaction.user)
        available = record.get("points", 0)
        if available < 1:
            return await interaction.response.send_message("❌ You have no unspent stat points.", ephemeral=True)

        raw = self.amount_input.value.strip()
        if not raw.isdigit() or int(raw) < 1:
            return await interaction.response.send_message("❌ Enter a whole number of 1 or more.", ephemeral=True)

        amount = int(raw)
        clamp_note = ""
        if amount > available:
            clamp_note = f" *(you asked for {amount}, but only had {available} — allocated all of it)*"
            amount = available

        record[self.stat_key] += amount
        record["points"] -= amount
        save_stats()

        label, emoji = STAT_LABELS[self.stat_key]
        new_embed = build_stats_embed(interaction.user)
        new_view = StatsView(self.user_id) if record["points"] > 0 else None
        await interaction.response.edit_message(embed=new_embed, view=new_view)
        await interaction.followup.send(f"{emoji} **+{amount} {label}**!{clamp_note}", ephemeral=True)


class StatAllocateSelect(discord.ui.Select):
    def __init__(self, user_id: int):
        self.user_id = user_id
        options = [
            discord.SelectOption(label="Health", emoji="❤️", value="health", description="Increases max HP"),
            discord.SelectOption(label="Attack", emoji="🗡️", value="attack", description="Increases Hit/Special damage"),
            discord.SelectOption(label="Luck", emoji="🍀", value="luck", description="Improves crit & dodge odds"),
            discord.SelectOption(label="Spirituality", emoji="✨", value="spirituality", description="Increases SP, damage & debuff resist"),
            discord.SelectOption(label="Speed", emoji="⚡", value="speed", description="Improves turn order & evasion"),
        ]
        super().__init__(placeholder="🔧 Allocate a stat point...", options=options, min_values=1, max_values=1)

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message("❌ Not your stat panel!", ephemeral=True)

        record = get_or_create_stats(self.user_id, interaction.user)
        available = record.get("points", 0)
        if available < 1:
            return await interaction.response.send_message("❌ You have no unspent stat points.", ephemeral=True)

        stat_key = self.values[0]
        await interaction.response.send_modal(StatAllocateModal(self.user_id, stat_key, available))


class StatsView(discord.ui.View):
    def __init__(self, user_id: int):
        super().__init__(timeout=120)
        self.add_item(StatAllocateSelect(user_id))


@bot.tree.command(name="stats_user", description="View your (or another Beyonder's) combat stats.")
@app_commands.describe(user="The user to check. Leave blank for your own stats.")
async def stats_user(interaction: discord.Interaction, user: discord.Member = None):
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


@bot.tree.command(name="log_apostle", description="View recent battle results.")
async def log_apostle(interaction: discord.Interaction):
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


@bot.tree.command(name="assign_role", description="Give or remove a role from a member.")
@app_commands.describe(member="The member to give the role to.", role="The role to assign or remove.")
async def role_cmd(interaction: discord.Interaction, member: discord.Member, role: discord.Role):
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


@bot.tree.command(name="set_battle_log_channel", description="Set or clear this channel as the battle results log channel.")
async def set_battle_log_channel(interaction: discord.Interaction):
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

@bot.tree.command(name="winstreak", description="Check your battle record and win streak.")
@app_commands.describe(user="The user to check. Leave blank for your own.")
async def winstreak_cmd(interaction: discord.Interaction, user: discord.Member = None):
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


# ====================== PVP XP BRIDGE ======================
# XP awarded in lotm_beyonders.db when winning a PvP fight
SEQ_WIN_XP = {
    9: 150,
    8: 250,
    7: 400,
    6: 600,
    5: 900,
    4: 1400,
    3: 2200,
    2: 3500,
    1: 5000,
    0: 8000,
}

# XP lost by the LOSER — roughly 40% of what a win gives at that seq level
SEQ_LOSS_XP = {
    9: 60,
    8: 100,
    7: 160,
    6: 240,
    5: 360,
    4: 560,
    3: 880,
    2: 1400,
    1: 2000,
    0: 3200,
}

async def award_combat_xp(winner: discord.Member, loser_seq: int, guild: discord.Guild):
    """Award LOTM progression XP to the PvP winner in lotm_beyonders.db."""
    if not guild:
        return
    xp_gain = SEQ_WIN_XP.get(loser_seq, 150)
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute(
                "SELECT pathway, sequence, xp FROM users WHERE user_id = ? AND guild_id = ?",
                (winner.id, guild.id)
            ) as cursor:
                row = await cursor.fetchone()

            if not row or not row[0]:
                return  # Winner has no LOTM pathway — skip silently

            pathway, seq, current_xp = row[0], row[1], row[2]
            if seq <= 0:
                return  # Already True God — nothing to gain

            new_xp = current_xp + xp_gain
            leveled = False
            new_seq = seq

            while new_seq > 0:
                req = get_xp_required(new_seq)
                if new_xp >= req:
                    new_xp -= req
                    new_seq -= 1
                    leveled = True
                else:
                    break

            await db.execute(
                "UPDATE users SET sequence = ?, xp = ? WHERE user_id = ? AND guild_id = ?",
                (new_seq, new_xp, winner.id, guild.id)
            )
            await db.commit()

        # Assign new role if leveled up
        if leveled:
            try:
                member = guild.get_member(winner.id) or await guild.fetch_member(winner.id)
                if member:
                    await assign_sequence_role(member, pathway, new_seq)
            except Exception:
                pass
            await announce_sequence_advance(guild, winner.id, pathway, new_seq)
        # No announcement for regular XP gain — announcement channel is for promotions/demotions only

        await log_event(
            event_type="PVP_XP_AWARD",
            details=f"Awarded **+{xp_gain} XP** for defeating Seq {loser_seq} opponent (Leveled: {leveled})",
            user_id=winner.id,
            username=str(winner),
            guild=guild
        )

    except Exception as e:
        print(f"[XP Bridge] Error awarding combat XP: {e}")

async def penalize_combat_xp(loser: discord.Member, loser_seq: int, guild: discord.Guild):
    """Deduct LOTM progression XP from the PvP loser."""
    if not guild:
        return
    xp_loss = SEQ_LOSS_XP.get(loser_seq, 60)
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute(
                "SELECT pathway, sequence, xp FROM users WHERE user_id = ? AND guild_id = ?",
                (loser.id, guild.id)
            ) as cursor:
                row = await cursor.fetchone()

            if not row or not row[0]:
                return

            pathway, seq, current_xp = row[0], row[1], row[2]
            if seq >= 9 and current_xp <= 0:
                return  # Already at absolute floor (Seq 9, 0 XP) — nothing to deduct

            new_xp = current_xp - xp_loss
            new_seq = seq
            demoted = False

            while new_xp < 0 and new_seq < 9:
                new_seq += 1
                new_xp = get_xp_required(new_seq) + new_xp
                demoted = True

            new_xp = max(0, new_xp)
            if new_seq >= 9:
                new_seq = 9

            await db.execute(
                "UPDATE users SET sequence = ?, xp = ? WHERE user_id = ? AND guild_id = ?",
                (new_seq, new_xp, loser.id, guild.id)
            )
            await db.commit()

        if demoted:
            try:
                member = guild.get_member(loser.id) or await guild.fetch_member(loser.id)
                if member:
                    await assign_sequence_role(member, pathway, new_seq)
            except Exception:
                pass
            try:
                channel_id = await get_setting("announcement_channel")
                if channel_id:
                    channel = guild.get_channel(int(channel_id))
                    if channel:
                        seq_name = get_sequence_name(pathway, new_seq)
                        color = pathway_colors.get(pathway, 0xf5c400)
                        embed = discord.Embed(
                            title="💀 Sequence Demotion!",
                            description=(
                                f"{loser.mention} was defeated and demoted to "
                                f"**Sequence {new_seq} — {seq_name}** in the **{pathway} Pathway**!\n\n"
                                f"*Defeat has its consequences...*"
                            ),
                            color=color
                        )
                        embed.set_footer(text="Lord of the Mysteries — Beyonder Tracker")
                        await channel.send(content=loser.mention, embed=embed)
            except Exception as e:
                print(f"[XP Bridge] Demotion announcement error: {e}")
        # No announcement for regular XP loss — announcement channel is for promotions/demotions only

        await log_event(
            event_type="PVP_XP_LOSS",
            details=f"Lost **-{xp_loss} XP** after defeat at Seq {loser_seq} (Demoted: {demoted})",
            user_id=loser.id,
            username=str(loser),
            guild=guild
        )

    except Exception as e:
        print(f"[XP Bridge] Error penalizing combat XP: {e}")

# ====================== MERGED ON_READY + RUN ======================
# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║          EXTENSION — Chairs, Items, Pirate Events                          ║
# ╚══════════════════════════════════════════════════════════════════════════════╝

# ── Extra DB tables ───────────────────────────────────────────────────────────
async def _init_extension_tables():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""CREATE TABLE IF NOT EXISTS player_items (
            user_id     INTEGER,
            guild_id    INTEGER,
            item_name   TEXT,
            quantity    INTEGER DEFAULT 0,
            PRIMARY KEY (user_id, guild_id, item_name)
        )""")
        await db.execute("""CREATE TABLE IF NOT EXISTS chair_shop (
            user_id         INTEGER,
            guild_id        INTEGER,
            last_buy        TEXT,
            bonus_chair     TEXT,
            bonus_expires   TEXT,
            PRIMARY KEY (user_id, guild_id)
        )""")
        await db.execute("""CREATE TABLE IF NOT EXISTS event_channels (
            guild_id    INTEGER PRIMARY KEY,
            channel_id  INTEGER
        )""")
        await db.commit()

# ── Item helpers ──────────────────────────────────────────────────────────────

async def db_add_item(user_id: int, guild_id: int, item_name: str, qty: int = 1):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO player_items(user_id,guild_id,item_name,quantity) VALUES(?,?,?,?) "
            "ON CONFLICT(user_id,guild_id,item_name) DO UPDATE SET quantity=quantity+?",
            (user_id, guild_id, item_name, qty, qty)
        )
        await db.commit()

async def db_remove_item(user_id: int, guild_id: int, item_name: str, qty: int = 1) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT quantity FROM player_items WHERE user_id=? AND guild_id=? AND item_name=?",
            (user_id, guild_id, item_name)
        ) as cur:
            row = await cur.fetchone()
        if not row or row[0] < qty:
            return False
        new_qty = row[0] - qty
        if new_qty <= 0:
            await db.execute(
                "DELETE FROM player_items WHERE user_id=? AND guild_id=? AND item_name=?",
                (user_id, guild_id, item_name)
            )
        else:
            await db.execute(
                "UPDATE player_items SET quantity=? WHERE user_id=? AND guild_id=? AND item_name=?",
                (new_qty, user_id, guild_id, item_name)
            )
        await db.commit()
    return True

async def db_get_items(user_id: int, guild_id: int) -> dict:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT item_name, quantity FROM player_items WHERE user_id=? AND guild_id=? AND quantity>0",
            (user_id, guild_id)
        ) as cur:
            rows = await cur.fetchall()
    return {r[0]: r[1] for r in rows}

async def db_has_item(user_id: int, guild_id: int, item_name: str, qty: int = 1) -> bool:
    items = await db_get_items(user_id, guild_id)
    return items.get(item_name, 0) >= qty


# ── Chair item constants (for legacy chair items already in inventories) ─────
CHAIR_XP    = 1000
CHAIR_USE_TEXTS = [
    "{user} sat on the chair and pondered on life.\n\n*The wood creaked softly beneath them. Somewhere between one breath and the next, the mysteries of the universe felt just a little less mysterious.*",
    "{user} settled into the chair and stared at nothing in particular.\n\n*The world kept moving. The chair did not. For a moment, that felt like wisdom.*",
    "{user} sat. Thought. Sat some more.\n\n*Great philosophers have paced. Great Beyonders, it turns out, prefer to sit. The XP would suggest they were right.*",
]
ALL_CHAIR_NAMES = {
    "Foolish Chair","Speedy Chair","Mischievous Chair","Holy Chair","Underwater Chair",
    "Clever Chair","Bloody Chair","Invisible Chair","Destructive Chair","Curvy Chair",
    "Rotten Chair","Black Chair","Dead Chair","Mechanical Chair","Mystical Chair",
    "Golden Chair","Balancing Chair","Throne Chair","Restrained Chair","Electro Chair",
    "Caring Chair","Luminous Chair","Healthy Chair",
}

# ── Item use view ─────────────────────────────────────────────────────────────


class ItemUseView(discord.ui.View):
    def __init__(self, user_id: int, guild_id: int, item_name: str):
        super().__init__(timeout=30)
        self.user_id   = user_id
        self.guild_id  = guild_id
        self.item_name = item_name
        btn = discord.ui.Button(label=f"Use {item_name[:40]}", style=discord.ButtonStyle.success)
        btn.callback = self._use_callback
        self.add_item(btn)

    async def _use_callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message("Not yours!", ephemeral=True)
        gid  = interaction.guild_id or 0
        name = self.item_name
        if name in ALL_CHAIR_NAMES:
            removed = await db_remove_item(self.user_id, gid, name)
            if not removed:
                return await interaction.response.send_message("You don't have that item anymore!", ephemeral=True)
            data = await get_user_data(self.user_id, gid)
            if not data or not data.get("pathway"):
                return await interaction.response.send_message("You need a pathway to gain XP!", ephemeral=True)
            new_seq, new_xp, leveled = await apply_xp_gain(
                self.user_id, interaction.guild, data["pathway"],
                data["sequence"], data["xp"], CHAIR_XP,
                interaction.user.mention,
            )
            flavour = random.choice(CHAIR_USE_TEXTS).format(user=f"**{interaction.user.display_name}**")
            level_line = f"\n\n Sequence advanced! You are now **Seq {new_seq}**!" if leveled else ""
            embed = discord.Embed(
                title=f"🪑 {name}",
                description=f"{flavour}\n\n+{CHAIR_XP:,} XP gained!{level_line}",
                color=0xF1C40F,
            )
            await interaction.response.edit_message(embed=embed, view=None)
            self.stop()
            return
        if name == "Healthy Chair":
            await interaction.response.send_message(
                "🪑 **Healthy Chair** can only be used during a Pirate Hunt event to revive a fallen ally.",
                ephemeral=True)
            return
        await interaction.response.send_message(
            f"*You hold the **{name}** carefully.* It has no use effect yet — more functionality coming soon!",
            ephemeral=True)
        self.stop()


class ItemUseButton(discord.ui.Button):
    def __init__(self, owner_id: int, item_name: str, idx: int):
        self.owner_id  = owner_id
        self.item_name = item_name
        super().__init__(
            label=f"Use {item_name[:35]}",
            style=discord.ButtonStyle.primary,
        )

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.owner_id:
            return await interaction.response.send_message("Not yours!", ephemeral=True)
        await interaction.response.send_message(
            f"Use **{self.item_name}**?",
            view=ItemUseView(interaction.user.id, interaction.guild_id or 0, self.item_name),
            ephemeral=True,
        )


@bot.tree.command(name="item", description="View and use items from your inventory.")
async def item_cmd(interaction: discord.Interaction):
    gid   = interaction.guild_id or 0
    items = await db_get_items(interaction.user.id, gid)
    if not items:
        return await interaction.response.send_message("Your inventory is empty.", ephemeral=True)
    lines = [f"**{n}** x {q}" for n, q in sorted(items.items())]
    embed = discord.Embed(
        title=f"🎒 {interaction.user.display_name}'s Inventory",
        description="\n".join(lines),
        color=0xF39C12,
    )
    embed.set_footer(text="Select an item below to use it.")

    class ItemView(discord.ui.View):
        async def on_error(self, inter, error, item):
            await _view_on_error(inter, error, item)

    view = ItemView(timeout=60)
    for idx, name in enumerate(sorted(items.keys())[:5]):
        view.add_item(ItemUseButton(interaction.user.id, name, idx))
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

# ── /give_item command (admin) ────────────────────────────────────────────────

@bot.tree.command(name="give_item", description="Give any item to a player. (Admin only)")
@app_commands.describe(
    user="The recipient.",
    item="Exact item name.",
    quantity="How many to give (default 1).")
async def give_item(interaction: discord.Interaction, user: discord.Member,
                    item: str, quantity: int = 1):
    if not await _require_bot_admin(interaction):
        return
    if quantity <= 0:
        return await interaction.response.send_message("Quantity must be positive.", ephemeral=True)
    gid = interaction.guild_id or 0
    await db_add_item(user.id, gid, item, quantity)
    await interaction.response.send_message(
        f"Gave **{quantity}x {item}** to **{user.display_name}**.", ephemeral=True)

# ── /transfer_data command (owner only) ──────────────────────────────────────
# Copies ALL data from source guild 1452586968486514842
#                  → destination guild 1514229914050498660
# Covers: users, player_items, chair_shop (SQLite) + economy JSON

_TRANSFER_SRC_GUILD = 1452586968486514842
_TRANSFER_DST_GUILD = 1514229914050498660

@bot.tree.command(name="transfer_data", description="Owner only: Copy all data from the source server to the destination server.")
async def transfer_data(interaction: discord.Interaction):
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

class SpellBuyButton(discord.ui.Button):
    """A single Buy button for one purchasable spell."""
    def __init__(self, user_id: int, guild_id: int, key: str, ab: dict, owned: bool):
        self.user_id  = user_id
        self.guild_id = guild_id
        self.key      = key
        self.ab       = ab
        label = f"{'✅' if owned else '🛒'} {ab['name'][:40]}"
        super().__init__(
            label=label,
            style=discord.ButtonStyle.secondary if owned else discord.ButtonStyle.primary,
            disabled=owned,
            row=0,
        )

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message("Not yours!", ephemeral=True)
        bal = get_pounds(self.user_id, guild_id=self.guild_id)
        price = self.ab["price"]
        if bal < price:
            return await interaction.response.send_message(
                f"❌ Need **{price:,} soli** — you only have **{bal:,}**.", ephemeral=True)
        if owns_spell(self.user_id, self.key, guild_id=self.guild_id):
            return await interaction.response.send_message(
                f"Already own **{self.ab['name']}**!", ephemeral=True)
        remove_pounds(self.user_id, price, guild_id=self.guild_id)
        buy_spell(self.user_id, self.key, guild_id=self.guild_id)
        await interaction.response.send_message(
            f"✅ Purchased **{self.ab['name']}** for **{price:,} soli**!\n"
            f"> {self.ab['description']}\n\nUse it in any battle via the **Ability** button.",
            ephemeral=True)


class ShopNavButton(discord.ui.Button):
    def __init__(self, user_id, guild_id, page, label, disabled):
        self.user_id  = user_id
        self.guild_id = guild_id
        self.target   = page
        super().__init__(label=label, style=discord.ButtonStyle.secondary,
                         disabled=disabled, row=4)

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message("Not yours!", ephemeral=True)
        await interaction.response.edit_message(**_build_shop_payload(self.user_id, self.guild_id, self.target))


class SpellShopView(discord.ui.View):
    def __init__(self, user_id: int, guild_id: int, page: int = 0):
        super().__init__(timeout=120)
        self.user_id  = user_id
        self.guild_id = guild_id
        self.page     = page
        self._build(page)

    def _build(self, page: int):
        purchasable = [(k, v) for k, v in ABILITIES.items() if v.get("purchasable")]
        total_pages = max(1, (len(purchasable) + 4) // 5)
        page_items  = purchasable[page*5:(page+1)*5]
        for row_idx, (key, ab) in enumerate(page_items):
            owned = owns_spell(self.user_id, key, guild_id=self.guild_id)
            btn   = SpellBuyButton(self.user_id, self.guild_id, key, ab, owned)
            btn.row = row_idx
            self.add_item(btn)
        if total_pages > 1:
            self.add_item(ShopNavButton(self.user_id, self.guild_id, page - 1, "◀ Prev", page == 0))
            self.add_item(ShopNavButton(self.user_id, self.guild_id, page + 1, "Next ▶", page >= total_pages - 1))

    async def on_error(self, interaction, error, item):
        await _view_on_error(interaction, error, item)


def _build_shop_payload(user_id: int, guild_id: int, page: int = 0) -> dict:
    purchasable = [(k, v) for k, v in ABILITIES.items() if v.get("purchasable")]
    total_pages = max(1, (len(purchasable) + 4) // 5)
    page = max(0, min(page, total_pages - 1))
    page_items = purchasable[page*5:(page+1)*5]
    bal = get_pounds(user_id, guild_id=guild_id)
    lines = [f"**Balance:** {bal:,} soli\n"]
    for key, ab in page_items:
        owned = owns_spell(user_id, key, guild_id=guild_id)
        status = "✅ Owned" if owned else f"{ab['price']:,} soli"
        lines.append(f"**{ab['name']}** — {status}\n> {ab['description']}")
    embed = discord.Embed(
        title="🛒 LOTM Spell Shop",
        description="\n\n".join(lines),
        color=0x9B59B6,
    )
    embed.set_footer(text=f"Page {page+1}/{total_pages} — Press a button below to purchase")
    return {"embed": embed, "view": SpellShopView(user_id, guild_id, page)}


@bot.tree.command(name="shop", description="Browse and buy combat abilities with soli.")
async def shop(interaction: discord.Interaction):
    uid = interaction.user.id
    gid = interaction.guild_id or 0
    payload = _build_shop_payload(uid, gid, 0)
    await interaction.response.send_message(**payload, ephemeral=True)


@bot.event
async def on_ready():
    await init_db()
    load_all()
    bot.add_view(PathwaySelectView())
    asyncio.create_task(_auto_save_loop())
    print(f"✅ {bot.user} — LOTM Bot loaded!")
    asyncio.create_task(start_http_server())
    try:
        synced = await bot.tree.sync()
        print(f"🔄 Synced {len(synced)} slash commands")
    except Exception as e:
        print(f"Sync error: {e}")


async def main():
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        print("❌ ERROR: DISCORD_TOKEN not set in haha.env!")
        return
    print("Starting Combined LOTM Bot...")
    await bot.start(token)

if __name__ == "__main__":
    asyncio.run(main())
