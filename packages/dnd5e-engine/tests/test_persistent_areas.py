"""Areas that stay on the map — ``persistent_areas`` and ``Activity.area``.

A placed area (Fog Cloud, Web) stays where it was cast; an anchored one
(Spirit Guardians, Aura of Protection) moves with the creature it surrounds.
While it stands it changes the map, and its effects reach the creatures in it,
looked up live; it ends with its concentration, its duration or its bearer.
"""

from __future__ import annotations

from typing import Any

import pytest
from dnd5e_srd_data.schema.common import (
    ActivationBlock,
    AreaMap,
    DurationBlock,
    PassiveEffect,
    PassiveEffectChange,
    PersistentArea,
    TargetBlock,
    TargetTemplateBlock,
    UtilityActivity,
)

from dnd5e_engine import CombatHandle
from dnd5e_engine.activities.passive_stats import CombatantSenses
from dnd5e_engine.areas import AreaTemplate
from dnd5e_engine.events import (
    AreaCreated,
    AreaEnded,
    AreaMoved,
    CastFailed,
    ConditionApplied,
    SpellCast,
)
from dnd5e_engine.lib_loader import set_lib_loader_for_tests
from dnd5e_engine.orchestrator import (
    PlayerIntent,
    _area_effects_on,
    _build_hydration_payload,
    _emit,
    _find_combatant,
    _LiveCombat,
    _sync_areas,
    get_live,
    push_combatant,
)
from dnd5e_engine.persistent_areas import (
    Area,
    emanation_cells,
    in_area,
    map_overlay,
)
from dnd5e_engine.spatial import GridTopology
from dnd5e_engine.specs import GridScene
from tests.c20_support import act, events, pc, start
from tests.e2e.harness import cell, events_of, run_async
from tests.fork_support import BUNDLED, WithAssets, caster, goblin, homebrew_feat
from tests.test_c16_orchestrator import _cast, _mon, _wiz

SLOTS = {1: 4, 2: 4, 3: 4, 4: 4}


@pytest.fixture(autouse=True)
def _bundled() -> None:
    set_lib_loader_for_tests(BUNDLED)


def _cast_area(spell: str, **intent: Any) -> tuple[CombatHandle, _LiveCombat]:
    handle, live = start([caster([spell], SLOTS)], seed=1, encounter=[goblin()])
    act(handle, "char:caster", intent_type="cast_spell", spell_id=spell, **intent)
    return handle, live


def _save_bonus(live: _LiveCombat, entity_id: str) -> str | None:
    payload = _build_hydration_payload(live, caster=_find_combatant(live, entity_id))
    bonus: str | None = payload["save_modifiers"][entity_id].get("passive_save_bonus")
    return bonus


@pytest.mark.parametrize(
    ("spell", "intent", "obscured", "light", "difficult"),
    [
        # "The Sphere is Heavily Obscured."
        ("fog-cloud", {"target_zone_id": "5,5"}, "heavy", "bright", False),
        # "magical Darkness ... Darkvision can't see through it"
        ("darkness", {"target_zone_id": "5,5", "slot_level": 2}, "heavy", "dark", False),
        # "Until the spell ends, Dim Light fills the Cylinder"
        ("moonbeam", {"target_zone_id": "5,5", "slot_level": 2}, "none", "dim", False),
        ("sleet-storm", {"target_zone_id": "5,5", "slot_level": 3}, "heavy", "bright", True),
        ("spike-growth", {"target_zone_id": "5,5", "slot_level": 2}, "none", "bright", True),
        ("stinking-cloud", {"target_zone_id": "5,5", "slot_level": 3}, "heavy", "bright", False),
        # Cubes and squares extend from the caster in the aimed direction.
        ("web", {"direction": (1, 1), "slot_level": 2}, "light", "bright", True),
        ("entangle", {"direction": (1, 1)}, "none", "bright", True),
        ("grease", {"direction": (1, 1)}, "none", "bright", True),
    ],
)
def test_a_placed_area_changes_the_map_while_it_stands(
    spell: str, intent: dict[str, Any], obscured: str, light: str, difficult: bool
) -> None:
    handle, live = _cast_area(spell, **intent)
    [created] = events(live, AreaCreated)
    assert (created.kind, created.source_id, created.anchor_id) == ("placed", spell, None)
    inside = "5,5" if "target_zone_id" in intent else "2,2"
    assert inside in created.cells
    topology = live.topology
    assert topology.obscurement_on_cell(inside) == obscured
    assert topology.light_on_cell(inside) == light
    assert topology.edge_distance("4,4" if inside == "5,5" else "1,1", inside) == (
        10 if difficult else 5
    )
    assert topology.obscurement_on_cell("0,9") == "none"
    [view] = get_live(handle).areas.values()
    assert view.cells == tuple(created.cells)


@pytest.mark.parametrize(("slot", "size_ft"), [(1, 20), (2, 40)])
def test_fog_cloud_grows_with_the_slot(slot: int, size_ft: int) -> None:
    """SRD 5.2 Fog Cloud: "The fog's radius increases by 20 feet for each spell slot level above 1"."""
    _handle, live = _cast_area("fog-cloud", target_zone_id="5,5", slot_level=slot)
    [created] = events(live, AreaCreated)
    assert (created.shape, created.size_ft) == ("sphere", size_ft)


def test_darkness_hides_from_darkvision_but_not_blindsight() -> None:
    _handle, live = _cast_area("darkness", target_zone_id="1,0", slot_level=2)
    see = live.topology.can_see
    assert not see("9,9", "1,0", CombatantSenses(darkvision=120))
    assert see("9,9", "1,0", CombatantSenses(blindsight=60))


def test_a_point_beyond_the_spells_range_is_refused() -> None:
    live = run_async(
        _cast(
            GridScene(width=41, height=5),
            [_wiz(["darkness"], {2: 1}, cell(0, 0))],
            [_mon("mon:a", cell(35, 0))],
            PlayerIntent(intent_type="cast_spell", spell_id="darkness", target_zone_id="20,0"),
        )
    )
    assert [e.reason for e in events_of(live, CastFailed)] == ["out_of_range"]
    assert not events_of(live, SpellCast)
    assert not live.areas


def test_dropping_concentration_ends_the_area_and_restores_the_map() -> None:
    handle, live = _cast_area("fog-cloud", target_zone_id="5,5")
    act(handle, "mon:foe", intent_type="pass")
    act(handle, "char:caster", intent_type="drop_concentration")
    [created] = events(live, AreaCreated)
    assert [e.area_id for e in events(live, AreaEnded)] == [created.area_id]
    assert live.topology.obscurement_on_cell("5,5") == "none"
    assert not get_live(handle).areas


def test_a_recast_replaces_the_earlier_area() -> None:
    handle, live = start([caster(["fog-cloud"], {1: 3})], seed=1, encounter=[goblin()])
    for point in ("2,2", "7,7"):
        act(
            handle,
            "char:caster",
            intent_type="cast_spell",
            spell_id="fog-cloud",
            target_zone_id=point,
        )
        act(handle, "mon:foe", intent_type="pass")
    first, second = events(live, AreaCreated)
    assert [e.area_id for e in events(live, AreaEnded)] == [first.area_id]
    assert [a.area_id for a in live.areas] == [second.area_id]


def test_an_area_without_concentration_lasts_its_duration() -> None:
    """Grease lasts 1 minute (10 rounds), counted at the caster's turn ends."""
    handle, live = _cast_area("grease", direction=(1, 1))
    [area] = live.areas
    assert area.rounds_remaining == 9  # the cast turn's own end counts
    area.rounds_remaining = 1
    act(handle, "mon:foe", intent_type="pass")
    act(handle, "char:caster", intent_type="pass")
    assert len(events(live, AreaEnded)) == 1
    assert live.topology.edge_distance("1,1", "2,2") == 5


def test_silence_refuses_a_verbal_spell_cast_inside_it() -> None:
    """SRD 5.2 Silence: "Casting a spell that includes a Verbal component is impossible there"; a
    creature inside has "Immunity to Thunder damage"."""
    handle, live = start([caster(["silence", "fire-bolt"], SLOTS)], seed=1, encounter=[goblin()])
    act(
        handle,
        "char:caster",
        intent_type="cast_spell",
        spell_id="silence",
        slot_level=2,
        target_zone_id="0,0",
    )
    act(handle, "mon:foe", intent_type="pass")
    act(handle, "char:caster", intent_type="cast_spell", spell_id="fire-bolt", target_id="mon:foe")
    assert [e.reason for e in events(live, CastFailed)] == ["components_missing"]
    [immunity] = _area_effects_on(live, "mon:foe")
    assert [(c.key, c.value) for c in immunity.changes] == [("system.traits.di.value", "thunder")]


def test_an_emanation_follows_its_caster_and_ends_with_concentration() -> None:
    """Spirit Guardians: "Protective spirits flit around you in a 15-foot
    Emanation for the duration"."""
    handle, live = start([caster(["spirit-guardians"], SLOTS)], seed=1, encounter=[goblin()])
    act(
        handle,
        "char:caster",
        intent_type="cast_spell",
        spell_id="spirit-guardians",
        slot_level=3,
        target_id="mon:foe",
    )
    [created] = events(live, AreaCreated)
    assert (created.kind, created.anchor_id, created.origin) == ("anchored", "char:caster", None)
    assert "3,3" in created.cells
    assert "4,4" not in created.cells
    act(handle, "mon:foe", intent_type="pass")
    act(handle, "char:caster", intent_type="move", target_zone_id="0,2")
    push_combatant(live, "char:caster", "0,1", 5)
    moved = events(live, AreaMoved)
    assert [("3,5" in m.cells, "3,6" in m.cells) for m in moved] == [(True, False), (True, True)]
    act(handle, "char:caster", intent_type="drop_concentration")
    assert [e.area_id for e in events(live, AreaEnded)] == [created.area_id]


def _paladin(entity_id: str, cha: int, at: str, initiative: int = 20) -> Any:
    return pc(
        entity_id,
        charisma=cha,
        class_slug="paladin",
        character_level=7,
        zone_id=at,
        initiative=initiative,
    )


@pytest.mark.parametrize(
    ("cha", "ally_at", "ally", "foe"),
    [
        # "You and your allies in the aura gain a bonus to saving throws equal to
        # your Charisma modifier"; the foe beside the paladin gains nothing.
        (16, "0,2", "3", None),
        # "(minimum bonus of +1)"
        (8, "0,2", "1", None),
        # out of the 10-foot Emanation
        (16, "0,3", None, None),
    ],
)
def test_aura_of_protection_reaches_allies_in_it(
    cha: int, ally_at: str, ally: str | None, foe: str | None
) -> None:
    _handle, live = start(
        [_paladin("char:pal", cha, "0,0"), pc("char:ally", zone_id=ally_at, initiative=10)], seed=2
    )
    [created] = events(live, AreaCreated)
    assert (created.source_id, created.kind) == ("aura-of-protection", "anchored")
    assert _save_bonus(live, "char:pal") == str(max(1, (cha - 10) // 2))
    assert _save_bonus(live, "char:ally") == ally
    assert _save_bonus(live, "mon:foe") == foe


def test_aura_of_protection_is_inactive_while_its_paladin_is_incapacitated() -> None:
    _handle, live = start([_paladin("char:pal", 16, "0,0"), pc("char:ally", zone_id="0,1")], seed=2)
    _emit(live, ConditionApplied(target_id="char:pal", condition="stunned"))
    assert _save_bonus(live, "char:ally") is None
    assert _save_bonus(live, "char:pal") is None


def test_two_auras_of_protection_do_not_stack() -> None:
    """ "a creature can benefit from only one Aura of Protection at a time" — the
    better one applies."""
    _handle, live = start(
        [
            _paladin("char:pal", 16, "0,0"),
            _paladin("char:pal2", 18, "0,2", initiative=15),
            pc("char:ally", zone_id="0,1", initiative=10),
        ],
        seed=2,
    )
    assert _save_bonus(live, "char:ally") == "4"


def _area(cells: set[str], **spec: Any) -> Area:
    template = AreaTemplate("sphere", "sphere", 5, "target", True)
    return Area(
        area_id="a",
        owner_id="o",
        source_kind="spell",
        source_slug="s",
        source_name="S",
        spec=PersistentArea(map=AreaMap(**spec)),
        template=template,
        kind="placed",
        cells=frozenset(cells),
    )


def test_the_pure_helpers() -> None:
    walled = GridTopology(GridScene(width=5, height=5, blocked_cells=["1,0"]))
    cells = emanation_cells(walled, {"0,0"}, 10)
    assert {"0,0", "2,1"} <= cells
    assert "2,0" not in cells  # behind the blocked cell
    # a creature is in an area when ANY cell it occupies is
    assert in_area(_area({"3,3"}), {"2,2", "3,3"})
    assert not in_area(_area({"3,3"}), {"2,2"})
    overlay = map_overlay(
        [
            _area({"1,1", "2,2"}, obscurement="heavy", lighting="dark"),
            _area({"2,2"}, obscurement="light", lighting="dim", difficult_terrain=True),
        ]
    )
    assert overlay.obscurement == {"1,1": "heavy", "2,2": "heavy"}
    assert overlay.lighting == {"1,1": "dark", "2,2": "dim"}
    assert overlay.difficult_terrain == {"2,2"}


def test_a_used_feats_emanation_reaches_enemies_and_ends_with_its_bearer() -> None:
    """A homebrew feat's Bonus Action Emanation (the shape of Elder Champion's
    "enemies in the aura"): made when used, for the activity's own minute, its
    effect on enemies in it only; it ends when its bearer dies."""
    menace = homebrew_feat(
        "menacing-presence",
        activities=[
            UtilityActivity(
                _id="presence",
                activation=ActivationBlock(type="bonus"),
                duration=DurationBlock(value="1", units="minute"),
                target=TargetBlock(template=TargetTemplateBlock(type="radius", size="10")),
                area=PersistentArea(effects=["presencefx"], affects="enemy"),
            )
        ],
        passive_effects=[
            PassiveEffect(
                _id="presencefx",
                name="Menaced",
                changes=[
                    PassiveEffectChange(key="system.bonuses.abilities.save", mode=2, value="-2")
                ],
            )
        ],
    )
    set_lib_loader_for_tests(WithAssets(menace))
    handle, live = start(
        [pc(feats=("menacing-presence",)), pc("char:ally", zone_id="0,1", initiative=10)], seed=3
    )
    act(handle, "char:hero", intent_type="use_feature", feature_id="menacing-presence")
    [area] = live.areas
    assert (area.source_kind, area.rounds_remaining, area.anchor_id) == ("feat", 10, "char:hero")
    assert _save_bonus(live, "mon:foe") == "-2"
    assert _save_bonus(live, "char:ally") is None
    live.dead_ids.add("char:hero")
    _sync_areas(live)
    assert [e.area_id for e in events(live, AreaEnded)] == [area.area_id]
