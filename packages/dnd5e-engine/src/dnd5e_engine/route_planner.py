"""Which route a creature walks, as one replaceable object.

SRD 5.2 Movement and Position lets a creature "move a distance up to your
Speed"; which squares it crosses on the way is the mover's choice, and the
rules only price it (Difficult Terrain doubles a square) and react to it
(leaving an enemy's reach provokes an Opportunity Attack). The engine picks a
route in three places: a player's ``move`` (``"move"``), a monster closing on
its target (``"approach"``, when the ``MonsterAI`` plans no move itself) and a
fleeing monster's retreat (``"flee"``).

``RoutePlanner`` is asked at each with the route the engine picked; its default
returns that route, so the engine behaves exactly as without it. A host that
prefers another route (the cheapest, or one that provokes no attacks)
subclasses it and installs it with ``set_route_planner`` for the process.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from .types.combat import Combatant

RoutePurpose = Literal["move", "approach", "flee"]


class RoutePlanner:
    """Override ``route``; the default is the engine's existing behaviour."""

    def route(
        self,
        live: Any,
        mover: Combatant,
        engine_route: list[str],
        *,
        purpose: RoutePurpose,
        start: str,
        goal: str,
        avoid: frozenset[str],
        budget_ft: int,
        provokes: Callable[[str, str], bool],
    ) -> list[str]:
        """The cells ``mover`` walks from ``start`` to ``goal``, ``start``
        first; ``[]`` for no route.

        ``engine_route`` is the engine's own pick: the fewest-cells route for
        ``"move"`` and ``"approach"`` (``[]`` when there is none), the cheapest
        for ``"flee"``. ``avoid`` holds the cells the engine kept the mover out of
        (an enemy's space; none for ``"approach"``). ``budget_ft`` is the movement it has left; a
        ``"move"`` route that costs more is refused whole, an ``"approach"`` or
        ``"flee"`` walk stops where the budget runs out. ``provokes(a, b)`` is
        true when the step from cell ``a`` to the adjacent cell ``b`` would let
        anyone make an Opportunity Attack against ``mover`` as things stand now.

        Each step must be a legal one (``GridTopology.edge_distance`` not
        ``None``) and keep out of ``avoid``: the engine prices and walks the
        route as returned. For ``"approach"`` the goal is the target's own
        cell, and the walk stops once the target is in range.
        """
        return engine_route


_route_planner: RoutePlanner = RoutePlanner()


def set_route_planner(route_planner: RoutePlanner | None) -> None:
    """Install the process-wide ``RoutePlanner``; ``None`` restores the default."""
    global _route_planner  # one installed object per process, as with the monster AI
    _route_planner = route_planner if route_planner is not None else RoutePlanner()


def route_planner() -> RoutePlanner:
    """The installed ``RoutePlanner``."""
    return _route_planner
