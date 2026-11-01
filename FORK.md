# This fork of nat20

Party of None's rules engine is this fork of [tapestria/nat20](https://github.com/tapestria/nat20),
branched from upstream `e187c81` (the 0.7.0 close-out, 2026-10-09). Every change is on `main`,
on top of upstream, with tests, a `CHANGELOG.md` `[Unreleased]` entry in the package it touches
and the relevant `docs/` page. Both package gates (`make check` in `packages/dnd5e-engine` and
`packages/dnd5e-srd-data`) pass at every commit. The whole set is meant to go upstream as one
PR, so what matters is the aggregate diff against `e187c81`: **80 files, +4,096 / −140** —
about 1,900 lines of source (of which ~440 are comments and docstrings), 1,370 of tests, 430 of
docs, 200 of canonical data.

**Rule:** nat20 is not our code. Any change here, however small, is discussed with and agreed
to by Ben first; Party of None works around engine gaps or proposes the engine change and waits.
Changes are kept surgical: new behaviour lives in a new module or object, the engine calls it at
the existing lines, and no upstream function is deleted or rewritten. Open proposals live in
`../nat20_engine_proposals.md`.

## Why we changed the engine at all

Upstream's design claim is "new content is a data change, not an engine change", and the table
owns a lot of content (2024 subclasses, feats, every spell) that it wants resolved by the engine
from its own text, not played by an AI from memory. The shipped engine resolved spells well and
characters thinly: a spell was all-or-nothing at cast time, a feature's or feat's always-on text
never reached a roll, reactions had to be readied with an Action, homebrew entries had no
legitimate provenance, and the monster AI could not be replaced. The commits below close those
gaps so that homebrew is a JSON/YAML file and a host can bring its own monster brain.

## The commits

| # | Commit | What changed | Where to look |
|---|--------|--------------|---------------|
| 1 | `84292a7` cast activity choice | A `cast_spell` honours `PlayerIntent.activity_id`: one named activity of a multi-activity spell resolves (Sunbeam's initial cast, Call Lightning's bolt), as `use_feature` already did. `CastFailedReason "activity_invalid"` for an id the spell lacks. | `orchestrator.py` `_cast_activities`, `_reject_unknown_cast_activity`; `tests/test_activity_triggers.py`; `docs/concepts/activities.md` |
| 2 | `d93cf13` activities that fire later | Data: `Activity.trigger` (`ActivityTrigger`: turn_start / turn_end / hits / hit_by_attack / takes_damage / later_use, with subject, melee/ranged, once, once_per_turn, ends_on_save, also_at_cast); 13 SRD spells marked via the translator table `_ACTIVITY_TRIGGERS` (Acid Arrow, Hex, Hunter's Mark, Sunbeam, Haste's lethargy …). Engine: `triggers.py` `HeldUse`; a cast, feature or item use whose activities carry triggers is held and fired at its moment by turn hooks (`engine:activity-triggers-turn-start/-end`) and by `_fire_resolution_triggers` after attacks and damage; `ActivityTriggered` event; `AttackRolled.attack_type`. A `later_use` activity only holds back from the cast (Haste's lethargy, Hex's re-mark); re-firing it on a later turn is not supported (see 12). | `schema/common.py`; `tools/translators/foundry.py`; `engine/triggers.py`; `orchestrator.py` `_hold_use`, `_fire_triggered_activity`, `_fire_turn_triggers`; `docs/concepts/activities.md` |
| 3 | `8e89342` effect changes count in combat | Effect change keys an applied effect carries are read in combat: `dr/di/dv` traits, per-ability save/check roll modes, `ac.min` / `ac.calc` (Mage Armor's floor), every `movement.walk` mode, `traits.ci.value` (condition immunities), `flags.grants.{advantage,disadvantage}.attack` on the bearer (Blur, Faerie Fire). `_effects_in_play` reads the actor's and the targets' effects. A Self-range cast lands on the caster. `rounds` beside `seconds`: the longer wins (Longstrider). Translator corrections for Blur and Faerie Fire. | `activities/attack.py` `target_grants_flags`; `activities/check.py`; `activities/effects.py` `_duration_from_passive`; `types/effects.py` `flag_change_is_set`; `tests/test_effect_changes_in_combat.py`; `docs/concepts/effects.md` |
| 4 | `8413d76` features and feats always-on | `_seed_always_on` seeds permanent effects at `start_combat` for every granted feature and held feat whose passive changes are on an allowlist (`system.bonuses.*`, `flags.advantage/disadvantage/grants.*`, `dv`, save/check roll modes), and holds a permanent use for their triggered activities, so "when you hit, add …" riders on features fire for the whole combat. Data: `Feat.passive_effects`; 17 SRD feats carry theirs. | `orchestrator.py` `_seed_always_on`; `build_spec.py` `_always_on_feat_changes`; `schema/feat.py`; `tests/test_always_on_sources.py` |
| 5 | `98bba6d` reactions from data, homebrew provenance | `arm` / `disarm` intents (free, turn-keeping) keep a spell, feature or feat reaction armed for the whole combat, its trigger read from the corpus's typed `activation.reaction_conditions` (`_REACTION_KIND_TO_TRIGGER`), re-armed at the owner's turn start; `ReactionTrigger` gains `takes_damage`; a readied reaction answers the creature that triggered it, Self range lands on the reactor. Data: `Provenance.source` admits `homebrew`, `license_tag` `proprietary` / `unspecified`, `srd_version` may be empty. | `orchestrator.py` `_handle_arm`, `_hook_rearm_standing_reactions`, `_resolve_readied_feature`; `tests/test_standing_reactions.py`; `schema/common.py` Provenance; `docs/concepts/reactions.md` |
| 6 | `8ce72a6` fix: token resolution when seeding | A seeded always-on change with an `@abilities.cha.mod`-style value (Aura of Protection) is resolved through `evaluate_uses_formula` instead of crashing the first save; an unresolved token is skipped with a warning. | `orchestrator.py` `_passive_change_to_active_change`; `tests/test_always_on_sources.py` |
| 7 | `96b1908` a feat's own action | `use_feature` accepts a feat the character holds (its activity's own `uses` is the cap); `CharacterBuildSpec.feats` / `make_build_spec(feats=)` so a sheet's feats join the derived sheet and the party spec. | `orchestrator.py` `_resolve_feature_invocation`; `build_spec.py`; `tests/test_always_on_sources.py` |
| 8 | `cafe775` armed reactions on the spec | `PartyMemberSpec.armed_reactions` (ordered = firing priority) seeded at `start_combat`; `arm` / `disarm` accepted out of turn, before the owner's first turn included; turn-start re-arming keeps the arming order. | `specs.py`; `orchestrator.py` `_seed_armed_reactions`, `_OUT_OF_TURN_INTENTS`; `tests/test_standing_reactions.py`; `docs/concepts/reactions.md` |
| 9 | `5406f88` monster AI seam | `MonsterAI`: one object with a method per decision of a driven monster turn (flee, target, action ranking, spell, aim, move plan), installed with `set_monster_ai`. Defaults call the engine's existing functions unchanged; `advance_monster_turn` swaps six call lines and keeps every rule. | `monster_ai.py` (new); `orchestrator.py` six call sites; `tests/test_monster_ai.py`; `docs/concepts/monsters.md` |
| 10 | `fac9a4c` trim: no re-casting of `later_use` | Removed the machinery that let a caster re-fire a `later_use` activity by casting again (`_later_use_of`, the action-cost / slot / Counterspell / concentration branches it threaded through the cast path). Such a `cast_spell` is refused `activity_invalid`. The flag still means "not at cast". −214 lines. | `orchestrator.py`; `tests/test_activity_triggers.py`; `tests/e2e/test_c01_dataset_fixes.py` |
| 11 | `1a2189b` trim: shared context builder | `_fire_triggered_activity` builds its activity context through the engine's existing `_pc_attack_context_kwargs` instead of by hand, so a fired activity also sees cover, visibility, Dodge and the other per-attacker state. Known gap: a triggered *attack* activity (none exists today) would read a Help/Vex grant without spending it. | `orchestrator.py` `_fire_triggered_activity` |
| 12 | `d1d6720` trim: tests | The fork's tests cut from 1,969 to 1,315 lines: shared setup through upstream's `tests/c20_support.py` and a small `tests/fork_support.py`, parametrised tables for the SRD trigger sentences, AC floors and provenance, duplicates removed. Per-file coverage of our modules unchanged. | `tests/fork_support.py`; the fork's test files |
| 13 | `41b267b` Color Spray blinds | The pinned Foundry effect was named Blinded and carried no status, so a failed save did nothing. `_EFFECT_STATUS_CORRECTIONS` (beside the change-corrections table) appends `blinded`; canonical patched; data CHANGELOG. | `tools/translators/foundry.py`; `canonical/spells/color-spray.json`; `tests/test_effect_change_corrections.py` |
| 14 | `3a1f76f` Ranger's Hunter's Mark | Favored Enemy says "You always have the Hunter's Mark spell prepared", but Foundry's advancement omits the grant that Paladin's Smite, Druidic etc. carry. `_MISSING_GRANTS` in `tools/regen.py` appends it to the resolved grants; canonical patched; data CHANGELOG. | `tools/regen.py`; `canonical/classes/ranger.json`; `tests/test_class_feature_ownership.py` |
| 15 | `0a47849` Sphere on a space | A `cast_spell`'s `target_zone_id` centres a Sphere or Cylinder there (Fireball "a point you choose within range"); before, with no single named creature the burst silently centred on the caster. A space out of range or sight is refused `out_of_range` before the slot is spent. | `orchestrator.py` `_area_point`, `_area_origin`, `_spell_out_of_range`; `tests/test_area_point.py`; `docs/concepts/grid.md` |

`docs/capabilities.md` rows were amended in commits 2, 3, 5 and 10 ("Activities that fire later"
⚠️, "Reactions from data" ⚠️, custom trigger kinds ❌).

## How Party of None leans on each

- Homebrew spells with triggers and self-targeting (2, 3); 133 modelled so far.
- Subclass features and feats modelled from the table's books as `activities` +
  `passive_effects` (2, 4, 7): 354 features, 192 feats, checked by
  `party-of-none/scripts/check_features.py`.
- Reactions armed for every character as the fight opens, with a per-character priority
  order on the sheet (5, 8).
- The table's fair/deadly targeting is a `MonsterAI` subclass (9) instead of a monkey-patch.
- The overlay's entries carry `source: homebrew`, `license_tag: proprietary` (5).

## Still worked around on the Party of None side (not engine changes)

Two SRD movement rules the closing walk ignores are monkey-patched in
`party-of-none/src/party_of_none/rules.py`: a monster may end its move in an occupied square
(`_forbid_stacking`) and paths straight through its allies (`_route_around_creatures`). They are
rules, not AI preference, and belong in `_take_walk_step` / the path planner — proposal 27 in
`../nat20_engine_proposals.md`. A better default monster AI (heals, spreads damage, aims areas
only when they catch two) will be a `MonsterAI` subclass in Party of None, not an engine change.

## Running the gates

```sh
cd packages/dnd5e-engine && make check      # ruff, mypy, pytest with coverage floor, bandit
cd packages/dnd5e-srd-data && make check
```

`scripts/smoke_clean_install.sh` needs the project's Python 3.14 first on `PATH` (Homebrew's
3.15 breaks `lark`). Unset `VIRTUAL_ENV` if another project's venv is active.
