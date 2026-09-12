#!/usr/bin/env python3
"""
One validated transition — ``parse → validate → merge`` — plus ``Runtime`` driver.

The ``Runtime`` class owns the state across an entire horizon and
implements the bounded retry loop (``max_retries``) with rollback
semantics. Every step is atomic: if any phase fails, the state is
untouched and the model is re-prompted with the rejection message.

.. rubric:: The Transition Pipeline (``apply_step``)

::

    raw_response
        │
        ▼
    parse_response()   → (patch, action)    ← ContractError on envelope failure
        │
        ▼
    validate_patch()   → ok / raise          ← ValidationError on schema failure
        │
        ▼
    action_space check → ok / raise          ← optional: verb-set enforcement
        │
        ▼
    merge()            → next_state          ← only if all checks passed

.. rubric:: References

- ``references/execution-loop.md`` — Algorithm 1 formal description.
- ``rules/validation-and-rollback.md`` — retry semantics.
"""

from __future__ import annotations

from copy import deepcopy

from .errors import SkillStateError, ValidationError
from .merge import merge as do_merge
from .parse import parse_response
from .serialization import compact
from .validate import validate_patch


def apply_step(state, response, schema=None, runtime_owned=(), action_space=None):
    """Execute one validated transition: raw response → ``(next_state, action)``.

    This is the single-call wrapper that the runtime uses for each step.
    The return-value pattern enforces the pipeline order: the caller **cannot**
    access ``action`` without having passed all three validation gates.

    Pipeline (load-bearing order):

    1. ``parse_response(response)``          → ``(patch, action)``
    2. ``validate_patch(patch, schema, ...)`` → ``None`` / raise
    3. ``action_space`` check                 → ``None`` / raise
    4. ``merge(state, patch)``                → ``next_state``

    If **any** step raises, ``state`` is untouched and the caller can
    safely retry with the same state dict.

    Args:
        state:
            Current ``Σ_t`` dict. NOT mutated.
        response:
            Raw model output string (free-form, may include markdown).
        schema:
            Optional schema dict for validation. If ``None``, the patch
            is accepted without type / key checking (dangerous).
        runtime_owned:
            ``tuple[str, ...]`` — dotted paths the model cannot write.
        action_space:
            Optional ``set[str]`` of allowed action verb prefixes
            (e.g. ``{"Ship","Store","Move","DONE"}``).
            See ``_expand_actions`` for prefix-matching semantics.

    Returns:
        ``(next_state: dict, action: str)`` — the merged state and
        the validated action string.

    Raises:
        ContractError: From ``parse_response()`` — envelope malformed.
        ValidationError: From ``validate_patch()`` or action-space check.
    """
    patch, action = parse_response(response)

    if schema is not None:
        validate_patch(patch, schema, runtime_owned)

    # Action-space enforcement.
    # ``action_space`` is a set of verbs (or verb prefixes). ``"Ship item_12
    # shelf_42"`` matches if ``"Ship"`` is in the space — parameterised
    # actions are allowed, but the verb vocabulary is constrained.
    if action_space is not None and action not in _expand_actions(action_space, action):
        raise ValidationError(
            "action %r is not in the declared action space: %s."
            % (action, ", ".join(sorted(action_space)))
        )

    return do_merge(state, patch), action


def _expand_actions(action_space, action):
    """Match an action against a space of verb prefixes.

    ``"Ship item_12 shelf_42"`` matches if ``"Ship"`` is in
    ``action_space`` — parameterised actions are allowed while the verb
    vocabulary is constrained.

    Args:
        action_space: ``set[str]`` of allowed verb prefixes.
        action: The full action string (e.g. ``"Ship item_12 shelf_42"``).

    Returns:
        ``set[str]`` — a set containing the original action-space verbs
        plus the full action string if its prefix-verb is in the space.
    """
    # Extract the first whitespace-delimited token as the verb.
    verb = action.split()[0] if action.split() else ""
    return {a for a in action_space} | ({action} if verb in action_space else set())


class Runtime:
    """Minimal Algorithm-1 driver.

    Owns the state across an entire horizon. The **caller** owns the
    model and the environment — ``Runtime`` only handles the
    prompt assembly, validation, merge, and retry logic.

    Key properties:

    - **State is the source of truth.** Reasoning traces are destroyed
      after each step (``del response`` — not returned, not stored).
      What matters for future steps must be projected into ``Σ`` via
      the ``state_patch``.

    - **Retry budget** bounds recovery from malformed responses. The
      model gets ``max_retries`` chances to produce a valid envelope
      before the runtime gives up and raises ``SkillStateError``.

    - **Sigma budget** catches unbounded state growth at runtime.
      If ``compact(Σ)`` exceeds ``sigma_budget`` characters, the
      runtime raises — this catches the O(T²) prompt-bloat that a
      ``list`` field causes.

    .. rubric:: References

    - ``references/execution-loop.md`` — Algorithm 1 pseudocode.
    - ``rules/state-boundedness.md`` — sigma budget and linting.
    """

    def __init__(self, schema, state=None, runtime_owned=(),
                 action_space=None, max_retries=3, sigma_budget=None):
        """Initialise the runtime with schema, state, and policy knobs.

        Args:
            schema:
                The state schema ``dict`` (used for ``validate_patch``).
            state:
                Initial ``Σ₀`` dict. Defaults to ``{}`` if ``None``.
                A deep copy is made so the caller's reference is safe.
            runtime_owned:
                ``tuple[str, ...]`` of dotted paths the model cannot write.
            action_space:
                Optional ``set[str]`` of allowed action verb prefixes.
            max_retries:
                How many times to retry a malformed response per step
                (default ``3``). Total attempts = ``max_retries + 1``.
            sigma_budget:
                Max characters for ``compact(state)`` — raises
                ``SkillStateError`` if exceeded after any successful merge.
        """
        self.schema = schema
        self.state = deepcopy(state) if state is not None else {}
        self.runtime_owned = tuple(runtime_owned)
        self.action_space = action_space
        self.max_retries = max_retries
        self.sigma_budget = sigma_budget

    def prompt(self, instructions, observation):
        """Assemble the entire per-step prompt from three inputs.

        **Three inputs, no history.** This is the flatness guarantee
        — no prior observations, no prior actions, no reasoning traces.
        Just the immutable skill spec ``P``, the compact current state
        ``Σ_t``, and the latest observation ``O_t``.

        Args:
            instructions:
                Immutable skill spec ``P`` (the "system prompt").
            observation:
                The latest observation ``O_t`` (from the environment, or
                a rejection message on retry).

        Returns:
            ``str`` — the complete prompt to send to the model.
            Includes formatting instructions so the model knows the
            expected two-key envelope format.
        """
        return (
            "Instructions:\n%s\n\n"
            "Skill Execution State:\n```json\n%s\n```\n\n"
            "Latest Observation: %s\n\n"
            "Provide your response with:\n"
            "1. Step-by-step reasoning (will be discarded after execution)\n"
            "2. A JSON block fenced with ```json ... ``` containing exactly "
            'these two keys:\n   {"state_patch": {<your state updates; set '
            'a key to null to delete it; include only keys you are changing>}'
            ',\n    "action": "<the exact command to execute>"}'
        ) % (instructions, compact(self.state), observation)

    def step(self, generate, instructions, observation):
        """Run one step with retry-rollback (Algorithm 1 lines 5-12).

        The model is called via ``generate(prompt) -> str``. On validation
        failure, the rejection message **becomes the next observation**
        (Algorithm 1 line 9: "re-prompt with rejection"). State is updated
        **only** on a fully successful validate-and-merge.

        Args:
            generate:
                Callable that takes a ``(prompt: str)`` and returns
                ``str`` — the model's raw response.
            instructions:
                Immutable skill spec ``P``.
            observation:
                The latest observation (environment observation on first
                try, rejection message on retries).

        Returns:
            ``str`` — the validated action to execute in the environment.

        Raises:
            SkillStateError:
                If the retry budget (``max_retries + 1`` attempts) is
                exhausted without a valid response. The state is still
                untouched (no partial merges occurred).
        """
        feedback = observation
        last = None

        for _ in range(self.max_retries + 1):
            response = generate(self.prompt(instructions, feedback))

            try:
                next_state, action = apply_step(
                    self.state, response, self.schema,
                    self.runtime_owned, self.action_space,
                )
            except SkillStateError as exc:
                # Model produced a malformed response.
                # Feed the rejection message back as the next observation
                # so the model can see what it did wrong and self-correct.
                last = exc
                feedback = "Your previous response was rejected: %s" % exc
                continue

            # -- State budget check --
            # After a successful merge, verify that compact(state) is
            # still within the sigma budget. If not, the schema has an
            # unbounded field (e.g. a growing list) and the run must
            # be aborted rather than allowed to consume unbounded tokens.
            if self.sigma_budget is not None:
                size = len(compact(next_state))
                if size > self.sigma_budget:
                    raise SkillStateError(
                        "state budget exceeded (%d > %d chars): the "
                        "schema has an unbounded field, see "
                        "rules/state-boundedness.md"
                        % (size, self.sigma_budget)
                    )

            # -- Success --
            # Commit the new state and destroy the reasoning trace.
            # ``del response`` is symbolic — the local variable goes
            # out of scope when the loop ends anyway — but it makes
            # the intent explicit: reasoning is never stored.
            self.state = next_state
            del response
            return action

        # Retry budget exhausted.
        # The model couldn't produce a valid response within the allowed
        # attempts. The state is still untouched (we never committed
        # a partial merge).
        raise SkillStateError(
            "retry budget exhausted; last error: %s" % last
        )