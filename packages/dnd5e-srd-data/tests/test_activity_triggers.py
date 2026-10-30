"""When an activity fires (``Activity.trigger``, translator override table).

Foundry fires every activity on use and has no field for a later moment, so an
SRD 5.2 spell whose text names one — "at the end of its next turn", "the next
time you hit a creature", "you can take a Magic action to call down lightning
in that way again" — ships with every activity firing at the cast. The
translator sets ``trigger`` from ``tools.translators.foundry._ACTIVITY_TRIGGERS``;
these tests pin the shipped canonical output against that table and the
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


def _activity(slug: str, activity_id: str):
    spell = LOADER.get_spell(slug)
    assert spell is not None, slug
    [activity] = [a for a in spell.activities if a.id == activity_id]
    return activity


def test_an_activity_fires_with_its_parent_by_default() -> None:
    assert _activity("acid-arrow", "UvDr2eESFyDiTwTX").trigger is None
    assert ActivityTrigger(kind="turn_end") == ActivityTrigger(
        kind=ActivityTriggerKind.TURN_END,
        subject=ActivityTriggerSubject.TARGET,
        attack="",
        against_bearer=False,
        once=False,
        ends_on_save=False,
        also_at_cast=False,
    )


def test_a_trigger_names_a_known_moment() -> None:
    with pytest.raises(ValidationError):
        ActivityTrigger(kind="when_convenient")
    with pytest.raises(ValidationError):
        ActivityTrigger(kind="hits", attack="thrown")


def test_an_absent_trigger_is_left_out_of_a_dump() -> None:
    """The field is additive: an activity that fires with its parent dumps as it
    did before, so only the entries the table names changed on disk."""
    assert "trigger" not in _activity("acid-arrow", "UvDr2eESFyDiTwTX").model_dump(mode="json")
    dumped = _activity("acid-arrow", "uAyE5DrJp0YpElcw").model_dump(mode="json")
    assert dumped["trigger"] == {
        "kind": "turn_end",
        "subject": "target",
        "attack": "",
        "against_bearer": False,
        "once": True,
        "once_per_turn": False,
        "ends_on_save": False,
        "also_at_cast": False,
    }


def test_acid_arrow_lingers_at_the_end_of_the_targets_next_turn() -> None:
    spell = LOADER.get_spell("acid-arrow")
    assert spell is not None
    assert "2d4 Acid damage at the end of its next turn" in spell.description
    trigger = _activity("acid-arrow", "uAyE5DrJp0YpElcw").trigger
    assert trigger == ActivityTrigger(kind="turn_end", once=True)


def test_searing_smite_burns_at_the_start_of_each_of_the_targets_turns() -> None:
    spell = LOADER.get_spell("searing-smite")
    assert spell is not None
    # The cast is the hit: "immediately after hitting a target with a Melee
    # weapon or an Unarmed Strike", so its "extra 1d6 Fire damage" fires at cast.
    assert spell.casting_time.condition.startswith("immediately after hitting")
    assert _activity("searing-smite", "M4KS57lcf4K6fUMU").trigger is None
    assert "At the start of each of its turns until the spell ends" in spell.description
    save = _activity("searing-smite", "dnd5eactivity000")
    assert isinstance(save, SaveActivity)
    assert save.trigger == ActivityTrigger(kind="turn_start", ends_on_save=True)
    # "the target takes 1d6 Fire damage and then makes a Constitution saving
    # throw": the damage lands whatever the save.
    assert save.damage.on_save == "full"


@pytest.mark.parametrize(
    ("slug", "activity_id", "sentence"),
    [
        ("hex", "dnd5eactivity000", "whenever you hit it with an attack roll"),
        ("hunters-mark", "dnd5eactivity000", "whenever you hit it with an attack roll"),
    ],
)
def test_marks_add_their_damage_on_a_hit_against_the_marked_creature(
    slug: str, activity_id: str, sentence: str
) -> None:
    spell = LOADER.get_spell(slug)
    assert spell is not None
    assert sentence in spell.description
    assert _activity(slug, activity_id).trigger == ActivityTrigger(
        kind="hits", subject="caster", against_bearer=True
    )


@pytest.mark.parametrize(
    ("slug", "activity_id", "also_at_cast"),
    [
        ("sunbeam", "Y0cJvZuD7EfwGqkf", False),
        ("hex", "E3f3Y8ULb3t5mBQu", False),
        ("hunters-mark", "NQtVK0T5uwfMPGQL", False),
        ("heat-metal", "YPEmwJHX7g68307N", False),
        ("call-lightning", "dnd5eactivity000", True),
        ("call-lightning", "dnd5eactivity200", False),
        ("vampiric-touch", "dnd5eactivity000", True),
        ("telekinesis", "ntGShpXYlBqLMZmK", True),
        ("haste", "rBQrZnq7cHkSUAK9", False),
    ],
)
def test_repeatable_spells_mark_the_activity_used_on_later_turns(
    slug: str, activity_id: str, also_at_cast: bool
) -> None:
    spell = LOADER.get_spell(slug)
    assert spell is not None
    assert (
        "Until the spell ends" in spell.description
        or "on your later turns" in spell.description
        or "When the spell ends" in spell.description
    )
    assert _activity(slug, activity_id).trigger == ActivityTrigger(
        kind="later_use", also_at_cast=also_at_cast
    )


def test_weird_and_vitriolic_sphere_follow_up_at_the_end_of_the_targets_turn() -> None:
    spell = LOADER.get_spell("weird")
    assert spell is not None
    assert "makes a Wisdom saving throw at the end of each of its turns" in spell.description
    assert "On a successful save, the spell ends on that target." in spell.description
    weird = _activity("weird", "nuStSySOEkwOUnXf")
    assert isinstance(weird, SaveActivity)
    assert weird.trigger == ActivityTrigger(kind="turn_end", ends_on_save=True)
    # "On a failed save, it takes [[/damage 5d10 type=psychic]] damage."
    assert weird.damage.on_save == "none"
    assert _activity("vitriolic-sphere", "dnd5eactivity200").trigger == ActivityTrigger(
        kind="turn_end", once=True
    )
    assert _activity("ensnaring-strike", "ZWId9mbOE9zFnP6f").trigger == ActivityTrigger(
        kind="turn_start"
    )


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
