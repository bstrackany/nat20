"""``MonsterAI`` — the monster turn's decisions behind one replaceable object.

The default is the engine's own behaviour, so with nothing installed every
other test in this suite is the regression check. These tests install an
override for one decision at a time and show that only that decision changes.
"""

from __future__ import annotations

import asyncio

import pytest

from dnd5e_engine import (
    EncounterMemberSpec,
    GridScene,
    MonsterAI,
    PartyMemberSpec,
    set_monster_ai,
    start_combat,
)
from dnd5e_engine.events import AttackRolled, DashTaken, IntentSubmitted
from dnd5e_engine.monster_ai import monster_ai
from dnd5e_engine.orchestrator import _get_live, advance_monster_turn
from dnd5e_engine.spatial import cell_id as cell


@pytest.fixture(autouse=True)
def _default_ai():
    set_monster_ai(None)
    yield
    set_monster_ai(None)


def _pc(entity_id: str, hp: int, x: int) -> PartyMemberSpec:
    return PartyMemberSpec(
        entity_id=entity_id,
        name=entity_id,
        initiative=1,
        hp_current=hp,
        hp_max=hp,
        ac=10,
        class_slug="fighter",
        character_level=3,
        zone_id=cell(x, 0),
    )


async def _goblin_turn(*, goblin_x: int = 1, seed: int = 3):
    start = await start_combat(
        session_id="monster-ai",
        party=[_pc("char:hurt", 5, 0), _pc("char:fresh", 40, 2)],
        encounter=[
            EncounterMemberSpec(
                entity_id="mon:gob",
                entity_type="Monster",
                name="goblin",
                initiative=20,
                hp_current=10,
                hp_max=10,
                ac=15,
                monster_template_slug="goblin-warrior",
                zone_id=cell(goblin_x, 0),
            )
        ],
        grid_scene=GridScene(width=12, height=4, cell_size_ft=5),
        rng_seed=seed,
    )
    live = _get_live(start.handle)
    await advance_monster_turn(start.handle)
    return live


def _events(live, kind):
    return [e for e in live.event_log if isinstance(e, kind)]


def test_the_default_is_the_engines_own_choice_lowest_hp():
    live = asyncio.run(_goblin_turn())
    [intent] = [e for e in _events(live, IntentSubmitted) if e.actor_id == "mon:gob"]
    assert intent.target_id == "char:hurt"


def test_an_installed_ai_overrides_one_decision_and_the_rest_fall_through():
    class SpareTheWounded(MonsterAI):
        def choose_target(self, live, actor, enemies):
            fresh = [c for c in enemies if c.hp_current == c.hp_max]
            return fresh[0] if fresh else super().choose_target(live, actor, enemies)

    set_monster_ai(SpareTheWounded())
    live = asyncio.run(_goblin_turn())
    [intent] = [e for e in _events(live, IntentSubmitted) if e.actor_id == "mon:gob"]
    assert intent.target_id == "char:fresh"
    assert _events(live, AttackRolled), "action choice and resolution are untouched"


def test_declining_a_target_makes_the_turn_a_pass():
    class Pacifist(MonsterAI):
        def choose_target(self, live, actor, enemies):
            return None

    set_monster_ai(Pacifist())
    live = asyncio.run(_goblin_turn())
    [intent] = [e for e in _events(live, IntentSubmitted) if e.actor_id == "mon:gob"]
    assert intent.intent_type == "pass"
    assert not _events(live, AttackRolled)


def test_a_fearless_ai_keeps_a_bloodied_monster_fighting():
    class Fearless(MonsterAI):
        def is_fleeing(self, live, actor):
            return False

    async def _go():
        start = await start_combat(
            session_id="fearless",
            party=[_pc("char:hurt", 5, 0)],
            encounter=[
                EncounterMemberSpec(
                    entity_id="mon:gob",
                    entity_type="Monster",
                    name="goblin",
                    initiative=20,
                    hp_current=1,  # 5%: below the 10% flee threshold
                    hp_max=20,
                    ac=15,
                    monster_template_slug="goblin-warrior",
                    zone_id=cell(1, 0),
                )
            ],
            grid_scene=GridScene(width=12, height=4, cell_size_ft=5),
            rng_seed=3,
        )
        live = _get_live(start.handle)
        await advance_monster_turn(start.handle)
        return live

    def _goblin_swings(live):
        return [a for a in _events(live, AttackRolled) if a.attacker_id == "mon:gob"]

    live = asyncio.run(_go())
    assert not _goblin_swings(live), "the default flees at 5% HP (and eats an opportunity attack)"
    set_monster_ai(Fearless())
    live = asyncio.run(_go())
    assert _goblin_swings(live)


def test_plan_move_replaces_the_closing_walk_but_not_the_walking():
    seen: dict[str, object] = {}

    class StraightLine(MonsterAI):
        def plan_move(self, live, actor, target, range_ft):
            seen["range_ft"] = range_ft
            here = live.actor_zone[actor.entity_id]
            there = live.actor_zone[target.entity_id]
            return live.topology.shortest_path(here, there), None  # the engine's route, no Dash

    set_monster_ai(StraightLine())
    live = asyncio.run(_goblin_turn(goblin_x=11))  # 55 ft away: the default would Dash
    assert seen["range_ft"] == 5
    assert not _events(live, DashTaken)
    assert live.actor_zone["mon:gob"] != cell(11, 0), "the engine still walked the planned path"


def test_set_monster_ai_none_restores_the_default():
    class Anything(MonsterAI):
        pass

    set_monster_ai(Anything())
    assert isinstance(monster_ai(), Anything)
    set_monster_ai(None)
    assert type(monster_ai()) is MonsterAI
