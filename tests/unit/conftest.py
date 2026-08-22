"""Shared test harness: a scripted provider behind httpx.MockTransport.

No test in this directory touches the network. The fake provider answers by
role, parsing the criterion ids straight out of the prompt so a test never has
to hard-code the Definition of Done the engine built.
"""

from __future__ import annotations

import asyncio
import json
import re

import httpx
import pytest

from omniagentos_starter.config import ProviderConfig, Settings
from omniagentos_starter.engine import Orchestrator
from omniagentos_starter.redact import clear_registered_secrets

TEST_KEY = "xai-unit-test-key-abcdef0123456789"

IDS_RE = re.compile(r"IDS YOU MUST RETURN:\s*(.+)")


def provider_config(**kw) -> ProviderConfig:
    defaults = dict(
        configured=True,
        provider="xai",
        model="grok-4.3",
        base_url="https://api.x.ai/v1",
        api_key=TEST_KEY,
        key_env="XAI_API_KEY",
    )
    defaults.update(kw)
    return ProviderConfig(**defaults)


def role_of(payload: dict) -> str:
    system = " ".join(m.get("content", "") for m in payload.get("messages", []) if m.get("role") == "system")
    for marker, role in (
        ("You are the PLANNER agent", "planner"),
        ("You are a WORKER agent", "worker"),
        ("You are the CRITIC agent", "critic"),
        ("You are the VERIFIER agent", "verifier"),
        ("You are the REFLECTOR agent", "reflector"),
    ):
        if marker in system:
            return role
    return "unknown"


def ids_in(payload: dict) -> list[str]:
    text = "\n".join(m.get("content", "") for m in payload.get("messages", []))
    match = IDS_RE.search(text)
    if not match:
        return []
    return [i.strip() for i in match.group(1).split(",") if i.strip()]


def json_response(obj, response_id="resp-json-1", status=200) -> httpx.Response:
    body = {
        "id": response_id,
        "choices": [{"index": 0, "message": {"role": "assistant", "content": json.dumps(obj)}}],
        "usage": {"prompt_tokens": 120, "completion_tokens": 60},
    }
    return httpx.Response(status, json=body)


def stream_response(text: str, response_id="resp-stream-1") -> httpx.Response:
    lines = []
    for chunk in [text[i : i + 24] for i in range(0, len(text), 24)] or [""]:
        lines.append(
            "data: " + json.dumps({"id": response_id, "choices": [{"delta": {"content": chunk}}]})
        )
    lines.append(
        "data: "
        + json.dumps({"id": response_id, "choices": [], "usage": {"prompt_tokens": 200, "completion_tokens": 80}})
    )
    lines.append("data: [DONE]")
    return httpx.Response(200, text="\n\n".join(lines) + "\n\n")


class Script:
    """A scripted provider. Every request is recorded for assertions."""

    def __init__(
        self,
        plan=None,
        worker_text="THE DELIVERABLE\n1. one\n2. two\n3. three",
        critic=None,
        verifier=None,
        reflector=None,
    ):
        self.plan = plan or {
            "dod": [{"id": "p1", "criterion": "The deliverable contains exactly three items."}],
            "tasks": [
                {
                    "id": "t1",
                    "title": "Draft the deliverable",
                    "skill_id": "general-assistant",
                    "instruction": "do it",
                    "depends_on": [],
                    "writes_files": False,
                }
            ],
        }
        self.worker_text = worker_text
        self.critic = critic or (lambda call, ids: [self.verdict(i, True) for i in ids])
        self.verifier = verifier or (lambda call, ids: [self.verdict(i, True) for i in ids])
        self.reflector = reflector or {"text": "State the constraint before drafting.", "tags": ["style"]}
        self.orch = None
        self.requests: list[tuple[str, dict]] = []
        self.counts: dict[str, int] = {}

    @staticmethod
    def verdict(cid, ok, task_id="t1", reason="evidence", fix="do better"):
        return {"criterion_id": cid, "task_id": task_id, "pass": ok, "reason": reason, "fix": fix}

    def payloads(self, role: str) -> list[dict]:
        return [p for r, p in self.requests if r == role]

    def prompt_text(self, role: str, index: int = 0) -> str:
        return "\n".join(m.get("content", "") for m in self.payloads(role)[index]["messages"])

    def handler(self, request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content.decode())
        role = role_of(payload)
        self.requests.append((role, payload))
        self.counts[role] = self.counts.get(role, 0) + 1
        call = self.counts[role]
        if payload.get("max_tokens") == 1:  # startup probe
            return json_response({"ok": True}, response_id="resp-probe")
        if role == "planner":
            return json_response(self.plan, response_id=f"resp-planner-{call}")
        if role == "worker":
            text = self.worker_text(call, payload) if callable(self.worker_text) else self.worker_text
            return stream_response(text, response_id=f"resp-worker-{call}")
        if role == "critic":
            return json_response({"verdicts": self.critic(call, ids_in(payload))}, response_id=f"resp-critic-{call}")
        if role == "verifier":
            return json_response(
                {"verdicts": self.verifier(call, ids_in(payload))}, response_id=f"resp-verifier-{call}"
            )
        if role == "reflector":
            return json_response(self.reflector, response_id=f"resp-reflector-{call}")
        return json_response({"ok": True}, response_id="resp-unknown")


@pytest.fixture(autouse=True)
def _no_leaked_secrets():
    clear_registered_secrets()
    yield
    clear_registered_secrets()


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        host="127.0.0.1",
        port=0,
        data_dir=tmp_path / "var",
        workspace_dir=tmp_path / "workspace",
        provider=provider_config(),
    )


async def _no_sleep(_attempt: int) -> None:
    await asyncio.sleep(0)


def make_orchestrator(settings: Settings, script: Script) -> Orchestrator:
    return Orchestrator(settings, transport=httpx.MockTransport(script.handler), retry_sleep=_no_sleep)


async def run_goal(settings: Settings, script: Script, goal: str, max_rounds: int = 3, extra_dod=None):
    """Execute one goal against the scripted provider. Returns (run, script)."""
    orch = make_orchestrator(settings, script)
    script.orch = orch
    run = orch.create(goal, max_rounds, extra_dod or [])
    await orch.execute(run)
    return run, script
