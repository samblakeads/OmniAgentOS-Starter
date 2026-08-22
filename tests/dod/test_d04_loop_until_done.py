"""How this could pass while broken: a critic parse-error mapped to pass=false plus a verifier timeout mapped to verified=true, or a worker echoing PRODUCTION LINE from the goal; now extra_dod injects the phrase off-goal, verdicts are raw JSON with keys present (never defaulted), repair.dispatched is required, artifacts change, and missing verified cannot read as true."""

from __future__ import annotations

import json
import re

from _harness import (
    assert_status_present,
    collect_sse,
    event_payload,
    event_type,
    events_of,
    get_run,
    live_xai_base_url_ok,
    require_live,
    spawn_serve,
    start_run,
    write_json,
)

# Phrase MUST NOT appear in the goal (F12).
D4_GOAL = "Write a two-sentence explanation of why a team of agents beats a single chatbox."
D4_CRITERION = "must contain the exact phrase 'PRODUCTION LINE' in caps and be under 60 words"
PHRASE_RE = re.compile(r"\bPRODUCTION LINE\b")


def _raw_obj(rec) -> dict:
    """Parse the event payload as JSON object; do not default missing keys."""
    data = rec.get("data")
    if isinstance(data, dict):
        return data
    raw = rec.get("raw")
    if isinstance(raw, str) and raw.strip():
        obj = json.loads(raw)
        if not isinstance(obj, dict):
            raise AssertionError(f"event data is not an object: {obj!r}")
        return obj
    raise AssertionError("event has no JSON object payload")


def test_d04_extra_dod_forces_repair_then_verify():
    require_live()
    assert live_xai_base_url_ok()
    assert "PRODUCTION LINE" not in D4_GOAL

    srv = spawn_serve()
    try:
        rid = start_run(
            srv.base_url,
            D4_GOAL,
            extra_dod=[{"criterion": D4_CRITERION}],
            max_rounds=3,
        )
        events = collect_sse(srv.base_url, rid, timeout_s=180.0)
        run = get_run(srv.base_url, rid)
        write_json(
            "d4-loop.json",
            {
                "run_id": rid,
                "run": run,
                "types": [event_type(e) for e in events],
            },
        )

        critic_fail_i = None
        for i, rec in enumerate(events):
            if event_type(rec) != "critic.verdict":
                continue
            obj = _raw_obj(rec)
            assert "pass" in obj, "critic.verdict JSON missing key 'pass' (must not default)"
            if obj["pass"] is False:
                critic_fail_i = i
                break
        assert critic_fail_i is not None, (
            "no critic.verdict with pass==false (raw JSON). extra_dod must be applied "
            "to the critic rubric only, not echoed from the goal."
        )

        repair_i = None
        repaired_ids = []
        for i, rec in enumerate(events):
            if i <= critic_fail_i:
                continue
            if event_type(rec) != "repair.dispatched":
                continue
            obj = _raw_obj(rec)
            tids = obj.get("task_ids") or []
            assert isinstance(tids, list) and len(tids) >= 1, (
                f"repair.dispatched.task_ids must have len>=1, got {tids!r} "
                "(empty set is REPAIR_UNLOCALISED)"
            )
            repair_i = i
            repaired_ids = [str(t) for t in tids]
            break
        assert repair_i is not None, "missing repair.dispatched after critic fail"

        worker_after = None
        for i, rec in enumerate(events):
            if i <= repair_i:
                continue
            if event_type(rec) != "worker.finished":
                continue
            obj = _raw_obj(rec)
            tid = str(obj.get("task_id", ""))
            if tid in repaired_ids or not repaired_ids:
                worker_after = i
                break
        assert worker_after is not None, (
            f"no worker.finished for repaired task_ids {repaired_ids} after repair.dispatched"
        )

        verifier_i = None
        verifier_obj = None
        for i, rec in enumerate(events):
            if i <= worker_after:
                continue
            if event_type(rec) != "verifier.verdict":
                continue
            obj = _raw_obj(rec)
            assert "verified" in obj, (
                "verifier.verdict JSON missing key 'verified' — must not default to true (F3)"
            )
            verifier_i = i
            verifier_obj = obj
            break
        assert verifier_i is not None, "missing verifier.verdict after repaired worker.finished"
        assert verifier_obj["verified"] is True, (
            f"verified must be JSON true, got {verifier_obj.get('verified')!r}"
        )

        critic_obj = _raw_obj(events[critic_fail_i])
        c_req = critic_obj.get("request_id")
        v_req = verifier_obj.get("request_id")
        if not c_req or not v_req:
            # Fall back to llm.call ids for those roles.
            from _harness import llm_calls

            calls = llm_calls(events)
            c_ids = [c.get("request_id") or c.get("response_id") for c in calls if c.get("role") == "critic"]
            v_ids = [c.get("request_id") or c.get("response_id") for c in calls if c.get("role") == "verifier"]
            assert c_ids and v_ids, "critic/verifier request_id missing on verdict and llm.call"
            c_req, v_req = c_ids[0], v_ids[0]
        assert str(c_req) != str(v_req), (
            "critic.request_id == verifier.request_id — not independent requests"
        )

        deliverable = run.get("deliverable") or event_payload(events_of(events, "run.done")[-1]).get(
            "deliverable", ""
        )
        assert isinstance(deliverable, str)
        assert PHRASE_RE.search(deliverable), "final deliverable lacks \\bPRODUCTION LINE\\b"
        assert len(deliverable.split()) < 60, (
            f"deliverable has {len(deliverable.split())} words, need <60"
        )

        # Repaired task's final artifact differs from round-1 artifact.
        artifacts_by_task: dict[str, list[str]] = {}
        for rec in events:
            if event_type(rec) != "worker.finished":
                continue
            obj = _raw_obj(rec)
            tid = str(obj.get("task_id", ""))
            art = obj.get("artifact")
            if tid and isinstance(art, str):
                artifacts_by_task.setdefault(tid, []).append(art)
        changed = False
        for tid in repaired_ids:
            arts = artifacts_by_task.get(tid) or []
            if len(arts) >= 2 and arts[0] != arts[-1]:
                changed = True
                break
        if not changed:
            # If task ids on worker.finished don't match, any task with 2 different artifacts.
            for arts in artifacts_by_task.values():
                if len(arts) >= 2 and arts[0] != arts[-1]:
                    changed = True
                    break
        assert changed, (
            "repaired task final artifact equals round-1 artifact "
            f"(task_ids={repaired_ids}, artifacts={list(artifacts_by_task)})"
        )

        assert_status_present(run, "done")
        assert "verified" in run and run["verified"] is True

        # F3: a missing/malformed verifier must not be treated as verified==true.
        # We assert OUR reader never defaults, and that if a later inject env is
        # honored the run fails. Binding: omitting `verified` is a fail.
        for rec in events_of(events, "verifier.verdict"):
            obj = _raw_obj(rec)
            if "verified" not in obj:
                raise AssertionError("verifier.verdict omitted 'verified' (fail-open)")
    finally:
        srv.stop()


def test_d04_verifier_payload_never_defaults_true():
    """Engine helper used by the run loop: missing/malformed => not verified.

    BINDING: omniagentos_starter.engine.verifier_is_verified(payload) returns True
    only when payload is a dict with JSON boolean verified is True. None, non-dict,
    missing key, or non-boolean must be False. The engine MUST call this (or
    equivalent present-key check) — GET /api/runs verified may be true only when
    a verifier.verdict event carried verified:true.
    """
    from omniagentos_starter.engine import verifier_is_verified  # type: ignore

    assert verifier_is_verified(None) is False
    assert verifier_is_verified("not-json") is False
    assert verifier_is_verified({}) is False
    assert verifier_is_verified({"pass": True}) is False
    assert verifier_is_verified({"verified": "true"}) is False
    assert verifier_is_verified({"verified": True}) is True
