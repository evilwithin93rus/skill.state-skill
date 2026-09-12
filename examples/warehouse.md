# Warehouse — verified reference trajectory

A four-turn SKILL.state episode in the Warehouse domain (SkillExecBench Environment 1, `references/evidence.md` §1). Every patch and every resulting state on this page is executed by `tests/run_tests.py`; if the narrative and the arithmetic ever disagree, the test suite fails.

**Machine-checked block order:** the first `json` block is the schema, the second is Σ₀, and every pair after that is `(patch, resulting state)`. Do not add or reorder `json` blocks.

## Domain

Action space: `Store <item> <shelf>`, `Ship <item> <shelf>`, `Move <item> <old> <new>`, `Wait`.

Paper's state representation: a map of 500 independent shelves, each holding one item identifier or null (Appendix B.1). Three shelves are shown here; the shape is identical at 500.

```json
{"inventory":{"*":"str?"},"maintenance":{"*":"str?"},"pending_intake":{"*":"str?"}}
```

Every field is an open keyed map, so every field is bounded by the warehouse, not by the horizon (`rules/state-boundedness.md`). Entries are created by observation and removed by `null` when the work they represent is finished.

## Σ₀

```json
{"inventory":{"shelf_41":"item_11","shelf_42":"item_12","shelf_43":"item_13"},"maintenance":{},"pending_intake":{}}
```

---

## Turn 1 — the ordinary case: a patch names only what changes

**Observation:** `Customer ordered item_12.`

**Reasoning (discarded):** State says item_12 is on shelf_42. Ship it, and free the shelf. Shelves 41 and 43 are untouched, so they must not appear in the patch.

```json
{"inventory":{"shelf_42":null}}
```

**Action:** `Ship item_12 shelf_42`

```json
{"inventory":{"shelf_41":"item_11","shelf_43":"item_13"},"maintenance":{},"pending_intake":{}}
```

`shelf_41` and `shelf_43` survive **because they were absent from the patch.** Resending the whole `inventory` map minus `shelf_42` would be the 68% failure mode (`rules/failure-state-update-modes.md`).

---

## Turn 2 — external drift and noise in the same observation

**Observation:**

```text
Alert: shelf_43 requires maintenance. Maintenance team relocated item_13 to shelf_99.

--- BACKGROUND TELEMETRY ---
[Robot] Battery: 43%, Temperature: 45C, CPU Load: 72%
[Sensor] Humidity: 45%, Temp: 22.3C, CO2: 450 ppm
```

**Reasoning (discarded):** The world changed outside my action loop. State believes item_13 is on shelf_43; the observation says shelf_99. The observation is fresher evidence, so it wins — patch now, do not argue with Σ. Telemetry has no bearing on inventory, so it is not patched; it will vanish with this reasoning trace.

```json
{"inventory":{"shelf_43":null,"shelf_99":"item_13"},"maintenance":{"shelf_43":"service due"}}
```

**Action:** `Wait`

```json
{"inventory":{"shelf_41":"item_11","shelf_99":"item_13"},"maintenance":{"shelf_43":"service due"},"pending_intake":{}}
```

Recovery cost: **zero turns.** A history runtime re-reads its own transcript, finds "item_13 is on shelf_43", and acts on it for 5–8 turns (`references/evidence.md` §4). Here the stale belief was overwritten before the next decision existed.

The two telemetry lines are gone forever. They were seen once, judged irrelevant, and dropped with the reasoning. They cannot drag attention at turn 40 because they are not anywhere.

---

## Turn 3 — the one thing you must never postpone

**Observation:** `Customer ordered item_11. Shipment arrived containing item_20.`

**Reasoning (discarded):** Two events, one action per step. I will ship item_11 now. But item_20 has arrived and needs a shelf — and this observation will not be shown to me again. If I do not commit it to state on this step, it is lost permanently and no future patch can recover it.

```json
{"inventory":{"shelf_41":null},"pending_intake":{"item_20":"awaiting shelf"}}
```

**Action:** `Ship item_11 shelf_41`

```json
{"inventory":{"shelf_99":"item_13"},"maintenance":{"shelf_43":"service due"},"pending_intake":{"item_20":"awaiting shelf"}}
```

This is the sufficient-statistic assumption being actively maintained rather than assumed. Every fact with future relevance is committed **at first observation** — that is the whole price of discarding history, and the only failure mode that no amount of validation can repair (`rules/when-not-to-use.md`, insufficiency mode 2).

---

## Turn 4 — finished work is deleted, not archived

**Observation:** `Success: Shipped item_11 from shelf_41.`

**Reasoning (discarded):** shelf_41 is now empty and item_20 is waiting. Store it there and clear the intake entry — it has no further bearing on any decision, so it must not survive.

```json
{"inventory":{"shelf_41":"item_20"},"pending_intake":{"item_20":null}}
```

**Action:** `Store item_20 shelf_41`

```json
{"inventory":{"shelf_41":"item_20","shelf_99":"item_13"},"maintenance":{"shelf_43":"service due"},"pending_intake":{}}
```

|Σ| after four turns is the same size as |Σ₀|. That is not an accident of a short example — it is what the `null` deletion in this patch bought. A runtime whose model never emits `null` has an unbounded state and has silently given up the O(1) bound (`rules/state-boundedness.md`).

---

## What each turn demonstrates

| Turn | Mechanism | Rule |
|---|---|---|
| 1 | Patches are deltas; absent keys survive | `rules/failure-state-update-modes.md` |
| 2 | Observation beats state; noise is dropped, not stored | `rules/env-drift-and-noise.md` |
| 3 | Commit at first observation or lose it forever | `rules/when-not-to-use.md` |
| 4 | `null` deletion keeps \|Σ\| flat | `rules/state-boundedness.md` |
| all | Prompt is `P + compact Σ + O_t`; no replay, ever | `rules/prompt-bounded-o1.md` |

## Run it

```bash
python scripts/skillstate.py merge \
  --state /dev/stdin --schema /dev/null \
  --patch '{"inventory":{"shelf_42":null}}' <<< \
  '{"inventory":{"shelf_41":"item_11","shelf_42":"item_12"},"maintenance":{},"pending_intake":{}}'
```

The full trajectory, schema conformance included, is replayed by `python tests/run_tests.py`.
