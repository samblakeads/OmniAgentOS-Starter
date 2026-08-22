"""Regression tests for the API/config/redaction findings of the Grok review.

The theme is the last hop. A payload that was redacted three layers ago is not
redacted if the thing actually written to the socket was built afterwards, and a
token scheme that a browser physically cannot use is the same as no dashboard.
"""

from __future__ import annotations

import json
import time

import pytest
from conftest import TEST_KEY, Script, make_orchestrator, provider_config

from omniagentos_starter import api as api_module
from omniagentos_starter.api import SESSION_COOKIE, ProbeCache, create_app
from omniagentos_starter.config import Brand, ProviderConfig, Settings
from omniagentos_starter.redact import PLACEHOLDER, register_secret
from omniagentos_starter.tools import UNSET, WorkspaceRefused, workspace_for_run

GOAL = "Write exactly 3 ad headlines"


def _client(settings, script=None):
    from fastapi.testclient import TestClient

    script = script or Script()
    orch = make_orchestrator(settings, script)
    return TestClient(create_app(settings=settings, orchestrator=orch)), script


# --------------------------------------------------------------- B2-F1 (BLOCKER)
def test_the_sse_stream_redacts_every_payload_it_serialises(settings):
    from omniagentos_starter.api import _sse

    register_secret(TEST_KEY)
    line = _sse(
        {
            "id": 3,
            "run_id": "r1",
            "ts": 1.0,
            "type": "tool.error",
            "payload": {"reason": f"Authorization: Bearer {TEST_KEY}"},
        }
    )
    assert TEST_KEY not in line
    assert PLACEHOLDER in line


# --------------------------------------------------------------- B2-F3 (BLOCKER)
def test_the_create_run_echo_redacts_the_goal(settings):
    client, _ = _client(settings)
    with client:
        register_secret(TEST_KEY)
        created = client.post("/api/runs", json={"goal": f"debug this: curl -H 'Bearer {TEST_KEY}'"})
        assert created.status_code == 201
        assert TEST_KEY not in created.text


# --------------------------------------------------------------- B2-F2 (BLOCKER)
def test_a_downloaded_workspace_file_is_redacted(settings, tmp_path):
    plan = {
        "dod": [{"id": "d1", "criterion": "ok"}],
        "tasks": [
            {"id": "t1", "title": "w", "skill_id": "general-assistant", "instruction": "x", "writes_files": True}
        ],
    }
    body = f"=== FILE: leak.md ===\nAuthorization: Bearer {TEST_KEY}\n=== END FILE ===\ndone"
    client, _ = _client(settings, Script(plan=plan, worker_text=body))
    with client:
        register_secret(TEST_KEY)
        run_id = client.post("/api/runs", json={"goal": GOAL}).json()["run_id"]
        for _ in range(200):
            if client.get(f"/api/runs/{run_id}").json().get("status") in ("done", "failed"):
                break
            time.sleep(0.01)
        listing = client.get(f"/api/runs/{run_id}/files")
        assert listing.status_code == 200
        downloaded = client.get(f"/api/runs/{run_id}/files/leak.md")
        assert downloaded.status_code == 200
        assert TEST_KEY not in downloaded.text
        assert PLACEHOLDER in downloaded.text


# --------------------------------------------------------------- B2-F4 (BLOCKER)
def _token_settings(tmp_path) -> Settings:
    return Settings(
        host="127.0.0.1",
        port=0,
        data_dir=tmp_path / "var",
        workspace_dir=tmp_path / "ws",
        provider=provider_config(),
        brand=Brand(name="t", logo_url="/assets/x.png"),
        token="webinar-token-demo",
    )


def test_an_eventsource_can_authenticate_without_a_header(tmp_path):
    """EventSource cannot set Authorization. A cookie and an SSE-only query token can."""
    settings = _token_settings(tmp_path)
    client, _ = _client(settings)
    with client:
        run_id = client.post(
            "/api/runs", json={"goal": GOAL}, headers={"Authorization": "Bearer webinar-token-demo"}
        ).json()["run_id"]

        # No credential at all is still refused — and with its own tag.
        anonymous = client.get(f"/api/runs/{run_id}/events")
        assert anonymous.status_code == 401
        assert anonymous.json()["error_tag"] == "APP_AUTH"

        # The query token is accepted on the stream only.
        streamed = client.get(f"/api/runs/{run_id}/events?token=webinar-token-demo")
        assert streamed.status_code == 200
        assert streamed.headers["content-type"].startswith("text/event-stream")

        # ...and not on anything that mutates state.
        assert client.post("/api/runs?token=webinar-token-demo", json={"goal": GOAL}).status_code == 401


def test_the_session_endpoint_exchanges_a_token_for_a_usable_cookie(tmp_path):
    settings = _token_settings(tmp_path)
    client, _ = _client(settings)
    with client:
        assert client.post("/api/session", json={"token": "wrong"}).status_code == 401
        opened = client.post("/api/session", json={"token": "webinar-token-demo"})
        assert opened.status_code == 200
        assert SESSION_COOKIE in client.cookies
        # The cookie now carries every /api/* call, including the ones a browser
        # makes without any JavaScript in the loop (SSE, file links).
        assert client.get("/api/health").status_code == 200
        run_id = client.post("/api/runs", json={"goal": GOAL}).json()["run_id"]
        assert client.get(f"/api/runs/{run_id}/events").status_code == 200


def test_the_dashboard_itself_never_needs_the_token(tmp_path):
    settings = _token_settings(tmp_path)
    client, _ = _client(settings)
    with client:
        assert client.get("/").status_code in (200, 500)


# -------------------------------------------------------------- B2-F9 (REQUIRED)
@pytest.mark.asyncio
async def test_health_status_tracks_configured_and_a_failure_is_not_cached_for_ten_minutes(tmp_path, monkeypatch):
    settings = Settings(
        host="127.0.0.1",
        port=0,
        data_dir=tmp_path / "var",
        workspace_dir=tmp_path / "ws",
        provider=ProviderConfig(configured=False, provider="none", model="", base_url=""),
    )

    class _Down:
        def __init__(self, *a, **k):
            pass

        async def probe(self):
            return False, "PROVIDER_UNAVAILABLE", "dns failed"

    class _Up:
        def __init__(self, *a, **k):
            pass

        async def probe(self):
            return True, None, "probe 200"

    monkeypatch.setattr(api_module, "LLMClient", _Down)
    cache = ProbeCache(settings)
    assert (await cache.get(force=True))["configured"] is False

    monkeypatch.setattr(api_module, "LLMClient", _Up)
    # The negative result is cached far more briefly than a positive one...
    assert api_module.PROBE_FAILURE_TTL_SECONDS <= 30 < api_module.PROBE_TTL_SECONDS
    # ...and an operator who has just fixed the network never waits for it.
    assert (await cache.get(force=True))["configured"] is True


def test_health_does_not_say_ok_when_the_provider_is_unreachable(tmp_path):
    settings = Settings(
        host="127.0.0.1",
        port=0,
        data_dir=tmp_path / "var",
        workspace_dir=tmp_path / "ws",
        provider=ProviderConfig(
            configured=False, provider="none", model="", base_url="", error_tag="PROVIDER_NOT_CONFIGURED"
        ),
    )
    client, _ = _client(settings)
    with client:
        body = client.get("/api/health").json()
        assert body["configured"] is False
        assert body["status"] != "ok"
        assert body["error_tag"] == "PROVIDER_NOT_CONFIGURED"


# ---------------------------------------------------------- B2-F10 (RECOMMENDED)
def test_forgetting_data_dir_refuses_the_workspace_instead_of_allowing_it(tmp_path):
    with pytest.raises(WorkspaceRefused):
        workspace_for_run(tmp_path / "ws", "run1")
    assert workspace_for_run(tmp_path / "ws", "run1", data_dir=tmp_path / "var")
    assert workspace_for_run(tmp_path / "ws2", "run1", data_dir=None)
    assert isinstance(UNSET, object)


def test_redact_coerces_paths_and_bytes_instead_of_passing_them_through(monkeypatch):
    from pathlib import Path

    from omniagentos_starter.redact import redact

    monkeypatch.setenv("XAI_API_KEY", "xai-STAGEKEY-ABCDEFGHIJKLMNOP")
    out = redact({"artifact": Path("/tmp/xai-STAGEKEY-ABCDEFGHIJKLMNOP.md")})
    assert "xai-STAGEKEY-ABCDEFGHIJKLMNOP" not in json.dumps(out, default=str)
    out2 = redact(b"Authorization: Bearer xai-STAGEKEY-ABCDEFGHIJKLMNOP")
    assert "xai-STAGEKEY-ABCDEFGHIJKLMNOP" not in out2


def test_a_short_bind_token_is_still_redacted(monkeypatch):
    from omniagentos_starter.redact import clear_registered_secrets, redact_text

    clear_registered_secrets()
    monkeypatch.setenv("OMNIAGENTOS_TOKEN", "tok1234")
    assert "tok1234" not in redact_text("Authorization: Bearer tok1234")


def test_contains_secret_also_knows_the_shapes():
    from omniagentos_starter.redact import clear_registered_secrets, contains_secret

    clear_registered_secrets()
    assert contains_secret({"argv": ["drill.py", "--token", "Bearer abcdefgh12345678"]}) is True
    assert contains_secret({"argv": ["drill.py", "--goal", "hello"]}) is False
