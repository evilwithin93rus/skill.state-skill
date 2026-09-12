---
title: Host Runtime Adaptation
impact: CRITICAL
description: What transfers when you cannot replace the host runtime. Distinguishes Mode A (you build the loop, true O(1)), Mode B (you operate inside a host and emulate the discipline), and Mode C (local MCP or skillstate.py loop runs Algorithm 1 outside the host transcript).
tags: skill-state, host-runtime, agent-agnostic, mode-a, mode-b, emulation
---

# Host Runtime Adaptation

> **Moral: you cannot forget on someone else's behalf. Know which of the three jobs you are doing.**

SKILL.state is a **runtime architecture**. The paper's Algorithm 1 is executed by a harness: *the harness* builds the prompt, *the harness* validates the patch, *the harness* discards the reasoning. The language model is a participant, not the implementer.

This has a consequence that most adopters get wrong, so it is stated first and bluntly:

**Copying this skill into an agent's skills directory does not make that agent's prompt O(1).** Claude Code, opencode, Cursor, Codex CLI, Qwen CLI and Gemini CLI all append tool calls and results to a growing transcript, and a Markdown skill cannot reach into the harness and stop it. Any document claiming otherwise is selling a capability the artifact does not have.

There are three distinct jobs. Identify yours before reading any other rule file.

---

## Mode A — You are building the runtime

You control the loop: an SDK script, a service, a custom harness, a LangGraph/DSPy node, a CI driver.

**Everything in this skill applies literally.** You get the measured results in `references/evidence.md` because you are reproducing the measured architecture.

Implement, in this order:

1. `rules/when-not-to-use.md` — applicability gate.
2. `rules/schema-state-schema-authoring.md` + `rules/state-boundedness.md` — the schema.
3. `references/runtime-prompt-template.md` — the per-step prompt.
4. `references/merge-semantics.md` + `scripts/skillstate.py` — the ⊕ operator.
5. `rules/validate-deterministic-rollback.md` — parse, validate, rollback-retry.
6. `references/execution-loop.md` — the loop and its invariants.

Verification: `python tests/run_tests.py`. The invariant that matters is that no code path can place a prior observation, prior action, or prior reasoning trace into a prompt.

---

## Mode B — You are an agent inside a host runtime you do not control

You are Claude Code, opencode, Cursor, Codex, Qwen, Gemini or similar, working a long task. You cannot delete the transcript. You *can* stop depending on it.

### What you actually do

Externalize Σ to a file and make that file the only thing you trust.

```text
1. Author the schema once, write Σ_0 to a state file (compact JSON, one line).
2. Each step:
     a. Re-read the state file. Do not scroll back through the transcript.
     b. Read the newest observation only.
     c. Decide the patch, apply it to the file, then act on the merged view.
3. When the host offers compaction / clearing / a fresh session:
     compact aggressively. The state file is your continuity, so the
     transcript is expendable — that is the whole point.
4. Never copy reasoning into the state file.
```

Use `scripts/skillstate.py` to apply patches so the merge is deterministic rather than an act of prose:

```bash
python scripts/skillstate.py merge --state .agent/state.json \
    --patch '{"tests":{"src/billing_test.py":{"status":"pass"}}}' --in-place
```

### What transfers, and what does not

| Measured benefit | Mode A | Mode B | Why |
|---|---|---|---|
| Zero-turn drift recovery (§4) | Yes | **Yes** | Depends on trusting the newest observation over memory. That is a decision discipline, not a harness feature. |
| No repeated failed actions (CTF +7.8) | Yes | **Yes** | Depends on `tested_hypotheses` existing in a durable place. A file works. |
| Noise never re-read | Yes | **Partial** | Distractors still sit in the host transcript. You can refuse to act on them; you cannot un-see them. |
| Survives compaction / session restart | Yes | **Yes — and this is the biggest practical win** | The state file outlives the context window. Baselines lose the task; you resume from Σ. |
| O(1) prompt per step | Yes | **No** | The host appends regardless. |
| O(T) cumulative tokens | Yes | **No** | Best case is O(T) *between* compactions, sawtooth overall. |
| 16.2× token reduction | Yes | **No — do not promise this** | It is a property of the harness, not of the discipline. |

**The honest Mode B pitch:** you do not get the token curve. You get correctness under drift, no rediscovery of solved subproblems, and a task that survives context exhaustion. On a 30-round debugging session those are worth more than the token savings anyway.

### Where to put the state file

Agent-agnostic, in order of preference:

1. A path the task already implies — `.agent/state.json`, `.cache/skillstate.json`.
2. A scratch directory the host designates for temporary work.
3. A branch-local file that is git-ignored.

Keep it out of version control unless the state *is* a deliverable. Add it to `.gitignore`.

### Host-specific notes

These are conveniences, not requirements. The discipline above is complete without them.

| Host | Useful mechanism |
|---|---|
| Claude Code | Skill auto-discovery via `SKILL.md` frontmatter; `/compact` between phases; hooks can write Σ deterministically |
| opencode | Skill auto-discovery; **local MCP** (`skillstate.py mcp`) for Mode C; a plugin can still own merge if you stay in Mode B |
| Cursor | Reference this folder from a rule file; keep the state file open as pinned context |
| Codex CLI | Reads repo-root `AGENTS.md` automatically — that file is deliberately tiny here |
| Qwen CLI / Gemini CLI | Point `QWEN.md` / `GEMINI.md` at `SKILL.md`; state file works identically |
| Any custom loop | You are in Mode A. Stop emulating and implement Algorithm 1. |

---

## Mode C — Local MCP / `skillstate.py loop` owns Algorithm 1

You are still inside Cursor, opencode, Qwen, Claude. You still cannot delete their transcript. You **can** refuse to run the T-step loop there.

The local stdio MCP (`python scripts/skillstate.py mcp`) or `skillstate.py loop` builds each child prompt from `(P, Σ, O)` only. Inner generate is a bare API (`host=api`) or a stub. The parent chat holds one `run_loop` result (or N tiny `{action}` values from `step`).

This is the same architecture as Mode A. It is **not** Mode B with extra tools. A merge/`get_state` MCP copies Σ into the host transcript and costs *more* tokens — that is what `benchmarks/model-opencode-cli-benchmark-output.md` measured.

Recipe, tool contract, per-host install JSON: `references/mcp.md`.

| Measured benefit | Mode A | Mode B | Mode C (local MCP `run_loop`, api generate) |
|---|---|---|---|
| O(1) inner prompt | Yes | No | **Yes** |
| O(T) cumulative tokens | Yes | No | **Yes, inner.** Parent is ~one tool call. |
| Zero-turn drift recovery | Yes | Yes | Yes |
| Token curve of the paper harness | Yes | **No** | Inner curve matches Mode A; never quote Mode B rows as that result |

LangGraph/LangChain: skip the MCP. You already control prompt assembly — Mode A. `references/integrations.md`.

---

## Choosing

```text
Do you control how the next prompt is assembled?
    yes → Mode A. Implement the architecture. Claim the numbers.
    no  → Can a local process run the T-step loop (MCP run_loop / skillstate.py loop)
          with api generate, while the host only starts it?
              yes → Mode C. Inner tokens match Mode A. Parent stays flat in T.
              no  → Mode B. Externalize the state. Claim drift resilience and
                    restart survival. Do not claim the token curve.
```

Misclassifying yourself as Mode A while in Mode B is the single most common failure with this pattern: the prompt keeps growing, the agent believes it is bounded, and nobody measures it until the context window ends the session. Installing a merge-only MCP is the same mistake with more tool calls.
