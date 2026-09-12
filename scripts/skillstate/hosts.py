#!/usr/bin/env python3
"""
Host adapters for Mode C — one-shot CLI wrappers and API client.

This module provides the abstraction layer between the deterministic
runtime and the actual model-hosting backends. Every host implements
two functions:

1.  **``build_host_argv``** — returns the ``argv`` list (pure, no I/O).
2.  **``extract_host_response``** — parses the host's output format into
    ``(text, usage)``.

.. rubric:: Mode C Design

Mode C runs the T-step loop inside a single subprocess. The host agent
(opencode, Cursor, Claude Code, etc.) does **not** interpret the loop
in its own transcript. Instead, it compiles the loop into one tool call
that invokes a one-shot host CLI (or bare API) per step.

This means prompt growth stays in the **child** process — the parent
chat sees one tool call per episode, not per step. The child's
transcript is destroyed when the subprocess exits.

.. rubric:: Supported Hosts

+------------+----------------------------------------------------+
| Host       | Description                                        |
+============+====================================================+
| ``api``    | OpenRouter / OpenAI-compatible HTTP API (no CLI).  |
+------------+----------------------------------------------------+
| ``opencode``| ``opencode run --pure`` — fresh session per call. |
+------------+----------------------------------------------------+
| ``qwen``   | ``qwen --bare`` CLI with native system prompt.     |
+------------+----------------------------------------------------+
| ``claude`` | ``claude -p --output-format json`` CLI.            |
+------------+----------------------------------------------------+
| ``codex``  | ``codex exec`` CLI.                                |
+------------+----------------------------------------------------+
| ``stub``   | Offline canned responses (testing / replay).       |
+------------+----------------------------------------------------+

.. rubric:: References

- ``references/host-runtime-adaptation.md`` — how to adapt a new host.
- ``references/evidence.md`` — host comparison benchmarks.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import time
import urllib.error
import urllib.request


# =============================================================================
# CONSTANTS
# =============================================================================

# All supported one-shot backends.
# ``stub`` is offline / test-only; the rest require either a CLI binary
# or an API key.
HOSTS = ("api", "opencode", "qwen", "claude", "codex", "stub")

# OpenRouter API endpoint for chat completions.
# OpenRouter provides a unified API to many model providers.
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"


# =============================================================================
# INTERNAL — API KEY RESOLUTION
# =============================================================================


def _api_key():
    """Resolve the OpenRouter / OpenAI API key from environment or keyfile.

    Lookup order (first found wins):

    1.  ``OPENROUTER_API_KEY`` environment variable.
    2.  ``OPENAI_API_KEY`` environment variable (fallback for OpenAI-
        compatible endpoints).
    3.  ``OPENROUTER_API_KEY_FILE`` environment variable → read first
        line from that file.
    4.  ``~/.config/openrouter/api_key`` file.
    5.  ``/home/deck/tempopenrouter.txt`` (legacy path).

    Returns:
        ``str | None`` — the API key (stripped), or ``None`` if no
        key was found via any method.
    """
    key = os.environ.get("OPENROUTER_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if key:
        return key.strip()

    # File-based fallbacks — read the first line of the key file.
    for path in (
        os.environ.get("OPENROUTER_API_KEY_FILE"),
        "/home/deck/tempopenrouter.txt",
        os.path.expanduser("~/.config/openrouter/api_key"),
    ):
        if path and os.path.isfile(path):
            return open(path, encoding="utf-8").read().strip()

    return None


# =============================================================================
# PUBLIC — BUILD HOST ARGV
# =============================================================================


def build_host_argv(host, model, system_prompt, user_prompt, extra=None):
    """Build the ``argv`` list a one-shot host CLI should run.

    **Pure function** — no I/O, no side effects. The returned list can
    be passed directly to ``subprocess.run()``.

    .. rubric:: System Prompt Handling

    Different hosts handle the system prompt differently:

    - **qwen, claude**: passed as ``--system-prompt`` flag (native support).
    - **opencode, codex, stub**: folded into a single message body because
      these hosts don't have a ``--system-prompt`` flag. ``P`` is
      prepended to the user message.
    - **api**: sent as a separate ``"system"`` role message in the HTTP
      payload (handled during HTTP request construction, not in argv).

    Args:
        host:
            One of ``HOSTS`` (``"api"``, ``"opencode"``, ``"qwen"``,
            ``"claude"``, ``"codex"``, ``"stub"``).
        model:
            Model identifier string (e.g. ``"openrouter/anthropic/..."``
            for OpenRouter, ``"claude-sonnet-4-20250514"`` for Claude CLI).
        system_prompt:
            Immutable skill spec ``P`` (may be empty).
        user_prompt:
            The full step prompt containing ``compact(Σ_t) + O_t``.
        extra:
            ``dict`` with optional host-specific keys:

            - ``"agent"`` — opencode agent name (``--agent`` flag).
            - ``"title"`` — opencode session title (``--title`` flag).
            - ``"dir"`` — opencode working directory (``--dir`` flag).

    Returns:
        ``list[str]`` — argv ready for ``subprocess.run()``.

        For ``"api"`` host, returns a sentinel list
        ``["api", model, "POST", OPENROUTER_URL]`` since ``exec_once``
        handles HTTP requests in-process (no subprocess needed).

    Raises:
        ValueError: If ``host`` is not in ``HOSTS``.
    """
    host = host.lower().strip()
    extra = extra or {}

    if host not in HOSTS:
        raise ValueError(
            "unknown host %r; choose from %s" % (host, ", ".join(HOSTS))
        )

    # -- stub: offline testing --
    # The "model" is a path to a file containing the canned response.
    # ``exec_once`` reads it; no subprocess is needed, but we return
    # a ``cat`` argv for completeness.
    if host == "stub":
        return ["cat", model or "/dev/null"]

    # -- api: handled in-process by exec_once --
    # Returns a sentinel list that ``exec_once`` recognises as
    # "make an HTTP call, not a subprocess call."
    if host == "api":
        return ["api", model or "", "POST", OPENROUTER_URL]

    # -- qwen: native --system-prompt support --
    # ``--bare`` disables the interactive shell.
    # ``--safe-mode`` strips ANSI formatting from output.
    # ``-o json`` requests JSON output format.
    # ``-y`` skips confirmation prompts.
    if host == "qwen":
        argv = ["qwen", "--bare", "--safe-mode", "-o", "json", "-y"]
        if model:
            argv += ["-m", model]
        if system_prompt:
            argv += ["--system-prompt", system_prompt]
        argv.append(user_prompt)
        return argv

    # -- claude: native --system-prompt via -p flag --
    # ``-p`` puts the CLI in "pipe" mode (stdin/stdout, no interactive).
    # ``--output-format json`` ensures structured JSON output.
    if host == "claude":
        argv = ["claude", "-p", "--output-format", "json"]
        if model:
            argv += ["--model", model]
        if system_prompt:
            argv += ["--system-prompt", system_prompt]
        argv.append(user_prompt)
        return argv

    # -- codex: no --system-prompt → fold P into message body --
    if host == "codex":
        body = user_prompt
        if system_prompt:
            body = system_prompt.rstrip() + "\n\n" + user_prompt
        argv = ["codex", "exec", "--skip-git-repo-check"]
        if model:
            argv += ["-m", model]
        argv.append(body)
        return argv

    # -- opencode: no --system-prompt → fold P into message body --
    # Fresh session each call (no ``--continue``) so the child
    # transcript never accumulates across steps.
    # ``--pure`` skips plugins for reproducibility.
    # ``--format json`` ensures structured output (NDJSON stream).
    # ``--auto`` auto-approves tool use (but the child should have
    # no tools configured — it should answer in one shot).
    body = user_prompt
    if system_prompt:
        body = system_prompt.rstrip() + "\n\n" + user_prompt
    argv = ["opencode", "run", "--pure", "--format", "json", "--auto"]
    if model:
        argv += ["-m", model]
    agent = extra.get("agent")
    if agent:
        argv += ["--agent", agent]
    title = extra.get("title")
    if title:
        argv += ["--title", title]
    workdir = extra.get("dir")
    if workdir:
        argv += ["--dir", workdir]
    argv.append(body)
    return argv


# =============================================================================
# PUBLIC — EXTRACT HOST RESPONSE
# =============================================================================


def extract_host_response(host, stdout, stderr=""):
    """Extract model text and token usage from a host CLI's stdout.

    Different hosts emit wildly different output formats — streaming
    NDJSON, single JSON object, plain text, etc. This function handles
    all of them and returns a canonical ``(text, usage)`` pair.

    Args:
        host:
            The host name (lowercase, one of ``HOSTS``).
        stdout:
            Captured stdout string from the host CLI.
        stderr:
            Captured stderr (reserved for future use — currently
            ignored but kept in the signature for consistency).

    Returns:
        ``(text: str, usage: dict)`` where ``usage`` has keys
        ``"input"``, ``"output"``, ``"total"`` (all ``int``).
        All usage values are ``0`` if the host doesn't report token
        counts or if extraction fails.
    """
    host = host.lower().strip()
    usage = {"input": 0, "output": 0, "total": 0}
    text = ""

    # -- stub: raw passthrough --
    # No parsing needed — the canned response is the text.
    if host == "stub":
        return (stdout or ""), usage

    # -- api: standard OpenAI chat completion response --
    # Expected format:
    #   {"choices": [{"message": {"content": "..."}}], "usage": {...}}
    if host == "api":
        payload = json.loads(stdout)

        # Extract token usage.
        # OpenRouter / OpenAI both use the same field names:
        #   prompt_tokens, completion_tokens, total_tokens
        u = payload.get("usage") or {}
        usage["input"] = int(u.get("prompt_tokens") or 0)
        usage["output"] = int(u.get("completion_tokens") or 0)
        usage["total"] = int(
            u.get("total_tokens") or (usage["input"] + usage["output"])
        )

        # Extract the assistant's message content.
        choices = payload.get("choices") or []
        if not choices:
            raise RuntimeError("api response has no choices")

        msg = choices[0].get("message") or {}
        content = msg.get("content") or ""

        # Some providers return content as a **list** of text parts
        # (multimodal format). Concatenate into a single string.
        if isinstance(content, list):
            content = "".join(
                (c.get("text") or "") if isinstance(c, dict) else str(c)
                for c in content
            )

        # Some models (DeepSeek R1, o1, etc.) return reasoning in a
        # separate ``reasoning`` field. If there's no fenced JSON block
        # in ``content`` but there is one in ``reasoning``, merge them.
        # This handles the case where reasoning models put the JSON
        # block inside their chain-of-thought.
        reasoning = msg.get("reasoning") or ""
        if isinstance(reasoning, list):
            reasoning = "".join(
                (c.get("text") or "") if isinstance(c, dict) else str(c)
                for c in reasoning
            )
        if reasoning and "```" not in content and "```" in str(reasoning):
            content = (content + "\n" + reasoning).strip()
        elif not content and reasoning:
            content = reasoning

        return str(content), usage

    # -- stream-JSON / NDJSON hosts (opencode, qwen, claude) --
    # These emit one JSON object per line (NDJSON format). We scan all
    # lines looking for text content and token usage, preferring the
    # longest coherent text chunk found.
    if host in ("opencode", "qwen", "claude"):
        last_text = ""

        for line in (stdout or "").splitlines():
            line = line.strip()

            # Skip non-JSON lines (plain-text fallback).
            # If we find no JSON at all, the last non-empty plain line
            # becomes the response text.
            if not line.startswith("{"):
                if line and not last_text:
                    last_text = line
                continue

            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue

            text_bits = []      # text content collected from this event
            stack = [ev]        # DFS stack — walk the JSON tree

            while stack:
                o = stack.pop()

                if isinstance(o, dict):
                    # -- Token usage extraction --
                    # Different hosts use different key names for token
                    # usage. Try all known variants.
                    if "usage" in o and isinstance(o["usage"], dict):
                        u = o["usage"]
                        inp = int(
                            u.get("input") or u.get("prompt_tokens")
                            or u.get("inputTokens") or 0
                        )
                        out = int(
                            u.get("output") or u.get("completion_tokens")
                            or u.get("outputTokens") or 0
                        )
                        tot = int(
                            u.get("total") or u.get("total_tokens")
                            or u.get("totalTokens") or (inp + out)
                        )
                        if tot or inp or out:
                            usage["input"] += inp
                            usage["output"] += out
                            usage["total"] += tot if tot else (inp + out)

                    # Alternative: ``"tokens"`` key (used by some hosts).
                    if "tokens" in o and isinstance(o["tokens"], dict):
                        t = o["tokens"]
                        usage["input"] += int(t.get("input") or 0)
                        usage["output"] += int(t.get("output") or 0)
                        usage["total"] += int(t.get("total") or 0)

                    # -- Text extraction --
                    # Common key names that carry assistant text content.
                    for key in ("text", "content", "message", "result"):
                        v = o.get(key)
                        if isinstance(v, str) and v.strip():
                            text_bits.append(v)
                        elif isinstance(v, dict):
                            stack.append(v)

                    # opencode-specific: ``properties`` / ``part`` dicts
                    # carry text in stream events.
                    props = o.get("properties") or o.get("part") or {}
                    if isinstance(props, dict):
                        stack.append(props)

                    # Generic DFS: push any nested dict/list values
                    # for further exploration.
                    for v in o.values():
                        if isinstance(v, (dict, list)):
                            stack.append(v)

                elif isinstance(o, list):
                    stack.extend(o)

            if text_bits:
                # Prefer the longest text chunk this event carried.
                # This avoids selecting a short partial chunk when a
                # longer final text exists.
                candidate = max(text_bits, key=len)
                if len(candidate) >= len(last_text):
                    last_text = candidate

        # Fallback: the entire stdout is a single JSON object
        # (e.g. ``claude -p --output-format json`` returns one blob).
        if not last_text and (stdout or "").lstrip().startswith("{"):
            try:
                blob = json.loads(stdout)
                if isinstance(blob, dict):
                    # Try common key names for the result text.
                    for key in ("result", "content", "text", "message"):
                        v = blob.get(key)
                        if isinstance(v, str) and v.strip():
                            last_text = v
                            break
                        if isinstance(v, dict) and isinstance(
                            v.get("content"), str
                        ):
                            last_text = v["content"]
                            break

                    # Extract usage from the single-object response.
                    u = blob.get("usage") or {}
                    if isinstance(u, dict):
                        usage["input"] = int(
                            u.get("input_tokens") or u.get("input") or 0
                        )
                        usage["output"] = int(
                            u.get("output_tokens") or u.get("output") or 0
                        )
                        usage["total"] = int(
                            u.get("total_tokens")
                            or (usage["input"] + usage["output"])
                        )
            except json.JSONDecodeError:
                pass

        text = last_text or (stdout or "").strip()
        if usage["total"] == 0:
            usage["total"] = usage["input"] + usage["output"]
        return text, usage

    # -- codex and unknown hosts --
    # Treat the entire stdout as the response body. No token usage
    # extraction (codex doesn't report token counts in exec mode).
    return (stdout or "").strip(), usage


# =============================================================================
# PUBLIC — ONE-SHOT EXECUTION
# =============================================================================


def exec_once(host, model, system_prompt, user_prompt, timeout=180,
              extra=None, stub_response=None):
    """Execute one model call through the chosen host adapter.

    This is the single entry point for one-shot generation. It handles
    three distinct execution paths:

    1.  **API HTTP calls** — OpenRouter / OpenAI-compatible endpoints
        (in-process HTTP request via ``urllib``).
    2.  **CLI subprocess calls** — opencode, qwen, claude, codex
        (``subprocess.run`` with timeout).
    3.  **Stub / offline mode** — file reads or pre-canned responses
        (no network, deterministic).

    Args:
        host:
            Host name from ``HOSTS`` (see module docstring).
        model:
            Model identifier string (e.g. ``"openrouter/anthropic/..."``
            for OpenRouter). Required for ``api`` host.
        system_prompt:
            Immutable skill spec ``P``.
        user_prompt:
            The step's full prompt (``compact(Σ_t) + O_t``).
        timeout:
            Seconds before the call is aborted (default ``180``).
        extra:
            ``dict`` for host-specific flags (agent, title, dir — used
            by opencode).
        stub_response:
            Pre-canned response string for ``"stub"`` host only.

    Returns:
        ``dict`` with keys:

        - ``text`` (``str``) — the model's response text.
        - ``usage`` (``dict``) — ``{input, output, total}`` token counts.
        - ``wall_s`` (``float``) — elapsed wall-clock seconds.
        - ``argv`` (``list[str]``) — the command argv (for debugging).
        - ``returncode`` (``int``) — subprocess exit code (``0`` for
          api/stub).

        Additional keys may be present for debugging:
        ``raw`` (api), ``stdout`` / ``stderr`` (CLI hosts).

    Raises:
        RuntimeError:
            On API errors (HTTP 4xx/5xx), missing binaries (CLI not
            installed), timeouts, or non-zero exit codes with no text
            output.
    """
    host = host.lower().strip()
    t0 = time.time()

    # -- stub: offline / deterministic replay --
    # Returns a pre-canned response — no network, no subprocess.
    # Used for testing and deterministic benchmarking.
    if host == "stub":
        text = stub_response if stub_response is not None else (model or "")
        # If ``model`` is a file path, read the canned response from it.
        # This allows stub mode with file-backed responses.
        if model and os.path.isfile(model) and stub_response is None:
            text = open(model, encoding="utf-8").read()
        return {
            "text": text,
            "usage": {"input": 0, "output": 0, "total": 0},
            "wall_s": time.time() - t0,
            "argv": ["stub"],
            "returncode": 0,
        }

    # -- api: OpenRouter / OpenAI-compatible HTTP call --
    # Everything is done in-process — no subprocess for the API path.
    if host == "api":
        key = _api_key()
        if not key:
            raise RuntimeError(
                "OPENROUTER_API_KEY not set (and no keyfile found)"
            )
        if not model:
            raise RuntimeError("--model is required for --host api")

        # Strip ``openrouter/`` prefix if present.
        # OpenRouter wants the bare ``provider/model`` format in the
        # API payload, but users may type ``openrouter/provider/model``
        # for consistency with opencode's ``-m`` flag.
        api_model = model
        if api_model.startswith("openrouter/"):
            api_model = api_model[len("openrouter/"):]

        # Build chat messages.
        # System prompt is sent as a separate ``"system"`` role message
        # so the model's API distinguishes it from the user message.
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": user_prompt})

        # Build the HTTP request payload.
        # ``temperature=0.0`` ensures deterministic output for
        # reproducible benchmark results.
        # ``max_tokens=4096`` is a generous ceiling — the model needs
        # room for both reasoning (scratch) and the JSON envelope.
        body = {
            "model": api_model,
            "temperature": 0.0,
            "max_tokens": 4096,
            "messages": messages,
        }
        data = json.dumps(body).encode("utf-8")

        # OpenRouter requires HTTP-Referer and X-Title headers.
        # These are optional but help with rate-limiting and analytics.
        req = urllib.request.Request(
            OPENROUTER_URL,
            data=data,
            headers={
                "Authorization": "Bearer %s" % key,
                "Content-Type": "application/json",
                "HTTP-Referer": "https://github.com/skill-state",
                "X-Title": "skillstate-loop",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            err = e.read().decode("utf-8", errors="replace")
            raise RuntimeError(
                "OpenRouter HTTP %s: %s" % (e.code, err[:500])
            )

        text, usage = extract_host_response("api", raw)
        return {
            "text": text,
            "usage": usage,
            "wall_s": time.time() - t0,
            "argv": build_host_argv(
                "api", model, system_prompt, user_prompt, extra
            ),
            "returncode": 0,
            "raw": raw,   # preserved for debugging API issues
        }

    # -- CLI hosts: opencode, qwen, claude, codex --
    # All CLI hosts follow the same pattern:
    #   1. Build argv via ``build_host_argv``.
    #   2. ``subprocess.run`` with timeout.
    #   3. Extract text and usage via ``extract_host_response``.
    argv = build_host_argv(host, model, system_prompt, user_prompt, extra)
    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=timeout,
            # ``OPENCODE_DISABLE_TERMINAL_TITLE`` prevents opencode from
            # trying to set the terminal title (which fails when run
            # without a TTY and produces noise on stderr).
            env={**os.environ, "OPENCODE_DISABLE_TERMINAL_TITLE": "1"},
        )
    except FileNotFoundError as e:
        raise RuntimeError(
            "host binary not found for %s: %s" % (host, e)
        )
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(
            "host timed out after %ss: %s" % (timeout, host)
        )

    text, usage = extract_host_response(
        host, proc.stdout or "", proc.stderr or ""
    )

    # Non-zero exit with no text → propagate the error to the caller.
    # If the CLI produced text despite the non-zero exit, accept it
    # (some CLIs exit non-zero on usage warnings but still produce
    # valid output).
    if proc.returncode != 0 and not text:
        raise RuntimeError(
            "host %s exit %d: %s"
            % (
                host, proc.returncode,
                (proc.stderr or proc.stdout or "")[-400:]
            )
        )

    return {
        "text": text,
        "usage": usage,
        "wall_s": time.time() - t0,
        "argv": argv,
        "returncode": proc.returncode,
        "stdout": proc.stdout,   # preserved for debugging
        "stderr": proc.stderr,
    }