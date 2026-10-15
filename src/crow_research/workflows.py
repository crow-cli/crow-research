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
from .todo import Todo, parse_new_items, parse_updates, parse_verdict
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


# -- the pipeline -------------------------------------------------------
#
# worker -> verifier, until the verifier signs off AND the todo list is
# complete. Then the explorer adds more items to the todo. Then it goes round
# again: the loop does not stop by itself.

WORKER_PROMPT = """The todo list is the work.

{todo}

{findings}Claim items, do them, and report. Report format:

* One `TODO <id> <state> <note>` line for every item you touched, where state is
  `working` (claimed, not finished), `done` (finished and checked), `open` (not
  started, or handed back) or `blocked` (cannot be done, say why in the note).
* Then your report: what you changed, the exact commands you ran, and what they
  returned. A verifier that never saw this work reads only that report.
"""

VERIFIER_PROMPT = """Verify the worker's pass. You did not write it and you do not fix it.

{todo}

The worker reported:

{report}

Report format:

* One `TODO <id> <state> <note>` line for every item whose state you dispute —
  `open` with the finding in the note when a `done` claim did not reproduce.
* Your findings, each with the exact command and its exact output.
* Last line: `VERDICT: PASS`, `VERDICT: FAIL` or `VERDICT: UNVERIFIED`.
"""

EXPLORER_PROMPT = """The pipeline is idle: the verifier signed off and the todo list is complete.

{todo}

The verifier's last word: {verdict}

{report}

Go and find what this work makes possible — the prior art it should have used,
the gap it leaves, the next thing worth building on top of it. Report format:

* One `ADD <text>` line per new todo item, in the order they should be done.
* Then your findings, with a URL and commit or tag for every source.
"""


def _findings_block(verifier_report: str) -> str:
    if not verifier_report.strip():
        return ""
    return f"The verifier's findings on the last pass:\n\n{verifier_report.strip()}\n\n"


def apply_updates(todo: Any, report: str, *, by: str) -> list[str]:
    """Write an agent's `TODO <id> <state>` lines back onto the list.

    An id that is not on the list is reported upstream rather than dropped: an
    agent inventing an id is a real event, and a silently ignored line is a list
    that quietly stops matching what the agents think it says.
    """
    applied: list[str] = []
    for item_id, state, note in parse_updates(report):
        try:
            todo.set(item_id, state, note)
        except KeyError:
            applied.append(f"todo {item_id} does not exist ({by} claimed it)")
            continue
        applied.append(f"todo {item_id} -> {state}")
    return applied


def pipeline(
    worker: Role,
    verifier: Role,
    explorer: Role,
    todo: Todo,
    *,
    timeout: Optional[float] = None,
    max_review: int = 5,
    announce: bool = True,
    idle_pause: float = 30.0,
) -> Workflow:
    """The loop: worker -> verifier until sign-off on a complete list, then the
    explorer adds more items, then round again. It does not end by itself;
    ``session/cancel`` ends it.

    ``max_review`` bounds one round's worker/verifier exchange, not the loop. A
    pair that cannot agree still falls through to the explorer, so a stuck round
    costs a round instead of the run.

    ``idle_pause`` is what keeps a perpetual loop from becoming a hot one: when
    the explorer adds nothing there is no work anywhere in the system, so the
    next round would immediately ask the explorer again. It waits instead — and
    the wait is cut short by a cancel, because a loop that ignores
    ``session/cancel`` for thirty seconds is a loop that does not stop.
    """

    async def workflow(turn: Turn) -> Optional[str]:
        last: Optional[str] = None
        verdict: Optional[str] = None
        verifier_report = ""
        round_no = 0
        if not len(todo):
            # The prompt is where the work comes from: it seeds the list, and
            # from here the agents own it. ``ADD`` lines are honored so one
            # prompt can hand over several items; otherwise the whole prompt is
            # the first one.
            todo.extend(parse_new_items(turn.prompt) or [turn.prompt], by="prompt")
            if announce:
                await turn.notice(
                    f"todo seeded with {len(todo)} items", description=todo.render(only_pending=True)
                )
        while True:
            turn._check()
            round_no += 1
            # -- worker -> verifier, until the verifier signs off on a complete list
            for _attempt in range(max_review):
                turn._check()
                # The exit condition, and a guard as well: a complete list has
                # nothing for the worker to claim, and dispatching it anyway
                # buys a turn whose whole content is "nothing to do". The first
                # real run did exactly that — round 1's explorer added no items,
                # so round 2 ran worker *and* verifier against an empty list
                # before the explorer was asked again.
                if todo.complete:
                    break
                if announce:
                    await turn.notice(
                        f"round {round_no} · {worker.label}",
                        description=f"{len(todo.pending())} pending · last verdict {verdict or 'none'}",
                    )
                worker_result = await turn.run(
                    worker,
                    WORKER_PROMPT.format(todo=todo.render(), findings=_findings_block(verifier_report)),
                    timeout=timeout,
                )
                last = worker_result.stop_reason
                for line in apply_updates(todo, worker_result.report, by=worker.name):
                    await turn.notice(line, severity="warning" if "does not exist" in line else "info")

                turn._check()
                if announce:
                    await turn.notice(f"round {round_no} · {verifier.label}", description="signing off on the pass")
                verifier_result = await turn.run(
                    verifier,
                    VERIFIER_PROMPT.format(todo=todo.render(), report=worker_result.report),
                    timeout=timeout,
                )
                last = verifier_result.stop_reason
                for line in apply_updates(todo, verifier_result.report, by=verifier.name):
                    await turn.notice(line, severity="warning" if "does not exist" in line else "info")
                verifier_report = verifier_result.report
                verdict = parse_verdict(verifier_report)
                if announce:
                    await turn.notice(
                        f"round {round_no} · verdict {verdict or 'UNREPORTED'}",
                        description=todo.render(only_pending=True).splitlines()[0],
                        severity="info" if verdict == "PASS" else "warning",
                    )

            # -- the explorer adds more items to the todo
            turn._check()
            if announce:
                await turn.notice(f"round {round_no} · {explorer.label}", description="adding work to the todo")
            explorer_result = await turn.run(
                explorer,
                EXPLORER_PROMPT.format(
                    todo=todo.render(), verdict=verdict or "UNREPORTED", report=_findings_block(verifier_report)
                ),
                timeout=timeout,
            )
            last = explorer_result.stop_reason
            added = todo.extend(parse_new_items(explorer_result.report), by=explorer.name)
            if announce:
                await turn.notice(
                    f"round {round_no} · {explorer.label} added {len(added)} items",
                    description="; ".join(i.text[:60] for i in added) or "nothing added — the loop continues",
                )
            verdict, verifier_report = None, ""
            if not added and idle_pause:
                try:
                    await asyncio.wait_for(turn.cancelled.wait(), timeout=idle_pause)
                except asyncio.TimeoutError:
                    pass
                turn._check()

    return workflow
