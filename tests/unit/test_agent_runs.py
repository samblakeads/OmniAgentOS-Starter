"""Assigning a run to an agent — and proving the no-agent path did not move.

The regression guard matters as much as the feature: an agent is an option, and
a run without one has to behave exactly as it did before agents existed.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import Script, make_orchestrator, run_goal

from omniagentos_starter.agents import AgentStore, load_agents
from omniagentos_starter.skills import load_skills

REPO_ROOT = Path(__file__).resolve().parents[2]
SKILLS_ROOT = REPO_ROOT / "skills"

REFUND_GOAL = (
    "A customer is asking for a refund 38 days after purchase. Draft the reply, "
    "grounded on our refund policy."
)
AD_GOAL = "Write 3 Meta feed ad headlines for a $149 12-week strength program for women over 40."

PERSONA = "Calm, exact, and never sorry twice. Cites the clause before the apology."


def _orch(settings, script, tmp_path, agents=None):
    orch = make_orchestrator(settings, script)
    orch.load_library(SKILLS_ROOT)
    root = tmp_path / "agents"
    store = AgentStore(root)
    for payload in agents or []:
        store.create(payload, library=orch.library)
    orch.load_roster(root)
    script.orch = orch
    return orch


def _events(run, etype):
    return [e["payload"] for e in run.bus.events if e["type"] == etype]


SUPPORT = {
    "name": "Riley",
    "title": "Meal-Prep Support",
    "persona": PERSONA,
    "skills": ["refund-request-handler"],
    "tools": ["read_file", "list_files"],
    "body": "Open with the customer's own words.",
}


# ------------------------------------------------------------- assignment
@pytest.mark.asyncio
async def test_a_run_assigned_to_an_agent_announces_who_is_executing(settings, tmp_path):
    script = Script()
    orch = _orch(settings, script, tmp_path, [SUPPORT])
    run = orch.create(REFUND_GOAL, 1, [], agent_id="riley")
    await orch.execute(run)

    assigned = _events(run, "agent.assigned")
    assert assigned, "a run with an agent must say so before it plans"
    assert assigned[0]["agent_id"] == "riley"
    assert assigned[0]["skills"] == ["refund-request-handler"]
    assert assigned[0]["name"] == "Riley"
    assert run.summary()["agent"] == {"id": "riley", "name": "Riley"}


@pytest.mark.asyncio
async def test_the_agents_persona_and_standing_instructions_reach_the_worker(settings, tmp_path):
    script = Script()
    orch = _orch(settings, script, tmp_path, [SUPPORT])
    run = orch.create(REFUND_GOAL, 1, [], agent_id="riley")
    await orch.execute(run)

    worker_prompt = script.prompt_text("worker")
    assert PERSONA in worker_prompt
    assert "Open with the customer's own words." in worker_prompt
    assert "agent-sha256:" in worker_prompt
    # ...and the pack the agent carries is the pack it was handed.
    pack = orch.library.by_id("refund-request-handler")
    assert f"skill-sha256:{pack.sha256}" in worker_prompt


@pytest.mark.asyncio
async def test_the_planner_is_told_who_will_execute(settings, tmp_path):
    script = Script()
    orch = _orch(settings, script, tmp_path, [SUPPORT])
    await orch.execute(orch.create(REFUND_GOAL, 1, [], agent_id="riley"))
    planner_prompt = script.prompt_text("planner")
    assert "Riley" in planner_prompt
    assert "Meal-Prep Support" in planner_prompt


@pytest.mark.asyncio
async def test_the_router_may_only_choose_from_the_agents_own_skills(settings, tmp_path):
    """A support agent handed an ad-copy goal does not get the ad-copy pack."""
    script = Script()
    orch = _orch(settings, script, tmp_path, [SUPPORT])
    run = orch.create(AD_GOAL, 1, [], agent_id="riley")
    await orch.execute(run)

    selected = _events(run, "skill.selected")[0]["skill_ids"]
    assert "ad-copy-framework-writer" not in selected, selected
    # nothing on this agent's shelf matches, so it falls back to the generalist
    assert selected == ["general-assistant"]
    assert _events(run, "skill.selection_fallback")

    worker_prompt = script.prompt_text("worker")
    ad_pack = orch.library.by_id("ad-copy-framework-writer")
    assert f"skill-sha256:{ad_pack.sha256}" not in worker_prompt


@pytest.mark.asyncio
async def test_an_agents_own_pack_is_still_chosen_when_it_matches(settings, tmp_path):
    script = Script()
    orch = _orch(settings, script, tmp_path, [SUPPORT])
    run = orch.create(REFUND_GOAL, 1, [], agent_id="riley")
    await orch.execute(run)
    assert _events(run, "skill.selected")[0]["skill_ids"] == ["refund-request-handler"]


# ----------------------------------------------------------------- refusals
@pytest.mark.asyncio
async def test_an_unknown_agent_is_refused_not_quietly_ignored(settings, tmp_path):
    orch = _orch(settings, Script(), tmp_path, [SUPPORT])
    with pytest.raises(ValueError) as exc:
        orch.create(REFUND_GOAL, 1, [], agent_id="nobody")
    assert "nobody" in str(exc.value)


@pytest.mark.asyncio
async def test_a_disabled_agent_is_refused(settings, tmp_path):
    orch = _orch(settings, Script(), tmp_path, [])
    root = tmp_path / "agents"
    root.mkdir(parents=True, exist_ok=True)
    (root / "broken.md").write_text(
        "---\nname: Broken\nskills: [no-such-pack]\n---\nbody\n", encoding="utf-8"
    )
    orch.load_roster(root)
    assert orch.roster.by_id("broken").enabled is False
    with pytest.raises(ValueError) as exc:
        orch.create(REFUND_GOAL, 1, [], agent_id="broken")
    assert "disabled" in str(exc.value)


@pytest.mark.asyncio
async def test_an_agent_without_write_file_cannot_write_files(settings, tmp_path):
    """The tool list is a capability grant, and it is enforced at the write."""
    plan = {
        "dod": [{"id": "d1", "criterion": "ok"}],
        "tasks": [
            {
                "id": "t1",
                "title": "w",
                "skill_id": "refund-request-handler",
                "instruction": "x",
                "writes_files": True,
            }
        ],
    }
    body = "=== FILE: note.md ===\nhello\n=== END FILE ===\ndone"
    script = Script(plan=plan, worker_text=body)
    orch = _orch(settings, script, tmp_path, [SUPPORT])  # tools: read_file, list_files
    run = orch.create(REFUND_GOAL, 1, [], agent_id="riley")
    await orch.execute(run)

    errors = _events(run, "tool.error")
    assert errors and errors[0]["error_tag"] == "TOOL_NOT_PERMITTED", errors
    assert not list((settings.workspace_dir / "runs").rglob("note.md"))
    # the worker was never told a file protocol it is not allowed to use
    assert "SAVE FILES" not in script.prompt_text("worker")


@pytest.mark.asyncio
async def test_an_agent_with_write_file_still_writes(settings, tmp_path):
    plan = {
        "dod": [{"id": "d1", "criterion": "ok"}],
        "tasks": [
            {"id": "t1", "title": "w", "skill_id": "general-assistant", "instruction": "x", "writes_files": True}
        ],
    }
    body = "=== FILE: note.md ===\nhello\n=== END FILE ===\ndone"
    script = Script(plan=plan, worker_text=body)
    writer = {**SUPPORT, "name": "Writer", "tools": ["read_file", "write_file", "list_files"], "skills": []}
    orch = _orch(settings, script, tmp_path, [writer])
    run = orch.create(REFUND_GOAL, 1, [], agent_id="writer")
    await orch.execute(run)
    assert _events(run, "tool.write"), [e["type"] for e in run.bus.events]


# ------------------------------------------------------------ @slug prefix
@pytest.mark.asyncio
async def test_an_at_slug_prefix_assigns_the_run_and_leaves_the_goal_clean(settings, tmp_path):
    orch = _orch(settings, Script(), tmp_path, [SUPPORT])
    run = orch.create("@riley " + REFUND_GOAL)
    assert run.agent_id == "riley"
    assert run.goal == REFUND_GOAL
    assert not run.goal.startswith("@")


@pytest.mark.asyncio
async def test_an_at_word_that_is_not_an_agent_is_left_alone(settings, tmp_path):
    """Eating the first word of somebody's goal is worse than not supporting it."""
    orch = _orch(settings, Script(), tmp_path, [SUPPORT])
    goal = "@channel please summarise the outage"
    run = orch.create(goal)
    assert run.agent_id == ""
    assert run.goal == goal


@pytest.mark.asyncio
async def test_an_explicit_agent_id_wins_over_a_prefix(settings, tmp_path):
    orch = _orch(settings, Script(), tmp_path, [SUPPORT, {**SUPPORT, "name": "Other"}])
    run = orch.create("@other " + REFUND_GOAL, agent_id="riley")
    assert run.agent_id == "riley"


# ------------------------------------------------------------------ memory
@pytest.mark.asyncio
async def test_a_lesson_is_stamped_with_the_agent_that_learned_it(settings, tmp_path):
    script = Script()
    orch = _orch(settings, script, tmp_path, [SUPPORT])
    run = orch.create(REFUND_GOAL, 1, [], agent_id="riley")
    await orch.execute(run)
    saved = _events(run, "lesson.saved")
    assert saved and saved[0]["agent_id"] == "riley"
    assert orch.memory.all_lessons()[0]["agent_id"] == "riley"


@pytest.mark.asyncio
async def test_a_second_run_by_the_same_agent_recalls_its_own_lesson(settings, tmp_path):
    script = Script()
    orch = _orch(settings, script, tmp_path, [SUPPORT])
    first = orch.create(REFUND_GOAL, 1, [], agent_id="riley")
    await orch.execute(first)
    assert _events(first, "lesson.saved")

    second = orch.create(REFUND_GOAL, 1, [], agent_id="riley")
    await orch.execute(second)
    recalled = _events(second, "memory.recalled")[0]
    assert recalled["matched"] >= 1
    assert recalled["agent_id"] == "riley"
    assert recalled["from_agent"] >= 1
    assert recalled["lessons"][0]["agent_id"] == "riley"


@pytest.mark.asyncio
async def test_an_agent_prefers_its_own_lessons_but_still_sees_the_shared_pool(settings, tmp_path):
    orch = _orch(settings, Script(), tmp_path, [SUPPORT])
    memory = orch.memory
    memory.create_run("r-global", REFUND_GOAL)
    memory.finish_run("r-global", "done", verified=True)
    memory.save_lesson("r-global", "GLOBAL LESSON", [], REFUND_GOAL)
    memory.create_run("r-riley", REFUND_GOAL, agent_id="riley")
    memory.finish_run("r-riley", "done", verified=True)
    memory.save_lesson("r-riley", "RILEY LESSON", [], REFUND_GOAL, agent_id="riley")

    mine = memory.recall(REFUND_GOAL, k=2, agent_id="riley")
    assert mine[0].text == "RILEY LESSON", [lesson.text for lesson in mine]
    assert "GLOBAL LESSON" in [lesson.text for lesson in mine], "the shared pool is still reachable"

    # An agent with nothing of its own falls back rather than starting blank.
    other = memory.recall(REFUND_GOAL, k=2, agent_id="someone-else")
    assert [lesson.text for lesson in other][0] in {"GLOBAL LESSON", "RILEY LESSON"}


# --------------------------------------------------------- regression guard
@pytest.mark.asyncio
async def test_a_run_without_an_agent_behaves_exactly_as_before(settings, tmp_path):
    """No agent block, no agent.assigned, unrestricted router, no agent_id anywhere."""
    script = Script()
    orch = _orch(settings, script, tmp_path, [SUPPORT])
    run = orch.create(AD_GOAL, 1, [])
    await orch.execute(run)

    assert run.agent_id == ""
    assert run.agent is None
    assert run.summary()["agent"] is None
    assert _events(run, "agent.assigned") == []
    # the router saw the whole library, so it picked the pack the goal matches
    assert _events(run, "skill.selected")[0]["skill_ids"] == ["ad-copy-framework-writer"]
    worker_prompt = script.prompt_text("worker")
    assert "<agent " not in worker_prompt
    assert "YOU ARE:" not in worker_prompt
    assert "THE WORKER FOR THIS RUN IS A NAMED AGENT" not in script.prompt_text("planner")
    assert run.status == "done"
    saved = _events(run, "lesson.saved")
    assert saved and saved[0]["agent_id"] == ""


@pytest.mark.asyncio
async def test_the_engine_still_runs_with_no_roster_at_all(settings):
    """The roster is optional; a tree with no agents/ directory is not broken."""
    run, script = await run_goal(settings, Script(), AD_GOAL, max_rounds=1)
    assert run.status == "done"
    assert run.agent_id == ""


def test_a_roster_loaded_before_the_library_would_disable_everything(settings, tmp_path):
    """Documenting the ordering the Orchestrator relies on."""
    root = tmp_path / "agents"
    AgentStore(root).create({"name": "Riley", "skills": ["refund-request-handler"]},
                            library=load_skills(SKILLS_ROOT))
    assert load_agents(root, library=load_skills(SKILLS_ROOT)).by_id("riley").enabled is True
    assert load_agents(root, library=load_skills(None)).by_id("riley").enabled is False


# --------------------------------------------------- D16: the agent's equipment
@pytest.mark.asyncio
async def test_the_agents_own_skills_reach_the_worker_even_when_the_goal_words_it_differently(
    settings, tmp_path
):
    """Equipment, not a lottery.

    The agent carries a pack the router would never score against this goal. It
    is still the agent's pack, and an agent that silently loses its expertise
    whenever the goal is phrased unexpectedly is not the agent you configured.
    """
    writer = {
        "name": "Max",
        "title": "Content Writer",
        "persona": "Writes like a human.",
        "skills": ["vsl-script-builder"],
        "tools": ["read_file"],
    }
    script = Script()
    orch = _orch(settings, script, tmp_path, [writer])
    run = orch.create(REFUND_GOAL, 1, [], agent_id="max")
    await orch.execute(run)

    prompt = script.prompt_text("worker")
    owned = orch.library.by_id("vsl-script-builder")
    assert f"skill-sha256:{owned.sha256}" in prompt, "the agent's own pack never reached the Worker"
    # ...and nothing outside its list did.
    outside = orch.library.by_id("refund-request-handler")
    assert f"skill-sha256:{outside.sha256}" not in prompt, "a pack outside the agent's list leaked in"


@pytest.mark.asyncio
async def test_without_an_agent_the_worker_sees_exactly_the_selected_pack(settings, tmp_path):
    script = Script()
    orch = _orch(settings, script, tmp_path, [SUPPORT])
    run = orch.create(REFUND_GOAL, 1, [])
    await orch.execute(run)
    prompt = script.prompt_text("worker")
    selected = orch.library.by_id(_events(run, "skill.selected")[0]["skill_ids"][0])
    assert f"skill-sha256:{selected.sha256}" in prompt
    others = [p for p in orch.library.packs if p.slug != selected.slug]
    for pack in others:
        assert f"skill-sha256:{pack.sha256}" not in prompt
