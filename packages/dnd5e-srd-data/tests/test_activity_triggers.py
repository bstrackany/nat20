"""When an activity fires (``Activity.trigger``, translator override table).

Foundry fires every activity on use and has no field for a later moment, so the
translator sets ``trigger`` from ``tools.translators.foundry._ACTIVITY_TRIGGERS``;
these tests pin the shipped canonical output against that table and the SRD 5.2
sentence each entry follows.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from dnd5e_srd_data import (
    ActivityTrigger,
    ActivityTriggerKind,
    ActivityTriggerSubject,
    BundledAssetLoader,
)
from dnd5e_srd_data.schema.common import SaveActivity
from tools.translators.foundry import _ACTIVITY_TRIGGERS, _apply_activity_triggers

LOADER = BundledAssetLoader()
CANONICAL = Path(__file__).resolve().parent.parent / "src" / "dnd5e_srd_data" / "canonical"
_ON_HIT = ActivityTrigger(kind="hits", subject="caster", against_bearer=True)


def _activity(slug: str, activity_id: str):
    spell = LOADER.get_spell(slug)
    assert spell is not None, slug
    [activity] = [a for a in spell.activities if a.id == activity_id]
    return activity


def test_an_activity_fires_with_its_parent_by_default_and_dumps_as_before() -> None:
    attack = _activity("acid-arrow", "UvDr2eESFyDiTwTX")
    assert attack.trigger is None
    assert "trigger" not in attack.model_dump(mode="json")  # additive: unchanged on disk
    assert ActivityTrigger(kind="turn_end") == ActivityTrigger(
        kind=ActivityTriggerKind.TURN_END,
        subject=ActivityTriggerSubject.TARGET,
        attack="",
        against_bearer=False,
        once=False,
        ends_on_save=False,
        also_at_cast=False,
    )
    assert _activity("acid-arrow", "uAyE5DrJp0YpElcw").model_dump(mode="json")["trigger"] == {
        "kind": "turn_end",
        "subject": "target",
        "attack": "",
        "against_bearer": False,
        "once": True,
        "once_per_turn": False,
        "ends_on_save": False,
        "also_at_cast": False,
    }


def test_a_trigger_names_a_known_moment() -> None:
    with pytest.raises(ValidationError):
        ActivityTrigger(kind="when_convenient")
    with pytest.raises(ValidationError):
        ActivityTrigger(kind="hits", attack="thrown")


def _later(also_at_cast: bool = False) -> ActivityTrigger:
    return ActivityTrigger(kind="later_use", also_at_cast=also_at_cast)


_END_ONCE = ActivityTrigger(kind="turn_end", once=True)
_TABLE = [
    ("acid-arrow", "uAyE5DrJp0YpElcw", "2d4 Acid damage at the end of its next turn", _END_ONCE),
    ("vitriolic-sphere", "dnd5eactivity200", "damage at the end of its next turn", _END_ONCE),
    (
        "searing-smite",
        "dnd5eactivity000",
        "At the start of each of its turns until the spell ends",
        ActivityTrigger(kind="turn_start", ends_on_save=True),
    ),
    (
        "ensnaring-strike",
        "ZWId9mbOE9zFnP6f",
        "Piercing damage at the start of each of its turns",
        ActivityTrigger(kind="turn_start"),
    ),
    (
        "weird",
        "nuStSySOEkwOUnXf",
        "makes a Wisdom saving throw at the end of each of its turns",
        ActivityTrigger(kind="turn_end", ends_on_save=True),
    ),
    ("hex", "dnd5eactivity000", "whenever you hit it with an attack roll", _ON_HIT),
    ("hunters-mark", "dnd5eactivity000", "whenever you hit it with an attack roll", _ON_HIT),
    ("sunbeam", "Y0cJvZuD7EfwGqkf", "Until the spell ends", _later()),
    ("hex", "E3f3Y8ULb3t5mBQu", "Until the spell ends", _later()),
    ("hunters-mark", "NQtVK0T5uwfMPGQL", "Until the spell ends", _later()),
    ("heat-metal", "YPEmwJHX7g68307N", "on each of your later turns", _later()),
    ("call-lightning", "dnd5eactivity000", "Until the spell ends", _later(True)),
    ("call-lightning", "dnd5eactivity200", "Until the spell ends", _later()),
    ("vampiric-touch", "dnd5eactivity000", "Until the spell ends", _later(True)),
    ("telekinesis", "ntGShpXYlBqLMZmK", "on your later turns", _later(True)),
    ("haste", "rBQrZnq7cHkSUAK9", "When the spell ends", _later()),
]


@pytest.mark.parametrize(("slug", "activity_id", "sentence", "trigger"), _TABLE)
def test_each_trigger_follows_its_spells_sentence(
    slug: str, activity_id: str, sentence: str, trigger: ActivityTrigger
) -> None:
    spell = LOADER.get_spell(slug)
    assert spell is not None
    assert sentence in spell.description
    assert _activity(slug, activity_id).trigger == trigger


def test_a_triggered_save_keeps_its_damage_rule() -> None:
    spell = LOADER.get_spell("searing-smite")
    assert spell is not None
    # The cast is the hit: "immediately after hitting a target with a Melee
    # weapon or an Unarmed Strike", so its "extra 1d6 Fire damage" fires at cast.
    assert spell.casting_time.condition.startswith("immediately after hitting")
    assert _activity("searing-smite", "M4KS57lcf4K6fUMU").trigger is None
    save = _activity("searing-smite", "dnd5eactivity000")
    assert isinstance(save, SaveActivity)
    # "the target takes 1d6 Fire damage and then makes a Constitution saving
    # throw": the damage lands whatever the save.
    assert save.damage.on_save == "full"
    weird = _activity("weird", "nuStSySOEkwOUnXf")
    assert isinstance(weird, SaveActivity)
    # "On a failed save, it takes [[/damage 5d10 type=psychic]] damage."
    assert weird.damage.on_save == "none"


def test_every_trigger_in_the_table_reached_the_corpus_and_nothing_else_has_one() -> None:
    shipped: dict[tuple[str, str], ActivityTrigger] = {}
    for path in sorted((CANONICAL / "spells").glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        for activity in data["activities"]:
            if "trigger" in activity:
                shipped[(data["slug"], activity["id"])] = ActivityTrigger.model_validate(
                    activity["trigger"]
                )
    assert shipped == _ACTIVITY_TRIGGERS


def test_the_table_is_a_no_op_for_unmapped_activities() -> None:
    spell = LOADER.get_spell("fireball")
    assert spell is not None
    assert _apply_activity_triggers("fireball", list(spell.activities)) == list(spell.activities)
    acid = LOADER.get_spell("acid-arrow")
    assert acid is not None
    bare = [a.model_copy(update={"trigger": None}) for a in acid.activities]
    assert [a.trigger for a in _apply_activity_triggers("acid-arrow", bare)] == [
        None,
        ActivityTrigger(kind="turn_end", once=True),
    ]
