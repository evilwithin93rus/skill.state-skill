# The Execution Loop

Algorithm 1 of arXiv:2608.26263, expanded with the initialization, retry budget and termination handling that a production loop needs and the published pseudocode omits. This loop is the entire runtime — there is no transcript, no replay, no second data structure.

Reference implementation: `Runtime` in `scripts/skillstate.py`. Mode C driver: `run_loop` / local MCP (`skillstate.py mcp`) — `references/mcp.md`.

## Pseudocode

```text
# ---- once, per domain -----------------------------------------------------
P      = skill.instructions     # persona, action space, completion convention,
                                # environment rules, schema shape
schema = authored_schema()      # rules/schema-state-schema-authoring.md
assert lint(schema) == []       # rules/state-boundedness.md
owned  = runtime_owned_paths()  # rules/untrusted-observations.md

# ---- once, per episode ----------------------------------------------------
Σ = init_state(schema)          # typically empty maps, not absent keys:
                                # {"inventory":{}} — so the model sees the shape
t = 0

# ---- the loop -------------------------------------------------------------
while not done and t < T:
    O = receive_observation()                 # environment → runtime

    for attempt in range(MAX_RETRIES + 1):
        prompt   = build(P, compact(Σ), O)    # EXACTLY three inputs, no history
        response = generate(prompt)           # R_t, ΔΣ_t, a_t

        try:
            Δ, a = parse_strict(response)     # one fenced block, exactly two keys
            validate(Δ, schema, owned)        # keys, types, enums, nullability, perms
            validate_action(a, action_space)  # close the string-shaped hole
        except ContractError, ValidationError as e:
            O = "Your previous response was rejected: " + str(e)
            continue                          # Σ untouched — nothing to roll back

        Σ_next = Σ ⊕ Δ                        # build from a copy
        assert size(compact(Σ_next)) <= Σ_BUDGET   # catch an unbounded schema
        Σ = Σ_next                            # commit, atomically
        break
    else:
        escalate()                            # budget exhausted; Σ still pristine
        break

    if terminal(a):                           # "DONE", or a runtime-side condition
        done = True
    else:
        O = execute(a)                        # environment transition

    discard(response)                         # R_t destroyed, permanently
    t += 1
    # note: O_t is gone too — the next iteration reads only the new observation
```

## Invariants

Assert these. Every one is mechanically checkable, and every one is a failure mode someone has shipped.

| Invariant | Assertion |
|---|---|
| Prompt inputs | The prompt contains exactly `P`, `compact(Σ)` and `O`. Grep the codebase: no history buffer should exist to accidentally include. |
| Prompt size | `\|prompt_t\|` is flat across `t`. Assert `max - min < tolerance`, not merely that it is small. |
| State writer | Σ changes **only** via a validated patch. One writer. Never model prose, never a manual fix-up, never two code paths. |
| State size | `\|Σ\|` is bounded by the environment, not by `t`. Enforce a budget. |
| Reasoning | Exists only inside the current generation call. No later prompt is built from it. |
| Observation | `O_t` is read once and superseded by `O_{t+1}`. |
| Validation owner | Parse and schema checks are deterministic runtime code. Model output can only be accepted or rejected, never half-applied. |
| Atomicity | After a rejected step, Σ is byte-identical to before the step. |
| Retry | Finite budget, distinct feedback per attempt, explicit escalation on exhaustion. |
| Termination | A reachable completion condition exists — otherwise the loop always runs to `T`. |

## The three things the published algorithm leaves to you

Algorithm 1 is five lines and assumes the rest. These are the omissions that bite.

### 1. Initialization

Initialize with the **shape**, not with nothing.

```text
// BAD: {} — the model has no idea what fields exist or what types they are
// GOOD: {"inventory":{},"maintenance":{},"pending_intake":{}}
```

An empty state is also the moment the model is most likely to invent a field, because the serialized Σ carries no type information to copy. Put the schema in `P` as well.

### 2. Termination

Two keys leave no room for a `done` flag, so completion must live in the action space. Declare one in `P`: a terminal action (`"DONE"`), a no-op for idle steps (`"Wait"`), or a runtime-side condition (the queue is empty, the flag is submitted). Without it the agent either runs to the horizon or invents a third key, which is a rejected patch.

### 3. Retry budget

The published loop has no failure branch at all. Bound it at 2–3 attempts. Structural errors that survive three *targeted* corrections are a schema or prompt defect, not a transient slip — escalate rather than retrying into the token budget.

## Common implementation mistakes

- Keeping a "last N turns" buffer *just in case*. It breaks the bound and re-imports the stale facts the architecture exists to delete — `rules/context-latest-observation-only.md`.
- Applying the patch before validating it. One malformed response poisons Σ permanently — `rules/validate-deterministic-rollback.md`.
- Pretty-printing the state. Whitespace is re-paid on every step of the horizon — `rules/prompt-bounded-o1.md`.
- Letting the reasoning string survive in any log that a later prompt is built from. Logging it for offline debugging is fine; building a prompt from it is not — `rules/reasoning-discard-after-transition.md`.
- Writing Σ from two places — the patch plus a manual correction. Σ must have exactly one writer, or the schema no longer describes it.
- Trusting an optimistic commit. The patch merges *before* the action runs, so Σ asserts an outcome the environment has not confirmed. Reconcile from the next observation — `rules/patch-two-key-contract.md`.
- No retry ceiling. A live-lock that burns tokens at full rate while making no progress.

## Verify

```bash
python scripts/skillstate.py self-test   # contract smoke test
python tests/run_tests.py                # merge, envelope, schema, loop, docs
```

The suite asserts the flat-prompt invariant against the reference `Runtime` across 50 steps, replays both trajectories in `examples/`, and checks that the constructed prompt contains no history section.

## Reference trajectories

- `examples/warehouse.md` — delta patching, external drift, noise filtering, deletion for boundedness.
- `examples/ctf.md` — the anti-repeat field, and why recording a *failure* is the point of it.
