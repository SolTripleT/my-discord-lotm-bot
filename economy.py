# ============================================================
# ECONOMY — soli (pounds) balances, owned spells, the per-guild bounty
# board, and the short gambling cooldown shared by coinflip/slots/blackjack.
#
# Balances/spells live in `persistence.store` (player_economy) so the
# JSON-backed data is loaded/saved from one place; this module is the
# domain-specific API on top of it (get/add/remove pounds, spells, bounties).
# ============================================================
import time

from persistence import player_economy, save_economy

GAMBLE_COOLDOWN = 5  # seconds
BOUNTY_MIN = 5_000
BOUNTY_MAX = 500_000


class EconomyManager:
    def __init__(self):
        self.gambling_cooldowns = {}  # user_id -> last gambling timestamp

    def check_gamble_cooldown(self, user_id: int) -> float:
        """Returns seconds remaining on cooldown, or 0 if ready."""
        import time
        last = self.gambling_cooldowns.get(user_id, 0)
        elapsed = time.time() - last
        return max(0.0, GAMBLE_COOLDOWN - elapsed)

    def set_gamble_cooldown(self, user_id: int):
        import time
        self.gambling_cooldowns[user_id] = time.time()

    def _eco_key(self, guild_id, user_id):
        """Composite key for per-server economy storage."""
        return f"{guild_id}:{user_id}"

    def get_economy(self, user_id, guild_id=0):
        key = self._eco_key(guild_id, user_id)
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

    def get_pounds(self, user_id, guild_id=0):
        return self.get_economy(user_id, guild_id)["pounds"]

    def add_pounds(self, user_id, amount, guild_id=0):
        self.get_economy(user_id, guild_id)["pounds"] += amount
        save_economy()

    def remove_pounds(self, user_id, amount, guild_id=0):
        self.get_economy(user_id, guild_id)["pounds"] = max(0, self.get_economy(user_id, guild_id)["pounds"] - amount)
        save_economy()

    def set_pounds(self, user_id, amount, guild_id=0):
        self.get_economy(user_id, guild_id)["pounds"] = max(0, amount)
        save_economy()

    def wipe_pounds(self, user_id, guild_id=0):
        self.get_economy(user_id, guild_id)["pounds"] = 0
        save_economy()

    def owns_spell(self, user_id, spell_key, guild_id=0):
        return spell_key in self.get_economy(user_id, guild_id)["spells"]

    def buy_spell(self, user_id, spell_key, guild_id=0):
        eco = self.get_economy(user_id, guild_id)
        if spell_key not in eco["spells"]:
            eco["spells"].append(spell_key)
            save_economy()

    def get_bounties(self, guild_id: int) -> dict:
        key = f"bounties_{guild_id}"
        eco = self.get_economy(key, guild_id=0)
        return eco.get("bounty_board", {})

    def set_bounties(self, guild_id: int, board: dict):
        key = f"bounties_{guild_id}"
        eco = self.get_economy(key, guild_id=0)
        eco["bounty_board"] = board
        # Save via player_economy
        player_economy[(key, 0)] = eco
        save_economy()

    def add_bounty(self, guild_id: int, target_id: int, placer_id: int, amount: int):
        board = self.get_bounties(guild_id)
        tid = str(target_id)
        if tid in board:
            board[tid]["amount"] += amount
        else:
            board[tid] = {"amount": amount, "placed_by": placer_id}
        self.set_bounties(guild_id, board)

    def claim_bounty(self, guild_id: int, target_id: int) -> int:
        board = self.get_bounties(guild_id)
        tid = str(target_id)
        if tid not in board:
            return 0
        amount = board.pop(tid)["amount"]
        self.set_bounties(guild_id, board)
        return amount



economy_manager = EconomyManager()

check_gamble_cooldown = economy_manager.check_gamble_cooldown
set_gamble_cooldown = economy_manager.set_gamble_cooldown
get_economy = economy_manager.get_economy
get_pounds = economy_manager.get_pounds
add_pounds = economy_manager.add_pounds
remove_pounds = economy_manager.remove_pounds
set_pounds = economy_manager.set_pounds
wipe_pounds = economy_manager.wipe_pounds
owns_spell = economy_manager.owns_spell
buy_spell = economy_manager.buy_spell
get_bounties = economy_manager.get_bounties
set_bounties = economy_manager.set_bounties
add_bounty = economy_manager.add_bounty
claim_bounty = economy_manager.claim_bounty
