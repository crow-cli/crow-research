"""crow-research — a multi-agent ACP v2 orchestrator, independent of crow-cli.

The public surface is small on purpose:

* :class:`~crow_research.agent.OrchestratorAgent` — a v2 agent upstream, a v2
  client downstream, whose react loop is a workflow you supply.
* :class:`~crow_research.turn.Turn` — what that workflow is given: emit upstream,
  run a role, notice cancellation.
* :class:`~crow_research.peer.Peer` — one downstream agent connection.
* :mod:`crow_research.workflows` — the predefined loops (single, chain, rounds).
* :class:`~crow_research.roles.Role` — a name, an endpoint, and a brief.

Nothing imports crow-cli. The default role endpoint happens to be
``crow-cli acp2`` because that is the agent on this machine, and any other v2
agent — spawned or already served on a socket — works exactly as well.
"""

from .agent import OrchestratorAgent, Session, Workflow, serve_stdio
from .peer import Peer, PeerClient, PeerExited, TurnResult
from .roles import BUILTIN, EXPLORER, RESEARCHER, VERIFIER, WORKER, Role, resolve
from .transport import Endpoint, Http, Stdio, Ws, open_endpoint, parse_endpoint
from .turn import META_AGENT, Turn, TurnCancelled
from .workflows import COMPACT_PROMPT, chain, forever, rounds, select, single

__all__ = [
    "BUILTIN",
    "COMPACT_PROMPT",
    "EXPLORER",
    "Endpoint",
    "Http",
    "META_AGENT",
    "OrchestratorAgent",
    "Peer",
    "PeerClient",
    "PeerExited",
    "RESEARCHER",
    "Role",
    "Session",
    "Stdio",
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
    "resolve",
    "rounds",
    "select",
    "serve_stdio",
    "single",
]

__version__ = "0.1.0"


def main() -> None:
    from .cli import main as _main

    raise SystemExit(_main())
