"""How this could pass while broken: unit-testing WorkspaceGuard on '../x' while prefix/absolute/symlink writes return empty success, or grepping logs while the key is unset; now escapes are driven through execute_worker_tool, each returns WORKSPACE_ESCAPE, the package has no subprocess/Popen/eval/exec, and a planted key is required and must not appear in SSE, logs, or /api JSON."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest
from _harness import (
    PLANTED_D10_KEY,
    REPO_ROOT,
    assert_no_key_leak,
    collect_sse,
    execute_worker_tool,
    get_json,
    scan_package_for_shell,
    spawn_serve,
    start_run,
    write_json,
)


def test_d10_workspace_escape_through_engine_tool_path():
    root = Path(tempfile.mkdtemp(prefix="omniagentos-ws-"))
    (root / "inside.txt").write_text("ok", encoding="utf-8")
    outside = Path(tempfile.mkdtemp(prefix="omniagentos-outside-"))
    evil = Path(str(root) + "_evil")
    evil.mkdir(exist_ok=True)

    cases = {
        "../x": "../x",
        "foo/../../x": "foo/../../x",
        "absolute": "/tmp/x",
        "prefix-collision": str(evil / "x"),
        "null-byte": "foo\x00/../etc/passwd",
    }

    # Symlink pointing outside root.
    link = root / "outlink"
    try:
        link.symlink_to(outside / "pwned")
    except OSError as exc:
        pytest.fail(f"could not create symlink for escape test: {exc}")
    cases["symlink-out"] = "outlink"

    results = {}
    for label, path in cases.items():
        result = execute_worker_tool(
            root,
            "write_file",
            {"path": path, "content": "pwned"},
        )
        results[label] = result
        assert result.get("error_tag") == "WORKSPACE_ESCAPE", (
            f"{label} path={path!r} did not return error_tag=WORKSPACE_ESCAPE; "
            f"got {result!r} (empty/silent success is a fail)"
        )
        # Never an empty "success"
        assert result.get("ok") is not True

    # Guard construction refuses repo-root / package-dir / data-dir.
    from omniagentos_starter.tools import WorkspaceGuard  # type: ignore

    data_dir = Path(tempfile.mkdtemp(prefix="omniagentos-data-"))
    for bad in (REPO_ROOT, REPO_ROOT / "omniagentos_starter", data_dir):
        raised = False
        try:
            WorkspaceGuard(bad)
        except Exception:
            raised = True
        assert raised, f"WorkspaceGuard({bad}) must be refused at construction"

    hits = scan_package_for_shell()
    assert hits == [], "package contains subprocess/os.system/Popen/eval(/exec(:\n" + "\n".join(hits)

    write_json("d10-invariants.txt", {"escapes": results, "shell_hits": hits})


def test_d10_planted_key_never_leaks():
    key = PLANTED_D10_KEY
    os.environ["OMNIAGENTOS_API_KEY"] = key
    if not os.environ.get("OMNIAGENTOS_API_KEY"):
        pytest.fail("planted OMNIAGENTOS_API_KEY unset — redaction test must not skip (F2)")

    extra = {
        "OMNIAGENTOS_API_KEY": key,
        "OMNIAGENTOS_BASE_URL": "http://127.0.0.1:1/v1",  # closed port: forces provider error
        "XAI_API_KEY": None,
        "OPENROUTER_API_KEY": None,
        "OPENAI_API_KEY": None,
    }
    srv = spawn_serve(extra_env=extra, clear_provider_keys=True)
    try:
        health = get_json(srv.base_url, "/api/health")
        assert_no_key_leak(health.text, key, "/api/health")
        skills = get_json(srv.base_url, "/api/skills")
        assert_no_key_leak(skills.text, key, "/api/skills")
        try:
            rid = start_run(srv.base_url, "ping")
            events = collect_sse(srv.base_url, rid, timeout_s=30.0)
            blob = json_blob(events)
            assert_no_key_leak(blob, key, "SSE events")
            run = get_json(srv.base_url, f"/api/runs/{rid}")
            assert_no_key_leak(run.text, key, f"/api/runs/{rid}")
        except Exception as exc:
            # Connection errors still must not leak the key in any response we did get.
            assert_no_key_leak(str(exc), key, "exception text")

        # Child stdout/logs
        if srv.proc.stdout is not None:
            # remaining buffered output; also scan data_dir
            pass
        for p in srv.data_dir.rglob("*"):
            if p.is_file() and p.stat().st_size < 2_000_000:
                try:
                    txt = p.read_text(encoding="utf-8", errors="replace")
                except Exception:
                    continue
                assert_no_key_leak(txt, key, str(p))
    finally:
        srv.stop()


def json_blob(events) -> str:
    import json

    return json.dumps(events, default=str)
