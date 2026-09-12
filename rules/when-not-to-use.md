---
title: When NOT to Use SKILL.state
impact: HIGH
description: The gate. SKILL.state assumes the execution state is a sufficient statistic for future action. It fails when the schema is unknowable upfront, when relevance is recognized late, or when the task is defined over the trajectory itself. Also covers the horizon and model-size thresholds below which it buys nothing.
tags: skill-state, limitations, applicability, when-not-to-use, gate
---

# When NOT to Use SKILL.state

> **Moral: forgetting is free only when nothing you forgot still matters.**

Read this first. It is cheaper to reject the pattern now than to discover at step 40 that the fact you needed was discarded at step 3.

## The assumption

Discarding history is lossless **only** when Σ is a *sufficient statistic* for future execution: everything in the past that bears on future actions can be projected into the schema **as soon as it becomes known**. Outside that boundary the architecture silently loses information the task required, and no validation, retry or merge rule can recover it.

## Three ways the assumption fails

### 1. The schema is not knowable in advance

```text
// BAD: forcing a fixed schema onto open-ended exploration
state = { "important_facts": [...] }   // you do not yet know what matters
// Mid-run you need "protocol_version" — no slot ever existed,
// so no patch ever committed it.
```

If the structure of the state must be *discovered* during execution, upfront authoring is impossible. Use history, or a hybrid: bounded state for the known part, a small strictly-typed scratch field for the rest.

### 2. Relevance is recognized late

```text
// Step 3:  O_3 = "build tag v0.9.2-rc1 emitted"   → ignored, not patched
// Step 40: the task turns out to be about that exact tag
// The observation is gone. History would still have it.
```

This is the failure with no remedy. A patch cannot retroactively commit an observation that was never shown again. Widening the schema to catch more at first sight (an `open_questions` or `hypotheses` field) trades boundedness for recall — measure it rather than assuming it.

### 3. The objective is defined over the trajectory

Auditing, debugging provenance, incident postmortems, regulatory replay, "explain why you did that". Here the interaction history **is** the deliverable. Discarding reasoning destroys the product. Note this is worse than losing a nice-to-have: because reasoning is discarded, a wrong value in Σ is also *unfalsifiable after the fact* — see `rules/untrusted-observations.md`.

## Decision table

| Task shape | Use it? |
|---|---|
| Sequential procedural execution, stable domain | **Yes** |
| Long horizon with noise and external drift | **Yes — strongest advantage** |
| Task must survive context-window exhaustion or a session restart | **Yes — even in Mode B** |
| Open-ended exploration, state structure unknown | No — or hybrid |
| Facts whose relevance surfaces only later | **No** |
| Output defined over history (audit, provenance, replay) | **No — history is the product** |
| Multi-agent concurrent writes | Partial — shared state is a good coordination substrate, but ⊕ needs conflict-resolution semantics the single-agent design never exercised and the paper never evaluated |

## Thresholds worth knowing before you build

These are not caveats in the footnotes; they change the decision.

| Threshold | What the measurements show |
|---|---|
| **Horizon T < 25** | No accuracy advantage. Baselines tie or win; significance is claimed only at T ≥ 50. Below ~25 steps you are buying tokens, nothing more. |
| **Entangled relational state** | At T=25 on a Git-graph domain the pattern **lost** to a history-carrying runtime (0.88 vs 0.94). It recovers decisively by T=50. Flat independent state benefits sooner. |
| **Small open-weight models** | It wins at every horizon but from a collapsing base: a 8B model scores 0.34 and a 31B model 0.42 at T=100, where a frontier model scores 0.94. Structured output adherence, not reasoning, is the bottleneck. Budget for grammar-constrained decoding or shorten the horizon. |
| **You do not own the loop** | Mode B unless you start the local MCP / `skillstate.py loop` (Mode C). The token guarantee does not transfer to Mode B. `references/host-runtime-adaptation.md`, `references/mcp.md`. |

Full tables and the negative results: `references/evidence.md` §2, §6, §7.

## It is not a universal corrector

In the drift experiments, one scenario in each environment was solved by **no runtime at all**, including this one (`references/evidence.md` §4). Zero-turn recovery means zero *lag* on corrections the agent could act on — not that every corrective observation can be acted on.

## Why

- The architecture trades replayability for boundedness. That trade is exactly right for forward-executing procedural skills and exactly wrong for retrospective ones. Naming which you have is the whole decision.
- Where the boundary is genuinely unclear, keep the bounded prompt and add a strictly-typed, capped `open_questions` field so more gets committed at first observation. Then measure whether sufficiency held, by replaying against a history baseline.
- Related mechanics: `rules/schema-state-schema-authoring.md` (the sufficient-statistic test), `rules/state-boundedness.md` (the other assumption behind the bound), `rules/reasoning-discard-after-transition.md` (what is actually destroyed).
