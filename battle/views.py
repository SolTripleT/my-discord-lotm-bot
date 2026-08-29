# ============================================================
# BATTLE VIEWS — every Discord UI component (Select/Button/Modal/View)
# used by the progression + PvP systems: the pathway-choice select
# menu, the in-battle action bar and per-pathway ability menus, the
# stat-allocation UI, the item-use menu, the spell shop, and the
# /guide + /pathway_ability + /stats_user embeds/browsers.
# ============================================================
import re
import random
import asyncio
import discord

from bot_instance import bot
from logging_config import logger
from config import pathways, pathway_colors
from database import get_user_data, update_user, is_blacklisted, db_remove_item
from utils import log_event
from roles import (
    get_sequence_name, get_seq_number, get_pathway_name, get_pathway_family,
    get_ability_info, assign_sequence_role, remove_all_lotm_sequence_roles,
)
from fighter import Fighter, build_stats_embed, get_or_create_stats, get_scaled_description, seq_val
from persistence import save_stats
from economy import (
    add_pounds, remove_pounds, get_pounds, get_economy, owns_spell, buy_spell,
)
from xp import apply_xp_gain, penalize_combat_xp, SEQ_LOSS_XP
from game_data import (
    ABILITIES, ROLE_ABILITIES, ROLE_ABILITIES_2, ROLE_ABILITY_INFO, INFO_PAGES,
    STAT_LABELS, CHAIR_XP, CHAIR_USE_TEXTS, ALL_CHAIR_NAMES,
)
from battle.manager import (
    battles, active_bets, get_battle, get_battle_key_for_user, get_fighters,
    battle_status, get_available_actions, inactivity_watcher, record_battle,
    safe_respond, _battle_key, _make_battle_view, _view_on_error,
)
from battle.actions import process_action, _check_cooldown

PATHWAY_EMOJI_NAMES = {
    "Fool":           "SeerN",
    "Error":          "MarauderN",
    "Door":           "ApprenticeN",
    "Visionary":      "SpectatorN",
    "Sun":            "BardN",
    "Tyrant":         "SailorN",
    "Hanged Man":     "SecreSuppN",
    "White Tower":    "ReaderN",
    "Darkness":       "SleeplessN",
    "Death":          "CorColleN",
    "Twilight Giant": "WarriorN",
    "Demoness":       "AssassinN",
    "Red Priest":     "HunterN",
    "Hermit":         "MysPryerN",
    "Paragon":        "SavantN",
    "Wheel of Fortune": "MonsterN",
    "Moon":           "ApothecaryN",
    "Mother":         "PlanterN",
    "Chained":        "PrisonerN",
    "Abyss":          "CriminalN",
    "Black Emperor":  "LawyerN",
    "Justiciar":      "ArbiterN",
}

def get_pathway_emoji(guild: discord.Guild, pathway: str):
    """Return the custom guild emoji object for a pathway, or None."""
    emoji_name = PATHWAY_EMOJI_NAMES.get(pathway)
    if not emoji_name or not guild:
        return None
    return discord.utils.get(guild.emojis, name=emoji_name)

PATHWAY_DESCRIPTIONS = {
    "Fool": "Mystery, deception, and fate", "Error": "Time, cryptology, and paradox",
    "Door": "Travel, secrets, and the stars", "Visionary": "Dreams, telepathy, and the mind",
    "Sun": "Light, justice, and divine radiance", "Tyrant": "Sea, storms, and raw fury",
    "Hanged Man": "Shadows, sacrifice, and dark angels", "White Tower": "Logic, wisdom, and omniscience",
    "Darkness": "Night, horror, and misfortune", "Death": "Souls, undeath, and the underworld",
    "Twilight Giant": "Battle, glory, and divine strength", "Demoness": "Desire, affliction, and ruin",
    "Red Priest": "Blood, war, and conquest", "Hermit": "Arcane lore and hidden knowledge",
    "Paragon": "Alchemy, invention, and illumination", "Wheel of Fortune": "Luck, chaos, and fate's whims",
    "Moon": "Beasts, potions, and life's beauty", "Mother": "Nature, growth, and ancient earth",
    "Chained": "Madness, curses, and ancient banes", "Abyss": "Sin, demons, and corruption",
    "Black Emperor": "Disorder, corruption, and entropy", "Justiciar": "Order, law, and balance",
}

PATHWAY_LIST = list(pathways.keys())  # 22 pathways — fits in one Discord select (max 25)


class PathwaySelect(discord.ui.Select):
    def __init__(self, guild=None):
        # guild=None when re-registering persistent view on startup
        options = [
            discord.SelectOption(
                label=p,
                value=p,
                description=PATHWAY_DESCRIPTIONS.get(p, ""),
                emoji=get_pathway_emoji(guild, p) if guild else None
            ) for p in PATHWAY_LIST
        ]
        super().__init__(
            placeholder="Choose your Beyonder Pathway…",
            options=options,
            custom_id="pathway_select"
        )

    async def callback(self, interaction: discord.Interaction):
        await handle_pathway_selection(interaction, self.values[0])


class PathwaySelectView(discord.ui.View):
    def __init__(self, guild=None):
        super().__init__(timeout=None)
        self.add_item(PathwaySelect(guild))


async def handle_pathway_selection(interaction: discord.Interaction, pathway: str):
    """Shared handler called when a member picks a pathway from the select menu."""
    guild_id = interaction.guild_id
    guild = interaction.guild or bot.get_guild(guild_id)
    logger.debug(f"[pathway selection] user={interaction.user.id} guild_id={guild_id} pathway={pathway}")

    if await is_blacklisted(interaction.user.id, guild_id):
        await interaction.response.send_message("🚫 You are blacklisted from using this bot.", ephemeral=True)
        return

    old_data = await get_user_data(interaction.user.id, guild_id)
    old_pathway = old_data["pathway"] if old_data else None

    # Always reset progress and switch pathway
    await update_user(interaction.user.id, guild_id=interaction.guild_id, pathway=pathway, sequence=9, xp=0,
                      last_daily=None, last_pray=None)

    # Verify the save actually worked
    verify = await get_user_data(interaction.user.id, guild_id)
    logger.debug(f"[pathway selection] verify after save: {verify}")

    member = None
    if guild:
        try:
            member = guild.get_member(interaction.user.id) or await guild.fetch_member(interaction.user.id)
            logger.debug(f"[ROLE] member fetched: {member}")
        except Exception:
            logger.warning(f"[ROLE] Could not fetch member {interaction.user.id}", exc_info=True)
    else:
        logger.debug(f"[ROLE] guild is None for guild_id={guild_id}")

    if member:
        await remove_all_lotm_sequence_roles(member)
        await assign_sequence_role(member, pathway, 9)
    else:
        logger.debug("[ROLE] Skipping role assignment — member is None")

    name = get_sequence_name(pathway, 9)

    await log_event(
        event_type="PATHWAY_CHANGE" if old_pathway else "PATHWAY_CHOICE",
        details=f"Switched from **{old_pathway}** to **{pathway}** Pathway (progress reset)" if old_pathway else f"Chose the **{pathway}** Pathway via select menu",
        user_id=interaction.user.id,
        username=str(interaction.user),
        guild=guild
    )

    color = pathway_colors.get(pathway, 0xf5c400)
    emoji = get_pathway_emoji(guild, pathway) if guild else None
    emoji_str = str(emoji) if emoji else ""

    if old_pathway and old_pathway != pathway:
        desc = (
            f"{interaction.user.mention} has left the **{old_pathway} Pathway** and entered the **{pathway} Pathway**!\n\n"
            f"You begin fresh as **Sequence 9 — {name}**.\n"
            f"*{PATHWAY_DESCRIPTIONS.get(pathway, '')}*\n\n"
            f"Use `/profile` to view your status."
        )
    else:
        desc = (
            f"{interaction.user.mention} has entered the **{pathway} Pathway**!\n\n"
            f"You begin as **Sequence 9 — {name}**.\n"
            f"*{PATHWAY_DESCRIPTIONS.get(pathway, '')}*\n\n"
            f"Use `/profile` to view your status, `/daily` to claim XP, and `/pray` for blessings."
        )

    embed = discord.Embed(title=f"{emoji_str} PATHWAY CHOSEN!", description=desc, color=color)
    embed.set_footer(text="Lord of the Mysteries — Beyonder Tracker")
    await interaction.response.send_message(embed=embed, ephemeral=True)



class InfoView(discord.ui.View):
    def __init__(self, page: int = 0):
        super().__init__(timeout=120)
        self.page = page
        self._update_buttons()

    async def on_error(self, interaction: discord.Interaction, error: Exception, item) -> None:
        await _view_on_error(interaction, error, item)

    def _update_buttons(self):
        self.prev_btn.disabled = self.page == 0
        self.next_btn.disabled = self.page == len(INFO_PAGES) - 1

    @discord.ui.button(label="◀ Prev", style=discord.ButtonStyle.secondary)
    async def prev_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page = max(0, self.page - 1)
        self._update_buttons()
        await interaction.response.edit_message(embed=INFO_PAGES[self.page], view=self)

    @discord.ui.button(label="Next ▶", style=discord.ButtonStyle.secondary)
    async def next_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page = min(len(INFO_PAGES) - 1, self.page + 1)
        self._update_buttons()
        await interaction.response.edit_message(embed=INFO_PAGES[self.page], view=self)

def info_embed() -> discord.Embed:
    return INFO_PAGES[0]

# ── Fighter ───────────────────────────────────────────────


class BattleView(discord.ui.View):
    # Fool, Error, and Door are the escape/misdirection pathways — they get a
    # clean, no-drawback Flee button instead of Surrender. Every other pathway
    # keeps Surrender (forfeit with an EXP penalty). Applies at any Sequence.
    FLEE_PATHWAYS = {"Fool", "Error", "Door"}

    def __init__(self, battle):
        super().__init__(timeout=None)  # inactivity_watcher is the sole timeout mechanism
        fighter, _ = get_fighters(battle["turn"], battle)
        pathway = get_pathway_name(fighter.role) if fighter.role else ""

        if pathway in self.FLEE_PATHWAYS:
            flee_button = discord.ui.Button(label="🏃 Flee", style=discord.ButtonStyle.success)
            flee_button.callback = self.flee_btn
            self.add_item(flee_button)
        else:
            surrender_button = discord.ui.Button(label="🏳️ Surrender", style=discord.ButtonStyle.danger)
            surrender_button.callback = self.surrender_btn
            self.add_item(surrender_button)

    async def on_timeout(self):
        pass  # never fires (timeout=None) — inactivity_watcher handles all fight endings

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        battle = get_battle(interaction.channel.id, interaction.user)
        if not battle:
            await safe_respond(interaction, "❌ No active battle!")
            return False
        if interaction.user != battle["turn"]:
            await safe_respond(interaction, "❌ It's not your turn!")
            return False
        return True

    @discord.ui.button(label="⚔️ Attack", style=discord.ButtonStyle.primary, disabled=True)
    async def attack_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await process_action(interaction, "attack")

    @discord.ui.button(label="⚡ Strike", style=discord.ButtonStyle.danger)
    async def special_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await process_action(interaction, "special")

    @discord.ui.button(label="🌟 Pathway", style=discord.ButtonStyle.secondary)
    async def pathway_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        battle = get_battle(interaction.channel.id, interaction.user)
        attacker, _ = get_fighters(interaction.user, battle)

        # Group abilities by pathway family
        family_pages = {}   # family_name -> [(role_name, ak), ...]
        for role_name in attacker.all_roles:
            ak = ROLE_ABILITIES.get(role_name)
            if not ak:
                continue
            family = get_pathway_family(role_name) or "Other"
            if family not in family_pages:
                family_pages[family] = []
            if not any(a == ak for _, a in family_pages[family]):
                family_pages[family].append((role_name, ak))
            # Also include Seq 5 secondary ability
            ak2 = ROLE_ABILITIES_2.get(role_name)
            if ak2 and not any(a == ak2 for _, a in family_pages[family]):
                family_pages[family].append((role_name, ak2))

        # Special one-shot entries (Prometheus/Scribe/Polymath) go on page 0
        special_keys = []
        if attacker.prometheus_stolen_ability:
            pstolen = attacker.prometheus_stolen_ability
            info_p, _ = get_ability_info(pstolen)
            if info_p:
                special_keys.append(("_prometheus_stolen", pstolen))
        if attacker.scribe_copied_ability and not attacker.scribe_active:
            pcopied = attacker.scribe_copied_ability
            info_c, _ = get_ability_info(pcopied)
            if info_c:
                special_keys.append(("_scribe_copy", pcopied))
        if attacker.polymath_ability and attacker.polymath_cooldown == 0:
            ppoly = attacker.polymath_ability
            info_poly, _ = get_ability_info(ppoly)
            if info_poly:
                special_keys.append(("_polymath_use", ppoly))

        if not family_pages and not special_keys:
            return await interaction.response.send_message(
                "❌ You have no pathway abilities.", ephemeral=True)

        pages = []  # list of (title, [(role_name, ak)])
        if special_keys:
            pages.append(("✨ Special", special_keys))
        for family, pairs in family_pages.items():
            pages.append((f"[{family}] Pathway", pairs))

        await interaction.response.send_message(
            **_build_pathway_page(attacker, pages, 0),
            view=PathwayPageView(attacker, pages, 0),
            ephemeral=True
        )

    @discord.ui.button(label="✨ Abilities", style=discord.ButtonStyle.success)
    async def abilities_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        battle = get_battle(interaction.channel.id, interaction.user)
        attacker, _ = get_fighters(interaction.user, battle)
        purchased = get_economy(interaction.user.id, guild_id=interaction.guild_id)["spells"]
        if not purchased:
            return await interaction.response.send_message(
                "❌ No purchased abilities! Visit `/shop`.\n*(Pathway ability → 🌟 Pathway button)*",
                ephemeral=True)
        lines = [f"**✨ Choose an ability** — Spirit: `{attacker.sp}/{attacker.max_sp}`\n"]
        for key in purchased:
            info, _ = get_ability_info(key)
            if not info:
                continue
            cost = attacker.get_ability_cost(info["cost"], key)
            cost_label = info.get("display_cost") or f"{cost} SP"
            affordable = "✅" if (info.get("display_cost") or attacker.sp >= cost) else "❌"
            lines.append(f"{affordable} **{info['name']}** — `{cost_label}`\n> {get_scaled_description(key, attacker)}\n")
        await interaction.response.send_message(
            "\n".join(lines), view=AbilityView(attacker, purchased), ephemeral=True)

    async def surrender_btn(self, interaction: discord.Interaction):
        battle = get_battle(interaction.channel.id, interaction.user)
        if not battle or interaction.user not in [battle["challenger"].user, battle["opponent"].user]:
            return await safe_respond(interaction, "❌ You're not in this fight!")
        # Show confirmation
        await interaction.response.send_message(
            "🏳️ **Surrender?** You'll forfeit the match immediately — no soli penalty but you lose the EXP.\n"
            "Click confirm to surrender.",
            view=SurrenderConfirmView(interaction.user, battle, interaction.channel.id),
            ephemeral=True
        )

    async def flee_btn(self, interaction: discord.Interaction):
        battle = get_battle(interaction.channel.id, interaction.user)
        if not battle or interaction.user not in [battle["challenger"].user, battle["opponent"].user]:
            return await safe_respond(interaction, "❌ You're not in this fight!")
        # Show confirmation
        await interaction.response.send_message(
            "🏃 **Flee the fight?** Your pathway lets you slip away clean — "
            "no soli lost, no EXP lost, nothing recorded.\n"
            "Click confirm to flee.",
            view=FleeConfirmView(interaction.user, battle, interaction.channel.id),
            ephemeral=True
        )


class AbilityBagView(discord.ui.View):
    """Shows grazed abilities as buttons — selecting one fires it as the player's turn action."""
    def __init__(self, attacker: "Fighter", entries: list, battle: dict, channel):
        super().__init__(timeout=30)
        self.attacker = attacker
        self.battle   = battle
        self.channel  = channel
        for ability_key, name, cost_txt in entries[:5]:  # max 5 buttons
            btn = discord.ui.Button(
                label=f"{name} ({cost_txt})",
                style=discord.ButtonStyle.primary,
                custom_id=f"bag_{ability_key}"
            )
            btn.callback = self._make_callback(ability_key, name)
            self.add_item(btn)

    def _make_callback(self, ability_key: str, name: str):
        async def callback(interaction: discord.Interaction):
            if interaction.user != self.attacker.user:
                return await interaction.response.send_message("❌ Not your bag!", ephemeral=True)
            self.stop()
            await interaction.response.defer()
            # Fire as a normal ability action — reuse process_action
            await process_action(interaction, "ability", ability_key=ability_key, from_bag=True)
        return callback


class SurrenderConfirmView(discord.ui.View):
    def __init__(self, user: discord.Member, battle: dict, channel_id: int):
        super().__init__(timeout=20)
        self.user = user
        self.battle = battle
        self.channel_id = channel_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user != self.user:
            await safe_respond(interaction, "❌ Not your surrender!")
            return False
        return True

    @discord.ui.button(label="✅ Confirm Surrender", style=discord.ButtonStyle.danger)
    async def confirm_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        battle = self.battle
        battle_key = get_battle_key_for_user(self.channel_id, self.user)
        if not battle_key or battle_key not in battles:
            return await safe_respond(interaction, "❌ Battle already ended.")
        attacker, defender = get_fighters(self.user, battle)
        battles.pop(battle_key, None)
        bets = active_bets.pop(self.channel_id, {})
        guild_id = battle.get("guild_id", 0)
        for bettor_id, bet in bets.items():
            add_pounds(bettor_id, bet["amount"], guild_id=guild_id)
        loser_seq = get_seq_number(attacker.role) if attacker.role else 9
        winner_seq = get_seq_number(defender.role) if defender.role else 9
        # No soli penalty for surrender — just XP
        record_battle(defender.user, attacker.user, winner_seq, loser_seq, guild_id)
        guild = defender.user.guild if hasattr(defender.user, "guild") else None
        bot.loop.create_task(penalize_combat_xp(attacker.user, loser_seq, guild))
        xp_penalty = SEQ_LOSS_XP.get(loser_seq, 60)
        embed = discord.Embed(
            title="🏳️ Surrender",
            description=(
                f"**{attacker.user.display_name}** raises the white flag.\n\n"
                f"**{defender.user.display_name}** wins by surrender!\n"
                f"*(No soli penalty — {attacker.user.display_name} loses **{xp_penalty} EXP**)*\n"
                f"💰 Bets refunded."
            ),
            color=0x95A5A6
        )
        channel = bot.get_channel(self.channel_id)
        if channel:
            old_msg = battle.get("battle_message")
            if old_msg:
                try: await old_msg.delete()
                except: logger.debug("[views] Could not delete old battle message", exc_info=True)
            await channel.send(
                content=f"{attacker.user.mention} {defender.user.mention}",
                embed=embed
            )
        await interaction.response.send_message("🏳️ You have surrendered.", ephemeral=True)

    @discord.ui.button(label="❌ Cancel", style=discord.ButtonStyle.secondary)
    async def cancel_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        await interaction.response.send_message("Surrender cancelled.", ephemeral=True)


class FleeConfirmView(discord.ui.View):
    """No-drawback escape — only ever shown to Fool/Error/Door fighters (see BattleView)."""
    def __init__(self, user: discord.Member, battle: dict, channel_id: int):
        super().__init__(timeout=20)
        self.user = user
        self.battle = battle
        self.channel_id = channel_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user != self.user:
            await safe_respond(interaction, "❌ Not your flee!")
            return False
        return True

    @discord.ui.button(label="🏃 Confirm Flee", style=discord.ButtonStyle.success)
    async def confirm_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        battle = self.battle
        battle_key = get_battle_key_for_user(self.channel_id, self.user)
        if not battle_key or battle_key not in battles:
            return await safe_respond(interaction, "❌ Battle already ended.")
        attacker, defender = get_fighters(self.user, battle)
        battles.pop(battle_key, None)
        bets = active_bets.pop(self.channel_id, {})
        guild_id = battle.get("guild_id", 0)
        for bettor_id, bet in bets.items():
            add_pounds(bettor_id, bet["amount"], guild_id=guild_id)
        # No soli penalty, no EXP penalty, no win/loss recorded — a clean escape
        embed = discord.Embed(
            title="🏃 Flee",
            description=(
                f"**{attacker.user.display_name}** slips away without a trace!\n\n"
                f"*(No soli lost, no EXP lost — nothing recorded)*\n"
                f"💰 Bets refunded."
            ),
            color=0x2ECC71
        )
        channel = bot.get_channel(self.channel_id)
        if channel:
            old_msg = battle.get("battle_message")
            if old_msg:
                try: await old_msg.delete()
                except: logger.debug("[views] Could not delete old battle message", exc_info=True)
            await channel.send(
                content=f"{attacker.user.mention} {defender.user.mention}",
                embed=embed
            )
        await interaction.response.send_message("🏃 You fled the fight — no penalty.", ephemeral=True)

    @discord.ui.button(label="❌ Cancel", style=discord.ButtonStyle.secondary)
    async def cancel_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        await interaction.response.send_message("Flee cancelled.", ephemeral=True)



def _build_pathway_page(attacker: "Fighter", pages: list, idx: int) -> dict:
    """Build the content dict for a pathway page."""
    title, pairs = pages[idx]
    sp_now = attacker.sp
    lines = [f"**🌟 {title}** — SP: `{sp_now}/{attacker.max_sp}`\n"]
    for role_name, ak in pairs:
        info, _ = get_ability_info(ak)
        if not info:
            continue
        if role_name == "_prometheus_stolen":
            cost = attacker.get_ability_cost(info["cost"], ak)
            affd = "✅" if attacker.sp >= cost else "❌"
            lines.append(f"🔥 {affd} **{info['name']}** — `{cost} SP` *(Stolen — 1 use)*\n> {info['description']}")
        elif role_name == "_scribe_copy":
            lines.append(f"📖 ✅ **{info['name']}** — `Free` *(Copied — 1 use)*\n> {info['description']}")
        elif role_name == "_polymath_use":
            cost = attacker.get_ability_cost(info["cost"], ak)
            affd = "✅" if attacker.sp >= cost else "❌"
            lines.append(f"📚 {affd} **{info['name']}** — `{cost} SP` *(Studied — 70% potency)*\n> {info['description']}")
        else:
            seq_num = get_seq_number(role_name)
            if ak == attacker.role_ability and attacker.role_ability_disabled > 0:
                lines.append(f"**Seq {seq_num}** — ~~{info['name']}~~ `[Stripped]`")
                continue
            cost = attacker.get_ability_cost(info["cost"], ak)
            cd_str = _check_cooldown(attacker, ak)
            cost_label = info.get("display_cost") or f"{cost} SP"
            affd = "✅" if (info.get("display_cost") or attacker.sp >= cost) else "❌"
            cd_tag = " *(cd)*" if cd_str else ""
            lines.append(f"**Seq {seq_num}** — {affd} **{info['name']}** — `{cost_label}`{cd_tag}\n> {get_scaled_description(ak, attacker)}")
    return {"content": "\n".join(lines)}


class PathwayPageView(discord.ui.View):
    """Paginated pathway view — dropdown to select pathway, then ability buttons."""
    async def on_error(self, interaction: discord.Interaction, error: Exception, item) -> None:
        await _view_on_error(interaction, error, item)

    def __init__(self, attacker: "Fighter", pages: list, page_idx: int):
        super().__init__(timeout=45)
        self.attacker = attacker
        self.pages = pages
        self.page_idx = page_idx
        self._build()

    def _build(self):
        self.clear_items()
        _, pairs = self.pages[self.page_idx]

        # Only show dropdown if there are 2+ pathways
        if len(self.pages) > 1:
            options = []
            for i, (title, _) in enumerate(self.pages):
                # Extract pathway name from title like "[Fool] Pathway" or "✨ Special"
                pathway_name = None
                if title.startswith("[") and "] Pathway" in title:
                    pathway_name = title[1:title.index("]")]
                emoji = None
                if pathway_name:
                    guild = getattr(self.attacker.user, "guild", None)
                    if guild:
                        emoji = get_pathway_emoji(guild, pathway_name)
                options.append(discord.SelectOption(
                    label=title[:100],
                    value=str(i),
                    default=(i == self.page_idx),
                    emoji=emoji
                ))
            select = discord.ui.Select(
                placeholder="Select Pathway…",
                options=options,
                custom_id="pp_select",
                row=4
            )
            select.callback = self._select_cb
            self.add_item(select)

        # Ability buttons (max 20 to stay under Discord's 25-item limit)
        added = 0
        for role_name, ak in pairs:
            if added >= 20:
                break
            info, _ = get_ability_info(ak)
            if not info:
                continue
            if role_name not in ("_prometheus_stolen", "_scribe_copy", "_polymath_use"):
                if ak == self.attacker.role_ability and self.attacker.role_ability_disabled > 0:
                    continue
            cd_str = "" if role_name.startswith("_") else _check_cooldown(self.attacker, ak)
            if role_name == "_prometheus_stolen":
                label = f"🔥 {info['name']}"[:80]
            elif role_name == "_scribe_copy":
                label = f"📖 {info['name']}"[:80]
            elif role_name == "_polymath_use":
                label = f"📚 {info['name']}"[:80]
            else:
                seq_num = get_seq_number(role_name)
                label = f"Seq {seq_num} — {info['name']}"[:80]
            btn = discord.ui.Button(
                label=label,
                style=discord.ButtonStyle.success if not cd_str else discord.ButtonStyle.secondary,
                custom_id=f"ppv_{ak}_{role_name[:10]}",
                disabled=bool(cd_str),
                row=min(3, added // 5)
            )
            btn.callback = self._ability_cb(ak, role_name)
            self.add_item(btn)
            added += 1

    async def _select_cb(self, interaction: discord.Interaction):
        self.page_idx = int(interaction.data["values"][0])
        self._build()
        await interaction.response.edit_message(
            **_build_pathway_page(self.attacker, self.pages, self.page_idx),
            view=self
        )

    def _ability_cb(self, ability_key: str, role_name: str):
        async def callback(interaction: discord.Interaction):
            self.stop()
            battle = get_battle(interaction.channel.id, interaction.user)
            attacker, _ = get_fighters(interaction.user, battle)
            if role_name == "_prometheus_stolen":
                attacker.prometheus_stolen_ability = None
                await process_action(interaction, "ability", ability_key)
                return
            if role_name == "_scribe_copy":
                attacker.scribe_copied_ability = None
                attacker.scribe_copy_pending = True
                await process_action(interaction, "ability", ability_key)
                return
            if role_name == "_polymath_use":
                attacker.polymath_pending = True   # flag 70% potency for this turn
                attacker.polymath_cooldown = 2
                attacker.polymath_ability = None
                await process_action(interaction, "ability", ability_key)
                return
            if ability_key == "ritual":         return await show_ritual_menu(interaction)
            if ability_key == "memorise":       return await show_memorise_menu(interaction)
            if ability_key == "magic_trick":    return await show_magic_trick_menu(interaction)
            if ability_key == "bribe":          return await show_bribe_menu(interaction)
            if ability_key == "instigation":    return await show_provocation_menu(interaction, block=False)
            if ability_key == "reading":        return await show_reading_menu(interaction)
            if ability_key == "witch_curse":    return await show_witch_curse_menu(interaction)
            if ability_key == "analyse":        return await show_analyse_menu(interaction)
            if ability_key == "hypnotist":      return await show_hypnotist_menu(interaction)
            if ability_key == "notary":         return await show_notary_menu(interaction)
            if ability_key == "rose_bishop":    return await show_rose_bishop_menu(interaction)
            if ability_key == "protection":     return await show_protection_menu(interaction)
            if ability_key == "potions_professor": return await show_potions_menu(interaction)
            if ability_key == "pacification":   return await show_pacification_menu(interaction)
            await process_action(interaction, "ability", ability_key)
        return callback

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        battle = get_battle(interaction.channel.id, interaction.user)
        if not battle or interaction.user != battle["turn"]:
            await safe_respond(interaction, "❌ It's not your turn!")
            return False
        return True


class PathwayMenuView(discord.ui.View):
    """Dynamic view with one button per available pathway ability (Seq 9, Seq 8, etc.)."""
    async def on_error(self, interaction: discord.Interaction, error: Exception, item) -> None:
        await _view_on_error(interaction, error, item)

    def __init__(self, attacker: Fighter, role_ability_pairs: list):
        super().__init__(timeout=30)
        self.attacker = attacker
        for role_name, ak in role_ability_pairs:
            info, _ = get_ability_info(ak)
            if not info:
                continue
            # Special synthetic entries for Prometheus stolen / Scribe copied / Polymath studied
            if role_name == "_prometheus_stolen":
                label = f"🔥 Stolen — {info['name']}"[:80]
                cd_str = ""
            elif role_name == "_scribe_copy":
                label = f"📖 Copy — {info['name']}"[:80]
                cd_str = ""
            elif role_name == "_polymath_use":
                label = f"📚 Studied — {info['name']}"[:80]
                cd_str = ""
            else:
                seq_num = get_seq_number(role_name)
                label = f"Seq {seq_num} — {info['name']}"[:80]
                cd_str = _check_cooldown(attacker, ak)
            btn = discord.ui.Button(
                label=label,
                style=discord.ButtonStyle.success if not cd_str else discord.ButtonStyle.secondary,
                custom_id=f"pathway_{ak}_{role_name[:12]}",
                disabled=bool(cd_str)
            )
            btn.callback = self._make_callback(ak, role_name)
            self.add_item(btn)

    def _make_callback(self, ability_key: str, role_name: str = ""):
        async def callback(interaction: discord.Interaction):
            self.stop()
            battle = get_battle(interaction.channel.id, interaction.user)
            attacker, _ = get_fighters(interaction.user, battle)
            # Prometheus: consume stolen ability on use
            if role_name == "_prometheus_stolen":
                attacker.prometheus_stolen_ability = None
                await process_action(interaction, "ability", ability_key)
                return
            # Scribe: consume copied ability on use (free, skip SP check)
            if role_name == "_scribe_copy":
                attacker.scribe_copied_ability = None
                attacker.scribe_copy_pending = True
                await process_action(interaction, "ability", ability_key)
                return
            # Polymath: use studied ability at 70% potency (flag set, cleared after use)
            if role_name == "_polymath_use":
                attacker.polymath_pending = True   # flag 70% potency for this turn
                attacker.polymath_cooldown = 2
                attacker.polymath_ability = None
                await process_action(interaction, "ability", ability_key)
                return
            # Sub-menus that need a picker first
            if ability_key == "ritual":
                return await show_ritual_menu(interaction)
            if ability_key == "memorise":
                return await show_memorise_menu(interaction)
            if ability_key == "magic_trick":
                return await show_magic_trick_menu(interaction)
            if ability_key == "bribe":
                return await show_bribe_menu(interaction)
            if ability_key == "instigation":
                return await show_provocation_menu(interaction, block=False)
            if ability_key == "reading":
                return await show_reading_menu(interaction)
            if ability_key == "witch_curse":
                return await show_witch_curse_menu(interaction)
            if ability_key == "analyse":
                return await show_analyse_menu(interaction)
            if ability_key == "hypnotist":
                return await show_hypnotist_menu(interaction)
            if ability_key == "notary":
                return await show_notary_menu(interaction)
            if ability_key == "rose_bishop":
                return await show_rose_bishop_menu(interaction)
            if ability_key == "potions_professor":
                return await show_potions_menu(interaction)
            if ability_key == "pacification":
                return await show_pacification_menu(interaction)
            await process_action(interaction, "ability", ability_key)
        return callback

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        battle = get_battle(interaction.channel.id, interaction.user)
        if not battle or interaction.user != battle["turn"]:
            await safe_respond(interaction, "❌ It's not your turn!")
            return False
        return True


# Keep PathwayUseView as alias for backwards compat
PathwayUseView = PathwayMenuView


class AbilityView(discord.ui.View):
    def __init__(self, attacker: Fighter, owned_spells: list):
        super().__init__(timeout=30)
        for key in owned_spells:
            info, _ = get_ability_info(key)
            if not info:
                continue
            btn = discord.ui.Button(
                label=info["name"][:80],
                style=discord.ButtonStyle.primary,
                custom_id=f"ability_{key}"
            )
            btn.callback = self._make_callback(key)
            self.add_item(btn)

    async def on_error(self, interaction: discord.Interaction, error: Exception, item) -> None:
        await _view_on_error(interaction, error, item)

    def _make_callback(self, spell_key):
        async def callback(interaction: discord.Interaction):
            if spell_key == "ritual":
                await show_ritual_menu(interaction)
            elif spell_key == "memorise":
                await show_memorise_menu(interaction)
            else:
                await process_action(interaction, "ability", spell_key)
        return callback

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        battle = get_battle(interaction.channel.id, interaction.user)
        if not battle or interaction.user != battle["turn"]:
            await safe_respond(interaction, "❌ It's not your turn!")
            return False
        return True



class RitualView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=30)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        battle = get_battle(interaction.channel.id, interaction.user)
        if not battle or interaction.user != battle["turn"]:
            await safe_respond(interaction, "❌ It's not your turn!")
            return False
        return True

    @discord.ui.button(label="💪 Strength", style=discord.ButtonStyle.danger)
    async def strength_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await process_action(interaction, "ability", "ritual", ritual_choice="strength")

    @discord.ui.button(label="🔮 Divination", style=discord.ButtonStyle.primary)
    async def divination_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await process_action(interaction, "ability", "ritual", ritual_choice="divination")

    @discord.ui.button(label="🌿 Blessing", style=discord.ButtonStyle.success)
    async def blessing_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await process_action(interaction, "ability", "ritual", ritual_choice="blessing")


class MemoriseView(discord.ui.View):
    async def on_error(self, interaction: discord.Interaction, error: Exception, item) -> None:
        await _view_on_error(interaction, error, item)

    def __init__(self, attacker: Fighter, guild_id=0):
        super().__init__(timeout=60)
        self.attacker = attacker
        # Include purchased spells AND all unlocked pathway abilities
        owned = list(get_economy(attacker.user.id, guild_id=guild_id)["spells"])
        for role_name in attacker.all_roles:
            ak = ROLE_ABILITIES.get(role_name)
            if ak and ak not in owned and ak != "memorise":
                owned.append(ak)
        added = 0
        for key in owned:
            if key == "memorise":
                continue
            info, _ = get_ability_info(key)
            if not info:
                continue
            if added >= 24:  # Discord limit
                break
            btn = discord.ui.Button(
                label=info["name"][:80],
                style=discord.ButtonStyle.primary,
                custom_id=f"mem_{key}"
            )
            btn.callback = self._make_callback(key)
            self.add_item(btn)
            added += 1

    def _make_callback(self, spell_key):
        async def callback(interaction: discord.Interaction):
            if interaction.user != self.attacker.user:
                return await safe_respond(interaction, "❌ This isn't yours!")
            self.attacker.memorise_ability = spell_key
            self.attacker.memorise_turns = random.randint(1, 2)
            info, _ = get_ability_info(spell_key)
            self.stop()
            await interaction.response.send_message(
                f"📖 Memorised **{info['name']}** — SP-free for **{self.attacker.memorise_turns}** turn(s)!\n"
                f"Use it via your Pathway or Abilities menu — cost will show as **0 SP**.",
                ephemeral=True)
        return callback


class MagicTrickView(discord.ui.View):
    """Sub-menu for Trickmaster magic trick."""
    async def on_error(self, interaction: discord.Interaction, error: Exception, item) -> None:
        await _view_on_error(interaction, error, item)

    def __init__(self):
        super().__init__(timeout=30)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        battle = get_battle(interaction.channel.id, interaction.user)
        if not battle or interaction.user != battle["turn"]:
            await safe_respond(interaction, "❌ It's not your turn!")
            return False
        return True

    @discord.ui.button(label="🎲 Random Element", style=discord.ButtonStyle.primary)
    async def random_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await process_action(interaction, "ability", "magic_trick", ritual_choice="random")

    @discord.ui.button(label="🏃 Escape Trick", style=discord.ButtonStyle.secondary)
    async def escape_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await process_action(interaction, "ability", "magic_trick", ritual_choice="escape")


class BribeView(discord.ui.View):
    """Sub-menu for Briber — player chooses which bribe effect to apply."""
    async def on_error(self, interaction: discord.Interaction, error: Exception, item) -> None:
        await _view_on_error(interaction, error, item)

    def __init__(self):
        super().__init__(timeout=30)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        battle = get_battle(interaction.channel.id, interaction.user)
        if not battle or interaction.user != battle["turn"]:
            await safe_respond(interaction, "❌ It's not your turn!")
            return False
        return True

    @discord.ui.button(label="⚔️ Weaken (−40% next attack)", style=discord.ButtonStyle.danger)
    async def weaken_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await process_action(interaction, "ability", "bribe", ritual_choice="weaken")

    @discord.ui.button(label="💋 Charm (can't attack 2 turns)", style=discord.ButtonStyle.primary)
    async def charm_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await process_action(interaction, "ability", "bribe", ritual_choice="charm")

    @discord.ui.button(label="🔗 Connect (share 40% dmg taken)", style=discord.ButtonStyle.secondary)
    async def connect_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await process_action(interaction, "ability", "bribe", ritual_choice="connect")


class ProvocationView(discord.ui.View):
    """Shows opponent's moves so Provoker/Instigator can pick one."""
    async def on_error(self, interaction: discord.Interaction, error: Exception, item) -> None:
        await _view_on_error(interaction, error, item)

    def __init__(self, attacker: Fighter, defender: Fighter, battle, block: bool):
        super().__init__(timeout=30)
        self.attacker = attacker
        self.defender = defender
        self.block = block  # True = provocation (block), False = instigation (force)
        self.seq_blocked = False

        # LOTM rule: you cannot instigate/provoke someone more than 2 sequences
        # stronger than you. Sequence numbers are inverted — lower = stronger.
        # e.g. attacker Seq 8, defender Seq 6 → gap = 2, borderline OK
        #      attacker Seq 8, defender Seq 5 → gap = 3, BLOCKED
        atk_seq = get_seq_number(attacker.role) if attacker.role else 9
        def_seq = get_seq_number(defender.role) if defender.role else 9
        seq_gap = atk_seq - def_seq  # positive = attacker is weaker (higher number)
        if seq_gap > 2:
            self.seq_blocked = True
            return  # no buttons added — handled in process_action

        actions = get_available_actions(defender, battle)
        # Only show abilities the defender can actually use at their sequence level
        # Filter out abilities from sequences stronger than the defender's current seq
        filtered = []
        for key in actions:
            if key in ("attack", "special"):
                filtered.append(key)
                continue
            info, is_role = get_ability_info(key)
            if not info:
                continue
            # Only include abilities that belong to defender's current seq or weaker
            if is_role:
                # Check the role name sequence number
                for role_name in defender.all_roles:
                    if ROLE_ABILITIES.get(role_name) == key or ROLE_ABILITIES_2.get(role_name) == key:
                        # Extract seq number from role name e.g. "Seq 8"
                        import re
                        m = re.search(r'Seq (\d+)', role_name)
                        if m and int(m.group(1)) >= def_seq:
                            filtered.append(key)
                        break
            else:
                filtered.append(key)  # purchased spells always includeable

        for key in filtered[:5]:
            info, is_role = get_ability_info(key)
            label = info["name"][:40] if info else key
            if key == "attack":
                label = "⚔️ Attack"
            elif key == "special":
                label = "⚡ Strike"
            btn = discord.ui.Button(
                label=label,
                style=discord.ButtonStyle.danger if block else discord.ButtonStyle.primary,
                custom_id=f"prov_{key}"
            )
            btn.callback = self._make_callback(key)
            self.add_item(btn)

    def _make_callback(self, action_key: str):
        async def callback(interaction: discord.Interaction):
            if interaction.user != self.attacker.user:
                return await safe_respond(interaction, "❌ This isn't yours!")
            self.stop()
            if self.block:
                await process_action(interaction, "ability", "provocation", ritual_choice=action_key)
            else:
                await process_action(interaction, "ability", "instigation", ritual_choice=action_key)
        return callback

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        battle = get_battle(interaction.channel.id, interaction.user)
        if not battle or interaction.user != battle["turn"]:
            await safe_respond(interaction, "❌ It's not your turn!")
            return False
        return True


class ReadingView(discord.ui.View):
    """Telepathist picks which move they think opponent will use."""
    async def on_error(self, interaction: discord.Interaction, error: Exception, item) -> None:
        await _view_on_error(interaction, error, item)

    def __init__(self, attacker: Fighter, defender: Fighter, battle):
        super().__init__(timeout=30)
        self.attacker = attacker
        actions = get_available_actions(defender, battle)
        for key in actions[:5]:
            info, _ = get_ability_info(key)
            label = info["name"][:40] if info else key
            if key == "attack":
                label = "⚔️ Attack"
            elif key == "special":
                label = "⚡ Strike"
            btn = discord.ui.Button(
                label=label,
                style=discord.ButtonStyle.primary,
                custom_id=f"read_{key}"
            )
            btn.callback = self._make_callback(key)
            self.add_item(btn)

    def _make_callback(self, action_key: str):
        async def callback(interaction: discord.Interaction):
            if interaction.user != self.attacker.user:
                return await safe_respond(interaction, "❌ This isn't yours!")
            self.stop()
            await process_action(interaction, "ability", "reading", ritual_choice=action_key)
        return callback

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        battle = get_battle(interaction.channel.id, interaction.user)
        if not battle or interaction.user != battle["turn"]:
            await safe_respond(interaction, "❌ It's not your turn!")
            return False
        return True


async def show_ritual_menu(interaction: discord.Interaction):
    battle = get_battle(interaction.channel.id, interaction.user)
    attacker, _ = get_fighters(interaction.user, battle)
    ab = ABILITIES["ritual"]
    actual_cost = attacker.get_ability_cost(ab["cost"], "ritual")
    if attacker.sp < actual_cost:
        return await interaction.response.send_message(
            f"❌ Not enough spirit! Costs **{actual_cost} SP** — you have **{attacker.sp} SP**.", ephemeral=True)
    await interaction.response.send_message(
        f"**🕯️ Ritualistic Magic** — `{actual_cost} SP`\n\n"
        f"💪 **Strength** — Boost your next attack.\n"
        f"🔮 **Divination** — High dodge chance for next incoming attack.\n"
        f"🌿 **Blessing** — Heal now, gain strength next turn.",
        view=RitualView(), ephemeral=True)


# ── Seq 6 sub-menu views ──────────────────────────────────

class HypnotistView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=30)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        battle = get_battle(interaction.channel.id, interaction.user)
        if not battle or interaction.user != battle["turn"]:
            await safe_respond(interaction, "❌ It's not your turn!")
            return False
        return True

    @discord.ui.button(label="💥 Self-Damage (20)", style=discord.ButtonStyle.danger)
    async def self_dmg_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await process_action(interaction, "ability", "hypnotist", ritual_choice="self_damage")

    @discord.ui.button(label="⏭️ Skip Turn", style=discord.ButtonStyle.secondary)
    async def skip_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await process_action(interaction, "ability", "hypnotist", ritual_choice="skip_turn")


class NotaryView(discord.ui.View):
    async def on_error(self, interaction: discord.Interaction, error: Exception, item) -> None:
        await _view_on_error(interaction, error, item)

    def __init__(self):
        super().__init__(timeout=30)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        battle = get_battle(interaction.channel.id, interaction.user)
        if not battle or interaction.user != battle["turn"]:
            await safe_respond(interaction, "❌ It's not your turn!")
            return False
        return True

    @discord.ui.button(label="💪 Buff Self (+50% dmg)", style=discord.ButtonStyle.success)
    async def buff_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await process_action(interaction, "ability", "notary", ritual_choice="buff_self")

    @discord.ui.button(label="🔻 Debuff Opponent (−50%)", style=discord.ButtonStyle.danger)
    async def debuff_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await process_action(interaction, "ability", "notary", ritual_choice="debuff_opponent")


class RoseBishopView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=30)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        battle = get_battle(interaction.channel.id, interaction.user)
        if not battle or interaction.user != battle["turn"]:
            await safe_respond(interaction, "❌ It's not your turn!")
            return False
        return True

    @discord.ui.button(label="💣 Flesh Bomb (30 dmg, −20 HP, −15 SP)", style=discord.ButtonStyle.danger)
    async def flesh_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await process_action(interaction, "ability", "rose_bishop", ritual_choice="flesh_bomb")

    @discord.ui.button(label="💉 Blood Regen (+40 HP, −25 SP)", style=discord.ButtonStyle.primary)
    async def regen_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await process_action(interaction, "ability", "rose_bishop", ritual_choice="blood_regen")


class ProtectionView(discord.ui.View):
    async def on_error(self, interaction: discord.Interaction, error: Exception, item) -> None:
        await _view_on_error(interaction, error, item)

    def __init__(self):
        super().__init__(timeout=30)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        battle = get_battle(interaction.channel.id, interaction.user)
        if not battle or interaction.user != battle["turn"]:
            await safe_respond(interaction, "❌ It's not your turn!")
            return False
        return True

    @discord.ui.button(label="⚔️ Active (until next attack)", style=discord.ButtonStyle.primary)
    async def active_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await process_action(interaction, "ability", "protection", ritual_choice="active")

    @discord.ui.button(label="🔄 Maintenance (toggle, 3 turn cd)", style=discord.ButtonStyle.secondary)
    async def maintenance_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await process_action(interaction, "ability", "protection", ritual_choice="maintenance")


class PotionsView(discord.ui.View):
    async def on_error(self, interaction: discord.Interaction, error: Exception, item) -> None:
        await _view_on_error(interaction, error, item)

    def __init__(self):
        super().__init__(timeout=30)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        battle = get_battle(interaction.channel.id, interaction.user)
        if not battle or interaction.user != battle["turn"]:
            await safe_respond(interaction, "❌ It's not your turn!")
            return False
        return True

    @discord.ui.button(label="🧪 Cure (Heal HP/SP)", style=discord.ButtonStyle.success)
    async def cure_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await process_action(interaction, "ability", "potions_professor", ritual_choice="cure")

    @discord.ui.button(label="☠️ Poison (50 dmg + −20 SP)", style=discord.ButtonStyle.danger)
    async def poison_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await process_action(interaction, "ability", "potions_professor", ritual_choice="poison")


async def show_hypnotist_menu(interaction: discord.Interaction):
    battle = get_battle(interaction.channel.id, interaction.user)
    attacker, _ = get_fighters(interaction.user, battle)
    info = ROLE_ABILITY_INFO["hypnotist"]
    cost = attacker.get_ability_cost(info["cost"], "hypnotist")
    if attacker.sp < cost:
        return await interaction.response.send_message(
            f"❌ Not enough spirit! Costs **{cost} SP**.", ephemeral=True)
    await process_action(interaction, "ability", "hypnotist", ritual_choice="both")


async def show_notary_menu(interaction: discord.Interaction):
    battle = get_battle(interaction.channel.id, interaction.user)
    attacker, _ = get_fighters(interaction.user, battle)
    info = ROLE_ABILITY_INFO["notary"]
    cost = attacker.get_ability_cost(info["cost"], "notary")
    if attacker.sp < cost:
        return await interaction.response.send_message(
            f"❌ Not enough spirit! Costs **{cost} SP**.", ephemeral=True)
    await interaction.response.send_message(
        f"**📜 Notary** — `{cost} SP`\n\n"
        f"Proclaim in the name of your god — choose your decree:",
        view=NotaryView(), ephemeral=True)


async def show_rose_bishop_menu(interaction: discord.Interaction):
    battle = get_battle(interaction.channel.id, interaction.user)
    attacker, _ = get_fighters(interaction.user, battle)
    info = ROLE_ABILITY_INFO["rose_bishop"]
    cost = attacker.get_ability_cost(0, "rose_bishop")  # cost varies by choice
    await interaction.response.send_message(
        f"**🌹 Rose Bishop** — Flesh & Blood Magic:\n\n"
        f"💣 **Flesh Bomb** — 30 dmg, costs **20 HP + 15 SP**\n"
        f"💉 **Blood Regen** — restore 40 SP, costs **25 SP**",
        view=RoseBishopView(), ephemeral=True)


async def show_protection_menu(interaction: discord.Interaction):
    battle = get_battle(interaction.channel.id, interaction.user)
    attacker, _ = get_fighters(interaction.user, battle)
    if attacker.protection_active:
        # Already active — handle toggle/cancel directly without showing menu
        await process_action(interaction, "ability", "protection")
        return
    if attacker.protection_cooldown > 0:
        await interaction.response.send_message(
            f"🛡️ **Protection** on cooldown for **{attacker.protection_cooldown}** turn(s)!", ephemeral=True)
        return
    cost = attacker.get_ability_cost(40, "protection")
    if attacker.sp < cost:
        return await interaction.response.send_message(
            f"❌ Not enough spirit! Costs **{cost} SP**.", ephemeral=True)
    reduction = seq_val(attacker, 80)
    await interaction.response.send_message(
        f"**🛡️ Protection** — `{cost} SP`\n\n"
        f"Give up offense (−50% damage dealt) in exchange for **{reduction} damage reduction** "
        f"(drops to **{seq_val(attacker, 10)}** for 1 turn after any attack).\n\n"
        f"Choose your mode:",
        view=ProtectionView(), ephemeral=True)


async def show_potions_menu(interaction: discord.Interaction):
    battle = get_battle(interaction.channel.id, interaction.user)
    attacker, defender = get_fighters(interaction.user, battle)
    info = ROLE_ABILITY_INFO["potions_professor"]
    cost = attacker.get_ability_cost(info["cost"], "potions_professor")
    if attacker.sp < cost:
        return await interaction.response.send_message(
            f"❌ Not enough spirit! Costs **{cost} SP**.", ephemeral=True)
    await interaction.response.send_message(
        f"**⚗️ Potions Professor** — `{cost} SP`\n\n"
        f"🧪 **Cure** — Restore HP & SP (more if no status effects)\n"
        f"☠️ **Poison** — Deal 50 damage + drain 20 SP from **{defender.user.display_name}**",
        view=PotionsView(), ephemeral=True)


class PacificationView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=30)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        battle = get_battle(interaction.channel.id, interaction.user)
        if not battle or interaction.user != battle["turn"]:
            await safe_respond(interaction, "❌ It's not your turn!")
            return False
        return True

    @discord.ui.button(label="💥 Strip Enemy Buffs", style=discord.ButtonStyle.danger)
    async def strip_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await process_action(interaction, "ability", "pacification", ritual_choice="strip_enemy")

    @discord.ui.button(label="✨ Cleanse My Debuffs", style=discord.ButtonStyle.success)
    async def cleanse_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await process_action(interaction, "ability", "pacification", ritual_choice="cleanse_self")


async def show_pacification_menu(interaction: discord.Interaction):
    battle = get_battle(interaction.channel.id, interaction.user)
    attacker, _ = get_fighters(interaction.user, battle)
    info = ROLE_ABILITY_INFO["pacification"]
    cost = attacker.get_ability_cost(info["cost"], "pacification")
    if attacker.sp < cost:
        return await interaction.response.send_message(
            f"❌ Not enough spirit! Costs **{cost} SP**.", ephemeral=True)
    await interaction.response.send_message(
        f"**☮️ Pacification** — `{cost} SP`\n\n"
        f"💥 **Strip Enemy Buffs** — Remove all active positive effects from your opponent.\n"
        f"✨ **Cleanse My Debuffs** — Clear all negative status effects from yourself.\n\n"
        f"*(6 turn cooldown)*",
        view=PacificationView(), ephemeral=True)


class JudgeView(discord.ui.View):
    """Judge picks one of opponent's moves to restrict."""
    async def on_error(self, interaction: discord.Interaction, error: Exception, item) -> None:
        await _view_on_error(interaction, error, item)

    def __init__(self, attacker: Fighter, defender: Fighter, battle):
        super().__init__(timeout=30)
        self.attacker = attacker
        actions = get_available_actions(defender, battle)
        for key in actions[:5]:
            info, _ = get_ability_info(key)
            if key == "attack":
                label = "⚔️ Attack"
            elif key == "special":
                label = "⚡ Strike"
            else:
                label = info["name"][:40] if info else key
            btn = discord.ui.Button(
                label=label,
                style=discord.ButtonStyle.danger,
                custom_id=f"judge_{key}"
            )
            btn.callback = self._make_callback(key)
            self.add_item(btn)

    def _make_callback(self, action_key: str):
        async def callback(interaction: discord.Interaction):
            if interaction.user != self.attacker.user:
                return await safe_respond(interaction, "❌ This isn't yours!")
            self.stop()
            await process_action(interaction, "ability", "judge", ritual_choice=action_key)
        return callback

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        battle = get_battle(interaction.channel.id, interaction.user)
        if not battle or interaction.user != battle["turn"]:
            await safe_respond(interaction, "❌ It's not your turn!")
            return False
        return True


async def show_judge_menu(interaction: discord.Interaction):
    battle = get_battle(interaction.channel.id, interaction.user)
    attacker, defender = get_fighters(interaction.user, battle)
    info = ROLE_ABILITY_INFO["judge"]
    cost = attacker.get_ability_cost(info["cost"], "judge")
    if attacker.sp < cost:
        return await interaction.response.send_message(
            f"❌ Not enough spirit! Costs **{cost} SP**.", ephemeral=True)
    await interaction.response.send_message(
        f"**⚖️ Judge** — Pick a move to restrict on **{defender.user.display_name}**:\n"
        f"*(If they use it anyway, they take **30 damage** as backfire)*",
        view=JudgeView(attacker, defender, battle), ephemeral=True)


async def show_memorise_menu(interaction: discord.Interaction):
    battle = get_battle(interaction.channel.id, interaction.user)
    attacker, _ = get_fighters(interaction.user, battle)
    if attacker.memorise_ability and attacker.memorise_turns > 0:
        info, _ = get_ability_info(attacker.memorise_ability)
        return await interaction.response.send_message(
            f"📖 Already memorised **{info['name']}** — **{attacker.memorise_turns}** SP-free turn(s) left.",
            ephemeral=True)
    await interaction.response.send_message(
        "**📖 Memorise** — Pick an ability to use SP-free for 1-2 turns:",
        view=MemoriseView(attacker, guild_id=interaction.guild_id), ephemeral=True)


async def show_magic_trick_menu(interaction: discord.Interaction):
    battle = get_battle(interaction.channel.id, interaction.user)
    attacker, _ = get_fighters(interaction.user, battle)
    info = ROLE_ABILITY_INFO["magic_trick"]
    cost = attacker.get_ability_cost(info["cost"], "magic_trick")
    if attacker.sp < cost:
        return await interaction.response.send_message(
            f"❌ Not enough spirit! Costs **{cost} SP** — you have **{attacker.sp} SP**.", ephemeral=True)
    await interaction.response.send_message(
        f"**🎩 Magic Trick** — `{cost} SP`\n\n"
        f"🎲 **Random Element** — Apply freeze, burn, or stun at random for 2 turns.\n"
        f"🏃 **Escape Trick** — Attempt to flee based on speed difference.",
        view=MagicTrickView(), ephemeral=True)


async def show_bribe_menu(interaction: discord.Interaction):
    battle = get_battle(interaction.channel.id, interaction.user)
    attacker, defender = get_fighters(interaction.user, battle)
    if attacker.bribe_cooldown > 0:
        return await interaction.response.send_message(
            f"❌ **Bribe** is on cooldown for **{attacker.bribe_cooldown}** more turn(s)!", ephemeral=True)
    cost_soli = 70000
    bal = get_pounds(attacker.user.id, guild_id=battle["guild_id"])
    if bal < cost_soli:
        return await interaction.response.send_message(
            f"❌ **Bribe** needs **{cost_soli:,} soli** — you only have **{bal:,}**.", ephemeral=True)
    spirit_diff = max(0, defender.stats.get("spirit", 0) - attacker.stats.get("spirit", 0))
    fail_pct = int(spirit_diff * 10)
    await interaction.response.send_message(
        f"**💰 Bribe** — `100,000 soli`\n\n"
        f"Choose your bribe for **{defender.user.display_name}**:\n"
        f"*(Fail chance: **{fail_pct}%** based on their spirit stat)*",
        view=BribeView(), ephemeral=True)


async def show_provocation_menu(interaction: discord.Interaction, block: bool):
    battle = get_battle(interaction.channel.id, interaction.user)
    attacker, defender = get_fighters(interaction.user, battle)
    ability_key = "provocation" if block else "instigation"
    info = ROLE_ABILITY_INFO[ability_key]
    cost = attacker.get_ability_cost(info["cost"], ability_key)
    if block and attacker.provocation_cooldown > 0:
        return await interaction.response.send_message(
            f"❌ **Provocation** on cooldown for **{attacker.provocation_cooldown}** more turn(s)! *(4t cd)*", ephemeral=True)
    if attacker.sp < cost:
        return await interaction.response.send_message(
            f"❌ Not enough spirit! Costs **{cost} SP**.", ephemeral=True)

    # LOTM sequence gap check — can't instigate/provoke someone more than 2 seqs stronger
    atk_seq = get_seq_number(attacker.role) if attacker.role else 9
    def_seq = get_seq_number(defender.role) if defender.role else 9
    if atk_seq - def_seq > 2:
        return await interaction.response.send_message(
            f"❌ **{info['name']} failed!**\n"
            f"**{defender.user.display_name}** is **Seq {def_seq}** — the gap is too great. "
            f"A Beyonder cannot easily impose their will on someone {atk_seq - def_seq} sequences above them.",
            ephemeral=True)

    view = ProvocationView(attacker, defender, battle, block)
    action_word = "block" if block else "force"
    await interaction.response.send_message(
        f"**{info['name']}** — Pick a move to {action_word} from **{defender.user.display_name}**:\n"
        f"*(Enemy skips **1 turn** and loses **25 SP**)*",
        view=view, ephemeral=True)


async def show_reading_menu(interaction: discord.Interaction):
    battle = get_battle(interaction.channel.id, interaction.user)
    attacker, defender = get_fighters(interaction.user, battle)
    info = ROLE_ABILITY_INFO["reading"]
    cost = attacker.get_ability_cost(info["cost"], "reading")
    if attacker.sp < cost:
        return await interaction.response.send_message(
            f"❌ Not enough spirit! Costs **{cost} SP**.", ephemeral=True)
    await interaction.response.send_message(
        f"**🔭 Reading** — Which move do you think **{defender.user.display_name}** will use?\n"
        f"*(If correct, it misses)*",
        view=ReadingView(attacker, defender, battle), ephemeral=True)


class WitchCurseView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=30)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        battle = get_battle(interaction.channel.id, interaction.user)
        if not battle or interaction.user != battle["turn"]:
            await safe_respond(interaction, "❌ It's not your turn!")
            return False
        return True

    @discord.ui.button(label="🔥 Black Flames", style=discord.ButtonStyle.danger)
    async def flames_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await process_action(interaction, "ability", "witch_curse", ritual_choice="black_flames")

    @discord.ui.button(label="🧊 Frost", style=discord.ButtonStyle.primary)
    async def frost_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await process_action(interaction, "ability", "witch_curse", ritual_choice="frost")

    @discord.ui.button(label="🪆 Voodoo", style=discord.ButtonStyle.secondary)
    async def voodoo_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await process_action(interaction, "ability", "witch_curse", ritual_choice="voodoo")


class AnalyseView(discord.ui.View):
    """Pick one of defender's abilities to permanently reduce its damage by 30%."""
    async def on_error(self, interaction: discord.Interaction, error: Exception, item) -> None:
        await _view_on_error(interaction, error, item)

    def __init__(self, attacker: Fighter, defender: Fighter, battle):
        super().__init__(timeout=30)
        self.attacker = attacker
        actions = get_available_actions(defender, battle)
        # Filter to actual abilities only (exclude basic attack/special)
        ability_actions = [k for k in actions if k not in ("attack", "special")]
        added = 0
        for key in ability_actions:
            if added >= 5:
                break
            info, _ = get_ability_info(key)
            label = info["name"][:40] if info else key
            btn = discord.ui.Button(label=label, style=discord.ButtonStyle.primary, custom_id=f"analyse_{key}", row=min(4, added // 3))
            btn.callback = self._make_callback(key)
            self.add_item(btn)
            added += 1

    def _make_callback(self, action_key: str):
        async def callback(interaction: discord.Interaction):
            if interaction.user != self.attacker.user:
                return await safe_respond(interaction, "❌ This isn't yours!")
            self.stop()
            await process_action(interaction, "ability", "analyse", ritual_choice=action_key)
        return callback

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        battle = get_battle(interaction.channel.id, interaction.user)
        if not battle or interaction.user != battle["turn"]:
            await safe_respond(interaction, "❌ It's not your turn!")
            return False
        return True


async def show_witch_curse_menu(interaction: discord.Interaction):
    battle = get_battle(interaction.channel.id, interaction.user)
    attacker, _ = get_fighters(interaction.user, battle)
    info = ROLE_ABILITY_INFO["witch_curse"]
    cost = attacker.get_ability_cost(info["cost"], "witch_curse")
    if attacker.sp < cost:
        return await interaction.response.send_message(
            f"❌ Not enough spirit! Costs **{cost} SP**.", ephemeral=True)
    ritual_choice = random.choice(["black_flames", "frost", "voodoo"])
    await process_action(interaction, "ability", "witch_curse", ritual_choice=ritual_choice)


async def show_analyse_menu(interaction: discord.Interaction):
    battle = get_battle(interaction.channel.id, interaction.user)
    attacker, defender = get_fighters(interaction.user, battle)
    info = ROLE_ABILITY_INFO["analyse"]
    cost = attacker.get_ability_cost(info["cost"], "analyse")
    if attacker.sp < cost:
        return await interaction.response.send_message(
            f"❌ Not enough spirit! Costs **{cost} SP**.", ephemeral=True)
    if attacker.analyse_uses <= 0:
        return await interaction.response.send_message("❌ No analyse uses remaining!", ephemeral=True)
    await interaction.response.send_message(
        f"**🧐 Analyse** — Pick a skill from **{defender.user.display_name}** to permanently reduce by 30%.\n"
        f"*({attacker.analyse_uses} use(s) remaining)*",
        view=AnalyseView(attacker, defender, battle), ephemeral=True)


class ChallengeView(discord.ui.View):
    def __init__(self, challenger, opponent, channel_id):
        super().__init__(timeout=60)
        self.challenger = challenger
        self.opponent = opponent
        self.channel_id = channel_id
        self.battle_key = _battle_key(channel_id, challenger.id, opponent.id)

    async def on_timeout(self):
        battles.pop(self.battle_key, None)
        channel = bot.get_channel(self.channel_id)
        if channel:
            embed = discord.Embed(
                title="⌛ Challenge Expired",
                description=(
                    f"**{self.challenger.display_name}** waited bravely, but "
                    f"**{self.opponent.display_name}** never answered.\n"
                    f"The arena stands empty."
                ),
                color=0x95A5A6
            )
            await channel.send(embed=embed)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user != self.opponent:
            await safe_respond(interaction, "❌ This challenge isn't for you!")
            return False
        return True

    @discord.ui.button(label="✅ Accept", style=discord.ButtonStyle.success)
    async def accept_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        battle = battles.get(self.battle_key)
        if not battle or battle["accepted"]:
            return await interaction.response.send_message("❌ No pending challenge.", ephemeral=True)
        battle["accepted"] = True
        self.stop()
        first = battle["turn"]
        c = battle["challenger"]
        o = battle["opponent"]

        def fighter_intro(f):
            if f.role:
                pathway = get_pathway_name(f.role)
                seq_num = get_seq_number(f.role)
                role_short = f.role.split("—")[-1].strip() if "—" in f.role else ""
                seq_label = f"**{pathway}** Seq {seq_num} — *{role_short}*"
            else:
                seq_label = "*No pathway*"
            stats = f.stats
            return (
                f"{seq_label}\n"
                f"❤️ `{f.max_hp} HP`  ✨ `{f.max_sp} SP`  ⚔️ `{stats['attack']} Atk`  🍀 `{stats['luck']} Luck`"
            )

        embed = discord.Embed(
            title="⚔️ The Battle Begins!",
            description=f"⚡ **{first.display_name}** moves first!",
            color=0xE74C3C
        )
        embed.add_field(name=f"🔵 {c.user.display_name}", value=fighter_intro(c), inline=True)
        embed.add_field(name=f"🔴 {o.user.display_name}", value=fighter_intro(o), inline=True)
        embed.add_field(name="\u200b", value=battle_status(battle), inline=False)
        embed.set_footer(text=f"Turn 0  •  ⚡ {first.display_name}'s move  •  /leave to forfeit")
        view = _make_battle_view(battle)
        msg = await interaction.channel.send(
            content=f"{c.user.mention} {o.user.mention}",
            embed=embed,
            view=view
        )
        await interaction.response.defer()
        battle["battle_message"] = msg
        # Start inactivity timer — ends fight if nobody acts for 3 minutes
        import time
        battle["last_action_time"] = time.time()
        asyncio.create_task(inactivity_watcher(self.battle_key, self.channel_id))

    @discord.ui.button(label="❌ Decline", style=discord.ButtonStyle.danger)
    async def decline_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        battles.pop(self.battle_key, None)
        self.stop()
        await interaction.response.send_message(embed=discord.Embed(
            description=f"🏳️ **{interaction.user.display_name}** declines. Duel cancelled.",
            color=0x95A5A6
        ))


class BetView(discord.ui.View):
    def __init__(self, channel_id, challenger, opponent, bettor_id, amount, guild_id=0):
        super().__init__(timeout=30)
        self.channel_id = channel_id
        self.bettor_id = bettor_id
        self.amount = amount
        self.guild_id = guild_id
        btn1 = discord.ui.Button(label=f"🏆 {challenger.display_name}", style=discord.ButtonStyle.primary, custom_id="bet_c")
        btn1.callback = self._make_callback(challenger)
        self.add_item(btn1)
        btn2 = discord.ui.Button(label=f"🏆 {opponent.display_name}", style=discord.ButtonStyle.danger, custom_id="bet_o")
        btn2.callback = self._make_callback(opponent)
        self.add_item(btn2)

    async def on_error(self, interaction: discord.Interaction, error: Exception, item) -> None:
        await _view_on_error(interaction, error, item)

    def _make_callback(self, target: discord.Member):
        async def callback(interaction: discord.Interaction):
            if interaction.user.id != self.bettor_id:
                return await safe_respond(interaction, "❌ Not your bet!")
            battle = get_battle(self.channel_id)
            if not battle or battle.get("turns", 0) >= 3:
                return await interaction.response.send_message("❌ Betting closed.", ephemeral=True)
            if self.channel_id not in active_bets:
                active_bets[self.channel_id] = {}
            if self.bettor_id in active_bets[self.channel_id]:
                return await interaction.response.send_message("❌ Already placed a bet!", ephemeral=True)
            remove_pounds(self.bettor_id, self.amount, guild_id=self.guild_id)
            active_bets[self.channel_id][self.bettor_id] = {"amount": self.amount, "target": target}
            self.stop()
            await interaction.response.send_message(
                f"✅ Bet **{self.amount:,} soli** on **{target.display_name}**!\n"
                f"💰 Remaining: **{get_pounds(self.bettor_id, guild_id=self.guild_id):,} soli**", ephemeral=True)
        return callback

# ── Action processor ──────────────────────────────────────


PATHWAY_LIST = [
    "Fool", "Door", "Error", "Hermit", "Paragon",
    "Red Priest", "Demoness", "Abyss", "Chained", "Twilight Giant",
    "Darkness", "Death", "Tyrant", "Sun", "Hanged Man",
    "White Tower", "Visionary", "Mother", "Moon",
    "Wheel of Fortune", "Black Emperor", "Justiciar",
]

def get_pathway_abilities_text(pathway: str) -> str:
    lines = [f"**{pathway} Pathway — Seq 9 to 5**\n"]

    def format_ability(ability_key: str) -> str:
        info = ROLE_ABILITY_INFO.get(ability_key) or ABILITIES.get(ability_key)
        if not info:
            return "> *(ability info not found)*\n"
        name_clean = re.sub(r'^[\U00010000-\U0010ffff\u2000-\u3300\U0001F000-\U0001FAFF\uFE0F\uFE0E]+\s*', '', info['name']).lstrip()
        cost_str = f"`{info['cost']} SP`" if info['cost'] > 0 else f"`{info.get('display_cost','Free')}`"
        return f"{name_clean} — {cost_str}\n> {info['description']}\n"

    for seq in [9, 8, 7, 6, 5]:
        role_name = None
        ability_key = None
        for rn, ak in ROLE_ABILITIES.items():
            if f"[{pathway}] Seq {seq}" in rn:
                role_name = rn
                ability_key = ak
                break
        if not role_name:
            lines.append(f"**Seq {seq}** — *(no entry)*\n")
            continue
        try:
            title = role_name.split("—")[1].strip()
        except IndexError:
            title = role_name

        block = f"**Seq {seq} — {title}**\n" + format_ability(ability_key)

        # Seq 5 is the one rank where every role gets a second ability
        if seq == 5:
            ability_key_2 = ROLE_ABILITIES_2.get(role_name)
            if ability_key_2:
                block += format_ability(ability_key_2)

        lines.append(block)
    return "\n".join(lines)


class PathwayAbilitySelectView(discord.ui.View):
    def __init__(self, guild=None):
        super().__init__(timeout=60)

        options = [
            discord.SelectOption(
                label=p,
                value=p,
                emoji=get_pathway_emoji(guild, p) if guild else None
            )
            for p in PATHWAY_LIST
        ]
        select = discord.ui.Select(
            placeholder="Choose a pathway…",
            options=options,
            custom_id="pathway_ability_select"
        )
        select.callback = self._make_callback()
        self.add_item(select)

    async def on_error(self, interaction: discord.Interaction, error: Exception, item) -> None:
        await _view_on_error(interaction, error, item)

    def _make_callback(self):
        async def callback(interaction: discord.Interaction):
            pathway = interaction.data["values"][0]
            text = get_pathway_abilities_text(pathway)
            color = pathway_colors.get(pathway, 0x9B59B6)
            embed = discord.Embed(description=text, color=color)
            embed.set_footer(text="Abilities for Seq 9, 8, 7, 6, and 5.")
            await interaction.response.send_message(embed=embed, ephemeral=True)
        return callback



class StatAllocateModal(discord.ui.Modal):
    def __init__(self, user_id: int, stat_key: str, available: int):
        label, emoji = STAT_LABELS[stat_key]
        super().__init__(title=f"Allocate {label} points")
        self.user_id = user_id
        self.stat_key = stat_key
        self.amount_input = discord.ui.TextInput(
            label=f"How many points? (1-{available})",
            placeholder=f"Enter a number up to {available}",
            default=str(available),
            required=True,
            max_length=6
        )
        self.add_item(self.amount_input)

    async def on_submit(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message("❌ Not your stat panel!", ephemeral=True)

        record = get_or_create_stats(self.user_id, interaction.user)
        available = record.get("points", 0)
        if available < 1:
            return await interaction.response.send_message("❌ You have no unspent stat points.", ephemeral=True)

        raw = self.amount_input.value.strip()
        if not raw.isdigit() or int(raw) < 1:
            return await interaction.response.send_message("❌ Enter a whole number of 1 or more.", ephemeral=True)

        amount = int(raw)
        clamp_note = ""
        if amount > available:
            clamp_note = f" *(you asked for {amount}, but only had {available} — allocated all of it)*"
            amount = available

        record[self.stat_key] += amount
        record["points"] -= amount
        save_stats()

        label, emoji = STAT_LABELS[self.stat_key]
        new_embed = build_stats_embed(interaction.user)
        new_view = StatsView(self.user_id) if record["points"] > 0 else None
        await interaction.response.edit_message(embed=new_embed, view=new_view)
        await interaction.followup.send(f"{emoji} **+{amount} {label}**!{clamp_note}", ephemeral=True)


class StatAllocateSelect(discord.ui.Select):
    def __init__(self, user_id: int):
        self.user_id = user_id
        options = [
            discord.SelectOption(label="Health", emoji="❤️", value="health", description="Increases max HP"),
            discord.SelectOption(label="Attack", emoji="🗡️", value="attack", description="Increases Hit/Special damage"),
            discord.SelectOption(label="Luck", emoji="🍀", value="luck", description="Improves crit & dodge odds"),
            discord.SelectOption(label="Spirituality", emoji="✨", value="spirituality", description="Increases SP, damage & debuff resist"),
            discord.SelectOption(label="Speed", emoji="⚡", value="speed", description="Improves turn order & evasion"),
        ]
        super().__init__(placeholder="🔧 Allocate a stat point...", options=options, min_values=1, max_values=1)

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message("❌ Not your stat panel!", ephemeral=True)

        record = get_or_create_stats(self.user_id, interaction.user)
        available = record.get("points", 0)
        if available < 1:
            return await interaction.response.send_message("❌ You have no unspent stat points.", ephemeral=True)

        stat_key = self.values[0]
        await interaction.response.send_modal(StatAllocateModal(self.user_id, stat_key, available))


class StatsView(discord.ui.View):
    def __init__(self, user_id: int):
        super().__init__(timeout=120)
        self.add_item(StatAllocateSelect(user_id))



class ItemUseView(discord.ui.View):
    def __init__(self, user_id: int, guild_id: int, item_name: str):
        super().__init__(timeout=30)
        self.user_id   = user_id
        self.guild_id  = guild_id
        self.item_name = item_name
        btn = discord.ui.Button(label=f"Use {item_name[:40]}", style=discord.ButtonStyle.success)
        btn.callback = self._use_callback
        self.add_item(btn)

    async def _use_callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message("Not yours!", ephemeral=True)
        gid  = interaction.guild_id or 0
        name = self.item_name
        if name in ALL_CHAIR_NAMES:
            removed = await db_remove_item(self.user_id, gid, name)
            if not removed:
                return await interaction.response.send_message("You don't have that item anymore!", ephemeral=True)
            data = await get_user_data(self.user_id, gid)
            if not data or not data.get("pathway"):
                return await interaction.response.send_message("You need a pathway to gain XP!", ephemeral=True)
            new_seq, new_xp, leveled = await apply_xp_gain(
                self.user_id, interaction.guild, data["pathway"],
                data["sequence"], data["xp"], CHAIR_XP,
                interaction.user.mention,
            )
            flavour = random.choice(CHAIR_USE_TEXTS).format(user=f"**{interaction.user.display_name}**")
            level_line = f"\n\n Sequence advanced! You are now **Seq {new_seq}**!" if leveled else ""
            embed = discord.Embed(
                title=f"🪑 {name}",
                description=f"{flavour}\n\n+{CHAIR_XP:,} XP gained!{level_line}",
                color=0xF1C40F,
            )
            await interaction.response.edit_message(embed=embed, view=None)
            self.stop()
            return
        if name == "Healthy Chair":
            await interaction.response.send_message(
                "🪑 **Healthy Chair** can only be used during a Pirate Hunt event to revive a fallen ally.",
                ephemeral=True)
            return
        await interaction.response.send_message(
            f"*You hold the **{name}** carefully.* It has no use effect yet — more functionality coming soon!",
            ephemeral=True)
        self.stop()


class ItemUseButton(discord.ui.Button):
    def __init__(self, owner_id: int, item_name: str, idx: int):
        self.owner_id  = owner_id
        self.item_name = item_name
        super().__init__(
            label=f"Use {item_name[:35]}",
            style=discord.ButtonStyle.primary,
        )

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.owner_id:
            return await interaction.response.send_message("Not yours!", ephemeral=True)
        await interaction.response.send_message(
            f"Use **{self.item_name}**?",
            view=ItemUseView(interaction.user.id, interaction.guild_id or 0, self.item_name),
            ephemeral=True,
        )



class SpellBuyButton(discord.ui.Button):
    """A single Buy button for one purchasable spell."""
    def __init__(self, user_id: int, guild_id: int, key: str, ab: dict, owned: bool):
        self.user_id  = user_id
        self.guild_id = guild_id
        self.key      = key
        self.ab       = ab
        label = f"{'✅' if owned else '🛒'} {ab['name'][:40]}"
        super().__init__(
            label=label,
            style=discord.ButtonStyle.secondary if owned else discord.ButtonStyle.primary,
            disabled=owned,
            row=0,
        )

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message("Not yours!", ephemeral=True)
        bal = get_pounds(self.user_id, guild_id=self.guild_id)
        price = self.ab["price"]
        if bal < price:
            return await interaction.response.send_message(
                f"❌ Need **{price:,} soli** — you only have **{bal:,}**.", ephemeral=True)
        if owns_spell(self.user_id, self.key, guild_id=self.guild_id):
            return await interaction.response.send_message(
                f"Already own **{self.ab['name']}**!", ephemeral=True)
        remove_pounds(self.user_id, price, guild_id=self.guild_id)
        buy_spell(self.user_id, self.key, guild_id=self.guild_id)
        await interaction.response.send_message(
            f"✅ Purchased **{self.ab['name']}** for **{price:,} soli**!\n"
            f"> {self.ab['description']}\n\nUse it in any battle via the **Ability** button.",
            ephemeral=True)


class ShopNavButton(discord.ui.Button):
    def __init__(self, user_id, guild_id, page, label, disabled):
        self.user_id  = user_id
        self.guild_id = guild_id
        self.target   = page
        super().__init__(label=label, style=discord.ButtonStyle.secondary,
                         disabled=disabled, row=4)

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message("Not yours!", ephemeral=True)
        await interaction.response.edit_message(**_build_shop_payload(self.user_id, self.guild_id, self.target))


class SpellShopView(discord.ui.View):
    def __init__(self, user_id: int, guild_id: int, page: int = 0):
        super().__init__(timeout=120)
        self.user_id  = user_id
        self.guild_id = guild_id
        self.page     = page
        self._build(page)

    def _build(self, page: int):
        purchasable = [(k, v) for k, v in ABILITIES.items() if v.get("purchasable")]
        total_pages = max(1, (len(purchasable) + 4) // 5)
        page_items  = purchasable[page*5:(page+1)*5]
        for row_idx, (key, ab) in enumerate(page_items):
            owned = owns_spell(self.user_id, key, guild_id=self.guild_id)
            btn   = SpellBuyButton(self.user_id, self.guild_id, key, ab, owned)
            btn.row = row_idx
            self.add_item(btn)
        if total_pages > 1:
            self.add_item(ShopNavButton(self.user_id, self.guild_id, page - 1, "◀ Prev", page == 0))
            self.add_item(ShopNavButton(self.user_id, self.guild_id, page + 1, "Next ▶", page >= total_pages - 1))

    async def on_error(self, interaction, error, item):
        await _view_on_error(interaction, error, item)


def _build_shop_payload(user_id: int, guild_id: int, page: int = 0) -> dict:
    purchasable = [(k, v) for k, v in ABILITIES.items() if v.get("purchasable")]
    total_pages = max(1, (len(purchasable) + 4) // 5)
    page = max(0, min(page, total_pages - 1))
    page_items = purchasable[page*5:(page+1)*5]
    bal = get_pounds(user_id, guild_id=guild_id)
    lines = [f"**Balance:** {bal:,} soli\n"]
    for key, ab in page_items:
        owned = owns_spell(user_id, key, guild_id=guild_id)
        status = "✅ Owned" if owned else f"{ab['price']:,} soli"
        lines.append(f"**{ab['name']}** — {status}\n> {ab['description']}")
    embed = discord.Embed(
        title="🛒 LOTM Spell Shop",
        description="\n\n".join(lines),
        color=0x9B59B6,
    )
    embed.set_footer(text=f"Page {page+1}/{total_pages} — Press a button below to purchase")
    return {"embed": embed, "view": SpellShopView(user_id, guild_id, page)}


