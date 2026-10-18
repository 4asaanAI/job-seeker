#!/bin/zsh
# Live demo runner: REAL Claude calls against the REAL JobPilot server and the REAL Fo MCP,
# on fictional data. Used for the recorded demo. Needs a JobPilot+Fo MCP config (LIVE_MCP) and
# a seeded demo folder (python scripts/fo_demo.py --dir /tmp/live-demo).
# Headless `claude -p` cannot show a permission prompt, so the Fo tools are pre-approved for this
# run only. In normal interactive use Claude Code asks first.
CFG=${LIVE_MCP:-/tmp/live-mcp.json}
[ -f "$LIVE_ENV" ] && source "$LIVE_ENV" && rm -f "$LIVE_ENV"   # key stays off screen
SYS="You are Claude, connected to JobPilot (the user is seeker 1) and to Fo. Use the tools. Be brief, no preamble."

typed() { printf '\033[1;34m> \033[0m\033[1m'; for ((i=1;i<=${#1};i++)); do printf '%s' "${1[i]}"; sleep 0.035; done; printf '\033[0m\n'; }
run() {  # $1 = prompt shown and sent, $2 = allowed tools, $3 = extra system text
  typed "$1"; sleep 0.6
  printf '\033[2m  (Claude is working...)\033[0m\n'
  claude -p --no-session-persistence --model haiku --strict-mcp-config --mcp-config "$CFG" \
    --tools "" --disable-slash-commands --append-system-prompt "$SYS $3" \
    --allowedTools "$2" --max-budget-usd 0.5 "$1" < /dev/null 2>&1 | grep -v "claude.ai connectors"
  echo; sleep 2
}

clear
printf '\033[1;33mJobPilot x Fo\033[0m  live run, fictional data, real tools\n\n'
sleep 3
run "What are my top three matches?" "mcp__jobpilot__my_matches"
run "Prepare my application for the Fabrikam role, and show me what you would send." \
    "mcp__jobpilot__my_matches,mcp__jobpilot__prepare_application"
printf '\033[2m  (headless run: Fo is pre-approved for this recording; interactive Claude Code asks first)\033[0m\n'
run "Looks good. Send me my new matches through Fo." \
    "mcp__jobpilot__build_fo_message,mcp__jobpilot__record_notification,mcp__fo__ask_fo,mcp__fo__get_fo_reply" \
    "To text the user their matches: call JobPilot build_fo_message, pass its message to Fo ask_fo EXACTLY unchanged with newConversation true, poll get_fo_reply until complete, then call record_notification with channel fo for each record_after item. Never alter or add any text to that message. Finish by saying whether Fo completed."
run "I applied to the Fabrikam role. Record it and put a follow-up on my calendar three days from now." \
    "mcp__jobpilot__my_matches,mcp__jobpilot__record_application,mcp__fo__ask_fo,mcp__fo__get_fo_reply" \
    "Record the application as applied, then ask Fo to add a 15 minute calendar event titled 'JobPilot follow-up: Fabrikam Commerce' three days from today at 10:00 local time, no guests, no invites. Poll get_fo_reply until complete. Say what Fo created."
printf '\033[1;32mDone.\033[0m  Live run: real Claude, real JobPilot server, real Fo. Fictional data.\n'
sleep 4
