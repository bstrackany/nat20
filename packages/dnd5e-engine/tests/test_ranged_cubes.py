"""A Cube cast "from a point within range" (SRD 5.2 Web, Entangle, Faerie Fire)
is placed at the space the cast names, aimed from there; a Cube of range Self
(Thunderwave) still comes out of the caster's own space. The point is range-
checked, and a creature that aims for itself places a ranged Cube too."""

from __future__ import annotations

import asyncio

import pytest

from dnd5e_engine.areas import area_activity, area_cells, area_template, best_aim
from dnd5e_engine.events import AreaCreated, AreaTargeted, CastFailed, SpellCast
from dnd5e_engine.lib_loader import get_lib_loader, set_lib_loader_for_tests
from dnd5e_engine.orchestrator import _get_live, _reaches_a_point, start_combat
from dnd5e_engine.spatial import cell_id as cell
from dnd5e_engine.spatial import parse_cell
from dnd5e_engine.specs import GridScene
from tests.c20_support import events
from tests.fork_support import BUNDLED, cast, caster, goblin

CASTER = "char:caster"


@pytest.fixture(autouse=True)
def _bundled_loader():
    set_lib_loader_for_tests(BUNDLED)
    yield
    set_lib_loader_for_tests(None)


def _start(spell_id: str, slot: int, *, at: str = cell(3, 4), foe_at: str = cell(9, 6)):
    """A wizard at ``at`` on a 30x10 grid (150 by 50 feet), a goblin at ``foe_at``."""
    result = asyncio.run(
        start_combat(
            session_id="ranged-cubes",
            party=[caster([spell_id], {slot: 1}, zone_id=at, initiative=20)],
            encounter=[goblin(zone_id=foe_at, initiative=1)],
            grid_scene=GridScene(width=30, height=10),
            rng_seed=1,
        )
    )
    return result.handle, _get_live(result.handle)


def _template(spell_id: str, *, ranged: bool):
    spell = get_lib_loader().get_spell(spell_id)
    assert spell is not None
    activity = area_activity(spell.activities)
    return activity, area_template(activity, ranged=ranged)


def test_web_lands_at_the_named_space_aimed_from_there():
    handle, live = _start("web", 2)
    cast(handle, "web", slot_level=2, target_zone_id=cell(8, 5), direction=(1, 1))
    [area] = events(live, AreaCreated)
    _, template = _template("web", ranged=True)
    assert area.origin == cell(8, 5)
    cells = area_cells(live.topology, template, cell(8, 5), (1, 1))
    assert area.cells == sorted(cells, key=parse_cell)
    assert cell(8, 5) not in area.cells, "a Cube's point of origin is on its face"
    assert cell(4, 5) not in area.cells, "nothing comes out of the caster's space"


def test_faerie_fire_is_placed_at_the_named_space():
    handle, live = _start("faerie-fire", 1)
    cast(handle, "faerie-fire", target_zone_id=cell(8, 5), direction=(1, 0))
    [placed] = events(live, AreaTargeted)
    assert placed.origin == cell(8, 5)


def test_thunderwave_still_comes_out_of_the_caster():
    handle, live = _start("thunderwave", 1, foe_at=cell(4, 4))
    cast(handle, "thunderwave", target_id="mon:foe")
    [placed] = events(live, AreaTargeted)
    assert placed.origin == cell(3, 4)
    assert "mon:foe" in placed.affected_ids


@pytest.mark.parametrize(("spell_id", "slot"), [("web", 2), ("faerie-fire", 1)])
def test_a_ranged_cube_named_beyond_its_range_is_refused(spell_id, slot):
    """60 feet is 12 spaces; 20,4 is 17 spaces from the caster."""
    handle, live = _start(spell_id, slot)
    cast(handle, spell_id, slot_level=slot, target_zone_id=cell(20, 4), direction=(1, 0))
    assert [e.reason for e in events(live, CastFailed)] == ["out_of_range"]
    assert not events(live, SpellCast)
    assert live.spell_slots_by_entity[CASTER] == {slot: 1}


def test_a_creature_aiming_a_ranged_cube_puts_its_enemy_inside():
    """Each candidate's point of origin is one space behind an enemy in range,
    so the enemy is just inside the Cube's near face."""
    _, live = _start("web", 2)
    activity, template = _template("web", ranged=True)
    aim = best_aim(
        live.topology,
        activity,
        template,
        actor_id="mon:caster",
        actor_cell=cell(0, 0),
        target_cells=[cell(9, 6)],
        creatures=[("mon:caster", cell(0, 0)), (CASTER, cell(9, 6))],
        is_enemy=CASTER.__eq__,
        is_ally="mon:caster".__eq__,
    )
    assert aim is not None
    assert aim.selection.affected_ids == (CASTER,)
    assert aim.direction is not None
    col, row = 9 - aim.direction[0], 6 - aim.direction[1]
    assert aim.origin == cell(col, row)


@pytest.mark.parametrize(
    ("spell_id", "anchor"), [("web", "target"), ("entangle", "target"), ("thunderwave", "actor")]
)
def test_a_spells_range_decides_where_its_cube_is_anchored(spell_id, anchor):
    spell = get_lib_loader().get_spell(spell_id)
    _, template = _template(spell_id, ranged=_reaches_a_point(spell))
    assert template is not None
    assert template.anchor == anchor
