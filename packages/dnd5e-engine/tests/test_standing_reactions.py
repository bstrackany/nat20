"""Reactions armed from data, for the whole combat.

An ``"arm"`` intent keeps a spell, feature or feat reaction armed: no Action, the
trigger read from the reaction's own typed condition (Shield: "hit by an attack
roll"; Hellish Rebuke: "taking damage"), fired by itself whenever the trigger
occurs and the Reaction is available, re-armed at the owner's next turn start.
A reaction that answers a creature (Hellish Rebuke) resolves against the
creature whose act triggered it; a Self-range spell lands on the reactor.
"""

from __future__ import annotations

import asyncio
from datetime import date
from typing import Any

import pytest
from dnd5e_srd_data import Feat, FeatCategory, Provenance, ReviewState
from dnd5e_srd_data.loader import BundledAssetLoader
from dnd5e_srd_data.schema.common import (
    ActivationBlock,
    DamagePartBlock,
    HealActivity,
    ReactionCondition,
    ReactionTriggerKind,
)

from dnd5e_engine import (
    EncounterMemberSpec,
    GridScene,
    PartyMemberSpec,
    PlayerIntent,
    start_combat,
    submit_player_intent,
)
from dnd5e_engine.events import (
    AttackRolled,
    CastFailed,
    DamageApplied,
    HealingApplied,
    IntentSubmitted,
    ReactionTriggered,
    SaveRolled,
    SpellCast,
)
from dnd5e_engine.lib_loader import set_lib_loader_for_tests
from dnd5e_engine.orchestrator import _get_live, advance_monster_turn
from dnd5e_engine.spatial import cell_id as cell

BUNDLED = BundledAssetLoader()


def _second_wind_feat() -> Feat:
    """A feat-shaped reaction: "When you take damage, you can regain 1d8 Hit
    Points" — once per combat here (``uses.max`` 1)."""
    return Feat(
        slug="stubborn-heart",
        name="Stubborn Heart",
        description="Test feat.",
        category=FeatCategory.GENERAL,
        activities=[
            HealActivity(
                _id="stubbornheal",
                name="Shrug It Off",
                activation=ActivationBlock(
                    type="reaction",
                    condition="When you take damage",
                    reaction_conditions=[ReactionCondition(kind=ReactionTriggerKind.TAKES_DAMAGE)],
                ),
                healing=DamagePartBlock(number=1, denomination=8, types=["healing"]),
                uses={"max": "1", "spent": 0, "recovery": []},  # type: ignore[arg-type]
            )
        ],
        provenance=Provenance(
            source="homebrew",
            source_url="test://stubborn-heart",
            ingest_date=date(2026, 1, 1),
            ingest_version="test",
            license_tag="unspecified",
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
    set_lib_loader_for_tests(_WithFeat(_second_wind_feat()))
    yield
    set_lib_loader_for_tests(None)


def _events(live, kind):
    return [e for e in live.event_log if isinstance(e, kind)]


async def _start(pc: PartyMemberSpec, *, seed: int = 3):
    start = await start_combat(
        session_id="standing",
        party=[pc],
        encounter=[
            EncounterMemberSpec(
                entity_id="mon:foe",
                entity_type="Monster",
                name="goblin",
                initiative=1,
                hp_current=200,
                hp_max=200,
                ac=15,
                monster_template_slug="goblin-warrior",
                zone_id=cell(1, 0),
            )
        ],
        grid_scene=GridScene(width=10, height=10, cell_size_ft=5),
        rng_seed=seed,
    )
    return start.handle, _get_live(start.handle)


def _pc(**overrides: Any) -> PartyMemberSpec:
    fields: dict[str, Any] = dict(
        entity_id="char:pc",
        name="pc",
        initiative=20,
        hp_current=60,
        hp_max=60,
        ac=1,  # every goblin swing hits
        charisma=16,
        class_slug="warlock",
        character_level=5,
        zone_id=cell(0, 0),
    )
    fields.update(overrides)
    return PartyMemberSpec(**fields)


def test_an_armed_shield_fires_on_each_round_without_a_ready_action():
    """SRD 5.2 Shield: "which you take when you are hit by an attack roll" —
    armed once, it answers a hit in each of two rounds, re-armed at the
    wizard's turn start; the Action stays free (the wizard casts Fire Bolt)."""

    async def _go():
        handle, live = await _start(
            _pc(
                class_slug="wizard",
                intelligence=16,
                spells_known=["shield", "fire-bolt"],
                spell_slots={1: 2},
            )
        )
        await submit_player_intent(
            handle, actor_id="char:pc", intent=PlayerIntent(intent_type="arm", spell_id="shield")
        )
        assert live.current_actor_id == "char:pc"  # arming keeps the turn
        fire_bolt = PlayerIntent(
            intent_type="cast_spell", spell_id="fire-bolt", target_id="mon:foe"
        )
        await submit_player_intent(handle, actor_id="char:pc", intent=fire_bolt)
        await advance_monster_turn(handle)
        await submit_player_intent(handle, actor_id="char:pc", intent=fire_bolt)
        await advance_monster_turn(handle)
        return live

    live = asyncio.run(_go())
    shields = [r for r in _events(live, ReactionTriggered) if r.reaction_name == "shield"]
    assert len(shields) == 2
    assert live.spell_slots_by_entity["char:pc"] == {1: 0}
    assert len([s for s in _events(live, SpellCast) if s.spell_id == "shield"]) == 2
    # both of Shield's typed triggers are armed ("hit by an attack roll",
    # "targeted by the Magic Missile spell"), recorded and queued for the next round
    assert sorted(pr.trigger for pr in live.standing_reactions) == [
        "hit_by_attack",
        "targeted_by_magic_missile",
    ]
    assert any(pr.standing for pr in live.pending_reactions)


def test_an_armed_hellish_rebuke_answers_the_creature_that_damaged_the_warlock():
    """SRD 5.2 Hellish Rebuke: "which you take in response to taking damage
    from a creature that you can see" — the goblin that hit the warlock makes
    the Dexterity save and takes the fire."""

    async def _go():
        handle, live = await _start(_pc(spells_known=["hellish-rebuke"], spell_slots={1: 1}))
        await submit_player_intent(
            handle,
            actor_id="char:pc",
            intent=PlayerIntent(intent_type="arm", spell_id="hellish-rebuke"),
        )
        await submit_player_intent(
            handle, actor_id="char:pc", intent=PlayerIntent(intent_type="pass")
        )
        await advance_monster_turn(handle)
        return live

    live = asyncio.run(_go())
    goblin_hits = [
        a for a in _events(live, AttackRolled) if a.attacker_id == "mon:foe" and a.is_hit
    ]
    assert goblin_hits, "the seed must give the goblin a hit"
    [rebuke] = _events(live, ReactionTriggered)
    assert rebuke.reaction_name == "hellish-rebuke"
    [save] = _events(live, SaveRolled)
    assert (save.target_id, save.ability) == ("mon:foe", "dex")
    fire = [e for e in _events(live, DamageApplied) if e.damage_type == "fire"]
    assert [e.target_id for e in fire] == ["mon:foe"]
    assert live.spell_slots_by_entity["char:pc"] == {1: 0}
    # the rebuke follows the goblin's damage, inside the goblin's turn
    first_hit_damage = next(
        e for e in _events(live, DamageApplied) if e.target_id == "char:pc" and e.amount > 0
    )
    assert live.event_log.index(first_hit_damage) < live.event_log.index(rebuke)


def test_a_feat_reaction_fires_from_its_own_typed_trigger_and_spends_its_use():
    async def _go():
        handle, live = await _start(_pc(feats=("stubborn-heart",)))
        await submit_player_intent(
            handle,
            actor_id="char:pc",
            intent=PlayerIntent(intent_type="arm", feature_id="stubborn-heart"),
        )
        await submit_player_intent(
            handle, actor_id="char:pc", intent=PlayerIntent(intent_type="pass")
        )
        await advance_monster_turn(handle)
        await submit_player_intent(
            handle, actor_id="char:pc", intent=PlayerIntent(intent_type="pass")
        )
        await advance_monster_turn(handle)
        return live

    live = asyncio.run(_go())
    hits = [a for a in _events(live, AttackRolled) if a.attacker_id == "mon:foe" and a.is_hit]
    assert len(hits) >= 2
    [fired] = _events(live, ReactionTriggered)
    assert fired.reaction_name == "stubborn-heart"
    [heal] = _events(live, HealingApplied)
    assert heal.target_id == "char:pc"
    assert 1 <= heal.amount <= 8
    # one use: the second round's hit draws nothing, and the arm stays recorded
    assert [pr.feature_id for pr in live.standing_reactions] == ["stubborn-heart"]


def test_arming_something_with_no_trigger_is_refused_and_keeps_the_turn():
    async def _go():
        handle, live = await _start(_pc(spells_known=["fire-bolt"]))
        await submit_player_intent(
            handle, actor_id="char:pc", intent=PlayerIntent(intent_type="arm", spell_id="fire-bolt")
        )
        return live

    live = asyncio.run(_go())
    assert [e.intent_type for e in _events(live, IntentSubmitted)] == ["arm"]
    assert [e.reason for e in _events(live, CastFailed)] == ["activity_invalid"]
    assert live.current_actor_id == "char:pc"
    assert live.standing_reactions == []


def test_disarm_stands_the_reaction_down():
    async def _go():
        handle, live = await _start(
            _pc(class_slug="wizard", spells_known=["shield"], spell_slots={1: 1})
        )
        await submit_player_intent(
            handle, actor_id="char:pc", intent=PlayerIntent(intent_type="arm", spell_id="shield")
        )
        await submit_player_intent(
            handle, actor_id="char:pc", intent=PlayerIntent(intent_type="disarm", spell_id="shield")
        )
        await submit_player_intent(
            handle, actor_id="char:pc", intent=PlayerIntent(intent_type="pass")
        )
        await advance_monster_turn(handle)
        return live

    live = asyncio.run(_go())
    assert live.standing_reactions == []
    assert live.pending_reactions == []
    assert not _events(live, ReactionTriggered)
    assert live.spell_slots_by_entity["char:pc"] == {1: 1}


def _hit_by_attack_feat(slug: str) -> Feat:
    """A second feat reaction on the SAME trigger as Shield ("hit by an attack")."""
    return Feat(
        slug=slug,
        name=slug.replace("-", " ").title(),
        description="Test feat.",
        category=FeatCategory.GENERAL,
        activities=[
            HealActivity(
                _id=f"{slug[:8]}heal",
                name="Brace",
                activation=ActivationBlock(
                    type="reaction",
                    condition="When an attack hits you",
                    reaction_conditions=[ReactionCondition(kind=ReactionTriggerKind.HIT_BY_ATTACK)],
                ),
                healing=DamagePartBlock(number=1, denomination=4, types=["healing"]),
                uses={"max": "9", "spent": 0, "recovery": []},  # type: ignore[arg-type]
            )
        ],
        provenance=Provenance(
            source="homebrew",
            source_url=f"test://{slug}",
            ingest_date=date(2026, 1, 1),
            ingest_version="test",
            license_tag="unspecified",
        ),
        review=ReviewState(),
    )


class _WithFeats(_WithFeat):
    def __init__(self, *feats: Feat):
        self._feats = {f.slug: f for f in feats}

    def get_feat(self, slug: str):
        return self._feats.get(slug) or BUNDLED.get_feat(slug)


def test_a_reaction_can_be_armed_before_the_owners_first_turn():
    """Arming is free and bound to no turn: the wizard who lost initiative
    arms Shield while the goblin is up, and it answers the goblin's very
    first hit of the fight."""

    async def _go():
        handle, live = await _start(
            _pc(
                class_slug="wizard",
                intelligence=16,
                initiative=1,  # the goblin (initiative 1 too, listed first) acts first
                spells_known=["shield"],
                spell_slots={1: 1},
            )
        )
        goblin_first = live.current_actor_id == "mon:foe"
        await submit_player_intent(
            handle, actor_id="char:pc", intent=PlayerIntent(intent_type="arm", spell_id="shield")
        )
        assert live.current_actor_id == ("mon:foe" if goblin_first else "char:pc")  # no hand-off
        if goblin_first:
            await advance_monster_turn(handle)
        else:
            await submit_player_intent(
                handle, actor_id="char:pc", intent=PlayerIntent(intent_type="pass")
            )
            await advance_monster_turn(handle)
        return live, goblin_first

    live, goblin_first = asyncio.run(_go())
    assert goblin_first, "the seed must give the goblin the first turn"
    assert [r.reaction_name for r in _events(live, ReactionTriggered)] == ["shield"]
    assert live.spell_slots_by_entity["char:pc"] == {1: 0}


def test_arming_order_is_firing_priority_and_survives_a_fired_reaction():
    """Two armed reactions answer "hit by an attack". The first armed fires; the
    other stays queued. After the owner's turn start re-queues the fired one,
    the next hit still goes to the first armed, not to whichever was left over."""
    set_lib_loader_for_tests(
        _WithFeats(_hit_by_attack_feat("iron-brace"), _hit_by_attack_feat("oak-brace"))
    )

    async def _go():
        handle, live = await _start(_pc(feats=("iron-brace", "oak-brace")))
        for slug in ("oak-brace", "iron-brace"):  # oak first: oak has priority
            await submit_player_intent(
                handle, actor_id="char:pc", intent=PlayerIntent(intent_type="arm", feature_id=slug)
            )
        for _ in range(3):
            await submit_player_intent(
                handle, actor_id="char:pc", intent=PlayerIntent(intent_type="pass")
            )
            await advance_monster_turn(handle)
        return live

    live = asyncio.run(_go())
    hits = [a for a in _events(live, AttackRolled) if a.attacker_id == "mon:foe" and a.is_hit]
    fired = [r.reaction_name for r in _events(live, ReactionTriggered)]
    assert len(hits) >= 2, "the seed must give the goblin hits"
    assert fired, "a hit must draw a reaction"
    assert set(fired) == {"oak-brace"}, fired


def test_armed_reactions_on_the_spec_stand_from_the_first_moment_in_priority_order():
    """``PartyMemberSpec.armed_reactions`` seeds the standing reactions at
    start_combat: the wizard who lost initiative has Shield against the
    goblin's first hit without ever having had a turn, and the order listed is
    the firing priority (the feat named first answers the hit, Shield waits)."""
    set_lib_loader_for_tests(_WithFeats(_hit_by_attack_feat("iron-brace")))

    async def _go():
        handle, live = await _start(
            _pc(
                class_slug="wizard",
                intelligence=16,
                initiative=1,
                spells_known=["shield"],
                spell_slots={1: 1},
                feats=("iron-brace",),
                armed_reactions=(
                    "iron-brace",
                    "shield",
                    "fire-bolt",
                ),  # fire-bolt: skipped, no trigger
            )
        )
        assert live.current_actor_id == "mon:foe"
        await advance_monster_turn(handle)
        return live

    live = asyncio.run(_go())
    assert [pr.feature_id or pr.spell_id for pr in live.standing_reactions] == [
        "iron-brace",
        "shield",
        "shield",  # Shield's second typed trigger (Magic Missile)
    ]
    assert [r.reaction_name for r in _events(live, ReactionTriggered)] == ["iron-brace"]
    assert live.spell_slots_by_entity["char:pc"] == {1: 1}
