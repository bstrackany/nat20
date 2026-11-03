"""Reactions that reduce the damage they answer (``dnd5e_engine.interrupts``).

An armed reduction fires as the damage lands — after the attack hit, before
Resistance — and the damage that lands is the reduced amount: SRD 5.2 Uncanny
Dodge halves it, Deflect Attacks and Stone's Endurance take off their roll, a
homebrew "to 0" takes all of it. Each scenario runs twice on one seed, armed and
not, and compares the goblin's first hit.
"""

from __future__ import annotations

from typing import Any

import pytest
from dnd5e_srd_data.schema.common import (
    ActivationBlock,
    DamagePartBlock,
    DamageReduction,
    DamageReductionMode,
    HealActivity,
    ReactionCondition,
    ReactionTriggerKind,
    UtilityActivity,
)

from dnd5e_engine.events import AttackRolled, DamageApplied, HealingApplied, ReactionTriggered
from dnd5e_engine.interrupts import (
    IncomingDamage,
    Interrupts,
    reduce_damage,
    reduction_answers,
)
from dnd5e_engine.lib_loader import set_lib_loader_for_tests
from dnd5e_engine.types.conditions import ActiveCondition
from tests.c20_support import act, combatant, events, monster_turn, pc, start
from tests.fork_support import BUNDLED, WithAssets, goblin, homebrew_feat

PC = "char:pc"


def _to_zero_feat() -> Any:
    """A homebrew "reduce the attack's total damage against you to 0", once."""
    shade = UtilityActivity(
        _id="shadeform0000000",
        name="Shade Form",
        activation=ActivationBlock(
            type="reaction",
            reaction_conditions=[ReactionCondition(kind=ReactionTriggerKind.HIT_BY_ATTACK)],
        ),
        uses={"max": "1", "spent": 0, "recovery": []},  # type: ignore[arg-type]
        damage_reduction=DamageReduction(mode=DamageReductionMode.TO_ZERO),
    )
    return homebrew_feat("shade-form", activities=[shade])


def _backlash() -> Any:
    """A homebrew reaction spell: "reducing the damage taken by 4d6 plus your
    spellcasting ability modifier" when you take damage."""
    ward = HealActivity(
        _id="backlashreduce00",
        activation=ActivationBlock(
            type="reaction",
            reaction_conditions=[ReactionCondition(kind=ReactionTriggerKind.TAKES_DAMAGE)],
        ),
        healing=DamagePartBlock(number=4, denomination=6, bonus="@mod", types=["healing"]),
        damage_reduction=DamageReduction(mode=DamageReductionMode.ROLL),
    )
    shield = BUNDLED.get_spell("shield")
    return shield.model_copy(update={"slug": "backlash", "activities": [ward]})


def _stones_endurance() -> Any:
    """SRD 5.2 Stone's Endurance as a feat: Giant Ancestry is a choice the
    character makes, which a ``PartyMemberSpec`` does not carry."""
    activities = BUNDLED.get_feature("stones-endurance").activities
    return homebrew_feat("stones-endurance-boon", activities=activities)


@pytest.fixture(autouse=True)
def _loader():
    set_lib_loader_for_tests(WithAssets(_to_zero_feat(), _backlash(), _stones_endurance()))
    yield
    set_lib_loader_for_tests(None)


def _fight(armed: str | None, *, rounds: int = 1, blinded: bool = False, **fields: Any):
    """A PC with AC 1 (every goblin swing hits) facing a goblin warrior."""
    base: dict[str, Any] = {"hp_current": 60, "hp_max": 60, "ac": 1, "dexterity": 16}
    handle, live = start([pc(PC, **(base | fields))], encounter=[goblin()], seed=3)
    if blinded:
        combatant(live, PC).conditions.append(
            ActiveCondition(condition="blinded", source_entity_id="implied:test", scope="combat")
        )
        live.active_conditions.setdefault(PC, set()).add("blinded")
    if armed:
        slug_field = "spell_id" if armed == "backlash" else "feature_id"
        act(handle, PC, intent_type="arm", **{slug_field: armed})
    for _ in range(rounds):
        act(handle, PC, intent_type="pass")
        monster_turn(handle)
    return live


def _hits_on_pc(live) -> list[int]:
    return [e.amount for e in events(live, DamageApplied) if e.target_id == PC]


ROGUE = {"class_slug": "rogue", "character_level": 5}
MONK = {"class_slug": "monk", "character_level": 3}
GOLIATH = {"class_slug": "fighter", "character_level": 1, "feats": ("stones-endurance-boon",)}
WIZARD = {"class_slug": "wizard", "character_level": 7, "intelligence": 16}


@pytest.mark.parametrize(
    ("armed", "sheet", "low", "high"),
    [
        # "halve the attack's damage against you (round down)" — no dice drawn
        ("uncanny-dodge", ROGUE, None, None),
        # "1d10 plus your Dexterity modifier and Monk level": 1+3+3 … 10+3+3
        ("deflect-attacks", MONK, 7, 16),
        # "roll 1d12. Add your Constitution modifier" (CON 10): 1 … 12
        ("stones-endurance-boon", GOLIATH, 1, 12),
        ("shade-form", ROGUE | {"feats": ("shade-form",)}, None, None),
        # 4d6 + INT mod (+3) at its 1st-level slot
        ("backlash", WIZARD | {"spells_known": ["backlash"], "spell_slots": {1: 1}}, 7, 27),
    ],
)
def test_an_armed_reduction_reduces_the_hit_it_answers(armed, sheet, low, high):
    plain = _hits_on_pc(_fight(None, **sheet))
    live = _fight(armed, **sheet)
    reduced = _hits_on_pc(live)
    assert plain, "the seed must give the goblin a hit"
    assert plain[0] > 0
    if armed == "uncanny-dodge":
        assert reduced[0] == plain[0] // 2
    elif armed == "shade-form":
        assert reduced[0] == 0
    else:
        assert 0 <= reduced[0] <= max(0, plain[0] - low)
        assert reduced[0] >= max(0, plain[0] - high)
    [fired] = events(live, ReactionTriggered)
    assert fired.reaction_name == armed
    # the reduction is not a heal after the fact (Foundry's convention)
    assert not events(live, HealingApplied)
    # it fires before the damage lands
    first_damage = next(e for e in events(live, DamageApplied) if e.target_id == PC)
    assert live.event_log.index(fired) < live.event_log.index(first_damage)
    if armed == "backlash":
        assert live.spell_slots_by_entity[PC] == {1: 0}


def test_a_reduction_spends_its_use_and_then_lets_the_damage_through():
    """Shade Form has one use: the first round's hit lands as 0, a later hit in
    full."""
    live = _fight("shade-form", rounds=3, feats=("shade-form",), **ROGUE)
    hits = [a for a in events(live, AttackRolled) if a.attacker_id == "mon:foe" and a.is_hit]
    amounts = _hits_on_pc(live)
    assert len(hits) >= 2, "the seed must give the goblin two hits"
    assert amounts[0] == 0
    assert any(amount > 0 for amount in amounts[1:])
    assert len(events(live, ReactionTriggered)) == 1


def test_uncanny_dodge_needs_the_attacker_in_sight():
    """ "When an attacker that you can see hits you" — a Blinded rogue takes it all."""
    plain = _hits_on_pc(_fight(None, blinded=True, **ROGUE))
    live = _fight("uncanny-dodge", blinded=True, **ROGUE)
    assert _hits_on_pc(live) == plain
    assert not events(live, ReactionTriggered)


@pytest.mark.parametrize(
    ("by_type", "mode", "rolled", "expected"),
    [
        ({"slashing": 9}, DamageReductionMode.HALVE, 0, {"slashing": 4}),
        # the whole attack halves, then the cut comes off the weapon's type first
        ({"slashing": 5, "fire": 4}, DamageReductionMode.HALVE, 0, {"slashing": 0, "fire": 4}),
        ({"slashing": 5, "fire": 4}, DamageReductionMode.ROLL, 7, {"slashing": 0, "fire": 2}),
        ({"piercing": 3}, DamageReductionMode.ROLL, 12, {"piercing": 0}),
        ({"slashing": 5, "fire": 4}, DamageReductionMode.TO_ZERO, 0, {"slashing": 0, "fire": 0}),
    ],
)
def test_reduce_damage(by_type, mode, rolled, expected):
    assert reduce_damage(by_type, mode, rolled) == expected


@pytest.mark.parametrize(
    ("reduction", "by_type", "sees", "answers"),
    [
        (DamageReduction(mode="halve", attacker_seen=True), {"slashing": 4}, True, True),
        (DamageReduction(mode="halve", attacker_seen=True), {"slashing": 4}, False, False),
        (DamageReduction(mode="halve"), {"slashing": 0}, True, False),
        # Deflect Attacks: "its damage includes Bludgeoning, Piercing, or Slashing"
        (DamageReduction(mode="roll", damage_types=["slashing"]), {"fire": 6}, True, False),
        (
            DamageReduction(mode="roll", damage_types=["slashing"]),
            {"slashing": 1, "fire": 6},
            True,
            True,
        ),
    ],
)
def test_reduction_answers(reduction, by_type, sees, answers):
    assert reduction_answers(reduction, by_type, sees_attacker=sees) is answers


def test_the_default_interrupts_change_nothing():
    incoming = IncomingDamage("a", "b", {"fire": 5}, attack_hit=True)
    assert Interrupts().before_damage_applied(incoming) == {"fire": 5}
