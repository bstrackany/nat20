"""Effects that end on an event or a turn boundary — ``PassiveEffect.ends_on``
and ``.until`` (midi-qol's DAE "special durations"), carried onto the runtime
effect's flags by ``activities/effects.py`` and ended by the orchestrator
(``_expire_on_events``, ``engine:effect-endings-turn-start/-end``).
"""

from __future__ import annotations

from typing import Any

import pytest
from dnd5e_srd_data import PassiveEffect, PassiveEffectChange

from dnd5e_engine.activities.effects import passive_effect_to_active_effect
from dnd5e_engine.effect_endings import end_events, ending_flags, is_ended, timed_duration
from dnd5e_engine.events import (
    AttackRolled,
    CheckRolled,
    ConcentrationCheck,
    ConditionRemoved,
    DamageApplied,
    Death,
    EffectApplied,
    EffectExpired,
    SaveRolled,
    SpellCast,
    TurnStarted,
    Unconscious,
)
from dnd5e_engine.lib_loader import set_lib_loader_for_tests
from dnd5e_engine.orchestrator import _emit, _expire_on_events, _LiveCombat
from tests.c20_support import act, events, monster_turn, pc, start
from tests.fork_support import BUNDLED, WithAssets, cast, caster, homebrew_feat

B = "char:caster"  # the bearer


@pytest.fixture(autouse=True)
def _bundled_loader():
    set_lib_loader_for_tests(BUNDLED)
    yield
    set_lib_loader_for_tests(None)


def _attack(attacker: str, target: str) -> AttackRolled:
    return AttackRolled(
        attacker_id=attacker,
        target_id=target,
        roll_total=15,
        advantage="normal",
        is_crit=False,
        is_hit=True,
    )


def _damage(target: str, amount: int = 4) -> DamageApplied:
    return DamageApplied(target_id=target, amount=amount, damage_type="fire", is_overkill=False)


def _save(target: str) -> SaveRolled:
    return SaveRolled(target_id=target, ability="dex", dc=13, roll_total=9, succeeded=False)


def _cast(actor: str) -> SpellCast:
    return SpellCast(
        actor_id=actor,
        spell_id="x",
        slot_level=1,
        ritual=False,
        components=[],
        material=None,
        material_consumed=False,
        material_cost_gp=0,
    )


# ── reading the stream ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("end", "ends", "does_not", "dealer"),
    [
        ("attacks", _attack(B, "mon:foe"), _attack("mon:foe", B), None),
        ("is_attacked", _attack("mon:foe", B), _attack(B, "mon:foe"), None),
        ("saves", _save(B), _save("mon:foe"), None),
        (
            "saves",
            ConcentrationCheck(target_id=B, dc=10, roll_total=12, succeeded=True),
            _attack(B, "mon:foe"),
            None,
        ),
        (
            "checks",
            CheckRolled(
                actor_id=B, ability="str", skill=None, dc=None, roll_total=11, succeeded=None
            ),
            _save(B),
            None,
        ),
        ("takes_damage", _damage(B), _damage(B, amount=0), None),
        ("deals_damage", _damage("mon:foe"), _damage(B), B),
        ("zero_hp", Unconscious(target_id=B), Unconscious(target_id="mon:foe"), None),
        ("zero_hp", Death(target_id=B, reason="damage"), _damage(B), None),
        ("casts_spell", _cast(B), _cast("mon:foe"), None),
    ],
)
def test_each_end_event_is_read_from_its_event(end: str, ends: Any, does_not: Any, dealer: Any):
    identity = (B, "effect:x", "cast:x:char:caster")
    assert end in end_events(ends, B, dealer)
    assert end not in end_events(ends, "char:someone", dealer)
    assert is_ended([end], identity, [ends], dealer)
    assert not is_ended([end], identity, [does_not], dealer)


def test_only_events_after_the_effect_landed_end_it():
    """An "until you next attack" effect granted by the attack's own resolution
    does not end on that attack; the next one ends it."""
    effect = passive_effect_to_active_effect(
        PassiveEffect(_id="x", name="X", ends_on=["attacks"]), target_id=B, caster_id=B
    )
    identity = (B, effect.id, effect.origin)
    landed = [_attack(B, "mon:foe"), EffectApplied(effect=effect), _damage("mon:foe")]
    assert not is_ended(["attacks"], identity, landed)
    assert is_ended(["attacks"], identity, [*landed, _attack(B, "mon:foe")])


@pytest.mark.parametrize(
    ("until", "flags"),
    [
        ("", {}),
        ("start_of_next_turn", {"until_start_of_next_turn_of": "char:origin"}),
        ("end_of_next_turn", {"until_end_of_next_turn_of": "char:origin"}),
        ("end_of_turn", {"until_end_of_turn": True}),
    ],
)
def test_until_is_keyed_to_the_originator_and_replaces_the_timed_duration(until: str, flags: Any):
    pe = PassiveEffect(_id="x", name="X", duration={"rounds": 1}, until=until)  # type: ignore[arg-type]
    effect = passive_effect_to_active_effect(pe, target_id=B, caster_id="char:origin")
    assert effect.flags == flags
    assert (effect.duration.rounds is None) == bool(until)
    assert timed_duration(pe) == (None if until else {"rounds": 1})
    assert ending_flags(PassiveEffect(_id="y", ends_on=["saves"]), "c") == {"ends_on": ["saves"]}


# ── in combat ────────────────────────────────────────────────────────────────


def test_invisibility_ends_when_its_bearer_attacks_not_when_it_is_cast_or_attacked():
    """SRD 5.2 Invisibility: "The spell ends early immediately after the target
    makes an attack roll, deals damage, or casts a spell." """
    handle, live = start([caster(["invisibility"], {2: 1})], seed=1)
    cast(handle, "invisibility", target_id=B)
    [invisible] = events(live, EffectApplied)
    assert invisible.effect.flags["ends_on"] == ["attacks", "deals_damage", "casts_spell"]
    monster_turn(handle)  # the foe's turn: being attacked is not in the list
    assert not events(live, EffectExpired)
    act(handle, B, intent_type="attack", weapon_id="dagger", target_id="mon:foe")
    [ended] = events(live, EffectExpired)
    assert (ended.effect_id, ended.target_id, ended.reason) == (
        invisible.effect.id,
        B,
        "duration",
    )
    [swing] = [a for a in events(live, AttackRolled) if a.attacker_id == B]
    assert live.event_log.index(swing) < live.event_log.index(ended)
    assert "invisible" not in live.active_conditions.get(B, set())


def test_guiding_bolts_advantage_is_spent_by_the_next_attack_against_its_target():
    """SRD 5.2 Guiding Bolt: "the next attack roll made against it before the end
    of your next turn has Advantage" — it outlasts the caster's own turn."""
    hero = pc("char:hero", initiative=10, zone_id="0,1", strength=18, attack_bonus=10)
    for seed in range(1, 10):
        handle, live = start([caster(["guiding-bolt"], {1: 1}), hero], seed=seed)
        cast(handle, "guiding-bolt", target_id="mon:foe", slot_level=1)
        marked = [e for e in events(live, EffectApplied) if e.effect.name == "Guiding Bolt"]
        if marked:
            break
    else:
        pytest.fail("no seed hit with the bolt")
    assert marked[0].effect.flags == {
        "ends_on": ["is_attacked"],
        "until_end_of_next_turn_of": B,
    }
    assert not events(live, EffectExpired)  # the caster's turn ended; the mark stands
    act(handle, "char:hero", intent_type="attack", weapon_id="longsword", target_id="mon:foe")
    [swing] = [a for a in events(live, AttackRolled) if a.attacker_id == "char:hero"]
    assert "grant:target" in swing.advantage_sources
    [ended] = events(live, EffectExpired)
    assert ended.effect_id == marked[0].effect.id
    assert live.event_log.index(swing) < live.event_log.index(ended)


def _apply(live: _LiveCombat, flags: dict[str, Any], bearer: str = B) -> tuple[str, str, str]:
    """Land an effect now (as a reaction would, on whoever's turn it is)."""
    effect = passive_effect_to_active_effect(
        PassiveEffect(_id="x", name="Marked", statuses=["charmed"]),
        target_id=bearer,
        caster_id=B,
    ).model_copy(update={"flags": flags})
    _emit(live, EffectApplied(effect=effect))
    return (bearer, effect.id, effect.origin)


def _ended_at(live: _LiveCombat, identity: tuple[str, str, str]) -> int | None:
    return next(
        (
            i
            for i, ev in enumerate(live.event_log)
            if isinstance(ev, EffectExpired) and (ev.target_id, ev.effect_id, ev.origin) == identity
        ),
        None,
    )


def _turn_starts(live: _LiveCombat, actor_id: str) -> list[int]:
    return [
        i
        for i, ev in enumerate(live.event_log)
        if isinstance(ev, TurnStarted) and ev.actor_id == actor_id
    ]


@pytest.mark.parametrize("on_own_turn", [True, False])
def test_until_the_start_of_your_next_turn(on_own_turn: bool):
    """SRD 5.2 Shield: "Until the start of your next turn" — applied on the
    caster's own turn or as its reaction on the foe's, it ends as the caster's
    next turn starts, not before."""
    handle, live = start([caster([], {})], seed=1)
    if not on_own_turn:
        act(handle, B, intent_type="pass")
    identity = _apply(live, {"until_start_of_next_turn_of": B}, bearer="mon:foe")
    if on_own_turn:
        act(handle, B, intent_type="pass")
    assert _ended_at(live, identity) is None
    monster_turn(handle)
    ended = _ended_at(live, identity)
    assert ended is not None
    assert ended > _turn_starts(live, B)[-1]
    assert "charmed" not in live.active_conditions.get("mon:foe", set())


def test_until_the_end_of_the_current_turn_is_whoevers_turn_it_is():
    """SRD 5.2 Stinking Cloud: "until the end of the current turn"."""
    handle, live = start([caster([], {})], seed=1)
    own = _apply(live, {"until_end_of_turn": True})
    act(handle, B, intent_type="pass")
    assert _ended_at(live, own) is not None
    on_foes = _apply(live, {"until_end_of_turn": True}, bearer="mon:foe")
    assert _ended_at(live, on_foes) is None
    monster_turn(handle)
    assert _ended_at(live, on_foes) is not None


def test_a_save_rolled_outside_a_resolution_ends_the_effect_at_the_turns_end():
    handle, live = start([caster([], {})], seed=1)
    identity = _apply(live, {"ends_on": ["saves"]})
    _emit(live, _save(B))  # an end-of-turn repeat save, a turn-start trigger's save
    assert _ended_at(live, identity) is None
    act(handle, B, intent_type="pass")
    assert _ended_at(live, identity) is not None


def test_ending_one_target_of_a_concentration_spell_keeps_the_others():
    """SRD 5.2 Hypnotic Pattern: "The spell ends for an affected creature if it
    takes any damage" — the caster keeps concentrating on the rest."""
    _handle, live = start([caster([], {})], seed=1)
    hit = _apply(live, {"ends_on": ["takes_damage"], "concentration": True}, bearer="mon:foe")
    other = ("mon:other", hit[1], hit[2])
    live.concentration_chain[B] = [hit, other]
    live.conditions_by_effect[hit] = ["charmed"]
    mark = len(live.event_log)
    _emit(live, _damage("mon:foe"))
    _expire_on_events(live, mark)
    assert _ended_at(live, hit) is not None
    assert ConditionRemoved(target_id="mon:foe", condition="charmed") in live.event_log
    assert live.concentration_chain[B] == [other]
    assert hit not in live.conditions_by_effect


def test_a_feats_always_on_effect_never_carries_an_ending():
    feat = homebrew_feat(
        "steady",
        passive_effects=[
            PassiveEffect(
                _id="steadypassive",
                name="Steady",
                changes=[
                    PassiveEffectChange(
                        key="system.abilities.wis.save.roll.mode", mode=2, value="1"
                    )
                ],
                ends_on=["saves"],
                until="start_of_next_turn",
            )
        ],
    )
    set_lib_loader_for_tests(WithAssets(feat))
    _handle, live = start([pc("char:ftr", feats=("steady",))], seed=1)
    [seeded] = live.active_effects["char:ftr"]
    assert seeded.flags == {"always_on": True}
