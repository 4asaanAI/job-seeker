# JobPilot Telegram bot watchdog. Keep this file ASCII-only.
# Runs every few minutes via the JobPilotTelegramWatchdog scheduled task.
# scripts/telegram.ps1 already restarts the bot process if it exits on its
# own; this catches the case where the whole process tree (and that inner
# restart loop with it) disappeared instead -- an unattended reboot, the
# interactive session being torn down, or anyone stopping it by hand and
# forgetting to start it again. It relaunches the same JobPilotTelegram task,
# so it needs no separate credentials or launch logic of its own.
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Definition)
$Log = Join-Path $Root "telegram-state\watchdog.log"
# Moved to the AWS host (layaa-shared-search) on 2026-09-25. While this marker
# exists the Windows copy stays off, so two bots never poll one token.
# Delete telegram-state\MOVED_TO_AWS to run the bot on this PC again.
if (Test-Path (Join-Path $Root "telegram-state\MOVED_TO_AWS")) { exit 0 }
New-Item -ItemType Directory -Force (Join-Path $Root "telegram-state") | Out-Null

$alive = Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
    Where-Object { $_.CommandLine -like '*jobpilot.telegram_bot*' }

if (-not $alive) {
    $stamp = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
    Add-Content -Path $Log -Value "$stamp bot not running; restarting JobPilotTelegram"
    schtasks /run /tn JobPilotTelegram | Out-Null
}
