"""A feature's save DC that reads "your spell save DC" uses the caster's class
spellcasting ability, as a spell's does.

SRD 5.2 Cleric, Turn Undead: "As a Magic action, you present your Holy Symbol
and censure Undead creatures. Each Undead of your choice within 30 feet of you
must make a Wisdom saving throw." Channel Divinity: "If a Channel Divinity
effect requires a saving throw, the DC equals the spell save DC from this
class's Spellcasting feature."
"""

from __future__ import annotations

from dnd5e_srd_data.loader import BundledAssetLoader

from dnd5e_engine.events import SaveRolled
from tests.c20_support import act, events, foe, pc, start

_TURN_UNDEAD = next(
    a.id
    for a in BundledAssetLoader().get_feature("channel-divinity-cleric").activities
    if a.name == "Turn Undead"
)


def test_turn_undead_uses_the_clerics_spell_save_dc() -> None:
    cleric = pc(class_slug="cleric", character_level=3, wisdom=16)  # 8 + WIS 3 + proficiency 2
    zombie = foe(monster_template_slug="zombie", name="zombie")
    handle, live = start([cleric], seed=7, encounter=[zombie])
    act(
        handle,
        "char:hero",
        intent_type="use_feature",
        feature_id="channel-divinity-cleric",
        activity_id=_TURN_UNDEAD,
        target_id="mon:foe",
    )
    saves = events(live, SaveRolled)
    assert [(s.target_id, s.ability, s.dc) for s in saves] == [("mon:foe", "wis", 13)]
