---
title: Latest Observation Only
impact: HIGH
description: Each step the model receives exactly (P, Sigma_t, O_t). No prior observations, actions or reasoning traces, not even one turn of them. Distractors are filtered during patch generation and never re-enter a prompt.
tags: skill-state, context, observation, noise, attention
---

# Latest Observation Only

> **Moral: one fresh fact outranks a thousand remembered ones.**

## Problem

Replaying past observations forces the model to distinguish current facts from historical artifacts on *every* step. That discrimination is not free and it is not reliable. Under dense telemetry the ReAct-style runtime fell from 0.68 to 0.53 on Warehouse and from 0.76 to 0.11 on the software-repository domain, while every state-carrying runtime held (`references/evidence.md` §3).

The mechanism is simple: in an append-only context a distractor seen once is re-read T times. Attention paid to it is paid again every step, forever.

## Incorrect

```text
// BAD: history replay in any form
prompt = P + Σ + O_t + "Previous observations: [O_1 ... O_{t-1}]"
prompt = P + Σ + O_t + "You previously ran: <cmd_1>; <cmd_2>; ..."
prompt = P + Σ + O_t + "Recent history: last 3 turns"
// Even one turn of replay re-imports obsolete facts and drift risk,
// and establishes the buffer that will grow.
```

There is no safe amount. A window is a growth curve with a flatter slope.

## Correct

```text
// GOOD: exactly three inputs
A_t = (P, Σ_t, O_t)
P   = skill.instructions      // immutable: persona, action space, environment rules
Σ_t = compact serialized state
O_t = the latest observation, and nothing older

// Distractors are seen exactly once, in O_t, and either:
//   - projected into Σ because they matter for future execution, or
//   - dropped entirely, because the patch simply does not mention them.
// Either way they never appear in a later prompt.
```

Dropping is the default and requires no mechanism: a fact not named in the patch ceases to exist at the end of the step.

## The four objections, answered

| Objection | Answer |
|---|---|
| "The model will forget earlier events." | Anything durable was patched into Σ at first observation. If that did not happen, the task violates the sufficient-statistic assumption and the pattern is the wrong choice — `rules/when-not-to-use.md`. |
| "What about state drift behind the agent's back?" | A corrective observation arrives as `O_t` and overwrites Σ on the spot. Recovery is zero turns against 5–14 for history runtimes — `rules/env-drift-and-noise.md`. |
| "What about dense noise?" | Noise is filtered at patch time and never persisted, so it cannot drag attention later. Robust, though not immune: on entangled state the score fell from 0.90 to 0.80 at 50 events/turn. |
| "Doesn't the agent need to remember its own last action?" | Its effects arrive in the next observation. A single `cmd_summary`-style scalar covers the rest — bounded, unlike an action log. |

## What must be in `P`, not in history

Everything the agent needs on every step and that never changes. This is what makes history unnecessary rather than merely forbidden.

- Persona and objective
- The complete action space, and the completion convention
- Environment rules and constraints
- The state schema shape, so the model can pattern-match what it is patching
- The response contract, including that reasoning will be discarded

If the agent needs history to behave correctly, the usual cause is an underspecified `P`, not an insufficient Σ. Fix `P` first.

## Why

- History is not neutral. Obsolete facts in the prompt actively overpower contradictory new observations — the direct cause of multi-turn hallucination after drift.
- Context management is the **only** independent variable in the measurements: `skill.instructions` is byte-identical across all four runtimes compared. The gains are attributable to bounded, noise-free context and nothing else.
- Shortness alone is not the mechanism. At an identical budget, truncation scored 0.18 and statistical compression 0.22, against 0.94 — because both destroy facts that a schema would have preserved (`references/evidence.md` §6).
- Exact prompt construction: `references/runtime-prompt-template.md`. The loop that maintains it: `references/execution-loop.md`.
