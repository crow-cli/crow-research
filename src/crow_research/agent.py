"""The orchestrator: an ACP v2 *agent* upstream, an ACP v2 *client* downstream.

This is the object the vision keeps circling — "a fully functioning AcpAgent but
we overwrite the normal react loop of a standard agent with our workflow." To
the user's client it is one agent with one session and one update stream. Inside,
it is a sequence of specialized agents whose work is relayed through that single
stream, each update stamped with the role that produced it.

Two v2 facts shape the whole file:

**``session/prompt`` is an ack.** The handler echoes the user's message, returns
a ``message_id``, and the turn runs as a task. The turn's ending is an
``idle`` ``state_update`` this agent emits — never the prompt response. An
orchestrator that returned the workflow's result from ``prompt`` would be
speaking v1 with v2 grammar, and the upstream client would sit waiting for a
state change that never came.

**One foreground turn per session.** A second prompt while one is running is
refused, because the update stream is session-wide and carries no turn id: two
interleaved turns would be indistinguishable to everybody downstream.
"""

from __future__ import annotations

import asyncio
import contextlib
import itertools
import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

from acp.experimental import v2

from .roles import Role
from .turn import Turn, TurnCancelled

log = logging.getLogger("crow_research")

#: What a workflow does with a turn. Returns the stop reason to report upstream,
#: or None for the ordinary ``end_turn``.
Workflow = Callable[[Turn], Awaitable[Optional[str]]]


@dataclass
class Session:
    """One upstream session: its environment, and the turn in flight, if any."""

    session_id: str
    cwd: str
    additional_directories: list[str] = field(default_factory=list)
    mcp_servers: list[Any] = field(default_factory=list)
    title: Optional[str] = None
    turn: Optional[Turn] = None
    task: Optional[asyncio.Task] = None
    messages: int = 0

    @property
    def busy(self) -> bool:
        return self.task is not None and not self.task.done()


class OrchestratorAgent:
    """A v2 agent whose react loop is a workflow you supplied."""

    def __init__(
        self,
        workflow: Workflow,
        *,
        name: str = "crow-research",
        title: str = "Crow Research Orchestrator",
        version: str = "0.1.0",
        default_cwd: Optional[str] = None,
        autostart: Optional[str] = None,
    ) -> None:
        """``autostart`` is the always-on switch: a prompt to begin working on
        the moment a session opens, without waiting for anybody to send one. v2
        is what makes that legal — updates may flow at any time and the agent may
        initiate — and it is what turns a workflow from something a user triggers
        into something that is simply running."""
        self.workflow = workflow
        self.name = name
        self.title = title
        self.version = version
        self.default_cwd = default_cwd
        self.autostart = autostart
        self.conn: Optional[v2.AgentSideConnection] = None
        self.sessions: dict[str, Session] = {}
        self._ids = itertools.count(1)

    def on_connect(self, conn: v2.AgentSideConnection) -> None:
        """The SDK hands the connection over before any traffic — hold it, since
        emitting an update is the only way this agent ever says anything."""
        self.conn = conn

    # -- handshake ------------------------------------------------------

    async def initialize(
        self,
        protocol_version: int,
        info: v2.schema.Implementation,
        capabilities: Optional[v2.schema.ClientCapabilities] = None,
        **kwargs: Any,
    ) -> v2.schema.InitializeResponse:
        log.info("initialize: client %s %s", info.name, info.version)
        return v2.schema.InitializeResponse(
            protocol_version=v2.PROTOCOL_VERSION,
            info=v2.schema.Implementation(name=self.name, title=self.title, version=self.version),
            capabilities=v2.schema.AgentCapabilities(session=v2.schema.SessionCapabilities()),
        )

    # -- sessions -------------------------------------------------------

    async def new_session(
        self,
        cwd: str,
        additional_directories: Optional[list[str]] = None,
        mcp_servers: Optional[list[Any]] = None,
        **kwargs: Any,
    ) -> v2.schema.NewSessionResponse:
        session_id = f"orch-{next(self._ids)}"
        session = Session(
            session_id=session_id,
            cwd=cwd,
            additional_directories=list(additional_directories or ()),
            mcp_servers=list(mcp_servers or ()),
        )
        self.sessions[session_id] = session
        log.info("session/new: %s cwd=%s", session_id, cwd)
        if self.autostart:
            # A task, not an await: the turn must not delay the response that
            # tells the client the session exists.
            asyncio.create_task(
                self._start_turn(session, [v2.schema.TextContentBlock(text=self.autostart)], autostart=True),
                name=f"autostart-{session_id}",
            )
        return v2.schema.NewSessionResponse(session_id=session_id)

    async def list_sessions(
        self, cwd: Optional[str] = None, cursor: Optional[str] = None, **kwargs: Any
    ) -> v2.schema.ListSessionsResponse:
        sessions = [
            v2.schema.SessionInfo(
                session_id=s.session_id,
                cwd=s.cwd,
                additional_directories=s.additional_directories or None,
                title=s.title,
            )
            for s in self.sessions.values()
            if cwd is None or s.cwd == cwd
        ]
        return v2.schema.ListSessionsResponse(sessions=sessions)

    async def resume_session(
        self,
        session_id: str,
        cwd: str,
        additional_directories: Optional[list[str]] = None,
        mcp_servers: Optional[list[Any]] = None,
        replay_from: Any = None,
        **kwargs: Any,
    ) -> v2.schema.ResumeSessionResponse:
        """Re-attach to a session id we know. History is NOT replayed: an
        orchestrator's transcript is the union of several agents' work and the
        children own theirs — resume here means "keep driving", not "re-read"."""
        session = self.sessions.get(session_id)
        if session is None:
            session = Session(session_id=session_id, cwd=cwd)
            self.sessions[session_id] = session
        session.cwd = cwd
        session.mcp_servers = list(mcp_servers or session.mcp_servers)
        return v2.schema.ResumeSessionResponse()

    async def close_session(self, session_id: str, **kwargs: Any) -> v2.schema.CloseSessionResponse:
        session = self.sessions.pop(session_id, None)
        if session is not None and session.busy:
            if session.turn is not None:
                session.turn.cancel()
                await session.turn.cancel_peers()
            session.task.cancel()
            with contextlib.suppress(BaseException):
                await asyncio.shield(session.task)
        return v2.schema.CloseSessionResponse()

    async def set_config_option(
        self, config_id: str, session_id: str, value: Any, *, type: Optional[str] = None, **kwargs: Any
    ) -> v2.schema.SetSessionConfigOptionResponse:
        """Accepted and ignored. A workflow's character comes from the roles it
        was built with, not from a knob turned mid-session."""
        return v2.schema.SetSessionConfigOptionResponse(config_options=[])

    # -- the turn -------------------------------------------------------

    async def prompt(
        self, session_id: str, prompt: list[Any], **kwargs: Any
    ) -> v2.schema.PromptResponse:
        session = self.sessions.get(session_id)
        if session is None:
            raise ValueError(f"unknown session {session_id!r}")
        if session.busy:
            raise RuntimeError(f"session {session_id} already has a turn in flight")

        message_id = await self._start_turn(session, prompt)
        return v2.schema.PromptResponse(message_id=message_id)

    async def _start_turn(self, session: Session, blocks: list[Any], *, autostart: bool = False) -> str:
        """Echo the user message, then run the workflow as a task.

        Echoing is not cosmetic: v2 requires the ``message_id`` in the prompt
        response to appear in a user-message update, so every observer sees
        where the prompt landed — including one that is only reading the stream
        and never sent anything. An autostarted turn is stamped as such, because
        nobody typed it.
        """
        session.messages += 1
        message_id = f"{session.session_id}-user-{session.messages}"
        await self._emit(
            session.session_id,
            v2.schema.UserMessageUpdate(message_id=message_id, content=list(blocks)),
            {"crow/autostart": True} if autostart else None,
        )
        turn = Turn(
            session.session_id,
            list(blocks),
            self._emit,
            cwd=session.cwd,
            mcp_servers=session.mcp_servers,
        )
        session.turn = turn
        session.task = asyncio.create_task(self._run(session, turn), name=f"turn-{session.session_id}")
        return message_id

    async def _run(self, session: Session, turn: Turn) -> None:
        """The overwritten react loop: announce, work, report, clean up."""
        await self._emit(session.session_id, v2.schema.RunningSessionStateUpdate())
        stop_reason: Optional[str] = "end_turn"
        try:
            stop_reason = await self.workflow(turn) or "end_turn"
        except TurnCancelled:
            stop_reason = "cancelled"
        except asyncio.CancelledError:
            # Swallowed, not re-raised: this task is ours, nobody is awaiting a
            # result from it, and re-raising would make every await in the
            # finally below raise again — including the idle the upstream client
            # is waiting for. A cancelled turn still has to report that it ended.
            stop_reason = "cancelled"
        except Exception as error:  # a workflow that blew up is the user's news, not a silent hang
            log.exception("workflow failed")
            with contextlib.suppress(Exception):
                await self._emit(
                    session.session_id,
                    v2.schema.SessionNotice(
                        severity="error",
                        title=f"{type(error).__name__}: {error}",
                        description="the workflow raised; the turn is over",
                    ),
                )
            stop_reason = "refusal"
        finally:
            with contextlib.suppress(Exception):
                await turn.close()
            session.turn = None
            session.task = None
            # Shielded: when the task itself was cancelled, an unshielded await
            # here raises before the write, and the client waits forever for an
            # idle that was already decided.
            with contextlib.suppress(BaseException):
                await asyncio.shield(
                    self._emit(session.session_id, v2.schema.IdleSessionStateUpdate(stop_reason=stop_reason))
                )

    async def cancel_session(self, session_id: str, **kwargs: Any) -> None:
        """``session/cancel``: stop the workflow, then the children it started.

        Order matters. Cancelling the peers first would let the workflow take
        another step before it noticed; setting the event first means every
        ``_check`` from here on raises, and the children are told afterwards so
        their in-flight turns end too. The required ``idle`` with
        ``stop_reason: cancelled`` is emitted by ``_run``'s finally, not here.
        """
        session = self.sessions.get(session_id)
        if session is None or session.turn is None:
            return
        log.info("session/cancel: %s", session_id)
        session.turn.cancel()
        await session.turn.cancel_peers()

    # -- plumbing -------------------------------------------------------

    async def _emit(self, session_id: str, update: Any, meta: Optional[dict[str, Any]] = None) -> None:
        if self.conn is None:
            raise RuntimeError("agent is not connected")
        await self.conn.session_update(session_id=session_id, update=update, **(meta or {}))


async def serve_stdio(agent: OrchestratorAgent) -> None:
    """Run ``agent`` on this process's stdio — what an upstream client spawns."""
    await v2.run_agent(agent)
