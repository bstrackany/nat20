"""When an activity fires — ``Activity.trigger`` and ``PlayerIntent.activity_id``
on a cast. The corpus marks an activity SRD 5.2 puts after the cast (Acid
Arrow's "2d4 Acid damage at the end of its next turn"); the engine holds the use
(``dnd5e_engine.triggers.HeldUse``) and fires the activity at its moment.
"""

from __future__ import annotations

import pytest
from dnd5e_srd_data import ActivityTrigger
from dnd5e_srd_data.schema.common import (
    DamageActivity,
    DamageActivityDamageBlock,
    DamagePartBlock,
)

from dnd5e_engine.events import (
    ActivityTriggered,
    AttackRolled,
    CastFailed,
    DamageApplied,
    EffectApplied,
    EffectExpired,
    SaveRolled,
    SpellCast,
)
from dnd5e_engine.lib_loader import set_lib_loader_for_tests
from dnd5e_engine.spatial import cell_id as cell
from dnd5e_engine.triggers import (
    HeldUse,
    bearers_of,
    fires_with_use,
    is_later_use,
    triggered_activities,
    use_is_live,
)
from tests.c20_support import act, events, foe, monster_turn, start
from tests.fork_support import BUNDLED, WithAssets, cast, caster, goblin

ACID_ARROW_LINGER = "uAyE5DrJp0YpElcw"  # "Lingering Acid Damage"
SUNBEAM_INITIAL = "o3kffaumfNGjVvg9"  # "Initial Cast"
SUNBEAM_NEW_LINE = "Y0cJvZuD7EfwGqkf"  # "Create New Sunbeam"


@pytest.fixture(autouse=True)
def _bundled_loader():
    set_lib_loader_for_tests(BUNDLED)
    yield
    set_lib_loader_for_tests(None)


# ── the pure record ──────────────────────────────────────────────────────────


def test_an_activity_without_a_trigger_fires_with_the_use():
    acid = BUNDLED.get_spell("acid-arrow")
    attack, linger = acid.activities
    assert fires_with_use(attack)
    assert not fires_with_use(linger)
    assert triggered_activities(acid.activities) == [linger]
    sunbeam = BUNDLED.get_spell("sunbeam")
    assert [is_later_use(a) for a in sunbeam.activities] == [False, True]
    call = BUNDLED.get_spell("call-lightning")
    assert [fires_with_use(a) for a in call.activities] == [True, False]  # also_at_cast


def test_a_use_lives_on_its_effects_or_its_rounds():
    use = HeldUse("char:c", "spell", "x", "X", 1, 1, "int", [], [])
    assert not use_is_live(use, {})
    use.rounds_remaining = 2
    assert use_is_live(use, {})
    use.effects = {("mon:a", "effect:x", "cast:x:char:c"), ("mon:b", "effect:x", "cast:x:char:c")}
    use.rounds_remaining = None

    class _Eff:
        def __init__(self, id, origin):
            self.id, self.origin = id, origin

    present = {"mon:b": [_Eff("effect:x", "cast:x:char:c")], "mon:a": [_Eff("effect:y", "o")]}
    assert bearers_of(use, present) == ["mon:b"]
    assert use_is_live(use, present)
    assert not use_is_live(use, {"mon:a": [_Eff("effect:y", "o")]})


# ── naming the activity a cast resolves ──────────────────────────────────────


def test_a_cast_naming_an_activity_resolves_only_that_one():
    handle, live = start([caster(["sunbeam"], {6: 1})], seed=1)
    cast(handle, "sunbeam", target_id="mon:foe", activity_id=SUNBEAM_INITIAL)
    assert len(events(live, SaveRolled)) == 1
    assert live.spell_slots_by_entity["char:caster"] == {6: 0}


@pytest.mark.parametrize(
    ("spell_id", "activity_id"),
    [
        ("acid-arrow", "not-an-activity"),
        ("acid-arrow", ACID_ARROW_LINGER),  # fires at the end of the target's turn, never by name
        ("sunbeam", SUNBEAM_NEW_LINE),  # a later_use line: re-firing by name is not supported
    ],
)
def test_an_activity_the_caster_cannot_use_now_is_refused_before_the_slot_is_spent(
    spell_id: str, activity_id: str
):
    handle, live = start([caster([spell_id], {2: 1, 6: 1})], seed=1)
    cast(handle, spell_id, target_id="mon:foe", activity_id=activity_id)
    assert [e.reason for e in events(live, CastFailed)] == ["activity_invalid"]
    assert live.spell_slots_by_entity["char:caster"] == {2: 1, 6: 1}
    assert not events(live, SpellCast)


# ── turn boundaries ──────────────────────────────────────────────────────────


def test_acid_arrow_burns_again_at_the_end_of_the_targets_next_turn_and_then_stops():
    """SRD 5.2 Acid Arrow: "On a hit, the target takes 4d4 Acid damage and 2d4
    Acid damage at the end of its next turn." """
    handle, live = start([caster(["acid-arrow"], {2: 1})], seed=1)
    cast(handle, "acid-arrow", target_id="mon:foe")
    after_cast = len(live.event_log)
    monster_turn(handle)  # the foe's turn: the acid lingers at its end
    after_foe = len(live.event_log)
    act(handle, "char:caster", intent_type="pass")
    monster_turn(handle)  # the foe's second turn: nothing left to fire

    [hit] = [a for a in events(live, AttackRolled) if a.attacker_id == "char:caster"]
    assert hit.is_hit
    [linger] = events(live, EffectApplied)
    assert linger.effect.name == "Lingering Acid"
    foe_turn = live.event_log[after_cast:after_foe]
    [fired] = [e for e in foe_turn if isinstance(e, ActivityTriggered)]
    assert fired.model_dump() == {
        "type": "activity_triggered",
        "actor_id": "char:caster",
        "source_id": "acid-arrow",
        "source_kind": "spell",
        "activity_id": ACID_ARROW_LINGER,
        "activity_name": "Lingering Acid Damage",
        "trigger": "turn_end",
        "subject_id": "mon:foe",
        "target_id": "mon:foe",
    }
    [later] = [e for e in foe_turn if isinstance(e, DamageApplied) and e.target_id == "mon:foe"]
    assert later.damage_type == "acid"
    assert 2 <= later.amount <= 8
    # the lingering acid is spent: its effect ends with it, and the use is gone
    [ended] = [e for e in foe_turn if isinstance(e, EffectExpired)]
    assert (ended.effect_id, ended.reason) == (linger.effect.id, "expended")
    assert live.event_log.index(fired) < live.event_log.index(later) < live.event_log.index(ended)
    assert len(events(live, ActivityTriggered)) == 1
    # the 4d4 at the cast, the 2d4 at the foe's turn end
    assert len([e for e in events(live, DamageApplied) if e.damage_type == "acid"]) == 2
    assert live.held_uses == []


def test_searing_smite_burns_at_the_start_of_each_of_the_targets_turns_until_it_saves():
    """SRD 5.2 Searing Smite: "At the start of each of its turns until the
    spell ends, the target takes 1d6 Fire damage and then makes a Constitution
    saving throw. On a failed save, the spell continues. On a successful save,
    the spell ends." """
    outcomes = set()
    for seed in range(1, 9):
        handle, live = start([caster(["searing-smite"], {1: 1})], seed=seed)
        cast(handle, "searing-smite", target_id="mon:foe")
        act(handle, "char:caster", intent_type="pass")
        monster_turn(handle)
        [seared] = events(live, EffectApplied)
        assert seared.effect.name == "Seared"
        [fired] = events(live, ActivityTriggered)
        assert (fired.trigger, fired.subject_id, fired.activity_name) == (
            "turn_start",
            "mon:foe",
            "Start of Turn Save",
        )
        [save] = events(live, SaveRolled)
        assert (save.ability, save.target_id) == ("con", "mon:foe")
        burns = [e for e in events(live, DamageApplied) if e.damage_type == "fire"]
        assert len(burns) == 2  # "As you hit the target, it takes an extra 1d6", then the turn's
        ended = events(live, EffectExpired)
        if save.succeeded:
            assert [e.effect_id for e in ended] == [seared.effect.id]
            assert live.held_uses == []
        else:
            assert ended == []
            assert len(live.held_uses) == 1
        outcomes.add(save.succeeded)
    assert outcomes == {True, False}


# ── hits ─────────────────────────────────────────────────────────────────────


def test_hunters_mark_adds_its_damage_when_the_caster_hits_the_marked_creature_only():
    """SRD 5.2 Hunter's Mark: "you deal an extra [[/damage]] damage to the
    target whenever you hit it with an attack roll." """
    handle, live = start(
        [caster(["hunters-mark"], {1: 1}, class_slug="ranger")],
        encounter=[foe(entity_id="mon:marked"), foe(entity_id="mon:other", zone_id=cell(0, 1))],
        seed=2,  # every swing hits
    )
    cast(handle, "hunters-mark", target_id="mon:marked")  # a Bonus Action: the turn goes on

    def swing(target_id: str) -> None:
        act(handle, "char:caster", intent_type="attack", weapon_id="longsword", target_id=target_id)

    swing("mon:other")
    swing("mon:marked")
    act(handle, "char:caster", intent_type="pass")
    monster_turn(handle)
    monster_turn(handle)
    # keeps the turn; the mark ends with the concentration
    act(handle, "char:caster", intent_type="drop_concentration")
    swing("mon:marked")

    hits = [h for h in events(live, AttackRolled) if h.attacker_id == "char:caster"]
    assert [h.is_hit for h in hits] == [True, True, True]
    assert [h.attack_type for h in hits] == ["melee", "melee", "melee"]
    [fired] = events(live, ActivityTriggered)
    assert (fired.trigger, fired.subject_id, fired.target_id) == (
        "hits",
        "char:caster",
        "mon:marked",
    )
    force = [e for e in events(live, DamageApplied) if e.damage_type == "force"]
    assert [e.target_id for e in force] == ["mon:marked"]
    # the mark's damage comes after the hit's own
    assert live.event_log.index(hits[1]) < live.event_log.index(force[0])
    # concentration dropped: the mark is gone, and the third hit adds nothing
    assert any(e.reason == "concentration_drop" for e in events(live, EffectExpired))
    assert live.held_uses == []


# ── a trigger a consumer's own data sets ─────────────────────────────────────


def test_a_hit_on_the_bearer_fires_the_held_activity_against_the_attacker():
    """A self-cast answering each melee hit on its bearer with 2d8 Cold damage
    to the attacker — Armor of Agathys's shape (not SRD), on Fire Shield's cast."""
    fire_shield = BUNDLED.get_spell("fire-shield")
    retaliate = DamageActivity(
        _id="retaliate",
        name="Frost Lash",
        damage=DamageActivityDamageBlock(
            parts=[DamagePartBlock(number=2, denomination=8, types=["cold"])]
        ),
        trigger=ActivityTrigger(kind="hit_by_attack", attack="melee"),
    )
    frost_armor = fire_shield.model_copy(
        update={"slug": "frost-armor", "activities": [*fire_shield.activities, retaliate]}
    )
    set_lib_loader_for_tests(WithAssets(frost_armor))
    handle, live = start(
        [caster(["frost-armor"], {4: 1}, hp_current=80, hp_max=80)],
        encounter=[goblin()],
        seed=5,
    )
    cast(handle, "frost-armor", target_id="char:caster")
    monster_turn(handle)  # the goblin swings at the caster

    hits = [h for h in events(live, AttackRolled) if h.attacker_id == "mon:foe" and h.is_hit]
    assert hits, "the seed must give the goblin a hit"
    fired = events(live, ActivityTriggered)
    assert len(fired) == len(hits)
    assert {(f.trigger, f.subject_id, f.target_id) for f in fired} == {
        ("hit_by_attack", "char:caster", "mon:foe")
    }
    cold = [e for e in events(live, DamageApplied) if e.damage_type == "cold"]
    assert [e.target_id for e in cold] == ["mon:foe"] * len(hits)
