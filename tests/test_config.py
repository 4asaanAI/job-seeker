import pytest

from jobpilot.config import load_config

GOOD = """
[paths]
data_dir = "data"
out_dir = "out"

[limits]
score_batch = 40
min_request_gap_seconds = 4.0

[sites.naukri]
enabled = true
country_indeed = "india"
hours_old = 168
results_wanted = 50
fetch_descriptions = true

[sites.naukri.canary]
search_term = "merchandiser"
results_wanted = 20
min_results = 10

[sites.indeed]
enabled = true
country_indeed = "india"
hours_old = 168
results_wanted = 50
fetch_descriptions = true

[sites.indeed.canary]
search_term = "merchandiser"
results_wanted = 20
min_results = 10
"""


def test_load_config_reads_each_site_separately(tmp_path):
    f = tmp_path / "config.toml"
    f.write_text(GOOD, encoding="utf-8")
    cfg = load_config(f)

    assert set(cfg.sites) == {"naukri", "indeed"}
    assert cfg.sites["naukri"].name == "naukri"
    assert cfg.sites["naukri"].fetch_descriptions is True
    assert cfg.sites["naukri"].canary.min_results == 10
    assert cfg.min_request_gap_seconds == 4.0


def test_site_without_canary_is_rejected(tmp_path):
    f = tmp_path / "config.toml"
    f.write_text(
        GOOD.replace(
            '[sites.indeed.canary]\nsearch_term = "merchandiser"\n'
            "results_wanted = 20\nmin_results = 10\n",
            "",
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="canary"):
        load_config(f)


def test_canary_must_not_carry_a_recency_filter(tmp_path):
    """A canary with hours_old can return zero in a quiet week and cry wolf."""
    f = tmp_path / "config.toml"
    f.write_text(GOOD + "\nhours_old = 24\n", encoding="utf-8")
    with pytest.raises(ValueError, match="hours_old"):
        load_config(f)


def test_canary_min_results_must_be_positive(tmp_path):
    f = tmp_path / "config.toml"
    f.write_text(GOOD.replace("min_results = 10", "min_results = 0"), encoding="utf-8")
    with pytest.raises(ValueError, match="min_results"):
        load_config(f)
