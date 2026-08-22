"""How this could pass while broken: agent_id accepted but ignored (router picks skills as usual), or the persona/skill text never actually reaching the LLM despite an agent.assigned event; now the worker prompt transcript must contain the persona text and the agent's skill-sha256 verbatim, must NOT contain a sha from a pack outside the agent's list, the run must be done+verified, lesson.saved must carry agent_id, a second run by the same agent must recall it, and a run WITHOUT agent_id must still behave exactly as before (no regression)."""

from __future__ import annotations

from _harness import (
    assert_status_present,
    collect_sse,
    create_agent,
    event_payload,
    events_of,
    first_real_skill,
    get_run,
    live_xai_base_url_ok,
    load_prompts,
    recalled_lesson_ids,
    require_live,
    second_real_skill,
    spawn_serve,
    start_run,
    tmp_agents_root,
    write_json,
)

AGENT_NAME = "Riley D16 Test"
AGENT_TITLE = "Meal-Prep Support"
AGENT_PERSONA = (
    "Riley is a warm, practical meal-prep coach for busy parents who never "
    "pushes upsells and always cites the exact refund window."
)
RUN1_GOAL = "A customer wants a refund on a subscription bought 10 days ago. Draft the reply."
RUN2_GOAL = "A customer wants a refund on a subscription bought 5 days ago. Draft the reply."
NO_AGENT_GOAL = "Write two sentences on why a planner-plus-critic loop beats a single chatbox."


def test_d16_agent_run_persona_skill_isolation_memory_and_regression():
    require_live()
    assert live_xai_base_url_ok()

    agent_skill_slug, agent_skill_sha = first_real_skill()
    other_skill_slug, other_skill_sha = second_real_skill(exclude_slug=agent_skill_slug)

    agents_root = tmp_agents_root()
    srv = spawn_serve(extra_env={"OMNIAGENTOS_AGENTS_ROOT": str(agents_root)})
    try:
        agent = create_agent(
            srv.base_url,
            name=AGENT_NAME,
            title=AGENT_TITLE,
            persona=AGENT_PERSONA,
            skills=[agent_skill_slug],
        )
        agent_id = agent["slug"]

        # --- run1: with agent_id ---
        rid1 = start_run(srv.base_url, RUN1_GOAL, agent_id=agent_id)
        events1 = _collect(srv.base_url, rid1)
        assigned = events_of(events1, "agent.assigned")
        assert assigned, "run1 (agent_id set) missing agent.assigned event"
        ap = event_payload(assigned[0])
        assert str(ap.get("agent_id")) == str(agent_id), (
            f"agent.assigned.agent_id={ap.get('agent_id')!r} != {agent_id!r}"
        )
        assigned_skills = [str(s) for s in (ap.get("skills") or [])]
        assert agent_skill_slug in assigned_skills, (
            f"agent.assigned.skills={assigned_skills!r} missing {agent_skill_slug!r}"
        )

        run1 = get_run(srv.base_url, rid1)
        status1 = assert_status_present(run1, expected="done")
        assert status1 == "done"
        verified1 = run1.get("verified")
        assert verified1 is True, f"run1.verified={verified1!r} (must be True, never defaulted)"

        transcript1 = load_prompts(srv.data_dir, rid1)
        assert AGENT_PERSONA in transcript1, (
            "worker prompt transcript missing the agent persona text verbatim"
        )
        assert f"skill-sha256:{agent_skill_sha}" in transcript1, (
            "worker prompt transcript missing the agent's own skill-sha256"
        )
        assert f"skill-sha256:{other_skill_sha}" not in transcript1, (
            "worker prompt transcript contains a skill-sha256 OUTSIDE the agent's skill list "
            "(agent's skill set must restrict the router, not just be a suggestion)"
        )

        saved = events_of(events1, "lesson.saved")
        assert saved, "run1 (done+verified) produced no lesson.saved"
        lp = event_payload(saved[-1])
        assert str(lp.get("agent_id")) == str(agent_id), (
            f"lesson.saved.agent_id={lp.get('agent_id')!r} != {agent_id!r}"
        )
        lesson_id = str(lp.get("id") or lp.get("lesson_id"))
        assert lesson_id and lesson_id != "None", "lesson.saved missing id"

        # --- run2: same agent, must recall run1's lesson with agent_id ---
        rid2 = start_run(srv.base_url, RUN2_GOAL, agent_id=agent_id)
        events2 = _collect(srv.base_url, rid2)
        recalled = events_of(events2, "memory.recalled")
        assert recalled, "run2 (same agent) missing memory.recalled"
        rp = event_payload(recalled[0])
        assert str(rp.get("agent_id")) == str(agent_id), (
            f"memory.recalled.agent_id={rp.get('agent_id')!r} != {agent_id!r} "
            "(agent-scoped recall must be attributable to the agent)"
        )
        ids2 = recalled_lesson_ids(recalled[0])
        assert ids2, "run2 memory.recalled ids empty — must not vacuously pass"
        assert lesson_id in ids2, f"run2 recalled {ids2!r}, missing run1 lesson {lesson_id!r}"

        # --- regression: a run WITHOUT agent_id behaves as before ---
        rid3 = start_run(srv.base_url, NO_AGENT_GOAL)
        events3 = _collect(srv.base_url, rid3)
        assigned3 = events_of(events3, "agent.assigned")
        assert not assigned3, "run without agent_id must NOT emit agent.assigned (regression)"
        run3 = get_run(srv.base_url, rid3)
        status3 = assert_status_present(run3, expected="done")
        assert status3 == "done"
        assert run3.get("verified") is True

        write_json(
            "d16-agent-run.json",
            {
                "agent_id": agent_id,
                "run1": rid1,
                "run2": rid2,
                "run3_no_agent": rid3,
                "lesson_id": lesson_id,
                "agent_skill": agent_skill_slug,
                "excluded_skill": other_skill_slug,
            },
        )
    finally:
        srv.stop()


def _collect(base_url: str, run_id: str, timeout_s: float = 180.0):
    return collect_sse(base_url, run_id, timeout_s=timeout_s)
