#!/usr/bin/env python3
"""Capture a real run and bundle it as the demo replay.

`omniagentos_starter/data/replay-run.json` is not a hand-written mock: it is a
genuine run, captured here against a live provider, then redacted and stripped
of local paths. That is what makes the no-key demo honest — the stage sees the
same event stream the engine really produced.

    python scripts/record_replay.py --goal "…"                 # run one now
    python scripts/record_replay.py --from-run <id> --data-dir var   # bundle one you already have

Refuses to write a recording that is missing a role, that never finished, or
that still contains a secret or an absolute local path.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

import httpx

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from omniagentos_starter.config import replay_path  # noqa: E402
from omniagentos_starter.memory import Memory  # noqa: E402
from omniagentos_starter.redact import contains_secret, redact  # noqa: E402
from omniagentos_starter.replay import SCHEMA  # noqa: E402

DEFAULT_GOAL = (
    "Write exactly 3 ad headlines for an AI video tool, each under 40 characters, "
    "and explain in one line why each works"
)
REQUIRED = ("run.started", "planner.plan", "worker.delta", "critic.verdict", "verifier.verdict", "run.done")

# An absolute local path is a fingerprint of the machine that recorded the run.
LOCAL_PATH_RE = re.compile(r"(?<![:\w/])/(?:Users|home|private|var|tmp|opt)/[^\s\"',)]{2,}")


def sequence_of(event: dict) -> int:
    """The stream's own sequence number.

    On the wire it is `event_id`. `id` belongs to whatever the payload is about
    (a lesson, for instance) — reading it here raised KeyError on the very first
    event, so the bundled demo could not be regenerated at all, and if a payload
    ever did carry an `id` the recording would have been silently wrong instead.
    """
    seq = event.get("event_id")
    if seq is None:
        raise SystemExit(
            f"SSE event {event.get('type')!r} carries no event_id; refusing to guess a sequence number"
        )
    return int(seq)


def scrub(value):
    """Redact secrets, then erase anything that looks like a local absolute path."""
    value = redact(value)
    if isinstance(value, str):
        return LOCAL_PATH_RE.sub("<path>", value)
    if isinstance(value, dict):
        return {k: scrub(v) for k, v in value.items()}
    if isinstance(value, list):
        return [scrub(v) for v in value]
    return value


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="record a live run into the bundled demo replay")
    p.add_argument("--goal", default=DEFAULT_GOAL)
    p.add_argument("--extra-dod", action="append", default=[])
    p.add_argument("--base-url", default=os.environ.get("OMNIAGENTOS_URL", "http://127.0.0.1:8486"))
    p.add_argument("--token", default=os.environ.get("OMNIAGENTOS_TOKEN", ""))
    p.add_argument("--out", default=str(replay_path()))
    p.add_argument("--timeout", type=float, default=300.0)
    p.add_argument("--from-run", default="", help="bundle a run already stored in the database")
    p.add_argument("--data-dir", default="var", help="database directory for --from-run")
    p.add_argument("--allow-partial", action="store_true", help="record even if a role is missing")
    p.add_argument("--provider", default="", help="provider name for --from-run (else read from llm.call events)")
    p.add_argument("--model", default="", help="model name for --from-run (else read from llm.call events)")
    return p.parse_args(argv)


def capture_live(args) -> tuple[list[dict], str, dict]:
    base = args.base_url.rstrip("/")
    headers = {"Authorization": f"Bearer {args.token}"} if args.token else {}
    with httpx.Client(base_url=base, headers=headers, timeout=30.0) as client:
        health = client.get("/api/health").json()
        if not health.get("configured"):
            raise SystemExit(f"provider not reachable: {health.get('error_tag')} — a recording must be real")
        body: dict = {"goal": args.goal}
        if args.extra_dod:
            body["extra_dod"] = args.extra_dod
        created = client.post("/api/runs", json=body)
        if created.status_code >= 300:
            raise SystemExit(f"run refused: {created.status_code} {created.text[:200]}")
        run_id = created.json()["run_id"]

        t0 = time.monotonic()
        # A wall-clock deadline, not httpx's idle-read timeout: the SSE endpoint
        # sends a keepalive every 15s, which reset the read timeout for ever, so a
        # hung run recorded until somebody noticed.
        deadline = t0 + float(args.timeout)
        captured: list[dict] = []
        with httpx.Client(base_url=base, headers=headers, timeout=args.timeout) as stream_client:
            with stream_client.stream("GET", f"/api/runs/{run_id}/events") as response:
                for line in response.iter_lines():
                    if time.monotonic() > deadline:
                        raise SystemExit(
                            f"no terminal event within {args.timeout}s; refusing to bundle a partial capture"
                        )
                    if not line.startswith("data:"):
                        continue
                    event = json.loads(line[5:].strip())
                    captured.append(
                        {
                            "id": sequence_of(event),
                            "offset_ms": int((time.monotonic() - t0) * 1000),
                            "type": event["type"],
                            "payload": event.get("payload") or {},
                        }
                    )
                    if event["type"] in ("run.done", "run.failed"):
                        break
    return captured, run_id, health


def capture_stored(args) -> tuple[list[dict], str, dict]:
    """Bundle a run that already happened — same events, same redaction path."""
    memory = Memory(Path(args.data_dir))
    stored = memory.get_run(args.from_run)
    if not stored:
        raise SystemExit(f"no run {args.from_run} in {args.data_dir}")
    events = memory.events(args.from_run)
    if not events:
        raise SystemExit(f"run {args.from_run} has no stored events")
    t0 = events[0]["ts"]
    captured = [
        {
            "id": e["id"],
            "offset_ms": int((e["ts"] - t0) * 1000),
            "type": e["type"],
            "payload": e["payload"],
        }
        for e in events
    ]
    # provider/model are NOT stored on the run row, so there is nothing here to
    # read them from. Inventing "xai"/"grok-4.3" would put a claim in the public
    # replay bundle that the recording cannot support — and the stage copy says
    # "this is a real grok-4.3 run" out loud.
    provider = args.provider
    model = args.model
    if not provider or not model:
        for event in reversed(captured):
            if event["type"] == "llm.call":
                provider = provider or event["payload"].get("provider")
                model = model or event["payload"].get("model")
                break
    if not provider or not model:
        raise SystemExit(
            f"run {args.from_run} does not record which provider answered; "
            "pass --provider and --model explicitly"
        )
    return captured, args.from_run, {"provider": provider, "model": model, "goal": stored["goal"]}


def main(argv=None) -> int:
    args = parse_args(argv)
    if args.from_run:
        captured, run_id, health = capture_stored(args)
        goal = health.get("goal") or args.goal
    else:
        captured, run_id, health = capture_live(args)
        goal = args.goal

    types = {e["type"] for e in captured}
    missing = [t for t in REQUIRED if t not in types]
    if missing and not args.allow_partial:
        print("refusing to bundle an incomplete recording; missing: " + ", ".join(missing), file=sys.stderr)
        return 1

    recording = {
        "schema": SCHEMA,
        "run_id": run_id,
        "goal": goal,
        "extra_dod": args.extra_dod,
        "recorded_ts": time.time(),
        "recorded_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "provider": health.get("provider"),
        "model": health.get("model"),
        "verified": bool(
            next(
                (e["payload"].get("verified") for e in reversed(captured) if e["type"] == "run.done"),
                False,
            )
            is True
        ),
        "events": captured,
    }
    recording = scrub(recording)

    blob = json.dumps(recording, indent=2, default=str)
    if contains_secret(blob):
        print("refusing to bundle a recording containing a secret", file=sys.stderr)
        return 3
    leaked = LOCAL_PATH_RE.findall(blob)
    if leaked:
        print(f"refusing to bundle a recording containing local paths: {leaked[:3]}", file=sys.stderr)
        return 3

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(blob + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "out": str(out),
                "run_id": run_id,
                "events": len(captured),
                "types": sorted(types),
                "bytes": len(blob),
                "repair_rounds": sum(1 for e in captured if e["type"] == "repair.dispatched"),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
