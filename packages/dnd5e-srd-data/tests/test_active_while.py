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
from tools.translators.foundry import (
    _ACTIVE_WHILE,
    _EFFECT_WHILE_STATUS,
    _apply_active_while,
    _apply_while_status,
)

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


# ── applied effects that hold only while a condition does (while_status) ─────


def test_an_effect_without_a_while_status_dumps_as_before() -> None:
    effect = PassiveEffect.model_validate({"_id": "x", "name": "Bless"})
    assert effect.while_status == ""
    assert "while_status" not in effect.model_dump(mode="json")
    marked = PassiveEffect(_id="x", while_status="charmed")
    assert marked.model_dump(mode="json")["while_status"] == "charmed"
    assert PassiveEffect.model_validate(marked.model_dump(mode="json")) == marked


@pytest.mark.parametrize(
    ("slug", "sentence", "status"),
    [
        (
            "hypnotic-pattern",
            "While &amp;Reference[charmed apply=false], the creature has the "
            "&amp;Reference[incapacitated apply=false] condition",
            "charmed",
        ),
    ],
)
def test_the_while_status_table_reached_the_corpus(slug: str, sentence: str, status: str) -> None:
    spell = LOADER.get_spell(slug)
    assert spell is not None
    ids = {k[1] for k, v in _EFFECT_WHILE_STATUS.items() if k[0] == slug and v == status}
    marked = [e for e in spell.passive_effects if e.id in ids]
    assert marked and all(status in e.statuses for e in marked)
    assert all(sentence in e.description or sentence in spell.description for e in marked)
    assert all(e.while_status == status for e in marked)
    data = json.loads((CANONICAL / "spells" / f"{slug}.json").read_text(encoding="utf-8"))
    assert {e["id"] for e in data["passive_effects"] if e.get("while_status") == status} == ids
    bare = [e.model_copy(update={"while_status": ""}) for e in spell.passive_effects]
    assert _apply_while_status(slug, bare) == list(spell.passive_effects)
    assert sum(1 for path in CANONICAL.rglob("*.json") if '"while_status"' in path.read_text()) == 1
