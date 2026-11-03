"""More moments a held activity fires on, read from what a resolution did.

``triggers.py`` holds the record of a use; the orchestrator fires its
``hits`` / ``hit_by_attack`` / ``takes_damage`` activities from a resolution's
attacks and damage. This module names four more moments in the same event
stream (``ActivityTrigger.kind``):

* ``natural_1`` — the subject's D20 Test shows a 1 on the d20 (an attack roll,
  a saving throw — the concentration save included — or an ability check);
  SRD 5.2 Luck: "When you roll a 1 on the d20 of a D20 Test".
* ``casts_spell`` — the subject casts a spell; ``spell`` narrows it to one
  spell, ``with_slot`` to a cast with a spell slot (not a cantrip). SRD 5.2
  Blessed Healer: "Immediately after you cast a spell with a spell slot".
* ``reduces_to_zero`` — the subject's act drops a creature hostile to it to 0
  Hit Points. SRD 5.2 Dark One's Blessing: "When you reduce an enemy to 0 Hit
  Points".
* ``enemy_falls`` — a creature hostile to the subject drops to 0 Hit Points,
  whoever's act it was; ``within_ft`` narrows it to one within that many feet
  of the subject. Dark One's Blessing: "an enemy within 10 feet of you is
  reduced to 0 Hit Points".

"Drops to 0 Hit Points" is ``Unconscious`` (a character) or a ``Death`` that is
not the third failed death save (a monster at 0 HP, a character killed
outright). The orchestrator owns the firing (``_fire_moment_triggers``) and the
questions that need the combat (sides, positions). Every fired activity resolves
against its subject: the creature who rolled, cast, or felled the enemy, or saw
it fall.

Pure: no I/O, no RNG.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, NamedTuple

from dnd5e_srd_data.schema.common import (
    ActivityTrigger,
    ActivityTriggerKind,
    ActivityTriggerSubject,
)

from dnd5e_engine.events import (
    AttackRolled,
    CheckRolled,
    CombatEvent,
    ConcentrationCheck,
    Death,
    SaveRolled,
    SpellCast,
    Unconscious,
)
from dnd5e_engine.triggers import HeldUse, bearers_of

__all__ = ["Moment", "cast_matches", "moments_of", "subject_candidates"]


class Moment(NamedTuple):
    """One moment an event is. ``subject_id`` is the creature whose moment it
    is, or ``None`` when any creature may be (a fall is every watcher's);
    ``fallen_id`` the creature that dropped to 0 HP, for the two fall kinds."""

    kind: ActivityTriggerKind
    subject_id: str | None
    fallen_id: str | None = None


def _d20_roller(event: CombatEvent) -> str | None:
    if isinstance(event, AttackRolled):
        return event.attacker_id
    if isinstance(event, SaveRolled | ConcentrationCheck):
        return event.target_id
    if isinstance(event, CheckRolled):
        return event.actor_id
    return None


def moments_of(event: CombatEvent, dealer_id: str | None) -> list[Moment]:
    """The moments ``event`` is. ``dealer_id`` is the creature whose act the
    stream being read is (a resolution's actor); without one, no creature
    reduced anyone (damage from an area at a turn's start)."""
    k = ActivityTriggerKind
    roller = _d20_roller(event)
    if roller is not None:
        natural = getattr(event, "natural", None)
        return [Moment(k.NATURAL_1, roller)] if natural == 1 else []
    if isinstance(event, SpellCast):
        return [Moment(k.CASTS_SPELL, event.actor_id)]
    if isinstance(event, Unconscious) or (
        isinstance(event, Death) and event.reason != "death_saves"
    ):
        fallen = event.target_id
        out = [Moment(k.ENEMY_FALLS, None, fallen)]
        if dealer_id is not None and dealer_id != fallen:
            out.append(Moment(k.REDUCES_TO_ZERO, dealer_id, fallen))
        return out
    return []


def cast_matches(trigger: ActivityTrigger, event: CombatEvent) -> bool:
    """Does the ``casts_spell`` narrowing hold for ``event``? Always, for any
    other event."""
    if not isinstance(event, SpellCast):
        return True
    if trigger.spell and event.spell_id != trigger.spell:
        return False
    return not trigger.with_slot or event.slot_level is not None


def subject_candidates(
    use: HeldUse, trigger: ActivityTrigger, active_effects: Mapping[str, Sequence[Any]]
) -> list[str]:
    """Every creature that is a subject of ``trigger`` for this use: the owner
    (``subject: caster``) or each bearer of an effect of the use."""
    if trigger.subject is ActivityTriggerSubject.CASTER:
        return [use.owner_id]
    return bearers_of(use, active_effects)
