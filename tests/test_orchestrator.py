"""End to end over a real wire, with no model in anything.

``serve`` is spawned as a child exactly as an IDE would spawn it; the stub agents
are spawned by it in turn. What is asserted is the contract the whole package
exists to provide: one session upstream, many agents downstream, every update
relayed and stamped with the role that produced it, the child's own state
machine kept to itself, and a handoff that actually carries the previous
agent's report forward.

Runnable two ways — ``pytest tests/`` or ``uv run python tests/test_orchestrator.py``.
"""

from __future__ import annotations

import asyncio
import os
import shlex
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
STUB = ROOT / "examples" / "stub_agent.py"

from crow_research import META_AGENT, Peer, PeerClient, Role, Stdio  # noqa: E402


class Recorder(PeerClient):
    """The upstream client: keep every update and the ``_meta`` it arrived with."""

    def __init__(self, peer: Peer) -> None:
        super().__init__(peer)
        self.events: list[tuple[str, Any, dict[str, Any]]] = []

    async def session_update(self, session_id: str, update: Any, **kwargs: Any) -> None:
        await super().session_update(session_id, update, **kwargs)
        self.events.append((getattr(update, "session_update", "?"), update, kwargs))

    def roles_in_order(self) -> list[str]:
        seen: list[str] = []
        for _kind, _update, meta in self.events:
            role = (meta.get(META_AGENT) or {}).get("role")
            if role and role not in seen:
                seen.append(role)
        return seen

    def text_from(self, role: str) -> str:
        return "".join(
            str(getattr(getattr(update, "content", None), "text", "") or "")
            for kind, update, meta in self.events
            if kind == "agent_message_chunk" and (meta.get(META_AGENT) or {}).get("role") == role
        )


def stub_role(name: str) -> str:
    """``--role`` spec pointing a role at the stub agent, named so it is
    distinguishable in the relay without any env plumbing."""
    return f"{name}={shlex.quote(sys.executable)} {shlex.quote(str(STUB))} {name}"


def serve(*extra: str, cwd: str) -> list[str]:
    return [
        sys.executable,
        "-m",
        "crow_research",
        "serve",
        "--workflow",
        "chain",
        "--role",
        stub_role("worker"),
        "--role",
        stub_role("verifier"),
        "--cwd",
        cwd,
        *extra,
    ]


async def drive(argv: list[str], prompt: str, *, delay: float = 0.0) -> tuple[Recorder, Any]:
    peer = Peer(Role(name="orchestrator", endpoint=Stdio(tuple(argv))), client_factory=Recorder)
    await peer.start(os.getcwd())
    try:
        if delay:
            await asyncio.sleep(delay)
        result = await peer.send(prompt)
        return peer.client, result  # type: ignore[return-value]
    finally:
        await peer.close()


async def test_chain_relays_tagged_updates_and_hands_off() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        client, result = await drive(serve(cwd=tmp), "prove the plumbing works")

    assert result.stop_reason == "end_turn", result.stop_reason
    # Both agents spoke, in order, over ONE upstream session.
    assert client.roles_in_order() == ["worker", "verifier"], client.roles_in_order()
    # The handoff carried the worker's own report into the verifier's prompt.
    # The worker got the task (with its brief in front of it) and said so.
    assert "prove the plumbing works" in client.text_from("worker"), client.text_from("worker")
    # The handoff carried the worker's own report into the verifier's prompt.
    assert "worker heard:" in client.text_from("verifier"), client.text_from("verifier")[:400]
    # The children's state machines stayed downstream: exactly one running/idle pair.
    states = [getattr(u, "state", None) for k, u, _ in client.events if k == "state_update"]
    assert states == ["running", "idle"], states
    # Tool calls were relayed, and stamped.
    tools = [(u.title, (m.get(META_AGENT) or {}).get("role")) for k, u, m in client.events if k == "tool_call_update"]
    assert tools == [("worker pretended to work", "worker"), ("verifier pretended to work", "verifier")], tools
    # One transition notice per step.
    notices = [u.title for k, u, _ in client.events if k == "notice"]
    assert len(notices) == 2 and "Worker" in notices[0], notices


async def test_cancel_ends_the_turn_and_reports_it() -> None:
    env = {**os.environ, "STUB_DELAY": "30"}
    with tempfile.TemporaryDirectory() as tmp:
        peer = Peer(
            Role(name="orchestrator", endpoint=Stdio(tuple(serve(cwd=tmp)), env=env)), client_factory=Recorder
        )
        await peer.start(os.getcwd())
        try:
            sent = asyncio.create_task(peer.send("something slow"))
            # Wait until the orchestrator has the turn in flight, then cancel it.
            for _ in range(200):
                if any(k == "state_update" for k, _, _ in peer.client.events):
                    break
                await asyncio.sleep(0.05)
            await peer.cancel()
            result = await asyncio.wait_for(sent, timeout=30)
            assert result.stop_reason == "cancelled", result.stop_reason
            states = [getattr(u, "state", None) for k, u, _ in peer.client.events if k == "state_update"]
            assert states == ["running", "idle"], states
        finally:
            await peer.close()


def test_ctrl_c_in_the_client_is_session_cancel() -> None:
    """Ctrl-C is the loop's only exit, so it has to actually reach the wire.

    ``asyncio`` installs its own SIGINT handler and turns the *first* press into a
    cancellation of the running coroutine rather than a ``KeyboardInterrupt``, so
    a client that catches only ``KeyboardInterrupt`` catches nothing: the cancel is
    never sent, the child is never told, and the run ends in a traceback instead of
    a ``stop_reason``. This drives the real console script as a subprocess, presses
    Ctrl-C once, and reads what it printed.
    """
    env = {**os.environ, "STUB_DELAY": "30"}
    with tempfile.TemporaryDirectory() as tmp:
        argv = [
            sys.executable, "-m", "crow_research.repl_client",
            "--cwd", tmp, "-p", "something slow",
            "--", sys.executable, str(STUB), "orchestrator",
        ]
        log = Path(tmp) / "client.log"
        with log.open("wb") as sink:
            # Its own process group, so the signal reaches the client and not the
            # agent it spawned: the graceful path is the thing under test.
            proc = subprocess.Popen(argv, cwd=ROOT, stdout=sink, stderr=subprocess.STDOUT,
                                    stdin=subprocess.DEVNULL, env=env, start_new_session=True)
            try:
                deadline = time.monotonic() + 30
                while time.monotonic() < deadline and b"something slow" not in log.read_bytes():
                    time.sleep(0.05)
                assert b"something slow" in log.read_bytes(), "the turn never got in flight"
                proc.send_signal(signal.SIGINT)
                assert proc.wait(timeout=30) == 0, "Ctrl-C did not exit cleanly"
            finally:
                if proc.poll() is None:
                    proc.kill()
                    proc.wait(timeout=10)
        out = log.read_text()
    assert "cancelling..." in out, out[-800:]
    # Only ``cancel_session`` produces this: the stub answers a cancel with
    # idle(cancelled), so the stop reason is proof the request crossed the wire.
    assert "stop_reason: cancelled" in out, out[-800:]


async def test_autostart_works_without_a_prompt() -> None:
    """The always-on shape: opening a session is enough to start the work.

    v2 lets an agent initiate — updates may flow at any time, not only inside a
    turn somebody asked for — so an orchestrator can be running before its
    client has thought to say anything. That is what makes "always on" a
    property of the agent rather than of a cron job wrapped around it.
    """
    with tempfile.TemporaryDirectory() as tmp:
        argv = serve("--autostart", "keep the lights on", cwd=tmp)
        peer = Peer(Role(name="orchestrator", endpoint=Stdio(tuple(argv))), client_factory=Recorder)
        await peer.start(os.getcwd())
        try:
            for _ in range(400):
                states = [getattr(u, "state", None) for k, u, _ in peer.client.events if k == "state_update"]
                if states[-1:] == ["idle"]:
                    break
                await asyncio.sleep(0.05)
            client = peer.client
            assert client.roles_in_order() == ["worker", "verifier"], client.roles_in_order()
            assert "keep the lights on" in client.text_from("worker"), client.text_from("worker")[:200]
            # Nobody typed it, and the stream says so.
            echoed = [m for k, u, m in client.events if k == "user_message" and m.get("crow/autostart")]
            assert len(echoed) == 1, echoed
        finally:
            await peer.close()


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
