"""Effects that end on an event or on a turn boundary (midi-qol's DAE "special
durations").

The corpus writes the ending on the effect definition — ``PassiveEffect.ends_on``
(SRD 5.2 Invisibility: "The spell ends early immediately after the target makes
an attack roll, deals damage, or casts a spell") and ``PassiveEffect.until``
(Shield: "Until the start of your next turn"). When the effect lands,
``ending_flags`` carries both onto the runtime ``ActiveEffect.flags``; this
module also reads the typed event stream for the events that end one. The
orchestrator owns the expiry itself: ``_expire_on_events`` after each
resolution and over each ending turn, and two turn hooks for ``until``.

Pure: no I/O, no RNG.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from typing import TYPE_CHECKING, Any

from dnd5e_srd_data.schema.common import EffectEndEvent

from dnd5e_engine.events import (
    AttackRolled,
    CheckRolled,
    CombatEvent,
    ConcentrationCheck,
    DamageApplied,
    Death,
    EffectApplied,
    SaveRolled,
    SpellCast,
    Unconscious,
)

if TYPE_CHECKING:
    from dnd5e_srd_data.schema.common import PassiveEffect

#: ``ActiveEffect.flags`` keys. The end-of-next-turn key is the one the engine's
#: timed expiry already reads (``_expire_timed_effects_at_turn_end``).
ENDS_ON_FLAG = "ends_on"
UNTIL_START_OF_NEXT_TURN_FLAG = "until_start_of_next_turn_of"
UNTIL_END_OF_NEXT_TURN_FLAG = "until_end_of_next_turn_of"
UNTIL_END_OF_TURN_FLAG = "until_end_of_turn"

EffectIdentity = tuple[str, str, str]


def ending_flags(pe: PassiveEffect, originator_id: str) -> dict[str, Any]:
    """The flags that carry ``pe``'s ending onto the effect it becomes. "Your
    next turn" is the next turn of ``originator_id``, the creature whose use
    applied the effect; "end_of_turn" ends at the end of whichever turn is under
    way when it lands (Stinking Cloud: "until the end of the current turn").
    Empty when ``pe`` sets neither field."""
    flags: dict[str, Any] = {}
    if pe.ends_on:
        flags[ENDS_ON_FLAG] = [str(event) for event in pe.ends_on]
    if pe.until == "start_of_next_turn":
        flags[UNTIL_START_OF_NEXT_TURN_FLAG] = originator_id
    elif pe.until == "end_of_next_turn":
        flags[UNTIL_END_OF_NEXT_TURN_FLAG] = originator_id
    elif pe.until == "end_of_turn":
        flags[UNTIL_END_OF_TURN_FLAG] = True
    return flags


def timed_duration(pe: PassiveEffect) -> dict[str, Any] | None:
    """``pe.duration``, unless ``until`` governs: the turn boundary then ends
    the effect, and the round counter Foundry ships beside it (Shield's
    ``rounds: 1``) would end it a turn early or late."""
    return None if pe.until else pe.duration


def end_events(
    event: CombatEvent, bearer_id: str, dealer_id: str | None = None
) -> set[EffectEndEvent]:
    """The end events ``event`` is for the creature ``bearer_id``.
    ``dealer_id`` is the creature whose act dealt the damage in the stream
    being read (the acting creature of a resolution); without one, no damage
    counts as dealt. Damage a creature deals to itself is not dealt."""
    e = EffectEndEvent
    out: set[EffectEndEvent] = set()
    if isinstance(event, AttackRolled):
        if event.attacker_id == bearer_id:
            out.add(e.ATTACKS)
        if event.target_id == bearer_id:
            out.add(e.IS_ATTACKED)
    elif isinstance(event, SaveRolled | ConcentrationCheck) and event.target_id == bearer_id:
        out.add(e.SAVES)
    elif isinstance(event, CheckRolled) and event.actor_id == bearer_id:
        out.add(e.CHECKS)
    elif isinstance(event, DamageApplied) and event.amount > 0:
        if event.target_id == bearer_id:
            out.add(e.TAKES_DAMAGE)
        elif dealer_id == bearer_id:
            out.add(e.DEALS_DAMAGE)
    elif isinstance(event, Unconscious | Death) and event.target_id == bearer_id:
        out.add(e.ZERO_HP)
    elif isinstance(event, SpellCast) and event.actor_id == bearer_id:
        out.add(e.CASTS_SPELL)
    return out


def is_ended(
    ends_on: Collection[str],
    identity: EffectIdentity,
    events: Sequence[CombatEvent],
    dealer_id: str | None = None,
) -> bool:
    """Does an event in ``events`` end the effect ``identity`` (bearer, id,
    origin)? Only events after the effect's own latest ``EffectApplied`` in
    ``events`` count, so the resolution that granted "until you next hit" with
    its hit does not end it at once; an effect applied before ``events`` starts
    sees all of them. Reading the same events twice ends nothing twice: an
    ended effect is gone."""
    start = 0
    for index in range(len(events) - 1, -1, -1):
        ev = events[index]
        if isinstance(ev, EffectApplied) and (
            (ev.effect.target_id, ev.effect.id, ev.effect.origin) == identity
        ):
            start = index + 1
            break
    wanted = set(ends_on)
    return any(end_events(ev, identity[0], dealer_id) & wanted for ev in events[start:])


def effects_ended_by(
    active_effects: Mapping[str, Sequence[Any]],
    events: Sequence[CombatEvent],
    dealer_id: str | None = None,
) -> list[EffectIdentity]:
    """Every live effect with an ``ends_on`` that ``events`` ends, in
    ``active_effects`` order."""
    return [
        (bearer_id, eff.id, eff.origin)
        for bearer_id, effects in active_effects.items()
        for eff in effects
        if eff.flags.get(ENDS_ON_FLAG)
        and is_ended(eff.flags[ENDS_ON_FLAG], (bearer_id, eff.id, eff.origin), events, dealer_id)
    ]


__all__ = [
    "ENDS_ON_FLAG",
    "UNTIL_END_OF_NEXT_TURN_FLAG",
    "UNTIL_END_OF_TURN_FLAG",
    "UNTIL_START_OF_NEXT_TURN_FLAG",
    "effects_ended_by",
    "end_events",
    "ending_flags",
    "is_ended",
    "timed_duration",
]
