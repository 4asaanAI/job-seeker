# Recording checklist (one page)

Deadline: quote-post on Wajo's MCP launch post by Oct 19, 4:00pm PT. Post EARLY on Oct 18 so likes can build.
Script to read: docs/demo-video-script.md. Post text: docs/fo-x-post-draft.md.

## 1. Setup (15 min, off camera)
- [ ] Quit Claude Code fully, then `cd ~/job-seeker && claude`. In `/mcp`, jobpilot-free is fine at this point; `fo` must show connected.
- [ ] `python scripts/fo_demo.py` (dry run). Copy ALL the `export` lines it prints and run them in the same terminal as Claude Code. Do this BEFORE starting Claude Code, or restart it after.
- [ ] `cp .mcp.fo-demo.json.example .mcp.json`, restart Claude Code, check `/mcp` shows BOTH `jobpilot` and `fo` connected.
- [ ] Use the fictional seeker only (Avery Demo). Never your real profile or resume on screen.
- [ ] Do Not Disturb on. Close email, Slack, Messages and anything with real names. Hide the Dock and desktop files.
- [ ] Terminal: big font (18pt+), dark theme, clear prompt. Window about 1280x720.
- [ ] Phone: your Fo iMessage thread has real history. Either start a fresh chat or record only the incoming banner. Turn on Do Not Disturb except for Messages.

## 2. Dry run, once, on camera-off
- [ ] "What are my top three matches?" shows 92, 88, 84.
- [ ] "Prepare my application for the Fabrikam role" shows the resume text and "Nothing was sent".
- [ ] "Send me my new matches through Fo": Claude calls `build_fo_message` first, then `ask_fo`. A text arrives on the phone.
- [ ] "I applied. Remind me to follow up in three days": Claude calls `record_application`, then Fo books a calendar entry. Check the calendar, then DELETE the test entry.
- [ ] If anything fails, stop and fix it before recording.
- [ ] Reset the demo data: `python scripts/fo_demo.py` again (it rebuilds the fictional folder).

## 3. Record (twice)
- [ ] Screen recording (QuickTime: File > New Screen Recording) plus a separate phone clip if needed. Mic check: say one sentence and play it back.
- [ ] Follow the script. Pause two seconds on the Claude permission prompt before approving: it is the safety moment.
- [ ] Do not say: "built from scratch", "the daily digest runs through Fo", or "text Fo to use JobPilot".
- [ ] Keep it 60-90 seconds. Keep the better take.

## 4. Before you post
- [ ] Watch the video once with sound off: can you follow it?
- [ ] No real names, numbers, emails or calendar entries visible. Pause on every frame that shows the phone or calendar.
- [ ] Remove the test calendar entry and any test texts you do not want visible.
- [ ] Repo is public and the link works in a private window: https://github.com/4asaanAI/job-seeker
- [ ] File size and length are within X's limits (about 2 min 20 s, 512 MB).

## 5. Post
- [ ] Quote-post on Wajo's MCP launch post (not a new post). Tag @wajo_ai. Attach the video. Paste the repo link.
- [ ] Then reply to your own post with the safety thread (docs/fo-x-thread-draft.md).
- [ ] Open the post once in a private window to confirm it shows as a quote of Wajo's post.
- [ ] Share the link in the Discord and submit/confirm any required form.
- [ ] Do it with hours to spare, not at 3:59pm.
