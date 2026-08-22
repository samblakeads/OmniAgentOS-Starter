"""How this could pass while broken: serve refusing to start without a key, or POST /api/demo emitting a single canned 'done' without four role events; now keys are unset, health is configured:false + PROVIDER_NOT_CONFIGURED, demo SSE carries planner/worker/critic/verifier, and a real screenshot is saved."""

from __future__ import annotations

import httpx
import pytest

from _harness import (
    collect_sse,
    event_type,
    evidence_path,
    get_json,
    spawn_serve,
    write_json,
)


def test_d13_nokey_serve_health_demo_screenshot():
    srv = spawn_serve(clear_provider_keys=True)
    screenshot = evidence_path("d13-nokey.png")
    try:
        health = get_json(srv.base_url, "/api/health").json()
        assert health.get("configured") is False, (
            f"configured must be false with no keys, got {health!r}"
        )
        assert health.get("error_tag") == "PROVIDER_NOT_CONFIGURED"

        resp = httpx.post(srv.base_url + "/api/demo", json={}, timeout=30.0)
        assert resp.status_code in (200, 201, 202), (
            f"POST /api/demo -> HTTP {resp.status_code}: {resp.text[:400]}"
        )
        payload = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
        rid = (payload.get("id") or payload.get("run_id") or payload.get("demo_id")) if isinstance(payload, dict) else None
        if not rid:
            runs = httpx.get(srv.base_url + "/api/runs", timeout=15.0).json()
            items = runs.get("items") or runs.get("runs") or runs
            if isinstance(items, dict):
                items = items.get("items") or []
            assert items, "POST /api/demo did not return a run id and GET /api/runs is empty"
            rid = items[0].get("id") or items[0].get("run_id")
        rid = str(rid)
        events = collect_sse(srv.base_url, rid, timeout_s=60.0)
        types = {event_type(e) for e in events}
        needed = {"planner.plan", "worker.finished", "critic.verdict", "verifier.verdict"}
        assert needed <= types, (
            f"demo replay must emit the full 4-role timeline, missing {needed - types}; got {types}"
        )

        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            pytest.fail(f"playwright not installed: {exc}")

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(srv.base_url + "/", wait_until="networkidle", timeout=30_000)
            # Trigger demo from the UI as well if a control exists; screenshot the resulting state.
            for sel in (
                '[data-testid="watch-demo"]',
                'button:has-text("Watch the demo")',
                'button:has-text("demo")',
            ):
                loc = page.locator(sel)
                if loc.count():
                    try:
                        loc.first.click(timeout=2000)
                    except Exception:
                        pass
                    break
            page.wait_for_timeout(1500)
            page.screenshot(path=str(screenshot), full_page=True)
            browser.close()

        assert screenshot.is_file() and screenshot.stat().st_size > 1000
        write_json("d13-nokey.json", {"run_id": rid, "event_types": sorted(types)})
    finally:
        srv.stop()
