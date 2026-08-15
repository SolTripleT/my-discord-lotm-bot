# ============================================================
# PERSISTENCE — the small JSON-backed "database" for everything that
# isn't in SQLite: soli/spells, combat-stat allocations, daily/beg
# cooldown streaks, per-guild channel config and the bot-admin whitelist.
#
# All of this lives on one PersistentStore instance (`store`). Bare-name
# module-level aliases are exported below for the handful of call sites
# elsewhere in the codebase that historically referenced these as plain
# globals — they point at the exact same dict/set objects as `store`, so
# mutating one mutates the other. This is safe because every mutation here
# is in-place (.clear()/.update()/item assignment) rather than rebinding
# the name to a new object.
# ============================================================
import os
import json
import asyncio

from config import BASE_DIR


class PersistentStore:
    def __init__(self):
        self.player_economy = {}
        self.player_stats = {}
        self.daily_data = {}          # user_id -> {"last": timestamp, "streak": int}
        self.beg_cooldowns = {}       # user_id -> last beg timestamp
        self.allowed_channel = {}     # guild_id -> list of channel_ids (empty = unrestricted)
        self.log_channel = {}         # guild_id -> channel_id for battle result broadcasts
        self.bot_admin_whitelist = set()  # user_id -> can use Admin: commands even without Discord Administrator perm

        self.DB_ECONOMY = os.path.join(BASE_DIR, "economy.json")
        self.DB_STATS = os.path.join(BASE_DIR, "stats.json")
        self.DB_DAILY = os.path.join(BASE_DIR, "daily.json")
        self.DB_BEG = os.path.join(BASE_DIR, "beg.json")
        self.DB_CONFIG = os.path.join(BASE_DIR, "config.json")

    # ── raw file I/O ──────────────────────────────────────
    def _load(self, path: str) -> dict:
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

    def _save(self, path: str, data: dict):
        try:
            with open(path, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            print(f"[DB] Save error ({path}): {e}")

    # ── bulk load ─────────────────────────────────────────
    def load_all(self):
        raw_eco = self._load(self.DB_ECONOMY)
        self.player_economy.clear()
        for k, v in raw_eco.items():
            k = str(k)
            # Normalise key format — always "guild_id:user_id"
            if ":" not in k:
                # Old plain user_id key — store under guild 0 for backward compat
                self.player_economy[f"0:{k}"] = v
            else:
                self.player_economy[k] = v

        raw_stats = self._load(self.DB_STATS)
        # NOTE: originally rebound (`player_stats = {...}`). Converted to an
        # in-place clear+update so any other module holding a reference to
        # this same dict object stays in sync.
        self.player_stats.clear()
        self.player_stats.update({int(k): v for k, v in raw_stats.items()})

        raw_daily = self._load(self.DB_DAILY)
        self.daily_data.clear()
        self.daily_data.update({int(k): v for k, v in raw_daily.items()})

        raw_beg = self._load(self.DB_BEG)
        self.beg_cooldowns.clear()
        self.beg_cooldowns.update({int(k): v for k, v in raw_beg.items()})

        raw_config = self._load(self.DB_CONFIG)
        raw_ac = raw_config.get("allowed_channel", {})
        # NOTE: originally rebound (`allowed_channel = {}`). Converted to
        # in-place clear + per-item assignment for the same reason as above.
        self.allowed_channel.clear()
        for k, v in raw_ac.items():
            raw_list = v if isinstance(v, list) else [v]
            self.allowed_channel[int(k)] = [int(c) for c in raw_list]

        raw_lc = raw_config.get("log_channel", {})
        # NOTE: originally rebound (`log_channel = {...}`). Converted to
        # in-place clear+update for the same reason as above.
        self.log_channel.clear()
        self.log_channel.update({int(k): int(v) for k, v in raw_lc.items()})

        self.bot_admin_whitelist.clear()
        self.bot_admin_whitelist.update(int(u) for u in raw_config.get("bot_admin_whitelist", []))

        print(f"[DB] Data directory: {BASE_DIR}")
        print(f"[DB] Loaded {len(self.player_economy)} economy entries, {len(self.daily_data)} daily entries.")

    async def auto_save_loop(self):
        """Periodically flush all JSON data to disk every 5 minutes as a safety net."""
        await asyncio.sleep(60)  # wait 1 min after boot before first save
        while True:
            try:
                self.save_economy()
                self.save_daily()
                self.save_beg()
                self.save_config()
            except Exception as e:
                print(f"[auto-save] Error: {e}")
            await asyncio.sleep(300)  # every 5 minutes

    # ── individual saves ──────────────────────────────────
    def save_economy(self):
        self._save(self.DB_ECONOMY, {str(k): v for k, v in self.player_economy.items()})

    def save_stats(self):
        self._save(self.DB_STATS, {str(k): v for k, v in self.player_stats.items()})

    def save_daily(self):
        self._save(self.DB_DAILY, {str(k): v for k, v in self.daily_data.items()})

    def save_beg(self):
        self._save(self.DB_BEG, {str(k): v for k, v in self.beg_cooldowns.items()})

    def save_config(self):
        self._save(self.DB_CONFIG, {
            "allowed_channel": {str(k): v for k, v in self.allowed_channel.items()},
            "log_channel": {str(k): v for k, v in self.log_channel.items()},
            "bot_admin_whitelist": list(self.bot_admin_whitelist),
        })


store = PersistentStore()

# Bare-name aliases — same underlying objects as `store.xxx` (see module
# docstring above for why this is safe).
player_economy = store.player_economy
player_stats = store.player_stats
daily_data = store.daily_data
beg_cooldowns = store.beg_cooldowns
allowed_channel = store.allowed_channel
log_channel = store.log_channel
bot_admin_whitelist = store.bot_admin_whitelist

load_all = store.load_all
auto_save_loop = store.auto_save_loop
save_economy = store.save_economy
save_stats = store.save_stats
save_daily = store.save_daily
save_beg = store.save_beg
save_config = store.save_config
