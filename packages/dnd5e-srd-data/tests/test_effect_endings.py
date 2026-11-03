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
from tools.translators.foundry import _EFFECT_ENDINGS, _EFFECT_UNTIL, _apply_effect_endings

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


_A, _AT, _S, _D = (
    EffectEndEvent.ATTACKS,
    EffectEndEvent.IS_ATTACKED,
    EffectEndEvent.CASTS_SPELL,
    EffectEndEvent.DEALS_DAMAGE,
)
_HURT, _ZERO = EffectEndEvent.TAKES_DAMAGE, EffectEndEvent.ZERO_HP

#: (kind, slug, effect id, the SRD 5.2 words, ends_on, until)
_TABLE: list[tuple[str, str, str, str, list[EffectEndEvent], str]] = [
    (
        "spells",
        "invisibility",
        "9svfDetqjhsFP3ey",
        "makes an attack roll, deals damage, or casts a spell",
        [_A, _D, _S],
        "",
    ),
    (
        "spells",
        "hypnotic-pattern",
        "mjAs8ssQlgp0AQOp",
        "ends for an affected creature if it takes any damage",
        [_HURT],
        "",
    ),
    (
        "spells",
        "guiding-bolt",
        "rHzZCeXfdeTcYyk8",
        "the next attack roll made against it before the end of your next turn",
        [_AT],
        "end_of_next_turn",
    ),
    (
        "spells",
        "sanctuary",
        "OuM8iKhckcDKavOx",
        "makes an attack roll, casts a spell, or deals damage",
        [_A, _S, _D],
        "",
    ),
    (
        "spells",
        "gaseous-form",
        "QeYsfnjEj2T9A3C8",
        "The spell ends on the target if it drops to 0 Hit Points",
        [_ZERO],
        "",
    ),
    (
        "spells",
        "sleep",
        "04Wa4xUzjA31kPno",
        "The spell ends on a target if it takes damage",
        [_HURT],
        "",
    ),
    ("spells", "eyebite", "5btX5iwwleMMzkvj", "It wakes up if it takes any damage", [_HURT], ""),
    ("spells", "symbol", "A1VA7t5gB7ODNsr6", "A creature awakens if it takes damage", [_HURT], ""),
    (
        "spells",
        "modify-memory",
        "ayfixWGhTtaIUusk",
        "If it takes any damage or is targeted by another spell, this spell ends",
        [_HURT],
        "",
    ),
    (
        "spells",
        "sequester",
        "DWu3UoqOICFFQA1W",
        "This spell also ends if the target takes any damage",
        [_HURT],
        "",
    ),
    (
        "spells",
        "color-spray",
        "DKl58EVNaSX976co",
        "condition until the end of your next turn",
        [],
        "end_of_next_turn",
    ),
    (
        "spells",
        "flesh-to-stone",
        "sFyjp6wt9bjyIl9W",
        "its Speed is 0 until the start of your next turn",
        [],
        "start_of_next_turn",
    ),
    (
        "spells",
        "ray-of-frost",
        "7dnnwBMWLykrVJub",
        "reduced by 10 feet until the start of your next turn",
        [],
        "start_of_next_turn",
    ),
    (
        "spells",
        "shield",
        "Bv3EoHGfYCprLdG1",
        "Until the start of your next turn, you have a +5 bonus to AC",
        [],
        "start_of_next_turn",
    ),
    (
        "spells",
        "sunbeam",
        "RZmnRyi6pyjIRN1i",
        "Blinded condition until the start of your next turn",
        [],
        "start_of_next_turn",
    ),
    (
        "spells",
        "stinking-cloud",
        "gFid0DG5HydORpr3",
        "condition until the end of the current turn",
        [],
        "end_of_turn",
    ),
    (
        "features",
        "abjure-foes",
        "iY8KgqafX22tiBVw",
        "for 1 minute or until it takes any damage",
        [_HURT],
        "",
    ),
    (
        "features",
        "channel-divinity-cleric",
        "iUeSbfduM7K8XrCF",
        "This effect ends early on the creature if it takes any damage",
        [_HURT],
        "",
    ),
    (
        "features",
        "devious-strikes",
        "C2IGgt4PnRMxZVey",
        "for 1 minute or until it takes any damage",
        [_HURT],
        "",
    ),
    (
        "features",
        "brutal-strike",
        "bg9jWpPRIJ0Q2vPY",
        "reduced by 15 feet until the start of your next turn",
        [],
        "start_of_next_turn",
    ),
    (
        "features",
        "frosts-chill",
        "YHKgdj2KIbQRBZWW",
        "reduce its Speed by 10 feet until the start of your next turn",
        [],
        "start_of_next_turn",
    ),
    (
        "features",
        "natures-veil",
        "NOSQczI2MCQ5Ha8F",
        "the Invisible condition until the end of your next turn",
        [],
        "end_of_next_turn",
    ),
    (
        "features",
        "stunning-strike",
        "vofnieSTB0l8rpRg",
        "Stunned condition until the start of your next turn",
        [],
        "start_of_next_turn",
    ),
    (
        "features",
        "superior-hunters-defense",
        "P7YUT6Y1NXGpo5Er",
        "same type until the end of the current turn",
        [],
        "end_of_turn",
    ),
    (
        "items",
        "dust-of-disappearance",
        "MC5GAZMrAc4wSvAS",
        "makes an attack roll, deals damage, or casts a spell, the Invisible condition ends",
        [_A, _D, _S],
        "",
    ),
    (
        "items",
        "potion-of-invisibility",
        "tsRnLFf1HtqAPphv",
        "The effect ends early if you make an attack roll, deal damage, or cast a spell",
        [_A, _D, _S],
        "",
    ),
    (
        "items",
        "spiders-sting",
        "zEQTKqd3uBj660cR",
        "The creature wakes up if it takes damage",
        [_HURT],
        "",
    ),
]

_GET = {"spells": LOADER.get_spell, "features": LOADER.get_feature, "items": LOADER.get_item}


@pytest.mark.parametrize(("kind", "slug", "effect_id", "sentence", "ends_on", "until"), _TABLE)
def test_the_table_reached_the_corpus(
    kind: str, slug: str, effect_id: str, sentence: str, ends_on: list[EffectEndEvent], until: str
) -> None:
    entry = _GET[kind](slug)
    assert entry is not None
    assert sentence in " ".join(entry.description.split())
    [effect] = [e for e in entry.passive_effects if e.id == effect_id]
    assert (effect.ends_on, effect.until) == (ends_on, until)
    data = json.loads((CANONICAL / kind / f"{slug}.json").read_text(encoding="utf-8"))
    [shipped] = [e for e in data["passive_effects"] if e["id"] == effect_id]
    assert shipped.get("ends_on", []) == [str(e) for e in ends_on]
    assert shipped.get("until", "") == until
    bare = [e.model_copy(update={"ends_on": [], "until": ""}) for e in entry.passive_effects]
    assert _apply_effect_endings(slug, bare) == list(entry.passive_effects)


def test_every_ending_in_the_corpus_is_in_the_table() -> None:
    shipped_ends: dict[tuple[str, str], list[str]] = {}
    shipped_until: dict[tuple[str, str], str] = {}
    for kind in ("spells", "features", "feats", "items"):
        for path in sorted((CANONICAL / kind).glob("*.json")):
            data = json.loads(path.read_text(encoding="utf-8"))
            for effect in data.get("passive_effects", []):
                key = (data["slug"], effect["id"])
                if "ends_on" in effect:
                    shipped_ends[key] = effect["ends_on"]
                if "until" in effect:
                    shipped_until[key] = effect["until"]
    assert shipped_ends == {k: [str(e) for e in v] for k, v in _EFFECT_ENDINGS.items()}
    assert shipped_until == _EFFECT_UNTIL
    # every superior-hunters-defense damage type ends with the turn
    assert len([k for k in _EFFECT_UNTIL if k[0] == "superior-hunters-defense"]) == 13


def test_the_table_leaves_other_effects_alone() -> None:
    bless = LOADER.get_spell("bless")
    assert bless is not None
    assert _apply_effect_endings("bless", list(bless.passive_effects)) == list(
        bless.passive_effects
    )
