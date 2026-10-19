# Fo bridge: talk to JobPilot through Fo by text, email or phone

Optional, for one user. Design and threat model: `docs/superpowers/specs/2026-10-19-fo-bridge-design.md`.

## Set up (once)

1. Make the bridge key: `python -m jobpilot.bridge --rotate`. It prints the key once.
2. Make the approval secret: `python -m jobpilot.bridge --setup-code`, then add the printed key to your authenticator app
   (Google Authenticator: + > Enter a setup key, time based). Fo needs a code from that app before it submits anything.
3. Publish only the bridge path through Tailscale Funnel: `tailscale funnel --bg --set-path /mcp http://127.0.0.1:8765/mcp`.
   Check `tailscale serve status` shows nothing else.
4. Start it: `python -m jobpilot.bridge --host <machine>.<tailnet>.ts.net`, or load `scripts/com.jobpilot.bridge.plist.example`
   as a LaunchAgent so it restarts by itself.
5. Tell Fo: "connect my MCP server JobPilot at https://<machine>.<tailnet>.ts.net/mcp". Enter the key through the secure link
   Fo gives you, never in chat.

## Use

Text Fo: "find me product jobs", "show my matches", "apply to 689", "what's my tracker link". For an application, Fo sends a
review draft, then asks for the current code from your authenticator app before it submits.

## Turn it off

- Pause: create `data/bridge.off` (requests get "turned off"); delete it to resume.
- Cut off Fo: `python -m jobpilot.bridge --rotate` (the old key stops working at once).
- Stop: `tailscale funnel --set-path /mcp off`, and unload the LaunchAgent.

Every call is logged in `data/bridge.log` (never the key or a code). The Mac must be awake for Fo to reach JobPilot.
