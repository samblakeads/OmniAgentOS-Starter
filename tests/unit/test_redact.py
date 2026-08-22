"""Redaction: a key that reaches an error body must never reach a user."""

from __future__ import annotations

import httpx
import pytest
from conftest import TEST_KEY, provider_config

from omniagentos_starter import redact as R
from omniagentos_starter.llm import LLMClient


def test_env_key_is_redacted_everywhere(monkeypatch):
    monkeypatch.setenv("XAI_API_KEY", "xai-live-key-9876543210")
    text = "upstream said: Invalid api key xai-live-key-9876543210 for org"
    assert "xai-live-key-9876543210" not in R.redact(text)
    assert R.PLACEHOLDER in R.redact(text)


def test_registered_secret_is_redacted_without_env():
    R.register_secret("sup3rsecret-value-not-in-env")
    assert "sup3rsecret-value-not-in-env" not in R.redact("token=sup3rsecret-value-not-in-env")


def test_bearer_and_key_shapes_are_redacted_even_when_unknown():
    assert "Bearer abcdef0123456789" not in R.redact("Authorization: Bearer abcdef0123456789")
    assert "sk-live-key-0987-6543" not in R.redact("sk-live-key-0987-6543")


def test_redact_walks_dicts_lists_and_keys(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-nested-secret-value-1")
    payload = {"outer": [{"inner": "sk-nested-secret-value-1"}], "sk-nested-secret-value-1": "k"}
    out = R.redact(payload)
    assert "sk-nested-secret-value-1" not in str(out)


def test_contains_secret_detects_a_leak(monkeypatch):
    monkeypatch.setenv("XAI_API_KEY", "xai-leak-detector-value")
    assert R.contains_secret({"a": "xai-leak-detector-value"}) is True
    assert R.contains_secret({"a": "clean"}) is False


async def provider_config_error_body_is_redacted_before_it_is_raised(monkeypatch):
    """A 401 whose body echoes the key must not carry the key into the exception."""
    monkeypatch.setenv("XAI_API_KEY", TEST_KEY)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": f"Incorrect API key provided: {TEST_KEY}"})

    client = LLMClient(provider_config(), transport=httpx.MockTransport(handler))
    with pytest.raises(R.ProviderError) as exc:
        await client.complete_json([{"role": "user", "content": "hi"}], "{}", role="planner")
    assert exc.value.error_tag == "PROVIDER_AUTH"
    assert TEST_KEY not in exc.value.safe_message
    assert TEST_KEY not in str(exc.value)
    assert TEST_KEY not in str(exc.value.as_dict())


async def test_llm_call_events_never_contain_prompt_or_key(monkeypatch):
    monkeypatch.setenv("XAI_API_KEY", TEST_KEY)
    events: list[tuple[str, dict]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text=f"upstream down, key {TEST_KEY}")

    client = LLMClient(
        provider_config(),
        on_event=lambda t, p: events.append((t, p)),
        transport=httpx.MockTransport(handler),
        retry_sleep=lambda _a: __import__("asyncio").sleep(0),
    )
    with pytest.raises(R.ProviderError) as exc:
        await client.complete_json([{"role": "user", "content": "hi"}], "{}", role="critic")
    assert exc.value.error_tag == "PROVIDER_UNAVAILABLE"
    assert events, "every attempt emits an llm.call event"
    for _type, payload in events:
        assert TEST_KEY not in str(payload)
        assert "hi" not in str(payload.get("prompt", ""))
        assert payload["provider_host"] == "api.x.ai"
        assert payload["http_status"] == 503
