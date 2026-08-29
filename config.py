# ============================================================
# CONFIG — environment variables, filesystem paths, and the
# static pathway/colour data loaded once at import time.
# ============================================================
import os
import json
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "lotm_beyonders.db")
load_dotenv(os.path.join(BASE_DIR, "haha.env"))

OWNER_ID = 787147722444505110

# ====================== read LOTM pathway names from json ======================
# NOTE: the original bot.py opened "pathways.json" (relative to the process's
# working directory). The actual file on disk is "Pathways.json" — on a
# case-sensitive filesystem this raised FileNotFoundError. Fixed here to use
# the real filename, resolved relative to this file (BASE_DIR) so the bot
# works no matter which directory it's launched from.
with open(os.path.join(BASE_DIR, "Pathways.json"), "r", encoding="utf-8") as f:
    pathways = json.load(f)

# ====================== PATHWAY SYMBOL COLORS ======================
with open(os.path.join(BASE_DIR, "pathway_colors.json"), "r", encoding="utf-8") as f:
    pathway_colors = json.load(f)
