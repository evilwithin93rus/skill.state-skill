#!/usr/bin/env python3
"""
The full Algorithm-1 execution loop — ``run_loop()``.

This is the complete Algorithm-1 driver for standalone / Mode C
operation. It runs a full T-step horizon:

::

    O_t ← observe()          # observe the environment
    prompt ← P + Σ_t + O_t   # assemble the flat prompt
    a_t ← generate(prompt)   # call the model (with retry loop)
    Σ_{t+1} ← merge(Σ_t, ΔΣ_t)  # validate + merge the state patch
    act(a_t)                 # execute the action in the environment
    t ← t + 1                # repeat until DONE or horizon

The caller provides **shell commands** for observe and act — the
runtime calls them as subprocesses. This decouples the loop from any
specific environment (warehouse, CTF, code review, etc.).

.. rubric:: Termination

The loop exits when:

- The model returns action ``"DONE"`` (success).
- The horizon limit ``T`` is reached (forced exit).
- An unrecoverable error occurs (retry budget exhausted, state budget
  exceeded, or environment failure).

.. rubric:: References

- ``references/execution-loop.md`` — Algorithm 1 formal pseudocode.
- ``rules/state-boundedness.md`` — sigma budget rationale.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess

from .errors import SkillStateError
from .hosts import exec_once
from .serialization import compact
from .step import Runtime, apply_step


# =============================================================================
# PUBLIC API
# =============================================================================


def run_loop(
    host,
    instructions,
    schema,
    state,
    observe_cmd,
    action_cmd,
    model=None,
    horizon=50,
    max_retries=3,
    runtime_owned=(),
    sigma_budget=None,
    timeout=180,
    trace_path=None,
    extra=None,
    stub_responses=None,
    initial_observation=None,
    workdir=None,
):
    """Run the full Algorithm-1 execution loop.

    This is the complete Mode C driver. Each step:

    1. Observe the environment (shell command or, for the first step,
       ``initial_observation``).
    2. Build the flat prompt: ``P`` + ``compact(Σ_t)`` + ``O_t``.
    3. Call the model via ``exec_once`` (with per-step retry on
       validation failure).
    4. Parse → Validate → Merge the response.
    5. Execute the validated action in the environment (shell command).
    6. Record the step in the trace (and optionally append to a
       trace file for live monitoring).

    Args:
        host:
            Host name for model calls (one of ``HOSTS``: ``"api"``,
            ``"opencode"``, ``"qwen"``, ``"claude"``, ``"codex"``,
            ``"stub"``).
        instructions:
            Immutable skill spec ``P`` (the "system prompt" string).
        schema:
            State schema ``dict`` for validation.
        state:
            Initial ``Σ₀`` dict.
        observe_cmd:
            Shell command (``str`` or ``list[str]``) to observe the
            environment. Its ``stdout`` becomes ``O_t``.
        action_cmd:
            Shell command (``str`` or ``callable``) to execute the
            validated action. May contain ``{action}`` placeholder
            which is replaced with the shell-quoted action string.
            If a callable, it receives ``(action: str) -> str`` and
            returns the observation.
        model:
            Model identifier string (e.g. ``"openrouter/anthropic/..."``).
            Required for ``api`` host; optional for others.
        horizon:
            Maximum number of steps before forced exit (default ``50``).
        max_retries:
            Per-step retry budget for malformed responses (default ``3``).
        runtime_owned:
            ``tuple[str, ...]`` of dotted paths the model cannot write.
        sigma_budget:
            Max characters for ``compact(state)`` — raises if exceeded.
        timeout:
            Seconds per model call (default ``180``).
        trace_path:
            If set, append per-step JSONL metrics (one line per step)
            to this file path.
        extra:
            ``dict`` of host-specific flags (e.g. ``{"agent": "..."}``
            for opencode).
        stub_responses:
            ``list[str]`` of pre-canned responses for ``"stub"`` host
            (deterministic replay / testing).
        initial_observation:
            First observation string to use — skips the first
            ``observe_cmd`` call. Useful for seeding the loop with
            a known initial state description.
        workdir:
            Working directory for observe/act subprocesses.

    Returns:
        ``dict`` with keys:

        - ``state`` (``dict``) — final ``Σ`` after all steps.
        - ``actions`` (``list[str]``) — action strings executed, in order.
        - ``steps`` (``int``) — number of steps taken.
        - ``totals`` (``dict``) — accumulated counters:
          ``{input, output, total, calls, wall_s, rejects}``.
        - ``trace`` (``list[dict]``) — per-step records:
          ``{step, prompt_chars, action, calls, state_chars}``.
        - ``prompt_sizes`` (``list[int]``) — per-step prompt char counts.
        - ``avg_prompt_chars`` (``float``) — mean prompt size.
        - ``max_prompt_chars`` (``int``) — largest prompt size.
        - ``prompt_growth`` (``float``) — ``last_size / first_size``
          (``1.0`` = flat, ``>1.0`` = growing).

    Raises:
        SkillStateError:
            If the retry budget is exhausted at any step, or if the
            sigma budget is exceeded.
        RuntimeError:
            If the observe or act subprocess fails unexpectedly.
    """
    # -- Initialise the runtime driver --
    # ``Runtime`` owns the state across the horizon and handles the
    # per-step retry loop internally.
    rt = Runtime(
        schema=schema,
        state=state,
        runtime_owned=runtime_owned,
        max_retries=max_retries,
        sigma_budget=sigma_budget,
    )

    # System prompt P is the immutable skill spec.
    # It stays the same for every step — no accumulation.
    system_prompt = instructions.strip()
    cwd = workdir

    # =========================================================================
    # Environment observer (``shell_observe``)
    # =========================================================================
    # Called at the start of every step to get O_t.
    # ``initial_observation`` is consumed exactly once — the first call
    # returns it without running the observe command. Subsequent calls
    # invoke the shell command.
    def shell_observe():
        """Call ``observe_cmd`` and return its ``stdout`` as ``O_t``."""
        # Consume ``initial_observation`` on the very first call only.
        # We use a function attribute as a cheap single-use flag (no
        # need for a ``nonlocal`` variable or a class).
        if initial_observation is not None and not hasattr(shell_observe, "used"):
            shell_observe.used = True
            return initial_observation

        proc = subprocess.run(
            observe_cmd if isinstance(observe_cmd, list)
            else shlex.split(observe_cmd),
            capture_output=True,
            text=True,
            timeout=60,
            cwd=cwd,
        )
        if proc.returncode != 0:
            raise RuntimeError(
                "observe failed: %s" % (proc.stderr or proc.stdout)[:300]
            )
        return proc.stdout

    # =========================================================================
    # Environment actor (``shell_act``)
    # =========================================================================
    # Called after every successful merge to execute the model's
    # validated action in the environment. The result of the action
    # becomes the next observation (``O_{t+1}``).
    def shell_act(action):
        """Execute the validated action in the environment."""
        if action_cmd is None:
            return "ok"

        # Callable action handler — used for stub/offline testing
        # where the "environment" is a simple function.
        if callable(action_cmd):
            return action_cmd(action)

        # Template-based: replace ``{action}`` placeholder with the
        # shell-quoted action string. If the template doesn't contain
        # ``{action}``, the action is appended as a positional argument.
        tmpl = action_cmd
        quoted = shlex.quote(action)
        if "{action}" in tmpl:
            cmd = tmpl.replace("{action}", quoted)
        else:
            cmd = tmpl + " " + quoted

        proc = subprocess.run(
            shlex.split(cmd) if isinstance(cmd, str) else cmd,
            capture_output=True,
            text=True,
            timeout=60,
            cwd=cwd,
        )

        # Combine stdout and stderr into the observation text.
        # The model can see both to understand what happened.
        out = (proc.stdout or "") + (
            ("\n" + proc.stderr) if proc.stderr else ""
        )
        if proc.returncode != 0 and not out.strip():
            return "action failed (exit %d)" % proc.returncode
        return out.strip() or "ok"

    # =========================================================================
    # Loop state
    # =========================================================================
    trace = []             # list[dict] — per-step metrics for analysis
    actions = []           # list[str] — executed action strings, in order
    totals = {             # accumulated token and timing counters
        "input": 0,
        "output": 0,
        "total": 0,
        "calls": 0,
        "wall_s": 0.0,
        "rejects": 0,
    }
    observation = shell_observe()   # O_0 — initial environment observation
    stub_i = 0                      # index into stub_responses list

    # =========================================================================
    # Main loop — T steps (or until DONE)
    # =========================================================================
    for step_i in range(horizon):
        feedback = observation    # current feedback: env observation or retry rejection
        last_err = None           # track last error for "budget exhausted" message
        step_calls = 0            # API calls made during this step (counting retries)
        step_prompt_chars = 0     # prompt size for this step (for flatness tracking)
        action = None             # validated action string for this step

        # =====================================================================
        # Inner retry loop — up to max_retries + 1 attempts per step
        # =====================================================================
        # On each validation failure, the rejection message replaces
        # ``feedback`` and the model is called again with the same
        # state + new feedback. State is only committed after a fully
        # successful parse-validate-merge.
        for attempt in range(max_retries + 1):
            # Assemble the user prompt: compact(Σ_t) + O_t.
            # The state is embedded as a fenced JSON block so the model
            # can read it clearly.
            user_prompt = (
                "Skill Execution State:\n```json\n%s\n```\n\n"
                "Latest Observation: %s\n\n"
                "Provide your response with:\n"
                "1. Step-by-step reasoning (will be discarded after "
                "execution)\n"
                "2. A JSON block fenced with ```json ... ``` containing "
                'exactly these two keys:\n   {"state_patch": {<your '
                'state updates; set a key to null to delete it; include '
                'only keys you are changing>},\n    "action": "<the exact '
                'command to execute>"}'
            ) % (compact(rt.state), feedback)

            # Full prompt = system_prompt ("Instructions:\n...") + user_prompt.
            # This is what the model sees — no prior steps, no history.
            full_prompt = (
                ("Instructions:\n%s\n\n" % system_prompt) + user_prompt
                if system_prompt
                else user_prompt
            )
            step_prompt_chars = len(full_prompt)

            # -- Resolve stub response for offline testing --
            stub = None
            if host == "stub" and stub_responses is not None:
                if stub_i >= len(stub_responses):
                    raise RuntimeError(
                        "stub_responses exhausted at step %d" % step_i
                    )
                stub = stub_responses[stub_i]
                stub_i += 1

            # -- Call the model --
            # Hosts without a ``--system-prompt`` flag (opencode, codex,
            # stub) get ``P`` folded into the user message. Others pass
            # ``P`` as a separate system prompt argument.
            fold = host in ("opencode", "codex", "stub")
            result = exec_once(
                host,
                model,
                "" if fold else system_prompt,
                full_prompt if fold else user_prompt,
                timeout=timeout,
                extra=extra,
                stub_response=stub,
            )

            step_calls += 1
            totals["calls"] += 1
            totals["input"] += int(result["usage"].get("input") or 0)
            totals["output"] += int(result["usage"].get("output") or 0)
            totals["total"] += int(result["usage"].get("total") or 0)
            totals["wall_s"] += float(result.get("wall_s") or 0)

            # -- Validate the model's response --
            try:
                next_state, action = apply_step(
                    rt.state, result["text"], rt.schema,
                    rt.runtime_owned, rt.action_space,
                )
            except SkillStateError as exc:
                # Response rejected — feed the error back as observation
                # and let the model self-correct on the next attempt.
                last_err = exc
                totals["rejects"] += 1
                feedback = "Your previous response was rejected: %s" % exc
                continue

            # -- State budget check --
            # Verify after merge that the state hasn't grown beyond the
            # sigma budget. This catches unbounded fields (e.g. a list
            # accumulating entries) before they bloat the prompt.
            if rt.sigma_budget is not None:
                size = len(compact(next_state))
                if size > rt.sigma_budget:
                    raise SkillStateError(
                        "state budget exceeded (%d > %d chars)"
                        % (size, rt.sigma_budget)
                    )

            # -- Success: commit the new state --
            rt.state = next_state
            break

        else:
            # The ``else`` clause on a ``for`` loop fires if the loop
            # completes without ``break`` — i.e. the retry budget was
            # exhausted without a single successful merge.
            raise SkillStateError(
                "retry budget exhausted at step %d; last: %s"
                % (step_i, last_err)
            )

        # -- Record this step --
        actions.append(action)
        entry = {
            "step": step_i,
            "prompt_chars": step_prompt_chars,
            "action": action,
            "calls": step_calls,
            "state_chars": len(compact(rt.state)),
        }
        trace.append(entry)

        # Live trace output: append one JSONL line per step for
        # real-time monitoring and offline analysis.
        if trace_path:
            with open(trace_path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, ensure_ascii=False) + "\n")

        # -- Check for termination --
        if action == "DONE":
            break

        # -- Execute the action and get the next observation --
        observation = shell_act(action)

    # =========================================================================
    # Aggregate results
    # =========================================================================
    prompt_sizes = [e["prompt_chars"] for e in trace]

    return {
        "state": rt.state,
        "actions": actions,
        "steps": len(actions),
        "totals": totals,
        "trace": trace,
        "prompt_sizes": prompt_sizes,
        # Avoid division by zero if the loop didn't take any steps
        # (empty trace — horizon=0 or initial DONE).
        "avg_prompt_chars": (
            (sum(prompt_sizes) / float(len(prompt_sizes)))
            if prompt_sizes else 0.0
        ),
        "max_prompt_chars": max(prompt_sizes) if prompt_sizes else 0,
        "prompt_growth": (
            (prompt_sizes[-1] / float(prompt_sizes[0]))
            if prompt_sizes and prompt_sizes[0] else 0.0
        ),
    }