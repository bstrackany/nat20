# Reactions

Reactions work — opportunity attacks, Shield, Counterspell — but they work
differently from how a table plays them, and the difference shapes any
integration. Read this before wiring a host.

## The constraint: reactions are pre-armed

**The engine never pauses mid-resolution to ask whether a creature wants to
react.** There is no callback, no "do you want to cast Shield?" prompt, and no
mid-turn round-trip to the host.

This is deliberate. `submit_player_intent` and `advance_monster_turn` are the
only two ways into the engine, and each resolves to completion before it
returns. A reaction is therefore *armed in advance* and fires automatically when
its trigger occurs.

```python
# On the wizard's own turn, arm Shield against being hit.
await submit_player_intent(
    handle,
    actor_id="char:wizard",
    intent=PlayerIntent(
        intent_type="ready",
        spell_id="shield",
        reaction_trigger="hit_by_attack",
    ),
)

# Later, on the goblin's turn, the armed Shield fires by itself if the
# goblin attacks the wizard — no host involvement.
await advance_monster_turn(handle)
```

If you want a player to *choose* in the moment, your host has to ask them
before their turn ends and arm accordingly. You cannot ask them mid-resolution.

## Arming for the whole combat

`"ready"` is the SRD Ready action: it spends the Action and lasts one round.
Most reactions aren't readied at all — Shield, Hellish Rebuke, Uncanny Dodge
are simply taken when their trigger occurs — so an `"arm"` intent keeps one
armed for the whole combat at no cost, firing whenever its trigger occurs and
the Reaction is available, and re-arming at the owner's next turn start:

```python
await submit_player_intent(
    handle,
    actor_id="char:warlock",
    intent=PlayerIntent(intent_type="arm", spell_id="hellish-rebuke"),
)
```

No `reaction_trigger` is needed: the trigger comes from the reaction's own
typed condition in the corpus (`activation.reaction_conditions`, derived from
its text — "taking damage from a creature that you can see"). A granted
feature or a feat arms the same way with `feature_id` (and `activity_id` where
it has several); its use is spent when it fires. A reaction that answers a
creature resolves against the one whose act triggered it (Hellish Rebuke's
fire hits the attacker); a Self-range spell lands on the reactor. `"disarm"`
stands one (or all) down. Arming keeps the turn, and neither `"arm"` nor
`"disarm"` waits for it: any living combatant in initiative may submit them at
any point of the fight, before their first turn included.

A host that knows what a PC keeps armed lists it on the spec instead:
`PartyMemberSpec.armed_reactions=("uncanny-dodge", "shield")` seeds the same
standing reactions at `start_combat`, so a wizard who lost initiative still has
Shield in round 1. The order is the priority: when two armed reactions answer
one trigger, the first listed (or first armed) fires and the other waits for
the next occurrence; that order survives the turn-start re-arming.

## Reactions that reduce damage

A reaction whose activity carries `damage_reduction` (`dnd5e-srd-data`) does
not fire with the others: it waits for the damage it answers and changes it
before it lands. SRD 5.2 Uncanny Dodge: "When an attacker that you can see hits
you with an attack roll, you can take a Reaction to halve the attack's damage
against you (round down)." An armed Uncanny Dodge does nothing on a miss; on a
hit, the damage is rolled, halved, and only then folded through Resistance,
Vulnerability and Immunity, so the `DamageApplied` the host sees is the reduced
amount, preceded by `ReactionTriggered`.

| `mode` | Reduces by | SRD 5.2 |
|---|---|---|
| `"halve"` | half the damage, round down | Uncanny Dodge |
| `"roll"` | the activity's own `healing` roll (where Foundry writes a reduction) | Deflect Attacks, Deflect Energy, Stone's Endurance |
| `"to_zero"` | all of it | (a consumer's own content) |

The activity's `reaction_conditions` say which damage: `hit_by_attack` answers
the damage of an attack roll that hit, `takes_damage` any damage.
`attacker_seen` requires the reactor to see the attacker; `damage_types`
requires the damage to include one of them (Deflect Attacks' Bludgeoning,
Piercing or Slashing). The reduction spends the Reaction and the feature's use
(or the spell's slot) only when it fires, follows the same arming order as every
other armed reaction, and comes off the attack's damage as a whole, from the
weapon's own damage type first. A `"roll"` reduction draws its dice from the
combat's seeded RNG at that moment, after the damage it reduces was rolled.

Only the reduction resolves: a reaction's other activities (Deflect Attacks'
Redirect) do not. A reduction protects its owner only, not another creature.

The engine asks this through `ActivityResolutionContext.interrupts`
(`dnd5e_engine.interrupts.Interrupts`), one object an activity resolution
consults before an outcome stands. Its default interrupts nothing.

## Recognized triggers

`PlayerIntent.reaction_trigger` accepts a closed set:

| Trigger | Fires when |
|---|---|
| `"hit_by_attack"` | The reactor is hit by an attack (Shield) |
| `"cast_spell"` | Another creature casts a spell (Counterspell) |
| `"targeted_by_magic_missile"` | The reactor is targeted by Magic Missile |

Opportunity attacks are separate: they need no arming. Every walk that leaves
an enemy's reach draws one, on either side, unless the mover took the Disengage
action; a summon makes none. A character strikes with
`PartyMemberSpec.opportunity_attack_weapon_id`, else the first melee weapon in
its `equipment`, else an Unarmed Strike; a monster with the first non-ranged
attack on its stat block.

!!! note "This set is not data-driven"

    A new reaction spell cannot be added as data today — the trigger vocabulary
    is a `Literal` in the orchestrator, and Shield / Counterspell / Magic
    Missile carry named special cases. This is a known architectural gap,
    tracked in `BACKLOG.md`.

## Reaction economy

Each creature has one reaction per round, refreshed at the start of its turn —
the same budget an opportunity attack spends. Arming a reaction does not spend
it; firing does.

## Known limits

Two gaps are worth knowing about before you rely on these (both in `BACKLOG.md`):

- **Counterspell ignores its 60 ft range.** An armed reactor counters a cast at
  any distance.
- **A readied spell fires without a slot.** The reaction spends a spell slot
  when one is available but fires regardless, so a reactor with an empty pool
  gets a free cast.

Neither is reachable by accident — the host chooses to arm the reaction — but
neither is gated by the engine.

For the full design, including how the queue drains relative to attack
resolution and why Shield's AC bonus lands before the hit/miss comparison, see
the [reaction queue design note](../dev/reaction-queue.md).

