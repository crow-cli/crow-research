"""The predefined loops: one role, a chain of them, and a chain that never ends.

A workflow is ``async (Turn) -> stop_reason | None``. These are factories that
return one, so a command line can build a whole cast out of ``--role`` flags and
hand the orchestrator something to run.

The chain is the vision's shape: a worker does the task, a verifier that never
saw the work tries to break it, an explorer goes and reads what the rest of the
world did, a researcher asks what the interesting problem even was. Each step
gets the *previous agent's own report* — not its transcript, not a database row
— because a report is already the compaction, and asking an agent to summarize
itself costs a turn to say what it just said. ``compact=`` buys the explicit
interagent summary anyway, for a step whose output is long enough that the next
agent would drown in it.
"""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable, Optional, Sequence

from .agent import Workflow
from .roles import Role
from .turn import Turn, TurnCancelled

#: The interagent compaction prompt — an agent asked to summarize itself for a
#: peer that has no memory of the work. "Call no tools" is load-bearing: a
#: summary turn that starts reading files is a second turn, not a handoff.
COMPACT_PROMPT = (
    "Summarize the conversation in RESTful markdown format and the steps you "
    "have taken. This is an interagent summary/compaction event: it is read by "
    "another agent that has no memory of your work. Respond directly. Call no tools."
)


def single(role: Role, *, timeout: Optional[float] = None) -> Workflow:
    """Hand the user's prompt to one role and relay what it does."""

    async def workflow(turn: Turn) -> Optional[str]:
        await turn.notice(f"{role.label} starting", description=turn.prompt[:200])
        result = await turn.run(role, turn.prompt, timeout=timeout)
        return result.stop_reason

    return workflow


def chain(
    *roles: Role,
    timeout: Optional[float] = None,
    compact: bool = False,
    announce: bool = True,
) -> Workflow:
    """Run the roles in order, each handed the previous one's report.

    The first role gets the user's prompt verbatim (with its brief prepended);
    every later role gets the handoff template filled with the original task and
    the report before it. Cancellation is checked between steps *and* inside
    every emit, so a cancel lands at the next boundary at the latest.
    """
    if not roles:
        raise ValueError("a chain needs at least one role")

    async def workflow(turn: Turn) -> Optional[str]:
        task = turn.prompt
        report: Optional[str] = None
        previous = ""
        last: Optional[str] = None
        for index, role in enumerate(roles):
            turn._check()
            if announce:
                await turn.notice(
                    f"{index + 1}/{len(roles)} · {role.label}",
                    description=None if index == 0 else f"handing off from {previous}",
                )
            text = role.assignment(task=task, report=report, previous=previous)
            result = await turn.run(role, text, timeout=timeout)
            last = result.stop_reason
            if compact:
                summary = await turn.run(role, COMPACT_PROMPT, timeout=timeout)
                report = summary.report or result.report
            else:
                report = result.report
            previous = role.name
        return last

    return workflow


def rounds(inner: Workflow, count: int, *, pause: float = 0.0) -> Workflow:
    """Run ``inner`` ``count`` times in one turn. ``count < 0`` means forever —
    the always-on shape, which ends only when the client cancels it."""

    async def workflow(turn: Turn) -> Optional[str]:
        done = 0
        stop: Optional[str] = None
        while count < 0 or done < count:
            turn._check()
            stop = await inner(turn)
            done += 1
            if pause:
                await asyncio.sleep(pause)
        return stop

    return workflow


def forever(inner: Workflow, *, pause: float = 0.0) -> Workflow:
    """``rounds(inner, -1)`` — the loop that does not stop by itself."""
    return rounds(inner, -1, pause=pause)


def select(
    picker: Callable[[Turn], Awaitable[Role]], *roles: Role, **kwargs: Any
) -> Workflow:
    """Let something else choose the next role, then chain from there.

    ``picker`` is the seam for "some way of setting up ways to select which
    agent to start with in the sequence" — an LLM judging the prompt, a config
    option, a delivery's sender. It sees the turn and returns a role; the
    remaining roles run after it in the order given.
    """

    async def workflow(turn: Turn) -> Optional[str]:
        first = await picker(turn)
        rest = [r for r in roles if r.name != first.name]
        return await chain(first, *rest, **kwargs)(turn)

    return workflow
