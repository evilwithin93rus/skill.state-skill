---
title: Discard Reasoning After the State Transition
impact: CRITICAL
description: Reasoning is a transient computation whose only product is the validated state patch. Once the patch is applied, the trace is destroyed and never appears in any later prompt. Reasoning must never be stored in state.
tags: skill-state, reasoning, discard, chain-of-thought, transient
---

# Discard Reasoning After the State Transition

> **Moral: think freely, then let the thought go. Keep only what you wrote down.**

## Problem

ReAct-style runtimes keep every reasoning trace in context forever. Old reasoning is not neutral ballast: it is stale, confidently worded, and competes with the current observation for attention. It drives quadratic token growth *and* degrades accuracy. The insight the architecture turns on is that reasoning has a **surviving product** — the patch — and the trace itself is scaffolding.

Note what is *not* being cut: within-step reasoning is fully intact and multi-step. Deliberate as long as the task needs. The discipline is about what crosses the step boundary.

## Incorrect

```text
// BAD: the trace persists into the next prompt
step_1: reasoning("check shelf_42 first because...") → appended to history
step_2: prompt = instructions + history(including step_1 reasoning) + ...
// Now context poison: possibly obsolete, unconditionally expensive.

// BAD: reasoning smuggled into state — the same failure wearing a schema
state_patch = {
  "reasoning_log": ["I think the item is on shelf_42..."],
  "inventory": {"shelf_42": "item_12"}
}
```

The second form is the one that survives code review, because it looks structured. It is a transcript with extra steps: it grows once per turn, it re-enters every future prompt, and it voids the bound.

## Correct

```text
// GOOD: full reasoning, then deliberate destruction
generate(R_t, ΔΣ_t, a_t)      // R_t = free multi-step chain of thought
validate(ΔΣ_t)                 // deterministic schema check
Σ_{t+1} = Σ_t ⊕ ΔΣ_t          // the patch is the only survivor
execute(a_t)
discard(R_t)                   // gone from every future prompt, permanently

// the next prompt is exactly: P + compact(Σ_{t+1}) + O_{t+1}
```

## Lifecycle

| Artifact | Alive during | After the validated transition |
|---|---|---|
| Reasoning `R_t` | The current generation only | **Destroyed** |
| Patch `ΔΣ_t` | The current step | Merged into Σ — the only survivor |
| Action `a_t` | The current step | Gone; its effects arrive as `O_{t+1}` |
| Observation `O_t` | The current prompt | Replaced by `O_{t+1}` |

## The test for "does this reasoning matter?"

Ask it of every conclusion, every step:

> **Will a future step decide differently if it does not know this?**

- **Yes** → it is not reasoning any more, it is state. Put it in the patch, in a typed field, now. There is no second chance (`rules/when-not-to-use.md`, insufficiency mode 2).
- **No** → let it die with the trace. Storing it dilutes attention on every remaining step and costs tokens T times.

The whole architecture is this question, asked once per turn, answered by the schema.

## What you give up

Say it out loud so it is a decision rather than a surprise: **the trace is unrecoverable.** You cannot audit why Σ holds a value, because the argument was destroyed. That is fine for forward execution and disqualifying for provenance work (`rules/when-not-to-use.md`), and it is what makes a poisoned field unfalsifiable (`rules/untrusted-observations.md`).

If you need the trace for offline debugging, log it **outside** the runtime — to a file, a trace store, stderr. The invariant is not "reasoning is never written down". It is **no prompt is ever built from it.**

## Why

- Reasoning that matters is projected into structured state; reasoning that does not would only dilute attention later. Projection, not retention.
- The prompt stays flat in the horizon: `|prompt| = O(|P| + |Σ| + |O|)` — `rules/prompt-bounded-o1.md`.
- Tell the model. The prompt must say reasoning "will be discarded after execution"; that single clause frames the trace as scratch space rather than output (`references/runtime-prompt-template.md`).
- A `notes` or `log` field re-imports everything you just deleted — `references/anti-patterns.md` #6, `rules/state-boundedness.md`.
