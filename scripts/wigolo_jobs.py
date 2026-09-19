"""Wigolo -> JobPilot wrapper: ``--url URL --query TERM [--location L] --format json``.

Runs ``wigolo fetch`` (JS-rendered) as a separate process and turns listing
links (``#### [Title](url)`` followed by a location line) into job JSON.
Unknown layouts yield ``[]`` so JobPilot's canary reports the source as blind.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys

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


def matches(job: dict, query: str, location: str | None) -> bool:
    words = [w for w in re.findall(r"\w+", query.lower()) if len(w) > 2]
    if words and not any(w in job["title"].lower() for w in words):
        return False
    return not location or location.lower() in job["location"].lower()


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
    done = subprocess.run(
        [exe, "fetch", args.url, "--render-js=always", "--json", "--include-full-markdown"],
        capture_output=True, text=True, encoding="utf-8", timeout=120, check=False,
    )
    if done.returncode != 0:
        print(done.stderr.strip()[:500], file=sys.stderr)
        return done.returncode
    try:
        page = json.loads(done.stdout)
    except json.JSONDecodeError:
        print("wigolo returned non-JSON stdout", file=sys.stderr)
        return 3
    jobs = [j for j in parse_jobs(page.get("markdown", "")) if matches(j, args.query, args.location)]
    sys.stdout.buffer.write(json.dumps({"jobs": jobs}).encode("ascii"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
