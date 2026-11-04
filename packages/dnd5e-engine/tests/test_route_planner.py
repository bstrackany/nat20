"""``RoutePlanner`` — which route a creature walks.

SRD 5.2 Movement and Position: a creature can "move a distance up to your
Speed"; the squares it crosses are its choice, priced square by square
(Difficult Terrain doubles one) and watched by every enemy whose reach it
leaves. The default returns the engine's own route, so every other movement
test in this suite is its regression check; these record what the planner is
asked and install one that routes elsewhere.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator

import pytest
from dnd5e_srd_data.loader import BundledAssetLoader

from dnd5e_engine import RoutePlanner, set_route_planner
from dnd5e_engine.events import ActorMoved, MoveFailed
from dnd5e_engine.lib_loader import set_lib_loader_for_tests
from dnd5e_engine.orchestrator import _get_live, _LiveCombat, advance_monster_turn, start_combat
from dnd5e_engine.route_planner import route_planner
from dnd5e_engine.spatial import cell_id
from dnd5e_engine.specs import EncounterMemberSpec, GridScene, PartyMemberSpec
from tests.c20_support import act, combatant, events, foe, pc, start


@pytest.fixture(autouse=True)
def _default_route_planner() -> Iterator[None]:
    set_lib_loader_for_tests(BundledAssetLoader())
    set_route_planner(None)
    yield
    set_route_planner(None)
    set_lib_loader_for_tests(None)


class _Recording(RoutePlanner):
    """Returns ``replacement`` (else the engine's route) and records each ask."""

    def __init__(self, replacement: list[str] | None = None) -> None:
        self.replacement = replacement
        self.asked: list[dict] = []

    def route(self, live, mover, engine_route, **ask):
        self.asked.append({"mover": mover.entity_id, "engine_route": engine_route, **ask})
        return self.replacement if self.replacement is not None else engine_route


def _cells(*pairs: tuple[int, int]) -> list[str]:
    return [cell_id(c, r) for c, r in pairs]


def test_set_route_planner_none_restores_the_default() -> None:
    set_route_planner(_Recording())
    set_route_planner(None)
    assert type(route_planner()) is RoutePlanner


def test_a_move_offers_the_engine_route_and_walks_it_by_default() -> None:
    recording = _Recording()
    set_route_planner(recording)
    handle, live = start([pc()], seed=1, encounter=[foe(zone_id=cell_id(9, 9))])
    expected = live.topology.shortest_path(cell_id(0, 0), cell_id(4, 0))
    act(handle, "char:hero", intent_type="move", target_zone_id=cell_id(4, 0))
    [ask] = recording.asked
    assert (ask["mover"], ask["purpose"], ask["start"], ask["goal"]) == (
        "char:hero",
        "move",
        cell_id(0, 0),
        cell_id(4, 0),
    )
    assert (ask["engine_route"], ask["budget_ft"]) == (expected, 30)
    assert ask["avoid"] == frozenset({cell_id(9, 9)})
    assert live.actor_zone["char:hero"] == cell_id(4, 0)
    assert combatant(live).movement_remaining == 10


def test_a_move_walks_and_pays_for_the_planner_route() -> None:
    """A five-square detour to a cell four squares away costs 25 feet."""
    detour = _cells((0, 0), (0, 1), (1, 2), (2, 2), (3, 1), (4, 0))
    set_route_planner(_Recording(detour))
    handle, live = start([pc()], seed=1, encounter=[foe(zone_id=cell_id(9, 9))])
    act(handle, "char:hero", intent_type="move", target_zone_id=cell_id(4, 0))
    assert [e.distance_ft for e in events(live, ActorMoved)] == [25]
    assert live.actor_zone["char:hero"] == cell_id(4, 0)
    assert combatant(live).movement_remaining == 5


def test_a_planner_route_over_budget_is_refused_whole() -> None:
    """Seven squares (35 ft) on a 30-foot budget: nothing moves."""
    detour = _cells((0, 0), (0, 1), (0, 2), (1, 3), (2, 3), (3, 2), (4, 1), (4, 0))
    set_route_planner(_Recording(detour))
    handle, live = start([pc()], seed=1, encounter=[foe(zone_id=cell_id(9, 9))])
    act(handle, "char:hero", intent_type="move", target_zone_id=cell_id(4, 0))
    assert [e.reason for e in events(live, MoveFailed)] == ["insufficient_movement"]
    assert live.actor_zone["char:hero"] == cell_id(0, 0)
    assert combatant(live).movement_remaining == 30


def test_an_empty_planner_route_is_unreachable() -> None:
    set_route_planner(_Recording([]))
    handle, live = start([pc()], seed=1, encounter=[foe(zone_id=cell_id(9, 9))])
    act(handle, "char:hero", intent_type="move", target_zone_id=cell_id(4, 0))
    assert [e.reason for e in events(live, MoveFailed)] == ["unreachable"]


def test_provokes_says_which_steps_leave_an_enemy_reach() -> None:
    """SRD 5.2 Opportunity Attacks: "when a creature that you can see leaves
    your reach". The goblin on 2,1 reaches columns 1-3, rows 0-2."""
    recording = _Recording()
    set_route_planner(recording)
    goblin = foe(monster_template_slug="goblin-warrior", zone_id=cell_id(2, 1))
    handle, _ = start([pc()], seed=1, encounter=[goblin])
    act(handle, "char:hero", intent_type="move", target_zone_id=cell_id(0, 3))
    provokes = recording.asked[0]["provokes"]
    assert provokes(cell_id(1, 0), cell_id(0, 0)) is True  # out of reach
    assert provokes(cell_id(1, 0), cell_id(1, 1)) is False  # around inside it
    assert provokes(cell_id(0, 0), cell_id(0, 1)) is False  # never in it


def _goblin_turn(goblin_cell: str, hp: int) -> _LiveCombat:
    """A goblin warrior on ``goblin_cell`` at ``hp``/20 takes the first turn
    against the hero on 0,0."""

    async def _inner() -> _LiveCombat:
        result = await start_combat(
            session_id="route-planner",
            party=[
                PartyMemberSpec(
                    entity_id="char:hero",
                    name="Hero",
                    initiative=1,
                    hp_current=30,
                    hp_max=30,
                    ac=12,
                    zone_id=cell_id(0, 0),
                )
            ],
            encounter=[
                EncounterMemberSpec(
                    entity_id="mon:goblin",
                    entity_type="Monster",
                    name="Goblin",
                    initiative=20,
                    hp_current=hp,
                    hp_max=20,
                    ac=15,
                    monster_template_slug="goblin-warrior",
                    zone_id=goblin_cell,
                )
            ],
            grid_scene=GridScene(width=10, height=10),
            rng_seed=1,
        )
        await advance_monster_turn(result.handle)
        return _get_live(result.handle)

    return asyncio.run(_inner())


def test_a_monster_closing_on_its_target_asks_with_purpose_approach() -> None:
    recording = _Recording()
    set_route_planner(recording)
    _goblin_turn(cell_id(5, 0), hp=20)
    [ask] = recording.asked
    assert (ask["mover"], ask["purpose"], ask["goal"]) == ("mon:goblin", "approach", cell_id(0, 0))
    assert ask["engine_route"][0] == cell_id(5, 0)
    assert ask["engine_route"][-1] == cell_id(0, 0)


def test_a_monster_closes_along_the_planner_route() -> None:
    """The walk stops once the hero is in reach: on 1,1, not the goal."""
    detour = _cells((5, 0), (4, 1), (3, 1), (2, 1), (1, 1), (0, 0))
    set_route_planner(_Recording(detour))
    live = _goblin_turn(cell_id(5, 0), hp=20)
    assert live.actor_zone["mon:goblin"] == cell_id(1, 1)


def test_a_fleeing_monster_asks_with_purpose_flee_and_walks_the_planner_route() -> None:
    recording = _Recording(_cells((3, 0), (4, 1), (5, 2)))
    set_route_planner(recording)
    live = _goblin_turn(cell_id(3, 0), hp=1)
    [ask] = recording.asked
    assert (ask["mover"], ask["purpose"]) == ("mon:goblin", "flee")
    assert ask["engine_route"] == _cells(*((c, 0) for c in range(3, 10)))
    assert ask["goal"] == cell_id(9, 0)
    assert live.actor_zone["mon:goblin"] == cell_id(5, 2)
