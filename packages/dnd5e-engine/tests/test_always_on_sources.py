"""A character's features and feats are in play for the whole combat.

Their always-on effects (``transfer`` and not ``disabled``) count the way a
cast's do — the keys the sheet derivation does not already fold are seeded as
permanent effects at ``start_combat`` — and every activity they hold a
trigger for is a permanent use: a "when you hit" rider fires on each hit, once
per turn when the trigger says so. The SRD corpus ships no feat effects, so
these tests build a feat of their own on top of the bundled corpus, the way a
consumer's data would.
"""

from __future__ import annotations

import asyncio
from datetime import date

import pytest
from dnd5e_srd_data import Feat, FeatCategory, Provenance, ReviewState
from dnd5e_srd_data.loader import BundledAssetLoader
from dnd5e_srd_data.schema.common import (
    ActivityTrigger,
    DamageActivity,
    DamageActivityDamageBlock,
    DamagePartBlock,
    PassiveEffect,
    PassiveEffectChange,
)

from dnd5e_engine import (
    EncounterMemberSpec,
    GridScene,
    PartyMemberSpec,
    PlayerIntent,
    start_combat,
    submit_player_intent,
)
from dnd5e_engine.build_spec import derive_sheet, make_build_spec
from dnd5e_engine.events import ActivityTriggered, AttackRolled, DamageApplied, EffectApplied
from dnd5e_engine.lib_loader import set_lib_loader_for_tests
from dnd5e_engine.orchestrator import _build_hydration_payload, _find_combatant, _get_live
from dnd5e_engine.spatial import cell_id as cell

BUNDLED = BundledAssetLoader()


def _feat() -> Feat:
    """A homebrew-shaped feat: advantage on Wisdom saves, immunity to the
    Charmed condition, and 1d4 Fire damage once per turn on a weapon hit."""
    return Feat(
        slug="ember-heart",
        name="Ember Heart",
        description="Test feat.",
        category=FeatCategory.GENERAL,
        passive_effects=[
            PassiveEffect(
                _id="emberheartpassive",
                name="Ember Heart",
                changes=[
                    PassiveEffectChange(
                        key="system.abilities.wis.save.roll.mode", mode=2, value="1"
                    ),
                    PassiveEffectChange(key="system.traits.ci.value", mode=2, value="charmed"),
                ],
            )
        ],
        activities=[
            DamageActivity(
                _id="emberlash",
                name="Ember Lash",
                damage=DamageActivityDamageBlock(
                    parts=[DamagePartBlock(number=1, denomination=4, types=["fire"])]
                ),
                trigger=ActivityTrigger(kind="hits", subject="caster", once_per_turn=True),
            )
        ],
        provenance=Provenance(
            source="foundry",
            source_url="test://ember-heart",
            ingest_date=date(2026, 1, 1),
            ingest_version="test",
            srd_version=["5.2"],
        ),
        review=ReviewState(),
    )


class _WithFeat:
    def __init__(self, feat: Feat):
        self._feat = feat

    def get_feat(self, slug: str):
        return self._feat if slug == self._feat.slug else BUNDLED.get_feat(slug)

    def __getattr__(self, name: str):
        return getattr(BUNDLED, name)


@pytest.fixture(autouse=True)
def _loader():
    set_lib_loader_for_tests(_WithFeat(_feat()))
    yield
    set_lib_loader_for_tests(None)


def _events(live, kind):
    return [e for e in live.event_log if isinstance(e, kind)]


async def _start(seed: int = 2):
    start = await start_combat(
        session_id="always-on",
        party=[
            PartyMemberSpec(
                entity_id="char:ftr",
                name="fighter",
                initiative=20,
                hp_current=60,
                hp_max=60,
                strength=16,
                class_slug="fighter",
                character_level=5,  # Extra Attack: two swings an Action
                feats=("ember-heart",),
                zone_id=cell(0, 0),
            )
        ],
        encounter=[
            EncounterMemberSpec(
                entity_id="mon:dummy",
                entity_type="Monster",
                name="dummy",
                initiative=1,
                hp_current=500,
                hp_max=500,
                ac=1,
                zone_id=cell(1, 0),
            )
        ],
        grid_scene=GridScene(width=10, height=10, cell_size_ft=5),
        rng_seed=seed,
    )
    return start.handle, _get_live(start.handle)


def test_a_feats_always_on_effect_is_in_play_from_the_start():
    async def _go():
        _handle, live = await _start()
        fighter = _find_combatant(live, "char:ftr")
        return live, fighter, _build_hydration_payload(live, caster=fighter)

    live, fighter, payload = asyncio.run(_go())
    [seeded] = live.active_effects["char:ftr"]
    assert (seeded.origin, seeded.flags) == ("feat:ember-heart:char:ftr", {"always_on": True})
    # the live-read key is seeded; the condition immunity is the build's and is
    # already on the Combatant, not seeded twice
    assert [c.key for c in seeded.changes] == ["system.abilities.wis.save.roll.mode"]
    assert payload["save_modifiers"]["char:ftr"]["passive_save_adv"] == ["WIS"]
    assert fighter.condition_immunities == ["charmed"]
    assert not _events(live, EffectApplied)  # nothing happened: the fighter arrived that way


def test_a_feats_on_hit_rider_fires_once_per_turn():
    """Two swings land in one Action; the rider's "once per turn" fires on the
    first only, and again on the next turn."""

    async def _go():
        handle, live = await _start()
        swing = PlayerIntent(intent_type="attack", weapon_id="longsword", target_id="mon:dummy")
        await submit_player_intent(handle, actor_id="char:ftr", intent=swing)
        await submit_player_intent(handle, actor_id="char:ftr", intent=swing)
        from dnd5e_engine.orchestrator import advance_monster_turn

        if live.current_actor_id == "char:ftr":
            await submit_player_intent(
                handle, actor_id="char:ftr", intent=PlayerIntent(intent_type="pass")
            )
        await advance_monster_turn(handle)
        await submit_player_intent(handle, actor_id="char:ftr", intent=swing)
        return live

    live = asyncio.run(_go())
    hits = [a for a in _events(live, AttackRolled) if a.attacker_id == "char:ftr"]
    assert [h.is_hit for h in hits] == [True, True, True]
    fired = _events(live, ActivityTriggered)
    assert [(f.source_kind, f.source_id, f.trigger) for f in fired] == [
        ("feat", "ember-heart", "hits"),
        ("feat", "ember-heart", "hits"),
    ]
    fire = [e for e in _events(live, DamageApplied) if e.damage_type == "fire"]
    assert len(fire) == 2
    # the first swing's rider lands before the second swing is rolled
    assert live.event_log.index(fire[0]) < live.event_log.index(hits[1])
    [use] = live.held_uses
    assert use.permanent


def test_a_feats_effects_reach_the_derived_sheet():
    """Build-time: a feat's always-on changes join the class features' in
    ``derive_sheet`` (its Charmed immunity lands on the sheet)."""
    sheet = derive_sheet(
        make_build_spec(
            species_slug="human",
            class_slug="fighter",
            level=4,
            background_slug="soldier",
            selected_choices=("feat:fighter:4:ember-heart",),
        ),
        loader=_WithFeat(_feat()),
    )
    assert "ember-heart" in sheet.feats
    assert "charmed" in sheet.condition_immunities
