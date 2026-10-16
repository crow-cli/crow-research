"""The campaign, end to end over a real wire, with no model in anything.

``workflows.scientist`` is ``pipeline`` with two more seats and three host-side
steps, so that difference is what is under test: the five roles are dispatched in
order and stamped in ``_meta``; a file that already holds a task never sees the
drafter; the scaffold the verifier is handed is the one the explorer wrote,
whatever the worker did to it in between; and the aggregates come out of the file
as the host computed them, not as an agent asserted them.

The scores are the example task's own. ``examples/tasks/circpack/evaluate.py``
really runs, as a subprocess, on a real candidate, and the metrics it writes are
the metrics the worker stub records — so the ``best`` gate is asserted against a
number nothing in this file invented.

Rejections are notices. A rubric type the loader refuses and a conclusions
paragraph the gate refuses both end the same way: the problem is announced, the
last good copy is back in the file, and the next round is dispatched anyway. No
retry counter, because there is nothing here that counts.

Runnable two ways — ``pytest tests/`` or ``uv run python tests/test_scientist.py``.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

from crow_research import Peer, Role, Stdio  # noqa: E402
from crow_research.campaign import CONCLUSIONS_MAX_WORDS, Best, TaskSpec  # noqa: E402
from crow_research.todo import Todo, state_path  # noqa: E402
from test_orchestrator import Recorder  # noqa: E402  (pytest puts tests/ on sys.path)
from test_pipeline import dispatches, notices, run_until, stub  # noqa: E402
from test_task_circpack import PROGRAM_0, TASK_DIR, run_evaluator, task_rubrics  # noqa: E402

#: The cast, in the order the campaign dispatches it.
CAST = ("drafter", "worker", "verifier", "explorer", "writer")

#: A dispatch notice is ``round N · <Role>`` — except the drafter's, which carries
#: no round number because it is not part of a round: it runs once, before the
#: first one. Counting the loop's other notices (``round N · verdict PASS``,
#: ``round N · Explorer added 0 items``) as dispatches is how a test passes for
#: the wrong reason, so both shapes are anchored.
DISPATCH = re.compile(r"^(?:round \d+ · )?(Drafter|Worker|Verifier|Explorer|Writer)$")


def serve_scientist(cwd: str) -> list[str]:
    argv = [sys.executable, "-m", "crow_research", "serve", "--workflow", "scientist"]
    for name in CAST:
        argv += ["--role", stub(name)]
    return argv + ["--cwd", cwd]


def campaign(cwd: str, scripts: dict[str, str]) -> Peer:
    """A peer driving the five stubs, with ``scripts`` as their shared env."""
    env = {**os.environ, "STUB_STATE_PATH": str(state_path(cwd)), **scripts}
    return Peer(
        Role(name="orchestrator", endpoint=Stdio(tuple(serve_scientist(cwd)), env=env)),
        client_factory=Recorder,
    )


def task_doc() -> dict:
    """The circpack task, in the shape the state file holds it."""
    return {
        "goal": ("place 26 circles inside the unit square, no two overlapping and none "
                 "outside it, so that the sum of the radii is as large as possible"),
        "rubrics": task_rubrics(),
        "evaluator": f"python {TASK_DIR / 'evaluate.py'} --program <candidate> --results-dir <dir>",
        "program0": str(PROGRAM_0),
    }


#: The idea's rubrics: one of each type, as ``_rubrics`` demands, and both of them
#: things the evaluator's own two files can settle. ``beats_program_0`` is the
#: task's rubric and not this idea's — an idea whose contract is "be correct and
#: expose the interface" is a legitimate first experiment, and it is what lets a
#: test bank an incumbent without first solving circle packing.
IDEA_RUBRICS = [
    {"id": "exposes_solution", "type": "implementation",
     "description": "the candidate defines solution() returning 26 centers and 26 radii"},
    {"id": "correct_packing", "type": "metric",
     "description": "correct.json reports correct true"},
]

IDEA_TITLE = "a modest grid packing, to have something correct on the books"
IDEA_HYPOTHESIS = "26 equal circles on a 10 x 3 grid at radius 0.04 are correct and expose the interface"
IDEA_PLAN = ["write program_1", "run the task's evaluator on it", "record the attempt"]


def idea_doc(rubrics=None, **over) -> dict:
    doc = {
        "id": 1, "title": IDEA_TITLE, "hypothesis": IDEA_HYPOTHESIS, "plan": list(IDEA_PLAN),
        "rubrics": IDEA_RUBRICS if rubrics is None else rubrics,
        "notes": "", "added_by": "explorer",
    }
    return {**doc, **over}


#: A real candidate. Correct, and deliberately modest: the point is that the
#: score the gate banks came out of the evaluator, not that it is a good packing.
PROGRAM_1 = '''
def solution():
    centers = [(0.05 + 0.1 * (i % 10), 0.05 + 0.1 * (i // 10)) for i in range(26)]
    radii = [0.04] * 26
    return centers, radii, sum(radii)
'''


def seeded_campaign(cwd: Path, *, idea: bool = True, done: bool = False) -> Path:
    """A state file with a task on it already, so the drafter has nothing to do."""
    path = state_path(str(cwd))
    todo = Todo()
    todo.add("run the first experiment", by="prompt")
    todo.task = TaskSpec.from_dict(task_doc())
    if idea:
        todo.add_idea(IDEA_TITLE, IDEA_HYPOTHESIS, IDEA_PLAN, IDEA_RUBRICS, by="explorer")
    if done:
        todo.set(1, "done", "settled by a run that was cancelled")
        todo.verdict = "PASS"
        todo.verdict_note = "signed off before the cancel"
    todo.save(path)
    return path


# --- 7.1 the cast, in order, forever ----------------------------------------


async def test_the_campaign_casts_five_roles_in_order_and_never_stops() -> None:
    """Drafter -> worker -> verifier -> explorer -> writer, and then round again.

    The writer's seat is the one that looks like an ending and is not: it is
    taken whenever a round signs off on a complete list whose explorer added
    nothing, and the round after it starts regardless. Waiting for round 2's
    explorer is what proves round 1's writer finished — cancelling on the
    writer's *dispatch* notice would cut the turn before the child had said
    anything, and the role stamp the order assertion reads would not be there.
    """
    with tempfile.TemporaryDirectory() as tmp:
        peer = campaign(tmp, {
            "STUB_STATE_DRAFTER": json.dumps({"task": task_doc()}),
            "STUB_STATE_WORKER": json.dumps(
                {"items": [{"id": 1, "state": "done", "note": "built program_1 and ran the evaluator"}]}
            ),
            "STUB_STATE_VERIFIER": json.dumps({"verdict": "PASS", "verdict_note": "every claim reproduced"}),
            # The explorer writes nothing, so the round signs off on a complete
            # list with nothing added — which is the writer's cue.
            "STUB_REPLY_EXPLORER": "no idea worth an experiment this round",
            "STUB_REPLY_WRITER": "one correct candidate, no incumbent beaten yet",
        })
        await peer.start(os.getcwd())
        try:
            result = await run_until(
                peer, "pack 26 circles", lambda c: dispatches(c, DISPATCH).get("Explorer", 0) >= 2
            )
            state = Todo.load(state_path(tmp))
        finally:
            await peer.close()

    client = peer.client
    assert result.stop_reason == "cancelled", result.stop_reason
    # The dispatch order, read off the role stamps in ``_meta`` — the same signal
    # a UI uses to say which of the five is speaking.
    assert client.roles_in_order() == list(CAST), client.roles_in_order()
    counts = dispatches(client, DISPATCH)
    assert counts["Drafter"] == 1, counts
    assert counts["Worker"] == 1, counts
    assert counts["Verifier"] == 1, counts
    assert counts["Explorer"] >= 2, counts
    assert counts["Writer"] >= 1, counts
    seen = notices(client)
    assert any("todo seeded with 1 items" in n for n in seen), seen
    assert "todo 1 -> done" in seen, seen
    assert any("verdict PASS" in n for n in seen), seen
    assert any("Explorer added 0 items" in n for n in seen), seen
    # The orchestrator's own state machine is the only one upstream: one pair.
    states = [getattr(u, "state", None) for k, u, _ in client.events if k == "state_update"]
    assert states == ["running", "idle"], states
    # And the file afterwards: the drafter's task survived the round, the list is
    # complete and signed off, and nothing was capped on the way.
    assert state.task is not None and state.task.program0 == str(PROGRAM_0), state.task
    assert state.verdict == "PASS" and state.complete, state.render()


# --- 7.2 a resume skips the drafter -----------------------------------------


async def test_a_resumed_campaign_never_dispatches_the_drafter() -> None:
    """The file outlives the process, and the drafter is a function of the file.

    A campaign resumed with a task, a signed-off verdict and a complete list has
    nothing to draft and nothing to execute, so the first agent dispatched is the
    explorer — the same shape as the plain pipeline's resume test, with the
    drafter's absence as the new half of the assertion.
    """
    with tempfile.TemporaryDirectory() as tmp:
        path = seeded_campaign(Path(tmp), done=True)
        peer = campaign(tmp, {
            "STUB_REPLY_EXPLORER": "no idea worth an experiment this round",
            "STUB_REPLY_WRITER": "the record, as it stands",
        })
        await peer.start(os.getcwd())
        try:
            result = await run_until(peer, "a prompt nobody reads", lambda c: "explorer" in c.roles_in_order())
            state = Todo.load(path)
        finally:
            await peer.close()

    client = peer.client
    assert result.stop_reason == "cancelled", result.stop_reason
    counts = dispatches(client, DISPATCH)
    assert counts.get("Drafter", 0) == 0, counts
    assert counts.get("Worker", 0) == 0, counts
    assert counts.get("Verifier", 0) == 0, counts
    assert counts["Explorer"] >= 1, counts
    seen = notices(client)
    # The file was already there, so nothing was seeded and nobody was drafted.
    assert not any("todo seeded" in n for n in seen), seen
    assert "Drafter" not in seen, seen
    assert state.task is not None and state.verdict == "PASS", state.render()
    assert state.complete, state.render()


# --- the explorer's idea reaches the round that executes it ------------------


async def test_an_idea_the_explorer_writes_lands_in_the_file_and_reaches_the_worker() -> None:
    """Ideation is a write, and the next round reads it.

    The explorer owns ``ideas``: a hypothesis and the rubrics that would confirm
    it, proposed before any program exists, plus the item that carries the
    experiment. What is asserted is the handover in both directions — the bundle
    is in the file exactly as the explorer wrote it, and the worker's dispatch
    names the idea, so the round that runs is the round that was proposed. And
    because the explorer added something, the writer is *not* dispatched: its seat
    belongs to a quiet round, and this one is not quiet.
    """
    with tempfile.TemporaryDirectory() as tmp:
        cwd = Path(tmp) / "run"
        cwd.mkdir()
        path = seeded_campaign(cwd, idea=False, done=True)
        peer = campaign(str(cwd), {
            "STUB_STATE_EXPLORER": json.dumps({
                "ideas": [idea_doc()],
                "items": [{"text": "run the experiment the idea names", "added_by": "explorer"}],
            }),
            "STUB_REPLY_EXPLORER": "one idea, and the item that executes it",
            # The worker echoes, so the dispatch it was handed is readable in the
            # stream. The verifier gets a scripted reply because its own dispatch
            # embeds the worker's report verbatim, and an echo of an echo outgrows
            # the stub's echo limit.
            "STUB_REPLY_VERIFIER": "nothing to check yet",
            "STUB_REPLY_WRITER": "the record, as it stands",
        })
        await peer.start(os.getcwd())
        try:
            result = await run_until(peer, "keep going", lambda c: IDEA_TITLE in c.text_from("worker"))
            state = Todo.load(path)
        finally:
            await peer.close()

    client = peer.client
    assert result.stop_reason == "cancelled", result.stop_reason
    counts = dispatches(client, DISPATCH)
    assert counts.get("Drafter", 0) == 0, counts
    assert counts["Explorer"] >= 1 and counts["Worker"] >= 1, counts
    assert counts.get("Writer", 0) == 0, counts
    assert any("Explorer added 1 items" in n for n in notices(client)), notices(client)
    # The bundle, as the explorer wrote it: one idea, both rubric types, and the
    # item that carries it into the next round.
    assert len(state.ideas) == 1, state.ideas
    idea = state.ideas[0]
    assert idea.title == IDEA_TITLE and idea.hypothesis == IDEA_HYPOTHESIS, idea
    assert list(idea.plan) == IDEA_PLAN, idea
    assert [r.id for r in idea.rubrics] == [r["id"] for r in IDEA_RUBRICS], idea
    assert {r.type for r in idea.rubrics} == {"metric", "implementation"}, idea
    assert idea.added_by == "explorer", idea
    assert any(i.text == "run the experiment the idea names" and i.added_by == "explorer"
               for i in state.items), state.render()


# --- 7.3 the best gate, on the evaluator's own number ------------------------


async def test_the_best_gate_banks_the_number_the_evaluator_wrote() -> None:
    """A worker-shaped stub records a real evaluation, and ``best`` is the host's.

    The candidate is a real file, the evaluator really runs on it as a
    subprocess, and the metrics the worker stub writes are the bytes
    ``metrics.json`` held. So the incumbent the file ends up with is a score
    nothing in this test chose — which is the whole reason the gate lives on this
    side of the wire: an agent may report a number, but only the host may bank one.
    """
    with tempfile.TemporaryDirectory() as tmp:
        scratch = Path(tmp)
        candidate = scratch / "program_1" / "src" / "program.py"
        candidate.parent.mkdir(parents=True)
        candidate.write_text(PROGRAM_1, encoding="utf-8")
        metrics, correct = run_evaluator(candidate, scratch / "results_1")
        baseline, baseline_correct = run_evaluator(PROGRAM_0, scratch / "results_0")
        assert correct["correct"] is True, correct
        assert baseline_correct["correct"] is True, baseline_correct
        # Modest, as advertised: it beats nothing, it is merely correct.
        assert metrics["combined_score"] < baseline["combined_score"], (metrics, baseline)

        scaffold = [{"id": r["id"], "pass": False, "evidence": ""} for r in IDEA_RUBRICS]
        attempt = {
            "id": 1, "idea_id": 1, "program": "program_1",
            "metrics": metrics, "correct": correct["correct"], "rubrics": scaffold,
            "stop_reason": "rubric_pass", "note": "the evaluator wrote metrics.json and correct.json",
        }
        # The verifier's turn: the same attempt with the two judgment fields
        # filled, because ``attempts`` is one key and overwriting it is what an
        # agent editing the document in place actually does.
        judged = {**attempt, "rubrics": [
            {"id": "exposes_solution", "pass": True, "evidence": "solution() returns 26 centers and 26 radii"},
            {"id": "correct_packing", "pass": True, "evidence": f"correct.json: {correct['message']}"},
        ], "next_recommendation": "now beat program_0"}

        cwd = scratch / "run"
        cwd.mkdir()
        path = seeded_campaign(cwd)
        peer = campaign(str(cwd), {
            "STUB_STATE_WORKER": json.dumps({"attempts": [attempt], "items": [{"id": 1, "state": "done"}]}),
            "STUB_STATE_VERIFIER": json.dumps(
                {"attempts": [judged], "verdict": "PASS", "verdict_note": "reproduced from the two files"}
            ),
            "STUB_REPLY_EXPLORER": "no idea worth an experiment this round",
            "STUB_REPLY_WRITER": "one correct candidate on the books",
        })
        await peer.start(os.getcwd())
        try:
            result = await run_until(peer, "run the first experiment",
                                     lambda c: any(n.startswith("best ") for n in notices(c)))
            state = Todo.load(path)
        finally:
            await peer.close()

    client = peer.client
    assert result.stop_reason == "cancelled", result.stop_reason
    # The task was already in the file, so the drafter had nothing to do.
    assert dispatches(client, DISPATCH).get("Drafter", 0) == 0, dispatches(client, DISPATCH)
    score = metrics["combined_score"]
    assert f"best (none) -> program_1 ({score})" in notices(client), notices(client)
    assert state.best == Best(program="program_1", score=score, attempt_id=1), state.best
    assert state.attempts[0].overall_pass is True, state.attempts[0]
    assert state.attempts[0].metrics == metrics, state.attempts[0].metrics
    assert state.attempts[0].next_recommendation == "now beat program_0", state.attempts[0]
    # And the dispatch that produced it named the experiment: the idea under
    # test, the next free program index, and the task's evaluator — the only
    # thing allowed to produce a score.
    asked = client.text_from("worker")
    assert f"idea 1 — {IDEA_TITLE}" in asked, asked[:600]
    assert "program_1/src/program.py" in asked, asked[:4000]
    assert task_doc()["evaluator"] in asked, asked[:4000]

# --- 5.3 the scaffold is the explorer's, the aggregates are the host's --------


async def test_the_scaffold_is_repaired_and_the_aggregates_are_not_believed() -> None:
    """What the verifier is handed, and what the file says afterwards.

    Two failures, both real, both host-side. A worker grades what it noticed
    rather than what was asked — it leaves a rubric out and invents one — so the
    ids are put back to the idea's before the verifier is dispatched, and the
    verifier is shown the scaffold the explorer wrote. Then the verifier asserts
    the two aggregates it does not own: ``overall_pass`` over a rubric it just
    failed, and an incumbent ``best``. Neither survives the reload, and the
    correction is announced rather than silent.
    """
    with tempfile.TemporaryDirectory() as tmp:
        cwd = Path(tmp) / "run"
        cwd.mkdir()
        path = seeded_campaign(cwd)
        attempt = {
            "id": 1, "idea_id": 1, "program": "program_1",
            "metrics": {"combined_score": 1.04, "num_circles": 26}, "correct": True,
            # One rubric the idea named is missing, and one it never named is here.
            "rubrics": [{"id": "correct_packing", "pass": True, "evidence": "correct.json: correct true"},
                        {"id": "tasteful_naming", "pass": True, "evidence": "looks nice"}],
            "stop_reason": "rubric_pass", "note": "built program_1",
        }
        judged = {**attempt, "overall_pass": True, "rubrics": [
            {"id": "exposes_solution", "pass": False, "evidence": "solution() returns 25 centers, not 26"},
            {"id": "correct_packing", "pass": True, "evidence": "correct.json: correct true"},
        ], "next_recommendation": "fix the interface"}
        peer = campaign(str(cwd), {
            "STUB_STATE_WORKER": json.dumps({"attempts": [attempt], "items": [{"id": 1, "state": "done"}]}),
            "STUB_STATE_VERIFIER": json.dumps(
                {"attempts": [judged], "verdict": "FAIL", "verdict_note": "the interface is not what the task asks",
                 "best": {"program": "program_1", "score": 1.04, "attempt_id": 1}}
            ),
            # The worker gets a scripted reply so the verifier's echo is not an
            # echo of an echo; the verifier echoes so the scaffold it was handed
            # is visible in the stream, which is the only way to assert on a
            # dispatch prompt at all.
            "STUB_REPLY_WORKER": "built program_1 and ran the evaluator",
            "STUB_REPLY_EXPLORER": "no idea worth an experiment this round",
            "STUB_REPLY_WRITER": "one failed attempt, nothing banked",
        })
        await peer.start(os.getcwd())
        try:
            result = await run_until(peer, "run the first experiment",
                                     lambda c: any("-> (none" in n for n in notices(c)))
            state = Todo.load(path)
        finally:
            await peer.close()

    client = peer.client
    assert result.stop_reason == "cancelled", result.stop_reason
    seen = notices(client)
    # The repair, announced: the ids went back to the idea's and the invention went.
    assert "attempt 1 scaffolded with 2 rubrics, dropped tasteful_naming" in seen, seen
    # The verifier was handed that scaffold, and not the worker's.
    asked = client.text_from("verifier")
    assert "This round produced attempt 1" in asked, asked[:400]
    assert '"id": "exposes_solution"' in asked and '"id": "correct_packing"' in asked, asked[:4000]
    assert "tasteful_naming" not in asked, asked[:4000]
    # The lie, corrected in the file: overall_pass is a property and best is
    # re-derived, so what an agent asserted about them is not what they say.
    assert state.attempts[0].overall_pass is False, state.attempts[0]
    assert state.best is None, state.best
    assert [r.id for r in state.attempts[0].rubrics] == ["exposes_solution", "correct_packing"]
    assert state.attempts[0].rubrics[0].evidence == "solution() returns 25 centers, not 26"
    # And the correction was not silent — the incumbent the verifier wrote was
    # taken back out, upstream, in the same breath.
    assert any(n.startswith("best program_1 (1.04) -> (none") for n in seen), seen
    assert any("verdict FAIL" in n for n in seen), seen


# --- 5.2 a rejected idea is a notice, not a retry counter -------------------


async def test_a_rejected_idea_is_a_notice_and_the_round_after_it_still_happens() -> None:
    """The explorer writes a rubric type that does not exist, and the campaign
    carries on.

    This is the shape the whole rejection story rests on. Their harness bounds a
    step with a retry counter and a timeout; this one has neither, so a bad write
    has to be *loud* instead — announced upstream with the rule it broke, the last
    good copy back in the file, and the next round dispatched to ask again. A
    loop that stopped here would be a loop with a budget, and a loop that
    swallowed it would be indistinguishable from an explorer that agreed.
    """
    with tempfile.TemporaryDirectory() as tmp:
        cwd = Path(tmp) / "run"
        cwd.mkdir()
        path = seeded_campaign(cwd, idea=False, done=True)
        peer = campaign(str(cwd), {
            # Every stub is slow, so the poll below is far finer than a turn and
            # the read lands in the window it was written for.
            "STUB_DELAY": "0.4",
            "STUB_STATE_EXPLORER": json.dumps({"ideas": [idea_doc(rubrics=[
                {"id": "feels_fast", "type": "vibes", "description": "it seems quicker than the baseline"},
                {"id": "correct_packing", "type": "metric", "description": "correct.json reports correct true"},
            ])]}),
            "STUB_REPLY_EXPLORER": "an idea worth an experiment",
            "STUB_REPLY_WRITER": "nothing banked this round",
        })
        restored: list[Todo] = []

        def rejected_and_still_going(client: Recorder) -> bool:
            # The file is read at the rejection rather than after the run: the
            # explorer writes the same bad idea every round, so the only moment
            # the file is the host's restored copy is the moment a write is refused.
            if not restored and any("does not parse" in n for n in notices(client)):
                restored.append(Todo.load(path))
            # Round 2's explorer is the proof the loop kept going: a rejection
            # that ended the run would never reach it, and one that retried in
            # place would never leave round 1.
            return bool(restored) and dispatches(client, DISPATCH).get("Explorer", 0) >= 2

        await peer.start(os.getcwd())
        try:
            result = await run_until(peer, "keep going", rejected_and_still_going)
        finally:
            await peer.close()

    state = restored[0]
    client = peer.client
    assert result.stop_reason == "cancelled", result.stop_reason
    seen = notices(client)
    assert any("does not parse" in n and "type 'vibes'" in n for n in seen), seen
    assert any("expected one of metric/implementation" in n for n in seen), seen
    counts = dispatches(client, DISPATCH)
    assert counts["Explorer"] >= 2, counts
    assert counts.get("Worker", 0) == 0, counts
    # The rejected write is gone and the campaign it landed on is untouched.
    assert state.ideas == [], state.ideas
    assert state.task is not None and state.verdict == "PASS" and state.complete, state.render()


# --- 5.4 conclusions are gated on the wire too ------------------------------


async def test_an_over_long_conclusions_is_refused_and_the_paragraph_survives() -> None:
    """The gate is a shape, and the shape is checked where the file is read.

    ``conclusions`` is one paragraph of at most 300 words or it is not in the
    document at all, so a round that writes a chronology produces a file that does
    not parse — the same loud rejection as a bad rubric, with the distilled
    paragraph the previous round earned still in place afterwards.
    """
    kept = "The grid baseline is correct, and the space it leaves between circles is where the score is."
    with tempfile.TemporaryDirectory() as tmp:
        cwd = Path(tmp) / "run"
        cwd.mkdir()
        path = seeded_campaign(cwd, done=True)
        todo = Todo.load(path)
        todo.set_conclusions(kept, trigger="refines", reason="the first attempt reproduced the baseline")
        todo.save(path)

        bloated = " ".join(["this round tried another packing and learned another thing about the gaps"] * 40)
        assert len(bloated.split()) > CONCLUSIONS_MAX_WORDS, len(bloated.split())
        peer = campaign(str(cwd), {
            # Every stub is slow, so the poll below is far finer than a turn and
            # the read lands in the window it was written for.
            "STUB_DELAY": "0.4",
            "STUB_STATE_EXPLORER": json.dumps(
                {"conclusions": {"text": bloated, "trigger": "refines", "reason": "more of the same"}}
            ),
            "STUB_REPLY_EXPLORER": "no idea worth an experiment this round",
            "STUB_REPLY_WRITER": "the record, as it stands",
        })
        restored: list[Todo] = []

        def refused_and_still_going(client: Recorder) -> bool:
            # Read at the refusal, not after the run: the explorer writes the same
            # chronology every round, so the paragraph the gate is protecting is
            # only in the file in the window right after one is refused.
            if not restored and any("does not parse" in n for n in notices(client)):
                restored.append(Todo.load(path))
            return bool(restored) and dispatches(client, DISPATCH).get("Explorer", 0) >= 2

        await peer.start(os.getcwd())
        try:
            result = await run_until(peer, "keep going", refused_and_still_going)
        finally:
            await peer.close()

    state = restored[0]
    client = peer.client
    assert result.stop_reason == "cancelled", result.stop_reason
    seen = notices(client)
    assert any("does not parse" in n and f"the limit is {CONCLUSIONS_MAX_WORDS}" in n for n in seen), seen
    assert dispatches(client, DISPATCH)["Explorer"] >= 2, dispatches(client, DISPATCH)
    # The chronology was refused and the distilled paragraph is still the one the
    # next round starts from.
    assert state.conclusions.text == kept, state.conclusions
    assert state.conclusions.trigger == "refines", state.conclusions


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
