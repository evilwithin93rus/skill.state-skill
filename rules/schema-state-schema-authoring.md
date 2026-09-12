---
title: State Schema Authoring
impact: CRITICAL
description: Author one static schema per domain, never per task. Include only fields required for future execution. Each field must pass the deletion test, the sufficient-statistic test and the boundedness test.
tags: skill-state, schema, state-design, sufficient-statistic
---

# State Schema Authoring

> **Moral: keep what the future needs. The past is not an asset.**

## Problem

Most runtimes let execution state emerge implicitly from the transcript, or invent an ad-hoc schema per task. Without a domain-authored schema the model must rediscover what state matters at every step, and obsolete facts leak into every future decision. The schema is the single highest-leverage artifact in the whole architecture: it decides what the agent can still know at step 200.

## Incorrect

```text
// BAD: per-task, free-form, unbounded
state = {
  "chat_history": [...],           // a transcript, not execution state
  "notes": "...",                  // unstructured dumping ground
  "everything_seen_so_far": {...}, // obsolete facts survive forever
  "turn_count": 42                 // describes the past, not the world
}

// BAD: reinvented for every task in the same domain
task_1_schema = { "flag": null }
task_2_schema = { "found_things": [], "cwd": "", "history": [] }
```

## Correct

```text
// GOOD: one static schema, authored once for the whole domain.
// The five fields below served all 100 InterCode CTF challenges —
// reverse engineering, forensics, crypto and pwn.
state_schema = {
  "discovered_flags": {"*": "str?"},   // outcomes needed later
  "tested":           {"*": "str?"},   // hypothesis -> outcome; prevents repeats
  "active_files":     {"*": "str?"},   // the current working set
  "working_dir":      "str",           // position in the environment
  "cmd_summary":      "str"            // what the last action did
}
// Keyed maps rather than lists: a repeated hypothesis overwrites its entry
// instead of appending, so |Σ| is bounded (rules/state-boundedness.md).
```

## Three tests every field must pass

Apply all three. A field that fails any one of them does not belong.

| Test | Question | If it fails |
|---|---|---|
| **Deletion** | If this field vanished, would a future step decide worse? | Remove it — it is decoration. |
| **Sufficiency** | When this fact becomes relevant, does a slot already exist so the patch can commit it at *first* observation? | The schema is incomplete, or the task is not a fit — `rules/when-not-to-use.md`. |
| **Boundedness** | At T = 10,000, how large is this field? | Redesign as a keyed map, or cap it — `rules/state-boundedness.md`. |

A schema where every field passes all three is minimal, sufficient and flat. That is the entire specification.

## Shape decisions

| Situation | Shape |
|---|---|
| Many independent variables (500 shelves, N files) | Flat keyed map, `null` for empty slots. Most resistant to type coercion. |
| Densely entangled variables (branches ↔ PRs ↔ CI) | Nested object mirroring the real dependencies, so one patch can express a cascade. |
| A value from a closed set | Enum, never a free string. `"ci": ["pass","fail","pending"]`. A closed type cannot be talked into a new value. |
| Something that accumulates per step | It does not belong in state. Re-derive it, or key it so repeats overwrite. |
| Structure genuinely unknowable upfront | The pattern does not apply — `rules/when-not-to-use.md`. |

## Write permissions are part of the schema

Decide per field whether the **model** may write it or only the **runtime**. Policy, identity, permissions and anything security-relevant are runtime-owned, and validation must reject any patch that names them. This costs one lookup and closes an entire failure class — `rules/untrusted-observations.md`.

## Author it once, then lint it

```bash
python scripts/skillstate.py lint --schema my_schema.json
```

The linter reports fields that can grow with the horizon and fields left untyped. Fix them before writing the loop; a schema defect is unrecoverable at step 200, whereas a loop defect is a stack trace.

## Why

- Domain-level schemas amortize: one 5-field schema across 100 diverse tasks (`references/evidence.md` §5). Per-task schemas multiply the surface where the 20% type-coercion failure mode lives.
- Fields that only describe the past grow with the horizon and void the bounded-prompt guarantee — `rules/prompt-bounded-o1.md`.
- Stable types across the whole domain are what let the model pattern-match the shape it sees in the serialized state; inconsistent nesting is what produces list/dict coercion — `rules/failure-state-update-modes.md`.
- Worked schemas for three domains, with the paper-verified parts marked: `references/schema-examples.md`.
