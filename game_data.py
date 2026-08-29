# ============================================================
# GAME DATA — static balance/content tables for the Beyonder
# progression + PvP battle systems: base stats per Sequence, per-pathway
# stat & role modifiers, ability metadata, the in-app /guide pages, and
# small display-only lookup tables. Pure data, no behaviour — moved out
# of the logic modules so game-balance tweaks don't require touching code.
# ============================================================
import discord

# ── Sequence base stats ───────────────────────────────────
SEQ_BASE_STATS = {
    9: {"hp": 240,  "sp": 110},
    8: {"hp": 275,  "sp": 125},
    7: {"hp": 315,  "sp": 150},
    6: {"hp": 360,  "sp": 175},
    5: {"hp": 420,  "sp": 205},
    4: {"hp": 540,  "sp": 265},
    3: {"hp": 705,  "sp": 350},
    2: {"hp": 915,  "sp": 475},
    1: {"hp": 1190, "sp": 595},
    0: {"hp": 1400, "sp": 740},
}

# ── Sequence average HP (across all pathways) — used for scaling damage ──
# Damage = pct * SEQ_AVG_HP[attacker's seq], so power grows naturally as you advance
SEQ_AVG_HP = {
    9: 270,
    8: 310,
    7: 355,
    6: 410,
    5: 420,
    4: 540,
    3: 705,
    2: 915,
    1: 1190,
    0: 1400,
}


# ── Pathway base stats / role modifiers / abilities ───────
PATHWAY_BASE_STATS = {
    # ── High HP / Low SP (Physical/Combat) ──
    ("Twilight Giant", 9): {"hp":  310, "sp":   90},
    ("Twilight Giant", 8): {"hp":  360, "sp":  105},
    ("Twilight Giant", 7): {"hp":  415, "sp":  125},
    ("Twilight Giant", 6): {"hp":  490, "sp":  150},
    ("Chained",        9): {"hp":  300, "sp":   90},
    ("Chained",        8): {"hp":  345, "sp":  105},
    ("Chained",        7): {"hp":  390, "sp":  125},
    ("Chained",        6): {"hp":  465, "sp":  155},
    ("Abyss",          9): {"hp":  295, "sp":   95},
    ("Abyss",          8): {"hp":  340, "sp":  110},
    ("Abyss",          7): {"hp":  380, "sp":  130},
    ("Abyss",          6): {"hp":  445, "sp":  160},
    ("Red Priest",     9): {"hp":  280, "sp":   95},
    ("Red Priest",     8): {"hp":  325, "sp":  110},
    ("Red Priest",     7): {"hp":  365, "sp":  130},
    ("Red Priest",     6): {"hp":  430, "sp":  165},
    ("Tyrant",         9): {"hp":  275, "sp":  100},
    ("Tyrant",         8): {"hp":  320, "sp":  115},
    ("Tyrant",         7): {"hp":  360, "sp":  135},
    ("Tyrant",         6): {"hp":  425, "sp":  170},
    ("Justiciar",      9): {"hp":  275, "sp":  100},
    ("Justiciar",      8): {"hp":  315, "sp":  115},
    ("Justiciar",      7): {"hp":  355, "sp":  135},
    ("Justiciar",      6): {"hp":  420, "sp":  170},
    ("Black Emperor",  9): {"hp":  270, "sp":  100},
    ("Black Emperor",  8): {"hp":  310, "sp":  115},
    ("Black Emperor",  7): {"hp":  355, "sp":  135},
    ("Black Emperor",  6): {"hp":  415, "sp":  175},
    # ── Balanced ──
    ("Demoness",       9): {"hp":  250, "sp":  115},
    ("Demoness",       8): {"hp":  290, "sp":  135},
    ("Demoness",       7): {"hp":  325, "sp":  165},
    ("Demoness",       6): {"hp":  385, "sp":  205},
    ("Death",          9): {"hp":  260, "sp":  115},
    ("Death",          8): {"hp":  295, "sp":  135},
    ("Death",          7): {"hp":  335, "sp":  165},
    ("Death",          6): {"hp":  400, "sp":  205},
    ("Darkness",       9): {"hp":  255, "sp":  115},
    ("Darkness",       8): {"hp":  295, "sp":  135},
    ("Darkness",       7): {"hp":  335, "sp":  165},
    ("Darkness",       6): {"hp":  395, "sp":  205},
    ("Sun",            9): {"hp":  265, "sp":  115},
    ("Sun",            8): {"hp":  305, "sp":  135},
    ("Sun",            7): {"hp":  345, "sp":  160},
    ("Sun",            6): {"hp":  405, "sp":  200},
    ("Moon",           9): {"hp":  260, "sp":  115},
    ("Moon",           8): {"hp":  300, "sp":  135},
    ("Moon",           7): {"hp":  340, "sp":  165},
    ("Moon",           6): {"hp":  400, "sp":  205},
    ("Mother",         9): {"hp":  265, "sp":  115},
    ("Mother",         8): {"hp":  305, "sp":  135},
    ("Mother",         7): {"hp":  345, "sp":  165},
    ("Mother",         6): {"hp":  410, "sp":  205},
    ("Door",           9): {"hp":  255, "sp":  115},
    ("Door",           8): {"hp":  295, "sp":  135},
    ("Door",           7): {"hp":  335, "sp":  165},
    ("Door",           6): {"hp":  390, "sp":  205},
    ("Error",          9): {"hp":  250, "sp":  115},
    ("Error",          8): {"hp":  290, "sp":  135},
    ("Error",          7): {"hp":  330, "sp":  165},
    ("Error",          6): {"hp":  390, "sp":  205},
    # ── Low HP / High SP (Spiritual/Knowledge) ──
    ("Hanged Man",     9): {"hp":  250, "sp":  140},
    ("Hanged Man",     8): {"hp":  285, "sp":  165},
    ("Hanged Man",     7): {"hp":  320, "sp":  195},
    ("Hanged Man",     6): {"hp":  375, "sp":  240},
    ("Wheel of Fortune",9): {"hp":  245, "sp":  145},
    ("Wheel of Fortune",8): {"hp":  285, "sp":  170},
    ("Wheel of Fortune",7): {"hp":  320, "sp":  205},
    ("Wheel of Fortune",6): {"hp":  370, "sp":  250},
    ("Hermit",         9): {"hp":  260, "sp":  145},
    ("Hermit",         8): {"hp":  295, "sp":  170},
    ("Hermit",         7): {"hp":  335, "sp":  205},
    ("Hermit",         6): {"hp":  390, "sp":  255},
    ("White Tower",    9): {"hp":  245, "sp":  140},
    ("White Tower",    8): {"hp":  285, "sp":  165},
    ("White Tower",    7): {"hp":  320, "sp":  195},
    ("White Tower",    6): {"hp":  370, "sp":  240},
    ("Visionary",      9): {"hp":  250, "sp":  135},
    ("Visionary",      8): {"hp":  285, "sp":  160},
    ("Visionary",      7): {"hp":  320, "sp":  195},
    ("Visionary",      6): {"hp":  370, "sp":  235},
    ("Paragon",        9): {"hp":  250, "sp":  130},
    ("Paragon",        8): {"hp":  290, "sp":  150},
    ("Paragon",        7): {"hp":  330, "sp":  185},
    ("Paragon",        6): {"hp":  380, "sp":  220},
    ("Fool",           9): {"hp":  250, "sp":  135},
    ("Fool",           8): {"hp":  290, "sp":  160},
    ("Fool",           7): {"hp":  325, "sp":  190},
    ("Fool",           6): {"hp":  375, "sp":  230},
    # Seq 5
    ("Twilight Giant", 5): {"hp":  560, "sp":  200},
    ("Chained",        5): {"hp":  540, "sp":  200},
    ("Abyss",          5): {"hp":  510, "sp":  210},
    ("Red Priest",     5): {"hp":  490, "sp":  205},
    ("Tyrant",         5): {"hp":  480, "sp":  210},
    ("Justiciar",      5): {"hp":  475, "sp":  210},
    ("Black Emperor",  5): {"hp":  460, "sp":  215},
    ("Demoness",       5): {"hp":  430, "sp":  240},
    ("Death",          5): {"hp":  440, "sp":  240},
    ("Darkness",       5): {"hp":  435, "sp":  240},
    ("Sun",            5): {"hp":  445, "sp":  240},
    ("Moon",           5): {"hp":  440, "sp":  240},
    ("Mother",         5): {"hp":  450, "sp":  235},
    ("Door",           5): {"hp":  420, "sp":  240},
    ("Error",          5): {"hp":  415, "sp":  240},
    ("Hanged Man",     5): {"hp":  405, "sp":  270},
    ("Wheel of Fortune",5):{"hp":  400, "sp":  275},
    ("Hermit",         5): {"hp":  415, "sp":  275},
    ("White Tower",    5): {"hp":  400, "sp":  275},
    ("Visionary",      5): {"hp":  405, "sp":  265},
    ("Paragon",        5): {"hp":  410, "sp":  255},
    ("Fool",           5): {"hp":  405, "sp":  260},
}

# ── Role definitions ──────────────────────────────────────

ROLE_MODIFIERS = {
    # Seq 9
    "[Hermit] Seq 9 — Mystery Pryer":            {"health": 0,  "attack": 0,  "luck": 0,  "spirituality": +2, "speed": 0,  "spirit": +2},
    "[Paragon] Seq 9 — Savant":                  {"health": 0,  "attack": 0,  "luck": 0,  "spirituality": 0,  "speed": 0,  "spirit": +3},
    "[Fool] Seq 9 — Seer":                       {"health": -1, "attack": 0,  "luck": 0,  "spirituality": +2, "speed": 0,  "spirit": +1},
    "[Error] Seq 9 — Marauder":                  {"health": 0,  "attack": 0,  "luck": 0,  "spirituality": 0,  "speed": +2, "spirit": 0},
    "[Door] Seq 9 — Apprentice":                 {"health": 0,  "attack": 0,  "luck": 0,  "spirituality": 0,  "speed": +2, "spirit": 0},
    "[Red Priest] Seq 9 — Hunter":               {"health": 0,  "attack": +1, "luck": 0,  "spirituality": 0,  "speed": +1, "spirit": 0},
    "[Demoness] Seq 9 — Assassin":               {"health": 0,  "attack": 0,  "luck": 0,  "spirituality": 0,  "speed": +2, "spirit": 0},
    "[Abyss] Seq 9 — Criminal":                  {"health": 0,  "attack": +2, "luck": 0,  "spirituality": 0,  "speed": 0,  "spirit": 0},
    "[Chained] Seq 9 — Prisoner":                {"health": +1, "attack": 0,  "luck": 0,  "spirituality": 0,  "speed": 0,  "spirit": 0},
    "[Twilight Giant] Seq 9 — Warrior":          {"health": 0,  "attack": +1, "luck": 0,  "spirituality": 0,  "speed": +1, "spirit": 0},
    "[Darkness] Seq 9 — Sleepless":              {"health": 0,  "attack": 0,  "luck": 0,  "spirituality": +1, "speed": 0,  "spirit": 0},
    "[Death] Seq 9 — Corpse Collector":          {"health": +1, "attack": 0,  "luck": 0,  "spirituality": 0,  "speed": 0,  "spirit": 0},
    "[Tyrant] Seq 9 — Sailor":                   {"health": 0,  "attack": +2, "luck": 0,  "spirituality": 0,  "speed": 0,  "spirit": 0},
    "[Sun] Seq 9 — Bard":                        {"health": +1, "attack": +1, "luck": +1, "spirituality": +1, "speed": +1, "spirit": +1},
    "[Hanged Man] Seq 9 — Secrets Suppliant":    {"health": 0,  "attack": 0,  "luck": 0,  "spirituality": +2, "speed": 0,  "spirit": 0},
    "[White Tower] Seq 9 — Reader":              {"health": 0,  "attack": 0,  "luck": 0,  "spirituality": 0,  "speed": 0,  "spirit": +3},
    "[Visionary] Seq 9 — Spectator":             {"health": 0,  "attack": 0,  "luck": 0,  "spirituality": 0,  "speed": 0,  "spirit": +2},
    "[Mother] Seq 9 — Planter":                  {"health": +2, "attack": 0,  "luck": 0,  "spirituality": 0,  "speed": 0,  "spirit": 0},
    "[Moon] Seq 9 — Apothecary":                 {"health": +1, "attack": 0,  "luck": 0,  "spirituality": +1, "speed": 0,  "spirit": 0},
    "[Wheel of Fortune] Seq 9 — Monster":        {"health": 0,  "attack": 0,  "luck": +3, "spirituality": 0,  "speed": 0,  "spirit": 0},
    "[Black Emperor] Seq 9 — Lawyer":            {"health": 0,  "attack": 0,  "luck": 0,  "spirituality": 0,  "speed": 0,  "spirit": +2},
    "[Justiciar] Seq 9 — Arbiter":               {"health": +1, "attack": +2, "luck": 0,  "spirituality": 0,  "speed": 0,  "spirit": 0},
    # Seq 8
    "[Hermit] Seq 8 — Melee Scholar":            {"health": 0,  "attack": 0,  "luck": 0,  "spirituality": +1, "speed": +2, "spirit": +2},
    "[Paragon] Seq 8 — Archaeologist":           {"health": +2, "attack": 0,  "luck": 0,  "spirituality": +1, "speed": 0,  "spirit": +2},
    "[Door] Seq 8 — Trickmaster":                {"health": 0,  "attack": 0,  "luck": 0,  "spirituality": +2, "speed": +3, "spirit": 0},
    "[Fool] Seq 8 — Clown":                      {"health": 0,  "attack": +2, "luck": +1, "spirituality": +1, "speed": +1, "spirit": 0},
    "[Error] Seq 8 — Swindler":                  {"health": 0,  "attack": 0,  "luck": +1, "spirituality": 0,  "speed": +2, "spirit": +1},
    "[Red Priest] Seq 8 — Provoker":             {"health": +1, "attack": 0,  "luck": 0,  "spirituality": +1, "speed": +1, "spirit": 0},
    "[Demoness] Seq 8 — Instigator":             {"health": 0,  "attack": 0,  "luck": 0,  "spirituality": 0,  "speed": +2, "spirit": 0},
    "[Abyss] Seq 8 — Unwinged Angel":            {"health": 0,  "attack": +2, "luck": 0,  "spirituality": 0,  "speed": 0,  "spirit": 0},
    "[Chained] Seq 8 — Lunatic":                 {"health": +2, "attack": 0,  "luck": 0,  "spirituality": 0,  "speed": 0,  "spirit": 0},
    "[Twilight Giant] Seq 8 — Pugilist":         {"health": +1, "attack": +2, "luck": 0,  "spirituality": 0,  "speed": 0,  "spirit": 0},
    "[Darkness] Seq 8 — Midnight Poet":          {"health": +1, "attack": 0,  "luck": +1, "spirituality": 0,  "speed": 0,  "spirit": 0},
    "[Death] Seq 8 — Gravedigger":               {"health": 0,  "attack": 0,  "luck": 0,  "spirituality": +1, "speed": +1, "spirit": 0},
    "[Hanged Man] Seq 8 — Listener":             {"health": 0,  "attack": 0,  "luck": 0,  "spirituality": +3, "speed": 0,  "spirit": 0},
    "[Tyrant] Seq 8 — Folk of Rage":             {"health": +1, "attack": 0,  "luck": 0,  "spirituality": 0,  "speed": 0,  "spirit": 0},
    "[White Tower] Seq 8 — Student of Ratiocination": {"health": 0, "attack": 0, "luck": 0, "spirituality": 0, "speed": 0, "spirit": +3},
    "[Visionary] Seq 8 — Telepathist":           {"health": 0,  "attack": 0,  "luck": +1, "spirituality": 0,  "speed": 0,  "spirit": +2},
    "[Sun] Seq 8 — Light Suppliant":             {"health": 0,  "attack": 0,  "luck": 0,  "spirituality": +2, "speed": 0,  "spirit": +1},
    "[Mother] Seq 8 — Doctor":                   {"health": +2, "attack": 0,  "luck": 0,  "spirituality": +1, "speed": 0,  "spirit": 0},
    "[Moon] Seq 8 — Beast Tamer":                {"health": +1, "attack": 0,  "luck": +1, "spirituality": 0,  "speed": 0,  "spirit": 0},
    "[Wheel of Fortune] Seq 8 — Robot":          {"health": +1, "attack": 0,  "luck": +2, "spirituality": +1, "speed": 0,  "spirit": +2},
    "[Black Emperor] Seq 8 — Barbarian":         {"health": 0,  "attack": +2, "luck": 0,  "spirituality": 0,  "speed": 0,  "spirit": +1},
    "[Justiciar] Seq 8 — Sheriff":               {"health": 0,  "attack": +1, "luck": 0,  "spirituality": 0,  "speed": +2, "spirit": 0},
    # Seq 7
    "[Fool] Seq 7 — Magician":                   {"health": 0,  "attack": 0,  "luck": +1, "spirituality": +1, "speed": +1, "spirit": +1},
    "[Door] Seq 7 — Astrologer":                 {"health": 0,  "attack": 0,  "luck": +1, "spirituality": +2, "speed": +1, "spirit": +1},
    "[Error] Seq 7 — Cryptologist":              {"health": 0,  "attack": 0,  "luck": 0,  "spirituality": +1, "speed": +2, "spirit": +2},
    "[Visionary] Seq 7 — Psychiatrist":          {"health": +1, "attack": 0,  "luck": 0,  "spirituality": +2, "speed": 0,  "spirit": +2},
    "[Sun] Seq 7 — Solar High Priest":           {"health": +2, "attack": 0,  "luck": +1, "spirituality": +2, "speed": 0,  "spirit": +1},
    "[Tyrant] Seq 7 — Seafarer":                 {"health": +1, "attack": +1, "luck": 0,  "spirituality": +1, "speed": +2, "spirit": 0},
    "[Hanged Man] Seq 7 — Shadow Ascetic":       {"health": 0,  "attack": +1, "luck": +1, "spirituality": +1, "speed": +2, "spirit": 0},
    "[White Tower] Seq 7 — Detective":           {"health": 0,  "attack": 0,  "luck": 0,  "spirituality": +1, "speed": 0,  "spirit": +3},
    "[Darkness] Seq 7 — Nightmare":              {"health": +1, "attack": 0,  "luck": +1, "spirituality": +2, "speed": 0,  "spirit": +1},
    "[Death] Seq 7 — Spirit Medium":             {"health": 0,  "attack": 0,  "luck": +1, "spirituality": +2, "speed": +1, "spirit": 0},
    "[Twilight Giant] Seq 7 — Weapon Master":    {"health": +1, "attack": +2, "luck": 0,  "spirituality": +1, "speed": +1, "spirit": 0},
    "[Demoness] Seq 7 — Witch":                  {"health": 0,  "attack": 0,  "luck": +1, "spirituality": +2, "speed": +1, "spirit": +1},
    "[Red Priest] Seq 7 — Pyromaniac":           {"health": 0,  "attack": +2, "luck": 0,  "spirituality": +1, "speed": +1, "spirit": 0},
    "[Hermit] Seq 7 — Warlock":                  {"health": 0,  "attack": +1, "luck": +1, "spirituality": +2, "speed": 0,  "spirit": +1},
    "[Paragon] Seq 7 — Appraiser":               {"health": 0,  "attack": 0,  "luck": 0,  "spirituality": +1, "speed": 0,  "spirit": +3},
    "[Wheel of Fortune] Seq 7 — Lucky One":      {"health": 0,  "attack": 0,  "luck": +3, "spirituality": +1, "speed": 0,  "spirit": +1},
    "[Moon] Seq 7 — Vampire":                    {"health": +1, "attack": +1, "luck": 0,  "spirituality": +1, "speed": +1, "spirit": 0},
    "[Mother] Seq 7 — Harvest Priest":           {"health": +2, "attack": 0,  "luck": 0,  "spirituality": +1, "speed": 0,  "spirit": +1},
    "[Chained] Seq 7 — Werewolf":                {"health": +3, "attack": +2, "luck": 0,  "spirituality": 0,  "speed": +1, "spirit": 0},
    "[Abyss] Seq 7 — Serial Killer":             {"health": 0,  "attack": +2, "luck": +1, "spirituality": +1, "speed": +1, "spirit": 0},
    "[Black Emperor] Seq 7 — Briber":            {"health": +1, "attack": 0,  "luck": +1, "spirituality": 0,  "speed": 0,  "spirit": +2},
    "[Justiciar] Seq 7 — Interrogator":          {"health": +1, "attack": +2, "luck": 0,  "spirituality": +1, "speed": +1, "spirit": 0},
    # Seq 6
    "[Fool] Seq 6 — Faceless":                   {"health": +1, "attack": +1, "luck": +1, "spirituality": +1, "speed": +1, "spirit": +2},
    "[Error] Seq 6 — Prometheus":                {"health": 0,  "attack": 0,  "luck": +1, "spirituality": +1, "speed": +2, "spirit": +2},
    "[Door] Seq 6 — Scribe":                     {"health": 0,  "attack": 0,  "luck": +1, "spirituality": +2, "speed": +2, "spirit": +2},
    "[Visionary] Seq 6 — Hypnotist":             {"health": +1, "attack": 0,  "luck": +1, "spirituality": +2, "speed": 0,  "spirit": +3},
    "[Sun] Seq 6 — Notary":                      {"health": +2, "attack": +1, "luck": +1, "spirituality": +2, "speed": +1, "spirit": +2},
    "[Tyrant] Seq 6 — Wind-blessed":             {"health": +2, "attack": +3, "luck": 0,  "spirituality": +1, "speed": +2, "spirit": 0},
    "[Hanged Man] Seq 6 — Rose Bishop":          {"health": +1, "attack": 0,  "luck": 0,  "spirituality": +3, "speed": 0,  "spirit": +2},
    "[White Tower] Seq 6 — Polymath":            {"health": 0,  "attack": 0,  "luck": 0,  "spirituality": +2, "speed": 0,  "spirit": +4},
    "[Darkness] Seq 6 — Soul Assurer":           {"health": +1, "attack": 0,  "luck": +1, "spirituality": +2, "speed": 0,  "spirit": +2},
    "[Death] Seq 6 — Spirit Guide":              {"health": +2, "attack": 0,  "luck": +1, "spirituality": +2, "speed": 0,  "spirit": +1},
    "[Twilight Giant] Seq 6 — Dawn Paladin":     {"health": +3, "attack": +2, "luck": 0,  "spirituality": +1, "speed": +1, "spirit": +1},
    "[Demoness] Seq 6 — Pleasure":               {"health": 0,  "attack": 0,  "luck": +2, "spirituality": +1, "speed": +2, "spirit": +2},
    "[Red Priest] Seq 6 — Conspirer":            {"health": 0,  "attack": +1, "luck": 0,  "spirituality": +1, "speed": +2, "spirit": +2},
    "[Hermit] Seq 6 — Scrolls Professor":        {"health": 0,  "attack": 0,  "luck": +2, "spirituality": +3, "speed": 0,  "spirit": +2},
    "[Paragon] Seq 6 — Artisan":                 {"health": 0,  "attack": 0,  "luck": +1, "spirituality": +2, "speed": 0,  "spirit": +4},
    "[Wheel of Fortune] Seq 6 — Calamity Priest":{"health": +1, "attack": 0,  "luck": +3, "spirituality": +1, "speed": 0,  "spirit": +2},
    "[Moon] Seq 6 — Potions Professor":          {"health": +2, "attack": 0,  "luck": +2, "spirituality": +2, "speed": 0,  "spirit": +1},
    "[Mother] Seq 6 — Biologist":                {"health": +3, "attack": +1, "luck": +2, "spirituality": +1, "speed": 0,  "spirit": +1},
    "[Chained] Seq 6 — Zombie":                  {"health": +3, "attack": +2, "luck": +1, "spirituality": 0,  "speed": +1, "spirit": 0},
    "[Abyss] Seq 6 — Devil":                     {"health": +1, "attack": +3, "luck": +1, "spirituality": +1, "speed": +1, "spirit": 0},
    "[Black Emperor] Seq 6 — Baron of Corruption":{"health": +1, "attack": 0, "luck": +1, "spirituality": +1, "speed": 0,  "spirit": +3},
    "[Justiciar] Seq 6 — Judge":                 {"health": +2, "attack": +2, "luck": 0,  "spirituality": +1, "speed": +1, "spirit": +2},
    # Seq 5
    "[Fool] Seq 5 — Marionettist":               {"health": +2, "attack": +1, "luck": +2, "spirituality": +3, "speed": +1, "spirit": +3},
    "[Error] Seq 5 — Dream Stealer":             {"health": +1, "attack": +1, "luck": +2, "spirituality": +3, "speed": +2, "spirit": +3},
    "[Door] Seq 5 — Traveler":                   {"health": +2, "attack": +1, "luck": +1, "spirituality": +2, "speed": +4, "spirit": +2},
    "[Visionary] Seq 5 — Dreamwalker":           {"health": +1, "attack": +1, "luck": +2, "spirituality": +3, "speed": +1, "spirit": +4},
    "[Sun] Seq 5 — Priest of Light":             {"health": +3, "attack": +2, "luck": +2, "spirituality": +3, "speed": +1, "spirit": +3},
    "[Tyrant] Seq 5 — Ocean Songster":           {"health": +3, "attack": +3, "luck": +1, "spirituality": +2, "speed": +2, "spirit": +1},
    "[Hanged Man] Seq 5 — Shepherd":             {"health": +2, "attack": +1, "luck": +2, "spirituality": +3, "speed": +1, "spirit": +4},
    "[White Tower] Seq 5 — Mysticism Magister":  {"health": +1, "attack": +1, "luck": +1, "spirituality": +3, "speed": +1, "spirit": +5},
    "[Darkness] Seq 5 — Spirit Warlock":         {"health": +2, "attack": +2, "luck": +1, "spirituality": +3, "speed": +1, "spirit": +3},
    "[Death] Seq 5 — Gatekeeper":                {"health": +3, "attack": +2, "luck": +1, "spirituality": +3, "speed": +1, "spirit": +2},
    "[Twilight Giant] Seq 5 — Guardian":         {"health": +5, "attack": +3, "luck": 0,  "spirituality": +2, "speed": +1, "spirit": +1},
    "[Demoness] Seq 5 — Affliction":             {"health": +2, "attack": +2, "luck": +2, "spirituality": +2, "speed": +2, "spirit": +2},
    "[Red Priest] Seq 5 — Reaper":               {"health": +2, "attack": +4, "luck": +1, "spirituality": +2, "speed": +2, "spirit": +1},
    "[Hermit] Seq 5 — Mysticologist":            {"health": +1, "attack": +2, "luck": +2, "spirituality": +3, "speed": +1, "spirit": +3},
    "[Paragon] Seq 5 — Astronomer":              {"health": +2, "attack": +2, "luck": +3, "spirituality": +2, "speed": +1, "spirit": +3},
    "[Wheel of Fortune] Seq 5 — Winner":         {"health": +2, "attack": +2, "luck": +5, "spirituality": +2, "speed": +1, "spirit": +2},
    "[Moon] Seq 5 — Scarlet Scholar":            {"health": +3, "attack": +2, "luck": +2, "spirituality": +3, "speed": +1, "spirit": +2},
    "[Mother] Seq 5 — Druid":                    {"health": +4, "attack": +2, "luck": +2, "spirituality": +2, "speed": +1, "spirit": +1},
    "[Chained] Seq 5 — Wraith":                  {"health": +3, "attack": +3, "luck": +1, "spirituality": +2, "speed": +2, "spirit": +1},
    "[Abyss] Seq 5 — Desire Apostle":            {"health": +2, "attack": +3, "luck": +2, "spirituality": +3, "speed": +1, "spirit": +1},
    "[Black Emperor] Seq 5 — Mentor of Disorder":{"health": +2, "attack": +2, "luck": +2, "spirituality": +2, "speed": +1, "spirit": +3},
    "[Justiciar] Seq 5 — Disciplinary Paladin":  {"health": +3, "attack": +3, "luck": +1, "spirituality": +2, "speed": +1, "spirit": +2},
}

ROLE_ABILITIES = {
    # Seq 9
    "[Hermit] Seq 9 — Mystery Pryer":            "prying",
    "[Paragon] Seq 9 — Savant":                  "memorise",
    "[Fool] Seq 9 — Seer":                       "danger_intuition",
    "[Error] Seq 9 — Marauder":                  "pickpocket",
    "[Door] Seq 9 — Apprentice":                 "phasing",
    "[Red Priest] Seq 9 — Hunter":               "trap",
    "[Demoness] Seq 9 — Assassin":               "vital_strike",
    "[Abyss] Seq 9 — Criminal":                  "crime",
    "[Chained] Seq 9 — Prisoner":                "break_out",
    "[Twilight Giant] Seq 9 — Warrior":          "invigorate",
    "[Darkness] Seq 9 — Sleepless":              "night_boost",
    "[Death] Seq 9 — Corpse Collector":          "desecration",
    "[Tyrant] Seq 9 — Sailor":                   "balancing_act",
    "[Sun] Seq 9 — Bard":                        "song",
    "[Hanged Man] Seq 9 — Secrets Suppliant":    "prayer",
    "[White Tower] Seq 9 — Reader":              "study",
    "[Visionary] Seq 9 — Spectator":             "spectate",
    "[Mother] Seq 9 — Planter":                  "planting",
    "[Moon] Seq 9 — Apothecary":                 "curing",
    "[Wheel of Fortune] Seq 9 — Monster":        "glance_into_fate",
    "[Black Emperor] Seq 9 — Lawyer":            "testimony",
    "[Justiciar] Seq 9 — Arbiter":               "arbitration",
    # Seq 8
    "[Hermit] Seq 8 — Melee Scholar":            "combat_studies",
    "[Paragon] Seq 8 — Archaeologist":           "excavation",
    "[Door] Seq 8 — Trickmaster":                "magic_trick",
    "[Fool] Seq 8 — Clown":                      "card_tricks",
    "[Error] Seq 8 — Swindler":                  "swindling",
    "[Red Priest] Seq 8 — Provoker":             "provocation",
    "[Demoness] Seq 8 — Instigator":             "instigation",
    "[Abyss] Seq 8 — Unwinged Angel":            "moral_freedom",
    "[Chained] Seq 8 — Lunatic":                 "rage_baited",
    "[Twilight Giant] Seq 8 — Pugilist":         "fighting_spirit",
    "[Darkness] Seq 8 — Midnight Poet":          "poem",
    "[Death] Seq 8 — Gravedigger":               "burial_restoration",
    "[Hanged Man] Seq 8 — Listener":             "listening",
    "[Tyrant] Seq 8 — Folk of Rage":             "power_up_punch",
    "[White Tower] Seq 8 — Student of Ratiocination": "ritualistic_reasoning",
    "[Visionary] Seq 8 — Telepathist":           "reading",
    "[Sun] Seq 8 — Light Suppliant":             "brilliant_light",
    "[Mother] Seq 8 — Doctor":                   "treatment",
    "[Moon] Seq 8 — Beast Tamer":                "animal_companion",
    "[Wheel of Fortune] Seq 8 — Robot":          "computing",
    "[Black Emperor] Seq 8 — Barbarian":         "domination",
    "[Justiciar] Seq 8 — Sheriff":               "jurisdiction",
    # Seq 7
    "[Fool] Seq 7 — Magician":                   "paper_figurine",
    "[Door] Seq 7 — Astrologer":                 "astrology",
    "[Error] Seq 7 — Cryptologist":              "observation",
    "[Visionary] Seq 7 — Psychiatrist":          "therapy",
    "[Sun] Seq 7 — Solar High Priest":           "holy_water",
    "[Tyrant] Seq 7 — Seafarer":                 "water_bullet",
    "[Hanged Man] Seq 7 — Shadow Ascetic":       "sneak_attack",
    "[White Tower] Seq 7 — Detective":           "analyse",
    "[Darkness] Seq 7 — Nightmare":              "sleep_spell",
    "[Death] Seq 7 — Spirit Medium":             "summoning_dead",
    "[Twilight Giant] Seq 7 — Weapon Master":    "weapon_throw",
    "[Demoness] Seq 7 — Witch":                  "witch_curse",
    "[Red Priest] Seq 7 — Pyromaniac":           "fire_ravens",
    "[Hermit] Seq 7 — Warlock":                  "magic_spell",
    "[Paragon] Seq 7 — Appraiser":               "appraisal",
    "[Wheel of Fortune] Seq 7 — Lucky One":      "lucky_day",
    "[Moon] Seq 7 — Vampire":                    "blood_sucker",
    "[Mother] Seq 7 — Harvest Priest":           "vine_bomb",
    "[Chained] Seq 7 — Werewolf":               "transformation",
    "[Abyss] Seq 7 — Serial Killer":             "vile_crime",
    "[Black Emperor] Seq 7 — Briber":            "bribe",
    "[Justiciar] Seq 7 — Interrogator":          "mental_piercing",
    # Seq 6
    "[Fool] Seq 6 — Faceless":                   "faceless",
    "[Error] Seq 6 — Prometheus":                "prometheus",
    "[Door] Seq 6 — Scribe":                     "scribe",
    "[Visionary] Seq 6 — Hypnotist":             "hypnotist",
    "[Sun] Seq 6 — Notary":                      "notary",
    "[Tyrant] Seq 6 — Wind-blessed":             "wind_blessed",
    "[Hanged Man] Seq 6 — Rose Bishop":          "rose_bishop",
    "[White Tower] Seq 6 — Polymath":            "polymath",
    "[Darkness] Seq 6 — Soul Assurer":           "pacification",
    "[Death] Seq 6 — Spirit Guide":              "spirit_guide",
    "[Twilight Giant] Seq 6 — Dawn Paladin":     "hurricane_of_light",
    "[Demoness] Seq 6 — Pleasure":               "pleasure_witch",
    "[Red Priest] Seq 6 — Conspirer":            "conspiracy",
    "[Hermit] Seq 6 — Scrolls Professor":        "scrolls_professor",
    "[Paragon] Seq 6 — Artisan":                 "artisan",
    "[Wheel of Fortune] Seq 6 — Calamity Priest":"calamity_priest",
    "[Moon] Seq 6 — Potions Professor":          "potions_professor",
    "[Mother] Seq 6 — Biologist":                "biologist",
    "[Chained] Seq 6 — Zombie":                  "zombie",
    "[Abyss] Seq 6 — Devil":                     "devil_form",
    "[Black Emperor] Seq 6 — Baron of Corruption":"distortion",
    "[Justiciar] Seq 6 — Judge":                 "judge",
    # Seq 5
    "[Fool] Seq 5 — Marionettist":               "spirit_thread",
    "[Error] Seq 5 — Dream Stealer":             "mental_theft",
    "[Door] Seq 5 — Traveler":                   "blink",
    "[Visionary] Seq 5 — Dreamwalker":           "dream_visitation",
    "[Sun] Seq 5 — Priest of Light":             "purification_halo",
    "[Tyrant] Seq 5 — Ocean Songster":           "singing",
    "[Hanged Man] Seq 5 — Shepherd":             "grazing",
    "[White Tower] Seq 5 — Mysticism Magister":  "combination_spell",
    "[Darkness] Seq 5 — Spirit Warlock":         "spiritual_suppression",
    "[Death] Seq 5 — Gatekeeper":                "dragged_to_hell",
    "[Twilight Giant] Seq 5 — Guardian":         "protection",
    "[Demoness] Seq 5 — Affliction":             "disease_propagation",
    "[Red Priest] Seq 5 — Reaper":               "cull",
    "[Hermit] Seq 5 — Mysticologist":            "stellar_self",
    "[Paragon] Seq 5 — Astronomer":              "fire_storm",
    "[Wheel of Fortune] Seq 5 — Winner":         "curse_of_misfortune",
    "[Moon] Seq 5 — Scarlet Scholar":            "artificial_moon",
    "[Mother] Seq 5 — Druid":                    "spirit_animal_transformation",
    "[Chained] Seq 5 — Wraith":                  "possession",
    "[Abyss] Seq 5 — Desire Apostle":            "desire_explosion",
    "[Black Emperor] Seq 5 — Mentor of Disorder":"disorder",
    "[Justiciar] Seq 5 — Disciplinary Paladin":  "prohibition",

}

# Secondary abilities for Seq 5 (each Seq 5 role gets 2 abilities)
ROLE_ABILITIES_2 = {
    "[Fool] Seq 5 — Marionettist":               "marionette_summon",
    "[Error] Seq 5 — Dream Stealer":             "rewards_theft",
    "[Door] Seq 5 — Traveler":                   "travellers_door",
    "[Visionary] Seq 5 — Dreamwalker":           "dream_alteration",
    "[Sun] Seq 5 — Priest of Light":             "light_of_holiness",
    "[Tyrant] Seq 5 — Ocean Songster":           "arrow_of_lightning",
    "[Hanged Man] Seq 5 — Shepherd":             "ability_bag",
    "[White Tower] Seq 5 — Mysticism Magister":  "combination_spell",
    "[Darkness] Seq 5 — Spirit Warlock":         "spiritual_takeover",
    "[Death] Seq 5 — Gatekeeper":                "evil_sealing",
    "[Twilight Giant] Seq 5 — Guardian":         "dawn_armor",
    "[Demoness] Seq 5 — Affliction":             "thread_storm",
    "[Red Priest] Seq 5 — Reaper":               "weakness_development",
    "[Hermit] Seq 5 — Mysticologist":            "star_pillar",
    "[Paragon] Seq 5 — Astronomer":              "star_of_curses",
    "[Wheel of Fortune] Seq 5 — Winner":         "active_luck_boost",
    "[Moon] Seq 5 — Scarlet Scholar":            "scarlet_transformation",
    "[Mother] Seq 5 — Druid":                    "wrath_of_nature",
    "[Chained] Seq 5 — Wraith":                  "wraith_shriek",
    "[Abyss] Seq 5 — Desire Apostle":            "desire_symbiosis",
    "[Black Emperor] Seq 5 — Mentor of Disorder":  "gift_of_corruption",
    "[Justiciar] Seq 5 — Disciplinary Paladin":     "punishment",
}

SPELL_DEFENCE_ROLES = {
    "[Death] Seq 9 — Corpse Collector",
    "[Moon] Seq 9 — Apothecary",
    "[Chained] Seq 9 — Prisoner",
    "[Mother] Seq 8 — Doctor",
    "[Wheel of Fortune] Seq 8 — Robot",
    "[Sun] Seq 7 — Solar High Priest",
    "[Visionary] Seq 7 — Psychiatrist",
}

ALL_ROLE_ABILITY_KEYS = set(ROLE_ABILITIES.values())

ROLE_ABILITY_INFO = {
    # Seq 9
    "prying":               {"name": "🔍 Prying",             "cost": 20, "description": "Lower enemy defence by 15-25% for the rest of the battle."},
    "memorise":             {"name": "📖 Memorise",            "cost": 20, "description": "Use a selected ability SP-free for 1-2 turns."},
    "danger_intuition":     {"name": "👁️ Danger Intuition",    "cost": 20, "description": "Toggle on/off. While active: 35% dodge, 65% take 50% damage. Costs 20 SP upkeep per turn. No cooldown."},
    "pickpocket":           {"name": "🖐️ Pickpocket",          "cost": 18, "description": "Steal 15-20 SP. 1/10 chance to steal 5 HP too. (2 turn cooldown)"},
    "phasing":              {"name": "👻 Phasing",             "cost": 20, "description": "Dodge the next attack or increase flee chance. (1 turn cooldown)"},
    "trap":                 {"name": "🪤 Trap",                "cost": 0,  "display_cost": "35 SP", "description": "Spend your turn to secretly lay 2 traps (35 SP, 8 turn cd). Activates after 1 turn, randomly within 5 rounds: Flames (burn 3 turns), Trip (miss attack), Dog Chase (−5–7% base HP/turn, 2 turns), Tripwire (30% backlash or 7% base HP damage). After the trap ends it goes on cooldown for 3-4 turns. Traps cannot be stacked — setting new ones replaces old ones."},
    "vital_strike":         {"name": "🗡️ Vital Strike",        "cost": 25, "description": "Sneak attack — channel all strength into one point for **66 damage**. (4 turn cooldown)"},
    "crime":                {"name": "🦹 Crime",               "cost": 22, "description": "Randomly inflict bleed, paralysis (2 turn cd if lands), or deal damage."},
    "break_out":            {"name": "⛓️ Break Out",           "cost": 20, "description": "Remove all harmful status effects — bleed, burn, paralysis, sleep, freeze, defence reduction, sneak attack, trap, charm, scroll affliction, moral freedom, debuffs, and notary debuff."},
    "invigorate":           {"name": "💢 Invigorate",          "cost": 22, "description": "Deal 30% more damage on your next attack."},
    "night_boost":          {"name": "🌙 Night Boost",         "cost": 30, "description": "Deal +10 damage for the rest of the battle. One use only."},
    "desecration":          {"name": "💀 Desecration",         "cost": 25, "description": "Paralysis for 1 turn and 15 damage. (4 turn cooldown)"},
    "balancing_act":        {"name": "⚖️ Balancing Act",       "cost": 22, "description": "50% each: remove opponent status OR gain 5% permanent damage. (2 turn cd)"},
    "song":                 {"name": "🎵 Song",                "cost": 8,  "description": "Restore 20% of max SP. No cooldown."},
    "prayer":               {"name": "🙏 Prayer",              "cost": 25, "description": "All damage +50% permanently, but you bleed 5-8 HP per turn forever."},
    "study":                {"name": "📚 Study",               "cost": 0,  "display_cost": "Skip turn", "description": "Restore all SP at the cost of skipping your next turn."},
    "spectate":             {"name": "👁️ Spectate",            "cost": 20, "description": "Boost flee chance to 60% and improve dodge. Lasts the battle."},
    "planting":             {"name": "🌱 Planting",            "cost": 18, "description": "Recover 20 HP per turn for 3 turns."},
    "curing":               {"name": "🧪 Curing",              "cost": 0,  "display_cost": "24% max SP", "description": "Recover 32% of max HP at the cost of 24% of max SP."},
    "glance_into_fate":     {"name": "🎴 Glance into Fate",    "cost": 30, "description": "Spend 30 SP to suppress the opponent's role ability for 2 turns, leaving them without it. (4 turn cooldown)"},
    "testimony":            {"name": "⚖️ Testimony",           "cost": 25, "description": "Randomly: drain 15-25% enemy base HP (max 35% user base HP), OR heal 20-25% user base HP, OR regen 20-25% user base SP. (2 turn cooldown)"},
    "arbitration":          {"name": "⚖️ Arbiter",             "cost": 30, "description": "Declare a ceasefire — both fighters recuperate for 1-3 turns (no attacks or debuffs). 40% chance of disagreement and failure. Violation deals 40% of violator's HP as divine punishment. (6 turn cooldown)"},
    "jurisdiction":         {"name": "🏛️ Jurisdiction",        "cost": 40, "description": "Declare the battlefield your domain — +20% damage for 3 turns, increasing by 7% each turn. (12 turn cooldown)"},
    # Seq 8
    "combat_studies":       {"name": "📘 Combat Studies",      "cost": 0,  "display_cost": "Skip turn", "description": "Spend a turn studying — permanently increase all attacks by 20%."},
    "excavation":           {"name": "⛏️ Excavation",          "cost": 20, "description": "Dig for ancient treasure — gain 20-30% max SP, 12-20% max HP, or stun block + 6% max HP."},
    "magic_trick":          {"name": "🎩 Magic Trick",         "cost": 22, "description": "Random: freeze 2 turns (4 turn cd), burn 2 turns, or stun 1 turn (2 turn cd). Or escape from battle."},
    "card_tricks":          {"name": "🃏 Card Tricks",         "cost": 25, "description": "Deal 3×10 damage with bleed for 3 turns. (2 turn cooldown)"},
    "swindling":            {"name": "🎭 Swindling",           "cost": 15, "description": "Steal 20 SP. Failure chance increases each use."},
    "provocation":          {"name": "😤 Provocation",         "cost": 25, "description": "Force enemy to skip their next 1 turn and drain 25 SP from them. (4 turn cooldown)"},
    "instigation":          {"name": "😈 Instigation",         "cost": 18, "description": "Pick one of opponent's moves to force them to use. 50% success."},
    "moral_freedom":        {"name": "👼 Moral Freedom",       "cost": 30, "description": "Apply a status dealing 15 damage/turn to opponent. One use only."},
    "rage_baited":          {"name": "😡 Rage Baited",         "cost": 22, "description": "Double damage at 50% miss chance for 2 turns. (2 turn cooldown)"},
    "fighting_spirit":      {"name": "🛡️ Supernatural Resist",  "cost": 20, "description": "Gain immunity to status effects. Each time a status is attempted against you, take 40 damage instead of being afflicted."},
    "poem":                 {"name": "📜 Midnight Poem",        "cost": 25, "description": "Recite a dark verse — 33% sleep opponent 1 turn, 33% deal 15 self-damage, 33% drain 20 SP. (3 turn cooldown)"},
    "burial_restoration":   {"name": "⚰️ Burial Restoration",  "cost": 35, "description": "Restore the grave of an unfortunate soul and obtain their blessing — fully restores your SP. (4-round cooldown)"},
    "listening":            {"name": "👂 Listening",           "cost": 15, "description": "Fully restore SP. Each turn after has a 1/3 chance to stun yourself."},
    "power_up_punch":       {"name": "👊 Power Up Punch",      "cost": 22, "description": "Normal attacks amplified by 5% per round for 5 rounds (max 25%). Can't stack."},
    "ritualistic_reasoning":{"name": "🔬 Ritualistic Reasoning","cost": 30, "description": "Spend 30 SP to sabotage opponent's next move — it fails or backfires."},
    "reading":              {"name": "🔭 Reading",             "cost": 20, "description": "Secretly predict an opponent's move. If correct, it misses AND they are stunned for 1 turn. If wrong, you are stunned for 1 turn as backlash. The opponent cannot see what you predicted."},
    "brilliant_light":      {"name": "☀️ Brilliant Light",     "cost": 20, "description": "Flash of light deals 10 damage and stuns opponent for 1 turn. (2 turn cooldown if stun lands)"},
    "treatment":            {"name": "💊 Treatment",           "cost": 25, "description": "Grant yourself immunity to status effects for 3 turns."},
    "animal_companion":     {"name": "🐾 Animal Companion",    "cost": 20, "description": "Summon a companion that attacks for 10 damage every other turn."},
    "computing":            {"name": "🤖 Computing",           "cost": 30, "description": "Increase accuracy of all chance-based attacks to 100% for the rest of battle."},
    "domination":           {"name": "👊 Domination",           "cost": 30, "description": "Boost damage by 40% for 3 turns. Stuns from same seq have 50% reduced chance, from lower seq are 70% ineffective. (5 turn CD)"},
    "gun_shot":             {"name": "🔫 Gun Shot",            "cost": 50, "description": "Deal damage and apply permanent bleed for the rest of the battle. (4 turn cooldown)"},
    # Seq 7
    "paper_figurine":       {"name": "🪆 Paper Figurine",       "cost": 20, "description": "All attacks against you fail for 2 turns — including beyonder abilities, physical hits, special attacks, and unique shop abilities (e.g. Leodero)."},
    "astrology":            {"name": "🌟 Astrology",            "cost": 35, "description": "One-time use: if opponent is higher seq, option to immediately flee. Otherwise strike a weak point for 40% of your current HP as damage. (One-time use)"},
    "observation":          {"name": "🔎 Observation",          "cost": 18, "description": "You take 40% less damage from all sources next turn. (2 turn cooldown)"},
    "therapy":              {"name": "🛋️ Therapy",              "cost": 0,  "display_cost": "Skip turn", "description": "Spend a turn clearing all harmful effects — bleed, burn, paralysis, sleep, freeze, defence reduction, sneak attack, trap, charm, gunshot bleed, scroll affliction, moral freedom, debuffs, and notary debuff. (1 turn cooldown)"},
    "holy_water":           {"name": "💧 Holy Water",           "cost": 20, "description": "Heal ~12% max HP now and ~3% max HP/turn for 2 turns."},
    "water_bullet":         {"name": "💧🔫 Water Bullet",       "cost": 18, "description": "Deal 30 damage. 25% chance to freeze for 1 turn."},
    "sneak_attack":         {"name": "🥷 Sneak Attack",         "cost": 20, "description": "Strike from shadows for **30 damage** and stun opponent for 1 turn. (2 turn cooldown)"},
    "analyse":              {"name": "🧐 Analyse",              "cost": 18, "description": "Pick a skill — it permanently deals 30% less damage. 2 uses total."},
    "sleep_spell":          {"name": "😴 Sleep Spell",          "cost": 22, "description": "Put opponent to sleep for 2 turns. (4 turn cooldown)"},
    "summoning_dead":       {"name": "💀 Summoning Dead",       "cost": 0,  "display_cost": "20–30 SP", "description": "Summon 1-3 spirits (5/25/70%) — each deals 5 damage, max stuns. (2 turn cooldown)"},
    "weapon_throw":         {"name": "🗡️ Weapon Throw",         "cost": 22, "description": "Deal 40 damage + bleed 3 turns. (4 turn cooldown)"},
    "witch_curse":          {"name": "🧙 Witch's Curse",        "cost": 30, "description": "Applies a random curse: Black Flames (burn 10 dmg/turn × 2 turns), Frost (freeze 2 turns, no actions or fleeing), or Voodoo Curse (128 damage). (2 turn cooldown)"},
    "fire_ravens":          {"name": "🐦‍🔥 Fire Ravens",        "cost": 40, "description": "Summon 4-7 fire ravens, each dealing 10-15 damage. (3 turn cooldown)"},
    "magic_spell":          {"name": "✨ Magic Spell",           "cost": 32, "description": "Random: +30% permanent damage boost, heal 40 HP, or restore 30 SP. (1 turn cooldown)"},
    "appraisal":            {"name": "🏷️ Appraisal",            "cost": 18, "description": "Next action deals 40% more damage. (3 turn cooldown)"},
    "lucky_day":            {"name": "🍀 Lucky Day",            "cost": 20, "description": "Halve opponent's hit chance for 4 rounds. One use only."},
    "blood_sucker":         {"name": "🧛 Blood Sucker",         "cost": 20, "description": "Drain 15-30 HP from opponent — gain that HP yourself. (1 turn cooldown)"},
    "vine_bomb":            {"name": "🌿 Vine Bomb",            "cost": 50, "description": "Deal 25 damage + trap opponent for 2 turns (no actions). (4 turn cooldown)"},
    "transformation":       {"name": "🐺 Transformation",       "cost": 25, "description": "One-use: ×1.5 max HP, ~2% HP regen/turn, dodge 15%→85% over 5 turns. Reverts to 50% HP after 5 turns."},
    "vile_crime":           {"name": "🔪 Vile Crime",           "cost": 12, "description": "One-use: track 3 standard attack hits — then apply permanent bleed and burn."},
    "bribe":                {"name": "💰 Bribe",                "cost": 0,  "display_cost": "100k soli", "description": "Spend 100,000 soli to choose a bribe: Weaken (−40% next attack), Charm (can't attack for 2 turns), or Connect (share 40% of your damage taken with them for 1 turn). Disguised as an evaded attack. 10% fail per spirit stat enemy has over you. 5 turn CD."},
    "mental_piercing":      {"name": "🧠 Mental Piercing",      "cost": 22, "description": "Deal 30 HP damage + drain 10 SP. (2 turn cooldown)"},
    # Seq 6
    "faceless":             {"name": "🎭 Faceless",              "cost": 30, "description": "Infiltrate your enemy's trust then betray them — 35 damage + opponent skips next turn. (2 turn cooldown)"},
    "prometheus":           {"name": "🔥 Prometheus",            "cost": 35, "description": "Steal a random ability your enemy has used, removing it from them. If higher seq than enemy, pick any ability to steal. Stored until used or replaced. (3 turn cooldown)"},
    "scribe":               {"name": "📖 Scribe",                "cost": 40, "description": "Record an ability 1 turn after it is used against you, even while stunned. Only one ability stored at a time — recording a new one replaces the old. (3 turn cooldown)"},
    "hypnotist":            {"name": "🌀 Hypnotist",             "cost": 20, "description": "Trance your opponent — deal 20 self-damage AND stun them for 1 turn. Both effects apply simultaneously. (2 turn cooldown)"},
    "notary":               {"name": "📜 Notary",                "cost": 35, "description": "Proclaim in the name of your god — choose: +50% damage for yourself for 3 turns OR -50% damage on opponent for 2 turns. (4 turn cooldown)"},
    "wind_blessed":         {"name": "🌪️ Wind-blessed",          "cost": 0,  "display_cost": "25 SP/round", "description": "Channel the wind for 2 build-up rounds (25 SP each), then unleash 60 damage. (5 turn cooldown after activation)"},
    "rose_bishop":          {"name": "🌹 Rose Bishop",           "cost": 0,  "display_cost": "Choose",      "description": "Flesh & Blood magic — Flesh Bomb: 13.6% seq dmg at cost of 20 HP + 15 SP. Blood Regen: restore 15% max HP at cost of 25 SP. (2 turn cooldown)"},
    "polymath":             {"name": "📚 Polymath",              "cost": 15, "description": "Study an opponent's ability and use it at 70% potency for the rest of battle. Each use swaps to a different ability. (2 turn cooldown)"},
    "pacification":         {"name": "☮️ Pacification",         "cost": 38, "description": "Clear all buffs from your opponent — OR clear all debuffs/status effects from yourself. Your choice. (6 turn cooldown)"},
    "spirit_guide":         {"name": "👻 Spirit Guide",          "cost": 50, "description": "Summon a great spirit for 5 turns — strikes ~9% seq HP/turn, costs ~6% max SP/turn. 30% chance to possess you (stun) instead of attacking. (7 turn cooldown)"},
    "hurricane_of_light":   {"name": "🌪️ Hurricane of Light",   "cost": 60, "description": "Stab Sword of Dawn into ground (1 turn charge), then auto-releases next turn dealing 35-40% avg base HP damage. Evasion (distortion/phasing) only mitigates 50%. Backlash: 30% of your hit. 3 uses max. (12 turn cooldown)"},
    "pleasure_witch":       {"name": "💋 Pleasure Witch",        "cost": 25, "description": "Charm your opponent into skipping their turn. 40% chance to linger each round, dropping by 10% per round until it fades. (3 turn cooldown)"},
    "conspiracy":           {"name": "🕸️ Conspiracy",            "cost": 50, "description": "Guaranteed: one random effect always lands. Then each remaining effect has 33% to also hit — miss debuff (3 rounds), forced random ability (3 rounds), or 40 dmg + 60 SP drain. (5 turn cooldown)"},
    "scrolls_professor":    {"name": "📜 Scrolls Professor",     "cost": 50, "description": "Summon a scroll of semi-permanent affliction — randomly applies freeze/bleed/burn/flinch/sleep. Each round has a 40% chance to re-apply that same status. Re-use to swap the status. (4 turn cooldown)"},
    "artisan":              {"name": "⚗️ Artisan",               "cost": 40, "description": "Craft a weapon of moderate destruction from the materials around you — Bomb (50 damage), Sword (40 damage), or Rifle (35 damage + bleed). Each has a 33% chance. (2 turn cooldown)"},
    "calamity_priest":      {"name": "☄️ Calamity Priest",       "cost": 20, "description": "Foresee calamity and share the damage — 50%: split 50/50 | 40%: take 40, deal 60 | 5%: take 30, deal 70 | 5%: take 20, deal 80. (3 turn cooldown)"},
    "potions_professor":    {"name": "⚗️ Potions Professor",     "cost": 30, "description": "Brew a cure or poison — Cure: restore 40 HP + 15 SP (or remove 1 status + 10 HP + 5 SP if afflicted). Poison: deal 50 damage + drain 20 SP from opponent. (3 turn cooldown)"},
    "biologist":            {"name": "🧬 Biologist",             "cost": 40, "description": "Crossbreed effects for a random result — double status (paralysis+bleed), or summon a creature for 67 damage. (3 turn cooldown)"},
    "zombie":               {"name": "🧟 Zombie",                "cost": 30, "description": "Summon the recently deceased — 1–4 zombies (60%) or 5–7 zombies (40%), each dealing 10 damage. 10% chance to flinch. (3 turn cooldown)"},
    "devil_form":           {"name": "😈 Devil Form",            "cost": 30, "description": "Transform into a devil — +50% strength, SP and HP. 30% chance to lose your own turn to madness. Maintenance mode. (4 turn cooldown after cancel)"},
    "distortion":           {"name": "👑 Distortion",            "cost": 30, "description": "Distort the enemy's next attack — 100% dodge. 40% chance to reflect 60% of the damage back. If attack ≥60% of your base HP, only dodges (no reflect). (3 turn cooldown)"},
    "judge":                {"name": "⚖️ Judge",                 "cost": 30, "description": "Deliver a random verdict — Imprisonment (3 turns trapped, −25 SP, 7cd) | Flogging (15% current HP dmg, 2cd) | Death (40%: 60-90 dmg | 30%: 50% HP | 30%: miss)."},
    # Seq 5
    "spirit_thread":        {"name": "🧵 Spirit Thread Control",  "cost": 70, "description": "Seize opponent's spirit body threads — 10% stun chance per turn (+10%/round). Disrupted by critical hit or special attack. 5 turn cooldown."},
    "marionette_summon":    {"name": "🎭 Sacrifice Marionette",   "cost": 60, "description": "Sacrifice a beyonder ability to summon a marionette — 50% it attacks each round, 50% it intercepts an attack for you. Lasts 3 turns. 3 turn cooldown."},
    "mental_theft":         {"name": "🧠 Mental Theft",           "cost": 50, "description": "Remove the thought of attack from opponent's mind — they lose their turn. 3 turn cooldown."},
    "rewards_theft":        {"name": "🏆 Rewards Theft",          "cost": 60, "description": "Steal an active buff from your opponent for yourself. 4 turn cooldown."},
    "blink":                {"name": "⚡ Blink",                  "cost": 35, "description": "Escape all stuns + 3-turn stun immunity, then surprise attack for 80 dmg. 6 turn cooldown."},
    "travellers_door":      {"name": "🚪 Traveller's Door",       "cost": 80, "description": "Banish opponent to a remote location (10% to deal 20 dmg) and fully regenerate all resources. 2 uses per match. Can escape battle if uses remain. 6 turn cooldown."},
    "dream_visitation":     {"name": "🌙 Dream Visitation",       "cost": 50, "description": "Enter opponent's dreams — 50% chance to force them to flee or drain 40% of their SP. 4 turn cooldown."},
    "dream_alteration":     {"name": "💭 Dream Alteration",       "cost": 40, "description": "Influence opponent's thoughts — lower their attack accuracy by 5% each round for the rest of battle. 1 use only."},
    "purification_halo":    {"name": "☀️ Purification Halo",      "cost": 65, "description": "Massive sun halo effect — boosts SP of non-death/criminal/chained allies for 3 rounds; deals 50/100/150 dmg + 5-round burn to those pathways respectively. 5 turn cooldown."},
    "light_of_holiness":    {"name": "✨ Light of Holiness",      "cost": 90, "description": "Giant ray of holy light — 70 dmg, skip 1 turn + 2-round burn. Chained pathway takes double damage. Revokes conditional criminal/death curses. 4 turn cooldown."},
    "singing":              {"name": "🎵 Singing",                "cost": 80, "description": "Ocean Songster's voice — 33% each: Spirit Interference (stun 2 turns, −30 SP), Vocal Enlightenment (+15% spirit/+20% strength for 3 turns), or Sound-Wave Explosion (40 dmg + 1 turn stun). 3 turn cooldown."},
    "arrow_of_lightning":   {"name": "⚡ Arrow of Lightning",     "cost": 65, "description": "Charge 1 turn then unleash a lightning arrow for 90 damage. 5 turn cooldown."},
    "grazing":              {"name": "🌾 Grazing",                "cost": 40, "description": "Copy 3 abilities from opponent's pathway at the start of battle. Copies last 3 matches (max 5 total). 1 use per round."},
    "ability_bag":          {"name": "🎒 Ability Bag",            "cost": 0,  "description": "Activate a grazed ability — costs the same SP as the original, same cooldowns apply."},
    "combination_spell":    {"name": "🔮 Combination Spell",      "cost": 0,  "display_cost": "varies", "description": "Fuse ability pairs for powerful combo spells: Lightning Curse, Spirit Seal, Greater Rejuvenation, or Cursed Betrayal. Activates automatically when correct ability sequence is used."},
    "spiritual_suppression":{"name": "🦷 Spiritual Suppression",  "cost": 45, "description": "Use spirits locked in your teeth — negate any ability costing more than 50 SP for 2 turns. 2 total uses with 4 turn cooldown."},
    "spiritual_takeover":   {"name": "👻 Spiritual Takeover",     "cost": 50, "description": "Plant a spirit trap — on trigger, opponent takes 15 dmg/round until battle ends. Negated by purification halo/twilight armour. 1 use only."},
    "dragged_to_hell":      {"name": "🔥 Dragged to Hell",        "cost": 50, "description": "Rip open the gateway in your glabella — deal 70 damage and drain 15 SP from yourself each round for the rest of the match. 1 use only."},
    "evil_sealing":         {"name": "💀 Evil Sealing",           "cost": 40, "display_cost": "40 SP/round", "description": "Compress a wraith into a spiritual bullet — gain a random debuff but charge up +30 dmg/round with 10 recoil. Activate again to fire. Deactivates for rest of match after firing. "},
    "protection":           {"name": "🛡️ Protection",            "cost": 40, "description": "Give up offense — deal 50% less damage while active. Damage taken is reduced by 80 (drops to 10 for 1 turn after any attack). Choose: Active (until next attack) or Maintenance (toggle, 3 turn cooldown on cancel)."},
    "dawn_armor":           {"name": "🌅 Dawn Armor",             "cost": 30, "description": "Don radiant armor — +20% max HP and +10% strength for the duration. Cannot be cancelled once cast (like Pugilist). 3 turn cooldown if dispelled."},
    "disease_propagation":  {"name": "🦠 Disease Propagation",    "cost": 50, "description": "Unleash a plague — 15 HP damage + 1% extra dmg per round, grows stronger until battle ends. 1 use only. Stacks with other effects."},
    "thread_storm":         {"name": "🕸️ Thread Storm",           "cost": 40, "description": "Wrap opponent in steel-like threads — skip 1-2 of their turns + 2-round bleed per turn skipped. 4 turn cooldown."},
    "cull":                 {"name": "☠️ Cull",                   "cost": 70, "description": "Strike any part of the body as a critical weak point — bonus damage on next attack (scales with sequence). 4 turn cooldown, 3 total uses."},
    "weakness_development": {"name": "🎯 Weakness Development",   "cost": 50, "description": "Halve the power of one opponent skill OR place a 20% damage debuff on them. 2 total uses with 3 turn cooldown."},
    "stellar_self":         {"name": "⭐ Stellar Self",           "cost": 0,  "description": "Form a substitute fused with your essence — for the next 2 turns, you cannot be hit by beyonders of your sequence or lower."},
    "star_pillar":          {"name": "💫 Star Pillar",            "cost": 40, "description": "Channel magnificent starlight into a disintegrating pillar — deal 45 damage. 3 turn cooldown."},
    "fire_storm":           {"name": "☄️ Fire Storm",             "cost": 0,  "display_cost": "5 turn cd", "description": "Redirect a falling meteor — 40% chance for 50 damage, 60% chance for 30 damage. 5 turn cooldown."},
    "star_of_curses":       {"name": "🌟 Star of Curses",         "cost": 50, "description": "Deal 5 damage then curse opponent for 5 rounds — each round applies one of: Slow (−30% accuracy), Weakness (−30% damage), Mutation (30 dmg), or Trauma (flinch). 1 use only."},
    "curse_of_misfortune":  {"name": "🎲 Curse of Misfortune",    "cost": 50, "description": "Transfer misfortune via a standard hit — for 5 rounds their accuracy is 10–40% with 20 recoil on each of their attacks. 5 turn cooldown."},
    "active_luck_boost":    {"name": "🍀 Active Luck Boost",      "cost": 70, "description": "Focus all gathered luck into 2 rounds — 90% chance to dodge attacks, best probability outcomes, buffs +1 duration, and 10% chance events deal 40 dmg to opponent. 1 use only."},
    "artificial_moon":      {"name": "🌕 Artificial Moon",        "cost": 50, "description": "Channel the red moon — +50% SP and +40% beyonder ability damage for 5 rounds. 1 use only."},
    "scarlet_transformation":{"name": "🌙 Scarlet Transformation","cost": 50, "description": "Transform into moonlight — all physical and special attacks are completely useless against you for 4 turns. 7 turn cooldown."},
    "spirit_animal_transformation":{"name": "🐻 Spirit Animal Transformation","cost": 35,"display_cost":"35 SP activate, 5 SP/round","description": "Transform into a giant bear — +40% HP and +50% physical strength. Maintained each round for 5 SP. 10 turn cooldown or large SP cost to restart."},
    "wrath_of_nature":      {"name": "🌿 Wrath of Nature",        "cost": 0,  "display_cost": "5 turn cd", "description": "Command the forest — 30% swamp trap (3 turns), 30% wooden coat (+20 HP + thorn recoil 5 dmg/hit), 40% poison vine (4 turns, 15 dmg/turn). 5 turn cooldown."},
    "possession":           {"name": "👁️ Possession",             "cost": 60, "description": "Enter opponent's body as a wraith — 20 dmg/turn for 3-5 turns. Sun pathway Seq 5+ is fully immune. 6 turn cooldown."},
    "wraith_shriek":        {"name": "👻 Wraith Shriek",          "cost": 50, "description": "Disturbing shriek — strip 40 SP from opponent and apply 5-round bleed. Your own accuracy is −40% for 2 turns after. 4 turn cooldown."},
    "desire_explosion":     {"name": "💥 Desire Explosion",       "cost": 0,  "display_cost": "Requires vile crime", "description": "Tear off a horn to trigger a mental explosion — 50 dmg + stun: 50% 1 turn, 40% 2 turns, 10% 3 turns. 2 uses, 5 turn cooldown. Requires a vile crime first."},
    "desire_symbiosis":     {"name": "😈 Desire Symbiosis",       "cost": 0,  "description": "Choose one emotion — Wrath (+50% attack, −20% accuracy), Lust (100% accuracy, −60% strength), or Envy (+70% SP, −30% strength & accuracy). 1 use only."},
    "prohibition":          {"name": "🚫 Prohibition",            "cost": 65, "description": "Seal one of opponent's beyonder abilities for the rest of the match. Reactivate to reset the seal. 5 turn cooldown, 2 total uses."},
    "punishment":           {"name": "⛓️ Punishment",             "cost": 60, "description": "Activate on target — all physical abilities used against them are boosted 45%, and they become petrified (−20% offensive ability power). 4 turn cooldown."},
    "disciplinary_strike":  {"name": "⚖️ Disciplinary Strike",    "cost": 65, "description": "Channel disciplinary authority — deal 60 damage and apply a 3-turn judgement debuff that reduces opponent's damage by 25% and strips one active buff. 4 turn cooldown."},
    "disorder":             {"name": "🌀 Disorder",            "cost": 35, "description": "–35 SP, 5 CD. 25% accuracy decrease. Random: descend enemy's mind into chaos (7-10% HP self-damage), or skip 2 turns, or −20% strength. 5 turn cooldown."},
    "gift_of_corruption":   {"name": "🎁 Gift of Corruption",  "cost": 35, "description": "–35 SP, 4 CD. Transfer all your negative status effects to the enemy. 10% chance of failure — if it fails, you are stunned 1 turn by a higher power. 4 turn cooldown."},
    "sacred_conviction":    {"name": "🌟 Sacred Conviction",      "cost": 60, "description": "Invoke the unyielding power of order — cleanse all debuffs on yourself, gain a 20% damage boost for 3 turns, and deal 40 damage to the opponent through righteous force. 5 turn cooldown."},

}

# ── Stats (Pathway base + Sequence-unlocked allocation points) ──
# Base combat stats are fixed per Pathway (some pathways run hotter in one
# stat than others). As a Beyonder advances Sequence — Seq 9 down to Seq 0 —
# they unlock a growing pool of stat points, which they spend themselves via
# the dropdown in /stats_user.

COMBAT_STAT_KEYS = ["health", "attack", "luck", "spirituality", "speed"]
DEFAULT_BASE_COMBAT_STATS = {"health": 2, "attack": 2, "luck": 2, "spirituality": 2, "speed": 2}

# Fixed base stats per pathway at Seq 9 — same for every player in that pathway.
# (Spirit has been folded into Spirituality — one stat, no redundancy.)
PATHWAY_BASE_COMBAT_STATS = {
    "Fool":             {"health": 2, "attack": 2, "luck": 2, "spirituality": 5, "speed": 2},
    "Error":            {"health": 2, "attack": 2, "luck": 2, "spirituality": 4, "speed": 3},
    "Door":             {"health": 2, "attack": 2, "luck": 2, "spirituality": 4, "speed": 3},
    "Visionary":        {"health": 2, "attack": 2, "luck": 2, "spirituality": 5, "speed": 2},
    "Sun":              {"health": 2, "attack": 2, "luck": 3, "spirituality": 4, "speed": 2},
    "Tyrant":           {"health": 2, "attack": 3, "luck": 2, "spirituality": 4, "speed": 2},
    "Hanged Man":       {"health": 2, "attack": 2, "luck": 2, "spirituality": 5, "speed": 2},
    "White Tower":      {"health": 2, "attack": 2, "luck": 2, "spirituality": 5, "speed": 2},
    "Darkness":         {"health": 2, "attack": 2, "luck": 2, "spirituality": 4, "speed": 3},
    "Death":            {"health": 3, "attack": 2, "luck": 2, "spirituality": 4, "speed": 2},
    "Twilight Giant":   {"health": 2, "attack": 3, "luck": 2, "spirituality": 4, "speed": 2},
    "Demoness":         {"health": 2, "attack": 2, "luck": 2, "spirituality": 4, "speed": 3},
    "Red Priest":       {"health": 2, "attack": 3, "luck": 2, "spirituality": 4, "speed": 2},
    "Hermit":           {"health": 2, "attack": 2, "luck": 2, "spirituality": 5, "speed": 2},
    "Paragon":          {"health": 2, "attack": 2, "luck": 3, "spirituality": 4, "speed": 2},
    "Wheel of Fortune": {"health": 2, "attack": 2, "luck": 3, "spirituality": 4, "speed": 2},
    "Moon":             {"health": 3, "attack": 2, "luck": 2, "spirituality": 4, "speed": 2},
    "Mother":           {"health": 3, "attack": 2, "luck": 2, "spirituality": 4, "speed": 2},
    "Chained":          {"health": 3, "attack": 2, "luck": 2, "spirituality": 4, "speed": 2},
    "Abyss":            {"health": 2, "attack": 3, "luck": 2, "spirituality": 4, "speed": 2},
    "Black Emperor":    {"health": 2, "attack": 2, "luck": 2, "spirituality": 5, "speed": 2},
    "Justiciar":        {"health": 2, "attack": 3, "luck": 2, "spirituality": 4, "speed": 2},
}

# Cumulative pool of allocatable stat points unlocked at each Sequence tier.
# Seq 9 = 0 (nothing unlocked yet); Seq 0 (Sovereign-adjacent) = full pool.
SEQ_STAT_POINTS = {
    9: 0,
    8: 6,
    7: 14,
    6: 24,
    5: 36,
    4: 52,
    3: 70,
    2: 90,
    1: 115,
    0: 145,
}

# Small random chance for the winner of a fight to earn a bonus stat point
# on top of whatever their Sequence pool already grants — a bit of "gamer"
# unpredictability without making stats purely EXP/win-farmable.
BONUS_STAT_POINT_CHANCE = 0.15  # 15% chance per win


# ── /guide info pages ─────────────────────────────────────
INFO_PAGES = [
    # Page 1 — Overview + Combat
    discord.Embed(
        title="𓂃 Lord Mysteries — Bot Guide  (1/5)",
        description=(
            "Welcome to the server, Beyonder. Battle, advance your Sequence, "
            "build wealth, and gamble your soli in the world of Lord of the Mysteries."
        ),
        color=0x9B59B6
    ).add_field(
        name="⚔️ Duelling",
        value=(
            "`/fight @user` — Challenge someone. They have **60s** to accept.\n"
            "Highest **Speed** goes first. Turns alternate until one side falls.\n\n"
            "**Actions each turn:**\n"
            "• **Hit** — Physical strike. Scales with Attack, restores a little SP.\n"
            "• **Special** — Spirit-enhanced bullet. Hits harder, costs SP.\n"
            "• **Beyonder Ability** — Your Sequence's unique power.\n"
            "• **Ability** — A purchased power from `/shop`.\n"
            "• **Flee** — Attempt to escape. Not always successful."
        ),
        inline=False
    ).add_field(
        name="❤️ HP & ✨ SP",
        value=(
            "**HP** — Your health. Reach 0 and you lose.\n"
            "**SP** (Spirit Points) — Spent on abilities, regenerates each turn.\n"
            "Base HP & SP scale with your **Pathway** and **Sequence**."
        ),
        inline=False
    ).add_field(
        name="🚪 Forfeiting & Records",
        value=(
            "`/leave` — Forfeit at any time.\n"
            "`/winstreak [@user]` — Check your battle record, win rate, and active streak."
        ),
        inline=False
    ).set_footer(text="◀ Prev  ·  𓂃  ·  Page 1 of 5  |  Next ▶"),

    # Page 2 — Stats + Abilities
    discord.Embed(
        title="𓂃 Lord Mysteries — Bot Guide  (2/5)",
        description="Everything about your Beyonder stats and abilities.",
        color=0x9B59B6
    ).add_field(
        name="ᯓ★ Combat Stats",
        value=(
            "Five stats shape every Beyonder:\n"
            "❤️ **Health** — Increases your max HP.\n"
            "🗡️ **Attack** — Scales your Hit and Special damage.\n"
            "🍀 **Luck** — Favour with probability-based attacks.\n"
            "✨ **Spirituality** — Increases max SP, boosts Special damage, and resists debuffs/status effects.\n"
            "⚡ **Speed** — Who goes first each round.\n\n"
            "Base stats come from your **Pathway**; bonus points unlock as you advance **Sequence**. "
            "Use `/stats_user` to view and allocate yours."
        ),
        inline=False
    ).add_field(
        name="🔮 Beyonder Abilities",
        value=(
            "Each Pathway grants abilities per Sequence rank:\n"
            "• **Seq 9** — First ability unlocked.\n"
            "• **Seq 8** — Second unlocked. Seq 9 ability still accessible.\n"
            "• **Seq 7** — Third unlocked, and so on upward.\n"
            "Use `/pathway_ability` to browse every Pathway's full list."
        ),
        inline=False
    ).add_field(
        name="🏪 Shop Abilities & Chairs",
        value=(
            "Visit `/shop` to open the unified shop with two categories:\n"
            "✨ **Spell Shop** — Buy extra combat abilities with soli. Available in any fight."
        ),
        inline=False
    ).set_footer(text="◀ Prev  ·  𓂃  ·  Page 2 of 5  |  Next ▶"),

    # Page 3 — Economy + Gambling
    discord.Embed(
        title="𓂃 Lord Mysteries — Bot Guide  (3/5)",
        description="Soli is the lifeblood of the realm. Earn it, spend it, lose it.",
        color=0x9B59B6
    ).add_field(
        name="💰 Economy — Soli",
        value=(
            "You start with **1,000 soli**. Earn more through battles, dailies, and gambling.\n\n"
            "`/daily` — **150 XP** + **1,000 soli** every 24h. Streak adds **+20 soli/day**.\n"
            "`/beg` — **67 or 69 soli** every 2 hours.\n"
            "`/balance` — Check your soli (or anyone else's).\n"
            "`/share @user <amount>` — Send soli to another Beyonder.\n"
            "`/money` — Admin-only give/take/wipe."
        ),
        inline=False
    ).add_field(
        name="🎰 The Shameless Corner — Gambling",
        value=(
            "The fastest path to riches — or ruin.\n\n"
            "`/coinflip <amount>` — Heads or tails. 50/50 to double up.\n"
            "`/slots <amount>` — Spin the reels for multiplied payouts.\n"
            "`/blackjack <amount>` — Beat the dealer to **21** for **2.5×**.\n\n"
            "All gambling has a **5-second cooldown** between uses."
        ),
        inline=False
    ).add_field(
        name="🏆 Betting on Duels",
        value=(
            "`/bet` — Wager on an active fight within the **first 3 turns**.\n"
            "Pick the winner correctly → receive **double** your bet back."
        ),
        inline=False
    ).set_footer(text="◀ Prev  ·  𓂃  ·  Page 3 of 5  |  Next ▶"),

    # Page 4 — Items
    discord.Embed(
        title="𓂃 Lord Mysteries — Bot Guide  (4/5)",
        description="Items — the material rewards of a Beyonder's journey.",
        color=0x9B59B6
    ).add_field(
        name="🎒 Items",
        value=(
            "Items drop from events and battles.\n\n"
            "`/item` — View your inventory and use items you own.\n"
            "Admins can grant items with `/give_item`."
        ),
        inline=False
    ).set_footer(text="◀ Prev  ·  𓂃  ·  Page 4 of 5  |  Next ▶"),

    # Page 5 — Server Layout + Battle Rewards
    discord.Embed(
        title="𓂃 Lord Mysteries — Bot Guide  (5/5)",
        description="Server layout and how the world rewards victorious Beyonders.",
        color=0x9B59B6
    ).add_field(
        name="📡 Server Sections",
        value=(
            "🏟️ **Battlefields** — Duel channels that keep main chat clean.\n"
            "💡 **Suggestions** — Submit ideas for server or bot improvements.\n"
            "📈 **Advancement Feed** — Notifies when anyone advances their Sequence.\n"
            "📜 **Pathway Abilities** — Updated weekly with new Sequence abilities.\n"
            "💬 **Bot Channel** — Main hub for commands, chat, and post-duel rage."
        ),
        inline=False
    ).add_field(
        name="💸 Battle Rewards",
        value=(
            "Win a duel and earn soli based on your opponent's Sequence:\n"
            "Seq 9 → **1,000** | Seq 8 → **2,000** | Seq 7 → **3,000**\n"
            "Seq 6 → **4,000** | Seq 5 → **5,000**\n\n"
            "Winners also gain **XP** based on the opponent's Sequence rank."
        ),
        inline=False
    ).add_field(
        name="🔑 Quick Reference",
        value=(
            "`/profile` · `/leaderboard` · `/pathway_ability` · `/stats_user`\n"
            "`/shop` · `/item` · `/winstreak` · `/daily` · `/pray`"
        ),
        inline=False
    ).set_footer(text="◀ Prev  ·  𓂃  ·  Page 5 of 5  |  Use /pathway_ability • /shop • /stats_user"),
]


# ── Ability metadata (cooldowns etc. keyed by ability id) ─
ABILITIES = {
    "leodero": {
        "name": "⚡ LEODERO", "cost": 22,
        "description": "Divine lightning — deals 30-40 damage but 10% rebounds to you.",
        "type": "leodero", "pct_min": 0.090, "pct_max": 0.145,
        "price": 10000, "purchasable": True,
    },
    "heal": {
        "name": "💚 Sacred Heal", "cost": 25,
        "description": "Mend your wounds with spiritual energy.",
        "type": "heal", "pct_min": 0.073, "pct_max": 0.109,
        "price": 10000, "purchasable": True,
    },
    "drain": {
        "name": "🌑 Soul Drain", "cost": 15,
        "description": "Siphon the enemy's life force and convert it into spirit.",
        "type": "drain", "pct_min": 0.045, "pct_max": 0.073,
        "price": 10000, "purchasable": True,
    },
    "shield": {
        "name": "🛡️ Spirit Shield", "cost": 20,
        "description": "Erect a barrier to absorb the next blow.",
        "type": "shield",
        "price": 10000, "purchasable": True,
    },
    "debuff": {
        "name": "🔻 Debuff", "cost": 70,
        "description": "Weaken the enemy — reduces their damage by 20% for 5 turns. One-time use per battle.",
        "type": "debuff",
        "price": 15000, "purchasable": True,
    },
    "ritual": {
        "name": "🕯️ Ritualistic Magic", "cost": 18,
        "description": "Channel ancient rites — choose Strength, Divination, or Blessing.",
        "type": "ritual",
        "price": 12000, "purchasable": True,
    },
}


# ── Sequence win rewards (soli) ───────────────────────────
SEQ_WIN_REWARD = {
    9: 10000,
    8: 20000,
    7: 30000,
    6: 40000,
    5: 50000,
    4: 60000,
    3: 70000,
    2: 80000,
    1: 90000,
    0: 100000,
}



# ── Pathway flavour text / colours / stat display names ──
PATHWAY_FLAVOUR = {
    "Fool":            "A wildcard of fate — balanced across all stats, unpredictable in battle.",
    "Door":            "Masters of space and movement. Exceptional Speed lets them strike first.",
    "Error":           "Elusive swindlers who slip through gaps. High Speed, tricky to pin down.",
    "Hermit":          "Scholars who blend intellect with spirituality. High Spirituality and SP efficiency.",
    "Paragon":         "Gifted with extraordinary Spirit — they resist debuffs better than anyone.",
    "Red Priest":      "Aggressive hunters. High Attack and Speed make them dangerous openers.",
    "Demoness":        "Silent assassins. They move fast and strike vital points without warning.",
    "Abyss":           "Pure, unapologetic offence. The highest Attack of any Seq 9 Pathway.",
    "Chained":         "Resilient brawlers. High HP means they absorb punishment and keep swinging.",
    "Twilight Giant":  "Combat-born titans. Enormous HP and strong Attack — built to outlast foes.",
    "Darkness":        "Night-walkers with strong spirituality, lethal in prolonged fights.",
    "Death":           "Durable and unsettling. High HP and eerie SP abilities wear opponents down.",
    "Tyrant":          "Raw brute force. High Attack stat — they hit hard from the opening blow.",
    "Sun":             "A rare jack-of-all-trades with bonuses across every stat. Brilliant and bold.",
    "Hanged Man":      "Secretive seers. Exceptional Spirituality fuels powerful SP-based abilities.",
    "White Tower":     "The sharpest minds on any Pathway. Unmatched Spirit and debuff resist.",
    "Visionary":       "Perceptive and cerebral. High Spirit lets them read and counter enemy moves.",
    "Mother":          "Enduring nurturers. The highest base HP — difficult to bring down.",
    "Moon":            "Adaptable and lucky. Decent Luck stat with surprising combat flexibility.",
    "Wheel of Fortune":"Fortune's favourite. The highest Luck stat — probability attacks love them.",
    "Black Emperor":   "Sharp commanders with high Spirit. They control the pace of any fight.",
    "Justiciar":       "Enforcers with strong Attack and HP. They deal justice swiftly and firmly.",
}

PATHWAY_COLOURS = {
    "Fool": 0x9B59B6, "Door": 0x3498DB, "Error": 0x2ECC71,
    "Hermit": 0x1ABC9C, "Paragon": 0xF1C40F, "Red Priest": 0xE74C3C,
    "Demoness": 0xE91E8C, "Abyss": 0x2C3E50, "Chained": 0x795548,
    "Twilight Giant": 0xF39C12, "Darkness": 0x4A148C, "Death": 0x607D8B,
    "Tyrant": 0xB71C1C, "Sun": 0xFFD600, "Hanged Man": 0x37474F,
    "White Tower": 0xECEFF1, "Visionary": 0x0288D1, "Mother": 0x43A047,
    "Moon": 0xB0BEC5, "Wheel of Fortune": 0xFFA000, "Black Emperor": 0x212121,
    "Justiciar": 0x1565C0,
}

STAT_DISPLAY_NAMES = {
    "health":       "❤️ Health",
    "attack":       "🗡️ Attack",
    "luck":         "🍀 Luck",
    "spirituality": "✨ Spirituality",
    "speed":        "⚡ Speed",
}

BAR_STAT_MAX = 15  # nominal cap for combat-stat bar visualization


# ── Stat allocation labels ────────────────────────────────
STAT_LABELS = {
    "health": ("Health", "❤️"),
    "attack": ("Attack", "🗡️"),
    "luck": ("Luck", "🍀"),
    "spirituality": ("Spirituality", "✨"),
    "speed": ("Speed", "⚡"),
}



# ── Chair / furniture item flavour ────────────────────────
CHAIR_XP    = 1000
CHAIR_USE_TEXTS = [
    "{user} sat on the chair and pondered on life.\n\n*The wood creaked softly beneath them. Somewhere between one breath and the next, the mysteries of the universe felt just a little less mysterious.*",
    "{user} settled into the chair and stared at nothing in particular.\n\n*The world kept moving. The chair did not. For a moment, that felt like wisdom.*",
    "{user} sat. Thought. Sat some more.\n\n*Great philosophers have paced. Great Beyonders, it turns out, prefer to sit. The XP would suggest they were right.*",
]
ALL_CHAIR_NAMES = {
    "Foolish Chair","Speedy Chair","Mischievous Chair","Holy Chair","Underwater Chair",
    "Clever Chair","Bloody Chair","Invisible Chair","Destructive Chair","Curvy Chair",
    "Rotten Chair","Black Chair","Dead Chair","Mechanical Chair","Mystical Chair",
    "Golden Chair","Balancing Chair","Throne Chair","Restrained Chair","Electro Chair",
    "Caring Chair","Luminous Chair","Healthy Chair",
}

# ── Item use view ─────────────────────────────────────────────────────────────


