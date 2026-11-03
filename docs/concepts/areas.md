# Areas on the map

Some spells and features leave an area behind them. SRD 5.2 Fog Cloud makes "a
20-foot-radius Sphere of fog centered on a point within range", and it stays
there until the spell ends. Spirit Guardians' "15-foot Emanation" and Aura of
Protection's "10-foot Emanation that originates from you" move with the creature
they surround. The engine keeps all of these as **areas**.

An area is one of two kinds:

- **placed**: it stays at its point of origin (Fog Cloud, Darkness, Grease, Web,
  Sleet Storm, Entangle, Spike Growth, Stinking Cloud, Moonbeam, Silence);
- **anchored**: it surrounds a creature and moves with it (Spirit Guardians, Aura
  of Protection).

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
| `map.difficult_terrain` | the cells are Difficult Terrain | Grease, Web, Sleet Storm, Entangle, Spike Growth |
| `map.blocks_verbal` | a spell with a Verbal component can't be cast from inside | Silence |
| `effects` | ids of the entry's own `passive_effects` that apply to a creature while it is in the area | Aura of Protection's save bonus, Silence's Thunder immunity |
| `affects` | who those effects reach: `all` (default), `ally` (the owner included), `enemy` | Aura of Protection; Silence; homebrew auras |
| `inactive_while_incapacitated` | the effects are off while the creature the area surrounds is Incapacitated | Aura of Protection |

Defaults are left out when the data is written, so an area that only changes the
map is written as `{"map": {...}}`. Spirit Guardians, whose area does nothing yet
(its save is in a later piece), is written as `{}`. An `area` on a `wall`
template is refused when the data loads.

## When an area is made, and when it ends

A cast, feature use or item use makes an area for each resolved activity that
carries `area`. A placed area takes the cast's own point of origin and aim, the
same ones its save used: `target_zone_id` for a Sphere or Cylinder, measured
against the spell's range, and `direction` for a Cube. A feature or feat whose
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

## Events and the view

- `AreaCreated` — `area_id`, `owner_id`, `source_id` (the slug), `kind`,
  `shape`, `size_ft`, `origin` (placed) or `anchor_id` (anchored), `cells`.
- `AreaMoved` — an anchored area's new `cells`, after its bearer walks or is
  pushed.
- `AreaEnded` — `area_id`.

`LiveCombatView.areas` maps each standing area's id to an `AreaView`. The view
carries the fields above plus `source_kind`, `source_name`, `map`, `affects` and
`effect_ids`. Cells are sorted by column, then row.

## Not yet

- What happens to a creature that enters an area, starts or ends its turn in
  it, or moves within it (Spirit Guardians' and Moonbeam's saves, Spike Growth's
  damage per 5 feet, Web's Restrained). Moving an area (Moonbeam's Magic
  action) is not modelled either.
- Wall of Force, and the five `wall` spells: a wall is an edge between cells,
  not a set of cells.
- Darkness dispelling a light spell of level 2 or lower that it overlaps.
- An area's conditions (Silence's Deafened): only an effect's `changes` are
  read live, not its `statuses`.
- Heavy obscurement blocks sight into a cell, not through it.
- A monster's cast and a readied cast make no area. A cast from a monster
  inside a Silence is not refused.
