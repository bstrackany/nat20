"""Always-on effects that count only while something holds of their bearer —
``PassiveEffect.active_while`` (``dnd5e_engine.conditional_passives``), asked
each time a creature's effects are read.

SRD 5.2 Mindless Rage: "You have Immunity to the Charmed and Frightened
conditions while your Rage is active"; Danger Sense: "You have Advantage on
Dexterity saving throws unless you have the Incapacitated condition"; Rules
Glossary: "A creature is Bloodied while it has half its Hit Points or fewer
remaining" (homebrew "while Bloodied" resistances and Advantage).
"""

from __future__ import annotations

from typing import Any

import pytest
from dnd5e_srd_data.schema.common import ActiveWhile, PassiveEffect, PassiveEffectChange

from dnd5e_engine.build_spec import derive_sheet, make_build_spec
from dnd5e_engine.conditional_passives import holds, in_force
from dnd5e_engine.lib_loader import set_lib_loader_for_tests
from dnd5e_engine.orchestrator import (
    _build_hydration_payload,
    _effects_in_play,
    _end_rage,
    _update_combatant,
)
from dnd5e_engine.spatial import cell_id
from dnd5e_engine.types.conditions import ActiveCondition
from dnd5e_engine.types.effects import ActiveEffect
from tests.c20_support import act, combatant, pc, start
from tests.fork_support import BUNDLED, WithAssets, cast, caster, homebrew_feat

HERO = "char:hero"


@pytest.fixture(autouse=True)
def _bundled():
    set_lib_loader_for_tests(BUNDLED)
    yield
    set_lib_loader_for_tests(None)


def _effect(name: str, **flags: Any) -> ActiveEffect:
    return ActiveEffect(
        id=f"effect:{name.lower().replace(' ', '_')}",
        name=name,
        origin=f"cast:{name}:{HERO}",
        target_id=HERO,
        flags=flags,
    )


@pytest.mark.parametrize(
    ("condition", "hp", "conditions", "others", "met"),
    [
        ({"effect": "rage"}, 30, [], ["Rage"], True),
        ({"effect": "rage"}, 30, [], ["Bless"], False),
        ({"effect": "wild-shape"}, 30, [], ["Wild Shape"], True),
        ({"bloodied": True}, 15, [], [], True),  # half its Hit Points
        ({"bloodied": True}, 16, [], [], False),
        ({"unless_incapacitated": True}, 30, ["paralyzed"], [], False),  # implies it
        ({"unless_incapacitated": True}, 30, ["prone"], [], True),
        ({"effect": "rage", "bloodied": True}, 30, [], ["Rage"], False),  # every field
    ],
)
def test_what_holds(
    condition: dict[str, Any], hp: int, conditions: list[str], others: list[str], met: bool
):
    _handle, live = start([pc()], seed=1)
    bearer = combatant(live).model_copy(
        update={
            "hp_current": hp,
            "conditions": [
                ActiveCondition(condition=c, source_entity_id="implied:test", scope="combat")
                for c in conditions
            ],
        }
    )
    gated = _effect("Gated", active_while=ActiveWhile(**condition).model_dump(mode="json"))
    effects = [gated, *(_effect(name) for name in others)]
    assert holds(ActiveWhile(**condition), bearer, effects) is met
    assert (gated in in_force(effects, bearer)) is met


def test_mindless_rage_immunity_lasts_while_the_rage_does():
    berserker = pc(class_slug="barbarian", subclass_slug="berserker", character_level=6)
    handle, live = start([berserker], seed=1)
    seeded = [e for e in live.active_effects[HERO] if e.origin.startswith("feature:mindless")]
    assert [c.value for e in seeded for c in e.changes] == ["charmed", "frightened"]
    assert combatant(live).condition_immunities == []
    act(handle, HERO, intent_type="use_feature", feature_id="rage")
    assert combatant(live).condition_immunities == ["charmed", "frightened"]
    _end_rage(live, HERO, "incapacitated")
    assert combatant(live).condition_immunities == []


@pytest.mark.parametrize(
    ("rages", "conditions"), [(True, []), (False, ["charmed", "incapacitated"])]
)
def test_hypnotic_pattern_leaves_a_raging_berserker_alone(rages: bool, conditions: list[str]):
    # SRD 5.2 Hypnotic Pattern: "have the Charmed condition for the duration.
    # While Charmed, the creature has the Incapacitated condition and a Speed of
    # 0." Immune to Charmed while raging, the Berserker isn't Incapacitated
    # either, so the Rage goes on. WIS 1 against DC 16: the save always fails.
    berserker = pc(class_slug="barbarian", subclass_slug="berserker", character_level=6, wisdom=1)
    ally = caster(["hypnotic-pattern"], {3: 1}, zone_id=cell_id(9, 9), initiative=15)
    handle, live = start([berserker, ally.model_copy(update={"intelligence": 20})], seed=13)
    if rages:
        act(handle, HERO, intent_type="use_feature", feature_id="rage")
    act(handle, HERO, intent_type="pass")
    cast(handle, "hypnotic-pattern", slot_level=3, target_zone_id=cell_id(6, 6), direction=(-1, -1))
    hero = combatant(live)
    assert sorted({c.condition for c in hero.conditions}) == conditions
    assert hero.condition_immunities == (["charmed", "frightened"] if rages else [])


@pytest.mark.parametrize(("condition", "advantage"), [(None, ["DEX"]), ("stunned", [])])
def test_danger_sense_is_off_while_incapacitated(condition: str | None, advantage: list[str]):
    barbarian = pc(class_slug="barbarian", character_level=2)
    stun = ActiveEffect(
        id="effect:stun",
        name="Stun",
        origin="cast:stun:mon:foe",
        target_id=HERO,
        statuses={"stunned"},
    )
    _handle, live = start([barbarian], seed=1, active_effects=[stun] if condition else [])
    payload = _build_hydration_payload(live, caster=combatant(live))
    assert payload["save_modifiers"][HERO]["passive_save_adv"] == advantage


def _last_stand() -> None:
    """A homebrew feat: Resistance to Fire and Advantage on attack rolls while
    Bloodied."""
    feat = homebrew_feat(
        "last-stand",
        passive_effects=[
            PassiveEffect(
                _id="laststandpassive",
                name="Last Stand",
                changes=[
                    PassiveEffectChange(key="system.traits.dr.value", mode=2, value="fire"),
                    PassiveEffectChange(key="flags.advantage.attack", mode=5, value="1"),
                ],
                active_while=ActiveWhile(bloodied=True),
            )
        ],
    )
    set_lib_loader_for_tests(WithAssets(feat))


@pytest.mark.parametrize(("hp", "bloodied"), [(30, False), (15, True)])
def test_a_feats_effect_counts_only_while_bloodied(hp: int, bloodied: bool):
    _last_stand()
    _handle, live = start([pc(feats=("last-stand",))], seed=1)
    _update_combatant(live, HERO, hp_current=hp)
    hero = combatant(live)
    payload = _build_hydration_payload(live, caster=hero)
    resistances = payload["passive_damage_modifiers"].get(HERO, {}).get("resistances", [])
    assert ("fire" in resistances) is bloodied
    in_play = _effects_in_play(live, hero, [])
    assert any(e.id.startswith("effect:last-stand") for e in in_play) is bloodied


def test_a_conditional_effect_stays_off_the_derived_sheet():
    _last_stand()
    spec = make_build_spec(
        species_slug="human", class_slug="fighter", level=3, feats=("last-stand",)
    )
    sheet = derive_sheet(spec, loader=WithAssets())
    assert "fire" not in sheet.damage_resistances
