#!/usr/bin/env python3
"""
SKILL.state reference implementation entry point.

This is the thinnest possible entry — it adjusts ``sys.path`` so the
``skillstate`` package is importable regardless of the invocation
directory, then delegates to ``skillstate.cli.main()``.

The entire runtime lives in the ``skillstate/`` subdirectory as a
``__main__`` package. The only reason this ``skillstate.py`` file exists
is so that users can invoke:

.. code-block:: bash

    python3 scripts/skillstate.py self-test

without setting ``PYTHONPATH``. For ``python -m`` usage use
``python -m scripts.skillstate`` instead (it has its own
``__main__.py``).

.. rubric:: References

- ``skillstate.cli.main`` — the actual CLI entry point.
- ``AGENTS.md`` — convention for thin top-level files.
"""

import sys
import os

# -- Path setup --
# Insert the ``scripts/`` directory at the front of sys.path so that
# ``from skillstate.cli import main`` resolves the package regardless of
# whether the caller is executing from the repo root or from
# ``scripts/`` directly.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from skillstate.cli import main

if __name__ == "__main__":
    sys.exit(main())