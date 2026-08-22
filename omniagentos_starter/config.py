"""Configuration: provider resolution, caps, brand, paths, bind policy.

Nothing in this module ever emits a key value. `ProviderConfig.api_key` is the
only place a secret lives in memory and it is excluded from `redacted_dict()`.
"""

from __future__ import annotations

import ipaddress
import os
from dataclasses import dataclass, field
from pathlib import Path

VERSION = "0.1.0"

PACKAGE_DIR = Path(__file__).resolve().parent
REPO_ROOT = PACKAGE_DIR.parent

# ---------------------------------------------------------------- caps -----
MAX_PLAN_TASKS = 6
MAX_DOD_CRITERIA = 8
MAX_ROUNDS = 3
MAX_LLM_CALLS_PER_RUN = 30
MAX_CONCURRENT_RUNS = 2
MAX_TASK_CONCURRENCY = 3
MAX_GOAL_CHARS = 4000
MAX_ARTIFACT_CHARS = 12000
MAX_EXTRA_DOD = 4
MAX_PLANNER_ADDED_CRITERIA = 3
MAX_LESSON_CHARS = 300
SSE_KEEPALIVE_SECONDS = 15

# Env var names that may hold a provider secret. Order matters: it is the
# provider resolution order.
KEY_ENV_VARS = (
    "OMNIAGENTOS_API_KEY",
    "XAI_API_KEY",
    "OPENROUTER_API_KEY",
    "OPENAI_API_KEY",
)
SECRET_ENV_VARS = KEY_ENV_VARS + ("OMNIAGENTOS_TOKEN",)

ERROR_TAGS = (
    "PROVIDER_NOT_CONFIGURED",
    "PROVIDER_AUTH",
    "PROVIDER_RATE_LIMIT",
    "PROVIDER_UNAVAILABLE",
    "PROVIDER_BAD_RESPONSE",
    "BUDGET_EXCEEDED",
    "ROUNDS_EXHAUSTED",
    "REPAIR_UNLOCALISED",
    "WORKSPACE_ESCAPE",
    "REPLAY_FAILED",
    "RUN_LIMIT",
    "BAD_REQUEST",
    "INTERNAL_ERROR",
)

# rough public list prices, USD per 1M tokens (input, output); used only for the
# receipt strip's "est $" — never billed against.
_PRICES = {
    "grok-4.3": (3.00, 15.00),
    "x-ai/grok-4.3": (3.00, 15.00),
    "gpt-4.1-mini": (0.40, 1.60),
}
_DEFAULT_PRICE = (3.00, 15.00)


def estimate_cost_usd(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    pin, pout = _PRICES.get(model, _DEFAULT_PRICE)
    return round((prompt_tokens / 1_000_000) * pin + (completion_tokens / 1_000_000) * pout, 6)


@dataclass
class ProviderConfig:
    """Which provider we would call, and with what. Never serialised whole."""

    configured: bool
    provider: str
    model: str
    base_url: str
    api_key: str = field(default="", repr=False)
    key_env: str = ""
    error_tag: str | None = None

    @property
    def host(self) -> str:
        rest = self.base_url.split("://", 1)[-1]
        return rest.split("/", 1)[0]

    def redacted_dict(self) -> dict:
        d = {
            "configured": self.configured,
            "provider": self.provider,
            "model": self.model,
            "base_url": self.base_url,
            "key_env": self.key_env,
        }
        if self.error_tag:
            d["error_tag"] = self.error_tag
        return d


def _env(env: dict | None, name: str) -> str:
    src = os.environ if env is None else env
    return (src.get(name) or "").strip()


def resolve_provider(env: dict | None = None) -> ProviderConfig:
    """Resolve the provider purely from the environment.

    OMNIAGENTOS_API_KEY (+OMNIAGENTOS_BASE_URL) overrides everything, then
    XAI_API_KEY, then OPENROUTER_API_KEY, then OPENAI_API_KEY. OMNIAGENTOS_MODEL
    always overrides the model. With no key at all the result is
    ``configured=False`` with error_tag PROVIDER_NOT_CONFIGURED — never an
    exception, so the server still serves the no-key first-run experience.
    """
    model_override = _env(env, "OMNIAGENTOS_MODEL")
    # An explicit base URL wins for whichever key is in play — pointing the client
    # at a gateway must never silently fall through to the vendor's own endpoint.
    base_override = _env(env, "OMNIAGENTOS_BASE_URL")

    candidates = [
        (
            "OMNIAGENTOS_API_KEY",
            "custom",
            base_override or "https://api.x.ai/v1",
            "grok-4.3",
        ),
        ("XAI_API_KEY", "xai", "https://api.x.ai/v1", "grok-4.3"),
        ("OPENROUTER_API_KEY", "openrouter", "https://openrouter.ai/api/v1", "x-ai/grok-4.3"),
        ("OPENAI_API_KEY", "openai", "https://api.openai.com/v1", "gpt-4.1-mini"),
    ]
    for var, provider, base_url, model in candidates:
        key = _env(env, var)
        if key:
            return ProviderConfig(
                configured=True,
                provider=provider,
                model=model_override or model,
                base_url=(base_override or base_url).rstrip("/"),
                api_key=key,
                key_env=var,
            )
    return ProviderConfig(
        configured=False,
        provider="none",
        model=model_override or "",
        base_url="",
        api_key="",
        key_env="",
        error_tag="PROVIDER_NOT_CONFIGURED",
    )


@dataclass
class Brand:
    name: str
    logo_url: str

    def as_dict(self) -> dict:
        return {"name": self.name, "logo_url": self.logo_url}


def resolve_brand(env: dict | None = None) -> Brand:
    return Brand(
        name=_env(env, "OMNIAGENTOS_BRAND_NAME") or "OmniRogue",
        logo_url=_env(env, "OMNIAGENTOS_BRAND_LOGO") or "/assets/omnirogue-logo.png",
    )


def _first_existing(*paths: Path) -> Path | None:
    for p in paths:
        if p and p.is_dir():
            return p
    return None


def assets_dir(env: dict | None = None) -> Path:
    """Directory served at /assets/. Repo checkout first, package copy second."""
    override = _env(env, "OMNIAGENTOS_ASSETS_DIR")
    if override:
        return Path(override).expanduser().resolve()
    found = _first_existing(Path.cwd() / "assets", REPO_ROOT / "assets", PACKAGE_DIR / "assets")
    return found or (REPO_ROOT / "assets")


def skills_dir(env: dict | None = None) -> Path:
    """Directory scanned for skill packs. A pure directory scan — no literals."""
    override = _env(env, "OMNIAGENTOS_SKILLS_ROOT") or _env(env, "OMNIAGENTOS_SKILLS_DIR")
    if override:
        return Path(override).expanduser().resolve()
    found = _first_existing(Path.cwd() / "skills", REPO_ROOT / "skills")
    return found or (REPO_ROOT / "skills")


def static_dir() -> Path:
    return PACKAGE_DIR / "static"


def builtin_skills_dir() -> Path:
    return PACKAGE_DIR / "builtin_skills"


def replay_path() -> Path:
    return PACKAGE_DIR / "data" / "replay-run.json"


class BindRefused(Exception):
    """Raised when a non-loopback bind is attempted without a token."""


# The wildcard forms. An empty host is the trap: `socket.bind(("", port))` and
# uvicorn `host=""` both mean EVERY interface, so classifying "" as loopback
# hands the LAN a live provider key with no token in front of it.
UNSPECIFIED_HOSTS = {"", "*", "0.0.0.0", "::", "[::]", "0", "::0", "0.0.0.0.0"}


def _is_loopback(host: str) -> bool:
    h = (host or "").strip().lower().strip("[]")
    if h in {"localhost", "localhost.localdomain"}:
        return True
    if h in UNSPECIFIED_HOSTS or (host or "").strip() in UNSPECIFIED_HOSTS:
        return False
    try:
        addr = ipaddress.ip_address(h)
    except ValueError:
        return False
    return addr.is_loopback and not addr.is_unspecified


def validate_bind(host: str, env: dict | None = None, token: str = "") -> None:
    """Fail closed: exposing the API off-loopback requires OMNIAGENTOS_TOKEN."""
    if _is_loopback(host):
        return
    if not (token or "").strip() and not _env(env, "OMNIAGENTOS_TOKEN"):
        raise BindRefused(
            f"refusing to bind {host!r}: set OMNIAGENTOS_TOKEN to expose the API off-loopback "
            "(every /api/* request must then carry Authorization: Bearer <token>)"
        )


@dataclass
class Settings:
    """Everything the server needs, resolved once at startup."""

    host: str = "127.0.0.1"
    port: int = 8486
    data_dir: Path = field(default_factory=lambda: Path.cwd() / "var")
    workspace_dir: Path = field(default_factory=lambda: Path.cwd() / "workspace")
    provider: ProviderConfig = field(default_factory=resolve_provider)
    brand: Brand = field(default_factory=resolve_brand)
    token: str = ""
    max_rounds: int = MAX_ROUNDS

    def __post_init__(self) -> None:
        # from_env() is not the only way a Settings gets built — create_app() and
        # every test fixture construct one directly, and each of those bypassed
        # the bind policy entirely. The check belongs to the object, not to one
        # constructor.
        self.host = (self.host or "").strip()
        validate_bind(self.host, token=self.token)

    @classmethod
    def from_env(
        cls,
        env: dict | None = None,
        host: str = "127.0.0.1",
        port: int = 8486,
        data_dir: str | Path | None = None,
    ) -> Settings:
        token = _env(env, "OMNIAGENTOS_TOKEN")
        validate_bind(host, env, token=token)
        data = Path(data_dir).expanduser().resolve() if data_dir else (Path.cwd() / "var").resolve()
        return cls(
            host=host,
            port=port,
            data_dir=data,
            workspace_dir=(Path.cwd() / "workspace").resolve(),
            provider=resolve_provider(env),
            brand=resolve_brand(env),
            token=token,
        )
