#!/usr/bin/env python3
"""
Schema linting — detect horizon-unbounded fields.

Not all schemas are safe for long-horizon execution. A ``list`` field
grows by one entry per step → O(T) characters in Σ → O(T²) cumulative
tokens across the horizon. An ``any`` field has no type constraint →
the model can inject arbitrary structures that cause silent failures
later (20 % of open-weight model errors in the paper, §5.3).

This linter is a **static analyser** for schemas — it runs before any
execution begins and reports warnings. It does not validate data; use
``validate_patch`` for that.

.. rubric:: Bounded-Schema Preferences

- **Keyed maps** (``{"*": "str?"}``) over lists: entries overwrite by
  identity, so ``|Σ|`` stays O(1) in T for a fixed key space.
- **Concrete types** (``"str"``) over ``"any"``: gives the validator
  teeth; the model cannot sneak a dictionary into a string field.

.. rubric:: References

- ``rules/state-boundedness.md`` — why boundedness matters.
- ``references/evidence.md`` — §5.3: type coercion failure rates.
"""

from __future__ import annotations


# =============================================================================
# PUBLIC API
# =============================================================================


def lint_schema(schema, path="$"):
    """Report schema fields that can grow unbounded with the execution horizon.

    Checks performed:

    1.  **list / list?** — entries are appended, never overwritten by
        identity. Each step adds one entry → state grows O(T).
        Recommendation: use a keyed map (``{"*": "str?"}``) so repeats
        overwrite by identity and Σ stays bounded.

    2.  **any / any?** — no type constraint; the model can inject
        unexpected structures (e.g. a dict where a string is expected).
        Recommendation: declare a concrete type (``"str"``, ``"int"``, etc.).

    Args:
        schema: The schema dict to lint (the same format accepted by
            ``validate_patch``).
        path: Internal — current dotted path during recursion. Callers
            should omit this; it defaults to ``"$"`` (the root).

    Returns:
        ``list[str]`` — human-readable warning strings, one per finding.
        An empty list means the schema contains no detected growth-risk
        fields (it is "bounded" with respect to the horizon).

    .. note::

       This is a conservative linter: a ``list`` field *may* be bounded
       by an external constraint (e.g. a known-short gallery). The
       linter flags it anyway so you can decide. False positives are
       better than O(T²) token budgets discovered at runtime.
    """
    findings = []

    if isinstance(schema, dict):
        for key, spec in schema.items():
            # Build the dotted path: "inventory" or "inventory.shelf_42"
            sub = "%s.%s" % (path, key) if path != "$" else key

            if isinstance(spec, dict):
                # Nested object — recurse to check its fields
                findings.extend(lint_schema(spec, sub))

            elif spec in ("list", "list?"):
                # Unbounded list: each step appends one entry.
                # Consider replacing with a keyed map ({"*": "str?"})
                # so that entries with the same identity key overwrite
                # instead of stacking.
                findings.append(
                    "%s is a list: bounded by the environment, or does it "
                    "grow one entry per step? Prefer a keyed map so repeats "
                    "overwrite." % sub
                )

            elif spec in ("any", "any?"):
                # Untyped field: the validator cannot protect you.
                # 20 % of open-weight model failures are type-coercion
                # errors. Declare a concrete type.
                findings.append(
                    "%s is untyped: type coercion is 20%% of open-weight "
                    "failures; declare a concrete type." % sub
                )

    return findings