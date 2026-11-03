"""Always-on effects that count only while something holds of their bearer.

A feature's or feat's always-on effect is seeded on the character for the whole
combat (``_seed_always_on``). Some only count while a condition holds — SRD 5.2
Mindless Rage: "You have Immunity to the Charmed and Frightened conditions
while your Rage is active"; Danger Sense: "You have Advantage on Dexterity
saving throws unless you have the Incapacitated condition". The corpus writes
the condition on the effect definition (``PassiveEffect.active_while``,
``dnd5e_srd_data.schema.common.ActiveWhile``); ``gate_flags`` carries it onto
the seeded ``ActiveEffect.flags``, and ``in_force`` drops an effect whose
condition does not hold each time a creature's effects are read. Nothing is
added or removed when the condition changes, so the effect can never be left
stale.

Pure: no I/O, no RNG.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, Final

from dnd5e_srd_data.schema.common import ActiveWhile

from dnd5e_engine.rules.conditions import active_condition_names, conditions_block_actions

if TYPE_CHECKING:
    from dnd5e_srd_data.schema.common import PassiveEffect

    from dnd5e_engine.types.combat import Combatant
    from dnd5e_engine.types.effects import ActiveEffect

#: The ``ActiveEffect.flags`` key that carries the condition (as its dump).
ACTIVE_WHILE_FLAG: Final = "active_while"

#: Keys the sheet derivation folds into the character for an unconditional
#: effect (resistances, immunities, condition immunities). A conditional one is
#: left out of the sheet and read live instead, where combat already reads
#: these keys from an applied effect (Rage's resistances, Heroism's Frightened).
GATED_LIVE_KEYS: Final = frozenset(
    {"system.traits.dr.value", "system.traits.di.value", "system.traits.ci.value"}
)


def gate_flags(passive: PassiveEffect) -> dict[str, Any]:
    """The flag that carries ``passive``'s condition onto the effect it is
    seeded as; empty when it has none."""
    if passive.active_while is None:
        return {}
    return {ACTIVE_WHILE_FLAG: passive.active_while.model_dump(mode="json")}


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def _names_of(effect: ActiveEffect) -> set[str]:
    """``effect``'s name and id as slugs: Rage applied from its feature is
    ``effect:rage`` named "Rage"; a Wild Shape form is ``effect:wild-shape``."""
    return {_slug(effect.name), _slug(effect.id.removeprefix("effect:"))}


def holds(condition: ActiveWhile, bearer: Combatant, effects: Sequence[ActiveEffect]) -> bool:
    """Every set field of ``condition`` holds of ``bearer`` now, ``effects``
    being all the effects it bears."""
    if condition.effect and not any(_slug(condition.effect) in _names_of(e) for e in effects):
        return False
    # SRD 5.2 Rules Glossary: "A creature is Bloodied while it has half its Hit
    # Points or fewer remaining."
    if condition.bloodied and not (bearer.hp_max > 0 and bearer.hp_current * 2 <= bearer.hp_max):
        return False
    return not (
        condition.unless_incapacitated
        and conditions_block_actions(active_condition_names(bearer.conditions))
    )


def in_force(effects: Sequence[ActiveEffect], bearer: Combatant) -> list[ActiveEffect]:
    """``effects`` (all of ``bearer``'s) without those whose ``active_while``
    does not hold now. Unchanged when none carries one."""
    if not any(ACTIVE_WHILE_FLAG in e.flags for e in effects):
        return list(effects)
    return [
        e
        for e in effects
        if ACTIVE_WHILE_FLAG not in e.flags
        or holds(ActiveWhile.model_validate(e.flags[ACTIVE_WHILE_FLAG]), bearer, effects)
    ]
