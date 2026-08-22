"""How this could pass while broken: a local OpenAI stub plus `status or 'done'` with canned role events; now every llm.call must hit api.x.ai 2xx with response_id, four distinct successful roles, status present==done, verified==true, and a non-placeholder deliverable."""

from __future__ import annotations

from _harness import (
    PLACEHOLDER_DELIVERABLES,
    XAI_HOST,
    assert_status_present,
    collect_sse,
    event_payload,
    events_of,
    get_run,
    live_xai_base_url_ok,
    llm_calls,
    provider_host_of,
    require_live,
    spawn_serve,
    start_run,
    write_json,
)

D2_GOAL = "Write a 3-bullet summary of why agent orchestration beats a chatbox"


def test_d02_unmocked_xai_run_completes():
    require_live()
    assert live_xai_base_url_ok(), (
        "OMNIAGENTOS_BASE_URL must be unset or exactly https://api.x.ai/v1 for D2 "
        "(a stub base URL is a false-success of F1)"
    )

    srv = spawn_serve()
    try:
        rid = start_run(srv.base_url, D2_GOAL)
        events = collect_sse(srv.base_url, rid, timeout_s=180.0)
        run = get_run(srv.base_url, rid)
        write_json(
            "d2-live-run.json",
            {
                "run_id": rid,
                "run": run,
                "event_types": [e.get("event") for e in events],
                "llm_calls": llm_calls(events),
            },
        )

        status = assert_status_present(run, "done")
        assert status == "done"
        assert "verified" in run, "run.verified must be present (never defaulted)"
        assert run["verified"] is True, f"verified must be JSON true, got {run.get('verified')!r}"

        deliverable = run.get("deliverable")
        if not deliverable:
            done_ev = events_of(events, "run.done")
            assert done_ev, "no run.done event and no deliverable on GET /api/runs/{id}"
            deliverable = event_payload(done_ev[-1]).get("deliverable")
        assert isinstance(deliverable, str)
        assert deliverable.strip() not in PLACEHOLDER_DELIVERABLES
        assert len(deliverable.strip()) >= 20

        calls = llm_calls(events)
        assert calls, "no llm.call events — roles cannot be proven on the wire"
        for c in calls:
            host = provider_host_of(c)
            assert host == XAI_HOST, f"provider_host={host!r} != {XAI_HOST}"
            st = c.get("http_status")
            assert isinstance(st, int) and 200 <= st < 300, f"llm.call http_status={st!r} not 2xx"
            assert c.get("response_id"), "llm.call missing response_id"
            assert "content" not in c or not c.get("content"), "llm.call must not include content"

        roles = {c.get("role") for c in calls}
        needed = {"planner", "worker", "critic", "verifier"}
        assert needed <= roles, f"missing roles {needed - roles} in llm.call events"

        # Distinct successful completions: distinct response_id and distinct system-prompt hash.
        by_role = {}
        for c in calls:
            role = c.get("role")
            if role in needed and c.get("http_status") and 200 <= int(c["http_status"]) < 300:
                by_role.setdefault(role, []).append(c)
        hashes = []
        resp_ids = []
        for role in ("planner", "worker", "critic", "verifier"):
            opts = by_role.get(role) or []
            assert opts, f"no successful llm.call for role={role}"
            chosen = opts[0]
            rid_ = chosen.get("response_id") or chosen.get("request_id")
            assert rid_, f"{role} missing response_id/request_id"
            resp_ids.append(str(rid_))
            h = chosen.get("system_prompt_sha256") or chosen.get("system_prompt_hash")
            assert h, (
                f"{role} llm.call missing system_prompt_sha256 "
                "(BINDING: each llm.call includes system_prompt_sha256 of the system prompt)"
            )
            hashes.append(str(h))
        assert len(set(resp_ids)) == 4, f"response ids not distinct: {resp_ids}"
        assert len(set(hashes)) == 4, f"system-prompt hashes not distinct across 4 roles: {hashes}"
    finally:
        srv.stop()
