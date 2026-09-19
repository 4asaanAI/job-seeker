# src/jobpilot/digest/render.py
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from jinja2 import Environment, select_autoescape

from ..sources.base import safe_url
from ..store.db import Store

# Autoescaping is load-bearing: every value below is scraped from the open
# internet. Never add |safe to this template.
TEMPLATE = """<!doctype html>
<html><head><meta charset="utf-8"><title>JobPilot - {{ seeker.name }}</title>
<style>
 body{font-family:system-ui,sans-serif;max-width:46rem;margin:2rem auto;padding:0 1rem}
 .job{border:1px solid #ddd;border-radius:8px;padding:1rem;margin:1rem 0}
 .score{font-weight:700}
 .health{background:#f6f6f6;border-radius:8px;padding:.75rem;font-size:.9rem}
 .warn{background:#ffe9e9}
</style></head><body>
<h1>{{ seeker.name }}</h1>
<p>{{ generated_at }}</p>

{% if not health %}
<div class="health warn">
  <strong>No source health recorded for this digest.</strong>
  Nothing here can be trusted as a picture of the market, because no source
  reported whether it ran. Do not read an empty list as "no jobs".
</div>
{% elif unhealthy %}
<div class="health warn">
  <strong>Some sources could not be read this run.</strong>
  Results below are incomplete - this is not a quiet market.
  <ul>{% for h in unhealthy %}
    <li>{{ h.source }}: {{ h.status }}{% if h.stale %} (stale -- last reported
      {{ h.run_at }}){% endif %} - {{ h.notes }}
      {%- if h.status == "degraded" %} (below its usual results; the source
      itself is still reachable and can recover on a later run without any
      action){% endif -%}</li>
  {% endfor %}</ul>
</div>
{% else %}
<div class="health">
  All sources healthy.
  {% for h in health %}{{ h.source }}: {{ h.results_count }} found.{% endfor %}
</div>
{% endif %}

{% if jobs %}
  <h2>{{ jobs|length }} role{{ '' if jobs|length == 1 else 's' }} worth a look</h2>
  {% for j in jobs %}
  <div class="job">
    <div class="score">{{ '%.0f'|format(j.fit_score) }}/100</div>
    <h3>{% if j.url %}<a href="{{ j.url }}">{{ j.title }}</a>{% else %}{{ j.title }}{% endif %}</h3>
    <p>{{ j.company_name }}</p>
    <p>{{ j.reasoning or "No reasoning was recorded for this score." }}</p>
  </div>
  {% endfor %}
{% elif health and not unhealthy %}
  <p>{{ total_found }} roles found, none cleared the bar this run. Sources were
  healthy, so this is a genuinely quiet period rather than a gap in coverage.</p>
{% endif %}
</body></html>
"""


def render_digest(
    store: Store,
    seeker_id: int,
    min_score: float,
    limit: int,
    out_dir: Path,
    channel: str = "digest",
    record: bool = True,
    stale_after_hours: float = 24.0,
) -> Path:
    """Render, and by default record what was shown so it is never shown again.

    stale_after_hours: a source whose last run predates this window is shown
    as unhealthy even if that run itself was healthy -- discovery not having
    run recently must not read as "checked and quiet" (deferred final-review
    finding: source_health_summary's `since`/`stale` behaviour was written in
    R1 but never actually wired into anything that reads it, until now).
    """
    seeker = store.get_seeker(seeker_id)
    since = (datetime.now(timezone.utc) - timedelta(hours=stale_after_hours)).isoformat()
    health = store.source_health_summary(since=since)
    unhealthy = [h for h in health if h["status"] != "healthy" or h["stale"]]
    total_found = sum(int(h["results_count"]) for h in health)

    jobs = store.notifiable_postings(seeker_id, min_score, limit)

    env = Environment(autoescape=select_autoescape(default_for_string=True))
    html = env.from_string(TEMPLATE).render(
        seeker=seeker,
        jobs=[
            {
                "title": j.title,
                "company_name": j.company_name,
                "url": safe_url(j.url),
                "fit_score": j.fit_score,
                "reasoning": j.reasoning,
            }
            for j in jobs
        ],
        health=health,
        unhealthy=unhealthy,
        total_found=total_found,
        generated_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
    )

    seeker_dir = Path(out_dir) / "seekers" / f"{seeker.id}-{seeker.output_token}"
    seeker_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    path = seeker_dir / f"digest-{stamp}.html"
    path.write_text(html, encoding="utf-8")

    if record:
        for j in jobs:
            store.record_notification(seeker_id, j.posting_id, channel, "first",
                                      message_ref=path.name)
    return path
