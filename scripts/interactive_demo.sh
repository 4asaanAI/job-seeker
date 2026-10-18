#!/bin/zsh
# Real interactive Claude Code session for the recorded demo: fictional data, only the JobPilot and Fo
# MCP servers loaded (nothing private from your other connectors), default permission mode so the real
# permission prompt appears before Fo acts. YOU type the prompts (see docs/demo-video-script.md).
ROOT=${0:A:h:h}
PY=${JOBPILOT_PYTHON:-python3}
DIR=${DEMO_DIR:-${TMPDIR:-/tmp}/jobpilot-fo-demo}
$PY "$ROOT/scripts/fo_demo.py" --dir "$DIR" > /dev/null || exit 1
CFG="$DIR/mcp.json"
cat > "$CFG" <<JSON
{"mcpServers":{
 "jobpilot":{"command":"$PY","args":["-m","jobpilot.server"],"env":{"PYTHONPATH":"$ROOT/src","JOBPILOT_HOME":"$ROOT","JOBPILOT_DB":"$DIR/jobpilot.db","JOBPILOT_OUT":"$DIR/out","JOBPILOT_SEEKER_ID":"1"}},
 "fo":{"type":"http","url":"https://mcp.api.wajo.ai/fo"}}}
JSON
cd "$ROOT"
clear
exec claude --permission-mode default --strict-mcp-config --mcp-config "$CFG" --disable-slash-commands \
  --append-system-prompt "The user is seeker 1 in JobPilot, and Fo is connected. To text the user their matches: call JobPilot build_fo_message, then pass its message to Fo ask_fo EXACTLY unchanged with newConversation true (never add or rewrite text), poll get_fo_reply until complete, then call record_notification with channel fo for each record_after item. To record an application use record_application. Be brief."
