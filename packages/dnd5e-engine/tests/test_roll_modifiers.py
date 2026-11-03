"""Changing a D20 Test after the roll, before its outcome
(``dnd5e_engine.roll_modifiers``, ``Interrupts.before_d20_outcome``).

SRD 5.2 Stroke of Luck turns a failed D20 Test into a 20, Indomitable rerolls a
failed save with a bonus, Cutting Words subtracts a Bardic Inspiration die from
an enemy's success, Halfling Luck rerolls a natural 1. An armed modifier fires
by itself, only when it can turn the outcome; the tests below run the seams
end to end, the orchestrator's choice on hand-made rolls, and the pure policy.
"""

from __future__ import annotations

import random
from dataclasses import replace
from typing import Any

import pytest
from dnd5e_srd_data.schema.common import (
    ActivationBlock,
    CheckActivity,
    ReactionCondition,
    ReactionTriggerKind,
    RollModifier,
    UtilityActivity,
)

from dnd5e_engine.activities.check import resolve_check
from dnd5e_engine.activities.context import ActivityResolutionContext
from dnd5e_engine.activities.save_primitive import roll_save
from dnd5e_engine.events import AttackRolled, D20TestChanged, ReactionTriggered, SaveRolled
from dnd5e_engine.interrupts import D20Outcome, Interrupts
from dnd5e_engine.lib_loader import set_lib_loader_for_tests
from dnd5e_engine.orchestrator import _ArmedInterrupts, _feature_use_spent, _update_combatant
from dnd5e_engine.roll_modifiers import (
    formula_reach,
    modified,
    modifier_activity,
    reroll_natural,
    worth_firing,
)
from dnd5e_engine.types.combat import Combatant
from dnd5e_engine.types.conditions import ActiveCondition
from tests.c20_support import act, combatant, events, foe, hold_person, monster_turn, pc, start
from tests.c20_support import wizard as hold_person_caster
from tests.fork_support import BUNDLED, WithAssets, goblin, homebrew_feat

PC = "char:hero"
FOE = "mon:foe"


def _fading_target() -> Any:
    """A homebrew "When a creature misses you with an attack roll" reaction."""
    fade = UtilityActivity(
        _id="fadingtarget0000",
        activation=ActivationBlock(
            type="reaction",
            reaction_conditions=[ReactionCondition(kind=ReactionTriggerKind.MISSED_BY_ATTACK)],
        ),
    )
    return homebrew_feat("fading-target", activities=[fade])


@pytest.fixture(autouse=True)
def _loader():
    set_lib_loader_for_tests(WithAssets(_fading_target()))
    yield
    set_lib_loader_for_tests(None)


def _roll(roller: str = PC, *, kind: str = "save", natural: int, mod: int, target: int, **kw: Any):
    total = natural + mod
    return D20Outcome(
        kind=kind,  # type: ignore[arg-type]
        roller_id=roller,
        opposer_id=kw.pop("opposer", FOE),
        natural=natural,
        total=total,
        target=target,
        succeeded=total >= target,
        **kw,
    )


ROGUE_20 = {"class_slug": "rogue", "character_level": 20, "armed_reactions": ("stroke-of-luck",)}
FIGHTER_9 = {"class_slug": "fighter", "character_level": 9, "armed_reactions": ("indomitable",)}
LORE_BARD = {
    "class_slug": "bard",
    "subclass_slug": "lore",
    "character_level": 3,
    "armed_reactions": ("cutting-words",),
}


def _table(sheet: dict[str, Any], *, blinded: bool = False, **foe_fields: Any):
    handle, live = start([pc(PC, **sheet)], encounter=[foe(**foe_fields)], seed=5)
    if blinded:
        combatant(live, PC).conditions.append(
            ActiveCondition(condition="blinded", source_entity_id="implied:test", scope="combat")
        )
        live.active_conditions.setdefault(PC, set()).add("blinded")
    return handle, live


# ── the orchestrator's choice, on hand-made rolls ────────────────────────────


@pytest.mark.parametrize(
    ("sheet", "roll", "change", "blinded"),
    [
        # Stroke of Luck: "If you fail a D20 Test, you can turn the roll into a 20"
        (ROGUE_20, _roll(natural=5, mod=2, target=15), "set", False),
        # … not when even a 20 fails, not on a success, not on an ally's roll
        (ROGUE_20, _roll(natural=5, mod=-6, target=15), None, False),
        (ROGUE_20, _roll(natural=15, mod=2, target=15), None, False),
        (ROGUE_20, _roll(FOE, natural=5, mod=2, target=15), None, False),
        # Indomitable: "reroll it with a bonus equal to your Fighter level"
        (FIGHTER_9, _roll(natural=3, mod=0, target=14), "reroll", False),
        (FIGHTER_9, _roll(kind="check", natural=3, mod=0, target=14), None, False),
        # Cutting Words: an enemy's attack that hits, which a d6 can turn
        (LORE_BARD, _roll(FOE, kind="attack", natural=12, mod=4, target=15), "roll", False),
        # … not one that hits by more than the die, not a save, not unseen
        (LORE_BARD, _roll(FOE, kind="attack", natural=18, mod=4, target=15), None, False),
        (LORE_BARD, _roll(FOE, kind="save", natural=12, mod=4, target=15), None, False),
        (LORE_BARD, _roll(FOE, kind="attack", natural=12, mod=4, target=15), None, True),
        # Halfling Luck: a natural 1 is always rerolled, nothing armed
        (
            {"species_slug": "halfling"},
            _roll(kind="attack", natural=1, mod=5, target=8),
            "reroll",
            False,
        ),
        ({}, _roll(kind="attack", natural=1, mod=5, target=8), None, False),
    ],
)
def test_an_armed_modifier_fires_only_when_it_can_turn_the_outcome(sheet, roll, change, blinded):
    _, live = _table(sheet, blinded=blinded)
    settled = _ArmedInterrupts(live).before_d20_outcome(roll)
    changes = events(live, D20TestChanged)
    if change is None:
        assert settled is roll
        assert not changes
        return
    [changed] = changes
    assert (changed.change, changed.roller_id, changed.test) == (change, roll.roller_id, roll.kind)
    assert (changed.natural_before, changed.total_before) == (roll.natural, roll.total)
    assert (settled.natural, settled.total, settled.succeeded) == (
        changed.natural,
        changed.total,
        changed.succeeded,
    )
    mod = roll.total - roll.natural
    if change == "set":
        assert (settled.natural, settled.total, settled.succeeded) == (20, 20 + mod, True)
    elif sheet is FIGHTER_9:
        assert settled.total == settled.natural + mod + 9
    elif sheet is LORE_BARD:
        assert 1 <= roll.total - settled.total <= 6
        assert [e.reaction_name for e in events(live, ReactionTriggered)] == ["cutting-words"]
        assert not combatant(live, PC).reaction_available
    else:
        assert changed.source_id == "luck"


def test_cutting_words_spends_a_bardic_inspiration_use():
    """ "expend one use of your Bardic Inspiration": with CHA 10 the bard has
    one, so the second hit stands even with the Reaction back."""
    _, live = _table(LORE_BARD)
    hit = _roll(FOE, kind="attack", natural=12, mod=4, target=15)
    _ArmedInterrupts(live).before_d20_outcome(hit)
    assert _feature_use_spent(live, PC, "bardic-inspiration") == 1
    assert _feature_use_spent(live, PC, "cutting-words") == 0
    live.pending_reactions.extend(live.standing_reactions)
    _update_combatant(live, PC, reaction_available=True)
    assert _ArmedInterrupts(live).before_d20_outcome(hit) is hit


# ── the seams, end to end ────────────────────────────────────────────────────


def test_stroke_of_luck_turns_a_miss_into_a_critical_hit_once():
    """Against AC 30 only a 20 hits; the armed Stroke of Luck makes the first
    miss a natural 20 (a Critical Hit) and is then spent until a rest."""
    handle, live = _table(ROGUE_20, ac=30)
    for _ in range(4):
        act(handle, PC, intent_type="attack", weapon_id="dagger", target_id=FOE)
        if live.initiative[live.current_turn_index].entity_id == PC:
            act(handle, PC, intent_type="pass")
        monster_turn(handle)
    [changed] = events(live, D20TestChanged)
    after = live.event_log[live.event_log.index(changed) + 1 :]
    swings = [a for a in after if isinstance(a, AttackRolled) and a.attacker_id == PC]
    assert (changed.natural_before, changed.natural) != (20, 20)
    assert (swings[0].natural, swings[0].is_hit, swings[0].is_crit) == (20, True, True)
    assert swings[1:], "the seed must give the rogue a later swing"
    assert all(a.is_hit is (a.natural == 20) for a in swings[1:])


def test_indomitable_rerolls_a_failed_save_with_its_bonus():
    """Hold Person at DC 16 against a Wisdom 10 Fighter 9: the failed save is
    rerolled with +9, and the reroll is what ``SaveRolled`` reports."""
    sheet = {**FIGHTER_9, "initiative": 20}
    handle, live = start([pc(PC, **sheet), hold_person_caster()], seed=2)
    act(handle, PC, intent_type="pass")
    hold_person(handle, PC)
    [changed] = events(live, D20TestChanged)
    [save] = [s for s in events(live, SaveRolled) if s.target_id == PC]
    assert changed.total_before < 16
    assert (save.natural, save.roll_total) == (changed.natural, changed.total)
    assert changed.total - changed.natural == changed.total_before - changed.natural_before + 9
    assert _feature_use_spent(live, PC, "indomitable") == 1


class _Succeed(Interrupts):
    def __init__(self) -> None:
        self.seen: list[D20Outcome] = []

    def before_d20_outcome(self, outcome: D20Outcome) -> D20Outcome:
        self.seen.append(outcome)
        return replace(outcome, natural=20, total=99, succeeded=True)


def _ctx(interrupts: Interrupts, emitted: list[Any]) -> ActivityResolutionContext:
    hero = Combatant(
        entity_id=PC, entity_type="Character", name="Hero", initiative=1, hp_current=9, hp_max=9
    )
    return ActivityResolutionContext(
        rng=random.Random(1),
        caster=hero,
        targets=[],
        event_emitter=emitted.append,
        caster_abilities={},
        interrupts=interrupts,
    )


def test_saves_and_checks_ask_before_their_outcome():
    """Both resolvers hand the roll over and keep what comes back; the check's
    roller is the caster, with no opposer, the save's the target."""
    interrupts, emitted = _Succeed(), []
    ctx = _ctx(interrupts, emitted)
    check = CheckActivity(check={"ability": "str", "dc": {"calculation": "flat", "formula": "30"}})
    resolve_check(check, ctx)
    target = ctx.caster.model_copy(update={"entity_id": FOE})
    save = roll_save(ctx, target, "wis", 30)
    [checked] = [e for e in emitted if e.type == "check_rolled"]
    assert (checked.natural, checked.roll_total, checked.succeeded) == (20, 99, True)
    assert (save.natural, save.total, save.succeeded) == (20, 99, True)
    assert [(o.kind, o.roller_id, o.opposer_id, o.target) for o in interrupts.seen] == [
        ("check", PC, "", 30),
        ("save", FOE, PC, 30),
    ]
    assert Interrupts().before_d20_outcome(interrupts.seen[0]) is interrupts.seen[0]


def test_a_miss_fires_a_missed_by_attack_reaction():
    """ "When a creature misses you with an attack roll" — against AC 30 the
    goblin misses and the armed reaction answers it; a hit would not."""
    handle, live = start(
        [pc(PC, ac=30, feats=("fading-target",), armed_reactions=("fading-target",))],
        encounter=[goblin()],
        seed=3,
    )
    act(handle, PC, intent_type="pass")
    monster_turn(handle)
    [miss] = [a for a in events(live, AttackRolled) if a.attacker_id == FOE]
    assert not miss.is_hit
    assert [e.reaction_name for e in events(live, ReactionTriggered)] == ["fading-target"]


# ── arming order (``_pop_pending_reaction``) ─────────────────────────────────


@pytest.mark.parametrize(
    ("armed", "slots", "fires"),
    [
        # Shield first with no slot left must not block Uncanny Dodge behind it
        (("shield", "uncanny-dodge"), {1: 0}, "uncanny-dodge"),
        # a reduction first must not keep Shield behind it from firing
        (("uncanny-dodge", "shield"), {1: 1}, "shield"),
    ],
)
def test_each_armed_entry_is_tried_in_arming_order(armed, slots, fires):
    sheet = {"class_slug": "rogue", "character_level": 5, "spells_known": ["shield"]}
    handle, live = start(
        [pc(PC, ac=1, spell_slots=slots, armed_reactions=armed, **sheet)],
        encounter=[goblin()],
        seed=3,
    )
    act(handle, PC, intent_type="pass")
    monster_turn(handle)
    assert [e.reaction_name for e in events(live, ReactionTriggered)][:1] == [fires]


# ── the pure policy ──────────────────────────────────────────────────────────

SET_20 = RollModifier(mode="set", applies_to=["save"], value=20)
SET_1 = RollModifier(mode="set", applies_to=["attack"], whose=["enemy"], value=1)
ROLL = RollModifier(mode="roll", applies_to=["attack"], whose=["self", "enemy"])
REROLL = RollModifier(mode="reroll", applies_to=["save"])
SUCCEED = RollModifier(mode="succeed", applies_to=["save"])


@pytest.mark.parametrize(
    ("modifier", "roll", "helps", "reach", "worth"),
    [
        (SET_20, _roll(natural=4, mod=1, target=12), True, 0, True),
        (SET_20, _roll(natural=4, mod=-9, target=12), True, 0, False),
        (SET_1, _roll(FOE, kind="attack", natural=15, mod=3, target=12), False, 0, True),
        (ROLL, _roll(natural=8, mod=1, target=12), True, 4, True),
        (ROLL, _roll(natural=8, mod=1, target=14), True, 4, False),
        (ROLL, _roll(FOE, kind="attack", natural=12, mod=3, target=12), False, 4, True),
        # a natural 1 misses and a natural 20 hits whatever is added
        (
            ROLL,
            _roll(kind="attack", natural=1, mod=9, target=12, rule=lambda n, t: n != 1 and t >= 12),
            True,
            9,
            False,
        ),
        (
            ROLL,
            _roll(
                FOE,
                kind="attack",
                natural=20,
                mod=0,
                target=25,
                rule=lambda n, t: n == 20 or t >= 25,
            ),
            False,
            9,
            False,
        ),
        (REROLL, _roll(natural=3, mod=0, target=21), True, 0, False),
        (REROLL, _roll(natural=3, mod=0, target=21), True, 1, True),
        # under Disadvantage the higher die still caps the reroll
        (
            REROLL,
            _roll(natural=3, mod=0, target=15, mode="disadvantage", dropped=12),
            True,
            0,
            False,
        ),
        (SUCCEED, _roll(natural=3, mod=0, target=15), True, 0, True),
        (SUCCEED, _roll(FOE, natural=13, mod=4, target=15), False, 0, False),
        # helping a success or hindering a failure is never worth it
        (SET_20, _roll(natural=14, mod=1, target=12), True, 0, False),
        (SET_1, _roll(FOE, kind="attack", natural=5, mod=3, target=12), False, 0, False),
    ],
)
def test_worth_firing(modifier, roll, helps, reach, worth):
    assert worth_firing(modifier, roll, helps=helps, reach=reach) is worth


@pytest.mark.parametrize(
    ("mode", "natural", "dropped", "new", "helps", "kept"),
    [
        ("normal", 1, None, 7, True, 7),
        # Advantage: the lower die is rerolled to help, the higher still counts
        ("advantage", 9, 4, 6, True, 9),
        ("advantage", 9, 4, 15, True, 15),
        # Disadvantage: the lower (kept) die is rerolled, the higher caps it
        ("disadvantage", 1, 12, 18, True, 12),
        ("disadvantage", 1, 12, 5, True, 5),
        # hindering rerolls the higher die
        ("advantage", 15, 6, 2, False, 6),
        ("disadvantage", 6, 15, 2, False, 2),
    ],
)
def test_reroll_natural(mode, natural, dropped, new, helps, kept):
    roll = _roll(natural=natural, mod=0, target=10, mode=mode, dropped=dropped)
    assert reroll_natural(roll, new, helps=helps) == kept


def test_modified_and_reach():
    roll = _roll(FOE, kind="attack", natural=12, mod=3, target=12)
    assert modified(ROLL, roll, helps=False, amount=4).total == 11
    assert modified(SET_1, roll, helps=False, amount=0).natural == 1
    assert modified(REROLL, roll, helps=True, amount=9, new_die=2).total == 14
    assert modified(SUCCEED, roll, helps=True, amount=0).succeeded is True
    formulas = ("1d8", "9", "2d4 + 3", "1d10 - 1", "-1d4", "(1d6)", "")
    assert [formula_reach(f) for f in formulas] == [8, 9, 11, 9, -4, 6, 0]
    with pytest.raises(ValueError):
        formula_reach("4d6kh3")
    cutting_words = BUNDLED.get_feature("cutting-words")
    assert modifier_activity(cutting_words, None) is cutting_words.activities[0]
    assert modifier_activity(cutting_words, "elsewhere") is None
    assert modifier_activity(BUNDLED.get_feature("luck"), None) is None
