# Getting started: use JobPilot every day, without remembering to

Five minutes to set up, then it works for you in the background. For Fo-specific steps see [APPLYING.md](APPLYING.md).

## 1. Install and create your profile

```bash
python3.12 -m venv .venv && .venv/bin/python -m pip install -e ".[dev]"
.venv/bin/python -m jobpilot.onboard --help      # name, resume, role, location, search terms, sources
.venv/bin/python -m jobpilot.doctor              # tells you what is missing, one line each
```

Copy `.mcp.fo-demo.json.example` to `.mcp.json`, fill in the paths and your seeker id, start Claude Code and log in to Fo
with `/mcp`.

## 2. What to say to Claude

| You say | What happens |
|---|---|
| "Find me the best new jobs" | searches your boards and employers, scores new jobs, lists the best (`run_my_search`, `my_matches`) |
| "What is open at Stripe for product managers?" | the employer's own job feed, title matches first (`company_openings`) |
| "Tailor my resume for job 12" | reorders your own bullets for that job, never invents anything (`tailor_resume`) |
| "Apply to my new jobs" | one plan, one yes from you, then a vetted request per job to Fo (`plan_apply_batch`); it stops at anything not in your resume |
| "How am I doing?" | one-call status: new matches, applications, follow-ups due (`my_dashboard`) |
| "Add Zomato to my companies" | looks for their job feed and subscribes if one exists, and tells you plainly if none was found (`update_my_profile`) |

Jobs drop off your lists the moment their deadline passes. Nothing is applied for without your yes, and no calendar entry is
made until a company itself sends an invitation.

## 3. Make it proactive (optional, macOS)

One command searches, scores and sends you the digest every morning, so you stop having to ask.

```bash
cp .mcp.scheduled.mac.json.example .mcp.scheduled.json        # fix the paths
cp scripts/com.jobpilot.daily.plist.example ~/Library/LaunchAgents/com.jobpilot.daily.plist   # fix the two paths
launchctl load ~/Library/LaunchAgents/com.jobpilot.daily.plist
```

It runs `scripts/daily.sh`, which runs `jobpilot.doctor` first and stops if anything is broken. Output goes to
`data/daily.log`. Stop it with `launchctl unload` on the same file. To try one run by hand: `JOBPILOT_HOME=$PWD bash scripts/daily.sh`.
Delivery goes through Fo when `[fo] enabled = true` (one Fo account serves one person), otherwise Telegram. Put personal settings such as
`[fo]` in a git-ignored `config.local.toml`; it is layered over `config.toml`, so the shared file stays clean.

### Text Fo from your phone (optional, macOS)

A second launchd job polls your Fo texts every 10 minutes so `jp matches`, `jp apply <id>`, `jp sync`, `jp deck` and the rest get answered.

```bash
sed -e "s#/path/to/jobpilot#$PWD#g" -e "s#/path/to/claude#$(which claude)#" scripts/com.jobpilot.inbox.plist.example > ~/Library/LaunchAgents/com.jobpilot.inbox.plist
launchctl load ~/Library/LaunchAgents/com.jobpilot.inbox.plist     # stop with launchctl unload
```

Logs: `data/inbox.log`. Each poll runs one small headless Claude session, so a shorter interval costs more.

## 4. Web tools through a shared Wigolo (optional)

`web_fetch`, `web_search`, `web_crawl`, `web_extract` and `web_research` need a Wigolo server. Either install one locally
or point at a shared one:

```bash
export JOBPILOT_WIGOLO_URL=http://127.0.0.1:13333           # for example an SSH tunnel to the shared server
export JOBPILOT_WIGOLO_TOKEN_FILE=$HOME/.jobpilot/wigolo-token   # file mode 600, never in the repo
```

Be aware: general web search is weak for jobs. Prefer `company_openings` and the board feeds, and open any result with
`web_fetch` before trusting it. Private and internal addresses are always refused.

## 5. If something looks off

`python -m jobpilot.doctor` first. Then `docs/KNOWN-LIMITS.md` lists what is not covered.
