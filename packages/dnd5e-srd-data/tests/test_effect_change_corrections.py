"""Effect-change corrections (translator override table).

Some SRD 5.2 spells state a mechanic their pinned Foundry effect leaves to a
module: Blur's "any creature has Disadvantage on attack rolls against you",
Faerie Fire's "Attack rolls against an affected creature or object have
Advantage", and the same grants on Guiding Bolt, Shining Smite and Holy Aura.
The translator appends the change the engine's attack resolver
reads (``flags.grants.{disadvantage,advantage}.attack``) through
``tools.translators.foundry._EFFECT_CHANGE_CORRECTIONS``; these tests pin the
shipped canonical output against that table.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dnd5e_srd_data import BundledAssetLoader
from dnd5e_srd_data.schema.common import PassiveEffectChange
from tools.translators.foundry import (
    _ACTIVITY_EFFECT_REF_CORRECTIONS,
    _EFFECT_CHANGE_CORRECTIONS,
    _EFFECT_STATUS_CORRECTIONS,
    _apply_activity_effect_ref_corrections,
    _apply_effect_change_corrections,
    _apply_effect_status_corrections,
)

LOADER = BundledAssetLoader()
CANONICAL = Path(__file__).resolve().parent.parent / "src" / "dnd5e_srd_data" / "canonical"


def _effect(slug: str, effect_id: str):
    spell = LOADER.get_spell(slug)
    assert spell is not None, slug
    [effect] = [e for e in spell.passive_effects if e.id == effect_id]
    return effect


def test_blur_grants_disadvantage_on_attacks_against_its_bearer() -> None:
    spell = LOADER.get_spell("blur")
    assert spell is not None
    assert "any creature has Disadvantage on attack rolls against you" in spell.description
    assert _effect("blur", "PsuBObX6WLcNEvUw").changes == [
        PassiveEffectChange(key="flags.grants.disadvantage.attack", mode=5, value="1")
    ]


def test_faerie_fire_grants_advantage_on_attacks_against_an_outlined_creature() -> None:
    spell = LOADER.get_spell("faerie-fire")
    assert spell is not None
    assert "Attack rolls against an affected creature or object have Advantage" in spell.description
    assert _effect("faerie-fire", "Y2i0OvwOixxag0ed").changes == [
        PassiveEffectChange(key="flags.grants.advantage.attack", mode=5, value="1")
    ]


def test_blurs_cast_applies_the_blurred_effect() -> None:
    """The pinned Foundry Blur applies no effect at all (a module did); the cast
    now applies Blurred to the caster."""
    spell = LOADER.get_spell("blur")
    assert spell is not None
    [cast] = spell.activities
    assert [ref.id for ref in cast.effects] == ["PsuBObX6WLcNEvUw"]
    bare = [cast.model_copy(update={"effects": []})]
    assert [r.id for r in _apply_activity_effect_ref_corrections("blur", bare)[0].effects] == [
        "PsuBObX6WLcNEvUw"
    ]
    assert _apply_activity_effect_ref_corrections("blur", list(spell.activities)) == list(
        spell.activities
    )
    assert set(_ACTIVITY_EFFECT_REF_CORRECTIONS) == {("blur", "dnd5eactivity000")}


@pytest.mark.parametrize(
    ("slug", "effect_id", "key", "sentence"),
    [
        (
            "guiding-bolt",
            "rHzZCeXfdeTcYyk8",
            "flags.grants.advantage.attack",
            "the next attack roll made against it before the end of your next turn has Advantage",
        ),
        (
            "shining-smite",
            "pBjrcT286iCAQrjm",
            "flags.grants.advantage.attack",
            "attack rolls against it have Advantage",
        ),
        (
            "holy-aura",
            "csA26qg5YSsji1ZZ",
            "flags.grants.disadvantage.attack",
            "other creatures have Disadvantage on attack rolls against them",
        ),
    ],
)
def test_an_attack_grant_follows_its_sentence(
    slug: str, effect_id: str, key: str, sentence: str
) -> None:
    spell = LOADER.get_spell(slug)
    assert spell is not None
    assert sentence in spell.description
    grant = PassiveEffectChange(key=key, mode=5, value="1")
    assert grant in _effect(slug, effect_id).changes
    assert _EFFECT_CHANGE_CORRECTIONS[(slug, effect_id)] == [grant]


def test_every_correction_reached_the_corpus() -> None:
    for (slug, effect_id), changes in _EFFECT_CHANGE_CORRECTIONS.items():
        data = json.loads((CANONICAL / "spells" / f"{slug}.json").read_text(encoding="utf-8"))
        [effect] = [e for e in data["passive_effects"] if e["id"] == effect_id]
        shipped = [PassiveEffectChange.model_validate(c) for c in effect["changes"]]
        for change in changes:
            assert change in shipped, (slug, effect_id, change)


def test_the_table_adds_each_change_once_and_leaves_other_effects_alone() -> None:
    blur = LOADER.get_spell("blur")
    assert blur is not None
    bare = [e.model_copy(update={"changes": []}) for e in blur.passive_effects]
    corrected = _apply_effect_change_corrections("blur", bare)
    assert [c.key for c in corrected[0].changes] == ["flags.grants.disadvantage.attack"]
    assert _apply_effect_change_corrections("blur", corrected) == corrected
    shield = LOADER.get_spell("shield")
    assert shield is not None
    assert _apply_effect_change_corrections("shield", list(shield.passive_effects)) == list(
        shield.passive_effects
    )


def test_color_sprays_blinded_effect_applies_the_blinded_condition() -> None:
    """The pinned Foundry effect is named Blinded and carries no status."""
    spell = LOADER.get_spell("color-spray")
    assert spell is not None
    assert "condition until the end of your next turn" in spell.description
    assert _effect("color-spray", "DKl58EVNaSX976co").statuses == ["blinded"]
    bare = [e.model_copy(update={"statuses": []}) for e in spell.passive_effects]
    corrected = _apply_effect_status_corrections("color-spray", bare)
    assert corrected[0].statuses == ["blinded"]
    assert _apply_effect_status_corrections("color-spray", corrected) == corrected
    assert set(_EFFECT_STATUS_CORRECTIONS) == {("color-spray", "DKl58EVNaSX976co")}
    for (slug, effect_id), statuses in _EFFECT_STATUS_CORRECTIONS.items():
        data = json.loads((CANONICAL / "spells" / f"{slug}.json").read_text(encoding="utf-8"))
        [effect] = [e for e in data["passive_effects"] if e["id"] == effect_id]
        assert all(st in effect["statuses"] for st in statuses), (slug, effect_id)
