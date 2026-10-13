"""The turn: everything a workflow is given, and nothing it has to know.

A workflow is an async callable that receives a :class:`Turn` and returns a
stop reason. That is the whole contract, and it is the seam the vision asked
for — "initialized with a callable that defines what happens on
``session/prompt``" — because the callable gets exactly three powers:

* **emit** — put an update on the upstream client's stream, tagged with the
  role that produced it, so one interface shows the work of many agents;
* **run** — hand a prompt to a role and get its report back, with every update
  it produced already relayed upstream while it worked;
* **cancelled** — the event ``session/cancel`` sets, checked between steps and
  raised out of a wait so a workflow that ignores it still stops.

Nothing here knows what a worker or a verifier is. The workflow decides.
"""

from __future__ import annotations

import asyncio
import contextlib
import itertools
from dataclasses import dataclass, field
from typing import Any, Optional

from acp.experimental import v2

from .peer import Peer, TurnResult
from .roles import Role

#: ``_meta`` key the orchestrator stamps on every relayed update. An upstream
#: client reads it to know which agent is speaking; a client that ignores it
#: still renders a perfectly ordinary stream.
META_AGENT = "crow/agent"

#: Downstream update kinds that are the child's business, not the user's. A
#: child's ``state_update`` says *its* turn ended — relaying it would tell the
#: upstream client the whole orchestration was over, mid-flight.
DEFAULT_DROP = frozenset({"state_update"})

#: Seconds a cancelled child gets to report its own idle before we let go of it.
GRACE = 5.0


class TurnCancelled(asyncio.CancelledError):
    """Raised inside a workflow when the upstream client cancelled the turn."""


@dataclass
class Step:
    """One role's contribution to a turn — what a handoff carries forward."""

    role: Role
    prompt: str
    result: TurnResult

    @property
    def report(self) -> str:
        return self.result.report or f"({self.role.name} produced no message)"


class Turn:
    """A single upstream ``session/prompt``, in flight."""

    def __init__(
        self,
        session_id: str,
        blocks: list[Any],
        emit: Any,
        *,
        cwd: str,
        drop: frozenset[str] = DEFAULT_DROP,
        mcp_servers: Optional[list] = None,
    ) -> None:
        self.session_id = session_id
        self.blocks = blocks
        self.cwd = cwd
        self.prompt = "".join(
            str(getattr(b, "text", "") or "") for b in blocks
        ).strip()
        self.cancelled = asyncio.Event()
        self.steps: list[Step] = []
        self.peers: dict[str, Peer] = {}
        self._emit = emit
        self._drop = drop
        self._mcp_servers = mcp_servers or []
        self._ids = itertools.count(1)

    # -- upstream -------------------------------------------------------

    async def emit(self, update: Any, **meta: Any) -> None:
        """Put one update on the upstream stream, with ``_meta`` attached."""
        self._check()
        await self._emit(self.session_id, update, meta)

    async def say(self, text: str, *, role: Optional[Role] = None) -> None:
        """Speak as the orchestrator (or in a role's name) to the user."""
        if not text:
            return
        await self.emit(
            v2.schema.AgentMessageChunk(
                message_id=f"orch-{next(self._ids)}",
                content=v2.schema.TextContentBlock(text=text),
            ),
            **self._tag(role),
        )

    async def notice(self, title: str, *, severity: str = "info", description: Optional[str] = None) -> None:
        """A live advisory — the transition marker the vision asks for."""
        await self.emit(
            v2.schema.SessionNotice(severity=severity, title=title, description=description),
        )

    def _tag(self, role: Optional[Role]) -> dict[str, Any]:
        return {META_AGENT: {"role": role.name, "title": role.label}} if role else {}

    # -- downstream -----------------------------------------------------

    async def peer(self, role: Role, *, reuse: bool = True) -> Peer:
        """The live peer for a role — started on first ask, cached after.

        Caching is what makes a multi-round workflow cheap: the second round
        reuses the same agent process and the same downstream session, so the
        verifier still has the context it built in round one.
        """
        self._check()
        existing = self.peers.get(role.name)
        if existing is not None and reuse:
            return existing
        if existing is not None:
            await existing.close()
        peer = Peer(role, on_update=self._relay_for(role))
        # Registered BEFORE it is started: a cancel that lands mid-handshake has
        # to find the peer in order to cancel it, and a peer that is invisible
        # to ``cancel_peers`` is a child left running after the turn ended.
        self.peers[role.name] = peer
        try:
            await peer.start(self.cwd, mcp_servers=self._mcp_servers)
        except BaseException:
            self.peers.pop(role.name, None)
            await peer.close()
            raise
        return peer

    def _relay_for(self, role: Role):
        """The hook a role's peer relays through: tag it, drop the child's own
        state machine, and forward everything else upstream verbatim."""

        async def relay(update: Any, meta: dict[str, Any]) -> None:
            if getattr(update, "session_update", None) in self._drop:
                return
            peer = self.peers.get(role.name)
            tag = {**self._tag(role), META_AGENT + "/session": peer.session_id if peer else None}
            if self.cancelled.is_set():
                return
            # The child's own ``_meta`` first, ours last: a relayed update is
            # *about* the role that produced it, and nothing downstream gets to
            # overwrite that.
            await self._emit(self.session_id, update, {**(meta or {}), **tag})

        return relay

    async def run(
        self, role: Role, text: str, *, timeout: Optional[float] = None, reuse: bool = True
    ) -> TurnResult:
        """Hand ``text`` to ``role`` and wait for its turn to end.

        The report comes back; the work went upstream while it happened.
        """
        peer = await self.peer(role, reuse=reuse)
        self._check()
        # The race is the cancellation seam. ``peer.send`` ends when the child
        # says it is idle, which a child that is stuck will never do; the cancel
        # event ends when the user says stop. Whichever lands first wins, and
        # when it is the cancel, the child is told too — then given a moment to
        # report its own cancelled idle so the relay does not cut off mid-word.
        send = asyncio.ensure_future(peer.send(text, timeout=timeout))
        stopped = asyncio.ensure_future(self.cancelled.wait())
        done, _ = await asyncio.wait({send, stopped}, return_when=asyncio.FIRST_COMPLETED)
        if send not in done:
            await peer.cancel()
            with contextlib.suppress(BaseException):
                await asyncio.wait_for(asyncio.shield(send), timeout=GRACE)
            send.cancel()
            stopped.cancel()
            raise TurnCancelled(f"turn {self.session_id} cancelled during {role.name}")
        stopped.cancel()
        result = send.result()
        self.steps.append(Step(role=role, prompt=text, result=result))
        return result

    async def close(self) -> None:
        """Tear every peer down. Called by the agent when the turn ends."""
        for peer in list(self.peers.values()):
            await peer.close()
        self.peers.clear()

    # -- cancellation ---------------------------------------------------

    def cancel(self) -> None:
        """``session/cancel`` landed: stop the workflow, then the children."""
        self.cancelled.set()

    def _check(self) -> None:
        if self.cancelled.is_set():
            raise TurnCancelled(f"turn {self.session_id} was cancelled")

    async def cancel_peers(self) -> None:
        for peer in self.peers.values():
            try:
                await peer.cancel()
            except Exception:  # a peer that is already gone cannot be cancelled
                pass
