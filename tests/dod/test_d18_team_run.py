"""How this could pass while broken: agent_id resolved to a manager but the manager itself executes both parts of the goal (team is decorative), or team.delegated fired without the delegated worker's own persona/skill actually reaching that task's prompt, or a self-referential/cyclic/too-deep team silently loading as if healthy (500 or a crash later) instead of being reported disabled; now >=2 team.delegated events must name >=2 DISTINCT members, EACH delegated task's own prompt-transcript line (task_id-attributed) must carry that member's persona verbatim and that member's skill-sha256 and NEVER another member's or the manager's own skill-sha256, the run must be done+verified, lessons must be attributed to the executing members' memory_scope, and a malformed team (self/cycle/depth>2/missing member) must list that agent as disabled with a reason in GET /api/agents (never a 500) and reject a run against it with 400."""

from __future__ import annotations

from _harness import (
    assert_status_present,
    create_agent,
    event_payload,
    events_of,
    first_real_skill,
    get_json,
    get_run,
    live_xai_base_url_ok,
    load_prompt_lines,
    post_json,
    require_live,
    second_real_skill,
    spawn_serve,
    start_run,
    team_delegated_events_of,
    tmp_agents_root,
    write_json,
)

MEMBER1_PERSONA = (
    "Member One is a punchy direct-response copywriter who always leads "
    "with the strongest benefit."
)
MEMBER2_PERSONA = (
    "Member Two is a calm, policy-literate support writer who always cites "
    "the exact clause behind a decision."
)
MANAGER_PERSONA = (
    "The Studio Director plans the work and hands each part to the right "
    "specialist; the Director never writes the deliverable text directly."
)
TEAM_GOAL = (
    "This has two parts, hand each to the right specialist. Part 1: write one "
    "punchy ad headline for a $99 skincare bundle. Part 2: draft a short reply "
    "to a customer refund request citing the 30-day policy."
)


def test_d18_team_run_delegates_to_distinct_members_with_isolated_prompts():
    require_live()
    assert live_xai_base_url_ok()

    member_skill_slug, member_skill_sha = first_real_skill()
    member2_skill_slug, member2_skill_sha = second_real_skill(exclude_slug=member_skill_slug)
    manager_skill_slug, manager_skill_sha = second_real_skill(
        exclude_slug=[member_skill_slug, member2_skill_slug]
    )

    agents_root = tmp_agents_root()
    srv = spawn_serve(extra_env={"OMNIAGENTOS_AGENTS_ROOT": str(agents_root)})
    try:
        member1 = create_agent(
            srv.base_url,
            name="Member One D18",
            title="Copywriter",
            persona=MEMBER1_PERSONA,
            skills=[member_skill_slug],
        )
        member2 = create_agent(
            srv.base_url,
            name="Member Two D18",
            title="Support Writer",
            persona=MEMBER2_PERSONA,
            skills=[member2_skill_slug],
        )
        manager = create_agent(
            srv.base_url,
            name="Studio Director D18",
            title="Manager",
            persona=MANAGER_PERSONA,
            skills=[manager_skill_slug],
            team=[member1["slug"], member2["slug"]],
        )

        rid = start_run(srv.base_url, TEAM_GOAL, agent_id=manager["slug"])
        from _harness import collect_sse

        events = collect_sse(srv.base_url, rid, timeout_s=180.0)

        delegated = team_delegated_events_of(events)
        assert len(delegated) >= 2, (
            f"expected >=2 team.delegated events, got {len(delegated)}: {delegated!r}"
        )
        by_task: dict[str, dict] = {}
        members_seen = set()
        for rec in delegated:
            p = event_payload(rec)
            assert str(p.get("manager")) == str(manager["slug"]), (
                f"team.delegated.manager={p.get('manager')!r} != {manager['slug']!r}"
            )
            tid = p.get("task_id")
            member = p.get("member")
            assert tid, f"team.delegated missing task_id: {p!r}"
            assert member, f"team.delegated missing member: {p!r}"
            by_task[str(tid)] = p
            members_seen.add(str(member))
        assert len(members_seen) >= 2, (
            f"team.delegated named only {members_seen!r} — must delegate to >=2 DISTINCT members"
        )
        assert {member1["slug"], member2["slug"]} >= members_seen, (
            f"team.delegated named a member outside the manager's team: {members_seen!r}"
        )

        member_sha_by_slug = {
            member1["slug"]: member_skill_sha,
            member2["slug"]: member2_skill_sha,
        }
        member_persona_by_slug = {
            member1["slug"]: MEMBER1_PERSONA,
            member2["slug"]: MEMBER2_PERSONA,
        }

        prompt_lines = load_prompt_lines(srv.data_dir, rid)
        for tid, p in by_task.items():
            member = str(p["member"])
            own_persona = member_persona_by_slug[member]
            own_sha = member_sha_by_slug[member]
            other_shas = {
                sha for slug, sha in member_sha_by_slug.items() if slug != member
            } | {manager_skill_sha}

            task_lines = [
                ln for ln in prompt_lines if str(ln.get("task_id")) == tid
            ]
            assert task_lines, (
                f"no prompts.jsonl line carries task_id={tid!r} (BINDING: a delegated "
                "worker's prompt line must be attributable to its own task_id)"
            )
            task_blob = " ".join(str(ln) for ln in task_lines)
            assert own_persona in task_blob, (
                f"task {tid} (member {member}) prompt missing that member's persona verbatim"
            )
            assert f"skill-sha256:{own_sha}" in task_blob, (
                f"task {tid} (member {member}) prompt missing that member's own skill-sha256"
            )
            for bad_sha in other_shas:
                assert f"skill-sha256:{bad_sha}" not in task_blob, (
                    f"task {tid} (member {member}) prompt contains a skill-sha256 that is "
                    "NOT this member's own (either the other member's or the manager's)"
                )

        run = get_run(srv.base_url, rid)
        status = assert_status_present(run, expected="done")
        assert status == "done"
        assert run.get("verified") is True, f"run.verified={run.get('verified')!r} (must be True)"

        saved = events_of(events, "lesson.saved")
        assert saved, "team run (done+verified) produced no lesson.saved"
        member_scopes = {member1["slug"], member2["slug"]}
        for rec in saved:
            lp = event_payload(rec)
            scope = str(lp.get("agent_id") or lp.get("memory_scope") or "")
            assert scope in member_scopes, (
                f"lesson.saved not attributed to an executing team member: {lp!r} "
                f"(expected agent_id/memory_scope in {member_scopes!r})"
            )

        write_json(
            "d18-team-run.json",
            {
                "manager": manager["slug"],
                "members": sorted(members_seen),
                "run_id": rid,
                "delegated_tasks": list(by_task.keys()),
            },
        )
    finally:
        srv.stop()


def test_d18_malformed_team_disables_never_crashes():
    agents_root = tmp_agents_root()
    srv = spawn_serve(extra_env={"OMNIAGENTOS_AGENTS_ROOT": str(agents_root)})
    try:
        skill_slug, _sha = first_real_skill()
        member = create_agent(
            srv.base_url,
            name="Loop Member D18",
            title="x",
            persona="x " * 6,
            skills=[skill_slug],
        )

        cases: dict[str, dict] = {}

        # too deep: a manager whose only team member is ITSELF a manager
        # (depth 2), then a THIRD level manager pointing at the depth-2
        # manager — must be refused/disabled (team depth > 2). Built first
        # since it needs a real sub-manager slug to reference.
        sub_manager_resp = post_json(
            srv.base_url,
            "/api/agents",
            {
                "name": "Sub Manager D18",
                "title": "x",
                "persona": "x " * 6,
                "skills": [],
                "team": [member["slug"]],
            },
        )
        assert sub_manager_resp.status_code != 500, (
            f"creating a depth-1 manager must never 500: {sub_manager_resp.text[:300]}"
        )
        sub_manager_slug = None
        if sub_manager_resp.status_code in (200, 201):
            sub_manager_slug = sub_manager_resp.json().get("slug") or sub_manager_resp.json().get("id")

        malformed = {
            "self-reference": {
                "name": "Self Ref Manager D18",
                "title": "x",
                "persona": "x " * 6,
                "skills": [],
                "team": ["self-ref-manager-d18"],
            },
            "missing-member": {
                "name": "Missing Member Manager D18",
                "title": "x",
                "persona": "x " * 6,
                "skills": [],
                "team": ["no-such-agent-slug-d18"],
            },
        }
        if sub_manager_slug:
            malformed["too-deep"] = {
                "name": "Too Deep Manager D18",
                "title": "x",
                "persona": "x " * 6,
                "skills": [],
                "team": [str(sub_manager_slug)],
            }

        # Each malformed case must NEVER 500, and every 2xx-created one must
        # end up disabled-with-a-reason in GET /api/agents (never silently
        # healthy) and must refuse a run against it with 400.
        created_slugs: dict[str, str] = {}
        for label, body in malformed.items():
            resp = post_json(srv.base_url, "/api/agents", body)
            cases[f"{label}-status"] = resp.status_code
            assert resp.status_code != 500, (
                f"{label} team must never 500 at creation: {resp.text[:400]}"
            )
            if resp.status_code in (200, 201):
                data = resp.json()
                slug = data.get("slug") or data.get("id")
                assert slug, f"{label}: 2xx response missing slug/id: {data!r}"
                created_slugs[label] = str(slug)

        listing = get_json(srv.base_url, "/api/agents")
        assert listing.status_code == 200, (
            f"GET /api/agents must never 500 on a malformed team, got "
            f"{listing.status_code}: {listing.text[:400]}"
        )
        items = listing.json()
        items = items.get("items") or items.get("agents") or items
        if isinstance(items, dict):
            items = items.get("items") or []
        by_slug = {str(i.get("slug") or i.get("id")): i for i in items}

        for label, slug in created_slugs.items():
            entry = by_slug.get(slug)
            assert entry is not None, f"{label} agent {slug!r} missing from GET /api/agents entirely"
            disabled = entry.get("enabled") is False or entry.get("disabled") is True
            assert disabled, (
                f"{label} agent {slug!r} was created (2xx) but is NOT listed disabled: {entry!r} "
                "(a self/cycle/depth/missing-member team must never load as healthy)"
            )
            errors = entry.get("errors") or entry.get("error") or entry.get("reason")
            assert errors, f"{label} agent {slug!r} disabled with no reason recorded: {entry!r}"
            run_resp = post_json(srv.base_url, "/api/runs", {"goal": "test", "agent_id": slug})
            assert run_resp.status_code == 400, (
                f"POST /api/runs against disabled {label} agent {slug!r} must 400, got "
                f"{run_resp.status_code}: {run_resp.text[:300]}"
            )
            cases[f"{label}-disabled-reason"] = str(errors)[:200]

        write_json("d18-malformed-team.json", cases)
    finally:
        srv.stop()
