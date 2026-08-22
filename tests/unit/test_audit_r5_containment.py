"""Containment findings from the Grok completion audit (F1, F2, F6, F7).

The theme the auditor named: a fix that landed on one path and not on its
sibling. `api._workspace` was given a containment pin; `execute_worker_tool` —
the path the invariant test actually drives — was not. Escapes in the *argument*
still died in resolve(), which is why the invariant test stayed green while the
root it was handed could be anything at all.
"""

from __future__ import annotations

import errno
import os
import tempfile
from pathlib import Path

import pytest
from conftest import Script, make_orchestrator, provider_config

from omniagentos_starter import tools as tools_module
from omniagentos_starter.api import create_app
from omniagentos_starter.config import ERROR_TAGS, Settings
from omniagentos_starter.engine import execute_worker_tool
from omniagentos_starter.tools import WorkspaceRefused, base_for_root, runs_root, workspace_for_run


# ------------------------------------------------------------------ F1 (major)
def test_the_worker_tool_refuses_a_root_that_owns_the_whole_runs_tree(tmp_path):
    """The too-wide root: the workspace parent, not one run's directory."""
    workspace = tmp_path / "workspace"
    (workspace / "runs").mkdir(parents=True)

    result = execute_worker_tool(str(workspace), "write_file", {"path": "pwn.txt", "content": "x"})

    assert result["ok"] is False
    assert result["error_tag"] == "WORKSPACE_ESCAPE", result
    assert not (workspace / "pwn.txt").exists(), "the write landed despite the refusal"


def test_the_worker_tool_pins_containment_to_the_runs_root_it_is_given(tmp_path):
    """An explicit base is honoured — the same pin api._workspace has always had."""
    workspace = tmp_path / "workspace"
    root = runs_root(workspace)
    (root / "abc123").mkdir(parents=True)
    outside = tmp_path / "elsewhere"
    outside.mkdir()

    ok = execute_worker_tool(
        str(root / "abc123"), "write_file", {"path": "note.md", "content": "x"}, base=root
    )
    assert ok["ok"] is True, ok

    refused = execute_worker_tool(
        str(outside), "write_file", {"path": "note.md", "content": "x"}, base=root
    )
    assert refused["ok"] is False and refused["error_tag"] == "WORKSPACE_ESCAPE", refused
    assert not (outside / "note.md").exists()


def test_the_pin_is_derived_from_the_root_when_no_caller_supplies_one(tmp_path):
    """`<workspace>/runs/<id>` names its own base; anything else gets no guess."""
    root = runs_root(tmp_path / "workspace")
    (root / "abc123").mkdir(parents=True)
    assert base_for_root(root / "abc123") == root
    # A root we do not recognise gets None, not `root.parent` — a pin that
    # contains the root by construction would prove nothing.
    assert base_for_root(tmp_path / "loose") is None


def test_the_engines_own_workspace_is_constructed_with_the_same_pin(tmp_path):
    """workspace_for_run is the constructor the live run uses."""
    guard = workspace_for_run(tmp_path / "workspace", "abc123", data_dir=tmp_path / "var")
    assert guard.root == runs_root(tmp_path / "workspace") / "abc123"
    with pytest.raises(WorkspaceRefused):
        # the runs root itself is not a run's workspace
        workspace_for_run(tmp_path / "workspace", "..", data_dir=tmp_path / "var")


def test_a_legitimate_run_workspace_containing_a_runs_dir_is_not_locked_out(tmp_path):
    """A worker writing `runs/…` inside its own box must not brick the box."""
    root = runs_root(tmp_path / "workspace") / "abc123"
    (root / "runs").mkdir(parents=True)
    result = execute_worker_tool(str(root), "write_file", {"path": "note.md", "content": "x"})
    assert result["ok"] is True, result


# ------------------------------------------------------------------ F6 (minor)
@pytest.mark.parametrize(
    ("label", "error"),
    [
        ("EACCES", PermissionError(errno.EACCES, "Permission denied")),
        ("ENOSPC", OSError(errno.ENOSPC, "No space left on device")),
        ("EROFS", OSError(errno.EROFS, "Read-only file system")),
    ],
)
def test_an_io_error_on_a_legal_path_is_not_reported_as_an_escape(tmp_path, monkeypatch, label, error):
    """A full disk is not an agent trying to leave the box.

    The failure is INJECTED at the write primitive rather than simulated with
    chmod. File permissions do not restrict writes the same way on Windows, so
    the chmod version of this test simply succeeded there and asserted nothing —
    CI caught it on windows-latest, which is exactly the platform a POSIX-shaped
    assumption is most likely to be wrong on. Injecting the errno makes the test
    say what it means on every platform: whatever the kernel refuses, a legal
    path that could not be written is an IO error, not an escape attempt.
    """
    root = runs_root(tmp_path / "workspace") / "abc123"
    root.mkdir(parents=True)
    real_open = os.open

    def failing_open(path, flags, mode=0o777, **kwargs):
        # Only our target fails. `tools.os` IS the global os module, so a fake
        # that refused everything would also break pytest's own capture.
        if str(path).endswith("n.txt"):
            raise error
        return real_open(path, flags, mode, **kwargs)

    monkeypatch.setattr(tools_module.os, "open", failing_open)
    result = execute_worker_tool(str(root), "write_file", {"path": "n.txt", "content": "x"})

    assert result["ok"] is False, f"{label}: {result}"
    assert result["error_tag"] == "WORKSPACE_IO_ERROR", f"{label}: {result}"
    assert result["error_tag"] != "WORKSPACE_ESCAPE"
    assert "WORKSPACE_IO_ERROR" in ERROR_TAGS
    assert not (root / "n.txt").exists(), f"{label}: the write landed despite the error"


def test_a_real_escape_still_reports_an_escape(tmp_path):
    """The distinction only means anything if the other side still fires."""
    root = runs_root(tmp_path / "workspace") / "abc123"
    root.mkdir(parents=True)
    for hostile in ("../x", "foo/../../x", "/tmp/x", "a\x00/../etc/passwd"):
        result = execute_worker_tool(str(root), "write_file", {"path": hostile, "content": "x"})
        assert result["error_tag"] == "WORKSPACE_ESCAPE", (hostile, result)


@pytest.mark.asyncio
async def test_the_live_write_path_makes_the_same_distinction(settings, tmp_path):
    """engine.write_file is the path a real worker's FILE block takes."""
    plan = {
        "dod": [{"id": "d1", "criterion": "ok"}],
        "tasks": [
            {"id": "t1", "title": "w", "skill_id": "general-assistant", "instruction": "x", "writes_files": True, "needs_tools": []}
        ],
    }
    body = "=== FILE: ../../pwned.md ===\nx\n=== END FILE ===\ndone"
    script = Script(plan=plan, worker_text=body)
    orch = make_orchestrator(settings, script)
    run = orch.create("Write a file", 1, [])
    await orch.execute(run)
    errors = [e["payload"] for e in run.bus.events if e["type"] == "tool.error"]
    assert errors and errors[0]["error_tag"] == "WORKSPACE_ESCAPE", errors


# --------------------------------------------------------- F2 (major) — HTTP
async def _asgi_get(app, raw_path: str) -> tuple[int, bytes]:
    """GET a path EXACTLY as written — no dot-segment normalisation.

    httpx (and therefore TestClient) collapses `..` in a URL before the request
    is ever built, so a client-side test cannot reach the handler with a hostile
    path param at all. Driving the ASGI app with a hand-built scope puts the
    literal bytes into `scope["path"]`, which is what Starlette routes on and
    what a hand-written HTTP request or `curl --path-as-is` would deliver.
    """
    path, _, query = raw_path.partition("?")
    status: dict = {}
    chunks: list[bytes] = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        if message["type"] == "http.response.start":
            status["code"] = message["status"]
        elif message["type"] == "http.response.body":
            chunks.append(message.get("body", b""))

    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.1"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("utf-8", "surrogateescape"),
        "query_string": query.encode(),
        "root_path": "",
        "headers": [(b"host", b"testserver")],
        "client": ("127.0.0.1", 12345),
        "server": ("127.0.0.1", 80),
    }
    await app(scope, receive, send)
    return status.get("code", 0), b"".join(chunks)


@pytest.fixture
def planted_workspace(tmp_path):
    """A real run directory, plus a secret outside it and a symlink pointing at it."""
    workspace = tmp_path / "workspace"
    run_dir = runs_root(workspace) / "probe123"
    run_dir.mkdir(parents=True)
    (run_dir / "note.md").write_text("BENIGN FILE BODY", encoding="utf-8")
    secret = tmp_path / "outside.txt"
    secret.write_text("SECRET-OUTSIDE-THE-BOX", encoding="utf-8")
    (run_dir / "link.md").symlink_to(secret)
    settings = Settings(
        host="127.0.0.1",
        port=0,
        data_dir=tmp_path / "var",
        workspace_dir=workspace,
        provider=provider_config(),
    )
    orch = make_orchestrator(settings, Script())
    return create_app(settings=settings, orchestrator=orch), secret.read_text()


HOSTILE_FILE_PATHS = [
    "../../../etc/passwd",
    "..%2F..%2F..%2Fetc%2Fpasswd",
    "%2e%2e%2f%2e%2e%2f%2e%2e%2fetc%2fpasswd",
    "..%252F..%252Fetc%252Fpasswd",
    "/etc/passwd",
    "//etc/passwd",
    "link.md",
    "note.md%00.png",
    "....//....//etc/passwd",
    "..\\..\\windows\\win.ini",
]


@pytest.mark.parametrize("hostile", HOSTILE_FILE_PATHS)
@pytest.mark.asyncio
async def test_a_hostile_file_path_never_returns_content(planted_workspace, hostile):
    """A valid run id plus a hostile file_path — the sibling of the run_id fix."""
    app, secret_body = planted_workspace
    code, body = await _asgi_get(app, f"/api/runs/probe123/files/{hostile}")
    assert code in (400, 404), f"{hostile!r} -> HTTP {code}: {body[:200]!r}"
    assert secret_body.encode() not in body, f"{hostile!r} leaked the file outside the box"
    assert b"root:" not in body


@pytest.mark.asyncio
async def test_the_benign_path_still_works(planted_workspace):
    """Otherwise the test above passes because nothing works at all."""
    app, _ = planted_workspace
    code, body = await _asgi_get(app, "/api/runs/probe123/files/note.md")
    assert code == 200 and b"BENIGN FILE BODY" in body


@pytest.mark.asyncio
async def test_the_listing_still_works_and_hides_the_symlink(planted_workspace):
    app, _ = planted_workspace
    code, body = await _asgi_get(app, "/api/runs/probe123/files")
    assert code == 200
    assert b"note.md" in body
    assert b"link.md" not in body, "a symlink out of the box must not be listed as a run artifact"


# ------------------------------------------------------------------ F7 (minor)
@pytest.mark.asyncio
async def test_an_unknown_run_and_a_refused_workspace_are_told_apart(tmp_path):
    workspace = tmp_path / "workspace"
    runs = runs_root(workspace)
    runs.mkdir(parents=True)
    # A run whose workspace exists but is a symlink — the guard refuses the root.
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (outside / "f.md").write_text("x", encoding="utf-8")
    (runs / "refused1").symlink_to(outside)

    settings = Settings(
        host="127.0.0.1",
        port=0,
        data_dir=tmp_path / "var",
        workspace_dir=workspace,
        provider=provider_config(),
    )
    app = create_app(settings=settings, orchestrator=make_orchestrator(settings, Script()))

    missing_code, missing_body = await _asgi_get(app, "/api/runs/nosuchrun/files")
    refused_code, refused_body = await _asgi_get(app, "/api/runs/refused1/files")

    # Both are still a 404 to the caller — fail closed, no oracle for an attacker.
    assert missing_code == refused_code == 404
    # But they no longer say the same thing.
    assert b"RUN_NOT_FOUND" in missing_body, missing_body
    assert b"WORKSPACE_REFUSED" in refused_body, refused_body
    assert missing_body != refused_body
    assert b"SECRET" not in refused_body


def test_the_new_tags_are_registered():
    for tag in ("WORKSPACE_IO_ERROR", "WORKSPACE_REFUSED", "RUN_NOT_FOUND"):
        assert tag in ERROR_TAGS


def test_a_temp_dir_root_is_still_usable_so_the_invariant_test_stays_meaningful():
    """The oracle drives escapes through a bare temp dir; that must still construct.

    If pinning had refused every unrecognised root, the invariant test would have
    gone green for the wrong reason: every hostile path would return
    WORKSPACE_ESCAPE from the CONSTRUCTOR, and the path-resolution logic it exists
    to exercise would never run again.
    """
    root = Path(tempfile.mkdtemp(prefix="omniagentos-ws-"))
    ok = execute_worker_tool(str(root), "write_file", {"path": "inside.txt", "content": "x"})
    assert ok["ok"] is True, ok
    escape = execute_worker_tool(str(root), "write_file", {"path": "../x", "content": "x"})
    assert escape["error_tag"] == "WORKSPACE_ESCAPE"
