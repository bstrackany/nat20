"""A caster's chosen damage type: ``PlayerIntent.damage_type``.

SRD 5.2 Sorcerous Burst: "Make a ranged attack roll against the target. On a
hit, the target takes 1d8 damage of a type you choose: Acid, Cold, Fire,
Lightning, Poison, Psychic, or Thunder."
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from dnd5e_srd_data.loader import BundledAssetLoader

from dnd5e_engine.events import DamageApplied
from dnd5e_engine.lib_loader import set_lib_loader_for_tests
from dnd5e_engine.spatial import cell_id
from tests.c20_support import act, events, foe, pc, start


@pytest.fixture(autouse=True)
def _bundled_loader() -> Iterator[None]:
    set_lib_loader_for_tests(BundledAssetLoader())
    yield
    set_lib_loader_for_tests(None)


def _burst(**intent: object) -> list[str]:
    sorcerer = pc(class_slug="sorcerer", charisma=16, spells_known=["sorcerous-burst"])
    handle, live = start([sorcerer], seed=3, encounter=[foe(zone_id=cell_id(3, 0))])
    act(
        handle,
        "char:hero",
        intent_type="cast_spell",
        spell_id="sorcerous-burst",
        target_id="mon:foe",
        **intent,
    )
    return [e.damage_type for e in events(live, DamageApplied)]


def test_the_chosen_type_is_dealt() -> None:
    dealt = _burst(damage_type="fire")
    assert dealt
    assert set(dealt) == {"fire"}


def test_without_a_choice_the_first_listed_type_stands() -> None:
    dealt = _burst()
    assert dealt
    assert set(dealt) == {"acid"}


def test_a_type_the_spell_does_not_offer_is_ignored() -> None:
    dealt = _burst(damage_type="radiant")
    assert dealt
    assert set(dealt) == {"acid"}
