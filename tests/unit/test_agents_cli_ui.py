"""The two surfaces an operator actually touches: the CLI and the dashboard.

The dashboard assertions are static — the markup and the script are the
contract the browser oracle drives, so a missing data-testid or a handler that
never got wired should fail here, in milliseconds, rather than in a browser
three minutes into a live run.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from omniagentos_starter import cli
from omniagentos_starter.agents import AgentStore
from omniagentos_starter.skills import load_skills

REPO_ROOT = Path(__file__).resolve().parents[2]
STATIC = REPO_ROOT / "omniagentos_starter" / "static"
INDEX = (STATIC / "index.html").read_text(encoding="utf-8")
APP_JS = (STATIC / "app.js").read_text(encoding="utf-8")
STYLE = (STATIC / "style.css").read_text(encoding="utf-8")


# ---------------------------------------------------------------- the plan's
# data-testids, verbatim from the Round 6 contract.
@pytest.mark.parametrize(
    "testid",
    ["agents-list", "agent-card", "agent-create", "agent-name", "agent-title", "agent-persona", "agent-save"],
)
def test_every_agent_testid_the_plan_names_exists(testid):
    haystack = INDEX + APP_JS
    assert f'data-testid="{testid}"' in haystack, f"{testid} is not in the markup or the renderer"


def test_the_agent_picker_exists_and_defaults_to_the_router():
    assert 'data-testid="agent-picker"' in INDEX
    picker = INDEX.split('<select id="agent-picker"')[1].split("</select>")[0]
    assert 'value=""' in picker
    assert "Let the router decide" in picker


def test_the_skill_checkboxes_carry_a_per_slug_testid():
    """agent-skill-<slug> is built by the renderer, one per installed pack."""
    assert '"agent-skill-"' in APP_JS
    assert 'data-testid="' in APP_JS
    # the prefix is applied to the skill host, not the tools host
    call = re.search(r'renderChoices\("agent-skills"[^;]+;', APP_JS).group(0)
    assert '"agent-skill-"' in call


def test_the_worker_lane_has_somewhere_to_show_the_agent():
    lane = INDEX.split('id="lane-worker"')[1].split("</article>")[0]
    assert 'id="worker-agent"' in lane, "the agent chip must live on the Workers lane"
    assert 'data-testid="worker-agent"' in lane


def test_the_dashboard_subscribes_to_agent_assigned():
    types = APP_JS.split("var EVENT_TYPES = [")[1].split("];")[0]
    assert "agent.assigned" in types
    assert 'case "agent.assigned"' in APP_JS


def test_the_run_request_carries_the_picked_agent():
    start = APP_JS.split("function startRun()")[1].split("function startDemo()")[0]
    assert "agent_id" in start
    assert "agent-picker" in start


def test_the_form_is_wired_to_the_api():
    for fragment in ('apiFetch("/api/agents"', "/api/agents/", '"PUT"', '"DELETE"', "duplicate"):
        assert fragment in APP_JS, fragment


def test_a_disabled_agent_is_rendered_rather_than_hidden():
    render = APP_JS.split("function renderAgents()")[1].split("function fillAgentPicker()")[0]
    assert "enabled === false" in render
    assert "disabled" in render


def test_the_agent_styles_exist():
    for selector in (".agent-card", ".agent-chip", ".agents-list", ".agent-form"):
        assert selector in STYLE, selector


# ----------------------------------------------------------------------- CLI
@pytest.fixture
def roster(tmp_path, monkeypatch):
    root = tmp_path / "agents"
    store = AgentStore(root)
    store.create(
        {
            "name": "Riley",
            "title": "Meal-Prep Support",
            "persona": "Calm and exact.",
            "skills": ["refund-request-handler"],
            "tools": ["read_file"],
            "body": "Lead with the clause.",
        },
        library=load_skills(REPO_ROOT / "skills"),
    )
    monkeypatch.setattr(cli, "agents_dir", lambda: root)
    monkeypatch.setattr(cli, "skills_dir", lambda: REPO_ROOT / "skills")
    return root


def test_agents_list_names_every_agent(roster, capsys):
    assert cli.main(["agents", "list", "--data-dir", str(roster.parent / "var")]) == 0
    out = capsys.readouterr().out
    assert "riley" in out and "Riley" in out
    assert "Meal-Prep Support" in out
    assert "refund-request-handler" in out


def test_agents_list_json_is_machine_readable(roster, capsys):
    assert cli.main(["agents", "list", "--json", "--data-dir", str(roster.parent / "var")]) == 0
    listed = json.loads(capsys.readouterr().out)
    assert {a["id"] for a in listed} >= {"riley", "general-worker"}


def test_agents_show_prints_the_persona_and_the_tools(roster, capsys):
    assert cli.main(["agents", "show", "riley", "--data-dir", str(roster.parent / "var")]) == 0
    out = capsys.readouterr().out
    assert "Calm and exact." in out
    assert "Lead with the clause." in out
    assert "read_file" in out
    assert "write_file" not in out.split("tools:")[1].split("\n")[0]


def test_agents_show_of_something_that_is_not_there_exits_nonzero(roster, capsys):
    assert cli.main(["agents", "show", "nobody", "--data-dir", str(roster.parent / "var")]) == 2
    assert "no agent" in capsys.readouterr().err


def test_run_accepts_an_agent_flag():
    parser = cli._parser()
    args = parser.parse_args(["run", "--agent", "riley", "do the thing"])
    assert args.agent == "riley"
    assert args.goal == "do the thing"


def test_run_with_an_unknown_agent_refuses_before_it_spends_anything(roster, capsys, monkeypatch):
    """Exit 2 and no provider call — the run never starts."""
    monkeypatch.setenv("XAI_API_KEY", "xai-unit-test-key-abcdef0123456789")
    code = cli.main(["run", "--agent", "nobody", "hello", "--data-dir", str(roster.parent / "var")])
    assert code == 2
    assert "nobody" in capsys.readouterr().err
