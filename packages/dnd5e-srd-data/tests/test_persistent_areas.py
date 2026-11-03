"""Activities that leave an area on the map — ``Activity.area``. Left out of a
dump when unset (and its defaults within it), so the corpus only changes where
the translator's ``_AREAS`` table sets one.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from dnd5e_srd_data import (
    AreaMap,
    AreaTrigger,
    BundledAssetLoader,
    PersistentArea,
    TargetBlock,
    TargetTemplateBlock,
    UtilityActivity,
)
from tools.translators.foundry import (
    _ADDED_SAVE_EFFECTS,
    _AREAS,
    _DROPPED_SAVE_DAMAGE,
    _FEATURE_EFFECT_VALUE_CORRECTIONS,
    _append_added_save_effects,
    _apply_added_save_effect_refs,
    _apply_areas,
    _apply_feature_effect_value_corrections,
    _drop_save_damage,
)

LOADER = BundledAssetLoader()
CANONICAL = Path(__file__).resolve().parent.parent / "src" / "dnd5e_srd_data" / "canonical"


def _sphere(**area: object) -> UtilityActivity:
    return UtilityActivity(
        _id="x",
        target=TargetBlock(template=TargetTemplateBlock(type="sphere", size="20")),
        **area,  # type: ignore[arg-type]
    )


def test_an_activity_without_an_area_dumps_as_before() -> None:
    assert "area" not in UtilityActivity(_id="x").model_dump(mode="json")
    assert PersistentArea().model_dump(mode="json") == {}


def test_an_area_round_trips() -> None:
    area = PersistentArea(
        map=AreaMap(obscurement="heavy", lighting="dark", difficult_terrain=True),
        effects=["fx"],
        affects="enemy",
        inactive_while_incapacitated=True,
    )
    activity = _sphere(area=area)
    dumped = activity.model_dump(mode="json")
    assert dumped["area"] == {
        "map": {"obscurement": "heavy", "lighting": "dark", "difficult_terrain": True},
        "effects": ["fx"],
        "affects": "enemy",
        "inactive_while_incapacitated": True,
    }
    assert UtilityActivity.model_validate(dumped) == activity


@pytest.mark.parametrize(
    "build",
    [
        # an area takes its shape from a template with a point of origin
        lambda: UtilityActivity(_id="x", area=PersistentArea()),
        lambda: UtilityActivity(
            _id="x",
            target=TargetBlock(template=TargetTemplateBlock(type="wall", size="10")),
            area=PersistentArea(),
        ),
        lambda: AreaMap(obscurement="total"),
        lambda: AreaMap(lighting="sunlight"),
        lambda: PersistentArea(affects="neutral"),
        lambda: PersistentArea(cover="half"),
        # a trigger option needs triggers; a moment once; only a placed area moves
        lambda: PersistentArea(once_per_turn=True),
        lambda: PersistentArea(fires="x"),
        lambda: PersistentArea(triggers=["enters", "enters"]),
        lambda: PersistentArea(triggers=["when_it_rains"]),
        lambda: PersistentArea(move_ft=0),
        lambda: UtilityActivity(
            _id="x",
            target=TargetBlock(template=TargetTemplateBlock(type="radius", size="10")),
            area=PersistentArea(move_ft=30),
        ),
    ],
)
def test_an_area_the_engine_cannot_run_is_refused(build) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(ValidationError):
        build()


@pytest.mark.parametrize(
    ("kind", "slug", "sentence"),
    [
        ("spells", "fog-cloud", "The Sphere is &amp;Reference[heavilyobscured]"),
        ("spells", "darkness", "Darkvision can't see through it"),
        ("spells", "grease", "turns it into &amp;Reference[difficultterrain]"),
        ("spells", "web", "the area within them is &amp;Reference[lightlyobscured]"),
        ("spells", "sleet-storm", "Ground in the Cylinder is &amp;Reference[difficultterrain]"),
        ("spells", "entangle", "turn the ground in the area into"),
        ("spells", "spike-growth", "The area becomes Difficult Terrain"),
        ("spells", "stinking-cloud", "The cloud is &amp;Reference[heavilyobscured]"),
        ("spells", "moonbeam", "Dim Light fills the Cylinder"),
        ("spells", "silence", "Casting a spell that includes a Verbal component is impossible"),
        ("spells", "spirit-guardians", "flit around you in a 15-foot Emanation"),
        ("spells", "black-tentacles", "A creature makes that save only once per turn"),
        ("spells", "insect-plague", "for the first time on a turn or ends its turn there"),
        ("spells", "cloudkill", "moves 10 feet away from you at the start of each"),
        ("spells", "incendiary-cloud", "A creature makes this save only once per turn"),
        ("features", "aura-of-protection", "The aura is inactive while you have"),
        ("spells", "daylight", "The sunlight's area is Bright Light"),
        ("spells", "holy-aura", "creatures of your choice have Advantage on all saving throws"),
        ("spells", "conjure-woodland-beings", "A creature makes this save only once per turn"),
        ("spells", "earthquake", "The ground there is Difficult Terrain"),
    ],
)
def test_the_table_reached_the_corpus(kind: str, slug: str, sentence: str) -> None:
    entry = LOADER.get_spell(slug) if kind == "spells" else LOADER.get_feature(slug)
    assert entry is not None
    assert sentence in entry.description
    [(key, area)] = [(k, v) for k, v in _AREAS.items() if k[0] == slug]
    [activity] = [a for a in entry.activities if a.id == key[1]]
    assert activity.area == area
    data = json.loads((CANONICAL / kind / f"{slug}.json").read_text(encoding="utf-8"))
    [shipped] = [a for a in data["activities"] if a["id"] == key[1]]
    assert shipped["area"] == area.model_dump(mode="json")
    bare = [a.model_copy(update={"area": None}) for a in entry.activities]
    assert _apply_areas(slug, bare) == list(entry.activities)


def test_an_areas_triggers_round_trip_and_dump_only_when_set() -> None:
    area = PersistentArea(
        triggers=[AreaTrigger.ENTERS, AreaTrigger.TURN_START],
        fires="save",
        once_per_turn=True,
        breaks_concentration=True,
        move_ft=60,
        drifts_ft=10,
    )
    dumped = area.model_dump(mode="json")
    assert dumped == {
        "triggers": ["enters", "turn_start"],
        "fires": "save",
        "once_per_turn": True,
        "breaks_concentration": True,
        "move_ft": 60,
        "drifts_ft": 10,
    }
    assert PersistentArea.model_validate(dumped) == area


@pytest.mark.parametrize(
    ("slug", "activity_id", "effect_id", "status", "sentence"),
    [
        (
            "grease",
            "dnd5eactivity000",
            "nat20GreaseProne",
            "prone",
            "or have the &amp;Reference[prone]",
        ),
        (
            "web",
            "dnd5eactivity000",
            "nat20WebRestrain",
            "restrained",
            "or have the &amp;Reference[restrained]",
        ),
        (
            "sleet-storm",
            "dnd5eactivity000",
            "nat20SleetProne0",
            "prone",
            "or have the &amp;Reference[prone]",
        ),
    ],
)
def test_a_failed_save_applies_its_condition(
    slug: str, activity_id: str, effect_id: str, status: str, sentence: str
) -> None:
    """The condition each sentence names on a failed save, which the pinned
    Foundry source leaves to a module; added with ``_ADDED_SAVE_EFFECTS``."""
    spell = LOADER.get_spell(slug)
    assert spell is not None
    assert sentence in spell.description
    [activity] = [a for a in spell.activities if a.id == activity_id]
    assert [(r.id, r.on_save) for r in activity.effects] == [(effect_id, False)]
    [effect] = [e for e in spell.passive_effects if e.id == effect_id]
    assert effect.statuses == [status]
    assert _ADDED_SAVE_EFFECTS[(slug, activity_id)] == effect
    bare = [a.model_copy(update={"effects": []}) for a in spell.activities]
    assert _drop_save_damage(slug, _apply_added_save_effect_refs(slug, bare)) == list(
        spell.activities
    )
    others = [e for e in spell.passive_effects if e.id != effect_id]
    assert _append_added_save_effects(slug, others) == list(spell.passive_effects)


def test_webs_burning_is_not_the_save_damage() -> None:
    """ "Any 5-foot Cube of webs exposed to fire burns away in 1 round, dealing
    2d4 Fire damage to any creature that starts its turn in the fire" — not the
    save's damage."""
    web = LOADER.get_spell("web")
    assert web is not None
    assert "burns away in 1 round" in web.description
    assert {("web", "dnd5eactivity000")} == _DROPPED_SAVE_DAMAGE
    [activity] = web.activities
    assert activity.damage.parts == []  # type: ignore[union-attr]


def test_aura_of_protection_has_its_minimum() -> None:
    """ "a bonus to saving throws equal to your Charisma modifier (minimum bonus
    of +1)"."""
    feature = LOADER.get_feature("aura-of-protection")
    assert feature is not None
    [(slug, effect_id, key)] = list(_FEATURE_EFFECT_VALUE_CORRECTIONS)
    [effect] = [e for e in feature.passive_effects if e.id == effect_id]
    [change] = [c for c in effect.changes if c.key == key]
    assert change.value == "max(1, @abilities.cha.mod)"
    foundry = [
        e.model_copy(
            update={
                "changes": [c.model_copy(update={"value": "@abilities.cha.mod"}) for c in e.changes]
            }
        )
        for e in feature.passive_effects
    ]
    assert _apply_feature_effect_value_corrections(slug, foundry) == list(feature.passive_effects)
