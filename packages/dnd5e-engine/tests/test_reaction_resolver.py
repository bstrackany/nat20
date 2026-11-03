"""``ReactionResolver`` — whether a reaction fires.

The default says yes to every eligible reaction, so every other test in this
suite is its regression check; these install one resolver at a time. Also
here: Shield answering an attack roll as it hits (``_raise_ac_when_hit``)
rather than before it is rolled.
"""

from __future__ import annotations

from typing import Any

import pytest

from dnd5e_engine import ReactionResolver, set_reaction_resolver
from dnd5e_engine.events import AttackRolled, ReactionTriggered
from dnd5e_engine.interrupts import D20Outcome, IncomingDamage, raised_target
from dnd5e_engine.orchestrator import _fire_opportunity_attacks_on_step
from dnd5e_engine.reaction_resolver import reaction_resolver
from dnd5e_engine.spatial import cell_id
from tests.c20_support import act, events, monster_turn, pc, start
from tests.fork_support import goblin

PC = "char:pc"


@pytest.fixture(autouse=True)
def _default_resolver():
    set_reaction_resolver(None)
    yield
    set_reaction_resolver(None)


class _Recording(ReactionResolver):
    """Says ``answer`` to every reaction and records what it was asked."""

    def __init__(self, answer: bool = True) -> None:
        self.answer = answer
        self.asked: list[tuple[str, str, Any]] = []

    def fires(self, live, reactor, reaction, trigger, context):
        self.asked.append((reactor.entity_id, trigger, context))
        return self.answer


class _AskError(Exception):
    """A host's "ask the player first"."""


class _Asking(ReactionResolver):
    def fires(self, live, reactor, reaction, trigger, context):
        raise _AskError(trigger)


def _wizard(**fields: Any):
    """A wizard 5 with Shield armed and two slots, against a goblin that acts first."""
    sheet: dict[str, Any] = {
        "class_slug": "wizard",
        "character_level": 5,
        "intelligence": 16,
        "hp_current": 60,
        "hp_max": 60,
        "spells_known": ["shield"],
        "spell_slots": {1: 2},
        "initiative": 1,
        "armed_reactions": ("shield",),
    }
    return start([pc(PC, **(sheet | fields))], encounter=[goblin()], seed=3)


def _goblin_swings(live) -> list[AttackRolled]:
    return [a for a in events(live, AttackRolled) if a.attacker_id == "mon:foe"]


# ── the default and the seam ─────────────────────────────────────────────────


def test_the_default_fires_every_eligible_reaction():
    assert reaction_resolver().fires(None, None, None, "hit_by_attack", None) is True


def test_a_declined_shield_stays_armed_and_the_hit_stands():
    resolver = _Recording(answer=False)
    set_reaction_resolver(resolver)
    _, live = _wizard(ac=1)
    monster_turn(_)
    [swing] = _goblin_swings(live)
    assert swing.is_hit
    assert swing.target_ac == 1
    assert not events(live, ReactionTriggered)
    assert live.spell_slots_by_entity[PC] == {1: 2}
    assert any(pr.spell_id == "shield" for pr in live.pending_reactions)
    [(reactor, trigger, context)] = resolver.asked
    assert (reactor, trigger) == (PC, "hit_by_attack")
    assert isinstance(context, D20Outcome)
    assert context.succeeded


def test_a_resolver_that_raises_stops_the_resolution_before_anything_is_spent():
    """A host asking a player raises; the engine catches nothing, and the
    Reaction, the slot and the armed entry are all still there."""
    set_reaction_resolver(_Asking())
    handle, live = _wizard(ac=1)
    with pytest.raises(_AskError, match="hit_by_attack"):
        monster_turn(handle)
    assert live.spell_slots_by_entity[PC] == {1: 2}
    assert any(pr.spell_id == "shield" for pr in live.pending_reactions)
    assert not events(live, ReactionTriggered)


def test_a_reduction_is_asked_with_the_damage_it_would_reduce():
    resolver = _Recording(answer=False)
    set_reaction_resolver(resolver)
    handle, live = start(
        [pc(PC, class_slug="rogue", character_level=5, ac=1, armed_reactions=("uncanny-dodge",))],
        encounter=[goblin()],
        seed=3,
    )
    act(handle, PC, intent_type="pass")
    monster_turn(handle)
    asked = [(trigger, context) for _, trigger, context in resolver.asked]
    assert asked
    assert all(isinstance(c, IncomingDamage) for _, c in asked)
    assert not events(live, ReactionTriggered)


def test_a_declined_opportunity_attack_is_not_made():
    resolver = _Recording(answer=False)
    set_reaction_resolver(resolver)
    _, live = start(
        [pc(PC, zone_id=cell_id(0, 0))],
        encounter=[goblin(zone_id=cell_id(1, 0))],
        seed=3,
    )
    _fire_opportunity_attacks_on_step(
        live, mover_id=PC, from_cell=cell_id(0, 0), to_cell=cell_id(0, 2)
    )
    assert resolver.asked == [("mon:foe", "opportunity_attack", PC)]
    assert not events(live, AttackRolled)
    assert next(c for c in live.initiative if c.entity_id == "mon:foe").reaction_available


# ── Shield answers the hit, after the roll ───────────────────────────────────


def test_shield_is_not_spent_on_an_attack_that_misses():
    handle, live = _wizard(ac=30)
    monster_turn(handle)
    [swing] = _goblin_swings(live)
    assert not swing.is_hit
    assert not events(live, ReactionTriggered)
    assert live.spell_slots_by_entity[PC] == {1: 2}


def test_shield_answers_a_hit_and_the_roll_is_judged_against_the_raised_ac():
    """SRD 5.2 Shield: "+5 bonus to AC, including against the triggering
    attack" — the goblin's hit on AC 1 is judged again against AC 6, and the
    roll's event reports the AC it stood against."""
    handle, live = _wizard(ac=1)
    monster_turn(handle)
    [swing] = _goblin_swings(live)
    [shield] = events(live, ReactionTriggered)
    assert shield.reaction_name == "shield"
    assert live.event_log.index(shield) < live.event_log.index(swing)
    assert swing.target_ac == 6
    assert swing.is_hit == (swing.natural == 20 or swing.roll_total >= 6)
    assert live.spell_slots_by_entity[PC] == {1: 1}


@pytest.mark.parametrize(
    ("natural", "total", "hits"),
    [(15, 17, True), (12, 14, False), (20, 21, True)],
)
def test_raised_target_judges_the_roll_again(natural, total, hits):
    outcome = D20Outcome(
        kind="attack",
        roller_id="mon:foe",
        opposer_id=PC,
        natural=natural,
        total=total,
        target=11,
        succeeded=True,
        rule=lambda n, t: n == 20 or (n != 1 and t >= 11),
    )
    raised = raised_target(outcome, 5)
    assert (raised.target, raised.succeeded) == (16, hits)
