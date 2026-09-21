"""Wigolo -> JobPilot wrapper: ``--url URL --query TERM [--location L] --format json``.

Runs ``wigolo fetch`` (JS-rendered) as a separate process and turns listing
links (``#### [Title](url)`` followed by a location line) into job JSON.
Unknown layouts yield ``[]`` so JobPilot's canary reports the source as blind.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

CACHE_SECONDS = 1800

# Wigolo uses any LLM provider key it finds in its environment (there is no
# "off" setting). JobPilot never wants Wigolo's own synthesis -- Claude does
# the reasoning -- so it runs with every provider key removed.
LLM_KEYS = ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY",
            "GROQ_API_KEY", "WIGOLO_LLM_API_KEY", "WIGOLO_LLM_PROVIDER")


def wigolo_env() -> dict[str, str]:
    return {k: v for k, v in os.environ.items() if k.upper() not in LLM_KEYS}

LINK = re.compile(r"^#{1,6}\s*\[(?P<title>[^\]]+)\]\((?P<url>https?://[^)\s]+)\)\s*$")


def parse_jobs(markdown: str) -> list[dict]:
    lines = [ln.strip() for ln in markdown.splitlines()]
    jobs = []
    for i, line in enumerate(lines):
        m = LINK.match(line)
        if not m:
            continue
        nxt = next((x for x in lines[i + 1:] if x), "")
        location = "" if LINK.match(nxt) or nxt.startswith("#") else nxt
        url = m["url"]
        jobs.append({
            "external_id": "url:" + hashlib.sha256(url.encode()).hexdigest()[:24],
            "title": m["title"].strip(),
            "location": location,
            "url": url,
        })
    return jobs


def matches(job: dict, query: str, location: str | None = None) -> bool:
    """Title match only; location is left to scoring (boards use many formats)."""
    words = [w for w in re.findall(r"\w+", query.lower()) if len(w) > 2]
    return not words or any(w in job["title"].lower() for w in words)


def fetch_markdown(url: str, exe: str) -> str:
    """Fetch through Wigolo, reusing a 30-minute cache so repeated queries are cheap."""
    cache = Path(tempfile.gettempdir()) / ("wigolo_jobs_" + hashlib.sha256(url.encode()).hexdigest()[:16] + ".md")
    if cache.exists() and time.time() - cache.stat().st_mtime < CACHE_SECONDS:
        return cache.read_text(encoding="utf-8")
    done = subprocess.run(
        [exe, "fetch", url, "--render-js=always", "--json", "--include-full-markdown"],
        capture_output=True, text=True, encoding="utf-8", timeout=120, check=False, env=wigolo_env(),
    )
    if done.returncode != 0:
        raise RuntimeError(done.stderr.strip()[:500] or f"wigolo exited {done.returncode}")
    try:
        markdown = json.loads(done.stdout).get("markdown", "")
    except json.JSONDecodeError as exc:
        raise RuntimeError("wigolo returned non-JSON stdout") from exc
    if markdown:
        cache.write_text(markdown, encoding="utf-8")
    return markdown


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--query", default="")
    ap.add_argument("--location")
    ap.add_argument("--format", default="json")
    args = ap.parse_args(argv)
    exe = shutil.which("wigolo")
    if not exe:
        print("wigolo executable not found on PATH", file=sys.stderr)
        return 2
    try:
        markdown = fetch_markdown(args.url, exe)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 3
    jobs = [j for j in parse_jobs(markdown) if matches(j, args.query)]
    sys.stdout.buffer.write(json.dumps({"jobs": jobs}).encode("ascii"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
