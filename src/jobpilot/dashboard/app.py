"""JobPilot's local dashboard: a pipeline board, a review drawer with code approval, and the search profile.

    python -m jobpilot.dashboard --new-token            # once: make the login token (printed once)
    python -m jobpilot.dashboard                        # http://127.0.0.1:8770 on this Mac only
    python -m jobpilot.dashboard --host 100.x.y.z       # also on your Tailscale address, for the phone

One user, one seeker ([fo] seeker_id). Every page and API call needs the login token (a session cookie derived from it,
so rotating the token logs everyone out). Requests must name an allowed Host, API calls must carry the X-JobPilot header
and, when a browser sends Origin, it must be this site: another web page cannot drive it. Submitting an application
needs the 6-digit code from the user's authenticator app (the same secret as the Fo bridge), checked here, single use.
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import secrets
import threading
from pathlib import Path

from ..bridge import CODE_PATH, code_window, codes_locked, read_secret, write_secret

PORT = 8770
TOKEN_PATH = Path.home() / ".config" / "jobpilot" / "dashboard_token"
STATIC = Path(__file__).parent / "static"
COOKIE = "jp_session"
MAX_BODY = 200_000
JOB_ACTIONS = ("skip", "shortlist", "applied", "interview", "rejected", "offer", "withdrawn", "cancel", "blocked",
               "failed")


def session_value(token: str) -> str:
    return hmac.new(token.encode(), b"jobpilot-dashboard-session", hashlib.sha256).hexdigest()


class Dashboard:
    """The data side, separate from HTTP so tests drive it directly."""

    def __init__(self, store, sid: int, out_dir: Path, home: Path, *, tools=None, fo_factory=None,
                 code_path: Path = CODE_PATH):
        from ..server import build_tools
        self.store, self.sid, self.home, self.code_path = store, sid, home, code_path
        self.tools = tools or build_tools(store, out_dir, pinned_seeker_id=sid, operator_mode=False)
        self.fo_factory = fo_factory
        self.sends: dict[int, dict] = {}

    # ---- reads ---------------------------------------------------------------------------------------------------------
    def board(self) -> dict:
        t, sid = self.tools, self.sid
        columns: dict[str, list[dict]] = {"match": [], "started": [], "applied": [], "interview": [], "rejected": []}
        for m in self.store.best_matches(sid, 60, open_only=True):
            columns["match"].append({"posting_id": m["posting_id"], "title": m["title"], "company": m["company_name"],
                                     "score": m["fit_score"], "deadline_soon": m["deadline_soon"],
                                     "work_mode": m["work_mode"], "location": m["location"]})
        for a in self.store.applications(sid, 500):
            column = {"offer": "interview", "withdrawn": "rejected"}.get(a["status"], a["status"])
            if column in columns:
                columns[column].append({"posting_id": a["posting_id"], "title": a["title"], "company": a["company_name"],
                                        "status": a["status"], "applied_at": a["applied_at"],
                                        "response_at": a["response_at"]})
        status = t["my_status"](sid)
        return {"ok": True, "columns": columns, "status": status,
                "proposals": self.store.proposals(sid), "retry_queue": self.store.retry_items(sid),
                "followups": t["application_followups"](sid).get("items", []),
                "in_flight": self.store.in_flight(sid), "sends": {str(k): v for k, v in self.sends.items()},
                "note": "Job titles and companies come from public job boards."}

    def job(self, posting_id: int) -> dict:
        t, sid = self.tools, self.sid
        detail = t["posting_detail"](sid, posting_id)
        if not detail.get("ok"):
            return detail
        app = next((a for a in self.store.applications(sid, 500) if a["posting_id"] == posting_id), None)
        review = None
        if app is None or app["status"] == "withdrawn":
            built = t["_apply_core"](sid, posting_id, preview=True)
            review = ({k: built.get(k) for k in ("message", "answers", "resume_text", "letter_text", "route",
                                                 "job_terms_missing_from_resume")} if built.get("ok")
                      else {"error": built.get("error"), "code": built.get("code")})
        form = t["form_profile"](sid)
        return {"ok": True, "job": detail, "application": app, "review": review,
                "form": form.get("fields") if form.get("ok") else None,
                "answers": form.get("answers") if form.get("ok") else {},
                "letter": self.store.letter(sid, posting_id),
                "resumes": t["my_resumes"](sid, posting_id).get("items", [])[:5],
                "audit": self.store.audit_log(sid, posting_id, 20),
                "send": self.sends.get(posting_id)}

    # ---- writes --------------------------------------------------------------------------------------------------------
    def action(self, posting_id: int, action: str, body: dict) -> dict:
        t, sid = self.tools, self.sid
        if action not in JOB_ACTIONS:
            return {"ok": False, "code": "invalid_argument", "error": f"action must be one of {', '.join(JOB_ACTIONS)}"}
        if action in ("skip", "shortlist"):
            return t["set_job_status"](sid, posting_id, "skipped" if action == "skip" else "shortlisted")
        if action == "cancel":
            return t["cancel_application"](sid, posting_id)
        if action == "failed":                      # the user saw it fail: park it for the one retry
            return t["report_submit_failed"](sid, posting_id, str(body.get("note") or "the user saw the submit fail"))
        if action == "blocked":
            return t["report_blocked"](sid, posting_id, str(body.get("kind") or "manual"), body.get("page_url"),
                                       str(body.get("note") or ""))
        return t["record_application"](sid, posting_id, action, str(body.get("notes") or "")[:500],
                                       reason=str(body.get("reason") or "")[:300])

    def document(self, posting_id: int, kind: str, body: dict) -> dict:
        t, sid = self.tools, self.sid
        if kind == "pdf":
            return t["render_resume_pdf"](sid, posting_id)
        if kind == "letter_draft":
            return t["draft_cover_letter"](sid, posting_id)
        if kind == "letter_approve":
            return t["approve_cover_letter"](sid, posting_id, str(body.get("text") or ""))
        if kind == "followup":
            return t["draft_followup"](sid, posting_id)
        if kind == "prep":
            return t["interview_prep"](sid, posting_id)
        return {"ok": False, "code": "invalid_argument", "error": "unknown document"}

    def _code_ok(self, posting_id: int, code: str) -> dict | None:
        """None when the authenticator code is right and unused (it is then spent); else the refusal."""
        secret = read_secret(self.code_path)
        if not secret:
            return {"ok": False, "code": "not_configured",
                    "error": "approval codes are not set up: run python -m jobpilot.bridge --setup-code"}
        with self.store._lock:
            if codes_locked(self.store, self.sid):
                return {"ok": False, "code": "approval_required",
                        "error": "too many wrong codes; run python -m jobpilot.bridge --unlock-codes on the Mac"}
            step = code_window(secret, code)
            if step is None:
                self.store.record_event("bridge_code_event", "fail", self.sid, posting_id)
                return {"ok": False, "code": "approval_required", "error": "that code is wrong or expired"}
        if not self.store.spend_code_step(self.sid, posting_id, step):
            return {"ok": False, "code": "approval_required", "error": "that code was already used; wait for the next one"}
        return None

    def approve(self, posting_id: int, code: str) -> dict:
        """The user reviewed the drawer and typed their code: build the approved request and send it through Fo."""
        if self.fo_factory is None:
            return {"ok": False, "code": "not_configured", "error": "Fo is not set up here ([fo] enabled with a seeker)"}
        busy = self.store.in_flight(self.sid)
        if busy is not None:
            return {"ok": False, "code": "application_in_flight",
                    "error": f"job {busy} is still in flight; finish it (mark its outcome, blocked or cancel) first"}
        refused = self._code_ok(posting_id, code)
        if refused:
            return refused
        built = self.tools["_apply_core"](self.sid, posting_id, user_approved=True)
        if not built.get("ok"):
            return built
        self.store.audit(self.sid, posting_id, "approved", "dashboard_code")
        self._send(posting_id, built["message"])
        return {"ok": True, "posting_id": posting_id, "state": "sending",
                "note": "Sent to Fo. Fo fills and submits the form, and stops for anything not in your resume."}

    def retry(self, posting_id: int, code: str) -> dict:
        """The one retry of a parked failure, approved with a code; the identical request is sent again."""
        if self.fo_factory is None:
            return {"ok": False, "code": "not_configured", "error": "Fo is not set up here"}
        refused = self._code_ok(posting_id, code)
        if refused:
            return refused
        out = self.tools["_retry_with_code"](self.sid, posting_id)
        if out.get("ok"):
            self._send(posting_id, out["message"])
        return out

    def _send(self, posting_id: int, message: str) -> None:
        self.sends[posting_id] = {"state": "sending"}

        def run():
            # ask_fo is not idempotent: once it may have reached Fo, the outcome is unknown, so nothing is parked or
            # retried here. The job stays in flight and the user records what happened (submitted, stuck, failed).
            try:
                client = self.fo_factory()
                if client is None:
                    raise RuntimeError("no Fo transport available")
                result = client.send(message)
                from ..fo import clean
                reply = clean(result.reply or result.response, 300) if (result.reply or result.response) else ""
                self.store.audit(self.sid, posting_id, "sent", "fo_" + result.status)
                self.sends[posting_id] = {"state": result.status, "reply": reply}
            except Exception as exc:  # noqa: BLE001 - logged and shown; never retried silently
                self.store.audit(self.sid, posting_id, "sent", "unknown", f"{type(exc).__name__}: {str(exc)[:120]}")
                self.sends[posting_id] = {"state": "unknown", "error": f"{type(exc).__name__}: check Fo, then record "
                                                                         "what happened"}
        threading.Thread(target=run, name=f"dashboard-send-{posting_id}", daemon=True).start()

    def decide(self, proposal_id: int, accept: bool) -> dict:
        return self.tools["decide_proposal"](self.sid, proposal_id, accept)

    def profile(self) -> dict:
        import yaml
        from ..profiles import load_profile
        seeker = self.store.get_seeker(self.sid)
        path = Path(seeker.profile_path)
        path = path if path.is_absolute() else self.home / path
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        load_profile(path)                                      # fails loudly on a broken profile
        keys = ("headline", "location", "also_search", "search_terms", "seniority", "work_modes", "locations",
                "salary_floor", "salary_currency", "blacklist_companies", "whitelist_companies",
                "exclude_staffing_agencies", "source_weights", "min_score", "followup_days", "sources")
        from .. import vault
        form = self.tools["form_profile"](self.sid)
        folder = self.tools["_vault_folder"](self.sid)
        formal = vault.load_formal(folder)
        answers = [{"question": q, "answer": a,
                    "formal": ({"text": f["text"], "approved": bool(f.get("approved")), "warnings": f.get("warnings", [])}
                               if (f := formal.get(q)) and f.get("for") == a else None)}
                   for q, a in vault.load_answers(folder).items()]
        return {"ok": True, "profile": {k: data.get(k) for k in keys},
                "answers": answers, "not_answered_yet": form.get("not_answered_yet", []),
                "sources_available": [s["source"] for s in self.tools["available_sources"]()]}

    def save_profile(self, body: dict) -> dict:
        allowed = ("headline", "location", "also_search", "search_terms", "seniority", "work_modes", "locations",
                   "salary_floor", "salary_currency", "blacklist_companies", "whitelist_companies",
                   "exclude_staffing_agencies", "source_weights", "min_score", "followup_days")
        changes = {k: v for k, v in body.items() if k in allowed}
        return self.tools["update_my_profile"](self.sid, **changes)

    def save_answer(self, body: dict) -> dict:
        return self.tools["save_answer"](self.sid, str(body.get("question") or ""), str(body.get("answer") or ""))

    def formal(self, body: dict) -> dict:
        """Write a formal version of a saved answer, or approve the one on screen (the user has read its warnings)."""
        question = str(body.get("question") or "")
        if body.get("action") == "approve":
            return self.tools["approve_formal_answer"](self.sid, question, True)
        return self.tools["draft_formal_answer"](self.sid, question)


def make_app(dash: Dashboard, token_path: Path = TOKEN_PATH, hosts: tuple[str, ...] = ("127.0.0.1", "localhost")):
    from starlette.applications import Starlette
    from starlette.requests import Request
    from starlette.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
    from starlette.concurrency import run_in_threadpool
    from starlette.routing import Route

    allowed_hosts = {h.lower() for h in hosts}

    def _host(request: Request) -> str:
        host = request.headers.get("host", "").lower()
        return host[1:].split("]", 1)[0] if host.startswith("[") else host.rsplit(":", 1)[0]

    def _authed(request: Request) -> bool:
        token = read_secret(token_path)
        given = request.cookies.get(COOKIE, "")
        return bool(token) and hmac.compare_digest(given, session_value(token))

    def guard(handler, api: bool = True, public: bool = False):
        async def call(request: Request):
            if _host(request) not in allowed_hosts:
                return Response("Misdirected request.", status_code=421)
            origin = request.headers.get("origin")
            if origin and origin.split("://", 1)[-1].lower() != request.headers.get("host", "").lower():
                return Response("Cross-site requests are not accepted.", status_code=403)
            if not public and not _authed(request):
                return (JSONResponse({"ok": False, "code": "approval_required", "error": "log in first"}, 401)
                        if api else RedirectResponse("/login", 303))
            if api and request.method == "POST":
                if request.headers.get("x-jobpilot") != "1":
                    return JSONResponse({"ok": False, "code": "invalid_argument", "error": "missing X-JobPilot header"},
                                        403)
                if int(request.headers.get("content-length") or 0) > MAX_BODY:
                    return JSONResponse({"ok": False, "code": "invalid_argument", "error": "request too large"}, 413)
            try:
                out = await handler(request)
                if callable(out) and not isinstance(out, Response):   # blocking work (Fo, a model, the database) off the event loop
                    out = await run_in_threadpool(out)
            except (ValueError, KeyError, TypeError) as exc:
                out = {"ok": False, "code": "invalid_argument", "error": str(exc)[:200]}
            if isinstance(out, dict):
                return JSONResponse(out, headers={"Cache-Control": "no-store"})
            return out
        return call

    async def body(request: Request) -> dict:
        raw = await request.body()
        if len(raw) > MAX_BODY:
            raise ValueError("request too large")
        data = json.loads(raw or b"{}")
        if not isinstance(data, dict):
            raise ValueError("the body must be a JSON object")
        return data

    async def login_page(request: Request):
        return HTMLResponse((STATIC / "login.html").read_text(encoding="utf-8"))

    async def login(request: Request):
        form = await request.form()
        token = read_secret(token_path)
        given = str(form.get("token") or "")
        if not token or not hmac.compare_digest(given.encode(), token.encode()):
            return RedirectResponse("/login?bad=1", 303)
        response = RedirectResponse("/", 303)
        response.set_cookie(COOKIE, session_value(token), httponly=True, samesite="strict",
                            secure=request.url.scheme == "https", max_age=30 * 86400)
        return response

    async def logout(request: Request):
        response = RedirectResponse("/login", 303)
        response.delete_cookie(COOKIE)
        return response

    async def index(request: Request):
        return HTMLResponse((STATIC / "index.html").read_text(encoding="utf-8"),
                            headers={"Content-Security-Policy": "default-src 'self'; img-src 'self' data:; "
                                                                "style-src 'self' 'unsafe-inline'; frame-ancestors 'none'",
                                     "X-Frame-Options": "DENY"})

    def static(name: str, media: str):
        async def serve(request: Request):
            return FileResponse(STATIC / name, media_type=media)
        return serve

    async def api_board(request: Request):
        return dash.board

    async def api_job(request: Request):
        pid = int(request.path_params["pid"])
        return lambda: dash.job(pid)

    async def api_action(request: Request):
        pid, action, data = int(request.path_params["pid"]), request.path_params["action"], await body(request)
        return lambda: dash.action(pid, action, data)

    async def api_document(request: Request):
        pid, kind, data = int(request.path_params["pid"]), request.path_params["kind"], await body(request)
        return lambda: dash.document(pid, kind, data)

    async def api_approve(request: Request):
        pid, code = int(request.path_params["pid"]), str((await body(request)).get("code") or "")
        return lambda: dash.approve(pid, code)

    async def api_retry(request: Request):
        pid, code = int(request.path_params["pid"]), str((await body(request)).get("code") or "")
        return lambda: dash.retry(pid, code)

    async def api_proposal(request: Request):
        proposal, accept = int(request.path_params["proposal"]), bool((await body(request)).get("accept"))
        return lambda: dash.decide(proposal, accept)

    async def api_profile(request: Request):
        if request.method == "GET":
            return dash.profile
        data = await body(request)
        return lambda: dash.save_profile(data)

    async def api_answer(request: Request):
        data = await body(request)
        return lambda: dash.save_answer(data)

    async def api_formal(request: Request):
        data = await body(request)
        return lambda: dash.formal(data)

    routes = [
        Route("/login", guard(login_page, api=False, public=True), methods=["GET"]),
        Route("/login", guard(login, api=False, public=True), methods=["POST"]),
        Route("/logout", guard(logout, api=False, public=True), methods=["POST"]),
        Route("/", guard(index, api=False)),
        Route("/app.js", guard(static("app.js", "text/javascript"), api=False)),
        Route("/style.css", guard(static("style.css", "text/css"), api=False)),
        Route("/manifest.webmanifest", guard(static("manifest.webmanifest", "application/manifest+json"), api=False,
                                             public=True)),
        Route("/sw.js", guard(static("sw.js", "text/javascript"), api=False, public=True)),
        Route("/icon.svg", guard(static("icon.svg", "image/svg+xml"), api=False, public=True)),
        Route("/api/board", guard(api_board)),
        Route("/api/job/{pid:int}", guard(api_job)),
        Route("/api/job/{pid:int}/action/{action}", guard(api_action), methods=["POST"]),
        Route("/api/job/{pid:int}/document/{kind}", guard(api_document), methods=["POST"]),
        Route("/api/job/{pid:int}/approve", guard(api_approve), methods=["POST"]),
        Route("/api/job/{pid:int}/retry", guard(api_retry), methods=["POST"]),
        Route("/api/proposal/{proposal:int}", guard(api_proposal), methods=["POST"]),
        Route("/api/profile", guard(api_profile), methods=["GET", "POST"]),
        Route("/api/answer", guard(api_answer), methods=["POST"]),
        Route("/api/answer/formal", guard(api_formal), methods=["POST"]),
    ]
    return Starlette(routes=routes)


def new_token(path: Path = TOKEN_PATH) -> str:
    token = secrets.token_urlsafe(24)
    write_secret(path, token, replace=True)
    return token


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="JobPilot dashboard on this Mac (and your Tailscale address)")
    parser.add_argument("--host", default=None, help="also listen on this address (your Tailscale IP or name)")
    parser.add_argument("--port", type=int, default=PORT)
    parser.add_argument("--seeker", type=int, default=None)
    parser.add_argument("--new-token", action="store_true", help="make a new login token and print it once")
    args = parser.parse_args(argv)
    if args.new_token:
        print("Dashboard login token (keep it private; it is not shown again):\n" + new_token())
        return 0
    if read_secret(TOKEN_PATH) is None:
        print("dashboard: no login token yet; run python -m jobpilot.dashboard --new-token")
        return 2
    from ..config import load_config
    from ..paths import jobpilot_home
    from ..scheduled import make_fo_client
    from ..store.db import Store
    cfg = load_config()
    sid = args.seeker or cfg.fo_seeker_id
    if sid is None:
        print("dashboard: pass --seeker N or set [fo] seeker_id")
        return 2
    home = jobpilot_home()
    store = Store(Path(os.environ.get("JOBPILOT_DB", home / "data" / "jobpilot.db")))
    store.migrate()
    store.fresh_days = cfg.fresh_days
    store.get_seeker(sid)
    fo_factory = (lambda: make_fo_client(cfg)) if cfg.fo_enabled and cfg.fo_seeker_id == sid else None
    dash = Dashboard(store, sid, cfg.out_dir, home, fo_factory=fo_factory)
    hosts = ("127.0.0.1", "localhost") + ((args.host.lower(),) if args.host else ())
    app = make_app(dash, hosts=hosts)
    import uvicorn
    bind = args.host or "127.0.0.1"
    print(f"JobPilot dashboard: http://{bind}:{args.port}/" + ("  (also http://127.0.0.1)" if args.host else ""))
    if args.host:
        # one server per address: the Mac itself, and the Tailscale address for the phone
        threading.Thread(target=lambda: uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning"),
                         daemon=True).start()
    uvicorn.run(app, host=bind, port=args.port, log_level="warning")
    return 0
