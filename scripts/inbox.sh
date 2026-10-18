#!/usr/bin/env bash
# One poll of the text-Fo inbox: answers any new "jp ..." texts (matches, status, tailor, apply, yes, sync, doc, deck).
# Set JOBPILOT_HOME (this checkout); JOBPILOT_PYTHON and JOBPILOT_CLAUDE are optional.
set -euo pipefail
HOME_DIR="${JOBPILOT_HOME:?set JOBPILOT_HOME to the jobpilot checkout}"
PY="${JOBPILOT_PYTHON:-$HOME_DIR/.venv/bin/python}"
CLAUDE="${JOBPILOT_CLAUDE:-claude}"
LOG="${JOBPILOT_INBOX_LOG:-$HOME_DIR/data/inbox.log}"
mkdir -p "$(dirname "$LOG")"
export JOBPILOT_HOME="$HOME_DIR" PYTHONPATH="$HOME_DIR/src"
{ echo "== $(date -u +%FT%TZ) inbox poll"; "$PY" -m jobpilot.inbox --once --claude "$CLAUDE"; } >>"$LOG" 2>&1
