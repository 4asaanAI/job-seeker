# JobPilot v0.2 usage

The repository is now merged into `main` at tag `v0.2.0`. R2 is a local Python
engine; it does not yet include a web UI, Telegram bot, or MCP server. Those
interfaces belong to R3.

## 1. Verify the safe offline demo

From the repository root:

```bash
/Users/abhimanyusingh/miniforge3/envs/jobpilot/bin/python -m pytest -q -p no:cacheprovider
/Users/abhimanyusingh/miniforge3/envs/jobpilot/bin/python scripts/demo.py
```

The demo uses a throwaway database and captured Indeed rows. It does not make
network requests, touch real seeker profiles, or send Telegram messages.

## 2. Run the guarded live smoke test

Use a real Terminal window, not a non-interactive shell:

```bash
/Users/abhimanyusingh/miniforge3/envs/jobpilot/bin/python scripts/demo.py --live
```

This path is deliberately limited to the enabled Indeed sources, caps the
request plan, waits between calls, stops on a detected block, and deletes its
throwaway database afterward. It is a smoke test, not the production R2 run:
it does not call an LLM for real scoring and it does not send notifications.

## 3. Exercise the R2 scoring and digest flow

R2 exposes the handoff between discovery, scoring, and digest rendering:

1. Create a `Store` and call `migrate()`.
2. Load a seeker profile with `load_profile()` and register it with
   `Store.add_seeker()`.
3. Build `JobSpySource` objects for the profile's configured sources and call
   `run_discovery()`.
4. Call `build_scoring_batch(store, seeker_id, profile, limit)`. This returns
   the seeker-scoped rubric, profile facts, and unscored jobs for Claude (or
   another approved scorer). Scraped descriptions are untrusted data; the
   returned `note` is part of the safety contract.
5. Write each model-approved score with `store.record_score(...)`.
6. Render the HTML digest with `render_digest(...)`. By default it records the
   delivered `first` notifications, so the same job is not returned again.

The generated file is under the configured output directory:

```text
out/seekers/<seeker-id>-<output-token>/digest-<timestamp>.html
```

Open that HTML file in a browser to inspect the current R2 digest. The
`review_required` flag is stored per seeker, but the approval workflow that
enforces it is R3 work.

## 4. Telegram / UI status

There is currently no Telegram bot or browser UI to connect to. Do not create a
Telegram token or expect `/start` to work against this checkout yet. R3 must
first add `src/jobpilot/server.py`, its MCP tools, sender-ID scoping, and the
Telegram channel registration. Until then, the offline/live demo and the HTML
digest are the supported manual interfaces.
