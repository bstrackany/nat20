"""Changing a D20 Test after the roll, before its outcome.

SRD 5.2 writes features into the moment between a d20 roll and its outcome:
Halfling Luck "When you roll a 1 on the d20 of a D20 Test, you can reroll the
die, and you must use the new roll"; Indomitable "If you fail a saving throw,
you can reroll it with a bonus equal to your Fighter level"; Stroke of Luck "If
you fail a D20 Test, you can turn the roll into a 20"; Cutting Words "subtract
the number rolled from the creature's roll ... potentially turning the success
into a failure". The engine asks ``Interrupts.before_d20_outcome`` there
(``interrupts.py``); an activity's ``roll_modifier`` (``dnd5e-srd-data``) says
what it does.

The engine never pauses to ask (``docs/concepts/reactions.md``), so an armed
modifier fires by itself under one policy, chosen so that firing is never worse
than not firing:

* it only helps: it answers a failing roll of its owner or an ally, or a
  succeeding roll of an enemy, never the other way round;
* it fires only when it can turn that outcome: the best its die can do (the
  most a ``roll`` adds or subtracts, a ``reroll``'s best die plus its bonus)
  would turn the failure into a success or the success into a failure. A die
  that then rolls too low is spent, as at the table;
* Halfling Luck costs nothing and the text says "you must use the new roll",
  so a natural 1 is always rerolled.

This module is the pure half (what a modifier would do, whether it is worth
firing); finding the armed one, spending its use and drawing its dice is the
orchestrator's.
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, Any

import d20
from dnd5e_srd_data.schema.common import RollModifierMode

if TYPE_CHECKING:
    from dnd5e_srd_data.schema.common import RollModifier

    from dnd5e_engine.interrupts import D20Outcome


def modifier_activity(source: Any, activity_id: str | None) -> Any | None:
    """The activity of a feature or feat that changes a D20 Test: the one
    ``activity_id`` names, else the first carrying ``roll_modifier``, else
    ``None``."""
    for activity in getattr(source, "activities", ()):
        if activity_id and activity.id != activity_id:
            continue
        if getattr(activity, "roll_modifier", None) is not None:
            return activity
    return None


def pool_feature(activity: Any) -> tuple[str, int] | None:
    """The other feature whose uses the activity spends, and how many: Cutting
    Words "expend one use of your Bardic Inspiration" is Foundry's consumption
    target ``feat:bardic-inspiration``. ``None`` when it spends its own."""
    for target in activity.consumption.targets:
        name = str(target.target)
        if target.type == "itemUses" and name.startswith("feat:"):
            value = str(target.value).strip()
            return name.removeprefix("feat:"), int(value) if value.isdigit() else 1
    return None


def formula_reach(formula: str) -> int:
    """The most a resolved roll formula can come to (``"1d8"`` → 8, ``"9"`` →
    9), with no dice drawn."""
    if not formula:
        return 0
    expr = d20.parse(formula)
    return _reach(expr.roll)


def _reach(node: Any) -> int:
    if isinstance(node, d20.ast.Dice):
        return int(node.num) * int(node.size)
    if isinstance(node, d20.ast.Literal):
        return int(node.value)
    if isinstance(node, d20.ast.UnOp):
        inner = _reach(node.value)
        return -inner if node.op == "-" else inner
    if isinstance(node, d20.ast.BinOp):
        left, right = _reach(node.left), _reach(node.right)
        return left - right if node.op == "-" else left + right
    if isinstance(node, d20.ast.Parenthetical):
        return _reach(node.value)
    raise ValueError(f"unsupported roll formula node {type(node).__name__}")


def reroll_natural(outcome: D20Outcome, new_die: int, *, helps: bool) -> int:
    """The d20 that stands after one die is rerolled to ``new_die``. With
    Advantage or Disadvantage only one of the two dice is rerolled: the lower one
    to help the roll (the higher still counts under Advantage, caps it under
    Disadvantage), the higher one to hinder it."""
    if outcome.dropped is None:
        return new_die
    rerolls_kept = helps == (outcome.mode == "disadvantage")
    other = outcome.dropped if rerolls_kept else outcome.natural
    return max(other, new_die) if outcome.mode == "advantage" else min(other, new_die)


def rerolled(outcome: D20Outcome, new_die: int, *, helps: bool, bonus: int = 0) -> D20Outcome:
    """``outcome`` with one die rerolled to ``new_die`` and ``bonus`` added to
    a roll it helps or subtracted from one it hinders (Indomitable's "with a
    bonus equal to your Fighter level")."""
    natural = reroll_natural(outcome, new_die, helps=helps)
    total = outcome.total - outcome.natural + natural + (bonus if helps else -bonus)
    return replace(
        outcome, natural=natural, total=total, succeeded=outcome.succeeds(natural, total)
    )


def modified(
    modifier: RollModifier, outcome: D20Outcome, *, helps: bool, amount: int, new_die: int = 0
) -> D20Outcome:
    """``outcome`` as ``modifier`` changes it: ``roll`` adds ``amount`` to a roll
    it helps and subtracts it from one it hinders; ``reroll`` rerolls one die to
    ``new_die`` with ``amount`` as its bonus; ``set`` turns the d20 into
    ``modifier.value``; ``succeed`` makes a failure a success."""
    mode = modifier.mode
    if mode is RollModifierMode.REROLL:
        return rerolled(outcome, new_die, helps=helps, bonus=amount)
    if mode is RollModifierMode.SUCCEED:
        return replace(outcome, succeeded=True)
    natural, total = outcome.natural, outcome.total
    if mode is RollModifierMode.ROLL:
        total += amount if helps else -amount
    else:
        natural = int(modifier.value or natural)
        total += natural - outcome.natural
    return replace(
        outcome, natural=natural, total=total, succeeded=outcome.succeeds(natural, total)
    )


def worth_firing(modifier: RollModifier, outcome: D20Outcome, *, helps: bool, reach: int) -> bool:
    """The policy (module docstring): does ``modifier``, at its best (``reach``
    is the most its roll comes to), turn a failing roll it helps into a success
    or a succeeding roll it hinders into a failure?"""
    if outcome.succeeded is helps:
        return False
    if modifier.mode is RollModifierMode.SUCCEED:
        return helps
    best_die = 20 if helps else 1
    best = modified(modifier, outcome, helps=helps, amount=reach, new_die=best_die)
    return best.succeeded is helps
