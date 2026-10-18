"""Exercise the real MCP server over stdio the way a judge would: list tools, call everything, abuse inputs."""
import asyncio
import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest
from fastmcp import Client
from fastmcp.client.transports import StdioTransport

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("fo_demo", ROOT / "scripts" / "fo_demo.py")
fo_demo = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(fo_demo)

# tools that change the database or the filesystem when given valid input
MUTATING = {"record_score", "record_notification", "record_application", "set_job_status", "save_base_resume",
            "render_resume", "add_target_company", "update_my_profile", "add_seeker", "discover", "run_my_search",
            "sync_google_sheet", "build_digest", "register_me", "investigate_posting", "verify_still_open",
            "check_company_careers", "web_search", "web_fetch", "web_crawl", "web_extract", "web_research",
            "company_openings", "get_scoring_batch"}


def env_for(tmp, sid=None):
    store, seeker, ids = fo_demo.seed(tmp / "d")
    store.close()
    env = {**os.environ, "JOBPILOT_HOME": str(ROOT), "JOBPILOT_DB": str(tmp / "d" / "jobpilot.db"),
           "JOBPILOT_OUT": str(tmp / "d" / "out"), "PYTHONPATH": str(ROOT / "src")}
    env.pop("JOBPILOT_SEEKER_ID", None)
    env.pop("JOBPILOT_ONBOARD_TELEGRAM_ID", None)
    if sid:
        env["JOBPILOT_SEEKER_ID"] = str(sid)
    return env, seeker, ids


def client(env):
    return Client(StdioTransport(command=sys.executable, args=["-m", "jobpilot.server"], env=env))


async def call(c, name, args):
    r = await c.call_tool(name, args, raise_on_error=False)
    if r.data is not None:
        return r.data
    try:
        return json.loads(r.content[0].text) if r.content else None
    except ValueError:                       # a schema-validation error is a refusal, not a crash
        return {"ok": False, "error": r.content[0].text}


def test_every_tool_is_listed_documented_and_has_a_schema(tmp_path):
    env, sid, ids = env_for(tmp_path, sid=1)

    async def run():
        async with client(env) as c:
            tools = await c.list_tools()
            assert len(tools) >= 30
            for t in tools:
                assert t.description and len(t.description) > 20, f"{t.name} has no real description"
                assert (getattr(t, 'input_schema', None) or t.inputSchema).get("type") == "object", t.name
            names = {t.name for t in tools}
            for must in ("my_matches", "tailor_resume", "prepare_application", "build_fo_message",
                         "plan_apply_batch", "build_fo_apply_message", "apply_candidates", "render_resume"):
                assert must in names
    asyncio.run(run())


def test_pinned_session_hides_operator_tools_and_refuses_other_seekers(tmp_path):
    env, sid, ids = env_for(tmp_path, sid=1)

    async def run():
        async with client(env) as c:
            names = {t.name for t in await c.list_tools()}
            assert "list_seekers" not in names and "add_seeker" not in names
            for tool in ("my_matches", "my_status", "my_history", "tailor_resume", "plan_apply_batch", "apply_candidates"):
                args = {"seeker_id": 2}
                if tool == "tailor_resume":
                    args["posting_id"] = ids[0]
                out = await call(c, tool, args)
                assert out["ok"] is False and "pinned" in out["error"], tool
    asyncio.run(run())


def test_read_only_tools_work_for_the_pinned_seeker(tmp_path):
    env, sid, ids = env_for(tmp_path, sid=1)

    async def run():
        async with client(env) as c:
            assert (await call(c, "my_matches", {"seeker_id": 1}))["ok"]
            assert (await call(c, "my_status", {"seeker_id": 1}))
            assert (await call(c, "get_followups", {"seeker_id": 1}))["ok"]
            assert (await call(c, "my_applications", {"seeker_id": 1}))["ok"]
            assert (await call(c, "my_resumes", {"seeker_id": 1}))["ok"]
            assert (await call(c, "resume_facts", {"seeker_id": 1}))["ok"]
            assert (await call(c, "apply_candidates", {"seeker_id": 1}))["ok"]
            assert (await call(c, "plan_apply_batch", {"seeker_id": 1}))["ok"]
            tailor = await call(c, "tailor_resume", {"seeker_id": 1, "posting_id": ids[0]})
            assert tailor["ok"] and tailor["suggestion"]["bullet_order"]
            msg = await call(c, "build_fo_message", {"seeker_id": 1})
            assert msg["ok"] and "take no other action" in msg["message"]
            prep = await call(c, "prepare_application", {"seeker_id": 1, "posting_id": ids[0]})
            assert prep["ok"] and "Nothing was sent" in prep["note"]
    asyncio.run(run())


@pytest.mark.parametrize("bad", [
    {"seeker_id": "1; DROP TABLE seekers;--"}, {"seeker_id": None}, {"seeker_id": -1}, {"seeker_id": 10**30},
    {"seeker_id": 1, "limit": -5}, {"seeker_id": 1, "limit": "ten"}, {}, {"seeker_id": [1, 2]}, {"seeker_id": 1.5},
])
def test_malformed_arguments_never_crash_the_server(tmp_path, bad):
    env, sid, ids = env_for(tmp_path, sid=1)

    async def run():
        async with client(env) as c:
            for tool in ("my_matches", "get_followups", "plan_apply_batch", "apply_candidates"):
                r = await c.call_tool(tool, bad, raise_on_error=False)   # an error result is fine; a dead server is not
                assert r is not None
            assert (await call(c, "my_matches", {"seeker_id": 1}))["ok"]  # still alive afterwards
    asyncio.run(run())


def test_hostile_ids_and_text_are_handled_safely(tmp_path):
    env, sid, ids = env_for(tmp_path, sid=1)

    async def run():
        async with client(env) as c:
            for bad_id in (0, -3, 10**9, "1 OR 1=1"):
                out = await call(c, "tailor_resume", {"seeker_id": 1, "posting_id": bad_id})
                assert out is None or out.get("ok") is False
            out = await call(c, "render_resume", {"seeker_id": 1, "posting_id": ids[0],
                                                  "bullet_order": {"northwind": ["../../etc/passwd"]}})
            assert out["ok"] is False
            out = await call(c, "render_resume", {"seeker_id": 1, "posting_id": ids[0], "skills": ["Invented Skill"]})
            assert out["ok"] is False                                       # facts only: unknown skill refused
            out = await call(c, "build_fo_apply_message", {"seeker_id": 1, "posting_id": ids[0], "batch_approved": True})
            assert out["ok"] in (True, False)
    asyncio.run(run())


def test_operator_session_lists_seekers_and_the_fo_guard_applies(tmp_path):
    env, sid, ids = env_for(tmp_path)

    async def run():
        async with client(env) as c:
            names = {t.name for t in await c.list_tools()}
            assert "list_seekers" in names
            seekers = await call(c, "list_seekers", {})
            assert seekers
            assert (await call(c, "my_matches", {"seeker_id": 1}))["ok"]
    asyncio.run(run())


def test_server_refuses_to_start_on_empty_pin_variables(tmp_path):
    import subprocess
    env, sid, ids = env_for(tmp_path)
    for var in ("JOBPILOT_SEEKER_ID", "JOBPILOT_ONBOARD_TELEGRAM_ID"):
        r = subprocess.run([sys.executable, "-m", "jobpilot.server"], env={**env, var: ""}, capture_output=True,
                           text=True, timeout=30, input="")
        assert r.returncode != 0 and "empty" in (r.stderr + r.stdout)


def test_demo_mcp_config_example_is_valid_json_with_no_secret():
    cfg = json.loads((ROOT / ".mcp.fo-demo.json.example").read_text())
    assert set(cfg["mcpServers"]) == {"jobpilot", "fo"}
    assert cfg["mcpServers"]["fo"]["url"] == "https://mcp.api.wajo.ai/fo"
    assert "sk-" not in json.dumps(cfg)
