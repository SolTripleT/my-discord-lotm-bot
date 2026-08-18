# ============================================================
# GAMES — coinflip, slots and blackjack: the pure game logic (deck/
# reel handling, payouts) plus their Discord UI (CoinFlipView,
# BlackjackView). `blackjack_games` holds each player's in-progress
# hand between button presses.
# ============================================================
import random
import discord

from logging_config import logger
from economy import add_pounds, get_pounds, remove_pounds
from battle.manager import safe_respond

blackjack_games = {}  # user_id -> in-progress blackjack hand

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
        logger.error(f"[Blackjack] {item!r}: {error}", exc_info=error)
        try:
            await interaction.response.send_message(f"❌ Error: `{error}`", ephemeral=True)
        except Exception:
            try:
                await interaction.followup.send(f"❌ Error: `{error}`", ephemeral=True)
            except Exception:
                logger.debug("[Blackjack] Could not deliver error message to user", exc_info=True)

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
                logger.debug("[Blackjack] _safe_edit fallback failed", exc_info=True)
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

