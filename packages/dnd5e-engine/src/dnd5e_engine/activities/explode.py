"""Foundry's exploding dice (``x``) for the seeded roller.

SRD 5.2 Sorcerous Burst: "If you roll an 8 on a d8 for this spell, you can roll
another d8, and add it to the damage. When you cast this spell, the maximum
number of these d8s you can add to the spell's damage equals your spellcasting
ability modifier." The corpus carries Foundry's ``1d8x@mod=8``: ``x`` explodes,
the number after it caps the extra dice for the whole term, ``=F`` names the
face that explodes (the die's highest when left out).

``d20`` has no cap, so ``parse`` rewrites each Foundry term to d20's own explode
(``1d8e8``) and keeps the cap on the parsed node; ``roll`` draws it from the
seeded rng. Only ``x``, ``x<cap>`` and ``x[<cap>]=<face>`` are read; any other
Foundry modifier still fails to parse.
"""

from __future__ import annotations

import random
import re

import d20

_FOUNDRY_EXPLODE = re.compile(r"(\d*)d(\d+)x(-?\d+)?(?:=(\d+))?", re.IGNORECASE)


def parse(expr: str) -> d20.Expression:
    """``d20.parse`` after rewriting Foundry explode terms.

    Each exploding node carries its ``cap`` (None: no cap).
    """
    caps: list[int | None] = []

    def _rewrite(match: re.Match[str]) -> str:
        count, size, cap, face = match.groups()
        caps.append(int(cap) if cap is not None else None)
        return f"{count}d{size}e{face or size}"

    ast = d20.parse(_FOUNDRY_EXPLODE.sub(_rewrite, expr))
    nodes = [n for n in _walk(ast.roll) if isinstance(n, d20.ast.OperatedDice)]
    for node, cap in zip(nodes, caps, strict=False):
        node.cap = cap
    return ast


def roll(node: d20.ast.OperatedDice, rng: random.Random, die_floor: int | None = None) -> int:
    """Roll an exploding term: each explode face adds a die (which may explode) up to the cap."""
    dice, face, budget = node.value, _explode_face(node), getattr(node, "cap", None)
    faces = [rng.randint(1, int(dice.size)) for _ in range(int(dice.num))]
    pending = (
        sum(1 for f in faces if f == face) if int(dice.size) > 1 else 0
    )  # a d1 would explode forever
    while pending and (budget is None or budget > 0):
        extra = rng.randint(1, int(dice.size))
        faces.append(extra)
        pending += (extra == face) - 1
        budget = None if budget is None else budget - 1
    return sum(faces if die_floor is None else (max(f, die_floor) for f in faces))


def _explode_face(node: d20.ast.OperatedDice) -> int:
    ops = [op for op in node.operations if op.op == "e"]
    if (
        len(ops) != len(node.operations)
        or len(ops) != 1
        or len(ops[0].sels) != 1
        or ops[0].sels[0].cat is not None
    ):
        raise ValueError(f"Unsupported dice operation in expression: {node}")
    return int(ops[0].sels[0].num)


def _walk(node: d20.ast.Node) -> list[d20.ast.Node]:
    return [node, *(n for child in node.children for n in _walk(child))]
