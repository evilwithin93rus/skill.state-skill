# Schema Examples

Worked schemas for four domains. Each is marked **verified** (stated in arXiv:2608.26263) or **illustrative** (a reasonable design this skill proposes, not measured). The distinction matters: an illustrative schema presented as evidence is how a skill becomes untrustworthy.

Schemas use the notation accepted by `scripts/skillstate.py`, so every one on this page can be linted:

```text
"str" "int" "num" "bool" "list" "any"   leaf type       "str?"  nullable (deletable)
["a","b","c"]                           enum            {"*": T} open keyed map
{"k": T, ...}                           closed object
```

```bash
python scripts/skillstate.py lint --schema my_schema.json
```

---

## 1. InterCode CTF — **verified**

The paper states the field names explicitly: one static 5-field schema reused across all 100 challenges, spanning reverse engineering, forensics, cryptography and binary exploitation (§3.1).

```text
state_schema = {
  "discovered_flags":  [],   // outcomes needed later
  "tested_hypotheses": [],   // commands or approaches already tried
  "active_files":      [],   // the current working set
  "working_dir":       "",   // position in the environment
  "cmd_summary":       ""    // what the last command did
}
```

- `tested_hypotheses` is the anti-repeat field. With history discarded it is the *only* thing preventing the model from re-running a failed command, and the paper attributes the CTF gain to it specifically (`references/evidence.md` §5).
- Five fields for 100 diverse tasks is the concrete evidence that schemas amortize per domain rather than per task.
- Average prompt was 813 characters — the smallest of any runtime measured, less than half the nearest baseline.

**Three of those five fields are append-only lists**, so `|Σ|` grows once per step. It stays bounded here only because CTF episodes are short. The bounded form, which costs nothing:

```json
{"discovered_flags":{"*":"str?"},"tested":{"*":"str?"},"active_files":{"*":"str?"},"working_dir":"str","cmd_summary":"str"}
```

Keying `tested` by the command means a repeat overwrites its entry instead of appending, and the value carries the outcome — strictly more useful. See `rules/state-boundedness.md` and the full trajectory in `examples/ctf.md`.

---

## 2. Warehouse management — **partly verified**

**Verified (Appendix B.1):** the state is a map of 500 independent shelves, each holding exactly one item identifier or null. Action space: `Store <item> <shelf>`, `Ship <item> <shelf>`, `Move <item> <old> <new>`, `Wait`. Observations are textual alerts: shipment arrived, customer ordered, maintenance required. `Ship` destroys the item; storing onto an occupied shelf is rejected with a local error.

**Illustrative:** the `maintenance` and `pending_intake` fields below. The paper's state representation is the shelf map alone; those alerts arrive as observations. They are added here because a single-field schema cannot demonstrate the deletion discipline.

```json
{"inventory":{"*":"str?"},"maintenance":{"*":"str?"},"pending_intake":{"*":"str?"}}
```

- A flat keyed map with nullable slots is the shape most resistant to type coercion — there is no nesting to get wrong (`rules/failure-state-update-modes.md`).
- 500 independent, non-overlapping variables is a direct test of whether an observation from step 1 survives to step 100. It does, via Σ rather than replay.
- Every field is bounded by the warehouse, not by the horizon. Entries are created by observation and deleted by `null` when the work is done.

Full four-turn trajectory, covering drift, noise and deletion: `examples/warehouse.md`.

---

## 3. Software repository — **partly verified**

**Verified (Appendix B.1):** a deeply nested relational graph of branches, commits, pull requests and CI statuses. Action space: `Commit(branch, file)`, `CreatePR(branch)`, `Merge(pr_id)`, `FixCI(branch)`, `Wait`. Merging a PR transitions master and deletes the feature branch. §4.1 additionally names `CherryPick`, `RunTests`, `CreateRelease` and `Rollback` when describing the environment — treat the Appendix list as the implemented space.

**Illustrative:** the concrete field layout below.

```json
{"branches":{"*":{"head":"str","ci":["pass","fail","pending"]}},"pull_requests":{"*":{"source":"str","target":"str","state":["open","merged","closed"]}}}
```

- Densely entangled by design: merging one PR mutates master, deletes a branch and flips dependent PR states. The schema mirrors those relations so a **single patch can express the whole cascade** — which is the argument for nesting here and against it everywhere else.
- Enums on `ci` and `state` close the sets. A free string is where a coercion error becomes a permanently wrong value.
- This is the domain where the pattern is weakest at short horizons: it **loses** to a history-carrying runtime at T=25 (0.88 vs 0.94) and wins decisively by T=50 (0.86 vs 0.74). Entangled state needs a longer horizon before boundedness pays — `references/evidence.md` §2.

---

## 4. τ-Bench style enterprise workflow — **illustrative**

**Verified:** only the measurements. Complex relational database responses drove baseline prompts above 11,000 tokens/step on the Airline domain, while the state runtime held a flat ~2,800 (`references/evidence.md` §5). The schema below is this skill's proposal; the paper does not publish one.

```json
{"user_id":"str?","policy_checks":{"*":"bool"},"db_view":{"*":"str?"},"pending_txn":"str?"}
```

- `db_view` is a **projection**, not a cache. Only workflow-relevant rows are committed; the rest of the query result dies with the reasoning trace. That projection is the whole difference between 2,800 and 11,000 tokens per step.
- `user_id` and `policy_checks` should be **runtime-owned**: identity and business-policy constraints must not be writable by a patch derived from an observation. This is the domain where that matters most — a refund policy is exactly what a hostile observation would target (`rules/untrusted-observations.md`).
- `pending_txn` makes an in-flight transactional action explicit, so an interrupted step is recoverable rather than ambiguous.

---

## Patterns across all four

| Pattern | Where it shows up |
|---|---|
| Keyed map with nullable values | Every domain. The default shape; nest only when relations are genuinely entangled. |
| An anti-repeat field | CTF `tested`. Mandatory whenever actions can be retried, because history is gone. |
| Deletion on completion | Warehouse `pending_intake`, CTF working set. This is what keeps `|Σ|` flat. |
| Enums for closed sets | Repository `ci` and `state`. Removes a coercion surface entirely. |
| Runtime-owned fields | τ-Bench `policy_checks`, `user_id`. Anything security-relevant. |
| One position scalar | CTF `working_dir`. Cheap, and relative paths are guesses without it. |
| One last-action summary | CTF `cmd_summary`. Bounded, unlike an action log. |
