"""Reactions that change a resolution midway.

SRD 5.2 writes some reactions into the middle of another creature's act: Uncanny
Dodge "When an attacker that you can see hits you with an attack roll, you can
take a Reaction to halve the attack's damage against you (round down)"; Deflect
Attacks "reduce the attack's total damage against you". The engine never pauses
to ask (``docs/concepts/reactions.md``): an armed reaction answers by itself.

``Interrupts`` is the one object an activity resolution asks at such a moment.
It rides on the activity context (``ActivityResolutionContext.interrupts``), so
the pure resolvers ask it without importing the orchestrator, which builds the
live one from what each creature keeps armed. The default interrupts nothing.

It has two moments. ``before_damage_applied``: damage about to land on a
creature, after it is rolled and before Resistance, Vulnerability and Immunity
fold in (``activities/apply.py``). ``before_d20_outcome``: a D20 Test rolled and
not yet decided (``activities/attack.py``, ``save_primitive.py``, ``check.py``),
which a feature may change "after seeing the roll but before any of the roll's
effects occur" (SRD 5.2 Dark One's Own Luck); see ``roll_modifiers.py``.

The helpers below are the pure half of a damage reduction (which reduction an
activity carries, whether it answers this damage, the reduced amounts); firing
one (spending the Reaction and the use) is the orchestrator's.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from dnd5e_srd_data.schema.common import DamageReductionMode

if TYPE_CHECKING:
    from dnd5e_srd_data.schema.common import D20TestKind, DamageReduction

    from dnd5e_engine.activities.context import ActivityResolutionContext
    from dnd5e_engine.events import AdvantageMode


@dataclass(frozen=True)
class IncomingDamage:
    """Damage about to land: ``by_type`` is the rolled amount per damage type,
    ``attacker_id`` the creature whose resolution dealt it, ``attack_hit``
    whether it is the damage of an attack roll that hit."""

    target_id: str
    attacker_id: str
    by_type: Mapping[str, int]
    attack_hit: bool = False


@dataclass(frozen=True)
class D20Outcome:
    """A D20 Test rolled and not yet decided: ``roller_id`` rolled ``natural``
    for ``total`` against ``target`` (the AC or DC) and ``succeeded`` so far.
    ``opposer_id`` is the creature on the other side (the attack's target, the
    creature that forced the save or the check; ``""`` for none). ``mode`` and
    ``dropped`` (the die not kept) are the roll's Advantage or Disadvantage.
    ``rule`` decides success from a natural and a total — an attack's natural 1
    misses and its natural 20 hits — and is ``total >= target`` when ``None``."""

    kind: D20TestKind
    roller_id: str
    opposer_id: str
    natural: int
    total: int
    target: int
    succeeded: bool
    mode: AdvantageMode = "normal"
    dropped: int | None = None
    rule: Callable[[int, int], bool] | None = None

    def succeeds(self, natural: int, total: int) -> bool:
        """Would this roll succeed showing ``natural`` for ``total``?"""
        return self.rule(natural, total) if self.rule is not None else total >= self.target


class Interrupts:
    """What an activity resolution asks before an outcome stands. This default
    changes nothing; the orchestrator passes one that fires armed reactions."""

    def before_damage_applied(self, incoming: IncomingDamage) -> dict[str, int]:
        """The amounts per damage type that land instead of ``incoming.by_type``."""
        return dict(incoming.by_type)

    def before_d20_outcome(self, outcome: D20Outcome) -> D20Outcome:
        """The roll that stands instead of ``outcome``."""
        return outcome


def reduction_activity(source: Any, activity_id: str | None) -> Any | None:
    """The activity of a spell, feature or feat that reduces damage: the one
    ``activity_id`` names, else the first carrying ``damage_reduction``, else
    ``None``."""
    for activity in getattr(source, "activities", ()):
        if activity_id and activity.id != activity_id:
            continue
        if getattr(activity, "damage_reduction", None) is not None:
            return activity
    return None


def reduction_answers(
    reduction: DamageReduction, by_type: Mapping[str, int], *, sees_attacker: bool
) -> bool:
    """Does ``reduction`` answer this damage? Some must land, the reactor must
    see the attacker when ``attacker_seen`` (Uncanny Dodge: "an attacker that
    you can see"), and the damage must include one of ``damage_types`` when set
    (Deflect Attacks: "its damage includes Bludgeoning, Piercing, or Slashing
    damage")."""
    landing = {damage_type for damage_type, amount in by_type.items() if amount > 0}
    if not landing:
        return False
    if reduction.attacker_seen and not sees_attacker:
        return False
    return not reduction.damage_types or bool(landing & set(reduction.damage_types))


def reduction_roll(activity: Any, ctx: ActivityResolutionContext) -> int:
    """A ``roll`` reduction's amount: the activity's own ``healing`` roll
    (Deflect Attacks' "1d10 plus your Dexterity modifier and Monk level"),
    rolled with the reactor's numbers and the slot a spell was cast with."""
    from dnd5e_engine.activities.dice import roll_damage_part
    from dnd5e_engine.activities.formula import resolve_damage_block

    healing = resolve_damage_block(activity.healing, ctx, ability=ctx.spellcasting_ability)
    return roll_damage_part(
        healing, ctx.rng, slot_level=ctx.slot_level, base_level=ctx.base_spell_level
    )


def reduce_damage(
    by_type: Mapping[str, int], mode: DamageReductionMode, rolled: int = 0
) -> dict[str, int]:
    """``by_type`` reduced as a whole: halved, round down (``halve``), less
    ``rolled`` (``roll``), or to 0 (``to_zero``). The reduction comes off the
    damage types in the order they were rolled (the weapon's own first)."""
    total = sum(max(0, amount) for amount in by_type.values())
    if mode is DamageReductionMode.HALVE:
        cut = total - total // 2
    elif mode is DamageReductionMode.ROLL:
        cut = min(total, max(0, rolled))
    else:
        cut = total
    out: dict[str, int] = {}
    for damage_type, amount in by_type.items():
        taken = min(max(0, amount), cut)
        cut -= taken
        out[damage_type] = amount - taken
    return out
