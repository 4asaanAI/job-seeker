# JobPilot Telegram bot launcher. Keep this file ASCII-only.
# Runs the open Telegram bot and restarts it if it ever exits.
$ErrorActionPreference = "Continue"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Definition)
$env:JOBPILOT_HOME = $Root
$env:GCM_INTERACTIVE = "Never"
$Python = Join-Path $Root ".venv\Scripts\python.exe"
$Log = Join-Path $Root "telegram-state\bot.log"
New-Item -ItemType Directory -Force (Join-Path $Root "telegram-state") | Out-Null
while ($true) {
    & $Python -m jobpilot.telegram_bot *>> $Log
    Start-Sleep -Seconds 5
}
