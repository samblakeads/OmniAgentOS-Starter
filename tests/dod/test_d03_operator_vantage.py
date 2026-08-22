"""How this could pass while broken: static HTML role cards, a 10ms click-spinner, and a non-empty placeholder deliverable with a broken logo; now busy is gated on POST 2xx, roles come from parsed SSE types, logo bytes hash-match, deliverable binds to run.done, and timestamps have a >500ms gap."""

from __future__ import annotations

import time

import httpx
import pytest

from _harness import (
    PLACEHOLDER_DELIVERABLES,
    collect_sse,
    event_payload,
    event_type,
    evidence_path,
    events_of,
    logo_png,
    live_xai_base_url_ok,
    require_live,
    sha256_bytes,
    spawn_serve,
    ts_of,
    write_json,
)

D3_GOAL = "Write two sentences on why a planner-plus-critic loop beats a single chatbox."
BUSY_SEL = '[data-testid="run-busy"]'
DELIVERABLE_SEL = '[data-testid="deliverable"]'
RUN_BTN_SEL = '[data-testid="run-button"], button:has-text("Run")'
GOAL_SEL = '[data-testid="goal-input"], textarea'


def test_d03_playwright_real_child_real_sse():
    require_live()
    assert live_xai_base_url_ok()

    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        pytest.fail(f"playwright not installed in the repo .venv: {exc}")

    srv = spawn_serve()
    screenshot = evidence_path("d3-vantage.png")
    try:
        logo = logo_png()
        logo_sha = sha256_bytes(logo.read_bytes())

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(srv.base_url + "/", wait_until="networkidle", timeout=30_000)

            img = page.locator('img[alt="OmniRogue"]')
            img.wait_for(state="visible", timeout=10_000)
            natural = page.evaluate(
                """el => ({w: el.naturalWidth, h: el.naturalHeight, src: el.getAttribute('src')})""",
                img.element_handle(),
            )
            assert natural["w"] > 100, f"logo naturalWidth={natural['w']} (broken/empty src)"
            src = natural["src"]
            assert src, "logo src empty"
            if src.startswith("/"):
                src_url = srv.base_url + src
            elif src.startswith("http"):
                src_url = src
            else:
                src_url = srv.base_url + "/" + src.lstrip("./")
            img_bytes = httpx.get(src_url, timeout=15.0).content
            assert sha256_bytes(img_bytes) == logo_sha, (
                "logo src bytes sha256 != assets/omnirogue-logo.png"
            )

            busy = page.locator(BUSY_SEL)
            # Hold POST /api/runs until we have observed click-without-busy.
            released = {"ok": False}

            def handle_route(route):
                req = route.request
                if req.method == "POST" and "/api/runs" in req.url and "/events" not in req.url:
                    # Pause: click has happened, request is in flight, not yet 2xx.
                    page.wait_for_timeout(400)
                    visible_before = _is_busy_visible(page, busy)
                    if visible_before:
                        route.abort()
                        raise AssertionError(
                            "busy indicator visible before POST /api/runs 2xx (F4 click-spinner)"
                        )
                    route.continue_()
                    released["ok"] = True
                    return
                route.continue_()

            page.route("**/api/runs", handle_route)
            page.locator(GOAL_SEL).first.fill(D3_GOAL)

            # Click Run. Busy must not be visible on click alone.
            if _is_busy_visible(page, busy):
                raise AssertionError("busy already visible before click")
            page.locator(RUN_BTN_SEL).first.click()
            time.sleep(0.15)
            # After click, before 2xx (route handler also checks). If the
            # handler already continued, busy MAY now be visible — that is OK.
            # The handler asserted the pre-2xx window.

            page.wait_for_function(
                """() => {
                  const el = document.querySelector('[data-testid="run-busy"]');
                  if (!el) return false;
                  const st = getComputedStyle(el);
                  return st.display !== 'none' && st.visibility !== 'hidden'
                         && st.opacity !== '0' && !el.hasAttribute('hidden');
                }""",
                timeout=60_000,
            )

            # Collect SSE from the real child (not DOM card text).
            # Discover run id from the POST response via performance or /api/runs.
            runs = httpx.get(srv.base_url + "/api/runs", timeout=15.0).json()
            items = runs.get("items") or runs.get("runs") or runs
            if isinstance(items, dict):
                items = items.get("items") or [items]
            assert items, "GET /api/runs empty after POST"
            rid = str(items[0].get("id") or items[0].get("run_id"))
            events = collect_sse(srv.base_url, rid, timeout_s=180.0)
            types = {event_type(e) for e in events}
            needed = {"planner.plan", "worker.finished", "critic.verdict", "verifier.verdict"}
            assert needed <= types, (
                f"role evidence must be parsed SSE event types {needed}, "
                f"not static card text; got {types}"
            )

            done = events_of(events, "run.done")
            assert done, "missing run.done SSE"
            deliverable = event_payload(done[-1]).get("deliverable")
            assert isinstance(deliverable, str)
            assert deliverable.strip() not in PLACEHOLDER_DELIVERABLES

            ui_text = page.locator(DELIVERABLE_SEL).inner_text(timeout=30_000)
            assert deliverable.strip() in ui_text or ui_text.strip() == deliverable.strip(), (
                "deliverable panel is not bound to run.done payload"
            )
            assert ui_text.strip() not in PLACEHOLDER_DELIVERABLES

            # Inter-event gaps: strictly increasing ts, >=1 gap >500ms (F4+F12).
            markers = []
            for typ in ("planner.plan", "worker.delta", "worker.finished", "critic.verdict", "verifier.verdict"):
                evs = events_of(events, typ)
                if evs:
                    markers.append((typ, ts_of(evs[0])))
            markers.sort(key=lambda x: x[1])
            assert len(markers) >= 3, f"not enough timestamped role events: {markers}"
            gaps = [markers[i][1] - markers[i - 1][1] for i in range(1, len(markers))]
            for g in gaps:
                assert g > 0, f"timestamps not strictly increasing: {markers}"
            assert any(g > 0.5 for g in gaps), (
                f"all inter-event gaps <=500ms (single late burst / faked spinner): {gaps}"
            )

            page.wait_for_function(
                """() => {
                  const el = document.querySelector('[data-testid="run-busy"]');
                  if (!el) return true;
                  const st = getComputedStyle(el);
                  return st.display === 'none' || st.visibility === 'hidden'
                         || st.opacity === '0' || el.hasAttribute('hidden');
                }""",
                timeout=30_000,
            )

            page.screenshot(path=str(screenshot), full_page=True)
            browser.close()

        write_json(
            "d3-vantage.json",
            {"run_id": rid, "event_types": sorted(types), "screenshot": str(screenshot)},
        )
        assert screenshot.is_file() and screenshot.stat().st_size > 1000
    finally:
        srv.stop()


def _is_busy_visible(page, busy) -> bool:
    try:
        if busy.count() == 0:
            return False
        return bool(
            page.evaluate(
                """() => {
                  const el = document.querySelector('[data-testid="run-busy"]');
                  if (!el) return false;
                  const st = getComputedStyle(el);
                  if (st.display === 'none' || st.visibility === 'hidden' || st.opacity === '0')
                      return false;
                  if (el.hasAttribute('hidden') || el.getAttribute('aria-hidden') === 'true')
                      return false;
                  const r = el.getBoundingClientRect();
                  return r.width > 0 && r.height > 0;
                }"""
            )
        )
    except Exception:
        return False
