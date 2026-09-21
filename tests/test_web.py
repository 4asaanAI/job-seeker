import json
from types import SimpleNamespace

import pytest

from jobpilot import web
from jobpilot.server import build_tools
from jobpilot.store.db import Store

PUBLIC = lambda host: ["93.184.216.34"]  # noqa: E731


@pytest.mark.parametrize("url", [
    "http://127.0.0.1:8888/", "http://localhost/", "http://10.0.0.5/admin",
    "http://192.168.1.1/", "http://169.254.169.254/latest/meta-data/",
    "http://[::1]/", "http://100.64.0.1/", "file:///etc/passwd", "ftp://example.com/",
    "http://user:pw@example.com/", "http://example.com:8080/",
])
def test_guard_refuses_anything_not_plainly_public(url):
    resolve = lambda host: ["127.0.0.1"] if host == "localhost" else [host.strip("[]")]  # noqa: E731
    with pytest.raises(web.WebError):
        web.guard_url(url, resolve=resolve)


def test_guard_refuses_a_public_name_that_resolves_privately():
    with pytest.raises(web.WebError, match="private"):
        web.guard_url("https://evil.example/", resolve=lambda h: ["93.184.216.34", "10.0.0.1"])


def test_guard_accepts_a_public_page():
    assert web.guard_url("https://jobs.example.com/a?b=1", resolve=PUBLIC) == "https://jobs.example.com/a?b=1"


@pytest.fixture
def fake_wigolo(monkeypatch):
    calls = []

    def run(cmd, **kw):
        calls.append((cmd, kw["env"]))
        verb = cmd[1]
        out = {"fetch": {"url": cmd[2], "title": "T", "markdown": "x" * 20000, "links": ["l"]},
               "crawl": {"pages": [{"url": cmd[2], "title": "P", "markdown": "m"}]},
               "extract": {"source_url": cmd[2], "data": {"jsonld": [{"@type": "JobPosting"}]}},
               "research": {"brief": {"highlights": [{"text": "sponsors", "source_url": "u"}]},
                            "citations": [{"title": "c", "url": "u"}]}}[verb]
        return SimpleNamespace(stdout=json.dumps(out), returncode=0)

    monkeypatch.setattr(web.shutil, "which", lambda name: "wigolo")
    monkeypatch.setattr(web.subprocess, "run", run)
    monkeypatch.setattr(web, "_resolve", PUBLIC)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-bad")
    return calls


def test_wigolo_runs_keyless_with_its_private_guard_on(fake_wigolo):
    page = web.fetch("https://example.com/job")
    assert len(page["text"]) < 13000 and "untrusted" in page["note"]
    cmd, env = fake_wigolo[0]
    assert "OPENAI_API_KEY" not in env and env["WIGOLO_FETCH_ALLOW_PRIVATE"] == "false"


def test_crawl_is_capped_and_stays_on_the_seed_site(fake_wigolo):
    web.crawl("https://example.com/careers", max_pages=500)
    cmd = fake_wigolo[0][0]
    assert "--max-pages=10" in cmd
    assert r"--include-patterns=^https?://example\.com(/|$)" in cmd


def test_research_returns_evidence_for_claude_to_write_up(fake_wigolo):
    out = web.research("Does Example sponsor visas?")
    assert out["highlights"] == [{"text": "sponsors", "source": "u"}]
    assert "Write the answer yourself" in out["note"]


def test_web_tools_are_pinned_capped_and_refuse_private_urls(tmp_path, fake_wigolo, monkeypatch):
    store = Store(tmp_path / "t.db")
    store.migrate()
    sid = store.add_seeker("A", 1, "a.yaml", 20)
    tools = build_tools(store, tmp_path, pinned_seeker_id=sid, operator_mode=False)
    assert tools["web_extract"](sid, "https://example.com/job")["ok"]
    assert not tools["web_fetch"](sid + 1, "https://example.com/")["ok"]
    monkeypatch.setattr(web, "_resolve", lambda host: ["127.0.0.1"])
    refused = tools["web_fetch"](sid, "http://internal.example/")
    assert not refused["ok"] and "private" in refused["error"]
    monkeypatch.setattr(store, "event_count_since", lambda *a: 60)
    assert "limit" in tools["web_search"](sid, "jobs")["error"]
    store.close()
