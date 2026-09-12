# InterCode CTF — verified reference trajectory

A four-turn SKILL.state episode on a reverse-engineering challenge. This is the domain where the pattern posted its largest accuracy gain: **54.2% pass@1, +7.8 points over the strongest baseline, at 813 characters of average prompt** — the smallest prompt of any runtime tested (`references/evidence.md` §5).

**Machine-checked block order:** the first `json` block is the schema, the second is Σ₀, and every pair after that is `(patch, resulting state)`. Do not add or reorder `json` blocks.

## The paper's schema, and why this page changes it

One static 5-field schema served all 100 challenges — reverse engineering, forensics, crypto and pwn — which is the paper's evidence that schemas amortize per *domain*, not per task (§3.1):

```text
state_schema = {
  "discovered_flags":  [],   // outcomes needed later
  "tested_hypotheses": [],   // the anti-repeat field
  "active_files":      [],   // current working set
  "working_dir":       "",   // environment position
  "cmd_summary":       ""    // what the last command did
}
```

Three of those five fields are append-only lists, so `|Σ|` grows once per step and the O(1) bound holds only because CTF episodes are short. Run `skillstate.py lint` on it and it says so. This page therefore uses the same five concepts with growing lists replaced by keyed maps, which is strictly better and costs nothing:

```json
{"discovered_flags":{"*":"str?"},"tested":{"*":"str?"},"active_files":{"*":"str?"},"working_dir":"str","cmd_summary":"str"}
```

Keying `tested` by the command means a repeated hypothesis **overwrites** its entry instead of appending a duplicate, and the value can carry the outcome — which is more useful than the bare command string anyway. See `rules/state-boundedness.md`.

## Σ₀

```json
{"discovered_flags":{},"tested":{},"active_files":{},"working_dir":"/home/ctf","cmd_summary":""}
```

---

## Turn 1 — reconnaissance

**Observation:** `You are in /home/ctf. Files: binary, README.txt`

**Reasoning (discarded):** Record the working set before doing anything, so I never have to re-run `ls` to remember what is in scope. Then identify the binary and read the README in one command.

```json
{"active_files":{"binary":"unknown","README.txt":"text"},"cmd_summary":"listed /home/ctf"}
```

**Action:** `file binary && cat README.txt`

```json
{"discovered_flags":{},"tested":{},"active_files":{"binary":"unknown","README.txt":"text"},"working_dir":"/home/ctf","cmd_summary":"listed /home/ctf"}
```

---

## Turn 2 — the cheapest hypothesis first

**Observation:** `binary: ELF 64-bit LSB executable, not stripped. README: "CTF level 3 — find the key."`

**Reasoning (discarded):** 64-bit ELF, symbols intact. Try `strings` before anything expensive — if the flag is embedded in plaintext this ends now. Record what `file` established so I never re-run it.

```json
{"active_files":{"binary":"ELF 64-bit"},"tested":{"file binary":"ELF64, not stripped"},"cmd_summary":"identified binary type"}
```

**Action:** `strings binary | grep -i flag`

```json
{"discovered_flags":{},"tested":{"file binary":"ELF64, not stripped"},"active_files":{"binary":"ELF 64-bit","README.txt":"text"},"working_dir":"/home/ctf","cmd_summary":"identified binary type"}
```

Note `active_files.binary` was overwritten from `"unknown"` to `"ELF 64-bit"` by a leaf write, while `README.txt` was left alone. One patch, one fact, no collateral damage.

---

## Turn 3 — recording a failure is the point of the field

**Observation:** `(no output)`

**Reasoning (discarded):** The grep found nothing. That is a result, not a non-event: if I do not record it, a later step with no memory of this turn will try the identical command. Commit the negative outcome, then widen the search with a minimum string length instead of a keyword filter.

```json
{"tested":{"strings binary | grep -i flag":"no match"},"cmd_summary":"strings+grep found nothing"}
```

**Action:** `strings -n 6 binary | head -50`

```json
{"discovered_flags":{},"tested":{"file binary":"ELF64, not stripped","strings binary | grep -i flag":"no match"},"active_files":{"binary":"ELF 64-bit","README.txt":"text"},"working_dir":"/home/ctf","cmd_summary":"strings+grep found nothing"}
```

**This turn is the mechanism behind the +7.8 points.** The paper attributes the CTF gain specifically to "maintaining explicit hypotheses and discovered flags in Σ", which "prevents the model from repeating failed commands" (§5.5). With history discarded, a failed command leaves *no other trace*. An agent that only patches on success will loop forever on the cheapest idea.

---

## Turn 4 — commit the outcome immediately

**Observation:** `flag{strings_are_not_security}`

**Reasoning (discarded):** Flag recovered. Write it to state in the same step it was observed — this observation will never be shown again, and a flag I have seen but not committed is a flag I have lost.

```json
{"discovered_flags":{"flag{strings_are_not_security}":"strings -n 6 binary"},"tested":{"strings -n 6 binary":"flag found"},"cmd_summary":"flag recovered"}
```

**Action:** `submit flag{strings_are_not_security}`

```json
{"discovered_flags":{"flag{strings_are_not_security}":"strings -n 6 binary"},"tested":{"file binary":"ELF64, not stripped","strings binary | grep -i flag":"no match","strings -n 6 binary":"flag found"},"active_files":{"binary":"ELF 64-bit","README.txt":"text"},"working_dir":"/home/ctf","cmd_summary":"flag recovered"}
```

Keying the flag by its value and storing the command that produced it makes the entry idempotent: re-observing the same flag rewrites one key instead of appending a duplicate.

---

## Field-by-field deletion test

The test from `rules/schema-state-schema-authoring.md`: remove the field — does a future step decide worse?

| Field | If it vanished |
|---|---|
| `tested` | The agent re-runs failed commands until the step budget ends. This is the field the benchmark gain is attributed to. |
| `discovered_flags` | On a multi-flag challenge, solved flags are lost; the episode cannot be completed. |
| `active_files` | `ls` gets re-run every few turns to rediscover the working set. |
| `working_dir` | Relative paths become guesses after any `cd`. |
| `cmd_summary` | The immediate consequence of the last action is unavailable when the observation is terse, e.g. `(no output)`. |

Five fields, five "yes". The schema is minimal and sufficient.

## Run it

```bash
python scripts/skillstate.py lint --schema <(printf '%s' \
  '{"discovered_flags":"list","tested_hypotheses":"list","active_files":"list","working_dir":"str","cmd_summary":"str"}')
```

That prints the growth warning for the paper's verbatim schema. The bounded variant above passes. The full trajectory is replayed by `python tests/run_tests.py`.
