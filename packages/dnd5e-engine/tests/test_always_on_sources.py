"""A character's features and feats are in play for the whole combat.

Their always-on effects count the way a cast's do — the keys the sheet
derivation does not already fold are seeded as permanent effects at
``start_combat`` — and every activity they hold a trigger for is a permanent
use. The SRD corpus ships no feat effects, so these tests build their own feat.
"""

from __future__ import annotations

from random import Random
from typing import Any

import pytest
from dnd5e_srd_data import Feat
from dnd5e_srd_data.schema.common import (
    ActivationBlock,
    ActivityTrigger,
    DamageActivity,
    DamageActivityDamageBlock,
    DamagePartBlock,
    HealActivity,
    PassiveEffect,
    PassiveEffectChange,
)

from dnd5e_engine.activities.dice import roll_expr
from dnd5e_engine.build_spec import derive_sheet, make_build_spec
from dnd5e_engine.events import (
    ActivityTriggered,
    AttackRolled,
    CastFailed,
    DamageApplied,
    EffectApplied,
    HealingApplied,
)
from dnd5e_engine.lib_loader import set_lib_loader_for_tests
from dnd5e_engine.orchestrator import _area_effects_on, _build_hydration_payload, _find_combatant
from tests.c20_support import act, events, monster_turn, pc, start
from tests.fork_support import BUNDLED, WithAssets, homebrew_feat


def _feat(**update: Any) -> Feat:
    """Advantage on Wisdom saves, immunity to Charmed, and 1d4 Fire damage once
    per turn on a weapon hit."""
    ember = homebrew_feat(
        "ember-heart",
        passive_effects=[
            PassiveEffect(
                _id="emberheartpassive",
                name="Ember Heart",
                changes=[
                    PassiveEffectChange(
                        key="system.abilities.wis.save.roll.mode", mode=2, value="1"
                    ),
                    PassiveEffectChange(key="system.traits.ci.value", mode=2, value="charmed"),
                ],
            )
        ],
        activities=[
            DamageActivity(
                _id="emberlash",
                name="Ember Lash",
                damage=DamageActivityDamageBlock(
                    parts=[DamagePartBlock(number=1, denomination=4, types=["fire"])]
                ),
                trigger=ActivityTrigger(kind="hits", subject="caster", once_per_turn=True),
            )
        ],
    )
    return ember.model_copy(update=update)


@pytest.fixture(autouse=True)
def _loader():
    set_lib_loader_for_tests(WithAssets(_feat()))
    yield
    set_lib_loader_for_tests(None)


def _fighter():
    """A Fighter 5 (Extra Attack: two swings an Action) with the feat."""
    fighter = pc(
        "char:ftr", strength=16, class_slug="fighter", character_level=5, feats=("ember-heart",)
    )
    return start([fighter], seed=2)


def test_a_feats_always_on_effect_is_in_play_from_the_start():
    _handle, live = _fighter()
    fighter = _find_combatant(live, "char:ftr")
    payload = _build_hydration_payload(live, caster=fighter)
    [seeded] = live.active_effects["char:ftr"]
    assert (seeded.origin, seeded.flags) == ("feat:ember-heart:char:ftr", {"always_on": True})
    # the live-read key is seeded; the condition immunity is the build's and is
    # already on the Combatant, not seeded twice
    assert [c.key for c in seeded.changes] == ["system.abilities.wis.save.roll.mode"]
    assert payload["save_modifiers"]["char:ftr"]["passive_save_adv"] == ["WIS"]
    assert fighter.condition_immunities == ["charmed"]
    assert not events(live, EffectApplied)  # nothing happened: the fighter arrived that way


def test_a_feats_on_hit_rider_fires_once_per_turn():
    """Two swings land in one Action; the rider's "once per turn" fires on the
    first only, and again on the next turn."""
    handle, live = _fighter()

    def swing() -> None:
        act(handle, "char:ftr", intent_type="attack", weapon_id="longsword", target_id="mon:foe")

    swing()
    swing()
    if live.current_actor_id == "char:ftr":
        act(handle, "char:ftr", intent_type="pass")
    monster_turn(handle)
    swing()

    hits = [a for a in events(live, AttackRolled) if a.attacker_id == "char:ftr"]
    assert [h.is_hit for h in hits] == [True, True, True]
    fired = events(live, ActivityTriggered)
    assert [(f.source_kind, f.source_id, f.trigger) for f in fired] == [
        ("feat", "ember-heart", "hits"),
        ("feat", "ember-heart", "hits"),
    ]
    fire = [e for e in events(live, DamageApplied) if e.damage_type == "fire"]
    assert len(fire) == 2
    # the first swing's rider lands before the second swing is rolled
    assert live.event_log.index(fire[0]) < live.event_log.index(hits[1])
    [use] = live.held_uses
    assert use.permanent


@pytest.mark.parametrize(
    "build",
    [
        {
            "level": 4,
            "background_slug": "soldier",
            "selected_choices": ("feat:fighter:4:ember-heart",),
        },
        {"level": 3, "feats": ("ember-heart",)},  # the feats a sheet says the character has
    ],
)
def test_a_feats_effects_reach_the_derived_sheet(build: dict[str, Any]):
    spec = make_build_spec(species_slug="human", class_slug="fighter", **build)
    sheet = derive_sheet(spec, loader=WithAssets(_feat()))
    assert "ember-heart" in sheet.feats
    assert "charmed" in sheet.condition_immunities


def test_a_seeded_changes_tokens_are_resolved_for_the_character():
    """SRD 5.2 Aura of Protection ships its save bonus as
    ``max(1, @abilities.cha.mod)``; the save roller parses numbers and dice, so
    the token is resolved when the aura is seeded (Charisma 16: +3) and a
    paladin's save rolls. The bonus reaches the paladin through the aura's
    area (``tests/test_persistent_areas.py``), not as an effect on it."""
    set_lib_loader_for_tests(BUNDLED)
    paladin = pc("char:pal", charisma=16, class_slug="paladin", character_level=7)
    _handle, live = start([paladin], seed=2)
    payload = _build_hydration_payload(live, caster=_find_combatant(live, "char:pal"))
    assert "char:pal" not in live.active_effects
    [aura] = _area_effects_on(live, "char:pal")
    assert [(c.key, c.value) for c in aura.changes] == [("system.bonuses.abilities.save", "3")]
    bonus = payload["save_modifiers"]["char:pal"]["passive_save_bonus"]
    assert bonus == "3"
    assert roll_expr(bonus, Random(1)) == 3


def test_a_feats_own_activity_can_be_used():
    """A feat with an action of its own is in the character's repertoire like a
    granted feature; its activity's own ``uses`` caps it."""
    mend = HealActivity(
        _id="emberheal",
        name="Ember Mend",
        activation=ActivationBlock(type="bonus"),
        healing=DamagePartBlock(number=1, denomination=8, types=["healing"]),
        uses={"max": "1", "spent": 0, "recovery": []},  # type: ignore[arg-type]
    )
    set_lib_loader_for_tests(WithAssets(_feat(activities=[mend])))
    handle, live = _fighter()
    for _ in range(2):
        act(
            handle,
            "char:ftr",
            intent_type="use_feature",
            feature_id="ember-heart",
            target_id="char:ftr",
        )
    [heal] = events(live, HealingApplied)
    assert heal.target_id == "char:ftr"
    assert [e.reason for e in events(live, CastFailed)] == ["no_uses_remaining"]
