# ============================================================
# LOGGING — one shared logger for the whole bot: timestamped,
# leveled output to the console AND to a rotating log file
# (logs/bot.log, 5MB x 5 backups) so nothing that goes wrong is
# silently lost. Every module does `from logging_config import logger`.
# ============================================================
import logging
import logging.handlers
import os

from config import BASE_DIR

LOG_DIR = os.path.join(BASE_DIR, "logs")
os.makedirs(LOG_DIR, exist_ok=True)

logger = logging.getLogger("lotm_bot")
logger.setLevel(logging.INFO)

_formatter = logging.Formatter(
    fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

if not logger.handlers:
    _console_handler = logging.StreamHandler()
    _console_handler.setFormatter(_formatter)
    logger.addHandler(_console_handler)

    _file_handler = logging.handlers.RotatingFileHandler(
        os.path.join(LOG_DIR, "bot.log"),
        maxBytes=5 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    _file_handler.setFormatter(_formatter)
    logger.addHandler(_file_handler)

# Keep discord.py's own (very chatty) logger from drowning ours out, but
# still capture warnings/errors from it in the same file.
logging.getLogger("discord").setLevel(logging.WARNING)
