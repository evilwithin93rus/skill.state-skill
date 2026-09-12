# Integrations

Concrete setup for each host, then two Mode A implementations. Read `references/host-runtime-adaptation.md` first to know which mode you are in — it determines which of these recipes is honest for your situation.

Everything below was checked against each tool's own documentation. Where a detail is likely to drift, the file says so rather than guessing.

---

## System prerequisites

### Mode B — using the skill inside an existing agent

| Requirement | Detail |
|---|---|
| **Runtime** | None. The skill is plain Markdown. |
| **Disk** | ~250 KB for the folder. |
| **Network** | None. Nothing phones home; there is no install step. |
| **Optional** | Python 3.8+ if you want `skillstate.py` to apply patches deterministically instead of the model hand-editing JSON. Strongly recommended — hand-editing a state file re-introduces the 68% overwrite failure mode. |

### Mode A — building a runtime

| Requirement | Detail |
|---|---|
| **Python** | 3.8+ for `scripts/skillstate.py` and `tests/run_tests.py`. **Standard library only — zero dependencies.** Verified on CPython 3.13 and 3.14. |
| **LangGraph / LangChain** | `langgraph`, `langchain-core`, and one provider package. Check each package's own `requires-python`; it moves faster than this document. |
| **Model access** | An API key, or a local server (Ollama, vLLM). Set `temperature=0` for reproducibility, as the paper does. |
| **Grammar-constrained decoding** | Optional for frontier models, close to mandatory below ~30B parameters: 12% of open-weight failures are JSON syntax alone (`references/evidence.md` §8). Available via vLLM, llama.cpp GBNF, or provider-side structured output. |
| **OS** | Any. The Python is POSIX-agnostic. One Windows caveat under Qwen Code hooks — see that section. |

### Per-host

| Host | Requirement | Skill auto-discovery |
|---|---|---|
| Claude Code | Recent version with Agent Skills | Yes — `SKILL.md` frontmatter |
| Qwen Code | Node.js **≥ 22** | Yes — `SKILL.md` frontmatter |
| opencode | Recent version | Yes — `SKILL.md` frontmatter |
| Cursor | Version supporting `.cursor/rules/*.mdc` | No — reference it from a rule |
| Codex CLI | Any | Via repo-root `AGENTS.md` |

### Verify the install

```bash
python scripts/skillstate.py self-test   # expect: self-test: 25 checks passed
python tests/run_tests.py                # expect: PASSED: all 159 checks
```

If the first command fails, your Python is too old or the file was truncated in transit. Nothing else can go wrong — there is nothing else.

---

## Claude Code

**Install.** Project-scoped (committed, shared with the team) or personal:

```bash
mkdir -p .claude/skills && cp -r skill-state .claude/skills/
# or, for every project:
mkdir -p ~/.claude/skills && cp -r skill-state ~/.claude/skills/
```

Discovery is automatic from the `SKILL.md` frontmatter. Confirm with `/skills`, or just describe a long multi-step task and watch whether it loads.

**Then give it a state file to own.** The skill alone changes how the model *thinks*; the state file is what makes the benefit real. Put this in `CLAUDE.md`:

```markdown
## Long-horizon tasks

For any task longer than ~15 steps, use the skill-state pattern:

1. Author a schema and write `.agent/state.json` (one line, compact JSON).
2. Each step: re-read that file. Do **not** scroll back through the transcript.
3. Apply changes with the merge operator, never by hand-editing:
   `python .claude/skills/skill-state/scripts/skillstate.py merge \
        --state .agent/state.json --schema .agent/schema.json \
        --patch '{"...":"..."}' --in-place`
4. Set a key to `null` to delete it. Omitting a key leaves it unchanged.
5. Never store reasoning, notes or turn counts in the state file.
6. Before `/compact`, confirm the state file is current — it is your continuity.
```

**The `/compact` point is the whole game in Mode B.** Claude Code appends to a transcript and you cannot stop it. But once Σ lives on disk, compaction stops being data loss and becomes housekeeping: compact aggressively between phases, because the state file survives and the transcript is expendable. That is the inversion the pattern buys you here.

**Optional — hooks.** If your setup uses hooks, a `PostToolUse` hook can run the merge so the model never writes Σ directly. That moves you from "the model is asked to be disciplined" to "the runtime is disciplined", which is the point of `rules/validate-deterministic-rollback.md`.

---

## Qwen Code

**Prerequisite:** Node.js ≥ 22.

```bash
npm install -g @qwen-code/qwen-code@latest
mkdir -p .qwen/skills && cp -r skill-state .qwen/skills/     # project, committable
mkdir -p ~/.qwen/skills && cp -r skill-state ~/.qwen/skills/ # personal, all projects
```

Qwen Code discovers skills from both paths. Verify with `/skills`, invoke directly with `/skill-state`, and if it does not appear run `qwen --debug` — invalid YAML in the frontmatter fails the load silently otherwise.

Qwen Code's frontmatter accepts several fields the base skill does not use. Two are genuinely useful here.

**`paths:` — gate the skill until it is relevant.** Keeps it out of the model's skill listing until a matching file is touched:

```yaml
---
name: skill-state
description: ...
paths:
  - '.agent/state.json'
  - '**/*runtime*.py'
---
```

Caveat from Qwen's own docs: once a matching file is touched the skill stays active for the rest of the session, and invoking it yourself via `/skill-state` does **not** unlock model-side activation.

**`hooks:` — enforce the merge instead of requesting it.** Everything in a `SKILL.md` body is prompt text, so compliance depends on the model. A hook is code. This is the most direct way to satisfy "the runtime owns validation, not the model":

```yaml
---
name: skill-state
description: ...
hooks:
  PreToolUse:
    - matcher: run_shell_command
      hooks:
        - type: command
          command: '"$QWEN_SKILL_ROOT/scripts/guard-state.sh"'
---
```

```bash
#!/usr/bin/env bash
# guard-state.sh -- refuse to proceed if the state file drifted off-schema.
# Exit 2 blocks the tool call; stderr is fed back to the model as the reason.
set -u
STATE=".agent/state.json"; SCHEMA=".agent/schema.json"
[ -f "$STATE" ] || exit 0
if ! python3 "$QWEN_SKILL_ROOT/scripts/skillstate.py" \
        merge --state "$STATE" --schema "$SCHEMA" --patch '{}' >/dev/null 2>&1; then
  echo "State file violates the schema. Fix it before running further commands." >&2
  exit 2
fi
exit 0
```

Four ways this fails **open** and silently, all from Qwen's documentation — get them right or the gate is decoration:

- Omit `matcher:` and it compiles to `^$`, matching no tool. Use `*` if you mean every tool.
- Forget `chmod +x` and it never runs.
- Drop the inner quotes around `$QWEN_SKILL_ROOT/...` and a project path containing a space splits into two words.
- Under `cmd.exe` on Windows, `$QWEN_SKILL_ROOT` is not expanded and a `.sh` is not executable. Write the gate for the shell you actually have.

Also: session hooks live in memory, so `--continue` / `--resume` restores the skill's instructions **without** its hooks. Re-invoke the skill after resuming to re-arm the gate.

---

## Cursor

Cursor does not read `SKILL.md`. It has two mechanisms, and both work.

**Option 1 — `AGENTS.md` (simplest).** Cursor reads `AGENTS.md` from the project root and subdirectories. This repository's `AGENTS.md` is 46 lines precisely so it is safe to load unconditionally:

```bash
cp -r skill-state .cursor/skill-state
cp .cursor/skill-state/AGENTS.md ./AGENTS.md   # or merge into an existing one
```

**Option 2 — a project rule.** Project rules live in `.cursor/rules/` and **must** use the `.mdc` extension; a plain `.md` there is ignored because it has no frontmatter. Create `.cursor/rules/skill-state.mdc`:

```markdown
---
description: Long-horizon procedural execution with an explicit state file instead of a growing transcript. Use for multi-step migrations, large refactors, test-suite repair, or any task over ~15 steps.
alwaysApply: false
---

Maintain an explicit execution state in `.agent/state.json` (compact, one line).

- Each step, re-read the state file. Do not scroll back through the conversation.
- Change it only with the merge operator:
  `python .cursor/skill-state/scripts/skillstate.py merge --state .agent/state.json --patch '{...}' --in-place`
- `null` deletes a key. Omitting a key leaves it unchanged. `{}` is a no-op, not a clear.
- Never store reasoning, notes or turn counts in state.
- When the newest tool output contradicts the state, the output wins. Patch immediately.

@skill-state/SKILL.md
@skill-state/rules/patch-two-key-contract.md
```

Pick the frontmatter to match how you want it pulled in — this is Cursor's documented behaviour:

| `alwaysApply` | `description` | `globs` | Behaviour |
|---|---|---|---|
| `true` | — | — | Every chat session. Too aggressive for this skill. |
| `false` | provided | omitted | **Recommended.** The agent reads the description and pulls it in when relevant. |
| `false` | — | provided | Auto-attached when a matching file is in context, e.g. `globs: .agent/*.json` |
| `false` | omitted | omitted | Only on `@skill-state` mention. |

The trailing `@`-references matter: they pull those files into the rule's context instead of copying their text, so the rule stays short and cannot go stale. Cursor's own guidance is to keep rules under 500 lines and reference files rather than duplicate them — which is the same progressive-disclosure argument this repository makes.

---

## opencode

```bash
mkdir -p ~/.config/opencode/skills && cp -r skill-state ~/.config/opencode/skills/
# or project-scoped:
mkdir -p .opencode/skills && cp -r skill-state .opencode/skills/
```

Auto-discovered from the frontmatter. Because opencode also loads a repo-root `AGENTS.md` into every session, keep that file small — this repository's is, deliberately.

For the token curve, do **not** step the horizon in opencode's transcript. Install the local MCP (`python scripts/skillstate.py mcp`) and call `run_loop` — `references/mcp.md`. Inner generate is `api`. A plugin that only owns merge is still Mode B.

---

## Codex CLI

Codex picks up the repo-root `AGENTS.md` automatically:

```bash
cp -r skill-state vendor/skill-state
cat vendor/skill-state/AGENTS.md >> AGENTS.md
```

No skill mechanism, so the state-file discipline is the entire integration. It works, and it is the part that transfers anyway. For the token curve, same local MCP as opencode — `references/mcp.md`.

---

## Local MCP — Mode C

Stdio, this machine, stdlib. Inner generate is `api` or `stub`.

```bash
python scripts/skillstate.py mcp
```

Host JSON snippets (opencode, Cursor, Claude, Qwen), the five-tool contract, and the rule that LangGraph must **not** use this server: `references/mcp.md`.

If `status` fails, the process is not installed. Show the snippet; do not emulate Algorithm 1 in the host chat.

---

## LangGraph — Mode A

This is the best structural fit of any host, because **LangGraph's reducer mechanism is the ⊕ operator.** Do **not** put the MCP in front of the graph — that reintroduces a messages channel. Import `merge` / `Runtime` from `scripts/skillstate.py`.

**The one thing that decides whether this works:** do not use `MessagesState`, do not use `add_messages`, do not put a `messages` channel in the graph. That is the append-only transcript, and it is the baseline this pattern beats. The graph below has no message history at all — by construction, not by discipline.

````python
"""SKILL.state on LangGraph. Requires: langgraph, langchain-core, a provider."""
from typing import Annotated, Literal, TypedDict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command

from skillstate import SkillStateError, compact, parse_response, merge, validate_patch

MAX_RETRIES = 3
SCHEMA = {"inventory": {"*": "str?"}, "pending_intake": {"*": "str?"}}
RUNTIME_OWNED = ()
INSTRUCTIONS = "You are a warehouse agent. Actions: Store, Ship, Move, Wait, DONE."


def sigma_reducer(left, right):
    """The (+) operator as a LangGraph reducer.

    LangGraph calls reducer(left=accumulated_state, right=node_update), which is
    precisely the signature of Sigma_t (+) Delta-Sigma_t. Deep merge, null-deletion,
    absent-key preservation -- all of it comes from the same implementation the
    test suite pins.
    """
    return merge(left or {}, right or {})


class ExecState(TypedDict):
    sigma: Annotated[dict, sigma_reducer]  # the ONLY accumulating channel
    observation: str                       # default reducer: replaced, never appended
    action: str
    retries: int


def step(state: ExecState) -> Command[Literal["execute", "step"]]:
    """Build the prompt from exactly three inputs, validate, emit the patch."""
    prompt = (
        f"Instructions:\n{INSTRUCTIONS}\n\n"
        f"Skill Execution State:\n```json\n{compact(state['sigma'])}\n```\n\n"
        f"Latest Observation: {state['observation']}\n\n"
        "Provide step-by-step reasoning (it will be discarded), then one ```json "
        'block with exactly two keys: {"state_patch": {...}, "action": "..."}'
    )
    response = model.invoke(prompt).content  # noqa: F821 -- your chat model

    try:
        patch, action = parse_response(response)
        validate_patch(patch, SCHEMA, RUNTIME_OWNED)
    except SkillStateError as exc:
        if state["retries"] >= MAX_RETRIES:
            return Command(goto=END, update={"observation": f"aborted: {exc}"})
        return Command(goto="step", update={
            "observation": f"Your previous response was rejected: {exc}",
            "retries": state["retries"] + 1,
        })
    # The reasoning in `response` is never written to state. It dies here.
    if action == "DONE":
        return Command(goto=END, update={"sigma": patch})
    return Command(goto="execute",
                   update={"sigma": patch, "action": action, "retries": 0})


def execute(state: ExecState) -> Command[Literal["step"]]:
    """The only node with side effects. Its result is the next observation."""
    observation = environment(state["action"])  # noqa: F821 -- your environment
    return Command(goto="step", update={"observation": observation})


builder = StateGraph(ExecState)
builder.add_node("step", step)
builder.add_node("execute", execute)
builder.add_edge(START, "step")
# No static edges out of "step" or "execute": Command does the routing, and mixing
# the two would run both paths.
graph = builder.compile(checkpointer=InMemorySaver())
````

Run it:

```python
config = {"configurable": {"thread_id": "warehouse-1"}}
initial = {
    "sigma": {"inventory": {}, "pending_intake": {}},
    "observation": "Shipment arrived containing item_12.",
    "action": "",
    "retries": 0,
}
final = graph.invoke(initial, config)
print(compact(final["sigma"]))
```

### Seven things to get right

| # | Point |
|---|---|
| 1 | **No `messages` channel.** If you add one "for debugging", you have rebuilt the baseline and the guarantee is gone. Log the reasoning to a file instead — the invariant is that no *prompt* is built from it. |
| 2 | **`observation` uses the default reducer**, which replaces. That is correct: `O_t` is superseded by `O_{t+1}`, never accumulated. Giving it a merging reducer would quietly recreate history. |
| 3 | **LangGraph's documented reducer gotcha is SKILL.state gotcha #1.** With a merging reducer, returning an empty value does **not** clear the field — the empty update is merged in. Deletion must be explicit `null`. The two systems agree exactly, which is a good sign you are using the framework as intended. |
| 4 | **Do not reach for `Overwrite` to reset Σ.** It bypasses the reducer, which is replacement-instead-of-merge — anti-pattern #5, the 68% failure mode, now spelled with framework support. Delete with `null` instead. |
| 5 | **`⊕` is idempotent in the patch**, so LangGraph re-running a node after an interrupt or retry cannot double-apply a merge. `tests/run_tests.py` asserts this, because LangGraph explicitly warns that nodes re-execute from the start on resume. |
| 6 | **The checkpointer gives you Mode B's best property at the runtime level:** Σ is persisted per `thread_id`, so a crashed or interrupted run resumes from state rather than from nothing. Use a real checkpointer (SQLite, Postgres) in production; `InMemorySaver` is for demos. |
| 7 | **`stream_mode="updates"` emits exactly `ΔΣ_t`.** It is the cleanest trace you will get: one patch per step, no transcript. Note that `stream_mode="values"` emits *all* channels including private ones, so pass `output_keys` if you are filtering. |

### Bounding Σ

The graph cannot enforce boundedness for you. Add the check to the reducer, where every write already passes:

```python
SIGMA_BUDGET = 4000


def sigma_reducer_bounded(left, right):
    """Same operator, but an unbounded schema fails loudly instead of at step 200."""
    result = merge(left or {}, right or {})
    size = len(compact(result))
    if size > SIGMA_BUDGET:
        raise ValueError(
            f"|sigma| = {size} chars exceeds the {SIGMA_BUDGET} budget: a schema "
            "field is growing with the horizon. See rules/state-boundedness.md"
        )
    return result
```

An `O(T²)` regression is otherwise invisible until the run is expensive. Run `skillstate.py lint --schema` in CI as well.

---

## LangChain — Mode A

Plain LangChain has no state machine, which for once is an advantage: the chain is stateless by design, and Σ lives outside it. The per-step chain is genuinely three inputs in and a validated patch out.

**What not to do:** no `ConversationBufferMemory`, no `RunnableWithMessageHistory`, no chat-history placeholder. Those are the append-only pattern. There is no conversation here — there are `T` independent single-turn calls that happen to share a state object.

````python
"""SKILL.state as a LangChain chain. Requires: langchain-core, a provider."""
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnableLambda

from skillstate import SkillStateError, compact, merge, parse_response, validate_patch

SCHEMA = {"tests": {"*": {"status": ["pass", "fail"], "error": "str?"}},
          "fixes": {"*": "str?"}, "last_command": "str"}

prompt = ChatPromptTemplate.from_messages([
    ("system",
     "{instructions}\n\n"
     "Skill Execution State:\n```json\n{state}\n```\n\n"
     "Reply with step-by-step reasoning (it will be discarded), then exactly one "
     '```json block with exactly two keys: {{"state_patch": {{...}}, '
     '"action": "..."}}. Set a key to null to delete it; include only keys you '
     "are changing."),
    ("human", "Latest Observation: {observation}"),
])

# Note: no MessagesPlaceholder. There is no history channel to fill.
step_chain = prompt | model | StrOutputParser() | RunnableLambda(parse_response)  # noqa: F821


def run_episode(instructions, sigma, first_observation, horizon=100, max_retries=3):
    """The loop LangChain does not provide. Sigma is the only thing that persists."""
    observation = first_observation
    for _ in range(horizon):
        for _attempt in range(max_retries + 1):
            try:
                patch, action = step_chain.invoke({
                    "instructions": instructions,
                    "state": compact(sigma),
                    "observation": observation,
                })
                validate_patch(patch, SCHEMA)
                break
            except SkillStateError as exc:
                observation = f"Your previous response was rejected: {exc}"
        else:
            raise RuntimeError("retry budget exhausted; sigma untouched")

        sigma = merge(sigma, patch)      # commit only after validation
        if action == "DONE":
            return sigma
        observation = environment(action)  # noqa: F821
    return sigma
````

Two notes specific to LangChain:

- **Escape braces in the template.** `ChatPromptTemplate` treats `{...}` as a variable, so the literal JSON contract needs `{{` and `}}`. Get this wrong and you get a `KeyError` on `state_patch` — a confusing failure with a trivial cause.
- **`validate_patch` after the parser, not inside it.** `parse_response` enforces the envelope; the schema check needs `SCHEMA`, which is application knowledge. Keeping them separate is what lets the envelope error and the schema error carry different feedback to the model.

### Which to choose

| | LangChain | LangGraph |
|---|---|---|
| Loop | You write it | The graph is the loop |
| ⊕ operator | You call `merge` explicitly | It is the channel reducer |
| Persistence | You implement it | Checkpointer, per `thread_id` |
| Retry / interrupt | You write it | Built in, with idempotent merges |
| Best for | A short procedure, or embedding a step in a larger chain | Anything long-horizon, anything that must survive a restart |

For a genuine long-horizon skill, use LangGraph. The reducer *is* the architecture, and you get persistence and interrupts for free.
