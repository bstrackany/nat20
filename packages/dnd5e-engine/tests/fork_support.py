"""A consumer's own content on top of the bundled corpus, and the casters and
goblins the fork's tests fight with (on ``tests.c20_support``'s grid)."""

from __future__ import annotations

from datetime import date
from typing import Any

from dnd5e_srd_data import Feat, FeatCategory, Provenance, ReviewState
from dnd5e_srd_data.loader import BundledAssetLoader

from dnd5e_engine import CombatHandle
from dnd5e_engine.specs import EncounterMemberSpec, PartyMemberSpec
from tests.c20_support import act, foe, pc

BUNDLED = BundledAssetLoader()


class WithAssets:
    """The bundled corpus plus spells and feats of the consumer's own."""

    def __init__(self, *assets: Any):
        self._feats = {a.slug: a for a in assets if isinstance(a, Feat)}
        self._spells = {a.slug: a for a in assets if not isinstance(a, Feat)}

    def get_feat(self, slug: str) -> Any:
        return self._feats.get(slug) or BUNDLED.get_feat(slug)

    def get_spell(self, slug: str) -> Any:
        return self._spells.get(slug) or BUNDLED.get_spell(slug)

    def __getattr__(self, name: str) -> Any:
        return getattr(BUNDLED, name)


def homebrew_feat(slug: str, *, activities: Any = (), passive_effects: Any = ()) -> Feat:
    return Feat(
        slug=slug,
        name=slug.replace("-", " ").title(),
        description="Test feat.",
        category=FeatCategory.GENERAL,
        activities=list(activities),
        passive_effects=list(passive_effects),
        provenance=Provenance(
            source="homebrew",
            source_url=f"test://{slug}",
            ingest_date=date(2026, 1, 1),
            ingest_version="test",
            license_tag="unspecified",
        ),
        review=ReviewState(),
    )


def caster(spells: list[str], slots: dict[int, int], **fields: Any) -> PartyMemberSpec:
    """A Wizard 11 (STR 16, DEX 14, INT 16, WIS 16) with 60 HP at cell 0,0."""
    base: dict[str, Any] = {
        "hp_current": 60,
        "hp_max": 60,
        "strength": 16,
        "dexterity": 14,
        "intelligence": 16,
        "wisdom": 16,
        "attack_bonus": 7,
        "class_slug": "wizard",
        "character_level": 11,
        "spells_known": spells,
        "spell_slots": slots,
    }
    return pc("char:caster", **(base | fields))


def goblin(**fields: Any) -> EncounterMemberSpec:
    """A 200-HP, AC 15 goblin warrior at cell 1,0 that acts last."""
    base: dict[str, Any] = {"name": "goblin", "hp_current": 200, "hp_max": 200, "ac": 15}
    return foe(**(base | {"monster_template_slug": "goblin-warrior"} | fields))


def cast(handle: CombatHandle, spell_id: str, actor_id: str = "char:caster", **fields: Any) -> None:
    act(handle, actor_id, intent_type="cast_spell", spell_id=spell_id, **fields)
