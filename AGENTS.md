# AGENTS.md — skill-state

This repository is an **agent skill**, not an application. It teaches the SKILL.state runtime pattern for long-horizon procedural execution.

**Start at [`SKILL.md`](SKILL.md).** It is the index: the step contract, the Mode A / B / C fork, the rule table in reading order, and nine gotchas. Load individual `rules/` and `references/` files on demand — that is the point of the layout, and this file stays deliberately short because tools that honour the [AGENTS.md](https://agents.md) convention inject it into *every* session. A 300-line guide here would violate the very thesis the repository argues for.

## The pattern in six lines

```text
A_t = ( P , Σ_t , O_t )   →   ( R_t , ΔΣ_t , a_t )
  P   immutable skill spec        R_t   reasoning — discarded after validation
  Σ_t structured state            ΔΣ_t  state patch — JSON dict, null = delete
  O_t latest observation ONLY     a_t   the action to execute

Σ_{t+1} = Σ_t ⊕ ΔΣ_t      # deep merge, null-deletion, validated by the runtime
```

Nothing accumulates, so the prompt is flat in the horizon. Reasoning that matters for the future is projected into Σ by the patch; everything else is destroyed.

## Before changing anything here

- **Every measured claim lives in [`references/evidence.md`](references/evidence.md)** and nowhere else. Do not introduce a number in any other file without citing that page. The test suite enforces this.
- **The examples are executable.** `examples/*.md` trajectories are replayed against the merge operator by the test suite; edit the prose and the arithmetic together or the build breaks.
- **Run the tests.** `python tests/run_tests.py` covers merge semantics, the envelope contract, schema validation, the retry loop, the example trajectories, and documentation integrity (dead links, nested fences, broken tables, orphaned files).

```bash
python scripts/skillstate.py self-test   # fast contract smoke test
python tests/run_tests.py                # full suite
```

## Layout

| Path | Contents |
|---|---|
| `SKILL.md` | Entry point: contract, modes, rule index, gotchas |
| `rules/` | 11 rules, one lesson each, ordered by impact |
| `references/` | Prompt templates, merge semantics, execution loop, schemas, anti-patterns, evidence, host adaptation |
| `examples/` | Two verified trajectories (warehouse, CTF) |
| `scripts/skillstate.py` | Reference implementation: parse → validate → merge |
| `tests/run_tests.py` | Full suite, stdlib only |

## The one thing most adopters get wrong

Copying this folder into an agent's skills directory does **not** make that agent's prompt bounded. Claude Code, opencode, Cursor, Codex and the rest append to a transcript, and a Markdown file cannot stop them. Mode C is the local MCP (`python scripts/skillstate.py mcp`) or `skillstate.py loop` — Algorithm 1 outside the host chat. Merge-only MCP is still Mode B. Read [`references/host-runtime-adaptation.md`](references/host-runtime-adaptation.md) and [`references/mcp.md`](references/mcp.md).

Source: Badhe, Tiwari & Chung, *SKILL.state: Scalable Long-Horizon Agent Skills*, arXiv:2608.26263 (EMNLP 2026).
