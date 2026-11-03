"""Effects a cast applies change the fight whatever their source.

The corpus writes a spell's mechanics as Foundry-shaped effect changes; each
test cites the SRD 5.2 sentence a change encodes and checks the engine honours
it: Self-range targeting, speed, advantage on attacks against the bearer, an AC
floor, save advantage, a condition immunity.
"""

from __future__ import annotations

import pytest

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
    _build_hydration_payload,
    _effective_speed,
    _find_combatant,
)
from dnd5e_engine.spatial import cell_id as cell
from dnd5e_engine.types.effects import ActiveEffectChange, flag_change_is_set
from tests.c20_support import act, events, monster_turn, start
from tests.fork_support import BUNDLED, cast, caster, goblin


@pytest.fixture(autouse=True)
def _bundled_loader():
    set_lib_loader_for_tests(BUNDLED)
    yield
    set_lib_loader_for_tests(None)


def _cast(spell_id: str, slot: int, **fields):
    handle, live = start([caster([spell_id], {slot: 1})], encounter=[goblin()], seed=1)
    cast(handle, spell_id, **fields)
    return handle, live


def _cast_then_goblin_turn(spell_id: str, slot: int, *, target: str | None):
    handle, live = _cast(spell_id, slot, target_id=target)
    if live.current_actor_id == "char:caster":  # a Bonus Action cast keeps the turn
        act(handle, "char:caster", intent_type="pass")
    monster_turn(handle)
    return live


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
    _handle, live = _cast("false-life", 1)
    [temp] = events(live, TempHpApplied)
    assert temp.target_id == "char:caster"
    assert 6 <= temp.amount <= 12
    assert live.tracked_temp_hp["char:caster"] == temp.amount


def test_a_speed_change_moves_the_budget_at_the_next_turn_start():
    """SRD 5.2 Longstrider: "The target's Speed increases by 10 feet until
    the spell ends." Haste: "its Speed is doubled ... it has Advantage on
    Dexterity saving throws"; Slow: "speed is halved"."""
    live = _cast_then_goblin_turn("longstrider", 1, target="char:caster")
    caster_ = _find_combatant(live, "char:caster")
    # Longstrider ships ``rounds: 1, seconds: 3600``: the hour stands, not the
    # round (one round has ticked at the caster's own turn end)
    assert live.active_effects["char:caster"][0].duration.rounds == 599
    assert _effective_speed(caster_, live) == 40
    assert caster_.movement_remaining == 40  # refreshed at the caster's next turn start

    live = _cast_then_goblin_turn("haste", 3, target="char:caster")
    caster_ = _find_combatant(live, "char:caster")
    assert _effective_speed(caster_, live) == 60
    payload = _build_hydration_payload(live, caster=caster_)
    assert payload["save_modifiers"]["char:caster"]["passive_save_adv"] == ["DEX"]

    # read before the goblin's swing can break the concentration
    _handle, live = _cast("slow", 3, target_id="mon:foe")
    [save] = [s for s in events(live, SaveRolled) if s.target_id == "mon:foe"]
    assert not save.succeeded
    assert _effective_speed(_find_combatant(live, "mon:foe"), live) == 15


def test_blur_gives_attacks_against_the_caster_disadvantage():
    """SRD 5.2 Blur: "For the duration, any creature has Disadvantage on
    attack rolls against you." """
    live = _cast_then_goblin_turn("blur", 2, target=None)
    names = {e.effect.name for e in events(live, EffectApplied)}
    assert "Blurred" in names  # the cast applies the effect its Foundry source left to a module
    assert all(e.effect.target_id == "char:caster" for e in events(live, EffectApplied))
    swings = [a for a in events(live, AttackRolled) if a.attacker_id == "mon:foe"]
    assert swings
    assert all(a.advantage == "disadvantage" for a in swings)
    assert all("grant:target" in a.disadvantage_sources for a in swings)


def test_faerie_fire_gives_attacks_against_an_outlined_creature_advantage():
    """SRD 5.2 Faerie Fire: "Attack rolls against an affected creature or
    object have Advantage if the attacker can see it." """
    for seed in range(1, 6):
        handle, live = start(
            [caster(["faerie-fire"], {1: 1}, hp_current=200, hp_max=200)],
            encounter=[
                goblin(entity_id="mon:a", zone_id=cell(2, 0)),
                goblin(entity_id="mon:b", zone_id=cell(2, 1)),
            ],
            seed=seed,
        )
        # the Cube's point of origin is the space before mon:a, aimed at it: both
        # goblins are inside its near face
        cast(handle, "faerie-fire", target_zone_id=cell(1, 0), target_id="mon:a")
        outlined = {e.effect.target_id for e in events(live, EffectApplied)}
        failed = {s.target_id for s in events(live, SaveRolled) if not s.succeeded}
        assert outlined == failed
        if outlined:
            break
    else:
        pytest.fail("no seed outlined a goblin")


@pytest.mark.parametrize(
    ("spell_id", "slot", "floor"),
    [
        ("barkskin", 2, 17),  # "the target's AC can't be less than 17"
        ("mage-armor", 1, 15),  # "base AC becomes 13 plus its Dexterity modifier" (DEX 14)
    ],
)
def test_an_ac_floor_lifts_the_targets_ac(spell_id: str, slot: int, floor: int):
    """SRD 5.2 Barkskin and Mage Armor: the goblin's swing needs the floor, not
    the caster's own AC 10."""
    live = _cast_then_goblin_turn(spell_id, slot, target="char:caster")
    caster_ = _find_combatant(live, "char:caster")
    assert caster_.ac == 10
    assert _ac_floor_from_effects(caster_, live.active_effects["char:caster"]) == floor
    for swing in [a for a in events(live, AttackRolled) if a.attacker_id == "mon:foe"]:
        assert swing.is_hit == (swing.roll_total >= floor) or swing.natural in (1, 20)


def test_heroism_makes_its_target_immune_to_the_frightened_condition():
    """SRD 5.2 Heroism: "the target is immune to the Frightened condition" —
    withheld while Heroism lasts, and back once it ends."""
    handle, live = _cast("heroism", 1, target_id="char:caster")
    caster_ = _find_combatant(live, "char:caster")
    assert caster_.condition_immunities == ["frightened"]
    assert is_condition_immune(caster_, "frightened")
    monster_turn(handle)
    act(handle, "char:caster", intent_type="drop_concentration")
    caster_ = _find_combatant(live, "char:caster")
    assert caster_.condition_immunities == []
    assert not is_condition_immune(caster_, "frightened")
    assert not events(live, ConditionApplied)
