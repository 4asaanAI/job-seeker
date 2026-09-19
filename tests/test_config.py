import pytest

from jobpilot.config import CanaryConfig, SiteConfig, load_config

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


HEAD = """
[paths]
data_dir = "data"
out_dir = "out"

[limits]
score_batch = 40
min_request_gap_seconds = 4.0
"""

CANARY = """
[sites.{key}.canary]
search_term = "business analyst"
results_wanted = 10
min_results = 3
"""


def _load(tmp_path, body, key):
    f = tmp_path / "config.toml"
    f.write_text(HEAD + body + CANARY.format(key=key), encoding="utf-8")
    return load_config(f).sites[key]


def test_site_field_names_the_jobspy_site_and_name_is_the_source_key(tmp_path):
    s = _load(
        tmp_path,
        """
[sites.indeed_us]
site = "indeed"
enabled = true
country_indeed = "usa"
hours_old = 168
results_wanted = 50
""",
        "indeed_us",
    )
    assert s.site == "indeed"
    assert s.name == "indeed_us"
    assert s.country_indeed == "usa"


def test_site_defaults_to_the_source_key_when_omitted(tmp_path):
    s = _load(
        tmp_path,
        """
[sites.naukri]
enabled = true
country_indeed = "india"
hours_old = 168
results_wanted = 50
""",
        "naukri",
    )
    assert s.site == s.name == "naukri"


def test_siteconfig_can_be_built_without_site():
    s = SiteConfig(
        name="naukri_in",
        enabled=True,
        country_indeed="india",
        hours_old=1,
        results_wanted=1,
        fetch_descriptions=True,
        canary=CanaryConfig(
            search_term="x", location=None, results_wanted=1, min_results=1
        ),
    )
    assert s.site == "naukri_in"


def test_shipped_config_loads_and_every_source_is_market_tagged():
    sites = load_config().sites
    for key, s in sites.items():
        assert key.endswith(("_in", "_us")), key
        assert s.country_indeed == ("usa" if key.endswith("_us") else "india"), key
    assert sites["naukri_in"].site == "naukri"
    assert sites["indeed_us"].site == "indeed"
    assert sites["naukri_in"].enabled
    assert sites["indeed_us"].enabled
    assert sites["linkedin_us"].enabled
    assert not sites["glassdoor_in"].enabled
    assert not sites["glassdoor_us"].enabled
    assert not {"naukri", "indeed", "linkedin", "glassdoor"} & sites.keys()
