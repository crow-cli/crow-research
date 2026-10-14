"""The command line: serve the orchestrator, or run one turn and watch it.

``serve`` is what an upstream client spawns — a v2 agent on stdio whose react
loop is whichever workflow the flags describe. ``run`` is the same thing seen
from the other end: it spawns ``serve`` as a child, drives it as a client, and
prints the stream, which makes the whole contraption testable without an IDE and
without an LLM (point a role at the stub agent in ``examples/``).

Deliberately no config file. A cast is ``--role worker="crow-cli acp2"`` repeated
once per agent, and a workflow is ``--workflow chain``; both fit on a command
line, and a research harness that needs a YAML file to start is a harness that
will not get started.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from typing import Any, Optional

from . import workflows
from .agent import OrchestratorAgent, Workflow, serve_stdio
from .peer import Peer, PeerClient
from .roles import BUILTIN, Role, resolve
from .transport import Stdio

DEFAULT_CHAIN = ("worker", "verifier")


def serve_argv(args: argparse.Namespace) -> list[str]:
    """The argv that reproduces this ``run`` as a ``serve`` child."""
    argv = [sys.executable, "-m", "crow_research", "serve", "--workflow", args.workflow]
    for spec in args.role:
        argv += ["--role", spec]
    if args.timeout is not None:
        argv += ["--timeout", str(args.timeout)]
    if args.compact:
        argv += ["--compact"]
    if args.rounds is not None:
        argv += ["--rounds", str(args.rounds)]
    if getattr(args, "autostart", None):  # `run` sends its own prompt, so it never autostarts
        argv += ["--autostart", args.autostart]
    return argv


def build_workflow(args: argparse.Namespace) -> Workflow:
    """Turn parsed flags into the callable the orchestrator runs."""
    roles = [resolve(spec, cwd=args.cwd) for spec in args.role] or [
        BUILTIN[name] for name in DEFAULT_CHAIN
    ]
    common = {"timeout": args.timeout, "compact": args.compact}
    if args.workflow == "single":
        inner: Workflow = workflows.single(roles[0], timeout=args.timeout)
    elif args.workflow == "chain":
        inner = workflows.chain(*roles, **common)
    else:
        raise SystemExit(f"unknown workflow {args.workflow!r}")
    if args.rounds is not None:
        return workflows.rounds(inner, args.rounds)
    return inner


class Printer(PeerClient):
    """The upstream face for ``run``: print what the orchestrator relays."""

    async def session_update(self, session_id: str, update: Any, **kwargs: Any) -> None:
        await super().session_update(session_id, update, **kwargs)
        agent = kwargs.get("crow/agent") or {}
        who = f"[{agent['title']}] " if agent.get("title") else ""
        kind = getattr(update, "session_update", "?")
        if kind in ("agent_message_chunk", "agent_message"):
            text = getattr(update, "content", None)
            print(f"{who}{getattr(text, 'text', '') or ''}", end="", flush=True)
        elif kind == "agent_thought_chunk":
            text = getattr(update, "content", None)
            print(f"{who}· {getattr(text, 'text', '') or ''}", end="", flush=True)
        elif kind == "tool_call_update":
            print(f"\n{who}⏳ {update.title or update.tool_call_id} [{update.status or 'pending'}]", flush=True)
        elif kind == "notice":
            print(f"\n{who}⚠ {update.title}" + (f" — {update.description}" if update.description else ""), flush=True)
        elif kind == "state_update":
            reason = getattr(update, "stop_reason", None)
            print(f"\n{who}state: {update.state}" + (f" ({reason})" if reason else ""), flush=True)
        elif kind in ("user_message", "user_message_chunk"):
            print(f"\n{who}you: {_blocks_text(getattr(update, 'content', None))}", flush=True)


def _blocks_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, (list, tuple)):
        return " ".join(_blocks_text(c) for c in content)
    return str(getattr(content, "text", "") or "")


async def _run(args: argparse.Namespace) -> int:
    role = Role(name="orchestrator", title="Orchestrator", endpoint=Stdio(tuple(serve_argv(args))))
    peer = Peer(role, client_factory=Printer)
    await peer.start(args.cwd or os.getcwd())
    try:
        result = await peer.send(args.prompt, timeout=args.timeout)
        print(f"\n— stop: {result.stop_reason} · {len(result.updates)} updates relayed")
        return 0 if result.stop_reason in (None, "end_turn") else 1
    finally:
        await peer.close()


def _serve(args: argparse.Namespace) -> int:
    agent = OrchestratorAgent(build_workflow(args), name=args.name, title=args.title, autostart=args.autostart)
    asyncio.run(serve_stdio(agent))
    return 0


def parser() -> argparse.ArgumentParser:
    top = argparse.ArgumentParser(prog="crow-research", description=__doc__.splitlines()[0])
    sub = top.add_subparsers(dest="command", required=True)

    def common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--workflow", choices=("chain", "single"), default="chain")
        p.add_argument(
            "--role",
            action="append",
            default=[],
            metavar="NAME[=ENDPOINT]",
            help=f"cast a role; endpoint is an argv, ws:// or http:// url. known: {', '.join(BUILTIN)}",
        )
        p.add_argument("--timeout", type=float, default=None, help="seconds to wait for one agent's turn")
        p.add_argument("--compact", action="store_true", help="ask each agent for an interagent summary to hand off")
        p.add_argument("--rounds", type=int, default=None, help="repeat the workflow N times (-1 = forever)")
        p.add_argument("--cwd", default=None)

    serve = sub.add_parser("serve", help="run the orchestrator as an ACP v2 agent on stdio")
    common(serve)
    serve.add_argument("--name", default="crow-research")
    serve.add_argument("--title", default="Crow Research Orchestrator")
    serve.add_argument("--autostart", default=None, metavar="PROMPT", help="begin working the moment a session opens")
    serve.set_defaults(func=_serve)

    run = sub.add_parser("run", help="spawn `serve` and drive one prompt, printing the relayed stream")
    common(run)
    run.add_argument("prompt")
    run.set_defaults(func=lambda a: asyncio.run(_run(a)))
    return top


def main(argv: Optional[list[str]] = None) -> int:
    args = parser().parse_args(argv)
    return args.func(args)
