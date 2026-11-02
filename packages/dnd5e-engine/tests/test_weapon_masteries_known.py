"""``PartyMemberSpec.weapon_masteries`` — which weapons' mastery a PC can use.

SRD 5.2 §Weapon Mastery (Fighter, Barbarian, Paladin, Ranger, Rogue): "Your
training with weapons allows you to use the mastery properties of two kinds of
weapons of your choice with which you have proficiency". ``None`` (unset) keeps
every weapon's mastery live; a tuple, even an empty one, limits mastery to the
weapon slugs it lists.
"""

from __future__ import annotations

from dnd5e_engine.events import CombatantMoved
from dnd5e_engine.orchestrator import submit_player_intent
from tests.e2e.harness import cell, events_of, run_async
from tests.test_weapon_masteries_c15 import _attack, _combatant, _mon, _pc, _start


def _club_hit(session_id: str, **pc: object):
    async def _run():
        start, live = await _start(
            session_id,
            [_pc("char:hero", "Hero", cell(0, 0), **pc)],
            [_mon("mon:foe", "Foe", cell(0, 1))],
        )
        await submit(start, _attack("club", "mon:foe"))
        return live

    return run_async(_run())


async def submit(start, intent) -> None:
    await submit_player_intent(start.handle, actor_id="char:hero", intent=intent)


def test_unset_uses_every_weapons_mastery() -> None:
    live = _club_hit("mastery-known-unset")
    assert live.slow_marks == {"mon:foe": {"char:hero"}}


def test_a_listed_weapon_uses_its_mastery() -> None:
    live = _club_hit("mastery-known-listed", weapon_masteries=("club", "dagger"))
    assert live.slow_marks == {"mon:foe": {"char:hero"}}


def test_an_unlisted_weapon_has_no_mastery() -> None:
    live = _club_hit("mastery-known-unlisted", weapon_masteries=("dagger",))
    assert live.slow_marks == {}


def test_an_empty_list_turns_every_mastery_off() -> None:
    live = _club_hit("mastery-known-empty", weapon_masteries=())
    assert live.slow_marks == {}


def test_an_unlisted_push_weapon_does_not_push() -> None:
    async def _run():
        start, live = await _start(
            "mastery-known-push",
            [_pc("char:hero", "Hero", cell(0, 0), weapon_masteries=())],
            [_mon("mon:foe", "Foe", cell(0, 1))],
        )
        await submit(start, _attack("greatclub", "mon:foe"))
        return live

    live = run_async(_run())
    assert [e for e in events_of(live, CombatantMoved) if e.forced] == []


def test_an_unlisted_nick_offhand_swing_spends_the_bonus_action() -> None:
    """Dagger then scimitar (Nick): without scimitar's mastery the off-hand
    swing is the ordinary Light extra attack, paid with the Bonus Action."""

    async def _run():
        start, live = await _start(
            "mastery-known-nick",
            [_pc("char:hero", "Hero", cell(0, 0), weapon_masteries=("dagger",))],
            [_mon("mon:foe", "Foe", cell(0, 1))],
        )
        await submit(start, _attack("dagger", "mon:foe"))
        await submit(start, _attack("scimitar", "mon:foe"))
        return live

    hero = _combatant(run_async(_run()), "char:hero")
    assert hero.offhand_attack_spent is True
    assert hero.bonus_action_available is False
