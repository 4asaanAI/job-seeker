import copy
import json
from types import SimpleNamespace

import pytest

from jobpilot import llm
from jobpilot.llm import BedrockBrain, bedrock_scorer, trim_history


class FakeError(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.response = {"Error": {"Code": code}}


class FakeClient:
    """Scripted Bedrock: each converse() pops the next reply for that model."""

    def __init__(self, script):
        self.script, self.calls = script, []

    def converse(self, **kw):
        self.calls.append(copy.deepcopy(kw))
        step = self.script[kw["modelId"]].pop(0)
        if isinstance(step, Exception):
            raise step
        return {"output": {"message": step}}


def use(name, args, tid="t1"):
    return {"role": "assistant", "content": [
        {"reasoningContent": {"reasoningText": {"text": "thinking"}}},
        {"toolUse": {"toolUseId": tid, "name": name, "input": args}}]}


def say(text):
    return {"role": "assistant", "content": [{"text": text}]}


class FakeMCP:
    def __init__(self, env, cwd, results=None):
        self.env, self.cwd, self.called = env, cwd, []
        self.results = results or {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def list_tools(self):
        return [SimpleNamespace(name="my_matches", description="best matches",
                                inputSchema={"type": "object", "properties": {"seeker_id": {"type": "integer"}}})]

    async def call_tool(self, name, args, **kw):
        self.called.append((name, args))
        text = self.results.get(name, json.dumps({"matches": [{"id": 1}]}))
        return SimpleNamespace(content=[SimpleNamespace(text=text)], is_error=False,
                               structured_content=None)


def brain(tmp_path, script, holder):
    def factory(env, cwd):
        holder["mcp"] = FakeMCP(env, cwd)
        return holder["mcp"]
    return BedrockBrain(tmp_path, bot_model="kimi", fallback_model="mistral",
                        client=FakeClient(script), mcp_client_factory=factory, log=lambda m: None)


def test_tool_loop_calls_pinned_tools_and_returns_text(tmp_path):
    holder = {}
    b = brain(tmp_path, {"kimi": [use("my_matches", {"seeker_id": 7}), say("1. Job, Co")]}, holder)
    env = {"JOBPILOT_SEEKER_ID": "7"}
    assert b.answer(42, "seeker7", "rules", env, str(tmp_path), "/jobs") == "1. Job, Co"
    assert holder["mcp"].env == env  # the pin reaches the tool process untouched
    assert holder["mcp"].called == [("my_matches", {"seeker_id": 7})]
    second = b.client.calls[1]
    assert second["toolConfig"]["tools"][0]["toolSpec"]["name"] == "my_matches"
    # reasoning blocks are stripped before the history is replayed
    assert all("reasoningContent" not in blk for m in second["messages"] for blk in m["content"])
    result = second["messages"][-1]["content"][0]["toolResult"]
    assert result["status"] == "success" and "matches" in result["content"][0]["text"]


def test_history_is_kept_per_mode_and_reset(tmp_path):
    holder = {}
    b = brain(tmp_path, {"kimi": [say("first"), say("second"), say("third")]}, holder)
    b.answer(1, "seeker7", "r", {}, str(tmp_path), "hello")
    b.answer(1, "seeker7", "r", {}, str(tmp_path), "again")
    assert [m["content"][0]["text"] for m in b.client.calls[1]["messages"]] == ["hello", "first", "again"]
    b.reset(1)
    b.answer(1, "seeker7", "r", {}, str(tmp_path), "fresh")
    assert len(b.client.calls[2]["messages"]) == 1
    assert b.load_history(1, "onboard") == []  # a different mode never sees it


def test_fallback_model_takes_over_on_outage(tmp_path):
    holder = {}
    b = brain(tmp_path, {"kimi": [FakeError("ServiceUnavailableException")],
                         "mistral": [say("from fallback")]}, holder)
    assert b.answer(1, "m", "r", {}, str(tmp_path), "hi") == "from fallback"


def test_bad_request_is_not_retried_on_fallback(tmp_path):
    holder = {}
    b = brain(tmp_path, {"kimi": [FakeError("ValidationException")], "mistral": []}, holder)
    with pytest.raises(llm.BrainError):
        b.answer(1, "m", "r", {}, str(tmp_path), "hi")


def test_round_cap_forces_a_final_answer(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "MAX_ROUNDS", 2)
    holder = {}
    b = brain(tmp_path, {"kimi": [use("my_matches", {}, "a"), use("my_matches", {}, "b"),
                                  say("partial answer")]}, holder)
    assert b.answer(1, "m", "r", {}, str(tmp_path), "go") == "partial answer"
    assert "Stop calling tools" in b.client.calls[-1]["messages"][-1]["content"][0]["text"]


def test_trim_history_never_starts_on_a_tool_result():
    msgs = [{"role": "user", "content": [{"text": "a"}]},
            {"role": "assistant", "content": [{"toolUse": {}}]},
            {"role": "user", "content": [{"toolResult": {}}]},
            {"role": "assistant", "content": [{"text": "b"}]},
            {"role": "user", "content": [{"text": "c"}]}]
    assert trim_history(msgs, limit=3)[0]["content"][0]["text"] == "c"


def test_scorer_falls_back_and_labels_itself():
    client = FakeClient({"qwen": [FakeError("ThrottlingException")],
                         "mistral": [say('[{"posting_id": 1}]')]})
    ask = bedrock_scorer(model="qwen", fallback_model="mistral", client=client)
    assert ask("score these") == '[{"posting_id": 1}]'
    assert ask.label == "bedrock-batch:qwen"
    assert "toolConfig" not in client.calls[0]  # scoring never gets tools


def test_config_provider_from_file_and_env(tmp_path, monkeypatch):
    from jobpilot.config import load_config
    cfg = tmp_path / "config.toml"
    cfg.write_text('[paths]\ndata_dir="d"\nout_dir="o"\n[limits]\nscore_batch=1\n'
                   'min_request_gap_seconds=1\n[llm]\nprovider="bedrock"\nbot_model="x"\n')
    monkeypatch.delenv("JOBPILOT_LLM_PROVIDER", raising=False)
    c = load_config(cfg).llm
    assert (c.provider, c.bot_model, c.scoring_model) == ("bedrock", "x", llm.DEFAULT_SCORING_MODEL)
    monkeypatch.setenv("JOBPILOT_LLM_PROVIDER", "claude")
    assert load_config(cfg).llm.provider == "claude"
    monkeypatch.setenv("JOBPILOT_LLM_PROVIDER", "openai")
    with pytest.raises(ValueError):
        load_config(cfg)


def test_bot_uses_brain_with_pinned_env(tmp_path):
    from jobpilot.store.db import Store
    from jobpilot.telegram_bot import Bot

    class Brain:
        def __init__(self):
            self.seen, self.resets = None, []

        def answer(self, uid, mode, system, env, cwd, prompt):
            self.seen = (uid, mode, env, prompt)
            return "reply"

        def reset(self, uid):
            self.resets.append(uid)

    store = Store(tmp_path / "j.db")
    store.migrate()
    fake = Brain()
    bot = Bot("tok", tmp_path, store, http=lambda *a, **k: {}, brain=fake,
              runner=lambda *a, **k: pytest.fail("claude must not run"))
    assert bot.ask_claude(99, "hello") == "reply"
    uid, mode, env, prompt = fake.seen
    assert (uid, mode, prompt) == (99, "onboard", "hello")
    assert env["JOBPILOT_ONBOARD_TELEGRAM_ID"] == "99"
    bot.reset(99)
    assert fake.resets == [99]


def test_daemon_body_maps_cli_flags():
    from jobpilot.web import daemon_body
    assert daemon_body(["crawl", "https://a.test", "--max-pages=3", "--include-patterns=^x",
                        "--render-js=always"]) == (
        "crawl", {"url": "https://a.test", "max_pages": 3, "include_patterns": ["^x"],
                  "render_js": "always"})
    assert daemon_body(["search", "q", "--no-content", "--include-domains=lever.co"]) == (
        "search", {"query": "q", "include_domains": ["lever.co"]})


def test_run_wigolo_uses_server_when_configured(tmp_path, monkeypatch):
    from jobpilot import web
    token = tmp_path / "tok"
    token.write_text("secret\n")
    seen = {}

    class Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps({"report": "R", "sources": [{"title": "T", "url": "https://s"}]}).encode()

    def fake_urlopen(req, timeout):
        seen["url"], seen["auth"], seen["body"] = req.full_url, req.get_header("Authorization"), json.loads(req.data)
        return Resp()

    monkeypatch.setenv("JOBPILOT_WIGOLO_URL", "http://127.0.0.1:3333")
    monkeypatch.setenv("JOBPILOT_WIGOLO_TOKEN_FILE", str(token))
    monkeypatch.setattr(web, "urlopen", fake_urlopen)
    out = web.research("does it sponsor visas?")
    assert seen["url"] == "http://127.0.0.1:3333/v1/research"
    assert seen["auth"] == "Bearer secret"
    assert seen["body"]["question"] == "does it sponsor visas?"
    assert out["report"] == "R" and out["sources"][0]["url"] == "https://s"


def test_server_error_becomes_web_error(monkeypatch):
    from jobpilot import web

    class Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b'{"ok": false, "error_reason": "ssrf_metadata"}'

    monkeypatch.setenv("JOBPILOT_WIGOLO_URL", "http://127.0.0.1:3333")
    monkeypatch.delenv("JOBPILOT_WIGOLO_TOKEN_FILE", raising=False)
    monkeypatch.setattr(web, "urlopen", lambda req, timeout: Resp())
    with pytest.raises(web.WebError, match="ssrf_metadata"):
        web.run_wigolo(["fetch", "http://169.254.169.254/"])
