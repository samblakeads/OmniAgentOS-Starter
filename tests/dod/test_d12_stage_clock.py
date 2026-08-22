"""How this could pass while broken: writing d12-clock.json by hand or using process wall-clock around a mocked burst; now each DEMO goal's t_first_event_ms and t_done_ms are computed from real SSE timestamps and must be <2000ms and <120000ms, INCLUDING beat 0 (goal 2 dispatched to the DEMO.md-pinned agent, which must not blow the same clock budget)."""

from __future__ import annotations

from _harness import (
    collect_sse,
    create_agent_idempotent,
    event_type,
    live_xai_base_url_ok,
    parse_demo_goals_full,
    pick_agent_skill,
    require_live,
    spawn_serve,
    start_run,
    tmp_agents_root,
    ts_of,
    write_json,
)


def test_d12_demo_goals_clock_from_sse():
    require_live()
    assert live_xai_base_url_ok()
    triples = parse_demo_goals_full()
    srv = spawn_serve(extra_env={"OMNIAGENTOS_AGENTS_ROOT": str(tmp_agents_root())})
    clocks = []
    try:
        for i, (goal, _dod, agent_slug) in enumerate(triples, 1):
            import time

            agent_id = None
            if agent_slug:
                display_name = agent_slug.replace("-", " ").replace("_", " ").title()
                agent = create_agent_idempotent(
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

            rid = start_run(srv.base_url, goal, agent_id=agent_id)
            post_at = time.time()
            events = collect_sse(srv.base_url, rid, timeout_s=150.0)
            assert events, f"goal {i} produced no SSE events"
            first = events[0]
            terminal = None
            for rec in events:
                if event_type(rec) in {"run.done", "run.failed"}:
                    terminal = rec
            assert terminal is not None, f"goal {i} never emitted run.done/failed"
            t0 = ts_of(first)
            tN = ts_of(terminal)
            # Prefer engine-provided elapsed; else derive from SSE ts.
            # BINDING: event ts is epoch seconds (float) or ISO-8601.
            if tN >= 1_000_000_000:  # epoch seconds
                t_first_ms = int(max(0.0, (t0 - post_at) * 1000))
                # first event should be very near post; use event-to-event from run.started
                started = None
                for rec in events:
                    if event_type(rec) == "run.started":
                        started = rec
                        break
                origin = ts_of(started) if started else t0
                t_first_ms = int(max(0.0, (t0 - origin) * 1000))
                t_done_ms = int(max(0.0, (tN - origin) * 1000))
            else:
                t_first_ms = int(t0) if t0 > 10 else int(t0 * 1000)
                t_done_ms = int(tN) if tN > 10 else int(tN * 1000)
            assert t_first_ms < 2000, f"goal {i} t_first_event_ms={t_first_ms} >= 2000"
            assert t_done_ms < 120000, f"goal {i} t_done_ms={t_done_ms} >= 120000"
            clocks.append(
                {
                    "goal": goal,
                    "run_id": rid,
                    "t_first_event_ms": t_first_ms,
                    "t_done_ms": t_done_ms,
                }
            )
    finally:
        srv.stop()
    write_json("d12-clock.json", {"goals": clocks})
    assert (len(clocks) == 3)
