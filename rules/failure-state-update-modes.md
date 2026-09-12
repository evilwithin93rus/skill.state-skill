---
title: State Update Failure Modes
impact: HIGH
description: Three failure modes account for the structured-update errors observed on open-weight models - premature overwrite or deletion (68%), schema and type coercion (20%), and JSON syntax slips (12%). Each has a specific mitigation in the prompt, the schema and the validator.
tags: skill-state, failure-modes, overwrite, type-coercion, json-errors
---

# State Update Failure Modes

> **Moral: a patch that names everything erases something.**

## Problem

On open-weight models roughly 80% of execution failures are **structural**, not reasoning failures. The model understands the task and then mis-expresses the update. Left unmitigated these errors compound: one silent deletion at step 12 is inherited by every step after it, and the reasoning that would have exposed the mistake has been discarded.

Distribution measured on a 31B model at T=100 (`references/evidence.md` §8). Each mitigation belongs in a different layer, which is why all three layers must exist.

---

## 1. Premature overwrite / deletion — 68%

The dominant mode by a wide margin. The model treats the patch as a *replacement* for a subtree rather than a *delta* into it.

```text
Σ_t = {"inventory": {"shelf_1": "item_A", "shelf_2": "item_B"}}
// Intent: free shelf_2 only.

// BAD: the model resends the subtree it believes should remain
Δ = {"inventory": {"shelf_1": "item_A"}}   // shelf_2 not deleted — merge is a no-op!
// Or, worse, it "helpfully" sends the post-state minus the key:
Δ = {"inventory": {}}                      // also a no-op; nothing is cleared

// GOOD: name the mutation, nothing else
Δ = {"inventory": {"shelf_2": null}}       // shelf_1 untouched by ⊕
```

Both bad forms are *well-formed JSON that validates cleanly*. Only the semantics are wrong, which is exactly why the mitigation has to be in the prompt.

**Mitigations, all three layers:**

| Layer | Action |
|---|---|
| Prompt | State the delta rule literally, in the response contract: "set keys to null to delete; include only keys you are changing." |
| Merge | Deep merge with absent-key preservation. A replacing merge makes the model's mistake fatal instead of harmless. |
| Schema | Keyed maps over lists, so a single entry can be addressed without restating its siblings. |

---

## 2. Schema comprehension / type coercion — 20%

```text
// BAD: the schema says list, the model emits an object (or the reverse)
schema: "tested_hypotheses": "list"
Δ:      {"tested_hypotheses": {"cmd_1": true}}     // coercion — rejected

// BAD: a scalar where a map belongs
schema: "inventory": {"*": "str?"}
Δ:      {"inventory": "shelf_42 is empty"}         // rejected
```

**Mitigations:**

| Layer | Action |
|---|---|
| Schema | Keep types stable across the entire domain. Flat keyed maps where possible; when nesting is required, mirror real relations so the shape is guessable. |
| Prompt | The serialized Σ is itself the type declaration — the model copies the shape it sees. Never show a shape you will not accept. |
| Validator | Reject the coercion rather than accepting it. Silent coercion is how a list becomes a dict permanently. |

Ambiguous types are the root cause. A field typed `any` is an invitation.

---

## 3. JSON syntax / formatting slips — 12%

```text
// BAD
{ "state_patch": {...}, "action": "Ship item_12 shelf_42", }   // trailing comma
{ state_patch: {...}, "action": "..." }                        // unquoted key
{ "state_patch": {...}, "action": "...", "note": "fyi" }       // third key

// GOOD
{"state_patch":{"inventory":{"shelf_42":null}},"action":"Ship item_12 shelf_42"}
```

**Mitigations:** strict parse with rollback-retry (`rules/validate-deterministic-rollback.md`); grammar-constrained decoding where the stack allows it, which eliminates this class outright; and the smallest possible envelope — two keys is fewer places to go wrong than three.

---

## Triage

| Symptom | Mode | First fix |
|---|---|---|
| Previously stored keys have vanished | Overwrite | Deltas only; deep merge; never replace a subtree |
| A stored update had no effect | Overwrite (inverse) | The model omitted a key expecting deletion — it must emit `null` |
| Lists become objects, or nesting appears | Coercion | Stabilize schema types; reject rather than coerce |
| Repeated parse failures, retries climbing | Syntax | Tighten the envelope; constrained decoding |
| Retry budget exhausting regularly | Any of the three | The rejection messages are too vague to act on |

## Why

- Small-model degradation is a structured-output problem, not a capacity problem. Fixing structure recovers the runtime without changing the model (`references/evidence.md` §8).
- The two-key contract exists precisely to shrink the surface where these modes occur — `rules/patch-two-key-contract.md`.
- Mode 1 is why the merge operator is specified the way it is: absent-key preservation turns the most common model error into a harmless no-op instead of data loss — `references/merge-semantics.md`.
