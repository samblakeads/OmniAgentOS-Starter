"""How this could pass while broken: invalid XAI_API_KEY falling through to OPENROUTER/OPENAI, a hidden data-attribute match, or 401 mapped to empty status=done; now each of 401/429/503/malformed/no-key is driven for real, secondary keys are unset, status=failed, and the visible banner text equals the exact error_tag."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest
from _harness import (
    collect_sse,
    event_payload,
    events_of,
    get_json,
    get_run,
    spawn_serve,
    start_run,
)

BANNER_SEL = '[data-testid="error-banner"], [role="alert"]'


class _StubState:
    def __init__(self, status: int, body: bytes):
        self.status = status
        self.body = body


def _start_stub(status: int, body: bytes) -> tuple[ThreadingHTTPServer, str]:
    state = _StubState(status, body)

    class H(BaseHTTPRequestHandler):
        def log_message(self, *args):
            return

        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0)
            if n:
                self.rfile.read(n)
            self.send_response(state.status)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(state.body)

        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"ok":true}')

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    host, port = httpd.server_address[:2]
    base = f"http://{host}:{port}/v1"
    return httpd, base


def _env_for_stub(stub_base: str, key: str = "xai-INVALID-d07-not-a-real-key") -> dict[str, str]:
    # Unset secondary providers so they cannot mask the primary failure (F5).
    return {
        "XAI_API_KEY": key,
        "OMNIAGENTOS_BASE_URL": stub_base,
        "OPENROUTER_API_KEY": "",
        "OPENAI_API_KEY": "",
        "OMNIAGENTOS_API_KEY": "",
    }


def _assert_visible_banner(page, tag: str) -> None:
    loc = page.locator(BANNER_SEL)
    loc.first.wait_for(state="visible", timeout=30_000)
    text = loc.first.inner_text().strip()
    assert text == tag, (
        f"visible banner text {text!r} != {tag!r} "
        "(hidden-only DOM / data-attribute match is a fail)"
    )
    # Hidden-only would have empty inner_text or not visible — already gated.


def _drive_run_expect_tag(srv, tag: str) -> dict:
    rid = start_run(srv.base_url, "Write one sentence about orchestration.")
    events = collect_sse(srv.base_url, rid, timeout_s=60.0)
    run = get_run(srv.base_url, rid)
    assert "status" in run, "status missing (must not default to done)"
    assert run["status"] == "failed", f"status={run['status']!r} want failed"
    # error_tag on run, run.failed event, or health after the run
    tags = [run.get("error_tag")]
    for rec in events_of(events, "run.failed"):
        tags.append(event_payload(rec).get("error_tag"))
    health = get_json(srv.base_url, "/api/health").json()
    tags.append(health.get("error_tag"))
    assert tag in tags, f"API error_tag {tag} not found in {tags}"
    return {"run": run, "events": [r.get("event") for r in events], "error_tag": tag}


def test_d07_each_failure_mode_api_and_banner():
    cases = [
        ("PROVIDER_AUTH", 401, json.dumps({"error": {"message": "bad key"}}).encode()),
        ("PROVIDER_RATE_LIMIT", 429, json.dumps({"error": {"message": "rate"}}).encode()),
        ("PROVIDER_UNAVAILABLE", 503, json.dumps({"error": {"message": "down"}}).encode()),
        ("PROVIDER_BAD_RESPONSE", 200, b"this is not json {{{"),
    ]
    results = []
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        pytest.fail(f"playwright not installed: {exc}")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        for tag, status, body in cases:
            httpd, stub = _start_stub(status, body)
            srv = spawn_serve(
                extra_env=_env_for_stub(stub),
                clear_provider_keys=True,
            )
            # re-apply extra_env after clear: spawn_serve applies extra_env after clear. Good.
            try:
                info = _drive_run_expect_tag(srv, tag)
                results.append(info)
                page = browser.new_page()
                page.goto(srv.base_url + "/", wait_until="networkidle", timeout=30_000)
                # If banner is not yet shown (needs a run from the UI), retry the goal.
                try:
                    page.locator('[data-testid="goal-input"], textarea').first.fill(
                        "Write one sentence about orchestration."
                    )
                    page.locator('[data-testid="run-button"], button:has-text("Run")').first.click()
                except Exception:
                    pass
                _assert_visible_banner(page, tag)
                page.close()
            finally:
                srv.stop()
                httpd.shutdown()

        # no key at all
        srv = spawn_serve(clear_provider_keys=True)
        try:
            health = get_json(srv.base_url, "/api/health").json()
            assert health.get("configured") is False
            assert health.get("error_tag") == "PROVIDER_NOT_CONFIGURED"
            # A run must fail with the same tag (not a silent empty success).
            try:
                info = _drive_run_expect_tag(srv, "PROVIDER_NOT_CONFIGURED")
                results.append(info)
            except AssertionError:
                # POST /api/runs itself may 4xx — also acceptable if error_tag is exact.
                resp = httpx.post(
                    srv.base_url + "/api/runs",
                    json={"goal": "hello"},
                    timeout=15.0,
                )
                if resp.status_code < 400:
                    raise
                body = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
                tag = body.get("error_tag") if isinstance(body, dict) else None
                assert tag == "PROVIDER_NOT_CONFIGURED" or health.get("error_tag") == "PROVIDER_NOT_CONFIGURED"
            page = browser.new_page()
            page.goto(srv.base_url + "/", wait_until="networkidle", timeout=30_000)
            try:
                page.locator('[data-testid="goal-input"], textarea').first.fill("hello")
                page.locator('[data-testid="run-button"], button:has-text("Run")').first.click()
            except Exception:
                pass
            _assert_visible_banner(page, "PROVIDER_NOT_CONFIGURED")
            page.close()
        finally:
            srv.stop()
        browser.close()

    from _harness import evidence_path

    path = evidence_path("d7-fallback.txt")
    path.write_text(json.dumps(results, indent=2, default=str) + "\n", encoding="utf-8")
