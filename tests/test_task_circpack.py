"""The example task: an executable benchmark a campaign can be run against.

These tests run the real evaluator as a subprocess on the real files, because the
evaluator is the only thing in a campaign allowed to say what a score is — a test
that imported it and re-checked its arithmetic would be grading the same sums
twice and proving nothing about the contract. What is under test is what the loop
leans on: the baseline is correct and scores above zero; a bad candidate is
*recorded* as a result rather than crashing the run; the two output files are
reproducible byte for byte; the evaluator stays stdlib-only, in-process and off
the clock; and the rubrics written in ``task.md`` are rubrics the state scaffold
accepts, naming keys the evaluator actually writes and a threshold that still
equals the baseline it claims to beat.
"""

from __future__ import annotations

import ast
import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from crow_research.campaign import RUBRIC_TYPES, TaskSpec

TASK_DIR = Path(__file__).resolve().parent.parent / "examples" / "tasks" / "circpack"
EVALUATE = TASK_DIR / "evaluate.py"
TASK_MD = TASK_DIR / "task.md"
PROGRAM_0 = TASK_DIR / "program_0" / "src" / "program.py"

#: What task.md documents and program_0 achieves. A threshold, not a target.
BASELINE = 2.166667

#: The ways an in-process evaluator could grow a clock, a socket or a subprocess.
#: Banned by name because they are all stdlib, so "stdlib only" does not catch
#: them, and because a clock here would be the evaluator deciding how long a
#: scientist is allowed to think.
BANNED_IMPORTS = {
    "multiprocessing", "signal", "threading", "subprocess", "concurrent",
    "socket", "urllib", "http", "requests", "numpy", "scipy",
}


def run_evaluator(program: Path, results: Path) -> tuple[dict, dict]:
    """Run the evaluator the way the loop does and read back what it wrote."""
    done = subprocess.run(
        [sys.executable, str(EVALUATE), "--program", str(program),
         "--results-dir", str(results), "--quiet"],
        capture_output=True, text=True,
    )
    # Zero even for a candidate that raised: a failed packing is a result, and an
    # agent reading the exit code instead of correct.json learns the wrong lesson.
    assert done.returncode == 0, done.stderr
    return (json.loads((results / "metrics.json").read_text()),
            json.loads((results / "correct.json").read_text()))


def write_candidate(directory: Path, name: str, source: str) -> Path:
    path = directory / f"{name}.py"
    path.write_text(source, encoding="utf-8")
    return path


def task_rubrics() -> list[dict]:
    """The rubric bundle out of task.md's json fence."""
    blocks = re.findall(r"```json\n(.*?)```", TASK_MD.read_text(encoding="utf-8"), re.DOTALL)
    for block in blocks:
        doc = json.loads(block)
        if isinstance(doc, list):
            return doc
    raise AssertionError("task.md has no json fence holding a list of rubrics")


# --- the baseline -----------------------------------------------------------


def test_program_0_is_correct_and_scores_above_zero(tmp_path):
    metrics, correct = run_evaluator(PROGRAM_0, tmp_path / "program_0")

    assert correct["correct"] is True
    assert "error" not in metrics
    assert metrics["combined_score"] > 0
    assert metrics["num_circles"] == 26
    assert metrics["combined_score"] == pytest.approx(BASELINE, abs=1e-6)


def test_the_baseline_task_md_documents_is_the_baseline_program_0_scores(tmp_path):
    """The prose number and the executed number are the same number.

    A task file advertising 2.166667 while the baseline scores something else
    hands every later round a threshold it cannot reason about.
    """
    documented = re.search(r"scores \*\*(\d+\.\d+)\*\*", TASK_MD.read_text(encoding="utf-8"))
    assert documented, "task.md no longer documents the baseline score"
    metrics, _ = run_evaluator(PROGRAM_0, tmp_path / "program_0")

    assert float(documented.group(1)) == pytest.approx(metrics["combined_score"], abs=1e-6)


def test_two_runs_write_the_same_bytes(tmp_path):
    """Determinism is what makes a score bankable across rounds."""
    first, second = tmp_path / "first", tmp_path / "second"
    run_evaluator(PROGRAM_0, first)
    run_evaluator(PROGRAM_0, second)

    for name in ("metrics.json", "correct.json"):
        assert (first / name).read_bytes() == (second / name).read_bytes(), name


# --- a bad candidate is a result, not a crash -------------------------------

OVERLAPPING = '''
def solution():
    centers = [(0.5, 0.5)] * 26
    radii = [0.1] * 26
    return centers, radii, sum(radii)
'''

OUTSIDE = '''
def solution():
    centers = [(1.2, 0.5)] + [(0.05 + 0.03 * i, 0.05) for i in range(25)]
    radii = [0.1] * 26
    return centers, radii, sum(radii)
'''

DISHONEST_SUM = '''
def solution():
    centers = [(0.1 + 0.2 * (i % 5), 0.1 + 0.2 * (i // 5)) for i in range(26)]
    radii = [0.05] * 26
    return centers, radii, 99.0
'''

TOO_FEW = '''
def solution():
    return [(0.25, 0.25), (0.75, 0.75)], [0.1, 0.1], 0.2
'''

# The exposes_solution rubric is what this one fails.
NO_SOLUTION = '''
def packing():
    return [], [], 0.0
'''

RAISES = '''
def solution():
    raise RuntimeError("the candidate blew up")
'''

BAD_CANDIDATES = [
    ("overlapping", OVERLAPPING, "overlap", None),
    ("outside", OUTSIDE, "leaves the unit square", None),
    ("dishonest_sum", DISHONEST_SUM, "sum_of_radii was reported as 99.0", None),
    ("too_few", TOO_FEW, "expected 26 centers, got 2", None),
    ("no_solution", NO_SOLUTION, "defines no solution()", 0.0),
    ("raises", RAISES, "RuntimeError", 0.0),
]


@pytest.mark.parametrize(("name", "source", "fragment", "score"), BAD_CANDIDATES)
def test_a_bad_candidate_is_recorded_not_raised(tmp_path, name, source, fragment, score):
    """Every failure mode lands in the two files, with a message naming the fault.

    ``score=None`` means the geometry was wrong but the radii were still measured:
    the sum is reported as data, and it is ``correct: false`` — not the score —
    that keeps such an attempt out of ``best``.
    """
    candidate = write_candidate(tmp_path, name, source)
    metrics, correct = run_evaluator(candidate, tmp_path / name)

    assert correct["correct"] is False
    assert fragment in correct["message"]
    assert metrics["error"] == correct["message"]
    if score is None:
        assert metrics["combined_score"] > 0
    else:
        assert metrics["combined_score"] == score


# --- the rubrics in task.md are the rubrics the scaffold holds --------------


def test_task_rubrics_are_a_task_spec_the_state_file_accepts():
    """task.md's bundle has to survive ``TaskSpec.from_dict`` unchanged.

    ``require_both`` is what makes this more than a parse: a bundle of nothing but
    metric rubrics, or nothing but implementation ones, is rejected there.
    """
    spec = TaskSpec.from_dict({
        "goal": "maximize the sum of the radii of 26 non-overlapping circles in the unit square",
        "rubrics": task_rubrics(),
        "evaluator": "examples/tasks/circpack/evaluate.py",
        "program0": "examples/tasks/circpack/program_0/src/program.py",
    })

    assert {r.id for r in spec.rubrics} == {"exposes_solution", "correct_packing", "beats_program_0"}
    assert {r.type for r in spec.rubrics} <= set(RUBRIC_TYPES)
    assert any(r.type == "metric" for r in spec.rubrics)
    assert any(r.type == "implementation" for r in spec.rubrics)
    # Round-trips: what the scaffold holds is what task.md wrote.
    assert TaskSpec.from_dict(spec.to_dict()) == spec


def test_metric_rubrics_name_keys_the_evaluator_writes(tmp_path):
    """A metric rubric has to point at a file and a key that really come out of a run.

    Prose in a metric rubric is a criterion nobody can fail, so each one is
    required to name the evaluator output it reads.
    """
    metrics, correct = run_evaluator(PROGRAM_0, tmp_path / "program_0")
    known = {"metrics.json": metrics, "correct.json": correct}
    keys = set(metrics) | set(correct)
    bundle = task_rubrics()

    metric = [r for r in bundle if r["type"] == "metric"]
    assert metric, "the task has no metric rubric, so nothing about it is measurable"
    for rubric in metric:
        description = rubric["description"]
        named = set(re.findall(r"(metrics\.json|correct\.json)", description))
        assert named, f"metric rubric {rubric['id']} names no evaluator output file"
        assert named <= set(known), f"metric rubric {rubric['id']} names a file the evaluator never writes"
        for key in re.findall(r"\b(combined_score|num_circles|min_radius|max_radius|radius_spread)\b",
                              description):
            assert key in keys, f"metric rubric {rubric['id']} names a key the evaluator never writes"


def test_the_implementation_rubric_is_evidenced_by_a_run(tmp_path):
    """An implementation rubric names the interface, and breaking it is observable.

    ``exposes_solution`` is not checked by reading a metric — it is checked by the
    evaluator refusing a candidate that does not define ``solution()``, and saying
    so in ``correct.json``. That message is the evidence a verifier cites.
    """
    bundle = task_rubrics()
    implementation = [r for r in bundle if r["type"] == "implementation"]
    assert implementation, "the task has no implementation rubric"
    for rubric in implementation:
        assert "solution()" in rubric["description"], f"{rubric['id']} names no interface"

    candidate = write_candidate(tmp_path, "no_solution", NO_SOLUTION)
    _, correct = run_evaluator(candidate, tmp_path / "no_solution")
    assert correct["correct"] is False
    assert "defines no solution()" in correct["message"]


def test_beats_program_0_is_a_strict_threshold_at_the_baseline(tmp_path):
    """The baseline ties the threshold; it does not clear it.

    Strictly greater, so a campaign that merely reproduces ``program_0`` has not
    beaten it — and a threshold drifting away from the baseline is caught here
    rather than in round four.
    """
    rubric = next(r for r in task_rubrics() if r["id"] == "beats_program_0")
    match = re.search(r"combined_score strictly greater than (\d+\.\d+)", rubric["description"])
    assert match, "beats_program_0 no longer states its threshold"
    metrics, _ = run_evaluator(PROGRAM_0, tmp_path / "program_0")

    assert float(match.group(1)) == pytest.approx(BASELINE, abs=1e-6)
    assert not metrics["combined_score"] > float(match.group(1))


# --- the evaluator stays what it claims to be -------------------------------


def test_the_evaluator_is_stdlib_only_and_off_the_clock():
    """No third-party imports, and none of the modules a clock is built from."""
    tree = ast.parse(EVALUATE.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            imported.add(node.module.split(".")[0])

    assert imported & BANNED_IMPORTS == set(), sorted(imported & BANNED_IMPORTS)
    assert imported <= set(sys.stdlib_module_names), sorted(imported - set(sys.stdlib_module_names))


def test_evaluate_is_callable_in_process(tmp_path):
    """``evaluate()`` returns exactly what it wrote, which is how a worker checks
    a candidate without shelling out."""
    spec = importlib.util.spec_from_file_location("circpack_evaluate", EVALUATE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    result = module.evaluate(PROGRAM_0, tmp_path / "in_process")
    written_metrics = json.loads((tmp_path / "in_process" / "metrics.json").read_text())
    written_correct = json.loads((tmp_path / "in_process" / "correct.json").read_text())

    assert result["correct"] is True
    assert result["metrics"]["combined_score"] == pytest.approx(BASELINE, abs=1e-6)
    assert result == {"metrics": written_metrics, "correct": written_correct["correct"],
                      "message": written_correct["message"]}
