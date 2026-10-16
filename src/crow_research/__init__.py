"""crow-research — a sequential agent pipeline over ACP v2.

The public surface is small on purpose:

* :class:`~crow_research.agent.OrchestratorAgent` — a v2 agent upstream, a v2
  client downstream, whose react loop is a workflow you supply.
* :class:`~crow_research.turn.Turn` — what that workflow is given: emit upstream,
  run a role, notice cancellation.
* :class:`~crow_research.peer.Peer` — one downstream agent connection.
* :func:`~crow_research.workflows.pipeline` — the loop: worker to verifier until
  sign-off on a complete list, then the explorer adds more work, then round
  again. :class:`~crow_research.todo.Todo` is the contract it runs on, and that
  contract is a JSON file the agents edit — ``.crow-research/todo.json`` under
  the working directory the session was opened on.
* :class:`~crow_research.roles.Role` — a name, an endpoint, and a brief.

The core is pure ACP v2: nothing here imports crow-cli at module load. Two edges
do, and only because they are the points where crow is the thing being used —
:mod:`crow_research.prompts` specializes crow's own system prompt rather than
writing a fresh one, and :mod:`crow_research.mcp` reads crow's config for the
tool supply (imported lazily, inside ``OrchestratorAgent._tool_supply``). The
default role endpoint is ``crow-cli acp2`` for the same reason, and any other v2
agent — spawned or already served on a socket — works exactly as well.
"""

from .agent import OrchestratorAgent, Session, Workflow, serve_http, serve_stdio
from .peer import Peer, PeerClient, PeerExited, TurnResult
from .roles import BUILTIN, EXPLORER, RESEARCHER, VERIFIER, WORKER, Role, resolve
from .todo import Item, Todo, state_path
from .transport import Endpoint, Http, Stdio, Ws, open_endpoint, parse_endpoint
from .turn import META_AGENT, Turn, TurnCancelled
from .workflows import COMPACT_PROMPT, chain, forever, pipeline, rounds, select, single

__all__ = [
    "BUILTIN",
    "COMPACT_PROMPT",
    "EXPLORER",
    "Endpoint",
    "Http",
    "Item",
    "META_AGENT",
    "OrchestratorAgent",
    "Peer",
    "PeerClient",
    "PeerExited",
    "RESEARCHER",
    "Role",
    "Session",
    "Stdio",
    "Todo",
    "Turn",
    "TurnCancelled",
    "TurnResult",
    "VERIFIER",
    "WORKER",
    "Workflow",
    "Ws",
    "chain",
    "forever",
    "open_endpoint",
    "parse_endpoint",
    "pipeline",
    "resolve",
    "rounds",
    "select",
    "serve_http",
    "serve_stdio",
    "single",
    "state_path",
]

__version__ = "0.1.0"


def main() -> None:
    from .cli import main as _main

    raise SystemExit(_main())
