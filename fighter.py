# ============================================================
# FIGHTER — the per-battle combat unit: stat rolling/allocation,
# Sequence/pathway-scaled combat math (damage, healing, HP/SP), and the
# Fighter class itself, which snapshots a Discord member's pathway,
# sequence, role-derived combat stats and ability kit for one duel.
# ============================================================
import random
import discord

from game_data import (
    SEQ_BASE_STATS, SEQ_AVG_HP, PATHWAY_BASE_STATS,
    COMBAT_STAT_KEYS, DEFAULT_BASE_COMBAT_STATS, PATHWAY_BASE_COMBAT_STATS,
    SEQ_STAT_POINTS, BONUS_STAT_POINT_CHANCE, SPELL_DEFENCE_ROLES,
    PATHWAY_FLAVOUR, PATHWAY_COLOURS, STAT_DISPLAY_NAMES, BAR_STAT_MAX,
)
from roles import (
    get_seq_number, get_pathway_name, get_user_role, get_pathway_family,
    get_user_role_modifier, get_role_ability_key, get_ability_info,
    get_all_user_roles, get_all_roles_from_db_and_discord,
)
from persistence import player_stats, save_stats
from economy import get_pounds, get_economy

# ── Combat math ────────────────────────────────────────────
def apply_healing(fighter: "Fighter", amount: int) -> int:
    """Apply healing respecting no_heal_permanent flag."""
    if getattr(fighter, "_no_heal_permanent", False):
        amount = 0
    elif getattr(fighter, "_catastrophe_dot_turns", 0) > 0 or getattr(fighter, "_inquisition_no_heal", 0) > 0:
        amount = amount // 2  # healing halved during catastrophe/inquisition
    old_hp = fighter.hp
    fighter.hp = min(fighter.max_hp, fighter.hp + amount)
    return fighter.hp - old_hp


def seq_gap_check(attacker: "Fighter", defender: "Fighter", max_gap: int = 2) -> tuple[bool, int]:
    """
    LOTM rule: weaker Beyonders (higher seq number) cannot easily impose their will
    on stronger ones (lower seq number). Returns (allowed, gap).
    gap = atk_seq - def_seq  (positive = attacker is weaker)
    """
    atk_seq = get_seq_number(attacker.role) if attacker.role else 9
    def_seq = get_seq_number(defender.role) if defender.role else 9
    gap = atk_seq - def_seq
    return gap <= max_gap, gap


def seq_dmg(attacker: "Fighter", pct: float) -> int:
    """Return a damage value scaled to the attacker's sequence average HP.
    pct is a fraction (e.g. 0.30 = 30%).  Minimum 1."""
    seq = get_seq_number(attacker.role) if attacker.role else 9
    base = SEQ_AVG_HP.get(seq, 220)
    return max(1, int(base * pct))

def seq_val(fighter: "Fighter", base_at_seq9: float) -> int:
    """Scale a flat value (balanced for Seq 9) up to the fighter's actual sequence.
    e.g. seq_val(f, 15) → 15 at seq9, 22 at seq7, 33 at seq5, 53 at seq3."""
    seq = get_seq_number(fighter.role) if fighter.role else 9
    avg_hp = SEQ_AVG_HP.get(seq, 270)
    seq9_hp = SEQ_AVG_HP[9]  # 270
    return max(1, int(base_at_seq9 * (avg_hp / seq9_hp)))

def seq_heal(fighter: "Fighter", base_at_seq9: float) -> int:
    """Scale a flat heal value to the fighter's sequence."""
    return seq_val(fighter, base_at_seq9)


# ── Stat rolling / allocation ────────────────────────────
def get_scaled_description(ability_key: str, fighter) -> str:
    """Return ability description with flat damage values scaled to the fighter's sequence."""
    info, _ = get_ability_info(ability_key)
    if not info:
        return ""
    desc = info.get("description", "")
    if fighter is None:
        return desc
    replacements = [
        ("100 damage", f"{seq_val(fighter, 100)} damage"),
        ("100 dmg",    f"{seq_val(fighter, 100)} dmg"),
        ("90 damage",  f"{seq_val(fighter, 90)} damage"),
        ("80 damage",  f"{seq_val(fighter, 80)} damage"),
        ("80 dmg",     f"{seq_val(fighter, 80)} dmg"),
        ("70 damage",  f"{seq_val(fighter, 70)} damage"),
        ("70 dmg",     f"{seq_val(fighter, 70)} dmg"),
        ("60 damage",  f"{seq_val(fighter, 60)} damage"),
        ("60 dmg",     f"{seq_val(fighter, 60)} dmg"),
        ("50 damage",  f"{seq_val(fighter, 50)} damage"),
        ("50 dmg",     f"{seq_val(fighter, 50)} dmg"),
        ("45 damage",  f"{seq_val(fighter, 45)} damage"),
        ("40 damage",  f"{seq_val(fighter, 40)} damage"),
        ("40 dmg",     f"{seq_val(fighter, 40)} dmg"),
        ("30 damage",  f"{seq_val(fighter, 30)} damage"),
        ("30 dmg",     f"{seq_val(fighter, 30)} dmg"),
        ("20 damage",  f"{seq_val(fighter, 20)} damage"),
        ("15 damage",  f"{seq_val(fighter, 15)} damage"),
        ("15 HP",      f"{seq_val(fighter, 15)} HP"),
        ("20 HP",      f"{seq_val(fighter, 20)} HP"),
        ("10 damage",  f"{seq_val(fighter, 10)} damage"),
        ("5 damage",   f"{seq_val(fighter, 5)} damage"),
        ("+50 bonus",  f"+{seq_val(fighter, 50)} bonus"),
        ("+100 bonus", f"+{seq_val(fighter, 100)} bonus"),
    ]
    for pattern, replacement in replacements:
        desc = desc.replace(pattern, replacement, 1)
    return desc

def roll_stats(member: discord.Member = None):
    """Return pathway-fixed base stats — same for everyone in that pathway at Seq 9."""
    if member:
        role = get_user_role(member)
        pathway = get_pathway_name(role) if role else None
        if pathway and pathway in PATHWAY_BASE_COMBAT_STATS:
            return dict(PATHWAY_BASE_COMBAT_STATS[pathway])
    return dict(DEFAULT_BASE_COMBAT_STATS)

def get_or_create_stats(user_id, member: discord.Member = None):
    """Fetch (or initialize) a player's stat record.
    Record shape: {health, attack, luck, spirituality, speed, points, points_granted_total}
    `points` is the player's unspent allocation pool; it grows automatically
    as their Sequence advances (Seq 9 -> Seq 0), tracked via points_granted_total
    so we only ever grant the *new* points unlocked since we last checked.
    """
    role = get_user_role(member) if member else None
    seq = get_seq_number(role) if role else 9
    target_total = SEQ_STAT_POINTS.get(seq, 0)

    if user_id in player_stats:
        stats = player_stats[user_id]
        changed = False
        # Legacy migration: 'iq' and 'spirit' have both been folded into 'spirituality'
        if "iq" in stats:
            stats["spirituality"] = stats.get("spirituality", 2) + stats.pop("iq")
            changed = True
        if "spirit" in stats:
            stats["spirituality"] = stats.get("spirituality", 2) + stats.pop("spirit")
            changed = True
        # Drop fields from the old generic leveling system, if present
        if "level" in stats or "exp" in stats:
            stats.pop("level", None)
            stats.pop("exp", None)
            changed = True
        # Backfill combat stat keys for older records, using pathway base as the floor
        base_pathway_stats = roll_stats(member)
        for key in COMBAT_STAT_KEYS:
            if key not in stats:
                stats[key] = base_pathway_stats[key]
                changed = True
        if "points" not in stats:
            stats["points"] = 0
            changed = True
        if "points_granted_total" not in stats:
            stats["points_granted_total"] = 0
            changed = True
        # Grant any new points unlocked by Sequence advancement since we last saw this player
        if target_total > stats["points_granted_total"]:
            gained = target_total - stats["points_granted_total"]
            stats["points"] += gained
            stats["points_granted_total"] = target_total
            changed = True
        if changed:
            save_stats()
        return stats

    new_stats = roll_stats(member)
    new_stats["points"] = target_total
    new_stats["points_granted_total"] = target_total
    player_stats[user_id] = new_stats
    save_stats()
    return player_stats[user_id]

def get_effective_stats(user_id, member: discord.Member = None):
    """Returns the player's current combat stats (health/attack/luck/
    spirituality/speed) — Pathway base plus whatever they've
    manually allocated from their Sequence-unlocked point pool."""
    record = get_or_create_stats(user_id, member)
    return {k: record[k] for k in COMBAT_STAT_KEYS}

def stat_bar(value, max_value=7):
    filled = round((value / max(1, max_value)) * 8)
    filled = max(0, min(8, filled))
    return "▰" * filled + "▱" * (8 - filled)


# ── Fighter ───────────────────────────────────────────────
class Fighter:
    def __init__(self, member: discord.Member):
        self.user = member
        self.stats = get_effective_stats(member.id, member)
        self.role = get_user_role(member)
        self.all_roles = get_all_user_roles(member)  # all pathway roles (Seq 9, Seq 8, etc.)
        self.role_ability = get_role_ability_key(member)
        self.has_spell_defence = self.role in SPELL_DEFENCE_ROLES

        # Sequence-based base stats — use pathway-specific values if available
        seq = get_seq_number(self.role) if self.role else 9
        pathway = get_pathway_name(self.role) if self.role else ""
        base = PATHWAY_BASE_STATS.get((pathway, seq)) or SEQ_BASE_STATS.get(seq, SEQ_BASE_STATS[9])
        self.base_hp = base["hp"]
        self.max_hp = base["hp"] + self.stats["health"] * 6
        self.hp = self.max_hp
        self.max_sp = base["sp"] + self.stats["spirituality"] * 2
        self.sp = self.max_sp

        # Status effects
        self.bleed = 0
        self.paralysis = 0
        self.slumber = 0
        self.freeze = 0
        self.freeze_weakened = False
        self.burn = 0
        self.defence_reduction = 0.0
        self.status_immune = 0      # turns of immunity (Doctor treatment)
        self.supernatural_resist_active = False  # Pugilist: permanent immunity, 40 self-dmg per attempt
        self.stun_block = False     # Archaeologist stun shield

        # Buffs and states
        self.strength_boost = 0
        self.divination_dodge = False
        self._seer_dodge_chance = None
        self.divination_dodge_turns = 0        # how many turns divination dodge lasts
        self._pending_divination_msg = ""      # set by apply_damage when divination dodges
        self.seer_divination_cooldown = 0      # 3 turn cooldown
        self.hurricane_interrupt_cd = 0        # 5 turn CD when hurricane is interrupted by stun
        self.blessed = False
        self.night_boost_active = False
        self.night_boost_used = False
        self.night_boost_damage = 0
        self.invigorated = False
        self.arbitration_boost = 0
        self.arbitration_cooldown = 0
        self.testimony_cooldown = 0
        self.scribe_copy_pending = False   # True when ability is being fired as a scribe copy
        self.prayer_active = False
        self.phasing_active = False
        self.phasing_cooldown = 0
        self.study_skip = False
        self.planting_turns = 0
        self.planting_cooldown = 0
        self.spectate_active = False
        self.spectate_cooldown = 0
        self.memorise_ability = None
        self.memorise_turns = 0
        self.desecration_cooldown = 0
        self.balancing_cooldown = 0
        self.balancing_act_evasion_turns = 0
        self.balancing_act_status_immune = False
        self.pickpocket_cooldown = 0
        self.vital_strike_cooldown = 0
        self.card_tricks_cooldown = 0
        self.song_cooldown = 0
        self.damage_boost_pct = 0.0
        # Glance into Fate (Monster Seq 9) — fate curse on opponent
        self.fate_cursed_turns = 0
        self.fate_cursed_backfire = False
        # Fighting Spirit (Pugilist Seq 8) — attack boost
        self.fighting_spirit_active = False
        self.fighting_spirit_turns = 0
        self.fighting_spirit_boost = 0.0
        self.fighting_spirit_cooldown = 0
        # Computing (Robot Seq 8) — duration limit
        self.computing_turns = 0
        self.computing_cooldown = 0
        # Grazing (Shepherd Seq 5)
        self.grazed_abilities = []
        self.grazing_used = False
        self.grazing_cooldown = 0
        self.role_ability_disabled = 0      # turns remaining where role ability is suppressed
        self.debuff_stacks = 0          # each stack = 20% less damage dealt
        self.damage_vulnerability = 0   # Weakness Development: 20% more damage taken
        self.debuff_cooldown = 0        # 1 turn cooldown between stacks
        self.debuff_turns = 0           # turns remaining on debuff effect
        self.debuff_used = False        # one-time use per battle
        self.debuff_applied = False     # tracks if debuff was ever applied this battle

        # Seq 8 states
        self.combat_studies_active = False  # 15% attack boost (permanent)
        self.combat_studies_skip = False    # skip next turn to study
        self.swindling_uses = 0             # failure stacks
        self.provoked_block = None          # action key blocked by provocation
        self.instigated_action = None       # action key forced by instigation
        self.provocation_cooldown = 0
        self.provocation_trapped = False    # True when trapped by Provocation (not vine bomb)
        self.trap_slots = []                # list of pending trap types
        self.trap_set_turn = 0             # turn traps were laid
        self.trap_cooldown = 0             # 10 turn cooldown
        self.dog_chase_turns = 0           # dog chase turns remaining
        self._dog_chase_dmg_pct = 0.10     # damage % per turn (default 10%, trap sets 5-7%)
        self._trip_trap_miss = False       # next attack misses from trip trap
        self.last_attack_dmg = 0           # track for tripwire backlash
        self.abilities_used_this_battle = set()  # track for Prometheus
        self.moral_freedom_used = False
        self.moral_freedom_active = False   # 15 dmg/turn to opponent
        self.rage_baited_turns = 0
        self.rage_baited_cooldown = 0
        self.poem_cooldown = 0
        self.burial_cooldown = 0
        self.crime_cooldown = 0
        self.magic_trick_cooldown = 0
        self.brilliant_light_cooldown = 0
        self.listening_active = False
        self.folk_of_rage_turns = 0
        self.folk_of_rage_pct = 0.0
        self.folk_of_rage_active = False
        self.ritualistic_reasoning_used = False  # sabotage opponent next move
        self.ritualistic_reasoning_cooldown = 0
        self.sabotaged = False              # this fighter's next move is sabotaged
        self.reading_prediction = None     # predicted action key
        self.animal_companion_active = False
        self.animal_companion_turn = 0     # attacks every other attacker turn
        self.computing_active = False
        self.forceful_cooldown = 0
        self.domination_cooldown = 0
        self.domination_active = False
        self.domination_turns = 0
        self.bribe_cooldown = 0
        self.bribe_pending = None
        self.bribe_concealed_turn = False
        self.connect_share_turns = 0
        self.bribe_weakened = False        # next attack deals 40% less
        self.charm_turns = 0              # can't attack for N turns
        self.danger_intuition_active = False  # toggle on/off
        self.intuition_upkeep_sp = 20      # SP drained per turn while active
        self.gun_shot_used = False
        self.gun_shot_cooldown = 0
        self.gun_shot_bleed = False        # permanent bleed
        self.witch_burn = False            # witch's black flames: 10 dmg instead of 7

        # Seq 7 states
        self.paper_figurine_turns = 0    # attacks against attacker fail for N turns
        self.astrology_active = False    # -30% miss chance permanently
        self.astrology_used = False      # one-time use flag
        self.astrology_flee_ready = False  # waiting for flee confirmation
        self.fire_ravens_cooldown = 0
        self.scribe_pending = False      # waiting to record ability used against us
        self.scribe_record_in = 0        # turns until recording fires
        self.observation_active = False  # this fighter takes 40% less damage next turn
        self.observation_cooldown = 0
        self.therapy_cooldown = 0
        self.holy_water_regen = 0        # turns of 5 HP/turn regen
        self.water_bullet_cooldown = 0
        self.sneak_attack_cooldown = 0
        self.sneak_attack_pending = False # opponent loses next turn
        self.analyse_uses = 2            # Detective: 2 uses remaining
        self.analysed_skills = {}        # skill_key -> 0.30 damage reduction
        self.sleep_spell_cooldown = 0
        self.summoning_dead_cooldown = 0
        self.weapon_throw_cooldown = 0
        self.witch_curse_cooldown = 0
        self.appraisal_active = False    # 40% boost on next action
        self.appraisal_cooldown = 0
        self.lucky_day_used = False
        self.lucky_day_turns = 0         # defender's hit chance halved for N rounds
        self.blood_sucker_cooldown = 0
        self.vine_bomb_cooldown = 0
        self.trapped = 0                 # vine bomb: turns trapped (can't act at all)
        self.transformation_used = False
        self.transformed = False         # werewolf form active
        self.transform_regen = 0
        self.transform_turns = 0         # counts turns active, reverts at 5
        self.transform_hp_bonus = 0      # extra HP added so we can remove it on revert
        self.transform_dodge_reduction = 0.25  # incoming hit chance starts at 25% (nerfed)
        self.vile_crime_used = False
        self.vile_crime_count = 0        # hit counter (needs 3)
        self.illegal_trade_cooldown = 0
        self.mental_piercing_cooldown = 0
        self.magic_spell_cooldown = 0

        # Seq 6 states
        self.faceless_cooldown = 0
        self.prometheus_cooldown = 0
        self.prometheus_stolen_ability = None   # ability key stolen from opponent
        self.scribe_cooldown = 0
        self.scribe_copied_ability = None       # ability key copied from last hit
        self.scribe_active = False              # copy ready to fire next turn
        self.hypnotist_cooldown = 0
        self.notary_cooldown = 0
        self.notary_buff_turns = 0             # turns of +50% dmg on self
        self.notary_debuff_turns = 0           # turns of -50% dmg on opponent
        self.wind_blessed_buildup = 0          # 0=idle, 1=first round, 2=second round
        self.wind_blessed_cooldown = 0
        self.rose_bishop_cooldown = 0
        self.polymath_cooldown = 0
        self.polymath_ability = None           # ability key currently studied
        self.polymath_pending = False          # True during the turn the studied ability is used (70% potency)
        self.soul_assurer_cooldown = 0
        self.spirit_guide_active = False
        self.spirit_guide_turns = 0   # counts turns active, max 5
        self.spirit_guide_cooldown = 0
        self.glance_fate_cooldown = 0
        self.dawn_paladin_active = False
        self.dawn_paladin_cooldown = 0
        self.dawn_paladin_hp_bonus = 0         # extra HP added by armour
        self.hurricane_charging = False        # turn 1 of charge
        self.hurricane_cooldown = 0
        self.hurricane_uses = 0                # max 3 uses
        self.pleasure_witch_cooldown = 0
        self.charm_turns = 0                   # opponent is charmed for N turns
        self.charm_chance = 0.0                # current chance of maintaining charm
        self.conspirer_cooldown = 0
        self.conspiracy_used = False          # one use per match
        self.conspiracy_miss_turns = 0        # 70% miss debuff turns remaining
        self.conspiracy_force_turns = 0       # force random ability turns remaining
        self.conspiracy_dmg_turns = 0         # 40dmg+60sp drain turns remaining
        self.scrolls_professor_cooldown = 0
        self.scroll_status = None              # active semi-permanent status key
        self.artisan_cooldown = 0
        self.artisan_item = None               # ability key the item replicates
        self.artisan_item_cooldown = 0
        self.calamity_priest_cooldown = 0
        self.potions_professor_cooldown = 0
        self.biologist_cooldown = 0
        self.zombie_cooldown = 0
        self.devil_form_active = False
        self.devil_form_cooldown = 0
        self.devil_form_hp_bonus = 0
        self.devil_form_sp_bonus = 0
        self.distortion_cooldown = 0
        self.distortion_active = False
        self.judge_cooldown = 0
        self.judge_restricted_move = None
        self.judge_move_pool = []
        self.jurisdiction_cooldown = 0
        self.jurisdiction_active = False
        self.jurisdiction_turns = 0
        self.jurisdiction_dmg_boost = 0.0
        self.arbiter_cooldown = 0
        self.arbiter_active = False
        self.arbiter_turns = 0              # turns of ceasefire remaining

        # Seq 5 states
        self.spirit_thread_active = False       # Marionettist: control active
        self.spirit_thread_stun_pct = 0         # current stun chance (10 base, +10/turn)
        self.spirit_thread_turns = 0            # turns elapsed
        self.spirit_thread_cooldown = 0
        self.marionette_active = False          # marionette summon active
        self.marionette_turns = 0
        self.marionette_sacrificed_ability = None
        self.marionette_cooldown = 0
        self.mental_theft_cooldown = 0
        self.rewards_theft_cooldown = 0
        self.blink_cooldown = 0
        self.blink_stun_immune_turns = 0        # turns of stun immunity
        self.travellers_door_uses = 2
        self.travellers_door_cooldown = 0
        self.dream_visitation_cooldown = 0
        self.dream_alteration_used = False
        self.dream_alteration_acc_loss = 0.0    # cumulative accuracy loss
        self.purification_halo_cooldown = 0
        self.purification_halo_sp_boost_turns = 0
        self.light_of_holiness_cooldown = 0
        self.singing_cooldown = 0
        self.singing_enlightened_turns = 0      # vocal enlightenment buff turns
        self.singing_str_boost = 0.0
        self.singing_spirit_boost = 0.0
        self.arrow_charging = False
        self.arrow_cooldown = 0
        self.grazing_abilities = []             # list of grazed ability keys
        self.grazing_cooldown = 0
        self.combination_spell_active = False   # combo spells unlocked
        self.combo_last_ability = None
        self.spiritual_suppression_uses = 2
        self.spiritual_suppression_turns = 0    # turns suppression is active
        self.spiritual_suppression_cooldown = 0
        self.spiritual_takeover_used = False
        self.spiritual_takeover_active = False  # 15 dmg/round to opponent
        self.dragged_to_hell_used = False
        self.dragged_to_hell_active = False     # 15 sp drain per round
        self.evil_sealing_active = False
        self.evil_sealing_damage = 0
        self.evil_sealing_debuff = None
        self.protection_active = False          # Guardian: damage reduction shield
        self.protection_mode = None             # "active" or "maintenance"
        self.protection_weakened_turn = False   # reduction drops to 10 for 1 turn after attacking
        self.protection_cooldown = 0
        self.last_stand_bonus = 0               # bonus damage on next attack (Last Stand ability)
        self.dawn_armor_active = False          # Guardian: HP/strength boost
        self.dawn_armor_hp_bonus = 0
        self.dawn_armor_cooldown = 0
        self.disease_propagation_used = False
        self.disease_propagation_active = False
        self.disease_propagation_turns = 0
        self.thread_storm_cooldown = 0
        self.cull_uses = 3
        self.cull_bonus = 0
        self.cull_cooldown = 0
        self.weakness_development_uses = 2
        self.weakness_development_cooldown = 0
        self.stellar_self_turns = 0
        self.star_pillar_cooldown = 0
        self.fire_storm_cooldown = 0
        self.star_of_curses_used = False
        self.star_of_curses_turns = 0
        self.curse_of_misfortune_cooldown = 0
        self.curse_of_misfortune_turns = 0      # turns victim is under misfortune
        self.active_luck_boost_used = False
        self.active_luck_boost_turns = 0
        self.artificial_moon_used = False
        self.artificial_moon_turns = 0
        self.scarlet_transformation_active = False
        self.scarlet_transformation_cooldown = 0
        self.spirit_animal_active = False
        self.spirit_animal_turns = 0
        self.spirit_animal_hp_bonus = 0
        self.spirit_animal_cooldown = 0
        self.wrath_of_nature_cooldown = 0
        self.wrath_of_nature_thorn = False
        self.wrath_of_nature_poison_turns = 0
        self.possession_active = False
        self.possession_turns = 0
        self.possession_cooldown = 0
        self.wraith_shriek_cooldown = 0
        self.wraith_shriek_acc_debuff = 0       # turns of −40% accuracy
        self.desire_explosion_uses = 2
        self.desire_explosion_cooldown = 0
        self.desire_symbiosis_used = False
        self.desire_emotion = None
        self.prohibition_uses = 2
        self.prohibition_cooldown = 0
        self.prohibited_ability = None
        self.punishment_cooldown = 0
        self.punishment_active = False          # target is petrified
        self.punishment_dmg_boost = 0.45        # 45% boost on attacks against punished target
        self.disciplinary_strike_cooldown = 0
        self.judgement_debuff_turns = 0         # −25% damage + lose buff
        self.sacred_conviction_cooldown = 0
        self.sacred_conviction_boost_turns = 0  # 3-turn +20% damage boost

    @classmethod
    async def load(cls, member: discord.Member, guild_id: int) -> "Fighter":
        """Async constructor — populates all_roles from both DB and Discord roles."""
        fighter = cls.__new__(cls)
        fighter.__init__(member)          # run sync init first (uses Discord roles only)
        # Now override all_roles with the richer combined source
        fighter.all_roles = await get_all_roles_from_db_and_discord(member, guild_id)
        # Also refresh spell_defence — it depends on all_roles now spanning DB pathway too
        fighter.has_spell_defence = any(r in SPELL_DEFENCE_ROLES for r in fighter.all_roles)
        return fighter

    def hp_bar(self):
        return f"[{self.hp}/{self.max_hp}]"

    def sp_bar(self):
        return f"[{self.sp}/{self.max_sp}]"

    def is_alive(self):
        return self.hp > 0

    def status(self):
        buffs = []
        if self.bleed > 0:              buffs.append(f"🩸×{self.bleed}")
        if self.gun_shot_bleed:         buffs.append("🩸∞")
        if self.paralysis > 0:          buffs.append(f"⚡×{self.paralysis}")
        if self.slumber > 0:            buffs.append(f"💤×{self.slumber}")
        if self.freeze > 0:             buffs.append(f"🧊×{self.freeze}")
        if self.burn > 0:               buffs.append(f"🔥×{self.burn}")
        if self.defence_reduction > 0:  buffs.append("🔍")
        if self.moral_freedom_active:   buffs.append("👼")
        if self.strength_boost > 0:     buffs.append("💪")
        if self.divination_dodge:       buffs.append("🔮")
        if self.danger_intuition_active: buffs.append("👁️intuition")
        if self.domination_active:      buffs.append(f"👊dom×{self.domination_turns}")
        if self.charm_turns > 0:        buffs.append(f"💰charm×{self.charm_turns}")
        if self.connect_share_turns > 0: buffs.append(f"🔗connect×{self.connect_share_turns}")
        if self.bribe_weakened:         buffs.append("💰weak")
        if self.invigorated:            buffs.append("💢")
        if self.prayer_active:          buffs.append("🙏🩸")
        if self.night_boost_active:     buffs.append("🌙")
        if self.phasing_active:         buffs.append("👻")
        if self.planting_turns > 0:     buffs.append(f"🌱×{self.planting_turns}")
        if self.spectate_active:        buffs.append("👁️")
        if self.arbitration_boost > 0:  buffs.append("⚔️+")
        if self.combat_studies_active:  buffs.append("📘")
        if self.rage_baited_turns > 0:  buffs.append(f"😡×{self.rage_baited_turns}")
        if self.folk_of_rage_active:    buffs.append(f"👊{int(self.folk_of_rage_pct*100)}%")
        if self.animal_companion_active: buffs.append("🐾")
        if self.computing_active:       buffs.append("🤖")
        if self.status_immune > 0:      buffs.append(f"💊×{self.status_immune}")
        if self.supernatural_resist_active: buffs.append("🛡️resist")
        if self.stun_block:             buffs.append("🛡️stun")
        # Seq 7
        if self.paper_figurine_turns > 0: buffs.append(f"🪆×{self.paper_figurine_turns}")
        if self.astrology_active:       buffs.append("🌟+hit")
        if self.observation_active:     buffs.append("🔎-dmg")
        if self.holy_water_regen > 0:   buffs.append(f"💧×{self.holy_water_regen}")
        if self.sneak_attack_pending:   buffs.append("🥷next")
        if self.appraisal_active:       buffs.append("🏷️+40%")
        if self.lucky_day_turns > 0:    buffs.append(f"🍀×{self.lucky_day_turns}")
        if self.trapped > 0:            buffs.append(f"🌿×{self.trapped}")
        if self.transformed:            buffs.append("🐺")
        if self.vile_crime_count > 0:   buffs.append(f"🔪×{self.vile_crime_count}/3")
        if self.debuff_stacks > 0:      buffs.append(f"🔻×{self.debuff_stacks}")
        if self.role_ability_disabled > 0: buffs.append(f"🎴×{self.role_ability_disabled}")
        # Seq 6
        if self.wind_blessed_buildup > 0: buffs.append(f"🌪️build×{self.wind_blessed_buildup}")
        if self.notary_buff_turns > 0:  buffs.append(f"📜+50%×{self.notary_buff_turns}")
        if self.notary_debuff_turns > 0: buffs.append(f"📜-50%×{self.notary_debuff_turns}")
        if self.charm_turns > 0:        buffs.append(f"💋charm×{self.charm_turns}")
        if self.distortion_active:      buffs.append("👑distort")
        if self.spirit_guide_active:    buffs.append(f"👻guide×{5 - self.spirit_guide_turns}")
        if self.dawn_paladin_active:    buffs.append("🛡️paladin")
        if self.devil_form_active:      buffs.append("😈")
        if self.scroll_status:          buffs.append(f"📜{self.scroll_status}")
        if self.scribe_active or self.scribe_copied_ability: buffs.append("📖copy")
        if self.polymath_ability:       buffs.append("📚studied")
        suffix = "  " + " ".join(buffs) if buffs else ""
        return f"❤️ {self.hp_bar()}  ✨ {self.sp_bar()}{suffix}"

    def can_receive_status(self):
        """Returns True if status effects can be applied."""
        return self.status_immune <= 0 and not self.supernatural_resist_active

    def apply_status_effect(self, effect: str, turns: int = 1, attacker=None):
        """Apply a status effect, respecting immunity and stun block."""
        if self.supernatural_resist_active:
            self.hp = max(0, self.hp - 40)
            return False
        # Balancing Act (Sailor Seq 9) — blocks the next 1 status effect
        if getattr(self, "balancing_act_status_immune", False):
            self.balancing_act_status_immune = False
            return False
        # Immortal Will — immune to stun/charm/seal when below 40% HP
        if getattr(self, "_immortal_will_active", False):
            if self.hp / max(1, self.max_hp) <= 0.40:
                if effect in ("stun", "charm", "paralysis", "slumber", "freeze"):
                    return False
        # Beast Form — only physical effects land (no ability seals etc.)
        if getattr(self, "_beast_form_turns", 0) > 0:
            if effect not in ("bleed", "burn", "paralysis"):
                return False
        if not self.can_receive_status():
            return False
        if effect in ("paralysis", "slumber", "freeze") and self.stun_block:
            self.stun_block = False
            return False
        # Seq 5: Blink stun immunity
        if effect in ("paralysis", "slumber", "freeze") and self.blink_stun_immune_turns > 0:
            return False
        # Domination stun resistance
        if self.domination_active and effect in ("paralysis", "slumber", "freeze"):
            if attacker is not None:
                atk_seq = get_seq_number(attacker.role) if attacker.role else 9
                def_seq = get_seq_number(self.role) if self.role else 9
                if atk_seq == def_seq:
                    if random.random() < 0.50:  # 50% resist from same seq
                        return False
                elif atk_seq > def_seq:  # attacker is lower seq (higher number)
                    if random.random() < 0.70:  # 70% resist from lower seq
                        return False
        if effect == "bleed":
            self.bleed = max(self.bleed, turns)
        elif effect == "paralysis":
            self.paralysis = max(self.paralysis, turns)
        elif effect == "slumber":
            self.slumber = max(self.slumber, turns)
        elif effect == "freeze":
            self.freeze = max(self.freeze, turns)
        elif effect == "burn":
            self.burn = max(self.burn, turns)
        return True

    def get_damage(self, pct_min: float, pct_max: float):
        lo = seq_dmg(self, pct_min)
        hi = seq_dmg(self, pct_max)
        base = random.randint(lo, hi)
        atk_bonus = self.stats["attack"] * 0.5
        spirit_mult = 1 + (self.stats["spirituality"] * 0.024)
        bonus = self.strength_boost + self.arbitration_boost + self.night_boost_damage
        self.strength_boost = 0
        self.arbitration_boost = 0
        dmg = int((base + atk_bonus + bonus) * spirit_mult)
        dmg = int(dmg * self.get_attack_mult())
        crit_chance = (self.stats["luck"] / 5) * 8
        crit = random.uniform(0, 100) < crit_chance
        if crit:
            dmg = int(dmg * 1.4)
            return dmg, True
        return dmg, False

    def apply_damage(self, dmg, is_ability=False, skill_key=None, attacker=None):
        # Radiant Armour — 50% damage reduction for duration
        if getattr(self, "_radiant_armour_turns", 0) > 0:
            dmg = int(dmg * 0.50)
            # Recoil on attacker handled elsewhere
        # Moon Manifestation / Moon Divinity — immune to physical/special
        if getattr(self, "_moon_manifest_turns", 0) > 0 and not is_ability:
            return 0
        # Planar Shift — fully immune
        if getattr(self, "_planar_shift_turns", 0) > 0:
            return 0
        # Divine Radiance — fully immune
        if getattr(self, "_divine_radiance_turns", 0) > 0:
            return 0
        # Omniscience — immune to all, attacker takes 40 counter (handled in process_action)
        if getattr(self, "_omniscience_turns", 0) > 0:
            return 0
        # True damage mode — ignore all reductions (skip paper_figurine/distortion etc.)
        # _true_damage_mode flag checked in process_action before calling apply_damage
        # Inquisition damage amplification — +50% incoming damage
        if getattr(self, "_inquisition_dmg_amp", 0) > 0:
            dmg = int(dmg * 1.50)
        # Permanent damage reduction
        if getattr(self, "_permanent_dmg_reduction", 0) > 0:
            pass  # handled in get_attack_mult on attacker side
        # Phase Dodge (Door Seq 9) — takes 0 damage from next hit
        if getattr(self, "_phase_dodge", False):
            self._phase_dodge = False
            return 0
        # Perfect Dodge (Error Seq 8 Swindler) — 100% dodge next turn
        if getattr(self, "_perfect_dodge", False):
            self._perfect_dodge = False
            return 0
        # Parry/Riposte (Twilight Giant Seq 7) — blocks hit, sets riposte flag
        if getattr(self, "_parry_active", False):
            self._parry_active = False
            self._riposte_pending = True
            return 0
        # Seq 5: Scarlet Transformation — immune to physical/special
        if self.scarlet_transformation_active and not is_ability:
            return 0
        # Divination dodge — fires on all ability damage
        if is_ability and getattr(self, "divination_dodge", False):
            dodge_chance = self._seer_dodge_chance or 70
            self.divination_dodge_turns = max(0, self.divination_dodge_turns - 1)
            if self.divination_dodge_turns <= 0:
                self.divination_dodge = False
                self._seer_dodge_chance = None
            if random.uniform(0, 100) < dodge_chance:
                self._pending_divination_msg = (
                    f"🔮 **{self.user.display_name}** reads the ability and sidesteps! "
                    f"*(divination {int(dodge_chance)}%)*"
                )
                return 0
        self._pending_divination_msg = ""
        # Seq 5: Stellar Self — completely unhittable for 2 turns vs same/lower seq
        if self.stellar_self_turns > 0:
            if attacker is not None:
                atk_seq = get_seq_number(attacker.role) if attacker.role else 9
                def_seq = get_seq_number(self.role) if self.role else 9
                if atk_seq >= def_seq:
                    return 0
        # Seq 5: Protection — damage reduction (80 normally, 10 for 1 turn after attacker attacked)
        if getattr(self, "protection_active", False):
            reduction = seq_val(self, 10) if getattr(self, "protection_weakened_turn", False) else seq_val(self, 80)
            dmg = max(0, dmg - reduction)
            self.protection_weakened_turn = False
            if self.protection_mode == "active":
                self.protection_active = False  # deactivates after absorbing one hit
        # Seq 5: Judgement debuff — -25% outgoing damage
        if self.judgement_debuff_turns > 0:
            dmg = int(dmg * 0.75)
        # Seq 5: Punishment — punished target takes +45% incoming damage
        if getattr(self, "punishment_active", False):
            dmg = int(dmg * 1.45)
        # Weakness Development: 20% damage vulnerability
        if getattr(self, "damage_vulnerability", 0) > 0:
            dmg = int(dmg * 1.20)
        if self.defence_reduction > 0:
            dmg = int(dmg * (1 + self.defence_reduction))
        if is_ability and self.has_spell_defence:
            dmg = int(dmg * 0.80)
        if skill_key and skill_key in self.analysed_skills:
            dmg = int(dmg * (1 - self.analysed_skills[skill_key]))
        if self.observation_active:
            dmg = max(1, int(dmg * random.uniform(0.50, 0.60)))
            self.observation_active = False
        if self.phasing_active:
            dmg = dmg // 2
            self.phasing_active = False
        # Bribe — Weaken: attacker's next attack deals 40% less
        if attacker is not None and getattr(attacker, "bribe_weakened", False):
            dmg = max(1, int(dmg * 0.60))
            attacker.bribe_weakened = False
        # Seq 6: notary debuff
        if self.notary_debuff_turns > 0:
            dmg = int(dmg * 0.50)
            return dmg
        # Last Stand (Death Gate / Undying Bone artifact) — survive lethal hit with 1 HP
        if dmg >= self.hp and getattr(self, "_last_stand", False):
            self._last_stand = False
            self.hp = 1
            return dmg
        self.hp = max(0, self.hp - dmg)
        # Connect: share 40% of damage taken with the attacker
        if attacker is not None and getattr(attacker, "connect_share_turns", 0) > 0:
            shared = max(1, int(dmg * 0.40))
            attacker.hp = max(0, attacker.hp - shared)
        return dmg

    def get_miss_chance(self, base_miss: int) -> int:
        """Return miss chance, modified by freeze and computing."""
        miss = base_miss
        if self.freeze_weakened:
            miss = min(95, miss + 30)
        if self.computing_active:
            miss = 0  # 100% accuracy
        if self.rage_baited_turns > 0:
            miss = 50
        if self.astrology_active:
            miss = max(0, miss - 30)
        # Conspiracy: 70% miss debuff
        if getattr(self, "conspiracy_miss_turns", 0) > 0:
            miss = min(95, miss + 70)
        # Seq 5: Desire Symbiosis modifiers
        if getattr(self, "desire_emotion", None) == "wrath":
            miss = min(95, miss + 20)   # −20% accuracy
        elif getattr(self, "desire_emotion", None) == "lust":
            miss = 0                    # 100% accuracy
        elif getattr(self, "desire_emotion", None) == "envy":
            miss = min(95, miss + 30)   # −30% accuracy
        # Seq 5: Wraith Shriek accuracy debuff
        if getattr(self, "wraith_shriek_acc_debuff", 0) > 0:
            miss = min(95, miss + 40)
        # Seq 5: Dream Alteration cumulative accuracy loss
        if getattr(self, "dream_alteration_acc_loss", 0) > 0:
            self.dream_alteration_acc_loss = min(0.50, self.dream_alteration_acc_loss + 0.05)
            miss = min(95, miss + int(self.dream_alteration_acc_loss * 100))
        # Seq 5: Active luck boost — 90% dodge vs incoming
        return miss

    def hit_chance_vs(self, defender) -> bool:
        """Returns True if attacker's hit lands considering lucky_day and conspiracy on attacker."""
        # Conspiracy miss debuff on the attacker
        if getattr(self, "conspiracy_miss_turns", 0) > 0:
            if random.random() < 0.70:
                return False
        # Glance into Fate curse — 30% miss chance for duration
        if getattr(self, "fate_cursed_turns", 0) > 0:
            if random.random() < 0.30:
                return False
        # Force miss (Fate Mastery)
        if getattr(self, "_force_miss_on_defender", 0) > 0:
            self._force_miss_on_defender -= 1
            return False
        # Absolute accuracy (Absolute Analysis / Absolute Vision / Paragon Divinity) — always hit
        if getattr(self, "_absolute_accuracy", False):
            return True
        if defender.lucky_day_turns > 0:
            return random.random() >= 0.5
        # Seq 5: Active Luck Boost — 90% dodge chance
        if getattr(defender, "active_luck_boost_turns", 0) > 0:
            if random.random() < 0.90:
                return False
        # Seq 5: Curse of Misfortune accuracy debuff on the attacker
        if getattr(self, "curse_of_misfortune_turns", 0) > 0:
            acc_roll = random.randint(10, 40)
            if random.randint(1, 100) > acc_roll:
                return False
        return True

    def get_ability_cost(self, base_cost, ability_key=None):
        if ability_key and self.memorise_turns > 0 and self.memorise_ability == ability_key:
            return 0
        if base_cost == 0:
            return 0
        # Lunar Domain — all abilities free
        if getattr(self, "_lunar_domain_turns", 0) > 0:
            return 0
        # Knowledge Divinity — all abilities free
        if getattr(self, "_knowledge_divinity_turns", 0) > 0:
            return 0
        reduction = min(0.15, self.stats["spirituality"] * 0.03)
        return max(1, int(base_cost * (1 - reduction)))

    def get_attack_mult(self) -> float:
        """Returns the combined attack multiplier from all active buffs/debuffs.
        Used by both get_damage() and direct ability damage calls."""
        mult = 1.0
        if self.invigorated:
            mult *= 1.30
            self.invigorated = False
        if self.prayer_active:
            mult *= 1.50
        if self.damage_boost_pct > 0:
            mult *= (1 + self.damage_boost_pct)
        if self.combat_studies_active:
            mult *= 1.20
        if self.folk_of_rage_active and self.folk_of_rage_pct > 0:
            mult *= (1 + self.folk_of_rage_pct)
        if self.rage_baited_turns > 0:
            mult *= 2.0
        if self.freeze_weakened:
            mult *= 0.70
        if self.appraisal_active:
            mult *= 1.40
            self.appraisal_active = False
        if self.debuff_stacks > 0 or self.debuff_turns > 0:
            mult *= 0.80  # 20% damage reduction while debuffed
        # Seq 6: notary buff
        if self.notary_buff_turns > 0:
            mult *= 1.50
        # Domination: +40% damage boost
        if self.domination_active:
            mult *= 1.40
        # Jurisdiction: starts at +20%, grows by 7% each turn
        if self.jurisdiction_active and self.jurisdiction_dmg_boost > 0:
            mult *= (1 + self.jurisdiction_dmg_boost)
        # Seq 5: Desire Symbiosis
        if getattr(self, "desire_emotion", None) == "lust":
            mult *= 0.40   # −60% strength
        elif getattr(self, "desire_emotion", None) == "envy":
            mult *= 0.70   # −30% strength
        # Seq 5: Punishment/Petrification — punished fighter deals -20% ability damage
        if getattr(self, "punishment_active", False):
            mult *= 0.80
        # Seq 5: Protection — −50% outgoing damage while shielding
        if getattr(self, "protection_active", False):
            mult *= 0.50
        # Fighting Spirit (Pugilist Seq 8) — +30% physical attack
        if self.fighting_spirit_active and self.fighting_spirit_boost > 0:
            mult *= (1 + self.fighting_spirit_boost)
        # Night Boost (Sleepless Seq 9) — damage bonus already applied via strength_boost
        # Moral Freedom (Instigator/Demoness) — handled as DOT, not mult
        # Sacred Conviction (Justiciar Seq 7) — +25% damage boost
        if getattr(self, "sacred_conviction_boost_turns", 0) > 0:
            mult *= 1.25
        # Strength Boost from various abilities
        if getattr(self, "strength_boost", 0) > 0:
            mult *= (1 + self.strength_boost * 0.05)  # each stack = +5%
        # Fear debuff (from spectate/nightmare abilities) — -25% damage
        if getattr(self, "_fear_turns", 0) > 0:
            mult *= 0.75
        # Sacred Conviction (Justiciar Seq 7) — +25% damage boost
        if getattr(self, "sacred_conviction_boost_turns", 0) > 0:
            mult *= 1.25
        # Fear debuff — -25% damage when feared
        if getattr(self, "_fear_turns", 0) > 0:
            mult *= 0.75
        # Radiant Armour — damage reduction (handled in apply_damage, not mult)
        # War Incarnation — 3x attack boost
        if getattr(self, "_war_incarnation_turns", 0) > 0:
            mult *= getattr(self, "_war_incarnation_boost", 3.0)
        # Fate Weave — double damage
        if getattr(self, "_fate_weave_dmg_boost", 0) > 0:
            mult *= 2.0
            self._fate_weave_dmg_boost -= 1
        # Fate Rewrite — max damage mode
        if getattr(self, "_fate_rewrite_max_dmg", 0) > 0:
            mult *= 2.5
            self._fate_rewrite_max_dmg -= 1
        # Moon Manifestation — double ability damage
        if getattr(self, "_moon_manifest_turns", 0) > 0:
            mult *= 2.0
        # Divine Radiance — 150% damage
        if getattr(self, "_divine_radiance_turns", 0) > 0:
            mult *= 1.50
        # Civilization Light — +50% damage
        if getattr(self, "_civilization_light_turns", 0) > 0:
            mult *= 1.50
        # Permanent damage reduction from omniscient analysis / tower divinity
        if getattr(self, "_permanent_dmg_reduction", 0) > 0:
            mult *= max(0.05, 1 - self._permanent_dmg_reduction)
        # Demonic corruption — -30% damage dealt
        if getattr(self, "_demonic_corruption_turns", 0) > 0 and getattr(self, "_demonic_corruption_dmg_reduce", 0) > 0:
            mult *= (1 - self._demonic_corruption_dmg_reduce)
        # Inquisition — +50% incoming damage (this is attacker's mult on defender's side,
        # we apply it here for the attacker's own outgoing damage amplification check below)
        # No damage turns (Miracle Wish)
        if getattr(self, "_no_damage_turns", 0) > 0:
            mult *= 0.0
        # Punishment active (Justiciar Seq 5) — +25% damage for 5 turns
        if getattr(self, "punishment_active", False) and getattr(self, "punishment_turns", 0) > 0:
            mult *= 1.25
        # Comeback mechanic — low HP fighter deals slightly more damage
        hp_pct = self.hp / max(1, self.max_hp)
        if hp_pct <= 0.20:
            mult *= 1.20   # last legs: +20% damage under 20% HP
        elif hp_pct <= 0.40:
            mult *= 1.10   # bloodied: +10% damage under 40% HP
        # Desperation bonus — empty SP means a raw physical push
        if self.sp <= 0:
            mult *= 1.08
        return mult

    def get_sp_regen(self):
        return 4 + int(self.stats["spirituality"] / 3)

    def evasion_chance(self):
        base = min(15, self.stats["speed"] * 1.5 + self.stats["luck"] * 0.75)
        if self.spectate_active:
            base = min(25, base + 8)
        # Balancing Act (Sailor Seq 9) — +20% evasion for duration
        if self.balancing_act_evasion_turns > 0:
            base = min(45, base + 20)
        # Absolute Luck — 100% evasion
        if getattr(self, "_absolute_luck_turns", 0) > 0:
            return 100
        # Fortune Divinity — 100% evasion
        if getattr(self, "_fortune_misfortune_turns", 0) > 0:
            return 100
        # Planar Shift — fully immune (no evasion needed, handled in apply_damage)
        # Moon Manifestation — immune to physical/special (handled in apply_damage)
        # No evasion turns (law alteration)
        if getattr(self, "_no_evasion_turns", 0) > 0:
            return 0
        if self.role == "[Fool] Seq 8 — Clown":
            base = min(30, base + 5)
        if self.role == "[Error] Seq 8 — Swindler":
            base = min(25, base + 4)
        if self.role == "[Darkness] Seq 8 — Midnight Poet":
            base = min(22, base + 3)
        if self.role == "[Moon] Seq 8 — Beast Tamer":
            base = min(22, base + 3)
        if self.role == "[Visionary] Seq 8 — Telepathist":
            base = min(22, base + 3)
        # Seq 7 role evasion boosts
        if self.role == "[Hanged Man] Seq 7 — Shadow Ascetic":
            base = min(28, base + 5)
        if self.role == "[Demoness] Seq 7 — Witch":
            base = min(25, base + 4)
        if self.role == "[Darkness] Seq 7 — Nightmare":
            base = min(25, base + 4)
        if self.role == "[Moon] Seq 7 — Vampire":
            base = min(24, base + 3)
        return base

    def divination_chance(self, defender_speed):
        speed_diff = self.stats["speed"] - defender_speed
        return max(40, min(90, 75 + speed_diff * 5))

    def tick_cooldowns(self):
        if self.desecration_cooldown > 0:   self.desecration_cooldown -= 1
        if self.trap_cooldown > 0:          self.trap_cooldown -= 1
        if self.balancing_cooldown > 0:     self.balancing_cooldown -= 1
        if self.pickpocket_cooldown > 0:    self.pickpocket_cooldown -= 1
        if self.vital_strike_cooldown > 0:  self.vital_strike_cooldown -= 1
        if self.card_tricks_cooldown > 0:   self.card_tricks_cooldown -= 1
        if self.phasing_cooldown > 0:       self.phasing_cooldown -= 1
        if self.arbitration_cooldown > 0:   self.arbitration_cooldown -= 1
        if self.testimony_cooldown > 0:     self.testimony_cooldown -= 1
        if self.memorise_turns > 0:         self.memorise_turns -= 1
        if self.rage_baited_cooldown > 0:   self.rage_baited_cooldown -= 1
        if self.poem_cooldown > 0:          self.poem_cooldown -= 1
        if self.burial_cooldown > 0:        self.burial_cooldown -= 1
        if self.crime_cooldown > 0:         self.crime_cooldown -= 1
        if self.magic_trick_cooldown > 0:   self.magic_trick_cooldown -= 1
        if self.brilliant_light_cooldown > 0: self.brilliant_light_cooldown -= 1
        if self.witch_curse_cooldown > 0:   self.witch_curse_cooldown -= 1
        if self.forceful_cooldown > 0:      self.forceful_cooldown -= 1
        if self.ritualistic_reasoning_cooldown > 0: self.ritualistic_reasoning_cooldown -= 1
        if self.domination_cooldown > 0:    self.domination_cooldown -= 1
        if self.bribe_cooldown > 0:         self.bribe_cooldown -= 1
        if self.domination_active:
            self.domination_turns -= 1
            if self.domination_turns <= 0:
                self.domination_active = False
        if self.status_immune > 0:          self.status_immune -= 1
        # Rage baited turns
        if self.rage_baited_turns > 0:
            self.rage_baited_turns -= 1
        # Folk of rage tick
        if self.folk_of_rage_active and self.folk_of_rage_turns > 0:
            self.folk_of_rage_turns -= 1
            if self.folk_of_rage_turns > 0:
                self.folk_of_rage_pct = min(0.25, self.folk_of_rage_pct + 0.05)
        # Freeze weakened clears after 1 turn
        if self.freeze_weakened and self.freeze == 0:
            self.freeze_weakened = False
        # Seq 7 cooldowns
        if self.observation_cooldown > 0:   self.observation_cooldown -= 1
        if self.therapy_cooldown > 0:       self.therapy_cooldown -= 1
        if self.water_bullet_cooldown > 0:  self.water_bullet_cooldown -= 1
        if self.gun_shot_cooldown > 0:      self.gun_shot_cooldown -= 1
        if self.sneak_attack_cooldown > 0:  self.sneak_attack_cooldown -= 1
        if self.sleep_spell_cooldown > 0:   self.sleep_spell_cooldown -= 1
        if self.summoning_dead_cooldown > 0: self.summoning_dead_cooldown -= 1
        if self.weapon_throw_cooldown > 0:  self.weapon_throw_cooldown -= 1
        if self.appraisal_cooldown > 0:     self.appraisal_cooldown -= 1
        if self.blood_sucker_cooldown > 0:  self.blood_sucker_cooldown -= 1
        if self.vine_bomb_cooldown > 0:     self.vine_bomb_cooldown -= 1
        if self.planting_cooldown > 0:      self.planting_cooldown -= 1
        if self.illegal_trade_cooldown > 0: self.illegal_trade_cooldown -= 1
        if self.mental_piercing_cooldown > 0: self.mental_piercing_cooldown -= 1
        if self.magic_spell_cooldown > 0:   self.magic_spell_cooldown -= 1
        if self.debuff_cooldown > 0:        self.debuff_cooldown -= 1
        if self.debuff_turns > 0:           self.debuff_turns -= 1
        # Role ability suppression (glance into fate)
        if self.role_ability_disabled > 0:  self.role_ability_disabled -= 1
        # Lucky day turns
        if self.lucky_day_turns > 0:        self.lucky_day_turns -= 1
        # Paper figurine
        if self.paper_figurine_turns > 0:   self.paper_figurine_turns -= 1
        # Holy water regen
        if self.holy_water_regen > 0:       self.holy_water_regen -= 1
        # Werewolf: increase dodge reduction each round, revert after 5 turns
        if self.transformed:
            self.transform_turns += 1
            self.transform_dodge_reduction = min(0.85, self.transform_dodge_reduction + 0.05)
            if self.transform_turns >= 5:
                self.transformed = False
                # Remove the HP bonus — drop user to 50% of their base max HP
                self.max_hp = max(1, self.max_hp - self.transform_hp_bonus)
                self.hp = max(1, self.max_hp // 2)   # 50% HP on revert
                self.transform_hp_bonus = 0
                self.transform_regen = 0
        # Seq 6 cooldowns
        if self.faceless_cooldown > 0:          self.faceless_cooldown -= 1
        if self.prometheus_cooldown > 0:        self.prometheus_cooldown -= 1
        if self.scribe_cooldown > 0:            self.scribe_cooldown -= 1
        if self.hypnotist_cooldown > 0:         self.hypnotist_cooldown -= 1
        if self.notary_cooldown > 0:            self.notary_cooldown -= 1
        if self.notary_buff_turns > 0:          self.notary_buff_turns -= 1
        if self.notary_debuff_turns > 0:        self.notary_debuff_turns -= 1
        if self.wind_blessed_cooldown > 0:      self.wind_blessed_cooldown -= 1
        if self.rose_bishop_cooldown > 0:       self.rose_bishop_cooldown -= 1
        if self.polymath_cooldown > 0:          self.polymath_cooldown -= 1
        if self.soul_assurer_cooldown > 0:      self.soul_assurer_cooldown -= 1
        if self.spirit_guide_cooldown > 0:      self.spirit_guide_cooldown -= 1
        if self.glance_fate_cooldown > 0:       self.glance_fate_cooldown -= 1
        if self.spirit_guide_active:
            self.spirit_guide_turns += 1
        if self.dawn_paladin_cooldown > 0:      self.dawn_paladin_cooldown -= 1
        if self.pleasure_witch_cooldown > 0:    self.pleasure_witch_cooldown -= 1
        if self.conspirer_cooldown > 0:         self.conspirer_cooldown -= 1
        if self.fire_ravens_cooldown > 0:       self.fire_ravens_cooldown -= 1
        if self.hurricane_cooldown > 0:         self.hurricane_cooldown -= 1
        if self.conspiracy_miss_turns > 0:      self.conspiracy_miss_turns -= 1
        if self.conspiracy_force_turns > 0:     self.conspiracy_force_turns -= 1
        if self.conspiracy_dmg_turns > 0:       self.conspiracy_dmg_turns -= 1
        if self.provocation_cooldown > 0:       self.provocation_cooldown -= 1
        if self.scrolls_professor_cooldown > 0: self.scrolls_professor_cooldown -= 1
        if self.artisan_cooldown > 0:           self.artisan_cooldown -= 1
        if self.artisan_item_cooldown > 0:      self.artisan_item_cooldown -= 1
        if self.calamity_priest_cooldown > 0:   self.calamity_priest_cooldown -= 1
        if self.potions_professor_cooldown > 0: self.potions_professor_cooldown -= 1
        if self.biologist_cooldown > 0:         self.biologist_cooldown -= 1
        if self.zombie_cooldown > 0:            self.zombie_cooldown -= 1
        if self.devil_form_cooldown > 0:        self.devil_form_cooldown -= 1
        if self.distortion_cooldown > 0:  self.distortion_cooldown -= 1
        if self.judge_cooldown > 0:             self.judge_cooldown -= 1
        if self.hurricane_interrupt_cd > 0:     self.hurricane_interrupt_cd -= 1
        if self.seer_divination_cooldown > 0:   self.seer_divination_cooldown -= 1
        if self.jurisdiction_cooldown > 0:      self.jurisdiction_cooldown -= 1
        if self.jurisdiction_active:
            self.jurisdiction_turns -= 1
            self.jurisdiction_dmg_boost += 0.07  # grows by 7% each turn
            if self.jurisdiction_turns <= 0:
                self.jurisdiction_active = False
                self.jurisdiction_dmg_boost = 0.0
        # Fear debuff tick
        if getattr(self, "_fear_turns", 0) > 0:    self._fear_turns -= 1
        # Balancing Act evasion buff tick
        if self.balancing_act_evasion_turns > 0:   self.balancing_act_evasion_turns -= 1
        # Fate curse tick
        if self.fate_cursed_turns > 0:             self.fate_cursed_turns -= 1
        # Fighting Spirit buff tick
        if self.fighting_spirit_cooldown > 0:      self.fighting_spirit_cooldown -= 1
        if self.fighting_spirit_active:
            self.fighting_spirit_turns -= 1
            if self.fighting_spirit_turns <= 0:
                self.fighting_spirit_active = False
                self.fighting_spirit_boost = 0.0
        # Computing duration tick
        if self.computing_cooldown > 0:            self.computing_cooldown -= 1
        if self.computing_active and self.computing_turns > 0:
            self.computing_turns -= 1
            if self.computing_turns <= 0:
                self.computing_active = False
        # Song / spectate cooldowns
        if self.song_cooldown > 0:                 self.song_cooldown -= 1
        if self.spectate_cooldown > 0:             self.spectate_cooldown -= 1
        if self.grazing_cooldown > 0:              self.grazing_cooldown -= 1

        # Seq 5 cooldowns
        if self.spirit_thread_cooldown > 0:         self.spirit_thread_cooldown -= 1
        if self.marionette_cooldown > 0:            self.marionette_cooldown -= 1
        if self.mental_theft_cooldown > 0:          self.mental_theft_cooldown -= 1
        if self.rewards_theft_cooldown > 0:         self.rewards_theft_cooldown -= 1
        if self.blink_cooldown > 0:                 self.blink_cooldown -= 1
        if self.blink_stun_immune_turns > 0:        self.blink_stun_immune_turns -= 1
        if self.travellers_door_cooldown > 0:       self.travellers_door_cooldown -= 1
        if self.dream_visitation_cooldown > 0:      self.dream_visitation_cooldown -= 1
        if self.purification_halo_cooldown > 0:     self.purification_halo_cooldown -= 1
        if self.purification_halo_sp_boost_turns > 0: self.purification_halo_sp_boost_turns -= 1
        if self.light_of_holiness_cooldown > 0:     self.light_of_holiness_cooldown -= 1
        if self.singing_cooldown > 0:               self.singing_cooldown -= 1
        if self.singing_enlightened_turns > 0:
            self.singing_enlightened_turns -= 1
            if self.singing_enlightened_turns <= 0:
                self.singing_str_boost = 0.0
                self.singing_spirit_boost = 0.0
        if self.arrow_cooldown > 0:                 self.arrow_cooldown -= 1
        if self.grazing_cooldown > 0:               self.grazing_cooldown -= 1
        if self.spiritual_suppression_cooldown > 0: self.spiritual_suppression_cooldown -= 1
        if self.spiritual_suppression_turns > 0:    self.spiritual_suppression_turns -= 1
        if self.damage_vulnerability > 0:           self.damage_vulnerability -= 1
        if self.protection_cooldown > 0:            self.protection_cooldown -= 1
        if self.dawn_armor_cooldown > 0:            self.dawn_armor_cooldown -= 1
        if self.thread_storm_cooldown > 0:          self.thread_storm_cooldown -= 1
        if self.cull_cooldown > 0:                  self.cull_cooldown -= 1
        if self.weakness_development_cooldown > 0:  self.weakness_development_cooldown -= 1
        if self.stellar_self_turns > 0:             self.stellar_self_turns -= 1
        if self.star_pillar_cooldown > 0:           self.star_pillar_cooldown -= 1
        if self.fire_storm_cooldown > 0:            self.fire_storm_cooldown -= 1
        if self.star_of_curses_turns > 0:           self.star_of_curses_turns -= 1
        if self.curse_of_misfortune_cooldown > 0:   self.curse_of_misfortune_cooldown -= 1
        if self.curse_of_misfortune_turns > 0:      self.curse_of_misfortune_turns -= 1
        if self.active_luck_boost_turns > 0:        self.active_luck_boost_turns -= 1
        if self.artificial_moon_turns > 0:          self.artificial_moon_turns -= 1
        if self.scarlet_transformation_cooldown > 0: self.scarlet_transformation_cooldown -= 1
        # Auto-deactivate scarlet transformation after 4 turns (cooldown starts at 7, deactivate when at 3)
        if self.scarlet_transformation_active and self.scarlet_transformation_cooldown == 3:
            self.scarlet_transformation_active = False
        if self.spirit_animal_cooldown > 0:         self.spirit_animal_cooldown -= 1
        if self.wrath_of_nature_cooldown > 0:       self.wrath_of_nature_cooldown -= 1
        if self.wrath_of_nature_poison_turns > 0:   self.wrath_of_nature_poison_turns -= 1
        if self.possession_cooldown > 0:            self.possession_cooldown -= 1
        if self.wraith_shriek_cooldown > 0:         self.wraith_shriek_cooldown -= 1
        if self.wraith_shriek_acc_debuff > 0:       self.wraith_shriek_acc_debuff -= 1
        if self.desire_explosion_cooldown > 0:      self.desire_explosion_cooldown -= 1
        if self.prohibition_cooldown > 0:           self.prohibition_cooldown -= 1
        if self.punishment_cooldown > 0:            self.punishment_cooldown -= 1
        if self.disciplinary_strike_cooldown > 0:   self.disciplinary_strike_cooldown -= 1
        if self.judgement_debuff_turns > 0:
            self.judgement_debuff_turns -= 1
            if self.judgement_debuff_turns <= 0:
                self.punishment_active = False  # Petrification ends with judgement
        if self.sacred_conviction_cooldown > 0:     self.sacred_conviction_cooldown -= 1
        if self.sacred_conviction_boost_turns > 0:
            self.sacred_conviction_boost_turns -= 1
            if self.sacred_conviction_boost_turns <= 0:
                self.damage_boost_pct = max(0.0, self.damage_boost_pct - 0.20)

# ── Purchasable Abilities ─────────────────────────────────


# ── Stats embed (used by /stats_user) ───────────────────
def build_stats_embed(target: discord.Member) -> discord.Embed:
    role_name = get_user_role(target)
    pathway   = get_pathway_name(role_name) if role_name else None
    seq       = get_seq_number(role_name)   if role_name else 9

    # Resolve title (e.g. "Seer", "Clown"…)
    title_name = ""
    if role_name and "—" in role_name:
        title_name = role_name.split("—")[1].strip()

    raw = get_or_create_stats(target.id, target)
    stats = get_effective_stats(target.id, target)
    points = raw.get("points", 0)

    # Compute actual HP/SP using same formula as Fighter
    base = (
        PATHWAY_BASE_STATS.get((pathway, seq))
        or SEQ_BASE_STATS.get(seq, SEQ_BASE_STATS[9])
    ) if pathway else SEQ_BASE_STATS.get(seq, SEQ_BASE_STATS[9])
    max_hp = base["hp"] + stats["health"] * 6
    max_sp = base["sp"] + stats["spirituality"] * 2

    colour = PATHWAY_COLOURS.get(pathway, 0x9B59B6)
    flavour = PATHWAY_FLAVOUR.get(pathway, "A Beyonder of unknown origin.")

    if role_name and pathway and title_name:
        header = f"[{pathway}] Seq {seq} — {title_name}"
    elif role_name:
        header = role_name
    else:
        header = "No Pathway role detected"

    embed = discord.Embed(
        title=f"𓂃 {target.display_name}'s Stats",
        description=f"*{header}*\n{flavour}",
        color=colour
    )
    base_hp = base["hp"]
    base_sp = base["sp"]
    hp_from_stats = stats["health"] * 6
    sp_from_stats = stats["spirituality"] * 2
    embed.add_field(
        name="ᯓ★ Vitals",
        value=(
            f"❤️ **HP:** {max_hp} *(base {base_hp} + {hp_from_stats} from stats)*\n"
            f"✨ **SP:** {max_sp} *(base {base_sp} + {sp_from_stats} from stats)*"
        ),
        inline=True
    )
    embed.add_field(
        name="🔸 Unspent Points",
        value=f"**{points}** available\n*(unlocked by advancing Sequence)*",
        inline=True
    )
    embed.add_field(
        name="ᯓ★ Combat Stats",
        value=(
            f"🗡️ **Attack** {stat_bar(stats['attack'], BAR_STAT_MAX)} `{stats['attack']}`\n"
            f"⚡ **Speed** {stat_bar(stats['speed'], BAR_STAT_MAX)} `{stats['speed']}`\n"
            f"🍀 **Luck** {stat_bar(stats['luck'], BAR_STAT_MAX)} `{stats['luck']}`\n"
            f"❤️ **Health** {stat_bar(stats['health'], BAR_STAT_MAX)} `{stats['health']}`\n"
            f"✨ **Spriti** {stat_bar(stats['spirituality'], BAR_STAT_MAX)} `{stats['spirituality']}`"
        ),
        inline=False
    )
    bal = get_pounds(target.id, guild_id=0)
    s_eco = get_economy(target.id, guild_id=0)
    wins = s_eco.get("wins", 0); losses = s_eco.get("losses", 0); streak = s_eco.get("win_streak", 0)
    total = wins + losses; winrate = int((wins / total) * 100) if total > 0 else 0
    streak_icon = "🔥" if streak >= 2 else "✨" if streak == 1 else "💔"
    embed.add_field(name="💰 Soli", value=f"**{bal:,}** soli", inline=True)
    embed.add_field(name="⚔️ Record", value=f"**{wins}W / {losses}L** ({winrate}%)\n{streak_icon} Streak: **{streak}**", inline=True)
    embed.set_thumbnail(url=target.display_avatar.url)
    embed.set_footer(text="𓂃 Base stats come from your Pathway — bonus points unlock as you advance Sequence. See /guide for stat details.")
    return embed

