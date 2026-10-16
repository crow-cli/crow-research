#!/usr/bin/env python
"""The campaign agent — one file, and a client spawns it like any other ACP agent.

    python agents/orchestrator_agent.py                       # stdio, perpetual
    python agents/orchestrator_agent.py --model glm-5.3-flash # (the default)
    python agents/orchestrator_agent.py --http --port 2772    # served, not spawned

stdio is what a client spawns; ``--http`` is what a client dials —
``ws://127.0.0.1:2772/acp``, Streamable HTTP + WebSocket, one agent per
connection. Same campaign either way, and neither one stops on its own.

To the upstream client this is one agent: one session, one update stream, an
``initialize`` that answers protocol 2. In ``session/prompt`` it is a *client* —
it spawns the agent files next to it, drives them in sequence, and relays every
``session/update`` they emit up that single stream, stamped with
``_meta["crow/agent"]`` so the UI can say which of them is speaking.

The sequence is the point:

    drafter                                           once, and only while the
                                                      state file has no task
    worker -> verifier -> worker -> verifier -> ...   until the verdict is PASS
                                                      and the todo list is done
    -> explorer                                       adds ideas, and the items
                                                      they need executed
    -> writer                                         when a signed-off round's
                                                      explorer added nothing
    -> worker -> ...                                  and it goes round again

It does not stop. ``session/cancel`` stops it.

The prompt the client sends is where the work comes from: it becomes the brief
the drafter turns into a task — a goal, the rubrics every later round is graded
by, the evaluator that scores a candidate, and a baseline to beat — and the
first item on the todo list. From then on the agents own the document. It is one
JSON file, ``.crow-research/todo.json`` under the working directory the client
opened the session on, that the five of them edit with the tools they already
have and that this file only ever seeds and reads. The worker claims items and
settles them, recording each candidate it built as an attempt; the verifier
reopens the ones whose claims did not reproduce, fills in the rubric scaffold
and signs off in the same document; the explorer adds the ideas the finished
work made possible, with the rubrics the next verifier will check; the writer
publishes what the campaign actually achieved, every number sourced to the file
that shows it.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path
from typing import Optional

from crow_research.agent import OrchestratorAgent, serve_http, serve_stdio
from crow_research.roles import BUILTIN, Role
from crow_research.todo import Todo
from crow_research.transport import Stdio
from crow_research import workflows

#: This file's directory: where the agents in the campaign live.
AGENTS = Path(__file__).resolve().parent

#: The local model the campaign runs on by default.
DEFAULT_MODEL = "glm-5.3-flash"

#: What ``--http`` binds by default. Not 2771: that is ``crow-cli acp2``'s own
#: default, and the two are exactly the agents most likely to be running at once.
DEFAULT_PORT = 2772


def agent_role(name: str, model: Optional[str]) -> Role:
    """One campaign agent: its handoff framing, and the argv that runs it.

    How you run the file *is* the acp server configuration, so this is the whole
    of the wiring. ``sys.executable`` rather than ``python`` so the child lands
    in the environment that spawned this one, with ``crow_cli`` and
    ``crow_research`` importable.
    """
    script = AGENTS / f"{name}_agent.py"
    if not script.exists():
        raise SystemExit(f"no such agent file: {script}")
    argv = [sys.executable, str(script)]
    if model:
        argv += ["--model", model]
    base = BUILTIN.get(name) or Role(name=name, title=name.title())
    return Role(
        name=base.name,
        brief=base.brief,
        endpoint=Stdio(tuple(argv)),
        title=base.title,
        model=model,
        handoff=base.handoff,
    )


def build(args: argparse.Namespace) -> OrchestratorAgent:
    """Assemble the campaign: five agents, one state file, no clock."""
    drafter = agent_role("drafter", args.model)
    worker = agent_role("worker", args.model)
    verifier = agent_role("verifier", args.model)
    explorer = agent_role("explorer", args.model)
    writer = agent_role("writer", args.model)
    # The campaign outlives a turn, and it lives in a file rather than in this
    # process: ``state_path`` is left unset so the workflow puts it under the
    # working directory the client opened the session on — the directory the
    # agents are actually working in. The prompt that arrives on
    # ``session/prompt`` seeds it. The ``Todo()`` is the seed a Python caller
    # supplies instead, and it is empty here.
    return OrchestratorAgent(
        workflows.scientist(drafter, worker, verifier, explorer, writer, Todo()),
        name=args.name,
        title=args.title,
        mcp_servers=None,  # None -> crow's own configured tool supply, per child
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--model", default=DEFAULT_MODEL, help=f"model every agent runs on (default {DEFAULT_MODEL})")
    parser.add_argument("--name", default="crow-research")
    parser.add_argument("--title", default="Crow Research Pipeline")
    parser.add_argument("--http", action="store_true",
                        help="serve over Streamable HTTP + WebSocket instead of stdio")
    parser.add_argument("--host", default="127.0.0.1", help="bind address for --http")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT,
                        help=f"bind port for --http; the client dials ws://HOST:PORT/acp (default {DEFAULT_PORT})")
    parser.add_argument("--debug", action="store_true", help="log to stderr (the parent client drains it)")
    args = parser.parse_args()
    if args.debug:
        logging.basicConfig(level=logging.DEBUG, stream=sys.stderr, format="%(levelname)s %(name)s: %(message)s")
    if args.http:
        # A factory, not an instance: the router builds one agent per connection.
        asyncio.run(serve_http(lambda conn: build(args), args.host, args.port))
    else:
        asyncio.run(serve_stdio(build(args)))


if __name__ == "__main__":
    main()
