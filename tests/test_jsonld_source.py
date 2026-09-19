from jobpilot.config import StructuredConfig
from jobpilot.pipeline import SeekerTarget, run_discovery
from jobpilot.profiles import Profile
from jobpilot.sources.base import SourceStatus
from jobpilot.sources.jsonld_source import JsonLdSource
from jobpilot.store.db import Store


HTML = """
<html><head>
<script type="application/ld+json">
{"@context":"https://schema.org","@graph":[
 {"@type":"https://schema.org/JobPosting","identifier":{"value":"job-1"},
  "title":"Senior Merchandiser","url":"https://careers.example.test/jobs/1",
  "hiringOrganization":{"@type":"Organization","name":"Acme & Co"},
  "jobLocation":{"address":{"addressLocality":"Gurgaon","addressRegion":"HR"}},
  "description":"<p>Own product development.</p>","datePosted":"2026-09-18"},
 {"@type":"JobPosting","title":"Business Analyst",
  "url":"https://careers.example.test/jobs/2",
  "hiringOrganization":{"name":"Acme & Co"},
  "jobLocation":[{"address":{"addressLocality":"Austin","addressRegion":"TX"}}],
  "description":"Analyse demand."}
]}
</script>
<script type="application/ld+json">{"@type":"WebSite","name":"Acme"}</script>
</head></html>
"""


def cfg(minimum=1):
    return StructuredConfig(
        name="acme_jsonld", company_name="Fallback Co",
        endpoint="https://careers.example.test", enabled=True,
        canary_min_results=minimum,
    )


def test_jsonld_jobposting_graph_normalizes_and_caches():
    calls = []

    def fetcher(url, timeout):
        calls.append((url, timeout))
        return HTML

    source = JsonLdSource(cfg(), fetcher=fetcher)
    assert source.canary() == (True, 2, 2)
    found, raw = source.search("merchandiser", "Gurgaon")

    assert raw == 2
    assert len(found) == 1
    assert found[0].external_id == "job-1"
    assert found[0].company_name == "Acme & Co"
    assert found[0].location == "Gurgaon, HR"
    assert found[0].description == "Own product development."
    assert len(calls) == 1


def test_jsonld_url_becomes_stable_id_and_canary_floor_is_honest():
    source = JsonLdSource(cfg(minimum=3), fetcher=lambda _url, _timeout: HTML)
    assert source.canary() == (False, 2, 2)
    found, _ = source.search("analyst", "Austin")
    assert len(found) == 1
    assert found[0].external_id.startswith("url:")


def test_jsonld_source_plugs_into_discovery_without_network(tmp_path):
    store = Store(tmp_path / "jobs.db")
    store.migrate()
    seeker_id = store.add_seeker("A", 22, "a.yaml", 20)
    profile = Profile(
        name="A", headline="Merchandiser", location="Gurgaon",
        sources=["acme_jsonld"], search_terms=["merchandiser"],
    )
    source = JsonLdSource(cfg(), fetcher=lambda _url, _timeout: HTML)

    results = run_discovery(store, [source], [SeekerTarget(seeker_id, profile)])

    assert results[0].status is SourceStatus.HEALTHY
    assert len(store.seeker_jobs(seeker_id)) == 1
    store.close()
