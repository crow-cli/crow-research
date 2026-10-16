"""The campaign record: the evidence half of the state file.

Unit tests, no wire and no model. What is under test is the split that makes the
record trustworthy — the host owns ids, ``overall_pass`` and ``best``, the agent
owns ``pass``, ``evidence`` and ``next_recommendation`` — and the rejections,
because a rejection is the only part of a contract an agent ever reads.
"""

from __future__ import annotations

import json
import tempfile

import pytest

from crow_research.campaign import (
    CONCLUSIONS_MAX_WORDS,
    Attempt,
    Best,
    Conclusions,
    Idea,
    Rubric,
    RubricStatus,
    TaskSpec,
)
from crow_research.todo import Todo, state_path

METRIC = Rubric(id="score_threshold", description="sum of radii exceeds 2.6", type="metric")
IMPL = Rubric(
    id="uses_restarts", description="a multi-restart loop is present", type="implementation",
    verification={"type": "source_contains", "patterns": ["restart"]},
)
BUNDLE = [METRIC.to_dict(), IMPL.to_dict()]


def idea_doc(**over) -> dict:
    return {
        "id": 1, "title": "penalty objective", "hypothesis": "a penalty term removes overlap",
        "plan": ["add the penalty", "tune its weight"], "rubrics": BUNDLE,
        "notes": "", "added_by": "explorer", **over,
    }


def attempt_doc(**over) -> dict:
    return {
        "id": 1, "idea_id": 1, "program": "program_1",
        "metrics": {"combined_score": 2.71}, "correct": True,
        "rubrics": [{"id": "score_threshold", "pass": True, "evidence": "2.71"},
                    {"id": "uses_restarts", "pass": True, "evidence": "restart loop at line 40"}],
        "stop_reason": "rubric_pass", "note": "", "next_recommendation": "", **over,
    }


def raises(message, fn, *args, **kwargs) -> str:
    with pytest.raises(ValueError) as info:
        fn(*args, **kwargs)
    text = str(info.value)
    assert message in text, f"{message!r} not in {text!r}"
    return text


# -- round trips ---------------------------------------------------------


def test_every_campaign_type_round_trips() -> None:
    for value in (
        METRIC, IMPL,
        Idea.from_dict(idea_doc()),
        RubricStatus(id="score_threshold", passed=True, evidence="2.71"),
        Attempt.from_dict(attempt_doc()),
        Best(program="program_1", score=2.71, attempt_id=1),
        TaskSpec(goal="pack 26 circles", rubrics=(METRIC, IMPL),
                 evaluator="tasks/circpack/evaluate.py", program0="program_0"),
        Conclusions(text="Penalty objectives beat projection.", trigger="refines", reason="narrowed"),
    ):
        assert type(value).from_dict(value.to_dict()) == value


def test_rubric_pass_is_the_json_key_and_passed_is_the_field() -> None:
    """`pass` is a Python keyword and their schema's key. Both have to work."""
    status = RubricStatus.from_dict({"id": "a_b", "pass": True, "evidence": "seen"})
    assert status.passed is True
    assert status.to_dict() == {"id": "a_b", "pass": True, "evidence": "seen"}


def test_verification_is_omitted_when_empty() -> None:
    assert "verification" not in METRIC.to_dict()
    assert IMPL.to_dict()["verification"] == {"type": "source_contains", "patterns": ["restart"]}


# -- rejections ----------------------------------------------------------


def test_a_rubric_type_outside_the_two_is_refused() -> None:
    raises("expected one of metric/implementation",
           Rubric.from_dict, {"id": "a_b", "description": "d", "type": "vibes"})


def test_a_rubric_id_that_is_not_stable_snake_case_is_refused() -> None:
    for bad in ("ScoreThreshold", "score threshold", "score-threshold", "_score", "", 7, None):
        raises("stable snake_case", Rubric.from_dict, {"id": bad, "description": "d", "type": "metric"})


def test_a_prose_verification_is_refused() -> None:
    raises("never prose", Rubric.from_dict,
           {"id": "a_b", "description": "d", "type": "metric", "verification": "look at the code"})


def test_an_idea_needs_both_kinds_of_rubric() -> None:
    raises("no implementation rubric", Idea.from_dict, idea_doc(rubrics=[METRIC.to_dict()]))
    raises("no metric rubric", Idea.from_dict, idea_doc(rubrics=[IMPL.to_dict()]))


def test_an_idea_cannot_name_the_same_rubric_twice() -> None:
    raises("twice", Idea.from_dict, idea_doc(rubrics=[METRIC.to_dict(), METRIC.to_dict()]))


def test_an_idea_without_a_hypothesis_is_a_guess_not_an_experiment() -> None:
    for bad in (None, "", "   ", 7):
        raises("no hypothesis", Idea.from_dict, idea_doc(hypothesis=bad))


def test_an_idea_without_a_plan_is_refused() -> None:
    raises("non-empty list of short strings", Idea.from_dict, idea_doc(plan=[]))
    raises("non-empty list of short strings", Idea.from_dict, idea_doc(plan=["ok", "  "]))


def test_there_is_no_cap_reached_because_nothing_is_capped() -> None:
    raises("expected one of rubric_pass/evaluation_failed/stopped",
           Attempt.from_dict, attempt_doc(stop_reason="cap_reached"))


def test_an_attempt_that_is_not_correct_says_so_in_a_boolean() -> None:
    raises("correct is a boolean", Attempt.from_dict, attempt_doc(correct="yes"))


def test_an_attempt_cannot_score_the_same_rubric_twice() -> None:
    raises("twice", Attempt.from_dict, attempt_doc(rubrics=[
        {"id": "score_threshold", "pass": True, "evidence": "a"},
        {"id": "score_threshold", "pass": False, "evidence": "b"},
    ]))


def test_an_attempt_for_an_unknown_idea_is_refused_on_load() -> None:
    raises("an attempt without an idea is evidence for nothing", Todo.from_dict,
           {"items": [], "ideas": [idea_doc()], "attempts": [attempt_doc(idea_id=9)]})


def test_a_conclusions_paragraph_over_the_limit_is_refused() -> None:
    text = " ".join(["word"] * (CONCLUSIONS_MAX_WORDS + 1))
    raises(f"the limit is {CONCLUSIONS_MAX_WORDS}", Conclusions.from_dict,
           {"text": text, "trigger": "refines", "reason": "r"})


def test_conclusions_is_one_paragraph() -> None:
    raises("no blank lines", Conclusions.from_dict,
           {"text": "one.\n\ntwo.", "trigger": "refines", "reason": "r"})


def test_a_change_to_conclusions_needs_a_trigger_and_a_reason() -> None:
    raises("a gate trigger says why", Conclusions.from_dict,
           {"text": "something new", "trigger": "none", "reason": ""})
    raises("the reason says what changed", Conclusions.from_dict,
           {"text": "something new", "trigger": "flips", "reason": ""})


def test_an_invented_gate_trigger_is_refused() -> None:
    raises("expected one of", Conclusions.from_dict,
           {"text": "x", "trigger": "felt_like_it", "reason": "r"})


def test_empty_conclusions_needs_no_trigger() -> None:
    assert Conclusions.from_dict(None) == Conclusions()
    assert Conclusions.from_dict({"text": "", "trigger": "none"}) == Conclusions()


# -- host-owned fields ---------------------------------------------------


def test_overall_pass_is_computed_not_believed() -> None:
    """The aggregate is the host's. An agent that writes `true` over a failing
    rubric has changed the file, not the record."""
    lying = attempt_doc(overall_pass=True, rubrics=[
        {"id": "score_threshold", "pass": True, "evidence": "2.71"},
        {"id": "uses_restarts", "pass": False, "evidence": ""},
    ])
    attempt = Attempt.from_dict(lying)
    assert attempt.overall_pass is False
    assert attempt.to_dict()["overall_pass"] is False


def test_overall_pass_needs_every_rubric_and_at_least_one() -> None:
    assert Attempt.from_dict(attempt_doc()).overall_pass is True
    assert Attempt.from_dict(attempt_doc(rubrics=[])).overall_pass is False


def test_the_scaffold_is_every_rubric_unpassed_with_no_evidence() -> None:
    todo = Todo()
    todo.add_idea("penalty objective", "a penalty term removes overlap",
                  ["add the penalty"], BUNDLE, by="explorer")
    assert todo.scaffold_rubrics() == [
        {"id": "score_threshold", "pass": False, "evidence": ""},
        {"id": "uses_restarts", "pass": False, "evidence": ""},
    ]
    assert Todo().scaffold_rubrics() == []


def test_recording_an_attempt_scaffolds_its_rubrics_from_the_idea() -> None:
    todo = Todo()
    idea = todo.add_idea("t", "h", ["p"], BUNDLE, by="explorer")
    attempt = todo.record_attempt(idea.id, "program_1", {"combined_score": 1.0}, True, "stopped")
    assert [r.to_dict() for r in attempt.rubrics] == todo.scaffold_rubrics(idea)
    assert attempt.overall_pass is False  # nothing has been verified yet


def test_recording_an_attempt_for_an_unknown_idea_is_refused() -> None:
    raises("which is not in 'ideas'", Todo().record_attempt, 4, "program_1", {}, True, "stopped")


def test_the_verifier_fills_pass_and_evidence_and_nothing_else() -> None:
    todo = Todo()
    idea = todo.add_idea("t", "h", ["p"], BUNDLE, by="explorer")
    attempt = todo.record_attempt(idea.id, "program_1", {"combined_score": 2.71}, True, "rubric_pass")
    updated = todo.apply_rubric_status(attempt.id, [
        {"id": "score_threshold", "pass": True, "evidence": "2.71"},
        {"id": "uses_restarts", "pass": True, "evidence": "restart loop at line 40"},
    ], next_recommendation="  try a heavier penalty  ")
    assert updated.overall_pass is True
    assert updated.next_recommendation == "try a heavier penalty"
    # The worker's fields are untouched by the verifier's write.
    assert updated.metrics == {"combined_score": 2.71}
    assert updated.correct is True
    assert updated.program == "program_1"


def test_a_partially_verified_attempt_does_not_pass() -> None:
    todo = Todo()
    idea = todo.add_idea("t", "h", ["p"], BUNDLE, by="explorer")
    attempt = todo.record_attempt(idea.id, "program_1", {"combined_score": 2.71}, True, "rubric_pass")
    updated = todo.apply_rubric_status(
        attempt.id, [{"id": "score_threshold", "pass": True, "evidence": "2.71"}])
    assert updated.overall_pass is False
    assert updated.rubrics[1].passed is False  # the unfilled scaffold stays unpassed


def test_scoring_a_rubric_the_idea_never_named_is_refused() -> None:
    todo = Todo()
    idea = todo.add_idea("t", "h", ["p"], BUNDLE, by="explorer")
    attempt = todo.record_attempt(idea.id, "program_1", {}, True, "stopped")
    raises("which the idea never named", todo.apply_rubric_status, attempt.id,
           [{"id": "tasteful_naming", "pass": True, "evidence": "looks nice"}])


def test_applying_status_to_an_attempt_that_does_not_exist_is_refused() -> None:
    raises("no attempt 7", Todo().apply_rubric_status, 7, [])


# -- the best gate -------------------------------------------------------


def _campaign(*scores) -> Todo:
    """A campaign with one idea and one attempt per (score, correct) pair."""
    todo = Todo()
    todo.task = TaskSpec(goal="pack them", rubrics=(METRIC, IMPL),
                         evaluator="evaluate.py", program0="program_0")
    idea = todo.add_idea("t", "h", ["p"], BUNDLE, by="explorer")
    for index, (score, correct) in enumerate(scores, start=1):
        attempt = todo.record_attempt(
            idea.id, f"program_{index}", {"combined_score": score}, correct, "rubric_pass")
        todo.apply_rubric_status(attempt.id, [
            {"id": "score_threshold", "pass": True, "evidence": str(score)},
            {"id": "uses_restarts", "pass": True, "evidence": "present"},
        ])
    return todo


def test_best_moves_only_to_a_strictly_higher_correct_score() -> None:
    todo = _campaign((1.0, True), (2.0, True), (2.0, True), (1.5, True))
    assert todo.best == Best(program="program_2", score=2.0, attempt_id=2)


def test_an_incorrect_candidate_never_becomes_best() -> None:
    todo = _campaign((1.0, True), (99.0, False))
    assert todo.best == Best(program="program_1", score=1.0, attempt_id=1)


def test_a_candidate_with_no_score_never_becomes_best() -> None:
    todo = Todo()
    idea = todo.add_idea("t", "h", ["p"], BUNDLE, by="explorer")
    todo.record_attempt(idea.id, "program_1", {"runtime_seconds": 3}, True, "rubric_pass")
    assert todo.best is None


def test_the_two_gates_are_independent() -> None:
    """All rubrics passing closes the contract; only a better correct score moves
    `best`. A rubric pass on a worse program is a finished contract and no progress."""
    todo = _campaign((2.0, True))
    idea = todo.ideas[0]
    worse = todo.record_attempt(idea.id, "program_2", {"combined_score": 1.0}, True, "rubric_pass")
    todo.apply_rubric_status(worse.id, [
        {"id": "score_threshold", "pass": True, "evidence": "1.0"},
        {"id": "uses_restarts", "pass": True, "evidence": "present"},
    ])
    assert todo.attempts[-1].overall_pass is True   # the contract closed
    assert todo.best.program == "program_1"          # and nothing was banked


def test_best_is_not_moved_by_an_unverified_better_score() -> None:
    todo = _campaign((1.0, True))
    idea = todo.ideas[0]
    todo.record_attempt(idea.id, "program_2", {"combined_score": 5.0}, True, "stopped")
    assert todo.best.program == "program_1"


def test_recompute_best_lands_where_the_incremental_gate_landed() -> None:
    """Replaying every attempt through the gate is the same answer as applying it
    once per turn — which is what makes it safe to run after every turn."""
    todo = _campaign((1.0, True), (2.0, True), (1.5, True), (9.0, False))
    incremental = todo.best
    assert todo.recompute_best() == incremental
    assert todo.recompute_best() == incremental, "not idempotent"


def test_recompute_best_corrects_an_incumbent_an_agent_wrote() -> None:
    """`best` is a field, so an agent *can* write it. Recomputing is what makes
    writing it pointless rather than merely discouraged."""
    todo = _campaign((1.0, True))
    todo.best = Best(program="program_9", score=99.0, attempt_id=99)
    assert todo.recompute_best() == Best(program="program_1", score=1.0, attempt_id=1)


def test_recompute_best_clears_an_incumbent_the_evidence_stopped_supporting() -> None:
    """A verdict the verifier walks back takes the incumbent with it."""
    todo = _campaign((1.0, True))
    assert todo.best is not None
    todo.apply_rubric_status(1, [{"id": "score_threshold", "pass": False,
                                  "evidence": "2.71 is not above the threshold after all"}])
    assert todo.recompute_best() is None


def test_recompute_best_on_a_campaign_with_no_attempts() -> None:
    assert Todo().recompute_best() is None
    assert _campaign().recompute_best() is None


# -- the host-owned scaffold --------------------------------------------


def _file_campaign(attempts: list[dict]) -> Todo:
    """A document as an agent would have written it: ideas from the explorer,
    attempts from the worker, and no host in between."""
    return Todo.from_dict({
        "task": {"goal": "pack them", "rubrics": BUNDLE,
                 "evaluator": "evaluate.py", "program0": "program_0"},
        "ideas": [idea_doc()],
        "attempts": attempts,
    })


def test_repair_scaffolds_adds_back_the_rubrics_a_worker_left_out() -> None:
    """The common case: the worker recorded the attempt and never touched the
    rubrics, so the verifier would be dispatched with nothing to fill in."""
    todo = _file_campaign([attempt_doc(rubrics=[])])
    lines = todo.repair_scaffolds()

    assert lines == ["attempt 1 scaffolded with 2 rubrics"], lines
    assert [r.id for r in todo.attempts[0].rubrics] == ["score_threshold", "uses_restarts"]
    assert all(not r.passed and r.evidence == "" for r in todo.attempts[0].rubrics)
    assert todo.attempts[0].overall_pass is False


def test_repair_scaffolds_drops_a_rubric_the_worker_invented() -> None:
    """An invented criterion is how a round starts being graded on something
    nobody proposed, so it does not survive to the verifier."""
    todo = _file_campaign([attempt_doc(rubrics=[
        {"id": "score_threshold", "pass": False, "evidence": ""},
        {"id": "uses_restarts", "pass": False, "evidence": ""},
        {"id": "tasteful_naming", "pass": True, "evidence": "looks nice"},
    ])])
    lines = todo.repair_scaffolds()

    assert "dropped tasteful_naming" in lines[0], lines
    assert [r.id for r in todo.attempts[0].rubrics] == ["score_threshold", "uses_restarts"]


def test_repair_scaffolds_leaves_a_verifiers_judgment_alone() -> None:
    """The ids are the host's and the values are the verifier's. Repairing one
    must not erase the other, or a resumed round loses its evidence."""
    todo = _file_campaign([attempt_doc(rubrics=[
        {"id": "score_threshold", "pass": True, "evidence": "metrics.json combined_score 2.71"},
        {"id": "uses_restarts", "pass": False, "evidence": "no restart loop in the source"},
    ])])
    assert todo.repair_scaffolds() == []
    assert [(r.id, r.passed) for r in todo.attempts[0].rubrics] == [
        ("score_threshold", True), ("uses_restarts", False)]
    assert todo.attempts[0].overall_pass is False


def test_repair_scaffolds_is_quiet_when_there_is_nothing_to_repair() -> None:
    todo = _campaign((1.0, True))
    assert todo.repair_scaffolds() == []


def test_repair_scaffolds_leaves_an_attempt_for_an_unknown_idea_alone() -> None:
    """`from_dict` already refuses that document; repair is not a second loader."""
    todo = _file_campaign([attempt_doc(rubrics=[])])
    todo.ideas = []
    assert todo.repair_scaffolds() == []


def test_a_repaired_scaffold_survives_the_file(tmp_path) -> None:
    """The repair is written, not held: the verifier reads the file, and so does
    the next process."""
    todo = _file_campaign([attempt_doc(rubrics=[])])
    todo.repair_scaffolds()
    path = todo.save(tmp_path / "todo.json")
    reloaded = Todo.load(path)

    assert [r.id for r in reloaded.attempts[0].rubrics] == ["score_threshold", "uses_restarts"]
    assert reloaded.attempts[0].overall_pass is False
    assert reloaded.best is None


def test_next_program_index_counts_up_and_never_reuses() -> None:
    todo = _campaign((1.0, True), (2.0, True))
    assert todo.next_program_index() == 3
    assert Todo().next_program_index() == 1


# -- the document --------------------------------------------------------


def test_a_file_from_before_the_campaign_still_loads() -> None:
    """Backward compatibility is not a courtesy: there are state files on disk
    from runs that predate this, and one of them is the pipeline's own."""
    legacy = {"verdict": "PASS", "verdict_note": "every claim reproduced",
              "items": [{"id": 1, "text": "make the thing", "state": "done"}]}
    todo = Todo.from_dict(legacy)
    assert todo.verdict == "PASS"
    assert todo.task is None and todo.ideas == [] and todo.attempts == []
    assert todo.best is None and todo.conclusions == Conclusions()
    assert todo.items[0].text == "make the thing"


def test_a_full_campaign_round_trips_through_the_file() -> None:
    todo = _campaign((1.0, True), (2.0, True))
    todo.add("write the report", by="explorer")
    todo.verdict, todo.verdict_note = "PASS", "every rubric reproduced"
    todo.set_conclusions("Penalty objectives beat projection on this task.",
                         trigger="new_category", reason="first campaign-level finding")
    with tempfile.TemporaryDirectory() as tmp:
        path = state_path(tmp)
        todo.save(path)
        loaded = Todo.load(path)
    assert loaded == todo
    assert loaded.to_dict() == todo.to_dict()
    assert loaded.next_program_index() == 3
    assert loaded.add_idea("next", "h", ["p"], BUNDLE).id == 2  # ids are never reused


def test_idea_and_attempt_ids_keep_counting_past_the_file() -> None:
    todo = _campaign((1.0, True))
    doc = json.loads(json.dumps(todo.to_dict()))
    doc["ideas"].append(idea_doc(id=9))
    doc["attempts"].append(attempt_doc(id=4, idea_id=9, program="program_9"))
    loaded = Todo.from_dict(doc)
    assert loaded.add_idea("t", "h", ["p"], BUNDLE).id == 10
    idea = loaded.idea(9)
    assert loaded.record_attempt(idea.id, "program_10", {}, True, "stopped").id == 5


def test_set_conclusions_under_trigger_none_leaves_the_paragraph_alone() -> None:
    todo = _campaign((1.0, True))
    todo.set_conclusions("first", trigger="new_category", reason="r")
    before = todo.conclusions
    assert todo.set_conclusions("a reconfirmation", trigger="none") == before
    assert todo.conclusions.text == "first"


def test_set_conclusions_validates_the_shape() -> None:
    todo = Todo()
    raises("no blank lines", todo.set_conclusions, "one.\n\ntwo.", trigger="flips", reason="r")
    assert todo.conclusions == Conclusions()  # a rejected write changed nothing


# -- rendering -----------------------------------------------------------


def test_render_of_the_list_is_untouched_by_the_campaign() -> None:
    """`render()` is a wire format three roles already read. The campaign is
    rendered beside it, not inside it."""
    todo = _campaign((2.0, True))
    todo.add("a thing")
    assert todo.render() == "TODO (1 pending / 1 total)\n1. [ ] a thing"


def test_render_campaign_shows_the_goal_the_incumbent_and_the_evidence() -> None:
    todo = _campaign((1.0, True), (2.0, True))
    todo.set_conclusions("Penalty objectives beat projection.", trigger="refines", reason="narrowed")
    text = todo.render_campaign()
    assert "TASK GOAL" in text and "pack them" in text
    assert "score_threshold (metric)" in text and "uses_restarts (implementation)" in text
    assert "BEST program_2 — combined_score 2.0" in text
    assert "IDEA 1 — t" in text and "hypothesis: h" in text
    assert "ATTEMPTS (last 2 of 2)" in text
    assert "program_1 (idea 1, attempt 1): combined_score 1.0, correct true, rubrics 2/2" in text
    assert "CONCLUSIONS (refines)" in text


def test_render_campaign_before_there_is_a_campaign() -> None:
    assert Todo().render_campaign() == "(no campaign yet — there is no task specification)"


def test_render_campaign_says_when_there_is_no_incumbent_yet() -> None:
    todo = Todo()
    todo.task = TaskSpec(goal="g", rubrics=(METRIC, IMPL), evaluator="e.py", program0="program_0")
    assert "BEST (none yet" in todo.render_campaign()


def test_render_campaign_bounds_the_attempts_it_shows_but_not_the_file() -> None:
    todo = _campaign(*[(float(i), True) for i in range(1, 9)])
    assert len(todo.attempts) == 8
    assert "ATTEMPTS (last 3 of 8)" in todo.render_campaign(attempts=3)
    assert "program_8" in todo.render_campaign(attempts=3)
    assert "program_1 " not in todo.render_campaign(attempts=3)
