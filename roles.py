# ============================================================
# ROLES — Discord role management for the Beyonder pathway/sequence
# system: creating & assigning "[Pathway] Seq N — Name" roles, parsing
# pathway/sequence back out of a role's name, and resolving which
# pathway abilities a member currently has access to.
# ============================================================
import re
import discord
import aiosqlite

from config import DB_PATH, pathways, pathway_colors
from game_data import ROLE_MODIFIERS, ROLE_ABILITIES, ROLE_ABILITY_INFO, ABILITIES
from logging_config import logger


def get_sequence_name(pathway: str, seq_num: int) -> str:
    if pathway not in pathways or not (0 <= seq_num <= 9):
        return "Unknown"
    return pathways[pathway][9 - seq_num]

# ====================== ROLE MANAGEMENT ======================
async def get_or_create_role(guild: discord.Guild, name: str, pathway: str):
    role = discord.utils.get(guild.roles, name=name)
    if role:
        logger.info(f"[ROLE] Found existing role '{name}' in {guild.name}")
        return role
    color = pathway_colors.get(pathway, 0xf5c400)
    logger.info(f"[ROLE] Creating new role '{name}' in {guild.name}")
    try:
        r = await guild.create_role(
            name=name, color=discord.Color(color),
            mentionable=True, reason="LOTM Beyonder Sequence role"
        )
        logger.info(f"[ROLE] Created role '{name}' successfully")
        return r
    except discord.Forbidden:
        logger.warning(f"[ROLE] Forbidden to create role '{name}' in {guild.name} — bot needs Manage Roles")
        return None
    except Exception:
        logger.exception(f"[ROLE] Error creating role '{name}'")
        return None

def is_lotm_sequence_role_name(role_name: str) -> bool:
    return (" Seq " in role_name and role_name.startswith("[")) or "] True God" in role_name

async def remove_all_lotm_sequence_roles(member: discord.Member):
    to_remove = [r for r in member.roles if is_lotm_sequence_role_name(r.name)]
    if to_remove:
        try:
            await member.remove_roles(*to_remove)
        except discord.Forbidden:
            logger.warning(f"[ROLE] Forbidden — could not remove sequence roles from {member.id}")

async def assign_sequence_role(member: discord.Member, pathway: str, seq_num: int):
    seq_name = get_sequence_name(pathway, seq_num)
    if seq_num == 0:
        role_name = f"[{pathway}] True God — {seq_name}"
    else:
        role_name = f"[{pathway}] Seq {seq_num} — {seq_name}"

    logger.info(f"[ROLE] Assigning role '{role_name}' to {member.id}")
    role = await get_or_create_role(member.guild, role_name, pathway)
    if role is None:
        logger.warning(f"[ROLE] Could not get or create role '{role_name}' (Forbidden?)")
        return

    roles_to_remove = [
        r for r in member.roles
        if r.name.startswith(f"[{pathway}] Seq ") or r.name.startswith(f"[{pathway}]")
    ]
    try:
        if roles_to_remove:
            await member.remove_roles(*roles_to_remove)
        await member.add_roles(role)
        logger.info(f"[ROLE] Successfully assigned '{role_name}' to {member.id}")
    except discord.Forbidden:
        logger.warning(f"[ROLE] Forbidden — bot lacks permission to assign roles to {member.id}")
    except Exception:
        logger.exception(f"[ROLE] Error assigning role '{role_name}' to {member.id}")


# ====================== SEQUENCE / PATHWAY NAME PARSING ======================
def get_seq_number(role_name: str) -> int:
    """Extract sequence number from role name like '[Fool] Seq 9 — Seer'"""
    try:
        parts = role_name.split("Seq ")
        if len(parts) > 1:
            return int(parts[1].split(" ")[0].split("—")[0].strip())
    except Exception:
        logger.debug(f"[ROLE] Could not parse sequence number from role name '{role_name}'", exc_info=True)
    return 9

def get_pathway_name(role_name: str) -> str:
    """Extract pathway name from role name like '[Fool] Seq 9 — Seer' -> 'Fool'"""
    try:
        return role_name.split("[")[1].split("]")[0]
    except Exception:
        logger.debug(f"[ROLE] Could not parse pathway name from role name '{role_name}'", exc_info=True)
        return ""

# ====================== ROLE / ABILITY LOOKUP ======================
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
                logger.debug(f"[ROLE] Fuzzy role match failed for '{role_name}'", exc_info=True)
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
                    logger.debug(f"[ROLE] Fuzzy role match failed for '{role_name}'", exc_info=True)
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
        logger.exception(f"[ROLE] DB lookup failed for user={member.id} guild={guild_id}")
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

