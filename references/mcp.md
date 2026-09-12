# Local MCP — Mode C for hosts you do not control

> **Moral: the server must run Algorithm 1. A merge tool inside the host chat is Mode B with extra roundtrips.**

This is the install path for Cursor, opencode, Qwen, Claude Code and similar: a **local stdio** process that owns `(P, Σ, O)` → generate → validate → ⊕ → discard `R`. The host starts an episode; it does not step the horizon in its own transcript.

LangGraph and LangChain **do not use this MCP.** They already own the loop — import `Runtime` / `merge` from `scripts/skillstate.py`. Wiring MCP into a graph that also has a `messages` channel is the paper's Stateful baseline.

There is no network listener. Σ stays on disk. The only outbound call is the model API the user already configured (`OPENROUTER_API_KEY` / `OPENAI_API_KEY` / a key file), or a stub in tests.

## What the host is allowed to do

```text
status → init → run_loop     # preferred: parent transcript O(1) in T
status → init → step* → sigma?   # only when the environment is the host's tools
```

`run_loop` inner generate is **`api` or `stub`**, never `opencode`. Using the host CLI as the inner model recreates Mode B (see `benchmarks/model-opencode-cli-benchmark-output.md`).

## Tools

| Tool | Returns | Do not |
|---|---|---|
| `status` | `{ok, version, sessions}` | skip this check |
| `init` | `{session_id}` only | dump schema/Σ back |
| `run_loop` | `{steps, last_action, done, tokens, prompt_growth, avg_prompt_chars, sigma_bytes}` | return Σ, `R`, or the action list |
| `step` | `{action}` | return Σ |
| `sigma` | compact Σ | call every turn |

There is **no** `merge`, **no** `get_state`, **no** `apply_patch`. Those copy Σ into the host transcript, which is the cost the Mode B live run measured.

## Install

Same binary everywhere:

```bash
python scripts/skillstate.py mcp
```

### opencode

```json
{
  "mcp": {
    "skillstate": {
      "type": "local",
      "command": ["python", "scripts/skillstate.py", "mcp"],
      "enabled": true
    }
  }
}
```

### Cursor

`.cursor/mcp.json`:

```json
{
  "mcpServers": {
    "skillstate": {
      "command": "python",
      "args": ["scripts/skillstate.py", "mcp"]
    }
  }
}
```

### Claude Code / Claude Desktop

```json
{
  "mcpServers": {
    "skillstate": {
      "command": "python",
      "args": ["scripts/skillstate.py", "mcp"]
    }
  }
}
```

### Qwen Code

Point the local MCP config at the same command. Then `/skills` plus `status` — if `status` fails, the process is not running.

## Agent procedure (inside a Mode B host)

1. Load only `references/execution-card.md` for the contract. Do not open the rest of this skill mid-task.
2. Call `status`. If it errors, show the user the install snippet for this host, then stop.
3. Author the schema once (`rules/schema-state-schema-authoring.md`). `init` with `host=api` and a `state_path` (`.agent/state.json`).
4. Prefer `run_loop` with `env_observe` / `env_action` shell commands. One tool result for the whole horizon.
5. If the environment *is* the host (read a file, run tests), use `step` with **only** the latest observation. Never re-send prior observations.
6. Compact the host session between phases. Continuity is `state_path`, not the chat. Call `sigma` only when resuming after compact.

## What this is not

- Not a token win if you `step` T times and paste every observation into the host chat — that is Mode B.
- Not the paper's 16.2× figure unless inner generate is a bare API and the parent makes ~one call. Cite `references/evidence.md` for paper numbers; cite the Mode A live table for a short-horizon API re-run.
- Not a replacement for LangGraph's reducer. If you control prompt assembly, you are in Mode A — implement Algorithm 1 directly (`references/integrations.md`).
