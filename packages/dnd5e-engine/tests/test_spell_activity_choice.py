"""``cast_spell`` honours ``PlayerIntent.activity_id``: a cast that names one of
the spell's activities resolves only that one, as a ``use_feature`` already
does. Naming none keeps every activity; naming one the spell lacks is refused
before the slot is spent."""

from __future__ import annotations

import asyncio
import typing

import pytest
from dnd5e_srd_data.loader import BundledAssetLoader

from dnd5e_engine import (
    EncounterMemberSpec,
    GridScene,
    PartyMemberSpec,
    PlayerIntent,
    start_combat,
    submit_player_intent,
)
from dnd5e_engine.events import (
    AttackRolled,
    CastFailed,
    CastFailedReason,
    DamageApplied,
    SaveRolled,
    SpellCast,
)
from dnd5e_engine.lib_loader import set_lib_loader_for_tests
from dnd5e_engine.orchestrator import _get_live
from dnd5e_engine.spatial import cell_id as cell

ACID_ARROW_ATTACK = "UvDr2eESFyDiTwTX"  # "Cast and Attack"; the other is "Lingering Acid Damage"
SUNBEAM_INITIAL = "o3kffaumfNGjVvg9"  # "Initial Cast"; the other is "Create New Sunbeam"


@pytest.fixture(autouse=True)
def _bundled_loader():
    set_lib_loader_for_tests(BundledAssetLoader())
    yield
    set_lib_loader_for_tests(None)


def _events(live, kind):
    return [e for e in live.event_log if isinstance(e, kind)]


def _cast(spell_id: str, slot_level: int, activity_id: str | None):
    async def _go():
        start = await start_combat(
            session_id="activity-choice",
            party=[
                PartyMemberSpec(
                    entity_id="char:caster",
                    name="caster",
                    initiative=20,
                    hp_current=30,
                    hp_max=30,
                    intelligence=16,
                    class_slug="wizard",
                    character_level=11,
                    spells_known=[spell_id],
                    spell_slots={slot_level: 1},
                    zone_id=cell(0, 0),
                )
            ],
            encounter=[
                EncounterMemberSpec(
                    entity_id="mon:foe",
                    entity_type="Monster",
                    name="foe",
                    initiative=1,
                    hp_current=100,
                    hp_max=100,
                    ac=1,
                    zone_id=cell(1, 0),
                )
            ],
            grid_scene=GridScene(width=10, height=10, cell_size_ft=5),
            rng_seed=1,
        )
        await submit_player_intent(
            start.handle,
            actor_id="char:caster",
            intent=PlayerIntent(
                intent_type="cast_spell",
                spell_id=spell_id,
                target_id="mon:foe",
                slot_level=slot_level,
                activity_id=activity_id,
            ),
        )
        return _get_live(start.handle)

    return asyncio.run(_go())


def test_a_cast_naming_no_activity_resolves_every_activity():
    live = _cast("acid-arrow", 2, None)
    assert len(_events(live, AttackRolled)) == 1
    assert len(_events(live, DamageApplied)) == 2  # the hit and the lingering acid, both now


def test_a_cast_naming_an_activity_resolves_only_that_one():
    live = _cast("acid-arrow", 2, ACID_ARROW_ATTACK)
    assert len(_events(live, AttackRolled)) == 1
    assert len(_events(live, DamageApplied)) == 1
    assert live.spell_slots_by_entity["char:caster"] == {2: 0}


def test_a_chosen_area_activity_still_takes_its_area():
    """SRD 5.2 Sunbeam: "Each creature in the Line makes a Constitution saving
    throw." The Initial Cast alone saves once, not once per Sunbeam activity."""
    assert len(_events(_cast("sunbeam", 6, None), SaveRolled)) == 2
    live = _cast("sunbeam", 6, SUNBEAM_INITIAL)
    assert len(_events(live, SaveRolled)) == 1
    assert len(_events(live, DamageApplied)) == 1


def test_an_activity_the_spell_lacks_is_refused_before_the_slot_is_spent():
    live = _cast("acid-arrow", 2, "not-an-activity")
    assert [e.reason for e in _events(live, CastFailed)] == ["activity_invalid"]
    assert live.spell_slots_by_entity["char:caster"] == {2: 1}
    assert not _events(live, SpellCast)


def test_activity_invalid_is_a_cast_failed_reason():
    assert "activity_invalid" in typing.get_args(CastFailedReason)
