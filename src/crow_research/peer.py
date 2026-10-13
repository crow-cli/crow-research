"""One downstream agent, driven as an ACP v2 client.

A :class:`Peer` owns a single connection to a single agent: spawn or dial it,
handshake, open a session, send turns, cancel them, tear it down. It is the
only place in this package that knows ``session/prompt`` is an *ack* — the
turn's real ending arrives later as an idle ``state_update``, so ``send`` is a
send plus a watch, and the watch is registered before the send because a turn
that ends fast can go idle before the ack comes back.

Everything the peer sees is handed to ``on_update`` before it is recorded, which
is the seam the orchestrator relays through: a workflow never has to touch the
wire to show a child's work to the user.
"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional, Union

from acp.experimental import v2

from .roles import Role
from .transport import Endpoint, Link, open_endpoint

#: Called for every ``session/update`` a peer receives, before it is recorded.
UpdateHook = Callable[[Any, Any], Union[None, Awaitable[None]]]

#: How much of a child's stderr to keep. The drain exists so the pipe cannot
#: fill and deadlock the child, not to archive its log.
STDERR_LINES = 200


class PeerExited(RuntimeError):
    """The child ended before its turn did — nothing will ever arrive now."""


@dataclass
class TurnResult:
    """What one turn produced."""

    stop_reason: Optional[str] = None
    text: str = ""
    thought: str = ""
    updates: list[Any] = field(default_factory=list)
    tool_calls: dict[str, Any] = field(default_factory=dict)

    @property
    def report(self) -> str:
        """The agent's own words — what a handoff passes on."""
        return self.text.strip()


def _text_of(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, (list, tuple)):
        return "".join(_text_of(item) for item in content)
    return str(getattr(content, "text", "") or "")


class PeerClient:
    """The face shown to a downstream agent: record, relay, and answer nothing
    that a workflow has not already decided.

    Permissions are auto-allowed and elicitations are cancelled, because a peer
    runs inside somebody else's turn and there is no human at *this* end of it —
    the human is upstream, watching a relay. An orchestrator that wants real
    consent forwards these instead: subclass and override.
    """

    def __init__(self, peer: "Peer") -> None:
        self.peer = peer
        self.stops: dict[str, asyncio.Queue] = {}
        self.running: set[str] = set()
        self.updates: list[Any] = []

    def watch(self, session_id: str) -> asyncio.Queue:
        """The queue this session's stop reasons land in. Called BEFORE sending."""
        return self.stops.setdefault(session_id, asyncio.Queue())

    async def session_update(self, session_id: str, update: Any, **kwargs: Any) -> None:
        self.updates.append(update)
        await self.peer.relay(update, kwargs)
        kind = getattr(update, "session_update", None)
        if kind == "agent_message_chunk":
            self.peer.turn.text += _text_of(getattr(update, "content", None))
        elif kind == "agent_thought_chunk":
            self.peer.turn.thought += _text_of(getattr(update, "content", None))
        elif kind == "tool_call_update":
            self.peer.turn.tool_calls[update.tool_call_id] = update
        elif kind == "state_update":
            state = getattr(update, "state", None)
            if state == "running":
                self.running.add(session_id)
            elif state == "idle" and session_id in self.running:
                self.running.discard(session_id)
                if session_id in self.stops:
                    self.stops[session_id].put_nowait(getattr(update, "stop_reason", None))

    async def request_permission(
        self, session_id: str, title: str, options: list[Any], **kwargs: Any
    ) -> Any:
        allowed = next(
            (o for o in options if getattr(o, "kind", None) in ("allow_once", "allow_always")),
            options[0] if options else None,
        )
        if allowed is None:
            return v2.schema.RequestPermissionResponse(
                outcome=v2.schema.CancelledPermissionOutcome()
            )
        return v2.schema.RequestPermissionResponse(
            outcome=v2.schema.SelectedPermissionOutcomeVariant(option_id=allowed.option_id)
        )

    async def create_elicitation(self, message: str, mode: str, **kwargs: Any) -> Any:
        return v2.schema.CancelElicitationResponse()


class Peer:
    """A live connection to one downstream agent, in the role it was cast in."""

    def __init__(
        self,
        role: Role,
        endpoint: Optional[Endpoint] = None,
        *,
        on_update: Optional[UpdateHook] = None,
        info: Optional[Any] = None,
        client_factory: Optional[Callable[["Peer"], "PeerClient"]] = None,
    ) -> None:
        self.role = role
        self.endpoint = endpoint or role.endpoint
        self.on_update = on_update
        self.info = info or v2.schema.Implementation(
            name="crow-research", title="Crow Research", version="0.1.0"
        )
        self.client = (client_factory or PeerClient)(self)
        self.conn: Optional[v2.ClientSideConnection] = None
        self.session_id: Optional[str] = None
        self.turn = TurnResult()
        self.stderr: list[str] = []
        self._link: Optional[Link] = None
        self._stack: Optional[Any] = None
        self._drainer: Optional[asyncio.Task] = None

    @property
    def started(self) -> bool:
        return self.conn is not None and self.session_id is not None

    async def relay(self, update: Any, meta: dict[str, Any]) -> None:
        """Hand one downstream update to the relay hook, if there is one."""
        if self.on_update is None:
            return
        result = self.on_update(update, meta)
        if asyncio.iscoroutine(result):
            await result

    async def start(self, cwd: str, *, mcp_servers: Optional[list] = None) -> str:
        """Connect, handshake, open a session. Returns the downstream session id."""
        self._stack = open_endpoint(self.endpoint, env=self.role.env)
        self._link = await self._stack.__aenter__()
        self.conn = v2.connect_to_agent(self.client, *self._link.args)
        if self._link.proc is not None:
            self._drainer = asyncio.create_task(self._drain_stderr(), name=f"peer-{self.role.name}-stderr")
        await self.conn.initialize(protocol_version=v2.PROTOCOL_VERSION, info=self.info)
        response = await self.conn.new_session(cwd=cwd, mcp_servers=mcp_servers or [])
        self.session_id = response.session_id
        return self.session_id

    async def _drain_stderr(self) -> None:
        """Keep the child's stderr pipe empty, or it blocks at 64KB and hangs."""
        stream = getattr(getattr(self._link, "proc", None), "stderr", None)
        if stream is None:
            return
        while True:
            line = await stream.readline()
            if not line:
                return
            self.stderr.append(line.decode(errors="replace"))
            del self.stderr[:-STDERR_LINES]

    async def send(self, text: str, *, timeout: Optional[float] = None) -> TurnResult:
        """One turn: send the prompt, watch for the idle, return what happened.

        ``timeout`` bounds the wait, not the child — on expiry the turn is still
        running downstream and :meth:`wait` can pick it back up.
        """
        if self.session_id is None:
            raise RuntimeError(f"peer {self.role.name} was never started")
        self.turn = TurnResult()
        stops = self.client.watch(self.session_id)
        await self.conn.prompt(
            session_id=self.session_id, prompt=[v2.schema.TextContentBlock(text=text)]
        )
        await self.wait(timeout=timeout)
        return self.turn

    async def wait(self, *, timeout: Optional[float] = None) -> Optional[str]:
        """Wait for the in-flight turn to end. Returns its stop reason."""
        stops = self.client.watch(self.session_id)
        stop = asyncio.ensure_future(stops.get())
        died = asyncio.ensure_future(self._exited())
        done, _ = await asyncio.wait({stop, died}, timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
        if stop in done:
            died.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await died
            self.turn.stop_reason = stop.result()
            return self.turn.stop_reason
        stop.cancel()
        died.cancel()
        for pending in (stop, died):
            with contextlib.suppress(asyncio.CancelledError):
                await pending
        if died.done() and not died.cancelled():
            raise PeerExited(self._exit_note())
        raise TimeoutError(f"peer {self.role.name} did not end its turn within {timeout}s")

    async def _exited(self) -> None:
        proc = getattr(self._link, "proc", None)
        if proc is None:
            await asyncio.Event().wait()  # a socket peer never "exits"; only the stop queue ends a wait
            return
        await proc.wait()

    def _exit_note(self) -> str:
        proc = getattr(self._link, "proc", None)
        code = getattr(proc, "returncode", None)
        note = f"agent {self.role.name} exited with code {code}"
        tail = [line.strip() for line in self.stderr[-3:] if line.strip()]
        return note + ("; its last words were: " + " | ".join(tail) if tail else "")

    async def cancel(self) -> None:
        """Cancel the in-flight turn. The session survives it, so a redirect is
        a cancel followed by another ``send`` on the same peer."""
        if self.conn is not None and self.session_id is not None:
            await self.conn.cancel_session(session_id=self.session_id)

    async def close(self) -> None:
        """Tear down. Safe on a peer that never started."""
        with contextlib.suppress(Exception):
            if self.conn is not None:
                await self.conn.close()
        if self._drainer is not None:
            self._drainer.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._drainer
            self._drainer = None
        if self._stack is not None:
            with contextlib.suppress(Exception):
                await self._stack.__aexit__(None, None, None)
            self._stack = None
        self.conn = None
        self._link = None

    async def __aenter__(self) -> "Peer":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.close()
