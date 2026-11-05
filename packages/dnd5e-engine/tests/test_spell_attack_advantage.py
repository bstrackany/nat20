"""Advantage on spell attack rolls only: ``flags.advantage.attack.spell``.

SRD 5.2 Innate Sorcery: "For 1 minute: The spell save DC of your Sorcerer
spells increases by 1. You have Advantage on the attack rolls of Sorcerer
spells you cast."
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from dnd5e_srd_data.loader import BundledAssetLoader

from dnd5e_engine.events import AttackRolled
from dnd5e_engine.lib_loader import set_lib_loader_for_tests
from tests.c20_support import act, events, pc, start


@pytest.fixture(autouse=True)
def _bundled_loader() -> Iterator[None]:
    set_lib_loader_for_tests(BundledAssetLoader())
    yield
    set_lib_loader_for_tests(None)


def _sorcerer():
    return pc(
        class_slug="sorcerer",
        character_level=1,
        charisma=16,
        spells_known=["sorcerous-burst"],
        equipment=["dagger"],
    )


def test_innate_sorcery_gives_a_spell_attack_advantage_and_a_weapon_attack_none() -> None:
    handle, live = start([_sorcerer()], seed=4)
    act(
        handle,
        "char:hero",
        intent_type="use_feature",
        feature_id="innate-sorcery",
        use_bonus_action=True,
    )
    act(
        handle,
        "char:hero",
        intent_type="cast_spell",
        spell_id="sorcerous-burst",
        target_id="mon:foe",
    )
    [spell] = events(live, AttackRolled)
    assert spell.advantage == "advantage"


def test_a_weapon_attack_under_innate_sorcery_is_rolled_normally() -> None:
    handle, live = start([_sorcerer()], seed=4)
    act(
        handle,
        "char:hero",
        intent_type="use_feature",
        feature_id="innate-sorcery",
        use_bonus_action=True,
    )
    act(handle, "char:hero", intent_type="attack", weapon_id="dagger", target_id="mon:foe")
    [swing] = events(live, AttackRolled)
    assert swing.advantage == "normal"


def test_without_innate_sorcery_the_spell_attack_is_rolled_normally() -> None:
    handle, live = start([_sorcerer()], seed=4)
    act(
        handle,
        "char:hero",
        intent_type="cast_spell",
        spell_id="sorcerous-burst",
        target_id="mon:foe",
    )
    [spell] = events(live, AttackRolled)
    assert spell.advantage == "normal"
