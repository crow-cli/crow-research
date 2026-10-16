"""The campaign record: what a scientific loop knows between rounds.

``todo.py`` is the work queue. This is the evidence — the task the campaign is
against, the ideas that were proposed, the attempts that tested them, the best
program found so far, and the one paragraph of distilled conclusions the next
round starts from. Both live in the same JSON file, because one contract the
agents edit beats two that can disagree.

The split of ownership is the point, and it is SakanaAI's (arXiv 2610.18933,
§3.3): **the host owns the derived fields and the agent owns the judgment.** An
agent fills in ``pass`` and ``evidence`` for a rubric; the host computes
``overall_pass`` from them. An agent reports ``metrics`` and ``correct``; the
host decides whether that moves ``best``. Anything an agent writes into a
host-owned field is recomputed on load rather than believed — not because the
agent is a liar, but because a field two writers can set is a field nobody can
read.

Nothing here is on a clock and nothing here is capped. Their loop ends when
``max_idea_cycles`` or ``max_program_evals_per_attempt`` runs out; this one ends
when the client sends ``session/cancel``. An attempt therefore has no
``cap_reached`` stop reason, and an idea has no generation-attempt counter: a
rejected idea is announced and the next round proposes another.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional

#: A rubric is either about a number the evaluator produced or about the code
#: that produced it. Their two types, unchanged: a bundle with only one of them
#: is either untestable or unmeasurable.
METRIC, IMPLEMENTATION = "metric", "implementation"
RUBRIC_TYPES = (METRIC, IMPLEMENTATION)

#: Why an attempt stopped. Only the first is load-bearing — it means the idea's
#: contract is complete. There is no ``cap_reached``: nothing is capped.
RUBRIC_PASS, EVALUATION_FAILED, STOPPED = "rubric_pass", "evaluation_failed", "stopped"
STOP_REASONS = (RUBRIC_PASS, EVALUATION_FAILED, STOPPED)

#: The only reasons a conclusions paragraph may be rewritten. Their gate, kept
#: because the alternative is a memory file that churns every round and says
#: nothing by round ten. ``none`` means the round confirmed what was there and
#: the text stays as it is.
CONTRADICTS, NEW_CATEGORY, FLIPS, REFINES, NONE = (
    "contradicts", "new_category", "flips", "refines", "none",
)
GATE_TRIGGERS = (CONTRADICTS, NEW_CATEGORY, FLIPS, REFINES, NONE)

#: A rubric id is stable snake_case, because it is referenced from the idea, the
#: attempt and the scaffold, and an id that drifts in case between them is three
#: rubrics instead of one.
_SNAKE = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$")

#: Their limit on the distilled paragraph. Long enough to carry a do and a
#: don't, short enough that the next agent reads it.
CONCLUSIONS_MAX_WORDS = 300


def _require(cond: bool, message: str) -> None:
    """Raise the message as a ``ValueError`` when the condition does not hold.

    Every rejection in this module goes through here, because every one of them
    ends up announced upstream to whoever is watching the stream: the message is
    written for them, not for a log.
    """
    if not cond:
        raise ValueError(message)


@dataclass(frozen=True)
class Rubric:
    """One checkable criterion. Written by the explorer, checked by the verifier.

    ``verification`` is optional and, when present, is a JSON object describing
    how to check — ``{"type": "source_contains", "patterns": [...]}`` — never a
    prose string. Prose in a verification field is a criterion nobody can fail.
    """

    id: str
    description: str
    type: str
    verification: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        doc: dict[str, Any] = {"id": self.id, "description": self.description, "type": self.type}
        if self.verification:
            doc["verification"] = self.verification
        return doc

    @classmethod
    def from_dict(cls, doc: Any, *, where: str = "a rubric") -> Rubric:
        _require(isinstance(doc, dict), f"{where} is a JSON object, got {type(doc).__name__}")
        rid = doc.get("id")
        _require(isinstance(rid, str) and bool(_SNAKE.match(rid)),
                 f"{where}'s id is stable snake_case, got {rid!r}")
        description = doc.get("description")
        _require(isinstance(description, str) and bool(description.strip()),
                 f"rubric {rid} has no description")
        rtype = doc.get("type")
        _require(isinstance(rtype, str) and rtype.lower() in RUBRIC_TYPES,
                 f"rubric {rid} has type {rtype!r}, expected one of {'/'.join(RUBRIC_TYPES)}")
        verification = doc.get("verification", {})
        _require(isinstance(verification, dict),
                 f"rubric {rid}'s verification is a JSON object, never prose — "
                 f"got {type(verification).__name__}")
        return cls(id=rid, description=description.strip(), type=rtype.lower(), verification=verification)


def _rubrics(raw: Any, *, where: str, require_both: bool) -> tuple[Rubric, ...]:
    """A rubric bundle: unique ids, and — for an idea or a task — both types.

    ``require_both`` is their rule and it earns its place: a bundle of only
    ``metric`` rubrics can be satisfied by a program that does the wrong thing
    well, and a bundle of only ``implementation`` rubrics can be satisfied by a
    program that does the right thing to no effect.
    """
    _require(isinstance(raw, list) and bool(raw), f"{where} is a non-empty list of rubrics")
    rubrics = tuple(Rubric.from_dict(entry, where=f"{where}[{i}]") for i, entry in enumerate(raw))
    seen: set[str] = set()
    for rubric in rubrics:
        _require(rubric.id not in seen, f"{where} names rubric {rubric.id!r} twice")
        seen.add(rubric.id)
    if require_both:
        kinds = {r.type for r in rubrics}
        for missing in (METRIC, IMPLEMENTATION) :
            _require(missing in kinds, f"{where} has no {missing} rubric — it needs at least one of each")
    return rubrics


@dataclass(frozen=True)
class Idea:
    """One hypothesis and the rubrics that would confirm it.

    The explorer writes this and nothing else, and it writes it *before* any
    code exists: intent and evidence stay separate, so the worker is testing a
    stated hypothesis rather than reverse-engineering one from a conversation.
    """

    id: int
    title: str
    hypothesis: str
    plan: tuple[str, ...]
    rubrics: tuple[Rubric, ...]
    notes: str = ""
    added_by: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "hypothesis": self.hypothesis,
            "plan": list(self.plan),
            "rubrics": [r.to_dict() for r in self.rubrics],
            "notes": self.notes,
            "added_by": self.added_by,
        }

    @classmethod
    def from_dict(cls, doc: Any) -> Idea:
        _require(isinstance(doc, dict), f"an idea is a JSON object, got {type(doc).__name__}")
        idea_id = doc.get("id")
        _require(isinstance(idea_id, int) and not isinstance(idea_id, bool),
                 f"an idea's id is an integer, got {idea_id!r}")
        title = doc.get("title")
        _require(isinstance(title, str) and bool(title.strip()), f"idea {idea_id} has no title")
        hypothesis = doc.get("hypothesis")
        _require(isinstance(hypothesis, str) and bool(hypothesis.strip()),
                 f"idea {idea_id} has no hypothesis — an experiment without one is a guess with a budget")
        plan = doc.get("plan")
        _require(isinstance(plan, list) and bool(plan)
                 and all(isinstance(s, str) and s.strip() for s in plan),
                 f"idea {idea_id}'s plan is a non-empty list of short strings")
        rubrics = _rubrics(doc.get("rubrics"), where=f"idea {idea_id}'s rubrics", require_both=True)
        for name in ("notes", "added_by"):
            _require(isinstance(doc.get(name, ""), str),
                     f"idea {idea_id}'s {name} is a string, got {type(doc.get(name)).__name__}")
        return cls(id=idea_id, title=title.strip(), hypothesis=hypothesis.strip(),
                   plan=tuple(s.strip() for s in plan), rubrics=rubrics,
                   notes=doc.get("notes", "").strip(), added_by=doc.get("added_by", ""))


@dataclass(frozen=True)
class RubricStatus:
    """One rubric's verdict, with the evidence for it.

    ``passed`` because ``pass`` is a keyword; the file says ``pass``, which is
    what their schema says and what an agent will have seen.
    """

    id: str
    passed: bool
    evidence: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "pass": self.passed, "evidence": self.evidence}

    @classmethod
    def from_dict(cls, doc: Any, *, where: str = "a rubric status") -> RubricStatus:
        _require(isinstance(doc, dict), f"{where} is a JSON object, got {type(doc).__name__}")
        rid = doc.get("id")
        _require(isinstance(rid, str) and bool(_SNAKE.match(rid)), f"{where}'s id is snake_case, got {rid!r}")
        passed = doc.get("pass")
        _require(isinstance(passed, bool), f"{where} {rid}'s pass is a boolean, got {passed!r}")
        evidence = doc.get("evidence", "")
        _require(isinstance(evidence, str), f"{where} {rid}'s evidence is a string")
        return cls(id=rid, passed=passed, evidence=evidence.strip())


@dataclass(frozen=True)
class Attempt:
    """One program evaluated against one idea's rubrics.

    ``overall_pass`` is not a field: it is computed from ``rubrics`` every time,
    which is what makes it the host's. An agent that writes ``true`` over a
    failing rubric has not changed the record, only the file, and the next load
    puts it right.

    ``idea_id`` is ``None`` when no idea was under test — the first round works
    on the item the client typed, before the explorer has proposed anything, and
    the rubrics it is graded on are the campaign's own ``task.rubrics``. An
    attempt with no idea is still evidence: it is the baseline the ideas have to
    beat.
    """

    id: int
    idea_id: Optional[int]
    program: str
    metrics: dict[str, Any]
    correct: bool
    rubrics: tuple[RubricStatus, ...]
    stop_reason: str
    note: str = ""
    next_recommendation: str = ""

    @property
    def overall_pass(self) -> bool:
        return bool(self.rubrics) and all(r.passed for r in self.rubrics)

    @property
    def score(self) -> Optional[float]:
        """The ranking number, or ``None`` when the evaluator did not produce one."""
        value = self.metrics.get("combined_score")
        return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "idea_id": self.idea_id,
            "program": self.program,
            "metrics": self.metrics,
            "correct": self.correct,
            "rubrics": [r.to_dict() for r in self.rubrics],
            "overall_pass": self.overall_pass,
            "stop_reason": self.stop_reason,
            "note": self.note,
            "next_recommendation": self.next_recommendation,
        }

    @classmethod
    def from_dict(cls, doc: Any) -> Attempt:
        _require(isinstance(doc, dict), f"an attempt is a JSON object, got {type(doc).__name__}")
        aid = doc.get("id")
        _require(isinstance(aid, int) and not isinstance(aid, bool),
                 f"an attempt's id is an integer, got {aid!r}")
        idea_id = doc.get("idea_id")
        _require(idea_id is None or (isinstance(idea_id, int) and not isinstance(idea_id, bool)),
                 f"an attempt's idea_id is an integer or null, got {idea_id!r}")
        program = doc.get("program")
        _require(isinstance(program, str) and bool(program.strip()),
                 f"attempt {doc.get('id')} has no program")
        metrics = doc.get("metrics", {})
        _require(isinstance(metrics, dict),
                 f"attempt {doc['id']}'s metrics is a JSON object, got {type(metrics).__name__}")
        correct = doc.get("correct")
        _require(isinstance(correct, bool),
                 f"attempt {doc['id']}'s correct is a boolean, got {correct!r}")
        raw = doc.get("rubrics", [])
        _require(isinstance(raw, list), f"attempt {doc['id']}'s rubrics is a list")
        statuses = tuple(RubricStatus.from_dict(e, where=f"attempt {doc['id']}'s rubric") for e in raw)
        seen: set[str] = set()
        for status in statuses:
            _require(status.id not in seen, f"attempt {doc['id']} scores rubric {status.id!r} twice")
            seen.add(status.id)
        stop_reason = doc.get("stop_reason", STOPPED)
        _require(isinstance(stop_reason, str) and stop_reason in STOP_REASONS,
                 f"attempt {doc['id']} has stop_reason {stop_reason!r}, expected one of {'/'.join(STOP_REASONS)}")
        for name in ("note", "next_recommendation"):
            _require(isinstance(doc.get(name, ""), str),
                     f"attempt {doc['id']}'s {name} is a string")
        return cls(id=doc["id"], idea_id=idea_id, program=program.strip(), metrics=metrics,
                   correct=correct, rubrics=statuses, stop_reason=stop_reason,
                   note=doc.get("note", "").strip(),
                   next_recommendation=doc.get("next_recommendation", "").strip())


@dataclass(frozen=True)
class Best:
    """The campaign's incumbent: the best *correct* program found so far."""

    program: str
    score: float
    attempt_id: int

    def to_dict(self) -> dict[str, Any]:
        return {"program": self.program, "score": self.score, "attempt_id": self.attempt_id}

    @classmethod
    def from_dict(cls, doc: Any) -> Best:
        _require(isinstance(doc, dict), f"best is a JSON object, got {type(doc).__name__}")
        program, score, attempt_id = doc.get("program"), doc.get("score"), doc.get("attempt_id")
        _require(isinstance(program, str) and bool(program.strip()), "best has no program")
        _require(isinstance(score, (int, float)) and not isinstance(score, bool),
                 f"best's score is a number, got {score!r}")
        _require(isinstance(attempt_id, int) and not isinstance(attempt_id, bool),
                 f"best's attempt_id is an integer, got {attempt_id!r}")
        return cls(program=program.strip(), score=float(score), attempt_id=attempt_id)


@dataclass(frozen=True)
class TaskSpec:
    """The executable task specification their §3.1 requires before any ideation.

    A goal in prose, the rubrics the campaign is held to, the evaluator that
    turns a program into a score, and the baseline program the first candidate is
    cloned from. Without the evaluator there is no score, and without a score
    "better" is a vibe the verifier cannot check.
    """

    goal: str
    rubrics: tuple[Rubric, ...]
    evaluator: str
    program0: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "goal": self.goal,
            "rubrics": [r.to_dict() for r in self.rubrics],
            "evaluator": self.evaluator,
            "program0": self.program0,
        }

    @classmethod
    def from_dict(cls, doc: Any) -> TaskSpec:
        _require(isinstance(doc, dict), f"the task is a JSON object, got {type(doc).__name__}")
        goal = doc.get("goal")
        _require(isinstance(goal, str) and bool(goal.strip()), "the task has no goal")
        rubrics = _rubrics(doc.get("rubrics"), where="the task's rubrics", require_both=True)
        for key in ("evaluator", "program0"):
            value = doc.get(key)
            _require(isinstance(value, str) and bool(value.strip()), f"the task has no {key}")
        return cls(goal=goal.strip(), rubrics=rubrics,
                   evaluator=doc["evaluator"].strip(), program0=doc["program0"].strip())


@dataclass(frozen=True)
class Conclusions:
    """The distilled paragraph the next round starts from.

    Forward-looking, not a chronology: every sentence either points at a lead
    worth pursuing or warns against a dead end. The gate is what keeps it that
    way — a round that merely reconfirms the paragraph leaves it alone, and a
    round that changes it has to say which of the four triggers fired.
    """

    text: str = ""
    trigger: str = NONE
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"text": self.text, "trigger": self.trigger, "reason": self.reason}

    @classmethod
    def from_dict(cls, doc: Any) -> Conclusions:
        if doc is None:
            return cls()
        _require(isinstance(doc, dict), f"conclusions is a JSON object, got {type(doc).__name__}")
        text = doc.get("text", "")
        _require(isinstance(text, str), f"conclusions' text is a string, got {type(text).__name__}")
        trigger = doc.get("trigger", NONE)
        _require(isinstance(trigger, str) and trigger in GATE_TRIGGERS,
                 f"conclusions' trigger is {trigger!r}, expected one of {'/'.join(GATE_TRIGGERS)}")
        reason = doc.get("reason", "")
        _require(isinstance(reason, str), "conclusions' reason is a string")
        return cls.validate(cls(text=text.strip(), trigger=trigger, reason=reason.strip()))

    @classmethod
    def validate(cls, conclusions: Conclusions) -> Conclusions:
        """Shape-check the paragraph. Raises with the rule it broke."""
        if not conclusions.text:
            _require(conclusions.trigger == NONE,
                     "empty conclusions cannot carry a gate trigger other than 'none'")
            return conclusions
        words = len(conclusions.text.split())
        _require(words <= CONCLUSIONS_MAX_WORDS,
                 f"conclusions is {words} words, the limit is {CONCLUSIONS_MAX_WORDS} — "
                 f"distil it, the chronology belongs in the attempts")
        _require("\n\n" not in conclusions.text,
                 "conclusions is one paragraph, with no blank lines in it")
        _require(conclusions.trigger != NONE,
                 "conclusions changed, so a gate trigger says why: "
                 f"{'/'.join(t for t in GATE_TRIGGERS if t != NONE)}")
        _require(bool(conclusions.reason),
                 f"conclusions changed under {conclusions.trigger!r}, so the reason says what changed")
        return conclusions
