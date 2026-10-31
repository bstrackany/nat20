"""The decisions a driven monster turn makes, as one replaceable object.

``advance_monster_turn`` keeps the rules — action economy, movement budget,
opportunity attacks, resolution — and asks the installed ``MonsterAI`` at each
point where it decides something: whether to flee, whom to target, which action
to rank first, which spell a Spellcasting action casts, where an area lands,
and (optionally) how to close the distance. The default methods are the
engine's own functions, unchanged, called from one hop further out; a host
subclasses ``MonsterAI``, overrides any subset, and installs it with
``set_monster_ai`` for the process.

The ``live`` argument is the engine's live combat state as the orchestrator
holds it — the same object its own decision functions read — so an override
sees exactly what the default saw.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from dnd5e_srd_data.schema.common import CastActivity
    from dnd5e_srd_data.schema.monster import MonsterAction
    from dnd5e_srd_data.schema.spell import Spell

    from .types.combat import Combatant


class MonsterAI:
    """Override any method; each default is the engine's existing behaviour."""

    def is_fleeing(self, live: Any, actor: Combatant) -> bool:
        """Whether the monster spends this turn retreating instead of acting
        (engine default: AGGRESSIVE below 10% HP, RANGED below 25%, DEFENSIVE
        never — ``_monster_is_fleeing``)."""
        from . import orchestrator as o

        return o._monster_is_fleeing(actor)

    def choose_target(
        self, live: Any, actor: Combatant, enemies: list[Combatant]
    ) -> Combatant | None:
        """The creature this turn's attack or spell goes at, from the living
        enemies the monster may target (engine default: lowest current HP —
        ``_lowest_hp_target``). ``None`` makes the turn a pass."""
        from . import orchestrator as o

        return o._lowest_hp_target(enemies)

    def rank_actions(
        self,
        live: Any,
        actor: Combatant,
        actions: Sequence[MonsterAction],
        *,
        is_available: Callable[[MonsterAction], bool],
        has_limited_use_remaining: Callable[[MonsterAction], bool],
    ) -> list[MonsterAction]:
        """The stat-block actions in the order to try them; the first is taken
        (engine default: recharge, then an N/Day offensive spell, then
        Multiattack, then the rest in list order — ``rank_monster_actions``)."""
        from .activities.monster_actions import rank_monster_actions

        return rank_monster_actions(
            actions,
            is_available=is_available,
            has_limited_use_remaining=has_limited_use_remaining,
        )

    def choose_spell(
        self, live: Any, actor: Combatant, action: MonsterAction
    ) -> tuple[CastActivity, Spell] | None:
        """Which of a Spellcasting action's spells to cast (engine default: the
        first offensive one with a use left, limited-use before at-will —
        ``_monster_cast_candidate``). ``None`` means nothing to cast."""
        from . import orchestrator as o

        return o._monster_cast_candidate(live, actor, action)

    def aim(
        self, live: Any, actor: Combatant, activity: Any, template: Any, range_ft: int | None
    ) -> Any:
        """Where an area the monster places lands (engine default: the placement
        that affects the most enemies minus allies, never itself —
        ``_monster_aim``). ``None`` means no placement affects an enemy."""
        from . import orchestrator as o

        return o._monster_aim(live, actor, activity, template, range_ft)

    def plan_move(
        self, live: Any, actor: Combatant, target: Combatant, range_ft: int
    ) -> tuple[list[str], int | None] | None:
        """How to close on a target that is out of range: the path to walk
        (cell ids, the monster's own cell first) and the movement budget after
        a Dash, or ``None`` for no Dash. Returning ``None`` from the method
        itself leaves the engine's own closing walk in place (the default:
        shortest path, Dash when the gap needs it). The engine still walks the
        path step by step, so opportunity attacks and the budget apply."""
        return None


_ai: MonsterAI = MonsterAI()


def set_monster_ai(ai: MonsterAI | None) -> None:
    """Install the process-wide ``MonsterAI``; ``None`` restores the default."""
    global _ai  # one installed AI per process, as with the lib loader
    _ai = ai if ai is not None else MonsterAI()


def monster_ai() -> MonsterAI:
    """The installed ``MonsterAI``."""
    return _ai
