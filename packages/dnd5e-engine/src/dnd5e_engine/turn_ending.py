"""Whether an Action ends the turn, as one replaceable object.

SRD 5.2 Your Turn: "On your turn, you can move a distance up to your Speed and
take one action." The engine ends a creature's turn as soon as its Action
resolves (an Attack action once its swings are spent), unless an Action Surge
extra action is left. Moving and a Bonus Action taken before the Action keep
the turn; after it they are lost.

``TurnEnding`` is asked at that moment; its default says end, so the engine
behaves exactly as without it. A host that lets a player move or take a Bonus
Action after the Action subclasses it, answers ``False``, and installs it with
``set_turn_ending`` for the process. The turn then stays open until the host
submits ``pass``; a second Action is refused as before.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .types.combat import Combatant


class TurnEnding:
    """Override ``after_action``; the default is the engine's existing behaviour."""

    def after_action(self, live: Any, actor: Combatant) -> bool:
        """Whether ``actor``'s turn ends now that its Action has resolved.
        Not asked while an Action Surge extra action is left, nor for ``pass``.
        """
        return True


_turn_ending: TurnEnding = TurnEnding()


def set_turn_ending(turn_ending: TurnEnding | None) -> None:
    """Install the process-wide ``TurnEnding``; ``None`` restores the default."""
    global _turn_ending  # one installed object per process, as with the monster AI
    _turn_ending = turn_ending if turn_ending is not None else TurnEnding()


def turn_ending() -> TurnEnding:
    """The installed ``TurnEnding``."""
    return _turn_ending
