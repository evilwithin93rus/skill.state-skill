#!/usr/bin/env python3
"""
The merge operator — :math:`\\Sigma_{t+1} = \\Sigma_t \\oplus \\Delta\\Sigma_t`.

This is the **⊕** (deep-merge) operator that applies a validated patch
to the state. It is the **only** way state changes — no direct writes,
no side effects.

.. rubric:: Rules (in evaluation order)

+---------------------+---------------------------------------------------+
| Condition           | Behaviour                                         |
+=====================+===================================================+
| ``key: null``       | Delete that key from the result (deep-path).      |
+---------------------+---------------------------------------------------+
| ``key`` absent      | Untouched — the original state key survives.      |
+---------------------+---------------------------------------------------+
| ``object × object`` | Recursive deep merge (both sides are ``dict``).   |
+---------------------+---------------------------------------------------+
| leaf value          | Overwritten (scalar replacement).                 |
+---------------------+---------------------------------------------------+
| type mismatch       | ``ValidationError`` (can't merge dict into scalar)|
+---------------------+---------------------------------------------------+

.. rubric:: Safety Guarantee

State is **deep-copied** before any mutation begins. If ``merge()``
raises part-way through, the caller's original ``state`` dict is
untouched. This makes rollback-retry safe (Algorithm 1 lines 8-9):
on any error, re-prompt with the rejection message and the same
unmodified state.

Call ``validate_patch()`` **before** ``merge()`` to guarantee
schema-level compatibility. ``merge()`` still checks structural
coherence (e.g. patch tries to merge an object into a scalar leaf)
because that can happen even with a valid schema if the state shape
drifted from what the model expects.

.. rubric:: References

- ``references/merge-semantics.md`` — formal semantics, edge cases.
- ``rules/validation-and-rollback.md`` — how merge fits into the retry loop.
"""

from __future__ import annotations

from copy import deepcopy

from .errors import ValidationError, _typename


def merge(state, patch):
    """Apply a patch to state via deep merge with null-deletion.

    This is the **⊕** operator: ``Σ_{t+1} = merge(Σ_t, ΔΣ_t)``.

    Args:
        state:
            The current ``Σ_t`` dict. **NOT mutated** — a deep copy is
            made internally so the caller's reference is safe.
        patch:
            The ``ΔΣ_t`` dict from the model's ``state_patch`` field.
            Keys present override state keys; keys set to ``None`` are
            deleted from the result; keys absent leave state unchanged.

    Returns:
        ``dict`` — a new dict representing ``Σ_{t+1}``. The caller takes
        ownership of the returned dict.

    Raises:
        ValidationError:
            If either argument is not a ``dict``, or if the patch tries
            to merge an object into a scalar leaf (or vice versa) at
            the same dotted path.

    .. note::

       ``merge({"a": 1, "b": 2}, {"b": None})`` returns ``{"a": 1}``.
       This is null-deletion: ``None`` in the patch is *not* a JSON
       value assignment — it means "remove this key from the state".
    """
    if not isinstance(state, dict):
        raise ValidationError(
            "state must be an object, got %s" % _typename(state)
        )
    if not isinstance(patch, dict):
        raise ValidationError(
            "patch must be an object, got %s" % _typename(patch)
        )

    # Deep copy *before* any mutation.
    # If _merge raises below, the caller's `state` is still intact
    # and can be used for rollback-retry.
    return _merge(deepcopy(state), patch, "$")


def _merge(result, patch, path):
    """Recursive merge helper — mutates ``result`` in place.

    ``result`` is already a deep copy of the original state, so in-place
    mutation is safe (no external references).

    Args:
        result:
            The working copy of the state (mutated in place).
        patch:
            The sub-patch dict being applied at this level.
        path:
            Dotted path for error messages. ``"$"`` at the root,
            ``"inventory"`` one level down,
            ``"inventory.shelf_42"`` two levels down, etc.

    Returns:
        The same ``result`` dict (mutated), for recursive chaining.
    """
    for key, value in patch.items():
        # Build the dotted path for this sub-key.
        # At the root (path == "$"), just use the key itself to avoid
        # prefixing every path with "$." in error messages.
        sub = "%s.%s" % (path, key) if path != "$" else key

        # -- Rule 1: null = delete --
        # Remove the key entirely from the result. ``pop(key, None)``
        # is safe: if the key was already absent (model tried to delete
        # something that doesn't exist) — no error, just a no-op.
        if value is None:
            result.pop(key, None)
            continue

        # -- Rule 2: key absent in state → insert --
        # The state has no entry for this key, so just insert it.
        # deepcopy ensures the new value doesn't share references
        # with the patch dict (the patch may be reused for retries).
        if key not in result:
            result[key] = deepcopy(value)
            continue

        # -- Rules 3-5: both sides have a value for this key --
        if isinstance(result[key], dict) and isinstance(value, dict):
            # Rule 3: both are dicts → recurse into the subtree
            result[key] = _merge(result[key], value, sub)

        elif isinstance(result[key], dict) != isinstance(value, dict):
            # Rule 5: type mismatch — one side is a dict, the other
            # is a scalar (or list). Cannot merge structurally different
            # types; the model must be told to fix its patch shape.
            raise ValidationError(
                "type mismatch at %s: state holds %s, patch holds %s. "
                "Cannot coerce."
                % (sub, _typename(result[key]), _typename(value))
            )

        else:
            # Rule 4: both are scalars (or lists) → overwrite.
            # Overwrite-with-deepcopy ensures the state doesn't hold
            # a reference to the patch dict's inner structure.
            result[key] = deepcopy(value)

    return result