"""A v2 agent with no model in it.

It exists so the orchestrator can be tested end to end over a real wire without
spending a token: it handshakes, opens a session, and on a prompt emits exactly
the update sequence a real agent owes — user-message echo, ``running``, some
message chunks, a tool call, ``idle(end_turn)`` — then acks. Its reply quotes
the prompt it was given, which is what lets a test assert that a handoff really
carried the previous agent's report into the next one.

``python examples/stub_agent.py <name>`` — the name is what it says it is, so a
cast of stubs is distinguishable in the relayed stream without any env plumbing.
``STUB_DELAY`` seconds of sleep before it answers makes cancellation testable,
``STUB_REPLY_<NAME>`` (or ``STUB_REPLY``) replaces the echo with a scripted
report, and ``STUB_STATE_<NAME>`` (or ``STUB_STATE``) is a JSON document
deep-merged into ``STUB_STATE_PATH`` before the stub goes idle — which is how a
test scripts what an agent *writes*, as opposed to what it says. The pipeline
reads the file and nothing else, so a test that only scripted replies would be
testing a stub talking to itself.
"""

from __future__ import annotations

import asyncio
import itertools
import json
import os
import sys
from pathlib import Path
from typing import Any, Optional

from acp.experimental import v2


def _text(prompt: list[Any]) -> str:
    return " ".join(str(getattr(b, "text", "") or "") for b in prompt).strip()


def _merge_state(path: str, patch: str) -> None:
    """Deep-merge a JSON patch into the state file, the way an agent edits it.

    Items carrying an ``id`` update the item that has it, items without one are
    appended under the next free id, and every other key overwrites. A merge
    rather than a write so a cast scripted through one shared environment cannot
    have its second role erase what the first one did — which is the property the
    real file has, and the reason there is a file.
    """
    file = Path(path)
    doc: Any = json.loads(file.read_text()) if file.exists() else {}
    items = doc.setdefault("items", [])
    for key, value in json.loads(patch).items():
        if key != "items":
            doc[key] = value
            continue
        for entry in value:
            if "id" not in entry:
                entry = {**entry, "id": max((i.get("id", 0) for i in items), default=0) + 1}
            for existing in items:
                if existing.get("id") == entry["id"]:
                    existing.update(entry)
                    break
            else:
                items.append(entry)
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(json.dumps(doc, indent=2) + "\n")


class StubAgent:
    def __init__(self, name: str) -> None:
        self.name = name
        self.conn: Optional[v2.AgentSideConnection] = None
        self.sessions: dict[str, int] = {}
        self._ids = itertools.count(1)

    def on_connect(self, conn: v2.AgentSideConnection) -> None:
        self.conn = conn

    async def initialize(self, protocol_version: int, info: Any, capabilities: Any = None, **kw: Any) -> Any:
        return v2.schema.InitializeResponse(
            protocol_version=v2.PROTOCOL_VERSION,
            info=v2.schema.Implementation(name=f"stub-{self.name}", version="0.1.0"),
            capabilities=v2.schema.AgentCapabilities(session=v2.schema.SessionCapabilities()),
        )

    async def new_session(self, cwd: str, additional_directories: Any = None, mcp_servers: Any = None, **kw: Any) -> Any:
        session_id = f"stub-{self.name}-{len(self.sessions) + 1}"
        self.sessions[session_id] = 0
        return v2.schema.NewSessionResponse(session_id=session_id)

    async def list_sessions(self, cwd: Optional[str] = None, cursor: Optional[str] = None, **kw: Any) -> Any:
        return v2.schema.ListSessionsResponse(
            sessions=[v2.schema.SessionInfo(session_id=s, cwd=cwd or ".") for s in self.sessions]
        )

    async def resume_session(self, session_id: str, cwd: str, **kw: Any) -> Any:
        self.sessions.setdefault(session_id, 0)
        return v2.schema.ResumeSessionResponse()

    async def close_session(self, session_id: str, **kw: Any) -> Any:
        self.sessions.pop(session_id, None)
        return v2.schema.CloseSessionResponse()

    async def prompt(self, session_id: str, prompt: list[Any], **kw: Any) -> Any:
        message_id = f"{session_id}-user-{self.sessions.get(session_id, 0) + 1}"
        self.sessions[session_id] = self.sessions.get(session_id, 0) + 1
        await self.conn.session_update(
            session_id=session_id, update=v2.schema.UserMessageUpdate(message_id=message_id, content=list(prompt))
        )
        asyncio.create_task(self._work(session_id, _text(prompt)))
        return v2.schema.PromptResponse(message_id=message_id)

    async def _work(self, session_id: str, heard: str) -> None:
        emit = lambda update: self.conn.session_update(session_id=session_id, update=update)  # noqa: E731
        await emit(v2.schema.RunningSessionStateUpdate())
        if delay := float(os.environ.get("STUB_DELAY", "0")):
            await asyncio.sleep(delay)
        # A scripted reply, per role: ``STUB_REPLY_WORKER`` beats ``STUB_REPLY``
        # beats the echo. One shared env can then give a whole cast different
        # words, which is what a pipeline test needs — the worker has to hand
        # the verifier something to check, in the same run.
        reply = os.environ.get(f"STUB_REPLY_{self.name.upper()}") or os.environ.get("STUB_REPLY")
        if reply is None:
            reply = f"{self.name} heard: {heard[:4000]}"
        mid = f"{session_id}-agent-{next(self._ids)}"
        await emit(v2.schema.AgentMessageChunk(message_id=mid, content=v2.schema.TextContentBlock(text=reply)))
        await emit(
            v2.schema.SessionToolCallUpdate(
                tool_call_id=f"{session_id}-tool-{next(self._ids)}",
                name="stub",
                title=f"{self.name} pretended to work",
                kind="other",
                status="completed",
            )
        )
        # The write, before the idle: the orchestrator reads the file back when
        # the turn ends, so a patch that landed after this would be read by
        # whoever goes next instead of by the reload that reports it.
        patch = os.environ.get(f"STUB_STATE_{self.name.upper()}") or os.environ.get("STUB_STATE")
        if patch and (state := os.environ.get("STUB_STATE_PATH")):
            _merge_state(state, patch)
        await emit(v2.schema.IdleSessionStateUpdate(stop_reason="end_turn"))

    async def cancel_session(self, session_id: str, **kw: Any) -> None:
        await self.conn.session_update(
            session_id=session_id, update=v2.schema.IdleSessionStateUpdate(stop_reason="cancelled")
        )


async def main(name: str) -> None:
    await v2.run_agent(StubAgent(name))


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "stub"))
