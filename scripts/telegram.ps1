# JobPilot Telegram channel launcher. Keep this file ASCII-only.
param(
    [int]$SeekerId = 1
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Definition
$env:JOBPILOT_HOME = $Root
$env:JOBPILOT_SEEKER_ID = "$SeekerId"
$env:TELEGRAM_STATE_DIR = Join-Path $Root "telegram-state"
$env:GCM_INTERACTIVE = "Never"
$Mcp = Join-Path $Root ".mcp.telegram.json"

if (-not (Test-Path $Mcp)) {
    Write-Error "Missing .mcp.telegram.json. Copy .mcp.telegram.json.example and review it."
    exit 1
}

# Only the pinned JobPilot MCP and the Telegram channel are exposed. Built-in
# Bash/file tools and unrelated user MCP servers are disabled for this process.
& claude `
    --channels "plugin:telegram@claude-plugins-official" `
    --strict-mcp-config `
    --mcp-config $Mcp `
    --tools "" `
    --allowedTools "mcp__jobpilot__*" "mcp__telegram__reply" "mcp__telegram__react" "mcp__telegram__edit_message" `
    --add-dir $Root
exit $LASTEXITCODE
