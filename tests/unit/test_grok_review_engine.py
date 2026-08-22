"""Regression tests for the engine findings of the Grok cross-lineage review.

Each test here was red before the fix it guards. They are grouped by the thing
that could go wrong on stage rather than by the function that was changed:
a verdict that fails open, a repair prompt that contradicts itself, a file the
critic never got to see, and a worker that keeps talking after the run failed.
"""

from __future__ import annotations

import asyncio

import pytest
from conftest import Script, make_orchestrator, provider_config, run_goal

from omniagentos_starter.config import Settings
from omniagentos_starter.engine import (
    FILE_BLOCK_RE,
    Engine,
    EventBus,
    RunState,
    Task,
    _esc,
    json_flag,
    json_true,
)
from omniagentos_starter.memory import Memory
from omniagentos_starter.redact import ProviderError
from omniagentos_starter.skills import load_skills
from omniagentos_starter.tools import workspace_for_run

GOAL = "Write exactly 3 ad headlines"


def _engine(tmp_path, llm=None) -> Engine:
    settings = Settings(
        host="127.0.0.1",
        port=0,
        data_dir=tmp_path / "var",
        workspace_dir=tmp_path / "ws",
        provider=provider_config(),
    )
    run = RunState(id="unit", goal=GOAL)
    run.bus = EventBus(run.id)
    engine = Engine(
        run,
        settings.provider,
        Memory(settings.data_dir),
        load_skills(None),
        settings.data_dir,
        settings.workspace_dir,
    )
    if llm is not None:
        engine.llm = llm
    return engine


# --------------------------------------------------------------- B1-F1 (BLOCKER)
def test_a_json_string_false_is_not_a_pass():
    assert json_true(True) is True
    for hostile in ("false", "true", "no", 1, "1", None, {}, [], 0):
        assert json_true(hostile) is False, hostile


class _StringFalseLLM:
    last_request_id = "req"
    last_response_id = "resp"

    async def complete_json(self, messages, schema, role="x"):
        return {
            "verdicts": [
                {"criterion_id": "d1", "task_id": "t1", "pass": "false", "reason": "41 chars", "fix": "cut"},
                {"criterion_id": "d2", "task_id": "t1", "pass": True, "reason": "ok", "fix": ""},
            ]
        }


@pytest.mark.asyncio
async def test_a_verdict_whose_pass_is_the_string_false_fails_closed(tmp_path):
    engine = _engine(tmp_path, _StringFalseLLM())
    verdicts = await engine._verdicts("sys", "user", ["d1", "d2"], role="critic", round_no=1)
    assert verdicts[0]["pass"] is False, "the string 'false' must never be recorded as a pass"
    assert verdicts[1]["pass"] is True


class _FreezeLLM:
    """First body hallucinates a pass and omits an id; the retry corrects it."""

    last_request_id = "req"
    last_response_id = "resp"

    def __init__(self):
        self.calls = 0

    async def complete_json(self, messages, schema, role="x"):
        self.calls += 1
        if self.calls == 1:
            return {"verdicts": [{"criterion_id": "d1", "task_id": "t1", "pass": True, "reason": "", "fix": ""}]}
        return {
            "verdicts": [
                {"criterion_id": "d1", "task_id": "t1", "pass": False, "reason": "actually fails", "fix": "cut"},
                {"criterion_id": "d2", "task_id": "t1", "pass": True, "reason": "ok", "fix": ""},
            ]
        }


@pytest.mark.asyncio
async def test_a_retry_may_correct_a_verdict_the_first_attempt_got_wrong(tmp_path):
    engine = _engine(tmp_path, _FreezeLLM())
    verdicts = await engine._verdicts("sys", "user", ["d1", "d2"], role="critic", round_no=1)
    assert verdicts[0]["pass"] is False
    assert verdicts[1]["pass"] is True


@pytest.mark.asyncio
async def test_a_run_whose_critic_says_string_false_is_not_verified(settings):
    def critic(call, ids):
        return [{"criterion_id": i, "task_id": "t1", "pass": "false", "reason": "no", "fix": "x"} for i in ids]

    run, _ = await run_goal(settings, Script(critic=critic), GOAL, max_rounds=1)
    assert run.verified is False
    assert run.status == "failed"


# --------------------------------------------------------------- B1-F2 (BLOCKER)
@pytest.mark.asyncio
async def test_a_workspace_failure_never_puts_an_absolute_path_on_the_wire(tmp_path):
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("nope", encoding="utf-8")
    settings = Settings(
        host="127.0.0.1",
        port=0,
        data_dir=tmp_path / "var",
        workspace_dir=blocker,
        provider=provider_config(),
    )
    script = Script()
    orch = make_orchestrator(settings, script)
    run = orch.create(GOAL, 1, [])
    await orch.execute(run)
    blob = (run.error_message or "") + "".join(
        str(e["payload"]) for e in run.bus.events if e["type"] == "run.failed"
    )
    assert run.status == "failed" and run.error_tag == "INTERNAL_ERROR"
    assert str(blocker.resolve()) not in blob, blob[:300]


# -------------------------------------------------------------- B1-F3 (REQUIRED)
@pytest.mark.asyncio
async def test_a_worker_failure_cancels_its_siblings(tmp_path):
    engine = _engine(tmp_path)
    engine.run.workspace = workspace_for_run(tmp_path / "ws", "unit", tmp_path / "var")
    engine.tasks = {
        "t1": Task(id="t1", title="a", skill_id="general-assistant", instruction="x"),
        "t2": Task(id="t2", title="b", skill_id="general-assistant", instruction="y"),
    }
    engine.order = ["t1", "t2"]
    finished = {"t2": False}

    async def worker(task, round_no, sem):
        async with sem:
            if task.id == "t1":
                await asyncio.sleep(0.02)
                raise ProviderError("PROVIDER_UNAVAILABLE", 503, "down")
            await asyncio.sleep(0.4)
            finished["t2"] = True
            engine.bus.emit("worker.finished", {"task_id": "t2"})

    engine._run_worker = worker
    with pytest.raises(ProviderError):
        await engine._run_workers(["t1", "t2"], 1)
    await asyncio.sleep(0.5)
    assert finished["t2"] is False, "a sibling worker kept running after the run failed"
    assert "worker.finished" not in [e["type"] for e in engine.bus.events]


# -------------------------------------------------------------- B1-F4 (REQUIRED)
@pytest.mark.asyncio
async def test_a_criterion_the_verifier_rejected_is_not_listed_as_already_passing(settings):
    def verifier(call, ids):
        if call == 1:
            return [Script.verdict(ids[0], False, reason="verifier says no", fix="fix it")] + [
                Script.verdict(i, True) for i in ids[1:]
            ]
        return [Script.verdict(i, True) for i in ids]

    run, script = await run_goal(settings, Script(verifier=verifier), GOAL, max_rounds=3)
    verdicts = next(e["payload"] for e in run.bus.events if e["type"] == "verifier.verdict")
    rejected = [v["criterion_id"] for v in verdicts["verdicts"] if not v["pass"]]
    assert rejected, "setup: the verifier must reject something"
    repair_prompt = script.prompt_text("worker", 1)
    holding = repair_prompt.split("<previous_attempt>")[0]
    for cid in rejected:
        assert f"- {cid}:" not in holding, f"{cid} was rejected and then held as passing:\n{holding[-600:]}"


# -------------------------------------------------------------- B1-F5 (REQUIRED)
def test_adjacent_file_markers_each_become_a_file_without_an_end_marker():
    text = "=== FILE: email-1.md ===\nA\n=== FILE: email-2.md ===\nB\n=== FILE: email-3.md ===\nC\n"
    matches = list(FILE_BLOCK_RE.finditer(text))
    assert [m.group("path").strip() for m in matches] == ["email-1.md", "email-2.md", "email-3.md"]
    assert [m.group("body").strip() for m in matches] == ["A", "B", "C"]


def test_a_closed_file_block_still_parses():
    text = "=== FILE: one.md ===\nA\n=== END FILE ===\n=== FILE: two.md ===\nB\n=== END FILE ===\n"
    assert [m.group("path").strip() for m in FILE_BLOCK_RE.finditer(text)] == ["one.md", "two.md"]


# -------------------------------------------------------------- B1-F6 (REQUIRED)
@pytest.mark.asyncio
async def test_a_refused_file_write_is_still_visible_to_the_critic(settings):
    marker = "SECRET_SHOULD_BE_VISIBLE_OR_ERRORED"
    plan = {
        "dod": [{"id": "d1", "criterion": "ok"}],
        "tasks": [
            {"id": "t1", "title": "write", "skill_id": "general-assistant", "instruction": "x", "writes_files": True}
        ],
    }
    body = (
        "=== FILE: good.md ===\nVISIBLE_GOOD\n=== END FILE ===\n"
        f"=== FILE: ../../pwned.md ===\n{marker}\n=== END FILE ===\nprose"
    )
    run, _ = await run_goal(settings, Script(plan=plan, worker_text=body), GOAL, max_rounds=1)
    artifact = next(e["payload"]["artifact"] for e in run.bus.events if e["type"] == "worker.finished")
    errors = [e["payload"] for e in run.bus.events if e["type"] == "tool.error"]
    assert errors and errors[0]["error_tag"] == "WORKSPACE_ESCAPE"
    assert "VISIBLE_GOOD" in artifact
    assert marker in artifact or "WORKSPACE_ESCAPE" in artifact, artifact[:400]


# ----------------------------------------------------------- B1-F8 (RECOMMENDED)
def test_esc_escapes_quotes_so_an_attribute_cannot_close_early():
    escaped = _esc('x"><img.md')
    assert '"' not in escaped and "&quot;" in escaped
    assert "&lt;" in escaped and "&gt;" in escaped
    assert f'<file path="{escaped}">'.count('"') == 2


def test_json_flag_reads_a_string_that_says_false_as_false():
    assert json_flag(True) is True
    assert json_flag("true") is True
    assert json_flag("false") is False
    assert json_flag("no") is False
    assert json_flag(None) is False
