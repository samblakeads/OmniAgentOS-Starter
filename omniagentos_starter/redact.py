"""Redaction — the last thing every string passes through before it leaves.

Every log line, every SSE event payload, every API error body and every
persisted prompt transcript goes through :func:`redact`. Provider exceptions are
never stringified raw; they are converted into a :class:`ProviderError` carrying
an ``error_tag`` and an already-redacted safe message.
"""

from __future__ import annotations

import json as _json
import os
import re
from pathlib import Path

from .config import SECRET_ENV_VARS

PLACEHOLDER = "[REDACTED]"

# Shape-based patterns: catch secrets we were never told about (a key pasted
# into a goal, a token echoed by an upstream error body, an Authorization header
# captured in a traceback).
_SHAPE_PATTERNS = [
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._\-~+/=]{8,}"),
    re.compile(r"\b(?:sk|xai|or|gsk|pk)-[A-Za-z0-9._\-]{12,}"),
    re.compile(r"(?i)\b(?:api[_-]?key|authorization|access[_-]?token)\"?\s*[:=]\s*\"?[A-Za-z0-9._\-]{12,}"),
]

# Secrets registered at runtime (e.g. a key handed to a client directly).
# Values are never logged, only matched.
_REGISTERED: set[str] = set()

# Short enough that a hand-typed OMNIAGENTOS_TOKEN is still substituted. A token
# nobody redacts because it is "too short to be a secret" is still a token.
_MIN_SECRET_LEN = 4


def register_secret(value: str | None) -> None:
    """Teach the redactor about a secret it cannot find in the environment."""
    if value and len(value.strip()) >= _MIN_SECRET_LEN:
        _REGISTERED.add(value.strip())


def clear_registered_secrets() -> None:
    _REGISTERED.clear()


def _known_secrets() -> list[str]:
    values = set(_REGISTERED)
    for name in SECRET_ENV_VARS:
        v = (os.environ.get(name) or "").strip()
        if len(v) >= _MIN_SECRET_LEN:
            values.add(v)
    # longest first so a prefix never masks the longer match
    return sorted(values, key=len, reverse=True)


def redact_text(text: str) -> str:
    out = text
    for secret in _known_secrets():
        if secret in out:
            out = out.replace(secret, PLACEHOLDER)
    for pat in _SHAPE_PATTERNS:
        out = pat.sub(PLACEHOLDER, out)
    return out


def redact(value):
    """Redact a str / dict / list / tuple recursively. Other types pass through.

    Dict keys are redacted too: a secret can end up as a key in a parsed body.
    ``Path`` and ``bytes`` are coerced to redacted strings rather than passed
    through, because ``json.dumps(..., default=str)`` downstream would stringify
    them *after* the last redaction ran — which is how an unredacted value leaves
    a process that believes it redacts everything.
    """
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, Path):
        return redact_text(str(value))
    if isinstance(value, (bytes, bytearray)):
        return redact_text(bytes(value).decode("utf-8", "replace"))
    if isinstance(value, dict):
        return {redact(k) if isinstance(k, str) else k: redact(v) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value]
    if isinstance(value, tuple):
        return tuple(redact(v) for v in value)
    return value


def contains_secret(value) -> bool:
    """True if a secret survives in `value` — used by tests and by receipts.

    Both halves of the redactor are applied: the registered/env values AND the
    shape patterns. A receipt gate that only knew the named secrets happily
    published a bearer token that arrived on the command line.
    """
    blob = value if isinstance(value, str) else _json.dumps(value, default=str)
    if any(secret in blob for secret in _known_secrets()):
        return True
    return any(pat.search(blob) for pat in _SHAPE_PATTERNS)


class ProviderError(Exception):
    """A provider failure that is safe to show a user.

    ``error_tag`` is one of config.ERROR_TAGS; ``safe_message`` is already
    redacted. The original exception is deliberately not retained.
    """

    def __init__(self, error_tag: str, status: int | None = None, safe_message: str = ""):
        self.error_tag = error_tag
        self.status = status
        self.safe_message = redact_text(safe_message or error_tag)[:400]
        super().__init__(f"{error_tag}: {self.safe_message}")

    def as_dict(self) -> dict:
        return {"error_tag": self.error_tag, "status": self.status, "message": self.safe_message}


class WorkspaceEscape(Exception):
    """A tool tried to touch something outside its run workspace."""

    error_tag = "WORKSPACE_ESCAPE"

    def __init__(self, requested: str, reason: str):
        self.requested = redact_text(str(requested))[:200]
        self.reason = reason
        super().__init__(f"WORKSPACE_ESCAPE: {reason} ({self.requested})")

    def as_dict(self) -> dict:
        return {"error_tag": self.error_tag, "reason": self.reason, "requested": self.requested}
