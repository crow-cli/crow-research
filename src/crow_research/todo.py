"""The todo list the pipeline works against.

One list, three agents, and no protocol between them beyond this object: the
worker claims items and reports on them, the verifier signs off on the work, the
explorer adds the items the work made possible.

The list is a JSON file the agents edit and the orchestrator reads —
``.crow-research/todo.json`` under the working directory the session was opened
on. That file is the whole handoff. The alternative is asking one agent to parse
another agent's prose for state, which fails silently and looks like agreement:
a report line the model happened to wrap in backticks read as prose, the item
stayed open, and the worker was dispatched again to do work it had already done.
A file has no parser to defeat — ``json.load`` either works or raises, and a
raise is an event the orchestrator announces instead of a list that quietly
stops matching what the agents think it says.

It is written ``indent=2`` because the agents write it with the same ``edit``
tool they use on source: structured text a model can patch in place, rather
than a document it has to regenerate from memory.

The list is also the loop's exit condition, which is why it is a real object
rather than a section of a prompt: "until the verifier signs off AND the todo
list is complete" has to be *evaluated*, and an evaluation needs a data
structure, not a vibe.
"""

from __future__ import annotations

import itertools
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator, Optional

from .campaign import (
    NONE,
    Attempt,
    Best,
    Conclusions,
    Idea,
    Rubric,
    RubricStatus,
    TaskSpec,
)

#: An item's life. ``open`` -> ``working`` -> ``done`` (verified) or back to
#: ``open`` (the verifier found a hole). ``blocked`` is a dead end a human or an
#: explorer has to reopen.
OPEN, WORKING, DONE, BLOCKED = "open", "working", "done", "blocked"

#: Every state an item may be in. Anything else in the file is a ``ValueError``
#: on load, not a guess: an invented state is the agent's mistake to make.
STATES = (OPEN, WORKING, DONE, BLOCKED)

#: What a verifier may conclude. ``None`` in the file means it has not yet.
VERDICTS = ("PASS", "FAIL", "UNVERIFIED")

#: Where the list lives, relative to the session's working directory. Hidden,
#: because it is the pipeline's state and not part of anyone's source tree.
STATE_FILE = ".crow-research/todo.json"


def state_path(cwd: str | Path) -> Path:
    """The state file for one working directory."""
    return Path(cwd) / STATE_FILE


def _each(raw: Any, key: str, parse) -> list:
    """Parse a list-valued key of the document, or reject it naming the key.

    Absent reads as empty: every campaign key is optional, so a file written
    before the campaign existed still loads and still means what it said.
    """
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError(f"'{key}' is a list, got {type(raw).__name__}")
    return [parse(entry) for entry in raw]


@dataclass
class Item:
    """One unit of work."""

    id: int
    text: str
    state: str = OPEN
    note: str = ""
    added_by: str = ""

    def render(self) -> str:
        mark = {OPEN: "[ ]", WORKING: "[~]", DONE: "[x]", BLOCKED: "[!]"}[self.state]
        note = f" — {self.note}" if self.note else ""
        return f"{self.id}. {mark} {self.text}{note}"

    # -- the file -------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """This item as the state file holds it. Every key, always: an agent
        editing the file should not have to wonder which ones are optional."""
        return {
            "id": self.id,
            "text": self.text,
            "state": self.state,
            "note": self.note,
            "added_by": self.added_by,
        }

    @classmethod
    def from_dict(cls, doc: Any) -> Item:
        """One item out of the file. Anything the contract does not describe is
        a ``ValueError`` naming what is wrong — the message goes upstream to
        whoever is watching, so it is written for them, not for a log."""
        if not isinstance(doc, dict):
            raise ValueError(f"an item is a JSON object, got {type(doc).__name__}")
        item_id = doc.get("id")
        if isinstance(item_id, bool) or not isinstance(item_id, int):
            raise ValueError(f"an item's id is an integer, got {item_id!r}")
        text = doc.get("text")
        if not isinstance(text, str) or not text.strip():
            raise ValueError(f"item {item_id} has no text")
        state = doc.get("state", OPEN)
        if not isinstance(state, str) or state.lower() not in STATES:
            raise ValueError(f"item {item_id} has state {state!r}, expected one of {'/'.join(STATES)}")
        state = state.lower()
        note, added_by = doc.get("note", ""), doc.get("added_by", "")
        for name, value in (("note", note), ("added_by", added_by)):
            if not isinstance(value, str):
                raise ValueError(f"item {item_id}'s {name} is a string, got {type(value).__name__}")
        return cls(id=item_id, text=text.strip(), state=state, note=note.strip(), added_by=added_by)


@dataclass
class Todo:
    """The shared list, and the campaign record hanging off it.

    ``verdict`` and ``verdict_note`` are the verifier's sign-off. They live on
    the list rather than in a report because the loop's exit condition reads
    them: "the verifier signed off AND the todo list is complete" is one
    expression over this object.

    ``task``, ``ideas``, ``attempts``, ``best`` and ``conclusions`` are the
    scientific campaign — see :mod:`crow_research.campaign` for what each one is
    and who owns it. They are optional and default to empty, so a file written
    before the campaign existed still loads: the list came first and a campaign
    is a list plus evidence, not a different document.
    """

    items: list[Item] = field(default_factory=list)
    verdict: Optional[str] = None
    verdict_note: str = ""
    #: The executable task specification, or ``None`` until the drafter writes one.
    task: Optional[TaskSpec] = None
    #: Ideas the explorer proposed, in the order it proposed them.
    ideas: list[Idea] = field(default_factory=list)
    #: Attempts the worker ran against them, in the order they were recorded.
    attempts: list[Attempt] = field(default_factory=list)
    #: The best *correct* program so far. Host-owned: :meth:`record_attempt` is
    #: the only thing that moves it.
    best: Optional[Best] = None
    #: The distilled paragraph the next round starts from.
    conclusions: Conclusions = field(default_factory=Conclusions)
    #: Where the next id comes from. Not part of the repr and not part of
    #: equality: two lists holding the same items are the same list, whatever
    #: their counters have been through.
    _ids: Iterator[int] = field(
        default_factory=lambda: itertools.count(1), repr=False, compare=False
    )
    _idea_ids: Iterator[int] = field(
        default_factory=lambda: itertools.count(1), repr=False, compare=False
    )
    _attempt_ids: Iterator[int] = field(
        default_factory=lambda: itertools.count(1), repr=False, compare=False
    )

    # -- mutation -------------------------------------------------------

    def add(self, text: str, *, by: str = "") -> Item:
        """Append one item. Text is stripped and empties are refused: an empty
        item is a loop that never completes."""
        text = (text or "").strip()
        if not text:
            raise ValueError("a todo item needs text")
        item = Item(id=next(self._ids), text=text, added_by=by)
        self.items.append(item)
        return item

    def extend(self, texts, *, by: str = "") -> list[Item]:
        return [self.add(t, by=by) for t in texts if (t or "").strip()]

    def set(self, item_id: int, state: str, note: str = "") -> Item:
        """Claim or settle one item.

        The same contract the file enforces on load: a state that is not one of
        the four is refused here as a ``ValueError`` instead of surfacing later
        as a ``KeyError`` out of :meth:`Item.render`. Case is forgiven —
        ``Done`` reads as ``done``; invention is not.
        """
        if not isinstance(state, str) or state.lower() not in STATES:
            raise ValueError(f"state {state!r}, expected one of {'/'.join(STATES)}")
        item = self[item_id]
        item.state = state.lower()
        if note:
            item.note = note.strip()
        return item

    def remove(self, item_id: int) -> Item:
        """Delete one item by id and return it.

        Ids are never reused: ``add`` keeps counting past every id ever handed
        out, so a removed id cannot be silently resurrected by the next item —
        an agent holding a stale reference to it fails loudly instead of
        mutating something new.
        """
        item = self[item_id]
        self.items.remove(item)
        return item

    def __getitem__(self, item_id: int) -> Item:
        for item in self.items:
            if item.id == item_id:
                return item
        raise KeyError(f"no todo item {item_id}")

    def get(self, item_id: int) -> Optional[Item]:
        """The item with that id, or ``None`` when it is not on the list.

        The quiet sibling of :meth:`__getitem__`: an unknown id comes back as
        ``None`` instead of a ``KeyError``, for callers that ask before they
        act. Lookup follows the list, not the id counter — a removed item's
        id reads as ``None`` immediately, and an id the counter has not handed
        out yet never reads as anything at all.
        """
        try:
            return self[item_id]
        except KeyError:
            return None

    # -- queries --------------------------------------------------------

    def pending(self) -> list[Item]:
        """Everything not finished: what the worker may claim next."""
        return [i for i in self.items if i.state in (OPEN, WORKING)]

    def claimed(self) -> list[Item]:
        return [i for i in self.items if i.state == WORKING]

    @property
    def complete(self) -> bool:
        """The loop's exit condition, second half. Nothing left to do."""
        return not self.pending()

    def __contains__(self, item_id: object) -> bool:
        """``item_id in todo`` — is that id on the list right now?

        Membership follows the list, not the id counter: a removed item's id
        stops being a member immediately, and an id the counter has not handed
        out yet is not a member either. Never raises — an unknown id is simply
        absent, unlike :meth:`__getitem__`, which fails loudly.
        """
        return any(i.id == item_id for i in self.items)

    def __len__(self) -> int:
        return len(self.items)

    # -- the campaign ---------------------------------------------------
    #
    # Everything below this line is host-owned. The agents write the file with
    # the same ``edit`` tool they use on source, and ``from_dict`` is what checks
    # what they wrote; these methods are what the orchestrator does with it
    # afterwards — assigning ids, scaffolding the fields an agent is allowed to
    # fill, and computing the aggregates nobody is allowed to assert.

    def add_idea(self, title: str, hypothesis: str, plan, rubrics, *,
                 notes: str = "", by: str = "") -> Idea:
        """Record one idea. The host assigns the id, the explorer supplies the rest.

        Validated by construction: building the :class:`Idea` and reading it back
        through ``from_dict`` is the same check the file would apply on the next
        load, so a bad bundle is refused here rather than poisoning the document.
        """
        idea = Idea.from_dict({
            "id": next(self._idea_ids), "title": title, "hypothesis": hypothesis,
            "plan": list(plan), "rubrics": [r.to_dict() if isinstance(r, Rubric) else r for r in rubrics],
            "notes": notes, "added_by": by,
        })
        self.ideas.append(idea)
        return idea

    def idea(self, idea_id: int) -> Optional[Idea]:
        return next((i for i in self.ideas if i.id == idea_id), None)

    def latest_idea(self) -> Optional[Idea]:
        """The idea the next attempt tests, or ``None`` before the first one."""
        return self.ideas[-1] if self.ideas else None

    def scaffold_rubrics(self, idea: Optional[Idea] = None) -> list[dict[str, Any]]:
        """The rubric statuses an agent is allowed to fill: ``pass`` and ``evidence``.

        Pre-created ``false`` with empty evidence, so a rubric nobody looked at
        reads as unpassed rather than absent — and so the aggregate is the host's
        to compute. This is the scaffold their harness writes into
        ``rubric_status.json`` before the verification agent is even dispatched.
        """
        target = idea if idea is not None else self.latest_idea()
        if target is None:
            return []
        return [{"id": r.id, "pass": False, "evidence": ""} for r in target.rubrics]

    def next_program_index(self) -> int:
        """The next free ``program_<idx>``. Candidates are append-only, so this
        only ever counts up: an older program is evidence and is never overwritten."""
        highest = 0
        for attempt in self.attempts:
            match = re.search(r"program_(\d+)", attempt.program)
            if match:
                highest = max(highest, int(match.group(1)))
        return highest + 1

    def record_attempt(self, idea_id: int, program: str, metrics: dict[str, Any],
                       correct: bool, stop_reason: str, *, note: str = "") -> Attempt:
        """Record one evaluation and apply the ``best`` gate.

        Two independent gates, as in their §3.3: every rubric passing closes the
        idea's contract, while moving ``best`` needs a *correct* candidate with a
        *strictly* higher score. A rubric pass on a worse program is a finished
        contract and no progress; a better score with a failing rubric is
        progress the campaign is not allowed to bank.

        Recording is therefore not the gate — it cannot be, because nothing has
        been verified yet. The rubric statuses start as the scaffold, filling
        them is the verifier's turn through :meth:`apply_rubric_status`, and that
        is where ``best`` moves. The worker reports what the evaluator measured,
        the verifier judges it, and the two writers never share a field.
        """
        idea = self.idea(idea_id)
        if idea is None:
            raise ValueError(f"attempt tests idea {idea_id}, which is not in 'ideas'")
        attempt = Attempt.from_dict({
            "id": next(self._attempt_ids), "idea_id": idea_id, "program": program,
            "metrics": metrics, "correct": correct,
            "rubrics": self.scaffold_rubrics(idea), "stop_reason": stop_reason, "note": note,
        })
        self.attempts.append(attempt)
        return attempt

    def apply_rubric_status(self, attempt_id: int, statuses, *,
                            next_recommendation: str = "") -> Attempt:
        """Take the verifier's judgment on one attempt's rubrics.

        Only ``pass``, ``evidence`` and ``next_recommendation`` are read; ids come
        from the scaffold, and ``overall_pass`` is computed rather than accepted.
        A status for a rubric the idea never named is refused — an invented
        criterion is how a verifier starts grading its own taste instead of the
        contract.
        """
        attempt = next((a for a in self.attempts if a.id == attempt_id), None)
        if attempt is None:
            raise ValueError(f"no attempt {attempt_id}")
        expected = {r.id for r in attempt.rubrics}
        filled: dict[str, RubricStatus] = {}
        for raw in statuses:
            status = RubricStatus.from_dict(raw, where=f"attempt {attempt_id}'s rubric")
            if status.id not in expected:
                raise ValueError(
                    f"attempt {attempt_id} was scored on rubric {status.id!r}, which the idea "
                    f"never named — expected {'/'.join(sorted(expected)) or '(none)'}"
                )
            filled[status.id] = status
        merged = tuple(filled.get(r.id, r) for r in attempt.rubrics)
        index = self.attempts.index(attempt)
        updated = Attempt(id=attempt.id, idea_id=attempt.idea_id, program=attempt.program,
                          metrics=attempt.metrics, correct=attempt.correct, rubrics=merged,
                          stop_reason=attempt.stop_reason, note=attempt.note,
                          next_recommendation=next_recommendation.strip())
        self.attempts[index] = updated
        self._consider_best(updated)
        return updated

    def _consider_best(self, attempt: Attempt) -> bool:
        """The gate, and the only place it lives.

        The rubrics have to have been judged, the candidate has to be correct,
        and it has to *strictly* beat the incumbent — a tie is not progress and
        does not earn the campaign a new reference program.
        """
        score = attempt.score
        if not (attempt.overall_pass and attempt.correct) or score is None:
            return False
        if self.best is not None and score <= self.best.score:
            return False
        self.best = Best(program=attempt.program, score=score, attempt_id=attempt.id)
        return True

    def recompute_best(self) -> Optional[Best]:
        """Re-derive ``best`` from the attempts in the file.

        The host's half of the split, and idempotent: replaying every attempt
        through the gate in order lands on the same incumbent whether it is
        called once or after every turn. Which is the point — an agent that
        wrote a ``best`` of its own has not changed the campaign, only the file,
        and this puts the file back to what the evidence supports.
        """
        self.best = None
        for attempt in self.attempts:
            self._consider_best(attempt)
        return self.best

    def repair_scaffolds(self) -> list[str]:
        """Put every attempt's rubric *ids* back to the ids its idea named.

        Called before the verifier is dispatched, so the scaffold it fills is the
        scaffold the explorer wrote: a rubric the worker left out comes back as
        ``pass`` false with empty evidence, and one it invented is dropped — an
        invented criterion is how a round starts being graded on something nobody
        proposed. Statuses whose id does match are left alone, because the values
        are the verifier's and it is about to write every one of them.

        Returns a line per attempt it repaired, for the orchestrator to announce.
        """
        lines: list[str] = []
        for index, attempt in enumerate(self.attempts):
            idea = self.idea(attempt.idea_id)
            if idea is None:
                continue
            expected = [r.id for r in idea.rubrics]
            if [r.id for r in attempt.rubrics] == expected:
                continue
            filled = {r.id: r for r in attempt.rubrics}
            merged = tuple(filled.get(rid, RubricStatus(id=rid, passed=False, evidence=""))
                           for rid in expected)
            dropped = sorted(set(filled) - set(expected))
            self.attempts[index] = Attempt(
                id=attempt.id, idea_id=attempt.idea_id, program=attempt.program,
                metrics=attempt.metrics, correct=attempt.correct, rubrics=merged,
                stop_reason=attempt.stop_reason, note=attempt.note,
                next_recommendation=attempt.next_recommendation,
            )
            detail = f", dropped {'/'.join(dropped)}" if dropped else ""
            lines.append(f"attempt {attempt.id} scaffolded with {len(expected)} rubrics{detail}")
        return lines

    def set_conclusions(self, text: str, *, trigger: str, reason: str = "") -> Conclusions:
        """Replace the distilled paragraph, under the gate.

        ``trigger`` ``none`` means the round reconfirmed what was there, and the
        text is left alone rather than rewritten — simple reconfirmation is not a
        reason to touch a paragraph the next agent has to read.
        """
        if trigger == NONE:
            return self.conclusions
        self.conclusions = Conclusions.validate(Conclusions(text=text.strip(), trigger=trigger, reason=reason.strip()))
        return self.conclusions

    # -- rendering ------------------------------------------------------

    def render(self, *, only_pending: bool = False) -> str:
        """The list as the agents see it. This text is the wire format: the
        worker reads it to know what to claim, and writes ids back."""
        shown = self.pending() if only_pending else self.items
        if not shown:
            return "(the todo list is empty)"
        head = f"TODO ({len(self.pending())} pending / {len(self.items)} total)"
        return head + "\n" + "\n".join(i.render() for i in shown)

    def render_campaign(self, *, attempts: int = 5) -> str:
        """The campaign as the agents see it, in the same voice as :meth:`render`.

        Kept separate from the list on purpose: ``render()`` is a wire format
        three existing roles and forty-odd tests already read, and appending a
        research digest to it would change what "the todo list" says. This is the
        rest of the document — the goal, the incumbent, the idea under test, the
        last few attempts and the distilled paragraph — and a dispatch prompt
        includes it when the role needs evidence rather than just work.

        ``attempts`` bounds how many of the most recent are shown. Not a cap on
        anything the loop does: the file keeps every attempt, and an agent that
        needs older ones reads the file.
        """
        out: list[str] = []
        if self.task:
            out.append(f"TASK GOAL\n{self.task.goal}")
            out.append("TASK RUBRICS\n" + "\n".join(
                f"- {r.id} ({r.type}): {r.description}" for r in self.task.rubrics))
            out.append(f"EVALUATOR {self.task.evaluator}\nBASELINE {self.task.program0}")
        if self.best:
            out.append(f"BEST {self.best.program} — combined_score {self.best.score} "
                       f"(attempt {self.best.attempt_id})")
        elif self.task:
            out.append("BEST (none yet — no correct candidate has been recorded)")
        latest = self.latest_idea()
        if latest:
            out.append(
                f"IDEA {latest.id} — {latest.title}\n"
                f"hypothesis: {latest.hypothesis}\n"
                + "\n".join(f"- {s}" for s in latest.plan)
                + "\nrubrics to satisfy:\n"
                + "\n".join(f"- {r.id} ({r.type}): {r.description}" for r in latest.rubrics)
            )
        shown = self.attempts[-attempts:] if attempts else []
        if shown:
            lines = []
            for a in shown:
                passed = sum(1 for r in a.rubrics if r.passed)
                score = a.score
                lines.append(
                    f"- {a.program} (idea {a.idea_id}, attempt {a.id}): "
                    f"combined_score {'n/a' if score is None else score}, "
                    f"correct {str(a.correct).lower()}, rubrics {passed}/{len(a.rubrics)}, "
                    f"stopped {a.stop_reason}"
                    + (f" — {a.next_recommendation}" if a.next_recommendation else "")
                )
            out.append(f"ATTEMPTS (last {len(shown)} of {len(self.attempts)})\n" + "\n".join(lines))
        if self.conclusions.text:
            out.append(f"CONCLUSIONS ({self.conclusions.trigger})\n{self.conclusions.text}")
        return "\n\n".join(out) if out else "(no campaign yet — there is no task specification)"

    # -- the file -------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """The document. Key order is the order the agents are shown it in.

        ``task`` first, because it is what every other key is in service of, and
        ``best`` next to ``attempts``, because reading one without the other is
        how a campaign loses track of whether it is improving.
        """
        return {
            "task": self.task.to_dict() if self.task else None,
            "verdict": self.verdict,
            "verdict_note": self.verdict_note,
            "items": [item.to_dict() for item in self.items],
            "ideas": [idea.to_dict() for idea in self.ideas],
            "attempts": [attempt.to_dict() for attempt in self.attempts],
            "best": self.best.to_dict() if self.best else None,
            "conclusions": self.conclusions.to_dict(),
        }

    @classmethod
    def from_dict(cls, doc: Any) -> Todo:
        """The list a document describes.

        Validating is the whole point of the method: this is the only place an
        agent's write is checked, and everything it rejects is a ``ValueError``
        the orchestrator announces upstream before putting the last good copy
        back. Nothing here guesses at what the agent meant.
        """
        if not isinstance(doc, dict):
            raise ValueError(f"the state file is a JSON object, got {type(doc).__name__}")
        raw = doc.get("items", [])
        if not isinstance(raw, list):
            raise ValueError(f"'items' is a list, got {type(raw).__name__}")
        items = [Item.from_dict(entry) for entry in raw]
        seen: set[int] = set()
        for item in items:
            if item.id in seen:
                raise ValueError(f"two items claim id {item.id}")
            seen.add(item.id)
        verdict = doc.get("verdict")
        if verdict is not None:
            if not isinstance(verdict, str) or verdict.upper() not in VERDICTS:
                raise ValueError(f"verdict is {verdict!r}, expected null or one of {'/'.join(VERDICTS)}")
            verdict = verdict.upper()
        note = doc.get("verdict_note", "")
        if not isinstance(note, str):
            raise ValueError(f"verdict_note is a string, got {type(note).__name__}")

        raw_task = doc.get("task")
        task = TaskSpec.from_dict(raw_task) if raw_task is not None else None
        ideas = _each(doc.get("ideas", []), "ideas", Idea.from_dict)
        attempts = _each(doc.get("attempts", []), "attempts", Attempt.from_dict)
        known = {idea.id for idea in ideas}
        for attempt in attempts:
            if attempt.idea_id not in known:
                raise ValueError(
                    f"attempt {attempt.id} tests idea {attempt.idea_id}, which is not in 'ideas' "
                    f"— an attempt without an idea is evidence for nothing"
                )
        raw_best = doc.get("best")
        best = Best.from_dict(raw_best) if raw_best is not None else None
        conclusions = Conclusions.from_dict(doc.get("conclusions"))

        todo = cls(items=items, verdict=verdict, verdict_note=note.strip(), task=task,
                   ideas=ideas, attempts=attempts, best=best, conclusions=conclusions)
        # Ids are never reused, so a loaded list keeps counting past every id in
        # the file: the next ``add`` must not collide with an item some agent is
        # still holding a reference to. Same for ideas and attempts.
        todo._ids = itertools.count(max(seen, default=0) + 1)
        todo._idea_ids = itertools.count(max((i.id for i in ideas), default=0) + 1)
        todo._attempt_ids = itertools.count(max((a.id for a in attempts), default=0) + 1)
        return todo

    @classmethod
    def load(cls, path: str | Path) -> Todo:
        """Read the file.

        A document that is not the shape above raises ``ValueError`` —
        ``json.JSONDecodeError`` is one — and a missing file raises
        ``FileNotFoundError``. Both are decisions for the caller; neither is
        something to paper over with an empty list, which is what a silently
        reset todo looks like from inside the loop.
        """
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))

    def save(self, path: str | Path) -> Path:
        """Write the file, atomically.

        Through a temporary and a rename, so a reader that arrives mid-write —
        an agent, or a cancel — sees the old document or the new one, never a
        truncated third thing.
        """
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(self.to_dict(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        os.replace(tmp, path)
        return path
