"""The predefined loops: one role, a chain of them, and a chain that never ends.

A workflow is ``async (Turn) -> stop_reason | None``. These are factories that
return one, so a command line can build a whole cast out of ``--role`` flags and
hand the orchestrator something to run.

The chain is the vision's shape: a worker does the task, a verifier that never
saw the work tries to break it, an explorer decides what the next round of work
is, a researcher asks what the interesting problem even was. Each step
gets the *previous agent's own report* — not its transcript, not a database row
— because a report is already the compaction, and asking an agent to summarize
itself costs a turn to say what it just said. ``compact=`` buys the explicit
interagent summary anyway, for a step whose output is long enough that the next
agent would drown in it.

:func:`pipeline` is the one that runs forever, and it keeps its state in a JSON
file the agents edit rather than in a report format it parses — see the section
at the bottom.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Awaitable, Callable, Optional, Sequence

from .agent import Workflow
from .roles import Role
from .todo import Item, Todo
from .todo import state_path as default_state_path
from .turn import Turn, TurnCancelled

#: The interagent compaction prompt — an agent asked to summarize itself for a
#: peer that has no memory of the work. "Call no tools" is load-bearing: a
#: summary turn that starts reading files is a second turn, not a handoff.
COMPACT_PROMPT = (
    "Summarize the conversation in RESTful markdown format and the steps you "
    "have taken. This is an interagent summary/compaction event: it is read by "
    "another agent that has no memory of your work. Respond directly. Call no tools."
)


def single(role: Role) -> Workflow:
    """Hand the user's prompt to one role and relay what it does."""

    async def workflow(turn: Turn) -> Optional[str]:
        await turn.notice(f"{role.label} starting", description=turn.prompt[:200])
        result = await turn.run(role, turn.prompt)
        return result.stop_reason

    return workflow


def chain(*roles: Role, compact: bool = False, announce: bool = True) -> Workflow:
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
            result = await turn.run(role, text)
            last = result.stop_reason
            if compact:
                summary = await turn.run(role, COMPACT_PROMPT)
                report = summary.report or result.report
            else:
                report = result.report
            previous = role.name
        return last

    return workflow


def rounds(inner: Workflow, count: int) -> Workflow:
    """Run ``inner`` ``count`` times in one turn. ``count < 0`` means forever —
    the always-on shape, which ends only when the client cancels it."""

    async def workflow(turn: Turn) -> Optional[str]:
        done = 0
        stop: Optional[str] = None
        while count < 0 or done < count:
            turn._check()
            stop = await inner(turn)
            done += 1
        return stop

    return workflow


def forever(inner: Workflow) -> Workflow:
    """``rounds(inner, -1)`` — the loop that does not stop by itself."""
    return rounds(inner, -1)


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


# -- the pipeline# -- the pipeline -------------------------------------------------------
#
# worker -> verifier, until the verifier signs off AND the todo list is
# complete. Then the explorer adds more items. Then it goes round again: the
# loop does not stop by itself.
#
# The state is a JSON file, not a report format. Every agent is told the path
# and the shape of the document, edits it with the tools it already has, and the
# orchestrator reads it back after each turn. What an agent *says* is prose for
# whoever is watching the stream; what it *writes* is the contract.

#: The document, shown to every agent verbatim. Substituted into the prompts as
#: a value rather than written inside them, so its braces are its own and not
#: ``.format`` escapes.
STATE_SCHEMA = """\
{
  "verdict": null,
  "verdict_note": "",
  "items": [
    {"id": 1, "text": "what to do", "state": "open", "note": "", "added_by": "prompt"}
  ]
}"""

#: What all three agents are told about the file. One copy, so the part that
#: matters cannot drift between the prompts.
STATE_RULES = """\
* Read the file before you write it, and leave it parsing when you are done. A
  document that does not parse is announced upstream and your write is thrown
  away, so the next agent sees the list exactly as it was before you touched it.
* `state` is one of `open`, `working`, `done`, `blocked`. Ids are the
  orchestrator's: never renumber one, and never reuse an id that is gone.

Your final message is prose for whoever is watching the stream. Nothing in it is
parsed — no state lines, no keywords, no machine-readable format of any kind.
The file is the only thing you write that the pipeline acts on."""

WORKER_PROMPT = """The work is a todo list, and the list is a file: {path}

{todo}

{findings}Claim items, do them, and write the outcome into that file:

```json
{schema}
```

* Set `state` on every item you touched — `working` when you have claimed it and
  are not finished, `done` when it is finished and you have checked it, `open`
  when you are handing it back, `blocked` when it cannot be done at all.
* Write what you learned into that item's `note`. It is the only thing the
  verifier reads about the item besides its state.
* Leave `verdict` alone: it is the verifier's to set, not yours.

{rules}
"""

VERIFIER_PROMPT = """Verify the worker's pass. You did not write it and you do not fix it.

The work is a todo list, and the list is a file: {path}

{todo}

{report}Your sign-off goes into that file and nowhere else:

```json
{schema}
```

* Set `verdict` to `PASS`, `FAIL` or `UNVERIFIED`, and say why in
  `verdict_note`. `PASS` means every claim reproduced and you found nothing
  worth a round trip. `UNVERIFIED` means the environment would not let you
  check — a finding about the harness, not about the work.
* Set `state` on every item whose claim you dispute: back to `open`, with the
  finding in `note`, when a `done` did not reproduce. A `FAIL` that reopens
  nothing is a round nobody can act on.
* Do not add items, and do not touch the work itself.

{rules}
"""

EXPLORER_PROMPT = """The pipeline is idle: the verifier signed off and the todo list is complete.

What the user asked for: {task}

The todo list is a file: {path}

{todo}

The verifier's last word: {verdict}

{report}Decide what happens next. Read the ask above, read what this round landed, and
work out what the package needs next for that ask to be met — not what would
make the tree tidier, and not a second opinion on code the verifier already
passed. What you decide goes into that file as new items:

```json
{schema}
```

* Append one item per thing worth doing, in the order they should be done, each
  with `id` one higher than the highest id in the file, `state` `open`, and
  `added_by` `{me}`.
* An item is a unit of work one agent can finish in a turn — not a theme, and
  not a paragraph. Its `text` is the whole of what the worker will be told, so
  what "done" looks like goes in it too: a worker should not have to guess what
  you would accept.
* If the honest answer is that there is nothing worth adding, add nothing. An
  empty round is a real outcome: the loop waits and asks you again.

{rules}
"""


def _findings_block(verifier_report: str) -> str:
    if not verifier_report.strip():
        return ""
    return f"The verifier's findings on the last pass:\n\n{verifier_report.strip()}\n\n"


#: What the verifier is handed when there is no worker report to hand it: a
#: round resumed from the file, where the work was settled before this process
#: began. Saying so is better than an empty section, which reads as a worker
#: that had nothing to say.
NO_REPORT = (
    "There is no report to check. The work was settled before this round began — "
    "the state file outlives the process that wrote it — so the file and the "
    "repository are the whole account of it. Verify those.\n\n"
)


def _report_block(worker_report: str) -> str:
    if not worker_report.strip():
        return NO_REPORT
    return f"The worker reported:\n\n{worker_report.strip()}\n\n"


def _changes(before: Todo, after: Todo) -> list[str]:
    """What one reload earned: a line per item whose state moved, and a line per
    item that disappeared. Additions are :func:`_added` instead, because the
    notice that reports them says who added them and what they say."""
    was = {item.id: item for item in before.items}
    lines = []
    for item in after.items:
        old = was.pop(item.id, None)
        if old is not None and old.state != item.state:
            lines.append(f"todo {item.id} -> {item.state}")
    lines += [f"todo {item_id} removed" for item_id in was]
    return lines


def _added(before: Todo, after: Todo) -> list[Item]:
    """The items on the list now that were not on it before."""
    known = {item.id for item in before.items}
    return [item for item in after.items if item.id not in known]


async def _open(turn: Turn, path: Path, seed: Todo, *, announce: bool = True) -> Todo:
    """The list as the file says it is — or, with no file yet, the file written.

    The prompt is where the work comes from: one item, verbatim, and from then
    on the agents own the file. Nothing reads the prompt for structure, because
    a prompt is prose a human typed and the pipeline has no business guessing at
    it. A seed list with items in it wins over the prompt, which is how a caller
    starts a run with work already on the books.
    """
    if path.exists():
        return Todo.load(path)
    state = Todo.from_dict(seed.to_dict())
    if not len(state):
        state.add(turn.prompt, by="prompt")
    state.save(path)
    if announce:
        await turn.notice(
            f"todo seeded with {len(state)} items",
            description=f"{path}\n\n{state.render(only_pending=True)}",
        )
    return state


async def _reload(turn: Turn, path: Path, before: Todo) -> tuple[Todo, list[str]]:
    """Read back what an agent just wrote, and report what moved.

    A document that does not parse is announced upstream and the last good copy
    goes back in its place. Restoring it is not politeness: the loop reads this
    file to decide what happens next, so an unreadable list is a list that can
    never be finished. The failure has to be loud, and the run has to go on —
    the protocol this replaced dropped a report line it did not recognize,
    which is indistinguishable from an agent that agreed with you.
    """
    try:
        after = Todo.load(path)
    except ValueError as error:
        await turn.notice(
            f"{path.name} does not parse — {error}",
            severity="error",
            description="the last good copy is back in place; that agent's write is lost",
        )
        before.save(path)
        return Todo.from_dict(before.to_dict()), []
    return after, _changes(before, after)


def pipeline(
    worker: Role,
    verifier: Role,
    explorer: Role,
    todo: Todo,
    *,
    max_review: int = 5,
    announce: bool = True,
    state_path: Optional[Path] = None,
) -> Workflow:
    """The loop: worker -> verifier until sign-off on a complete list, then the
    explorer adds more items, then round again. It does not end by itself;
    ``session/cancel`` ends it.

    The state lives in the file at ``state_path`` — by default
    ``.crow-research/todo.json`` under the working directory the session was
    opened on. ``todo`` is what seeds that file the first time it is written;
    with no file and an empty seed, the prompt becomes the first item. From then
    on the file *is* the list: the orchestrator writes it once, reads it after
    every turn, and never edits it in between, because the agents are the ones
    doing the work. A run can therefore be picked up where it left off, and a
    human can add an item with a text editor.

    ``max_review`` bounds one round's worker/verifier exchange, not the loop. A
    pair that cannot agree still falls through to the explorer, so a stuck round
    costs a round instead of the run.

    Because the state is a file, a round can also *begin* with the work already
    done and unsigned — a run cancelled between the worker's write and the
    verifier's verdict. The loop picks that up where it left off: the worker is
    dispatched only when there is something to claim, so a resumed round goes
    straight to the verifier.

    Nothing in here is on a clock. A turn ends when the child ends it and the
    loop ends when the client cancels it — an explorer that adds nothing simply
    gets asked again, which is what "in perpetuity" means.
    """

    async def workflow(turn: Turn) -> Optional[str]:
        path = Path(state_path) if state_path else default_state_path(turn.cwd)
        state = await _open(turn, path, todo, announce=announce)
        last: Optional[str] = None
        verifier_report = ""
        round_no = 0
        while True:
            turn._check()
            round_no += 1
            # -- worker -> verifier, until the verifier signs off on a complete list
            worker_report = ""
            for _attempt in range(max_review):
                turn._check()
                # The exit condition: the list is done and somebody has signed
                # off on it. That is PASS, or a FAIL that reopened nothing — and
                # in the second case the pair has no move left, because the
                # worker has nothing to claim and the verdict is the only thing
                # that could change. Asking the same verifier again is a slot
                # machine, so the round falls through to the explorer, which is
                # what ``max_review`` exists for, minus the identical exchanges.
                #
                # What is *not* an exit is a complete list nobody has signed off
                # yet. The file outlives the process, so a run cancelled between
                # the worker's write and the verifier's verdict resumes here, and
                # the agent that owes the next move is the verifier.
                if state.complete and state.verdict is not None:
                    break
                # The worker goes only when there is something to claim. Round 2
                # of run 1 spent a worker turn and a verifier turn on an empty
                # list; a resumed round would spend one saying "nothing to do"
                # before the verifier ever saw the work.
                if not state.complete:
                    if announce:
                        await turn.notice(
                            f"round {round_no} · {worker.label}",
                            description=f"{len(state.pending())} pending · last verdict {state.verdict or 'none'}",
                        )
                    worker_result = await turn.run(
                        worker,
                        WORKER_PROMPT.format(
                            path=path,
                            todo=state.render(),
                            findings=_findings_block(verifier_report),
                            schema=STATE_SCHEMA,
                            rules=STATE_RULES,
                        ),
                    )
                    last = worker_result.stop_reason
                    worker_report = worker_result.report
                    state, moved = await _reload(turn, path, state)
                    for line in moved:
                        await turn.notice(line)
                    turn._check()

                if announce:
                    await turn.notice(f"round {round_no} · {verifier.label}", description="signing off on the pass")
                verifier_result = await turn.run(
                    verifier,
                    VERIFIER_PROMPT.format(
                        path=path, todo=state.render(), report=_report_block(worker_report),
                        schema=STATE_SCHEMA, rules=STATE_RULES,
                    ),
                )
                last = verifier_result.stop_reason
                state, moved = await _reload(turn, path, state)
                for line in moved:
                    await turn.notice(line)
                verifier_report = verifier_result.report
                if announce:
                    await turn.notice(
                        f"round {round_no} · verdict {state.verdict or 'UNREPORTED'}",
                        description=state.verdict_note or state.render(only_pending=True).splitlines()[0],
                        severity="info" if state.verdict == "PASS" else "warning",
                    )
            # -- the explorer adds more items to the todo
            turn._check()
            if announce:
                await turn.notice(f"round {round_no} · {explorer.label}", description="adding work to the todo")
            before = state
            explorer_result = await turn.run(
                explorer,
                EXPLORER_PROMPT.format(
                    task=turn.prompt.strip() or "(the client sent no task text)",
                    path=path, todo=state.render(), verdict=state.verdict or "UNREPORTED",
                    report=_findings_block(verifier_report), me=explorer.name,
                    schema=STATE_SCHEMA, rules=STATE_RULES,
                ),
            )
            last = explorer_result.stop_reason
            state, moved = await _reload(turn, path, state)
            added = _added(before, state)
            for line in moved:
                await turn.notice(line)
            if announce:
                await turn.notice(
                    f"round {round_no} · {explorer.label} added {len(added)} items",
                    description="; ".join(i.text[:60] for i in added) or "nothing added — the loop continues",
                )
            # The verdict stays in the file. It is the verifier's to set and the
            # next round's verifier overwrites it; the orchestrator writing null
            # over an agent's key would be the orchestrator doing the work.
            verifier_report = ""

    return workflow
