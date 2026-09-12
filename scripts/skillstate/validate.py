#!/usr/bin/env python3
"""
Schema validation — ``validate_patch()``.

The guard that runs **between** parsing and merging. It ensures the
patch only touches declared keys, uses correct types, respects
nullability rules, and does not write into runtime-owned territory.

.. rubric:: Schema Format (JSON-Native)

The schema language is JSON-native so an agent can author it as a plain
file without learning a DSL:

+-----------------------------+---------------------------------------------------+
| Form                        | Meaning                                           |
+=============================+===================================================+
| ``"str"`` | ``"int"`` | ... | Leaf type — value must be an instance.            |
+-----------------------------+---------------------------------------------------+
| ``"str?"`` | ``"int?"`` | ...| Nullable leaf — model may set to ``null`` to       |
|                             | delete it.                                        |
+-----------------------------+---------------------------------------------------+
| ``["pass", "fail"]``        | Enum — value must be one of the listed literals.  |
+-----------------------------+---------------------------------------------------+
| ``{"a": ..., "b": ...}``    | Closed object — only the named keys are allowed;   |
|                             | unknown keys are rejected.                        |
+-----------------------------+---------------------------------------------------+
| ``{"*": ...}``              | Open map — any string key is allowed (e.g.         |
|                             | inventory shelves); all values must conform to     |
|                             | the inner schema. Entries are **always** deletable.|
+-----------------------------+---------------------------------------------------+

.. rubric:: Important Distinction: Deletability

- **Open-map entries** (``{"*": ...}``) are **always** deletable. That
  is the whole point of null-deletion for keyed maps — you clear a
  shelf by deleting its entry from the inventory.
- **Closed-object entries** are deletable **only** if the type spec
  ends in ``"?"`` (explicit opt-in to null).
- **Non-nullable closed-object keys** can **never** be set to ``null``.
  The model must omit the key from the patch to leave it unchanged.

.. rubric:: References

- ``rules/schema-authoring.md`` — how to write a bounded schema.
- ``rules/validation-and-rollback.md`` — where validate fits in the pipeline.
"""

from __future__ import annotations

import json

from .errors import ValidationError, _typename

# Mapping from schema type-name strings to Python ``isinstance`` targets.
#
# ``"num"`` accepts both ``int`` and ``float`` (the schema author wants
# "a number" without caring which).
#
# ``"any"`` uses ``None`` as a sentinel — ``_check_leaf`` treats
# ``expected is None`` as "accept everything" (except ``null`` for
# non-nullable fields).
_LEAF_TYPES = {
    "str": str,
    "int": int,
    "float": float,
    "num": (int, float),   # numeric: both int and float pass
    "bool": bool,
    "list": list,
    "any": None,           # sentinel — anything passes (null caught separately)
}


def _is_open_map(node):
    """Check whether a schema node is an open map (has the wildcard ``*`` key).

    Args:
        node: A schema node (``dict`` or leaf spec).

    Returns:
        ``True`` if ``node`` is a ``dict`` and contains the ``"*"`` key,
        ``False`` otherwise.
    """
    return isinstance(node, dict) and "*" in node


def _check_leaf(spec, value, path):
    """Validate a single leaf value against its schema spec.

    Args:
        spec:
            A type string (``"str"``, ``"int?"``, ...) or an enum list
            (``["pass", "fail"]``).
        value:
            The actual value from the patch. May be ``None`` for deletion.
        path:
            Dotted path for error messages (e.g. ``"inventory.shelf_42"``).

    Raises:
        ValidationError: If the value doesn't match the spec. The message
            always includes (a) the field path, (b) what was expected,
            and (c) what was received.
    """
    # -- Case 1: enum --
    # The spec is a list of allowed string literals.
    # ``value not in spec`` works for both strings and None — None is
    # never in an enum list.
    if isinstance(spec, list):
        if value not in spec:
            raise ValidationError(
                "%s must be one of %s, got %s."
                % (path, json.dumps(spec), json.dumps(value))
            )
        return

    if not isinstance(spec, str):
        raise ValidationError("schema at %s is not a valid type spec" % path)

    # Parse the nullable suffix: ``"str?"`` → base=``"str"``, nullable=True
    nullable = spec.endswith("?")
    base = spec[:-1] if nullable else spec

    if base not in _LEAF_TYPES:
        raise ValidationError(
            "schema at %s declares unknown type %r" % (path, base)
        )

    # -- Null check --
    # Only nullable types accept ``None``. If the field is non-nullable
    # and the patch sets it to null, reject with a message telling the
    # model to omit the key instead.
    if value is None:
        if not nullable:
            raise ValidationError(
                "%s is declared %s and is not nullable; it cannot be "
                "deleted. Omit the key to leave it unchanged."
                % (path, base)
            )
        return  # null is valid for nullable types → merge will delete

    # -- Type check --
    # ``expected is None`` means "any" type — accept everything.
    # (We already passed the null check above, so non-None "any" passes.)
    expected = _LEAF_TYPES[base]
    if expected is None:
        return

    # **Python gotcha**: ``bool`` is a subclass of ``int``.
    # ``isinstance(True, int)`` is ``True`` in Python.
    # A model writing ``true`` when the schema says ``"int"`` must be
    # caught — otherwise bools silently become 0/1 and bugs persist
    # for many steps before anyone notices.
    if base in ("int", "num", "float") and isinstance(value, bool):
        raise ValidationError(
            "%s must be %s, got bool." % (path, base)
        )

    if not isinstance(value, expected):
        raise ValidationError(
            "%s must be %s, got %s. Do not change the shape of a field."
            % (path, base, _typename(value))
        )


def validate_patch(patch, schema, runtime_owned=(), _path="$"):
    """Check a patch against the schema **before** merging.

    This is the guard in the middle of the ``parse → validate → merge``
    pipeline. It ensures the model's patch obeys every declared
    constraint. If this passes, ``merge()`` can proceed without
    schema-level surprises (merge still checks structural type
    mismatches like object-vs-scalar since the state shape may have
    drifted).

    Args:
        patch:
            The ``state_patch`` dict from ``parse_response()``.
        schema:
            The JSON-native schema dict (see module docstring for format).
        runtime_owned:
            Iterable of dotted paths the model may **never** write
            (e.g. ``"policy_checks"``, ``"user_id"``). These are fields
            managed exclusively by the runtime, not the skill.
        _path:
            Internal — the current dotted path during recursion.
            Callers should omit this; it defaults to ``"$"`` (the root).

    Returns:
        ``None`` — the patch is valid (the caller proceeds to merge).

    Raises:
        ValidationError:
            On the **first** violation found (fail-fast semantics).
            The model only needs to fix one error at a time because
            the error message is fed back as the next observation
            and the retry loop allows self-correction.
    """
    # Convert to a set for O(1) lookups during recursive descent.
    # Dotted paths examples:
    #   "policy_checks"            — top-level owned field
    #   "inventory.shelf_42.flag"  — deeply nested owned field
    owned = {p for p in runtime_owned}
    _validate(patch, schema, owned, _path, ())
    return None


def _validate(patch, schema, owned, path, trail):
    """Recursive schema validation — the workhorse.

    Args:
        patch:
            The sub-patch dict being validated at this level.
        schema:
            The sub-schema for this level (must be a ``dict`` if
            ``patch`` contains keys).
        owned:
            ``set[str]`` — dotted paths the model cannot write.
        path:
            Current dotted path for error messages (``"$"`` at root).
        trail:
            ``tuple[str, ...]`` — key names from root, used to build
            owned-path lookups (``".".join(trail + (key,))``).
    """
    # Patch at this level must be a dict — leaf fields can't have
    # sub-patches. (Model tried to do ``{"cwd": {"sub": ...}}`` when
    # ``cwd`` is a ``"str"`` leaf.)
    if not isinstance(patch, dict):
        raise ValidationError(
            "%s must be an object, got %s." % (path, _typename(patch))
        )
    if not isinstance(schema, dict):
        raise ValidationError(
            "%s is a leaf field in the schema; a patch cannot descend "
            "into it." % path
        )

    # Determine if this schema node is an open map.
    # Open maps allow any string key; closed objects only allow declared keys.
    open_map = _is_open_map(schema)

    for key, value in patch.items():
        # Top-level keys must be strings (JSON spec).
        # Non-string keys would break dotted-path formatting and
        # schema lookups, so reject early.
        if not isinstance(key, str):
            raise ValidationError(
                "%s has a non-string key %r." % (path, key)
            )

        # Build identifying paths for this key.
        # ``trail`` is used for the owned-field check (dotted string).
        # ``sub_path`` is used for human-readable error messages
        # and schema navigation.
        here = trail + (key,)
        dotted = ".".join(here)
        sub_path = "%s.%s" % (path, key) if path != "$" else key

        # -- Runtime-owned field check --
        # The runtime manages certain fields itself (e.g. it observes
        # them from the environment). The model must never write to
        # these — if it could, it could overwrite ground-truth data
        # with hallucinated values.
        if dotted in owned:
            raise ValidationError(
                "%s is runtime-owned and cannot be written by a patch. "
                "Observations may not change it." % sub_path
            )

        # Resolve the schema definition for this key.
        if open_map:
            # Open map: any key is valid; all use the same schema.
            spec = schema["*"]
            deletable = True   # open-map entries are always deletable
        elif key in schema:
            # Closed object: only declared keys allowed.
            spec = schema[key]
            # Deletable if the type-spec string ends with "?".
            deletable = isinstance(spec, str) and spec.endswith("?")
        else:
            # Key not in schema — the model tried to write a field
            # that was never declared. This is a common early-stage
            # error: the model invents a field it "needs."
            raise ValidationError(
                "%s is not in the schema. Allowed keys at %s: %s."
                % (sub_path, path, ", ".join(sorted(schema)) or "(none)")
            )

        # -- Null value = deletion request --
        if value is None:
            if isinstance(spec, dict):
                # Deleting a whole subtree (e.g. ``{"inventory": {"shelf_42":
                # null}}`` in an open map). This is legitimate — just
                # recurse to validate any sub-schema constraints.
                pass
            elif not deletable:
                # Non-nullable leaf being set to null — reject and tell
                # the model to omit the key instead.
                _check_leaf(spec, None, sub_path)
            continue

        # -- Non-null value: recurse or check leaf type --
        if isinstance(spec, dict):
            # Sub-object: recurse into it with the sub-schema.
            _validate(value, spec, owned, sub_path, here)
        else:
            # Leaf type: check the value matches the declared type.
            _check_leaf(spec, value, sub_path)