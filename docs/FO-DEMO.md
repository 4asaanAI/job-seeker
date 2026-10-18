# Fo x JobPilot demo

JobPilot finds and scores jobs. Fo (Wajo's assistant) does the real-world part:
texts you the matches, reminds you to follow up, and puts the follow-up on your calendar.
Everything here uses a fictional seeker ("Avery Demo"): no real resume, phone or profile.

```
 job boards ──► JobPilot (MCP) ──► scored matches ──┐
                     ▲                              ▼
        Claude ──────┘                    Fo (MCP): text / calendar
          ▲  you ask in plain English                │
          └──────────────── you read it on your phone ◄┘
```

Two ways in, one ledger:

- **A. Claude is the front end.** Claude holds both MCP servers. "What new matches do I have?"
  calls JobPilot's `my_matches`. "Tailor my resume for the Fabrikam role" calls `render_resume`
  (a local DOCX). "I applied" calls `record_application`, then `ask_fo` adds a calendar follow-up.
- **B. Fo is the notifier.** After the scheduled run discovers and scores, JobPilot sends the
  digest and follow-up nudges through Fo (`[fo]` in `config.toml`). Each delivery is written to the
  notifications ledger with `channel = fo`, so a job is never announced twice.

What Fo cannot do (so we do not pretend): `ask_fo` takes text only, so it cannot attach the resume,
and your replies inside Fo do not come back to JobPilot. Replying and acting happens in the Claude session.

## What is verified live

- Calendar: verified. Through a Claude session, Fo created a calendar entry, and then deleted it (checked by the owner).
- Interactive path (A): verified. `ask_fo` through a logged-in Claude session sent a real iMessage, and the
  reply shape (a text block holding JSON with `status`, `response`, `conversationId`) is covered by a test.
- Wajo's answer (Oct 17): an API key for headless use and an inbound-reply webhook (conversation.reply) are not released yet.
  So a text to Fo cannot trigger JobPilot, and unattended runs cannot call Fo with a key.
- Scheduled notifier (B), transport `claude`: verified live. A headless `claude -p` relay with only the Fo send/poll
  tools delivered a vetted message, and Fo's own transcript shows the exact text. An LLM is in this path, so delivery
  counts only when the relay prints a fixed status line. Transport `api` (FO_API_KEY): tested with a fake Fo only.
- (older note) Scheduled notifier via API key: covered by tests with a fake Fo, NOT yet run live. It needs `FO_API_KEY`, and the
  header the key goes in (`Authorization: Bearer`) is unconfirmed. A `working` (slow) reply has not been seen live.

## Text Fo, JobPilot answers (polling inbox) - EXPERIMENTAL

Text Fo `jp matches`, `jp status` or `jp tailor <id>` from your phone, then run
`python -m jobpilot.inbox --once --config <your config with [fo] enabled>` (or put it on a timer). A headless Claude on
your own Fo login reads your recent Fo texts (it can see those threads, and only returns texts starting with `jp`), Python
re-validates each one against the grammar, JobPilot answers locally, and Fo texts the reply. Each text is answered once;
a failed reply is retried. Nothing here applies, emails or changes a job's status.

Live status (Oct 18): the reader runs end to end against a real Fo account, but a test text `jp matches` from the phone did not
appear in any conversation Fo's MCP exposes (user turns visible to the MCP stopped at Oct 14). Until Wajo confirms that texts to Fo are
readable through `get_fo_conversation`, treat this as built and unit-tested, not verified live.

## Run it

```bash
python scripts/fo_demo.py            # dry run: builds the fictional data, prints what Fo would get
export FO_API_KEY=...                # from the hackathon early-access email (never commit it)
python scripts/fo_demo.py --send     # really sends through your Fo account (asks you to type: yes)
```

The script prints the `export` lines (`JOBPILOT_DB`, `JOBPILOT_PYTHON`, `JOBPILOT_SEEKER_ID`, `JOBPILOT_OUT`).
Run them all, copy `.mcp.fo-demo.json.example` to `.mcp.json`, start Claude Code in the repo, and
authenticate Fo with `/mcp` (or set `FO_API_KEY`).

## Demo script (60-90 seconds)

Start from the Claude session, never by texting Fo "use JobPilot": Fo cannot see JobPilot and will say so.

1. `python scripts/fo_demo.py --send`: the phone buzzes with three matches.
2. In Claude Code: "What new matches do I have?" (JobPilot answers).
3. "Tailor my resume for the Fabrikam role." (JobPilot writes a DOCX; show the path).
3b. To text yourself matches from the session, Claude calls `build_fo_message` (a vetted, wrapped message)
   and passes it to `ask_fo` unchanged, so Fo never gets free-form job text.
4. "I applied. Remind me to follow up on Friday." Claude records the application, then asks to call
   Fo. Approve the permission prompt on camera: that prompt is the safety gate.
5. The calendar entry appears.

## Safety choices (and why)

- Fo is an agent that can email, call and buy, and job postings are written by strangers. So the
  message JobPilot sends Fo contains only an id, a score, and a short title/company limited to
  `A-Z a-z 0-9 . , & ( ) / -`; one that is over 50 characters or reads like an instruction
  (ignore, email, send, call, ...) is replaced with `[withheld]`. This keyword check is a safeguard,
  not a guarantee, which is why the Fo account itself should have purchasing and calling off. No reasoning, no links, no instructions. It is wrapped between random
  markers with an explicit "send only, take no other action".
- Never put `mcp__fo__*` in an allow-list and never use `--dangerously-skip-permissions`: in the
  interactive session scraped text and `ask_fo` share one context, so the permission prompt is the control.
- Use a demo Wajo account with purchasing and calling turned off if you can, and scrub the calendar and
  notifications before recording.
- One Fo account serves exactly one seeker (`[fo] seeker_id`). A seeker served through Fo is skipped on
  Telegram, because the "already told" ledger ignores the channel.
- A send counts as delivered only when Fo reports `complete`; anything else is retried next run.
- The API key lives in `FO_API_KEY` only: it is pinned to `https://mcp.api.wajo.ai`, never follows redirects,
  is masked in errors, and is removed from the environment of every `claude` child process.
