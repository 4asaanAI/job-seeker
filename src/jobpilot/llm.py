"""Amazon Bedrock brain for JobPilot.

Replaces the ``claude -p`` subprocess when ``[llm] provider = "bedrock"``.
Two jobs:

* ``BedrockBrain.answer`` runs one Telegram turn: the model gets the same
  JobPilot MCP tools the Claude path had (the same ``jobpilot.server``
  process, pinned to one seeker through its environment, so identity still
  comes from the Telegram update and never from the model), and loops
  model -> tool calls -> model until it writes a reply.
* ``bedrock_scorer`` is a drop-in for ``score_runner.claude_scorer``.

Credentials come from the normal AWS chain (the EC2 instance role on the
server); nothing is stored here. Only models the account can actually invoke
belong in config: Anthropic and OpenAI models are not usable on Bedrock
credits for this account.
"""
from __future__ import annotations

import asyncio
import json
import sys
import threading
import time
from pathlib import Path

DEFAULT_REGION = "ap-south-1"
DEFAULT_BOT_MODEL = "moonshotai.kimi-k2.5"
DEFAULT_FALLBACK_MODEL = "mistral.mistral-large-3-675b-instruct"
DEFAULT_SCORING_MODEL = "qwen.qwen3-235b-a22b-2507-v1:0"

MAX_ROUNDS = 24             # model calls per turn; a resume build needs ~8
TURN_BUDGET_SECONDS = 600   # same ceiling the claude subprocess had
TOOL_TIMEOUT_SECONDS = 300  # run_my_search can take minutes
MAX_TOOL_RESULT_CHARS = 30_000
MAX_OUTPUT_TOKENS = 4096
HISTORY_MESSAGES = 40       # kept per user between turns

# Errors worth one retry on the fallback model. Anything else (bad request,
# validation) would fail the same way there.
FALLBACK_ERRORS = {
    "ThrottlingException", "ServiceUnavailableException", "ModelNotReadyException",
    "InternalServerException", "ModelTimeoutException", "ModelErrorException",
    "AccessDeniedException",
}


class BrainError(RuntimeError):
    pass


def make_client(region: str = DEFAULT_REGION):
    import boto3
    from botocore.config import Config
    return boto3.client("bedrock-runtime", region_name=region, config=Config(
        read_timeout=300, connect_timeout=10, retries={"max_attempts": 3, "mode": "adaptive"}))


def _error_code(exc: Exception) -> str:
    return str(getattr(exc, "response", {}).get("Error", {}).get("Code") or type(exc).__name__)


def _text(message: dict) -> str:
    return "".join(b["text"] for b in message.get("content", []) if isinstance(b.get("text"), str))


def _clean(message: dict) -> dict:
    """Keep only text/toolUse/toolResult blocks. Reasoning blocks are model-
    specific and one model's cannot be replayed to another (the fallback)."""
    keep = [b for b in message.get("content", [])
            if any(k in b for k in ("text", "toolUse", "toolResult"))]
    return {"role": message["role"], "content": keep or [{"text": "(no text)"}]}


def tool_specs(tools) -> list[dict]:
    specs = []
    for t in tools:
        schema = (getattr(t, "input_schema", None) or getattr(t, "inputSchema", None)
                  or {"type": "object", "properties": {}})
        specs.append({"toolSpec": {"name": t.name,
                                   "description": (t.description or t.name)[:4000],
                                   "inputSchema": {"json": schema}}})
    return specs


def trim_history(messages: list[dict], limit: int = HISTORY_MESSAGES) -> list[dict]:
    """Last ``limit`` messages, starting on a plain user message so no
    toolResult is left without the toolUse that asked for it."""
    tail = messages[-limit:]
    for i, m in enumerate(tail):
        if m["role"] == "user" and not any("toolResult" in b for b in m["content"]):
            return tail[i:]
    return []


def _result_text(result) -> str:
    parts = []
    for block in getattr(result, "content", None) or []:
        text = getattr(block, "text", None)
        parts.append(text if isinstance(text, str) else json.dumps(
            getattr(block, "model_dump", lambda: str(block))(), default=str))
    if not parts and getattr(result, "structured_content", None) is not None:
        parts.append(json.dumps(result.structured_content, default=str))
    text = "\n".join(parts).strip() or "(no output)"
    if len(text) > MAX_TOOL_RESULT_CHARS:
        text = text[:MAX_TOOL_RESULT_CHARS] + "\n[... output truncated]"
    return text


class BedrockBrain:
    def __init__(self, state: Path, bot_model: str = DEFAULT_BOT_MODEL,
                 fallback_model: str | None = DEFAULT_FALLBACK_MODEL,
                 region: str = DEFAULT_REGION, client=None, mcp_client_factory=None,
                 log=None):
        self.bot_model, self.fallback_model = bot_model, fallback_model
        self.client = client or make_client(region)
        self._mcp_factory = mcp_client_factory or self._stdio_client
        self._log = log or (lambda msg: sys.stderr.write(msg + "\n"))
        self.history_dir = state / "history"
        self.history_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    # --- history -----------------------------------------------------------
    def _history_path(self, uid: int) -> Path:
        return self.history_dir / f"{uid}.json"

    def load_history(self, uid: int, mode: str) -> list[dict]:
        try:
            data = json.loads(self._history_path(uid).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        return data.get("messages", []) if data.get("mode") == mode else []

    def save_history(self, uid: int, mode: str, messages: list[dict]) -> None:
        with self._lock:
            self._history_path(uid).write_text(json.dumps(
                {"mode": mode, "messages": trim_history(messages)}), encoding="utf-8")

    def reset(self, uid: int) -> None:
        self._history_path(uid).unlink(missing_ok=True)

    # --- model ---------------------------------------------------------------
    def _converse(self, model: str, system: str, messages: list[dict], tools: list[dict]) -> dict:
        kwargs = {"modelId": model, "system": [{"text": system}], "messages": messages,
                  "inferenceConfig": {"maxTokens": MAX_OUTPUT_TOKENS}}
        if tools:
            kwargs["toolConfig"] = {"tools": tools}
        return self.client.converse(**kwargs)

    def _call(self, state: dict, system: str, messages: list[dict], tools: list[dict]) -> dict:
        try:
            return self._converse(state["model"], system, messages, tools)
        except Exception as exc:  # noqa: BLE001 - classified below
            code = _error_code(exc)
            fallback = self.fallback_model
            if code in FALLBACK_ERRORS and fallback and state["model"] != fallback:
                self._log(f"bedrock {state['model']} failed ({code}); using {fallback}")
                state["model"] = fallback
                return self._converse(fallback, system, messages, tools)
            raise BrainError(f"{code}: {exc}") from exc

    # --- tools ---------------------------------------------------------------
    @staticmethod
    def _stdio_client(env: dict[str, str], cwd: str):
        from fastmcp import Client
        from fastmcp.client.transports import StdioTransport
        return Client(StdioTransport(sys.executable, ["-m", "jobpilot.server"], env=env, cwd=cwd))

    async def _turn(self, env: dict[str, str], cwd: str, system: str,
                    history: list[dict], prompt: str) -> tuple[str, list[dict]]:
        messages = [*history, {"role": "user", "content": [{"text": prompt}]}]
        state = {"model": self.bot_model}
        deadline = time.monotonic() + TURN_BUDGET_SECONDS
        async with self._mcp_factory(env, cwd) as mcp:
            tools = tool_specs(await mcp.list_tools())
            for _ in range(MAX_ROUNDS):
                if time.monotonic() > deadline:
                    break
                response = await asyncio.to_thread(self._call, state, system, messages, tools)
                message = _clean(response["output"]["message"])
                messages.append(message)
                uses = [b["toolUse"] for b in message["content"] if "toolUse" in b]
                if not uses:
                    return _text(message), messages
                results = []
                for use in uses:
                    try:
                        result = await mcp.call_tool(use["name"], use.get("input") or {},
                                                     raise_on_error=False,
                                                     timeout=TOOL_TIMEOUT_SECONDS)
                        text, failed = _result_text(result), bool(getattr(result, "is_error", False))
                    except Exception as exc:  # noqa: BLE001 - reported to the model
                        text, failed = f"Tool failed: {type(exc).__name__}: {exc}"[:2000], True
                    results.append({"toolResult": {"toolUseId": use["toolUseId"],
                                                   "content": [{"text": text}],
                                                   "status": "error" if failed else "success"}})
                messages.append({"role": "user", "content": results})
        # Out of rounds or time: ask for an answer from what was gathered.
        messages.append({"role": "user", "content": [{"text": (
            "Stop calling tools now. Reply to me with what you have so far, and say "
            "plainly what could not be finished.")}]})
        response = await asyncio.to_thread(self._call, state, system, messages, tools)
        message = _clean(response["output"]["message"])
        messages.append(message)
        return _text(message), messages

    def answer(self, uid: int, mode: str, system: str, env: dict[str, str],
               cwd: str, prompt: str) -> str:
        history = self.load_history(uid, mode)
        try:
            reply, messages = asyncio.run(self._turn(env, cwd, system, history, prompt))
        except Exception:
            if not history:
                raise
            # A corrupt or incompatible history must not wedge the user.
            self.reset(uid)
            reply, messages = asyncio.run(self._turn(env, cwd, system, [], prompt))
        self.save_history(uid, mode, messages)
        return reply


def bedrock_scorer(model: str = DEFAULT_SCORING_MODEL, region: str = DEFAULT_REGION,
                   client=None, fallback_model: str | None = DEFAULT_FALLBACK_MODEL):
    """Drop-in for ``score_runner.claude_scorer``: prompt in, text out, no tools."""
    client = client or make_client(region)

    def ask(prompt: str) -> str:
        messages = [{"role": "user", "content": [{"text": prompt}]}]
        for candidate in [m for m in (model, fallback_model) if m]:
            try:
                out = client.converse(modelId=candidate, messages=messages,
                                      inferenceConfig={"maxTokens": 8000, "temperature": 0.2})
                return _text(out["output"]["message"])
            except Exception as exc:  # noqa: BLE001
                if _error_code(exc) not in FALLBACK_ERRORS or candidate == fallback_model:
                    raise RuntimeError(f"bedrock {candidate}: {_error_code(exc)}: {exc}") from exc
        raise RuntimeError("no scoring model available")
    ask.label = f"bedrock-batch:{model}"
    return ask
