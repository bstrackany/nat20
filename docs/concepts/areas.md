# Areas on the map

Some spells and features leave an area behind them. SRD 5.2 Fog Cloud makes "a
20-foot-radius Sphere of fog centered on a point within range", and it stays
there until the spell ends. Spirit Guardians' "15-foot Emanation" and Aura of
Protection's "10-foot Emanation that originates from you" move with the creature
they surround. The engine keeps all of these as **areas**.

An area is one of two kinds:

- **placed**: it stays at its point of origin (Fog Cloud, Darkness, Daylight,
  Grease, Web, Sleet Storm, Entangle, Spike Growth, Stinking Cloud, Moonbeam,
  Silence, Black Tentacles, Insect Plague, Incendiary Cloud, Earthquake), unless
  the spell moves it (Moonbeam, Cloudkill);
- **anchored**: it surrounds a creature and moves with it (Spirit Guardians,
  Conjure Woodland Beings, Holy Aura, Aura of Protection).

## The data

The corpus marks the activity that makes an area with `Activity.area`
(`PersistentArea`). The area's shape and size come from the activity's own
`target.template`. A `radius` template is an Emanation, so the area is
anchored; every other shape is placed. A size written as a formula is worked
out when the area is made, from the user's numbers and the level of the cast:
Fog Cloud's `20 * @item.level`, Aura of Protection's `@scale.paladin.aura`.

```json
"area": {
  "map": {"obscurement": "heavy", "lighting": "dark"},
  "effects": ["WOJ5r71FCDRmElea"],
  "affects": "ally",
  "inactive_while_incapacitated": true
}
```

| Field | Meaning | Case |
|---|---|---|
| `map.obscurement` | `light` / `heavy` on the area's cells | Web; Fog Cloud, Sleet Storm, Stinking Cloud, Darkness |
| `map.lighting` | `bright` / `dim` / `dark` on the area's cells | Daylight; Moonbeam; Darkness |
| `map.difficult_terrain` | the cells are Difficult Terrain | Grease, Web, Sleet Storm, Entangle, Spike Growth, Earthquake |
| `map.blocks_verbal` | a spell with a Verbal component can't be cast from inside | Silence |
| `effects` | ids of the entry's own `passive_effects` that apply to a creature while it is in the area | Aura of Protection's save bonus, Silence's Thunder immunity, Holy Aura's save Advantage and attackers' Disadvantage |
| `affects` | who those effects reach: `all` (default), `ally` (the owner included), `enemy` | Aura of Protection, Holy Aura; Silence; Spirit Guardians, Conjure Woodland Beings |
| `inactive_while_incapacitated` | the effects are off while the creature the area surrounds is Incapacitated | Aura of Protection |
| `triggers` | the moments the area fires an activity at a creature (below) | Moonbeam, Grease, Web, Sleet Storm, Spike Growth, Stinking Cloud, Spirit Guardians, Black Tentacles, Insect Plague, Cloudkill, Incendiary Cloud |
| `fires` | the activity it fires, when not the one that makes the area (that activity carries `trigger.kind: area`, so the cast doesn't resolve it) | Stinking Cloud's "Start of Turn Save", Spirit Guardians' and Conjure Woodland Beings' "Emanation Save", Incendiary Cloud's "Per Turn Save" |
| `once_per_turn` | a creature makes the save "only once per turn", in any creature's turn | Moonbeam, Spirit Guardians; Web and Sleet Storm ("the first time ... on a turn") |
| `breaks_concentration` | a creature that fails the save loses Concentration | Sleet Storm |
| `move_ft` | on a later turn the owner can take a Magic action to move the placed area that far | Moonbeam (60) |
| `drifts_ft` | the placed area moves that far straight away from its owner at the start of each of the owner's turns | Cloudkill (10) |

`affects` decides who the triggers reach as well as who the effects reach.
With `all`, that is whoever is in the area, the caster included: a Moonbeam
or Stinking Cloud over its caster catches it, and one elsewhere never does.
Spirit Guardians is `enemy`: "you can designate creatures to be unaffected"
is modelled as the caster and its allies, so neither saves nor has its Speed
halved. Conjure Woodland Beings' "you can force that creature to make a Wisdom
saving throw" is `enemy` the same way (its "creature you can see" is not
checked), and Holy Aura's "creatures of your choice" is `ally`.

Defaults are left out when the data is written, so an area that only changes the
map is written as `{"map": {...}}`. An `area` on a `wall` template is refused
when the data loads, and so are `fires`, `once_per_turn` or
`breaks_concentration` without `triggers`, a moment listed twice, and
`move_ft` or `drifts_ft` on an Emanation.

## When an area is made, and when it ends

A cast, feature use or item use makes an area for each resolved activity that
carries `area`. A placed area takes the cast's own point of origin and aim, the
same ones its save used: `target_zone_id` for a Sphere or Cylinder, measured
against the spell's range, and for a Cube `target_zone_id` (the face it starts
from: Web, Grease, Entangle, Black Tentacles, also range-checked) and
`direction`. A feature or feat whose
area activity needs no activation (Aura of Protection) has its area from
`start_combat` for the whole combat.

An area lasts as long as the use that made it:

- **Concentration spell**: while the caster concentrates on it. The area holds
  the caster's concentration anchor, so the spell does not end just because the
  creature it Restrained breaks free. Any way concentration ends (the drop
  intent, a failed Constitution save, Incapacitated, death, another
  concentration spell, the maximum duration) ends the area.
- **Other spell**: while an effect it applied remains. If it applied none, for
  its duration in rounds, counted down at the caster's turn end (Grease: 10).
- **Used feature or item**: the activity's own duration.
- **Always-on feature**: the whole combat.

An anchored area also ends when the creature it surrounds dies or leaves the
combat.

## What an area does

**The map.** While an area stands, `GridTopology` sees its changes. They are
laid over the scene with `GridTopology.apply_area_overlay`, so `can_see`,
`obscurement_on_cell`, `light_on_cell`, `edge_distance`, the paths built on
`edge_distance`, and Hide's Heavily Obscured gate all read them. An area never
lowers the scene's obscurement. Its light replaces the scene's light on its
cells, and when two areas overlap, the later area's light wins. Difficult
Terrain doesn't stack. Darkness is both `dark` and `heavy`, so Darkvision can't
see into it, while Blindsight and Truesight can.

**Its effects.** An area's effects are never copied onto a creature. Whenever
a roll reads a creature's effects, the engine also asks which areas the
creature is in and adds their effects. This happens in the save, check and AC
projection, and in `_effects_in_play` for attack flags. So stepping out of an
aura, the paladin becoming Incapacitated, or the area ending can never leave a
stale bonus behind. The effects' values are worked out from the **owner's**
numbers when the area is made: an ally in Aura of Protection gains the paladin's
Charisma modifier, with a minimum of +1. Because the aura reaches its paladin
through the area, the paladin's own bonus also switches off while the paladin
is Incapacitated.

SRD 5.2 Combining Game Effects: "The effects of the same spell cast multiple
times don't combine." When a creature stands in two areas from the same source,
only the more potent one applies. Potency is decided by the higher slot, then
the larger numeric change, then the more recent area. So with two paladins, the
better aura applies.

**Silence.** A `cast_spell` of a spell with a Verbal component, from a caster
standing in a Silence, is refused with `CastFailed(reason="components_missing")`
before anything is spent.

**Where a creature is.** A creature is in an area when **any** cell it occupies
is in the area. Today every creature occupies one cell, so this is its own
cell, but the rule is written for larger creatures too. An Emanation is measured
from the edge of its bearer's space, and it includes the bearer's own space.
Every area keeps only the cells it has line of effect to from its origin: a wall
or a blocked cell drops the cells behind it.

## What an area does to the creatures in it

`triggers` names the moments (`AreaTrigger`), each from a spell's own text:

| Moment | When | Case |
|---|---|---|
| `created` | the area appears on a creature: this is the cast's own resolution of the area's activity, against the creatures in it | Moonbeam "When the Cylinder appears"; Grease, Black Tentacles, Insect Plague, Cloudkill, Incendiary Cloud |
| `enters` | a step brings the creature into the area: its own move, a monster's walk, or a push (each 5-foot step of the push, after it lands) | Grease, Web, Sleet Storm, Moonbeam, Spirit Guardians |
| `turn_start` | the creature starts its turn in the area | Stinking Cloud, Web, Sleet Storm |
| `turn_end` | the creature ends its turn in the area | Moonbeam, Grease, Spirit Guardians |
| `moves_within` | every 5-foot step that ends in the area, the entering one included | Spike Growth "2d4 Piercing damage for every 5 feet it travels" |
| `moved_onto` | the area comes to cover the creature: an Emanation following its bearer (at each step of the walk), a `move_area`, or a drift | Spirit Guardians "whenever the Emanation enters a creature's space"; Moonbeam, Cloudkill |

An area without `created` among its moments resolves its own activity against
nobody when cast (SRD 5.2 Web: "The first time a creature enters the webs on a
turn or starts its turn there" — not when the webs appear), and so does a
utility whose every effect the area delivers live (Silence). A creature is in
an area when any cell it occupies is in it.

Each firing emits `AreaTriggered` (the area, the moment, the creature), then
resolves the activity through the held-use path (`ActivityTriggered` with
`trigger: "area"`): from the area's owner, at the level it was cast at, with
the caster's save DC, against that creature. Effects the area delivers live
(Spirit Guardians' halved Speed) are left off the fired copy. On a walk the
`ActorMoved` run so far is closed first, so the creature is in the cell when
it saves. A mover the firing leaves at Speed 0 (Web's Restrained) or at 0 Hit
Points has no movement left and stops there.

**Once per turn.** One record per spell and creature holds the turn (`round`,
whichever creature's turn it is) its save was last made in. It is marked before
the save rolls, and shared by every moment: the cast's own save uses up the
cast turn (`created`), so the caster's turn end doesn't fire again. It is
keyed by the spell, not the cast. SRD 5.2 Combining Game Effects: "The effects
of the same spell cast multiple times don't combine." So a creature in two
Moonbeams saves once, and at any one moment only the more potent of two
same-spell areas fires (higher slot, then more recent). An area's record is
dropped when no area of that spell is left, so a recast starts clean. Spike
Growth and Grease have no once-per-turn limit.

**How the effects end.** An effect the triggers applied that has a duration
ends when the area ends (Black Tentacles' Restrained "until the spell ends";
Web's). One without a duration is a condition that stands (Grease's and Sleet
Storm's Prone). Only an effect with a duration holds a non-concentration
spell's area open, so Grease lasts its minute even after it knocks someone
Prone.

**Moving an area.** A `move_area` intent (`area_id`, `target_zone_id` = the
new point of origin) moves the actor's own placed area that has `move_ft`, up
to that many feet, for the Action (a Magic action, which Action Surge can't
pay for). Because the cast spent the cast turn's Action, the move is always on
a later turn. The area travels in 5-foot steps along a straight line, emits
`AreaMoved` with its new cells, and then fires `moved_onto` for each creature
a step newly covered. Refusals raise `IntentRejectedError`: `target_invalid`
(no such area of the actor's, one that doesn't move, an off-grid point),
`out_of_range`, `no_action_economy`. Cloudkill's `drifts_ft` moves the same way
at its owner's turn start, straight away from the owner. It stops at the grid
edge, and an area centred on its owner stays put.

**Determinism.** With no area on the map nothing here draws a die. An area's
dice are drawn when its moment happens: after the step that triggers it on a
walk, after a push lands, in the `turn_start` / `turn_end` hooks
(`engine:area-triggers-turn-start`, which runs last at turn start, and
`engine:area-triggers-turn-end`, just before `engine:areas-turn-end`), and after
a `move_area`.

## Events and the view

- `AreaCreated` — `area_id`, `owner_id`, `source_id` (the slug), `kind`,
  `shape`, `size_ft`, `origin` (placed) or `anchor_id` (anchored), `cells`.
- `AreaMoved` — an area's new `cells`: an anchored one after its bearer walks
  or is pushed, a placed one after a `move_area` or a drift.
- `AreaTriggered` — `area_id`, `source_id`, `trigger` (the moment),
  `creature_id`.
- `AreaEnded` — `area_id`.

`LiveCombatView.areas` maps each standing area's id to an `AreaView`. The view
carries the fields above plus `source_kind`, `source_name`, `map`, `affects` and
`effect_ids`. Cells are sorted by column, then row.

## Not yet

- Escaping by an action: Web, Entangle and Black Tentacles let a Restrained
  creature "take an action to make a Strength (Athletics) check against your
  spell save DC". No intent does that yet. The only escape is
  `escape_grapple`, and it reads a Grapple's stored DC. Entangle's Restrained
  is ended instead by the existing end-of-turn repeat save on its effect.
- Web's Restrained "while in the webs": it ends with the webs, not when a push
  moves the creature out of them. The webs' collapse, burning and the
  flammable 2d4 Fire are not modelled either.
- Stinking Cloud's "can't take an action or a Bonus Action" while Poisoned,
  Moonbeam's shape-shift revert, and Spike Growth's camouflage.
- Spirit Guardians' halved Speed is in its area's effects, but nothing reads
  Speed from an effect yet. Its caster can't pick which creatures to spare.
- Incendiary Cloud's drift ("in a direction you choose"), Flaming Sphere (no
  template in the data; a Bonus Action move that rams), and a Bonus Action
  move (a homebrew rift).
- One area firing different activities at different moments (a homebrew zone:
  cold at the start of a turn, an acid save at its end).
- A save the area makes "at the end of each of your turns" — the caster's turn,
  for everyone in it (Earthquake), and Daylight's Dim Light ring beyond its
  Sphere.
- A teleport into an area (no engine teleport exists yet).
- An enemy an area drops at a turn's end isn't read by `enemy_falls`
  triggers: the moment sweep runs before the area hook.
- Wall of Force, and the five `wall` spells: a wall is an edge between cells,
  not a set of cells.
- Darkness dispelling a light spell of level 2 or lower that it overlaps.
- An area's conditions (Silence's Deafened): only an effect's `changes` are
  read live, not its `statuses`.
- Heavy obscurement blocks sight into a cell, not through it.
- A monster's cast and a readied cast make no area. A cast from a monster
  inside a Silence is not refused.
