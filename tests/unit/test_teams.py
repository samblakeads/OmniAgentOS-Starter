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
from omniagentos_starter.skills import load_skills

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
         "instruction": "score it", "member": "vera", "depends_on": []},
        {"id": "t2", "title": "Write the headlines", "skill_id": "ad-copy-framework-writer",
         "instruction": "write them", "member": "nils", "depends_on": ["t1"]},
    ],
}


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
                   "instruction": "x", "member": "nils", "writes_files": True}],
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


@pytest.mark.asyncio
async def test_a_member_keeps_a_tool_the_manager_also_has(settings, tmp_path):
    plan = {
        "dod": [{"id": "p1", "criterion": "ok"}],
        "tasks": [{"id": "t1", "title": "w", "skill_id": "ad-copy-framework-writer",
                   "instruction": "x", "member": "nils", "writes_files": True}],
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
