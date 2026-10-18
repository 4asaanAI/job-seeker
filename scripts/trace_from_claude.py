"""Turn a real Claude Code session transcript into a run trace: every tool call, in order, with timing and outcome.

    python scripts/trace_from_claude.py ~/.claude/projects/<project>/<session>.jsonl > docs/run-trace-session.md

Inputs are cut to 160 characters and emails, phone numbers and long digit runs are masked, so a resume or contact detail that
passed through a tool call never lands in the trace. Nothing else is changed: the order, names and outcomes are the real ones.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_PHONE = re.compile(r"\+?\d[\d\s().-]{7,}\d")


def scrub(text: object, limit: int = 160) -> str:
    out = " ".join(str(text).split())
    out = _PHONE.sub("[number]", _EMAIL.sub("[email]", out))
    return out if len(out) <= limit else out[:limit].rstrip() + "..."


def calls(lines: list[str]) -> list[dict]:
    started: dict[str, dict] = {}
    done: list[dict] = []
    for line in lines:
        try:
            event = json.loads(line)
        except ValueError:
            continue
        content = (event.get("message") or {}).get("content") if isinstance(event, dict) else None
        for block in content if isinstance(content, list) else []:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use":
                item = {"name": block.get("name", ""), "at": str(event.get("timestamp", ""))[:19],
                        "input": scrub(json.dumps(block.get("input", {}), ensure_ascii=False)), "ok": None, "result": ""}
                started[block.get("id", "")] = item
                done.append(item)
            elif block.get("type") == "tool_result":
                item = started.get(block.get("tool_use_id", ""))
                if item is not None:
                    item["ok"] = not block.get("is_error", False)
                    text = block.get("content")
                    if isinstance(text, list):
                        text = " ".join(p.get("text", "") for p in text if isinstance(p, dict))
                    item["result"] = scrub(text or "", 200)
    return done


def render(items: list[dict], title: str) -> str:
    out = [f"# Run trace: {title}", "", f"{len(items)} tool call(s), in order. Inputs and results are cut and masked.", ""]
    for i, c in enumerate(items, 1):
        status = "ok" if c["ok"] else ("error" if c["ok"] is False else "no result")
        out.append(f"{i}. `{c['name']}` at {c['at']} ({status})")
        out.append(f"   - input: {c['input']}")
        if c["result"]:
            out.append(f"   - result: {c['result']}")
    return "\n".join(out) + "\n"


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2
    path = Path(argv[1]).expanduser()
    print(render(calls(path.read_text(encoding="utf-8", errors="replace").splitlines()), path.stem))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
