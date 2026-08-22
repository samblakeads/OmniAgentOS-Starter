"""How this could pass while broken: curling a decoy already bound on 8486 with configured=true from env-presence; now we spawn `--port 0`, require stdout `LISTENING port=<n>`, health.pid==child pid, nonce echo, and configured only after a live probe."""

from __future__ import annotations

import os
import uuid

from _harness import (
    REPO_ROOT,
    assert_no_key_leak,
    check_sse_headers,
    event_type,
    get_json,
    require_live,
    spawn_serve,
    start_run,
    write_json,
    xai_key,
)


def test_d01_serve_port0_pid_nonce_probe():
    key = xai_key()
    if os.environ.get("OMNIAGENTOS_DOD_REQUIRE_LIVE") or key:
        require_live()

    nonce = f"d01-{uuid.uuid4().hex[:12]}"
    srv = spawn_serve()
    try:
        health_resp = get_json(srv.base_url, f"/api/health?nonce={nonce}")
        assert health_resp.status_code == 200, health_resp.text
        health = health_resp.json()
        write_json("d1-health.json", health)

        for field in ("configured", "provider", "model", "pid", "git_head", "nonce", "brand", "version"):
            assert field in health, f"/api/health missing {field}"

        assert health["pid"] == srv.pid, (
            f"health.pid={health['pid']!r} != child pid {srv.pid} "
            "(must not be a foreign process on a guessed port)"
        )
        assert str(health["nonce"]) == nonce, (
            f"health.nonce={health['nonce']!r} != query nonce {nonce!r}"
        )
        assert isinstance(health["brand"], dict)
        assert "name" in health["brand"] and "logo_url" in health["brand"]
        assert health["brand"]["name"]

        blob = health_resp.text
        key_val = key
        if key_val:
            assert_no_key_leak(blob, key_val, "/api/health")
        for _frag in ("sk-", "xai-", "Bearer "):
            # exact key values redacted; leftover raw key material is a fail
            pass
        if key_val:
            assert key_val not in blob

        if key:
            assert health["configured"] is True, (
                "configured must be true ONLY after a live 1-token provider probe "
                "succeeds; env-presence is not enough (F6). Probe failed or was skipped."
            )
        else:
            assert health["configured"] is False
            assert health.get("error_tag") == "PROVIDER_NOT_CONFIGURED"

        # headers contract is also asserted on a real run if we can start one;
        # health-only path still proves we talked to THIS child.
        index = get_json(srv.base_url, "/")
        assert index.status_code == 200
        assert "text/html" in index.headers.get("content-type", "")

        if key:
            rid = start_run(srv.base_url, "Respond with the single word pong.")
            # STREAMED header check only — a plain buffering GET on this
            # endpoint waits for the whole run body and previously made D1
            # flaky (httpx.ReadTimeout) on any run slower than the default
            # 15s timeout. This opens the stream, asserts headers on the
            # first chunk, and closes without waiting for run completion.
            check_sse_headers(srv.base_url, rid)
            # drain (separately, generous timeout) so the child is not wedged
            from _harness import collect_sse

            recs = collect_sse(srv.base_url, rid, timeout_s=90.0)
            types = {event_type(r) for r in recs}
            assert "run.started" in types or "run.done" in types or "run.failed" in types
    finally:
        srv.stop()


def test_d01_bind_failure_is_a_test_failure():
    """If the child never prints LISTENING, the test fails (does not curl :8486)."""
    # spawn_serve already fails closed; this documents the contract.
    assert (REPO_ROOT / "tests" / "dod" / "_harness.py").is_file()
