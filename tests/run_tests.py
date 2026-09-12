#!/usr/bin/env python3
"""
SKILL.state test suite. Stdlib only, no pytest.

    python tests/run_tests.py [-v]

Four sections:

  1. merge        -- the (+) operator's documented semantics
  2. contract     -- envelope parsing and schema validation
  3. runtime      -- the step loop, rollback-retry, budget enforcement
  4. trajectories -- replays every example in examples/ against the merge
                     operator, so the docs cannot drift from the arithmetic
  5. docs         -- structural integrity of every Markdown file

Section 4 is the load-bearing one: it makes the prose executable.
"""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile

ROOT = os.path.dirname(os.path.abspath(os.path.join(__file__, "..")))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from skillstate import (  # noqa: E402
    ContractError,
    Runtime,
    SkillStateError,
    ValidationError,
    apply_step,
    compact,
    lint_schema,
    merge,
    parse_response,
    validate_patch,
    McpServer,
    dispatch_mcp,
    serve_mcp,
)

VERBOSE = "-v" in sys.argv
FAILURES = []
COUNT = 0

# The count skillstate.py's own smoke test reports, and a slot for this suite's
# final total. The docs-integrity section asserts every documented figure
# matches these, so a stale number in a README fails the build.
SELF_TEST_CHECKS = 25
COUNT_TOTAL = [0]


def check(label, fn):
    """Run one assertion-style check. fn() must return True or raise."""
    global COUNT
    COUNT += 1
    try:
        result = fn()
    except Exception as exc:  # noqa: BLE001
        FAILURES.append("%s -- raised %s: %s" % (label, type(exc).__name__, exc))
        print("  FAIL %s -- %s: %s" % (label, type(exc).__name__, exc))
        return
    if result is False:
        FAILURES.append(label)
        print("  FAIL %s" % label)
    elif VERBOSE:
        print("  ok   %s" % label)


def empty(label, items, noun):
    """Fail with a printed inventory when `items` is non-empty."""
    def run():
        if not items:
            return True
        print("       %s: %s" % (noun, ", ".join(sorted(items))))
        return False
    check(label, run)


def rejects(label, fn, exc=SkillStateError):
    def run():
        try:
            fn()
        except exc:
            return True
        return False
    check(label, run)


# --- 1. merge semantics -----------------------------------------------------


def section_merge():
    print("merge semantics:")
    check("leaf overwrite",
          lambda: merge({"mode": "idle"}, {"mode": "shipping"}) == {"mode": "shipping"})
    check("absent key preserved (delta, not replacement)",
          lambda: merge({"i": {"s1": "A", "s2": "B"}}, {"i": {"s2": None}})
          == {"i": {"s1": "A"}})
    check("nested insert",
          lambda: merge({"i": {"s1": "A"}}, {"i": {"s2": "B"}})
          == {"i": {"s1": "A", "s2": "B"}})
    check("deep recursion, 3 levels",
          lambda: merge({"a": {"b": {"c": 1, "d": 2}}}, {"a": {"b": {"c": 9}}})
          == {"a": {"b": {"c": 9, "d": 2}}})
    check("null deletes at top level",
          lambda: merge({"a": 1, "b": 2, "c": 3}, {"b": None}) == {"a": 1, "c": 3})
    check("null deletes a whole subtree",
          lambda: merge({"a": {"b": {"c": 1}}, "z": 0}, {"a": None}) == {"z": 0})
    check("null on a missing key is a no-op, not an error",
          lambda: merge({"a": 1}, {"b": None}) == {"a": 1})
    check("empty patch is a no-op",
          lambda: merge({"a": 1, "b": 2}, {}) == {"a": 1, "b": 2})
    check("empty nested patch is a no-op (does NOT clear the map)",
          lambda: merge({"i": {"s1": "A"}}, {"i": {}}) == {"i": {"s1": "A"}})
    check("new top-level key is inserted",
          lambda: merge({"a": 1}, {"b": 2}) == {"a": 1, "b": 2})
    check("list is overwritten wholesale, not merged",
          lambda: merge({"x": ["a", "b"], "m": 1}, {"x": ["c"]}) == {"x": ["c"], "m": 1})
    check("falsy leaves are written, not treated as absent",
          lambda: merge({"a": 1, "b": "x", "c": True}, {"a": 0, "b": "", "c": False})
          == {"a": 0, "b": "", "c": False})

    rejects("dict -> scalar coercion rejected",
            lambda: merge({"i": {"s": "A"}}, {"i": "oops"}), ValidationError)
    rejects("scalar -> dict coercion rejected",
            lambda: merge({"m": "idle"}, {"m": {"sub": 1}}), ValidationError)
    rejects("non-dict state rejected", lambda: merge([], {}), ValidationError)
    rejects("non-dict patch rejected", lambda: merge({}, []), ValidationError)

    def atomic():
        original = {"a": {"b": 1}, "keep": [1, 2]}
        merge(original, {"a": {"c": 2}})
        return original == {"a": {"b": 1}, "keep": [1, 2]}
    check("input state is never mutated", atomic)

    def no_alias():
        src = {"n": {"deep": 1}}
        out = merge({}, src)
        out["n"]["deep"] = 99
        return src["n"]["deep"] == 1
    check("patch values are deep-copied, not aliased into the new state", no_alias)

    def atomic_on_failure():
        state = {"ok": {"a": 1}, "bad": {"b": 2}}
        snapshot = json.dumps(state, sort_keys=True)
        try:
            merge(state, {"ok": {"a": 2}, "bad": "coerce"})
        except ValidationError:
            pass
        return json.dumps(state, sort_keys=True) == snapshot
    check("a patch that fails half-way leaves the state untouched", atomic_on_failure)

    # Idempotence in the patch. LangGraph re-executes a whole node from the start
    # after an interrupt or retry, so a reducer that double-applies would corrupt
    # state on resume. references/integrations.md claims this is safe; prove it.
    print("idempotence (re-execution safety):")
    cases = [
        ({"i": {"s1": "A", "s2": "B"}}, {"i": {"s2": None}}),
        ({"i": {"s1": "A"}}, {"i": {"s2": "B"}}),
        ({"a": 1}, {"a": 2}),
        ({"a": {"b": {"c": 1}}}, {"a": {"b": {"c": 9, "d": 0}}}),
        ({"x": ["a"]}, {"x": ["b"]}),
        ({"a": 1, "b": 2}, {"a": None, "b": 0}),
    ]
    for n, (st, pt) in enumerate(cases):
        check("merge is idempotent in the patch (case %d)" % n,
              lambda s=st, p=pt: merge(merge(s, p), p) == merge(s, p))

    # The LangGraph reducer contract: reducer(left=accumulated, right=update).
    print("LangGraph reducer contract:")

    def sigma_reducer(left, right):
        return merge(left or {}, right or {})

    check("reducer signature works with left/right keywords",
          lambda: sigma_reducer(left={"a": 1}, right={"b": 2}) == {"a": 1, "b": 2})
    check("reducer tolerates an empty initial channel",
          lambda: sigma_reducer(left=None, right={"a": 1}) == {"a": 1})
    check("an empty update does NOT clear the channel (LangGraph's documented "
          "gotcha is our gotcha #1)",
          lambda: sigma_reducer(left={"i": {"s": "A"}}, right={"i": {}})
          == {"i": {"s": "A"}})
    check("only null clears, through the reducer too",
          lambda: sigma_reducer(left={"i": {"s": "A"}}, right={"i": {"s": None}})
          == {"i": {}})
    check("sequential reduction equals one combined merge",
          lambda: sigma_reducer(sigma_reducer({"a": 1}, {"b": 2}), {"c": 3})
          == merge({"a": 1}, {"b": 2, "c": 3}))


# --- 2. envelope + schema ---------------------------------------------------


def section_contract():
    print("envelope contract:")
    check("parses reasoning + one fenced block",
          lambda: parse_response(
              'I should ship it.\n```json\n{"state_patch":{"i":{"s":null}},'
              '"action":"Ship item_12 s"}\n```\ndone')
          == ({"i": {"s": None}}, "Ship item_12 s"))
    check("untagged fence is accepted",
          lambda: parse_response('```\n{"state_patch":{},"action":"Wait"}\n```')
          == ({}, "Wait"))
    check("empty patch is legal (observe-only step)",
          lambda: parse_response('```json\n{"state_patch":{},"action":"Wait"}\n```')
          == ({}, "Wait"))

    rejects("third key rejected",
            lambda: parse_response(
                '```json\n{"state_patch":{},"action":"a","confidence":0.9}\n```'),
            ContractError)
    rejects("missing action rejected",
            lambda: parse_response('```json\n{"state_patch":{}}\n```'), ContractError)
    rejects("missing state_patch rejected",
            lambda: parse_response('```json\n{"action":"a"}\n```'), ContractError)
    rejects("trailing comma rejected",
            lambda: parse_response('```json\n{"state_patch":{},"action":"a",}\n```'),
            ContractError)
    rejects("unquoted key rejected",
            lambda: parse_response('```json\n{state_patch:{},"action":"a"}\n```'),
            ContractError)
    rejects("no fenced block rejected",
            lambda: parse_response('state_patch: {} action: go'), ContractError)
    rejects("two candidate blocks rejected as ambiguous",
            lambda: parse_response(
                '```json\n{"state_patch":{},"action":"a"}\n```\n'
                '```json\n{"state_patch":{},"action":"b"}\n```'),
            ContractError)
    rejects("state_patch must be an object",
            lambda: parse_response('```json\n{"state_patch":[],"action":"a"}\n```'),
            ContractError)
    rejects("action must be a string",
            lambda: parse_response('```json\n{"state_patch":{},"action":["a"]}\n```'),
            ContractError)
    check("a non-JSON fenced block does not shadow the real one",
          lambda: parse_response(
              '```bash\nls -la\n```\n```json\n{"state_patch":{},"action":"ls"}\n```')
          == ({}, "ls"))

    print("schema validation:")
    schema = {
        "inventory": {"*": "str?"},
        "counts": {"*": "int"},
        "ci": {"*": ["pass", "fail", "pending"]},
        "working_dir": "str",
        "retries": "int",
        "note": "str?",
        "tags": "list",
        "nested": {"depth": "int"},
    }
    check("open-map entry write accepted",
          lambda: validate_patch({"inventory": {"shelf_1": "item_A"}}, schema) is None)
    check("open-map entry deletion accepted",
          lambda: validate_patch({"inventory": {"shelf_1": None}}, schema) is None)
    check("nullable leaf deletion accepted",
          lambda: validate_patch({"note": None}, schema) is None)
    check("enum member accepted",
          lambda: validate_patch({"ci": {"pr_3": "pass"}}, schema) is None)
    check("closed sub-object key accepted",
          lambda: validate_patch({"nested": {"depth": 3}}, schema) is None)
    check("list leaf accepted",
          lambda: validate_patch({"tags": ["a"]}, schema) is None)

    rejects("unknown top-level key rejected",
            lambda: validate_patch({"chat_history": []}, schema), ValidationError)
    rejects("unknown key in a closed sub-object rejected",
            lambda: validate_patch({"nested": {"other": 1}}, schema), ValidationError)
    rejects("non-nullable leaf cannot be deleted",
            lambda: validate_patch({"working_dir": None}, schema), ValidationError)
    rejects("str -> list coercion rejected",
            lambda: validate_patch({"working_dir": ["/tmp"]}, schema), ValidationError)
    rejects("str -> int coercion rejected",
            lambda: validate_patch({"working_dir": 7}, schema), ValidationError)
    rejects("bool is not an int",
            lambda: validate_patch({"retries": True}, schema), ValidationError)
    rejects("enum violation rejected",
            lambda: validate_patch({"ci": {"pr_3": "flaky"}}, schema), ValidationError)
    rejects("open-map value type is still enforced",
            lambda: validate_patch({"counts": {"a": "many"}}, schema), ValidationError)
    rejects("cannot descend into a leaf",
            lambda: validate_patch({"working_dir": {"a": 1}}, schema), ValidationError)
    rejects("runtime-owned field cannot be written",
            lambda: validate_patch({"working_dir": "/tmp"}, schema,
                                   runtime_owned=("working_dir",)), ValidationError)
    rejects("runtime-owned nested path cannot be written",
            lambda: validate_patch({"nested": {"depth": 1}}, schema,
                                   runtime_owned=("nested.depth",)), ValidationError)
    check("runtime-owned sibling is still writable",
          lambda: validate_patch({"retries": 1}, schema,
                                 runtime_owned=("working_dir",)) is None)

    print("boundedness lint:")
    check("list field is flagged",
          lambda: len(lint_schema({"tried": "list"})) == 1)
    check("untyped field is flagged",
          lambda: len(lint_schema({"blob": "any"})) == 1)
    check("keyed map is not flagged",
          lambda: lint_schema({"tried": {"*": "str"}}) == [])
    check("nested list is flagged with a dotted path",
          lambda: "a.b" in lint_schema({"a": {"b": "list"}})[0])
    check("paper's verbatim CTF schema trips the lint (3 list fields)",
          lambda: len(lint_schema({
              "discovered_flags": "list", "tested_hypotheses": "list",
              "active_files": "list", "working_dir": "str", "cmd_summary": "str",
          })) == 3)

    print("serialization:")
    check("compact output has no insignificant whitespace",
          lambda: compact({"a": {"b": 1}, "c": [1, 2]}) == '{"a":{"b":1},"c":[1,2]}')
    check("compact output is a single line",
          lambda: "\n" not in compact({"a": {"b": {"c": [1, 2, 3]}}}))
    check("non-ascii is preserved, not escaped",
          lambda: compact({"k": "\u00e9"}) == '{"k":"\u00e9"}')


# --- 3. the step loop -------------------------------------------------------


def section_runtime():
    print("step loop:")
    schema = {"inventory": {"*": "str?"}}
    good = '```json\n{"state_patch":{"inventory":{"s42":null}},"action":"Ship i s42"}\n```'

    check("apply_step validates, merges, and returns the action",
          lambda: apply_step({"inventory": {"s42": "i", "s41": "j"}}, good, schema)
          == ({"inventory": {"s41": "j"}}, "Ship i s42"))
    check("apply_step discards reasoning (returns only state and action)",
          lambda: len(apply_step({"inventory": {}}, "think hard\n" + good, schema)) == 2)
    rejects("apply_step rejects an off-schema patch",
            lambda: apply_step(
                {"inventory": {}},
                '```json\n{"state_patch":{"notes":"x"},"action":"a"}\n```', schema),
            ValidationError)

    def state_untouched_on_reject():
        state = {"inventory": {"s1": "A"}}
        try:
            apply_step(state, '```json\n{"state_patch":{"bad":1},"action":"a"}\n```',
                       schema)
        except ValidationError:
            pass
        return state == {"inventory": {"s1": "A"}}
    check("a rejected step leaves the caller's state untouched", state_untouched_on_reject)

    check("action space accepts a declared verb",
          lambda: apply_step({"inventory": {}},
                             '```json\n{"state_patch":{},"action":"Ship i s1"}\n```',
                             schema, action_space=["Ship", "Wait"])[1] == "Ship i s1")
    rejects("action outside the declared space is rejected",
            lambda: apply_step({"inventory": {}},
                               '```json\n{"state_patch":{},"action":"rm -rf /"}\n```',
                               schema, action_space=["Ship", "Wait"]), ValidationError)

    print("rollback-retry:")

    def retry_then_succeed():
        calls = []

        def generate(prompt):
            calls.append(prompt)
            if len(calls) == 1:
                return "```json\n{\"state_patch\":{},\"action\":\"a\",\"extra\":1}\n```"
            return good
        rt = Runtime(schema, {"inventory": {"s42": "i"}}, max_retries=3)
        action = rt.step(generate, "P", "O")
        return (action == "Ship i s42"
                and rt.state == {"inventory": {}}
                and len(calls) == 2
                and "rejected" in calls[1])
    check("malformed response retries with the error as feedback, then commits",
          retry_then_succeed)

    def retry_exhausted():
        rt = Runtime(schema, {"inventory": {"s1": "A"}}, max_retries=2)
        try:
            rt.step(lambda p: "no json at all", "P", "O")
        except SkillStateError:
            return rt.state == {"inventory": {"s1": "A"}}
        return False
    check("exhausted retry budget raises and preserves the state", retry_exhausted)

    def budget_guard():
        rt = Runtime({"blob": {"*": "str"}}, {"blob": {}}, sigma_budget=40)
        try:
            rt.step(lambda p: '```json\n{"state_patch":{"blob":{"k":"%s"}},'
                              '"action":"a"}\n```' % ("x" * 200), "P", "O")
        except SkillStateError as exc:
            return "budget" in str(exc) and rt.state == {"blob": {}}
        return False
    check("exceeding the state budget fails loudly and does not commit", budget_guard)

    print("prompt construction:")
    rt = Runtime(schema, {"inventory": {"s1": "A"}})
    prompt = rt.prompt("be a warehouse agent", "Customer ordered item_12.")
    check("prompt contains the instructions", lambda: "be a warehouse agent" in prompt)
    check("prompt contains the compact state",
          lambda: '{"inventory":{"s1":"A"}}' in prompt)
    check("prompt contains the latest observation",
          lambda: "Customer ordered item_12." in prompt)
    check("prompt states that reasoning will be discarded",
          lambda: "discarded" in prompt)
    check("prompt names both required keys",
          lambda: "state_patch" in prompt and "action" in prompt)
    check("prompt teaches null-deletion",
          lambda: "null" in prompt)
    check("prompt has no history section",
          lambda: not re.search(r"(?i)\b(history|previous|transcript|earlier turns)\b",
                                prompt))

    def prompt_is_flat():
        a = Runtime(schema, {"inventory": {"s1": "A"}})
        sizes = []
        for i in range(50):
            sizes.append(len(a.prompt("P" * 100, "O" * 50)))
            a.state = merge(a.state, {"inventory": {"s%d" % i: "x", "s%d" % (i - 1): None}})
        return max(sizes) - min(sizes) < 40
    check("prompt size stays flat across 50 steps with bounded state", prompt_is_flat)

    print("Mode C host adapters:")
    from skillstate import (  # noqa: WPS433 — already on path via import *
        build_host_argv, extract_host_response, run_loop as _run_loop)

    qargv = build_host_argv("qwen", "m", "SYS", "USR")
    check("qwen argv uses bare+safe-mode+system-prompt",
          lambda: qargv[:1] == ["qwen"] and "--bare" in qargv and "--safe-mode" in qargv
          and "--system-prompt" in qargv and "SYS" in qargv and "USR" in qargv)
    oargv = build_host_argv("opencode", "openrouter/x", "SYS", "USR")
    check("opencode argv is fresh session (no --continue) and folds system",
          lambda: oargv[0] == "opencode" and "--continue" not in oargv
          and "--pure" in oargv
          and any("SYS" in a and "USR" in a for a in oargv))
    carav = build_host_argv("claude", "m", "S", "U")
    check("claude argv has system-prompt flag",
          lambda: "--system-prompt" in carav and carav[0] == "claude")
    check("api argv is a sentinel, not a shell binary",
          lambda: build_host_argv("api", "x", "", "y")[0] == "api")

    def _unknown_host():
        try:
            build_host_argv("nope", "", "", "")
            return False
        except ValueError:
            return True
    check("unknown host raises ValueError", _unknown_host)

    text, usage = extract_host_response(
        "api",
        json.dumps({
            "choices": [{"message": {"content": "```json\n{\"state_patch\":{},\"action\":\"Wait\"}\n```"}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }),
    )
    check("api extract returns content and usage",
          lambda: "state_patch" in text and usage["total"] == 15)

    def flat_mode_c_stub():
        responses = []
        for i in range(12):
            act = "DONE" if i == 11 else "Wait"
            responses.append(
                'r\n```json\n{"state_patch":{"n":"%d"},"action":"%s"}\n```' % (i, act)
            )
        out = _run_loop(
            host="stub",
            instructions="P" * 80,
            schema={"n": "str?"},
            state={"n": "0"},
            observe_cmd="true",
            action_cmd=lambda a: "obs-after-%s" % a,
            horizon=12,
            stub_responses=responses,
            initial_observation="start",
        )
        sizes = out["prompt_sizes"]
        # Allow mild growth from state field width only
        return (
            len(sizes) == 12
            and out["prompt_growth"] < 1.15
            and out["actions"][-1] == "DONE"
        )
    check("Mode C stub loop keeps prompt growth under 1.15x at T=12", flat_mode_c_stub)

    print("local MCP (Mode C):")
    mcp = McpServer()
    st = dispatch_mcp(mcp, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    check("MCP initialize names skillstate",
          lambda: st["result"]["serverInfo"]["name"] == "skillstate")
    check("notifications/initialized has no reply",
          lambda: dispatch_mcp(mcp, {"jsonrpc": "2.0", "method": "notifications/initialized"}) is None)
    listed = dispatch_mcp(mcp, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    names = sorted(t["name"] for t in listed["result"]["tools"])
    check("MCP tools are status/init/run_loop/step/sigma only",
          lambda: names == ["init", "run_loop", "sigma", "status", "step"])

    def _call(server, tool, args, mid=10):
        resp = dispatch_mcp(server, {
            "jsonrpc": "2.0", "id": mid, "method": "tools/call",
            "params": {"name": tool, "arguments": args},
        })
        body = resp["result"]
        payload = json.loads(body["content"][0]["text"]) if not body.get("isError") else None
        return body, payload

    status_body, status_payload = _call(mcp, "status", {})
    check("status is compact and ok",
          lambda: status_payload["ok"] is True and "sessions" in status_payload)

    def _opencode_host_rejected():
        body, _ = _call(mcp, "init", {
            "instructions": "P", "schema": {"n": "str?"}, "host": "opencode",
        })
        return body.get("isError") is True
    check("init rejects opencode as inner generate host", _opencode_host_rejected)

    def _no_merge_tool():
        body, _ = _call(mcp, "merge", {"state": {}})
        return body.get("isError") is True
    check("there is no merge/get_state tool", _no_merge_tool)

    responses = []
    for i in range(6):
        act = "DONE" if i == 5 else "Wait"
        responses.append(
            'r\n```json\n{"state_patch":{"n":"%d"},"action":"%s"}\n```' % (i, act)
        )
    tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
    tmp.close()
    try:
        sid = mcp.init(
            instructions="P" * 40,
            schema={"n": "str?"},
            state={"n": "0"},
            host="stub",
            stub_responses=responses,
            state_path=tmp.name,
        )["session_id"]
        init_body, init_payload = _call(McpServer(), "init", {
            "instructions": "x", "schema": {"a": "str?"}, "host": "stub",
        })
        check("init tool payload keys are only session_id",
              lambda: list(init_payload.keys()) == ["session_id"])

        loop_out = mcp.run_loop(
            sid, env_observe="true", env_action="true",
            horizon=6, initial_observation="start",
        )
        allowed = {"steps", "last_action", "done", "tokens", "prompt_growth",
                   "avg_prompt_chars", "sigma_bytes"}
        check("run_loop payload has no state, no R, no action list",
              lambda: set(loop_out.keys()) == allowed
              and "actions" not in loop_out and "state" not in loop_out)
        check("run_loop stub stays flat and finishes",
              lambda: loop_out["done"] and loop_out["last_action"] == "DONE"
              and loop_out["prompt_growth"] < 1.15)
        on_disk = open(tmp.name, encoding="utf-8").read().strip()
        check("run_loop persists compact Sigma to state_path",
              lambda: on_disk == compact(mcp.sessions[sid]["runtime"].state)
              and "\n" not in on_disk)
    finally:
        os.unlink(tmp.name)

    step_mcp = McpServer()
    step_sid = step_mcp.init(
        instructions="P",
        schema={"n": "str?"},
        state={"n": "0"},
        host="stub",
        stub_responses=[
            'r\n```json\n{"state_patch":{"n":"1"},"action":"Wait"}\n```',
        ],
    )["session_id"]
    step_out = step_mcp.step(step_sid, "obs")
    check("step returns only action",
          lambda: list(step_out.keys()) == ["action"] and step_out["action"] == "Wait")
    sig = step_mcp.sigma(step_sid)
    check("sigma is opt-in compact JSON",
          lambda: json.loads(sig["state"]) == {"n": "1"})

    import io
    canned = (
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        + "\n"
        + json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        + "\n"
    )
    buf_in, buf_out = io.StringIO(canned), io.StringIO()
    check("serve_mcp NDJSON round-trip writes two replies",
          lambda: serve_mcp(buf_in, buf_out) == 0
          and buf_out.getvalue().count("\n") == 2)

    def _step_rpc_tiny():
        s = McpServer()
        sid = s.init(
            instructions="P", schema={"n": "str?"}, state={"n": "0"},
            host="stub",
            stub_responses=['r\n```json\n{"state_patch":{},"action":"Wait"}\n```'],
        )["session_id"]
        body, payload = _call(s, "step", {"session_id": sid, "observation": "o"})
        return (not body.get("isError")) and list(payload.keys()) == ["action"]
    check("step tool result is {action} only", _step_rpc_tiny)


# --- 4. example trajectories ------------------------------------------------
#
# Convention enforced here: in examples/*.md the json blocks are, in order,
#   [schema, sigma_0, patch_1, sigma_1, patch_2, sigma_2, ...]
# Every patch is validated against the schema and merged; the result must
# equal the documented next state. This is what keeps the prose honest.

JSON_BLOCK = re.compile(r"```json[ \t]*\r?\n(.*?)```", re.DOTALL)


def section_trajectories():
    print("example trajectories:")
    ex_dir = os.path.join(ROOT, "examples")
    files = sorted(f for f in os.listdir(ex_dir) if f.endswith(".md"))
    check("examples/ is not empty", lambda: len(files) > 0)

    for name in files:
        text = open(os.path.join(ex_dir, name), encoding="utf-8").read()
        blocks = JSON_BLOCK.findall(text)

        check("%s: has a schema, an initial state and at least one turn" % name,
              lambda b=blocks: len(b) >= 4)
        check("%s: turn blocks come in (patch, state) pairs" % name,
              lambda b=blocks: len(b) % 2 == 0)
        if len(blocks) < 4 or len(blocks) % 2:
            continue

        parsed = []
        bad = None
        for i, raw in enumerate(blocks):
            try:
                parsed.append(json.loads(raw))
            except json.JSONDecodeError as exc:
                bad = "block %d: %s" % (i, exc)
                break
        check("%s: every json block parses" % name, lambda b=bad: b is None)
        if bad:
            continue

        schema, sigma = parsed[0], parsed[1]
        check("%s: schema is bounded with respect to the horizon" % name,
              lambda s=schema: lint_schema(s) == [])
        check("%s: initial state conforms to the schema" % name,
              lambda s=schema, g=sigma: validate_patch(g, s) is None)

        for i in range(2, len(parsed), 2):
            turn = i // 2
            patch, expected = parsed[i], parsed[i + 1]
            check("%s turn %d: patch conforms to the schema" % (name, turn),
                  lambda p=patch, s=schema: validate_patch(p, s) is None)
            check("%s turn %d: merge(sigma, patch) equals the documented state"
                  % (name, turn),
                  lambda g=sigma, p=patch, e=expected: merge(g, p) == e)
            check("%s turn %d: documented state is serialized compactly" % (name, turn),
                  lambda raw=blocks[i + 1], e=expected: raw.strip() == compact(e))
            sigma = merge(sigma, patch)

        check("%s: final state still conforms to the schema" % name,
              lambda s=schema, g=sigma: validate_patch(g, s) is None)


DOCS_SECTION_CHECKS = 15   # kept honest by the self-check in main()


# --- 5. documentation integrity ---------------------------------------------

PATH_REF = re.compile(r"(?<![\w/.])((?:rules|references|examples|scripts|tests)"
                      r"/[A-Za-z0-9_\-./]+\.(?:md|py|json))")
FENCE_LINE = re.compile(r"^(\s*)(`{3,})([A-Za-z0-9_+-]*)\s*$")
DELIM_ROW = re.compile(r"^\|[\s:|-]+\|$")


def row_cells(line):
    """Split a Markdown table row on real column separators only.

    A pipe does not separate columns when it is backslash-escaped or inside an
    inline-code span. Getting this right is the difference between finding a
    broken table and crying wolf over correctly escaped maths like `\\|Sigma\\|`.
    """
    cells, buf, in_code, i = [], [], False, 0
    while i < len(line):
        ch = line[i]
        if ch == "\\" and i + 1 < len(line):
            buf.append(line[i:i + 2])
            i += 2
            continue
        if ch == "`":
            in_code = not in_code
        if ch == "|" and not in_code:
            cells.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
        i += 1
    cells.append("".join(buf))
    return cells[1:-1] if len(cells) >= 2 else cells


def markdown_files():
    out = []
    skip_dirs = {".git", "__pycache__", "node_modules", ".opencode", "runs"}
    for base, dirs, files in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in skip_dirs]
        for f in sorted(files):
            if f.endswith(".md"):
                out.append(os.path.join(base, f))
    return sorted(out)


def section_docs():
    print("documentation integrity:")
    docs = markdown_files()
    on_disk = set()
    skip_dirs = {".git", "__pycache__", "node_modules", ".opencode", "runs"}
    for base, dirs, files in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in skip_dirs]
        for f in files:
            on_disk.add(os.path.relpath(os.path.join(base, f), ROOT))

    broken, nested, pipes, unclosed = [], [], [], []
    for path in docs + [os.path.join(ROOT, "scripts", "skillstate.py")]:
        rel = os.path.relpath(path, ROOT)
        text = open(path, encoding="utf-8").read()

        for ref in set(PATH_REF.findall(text)):
            if ref not in on_disk:
                broken.append("%s -> %s" % (rel, ref))

        if not rel.endswith(".md"):
            continue

        stack, expected_cols = [], None
        for n, line in enumerate(text.split("\n"), 1):
            m = FENCE_LINE.match(line)
            if m:
                ticks, lang = m.group(2), m.group(3)
                # CommonMark: a block opened with N backticks is closed by a
                # line of >= N backticks with no info string. An inner fence
                # with FEWER backticks nests legally; one with >= truncates
                # the outer block, which is the bug worth catching.
                if stack and not lang and len(ticks) >= len(stack[-1][0]):
                    stack.pop()
                else:
                    if stack and len(ticks) >= len(stack[-1][0]):
                        nested.append("%s:%d" % (rel, n))
                    stack.append((ticks, lang, n))
                continue
            if stack:
                continue
            s = line.strip()
            if not (s.startswith("|") and s.endswith("|") and len(s) > 1):
                expected_cols = None
                continue
            if DELIM_ROW.match(s):
                expected_cols = len(row_cells(s))
                continue
            if expected_cols is not None and len(row_cells(s)) != expected_cols:
                pipes.append("%s:%d (%d cells, table has %d)"
                             % (rel, n, len(row_cells(s)), expected_cols))
        if stack:
            unclosed.append("%s:%s" % (rel, [f[2] for f in stack]))

    empty("every relative file reference resolves", broken, "broken")
    empty("no nested code fences (they truncate the outer block)", nested, "nested at")
    empty("no unclosed code fences", unclosed, "unclosed")
    empty("no unescaped pipes inside table cells", pipes, "bad rows")

    skill = open(os.path.join(ROOT, "SKILL.md"), encoding="utf-8").read()
    fm = skill.split("---")[1] if skill.startswith("---") else ""
    check("SKILL.md has YAML frontmatter", lambda: skill.startswith("---"))
    check("SKILL.md frontmatter declares name", lambda: "\nname:" in fm)
    check("SKILL.md frontmatter declares description", lambda: "\ndescription:" in fm)

    desc = re.search(r"description:\s*>?\s*\n?((?:.|\n)*?)\n[a-z_]+:", fm)
    body = " ".join(x.strip() for x in desc.group(1).split("\n")).strip() if desc else ""
    check("description is non-empty and under the 1024-char convention",
          lambda: 0 < len(body) <= 1024)

    linked = set(PATH_REF.findall(skill))
    orphans = sorted(
        p for p in on_disk
        if (p.startswith(("rules/", "references/", "examples/")) and p.endswith(".md")
            and p not in linked))
    empty("every rules/, references/ and examples/ file is linked from SKILL.md",
          orphans, "orphaned")

    check("AGENTS.md stays small enough to be safe as an auto-loaded file",
          lambda: len(open(os.path.join(ROOT, "AGENTS.md"),
                           encoding="utf-8").read().split("\n")) <= 80)

    numbers = re.compile(r"16\.2|65,408|1,062,387|54\.2|122,384|6,175,509|0\.97")
    offenders = []
    for path in docs:
        rel = os.path.relpath(path, ROOT)
        if rel in ("references/evidence.md", "README.md", "README_RU.md", "SKILL.md"):
            continue
        text = open(path, encoding="utf-8").read()
        if numbers.search(text) and "references/evidence.md" not in text:
            offenders.append(rel)
    empty("any file quoting a headline figure also cites references/evidence.md",
          offenders, "uncited")

    # Every python block in the docs must at least parse. An integration recipe
    # with a syntax error is worse than no recipe: it looks authoritative.
    # Fence width matters: a block whose code contains a ``` sequence must be
    # opened with four or more backticks, or every renderer truncates it.
    print("documentation code blocks:")
    py_block = re.compile(r"^(`{3,})python[ \t]*\r?\n(.*?)^\1[ \t]*$",
                          re.DOTALL | re.MULTILINE)
    bad_py, n_py = [], 0
    for path in docs:
        rel = os.path.relpath(path, ROOT)
        for i, (ticks, src) in enumerate(
                py_block.findall(open(path, encoding="utf-8").read())):
            n_py += 1
            if "```" in src and len(ticks) < 4:
                bad_py.append("%s#python[%d]: contains ``` inside a %d-backtick "
                              "fence; use four" % (rel, i, len(ticks)))
                continue
            try:
                compile(src, "%s#python[%d]" % (rel, i), "exec")
            except SyntaxError as exc:
                bad_py.append("%s#python[%d]: line %s: %s"
                              % (rel, i, exc.lineno, exc.msg))
    empty("every python block in the docs compiles", bad_py, "syntax errors")
    check("the docs actually contain python examples to check",
          lambda: n_py >= 4)

    # Frontmatter must satisfy the strictest host we document. Qwen Code rejects
    # a skill name containing whitespace, slashes or brackets at parse time.
    name = re.search(r"^name:\s*(\S+)\s*$", fm, re.M)
    check("skill name is present and host-portable",
          lambda: bool(name) and re.match(r"^[\w:.-]+$", name.group(1)) is not None)

    # Documented check counts drift the moment anyone adds a test. Pin them to
    # reality: this file is the only place either number may be computed.
    stale = []
    advertised = re.compile(r"(?:PASSED: all|full suite,|suite,)\s*(\d+)\s*(?:checks|\S*)"
                            r"|(\d+)\s*(?:checks|проверки|проверок)")
    for path in docs:
        rel = os.path.relpath(path, ROOT)
        for line in open(path, encoding="utf-8").read().split("\n"):
            if "self-test" in line:
                m = re.search(r"(\d+)\s*checks", line)
                if m and int(m.group(1)) != SELF_TEST_CHECKS:
                    stale.append("%s: self-test says %s, actual %d"
                                 % (rel, m.group(1), SELF_TEST_CHECKS))
            elif re.search(r"run_tests\.py", line) or "полный набор" in line \
                    or "stdlib only" in line or "стандартная библиотека" in line:
                m = re.search(r"(\d+)\s*(?:checks|проверки|проверок)", line)
                if m and int(m.group(1)) != COUNT_TOTAL[0]:
                    stale.append("%s: suite says %s, actual %d"
                                 % (rel, m.group(1), COUNT_TOTAL[0]))
    empty("documented test counts match the real suite", stale, "stale counts")


# --- main -------------------------------------------------------------------


def main():
    # The docs section asserts the suite's own advertised size, so the total has
    # to be known before it runs. Count everything else first, then add the
    # checks this section itself will contribute.
    for section in (section_merge, section_contract, section_runtime,
                    section_trajectories):
        section()
    COUNT_TOTAL[0] = COUNT + DOCS_SECTION_CHECKS
    section_docs()
    if COUNT != COUNT_TOTAL[0]:
        print("\nharness error: DOCS_SECTION_CHECKS should be %d, not %d"
              % (COUNT - (COUNT_TOTAL[0] - DOCS_SECTION_CHECKS), DOCS_SECTION_CHECKS))
        return 1
    print("\n" + "=" * 60)
    if FAILURES:
        print("FAILED: %d of %d checks" % (len(FAILURES), COUNT))
        for f in FAILURES:
            print("  - %s" % f)
        return 1
    print("PASSED: all %d checks" % COUNT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
