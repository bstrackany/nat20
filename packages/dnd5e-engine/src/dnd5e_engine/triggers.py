"""Held uses — the activities a spell, feature or item fires later.

SRD 5.2 puts part of many spells after the cast: Acid Arrow's "2d4 Acid
damage at the end of its next turn", Hex's "extra 1d6 Necrotic damage to the
target whenever you hit it with an attack roll", Sunbeam's "you can take a
Magic action to create a new Line of radiance". The corpus marks such an
activity with ``Activity.trigger`` (``dnd5e_srd_data.schema.common
.ActivityTrigger``); an activity without one fires with the use, as before.

When a use resolves, the orchestrator keeps a ``HeldUse`` for it: who used
what, at what level, which activities wait, and the effects the use has
applied so far (``(target_id, effect.id, effect.origin)`` identities, the same
key the effect lifecycle uses). The use lives while any of those effects
remains on a creature — or, for a use that applied none, for the spell's own
duration in rounds — and its triggers end with it. This module holds that
record and the pure questions asked of it; the orchestrator owns the firing
(``_fire_turn_triggers``, ``_fire_resolution_triggers``) because firing
builds an activity context and emits events.

**Determinism.** Uses are kept in the order they were made and walked in that
order; a trigger fires once per matching event, in event order.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from dnd5e_srd_data.schema.common import (
    ActivityTrigger,
    ActivityTriggerKind,
    ActivityTriggerSubject,
)

__all__ = [
    "EffectIdentity",
    "HeldUse",
    "TriggerSource",
    "bearers_of",
    "fires_with_use",
    "is_later_use",
    "trigger_of",
    "triggered_activities",
    "use_is_live",
]

#: ``(target_id, effect.id, effect.origin)`` — the effect lifecycle's key.
EffectIdentity = tuple[str, str, str]

TriggerSource = Literal["spell", "feature", "item", "feat"]


@dataclass
class HeldUse:
    """One resolved use whose activities fire later."""

    owner_id: str
    source_kind: TriggerSource
    source_slug: str
    source_name: str
    #: The level the use was cast at, and the spell's own level (``None`` for a
    #: feature or an item): a fired activity scales as the cast did.
    slot_level: int | None
    base_level: int | None
    spellcasting_ability: str | None
    #: The activities that carry a trigger, in corpus order.
    activities: list[Any]
    passive_effects: list[Any]
    #: Effects this use has applied, as the lifecycle identifies them. Grows
    #: when a fired activity applies more (Searing Smite's burn on the hit).
    effects: set[EffectIdentity] = field(default_factory=set)
    #: For a use that applied no effect: the rounds the spell lasts, counted
    #: down at the owner's turn end. ``None`` once an effect anchors the use.
    rounds_remaining: int | None = None
    #: Activity ids whose ``once`` trigger has fired.
    spent: set[str] = field(default_factory=set)
    #: A feature or feat the owner has for the whole combat: the use never ends.
    permanent: bool = False
    #: For ``once_per_turn``: the ``(round, actor)`` turn each activity last fired in.
    fired_turns: dict[str, tuple[int, str | None]] = field(default_factory=dict)


def trigger_of(activity: Any) -> ActivityTrigger | None:
    """The activity's trigger, if the corpus gave it one."""
    trigger = getattr(activity, "trigger", None)
    return trigger if isinstance(trigger, ActivityTrigger) else None


def is_later_use(activity: Any) -> bool:
    """A ``later_use`` activity: the caller chooses when, on a later turn."""
    trigger = trigger_of(activity)
    return trigger is not None and trigger.kind is ActivityTriggerKind.LATER_USE


def fires_with_use(activity: Any) -> bool:
    """Does the activity resolve with its parent's use (a cast, a feature or
    item use)? Everything without a trigger does; a ``later_use`` activity does
    only when ``also_at_cast`` says so (Call Lightning's first bolt)."""
    trigger = trigger_of(activity)
    if trigger is None:
        return True
    return trigger.kind is ActivityTriggerKind.LATER_USE and trigger.also_at_cast


def triggered_activities(activities: Iterable[Any]) -> list[Any]:
    """The activities a use holds for later: every one with a trigger."""
    return [a for a in activities if trigger_of(a) is not None]


def _present(identity: EffectIdentity, active_effects: Mapping[str, Sequence[Any]]) -> bool:
    target_id, effect_id, origin = identity
    return any(
        eff.id == effect_id and eff.origin == origin for eff in active_effects.get(target_id, ())
    )


def bearers_of(use: HeldUse, active_effects: Mapping[str, Sequence[Any]]) -> list[str]:
    """The creatures still bearing an effect of the use, in a stable order."""
    return sorted({identity[0] for identity in use.effects if _present(identity, active_effects)})


def use_is_live(use: HeldUse, active_effects: Mapping[str, Sequence[Any]]) -> bool:
    """Does the use still hold its triggers? A permanent one always; otherwise
    while an effect of it remains on a creature, or, for a use that applied
    none, for its ``rounds_remaining``."""
    if use.permanent:
        return True
    if use.effects:
        return any(_present(identity, active_effects) for identity in use.effects)
    return use.rounds_remaining is not None and use.rounds_remaining > 0


def subjects_of(
    use: HeldUse,
    trigger: ActivityTrigger,
    candidate_id: str,
    active_effects: Mapping[str, Sequence[Any]],
) -> bool:
    """Is ``candidate_id`` a subject of ``trigger`` for this use — the owner
    (``subject: caster``) or a creature bearing an effect of the use
    (``subject: target``)?"""
    if trigger.subject is ActivityTriggerSubject.CASTER:
        return candidate_id == use.owner_id
    return candidate_id in bearers_of(use, active_effects)
