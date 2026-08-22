"""HTTP surface: the dashboard, the run API and the SSE event stream.

Defaults are the safe ones. The server binds 127.0.0.1; binding anywhere else
requires OMNIAGENTOS_TOKEN and then every /api/* request must carry it.
`/api/health` reports whether the provider is genuinely reachable — the flag is
set by a real probe at startup, not by the presence of an environment variable —
and it never contains a fragment of a key.
"""

from __future__ import annotations

import asyncio
import hmac
import json
import os
import time
from pathlib import Path

from fastapi import APIRouter, FastAPI, Header, Query, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from .agents import AgentError, AgentStore, safe_agent_slug
from .config import (
    MAX_ARTIFACT_CHARS,
    MAX_EXTRA_DOD,
    MAX_GOAL_CHARS,
    REPO_ROOT,
    SSE_KEEPALIVE_SECONDS,
    VERSION,
    Settings,
    assets_dir,
    skills_dir,
    static_dir,
)
from .engine import EventBus, Orchestrator, RunLimit, RunState
from .llm import LLMClient
from .redact import WorkspaceEscape, redact, register_secret
from .replay import ReplayUnavailable, replay_into, replay_metadata
from .tools import WorkspaceGuard, WorkspaceRefused, runs_root, safe_run_id

PROBE_TTL_SECONDS = 600
# A failed probe is cached far more briefly than a successful one. Ten minutes of
# "provider unreachable" after a Wi-Fi blip is ten minutes of a red dashboard in
# front of an audience, with a provider that came back thirty seconds in.
PROBE_FAILURE_TTL_SECONDS = 20
SESSION_COOKIE = "omniagentos_session"


def run_workspace_dir(workspace_dir: Path | str, run_id: str) -> Path | None:
    """Resolve ``<workspace>/runs/<run_id>`` — or nothing at all.

    The run id comes from the URL, so it is treated as hostile: reduced to the
    same alphanumeric charset the engine uses when it creates the directory, and
    then checked for containment in the runs tree. Handing an unsanitised
    segment to WorkspaceGuard and trusting the guard afterwards does not work —
    the guard polices what happens inside its root, and `..` chooses the root.
    """
    safe_id = safe_run_id(run_id)
    if not safe_id or safe_id != str(run_id):
        return None
    root = runs_root(workspace_dir)
    path = (root / safe_id).resolve()
    if path == root or not path.is_relative_to(root) or not path.is_dir():
        return None
    return path


def git_head(root: Path | None = None) -> str:
    """Best-effort HEAD sha, read from .git directly — this package never spawns a process."""
    root = Path(root or REPO_ROOT)
    git = root / ".git"
    try:
        if git.is_file():  # worktree pointer
            git = Path(git.read_text(encoding="utf-8").split("gitdir:", 1)[1].strip())
        head = (git / "HEAD").read_text(encoding="utf-8").strip()
        if not head.startswith("ref:"):
            return head[:40]
        ref = head.split(":", 1)[1].strip()
        direct = git / ref
        if direct.is_file():
            return direct.read_text(encoding="utf-8").strip()[:40]
        packed = git / "packed-refs"
        if packed.is_file():
            for line in packed.read_text(encoding="utf-8").splitlines():
                if line.endswith(" " + ref):
                    return line.split(" ", 1)[0].strip()[:40]
    except Exception:
        return ""
    return ""


def criterion_text(item) -> str:
    """One acceptance criterion, however the caller chose to phrase it.

    A plain string, or an object with `criterion` (`text`/`requirement`/`dod`
    also accepted, and an `id` alongside is ignored — the engine issues its own).
    Anything else is not a criterion and is rejected rather than quietly dropped:
    a criterion the user believes is being enforced, but isn't, is worse than an
    error message.
    """
    if isinstance(item, str):
        return item.strip()
    if isinstance(item, dict):
        for key in ("criterion", "text", "requirement", "dod"):
            value = item.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return ""


class RunRequest(BaseModel):
    goal: str = Field(min_length=1, max_length=MAX_GOAL_CHARS)
    max_rounds: int | None = None
    agent_id: str | None = Field(default=None, max_length=64)
    extra_dod: list[str | dict] = Field(default_factory=list, max_length=MAX_EXTRA_DOD)

    @field_validator("extra_dod")
    @classmethod
    def _every_entry_is_a_criterion(cls, value: list) -> list:
        for item in value:
            if not isinstance(item, (str, dict)) or not criterion_text(item):
                raise ValueError(
                    "each extra_dod entry must be a non-empty string or an object with a "
                    f"non-empty 'criterion'; got {item!r}"
                )
        return value

    def criteria(self) -> list[str]:
        return [c for c in (criterion_text(x) for x in self.extra_dod) if c]


class ProbeCache:
    """One live provider probe, shared and cached. `configured` means reachable."""

    def __init__(self, settings: Settings, transport=None):
        self.settings = settings
        self.transport = transport
        self.ok = False
        self.error_tag: str | None = settings.provider.error_tag or "PROVIDER_NOT_CONFIGURED"
        self.detail = ""
        self.checked_ts = 0.0
        self._lock = asyncio.Lock()

    async def get(self, force: bool = False) -> dict:
        async with self._lock:
            ttl = PROBE_TTL_SECONDS if self.ok else PROBE_FAILURE_TTL_SECONDS
            fresh = (time.time() - self.checked_ts) < ttl
            if not force and fresh:
                return self.as_dict()
            client = LLMClient(self.settings.provider, transport=self.transport)
            ok, tag, detail = await client.probe()
            self.ok, self.error_tag, self.detail = ok, tag, detail
            self.checked_ts = time.time()
            return self.as_dict()

    def as_dict(self) -> dict:
        return {
            "configured": bool(self.ok),
            "error_tag": self.error_tag,
            "detail": redact(self.detail)[:200],
            "checked_ts": self.checked_ts,
        }


def sse_data(event: dict) -> dict:
    """The JSON object carried on an SSE `data:` line.

    The payload is flattened to the top level (so a consumer reads
    `data.task_id`, not `data.payload.task_id`) and `payload` is kept alongside
    it for the dashboard. `id`, `ts` and `type` are canonical and always win.
    """
    payload = event.get("payload") or {}
    body = dict(payload) if isinstance(payload, dict) else {"value": payload}
    body["payload"] = payload
    # The stream's own sequence number goes on the SSE `id:` line and into
    # `event_id` — never into `id`, which belongs to whatever the payload is
    # about (a lesson, for instance). Clobbering it silently renamed a lesson.
    body["event_id"] = event["id"]
    body["ts"] = event["ts"]
    body["type"] = event["type"]
    body["run_id"] = payload.get("run_id", event.get("run_id")) if isinstance(payload, dict) else event.get("run_id")
    return body


def _sse(event: dict) -> str:
    # The last hop to the browser. `default=str` below stringifies Paths and
    # exceptions AFTER any earlier redaction ran, so the redaction has to happen
    # here, on the object that is actually about to be serialised.
    return (
        f"id: {event['id']}\n"
        f"event: {event['type']}\n"
        f"data: {json.dumps(redact(sse_data(event)), default=str)}\n\n"
    )


def create_app(settings: Settings | None = None, orchestrator: Orchestrator | None = None, transport=None) -> FastAPI:
    settings = settings or Settings.from_env()
    orch = orchestrator or Orchestrator(settings, transport=transport)
    orch.load_library(skills_dir())
    # After the library: an agent's `skills:` list is validated against it, and a
    # roster read first would disable every agent for naming packs not yet read.
    orch.load_roster(settings.agents_dir)
    store = AgentStore(settings.agents_dir)
    probe = ProbeCache(settings, transport=transport)

    app = FastAPI(title="OmniAgentOS Starter", version=VERSION, docs_url=None, redoc_url=None)
    app.state.settings = settings
    app.state.orchestrator = orch
    app.state.probe = probe
    api = APIRouter(prefix="/api")

    def _token_ok(candidate: str | None) -> bool:
        return bool(candidate) and hmac.compare_digest(str(candidate), settings.token)

    def _authorised(request: Request) -> bool:
        """Three ways to present the bind token, for three kinds of client.

        The header is the one an API client uses. The browser cannot set a header
        on an EventSource at all, so it exchanges the token once at /api/session
        for a same-origin cookie — which then rides along on the SSE connection
        and on the file-tree links. The `token=` query parameter is the
        last-resort channel for a hand-driven `curl` against the stream; it is
        accepted only on the events route, never on anything that mutates state.
        """
        header = request.headers.get("authorization", "")
        if header.startswith("Bearer ") and _token_ok(header[7:]):
            return True
        if _token_ok(request.cookies.get(SESSION_COOKIE)):
            return True
        if request.url.path.endswith("/events") and _token_ok(request.query_params.get("token")):
            return True
        return False

    @app.middleware("http")
    async def auth_and_redaction(request: Request, call_next):
        path = request.url.path
        if settings.token and path.startswith("/api/") and path != "/api/session":
            if not _authorised(request):
                # Its own tag: this is the bind token in front of the dashboard,
                # not the provider key. Reusing PROVIDER_AUTH sent the operator
                # hunting for an xAI problem that did not exist.
                return JSONResponse(
                    {"error_tag": "APP_AUTH", "message": "missing or invalid access token"}, status_code=401
                )
        try:
            return await call_next(request)
        except Exception as exc:  # a stack trace is never a user-facing string
            return JSONResponse(
                {"error_tag": "INTERNAL_ERROR", "message": redact(f"{type(exc).__name__}")}, status_code=500
            )

    @app.on_event("startup")
    async def _startup() -> None:
        # Teach the redactor about the secrets this process holds in memory, not
        # only the ones still visible in os.environ: a parent that cleared the
        # environment after start would otherwise disable redaction entirely.
        register_secret(settings.provider.api_key)
        register_secret(settings.token)
        asyncio.create_task(probe.get(force=True))

    @api.post("/session")
    async def session(request: Request) -> Response:
        """Exchange the bind token for a same-origin cookie the browser can use."""
        if not settings.token:
            return JSONResponse({"ok": True, "token_required": False})
        supplied = ""
        header = request.headers.get("authorization", "")
        if header.startswith("Bearer "):
            supplied = header[7:]
        if not supplied:
            try:
                body = await request.json()
                supplied = str((body or {}).get("token") or "")
            except Exception:
                supplied = ""
        if not _token_ok(supplied):
            return JSONResponse(
                {"error_tag": "APP_AUTH", "message": "missing or invalid access token"}, status_code=401
            )
        response = JSONResponse({"ok": True, "token_required": True})
        response.set_cookie(
            SESSION_COOKIE,
            settings.token,
            httponly=True,
            samesite="strict",
            path="/",
            max_age=12 * 3600,
        )
        return response

    # ------------------------------------------------------------------ meta
    @api.get("/health")
    async def health(
        nonce: str | None = Query(default=None, max_length=64),
        fresh: int | None = Query(default=None),
    ) -> JSONResponse:
        # `?fresh=1` re-probes now. The negative cache is deliberately short, but
        # an operator who has just fixed the network should not have to wait for
        # it at all.
        state = await probe.get(force=bool(fresh))
        lib = orch.library
        body = {
            "status": "ok",
            "version": VERSION,
            "pid": os.getpid(),
            "git_head": git_head(),
            "configured": state["configured"],
            "provider": settings.provider.provider,
            "model": settings.provider.model,
            "provider_host": settings.provider.host,
            "brand": settings.brand.as_dict(),
            "skills": lib.count,
            "agents": orch.roster.count + (1 if orch.roster.builtin else 0),
            "replay": replay_metadata(),
            "max_rounds": settings.max_rounds,
        }
        if not state["configured"]:
            # `status: ok` next to `configured: false` is the healthy twin of a
            # working provider, and every client that keys off `status` believed
            # it. The top-level word now says the same thing the flag does.
            body["status"] = "degraded"
            body["error_tag"] = state["error_tag"] or "PROVIDER_NOT_CONFIGURED"
            body["message"] = state["detail"] or "no reachable provider"
        if nonce is not None:
            body["nonce"] = redact(str(nonce))[:64]
        return JSONResponse(redact(body))

    @api.get("/skills")
    async def list_skills() -> JSONResponse:
        body = orch.library.as_dict()
        body["items"] = body["skills"]
        return JSONResponse(redact(body))

    @api.get("/lessons")
    async def list_lessons() -> JSONResponse:
        lessons = orch.memory.all_lessons()
        return JSONResponse(redact({"lessons": lessons, "items": lessons}))

    # ---------------------------------------------------------------- agents
    def _reload_roster():
        return orch.load_roster(settings.agents_dir)

    def _agent_error(exc: AgentError) -> JSONResponse:
        return JSONResponse(redact(exc.as_dict()), status_code=exc.status)

    def _with_memory(body: dict) -> dict:
        """Roster cards show how much each agent has actually learned."""
        counts = orch.memory.lesson_counts_by_agent()
        for item in body.get("agents") or []:
            item["lessons"] = counts.get(item.get("id") or "", 0)
        body["items"] = body["agents"]
        return body

    @api.get("/agents")
    async def list_agents() -> JSONResponse:
        return JSONResponse(redact(_with_memory(_reload_roster().as_dict())))

    @api.get("/agents/{slug}")
    async def get_agent(slug: str) -> JSONResponse:
        agent = _reload_roster().by_id(slug)
        if agent is None:
            return JSONResponse(
                {"error_tag": "AGENT_NOT_FOUND", "message": f"no agent {safe_agent_slug(slug)!r}"},
                status_code=404,
            )
        body = agent.as_dict()
        body["lessons"] = orch.memory.lesson_counts_by_agent().get(agent.slug, 0)
        return JSONResponse(redact(body))

    @api.post("/agents")
    async def create_agent(request: Request) -> JSONResponse:
        try:
            payload = await request.json()
        except Exception:
            return JSONResponse(
                {"error_tag": "BAD_REQUEST", "message": "the body must be a JSON object"}, status_code=400
            )
        try:
            agent = store.create(payload, library=orch.library)
        except AgentError as exc:
            return _agent_error(exc)
        _reload_roster()
        return JSONResponse(redact(agent.as_dict()), status_code=201)

    @api.put("/agents/{slug}")
    async def update_agent(slug: str, request: Request) -> JSONResponse:
        try:
            payload = await request.json()
        except Exception:
            return JSONResponse(
                {"error_tag": "BAD_REQUEST", "message": "the body must be a JSON object"}, status_code=400
            )
        try:
            agent = store.update(slug, payload, library=orch.library)
        except AgentError as exc:
            return _agent_error(exc)
        _reload_roster()
        return JSONResponse(redact(agent.as_dict()))

    @api.post("/agents/{slug}/duplicate")
    async def duplicate_agent(slug: str, request: Request) -> JSONResponse:
        try:
            payload = await request.json()
        except Exception:
            payload = {}
        try:
            agent = store.duplicate(slug, _reload_roster(), payload if isinstance(payload, dict) else {})
        except AgentError as exc:
            return _agent_error(exc)
        _reload_roster()
        return JSONResponse(redact(agent.as_dict()), status_code=201)

    @api.delete("/agents/{slug}")
    async def delete_agent(slug: str) -> JSONResponse:
        try:
            removed = store.delete(slug, _reload_roster())
        except AgentError as exc:
            return _agent_error(exc)
        _reload_roster()
        return JSONResponse({"deleted": removed})

    # ------------------------------------------------------------------ runs
    @api.post("/runs")
    async def create_run(req: RunRequest) -> JSONResponse:
        try:
            run = orch.create(req.goal, req.max_rounds, req.criteria(), agent_id=req.agent_id)
        except RunLimit as exc:
            return JSONResponse(redact({"error_tag": "RUN_LIMIT", "message": str(exc)}), status_code=429)
        except ValueError as exc:
            return JSONResponse(redact({"error_tag": "BAD_REQUEST", "message": str(exc)}), status_code=400)
        orch.start(run)
        # The goal is echoed back, and a goal is whatever the operator pasted —
        # including, on a bad day, the curl command with the key still in it.
        return JSONResponse(
            redact(
                {
                    "run_id": run.id,
                    "status": run.status,
                    "goal": run.goal,
                    "agent_id": run.agent_id,
                    "agent": {"id": run.agent.slug, "name": run.agent.name} if run.agent else None,
                }
            ),
            status_code=201,
        )

    @api.post("/demo")
    async def demo() -> JSONResponse:
        meta = replay_metadata()
        if not meta.get("available"):
            return JSONResponse(
                redact({"error_tag": "PROVIDER_NOT_CONFIGURED", "message": meta.get("reason", "no recorded run")}),
                status_code=503,
            )
        try:
            run = orch.create(meta.get("goal") or "recorded demonstration run")
        except RunLimit as exc:
            return JSONResponse(redact({"error_tag": "RUN_LIMIT", "message": str(exc)}), status_code=429)
        run.replay = True
        run.task = asyncio.create_task(_run_replay(run))
        return JSONResponse(
            redact({"run_id": run.id, "status": run.status, "replay": True, "goal": run.goal}), status_code=201
        )

    async def _run_replay(run: RunState) -> None:
        # Whatever goes wrong in here, the run must end. A background task that
        # dies quietly leaves its run "running" for ever, and two of those are
        # every concurrency slot the server has.
        # A fallback bus that is not written back onto the run is a bus nobody
        # can subscribe to: the SSE route would look at `run.bus is None`, take
        # the stored-events branch, and stream a recording that is still playing
        # somewhere else.
        if run.bus is None:
            run.bus = EventBus(run.id, orch.memory)
        try:
            await replay_into(run.bus, run)
        except Exception as exc:
            run.status = "failed"
            run.error_tag = (
                "PROVIDER_NOT_CONFIGURED" if isinstance(exc, ReplayUnavailable) else "REPLAY_FAILED"
            )
            run.error_message = redact(f"{type(exc).__name__}: {exc}")[:300]
            run.finished_ts = time.time()
            if run.bus:
                run.bus.emit(
                    "run.failed",
                    {
                        "run_id": run.id,
                        "error_tag": run.error_tag,
                        "message": run.error_message,
                        "replay": True,
                    },
                )
                run.bus.close()
        finally:
            if run.status not in ("done", "failed"):
                run.status = "failed"
                run.error_tag = run.error_tag or "REPLAY_FAILED"
                if run.bus and not run.bus.done:
                    run.bus.emit("run.failed", {"run_id": run.id, "error_tag": run.error_tag, "replay": True})
                    run.bus.close()

    @api.get("/runs")
    async def list_runs() -> JSONResponse:
        live = {r.id: r.summary() for r in orch.runs.values()}
        rows: list[dict] = []
        seen: set[str] = set()
        for row in orch.memory.list_runs():
            run_id = row.get("id")
            seen.add(run_id)
            rows.append(live.get(run_id) or {**row, "run_id": run_id})
        for run_id, summary in live.items():
            if run_id not in seen:
                rows.insert(0, summary)
        return JSONResponse(redact({"runs": rows, "items": rows}))

    @api.get("/runs/{run_id}")
    async def get_run(run_id: str) -> JSONResponse:
        run = orch.get(run_id)
        if run:
            return JSONResponse(redact(run.summary()))
        stored = orch.memory.get_run(run_id)
        if not stored:
            return JSONResponse({"error_tag": "BAD_REQUEST", "message": "unknown run"}, status_code=404)
        return JSONResponse(redact(stored))

    @api.get("/runs/{run_id}/events")
    async def run_events(
        run_id: str,
        last_event_id: int | None = Query(default=None),
        header_last_id: str | None = Header(default=None, alias="Last-Event-ID"),
    ):
        run = orch.get(run_id)
        last_id = 0
        for candidate in (header_last_id, last_event_id):
            try:
                last_id = max(last_id, int(candidate))
            except (TypeError, ValueError):
                continue

        if run is None or run.bus is None:
            stored = orch.memory.events(run_id, after_id=last_id)
            if not stored and not orch.memory.get_run(run_id):
                return JSONResponse({"error_tag": "BAD_REQUEST", "message": "unknown run"}, status_code=404)

            async def replay_stored():
                for event in stored:
                    yield _sse({**event, "run_id": run_id})

            return StreamingResponse(replay_stored(), media_type="text/event-stream", headers=_sse_headers())

        bus = run.bus
        queue, backlog = bus.subscribe(last_id)
        # A recorded run keeps emitting after run.done — the lesson it saved is
        # the last thing on the tape. Cutting the stream at the terminal event
        # means the Memory column never fills during the canned demo, so a replay
        # runs until the bus itself closes.
        stop_at_terminal = not run.replay

        async def stream():
            seen = last_id
            terminal = False
            try:
                for event in backlog:
                    seen = event["id"]
                    yield _sse(event)
                    if event["type"] in ("run.done", "run.failed") and stop_at_terminal:
                        terminal = True
                        break
                if bus.closed:
                    # Nothing more will ever arrive: a reconnect that waited on
                    # the queue here would hang until the client gave up.
                    terminal = True
                if not terminal:
                    while True:
                        try:
                            event = await asyncio.wait_for(queue.get(), timeout=SSE_KEEPALIVE_SECONDS)
                        except TimeoutError:
                            yield ": keepalive\n\n"
                            continue
                        if event is None:
                            break
                        if event["id"] <= seen:
                            continue
                        seen = event["id"]
                        yield _sse(event)
                        if event["type"] in ("run.done", "run.failed") and stop_at_terminal:
                            break
            finally:
                bus.unsubscribe(queue)

        return StreamingResponse(stream(), media_type="text/event-stream", headers=_sse_headers())

    def _workspace(run_id: str) -> tuple[WorkspaceGuard | None, dict | None]:
        """The run's sandbox, or the reason there isn't one.

        Both outcomes are a 404 to the caller, and they used to be the SAME 404:
        `{"error_tag": "BAD_REQUEST", "message": "unknown run"}`. That made a real
        run whose workspace the guard refused — a symlinked root, a root inside
        the data dir, a root too wide to contain — look exactly like a mistyped
        id. Fail-closed for an attacker either way; for an operator it is the
        difference between "you typed it wrong" and "this run's sandbox is
        broken", and only one of those is worth investigating.
        """
        run = orch.get(run_id)
        if run and run.workspace:
            return run.workspace, None
        path = run_workspace_dir(settings.workspace_dir, run_id)
        if path is None:
            # `run_workspace_dir` says no for two very different reasons: nothing
            # is there, or something IS there and it failed containment (a
            # symlinked run directory is the common shape). lexists() does not
            # follow the link, so it can tell those apart where resolve() cannot.
            safe = safe_run_id(run_id)
            candidate = runs_root(settings.workspace_dir) / safe if safe and safe == str(run_id) else None
            if candidate is not None and os.path.lexists(candidate):
                return None, {
                    "error_tag": "WORKSPACE_REFUSED",
                    "message": "the run workspace exists but is not a directory inside the runs tree",
                }
            return None, {"error_tag": "RUN_NOT_FOUND", "message": "no such run"}
        try:
            guard = WorkspaceGuard(
                path, data_dir=settings.data_dir, create=False, base=runs_root(settings.workspace_dir)
            )
        except WorkspaceRefused as exc:
            return None, {"error_tag": "WORKSPACE_REFUSED", "message": redact(str(exc))[:300]}
        except Exception as exc:
            return None, {
                "error_tag": "WORKSPACE_REFUSED",
                "message": f"the run workspace could not be opened ({type(exc).__name__})",
            }
        return guard, None

    @api.get("/runs/{run_id}/files")
    async def run_files(run_id: str) -> JSONResponse:
        guard, refusal = _workspace(run_id)
        if guard is None:
            # An empty list would hide the difference between "this run wrote
            # nothing" and "that is not a run id" — and the second one is an
            # attempt to read somewhere it should not.
            return JSONResponse(redact(refusal), status_code=404)
        return JSONResponse(redact({"run_id": run_id, "files": guard.list_files()}))

    @api.get("/runs/{run_id}/files/{file_path:path}")
    async def run_file(run_id: str, file_path: str):
        guard, refusal = _workspace(run_id)
        if guard is None:
            return JSONResponse(redact(refusal), status_code=404)
        try:
            # A worker wrote this file, from text a user supplied. It is opened in
            # a browser tab on a projector, so it goes through the redactor like
            # every other thing this process hands to a client.
            body = redact(guard.read_file(file_path))[:MAX_ARTIFACT_CHARS]
            return PlainTextResponse(body, media_type="text/plain; charset=utf-8")
        except FileNotFoundError:
            return JSONResponse({"error_tag": "BAD_REQUEST", "message": "no such file"}, status_code=404)
        except WorkspaceEscape as exc:
            # as_dict() rather than str(exc): the same shape the tool path and the
            # SSE `tool.error` event carry, so one refusal reads identically
            # wherever a client meets it.
            return JSONResponse(redact(exc.as_dict()), status_code=400)
        except OSError as exc:
            return JSONResponse(
                redact({"error_tag": "WORKSPACE_IO_ERROR", "message": type(exc).__name__}),
                status_code=400,
            )
        except Exception as exc:
            return JSONResponse(
                redact({"error_tag": getattr(exc, "error_tag", "BAD_REQUEST"), "message": str(exc)}),
                status_code=400,
            )

    app.include_router(api)

    # ---------------------------------------------------------------- static
    static = static_dir()
    if static.is_dir():
        app.mount("/static", StaticFiles(directory=str(static)), name="static")

    @app.get("/assets/{asset_path:path}")
    async def asset(asset_path: str):
        root = assets_dir().resolve()
        target = (root / asset_path).resolve()
        if not target.is_file() or not target.is_relative_to(root):
            return JSONResponse({"error_tag": "BAD_REQUEST", "message": "no such asset"}, status_code=404)
        return FileResponse(target)

    @app.get("/")
    async def index():
        page = static / "index.html"
        if not page.is_file():
            return PlainTextResponse("dashboard assets are missing from this install", status_code=500)
        return FileResponse(page, headers={"Cache-Control": "no-cache"})

    return app


def _sse_headers() -> dict:
    return {
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
        "Connection": "keep-alive",
    }
