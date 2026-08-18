# ============================================================
# BATTLE MANAGER — the PvP duel state machine: looking up/creating
# battles, whose turn it is, HP/SP bars & status embeds, applying
# per-turn status effects, resolving victories (rewards, win-streak,
# bounty payout, XP bridge, battle log), and the inactivity watchdog
# that auto-ends abandoned fights.
#
# `battles` / `active_bets` / `battle_log` live on the BattleManager
# instance; bare-name module-level aliases are exported for the
# handful of other battle modules (actions, views) that reference them
# directly as plain dicts/lists — same object, so mutations stay in sync.
# ============================================================
import asyncio
import random
import discord

from bot_instance import bot
from logging_config import logger
from persistence import log_channel, save_stats, save_economy
from fighter import Fighter, seq_val, seq_dmg, get_or_create_stats
from economy import add_pounds, get_economy, claim_bounty
from roles import get_seq_number
from xp import award_combat_xp, penalize_combat_xp, SEQ_WIN_XP, SEQ_LOSS_XP
from game_data import BONUS_STAT_POINT_CHANCE, ROLE_ABILITIES, ROLE_ABILITIES_2, SEQ_WIN_REWARD

INACTIVITY_TIMEOUT = 180  # 3 minutes


class BattleManager:
    def __init__(self):
        self.battles = {}
        self.active_bets = {}
        self.battle_log = []  # list of dicts recorded per battle

    def _battle_key(self, channel_id, user1_id, user2_id):
        return (channel_id, frozenset({user1_id, user2_id}))

    def get_battle(self, channel_id, user=None):
        """Get the battle for a channel. If user is provided, finds their specific battle."""
        if user is not None:
            for key, battle in self.battles.items():
                if key[0] == channel_id:
                    c = battle["challenger"].user
                    o = battle["opponent"].user
                    if user in (c, o):
                        return battle
            return None
        # Legacy: return first battle in channel (for compat where user context isn't available)
        for key, battle in self.battles.items():
            if key[0] == channel_id:
                return battle
        return None

    def get_battle_key_for_user(self, channel_id, user):
        for key, battle in self.battles.items():
            if key[0] == channel_id:
                c = battle["challenger"].user
                o = battle["opponent"].user
                if user in (c, o):
                    return key
        return None

    def find_battle_by_players(self, challenger: discord.Member, opponent: discord.Member):
        for key, battle in self.battles.items():
            c = battle["challenger"].user
            o = battle["opponent"].user
            if (c == challenger and o == opponent) or (c == opponent and o == challenger):
                channel_id = key[0]
                return channel_id, battle
        return None, None

    def swap_turn(self, battle):
        import time
        c = battle["challenger"]
        o = battle["opponent"]
        battle["turn"] = o.user if battle["turn"] == c.user else c.user
        battle["turn_count"] = battle.get("turn_count", 0) + 1
        battle["last_action_time"] = time.time()  # reset inactivity timer

        # Sudden death after turn 40 — both fighters take escalating damage
        turn_count = battle["turn_count"]
        if turn_count >= 40:
            overtime = turn_count - 39
            c_drain = max(1, int(c.max_hp * 0.04 * overtime))
            o_drain = max(1, int(o.max_hp * 0.04 * overtime))
            c.hp = max(0, c.hp - c_drain)
            o.hp = max(0, o.hp - o_drain)
            battle["_sudden_death_msg"] = (
                f"⚠️ **SUDDEN DEATH — Turn {turn_count}!** "
                f"The arena itself drains both fighters! "
                f"**{c.user.display_name}** −{c_drain} HP, **{o.user.display_name}** −{o_drain} HP!\n"
            )

    def get_fighters(self, user, battle):
        c = battle["challenger"]
        o = battle["opponent"]
        if user == c.user:
            return c, o
        return o, c

    def validate_turn(self, user, battle):
        if not battle or not battle["accepted"]:
            return False
        if user not in [battle["challenger"].user, battle["opponent"].user]:
            return False
        if battle["turn"] != user:
            return False
        return True

    def hp_bar_visual(self, current, maximum, length=10):
        """Compact filled bar — e.g. ████████░░ 80%"""
        if maximum <= 0:
            return "`░░░░░░░░░░` **0%**"
        pct = current / maximum
        filled = max(0, min(length, round(pct * length)))
        bar = "█" * filled + "░" * (length - filled)
        return f"`{bar}` **{int(pct*100)}%**"

    def get_status_icons(self, f: "Fighter") -> str:
        """Compact icon row for active effects only."""
        icons = []
        if f.bleed > 0:          icons.append(f"🩸{f.bleed}")
        if f.burn > 0:           icons.append(f"🔥{f.burn}")
        if f.paralysis > 0:      icons.append(f"⚡{f.paralysis}")
        if f.slumber > 0:        icons.append(f"😴{f.slumber}")
        if f.freeze > 0:         icons.append(f"❄️{f.freeze}")
        if f.trapped > 0:        icons.append(f"🪤{f.trapped}")
        if getattr(f, "blink_stun_immune_turns", 0) > 0: icons.append("💨imm")
        if getattr(f, "protection_active", False):     icons.append(f"🛡️prot")
        if getattr(f, "dawn_armor_active", False):      icons.append("🌅armor")
        if getattr(f, "scarlet_transformation_active", False): icons.append("🌙imm")
        if getattr(f, "marionette_active", False):         icons.append("🎭")
        if getattr(f, "artificial_moon_turns", 0) > 0:    icons.append(f"🌕{f.artificial_moon_turns}")
        if getattr(f, "active_luck_boost_turns", 0) > 0:  icons.append(f"🍀{f.active_luck_boost_turns}")
        if getattr(f, "desire_emotion", None):             icons.append(f"😈{f.desire_emotion[:3]}")
        if getattr(f, "stellar_self_turns", 0) > 0:       icons.append(f"⭐{f.stellar_self_turns}")
        if getattr(f, "possession_active", False):         icons.append("👁️")
        if getattr(f, "disease_propagation_active", False):icons.append("🦠")
        if getattr(f, "spiritual_suppression_turns", 0) > 0: icons.append(f"🦷{f.spiritual_suppression_turns}")
        if getattr(f, "judgement_debuff_turns", 0) > 0:   icons.append(f"⛓️{f.judgement_debuff_turns}")
        if getattr(f, "curse_of_misfortune_turns", 0) > 0:icons.append(f"🎲{f.curse_of_misfortune_turns}")
        if getattr(f, "star_of_curses_turns", 0) > 0:     icons.append(f"🌟{f.star_of_curses_turns}")
        if getattr(f, "wrath_of_nature_poison_turns", 0) > 0: icons.append(f"☠️{f.wrath_of_nature_poison_turns}")
        if getattr(f, "dream_alteration_acc_loss", 0) > 0:icons.append(f"💭-{int(f.dream_alteration_acc_loss*100)}%")
        return " ".join(icons)

    def battle_status(self, battle):
        c = battle["challenger"]
        o = battle["opponent"]
        turn_count = battle.get("turn_count", 0)

        def row(f, is_turn):
            arrow = "▶ " if is_turn else "     "
            name  = f"**{arrow}{f.user.display_name}**"
            hp    = self.hp_bar_visual(f.hp, f.max_hp)
            sp    = self.hp_bar_visual(f.sp, f.max_sp, length=8)
            icons = self.get_status_icons(f)
            lines = [
                name,
                f"❤️ {hp} `{f.hp}/{f.max_hp}`",
                f"✨ {sp} `{f.sp}/{f.max_sp}`",
            ]
            if icons:
                lines.append(icons)
            return "\n".join(lines)

        sep = f"\n{'─'*32}\n"
        return f"{row(c, battle['turn']==c.user)}{sep}{row(o, battle['turn']==o.user)}\n\n*Turn {turn_count}*"

    def apply_turn_effects(self, attacker: Fighter, defender: Fighter, battle):
        log = ""
        skip_turn = False

        # Death Gate retaliate — auto-deal damage the turn after surviving a lethal hit
        if getattr(attacker, "_death_gate_retaliate", False):
            attacker._death_gate_retaliate = False
            ret_dmg = seq_val(attacker, 70)
            defender.hp = max(0, defender.hp - ret_dmg)
            log += f"🗝️ **Death Gate Retaliate!** **{attacker.user.display_name}** unleashes **{ret_dmg}** spirit damage!\n"

        # Bribe — resolve pending effect at start of the briber's turn after the concealed throw
        if attacker.bribe_pending and not attacker.bribe_concealed_turn:
            effect = attacker.bribe_pending
            attacker.bribe_pending = None
            if effect == "weaken":
                defender.bribe_weakened = True
                log += f"💰 **Bribe — Weaken!** A secret deal weakens **{defender.user.display_name}** — their next attack deals **40% less damage**!\n"
            elif effect == "charm":
                defender.charm_turns = 2
                log += f"💰 **Bribe — Charm!** **{defender.user.display_name}** is enchanted — cannot attack for **2 turns**!\n"
            elif effect == "connect":
                attacker.connect_share_turns = 1
                log += f"💰 **Bribe — Connect!** A mystical link is forged — **40% of damage {attacker.user.display_name} takes** is shared with **{defender.user.display_name}** this turn!\n"
        attacker.bribe_concealed_turn = False  # clear the concealed flag

        # Blessing from previous turn
        if attacker.blessed:
            attacker.strength_boost += 3
            attacker.blessed = False
            log += f"🌿 **{attacker.user.display_name}**'s blessing awakens!\n"

        # Planting heal
        if attacker.planting_turns > 0:
            plant_heal = seq_dmg(attacker, 0.074)  # ~20 at Seq 9, scales with sequence
            attacker.hp = min(attacker.max_hp, attacker.hp + plant_heal)
            attacker.planting_turns -= 1
            log += f"🌱 Seed blooms — **{attacker.user.display_name}** restored **{plant_heal} HP**! ({attacker.planting_turns} left)\n"

        # Prayer self-bleed
        if attacker.prayer_active:
            bleed_dmg = random.randint(max(1, int(attacker.max_hp * 0.02)), max(1, int(attacker.max_hp * 0.03)))
            attacker.hp = max(0, attacker.hp - bleed_dmg)
            log += f"🙏🩸 Prayer toll — **{bleed_dmg}** damage to **{attacker.user.display_name}**!\n"

        # Bleed
        if attacker.bleed > 0:
            bleed_dmg = max(1, int(attacker.max_hp * 0.02))
            attacker.hp = max(0, attacker.hp - bleed_dmg)
            attacker.bleed -= 1
            log += f"🩸 **{attacker.user.display_name}** bleeds **{bleed_dmg}** damage! ({attacker.bleed} left)\n"

        # Gun shot permanent bleed
        if attacker.gun_shot_bleed:
            gs_bleed = max(1, int(attacker.max_hp * 0.02))
            attacker.hp = max(0, attacker.hp - gs_bleed)
            log += f"🔫🩸 Gunshot wound bleeds **{gs_bleed}** damage!\n"

        # Burn (witch_burn = 4% max HP, else 3%)
        if attacker.burn > 0:
            b_dmg = max(1, int(attacker.max_hp * (0.04 if attacker.witch_burn else 0.03)))
            attacker.hp = max(0, attacker.hp - b_dmg)
            attacker.burn -= 1
            if attacker.burn == 0:
                attacker.witch_burn = False
            log += f"🔥 **{attacker.user.display_name}** burns for **{b_dmg}** damage! ({attacker.burn} left)\n"

        # Moral freedom: fighter takes scaled damage per turn
        if attacker.moral_freedom_active:
            mf_dmg = getattr(attacker, "_moral_freedom_dot", None)
            if mf_dmg is None:
                mf_dmg = max(1, int(attacker.max_hp * 0.05))
            attacker.hp = max(0, attacker.hp - mf_dmg)
            log += f"👼 **{attacker.user.display_name}** suffers moral freedom — **{mf_dmg}** damage!\n"

        # Dog chase (from trap)
        if attacker.dog_chase_turns > 0:
            dc_pct = getattr(attacker, "_dog_chase_dmg_pct", 0.10)
            dc_dmg = max(1, int(attacker.base_hp * dc_pct))
            attacker.hp = max(0, attacker.hp - dc_dmg)
            attacker.dog_chase_turns -= 1
            log += f"🐕 **{attacker.user.display_name}** is being chased — **{dc_dmg}** damage! ({attacker.dog_chase_turns} left)\n"

        # Conspiracy forced random ability
        if attacker.conspiracy_force_turns > 0 and not attacker.instigated_action:
            all_acts = [k for k in [attacker.role_ability, "attack"] if k]
            if all_acts:
                attacker.instigated_action = random.choice(all_acts)

        # Trap activation (activates randomly across 5 rounds, minimum 1 turn delay)
        # Traps are laid by the defender and triggered when the attacker takes their turn
        if hasattr(defender, "trap_slots") and defender.trap_slots:
            turn_count = battle.get("turn_count", 0) if battle else 0
            turns_since = turn_count - defender.trap_set_turn
            if turns_since >= 1:
                activation_chance = min(0.9, turns_since * 0.18)
                if random.random() < activation_chance:
                    trap = defender.trap_slots.pop(0)
                    if trap == "flames":
                        attacker.apply_status_effect("burn", 3)
                        log += f"🪤 **TRAP!** Fire trap ignites — **{attacker.user.display_name}** burns for **3 turns**!\n"
                    elif trap == "trip":
                        attacker._trip_trap_miss = True
                        log += f"🪤 **TRAP!** Trip wire! **{attacker.user.display_name}** stumbles — next attack misses!\n"
                    elif trap == "dog_chase":
                        pct = random.uniform(0.05, 0.07)
                        dog_dmg = int(attacker.base_hp * pct)
                        attacker.dog_chase_turns = max(getattr(attacker, "dog_chase_turns", 0), 2)
                        attacker._dog_chase_dmg_pct = pct
                        log += f"🪤 **TRAP!** Dogs released! **{attacker.user.display_name}** chased — **{int(pct*100)}% base HP/turn** for **2 turns**!\n"
                    elif trap == "tripwire":
                        backlash = max(int(getattr(attacker, "last_attack_dmg", 0) * 0.30), int(attacker.base_hp * 0.07))
                        attacker.hp = max(0, attacker.hp - backlash)
                        log += f"🪤 **TRAP!** Tripwire! **{attacker.user.display_name}** falls — **{backlash}** backlash damage!\n"
                    # Start cooldown after last trap fires
                    if not defender.trap_slots:
                        defender.trap_cooldown = random.randint(3, 4)

        # Holy water regen
        if attacker.holy_water_regen > 0:
            hw_heal = max(1, int(attacker.max_hp * 0.03))
            attacker.hp = min(attacker.max_hp, attacker.hp + hw_heal)
            log += f"💧 Holy water heals **{attacker.user.display_name}** **{hw_heal} HP**! ({attacker.holy_water_regen - 1} left)\n"
            # tick_cooldowns will decrement this

        # Werewolf regen
        if attacker.transformed:
            wolf_regen = max(1, int(attacker.max_hp * 0.02))
            attacker.hp = min(attacker.max_hp, attacker.hp + wolf_regen)
            log += f"🐺 **{attacker.user.display_name}** regenerates **{wolf_regen} HP**!\n"

        # Sneak attack pending — the attacker is the one who had sneak applied to them, so they lose their turn
        if attacker.sneak_attack_pending:
            attacker.sneak_attack_pending = False
            log += f"🥷 **{attacker.user.display_name}** is reeling from the sneak attack — turn lost!\n"
            regen = attacker.get_sp_regen()
            attacker.sp = min(attacker.max_sp, attacker.sp + regen)
            attacker.tick_cooldowns()
            return log, True

        # Trapped (vine bomb or provocation)
        if attacker.trapped > 0:
            turns_left = attacker.trapped - 1
            attacker.trapped -= 1
            if getattr(attacker, "provocation_trapped", False):
                if attacker.trapped == 0:
                    attacker.provocation_trapped = False
                log += f"😤 **{attacker.user.display_name}** chokes on their own rage, unable to act! " + (f"({turns_left} turn{'s' if turns_left != 1 else ''} remaining)\n" if turns_left > 0 else "*(trap ends next turn)*\n")
            else:
                log += f"🌿 **{attacker.user.display_name}** is trapped in vines — turn lost! " + (f"({turns_left} remaining)\n" if turns_left > 0 else "*(trap ends next turn)*\n")
            regen = attacker.get_sp_regen()
            attacker.sp = min(attacker.max_sp, attacker.sp + regen)
            attacker.tick_cooldowns()
            return log, True

        # Charm (bribe) — cannot attack
        if attacker.charm_turns > 0:
            turns_left = attacker.charm_turns - 1
            attacker.charm_turns -= 1
            if attacker.hurricane_charging:
                attacker.hurricane_charging = False
                attacker.hurricane_interrupt_cd = 5
                log += f"💰 **{attacker.user.display_name}**'s Hurricane of Light is interrupted by charm!\n"
            log += f"💰 **{attacker.user.display_name}** is charmed — cannot attack! " + (f"({turns_left} turns remaining)\n" if turns_left > 0 else "*(charm ends next turn)*\n")
            regen = attacker.get_sp_regen()
            attacker.sp = min(attacker.max_sp, attacker.sp + regen)
            attacker.tick_cooldowns()
            return log, True

        # Paralysis
        if attacker.paralysis > 0:
            turns_left = attacker.paralysis - 1
            attacker.paralysis -= 1
            if attacker.hurricane_charging:
                attacker.hurricane_charging = False
                attacker.hurricane_interrupt_cd = 5
                log += f"⚡ **{attacker.user.display_name}**'s Hurricane of Light is interrupted by paralysis! *(5 turn cd before recharge)*\n"
            log += f"⚡ **{attacker.user.display_name}** is paralysed — turn lost! " + (f"({turns_left} remaining)\n" if turns_left > 0 else "*(paralysis ends next turn)*\n")
            regen = attacker.get_sp_regen()
            attacker.sp = min(attacker.max_sp, attacker.sp + regen)
            attacker.tick_cooldowns()
            return log, True

        # Slumber (sleep)
        if attacker.slumber > 0:
            turns_left = attacker.slumber - 1
            attacker.slumber -= 1
            if attacker.hurricane_charging:
                attacker.hurricane_charging = False
                attacker.hurricane_interrupt_cd = 5
                log += f"💤 **{attacker.user.display_name}**'s Hurricane of Light is interrupted by sleep! *(5 turn cd before recharge)*\n"
            log += f"💤 **{attacker.user.display_name}** is asleep — turn lost! " + (f"({turns_left} remaining)\n" if turns_left > 0 else "*(sleep ends next turn)*\n")
            regen = attacker.get_sp_regen()
            attacker.sp = min(attacker.max_sp, attacker.sp + regen)
            attacker.tick_cooldowns()
            return log, True

        # Freeze skip (first turn)
        if attacker.freeze > 0:
            turns_left = attacker.freeze - 1
            attacker.freeze -= 1
            if attacker.freeze_weakened is False and attacker.hurricane_charging:
                attacker.hurricane_charging = False
                attacker.hurricane_interrupt_cd = 5
                log += f"🧊 **{attacker.user.display_name}**'s Hurricane of Light is interrupted by freeze! *(5 turn cd before recharge)*\n"
            if attacker.freeze == 0:
                attacker.freeze_weakened = True
            log += f"🧊 **{attacker.user.display_name}** is frozen — turn lost! " + (f"({turns_left} remaining)\n" if turns_left > 0 else "*(freeze ends next turn, accuracy weakened)*\n")
            regen = attacker.get_sp_regen()
            attacker.sp = min(attacker.max_sp, attacker.sp + regen)
            attacker.tick_cooldowns()
            return log, True

        # Listener self-stun check
        if attacker.listening_active:
            if random.randint(1, 3) == 1:
                log += f"👂 **{attacker.user.display_name}**'s listening backfires — stunned this turn!\n"
                regen = attacker.get_sp_regen()
                attacker.sp = min(attacker.max_sp, attacker.sp + regen)
                attacker.tick_cooldowns()
                return log, True

        # Study restore
        if attacker.study_skip:
            attacker.study_skip = False
            attacker.sp = attacker.max_sp
            log += f"📚 **{attacker.user.display_name}** finishes studying — SP fully restored!\n"

        # Combat studies skip
        if attacker.combat_studies_skip:
            attacker.combat_studies_skip = False
            attacker.combat_studies_active = True
            log += f"📘 **{attacker.user.display_name}** finishes studying — attacks permanently boosted by **20%**!\n"
            regen = attacker.get_sp_regen()
            attacker.sp = min(attacker.max_sp, attacker.sp + regen)
            attacker.tick_cooldowns()
            return log, True

        # Animal companion attacks
        if attacker.animal_companion_active:
            attacker.animal_companion_turn += 1
            if attacker.animal_companion_turn % 2 == 0:
                actual_dmg = defender.apply_damage(seq_dmg(attacker, 0.045))
                log += f"🐾 **{attacker.user.display_name}**'s companion strikes **{defender.user.display_name}** for **{actual_dmg}** damage!\n"

        # Seq 6: Spirit Guide deals 20 damage per turn and costs 15 SP to maintain
        if attacker.spirit_guide_active:
            sg_sp_cost = max(5, int(attacker.max_sp * 0.06))  # ~6% max SP per turn
            if attacker.spirit_guide_turns >= 5:
                attacker.spirit_guide_active = False
                attacker.spirit_guide_turns = 0
                attacker.spirit_guide_cooldown = 7
                log += f"👻 **Spirit Guide** departs — reached its limit of 5 turns! *(7 turn cd)*\n"
            elif attacker.sp < sg_sp_cost:
                attacker.spirit_guide_active = False
                attacker.spirit_guide_turns = 0
                attacker.spirit_guide_cooldown = 7
                log += f"👻 **Spirit Guide** fades — insufficient SP to maintain! *(7 turn cd)*\n"
            elif random.random() < 0.30:
                # 30% chance: spirit possesses the user — they lose their turn
                attacker.sp -= sg_sp_cost
                log += f"👻 **Spirit Guide** turns on **{attacker.user.display_name}** — possessed! Turn lost! *(−{sg_sp_cost} SP)*\n"
                regen = attacker.get_sp_regen()
                attacker.sp = min(attacker.max_sp, attacker.sp + regen)
                attacker.tick_cooldowns()
                return log, True
            else:
                attacker.sp -= sg_sp_cost
                actual_dmg = defender.apply_damage(seq_dmg(attacker, 0.091))
                log += f"👻 **Spirit Guide** strikes **{defender.user.display_name}** for **{actual_dmg}** damage! *(−{sg_sp_cost} SP, {5 - attacker.spirit_guide_turns} turns left)*\n"

        # Seq 6: Devil Form madness — 30% chance to lose own turn
        if attacker.devil_form_active:
            if random.random() < 0.30:
                log += f"😈 **{attacker.user.display_name}** succumbs to devil madness — turn lost!\n"
                regen = attacker.get_sp_regen()
                attacker.sp = min(attacker.max_sp, attacker.sp + regen)
                attacker.tick_cooldowns()
                return log, True

        # Seq 6: Charm — attacker (who is charmed) loses their turn
        if attacker.charm_turns > 0:
            attacker.charm_turns -= 1
            log += f"💋 **{attacker.user.display_name}** is charmed — turn lost! ({attacker.charm_turns} left)\n"
            regen = attacker.get_sp_regen()
            attacker.sp = min(attacker.max_sp, attacker.sp + regen)
            attacker.tick_cooldowns()
            return log, True

        # Seq 6: Scroll semi-permanent status re-apply (20% chance each turn, applies to defender)
        if attacker.scroll_status and random.random() < 0.20:
            defender.apply_status_effect(attacker.scroll_status, 1)
            log += f"📜 **Scroll** re-applies **{attacker.scroll_status}** to **{defender.user.display_name}**!\n"

        # Seq 6: Wind Blessed SP drain during buildup
        if attacker.wind_blessed_buildup > 0:
            if attacker.sp >= 25:
                attacker.sp -= 25
                attacker.wind_blessed_buildup += 1
                log += f"🌪️ **Wind Blessed** charging… *(round {attacker.wind_blessed_buildup - 1}/2, −25 SP)*\n"
                if attacker.wind_blessed_buildup > 2:
                    attacker.wind_blessed_buildup = 0
                    attacker.wind_blessed_cooldown = 5
                    if defender.phasing_active:
                        defender.phasing_active = False
                        defender.phasing_cooldown = 1
                        log += f"🌪️ **WIND BLESSED UNLEASHED!** — but **{defender.user.display_name}** phases through the blast!\n"
                    else:
                        actual_dmg = defender.apply_damage(seq_dmg(attacker, 0.273), is_ability=True)
                        log += f"🌪️ **WIND BLESSED UNLEASHED!** **{actual_dmg}** crushing damage! *(5 turn cd)*\n"
            else:
                attacker.wind_blessed_buildup = 0
                log += f"🌪️ **Wind Blessed** fizzled — insufficient SP during buildup!\n"

        attacker.tick_cooldowns()

        # Seq 5: Spirit Thread — escalating stun chance
        if attacker.spirit_thread_active:
            attacker.spirit_thread_turns += 1
            attacker.spirit_thread_stun_pct = min(100, attacker.spirit_thread_turns * 10)
            if random.randint(1, 100) <= attacker.spirit_thread_stun_pct:
                attacker.spirit_thread_active = False
                attacker.spirit_thread_cooldown = 5
                log += f"🧵 **Spirit Thread** — **{attacker.user.display_name}**'s spirit threads snap — stunned and thread control ends! *(5 turn cd)*\n"
                regen = attacker.get_sp_regen()
                attacker.sp = min(attacker.max_sp, attacker.sp + regen)
                return log, True

        # Seq 5: Marionette — 50% attack / 50% intercept each turn
        if attacker.marionette_active:
            attacker.marionette_turns -= 1
            if attacker.marionette_turns <= 0:
                attacker.marionette_active = False
                log += f"🎭 **Marionette** dissolves!\n"
            elif random.random() < 0.50:
                m_dmg = seq_val(attacker, 10)
                actual_dmg = defender.apply_damage(m_dmg, is_ability=True)
                log += f"🎭 **Marionette** strikes **{defender.user.display_name}** for **{actual_dmg}** damage! ({attacker.marionette_turns} turns left)\n"

        # Seq 5: Disease Propagation — growing DoT
        if attacker.disease_propagation_active:
            attacker.disease_propagation_turns += 1
            base_dot = seq_val(attacker, 15)
            base_dmg = base_dot + int(attacker.disease_propagation_turns * attacker.max_hp * 0.01)
            actual_dmg = defender.apply_damage(base_dmg, is_ability=True)
            log += f"🦠 **Plague** ravages **{defender.user.display_name}** for **{actual_dmg}** damage! (turn {attacker.disease_propagation_turns})\n"

        # Seq 5: Dragged to Hell — drain self 15 SP/round (scales as pct of max_sp)
        if attacker.dragged_to_hell_active:
            drain = max(1, int(attacker.max_sp * 0.07))
            attacker.sp = max(0, attacker.sp - drain)
            log += f"🔥 **Gate to Hell** drains **{drain} SP** from **{attacker.user.display_name}**!\n"

        # Seq 5: Evil Sealing — 10 recoil per round charged (scaled)
        if attacker.evil_sealing_active:
            recoil = seq_val(attacker, 10)
            charge = seq_val(attacker, 30)
            attacker.hp = max(0, attacker.hp - recoil)
            attacker.evil_sealing_damage += charge
            log += f"💀 **Evil Sealing** charging — power at **{attacker.evil_sealing_damage}** dmg ({recoil} recoil taken)!\n"

        # Seq 5: Spiritual Takeover — scaled dmg/round on victim
        if defender.spiritual_takeover_active:
            dot = seq_val(attacker, 15)
            actual_dmg = defender.apply_damage(dot, is_ability=True)
            log += f"👻 **Spiritual Takeover** — **{defender.user.display_name}** suffers **{actual_dmg}** spirit damage!\n"

        # Seq 5: Possession — scaled dmg/round
        if attacker.possession_active:
            attacker.possession_turns -= 1
            if attacker.possession_turns <= 0:
                attacker.possession_active = False
                log += f"👁️ **Possession** ends!\n"
            else:
                dot = seq_val(attacker, 20)
                actual_dmg = defender.apply_damage(dot, is_ability=True)
                log += f"👁️ **Possession** tears at **{defender.user.display_name}** — **{actual_dmg}** damage! ({attacker.possession_turns} turns left)\n"

        # Seq 5: Wrath of Nature — thorn recoil (scaled)
        if attacker.wrath_of_nature_thorn and defender.hp > 0:
            recoil = seq_val(attacker, 5)
            defender.hp = max(0, defender.hp - recoil)
            log += f"🌿 **Thorn Coat** deals **{recoil}** recoil to **{defender.user.display_name}**!\n"

        # Seq 5: Wrath of Nature — poison DoT (scaled)
        if attacker.wrath_of_nature_poison_turns > 0:
            dot = seq_val(attacker, 15)
            actual_dmg = defender.apply_damage(dot, is_ability=True)
            log += f"☠️ **Poison Vine** poisons **{defender.user.display_name}** for **{actual_dmg}** dmg! ({attacker.wrath_of_nature_poison_turns} left)\n"

        # Seq 5: Star of Curses — one random curse per round
        if attacker.star_of_curses_turns > 0:
            curse = random.choice(["slow", "weakness", "mutation", "trauma"])
            if curse == "slow":
                log += f"🌟 **Star of Curses — Slow!** **{defender.user.display_name}** accuracy reduced by 30%!\n"
                defender.conspiracy_miss_turns = max(defender.conspiracy_miss_turns, 1)
            elif curse == "weakness":
                defender.debuff_stacks = max(defender.debuff_stacks, 1)
                log += f"🌟 **Star of Curses — Weakness!** **{defender.user.display_name}** damage reduced by 30%!\n"
            elif curse == "mutation":
                mut_dmg = seq_val(attacker, 30)
                actual_dmg = defender.apply_damage(mut_dmg, is_ability=True)
                log += f"🌟 **Star of Curses — Mutation!** **{defender.user.display_name}** takes **{actual_dmg}** damage!\n"
            elif curse == "trauma":
                log += f"🌟 **Star of Curses — Trauma!** **{defender.user.display_name}** flinches — turn lost!\n"
                regen = defender.get_sp_regen()
                defender.sp = min(defender.max_sp, defender.sp + regen)

        # Seq 5: Curse of Misfortune — recoil on the cursed fighter each round
        if attacker.curse_of_misfortune_turns > 0:
            recoil = seq_val(attacker, 20)
            attacker.hp = max(0, attacker.hp - recoil)
            log += f"🎲 **Misfortune** recoils — **{attacker.user.display_name}** takes **{recoil}** self damage!\n"

        # Seq 5: Active Luck Boost — 10% random event each turn (scaled)
        if attacker.active_luck_boost_turns > 0:
            if random.random() < 0.10:
                lucky_hit = seq_val(attacker, 40)
                actual_dmg = defender.apply_damage(lucky_hit, is_ability=True)
                log += f"🍀 **Lucky Event!** Fortune strikes **{defender.user.display_name}** for **{actual_dmg}** damage!\n"
        if attacker.danger_intuition_active:
            if attacker.sp >= attacker.intuition_upkeep_sp:
                attacker.sp -= attacker.intuition_upkeep_sp
                log += f"👁️ **Danger Intuition** active — **{attacker.intuition_upkeep_sp} SP** upkeep paid.\n"
            else:
                attacker.danger_intuition_active = False
                log += f"👁️ **Danger Intuition** deactivated — insufficient SP!\n"

        # Connect damage share — tick down
        if attacker.connect_share_turns > 0:
            attacker.connect_share_turns -= 1

        regen = attacker.get_sp_regen()
        attacker.sp = min(attacker.max_sp, attacker.sp + regen)
        if not battle.get("trap_set_this_turn"):
            if attacker.sp < attacker.max_sp * 0.3:
                log += f"*⚠️ {attacker.user.display_name} is low on SP! `{attacker.sp}/{attacker.max_sp}`*\n"
            # Only show regen when meaningful — skip if full
            elif attacker.sp < attacker.max_sp:
                log += f"*+{regen} SP → `{attacker.sp}/{attacker.max_sp}`*\n"
        # Comeback alert — compact single line
        hp_pct = attacker.hp / max(1, attacker.max_hp)
        if hp_pct <= 0.20 and attacker.hp > 0:
            log += f"*🔥 {attacker.user.display_name} is on the brink (+20% dmg)*\n"
        elif hp_pct <= 0.40 and attacker.hp > 0:
            log += f"*💢 {attacker.user.display_name} is bloodied (+10% dmg)*\n"
        battle.pop("trap_set_this_turn", None)
        return log, False

    def make_battle_embed(self, description, battle, next_turn_name, color=0x9B59B6):
        turn_count = battle.get("turn_count", 0)
        # Keep description tight — trim if too long
        if len(description) > 800:
            description = "…" + description[-797:]
        embed = discord.Embed(description=description, color=color)
        embed.add_field(name="\u200b", value=self.battle_status(battle), inline=False)
        embed.set_footer(text=f"Turn {turn_count}  •  ⚡ {next_turn_name}'s move  •  /leave to forfeit")
        return embed

    def _make_battle_view(self, battle):
        # Deferred import: battle.views imports plenty from this module
        # (battles, get_battle, swap_turn, ...), so importing BattleView at
        # module level here would create a circular import. By the time
        # this method actually runs, both modules are fully loaded.
        from battle.views import BattleView
        old_view = battle.get("view")
        if old_view is not None:
            old_view.stop()  # disable buttons on old view so players can't click stale UI
        view = BattleView(battle)
        battle["view"] = view
        return view

    def resolve_bets(self, channel_id, winner: discord.Member, loser: discord.Member, guild_id: int = 0):
        bets = self.active_bets.pop(channel_id, {})
        if not bets:
            return ""
        log = "\n\n💰 **Betting Results:**\n"
        for bettor_id, bet in bets.items():
            amount = bet["amount"]
            target = bet["target"]
            if target.id == winner.id:
                winnings = amount * 2
                add_pounds(bettor_id, winnings, guild_id=guild_id)
                log += f"> 🏆 <@{bettor_id}> won **{winnings:,} soli**!\n"
            else:
                log += f"> 💸 <@{bettor_id}> lost **{amount:,} soli**.\n"
        return log

    async def safe_edit(self, message: discord.Message, **kwargs):
        try:
            await message.edit(**kwargs)
        except (discord.errors.NotFound, discord.errors.HTTPException):
            pass

    async def safe_respond(self, interaction: discord.Interaction, content: str, ephemeral: bool = True):
        """Send a response safely — handles already-acknowledged and expired interactions."""
        try:
            if interaction.response.is_done():
                await interaction.followup.send(content, ephemeral=ephemeral)
            else:
                await interaction.response.send_message(content, ephemeral=ephemeral)
        except (discord.errors.NotFound, discord.errors.InteractionResponded, discord.errors.HTTPException):
            pass

    async def _view_on_error(self, interaction: discord.Interaction, error: Exception, item):
        """Shared on_error for all Views — prevents silent button death."""
        logger.error(f"[View error] {item!r}: {error}", exc_info=error)
        await self.safe_respond(interaction, f"❌ Something went wrong: `{type(error).__name__}`")

    async def send_and_replace(self, battle: dict, channel, embed, view=None):
        """Delete old message instantly, send new one with player pings."""
        c = battle["challenger"]
        o = battle["opponent"]
        ping = f"{c.user.mention} {o.user.mention}"
        old_msg = battle.get("battle_message")
        if old_msg:
            try:
                await old_msg.delete()
            except Exception:
                logger.debug("[send_and_replace] Could not delete old battle message (likely already gone)", exc_info=True)
        try:
            new_msg = await channel.send(content=ping, embed=embed, view=view)
            battle["battle_message"] = new_msg
            return new_msg
        except (discord.errors.HTTPException, discord.errors.NotFound):
            logger.warning("[send_and_replace] Failed to send battle update", exc_info=True)
            return None



    async def inactivity_watcher(self, battle_key: tuple, channel_id: int):
        """Ends a fight if nobody takes an action for INACTIVITY_TIMEOUT seconds."""
        import time
        while True:
            await asyncio.sleep(30)  # check every 30 seconds
            battle = self.battles.get(battle_key)
            if battle is None:
                return  # battle already ended normally
            if not battle.get("accepted"):
                return  # not started yet — ChallengeView timeout handles this
            last = battle.get("last_action_time", time.time())
            if (time.time() - last) >= INACTIVITY_TIMEOUT:
                # Inactivity timeout — end the fight
                self.battles.pop(battle_key, None)
                # Refund any bets
                bets = self.active_bets.pop(channel_id, {})
                for bettor_id, bet in bets.items():
                    add_pounds(bettor_id, bet["amount"], guild_id=battle.get("guild_id", 0))
                channel = bot.get_channel(channel_id)
                if channel:
                    c = battle["challenger"]
                    o = battle["opponent"]
                    embed = discord.Embed(
                        title="⏳ Fight Over — Inactivity",
                        description=(
                            f"Neither **{c.user.display_name}** nor **{o.user.display_name}** "
                            f"responded for **3 minutes**.\n\n"
                            f"The fight has been called off. No winner, no loser.\n"
                            f"💰 Bets refunded."
                        ),
                        color=0x95A5A6
                    )
                    old_msg = battle.get("battle_message")
                    if old_msg:
                        try:
                            await old_msg.delete()
                        except Exception:
                            logger.debug("[inactivity_watcher] Could not delete old battle message", exc_info=True)
                    try:
                        await channel.send(
                            content=f"{c.user.mention} {o.user.mention}",
                            embed=embed
                        )
                    except Exception:
                        logger.warning("[inactivity_watcher] Failed to send timeout notice", exc_info=True)
                return

    # ── Battle log ────────────────────────────────────────────
    battle_log = []  # list of dicts recorded per battle

    def record_battle(self, winner: discord.Member, loser: discord.Member, winner_seq: int, loser_seq: int, guild_id: int = 0):
        import time
        self.battle_log.append({
            "winner": winner.display_name,
            "winner_id": winner.id,
            "loser": loser.display_name,
            "loser_id": loser.id,
            "winner_seq": winner_seq,
            "loser_seq": loser_seq,
            "ts": int(time.time()),
        })
        if len(self.battle_log) > 100:
            self.battle_log.pop(0)
        # Track win/loss streaks in economy data
        w_eco = get_economy(winner.id, guild_id=guild_id)
        l_eco = get_economy(loser.id, guild_id=guild_id)
        w_eco.setdefault("wins", 0); w_eco.setdefault("losses", 0); w_eco.setdefault("win_streak", 0)
        l_eco.setdefault("wins", 0); l_eco.setdefault("losses", 0); l_eco.setdefault("win_streak", 0)
        w_eco["wins"] += 1
        w_eco["win_streak"] += 1
        l_eco["losses"] += 1
        l_eco["win_streak"] = 0
        save_economy()

    async def post_battle_log(self, guild: discord.Guild, winner: discord.Member, loser: discord.Member,
                              winner_seq: int, loser_seq: int, reward: int, guild_id: int = 0):
        """Send a battle result embed to the guild's log channel if configured."""
        if not guild:
            return
        ch_id = log_channel.get(guild.id)
        if not ch_id:
            return
        ch = guild.get_channel(ch_id)
        if not ch:
            return
        w_eco = get_economy(winner.id, guild_id=guild_id)
        streak = w_eco.get("win_streak", 1)
        wins = w_eco.get("wins", 1)
        losses = get_economy(loser.id, guild_id=guild_id).get("losses", 1)
        streak_txt = f" 🔥 **{streak} win streak!**" if streak >= 2 else ""
        embed = discord.Embed(
            title="⚔️ Battle Result",
            description=(
                f"🏆 **{winner.display_name}** *(Seq {winner_seq})* defeated **{loser.display_name}** *(Seq {loser_seq})*{streak_txt}\n"
                f"💰 **+{reward:,} soli** awarded\n"
                f"📊 {winner.mention} — {wins}W / {w_eco.get('losses', 0)}L | "
                f"{loser.mention} — {get_economy(loser.id, guild_id=guild_id).get('wins', 0)}W / {losses}L"
            ),
            color=0xF1C40F
        )
        import time
        embed.set_footer(text=time.strftime("%d/%m/%Y %H:%M"))
        try:
            await ch.send(embed=embed)
        except Exception:
            logger.warning("[log] Failed to post battle log", exc_info=True)



    def check_victory(self, attacker, defender, battle_key, msg):
        if not defender.is_alive() or not attacker.is_alive():
            winner = attacker if defender.hp <= 0 else defender
            loser = defender if defender.hp <= 0 else attacker
            channel_id = battle_key[0] if isinstance(battle_key, tuple) else battle_key
            # Extract guild_id BEFORE popping the battle from the dict
            b = self.battles.get(battle_key)
            guild_id = b["guild_id"] if b else 0
            bet_log = self.resolve_bets(channel_id, winner.user, loser.user, guild_id)
            self.battles.pop(battle_key, None)

            # Soli reward based on loser's sequence
            loser_seq = get_seq_number(loser.role) if loser.role else 9
            winner_seq = get_seq_number(winner.role) if winner.role else 9
            reward = SEQ_WIN_REWARD.get(loser_seq, 1000)
            add_pounds(winner.user.id, reward, guild_id=guild_id)

            # Bounty claim — if loser had a bounty, winner claims it
            bounty_amount = claim_bounty(guild_id, loser.user.id)
            if bounty_amount > 0:
                add_pounds(winner.user.id, bounty_amount, guild_id=guild_id)
                _bchan = bot.get_channel(channel_id)
                if _bchan:
                    async def _send_bounty_msg(ch=_bchan, w=winner, l=loser, amt=bounty_amount):
                        await ch.send(
                            f"🎯 **Bounty Claimed!** **{w.user.display_name}** collected "
                            f"**{amt:,} soli** for defeating **{l.user.display_name}**!"
                        )
                    bot.loop.create_task(_send_bounty_msg())

            self.record_battle(winner.user, loser.user, winner_seq, loser_seq, guild_id)
            guild = winner.user.guild if hasattr(winner.user, "guild") else None
            bot.loop.create_task(self.post_battle_log(guild, winner.user, loser.user, winner_seq, loser_seq, reward, guild_id))
            bot.loop.create_task(award_combat_xp(winner.user, loser_seq, guild))
            bot.loop.create_task(penalize_combat_xp(loser.user, loser_seq, guild))

            xp_gain = SEQ_WIN_XP.get(loser_seq, 150)
            xp_penalty = SEQ_LOSS_XP.get(loser_seq, 60)
            turns_fought = b.get("turn_count", 0) if b else 0
            hp_remaining_pct = int((winner.hp / max(1, winner.max_hp)) * 100)
            # Flavour line based on how close the fight was
            if loser.hp <= 0 and winner.hp <= int(winner.max_hp * 0.15):
                fight_flavour = "⚡ *A razor-thin victory — both fighters nearly destroyed!*"
            elif turns_fought >= 30:
                fight_flavour = f"⏳ *An epic {turns_fought}-turn war of attrition.*"
            elif turns_fought <= 5:
                fight_flavour = "💨 *A dominant, lightning-fast finish.*"
            else:
                fight_flavour = f"⚔️ *Fought across {turns_fought} turns.*"

            # Random chance for the winner to score a bonus stat point, independent
            # of the Sequence-based pool
            bonus_point_line = ""
            if random.random() < BONUS_STAT_POINT_CHANCE:
                bonus_record = get_or_create_stats(winner.user.id, winner.user)
                bonus_record["points"] += 1
                save_stats()
                bonus_point_line = (
                    f"\n🎲 **{winner.user.display_name}** gains a flash of insight mid-battle — "
                    f"**+1 bonus stat point!** *(spend it with `/stats_user`)*"
                )

            embed = discord.Embed(
                title="𓂃 🏆 Victory!",
                description=(
                    msg
                    + f"\n\n**{loser.user.display_name}** falls.\n"
                    + f"✨ **{winner.user.display_name}** stands victorious at **{winner.hp}/{winner.max_hp} HP** ({hp_remaining_pct}%)!\n"
                    + f"{fight_flavour}\n\n"
                    + f"💰 **+{reward:,} soli** awarded for defeating a Seq {loser_seq}!\n"
                    + f"📈 **{winner.user.display_name}** gains **+{xp_gain} EXP**\n"
                    + f"📉 **{loser.user.display_name}** loses **-{xp_penalty} EXP**"
                    + bonus_point_line
                    + bet_log
                ),
                color=0xF1C40F
            )
            return embed
        return None

    def get_available_actions(self, fighter: Fighter, battle) -> list:
        """Returns list of action keys available to this fighter."""
        actions = ["attack", "special"]
        # Add purchased spells
        owned = get_economy(fighter.user.id, guild_id=battle["guild_id"])["spells"]
        actions.extend(owned)
        # Add all pathway role abilities (Seq 9, 8, 7...)
        for role_name in fighter.all_roles:
            ak = ROLE_ABILITIES.get(role_name)
            if ak and ak not in actions and not fighter.role_ability_disabled > 0:
                actions.append(ak)
            ak2 = ROLE_ABILITIES_2.get(role_name)
            if ak2 and ak2 not in actions:
                actions.append(ak2)
        return actions

    # ── Views ─────────────────────────────────────────────────


battle_manager = BattleManager()

# Bare-name aliases — same underlying dict/list objects as
# `battle_manager.xxx` (see module docstring above).
battles = battle_manager.battles
active_bets = battle_manager.active_bets
battle_log = battle_manager.battle_log

_battle_key = battle_manager._battle_key
_make_battle_view = battle_manager._make_battle_view
_view_on_error = battle_manager._view_on_error
get_battle = battle_manager.get_battle
get_battle_key_for_user = battle_manager.get_battle_key_for_user
find_battle_by_players = battle_manager.find_battle_by_players
swap_turn = battle_manager.swap_turn
get_fighters = battle_manager.get_fighters
validate_turn = battle_manager.validate_turn
hp_bar_visual = battle_manager.hp_bar_visual
get_status_icons = battle_manager.get_status_icons
battle_status = battle_manager.battle_status
apply_turn_effects = battle_manager.apply_turn_effects
make_battle_embed = battle_manager.make_battle_embed
resolve_bets = battle_manager.resolve_bets
safe_edit = battle_manager.safe_edit
safe_respond = battle_manager.safe_respond
send_and_replace = battle_manager.send_and_replace
inactivity_watcher = battle_manager.inactivity_watcher
record_battle = battle_manager.record_battle
post_battle_log = battle_manager.post_battle_log
check_victory = battle_manager.check_victory
get_available_actions = battle_manager.get_available_actions
