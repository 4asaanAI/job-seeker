#!/bin/bash
# One-time setup on the Job Pilot Mac so LayaaStat's Control Room can start and stop the dashboard.
# Run from the Job Pilot folder:   bash scripts/setup-control-room-mac.sh
# It installs the dashboard LaunchAgent, writes ~/.layaa/control-room.env (asks for the report token, never echoed),
# fetches LayaaStat's Mac checker and installs it. Safe to run again.
set -euo pipefail
JP="$(cd "$(dirname "$0")/.." && pwd)"
PY="$JP/.venv/bin/python"
[ -x "$PY" ] || { echo "No Job Pilot venv at $JP/.venv; set it up first (docs/GETTING-STARTED.md)."; exit 2; }
TS="$(command -v tailscale || echo /Applications/Tailscale.app/Contents/MacOS/Tailscale)"
IP="$("$TS" ip -4 2>/dev/null | head -1 || true)"
[ -n "$IP" ] || { echo "Tailscale is not running (tailscale ip -4 gave nothing)."; exit 2; }

# 1. the dashboard, started and stopped by launchd
mkdir -p "$HOME/Library/LaunchAgents" "$JP/data"
sed -e "s#/path/to/jobpilot#$JP#g" -e "s#100.x.y.z#$IP#" "$JP/scripts/com.jobpilot.dashboard.plist.example" \
  > "$HOME/Library/LaunchAgents/com.jobpilot.dashboard.plist"
[ -f "$HOME/.config/jobpilot/dashboard_token" ] || "$PY" -m jobpilot.dashboard --new-token
echo "Dashboard LaunchAgent written (http://$IP:8770/)."

# 2. the checker's settings
mkdir -p "$HOME/.layaa"
ENV="$HOME/.layaa/control-room.env"
if ! grep -q '^CONTROL_ROOM_REPORT_TOKEN=' "$ENV" 2>/dev/null; then
  read -r -s -p "Control Room report token (CONTROL_ROOM_REPORT_TOKEN, same as the other checkers): " TOKEN; echo
  [ ${#TOKEN} -ge 24 ] || { echo "That token is too short."; exit 2; }
  printf 'CONTROL_ROOM_URL=https://stat.layaa.ai\nCONTROL_ROOM_REPORT_TOKEN=%s\n' "$TOKEN" > "$ENV"
fi
grep -v '^JOBPILOT_DASHBOARD_URL=' "$ENV" > "$ENV.tmp" || true
echo "JOBPILOT_DASHBOARD_URL=http://$IP:8770/" >> "$ENV.tmp" && mv "$ENV.tmp" "$ENV" && chmod 600 "$ENV"

# 3. LayaaStat's Mac checker
SRC="$(mktemp -d)"
git clone -q --depth 1 https://github.com/4asaanAI/LayaaStat.git "$SRC/layaastat"
bash "$SRC/layaastat/scripts/control-room/install-mac-checker.sh" "$PY"
rm -rf "$SRC"
"$PY" "$HOME/.layaa/mac_checker.py" --once --dry-run
echo "Done. Open stat.layaa.ai > Control Room: 'The Mac' should turn green within a minute."
