"""The routed skill must actually reach a Worker.

Round-4 defect, found in a live event log: the router picked the right pack
(`refund-request-handler`, score 29.1) and the Planner then assigned every task
to `general-assistant`, so the run emitted
`skill.declined {"reason": "no task was assigned this skill"}` and the quality
gate read "from skill: general-assistant". The routing worked; the *inheritance*
— the thing the webinar script calls "skills your agents plug in and inherit" —
never happened.

A selection nobody acts on is not a selection.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import Script, make_orchestrator

REPO_ROOT = Path(__file__).resolve().parents[2]
SKILLS_ROOT = REPO_ROOT / "skills"

AD_GOAL = (
    "Write 3 Meta feed ad headlines for a $149 12-week strength program for "
    "women over 40. Each headline must be 30 characters or fewer."
)
REFUND_GOAL = (
    "A customer is asking for a refund 38 days after purchase. Draft the reply, "
    "grounded on our refund policy."
)


def _plan_all_general(task_count: int = 1) -> dict:
    """The exact shape the live planner kept returning: everything to the generalist."""
    return {
        "dod": [{"id": "p1", "criterion": "The deliverable answers the goal."}],
        "tasks": [
            {
                "id": f"t{i}",
                "title": f"Task {i}",
                "skill_id": "general-assistant",
                "instruction": "do it",
                "depends_on": [] if i == 1 else [f"t{i - 1}"],
                "writes_files": False,
            }
            for i in range(1, task_count + 1)
        ],
    }


async def _run(settings, goal, plan):
    script = Script(plan=plan)
    orch = make_orchestrator(settings, script)
    orch.load_library(SKILLS_ROOT)
    script.orch = orch
    run = orch.create(goal, 1, [])
    await orch.execute(run)
    return run, script


def _events(run, etype):
    return [e["payload"] for e in run.bus.events if e["type"] == etype]


def _routed_top(goal):
    from omniagentos_starter.skills import load_skills

    packs, scores, fallback = load_skills(SKILLS_ROOT).select(goal, k=2)
    return packs[0].slug, scores[0]["score"], fallback


# ---------------------------------------------------------------- the router
def test_the_library_routes_these_two_goals_to_their_domain_packs():
    """Setup check: the routing half was never the broken half."""
    slug, score, fallback = _routed_top(AD_GOAL)
    assert slug == "ad-copy-framework-writer" and not fallback and score > 1
    slug, score, fallback = _routed_top(REFUND_GOAL)
    assert slug == "refund-request-handler" and not fallback and score > 1


# ------------------------------------------------------- the defect (red-first)
@pytest.mark.asyncio
async def test_a_planner_that_ignores_the_routed_pack_is_overridden(settings):
    top, _score, _fb = _routed_top(REFUND_GOAL)
    run, _ = await _run(settings, REFUND_GOAL, _plan_all_general())

    assigned = _events(run, "planner.plan")[0]["tasks"]
    assert any(t["skill_id"] == top for t in assigned), (
        f"the router chose {top} and no task carries it: {assigned}"
    )
    routed = _events(run, "skill.assigned_by_router")
    assert routed and routed[0]["skill_id"] == top
    assert routed[0]["replaced"] == "general-assistant"


@pytest.mark.asyncio
async def test_the_routed_pack_is_not_reported_as_declined(settings):
    top, _score, _fb = _routed_top(AD_GOAL)
    run, _ = await _run(settings, AD_GOAL, _plan_all_general())
    for payload in _events(run, "skill.declined"):
        assert top not in payload["skill_ids"], (
            f"{top} was assigned by the router and still reported as declined: {payload}"
        )


@pytest.mark.asyncio
async def test_the_routed_packs_quality_checks_become_binding_criteria(settings):
    top, _score, _fb = _routed_top(REFUND_GOAL)
    run, _ = await _run(settings, REFUND_GOAL, _plan_all_general())
    dod = _events(run, "planner.plan")[0]["dod"]
    assert any(c["source"] == top for c in dod), (
        f"no criterion is sourced from {top}; the quality gate will read "
        f"'from skill: general-assistant': {dod}"
    )
    verdict = _events(run, "critic.verdict")[0]
    assert any(
        c["source"] == top for c in dod for v in verdict["verdicts"] if v["criterion_id"] == c["id"]
    ), "the critic never checked a criterion that came from the routed pack"


@pytest.mark.asyncio
async def test_the_override_lands_on_the_primary_task(settings):
    """The task with no depends_on — the one that produces the deliverable."""
    top, _score, _fb = _routed_top(AD_GOAL)
    run, _ = await _run(settings, AD_GOAL, _plan_all_general(task_count=3))
    tasks = {t["id"]: t for t in _events(run, "planner.plan")[0]["tasks"]}
    assert tasks["t1"]["skill_id"] == top, tasks
    assert tasks["t2"]["skill_id"] == "general-assistant"


# ------------------------------------------------------------ the honest cases
@pytest.mark.asyncio
async def test_a_planner_that_did_assign_the_routed_pack_is_left_alone(settings):
    top, _score, _fb = _routed_top(AD_GOAL)
    plan = _plan_all_general()
    plan["tasks"][0]["skill_id"] = top
    run, _ = await _run(settings, AD_GOAL, plan)
    assert not _events(run, "skill.assigned_by_router"), "nothing to override"
    assert _events(run, "planner.plan")[0]["tasks"][0]["skill_id"] == top


@pytest.mark.asyncio
async def test_a_goal_outside_every_pack_keeps_the_generalist(settings):
    """Score 0 means the router has no opinion; it must not manufacture one."""
    goal = "Explain the Kalman filter to a sceptical cat in iambic pentameter."
    _slug, score, fallback = _routed_top(goal)
    assert fallback and score == 0.0, "setup: this goal must not match any pack"
    run, _ = await _run(settings, goal, _plan_all_general())
    assert not _events(run, "skill.assigned_by_router")
    assert _events(run, "planner.plan")[0]["tasks"][0]["skill_id"] == "general-assistant"


@pytest.mark.asyncio
async def test_a_task_with_no_skill_id_at_all_gets_the_routed_pack(settings):
    top, _score, _fb = _routed_top(REFUND_GOAL)
    plan = _plan_all_general()
    del plan["tasks"][0]["skill_id"]
    run, _ = await _run(settings, REFUND_GOAL, plan)
    assert _events(run, "planner.plan")[0]["tasks"][0]["skill_id"] == top


@pytest.mark.asyncio
async def test_the_planner_prompt_names_the_routed_ids_and_demands_one(settings):
    top, _score, _fb = _routed_top(REFUND_GOAL)
    _run_state, script = await _run(settings, REFUND_GOAL, _plan_all_general())
    prompt = script.prompt_text("planner")
    assert top in prompt
    assert "ROUTED SKILLS" in prompt
    assert "skill_reason" in prompt, "the escape hatch must be explicit and must cost a sentence"


@pytest.mark.asyncio
async def test_a_planner_that_rejects_the_pack_in_words_is_recorded_as_a_decline(settings):
    """The escape hatch exists — it just has to cost a sentence."""
    plan = _plan_all_general(task_count=2)
    plan["tasks"][0]["skill_reason"] = "the goal asks for a policy citation, not a welcome sequence"
    run, _ = await _run(settings, REFUND_GOAL, plan)
    declines = _events(run, "skill.declined")
    assert declines, "a stated rejection is worth an event"
    assert "policy citation" in declines[0]["reason"]


@pytest.mark.asyncio
async def test_the_generalist_on_the_bench_is_never_reported_as_declined(settings):
    """It is on the bench by design; saying so every run is noise, not evidence."""
    plan = _plan_all_general()
    plan["tasks"][0]["skill_id"] = "ad-copy-framework-writer"
    run, _ = await _run(settings, AD_GOAL, plan)
    for payload in _events(run, "skill.declined"):
        assert "general-assistant" not in payload["skill_ids"]
