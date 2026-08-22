#!/usr/bin/env python3
"""Drive one real run against a running server and write a live receipt.

This is the standing proof that the production line actually runs: it POSTs a
goal to a server you started yourself, watches the SSE stream, and asserts that
all four roles appeared, that the run finished, and that the provider answered
over HTTP. The receipt it writes is the evidence — schema OMNIAGENTOS-RECEIPT-1.

    python scripts/drill.py --goal "…" --out evidence/live-receipts/drill.json

Exit 0 only when the run reached `done` with planner, worker, critic and
verifier all present. Anything else is a non-zero exit and a receipt that says
why.
"""

from __future__ import annotations

import argparse
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
from omniagentos_starter.redact import contains_secret, redact  # noqa: E402

RECEIPT_MAGIC = "OMNIAGENTOS-RECEIPT-1"
ROLE_EVENTS = {
    "planner": ("planner.plan",),
    "worker": ("worker.started", "worker.finished", "worker.delta"),
    "critic": ("critic.verdict",),
    "verifier": ("verifier.verdict",),
}


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="run one goal against a live OmniAgentOS Starter server")
    p.add_argument("--goal", required=True)
    p.add_argument("--out", required=True, help="path to write the receipt JSON")
    p.add_argument("--base-url", default=os.environ.get("OMNIAGENTOS_URL", "http://127.0.0.1:8486"))
    p.add_argument("--extra-dod", action="append", default=[], help="criterion added to the critic rubric only")
    p.add_argument("--max-rounds", type=int, default=None)
    p.add_argument("--token", default=os.environ.get("OMNIAGENTOS_TOKEN", ""))
    p.add_argument("--timeout", type=float, default=300.0, help="seconds to wait for run.done")
    p.add_argument("--demo", action="store_true", help="drive the bundled replay instead of a live run")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    base = args.base_url.rstrip("/")
    headers = {"Authorization": f"Bearer {args.token}"} if args.token else {}
    receipt: dict = {
        "magic": RECEIPT_MAGIC,
        "kind": "drill",
        "git_head": git_head(REPO_ROOT),
        "argv": sys.argv,
        "base_url": base,
        "goal": args.goal,
        "started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "roles_seen": [],
        "event_types": [],
        "provider_http_status": [],
        "provider_hosts": [],
        "response_ids": [],
        "status": "unknown",
    }

    with httpx.Client(base_url=base, headers=headers, timeout=30.0) as client:
        nonce = f"drill-{int(time.time())}"
        health = client.get("/api/health", params={"nonce": nonce}).json()
        receipt["health_json"] = health
        receipt["nonce_echoed"] = health.get("nonce") == nonce

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
            return finish(receipt, args.out, 2)
        run_id = created.json()["run_id"]
        receipt["run_id"] = run_id

        events: list[dict] = []
        first_event_ms = None
        done_ms = None
        terminal: dict | None = None
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
                    if first_event_ms is None:
                        first_event_ms = int((time.monotonic() - t0) * 1000)
                    events.append(event)
                    payload = event.get("payload") or {}
                    if event["type"] == "llm.call":
                        receipt["provider_http_status"].append(payload.get("http_status"))
                        receipt["provider_hosts"].append(payload.get("provider_host"))
                        if payload.get("response_id"):
                            receipt["response_ids"].append(payload["response_id"])
                    if event["type"] in ("run.done", "run.failed"):
                        done_ms = int((time.monotonic() - t0) * 1000)
                        terminal = event
                        break

        summary = client.get(f"/api/runs/{run_id}").json()

    types = [e["type"] for e in events]
    roles = sorted(r for r, markers in ROLE_EVENTS.items() if any(m in types for m in markers))
    deliverable = ((terminal or {}).get("payload") or {}).get("deliverable", "") or summary.get("deliverable", "")
    receipt.update(
        {
            "event_types": sorted(set(types)),
            "event_count": len(events),
            "roles_seen": roles,
            "t_first_event_ms": first_event_ms,
            "t_done_ms": done_ms,
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
    if (terminal or {}).get("type") == "run.failed":
        receipt["error_tag"] = ((terminal or {}).get("payload") or {}).get("error_tag")
        receipt["error"] = redact(((terminal or {}).get("payload") or {}).get("message", ""))[:400]

    problems = []
    if receipt["status"] != "done":
        problems.append(f"run status is {receipt['status']}, not done")
    missing = sorted(set(ROLE_EVENTS) - set(roles))
    if missing:
        problems.append("roles never appeared: " + ", ".join(missing))
    if not args.demo and not any(s == 200 for s in receipt["provider_http_status"]):
        problems.append("no provider call returned HTTP 200")
    if not deliverable.strip():
        problems.append("the deliverable is empty")
    receipt["problems"] = problems

    return finish(receipt, args.out, 0 if not problems else 1)


def finish(receipt: dict, out: str, code: int) -> int:
    receipt["ok"] = code == 0
    receipt["finished_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    receipt = redact(receipt)
    if contains_secret(receipt):  # belt and braces: a receipt is a published artifact
        print("refusing to write a receipt containing a secret", file=sys.stderr)
        return 3
    path = Path(out)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(receipt, indent=2, default=str), encoding="utf-8")
    print(
        json.dumps(
            {
                k: receipt.get(k)
                for k in (
                    "ok",
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
