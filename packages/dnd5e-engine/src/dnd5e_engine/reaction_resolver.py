"""Whether a reaction fires, as one replaceable object.

The engine never pauses to ask (``docs/concepts/reactions.md``): when an armed
reaction answers a trigger, the first one eligible fires, creatures in
initiative order and each one's reactions in arming order, and every creature
that may make an opportunity attack makes it. ``ReactionResolver`` is asked at
each of those points just before the reaction fires; its default says yes, so
the engine behaves exactly as without it. A host subclasses it, overrides
``fires``, and installs it with ``set_reaction_resolver`` for the process.

A host that asks a person can raise from ``fires``: nothing in the engine
catches it, no reaction has been spent and no die drawn for it yet, and the
host replays the combat from its seed with the answer.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .types.combat import Combatant


class ReactionResolver:
    """Override ``fires``; its default is the engine's existing behaviour."""

    def fires(
        self, live: Any, reactor: Combatant, reaction: Any, trigger: str, context: Any
    ) -> bool:
        """Whether ``reactor``'s eligible ``reaction`` fires now. ``False``
        leaves it armed and lets the next eligible one answer.

        :param reaction: The armed entry (``spell_id`` or ``feature_id``,
            ``activity_id``), or ``None`` for an opportunity attack.
        :param trigger: The armed trigger (``"hit_by_attack"``, ``"d20_test"``,
            ``"takes_damage"`` …) or ``"opportunity_attack"``.
        :param context: What it answers: the ``D20Outcome`` of a roll it would
            change, the ``IncomingDamage`` it would reduce, else the id of the
            creature whose act triggered it (the mover, for an opportunity
            attack).
        """
        return True


_resolver: ReactionResolver = ReactionResolver()


def set_reaction_resolver(resolver: ReactionResolver | None) -> None:
    """Install the process-wide ``ReactionResolver``; ``None`` restores the default."""
    global _resolver  # one installed resolver per process, as with the monster AI
    _resolver = resolver if resolver is not None else ReactionResolver()


def reaction_resolver() -> ReactionResolver:
    """The installed ``ReactionResolver``."""
    return _resolver
