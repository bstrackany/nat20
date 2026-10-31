"""Reactions armed from data, for the whole combat.

An ``"arm"`` intent keeps a spell, feature or feat reaction armed: no Action, the
trigger read from the reaction's own typed condition (Shield: "hit by an attack
roll"; Hellish Rebuke: "taking damage"), fired whenever the trigger occurs and
the Reaction is available, re-armed at the owner's next turn start.
"""

from __future__ import annotations

from typing import Any

import pytest
from dnd5e_srd_data import Feat
from dnd5e_srd_data.schema.common import (
    ActivationBlock,
    DamagePartBlock,
    HealActivity,
    ReactionCondition,
    ReactionTriggerKind,
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
from tests.c20_support import act, events, monster_turn, pc, start
from tests.fork_support import WithAssets, cast, goblin, homebrew_feat


def _reaction_feat(slug: str, kind: ReactionTriggerKind, *, die: int, uses: int) -> Feat:
    """A feat whose reaction heals 1d``die`` on ``kind``, ``uses`` times a combat."""
    heal = HealActivity(
        _id=f"{slug[:8]}heal",
        name="Brace",
        activation=ActivationBlock(
            type="reaction", reaction_conditions=[ReactionCondition(kind=kind)]
        ),
        healing=DamagePartBlock(number=1, denomination=die, types=["healing"]),
        uses={"max": str(uses), "spent": 0, "recovery": []},  # type: ignore[arg-type]
    )
    return homebrew_feat(slug, activities=[heal])


@pytest.fixture(autouse=True)
def _loader():
    stubborn = _reaction_feat("stubborn-heart", ReactionTriggerKind.TAKES_DAMAGE, die=8, uses=1)
    braces = [
        _reaction_feat(slug, ReactionTriggerKind.HIT_BY_ATTACK, die=4, uses=9)
        for slug in ("iron-brace", "oak-brace")
    ]
    set_lib_loader_for_tests(WithAssets(stubborn, *braces))
    yield
    set_lib_loader_for_tests(None)


def _start(*, wizard: bool = False, **fields: Any):
    """A warlock 5 (or a wizard 5 with Shield) with AC 1 — every goblin swing hits."""
    base: dict[str, Any] = {"hp_current": 60, "hp_max": 60, "ac": 1, "charisma": 16}
    base |= {"class_slug": "warlock", "character_level": 5}
    if wizard:
        base |= {"class_slug": "wizard", "intelligence": 16, "spells_known": ["shield"]}
    return start([pc("char:pc", **(base | fields))], encounter=[goblin()], seed=3)


def _arm(handle, intent_type: str = "arm", **fields: Any) -> None:
    act(handle, "char:pc", intent_type=intent_type, **fields)


def _rounds(handle, n: int) -> None:
    for _ in range(n):
        act(handle, "char:pc", intent_type="pass")
        monster_turn(handle)


def _goblin_hits(live) -> list[AttackRolled]:
    return [a for a in events(live, AttackRolled) if a.attacker_id == "mon:foe" and a.is_hit]


def test_an_armed_shield_fires_on_each_round_without_a_ready_action():
    """SRD 5.2 Shield: "which you take when you are hit by an attack roll" —
    armed once, it answers a hit in each of two rounds, re-armed at the
    wizard's turn start; the Action stays free (the wizard casts Fire Bolt)."""
    handle, live = _start(wizard=True, spells_known=["shield", "fire-bolt"], spell_slots={1: 2})
    _arm(handle, spell_id="shield")
    assert live.current_actor_id == "char:pc"  # arming keeps the turn
    for _ in range(2):
        cast(handle, "fire-bolt", actor_id="char:pc", target_id="mon:foe")
        monster_turn(handle)

    shields = [r for r in events(live, ReactionTriggered) if r.reaction_name == "shield"]
    assert len(shields) == 2
    assert live.spell_slots_by_entity["char:pc"] == {1: 0}
    assert len([s for s in events(live, SpellCast) if s.spell_id == "shield"]) == 2
    # both of Shield's typed triggers are armed, recorded and queued for the next round
    assert sorted(pr.trigger for pr in live.standing_reactions) == [
        "hit_by_attack",
        "targeted_by_magic_missile",
    ]
    assert any(pr.standing for pr in live.pending_reactions)


def test_an_armed_hellish_rebuke_answers_the_creature_that_damaged_the_warlock():
    """SRD 5.2 Hellish Rebuke: "which you take in response to taking damage
    from a creature that you can see" — the goblin that hit the warlock makes
    the Dexterity save and takes the fire."""
    handle, live = _start(spells_known=["hellish-rebuke"], spell_slots={1: 1})
    _arm(handle, spell_id="hellish-rebuke")
    _rounds(handle, 1)

    assert _goblin_hits(live), "the seed must give the goblin a hit"
    [rebuke] = events(live, ReactionTriggered)
    assert rebuke.reaction_name == "hellish-rebuke"
    [save] = events(live, SaveRolled)
    assert (save.target_id, save.ability) == ("mon:foe", "dex")
    fire = [e for e in events(live, DamageApplied) if e.damage_type == "fire"]
    assert [e.target_id for e in fire] == ["mon:foe"]
    assert live.spell_slots_by_entity["char:pc"] == {1: 0}
    # the rebuke follows the goblin's damage, inside the goblin's turn
    first_hit_damage = next(
        e for e in events(live, DamageApplied) if e.target_id == "char:pc" and e.amount > 0
    )
    assert live.event_log.index(first_hit_damage) < live.event_log.index(rebuke)


def test_a_feat_reaction_fires_from_its_own_typed_trigger_and_spends_its_use():
    handle, live = _start(feats=("stubborn-heart",))
    _arm(handle, feature_id="stubborn-heart")
    _rounds(handle, 2)

    assert len(_goblin_hits(live)) >= 2
    [fired] = events(live, ReactionTriggered)
    assert fired.reaction_name == "stubborn-heart"
    [heal] = events(live, HealingApplied)
    assert heal.target_id == "char:pc"
    assert 1 <= heal.amount <= 8
    # one use: the second round's hit draws nothing, and the arm stays recorded
    assert [pr.feature_id for pr in live.standing_reactions] == ["stubborn-heart"]


def test_arming_something_with_no_trigger_is_refused_and_keeps_the_turn():
    handle, live = _start(spells_known=["fire-bolt"])
    _arm(handle, spell_id="fire-bolt")
    assert [e.intent_type for e in events(live, IntentSubmitted)] == ["arm"]
    assert [e.reason for e in events(live, CastFailed)] == ["activity_invalid"]
    assert live.current_actor_id == "char:pc"
    assert live.standing_reactions == []


def test_disarm_stands_the_reaction_down():
    handle, live = _start(wizard=True, spell_slots={1: 1})
    _arm(handle, spell_id="shield")
    _arm(handle, "disarm", spell_id="shield")
    _rounds(handle, 1)
    assert live.standing_reactions == []
    assert live.pending_reactions == []
    assert not events(live, ReactionTriggered)
    assert live.spell_slots_by_entity["char:pc"] == {1: 1}


def test_a_reaction_can_be_armed_before_the_owners_first_turn():
    """Arming is free and bound to no turn: the wizard who lost initiative
    arms Shield while the goblin is up, and it answers the goblin's first hit."""
    handle, live = _start(wizard=True, initiative=1, spell_slots={1: 1})
    assert live.current_actor_id == "mon:foe", "the seed must give the goblin the first turn"
    _arm(handle, spell_id="shield")
    assert live.current_actor_id == "mon:foe"  # no hand-off
    monster_turn(handle)
    assert [r.reaction_name for r in events(live, ReactionTriggered)] == ["shield"]
    assert live.spell_slots_by_entity["char:pc"] == {1: 0}


def test_arming_order_is_firing_priority_and_survives_a_fired_reaction():
    """Two armed reactions answer "hit by an attack". The first armed fires; the
    other stays queued. After the owner's turn start re-queues the fired one,
    the next hit still goes to the first armed, not to whichever was left over."""
    handle, live = _start(feats=("iron-brace", "oak-brace"))
    for slug in ("oak-brace", "iron-brace"):  # oak first: oak has priority
        _arm(handle, feature_id=slug)
    _rounds(handle, 3)
    fired = [r.reaction_name for r in events(live, ReactionTriggered)]
    assert len(_goblin_hits(live)) >= 2, "the seed must give the goblin hits"
    assert fired, "a hit must draw a reaction"
    assert set(fired) == {"oak-brace"}, fired


def test_armed_reactions_on_the_spec_stand_from_the_first_moment_in_priority_order():
    """``PartyMemberSpec.armed_reactions`` seeds the standing reactions at
    start_combat, in firing priority (the feat named first answers the hit,
    Shield waits); a name with no trigger (Fire Bolt) is skipped."""
    handle, live = _start(
        wizard=True,
        initiative=1,
        spell_slots={1: 1},
        feats=("iron-brace",),
        armed_reactions=("iron-brace", "shield", "fire-bolt"),
    )
    assert live.current_actor_id == "mon:foe"
    monster_turn(handle)
    assert [pr.feature_id or pr.spell_id for pr in live.standing_reactions] == [
        "iron-brace",
        "shield",
        "shield",  # Shield's second typed trigger (Magic Missile)
    ]
    assert [r.reaction_name for r in events(live, ReactionTriggered)] == ["iron-brace"]
    assert live.spell_slots_by_entity["char:pc"] == {1: 1}
