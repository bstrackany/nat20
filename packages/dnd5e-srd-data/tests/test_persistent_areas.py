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
    BundledAssetLoader,
    PersistentArea,
    TargetBlock,
    TargetTemplateBlock,
    UtilityActivity,
)
from tools.translators.foundry import (
    _AREAS,
    _FEATURE_EFFECT_VALUE_CORRECTIONS,
    _apply_areas,
    _apply_feature_effect_value_corrections,
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
        ("features", "aura-of-protection", "The aura is inactive while you have"),
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
