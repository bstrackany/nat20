"""Areas that stay on the map (SRD 5.2 §Areas of Effect).

Many spells and features leave an area behind them: Fog Cloud's "20-foot-radius
Sphere of fog centered on a point within range" stays where it was cast; Spirit
Guardians' "15-foot Emanation" and Aura of Protection's "10-foot Emanation that
originates from you" move with the creature they surround. The corpus marks the
activity that makes one with ``Activity.area`` (``dnd5e_srd_data.schema.common
.PersistentArea``); its shape and size are the activity's own template.

One model covers both kinds: an ``Area`` is **placed** (a point of origin) or
**anchored** (the creature it surrounds). While it lasts, its ``AreaMap``
changes the cells it covers — obscurement, light, Difficult Terrain, silence —
and its ``effects`` apply to the creatures inside it. Those effects are never
copied onto a creature: they are looked up each time a roll reads a creature's
effects, so leaving the area, the bearer becoming Incapacitated or the area
ending can never leave a stale copy behind.

A creature is in an area when any cell it occupies is in the area
(``in_area``); today every creature occupies one cell, and the rule holds
unchanged for larger ones. An area lasts as long as its use: the concentration
that holds it, the spell's duration in rounds, or the whole combat for a feature
that is always on. The orchestrator owns ``live.areas``, the events and the
topology overlay; this module holds the record and the pure questions asked of
it.

What an area does to the creatures it catches (its ``triggers``, E2) is
asked here too: which moments a step is (``step_moments``), whether the cast
resolves the area's activity against nobody (``cast_reaches_nobody``), the
copy an area fires (``fired_copy``), which of a spell's areas fires
(``same_spell_best``) and where a moved area passes (``line_path``,
``away_point``).

**Determinism.** Nothing here draws a die. Areas are kept and walked in the
order they were made, so overlays and ties resolve the same way every replay.
The orchestrator draws an area's dice only when one of its moments happens.
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Final

from dnd5e_srd_data.schema.common import (
    ActivityTrigger,
    ActivityTriggerKind,
    AreaTrigger,
    PersistentArea,
)

from dnd5e_engine.areas import AreaTemplate, area_cells, area_template, has_line_of_effect
from dnd5e_engine.events import AreaKind, AreaShape
from dnd5e_engine.rules.uses import UsesRollData, evaluate_uses_formula
from dnd5e_engine.spatial import GridTopology, cell_id, parse_cell
from dnd5e_engine.specs import LightLevel, Obscurement
from dnd5e_engine.triggers import EffectIdentity, TriggerSource
from dnd5e_engine.types.effects import ActiveEffect

__all__ = [
    "Area",
    "MapOverlay",
    "area_is_live",
    "away_point",
    "cast_reaches_nobody",
    "duration_rounds",
    "effects_inside",
    "emanation_cells",
    "fired_copy",
    "fires_on",
    "in_area",
    "line_path",
    "map_overlay",
    "same_spell_best",
    "silenced",
    "sorted_cells",
    "step_moments",
    "template_of",
]

#: SRD 5.2 §Duration — rounds per unit (a round is 6 seconds).
_ROUNDS_PER_UNIT: Final[dict[str, int]] = {"round": 1, "minute": 10, "hour": 600, "day": 14400}

#: The roll-data token for the level a spell was cast at (Fog Cloud's
#: ``20 * @item.level``: "The fog's radius increases by 20 feet for each spell
#: slot level above 1").
_ITEM_LEVEL: Final = "@item.level"


@dataclass
class Area:
    """One area on the map, from the use that made it until it ends."""

    area_id: str
    owner_id: str
    source_kind: TriggerSource
    source_slug: str
    source_name: str
    spec: PersistentArea
    template: AreaTemplate
    kind: AreaKind
    cells: frozenset[str]
    #: A placed area's point of origin and aim; ``None`` for an anchored one.
    origin: str | None = None
    direction: tuple[int, int] | None = None
    #: The creature an anchored area surrounds; ``None`` for a placed one.
    anchor_id: str | None = None
    #: The level the use was cast at (``None`` for a feature): the most potent of
    #: two castings of one spell is the higher-level one.
    slot_level: int | None = None
    #: The ``spec.effects``, resolved with the owner's numbers when the area was
    #: made (Aura of Protection's bonus is the paladin's Charisma modifier, not
    #: the ally's). ``target_id`` is empty: ``effects_inside`` fills it.
    effects: tuple[ActiveEffect, ...] = ()
    #: What holds the area. ``concentration``: while the owner's concentration
    #: chain still holds any of ``held_by``; otherwise while any ``held_by``
    #: effect remains on a creature; with neither, for ``rounds_remaining``
    #: (counted down at the owner's turn end); ``permanent`` for a feature that
    #: is always on.
    concentration: bool = False
    held_by: frozenset[EffectIdentity] = field(default_factory=frozenset)
    rounds_remaining: int | None = None
    permanent: bool = False
    #: Creation order, the tie-break between equally potent areas.
    seq: int = 0
    #: The activity that made the area: what its ``triggers`` fire, unless
    #: ``spec.fires`` names another (E2).
    activity_id: str = ""
    #: The effects its triggers applied, as the lifecycle identifies them: one
    #: with a duration ends with the area (Black Tentacles' Restrained "until
    #: the spell ends"); one without (Grease's Prone) stands.
    applied: set[EffectIdentity] = field(default_factory=set)

    @property
    def shape(self) -> AreaShape:
        return self.template.shape


@dataclass(frozen=True)
class MapOverlay:
    """The map changes of every area in play, per cell (``GridTopology
    .apply_area_overlay``)."""

    obscurement: Mapping[str, Obscurement]
    lighting: Mapping[str, LightLevel]
    difficult_terrain: frozenset[str]


def sorted_cells(cells: Iterable[str]) -> list[str]:
    """``cells`` by column, then row: the order events and views list them in."""
    return sorted(cells, key=parse_cell)


def in_area(area: Area, occupied: Collection[str]) -> bool:
    """SRD 5.2 — a creature is in an area when any cell it occupies is in it."""
    return not area.cells.isdisjoint(occupied)


def template_of(
    activity: Any, roll_data: UsesRollData, slot_level: int | None, *, ranged: bool = False
) -> AreaTemplate | None:
    """The activity's template on the grid, its size resolved for the user now:
    a number of feet, or a formula of the owner's numbers and the level it was
    cast at (Fog Cloud ``20 * @item.level``, Aura of Protection
    ``@scale.paladin.aura``). ``None`` when the size doesn't resolve or the
    template has no grid shape. ``ranged`` as ``areas.area_template``."""
    size = str(activity.target.template.size or "").strip()
    if not size.isdigit():
        formula = size.replace(_ITEM_LEVEL, str(slot_level)) if slot_level is not None else size
        resolved = evaluate_uses_formula(formula, roll_data)
        if resolved is None:
            return None
        template = activity.target.template.model_copy(update={"size": str(resolved)})
        activity = activity.model_copy(
            update={"target": activity.target.model_copy(update={"template": template})}
        )
    return area_template(activity, ranged=ranged)


def emanation_cells(
    topology: GridTopology, bearer_cells: Collection[str], size_ft: int
) -> frozenset[str]:
    """SRD 5.2 Emanation: it "extends ... from the edge of that creature's
    space" — every cell within ``size_ft`` of any cell the bearer occupies,
    with line of effect from that cell (a wall or a blocked cell drops the
    cells behind it), the bearer's own cells included."""
    cells: set[str] = set()
    for origin in bearer_cells:
        for cell in topology.cells_in_template(origin, "sphere", size_ft):
            if has_line_of_effect(topology, origin, cell):
                cells.add(cell)
    return frozenset(cells)


def placed_cells(
    topology: GridTopology,
    template: AreaTemplate,
    origin: str,
    direction: tuple[int, int] | None,
) -> frozenset[str]:
    """A placed area's cells: its template at ``origin`` with line of effect
    from it (``areas.area_cells``)."""
    return area_cells(topology, template, origin, direction)


def duration_rounds(value: Any, units: Any) -> int | None:
    """A duration in rounds (SRD 5.2 §Duration), or ``None`` for an
    instantaneous or non-metric one."""
    per_unit = _ROUNDS_PER_UNIT.get(str(getattr(units, "value", units) or ""))
    try:
        count = int(value)
    except (TypeError, ValueError):
        return None
    return None if per_unit is None or count <= 0 else count * per_unit


def area_is_live(
    area: Area,
    *,
    concentration_chain: Sequence[EffectIdentity],
    active_effects: Mapping[str, Sequence[ActiveEffect]],
    bearer_present: bool,
) -> bool:
    """Does the area still stand? An anchored one needs its bearer (alive and in
    the combat); then a permanent one always does, a concentration one while
    the owner's chain holds any of its effects, another while any of its
    effects remains on a creature, else while it has rounds left."""
    if area.anchor_id is not None and not bearer_present:
        return False
    if area.permanent:
        return True
    if area.concentration:
        return any(identity in area.held_by for identity in concentration_chain)
    if area.held_by:
        return any(
            eff.id == effect_id and eff.origin == origin
            for target_id, effect_id, origin in area.held_by
            for eff in active_effects.get(target_id, ())
        )
    return area.rounds_remaining is not None and area.rounds_remaining > 0


_OBSCUREMENT_RANK: Final[dict[str, int]] = {"light": 1, "heavy": 2}


def map_overlay(areas: Sequence[Area]) -> MapOverlay:
    """The cells every area changes: the heavier obscurement wins, the later
    area's light wins (a light spell over a darkness, or the other way round),
    and Difficult Terrain is a set (it doesn't stack)."""
    obscurement: dict[str, Obscurement] = {}
    lighting: dict[str, LightLevel] = {}
    difficult: set[str] = set()
    for area in areas:
        changes = area.spec.map
        for cell in area.cells:
            degree = changes.obscurement
            if degree is not None and _OBSCUREMENT_RANK[degree] > _OBSCUREMENT_RANK.get(
                obscurement.get(cell, ""), 0
            ):
                obscurement[cell] = degree
            if changes.lighting is not None:
                lighting[cell] = changes.lighting
        if changes.difficult_terrain:
            difficult |= area.cells
    return MapOverlay(obscurement, lighting, frozenset(difficult))


def silenced(areas: Sequence[Area], occupied: Collection[str]) -> bool:
    """SRD 5.2 Silence: "Casting a spell that includes a Verbal component is
    impossible there" — is a creature occupying ``occupied`` in such an area?"""
    return any(area.spec.map.blocks_verbal and in_area(area, occupied) for area in areas)


def _potency(area: Area, effect: ActiveEffect) -> tuple[int, int, int]:
    """How potent an area's effect is, for SRD 5.2 "Combining Game Effects":
    the higher slot, then the larger numeric change (two paladins' Aura of
    Protection), then the more recent area."""
    numeric = 0
    for change in effect.changes:
        try:
            numeric += int(str(change.value))
        except ValueError:
            continue
    return (area.slot_level or 0, numeric, area.seq)


def effects_inside(
    areas: Sequence[Area],
    occupied: Collection[str],
    creature_id: str,
    *,
    reaches: Callable[[Area, str], bool],
    active: Callable[[Area], bool],
) -> list[ActiveEffect]:
    """The effects the areas give a creature occupying ``occupied`` now: every
    area it is in, that is ``active`` (its bearer qualifies) and that
    ``reaches`` it (``spec.affects`` seen from the owner).

    SRD 5.2 Combining Game Effects: "The effects of the same spell cast multiple
    times don't combine. Instead, the most potent effect ... applies while their
    durations overlap. The most recent effect applies if the castings are
    equally potent" — so of two areas from one source (two Spirit Guardians, two
    paladins' auras: "a creature can benefit from only one Aura of Protection at
    a time") only the most potent one's effect applies."""
    best: dict[tuple[str, str], tuple[tuple[int, int, int], ActiveEffect]] = {}
    for area in areas:
        if not area.effects or not in_area(area, occupied):
            continue
        if not active(area) or not reaches(area, creature_id):
            continue
        for effect in area.effects:
            key = (area.source_slug, effect.id)
            potency = _potency(area, effect)
            if key not in best or potency > best[key][0]:
                best[key] = (potency, effect)
    return [effect.model_copy(update={"target_id": creature_id}) for _, effect in best.values()]


# ── E2: what an area does to the creatures it catches ───────────────────────
#
# SRD 5.2 writes these moments into each spell: Grease "When the grease
# appears, each creature standing in its area ... A creature that enters the
# area or ends its turn there"; Stinking Cloud "Each creature that starts its
# turn in the Sphere"; Spike Growth "When a creature moves into or within the
# area, it takes 2d4 Piercing damage for every 5 feet it travels"; Spirit
# Guardians "whenever the Emanation enters a creature's space". The data names
# them (``PersistentArea.triggers``); the orchestrator fires the area's activity
# at each through the held-use path (``_fire_triggered_activity``).


def fires_on(area: Area, moment: AreaTrigger) -> bool:
    """Does the area fire at ``moment``?"""
    return moment in area.spec.triggers


def cast_reaches_nobody(activity: Any) -> bool:
    """Does the cast resolve this area activity against nobody, because the
    area itself does the work later?

    - It has moments but not ``created``: Web's save is "The first time a
      creature enters the webs on a turn or starts its turn there", not when
      the webs appear; Spike Growth's damage is for moving in it.
    - It is a utility whose every effect the area delivers live: Silence's
      creatures are Deafened and immune to Thunder "while entirely inside it",
      so the cast gives nobody a copy — a caster inside has it through the area,
      a caster outside never does."""
    spec = getattr(activity, "area", None)
    if spec is None:
        return False
    if spec.triggers and AreaTrigger.CREATED not in spec.triggers:
        return True
    refs = [ref.id for ref in getattr(activity, "effects", ())]
    return activity.kind == "utility" and bool(refs) and set(refs) <= set(spec.effects)


def fired_copy(activity: Any, spec: PersistentArea) -> Any:
    """The activity as an area fires it: held for the ``area`` moment (the
    held-use resolver needs a trigger), without its own ``area``, and without
    the effects the area delivers live (Spirit Guardians' halved Speed is "in
    the Emanation", never a copy that outlasts leaving it)."""
    refs = [ref for ref in getattr(activity, "effects", ()) if ref.id not in spec.effects]
    update: dict[str, Any] = {
        "trigger": ActivityTrigger(kind=ActivityTriggerKind.AREA),
        "area": None,
    }
    if hasattr(activity, "effects"):
        update["effects"] = refs
    return activity.model_copy(update=update)


def step_moments(area: Area, was_in: bool, now_in: bool) -> list[AreaTrigger]:
    """The moments one 5-foot step is for the area: ``enters`` on the step
    that brings the creature into it, ``moves_within`` on every step that ends
    in it, the entering one included (Spike Growth: "moves into or within the
    area ... for every 5 feet it travels")."""
    moments: list[AreaTrigger] = []
    if now_in and not was_in and fires_on(area, AreaTrigger.ENTERS):
        moments.append(AreaTrigger.ENTERS)
    if now_in and fires_on(area, AreaTrigger.MOVES_WITHIN):
        moments.append(AreaTrigger.MOVES_WITHIN)
    return moments


def same_spell_best(areas: Iterable[Area]) -> list[Area]:
    """SRD 5.2 Combining Game Effects — "The effects of the same spell cast
    multiple times don't combine": of the areas from one source that catch a
    creature at one moment, only the most potent fires (the higher slot, then
    the more recent). Kept in creation order."""
    best: dict[str, Area] = {}
    for area in areas:
        held = best.get(area.source_slug)
        if held is None or ((area.slot_level or 0), area.seq) > ((held.slot_level or 0), held.seq):
            best[area.source_slug] = area
    return sorted(best.values(), key=lambda area: area.seq)


def line_path(start: str, end: str) -> list[str]:
    """The cells an area's point of origin passes moving from ``start`` to
    ``end``, one 5-foot step at a time (diagonal steps first, then straight),
    ``end`` included and ``start`` not."""
    col, row = parse_cell(start)
    end_col, end_row = parse_cell(end)
    path: list[str] = []
    while (col, row) != (end_col, end_row):
        col += (end_col > col) - (end_col < col)
        row += (end_row > row) - (end_row < row)
        path.append(cell_id(col, row))
    return path


def away_point(owner: str, origin: str, distance_ft: int, cell_ft: int) -> str | None:
    """Cloudkill's drift: the point ``distance_ft`` straight away from
    ``owner`` (the sign of ``origin - owner`` per axis), or ``None`` when the
    area is centred on its owner and "away" has no direction."""
    oc, orow = parse_cell(owner)
    col, row = parse_cell(origin)
    dc, dr = (col > oc) - (col < oc), (row > orow) - (row < orow)
    if (dc, dr) == (0, 0):
        return None
    steps = distance_ft // cell_ft
    return cell_id(col + dc * steps, row + dr * steps)
