"""The dispatch prompts: what each role is actually told.

Unit tests, no wire and no model. The prompts are the contract — an agent that
was never told the evaluator's path cannot run it, and one that was never told
`best` is the host's will write it and learn nothing from having it discarded. So
what is asserted here is that each template formats against a realistic state
without a `KeyError`, that the campaign half of the contract appears exactly when
there is a campaign, and that the schema shown to the agents is the document the
host writes — key for key, including the keys an agent is *not* allowed to write.

The last one is the test that pays for itself. A schema that drifts from
`to_dict()` is a prompt teaching agents a document shape the loader rejects, and
it fails silently: the agent writes what it was shown, the file does not parse,
and the round is thrown away.
"""

from __future__ import annotations

import json

import pytest

from crow_research.campaign import Attempt, Best, Conclusions, Idea, Rubric, TaskSpec
from crow_research.todo import Item, Todo
from crow_research.workflows import (
    EXPLORER_PROMPT,
    STATE_RULES,
    STATE_SCHEMA,
    VERIFIER_PROMPT,
    WORKER_PROMPT,
    _campaign_block,
    _experiment_block,
    _idea_contract_block,
    _scaffold_block,
)

TASK = TaskSpec(
    goal="maximize the sum of the radii of 26 non-overlapping circles in the unit square",
    rubrics=(
        Rubric(id="correct_packing", type="metric", description="correct.json reports correct true"),
        Rubric(id="exposes_solution", type="implementation", description="the candidate defines solution()"),
    ),
    evaluator="examples/tasks/circpack/evaluate.py",
    program0="examples/tasks/circpack/program_0/src/program.py",
)


def campaign_todo() -> Todo:
    """A state file mid-campaign: a task, an idea, two attempts, an incumbent."""
    state = Todo(task=TASK)
    state.add("test the penalty objective", by="explorer")
    idea = state.add_idea(
        title="penalty objective",
        hypothesis="a penalty term removes overlap without shrinking the radii",
        plan=["add the penalty", "tune its weight", "evaluate"],
        rubrics=[
            {"id": "score_threshold", "type": "metric",
             "description": "metrics.json reports combined_score strictly greater than 2.4"},
            {"id": "uses_restarts", "type": "implementation",
             "description": "the candidate runs a multi-restart loop"},
        ],
        by="explorer",
    )
    state.record_attempt(idea.id, "program_1/src/program.py", {"combined_score": 2.41},
                         True, "rubric_pass", note="penalty weight 0.5")
    state.record_attempt(idea.id, "program_2/src/program.py", {"combined_score": 0.0},
                         False, "evaluation_failed", note="circles 3 and 4 overlap")
    state.apply_rubric_status(1, [{"id": "score_threshold", "pass": True,
                                   "evidence": "metrics.json combined_score 2.41"},
                                  {"id": "uses_restarts", "pass": True,
                                   "evidence": "grep restart program_1/src/program.py"}],
                              next_recommendation="raise the penalty weight")
    state.set_conclusions("Unequal radii beat any grid layout.", trigger="new_category",
                          reason="round 2 opened a category the paragraph did not have")
    return state


def format_all(state: Todo, *, task: str = "pack 26 circles") -> dict[str, str]:
    """Format every template the way `pipeline` does, with the same kwargs."""
    todo, path, schema, rules = state.render(), "/tmp/x/.crow-research/todo.json", STATE_SCHEMA, STATE_RULES
    campaign = _campaign_block(state)
    return {
        "worker": WORKER_PROMPT.format(
            path=path, todo=todo, campaign=campaign, findings="",
            experiment=_experiment_block(state), schema=schema, rules=rules,
        ),
        "verifier": VERIFIER_PROMPT.format(
            path=path, todo=todo, campaign=campaign, report="The worker reported:\n\ndone\n\n",
            scaffold=_scaffold_block(state), schema=schema, rules=rules,
        ),
        "explorer": EXPLORER_PROMPT.format(
            task=task, path=path, todo=todo, campaign=campaign, verdict="PASS",
            report="", me="explorer", idea_contract=_idea_contract_block(state),
            schema=schema, rules=rules,
        ),
    }


# --- the templates format, with and without a campaign ----------------------


def test_every_template_formats_against_a_realistic_campaign():
    """PLAN 4.4's criterion: no `KeyError`, and the campaign half really landed."""
    prompts = format_all(campaign_todo())

    for role, text in prompts.items():
        assert "{schema}" not in text and "{rules}" not in text, role
        assert "TASK GOAL" in text, f"{role} was not shown the campaign"
        assert "pack 26 circles" in text or role != "explorer"

    assert "program_3/src/program.py" in prompts["worker"], "the worker was not told the next free index"
    assert TASK.evaluator in prompts["worker"]
    assert "append-only" in prompts["worker"]
    assert '"id": "score_threshold"' in prompts["verifier"], "the verifier was not shown the scaffold"
    assert "at least one `metric` rubric" in prompts["explorer"]
    assert "conclusions" in prompts["explorer"]


def test_every_template_formats_with_no_campaign_at_all():
    """The plain pipeline: a list with no task specification gets no experiment
    contract, no scaffold and no idea contract, because there is nothing to
    score and telling it otherwise is noise."""
    prompts = format_all(Todo())

    for role, text in prompts.items():
        assert "TASK GOAL" not in text, role
        assert "{schema}" not in text and "{campaign}" not in text, role
    assert "task specification" not in prompts["worker"]
    assert "scaffold" not in prompts["verifier"]
    assert "falsifiable" not in prompts["explorer"]
    # The list contract itself is unchanged — this is the prompt three roles and
    # forty-odd tests already read.
    assert "Claim items, do them" in prompts["worker"]
    assert "Your sign-off goes into that file" in prompts["verifier"]
    assert "Decide what happens next" in prompts["explorer"]


def test_the_blocks_are_empty_exactly_when_there_is_no_task():
    bare, campaign = Todo(), campaign_todo()
    for block in (_campaign_block, _experiment_block, _scaffold_block, _idea_contract_block):
        assert block(bare) == "", block.__name__
        assert block(campaign).strip(), block.__name__


def test_the_scaffold_block_is_absent_until_there_is_an_attempt():
    """Nothing to score yet, so nothing to hand the verifier."""
    state = Todo(task=TASK)
    state.add_idea(title="t", hypothesis="h", plan=["p"], by="explorer", rubrics=[
        {"id": "a_b", "type": "metric", "description": "d"},
        {"id": "c_d", "type": "implementation", "description": "d"},
    ])
    assert _scaffold_block(state) == ""
    state.record_attempt(1, "program_1/src/program.py", {"combined_score": 1.0}, True, "rubric_pass")
    assert '"id": "a_b"' in _scaffold_block(state)


def test_round_one_prompts_say_the_attempt_tests_no_idea():
    """The first round runs before the explorer has written anything, so the
    worker is told to record `idea_id` null and the verifier is told whose
    rubrics it is judging. A prompt that only shows the idea case is how a
    worker's first record gets refused on load."""
    state = Todo(task=TASK)
    state.add("run the campaign", by="prompt")
    experiment = _experiment_block(state)
    assert "no idea yet" in experiment
    assert "`idea_id` null" in experiment
    assert "the task's own rubrics" in experiment
    state.record_attempt(None, "program_1/src/program.py", {"combined_score": 2.6},
                         True, "rubric_pass")
    scaffold = _scaffold_block(state)
    assert "No idea was under test, so the drafter" in scaffold
    assert '"id": "correct_packing"' in scaffold
    assert "`null` when the round tested no idea" in STATE_RULES
    # And the same block still names the explorer once there is an idea.
    assert "The explorer" in _scaffold_block(campaign_todo())


# --- the schema shown to agents is the document the host writes -------------


def test_the_schema_is_the_document_key_for_key():
    """A schema that drifts from `to_dict()` teaches agents a shape the loader
    rejects, and it fails silently: the write does not parse and the round is
    thrown away."""
    shown = json.loads(STATE_SCHEMA)
    written = Todo().to_dict()

    assert list(shown) == list(written), (list(shown), list(written))
    assert list(shown["items"][0]) == list(Item(id=1, text="x").to_dict())
    assert list(shown["ideas"][0]) == list(Idea(
        id=1, title="t", hypothesis="h", plan=["p"],
        rubrics=(Rubric(id="a_b", description="d", type="metric"),),
    ).to_dict())
    assert list(shown["conclusions"]) == list(Conclusions().to_dict())


def test_the_schema_omits_exactly_the_keys_an_agent_may_not_write():
    """`overall_pass` is computed, `best` is gated. Showing either as writable is
    an invitation to assert an aggregate the host is going to recompute anyway."""
    shown = json.loads(STATE_SCHEMA)
    attempt = Attempt(id=1, idea_id=1, program="p", metrics={}, correct=True,
                      rubrics=(), stop_reason="stopped")

    assert list(shown["attempts"][0]) == [k for k in attempt.to_dict() if k != "overall_pass"]
    assert shown["best"] is None
    assert "overall_pass" not in shown["attempts"][0]
    assert list(Best(program="p", score=1.0, attempt_id=1).to_dict()) == [
        "program", "score", "attempt_id"]


def test_the_schema_example_is_a_valid_document():
    """Everything the schema shows has to survive the loader, or the example is
    teaching a shape that would be refused."""
    shown = json.loads(STATE_SCHEMA)
    shown["task"] = TASK.to_dict()
    shown["best"] = {"program": "program_1/src/program.py", "score": 2.41, "attempt_id": 1}

    state = Todo.from_dict(shown)
    assert state.task == TASK
    assert state.ideas[0].title == "penalty objective"
    assert {r.type for r in state.ideas[0].rubrics} == {"metric", "implementation"}
    assert state.attempts[0].program == "program_1/src/program.py"
    assert state.attempts[0].overall_pass is False, "the scaffold example must not arrive pre-passed"
    assert state.best is not None and state.best.score == pytest.approx(2.41)


# --- the rules say who owns what -------------------------------------------


@pytest.mark.parametrize("owner", ["drafter", "explorer", "worker", "verifier"])
def test_the_rules_name_every_owner(owner):
    assert owner in STATE_RULES, f"STATE_RULES never tells an agent what the {owner} owns"


def test_the_rules_forbid_the_aggregates_and_the_clock_free_contract():
    for fragment in ("best", "overall_pass", "append-only", "snake_case", "DATA, never as"):
        assert fragment in STATE_RULES, fragment
