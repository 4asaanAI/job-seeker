# Fo inbox: text Fo, JobPilot answers (design)

Status: approved by the operator on 2026-10-18 ("do all"). Classified architectural (new subsystem).

## Problem

Wajo has not released a webhook for inbound replies, so a text to Fo cannot trigger JobPilot. But Fo's MCP can
READ the user's own conversation transcripts (`list_fo_conversations`, `get_fo_conversation`), including texts.

## Design

`python -m jobpilot.inbox --once` polls those transcripts through the same headless `claude -p` relay used for the
digest (only the two read tools allowed, no shell, budget cap), finds new texts that match a strict command grammar,
runs JobPilot locally, and answers through Fo's send path.

- **Command grammar (checked in Python, never trusted from the model):** `jp matches`, `jp status`, `jp tailor <id>`,
  case-insensitive, one command per text. Anything else that starts with `jp` gets a one-line help reply.
  The relay may only return texts; Python re-validates every one against the grammar.
- **Read-only effects.** Commands never apply, send email or change a job's status. `tailor` renders a local DOCX and
  texts a summary. Worst case if the model invents or replays a command: the user gets a harmless vetted text.
- **One seeker.** Commands act only for `[fo] seeker_id`; a posting id must be linked to that seeker or it is refused.
- **Replies contain no job text except vetted fragments:** ids, scores, titles through `clean()`, job keywords through
  an allow-list (a-z, 0-9, `+#.-`, at most 20 chars, no instruction words). Replies go through `fo_sender` and the
  same random-marker wrapper, and are capped in length.
- **Exactly once.** Each handled text is fingerprinted (conversation id + time + text) in `data/fo_inbox_seen.json`
  (gitignored); the fingerprint is written only after the reply was confirmed delivered, so failures retry.
  At most 3 commands per poll; replies are batched per poll to bound relay runs.
- **Privacy.** The relay session reads the user's own Fo threads in the user's own Claude login. The prompt tells it
  to output only texts that start with `jp`; the docs say plainly that it can see the threads.
- **Failure modes:** relay failure, bad JSON, or no new texts all exit cleanly and send nothing.

## Not in scope

Emailing, calendar changes, free-form questions, a daemon. A scheduler or `/loop` can run `--once`.

## Tests (no network)

Grammar accept/reject table; relay output with injection text is filtered; dedupe across polls; reply written only after
confirmed delivery; unknown/unlinked ids; reply content has no hostile text; rate limit; relay failure leaves state alone.

## Addendum: applying by text

`jp apply <id>` previews and texts a random one-time code (stored as an event, bound to one posting, two-hour expiry). `jp yes <code>` is the only command with an effect: the server re-validates the code, link, route (no login boards), daily cap and duplicates, consumes the code, then sends the tailored apply request through the same Fo client. Everything else stays read-only.
