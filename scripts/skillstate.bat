@echo off
REM Wrapper that runs skillstate.py through a project-local .venv if one
REM exists, falling back to system python otherwise.
REM
REM     scripts\skillstate self-test
REM     scripts\skillstate merge --state state.json --patch "{\"k\":\"v\"}"

set "SCRIPT_DIR=%~dp0"
set "REPO_ROOT=%SCRIPT_DIR%.."
set "PY_SCRIPT=%SCRIPT_DIR%skillstate.py"

if exist "%REPO_ROOT%\.venv\Scripts\python.exe" (
    set "PYTHON=%REPO_ROOT%\.venv\Scripts\python.exe"
) else (
    set "PYTHON=python"
)

"%PYTHON%" "%PY_SCRIPT%" %*