#!/usr/bin/env python3
"""
CLI entry point and helper utilities for the SKILL.state runtime.

The command-line interface exposes each piece of the runtime as a
separate subcommand. This is useful for integration testing, manual
exploration, scripting, and as a reference for how the pieces fit
together.

.. rubric:: Subcommands

+----------------+----------------------------------------------------------+
| Subcommand     | Purpose                                                  |
+================+==========================================================+
| ``merge``      | Apply a patch to a state file (no model involved).       |
+----------------+----------------------------------------------------------+
| ``step``       | Parse a model response file, validate, merge.            |
+----------------+----------------------------------------------------------+
| ``lint``       | Check a schema for horizon-unbounded fields.             |
+----------------+----------------------------------------------------------+
| ``compact``    | Re-serialise a state file compactly (whitespace stripped).|
+----------------+----------------------------------------------------------+
| ``exec``       | One-shot model call via a host adapter.                  |
+----------------+----------------------------------------------------------+
| ``loop``       | Full Algorithm-1 execution (Mode C standalone).          |
+----------------+----------------------------------------------------------+
| ``mcp``        | Start the local stdio MCP server.                        |
+----------------+----------------------------------------------------------+
| ``self-test``  | Built-in contract smoke test (no network, < 1 second).   |
+----------------+----------------------------------------------------------+

.. rubric:: References

- ``references/execution-loop.md`` — Algorithm 1 formal specification.
- Each subcommand maps directly to a public function in the package.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import sys

from .errors import ContractError, SkillStateError, ValidationError
from .hosts import HOSTS, build_host_argv, exec_once, extract_host_response
from .lint import lint_schema
from .loop import run_loop
from .mcp import serve_mcp
from .merge import merge
from .parse import parse_response
from .serialization import compact
from .step import apply_step
from .validate import validate_patch


# =============================================================================
# CLI FILE-LOADING HELPERS
# =============================================================================


def _load(path):
    """Load and parse a JSON file.

    Args:
        path: File path to a JSON file.

    Returns:
        The parsed JSON value (usually ``dict``).
    """
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _read(path):
    """Read a text file as a string.

    Args:
        path: File path to a text file.

    Returns:
        The entire file contents as a ``str``.
    """
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _patch_arg(value):
    """Parse a ``--patch`` argument: try inline JSON first, then file path.

    If the value starts with ``{`` it is interpreted as an inline JSON
    string. Otherwise it is treated as a file path to a JSON file.

    Args:
        value: The raw argument string from ``--patch``.

    Returns:
        The parsed JSON value (usually ``dict``).
    """
    if value.lstrip().startswith("{"):
        return json.loads(value)
    return _load(value)


# =============================================================================
# MAIN CLI ENTRY POINT
# =============================================================================


def main(argv=None):
    """Main CLI entry point.

    Parses command-line arguments, dispatches to the appropriate
    subcommand handler, and returns an exit code.

    Args:
        argv:
            Argument list (defaults to ``sys.argv[1:]`` if ``None``).

    Returns:
        ``int`` — exit code:
        ``0`` = success,
        ``1`` = validation failure,
        ``2`` = usage error.
    """
    ap = argparse.ArgumentParser(
        prog="skillstate",
        description=(
            "SKILL.state reference runtime: parse, validate, merge, "
            "exec, loop."
        ),
    )
    sub = ap.add_subparsers(dest="cmd")

    # =========================================================================
    # ``merge`` — apply a patch to a state file
    # =========================================================================
    m = sub.add_parser(
        "merge", help="apply a patch to a state file"
    )
    m.add_argument("--state", required=True, help="path to the state JSON file")
    m.add_argument(
        "--patch", required=True,
        help="inline JSON or a file path containing the patch",
    )
    m.add_argument("--schema", help="path to the schema JSON file (optional)")
    m.add_argument(
        "--runtime-owned", default="",
        help="comma-separated dotted paths the model cannot write",
    )
    m.add_argument(
        "--in-place", action="store_true",
        help="write the result back to --state (overwrites)",
    )
    m.add_argument(
        "--output",
        help="write the result to this file instead of stdout",
    )

    # =========================================================================
    # ``step`` — parse a model response, validate, merge
    # =========================================================================
    s = sub.add_parser(
        "step", help="parse a model response, validate, merge"
    )
    s.add_argument("--state", required=True, help="path to the state JSON file")
    s.add_argument(
        "--response", required=True,
        help="file containing the raw model response text",
    )
    s.add_argument("--schema", help="path to the schema JSON file (optional)")
    s.add_argument("--runtime-owned", default="")
    s.add_argument(
        "--in-place", action="store_true",
        help="write the result back to --state",
    )

    # =========================================================================
    # ``lint`` — check a schema for horizon-unbounded fields
    # =========================================================================
    l = sub.add_parser(
        "lint", help="check a schema for horizon-unbounded fields"
    )
    l.add_argument("--schema", required=True, help="path to the schema JSON file")
    l.add_argument(
        "--state",
        help="optional: check current state size against --budget",
    )
    l.add_argument(
        "--budget", type=int,
        help="sigma budget in characters for state-size check",
    )

    # =========================================================================
    # ``compact`` — re-serialise state compactly
    # =========================================================================
    c = sub.add_parser(
        "compact", help="re-serialize a state file compactly"
    )
    c.add_argument("--state", required=True, help="path to the state JSON file")

    # =========================================================================
    # ``exec`` — one-shot model call via a host adapter
    # =========================================================================
    e = sub.add_parser(
        "exec", help="one-shot model call via a host adapter"
    )
    e.add_argument("--host", required=True, choices=list(HOSTS),
                   help="host adapter to use")
    e.add_argument("--model", default="", help="model identifier string")
    e.add_argument(
        "--system-prompt", default="",
        help="inline system prompt (immutable skill spec P)",
    )
    e.add_argument(
        "--system-prompt-file", default="",
        help="file containing the system prompt",
    )
    e.add_argument(
        "--prompt", default="",
        help="inline user prompt (sigma + observation)",
    )
    e.add_argument(
        "--prompt-file", default="",
        help="file containing the user prompt",
    )
    e.add_argument(
        "--timeout", type=int, default=180,
        help="seconds before the call is aborted",
    )
    e.add_argument(
        "--dir", default="",
        help="working directory for opencode --dir",
    )
    e.add_argument("--agent", default="", help="opencode agent name")
    e.add_argument("--title", default="", help="opencode session title")

    # =========================================================================
    # ``loop`` — full Algorithm-1 execution (Mode C)
    # =========================================================================
    lp = sub.add_parser(
        "loop", help="Mode C: run Algorithm 1 via host one-shots"
    )
    lp.add_argument("--host", required=True, choices=list(HOSTS),
                    help="host adapter for model calls")
    lp.add_argument("--model", default="", help="model identifier string")
    lp.add_argument(
        "--instructions", required=True,
        help="file containing the immutable skill spec P",
    )
    lp.add_argument(
        "--schema", required=True,
        help="path to the schema JSON file",
    )
    lp.add_argument(
        "--state", required=True,
        help="initial state JSON (updated in place)",
    )
    lp.add_argument(
        "--env-observe", required=True,
        help="shell command that prints O_t to stdout",
    )
    lp.add_argument(
        "--env-action", required=True,
        help=(
            "shell command to execute actions; use {action} placeholder "
            "for the validated action string"
        ),
    )
    lp.add_argument(
        "--horizon", type=int, default=50,
        help="maximum number of steps (default 50)",
    )
    lp.add_argument(
        "--max-retries", type=int, default=3,
        help="per-step retry budget (default 3)",
    )
    lp.add_argument(
        "--timeout", type=int, default=180,
        help="seconds per model call (default 180)",
    )
    lp.add_argument(
        "--runtime-owned", default="",
        help="comma-separated dotted paths the model cannot write",
    )
    lp.add_argument(
        "--sigma-budget", type=int, default=None,
        help="max chars for compact(state) — raises if exceeded",
    )
    lp.add_argument(
        "--trace", default="",
        help="append per-step JSONL metrics to this file",
    )
    lp.add_argument("--dir", default="", help="working directory for subprocesses")
    lp.add_argument("--agent", default="", help="opencode agent name")
    lp.add_argument(
        "--result", default="",
        help="write final result JSON to this file",
    )

    # =========================================================================
    # ``mcp`` — start the local stdio MCP server
    # =========================================================================
    sub.add_parser(
        "mcp", help="local stdio MCP: Algorithm 1 (api/stub generate)"
    )

    # =========================================================================
    # ``self-test`` — built-in contract smoke test
    # =========================================================================
    sub.add_parser(
        "self-test", help="run the built-in contract smoke test"
    )

    args = ap.parse_args(argv)

    # No subcommand → print help and exit with usage error.
    if args.cmd is None:
        ap.print_help()
        return 2

    # =========================================================================
    # DISPATCH — route to subcommand handler
    # =========================================================================

    # -- self-test: built-in smoke test (no network) --
    if args.cmd == "self-test":
        return _self_test()

    # -- mcp: local MCP stdio server --
    if args.cmd == "mcp":
        return serve_mcp()

    # -- compact: re-serialise a state file compactly --
    # Read the state file, compact it, print to stdout.
    if args.cmd == "compact":
        print(compact(_load(args.state)))
        return 0

    # -- lint: schema boundedness check --
    if args.cmd == "lint":
        schema = _load(args.schema)
        findings = lint_schema(schema)

        # Optional: check the current state size against a declared
        # sigma budget. This is a runtime check that's complementary
        # to the static schema lint — a schema might be bounded but
        # the data in it might have grown beyond the budget.
        if args.state and args.budget:
            size = len(compact(_load(args.state)))
            verdict = "OK" if size <= args.budget else "OVER BUDGET"
            print("state size: %d chars (budget %d) -- %s" % (
                size, args.budget, verdict
            ))
            if size > args.budget:
                findings.append(
                    "current state already exceeds the declared budget"
                )

        for f in findings:
            print("warn: %s" % f)
        if not findings:
            print("schema is bounded with respect to the horizon")
        # Exit code 1 if there are warnings (for CI / scripting).
        return 1 if findings else 0

    # -- exec: one-shot model call --
    if args.cmd == "exec":
        # Resolve system prompt: inline arg takes precedence over file.
        system = args.system_prompt
        if args.system_prompt_file:
            system = _read(args.system_prompt_file)

        # Resolve user prompt: inline arg takes precedence over file.
        prompt = args.prompt
        if args.prompt_file:
            prompt = _read(args.prompt_file)

        if not prompt:
            print(
                "exec: --prompt or --prompt-file required",
                file=sys.stderr,
            )
            return 2

        extra = {}
        if args.dir:
            extra["dir"] = args.dir
        if args.agent:
            extra["agent"] = args.agent
        if args.title:
            extra["title"] = args.title

        try:
            result = exec_once(
                args.host, args.model, system, prompt,
                timeout=args.timeout, extra=extra or None,
            )
        except Exception as exc:  # noqa: BLE001
            # Broad catch: exec_once can raise RuntimeError for many
            # reasons (API errors, timeouts, missing binaries).
            print("exec failed: %s" % exc, file=sys.stderr)
            return 1

        # Model response to stdout, usage stats to stderr.
        # This split allows piping the response text while still
        # seeing token usage on the terminal.
        print(result["text"])
        print(
            "usage: in=%d out=%d total=%d wall=%.2fs rc=%s"
            % (
                result["usage"]["input"],
                result["usage"]["output"],
                result["usage"]["total"],
                result["wall_s"],
                result.get("returncode"),
            ),
            file=sys.stderr,
        )
        return 0

    # -- loop: full Algorithm-1 execution --
    if args.cmd == "loop":
        instructions = _read(args.instructions)
        schema = _load(args.schema)
        state = _load(args.state)

        # Parse runtime-owned fields from comma-separated string.
        # Empty string produces an empty tuple (no owned fields).
        owned = tuple(p for p in args.runtime_owned.split(",") if p)

        extra = {}
        if args.dir:
            extra["dir"] = args.dir
        if args.agent:
            extra["agent"] = args.agent

        # Clean up old trace file before starting a fresh run.
        # The trace is append-only during the loop, so we need to
        # remove any stale file from a previous run.
        if args.trace and os.path.isfile(args.trace):
            os.remove(args.trace)

        try:
            result = run_loop(
                host=args.host,
                instructions=instructions,
                schema=schema,
                state=state,
                observe_cmd=args.env_observe,
                action_cmd=args.env_action,
                model=args.model or None,
                horizon=args.horizon,
                max_retries=args.max_retries,
                runtime_owned=owned,
                sigma_budget=args.sigma_budget,
                timeout=args.timeout,
                trace_path=args.trace or None,
                extra=extra or None,
                workdir=args.dir or None,
            )
        except Exception as exc:  # noqa: BLE001
            print("loop failed: %s" % exc, file=sys.stderr)
            return 1

        # Persist final state to the state file (in-place update).
        # This is the Mode B survival guarantee: even if the loop
        # process is killed, the state file was updated on every step.
        with open(args.state, "w", encoding="utf-8") as fh:
            fh.write(compact(result["state"]) + "\n")

        # Build a comprehensive result summary for --result output.
        summary = {
            "steps": result["steps"],
            "actions": result["actions"],
            "avg_prompt_chars": result["avg_prompt_chars"],
            "max_prompt_chars": result["max_prompt_chars"],
            "prompt_growth": result["prompt_growth"],
            "prompt_sizes": result["prompt_sizes"],
            "totals": result["totals"],
            "state": result["state"],
        }
        if args.result:
            with open(args.result, "w", encoding="utf-8") as fh:
                json.dump(summary, fh, indent=2, ensure_ascii=False)
                fh.write("\n")

        # Print compact summary to stdout.
        # This is what the caller sees — not the full state or actions,
        # but the metrics that matter for evaluation.
        print(json.dumps({
            "steps": result["steps"],
            "avg_prompt_chars": round(result["avg_prompt_chars"], 1),
            "max_prompt_chars": result["max_prompt_chars"],
            "prompt_growth": round(result["prompt_growth"], 3),
            "tokens": result["totals"],
            "actions": result["actions"],
        }, ensure_ascii=False, indent=2))
        return 0

    # =========================================================================
    # SHARED — ``merge`` and ``step`` subcommands
    # =========================================================================
    owned = tuple(p for p in args.runtime_owned.split(",") if p)
    schema = _load(args.schema) if args.schema else None
    state = _load(args.state)

    try:
        if args.cmd == "merge":
            # ``merge`` applies a patch directly — no parsing needed.
            # Useful for testing merge semantics without a model.
            patch = _patch_arg(args.patch)
            if schema is not None:
                validate_patch(patch, schema, owned)
            result = merge(state, patch)
            action = None
        else:
            # ``step``: full ``parse → validate → merge`` pipeline.
            # The response file contains the raw model output.
            result, action = apply_step(
                state, _read(args.response), schema, owned
            )
    except SkillStateError as exc:
        # On validation failure: print the rejection message to stderr,
        # state is untouched, exit code 1 for scripting / CI.
        # The rejection message is designed to be fed back to the model
        # as the next observation (rollback-retry).
        print("REJECTED: %s" % exc, file=sys.stderr)
        print(
            "state unchanged; feed the message above back as the next "
            "observation.",
            file=sys.stderr,
        )
        return 1

    # Write the result to the appropriate destination.
    out = compact(result)
    target = (
        args.state if getattr(args, "in_place", False)
        else getattr(args, "output", None)
    )
    if target:
        with open(target, "w", encoding="utf-8") as fh:
            fh.write(out + "\n")
    else:
        print(out)

    # Print the action to stderr if present (so stdout has only state).
    if action is not None:
        print("action: %s" % action, file=sys.stderr)

    return 0


# =============================================================================
# SELF-TEST — _self_test()
# =============================================================================
# Built-in contract smoke test. Runs without network or external
# dependencies. Covers:
#
#   - Merge semantics (null-deletion, deep merge, immutability).
#   - Envelope parsing (valid, extra keys, missing keys, syntax errors).
#   - Schema validation (types, nullability, enums, runtime-owned).
#   - Schema lint (list/untyped detection).
#   - Host argv builders (pure, no I/O).
#   - Stub loop flatness (the key architectural invariant).
#   - API response extraction.
#
# The exhaustive test suite lives in ``../tests/run_tests.py`` — this
# is a quick sanity check that runs in < 1 second with zero network
# calls.
# =============================================================================


def _self_test():
    """Contract smoke test — runs in < 1 second, no network.

    Returns:
        ``int`` — ``0`` on all checks passed, ``1`` on first failure.
    """
    checks = 0

    def ok(cond, label):
        """Assert ``cond`` is true. Increments the check counter.

        Args:
            cond: A boolean condition to verify.
            label: Human-readable description of the check.
        """
        nonlocal checks
        checks += 1
        if not cond:
            print("FAIL: %s" % label)
            sys.exit(1)

    def raises(fn, exc, label):
        """Assert ``fn()`` raises an exception of type ``exc``.

        Args:
            fn: A zero-argument callable.
            exc: The expected exception class (or tuple of classes).
            label: Human-readable description of the check.
        """
        nonlocal checks
        checks += 1
        try:
            fn()
        except exc:
            return          # expected — test passes
        except Exception as e:  # noqa: BLE001
            print("FAIL: %s (raised %r instead)" % (label, e))
            sys.exit(1)
        print("FAIL: %s (no error raised)" % label)
        sys.exit(1)

    # =========================================================================
    # MERGE OPERATOR
    # =========================================================================

    # Basic null-deletion: key set to None is removed from the state.
    ok(
        merge({"a": 1, "b": 2}, {"b": None}) == {"a": 1},
        "null deletes key",
    )

    # Absent keys in the patch leave state keys untouched.
    # This is Rule 2 of merge: keys absent in the patch survive in
    # the result.
    ok(
        merge({"i": {"s1": "A", "s2": "B"}}, {"i": {"s2": None}})
        == {"i": {"s1": "A"}},
        "absent keys preserved during deep delete",
    )

    # Empty patch is a no-op. Merging ``{}`` produces a copy of the
    # state with no changes.
    ok(
        merge({"i": {"s1": "A"}}, {"i": {}}) == {"i": {"s1": "A"}},
        "empty patch is no-op",
    )

    # Immutability: the original state dict is never mutated.
    # ``merge`` deep-copies before any changes.
    original = {"a": {"b": 1}}
    merge(original, {"a": {"c": 2}})
    ok(original == {"a": {"b": 1}}, "state never mutated by merge")

    # =========================================================================
    # ENVELOPE PARSING
    # =========================================================================

    # Happy path: reasoning text + fenced JSON block with both keys.
    p, a = parse_response(
        'reasoning\n```json\n{"state_patch":{"x":1},"action":"Go"}\n```'
    )
    ok((p, a) == ({"x": 1}, "Go"), "envelope parsed correctly")

    # Extra key in the JSON block → rejected (ContractError).
    # The model must emit exactly {state_patch, action}.
    raises(
        lambda: parse_response(
            '```json\n{"state_patch":{},"action":"a","c":1}\n```'
        ),
        ContractError, "extra key rejected",
    )

    # Missing state_patch → rejected.
    raises(
        lambda: parse_response('```json\n{"action":"a"}\n```'),
        ContractError, "missing state_patch rejected",
    )

    # Trailing comma → JSON parse error → ContractError.
    raises(
        lambda: parse_response(
            '```json\n{"state_patch":{},"action":"a",}\n```'
        ),
        ContractError, "trailing comma rejected",
    )

    # No fenced block at all → rejected with actionable message.
    raises(
        lambda: parse_response("no block here"),
        ContractError, "no block rejected",
    )

    # =========================================================================
    # SCHEMA VALIDATION
    # =========================================================================

    schema = {
        "inventory": {"*": "str?"},     # open map, deletable entries
        "cwd": "str",                    # closed key, NOT nullable
        "ci": ["pass", "fail"],         # enum of two values
    }

    # Open-map null deletion → valid.
    # Entries in an open map are always deletable.
    validate_patch({"inventory": {"shelf_42": None}}, schema)

    # Non-nullable field set to null → rejected.
    # ``cwd`` is "str" (not "str?") — cannot be deleted.
    raises(
        lambda: validate_patch({"cwd": None}, schema),
        ValidationError, "non-nullable leaf not deletable",
    )

    # Unknown key → rejected.
    # The model tried to write a field not declared in the schema.
    raises(
        lambda: validate_patch({"nope": 1}, schema),
        ValidationError, "unknown key rejected",
    )

    # Type coercion: list where string expected → rejected.
    raises(
        lambda: validate_patch({"cwd": ["/tmp"]}, schema),
        ValidationError, "type coercion rejected",
    )

    # Enum violation: value not in declared enum → rejected.
    raises(
        lambda: validate_patch({"ci": "flaky"}, schema),
        ValidationError, "enum violation rejected",
    )

    # Runtime-owned field: model can't write to "cwd" when it's owned.
    # This prevents the model from overwriting ground-truth data.
    raises(
        lambda: validate_patch(
            {"cwd": "/tmp"}, schema, runtime_owned=("cwd",)
        ),
        ValidationError, "runtime-owned field rejected",
    )

    # =========================================================================
    # SCHEMA LINT
    # =========================================================================

    # ``list`` field → flagged as growth risk (each step appends).
    ok(
        lint_schema({"tried": "list"}),
        "list field flagged as growth risk",
    )

    # Keyed map ({"*": ...}) → bounded, no warnings.
    ok(
        not lint_schema({"tried": {"*": "str"}}),
        "keyed map is bounded",
    )

    # =========================================================================
    # HOST ARGV BUILDERS (pure — no I/O, no network)
    # =========================================================================

    # qwen: ``--bare``, ``--safe-mode``, ``--system-prompt`` flags.
    q = build_host_argv("qwen", "m", "SYS", "USR")
    ok(
        q[0] == "qwen" and "--bare" in q and "--safe-mode" in q
        and "--system-prompt" in q,
        "qwen argv includes bare+safe+system",
    )

    # opencode: system prompt folded into body, ``--pure`` and
    # ``--format json`` flags.
    o = build_host_argv(
        "opencode", "openrouter/x", "SYS", "USR", {"title": "t"}
    )
    ok(
        o[0] == "opencode" and "--format" in o and "--pure" in o
        and "json" in o and any("SYS" in a for a in o),
        "opencode folds system into body",
    )

    # api: sentinel argv — handled in-process by exec_once.
    ok(
        build_host_argv("api", "x", "", "y")[0] == "api",
        "api sentinel argv correct",
    )

    # claude: ``--system-prompt`` flag passed natively.
    cl = build_host_argv("claude", "m", "S", "U")
    ok(
        cl[0] == "claude" and "--system-prompt" in cl,
        "claude system flag present",
    )

    # =========================================================================
    # STUB LOOP — FLAT PROMPT TEST (the key architectural invariant)
    # =========================================================================
    # With stub responses, the prompt must stay nearly flat across the
    # full horizon. If the prompt grows, the pattern is broken — the
    # state is accumulating unbounded data.

    responses = []
    for i in range(5):
        act = "DONE" if i == 4 else "Wait"
        responses.append(
            'r\n```json\n{"state_patch":{"tick":"%d"},'
            '"action":"%s"}\n```' % (i, act)
        )

    def fake_act(a):
        """Minimal action handler for stub loop — returns observation."""
        return "after %s" % a

    loop_out = run_loop(
        host="stub",
        instructions="You are a test agent. Actions: Wait, DONE.",
        schema={"tick": "str?"},
        state={"tick": "0"},
        observe_cmd="true",
        action_cmd=fake_act,
        horizon=5,
        stub_responses=responses,
        initial_observation="start",
    )

    sizes = loop_out["prompt_sizes"]
    ok(len(sizes) == 5, "stub loop ran 5 steps")
    # The prompt must stay nearly flat — if it grew by more than 80
    # characters across 5 steps, something is accumulating in the state
    # that shouldn't be.
    ok(
        max(sizes) - min(sizes) < 80,
        "stub loop prompt nearly flat (delta < 80 chars)",
    )
    ok(
        loop_out["actions"][-1] == "DONE",
        "stub loop ends on DONE action",
    )
    ok(
        loop_out["state"].get("tick") == "4",
        "stub loop state advanced to tick 4",
    )

    # =========================================================================
    # API RESPONSE EXTRACTION
    # =========================================================================

    text, usage = extract_host_response(
        "api",
        json.dumps({
            "choices": [{"message": {"content": "hello"}}],
            "usage": {
                "prompt_tokens": 3,
                "completion_tokens": 1,
                "total_tokens": 4,
            },
        }),
    )
    ok(
        text == "hello" and usage["total"] == 4,
        "api extract returns correct text and usage",
    )

    # =========================================================================
    # ALL CHECKS PASSED
    # =========================================================================
    print("self-test: %d checks passed" % checks)
    return 0


if __name__ == "__main__":
    sys.exit(main())