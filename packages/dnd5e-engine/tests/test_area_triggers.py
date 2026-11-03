"""What an area does to the creatures it catches — ``PersistentArea.triggers``.

SRD 5.2 names the moments in each spell: Moonbeam "When the Cylinder appears,
each creature in it makes a Constitution saving throw ... A creature also makes
this save when the spell's area moves into its space and when it enters the
spell's area or ends its turn there. A creature makes this save only once per
turn"; Stinking Cloud "Each creature that starts its turn in the Sphere"; Spike
Growth "2d4 Piercing damage for every 5 feet it travels". An area affects
whoever is in it, its caster included, unless its text spares someone (Spirit
Guardians' designated creatures).
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from dnd5e_srd_data.schema.common import AreaTrigger, PersistentArea

from dnd5e_engine import CombatHandle
from dnd5e_engine.areas import AreaTemplate
from dnd5e_engine.events import (
    ActivityTriggered,
    AreaCreated,
    AreaEnded,
    AreaMoved,
    AreaTriggered,
    ConcentrationDropped,
    ConditionApplied,
    ConditionRemoved,
    DamageApplied,
    EffectApplied,
    SaveRolled,
)
from dnd5e_engine.lib_loader import set_lib_loader_for_tests
from dnd5e_engine.orchestrator import (
    IntentRejectedError,
    _area_effects_on,
    _drop_concentration,
    _LiveCombat,
    advance_monster_turn,
    push_combatant,
)
from dnd5e_engine.persistent_areas import (
    Area,
    away_point,
    line_path,
    same_spell_best,
    step_moments,
)
from tests.c20_support import act, events, pc, start
from tests.fork_support import BUNDLED, caster, goblin

SLOTS = {1: 4, 2: 4, 3: 4, 4: 4, 5: 2}
SLEET_SEED = 1


@pytest.fixture(autouse=True)
def _bundled() -> None:
    set_lib_loader_for_tests(BUNDLED)


def _cast(
    spell: str, *party: Any, seed: int = 1, **intent: Any
) -> tuple[CombatHandle, _LiveCombat]:
    handle, live = start([caster([spell], SLOTS), *party], seed=seed, encounter=[goblin()])
    act(handle, "char:caster", intent_type="cast_spell", spell_id=spell, **intent)
    return handle, live


def _fired(live: _LiveCombat) -> list[tuple[str, str]]:
    return [(e.trigger, e.creature_id) for e in events(live, AreaTriggered)]


def _saves(live: _LiveCombat) -> list[str]:
    return [e.target_id for e in events(live, SaveRolled)]


def test_moonbeam_saves_when_it_appears_then_once_per_turn() -> None:
    """The cast's save is the "appears" one and uses up that turn's save, so the
    cast turn's end fires nothing; each later turn end does. The caster stands
    in the beam, so it saves too."""
    handle, live = _cast("moonbeam", target_zone_id="1,0", slot_level=2)
    assert _saves(live) == ["char:caster", "mon:foe"]
    assert not _fired(live)
    act(handle, "mon:foe", intent_type="pass")
    assert _fired(live) == [("turn_end", "mon:foe")]
    [triggered] = events(live, ActivityTriggered)
    assert (triggered.trigger, triggered.actor_id) == ("area", "char:caster")
    assert {e.dc for e in events(live, SaveRolled)} == {15}


def test_moving_moonbeam_onto_a_creature() -> None:
    """ "you can take a Magic action on later turns to move the Cylinder up to
    60 feet": the creature it moves onto saves, once that turn."""
    handle, live = _cast("moonbeam", target_zone_id="6,6", slot_level=2)
    [created] = events(live, AreaCreated)
    act(handle, "mon:foe", intent_type="pass")
    assert not _fired(live)
    act(
        handle,
        "char:caster",
        intent_type="move_area",
        area_id=created.area_id,
        target_zone_id="2,0",
    )
    [moved] = events(live, AreaMoved)
    assert "1,0" in moved.cells
    assert _fired(live) == [("moved_onto", "mon:foe")]
    assert not live.initiative[0].action_available
    with pytest.raises(IntentRejectedError) as spent:
        act(
            handle,
            "char:caster",
            intent_type="move_area",
            area_id=created.area_id,
            target_zone_id="4,0",
        )
    assert spent.value.reason == "no_action_economy"


@pytest.mark.parametrize(
    ("area_id", "point", "move_ft", "reason"),
    [
        ("area:none", "3,0", 60, "target_invalid"),  # no such area
        (None, "30,30", 60, "target_invalid"),  # off the grid
        (None, "6,0", 10, "out_of_range"),  # 30 ft away, 10 allowed
    ],
)
def test_a_move_area_the_spell_doesnt_allow_is_refused(
    area_id: str | None, point: str, move_ft: int, reason: str
) -> None:
    handle, live = _cast("moonbeam", target_zone_id="6,6", slot_level=2)
    act(handle, "mon:foe", intent_type="pass")
    [area] = live.areas
    area.spec = area.spec.model_copy(update={"move_ft": move_ft})
    with pytest.raises(IntentRejectedError) as refused:
        act(
            handle,
            "char:caster",
            intent_type="move_area",
            area_id=area_id or area.area_id,
            target_zone_id=point,
        )
    assert refused.value.reason == reason
    assert area.origin == "6,6"
    assert live.initiative[0].action_available


@pytest.mark.parametrize(
    ("point", "caster_saves"),
    [
        # the cloud covers the caster: it saves at its own turn start
        ("1,1", True),
        # the cloud is elsewhere: nothing reaches the caster, at the cast or later
        ("8,8", False),
    ],
)
def test_stinking_cloud_affects_whoever_starts_a_turn_in_it(point: str, caster_saves: bool) -> None:
    """ "Each creature that starts its turn in the Sphere must succeed on a
    Constitution saving throw" — nobody at the cast, the caster only when
    inside."""
    handle, live = _cast("stinking-cloud", target_zone_id=point, slot_level=3)
    # the cast ended the caster's turn; the goblin (inside "1,1") starts its own
    assert _saves(live) == (["mon:foe"] if caster_saves else [])
    act(handle, "mon:foe", intent_type="pass")
    expected = [("turn_start", "mon:foe"), ("turn_start", "char:caster")] if caster_saves else []
    assert _fired(live) == expected
    assert ("char:caster" in _saves(live)) is caster_saves


@pytest.mark.parametrize(("point", "inside"), [("0,0", True), ("8,8", False)])
def test_silence_reaches_its_caster_only_inside(point: str, inside: bool) -> None:
    """Silence's creatures are immune to Thunder "while entirely inside it": a
    caster inside has it through the area, one outside never does, and the cast
    gives nobody a copy."""
    _handle, live = _cast("silence", target_zone_id=point, slot_level=2)
    assert not [e for e in events(live, EffectApplied) if "kjb" in e.effect.id.lower()]
    assert not [e for e in events(live, ConditionApplied) if e.target_id == "char:caster"]
    assert bool(_area_effects_on(live, "char:caster")) is inside


def test_spirit_guardians_spares_the_casters_side() -> None:
    """ "you can designate creatures to be unaffected by it" — the caster's
    side; the enemy saves when it ends its turn there, not when cast."""
    handle, live = _cast(
        "spirit-guardians", pc("char:ally", zone_id="0,1", initiative=10), slot_level=3
    )
    assert not _saves(live)
    act(handle, "char:ally", intent_type="pass")
    act(handle, "mon:foe", intent_type="pass")
    act(handle, "char:caster", intent_type="pass")
    assert _fired(live) == [("turn_end", "mon:foe")]
    assert not [e for e in events(live, EffectApplied) if e.effect.target_id == "mon:foe"]


def test_spirit_guardians_moves_onto_a_creature_on_its_bearers_walk() -> None:
    """ "whenever the Emanation enters a creature's space" — mid-walk, after
    the step that brings it there, and once that turn however far it goes."""
    handle, live = start(
        [caster(["spirit-guardians"], SLOTS)], seed=1, encounter=[goblin(zone_id="6,0")]
    )
    act(handle, "char:caster", intent_type="cast_spell", spell_id="spirit-guardians", slot_level=3)
    act(handle, "mon:foe", intent_type="pass")
    act(handle, "char:caster", intent_type="move", target_zone_id="3,1")
    assert _fired(live) == [("moved_onto", "mon:foe")]


def test_spike_growth_hurts_per_5_feet_moved_in_it() -> None:
    """ "When a creature moves into or within the area, it takes 2d4 Piercing
    damage for every 5 feet it travels" — no damage at the cast; the caster
    walking two cells into it takes it twice, each after its own step."""
    handle, live = _cast("spike-growth", target_zone_id="6,0", slot_level=2)
    assert not events(live, DamageApplied)
    act(handle, "mon:foe", intent_type="pass")
    act(handle, "char:caster", intent_type="move", target_zone_id="3,0")
    assert _fired(live) == [("moves_within", "char:caster")] * 2
    spikes = [e for e in events(live, DamageApplied) if e.damage_type == "piercing"]
    assert [e.target_id for e in spikes] == ["char:caster"] * 2
    assert live.actor_zone["char:caster"] == "3,0"


def test_a_push_into_the_webs_restrains_and_the_spell_ending_frees() -> None:
    """Web: "The first time a creature enters the webs on a turn ... or have the
    Restrained condition"; a push enters them (the save once, however many cells
    it crosses), and the Restrained ends with the webs."""
    handle, live = _cast("web", direction=(1, 1), slot_level=2, seed=4)
    assert not _saves(live)
    live.actor_zone["mon:foe"] = "3,7"
    push_combatant(live, "mon:foe", "3,8", 15)
    assert _fired(live) == [("enters", "mon:foe")]
    [save] = events(live, SaveRolled)
    assert not save.succeeded
    assert [e.condition for e in events(live, ConditionApplied)] == ["restrained"]
    act(handle, "mon:foe", intent_type="pass")
    _drop_concentration(live, "char:caster")
    assert events(live, AreaEnded)
    assert [e.condition for e in events(live, ConditionRemoved)] == ["restrained"]


def test_grease_prone_stands_after_the_grease_is_gone() -> None:
    """Grease: "must succeed on a Dexterity saving throw or have the Prone
    condition" when it appears — Prone is a condition, not the spell's."""
    handle, live = _cast("grease", direction=(1, 0), seed=2)
    [save] = events(live, SaveRolled)
    assert (save.target_id, save.succeeded) == ("mon:foe", False)
    [area] = live.areas
    area.rounds_remaining = 0
    act(handle, "mon:foe", intent_type="pass")
    assert events(live, AreaEnded)
    assert not events(live, ConditionRemoved)


def test_sleet_storm_breaks_a_failed_savers_concentration() -> None:
    """ "or have the Prone condition and lose Concentration" — an ally holding
    Fog Cloud starts its turn in the storm and fails."""
    ally = pc(
        "char:ally",
        zone_id="5,5",
        initiative=25,
        dexterity=1,
        class_slug="wizard",
        character_level=5,
        intelligence=16,
        spells_known=["fog-cloud"],
        spell_slots={1: 2},
    )
    handle, live = start(
        [caster(["sleet-storm"], SLOTS), ally], seed=SLEET_SEED, encounter=[goblin()]
    )
    act(handle, "char:ally", intent_type="cast_spell", spell_id="fog-cloud", target_zone_id="9,0")
    act(
        handle,
        "char:caster",
        intent_type="cast_spell",
        spell_id="sleet-storm",
        target_zone_id="5,5",
        slot_level=3,
    )
    assert not _saves(live)
    act(handle, "mon:foe", intent_type="pass")
    assert _fired(live) == [("turn_start", "char:ally")]
    assert [e.condition for e in events(live, ConditionApplied)] == ["prone"]
    assert [e.target_id for e in events(live, ConcentrationDropped)] == ["char:ally"]


def test_cloudkill_drifts_away_from_its_caster() -> None:
    """ "The Sphere moves 10 feet away from you at the start of each of your turns." """
    handle, live = _cast("cloudkill", target_zone_id="4,4", slot_level=5)
    act(handle, "mon:foe", intent_type="pass")
    [area] = live.areas
    assert area.origin == "6,6"
    [moved] = events(live, AreaMoved)
    assert "9,9" in moved.cells


def test_two_moonbeams_on_one_creature_give_one_save() -> None:
    """SRD 5.2 Combining Game Effects: "The effects of the same spell cast
    multiple times don't combine" — the goblin ends its turn in two beams and
    saves once."""
    ally = pc(
        "char:ally",
        zone_id="0,5",
        initiative=15,
        class_slug="wizard",
        character_level=5,
        intelligence=16,
        spells_known=["moonbeam"],
        spell_slots={2: 2},
    )
    handle, live = start([caster(["moonbeam"], SLOTS), ally], seed=1, encounter=[goblin()])
    for actor in ("char:caster", "char:ally"):
        act(
            handle,
            actor,
            intent_type="cast_spell",
            spell_id="moonbeam",
            target_zone_id="1,0",
            slot_level=2,
        )
    assert len(live.areas) == 2
    act(handle, "mon:foe", intent_type="pass")
    assert _fired(live).count(("turn_end", "mon:foe")) == 1


def test_a_monster_walking_through_spikes_is_hurt_per_step() -> None:
    handle, live = start(
        [caster(["spike-growth"], SLOTS)], seed=1, encounter=[goblin(zone_id="8,0")]
    )
    act(
        handle,
        "char:caster",
        intent_type="cast_spell",
        spell_id="spike-growth",
        target_zone_id="5,0",
        slot_level=2,
    )
    asyncio.run(advance_monster_turn(handle))
    walked = [e for e in _fired(live) if e[1] == "mon:foe"]
    assert walked
    assert set(walked) == {("moves_within", "mon:foe")}


def _area(slug: str, seq: int, slot: int | None = None, **spec: Any) -> Area:
    return Area(
        area_id=f"a{seq}",
        owner_id="o",
        source_kind="spell",
        source_slug=slug,
        source_name=slug,
        spec=PersistentArea(**spec),
        template=AreaTemplate("sphere", "sphere", 5, "target", True),
        kind="placed",
        cells=frozenset({"1,1"}),
        slot_level=slot,
        seq=seq,
    )


def test_the_pure_helpers() -> None:
    walk = _area("spike-growth", 1, triggers=[AreaTrigger.MOVES_WITHIN, AreaTrigger.ENTERS])
    assert step_moments(walk, False, True) == [AreaTrigger.ENTERS, AreaTrigger.MOVES_WITHIN]
    assert step_moments(walk, True, True) == [AreaTrigger.MOVES_WITHIN]
    assert step_moments(walk, True, False) == []
    # two castings of one spell: only the more potent fires
    low, high, other = _area("moonbeam", 1, 3), _area("moonbeam", 2, 2), _area("web", 3)
    assert same_spell_best([low, high, other]) == [low, other]
    assert line_path("0,0", "2,3") == ["1,1", "2,2", "2,3"]
    assert away_point("0,0", "2,1", 10, 5) == "4,3"
    assert away_point("2,2", "2,2", 10, 5) is None
