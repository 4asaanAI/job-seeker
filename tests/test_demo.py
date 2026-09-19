# tests/test_demo.py
"""Offline tests for scripts/demo.py. No network, no gitignored files."""
import importlib.util
import json
import logging
import re
import shutil
import socket
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "jobspy_indeed_us.json"

spec = importlib.util.spec_from_file_location("demo", ROOT / "scripts" / "demo.py")
demo = importlib.util.module_from_spec(spec)
sys.modules["demo"] = demo
spec.loader.exec_module(demo)

BLOCK_MSG = "stopped: the site looks like it is blocking us; nothing further was requested"


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("network call attempted")

    monkeypatch.setattr(socket.socket, "connect", boom)
    monkeypatch.setattr(demo, "_default_scraper", boom)


@pytest.fixture
def tmp_root(tmp_path, monkeypatch):
    """Redirect tempfile so the demo's temp dir is visible and checkable."""
    t = tmp_path / "tmp"
    t.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(t))
    return t


# ---------------------------------------------------------------- offline
def test_offline_demo_end_to_end(capsys, tmp_root):
    assert demo.main([]) == 0
    out = capsys.readouterr().out

    assert "indeed_us: HEALTHY" in out                       # health line
    assert "Sample rows: 10 | became job postings: 9 | rejected: 1" in out
    assert "no company name" in out                          # why the reject
    assert "Demo Seeker A (synthetic)" in out and "Demo Seeker B (synthetic)" in out
    assert "Hitachi Energy" in out and "Austin, TX, US" in out and "2026-09-17" in out
    assert "PLACEHOLDER scores: real scoring arrives in release R2" in out

    before = re.findall(r"(Demo Seeker [AB]) \(synthetic\): (\d+) notifiable before notifying", out)
    after = re.findall(r"(Demo Seeker [AB]) \(synthetic\): (\d+) notifiable after notifying", out)
    assert len(before) == 2 and all(int(n) > 0 for _, n in before)
    assert len(after) == 2 and all(n == "0" for _, n in after)
    assert "Second run: 0 new postings, 0 new links, 0 notifiable" in out
    assert list(tmp_root.iterdir()) == []                    # temp dir removed


def test_offline_keep_prints_path_and_keeps_db(capsys, tmp_root):
    assert demo.main(["--keep"]) == 0
    m = re.search(r"Kept: (\S+)", capsys.readouterr().out)
    kept = Path(m.group(1))
    try:
        assert (kept / "demo.db").exists()
    finally:
        shutil.rmtree(kept)


def test_seeker_b_is_filtered_by_its_own_exclusion(capsys, tmp_root):
    demo.main([])
    tables = capsys.readouterr().out.split("Never told twice")[0]
    a, b = tables.split("Demo Seeker A (synthetic): ")[1].split("Demo Seeker B (synthetic): ")
    assert "Manager" in a and "Manager" not in b


# ------------------------------------------------------------------- live
def write_profile(root, folder, sources, terms, location="Austin, TX",
                  also_search=("Dallas, TX",), notes=("SECRET-NOTE-XYZ",)):
    d = root / folder
    d.mkdir(parents=True)
    d.joinpath("profile.yaml").write_text(json.dumps({
        "name": "Real Person Name", "headline": "SECRET-HEADLINE-QQQ",
        "location": location, "sources": list(sources),
        "search_terms": list(terms), "also_search": list(also_search),
        "notes": list(notes), "min_score": 55,
    }), encoding="utf-8")  # JSON is valid YAML


class Rig:
    def __init__(self, tmp_path):
        self.root = tmp_path / "seekers"
        self.root.mkdir()
        self.calls, self.sleeps, self.prompts = [], [], []
        self.answer = "yes"
        self.rows = json.loads(FIXTURE.read_text(encoding="utf-8"))
        self.on_call = None

    def scraper(self, **kw):
        self.calls.append(kw)
        if self.on_call:
            self.on_call(len(self.calls))
        return list(self.rows)

    def input(self, prompt=""):
        self.prompts.append(prompt)
        return self.answer

    def run(self, *args):
        return demo.main(["--live", *args], input_fn=self.input,
                         sleep=self.sleeps.append, scraper=self.scraper,
                         seekers_dir=self.root)


@pytest.fixture
def rig(tmp_path, tmp_root):
    return Rig(tmp_path)


@pytest.mark.parametrize("answer", ["", "no", "y", "YES please", "Yes"])
def test_live_needs_typed_yes(rig, answer, capsys):
    write_profile(rig.root, "a", ["indeed_us"], ["t1"])
    rig.answer = answer
    assert rig.run() != 0
    assert rig.calls == [] and rig.sleeps == []
    assert len(rig.prompts) == 1


def test_live_eof_at_prompt_is_a_no(rig):
    write_profile(rig.root, "a", ["indeed_us"], ["t1"])

    def eof(prompt=""):
        raise EOFError

    assert demo.main(["--live"], input_fn=eof, sleep=rig.sleeps.append,
                     scraper=rig.scraper, seekers_dir=rig.root) != 0
    assert rig.calls == []


def test_live_has_no_bypass_flag(rig):
    write_profile(rig.root, "a", ["indeed_us"], ["t1"])
    with pytest.raises(SystemExit):
        demo.main(["--live", "--yes"], input_fn=rig.input, scraper=rig.scraper,
                  seekers_dir=rig.root)
    assert rig.calls == []


def test_live_flag_required_for_terms(rig):
    with pytest.raises(SystemExit):
        demo.main(["--terms", "2"])


def test_plan_is_printed_before_the_prompt(rig, capsys):
    write_profile(rig.root, "folder_a", ["indeed_us"], ["data analyst", "second term"])
    seen = {}

    def spy_input(prompt=""):
        seen["out"] = capsys.readouterr().out
        return "no"

    demo.main(["--live"], input_fn=spy_input, scraper=rig.scraper,
              sleep=rig.sleeps.append, seekers_dir=rig.root)
    out = seen["out"]
    assert "folder_a" in out and "indeed_us" in out
    assert "data analyst" in out and "Austin, TX" in out
    assert "second term" not in out and "Dallas" not in out      # caps by default
    assert "Total requests: 2" in out
    assert "Results per request: 10" in out
    assert rig.calls == []


def test_allowlist_constant():
    assert demo.ALLOWED_SOURCES == frozenset({"indeed_in", "indeed_us"})


def test_allowlist_drops_other_sites(rig, capsys):
    write_profile(rig.root, "a", ["naukri_in", "indeed_us", "linkedin_us",
                                  "glassdoor_us", "linkedin_in"], ["t1"])
    assert rig.run() == 0
    out = capsys.readouterr().out
    assert rig.calls and all(c["site_name"] == ["indeed"] for c in rig.calls)
    assert {c["country_indeed"] for c in rig.calls} == {"usa"}
    for name in ("naukri_in", "linkedin_us", "glassdoor_us", "linkedin_in"):
        assert f"skipped {name}" in out
    assert "Total requests: 2" in out


def test_profile_with_only_disallowed_sources_makes_no_request(rig, capsys):
    write_profile(rig.root, "a", ["naukri_in", "linkedin_in"], ["t1"])
    assert rig.run() != 0
    assert rig.calls == [] and rig.prompts == []


def test_default_uses_first_term_own_location_ten_results_and_gap(rig):
    write_profile(rig.root, "a", ["indeed_us"], ["first", "second", "third"])
    assert rig.run() == 0
    assert len(rig.calls) == 2                       # canary + one search
    search = rig.calls[1]
    assert search["search_term"] == "first" and search["location"] == "Austin, TX"
    assert all(c["results_wanted"] == 10 for c in rig.calls)
    assert len(rig.sleeps) == len(rig.calls)
    assert all(s >= 8 for s in rig.sleeps)


def test_terms_flag_raises_terms_up_to_the_cap(rig, capsys):
    write_profile(rig.root, "a", ["indeed_us"], ["t1", "t2", "t3", "t4", "t5", "t6"])
    assert rig.run("--terms", "5") == 0              # 1 canary + 5 = 6, allowed
    assert len(rig.calls) == 6


def test_cap_refuses_above_six(rig, capsys):
    write_profile(rig.root, "a", ["indeed_us"], ["t1", "t2", "t3", "t4", "t5", "t6"])
    assert rig.run("--terms", "6") != 0              # 1 canary + 6 = 7
    out = capsys.readouterr().out
    assert "limit is 6" in out
    assert rig.calls == [] and rig.prompts == []


def test_cap_counts_canaries_across_sources_and_seekers(rig, capsys):
    write_profile(rig.root, "a", ["indeed_us", "indeed_in"], ["a1", "a2"])
    write_profile(rig.root, "b", ["indeed_us"], ["b1", "b2"])
    # two searches per seeker is 4 unique searches on us + 2 on in + 2 canaries
    assert rig.run("--terms", "2") != 0
    assert rig.calls == []


def test_runtime_guard_refuses_a_seventh_request():
    calls = []
    shared = {"n": 0, "cap": 6}
    g = demo.Guard(lambda **k: calls.append(k) or [], shared)
    for _ in range(6):
        g()
    with pytest.raises(demo.Stop):
        g()
    assert len(calls) == 6


KEYWORDS = ["403", "406", "429", "captcha", "recaptcha", "forbidden", "blocked",
            "too many"]


def emit(logger, msg):
    logger.handle(logging.LogRecord(logger.name, logging.ERROR, __file__, 1,
                                    msg, None, None))


@pytest.fixture
def jobspy_logger():
    lg = logging.getLogger("JobSpy:FakeTest")
    lg.propagate = False
    yield lg
    lg.handlers.clear()


@pytest.mark.parametrize("kw", KEYWORDS)
def test_block_detector_aborts_on_each_keyword(rig, jobspy_logger, capsys, kw):
    write_profile(rig.root, "a", ["indeed_us", "indeed_in"], ["t1"])
    rig.on_call = lambda n: emit(jobspy_logger, f"Indeed: response said {kw.upper()} here")
    rc = rig.run()
    out = capsys.readouterr().out
    assert rc == 3
    assert BLOCK_MSG in out
    assert len(rig.calls) == 1                       # no request after the hit
    assert jobspy_logger.handlers == []              # handler removed again


def test_block_detected_after_a_later_request(rig, jobspy_logger, capsys):
    write_profile(rig.root, "a", ["indeed_us"], ["t1", "t2"])
    rig.on_call = lambda n: n == 2 and emit(jobspy_logger, "Indeed: Too Many Requests")
    assert rig.run("--terms", "2") == 3
    assert len(rig.calls) == 2


def test_harmless_log_lines_do_not_stop_the_run(rig, jobspy_logger):
    write_profile(rig.root, "a", ["indeed_us"], ["t1"])
    rig.on_call = lambda n: emit(jobspy_logger, "Indeed: finished scraping 1403 rows")
    assert rig.run() == 0
    assert len(rig.calls) == 2


def test_scraper_exception_stops_the_run(rig, capsys):
    write_profile(rig.root, "a", ["indeed_us", "indeed_in"], ["t1"])

    def boom(n):
        raise RuntimeError("connection reset")

    rig.on_call = boom
    assert rig.run() == 3
    assert len(rig.calls) == 1
    assert "nothing further was requested" in capsys.readouterr().out


def test_notes_and_other_profile_fields_are_never_printed(rig, capsys):
    write_profile(rig.root, "folder_x", ["indeed_us"], ["t1"])
    assert rig.run() == 0
    cap = capsys.readouterr()
    text = cap.out + cap.err
    assert "folder_x" in text
    for secret in ("SECRET-NOTE-XYZ", "SECRET-HEADLINE-QQQ", "Real Person Name"):
        assert secret not in text


def test_live_prints_all_sections_and_never_runs_discovery_twice(rig, capsys, tmp_root):
    write_profile(rig.root, "a", ["indeed_us"], ["t1"])
    assert rig.run() == 0
    out = capsys.readouterr().out
    assert "indeed_us: HEALTHY" in out
    assert "Sample rows" in out and "became job postings" in out
    assert "Hitachi Energy" in out
    assert "PLACEHOLDER scores: real scoring arrives in release R2" in out
    assert re.search(r"a: [1-9]\d* notifiable before notifying", out)
    assert re.search(r"a: 0 notifiable after notifying", out)
    assert "Second run" not in out
    assert len(rig.calls) == 2                       # canary + search, no repeat
    assert list(tmp_root.iterdir()) == []            # temp db deleted


def test_live_keep_keeps_the_temp_dir(rig, capsys):
    write_profile(rig.root, "a", ["indeed_us"], ["t1"])
    assert rig.run("--keep") == 0
    kept = Path(re.search(r"Kept: (\S+)", capsys.readouterr().out).group(1))
    try:
        assert (kept / "demo.db").exists()
    finally:
        shutil.rmtree(kept)
