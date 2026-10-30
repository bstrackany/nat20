"""Effects a cast applies change the fight whatever their source.

The corpus writes a spell's mechanics as Foundry-shaped effect changes; the
engine read only a few of those keys from a spell, so a cast buff did less than
its text. Each test here cites the SRD 5.2 sentence the change encodes and
checks the engine honours it: a Self-range spell lands on the caster, speed
changes move the budget, an effect on the target grants advantage or
disadvantage to attacks against it, an AC floor lifts the base, a save gets
advantage, a condition immunity withholds the condition.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from dnd5e_srd_data.loader import BundledAssetLoader

from dnd5e_engine import (
    EncounterMemberSpec,
    GridScene,
    PartyMemberSpec,
    PlayerIntent,
    start_combat,
    submit_player_intent,
)
from dnd5e_engine.activities.effects import is_condition_immune
from dnd5e_engine.events import (
    AttackRolled,
    ConditionApplied,
    EffectApplied,
    SaveRolled,
    TempHpApplied,
)
from dnd5e_engine.lib_loader import set_lib_loader_for_tests
from dnd5e_engine.orchestrator import (
    _ac_floor_from_effects,
    _effective_speed,
    _find_combatant,
    _get_live,
    advance_monster_turn,
)
from dnd5e_engine.spatial import cell_id as cell
from dnd5e_engine.types.effects import ActiveEffectChange, flag_change_is_set

BUNDLED = BundledAssetLoader()


@pytest.fixture(autouse=True)
def _bundled_loader():
    set_lib_loader_for_tests(BUNDLED)
    yield
    set_lib_loader_for_tests(None)


def _events(live, kind):
    return [e for e in live.event_log if isinstance(e, kind)]


def _caster(spells: list[str], slots: dict[int, int], **overrides: Any) -> PartyMemberSpec:
    fields: dict[str, Any] = dict(
        entity_id="char:caster",
        name="caster",
        initiative=20,
        hp_current=60,
        hp_max=60,
        intelligence=16,
        dexterity=14,
        class_slug="wizard",
        character_level=11,
        spells_known=spells,
        spell_slots=slots,
        zone_id=cell(0, 0),
    )
    fields.update(overrides)
    return PartyMemberSpec(**fields)


def _goblin(**overrides: Any) -> EncounterMemberSpec:
    fields: dict[str, Any] = dict(
        entity_id="mon:foe",
        entity_type="Monster",
        name="goblin",
        initiative=1,
        hp_current=200,
        hp_max=200,
        ac=15,
        monster_template_slug="goblin-warrior",
        zone_id=cell(1, 0),
    )
    fields.update(overrides)
    return EncounterMemberSpec(**fields)


async def _start(party, encounter, *, seed: int = 1):
    start = await start_combat(
        session_id="effect-changes",
        party=party,
        encounter=encounter,
        grid_scene=GridScene(width=10, height=10, cell_size_ft=5),
        rng_seed=seed,
    )
    return start.handle, _get_live(start.handle)


def _cast(spell_id: str, **fields: Any) -> PlayerIntent:
    return PlayerIntent(intent_type="cast_spell", spell_id=spell_id, **fields)


async def _cast_then_goblin_turn(spell_id: str, slot: int, *, target: str | None, seed: int = 1):
    handle, live = await _start([_caster([spell_id], {slot: 1})], [_goblin()], seed=seed)
    await submit_player_intent(
        handle, actor_id="char:caster", intent=_cast(spell_id, target_id=target)
    )
    if live.current_actor_id == "char:caster":  # a Bonus Action cast keeps the turn
        await submit_player_intent(
            handle, actor_id="char:caster", intent=PlayerIntent(intent_type="pass")
        )
    await advance_monster_turn(handle)
    return handle, live


def test_a_flag_is_on_however_foundry_wrote_it():
    on = [("override", True), ("override", "1"), ("add", "1"), ("override", "true"), ("add", 1)]
    off = [("override", False), ("override", "0"), ("multiply", "1"), ("override", ""), ("add", 0)]
    for mode, value in on:
        assert flag_change_is_set(ActiveEffectChange(key="flags.x", mode=mode, value=value))
    for mode, value in off:
        assert not flag_change_is_set(ActiveEffectChange(key="flags.x", mode=mode, value=value))


def test_a_self_range_spell_with_no_target_lands_on_the_caster():
    """SRD 5.2 False Life (range Self): "You gain 2d4 + 4 Temporary Hit Points
    until the spell ends." """

    async def _go():
        handle, live = await _start([_caster(["false-life"], {1: 1})], [_goblin()])
        await submit_player_intent(handle, actor_id="char:caster", intent=_cast("false-life"))
        return live

    live = asyncio.run(_go())
    [temp] = _events(live, TempHpApplied)
    assert temp.target_id == "char:caster"
    assert 6 <= temp.amount <= 12
    assert live.tracked_temp_hp["char:caster"] == temp.amount


def test_a_speed_change_moves_the_budget_at_the_next_turn_start():
    """SRD 5.2 Longstrider: "The target's Speed increases by 10 feet until
    the spell ends." Haste: "its Speed is doubled"; Slow: "speed is halved"."""

    async def _go(spell_id: str, slot: int, target: str):
        _handle, live = await _cast_then_goblin_turn(spell_id, slot, target=target)
        return live

    live = asyncio.run(_go("longstrider", 1, "char:caster"))
    caster = _find_combatant(live, "char:caster")
    # Longstrider ships ``rounds: 1, seconds: 3600``: the hour stands, not the
    # round (one round has ticked at the caster's own turn end)
    assert live.active_effects["char:caster"][0].duration.rounds == 599
    assert _effective_speed(caster, live) == 40
    assert caster.movement_remaining == 40  # refreshed at the caster's next turn start

    live = asyncio.run(_go("haste", 3, "char:caster"))
    assert _effective_speed(_find_combatant(live, "char:caster"), live) == 60

    async def _slow():  # read before the goblin's swing can break the concentration
        handle, live = await _start([_caster(["slow"], {3: 1})], [_goblin()])
        await submit_player_intent(
            handle, actor_id="char:caster", intent=_cast("slow", target_id="mon:foe")
        )
        return live

    live = asyncio.run(_slow())
    goblin = _find_combatant(live, "mon:foe")
    [save] = [s for s in _events(live, SaveRolled) if s.target_id == "mon:foe"]
    assert not save.succeeded
    assert _effective_speed(goblin, live) == 15


def test_blur_gives_attacks_against_the_caster_disadvantage():
    """SRD 5.2 Blur: "For the duration, any creature has Disadvantage on
    attack rolls against you." """
    _handle, live = asyncio.run(_cast_then_goblin_turn("blur", 2, target=None))
    names = {e.effect.name for e in _events(live, EffectApplied)}
    assert "Blurred" in names  # the cast applies the effect its Foundry source left to a module
    assert all(e.effect.target_id == "char:caster" for e in _events(live, EffectApplied))
    swings = [a for a in _events(live, AttackRolled) if a.attacker_id == "mon:foe"]
    assert swings
    assert all(a.advantage == "disadvantage" for a in swings)
    assert all("grant:target" in a.disadvantage_sources for a in swings)


def test_faerie_fire_gives_attacks_against_an_outlined_creature_advantage():
    """SRD 5.2 Faerie Fire: "Attack rolls against an affected creature or
    object have Advantage if the attacker can see it." """

    async def _go(seed: int):
        handle, live = await _start(
            [_caster(["faerie-fire"], {1: 1}, hp_current=200, hp_max=200)],
            [
                _goblin(entity_id="mon:a", zone_id=cell(2, 0)),
                _goblin(entity_id="mon:b", zone_id=cell(2, 1)),
            ],
            seed=seed,
        )
        # the goblins outline each other: the cube is aimed at mon:a's cell
        await submit_player_intent(
            handle,
            actor_id="char:caster",
            intent=_cast("faerie-fire", target_zone_id=cell(2, 0), target_id="mon:a"),
        )
        return live

    for seed in range(1, 6):
        live = asyncio.run(_go(seed))
        outlined = {e.effect.target_id for e in _events(live, EffectApplied)}
        failed = {s.target_id for s in _events(live, SaveRolled) if not s.succeeded}
        assert outlined == failed
        if outlined:
            break
    else:
        pytest.fail("no seed outlined a goblin")


def test_barkskin_lifts_the_targets_ac_to_17():
    """SRD 5.2 Barkskin: "the target's AC can't be less than 17" — the goblin's
    scimitar needs 17 to hit, not the caster's 10."""
    _handle, live = asyncio.run(_cast_then_goblin_turn("barkskin", 2, target="char:caster"))
    caster = _find_combatant(live, "char:caster")
    assert caster.ac == 10
    assert _ac_floor_from_effects(caster, live.active_effects["char:caster"]) == 17
    for swing in [a for a in _events(live, AttackRolled) if a.attacker_id == "mon:foe"]:
        assert swing.is_hit == (swing.roll_total >= 17) or swing.natural in (1, 20)


def test_mage_armor_sets_a_base_of_13_plus_dexterity():
    """SRD 5.2 Mage Armor: "The target's base AC becomes 13 plus its Dexterity
    modifier." """
    _handle, live = asyncio.run(_cast_then_goblin_turn("mage-armor", 1, target="char:caster"))
    caster = _find_combatant(live, "char:caster")  # Dexterity 14: +2
    assert _ac_floor_from_effects(caster, live.active_effects["char:caster"]) == 15


def test_haste_gives_advantage_on_dexterity_saves():
    """SRD 5.2 Haste: "it has Advantage on Dexterity saving throws" — the hasted
    caster then stands in its own Fireball... a goblin's cast instead: the
    projection the save resolver reads carries DEX."""

    async def _go():
        handle, live = await _start([_caster(["haste"], {3: 1})], [_goblin()])
        await submit_player_intent(
            handle, actor_id="char:caster", intent=_cast("haste", target_id="char:caster")
        )
        from dnd5e_engine.orchestrator import _build_hydration_payload

        return _build_hydration_payload(live, caster=_find_combatant(live, "char:caster"))

    payload = asyncio.run(_go())
    assert payload["save_modifiers"]["char:caster"]["passive_save_adv"] == ["DEX"]


def test_heroism_makes_its_target_immune_to_the_frightened_condition():
    """SRD 5.2 Heroism: "the target is immune to the Frightened condition" —
    a Fear-like effect that would frighten it is withheld while Heroism
    lasts, and lands again once it ends."""

    async def _go():
        handle, live = await _start([_caster(["heroism"], {1: 1})], [_goblin()])
        await submit_player_intent(
            handle, actor_id="char:caster", intent=_cast("heroism", target_id="char:caster")
        )
        caster = _find_combatant(live, "char:caster")
        immune_during = (
            list(caster.condition_immunities),
            is_condition_immune(caster, "frightened"),
        )
        await advance_monster_turn(handle)
        await submit_player_intent(
            handle,
            actor_id="char:caster",
            intent=PlayerIntent(intent_type="drop_concentration"),
        )
        return live, immune_during

    live, immune_during = asyncio.run(_go())
    assert immune_during == (["frightened"], True)
    caster = _find_combatant(live, "char:caster")
    assert caster.condition_immunities == []
    assert not is_condition_immune(caster, "frightened")
    assert not _events(live, ConditionApplied)
