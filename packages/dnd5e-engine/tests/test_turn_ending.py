"""``TurnEnding`` — whether an Action ends the turn.

SRD 5.2 Your Turn: "On your turn, you can move a distance up to your Speed and
take one action. You decide whether to move first or take the action first."
The default ends the turn when the Action resolves, so every other test in this
suite is its regression check; these install one that keeps the turn open, and
check that the Action, once spent, stays spent.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from dnd5e_srd_data.loader import BundledAssetLoader

from dnd5e_engine import TurnEnding, set_turn_ending
from dnd5e_engine.events import AttackFailed, AttackRolled, DashTaken
from dnd5e_engine.lib_loader import set_lib_loader_for_tests
from dnd5e_engine.orchestrator import IntentRejectedError
from dnd5e_engine.spatial import cell_id
from tests.c20_support import act, combatant, events, pc, start

ROGUE_2: dict[str, Any] = {"class_slug": "rogue", "character_level": 2, "attack_bonus": 5}


@pytest.fixture(autouse=True)
def _default_turn_ending() -> Iterator[None]:
    set_lib_loader_for_tests(BundledAssetLoader())
    set_turn_ending(None)
    yield
    set_turn_ending(None)
    set_lib_loader_for_tests(None)


class _KeepOpen(TurnEnding):
    """Keeps every turn open after its Action and records who was asked."""

    def __init__(self) -> None:
        self.asked: list[str] = []

    def after_action(self, live, actor):
        self.asked.append(actor.entity_id)
        return False


def _swing(handle) -> None:
    act(handle, "char:hero", intent_type="attack", weapon_id="longsword", target_id="mon:foe")


def test_by_default_the_action_ends_the_turn() -> None:
    handle, live = start([pc(**ROGUE_2)], seed=1)
    act(handle, "char:hero", intent_type="dodge")
    assert live.current_actor_id == "mon:foe"


def test_kept_open_the_turn_goes_on_after_the_action_until_pass() -> None:
    """Dodge, then a Cunning Action Dash and a move: the Bonus Action and the
    movement are still the rogue's. ``pass`` ends the turn."""
    keep = _KeepOpen()
    set_turn_ending(keep)
    handle, live = start([pc(**ROGUE_2)], seed=1)
    act(handle, "char:hero", intent_type="dodge")
    assert (live.current_actor_id, keep.asked) == ("char:hero", ["char:hero"])
    act(handle, "char:hero", intent_type="dash", use_bonus_action=True)
    [dash] = events(live, DashTaken)
    assert (dash.budget_consumed, dash.doubled_movement_remaining) == ("bonus_action", 60)
    act(handle, "char:hero", intent_type="move", target_zone_id=cell_id(0, 9))
    hero = combatant(live)
    assert (hero.dodging, hero.action_available, hero.bonus_action_available) == (
        True,
        False,
        False,
    )
    assert hero.movement_remaining == 15
    act(handle, "char:hero", intent_type="pass")
    assert live.current_actor_id == "mon:foe"
    assert keep.asked == ["char:hero"]


def test_kept_open_a_second_action_is_refused() -> None:
    """One action per turn: after the Attack action's one swing, another swing
    is a turn-keeping ``AttackFailed`` and any other Action is refused."""
    set_turn_ending(_KeepOpen())
    handle, live = start([pc(**ROGUE_2)], seed=1)
    _swing(handle)
    assert live.current_actor_id == "char:hero"
    _swing(handle)
    assert len(events(live, AttackRolled)) == 1
    assert [e.reason for e in events(live, AttackFailed)] == ["no_action_economy"]
    for intent_type in ("dodge", "dash", "disengage"):
        with pytest.raises(IntentRejectedError, match="no_action_economy"):
            act(handle, "char:hero", intent_type=intent_type)
    assert live.current_actor_id == "char:hero"


def test_not_asked_while_an_action_surge_action_is_left() -> None:
    keep = _KeepOpen()
    set_turn_ending(keep)
    handle, live = start([pc(class_slug="fighter", character_level=2, attack_bonus=5)], seed=1)
    act(handle, "char:hero", intent_type="use_feature", feature_id="action-surge")
    act(handle, "char:hero", intent_type="dodge")
    assert keep.asked == []
    _swing(handle)
    assert keep.asked == ["char:hero"]
    assert live.current_actor_id == "char:hero"
