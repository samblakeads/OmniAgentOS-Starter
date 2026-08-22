"""Provider resolution, brand, caps and the bind policy."""

from __future__ import annotations

import pytest

from omniagentos_starter import config


def test_xai_is_the_default_provider():
    p = config.resolve_provider({"XAI_API_KEY": "xai-abc"})
    assert (p.configured, p.provider, p.model, p.base_url) == (True, "xai", "grok-4.3", "https://api.x.ai/v1")
    assert p.host == "api.x.ai"


def test_resolution_order_prefers_the_override_then_xai_then_openrouter_then_openai():
    env = {
        "OMNIAGENTOS_API_KEY": "k1",
        "OMNIAGENTOS_BASE_URL": "https://gateway.internal/v1",
        "XAI_API_KEY": "k2",
        "OPENROUTER_API_KEY": "k3",
        "OPENAI_API_KEY": "k4",
    }
    assert config.resolve_provider(env).base_url == "https://gateway.internal/v1"
    env.pop("OMNIAGENTOS_API_KEY")
    assert config.resolve_provider(env).provider == "xai"
    env.pop("XAI_API_KEY")
    p = config.resolve_provider(env)
    assert (p.provider, p.model) == ("openrouter", "x-ai/grok-4.3")
    env.pop("OPENROUTER_API_KEY")
    p = config.resolve_provider(env)
    assert (p.provider, p.model) == ("openai", "gpt-4.1-mini")


def test_model_override_applies_to_every_provider():
    p = config.resolve_provider({"XAI_API_KEY": "k", "OMNIAGENTOS_MODEL": "grok-4.3-fast"})
    assert p.model == "grok-4.3-fast"


def test_no_key_is_a_state_not_an_exception():
    p = config.resolve_provider({})
    assert p.configured is False
    assert p.error_tag == "PROVIDER_NOT_CONFIGURED"
    assert p.api_key == ""


def test_whitespace_only_key_does_not_count_as_configured():
    assert config.resolve_provider({"XAI_API_KEY": "   "}).configured is False


def test_redacted_dict_never_carries_the_key():
    p = config.resolve_provider({"XAI_API_KEY": "xai-supersecret-value"})
    assert "xai-supersecret-value" not in str(p.redacted_dict())
    assert "xai-supersecret-value" not in repr(p)


def test_loopback_binds_need_no_token():
    for host in ("127.0.0.1", "localhost", "::1", ""):
        config.validate_bind(host, {})


def test_public_bind_without_a_token_is_refused():
    with pytest.raises(config.BindRefused):
        config.validate_bind("0.0.0.0", {})
    with pytest.raises(config.BindRefused):
        config.validate_bind("192.168.1.20", {})


def test_public_bind_with_a_token_is_allowed():
    config.validate_bind("0.0.0.0", {"OMNIAGENTOS_TOKEN": "t0ken"})


def test_caps_are_the_documented_values():
    assert config.MAX_PLAN_TASKS == 6
    assert config.MAX_DOD_CRITERIA == 8
    assert config.MAX_ROUNDS == 3
    assert config.MAX_LLM_CALLS_PER_RUN == 30
    assert config.MAX_CONCURRENT_RUNS == 2


def test_brand_defaults_and_overrides():
    assert config.resolve_brand({}).name == "OmniRogue"
    assert config.resolve_brand({}).logo_url == "/assets/omnirogue-logo.png"
    b = config.resolve_brand({"OMNIAGENTOS_BRAND_NAME": "Acme", "OMNIAGENTOS_BRAND_LOGO": "/assets/acme.png"})
    assert b.as_dict() == {"name": "Acme", "logo_url": "/assets/acme.png"}
