#!/usr/bin/env bash
# shellcheck shell=bash
# Wrapper that runs skillstate.py through a project-local .venv if one
# exists, falling back to system python3 otherwise.  Use it anywhere:
#
#     ./scripts/skillstate.sh self-test
#     ./scripts/skillstate.sh merge --state state.json --patch '{"k":"v"}'
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
PY_SCRIPT="${SCRIPT_DIR}/skillstate.py"

_candidate_python() {
    local venv_py="${REPO_ROOT}/.venv/bin/python3"
    if [[ -x "$venv_py" ]]; then
        echo "$venv_py"
        return
    fi
    venv_py="${REPO_ROOT}/.venv/bin/python"
    if [[ -x "$venv_py" ]]; then
        echo "$venv_py"
        return
    fi
    echo "python3"
}

exec "$(_candidate_python)" "$PY_SCRIPT" "$@"
