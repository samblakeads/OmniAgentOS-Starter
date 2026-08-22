"""Replay of a real recorded run.

`data/replay-run.json` is not a mock-up: it is a genuine run captured by
`scripts/record_replay.py` against a live provider and redacted. It powers
`omniagentos demo`, the first-run panel when no key is configured, and the stage
fallback if the provider hiccups mid-demo — the same event stream, the same UI,
paced so it reads at human speed.
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

from .config import replay_path
from .engine import EventBus, RunState

SCHEMA = "omniagentos-replay-1"
MAX_GAP_MS = 450
MIN_GAP_MS = 12


class ReplayUnavailable(Exception):
    """No recorded run is bundled (or it is unreadable)."""


def load_replay(path: Path | str | None = None) -> dict:
    p = Path(path) if path else replay_path()
    if not p.is_file():
        raise ReplayUnavailable(f"no recorded run at {p.name}")
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ReplayUnavailable(f"recorded run is unreadable: {type(exc).__name__}") from None
    if not isinstance(data, dict) or not data.get("events"):
        raise ReplayUnavailable("recorded run contains no events")
    return data


def replay_metadata(path: Path | str | None = None) -> dict:
    try:
        data = load_replay(path)
    except ReplayUnavailable as exc:
        return {"available": False, "reason": str(exc)}
    return {
        "available": True,
        "schema": data.get("schema", SCHEMA),
        "goal": data.get("goal", ""),
        "events": len(data.get("events") or []),
        "recorded_ts": data.get("recorded_ts"),
        "source_run_id": data.get("run_id"),
    }


async def replay_into(
    bus: EventBus,
    run: RunState,
    path: Path | str | None = None,
    speed: float = 1.0,
    sleep=asyncio.sleep,
) -> RunState:
    """Re-emit a recorded run onto `bus` at paced speed.

    Ids are re-issued by the bus, so a replay is indistinguishable to the
    dashboard from a live run — the only tell is `replay: true` on run.started.
    """
    data = load_replay(path)
    events = data.get("events") or []
    run.status = "running"
    run.started_ts = time.time()
    run.replay = True
    previous_offset = 0.0
    for raw in events:
        offset = float(raw.get("offset_ms") or 0)
        gap = max(MIN_GAP_MS, min(MAX_GAP_MS, offset - previous_offset)) / max(0.05, speed)
        previous_offset = offset
        if bus.events:  # never delay the very first event: the stage clock is watching
            await sleep(gap / 1000.0)
        payload = dict(raw.get("payload") or {})
        etype = str(raw.get("type") or "note")
        if etype == "run.started":
            payload["replay"] = True
            payload["run_id"] = run.id
            run.goal = payload.get("goal", run.goal)
        elif etype in ("run.done", "run.failed"):
            payload["run_id"] = run.id
            payload["replay"] = True
        bus.emit(etype, payload)
        if etype == "run.done":
            run.status = "done"
            run.verified = True
            run.deliverable = str(payload.get("deliverable") or "")
            run.rounds = int(payload.get("rounds") or 0)
        elif etype == "run.failed":
            run.status = "failed"
            run.error_tag = str(payload.get("error_tag") or "INTERNAL_ERROR")
    if run.status == "running":
        # a recording that never terminated is still not left in limbo
        run.status = "done"
        bus.emit("run.done", {"run_id": run.id, "deliverable": run.deliverable, "replay": True, "rounds": run.rounds})
    run.finished_ts = time.time()
    bus.close()
    return run
