from __future__ import annotations

import os
from pathlib import Path


def jobpilot_home() -> Path:
    """Project root. Never derived from cwd.

    An MCP server launched by Claude Code, or a scheduled task, does not
    reliably start in the project directory.
    """
    env = os.environ.get("JOBPILOT_HOME")
    if env:
        return Path(env).resolve()
    # src/jobpilot/paths.py -> src/jobpilot -> src -> project root
    return Path(__file__).resolve().parents[2]
