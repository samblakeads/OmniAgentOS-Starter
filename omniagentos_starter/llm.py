"""Async OpenAI-compatible LLM client.

Two entry points the engine uses:

* :meth:`LLMClient.complete_json` — structured role output (`response_format`
  ``json_object``) with one repair retry when the body will not parse.
* :meth:`LLMClient.stream` — token streaming for workers, so the dashboard shows
  the deliverable typing itself rather than a spinner and a late burst.

Every call emits an ``llm.call`` event carrying provider_host, http_status and
the provider's response id — never any prompt or completion content.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

import httpx

from .config import MAX_LLM_CALLS_PER_RUN, ProviderConfig, estimate_cost_usd
from .redact import ProviderError, redact, redact_text, register_secret

Messages = Sequence[dict]
JSON_INSTRUCTION = (
    "Reply with a single JSON object and nothing else — no prose, no markdown fence. "
    "It must match this schema exactly:\n"
)

_RETRYABLE_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}


def _error_tag_for_status(status: int) -> str:
    if status in (401, 403):
        return "PROVIDER_AUTH"
    if status == 429:
        return "PROVIDER_RATE_LIMIT"
    if status >= 500 or status in (408, 409, 425):
        return "PROVIDER_UNAVAILABLE"
    if status == 400:
        return "PROVIDER_BAD_RESPONSE"
    return "PROVIDER_UNAVAILABLE"


def extract_json(text: str) -> dict:
    """Parse a JSON object out of a model reply, tolerating fences and preamble."""
    if text is None:
        raise ValueError("empty body")
    raw = text.strip()
    if raw.startswith("```"):
        raw = raw.split("```", 2)[1] if raw.count("```") >= 2 else raw.strip("`")
        if raw.lstrip().lower().startswith("json"):
            raw = raw.lstrip()[4:]
        raw = raw.strip()
    try:
        parsed = json.loads(raw)
    except Exception:
        start, end = raw.find("{"), raw.rfind("}")
        if start == -1 or end <= start:
            raise ValueError("no JSON object in response") from None
        parsed = json.loads(raw[start : end + 1])
    if not isinstance(parsed, dict):
        raise ValueError("JSON root is not an object")
    return parsed


class Budget:
    """Per-run call ceiling and token/cost tally."""

    def __init__(self, max_calls: int = MAX_LLM_CALLS_PER_RUN):
        self.max_calls = max_calls
        self.calls = 0
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.cost_usd = 0.0

    def check(self) -> None:
        if self.calls >= self.max_calls:
            raise ProviderError(
                "BUDGET_EXCEEDED",
                None,
                f"run exceeded MAX_LLM_CALLS_PER_RUN={self.max_calls}",
            )

    def record(self, model: str, prompt_tokens: int, completion_tokens: int) -> None:
        self.calls += 1
        self.prompt_tokens += prompt_tokens
        self.completion_tokens += completion_tokens
        self.cost_usd = round(self.cost_usd + estimate_cost_usd(model, prompt_tokens, completion_tokens), 6)

    def as_dict(self) -> dict:
        return {
            "llm_calls": self.calls,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "tokens": self.prompt_tokens + self.completion_tokens,
            "est_cost_usd": round(self.cost_usd, 6),
        }


class LLMClient:
    def __init__(
        self,
        provider: ProviderConfig,
        on_event: Callable[[str, dict], Any] | None = None,
        on_transcript: Callable[[dict], Any] | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        budget: Budget | None = None,
        timeout: float = 90.0,
        retry_sleep: Callable[[int], Awaitable[None]] | None = None,
        max_retries: int = 3,
    ):
        self.provider = provider
        self.on_event = on_event
        self.on_transcript = on_transcript
        self.transport = transport
        self.budget = budget or Budget()
        self.timeout = timeout
        self.max_retries = max_retries
        self._retry_sleep = retry_sleep or (lambda attempt: asyncio.sleep(min(8.0, 0.5 * 2**attempt)))
        register_secret(provider.api_key)

    # ------------------------------------------------------------- plumbing
    def _headers(self) -> dict:
        h = {"Content-Type": "application/json"}
        if self.provider.api_key:
            h["Authorization"] = f"Bearer {self.provider.api_key}"
        if self.provider.provider == "openrouter":
            h["HTTP-Referer"] = "https://github.com/omnirogue/OmniAgentOS-Starter"
            h["X-Title"] = "OmniAgentOS Starter"
        return h

    def _require_configured(self) -> None:
        if not self.provider.configured or not self.provider.api_key:
            raise ProviderError(
                "PROVIDER_NOT_CONFIGURED",
                None,
                "no provider key found: set XAI_API_KEY, OPENROUTER_API_KEY or OPENAI_API_KEY",
            )

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self.provider.base_url,
            headers=self._headers(),
            timeout=self.timeout,
            transport=self.transport,
        )

    def _emit(self, etype: str, payload: dict) -> None:
        if self.on_event:
            self.on_event(etype, redact(payload))

    def _transcript(self, entry: dict) -> None:
        if self.on_transcript:
            self.on_transcript(redact(entry))

    def _emit_call(
        self,
        role: str,
        model: str,
        started: float,
        status: int | None,
        response_id: str,
        usage: dict,
        stream: bool,
        attempt: int,
    ) -> None:
        prompt_tokens = int(usage.get("prompt_tokens") or 0)
        completion_tokens = int(usage.get("completion_tokens") or 0)
        self.budget.record(model, prompt_tokens, completion_tokens)
        self._emit(
            "llm.call",
            {
                "role": role,
                "model": model,
                "ms": int((time.monotonic() - started) * 1000),
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "provider": self.provider.provider,
                "provider_host": self.provider.host,
                "http_status": status,
                "response_id": response_id,
                "stream": stream,
                "attempt": attempt,
                "call_index": self.budget.calls,
            },
        )

    @staticmethod
    def _safe_body(text: str) -> str:
        return redact_text((text or "").strip())[:300]

    # ---------------------------------------------------------------- calls
    async def _complete_raw(
        self, messages: Messages, role: str, model: str | None, json_mode: bool
    ) -> tuple[str, str]:
        """Non-streaming call → (content, response_id). Retries 429/5xx."""
        self._require_configured()
        model = model or self.provider.model
        payload: dict[str, Any] = {"model": model, "messages": list(messages), "temperature": 0.3}
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        last: ProviderError | None = None
        for attempt in range(self.max_retries):
            self.budget.check()
            started = time.monotonic()
            async with self._client() as client:
                try:
                    resp = await client.post("/chat/completions", json=payload)
                except (httpx.TimeoutException, httpx.TransportError) as exc:
                    last = ProviderError(
                        "PROVIDER_UNAVAILABLE", None, f"{type(exc).__name__} contacting {self.provider.host}"
                    )
                    self._emit_call(role, model, started, None, "", {}, False, attempt)
                    if attempt < self.max_retries - 1:
                        await self._retry_sleep(attempt)
                        continue
                    raise last from None
                status = resp.status_code
                text = resp.text
                if status >= 400:
                    tag = _error_tag_for_status(status)
                    last = ProviderError(tag, status, self._safe_body(text))
                    self._emit_call(role, model, started, status, "", {}, False, attempt)
                    if status in _RETRYABLE_STATUS and attempt < self.max_retries - 1:
                        await self._retry_sleep(attempt)
                        continue
                    raise last
                try:
                    body = resp.json()
                    content = body["choices"][0]["message"]["content"]
                except Exception:
                    last = ProviderError(
                        "PROVIDER_BAD_RESPONSE", status, "provider returned a body with no message content"
                    )
                    self._emit_call(role, model, started, status, "", {}, False, attempt)
                    if attempt < self.max_retries - 1:
                        await self._retry_sleep(attempt)
                        continue
                    raise last from None
                response_id = str(body.get("id") or "")
                self._emit_call(role, model, started, status, response_id, body.get("usage") or {}, False, attempt)
                return content or "", response_id
        raise last or ProviderError("PROVIDER_UNAVAILABLE", None, "no attempt succeeded")

    async def complete_json(
        self,
        messages: Messages,
        schema_hint: str,
        role: str = "agent",
        model: str | None = None,
    ) -> dict:
        """Structured call. One repair retry when the reply will not parse."""
        msgs = list(messages)
        msgs.append({"role": "system", "content": JSON_INSTRUCTION + schema_hint})
        content, response_id = await self._complete_raw(msgs, role, model, json_mode=True)
        try:
            parsed = extract_json(content)
        except Exception as first_error:
            repair = list(msgs) + [
                {"role": "assistant", "content": content[:2000]},
                {
                    "role": "user",
                    "content": (
                        "That was not valid JSON ("
                        + str(first_error)[:120]
                        + "). Reply again with ONLY the JSON object matching the schema."
                    ),
                },
            ]
            content2, response_id2 = await self._complete_raw(repair, role, model, json_mode=True)
            self._transcript(
                {
                    "role": role,
                    "kind": "json-repair",
                    "messages": repair,
                    "response": content2,
                    "response_id": response_id2,
                }
            )
            try:
                return extract_json(content2)
            except Exception as second_error:
                raise ProviderError(
                    "PROVIDER_BAD_RESPONSE", 200, f"model did not return parseable JSON: {second_error}"
                ) from None
        self._transcript(
            {"role": role, "kind": "json", "messages": msgs, "response": content, "response_id": response_id}
        )
        return parsed

    async def stream(
        self,
        messages: Messages,
        on_delta: Callable[[str], Any],
        role: str = "worker",
        model: str | None = None,
    ) -> str:
        """Streaming call. Deltas are handed to `on_delta` as they arrive."""
        self._require_configured()
        model = model or self.provider.model
        payload = {"model": model, "messages": list(messages), "temperature": 0.4, "stream": True}
        last: ProviderError | None = None
        for attempt in range(self.max_retries):
            self.budget.check()
            started = time.monotonic()
            chunks: list[str] = []
            usage: dict = {}
            response_id = ""
            status: int | None = None
            try:
                async with self._client() as client:
                    async with client.stream("POST", "/chat/completions", json=payload) as resp:
                        status = resp.status_code
                        if status >= 400:
                            body = await resp.aread()
                            tag = _error_tag_for_status(status)
                            last = ProviderError(tag, status, self._safe_body(body.decode("utf-8", "replace")))
                            self._emit_call(role, model, started, status, "", {}, True, attempt)
                            if status in _RETRYABLE_STATUS and attempt < self.max_retries - 1:
                                await self._retry_sleep(attempt)
                                continue
                            raise last
                        async for line in resp.aiter_lines():
                            if not line or not line.startswith("data:"):
                                continue
                            data = line[5:].strip()
                            if data == "[DONE]":
                                break
                            try:
                                obj = json.loads(data)
                            except Exception:
                                continue
                            response_id = response_id or str(obj.get("id") or "")
                            if obj.get("usage"):
                                usage = obj["usage"]
                            for choice in obj.get("choices") or []:
                                piece = (choice.get("delta") or {}).get("content")
                                if piece:
                                    chunks.append(piece)
                                    res = on_delta(piece)
                                    if asyncio.iscoroutine(res):
                                        await res
            except ProviderError:
                raise
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last = ProviderError(
                    "PROVIDER_UNAVAILABLE", None, f"{type(exc).__name__} streaming from {self.provider.host}"
                )
                self._emit_call(role, model, started, None, "", {}, True, attempt)
                if attempt < self.max_retries - 1:
                    await self._retry_sleep(attempt)
                    continue
                raise last from None

            text = "".join(chunks)
            if not usage:
                prompt_chars = sum(len(str(m.get("content", ""))) for m in messages)
                usage = {"prompt_tokens": prompt_chars // 4, "completion_tokens": len(text) // 4}
            self._emit_call(role, model, started, status, response_id, usage, True, attempt)
            if not text.strip():
                last = ProviderError("PROVIDER_BAD_RESPONSE", status, "provider streamed an empty completion")
                if attempt < self.max_retries - 1:
                    await self._retry_sleep(attempt)
                    continue
                raise last
            self._transcript(
                {
                    "role": role,
                    "kind": "stream",
                    "messages": list(messages),
                    "response": text,
                    "response_id": response_id,
                }
            )
            return text
        raise last or ProviderError("PROVIDER_UNAVAILABLE", None, "no attempt succeeded")

    async def probe(self) -> tuple[bool, str | None, str]:
        """Cheap liveness probe used at startup: (ok, error_tag, detail)."""
        try:
            self._require_configured()
        except ProviderError as exc:
            return False, exc.error_tag, exc.safe_message
        payload = {
            "model": self.provider.model,
            "messages": [{"role": "user", "content": "ping"}],
            "max_tokens": 1,
            "temperature": 0,
        }
        try:
            async with httpx.AsyncClient(
                base_url=self.provider.base_url,
                headers=self._headers(),
                timeout=20.0,
                transport=self.transport,
            ) as client:
                resp = await client.post("/chat/completions", json=payload)
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            return False, "PROVIDER_UNAVAILABLE", f"{type(exc).__name__} contacting {self.provider.host}"
        if resp.status_code >= 400:
            return False, _error_tag_for_status(resp.status_code), self._safe_body(resp.text)
        return True, None, f"probe {resp.status_code} from {self.provider.host}"
