# Wrapper that runs skillstate.py through a project-local .venv if one
# exists, falling back to system python otherwise.
#
#     ./scripts/skillstate self-test
#     ./scripts/skillstate merge --state state.json --patch '{...}'

param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$RemainingArgs
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent $ScriptDir
$PyScript = Join-Path $ScriptDir "skillstate.py"

$venvPython = Join-Path $RepoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    $venvPython = "python"
}

& $venvPython $PyScript @RemainingArgs
exit $LASTEXITCODE