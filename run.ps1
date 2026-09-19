# JobPilot entry point for the Windows desktop. Keep this file ASCII-only.
param(
    [ValidateSet("test", "smoke", "capture", "serve")]
    [string]$Task = "smoke"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Definition
$env:JOBPILOT_HOME = $Root
$env:GCM_INTERACTIVE = "Never"
$Py = Join-Path $Root ".venv\Scripts\python.exe"

if (-not (Test-Path $Py)) {
    Write-Error "venv missing. Create it with the pinned Python 3.12 first."
    exit 1
}

switch ($Task) {
    "test"    { & $Py -m pytest -q }
    "smoke"   { & $Py (Join-Path $Root "scripts\smoke.py") --yes }
    "capture" { & $Py (Join-Path $Root "scripts\capture_fixture.py") }
    "serve"   { & $Py -m jobpilot.server }
}
exit $LASTEXITCODE
