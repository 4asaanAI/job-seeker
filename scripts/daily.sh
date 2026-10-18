#!/usr/bin/env bash
# One proactive JobPilot run: search the boards, score new jobs, push the digest.
# Set JOBPILOT_HOME (this checkout) and JOBPILOT_PYTHON (its venv python); everything else is optional.
set -euo pipefail
HOME_DIR="${JOBPILOT_HOME:?set JOBPILOT_HOME to the jobpilot checkout}"
PY="${JOBPILOT_PYTHON:-$HOME_DIR/.venv/bin/python}"
LOG="${JOBPILOT_LOG:-$HOME_DIR/data/daily.log}"
mkdir -p "$(dirname "$LOG")"
export JOBPILOT_HOME="$HOME_DIR" PYTHONPATH="$HOME_DIR/src"
{
  echo "== $(date -u +%FT%TZ) daily run"
  "$PY" -m jobpilot.doctor || { echo "doctor found problems; not running"; exit 1; }
  "$PY" -m jobpilot.scheduled --mcp-config "${JOBPILOT_MCP_CONFIG:-$HOME_DIR/.mcp.scheduled.json}"
} >>"$LOG" 2>&1
