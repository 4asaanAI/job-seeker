import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "wigolo_jobs", Path(__file__).parents[1] / "scripts" / "wigolo_jobs.py")
wj = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(wj)

MD = """Sales

##### [Sales Engineer](https://jobs.example.test/a)

Remote — New York, New York

Finance

##### [Staff Accountant](https://jobs.example.test/b)

Hybrid — Atlanta, Georgia

##### [Dentist](https://jobs.example.test/c)
"""


def test_parse_jobs_extracts_title_url_location():
    jobs = wj.parse_jobs(MD)
    assert [j["title"] for j in jobs] == ["Sales Engineer", "Staff Accountant", "Dentist"]
    assert jobs[0]["location"] == "Remote — New York, New York"
    assert jobs[2]["location"] == ""
    assert jobs[0]["external_id"] != jobs[1]["external_id"]


def test_matches_filters_on_title_only():
    jobs = wj.parse_jobs(MD)
    assert [j["title"] for j in jobs if wj.matches(j, "accountant", None)] == ["Staff Accountant"]
    assert len([j for j in jobs if wj.matches(j, "", "atlanta")]) == 3
    assert wj.parse_jobs("no links here") == []
