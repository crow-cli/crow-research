"""The todo list the pipeline works against.

One list, three agents, and no protocol between them beyond this object: the
worker claims items and reports on them, the verifier signs off on the work, the
explorer adds the items the work made possible. It lives in the orchestrator's
process because that is the only place all three agents are visible at once —
the alternative is asking one agent to parse another agent's prose for state,
which is a handoff that fails silently and looks like agreement.

The list is also the loop's exit condition, which is why it is a real object
rather than a section of a prompt: "until the verifier signs off AND the todo
list is complete" has to be *evaluated*, and an evaluation needs a data
structure, not a vibe.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from typing import Iterator, Optional

#: An item's life. ``open`` -> ``working`` -> ``done`` (verified) or back to
#: ``open`` (the verifier found a hole). ``blocked`` is a dead end a human or an
#: explorer has to reopen.
OPEN, WORKING, DONE, BLOCKED = "open", "working", "done", "blocked"


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


@dataclass
class Todo:
    """The shared list. Small on purpose: add, claim, settle, render."""

    items: list[Item] = field(default_factory=list)
    _ids: Iterator[int] = field(default_factory=lambda: itertools.count(1), repr=False)

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
        item = self[item_id]
        item.state = state
        if note:
            item.note = note.strip()
        return item

    def __getitem__(self, item_id: int) -> Item:
        for item in self.items:
            if item.id == item_id:
                return item
        raise KeyError(f"no todo item {item_id}")

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

    def __len__(self) -> int:
        return len(self.items)

    # -- rendering ------------------------------------------------------

    def render(self, *, only_pending: bool = False) -> str:
        """The list as the agents see it. This text is the wire format: the
        worker reads it to know what to claim, and writes ids back."""
        shown = self.pending() if only_pending else self.items
        if not shown:
            return "(the todo list is empty)"
        head = f"TODO ({len(self.pending())} pending / {len(self.items)} total)"
        return head + "\n" + "\n".join(i.render() for i in shown)


def parse_updates(report: str) -> list[tuple[int, str, str]]:
    """``(id, state, note)`` triples out of an agent's report.

    The contract the worker and verifier are given is one line per item::

        TODO 3 done built the parser, tests pass
        TODO 5 open the empty input still crashes

    Parsing is deliberately dumb and deliberately loud about nothing: a line
    that does not start with ``TODO <id> <state>`` is prose and is ignored, so
    an agent that explains itself at length cannot corrupt the list. A state we
    do not know is ignored for the same reason — inventing one is the agent's
    mistake to make, not ours to guess at.
    """
    out: list[tuple[int, str, str]] = []
    for line in (report or "").splitlines():
        parts = line.strip().split(None, 3)
        if len(parts) < 3 or parts[0].upper() != "TODO":
            continue
        try:
            item_id = int(parts[1])
        except ValueError:
            continue
        state = parts[2].lower()
        if state not in (OPEN, WORKING, DONE, BLOCKED):
            continue
        out.append((item_id, state, parts[3] if len(parts) > 3 else ""))
    return out


def parse_new_items(report: str) -> list[str]:
    """The explorer's additions: ``ADD <text>`` lines, in order."""
    out: list[str] = []
    for line in (report or "").splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) == 2 and parts[0].upper() == "ADD":
            out.append(parts[1].strip())
    return out


def parse_verdict(report: str) -> Optional[str]:
    """``PASS`` / ``FAIL`` / ``UNVERIFIED`` out of a verifier's report, or None.

    The last verdict wins, because an agent that reasons its way from FAIL to
    PASS in one message has changed its mind and the later word is the verdict.
    """
    verdict: Optional[str] = None
    for line in (report or "").splitlines():
        head, sep, rest = line.strip().partition(":")
        if sep and head.upper().lstrip("#* ") == "VERDICT":
            word = rest.strip().split(None, 1)[0].upper() if rest.strip() else ""
            if word in ("PASS", "FAIL", "UNVERIFIED"):
                verdict = word
    return verdict
