"""When an activity fires — ``Activity.trigger`` and ``PlayerIntent.activity_id``
on a cast.

SRD 5.2 puts part of many spells after the cast: Acid Arrow's "2d4 Acid damage
at the end of its next turn", Hunter's Mark's "extra damage to the target
whenever you hit it with an attack roll", Sunbeam's "you can take a Magic
action to create a new Line of radiance", Searing Smite's "At the start of each
of its turns until the spell ends". Each such activity carries a trigger in the
corpus; the engine holds the use (``dnd5e_engine.triggers.HeldUse``) and fires
the activity at its moment. A cast that names an ``activity_id`` resolves only
that activity, as a ``use_feature`` already does.
"""

from __future__ import annotations

import asyncio
import typing
from typing import Any

import pytest
from dnd5e_srd_data import ActivityTrigger
from dnd5e_srd_data.loader import BundledAssetLoader
from dnd5e_srd_data.schema.common import (
    DamageActivity,
    DamageActivityDamageBlock,
    DamagePartBlock,
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
    ActivityTriggered,
    AttackRolled,
    CastFailed,
    CastFailedReason,
    DamageApplied,
    EffectApplied,
    EffectExpired,
    SaveRolled,
    SpellCast,
)
from dnd5e_engine.lib_loader import set_lib_loader_for_tests
from dnd5e_engine.orchestrator import _get_live, advance_monster_turn
from dnd5e_engine.spatial import cell_id as cell
from dnd5e_engine.triggers import (
    HeldUse,
    bearers_of,
    fires_with_use,
    is_later_use,
    triggered_activities,
    use_is_live,
)

ACID_ARROW_ATTACK = "UvDr2eESFyDiTwTX"  # "Cast and Attack"
ACID_ARROW_LINGER = "uAyE5DrJp0YpElcw"  # "Lingering Acid Damage"
SUNBEAM_INITIAL = "o3kffaumfNGjVvg9"  # "Initial Cast"
SUNBEAM_NEW_LINE = "Y0cJvZuD7EfwGqkf"  # "Create New Sunbeam"
BUNDLED = BundledAssetLoader()


@pytest.fixture(autouse=True)
def _bundled_loader():
    set_lib_loader_for_tests(BUNDLED)
    yield
    set_lib_loader_for_tests(None)


def _events(live, kind):
    return [e for e in live.event_log if isinstance(e, kind)]


def _caster(spells: list[str], slots: dict[int, int], **overrides: Any) -> PartyMemberSpec:
    fields: dict[str, Any] = dict(
        entity_id="char:caster",
        name="caster",
        initiative=20,
        hp_current=60,
        hp_max=60,
        intelligence=16,
        wisdom=16,
        strength=16,
        attack_bonus=7,
        class_slug="wizard",
        character_level=11,
        spells_known=spells,
        spell_slots=slots,
        zone_id=cell(0, 0),
    )
    fields.update(overrides)
    return PartyMemberSpec(**fields)


def _foe(entity_id: str = "mon:foe", col: int = 1, **overrides: Any) -> EncounterMemberSpec:
    fields: dict[str, Any] = dict(
        entity_id=entity_id,
        entity_type="Monster",
        name=entity_id,
        initiative=1,
        hp_current=200,
        hp_max=200,
        ac=1,
        zone_id=cell(col, 0),
    )
    fields.update(overrides)
    return EncounterMemberSpec(**fields)


async def _start(party, encounter, *, seed: int = 1):
    start = await start_combat(
        session_id="activity-triggers",
        party=party,
        encounter=encounter,
        grid_scene=GridScene(width=10, height=10, cell_size_ft=5),
        rng_seed=seed,
    )
    return start.handle, _get_live(start.handle)


def _cast(spell_id: str, **fields: Any) -> PlayerIntent:
    return PlayerIntent(intent_type="cast_spell", spell_id=spell_id, **fields)


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


def test_a_cast_naming_no_activity_resolves_every_activity_that_fires_at_cast():
    async def _go():
        handle, live = await _start([_caster(["acid-arrow"], {2: 1})], [_foe()])
        await submit_player_intent(
            handle, actor_id="char:caster", intent=_cast("acid-arrow", target_id="mon:foe")
        )
        return live

    live = asyncio.run(_go())
    assert len(_events(live, AttackRolled)) == 1
    assert len(_events(live, DamageApplied)) == 1  # the 4d4; the 2d4 waits for the foe's turn end


def test_a_cast_naming_an_activity_resolves_only_that_one():
    async def _go():
        handle, live = await _start([_caster(["sunbeam"], {6: 1})], [_foe()])
        await submit_player_intent(
            handle,
            actor_id="char:caster",
            intent=_cast("sunbeam", target_id="mon:foe", activity_id=SUNBEAM_INITIAL),
        )
        return live

    live = asyncio.run(_go())
    assert len(_events(live, SaveRolled)) == 1
    assert live.spell_slots_by_entity["char:caster"] == {6: 0}


@pytest.mark.parametrize(
    ("spell_id", "activity_id"),
    [
        ("acid-arrow", "not-an-activity"),
        ("acid-arrow", ACID_ARROW_LINGER),  # fires at the end of the target's turn, never by name
        ("sunbeam", SUNBEAM_NEW_LINE),  # a later use of a Sunbeam not running
    ],
)
def test_an_activity_the_caster_cannot_use_now_is_refused_before_the_slot_is_spent(
    spell_id: str, activity_id: str
):
    async def _go():
        handle, live = await _start([_caster([spell_id], {2: 1, 6: 1})], [_foe()])
        await submit_player_intent(
            handle,
            actor_id="char:caster",
            intent=_cast(spell_id, target_id="mon:foe", activity_id=activity_id),
        )
        return live

    live = asyncio.run(_go())
    assert [e.reason for e in _events(live, CastFailed)] == ["activity_invalid"]
    assert live.spell_slots_by_entity["char:caster"] == {2: 1, 6: 1}
    assert not _events(live, SpellCast)


def test_activity_invalid_is_a_cast_failed_reason():
    assert "activity_invalid" in typing.get_args(CastFailedReason)


# ── turn boundaries ──────────────────────────────────────────────────────────


def test_acid_arrow_burns_again_at_the_end_of_the_targets_next_turn_and_then_stops():
    """SRD 5.2 Acid Arrow: "On a hit, the target takes 4d4 Acid damage and 2d4
    Acid damage at the end of its next turn." """

    async def _go():
        handle, live = await _start([_caster(["acid-arrow"], {2: 1})], [_foe()])
        await submit_player_intent(
            handle, actor_id="char:caster", intent=_cast("acid-arrow", target_id="mon:foe")
        )
        after_cast = len(live.event_log)
        await advance_monster_turn(handle)  # the foe's turn: the acid lingers at its end
        after_foe = len(live.event_log)
        await submit_player_intent(
            handle, actor_id="char:caster", intent=PlayerIntent(intent_type="pass")
        )
        await advance_monster_turn(handle)  # the foe's second turn: nothing left to fire
        return live, after_cast, after_foe

    live, after_cast, after_foe = asyncio.run(_go())
    [hit] = [a for a in _events(live, AttackRolled) if a.attacker_id == "char:caster"]
    assert hit.is_hit
    [linger] = _events(live, EffectApplied)
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
    assert len(_events(live, ActivityTriggered)) == 1
    assert len([e for e in _events(live, DamageApplied) if e.damage_type == "acid"]) == 2
    assert live.held_uses == []


def test_searing_smite_burns_at_the_start_of_each_of_the_targets_turns_until_it_saves():
    """SRD 5.2 Searing Smite: "At the start of each of its turns until the
    spell ends, the target takes 1d6 Fire damage and then makes a Constitution
    saving throw. On a failed save, the spell continues. On a successful save,
    the spell ends." """

    async def _go(seed: int):
        handle, live = await _start([_caster(["searing-smite"], {1: 1})], [_foe()], seed=seed)
        await submit_player_intent(
            handle, actor_id="char:caster", intent=_cast("searing-smite", target_id="mon:foe")
        )
        await submit_player_intent(
            handle, actor_id="char:caster", intent=PlayerIntent(intent_type="pass")
        )
        await advance_monster_turn(handle)
        return live

    outcomes = set()
    for seed in range(1, 9):
        live = asyncio.run(_go(seed))
        [seared] = _events(live, EffectApplied)
        assert seared.effect.name == "Seared"
        [fired] = _events(live, ActivityTriggered)
        assert (fired.trigger, fired.subject_id, fired.activity_name) == (
            "turn_start",
            "mon:foe",
            "Start of Turn Save",
        )
        [save] = _events(live, SaveRolled)
        assert (save.ability, save.target_id) == ("con", "mon:foe")
        burns = [e for e in _events(live, DamageApplied) if e.damage_type == "fire"]
        assert len(burns) == 2  # "As you hit the target, it takes an extra 1d6", then the turn's
        ended = _events(live, EffectExpired)
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

    async def _go():
        handle, live = await _start(
            [_caster(["hunters-mark"], {1: 1}, class_slug="ranger")],
            [_foe("mon:marked", col=1), _foe("mon:other", col=0, zone_id=cell(0, 1))],
            seed=2,  # every swing hits
        )
        await submit_player_intent(  # a Bonus Action: the turn goes on
            handle, actor_id="char:caster", intent=_cast("hunters-mark", target_id="mon:marked")
        )
        swing = PlayerIntent(intent_type="attack", weapon_id="longsword", target_id="mon:other")
        await submit_player_intent(handle, actor_id="char:caster", intent=swing)
        await submit_player_intent(
            handle,
            actor_id="char:caster",
            intent=swing.model_copy(update={"target_id": "mon:marked"}),
        )
        await submit_player_intent(
            handle, actor_id="char:caster", intent=PlayerIntent(intent_type="pass")
        )
        await advance_monster_turn(handle)
        await advance_monster_turn(handle)
        await submit_player_intent(  # keeps the turn; the mark ends with the concentration
            handle,
            actor_id="char:caster",
            intent=PlayerIntent(intent_type="drop_concentration"),
        )
        await submit_player_intent(
            handle,
            actor_id="char:caster",
            intent=swing.model_copy(update={"target_id": "mon:marked"}),
        )
        return live

    live = asyncio.run(_go())
    hits = [h for h in _events(live, AttackRolled) if h.attacker_id == "char:caster"]
    assert [h.is_hit for h in hits] == [True, True, True]
    assert [h.attack_type for h in hits] == ["melee", "melee", "melee"]
    [fired] = _events(live, ActivityTriggered)
    assert (fired.trigger, fired.subject_id, fired.target_id) == (
        "hits",
        "char:caster",
        "mon:marked",
    )
    force = [e for e in _events(live, DamageApplied) if e.damage_type == "force"]
    assert [e.target_id for e in force] == ["mon:marked"]
    # the mark's damage comes after the hit's own
    assert live.event_log.index(hits[1]) < live.event_log.index(force[0])
    # concentration dropped: the mark is gone, and the third hit adds nothing
    assert any(e.reason == "concentration_drop" for e in _events(live, EffectExpired))
    assert live.held_uses == []


# ── later uses ───────────────────────────────────────────────────────────────


def test_a_later_use_repeats_the_activity_without_a_slot_or_a_new_cast():
    """SRD 5.2 Sunbeam: "Until the spell ends, you can take a Magic action to
    create a new Line of radiance." """

    async def _go():
        handle, live = await _start([_caster(["sunbeam"], {6: 1})], [_foe(col=9)])
        await submit_player_intent(
            handle, actor_id="char:caster", intent=_cast("sunbeam", target_id="mon:foe")
        )
        await advance_monster_turn(handle)  # 45 ft off: the foe closes but can't reach
        await submit_player_intent(
            handle,
            actor_id="char:caster",
            intent=_cast("sunbeam", target_id="mon:foe", activity_id=SUNBEAM_NEW_LINE),
        )
        return live

    live = asyncio.run(_go())
    assert not _events(live, CastFailed)
    assert len(_events(live, SpellCast)) == 1
    assert live.spell_slots_by_entity["char:caster"] == {6: 0}
    assert len(_events(live, SaveRolled)) == 2
    [fired] = _events(live, ActivityTriggered)
    assert (fired.trigger, fired.activity_id, fired.actor_id) == (
        "later_use",
        SUNBEAM_NEW_LINE,
        "char:caster",
    )
    # the later line neither restarts nor ends the cast's concentration
    assert not any(e.reason == "concentration_drop" for e in _events(live, EffectExpired))
    assert "char:caster" in live.concentration_chain


def test_a_later_use_costs_what_its_own_activity_says():
    """SRD 5.2 Heat Metal: "you can take a Bonus Action on each of your later
    turns to deal this damage again" after an Action to cast."""

    async def _go():
        handle, live = await _start([_caster(["heat-metal"], {2: 1})], [_foe(col=9)])
        await submit_player_intent(
            handle, actor_id="char:caster", intent=_cast("heat-metal", target_id="mon:foe")
        )
        await advance_monster_turn(handle)
        await submit_player_intent(
            handle,
            actor_id="char:caster",
            intent=_cast("heat-metal", target_id="mon:foe", activity_id="YPEmwJHX7g68307N"),
        )
        return live, live.current_actor_id

    live, actor_after = asyncio.run(_go())
    assert not _events(live, CastFailed)
    assert actor_after == "char:caster"  # a Bonus Action keeps the turn
    caster = next(c for c in live.initiative if c.entity_id == "char:caster")
    assert (caster.bonus_action_available, caster.action_available) == (False, True)
    assert len([e for e in _events(live, DamageApplied) if e.damage_type == "fire"]) == 2


# ── a trigger a consumer's own data sets ─────────────────────────────────────


class _WithSpell:
    """The bundled corpus plus one spell of the consumer's own."""

    def __init__(self, spell):
        self._spell = spell

    def get_spell(self, slug: str):
        return self._spell if slug == self._spell.slug else BUNDLED.get_spell(slug)

    def __getattr__(self, name: str):
        return getattr(BUNDLED, name)


def _frost_armor():
    """A self-cast that lasts 10 minutes and answers each melee hit on its
    bearer with 2d8 Cold damage to the attacker — the shape of Armor of
    Agathys, which is not SRD. Built on Fire Shield's cast (its effects on the
    caster anchor the use)."""
    fire_shield = BUNDLED.get_spell("fire-shield")
    retaliate = DamageActivity(
        _id="retaliate",
        name="Frost Lash",
        damage=DamageActivityDamageBlock(
            parts=[DamagePartBlock(number=2, denomination=8, types=["cold"])]
        ),
        trigger=ActivityTrigger(kind="hit_by_attack", attack="melee"),
    )
    return fire_shield.model_copy(
        update={
            "slug": "frost-armor",
            "name": "Frost Armor",
            "activities": [*fire_shield.activities, retaliate],
        }
    )


def test_a_hit_on_the_bearer_fires_the_held_activity_against_the_attacker():
    set_lib_loader_for_tests(_WithSpell(_frost_armor()))

    async def _go():
        handle, live = await _start(
            [_caster(["frost-armor"], {4: 1}, hp_current=80, hp_max=80)],
            [_foe(monster_template_slug="goblin-warrior", ac=15)],
            seed=5,
        )
        await submit_player_intent(
            handle, actor_id="char:caster", intent=_cast("frost-armor", target_id="char:caster")
        )
        await advance_monster_turn(handle)  # the goblin swings at the caster
        return live

    live = asyncio.run(_go())
    hits = [h for h in _events(live, AttackRolled) if h.attacker_id == "mon:foe" and h.is_hit]
    assert hits, "the seed must give the goblin a hit"
    fired = _events(live, ActivityTriggered)
    assert len(fired) == len(hits)
    assert {(f.trigger, f.subject_id, f.target_id) for f in fired} == {
        ("hit_by_attack", "char:caster", "mon:foe")
    }
    cold = [e for e in _events(live, DamageApplied) if e.damage_type == "cold"]
    assert [e.target_id for e in cold] == ["mon:foe"] * len(hits)
