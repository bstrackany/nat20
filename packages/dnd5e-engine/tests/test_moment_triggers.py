"""More moments a held activity fires on — ``natural_1``, ``casts_spell``,
``reduces_to_zero`` and ``enemy_falls`` (``dnd5e_engine.trigger_moments``),
read after each resolution and over each ending turn
(``_fire_moment_triggers``, ``engine:moment-triggers-turn-end``). Each fired
activity resolves against its subject.
"""

from __future__ import annotations

import random
from typing import Any

import pytest
from dnd5e_srd_data.schema.common import (
    ActivityTrigger,
    DamagePartBlock,
    HealActivity,
)

from dnd5e_engine.activities.dice import fold_min_max, roll_expr
from dnd5e_engine.events import (
    ActivityTriggered,
    AttackRolled,
    CheckRolled,
    ConcentrationCheck,
    Death,
    SaveRolled,
    SpellCast,
    TempHpApplied,
    Unconscious,
)
from dnd5e_engine.lib_loader import set_lib_loader_for_tests
from dnd5e_engine.orchestrator import _emit, _fire_moment_triggers, _LiveCombat, _update_combatant
from dnd5e_engine.spatial import cell_id as cell
from dnd5e_engine.trigger_moments import Moment, cast_matches, moments_of, subject_candidates
from dnd5e_engine.triggers import HeldUse
from tests.c20_support import act, combatant, events, monster_turn, pc, start
from tests.fork_support import BUNDLED, WithAssets, cast, caster, goblin, homebrew_feat

B = "char:caster"
F = "mon:foe"


@pytest.fixture(autouse=True)
def _bundled_loader():
    set_lib_loader_for_tests(BUNDLED)
    yield
    set_lib_loader_for_tests(None)


def _save(target: str, natural: int) -> SaveRolled:
    return SaveRolled(
        target_id=target, ability="con", dc=13, roll_total=natural, succeeded=False, natural=natural
    )


def _cast(spell_id: str = "magic-missile", slot_level: int | None = 1) -> SpellCast:
    return SpellCast(
        actor_id=B,
        spell_id=spell_id,
        slot_level=slot_level,
        ritual=False,
        components=[],
        material=None,
        material_consumed=False,
        material_cost_gp=0,
    )


# ── reading the stream ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("event", "dealer", "moments"),
    [
        (
            AttackRolled(
                attacker_id=B,
                target_id=F,
                roll_total=3,
                advantage="normal",
                is_crit=False,
                is_hit=False,
                natural=1,
            ),
            None,
            [Moment("natural_1", B)],
        ),
        (_save(B, 1), F, [Moment("natural_1", B)]),
        (_save(B, 2), None, []),
        (
            ConcentrationCheck(target_id=B, dc=10, roll_total=3, succeeded=False, natural=1),
            None,
            [Moment("natural_1", B)],
        ),
        (
            CheckRolled(
                actor_id=B,
                ability="str",
                skill=None,
                dc=None,
                roll_total=4,
                succeeded=None,
                natural=1,
            ),
            None,
            [Moment("natural_1", B)],
        ),
        (_cast(), None, [Moment("casts_spell", B)]),
        (
            Unconscious(target_id=B),
            F,
            [Moment("enemy_falls", None, B), Moment("reduces_to_zero", F, B)],
        ),
        (Death(target_id=F, reason="damage"), None, [Moment("enemy_falls", None, F)]),
        (Death(target_id=F, reason="instant_kill"), F, [Moment("enemy_falls", None, F)]),
        (Death(target_id=B, reason="death_saves"), F, []),
    ],
)
def test_each_moment_is_read_from_its_event(event: Any, dealer: Any, moments: Any):
    assert moments_of(event, dealer) == moments


@pytest.mark.parametrize(
    ("narrowing", "event", "matches"),
    [
        ({}, _cast(slot_level=None), True),
        ({"with_slot": True}, _cast(slot_level=None), False),
        ({"with_slot": True}, _cast(slot_level=2), True),
        ({"spell": "hunters-mark"}, _cast(), False),
        ({"spell": "hunters-mark"}, _cast("hunters-mark"), True),
    ],
)
def test_a_cast_is_narrowed_by_spell_and_slot(narrowing: Any, event: Any, matches: bool):
    assert cast_matches(ActivityTrigger(kind="casts_spell", **narrowing), event) is matches


def test_a_fall_is_watched_by_the_owner_or_each_bearer():
    class _Effect:
        id, origin = "effect:x", "cast:x:char:caster"

    use = HeldUse(
        B, "spell", "x", "X", 1, 1, None, [], [], effects={(F, "effect:x", "cast:x:char:caster")}
    )
    effects = {F: [_Effect()]}
    assert subject_candidates(
        use, ActivityTrigger(kind="enemy_falls", subject="caster"), effects
    ) == [B]
    assert subject_candidates(use, ActivityTrigger(kind="enemy_falls"), effects) == [F]


# ── in combat ────────────────────────────────────────────────────────────────


def _feat_with(trigger: ActivityTrigger) -> None:
    """A feat whose one activity grants its subject 1d4 Temporary Hit Points
    at ``trigger``'s moment."""
    feat = homebrew_feat(
        "omen",
        activities=[
            HealActivity(
                _id="omenward",
                name="Omen Ward",
                healing=DamagePartBlock(number=1, denomination=4, types=["temphp"]),
                trigger=trigger,
            )
        ],
    )
    set_lib_loader_for_tests(WithAssets(feat))


def _fired(live: _LiveCombat) -> list[tuple[str, str, str]]:
    return [(f.trigger, f.subject_id, f.target_id) for f in events(live, ActivityTriggered)]


@pytest.mark.parametrize(
    ("narrowing", "fired"),
    [
        ({}, 2),  # the cantrip and the levelled spell
        ({"with_slot": True}, 1),
        ({"spell": "fire-bolt"}, 1),
    ],
)
def test_a_feat_fires_when_its_owner_casts(narrowing: Any, fired: int):
    """SRD 5.2 Blessed Healer: "Immediately after you cast a spell with a spell
    slot" — the activity lands on the caster after the cast."""
    _feat_with(ActivityTrigger(kind="casts_spell", subject="caster", **narrowing))
    handle, live = start([caster(["fire-bolt", "magic-missile"], {1: 2}, feats=("omen",))], seed=3)
    cast(handle, "fire-bolt", target_id=F)  # the Action ends the turn
    monster_turn(handle)
    cast(handle, "magic-missile", target_id=F, slot_level=1)
    assert _fired(live) == [("casts_spell", B, B)] * fired
    assert len(events(live, TempHpApplied)) == fired
    if fired:
        [first_cast] = events(live, SpellCast)[:1]
        first_fire = events(live, ActivityTriggered)[0]
        assert live.event_log.index(first_cast) < live.event_log.index(first_fire)


def test_reducing_an_enemy_to_zero_fires_on_the_killing_blow_only():
    """SRD 5.2 Dark One's Blessing: "When you reduce an enemy to 0 Hit Points"."""
    _feat_with(ActivityTrigger(kind="reduces_to_zero", subject="caster"))
    fighter = pc("char:ftr", strength=18, attack_bonus=10, feats=("omen",))
    handle, live = start([fighter], encounter=[goblin(hp_current=1, hp_max=1, ac=1)], seed=4)
    act(handle, "char:ftr", intent_type="attack", weapon_id="longsword", target_id=F)
    assert events(live, Death)
    assert _fired(live) == [("reduces_to_zero", "char:ftr", "char:ftr")]


def _watchers(within_ft: int | None) -> _LiveCombat:
    """The feat's owner at 0,0, an ally at 0,1, two foes at 1,0 and 9,9."""
    _feat_with(ActivityTrigger(kind="enemy_falls", subject="caster", within_ft=within_ft))
    _handle, live = start(
        [pc(B, feats=("omen",)), pc("char:ally", zone_id=cell(0, 1), initiative=10)],
        encounter=[goblin(entity_id="mon:near"), goblin(entity_id="mon:far", zone_id=cell(9, 9))],
        seed=1,
    )
    return live


@pytest.mark.parametrize(
    ("within_ft", "fallen", "dealer", "fires"),
    [
        (None, "mon:far", None, True),  # anyone's act, any distance
        (None, "mon:near", "char:ally", True),
        (10, "mon:near", None, True),
        (10, "mon:far", None, False),
        (None, "char:ally", "mon:near", False),  # not an enemy
    ],
)
def test_an_enemy_falling_fires_for_its_watcher(
    within_ft: int | None, fallen: str, dealer: str | None, fires: bool
):
    """Dark One's Blessing: "an enemy within 10 feet of you is reduced to 0
    Hit Points"."""
    live = _watchers(within_ft)
    mark = len(live.event_log)
    fall = (
        Unconscious(target_id=fallen)
        if fallen.startswith("char:")
        else Death(target_id=fallen, reason="damage")
    )
    _emit(live, fall)
    _fire_moment_triggers(live, mark, dealer_id=dealer)
    assert _fired(live) == ([("enemy_falls", B, B)] if fires else [])


def test_a_natural_1_fires_once_whether_a_resolution_or_the_turn_end_reads_it():
    """A flaw that answers its owner's natural 1: read once by the resolution,
    the turn-end sweep does not read it again; a save outside any resolution
    (an end-of-turn repeat save) is read at the turn's end."""
    _feat_with(ActivityTrigger(kind="natural_1", subject="caster"))
    handle, live = start([pc(B, feats=("omen",))], seed=1)
    mark = len(live.event_log)
    _emit(live, _save(B, 1))
    _fire_moment_triggers(live, mark, dealer_id=B)
    assert _fired(live) == [("natural_1", B, B)]
    _emit(live, _save(B, 1))  # outside any resolution
    _emit(live, _save(B, 20))
    act(handle, B, intent_type="pass")
    assert _fired(live) == [("natural_1", B, B)] * 2
    assert not live.moments_read


def test_a_spells_moment_watches_the_bearer_of_its_effect():
    """``subject: target`` — a held spell answers a cast by the creature
    bearing its effect, and the activity lands on that creature."""
    bless = BUNDLED.get_spell("bless")
    boon = HealActivity(
        _id="boon",
        name="Boon",
        healing=DamagePartBlock(number=1, denomination=4, types=["temphp"]),
        trigger=ActivityTrigger(kind="casts_spell", with_slot=True),
    )
    set_lib_loader_for_tests(
        WithAssets(
            bless.model_copy(update={"slug": "boon", "activities": [*bless.activities, boon]})
        )
    )
    handle, live = start([caster(["boon", "magic-missile"], {1: 3})], seed=2)
    cast(handle, "boon", target_ids=[B], slot_level=1)
    assert not _fired(live)  # its own cast came before the effect landed
    if live.current_actor_id == B:
        act(handle, B, intent_type="pass")
    monster_turn(handle)
    cast(handle, "magic-missile", target_id=F, slot_level=1)
    assert _fired(live) == [("casts_spell", B, B)]


def test_an_enemy_an_area_drops_at_its_turn_end_falls():
    """Moonbeam: "when it ... ends its turn there". The area fires after the
    turn's moment sweep, so it reads the fall it causes itself."""
    _feat_with(ActivityTrigger(kind="enemy_falls", subject="caster"))
    beam = caster(["moonbeam"], {2: 1}, feats=("omen",))
    handle, live = start([beam], encounter=[goblin(hp_current=21, hp_max=21)], seed=1)
    cast(handle, "moonbeam", target_zone_id="1,0", slot_level=2)
    live.tracked_hp[F] = 1
    _update_combatant(live, F, hp_current=1)
    act(handle, F, intent_type="pass")
    assert [d.target_id for d in events(live, Death)] == [F]
    assert [f for f in _fired(live) if f[0] == "enemy_falls"] == [("enemy_falls", B, B)]


# ── Dark One's Blessing ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("expr", "folded"),
    [
        ("max(1, 3 + 3)", "6"),
        ("max(1, -1 + 0)", "1"),
        ("min(5, 2 * 4)", "5"),
        ("max(1d4, 2)", "max(1d4, 2)"),  # dice: left for the parser
        ("2d6+3", "2d6+3"),
    ],
)
def test_a_dice_free_max_or_min_folds_to_its_value(expr: str, folded: str):
    assert fold_min_max(expr) == folded


def test_a_max_formula_rolls():
    assert roll_expr("max(1, 3 + 3)", random.Random(1)) == 6
    with pytest.raises(ValueError, match="Unparseable dice expression"):
        roll_expr("max(1d4, 2)", random.Random(1))


@pytest.mark.parametrize(
    ("foe_at", "fired"),
    [
        # 5 feet away: the kill is also "an enemy within 10 feet" falling.
        (cell(1, 0), ["enemy_falls", "reduces_to_zero"]),
        (cell(5, 0), ["reduces_to_zero"]),
    ],
)
def test_dark_ones_blessing_grants_temporary_hit_points_on_a_kill(foe_at: str, fired: list[str]):
    """SRD 5.2 Dark One's Blessing: "When you reduce an enemy to 0 Hit Points,
    you gain Temporary Hit Points equal to your Charisma modifier plus your
    Warlock level (minimum of 1 Temporary Hit Point). You also gain this
    benefit if someone else reduces an enemy within 10 feet of you to 0 Hit
    Points." CHA 16 and Warlock 3: 6. Gained twice on one kill, they don't add
    up (Temporary Hit Points: "you decide whether to keep the ones you have")."""
    warlock = pc(
        "char:wlk",
        class_slug="warlock",
        subclass_slug="fiend",
        character_level=3,
        charisma=16,
        spells_known=["eldritch-blast"],
    )
    handle, live = start(
        [warlock], encounter=[goblin(hp_current=1, hp_max=1, ac=1, zone_id=foe_at)], seed=4
    )
    cast(handle, "eldritch-blast", actor_id="char:wlk", target_id=F)
    assert [d.target_id for d in events(live, Death)] == [F]
    assert sorted(f[0] for f in _fired(live)) == fired
    assert [t.amount for t in events(live, TempHpApplied)] == [6] * len(fired)
    assert combatant(live, "char:wlk").temp_hp == 6


def test_dark_ones_blessing_fires_when_someone_else_drops_an_enemy_nearby():
    warlock = pc("char:wlk", class_slug="warlock", subclass_slug="fiend", character_level=3)
    fighter = pc("char:ftr", strength=18, attack_bonus=10, zone_id=cell(1, 1), initiative=25)
    handle, live = start(
        [warlock, fighter], encounter=[goblin(hp_current=1, hp_max=1, ac=1)], seed=4
    )
    act(handle, "char:ftr", intent_type="attack", weapon_id="longsword", target_id=F)
    assert [d.target_id for d in events(live, Death)] == [F]
    assert _fired(live) == [("enemy_falls", "char:wlk", "char:wlk")]
    assert combatant(live, "char:wlk").temp_hp == 3  # CHA 10, Warlock 3
