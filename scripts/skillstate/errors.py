#!/usr/bin/env python3
"""
Error hierarchy for the SKILL.state deterministic runtime.

Every exception message is written to be fed back to the model verbatim
as the next observation (rollback-retry). Messages name the specific
violation and the required correction — the model repairs its own output
on the next attempt without human intervention.

.. rubric:: Exception Tree

.. code-block:: text

    Exception
     └── SkillStateError        (base — safe to surface to model)
          ├── ContractError      (envelope malformed: bad JSON, wrong keys)
          └── ValidationError    (patch violates schema or runtime rules)

.. rubric:: Design Constraint

The error message string **is** the retry prompt. It must:

1.  Name the exact field(s) involved.
2.  State what was wrong.
3.  State what to do instead.

If a message is truncated or opaque the model will retry with the same
malformed output and exhaust the retry budget.

.. rubric:: References

- ``rules/validation-and-rollback.md`` — full specification of the
  retry contract.
- ``references/evidence.md`` — §5.2: self-repair success rate with
  different error message styles.
"""


class SkillStateError(Exception):
    """Base class for all deterministic-runtime errors.

    Any instance is safe to surface to the model as feedback text.
    The message always includes (a) what went wrong and (b) how to fix it.

    Subclasses add no additional fields — the message string carries
    all the information the model needs for self-correction.
    """


class ContractError(SkillStateError):
    """The response envelope is malformed.

    Causes include:

    - No JSON block found in the response.
    - Wrong keys in the top-level JSON object (anything other than the
      exact two-key set ``{state_patch, action}``).
    - Trailing commas, unquoted keys, or other JSON syntax errors.
    - Multiple fenced blocks (ambiguous — which one is the payload?).
    - Non-dict ``state_patch`` or non-string ``action``.

    The model receives the exact rejection message and retries with a
    corrected envelope on the next attempt.
    """


class ValidationError(SkillStateError):
    """The envelope parsed correctly, but the patch violates the schema.

    Causes include:

    - Writing an unknown key not declared in the schema.
    - Deleting (setting to null) a non-nullable field.
    - Type mismatch: e.g. putting a string where an int is expected.
    - Writing a runtime-owned field (the runtime manages those, not the model).
    - Action not in the declared action space (when ``action_space`` is set).

    Like ``ContractError``, the message is fed back to the model for
    self-correction.
    """


def _typename(value):
    """Return a human-readable type name for a Python value.

    Used exclusively in error messages so that the model sees names it
    understands (e.g. "object" not "dict", "str" not "str").

    Args:
        value: Any Python value.

    Returns:
        One of ``"null"``, ``"bool"``, ``"int"``, ``"float"``, ``"str"``,
        ``"list"``, ``"object"``, or the ``type(value).__name__`` for
        unrecognised types.
    """
    if value is None:
        return "null"
    # Ordered lookup: ``bool`` must come before ``int`` because
    # ``isinstance(True, int)`` is ``True`` in Python.
    return {
        bool: "bool",
        int: "int",
        float: "float",
        str: "str",
        list: "list",
        dict: "object",
    }.get(type(value), type(value).__name__)