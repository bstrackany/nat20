"""Activities that change a D20 Test after the roll — ``Activity.roll_modifier``.
Left out of a dump when unset, so the corpus only changes where the translator's
``_ROLL_MODIFIERS`` table sets one.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from dnd5e_srd_data import BundledAssetLoader, RollBlock, RollModifier, UtilityActivity
from tools.translators.foundry import _ROLL_MODIFIERS, _apply_roll_modifiers

LOADER = BundledAssetLoader()
CANONICAL = Path(__file__).resolve().parent.parent / "src" / "dnd5e_srd_data" / "canonical"


def test_an_activity_without_a_modifier_dumps_as_before() -> None:
    assert "roll_modifier" not in UtilityActivity(_id="x").model_dump(mode="json")
    modifier = RollModifier(mode="reroll", applies_to=["save"])
    assert modifier.model_dump(mode="json") == {"mode": "reroll", "applies_to": ["save"]}


def test_a_modifier_round_trips() -> None:
    modifier = RollModifier(
        mode="roll", applies_to=["attack"], whose=["enemy"], within_ft=60, seen=True
    )
    activity = UtilityActivity(_id="x", roll=RollBlock(formula="1d6"), roll_modifier=modifier)
    dumped = activity.model_dump(mode="json")
    assert dumped["roll_modifier"] == {
        "mode": "roll",
        "applies_to": ["attack"],
        "whose": ["enemy"],
        "within_ft": 60,
        "seen": True,
    }
    assert UtilityActivity.model_validate(dumped) == activity


@pytest.mark.parametrize(
    "build",
    [
        # a roll modifier adds or subtracts the activity's own roll
        lambda: UtilityActivity(
            _id="x", roll_modifier=RollModifier(mode="roll", applies_to=["save"])
        ),
        # a set needs its number, and only a set has one
        lambda: RollModifier(mode="set", applies_to=["save"]),
        lambda: RollModifier(mode="reroll", applies_to=["save"], value=20),
        lambda: RollModifier(mode="set", applies_to=["save"], value=21),
        lambda: RollModifier(mode="reroll", applies_to=[]),
        lambda: RollModifier(mode="reroll", applies_to=["damage"]),
        lambda: RollModifier(mode="portent", applies_to=["save"]),
    ],
)
def test_a_modifier_the_engine_cannot_run_is_refused(build) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(ValidationError):
        build()


@pytest.mark.parametrize(
    ("slug", "sentence"),
    [
        ("cutting-words", "subtract the number rolled from the creature's roll"),
        ("dark-ones-own-luck", "add 1d10 to your roll"),
        ("indomitable", "reroll it with a bonus equal to your Fighter level"),
        ("stroke-of-luck", "you can turn the roll into a 20"),
    ],
)
def test_the_table_reached_the_corpus(slug: str, sentence: str) -> None:
    feature = LOADER.get_feature(slug)
    assert feature is not None
    assert sentence in feature.description
    [(key, modifier)] = [(k, v) for k, v in _ROLL_MODIFIERS.items() if k[0] == slug]
    [activity] = [a for a in feature.activities if a.id == key[1]]
    assert activity.roll_modifier == modifier
    data = json.loads((CANONICAL / "features" / f"{slug}.json").read_text(encoding="utf-8"))
    [shipped] = [a for a in data["activities"] if a["id"] == key[1]]
    assert shipped["roll_modifier"] == modifier.model_dump(mode="json")
    bare = [a.model_copy(update={"roll_modifier": None}) for a in feature.activities]
    assert _apply_roll_modifiers(slug, bare) == list(feature.activities)
