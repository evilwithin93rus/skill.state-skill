#!/usr/bin/env python3
"""
State serialization utilities for SKILL.state.

The compact serialisation format used in every prompt. Compactness is
**not** cosmetic — every character costs tokens, and at horizon T=200
each byte saved compounds across 200 steps (and thus ~350 API calls
including retries).

.. rubric:: Design Notes

- ``separators=(",", ":")`` removes all whitespace from the JSON output.
  The saving is ~15 % vs ``json.dumps`` defaults for a typical 2 KB state.
- ``ensure_ascii=False`` allows Unicode characters to pass through
  natively. At the horizon scale this saves 6 bytes per non-ASCII char
  (``\\uXXXX`` → 1 code point).
- ``sort_keys`` is intentionally *not* used because key ordering can
  carry semantic information for the model and because the state is
  authored by a human who cares about readability during debugging.

.. rubric:: References

- ``references/evidence.md`` — §5.1: token savings from compact encoding.
"""

from __future__ import annotations

import json


def compact(state):
    """Serialize state into the compact prompt-ready JSON string.

    Args:
        state: The current ``Σ_t`` dict to serialise.

    Returns:
        A single-line JSON string with no unnecessary whitespace.
        Example: ``'{"inventory":{"shelf_42":"widget"},"cwd":"/tmp"}'``

    .. note::

       This is the **only** serialisation function used in prompts.
       ``json.dumps`` with default ``indent`` is never used because the
       indentation whitespace would consume ~15 % of the token budget at
       the expense of the reasoning budget.
    """
    return json.dumps(state, separators=(",", ":"), ensure_ascii=False)