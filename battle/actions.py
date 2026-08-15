# ============================================================
# BATTLE ACTIONS — resolves a single turn's chosen action for every
# pathway ability in the game. `process_action` is the public entry
# point (called from BattleView's buttons and the various pathway
# ability menus); `_process_action_inner` is the large per-ability
# dispatch table that actually applies effects, damage, healing,
# status conditions, checks victory, and renders the result embed.
#
# This function is intentionally kept as one large, linear dispatcher
# rather than split into dozens of tiny handlers — the whole point is a
# single readable place to see exactly what every ability in the game
# does, and splitting it purely for line-count would have meant
# rewriting (not just relocating) thousands of lines of combat balance
# logic, which is the highest-risk kind of change in a refactor like
# this. It's been given its own focused module instead.
# ============================================================
import random
import discord

from bot_instance import bot
from fighter import Fighter, seq_dmg, seq_gap_check, seq_val, get_or_create_stats
from roles import get_ability_info, get_pathway_name, get_seq_number
from persistence import save_stats
from economy import add_pounds, get_pounds, remove_pounds, owns_spell, claim_bounty
from xp import award_combat_xp, penalize_combat_xp, SEQ_WIN_XP, SEQ_LOSS_XP
from game_data import BONUS_STAT_POINT_CHANCE, ROLE_ABILITIES, SEQ_AVG_HP, SEQ_WIN_REWARD
from battle.manager import (
    battles, active_bets, get_battle, get_battle_key_for_user, get_fighters,
    validate_turn, apply_turn_effects, make_battle_embed, _make_battle_view,
    resolve_bets, safe_respond, send_and_replace, swap_turn, check_victory,
    record_battle, post_battle_log,
)

def check_divination_dodge(attacker: "Fighter", defender: "Fighter") -> tuple:
    """Check if defender dodges an incoming ability via Divination.
    Returns (dodged: bool, dodge_msg: str)."""
    if not getattr(defender, "divination_dodge", False):
        return False, ""
    dodge_chance = defender._seer_dodge_chance or 70
    defender.divination_dodge_turns = max(0, defender.divination_dodge_turns - 1)
    if defender.divination_dodge_turns <= 0:
        defender.divination_dodge = False
        defender._seer_dodge_chance = None
    if random.uniform(0, 100) < dodge_chance:
        return True, f"🔮 **{defender.user.display_name}** reads the ability and vanishes! *(divination dodge {int(dodge_chance)}%)*"
    return False, ""

def apply_ability_damage(attacker: "Fighter", defender: "Fighter", pct: float,
                         is_ability=True, skill_key=None, battle=None) -> int:
    """Apply ability damage scaled to attacker's sequence average HP.
    pct is a fraction (e.g. 0.30 = 30% of SEQ_AVG_HP).
    Returns 0 if divination dodges it — caller should check and append dodge_msg."""
    dodged, _dodge_msg = check_divination_dodge(attacker, defender)
    if dodged:
        # Store the message for the caller to append
        attacker._last_divination_msg = _dodge_msg
        return 0
    attacker._last_divination_msg = ""
    dmg = int(seq_dmg(attacker, pct) * attacker.get_attack_mult())
    # Polymath: studied ability fires at 70% potency
    if getattr(attacker, "polymath_pending", False):
        dmg = int(dmg * 0.70)
        attacker.polymath_pending = False
    actual = defender.apply_damage(dmg, is_ability=is_ability, skill_key=skill_key)
    # Mental Reflect (Visionary Seq 8) — reflects 50% ability damage back to attacker
    if getattr(defender, "_mental_reflect", False):
        defender._mental_reflect = False
        reflect = max(1, actual // 2)
        attacker.hp = max(0, attacker.hp - reflect)
        attacker._last_divination_msg += f"\n🪞 **Mental Reflect!** {reflect} damage reflected back at **{attacker.user.display_name}**!"
    return actual

def _abil_dmg(attacker, defender, pct, msg_list, is_ability=True, skill_key=None):
    """Apply ability damage and append any divination dodge message to msg_list[0].
    Returns actual damage dealt (0 if dodged)."""
    result = apply_ability_damage(attacker, defender, pct, is_ability=is_ability, skill_key=skill_key)
    div_msg = getattr(attacker, "_last_divination_msg", "")
    if div_msg:
        msg_list[0] += div_msg + "\n"
    return result


def _check_cooldown(attacker: Fighter, key: str) -> str:
    """Returns cooldown message or empty string."""
    checks = {
        "desecration":          (attacker.desecration_cooldown,    "Desecration"),
        "trap":                 (attacker.trap_cooldown,           "Trap"),
        "balancing_act":        (attacker.balancing_cooldown,      "Balancing Act"),
        "phasing":              (attacker.phasing_cooldown,        "Phasing"),
        "pickpocket":           (attacker.pickpocket_cooldown,     "Pickpocket"),
        "vital_strike":         (attacker.vital_strike_cooldown,   "Vital Strike"),
        "card_tricks":          (attacker.card_tricks_cooldown,    "Card Tricks"),
        "arbitration":          (attacker.arbiter_cooldown,        "Arbiter"),
        "jurisdiction":         (attacker.jurisdiction_cooldown,   "Jurisdiction"),
        "testimony":            (attacker.testimony_cooldown,       "Testimony"),
        "rage_baited":          (attacker.rage_baited_cooldown,    "Rage Baited"),
        "poem":                 (attacker.poem_cooldown,           "Poem"),
        "burial_restoration":   (attacker.burial_cooldown,        "Burial Restoration"),
        "crime":                (attacker.crime_cooldown,          "Crime"),
        "magic_trick":          (attacker.magic_trick_cooldown,    "Magic Trick"),
        "brilliant_light":      (attacker.brilliant_light_cooldown,"Brilliant Light"),
        "witch_curse":          (attacker.witch_curse_cooldown,    "Witch's Curse"),
        "domination":           (attacker.domination_cooldown,     "Domination"),
        "bribe":                (attacker.bribe_cooldown,          "Bribe"),
        # Seq 7
        "observation":          (attacker.observation_cooldown,    "Observation"),
        "therapy":              (attacker.therapy_cooldown,        "Therapy"),
        "water_bullet":         (attacker.water_bullet_cooldown,   "Water Bullet"),
        "gun_shot":             (attacker.gun_shot_cooldown,        "Gun Shot"),
        "sneak_attack":         (attacker.sneak_attack_cooldown,   "Sneak Attack"),
        "sleep_spell":          (attacker.sleep_spell_cooldown,    "Sleep Spell"),
        "summoning_dead":       (attacker.summoning_dead_cooldown, "Summoning Dead"),
        "weapon_throw":         (attacker.weapon_throw_cooldown,   "Weapon Throw"),
        "appraisal":            (attacker.appraisal_cooldown,      "Appraisal"),
        "blood_sucker":         (attacker.blood_sucker_cooldown,   "Blood Sucker"),
        "vine_bomb":            (attacker.vine_bomb_cooldown,      "Vine Bomb"),
        "planting":             (attacker.planting_cooldown,       "Planting"),
        "illegal_trade":        (attacker.illegal_trade_cooldown,  "Illegal Trade"),
        "mental_piercing":      (attacker.mental_piercing_cooldown,"Mental Piercing"),
        "magic_spell":          (attacker.magic_spell_cooldown,    "Magic Spell"),
        # Seq 6
        "faceless":             (attacker.faceless_cooldown,       "Faceless"),
        "prometheus":           (attacker.prometheus_cooldown,     "Prometheus"),
        "scribe":               (attacker.scribe_cooldown,         "Scribe"),
        "hypnotist":            (attacker.hypnotist_cooldown,      "Hypnotist"),
        "notary":               (attacker.notary_cooldown,         "Notary"),
        "wind_blessed":         (attacker.wind_blessed_cooldown,   "Wind Blessed"),
        "rose_bishop":          (attacker.rose_bishop_cooldown,    "Rose Bishop"),
        "polymath":             (attacker.polymath_cooldown,       "Polymath"),
        "ritualistic_reasoning":(attacker.ritualistic_reasoning_cooldown, "Ritualistic Reasoning"),
        "pacification":         (attacker.soul_assurer_cooldown,   "Pacification"),
        "spirit_guide":         (attacker.spirit_guide_cooldown,   "Spirit Guide"),
        "glance_into_fate":     (attacker.glance_fate_cooldown,    "Glance into Fate"),
        "pleasure_witch":       (attacker.pleasure_witch_cooldown, "Pleasure Witch"),
        "fire_ravens":          (attacker.fire_ravens_cooldown,    "Fire Ravens"),
        "conspiracy":           (attacker.conspirer_cooldown,      "Conspiracy"),
        "hurricane_of_light":   (attacker.hurricane_cooldown,     "Hurricane of Light"),
        "scrolls_professor":    (attacker.scrolls_professor_cooldown, "Scrolls Professor"),
        "artisan":              (attacker.artisan_cooldown,        "Artisan"),
        "calamity_priest":      (attacker.calamity_priest_cooldown,"Calamity Priest"),
        "potions_professor":    (attacker.potions_professor_cooldown, "Potions Professor"),
        "biologist":            (attacker.biologist_cooldown,      "Biologist"),
        "zombie":               (attacker.zombie_cooldown,         "Zombie"),
        "devil_form":           (attacker.devil_form_cooldown,     "Devil Form"),
        "distortion":           (attacker.distortion_cooldown,     "Distortion"),
        "judge":                (attacker.judge_cooldown,          "Judge"),
    }
    one_use = {
        "night_boost":    attacker.night_boost_used,
        "moral_freedom":  attacker.moral_freedom_used,
        "power_up_punch": attacker.folk_of_rage_active,
        # gun_shot now uses cooldown, not one-use flag
        "lucky_day":      attacker.lucky_day_used,
        "transformation": attacker.transformation_used,
        "vile_crime":     attacker.vile_crime_used,
        "astrology":      attacker.astrology_used,
    }
    for k, used in one_use.items():
        if key == k and used:
            return f"❌ **{k.replace('_',' ').title()}** already used this battle!"
    if key in checks:
        cd, name = checks[key]
        if cd > 0:
            return f"❌ **{name}** on cooldown for **{cd}** more turn(s)."
    if key == "analyse" and attacker.analyse_uses <= 0:
        return "❌ **Analyse** — no uses remaining!"
    return ""


async def process_action(
    interaction: discord.Interaction,
    action: str,
    ability_key: str = None,
    ritual_choice: str = None,
    from_bag: bool = False
):
    battle = get_battle(interaction.channel.id, interaction.user)
    if not validate_turn(interaction.user, battle):
        return await safe_respond(interaction, "❌ It's not your turn or no battle active!")

    try:
        if not from_bag:
            await interaction.response.defer()
    except (discord.errors.InteractionResponded, discord.errors.NotFound, discord.errors.HTTPException):
        pass  # already deferred or responded

    try:
        await _process_action_inner(interaction, action, ability_key, ritual_choice)
    except Exception as e:
        import traceback
        traceback.print_exc()
        try:
            await interaction.followup.send(f"❌ An error occurred: `{type(e).__name__}: {e}`", ephemeral=True)
        except Exception:
            pass

async def _process_action_inner(
    interaction: discord.Interaction,
    action: str,
    ability_key: str = None,
    ritual_choice: str = None
):
    battle = get_battle(interaction.channel.id, interaction.user)
    # Capture the dict key so we can pop correctly
    battle_key = get_battle_key_for_user(interaction.channel.id, interaction.user)
    channel_id = interaction.channel.id

    attacker, defender = get_fighters(interaction.user, battle)
    is_challenger = attacker.user == battle["challenger"].user
    shield_key_def = "shield_opponent" if is_challenger else "shield_challenger"
    shield_key_att = "shield_challenger" if is_challenger else "shield_opponent"
    battle_msg = battle.get("battle_message")

    battle["turns"] = battle.get("turns", 0) + 1

    # ── Pre-check: if it's an ability on cooldown/no-uses, bail early before turn effects ──
    if action == "ability" and ability_key:
        ab_pre, _ = get_ability_info(ability_key)
        if ab_pre:
            _pre_cost = attacker.get_ability_cost(ab_pre.get("cost", 0), ability_key)
            # Check prohibition
            if getattr(attacker, "prohibited_ability", None) and ability_key == attacker.prohibited_ability:
                ainfo2, _ = get_ability_info(ability_key)
                aname2 = ainfo2["name"] if ainfo2 else ability_key
                await interaction.followup.send(f"🚫 **{aname2}** has been **Prohibited** for the rest of the match!", ephemeral=True)
                return
            # Check Spiritual Suppression
            if getattr(defender, "spiritual_suppression_turns", 0) > 0 and _pre_cost > 50:
                await interaction.followup.send(
                    f"🦷 **Spiritual Suppression** blocks abilities costing more than **50 SP**! "
                    f"*({defender.spiritual_suppression_turns} turn(s) remaining)*", ephemeral=True)
                return
            # Check insufficient SP
            if attacker.sp < _pre_cost:
                await interaction.followup.send(
                    f"❌ Not enough spirit! Costs **{_pre_cost} SP** — you have **{attacker.sp} SP**.", ephemeral=True)
                return
            # Check generic cooldown (from _check_cooldown)
            _pre_cd = _check_cooldown(attacker, ability_key)
            if _pre_cd:
                await interaction.followup.send(_pre_cd, ephemeral=True)
                return

    # ── Cleanse override: allow break_out / therapy / blink to fire even when stunned ──
    CLEANSE_ABILITIES = {"break_out", "therapy", "blink"}
    is_cleanse_action = (
        action == "ability" and (
            ability_key in CLEANSE_ABILITIES or
            (ability_key == "pacification" and ritual_choice == "cleanse_self")
        )
    )
    would_skip = (
        attacker.sneak_attack_pending or
        attacker.trapped > 0 or
        attacker.paralysis > 0 or
        attacker.slumber > 0 or
        attacker.freeze > 0 or
        attacker.charm_turns > 0
    )
    if is_cleanse_action and would_skip:
        # Apply only the non-skip turn effects (damage ticks etc.) without the skip
        msg = ""
        if attacker.bleed > 0:
            bleed_dmg = max(1, int(attacker.max_hp * 0.02))
            attacker.hp = max(0, attacker.hp - bleed_dmg)
            attacker.bleed -= 1
            msg += f"🩸 **{attacker.user.display_name}** bleeds **{bleed_dmg}** damage! ({attacker.bleed} left)\n"
        if attacker.gun_shot_bleed:
            gs_bleed = max(1, int(attacker.max_hp * 0.02))
            attacker.hp = max(0, attacker.hp - gs_bleed)
            msg += f"🔫🩸 Gunshot wound bleeds **{gs_bleed}** damage!\n"
        if attacker.burn > 0:
            b_dmg = max(1, int(attacker.max_hp * (0.04 if attacker.witch_burn else 0.03)))
            attacker.hp = max(0, attacker.hp - b_dmg)
            attacker.burn -= 1
            if attacker.burn == 0:
                attacker.witch_burn = False
            msg += f"🔥 **{attacker.user.display_name}** burns for **{b_dmg}** damage! ({attacker.burn} left)\n"
        if attacker.moral_freedom_active:
            mf_dmg = max(1, int(attacker.max_hp * 0.05))
            attacker.hp = max(0, attacker.hp - mf_dmg)
            msg += f"👼 **{attacker.user.display_name}** suffers moral freedom — **{mf_dmg}** damage!\n"
        status_name = (
            "paralysis" if attacker.paralysis > 0 else
            "sleep" if attacker.slumber > 0 else
            "freeze" if attacker.freeze > 0 else
            "charm" if attacker.charm_turns > 0 else
            "sneak attack" if attacker.sneak_attack_pending else
            "trap"
        )
        msg += f"💥 **{attacker.user.display_name}** fights through the {status_name} to use their cleanse!\n"
        skip_turn = False
        color = 0x9B59B6
    else:
        msg, skip_turn = apply_turn_effects(attacker, defender, battle)
        color = 0x9B59B6

    # Death from turn effects
    if not attacker.is_alive() or not defender.is_alive():
        winner = defender if not attacker.is_alive() else attacker
        loser = attacker if not attacker.is_alive() else defender
        bet_log = resolve_bets(channel_id, winner.user, loser.user, battle.get("guild_id", 0))
        battles.pop(battle_key, None)

        # Soli reward based on loser's sequence
        loser_seq = get_seq_number(loser.role) if loser.role else 9
        winner_seq = get_seq_number(winner.role) if winner.role else 9
        reward = SEQ_WIN_REWARD.get(loser_seq, 1000)
        _gid = battle.get("guild_id", 0)
        add_pounds(winner.user.id, reward, guild_id=_gid)

        # Bounty claim
        bounty_amount = claim_bounty(_gid, loser.user.id)
        if bounty_amount > 0:
            add_pounds(winner.user.id, bounty_amount, guild_id=_gid)
            _bchan = bot.get_channel(channel_id)
            if _bchan:
                async def _send_bounty_msg2(ch=_bchan, w=winner, l=loser, amt=bounty_amount):
                    await ch.send(
                        f"🎯 **Bounty Claimed!** **{w.user.display_name}** collected "
                        f"**{amt:,} soli** for defeating **{l.user.display_name}**!"
                    )
                bot.loop.create_task(_send_bounty_msg2())

        record_battle(winner.user, loser.user, winner_seq, loser_seq, _gid)
        guild = winner.user.guild if hasattr(winner.user, "guild") else None
        bot.loop.create_task(post_battle_log(guild, winner.user, loser.user, winner_seq, loser_seq, reward, _gid))
        bot.loop.create_task(award_combat_xp(winner.user, loser_seq, guild))
        bot.loop.create_task(penalize_combat_xp(loser.user, loser_seq, guild))

        xp_gain = SEQ_WIN_XP.get(loser_seq, 150)
        xp_penalty = SEQ_LOSS_XP.get(loser_seq, 60)

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
            title="𓂃 💀 Fallen",
            description=(
                msg
                + f"\n**{loser.user.display_name}** collapses.\n"
                + f"🏆 **{winner.user.display_name}** wins!\n"
                + f"💰 **+{reward:,} soli** awarded for defeating a Seq {loser_seq}!\n"
                + f"📈 **{winner.user.display_name}** gains **+{xp_gain} EXP**\n"
                + f"📉 **{loser.user.display_name}** loses **-{xp_penalty} EXP**"
                + bonus_point_line
                + bet_log
            ),
            color=0x2C3E50
        )
        await send_and_replace(battle, interaction.channel, embed)
        return

    # Paralysis / sleep / freeze skip
    if skip_turn:
        swap_turn(battle)
        embed = make_battle_embed(msg, battle, battle["turn"].display_name, 0x95A5A6)
        await send_and_replace(battle, interaction.channel, embed, view=_make_battle_view(battle))
        return

    # Check if this action was blocked by provocation
    if attacker.provoked_block == action:
        attacker.provoked_block = None
        msg += f"😤 **{attacker.user.display_name}**'s **{action}** was blocked by provocation! Must choose another action.\n"
        swap_turn(battle)
        embed = make_battle_embed(msg, battle, battle["turn"].display_name, 0xF39C12)
        await send_and_replace(battle, interaction.channel, embed, view=_make_battle_view(battle))
        return

    # Check if action was forced by instigation
    if attacker.instigated_action and action != attacker.instigated_action:
        forced = attacker.instigated_action
        attacker.instigated_action = None
        msg += f"😈 **{attacker.user.display_name}** was instigated into using **{forced}**!\n"
        action = forced
        ability_key = forced if forced not in ("attack", "special", "flee") else ability_key

    # Check sabotage (ritualistic reasoning)
    if attacker.sabotaged:
        attacker.sabotaged = False
        if random.random() < 0.5:
            # Backfire — hits themselves
            self_dmg = random.randint(8, 18)
            attacker.hp = max(0, attacker.hp - self_dmg)
            msg += f"🔬 **{attacker.user.display_name}**'s move backfires — **{self_dmg}** self-damage!\n"
        else:
            msg += f"🔬 **{attacker.user.display_name}**'s move fizzles — it fails completely!\n"
        swap_turn(battle)
        v = check_victory(attacker, defender, battle_key, msg)
        if v:
            await send_and_replace(battle, interaction.channel, v)
            return
        embed = make_battle_embed(msg, battle, battle["turn"].display_name, 0x95A5A6)
        await send_and_replace(battle, interaction.channel, embed, view=_make_battle_view(battle))
        return

    # Reading prediction check — if defender predicted this action
    if defender.reading_prediction is not None:
        predicted = defender.reading_prediction
        defender.reading_prediction = None
        if predicted == action:
            # Correct — move misses AND attacker is stunned
            applied = attacker.apply_status_effect("paralysis", 1)
            stun_txt = " and is **stunned for 1 turn**" if applied else ""
            msg += f"🔭 **{defender.user.display_name}** read the move perfectly — it **misses**{stun_txt}!\n"
            swap_turn(battle)
            embed = make_battle_embed(msg, battle, battle["turn"].display_name, 0x9B59B6)
            await send_and_replace(battle, interaction.channel, embed, view=_make_battle_view(battle))
            return
        else:
            # Wrong prediction — Telepathist suffers backlash stun for 1 round
            msg += f"🔭 **{defender.user.display_name}**'s prediction was wrong — backlash stun! **{defender.user.display_name}** loses their next turn.\n"
            defender.paralysis = max(defender.paralysis, 1)

    # Seq 6: Judge — backfire if attacker uses restricted move
    if attacker.judge_restricted_move and attacker.judge_restricted_move == (ability_key or action):
        restricted_move = attacker.judge_restricted_move
        attacker.judge_restricted_move = None
        backfire_dmg = 30
        attacker.hp = max(0, attacker.hp - backfire_dmg)
        info_r, _ = get_ability_info(restricted_move)
        rname = info_r["name"] if info_r else restricted_move
        msg += f"⚖️ **Judge's Ruling!** **{attacker.user.display_name}** used the restricted **{rname}** — **{backfire_dmg}** backfire damage!\n"

    # Arbiter ceasefire violation check
    ARBITER_VIOLATIONS = {"attack", "special"}
    if attacker.arbiter_active and attacker.arbiter_turns > 0 and action in ARBITER_VIOLATIONS:
        punishment = int(attacker.hp * 0.40)
        attacker.hp = max(1, attacker.hp - punishment)
        attacker.arbiter_active = False
        defender.arbiter_active = False
        msg += f"⚖️ **ARBITER VIOLATION!** **{attacker.user.display_name}** broke the ceasefire — a higher power strikes them for **{punishment}** damage (**40% of their HP**)!\n"
    elif defender.arbiter_active and defender.arbiter_turns > 0 and action in ARBITER_VIOLATIONS:
        punishment = int(defender.hp * 0.40)
        defender.hp = max(1, defender.hp - punishment)
        attacker.arbiter_active = False
        defender.arbiter_active = False
        msg += f"⚖️ **ARBITER VIOLATION!** **{defender.user.display_name}** broke the ceasefire by provoking — a higher power strikes them for **{punishment}** damage (**40% of their HP**)!\n"

    # Seq 6: Charm maintain-or-fade logic (checked each turn, charm already handled in apply_turn_effects)
    # Charm chance decays after a charmed turn passes
    if attacker.charm_turns == 0 and attacker.charm_chance > 0:
        if random.random() < attacker.charm_chance:
            attacker.charm_turns = 1
            attacker.charm_chance = max(0.0, attacker.charm_chance - 0.10)
        else:
            attacker.charm_chance = 0.0  # charm fully faded

    # Seq 6: Scribe — new system: pending copy fires 1 turn after activation, even through stuns
    # If scribe_pending is set on the defender, record whatever ability was just used against them
    if defender.scribe_pending and action == "ability" and ability_key:
        defender.scribe_copied_ability = ability_key
        defender.scribe_pending = False
        defender.scribe_record_in = 0
        info_sc, _ = get_ability_info(ability_key)
        scname = info_sc["name"] if info_sc else ability_key
        msg += f"📖 **Scribe** recorded **{scname}** — **{defender.user.display_name}** can use it next turn!\n"
    # Legacy scribe_active support (in case copied ability fires immediately)
    elif defender.scribe_active and action == "ability" and ability_key:
        defender.scribe_copied_ability = ability_key
        defender.scribe_active = False
        info_sc, _ = get_ability_info(ability_key)
        scname = info_sc["name"] if info_sc else ability_key
        msg += f"📖 **Scribe** copied **{scname}** — **{defender.user.display_name}** can use it next turn!\n"

    # ── Hurricane of Light auto-release ───────────────────
    # If the attacker planted the sword last turn and isn't stunned, it fires automatically
    if attacker.hurricane_charging and ability_key != "hurricane_of_light":
        attacker.hurricane_charging = False
        attacker.hurricane_uses += 1
        attacker.hurricane_cooldown = 12
        avg_base = (attacker.base_hp + defender.base_hp) // 2
        pct = random.uniform(0.35, 0.40)
        raw_dmg = int(avg_base * pct)
        if defender.distortion_active:
            defender.distortion_active = False
            raw_dmg = max(1, raw_dmg // 2)
            msg += f"👑 **{defender.user.display_name}** distorts — hurricane damage halved!\n"
        elif defender.phasing_active:
            defender.phasing_active = False
            defender.phasing_cooldown = 1
            raw_dmg = max(1, raw_dmg // 2)
            msg += f"👻 **{defender.user.display_name}** phases — hurricane damage halved!\n"
        actual_dmg = defender.apply_damage(int(raw_dmg * attacker.get_attack_mult()), is_ability=True)
        backlash = int(actual_dmg * 0.30)
        attacker.hp = max(0, attacker.hp - backlash)
        msg += f"🌪️ **HURRICANE OF LIGHT — AUTO-RELEASE!** The stored hurricane erupts! **{actual_dmg}** damage dealt — **{backlash}** backlash to you! *(use {attacker.hurricane_uses}/3, 12 turn cd)*\n"
        color = 0xF5D020
        # Check for victory after auto-release
        v = check_victory(attacker, defender, battle_key, msg)
        if v:
            await send_and_replace(battle, interaction.channel, v)
            return

    # ── Attack ────────────────────────────────────────────
    if action == "attack":
        # Trip trap — next attack misses
        if attacker._trip_trap_miss:
            attacker._trip_trap_miss = False
            msg += f"🪜 **{attacker.user.display_name}** trips! Attack misses!"; color = 0x95A5A6
            swap_turn(battle)
            await send_and_replace(battle, interaction.channel, make_battle_embed(msg, battle, battle["turn"].display_name), view=_make_battle_view(battle))
            return
        # Seq 6: Distortion — guaranteed dodge, 40% reflect (disguised as evade)
        if defender.distortion_active:
            defender.distortion_active = False
            base_dmg_est, _ = attacker.get_damage(0.055, 0.090)
            if random.random() < 0.40 and base_dmg_est < int(defender.max_hp * 0.60):
                reflect_dmg = int(base_dmg_est * 0.60)
                attacker.hp = max(0, attacker.hp - reflect_dmg)
                msg += f"💨 **{attacker.user.display_name}**'s attack misses — **{defender.user.display_name}** sidesteps cleanly! *(+{reflect_dmg} reflected)*"; color = 0x8E44AD
            else:
                msg += f"💨 **{attacker.user.display_name}** swings wide — **{defender.user.display_name}** steps back!"; color = 0x3498DB
        # Seq 7: paper figurine blocks all attacks
        elif defender.paper_figurine_turns > 0:
            defender.paper_figurine_turns -= 1
            msg += f"🪆 **{defender.user.display_name}**'s paper figurine blocks the attack! *({defender.paper_figurine_turns} turn{'s' if defender.paper_figurine_turns != 1 else ''} left)*"; color = 0x9B59B6
        # Seq 7: lucky_day — attacker's hit chance halved
        elif not attacker.hit_chance_vs(defender):
            msg += f"🍀 **{attacker.user.display_name}**'s attack is deflected by fortune!"; color = 0x2ECC71
        # Seq 7: werewolf dodge (on defender)
        elif defender.transformed and random.random() < defender.transform_dodge_reduction:
            msg += f"🐺 **{defender.user.display_name}** lunges aside — attack misses! *(dodge {int(defender.transform_dodge_reduction*100)}%)*"; color = 0x95A5A6
        else:
            if attacker.protection_active:
                attacker.protection_weakened_turn = True  # reduction drops to 10 for 1 turn after attacking
                if attacker.protection_mode == "active":
                    attacker.protection_active = False  # Active mode: ends after attacker attacks
            dmg, crit = attacker.get_damage(0.055, 0.090)
            if defender.divination_dodge:
                dodge_chance = defender._seer_dodge_chance or 70
                if random.uniform(0, 100) < dodge_chance:
                    defender.divination_dodge_turns = max(0, defender.divination_dodge_turns - 1)
                    if defender.divination_dodge_turns <= 0:
                        defender.divination_dodge = False
                        defender._seer_dodge_chance = None
                    msg += f"🔮 **{defender.user.display_name}** vanishes! *(dodge {int(dodge_chance)}%)*"
                    swap_turn(battle)
                    await send_and_replace(battle, interaction.channel, make_battle_embed(msg, battle, battle["turn"].display_name), view=_make_battle_view(battle))
                    return
                else:
                    defender.divination_dodge_turns = max(0, defender.divination_dodge_turns - 1)
                    if defender.divination_dodge_turns <= 0:
                        defender.divination_dodge = False
                        defender._seer_dodge_chance = None
            # Danger Intuition: 35% full dodge, 65% graze (50% dmg) — single linked roll
            if defender.danger_intuition_active:
                roll = random.random()
                if roll < 0.35:
                    msg += f"👁️ **{defender.user.display_name}** senses danger — full dodge!"
                    swap_turn(battle)
                    await send_and_replace(battle, interaction.channel, make_battle_embed(msg, battle, battle["turn"].display_name), view=_make_battle_view(battle))
                    return
                else:
                    dmg = max(1, dmg // 2)
                    msg += f"👁️ **{defender.user.display_name}** senses it — grazed for **{dmg}** (50%)!\n"
            if defender.phasing_active:
                defender.phasing_active = False
                defender.phasing_cooldown = 1
                dmg = max(1, int(dmg * random.uniform(0.40, 0.50)))  # reduce to 40-50%, not full block
                msg += f"👻 **{defender.user.display_name}** phases — damage reduced to **{dmg}**!\n"
            if random.uniform(0, 100) < defender.evasion_chance():
                msg += f"💨 **{defender.user.display_name}** sidesteps!"; color = 0x3498DB
            else:
                if battle.get(shield_key_def):
                    dmg = dmg // 2
                    battle[shield_key_def] = False
                    msg += "🛡️ Shield shatters!\n"
                actual_dmg = defender.apply_damage(dmg)
                # Storm Divinity — +20 lightning on every attack
                # Radiant Armour recoil — attacker takes recoil on hitting defender
                if getattr(defender, "_radiant_armour_turns", 0) > 0 and getattr(defender, "_radiant_armour_recoil", 0) > 0:
                    recoil = defender._radiant_armour_recoil
                    attacker.hp = max(0, attacker.hp - recoil)
                    msg += f"✨ **Radiant Armour** — {recoil} recoil! "
                # Thorn recoil (Creation Divinity)
                if getattr(defender, "_thorn_recoil", 0) > 0 and getattr(defender, "_thorn_recoil_dmg", 0) > 0:
                    attacker.hp = max(0, attacker.hp - defender._thorn_recoil_dmg)
                    msg += f"🌿 **Thorn recoil** — {defender._thorn_recoil_dmg}! "
                # Parry/Riposte — defender parried, deal 35 back to attacker
                if getattr(defender, "_riposte_pending", False):
                    defender._riposte_pending = False
                    riposte_dmg = seq_val(defender, 35)
                    attacker.hp = max(0, attacker.hp - riposte_dmg)
                    msg += f"🛡️ **{defender.user.display_name}** PARRIES and ripostes for **{riposte_dmg}** damage!\n"
                    actual_dmg = 0
                if attacker.last_stand_bonus > 0:
                    extra = attacker.last_stand_bonus
                    attacker.last_stand_bonus = 0
                    defender.hp = max(0, defender.hp - extra)
                    actual_dmg += extra
                    msg_bonus = f" *(+{extra} Last Stand!)*"
                else:
                    msg_bonus = ""
                # Seq 5: Cull bonus
                if attacker.cull_bonus > 0:
                    extra = attacker.cull_bonus
                    attacker.cull_bonus = 0
                    defender.hp = max(0, defender.hp - extra)
                    actual_dmg += extra
                    msg_bonus += f" *(+{extra} Cull!)*"
                attacker.last_attack_dmg = actual_dmg
                attacker.sp = min(attacker.max_sp, attacker.sp + 6)
                if crit:
                    # Seq 5: Spirit Thread — broken by critical hit
                    if defender.spirit_thread_active:
                        defender.spirit_thread_active = False
                        defender.spirit_thread_cooldown = 5
                        msg += f"\n🧵 **Spirit Thread** disrupted by critical hit!"
                    msg += f"💥 **CRITICAL!** **{attacker.user.display_name}** strikes for **{actual_dmg}** damage{msg_bonus}! *(+6 SP)*"; color = 0xF39C12
                else:
                    msg += f"⚔️ **{attacker.user.display_name}** strikes for **{actual_dmg}** damage{msg_bonus}. *(+6 SP)*"
                # Seq 7: vile crime tracking
                if attacker.vile_crime_count > 0 and not attacker.vile_crime_used:
                    attacker.vile_crime_count += 1
                    if attacker.vile_crime_count > 3:
                        attacker.vile_crime_used = True
                        attacker.vile_crime_count = 0
                        defender.apply_status_effect("bleed", 999)  # permanent
                        defender.apply_status_effect("burn", 999)   # permanent
                        msg += f"\n🔪 **Vile Crime complete!** **{defender.user.display_name}** afflicted with permanent bleed and burn!"
                    else:
                        msg += f"\n🔪 Vile Crime: *({attacker.vile_crime_count - 1}/3 hits)*"

    # ── Special ───────────────────────────────────────────
    elif action == "special":
        # Seq 6: Distortion intercept
        if defender.distortion_active:
            defender.distortion_active = False
            base_dmg_est, _ = attacker.get_damage(0.090, 0.140)
            if random.random() < 0.40 and base_dmg_est < int(defender.max_hp * 0.60):
                reflect_dmg = int(base_dmg_est * 0.60)
                attacker.hp = max(0, attacker.hp - reflect_dmg)
                msg += f"💨 **{attacker.user.display_name}**'s special misses — **{defender.user.display_name}** deflects it cleanly! *(+{reflect_dmg} reflected)*"; color = 0x8E44AD
            else:
                msg += f"💨 **{attacker.user.display_name}**'s special whiffs — **{defender.user.display_name}** moves aside!"; color = 0x3498DB
        # Seq 7: paper figurine blocks
        elif defender.paper_figurine_turns > 0:
            defender.paper_figurine_turns -= 1
            msg += f"🪆 **{defender.user.display_name}**'s paper figurine blocks the special! *({defender.paper_figurine_turns} turn{'s' if defender.paper_figurine_turns != 1 else ''} left)*"; color = 0x9B59B6
        # Seq 7: lucky_day
        elif not attacker.hit_chance_vs(defender):
            msg += f"🍀 Fortune deflects the special attack!"; color = 0x2ECC71
        # Seq 7: werewolf dodge
        elif defender.transformed and random.random() < defender.transform_dodge_reduction:
            msg += f"🐺 **{defender.user.display_name}** leaps away — special misses! *(dodge {int(defender.transform_dodge_reduction*100)}%)*"; color = 0x95A5A6
        else:
            base_miss = max(5, 25 - attacker.stats["luck"] * 3)
            miss_chance = attacker.get_miss_chance(base_miss)
            if random.randint(1, 100) <= miss_chance:
                attacker.sp = min(attacker.max_sp, attacker.sp + 4)
                msg += f"💨 **{attacker.user.display_name}** misses! *(+4 SP)*"; color = 0x95A5A6
            else:
                if defender.divination_dodge:
                    dodge_chance = defender._seer_dodge_chance or 70
                    if random.uniform(0, 100) < dodge_chance:
                        defender.divination_dodge_turns = max(0, defender.divination_dodge_turns - 1)
                        if defender.divination_dodge_turns <= 0:
                            defender.divination_dodge = False
                            defender._seer_dodge_chance = None
                        msg += f"🔮 **{defender.user.display_name}** slips the special! *(dodge {int(dodge_chance)}%)*"
                        swap_turn(battle)
                        await send_and_replace(battle, interaction.channel, make_battle_embed(msg, battle, battle["turn"].display_name), view=_make_battle_view(battle))
                        return
                    else:
                        defender.divination_dodge_turns = max(0, defender.divination_dodge_turns - 1)
                        if defender.divination_dodge_turns <= 0:
                            defender.divination_dodge = False
                            defender._seer_dodge_chance = None
                # Danger Intuition on special — single linked roll
                if defender.danger_intuition_active:
                    roll = random.random()
                    if roll < 0.35:
                        msg += f"👁️ **{defender.user.display_name}** senses the special — full dodge!"
                        swap_turn(battle)
                        await send_and_replace(battle, interaction.channel, make_battle_embed(msg, battle, battle["turn"].display_name), view=_make_battle_view(battle))
                        return
                    else:
                        msg += f"👁️ **{defender.user.display_name}** senses it — grazed for 50%!\n"
                        # dmg halved below after phasing/evasion checks
                        _danger_intuition_graze = True
                else:
                    _danger_intuition_graze = False
                if defender.phasing_active:
                    defender.phasing_active = False
                    defender.phasing_cooldown = 1
                    _phasing_reduction = random.uniform(0.40, 0.50)
                    msg += f"👻 **{defender.user.display_name}** phases — damage halved!\n"
                else:
                    _phasing_reduction = None
                if random.uniform(0, 100) < defender.evasion_chance():
                    msg += f"💨 **{defender.user.display_name}** evades!"; color = 0x3498DB
                else:
                    dmg, crit = attacker.get_damage(0.090, 0.140)
                    # Special Boost (White Tower Seq 8) — next special deals 50% more
                    if getattr(attacker, "_special_boost", False):
                        attacker._special_boost = False
                        dmg = int(dmg * 1.50)
                    if _phasing_reduction is not None:
                        dmg = max(1, int(dmg * _phasing_reduction))
                    if _danger_intuition_graze:
                        dmg = max(1, dmg // 2)
                    if battle.get(shield_key_def):
                        dmg = dmg // 2
                        battle[shield_key_def] = False
                        msg += "🛡️ Shield shatters!\n"
                    actual_dmg = defender.apply_damage(dmg)
                    # Seq 5: Last Stand bonus
                    if attacker.last_stand_bonus > 0:
                        extra = attacker.last_stand_bonus
                        attacker.last_stand_bonus = 0
                        defender.hp = max(0, defender.hp - extra)
                        actual_dmg += extra
                        msg_bonus = f" *(+{extra} Last Stand!)*"
                    else:
                        msg_bonus = ""
                    # Seq 5: Cull bonus
                    if attacker.cull_bonus > 0:
                        extra = attacker.cull_bonus
                        attacker.cull_bonus = 0
                        defender.hp = max(0, defender.hp - extra)
                        actual_dmg += extra
                        msg_bonus += f" *(+{extra} Cull!)*"
                    attacker.sp = min(attacker.max_sp, attacker.sp + 4)
                    # Seq 5: Spirit Thread broken by special attack
                    if defender.spirit_thread_active:
                        defender.spirit_thread_active = False
                        defender.spirit_thread_cooldown = 5
                        msg += f"\n🧵 **Spirit Thread** disrupted by special attack!"
                    if crit:
                        msg += f"💥 **CRITICAL SPECIAL!** **{actual_dmg}** damage{msg_bonus}! *(+4 SP)*"; color = 0xE74C3C
                    else:
                        msg += f"💥 **{attacker.user.display_name}** strikes for **{actual_dmg}** damage{msg_bonus}! *(+4 SP)*"; color = 0xE67E22

    # ── Ability ───────────────────────────────────────────
    elif action == "ability":
        ab, is_role = get_ability_info(ability_key)
        if not ab:
            await interaction.followup.send("❌ Unknown ability.", ephemeral=True)
            return
        # Allow scribe-copied shop abilities (e.g. leodero) even if not owned
        _is_scribe_copy = attacker.scribe_copy_pending
        attacker.scribe_copy_pending = False
        if not is_role and not owns_spell(attacker.user.id, ability_key, guild_id=battle["guild_id"]) and not _is_scribe_copy:
            await interaction.followup.send("❌ You don't own that spell!", ephemeral=True)
            return
        if is_role and attacker.role_ability_disabled > 0:
            await interaction.followup.send(f"❌ Your role ability is suppressed for **{attacker.role_ability_disabled}** more turn(s)!", ephemeral=True)
            return
        # Prohibition: only block the one specifically sealed ability
        if getattr(attacker, "prohibited_ability", None) and ability_key == attacker.prohibited_ability:
            ainfo, _ = get_ability_info(ability_key)
            aname = ainfo["name"] if ainfo else ability_key
            await interaction.followup.send(f"🚫 **{aname}** has been **Prohibited** for the rest of the match!", ephemeral=True)
            return

        cd_msg = _check_cooldown(attacker, ability_key)
        if cd_msg:
            await interaction.followup.send(cd_msg, ephemeral=True)
            return

        actual_cost = attacker.get_ability_cost(ab["cost"], ability_key)
        if attacker.sp < actual_cost:
            await interaction.followup.send(
                f"❌ Not enough spirit! Costs **{actual_cost} SP** — you have **{attacker.sp} SP**.", ephemeral=True)
            return

        # Seq 5: Spiritual Suppression — block abilities costing >50 SP
        if defender.spiritual_suppression_turns > 0 and actual_cost > 50:
            await interaction.followup.send(
                f"🦷 **Spiritual Suppression** blocks abilities costing more than **50 SP**! "
                f"*({defender.spiritual_suppression_turns} turn(s) remaining)*", ephemeral=True)
            return

        # Free Ability (White Tower Seq 6) — next ability costs 0 SP
        if getattr(attacker, "_free_ability", False) and actual_cost > 0:
            attacker._free_ability = False
            actual_cost = 0
        attacker.sp -= actual_cost
        cd_blocked = False  # set True in cooldown/already-used branches so turn is NOT consumed
        # Soul Parasite — drain 50% of SP cost from opponent when they use ability
        if action == "ability" and getattr(defender, "_soul_parasite_active", False):
            parasite_drain = actual_cost // 2
            if parasite_drain > 0:
                attacker.sp = min(attacker.max_sp, attacker.sp + parasite_drain)
                msg += f"🦠 **Soul Parasite** drained **{parasite_drain} SP**! "
        # Mind Dominated — actions heal attacker instead
        if getattr(attacker, "_mind_dominated_turns", 0) > 0:
            pass  # handled in process_action result — attacker's damage heals defender

        # Chaos Dominion — 50% chance ability backfires
        if getattr(attacker, "_chaos_dominion_abilities", 0) > 0:
            attacker._chaos_dominion_abilities -= 1
            if random.random() < 0.50:
                backfire = int(attacker.max_hp * 0.10)
                attacker.hp = max(0, attacker.hp - backfire)
                await interaction.followup.send(
                    f"🌀 **Chaos Dominion Backfire!** **{attacker.user.display_name}**'s ability backfired for **{backfire}** self-damage!", ephemeral=False)
                if attacker.hp <= 0:
                    return
        # Bizarro reflect — if flagged, redirect to self
        if getattr(attacker, "_bizarro_reflect_next", False):
            attacker._bizarro_reflect_next = False
            # ability hits self instead — swap attacker/defender for this ability
        # Holy Domain seal — only Sun pathway abilities allowed
        if getattr(defender, "_holy_domain_sealed", 0) > 0:
            atk_pathway = attacker.role.split("] ")[0].replace("[","") if attacker.role else ""
            if atk_pathway != "Sun":
                defender._holy_domain_sealed -= 1
                # The ability still fires but we note the seal is active
        # Mystery Authority — next ability is a miracle
        # Spatial lock — double SP cost
        if getattr(attacker, "_spatial_lock_abilities", 0) > 0:
            attacker._spatial_lock_abilities -= 1
            actual_cost = actual_cost * 2
        # Glance into Fate — 30% chance ability backfires onto the caster
        if getattr(attacker, "fate_cursed_backfire", False) and ability_key not in ("grazing", "spectate", "song", "study", "computing", "night_boost"):
            attacker.fate_cursed_backfire = False
            if random.random() < 0.30:
                backfire_dmg = int(attacker.max_hp * 0.10)
                attacker.hp = max(0, attacker.hp - backfire_dmg)
                await interaction.followup.send(
                    f"🎲 **Fate Backfire!** The cursed fate threads unravel — **{attacker.user.display_name}**'s ability backfires for **{backfire_dmg}** self-damage!", ephemeral=False)
                if attacker.hp <= 0:
                    return  # will be caught by hp check below

        # Track for Prometheus — record every ability the attacker uses so opponent can steal them
        if not hasattr(attacker, "abilities_used_this_battle"):
            attacker.abilities_used_this_battle = set()
        if ability_key not in ("prometheus", "scribe"):
            attacker.abilities_used_this_battle.add(ability_key)
        OFFENSIVE_ABILITIES = {
            "leodero","drain","debuff","trap","vital_strike","crime","desecration",
            "prying","card_tricks","moral_freedom",
            "poem","brilliant_light","gun_shot","water_bullet","sneak_attack","weapon_throw",
            "witch_curse","fire_ravens","blood_sucker","vine_bomb","mental_piercing",
            "faceless","hypnotist","conspiracy","scrolls_professor","calamity_priest",
            "zombie","distortion","soul_assurer","pacification",
            "provocation","instigation","glance_into_fate","testimony",
            "summoning_dead","astrology","observation","mental_theft","dream_visitation",
            "spiritual_takeover","dragged_to_hell","evil_sealing","thread_storm","cull",
            "star_of_curses","curse_of_misfortune","purification_halo","light_of_holiness",
            "arrow_of_lightning","disease_propagation","possession","wraith_shriek",
            "desire_explosion","fire_storm","star_pillar","vile_crime","bribe",
            "artisan","biologist","potions_professor","devil_form",
        }
        if ability_key in OFFENSIVE_ABILITIES and defender.paper_figurine_turns > 0:
            defender.paper_figurine_turns -= 1
            attacker.sp += actual_cost  # refund
            msg += f"🪆 **{defender.user.display_name}**'s paper figurine blocks the ability! *({defender.paper_figurine_turns} turn{'s' if defender.paper_figurine_turns != 1 else ''} left)*"; color = 0x9B59B6
            swap_turn(battle)
            embed = make_battle_embed(msg, battle, battle["turn"].display_name, color)
            await send_and_replace(battle, interaction.channel, embed, view=_make_battle_view(battle))
            return

        # Seq 6: Distortion also intercepts offensive abilities
        if ability_key in OFFENSIVE_ABILITIES and defender.distortion_active:
            defender.distortion_active = False
            attacker.sp += actual_cost  # refund
            msg += f"💨 **{attacker.user.display_name}**'s ability fizzles — **{defender.user.display_name}** distorts it away!"; color = 0x8E44AD
            swap_turn(battle)
            embed = make_battle_embed(msg, battle, battle["turn"].display_name, color)
            await send_and_replace(battle, interaction.channel, embed, view=_make_battle_view(battle))
            return

        if ability_key == "leodero":
            if random.uniform(0, 100) < defender.evasion_chance():
                msg += f"💨 **{defender.user.display_name}** leaps away!"; color = 0x3498DB
            else:
                dmg = int(random.randint(seq_val(attacker, 30), seq_val(attacker, 40)) * attacker.get_attack_mult())
                if battle.get(shield_key_def):
                    dmg = dmg // 2; battle[shield_key_def] = False; msg += "🛡️ Shield shatters!\n"
                actual_dmg = defender.apply_damage(dmg, is_ability=True)
                recoil = max(1, int(actual_dmg * 0.10))
                attacker.hp = max(0, attacker.hp - recoil)
                msg += (f"⚡ **LEODERO!** **{defender.user.display_name}** struck for **{actual_dmg}** damage!\n"
                        f"🔥 **{attacker.user.display_name}** suffers **{recoil}** recoil!"); color = 0xF1C40F

        elif ability_key == "heal":
            lo, hi = seq_dmg(attacker, ab["pct_min"]), seq_dmg(attacker, ab["pct_max"])
            heal = int(random.randint(lo, hi) * (1 + attacker.stats["luck"] * 0.02))
            attacker.hp = min(attacker.max_hp, attacker.hp + heal)
            msg += f"💚 **{attacker.user.display_name}** recovers **{heal} HP**!"; color = 0x2ECC71

        elif ability_key == "drain":
            drain = random.randint(seq_dmg(attacker, ab["pct_min"]), seq_dmg(attacker, ab["pct_max"]))
            actual_dmg = defender.apply_damage(drain, is_ability=True)
            attacker.sp = min(attacker.max_sp, attacker.sp + actual_dmg)
            msg += f"🌑 Drained **{actual_dmg}** HP as spirit!"; color = 0x8E44AD

        elif ability_key == "shield":
            battle[shield_key_att] = True
            msg += f"🛡️ **{attacker.user.display_name}** raises a spirit shield!"; color = 0x3498DB

        elif ability_key == "debuff":
            if attacker.debuff_used:
                attacker.sp += actual_cost
                msg += f"🔻 **Debuff** already used this battle!"; color = 0x95A5A6
                cd_blocked = True
            else:
                attacker.debuff_used = True
                defender.debuff_turns = 5
                defender.debuff_stacks = 1  # keep for compatibility with damage mult
                msg += f"🔻 **Debuff!** **{defender.user.display_name}**'s damage reduced by **20%** for **5 turns**!"; color = 0x8E44AD

        elif ability_key == "ritual":
            if ritual_choice == "strength":
                boost = random.randint(3, 6); attacker.strength_boost += boost
                msg += f"🕯️ **Ritual of Strength!** Next attack **+{boost}** damage!"; color = 0xE74C3C
            elif ritual_choice == "divination":
                attacker.divination_dodge = True; attacker._seer_dodge_chance = None; attacker.divination_dodge_turns = 1
                dodge_display = attacker.divination_chance(defender.stats["speed"])
                msg += f"🕯️ **Ritual of Divination!** **{int(dodge_display)}%** dodge chance!"; color = 0x9B59B6
            elif ritual_choice == "blessing":
                heal = random.randint(seq_dmg(attacker, 0.040), seq_dmg(attacker, 0.065)); attacker.hp = min(attacker.max_hp, attacker.hp + heal); attacker.blessed = True
                msg += f"🕯️ **Ritual of Blessing!** Recovered **{heal} HP**, strength rises next turn!"; color = 0x2ECC71

        # ── Seq 9 Role abilities ──────────────────────────

        elif ability_key == "prying":
            reduction = round(random.uniform(0.15, 0.25), 2)
            defender.defence_reduction = max(defender.defence_reduction, reduction)
            msg += f"🔍 **{defender.user.display_name}** takes **{int(reduction*100)}%** more damage!"; color = 0xF39C12

        elif ability_key == "danger_intuition":
            # Toggle on/off — no cooldown
            if attacker.danger_intuition_active:
                attacker.danger_intuition_active = False
                attacker.sp += actual_cost  # refund — toggle off costs nothing
                msg += f"👁️ **Danger Intuition** deactivated."; color = 0x95A5A6
            else:
                attacker.danger_intuition_active = True
                msg += f"👁️ **Danger Intuition** activated! **35% dodge** / **65% take 50% damage** — **{attacker.intuition_upkeep_sp} SP/turn** upkeep. Toggle off to stop."; color = 0x9B59B6

        elif ability_key == "pickpocket":
            stolen_sp = min(random.randint(seq_val(attacker, 15), seq_val(attacker, 20)), defender.sp)
            defender.sp = max(0, defender.sp - stolen_sp); attacker.sp = min(attacker.max_sp, attacker.sp + stolen_sp)
            msg += f"🖐️ Pickpocketed **{stolen_sp} SP** from **{defender.user.display_name}**!"
            if random.randint(1, 10) == 1:
                stolen_hp = min(seq_val(attacker, 5), defender.hp); defender.hp -= stolen_hp; attacker.hp = min(attacker.max_hp, attacker.hp + stolen_hp)
                msg += f" Also swiped **{stolen_hp} HP**!"
            attacker.pickpocket_cooldown = 2; color = 0xF39C12

        elif ability_key == "phasing":
            attacker.phasing_active = True; attacker.phasing_cooldown = 4
            msg += f"👻 **{attacker.user.display_name}** phases — next attack completely dodged! *(4 turn cd)*"; color = 0x9B59B6

        elif ability_key == "trap":
            trap_sp_cost = 35
            if attacker.trap_cooldown > 0 or getattr(attacker, "trap_slots", []):
                cd_turns = attacker.trap_cooldown
                pending = len(getattr(attacker, "trap_slots", []))
                if pending:
                    msg += f"🪤 **Trap** — traps still active ({pending} remaining)!"; color = 0x95A5A6
                else:
                    msg += f"🪤 **Trap** on cooldown for **{cd_turns}** more turn(s)!"; color = 0x95A5A6
                cd_blocked = True
            elif attacker.sp < trap_sp_cost:
                battle["turns"] = max(0, battle.get("turns", 0) - 1)
                await interaction.followup.send(
                    f"❌ **Trap** needs **{trap_sp_cost} SP** — you only have **{attacker.sp} SP**!",
                    ephemeral=True
                )
                await send_and_replace(battle, interaction.channel, make_battle_embed(msg, battle, attacker.user.display_name), view=_make_battle_view(battle))
                return
            else:
                attacker.sp -= trap_sp_cost  # actual_cost was 0, so no double-charge
                trap_options = ["flames", "trip", "dog_chase", "tripwire"]
                trap1 = random.choice(trap_options)
                trap2 = random.choice(trap_options)
                attacker.trap_slots = [trap1, trap2]  # replaces old traps (no stacking)
                attacker.trap_set_turn = battle.get("turn_count", 0)
                attacker.trap_cooldown = 0  # cooldown starts after last trap fires
                battle["trap_set_this_turn"] = True  # suppress "channels SP" message
                msg += f"🪤 **Trap!** Two traps secretly laid — activating after **1 turn**, randomly within **5 rounds**!\n*(Contents hidden from opponent)* *(8 turn cd — starts after trap ends)*"; color = 0x8E44AD

        elif ability_key == "vital_strike":
            flat_dmg = seq_val(attacker, 55)
            actual_dmg = defender.apply_damage(int(flat_dmg * attacker.get_attack_mult()), is_ability=True, skill_key="vital_strike")
            attacker.vital_strike_cooldown = 5
            attacker._vital_strike_weaken_turns = 5  # -40% ability damage for 5 rounds
            attacker._vital_strike_weaken_pct = 0.40
            msg += f"🗡️ **VITAL STRIKE!** Every ounce of strength slams into one point — **{actual_dmg}** damage! *(5t cd — ability damage −40% for 5 turns)*"; color = 0xE74C3C

        elif ability_key == "crime":
            roll = random.randint(1, 3)
            if roll == 1:
                applied = defender.apply_status_effect("bleed", 3)
                msg += f"🦹 **Crime — Bleed!**" + ("" if not applied else f" **{defender.user.display_name}** bleeds 3 turns!"); color = 0xE74C3C
            elif roll == 2:
                applied = defender.apply_status_effect("paralysis", 1)
                if applied:
                    attacker.crime_cooldown = 2
                msg += f"🦹 **Crime — Paralysis!**" + (" Resisted!" if not applied else f" **{defender.user.display_name}** paralysed 1 turn! *(2 turn cd)*"); color = 0x95A5A6
            else:
                actual_dmg = apply_ability_damage(attacker, defender, 0.041)
                msg += f"🦹 **Crime — Backfire! {actual_dmg}** damage!"; color = 0xE67E22

        elif ability_key == "break_out":
            cleared = []
            if attacker.bleed > 0:              attacker.bleed = 0;                       cleared.append("bleed")
            if attacker.gun_shot_bleed:         attacker.gun_shot_bleed = False;          cleared.append("gunshot bleed")
            if attacker.paralysis > 0:          attacker.paralysis = 0;                   cleared.append("paralysis")
            if attacker.slumber > 0:            attacker.slumber = 0;                     cleared.append("sleep")
            if attacker.freeze > 0:             attacker.freeze = 0;                      cleared.append("freeze")
            if attacker.freeze_weakened:        attacker.freeze_weakened = False;         cleared.append("freeze weakened")
            if attacker.burn > 0:               attacker.burn = 0;                        cleared.append("burn")
            if attacker.defence_reduction > 0:  attacker.defence_reduction = 0;          cleared.append("defence reduction")
            if attacker.sneak_attack_pending:   attacker.sneak_attack_pending = False;    cleared.append("sneak attack")
            if attacker.trapped > 0:            attacker.trapped = 0;                     cleared.append("trapped")
            if attacker.charm_turns > 0:        attacker.charm_turns = 0; attacker.charm_chance = 0.0; cleared.append("charm")
            if attacker.scroll_status:          attacker.scroll_status = None;            cleared.append("scroll affliction")
            if attacker.moral_freedom_active:   attacker.moral_freedom_active = False;    cleared.append("moral freedom")
            if attacker.debuff_stacks > 0:      attacker.debuff_stacks = 0;              cleared.append("debuff stacks")
            if attacker.notary_debuff_turns > 0: attacker.notary_debuff_turns = 0;       cleared.append("notary debuff")
            if cleared:
                msg += f"⛓️ **Break Out!** Cleared: **{', '.join(cleared)}**!"; color = 0x2ECC71
            else:
                attacker.sp += actual_cost; msg += f"⛓️ No status effects to clear!"; color = 0x95A5A6

        elif ability_key == "invigorate":
            attacker.invigorated = True
            msg += f"💢 Next attack deals **30% more** damage!"; color = 0xE74C3C

        elif ability_key == "night_boost":
            uses_left = 2 - getattr(attacker, "night_boost_uses_count", 0)
            if uses_left <= 0:
                attacker.sp += actual_cost; msg += f"🌙 **Night Boost** — both uses spent!"; cd_blocked = True
            else:
                nb = seq_val(attacker, 10)
                attacker.night_boost_active = True; attacker.night_boost_used = True; attacker.night_boost_damage = nb
                attacker.night_boost_turns = 7
                attacker.night_boost_uses_count = getattr(attacker, "night_boost_uses_count", 0) + 1
                remaining = uses_left - 1
                msg += f"🌙 **Night Boost!** **+{nb} damage** per attack for **7 turns**! ({remaining} use{'s' if remaining!=1 else ''} remaining)"; color = 0x2C3E50

        elif ability_key == "desecration":
            actual_dmg = apply_ability_damage(attacker, defender, 0.068)
            applied = defender.apply_status_effect("paralysis", 1)
            para_txt = " + paralysed 1 turn" if applied else ""
            attacker.desecration_cooldown = 4
            msg += f"💀 **Desecration!** **{actual_dmg}** damage{para_txt}! *(4 turn cd)*"; color = 0x2C3E50

        elif ability_key == "balancing_act":
            # Sailor Seq 9 — phantom balance on spirit scales. Raises evasion + resists next status.
            attacker.balancing_act_evasion_turns = 3
            attacker.balancing_act_status_immune = True   # blocks next 1 status effect
            attacker.balancing_cooldown = 4
            msg += (f"⚓ **Balancing Act!** **{attacker.user.display_name}** steadies on phantom scales — "
                    f"**+20% evasion** for **3 turns** and blocks the next status effect! *(4 turn cd)*"); color = 0x3498DB

        elif ability_key == "song":
            # Bard Seq 9 — spiritual melody restores the singer's spirit and unsettles the listener
            regen = int(attacker.max_sp * 0.20)
            attacker.sp = min(attacker.max_sp, attacker.sp + regen)
            charm_txt = ""
            if random.random() < 0.25 and not defender.charm_turns > 0:
                defender.charm_turns = 1
                defender.charm_chance = 0.20
                charm_txt = f" and charms **{defender.user.display_name}** for **1 turn** (20% linger)!"
            attacker.song_cooldown = 3
            msg += f"🎵 **Song!** Spiritual melody restores **{regen} SP**{charm_txt or '!'}  *(3 turn cd)*"; color = 0x2ECC71

        elif ability_key == "prayer":
            if attacker.prayer_active:
                attacker.sp += actual_cost; msg += f"🙏 Prayer already active!"
                cd_blocked = True
            else:
                attacker.prayer_active = True
                msg += f"🙏 All damage **+50%** — but bleeds **5-8 HP/turn** forever!"; color = 0xE74C3C

        elif ability_key == "study":
            attacker.study_skip = True; attacker.sp += actual_cost
            msg += f"📚 Studying — SP fully restored next turn at cost of that turn!"; color = 0x3498DB

        elif ability_key == "spectate":
            # Spectator Seq 9 — spirit vision; see through defenses and read opponent's state
            attacker.spectate_active = True  # improves flee chance, handled in flee logic
            # Reveal opponent's current SP and active buffs as intel
            defender_buffs = defender.get_status_line() if hasattr(defender, "get_status_line") else "unknown"
            attacker.spectate_cooldown = 3
            msg += (f"👁️ **Spectate!** Spirit Vision activated — **{defender.user.display_name}** has "
                    f"**{defender.sp}/{defender.max_sp} SP** | Status: **{defender_buffs or 'none'}**\n"
                    f"Flee chance improved to **60%** for **3 turns**. *(3 turn cd)*"); color = 0x9B59B6

        elif ability_key == "planting":
            if attacker.planting_cooldown > 0:
                attacker.sp += actual_cost
                msg += f"🌱 **Planting** on cooldown for **{attacker.planting_cooldown}** more turn(s)!"; color = 0x95A5A6
                cd_blocked = True
            elif attacker.planting_turns > 0:
                attacker.sp += actual_cost; msg += f"🌱 Already growing!"
            else:
                attacker.planting_turns = 3
                attacker.planting_cooldown = 2
                msg += f"🌱 **Planting!** Recovers seq-scaled HP/turn for **3 turns**! *(2 turn cd)*"; color = 0x2ECC71

        elif ability_key == "curing":
            sp_cost = int(attacker.max_sp * 0.24)
            if attacker.sp + actual_cost < sp_cost:
                attacker.sp += actual_cost
                await interaction.followup.send(f"❌ Needs **{sp_cost} SP** total.", ephemeral=True); return
            heal = seq_dmg(attacker, 0.320); attacker.sp = max(0, attacker.sp - sp_cost + actual_cost)
            attacker.hp = min(attacker.max_hp, attacker.hp + heal)
            msg += f"🧪 Recovered **{heal} HP** at cost of **{sp_cost} SP**!"; color = 0x2ECC71

        elif ability_key == "glance_into_fate":
            # Monster (Wheel of Fortune Seq 9) — glance at fate's threads; twist opponent's luck
            # Curses the opponent: their next 3 random rolls are unfavourable
            # Mechanically: 30% miss chance on their next 3 attacks + 30% chance their next ability backfires
            attacker.glance_fate_cooldown = 4
            defender.fate_cursed_turns = 3        # -30% accuracy for 3 turns
            defender.fate_cursed_backfire = True  # next ability has 30% chance to self-hit
            msg += (f"🎲 **Glance into Fate!** **{attacker.user.display_name}** peers at **{defender.user.display_name}**'s fate — "
                    f"**30% miss chance** on their next **3 attacks** and their next ability risks **30% backfire**! *(4 turn cd)*"); color = 0x8E44AD

        elif ability_key == "testimony":
            allowed_gap, gap = seq_gap_check(attacker, defender, max_gap=3)
            if not allowed_gap:
                attacker.sp += actual_cost
                msg += f"📜 **Testimony failed!** Cannot impose testimony on a Beyonder {gap} sequences stronger."; color = 0x95A5A6
                cd_blocked = True
            attacker.testimony_cooldown = 2
            roll = random.randint(1, 3)
            if roll == 1:
                # Option 1: drain 15-25% enemy base HP, capped at 35% user base HP
                pct = random.uniform(0.15, 0.25)
                raw = int(defender.base_hp * pct)
                cap = int(attacker.base_hp * 0.35)
                dmg = min(raw, cap)
                actual_dmg = defender.apply_damage(int(dmg * attacker.get_attack_mult()), is_ability=True)
                msg += f"⚖️ **Testimony — Judgement!** Drained **{actual_dmg} HP** from **{defender.user.display_name}**! *(2 turn cd)*"; color = 0xE74C3C
            elif roll == 2:
                # Option 2: heal 20-25% user base HP
                pct = random.uniform(0.20, 0.25)
                heal = int(attacker.base_hp * pct)
                attacker.hp = min(attacker.max_hp, attacker.hp + heal)
                msg += f"⚖️ **Testimony — Acquittal!** Healed **{heal} HP** *(20-25% base HP)*! *(2 turn cd)*"; color = 0x2ECC71
            else:
                # Option 3: regen 20-25% user base SP
                pct = random.uniform(0.20, 0.25)
                sp_gain = int(attacker.max_sp * pct)
                attacker.sp = min(attacker.max_sp, attacker.sp + sp_gain + actual_cost)  # refund cost too
                msg += f"⚖️ **Testimony — Absolution!** Regained **{sp_gain} SP** *(20-25% base SP)*! *(2 turn cd)*"; color = 0x9B59B6

        elif ability_key == "arbitration":
            if attacker.arbiter_cooldown > 0:
                attacker.sp += actual_cost
                msg += f"⚖️ **Arbiter** on cooldown for **{attacker.arbiter_cooldown}** more turn(s)!"; color = 0x95A5A6
                cd_blocked = True
            elif random.random() < 0.40:
                # 40% failure — disagreement
                attacker.arbiter_cooldown = 6
                msg += f"⚖️ **Arbiter** — **{defender.user.display_name}** refuses to stand down! Arbitration fails. *(6 turn cd)*"; color = 0xE74C3C
            else:
                ceasefire_turns = random.randint(1, 3)
                # Both sides forced into non-violence
                attacker.arbiter_active = True; attacker.arbiter_turns = ceasefire_turns
                defender.arbiter_active = True; defender.arbiter_turns = ceasefire_turns
                attacker.arbiter_cooldown = 6
                msg += (f"⚖️ **ARBITER!** Both fighters stand down for **{ceasefire_turns} turn(s)**!\n"
                        f"⚠️ Attacking during this period summons divine punishment — **40% of the violator's HP**!\n"
                        f"*({attacker.user.display_name} is also bound by this decree)* *(6t cd)*"); color = 0xF1C40F

        elif ability_key == "jurisdiction":
            allowed_gap, gap = seq_gap_check(attacker, defender, max_gap=2)
            if not allowed_gap:
                attacker.sp += actual_cost
                msg += f"⚖️ **Jurisdiction failed!** The authority of a weaker Beyonder cannot bind Seq {get_seq_number(defender.role) if defender.role else '?'}. (gap: {gap})"; color = 0x95A5A6
                cd_blocked = True
            if attacker.jurisdiction_cooldown > 0:
                attacker.sp += actual_cost
                msg += f"🏛️ **Jurisdiction** on cooldown for **{attacker.jurisdiction_cooldown}** more turn(s)!"; color = 0x95A5A6
                cd_blocked = True
            else:
                attacker.jurisdiction_active = True
                attacker.jurisdiction_turns = 3
                attacker.jurisdiction_dmg_boost = 0.20
                attacker.jurisdiction_cooldown = 12
                msg += f"🏛️ **Jurisdiction!** The Sheriff has arrived — **+20% damage** for 3 turns, growing by **+7% each turn**! *(12 turn cd)*"; color = 0xF39C12

        elif ability_key == "memorise":
            pass  # handled via menu

        # ── Seq 8 Role abilities ──────────────────────────

        elif ability_key == "combat_studies":
            attacker.combat_studies_skip = True
            attacker.sp += actual_cost  # refund, costs a turn instead
            msg += f"📘 **{attacker.user.display_name}** begins studying — attacks permanently +20% next turn!"; color = 0x3498DB

        elif ability_key == "excavation":
            roll = random.randint(1, 3)
            if roll == 1:
                gain = random.randint(int(attacker.max_sp * 0.20), int(attacker.max_sp * 0.30))
                attacker.sp = min(attacker.max_sp, attacker.sp + gain)
                msg += f"⛏️ **Excavation!** Found ancient spirit — **+{gain} SP**!"; color = 0x9B59B6
            elif roll == 2:
                gain = random.randint(seq_dmg(attacker, 0.120), seq_dmg(attacker, 0.200))
                attacker.hp = min(attacker.max_hp, attacker.hp + gain)
                msg += f"⛏️ **Excavation!** Found a healing relic — **+{gain} HP**!"; color = 0x2ECC71
            else:
                exc_hp = seq_dmg(attacker, 0.060)
                attacker.stun_block = True
                attacker.hp = min(attacker.max_hp, attacker.hp + exc_hp)
                msg += f"⛏️ **Excavation!** Found an ancient ward — **stun block** + **+{exc_hp} HP**!"; color = 0xF39C12

        elif ability_key == "magic_trick":
            if ritual_choice == "escape":
                speed_diff = attacker.stats["speed"] - defender.stats["speed"]
                flee_chance = max(20, min(80, 50 + speed_diff * 8))
                if random.uniform(0, 100) < flee_chance:
                    bets = active_bets.pop(channel_id, {})
                    for bettor_id, bet in bets.items(): add_pounds(bettor_id, bet["amount"], guild_id=battle["guild_id"])
                    battles.pop(battle_key, None)
                    embed = discord.Embed(description=f"🎩 **{attacker.user.display_name}** vanishes in a puff of smoke!\n\n💰 Bets refunded.", color=0x95A5A6)
                    await interaction.channel.send(embed=embed)
                    old_msg = battle.get("battle_message")
                    if old_msg:
                        try: await old_msg.delete()
                        except: pass
                    return
                else:
                    msg += f"🎩 The escape trick failed — **{attacker.user.display_name}** couldn't vanish!"; color = 0x95A5A6
            else:
                element = random.choice(["freeze", "burn", "stun"])
                if element == "freeze":
                    applied = defender.apply_status_effect("freeze", 2)
                    if applied:
                        attacker.magic_trick_cooldown = 4
                    msg += f"🎩🧊 **Ice Trick!** **{defender.user.display_name}** is frozen for **2 turns**! *(4 turn cd)*" if applied else f"🎩 **{defender.user.display_name}** resisted the freeze!"; color = 0x3498DB
                elif element == "burn":
                    applied = defender.apply_status_effect("burn", 2)
                    if applied:
                        attacker.magic_trick_cooldown = 4
                    msg += f"🎩🔥 **Fire Trick!** **{defender.user.display_name}** burns for **2 turns**! *(4 turn cd)*" if applied else f"🎩 Burn resisted!"; color = 0xE74C3C
                else:
                    applied = defender.apply_status_effect("paralysis", 1)
                    if applied:
                        attacker.magic_trick_cooldown = 2
                    msg += f"🎩⚡ **Stun Trick!** **{defender.user.display_name}** stunned for **1 turn**! *(2 turn cd)*" if applied else f"🎩 Stun resisted!"; color = 0xF39C12

        elif ability_key == "card_tricks":
            # Clown Seq 8 — paper daggers: razor-hard paper that pierces flesh and sticks in bone
            count = random.randint(2, 4)
            total_dmg = 0
            hits = []
            for _ in range(count):
                dmg = defender.apply_damage(int(seq_dmg(attacker, 0.055) * attacker.get_attack_mult()), is_ability=True)
                hits.append(str(dmg))
                total_dmg += dmg
            applied = defender.apply_status_effect("bleed", 2)
            attacker.card_tricks_cooldown = 2
            bleed_txt = " + bleed 2 turns" if applied else ""
            msg += (f"🃏 **Paper Daggers!** **{count}** daggers fly — **{' + '.join(hits)}** = **{total_dmg}** total damage{bleed_txt}! "
                    f"*(2 turn cd)*"); color = 0xE74C3C

        elif ability_key == "swindling":
            attacker.swindling_uses += 1
            fail_chance = min(90, (attacker.swindling_uses - 1) * 4)
            if random.randint(1, 100) <= fail_chance:
                attacker.sp += actual_cost
                msg += f"🎭 **Swindling** failed! *(fail chance was {fail_chance}%)*"; color = 0x95A5A6
            else:
                stolen = min(seq_val(attacker, 20), defender.sp)
                defender.sp = max(0, defender.sp - stolen)
                attacker.sp = min(attacker.max_sp, attacker.sp + stolen)
                msg += f"🎭 **Swindled {stolen} SP** from **{defender.user.display_name}**! *(fail chance {fail_chance}%)*"; color = 0xF39C12

        elif ability_key == "provocation":
            if attacker.provocation_cooldown > 0:
                attacker.sp += actual_cost
                msg += f"😤 **Provocation** on cooldown for **{attacker.provocation_cooldown}** more turn(s)!"; color = 0x95A5A6
                cd_blocked = True
            else:
                attacker.provocation_cooldown = 4
                # Force defender to skip their next 1 turn and drain scaled SP
                defender.trapped = max(getattr(defender, "trapped", 0), 1)
                defender.provocation_trapped = True
                sp_drain = min(seq_val(attacker, 25), defender.sp)
                defender.sp = max(0, defender.sp - sp_drain)
                msg += f"😤 **Provocation!** **{defender.user.display_name}** is enraged into submission — forced to skip **1 turn** and loses **{sp_drain} SP**! *(4 turn cd)*"; color = 0xF39C12

        elif ability_key == "instigation":
            if random.random() < 0.5:
                defender.instigated_action = ritual_choice
                msg += f"😈 **Instigation!** **{defender.user.display_name}** will be forced to use **{ritual_choice}** next turn!"; color = 0x8E44AD
            else:
                msg += f"😈 **Instigation** failed!"; color = 0x95A5A6

        elif ability_key == "moral_freedom":
            if attacker.moral_freedom_used:
                attacker.sp += actual_cost
                msg += f"👼 Moral Freedom already used!"; color = 0x95A5A6
                cd_blocked = True
            else:
                attacker.moral_freedom_used = True
                defender.moral_freedom_active = True
                dot = seq_val(attacker, 15)
                defender._moral_freedom_dot = dot
                msg += f"👼 **Moral Freedom!** **{defender.user.display_name}** suffers **{dot} damage/turn** for the rest of battle!"; color = 0x2C3E50

        elif ability_key == "rage_baited":
            if attacker.rage_baited_cooldown > 0:
                attacker.sp += actual_cost
                msg += f"😡 Rage Baited on cooldown!"; color = 0x95A5A6
                cd_blocked = True
            else:
                attacker.rage_baited_turns = 2
                attacker.rage_baited_cooldown = 2
                msg += f"😡 **Rage Baited!** Double damage at 50% miss for **2 turns**!"; color = 0xE74C3C

        elif ability_key == "fighting_spirit":
            # Pugilist Seq 8 — iron fist fighting spirit; rouses physical power
            if attacker.fighting_spirit_active:
                attacker.sp += actual_cost
                msg += f"👊 **Fighting Spirit** already active!"; color = 0x95A5A6
                cd_blocked = True
            else:
                attacker.fighting_spirit_active = True
                attacker.fighting_spirit_turns = 4
                attacker.fighting_spirit_boost = 0.30  # +30% attack
                attacker.fighting_spirit_cooldown = 6
                msg += (f"👊 **Fighting Spirit!** **{attacker.user.display_name}** ignites their iron will — "
                        f"**+30% physical damage** for **4 turns**! Stuns from lower-seq opponents are halved. *(6 turn cd)*"); color = 0xE74C3C

        elif ability_key == "poem":
            attacker.poem_cooldown = 3
            roll = random.randint(1, 3)
            if roll == 1:
                applied = defender.apply_status_effect("slumber", 1)
                if applied:
                    msg += f"📜 **Midnight Poem!** The verse lulls **{defender.user.display_name}** to sleep for **1 turn**! *(3 turn cd)*"; color = 0x9B59B6
                else:
                    attacker.sp += actual_cost
                    msg += f"📜 **Midnight Poem!** The sleep effect was resisted! *(3 turn cd)*"; color = 0x95A5A6
            elif roll == 2:
                self_dmg = seq_val(attacker, 15)
                attacker.hp = max(0, attacker.hp - self_dmg)
                msg += f"📜 **Midnight Poem!** The dark verse turns inward — **{attacker.user.display_name}** suffers **{self_dmg} self-inflicted damage**! *(3 turn cd)*"; color = 0xE74C3C
            else:
                drained = min(seq_val(attacker, 20), defender.sp)
                defender.sp = max(0, defender.sp - drained)
                msg += f"📜 **Midnight Poem!** The verse drains **{drained} SP** from **{defender.user.display_name}**! *(3 turn cd)*"; color = 0x8E44AD

        elif ability_key == "burial_restoration":
            restored = attacker.max_sp - attacker.sp
            attacker.sp = attacker.max_sp
            attacker.burial_cooldown = 4
            msg += f"⚰️ **Burial Restoration!** **{attacker.user.display_name}** honours the grave and receives a soul's blessing — SP fully restored! **(+{restored} SP)**"; color = 0x8E44AD

        elif ability_key == "listening":
            attacker.listening_active = True
            attacker.sp = attacker.max_sp - actual_cost
            msg += f"👂 **{attacker.user.display_name}** listens — SP fully restored! Each turn has a 1/3 chance to stun."; color = 0x9B59B6

        elif ability_key == "power_up_punch":
            if attacker.folk_of_rage_active:
                attacker.sp += actual_cost
                msg += f"👊 **Power Up Punch** already active!"
                cd_blocked = True
            else:
                attacker.folk_of_rage_active = True
                attacker.folk_of_rage_turns = 5
                attacker.folk_of_rage_pct = 0.05
                msg += f"👊 **Power Up Punch!** Normal strikes amplified — grows up to **25%** over **5 rounds**!"; color = 0xE74C3C

        elif ability_key == "ritualistic_reasoning":
            if attacker.ritualistic_reasoning_cooldown > 0:
                attacker.sp += actual_cost
                msg += f"🔬 **Ritualistic Reasoning** on cooldown for **{attacker.ritualistic_reasoning_cooldown}** turn(s)!"; cd_blocked = True
            else:
                defender.sabotaged = True
                attacker.ritualistic_reasoning_cooldown = 1
                msg += f"🔬 **Ritualistic Reasoning!** **{defender.user.display_name}**'s next move will fail or backfire! *(1 turn cd)*"; color = 0x8E44AD

        elif ability_key == "reading":
            attacker.reading_prediction = ritual_choice
            msg += f"🔭 **Reading!** **{attacker.user.display_name}** focuses their mind, silently predicting **{defender.user.display_name}**'s next move…"; color = 0x9B59B6

        elif ability_key == "brilliant_light":
            actual_dmg = apply_ability_damage(attacker, defender, 0.045)
            applied = defender.apply_status_effect("paralysis", 1)
            if applied:
                attacker.brilliant_light_cooldown = 2
            stun_txt = " + stunned 1 turn *(2 turn cd)*" if applied else ""
            msg += f"☀️ **Brilliant Light!** **{actual_dmg}** damage{stun_txt}!"; color = 0xF1C40F

        elif ability_key == "treatment":
            # Clear all existing status effects first
            cleared = []
            for attr, label in [
                ("bleed","bleed"), ("burn","burn"), ("paralysis","paralysis"),
                ("slumber","sleep"), ("freeze","freeze"), ("trapped","trap"),
                ("debuff_stacks","debuff"), ("sneak_attack_pending","sneak attack"),
                ("moral_freedom_active","moral freedom"),
            ]:
                if getattr(attacker, attr, 0):
                    setattr(attacker, attr, 0); cleared.append(label)
            if attacker.freeze_weakened:          attacker.freeze_weakened = False;          cleared.append("freeze weakness")
            if attacker.gun_shot_bleed:           attacker.gun_shot_bleed = False;           cleared.append("gunshot bleed")
            if attacker.charm_turns > 0:          attacker.charm_turns = 0;                  cleared.append("charm")
            if attacker.scroll_status:            attacker.scroll_status = None;             cleared.append("scroll")
            if attacker.judgement_debuff_turns>0: attacker.judgement_debuff_turns = 0;       cleared.append("judgement")
            if attacker.curse_of_misfortune_turns>0: attacker.curse_of_misfortune_turns = 0; cleared.append("misfortune")
            if attacker.spiritual_takeover_active: attacker.spiritual_takeover_active = False; cleared.append("spirit takeover")
            if getattr(attacker,"dream_alteration_acc_loss",0)>0: attacker.dream_alteration_acc_loss=0; cleared.append("dream alteration")
            attacker.status_immune = 3
            cleared_txt = f" Cleared: **{', '.join(cleared)}**." if cleared else ""
            msg += f"💊 **Treatment!**{cleared_txt} Immune to status effects for **3 turns**!"; color = 0x2ECC71

        elif ability_key == "animal_companion":
            attacker.animal_companion_active = True
            attacker.animal_companion_turn = 0
            ac_dmg = seq_val(attacker, 10)
            msg += f"🐾 **Animal Companion** summoned — attacks **{defender.user.display_name}** every other turn for **{ac_dmg} damage**!"; color = 0x2ECC71

        elif ability_key == "computing":
            # Robot Seq 8 — compute optimal attack vectors; guarantee accuracy for limited duration
            if attacker.computing_active:
                attacker.sp += actual_cost
                msg += f"🤖 **Computing** already active!"; color = 0x95A5A6
                cd_blocked = True
            else:
                attacker.computing_active = True
                attacker.computing_turns = 4
                attacker.computing_cooldown = 6
                msg += (f"🤖 **Computing!** **{attacker.user.display_name}** calculates optimal attack vectors — "
                        f"**100% accuracy** and **+15% crit chance** for **4 turns**! *(6 turn cd)*"); color = 0x3498DB

        elif ability_key == "domination":
            allowed_gap, gap = seq_gap_check(attacker, defender, max_gap=2)
            if not allowed_gap:
                attacker.sp += actual_cost
                msg += f"🧠 **Domination failed!** Too strong to dominate. (gap: {gap})"; color = 0x95A5A6; cd_blocked = True
            elif attacker.domination_cooldown > 0:
                attacker.sp += actual_cost
                msg += f"👊 **Domination** on cooldown for **{attacker.domination_cooldown}** more turn(s)!"; color = 0x95A5A6; cd_blocked = True
            else:
                attacker.domination_active = True
                attacker.domination_cooldown = 5
                if random.random() < 0.5:
                    attacker.domination_turns = 3
                    attacker.domination_boost = 1.0  # +100%
                    msg += f"👊 **Domination!** **+100% all damage** for **3 turns**! *(5 turn cd)*"; color = 0xE74C3C
                else:
                    attacker.domination_turns = 5
                    attacker.domination_boost = 0.5  # +50%
                    msg += f"👊 **Domination!** **+50% all damage** for **5 turns**! *(5 turn cd)*"; color = 0xE74C3C

        elif ability_key == "gun_shot":
            attacker.gun_shot_cooldown = 4
            actual_dmg = apply_ability_damage(attacker, defender, 0.068)
            defender.gun_shot_bleed = True
            msg += f"🔫 **Gun Shot!** **{actual_dmg}** damage + permanent bleed **5/turn** for the rest of battle! *(4 turn cd)*"; color = 0xE74C3C

        # ── Seq 7 Role abilities ──────────────────────────

        elif ability_key == "paper_figurine":
            attacker.paper_figurine_turns = 1
            msg += f"🪆 **Paper Figurine!** All attacks against **{attacker.user.display_name}** fail for **1 turn**!"; color = 0x9B59B6

        elif ability_key == "astrology":
            attacker.astrology_used = True
            atk_seq = get_seq_number(attacker.role) if attacker.role else 9
            def_seq = get_seq_number(defender.role) if defender.role else 9
            if def_seq < atk_seq:
                # Opponent is stronger — flee or stand
                attacker.astrology_flee_ready = True
                msg += f"🌟 **Astrology!** The stars foretell doom — **{defender.user.display_name}** outranks you! Choose to flee or stand your ground."; color = 0x9B59B6
            elif def_seq == atk_seq:
                # Same seq — only 20% dodge chance
                if random.random() < 0.20:
                    astro_dmg = int(attacker.hp * 0.40)
                    actual_astro_dmg = defender.apply_damage(astro_dmg, is_ability=True)
                    msg += f"🌟 **Astrology!** *(same seq — 20% chance)* The stars aligned — **{actual_astro_dmg}** damage (40% current HP)! *(one-time use)*"; color = 0x9B59B6
                else:
                    attacker.sp += actual_cost
                    msg += f"🌟 **Astrology!** *(same seq — 20% chance)* The stars did not align this time!"; color = 0x95A5A6
                    cd_blocked = True
            else:
                # Opponent is weaker — full 40% HP damage
                astro_dmg = int(attacker.hp * 0.40)
                actual_astro_dmg = defender.apply_damage(astro_dmg, is_ability=True)
                msg += f"🌟 **Astrology!** The stars align — **{actual_astro_dmg}** damage (40% current HP)! *(one-time use)*"; color = 0x9B59B6

        elif ability_key == "observation":
            attacker.observation_active = True
            attacker.observation_cooldown = 2
            msg += f"🔎 **Observation!** **{attacker.user.display_name}** takes **40% less damage** from all sources next turn! *(2 turn cd)*"; color = 0x3498DB

        elif ability_key == "therapy":
            attacker.sp += actual_cost  # refund — costs a turn instead
            cleared = []
            for attr, name in [("bleed","bleed"),("paralysis","paralysis"),("burn","burn"),
                                ("freeze","freeze"),("slumber","sleep"),("defence_reduction","defence reduction"),
                                ("moral_freedom_active","moral freedom"),("debuff_stacks","debuff stacks")]:
                if getattr(attacker, attr, 0):
                    setattr(attacker, attr, 0); cleared.append(name)
            if attacker.freeze_weakened:         attacker.freeze_weakened = False;          cleared.append("freeze weakened")
            if attacker.gun_shot_bleed:          attacker.gun_shot_bleed = False;           cleared.append("gunshot bleed")
            if attacker.sneak_attack_pending:    attacker.sneak_attack_pending = False;     cleared.append("sneak attack")
            if attacker.trapped > 0:             attacker.trapped = 0;                      cleared.append("trapped")
            if attacker.charm_turns > 0:         attacker.charm_turns = 0; attacker.charm_chance = 0.0; cleared.append("charm")
            if attacker.scroll_status:           attacker.scroll_status = None;             cleared.append("scroll affliction")
            if attacker.notary_debuff_turns > 0: attacker.notary_debuff_turns = 0;          cleared.append("notary debuff")
            attacker.therapy_cooldown = 1
            msg += f"🛋️ **Therapy!** Cleared: **{', '.join(cleared) if cleared else 'nothing'}**! Turn lost. *(1 turn cd)*"; color = 0x2ECC71
            # Costs the turn — swap and return early
            swap_turn(battle)
            v = check_victory(attacker, defender, battle_key, msg)
            if v:
                await interaction.channel.send(embed=v)
                old_msg = battle.get("battle_message")
                if old_msg:
                    try: await old_msg.delete()
                    except: pass
                return
            embed = make_battle_embed(msg, battle, battle["turn"].display_name, color)
            await send_and_replace(battle, interaction.channel, embed, view=_make_battle_view(battle))
            return

        elif ability_key == "holy_water":
            hw_instant = max(1, int(attacker.max_hp * 0.15))
            attacker.hp = min(attacker.max_hp, attacker.hp + hw_instant)
            attacker.holy_water_regen = 2
            msg += f"💧 **Holy Water!** Healed **{hw_instant} HP** (15% max HP) + **~3% HP/turn** for **2 turns**!"; color = 0x2ECC71

        elif ability_key == "water_bullet":
            actual_dmg = apply_ability_damage(attacker, defender, 0.136, skill_key="water_bullet")
            freeze_applied = False
            if random.random() < 0.25:
                freeze_applied = defender.apply_status_effect("freeze", 1)
            attacker.water_bullet_cooldown = 2
            freeze_txt = " + frozen 1 turn" if freeze_applied else ""
            msg += f"💧🔫 **Water Bullet!** **{actual_dmg}** damage{freeze_txt}!"; color = 0x3498DB

        elif ability_key == "sneak_attack":
            actual_dmg = apply_ability_damage(attacker, defender, 0.136, skill_key="sneak_attack")
            defender.sneak_attack_pending = True
            attacker.sneak_attack_cooldown = 2
            msg += f"🥷 **Sneak Attack!** **{actual_dmg}** damage — **{defender.user.display_name}** loses their next turn! *(2 turn cd)*"; color = 0x2C3E50

        elif ability_key == "analyse":
            skill_to_analyse = ritual_choice
            defender.analysed_skills[skill_to_analyse] = 0.30
            attacker.analyse_uses -= 1
            skill_info, _ = get_ability_info(skill_to_analyse)
            skill_name = skill_info["name"] if skill_info else skill_to_analyse
            msg += (f"🧐 **Analyse!** **{skill_name}** from **{defender.user.display_name}** permanently deals **30% less** damage! "
                    f"*({attacker.analyse_uses} use(s) left)*"); color = 0x3498DB

        elif ability_key == "sleep_spell":
            applied = defender.apply_status_effect("slumber", 2)
            attacker.sleep_spell_cooldown = 4
            if applied:
                msg += f"😴 **Sleep Spell!** **{defender.user.display_name}** slumbers for **2 turns**! *(4 turn cd)*"; color = 0x9B59B6
            else:
                attacker.sp += actual_cost
                msg += f"😴 Sleep Spell resisted!"; color = 0x95A5A6

        elif ability_key == "summoning_dead":
            roll = random.randint(1, 100)
            if roll <= 5:
                spirits = 3
            elif roll <= 30:
                spirits = 2
            else:
                spirits = 1
            sp_cost = 30 if spirits == 3 else 20
            if attacker.sp < sp_cost:
                await interaction.followup.send(f"❌ Need **{sp_cost} SP** — only have **{attacker.sp}**.", ephemeral=True); return
            attacker.sp -= sp_cost
            total_dmg = sum(apply_ability_damage(attacker, defender, 0.023) for _ in range(spirits))
            stun_applied = False
            if spirits >= 2 and defender.can_receive_status():
                stun_applied = defender.apply_status_effect("paralysis", 1)
            attacker.summoning_dead_cooldown = 2
            stun_txt = " + stun" if stun_applied else ""
            msg += f"💀 **Summoning Dead!** **{spirits}** spirit(s) — **{total_dmg}** damage{stun_txt}!"; color = 0x8E44AD

        elif ability_key == "weapon_throw":
            actual_dmg = apply_ability_damage(attacker, defender, 0.182, skill_key="weapon_throw")
            applied = defender.apply_status_effect("bleed", 3)
            attacker.weapon_throw_cooldown = 4
            bleed_txt = " + bleed 3 turns" if applied else ""
            msg += f"🗡️ **Weapon Throw!** **{actual_dmg}** damage{bleed_txt}! *(4 turn cd)*"; color = 0xE74C3C

        elif ability_key == "witch_curse":
            if attacker.witch_curse_cooldown > 0:
                attacker.sp += actual_cost
                msg += f"🧙 **Witch Curse** on cooldown ({attacker.witch_curse_cooldown}t)!"; color = 0x95A5A6; cd_blocked = True
            else:
                attacker.witch_curse_cooldown = 4
                roll = random.random()
                if roll < 0.40:
                    # Black Flame — ~20 dmg/turn + SP drain for 3 turns
                    black_dmg = seq_val(attacker, 20)
                    defender.hp = max(0, defender.hp - black_dmg)
                    defender._black_flame_dot_turns = 3; defender._black_flame_dot_dmg = black_dmg
                    defender._black_flame_sp_drain = 10
                    msg += f"🔥 **Witch Curse — Black Flame!** **{black_dmg}** damage + **{black_dmg} dmg/turn** + **10 SP drain/turn** for **3 turns**! *(4t cd)*"; color = 0x8E44AD
                elif roll < 0.70:
                    # Frost — stun in ice for 2 turns
                    applied = defender.apply_status_effect("stun", 2) or defender.apply_status_effect("freeze", 2)
                    msg += f"❄️ **Witch Curse — Frost!** **{defender.user.display_name}** frozen in ice for **2 turns**! *(4t cd)*"; color = 0x3498DB
                else:
                    # Mirror Substitution — secretly take all damage meant for you next turn
                    attacker._mirror_substitution_active = True
                    msg += f"🪞 **Witch Curse — Mirror Substitution!** Set secretly... *(4t cd)*"; color = 0x9B59B6

        elif ability_key == "fire_ravens":
            if attacker.fire_ravens_cooldown > 0:
                attacker.sp += actual_cost
                msg += f"🐦‍🔥 **Fire Ravens** on cooldown ({attacker.fire_ravens_cooldown}t)!"; color = 0x95A5A6; cd_blocked = True
            else:
                # 40% → 3, 25% → 4, 15% → 5, 10% → 6 (10-20% → 7)
                roll = random.random()
                if roll < 0.40:   count = 3
                elif roll < 0.65: count = 4
                elif roll < 0.80: count = 5
                elif roll < 0.90: count = 6
                else:             count = 7
                total_dmg = sum(apply_ability_damage(attacker, defender, 0.038) for _ in range(count))
                attacker.fire_ravens_cooldown = 5
                msg += f"🐦‍🔥 **Fire Ravens! {count}** ravens strike — **{total_dmg}** total damage! *(5t cd)*"; color = 0xE74C3C

        elif ability_key == "magic_spell":
            if attacker.magic_spell_cooldown > 0:
                attacker.sp += actual_cost
                msg += f"✨ **Magic Spell** is on cooldown for **{attacker.magic_spell_cooldown}** more turn(s)!"; color = 0x95A5A6
                cd_blocked = True
            else:
                attacker.magic_spell_cooldown = 1
                roll = random.randint(1, 3)
                if roll == 1:
                    attacker.damage_boost_pct = min(1.0, attacker.damage_boost_pct + 0.30)
                    msg += f"✨ **Magic Spell!** Permanent **+30% damage boost**!"; color = 0xF1C40F
                elif roll == 2:
                    ms_heal = seq_dmg(attacker, 0.150)
                    attacker.hp = min(attacker.max_hp, attacker.hp + ms_heal)
                    msg += f"✨ **Magic Spell!** Healed **{ms_heal} HP**!"; color = 0x2ECC71
                else:
                    ms_sp = max(1, int(attacker.max_sp * 0.20))
                    attacker.sp = min(attacker.max_sp, attacker.sp + ms_sp + actual_cost)
                    msg += f"✨ **Magic Spell!** Restored **{ms_sp} SP**!"; color = 0x9B59B6

        elif ability_key == "appraisal":
            attacker.appraisal_active = True
            attacker.appraisal_cooldown = 3
            msg += f"🏷️ **Appraisal!** Next action deals **40% more** damage! *(3 turn cd)*"; color = 0xF39C12

        elif ability_key == "lucky_day":
            attacker.lucky_day_used = True
            defender.lucky_day_turns = 4
            msg += f"🍀 **Lucky Day!** **{defender.user.display_name}**'s hit chance halved for **4 rounds**!"; color = 0x2ECC71

        elif ability_key == "blood_sucker":
            drain = random.randint(seq_dmg(attacker, 0.068), seq_dmg(attacker, 0.136))
            actual_dmg = apply_ability_damage(attacker, defender, drain / SEQ_AVG_HP.get(get_seq_number(attacker.role) if attacker.role else 9, 220), skill_key="blood_sucker")
            attacker.hp = min(attacker.max_hp, attacker.hp + actual_dmg)
            attacker.blood_sucker_cooldown = 1
            msg += f"🧛 **Blood Sucker!** Drained **{actual_dmg} HP** from **{defender.user.display_name}**! *(1 turn cd)*"; color = 0x8E44AD

        elif ability_key == "vine_bomb":
            actual_dmg = apply_ability_damage(attacker, defender, 0.070, skill_key="vine_bomb")
            defender.trapped = 2
            attacker.vine_bomb_cooldown = 4
            msg += f"🌿 **Vine Bomb!** **{actual_dmg}** damage + **{defender.user.display_name}** trapped **2 turns**! *(4 turn cd)*"; color = 0x2ECC71

        elif ability_key == "transformation":
            if attacker.transformation_used:
                attacker.sp += actual_cost
                msg += f"🐺 Already transformed!"
                cd_blocked = True
            else:
                attacker.transformation_used = True
                attacker.transformed = True
                bonus_hp = int(attacker.max_hp * 0.5)  # 1.5× total (add 0.5× current)
                attacker.transform_hp_bonus = bonus_hp
                attacker.max_hp += bonus_hp
                attacker.hp = min(attacker.max_hp, attacker.hp + bonus_hp)
                attacker.transform_dodge_reduction = 0.15  # starts lower — debuffed
                attacker.transform_regen = 5
                attacker.transform_turns = 0
                msg += (f"🐺 **Transformation!** **{attacker.user.display_name}** shifts into werewolf form! "
                        f"HP ×1.5 (+{bonus_hp}), **5 HP regen/turn**. "
                        f"Dodge starts at **15%**, rises **5%/round** (max 85%). Lasts **5 turns**. Reverts to **50% HP**!"); color = 0x8E44AD

        elif ability_key == "vile_crime":
            if getattr(attacker, "vile_crime_cd", 0) > 0:
                attacker.sp += actual_cost
                msg += f"🔪 **Vile Crime** on cooldown ({attacker.vile_crime_cd}t)!"; cd_blocked = True
            elif attacker.vile_crime_used:
                attacker.sp += actual_cost
                msg += f"🔪 **Vile Crime** already completed!"; cd_blocked = True
            elif attacker.vile_crime_count == 0:
                attacker.vile_crime_count = 1
                attacker.vile_crime_hits_needed = 2  # track 2 hits
                msg += f"🔪 **Vile Crime!** Tracking — land **2 standard attacks** to apply **bleed 6 turns**! *(0/2)*"; color = 0xE74C3C
            else:
                attacker.sp += actual_cost
                msg += f"🔪 Vile Crime already tracking — use standard attacks! *({attacker.vile_crime_count}/2)*"; cd_blocked = True

        elif ability_key == "bribe":
            if attacker.bribe_cooldown > 0:
                attacker.sp += actual_cost
                msg += f"💰 **Bribe** on cooldown for **{attacker.bribe_cooldown}** more turn(s)!"; color = 0x95A5A6
            else:
                cost_soli = 70000
                bal = get_pounds(attacker.user.id, guild_id=battle["guild_id"])
                if bal < cost_soli:
                    attacker.sp += actual_cost
                    await interaction.followup.send(f"❌ **Bribe** needs **{cost_soli:,} soli** — you only have **{bal:,}**.", ephemeral=True); return
                # Fail chance: 10% per spirit stat enemy has over attacker
                spirit_diff = max(0, defender.stats.get("spirit", 0) - attacker.stats.get("spirit", 0))
                fail_chance = spirit_diff * 0.10
                if random.random() < fail_chance:
                    # Failed — 50% soli refunded, 50% lost
                    refund = cost_soli // 2
                    remove_pounds(attacker.user.id, cost_soli - refund, guild_id=battle["guild_id"])
                    attacker.bribe_cooldown = 5
                    msg += (f"💰 **Bribe failed!** **{defender.user.display_name}**'s spirit resisted the bribe — "
                            f"**{refund:,} soli** refunded, **{refund:,} soli** lost. *(5 turn cd)*"); color = 0x95A5A6
                else:
                    remove_pounds(attacker.user.id, cost_soli, guild_id=battle["guild_id"])
                    add_pounds(defender.user.id, cost_soli, guild_id=battle["guild_id"])
                    bribe_choice = ritual_choice if ritual_choice in ("weaken", "charm", "connect") else "weaken"
                    attacker.bribe_pending = bribe_choice
                    attacker.bribe_cooldown = 5
                    attacker.bribe_concealed_turn = True
                    # Fully concealed — only show evade
                    msg += f"⚔️ **{attacker.user.display_name}** attacks — **{defender.user.display_name}** evades!"; color = 0x95A5A6

        elif ability_key == "mental_piercing":
            actual_dmg = apply_ability_damage(attacker, defender, 0.136, skill_key="mental_piercing")
            sp_drain = min(max(1, int(defender.max_sp * 0.10)), defender.sp)
            defender.sp = max(0, defender.sp - sp_drain)
            attacker.mental_piercing_cooldown = 2
            msg += f"🧠 **Mental Piercing!** **{actual_dmg}** HP damage + **{sp_drain} SP** drained! *(2 turn cd)*"; color = 0xE74C3C

        # ── Seq 6 Role abilities ──────────────────────────

        elif ability_key == "faceless":
            allowed_gap, gap = seq_gap_check(attacker, defender, max_gap=2)
            if not allowed_gap:
                attacker.sp += actual_cost
                msg += f"👤 **Faceless failed!** Cannot steal the identity of someone {gap} sequences stronger."; color = 0x95A5A6
                cd_blocked = True
            actual_dmg = apply_ability_damage(attacker, defender, 0.159, is_ability=True, skill_key="faceless")
            applied = defender.apply_status_effect("paralysis", 1)
            attacker.faceless_cooldown = 2
            stun_txt = " + turn skip" if applied else ""
            msg += f"🎭 **Faceless!** Betrayal strikes — **{actual_dmg}** damage{stun_txt}! *(2 turn cd)*"; color = 0x8E44AD

        elif ability_key == "prometheus":
            if attacker.prometheus_cooldown > 0:
                attacker.sp += actual_cost
                msg += f"🔥 **Prometheus** on cooldown for **{attacker.prometheus_cooldown}** more turn(s)!"; color = 0x95A5A6
                cd_blocked = True
            else:
                # Build list of abilities the defender has USED this battle
                used = list(getattr(defender, "abilities_used_this_battle", set()))
                if not used:
                    attacker.sp += actual_cost
                    msg += f"🔥 **Prometheus** — nothing to steal yet!"; color = 0x95A5A6
                    cd_blocked = True
                else:
                    atk_seq = get_seq_number(attacker.role) if attacker.role else 9
                    def_seq = get_seq_number(defender.role) if defender.role else 9
                    if atk_seq <= def_seq:
                        # Higher seq (lower number) or equal — pick any used ability
                        stolen = ritual_choice if ritual_choice and ritual_choice in used else used[0]
                    else:
                        # Lower seq — steal random
                        stolen = random.choice(used)

                    # Remove from defender permanently
                    if hasattr(defender, "abilities_used_this_battle"):
                        defender.abilities_used_this_battle.discard(stolen)
                    if defender.role_ability == stolen:
                        defender.role_ability = None
                    attacker.prometheus_stolen_ability = stolen
                    attacker.prometheus_cooldown = 3
                    info_stolen, _ = get_ability_info(stolen)
                    sname = info_stolen["name"] if info_stolen else stolen
                    strip_log = []

                    if stolen == "hurricane_of_light" and defender.hurricane_charging:
                        defender.hurricane_charging = False
                        strip_log.append("Hurricane charge interrupted")
                    elif stolen == "devil_form" and defender.devil_form_active:
                        defender.devil_form_active = False
                        hp_remove = defender.devil_form_hp_bonus
                        defender.max_hp = max(1, defender.max_hp - hp_remove)
                        defender.hp = min(defender.max_hp, defender.hp)
                        defender.devil_form_hp_bonus = 0
                        sp_remove = defender.devil_form_sp_bonus
                        defender.max_sp = max(1, defender.max_sp - sp_remove)
                        defender.sp = min(defender.max_sp, defender.sp)
                        defender.devil_form_hp_bonus = 0
                        defender.damage_boost_pct = max(0.0, defender.damage_boost_pct - 0.50)
                        strip_log.append("Devil Form shattered")
                    elif stolen == "spirit_guide" and defender.spirit_guide_active:
                        defender.spirit_guide_active = False
                        defender.spirit_guide_turns = 0
                        defender.spirit_guide_cooldown = 7
                        strip_log.append("Spirit Guide banished")
                    elif stolen == "transformation" and defender.transformed:
                        defender.transformed = False
                        bonus_hp = defender.max_hp * 2 // 3
                        defender.max_hp = max(1, defender.max_hp - bonus_hp)
                        defender.hp = min(defender.max_hp, defender.hp)
                        strip_log.append("Transformation broken")
                    elif stolen == "night_boost" and defender.night_boost_active:
                        defender.night_boost_active = False
                        defender.night_boost_damage = 0
                        strip_log.append("Night Boost extinguished")
                    elif stolen == "prayer" and defender.prayer_active:
                        defender.prayer_active = False
                        strip_log.append("Prayer silenced")
                    elif stolen == "combat_studies" and defender.combat_studies_active:
                        defender.combat_studies_active = False
                        strip_log.append("Combat Studies erased")

                    strip_txt = f"\n> Torn down: *{'; '.join(strip_log)}*" if strip_log else ""
                    pick_txt = " *(you picked it)*" if atk_seq <= def_seq else " *(random steal)*"
                    msg += f"🔥 **Prometheus!** Stole **{sname}**{pick_txt} from **{defender.user.display_name}**!{strip_txt} Use it via Pathway. *(3 turn cd)*"; color = 0xE67E22

        elif ability_key == "scribe":
            if attacker.scribe_cooldown > 0:
                attacker.sp += actual_cost
                msg += f"📖 **Scribe** on cooldown for **{attacker.scribe_cooldown}** more turn(s)!"; color = 0x95A5A6
                cd_blocked = True
            else:
                # Mark pending — will copy the NEXT ability used against attacker (even while stunned)
                attacker.scribe_pending = True
                attacker.scribe_record_in = 1   # record ability used against us next turn
                attacker.scribe_cooldown = 3
                msg += f"📖 **Scribe!** Soul as pen, spirit body as paper — the next ability used against you will be recorded, even through a stun! *(3 turn cd)*"; color = 0x3498DB

        elif ability_key == "hypnotist":
            attacker.hypnotist_cooldown = 2
            actual_dmg = defender.apply_damage(seq_dmg(attacker, 0.091), is_ability=True)
            applied = defender.apply_status_effect("paralysis", 1)
            stun_txt = " + stunned 1 turn" if applied else " *(stun resisted)*"
            msg += f"🌀 **Hypnotist!** **{defender.user.display_name}** is deep in trance — **{actual_dmg}** self-damage{stun_txt}! *(2 turn cd)*"; color = 0x8E44AD

        elif ability_key == "notary":
            attacker.notary_cooldown = 4
            if ritual_choice == "buff_self":
                attacker.notary_buff_turns = 3
                msg += f"📜 **Notary!** Proclaimed in the name of your god — **+50% damage** for **3 turns**! *(4 turn cd)*"; color = 0xF1C40F
            elif ritual_choice == "debuff_opponent":
                defender.notary_debuff_turns = 2
                msg += f"📜 **Notary!** Proclaimed weakness upon **{defender.user.display_name}** — **−50% damage** for **2 turns**! *(4 turn cd)*"; color = 0x3498DB

        elif ability_key == "wind_blessed":
            if _is_scribe_copy:
                # Scribe copy: fire immediately — no build-up, no extra SP cost
                attacker.wind_blessed_cooldown = 5
                if defender.phasing_active:
                    defender.phasing_active = False
                    defender.phasing_cooldown = 1
                    msg += f"🌪️ **WIND BLESSED (Copied)!** — **{defender.user.display_name}** phases through the blast!"; color = 0x3498DB
                else:
                    actual_dmg = defender.apply_damage(seq_dmg(attacker, 0.273), is_ability=True)
                    msg += f"🌪️ **WIND BLESSED (Copied)!** Unleashed immediately — **{actual_dmg}** damage! *(5 turn cd)*"; color = 0x3498DB
            elif attacker.wind_blessed_buildup == 0:
                if attacker.sp >= 25:
                    attacker.sp -= 25
                    attacker.wind_blessed_buildup = 1
                    msg += f"🌪️ **Wind Blessed** — channelling wind *(round 1/2, −25 SP)*. Attack next turn to continue building!"; color = 0x3498DB
                else:
                    attacker.sp += actual_cost
                    msg += f"🌪️ Not enough SP to begin Wind Blessed!"; color = 0x95A5A6
            else:
                attacker.sp += actual_cost
                msg += f"🌪️ Wind Blessed is already building — wait for it to fire automatically!"; color = 0x95A5A6
                cd_blocked = True

        elif ability_key == "rose_bishop":
            attacker.rose_bishop_cooldown = 2
            if ritual_choice == "flesh_bomb":
                fb_hp_cost = seq_val(attacker, 20)
                fb_sp_cost = seq_val(attacker, 15)
                if attacker.hp <= fb_hp_cost:
                    attacker.sp += actual_cost
                    msg += f"🌹 **Flesh Bomb** — not enough HP to cast (need >{fb_hp_cost} HP)!"; color = 0x95A5A6
                    cd_blocked = True
                else:
                    attacker.hp = max(1, attacker.hp - fb_hp_cost)
                    attacker.sp = max(0, attacker.sp - fb_sp_cost)
                    actual_dmg = apply_ability_damage(attacker, defender, 0.136, is_ability=True)
                    msg += f"🌹 **Flesh Bomb!** **{actual_dmg}** damage at cost of **{fb_hp_cost} HP + {fb_sp_cost} SP**! *(2 turn cd)*"; color = 0xE74C3C
            elif ritual_choice == "blood_regen":
                if attacker.sp < 25:
                    attacker.sp += actual_cost
                    msg += f"🌹 **Blood Regen** — need at least 25 SP!"; color = 0x95A5A6
                    cd_blocked = True
                else:
                    rr_heal = seq_dmg(attacker, 0.150)
                    attacker.sp = max(0, attacker.sp - 25)
                    attacker.hp = min(attacker.max_hp, attacker.hp + rr_heal)
                    msg += f"🌹 **Blood Regen!** Restored **{rr_heal} HP** at cost of **25 SP**! *(2 turn cd)*"; color = 0x2ECC71

        elif ability_key == "polymath":
            if defender.role_ability:
                attacker.polymath_ability = defender.role_ability
                attacker.polymath_cooldown = 2
                info_poly, _ = get_ability_info(defender.role_ability)
                pname = info_poly["name"] if info_poly else defender.role_ability
                msg += f"📚 **Polymath!** Studied **{pname}** — can now use it at **70% potency** via Pathway! *(2 turn cd)*"; color = 0x1ABC9C
            else:
                attacker.sp += actual_cost
                msg += f"📚 **Polymath** — opponent has no role ability to study!"; color = 0x95A5A6

        elif ability_key == "pacification":
            attacker.soul_assurer_cooldown = 6
            if ritual_choice == "strip_enemy":
                # Clear all positive effects from opponent
                stripped = []
                if defender.strength_boost > 0:       defender.strength_boost = 0;              stripped.append("strength boost")
                if defender.invigorated:               defender.invigorated = False;             stripped.append("invigorated")
                if defender.prayer_active:             defender.prayer_active = False;           stripped.append("prayer")
                if defender.night_boost_active:        defender.night_boost_active = False; defender.night_boost_damage = 0; stripped.append("night boost")
                if defender.phasing_active:            defender.phasing_active = False;          stripped.append("phasing")
                if defender.planting_turns > 0:        defender.planting_turns = 0;              stripped.append("planting")
                if defender.spectate_active:           defender.spectate_active = False;         stripped.append("spectate")
                if defender.arbitration_boost > 0:    defender.arbitration_boost = 0;           stripped.append("arbitration boost")
                if defender.combat_studies_active:     defender.combat_studies_active = False;   stripped.append("combat studies")
                if defender.folk_of_rage_active:       defender.folk_of_rage_active = False; defender.folk_of_rage_pct = 0; stripped.append("power up punch")
                if defender.animal_companion_active:   defender.animal_companion_active = False; stripped.append("animal companion")
                if defender.computing_active:          defender.computing_active = False;        stripped.append("computing")
                if defender.paper_figurine_turns > 0: defender.paper_figurine_turns = 0;        stripped.append("paper figurine")
                if defender.astrology_active:          defender.astrology_active = False;        stripped.append("astrology")
                if defender.holy_water_regen > 0:     defender.holy_water_regen = 0;            stripped.append("holy water")
                if defender.appraisal_active:          defender.appraisal_active = False;        stripped.append("appraisal")
                if defender.damage_boost_pct > 0:     defender.damage_boost_pct = 0.0;          stripped.append("damage boost")
                if defender.dawn_paladin_active:
                    defender.dawn_paladin_active = False
                    hp_remove = defender.dawn_paladin_hp_bonus
                    defender.max_hp = max(1, defender.max_hp - hp_remove)
                    defender.hp = min(defender.max_hp, defender.hp)
                    defender.dawn_paladin_hp_bonus = 0
                    stripped.append("dawn paladin")
                if defender.notary_buff_turns > 0:    defender.notary_buff_turns = 0;           stripped.append("notary buff")
                if stripped:
                    msg += f"☮️ **Pacification!** Stripped from **{defender.user.display_name}**: **{', '.join(stripped)}**! *(6 turn cd)*"; color = 0x4A148C
                else:
                    attacker.sp += actual_cost
                    attacker.soul_assurer_cooldown = 0
                    msg += f"☮️ **Pacification** — **{defender.user.display_name}** has no active buffs to strip!"; color = 0x95A5A6
            elif ritual_choice == "cleanse_self":
                # Clear all negative effects from self
                cleared = []
                if attacker.bleed > 0:             attacker.bleed = 0;                       cleared.append("bleed")
                if attacker.gun_shot_bleed:         attacker.gun_shot_bleed = False;          cleared.append("gunshot bleed")
                if attacker.paralysis > 0:          attacker.paralysis = 0;                   cleared.append("paralysis")
                if attacker.slumber > 0:            attacker.slumber = 0;                     cleared.append("sleep")
                if attacker.freeze > 0:             attacker.freeze = 0;                      cleared.append("freeze")
                if attacker.freeze_weakened:        attacker.freeze_weakened = False;         cleared.append("freeze weakened")
                if attacker.burn > 0:               attacker.burn = 0;                        cleared.append("burn")
                if attacker.defence_reduction > 0:  attacker.defence_reduction = 0;          cleared.append("defence reduction")
                if attacker.moral_freedom_active:   attacker.moral_freedom_active = False;    cleared.append("moral freedom")
                if attacker.debuff_stacks > 0:      attacker.debuff_stacks = 0;              cleared.append("debuff stacks")
                if attacker.gun_shot_bleed:         attacker.gun_shot_bleed = False;          cleared.append("gunshot bleed")
                if attacker.charm_turns > 0:        attacker.charm_turns = 0; attacker.charm_chance = 0.0; cleared.append("charm")
                if attacker.notary_debuff_turns > 0: attacker.notary_debuff_turns = 0;       cleared.append("notary debuff")
                if attacker.scroll_status:          attacker.scroll_status = None;            cleared.append("scroll affliction")
                if attacker.trapped > 0:            attacker.trapped = 0;                     cleared.append("trapped")
                if attacker.sneak_attack_pending:   attacker.sneak_attack_pending = False;    cleared.append("sneak attack")
                if cleared:
                    msg += f"☮️ **Pacification!** Cleansed from **{attacker.user.display_name}**: **{', '.join(cleared)}**! *(6 turn cd)*"; color = 0x4A148C
                else:
                    attacker.sp += actual_cost
                    attacker.soul_assurer_cooldown = 0
                    msg += f"☮️ **Pacification** — nothing to cleanse!"; color = 0x95A5A6

        elif ability_key == "spirit_guide":
            if attacker.spirit_guide_active:
                attacker.spirit_guide_active = False
                attacker.spirit_guide_turns = 0
                attacker.spirit_guide_cooldown = 7
                msg += f"👻 **Spirit Guide** dismissed — **7 turn cooldown** begins."; color = 0x8E44AD
            else:
                attacker.spirit_guide_active = True
                attacker.spirit_guide_turns = 0
                sg_sp_preview = max(5, int(attacker.max_sp * 0.06))
                msg += f"👻 **Spirit Guide** summoned — strikes each turn (~9% seq HP damage), costs **{sg_sp_preview} SP/turn**. 30% chance to possess you! Lasts **5 turns**. *(7 turn cd after)*"; color = 0x8E44AD

        elif ability_key == "hurricane_of_light":
            if attacker.hurricane_cooldown > 0:
                attacker.sp += actual_cost
                msg += f"🌪️ **Hurricane of Light** on cooldown for **{attacker.hurricane_cooldown}** more turn(s)!"; color = 0x95A5A6
                cd_blocked = True
            elif attacker.hurricane_interrupt_cd > 0:
                attacker.sp += actual_cost
                msg += f"🌪️ **Hurricane of Light** interrupted — sword must be re-planted in **{attacker.hurricane_interrupt_cd}** turn(s)!"; color = 0x95A5A6
                cd_blocked = True
            elif attacker.hurricane_uses >= 3:
                attacker.sp += actual_cost
                msg += f"🌪️ **Hurricane of Light** — all 3 uses exhausted!"; color = 0x95A5A6
                cd_blocked = True
            elif not attacker.hurricane_charging:
                attacker.hurricane_charging = True
                attacker.sp += actual_cost
                msg += f"🌪️ **Hurricane of Light** — sword driven into the ground, light gathering... *(releases automatically next turn)*"; color = 0xF1C40F
            else:
                attacker.hurricane_charging = False
                attacker.hurricane_uses += 1
                attacker.hurricane_cooldown = 12
                avg_base = (attacker.base_hp + defender.base_hp) // 2
                pct = random.uniform(0.35, 0.40)
                raw_dmg = int(avg_base * pct)
                # Evasion abilities (distortion, phasing) only mitigate 50% of hurricane damage
                if defender.distortion_active:
                    defender.distortion_active = False
                    raw_dmg = max(1, raw_dmg // 2)
                    msg += f"👑 **{defender.user.display_name}** distorts — hurricane damage halved!\n"
                elif defender.phasing_active:
                    defender.phasing_active = False
                    defender.phasing_cooldown = 1
                    raw_dmg = max(1, raw_dmg // 2)
                    msg += f"👻 **{defender.user.display_name}** phases — hurricane damage halved!\n"
                actual_dmg = defender.apply_damage(int(raw_dmg * attacker.get_attack_mult()), is_ability=True)
                backlash = int(actual_dmg * 0.30)
                attacker.hp = max(0, attacker.hp - backlash)
                msg += f"🌪️ **HURRICANE OF LIGHT!** A massive hurricane of light tears through! **{actual_dmg}** damage dealt — **{backlash}** backlash to you! *(use {attacker.hurricane_uses}/3, 12 turn cd)*"; color = 0xF5D020

        elif ability_key == "pleasure_witch":
            if defender.charm_turns > 0:
                attacker.sp += actual_cost
                msg += f"💋 **Pleasure Witch** — opponent already charmed!"; color = 0x95A5A6
                cd_blocked = True
            else:
                defender.charm_turns = 1
                defender.charm_chance = 0.40
                attacker.pleasure_witch_cooldown = 3
                msg += f"💋 **Pleasure Witch!** **{defender.user.display_name}** is charmed — skips next turn! *(40% linger chance, −10%/round, 3 turn cd)*"; color = 0xE91E8C

        elif ability_key == "conspiracy":
            allowed_gap, gap = seq_gap_check(attacker, defender, max_gap=2)
            if not allowed_gap:
                attacker.sp += actual_cost
                msg += f"🕸️ **Conspiracy failed!** The web cannot ensnare a Beyonder {gap} sequences above you."; color = 0x95A5A6
                cd_blocked = True
            if attacker.conspirer_cooldown > 0:
                attacker.sp += actual_cost
                msg += f"🕸️ **Conspiracy** on cooldown for **{attacker.conspirer_cooldown}** more turn(s)!"; color = 0x95A5A6
                cd_blocked = True
            else:
                attacker.conspirer_cooldown = 5
                parts = []

                # Guarantee at least one effect always lands by forcing the guaranteed one first
                CONSPIRACY_EFFECTS = ["miss", "force", "damage"]
                guaranteed = random.choice(CONSPIRACY_EFFECTS)
                remaining = [e for e in CONSPIRACY_EFFECTS if e != guaranteed]

                def apply_conspiracy_effect(effect):
                    if effect == "miss":
                        defender.conspiracy_miss_turns = 3
                        parts.append("🎯 **33% miss** applied for 3 rounds")
                    elif effect == "force":
                        defender.conspiracy_force_turns = 3
                        parts.append("🌀 **forced random ability** for 3 rounds")
                    elif effect == "damage":
                        actual_dmg = defender.apply_damage(40, is_ability=True)
                        sp_drained = min(60, defender.sp)
                        defender.sp = max(0, defender.sp - sp_drained)
                        parts.append(f"💥 **{actual_dmg} damage** + **{sp_drained} SP** drained")

                # Guaranteed hit
                apply_conspiracy_effect(guaranteed)
                # Other two each still roll at 33%
                for effect in remaining:
                    if random.random() < 0.33:
                        apply_conspiracy_effect(effect)

                msg += f"🕸️ **Conspiracy!** The web closes in:\n" + "\n".join(f"> {p}" for p in parts); color = 0x8E44AD

        elif ability_key == "scrolls_professor":
            statuses = ["freeze", "bleed", "burn", "paralysis", "slumber"]
            chosen = random.choice(statuses)
            attacker.scroll_status = chosen
            applied = defender.apply_status_effect(chosen, 1)
            attacker.scrolls_professor_cooldown = 4
            if applied:
                msg += f"📜 **Scrolls Professor!** Scroll afflicts **{defender.user.display_name}** with **{chosen}**! Rolls 20% chance each turn to re-apply. *(4 turn cd)*"; color = 0x8E44AD
            else:
                msg += f"📜 **Scrolls Professor!** Scroll chose **{chosen}** but was resisted. Still rolls 20%/turn. *(4 turn cd)*"; color = 0x95A5A6

        elif ability_key == "artisan":
            attacker.artisan_cooldown = 2
            roll = random.randint(1, 3)
            if roll == 1:
                # Bomb — 50 damage
                actual_dmg = apply_ability_damage(attacker, defender, 0.227, skill_key="artisan")
                msg += f"⚗️ **Artisan — Bomb!** Crafted from the materials around you — **{actual_dmg}** explosive damage! *(2 turn cd)*"; color = 0xE74C3C
            elif roll == 2:
                # Sword — 40 damage
                actual_dmg = apply_ability_damage(attacker, defender, 0.182, skill_key="artisan")
                msg += f"⚗️ **Artisan — Sword!** Forged a blade from the surroundings — **{actual_dmg}** damage! *(2 turn cd)*"; color = 0xF39C12
            else:
                # Rifle — 35 damage + bleed
                actual_dmg = apply_ability_damage(attacker, defender, 0.159, skill_key="artisan")
                applied = defender.apply_status_effect("bleed", 3)
                bleed_txt = " + bleed 3 turns" if applied else ""
                msg += f"⚗️ **Artisan — Rifle!** Assembled a firearm — **{actual_dmg}** damage{bleed_txt}! *(2 turn cd)*"; color = 0xE67E22

        elif ability_key == "calamity_priest":
            roll = random.random()
            if roll < 0.50:
                share_attacker, share_defender = 50, 50
            elif roll < 0.90:
                share_attacker, share_defender = 40, 60
            elif roll < 0.95:
                share_attacker, share_defender = 30, 70
            else:
                share_attacker, share_defender = 20, 80
            total_dmg = seq_dmg(attacker, 0.455)
            self_dmg = int(total_dmg * share_attacker / 100)
            opp_dmg = int(total_dmg * share_defender / 100)
            attacker.hp = max(0, attacker.hp - self_dmg)
            actual_opp = defender.apply_damage(int(opp_dmg * attacker.get_attack_mult()), is_ability=True)
            attacker.calamity_priest_cooldown = 3
            msg += f"☄️ **Calamity Priest!** Calamity shared — **{self_dmg}** to you, **{actual_opp}** to **{defender.user.display_name}**! *({share_attacker}/{share_defender} split)* *(3 turn cd)*"; color = 0xFFA000

        elif ability_key == "potions_professor":
            attacker.potions_professor_cooldown = 3
            if ritual_choice == "cure":
                if attacker.bleed > 0 or attacker.burn > 0 or attacker.paralysis > 0 or attacker.freeze > 0 or attacker.slumber > 0:
                    # Has status — lesser heal + remove one status
                    for attr in ["bleed", "burn", "paralysis", "freeze", "slumber"]:
                        if getattr(attacker, attr, 0) > 0:
                            setattr(attacker, attr, 0)
                            break
                    pot_hp_s = max(1, int(attacker.max_hp * 0.05))
                    pot_sp_s = max(1, int(attacker.max_sp * 0.04))
                    attacker.hp = min(attacker.max_hp, attacker.hp + pot_hp_s)
                    attacker.sp = min(attacker.max_sp, attacker.sp + pot_sp_s)
                    msg += f"🧪 **Cure!** Removed a status effect + restored **{pot_hp_s} HP + {pot_sp_s} SP**! *(3 turn cd)*"; color = 0x2ECC71
                else:
                    pot_hp = max(1, int(attacker.max_hp * 0.15))
                    pot_sp = max(1, int(attacker.max_sp * 0.10))
                    attacker.hp = min(attacker.max_hp, attacker.hp + pot_hp)
                    attacker.sp = min(attacker.max_sp, attacker.sp + pot_sp)
                    msg += f"🧪 **Cure!** Restored **{pot_hp} HP + {pot_sp} SP**! *(3 turn cd)*"; color = 0x2ECC71
            elif ritual_choice == "poison":
                actual_dmg = apply_ability_damage(attacker, defender, 0.227, is_ability=True)
                sp_drain = min(seq_val(attacker, 20), defender.sp)
                defender.sp = max(0, defender.sp - sp_drain)
                msg += f"☠️ **Poison!** **{actual_dmg}** damage + drained **{sp_drain} SP** from **{defender.user.display_name}**! *(3 turn cd)*"; color = 0xE74C3C

        elif ability_key == "biologist":
            attacker.biologist_cooldown = 3
            roll = random.randint(1, 2)
            if roll == 1:
                # Double status: paralysis + bleed
                a1 = defender.apply_status_effect("paralysis", 2)
                a2 = defender.apply_status_effect("bleed", 2)
                applied_names = []
                if a1: applied_names.append("paralysis")
                if a2: applied_names.append("bleed")
                msg += f"🧬 **Biologist!** Crossbred afflictions — **{', '.join(applied_names) if applied_names else 'resisted'}** on **{defender.user.display_name}**! *(3 turn cd)*"; color = 0x8E44AD
            else:
                actual_dmg = apply_ability_damage(attacker, defender, 0.163, is_ability=True)
                msg += f"🧬 **Biologist!** Summoned creature — **{actual_dmg}** damage! *(3 turn cd)*"; color = 0xE74C3C

        elif ability_key == "zombie":
            attacker.zombie_cooldown = 3
            roll = random.random()
            if roll < 0.60:
                # 1–4 zombies (60% chance), each deals 10 damage
                count = random.randint(1, 4)
                total_base = count * seq_dmg(attacker, 0.045)
                actual_dmg = defender.apply_damage(int(total_base * attacker.get_attack_mult()), is_ability=True)
                flinch_txt = ""
                if random.random() < 0.10:
                    applied = defender.apply_status_effect("paralysis", 1)
                    flinch_txt = " + flinch!" if applied else ""
                msg += f"🧟 **Zombie!** **{count}** zombie(s) summoned — **{actual_dmg}** damage{flinch_txt}! *(3 turn cd)*"; color = 0x4A148C
            else:
                # 5–7 zombies (40% chance), each deals 10 damage
                count = random.randint(5, 7)
                total_base = count * seq_dmg(attacker, 0.045)
                actual_dmg = defender.apply_damage(int(total_base * attacker.get_attack_mult()), is_ability=True)
                flinch_txt = ""
                if random.random() < 0.10:
                    applied = defender.apply_status_effect("paralysis", 1)
                    flinch_txt = " + flinch!" if applied else ""
                msg += f"🧟 **Zombie Horde!** **{count}** zombies — **{actual_dmg}** damage{flinch_txt}! *(3 turn cd)*"; color = 0x2C3E50

        elif ability_key == "devil_form":
            if attacker.devil_form_active:
                # Cancel
                attacker.devil_form_active = False
                hp_remove = attacker.devil_form_hp_bonus
                attacker.max_hp = max(1, attacker.max_hp - hp_remove)
                attacker.hp = min(attacker.max_hp, attacker.hp)
                attacker.devil_form_hp_bonus = 0
                sp_remove = attacker.devil_form_sp_bonus
                attacker.max_sp = max(1, attacker.max_sp - sp_remove)
                attacker.sp = min(attacker.max_sp, attacker.sp)
                attacker.devil_form_sp_bonus = 0
                attacker.damage_boost_pct = max(0.0, attacker.damage_boost_pct - 0.50)
                attacker.devil_form_cooldown = 4
                msg += f"😈 **Devil Form** released — **4 turn cooldown** begins."; color = 0xF39C12
            else:
                hp_bonus = int(attacker.max_hp * 0.50)
                attacker.devil_form_hp_bonus = hp_bonus
                attacker.max_hp += hp_bonus
                attacker.hp = min(attacker.max_hp, attacker.hp + hp_bonus)
                sp_bonus = int(attacker.max_sp * 0.50)
                attacker.devil_form_sp_bonus = sp_bonus
                attacker.max_sp += sp_bonus
                attacker.damage_boost_pct += 0.50
                attacker.devil_form_active = True
                msg += f"😈 **Devil Form!** Transformed — **+50% HP, SP, and damage**! 30% chance to lose turn to madness. Use again to cancel."; color = 0xE74C3C

        elif ability_key == "distortion":
            attacker.distortion_active = True
            attacker.distortion_cooldown = 3
            attacker.distortion_concealed = True  # conceal next action from opponent
            msg += f"👑 **Distortion!** **{attacker.user.display_name}** distorts reality — next incoming attack misses + next action concealed! *(3t cd)*"; color = 0x8E44AD

        elif ability_key == "judge":
            # Random verdict — no choice, fate decides
            verdict = random.choices(
                ["imprisonment", "flogging", "death"],
                weights=[33, 33, 34]
            )[0]
            attacker.judge_cooldown = 2

            if verdict == "imprisonment":
                # Imprisonment: trap opponent for 3 turns, costs them 25 SP
                defender.trapped = max(defender.trapped, 3)
                sp_drain = min(25, defender.sp)
                defender.sp = max(0, defender.sp - sp_drain)
                attacker.judge_cooldown = 7  # 4 + 3 turn cooldown
                msg += (f"⚖️ **Judge — Imprisonment!** The verdict is delivered — "
                        f"**{defender.user.display_name}** is imprisoned for **3 turns** "
                        f"and loses **{sp_drain} SP**! *(7 turn cd)*"); color = 0x1565C0

            elif verdict == "flogging":
                # Flogging: 15% of defender's current HP as damage
                dmg = max(1, int(defender.hp * 0.15))
                actual_dmg = defender.apply_damage(int(dmg * attacker.get_attack_mult()), is_ability=True)
                msg += (f"⚖️ **Judge — Flogging!** The whip cracks — **{actual_dmg}** damage "
                        f"(**15%** of **{defender.user.display_name}**'s current HP)! *(2 turn cd)*"); color = 0x1565C0

            elif verdict == "death":
                # Death: 40% flat dmg | 35% 50% HP | 25% miss
                roll = random.random()
                if roll < 0.40:
                    dmg = random.randint(seq_dmg(attacker, 0.273), seq_dmg(attacker, 0.409))
                    actual_dmg = defender.apply_damage(int(dmg * attacker.get_attack_mult()), is_ability=True)
                    msg += (f"⚖️ **Judge — Death Sentence!** ☠️ Sentence carried out — "
                            f"**{actual_dmg}** flat damage! *(2 turn cd)*"); color = 0xE74C3C
                elif roll < 0.75:
                    dmg = max(1, int(defender.hp * 0.50))
                    actual_dmg = defender.apply_damage(int(dmg * attacker.get_attack_mult()), is_ability=True)
                    msg += (f"⚖️ **Judge — Death Sentence!** ☠️ Half their life taken — "
                            f"**{actual_dmg}** damage (**50%** of current HP)! *(2 turn cd)*"); color = 0xE74C3C
                else:
                    attacker.sp += actual_cost
                    attacker.judge_cooldown = 0
                    msg += (f"⚖️ **Judge — Death Sentence!** The executioner hesitates — "
                            f"**{defender.user.display_name}** escapes unscathed! *(no cd)*"); color = 0x95A5A6

        # ── Seq 5 Role abilities ──────────────────────────

        elif ability_key == "spirit_thread":
            if attacker.spirit_thread_active:
                attacker.sp += actual_cost
                msg += f"🧵 **Spirit Thread** already active!"; cd_blocked = True
            elif attacker.spirit_thread_cooldown > 0:
                attacker.sp += actual_cost
                msg += f"🧵 **Spirit Thread** on cooldown for **{attacker.spirit_thread_cooldown}** turn(s)!"; cd_blocked = True
            else:
                attacker.spirit_thread_active = True
                attacker.spirit_thread_turns = 0
                attacker.spirit_thread_stun_pct = 0
                msg += (f"🧵 **Spirit Thread Control!** **{attacker.user.display_name}** seizes **{defender.user.display_name}**'s spirit threads! "
                        f"10% stun chance this turn, growing +10% each round. Broken by critical hit or special. *(5 turn cd)*"); color = 0x9B59B6

        elif ability_key == "marionette_summon":
            if attacker.marionette_active:
                attacker.sp += actual_cost
                msg += f"🎭 **Marionette** already active!"; cd_blocked = True
            elif attacker.marionette_cooldown > 0:
                attacker.sp += actual_cost
                msg += f"🎭 **Marionette** on cooldown for **{attacker.marionette_cooldown}** turn(s)!"; cd_blocked = True
            else:
                attacker.marionette_active = True
                attacker.marionette_turns = 3
                attacker.marionette_cooldown = 3
                msg += (f"🎭 **Sacrifice Marionette!** A marionette is summoned — each turn **50%** chance it attacks opponent or **50%** chance it intercepts an attack! "
                        f"Lasts **3 turns**. *(3 turn cd)*"); color = 0x8E44AD

        elif ability_key == "mental_theft":
            allowed_gap, gap = seq_gap_check(attacker, defender, max_gap=2)
            if not allowed_gap:
                attacker.sp += actual_cost
                msg += f"🧠 **Mental Theft failed!** Cannot pierce the mind of a Beyonder {gap} sequences stronger."; color = 0x95A5A6
                cd_blocked = True
            if attacker.mental_theft_cooldown > 0:
                attacker.sp += actual_cost
                msg += f"🧠 **Mental Theft** on cooldown for **{attacker.mental_theft_cooldown}** turn(s)!"; cd_blocked = True
            else:
                attacker.mental_theft_cooldown = 3
                defender.sneak_attack_pending = True
                msg += (f"🧠 **Mental Theft!** **{attacker.user.display_name}** removes the thought of attack from "
                        f"**{defender.user.display_name}**'s mind — they lose their next turn! *(3 turn cd)*"); color = 0x9B59B6

        elif ability_key == "rewards_theft":
            if attacker.rewards_theft_cooldown > 0:
                attacker.sp += actual_cost
                msg += f"🏆 **Rewards Theft** on cooldown for **{attacker.rewards_theft_cooldown}** turn(s)!"; cd_blocked = True
            else:
                # Try to steal a buff from defender
                stolen = None
                if defender.notary_buff_turns > 0:
                    attacker.notary_buff_turns = max(attacker.notary_buff_turns, defender.notary_buff_turns)
                    defender.notary_buff_turns = 0
                    stolen = "Notary Buff (+50% dmg)"
                elif defender.domination_active:
                    attacker.domination_active = True
                    attacker.domination_turns = defender.domination_turns
                    defender.domination_active = False
                    stolen = "Domination"
                elif defender.invigorated:
                    attacker.invigorated = True
                    defender.invigorated = False
                    stolen = "Invigorated"
                elif defender.appraisal_active:
                    attacker.appraisal_active = True
                    defender.appraisal_active = False
                    stolen = "Appraisal (+40%)"
                elif defender.damage_boost_pct > 0:
                    attacker.damage_boost_pct += defender.damage_boost_pct * 0.5
                    defender.damage_boost_pct = 0
                    stolen = "Damage Boost"
                elif defender.artificial_moon_turns > 0:
                    attacker.artificial_moon_turns = max(attacker.artificial_moon_turns, defender.artificial_moon_turns)
                    defender.artificial_moon_turns = 0
                    stolen = "Artificial Moon"
                elif defender.singing_enlightened_turns > 0:
                    attacker.singing_enlightened_turns = defender.singing_enlightened_turns
                    attacker.singing_str_boost = defender.singing_str_boost
                    defender.singing_enlightened_turns = 0
                    defender.singing_str_boost = 0
                    stolen = "Vocal Enlightenment"
                if stolen:
                    attacker.rewards_theft_cooldown = 4
                    msg += (f"🏆 **Rewards Theft!** **{attacker.user.display_name}** steals "
                            f"**{stolen}** from **{defender.user.display_name}**! *(4 turn cd)*"); color = 0xF39C12
                else:
                    attacker.sp += actual_cost
                    msg += f"🏆 **Rewards Theft** — nothing to steal!"; cd_blocked = True

        elif ability_key == "blink":
            if attacker.blink_cooldown > 0:
                attacker.sp += actual_cost
                msg += f"⚡ **Blink** on cooldown for **{attacker.blink_cooldown}** turn(s)!"; cd_blocked = True
            else:
                attacker.blink_cooldown = 6
                attacker.blink_stun_immune_turns = 3
                # Clear ALL stun types
                escaped = []
                if attacker.paralysis > 0:          attacker.paralysis = 0;          escaped.append("paralysis")
                if attacker.slumber > 0:            attacker.slumber = 0;            escaped.append("sleep")
                if attacker.freeze > 0:             attacker.freeze = 0;             escaped.append("freeze")
                if attacker.trapped > 0:            attacker.trapped = 0;            escaped.append("trap")
                if attacker.sneak_attack_pending:   attacker.sneak_attack_pending = False; escaped.append("sneak attack")
                if attacker.charm_turns > 0:        attacker.charm_turns = 0;        escaped.append("charm")
                escape_txt = f" Escaped: **{', '.join(escaped)}**." if escaped else ""
                surprise_dmg = apply_ability_damage(attacker, defender, 0.190, is_ability=True)
                # Override the "fights through X to use cleanse" message — blink has its own
                msg = msg.replace(
                    f"💥 **{attacker.user.display_name}** fights through the {msg.split('fights through the ')[-1].split(' to use')[0] if 'fights through' in msg else ''} to use their cleanse!\n",
                    ""
                )
                msg += (f"⚡ **Blink!**{escape_txt} **{attacker.user.display_name}** is stun-immune for **3 turns** and "
                        f"surprise-strikes **{defender.user.display_name}** for **{surprise_dmg}** damage! *(6 turn cd)*"); color = 0x3498DB

        elif ability_key == "travellers_door":
            if attacker.travellers_door_cooldown > 0:
                attacker.sp += actual_cost
                msg += f"🚪 **Traveller's Door** on cooldown for **{attacker.travellers_door_cooldown}** turn(s)!"; cd_blocked = True
            elif attacker.travellers_door_uses <= 0:
                attacker.sp += actual_cost
                msg += f"🚪 **Traveller's Door** — no uses remaining!"; cd_blocked = True
            else:
                attacker.travellers_door_uses -= 1
                attacker.travellers_door_cooldown = 6
                # Full resource regen
                attacker.sp = attacker.max_sp
                attacker.hp = min(attacker.max_hp, attacker.hp + int(attacker.max_hp * 0.30))
                # Clear all debuffs on self
                attacker.bleed = 0; attacker.burn = 0; attacker.paralysis = 0
                attacker.slumber = 0; attacker.freeze = 0
                env_dmg = 0
                if random.random() < 0.10:
                    env_dmg = apply_ability_damage(attacker, defender, 0.048)
                env_txt = f" + **{env_dmg}** environmental damage!" if env_dmg else ""
                msg += (f"🚪 **Traveller's Door!** **{defender.user.display_name}** is flung to a remote location! "
                        f"**{attacker.user.display_name}** fully recuperates (SP restored, +30% HP, debuffs cleared){env_txt}. "
                        f"*({attacker.travellers_door_uses} use(s) left, 6 turn cd)*"); color = 0x2ECC71

        elif ability_key == "dream_visitation":
            allowed_gap, gap = seq_gap_check(attacker, defender, max_gap=3)
            if not allowed_gap:
                attacker.sp += actual_cost
                msg += f"💭 **Dream Visitation failed!** **{defender.user.display_name}**'s mind is too strong to enter. (gap: {gap})"; color = 0x95A5A6
                cd_blocked = True
            if attacker.dream_visitation_cooldown > 0:
                attacker.sp += actual_cost
                msg += f"🌙 **Dream Visitation** on cooldown for **{attacker.dream_visitation_cooldown}** turn(s)!"; cd_blocked = True
            else:
                attacker.dream_visitation_cooldown = 4
                if random.random() < 0.50:
                    # Force flee or SP drain — SP drain for bot fairness
                    sp_drained = min(int(defender.max_sp * 0.40), defender.sp)
                    defender.sp = max(0, defender.sp - sp_drained)
                    msg += (f"🌙 **Dream Visitation!** **{attacker.user.display_name}** enters the dreams of "
                            f"**{defender.user.display_name}** — their secrets are exposed! Drained **{sp_drained} SP** "
                            f"(**40%** of max SP)! *(4 turn cd)*"); color = 0x9B59B6
                else:
                    msg += (f"🌙 **Dream Visitation** — **{defender.user.display_name}**'s mind resists the intrusion! *(4 turn cd)*"); color = 0x95A5A6

        elif ability_key == "dream_alteration":
            if attacker.dream_alteration_used:
                attacker.sp += actual_cost
                msg += f"💭 **Dream Alteration** already used!"; cd_blocked = True
            else:
                attacker.dream_alteration_used = True
                defender.dream_alteration_acc_loss = 0.05  # Start with 5%
                msg += (f"💭 **Dream Alteration!** **{attacker.user.display_name}** corrupts **{defender.user.display_name}**'s thoughts — "
                        f"accuracy drops **5% every round** for the rest of battle! *(one use)*"); color = 0x9B59B6

        elif ability_key == "purification_halo":
            if attacker.purification_halo_cooldown > 0:
                attacker.sp += actual_cost
                msg += f"☀️ **Purification Halo** on cooldown for **{attacker.purification_halo_cooldown}** turn(s)!"; cd_blocked = True
            else:
                attacker.purification_halo_cooldown = 5
                attacker.purification_halo_sp_boost_turns = 3
                sp_boost = max(5, int(attacker.max_sp * 0.15))
                attacker.sp = min(attacker.max_sp, attacker.sp + sp_boost)
                def_pathway = get_pathway_name(defender.role) if defender.role else ""
                halo_dmg = 0
                burn_turns = 0
                if def_pathway == "Death":
                    halo_dmg = 50; burn_turns = 5
                elif def_pathway == "Abyss":
                    halo_dmg = 100; burn_turns = 5
                elif def_pathway == "Chained":
                    halo_dmg = 150; burn_turns = 5
                if halo_dmg > 0:
                    actual_dmg = apply_ability_damage(attacker, defender, halo_dmg / SEQ_AVG_HP.get(get_seq_number(attacker.role) if attacker.role else 9, 420))
                    defender.apply_status_effect("burn", 5)
                    msg += (f"☀️ **Purification Halo!** A holy sun halo erupts — **{defender.user.display_name}** ({def_pathway} pathway) "
                            f"suffers **{actual_dmg}** holy damage + **5-round burn**! **{attacker.user.display_name}** gains **+{sp_boost} SP**. *(5 turn cd)*"); color = 0xFFD700
                else:
                    msg += (f"☀️ **Purification Halo!** A radiant sun halo bathes the area in holy light — "
                            f"**{attacker.user.display_name}** gains **+{sp_boost} SP** and an SP surge for **3 turns**! *(5 turn cd)*"); color = 0xFFD700

        elif ability_key == "light_of_holiness":
            if attacker.light_of_holiness_cooldown > 0:
                attacker.sp += actual_cost
                msg += f"✨ **Light of Holiness** on cooldown for **{attacker.light_of_holiness_cooldown}** turn(s)!"; cd_blocked = True
            else:
                attacker.light_of_holiness_cooldown = 4
                def_pathway = get_pathway_name(defender.role) if defender.role else ""
                base_dmg = 70
                if def_pathway == "Chained":
                    base_dmg = 140
                actual_dmg = apply_ability_damage(attacker, defender, base_dmg / SEQ_AVG_HP.get(get_seq_number(attacker.role) if attacker.role else 9, 420))
                defender.apply_status_effect("burn", 2)
                defender.sneak_attack_pending = True  # force turn skip
                # Revoke conditional curses
                if def_pathway in ("Abyss", "Death"):
                    defender.scroll_status = None
                    defender.conspiracy_miss_turns = 0
                chain_txt = " (**doubled** — Chained pathway!)" if def_pathway == "Chained" else ""
                msg += (f"✨ **Light of Holiness!** A colossal ray of holy light descends — "
                        f"**{actual_dmg}** damage{chain_txt} + **2-round burn** + **{defender.user.display_name}** loses next turn! *(4 turn cd)*"); color = 0xFFD700

        elif ability_key == "singing":
            if attacker.singing_cooldown > 0:
                attacker.sp += actual_cost
                msg += f"🎵 **Singing** on cooldown for **{attacker.singing_cooldown}** turn(s)!"; cd_blocked = True
            else:
                attacker.singing_cooldown = 3
                roll = random.randint(1, 3)
                if roll == 1:
                    # Spirit Body Interference
                    applied = defender.apply_status_effect("paralysis", 2)
                    sp_strip = min(seq_val(attacker, 30), defender.sp)
                    defender.sp = max(0, defender.sp - sp_strip)
                    stun_txt = " + stunned 2 turns" if applied else " (stun resisted)"
                    msg += (f"🎵 **Singing — Spirit Interference!** A terrible singing voice disrupts **{defender.user.display_name}**'s spirit body — "
                            f"**{sp_strip} SP** drained{stun_txt}! *(3 turn cd)*"); color = 0x9B59B6
                elif roll == 2:
                    # Vocal Enlightenment
                    attacker.singing_enlightened_turns = 3
                    attacker.singing_str_boost = 0.20
                    attacker.damage_boost_pct += 0.20
                    msg += (f"🎵 **Singing — Vocal Enlightenment!** A magnificent performance empowers **{attacker.user.display_name}** — "
                            f"**+15% spirit** and **+20% strength** for **3 turns**! *(3 turn cd)*"); color = 0xF39C12
                else:
                    # Sound-Wave Explosion
                    actual_dmg = apply_ability_damage(attacker, defender, 0.095)
                    defender.apply_status_effect("paralysis", 1)
                    msg += (f"🎵 **Singing — Sound-Wave Explosion!** A devastating shockwave blasts **{defender.user.display_name}** for "
                            f"**{actual_dmg}** damage + stun 1 turn! *(3 turn cd)*"); color = 0xE74C3C

        elif ability_key == "arrow_of_lightning":
            if attacker.arrow_charging:
                # Fire the arrow
                attacker.arrow_charging = False
                attacker.arrow_cooldown = 5
                actual_dmg = apply_ability_damage(attacker, defender, 0.214, is_ability=True)  # ~90 at seq5
                msg += (f"⚡ **Arrow of Lightning FIRES!** A bolt faster than sight strikes **{defender.user.display_name}** for "
                        f"**{actual_dmg}** damage! *(5 turn cd)*"); color = 0xFFD700
            elif attacker.arrow_cooldown > 0:
                attacker.sp += actual_cost
                msg += f"⚡ **Arrow of Lightning** on cooldown for **{attacker.arrow_cooldown}** turn(s)!"; cd_blocked = True
            else:
                attacker.arrow_charging = True
                msg += (f"⚡ **Arrow of Lightning** charging — **{attacker.user.display_name}** draws back the lightning! "
                        f"Will fire automatically next turn for **90 damage**!"); color = 0xF39C12

        elif ability_key == "ability_bag":
            if not attacker.grazing_abilities:
                attacker.sp += actual_cost
                msg += f"🎒 **Ability Bag** — nothing grazed yet! Use **Grazing** first to copy abilities."; cd_blocked = True
            else:
                attacker.sp += actual_cost   # refund — opening the bag doesn't cost SP
                # Show the bag contents as an ephemeral select menu
                bag_entries = []
                for gk in attacker.grazing_abilities:
                    ab_info, _ = get_ability_info(gk)
                    if ab_info:
                        cost = ab_info.get("cost", 0)
                        display_cost = ab_info.get("display_cost", f"{cost} SP")
                        bag_entries.append((gk, ab_info["name"], display_cost))
                if not bag_entries:
                    msg += f"🎒 **Ability Bag** — no valid abilities found."; cd_blocked = True
                else:
                    # Deferred import: battle.views imports process_action from this
                    # module for its button callbacks, so importing it back at module
                    # level here would create a circular import.
                    from battle.views import AbilityBagView
                    view = AbilityBagView(attacker, bag_entries, battle, interaction.channel)
                    names_txt = ", ".join(f"**{n}** ({c})" for _, n, c in bag_entries)
                    await interaction.followup.send(
                        f"🎒 **Ability Bag** — choose an ability to activate:\n{names_txt}",
                        view=view,
                        ephemeral=True
                    )
                    return
            if attacker.grazing_cooldown > 0:
                attacker.sp += actual_cost
                msg += f"🌾 **Grazing** on cooldown for **{attacker.grazing_cooldown}** turn(s)!"; cd_blocked = True
            elif len(attacker.grazing_abilities) >= 5:
                attacker.sp += actual_cost
                msg += f"🌾 **Grazing** — ability bag full (5 max)!"; cd_blocked = True
            else:
                # Copy 3 random abilities from defender's pathway
                attacker.grazing_cooldown = 1
                def_pathway = get_pathway_name(defender.role) if defender.role else ""
                def_seq = get_seq_number(defender.role) if defender.role else 9
                pool = []
                for seq in range(9, def_seq - 1, -1):
                    role_key = next((r for r in ROLE_ABILITIES if f"[{def_pathway}] Seq {seq}" in r), None)
                    if role_key:
                        pool.append(ROLE_ABILITIES[role_key])
                picked = random.sample(pool, min(3, len(pool))) if pool else []
                for ak in picked:
                    if ak not in attacker.grazing_abilities and len(attacker.grazing_abilities) < 5:
                        attacker.grazing_abilities.append(ak)
                if picked:
                    names = [get_ability_info(k)[0]["name"] if get_ability_info(k)[0] else k for k in picked]
                    msg += (f"🌾 **Grazing!** Copied **{len(picked)}** abilities from **{defender.user.display_name}**: "
                            f"**{', '.join(names)}**! *(1 use per round)*"); color = 0x2ECC71
                else:
                    attacker.sp += actual_cost
                    msg += f"🌾 **Grazing** — nothing to copy!"; cd_blocked = True

        elif ability_key == "combination_spell":
            # Activate the combo system — from now on using certain pairs triggers combos
            if attacker.combination_spell_active:
                attacker.sp += actual_cost
                msg += f"🔮 **Combination Spell** already active!"; cd_blocked = True
            else:
                attacker.combination_spell_active = True
                msg += (f"🔮 **Combination Spell!** **{attacker.user.display_name}** unlocks ability fusion — "
                        f"use **Leodero+Debuff**, **Analyse+Ritualistic Reasoning**, "
                        f"**Study+Polymath**, or **Debuff+Analyse** in sequence to unleash combo spells!"); color = 0x9B59B6

        elif ability_key == "spiritual_suppression":
            if attacker.spiritual_suppression_uses <= 0:
                attacker.sp += actual_cost
                msg += f"🦷 **Spiritual Suppression** — no uses remaining!"; cd_blocked = True
            elif attacker.spiritual_suppression_cooldown > 0:
                attacker.sp += actual_cost
                msg += f"🦷 **Spiritual Suppression** on cooldown for **{attacker.spiritual_suppression_cooldown}** turn(s)!"; cd_blocked = True
            else:
                attacker.spiritual_suppression_uses -= 1
                attacker.spiritual_suppression_turns = 2
                attacker.spiritual_suppression_cooldown = 4
                msg += (f"🦷 **Spiritual Suppression!** Spirits locked within teeth surge forth — all of "
                        f"**{defender.user.display_name}**'s abilities costing **>50 SP** are negated for **2 turns**! "
                        f"*({attacker.spiritual_suppression_uses} use(s) left, 4 turn cd)*"); color = 0x4B0082

        elif ability_key == "spiritual_takeover":
            allowed_gap, gap = seq_gap_check(attacker, defender, max_gap=2)
            if not allowed_gap:
                attacker.sp += actual_cost
                msg += f"👁️ **Spiritual Takeover failed!** Cannot seize the spirit of someone {gap} sequences stronger."; color = 0x95A5A6
                cd_blocked = True
            if attacker.spiritual_takeover_used:
                attacker.sp += actual_cost
                msg += f"👻 **Spiritual Takeover** already used!"; cd_blocked = True
            else:
                # Check Sun pathway immunity
                def_pathway = get_pathway_name(defender.role) if defender.role else ""
                def_seq = get_seq_number(defender.role) if defender.role else 9
                if def_pathway == "Sun" and def_seq <= 5:
                    attacker.sp += actual_cost
                    msg += f"👻 **Spiritual Takeover** — Sun pathway Seq 5+ is completely resistant!"; cd_blocked = True
                else:
                    attacker.spiritual_takeover_used = True
                    defender.spiritual_takeover_active = True
                    msg += (f"👻 **Spiritual Takeover!** A corrupted spirit is planted near **{defender.user.display_name}** — "
                            f"they will suffer **15 damage per round** until battle ends! *(1 use only)*"); color = 0x8E44AD

        elif ability_key == "dragged_to_hell":
            if attacker.dragged_to_hell_used:
                attacker.sp += actual_cost
                msg += f"🔥 **Dragged to Hell** already used!"; cd_blocked = True
            else:
                attacker.dragged_to_hell_used = True
                attacker.dragged_to_hell_active = True
                actual_dmg = apply_ability_damage(attacker, defender, 0.167, is_ability=True)  # ~70 at seq5
                msg += (f"🔥 **Dragged to Hell!** The gateway within **{attacker.user.display_name}**'s glabella tears open — "
                        f"**{actual_dmg}** damage to **{defender.user.display_name}**! **{attacker.user.display_name}** drains "
                        f"**15 SP** each round for the rest of the match. *(1 use only)*"); color = 0xE74C3C

        elif ability_key == "evil_sealing":
            if attacker.evil_sealing_active:
                # Fire the stored shot
                total_dmg = attacker.evil_sealing_damage
                attacker.evil_sealing_active = False
                attacker.evil_sealing_damage = 0
                attacker.evil_sealing_debuff = None
                # Ability deactivates for rest of match — mark as used
                attacker.dragged_to_hell_used = True  # reuse flag to mark fired
                actual_dmg = apply_ability_damage(attacker, defender, total_dmg / SEQ_AVG_HP.get(get_seq_number(attacker.role) if attacker.role else 9, 420))
                msg += (f"💀 **Evil Sealing FIRES!** The compressed wraith-bullet erupts for **{actual_dmg}** damage! "
                        f"*(ability deactivated for rest of match)*"); color = 0x8E44AD
            else:
                # Start charging
                debuff_choices = ["burn", "bleed", "paralysis"]
                attacker.evil_sealing_debuff = random.choice(debuff_choices)
                attacker.apply_status_effect(attacker.evil_sealing_debuff, 1)
                attacker.evil_sealing_active = True
                attacker.evil_sealing_damage = 30
                msg += (f"💀 **Evil Sealing** — **{attacker.user.display_name}** begins compressing a wraith! "
                        f"Afflicted with **{attacker.evil_sealing_debuff}** as payment. Charges **+30 dmg/round** with 10 recoil. "
                        f"Use again to fire!"); color = 0x2C3E50

        elif ability_key == "protection":
            if attacker.protection_cooldown > 0:
                attacker.sp += actual_cost
                msg += f"🛡️ **Protection** on cooldown for **{attacker.protection_cooldown}** turn(s)!"; cd_blocked = True
            elif attacker.protection_active:
                # Deactivate (maintenance mode only)
                if attacker.protection_mode == "maintenance":
                    attacker.protection_active = False
                    attacker.protection_cooldown = 3
                    attacker.protection_mode = None
                    msg += f"🛡️ **Protection** cancelled — **3 turn cooldown**!"; color = 0x3498DB
                else:
                    attacker.sp += actual_cost
                    msg += f"🛡️ **Protection** is active in **Active** mode — lasts until your next attack!"; cd_blocked = True
            else:
                chosen_mode = ritual_choice if ritual_choice in ("active", "maintenance") else "maintenance"
                attacker.protection_active = True
                attacker.protection_weakened_turn = False
                attacker.protection_mode = chosen_mode
                reduction = seq_val(attacker, 80)
                if chosen_mode == "active":
                    mode_desc = "**Active** mode — protection lasts until your next attack, then automatically ends."
                else:
                    mode_desc = "**Maintenance** mode — toggle off anytime (3 turn cd on cancel)."
                msg += (f"🛡️ **Protection!** **{attacker.user.display_name}** abandons offense — damage dealt **-50%**, "
                        f"damage taken reduced by **{reduction}** (drops to **{seq_val(attacker, 10)}** for 1 turn after any attack). "
                        f"{mode_desc} *(40 SP)*"); color = 0x3498DB

        elif ability_key == "dawn_armor":
            if attacker.dawn_armor_active:
                attacker.sp += actual_cost
                msg += f"🌅 **Dawn Armor** is already active and cannot be cancelled!"; cd_blocked = True
            elif attacker.dawn_armor_cooldown > 0:
                attacker.sp += actual_cost
                msg += f"🌅 **Dawn Armor** on cooldown for **{attacker.dawn_armor_cooldown}** turn(s)!"; cd_blocked = True
            else:
                attacker.dawn_armor_active = True
                hp_bonus = int(attacker.max_hp * 0.20)
                attacker.dawn_armor_hp_bonus = hp_bonus
                attacker.max_hp += hp_bonus
                attacker.hp = min(attacker.max_hp, attacker.hp + hp_bonus)
                attacker.damage_boost_pct += 0.10
                msg += (f"🌅 **Dawn Armor!** Radiant armor envelops **{attacker.user.display_name}** — "
                        f"**+{hp_bonus} max HP** (+20%) and **+10% strength**! Cannot be cancelled. *(30 SP)*"); color = 0xFFD700

        elif ability_key == "disease_propagation":
            if attacker.disease_propagation_used:
                attacker.sp += actual_cost
                msg += f"🦠 **Disease Propagation** already active!"; cd_blocked = True
            else:
                attacker.disease_propagation_used = True
                attacker.disease_propagation_active = True
                attacker.disease_propagation_turns = 0
                msg += (f"🦠 **Disease Propagation!** **{attacker.user.display_name}** channels the power of plague — "
                        f"**{defender.user.display_name}** is infected! 15 HP + growing damage every round until death. "
                        f"*(1 use only)*"); color = 0x8E44AD

        elif ability_key == "thread_storm":
            # Mirror Curse (Demoness Seq 5 ability 2 — renamed from thread_storm)
            mc_used = getattr(attacker, "mirror_curse_used", 0)
            mc_cd   = getattr(attacker, "mirror_curse_cd", 0)
            if mc_used >= 2:
                attacker.sp += actual_cost; msg += f"🪞 **Mirror Curse** — both uses spent!"; cd_blocked = True
            elif mc_cd > 0:
                attacker.sp += actual_cost; msg += f"🪞 **Mirror Curse** on cooldown ({mc_cd}t)!"; cd_blocked = True
            elif not getattr(attacker, "mirror_curse_charging", False):
                # Start charging — 2 turn charge, no actions during
                attacker.mirror_curse_charging = True; attacker.mirror_curse_charge_turns = 2
                attacker.sp += actual_cost  # refund — charging turn
                msg += f"🪞 **Mirror Curse** — charging for **2 turns**... Cannot act during this time!"; color = 0x8E44AD; cd_blocked = True
            else:
                # Should be handled via charge completion in apply_turn_effects
                attacker.sp += actual_cost; msg += f"🪞 **Mirror Curse** — still charging!"; cd_blocked = True

        elif ability_key == "cull":
            if attacker.cull_uses <= 0:
                attacker.sp += actual_cost
                msg += f"☠️ **Cull** — no uses remaining!"; cd_blocked = True
            elif attacker.cull_cooldown > 0:
                attacker.sp += actual_cost
                msg += f"☠️ **Cull** on cooldown for **{attacker.cull_cooldown}** turn(s)!"; cd_blocked = True
            elif attacker.cull_bonus > 0:
                attacker.sp += actual_cost
                msg += f"☠️ **Cull** already charged!"; cd_blocked = True
            else:
                attacker.cull_uses -= 1
                attacker.cull_cooldown = 4
                attacker.cull_bonus = seq_val(attacker, 100)
                msg += (f"☠️ **Cull!** **{attacker.user.display_name}** marks the ultimate weak point — "
                        f"next attack gains **+{seq_val(attacker, 100)} bonus damage**! "
                        f"*({attacker.cull_uses} use(s) left, 4 turn cd)*"); color = 0xE74C3C

        elif ability_key == "weakness_development":
            if attacker.weakness_development_uses <= 0:
                attacker.sp += actual_cost
                msg += f"🎯 **Weakness Development** — no uses remaining!"; cd_blocked = True
            elif attacker.weakness_development_cooldown > 0:
                attacker.sp += actual_cost
                msg += f"🎯 **Weakness Development** on cooldown for **{attacker.weakness_development_cooldown}** turn(s)!"; cd_blocked = True
            else:
                attacker.weakness_development_uses -= 1
                attacker.weakness_development_cooldown = 3
                # Randomly choose: halve a skill or apply debuff
                if random.random() < 0.50:
                    # Place 20% damage vulnerability (more damage taken) for 3 turns
                    defender.damage_vulnerability = 3
                    msg += (f"🎯 **Weakness Development!** A weakpoint is exposed — "
                            f"**{defender.user.display_name}** takes **20% more damage**! "
                            f"*({attacker.weakness_development_uses} use(s) left, 3 turn cd)*"); color = 0xF39C12
                else:
                    # Halve defender's role ability by adding it to analysed
                    def_ak = defender.role_ability
                    if def_ak:
                        defender.analysed_skills[def_ak] = max(defender.analysed_skills.get(def_ak, 0), 0.50)
                        info_d, _ = get_ability_info(def_ak)
                        dname = info_d["name"] if info_d else def_ak
                        msg += (f"🎯 **Weakness Development!** **{dname}** from **{defender.user.display_name}** "
                                f"is permanently halved in power! *({attacker.weakness_development_uses} use(s) left, 3 turn cd)*"); color = 0xF39C12
                    else:
                        defender.damage_vulnerability = 3
                        msg += (f"🎯 **Weakness Development!** **{defender.user.display_name}** takes **20% more damage**! "
                                f"*({attacker.weakness_development_uses} use(s) left, 3 turn cd)*"); color = 0xF39C12

        elif ability_key == "stellar_self":
            if attacker.stellar_self_turns > 0:
                attacker.sp += actual_cost
                msg += f"⭐ **Stellar Self** already active!"; cd_blocked = True
            else:
                attacker.stellar_self_turns = 2
                msg += (f"⭐ **Stellar Self!** **{attacker.user.display_name}** fuses with a substitute star-clone — "
                        f"completely unhittable by Beyonders of the same sequence or lower for **2 turns**!"); color = 0xF1C40F

        elif ability_key == "star_pillar":
            if attacker.star_pillar_cooldown > 0:
                attacker.sp += actual_cost
                msg += f"💫 **Star Pillar** on cooldown for **{attacker.star_pillar_cooldown}** turn(s)!"; cd_blocked = True
            else:
                attacker.star_pillar_cooldown = 3
                actual_dmg = apply_ability_damage(attacker, defender, 0.107, is_ability=True)
                msg += (f"💫 **Star Pillar!** Layers of starlight coalesce into a disintegrating column — "
                        f"**{actual_dmg}** damage! *(3 turn cd)*"); color = 0xF1C40F

        elif ability_key == "fire_storm":
            if attacker.fire_storm_cooldown > 0:
                attacker.sp += actual_cost
                msg += f"☄️ **Fire Storm** on cooldown for **{attacker.fire_storm_cooldown}** turn(s)!"; cd_blocked = True
            else:
                attacker.fire_storm_cooldown = 5
                roll = random.random()
                if roll < 0.40:
                    actual_dmg = apply_ability_damage(attacker, defender, 0.119, is_ability=True)  # ~50 at seq5
                    msg += (f"☄️ **Fire Storm!** A burning meteor is redirected — **{actual_dmg}** damage! *(5 turn cd)*"); color = 0xE74C3C
                else:
                    actual_dmg = apply_ability_damage(attacker, defender, 0.071, is_ability=True)  # ~30 at seq5
                    msg += (f"☄️ **Fire Storm!** Partial meteor impact — **{actual_dmg}** damage. *(5 turn cd)*"); color = 0xF39C12

        elif ability_key == "star_of_curses":
            if attacker.star_of_curses_used:
                attacker.sp += actual_cost
                msg += f"🌟 **Star of Curses** already active!"; cd_blocked = True
            else:
                attacker.star_of_curses_used = True
                attacker.star_of_curses_turns = 5
                initial_dmg = apply_ability_damage(attacker, defender, 0.012, is_ability=True)  # ~5 dmg
                msg += (f"🌟 **Star of Curses!** An insidious constellation appears — **{initial_dmg}** initial damage. "
                        f"**{defender.user.display_name}** will be cursed with a random debuff **every round for 5 turns**! "
                        f"*(1 use only)*"); color = 0xF1C40F

        elif ability_key == "curse_of_misfortune":
            if attacker.curse_of_misfortune_cooldown > 0:
                attacker.sp += actual_cost
                msg += f"🎲 **Curse of Misfortune** on cooldown for **{attacker.curse_of_misfortune_cooldown}** turn(s)!"; cd_blocked = True
            else:
                attacker.curse_of_misfortune_cooldown = 5
                defender.curse_of_misfortune_turns = 5
                msg += (f"🎲 **Curse of Misfortune!** Misfortune is transferred to **{defender.user.display_name}** via a standard hit — "
                        f"for **5 rounds**: accuracy **10-40%** and **20 recoil** on each of their attacks! *(5 turn cd)*"); color = 0xDAA520

        elif ability_key == "active_luck_boost":
            if attacker.active_luck_boost_used:
                attacker.sp += actual_cost
                msg += f"🍀 **Active Luck Boost** already used!"; cd_blocked = True
            else:
                attacker.active_luck_boost_used = True
                attacker.active_luck_boost_turns = 2
                msg += (f"🍀 **Active Luck Boost!** **{attacker.user.display_name}** focuses all gathered fortune — "
                        f"for **2 turns**: 90% dodge chance, best probability outcomes, +1 buff duration, "
                        f"and 10% chance for 40 random damage per turn! *(1 use only)*"); color = 0x2ECC71

        elif ability_key == "artificial_moon":
            if attacker.artificial_moon_used:
                attacker.sp += actual_cost
                msg += f"🌕 **Artificial Moon** already used!"; cd_blocked = True
            else:
                attacker.artificial_moon_used = True
                attacker.artificial_moon_turns = 5
                sp_bonus = int(attacker.max_sp * 0.50)
                attacker.sp = min(attacker.max_sp, attacker.sp + sp_bonus)
                attacker.damage_boost_pct += 0.40
                msg += (f"🌕 **Artificial Moon!** The red moon's power channels through **{attacker.user.display_name}** — "
                        f"**+{sp_bonus} SP** and all beyonder ability damage boosted **+40%** for **5 rounds**! *(1 use only)*"); color = 0xC0C0C0

        elif ability_key == "scarlet_transformation":
            if attacker.scarlet_transformation_active:
                attacker.sp += actual_cost
                msg += f"🌙 **Scarlet Transformation** already active!"; cd_blocked = True
            elif attacker.scarlet_transformation_cooldown > 0:
                attacker.sp += actual_cost
                msg += f"🌙 **Scarlet Transformation** on cooldown for **{attacker.scarlet_transformation_cooldown}** turn(s)!"; cd_blocked = True
            else:
                attacker.scarlet_transformation_active = True
                attacker.scarlet_transformation_cooldown = 7  # deactivates at 3 (7-4=3) via tick_cooldowns
                msg += (f"🌙 **Scarlet Transformation!** **{attacker.user.display_name}** transforms into living moonlight — "
                        f"completely immune to all physical and special attacks for **4 turns**! *(7 turn cd)*"); color = 0xC0C0C0

        elif ability_key == "spirit_animal_transformation":
            if attacker.spirit_animal_active:
                # Deactivate
                attacker.spirit_animal_active = False
                attacker.max_hp = max(1, attacker.max_hp - attacker.spirit_animal_hp_bonus)
                attacker.hp = min(attacker.max_hp, attacker.hp)
                attacker.spirit_animal_hp_bonus = 0
                attacker.spirit_animal_cooldown = 10
                attacker.damage_boost_pct = max(0.0, attacker.damage_boost_pct - 0.50)
                msg += (f"🐻 **Spirit Animal** deactivated — **{attacker.user.display_name}** reverts! "
                        f"*(10 turn cd)*"); color = 0x2ECC71
            elif attacker.spirit_animal_cooldown > 0:
                attacker.sp += actual_cost
                msg += f"🐻 **Spirit Animal** on cooldown for **{attacker.spirit_animal_cooldown}** turn(s)!"; cd_blocked = True
            else:
                attacker.spirit_animal_active = True
                attacker.spirit_animal_turns = 0
                hp_bonus = int(attacker.max_hp * 0.40)
                attacker.spirit_animal_hp_bonus = hp_bonus
                attacker.max_hp += hp_bonus
                attacker.hp = min(attacker.max_hp, attacker.hp + hp_bonus)
                attacker.damage_boost_pct += 0.50
                msg += (f"🐻 **Spirit Animal Transformation!** **{attacker.user.display_name}** becomes a giant bear — "
                        f"**+40% HP** (+{hp_bonus}) and **+50% physical strength**! Costs **5 SP/round** to maintain. *(Use again to deactivate, 10 turn cd)*"); color = 0x228B22

        elif ability_key == "wrath_of_nature":
            if attacker.wrath_of_nature_cooldown > 0:
                attacker.sp += actual_cost
                msg += f"🌿 **Wrath of Nature** on cooldown for **{attacker.wrath_of_nature_cooldown}** turn(s)!"; cd_blocked = True
            else:
                attacker.wrath_of_nature_cooldown = 5
                roll = random.random()
                if roll < 0.30:
                    defender.trapped = max(defender.trapped, 3)
                    msg += (f"🌿 **Wrath of Nature — Swamp!** The earth swallows **{defender.user.display_name}** — "
                            f"trapped for **3 turns**! *(5 turn cd)*"); color = 0x228B22
                elif roll < 0.60:
                    wood_hp = 20
                    attacker.hp = min(attacker.max_hp, attacker.hp + wood_hp)
                    attacker.wrath_of_nature_thorn = True
                    msg += (f"🌿 **Wrath of Nature — Child of the Oak!** Wooden armour grants **+{wood_hp} HP** "
                            f"+ thorn recoil **5 dmg** each time **{attacker.user.display_name}** is struck! *(5 turn cd)*"); color = 0x228B22
                else:
                    attacker.wrath_of_nature_poison_turns = 4
                    msg += (f"🌿 **Wrath of Nature — Poison Creation!** Poisonous vines unleash a shroud — "
                            f"**{defender.user.display_name}** suffers **15 dmg/round** for **4 rounds**! *(5 turn cd)*"); color = 0x228B22

        elif ability_key == "possession":
            allowed_gap, gap = seq_gap_check(attacker, defender, max_gap=2)
            if not allowed_gap:
                attacker.sp += actual_cost
                msg += f"😈 **Possession failed!** **{defender.user.display_name}**'s spirit is too refined to be possessed. (gap: {gap})"; color = 0x95A5A6
                cd_blocked = True
            if attacker.possession_active:
                attacker.sp += actual_cost
                msg += f"👁️ **Possession** already active!"; cd_blocked = True
            elif attacker.possession_cooldown > 0:
                attacker.sp += actual_cost
                msg += f"👁️ **Possession** on cooldown for **{attacker.possession_cooldown}** turn(s)!"; cd_blocked = True
            else:
                def_pathway = get_pathway_name(defender.role) if defender.role else ""
                def_seq = get_seq_number(defender.role) if defender.role else 9
                if def_pathway == "Sun" and def_seq <= 5:
                    attacker.sp += actual_cost
                    msg += f"👁️ **Possession** — Sun pathway Seq 5+ is completely immune!"; cd_blocked = True
                else:
                    attacker.possession_active = True
                    turns = random.choices([3, 4, 5], weights=[50, 30, 20])[0]
                    attacker.possession_turns = turns
                    attacker.possession_cooldown = 6
                    msg += (f"👁️ **Possession!** **{attacker.user.display_name}** slips into **{defender.user.display_name}**'s body — "
                            f"**20 damage/turn** for **{turns} turns**! *(6 turn cd)*"); color = 0x8B0000

        elif ability_key == "wraith_shriek":
            if attacker.wraith_shriek_cooldown > 0:
                attacker.sp += actual_cost
                msg += f"👻 **Wraith Shriek** on cooldown for **{attacker.wraith_shriek_cooldown}** turn(s)!"; cd_blocked = True
            else:
                attacker.wraith_shriek_cooldown = 4
                attacker.wraith_shriek_acc_debuff = 2
                sp_stripped = min(seq_val(attacker, 40), defender.sp)
                defender.sp = max(0, defender.sp - sp_stripped)
                defender.apply_status_effect("bleed", 5)
                msg += (f"👻 **Wraith Shriek!** A soul-rending shriek — **{sp_stripped} SP** stripped from "
                        f"**{defender.user.display_name}** + **5-round bleed**! "
                        f"**{attacker.user.display_name}**'s accuracy is **-40%** for 2 turns. *(4 turn cd)*"); color = 0x4B0082

        elif ability_key == "grazing":
            allowed_gap, gap = seq_gap_check(attacker, defender, max_gap=3)
            if not allowed_gap:
                attacker.sp += actual_cost
                msg += f"🌾 **Grazing failed!** Cannot graze the spirit body of someone {gap} sequences stronger."; color = 0x95A5A6
                cd_blocked = True
            # Shepherd Seq 5 (Hanged Man) — graze the opponent's spirit body to copy their abilities
            if attacker.grazing_used:
                attacker.sp += actual_cost
                msg += f"🌾 **Grazing** — already grazed this battle!"; color = 0x95A5A6
                cd_blocked = True
            else:
                attacker.grazing_used = True
                # Copy up to 3 of the opponent's available abilities (role ability + used abilities)
                available = list(getattr(defender, "abilities_used_this_battle", set()))
                if defender.role_ability and defender.role_ability not in available:
                    available.insert(0, defender.role_ability)
                copied = available[:3]
                attacker.grazed_abilities = list(copied)
                names = []
                for ak in copied:
                    info_g, _ = get_ability_info(ak)
                    names.append(info_g["name"] if info_g else ak)
                attacker.grazing_cooldown = 5
                if copied:
                    msg += (f"🌾 **Grazing!** **{attacker.user.display_name}** grazes **{defender.user.display_name}**'s spirit body — "
                            f"copied **{len(copied)}** abilities: **{', '.join(names)}**! Use them with **Ability Bag**. *(5 turn cd)*"); color = 0x2ECC71
                else:
                    attacker.sp += actual_cost
                    msg += f"🌾 **Grazing** — nothing to copy yet!"; color = 0x95A5A6

        elif ability_key == "desire_explosion":
            if attacker.desire_explosion_uses <= 0:
                attacker.sp += actual_cost
                msg += f"💥 **Desire Explosion** — no uses remaining!"; cd_blocked = True
            elif attacker.desire_explosion_cooldown > 0:
                attacker.sp += actual_cost
                msg += f"💥 **Desire Explosion** on cooldown for **{attacker.desire_explosion_cooldown}** turn(s)!"; cd_blocked = True
            elif not attacker.vile_crime_used:
                attacker.sp += actual_cost
                msg += f"💥 **Desire Explosion** — requires a vile crime to be committed first!"; cd_blocked = True
            else:
                attacker.desire_explosion_uses -= 1
                attacker.desire_explosion_cooldown = 5
                actual_dmg = apply_ability_damage(attacker, defender, 0.119, is_ability=True)  # ~50 at seq5
                stun_roll = random.random()
                if stun_roll < 0.50:
                    stun = 1
                elif stun_roll < 0.90:
                    stun = 2
                else:
                    stun = 3
                defender.apply_status_effect("paralysis", stun)
                msg += (f"💥 **Desire Explosion!** A mental shockwave erupts — **{actual_dmg}** damage + "
                        f"**{stun}-turn stun**! *({attacker.desire_explosion_uses} use(s) left, 5 turn cd)*"); color = 0xC71585

        elif ability_key == "desire_symbiosis":
            if attacker.desire_symbiosis_used:
                attacker.sp += actual_cost
                msg += f"😈 **Desire Symbiosis** already used!"; cd_blocked = True
            else:
                attacker.desire_symbiosis_used = True
                emotion = random.choice(["wrath", "lust", "envy"])
                attacker.desire_emotion = emotion
                if emotion == "wrath":
                    attacker.damage_boost_pct += 0.50
                    msg += (f"😈 **Desire Symbiosis — Wrath!** **{attacker.user.display_name}** is consumed by rage — "
                            f"**+50% attack** but **-20% accuracy**! *(1 use only)*"); color = 0xE74C3C
                elif emotion == "lust":
                    msg += (f"😈 **Desire Symbiosis — Lust!** **{attacker.user.display_name}** is consumed by desire — "
                            f"**100% accuracy** but **-60% strength**! *(1 use only)*"); color = 0xC71585
                else:
                    sp_bonus = int(attacker.max_sp * 0.70)
                    attacker.sp = min(attacker.max_sp, attacker.sp + sp_bonus)
                    msg += (f"😈 **Desire Symbiosis — Envy!** **{attacker.user.display_name}** covets power — "
                            f"**+70% SP** (+{sp_bonus}) but **-30% strength & accuracy**! *(1 use only)*"); color = 0x9B59B6

        elif ability_key == "prohibition":
            if attacker.prohibition_cooldown > 0:
                attacker.sp += actual_cost; msg += f"🚫 **Prohibition** on cooldown ({attacker.prohibition_cooldown}t)!"; cd_blocked = True
            elif attacker.prohibition_uses <= 0:
                attacker.sp += actual_cost; msg += f"🚫 **Prohibition** — no uses remaining!"; cd_blocked = True
            else:
                # Show menu to select which ability to prohibit
                allowed_gap, gap = seq_gap_check(attacker, defender, max_gap=3)
                if not allowed_gap:
                    attacker.sp += actual_cost
                    msg += f"🚫 **Prohibition failed!** {gap} sequences too strong."; cd_blocked = True
                else:
                    # Build list of defender abilities to choose from
                    def_abilities = []
                    if defender.role_ability:
                        info_p, _ = get_ability_info(defender.role_ability)
                        def_abilities.append((defender.role_ability, info_p["name"] if info_p else defender.role_ability))
                    for spell in getattr(defender, "purchased_spells", {}).keys() if hasattr(defender, "purchased_spells") else []:
                        info_p, _ = get_ability_info(spell)
                        def_abilities.append((spell, info_p["name"] if info_p else spell))
                    if not def_abilities:
                        attacker.sp += actual_cost; msg += f"🚫 **Prohibition** — no abilities to prohibit!"; cd_blocked = True
                    else:
                        # For now prohibit role ability by default (menu would be ideal but complex)
                        target_key = ritual_choice if ritual_choice and ritual_choice in dict(def_abilities) else def_abilities[0][0]
                        target_name = dict(def_abilities).get(target_key, target_key)
                        setattr(defender, f"_prohibited_{target_key}", True)
                        setattr(defender, f"_prohibited_{target_key}_turns", 10)
                        attacker.prohibition_cooldown = 10
                        attacker.prohibition_uses -= 1
                        msg += (f"🚫 **Prohibition!** **{target_name}** prohibited for **10 turns**! "
                                f"({attacker.prohibition_uses} use(s) remaining) *(10t cd)*"); color = 0xF39C12

        elif ability_key == "punishment":
            if attacker.punishment_cooldown > 0:
                attacker.sp += actual_cost; msg += f"⛓️ **Punishment** on cooldown ({attacker.punishment_cooldown}t)!"; cd_blocked = True
            elif getattr(attacker, "punishment_active", False):
                attacker.sp += actual_cost; msg += f"⛓️ **Punishment** already active!"; cd_blocked = True
            else:
                attacker.punishment_cooldown = 8
                attacker.punishment_active = True
                attacker.punishment_turns = 5
                # Cleanse all current debuffs
                for attr in ["bleed","burn","paralysis","slumber","freeze","debuff_stacks","charm_turns"]:
                    if getattr(attacker, attr, 0): setattr(attacker, attr, 0)
                attacker.bleed = 0; attacker.burn = 0; attacker.paralysis = 0
                attacker.slumber = 0; attacker.freeze = 0
                if attacker.charm_turns > 0: attacker.charm_turns = 0; attacker.charm_chance = 0.0
                # Immune to debuffs from enemies ≤1 seq higher
                attacker._punishment_immunity_turns = 5
                msg += (f"⛓️ **Punishment!** All debuffs cleansed + **immune to debuffs** (from enemies ≤1 seq higher) for **5 turns**! "
                        f"Punishment state active — deal **+25% damage** for 5 turns. *(8t cd)*"); color = 0xF39C12

        elif ability_key == "disciplinary_strike":
            if attacker.disciplinary_strike_cooldown > 0:
                attacker.sp += actual_cost
                msg += f"⚖️ **Disciplinary Strike** on cooldown for **{attacker.disciplinary_strike_cooldown}** turn(s)!"; cd_blocked = True
            else:
                attacker.disciplinary_strike_cooldown = 4
                actual_dmg = apply_ability_damage(attacker, defender, 0.143, is_ability=True)
                defender.judgement_debuff_turns = max(defender.judgement_debuff_turns, 3)
                stripped = None
                if defender.notary_buff_turns > 0:
                    defender.notary_buff_turns = 0; stripped = "Notary Buff"
                elif defender.domination_active:
                    defender.domination_active = False; stripped = "Domination"
                elif defender.appraisal_active:
                    defender.appraisal_active = False; stripped = "Appraisal"
                elif defender.invigorated:
                    defender.invigorated = False; stripped = "Invigorate"
                elif defender.artificial_moon_turns > 0:
                    defender.artificial_moon_turns = 0; stripped = "Artificial Moon"
                elif getattr(defender, "dawn_armor_active", False):
                    defender.dawn_armor_active = False
                    defender.max_hp = max(1, defender.max_hp - defender.dawn_armor_hp_bonus)
                    defender.hp = min(defender.max_hp, defender.hp)
                    defender.damage_boost_pct = max(0.0, defender.damage_boost_pct - 0.10)
                    defender.dawn_armor_cooldown = 3
                    stripped = "Dawn Armor"
                strip_txt = f" Stripped **{stripped}**!" if stripped else ""
                msg += (f"⚖️ **Disciplinary Strike!** A blow of supreme authority — **{actual_dmg}** damage!{strip_txt} "
                        f"Judgement debuff extended. *(4 turn cd)*"); color = 0x4169E1

        elif ability_key == "disorder":
            cd = getattr(attacker, "disorder_cd", 0)
            if cd > 0: attacker.sp += actual_cost; msg += f"🌀 **Disorder** on cooldown ({cd}t)!"; cd_blocked = True
            else:
                attacker.disorder_cd = 5
                # -25% accuracy for 3 turns
                attacker._force_miss_on_defender = getattr(attacker, "_force_miss_on_defender", 0)
                defender.conspiracy_miss_turns = max(defender.conspiracy_miss_turns, 3)
                roll = random.random()
                if roll < 0.33:
                    # Chaos — enemy deals self-damage 7-10% HP
                    pct = random.uniform(0.07, 0.10)
                    dmg = max(1, int(defender.hp * pct))
                    defender.hp = max(0, defender.hp - dmg)
                    msg += f"🌀 **Disorder — Chaos!** **{defender.user.display_name}** descends into chaos — deals **{dmg}** self-damage ({int(pct*100)}% HP)! *(5t cd)*"; color = 0x8E44AD
                elif roll < 0.66:
                    # Skip 2 turns
                    defender.stun = 2
                    msg += f"🌀 **Disorder — Mind Break!** **{defender.user.display_name}** skips **2 turns**! *(5t cd)*"; color = 0x8E44AD
                else:
                    # -20% strength 3 turns
                    defender._disorder_str_debuff_turns = 3
                    msg += f"🌀 **Disorder — Weakness!** **{defender.user.display_name}**'s strength −**20%** for **3 turns**! *(5t cd)*"; color = 0x8E44AD

        elif ability_key == "gift_of_corruption":
            cd = getattr(attacker, "gift_of_corruption_cd", 0)
            if cd > 0: attacker.sp += actual_cost; msg += f"🎁 **Gift of Corruption** on cooldown ({cd}t)!"; cd_blocked = True
            else:
                attacker.gift_of_corruption_cd = 4
                if random.random() < 0.10:
                    # 10% failure — stunned by higher power
                    attacker.stun = 1
                    msg += f"🎁 **Gift of Corruption — Failed!** A higher power intervenes — **{attacker.user.display_name}** is stunned for **1 turn**!"; color = 0x95A5A6
                else:
                    # Transfer all negative statuses to defender
                    transferred = []
                    for attr, label in [("bleed","bleed"),("burn","burn"),("paralysis","paralysis"),
                                        ("slumber","sleep"),("freeze","freeze"),("debuff_stacks","debuffs")]:
                        val = getattr(attacker, attr, 0)
                        if val:
                            setattr(attacker, attr, 0)
                            cur = getattr(defender, attr, 0)
                            setattr(defender, attr, max(cur, val))
                            transferred.append(label)
                    if attacker.bleed > 0: defender.bleed = max(defender.bleed, attacker.bleed); attacker.bleed = 0; transferred.append("bleed") if "bleed" not in transferred else None
                    if attacker.stun > 0 and not hasattr(attacker, '_stun_transferred'):
                        defender.stun = max(defender.stun, attacker.stun); attacker.stun = 0; transferred.append("stun")
                    txt = ", ".join(transferred) if transferred else "nothing to transfer"
                    msg += f"🎁 **Gift of Corruption!** Transferred **{txt}** to **{defender.user.display_name}**! *(4t cd)*"; color = 0x8E44AD

        elif ability_key == "sacred_conviction":
            if attacker.sacred_conviction_cooldown > 0:
                attacker.sp += actual_cost
                msg += f"🌟 **Sacred Conviction** on cooldown for **{attacker.sacred_conviction_cooldown}** turn(s)!"; cd_blocked = True
            else:
                attacker.sacred_conviction_cooldown = 5
                # Cleanse all debuffs on self
                cleared = []
                if attacker.bleed > 0:      attacker.bleed = 0;      cleared.append("bleed")
                if attacker.burn > 0:       attacker.burn = 0;       cleared.append("burn")
                if attacker.paralysis > 0:  attacker.paralysis = 0;  cleared.append("paralysis")
                if attacker.slumber > 0:    attacker.slumber = 0;    cleared.append("slumber")
                if attacker.freeze > 0:     attacker.freeze = 0;     cleared.append("freeze")
                if attacker.judgement_debuff_turns > 0: attacker.judgement_debuff_turns = 0; cleared.append("judgement")
                if attacker.curse_of_misfortune_turns > 0: attacker.curse_of_misfortune_turns = 0; cleared.append("misfortune")
                if getattr(attacker, "dream_alteration_acc_loss", 0) > 0: attacker.dream_alteration_acc_loss = 0; cleared.append("dream alteration")
                # +20% damage boost for 3 turns
                attacker.sacred_conviction_boost_turns = 3
                attacker.damage_boost_pct += 0.20
                # Deal righteous damage to opponent
                actual_dmg = apply_ability_damage(attacker, defender, 0.095, is_ability=True)
                cleanse_txt = f" Cleansed: **{', '.join(cleared)}**." if cleared else " No debuffs to cleanse."
                msg += (f"🌟 **Sacred Conviction!** The power of order surges — **{actual_dmg}** righteous damage!{cleanse_txt} "
                        f"**+20% damage** for **3 turns**! *(5 turn cd)*"); color = 0x4169E1


    # ── Victory check ──────────────────────────────────────
    v = check_victory(attacker, defender, battle_key, msg)
    if v:
        await interaction.channel.send(embed=v)
        old_msg = battle.get("battle_message")
        if old_msg:
            try: await old_msg.delete()
            except: pass
        return

    # Append any divination dodge message generated during ability damage
    div_msg = getattr(defender, "_pending_divination_msg", "")
    if div_msg:
        msg += f"\n{div_msg}"
        defender._pending_divination_msg = ""

    if action == "ability" and cd_blocked:
        # Cooldown or already-used — SP already refunded, turn is NOT consumed
        attacker.polymath_pending = False  # clean up if ability was blocked
        embed = make_battle_embed(msg, battle, battle["turn"].display_name, color)
        await send_and_replace(battle, interaction.channel, embed, view=_make_battle_view(battle))
        return

    attacker.polymath_pending = False  # ensure cleared after any ability resolves

    # ── Combo Spell trigger (Mysticism Magister) ──────────────
    if getattr(attacker, "combination_spell_active", False) and not cd_blocked:
        COMBOS = {
            frozenset({"leodero", "debuff"}):                  "lightning_curse",
            frozenset({"analyse", "ritualistic_reasoning"}):   "spirit_seal",
            frozenset({"study", "polymath"}):                  "greater_rejuvenation",
            frozenset({"debuff", "analyse"}):                  "cursed_betrayal",
        }
        prev = attacker.combo_last_ability
        curr = ability_key if action == "ability" else action
        triggered_combo = None
        if prev and curr:
            pair = frozenset({prev, curr})
            triggered_combo = COMBOS.get(pair)
        attacker.combo_last_ability = curr  # update for next turn

        if triggered_combo == "lightning_curse":
            combo_dmg = apply_ability_damage(attacker, defender, 0.30, skill_key="lightning_curse")
            applied = defender.apply_status_effect("paralysis", 2)
            msg += (f"\n⚡🔮 **COMBO — Lightning Curse!** Arcane lightning surges — **{combo_dmg}** damage"
                    f"{' + paralysis 2 turns' if applied else ''}!")
            color = 0xF1C40F
        elif triggered_combo == "spirit_seal":
            applied = defender.apply_status_effect("slumber", 2)
            seal_dmg = apply_ability_damage(attacker, defender, 0.12)
            msg += (f"\n🔮💤 **COMBO — Spirit Seal!** Consciousness locked away — **{seal_dmg}** damage"
                    f"{' + sleep 2 turns' if applied else ''}!")
            color = 0x9B59B6
        elif triggered_combo == "greater_rejuvenation":
            heal = int(attacker.max_hp * 0.20)
            sp_gain = int(attacker.max_sp * 0.30)
            attacker.hp = min(attacker.max_hp, attacker.hp + heal)
            attacker.sp = min(attacker.max_sp, attacker.sp + sp_gain)
            msg += f"\n🔮💚 **COMBO — Greater Rejuvenation!** Restored **{heal} HP** and **{sp_gain} SP**!"
            color = 0x2ECC71
        elif triggered_combo == "cursed_betrayal":
            betrayal_total = 0
            for _ in range(3):
                betrayal_total += apply_ability_damage(attacker, defender, 0.09)
            defender.debuff_stacks = max(defender.debuff_stacks, 2)
            msg += f"\n🔮☠️ **COMBO — Cursed Betrayal!** Triple arcane strike — **{betrayal_total}** total damage + defence broken!"
            color = 0xE74C3C

    # ── Combo Victory check (combo spells can kill) ──────────────
    v = check_victory(attacker, defender, battle_key, msg)
    if v:
        await interaction.channel.send(embed=v)
        old_msg = battle.get("battle_message")
        if old_msg:
            try: await old_msg.delete()
            except: pass
        return

    swap_turn(battle)

    # Prepend sudden death drain message if it was set during swap_turn
    sudden_msg = battle.pop("_sudden_death_msg", "")
    if sudden_msg:
        msg = sudden_msg + msg
        # Check if sudden death killed someone
        v = check_victory(attacker, defender, battle_key, msg)
        if v:
            await send_and_replace(battle, interaction.channel, v)
            return

    embed = make_battle_embed(msg, battle, battle["turn"].display_name, color)
    await send_and_replace(battle, interaction.channel, embed, view=_make_battle_view(battle))

# ── Gambling ──────────────────────────────────────────────

