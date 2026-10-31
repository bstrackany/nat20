"""A Sphere aimed at a space: SRD 5.2 Fireball, "a point you choose within range"."""

from __future__ import annotations

from dnd5e_engine import events as events_module
from dnd5e_engine.orchestrator import PlayerIntent
from dnd5e_engine.specs import GridScene
from tests.e2e.harness import cell, events_of, run_async
from tests.test_c16_orchestrator import _cast, _damaged, _mon, _wiz


def test_fireball_bursts_on_an_empty_square_between_two_foes():
    live = run_async(
        _cast(
            GridScene(width=21, height=21),
            [_wiz(["fireball"], {3: 1}, cell(0, 10))],
            [_mon("mon:a", cell(10, 8)), _mon("mon:b", cell(10, 12))],
            PlayerIntent(
                intent_type="cast_spell", spell_id="fireball", target_zone_id=cell(10, 10)
            ),
        )
    )
    assert events_of(live, events_module.AreaTargeted)[0].origin == cell(10, 10)
    assert _damaged(live) == {"mon:a", "mon:b"}


def test_a_square_beyond_the_spells_range_is_refused_before_the_slot_is_spent():
    live = run_async(
        _cast(
            GridScene(width=41, height=21),
            [_wiz(["fireball"], {3: 1}, cell(0, 10))],
            [_mon("mon:a", cell(35, 10))],
            PlayerIntent(
                intent_type="cast_spell", spell_id="fireball", target_zone_id=cell(35, 10)
            ),
        )
    )
    assert [e.reason for e in events_of(live, events_module.CastFailed)] == ["out_of_range"]
    assert not events_of(live, events_module.SpellCast)
    assert not _damaged(live)
