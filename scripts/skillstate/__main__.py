#!/usr/bin/env python3
"""
Entry point for ``python -m scripts.skillstate``.

This file exists so that the package is directly executable as a
module. It delegates to ``skillstate.cli.main()`` — the same function
that the standalone ``skillstate.py`` script calls.

Usage::

    python3 -m scripts.skillstate self-test
    python3 -m scripts.skillstate merge --state s.json --patch p.json

.. rubric:: See Also

- ``scripts/skillstate.py`` — standalone script (no ``-m`` needed).
- ``skillstate.cli`` — the CLI logic and subcommand dispatch.
"""

from .cli import main

if __name__ == "__main__":
    import sys
    sys.exit(main())