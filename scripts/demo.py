"""JobPilot R1 demo: see the built pieces work.

Run from the project folder:

    python scripts/demo.py            offline demo, no internet, nothing real touched
    python scripts/demo.py --live     small, careful check against live Indeed pages
    python scripts/demo.py --keep     (either mode) keep the throwaway database folder

What the offline demo does
    Two made-up seekers ("Demo Seeker A" and "B") are set up in a throwaway
    database. A real job sample captured earlier (tests/fixtures/
    jobspy_indeed_us.json) is fed through the real normalisation and discovery
    code by a stand-in scraper. It prints source health, how many sample rows
    became job postings (and why any were rejected), and each seeker's jobs.
    Then it shows "never told twice": jobs get PLACEHOLDER scores, are listed as
    notifiable, are marked as notified, and are no longer listed. A second
    discovery run shows nothing new is created and nothing is notifiable.

What --live does (extra safety rules, none can be switched off)
    * Reads data/seekers/*/profile.yaml (read only) and prints only the folder
      name, the sources, and the search term and place actually used.
    * Only indeed_in and indeed_us are ever contacted. Anything else a profile
      lists (naukri, linkedin, glassdoor, ...) is skipped.
    * By default one search term per seeker, in the profile's own location only,
      10 results per request. --terms N allows more terms, but the whole run may
      never plan more than 6 scraper calls (health checks included); otherwise
      it refuses to start. Note that 6 counts scraper calls, not page loads:
      each call may load up to about two pages (Indeed keeps paging until it has
      10 unique results), so real page loads can be up to about twice the call
      count. The 8 second pause applies between calls, not between those pages.
    * At least 8 seconds between requests.
    * Says exactly what it will do (including each source's health-check
      search), then waits for you to type: yes. This needs an interactive
      terminal; piped input is refused.
    * After every request it checks JobSpy's log for signs of blocking (403, 406,
      429, captcha, forbidden, blocked, too many, or any "responded with status
      code" line, which JobSpy logs for every non-OK answer). The live scraper
      runs with JobSpy's verbose=2, because at its default level JobSpy hides
      exactly those lines. On any hit it stops at once,
      makes no further request and does not retry. Any error from the scraper
      also stops the run.
    * No login, no accounts, no proxies, no special headers.
    * Discovery runs once only; the throwaway database is deleted at the end.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import logging
import re
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from jobpilot.config import load_config  # noqa: E402
from jobpilot.pipeline import SeekerTarget, _plan_searches, run_discovery  # noqa: E402
from jobpilot.profiles import Profile, load_profile  # noqa: E402
from jobpilot.sources.jobspy_source import JobSpySource, _default_scraper  # noqa: E402
from jobpilot.store.db import Store  # noqa: E402

FIXTURE = ROOT / "tests" / "fixtures" / "jobspy_indeed_us.json"
ALLOWED_SOURCES = frozenset({"indeed_in", "indeed_us"})
MAX_REQUESTS = 6
RESULTS_PER_REQUEST = 10
MIN_GAP_SECONDS = 8.0
DEMO_MIN_SCORE = 70.0
PLACEHOLDER_SCORES = (90.0, 80.0, 60.0)
BLOCK_MSG = "stopped: the site looks like it is blocking us; nothing further was requested"
BLOCK_WORDS = re.compile(
    r"(?<!\d)(?:403|406|429)(?!\d)|captcha|forbidden|blocked|too many"
    r"|responded with status code", re.I)
ALLOWED_COUNTRIES = frozenset({"india", "usa"})


# ------------------------------------------------------------- safety pieces
class Stop(BaseException):
    """Ends the whole run. BaseException on purpose: run_discovery contains
    every Exception per site and would carry on to the next site."""


class BlockWatch(logging.Handler):
    """Collects JobSpy's log text so it can be checked after each request."""

    def __init__(self) -> None:
        super().__init__()
        self.text: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.text.append(self.format(record))

    def take_hit(self) -> str | None:
        joined, self.text = "\n".join(self.text), []
        m = BLOCK_WORDS.search(joined)
        return m.group(0) if m else None


def attach_watch(watch: BlockWatch) -> list[logging.Logger]:
    # JobSpy loggers do not propagate, so the handler goes on each of them.
    loggers = [logging.getLogger(n) for n in list(logging.root.manager.loggerDict)
               if n.startswith("JobSpy")]
    for lg in loggers:
        lg.addHandler(watch)
    return loggers


def real_scraper(**kwargs):
    """The real JobSpy scrape, at verbose=2. At jobspy's default verbose=0 it
    resets every JobSpy logger to ERROR and Indeed's block message (logged at
    INFO, followed by an empty result and no exception) would never reach the
    block watch. The level is set inside scrape_jobs, before any request."""
    return _default_scraper(**{**kwargs, "verbose": 2})


class Guard:
    """Wraps a scraper: request budget, block check after every call, and a
    record of every raw row so rejects can be explained. One per source; the
    request counter `shared` is common to all of them."""

    def __init__(self, scraper, shared: dict, watch: BlockWatch | None = None):
        self.scraper, self.shared, self.watch = scraper, shared, watch
        self.rows: dict[tuple, dict] = {}

    def __call__(self, **kwargs):
        if (kwargs.get("site_name") != ["indeed"]
                or kwargs.get("country_indeed") not in ALLOWED_COUNTRIES):
            raise Stop("stopped: request was not for Indeed India or US; "
                       "nothing further was requested")
        cap = self.shared.get("cap")
        if cap is not None and self.shared["n"] >= cap:
            raise Stop(f"stopped: request limit of {cap} reached; nothing further was requested")
        if self.watch:
            self.watch.take_hit()  # forget anything logged before this request
        self.shared["n"] += 1
        try:
            rows = self.scraper(**kwargs)
        except Exception as exc:  # noqa: BLE001 - any failure ends the run
            raise Stop(f"stopped: a request failed ({type(exc).__name__}); "
                       "nothing further was requested") from None
        hit = self.watch.take_hit() if self.watch else None
        if hit:
            raise Stop(f"{BLOCK_MSG}\n  (matched in the site's log: {hit!r})")
        for r in rows:
            self.rows[(r.get("id"), r.get("title"), r.get("company"))] = r
        return rows


# --------------------------------------------------------------- printing
def head(text: str) -> None:
    print(f"\n{text}\n{'-' * len(text)}")


def cut(s, n: int) -> str:
    s = str(s or "")
    return s if len(s) <= n else s[: n - 1] + "~"


def show_health(results) -> None:
    for r in results:
        check = "passed" if r.canary_ok else "FAILED"
        print(f"{r.source}: {r.status.value.upper()} - {r.notes} "
              f"(health check {check}, {r.canary_count} usable rows)")


def reject_reason(row: dict) -> str:
    missing = [w for k, w in (("id", "id"), ("title", "title"), ("company", "company name"))
               if not str(row.get(k) or "").strip()]
    return "no " + " or ".join(missing)


def show_rows(results, guards: dict) -> None:
    for r in results:
        rows = list(guards[r.source].rows.values())
        rejected = [x for x in rows if JobSpySource._to_posting(x, "indeed") is None]
        print(f"{r.source}: Sample rows: {len(rows)} | became job postings: "
              f"{len(r.postings)} | rejected: {len(rejected)}")
        for x in rejected:
            print(f"  rejected: {cut(x.get('title') or '(no title)', 60)!r} - "
                  f"{reject_reason(x)}")


def show_jobs(store: Store, seekers: list[tuple[str, int]], results) -> None:
    by_key = {(p.title, p.company_name): p for r in results for p in r.postings}
    for label, sid in seekers:
        jobs = store.seeker_jobs(sid)
        print(f"\n{label}: {len(jobs)} jobs")
        print(f"  {'#':>2}  {'Title':<44} {'Company':<24} {'Location':<22} Posted")
        for i, j in enumerate(jobs[:15], 1):
            p = by_key.get((j.title, j.company_name))
            print(f"  {i:>2}  {cut(j.title, 44):<44} {cut(j.company_name, 24):<24} "
                  f"{cut(p and p.location, 22):<22} {(p and p.posted_at) or ''}")
        if len(jobs) > 15:
            print(f"  ... and {len(jobs) - 15} more")


def never_told_twice(store: Store, seekers: list[tuple[str, int]]) -> None:
    head("Never told twice")
    print("PLACEHOLDER scores: real scoring arrives in release R2")
    print(f"(scores of 90, 80 and 60 handed out in turn; {DEMO_MIN_SCORE:.0f} or more "
          "counts as worth telling the seeker about)")
    for label, sid in seekers:
        for i, j in enumerate(store.seeker_jobs(sid)):
            store.record_score(sid, j.posting_id, PLACEHOLDER_SCORES[i % 3],
                               "PLACEHOLDER score, not a real assessment",
                               model="placeholder")
    todo = {}
    for label, sid in seekers:
        todo[sid] = store.notifiable_postings(sid, DEMO_MIN_SCORE, 1000)
        print(f"{label}: {len(todo[sid])} notifiable before notifying "
              f"(of {len(store.seeker_jobs(sid))} jobs)")
    print("Now recording that those jobs were shown to the seeker (a demo notification):")
    for label, sid in seekers:
        for j in todo[sid]:
            store.record_notification(sid, j.posting_id, "demo", "first")
        print(f"  {label}: {len(todo[sid])} recorded")
    for label, sid in seekers:
        n = len(store.notifiable_postings(sid, DEMO_MIN_SCORE, 1000))
        print(f"{label}: {n} notifiable after notifying")


def make_sources(cfgs, scraper, sleep, gap, shared, watch=None):
    guards = {c.name: Guard(scraper, shared, watch) for c in cfgs}
    sources = [JobSpySource(c, scraper=guards[c.name], sleeper=sleep,
                            min_request_gap_seconds=gap) for c in cfgs]
    return sources, guards


def finish_dir(tmp: Path, keep: bool) -> None:
    if keep:
        print(f"\nKept: {tmp}")
    else:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------- offline
def demo_profile(name: str, term: str, exclude=()) -> Profile:
    return Profile(name=name, headline="Synthetic demo profile, not a real person",
                   location="United States", sources=["indeed_us"],
                   search_terms=[term], exclude_keywords=list(exclude),
                   min_score=DEMO_MIN_SCORE)


def offline(keep: bool) -> int:
    tmp = Path(tempfile.mkdtemp(prefix="jobpilot-demo-"))
    store = None
    try:
        store = Store(tmp / "demo.db")
        store.migrate()
        print("JobPilot R1 demo (offline: no internet, throwaway database)")
        print("Both seekers below are SYNTHETIC. The job sample is a real capture "
              "from Indeed US,\nplayed back by a stand-in scraper.")
        profiles = [("Demo Seeker A (synthetic)", demo_profile("Demo Seeker A", "product analyst")),
                    ("Demo Seeker B (synthetic)",
                     demo_profile("Demo Seeker B", "product owner", exclude=["manager"]))]
        seekers = [(label, store.add_seeker(label, None, "synthetic")) for label, _ in profiles]
        targets = [SeekerTarget(sid, p) for (_, sid), (_, p) in zip(seekers, profiles)]
        print("Seeker A searches 'product analyst'. Seeker B searches 'product owner' "
              "and hides any\njob with 'manager' in it. The stand-in returns the same "
              "sample for every search.")

        cfg = load_config().sites["indeed_us"]
        sample = lambda **kw: json.loads(FIXTURE.read_text(encoding="utf-8"))  # noqa: E731
        no_sleep = lambda s: None  # noqa: E731

        def discover():
            sources, guards = make_sources([cfg], sample, no_sleep, 0, {"n": 0, "cap": None})
            return run_discovery(store, sources, targets), guards

        head("1. Source health and sample rows")
        results, guards = discover()
        show_health(results)
        show_rows(results, guards)
        head("2. Jobs found per seeker (first 15 rows)")
        show_jobs(store, seekers, results)
        never_told_twice(store, seekers)

        head("Second discovery run")
        ids = lambda: {j.posting_id for _, sid in seekers for j in store.seeker_jobs(sid)}  # noqa: E731
        links = lambda: sum(len(store.seeker_jobs(sid)) for _, sid in seekers)  # noqa: E731
        before_ids, before_links = ids(), links()
        results, _ = discover()
        show_health(results)
        left = sum(len(store.notifiable_postings(sid, DEMO_MIN_SCORE, 1000)) for _, sid in seekers)
        print(f"Second run: {len(ids() - before_ids)} new postings, "
              f"{links() - before_links} new links, {left} notifiable")
        return 0
    finally:
        if store:
            store.close()
        finish_dir(tmp, keep)


# ------------------------------------------------------------------- live
def load_live_profiles(seekers_dir: Path, n_terms: int, sites: dict):
    """Read-only. Prints only folder names, sources and skip reasons."""
    found = []
    for path in sorted(Path(seekers_dir).glob("*/profile.yaml")):
        folder = path.parent.name
        try:
            prof = load_profile(path)
        except Exception:  # noqa: BLE001 - never echo profile content
            print(f"  {folder}: profile could not be read; skipped")
            continue
        keep = []
        for s in prof.sources:
            if s not in ALLOWED_SOURCES:
                print(f"  {folder}: skipped {s} (this demo only ever contacts "
                      "indeed_in and indeed_us)")
            elif s not in sites:
                print(f"  {folder}: skipped {s} (not set up in config.toml)")
            else:
                keep.append(s)
        if not keep:
            print(f"  {folder}: no allowed source left; skipped")
            continue
        found.append((folder, dataclasses.replace(
            prof, sources=keep, search_terms=prof.search_terms[:n_terms], also_search=[])))
    return found


def safe_cfg(cfg):
    """Force the demo's own limits whatever config.toml says."""
    return dataclasses.replace(
        cfg, site="indeed", results_wanted=RESULTS_PER_REQUEST,
        canary=dataclasses.replace(cfg.canary, results_wanted=RESULTS_PER_REQUEST))


def live(n_terms: int, keep: bool, input_fn, sleep, scraper, seekers_dir) -> int:
    sites = load_config().sites
    print("JobPilot R1 demo (LIVE: real requests to Indeed)")
    print("Reading profiles (read only):")
    picked = load_live_profiles(seekers_dir, n_terms, sites)
    if not picked:
        print("Nothing to do: no usable profile with an allowed source.")
        return 1

    cfgs = {}
    for _, p in picked:
        for s in p.sources:
            cfgs.setdefault(s, safe_cfg(sites[s]))
    searches = {s: len(_plan_searches([SeekerTarget(0, p) for _, p in picked
                                       if s in p.sources])) for s in cfgs}
    total = sum(1 + n for n in searches.values())

    head("What will happen")
    for name, cfg in cfgs.items():
        c = cfg.canary
        print(f"  health check for {name}: {c.search_term!r}, "
              f"{'in ' + repr(c.location) if c.location else 'no location'}, "
              f"{c.results_wanted} results")
    for folder, p in picked:
        for term in p.search_terms:
            print(f"  {folder}: {', '.join(p.sources)}; search {term!r} in {p.location!r}")
    print(f"Health checks: {len(cfgs)} (one per source)   Searches: {sum(searches.values())}")
    print(f"Total requests: {total}   Results per request: {RESULTS_PER_REQUEST}   "
          f"Pause between requests: at least {MIN_GAP_SECONDS:.0f} seconds")
    print(f"Note: the limit of {MAX_REQUESTS} counts scraper calls. Each call may load up to "
          "about two pages, so real page loads can be up to about twice the call count.")
    if total > MAX_REQUESTS:
        print(f"Refusing to start: {total} requests planned, limit is {MAX_REQUESTS}. "
              "Nothing was requested.")
        return 2

    if input_fn is None:  # the real prompt: a person must be at the keyboard
        if not sys.stdin.isatty():
            print("Refusing to start: --live needs an interactive terminal so a person "
                  "can type yes. Nothing was requested.")
            return 2
        input_fn = input

    real = scraper is None
    if real:
        import jobspy  # noqa: F401 - creates JobSpy's loggers; no network
    watch = BlockWatch()
    attached = attach_watch(watch)
    try:
        if real and not attached:
            print("Refusing to start: cannot watch JobSpy's log for blocks. "
                  "Nothing was requested.")
            return 2
        try:
            answer = input_fn("\nType yes to go ahead (anything else cancels): ")
        except EOFError:
            answer = ""
        if answer.strip() != "yes":
            print("Cancelled. Nothing was requested.")
            return 1

        tmp = Path(tempfile.mkdtemp(prefix="jobpilot-demo-"))
        store = None
        try:
            store = Store(tmp / "demo.db")
            store.migrate()
            seekers = [(f, store.add_seeker(f, None, f"data/seekers/{f}/profile.yaml"))
                       for f, _ in picked]
            targets = [SeekerTarget(sid, p) for (_, sid), (_, p) in zip(seekers, picked)]
            sources, guards = make_sources(
                list(cfgs.values()), scraper or real_scraper, sleep, MIN_GAP_SECONDS,
                {"n": 0, "cap": MAX_REQUESTS}, watch)
            try:
                results = run_discovery(store, sources, targets)
            except Stop as stop:
                print(f"\n{stop}")
                return 3
            head("1. Source health and rows")
            show_health(results)
            show_rows([r for r in results if r.source in guards], guards)
            head("2. Jobs found per folder (first 15 rows)")
            show_jobs(store, seekers, results)
            never_told_twice(store, seekers)
            return 0
        finally:
            if store:
                store.close()
            finish_dir(tmp, keep)
    finally:
        for lg in attached:
            lg.removeHandler(watch)


def main(argv=None, *, input_fn=None, sleep=time.sleep, scraper=None,
         seekers_dir=None) -> int:
    ap = argparse.ArgumentParser(description="JobPilot R1 demo (offline by default).")
    ap.add_argument("--live", action="store_true", help="small careful live Indeed check")
    ap.add_argument("--keep", action="store_true", help="keep the throwaway database folder")
    ap.add_argument("--terms", type=int, default=1, metavar="N",
                    help="live only: search terms per seeker (default 1; 6-request cap applies)")
    args = ap.parse_args(argv)
    if args.terms != 1 and not args.live:
        ap.error("--terms only applies with --live")
    if args.terms < 1:
        ap.error("--terms must be at least 1")
    if not args.live:
        return offline(args.keep)
    return live(args.terms, args.keep, input_fn, sleep, scraper,
                seekers_dir or ROOT / "data" / "seekers")


if __name__ == "__main__":
    sys.exit(main())
