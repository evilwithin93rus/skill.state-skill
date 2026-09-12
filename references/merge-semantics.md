# Merge Semantics: the ⊕ Operator

`Σ_{t+1} = Σ_t ⊕ ΔΣ_t` is the **only** mechanism by which execution state changes. Single writer, no exceptions: no prose updates, no manual fix-ups, no side channels. Its semantics must be deterministic and implemented runtime-side (`rules/validate-deterministic-rollback.md`).

Reference implementation: `scripts/skillstate.py`. Every rule below has a corresponding assertion in `tests/run_tests.py`.

## Core rules

| Rule | Behaviour |
|---|---|
| **Null deletion** | A key set to `null` **deletes** it from the state, at whatever depth it appears |
| **Absent keys** | Keys not present in the patch are **untouched**. Patches are deltas, never replacements |
| **Deep merge** | Object × object merges recursively at every level; the patch names a path, the runtime walks it |
| **Leaf overwrite** | Scalars, strings and lists are replaced wholesale by the patch value |
| **Unknown keys** | Rejected at **validation** time: a key must exist in the authored schema |
| **Type changes** | Rejected at validation time when the patch value's type disagrees with the schema |
| **Atomicity** | The result is built from a copy and committed in one assignment. Σ is never partially mutated |

Validation and merging are separate stages, and the division is deliberate: `validate_patch()` enforces the *schema* (unknown keys, declared types, enums, nullability, write permissions), while `merge()` enforces only *structural* possibility. Call them in that order. A merge without a prior schema check will happily accept a key the schema never declared.

## Pseudocode

```text
function merge(state, patch):
  result = deep_copy(state)             # never mutate the input
  for key, value in patch:
    if value is NULL:
      delete result[key]                # deletion; a missing key is a no-op
    else if key not in result:
      result[key] = deep_copy(value)    # insertion
    else if is_object(result[key]) and is_object(value):
      result[key] = merge(result[key], value)      # recurse
    else if is_object(result[key]) != is_object(value):
      reject("type mismatch: cannot coerce object to leaf or back")
    else:
      result[key] = deep_copy(value)    # leaf overwrite
  return result
```

`deep_copy` on the patch value matters as much as on the state: without it, the new state aliases the patch object and a later mutation of the patch silently rewrites history.

## Worked examples

### Insert and overwrite

```text
Σ_t = {"inventory": {"shelf_41": "item_11"}, "mode": "idle"}
Δ   = {"inventory": {"shelf_42": "item_12"}, "mode": "shipping"}
Σ'  = {"inventory": {"shelf_41": "item_11", "shelf_42": "item_12"},
       "mode": "shipping"}
// shelf_41 survives: it was absent from the patch.
```

### Delete

```text
Σ_t = {"inventory": {"shelf_42": "item_12", "shelf_41": "item_11"}}
Δ   = {"inventory": {"shelf_42": null}}
Σ'  = {"inventory": {"shelf_41": "item_11"}}
```

### Delete versus absent — the distinction that costs 68% of failures

```text
Δ = {}                                  // no-op
Δ = {"inventory": {}}                   // no-op — does NOT clear the map
Δ = {"inventory": {"shelf_41": "item_11"}}
                                        // no-op for shelf_42! It is merely absent
Δ = {"inventory": {"shelf_42": null}}   // DELETES shelf_42
```

Deletion must be **stated**. "I left it out of my patch" means nothing to the operator — and a model that believes otherwise produces the single most common error in the entire system (`rules/failure-state-update-modes.md`).

### Deleting a subtree

```text
Σ_t = {"branches": {"feature_x": {"head": "abc", "ci": "pass"}}, "master": "def"}
Δ   = {"branches": {"feature_x": null}}
Σ'  = {"branches": {}, "master": "def"}
// One null removes the whole object. This is how a merged PR cleans up.
```

### Falsy values are values

```text
Σ_t = {"retries": 3, "note": "busy", "ready": true}
Δ   = {"retries": 0, "note": "", "ready": false}
Σ'  = {"retries": 0, "note": "", "ready": false}
// 0, "" and false are written. Only null deletes.
```

An implementation that tests truthiness instead of `is null` will refuse to write zeros — a bug that survives every happy-path test.

### Lists are leaves

```text
Σ_t = {"tags": ["a", "b"]}
Δ   = {"tags": ["c"]}
Σ'  = {"tags": ["c"]}        // replaced, NOT appended or merged
```

There is no element-wise list merge, by design: it would need index or identity semantics that no patch can express unambiguously. This is the concrete reason to prefer keyed maps — a map lets you address one entry, a list forces you to restate all of them, and restating is where keys get dropped (`rules/state-boundedness.md`).

## Atomicity

```text
// BAD: in-place mutation
for k, v in patch: Σ[k] = v          // raises at k=3 → Σ is now half-applied,
                                     // in a state no schema describes

// GOOD: copy, build, commit once
Σ_next = merge(deep_copy(Σ), validated_patch)
Σ = Σ_next                           // a single assignment; nothing to roll back
```

Rollback is not a recovery procedure here — it is the *absence* of a write. The old state was never touched, so there is nothing to undo. That property is what makes the retry loop safe to run unattended.

## Conflicts and concurrency

- **Single agent:** one writer per step, so no conflicts are possible. Order is parse → validate → merge → execute.
- **Multi-agent:** a shared execution state is a natural coordination substrate — far better than exchanging quadratic conversational transcripts. But concurrent writes need deterministic conflict resolution in ⊕ (last-write-wins per key path, or CRDT-style merge), which the single-agent design never exercised and the paper does not evaluate. Treat it as unsolved — `rules/when-not-to-use.md`.
- **Late-discovered type mismatch:** roll back to `Σ_t` and retry the step. `Σ_t` must never be partially mutated.

## Why

- Null-deletion is what makes "carry only what the future needs" executable. Finished facts are removed, which is the mechanism keeping `|Σ|` flat and the bounded-prompt guarantee true — `rules/prompt-bounded-o1.md`.
- Absent-key preservation is the structural defence against the dominant failure mode: it turns the model's most common mistake into a harmless no-op rather than data loss — `references/evidence.md` §8.
- Determinism is what lets the runtime own the schema. An operator with interpretive latitude cannot be a safety boundary.
