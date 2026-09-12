# SKILLSTATE execution card (Mode B / Mode C)

Self-contained. Do **not** open other skill files mid-task — each read is re-billed for the rest of the host session.

## Contract every step

```text
A_t = (P, Σ_t, O_t)  →  (R_t, ΔΣ_t, a_t)
Σ_{t+1} = Σ_t ⊕ ΔΣ_t     # then discard R_t
```

Reply with reasoning (discarded), then **one** fenced JSON block with **exactly two keys**:

```json
{"state_patch":{},"action":"..."}
```

## Merge rules (non-negotiable)

| Patch | Effect |
|---|---|
| key omitted | unchanged |
| `"k": null` | delete `k` |
| `"k": {}` | **no-op** (not a clear) |
| object × object | deep merge |
| unknown key / type change | reject — do not hand-edit around it |

Apply with the tool, never by rewriting the whole file:

```bash
python skillstate.py merge --state .agent/state.json --patch '<json>' --in-place
```

## Mode B (inside a host you do not control)

1. Author schema once. Keep compact Σ in `.agent/state.json` (one line).
2. **Do not re-read Σ every step** — once per phase is enough; re-reads multiply in the transcript.
3. Trust latest observation over transcript. On contradiction, observation wins — patch immediately.
4. Never store reasoning, notes, telemetry, or turn counts in Σ.
5. Prefer projecting tool output in the shell before it enters the transcript.
6. Compact / clear host context between phases; Σ is continuity.

## Mode C (when the loop is scriptable)

Do **not** run the T-step loop in the host transcript. Prefer the local MCP (`status` → `init` → `run_loop`). Inner generate is api, not the host CLI. Details: `references/mcp.md`.

Or compile it:

```bash
python skillstate.py loop --host api \
  --instructions P.md --schema schema.json --state .agent/state.json \
  --env-observe '…' --env-action '… {action}' --horizon T --trace .agent/trace.jsonl
```

Parent transcript stays O(1) in T. Child prompts stay `|P|+|Σ|+|O|`.

## When not to use

Schema unknown upfront; relevance of a fact only clear later; deliverable *is* the trajectory (audit). Accuracy edge vs history appears at long horizons — short tasks mainly buy prompt shape, not score.
