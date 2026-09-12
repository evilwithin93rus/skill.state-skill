---
title: State Boundedness
impact: HIGH
description: O(1) prompt holds with respect to the horizon only if the state itself is bounded. Append-only list fields reintroduce O(T) growth and O(T^2) cumulative cost. Bound every field by the environment's state space, never by the number of steps taken.
tags: skill-state, bounded-state, sigma-growth, eviction, complexity
---

# State Boundedness

> **Moral: a list that only grows is a transcript wearing a schema.**

## Problem

The complexity guarantee is `|prompt_t| = O(|P| + |Σ| + |O|)`. That is independent of `t` **only if `|Σ|` is independent of `t`**. Nothing in the patch contract enforces this. A field the model appends to once per step makes `|Σ| = O(T)`, and cumulative cost returns to `O(T²)` — the exact failure the architecture exists to prevent, now hidden inside a well-formed schema.

This is a real gap in the source design, not a straw man. The paper's own InterCode CTF schema contains three append-only lists (`discovered_flags`, `tested_hypotheses`, `active_files`). It stays bounded in practice because CTF challenges are short and the environment's file count is small — measured average prompt was 813 characters, the smallest of any runtime. It would not stay bounded at T=200 in a large filesystem. Design for the bound; do not inherit it by luck.

## Incorrect

```text
// BAD: every field here is O(T)
{
  "tested_hypotheses": [ ...one entry per step, forever... ],
  "cmd_summary_log":   [ ...one entry per step, forever... ],
  "turn_count":        417,
  "events_seen":       [ ...one entry per observation... ]
}
// |Σ| grows linearly in steps → prompt grows → cumulative O(T²).
// The schema is well-typed, validates cleanly, and is still wrong.
```

## Correct

```text
// GOOD: every field is bounded by the ENVIRONMENT, not by the horizon
{
  "inventory":   { "shelf_0": null, ... "shelf_499": null },   // ≤ 500 slots, fixed
  "open_orders": { "order_7": "pending" },                     // ≤ concurrent orders
  "tested":      { "strings binary": "no flag",                // keyed by hypothesis,
                   "file binary":    "ELF64" },                // not appended per step
  "cwd":         "/home/ctf"                                   // scalar
}
// Re-testing a hypothesis overwrites its key instead of extending a list.
```

## The Boundedness Test

Apply to every field, alongside the sufficient-statistic test in `rules/schema-state-schema-authoring.md`:

> **"At T = 10,000, how large is this field?"**

| Answer | Verdict |
|---|---|
| Fixed by the environment (500 shelves, N branches, one cwd) | Bounded. Ship it. |
| Bounded by concurrency (open orders, in-flight PRs) | Bounded. Ship it. |
| Bounded by an explicit cap you enforce | Acceptable — document the eviction policy. |
| Grows with steps taken | **Unbounded. Redesign or cap it.** |

## Four Ways to Bound a Growing Field

| Technique | Use when | Example |
|---|---|---|
| **Key it, don't append it** | Entries are naturally unique | `{"tested": {"<cmd>": "<outcome>"}}` instead of `["<cmd>", ...]` — repeats overwrite |
| **Delete on completion** | Entries have a lifecycle | Ship the order → `{"open_orders": {"order_7": null}}` |
| **Cap with eviction** | History genuinely helps but only recently | Keep the last K; state the policy in `P` so the model maintains it |
| **Summarize into a scalar** | Only an aggregate is needed | `{"flags_found": 3}` instead of the full list, when the values are not reused |

Deletion is the mechanism the merge operator exists for. Null-deletion is not a convenience — it is how `|Σ|` stays flat. A runtime whose model never emits `null` has an unbounded state by construction.

## Enforce It in the Runtime

Boundedness is checkable, so check it. Do not hope.

```text
// After each merge:
if serialized_size(Σ) > SIGMA_BUDGET:
    log_violation(largest_fields(Σ))
    // fail loudly in development; the schema is wrong, not the step
```

A fixed `SIGMA_BUDGET` (characters or tokens) turns an asymptotic argument into a regression test. Pick it from the measured baseline: the paper's Warehouse state held ~1,800 characters across a 20× horizon increase (`references/evidence.md` §1).

## Why

- The proof of `O(T)` cumulative tokens (Eq. 6–7) takes `|Σ|` as a constant. An unbounded field invalidates the proof silently; nothing in validation catches it, because the patch is well-formed at every step.
- Unbounded fields defeat the noise result too: a growing `events_seen` re-imports the distractors that `rules/context-latest-observation-only.md` was supposed to drop.
- This is the same defect as `references/anti-patterns.md` #7 ("state as chat log"), seen from the complexity side rather than the content side.
