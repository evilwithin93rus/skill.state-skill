---
title: Two-Key State Patch Contract
impact: CRITICAL
description: Every step emits one fenced JSON block with exactly two keys, state_patch and action. The patch is a dict of mutations where null deletes. No third key, no side channels, no free-text state updates.
tags: skill-state, state-patch, contract, json, null-deletion
---

# Two-Key State Patch Contract

> **Moral: two keys, or nothing. An envelope that can hold anything guarantees nothing.**

## Problem

If the model interleaves reasoning, actions and state updates in prose, the runtime cannot deterministically validate or apply the transition. Free-form `StateUpdate:` lines, optional keys and omitted deletions all make state application a matter of interpretation — and a runtime that interprets is a runtime that can be talked into corrupting itself.

## Incorrect

```text
// BAD: free-text inline update (the LangGraph-style baseline) — no fixed contract
Reasoning: The customer ordered item_12, I found it on shelf_42...
Action: Ship item_12 shelf_42
StateUpdate: {"inventory": {"shelf_42": null}}   // parsing is guesswork

// BAD: extra keys, duplicate mechanisms, narrative fields
{
  "state_patch": {"inventory": {"shelf_42": null}},
  "action": "Ship item_12 shelf_42",
  "confidence": 0.9,                              // not in the contract
  "thoughts": "I should ship this",               // reasoning, smuggled
  "deletes": ["inventory.shelf_42"]               // second deletion mechanism
}
```

## Correct

```json
{
  "state_patch": {
    "inventory": {
      "shelf_42": null
    }
  },
  "action": "Ship item_12 shelf_42"
}
```

## Contract rules

| Element | Rule |
|---|---|
| Envelope | Exactly one fenced JSON block per step. Reasoning goes outside it, never inside. |
| Top-level keys | **Exactly** `state_patch` and `action`. Not a superset. Not a subset. |
| `state_patch` | An object of mutations, deep-merged into Σ. A key set to `null` **deletes** it. Include only keys you are changing. |
| `action` | A single string: the exact command to execute. Validate it against the declared action space. |
| Deletion | Expressed **only** as `null`. Never a delete-list, never a sentinel string, never "I left it out". |
| Empty patch | `{}` is legal and meaningful: "this observation changes nothing I need to remember." |
| Ordering | The patch justifies the action. Mutate state first, then act on the mutated view. |

## The action is part of the contract, not an afterthought

`action` is a free string, which makes it the widest hole in an otherwise rigid envelope. Close it: declare the action space in `P` and reject anything outside it at validation time, before execution.

```text
action_space = ["Store", "Ship", "Move", "Wait"]
// "Ship item_12 shelf_42"  -> verb in space, accepted
// "rm -rf /"               -> rejected; Σ untouched, step retried
```

Without this check the two-key contract protects the state and leaves the environment wide open.

## Signalling completion

Two keys leaves no room for a `done` flag, so completion must live in the action space. Pick one and put it in `P`:

| Convention | When |
|---|---|
| A terminal action, e.g. `"action": "DONE"` | The agent knows it has finished |
| A no-op action, e.g. `"action": "Wait"` | Nothing to do this step but the episode continues |
| Runtime-side detection | The environment decides, e.g. the flag is submitted or the queue is empty |

An agent with no way to say "finished" either loops to the horizon or invents a key, which is a rejected patch. This is a real gap in a naive reading of the contract; close it explicitly.

## The patch commits before the action runs

The merge happens first, so Σ asserts an outcome the environment has not yet confirmed. This is deliberate — the model acts on the state view it just produced — but it means **a merged patch is not evidence the action succeeded.**

```text
Δ = {"inventory": {"shelf_42": null}}    // Σ now says the shelf is empty
a = "Ship item_12 shelf_42"              // ...and the environment rejects it
O_next = "Error: shelf_42 already empty" // reconcile on the next merge
```

Design `P` so that error observations are treated as authoritative corrections, exactly like external drift (`rules/env-drift-and-noise.md`). An agent that assumes its own optimistic commit is ground truth will drift away from reality one failed action at a time.

## Why

- Rigidity is what makes validation deterministic: the runtime knows precisely where to look and can reject everything else — `rules/validate-deterministic-rollback.md`.
- "Exactly two keys" is not stylistic. Extra keys widen the surface where small models emit malformed JSON, which accounts for 12% of open-weight failures (`references/evidence.md` §8).
- The prompt that elicits this envelope: `references/runtime-prompt-template.md`. Merge behaviour: `references/merge-semantics.md`. Both are enforced by `scripts/skillstate.py`.
