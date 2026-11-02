"""Foundry exploding dice (``x``) — Sorcerous Burst's ``1d8x@mod=8``.

SRD 5.2 Sorcerous Burst: "If you roll an 8 on a d8 for this spell, you can roll
another d8, and add it to the damage. ... the maximum number of these d8s you
can add to the spell's damage equals your spellcasting ability modifier."
"""

from __future__ import annotations

import random

import pytest
from dnd5e_srd_data.loader import BundledAssetLoader

from dnd5e_engine.activities.dice import roll_expr


class _Faces(random.Random):
    """An rng that shows the given faces in order."""

    def __init__(self, *faces: int) -> None:
        super().__init__(0)
        self.faces = list(faces)

    def randint(self, a: int, b: int) -> int:
        return self.faces.pop(0)


@pytest.mark.parametrize(
    ("expr", "faces", "total"),
    [
        ("1d8x3=8", (5,), 5),  # no 8, no extra die
        ("1d8x3=8", (8, 4), 12),  # an 8 adds a d8
        ("1d8x3=8", (8, 8, 8, 8), 32),  # three extra dice, the last 8 adds no fourth
        ("1d8x0=8", (8,), 8),  # a +0 modifier adds none
        ("1d8x-1=8", (8,), 8),
        ("1d8x=8+2", (8, 8, 1), 19),  # no cap: extras keep exploding; modifiers fold as usual
        ("2d8x1=8", (8, 8, 3), 19),  # the cap is for the whole term, not each die
        ("1d6x", (6, 2), 8),  # no face: the die's highest
    ],
)
def test_foundry_explode(expr: str, faces: tuple[int, ...], total: int) -> None:
    rng = _Faces(*faces)
    assert roll_expr(expr, rng) == total
    assert rng.faces == []  # no die drawn beyond the ones the rule allows


def test_a_crit_doubles_the_dice_and_keeps_the_cap() -> None:
    assert roll_expr("1d8x1=8", _Faces(8, 8, 8), crit=True) == 24


def test_sorcerous_burst_corpus_formula_rolls() -> None:
    spell = BundledAssetLoader().get_spell("sorcerous-burst")
    assert spell is not None
    formula = next(
        p.custom.formula
        for a in spell.activities
        for p in getattr(getattr(a, "damage", None), "parts", [])
        if p.custom.enabled
    )
    assert formula == "1d8x@mod=8"
    assert roll_expr(formula.replace("@mod", "3"), _Faces(8, 2)) == 10
