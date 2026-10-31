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

A `later_use` activity (Sunbeam's new line, Haste's lethargy) only keeps the
cast from resolving it; re-firing one is not supported, and a `cast_spell`
naming it in `activity_id` is refused with `activity_invalid`.

An activity with no trigger behaves exactly as before, so content that never
sets the field is unchanged.
