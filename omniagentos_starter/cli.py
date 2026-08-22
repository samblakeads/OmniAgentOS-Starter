"""Command line: `omniagentos serve`, `omniagentos run "<goal>"`, `omniagentos demo`.

Three verbs, no build step, no shell-outs. `serve` refuses a non-loopback bind
unless OMNIAGENTOS_TOKEN is set, and says so plainly rather than binding
anyway.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import threading
import time
import webbrowser
from pathlib import Path

from .config import VERSION, BindRefused, Settings, skills_dir, validate_bind
from .engine import Orchestrator
from .redact import ProviderError, redact
from .replay import ReplayUnavailable, replay_into


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="omniagentos", description="OmniAgentOS Starter — an agent operating system")
    p.add_argument("--version", action="version", version=f"omniagentos-starter {VERSION}")
    sub = p.add_subparsers(dest="command", required=True)

    serve = sub.add_parser("serve", help="run the dashboard and API")
    serve.add_argument("--port", type=int, default=8486)
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--data-dir", default="var")
    serve.add_argument("--open", action="store_true", help="open the dashboard in a browser")

    run = sub.add_parser("run", help="run one goal headlessly and print the deliverable")
    run.add_argument("goal")
    run.add_argument("--data-dir", default="var")
    run.add_argument("--max-rounds", type=int, default=None)
    run.add_argument("--extra-dod", action="append", default=[], help="extra Definition-of-Done criterion")
    run.add_argument("--json", action="store_true", help="print the run summary as JSON")

    demo = sub.add_parser("demo", help="replay a recorded run (no API key needed)")
    demo.add_argument("--port", type=int, default=8486)
    demo.add_argument("--host", default="127.0.0.1")
    demo.add_argument("--data-dir", default="var")
    demo.add_argument("--headless", action="store_true", help="print the replay to stdout instead of serving")
    return p


def _serve(args, path: str = "/") -> int:
    import socket

    import uvicorn

    from .api import create_app

    try:
        validate_bind(args.host)
    except BindRefused as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    settings = Settings.from_env(host=args.host, port=args.port, data_dir=args.data_dir)
    app = create_app(settings)

    # Bind the socket ourselves so the port is known before we serve. With
    # --port 0 the kernel picks it, and anything supervising this process needs
    # to be told which one rather than guessing (a guessed port can find someone
    # else's server and report it as ours).
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind((args.host, args.port))
    except OSError as exc:
        sock.close()
        print(f"error: cannot bind {args.host}:{args.port}: {exc}", file=sys.stderr)
        return 2
    sock.listen(2048)
    sock.set_inheritable(True)
    port = int(sock.getsockname()[1])
    settings.port = port

    # The machine-readable line goes FIRST and straight to fd 1: a supervisor
    # reading our stdout learns the port from the very first line it gets,
    # whatever buffering sits between us. Everything below is for humans.
    os.write(1, f"LISTENING port={port}\n".encode())

    url = f"http://{args.host}:{port}{path}"
    print(f"OmniAgentOS Starter {VERSION} — dashboard on {url}")
    print(f"provider: {settings.provider.provider or 'none'} model: {settings.provider.model or '-'}")
    print(f"skills:   {skills_dir()}", flush=True)

    if getattr(args, "open", False) or path != "/":
        threading.Thread(target=lambda: (time.sleep(1.2), webbrowser.open(url)), daemon=True).start()
    server = uvicorn.Server(uvicorn.Config(app, log_level="warning"))
    server.run(sockets=[sock])
    return 0


async def _run_goal(args) -> int:
    settings = Settings.from_env(data_dir=args.data_dir)
    orch = Orchestrator(settings)
    orch.load_library(skills_dir())
    run = orch.create(args.goal, args.max_rounds, args.extra_dod)
    printed: dict[str, bool] = {}

    def echo(event: dict) -> None:
        etype, payload = event["type"], event["payload"]
        if etype == "worker.delta":
            sys.stdout.write(payload.get("text", ""))
            sys.stdout.flush()
            printed[payload.get("task_id", "")] = True
        elif etype == "worker.started":
            print(f"\n[worker {payload.get('task_id')}] {payload.get('title', '')}", file=sys.stderr)
        elif etype in ("critic.verdict", "verifier.verdict"):
            role = etype.split(".")[0]
            print(
                f"\n[{role}] {'PASS' if payload.get('pass') else 'FAIL'} "
                f"({len(payload.get('failures') or [])} failing of {payload.get('checked')})",
                file=sys.stderr,
            )
        elif etype == "repair.dispatched":
            print(f"\n[repair] round {payload.get('round')} → {payload.get('task_ids')}", file=sys.stderr)
        elif etype in ("skill.selected", "memory.recalled", "run.failed", "plan.pruned", "tool.error"):
            print(f"\n[{etype}] {json.dumps(payload, default=str)[:400]}", file=sys.stderr)

    original_emit = run.bus.emit

    def emit(etype: str, payload=None):
        event = original_emit(etype, payload)
        echo(event)
        return event

    run.bus.emit = emit  # type: ignore[method-assign]
    try:
        await orch.execute(run)
    except ProviderError as exc:
        print(json.dumps(exc.as_dict()), file=sys.stderr)
        return 1
    print("\n" + "-" * 60)
    if args.json:
        print(json.dumps(redact(run.summary()), indent=2, default=str))
    else:
        print(run.deliverable or f"run failed: {run.error_tag} {run.error_message}")
    return 0 if run.status == "done" else 1


async def _demo_headless(args) -> int:
    settings = Settings.from_env(data_dir=args.data_dir)
    orch = Orchestrator(settings)
    run = orch.create("recorded demonstration run")
    try:
        await replay_into(run.bus, run)
    except ReplayUnavailable as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    for event in run.bus.events:
        if event["type"] == "worker.delta":
            continue
        print(f"{event['id']:>3} {event['type']}")
    print("\n" + (run.deliverable or ""))
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    Path(args.data_dir).mkdir(parents=True, exist_ok=True)
    if args.command == "serve":
        return _serve(args)
    if args.command == "run":
        return asyncio.run(_run_goal(args))
    if args.command == "demo":
        if args.headless:
            return asyncio.run(_demo_headless(args))
        return _serve(args, path="/?demo=1")
    return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
