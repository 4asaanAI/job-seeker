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


class _Response:
    def __init__(self, url):
        self.url = url

    def geturl(self):
        return self.url

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture
def fake_wigolo(monkeypatch):
    calls = []

    def run(cmd, **kw):
        calls.append((cmd, kw["env"]))
        verb = cmd[1]
        out = {"fetch": {"url": cmd[2], "title": "T", "markdown": "x" * 20000, "links": ["l"]},
               "crawl": {"pages": [{"url": cmd[2], "title": "P", "markdown": "m"}]},
               "extract": {"source_url": cmd[2], "data": {"jsonld": [{"@type": "JobPosting"}]}},
               "research": {"evidence": [{"excerpt": "sponsors", "url": "u", "score": 0.9},
                                         {"excerpt": "nav", "url": "v", "score": 0.1}],
                            "citations": [{"title": "c", "url": "u"}]}}[verb]
        return SimpleNamespace(stdout=json.dumps(out), returncode=0)

    monkeypatch.setattr(web, "wigolo_command", lambda: ["wigolo"])
    monkeypatch.setattr(web.subprocess, "run", run)
    monkeypatch.setattr(web, "_resolve", PUBLIC)
    monkeypatch.setattr(web, "_open", lambda request: _Response(request.full_url))
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
    assert out["evidence"][0] == {"excerpt": "sponsors", "source": "u", "score": 0.9}
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


def test_a_redirect_to_a_private_address_is_refused_before_wigolo_runs(monkeypatch, fake_wigolo):
    resolve = {"public.example": ["93.184.216.34"], "127.0.0.1": ["127.0.0.1"]}
    monkeypatch.setattr(web, "_resolve", lambda host: resolve[host])

    def redirecting_open(request):
        handler = web._GuardedRedirects()
        handler.redirect_request(request, None, 302, "Found", {}, "http://127.0.0.1/")
    monkeypatch.setattr(web, "_open", redirecting_open)
    with pytest.raises(web.WebError, match="private"):
        web.fetch("http://public.example/r")
    assert fake_wigolo == []


def test_content_that_ended_up_on_a_private_address_is_discarded(monkeypatch, fake_wigolo):
    monkeypatch.setattr(web, "public_target", lambda url: url)
    monkeypatch.setattr(web, "_resolve", lambda host: ["10.0.0.9"] if host == "10.0.0.9" else ["93.184.216.34"])
    monkeypatch.setattr(web.subprocess, "run", lambda cmd, **kw: SimpleNamespace(
        stdout=json.dumps({"url": "http://10.0.0.9/secret", "markdown": "secret"}), returncode=0))
    with pytest.raises(web.WebError, match="private"):
        web.fetch("https://public.example/js-redirect")
