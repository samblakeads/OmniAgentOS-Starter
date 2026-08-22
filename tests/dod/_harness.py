"""Shared DoD oracle harness for OmniAgentOS Starter.

Coded to PLAN.md / U0 contract, not to implementer source. Never prints key values.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import select
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
_PW_BROWSERS = REPO_ROOT / ".venv" / "ms-playwright"
os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(_PW_BROWSERS))
EVIDENCE_DIR = Path(
    os.environ.get(
        "OMNIAGENTOS_DOD_EVIDENCE",
        str(REPO_ROOT / "devtasks" / "gomax-omniagentos-lite-0821" / "evidence"),
    )
)

LISTENING_RE = re.compile(r"^LISTENING port=(\d+)\s*$")
PLACEHOLDER_DELIVERABLES = {
    "",
    "Ready.",
    "—",
    "Your deliverable will appear here",
    "TODO",
    "lorem ipsum",
}
RECEIPT_REQUIRED_KEYS = (
    "magic",
    "git_head",
    "argv",
    "health_json",
    "run_id",
    "status",
    "provider_http_status",
    "t_first_event_ms",
    "t_done_ms",
    "goal",
    "deliverable_sha256",
)
RECEIPT_MAGIC = "OMNIAGENTOS-RECEIPT-1"
XAI_ORIGIN = "https://api.x.ai/v1"
XAI_HOST = "api.x.ai"
PLANTED_D10_KEY = "secret_TESTKEY_d10_7f3a9c2e"

ERROR_TAGS = {
    "PROVIDER_NOT_CONFIGURED",
    "PROVIDER_AUTH",
    "PROVIDER_RATE_LIMIT",
    "PROVIDER_UNAVAILABLE",
    "PROVIDER_BAD_RESPONSE",
    "BUDGET_EXCEEDED",
    "WORKSPACE_ESCAPE",
    "REPAIR_UNLOCALISED",
    "ROUNDS_EXHAUSTED",
}


def repo_head_sha() -> str:
    out = subprocess.check_output(
        ["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"],
        text=True,
    )
    return out.strip()


def evidence_path(*parts: str) -> Path:
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    p = EVIDENCE_DIR.joinpath(*parts)
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def write_json(name: str, payload: Any) -> Path:
    path = evidence_path(name)
    path.write_text(json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8")
    return path


def xai_key() -> str | None:
    """Read XAI_API_KEY from the environment. Never print the value."""
    val = os.environ.get("XAI_API_KEY")
    if val is None:
        return None
    stripped = val.strip()
    return stripped or None


def require_live() -> str:
    """Proceed / skip / fail based on XAI_API_KEY and OMNIAGENTOS_DOD_REQUIRE_LIVE.

    If the key is set, proceed (never skip). If unset and REQUIRE_LIVE is set,
    pytest.fail (loud). If unset and REQUIRE_LIVE is unset, pytest.skip.
    """
    key = xai_key()
    if key:
        return key
    if os.environ.get("OMNIAGENTOS_DOD_REQUIRE_LIVE"):
        pytest.fail(
            "OMNIAGENTOS_DOD_REQUIRE_LIVE is set but XAI_API_KEY is unset "
            "(source ~/.config/omni/connections.env before pytest)"
        )
    pytest.skip("XAI_API_KEY unset; live-boundary test skipped")
    raise AssertionError("unreachable")


def live_xai_base_url_ok() -> bool:
    raw = os.environ.get("OMNIAGENTOS_BASE_URL")
    if raw is None or raw.strip() == "":
        return True
    return raw.strip().rstrip("/") == XAI_ORIGIN


def omniagentos_argv() -> list[str]:
    """Resolve the CLI. Console script preferred; python -m omniagentos_starter is equivalent.

    BINDING: pyproject must install console script `omniagentos` pointing at
    omniagentos_starter.cli, AND `python -m omniagentos_starter` must work
    (package __main__.py).
    """
    venv_cli = REPO_ROOT / ".venv" / "bin" / "omniagentos"
    if venv_cli.is_file() and os.access(venv_cli, os.X_OK):
        return [str(venv_cli)]
    which = shutil.which("omniagentos")
    if which:
        return [which]
    py = REPO_ROOT / ".venv" / "bin" / "python"
    exe = str(py) if py.is_file() else sys.executable
    return [exe, "-u", "-m", "omniagentos_starter"]


class ServerProc:
    def __init__(self, proc: subprocess.Popen[str], port: int, base_url: str, data_dir: Path):
        self.proc = proc
        self.port = port
        self.base_url = base_url
        self.data_dir = data_dir

    @property
    def pid(self) -> int:
        return int(self.proc.pid)

    def stop(self) -> None:
        if self.proc.poll() is not None:
            return
        self.proc.send_signal(signal.SIGTERM)
        try:
            self.proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait(timeout=5)


def spawn_serve(
    data_dir: str | Path | None = None,
    extra_env: dict[str, str | None] | None = None,
    extra_args: list[str] | None = None,
    timeout_s: float = 30.0,
    clear_provider_keys: bool = False,
) -> ServerProc:
    """Spawn `omniagentos serve --host 127.0.0.1 --port 0 --data-dir <tmp>`.

    Reads stdout line-by-line until `LISTENING port=<n>` (exact). Never guesses a port.
    """
    tmp_owned = False
    if data_dir is None:
        data_dir = Path(tempfile.mkdtemp(prefix="omniagentos-dod-"))
        tmp_owned = True
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)

    cmd = omniagentos_argv() + [
        "serve",
        "--host",
        "127.0.0.1",
        "--port",
        "0",
        "--data-dir",
        str(data_dir),
    ]
    if extra_args:
        cmd.extend(extra_args)

    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    if str(REPO_ROOT) not in env.get("PYTHONPATH", ""):
        env["PYTHONPATH"] = (
            str(REPO_ROOT) + os.pathsep + env["PYTHONPATH"]
            if env.get("PYTHONPATH")
            else str(REPO_ROOT)
        )
    if clear_provider_keys:
        for k in (
            "XAI_API_KEY",
            "OPENROUTER_API_KEY",
            "OPENAI_API_KEY",
            "OMNIAGENTOS_API_KEY",
            "OMNIAGENTOS_BASE_URL",
        ):
            env.pop(k, None)
    if extra_env:
        for k, v in extra_env.items():
            if v is None:
                env.pop(k, None)
            else:
                env[k] = v

    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(REPO_ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            env=env,
        )
    except FileNotFoundError as exc:
        raise AssertionError(
            f"failed to spawn omniagentos CLI ({cmd!r}): {exc}. "
            "Package omniagentos_starter must install console script `omniagentos`."
        ) from exc

    assert proc.stdout is not None
    deadline = time.time() + timeout_s
    lines: list[str] = []
    port: int | None = None
    fd = proc.stdout.fileno()
    try:
        while time.time() < deadline:
            if proc.poll() is not None:
                rest = proc.stdout.read() or ""
                lines.append(rest)
                raise AssertionError(
                    "omniagentos serve exited before LISTENING "
                    f"(code={proc.returncode}). output:\n{''.join(lines)[-4000:]}"
                )
            remaining = max(0.05, deadline - time.time())
            ready, _, _ = select.select([fd], [], [], min(0.5, remaining))
            if not ready:
                continue
            line = proc.stdout.readline()
            if line == "":
                time.sleep(0.05)
                continue
            lines.append(line)
            m = LISTENING_RE.match(line.rstrip("\n"))
            if m:
                port = int(m.group(1))
                break
        if port is None:
            proc.kill()
            raise AssertionError(
                "timed out waiting for stdout line `LISTENING port=<n>` "
                f"from {cmd!r}. captured:\n{''.join(lines)[-4000:]}"
            )
        if port <= 0 or port > 65535:
            proc.kill()
            raise AssertionError(f"invalid LISTENING port={port}")
        base = f"http://127.0.0.1:{port}"
        _wait_http(base + "/api/health", timeout_s=max(5.0, timeout_s / 3))
        return ServerProc(proc, port, base, data_dir)
    except Exception:
        if proc.poll() is None:
            proc.kill()
            try:
                proc.wait(timeout=3)
            except Exception:
                pass
        if tmp_owned:
            shutil.rmtree(data_dir, ignore_errors=True)
        raise


def _wait_http(url: str, timeout_s: float = 10.0) -> None:
    deadline = time.time() + timeout_s
    last = None
    while time.time() < deadline:
        try:
            r = httpx.get(url, timeout=2.0)
            if r.status_code < 500:
                return
            last = f"HTTP {r.status_code}"
        except Exception as exc:
            last = str(exc)
        time.sleep(0.1)
    raise AssertionError(f"server bound but {url} never answered: {last}")


def get_json(base_url: str, path: str, **kwargs: Any) -> httpx.Response:
    return httpx.get(base_url + path, timeout=kwargs.pop("timeout", 15.0), **kwargs)


def post_json(base_url: str, path: str, body: dict[str, Any], **kwargs: Any) -> httpx.Response:
    return httpx.post(
        base_url + path,
        json=body,
        timeout=kwargs.pop("timeout", 30.0),
        **kwargs,
    )


def check_sse_headers(base_url: str, run_id: str, read_timeout_s: float = 15.0) -> dict[str, str]:
    """Verify the SSE response line/headers WITHOUT buffering the full body.

    BINDING: a plain `httpx.get(.../events)` buffers the entire streamed
    response before returning, so on a real (tens-of-seconds) run it either
    times out well before completion or forces an unreasonably long request
    timeout. This opens the connection with `httpx.stream`, reads only the
    first chunk (enough to prove the server is actually streaming, not
    buffering server-side either), asserts status/Content-Type/Cache-Control/
    X-Accel-Buffering, then closes the connection immediately — the caller
    never waits for run completion just to check headers.
    """
    url = f"{base_url}/api/runs/{run_id}/events"
    headers = {"Accept": "text/event-stream", "Cache-Control": "no-cache"}
    with httpx.stream("GET", url, headers=headers, timeout=read_timeout_s) as resp:
        if resp.status_code != 200:
            body = resp.read().decode("utf-8", "replace")
            raise AssertionError(f"SSE GET {url} -> HTTP {resp.status_code}: {body[:500]}")
        ctype = resp.headers.get("content-type", "")
        if "text/event-stream" not in ctype.lower():
            raise AssertionError(f"SSE Content-Type must be text/event-stream, got {ctype!r}")
        cc = resp.headers.get("cache-control", "")
        if "no-cache" not in cc.lower():
            raise AssertionError(f"SSE Cache-Control must include no-cache, got {cc!r}")
        xab = resp.headers.get("x-accel-buffering", "")
        if xab.lower() != "no":
            raise AssertionError(f"SSE X-Accel-Buffering must be 'no', got {xab!r}")
        # Prove bytes are actually flowing (not server-buffered) without
        # waiting for the stream to finish.
        got_bytes = False
        for chunk in resp.iter_bytes():
            if chunk:
                got_bytes = True
            break
        if not got_bytes:
            raise AssertionError("SSE stream produced no bytes before closing")
        return dict(resp.headers)


def event_type(rec: dict[str, Any]) -> str:
    ev = rec.get("event") or ""
    data = rec.get("data")
    if isinstance(data, dict) and data.get("type"):
        if ev and ev not in ("message", "error"):
            return str(ev)
        return str(data["type"])
    return str(ev)


def event_payload(rec: dict[str, Any]) -> dict[str, Any]:
    data = rec.get("data")
    return data if isinstance(data, dict) else {}


def iter_sse(
    base_url: str,
    run_id: str,
    last_event_id: str | int | None = None,
    timeout_s: float = 180.0,
    stop_events: tuple[str, ...] = ("run.done", "run.failed"),
) -> Iterator[dict[str, Any]]:
    """Yield parsed {event, id, data} records. Honors Last-Event-ID.

    BINDING SSE shape (each event separated by a blank line):

        id: <monotonic integer>
        event: <type>
        data: <json object, may be split across multiple data: lines>

    JSON MUST include `"type"` equal to the SSE `event:` field, and `"ts"`
    (epoch seconds float or ISO-8601). Keepalive comments (`: ...`) are ignored.
    """
    headers = {
        "Accept": "text/event-stream",
        "Cache-Control": "no-cache",
    }
    if last_event_id is not None:
        headers["Last-Event-ID"] = str(last_event_id)
    url = f"{base_url}/api/runs/{run_id}/events"
    with httpx.Client(timeout=None) as client:
        with client.stream("GET", url, headers=headers) as resp:
            if resp.status_code != 200:
                body = resp.read().decode("utf-8", "replace")
                raise AssertionError(
                    f"SSE GET {url} -> HTTP {resp.status_code}: {body[:500]}"
                )
            cc = resp.headers.get("cache-control", "")
            if "no-cache" not in cc.lower():
                raise AssertionError(
                    f"SSE Cache-Control must include no-cache, got {cc!r}"
                )
            xab = resp.headers.get("x-accel-buffering", "")
            if xab.lower() != "no":
                raise AssertionError(
                    f"SSE X-Accel-Buffering must be 'no', got {xab!r}"
                )
            yield from _parse_sse_lines(resp.iter_lines(), timeout_s, stop_events)


def _parse_sse_lines(
    lines: Iterator[str],
    timeout_s: float,
    stop_events: tuple[str, ...],
) -> Iterator[dict[str, Any]]:
    deadline = time.time() + timeout_s
    buf_event = ""
    buf_id = ""
    buf_data: list[str] = []
    for line in lines:
        if time.time() > deadline:
            raise AssertionError(f"SSE timed out after {timeout_s}s")
        if line is None:
            continue
        if line.startswith(":"):
            continue
        if line == "":
            rec = _flush_sse(buf_event, buf_id, buf_data)
            buf_event, buf_id, buf_data = "", "", []
            if rec is None:
                continue
            yield rec
            if event_type(rec) in stop_events:
                return
            continue
        if line.startswith("event:"):
            buf_event = line[6:].lstrip()
        elif line.startswith("id:"):
            buf_id = line[3:].lstrip()
        elif line.startswith("data:"):
            buf_data.append(line[5:].lstrip() if line[5:].startswith(" ") else line[5:])
        else:
            continue
    rec = _flush_sse(buf_event, buf_id, buf_data)
    if rec is not None:
        yield rec


def _flush_sse(event: str, eid: str, data_lines: list[str]) -> dict[str, Any] | None:
    if not event and not eid and not data_lines:
        return None
    raw = "\n".join(data_lines)
    parsed: Any = raw
    if raw:
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = raw
    return {"event": event, "id": eid, "data": parsed, "raw": raw}


def collect_sse(
    base_url: str,
    run_id: str,
    timeout_s: float = 180.0,
) -> list[dict[str, Any]]:
    return list(iter_sse(base_url, run_id, timeout_s=timeout_s))


def start_run(
    base_url: str,
    goal: str,
    extra_dod: list[dict[str, Any]] | None = None,
    max_rounds: int | None = None,
    agent_id: str | None = None,
) -> str:
    body: dict[str, Any] = {"goal": goal}
    if extra_dod is not None:
        body["extra_dod"] = extra_dod
    if max_rounds is not None:
        body["max_rounds"] = max_rounds
    if agent_id is not None:
        body["agent_id"] = agent_id
    resp = post_json(base_url, "/api/runs", body)
    if resp.status_code == 429:
        raise AssertionError("POST /api/runs returned 429 MAX_CONCURRENT_RUNS busy")
    if resp.status_code not in (200, 201):
        raise AssertionError(
            f"POST /api/runs -> HTTP {resp.status_code}: {resp.text[:800]}"
        )
    payload = resp.json()
    rid = payload.get("id") or payload.get("run_id")
    if not rid:
        raise AssertionError(f"POST /api/runs missing id/run_id: {payload!r}")
    return str(rid)


def get_run(base_url: str, run_id: str) -> dict[str, Any]:
    resp = get_json(base_url, f"/api/runs/{run_id}")
    if resp.status_code != 200:
        raise AssertionError(
            f"GET /api/runs/{run_id} -> HTTP {resp.status_code}: {resp.text[:500]}"
        )
    data = resp.json()
    if not isinstance(data, dict):
        raise AssertionError(f"GET /api/runs/{{id}} must return an object, got {data!r}")
    return data


def assert_status_present(run: dict[str, Any], expected: str | None = None) -> str:
    """Absent status is a failure — never default to done (F1/F3)."""
    if "status" not in run:
        raise AssertionError("run JSON missing required field 'status' (must not default)")
    status = run["status"]
    if status in (None, ""):
        raise AssertionError("run.status present but empty/null — treat as failed, not done")
    if expected is not None and status != expected:
        raise AssertionError(f"run.status={status!r}, expected {expected!r}")
    return str(status)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def git_head_matches(receipt_head: str) -> None:
    actual = repo_head_sha()
    if receipt_head != actual:
        raise AssertionError(
            f"receipt.git_head={receipt_head!r} != git rev-parse HEAD {actual!r} "
            "(mtime/existence is not proof)"
        )


def validate_receipt(receipt: dict[str, Any] | str | Path) -> dict[str, Any]:
    """Validate OMNIAGENTOS-RECEIPT-1 schema and git_head == actual HEAD."""
    if isinstance(receipt, (str, Path)):
        path = Path(receipt)
        if not path.is_file():
            raise AssertionError(f"receipt file missing: {path}")
        try:
            receipt = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise AssertionError(f"receipt is not JSON: {exc}") from exc
    if not isinstance(receipt, dict):
        raise AssertionError(f"receipt must be an object, got {type(receipt)}")
    missing = [k for k in RECEIPT_REQUIRED_KEYS if k not in receipt]
    if missing:
        raise AssertionError(f"receipt missing keys {missing}")
    if receipt["magic"] != RECEIPT_MAGIC:
        raise AssertionError(
            f"receipt.magic={receipt['magic']!r} != {RECEIPT_MAGIC!r}"
        )
    git_head_matches(str(receipt["git_head"]))
    if not isinstance(receipt["argv"], (list, tuple)) or not receipt["argv"]:
        raise AssertionError("receipt.argv must be a non-empty list")
    if not isinstance(receipt["health_json"], dict):
        raise AssertionError("receipt.health_json must be an object")
    if not receipt["run_id"]:
        raise AssertionError("receipt.run_id empty")
    if not isinstance(receipt["provider_http_status"], list):
        raise AssertionError("receipt.provider_http_status must be a list")
    for k in ("t_first_event_ms", "t_done_ms"):
        v = receipt[k]
        if not isinstance(v, (int, float)):
            raise AssertionError(f"receipt.{k} must be a number, got {v!r}")
    if not isinstance(receipt["goal"], str) or not receipt["goal"].strip():
        raise AssertionError("receipt.goal must be a non-empty string")
    sha = receipt["deliverable_sha256"]
    if not isinstance(sha, str) or len(sha) != 64 or any(c not in "0123456789abcdef" for c in sha.lower()):
        raise AssertionError(f"receipt.deliverable_sha256 is not a sha256 hex: {sha!r}")
    return receipt


_DOD_SUFFIX_RE = re.compile(r"\s*\|\|\|\s*dod:\s*(.+?)\s*(?=\s*\|\|\|\s*agent:|$)", re.I)
_AGENT_SUFFIX_RE = re.compile(r"\s*\|\|\|\s*agent:\s*(\S+)\s*$", re.I)


def _split_dod_suffix(line: str) -> tuple[str, list[str]]:
    """Split a trailing ` ||| dod: <criterion>` off a dod-goals fence line.

    BINDING separator (implementers/U2 must match exactly): a fenced
    ```dod-goals line may end with literal ` ||| dod: <criterion text>`.
    Everything before the separator is the goal; everything after is one
    critic/verifier-only rubric criterion posted as `extra_dod`. A line with
    no separator carries no extra_dod (empty list). FIXED ORDER when both
    suffixes are present on one line: ` ||| dod: <criterion> ||| agent:
    <slug>` — dod always precedes agent. The trailing agent segment is
    stripped first so the dod regex only has to match up to end-of-string.
    """
    goal, _agent = _split_agent_suffix(line)
    m = _DOD_SUFFIX_RE.search(goal)
    if not m:
        return goal.strip(), []
    stripped_goal = goal[: m.start()].strip()
    return stripped_goal, [m.group(1).strip()]


def _split_agent_suffix(line: str) -> tuple[str, str | None]:
    """Split a trailing ` ||| agent: <slug>` off a dod-goals fence line.

    BINDING separator (Round 6 pin, implementers/U2 must match exactly): a
    fenced ```dod-goals line may end with literal ` ||| agent: <slug>` —
    the DEMO beat's goal is assigned to that agent (POST /api/runs
    {..., agent_id: <slug>}) instead of the router. May appear together
    with ` ||| dod: <criterion>` on the same line, in either order. A line
    with no `||| agent:` segment carries no agent assignment (None).
    """
    m = _AGENT_SUFFIX_RE.search(line)
    if not m:
        return line, None
    return line[: m.start()], m.group(1).strip()


def parse_demo_goals(demo_md: Path | None = None) -> list[str]:
    """Parse exactly 3 literal goal strings from DEMO.md.

    BINDING (implementers/U2 must match): DEMO.md at repo root contains either

    1. a fenced block tagged dod-goals with exactly three non-empty lines
       (each optionally suffixed ` ||| dod: <criterion>` — stripped here; see
       parse_demo_goals_with_dod() for the paired form), or
    2. three lines of the form `GOAL: <literal>` (optionally `GOAL 1:` etc.), or
    3. three headings/lines `Goal 1:` / `Goal 2:` / `Goal 3:` followed by the
       literal text on the rest of the line or the following quoted line.

    Quoted wrapping (`"..."` or `«...»`) is stripped. No other prose is a goal.
    """
    return [g for g, _ in parse_demo_goals_with_dod(demo_md)]


def parse_demo_goals_with_dod(
    demo_md: Path | None = None,
) -> list[tuple[str, list[str]]]:
    """Like parse_demo_goals() but returns (goal, [extra_dod_criterion, ...]) pairs.

    Only the fenced ```dod-goals form carries the ` ||| dod: <criterion>`
    suffix; the GOAL:/Goal N: forms never carry extra_dod (empty list).
    """
    return [(g, dod) for g, dod, _agent in parse_demo_goals_full(demo_md)]


def parse_demo_goals_full(
    demo_md: Path | None = None,
) -> list[tuple[str, list[str], str | None]]:
    """(goal, [extra_dod_criterion, ...], agent_slug_or_None) triples.

    Only the fenced ```dod-goals form carries either the ` ||| dod:
    <criterion>` or ` ||| agent: <slug>` suffix (Round 6 pin); the
    GOAL:/Goal N: forms never carry either (empty dod list, agent=None).
    """
    path = demo_md or (REPO_ROOT / "DEMO.md")
    if not path.is_file():
        raise AssertionError(f"DEMO.md missing at {path}")
    text = path.read_text(encoding="utf-8")

    fenced = re.search(r"```dod-goals\s*\n(.*?)```", text, re.S | re.I)
    if fenced:
        lines = [ln.strip() for ln in fenced.group(1).splitlines() if ln.strip()]
        if len(lines) != 3:
            raise AssertionError(
                f"dod-goals fence must contain exactly 3 goals, got {len(lines)}"
            )
        triples: list[tuple[str, list[str], str | None]] = []
        for ln in lines:
            goal_and_dod, agent = _split_agent_suffix(ln)
            goal, dod = _split_dod_suffix(goal_and_dod)
            triples.append((_unquote(goal), dod, agent))
        return triples

    labeled = re.findall(r"^GOAL(?:\s*[1-3])?:\s*(.+?)\s*$", text, re.M | re.I)
    if len(labeled) >= 3:
        return [(_unquote(g), [], None) for g in labeled[:3]]

    numbered: list[str] = []
    for n in (1, 2, 3):
        m = re.search(
            rf"^(?:#+\s*)?Goal\s*{n}\s*[:.—-]\s*(.+?)\s*$",
            text,
            re.M | re.I,
        )
        if not m:
            break
        numbered.append(_unquote(m.group(1)))
    if len(numbered) == 3:
        return [(g, [], None) for g in numbered]

    raise AssertionError(
        "DEMO.md does not contain 3 parseable literal goals. "
        "Add a ```dod-goals fence with three lines, or `GOAL: ...` lines."
    )


def check_planted_criterion(deliverable: str, criterion: str) -> bool:
    """Best-effort MECHANICAL re-check of a planted extra_dod criterion.

    Not a substitute for the verifier — this is the oracle independently
    re-deriving the same judgement the criterion states, so a verifier that
    rubber-stamps `verified=True` without actually satisfying the planted
    rubric still fails D9. Supports the two literal forms PLAN.md/D4 use:
    - an exact phrase requirement: `exact phrase 'X'` / `contains 'X'`
      (quotes: ' " smart-quotes); checked case-sensitively as stated.
    - a word-count ceiling: `under N words` / `fewer than N words` /
      `less than N words` / `<= N words` / `at most N words`.
    Any additional clause type in the criterion is not mechanically checkable
    here and is left to the verifier; returns True only for clauses this
    function can itself verify (never a vacuous pass on unparseable text —
    callers must additionally confirm the criterion was actually planted).
    """
    ok = True
    checked_any = False

    for m in re.finditer(
        r"(?:exact phrase|contains(?:\s+the)?(?:\s+exact)?\s+phrase|contains)\s*"
        r"['\"“”](.+?)['\"“”]",
        criterion,
        re.I,
    ):
        checked_any = True
        phrase = m.group(1)
        if phrase not in deliverable:
            ok = False

    m = re.search(
        r"(?:under|fewer than|less than|at most|<=)\s*(\d+)\s*words",
        criterion,
        re.I,
    )
    if m:
        checked_any = True
        limit = int(m.group(1))
        if len(deliverable.split()) >= limit:
            ok = False

    if not checked_any:
        return False
    return ok


def _unquote(s: str) -> str:
    s = s.strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "\"'“”":
        return s[1:-1].strip()
    return s


def load_prompts(data_dir: Path, run_id: str) -> str:
    """Return concatenated prompt transcript for a run.

    BINDING path: <data-dir>/runs/<run_id>/prompts.jsonl
    Each line is JSON with at least one of: messages, system, prompt, content.
    Redacted. Used to prove skill-sha256 injection and recalled-lesson verbatim text.
    """
    path = Path(data_dir) / "runs" / str(run_id) / "prompts.jsonl"
    if not path.is_file():
        alt = Path(data_dir) / "var" / "runs" / str(run_id) / "prompts.jsonl"
        path = alt if alt.is_file() else path
    if not path.is_file():
        raise AssertionError(
            f"prompt transcript missing at {path} "
            "(engine must persist redacted prompts.jsonl per run)"
        )
    return path.read_text(encoding="utf-8")


def recalled_lesson_ids(rec: dict[str, Any]) -> list[str]:
    data = event_payload(rec)
    if "lesson_ids" in data and isinstance(data["lesson_ids"], list):
        return [str(x) for x in data["lesson_ids"]]
    lessons = data.get("lessons") or []
    ids = []
    for item in lessons:
        if isinstance(item, dict) and "id" in item:
            ids.append(str(item["id"]))
        else:
            ids.append(str(item))
    return ids


def assert_no_key_leak(blob: str, key: str, where: str) -> None:
    if not key:
        raise AssertionError(f"planted key empty while grepping {where}")
    if key in blob:
        raise AssertionError(f"planted key leaked in {where}")
    # also any 8+ char fragment of the key
    if len(key) >= 12:
        mid = key[2:-2]
        if mid and mid in blob:
            raise AssertionError(f"key fragment leaked in {where}")


def provider_host_of(call: dict[str, Any]) -> str:
    host = call.get("provider_host") or ""
    if not host and call.get("url"):
        host = urlparse(str(call["url"])).hostname or ""
    return str(host).replace("https://", "").replace("http://", "").split("/")[0]


def llm_calls(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for rec in events:
        if event_type(rec) == "llm.call":
            out.append(event_payload(rec))
    return out


def events_of(events: list[dict[str, Any]], typ: str) -> list[dict[str, Any]]:
    return [rec for rec in events if event_type(rec) == typ]


def ts_of(rec: dict[str, Any]) -> float:
    data = event_payload(rec)
    ts = data.get("ts") if isinstance(data, dict) else None
    if ts is None:
        raise AssertionError(f"event {event_type(rec)!r} missing ts")
    if isinstance(ts, (int, float)):
        return float(ts)
    # ISO-8601
    from datetime import datetime

    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00")).timestamp()
    except Exception as exc:
        raise AssertionError(f"unparseable ts {ts!r}: {exc}") from exc


def logo_png() -> Path:
    p = REPO_ROOT / "assets" / "omnirogue-logo.png"
    if not p.is_file():
        raise AssertionError(f"bundled logo missing: {p}")
    return p


def scan_package_for_shell() -> list[str]:
    """rg-equivalent: no subprocess/os.system/Popen/eval(/exec( under the package."""
    root = REPO_ROOT / "omniagentos_starter"
    if not root.is_dir():
        raise AssertionError("omniagentos_starter/ missing")
    pat = re.compile(r"subprocess|os\.system|Popen|eval\(|exec\(")
    hits: list[str] = []
    for path in root.rglob("*"):
        if path.suffix not in {".py", ".pyi", ".sh"}:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except Exception:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            if pat.search(line):
                hits.append(f"{path.relative_to(REPO_ROOT)}:{i}:{line.strip()}")
    return hits


def execute_worker_tool(root: str | Path, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Drive a tool through the engine worker tool-call path (not WorkspaceGuard alone).

    BINDING: omniagentos_starter.engine.execute_worker_tool(root, name, arguments)
    is THE function the worker LLM tool-call loop invokes for read_file / write_file /
    list_files. Returns a dict that MUST include error_tag=="WORKSPACE_ESCAPE" on
    rejected paths (never an empty/silent success).
    """
    from omniagentos_starter.engine import execute_worker_tool as _fn  # type: ignore

    result = _fn(root, name, arguments)
    if not isinstance(result, dict):
        raise AssertionError(
            f"execute_worker_tool must return dict, got {type(result)} {result!r}"
        )
    return result


# ------------------------------------------------------------- Round 6: AGENTS
# BINDING (PLAN.md "Round 6 — AGENTS"): AgentStore is rooted at
# OMNIAGENTOS_AGENTS_ROOT if set, else <repo>/agents. Loader = directory scan,
# same shape as skills/: `agents/<slug>.md` = YAML front-matter + body,
# `_builtin/general-worker.md` always present. Global tool allow-list is
# [read_file, write_file, list_files]; an agent's `tools` may only narrow it.

AGENT_GLOBAL_TOOLS = ("read_file", "write_file", "list_files")


def agents_root() -> Path:
    env = os.environ.get("OMNIAGENTOS_AGENTS_ROOT")
    if env:
        return Path(env)
    p = REPO_ROOT / "agents"
    if p.is_dir():
        return p
    raise AssertionError("agents/ directory missing at repo root")


def tmp_agents_root() -> Path:
    """Copy the shipped agents/ roster into a fresh tmp dir.

    Tests must NEVER write through the real <repo>/agents tree — every test
    that creates/edits/deletes an agent points OMNIAGENTOS_AGENTS_ROOT at the
    directory this returns (via spawn_serve(extra_env=...)), so the shipped
    prebuilt roster is never mutated. If agents/ does not exist yet (the
    red-first state before implementers build it), a minimal `_builtin/
    general-worker.md` fixture is synthesized so the harness itself doesn't
    crash before the real assertions (which still fail red against the
    missing API/engine).
    """
    src = REPO_ROOT / "agents"
    tmp = Path(tempfile.mkdtemp(prefix="omniagentos-agents-"))
    dest = tmp / "agents"
    if src.is_dir():
        # README.md is documentation, not an agent — exclude it from the
        # tmp copy so a created-agent lookup can never match it by accident
        # (e.g. its prose mentioning a test agent's name).
        shutil.copytree(
            src,
            dest,
            ignore=shutil.ignore_patterns("README.md", "readme.md"),
        )
    else:
        builtin = dest / "_builtin"
        builtin.mkdir(parents=True, exist_ok=True)
        (builtin / "general-worker.md").write_text(
            "---\n"
            "name: General Worker\n"
            "title: General Worker\n"
            "persona: A capable general-purpose worker with no specialised pack.\n"
            "skills: []\n"
            "tools: [read_file, write_file, list_files]\n"
            "memory_scope: general-worker\n"
            "visibility: public\n"
            "version: 1\n"
            "---\n"
            "You are a capable general-purpose worker.\n",
            encoding="utf-8",
        )
    return dest


def slugify(name: str) -> str:
    """BINDING slug derivation the oracle expects from a display name.

    Lowercase, non-alnum runs collapsed to a single hyphen, no leading/
    trailing hyphen. Implementers may use any equivalent derivation as long
    as it is deterministic and collision-checked (409) — tests treat the
    API's returned slug as authoritative and only use this as a fallback
    guess when a response omits it.
    """
    s = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    return s or "agent"


def parse_agent_file(path: Path) -> dict[str, Any]:
    """Parse an agents/<slug>.md front-matter + body. Oracle definition, BINDING on the loader."""
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        raise AssertionError(f"agent file {path} missing YAML front-matter")
    parts = text.split("---", 2)
    if len(parts) < 3:
        raise AssertionError(f"agent file {path} malformed front-matter")
    fm = yaml.safe_load(parts[1]) or {}
    if not isinstance(fm, dict):
        raise AssertionError(f"agent file {path} front-matter is not a mapping")
    body = parts[2].strip()
    fm["_body"] = body
    fm["_path"] = path
    fm["_sha256"] = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return fm


def find_agent_file(root: Path, slug: str) -> Path:
    for p in root.rglob("*.md"):
        if p.stem == slug:
            return p
    raise AssertionError(f"no agents/**/{slug}.md under {root}")


def find_agent_file_by_name(root: Path, name: str) -> Path:
    """Locate a just-created agent's file by parsing front-matter `name:` exactly.

    Never a substring/text-contains scan (that false-matched agents/README.md
    once its prose happened to mention a test agent's first name — the F5-class
    bug this replaces). README.md and anything under _builtin/ are excluded
    outright: they are never a created-agent's own file. A candidate file that
    fails to parse as agent front-matter (e.g. isn't one at all) is skipped,
    not treated as a match.
    """
    candidates = []
    for p in sorted(root.rglob("*.md")):
        if p.name.lower() == "readme.md":
            continue
        if "_builtin" in p.relative_to(root).parts:
            continue
        try:
            fm = parse_agent_file(p)
        except AssertionError:
            continue
        if fm.get("name") == name:
            candidates.append(p)
    if not candidates:
        raise AssertionError(
            f"no agent file under {root} has front-matter name=={name!r} "
            "(README.md and _builtin/ are excluded from this lookup)"
        )
    if len(candidates) > 1:
        raise AssertionError(
            f"ambiguous: {len(candidates)} agent files have front-matter name=={name!r}: "
            f"{candidates!r}"
        )
    return candidates[0]


def create_agent(
    base_url: str,
    name: str,
    title: str,
    persona: str,
    skills: list[str],
    tools: list[str] | None = None,
) -> dict[str, Any]:
    """POST /api/agents. Returns the parsed JSON body (must include a slug)."""
    body: dict[str, Any] = {"name": name, "title": title, "persona": persona, "skills": skills}
    if tools is not None:
        body["tools"] = tools
    resp = post_json(base_url, "/api/agents", body)
    if resp.status_code not in (200, 201):
        raise AssertionError(f"POST /api/agents -> HTTP {resp.status_code}: {resp.text[:800]}")
    data = resp.json()
    if not isinstance(data, dict):
        raise AssertionError(f"POST /api/agents must return an object, got {data!r}")
    slug = data.get("slug") or data.get("id")
    if not slug:
        raise AssertionError(f"POST /api/agents response missing slug/id: {data!r}")
    data["slug"] = str(slug)
    return data


def first_real_skill() -> tuple[str, str]:
    """Return (skill_slug, sha256) of the first non-builtin shipped skill on disk.

    Used by D16/D17 as a real, injectable skill to assign to a test agent.
    """
    root = REPO_ROOT / "skills"
    if not root.is_dir():
        raise AssertionError("skills/ directory missing at repo root")
    files = sorted(
        p
        for p in root.rglob("*.md")
        if p.name.lower() != "readme.md" and p.parent.name != "_builtin"
    )
    if not files:
        raise AssertionError("no non-builtin skill files under skills/")
    f = files[0]
    text = f.read_text(encoding="utf-8")
    return f.stem, hashlib.sha256(text.encode("utf-8")).hexdigest()


def second_real_skill(exclude_slug: str) -> tuple[str, str]:
    """Return (skill_slug, sha256) of a DIFFERENT shipped skill than exclude_slug.

    Used by D16 to prove a pack outside the agent's list never reaches the
    worker prompt.
    """
    root = REPO_ROOT / "skills"
    files = sorted(
        p
        for p in root.rglob("*.md")
        if p.name.lower() != "readme.md" and p.parent.name != "_builtin"
    )
    for f in files:
        if f.stem != exclude_slug:
            text = f.read_text(encoding="utf-8")
            return f.stem, hashlib.sha256(text.encode("utf-8")).hexdigest()
    raise AssertionError("need >=2 shipped skills to prove skill isolation (D16)")


def agent_events_of(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return events_of(events, "agent.assigned")


def pick_agent_skill() -> str:
    """Prefer a refund-handling pack for DEMO beat 0 (PLAN: 'Riley ... with the refund pack')."""
    root = REPO_ROOT / "skills"
    if root.is_dir():
        for p in sorted(root.rglob("*.md")):
            if p.parent.name == "_builtin" or p.name.lower() == "readme.md":
                continue
            if "refund" in p.stem.lower():
                return p.stem
    return first_real_skill()[0]
