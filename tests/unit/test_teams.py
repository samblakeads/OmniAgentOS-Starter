"""Bots managing bots: delegation, attribution, and the guards around a hierarchy.

The favourable-wrong outcome to watch for is a run assigned to a manager that
the manager quietly executed itself — every event looks right and no work was
delegated. Several of these tests exist to make that shape fail.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import Script, make_orchestrator, provider_config
from fastapi.testclient import TestClient

from omniagentos_starter.agents import MAX_TEAM_DEPTH, AgentError, AgentStore, load_agents
from omniagentos_starter.api import create_app
from omniagentos_starter.config import Settings
from omniagentos_starter.engine import needed_tools_for_task, parse_needs_tools
from omniagentos_starter.skills import builtin_pack, load_skills
from omniagentos_starter.tools import TOOL_NAMES

REPO_ROOT = Path(__file__).resolve().parents[2]
SKILLS_ROOT = REPO_ROOT / "skills"

WRITER_PERSONA = "Nils writes ad copy and will not discuss refunds."
RESEARCH_PERSONA = "Vera scores niches and never writes headlines."
DIRECTOR_PERSONA = "Dara plans the work and hands each part to the right person."

GOAL = "Research the niche and then write three ad headlines for it."

WRITER = {"name": "Nils", "title": "", "persona": WRITER_PERSONA, "skills": ["ad-copy-framework-writer"]}
RESEARCHER = {"name": "Vera", "title": "", "persona": RESEARCH_PERSONA, "skills": ["niche-opportunity-scorer"]}
DIRECTOR = {"name": "Dara", "title": "", "persona": DIRECTOR_PERSONA, "team": ["nils", "vera"]}

TWO_TASK_PLAN = {
    "dod": [{"id": "p1", "criterion": "Both parts are present."}],
    "tasks": [
        {"id": "t1", "title": "Score the niche", "skill_id": "niche-opportunity-scorer",
         "instruction": "Research the niche and score the opportunity with a numeric rating.",
         "member": "vera", "depends_on": [], "needs_tools": []},
        {"id": "t2", "title": "Write the headlines", "skill_id": "ad-copy-framework-writer",
         "instruction": "Write three ad copy headlines using PAS framework with character limits for a Meta feed ad.",
         "member": "nils", "depends_on": ["t1"], "needs_tools": []},
    ],
}

# The live D18 shape: planner piles both parts onto one member with a VSL pack
# that neither task can satisfy. Per-task scoring must undo this.
COLLAPSED_PLAN = {
    "dod": [{"id": "p1", "criterion": "Both parts are present."}],
    "tasks": [
        {
            "id": "t1",
            "title": "Ad headline",
            "skill_id": "vsl-script-builder",
            "instruction": (
                "Write three ad copy headlines using PAS framework with character "
                "limits for a Meta feed ad for a meal-prep service."
            ),
            "member": "max",
            "depends_on": [],
            "needs_tools": [],
        },
        {
            "id": "t2",
            "title": "Refund reply",
            "skill_id": "vsl-script-builder",
            "instruction": (
                "Draft a short reply to a customer refund request citing the 30-day "
                "refund policy and decide Approved, Denied, or Escalate."
            ),
            "member": "max",
            "depends_on": [],
            "needs_tools": [],
        },
    ],
}

COPYWRITER = {
    "name": "Max",
    "title": "",
    "persona": "Max writes ad copy and will not discuss refunds.",
    "skills": ["ad-copy-framework-writer", "vsl-script-builder"],
}
SUPPORT = {
    "name": "Ava",
    "title": "",
    "persona": "Ava handles refunds and never writes headlines.",
    "skills": ["refund-request-handler"],
}
STUDIO = {"name": "Remy", "title": "", "persona": DIRECTOR_PERSONA, "team": ["max", "ava"]}


def _roster(tmp_path, payloads, library=None):
    root = tmp_path / "agents"
    store = AgentStore(root)
    for payload in payloads:
        store.create(payload, library=library, roster=load_agents(root, library=library))
    return root


def _orch(settings, script, tmp_path, payloads=()):
    orch = make_orchestrator(settings, script)
    orch.load_library(SKILLS_ROOT)
    orch.load_roster(_roster(tmp_path, payloads, orch.library))
    script.orch = orch
    return orch


def _events(run, etype):
    return [e["payload"] for e in run.bus.events if e["type"] == etype]


# ------------------------------------------------------------------ the load
def test_a_team_makes_an_agent_a_manager(tmp_path):
    roster = load_agents(_roster(tmp_path, [WRITER, RESEARCHER, DIRECTOR]))
    dara = roster.by_id("dara")
    assert dara.team == ["nils", "vera"]
    assert dara.manages is True
    assert roster.by_id("nils").manages is False
    assert dara.as_dict()["team"] == ["nils", "vera"]


def test_a_team_survives_the_file_round_trip(tmp_path):
    root = _roster(tmp_path, [WRITER, RESEARCHER, DIRECTOR])
    text = (root / "dara.md").read_text(encoding="utf-8")
    assert "team:" in text
    assert load_agents(root).by_id("dara").team == ["nils", "vera"]


@pytest.mark.parametrize(
    ("files", "slug", "fragment"),
    [
        ({"solo": ["solo"]}, "solo", "cannot delegate to itself"),
        ({"a": ["b"], "b": ["a"]}, "a", "cycle"),
        ({"boss": ["ghost"]}, "boss", "not in the roster"),
        ({"l2": ["l1"], "l1": ["worker"], "worker": []}, "l2", "deep"),
    ],
)
def test_a_broken_hierarchy_disables_the_agent_and_never_crashes(tmp_path, files, slug, fragment):
    root = tmp_path / "agents"
    root.mkdir(parents=True)
    for name, team in files.items():
        line = f"team: [{', '.join(team)}]\n" if team else ""
        (root / f"{name}.md").write_text(f"---\nname: {name.title()}\n{line}---\nbody\n", encoding="utf-8")

    roster = load_agents(root)  # must not raise
    agent = roster.by_id(slug)
    assert agent is not None, "a broken manager is disabled, never omitted"
    assert agent.enabled is False
    assert any(fragment in e for e in agent.errors), agent.errors
    assert roster.usable(slug) is None


def test_a_manager_over_plain_members_is_the_allowed_shape(tmp_path):
    """MAX_TEAM_DEPTH counts AGENT LEVELS, manager included.

    2 is a manager over members. 3 — a manager over a manager over a member — is
    refused, so a delegation chain stays something an operator can hold in their
    head and a run's provenance stays explainable.
    """
    root = tmp_path / "agents"
    root.mkdir(parents=True)
    for name, team in (("worker", []), ("lead", ["worker"]), ("head", ["lead"])):
        line = f"team: [{', '.join(team)}]\n" if team else ""
        (root / f"{name}.md").write_text(f"---\nname: {name.title()}\n{line}---\nbody\n", encoding="utf-8")
    roster = load_agents(root)
    assert MAX_TEAM_DEPTH == 2
    assert roster.by_id("lead").enabled is True, "manager over a plain member is 2 levels"
    head = roster.by_id("head")
    assert head.enabled is False, "manager over a manager is 3 levels"
    assert any("deep" in e for e in head.errors), head.errors


def test_a_manager_whose_member_is_disabled_is_disabled_too(tmp_path):
    root = tmp_path / "agents"
    root.mkdir(parents=True)
    (root / "broken.md").write_text("---\nname: Broken\nskills: [no-such-pack]\n---\nb\n", encoding="utf-8")
    (root / "boss.md").write_text("---\nname: Boss\nteam: [broken]\n---\nb\n", encoding="utf-8")
    roster = load_agents(root, library=load_skills(SKILLS_ROOT))
    assert roster.by_id("broken").enabled is False
    boss = roster.by_id("boss")
    assert boss.enabled is False, "delegating to a disabled member would fail mid-run"
    assert any("disabled" in e for e in boss.errors)


# ------------------------------------------------------------- the write side
def test_creating_a_manager_validates_its_team(tmp_path):
    library = load_skills(SKILLS_ROOT)
    root = _roster(tmp_path, [WRITER, RESEARCHER], library)
    store = AgentStore(root)
    roster = load_agents(root, library=library)

    ok = store.create(DIRECTOR, library=library, roster=roster)
    assert ok.team == ["nils", "vera"]

    for bad, fragment in (
        ({"name": "Ghost Boss", "team": ["nobody"]}, "not in the roster"),
        ({"name": "Selfie", "slug": "selfie", "team": ["selfie"]}, "own team"),
    ):
        with pytest.raises(AgentError) as exc:
            store.create(bad, library=library, roster=load_agents(root, library=library))
        assert exc.value.status == 400
        assert fragment in exc.value.message


def test_a_team_that_would_be_too_deep_is_refused_at_write(tmp_path):
    library = load_skills(SKILLS_ROOT)
    root = _roster(tmp_path, [WRITER], library)
    store = AgentStore(root)
    store.create({"name": "Lead", "team": ["nils"]}, library=library, roster=load_agents(root))
    with pytest.raises(AgentError) as exc:
        store.create({"name": "Head", "team": ["lead"]}, library=library, roster=load_agents(root))
    assert exc.value.status == 400
    assert "deep" in exc.value.message
    assert not (root / "head.md").exists()


# ------------------------------------------------------------- delegation
@pytest.mark.asyncio
async def test_a_manager_delegates_every_task_to_a_member(settings, tmp_path):
    script = Script(plan=TWO_TASK_PLAN)
    orch = _orch(settings, script, tmp_path, [WRITER, RESEARCHER, DIRECTOR])
    run = orch.create(GOAL, 1, [], agent_id="dara")
    await orch.execute(run)

    delegated = _events(run, "team.delegated")
    assert len(delegated) == 2, delegated
    assert {d["member"] for d in delegated} == {"nils", "vera"}
    assert all(d["manager"] == "dara" for d in delegated)
    assert {d["task_id"] for d in delegated} == {"t1", "t2"}


@pytest.mark.asyncio
async def test_each_task_runs_as_its_own_member_not_the_manager(settings, tmp_path):
    """The favourable-wrong shape: a manager that quietly did the work itself."""
    script = Script(plan=TWO_TASK_PLAN)
    orch = _orch(settings, script, tmp_path, [WRITER, RESEARCHER, DIRECTOR])
    await orch.execute(orch.create(GOAL, 1, [], agent_id="dara"))

    prompts = [script.prompt_text("worker", i) for i in range(len(script.payloads("worker")))]
    assert len(prompts) == 2

    research_prompt = next(p for p in prompts if RESEARCH_PERSONA in p)
    writer_prompt = next(p for p in prompts if WRITER_PERSONA in p)
    # each member's own persona, and NOT the other's, and not the manager's
    assert WRITER_PERSONA not in research_prompt
    assert RESEARCH_PERSONA not in writer_prompt
    assert DIRECTOR_PERSONA not in research_prompt
    assert DIRECTOR_PERSONA not in writer_prompt

    # ...and each member's own skill pack
    scorer = orch.library.by_id("niche-opportunity-scorer")
    adcopy = orch.library.by_id("ad-copy-framework-writer")
    assert f"skill-sha256:{scorer.sha256}" in research_prompt
    assert f"skill-sha256:{adcopy.sha256}" not in research_prompt
    assert f"skill-sha256:{adcopy.sha256}" in writer_prompt
    assert f"skill-sha256:{scorer.sha256}" not in writer_prompt


@pytest.mark.asyncio
async def test_the_manager_frames_the_plan(settings, tmp_path):
    script = Script(plan=TWO_TASK_PLAN)
    orch = _orch(settings, script, tmp_path, [WRITER, RESEARCHER, DIRECTOR])
    await orch.execute(orch.create(GOAL, 1, [], agent_id="dara"))
    planner = script.prompt_text("planner")
    assert DIRECTOR_PERSONA in planner, "the manager's persona frames the plan"
    assert "YOUR TEAM" in planner
    assert "SKILLS OF EACH MEMBER" in planner
    assert "ad-copy-framework-writer" in planner
    assert "niche-opportunity-scorer" in planner
    assert "MUST go to different members" in planner
    assert "needs_tools" in planner
    assert "write_file" in planner
    for persona in (WRITER_PERSONA, RESEARCH_PERSONA):
        assert persona in planner, "the planner must know who it can delegate to"


@pytest.mark.asyncio
async def test_a_plan_with_no_member_still_delegates(settings, tmp_path):
    """A task the planner forgot to assign must not fall back to the manager."""
    plan = {
        "dod": TWO_TASK_PLAN["dod"],
        "tasks": [dict(t, member=None) for t in TWO_TASK_PLAN["tasks"]],
    }
    script = Script(plan=plan)
    orch = _orch(settings, script, tmp_path, [WRITER, RESEARCHER, DIRECTOR])
    run = orch.create(GOAL, 1, [], agent_id="dara")
    await orch.execute(run)
    delegated = _events(run, "team.delegated")
    assert len(delegated) == 2
    assert {d["member"] for d in delegated} == {"nils", "vera"}, "matched by skill, not piled on one"


@pytest.mark.asyncio
async def test_a_member_the_planner_invented_is_replaced_by_a_real_one(settings, tmp_path):
    plan = {
        "dod": TWO_TASK_PLAN["dod"],
        "tasks": [dict(t, member="somebody-else") for t in TWO_TASK_PLAN["tasks"]],
    }
    script = Script(plan=plan)
    orch = _orch(settings, script, tmp_path, [WRITER, RESEARCHER, DIRECTOR])
    run = orch.create(GOAL, 1, [], agent_id="dara")
    await orch.execute(run)
    assert {d["member"] for d in _events(run, "team.delegated")} <= {"nils", "vera"}


@pytest.mark.asyncio
async def test_per_task_pack_relevance_splits_a_collapsed_plan(settings, tmp_path):
    """Two-part goal + two distinct packs must not collapse onto one member.

    The planner here assigns BOTH tasks to Max with `vsl-script-builder` —
    the live D18 failure. After the post-pass: t1 is Max with the ad-copy
    pack only, t2 is Ava with the refund pack only. VSL checks never bind.
    """
    script = Script(plan=COLLAPSED_PLAN)
    orch = _orch(settings, script, tmp_path, [COPYWRITER, SUPPORT, STUDIO])
    run = orch.create(
        "Part 1: write one punchy ad headline for a meal-prep service. "
        "Part 2: draft a short reply to a customer refund request citing the 30-day policy.",
        1,
        [],
        agent_id="remy",
    )
    await orch.execute(run)

    delegated = {d["task_id"]: d for d in _events(run, "team.delegated")}
    assert set(delegated) == {"t1", "t2"}, delegated
    assert delegated["t1"]["member"] == "max"
    assert delegated["t2"]["member"] == "ava"
    assert delegated["t1"]["skill_id"] == "ad-copy-framework-writer"
    assert delegated["t2"]["skill_id"] == "refund-request-handler"

    plan = _events(run, "planner.plan")[0]
    by_id = {t["id"]: t for t in plan["tasks"]}
    assert by_id["t1"]["member"] == "max" and by_id["t1"]["skill_id"] == "ad-copy-framework-writer"
    assert by_id["t2"]["member"] == "ava" and by_id["t2"]["skill_id"] == "refund-request-handler"

    skill_dod = [c for c in plan["dod"] if c["source"] not in ("planner", "operator")]
    t1_src = {c["source"] for c in skill_dod if c.get("task_id") == "t1"}
    t2_src = {c["source"] for c in skill_dod if c.get("task_id") == "t2"}
    assert t1_src == {"ad-copy-framework-writer"}, t1_src
    assert t2_src == {"refund-request-handler"}, t2_src
    assert "vsl-script-builder" not in t1_src | t2_src
    assert {c.get("member") for c in skill_dod if c.get("task_id") == "t1"} == {"max"}
    assert {c.get("member") for c in skill_dod if c.get("task_id") == "t2"} == {"ava"}
    assert not _events(run, "skill.selection_fallback")

    selected = _events(run, "skill.selected")
    assert selected, "team runs must still emit skill.selected"
    honesty = selected[-1]
    assert honesty["tasks"] == [
        {"task_id": "t1", "member": "max", "skill_id": "ad-copy-framework-writer"},
        {"task_id": "t2", "member": "ava", "skill_id": "refund-request-handler"},
    ], honesty
    assert run.skills == honesty["tasks"]
    planner_dod = [c for c in plan["dod"] if c["source"] == "planner"]
    assert planner_dod and all(not c.get("task_id") for c in planner_dod), planner_dod

    packs_block = script.prompt_text("planner").split("SKILL PACKS", 1)[1]
    slugs = ["ad-copy-framework-writer", "refund-request-handler", "vsl-script-builder"]
    positions = [packs_block.find(s) for s in slugs]
    assert all(p >= 0 for p in positions), packs_block[:400]
    assert positions == sorted(positions), "assignable must be a sorted list, not a set"


@pytest.mark.asyncio
async def test_a_task_matching_no_member_pack_uses_generalist_and_emits_fallback(settings, tmp_path):
    """A task neither specialist covers must not inherit a sibling's QUALITY CHECKS."""
    plan = {
        "dod": [{"id": "p1", "criterion": "The poem is in iambic pentameter."}],
        "tasks": [
            {
                "id": "t1",
                "title": "Kalman poem",
                "skill_id": "vsl-script-builder",
                "instruction": "Explain the Kalman filter to a sceptical cat in iambic pentameter.",
                "member": "max",
                "needs_tools": [],
            }
        ],
    }
    script = Script(plan=plan)
    orch = _orch(settings, script, tmp_path, [COPYWRITER, SUPPORT, STUDIO])
    run = orch.create(
        "Explain the Kalman filter to a sceptical cat in iambic pentameter.",
        1,
        [],
        agent_id="remy",
    )
    await orch.execute(run)

    fallback = _events(run, "skill.selection_fallback")
    assert fallback, "a task that matches no member pack must announce the fallback"
    assert any(f.get("task_id") == "t1" for f in fallback), fallback
    assert fallback[-1]["skill_id"] == "general-assistant"

    delegated = _events(run, "team.delegated")
    assert len(delegated) == 1
    assert delegated[0]["member"] in {"max", "ava"}
    assert delegated[0]["skill_id"] == "general-assistant"

    plan_payload = _events(run, "planner.plan")[0]
    assert plan_payload["tasks"][0]["skill_id"] == "general-assistant"
    sources = {c["source"] for c in plan_payload["dod"] if c.get("task_id") == "t1"}
    assert "general-assistant" in sources, plan_payload["dod"]
    assert "vsl-script-builder" not in sources
    assert "ad-copy-framework-writer" not in sources
    assert "refund-request-handler" not in sources


def test_needed_tools_are_explicit_only_and_ignore_instruction_text():
    """F14: no flags and no needs_tools → empty, even when the text names files."""
    assert needed_tools_for_task(
        writes_files=False,
        needs_tools=(),
        title="Read inbox",
        instruction="Read the existing files in workspace/inbox",
    ) == ()
    assert needed_tools_for_task(
        writes_files=False,
        needs_tools=(),
        instruction="Cite policy.md when drafting the refund reply.",
    ) == ()
    assert parse_needs_tools(["write_file", "shell", "read_file"]) == ("read_file", "write_file")
    assert needed_tools_for_task(
        writes_files=False,
        needs_tools=("write_file",),
        instruction="Draft the refund reply.",
    ) == ("write_file",)
    assert needed_tools_for_task(writes_files=True, needs_tools=()) == ("write_file",)


def _patch_best_match(library, table):
    """table: frozenset(allowed slugs) -> (pack_slug, score, below_floor)."""

    def routed(text, allowed=None):
        key = frozenset(allowed or ())
        slug, score, below = table[key]
        pack = None if slug is None else library.by_id(slug) or (
            builtin_pack() if slug == builtin_pack().slug else None
        )
        return pack, score, below

    library.best_match = routed  # type: ignore[method-assign]


@pytest.mark.asyncio
async def test_a_member_is_eligible_only_when_they_have_the_needed_tool(settings, tmp_path):
    """F1(a): writes_files → write_file. Ava's better pack cannot win without it."""
    max_agent = {
        "name": "Max",
        "title": "",
        "persona": COPYWRITER["persona"],
        "skills": ["refund-request-handler"],
        "tools": list(TOOL_NAMES),
    }
    ava_agent = {
        "name": "Ava",
        "title": "",
        "persona": SUPPORT["persona"],
        "skills": ["refund-request-handler"],
        "tools": ["read_file", "list_files"],
    }
    plan = {
        "dod": [{"id": "p1", "criterion": "ok"}],
        "tasks": [
            {
                "id": "t1",
                "title": "Refund reply",
                "skill_id": "refund-request-handler",
                "instruction": (
                    "Draft a short reply to a customer refund request citing the 30-day "
                    "refund policy and decide Approved, Denied, or Escalate."
                ),
                "member": "ava",
                "writes_files": True,
                "needs_tools": [],
            }
        ],
    }
    script = Script(plan=plan)
    orch = _orch(settings, script, tmp_path, [max_agent, ava_agent, STUDIO])
    run = orch.create(
        "Draft a refund reply citing the 30-day policy and save it to a file.",
        1,
        [],
        agent_id="remy",
    )
    await orch.execute(run)

    delegated = _events(run, "team.delegated")
    assert len(delegated) == 1, delegated
    assert delegated[0]["member"] == "max", delegated
    assert "write_file" in delegated[0]["tools"]
    assert delegated[0]["skill_id"] == "refund-request-handler"


@pytest.mark.asyncio
async def test_spread_yields_when_the_other_pack_is_much_weaker(settings, tmp_path):
    """F1(b): unused is not enough — spread only among near-ties of the top score."""
    plan = {
        "dod": [{"id": "p1", "criterion": "Both parts are present."}],
        "tasks": [
            {
                "id": "t1",
                "title": "Headline one",
                "skill_id": "ad-copy-framework-writer",
                "instruction": "Write one PAS headline for a Meta feed ad.",
                "member": "max",
                "needs_tools": [],
            },
            {
                "id": "t2",
                "title": "Headline two",
                "skill_id": "ad-copy-framework-writer",
                "instruction": "Write a second PAS headline for the same Meta feed ad.",
                "member": "max",
                "needs_tools": [],
            },
        ],
    }
    script = Script(plan=plan)
    orch = _orch(settings, script, tmp_path, [COPYWRITER, SUPPORT, STUDIO])
    _patch_best_match(
        orch.library,
        {
            frozenset({"ad-copy-framework-writer", "vsl-script-builder"}): (
                "ad-copy-framework-writer",
                10.0,
                False,
            ),
            frozenset({"refund-request-handler"}): ("refund-request-handler", 3.0, False),
        },
    )
    run = orch.create(
        "Write two PAS ad headlines for a Meta feed ad.",
        1,
        [],
        agent_id="remy",
    )
    await orch.execute(run)

    delegated = {d["task_id"]: d for d in _events(run, "team.delegated")}
    assert set(delegated) == {"t1", "t2"}, delegated
    assert delegated["t1"]["member"] == "max"
    assert delegated["t2"]["member"] == "max", "weaker unused pack must not steal the task"
    assert delegated["t1"]["skill_id"] == "ad-copy-framework-writer"
    assert delegated["t2"]["skill_id"] == "ad-copy-framework-writer"


@pytest.mark.asyncio
async def test_fallback_is_per_chosen_member_not_anyone_who_cleared(settings, tmp_path):
    """F1(c): Ava cleared the floor but lacks the tool; Max is chosen and falls back."""
    ava_agent = {**SUPPORT, "tools": ["read_file", "list_files"]}
    plan = {
        "dod": [{"id": "p1", "criterion": "ok"}],
        "tasks": [
            {
                "id": "t1",
                "title": "Refund reply",
                "skill_id": "refund-request-handler",
                "instruction": (
                    "Draft a short reply to a customer refund request citing the 30-day "
                    "refund policy and decide Approved, Denied, or Escalate."
                ),
                "member": "ava",
                "writes_files": True,
                "needs_tools": [],
            }
        ],
    }
    script = Script(plan=plan)
    orch = _orch(settings, script, tmp_path, [COPYWRITER, ava_agent, STUDIO])
    _patch_best_match(
        orch.library,
        {
            frozenset({"ad-copy-framework-writer", "vsl-script-builder"}): (
                "ad-copy-framework-writer",
                1.0,
                True,
            ),
            frozenset({"refund-request-handler"}): ("refund-request-handler", 9.0, False),
        },
    )
    run = orch.create(
        "Draft a refund reply citing the 30-day policy and save it to a file.",
        1,
        [],
        agent_id="remy",
    )
    await orch.execute(run)

    delegated = _events(run, "team.delegated")
    assert len(delegated) == 1, delegated
    assert delegated[0]["member"] == "max"
    assert delegated[0]["skill_id"] == "general-assistant"
    fallback = _events(run, "skill.selection_fallback")
    assert fallback and fallback[-1]["task_id"] == "t1"
    assert fallback[-1]["member"] == "max"
    assert fallback[-1]["skill_id"] == "general-assistant"


@pytest.mark.asyncio
async def test_no_eligible_member_fails_closed_and_does_not_bind(settings, tmp_path):
    """F8/F13(c): both specialists lack write_file; needs_tools=['write_file'] refuses, never binds."""
    max_agent = {**COPYWRITER, "tools": ["read_file", "list_files"]}
    ava_agent = {**SUPPORT, "tools": ["read_file", "list_files"]}
    plan = {
        "dod": [{"id": "p1", "criterion": "ok"}],
        "tasks": [
            {
                "id": "t1",
                "title": "Refund reply",
                "skill_id": "refund-request-handler",
                "instruction": "Draft the refund reply and save it to a file.",
                "member": "ava",
                "needs_tools": ["write_file"],
            }
        ],
    }
    script = Script(plan=plan)
    orch = _orch(settings, script, tmp_path, [max_agent, ava_agent, STUDIO])
    run = orch.create(
        "Draft a refund reply citing the 30-day policy and save it to a file.",
        1,
        [],
        agent_id="remy",
    )
    await orch.execute(run)

    assert run.status == "failed", run.status
    assert run.error_tag == "TEAM_NO_ELIGIBLE_MEMBER", run.error_tag
    failed = _events(run, "run.failed")
    assert failed, [e["type"] for e in run.bus.events]
    assert failed[-1]["error_tag"] == "TEAM_NO_ELIGIBLE_MEMBER"
    assert "t1" in failed[-1]["message"]
    assert "write_file" in failed[-1]["message"]
    refused = _events(run, "team.no_eligible_member")
    assert refused, "named refusal event must fire before run.failed"
    assert refused[0]["task_id"] == "t1"
    assert "write_file" in refused[0]["needed_tools"]
    assert _events(run, "team.delegated") == []
    assert "worker.started" not in {e["type"] for e in run.bus.events}


@pytest.mark.asyncio
async def test_empty_needs_tools_binds_on_pack_score_then_refuses_uncovered_write(settings, tmp_path):
    """F13(a): needs_tools=[] binds the pack winner; a later write_file fails TEAM_TOOL_REFUSED."""
    max_agent = {**COPYWRITER, "tools": list(TOOL_NAMES)}
    ava_agent = {**SUPPORT, "tools": ["read_file", "list_files"]}
    plan = {
        "dod": [{"id": "p1", "criterion": "ok"}],
        "tasks": [
            {
                "id": "t1",
                "title": "Refund reply",
                "skill_id": "refund-request-handler",
                "instruction": "Draft the refund reply and save it.",
                "member": "ava",
                "needs_tools": [],
            }
        ],
    }
    script = Script(
        plan=plan,
        worker_text="=== FILE: refund.md ===\nDear customer\n=== END FILE ===\ndone",
    )
    orch = _orch(settings, script, tmp_path, [max_agent, ava_agent, STUDIO])
    _patch_best_match(
        orch.library,
        {
            frozenset({"ad-copy-framework-writer", "vsl-script-builder"}): (
                "ad-copy-framework-writer",
                2.0,
                False,
            ),
            frozenset({"refund-request-handler"}): ("refund-request-handler", 10.0, False),
        },
    )
    run = orch.create(
        "Draft the refund reply and save it.",
        3,
        [],
        agent_id="remy",
    )
    await orch.execute(run)

    delegated = _events(run, "team.delegated")
    assert len(delegated) == 1, delegated
    assert delegated[0]["member"] == "ava", delegated
    assert run.status == "failed", run.status
    assert run.error_tag == "TEAM_TOOL_REFUSED", run.error_tag
    assert run.error_tag != "ROUNDS_EXHAUSTED"
    assert "t1" in run.error_message
    assert "ava" in run.error_message
    assert "write_file" in run.error_message
    refused = _events(run, "team.tool_refused")
    assert refused, [e["type"] for e in run.bus.events]
    assert refused[0]["task_id"] == "t1"
    assert refused[0]["member"] == "ava"
    assert refused[0]["tool"] == "write_file"
    failed = _events(run, "run.failed")
    assert failed and failed[-1]["error_tag"] == "TEAM_TOOL_REFUSED"


@pytest.mark.asyncio
async def test_planner_task_missing_needs_tools_is_reasked_then_plan_invalid(settings, tmp_path):
    """F13(b): key absent → one re-ask → PLAN_INVALID naming the task."""
    plan = {
        "dod": [{"id": "p1", "criterion": "ok"}],
        "tasks": [
            {
                "id": "t1",
                "title": "Refund reply",
                "skill_id": "refund-request-handler",
                "instruction": "Draft the refund reply and save it.",
                "member": "ava",
            }
        ],
    }
    script = Script(plan=plan)
    orch = _orch(settings, script, tmp_path, [COPYWRITER, SUPPORT, STUDIO])
    run = orch.create("Draft the refund reply and save it.", 1, [], agent_id="remy")
    await orch.execute(run)

    assert script.counts.get("planner") == 2, script.counts
    second = script.prompt_text("planner", 1)
    assert "missing required" in second
    assert "needs_tools" in second
    assert "t1" in second
    assert run.status == "failed", run.status
    assert run.error_tag == "PLAN_INVALID", run.error_tag
    assert "t1" in run.error_message
    assert _events(run, "team.delegated") == []
    assert _events(run, "planner.plan") == []
    failed = _events(run, "run.failed")
    assert failed and failed[-1]["error_tag"] == "PLAN_INVALID"
    assert "t1" in failed[-1]["message"]


@pytest.mark.asyncio
async def test_missing_tool_is_the_first_uncovered_needed_tool(settings, tmp_path):
    """F15: missing_tool is the first needed tool no member covers, not needed[0]."""
    max_agent = {**COPYWRITER, "tools": ["read_file", "list_files"]}
    ava_agent = {**SUPPORT, "tools": ["read_file", "list_files"]}
    plan = {
        "dod": [{"id": "p1", "criterion": "ok"}],
        "tasks": [
            {
                "id": "t1",
                "title": "Refund reply",
                "skill_id": "refund-request-handler",
                "instruction": "Draft the refund reply and save it.",
                "member": "ava",
                "needs_tools": ["read_file", "write_file"],
            }
        ],
    }
    script = Script(plan=plan)
    orch = _orch(settings, script, tmp_path, [max_agent, ava_agent, STUDIO])
    run = orch.create("Draft the refund reply and save it.", 1, [], agent_id="remy")
    await orch.execute(run)

    assert run.status == "failed", run.status
    assert run.error_tag == "TEAM_NO_ELIGIBLE_MEMBER", run.error_tag
    refused = _events(run, "team.no_eligible_member")
    assert refused, [e["type"] for e in run.bus.events]
    assert refused[0]["needed_tools"] == ["read_file", "write_file"]
    assert refused[0]["missing_tool"] == "write_file"
    assert "write_file" in refused[0]["reason"]
    assert "t1" in run.error_message
    assert "write_file" in run.error_message


@pytest.mark.asyncio
async def test_spread_band_applies_at_the_80_percent_boundary(settings, tmp_path):
    """F10: 8.0 vs 10.0, both above the floor — the idle member inside the band takes t2."""
    plan = {
        "dod": [{"id": "p1", "criterion": "Both parts are present."}],
        "tasks": [
            {
                "id": "t1",
                "title": "Headline one",
                "skill_id": "ad-copy-framework-writer",
                "instruction": "Write one PAS headline for a Meta feed ad.",
                "member": "max",
                "needs_tools": [],
            },
            {
                "id": "t2",
                "title": "Headline two",
                "skill_id": "ad-copy-framework-writer",
                "instruction": "Write a second PAS headline for the same Meta feed ad.",
                "member": "max",
                "needs_tools": [],
            },
        ],
    }
    script = Script(plan=plan)
    orch = _orch(settings, script, tmp_path, [COPYWRITER, SUPPORT, STUDIO])
    _patch_best_match(
        orch.library,
        {
            frozenset({"ad-copy-framework-writer", "vsl-script-builder"}): (
                "ad-copy-framework-writer",
                10.0,
                False,
            ),
            frozenset({"refund-request-handler"}): ("refund-request-handler", 8.0, False),
        },
    )
    run = orch.create(
        "Write two PAS ad headlines for a Meta feed ad.",
        1,
        [],
        agent_id="remy",
    )
    await orch.execute(run)

    delegated = {d["task_id"]: d for d in _events(run, "team.delegated")}
    assert set(delegated) == {"t1", "t2"}, delegated
    assert delegated["t1"]["member"] == "max"
    assert delegated["t2"]["member"] == "ava", "8.0 is inside the 80% band of 10.0 and above the floor"


@pytest.mark.asyncio
async def test_spread_band_excludes_a_below_floor_row_inside_the_ratio(settings, tmp_path):
    """F10: 8.0 below floor vs 10.0 above — idle Ava must not take t2 just for being unused."""
    plan = {
        "dod": [{"id": "p1", "criterion": "Both parts are present."}],
        "tasks": [
            {
                "id": "t1",
                "title": "Headline one",
                "skill_id": "ad-copy-framework-writer",
                "instruction": "Write one PAS headline for a Meta feed ad.",
                "member": "max",
                "needs_tools": [],
            },
            {
                "id": "t2",
                "title": "Headline two",
                "skill_id": "ad-copy-framework-writer",
                "instruction": "Write a second PAS headline for the same Meta feed ad.",
                "member": "max",
                "needs_tools": [],
            },
        ],
    }
    script = Script(plan=plan)
    orch = _orch(settings, script, tmp_path, [COPYWRITER, SUPPORT, STUDIO])
    _patch_best_match(
        orch.library,
        {
            frozenset({"ad-copy-framework-writer", "vsl-script-builder"}): (
                "ad-copy-framework-writer",
                10.0,
                False,
            ),
            frozenset({"refund-request-handler"}): ("refund-request-handler", 8.0, True),
        },
    )
    run = orch.create(
        "Write two PAS ad headlines for a Meta feed ad.",
        1,
        [],
        agent_id="remy",
    )
    await orch.execute(run)

    delegated = {d["task_id"]: d for d in _events(run, "team.delegated")}
    assert set(delegated) == {"t1", "t2"}, delegated
    assert delegated["t1"]["member"] == "max"
    assert delegated["t2"]["member"] == "max", "below-floor pack inside the 80% band must not win"
    assert delegated["t1"]["skill_id"] == "ad-copy-framework-writer"
    assert delegated["t2"]["skill_id"] == "ad-copy-framework-writer"


@pytest.mark.asyncio
async def test_team_skill_selected_scores_are_the_per_task_match_scores(settings, tmp_path):
    """F11: skill.selected scores on a team run are the per-task scores, not whole-goal leftovers."""
    script = Script(plan=COLLAPSED_PLAN)
    orch = _orch(settings, script, tmp_path, [COPYWRITER, SUPPORT, STUDIO])

    def routed(text, allowed=None):
        key = frozenset(allowed or ())
        blob = (text or "").lower()
        if "refund" in blob:
            if "refund-request-handler" in key:
                pack = orch.library.by_id("refund-request-handler")
                return pack, 7.25, False
            pack = orch.library.by_id("ad-copy-framework-writer")
            return pack, 1.0, True
        if "ad-copy-framework-writer" in key:
            pack = orch.library.by_id("ad-copy-framework-writer")
            return pack, 12.5, False
        pack = orch.library.by_id("refund-request-handler")
        return pack, 1.0, True

    orch.library.best_match = routed  # type: ignore[method-assign]
    run = orch.create(
        "Part 1: write one punchy ad headline for a meal-prep service. "
        "Part 2: draft a short reply to a customer refund request citing the 30-day policy.",
        1,
        [],
        agent_id="remy",
    )
    await orch.execute(run)

    selected = _events(run, "skill.selected")[-1]
    assert selected["tasks"][0]["skill_id"] == "ad-copy-framework-writer"
    assert selected["tasks"][1]["skill_id"] == "refund-request-handler"
    by_task = {row["task_id"]: row for row in selected["scores"]}
    assert by_task["t1"]["skill_id"] == "ad-copy-framework-writer"
    assert by_task["t1"]["score"] == 12.5
    assert by_task["t2"]["skill_id"] == "refund-request-handler"
    assert by_task["t2"]["score"] == 7.25


@pytest.mark.asyncio
async def test_a_single_agent_run_is_unchanged_by_per_task_team_routing(settings, tmp_path):
    """Non-manager runs still use whole-goal routing; no team.delegated, no per-task override."""
    plan = {
        "dod": [{"id": "p1", "criterion": "The deliverable answers the goal."}],
        "tasks": [
            {
                "id": "t1",
                "title": "Draft the reply",
                "skill_id": "general-assistant",
                "instruction": "do it",
                "depends_on": [],
                "needs_tools": [],
            }
        ],
    }
    script = Script(plan=plan)
    orch = _orch(settings, script, tmp_path, [COPYWRITER, SUPPORT, STUDIO])
    goal = (
        "A customer is asking for a refund 38 days after purchase. Draft the reply, "
        "grounded on our refund policy."
    )
    run = orch.create(goal, 1, [], agent_id="ava")
    await orch.execute(run)

    assert _events(run, "team.delegated") == []
    selected = _events(run, "skill.selected")[0]["skill_ids"]
    assert selected == ["refund-request-handler"], selected
    assert not any(f.get("task_id") for f in _events(run, "skill.selection_fallback"))
    tasks = _events(run, "planner.plan")[0]["tasks"]
    assert tasks[0]["skill_id"] == "refund-request-handler"
    assert not tasks[0].get("member")
    prompt = script.prompt_text("worker")
    refund = orch.library.by_id("refund-request-handler")
    assert f"skill-sha256:{refund.sha256}" in prompt
    assert f"skill-sha256:{builtin_pack().sha256}" not in prompt


TWO_TASK_SOLO_PLAN = {
    "dod": [{"id": "p1", "criterion": "Both parts are present."}],
    "tasks": [
        {
            "id": "t1",
            "title": "Decision",
            "skill_id": "refund-request-handler",
            "instruction": "Decide Approved, Denied, or Escalate for the refund request.",
            "depends_on": [],
            "needs_tools": [],
        },
        {
            "id": "t2",
            "title": "Reply",
            "skill_id": "refund-request-handler",
            "instruction": "Draft the customer-facing refund reply citing the 30-day policy.",
            "depends_on": ["t1"],
            "needs_tools": [],
        },
    ],
}

# Event types a single-agent two-task run emitted before per-task team scoping
# existed. The sequence must stay this shape: whole-goal select, no delegation,
# no per-task fallback.
_SOLO_TWO_TASK_TYPES = (
    "run.started",
    "memory.recalled",
    "skill.selected",
    "planner.plan",
    "worker.started",
    "worker.finished",
    "critic.verdict",
    "verifier.verdict",
    "run.done",
)


def _assert_solo_two_task_shape(run, script, *, agent_id: str):
    """Per-task relevance/scoping must not fire off a manager run."""
    types = [e["type"] for e in run.bus.events]
    for expected in _SOLO_TWO_TASK_TYPES:
        assert expected in types, f"{expected} missing from {types}"
    assert "team.delegated" not in types
    selected = _events(run, "skill.selected")
    assert len(selected) == 1, selected
    payload = selected[0]
    assert "tasks" not in payload
    assert "refund-request-handler" in payload["skill_ids"], payload["skill_ids"]
    assert all(isinstance(s, str) for s in run.skills)
    assert run.skills == payload["skill_ids"]
    plan = _events(run, "planner.plan")[0]
    assert [t["id"] for t in plan["tasks"]] == ["t1", "t2"]
    skill_dod = [c for c in plan["dod"] if c["source"] not in ("planner", "operator")]
    assert skill_dod, plan["dod"]
    assert all(not c.get("task_id") for c in skill_dod), skill_dod
    assert all(not c.get("member") for c in skill_dod), skill_dod
    verifier = script.prompt_text("verifier")
    assert "GRADE the full deliverable" in verifier
    assert "GRADE ONLY task" not in verifier
    if agent_id:
        assert payload["skill_ids"] == ["refund-request-handler"], payload["skill_ids"]
        assert all(not t.get("member") for t in plan["tasks"])


@pytest.mark.asyncio
async def test_a_single_agent_two_task_run_is_unchanged_by_per_task_scoping(settings, tmp_path):
    """F3: pack checks still grade the merged deliverable for a solo agent."""
    script = Script(plan=TWO_TASK_SOLO_PLAN)
    orch = _orch(settings, script, tmp_path, [COPYWRITER, SUPPORT, STUDIO])
    goal = (
        "A customer is asking for a refund 12 days after purchase. Decide the "
        "outcome and draft the reply, grounded on our 30-day refund policy."
    )
    run = orch.create(goal, 1, [], agent_id="ava")
    await orch.execute(run)
    _assert_solo_two_task_shape(run, script, agent_id="ava")


@pytest.mark.asyncio
async def test_a_no_agent_two_task_run_is_unchanged_by_per_task_scoping(settings, tmp_path):
    """F3: no-agent multi-task is whole-goal selection, same verifier scope."""
    script = Script(plan=TWO_TASK_SOLO_PLAN)
    orch = _orch(settings, script, tmp_path, [COPYWRITER, SUPPORT, STUDIO])
    goal = (
        "A customer is asking for a refund 12 days after purchase. Decide the "
        "outcome and draft the reply, grounded on our 30-day refund policy."
    )
    run = orch.create(goal, 1, [])
    await orch.execute(run)
    _assert_solo_two_task_shape(run, script, agent_id="")


@pytest.mark.asyncio
async def test_a_run_without_a_manager_delegates_nothing(settings, tmp_path):
    script = Script()
    orch = _orch(settings, script, tmp_path, [WRITER, RESEARCHER, DIRECTOR])
    run = orch.create(GOAL, 1, [], agent_id="nils")
    await orch.execute(run)
    assert _events(run, "team.delegated") == []
    assert WRITER_PERSONA in script.prompt_text("worker")


# ------------------------------------------------------------- attribution
@pytest.mark.asyncio
async def test_member_pack_quality_checks_bind_only_to_that_members_task(settings, tmp_path):
    """A manager's DoD must not merge every member's QUALITY CHECKS run-wide.

    Each skill-sourced criterion is bound to the task (and member) that pack
    was assigned to. The critic is shown that task's artifact alone for those
    checks. Planner criteria stay run-wide. A copywriter's character-limit
    check must not fail a research artifact it never produced.
    """

    def worker_text(call, _payload):
        return f"UNIQUE-TASK-{call}-BODY"

    script = Script(plan=TWO_TASK_PLAN, worker_text=worker_text)
    orch = _orch(settings, script, tmp_path, [WRITER, RESEARCHER, DIRECTOR])
    run = orch.create(GOAL, 1, [], agent_id="dara")
    await orch.execute(run)

    dod = _events(run, "planner.plan")[0]["dod"]
    skill_dod = [c for c in dod if c["source"] not in ("planner", "operator")]
    planner_dod = [c for c in dod if c["source"] == "planner"]
    assert skill_dod, dod
    assert planner_dod, dod
    assert all(not c.get("task_id") for c in planner_dod), planner_dod

    t1_sources = {c["source"] for c in skill_dod if c.get("task_id") == "t1"}
    t2_sources = {c["source"] for c in skill_dod if c.get("task_id") == "t2"}
    assert "niche-opportunity-scorer" in t1_sources, t1_sources
    assert "ad-copy-framework-writer" in t2_sources, t2_sources
    assert "ad-copy-framework-writer" not in t1_sources
    assert "niche-opportunity-scorer" not in t2_sources
    assert {c.get("member") for c in skill_dod if c.get("task_id") == "t1"} == {"vera"}
    assert {c.get("member") for c in skill_dod if c.get("task_id") == "t2"} == {"nils"}

    vera_prompt = script.prompt_text("worker", 0)
    nils_prompt = script.prompt_text("worker", 1)
    assert WRITER_PERSONA not in vera_prompt
    assert RESEARCH_PERSONA not in nils_prompt
    assert "platform character limit" not in vera_prompt
    assert "Every niche has a score" not in nils_prompt
    assert "Both parts are present" in vera_prompt
    assert "Both parts are present" in nils_prompt

    critic = script.prompt_text("critic", 0)
    assert "GRADE ONLY task t1" in critic
    assert "GRADE ONLY task t2" in critic
    assert "GRADE the full deliverable" in critic
    t1_block = critic.split('task_id="t1"', 1)[1].split("</criterion>", 1)[0]
    t2_block = critic.split('task_id="t2"', 1)[1].split("</criterion>", 1)[0]
    assert "UNIQUE-TASK-1-BODY" in t1_block
    assert "UNIQUE-TASK-2-BODY" not in t1_block
    assert "UNIQUE-TASK-2-BODY" in t2_block
    assert "UNIQUE-TASK-1-BODY" not in t2_block

    verifier = script.prompt_text("verifier", 0)
    assert "GRADE ONLY task t1" in verifier
    assert "GRADE ONLY task t2" in verifier


@pytest.mark.asyncio
async def test_lessons_are_credited_to_the_members_who_did_the_work(settings, tmp_path):
    script = Script(plan=TWO_TASK_PLAN)
    orch = _orch(settings, script, tmp_path, [WRITER, RESEARCHER, DIRECTOR])
    run = orch.create(GOAL, 1, [], agent_id="dara")
    await orch.execute(run)

    saved = _events(run, "lesson.saved")
    assert {s["agent_id"] for s in saved} == {"nils", "vera"}, saved
    assert all(s["delegated_by"] == "dara" for s in saved)
    assert {s["memory_scope"] for s in saved} == {"nils", "vera"}
    assert "dara" not in {s["agent_id"] for s in saved}, "the manager did not do the work"

    # ...and each member can recall what it learned
    for member in ("nils", "vera"):
        recalled = orch.memory.recall(GOAL, k=3, agent_id=member, memory_scope=member)
        assert recalled and recalled[0].agent_id == member


# --------------------------------------------------------- tools never widen
@pytest.mark.asyncio
async def test_a_member_cannot_write_files_the_manager_may_not(settings, tmp_path):
    """Delegation narrows. A manager cannot grant what it does not have."""
    plan = {
        "dod": [{"id": "p1", "criterion": "ok"}],
        "tasks": [{"id": "t1", "title": "w", "skill_id": "ad-copy-framework-writer",
                   "instruction": "x", "member": "nils", "needs_tools": []}],
    }
    script = Script(plan=plan, worker_text="=== FILE: n.md ===\nhi\n=== END FILE ===\ndone")
    boss = {**DIRECTOR, "name": "Dara", "tools": ["read_file", "list_files"]}
    writer = {**WRITER, "tools": ["read_file", "write_file", "list_files"]}
    orch = _orch(settings, script, tmp_path, [writer, RESEARCHER, boss])
    run = orch.create(GOAL, 1, [], agent_id="dara")
    await orch.execute(run)

    errors = _events(run, "tool.error")
    assert errors and errors[0]["error_tag"] == "TOOL_NOT_PERMITTED", errors
    assert not list((settings.workspace_dir / "runs").rglob("n.md"))
    delegated = _events(run, "team.delegated")[0]
    assert "write_file" not in delegated["tools"], delegated
    assert run.status == "failed", run.status
    assert run.error_tag == "TEAM_TOOL_REFUSED", run.error_tag
    refused = _events(run, "team.tool_refused")
    assert refused and refused[0]["member"] == "nils" and refused[0]["tool"] == "write_file"


@pytest.mark.asyncio
async def test_a_member_keeps_a_tool_the_manager_also_has(settings, tmp_path):
    plan = {
        "dod": [{"id": "p1", "criterion": "ok"}],
        "tasks": [{"id": "t1", "title": "w", "skill_id": "ad-copy-framework-writer",
                   "instruction": "x", "member": "nils", "writes_files": True, "needs_tools": []}],
    }
    script = Script(plan=plan, worker_text="=== FILE: n.md ===\nhi\n=== END FILE ===\ndone")
    orch = _orch(settings, script, tmp_path, [WRITER, RESEARCHER, DIRECTOR])
    run = orch.create(GOAL, 1, [], agent_id="dara")
    await orch.execute(run)
    assert _events(run, "tool.write"), [e["type"] for e in run.bus.events]


# --------------------------------------------------------------------- API
@pytest.fixture
def client(tmp_path):
    settings = Settings(
        host="127.0.0.1", port=0,
        data_dir=tmp_path / "var", workspace_dir=tmp_path / "workspace",
        agents_dir=tmp_path / "agents", provider=provider_config(),
    )
    orch = make_orchestrator(settings, Script())
    with TestClient(create_app(settings=settings, orchestrator=orch)) as c:
        c.roster_root = tmp_path / "agents"
        yield c


def test_the_api_creates_and_lists_a_manager(client):
    client.post("/api/agents", json=WRITER)
    client.post("/api/agents", json=RESEARCHER)
    made = client.post("/api/agents", json=DIRECTOR)
    assert made.status_code == 201, made.text
    assert made.json()["team"] == ["nils", "vera"]
    assert made.json()["manages"] is True

    listed = client.get("/api/agents").json()["agents"]
    dara = next(a for a in listed if a["id"] == "dara")
    assert dara["team"] == ["nils", "vera"]
    # names, not ids, for the card
    assert {m["name"] for m in dara["team_members"]} == {"Nils", "Vera"}


def test_the_api_refuses_a_team_that_names_nobody(client):
    resp = client.post("/api/agents", json={"name": "Ghost Boss", "team": ["nobody"]})
    assert resp.status_code == 400, resp.text
    assert "not in the roster" in resp.json()["message"]
    assert not (client.roster_root / "ghost-boss.md").exists()


def test_the_api_refuses_a_cycle(client):
    client.post("/api/agents", json=WRITER)
    client.post("/api/agents", json={"name": "Lead", "team": ["nils"]})
    resp = client.put("/api/agents/nils", json={"team": ["lead"]})
    assert resp.status_code == 400, resp.text
    assert "cycle" in resp.json()["message"]


# Round-8b: write-path team refusals must be named on BOTH verbs, and must
# not leave a file behind. duplicate() already does this; create/update must.
TEAM_WRITE_CASES = (
    "self",
    "cycle",
    "missing",
    "disabled",
)


def _seed_writer(client):
    assert client.post("/api/agents", json=WRITER).status_code == 201, "fixture writer must exist"


def _team_write_payload(client, case: str, verb: str) -> tuple[str, dict, str]:
    """(url-or-empty, json body, expected error_tag) for one of the four cases."""
    if case == "self":
        if verb == "POST":
            return "/api/agents", {"name": "Selfie", "team": ["selfie"]}, "TEAM_SELF"
        _seed_writer(client)
        return "/api/agents/nils", {"team": ["nils"]}, "TEAM_SELF"
    if case == "missing":
        if verb == "POST":
            return "/api/agents", {"name": "Ghost Boss", "team": ["nobody"]}, "TEAM_MISSING_MEMBER"
        _seed_writer(client)
        return "/api/agents/nils", {"team": ["nobody"]}, "TEAM_MISSING_MEMBER"
    if case == "disabled":
        client.roster_root.mkdir(parents=True, exist_ok=True)
        (client.roster_root / "broken.md").write_text(
            "---\nname: Broken\nskills: [no-such-pack]\n---\nb\n", encoding="utf-8"
        )
        if verb == "POST":
            return "/api/agents", {"name": "Boss", "team": ["broken"]}, "TEAM_DISABLED_MEMBER"
        _seed_writer(client)
        return "/api/agents/nils", {"team": ["broken"]}, "TEAM_DISABLED_MEMBER"
    # cycle: a file on disk already points at the slug we are about to write.
    client.roster_root.mkdir(parents=True, exist_ok=True)
    (client.roster_root / "lead.md").write_text(
        "---\nname: Lead\nteam: [head]\n---\nb\n", encoding="utf-8"
    )
    if verb == "POST":
        return "/api/agents", {"name": "Head", "team": ["lead"]}, "TEAM_CYCLE"
    assert client.post("/api/agents", json={"name": "Head", "team": []}).status_code == 201
    return "/api/agents/head", {"team": ["lead"]}, "TEAM_CYCLE"


@pytest.mark.parametrize("case", TEAM_WRITE_CASES)
@pytest.mark.parametrize("verb", ("POST", "PUT"))
def test_post_and_put_refuse_a_bad_team_with_a_named_tag_and_write_nothing(client, case, verb):
    url, payload, tag = _team_write_payload(client, case, verb)
    before = {p.name for p in client.roster_root.glob("*.md")} if client.roster_root.is_dir() else set()
    resp = client.post(url, json=payload) if verb == "POST" else client.put(url, json=payload)
    assert resp.status_code == 400, resp.text
    body = resp.json()
    assert body.get("error_tag") == tag, body
    after = {p.name for p in client.roster_root.glob("*.md")} if client.roster_root.is_dir() else set()
    if verb == "POST":
        assert after == before, f"POST {case} wrote {after - before}"
    else:
        # PUT must not persist the rejected team onto the existing file.
        if case == "self":
            text = (client.roster_root / "nils.md").read_text(encoding="utf-8")
            assert "team:" not in text or "nils" not in text.split("team:", 1)[-1].split("\n", 1)[0]
        if case == "missing":
            text = (client.roster_root / "nils.md").read_text(encoding="utf-8")
            assert "nobody" not in text
        if case == "disabled":
            text = (client.roster_root / "nils.md").read_text(encoding="utf-8")
            assert "broken" not in text
        if case == "cycle":
            text = (client.roster_root / "head.md").read_text(encoding="utf-8")
            assert "lead" not in text.split("---", 2)[1]


def test_list_runs_can_be_filtered_by_agent_id(client):
    assert client.post("/api/agents", json=WRITER).status_code == 201
    assigned = client.post("/api/runs", json={"goal": GOAL, "agent_id": "nils"})
    assert assigned.status_code == 201, assigned.text
    other = client.post("/api/runs", json={"goal": "a run with no agent"})
    assert other.status_code == 201, other.text
    everyone = client.get("/api/runs").json()["runs"]
    assert len(everyone) >= 2
    scoped = client.get("/api/runs?agent_id=nils").json()
    rows = scoped["runs"]
    assert scoped["items"] == rows
    assert len(rows) == 1
    assert rows[0]["agent_id"] == "nils"
    assert rows[0]["run_id"] == assigned.json()["run_id"]
    assert all(r.get("agent_id") == "nils" for r in rows)
    assert len(rows) < len(everyone)


def test_a_run_is_refused_when_a_member_file_vanishes_before_start(client):
    """A team that goes bad between roster load and POST /api/runs must 400,
    never hang or 500. The manager still exists; its member file does not.
    """
    assert client.post("/api/agents", json=WRITER).status_code == 201
    assert client.post("/api/agents", json=RESEARCHER).status_code == 201
    made = client.post("/api/agents", json=DIRECTOR)
    assert made.status_code == 201, made.text
    (client.roster_root / "nils.md").unlink()
    before = client.get("/api/runs").json()["runs"]
    resp = client.post("/api/runs", json={"goal": GOAL, "agent_id": "dara"})
    assert resp.status_code == 400, resp.text
    assert resp.json().get("error_tag") == "TEAM_MISSING_MEMBER", resp.json()
    after = client.get("/api/runs").json()["runs"]
    assert len(after) == len(before), "a refused run must not be created"


def test_a_hand_edited_cycle_is_refused_at_run_start(client):
    """F2: a cyclic pair written after load is TEAM_CYCLE at POST /api/runs.

    The write path already refuses this shape. A drop-in directory can still
    grow a cycle between load and the next run; that must 400, never start.
    """
    assert client.post("/api/agents", json={"name": "Alpha"}).status_code == 201
    made = client.post("/api/agents", json={"name": "Bravo", "team": ["alpha"]})
    assert made.status_code == 201, made.text
    alpha = client.roster_root / "alpha.md"
    text = alpha.read_text(encoding="utf-8")
    assert "team: []" in text, text
    alpha.write_text(text.replace("team: []", "team: [bravo]", 1), encoding="utf-8")

    before = client.get("/api/runs").json()["runs"]
    resp = client.post("/api/runs", json={"goal": GOAL, "agent_id": "alpha"})
    assert resp.status_code == 400, resp.text
    body = resp.json()
    assert body.get("error_tag") == "TEAM_CYCLE", body
    assert "cycle" in (body.get("message") or "").lower()
    after = client.get("/api/runs").json()["runs"]
    assert len(after) == len(before), "a refused run must not be created"


# ---------------------------------------------------------------------- UI
APP_JS = (REPO_ROOT / "omniagentos_starter" / "static" / "app.js").read_text(encoding="utf-8")
INDEX = (REPO_ROOT / "omniagentos_starter" / "static" / "index.html").read_text(encoding="utf-8")


def test_the_roster_card_names_who_a_manager_manages():
    render = APP_JS.split("function renderAgents()")[1].split("function fillAgentPicker()")[0]
    # the oracle binds to this hook on the CARD; the form's checkboxes are
    # agent-team-choices so the two cannot be confused
    assert 'data-testid="agent-team"' in render
    assert "team_members" in render
    assert "manages" in render
    assert "m.name" in render, "names, not ids"


def test_the_run_view_shows_the_delegation():
    assert 'case "team.delegated"' in APP_JS
    block = APP_JS.split('case "team.delegated"')[1].split("case ")[0]
    assert "delegated by" in block
    assert "worker-agent" in block, "the chip lives on the lane doing the work"
    assert '"team.delegated"' in APP_JS.split("var EVENT_TYPES = [")[1].split("];")[0]
    assert 'data-testid="task-member"' in APP_JS


def test_each_task_row_joins_team_delegated_into_per_task_state():
    """The worker chip already works; the per-task marker did not.

    team.delegated fires before planner.plan, so a side-map keyed after
    worker.started is how the marker never rendered. The join has to land
    on the task object itself, and both the Workers timeline and the
    quality-gate / plan rows have to read it.
    """
    plan = APP_JS.split('case "planner.plan"')[1].split("case ")[0]
    assert "state.tasks" in plan, "planner.plan must seed per-task state so delegations can join"
    delegated = APP_JS.split('case "team.delegated"')[1].split("case ")[0]
    assert "state.tasks" in delegated, (
        "team.delegated must join onto state.tasks[task_id], not only a side map "
        "that renderTasks looks up later"
    )
    assert ".member" in delegated
    render = APP_JS.split("function renderTasks()")[1].split("function renderCards()")[0]
    assert "taskMemberHtml(t)" in render
    helper = APP_JS.split("function taskMemberHtml(")[1].split("function taskForSource")[0]
    assert 'data-testid="task-member"' in helper
    # Plan timeline and quality-gate must paint the same marker, not only the chip.
    dod = APP_JS.split("function renderDod()")[1].split("function renderTasks()")[0]
    assert "taskMemberHtml" in plan
    assert "taskMemberHtml" in dod
    assert 'params.get("run_id")' in APP_JS, "a finished run is re-opened at /?run_id="


def test_each_task_row_emits_data_task_id_around_its_member_marker():
    """D18 locates the delegated member via [data-task-id="<id>"] [data-testid="task-member"].

    A mismatched join would still pass a page-wide exists() check: member
    markers in team.delegated order sitting next to task rows in plan order.
    The attribute has to be on the .task row itself, and the member marker
    for that task has to live inside that same row.
    """
    render = APP_JS.split("function renderTasks()")[1].split("function renderCards()")[0]
    assert 'class="task" data-task-id="' in render, (
        "renderTasks() must emit data-task-id on the .task row (D18 JOIN selector)"
    )
    assert "esc(t.id || id)" in render or "esc(t.id)" in render
    assert "taskMemberHtml(t)" in render, (
        "the [data-testid=task-member] marker must be nested inside the same row"
    )

    plan = APP_JS.split('case "planner.plan"')[1].split("case ")[0]
    assert 'class="task" data-task-id="' in plan, (
        "planner.plan's inline .task rows are the other build path and must "
        "emit data-task-id too"
    )
    assert "esc(t.id || t.task_id)" in plan or "esc(t.id)" in plan
    assert "taskMemberHtml(task)" in plan


def test_the_form_can_build_a_team_without_offering_self():
    assert 'data-testid="agent-team-choices"' in INDEX
    form = APP_JS.split("function openAgentForm(")[1].split("function closeAgentForm")[0]
    assert "agent-team" in form
    assert "a.id !== editing" in form, "an agent must not be offered as its own member"
    assert 'checkedValues("agent-team")' in APP_JS


def test_every_pre_existing_testid_survived():
    """Round 8 must not move the hooks the browser oracle already binds to."""
    for testid in (
        "agents-list", "agent-card", "agent-create", "agent-name", "agent-title",
        "agent-persona", "agent-save", "agent-picker", "worker-agent", "agent-resolved",
        "run-button", "goal-input", "deliverable", "run-busy", "watch-demo",
        "extra-dod-input", "error-banner", "receipt-agent",
    ):
        assert f'data-testid="{testid}"' in INDEX + APP_JS, testid
