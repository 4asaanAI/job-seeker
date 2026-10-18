import json

from jobpilot.config import WigoloConfig
from jobpilot.sources.wigolo_source import WigoloSource


def config():
    return WigoloConfig(
        name="acme_wigolo", company_name="Acme", endpoint="https://careers.example.test",
        command=("wigolo-wrapper",), canary_term="analyst", canary_location="Austin",
        enabled=True,
    )


def test_wigolo_runs_as_argv_process_and_normalizes_json():
    calls = []

    class Completed:
        returncode = 0
        stdout = json.dumps({"jobs": [{
            "id": "w1", "title": "Business Analyst", "location": "Austin",
            "url": "https://careers.example.test/job/w1",
            "description": "Analyse demand.",
        }]})
        stderr = ""

    def runner(command, **kwargs):
        calls.append((command, kwargs))
        return Completed()

    source = WigoloSource(config(), runner=runner)
    assert source.canary() == (True, 1, 1)
    found, raw = source.search("analyst", "Austin")

    assert raw == 1 and found[0].external_id == "w1"
    assert calls[0][0] == [
        "wigolo-wrapper", "--url", "https://careers.example.test",
        "--query=analyst", "--format", "json", "--location=Austin",
    ]
    assert calls[0][1]["check"] is False
    assert calls[0][1]["timeout"] == 30.0


def test_wigolo_invalid_json_is_a_loud_failure():
    class Completed:
        returncode = 0
        stdout = "not-json"
        stderr = ""

    source = WigoloSource(config(), runner=lambda *args, **kwargs: Completed())
    try:
        source.canary()
    except RuntimeError as exc:
        assert "invalid JSON" in str(exc)
    else:
        raise AssertionError("invalid wrapper output was accepted")
