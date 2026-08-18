# ============================================================
# XP — the Beyonder progression curve (chat XP + daily/pray bonuses)
# and the PvP XP bridge that feeds fight results back into
# lotm_beyonders.db (sequence advances/demotions, roles, announcements).
# ============================================================
import discord
import aiosqlite
import random

from config import DB_PATH, pathway_colors
from database import get_user_data, update_user, get_setting
from roles import assign_sequence_role, get_sequence_name
from utils import log_event
from logging_config import logger

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


class XPSystem:
    def get_base_xp_gain(self, current_seq: int) -> int:
        if current_seq <= 0:
            return 0
        base = random.randint(12, 28)
        multiplier = max(0.35, (current_seq / 9.0) ** 0.78)
        return max(5, int(base * multiplier))

    def get_xp_required(self, current_seq: int) -> int:
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
    async def announce_sequence_advance(self, guild: discord.Guild, user_id: int, pathway: str, new_seq: int):
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

    async def apply_xp_gain(self, user_id: int, guild: discord.Guild, pathway: str, current_seq: int, current_xp: int, gained_xp: int, mention: str):
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
            req = self.get_xp_required(seq)
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
            await self.announce_sequence_advance(guild, user_id, pathway, seq)
        else:
            await update_user(user_id, guild_id=guild.id, xp=xp)

        return seq, xp, leveled

    # ====================== DEMOTION HELPER ======================
    async def handle_xp_removal(self, guild: discord.Guild, user_id: int, data: dict, amount: int):
        new_xp = data["xp"] - amount
        seq = data["sequence"]
        demoted = False

        while new_xp < 0 and seq < 9:
            seq += 1
            new_xp = self.get_xp_required(seq) + new_xp
            demoted = True

        if seq >= 9:
            seq = 9
            new_xp = max(0, new_xp)

        return seq, new_xp, demoted


    async def award_combat_xp(self, winner: discord.Member, loser_seq: int, guild: discord.Guild):
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
                    req = self.get_xp_required(new_seq)
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
                    logger.warning(f"[XP Bridge] Could not sync role for winner {winner.id}", exc_info=True)
                await self.announce_sequence_advance(guild, winner.id, pathway, new_seq)
            # No announcement for regular XP gain — announcement channel is for promotions/demotions only

            await log_event(
                event_type="PVP_XP_AWARD",
                details=f"Awarded **+{xp_gain} XP** for defeating Seq {loser_seq} opponent (Leveled: {leveled})",
                user_id=winner.id,
                username=str(winner),
                guild=guild
            )

        except Exception:
            logger.exception("[XP Bridge] Error awarding combat XP")

    async def penalize_combat_xp(self, loser: discord.Member, loser_seq: int, guild: discord.Guild):
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
                    new_xp = self.get_xp_required(new_seq) + new_xp
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
                    logger.warning(f"[XP Bridge] Could not sync role for loser {loser.id}", exc_info=True)
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
                except Exception:
                    logger.exception("[XP Bridge] Demotion announcement error")
            # No announcement for regular XP loss — announcement channel is for promotions/demotions only

            await log_event(
                event_type="PVP_XP_LOSS",
                details=f"Lost **-{xp_loss} XP** after defeat at Seq {loser_seq} (Demoted: {demoted})",
                user_id=loser.id,
                username=str(loser),
                guild=guild
            )

        except Exception:
            logger.exception("[XP Bridge] Error penalizing combat XP")


xp_system = XPSystem()

get_base_xp_gain = xp_system.get_base_xp_gain
get_xp_required = xp_system.get_xp_required
announce_sequence_advance = xp_system.announce_sequence_advance
apply_xp_gain = xp_system.apply_xp_gain
handle_xp_removal = xp_system.handle_xp_removal
award_combat_xp = xp_system.award_combat_xp
penalize_combat_xp = xp_system.penalize_combat_xp
