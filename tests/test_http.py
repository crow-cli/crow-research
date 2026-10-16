"""The orchestrator over a socket: ``serve_http``, dialled by a real v2 client.

``--http`` is the other half of the agent's contract — a client that dials
instead of spawns. What is asserted is that nothing about the pipeline changes
when the wire does: one session, every child's update relayed and stamped with
the role that produced it, and ``session/cancel`` still the only thing that ends
a loop built to run in perpetuity.

Runnable two ways — ``pytest tests/`` or ``uv run python tests/test_http.py``.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import socket
import sys
import tempfile
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STUB = ROOT / "examples" / "stub_agent.py"
ORCHESTRATOR = ROOT / "agents" / "orchestrator_agent.py"

from crow_research import (  # noqa: E402
    BUILTIN,
    OrchestratorAgent,
    Peer,
    Role,
    Stdio,
    Todo,
    Ws,
    serve_http,
    workflows,
)
from crow_research.todo import state_path  # noqa: E402
from test_orchestrator import Recorder  # noqa: E402  (pytest puts tests/ on sys.path)
from test_pipeline import dispatches, notices, run_until  # noqa: E402


def free_port() -> int:
    """An ephemeral port, released before the server binds it."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


async def wait_for_port(port: int, *, limit: float = 30.0) -> None:
    """Block until something is listening, so the dial is not a race."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + limit
    while loop.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.25):
                return
        except OSError:
            await asyncio.sleep(0.1)
    raise AssertionError(f"nothing listening on 127.0.0.1:{port}")


def stub_role(name: str, env: dict[str, str]) -> Role:
    """A built-in role with the model-less stub behind it."""
    return replace(BUILTIN[name], endpoint=Stdio((sys.executable, str(STUB), name), env=env))


async def test_the_pipeline_is_the_same_over_a_socket() -> None:
    """Serve the loop with ``serve_http``, dial it with ``Peer`` over WebSocket,
    and hold it to the same contract the stdio tests assert."""
    with tempfile.TemporaryDirectory() as tmp:
        env = {
            **os.environ,
            "STUB_STATE_PATH": str(state_path(tmp)),
            "STUB_STATE_WORKER": json.dumps(
                {"items": [{"id": 1, "state": "done", "note": "built it and ran the tests"}]}
            ),
            "STUB_STATE_VERIFIER": json.dumps({"verdict": "PASS", "verdict_note": "every claim reproduced"}),
            # The explorer writes nothing, so the list stays complete and signed
            # off: exactly one worker turn and one verifier turn, and the loop
            # keeps asking the explorer for more work. A stub that appended an
            # item every round would spin the review loop instead, and the shape
            # of that spin is test_pipeline's business, not the transport's.
            "STUB_REPLY_EXPLORER": "no prior art worth adding this round",
        }
        cast = {n: stub_role(n, env) for n in ("worker", "verifier", "explorer")}
        agent = OrchestratorAgent(
            workflows.pipeline(cast["worker"], cast["verifier"], cast["explorer"], Todo(),
                               state_path=state_path(tmp)),
            name="crow-research",
            title="Crow Research Pipeline",
            mcp_servers=[],  # the stubs are model-less; a tool supply would only slow this down
        )
        port = free_port()
        stop = asyncio.Event()
        server = asyncio.create_task(
            serve_http(lambda conn: agent, "127.0.0.1", port, shutdown_trigger=stop.wait)
        )
        await wait_for_port(port)
        peer = Peer(
            Role(name="orchestrator", endpoint=Ws(f"ws://127.0.0.1:{port}/acp")),
            client_factory=Recorder,
        )
        await peer.start(tmp)
        try:
            # Not the explorer's *dispatch* notice — that one is the
            # orchestrator's own and lands before the child has said anything, so
            # cancelling on it would cut the turn before the explorer had a
            # stamped update in the stream. Wait for the round to close.
            result = await run_until(
                peer, "make the thing", lambda c: any("Explorer added" in n for n in notices(c))
            )
            state = Todo.load(state_path(tmp))
        finally:
            await peer.close()
            stop.set()
            await server

    client = peer.client
    assert result.stop_reason == "cancelled", result.stop_reason
    counts = dispatches(client)
    # One pass of work, one sign-off — and then the loop keeps asking the
    # explorer, because a perpetual pipeline with nothing to do is not a
    # pipeline that stops.
    assert counts["Worker"] == 1, counts
    assert counts["Verifier"] == 1, counts
    assert counts["Explorer"] >= 1, counts
    # The role stamps survived the socket — this is what tells a UI who spoke.
    assert client.roles_in_order() == ["worker", "verifier", "explorer"], client.roles_in_order()
    seen = notices(client)
    assert any("todo seeded with 1 items" in n for n in seen), seen
    assert "todo 1 -> done" in seen, seen
    assert any("verdict PASS" in n for n in seen), seen
    assert any("Explorer added 0 items" in n for n in seen), seen
    # The orchestrator's own state machine is the only one upstream: one pair.
    states = [getattr(u, "state", None) for k, u, _ in client.events if k == "state_update"]
    assert states == ["running", "idle"], states
    assert state.verdict == "PASS", state
    assert state.complete, state.render()
    assert state.items[0].note == "built it and ran the tests", state.items[0]


async def test_the_agent_file_serves_http() -> None:
    """``agents/orchestrator_agent.py --http --port N`` binds, negotiates v2 and
    opens a session. Only the handshake: a prompt would start the real pipeline
    on a real model, and this test has no business paying for that."""
    port = free_port()
    proc = await asyncio.create_subprocess_exec(
        sys.executable, str(ORCHESTRATOR), "--http", "--port", str(port),
        stderr=asyncio.subprocess.PIPE,
    )
    peer = None
    try:
        await wait_for_port(port)
        peer = Peer(
            Role(name="orchestrator", endpoint=Ws(f"ws://127.0.0.1:{port}/acp")),
            client_factory=Recorder,
        )
        session_id = await peer.start(os.getcwd())
        assert session_id, "session/new came back with no id"
    finally:
        if peer is not None:
            await peer.close()
        proc.terminate()
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(proc.wait(), timeout=20)
        assert proc.returncode is not None, "the served agent did not exit on SIGTERM"


async def main() -> int:
    failures = 0
    for name, test in sorted(globals().items()):
        if not name.startswith("test_"):
            continue
        print(f"=== {name}")
        try:
            await test()
        except Exception as error:
            failures += 1
            import traceback

            traceback.print_exc()
            print(f"FAIL {name}: {error}")
        else:
            print(f"ok   {name}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
