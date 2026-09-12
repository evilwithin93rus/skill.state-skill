"""
SKILL.state reference implementation — the deterministic half of the runtime.

The model proposes; this module decides. Three load-bearing pieces the
runtime **must** own so that a malformed or hostile model response can
never corrupt persistent state:

1.  ``parse_response()`` — strict two-key fenced-JSON envelope extraction.
2.  ``validate_patch()`` — schema check: keys, types, nullability,
    write-permission.
3.  ``merge()`` — the *⊕* operator: deep merge, null-deletion, atomic.

Together they implement the **envelope contract**: the model gets an
imperative state for free but must route every modification through
``parse → validate → merge``. Any deviation raises a ``SkillStateError``
whose message is designed to be fed back verbatim as the next observation
(rollback-retry, Algorithm 1 line 9).

.. rubric:: Public API

.. autosummary::

    parse_response
    validate_patch
    merge
    apply_step
    compact
    lint_schema
    build_host_argv
    exec_once
    extract_host_response
    run_loop

.. rubric:: Reference

*Badhe, Tiwari & Chung,* "SKILL.state: Scalable Long-Horizon Agent Skills"
(arXiv:2608.26263, EMNLP 2026).

Stdlib only. Python 3.8+.
"""

from .errors import ContractError, SkillStateError, ValidationError
from .hosts import HOSTS, build_host_argv, exec_once, extract_host_response
from .lint import lint_schema
from .loop import run_loop
from .mcp import MCP_INNER_HOSTS, McpServer, dispatch_mcp, serve_mcp
from .merge import merge
from .parse import parse_response
from .serialization import compact
from .step import Runtime, apply_step
from .validate import validate_patch

__all__ = [
    "SkillStateError",
    "ContractError",
    "ValidationError",
    "merge",
    "validate_patch",
    "parse_response",
    "apply_step",
    "compact",
    "lint_schema",
    "Runtime",
    "build_host_argv",
    "extract_host_response",
    "exec_once",
    "run_loop",
    "HOSTS",
    "MCP_INNER_HOSTS",
    "McpServer",
    "dispatch_mcp",
    "serve_mcp",
]