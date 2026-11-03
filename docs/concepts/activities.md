# Activities

Nat20 resolves combat against a **typed activity corpus**, not hard-coded
rule branches. Weapons, spells, monster actions, and class features each
carry a list of *activities* — Foundry-aligned, Pydantic-typed descriptions
of what happens when the thing is used (an attack roll, a save-for-damage, an
applied effect, and so on). The dataset lives in the companion
`dnd5e-srd-data` package and is loaded through `BundledAssetLoader`.

## How an intent becomes activities

When you call `submit_player_intent(handle, actor_id, intent)`, the engine
reads the `PlayerIntent`'s `intent_type` to pick the relevant asset
reference: `"attack"` consumes `weapon_id`, `"cast_spell"` consumes
`spell_id` (and optional `slot_level`), `"use_item"` consumes `item_id`, and
`"use_feature"` consumes `feature_id`. It fetches that typed entity from the
loader and walks its activities through the per-kind resolvers, emitting the
resulting `CombatEvent` stream.

## Why typed activities

This is the core design choice: the engine is **edition-agnostic** and
consumes whatever typed content it is handed. Adding a new weapon, spell, or
monster is a data change to the SRD corpus, not an engine change. Because the
shapes mirror the Foundry VTT dnd5e schema, content can be sourced and
validated against an existing, battle-tested model rather than a bespoke
Nat20-only one.

The activity layer is where attack rolls, damage expressions, area-of-effect
targeting, and effect application are uniformly described — so combat
resolution stays a single code path regardless of which game object triggered
it.

## Which activities a use resolves

A spell, feature or item often carries several activities. A `use_feature`
names the one it means with `PlayerIntent.activity_id` (Channel Divinity's
three options); a `cast_spell` may do the same (Sunbeam's "Initial Cast"),
and naming one the spell lacks is refused with
`CastFailed(reason="activity_invalid")` before the slot is spent. Without a
name, a cast resolves every activity that fires with the cast.

## When an activity fires

Not every activity fires with the use. SRD 5.2 puts part of many spells
later — Acid Arrow's "2d4 Acid damage at the end of its next turn", Hunter's
Mark's "extra damage to the target whenever you hit it with an attack roll" —
and the corpus marks such an activity with `Activity.trigger`:

| `trigger.kind` | Fires when | Resolves against |
|---|---|---|
| `turn_start`, `turn_end` | a subject's turn begins or ends | the subject |
| `hits` | the subject's attack roll hits (`attack`: melee or ranged only; `against_bearer`: only a creature bearing an effect of the use) | the creature hit |
| `hit_by_attack` | an attack roll hits the subject | the attacker |
| `takes_damage` | the subject takes damage | the creature whose action dealt it |
| `natural_1` | the d20 of the subject's D20 Test (attack roll, saving throw, ability check) shows 1 | the subject |
| `casts_spell` | the subject casts a spell (`spell`: only that spell's slug; `with_slot`: only a cast with a spell slot, not a cantrip) | the subject |
| `reduces_to_zero` | the subject's act drops a creature hostile to it to 0 Hit Points | the subject |
| `enemy_falls` | a creature hostile to the subject drops to 0 Hit Points, by anyone's act (`within_ft`: only within that many feet of the subject) | the subject |
| `later_use` | never by the engine: the cast does not resolve it (`also_at_cast`: it does) | — |

The subject is each creature bearing an effect of the use (`subject: target`,
the default) or the creature that used it (`subject: caster`). The engine
holds the use while any effect it applied remains on a creature — or, when it
applied none, for the spell's duration — and the triggers end with it. A
character's features and feats are held for the whole combat, so a feature's
"when you hit" rider fires on every hit; `once_per_turn` caps it at one per
turn. A
fired activity resolves through the same context and resolver as the cast, at
the level the spell was cast at, after an `ActivityTriggered` event names the
source, the activity, the moment and the creatures. `once` fires the activity
a single time and then ends the use's effects on the subject (the lingering
acid is spent); `ends_on_save` ends them on a creature whose save succeeds
(Searing Smite: "On a successful save, the spell ends").

The last four moments are read from what a resolution did — SRD 5.2 Blessed
Healer's "Immediately after you cast a spell with a spell slot", Dark One's
Blessing's "When you reduce an enemy to 0 Hit Points or an enemy within 10 feet
of you is reduced to 0 Hit Points" — and over each ending turn, for the rolls
and falls no resolution made (the end-of-turn repeat save, an area's damage at
a turn's start; nobody reduced a creature an area dropped). "Drops to 0 Hit
Points" is a character falling Unconscious or a creature dying of damage, not
a third failed death save. Each event fires a moment once, and an event from
before a use was held (the use's own cast) is not its moment. A hit's rider
counts (Hex's extra damage that drops the creature is the caster reducing it);
what an activity fired on one of these four moments emits fires none of them.

A `later_use` activity (Sunbeam's new line, Haste's lethargy) only keeps the
cast from resolving it; re-firing one is not supported, and a `cast_spell`
naming it in `activity_id` is refused with `activity_invalid`.

An activity with no trigger behaves exactly as before, so content that never
sets the field is unchanged.
