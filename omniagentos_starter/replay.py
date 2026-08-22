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
import os
import time
from pathlib import Path

from .config import replay_path
from .engine import EventBus, RunState
from .redact import redact

SCHEMA = "omniagentos-replay-1"
MAX_GAP_MS = 450
MIN_GAP_MS = 12

# The replay is paced so it reads at human speed on a stage. Nothing else wants
# that: a test wants the same event sequence as fast as the machine will produce
# it, and an operator rehearsing may want it slower. OMNIAGENTOS_REPLAY_SPEED is
# a multiplier on the pace — 20 is twenty times faster, 0.5 is half speed — so
# neither has to reach in and monkeypatch the constants above.
SPEED_ENV_VAR = "OMNIAGENTOS_REPLAY_SPEED"
DEFAULT_SPEED = 1.0
MAX_SPEED = 1000.0


def configured_speed(env: dict | None = None) -> float:
    """The pacing multiplier, from the environment, never zero or negative."""
    raw = (os.environ if env is None else env).get(SPEED_ENV_VAR, "")
    try:
        speed = float(str(raw).strip())
    except (TypeError, ValueError):
        return DEFAULT_SPEED
    if speed <= 0:
        # "Instant" is a legitimate ask; a negative or zero divisor is not.
        return MAX_SPEED
    return min(speed, MAX_SPEED)


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
    speed: float | None = None,
    sleep=asyncio.sleep,
) -> RunState:
    """Re-emit a recorded run onto `bus` at paced speed.

    Ids are re-issued by the bus, so a replay is indistinguishable to the
    dashboard from a live run — the only tell is `replay: true` on run.started.
    """
    data = load_replay(path)
    events = data.get("events") or []
    speed = configured_speed() if speed is None else speed
    run.status = "running"
    run.started_ts = time.time()
    run.replay = True
    previous_offset = 0.0
    terminal_seen = False
    for raw in events:
        # A recording is a file on disk; a file on disk is input. One malformed
        # entry must degrade to ReplayUnavailable — which the caller already
        # turns into a tagged run.failed — never to an AttributeError that kills
        # the fallback the operator reached for because something else broke.
        try:
            if not isinstance(raw, dict):
                raise TypeError("recorded event is not an object")
            offset = float(raw.get("offset_ms") or 0)
            payload = dict(raw.get("payload") or {})
            etype = str(raw.get("type") or "note")
        except (TypeError, ValueError, AttributeError) as exc:
            raise ReplayUnavailable(f"recorded run is malformed: {type(exc).__name__}") from None
        gap = max(MIN_GAP_MS, min(MAX_GAP_MS, offset - previous_offset)) / max(0.05, speed)
        previous_offset = offset
        if bus.events:  # never delay the very first event: the stage clock is watching
            await sleep(gap / 1000.0)
        if etype == "run.started":
            payload["replay"] = True
            payload["run_id"] = run.id
            run.goal = payload.get("goal", run.goal)
        elif etype in ("run.done", "run.failed"):
            payload["run_id"] = run.id
            payload["replay"] = True
        # The live paths redact at the call site; a re-recorded or hand-supplied
        # tape has no call site, so it is redacted here before it reaches a
        # browser. (EventBus.emit redacts too — this is the belt to that brace,
        # and it is what makes `path=` safe for a tape we did not audit.)
        bus.emit(etype, redact(payload))
        if etype == "run.done":
            terminal_seen = True
            run.status = "done"
            # Never hard-code the verdict. A recording of a run that the verifier
            # rejected must replay as rejected; a recording with no verdict at
            # all is not a pass either.
            run.verified = payload.get("verified") is True
            run.deliverable = str(payload.get("deliverable") or "")
            run.rounds = _int_or(payload.get("rounds"), 0)
        elif etype == "run.failed":
            terminal_seen = True
            run.status = "failed"
            run.verified = False
            run.error_tag = str(payload.get("error_tag") or "INTERNAL_ERROR")
    if not terminal_seen:
        # A tape that stops in the middle is a broken tape. Promoting it to
        # `done` with whatever deliverable happened to be there is exactly the
        # favourable default this system refuses everywhere else.
        run.status = "failed"
        run.verified = False
        run.error_tag = "REPLAY_TRUNCATED"
        run.error_message = "the recorded run has no terminal event"
        bus.emit(
            "run.failed",
            {
                "run_id": run.id,
                "error_tag": "REPLAY_TRUNCATED",
                "message": run.error_message,
                "replay": True,
                "rounds": run.rounds,
                "verified": False,
            },
        )
    run.finished_ts = time.time()
    bus.close()
    return run


def _int_or(value, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
