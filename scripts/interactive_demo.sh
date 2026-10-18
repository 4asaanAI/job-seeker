#!/bin/zsh
# Real interactive Claude Code session for the recorded demo: fictional data, only the JobPilot and Fo
# MCP servers loaded (nothing private from your other connectors), default permission mode so the real
# permission prompt appears before Fo acts. YOU type the prompts (see docs/demo-video-script.md).
ROOT=${0:A:h:h}
PY=${JOBPILOT_PYTHON:-python3}
DIR=${DEMO_DIR:-${TMPDIR:-/tmp}/jobpilot-fo-demo}
mkdir -p "$DIR"
if [ -n "$REAL" ]; then   # your own local, git-ignored database and profile; nothing is seeded or committed
  DB="$ROOT/data/jobpilot.db"; OUT="$ROOT/out"; SEEKER=${SEEKER_ID:-1}
else
  $PY "$ROOT/scripts/fo_demo.py" --dir "$DIR" > /dev/null || exit 1
  DB="$DIR/jobpilot.db"; OUT="$DIR/out"; SEEKER=1
fi
CFG="$DIR/mcp.json"
cat > "$CFG" <<JSON
{"mcpServers":{
 "jobpilot":{"command":"$PY","args":["-m","jobpilot.server"],"env":{"PYTHONPATH":"$ROOT/src","JOBPILOT_HOME":"$ROOT","JOBPILOT_DB":"$DB","JOBPILOT_OUT":"$OUT","JOBPILOT_SEEKER_ID":"$SEEKER"}},
 "fo":{"type":"http","url":"https://mcp.api.wajo.ai/fo"}}}
JSON
# Run from a neutral folder: a project's own .claude/settings.local.json could already allow Fo tools,
# which would hide the permission prompt. Prompt suggestions are off so nothing is auto-submitted.
WORK=${WORKDIR:-$HOME/jobpilot-demo}; mkdir -p "$WORK"; cd "$WORK"
clear
exec claude --settings '{"promptSuggestionEnabled":false}' --permission-mode default --allowedTools "mcp__jobpilot__*" --strict-mcp-config --mcp-config "$CFG" --disable-slash-commands \
  --append-system-prompt "The user is seeker $SEEKER in JobPilot, and Fo is connected. To text the user their matches: call JobPilot build_fo_message, then pass its message to Fo ask_fo EXACTLY unchanged with newConversation true (never add or rewrite text), poll get_fo_reply until complete, then call record_notification with channel fo for each record_after item. To apply to a job ONLY after the user says yes for that job: call build_fo_apply_message and pass its message to Fo ask_fo EXACTLY unchanged; after Fo reports it submitted, call record_application with status applied. Never create a calendar event yourself or ask Fo to: a calendar entry is made only when the company itself sends an invitation (use build_fo_reply_check_message to look for it). Be brief."
