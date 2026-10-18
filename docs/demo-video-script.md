# Demo video script (about 90 seconds)

Read the SAY lines out loud. Do the DO lines on screen. Slow down: it always feels too slow when you record and is
right when people watch.

## Set up first (not on camera)
1. Run `JOBPILOT_PYTHON=/path/to/python scripts/interactive_demo.sh` in a clean Terminal (big font). It seeds the fictional data and starts Claude Code with ONLY JobPilot and Fo loaded, in default permission mode, so the real Fo permission prompt appears. Check the bottom bar says manual/default mode, not auto mode.
2. Start the screen recording (Cmd+Shift+5, record the Terminal window only), then type the prompts below yourself.
3. Use the fictional seeker only. Close email, Slack and anything with real names. Turn on Do Not Disturb.
4. Phone: your iMessage thread with Fo has real history. Record the phone separately, or start a fresh chat, and only show the new messages.
5. Rehearse once. Record twice. Keep the better take.

## Script

**0:00 Hook** (show the repo README with the diagram)
SAY: "Job hunting is a lot of tabs and a lot of forgetting. I built JobPilot, which finds and scores jobs, and
connected it to Fo, so the real-world part, texts and calendar, just happens."

**0:12 Ask for matches** (Claude Code terminal)
DO: type `What are my top three matches?`
SAY: "I just talk to Claude. Claude has two tools connected: JobPilot for the data, and Fo for actions."
DO: let it answer. Point at the three roles and their scores (92, 88, 84). If it lists more, say "top three" again; the list also holds older and lower-scored roles.

**0:30 Prepare an application** (terminal)
DO: type `Prepare my application for the Fabrikam role, and show me what you'd send.`
SAY: "JobPilot builds the application from my own saved resume facts. It can reorder them, but it can't invent anything.
And nothing is sent yet."

**0:48 The safety moment** (terminal, keep the prompt on screen)
DO: type `Looks good. Send me my new matches through Fo.` Claude should first call JobPilot's `build_fo_message`, then `ask_fo` with that message unchanged. Point at it.
DO: when Claude Code asks permission to call Fo, pause on the prompt for two seconds, then approve.
SAY: "Fo can email, call and buy for me, and job posts are written by strangers. So Claude has to ask me first, and
Fo only ever gets an id, a score and a vetted title. Never the job post itself."

**1:05 It lands on the phone** (phone screen, then back)
DO: show the text arriving: "JobPilot: 3 new match(es) ..." (the three roles that had not been sent yet: ids 1, 2 and 3)
SAY: "And there it is, on my phone."

**1:15 Follow-up** (terminal)
DO: type `I applied to the Fabrikam role. Remind me to follow up in three days.`
DO: approve the Fo prompt again. Show the calendar entry if it appears.
SAY: "I record the application, and Fo books the follow-up. If it never gets a reply, JobPilot reminds me again."
(Fo creating a calendar entry was verified live. If it still does not appear on camera, say "...and Fo texts me the reminder" and do not claim an entry you did not see.)

**1:28 Close** (README with the repo link)
SAY: "JobPilot already existed. For this hackathon I added the Fo connection, the safety layer, and applications.
It's open source, link below. Thanks, Wajo."

## Do not say or show
- Do not say it was built from scratch; JobPilot predates the hackathon.
- Do not text Fo "use JobPilot": Fo cannot see JobPilot and will say so.
- Do not say the scheduled daily digest runs through Fo; that part is tested but not run live.
- Do not show your real resume, phone number, calendar or Fo chat history.
