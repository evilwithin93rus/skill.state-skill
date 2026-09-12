---
title: Bounded Prompt
impact: CRITICAL
description: The per-step prompt is the skill spec, the compact state, and the latest observation. Nothing else, ever. Serialize state with no insignificant whitespace. Prompt size must not depend on how many steps have run.
tags: skill-state, prompt, bounded, compact-json, token-cost
---

# Bounded Prompt

> **Moral: a prompt that grows with the journey never arrives.**

## Problem

Appending observations, responses and tool outputs makes prompt size grow with the step index, so cumulative token burn grows quadratically. The growth is not merely expensive: obsolete facts stay resident and compete with fresh ones, which is why accuracy *falls* as the horizon extends even though the model has been given strictly more information.

At T=100 on Warehouse the history-carrying baseline consumed 1,062,387 tokens against 65,408 — a 16.2× reduction — while scoring *lower* (0.91 vs 0.94). See `references/evidence.md` §1.

## Incorrect

````text
// BAD: any of these inside the per-step prompt
- History: Observation: ... / Response: ...      (appended every step)
- Recent History: last 3 turns                    (a window still grows the summary)
- Full transcript + structured state side by side (the Stateful/LangGraph hybrid)
- state serialized with indentation:
  {
    "inventory": {
      "shelf_42": "item_12"
    }
  }                                               // pays for whitespace on every step
````

Note the fence above is four backticks. A three-backtick example containing a
three-backtick block closes the outer block early and truncates the file — the
same trap the template below has to sidestep.

## Correct

The entire step prompt. Nothing precedes `Instructions`, nothing follows the response contract.

````text
Instructions:
{skill.instructions}                        // immutable spec P

Skill Execution State:
```json
{compact_json(state)}                       // no indentation, no spaces
```

Latest Observation: {observation}           // O_t only

Provide your response with:
1. Step-by-step reasoning (will be discarded after execution)
2. A JSON block fenced with ```json ... ``` containing exactly two keys:
   {"state_patch": {<updates; set a key to null to delete; include only
                     keys you are changing>},
    "action": "<the exact command to execute>"}
````

## Serialization Rules

| Rule | Reason |
|---|---|
| No insignificant whitespace — in Python, `json.dumps(s, separators=(',',':'))` | State is re-serialized every step, so the overhead multiplies by T |
| `|Σ|` bounded by the environment, not by steps taken | Otherwise the bound is void — see `rules/state-boundedness.md` |
| Never include prior observations, actions or reasoning | Reconstructible from Σ, or irrelevant |
| Only `Σ` and `O` differ between consecutive prompts | Everything else is the immutable `P` |

Compactness and boundedness are different guarantees and you need both. Compact serialization shrinks the constant; boundedness stops the constant from being a function of T. Compact serialization of an unbounded state is still `O(T²)`.

## Complexity

| Runtime | Prompt size | Cumulative tokens |
|---|---|---|
| ReAct (append history) | `O(t)` | `O(T²)` |
| Memory (summary + window) | `O(1)` + growing summary | between `O(T)` and `O(T²)` |
| Stateful (state + full history) | `O(t)` | `O(T²)` |
| **SKILL.state** | **`O(|P| + |Σ| + |O|)`** | **`O(T)`** |

The `O(1)` claim is shorthand for "independent of `t`". It is conditional on `|Σ|` and `|O|` being horizon-independent. State both conditions when you quote it; a reviewer who notices the gap will distrust everything else you said.

## Bounded ≠ small

On τ-Bench Retail the state runtime had the **largest** average prompt of any runtime tested and still the lowest cumulative token cost (`references/evidence.md` §5). A well-populated state can exceed a short transcript on any single step and still win decisively over the horizon. Optimize the slope, not the intercept.

## Verify it

Asymptotics are not a code review; they are a test.

```text
sizes = [len(prompt_t) for t in range(T)]
assert max(sizes) - min(sizes) < tolerance     # flat, not merely small
assert no code path can place a prior observation, action,
       or reasoning trace into a prompt        # grep for history buffers
```

`tests/run_tests.py` asserts exactly this against the reference `Runtime`.

## Why

- Bounded prompts are the mechanism behind every other measured win: the token curve, noise robustness (distractors cannot accumulate), and drift recovery (no stale facts to argue with) — see `rules/context-latest-observation-only.md`.
- Brevity alone does not work. Pinned to the same budget, sliding-window truncation scored 0.18 and perplexity-based compression 0.22, against 0.94 for structured state (`references/evidence.md` §6). The mechanism is lossless projection into a schema, not a shorter prompt.
- Exact template and baseline comparison: `references/runtime-prompt-template.md`. The loop that maintains the bound: `references/execution-loop.md`.
