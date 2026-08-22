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
import hashlib
import json
import time
import uuid
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


def system_prompt_sha256(messages: Messages) -> str:
    """Hash of the system prompt actually sent — proof of which agent spoke."""
    system = "\n".join(str(m.get("content", "")) for m in messages if m.get("role") == "system")
    return hashlib.sha256(system.encode("utf-8")).hexdigest()


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
    """Per-run call ceiling and token/cost tally.

    The ceiling counts calls that SUCCEEDED. Counting failed attempts toward it
    meant a provider rate-limit storm reported itself as BUDGET_EXCEEDED — our
    bug, not theirs — and burned the run's remaining allowance on retries that
    produced nothing. Failed attempts are still tallied separately, because they
    still cost time and, sometimes, tokens.
    """

    def __init__(self, max_calls: int = MAX_LLM_CALLS_PER_RUN):
        self.max_calls = max_calls
        self.calls = 0
        self.failed_calls = 0
        self.reserved = 0
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.cost_usd = 0.0

    def check(self) -> None:
        if self.calls + self.reserved >= self.max_calls:
            raise ProviderError(
                "BUDGET_EXCEEDED",
                None,
                f"run exceeded MAX_LLM_CALLS_PER_RUN={self.max_calls}",
            )

    def reserve(self) -> None:
        """Claim a slot before awaiting, so two concurrent workers cannot both pass check()."""
        self.check()
        self.reserved += 1

    def release(self) -> None:
        self.reserved = max(0, self.reserved - 1)

    def record(
        self, model: str, prompt_tokens: int, completion_tokens: int, ok: bool = True
    ) -> None:
        if ok:
            self.calls += 1
        else:
            self.failed_calls += 1
        self.prompt_tokens += prompt_tokens
        self.completion_tokens += completion_tokens
        self.cost_usd = round(self.cost_usd + estimate_cost_usd(model, prompt_tokens, completion_tokens), 6)

    def as_dict(self) -> dict:
        return {
            "llm_calls": self.calls,
            "failed_calls": self.failed_calls,
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
        # the provenance of the most recent call, so a caller can stamp the event
        # it emits with the exact request that produced it
        self.last_request_id: str = ""
        self.last_response_id: str = ""
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
        request_id: str = "",
        system_sha: str = "",
        ok: bool = True,
        error_tag: str | None = None,
    ) -> None:
        prompt_tokens = int(usage.get("prompt_tokens") or 0)
        completion_tokens = int(usage.get("completion_tokens") or 0)
        self.budget.record(model, prompt_tokens, completion_tokens, ok=ok)
        self.last_request_id = request_id or self.last_request_id
        self.last_response_id = response_id or self.last_response_id
        self._emit(
            "llm.call",
            {
                "ok": ok,
                "error_tag": error_tag,
                "role": role,
                "model": model,
                "ms": int((time.monotonic() - started) * 1000),
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "provider": self.provider.provider,
                "provider_host": self.provider.host,
                "http_status": status,
                "response_id": response_id,
                "request_id": request_id,
                "system_prompt_sha256": system_sha,
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
        self, messages: Messages, role: str, model: str | None, json_mode: bool, max_tokens: int | None = None
    ) -> tuple[str, str]:
        """Non-streaming call → (content, response_id). Retries 429/5xx."""
        self._require_configured()
        model = model or self.provider.model
        payload: dict[str, Any] = {"model": model, "messages": list(messages), "temperature": 0.3}
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        if max_tokens:
            payload["max_tokens"] = int(max_tokens)

        system_sha = system_prompt_sha256(messages)
        last: ProviderError | None = None
        for attempt in range(self.max_retries):
            # reserve() is check() plus a claim held across the await, so two
            # concurrent workers cannot both pass the ceiling in the same gap.
            self.budget.reserve()
            started = time.monotonic()
            request_id = uuid.uuid4().hex
            self.last_request_id = request_id
            try:
                async with self._client() as client:
                    try:
                        resp = await client.post(
                            "/chat/completions", json=payload, headers={"X-Request-Id": request_id}
                        )
                    except (httpx.TimeoutException, httpx.TransportError) as exc:
                        last = ProviderError(
                            "PROVIDER_UNAVAILABLE", None, f"{type(exc).__name__} contacting {self.provider.host}"
                        )
                        self._emit_call(
                            role, model, started, None, "", {}, False, attempt, request_id, system_sha,
                            ok=False, error_tag=last.error_tag,
                        )
                        if attempt < self.max_retries - 1:
                            await self._retry_sleep(attempt)
                            continue
                        raise last from None
                    status = resp.status_code
                    text = resp.text
                    if status >= 400:
                        tag = _error_tag_for_status(status)
                        last = ProviderError(tag, status, self._safe_body(text))
                        self._emit_call(
                            role, model, started, status, "", {}, False, attempt, request_id, system_sha,
                            ok=False, error_tag=tag,
                        )
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
                        self._emit_call(
                            role, model, started, status, "", {}, False, attempt, request_id, system_sha,
                            ok=False, error_tag="PROVIDER_BAD_RESPONSE",
                        )
                        if attempt < self.max_retries - 1:
                            await self._retry_sleep(attempt)
                            continue
                        raise last from None
                    response_id = str(body.get("id") or "")
                    self._emit_call(
                        role, model, started, status, response_id, body.get("usage") or {},
                        False, attempt, request_id, system_sha,
                    )
                    return content or "", response_id
            finally:
                self.budget.release()
        raise last or ProviderError("PROVIDER_UNAVAILABLE", None, "no attempt succeeded")

    async def complete_json(
        self,
        messages: Messages,
        schema_hint: str,
        role: str = "agent",
        model: str | None = None,
        max_tokens: int | None = None,
        extra: dict | None = None,
    ) -> dict:
        """Structured call. One repair retry when the reply will not parse."""
        msgs = list(messages)
        msgs.append({"role": "system", "content": JSON_INSTRUCTION + schema_hint})
        content, response_id = await self._complete_raw(msgs, role, model, json_mode=True, max_tokens=max_tokens)
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
            content2, response_id2 = await self._complete_raw(
                repair, role, model, json_mode=True, max_tokens=max_tokens
            )
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
            {
                "role": role, "kind": "json", "messages": msgs,
                "response": content, "response_id": response_id, **(extra or {}),
            }
        )
        return parsed

    async def stream(
        self,
        messages: Messages,
        on_delta: Callable[[str], Any],
        role: str = "worker",
        model: str | None = None,
        temperature: float = 0.4,
        on_reset: Callable[[str], Any] | None = None,
        extra: dict | None = None,
    ) -> str:
        """Streaming call. Deltas are handed to `on_delta` as they arrive.

        A retry after a mid-stream drop is the dangerous case: the first
        attempt's tokens have already been forwarded, and the return value is
        only the last attempt's. Screen and answer then disagree, and the screen
        is the one the audience is reading. So before retrying we tell the
        consumer, via `on_reset`, to throw away everything it has been given for
        this call — the dashboard clears the panel, the CLI starts a fresh line —
        and only then do we stream again.
        """
        self._require_configured()
        model = model or self.provider.model
        payload = {"model": model, "messages": list(messages), "temperature": temperature, "stream": True}
        system_sha = system_prompt_sha256(messages)
        last: ProviderError | None = None
        forwarded = False

        async def _maybe(callback, arg):
            if callback is None:
                return
            res = callback(arg)
            if asyncio.iscoroutine(res):
                await res

        async def _rewind(reason: str) -> None:
            nonlocal forwarded
            if forwarded:
                await _maybe(on_reset, reason)
                forwarded = False

        for attempt in range(self.max_retries):
            self.budget.reserve()
            started = time.monotonic()
            request_id = uuid.uuid4().hex
            self.last_request_id = request_id
            chunks: list[str] = []
            usage: dict = {}
            response_id = ""
            status: int | None = None
            try:
                try:
                    async with self._client() as client:
                        async with client.stream(
                            "POST", "/chat/completions", json=payload, headers={"X-Request-Id": request_id}
                        ) as resp:
                            status = resp.status_code
                            if status >= 400:
                                body = await resp.aread()
                                tag = _error_tag_for_status(status)
                                last = ProviderError(tag, status, self._safe_body(body.decode("utf-8", "replace")))
                                self._emit_call(
                                    role, model, started, status, "", {}, True, attempt, request_id, system_sha,
                                    ok=False, error_tag=tag,
                                )
                                if status in _RETRYABLE_STATUS and attempt < self.max_retries - 1:
                                    await _rewind(tag)
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
                                        forwarded = True
                                        await _maybe(on_delta, piece)
                except ProviderError:
                    raise
                except (httpx.TimeoutException, httpx.TransportError) as exc:
                    last = ProviderError(
                        "PROVIDER_UNAVAILABLE", None, f"{type(exc).__name__} streaming from {self.provider.host}"
                    )
                    self._emit_call(
                        role, model, started, None, "", {}, True, attempt, request_id, system_sha,
                        ok=False, error_tag=last.error_tag,
                    )
                    if attempt < self.max_retries - 1:
                        await _rewind(last.error_tag)
                        await self._retry_sleep(attempt)
                        continue
                    raise last from None

                text = "".join(chunks)
                if not text.strip():
                    self._emit_call(
                        role, model, started, status, response_id, usage, True, attempt, request_id, system_sha,
                        ok=False, error_tag="PROVIDER_BAD_RESPONSE",
                    )
                    last = ProviderError("PROVIDER_BAD_RESPONSE", status, "provider streamed an empty completion")
                    if attempt < self.max_retries - 1:
                        await _rewind("PROVIDER_BAD_RESPONSE")
                        await self._retry_sleep(attempt)
                        continue
                    raise last
                if not usage:
                    prompt_chars = sum(len(str(m.get("content", ""))) for m in messages)
                    usage = {"prompt_tokens": prompt_chars // 4, "completion_tokens": len(text) // 4}
                self._emit_call(
                    role, model, started, status, response_id, usage, True, attempt, request_id, system_sha
                )
                self._transcript(
                    {
                        "role": role,
                        "kind": "stream",
                        "messages": list(messages),
                        "response": text,
                        "response_id": response_id,
                        **(extra or {}),
                    }
                )
                return text
            finally:
                self.budget.release()
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
        try:
            # A 200 with no completion in it is not a working provider — it is a
            # gateway answering on the provider's behalf. Reading the body is the
            # difference between "reachable" and "will actually answer".
            body = resp.json()
            choice = (body.get("choices") or [])[0]
        except Exception:
            return (
                False,
                "PROVIDER_BAD_RESPONSE",
                f"probe {resp.status_code} from {self.provider.host} had no completion in it",
            )
        if not isinstance(choice, dict) or "message" not in choice and "delta" not in choice:
            return (
                False,
                "PROVIDER_BAD_RESPONSE",
                f"probe {resp.status_code} from {self.provider.host} returned no message",
            )
        return True, None, f"probe {resp.status_code} from {self.provider.host}"
