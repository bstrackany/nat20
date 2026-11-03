"""Reactions that reduce the damage they answer — ``Activity.damage_reduction``.
Left out of a dump when unset, so the corpus only changes where the translator's
``_DAMAGE_REDUCTIONS`` table sets one.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from dnd5e_srd_data import (
    BundledAssetLoader,
    DamageReduction,
    HealActivity,
    ReactionTriggerKind,
    UtilityActivity,
)
from tools.translators.foundry import (
    _DAMAGE_REDUCTIONS,
    _apply_damage_reductions,
    reaction_conditions_from_text,
)

LOADER = BundledAssetLoader()
CANONICAL = Path(__file__).resolve().parent.parent / "src" / "dnd5e_srd_data" / "canonical"


def test_an_activity_without_a_reduction_dumps_as_before() -> None:
    dumped = UtilityActivity(_id="x").model_dump(mode="json")
    assert "damage_reduction" not in dumped
    reduction = DamageReduction(mode="halve")
    assert reduction.model_dump(mode="json") == {"mode": "halve"}


def test_a_reduction_round_trips() -> None:
    reduction = DamageReduction(mode="roll", damage_types=["slashing"], attacker_seen=True)
    activity = HealActivity(_id="x", damage_reduction=reduction)
    dumped = activity.model_dump(mode="json")
    assert dumped["damage_reduction"] == {
        "mode": "roll",
        "attacker_seen": True,
        "damage_types": ["slashing"],
    }
    assert HealActivity.model_validate(dumped) == activity


@pytest.mark.parametrize(
    "build",
    [
        # a roll reduction reduces by the activity's own healing roll
        lambda: UtilityActivity(_id="x", damage_reduction=DamageReduction(mode="roll")),
        lambda: DamageReduction(mode="deflect"),
        lambda: DamageReduction(mode="halve", damage_types=["sonic"]),
    ],
)
def test_a_reduction_the_engine_cannot_run_is_refused(build) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(ValidationError):
        build()


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        ("When an attacker that you can see hits you with an attack roll", "hit_by_attack"),
        ("When an attack roll hits you and its damage includes Slashing damage", "hit_by_attack"),
        ("When you take damage", "takes_damage"),
    ],
)
def test_the_reduction_sentences_are_typed(text: str, kind: str) -> None:
    assert [c.kind for c in reaction_conditions_from_text(text)] == [ReactionTriggerKind(kind)]


@pytest.mark.parametrize(
    ("slug", "sentence"),
    [
        ("uncanny-dodge", "halve the attack's damage against you (round down)"),
        ("deflect-attacks", "The reduction equals 1d10 plus your Dexterity modifier"),
        ("deflect-energy", "against attacks that deal any damage type"),
        ("stones-endurance", "reduce the damage by that total"),
    ],
)
def test_the_table_reached_the_corpus(slug: str, sentence: str) -> None:
    feature = LOADER.get_feature(slug)
    assert feature is not None
    assert sentence in feature.description
    [(key, reduction)] = [(k, v) for k, v in _DAMAGE_REDUCTIONS.items() if k[0] == slug]
    [activity] = [a for a in feature.activities if a.id == key[1]]
    assert activity.damage_reduction == reduction
    assert activity.activation.reaction_conditions, "a reduction must be armable"
    data = json.loads((CANONICAL / "features" / f"{slug}.json").read_text(encoding="utf-8"))
    [shipped] = [a for a in data["activities"] if a["id"] == key[1]]
    assert shipped["damage_reduction"] == reduction.model_dump(mode="json")
    bare = [a.model_copy(update={"damage_reduction": None}) for a in feature.activities]
    assert _apply_damage_reductions(slug, bare) == list(feature.activities)
