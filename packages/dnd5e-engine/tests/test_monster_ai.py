"""``MonsterAI`` — the monster turn's decisions behind one replaceable object.

The default is the engine's own behaviour, so every other test in this suite is
its regression check; these install an override for one decision at a time.
"""

from __future__ import annotations

import pytest

from dnd5e_engine import MonsterAI, set_monster_ai
from dnd5e_engine.events import AttackRolled, DashTaken, IntentSubmitted
from dnd5e_engine.monster_ai import monster_ai
from dnd5e_engine.spatial import cell_id as cell
from tests.c20_support import events, monster_turn, pc, start
from tests.fork_support import goblin


@pytest.fixture(autouse=True)
def _default_ai():
    set_monster_ai(None)
    yield
    set_monster_ai(None)


def _goblin_turn(ai: MonsterAI | None = None, *, goblin_x: int = 1, goblin_hp: int = 10):
    """A goblin (acting first) against a 5-HP PC at cell 0,0 and a fresh one at 2,0."""
    set_monster_ai(ai)
    party = [
        pc("char:hurt", initiative=1, hp_current=5, hp_max=5, ac=10),
        pc("char:fresh", initiative=1, hp_current=40, hp_max=40, ac=10, zone_id=cell(2, 0)),
    ]
    gob = goblin(
        entity_id="mon:gob",
        initiative=20,
        hp_current=goblin_hp,
        hp_max=20,
        zone_id=cell(goblin_x, 0),
    )
    handle, live = start(party, encounter=[gob], seed=3)
    monster_turn(handle)
    return live


def _goblin_intent(live) -> IntentSubmitted:
    [intent] = [e for e in events(live, IntentSubmitted) if e.actor_id == "mon:gob"]
    return intent


def test_the_default_is_the_engines_own_choice_lowest_hp():
    assert _goblin_intent(_goblin_turn()).target_id == "char:hurt"


def test_an_installed_ai_overrides_one_decision_and_the_rest_fall_through():
    class SpareTheWounded(MonsterAI):
        def choose_target(self, live, actor, enemies):
            fresh = [c for c in enemies if c.hp_current == c.hp_max]
            return fresh[0] if fresh else super().choose_target(live, actor, enemies)

    live = _goblin_turn(SpareTheWounded())
    assert _goblin_intent(live).target_id == "char:fresh"
    assert events(live, AttackRolled), "action choice and resolution are untouched"


def test_declining_a_target_makes_the_turn_a_pass():
    class Pacifist(MonsterAI):
        def choose_target(self, live, actor, enemies):
            return None

    live = _goblin_turn(Pacifist())
    assert _goblin_intent(live).intent_type == "pass"
    assert not events(live, AttackRolled)


def test_a_fearless_ai_keeps_a_bloodied_monster_fighting():
    class Fearless(MonsterAI):
        def is_fleeing(self, live, actor):
            return False

    def _goblin_swings(live):
        return [a for a in events(live, AttackRolled) if a.attacker_id == "mon:gob"]

    # 1 HP of 20 (5%): below the 10% flee threshold
    live = _goblin_turn(goblin_hp=1)
    assert not _goblin_swings(live), "the default flees at 5% HP"
    assert _goblin_swings(_goblin_turn(Fearless(), goblin_hp=1))


def test_plan_move_replaces_the_closing_walk_but_not_the_walking():
    seen: dict[str, object] = {}

    class StraightLine(MonsterAI):
        def plan_move(self, live, actor, target, range_ft):
            seen["range_ft"] = range_ft
            here = live.actor_zone[actor.entity_id]
            there = live.actor_zone[target.entity_id]
            return live.topology.shortest_path(here, there), None  # the engine's route, no Dash

    assert events(_goblin_turn(goblin_x=9), DashTaken), "45 ft away: the default Dashes"
    live = _goblin_turn(StraightLine(), goblin_x=9)
    assert seen["range_ft"] == 5
    assert not events(live, DashTaken)
    assert live.actor_zone["mon:gob"] != cell(9, 0), "the engine still walked the planned path"


def test_set_monster_ai_none_restores_the_default():
    class Anything(MonsterAI):
        pass

    set_monster_ai(Anything())
    assert isinstance(monster_ai(), Anything)
    set_monster_ai(None)
    assert type(monster_ai()) is MonsterAI
