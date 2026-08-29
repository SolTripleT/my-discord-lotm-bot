# ============================================================
# DATABASE — all SQLite access (aiosqlite) behind one Database class:
# schema creation/migrations, per-user progression rows, bot settings,
# the blacklist, the audit log table, and the item inventory tables.
#
# A module-level singleton (`db`) is created below, together with
# bare-name aliases for every method — the rest of the codebase calls
# these the same way it always did (`get_user_data(...)`, `update_user(...)`,
# etc.), they just now live behind a real class instead of loose globals.
# ============================================================
import aiosqlite

from config import DB_PATH
from logging_config import logger


class Database:
    async def init_db(self):
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
            except Exception as e:
                logger.debug(f"[DB] guild_id column migration skipped (likely already applied): {e}")

            # Migration: add pray cooldown column
            try:
                await db.execute("ALTER TABLE users ADD COLUMN last_pray TIMESTAMP")
            except Exception as e:
                logger.debug(f"[DB] last_pray column migration skipped (likely already applied): {e}")

            # Migration: add username column to logs (added in v5.20)
            try:
                await db.execute("ALTER TABLE logs ADD COLUMN username TEXT")
            except Exception as e:
                logger.debug(f"[DB] logs.username column migration skipped (likely already applied): {e}")

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
                logger.exception("[DB] guild_id=0 cleanup migration failed")

            await db.commit()

        # ── Extension tables (chairs, items, events) ──────────────────
        await self._init_extension_tables()


    async def get_setting(self, key: str):
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute("SELECT value FROM settings WHERE key = ?", (key,)) as cursor:
                row = await cursor.fetchone()
                return row[0] if row else None

    async def set_setting(self, key: str, value: str):
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))
            await db.commit()

    async def is_blacklisted(self, user_id: int, guild_id: int) -> bool:
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute(
                "SELECT 1 FROM blacklist WHERE user_id = ? AND guild_id = ?", (user_id, guild_id)
            ) as cursor:
                return await cursor.fetchone() is not None

    async def get_user_data(self, user_id: int, guild_id: int = 0):
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

    async def update_user(self, user_id: int, guild_id: int = 0, pathway=None, sequence=None, xp=None, last_message=None, last_daily=None, last_pray=None):
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
            logger.info(f"[DB] update_user: user={user_id} guild={guild_id} pathway={final_pathway} seq={final_sequence} xp={final_xp}")


    async def _init_extension_tables(self):
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

    async def db_add_item(self, user_id: int, guild_id: int, item_name: str, qty: int = 1):
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "INSERT INTO player_items(user_id,guild_id,item_name,quantity) VALUES(?,?,?,?) "
                "ON CONFLICT(user_id,guild_id,item_name) DO UPDATE SET quantity=quantity+?",
                (user_id, guild_id, item_name, qty, qty)
            )
            await db.commit()

    async def db_remove_item(self, user_id: int, guild_id: int, item_name: str, qty: int = 1) -> bool:
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

    async def db_get_items(self, user_id: int, guild_id: int) -> dict:
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute(
                "SELECT item_name, quantity FROM player_items WHERE user_id=? AND guild_id=? AND quantity>0",
                (user_id, guild_id)
            ) as cur:
                rows = await cur.fetchall()
        return {r[0]: r[1] for r in rows}

    async def db_has_item(self, user_id: int, guild_id: int, item_name: str, qty: int = 1) -> bool:
        items = await self.db_get_items(user_id, guild_id)
        return items.get(item_name, 0) >= qty



db = Database()

init_db = db.init_db
get_setting = db.get_setting
set_setting = db.set_setting
is_blacklisted = db.is_blacklisted
get_user_data = db.get_user_data
update_user = db.update_user
db_add_item = db.db_add_item
db_remove_item = db.db_remove_item
db_get_items = db.db_get_items
db_has_item = db.db_has_item
