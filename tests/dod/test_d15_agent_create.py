"""How this could pass while broken: a UI card that appears from a client-side-only form submit with no server write, or an /api/agents that lists an in-memory fake; now the created agent must exist as a real agents/<slug>.md file on disk (in an isolated tmp AgentStore root) with matching front-matter, survive a page reload, be listed by a real GET /api/agents on the same child, round-trip through duplicate+edit, and refuse DELETE on a builtin with 403."""

from __future__ import annotations

import httpx
import pytest
from _harness import (
    create_agent,
    find_agent_file,
    find_agent_file_by_name,
    first_real_skill,
    live_xai_base_url_ok,
    parse_agent_file,
    require_live,
    spawn_serve,
    tmp_agents_root,
    write_json,
)

AGENTS_LIST_SEL = '[data-testid="agents-list"]'
AGENT_CARD_SEL = '[data-testid="agent-card"]'
AGENT_CREATE_SEL = '[data-testid="agent-create"]'
AGENT_NAME_SEL = '[data-testid="agent-name"]'
AGENT_TITLE_SEL = '[data-testid="agent-title"]'
AGENT_PERSONA_SEL = '[data-testid="agent-persona"]'
AGENT_SAVE_SEL = '[data-testid="agent-save"]'

AGENT_NAME = "Riley D15 Test"
AGENT_TITLE = "Meal-Prep Support"
AGENT_PERSONA = (
    "Riley helps busy parents plan affordable, high-protein meal prep in under "
    "30 minutes. Warm, practical, never pushy."
)


def test_d15_agent_create_edit_duplicate_delete_via_browser():
    require_live()
    assert live_xai_base_url_ok()

    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        pytest.fail(f"playwright not installed in the repo .venv: {exc}")

    skill_slug, _skill_sha = first_real_skill()
    agents_root = tmp_agents_root()
    srv = spawn_serve(extra_env={"OMNIAGENTOS_AGENTS_ROOT": str(agents_root)})
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(srv.base_url + "/", wait_until="networkidle", timeout=30_000)

            page.locator(AGENT_CREATE_SEL).first.click()
            page.locator(AGENT_NAME_SEL).first.fill(AGENT_NAME)
            page.locator(AGENT_TITLE_SEL).first.fill(AGENT_TITLE)
            page.locator(AGENT_PERSONA_SEL).first.fill(AGENT_PERSONA)
            skill_cb = page.locator(f'[data-testid="agent-skill-{skill_slug}"]')
            skill_cb.wait_for(state="visible", timeout=10_000)
            skill_cb.check()
            page.locator(AGENT_SAVE_SEL).first.click()

            card = page.locator(AGENT_CARD_SEL).filter(has_text=AGENT_NAME)
            card.first.wait_for(state="visible", timeout=15_000)

            # File exists on disk under the ISOLATED tmp AgentStore root, not
            # the real shipped agents/ tree (OMNIAGENTOS_AGENTS_ROOT above).
            # Located by parsing YAML front-matter `name:` EXACTLY — never a
            # substring/text-contains scan (that false-matched agents/README.md
            # once its prose happened to mention the test agent's first name).
            # README.md and _builtin/ are excluded from this lookup outright.
            agent_files_before = [
                p
                for p in agents_root.rglob("*.md")
                if p.name.lower() != "readme.md" and "_builtin" not in p.relative_to(agents_root).parts
            ]
            assert agent_files_before, f"no agent .md files under {agents_root} after create"
            matched_file = find_agent_file_by_name(agents_root, AGENT_NAME)
            slug = matched_file.stem
            fm = parse_agent_file(matched_file)
            assert fm.get("name") == AGENT_NAME, f"front-matter name={fm.get('name')!r} != {AGENT_NAME!r}"
            assert fm.get("title") == AGENT_TITLE, f"front-matter title={fm.get('title')!r} != {AGENT_TITLE!r}"
            assert isinstance(fm.get("persona"), str) and fm["persona"].strip(), "front-matter persona empty"
            fm_skills = fm.get("skills") or []
            assert skill_slug in [str(s) for s in fm_skills], (
                f"front-matter skills={fm_skills!r} missing chosen {skill_slug!r}"
            )

            # Reload: card must still be there (server-persisted, not client state).
            page.reload(wait_until="networkidle", timeout=30_000)
            card2 = page.locator(AGENT_CARD_SEL).filter(has_text=AGENT_NAME)
            card2.first.wait_for(state="visible", timeout=15_000)

            browser.close()

        # GET /api/agents lists it (real server, real HTTP — not DOM text).
        resp = httpx.get(srv.base_url + "/api/agents", timeout=15.0)
        assert resp.status_code == 200, resp.text
        listing = resp.json()
        items = listing.get("items") or listing.get("agents") or listing
        if isinstance(items, dict):
            items = items.get("items") or []
        api_slugs = {str(i.get("slug") or i.get("id")) for i in items}
        assert slug in api_slugs, f"GET /api/agents does not list created slug {slug!r}: {api_slugs}"

        # Duplicate + edit round-trip (server API — same real child).
        dup_resp = httpx.post(srv.base_url + f"/api/agents/{slug}/duplicate", timeout=15.0)
        assert dup_resp.status_code in (200, 201), dup_resp.text
        dup = dup_resp.json()
        dup_slug = str(dup.get("slug") or dup.get("id"))
        assert dup_slug and dup_slug != slug, f"duplicate did not produce a new slug: {dup!r}"
        dup_file = find_agent_file(agents_root, dup_slug)
        dup_fm = parse_agent_file(dup_file)
        assert dup_fm.get("persona") == AGENT_PERSONA, "duplicate did not copy persona"

        new_title = "Meal-Prep Support (edited)"
        put_resp = httpx.put(
            srv.base_url + f"/api/agents/{slug}",
            json={"title": new_title},
            timeout=15.0,
        )
        assert put_resp.status_code == 200, put_resp.text
        edited_file = find_agent_file(agents_root, slug)
        edited_fm = parse_agent_file(edited_file)
        assert edited_fm.get("title") == new_title, (
            f"PUT edit did not persist to disk: title={edited_fm.get('title')!r}"
        )

        # DELETE on a builtin -> 403; the builtin file must survive.
        builtin_file = agents_root / "_builtin" / "general-worker.md"
        assert builtin_file.is_file(), f"expected shipped builtin at {builtin_file}"
        del_resp = httpx.delete(srv.base_url + "/api/agents/general-worker", timeout=15.0)
        assert del_resp.status_code == 403, (
            f"DELETE on a builtin agent must be 403, got {del_resp.status_code}: {del_resp.text}"
        )
        assert builtin_file.is_file(), "builtin agent file was deleted despite 403 response"

        # Non-builtin DELETE must succeed and remove the file.
        del_ok = httpx.delete(srv.base_url + f"/api/agents/{dup_slug}", timeout=15.0)
        assert del_ok.status_code in (200, 204), del_ok.text
        assert not dup_file.is_file(), "DELETE on a non-builtin agent did not remove its file"

        write_json(
            "d15-agent-create.json",
            {"slug": slug, "duplicate_slug": dup_slug, "agents_root": str(agents_root)},
        )
    finally:
        srv.stop()



def test_d15_manager_card_lists_team_members():
    """Round 8 (minimal extension): a manager's roster card must list its team.

    data-testid="agent-team" on the card (pinned in BOUNDARIES.md) — this is
    the ONLY new assertion; it does not touch the create/edit/duplicate/
    delete flow already proven above.
    """
    require_live()
    assert live_xai_base_url_ok()

    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        pytest.fail(f"playwright not installed in the repo .venv: {exc}")

    skill_slug, _skill_sha = first_real_skill()
    agents_root = tmp_agents_root()
    srv = spawn_serve(extra_env={"OMNIAGENTOS_AGENTS_ROOT": str(agents_root)})
    try:
        member = create_agent(
            srv.base_url,
            name="Team Card Member D15",
            title="Specialist",
            persona="A specialist member persona for the D15 team-card check.",
            skills=[skill_slug],
        )
        manager = create_agent(
            srv.base_url,
            name="Team Card Manager D15",
            title="Manager",
            persona="A manager persona for the D15 team-card check.",
            skills=[],
            team=[member["slug"]],
        )

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(srv.base_url + "/", wait_until="networkidle", timeout=30_000)

            manager_card = page.locator(AGENT_CARD_SEL).filter(has_text="Team Card Manager D15")
            manager_card.first.wait_for(state="visible", timeout=15_000)

            team_el = manager_card.first.locator('[data-testid="agent-team"]')
            team_el.wait_for(state="visible", timeout=10_000)
            team_text = team_el.inner_text(timeout=10_000)
            assert member["slug"] in team_text or "Team Card Member D15" in team_text, (
                f"manager card's [data-testid=\"agent-team\"] does not list its member "
                f"(slug {member['slug']!r} or its name): got {team_text!r}"
            )

            browser.close()

        write_json(
            "d15-team-card.json",
            {"manager": manager["slug"], "member": member["slug"]},
        )
    finally:
        srv.stop()
