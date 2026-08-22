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

from .config import VERSION, BindRefused, Settings, agents_dir, skills_dir, validate_bind
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
    run.add_argument(
        "--dod",
        dest="extra_dod",
        action="append",
        default=[],
        metavar="CRITERION",
        help="acceptance criterion the Critic enforces and the Worker never sees (repeatable)",
    )
    run.add_argument("--extra-dod", dest="extra_dod", action="append", help=argparse.SUPPRESS)
    run.add_argument(
        "--agent",
        dest="agent",
        default="",
        metavar="SLUG",
        help="hand this goal to a named agent from the roster (see `omniagentos agents list`)",
    )
    run.add_argument("--json", action="store_true", help="print the run summary as JSON")

    agents = sub.add_parser("agents", help="the agent roster")
    agents_sub = agents.add_subparsers(dest="agents_command", required=True)
    agents_list = agents_sub.add_parser("list", help="list every agent in the roster")
    agents_list.add_argument("--data-dir", default="var")
    agents_list.add_argument("--json", action="store_true")
    agents_show = agents_sub.add_parser("show", help="show one agent in full")
    agents_show.add_argument("slug")
    agents_show.add_argument("--data-dir", default="var")
    agents_show.add_argument("--json", action="store_true")

    demo = sub.add_parser("demo", help="replay a recorded run (no API key needed)")
    # NOT 8486. `omniagentos demo` starts its own server, and the one moment an
    # operator reaches for it live is the moment a server is already on 8486 —
    # so the documented recovery used to die on "Address already in use". The
    # in-dashboard "Replay demo" button is the mid-show fallback; this command is
    # for rehearsal and for the case where the real server is gone.
    demo.add_argument("--port", type=int, default=DEMO_PORT)
    demo.add_argument("--host", default="127.0.0.1")
    demo.add_argument("--data-dir", default="var")
    demo.add_argument("--headless", action="store_true", help="print the replay to stdout instead of serving")
    return p


DEMO_PORT = 8487


def _serve(args, path: str = "/", fallback_to_any_port: bool = False) -> int:
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
        if not fallback_to_any_port:
            sock.close()
            print(f"error: cannot bind {args.host}:{args.port}: {exc}", file=sys.stderr)
            return 2
        # The replay is the thing the operator wants; the port number is not.
        # Refusing to start because a port is busy fails the one command whose
        # whole job is to work when something else has gone wrong.
        print(
            f"note: {args.host}:{args.port} is in use; taking an ephemeral port instead. "
            "If that is your own server, the dashboard's 'Replay demo' button plays the "
            "same recording there without starting a second process.",
            file=sys.stderr,
        )
        try:
            sock.bind((args.host, 0))
        except OSError as exc2:
            sock.close()
            print(f"error: cannot bind {args.host}: {exc2}", file=sys.stderr)
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
    orch.load_roster(agents_dir())
    try:
        run = orch.create(args.goal, args.max_rounds, args.extra_dod, agent_id=getattr(args, "agent", ""))
    except ValueError as exc:
        # A named agent that is not there is not a smaller version of what you
        # asked for, so the run does not start.
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if run.agent is not None:
        print(f"[agent] {run.agent.name} ({run.agent.slug})", file=sys.stderr)
    printed: dict[str, bool] = {}

    def echo(event: dict) -> None:
        etype, payload = event["type"], event["payload"]
        if etype == "worker.delta":
            sys.stdout.write(payload.get("text", ""))
            sys.stdout.flush()
            printed[payload.get("task_id", "")] = True
        elif etype == "worker.reset":
            # The tokens already on this terminal belong to an attempt that died.
            # Say so, rather than letting the next attempt concatenate onto them.
            print(
                f"\n[worker {payload.get('task_id')}] stream dropped "
                f"({payload.get('reason')}) — restarting this deliverable\n",
                file=sys.stderr,
            )
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
    # `done` is not the same as `signed off`. A run the Verifier rejected can
    # still reach a terminal state, and exiting 0 on it tells every supervisor
    # and every "did the demo work?" script that it passed.
    if run.status == "done" and run.verified is True:
        return 0
    print(
        f"not verified: status={run.status} verified={run.verified} "
        f"error_tag={run.error_tag or '-'}",
        file=sys.stderr,
    )
    return 1


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
    # A truncated or rejected recording is not a successful demonstration, and
    # exit 0 was the only thing anything downstream looked at.
    if run.status == "done" and run.verified is True:
        return 0
    print(
        f"replay did not finish verified: status={run.status} error_tag={run.error_tag or '-'}",
        file=sys.stderr,
    )
    return 1


def _agents(args) -> int:
    from .agents import load_agents
    from .skills import load_skills

    roster = load_agents(agents_dir(), library=load_skills(skills_dir()))
    if args.agents_command == "list":
        listed = roster.as_dict()["agents"]
        if args.json:
            print(json.dumps(redact(listed), indent=2, default=str))
            return 0
        if not listed:
            print("no agents in the roster")
            return 0
        for agent in listed:
            mark = "  " if agent["enabled"] else "✕ "
            skills = ", ".join(agent["skills"]) or "router's choice"
            builtin = " [built-in]" if agent["builtin"] else ""
            print(f"{mark}{agent['id']:<24} {agent['name']}{builtin}")
            print(f"  {'':<24} {agent['title'] or '-'} · skills: {skills}")
            for problem in agent["errors"]:
                print(f"  {'':<24} ! {problem}")
        return 0

    agent = roster.by_id(args.slug)
    if agent is None:
        print(f"error: no agent {args.slug!r} in {agents_dir()}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(redact(agent.as_dict()), indent=2, default=str))
        return 0
    print(f"{agent.name} ({agent.slug})" + (" [built-in]" if agent.builtin else ""))
    print(f"title:   {agent.title or '-'}")
    chosen = ", ".join(agent.skills) or "router's choice"
    print(f"skills:  {chosen}")
    print(f"tools:   {', '.join(agent.tools) or '(none)'}")
    print(f"memory:  {agent.memory_scope or agent.slug}")
    print(f"enabled: {agent.enabled}")
    for problem in agent.errors:
        print(f"  ! {problem}")
    print(f"\npersona:\n{agent.persona or '-'}")
    print(f"\nstanding instructions:\n{agent.body or '-'}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    Path(args.data_dir).mkdir(parents=True, exist_ok=True)
    if args.command == "serve":
        return _serve(args)
    if args.command == "run":
        return asyncio.run(_run_goal(args))
    if args.command == "agents":
        return _agents(args)
    if args.command == "demo":
        if args.headless:
            return asyncio.run(_demo_headless(args))
        return _serve(args, path="/?demo=1", fallback_to_any_port=True)
    return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
