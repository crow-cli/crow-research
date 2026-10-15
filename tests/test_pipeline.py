"""The pipeline loop, end to end over a real wire, with no model in anything.

What is asserted here is the shape the whole project exists for: worker ->
verifier until the verifier signs off on a complete todo list, then the explorer
adds more items, then round again — forever, until ``session/cancel``.

The stubs are scripted through ``STUB_REPLY_<ROLE>`` so each role says what the
loop parses (``TODO 1 done``, ``VERDICT: PASS``, ``ADD ...``) without an LLM in
the loop. Dispatch counts are read off the transition notices, which is what an
upstream client sees, so the test asserts the observable contract rather than
poking at internals.

Runnable two ways — ``pytest tests/`` or ``uv run python tests/test_pipeline.py``.
"""

from __future__ import annotations

import asyncio
import os
import re
import shlex
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STUB = ROOT / "examples" / "stub_agent.py"

from crow_research import META_AGENT, Peer, Role, Stdio  # noqa: E402
from test_orchestrator import Recorder  # noqa: E402  (pytest puts tests/ on sys.path)


def stub(name: str) -> str:
    return f"{name}={shlex.quote(sys.executable)} {shlex.quote(str(STUB))} {name}"


def serve_pipeline(cwd: str, *, idle_pause: float = 0.2) -> list[str]:
    return [
        sys.executable, "-m", "crow_research", "serve",
        "--workflow", "pipeline",
        "--role", stub("worker"),
        "--role", stub("verifier"),
        "--role", stub("explorer"),
        "--idle-pause", str(idle_pause),
        "--cwd", cwd,
    ]


#: A dispatch notice is exactly ``round N · <Role>``. The loop emits other
#: notices with the same prefix — ``round N · verdict PASS``, ``round N ·
#: Explorer added 3 items`` — and counting those as dispatches is how a test
#: passes for the wrong reason.
DISPATCH = re.compile(r"^round \d+ · (Worker|Verifier|Explorer)$")


def dispatches(client: Recorder) -> dict[str, int]:
    """How many times each role was dispatched, counted off the transition
    notices — the same signal the upstream UI renders."""
    counts: dict[str, int] = {}
    for kind, update, _meta in client.events:
        if kind != "notice":
            continue
        matched = DISPATCH.match(getattr(update, "title", "") or "")
        if matched:
            counts[matched.group(1)] = counts.get(matched.group(1), 0) + 1
    return counts


def notices(client: Recorder) -> list[str]:
    return [u.title for k, u, _ in client.events if k == "notice"]


async def run_until(peer: Peer, prompt: str, predicate, *, limit: float = 90.0) -> None:
    """Send the prompt, then wait for ``predicate(client)`` before cancelling.

    The pipeline never goes idle on its own — that is the point of it — so a
    test drives it by watching the stream and ending the turn with the same
    ``session/cancel`` a user would.
    """
    sent = asyncio.create_task(peer.send(prompt, timeout=limit))
    deadline = asyncio.get_running_loop().time() + limit
    while asyncio.get_running_loop().time() < deadline:
        if predicate(peer.client):
            break
        await asyncio.sleep(0.05)
    else:
        raise AssertionError(f"predicate never held; notices={notices(peer.client)}")
    await peer.cancel()
    return await asyncio.wait_for(sent, timeout=30)


async def test_signoff_then_explorer_and_no_work_is_not_a_worker_turn() -> None:
    """VERDICT: PASS on a complete list hands over to the explorer; an explorer
    that adds nothing is asked again, and the worker is NOT re-dispatched to say
    "nothing to do" — the defect the first real run showed."""
    env = {
        **os.environ,
        "STUB_REPLY_WORKER": "TODO 1 done built it and ran the tests\nreport: 30 passed",
        "STUB_REPLY_VERIFIER": "reproduced every claim\nVERDICT: PASS",
        "STUB_REPLY_EXPLORER": "no prior art worth adding this round",
    }
    with tempfile.TemporaryDirectory() as tmp:
        peer = Peer(
            Role(name="orchestrator", endpoint=Stdio(tuple(serve_pipeline(tmp)), env=env)),
            client_factory=Recorder,
        )
        await peer.start(os.getcwd())
        try:
            result = await run_until(peer, "ADD make the thing", lambda c: dispatches(c).get("Explorer", 0) >= 2)
        finally:
            await peer.close()

    client = peer.client
    assert result.stop_reason == "cancelled", result.stop_reason
    counts = dispatches(client)
    # One pass of work, one sign-off, and the explorer asked again for work.
    assert counts["Worker"] == 1, counts
    assert counts["Verifier"] == 1, counts
    assert counts["Explorer"] >= 2, counts
    seen = notices(client)
    # The prompt seeded the list, the worker settled the item, the verdict landed.
    assert any("todo seeded with 1 items" in n for n in seen), seen
    assert "todo 1 -> done" in seen, seen
    assert any("verdict PASS" in n for n in seen), seen
    assert any("Explorer added 0 items" in n for n in seen), seen
    # The children's state machines stayed downstream: one running/idle pair.
    states = [getattr(u, "state", None) for k, u, _ in client.events if k == "state_update"]
    assert states == ["running", "idle"], states


async def test_explorer_additions_reach_the_next_worker_turn() -> None:
    """The explorer's ``ADD`` lines land on the list, and the next worker turn is
    prompted with them — the handover that makes the loop perpetual rather than
    a single pass."""
    env = {**os.environ, "STUB_REPLY_EXPLORER": "ADD second thing worth doing"}
    with tempfile.TemporaryDirectory() as tmp:
        peer = Peer(
            Role(name="orchestrator", endpoint=Stdio(tuple(serve_pipeline(tmp)), env=env)),
            client_factory=Recorder,
        )
        await peer.start(os.getcwd())
        try:
            # Worker and verifier echo their prompt here (no scripted reply), so
            # the todo list they were handed is visible in the relayed stream.
            result = await run_until(
                peer, "ADD first thing", lambda c: "second thing worth doing" in c.text_from("worker")
            )
        finally:
            await peer.close()

    client = peer.client
    assert result.stop_reason == "cancelled", result.stop_reason
    counts = dispatches(client)
    assert counts["Worker"] >= 2, counts
    assert any("Explorer added 1 items" in n for n in notices(client)), notices(client)
    assert "second thing worth doing" in client.text_from("worker")


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
