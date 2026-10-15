#!/usr/bin/env python
"""The pipeline agent — one file, and a client spawns it like any other ACP agent.

    python agents/orchestrator_agent.py                       # stdio, perpetual
    python agents/orchestrator_agent.py --model glm-5.3-flash # (the default)

To the upstream client this is one agent: one session, one update stream, an
``initialize`` that answers protocol 2. In ``session/prompt`` it is a *client* —
it spawns the agent files next to it, drives them in sequence, and relays every
``session/update`` they emit up that single stream, stamped with
``_meta["crow/agent"]`` so the UI can say which of them is speaking.

The sequence is the point:

    worker -> verifier -> worker -> verifier -> ...   until VERDICT: PASS and the
                                                      todo list is complete
    -> explorer                                       adds more items to the todo
    -> worker -> ...                                  and it goes round again

It does not stop. ``session/cancel`` stops it.

The prompt the client sends is where the work comes from: it seeds the todo list
(``ADD <text>`` lines, or the whole prompt as one item), and from then on the
agents own the list — the worker claims and settles items, the verifier reopens
the ones whose claims did not reproduce, the explorer adds what the finished work
made possible.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path
from typing import Optional

from crow_research.agent import OrchestratorAgent, serve_stdio
from crow_research.roles import BUILTIN, Role
from crow_research.todo import Todo
from crow_research.transport import Stdio
from crow_research import workflows

#: This file's directory: where the agents in the pipeline live.
AGENTS = Path(__file__).resolve().parent

#: The local model the pipeline runs on by default.
DEFAULT_MODEL = "glm-5.3-flash"


def agent_role(name: str, model: Optional[str]) -> Role:
    """One pipeline agent: its handoff framing, and the argv that runs it.

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
    """Assemble the pipeline. The todo outlives a turn: the explorer's items are
    what the next round works on, so it is built here, not per prompt."""
    worker = agent_role("worker", args.model)
    verifier = agent_role("verifier", args.model)
    explorer = agent_role("explorer", args.model)
    # The todo outlives a turn — the explorer's items are what the next round
    # works on — so it is built here, once, and the prompt that arrives on
    # ``session/prompt`` seeds it.
    todo = Todo()
    return OrchestratorAgent(
        workflows.pipeline(worker, verifier, explorer, todo, timeout=args.timeout),
        name=args.name,
        title=args.title,
        mcp_servers=None,  # None -> crow's own configured tool supply, per child
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--model", default=DEFAULT_MODEL, help=f"model every agent runs on (default {DEFAULT_MODEL})")
    parser.add_argument("--timeout", type=float, default=None, help="seconds to wait for one agent's turn")
    parser.add_argument("--name", default="crow-research")
    parser.add_argument("--title", default="Crow Research Pipeline")
    parser.add_argument("--debug", action="store_true", help="log to stderr (the parent client drains it)")
    args = parser.parse_args()
    if args.debug:
        logging.basicConfig(level=logging.DEBUG, stream=sys.stderr, format="%(levelname)s %(name)s: %(message)s")
    asyncio.run(serve_stdio(build(args)))


if __name__ == "__main__":
    main()
