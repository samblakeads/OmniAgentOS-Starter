"""The provider client: error tags, retries, JSON repair, streaming, budget."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from conftest import json_response, provider_config, stream_response

from omniagentos_starter.llm import Budget, LLMClient, extract_json
from omniagentos_starter.redact import ProviderError


async def _no_sleep(_a):
    await asyncio.sleep(0)


def client(handler, **kw) -> LLMClient:
    return LLMClient(provider_config(), transport=httpx.MockTransport(handler), retry_sleep=_no_sleep, **kw)


@pytest.mark.parametrize(
    "status,tag",
    [(401, "PROVIDER_AUTH"), (403, "PROVIDER_AUTH"), (429, "PROVIDER_RATE_LIMIT"), (503, "PROVIDER_UNAVAILABLE")],
)
async def test_http_status_maps_to_an_error_tag(status, tag):
    c = client(lambda r: httpx.Response(status, text="upstream says no"))
    with pytest.raises(ProviderError) as exc:
        await c.complete_json([{"role": "user", "content": "x"}], "{}", role="planner")
    assert exc.value.error_tag == tag
    assert exc.value.status == status


async def test_a_transport_timeout_is_provider_unavailable():
    def boom(request):
        raise httpx.ConnectTimeout("timed out", request=request)

    c = client(boom)
    with pytest.raises(ProviderError) as exc:
        await c.complete_json([{"role": "user", "content": "x"}], "{}", role="planner")
    assert exc.value.error_tag == "PROVIDER_UNAVAILABLE"


async def test_an_empty_body_is_a_bad_response_not_a_silent_success():
    c = client(lambda r: httpx.Response(200, json={"id": "x", "choices": []}))
    with pytest.raises(ProviderError) as exc:
        await c.complete_json([{"role": "user", "content": "x"}], "{}", role="planner")
    assert exc.value.error_tag == "PROVIDER_BAD_RESPONSE"


async def test_a_rate_limit_is_retried_then_succeeds():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, text="slow down")
        return json_response({"ok": True})

    c = client(handler)
    assert await c.complete_json([{"role": "user", "content": "x"}], "{}", role="planner") == {"ok": True}
    assert calls["n"] == 2


async def test_unparseable_json_gets_exactly_one_repair_retry():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(
                200,
                json={"id": "r", "choices": [{"message": {"content": "Sure! Here you go: not json"}}], "usage": {}},
            )
        return json_response({"repaired": True})

    c = client(handler)
    assert await c.complete_json([{"role": "user", "content": "x"}], "{}", role="critic") == {"repaired": True}
    assert calls["n"] == 2


async def test_a_second_unparseable_reply_raises_bad_response():
    c = client(
        lambda r: httpx.Response(200, json={"id": "r", "choices": [{"message": {"content": "nope"}}], "usage": {}})
    )
    with pytest.raises(ProviderError) as exc:
        await c.complete_json([{"role": "user", "content": "x"}], "{}", role="critic")
    assert exc.value.error_tag == "PROVIDER_BAD_RESPONSE"


async def test_json_mode_is_requested_for_structured_roles():
    seen = {}

    def handler(request):
        seen.update(json.loads(request.content.decode()))
        return json_response({"ok": True})

    await client(handler).complete_json([{"role": "user", "content": "x"}], "{schema}", role="planner")
    assert seen["response_format"] == {"type": "json_object"}
    assert seen["messages"][-1]["content"].endswith("{schema}")


async def test_streaming_delivers_deltas_in_order_and_returns_the_whole_text():
    pieces = []
    c = client(lambda r: stream_response("Hello there, production line."))
    text = await c.stream([{"role": "user", "content": "x"}], on_delta=pieces.append, role="worker")
    assert "".join(pieces) == text == "Hello there, production line."
    assert len(pieces) > 1


async def test_an_empty_stream_is_a_bad_response():
    c = client(lambda r: stream_response(""))
    with pytest.raises(ProviderError) as exc:
        await c.stream([{"role": "user", "content": "x"}], on_delta=lambda t: None, role="worker")
    assert exc.value.error_tag == "PROVIDER_BAD_RESPONSE"


async def test_an_unconfigured_provider_never_makes_a_call():
    c = LLMClient(provider_config(configured=False, api_key=""))
    with pytest.raises(ProviderError) as exc:
        await c.complete_json([{"role": "user", "content": "x"}], "{}", role="planner")
    assert exc.value.error_tag == "PROVIDER_NOT_CONFIGURED"


async def test_the_budget_is_a_hard_ceiling():
    budget = Budget(max_calls=2)
    c = client(lambda r: json_response({"ok": True}), budget=budget)
    await c.complete_json([{"role": "user", "content": "x"}], "{}", role="planner")
    await c.complete_json([{"role": "user", "content": "x"}], "{}", role="planner")
    with pytest.raises(ProviderError) as exc:
        await c.complete_json([{"role": "user", "content": "x"}], "{}", role="planner")
    assert exc.value.error_tag == "BUDGET_EXCEEDED"


async def test_every_call_emits_an_llm_call_event_with_provenance():
    events = []
    c = client(lambda r: json_response({"ok": True}, response_id="resp-42"), on_event=lambda t, p: events.append((t, p)))
    await c.complete_json([{"role": "user", "content": "x"}], "{}", role="verifier")
    assert len(events) == 1
    etype, payload = events[0]
    assert etype == "llm.call"
    assert payload["role"] == "verifier"
    assert payload["provider_host"] == "api.x.ai"
    assert payload["http_status"] == 200
    assert payload["response_id"] == "resp-42"
    assert payload["prompt_tokens"] == 120 and payload["completion_tokens"] == 60


async def test_the_probe_reports_reachability_not_key_presence():
    assert (await client(lambda r: json_response({"ok": True})).probe())[0] is True
    ok, tag, _ = await client(lambda r: httpx.Response(401, text="bad key")).probe()
    assert (ok, tag) == (False, "PROVIDER_AUTH")
    ok, tag, _ = await LLMClient(provider_config(configured=False, api_key="")).probe()
    assert (ok, tag) == (False, "PROVIDER_NOT_CONFIGURED")


def test_extract_json_survives_fences_and_preamble():
    assert extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert extract_json('Here you go: {"a": 2} — enjoy') == {"a": 2}
    with pytest.raises(ValueError):
        extract_json("no object here")
    with pytest.raises(ValueError):
        extract_json("[1, 2, 3]")
