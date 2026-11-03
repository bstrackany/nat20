# Effects

Ongoing conditions and buffs are modeled as **`ActiveEffect`** documents — a
Foundry-aligned, Pydantic-typed shape. A spell like *bless*, a *poisoned*
condition, or a magic-item bonus all become `ActiveEffect`s attached to a
combatant for the duration of combat.

## The model

An `ActiveEffect` carries:

- `id` — the template id analog (e.g. `"effect:bless"`).
- `origin` — what produced it (e.g. `"cast:bless:1"`, `"item:sword+1:abc12"`).
- `target_id` — the combatant it is attached to.
- `duration` — an `ActiveEffectDuration` (see *Durations* below).
- `changes` — a list of `ActiveEffectChange` entries (the mechanical
  modifications it applies).
- `statuses` — the set of condition slugs it imposes.
- `flags` — a free-form dict. The engine reads a few keys: `concentration`
  (the effect is its caster's concentration), `applicable_action_types` (a
  weapon-only bonus), and three from C21: `concentration_anchor` (the marker a
  concentration spell that applied no effect of its own leaves on its caster),
  `enchanted_weapon` (a weapon slug: the effect's changes apply to attacks with
  that weapon only) and `transform_form` (a monster slug: the effect carries a
  Wild Shape or *Polymorph* form).

## Durations

`ActiveEffectDuration` carries three counters, all of which tick at a turn
boundary (`turn_lifecycle`'s `turn_end` hooks). SRD 5.2 §Duration puts a round
at about 6 seconds, which is the whole conversion:

| Field | Ticks at | Notes |
|---|---|---|
| `rounds` | the **caster's** turn end (parsed from `origin`; item/environment origins fall back to the target's) | reaching zero emits `EffectExpired(reason="duration")` |
| `turns` | the **target's own** turn end | independent of `rounds` — whichever counter hits zero first expires the effect |
| `seconds` | the caster's turn end, as `ceil(seconds / 6)` rounds | materialised once into `rounds`; `seconds` is never mutated. **If both are set, `rounds` wins** |

`flags["until_end_of_next_turn_of"] = "<entity_id>"` expresses SRD's "until the
end of your next turn": the effect expires at that actor's next turn end, with
a one-turn grace if it was applied during that actor's own turn.

Concentration-flagged effects are exempt from all of the above — the
concentration cascade and the per-turn repeat save own their lifetime, and the
imported packs carry display-only counters on them.

### Effects that end on an event or a turn boundary

A corpus effect may also say what ends it (midi-qol's DAE "special durations"),
and the effect carries it into combat as flags:

- `PassiveEffect.ends_on` → `flags["ends_on"]`: the effect ends after the first
  of these that happens to its bearer — `attacks` (makes an attack roll),
  `is_attacked` (an attack roll is made against it), `saves` (a saving throw,
  the concentration check included), `checks` (an ability check),
  `takes_damage`, `deals_damage`, `zero_hp` (drops to 0 Hit Points) and
  `casts_spell`. SRD 5.2 Invisibility, Sanctuary, Potion of Invisibility and
  Dust of Disappearance ship `attacks`, `deals_damage` and `casts_spell`;
  Hypnotic Pattern, Sleep, Eyebite's Asleep, Symbol's Sleep, Modify Memory,
  Sequester, Lolth's Sting's Unconscious, Turn Undead, Abjure Foes and Knock Out
  ship `takes_damage`; Gaseous Form ships `zero_hp`; Guiding Bolt ships
  `is_attacked`. A clause the events can't say (Turn Undead's "if you have the
  Incapacitated condition") is left to the host.
- `PassiveEffect.until` → a turn boundary, which then replaces the timed
  `duration`: `start_of_next_turn` (`flags["until_start_of_next_turn_of"]`,
  ends as the applying creature's next turn starts — Shield), `end_of_next_turn`
  (the `until_end_of_next_turn_of` flag above — Guiding Bolt) and `end_of_turn`
  (`flags["until_end_of_turn"]`, ends with the turn under way when it landed —
  Stinking Cloud). The SRD corpus also marks Color Spray, Flesh to Stone's
  successful save, Ray of Frost, Sunbeam, Brutal Strike's Hamstring Blow,
  Frost's Chill, Nature's Veil, Stunning Strike and Superior Hunter's Defense.
  "Your next turn" is always the applying creature's: SRD's "until the end of
  its next turn" (Vicious Mockery, Holy Aura's Blinded) has no `until` and keeps
  Foundry's own counter.

The engine reads the typed events after each resolution (the end of
`_fire_resolution_triggers`) and over the whole turn at its end, for a save or
damage no resolution passed it. Only events after the effect's own
`EffectApplied` count, so an effect a resolution grants is not ended by that
resolution's own roll, and one "until you next attack" counts for that attack
and ends after its damage. Each ending emits `EffectExpired(reason="duration")`,
then `ConditionRemoved` for each condition paired to the effect, and prunes it
from its caster's concentration (the caster keeps concentrating on the others).
A monster's multiattack is one resolution, so all of its attacks see an effect
that its first attack ends. A feature's or feat's always-on effect never
carries an ending.

### Effects that count only while something holds

A feature's or feat's always-on effect may count only while something holds of
its bearer. `PassiveEffect.active_while` (`ActiveWhile`) names it; every set
field must hold:

- `effect` — another effect is on the bearer, named by slug (matched against
  the effect's name or id): SRD 5.2 Mindless Rage "You have Immunity to the
  Charmed and Frightened conditions while your Rage is active" ships
  `{"effect": "rage"}`. Homebrew writes `wild-shape`, `bladesong` the same way.
- `bloodied` — SRD 5.2 Rules Glossary: "A creature is Bloodied while it has
  half its Hit Points or fewer remaining."
- `unless_incapacitated` — SRD 5.2 Danger Sense: "You have Advantage on
  Dexterity saving throws unless you have the Incapacitated condition" (a
  condition that implies Incapacitated counts).

The effect is seeded at `start_combat` like any always-on effect (even when
Foundry ships it `disabled` for a player to toggle, as Mindless Rage does), with
`flags["active_while"]`; its resistances, immunities and condition immunities
are seeded too, since the derived sheet leaves a conditional effect out.
Nothing is added or removed when the condition changes: each time a creature's
effects are read — the save, check, AC and damage-trait projection, an attack's
effects in play, and the condition immunities whenever an effect lands on or
leaves the creature — `conditional_passives.in_force` drops the ones whose
condition does not hold. An activity's trigger is not gated, and there is no
condition for worn armor, a held item, position or lighting (`BACKLOG.md`).

### Effects that depend on a condition

An applied effect whose other statuses and changes hold only while its target
has one condition names it in `PassiveEffect.while_status`. SRD 5.2 Hypnotic
Pattern: "have the Charmed condition for the duration. While Charmed, the
creature has the Incapacitated condition and a Speed of 0" ships `charmed`.
A target immune to that condition
(`is_condition_immune`: a raging Berserker's Mindless Rage) gets none of the
effect, so it is not Incapacitated and keeps its Rage.

## Concentration anchors, enchantments and forms

Every concentration spell concentrates. When a cast applies no concentration
effect of its own — *Spiritual Weapon*, a *Hold Person* every target saved
against — the engine puts an anchor on the caster: `effect:<spell>`, origin
`cast:<spell>:<caster>`, flags `concentration` and `concentration_anchor`, and
no changes. It joins `concentration_chain` and ends like any concentration
effect, and what depends on it (a *Spiritual Weapon* force) ends with it.

*Magic Weapon* lands on the creature it touches, with `enchanted_weapon`
naming the weapon (`PlayerIntent.weapon_id`); the engine applies the effect's
`system.magicalBonus` (upgrade) and `mgc` property changes to that weapon's
attacks only. A host can carry one into a combat:

```python
ActiveEffect(
    id="effect:magic_weapon_+1",
    name="Magic Weapon +1",
    origin="cast:magic_weapon_+1:char:hero",
    target_id="char:hero",
    changes=[
        ActiveEffectChange(key="system.magicalBonus", mode="upgrade", value="1"),
        ActiveEffectChange(key="system.properties", mode="add", value="mgc"),
    ],
    duration=ActiveEffectDuration(seconds=3600),
    flags={"enchanted_weapon": "longsword"},
)
```

A Wild Shape or *Polymorph* form rides `effect:wild-shape` or
`effect:polymorph`, with `transform_form` naming the Beast. The form's
statistics show on the creature's `initiative` entry, and the effect's expiry
restores its own. Seeding a form through `start_combat(active_effects=...)`
does not transform the creature (BACKLOG.md).

## Lifecycle and scope

Effects are **combat-scoped and engine-owned**. The engine holds active
effects in memory during the encounter; concentration and repeat-save linkage
key on `(target_id, id, origin)` identity. You can pass starting effects into
`start_combat(..., active_effects=...)` and read a combatant's current effects
during combat with `get_actor_active_effects(handle, entity_id)`.

When combat ends, `end_combat` returns the final tuple on
`EndCombatResult.final_active_effects` — but the engine itself discards them:
effects do not persist across combats. Cross-combat persistence is a host
concern, not the engine's. This is a deliberate boundary: the engine is the
in-combat effect authority and nothing more.

## Which changes the engine reads

An effect's `changes` use Foundry's keys, and a cast effect's changes count in
combat the same way a class feature's or a feat's do (a character's always-on
effects are seeded as permanent effects at `start_combat`, apart from the AC,
Speed, Hit Point, sense, resistance and condition-immunity keys the sheet
derivation already folds):

| Key | Read as |
|---|---|
| `system.attributes.ac.bonus` | a bonus to AC (Shield, Shield of Faith) |
| `system.attributes.ac.min`, `ac.calc` = `mage` | a floor under AC: 17, or 13 + Dexterity modifier (Barkskin, Mage Armor) |
| `system.bonuses.{mwak,msak,rwak,rsak}.attack` / `.damage` | a bonus to attack rolls / damage of that kind (Bless, Bane, Rage) |
| `system.bonuses.abilities.save`, `.check`, `.skill`; `system.abilities.<ab>.bonuses.save`; `system.skills.<sk>.bonuses.check` | a bonus to those D20 Tests (Bless, Guidance) |
| `system.abilities.<ab>.save.roll.mode`, `.check.roll.mode` | `1` advantage, `-1` disadvantage on that ability's saves or checks (Haste, Hex) |
| `system.traits.dr.value`, `di.value`, `dv.value` | resistance, immunity, vulnerability to the named damage type (Protection from Energy, Mind Blank) |
| `system.traits.ci.value` | immunity to the named condition while the effect lasts (Heroism) |
| `system.attributes.movement.walk` (add, multiply, override, downgrade, upgrade) | the walking Speed and the turn's movement budget (Longstrider, Haste, Slow) |
| `system.bonuses.spell.dc` | the caster's spell save DC |
| `flags.advantage.attack`, `flags.disadvantage.attack` | the bearer's own attack rolls |
| `flags.grants.advantage.attack`, `flags.grants.disadvantage.attack` | attack rolls against the bearer (Faerie Fire, Blur) |
| `flags.advantage.save[.<ability>]`, `flags.advantage.check[.<ability>]` and the `disadvantage` twins | the bearer's saves and checks (standalone `resolve_check`) |
| `statuses` | the conditions the effect imposes |

A flag counts when written as `override` or `add` with `true` or `1`, in any
spelling. Keys not listed (`system.attributes.hp.tempmax`, senses, languages,
item properties) are carried but not read; `BACKLOG.md` tracks them.
