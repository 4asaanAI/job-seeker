"""Start the real MCP server over stdio on the fictional demo data, as Claude would."""
import asyncio
import importlib.util
import os
import sys
from pathlib import Path

from fastmcp import Client
from fastmcp.client.transports import StdioTransport

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("fo_demo", ROOT / "scripts" / "fo_demo.py")
fo_demo = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(fo_demo)


def test_server_serves_demo_seeker_and_stays_pinned(tmp_path):
    store, sid, ids = fo_demo.seed(tmp_path / "d")
    store.close()
    env = {**os.environ, "JOBPILOT_HOME": str(ROOT), "JOBPILOT_DB": str(tmp_path / "d" / "jobpilot.db"),
           "JOBPILOT_OUT": str(tmp_path / "d" / "out"), "JOBPILOT_SEEKER_ID": str(sid),
           "PYTHONPATH": str(ROOT / "src")}

    async def run():
        transport = StdioTransport(command=sys.executable, args=["-m", "jobpilot.server"], env=env)
        async with Client(transport) as client:
            names = {t.name for t in await client.list_tools()}
            assert {"my_matches", "prepare_application", "render_resume", "record_application",
                    "get_followups"} <= names
            mine = await client.call_tool("my_matches", {"seeker_id": sid}, raise_on_error=False)
            assert mine.data["ok"] and mine.data["items"]
            prep = await client.call_tool("prepare_application",
                                          {"seeker_id": sid, "posting_id": ids[0]}, raise_on_error=False)
            assert prep.data["ok"] and "Avery Demo" in prep.data["resume_text"]
            other = await client.call_tool("my_matches", {"seeker_id": sid + 1}, raise_on_error=False)
            assert other.data["ok"] is False and "pinned" in other.data["error"]

    asyncio.run(run())
