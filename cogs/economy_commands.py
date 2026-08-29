# ============================================================
# ECONOMY COMMANDS — soli balance/transfer, gambling (coinflip, slots,
# blackjack), the bounty board, begging, item inventory, and the spell
# shop.
# ============================================================
import random
import discord
from discord import app_commands
from discord.ext import commands

from utils import check_blacklist
from economy import (
    get_pounds, add_pounds, remove_pounds, check_gamble_cooldown,
    set_gamble_cooldown, get_bounties, set_bounties, add_bounty, claim_bounty,
    BOUNTY_MIN, BOUNTY_MAX,
)
from persistence import beg_cooldowns, save_beg
from database import db_get_items, db_remove_item
from game_data import ALL_CHAIR_NAMES, CHAIR_USE_TEXTS, CHAIR_XP
from roles import get_sequence_name
from xp import apply_xp_gain
from database import get_user_data
from games import (
    CoinFlipView, resolve_coinflip, spin_slots, get_slot_result,
    make_slots_embed, make_deck, hand_value, make_bj_embed, BlackjackView,
    blackjack_games,
)
from battle.views import ItemUseButton, _build_shop_payload
from battle.manager import _view_on_error


class EconomyCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="balance", description="Check your pound balance — or someone else's.")
    @app_commands.describe(user="The user to check. Leave blank for your own.")
    async def balance_cmd(self, interaction: discord.Interaction, user: discord.Member = None):
        target = user or interaction.user
        bal = get_pounds(target.id, guild_id=interaction.guild_id)
        embed = discord.Embed(description=f"💰 **{target.display_name}** has **{bal:,} soli**.", color=0xF39C12)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="share", description="Send some of your soli to another user.")
    @app_commands.describe(user="The user to send money to.", amount="How much to send.")
    async def share(self, interaction: discord.Interaction, user: discord.Member, amount: int):
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

    @app_commands.command(name="coinflip", description="Flip a coin — bet soli on heads or tails.")
    @app_commands.describe(amount="How much to bet.")
    async def coinflip(self, interaction: discord.Interaction, amount: int):
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

    @app_commands.command(name="slots", description="Spin the slot machine!")
    @app_commands.describe(amount="How much to bet.")
    async def slots(self, interaction: discord.Interaction, amount: int):
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

    @app_commands.command(name="blackjack", description="Play a hand of blackjack against the dealer.")
    @app_commands.describe(amount="How much to bet.")
    async def blackjack_cmd(self, interaction: discord.Interaction, amount: int):
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


    @app_commands.command(name="bounty", description="Place or remove a bounty on another Beyonder.")
    @app_commands.describe(
        action="Place a new bounty or remove one you placed",
        target="The Beyonder the bounty is on.",
        amount="Soli to offer as reward. (Min 5,000 — Max 500,000, only used when placing)"
    )
    @app_commands.choices(action=[
        app_commands.Choice(name="Place", value="place"),
        app_commands.Choice(name="Remove", value="remove"),
    ])
    async def bounty_cmd(self, interaction: discord.Interaction, action: app_commands.Choice[str], target: discord.Member, amount: int = 0):
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


    @app_commands.command(name="bounties", description="View all active bounties on this server.")
    async def bounties_cmd(self, interaction: discord.Interaction):
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



    @app_commands.command(name="beg", description="Beg for soli. Returns 67 or 69 soli every 2 hours.")
    async def beg(self, interaction: discord.Interaction):
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



    @app_commands.command(name="item", description="View and use items from your inventory.")
    async def item_cmd(self, interaction: discord.Interaction):
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

    @app_commands.command(name="shop", description="Browse and buy combat abilities with soli.")
    async def shop(self, interaction: discord.Interaction):
        uid = interaction.user.id
        gid = interaction.guild_id or 0
        payload = _build_shop_payload(uid, gid, 0)
        await interaction.response.send_message(**payload, ephemeral=True)




async def setup(bot):
    await bot.add_cog(EconomyCommands(bot))
