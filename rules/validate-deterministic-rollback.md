---
title: Deterministic Validation with Rollback-Retry
impact: HIGH
description: Schema ownership and validation live in the runtime, never in the model. A malformed or off-schema patch is rejected with actionable feedback and retried against an untouched state. The retry budget must be finite.
tags: skill-state, validation, rollback, runtime, determinism
---

# Deterministic Validation with Rollback-Retry

> **Moral: never let the storyteller keep the ledger.**

## Problem

If the model's output is applied as-is, one malformed block or one coerced type silently corrupts Σ — and every later step inherits the corruption, conditioned on it, unable to see it. A rejected patch costs one retry. A corrupted state costs the rest of the episode.

This is the guarantee that makes the pattern safe to run unattended: *"because schema ownership and validation reside in the deterministic runtime rather than the model, malformed outputs cannot corrupt persistent state Σ_t; an invalid patch triggers a rollback-retry cycle."*

## Incorrect

```text
// BAD: trust the model's patch
Δ = parse(model_output)            // no schema check
Σ = merge(Σ, Δ)                    // wrong types now live in Σ forever
execute(Δ.action)

// BAD: let the model adjudicate its own output
"Model: I have updated the state correctly."   // a self-report is not a check

// BAD: mutate in place and hope
for k, v in patch: Σ[k] = v        // raises at k=3 → Σ is now half-applied
```

## Correct

```text
// GOOD: parse → validate → merge, with a bounded retry
for attempt in range(max_retries + 1):
    response = generate(prompt(P, Σ_t, O_t))
    try:
        Δ, a = parse_strict(response)        // one fenced block, exactly two keys
        validate(Δ, schema, runtime_owned)   // keys, types, nullability, permissions
        validate_action(a, action_space)     // close the string-shaped hole
    except ContractError, ValidationError as e:
        O_t = "Your previous response was rejected: " + str(e)
        continue                             // Σ_t never saw Δ
    Σ_{t+1} = Σ_t ⊕ Δ                        // build a new object; commit atomically
    O_{t+1} = execute(a)
    discard(response.reasoning)
    break
else:
    escalate()                               // budget exhausted; Σ_t still pristine
```

The ordering is load-bearing, and so is the fact that every `raise` happens **before** the first write.

## Validation checklist

| Check | Reject when |
|---|---|
| Envelope | Not exactly one fenced JSON block; not exactly `state_patch` + `action` |
| Syntax | Trailing commas, unquoted keys, truncated delimiters |
| Keys | The patch names a key absent from the schema |
| Types | A patch value's type disagrees with the schema (list ↔ dict is the common one) |
| Enums | A value outside a closed set |
| Nullability | Deleting a slot the schema declares non-nullable |
| Permissions | The patch names a runtime-owned field — `rules/untrusted-observations.md` |
| Action | The command is outside the declared action space |
| Budget | The merged state exceeds the declared `|Σ|` budget — `rules/state-boundedness.md` |

`scripts/skillstate.py` implements all nine; `tests/run_tests.py` asserts each one rejects.

## Rejection messages are prompts

The error becomes the next observation, so it is a prompt and should be written like one: name the violation, name the location, name the correction.

```text
// BAD — the model cannot act on this
"ValidationError: invalid patch"
"KeyError: 'notes'"

// GOOD — states the rule and the fix
"notes is not in the schema. Allowed keys at $: active_files, cmd_summary,
 discovered_flags, tested, working_dir."
"working_dir is declared str and is not nullable; it cannot be deleted.
 Omit the key to leave it unchanged."
```

A vague rejection turns a one-step retry into an exhausted budget. Treat every message as documentation delivered exactly when it is needed.

## Bound the retry

An unbounded retry loop is a live-lock that burns tokens at full rate while making no progress, and it is the easiest thing to leave out.

| Requirement | Detail |
|---|---|
| Finite budget | 2–3 attempts. Structural errors that survive three targeted corrections are a schema or prompt defect, not a transient slip. |
| Distinct feedback each time | Repeating the same message invites the same output. Include what was rejected. |
| Escalate, do not fake progress | On exhaustion: raise, alert, or fall back — never merge a partial patch to keep moving. |
| Σ untouched throughout | After N failed attempts the state must be byte-identical to before attempt 1. |

## Why

- Self-corrupting state is unrecoverable: every later decision is conditioned on a poisoned Σ, and the reasoning that could have exposed it was discarded (`rules/reasoning-discard-after-transition.md`).
- On open-weight models roughly 80% of failures are structural rather than reasoning failures (`references/evidence.md` §8). Strict validation plus grammar-constrained decoding removes that class outright — it recovers the runtime, not the model.
- Build the next state from a copy and commit it in one assignment. Partial mutation is the one failure mode this rule exists to make impossible — `references/merge-semantics.md`.
