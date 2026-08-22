"""Round-7 audit findings, plus the test-design gap the auditor named.

Q3-REC2 is the interesting one: the whole agent layer could be green while an
engine emitted `agent.assigned` for the right id and injected a DIFFERENT
agent's persona. Every existing test would still pass, because each one checks a
single agent in isolation. The test below plants two agents whose personas are
distinguishable and asserts the worker prompt carries the assigned one's text
byte-for-byte — and nobody else's.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import Script, make_orchestrator, provider_config
from fastapi.testclient import TestClient

from omniagentos_starter.agents import BUILTIN_AGENT_SLUG, AgentError, AgentStore, load_agents
from omniagentos_starter.api import create_app
from omniagentos_starter.config import Settings
from omniagentos_starter.skills import load_skills

REPO_ROOT = Path(__file__).resolve().parents[2]
SKILLS_ROOT = REPO_ROOT / "skills"
APP_JS = (REPO_ROOT / "omniagentos_starter" / "static" / "app.js").read_text(encoding="utf-8")

GOAL = "A customer wants a refund 38 days after purchase. Draft the reply."

AVA_PERSONA = "Ava opens with the clause and never apologises twice."
MAX_PERSONA = "Max writes headlines and refuses to discuss refunds at all."


@pytest.fixture
def client(tmp_path):
    settings = Settings(
        host="127.0.0.1",
        port=0,
        data_dir=tmp_path / "var",
        workspace_dir=tmp_path / "workspace",
        agents_dir=tmp_path / "agents",
        provider=provider_config(),
    )
    orch = make_orchestrator(settings, Script())
    with TestClient(create_app(settings=settings, orchestrator=orch)) as c:
        c.roster_root = tmp_path / "agents"
        yield c


def _orch(settings, script, tmp_path, agents=()):
    orch = make_orchestrator(settings, script)
    orch.load_library(SKILLS_ROOT)
    root = tmp_path / "agents"
    store = AgentStore(root)
    for payload in agents:
        store.create(payload, library=orch.library)
    orch.load_roster(root)
    script.orch = orch
    return orch


def _events(run, etype):
    return [e["payload"] for e in run.bus.events if e["type"] == etype]


# ======================================================================= Q3-REC2
@pytest.mark.asyncio
async def test_the_assigned_agents_own_persona_is_what_reaches_the_worker(settings, tmp_path):
    """Would FAIL on an engine that announces one agent and injects another.

    Two agents, two personas that cannot be mistaken for each other. Assign one.
    The worker prompt must carry that agent's persona byte-for-byte, and must not
    carry a single line of the other's — announcing the right id while injecting
    the wrong text is the favourable-wrong shape this test exists to catch.
    """
    script = Script()
    orch = _orch(
        settings,
        script,
        tmp_path,
        [
            {"name": "Ava", "title": "", "persona": AVA_PERSONA, "skills": ["refund-request-handler"]},
            {"name": "Max", "title": "", "persona": MAX_PERSONA, "skills": ["ad-copy-framework-writer"]},
        ],
    )
    run = orch.create(GOAL, 1, [], agent_id="ava")
    await orch.execute(run)

    assigned = _events(run, "agent.assigned")
    assert assigned and assigned[0]["agent_id"] == "ava"

    worker_prompt = script.prompt_text("worker")
    assert AVA_PERSONA in worker_prompt, "the assigned agent's persona did not reach the worker"
    assert MAX_PERSONA not in worker_prompt, "another agent's persona reached the worker"

    # ...and the same holds for the OTHER direction, so a test that hard-coded
    # one agent could not pass by accident.
    script2 = Script()
    orch2 = _orch(
        settings,
        script2,
        tmp_path / "second",
        [
            {"name": "Ava", "title": "", "persona": AVA_PERSONA, "skills": ["refund-request-handler"]},
            {"name": "Max", "title": "", "persona": MAX_PERSONA, "skills": ["ad-copy-framework-writer"]},
        ],
    )
    run2 = orch2.create(GOAL, 1, [], agent_id="max")
    await orch2.execute(run2)
    prompt2 = script2.prompt_text("worker")
    assert MAX_PERSONA in prompt2
    assert AVA_PERSONA not in prompt2


@pytest.mark.asyncio
async def test_the_announced_agent_and_the_injected_agent_are_the_same_object(settings, tmp_path):
    """The id on the wire and the persona in the prompt come from one agent."""
    script = Script()
    orch = _orch(
        settings, script, tmp_path,
        [{"name": "Ava", "title": "", "persona": AVA_PERSONA, "skills": ["refund-request-handler"]}],
    )
    run = orch.create(GOAL, 1, [], agent_id="ava")
    await orch.execute(run)

    announced = _events(run, "agent.assigned")[0]
    agent = orch.roster.by_id(announced["agent_id"])
    worker_prompt = script.prompt_text("worker")
    # the sha is of the agent's own file, so this binds prompt to identity
    assert f"agent-sha256:{agent.sha256}" in worker_prompt
    assert agent.persona in worker_prompt
    assert announced["name"] == agent.name


# ================================================================== Q1-R2 (create)
def test_create_refuses_the_builtin_slug_with_409(tmp_path):
    """Proven at the store, not only through duplicate()."""
    store = AgentStore(tmp_path / "agents")
    with pytest.raises(AgentError) as exc:
        store.create({"name": "General Worker"})
    assert exc.value.status == 409
    assert exc.value.error_tag == "AGENT_EXISTS"
    assert not (tmp_path / "agents" / f"{BUILTIN_AGENT_SLUG}.md").exists()


def test_create_refuses_an_explicit_builtin_slug_with_409(tmp_path):
    store = AgentStore(tmp_path / "agents")
    with pytest.raises(AgentError) as exc:
        store.create({"name": "Something Else", "slug": BUILTIN_AGENT_SLUG})
    assert exc.value.status == 409
    assert not (tmp_path / "agents" / f"{BUILTIN_AGENT_SLUG}.md").exists()


def test_the_create_endpoint_refuses_the_builtin_slug_with_409(client):
    resp = client.post("/api/agents", json={"name": "General Worker", "persona": "mine"})
    assert resp.status_code == 409, resp.text
    assert resp.json()["error_tag"] == "AGENT_EXISTS"


# =========================================================== D17 (e): PUT + slug
def test_a_put_carrying_a_slug_renames_rather_than_dropping_it(client):
    """Silently ignoring a field the client sent is worse than refusing it."""
    client.post("/api/agents", json={"name": "Ava", "title": "", "persona": AVA_PERSONA})
    renamed = client.put("/api/agents/ava", json={"slug": "ava-support"})
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["id"] == "ava-support"

    assert (client.roster_root / "ava-support.md").is_file()
    assert not (client.roster_root / "ava.md").exists(), "the old file must not linger"
    assert client.get("/api/agents/ava").status_code == 404
    got = client.get("/api/agents/ava-support")
    assert got.status_code == 200
    assert got.json()["persona"] == AVA_PERSONA, "a rename must not lose the rest of the agent"


def test_a_renamed_agent_keeps_the_memory_it_earned(client):
    """`memory_scope` travels with the agent, so a rename does not orphan it."""
    client.post("/api/agents", json={"name": "Ava", "title": "", "persona": AVA_PERSONA})
    before = client.get("/api/agents/ava").json()["memory_scope"]
    client.put("/api/agents/ava", json={"slug": "ava-support"})
    after = client.get("/api/agents/ava-support").json()["memory_scope"]
    assert after == before == "ava", "the scope must not silently follow the new slug"


def test_a_put_renaming_onto_the_builtin_slug_is_a_409(client):
    client.post("/api/agents", json={"name": "Ava", "title": "", "persona": AVA_PERSONA})
    resp = client.put(
        "/api/agents/ava", json={"slug": BUILTIN_AGENT_SLUG, "name": "General Worker"}
    )
    assert resp.status_code == 409, resp.text
    assert resp.json()["error_tag"] == "AGENT_EXISTS"
    # nothing moved, nothing was written
    assert (client.roster_root / "ava.md").is_file()
    assert not (client.roster_root / f"{BUILTIN_AGENT_SLUG}.md").exists()
    assert client.get(f"/api/agents/{BUILTIN_AGENT_SLUG}").json()["builtin"] is True


def test_a_put_renaming_onto_an_existing_agent_is_a_409(client):
    client.post("/api/agents", json={"name": "Ava", "title": ""})
    client.post("/api/agents", json={"name": "Max", "title": ""})
    resp = client.put("/api/agents/ava", json={"slug": "max"})
    assert resp.status_code == 409, resp.text
    assert (client.roster_root / "ava.md").is_file()
    assert client.get("/api/agents/max").json()["name"] == "Max", "the target was overwritten"


@pytest.mark.parametrize("hostile", ["../../etc/passwd", "/etc/passwd", "a\x00b", "....//x"])
def test_a_put_with_a_path_shaped_slug_is_a_400(client, hostile):
    client.post("/api/agents", json={"name": "Ava", "title": ""})
    resp = client.put("/api/agents/ava", json={"slug": hostile})
    assert resp.status_code == 400, resp.text
    assert resp.json()["error_tag"] == "BAD_REQUEST"
    assert (client.roster_root / "ava.md").is_file()
    assert not (client.roster_root / "etc-passwd.md").exists()


def test_a_put_with_the_same_slug_is_an_ordinary_edit(client):
    client.post("/api/agents", json={"name": "Ava", "title": "", "persona": AVA_PERSONA})
    resp = client.put("/api/agents/ava", json={"slug": "ava", "title": "Support"})
    assert resp.status_code == 200
    assert resp.json()["title"] == "Support"
    assert resp.json()["persona"] == AVA_PERSONA
    assert (client.roster_root / "ava.md").is_file()


# =============================================== F-Q1-R4-DUP-SLUG through the API
def test_a_duplicate_slug_on_disk_is_listed_disabled_not_omitted(client):
    client.roster_root.mkdir(parents=True, exist_ok=True)
    (client.roster_root / "sales-closer.md").write_text(
        "---\nname: Cole\n---\nbody\n", encoding="utf-8"
    )
    (client.roster_root / "sales-closer-dup.md").write_text(
        "---\nname: Impostor\nslug: sales-closer\n---\nbody\n", encoding="utf-8"
    )
    body = client.get("/api/agents").json()
    entries = [a for a in body["agents"] if a["id"] == "sales-closer"]
    assert len(entries) == 2, f"both files must appear: {[a['id'] for a in body['agents']]}"
    assert body["count"] == len(body["agents"])

    disabled = [a for a in entries if not a["enabled"]]
    assert len(disabled) == 1
    assert "duplicate slug of" in disabled[0]["errors"][0]
    # the two are distinguishable, or the operator cannot tell which to delete
    assert {a["file"] for a in entries} == {"sales-closer.md", "sales-closer-dup.md"}
    assert disabled[0]["file"] == "sales-closer-dup.md", "the canonical filename keeps the id"


def test_a_disabled_duplicate_cannot_be_handed_a_run(client):
    client.roster_root.mkdir(parents=True, exist_ok=True)
    (client.roster_root / "sales-closer.md").write_text(
        "---\nname: Cole\n---\nbody\n", encoding="utf-8"
    )
    (client.roster_root / "sales-closer-dup.md").write_text(
        "---\nname: Impostor\nslug: sales-closer\n---\nbody\n", encoding="utf-8"
    )
    created = client.post("/api/runs", json={"goal": "hello", "agent_id": "sales-closer"})
    assert created.status_code == 201, created.text
    assert created.json()["agent"]["name"] == "Cole", "the impostor took the run"


def test_the_card_names_the_file_of_a_disabled_agent():
    render = APP_JS.split("function renderAgents()")[1].split("function fillAgentPicker()")[0]
    assert "a.file" in render, "two cards can share a slug; the filename tells them apart"


# ================================================== F-WATCH-FETCH-RACE (MINOR)
def test_a_stale_roster_response_cannot_repaint_the_list():
    """Overlapping GET /api/agents: only the newest reply may render."""
    load = APP_JS.split("function loadAgents()")[1].split("function renderAgents()")[0]
    assert "agentsGeneration" in load, "no request-generation counter"
    assert "state.agentsGeneration += 1" in load
    assert load.count("current()") >= 2, "both the success and the failure path must check"
    assert "agentsGeneration: 0" in APP_JS, "the counter must be initialised in state"


# ================================================================ SEC-OBS-1
def test_agent_files_are_written_atomically(tmp_path):
    """A half-written agent loads cleanly and is silently wrong."""
    source = (REPO_ROOT / "omniagentos_starter" / "agents.py").read_text(encoding="utf-8")
    assert "os.replace" in source, "the swap must be atomic"
    assert "mkstemp" in source
    assert "write_text(agent.raw" not in source, "a direct truncating write is left somewhere"
    assert "write_text(clone.raw" not in source

    store = AgentStore(tmp_path / "agents")
    agent = store.create({"name": "Ava", "title": "", "persona": AVA_PERSONA})
    # no temp files left behind
    assert [p.name for p in (tmp_path / "agents").iterdir()] == ["ava.md"]
    assert load_agents(tmp_path / "agents").by_id("ava").persona == agent.persona


def test_a_failed_write_leaves_no_partial_file(tmp_path, monkeypatch):
    import omniagentos_starter.agents as agents_module

    store = AgentStore(tmp_path / "agents")
    store.create({"name": "Ava", "title": "", "persona": AVA_PERSONA})
    original = agents_module.os.replace

    def boom(src, dst):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(agents_module.os, "replace", boom)
    with pytest.raises(OSError):
        store.update("ava", {"title": "Support"})
    monkeypatch.setattr(agents_module.os, "replace", original)

    # the old agent is intact and no temp file survives
    reloaded = load_agents(tmp_path / "agents", library=load_skills(SKILLS_ROOT))
    assert reloaded.by_id("ava").persona == AVA_PERSONA
    assert reloaded.by_id("ava").title == ""
    assert [p.name for p in (tmp_path / "agents").iterdir()] == ["ava.md"]


def test_an_agent_dropped_into_the_directory_can_be_handed_a_run_immediately(client):
    """The roster is a drop-in directory, so a run must see a drop-in.

    Found while testing the duplicate case: GET /api/agents reloaded and run
    creation did not, so a freshly copied file was visible in the list and
    "unknown" to the run that wanted it.
    """
    client.roster_root.mkdir(parents=True, exist_ok=True)
    (client.roster_root / "dropped-in.md").write_text(
        "---\nname: Dropped In\n---\nbody\n", encoding="utf-8"
    )
    created = client.post("/api/runs", json={"goal": "hello", "agent_id": "dropped-in"})
    assert created.status_code == 201, created.text
    assert created.json()["agent"]["name"] == "Dropped In"


def test_a_dropped_in_agent_also_resolves_through_an_at_mention(client):
    client.roster_root.mkdir(parents=True, exist_ok=True)
    (client.roster_root / "dropped-in.md").write_text(
        "---\nname: Dropped In\n---\nbody\n", encoding="utf-8"
    )
    created = client.post("/api/runs", json={"goal": "@dropped-in hello"})
    assert created.status_code == 201, created.text
    assert created.json()["agent_id"] == "dropped-in"
    assert created.json()["goal"] == "hello"
