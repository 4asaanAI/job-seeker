from pathlib import Path

from jobpilot.scheduled import run_once
from jobpilot.store.db import Store


def test_scheduled_runner_is_bounded_and_records_machine_run(tmp_path):
    calls = []

    class Completed:
        returncode = 0
        stdout = "routine complete\n"
        stderr = ""

    def fake_runner(command, **kwargs):
        calls.append((command, kwargs))
        return Completed()

    db = tmp_path / "jobpilot.db"
    result = run_once(
        machine="test-machine", mcp_config=tmp_path / "mcp.json",
        db_path=db, claude_bin="claude-test", runner=fake_runner,
    )

    assert result.returncode == 0
    command, kwargs = calls[0]
    assert "--strict-mcp-config" in command
    assert "--no-session-persistence" in command
    assert "--tools" in command and command[command.index("--tools") + 1] == ""
    assert "mcp__jobpilot__list_seekers,mcp__jobpilot__discover" in command
    assert kwargs["cwd"] == str(Path(__file__).resolve().parents[1])

    store = Store(db)
    store.migrate()
    assert store.latest_machine_run("test-machine")["status"] == "completed"
    store.close()


def test_scheduled_runner_marks_failure(tmp_path):
    class Completed:
        returncode = 3
        stdout = ""
        stderr = "failed"

    db = tmp_path / "jobpilot.db"
    result = run_once(
        machine="test-machine", mcp_config=tmp_path / "mcp.json",
        db_path=db, runner=lambda *args, **kwargs: Completed(),
    )

    assert result.returncode == 3
    store = Store(db)
    store.migrate()
    assert store.latest_machine_run("test-machine")["status"] == "error"
    store.close()
