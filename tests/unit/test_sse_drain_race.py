"""The SSE stream must not drop events a run emitted while it was being read.

CI found this on windows-latest/py3.11 only: the demo replay's SSE stream came
back without `run.done`. It was not slowness — it was a window. The stream
yielded its backlog, then asked `bus.closed`, and a run that had finished during
that yield answered True, so the loop returned WITHOUT draining the queue that
already held the rest of the run. On a fast machine the backlog was almost
always complete and the window never opened; a slow runner landed in it.

These tests close the bus at exactly that moment on purpose, so the window is
reproduced by construction rather than by luck.
"""

from __future__ import annotations

import json

import pytest
from conftest import Script, make_orchestrator, provider_config
from fastapi.testclient import TestClient

from omniagentos_starter import api as api_module
from omniagentos_starter.api import create_app
from omniagentos_starter.config import Settings
from omniagentos_starter.engine import EventBus

TAPE = [
    ("run.started", {"goal": "recorded goal"}),
    ("planner.plan", {"dod": [], "tasks": []}),
    ("worker.delta", {"task_id": "t1", "text": "hi"}),
    ("critic.verdict", {"pass": True, "verdicts": []}),
    ("verifier.verdict", {"verified": True}),
    ("run.done", {"deliverable": "recorded deliverable", "verified": True}),
]


# ------------------------------------------------------------- the bus itself
def test_a_subscription_records_whether_the_bus_was_already_closed():
    live = EventBus("r1")
    open_sub = live.subscribe()
    assert open_sub.closed is False, "the bus was open when this subscription was made"

    live.close()
    late_sub = live.subscribe()
    assert late_sub.closed is True, "this subscriber missed the sentinel and must not wait for one"


def test_closing_after_you_subscribe_still_hands_you_the_sentinel():
    bus = EventBus("r1")
    sub = bus.subscribe()
    bus.emit("run.started", {})
    bus.emit("run.done", {})
    bus.close()

    drained = []
    while True:
        item = sub.queue.get_nowait()
        if item is None:
            break
        drained.append(item["type"])
    assert drained == ["run.started", "run.done"], drained
    assert sub.closed is False, "closed-at-subscribe is a snapshot, not a live flag"


# ------------------------------------------------------ the stream, at the API
def _app(tmp_path):
    settings = Settings(
        host="127.0.0.1",
        port=0,
        data_dir=tmp_path / "var",
        workspace_dir=tmp_path / "ws",
        agents_dir=tmp_path / "agents",
        provider=provider_config(configured=False, api_key="", provider="none", model="", base_url=""),
    )
    orch = make_orchestrator(settings, Script())
    return create_app(settings=settings, orchestrator=orch), orch


def _finish_during_backlog(monkeypatch, run, remaining):
    """Emit the rest of the run and close the bus during the FIRST backlog yield.

    That is the precise interleaving the CI failure hit: the run reached its end
    while the response generator was between the backlog snapshot and its next
    decision.
    """
    real_sse = api_module._sse
    state = {"fired": False}

    def sse(event):
        rendered = real_sse(event)
        if not state["fired"]:
            state["fired"] = True
            for etype, payload in remaining:
                run.bus.emit(etype, payload)
            run.bus.close()
        return rendered

    monkeypatch.setattr(api_module, "_sse", sse)
    return state


@pytest.mark.parametrize("replay", [True, False])
def test_a_run_that_finishes_mid_stream_still_delivers_its_terminal_event(tmp_path, monkeypatch, replay):
    app, orch = _app(tmp_path)
    with TestClient(app) as client:
        run = orch.create("recorded goal")
        run.replay = replay
        run.bus.emit(*TAPE[0])  # the only event in the backlog

        state = _finish_during_backlog(monkeypatch, run, TAPE[1:])

        events = []
        with client.stream("GET", f"/api/runs/{run.id}/events") as response:
            assert response.status_code == 200
            for line in response.iter_lines():
                if line.startswith("data:"):
                    events.append(json.loads(line[5:].strip()))

    assert state["fired"], "setup: the run must have finished during the backlog yield"
    types = [e["type"] for e in events]
    assert types[0] == "run.started"
    assert types[-1] == "run.done", f"the stream stopped before the terminal event: {types}"
    assert "verifier.verdict" in types
    assert events[-1]["payload"]["deliverable"] == "recorded deliverable"


def test_a_reconnect_after_the_run_ended_returns_instead_of_hanging(tmp_path):
    """The case the closed-check exists for: no sentinel is coming for us."""
    app, orch = _app(tmp_path)
    with TestClient(app) as client:
        run = orch.create("recorded goal")
        for etype, payload in TAPE:
            run.bus.emit(etype, payload)
        run.bus.close()

        events = []
        with client.stream("GET", f"/api/runs/{run.id}/events") as response:
            for line in response.iter_lines():
                if line.startswith("data:"):
                    events.append(json.loads(line[5:].strip()))
    assert [e["type"] for e in events] == [t for t, _ in TAPE]
