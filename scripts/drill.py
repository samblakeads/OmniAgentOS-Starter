#!/usr/bin/env python3
"""Drive one real run and write a live receipt (schema OMNIAGENTOS-RECEIPT-1).

This is the standing proof that the production line actually runs. Point it at a
server you started yourself:

    python scripts/drill.py --goal "…" --receipt evidence/live-receipts/drill.json

or run it with no server at all and it drives the engine in-process against the
same provider. Either way the receipt records what really happened: the health
document, every provider HTTP status, the roles seen on the wire, the timings,
and the sha256 of the deliverable.

Exit 0 only when the run reached `done` with planner, worker, critic and
verifier all present. Anything else exits non-zero and the receipt says why.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
import time
from pathlib import Path

import httpx

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from omniagentos_starter.api import git_head  # noqa: E402
from omniagentos_starter.config import VERSION, Settings, skills_dir  # noqa: E402
from omniagentos_starter.redact import contains_secret, redact  # noqa: E402

RECEIPT_MAGIC = "OMNIAGENTOS-RECEIPT-1"
ROLE_EVENTS = {
    "planner": ("planner.plan",),
    "worker": ("worker.started", "worker.finished", "worker.delta"),
    "critic": ("critic.verdict",),
    "verifier": ("verifier.verdict",),
}


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="run one goal and write a live receipt")
    p.add_argument("--goal", required=True)
    p.add_argument("--out", default=None, help="path to write the receipt JSON")
    p.add_argument("--receipt", default=None, help="alias for --out")
    p.add_argument("--base-url", default=os.environ.get("OMNIAGENTOS_URL", "http://127.0.0.1:8486"))
    p.add_argument("--extra-dod", action="append", default=[], help="criterion added to the critic rubric only")
    p.add_argument("--max-rounds", type=int, default=None)
    p.add_argument("--data-dir", default="var", help="data directory when running in-process")
    p.add_argument("--token", default=os.environ.get("OMNIAGENTOS_TOKEN", ""))
    p.add_argument("--timeout", type=float, default=300.0, help="seconds to wait for run.done")
    p.add_argument("--demo", action="store_true", help="drive the bundled replay instead of a live run")
    p.add_argument("--in-process", action="store_true", help="never use HTTP; drive the engine directly")
    args = p.parse_args(argv)
    args.out = args.receipt or args.out
    if not args.out:
        p.error("one of --out/--receipt is required")
    return args


# ------------------------------------------------------------------ over HTTP
def drive_http(args, receipt: dict) -> tuple[list[dict], dict, str]:
    base = args.base_url.rstrip("/")
    headers = {"Authorization": f"Bearer {args.token}"} if args.token else {}
    with httpx.Client(base_url=base, headers=headers, timeout=30.0) as client:
        nonce = f"drill-{int(time.time())}"
        health = client.get("/api/health", params={"nonce": nonce}).json()
        receipt["health_json"] = health
        receipt["nonce_echoed"] = health.get("nonce") == nonce
        receipt["mode"] = "http"

        t0 = time.monotonic()
        if args.demo:
            created = client.post("/api/demo")
        else:
            body: dict = {"goal": args.goal}
            if args.extra_dod:
                body["extra_dod"] = args.extra_dod
            if args.max_rounds:
                body["max_rounds"] = args.max_rounds
            created = client.post("/api/runs", json=body)
        receipt["create_status"] = created.status_code
        if created.status_code >= 300:
            receipt["status"] = "failed"
            receipt["error"] = redact(created.text)[:400]
            return [], {"status": "failed"}, ""
        run_id = created.json()["run_id"]
        receipt["run_id"] = run_id

        events: list[dict] = []
        with httpx.Client(base_url=base, headers=headers, timeout=args.timeout) as stream_client:
            with stream_client.stream("GET", f"/api/runs/{run_id}/events") as response:
                receipt["sse_status"] = response.status_code
                receipt["sse_headers"] = {
                    "cache-control": response.headers.get("cache-control"),
                    "x-accel-buffering": response.headers.get("x-accel-buffering"),
                }
                for line in response.iter_lines():
                    if not line.startswith("data:"):
                        continue
                    event = json.loads(line[5:].strip())
                    if receipt.get("t_first_event_ms") is None:
                        receipt["t_first_event_ms"] = int((time.monotonic() - t0) * 1000)
                    events.append(event)
                    if event["type"] in ("run.done", "run.failed"):
                        receipt["t_done_ms"] = int((time.monotonic() - t0) * 1000)
                        break
        summary = client.get(f"/api/runs/{run_id}").json()
    return events, summary, run_id


# --------------------------------------------------------------- in-process
async def _drive_in_process(args, receipt: dict) -> tuple[list[dict], dict, str]:
    from omniagentos_starter.engine import Orchestrator
    from omniagentos_starter.llm import LLMClient

    settings = Settings.from_env(data_dir=args.data_dir)
    orch = Orchestrator(settings)
    orch.load_library(skills_dir())

    ok, error_tag, detail = await LLMClient(settings.provider).probe()
    receipt["health_json"] = {
        "status": "ok",
        "version": VERSION,
        "pid": os.getpid(),
        "git_head": git_head(REPO_ROOT),
        "configured": bool(ok),
        "provider": settings.provider.provider,
        "model": settings.provider.model,
        "provider_host": settings.provider.host,
        "brand": settings.brand.as_dict(),
        "skills": orch.library.count,
        "error_tag": error_tag,
        "detail": redact(detail)[:200],
    }
    receipt["mode"] = "in-process"

    run = orch.create(args.goal, args.max_rounds, args.extra_dod)
    receipt["run_id"] = run.id
    receipt["create_status"] = 201

    t0 = time.monotonic()
    events: list[dict] = []
    original_emit = run.bus.emit

    def emit(etype: str, payload=None):
        event = original_emit(etype, payload)
        if receipt.get("t_first_event_ms") is None:
            receipt["t_first_event_ms"] = int((time.monotonic() - t0) * 1000)
        # same shape the SSE endpoint publishes: payload flattened, canonical keys on top
        from omniagentos_starter.api import sse_data

        events.append(sse_data(event))
        if etype in ("run.done", "run.failed"):
            receipt["t_done_ms"] = int((time.monotonic() - t0) * 1000)
        return event

    run.bus.emit = emit  # type: ignore[method-assign]
    await orch.execute(run)
    return events, redact(run.summary()), run.id


def drive_in_process(args, receipt: dict) -> tuple[list[dict], dict, str]:
    return asyncio.run(_drive_in_process(args, receipt))


# --------------------------------------------------------------------- main
def main(argv=None) -> int:
    args = parse_args(argv)
    receipt: dict = {
        "magic": RECEIPT_MAGIC,
        "kind": "drill",
        "git_head": git_head(REPO_ROOT),
        "argv": list(sys.argv),
        "base_url": args.base_url.rstrip("/"),
        "goal": args.goal,
        "started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "roles_seen": [],
        "event_types": [],
        "provider_http_status": [],
        "provider_hosts": [],
        "response_ids": [],
        "status": "unknown",
        "t_first_event_ms": None,
        "t_done_ms": None,
    }

    try:
        if args.in_process:
            raise httpx.ConnectError("--in-process requested")
        events, summary, run_id = drive_http(args, receipt)
    except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
        if args.demo:
            receipt["status"] = "failed"
            receipt["error"] = f"no server to replay against: {type(exc).__name__}"
            return finish(receipt, args.out, 2)
        receipt["http_unavailable"] = type(exc).__name__
        events, summary, run_id = drive_in_process(args, receipt)

    for event in events:
        if event.get("type") == "llm.call":
            receipt["provider_http_status"].append(event.get("http_status"))
            receipt["provider_hosts"].append(event.get("provider_host"))
            if event.get("response_id"):
                receipt["response_ids"].append(event["response_id"])

    types = [e.get("type") for e in events]
    roles = sorted(r for r, markers in ROLE_EVENTS.items() if any(m in types for m in markers))
    terminal = next((e for e in reversed(events) if e.get("type") in ("run.done", "run.failed")), {})
    deliverable = terminal.get("deliverable") or summary.get("deliverable") or ""
    receipt.update(
        {
            "event_types": sorted({t for t in types if t}),
            "event_count": len(events),
            "roles_seen": roles,
            "status": summary.get("status", "unknown"),
            "verified": bool(summary.get("verified")),
            "rounds": summary.get("rounds"),
            "llm_calls": summary.get("llm_calls"),
            "tokens": summary.get("tokens"),
            "est_cost_usd": summary.get("est_cost_usd"),
            "deliverable_sha256": hashlib.sha256((deliverable or "").encode("utf-8")).hexdigest(),
            "deliverable_chars": len(deliverable or ""),
            "deliverable_head": redact(deliverable or "")[:400],
            "run_summary": redact(summary),
            "repair_dispatched": types.count("repair.dispatched"),
        }
    )
    receipt["t_first_event_ms"] = receipt["t_first_event_ms"] or 0
    receipt["t_done_ms"] = receipt["t_done_ms"] or 0
    if terminal.get("type") == "run.failed":
        receipt["error_tag"] = terminal.get("error_tag")
        receipt["error"] = redact(terminal.get("message", ""))[:400]

    problems = []
    if receipt["status"] != "done":
        problems.append(f"run status is {receipt['status']}, not done")
    missing = sorted(set(ROLE_EVENTS) - set(roles))
    if missing:
        problems.append("roles never appeared: " + ", ".join(missing))
    if not args.demo and not any(s == 200 for s in receipt["provider_http_status"]):
        problems.append("no provider call returned HTTP 200")
    if not (deliverable or "").strip():
        problems.append("the deliverable is empty")
    receipt["problems"] = problems

    return finish(receipt, args.out, 0 if not problems else 1)


def finish(receipt: dict, out: str, code: int) -> int:
    receipt["ok"] = code == 0
    receipt["finished_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    receipt.setdefault("health_json", {})
    receipt.setdefault("run_id", "")
    receipt["t_first_event_ms"] = receipt.get("t_first_event_ms") or 0
    receipt["t_done_ms"] = receipt.get("t_done_ms") or 0
    receipt.setdefault("deliverable_sha256", hashlib.sha256(b"").hexdigest())
    receipt = redact(receipt)
    if contains_secret(receipt):  # belt and braces: a receipt is a published artifact
        print("refusing to write a receipt containing a secret", file=sys.stderr)
        return 3
    path = Path(out)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(receipt, indent=2, default=str) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                k: receipt.get(k)
                for k in (
                    "ok",
                    "mode",
                    "status",
                    "roles_seen",
                    "t_first_event_ms",
                    "t_done_ms",
                    "llm_calls",
                    "rounds",
                    "repair_dispatched",
                    "problems",
                )
            },
            indent=2,
            default=str,
        )
    )
    print(f"receipt → {path}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
