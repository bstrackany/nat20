"""Always-on effects that count only while something holds of their bearer —
``PassiveEffect.active_while`` (``ActiveWhile``). Left out of a dump when unset,
so the corpus only changes where the translator's table sets one.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from dnd5e_srd_data import ActiveWhile, BundledAssetLoader, PassiveEffect
from tools.translators.foundry import _ACTIVE_WHILE, _apply_active_while

LOADER = BundledAssetLoader()
CANONICAL = Path(__file__).resolve().parent.parent / "src" / "dnd5e_srd_data" / "canonical"


def test_an_effect_without_a_condition_dumps_as_before() -> None:
    effect = PassiveEffect.model_validate({"_id": "x", "name": "Bless"})
    assert effect.active_while is None
    assert "active_while" not in effect.model_dump(mode="json")


@pytest.mark.parametrize(
    "condition",
    [
        {"effect": "rage"},
        {"bloodied": True},
        {"effect": "wild-shape", "unless_incapacitated": True},
    ],
)
def test_a_condition_round_trips_without_its_defaults(condition: dict[str, object]) -> None:
    effect = PassiveEffect(_id="x", active_while=ActiveWhile.model_validate(condition))
    dumped = effect.model_dump(mode="json")
    assert dumped["active_while"] == condition
    assert PassiveEffect.model_validate(dumped) == effect


@pytest.mark.parametrize("condition", [{}, {"bloodied": False}, {"holding": "spellbook"}])
def test_an_empty_or_unknown_condition_is_refused(condition: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        ActiveWhile.model_validate(condition)


@pytest.mark.parametrize(
    ("slug", "sentence"),
    [
        ("danger-sense", "unless you have the &amp;Reference[Incapacitated] condition"),
        ("mindless-rage", "conditions while your Rage is active"),
    ],
)
def test_the_table_reached_the_corpus(slug: str, sentence: str) -> None:
    feature = LOADER.get_feature(slug)
    assert feature is not None
    assert sentence in feature.description
    [(key, condition)] = [(k, v) for k, v in _ACTIVE_WHILE.items() if k[0] == slug]
    [effect] = [e for e in feature.passive_effects if e.id == key[1]]
    assert effect.active_while == condition
    data = json.loads((CANONICAL / "features" / f"{slug}.json").read_text(encoding="utf-8"))
    [shipped] = [e for e in data["passive_effects"] if e["id"] == key[1]]
    assert shipped["active_while"] == condition.model_dump(mode="json")
    bare = [e.model_copy(update={"active_while": None}) for e in feature.passive_effects]
    assert _apply_active_while(slug, bare) == list(feature.passive_effects)
    rage = LOADER.get_feature("rage")
    assert rage is not None
    assert _apply_active_while("rage", list(rage.passive_effects)) == list(rage.passive_effects)
