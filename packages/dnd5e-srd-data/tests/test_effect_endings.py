"""Effects that end on an event or a turn boundary — ``PassiveEffect.ends_on``
and ``.until`` (midi-qol's DAE "special durations"). Both are left out of a dump
when unset, so the corpus only changes where the translator's table sets one.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from dnd5e_srd_data import BundledAssetLoader, EffectEndEvent, PassiveEffect
from tools.translators.foundry import _EFFECT_ENDINGS, _apply_effect_endings

LOADER = BundledAssetLoader()
CANONICAL = Path(__file__).resolve().parent.parent / "src" / "dnd5e_srd_data" / "canonical"


def test_an_effect_without_an_ending_dumps_as_before() -> None:
    raw = {"_id": "x", "name": "Bless", "duration": {"rounds": 10}}
    effect = PassiveEffect.model_validate(raw)
    assert (effect.ends_on, effect.until) == ([], "")
    dumped = effect.model_dump(mode="json")
    assert "ends_on" not in dumped and "until" not in dumped
    assert PassiveEffect.model_validate(dumped).model_dump(mode="json") == dumped


def test_an_ending_round_trips() -> None:
    effect = PassiveEffect(_id="x", ends_on=["attacks", "zero_hp"], until="start_of_next_turn")
    dumped = effect.model_dump(mode="json")
    assert (dumped["ends_on"], dumped["until"]) == (["attacks", "zero_hp"], "start_of_next_turn")
    assert PassiveEffect.model_validate(dumped) == effect


@pytest.mark.parametrize("field", [{"ends_on": ["sneezes"]}, {"until": "next_week"}])
def test_an_unknown_ending_is_refused(field: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        PassiveEffect.model_validate({"_id": "x", **field})


@pytest.mark.parametrize(
    ("slug", "sentence"),
    [
        ("invisibility", "makes an attack roll, deals damage, or casts a spell"),
        ("hypnotic-pattern", "ends for an affected creature if it takes any damage"),
    ],
)
def test_the_table_reached_the_corpus(slug: str, sentence: str) -> None:
    spell = LOADER.get_spell(slug)
    assert spell is not None
    assert sentence in spell.description
    [(key, ends_on)] = [(k, v) for k, v in _EFFECT_ENDINGS.items() if k[0] == slug]
    [effect] = [e for e in spell.passive_effects if e.id == key[1]]
    assert effect.ends_on == ends_on
    data = json.loads((CANONICAL / "spells" / f"{slug}.json").read_text(encoding="utf-8"))
    [shipped] = [e for e in data["passive_effects"] if e["id"] == key[1]]
    assert shipped["ends_on"] == [str(e) for e in ends_on]
    bare = [e.model_copy(update={"ends_on": []}) for e in spell.passive_effects]
    assert _apply_effect_endings(slug, bare) == list(spell.passive_effects)


def test_the_table_leaves_other_effects_alone() -> None:
    bless = LOADER.get_spell("bless")
    assert bless is not None
    assert _apply_effect_endings("bless", list(bless.passive_effects)) == list(
        bless.passive_effects
    )
    assert set(_EFFECT_ENDINGS) == {
        ("invisibility", "9svfDetqjhsFP3ey"),
        ("hypnotic-pattern", "mjAs8ssQlgp0AQOp"),
    }
    assert EffectEndEvent.TAKES_DAMAGE in _EFFECT_ENDINGS[("hypnotic-pattern", "mjAs8ssQlgp0AQOp")]
