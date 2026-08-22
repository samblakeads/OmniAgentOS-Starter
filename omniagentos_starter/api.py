"""HTTP surface: the dashboard, the run API and the SSE event stream.

Defaults are the safe ones. The server binds 127.0.0.1; binding anywhere else
requires OMNIAGENTOS_TOKEN and then every /api/* request must carry it.
`/api/health` reports whether the provider is genuinely reachable — the flag is
set by a real probe at startup, not by the presence of an environment variable —
and it never contains a fragment of a key.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path

from fastapi import APIRouter, FastAPI, Header, Query, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .config import (
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
from .redact import redact
from .replay import ReplayUnavailable, replay_into, replay_metadata
from .tools import WorkspaceGuard

PROBE_TTL_SECONDS = 600


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
    """Accept an extra_dod entry as a plain string or as {"criterion": "..."}."""
    if isinstance(item, str):
        return item.strip()
    if isinstance(item, dict):
        for key in ("criterion", "text", "requirement", "dod"):
            value = item.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return str(item or "").strip()


class RunRequest(BaseModel):
    goal: str = Field(min_length=1, max_length=MAX_GOAL_CHARS)
    max_rounds: int | None = None
    extra_dod: list[str | dict] = Field(default_factory=list, max_length=MAX_EXTRA_DOD)

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
            fresh = (time.time() - self.checked_ts) < PROBE_TTL_SECONDS
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
    return (
        f"id: {event['id']}\n"
        f"event: {event['type']}\n"
        f"data: {json.dumps(sse_data(event), default=str)}\n\n"
    )


def create_app(settings: Settings | None = None, orchestrator: Orchestrator | None = None, transport=None) -> FastAPI:
    settings = settings or Settings.from_env()
    orch = orchestrator or Orchestrator(settings, transport=transport)
    orch.load_library(skills_dir())
    probe = ProbeCache(settings, transport=transport)

    app = FastAPI(title="OmniAgentOS Starter", version=VERSION, docs_url=None, redoc_url=None)
    app.state.settings = settings
    app.state.orchestrator = orch
    app.state.probe = probe
    api = APIRouter(prefix="/api")

    @app.middleware("http")
    async def auth_and_redaction(request: Request, call_next):
        if settings.token and request.url.path.startswith("/api/"):
            supplied = request.headers.get("authorization", "")
            if supplied != f"Bearer {settings.token}":
                return JSONResponse(
                    {"error_tag": "PROVIDER_AUTH", "message": "missing or invalid bearer token"}, status_code=401
                )
        try:
            return await call_next(request)
        except Exception as exc:  # a stack trace is never a user-facing string
            return JSONResponse(
                {"error_tag": "INTERNAL_ERROR", "message": redact(f"{type(exc).__name__}")}, status_code=500
            )

    @app.on_event("startup")
    async def _startup() -> None:
        asyncio.create_task(probe.get(force=True))

    # ------------------------------------------------------------------ meta
    @api.get("/health")
    async def health(nonce: str | None = Query(default=None, max_length=64)) -> JSONResponse:
        state = await probe.get()
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
            "replay": replay_metadata(),
            "max_rounds": settings.max_rounds,
        }
        if not state["configured"]:
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

    # ------------------------------------------------------------------ runs
    @api.post("/runs")
    async def create_run(req: RunRequest) -> JSONResponse:
        try:
            run = orch.create(req.goal, req.max_rounds, req.criteria())
        except RunLimit as exc:
            return JSONResponse({"error_tag": "RUN_LIMIT", "message": str(exc)}, status_code=429)
        except ValueError as exc:
            return JSONResponse({"error_tag": "BAD_REQUEST", "message": str(exc)}, status_code=400)
        orch.start(run)
        return JSONResponse({"run_id": run.id, "status": run.status, "goal": run.goal}, status_code=201)

    @api.post("/demo")
    async def demo() -> JSONResponse:
        meta = replay_metadata()
        if not meta.get("available"):
            return JSONResponse(
                {"error_tag": "PROVIDER_NOT_CONFIGURED", "message": meta.get("reason", "no recorded run")},
                status_code=503,
            )
        try:
            run = orch.create(meta.get("goal") or "recorded demonstration run")
        except RunLimit as exc:
            return JSONResponse({"error_tag": "RUN_LIMIT", "message": str(exc)}, status_code=429)
        run.replay = True
        run.task = asyncio.create_task(_run_replay(run))
        return JSONResponse({"run_id": run.id, "status": "running", "replay": True, "goal": run.goal}, status_code=201)

    async def _run_replay(run: RunState) -> None:
        try:
            await replay_into(run.bus or EventBus(run.id, orch.memory), run)
        except ReplayUnavailable as exc:
            run.status = "failed"
            run.error_tag = "PROVIDER_NOT_CONFIGURED"
            if run.bus:
                run.bus.emit("run.failed", {"error_tag": "PROVIDER_NOT_CONFIGURED", "message": str(exc)})
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

        async def stream():
            seen = last_id
            terminal = False
            try:
                for event in backlog:
                    seen = event["id"]
                    yield _sse(event)
                    if event["type"] in ("run.done", "run.failed"):
                        terminal = True
                        break
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
                        if event["type"] in ("run.done", "run.failed"):
                            break
            finally:
                bus.unsubscribe(queue)

        return StreamingResponse(stream(), media_type="text/event-stream", headers=_sse_headers())

    def _workspace(run_id: str) -> WorkspaceGuard | None:
        run = orch.get(run_id)
        if run and run.workspace:
            return run.workspace
        path = Path(settings.workspace_dir) / "runs" / run_id
        if not path.is_dir():
            return None
        try:
            return WorkspaceGuard(path, data_dir=settings.data_dir, create=False)
        except Exception:
            return None

    @api.get("/runs/{run_id}/files")
    async def run_files(run_id: str) -> JSONResponse:
        guard = _workspace(run_id)
        return JSONResponse({"run_id": run_id, "files": guard.list_files() if guard else []})

    @api.get("/runs/{run_id}/files/{file_path:path}")
    async def run_file(run_id: str, file_path: str):
        guard = _workspace(run_id)
        if guard is None:
            return JSONResponse({"error_tag": "BAD_REQUEST", "message": "unknown run"}, status_code=404)
        try:
            return PlainTextResponse(guard.read_file(file_path))
        except FileNotFoundError:
            return JSONResponse({"error_tag": "BAD_REQUEST", "message": "no such file"}, status_code=404)
        except Exception as exc:
            return JSONResponse(
                {"error_tag": getattr(exc, "error_tag", "BAD_REQUEST"), "message": redact(str(exc))}, status_code=400
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
