#!/usr/bin/env python3
"""
Envelope parsing — extract the ``(state_patch, action)`` pair from model output.

The model response is free-form text that may contain reasoning,
markdown, and code blocks. This function extracts **exactly one** JSON
object with the two required keys ``{state_patch, action}``. Everything
else — the reasoning text, the conversational preamble, the signing-off
pleasantries — is discarded.

.. rubric:: Parsing Strategy

1.  **Fenced-block scan.**
    Search for `` ```json `` blocks (or ``json5`` / ``jsonc`` — some
    models/linters use those). Try to parse each as JSON. Collect
    objects that parse successfully.

2.  **Fallback: bare JSON.**
    If no fenced block was found, check whether the **entire** response
    text is itself a valid two-key JSON object. This handles host agents
    that strip markdown or models that emit plain JSON without fences.

3.  **Error reporting.**
    If nothing matched, raise a ``ContractError`` with an actionable
    message. The message itself becomes the next observation in the
    retry loop so the model can self-correct.

.. rubric:: Design Choices

- Reasoning **before or after** the JSON block is scratch space — it is
  never stored, never returned to the model.
- Only `` ```json `` blocks are the primary path. `` ```python ``,
  `` ```bash `` etc. are examples or tool instructions and are skipped.
- Exactly **one** block is allowed. Multiple blocks are ambiguous (which
  one is the payload?) and rejected.
- Trailing commas and other JSON deviations are rejected (strict JSON
  spec). The model learns to fix them via the retry loop.

.. rubric:: References

- ``rules/envelope-contract.md`` — the two-key contract specification.
- ``references/evidence.md`` — §5.4: envelope parsing accuracy per model.
"""

from __future__ import annotations

import json
import re

from .errors import ContractError

# Regex to find fenced code blocks: ``language\\n...\\n``.
# DOTALL makes ``.`` match newlines, so ``.*?`` spans the entire block
# body including line breaks.
_FENCE = re.compile(
    r"```[ \t]*([A-Za-z0-9_+-]*)[ \t]*\r?\n(.*?)```", re.DOTALL
)

# The only two keys the model is allowed to emit at the top level.
# Frozen so it can be used in set operations without accidental mutation.
_REQUIRED_KEYS = frozenset({"state_patch", "action"})


def parse_response(text):
    """Extract the ``(state_patch, action)`` pair from a raw model response.

    This is the first step in the ``parse → validate → merge`` pipeline.
    It handles the full variety of model output formats (fenced JSON,
    bare JSON, reasoning-then-JSON) and returns a canonicalised
    ``(patch, action)`` tuple or raises an actionable error.

    Args:
        text:
            The raw model output string — free-form text that may
            include reasoning, markdown formatting, and one or more
            fenced code blocks.

    Returns:
        ``(patch: dict, action: str)`` — the validated two-key payload.
        ``patch`` is always a ``dict``; ``action`` is always a ``str``.

    Raises:
        ContractError:
            If the response doesn't contain exactly one valid two-key
            JSON object. The error message is designed to be fed back
            to the model as the next observation for self-correction.

    .. rubric:: Parsing Order (deterministic)

    1.  Find all `` ``` ``-fenced blocks with a JSON language tag.
    2.  Attempt to parse each as JSON; collect dict results.
    3.  If exactly one dict has the right keys → accept it.
    4.  Fallback: if the whole response text is itself a two-key JSON
        object → accept it (host without tool-use, plain JSON).
    5.  Otherwise → detailed ``ContractError``.
    """
    if not isinstance(text, str):
        raise ContractError("response must be text")

    # -- Phase 1: scan for fenced JSON blocks --
    # Collect successfully-parsed **dict** objects from the response.
    # Non-dict JSON (arrays, strings, numbers) in fenced blocks are
    # ignored — they're examples or debug output, not payloads.
    candidates = []       # list[dict] — each is a candidate payload
    malformed = []        # list[str] — parse error messages for reporting

    for lang, body in _FENCE.findall(text):
        # Skip non-JSON fences: ``python``, ``bash``, etc. are examples
        # or tool calls — they are not the payload envelope.
        if lang and lang.lower() not in ("json", "json5", "jsonc"):
            continue

        body = body.strip()

        # Skip blocks that don't open with ``{`` — arrays, strings, or
        # numbers are not valid envelope objects.
        if not body.startswith("{"):
            continue

        try:
            obj = json.loads(body)
        except json.JSONDecodeError as exc:
            # Remember the parse error but keep scanning — the model
            # might have multiple blocks and only one is the intended
            # payload. We'll use this error message if *nothing* parses.
            malformed.append(str(exc))
            continue

        if isinstance(obj, dict):
            candidates.append(obj)

    # -- Phase 2: fallback — whole response is a bare two-key JSON --
    # This handles hosts (e.g. some API-only callers) that strip
    # markdown fences, and models that emit plain JSON without any
    # markdown wrapping. Only accept if the keys are exactly right
    # (to avoid accepting arbitrary JSON replies).
    if not candidates:
        stripped = text.strip()
        if stripped.startswith("{") and stripped.endswith("}"):
            try:
                obj = json.loads(stripped)
            except json.JSONDecodeError:
                obj = None
            if isinstance(obj, dict) and set(obj.keys()) == _REQUIRED_KEYS:
                candidates.append(obj)

    # -- Phase 3: reject with actionable feedback --
    if not candidates:
        if malformed:
            # The model produced a JSON-*looking* block but it didn't
            # parse. Tell it exactly what was wrong (trailing comma,
            # unquoted key, etc.) so it can fix the syntax.
            raise ContractError(
                "the JSON block could not be parsed (%s). Re-emit a "
                "single ```json block containing exactly "
                '{"state_patch": {...}, "action": "..."} with no '
                "trailing commas and all keys quoted."
                % malformed[0]
            )
        # No block was found at all — the model forgot to emit its
        # envelope. Tell it exactly what format to use.
        raise ContractError(
            "no JSON block found. Respond with reasoning followed by "
            "exactly one ```json fenced block containing exactly "
            '{"state_patch": {...}, "action": "..."}.'
        )

    # Multiple fenced JSON dicts → ambiguous. The model might have
    # shown an example and then a real response. Reject and ask for
    # a single block only.
    if len(candidates) > 1:
        raise ContractError(
            "found %d JSON blocks; exactly one is allowed. Emit a "
            "single block with your final patch and action."
            % len(candidates)
        )

    # -- Phase 4: validate the two-key contract --
    # The candidate dict must have *exactly* the two keys {state_patch,
    # action} — no more, no fewer. Extra keys are rejected because the
    # model might be trying to smuggle information past the envelope.
    obj = candidates[0]
    keys = set(obj)

    if keys != _REQUIRED_KEYS:
        extra = sorted(keys - _REQUIRED_KEYS)
        missing = sorted(_REQUIRED_KEYS - keys)
        parts = []
        if missing:
            parts.append("missing key(s): %s" % ", ".join(missing))
        if extra:
            parts.append("forbidden extra key(s): %s" % ", ".join(extra))
        raise ContractError(
            "the JSON block must have exactly the two keys state_patch "
            "and action -- %s." % "; ".join(parts)
        )

    # -- Phase 5: type-check the two values --
    # The envelope parsed and has the right keys, but the values might
    # be the wrong type (e.g. action is a number instead of a string).
    patch = obj["state_patch"]
    action = obj["action"]

    if not isinstance(patch, dict):
        raise ContractError(
            "state_patch must be a JSON object of the keys you are "
            "changing, got %s." % type(patch).__name__
        )
    if not isinstance(action, str):
        raise ContractError(
            "action must be a single command string, got %s."
            % type(action).__name__
        )

    return patch, action