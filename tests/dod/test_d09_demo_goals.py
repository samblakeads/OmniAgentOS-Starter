"""How this could pass while broken: three empty evidence files named d9-demo-*.json whose goals are 'hello'; now DEMO.md's three literal goals bijection-match receipt.goal, each status==done with len(deliverable)>40, goal 1 exhibits the D4 loop, goal 3 writes >=5 workspace files, and beat 0 (DEMO.md's ` ||| agent: <slug>` pin on goal 2) proves the run was actually dispatched through agent.assigned, not just that an agent happened to exist."""

from __future__ import annotations

import hashlib

import httpx
from _harness import (
    check_planted_criterion,
    collect_sse,
    create_agent,
    event_payload,
    event_type,
    events_of,
    get_run,
    live_xai_base_url_ok,
    parse_demo_goals_full,
    pick_agent_skill,
    repo_head_sha,
    require_live,
    spawn_serve,
    start_run,
    tmp_agents_root,
    validate_receipt,
    write_json,
)


def test_d09_three_demo_goals_bijection_and_loop():
    require_live()
    assert live_xai_base_url_ok()
    triples = parse_demo_goals_full()
    assert len(triples) == 3
    goals = [g for g, _, _ in triples]

    # Isolated agents root: beat 0 creates a real agent (POST /api/agents),
    # and per Round 6's binding pin that write must NEVER land in the real
    # shipped agents/ tree — every DEMO drill runs against a tmp copy.
    srv = spawn_serve(extra_env={"OMNIAGENTOS_AGENTS_ROOT": str(tmp_agents_root())})
    receipts = []
    beat0_agent_id = None
    try:
        for i, (goal, extra_dod_criteria, agent_slug) in enumerate(triples, 1):
            extra_dod = (
                [{"criterion": c} for c in extra_dod_criteria]
                if extra_dod_criteria
                else None
            )
            agent_id = None
            if agent_slug:
                # Beat 0: create the pinned agent (name/title mirror the
                # DEMO.md slug; PLAN's stage script creates "Riley,
                # Meal-Prep Support" with the refund pack) and dispatch
                # THIS goal to it. Uses the slug the API actually returns,
                # not the literal DEMO.md string, so this is decoupled
                # from the implementer's slugify() algorithm.
                display_name = agent_slug.replace("-", " ").replace("_", " ").title()
                agent = create_agent(
                    srv.base_url,
                    name=display_name,
                    title="Meal-Prep Support",
                    persona=(
                        f"{display_name} is a warm, practical support agent who "
                        "always cites the exact policy clause behind a decision."
                    ),
                    skills=[pick_agent_skill()],
                )
                agent_id = agent["slug"]
                beat0_agent_id = agent_id
            rid = start_run(srv.base_url, goal, extra_dod=extra_dod, agent_id=agent_id)
            events = collect_sse(srv.base_url, rid, timeout_s=150.0)
            if agent_id:
                assigned = events_of(events, "agent.assigned")
                assert assigned, (
                    f"DEMO goal {i} beat 0: agent_id={agent_id!r} set but no "
                    "agent.assigned event — the run must actually be dispatched "
                    "through the agent, not merely accept the field"
                )
                assert str(event_payload(assigned[0]).get("agent_id")) == str(agent_id), (
                    f"DEMO goal {i} beat 0: agent.assigned.agent_id != {agent_id!r}"
                )
            run = get_run(srv.base_url, rid)
            assert "status" in run and run["status"] == "done", (
                f"DEMO goal {i} status={run.get('status')!r} (present==done required)"
            )
            deliverable = run.get("deliverable") or ""
            if not deliverable:
                done = events_of(events, "run.done")
                assert done, f"DEMO goal {i} missing run.done"
                deliverable = event_payload(done[-1]).get("deliverable") or ""
            assert len(deliverable) > 40, (
                f"DEMO goal {i} deliverable len={len(deliverable)} <= 40"
            )

            if i == 1:
                assert extra_dod_criteria, (
                    "DEMO goal 1 must carry a ` ||| dod: <criterion>` planted "
                    "rubric so the D4 loop is reliably exhibited on stage, "
                    "not left to LLM nondeterminism"
                )
                types = [event_type(e) for e in events]
                # D4 loop: critic fail -> repair.dispatched -> worker.finished -> verifier
                c_fail = False
                repaired = False
                for rec in events:
                    if event_type(rec) == "critic.verdict":
                        obj = event_payload(rec)
                        if "pass" in obj and obj["pass"] is False:
                            c_fail = True
                    if c_fail and event_type(rec) == "repair.dispatched":
                        tids = event_payload(rec).get("task_ids") or []
                        assert len(tids) >= 1
                        repaired = True
                v = events_of(events, "verifier.verdict")
                assert c_fail and repaired and v, (
                    f"DEMO goal 1 must exhibit the D4 loop; types={types}"
                )
                vobj = event_payload(v[-1])
                assert "verified" in vobj and vobj["verified"] is True
                # Mechanically re-check the deliverable against the planted
                # criterion(s) — a verifier that rubber-stamps verified=True
                # without satisfying the rubric it was given must still fail.
                for crit in extra_dod_criteria:
                    assert check_planted_criterion(deliverable, crit), (
                        f"DEMO goal 1 deliverable does not mechanically satisfy "
                        f"planted extra_dod criterion {crit!r}: {deliverable!r}"
                    )

            if i == 3:
                files_resp = httpx.get(srv.base_url + f"/api/runs/{rid}/files", timeout=15.0)
                assert files_resp.status_code == 200, files_resp.text
                fjson = files_resp.json()
                files = fjson.get("files") or fjson.get("items") or fjson
                if isinstance(files, dict):
                    files = files.get("files") or files.get("items") or []
                assert isinstance(files, list) and len(files) >= 5, (
                    f"DEMO goal 3 must produce >=5 workspace files, got {files!r}"
                )

            t_first = None
            t_done = None
            for rec in events:
                ts = event_payload(rec).get("ts")
                if ts is None:
                    continue
                if t_first is None:
                    t_first = ts
                if event_type(rec) in {"run.done", "run.failed"}:
                    t_done = ts
            receipt = {
                "magic": "OMNIAGENTOS-RECEIPT-1",
                "git_head": repo_head_sha(),
                "argv": ["omniagentos", "serve", "--port", "0"],
                "health_json": httpx.get(srv.base_url + "/api/health", timeout=10).json(),
                "run_id": rid,
                "status": run["status"],
                "provider_http_status": [
                    c.get("http_status")
                    for rec in events
                    if event_type(rec) == "llm.call"
                    for c in [event_payload(rec)]
                ],
                "t_first_event_ms": 0 if t_first is None else 0,
                "t_done_ms": 0,
                "goal": goal,
                "deliverable_sha256": hashlib.sha256(deliverable.encode()).hexdigest(),
            }
            # Real SSE timestamps relative to run start if present on run.started.
            rs = events_of(events, "run.started")
            if rs and event_payload(rs[0]).get("ts") is not None and t_done is not None:
                t0 = float(event_payload(rs[0])["ts"])
                # ts may be epoch seconds; convert to ms deltas via first event.
                first_ts = float(event_payload(events[0]).get("ts") or t0)
                last_ts = float(t_done)
                # If values look like epoch seconds, convert gap to ms.
                gap = last_ts - first_ts
                if gap < 10_000:  # seconds
                    receipt["t_first_event_ms"] = max(0, int((first_ts - t0) * 1000))
                    receipt["t_done_ms"] = max(0, int((last_ts - t0) * 1000))
                else:
                    receipt["t_first_event_ms"] = int(first_ts - t0)
                    receipt["t_done_ms"] = int(last_ts - t0)
            write_json(f"d9-demo-{i}.json", receipt)
            receipts.append(receipt)
    finally:
        srv.stop()

    got = [r["goal"] for r in receipts]
    assert sorted(got) == sorted(goals), (
        f"bijection failed: DEMO.md goals={goals!r} receipt.goal={got!r}"
    )
    for r in receipts:
        validate_receipt(r)
    write_json(
        "d9-demo-summary.json",
        {"goals": goals, "run_ids": [r["run_id"] for r in receipts], "beat0_agent_id": beat0_agent_id},
    )
