# ============================================================
# BATTLE COMMANDS — starting a duel (/fight), betting on one in
# progress (/bet), and forfeiting (/leave).
# ============================================================
import random
import discord
from discord import app_commands
from discord.ext import commands

from logging_config import logger
from utils import check_blacklist
from roles import get_seq_number
from economy import get_pounds, add_pounds
from fighter import Fighter
from battle.manager import (
    battles, active_bets, get_battle, get_battle_key_for_user, get_fighters,
    swap_turn, resolve_bets, make_battle_embed, _make_battle_view, _battle_key,
    find_battle_by_players, record_battle,
)
from battle.views import ChallengeView, BetView

BET_MIN = 5_000
BET_MAX = 100_000


class BattleCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="fight", description="Challenge another warrior to a turn-based spiritual duel.")
    @app_commands.describe(opponent="The warrior you wish to challenge.")
    async def fight(self, interaction: discord.Interaction, opponent: discord.Member):
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


    @app_commands.command(name="bet", description="Bet on an ongoing fight within the first 3 turns.")
    @app_commands.describe(
        challenger="The person who issued the challenge.",
        opponent="The person who was challenged.",
        amount="How many soli to bet. (Min 5,000 — Max 100,000)"
    )
    async def bet(self, interaction: discord.Interaction, challenger: discord.Member, opponent: discord.Member, amount: int):
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

    @app_commands.command(name="leave", description="Forfeit your current duel.")
    async def leave(self, interaction: discord.Interaction):
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
                logger.debug("[leave] Could not delete old battle message", exc_info=True)

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





async def setup(bot):
    await bot.add_cog(BattleCommands(bot))
