"""The HTTP surface: health, auth, run creation, SSE, files, demo replay."""

from __future__ import annotations

import json

import httpx
import pytest
from conftest import TEST_KEY, Script, provider_config
from fastapi.testclient import TestClient

from omniagentos_starter import replay as replay_module
from omniagentos_starter.api import create_app, git_head
from omniagentos_starter.config import Settings

GOAL = "Write exactly 3 ad headlines for an AI video tool"


def build(settings: Settings, script: Script | None = None) -> TestClient:
    script = script or Script()
    app = create_app(settings, transport=httpx.MockTransport(script.handler))
    client = TestClient(app)
    client.script = script
    return client


def sse_events(client: TestClient, run_id: str, headers=None, until_terminal: bool = False) -> list[dict]:
    """Read a run's SSE stream to the end.

    The server closes the stream when the run is over, so reading to EOF is the
    wait — there is no sleep to tune and no window to lose a race in. Pass
    `until_terminal=True` when the test's subject is the whole run: it turns "the
    stream ended early" into a failure that names what actually arrived, instead
    of an IndexError or an assertion about the wrong event.
    """
    events = []
    with client.stream("GET", f"/api/runs/{run_id}/events", headers=headers or {}) as response:
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-cache"
        assert response.headers["x-accel-buffering"] == "no"
        for line in response.iter_lines():
            if line.startswith("data:"):
                events.append(json.loads(line[5:].strip()))
    if until_terminal:
        types = [e["type"] for e in events]
        assert types and types[-1] in ("run.done", "run.failed"), (
            f"the stream for {run_id} ended before a terminal event; got {types}"
        )
    return events


# ------------------------------------------------------------------- health
def test_health_reports_a_probed_provider_and_never_a_key(settings, monkeypatch):
    monkeypatch.setenv("XAI_API_KEY", TEST_KEY)
    with build(settings) as client:
        body = client.get("/api/health").json()
    assert body["configured"] is True
    assert body["provider"] == "xai"
    assert body["model"] == "grok-4.3"
    assert body["pid"] > 0
    assert "git_head" in body
    assert body["brand"]["name"] == "OmniRogue"
    assert body["brand"]["logo_url"].endswith(".png")
    blob = json.dumps(body)
    assert TEST_KEY not in blob
    for fragment in (TEST_KEY[:8], TEST_KEY[-8:]):
        assert fragment not in blob


def test_health_echoes_a_nonce_for_process_identification(settings):
    with build(settings) as client:
        body = client.get("/api/health", params={"nonce": "abc123"}).json()
    assert body["nonce"] == "abc123"


def test_health_is_not_configured_when_the_provider_rejects_the_key(settings):
    app = create_app(settings, transport=httpx.MockTransport(lambda r: httpx.Response(401, text="bad key")))
    with TestClient(app) as client:
        body = client.get("/api/health").json()
    assert body["configured"] is False
    assert body["error_tag"] == "PROVIDER_AUTH"


def test_health_without_any_key_says_so_and_still_serves(tmp_path):
    settings = Settings(
        data_dir=tmp_path / "var",
        workspace_dir=tmp_path / "ws",
        provider=provider_config(configured=False, api_key="", provider="none", model="", base_url=""),
    )
    with build(settings) as client:
        body = client.get("/api/health").json()
        assert body["configured"] is False
        assert body["error_tag"] == "PROVIDER_NOT_CONFIGURED"
        assert client.get("/").status_code in (200, 500)


def test_git_head_is_read_without_spawning_a_process():
    head = git_head()
    assert head == "" or len(head) >= 7


# --------------------------------------------------------------------- auth
def test_a_token_makes_every_api_route_require_a_bearer(settings):
    settings.token = "s3cret-token"
    with build(settings) as client:
        assert client.get("/api/health").status_code == 401
        ok = client.get("/api/health", headers={"Authorization": "Bearer s3cret-token"})
        assert ok.status_code == 200
        assert client.get("/").status_code in (200, 500), "the dashboard itself stays reachable"


# --------------------------------------------------------------------- runs
def test_a_run_streams_the_whole_production_line(settings):
    with build(settings) as client:
        created = client.post("/api/runs", json={"goal": GOAL})
        assert created.status_code == 201
        run_id = created.json()["run_id"]
        events = sse_events(client, run_id)
    types = [e["type"] for e in events]
    assert types[0] == "run.started"
    assert types[-1] == "run.done"
    for role_event in ("planner.plan", "worker.delta", "critic.verdict", "verifier.verdict"):
        assert role_event in types
    assert all(events[i]["event_id"] < events[i + 1]["event_id"] for i in range(len(events) - 1))
    assert all("payload" in e and e["type"] for e in events)


def test_last_event_id_resumes_without_repeating(settings):
    with build(settings) as client:
        run_id = client.post("/api/runs", json={"goal": GOAL}).json()["run_id"]
        first = sse_events(client, run_id)
        resumed = sse_events(client, run_id, headers={"Last-Event-ID": str(first[2]["event_id"])})
    assert [e["event_id"] for e in resumed] == [e["event_id"] for e in first[3:]]


def test_an_empty_goal_is_a_bad_request(settings):
    with build(settings) as client:
        assert client.post("/api/runs", json={"goal": "   "}).status_code in (400, 422)


def test_the_concurrency_cap_answers_429(settings):
    with build(settings) as client:
        orch = client.app.state.orchestrator
        orch.create("first goal")
        orch.create("second goal")
        refused = client.post("/api/runs", json={"goal": GOAL})
        assert refused.status_code == 429
        assert refused.json()["error_tag"] == "RUN_LIMIT"


def test_run_files_are_listed_and_readable_but_never_outside_the_workspace(settings):
    plan = {
        "dod": [{"id": "d1", "criterion": "files exist"}],
        "tasks": [{"id": "t1", "title": "write", "skill_id": "general-assistant", "instruction": "x", "writes_files": True, "needs_tools": []}],
    }
    script = Script(plan=plan, worker_text="=== FILE: note.md ===\nhello file\n=== END FILE ===\ndone")
    with build(settings, script) as client:
        run_id = client.post("/api/runs", json={"goal": GOAL}).json()["run_id"]
        sse_events(client, run_id)
        listing = client.get(f"/api/runs/{run_id}/files").json()
        assert [f["path"] for f in listing["files"]] == ["note.md"]
        assert client.get(f"/api/runs/{run_id}/files/note.md").text.strip() == "hello file"
        assert client.get(f"/api/runs/{run_id}/files/../../../etc/passwd").status_code in (400, 404)


def test_an_unknown_run_is_a_404(settings):
    with build(settings) as client:
        assert client.get("/api/runs/nope").status_code == 404


# ------------------------------------------------------------------- skills
def test_the_skills_endpoint_reports_the_scan(settings):
    with build(settings) as client:
        body = client.get("/api/skills").json()
    assert body["count"] == len(body["skills"])
    assert body["integrity"]["parsed"] == body["integrity"]["files_on_disk"]


# --------------------------------------------------------------------- demo
@pytest.fixture
def recorded_run(tmp_path, monkeypatch):
    path = tmp_path / "replay-run.json"
    path.write_text(
        json.dumps(
            {
                "schema": "omniagentos-replay-1",
                "run_id": "recorded1",
                "goal": "recorded goal",
                "events": [
                    {"id": 1, "offset_ms": 0, "type": "run.started", "payload": {"goal": "recorded goal"}},
                    {"id": 2, "offset_ms": 10, "type": "planner.plan", "payload": {"dod": [], "tasks": []}},
                    {"id": 3, "offset_ms": 20, "type": "worker.delta", "payload": {"task_id": "t1", "text": "hi"}},
                    {"id": 4, "offset_ms": 30, "type": "critic.verdict", "payload": {"pass": True, "verdicts": []}},
                    {"id": 5, "offset_ms": 40, "type": "verifier.verdict", "payload": {"verified": True}},
                    {"id": 6, "offset_ms": 50, "type": "run.done", "payload": {"deliverable": "recorded deliverable"}},
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(replay_module, "replay_path", lambda: path)
    # The replay is paced for a stage; a test wants the same events as fast as
    # the machine will make them. This is the shipped knob, not a reach into the
    # module's constants, so the test exercises what an operator would use.
    monkeypatch.setenv(replay_module.SPEED_ENV_VAR, "1000")
    return path


def test_the_demo_replays_a_real_recorded_run_without_a_provider(tmp_path, recorded_run):
    settings = Settings(
        data_dir=tmp_path / "var",
        workspace_dir=tmp_path / "ws",
        provider=provider_config(configured=False, api_key="", provider="none", model="", base_url=""),
    )
    with build(settings) as client:
        created = client.post("/api/demo")
        assert created.status_code == 201
        assert created.json()["replay"] is True
        # Reading the stream to EOF IS the wait: the server ends it when the
        # replay ends. No sleep, and nothing to be slow enough to miss.
        events = sse_events(client, created.json()["run_id"], until_terminal=True)
    types = [e["type"] for e in events]
    assert types[0] == "run.started" and types[-1] == "run.done"
    assert "verifier.verdict" in types
    assert events[-1]["payload"]["deliverable"] == "recorded deliverable"


def test_the_demo_says_so_plainly_when_no_recording_is_bundled(settings, monkeypatch, tmp_path):
    monkeypatch.setattr(replay_module, "replay_path", lambda: tmp_path / "missing.json")
    with build(settings) as client:
        refused = client.post("/api/demo")
    assert refused.status_code == 503
    assert refused.json()["error_tag"] == "PROVIDER_NOT_CONFIGURED"


# ------------------------------------------------------- acceptance criteria
def test_extra_dod_accepts_plain_strings(settings):
    """What the dashboard posts."""
    with build(settings) as client:
        created = client.post(
            "/api/runs",
            json={"goal": GOAL, "extra_dod": ["Every headline is under 40 characters", "Mentions the price"]},
        )
        assert created.status_code == 201
        run = client.app.state.orchestrator.get(created.json()["run_id"])
    assert run.extra_dod == ["Every headline is under 40 characters", "Mentions the price"]


def test_extra_dod_accepts_criterion_objects(settings):
    """What the oracle posts — same meaning, different shape."""
    with build(settings) as client:
        created = client.post(
            "/api/runs",
            json={
                "goal": GOAL,
                "extra_dod": [
                    {"criterion": "must contain the exact phrase 'PRODUCTION LINE' in caps"},
                    {"id": "x9", "criterion": "under 60 words"},
                ],
            },
        )
        assert created.status_code == 201
        run = client.app.state.orchestrator.get(created.json()["run_id"])
    assert run.extra_dod == [
        "must contain the exact phrase 'PRODUCTION LINE' in caps",
        "under 60 words",
    ]


def test_extra_dod_accepts_the_two_shapes_mixed(settings):
    with build(settings) as client:
        created = client.post(
            "/api/runs", json={"goal": GOAL, "extra_dod": ["a plain one", {"criterion": "an object one"}]}
        )
        assert created.status_code == 201
        run = client.app.state.orchestrator.get(created.json()["run_id"])
    assert run.extra_dod == ["a plain one", "an object one"]


@pytest.mark.parametrize(
    "bad",
    [[42], [None], [[]], [{}], [{"id": "x1"}], [""], ["   "], [{"criterion": ""}], [{"criterion": 7}]],
)
def test_a_criterion_that_is_not_a_criterion_is_refused_not_dropped(settings, bad):
    """Silently dropping it would leave the user believing it is being enforced."""
    with build(settings) as client:
        refused = client.post("/api/runs", json={"goal": GOAL, "extra_dod": bad})
    assert refused.status_code == 422, refused.text


def test_operator_criteria_are_labelled_for_the_quality_gate(settings):
    with build(settings) as client:
        created = client.post("/api/runs", json={"goal": GOAL, "extra_dod": ["it rhymes"]})
        run_id = created.json()["run_id"]
        events = sse_events(client, run_id)
    plan = next(e for e in events if e["type"] == "planner.plan")
    operator = [c for c in plan["dod"] if c["source"] == "operator"]
    assert [c["criterion"] for c in operator] == ["it rhymes"]


def test_the_dashboard_offers_the_criteria_box_and_credits_it_to_you():
    """The control the criteria are typed into, and how the gate attributes them."""
    from omniagentos_starter.config import static_dir

    index = (static_dir() / "index.html").read_text(encoding="utf-8")
    app_js = (static_dir() / "app.js").read_text(encoding="utf-8")
    assert 'data-testid="extra-dod-input"' in index
    assert "Acceptance criteria (the Critic enforces these; the Worker doesn't see them)" in index
    assert "extra_dod" in app_js
    assert '"from you"' in app_js
