#!/usr/bin/env python3
"""
Local MCP server — ``McpServer``, ``dispatch_mcp``, ``serve_mcp``.

Mode C for hosts that cannot assemble the prompt themselves (opencode,
Cursor, Claude Code). The MCP server runs Algorithm 1 as a local
**stdio JSON-RPC process**. The parent agent calls tools like
``run_loop``, ``step``, ``sigma`` — but never sees ``Σ`` or reasoning
traces in its own transcript.

.. rubric:: Why Local MCP (not a Remote Service)

- **No network latency per step.** The loop is tight — hundreds of
  API calls — and a round-trip per step would dominate execution time.
- **API keys stay in the host process.** No secret sharing with a
  remote service.
- **State files live on the host filesystem.** Mode B survival across
  ``/compact`` — the state persists on disk even if the host context
  is destroyed.

.. rubric:: Inner Generate Hosts

The inner generate host **must** be ``api`` or ``stub`` — using
``opencode``, ``qwen``, or ``cursor`` as the inner host recreates the
Mode B problem (host transcript growth). The whole point of Mode C is
that the parent agent's transcript is O(1) in the horizon.

.. rubric:: References

- ``references/mcp.md`` — MCP server architecture and protocol.
- ``references/host-runtime-adaptation.md`` — Mode B vs Mode C.
"""

from __future__ import annotations

import json
import os
import sys
import uuid

from .errors import SkillStateError
from .hosts import exec_once
from .loop import run_loop
from .serialization import compact
from .step import Runtime


# =============================================================================
# CONSTANTS
# =============================================================================

# MCP protocol version — used in the ``initialize`` handshake.
# This is the 2024-11-05 revision of the Model Context Protocol spec.
MCP_PROTOCOL = "2024-11-05"

# Hosts allowed as the inner generate backend.
# Using the parent agent as the inner host would recreate Mode B
# (host transcript growth).
MCP_INNER_HOSTS = ("api", "stub")

# Version of this MCP server implementation.
_MCP_VERSION = "2.0.0"


# =============================================================================
# TOOL SCHEMAS
# =============================================================================
# Each tool definition follows the MCP tools/list response format:
#   name → {description, inputSchema}
#
# inputSchema follows JSON Schema draft-2020-12 (used by MCP clients
# for parameter validation).

_MCP_TOOL_SCHEMA = {
    "status": {
        "description": (
            "Check that the local SKILL.state MCP is running. Call this "
            "first. Does not read or return execution state."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    },
    "init": {
        "description": (
            "Start a session: schema + initial Sigma + immutable skill "
            "spec P. Returns only session_id. Do not call get_state; use "
            "sigma only for resume after compact. Inner generate host "
            "must be api or stub."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "instructions": {"type": "string"},
                "schema": {"type": "object"},
                "state": {"type": "object"},
                "model": {"type": "string"},
                "host": {"type": "string", "enum": ["api", "stub"]},
                "state_path": {"type": "string"},
                "workdir": {"type": "string"},
                "runtime_owned": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "sigma_budget": {"type": "integer"},
                "max_retries": {"type": "integer"},
            },
            "required": ["instructions", "schema"],
            "additionalProperties": False,
        },
    },
    "run_loop": {
        "description": (
            "Run Algorithm 1 inside this process. The host must NOT step "
            "the horizon in its own transcript. Returns steps/tokens/"
            "summary only — never Sigma, never reasoning, never the "
            "action list."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "session_id": {"type": "string"},
                "env_observe": {"type": "string"},
                "env_action": {"type": "string"},
                "horizon": {"type": "integer"},
                "initial_observation": {"type": "string"},
            },
            "required": ["session_id", "env_observe", "env_action"],
            "additionalProperties": False,
        },
    },
    "step": {
        "description": (
            "One Algorithm-1 step when the environment is the host's "
            "tools. Pass only the latest observation. Returns {action} "
            "only."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "session_id": {"type": "string"},
                "observation": {"type": "string"},
            },
            "required": ["session_id", "observation"],
            "additionalProperties": False,
        },
    },
    "sigma": {
        "description": (
            "Return compact Sigma for compact/resume. Do not call every "
            "turn — that copies state back into the host transcript."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "session_id": {"type": "string"},
            },
            "required": ["session_id"],
            "additionalProperties": False,
        },
    },
}


# =============================================================================
# MCP SERVER — session manager
# =============================================================================


class McpServer:
    """In-process Algorithm-1 session manager for MCP.

    Holds multiple named sessions (keyed by ``session_id``). Each
    session has its own ``Runtime``, instructions, host config, and
    optional ``state_path`` for on-disk persistence.

    The parent agent's transcript stays O(1) in the horizon because:

    - ``run_loop`` returns only ``{steps, tokens, summary}`` — no
      ``Σ``, no reasoning.
    - ``step`` returns only ``{action}`` — a single string.
    - ``sigma`` returns compact ``Σ`` only on explicit request (for
      compact/resume — should be used sparingly).

    **Anti-pattern warnings** (designed out by omission):

    - There is no ``get_state`` tool — that would copy ``Σ`` into the
      host transcript on every call.
    - There is no ``merge`` tool exposed to the host — the host does
      not write to ``Σ`` directly.
    """

    def __init__(self, generate=None):
        """Initialise the MCP server.

        Args:
            generate:
                Optional ``(prompt: str) -> str`` callable for
                ``step()``. If not provided, ``step()`` builds one
                from ``exec_once`` using the session's host and model
                configuration.
        """
        self.sessions = {}     # session_id → session config dict
        self.generate = generate

    # -------------------------------------------------------------------------
    # Session management tools
    # -------------------------------------------------------------------------

    def status(self):
        """Health check — confirms the server is running.

        Returns:
            ``dict`` with ``ok``, ``version``, ``protocol``,
            ``inner_hosts``, and ``sessions`` (count).
        """
        return {
            "ok": True,
            "version": _MCP_VERSION,
            "protocol": MCP_PROTOCOL,
            "inner_hosts": list(MCP_INNER_HOSTS),
            "sessions": len(self.sessions),
        }

    def init(
        self,
        instructions,
        schema,
        state=None,
        model="",
        host="api",
        state_path="",
        workdir="",
        runtime_owned=None,
        sigma_budget=None,
        max_retries=3,
        stub_responses=None,
    ):
        """Create a new session.

        This is the MCP equivalent of::

            skillstate.py loop --host api --instructions P --schema S.json

        It stores all configuration and initial state, then returns a
        ``session_id`` for subsequent ``run_loop`` / ``step`` / ``sigma``
        calls.

        Args:
            instructions:
                Immutable skill spec ``P`` (the system prompt).
            schema:
                State schema ``dict`` for validation.
            state:
                Initial ``Σ₀`` dict (defaults to ``{}``).
            model:
                Model identifier for the inner generate host
                (e.g. ``"openrouter/anthropic/..."``).
            host:
                Inner generate host — **must** be ``"api"`` or
                ``"stub"`` (not opencode/qwen/cursor — that recreates
                Mode B).
            state_path:
                File path for on-disk state persistence. On every
                state mutation, ``compact(Σ)`` is written here. This is
                how Mode B survival works: after ``/compact``, the
                state file survives on disk.
            workdir:
                Working directory for observe/act subprocesses.
            runtime_owned:
                ``list[str]`` of dotted paths the model cannot write.
            sigma_budget:
                Max characters for ``compact(state)``.
            max_retries:
                Per-step retry budget for malformed responses.
            stub_responses:
                ``list[str]`` of pre-canned responses for ``"stub"``
                inner host (testing / replay).

        Returns:
            ``{"session_id": str}`` — the 12-hex-char session ID.
        """
        host = (host or "api").lower().strip()
        if host not in MCP_INNER_HOSTS:
            raise SkillStateError(
                "MCP inner generate host must be api or stub, not %r. "
                "opencode/qwen/cursor as the inner host recreates Mode B."
                % host
            )

        if not isinstance(schema, dict):
            raise SkillStateError("schema must be a JSON object")

        # Generate a short unique session ID (first 12 hex chars of
        # a UUID4). Short enough to be easy to copy-paste but long
        # enough to avoid collisions in practice.
        sid = uuid.uuid4().hex[:12]

        # Build the Runtime — this owns the state and validation logic.
        rt = Runtime(
            schema=schema,
            state=state if isinstance(state, dict) else {},
            runtime_owned=tuple(runtime_owned or ()),
            max_retries=int(max_retries) if max_retries is not None else 3,
            sigma_budget=sigma_budget,
        )

        self.sessions[sid] = {
            "runtime": rt,
            "instructions": instructions,
            "host": host,
            "model": model or "",
            "state_path": state_path or "",
            "workdir": workdir or None,
            "stub_responses": (
                list(stub_responses) if stub_responses else []
            ),
            "stub_i": 0,    # consumption index into stub_responses
        }

        # Persist initial state to disk.
        # This ensures the state file exists even if the session
        # is created but no steps have run yet (important for
        # compact/resume scenarios).
        self._persist(sid)
        return {"session_id": sid}

    # -------------------------------------------------------------------------
    # Execution tools
    # -------------------------------------------------------------------------

    def run_loop(self, session_id, env_observe, env_action,
                 horizon=50, initial_observation=None):
        """Execute the full Algorithm-1 loop for a session.

        The parent agent calls this, waits for the result, and gets
        back **only summary data**. The T-step transcript — prompts,
        model responses, reasoning — never enters the parent's context.

        Args:
            session_id:
                Session ID from ``init()``.
            env_observe:
                Shell command to observe the environment.
            env_action:
                Shell command to execute actions (may contain
                ``{action}`` placeholder).
            horizon:
                Maximum steps (default ``50``).
            initial_observation:
                First observation (skips first ``env_observe`` call).

        Returns:
            ``dict`` with keys:
            ``steps``, ``last_action``, ``done`` (bool), ``tokens``
            (``{input, output, total, calls}``), ``prompt_growth``
            (float), ``avg_prompt_chars`` (int), ``sigma_bytes`` (int).
        """
        sess = self._session(session_id)
        rt = sess["runtime"]
        stubs = sess["stub_responses"] or None

        out = run_loop(
            host=sess["host"],
            instructions=sess["instructions"],
            schema=rt.schema,
            state=rt.state,
            observe_cmd=env_observe,
            action_cmd=env_action,
            model=sess["model"] or None,
            horizon=int(horizon),
            max_retries=rt.max_retries,
            runtime_owned=rt.runtime_owned,
            sigma_budget=rt.sigma_budget,
            stub_responses=stubs,
            initial_observation=initial_observation,
            workdir=sess["workdir"],
        )

        # Sync the runtime state with the loop result.
        # ``run_loop`` modifies ``rt.state`` in place, so this is
        # already current — but being explicit is clearer.
        rt.state = out["state"]

        # Advance the stub response index if we consumed any.
        if stubs is not None:
            sess["stub_i"] = len(stubs)

        self._persist(session_id)

        last = out["actions"][-1] if out["actions"] else ""
        return {
            "steps": out["steps"],
            "last_action": last,
            "done": last == "DONE",
            "tokens": {
                "input": out["totals"]["input"],
                "output": out["totals"]["output"],
                "total": out["totals"]["total"],
                "calls": out["totals"]["calls"],
            },
            "prompt_growth": round(float(out["prompt_growth"]), 4),
            "avg_prompt_chars": int(round(out["avg_prompt_chars"])),
            "sigma_bytes": len(compact(rt.state).encode("utf-8")),
        }

    def step(self, session_id, observation):
        """Execute **one** step when the environment is the host's tools.

        Use this when the parent agent **owns** the environment (its
        tools are the actions). The MCP server does not call
        observe/act itself — it only generates the action and updates
        ``Σ``. The parent calls ``step``, gets back ``{action}``,
        executes the action with its own tools, and passes the result
        as the next ``observation``.

        Args:
            session_id:
                Session ID from ``init()``.
            observation:
                The latest observation from the host's environment
                (result of the last tool call or action).

        Returns:
            ``{"action": str}`` — the validated action for the host
            to execute.
        """
        sess = self._session(session_id)
        rt = sess["runtime"]

        # Use an externally provided ``generate`` if available,
        # otherwise build one from ``exec_once`` with the session's
        # host and model config.
        generate = self.generate or self._make_generate(sess)

        action = rt.step(generate, sess["instructions"], observation)
        self._persist(session_id)
        return {"action": action}

    def sigma(self, session_id):
        """Return compact Sigma.

        **Use sparingly** — only for compact/resume. Do **not** call
        every turn; that copies the entire state back into the host
        transcript and defeats Mode C's isolation guarantee.

        Returns:
            ``{"state": str}`` — compact JSON of the current state.
        """
        sess = self._session(session_id)
        return {"state": compact(sess["runtime"].state)}

    # -------------------------------------------------------------------------
    # Tool dispatch
    # -------------------------------------------------------------------------

    def call_tool(self, name, arguments=None):
        """Route a tool call to the appropriate handler.

        This is the main dispatch for MCP ``tools/call`` requests.
        Arguments may be a ``dict`` or a JSON string (both are accepted
        for flexibility with different MCP client implementations).

        Args:
            name:
                Tool name: ``"status"``, ``"init"``, ``"run_loop"``,
                ``"step"``, or ``"sigma"``.
            arguments:
                ``dict`` or JSON ``str`` — tool parameters.

        Returns:
            The handler's return value (``dict``).

        Raises:
            SkillStateError:
                For unknown tools. There is intentionally **no**
                ``merge`` or ``get_state`` tool — those would dump
                ``Σ`` into the host transcript and defeat Mode C.
        """
        arguments = arguments or {}
        if isinstance(arguments, str):
            arguments = json.loads(arguments) if arguments.strip() else {}

        if name == "status":
            return self.status()
        if name == "init":
            return self.init(**arguments)
        if name == "run_loop":
            return self.run_loop(**arguments)
        if name == "step":
            return self.step(**arguments)
        if name == "sigma":
            return self.sigma(**arguments)

        raise SkillStateError(
            "unknown tool %r; use status, init, run_loop, step, sigma. "
            "There is no merge/get_state tool — those dump Sigma into "
            "the host." % name
        )

    # -------------------------------------------------------------------------
    # Internal helpers
    # -------------------------------------------------------------------------

    def _session(self, session_id):
        """Look up a session by ID.

        Args:
            session_id: The session ID string.

        Returns:
            The session config ``dict``.

        Raises:
            SkillStateError: If the ``session_id`` is not found.
        """
        sess = self.sessions.get(session_id)
        if sess is None:
            raise SkillStateError("unknown session_id")
        return sess

    def _persist(self, session_id):
        """Write the current state to disk if ``state_path`` is configured.

        This is how Mode B survival works: even if the host context is
        destroyed (``/compact``, session restart, crash), the state
        file survives on disk. On resume, read the file and pass it
        as ``Sigma`` to ``init()``.

        Args:
            session_id: The session ID.
        """
        sess = self.sessions[session_id]
        path = sess.get("state_path")
        if not path:
            return

        # Ensure the parent directory exists.
        parent = os.path.dirname(path)
        if parent and not os.path.isdir(parent):
            os.makedirs(parent)

        with open(path, "w", encoding="utf-8") as fh:
            fh.write(compact(sess["runtime"].state) + "\n")

    def _make_generate(self, sess):
        """Build a ``generate(prompt) -> str`` callable from session config.

        Used by ``step()`` when no external generate function was
        provided at construction time. Wraps ``exec_once`` to match
        the ``Runtime.step()`` interface (which expects a simple
        ``callable(str) -> str``).

        Args:
            sess: The session config ``dict``.

        Returns:
            A callable ``(prompt: str) -> str``.
        """
        def generate(prompt):
            stub = None
            if sess["host"] == "stub":
                stubs = sess["stub_responses"]
                if sess["stub_i"] >= len(stubs):
                    raise SkillStateError("stub_responses exhausted")
                stub = stubs[sess["stub_i"]]
                sess["stub_i"] += 1

            result = exec_once(
                sess["host"],
                sess["model"],
                "",           # no system prompt — folded by caller
                prompt,
                stub_response=stub,
            )
            return result["text"]

        return generate


# =============================================================================
# JSON-RPC DISPATCH
# =============================================================================
# Standard MCP (Model Context Protocol) JSON-RPC 2.0 over stdio.
#
# Each line is a complete JSON message (newline-delimited).
# Notifications (messages without an ``"id"`` field) return ``None``
# (no reply — the spec requires no response for notifications).


def dispatch_mcp(server, msg):
    """Handle a single JSON-RPC 2.0 message.

    Supported methods:

    +--------------------------+--------------------------------------------+
    | Method                   | Behaviour                                  |
    +==========================+============================================+
    | ``initialize``           | MCP handshake — returns capabilities.      |
    +--------------------------+--------------------------------------------+
    | ``notifications/*``      | Silently acknowledged (no reply).          |
    +--------------------------+--------------------------------------------+
    | ``ping``                 | Health check — returns empty result.       |
    +--------------------------+--------------------------------------------+
    | ``tools/list``           | Return the tool definitions (schema).      |
    +--------------------------+--------------------------------------------+
    | ``tools/call``           | Execute a tool and return its result.      |
    +--------------------------+--------------------------------------------+
    | ``shutdown`` / ``exit``  | Graceful termination.                      |
    +--------------------------+--------------------------------------------+

    Args:
        server:
            ``McpServer`` instance.
        msg:
            Parsed JSON-RPC message ``dict``.

    Returns:
        JSON-RPC response ``dict``, or ``None`` for notifications
        (which get no reply per the JSON-RPC 2.0 spec).
    """
    if not isinstance(msg, dict):
        return {
            "jsonrpc": "2.0",
            "id": None,
            "error": {"code": -32600, "message": "invalid"},
        }

    mid = msg.get("id", None)
    method = msg.get("method") or ""
    params = msg.get("params") if isinstance(msg.get("params"), dict) else {}
    is_note = "id" not in msg     # notifications have no ``id`` field

    def result(payload):
        """Build a success response (or ``None`` for notifications)."""
        if is_note:
            return None
        return {"jsonrpc": "2.0", "id": mid, "result": payload}

    def error(code, message):
        """Build an error response (or ``None`` for notifications)."""
        if is_note:
            return None
        return {
            "jsonrpc": "2.0",
            "id": mid,
            "error": {"code": code, "message": message},
        }

    # -- MCP handshake --
    # The MCP client sends ``initialize`` with its own capabilities.
    # We respond with our capabilities (only ``tools``).
    if method == "initialize":
        return result({
            "protocolVersion": MCP_PROTOCOL,
            "capabilities": {"tools": {}},    # we only support tools
            "serverInfo": {
                "name": "skillstate",
                "version": _MCP_VERSION,
            },
        })

    # -- Notifications --
    # ``notifications/initialized`` is sent by the client after it
    # receives the ``initialize`` response. We silently acknowledge
    # all notifications.
    if method == "notifications/initialized" or method.startswith(
        "notifications/"
    ):
        return None

    # -- Ping --
    if method == "ping":
        return result({})

    # -- Tool listing --
    # Returns the full tool schema so the MCP client knows what tools
    # are available and what parameters each accepts.
    if method == "tools/list":
        tools = []
        for name, spec in _MCP_TOOL_SCHEMA.items():
            tools.append({
                "name": name,
                "description": spec["description"],
                "inputSchema": spec["inputSchema"],
            })
        return result({"tools": tools})

    # -- Tool execution --
    # The client calls a tool by name with arguments. We dispatch to
    # ``server.call_tool`` and return the result as an MCP tool result.
    if method == "tools/call":
        name = params.get("name") or ""
        args = params.get("arguments") or {}

        try:
            payload = server.call_tool(name, args)
        except SkillStateError as exc:
            # Model-facing errors → return as tool result with
            # ``isError: true`` so the MCP client can surface it.
            return result({
                "content": [{"type": "text", "text": str(exc)}],
                "isError": True,
            })
        except TypeError as exc:
            # Parameter mismatch (e.g. wrong types in arguments).
            return result({
                "content": [{"type": "text", "text": str(exc)}],
                "isError": True,
            })

        # Success — return the payload as compact JSON in a text
        # content block.
        text = json.dumps(
            payload, separators=(",", ":"), ensure_ascii=False
        )
        return result({
            "content": [{"type": "text", "text": text}],
            "isError": False,
        })

    # -- Shutdown --
    # ``shutdown`` returns a result (the client waits for it).
    # ``exit`` is a notification (no reply expected).
    if method in ("shutdown", "exit"):
        return result({}) if method == "shutdown" else None

    return error(-32601, "method not found: %s" % method)


# =============================================================================
# SERVE MCP — stdio JSON-RPC loop
# =============================================================================


def serve_mcp(stdin=None, stdout=None):
    """Run the MCP server on stdio (newline-delimited JSON-RPC).

    This is the entry point for ``skillstate.py mcp``. It never prints
    anything to stdout except JSON-RPC responses — all logging goes to
    stderr (or is suppressed entirely).

    The loop reads one JSON message per line, dispatches it, and writes
    the response. Empty lines are skipped. Invalid JSON lines are
    silently ignored (robustness against stray noise, keep-alive pings,
    or log output accidentally written to stdout by a subprocess).

    Args:
        stdin:
            Override for testing (defaults to ``sys.stdin``).
        stdout:
            Override for testing (defaults to ``sys.stdout``).

    Returns:
        ``int`` — ``0`` on clean exit (stdin closed / EOF).
    """
    server = McpServer()
    inf = stdin or sys.stdin
    out = stdout or sys.stdout

    while True:
        line = inf.readline()
        if not line:
            break       # stdin closed — clean exit

        line = line.strip()
        if not line:
            continue     # skip empty lines (keep-alive, whitespace)

        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue     # silently skip invalid JSON (noise-resistant)

        resp = dispatch_mcp(server, msg)
        if resp is not None:
            out.write(
                json.dumps(resp, separators=(",", ":"), ensure_ascii=False)
                + "\n"
            )
            out.flush()

    return 0